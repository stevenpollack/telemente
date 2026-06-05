# Plan 0019: Pyright clean output (zero errors, zero warnings)

**Goal**: `npx pyright src/ tests/ stubs/` exits 0 with no errors or warnings,
so the LSP produces actionable signal rather than noise.

**Baseline** (measured 2026-06-05):
- `src/` only: 29 errors
- `src/ tests/ stubs/`: 194 errors
- Rule breakdown: `reportPrivateUsage` 151, `reportUnknownVariableType` 10,
  `reportUnknownMemberType` ~22 (embedded in the Unknown cascade),
  `reportUnknownArgumentType` ~10, `reportUnnecessaryIsInstance` 1

---

## Full error inventory

### GROUP A — `reportPrivateUsage` on test-double scripting fields (148 errors)

**Root cause**: `FakeMatrixClient` in `tests/fakes.py` intentionally exposes
`_rooms`, `_members`, `_messages`, and `_logged_in` as the scripting surface
for tests. Pyright treats any `_`-prefixed attribute as protected, so every
test that writes `fake._rooms = [...]` triggers this rule. The same applies to
`_RoomItem` / `_MessageRow` (private module-level classes accessed by tests),
`_start_sync_and_subscribe`, `_message_view_for`, `_open_tabs`, `_unread`,
`_cmd_*` helpers on `MainScreen`/`TelementeApp`, and internal state inspected
to assert post-conditions.

**Files and representative lines**:
- `tests/fakes.py` itself — the attributes *are* public test-scripting API
- `tests/tui/test_message_view.py:295` — `fake._logged_in = True`
- `tests/tui/test_message_view.py:296` — `fake._messages[room_id] = [...]`
- `tests/tui/test_main_screen.py:197` — `fake._logged_in`
- `tests/tui/test_main_screen.py:296` — `screen._open_tabs`
- `tests/tui/test_sync_integration.py:159` — `screen._message_view_for`
- `tests/tui/test_sync_integration.py:169` — `_MessageRow` class
- `tests/tui/test_room_list.py:241` — `_RoomItem` class
- `tests/tui/test_commands.py:47` — `fake._logged_in`
- `tests/tui/test_commands.py:161` — `app._cmd_sort_alpha`
- `tests/matrix/test_e2ee.py:151` — `client._logged_in`
- `tests/matrix/test_e2ee.py:209` — `client._on_sync`
- `tests/matrix/conftest.py:98` — `resp._raw_headers`
- `tests/test_config.py:256` — `store._path`

**Two distinct sub-cases**:

**A1 — Fake scripting fields on `FakeMatrixClient`**
(`_rooms`, `_members`, `_messages`, `_logged_in`, `_flows`):
These *are* the public API of the fake. Rename them to drop the leading
underscore: `fake.rooms_data`, `fake.members_data`, `fake.messages_data`,
`fake.logged_in` (or just use public names from the start). The `_`-prefix
convention is being misused here — the attributes are explicitly documented in
the class docstring as "Scripted behaviour" and "Test spies".

Fix: rename in `tests/fakes.py` + update all references in test files. ~80
lines changed across ~10 test files.

**A2 — Private implementation internals genuinely accessed from tests**
(`_open_tabs`, `_unread`, `_rooms`, `_message_view_for`, `_start_sync_and_subscribe`,
`_cmd_*`, `_RoomItem`, `_MessageRow`, `_raw_headers`, `_path`, `_on_sync`,
`_on_megolm_event`, `_cached_user_id`, `_room_cache`, `_client` in commands):

These are real private internals being poked for white-box assertions. The
choices per-symbol are:

