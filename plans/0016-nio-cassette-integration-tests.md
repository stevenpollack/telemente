# 0016 — nio Cassette Integration Tests

## Goal

Replace `MagicMock(spec=nio.AsyncClient)` unit tests with **real-nio integration
tests** that feed `aioresponses`-stubbed HTTP responses through the actual
`matrix-nio` parsing stack.  Every attribute access on a nio object becomes
validated by the real class rather than a spec-less mock — the class of bug
where `MatrixRoom.timeline` silently returns `None` (because `MagicMock` obliges
any attribute) becomes a hard `AttributeError` in CI.

## Motivation

The `_last_activity` sort bug survived review and unit tests because:

1. `_build_nio_mock()` creates `AsyncMock(spec=nio.AsyncClient)`.  Any attribute
   access that does not exist on the real class still returns another `AsyncMock`
   (specs only validate *called* methods, not nested attribute chains).
2. `SimpleNamespace(timeline=SimpleNamespace(events=[...]))` mimicked the shape
   of a *sync response* `RoomInfo`, not a `MatrixRoom`.  There is no connection
   between the two — the test proved nothing about the real object.
3. The real `SyncResponse` deserialized by nio sets `SyncResponse.rooms.join[id].timeline.events`
   (the `RoomInfo` path) but not `MatrixRoom.timeline`.  A test using the real
   parsing stack would have raised `AttributeError` immediately.

## Dependencies

- 0015 (nio stubs) is complementary but **not required** — cassette tests catch
  runtime attribute errors; stubs catch static-analysis errors.  They can land
  independently.
- Current dev dependencies (`aioresponses`, `pytest-asyncio`) are already
  present.  No new packages needed.

## Files to create / modify

```
tests/
  fixtures/
    nio/
      sync_initial.json        # full-state sync response, 3 rooms, 1 with timeline event
      sync_incremental.json    # incremental sync, 1 room with new message
      room_messages.json       # room_messages() response, 3 messages
  matrix/
    test_client.py             # extend: replace _build_nio_mock tests with real-nio equivalents
    conftest.py                # add real_nio_client fixture
```

## Fixture format

Fixtures are minimal but **protocol-correct** JSON that nio's parser actually
accepts.  Derive shapes from the Matrix Client-Server spec r0.6.0+ and verified
against a real Synapse instance.

### `tests/fixtures/nio/sync_initial.json`

Shape: a valid `/_matrix/client/v3/sync` response body.  Three rooms:

```json
{
  "next_batch": "s1_initial",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.message",
              "event_id": "$ev1:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000001000,
              "content": { "msgtype": "m.text", "body": "hello" }
            }
          ],
          "limited": false,
          "prev_batch": "p1"
        },
        "state": { "events": [] },
        "account_data": { "events": [] },
        "unread_notifications": { "notification_count": 1, "highlight_count": 0 }
      },
      "!room_b:example.com": {
        "timeline": { "events": [], "limited": false },
        "state": { "events": [] },
        "account_data": { "events": [] }
      },
      "!room_c:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.message",
              "event_id": "$ev2:example.com",
              "sender": "@bob:example.com",
              "origin_server_ts": 1700000003000,
              "content": { "msgtype": "m.text", "body": "newer" }
            }
          ],
          "limited": false,
          "prev_batch": "p3"
        },
        "state": { "events": [] },
        "account_data": { "events": [] }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

Expected after parsing: `room_c` has `last_activity > room_a > room_b (None)`.

### `tests/fixtures/nio/sync_incremental.json`

Shape: incremental sync, one room with a new message.

```json
{
  "next_batch": "s2_incremental",
  "rooms": {
    "join": {
      "!room_b:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.message",
              "event_id": "$ev3:example.com",
              "sender": "@carol:example.com",
              "origin_server_ts": 1700000010000,
              "content": { "msgtype": "m.text", "body": "now b is active" }
            }
          ],
          "limited": false,
          "prev_batch": "p2"
        },
        "state": { "events": [] },
        "account_data": { "events": [] }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

Expected: after processing, `room_b` gets `last_activity` from `origin_server_ts`.

### `tests/fixtures/nio/room_messages.json`

Shape: `/_matrix/client/v3/rooms/{roomId}/messages` response.

```json
{
  "start": "t1-start",
  "end": "t1-end",
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$msg1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000005000,
      "content": { "msgtype": "m.text", "body": "backfill message" }
    },
    {
      "type": "m.room.message",
      "event_id": "$msg2:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000006000,
      "content": { "msgtype": "m.text", "body": "newer backfill" }
    }
  ]
}
```

