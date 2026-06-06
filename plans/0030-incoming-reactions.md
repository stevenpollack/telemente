# Plan 0030 — Incoming live reactions (m.reaction callback)

## Goal

When another user sends an `m.reaction` during an active session, the reaction
chip on the original message updates immediately. Currently `ReactionEvent` is
not registered in `_register_callbacks`, so reactions from other users only
appear after the room is reloaded (via the backfill path in `messages()`).

---

## Dependencies

- Plans 0001–0029 complete.
- No additional runtime dependencies.

---

## Architecture

### What changes

**`src/telemente/matrix/client.py`**

1. Add a new `ClientEvent` subtype:

```python
@dataclass(frozen=True, slots=True)
class ReactionReceived:
    """A live m.reaction event arrived for an existing message."""

    room_id: str
    target_event_id: str  # the event being reacted to
    emoji: str
    sender: str
```

2. Extend the `ClientEvent` union:

```python
ClientEvent = (
    RoomsChanged | NewMessage | MembersChanged | TypingChanged
    | MessageRedacted | MessageEdited | ReactionReceived
)
```

3. Register the new callback in `_register_callbacks`:

```python
self._client.add_event_callback(self._on_reaction_event, nio.ReactionEvent)
```

4. Implement `_on_reaction_event`:

```python
async def _on_reaction_event(
    self, room: nio.MatrixRoom, event: nio.ReactionEvent
) -> None:
    logger.debug(
        "_on_reaction_event: room=%s target=%s key=%r sender=%s",
        room.room_id, event.reacts_to, event.key, event.sender,
    )
    await self._emit(
        ReactionReceived(
            room_id=room.room_id,
            target_event_id=event.reacts_to,
            emoji=event.key,
            sender=event.sender,
        )
    )
```

No cache update is required for reactions: the cache stores reactions as a JSON
dict in the `reactions` column (set during `messages()` backfill). Live
reactions are ephemeral in-session state; the next cold reload will pick them up
from the server. A cache update is a future optimisation (P2).

**`src/telemente/tui/screens/main.py`**

1. Import `ReactionReceived` from `telemente.matrix.client`.
2. Add `handle_reaction_received`:

```python
def handle_reaction_received(self, event: ReactionReceived) -> None:
    view = self.message_view_for(event.room_id)
    if view is not None:
        view.apply_reaction(event.target_event_id, event.emoji, event.sender)
    # Forward to thread panel if open and relevant.
    if self.thread_visible:
        panel = self.query_one(ThreadPanel)
        if panel.room_id == event.room_id:
            panel.apply_reaction(event.target_event_id, event.emoji, event.sender)
```

**`src/telemente/tui/widgets/message_view.py`**

Add a public `apply_reaction(target_event_id, emoji, sender)` method:

```python
def apply_reaction(
    self, target_event_id: str, emoji: str, sender: str
) -> None:
    """Apply a live incoming reaction from another user to a rendered row."""
    for row in self.query(MessageRow):
        if row.message.event_id == target_event_id:
            row.update_reaction(emoji, sender)
            return
    logger.debug(
        "apply_reaction: target=%s not in current view (room=%s)",
        target_event_id,
        self._current_room_id,
    )
```

`MessageRow.update_reaction(emoji, user_id)` already exists and handles both
the "new emoji chip" and "increment existing chip" cases.

**`src/telemente/tui/widgets/thread_panel.py`**

Add `apply_reaction(target_event_id, emoji, sender)` with the same delegation
to `MessageRow.update_reaction`.

**`src/telemente/tui/app.py`**

Route `ReactionReceived` events to `MainScreen.handle_reaction_received` in
the subscriber dispatch.

**`stubs/nio/__init__.pyi`**

Confirm `ReactionEvent` is already in the stubs. If not, add:

```python
class ReactionEvent(Event):
    reacts_to: str
    key: str
```

**`tests/fakes.py`**