| Symbol | Owner | Fix |
|--------|-------|-----|
| `_open_tabs` (MainScreen) | assertion-only access | Add `@property open_tabs` returning a copy |
| `_unread` (MainScreen) | assertion-only | Add `@property unread_counts` |
| `_message_view_for` (MainScreen) | test-internal helper access | Add `@property _message_views` or make public `message_view_for(room_id)` |
| `_rooms` (FakeMatrixClient) | scripting | Rename to `rooms_data` (covered in A1) |
| `_start_sync_and_subscribe` (FakeMatrixClient) | test helper | Rename to `start_sync_and_subscribe` (covered in A1) |
| `_cmd_*` (MainScreen) | called directly in test | Make public `cmd_sort_alpha()` etc. or keep them `_`-prefixed and suppress |
| `_RoomItem` (room_list module) | type check in test | Export as `RoomItem` — rename the class, keeping `_` only if CSS references need updating |
| `_MessageRow` (message_view module) | same | Export as `MessageRow` |
| `_raw_headers` (aiohttp ClientResponse) | conftest compatibility shim | `# type: ignore[assignment]` — this is an aiohttp internals workaround, not fixable without the upstream fix |
| `_path` (keyring store) | post-condition assert | `# type: ignore[attr-defined]` — third-party internal |
| `_on_sync`, `_on_megolm_event` (MatrixClient) | direct invocation in e2ee tests | Add public `__test_on_sync` seam OR suppress per-call — see A2 rationale below |
| `_cached_user_id`, `_room_cache`, `_client` (TelementeApp) | integration assertions | Add properties or suppress |

**A2 rationale for `_on_sync` / `_on_megolm_event`**:
In `tests/matrix/test_e2ee.py` these are called directly to fire the nio callback
without a running sync loop. A clean real fix is to add a thin public test-seam
method (e.g. `async def _test_invoke_sync_callback(response)`) or use the
existing `response_callback_for()` / `event_callback_for()` helpers from
`helpers.py` (which already exist for this purpose). Prefer the helpers — they
already return the callback function, no new seam needed.

**A2 rationale for `_cmd_*` on MainScreen**:
These are action methods invoked via `await screen._cmd_sort_alpha()`. A clean
fix: make them public (`cmd_sort_alpha`) and remove the underscore. They are
already delegated-to by Textual command palette code, not internal machinery.

**A2 rationale for `_room` and `_message` in production code** (28:14 in
`room_list.py`, and 441/475/489 in `message_view.py`):
- `room_list.py:214` — `item._room.room_id` inside `update_unread`, where
  `item` is a `_RoomItem` queried within the same module. Pyright sees
  cross-class access even within the same file. Real fix: the `room` property
  already exists on `_RoomItem` (`@property def room`), so change
  `item._room.room_id` → `item.room.room_id`. One line.
- `message_view.py:441,475,489` — `row._message.event_id` inside `MessageView`
  methods. `_MessageRow._message` has no public property. Add
  `@property def message(self) -> Message: return self._message` to `_MessageRow`.
  Then replace the three call sites. ~5 lines total.

**`commands.py:242,315`** — `app._client`: `TelementeApp._client` is accessed
from a command handler that already does `isinstance(app, TelementeApp)`. Add
a public `@property client` on `TelementeApp` (or rename the attribute). The
client is not secret; it is the documented DI seam. ~5 lines.

---

### GROUP B — `reportUnknownVariableType` / `reportUnknownMemberType` on `dict[str, object]` traversal (16 errors)

**Root cause**: `parse_login_flows()` in `auth.py` receives `Mapping[str, object]`
and calls `.get("flows", [])`. The returned value is `object`, so iterating it
gives `Unknown` items, and all subsequent `.get()` calls on them propagate
`Unknown`. Similarly `_parse_well_known_base_url` in `discovery.py` and
`field(default_factory=dict)` in `models.py`.

**File: `src/telemente/matrix/auth.py`** (lines 42, 62, 65, 70, 72, 75-77, 83)

```
auth.py:42:5  - Type of "identity_providers" is partially unknown — list[Unknown]
auth.py:62:9  - Type of "flow" is unknown
auth.py:65:9  - Type of "flow_type" is unknown
auth.py:65:21 - Type of "get" is partially unknown (Unknown dict)
auth.py:70:13 - Type of "raw_idps" is unknown
auth.py:70:24 - Type of "get" is partially unknown
auth.py:72:21 - Type of "idp" is unknown
auth.py:75:21 - Type of "idp_id" is unknown
auth.py:75:30 - Type of "get" is partially unknown
auth.py:76:21 - Type of "idp_name" is unknown
auth.py:76:32 - Type of "get" is partially unknown
auth.py:77:21 - Type of "idp_icon" is partially unknown
auth.py:77:32 - Type of "get" is partially unknown
auth.py:83:42 - Argument type is unknown (str(idp_icon))
```

