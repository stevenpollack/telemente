# 0009 — Sync ↔ State Integration

## Goal

Wire `MatrixClient`'s background sync into the Textual app so the three panels
update live. The sync loop runs as a Textual worker on the same event loop;
client events are translated into Textual messages that the screen routes to the
right widget. This is the plumbing that makes telemente "live".

## Dependencies

- 0003 (`MatrixClient.subscribe`, `start_sync`, event dataclasses).
- 0005/0006/0007/0008 (the screen + the three widgets and their update methods).

## Files to create / modify

- `src/telemente/tui/app.py` — own the `MatrixClient` lifecycle; start sync;
  bridge client events → Textual messages.
- `src/telemente/tui/screens/main.py` — handle the bridged messages and dispatch
  to `RoomList.set_rooms`, `MessageView.append_message`, `MemberList.set_members`.
- `tests/tui/test_sync_integration.py` — new.

## Design

- **One loop**: after login/restore, the app calls `await client.start_sync()`,
  which runs `sync_forever` as an asyncio task on Textual's loop. Do **not** use
  threads.
- **Bridge**: the app subscribes to the client
  (`client.subscribe(self._on_client_event)`). `_on_client_event(event)` runs on
  the loop; it converts the `ClientEvent` (`RoomsChanged` / `NewMessage` /
  `MembersChanged`) into a Textual message and `self.post_message(...)` (thread-
  safe and order-preserving). Define thin Textual message wrappers if helpful,
  or post the dataclasses inside a single `ClientEventMessage`.
- **Routing** (in `MainScreen`):
  - `RoomsChanged` → `RoomList.set_rooms(rooms)` (preserve filter/selection).
  - `NewMessage` → `MessageView.append_message(msg)` (widget ignores other
    rooms) and bump unread on `RoomList` for non-active rooms.
  - `MembersChanged` → if it's the active room, `MemberList.set_members(...)`.
  - `RoomList.RoomSelected` → `await MessageView.load_room(id)` +
    `MemberList.load_room(id)` + clear that room's unread.
- **Teardown**: on app exit (`on_unmount` / `action_quit`), `await
  client.close()` to cancel sync and close the nio client cleanly.

## Test cases (write first)

`tests/tui/test_sync_integration.py` (host = real `TelementeApp` wired with a
`FakeMatrixClient` that can `emit(...)` scripted events):

1. `test_rooms_changed_updates_room_list` — start app on main screen; fake
   `emit(RoomsChanged([...3 rooms]))`; `await pilot.pause()`; `RoomList` shows 3.
2. `test_new_message_appends_to_active_room` — select room A; fake
   `emit(NewMessage(Message in A))`; the message appears in `MessageView`.
3. `test_new_message_other_room_bumps_unread` — active room A; emit a message
   for room B; `MessageView` unchanged, `RoomList` shows B's unread incremented.
4. `test_members_changed_updates_active_room` — active room A; emit
   `MembersChanged(A, [...])`; `MemberList` re-renders to the new set.
5. `test_members_changed_other_room_ignored` — active room A; emit
   `MembersChanged(B, ...)`; `MemberList` unchanged.
6. `test_room_selected_loads_message_and_members` — post
   `RoomList.RoomSelected(B)`; `MessageView.current_room_id == "B"` and
   `MemberList` shows B's members.
7. `test_close_cancels_sync` — exit the app; assert `FakeMatrixClient.close`
   was awaited and no asyncio task warnings (the sync task is cancelled).

## Mocking strategy

- `FakeMatrixClient` (from 0003) gains/uses: `subscribe(handler)`, `emit(event)`
  (calls every subscribed handler), `start_sync()` (no-op or sets a flag),
  `close()` (spy). Tests drive real Textual message flow via `emit` +
  `await pilot.pause()`; no network, no real nio, no libolm.
- Assert ordering/race-safety by always `await pilot.pause()` after `emit` so
  posted messages are processed before assertions.

## Done-when

- [ ] All 7 tests pass.
- [ ] Sync runs as a worker on Textual's loop; no threads; clean shutdown.
- [ ] UI updates live for rooms, messages, members; unread tracking works.
- [ ] UI still never imports `nio`; everything flows through `MatrixClient`.
- [ ] `mypy --strict` + `ruff` clean.