## `tests/matrix/conftest.py` additions

```python
import pytest
import nio

@pytest.fixture
async def real_nio_client() -> AsyncGenerator[nio.AsyncClient, None]:
    """A real nio.AsyncClient with in-memory store, no homeserver connection."""
    client = nio.AsyncClient("https://example.com", "@testuser:example.com")
    yield client
    await client.close()
```

## Test cases to add

All new tests use `real_nio_client` (real nio) + `aioresponses` (stubbed HTTP).
Remove or clearly mark the `_build_nio_mock`-based tests they replace.

### `test_update_last_activity_real_nio_initial_sync`

- Stub `POST /_matrix/client/v3/login` → login payload.
- Stub `GET /_matrix/client/v3/sync` → `sync_initial.json`.
- Call `client.restore(session)` then `await real_client.sync(full_state=True)`.
- Assert `client._last_activity["!room_c:example.com"] > client._last_activity["!room_a:example.com"]`.
- Assert `"!room_b:example.com" not in client._last_activity`.

This test directly validates the code path the `_newest_timestamp_from_nio_room`
bug broke.

### `test_update_last_activity_real_nio_incremental_sync`

- Restore session, initial sync, then incremental sync from `sync_incremental.json`.
- Assert `room_b` now has a `last_activity` timestamp.
- Assert `room_b.last_activity` equals `datetime(2023, 11, 14, ..., tzinfo=UTC)`
  (derived from `origin_server_ts: 1700000010000`).

### `test_rooms_sorted_by_recency_after_real_sync`

- Run initial sync from `sync_initial.json`.
- Call `client.rooms()`.
- Assert returned list order: `room_c` first, `room_a` second, `room_b` last
  (or absent since it has no timestamp).

### `test_messages_backfill_seeds_last_activity_real_nio`

- Stub `GET /_matrix/client/v3/rooms/!room_b:example.com/messages` → `room_messages.json`.
- Call `await client.messages("!room_b:example.com")`.
- Assert `client._last_activity["!room_b:example.com"]` matches the
  `origin_server_ts` of the newest message in the fixture.

This directly tests the `messages()` backfill-seeding fix.

### `test_matrix_room_has_no_timeline_attribute`

A regression guard — documents what the real nio class looks like:

```python
async def test_matrix_room_has_no_timeline_attribute(real_nio_client: nio.AsyncClient) -> None:
    """MatrixRoom has no .timeline — guard against reintroducing the dead fallback."""
    import nio
    # Construct a minimal MatrixRoom as nio does internally.
    room = nio.MatrixRoom("!r:example.com", "@me:example.com")
    assert not hasattr(room, "timeline"), (
        "nio.MatrixRoom gained a .timeline attribute — "
        "review _update_last_activity and remove this guard if intentional"
    )
```

## Fixture loading helper

Add to `conftest.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nio"

def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())
```

## Mocking approach for `sync_forever`

`_sync_loop` calls `sync_forever` which loops indefinitely.  Tests that need the
sync loop to run once should call `client.sync()` directly (not `start_sync()`),
which returns after one request.  Stubs must use `repeat=False` (default in
aioresponses) so a second call raises `ConnectionError`, providing a natural
stop condition if the code erroneously polls.

## Migration plan for existing `_build_nio_mock` tests

Do NOT delete all mock-based tests in one pass.  Migrate test by test:

1. For each test currently using `_build_nio_mock`, decide:
   - **Replace** if the test verifies nio parsing behaviour (attribute access,
     response deserialization, event dispatch).  Write the real-nio equivalent.
   - **Keep** if the test verifies `MatrixClient` business logic only (e.g.,
     error handling, state machine transitions) where mock return values are
     cleaner than full HTTP stubs.
2. `_build_nio_mock` may remain for purely-unit tests that don't touch nio object
   structure — remove only the tests where the mock was masking real behaviour.

## Done-when

- `uv run pytest -n auto tests/matrix/test_client.py` passes with the new
  cassette tests included.
- `test_matrix_room_has_no_timeline_attribute` passes (documents the invariant).
- `test_rooms_sorted_by_recency_after_real_sync` passes, proving the sort works
  end-to-end through real nio parsing.
- `uv run mypy` and `uv run ruff check .` still pass.
- CI time increase is < 2 s (all tests are in-process, no network, no Docker).

## What this does NOT do

- Does not require a running Synapse server.
- Does not use `vcrpy`, `pytest-recording`, or any new test dependency — pure
  `aioresponses` + JSON fixtures already in the repo.
- Does not replace all `_build_nio_mock` usage — only the tests where mock
  structure was silently masking real attribute shapes.