Fix: the function already does `isinstance` narrowing on `flows_list` and each
`flow`. The problem is that `Mapping[str, object]` means `.get()` returns
`object`, not `Unknown` per se — but Pyright still can't narrow `object` to
`list` without explicit casts. The clean fix is to change the parameter type to
`dict[str, Any]` (which is what the caller already passes after `await
resp.json()`), and add a local typed variable for the flows list:

```python
def parse_login_flows(payload: dict[str, Any]) -> LoginFlows:
    flows_raw = payload.get("flows", [])
    flows_list: list[Any] = flows_raw if isinstance(flows_raw, list) else []
    for flow in flows_list:
        if not isinstance(flow, dict):
            continue
        flow_typed: dict[str, Any] = flow
        flow_type = flow_typed.get("type", "")
        ...
```

This eliminates all 14 `auth.py` errors. ~15 lines changed. The `Mapping[str,
object]` signature is overly strict for JSON payloads — `dict[str, Any]` is the
idiomatic Python choice for unvalidated JSON.

**File: `src/telemente/matrix/discovery.py`** (lines 61-61, 2 errors)

```
discovery.py:61:5  - Type of "base_url" is partially unknown — Unknown | None
discovery.py:61:16 - Type of "get" is partially unknown
```

The function `_parse_well_known_base_url(payload: dict[str, Any])` already uses
`Any`. The issue is that `homeserver.get("base_url")` where `homeserver` was
narrowed to `dict` (from `isinstance(homeserver, dict)`) — but the dict's value
type is `Any` which propagates. Actually re-reading the code:
`payload.get("m.homeserver")` returns `Any | None`; after `isinstance(...,
dict)` check, Pyright narrows it to `dict[Unknown, Unknown]` because `payload`
is `dict[str, Any]` but `.get()` returns `Any` not `dict`. Fix: add explicit
cast: `homeserver: dict[str, Any] = payload.get("m.homeserver")` after the
isinstance check, or simply annotate the local:

```python
homeserver: dict[str, Any] = payload.get("m.homeserver")  # after isinstance check
base_url: str | None = homeserver.get("base_url")
```

~3 lines changed.

**File: `src/telemente/matrix/models.py`** (lines 21, 40, 2 errors)

```
models.py:21:5 - Type of "tags" is partially unknown — dict[Unknown, Unknown]
models.py:40:5 - Type of "reactions" is partially unknown — dict[Unknown, Unknown]
```

The fields use `field(default_factory=dict)` but are annotated `dict[str,
float | None]` and `dict[str, list[str]]` respectively. Pyright infers
`dict[Unknown, Unknown]` from the bare `dict` factory. Fix: use
`field(default_factory=lambda: {})` with the type annotation already present,
OR explicitly type the factory:

```python
tags: dict[str, float | None] = field(default_factory=dict[str, float | None])
```

Actually the cleanest fix for Pyright: add a `__post_init__` typed cast, or
just pass the annotation explicitly. The real issue is Pyright's inference of
`dict()` — it cannot infer the KV types from the annotation alone. The
idiomatic fix that Pyright understands is:

```python
tags: dict[str, float | None] = field(default_factory=lambda: cast(dict[str, float | None], {}))
```

Or use `default_factory=dict` but add a `# type: ignore[assignment]` comment
with justification that the annotation constrains the actual type. However, the
proper fix without suppression is to change to a typed empty dict literal in a
lambda. ~4 lines.

**File: `tests/matrix/test_client.py`** (lines 322, 327, 400, 404, 440, 444, 792, 796 — 8 errors)

```
test_client.py:322:5 - Type of "idle" is partially unknown
test_client.py:327:22 - Argument type is partially unknown
```

`idle` is assigned as a dict literal `{"next_batch": "idle", "rooms": {...}}`.
The value type `dict[str, str | dict[str, dict[str, ...]]]` contains nested
dicts, and the inner `{}` literals infer as `dict[Unknown, Unknown]`. Fix: add
explicit annotation `idle: dict[str, Any] = {...}`. ~8 lines (one per test
function).

