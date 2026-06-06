# Plan 0033 — Join room by ID or alias

## Goal

Users can join a Matrix room by entering a room ID (`!roomid:server`) or
canonical alias (`#alias:server`) via a dialog or command palette entry. This
is the primary discovery mechanism for public rooms and the only way to enter
rooms where the user was not invited.

---

## Dependencies

- Plans 0001–0032 complete.
- No additional runtime dependencies.

---

## Architecture

### Matrix protocol notes

nio provides:

```python
response = await self._client.join(room_id_or_alias)
# returns JoinResponse(room_id=...) on success
# returns JoinError on failure
```

`JoinResponse` is a subclass of `RoomIdResponse` and has a `room_id` attribute
containing the canonical room ID (even if an alias was given). `JoinError` is
a subclass of `ErrorResponse` with a `message` attribute.

After a successful join, the room does not appear in `self._client.rooms` until
the next sync response delivers the join. `MatrixClient.join_room()` should
emit `RoomsChanged` optimistically (by running a sync or by inserting a minimal
placeholder); however, since a sync is already running continuously, the cleanest
approach is to simply await the next sync. The UI shows a toast "Joining…" and
the room appears in the list when sync delivers it. No optimistic placeholder
is needed for P0; that is a P1 enhancement.

### What changes

**`src/telemente/matrix/client.py`**

Add a new public method:

```python
async def join_room(self, room_id_or_alias: str) -> str:
    """Join a room by ID or alias.

    Returns the canonical room_id on success.
    Raises NotLoggedInError if not logged in.
    Raises MatrixError on failure (unknown room, permission denied, etc.).
    """
    if not self._logged_in:
        raise NotLoggedInError("Must be logged in to join a room")
    response = await self._client.join(room_id_or_alias)
    if isinstance(response, nio.JoinError):
        raise MatrixError(f"join_room failed for {room_id_or_alias!r}: {response.message}")
    logger.info(
        "join_room: joined %s (canonical: %s)", room_id_or_alias, response.room_id
    )
    return response.room_id
```

**`stubs/nio/__init__.pyi`**

Add stubs for `JoinResponse` and `JoinError` if not already present:

```python
class JoinResponse:
    room_id: str

class JoinError(ErrorResponse):
    message: str
```

**`src/telemente/tui/screens/join_room.py`** (new file)

A `JoinRoomScreen` that presents a single input field, validates the format,
calls `client.join_room()`, and dismisses with the canonical room_id on
success (or None on cancel/error).

```python
class JoinRoomScreen(Screen[str | None]):
    """Modal screen: enter a room ID or alias to join."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, client: _JoinRoomClient) -> None:
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Static("Join room", id="join-title")
        yield Input(
            id="join-input",
            placeholder="!roomid:server or #alias:server",
        )
        yield Static("", id="join-error", classes="error-message")
        yield Static("Enter a room ID or alias and press Enter.", id="join-hint")

    def on_mount(self) -> None:
        self.query_one("#join-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        if not (value.startswith("!") or value.startswith("#")):
            self.query_one("#join-error", Static).update(
                "Must start with ! (room ID) or # (alias)"
            )
            return
        self.run_worker(self._do_join(value), exclusive=True)

    async def _do_join(self, room_id_or_alias: str) -> None:
        error_widget = self.query_one("#join-error", Static)
        error_widget.update("Joining…")
        try:
            canonical_id = await self._client.join_room(room_id_or_alias)
        except Exception as exc:
            logger.warning("JoinRoomScreen._do_join failed: %s", exc)
            error_widget.update(f"Failed: {exc}")
            return
        self.dismiss(canonical_id)
```

Define the protocol:

```python
class _JoinRoomClient(Protocol):
    async def join_room(self, room_id_or_alias: str) -> str: ...
```

**`src/telemente/tui/screens/main.py`**

1. Add `join_room` to `_MainClient` protocol:

```python
async def join_room(self, room_id_or_alias: str) -> str: ...
```

2. Add `action_join_room` to `MainScreen`:

```python
def action_join_room(self) -> None:
    """Open the Join Room dialog."""
    from telemente.tui.screens.join_room import JoinRoomScreen

    def _on_joined(room_id: str | None) -> None:
        if room_id:
            self.app.notify(
                f"Joined {room_id} — room will appear when sync updates.",
                severity="information",
                timeout=5,
            )

    self.app.push_screen(JoinRoomScreen(self._client), _on_joined)
```

3. Add a keybinding (optional; the command palette is the canonical entry):

```python
BINDINGS: ClassVar[list[BindingType]] = [
    ...
    ("ctrl+j", "join_room", "Join room"),
]
```

**`src/telemente/tui/commands.py`**

Add a `DiscoveryHit` and command for joining a room. Following the existing
command palette pattern:

