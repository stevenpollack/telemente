# UX Testing Strategy

## How Other Textual TUIs Test (Industry Survey)

Three mature projects were surveyed: **Harlequin** (SQL IDE), **Posting** (HTTP
client), and **Textual itself**. All three converge on the same layered strategy.

### Pattern 1 — Harlequin: Pilot assertions + selective snapshots

Harlequin uses a hybrid approach that is the closest to what we should adopt:

```python
async def test_select_1(app_all_adapters, app_snapshot, wait_for_workers):
    app = app_all_adapters
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        assert app.focused.__class__.__name__ == "TextAreaPlus"  # focus check first
        for key in "select 1 as foo":
            await pilot.press(key)
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        [msg] = [m for m in messages if isinstance(m, QuerySubmitted)]
        assert msg.queries == ["select 1 as foo"]
        # snapshot only after all assertions pass
        assert await app_snapshot(app, "select 1 as foo")
```

Key techniques:

- **`message_hook=messages.append`** in `run_test()` — captures every Textual
  message posted during the test. Lets you assert `isinstance(m, QuerySubmitted)`
  rather than probing widget state. Far more reliable than reading widget
  attributes after the fact.
- **`wait_for_workers(app)` fixture** — polls `app.workers` until all workers
  complete, then calls `pilot.pause()`. More robust than a fixed number of
  `await pilot.pause()` calls.
- **`app.focused.__class__.__name__`** — asserts focus by class name, not
  by widget identity, which avoids import/type issues in tests.
- **Snapshot is conditional and last** — only snapped if all behavioural
  assertions pass and there are no environmental quirks (`if not transaction_button_visible(app)`).
- **`size=(120, 36)`** used selectively in tests that depend on layout.

### Pattern 2 — Posting: snap_compare with run_before callbacks

Posting goes further toward pure snapshot testing using `pytest-textual-snapshot`:

```python
async def run_before(pilot: Pilot) -> None:
    pilot.app.screen.query_one(Input).cursor_blink = False  # disable blink noise
    await pilot.press("ctrl+o")
    await pilot.app.workers.wait_for_complete()

assert snap_compare(POSTING_MAIN, run_before=run_before, terminal_size=(80, 34))
```

Key techniques:

- **`cursor_blink = False`** before snapping to eliminate timing noise in SVG diffs.
- **`terminal_size=(80, 34)`** passed directly to `snap_compare`, not to `run_test()`.
- **`await pilot.app.workers.wait_for_complete()`** — waits for all background
  workers before snapping.
- App is launched from a **file path** (`POSTING_MAIN = Path("posting_snapshot_app.py")`)
  rather than an inline class, keeping test files clean.

Posting uses a minimal `posting_snapshot_app.py` that initialises the real app
with sample config files rather than mocks — because their tests don't need
network isolation (they test UI state, not network behaviour).

### Pattern 3 — Textual itself: purely declarative snapshots

```python
def test_switches(snap_compare):
    press = ["shift+tab", "enter", "wait:20", "enter", "wait:20"]
    assert snap_compare(WIDGET_EXAMPLES_DIR / "switch.py", press=press)
```

Uses `"wait:N"` tokens in the `press=` list as inline pauses (milliseconds).
Tests are synchronous (no `async def`). No Pilot assertions — purely visual.
Not appropriate for us because we need to verify messages and state, not
just appearance.

### What this means for telemente

1. **Add `message_hook=messages.append` to integration tests.** This is the
   most impactful single change — it lets us assert that the right Textual
   messages were posted rather than probing widget attributes after the fact.

2. **Add a `wait_for_workers` fixture** to `tests/conftest.py` that polls
   `app.workers` until complete. Replace all `await pilot.pause(); await pilot.pause()`
   patterns with `await wait_for_workers(app)`.

3. **Disable cursor blink before any visual snapshot** to avoid SVG diff noise:
   `view.query_one(Input).cursor_blink = False`.

4. **Focus assertions by class name** (`app.focused.__class__.__name__`) rather
   than `.has_focus` on a specific widget instance — more readable and less
   brittle.

---

## The Problem

We are shipping features that pass all tests but don't work correctly for the
user. That means one or both of:

1. **Tests assert the wrong things** — they verify internal state (CSS classes,
   widget attributes, reactive values) rather than observable user behaviour.
2. **The test infrastructure has blind spots** — `Pilot`-based headless tests
   cannot catch entire categories of failure.

This document diagnoses which blind spots exist, evaluates browser-based testing
as a remedy, and recommends a pragmatic path forward.

---

## What Pilot Tests Can and Cannot Catch

`textual.testing.Pilot` runs the full Textual application in-process with a
headless driver. It is fast, deterministic, injectable (we pass `FakeMatrixClient`
directly), and sufficient for most logic. But it has structural blind spots:

