# 0007 — Message View Panel

## Goal

The center panel: a scrollable message timeline for the selected room plus a
composer input. Renders sender / timestamp / body, appends live messages from
sync, sends on Enter via `MatrixClient.send_text`, and auto-scrolls to the
bottom.

## Dependencies

- 0003 (`Message`, `MatrixClient.send_text` / `messages` / `FakeMatrixClient`).
- 0005 (mounts this as the center panel).

## Files to create / modify

- `src/telemente/tui/widgets/message_view.py` — new (`MessageView`).
- `src/telemente/tui/styles/app.tcss` — message styling.
- `tests/tui/test_message_view.py` — new.

## Public interface

```python
# src/telemente/tui/widgets/message_view.py
class MessageView(Widget):
    async def load_room(self, room_id: str) -> None: ...  # fetch history, render
    def append_message(self, message: Message) -> None: ... # live event
    def clear(self) -> None: ...
    @property
    def current_room_id(self) -> str | None: ...
```

## Behavior / layout

- `compose`: a scrollable log region (use `VerticalScroll` containing per-message
  widgets, or a `RichLog`/`Log`; prefer per-message `Static`s in a
  `VerticalScroll` so styling and member colors are possible) plus an
  `Input(id="composer", placeholder="Message…")` pinned at the bottom.
- **load_room(room_id)**: store `current_room_id`, `clear()`, then
  `messages = await client.messages(room_id)`; render each (oldest→newest).
  Auto-scroll to bottom. Loading another room replaces content.
- **Rendering**: one line/block per message: `HH:MM  sender_display_name: body`.
  Timestamps from `Message.timestamp` (local time). Keep it simple for v0.1.0
  (plain text bodies only).
- **Sending**: on `Input.Submitted` of `#composer` with non-empty text and a
  `current_room_id`: call `await client.send_text(room_id, text)`, clear the
  composer. Do **not** optimistically append — the echo arrives via sync
  (`append_message`) in 0009. (If desired, document an optimistic-render option,
  but default is sync-driven to avoid duplicates.)
- **append_message**: if `message.room_id == current_room_id`, add it and
  auto-scroll if already near the bottom.
- Empty composer submit is a no-op.

## Test cases (write first)

`tests/tui/test_message_view.py` (host app mounts `MessageView(FakeMatrixClient)`):

1. `test_load_room_renders_messages_in_order` — fake returns 3 `Message`s for a
   room; `await view.load_room("!r:s")`; assert 3 rendered, oldest first, bodies
   present.
2. `test_switching_rooms_replaces_content` — load room A (2 msgs), then room B
   (1 msg); only B's message remains; `current_room_id == "B"`.
3. `test_send_on_enter_calls_send_text_and_clears` — load a room; set composer
   value; `pilot.press("enter")`; assert `FakeMatrixClient.send_text` called
   once with `(room_id, text)` and composer cleared.
4. `test_empty_composer_submit_noop` — Enter with empty composer → `send_text`
   not called.
5. `test_append_message_for_current_room` — load room A; `append_message` with a
   `Message` in A → it appears appended at the end.
6. `test_append_message_other_room_ignored` — load room A; append a `Message`
   for room B → not shown.

## Mocking strategy

- No network. `FakeMatrixClient.messages(room_id)` returns scripted lists;
  `send_text` is a spy recording `(room_id, body)`. Use `await pilot.pause()`
  after Enter so the worker/await for `send_text` completes before asserting.
- For send, the fake's `send_text` should be an `AsyncMock`-like spy.

## Done-when

- [ ] All 6 tests pass.
- [ ] History loads on room switch; Enter sends and clears; live append works;
      cross-room messages are ignored.
- [ ] `mypy --strict` + `ruff` clean.