**File: `tests/matrix/test_recorded_fixtures.py`** (lines 153, 158 — 2 errors)

Same idle dict pattern. Fix: explicit `dict[str, Any]` annotation. ~2 lines.

---

### GROUP C — `reportUnknownMemberType` on Textual's `App[Unknown]` and `Worker[Unknown]` (3 errors)

**Root cause**: Textual's bundled type stubs declare `App` as a Generic
(`App[ReturnType]`) and `self.app` returns `App[Unknown]` when the concrete app
class isn't visible in scope. Same for `Worker[T]`.

**File: `src/telemente/tui/screens/main.py`** (line 226)
```
main.py:226:17 - Type of "app" is partially unknown — App[Unknown]
```
`self.app.notify(...)` — `notify` is on `App[T]` regardless of `T`, but Pyright
reports it as unknown member because of the `Unknown` type arg.

**File: `src/telemente/tui/widgets/message_view.py`** (line 486)
```
message_view.py:486:13 - Type of "app" is partially unknown — App[Unknown]
```
`self.app.notify(...)` — same pattern.

**File: `src/telemente/tui/screens/login.py`** (lines 672-672)
```
login.py:672:54 - Type of "worker" is partially unknown — Worker[Unknown]
login.py:672:54 - Argument type is partially unknown — Worker[Unknown]
```
`event.worker` in `on_worker_state_changed`.

**Root cause analysis**: Textual ships its own `py.typed` stubs. `Widget.app`
returns `App[object]` in Textual 1.x stubs, which Pyright widens to
`App[Unknown]` because `App` is generic. This is a Textual stubs issue, not a
project issue.

**Real fix options**:
1. Override `app` property in `MessageView` / `MainScreen` to narrow the type:
   ```python
   @property
   def app(self) -> "TelementeApp":  # type: ignore[override]
       return cast("TelementeApp", super().app)
   ```
   This is the Textual-recommended pattern for typed apps. Requires importing
   `TelementeApp` (or using `TYPE_CHECKING` guard). ~4 lines per widget. Clean
   and removes the errors.
2. Suppress with `# type: ignore[union-attr]` at each call site (~4 lines, but
   this leaves the root cause unfixed and pollutes call sites).

**Recommendation**: option 1. Add a typed `app` property override in
`MainScreen` and `MessageView`. For `login.py:672` the `event.worker` issue is
that `Worker.StateChanged` event has `worker: Worker[T]` and `T` is unknown at
the event handler site — a `cast` to `Worker[object]` is sufficient since we
only call `.cancel()` or log it.

---

### GROUP D — `reportUnnecessaryIsInstance` (1 error)

**File: `src/telemente/tui/app.py`** (line 283)
```
app.py:283:14 - Unnecessary isinstance call; "MembersChanged" is always an instance of "MembersChanged"
```

The code pattern is:
```python
elif isinstance(event, MembersChanged):
    ...
    self.post_message(_ClientMembersChanged(event))
```

Pyright has narrowed `event` to `MembersChanged` via the preceding
`elif isinstance(event, NewMessage)` branch — the final elif is the only
remaining type in the union. The isinstance call is redundant. Fix: change to
`else:` and cast if needed, or restructure the if/elif chain. ~2 lines.

---

### GROUP E — `reportPrivateUsage` on `_raw_headers` / `_path` (third-party internals, 2 errors)

**File: `tests/matrix/conftest.py:98`**
```
conftest.py:98:10 - "_raw_headers" is protected and used outside of the class
```
This is inside `_patched_build_response` which must set `resp._raw_headers`
because `ClientResponse.__init__` requires it and there is no public setter.
The entire function is a compatibility shim for aiohttp 3.10+ internals.

**Fix**: `# type: ignore[attr-defined]` on that line, with a comment explaining
it is an aiohttp-internal attribute set by the compat shim. The existing
`resp._headers = _headers  # type: ignore[assignment]` on line 97 already uses
this pattern consistently. ~1 line.

**File: `tests/test_config.py:256`**
```
test_config.py:256:11 - "_path" is protected and used outside of the class
```
Read the context to determine if this is a keyring store internal being
inspected for path assertions.