| Category | Pilot catches? | Notes |
|---|---|---|
| Widget mounts / unmounts | Yes | `query_one`, `query` |
| CSS class toggling | Yes | `.has_class()` |
| Reactive attribute changes | Yes | Direct attribute read |
| Message passing between widgets | Yes | With `await pilot.pause()` |
| Key binding dispatch | **Partial** | Bindings work, but `check_consume_key` interactions (e.g. `Input` eating `n`/`N`) only surfaced when we specifically tested for it |
| Visual layout correctness | **No** | Headless driver doesn't render to a real grid |
| Panel sizes / overflow / scrolling | **No** | `size` is zero or a default stub in headless mode |
| Color / style correctness | **No** | TCSS rules apply but nothing renders |
| Actual terminal keypress paths | **No** | `pilot.press()` injects events directly, bypassing OS→terminal→driver path |
| Compositor z-ordering / layers | **No** | `layer: context-menu` is defined but not visually verified |

### The real gap: behavioural tests vs. state tests

Most of our current tests look like this:

```python
panel.load_thread("!r:s", "$root")
await pilot.pause()
rows = list(panel.query(MessageRow))
assert len(rows) == 2
```

This asserts that two `MessageRow` widgets exist in the DOM. It does **not**
assert that the user can see them, that they scroll correctly, that the layout
doesn't overflow, or that a keyboard shortcut actually reaches them.

The failure mode we keep hitting is: **the widget is mounted correctly, but
the user experience is broken** — a keybinding is eaten by a focused `Input`,
a panel is rendered at zero width, or a CSS rule has no effect because the
selector is wrong.

---

## Research Findings: Browser Testing via `textual-serve` + Playwright

### How `textual-serve` works

`textual-serve` (v1.1.3, already installed) is a **separate package** from
`textual`. It is **not** an in-process test harness — it launches the Textual
app as a fresh **subprocess** on every WebSocket connection and relays binary
terminal output over aiohttp to an `xterm.js` frontend.

```
Playwright ↔ aiohttp /ws ↔ subprocess running TelementeApp
```

Key consequence: **`FakeMatrixClient` cannot be injected** across the subprocess
boundary. Browser tests either need a real Matrix homeserver, a local
homeserver mock at the TCP level, or a `--fake-client` CLI flag that wires
fakes internally.

**Programmatic API:**

```python
from textual_serve.server import Server

server = Server(
    command="uv run telemente",  # shell string — no factory injection
    host="127.0.0.1",
    port=8080,
)
server.serve()  # blocking — calls aiohttp.web.run_app() with its own loop
```

There is no `async` context manager, no `start()`/`stop()` pair, and no pytest
plugin. The server must run in a `threading.Thread` with `daemon=True`.

The reliable "app is alive" signal in the browser is the CSS class
`body.-first-byte`, set by `textual.js` on first data received from the
subprocess.

### xterm.js rendering: canvas, not DOM text

xterm.js renders to `<canvas>` via WebGL. **There are no text nodes in the
DOM.** The rendered terminal content is not accessible via CSS selectors.

Available DOM hooks:

- `.xterm-helper-textarea` — the hidden `<textarea>` that receives keyboard
  input. This is how Playwright types into the terminal.
- `.xterm-accessibility-tree` — an `aria-live` region populated **only** when
  a screen reader is detected. Unreliable in headless Chromium.
- `page.evaluate()` into the xterm buffer API:
  `terminal.buffer.active.getLine(y).translateToString()` — the most reliable
  way to assert text content, but requires knowing the line number.

**Screenshot diffing** is the other option. It is fragile: font rendering,
WebGL vs canvas 2D fallback, and missing Google Fonts in CI all introduce
pixel-level noise.

### Playwright for Python

Microsoft's `playwright` package is the right choice (pyppeteer is
abandoned; selenium is heavier with no native async):

```
uv add --dev playwright
uv run playwright install chromium
```

WebGL may be unavailable in headless Chromium without
`--use-gl=swiftshader`. Without WebGL, xterm.js falls back to the canvas 2D
renderer; text is still in canvas either way.

### Minimal fixture sketch

```python
# tests/browser/conftest.py
import socket, threading
from collections.abc import Generator, AsyncGenerator

import pytest, pytest_asyncio
from playwright.async_api import async_playwright, Page
from textual_serve.server import Server

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class _ReadyServer(Server):
    def __init__(self, *args, ready: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self._ready = ready

    async def on_startup(self, app) -> None:
        await super().on_startup(app)
        self._ready.set()

@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    port = _free_port()
    ready = threading.Event()
    server = _ReadyServer(
        command="uv run telemente --fake-client",  # requires CLI flag (see below)
        host="127.0.0.1", port=port, ready=ready,
    )
    threading.Thread(target=server.serve, daemon=True).start()
    assert ready.wait(timeout=15), "server did not start"
    yield f"http://127.0.0.1:{port}"

@pytest_asyncio.fixture
async def page(server_url: str) -> AsyncGenerator[Page, None]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--use-gl=swiftshader"],
        )
        ctx = await browser.new_context()
        pg = await ctx.new_page()
        await pg.goto(server_url)
        await pg.wait_for_selector(".xterm-helper-textarea", timeout=15_000)
        await pg.wait_for_selector("body.-first-byte", timeout=15_000)
        yield pg
        await ctx.close()
        await browser.close()
```