```python
yield DiscoveryHit(
    "Join room",
    self._action_join_room,
    help="Join a Matrix room by ID or alias",
)
```

Implement `_action_join_room` to call `self.app.get_screen("main").action_join_room()`.

**`tests/fakes.py`**

Add to `FakeMatrixClient`:

```python
# Scripted join results: room_id_or_alias -> canonical room_id
# If the alias is not in this dict, join_room raises MatrixError.
self.join_results: dict[str, str] = {}
# Spy
self.joined_rooms: list[str] = []
```

Add method:

```python
async def join_room(self, room_id_or_alias: str) -> str:
    if not self.logged_in:
        raise NotLoggedInError("Not logged in")
    self._check_fail("join_room")
    await self._maybe_block("join_room")
    self.joined_rooms.append(room_id_or_alias)
    if room_id_or_alias not in self.join_results:
        raise MatrixError(f"Unknown room: {room_id_or_alias}")
    return self.join_results[room_id_or_alias]
```

Update `reset_spies`:

```python
self.joined_rooms.clear()
```

---

## Implementation steps

1. Add `JoinResponse` / `JoinError` to `stubs/nio/__init__.pyi` if needed.
2. Add `MatrixClient.join_room()` to `client.py`.
3. Add `join_room` to `FakeMatrixClient` in `tests/fakes.py`.
4. Create `src/telemente/tui/screens/join_room.py`.
5. Add `action_join_room` and `join_room` protocol entry to `main.py`.
6. Add command palette entry to `commands.py`.
7. Write tests before steps 2–6.

---

## Tests

### Tier 1 — `tests/matrix/test_client_join.py`

```python
async def test_join_room_by_id_returns_canonical_id(
    restore_client, aioresponses_ctx
) -> None:
    """join_room with a !roomid:server returns the canonical room_id from the
    server response."""

async def test_join_room_by_alias_returns_canonical_id(
    restore_client, aioresponses_ctx
) -> None:
    """join_room with a #alias:server returns the canonical room_id."""

async def test_join_room_error_raises_matrix_error(
    restore_client, aioresponses_ctx
) -> None:
    """When the server returns a JoinError, join_room raises MatrixError with
    the server's error message."""

async def test_join_room_not_logged_in_raises(restore_client) -> None:
    """join_room raises NotLoggedInError when not logged in."""
```

Setup: use `restore_client()`, stub the join endpoint with `aioresponses`
returning a JSON `{"room_id": "!canonical:server"}` (success) or
`{"errcode": "M_NOT_FOUND", "error": "Room not found"}` (error).

### Tier 2 — `tests/tui/test_join_room.py`

```python
async def test_join_room_dialog_opens_via_action() -> None:
    """Calling action_join_room on MainScreen pushes JoinRoomScreen."""

async def test_join_room_dialog_opens_via_command_palette() -> None:
    """The 'Join room' command palette entry opens JoinRoomScreen."""

async def test_join_input_invalid_format_shows_error() -> None:
    """Entering a string that doesn't start with ! or # shows an error message
    and does not call join_room."""

async def test_join_success_dismisses_screen_with_room_id() -> None:
    """After a successful join, JoinRoomScreen dismisses and passes the canonical
    room_id to the callback."""

async def test_join_success_shows_toast_notification() -> None:
    """After a successful join, MainScreen displays a notification toast."""

async def test_join_failure_shows_error_in_dialog() -> None:
    """When join_room raises MatrixError, the error message is displayed in the
    dialog and the screen is not dismissed."""

async def test_join_escape_dismisses_without_joining() -> None:
    """Pressing Escape on JoinRoomScreen dismisses with None and does not call
    join_room."""

async def test_join_room_command_in_palette() -> None:
    """The command palette (Ctrl+P) contains a 'Join room' entry."""
```

Setup: build a host app with `FakeMatrixClient`, pre-populate
`fake.join_results["#test:example.org"] = "!canonical:example.org"` for
success tests; use `pilot.press()` and `await wait_for_workers(app)`.

---

## Done-when checklist

- [ ] `MatrixClient.join_room()` calls nio's `join()` and returns the canonical
      room_id, or raises `MatrixError`.
- [ ] `FakeMatrixClient.join_room` is scripted via `join_results` and records
      calls in `joined_rooms`.
- [ ] `JoinRoomScreen` is a standalone screen with input validation and error
      display.
- [ ] `MainScreen.action_join_room` opens `JoinRoomScreen` and shows a success
      toast.
- [ ] Command palette has a "Join room" entry reachable via `Ctrl+P`.
- [ ] `Ctrl+J` keybinding (or equivalent) triggers `action_join_room`.
- [ ] All Tier 1 and Tier 2 tests listed above pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