Fix: `# type: ignore[attr-defined]` if it is a third-party internal with no
public accessor. ~1 line.

---

## Fix categories summary

| Category | Rule | Count | Fix type | Effort (lines) |
|----------|------|-------|----------|----------------|
| A1 | `reportPrivateUsage` on FakeMatrixClient scripting fields | ~70 | Rename `_x` → `x` on fake attrs + all references | ~120 |
| A2a | `reportPrivateUsage` on `_room`/`_message` in same-module access | 4 | Add public property (`room`, `message`) | ~6 |
| A2b | `reportPrivateUsage` on `_RoomItem`/`_MessageRow` in tests | 15 | Rename to `RoomItem`/`MessageRow` | ~20 |
| A2c | `reportPrivateUsage` on `_cmd_*`, `_open_tabs`, `_unread`, `_message_view_for`, `_start_sync_and_subscribe` (MainScreen) | 25 | Drop leading `_` from method/property names | ~35 |
| A2d | `reportPrivateUsage` on `_on_sync`/`_on_megolm_event` (MatrixClient) in e2ee tests | 7 | Use existing `response_callback_for()` / `event_callback_for()` helpers | ~10 |
| A2e | `reportPrivateUsage` on `_client` in `commands.py` | 2 | Add public `client` property on `TelementeApp` | ~4 |
| A2f | `reportPrivateUsage` on `_cached_user_id`, `_room_cache`, `_client` in sync integration tests | 3 | Add properties or suppress with justification | ~6 |
| B | `reportUnknownVariable/Member/ArgumentType` from `dict[str, object]` traversal | 26 | Re-annotate to `dict[str, Any]` + local cast variables | ~30 |
| C | `reportUnknownMemberType` on `App[Unknown]` / `Worker[Unknown]` | 3 | Typed `app` property override + `cast` for Worker | ~8 |
| D | `reportUnnecessaryIsInstance` | 1 | Replace redundant `elif isinstance` with `else` | ~2 |
| E | `reportPrivateUsage` on aiohttp/keyring internals | 2 | `# type: ignore[attr-defined]` with comment | ~2 |
| **Total** | | **~158** | | **~243** |

---

## Recommended implementation order

### Phase 1 — Zero-touch production code, highest signal-to-noise (10 min)

**Step 1** (`reportUnnecessaryIsInstance` — 1 error, ~2 lines):
`src/telemente/tui/app.py:283` — change `elif isinstance(event,
MembersChanged):` to `else:`. Verify with `isinstance` narrowing comment if
needed.

**Step 2** (`dict[str, object]` → `dict[str, Any]` — 28 errors, ~30 lines):
- `src/telemente/matrix/auth.py` — change `Mapping[str, object]` parameter to
  `dict[str, Any]`, add `flows_list: list[Any]` and `flow_typed: dict[str, Any]`
  local bindings.
- `src/telemente/matrix/discovery.py` — annotate `homeserver` local as
  `dict[str, Any]` after isinstance check.
- `src/telemente/matrix/models.py` — change `field(default_factory=dict)` to
  `field(default_factory=lambda: {})` (Pyright infers correctly from
  annotation when factory is a lambda returning a literal).
- `tests/matrix/test_client.py` and `tests/matrix/test_recorded_fixtures.py` —
  annotate `idle: dict[str, Any]`.

**Step 3** (`App[Unknown]` / `Worker[Unknown]` — 3 errors, ~8 lines):
- Add `@property app(self) -> "TelementeApp"` override in
  `src/telemente/tui/screens/main.py` and
  `src/telemente/tui/widgets/message_view.py`.
- Cast `event.worker` in `login.py:672`.

**Step 4** (`_raw_headers` / `_path` suppression — 2 errors, ~2 lines):
Add `# type: ignore[attr-defined]  # aiohttp compat shim` and
`# type: ignore[attr-defined]  # keyring internal` respectively.

### Phase 2 — Production code structural fixes (30 min)

**Step 5** (`_room` / `_message` property in same module — 4 errors, ~6 lines):
- `room_list.py:214`: change `item._room.room_id` → `item.room.room_id` (public
  property already exists).
- `message_view.py:441,475,489`: add `@property def message(self) -> Message`
  to `_MessageRow`, then update the three `row._message` call sites.