### Gotchas summary

| Issue | Impact |
|---|---|
| Subprocess boundary — no `FakeMatrixClient` injection | All browser tests need a fake-mode CLI flag or real network |
| No `Server.stop()` — must use `daemon=True` | Unclean teardown; leftover subprocesses possible |
| No text nodes — all text assertions need `page.evaluate()` | Verbose; requires knowing xterm buffer line numbers |
| `body.-first-byte` can time out if app startup is slow | Fragile in CI if nio sync blocks |
| Every Playwright reconnect = fresh subprocess = fresh app state | Cannot test state across reconnects |
| WebGL unavailable without `--use-gl=swiftshader` | Add the flag explicitly to `launch()` |

---

## A Better Alternative: `App.export_screenshot()`

Before adding browser infrastructure, consider that Textual ships
`App.export_screenshot() -> str` which returns an **SVG** of the rendered
terminal — available from within a Pilot test, no browser required:

```python
async with app.run_test(size=(120, 40)) as pilot:
    # ... interact ...
    svg = app.export_screenshot()
    # compare against a stored baseline, or parse the SVG for text content
```

The SVG contains actual `<text>` elements with rendered characters and
coordinates. This gives visual regression coverage without a browser, subprocess
boundary, or xterm.js canvas. It is imperfect (ANSI colours map to SVG `fill`,
not CSS), but it is far simpler to integrate than the full Playwright stack.

---

## Recommended Testing Layers

### Layer 1 — Fix existing Pilot tests (immediate)

Before adding new infrastructure, audit current tests against this rubric:

- **Assert user-observable behaviour, not internal state.** Instead of
  `assert len(rows) == 2`, assert that the user can scroll to both rows, that
  the text content is correct, and that the keybinding reaches the widget.
- **Test at the integration seam.** Mount the full `MainScreen`, not isolated
  widgets. Most of the bugs we ship are in the wiring between widgets, not in
  the widgets themselves.
- **Explicitly test focus state.** Many of our bugs are "key binding silently
  eaten by focused `Input`". Every test for a keybinding should assert both
  that the correct widget has focus and that the action fires.
- **Use `size=(120, 40)` in `run_test()`** to get realistic layout dimensions.
  At size zero, panels collapse and layout bugs are invisible.

### Layer 2 — SVG snapshot tests (medium term)

Add a small set of snapshot tests using `export_screenshot()`:

```python
# tests/tui/snapshots/test_main_layout.svg  (committed baseline)
async def test_main_layout_renders() -> None:
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
        assert_matches_snapshot(svg, "test_main_layout")
```

This catches regressions in TCSS rules, panel layout, and text rendering
without a browser. Store baselines in `tests/tui/snapshots/`.

**Note:** Textual ships `pytest-textual-snapshot` for exactly this purpose:
`uv add --dev pytest-textual-snapshot`. It handles baseline storage, diffing,
and the `--snapshot-update` flag automatically.

### Layer 3 — Browser smoke tests (future, narrow scope)

Only if Layers 1 and 2 don't cover enough ground, add a narrow browser smoke
suite using the fixture above. Scope it to:

- App starts and renders something (not blank)
- Login screen appears before authentication
- Keyboard input reaches the terminal (`page.type()` on `.xterm-helper-textarea`)

Do **not** try to replicate Pilot test coverage in the browser — the
canvas/line-number extraction is too fragile for feature-level assertions.

---

## Prerequisites for Layer 3

If browser tests are pursued, the app needs a `--fake-client` CLI entry point:

```python
# src/telemente/__main__.py  (or CLI entry in pyproject.toml)
@click.option("--fake-client", is_flag=True, hidden=True)
def main(fake_client: bool) -> None:
    client = FakeMatrixClient() if fake_client else MatrixClient(...)
    app = TelementeApp(client=client, ...)
    app.run()
```

This is the only clean way to inject test doubles across the subprocess
boundary without a real homeserver.

---

## Conclusion

The immediate fix is **not** new infrastructure — it is writing better Pilot
tests. Most of our shipped-but-broken features would have been caught by:

1. Testing at `MainScreen` integration level rather than isolated widget level
2. Asserting focus state before testing key bindings
3. Running `run_test(size=(120, 40))` so layout is exercised

`pytest-textual-snapshot` is the next step after that, and it requires zero
new dependencies or subprocess machinery. Browser testing via `textual-serve`
+ Playwright is a valid future layer, but its complexity (no fake injection,
canvas rendering, no clean teardown) means it should be the last resort, not
the first.