No new methods needed. Tests push scripted `ReactionReceived` events via
`fake.emit(ReactionReceived(...))`.

---

## Implementation steps

1. Verify `nio.ReactionEvent` is in `stubs/nio/__init__.pyi`; add stub entry if
   missing.
2. Add `ReactionReceived` dataclass to `client.py`; extend `ClientEvent` union.
3. Register `_on_reaction_event` in `_register_callbacks`; implement it.
4. Add `apply_reaction` to `MessageView`.
5. Add `apply_reaction` to `ThreadPanel`.
6. Add `handle_reaction_received` to `MainScreen`; route in `app.py`.
7. Write tests before steps 2–6.

---

## Tests

### Tier 1 — `tests/matrix/test_client_reactions.py`

```python
async def test_reaction_event_emits_reaction_received(
    restore_client, event_callback_for
) -> None:
    """When a nio.ReactionEvent fires via the callback, MatrixClient emits
    ReactionReceived with the correct room_id, target_event_id, emoji, sender."""

async def test_reaction_event_callback_is_registered(
    restore_client,
) -> None:
    """After restore(), a ReactionEvent callback is registered on the nio client."""

async def test_reaction_received_has_correct_target(
    restore_client, event_callback_for
) -> None:
    """ReactionReceived.target_event_id matches event.reacts_to from the nio event."""

async def test_reaction_received_has_correct_emoji(
    restore_client, event_callback_for
) -> None:
    """ReactionReceived.emoji matches event.key from the nio ReactionEvent."""
```

Setup: use `restore_client()` for an authenticated client; use
`event_callback_for(client, nio.ReactionEvent)` to get the registered callback;
construct a minimal fake `nio.ReactionEvent` via `SimpleNamespace` (matching
the real attributes: `event_id`, `sender`, `server_timestamp`, `reacts_to`,
`key`); call the callback; assert the emitted event.

### Tier 2 — `tests/tui/test_incoming_reactions.py`

```python
async def test_live_reaction_updates_chip_count() -> None:
    """When a ReactionReceived event arrives for a loaded message, the reaction
    chip count increments (or appears if no chip existed)."""

async def test_live_reaction_for_different_room_is_ignored() -> None:
    """A ReactionReceived event for a room not currently open has no visible
    effect and does not raise."""

async def test_live_reaction_for_unknown_target_is_silent() -> None:
    """A ReactionReceived whose target_event_id is not rendered logs at debug
    and does not raise."""

async def test_live_reaction_adds_new_chip_when_first_reactor() -> None:
    """When no chip exists for the emoji yet, a new chip is created showing
    '<emoji> 1'."""

async def test_live_reaction_does_not_duplicate_for_same_sender() -> None:
    """If the same sender is already in the senders list, the count does not
    increase (MessageRow.update_reaction deduplication)."""

async def test_live_reaction_forwarded_to_thread_panel() -> None:
    """If a thread panel is open for the same room, apply_reaction is called on
    the thread panel for matching target_event_ids."""
```

Setup: build a minimal `App` subclass with a `FakeMatrixClient`, mount a
`MessageView`, load a room with one message (potentially with an existing
reaction), call `await fake.emit(ReactionReceived(...))`, assert chip text.

---

## Done-when checklist

- [ ] `ReactionReceived` dataclass exists in `client.py` and is in `ClientEvent`.
- [ ] `nio.ReactionEvent` stub entry exists in `stubs/nio/__init__.pyi`.
- [ ] `MatrixClient._on_reaction_event` is registered and emits `ReactionReceived`.
- [ ] Backfill reactions in `messages()` are unaffected (no regression).
- [ ] `MessageView.apply_reaction` updates the correct `MessageRow` chip.
- [ ] `ThreadPanel.apply_reaction` updates thread rows.
- [ ] `MainScreen.handle_reaction_received` routes to view and thread panel.
- [ ] All Tier 1 and Tier 2 tests listed above pass.
- [ ] `uv run ruff check .` / `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