**Step 6** (`_client` in `commands.py` — 2 errors, ~4 lines):
Add `@property def client(self) -> MatrixClient` on `TelementeApp` that returns
`self._client`. Update `commands.py:242` and `commands.py:315`.

**Step 7** (`App[Unknown]` in `commands.py` if still present after Step 3):
Commands access `app._client` which will be resolved by Step 6.

### Phase 3 — Rename private test-internal symbols (60 min)

**Step 8** (Rename `_RoomItem` → `RoomItem` and `_MessageRow` → `MessageRow`
— 15 errors, ~20 lines):
These are module-private only by convention; the CSS selectors use the class
name string. Update the CSS `DEFAULT_CSS` strings too. Confirm no other
references to the old names.

**Step 9** (Drop `_` from `MainScreen` command/helper methods — 25 errors, ~35
lines):
Rename `_cmd_sort_alpha` → `cmd_sort_alpha`, `_open_tabs` → `open_tabs`,
`_unread` → `unread_counts` (add public property), `_message_view_for` →
`message_view_for`, `_start_sync_and_subscribe` → `start_sync_and_subscribe`
in both `FakeMatrixClient` and `MainScreen`/`TelementeApp`. Update all
references in `tests/`.

**Step 10** (Rename `FakeMatrixClient` scripting fields — ~70 errors, ~120
lines):
Rename `_rooms → rooms_data`, `_members → members_data`, `_messages →
messages_data`, `_logged_in → logged_in` (or `is_logged_in`). Update all test
files that write `fake._messages[room_id] = [...]`.
This is the highest volume change. Run `ruff check` and `pytest` after.

**Step 11** (Replace `_on_sync` / `_on_megolm_event` direct calls in e2ee tests
— 7 errors, ~10 lines):
Replace `await client._on_sync(resp)` with:
```python
cb = response_callback_for(nio_mock, nio.SyncResponse)
await cb(resp)
```
Replace `await client._on_megolm_event(room, event)` with:
```python
cb = event_callback_for(nio_mock, nio.MegolmEvent)
await cb(room, event)
```
Both helpers already exist in `tests/matrix/helpers.py`.

### Phase 4 — Suppress remaining unfixable third-party internals (5 min)

**Step 12** (Already covered in Step 4 above): `_raw_headers`, `_path`.

**Step 13** (`_cached_user_id`, `_room_cache` in sync integration tests — 3
errors): Either add properties to `TelementeApp` for these (preferable if they
are meaningfully test-observable) or suppress with
`# type: ignore[attr-defined]` — these are cache internals with no natural
public name.

---

## Cases where suppression is the right call (with justification)

1. **`conftest.py:98` `_raw_headers`**: aiohttp's `ClientResponse` has no
   public `raw_headers` setter. The entire function is a documented compat shim
   expected to be deleted when aioresponses ships aiohttp 3.10+ support. The
   real fix is upstream. Use `# type: ignore[attr-defined]`.

2. **`test_config.py:256` `_path`**: If this accesses keyring's internal store
   path to assert the file was written in the expected location, there is no
   public API alternative. `# type: ignore[attr-defined]` with a comment.

3. **`_cached_user_id`, `_room_cache` in `test_sync_integration.py`** (Steps
   13): these are cache-management internals. Adding public properties solely to
   satisfy Pyright is acceptable but produces noise in the production API. Prefer
   `# type: ignore[attr-defined]` with a brief explanation, unless the values
   are naturally observable test post-conditions (then add read-only properties).

For all three cases, suppression is justified because a real fix would require
either modifying third-party code or polluting the production API with
test-only accessors.

---

## Estimated total lines changed

| Phase | Steps | Estimated lines |
|-------|-------|----------------|
| 1 | 1–4 | ~42 |
| 2 | 5–7 | ~10 |
| 3 | 8–11 | ~185 |
| 4 | 12–13 | ~5 |
| **Total** | | **~242** |

The bulk is Phase 3 (Step 10 alone: ~120 lines across ~10 test files). All
changes are mechanical renames — no logic changes. Run `uv run pytest -x -q`
after each step to confirm nothing regresses.
