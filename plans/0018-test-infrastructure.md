# Plan 0018 — Test infrastructure: cassette expansion & FakeMatrixClient hardening

## Goal

Make the two-tier testing architecture comprehensive enough that every new
feature can be fully specified and verified without a live homeserver. Right
now both tiers have coverage gaps that force tests to either skip realistic
scenarios or reach around the intended boundaries.

---

## Background: the two-tier model

```
┌────────────────────────────────────────────────────────────────────┐
│  Tier 1: MatrixClient integration tests   (tests/matrix/)          │
│  Real nio + aioresponses cassettes                                 │
│  What it proves: "our parsing and protocol glue is correct"        │
│  What it does NOT test: Textual widgets, layouts, key bindings     │
└────────────────────────────────────────────────────────────────────┘
          │  MatrixClient.subscribe() → ClientEvent stream
          ▼
┌────────────────────────────────────────────────────────────────────┐
│  Tier 2: TUI tests                        (tests/tui/)             │
│  FakeMatrixClient injected via DI seam                             │
│  What it proves: "the UI reacts correctly to every client event"   │
│  What it does NOT need: HTTP stubs, nio, network                   │
└────────────────────────────────────────────────────────────────────┘
```

The tiers are independent by design. Tier 1 verifies that
`MatrixClient` produces the right `ClientEvent` objects given realistic
Matrix HTTP traffic. Tier 2 verifies that the TUI does the right thing
when it receives those events. Neither tier needs the other to run.

Adding a new feature always means work in both tiers:
- Tier 1: add/extend cassettes and write client integration tests.
- Tier 2: extend `FakeMatrixClient` and write TUI tests.

---

## Part 1 — Cassette expansion

### 1.1 What the current cassettes cover (and don't)

| File | Covers | Missing |
|------|--------|---------|
| `login.json` | Password login success | SSO token exchange, login flows endpoint |
| `sync_initial.json` | 3 rooms, 2 text events, 1 unread badge | State events, room names, encrypted rooms, member events, tags, limited timeline, invites, leave section, ephemeral |
| `sync_incremental.json` | 1 new message in 1 room | Reactions, edits, redactions, media, typing, read receipts, membership changes |
| `room_messages.json` | 2 plain text backfill messages | Reactions, edits, redacted messages, media, encrypted events, reply threads, pagination |

Everything not covered means we cannot write a cassette-backed test for that
scenario — forcing us to use mock objects and lose confidence in the parsing.

### 1.2 Cassettes to add

All files go in `tests/fixtures/nio/synthetic/`. Every cassette is a
minimal but protocol-correct JSON object. Timestamps are fixed; room IDs
are the same three used today (`!room_a`, `!room_b`, `!room_c`).

---

#### `login_flows.json`
Response to `GET /_matrix/client/v3/login`.

```json
{
  "flows": [
    { "type": "m.login.password" },
    {
      "type": "m.login.sso",
      "identity_providers": [
        { "id": "gitlab", "name": "GitLab" }
      ]
    }
  ]
}
```

**Enables:** tier-1 test that `login_flows()` parses both password and SSO
flows from a real HTTP response (currently the SSO plan tests only use
`FakeMatrixClient`).

---

#### `sync_with_state.json`
Initial sync that includes state events: `m.room.name`, `m.room.member` (join
for two users), `m.room.power_levels`.

```json
{
  "next_batch": "s_state",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "state": {
          "events": [
            {
              "type": "m.room.name",
              "event_id": "$name1:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000000000,
              "state_key": "",
              "content": { "name": "General" }
            },
            {
              "type": "m.room.member",
              "event_id": "$member_alice:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000000001,
              "state_key": "@alice:example.com",
              "content": { "membership": "join", "displayname": "Alice" }
            },
            {
              "type": "m.room.member",
              "event_id": "$member_bob:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000000002,
              "state_key": "@bob:example.com",
              "content": { "membership": "join", "displayname": "Bob" }
            },
            {
              "type": "m.room.power_levels",
              "event_id": "$pl1:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000000003,
              "state_key": "",
              "content": {
                "users": { "@alice:example.com": 100 },
                "users_default": 0
              }
            }
          ]
        },
        "timeline": { "events": [], "limited": false },
        "account_data": { "events": [] }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

**Enables:** tier-1 test that `members()` returns the correct `Member` list
after a sync that carries state events; verifies display names and power
levels are parsed.

---

#### `sync_with_tags.json`
Sync response where a room's `account_data` contains an `m.tag` event.

```json
{
  "next_batch": "s_tags",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": { "events": [], "limited": false },
        "state": { "events": [] },
        "account_data": {
          "events": [
            {
              "type": "m.tag",
              "content": {
                "tags": {
                  "m.favourite": { "order": 0.5 }
                }
              }
            }
          ]
        }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

**Note:** nio parses `m.tag` into `MatrixRoom.tags`. This cassette lets us
verify end-to-end that the `tags` dict in `RoomSummary` is populated after
a sync, not just after a manual `set_room_tag()`.

---

#### `sync_with_invite.json`
Sync that includes an invitation in `rooms.invite`.

```json
{
  "next_batch": "s_invite",
  "rooms": {
    "join": {},
    "invite": {
      "!invited_room:example.com": {
        "invite_state": {
          "events": [
            {
              "type": "m.room.name",
              "state_key": "",
              "sender": "@bob:example.com",
              "content": { "name": "Secret Club" }
            },
            {
              "type": "m.room.member",
              "state_key": "@alice:example.com",
              "sender": "@bob:example.com",
              "content": { "membership": "invite", "displayname": "Alice" }
            }
          ]
        }
      }
    },
    "leave": {}
  }
}
```

**Enables:** future invite-accept/decline feature tests at tier 1.

---

#### `sync_with_leave.json`
Sync that includes a room in `rooms.leave` (the server confirming a departure).

```json
{
  "next_batch": "s_leave",
  "rooms": {
    "join": {},
    "invite": {},
    "leave": {
      "!room_b:example.com": {
        "timeline": { "events": [], "limited": false },
        "state": { "events": [] }
      }
    }
  }
}
```

**Enables:** verifying that `_on_sync` properly prunes `_left_rooms` once
nio confirms the departure.

---

#### `sync_limited_timeline.json`
A room whose timeline has `limited: true` and a `prev_batch` token. This is
what nio delivers when there are more events than the sync window.

```json
{
  "next_batch": "s_limited",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.message",
              "event_id": "$latest:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000100000,
              "content": { "msgtype": "m.text", "body": "latest message" }
            }
          ],
          "limited": true,
          "prev_batch": "t10-before_the_gap"
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

**Enables:** verifying that a limited timeline still sets `last_activity`
correctly; future "load more" / pagination tests.

---

#### `sync_with_encrypted_room.json`
A room with `m.room.encryption` in state, a join membership, and a
`m.room.encrypted` (MegolmEvent) in the timeline.

```json
{
  "next_batch": "s_encrypted",
  "rooms": {
    "join": {
      "!room_enc:example.com": {
        "state": {
          "events": [
            {
              "type": "m.room.encryption",
              "event_id": "$enc_state:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000000000,
              "state_key": "",
              "content": { "algorithm": "m.megolm.v1.aes-sha2" }
            }
          ]
        },
        "timeline": {
          "events": [
            {
              "type": "m.room.encrypted",
              "event_id": "$enc_msg:example.com",
              "sender": "@bob:example.com",
              "origin_server_ts": 1700000200000,
              "content": {
                "algorithm": "m.megolm.v1.aes-sha2",
                "sender_key": "abc123",
                "session_id": "session123",
                "ciphertext": "AQIDBA=="
              }
            }
          ],
          "limited": false
        },
        "account_data": { "events": [] },
        "unread_notifications": { "notification_count": 1, "highlight_count": 0 }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

**Enables:** verifying that an encrypted room shows up in `rooms()` with
`encrypted=True`; that the MegolmEvent in the sync timeline triggers the
`_on_megolm_event` callback (via `sync_forever`); and that `NewMessage` is
emitted with the lock-emoji placeholder.

---

#### `sync_with_reactions.json`
Incremental sync with an `m.reaction` event in the timeline (for testing
the live-reaction path, not the backfill path).

```json
{
  "next_batch": "s_reaction",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.reaction",
              "event_id": "$rxn1:example.com",
              "sender": "@bob:example.com",
              "origin_server_ts": 1700000300000,
              "content": {
                "m.relates_to": {
                  "rel_type": "m.annotation",
                  "event_id": "$ev1:example.com",
                  "key": "👍"
                }
              }
            }
          ],
          "limited": false
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

**Enables:** tier-1 test for the live-reaction path once a `ReactionsUpdated`
client event is added to `MatrixClient`.

---

#### `sync_with_edit.json`
Incremental sync delivering an `m.replace` (edit) event.

```json
{
  "next_batch": "s_edit",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.message",
              "event_id": "$edit1:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000400000,
              "content": {
                "msgtype": "m.text",
                "body": "* corrected body",
                "m.new_content": { "msgtype": "m.text", "body": "corrected body" },
                "m.relates_to": {
                  "rel_type": "m.replace",
                  "event_id": "$ev1:example.com"
                }
              }
            }
          ],
          "limited": false
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

**Enables:** verifying that `_on_room_message` ignores `m.replace` events
(existing test uses mock objects; this upgrades it to real nio parsing).

---

#### `sync_with_redaction.json`
Incremental sync delivering an `m.room.redaction` event.

```json
{
  "next_batch": "s_redaction",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": {
          "events": [
            {
              "type": "m.room.redaction",
              "event_id": "$redact1:example.com",
              "sender": "@alice:example.com",
              "origin_server_ts": 1700000500000,
              "redacts": "$ev1:example.com",
              "content": { "reason": "spam" }
            }
          ],
          "limited": false
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

**Enables:** future live-redaction event handling. Right now `MatrixClient`
doesn't subscribe to redaction events in the sync stream; this cassette is
pre-positioned for that work.

---

#### `sync_with_typing.json`
Incremental sync with an ephemeral `m.typing` event.

```json
{
  "next_batch": "s_typing",
  "rooms": {
    "join": {
      "!room_a:example.com": {
        "timeline": { "events": [], "limited": false },
        "state": { "events": [] },
        "account_data": { "events": [] },
        "ephemeral": {
          "events": [
            {
              "type": "m.typing",
              "content": {
                "user_ids": ["@bob:example.com"]
              }
            }
          ]
        }
      }
    },
    "invite": {},
    "leave": {}
  }
}
```

**Enables:** future `TypingIndicator` client event and UI widget.

---

#### `room_messages_with_reactions.json`
Backfill response containing text events and their reaction events.

```json
{
  "start": "t2-start",
  "end": "t2-end",
  "chunk": [
    {
      "type": "m.reaction",
      "event_id": "$rxn_a:example.com",
      "sender": "@bob:example.com",
      "origin_server_ts": 1700000006500,
      "content": {
        "m.relates_to": {
          "rel_type": "m.annotation",
          "event_id": "$msg2:example.com",
          "key": "👍"
        }
      }
    },
    {
      "type": "m.room.message",
      "event_id": "$msg2:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000006000,
      "content": { "msgtype": "m.text", "body": "newer backfill" }
    },
    {
      "type": "m.room.message",
      "event_id": "$msg1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000005000,
      "content": { "msgtype": "m.text", "body": "backfill message" }
    }
  ]
}
```

**Enables:** tier-1 test that `messages()` aggregates reactions from a real
nio-parsed backfill response (currently only tested with mock objects).

---

#### `room_messages_with_replies.json`
Backfill response containing a reply chain.

```json
{
  "start": "t3-start",
  "end": "t3-end",
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$reply1:example.com",
      "sender": "@bob:example.com",
      "origin_server_ts": 1700000007000,
      "content": {
        "msgtype": "m.text",
        "body": "> <@alice:example.com> original\n\nreplying",
        "m.relates_to": {
          "m.in_reply_to": { "event_id": "$msg1:example.com" }
        }
      }
    },
    {
      "type": "m.room.message",
      "event_id": "$msg1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000005000,
      "content": { "msgtype": "m.text", "body": "original" }
    }
  ]
}
```

**Enables:** tier-1 test for `reply_to_event_id` parsing from real nio; also
pre-positions the cassette for threaded-message rendering work.

---

#### `room_messages_with_media.json`
Backfill with image, video, and file events.

```json
{
  "start": "t4-start",
  "end": "t4-end",
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$img1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000008000,
      "content": {
        "msgtype": "m.image",
        "body": "photo.jpg",
        "url": "mxc://example.com/photo123"
      }
    },
    {
      "type": "m.room.message",
      "event_id": "$vid1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000009000,
      "content": {
        "msgtype": "m.video",
        "body": "clip.mp4",
        "url": "mxc://example.com/video456"
      }
    }
  ]
}
```

**Note:** `mxc_to_http` is a pure URL transformation, not an HTTP call, so
no stub is needed for that part; the test just verifies that media events are
correctly categorized.

---

#### `room_messages_with_redacted.json`
Backfill containing a server-redacted event (content stripped by the server,
`unsigned.redacted_because` present).

```json
{
  "start": "t5-start",
  "end": "t5-end",
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$msg1:example.com",
      "sender": "@alice:example.com",
      "origin_server_ts": 1700000005000,
      "content": {},
      "unsigned": {
        "redacted_because": {
          "type": "m.room.redaction",
          "sender": "@alice:example.com",
          "content": { "reason": "oops" }
        }
      }
    }
  ]
}
```

**Enables:** verifying how `messages()` handles server-side redacted events
(currently undefined behavior — does nio produce a `RoomMessageText` with
empty body, or a different event type?). The cassette will reveal the nio
behaviour and the test will pin it.

---

#### `room_send_response.json`
Response to `PUT /_matrix/client/v3/rooms/{id}/send/{type}/{txnid}`.

```json
{ "event_id": "$new_event:example.com" }
```

**Enables:** tier-1 integration test that `send_text()` returns the
server-assigned `event_id` parsed from a real nio response.

---

#### `room_redact_response.json`
Response to `PUT /_matrix/client/v3/rooms/{id}/redact/{event_id}/{txnid}`.

```json
{ "event_id": "$redact_event:example.com" }
```

**Enables:** tier-1 test that `redact_message()` parses the nio response
correctly.

---

#### `room_messages_paginated.json`
Second page of backfill (for pagination/load-more testing).

```json
{
  "start": "t1-end",
  "end": "t1-page2-end",
  "chunk": [
    {
      "type": "m.room.message",
      "event_id": "$older2:example.com",
      "sender": "@carol:example.com",
      "origin_server_ts": 1700000002000,
      "content": { "msgtype": "m.text", "body": "page 2 message" }
    }
  ]
}
```

**Enables:** future `messages(room_id, from_token=...)` pagination feature.
The cassette pair (`room_messages.json` page 1 → `room_messages_paginated.json`
page 2) fully describes the protocol exchange.

---

#### `error_rate_limit.json`
Standard Matrix `M_LIMIT_EXCEEDED` error body.

```json
{
  "errcode": "M_LIMIT_EXCEEDED",
  "error": "Too many requests",
  "retry_after_ms": 2000
}
```

**Enables:** tier-1 tests that `send_text()` / `messages()` surface
`MatrixError` on a 429 response; future retry logic.

---

### 1.3 Cassette authoring conventions

1. **Fixed IDs everywhere.** Room IDs are always one of the three canonical
   ones. Event IDs are `$<slug>:<file-context>:example.com`. Timestamps
   increase monotonically within a file (origin_server_ts in ms).

2. **Minimal but complete.** Include only the fields that nio actually
   parses for the feature under test. Don't copy-paste full Synapse
   responses — they add noise and make future diffs hard to read.

3. **One scenario per file.** Don't combine typing + reactions + redactions
   in a single sync fixture. Composing scenarios is done in the test by
   calling `stub_sync()` multiple times with different fixtures.

4. **No secrets.** Tokens in cassettes are always `access_token_xyz` (the
   constant in `helpers.py`).

5. **Verify with real nio before committing.** Write the test first, run it
   against the cassette, and only commit once nio parses the fixture
   without errors.

---

### 1.4 New helper functions needed in `tests/matrix/helpers.py`

```python
def stub_room_send(
    m: HttpMocker,
    room_id: str,
    *,
    event_type: str = "m.room.message",
    event_id: str = "$new_event:example.com",
    homeserver: str = HOMESERVER,
) -> None:
    """Stub PUT /rooms/{id}/send/{type}/{txnid}."""
    pattern = re.compile(
        rf"^{re.escape(homeserver)}/_matrix/client/v3/rooms/"
        rf"{re.escape(room_id)}/send/{re.escape(event_type)}/[^/]+$"
    )
    stub_put(m, pattern, payload={"event_id": event_id})


def stub_room_redact(
    m: HttpMocker,
    room_id: str,
    event_id: str,
    *,
    homeserver: str = HOMESERVER,
) -> None:
    """Stub PUT /rooms/{id}/redact/{event_id}/{txnid}."""
    pattern = re.compile(
        rf"^{re.escape(homeserver)}/_matrix/client/v3/rooms/"
        rf"{re.escape(room_id)}/redact/{re.escape(event_id)}/[^/]+$"
    )
    stub_put(m, pattern, payload={"event_id": "$redact_event:example.com"})


def stub_login_flows(
    m: HttpMocker,
    payload: dict[str, Any] | None = None,
    *,
    homeserver: str = HOMESERVER,
    status: int = 200,
) -> None:
    """Stub GET /_matrix/client/v3/login for login-flows discovery."""
    url = f"{homeserver}/_matrix/client/v3/login"
    stub_get(m, url, payload=payload or load_fixture("login_flows.json"), status=status)


def stub_room_messages(
    m: HttpMocker,
    room_id: str,
    payload: dict[str, Any],
    *,
    homeserver: str = HOMESERVER,
) -> None:
    """Stub GET /rooms/{id}/messages (convenience wrapper)."""
    stub_get(m, room_messages_url_pattern(room_id, homeserver=homeserver), payload=payload)
```

The existing `start_sync_with_stubs()` covers the sync lifecycle; these
new stubs cover the other HTTP surfaces.

---

## Part 2 — FakeMatrixClient hardening

### 2.1 What it is and why it exists

`FakeMatrixClient` sits at the dependency-injection seam between the UI
and the network. It is **not** a recording of the protocol; it is a
programmable stub that the test controls. Its job is:

- Accept scripted state (rooms, messages, members) for query methods.
- Accept scripted failure modes so the UI's error paths are exercised.
- Record calls so tests can assert "the UI did call X".
- Emit events via `.emit()` so the UI's reactive paths are exercised
  without any timing or network dependency.

It must mirror the **public surface** of `MatrixClient` exactly (same
method signatures, same exceptions). Any divergence is a DI contract
violation and will cause silent test/prod divergence.

### 2.2 Current gaps

| Gap | Impact |
|-----|--------|
| Only `login()` can be blocked/failed. All other async operations always succeed. | Can't test loading spinners, retry prompts, or error banners for send/fetch failures. |
| `messages()` ignores `limit`. | Pagination tests are impossible. |
| No scripted failure for `send_text`, `send_reaction`, `edit_message`, `redact_message`, `leave_room`, `set_room_tag`, `remove_room_tag`. | UI error-state tests for all write operations are untestable. |
| No way to simulate network errors (connection refused, timeout). | Can't test the "server unreachable" UX path. |
| `send_text()` returns a deterministic fake event_id but doesn't emit a `NewMessage` back. | The "optimistic message insert" pattern can't be tested at the UI tier. |
| `messages()` always returns the full scripted list in one call. | Paginated load ("load older messages") is untestable. |
| No `send_text_should_block` / similar. | Can't test "message sending in progress" indicator. |
| `me()` always returns `"@fake:matrix.org"` / `"Fake User"`. | Tests that show the logged-in user's display name in the UI can't verify the correct value. |
| No `send_should_fail_with` to control the exception type. | UI must distinguish `NotLoggedInError` from `MatrixError`; only one is testable right now. |
| Subscription counter not exposed. | Can't assert that the UI subscribed / unsubscribed the correct number of times. |

### 2.3 Proposed changes

All changes are additive. No existing API is removed or renamed.

---

#### 2.3.1 Per-operation failure scripting

Add a `_fail_ops: set[str]` attribute and a `fail_next(op)` method.
When `fail_next("send_text")` has been called, the next call to
`send_text()` raises `MatrixError` and removes `"send_text"` from the set
(one-shot failure). For persistent failures, add a `_always_fail: set[str]`
variant.

```python
# New attributes in __init__
self._fail_next: set[str] = set()
self._always_fail: set[str] = set()

# New scripting methods
def fail_next(self, op: str) -> None:
    """Make the next call to `op` raise MatrixError."""
    self._fail_next.add(op)

def always_fail(self, op: str) -> None:
    """Make every call to `op` raise MatrixError until cleared."""
    self._always_fail.add(op)

def clear_failures(self, op: str | None = None) -> None:
    """Clear failure scripting for `op`, or all ops if None."""
    if op is None:
        self._fail_next.clear()
        self._always_fail.clear()
    else:
        self._fail_next.discard(op)
        self._always_fail.discard(op)

# Internal helper (private)
def _check_fail(self, op: str) -> None:
    if op in self._always_fail:
        raise MatrixError(f"Scripted failure: {op}")
    if op in self._fail_next:
        self._fail_next.discard(op)
        raise MatrixError(f"Scripted failure: {op}")
```

Then every write operation gains a `self._check_fail("send_text")` line
at the top (after the login check).

This pattern is composable: to test "send fails with not-logged-in", the
test just doesn't call `fake.logged_in = True`; to test "send fails with
server error", it calls `fake.fail_next("send_text")`.

---

#### 2.3.2 Per-operation blocking (latency simulation)

Extend the `login_should_block` / `unblock_login()` pattern to all async
write operations.

```python
# New attributes
self._blocked_ops: dict[str, asyncio.Event] = {}

# New scripting methods
def block_op(self, op: str) -> None:
    """Make the next call to `op` block until unblock_op(op) is called."""
    ev = asyncio.Event()
    self._blocked_ops[op] = ev

def unblock_op(self, op: str) -> None:
    """Release a blocked operation."""
    ev = self._blocked_ops.pop(op, None)
    if ev:
        ev.set()

# Internal helper
async def _maybe_block(self, op: str) -> None:
    ev = self._blocked_ops.get(op)
    if ev is not None:
        await ev.wait()
        self._blocked_ops.pop(op, None)
```

Usage in test:

```python
fake.block_op("send_text")
# ... trigger the UI send action ...
# assert loading indicator is visible
fake.unblock_op("send_text")
# ... assert message appeared, indicator gone
```

---

#### 2.3.3 Scripted `me()` return value

```python
# __init__
self._me: tuple[str, str] = ("@fake:matrix.org", "Fake User")

# New scripting method
def set_me(self, user_id: str, display_name: str) -> None:
    self._me = (user_id, display_name)

# Updated method
def me(self) -> tuple[str, str]:
    return self._me
```

---

#### 2.3.4 Paginated `messages()` with a cursor

Real `MatrixClient.messages()` takes a `limit` parameter. Future work will
add a `from_token` parameter for pagination. Pre-position the fake now.

```python
# __init__
self.messages_data: dict[str, list[Message]] = {}
self._messages_page_size: int = 50  # mirrors default limit

# Scripting method
def set_messages_page_size(self, size: int) -> None:
    """Limit how many messages messages() returns per call (simulates pagination)."""
    self._messages_page_size = size

# Updated method
async def messages(self, room_id: str, limit: int = 50) -> list[Message]:
    all_msgs = list(self.messages_data.get(room_id, []))
    effective_limit = min(limit, self._messages_page_size)
    return all_msgs[:effective_limit]
```

---

#### 2.3.5 Auto-emit `NewMessage` on `send_text()`

When a user sends a message in the real app, the server echoes it back in
the next sync and `_on_room_message` emits `NewMessage`. The fake can
optionally mirror this.

```python
# __init__
self.auto_emit_sent_messages: bool = False

# Updated send_text
async def send_text(self, room_id: str, body: str, reply_to_event_id: str | None = None) -> str:
    if not self.logged_in:
        raise NotLoggedInError("Not logged in")
    self._check_fail("send_text")
    await self._maybe_block("send_text")
    self.sent_messages.append((room_id, body, reply_to_event_id))
    event_id = f"$fake_sent_{len(self.sent_messages)}:matrix.org"
    if self.auto_emit_sent_messages:
        from telemente.matrix.client import NewMessage
        from telemente.matrix.models import Message
        from datetime import UTC, datetime
        msg = Message(
            event_id=event_id,
            room_id=room_id,
            sender=self._me[0],
            sender_display_name=self._me[1],
            body=body,
            timestamp=datetime.now(UTC),
            reply_to_event_id=reply_to_event_id,
        )
        await self.emit(NewMessage(message=msg))
    return event_id
```

Set `fake.auto_emit_sent_messages = True` in tests that need to verify the
full send → display cycle.

---

#### 2.3.6 Subscription counter spy

```python
# __init__
self.subscribe_count: int = 0
self.unsubscribe_count: int = 0

# Updated subscribe
def subscribe(self, handler: EventHandler) -> Callable[[], None]:
    self._handlers.append(handler)
    self.subscribe_count += 1

    def _unsubscribe() -> None:
        with contextlib.suppress(ValueError):
            self._handlers.remove(handler)
        self.unsubscribe_count += 1

    return _unsubscribe
```

---

#### 2.3.7 `emit_sequence()` for scripted event trains

Convenience method to push multiple events in order, letting the event loop
drain between each. Useful for tests that set up complex room state.

```python
async def emit_sequence(self, *events: ClientEvent, pause: float = 0.0) -> None:
    """Emit events in order, optionally sleeping between each."""
    for event in events:
        await self.emit(event)
        if pause > 0:
            await asyncio.sleep(pause)
```

---

#### 2.3.8 `reset()` for test isolation

Tests that reuse a `FakeMatrixClient` across multiple assertions sometimes
need to clear spy state without recreating the object.

```python
def reset_spies(self) -> None:
    """Clear all call recording without affecting scripted state."""
    self.login_called = False
    self.start_sync_called = False
    self.close_called = False
    self.sent_messages.clear()
    self.sent_reactions.clear()
    self.edited_messages.clear()
    self.redacted_messages.clear()
    self.left_rooms.clear()
    self.set_tags.clear()
    self.removed_tags.clear()
    self.login_with_token_called = False
    self.login_with_token_token = ""
    self.sso_redirect_url_called = False
    self.sso_redirect_url_idp_id = None
    self.subscribe_count = 0
    self.unsubscribe_count = 0
```

---

#### 2.3.9 Typed `NotLoggedInError` variant for failure scripting

Some UI flows need to distinguish "server rejected" from "not authenticated".
Add a separate flag:

```python
self.raise_not_logged_in: bool = False  # in __init__

def _check_fail(self, op: str) -> None:
    if self.raise_not_logged_in:
        raise NotLoggedInError(f"Scripted not-logged-in: {op}")
    # ... rest of existing logic
```

---

### 2.4 Summary of FakeMatrixClient state after changes

```
Scripted state:
  rooms_data, members_data, messages_data   (as before)
  _flows, homeserver, _me                   (extended)
  _messages_page_size                        (new)
  login_should_fail, login_should_block      (as before)
  login_with_token_should_fail               (as before)
  _fail_next, _always_fail                   (new)
  _blocked_ops                               (new)
  auto_emit_sent_messages                    (new)
  raise_not_logged_in                        (new)

Spies:
  login_called, start_sync_called, close_called  (as before)
  sent_messages, sent_reactions, edited_messages  (as before)
  redacted_messages, left_rooms, set_tags, removed_tags  (as before)
  login_with_token_called, sso_redirect_url_called  (as before)
  subscribe_count, unsubscribe_count              (new)
```

---

## Part 3 — How to use this infrastructure for a new feature

Every feature follows this exact pattern. No shortcuts.

### 3.1 The mental model

Before writing any code for a new feature, answer two questions:

1. **What does `MatrixClient` need to do differently?**
   - Does it call a new HTTP endpoint? → need a new cassette.
   - Does it produce a new `ClientEvent`? → need to add it to the
     `ClientEvent` union and `FakeMatrixClient.emit()`.
   - Does it transform existing data differently? → need a cassette that
     exercises the new data shape.

2. **What does the UI need to do differently?**
   - Does it react to a new event type? → write a TUI test that calls
     `fake.emit(TheNewEvent(...))` and asserts the widget updates.
   - Does it call a new `MatrixClient` method? → add the method to
     `FakeMatrixClient`, write a TUI test that asserts the spy was called.
   - Does it show an error state on failure? → call `fake.fail_next("op")`
     before the action and assert the error widget appears.

### 3.2 Step-by-step TDD workflow for a new feature

**Example: typing indicators**

The feature: "When someone in the active room is typing, show a
`Typing: Alice, Bob…` label below the message view."

---

**Step 1: Define the new `ClientEvent`.**

In `matrix/client.py`:
```python
@dataclass(frozen=True, slots=True)
class TypingChanged:
    room_id: str
    user_ids: list[str]

ClientEvent = RoomsChanged | NewMessage | MembersChanged | TypingChanged
```

---

**Step 2: Write the tier-1 cassette test.**

Add `sync_with_typing.json` (already specced above). Then in
`tests/matrix/test_client.py`:

```python
async def test_typing_event_emits_typing_changed(real_nio_client: Any) -> None:
    """Sync with m.typing ephemeral emits TypingChanged with correct user_ids."""
    received: list[Any] = []
    with aioresponses() as m:
        client = MatrixClient(HOMESERVER, nio_client=real_nio_client)
        await client.restore(make_session())
        client.subscribe(lambda e: received.append(e))
        await start_sync_with_stubs(
            client, m,
            initial_sync=load_fixture("sync_with_typing.json"),
            min_rooms=0,
        )

    typing_events = [e for e in received if isinstance(e, TypingChanged)]
    assert len(typing_events) == 1
    assert typing_events[0].room_id == ROOM_A
    assert "@bob:example.com" in typing_events[0].user_ids
```

This test fails. Now go implement the callback in `MatrixClient`.

---

**Step 3: Implement in `MatrixClient`.**

Register an ephemeral callback in `_register_callbacks()`:

```python
self._client.add_ephemeral_callback(self._on_typing, nio.TypingNoticeEvent)
```

Implement the callback to emit `TypingChanged`. Run the tier-1 test; it
must pass.

---

**Step 4: Extend `FakeMatrixClient` for the UI tier.**

`TypingChanged` is already in the `ClientEvent` union, so `fake.emit()`
already works. No new methods needed unless the UI also needs to call
a new client method.

---

**Step 5: Write the TUI test.**

In `tests/tui/test_message_view.py` (or a new file):

```python
async def test_typing_indicator_shows_while_typing() -> None:
    app, fake = _make_app()
    fake.messages_data["!a:h"] = []
    fake.members_data["!a:h"] = []

    async with app.run_test() as pilot:
        app.push_screen(MainScreen(fake))
        await pilot.pause()
        # ... open room A ...

        await fake.emit(TypingChanged(room_id="!a:h", user_ids=["@bob:example.com"]))
        await pilot.pause()

        label = app.screen.query_one("#typing-indicator", Label)
        assert "Bob" in label.renderable

    async with app.run_test() as pilot:
        # ... open room A ...
        await fake.emit(TypingChanged(room_id="!a:h", user_ids=[]))
        await pilot.pause()

        label = app.screen.query_one("#typing-indicator", Label)
        assert label.display is False
```

These tests fail. Now implement the widget.

---

**Step 6: Implement the widget. Run all tests.**

`uv run ruff check . && uv run ruff format . && uv run mypy && pyright src/ && uv run pytest`

All must pass.

---

### 3.3 Cassette-first vs mock-first decision tree

```
New feature touch point
        │
        ├─ Calls a NEW Matrix HTTP endpoint?
        │   └─ YES → Write a new cassette. Write a tier-1 test using it.
        │              Write a FakeMatrixClient method. Write tier-2 tests.
        │
        ├─ Calls an EXISTING endpoint but expects a NEW response shape?
        │   └─ YES → Add a new cassette variant for the new shape.
        │              Extend the tier-1 test that covers that endpoint.
        │
        ├─ Reacts to a NEW event type in the sync stream?
        │   └─ YES → Write a sync cassette that includes the event.
        │              Tier-1 test: verify MatrixClient emits correct ClientEvent.
        │              FakeMatrixClient: emit() already works (if ClientEvent union updated).
        │              Tier-2 test: verify UI reacts to fake.emit(TheNewEvent).
        │
        ├─ Pure UI change (new widget, new keybinding, new layout)?
        │   └─ YES → No cassette needed. Only tier-2 tests.
        │              FakeMatrixClient may need new scripted state.
        │
        └─ Error path only (UI shows error when operation fails)?
            └─ YES → Tier-2 only: fake.fail_next("op"), assert error widget.
                       No cassette needed unless the error shape is protocol-specific.
```

---

### 3.4 What each tier proves and does not prove

| Tier | Proves | Does NOT prove |
|------|--------|----------------|
| **Cassette / tier-1** | The real nio library parses our fixture correctly. `MatrixClient` methods produce the right `ClientEvent` objects. HTTP error status codes raise the right exceptions. | Anything about the UI. Widget layout. Key bindings. Textual message routing. |
| **FakeMatrixClient / tier-2** | The UI responds correctly to every `ClientEvent`. Write operations call the right `MatrixClient` methods. Error states are shown to the user when operations fail. Loading indicators appear while operations are in progress. | Whether the protocol parser is correct. Whether HTTP stubs match the real server. |
| **Recorded fixtures (local, not in CI)** | The entire stack end-to-end from HTTP → nio → MatrixClient → ClientEvent works against a real Synapse response shape. | UI behaviour. Stable CI. |

The guarantee the two-tier model provides is: if tier-1 passes, `MatrixClient`
produces correct `ClientEvent` objects for any given protocol traffic. If
tier-2 passes, the UI handles every `ClientEvent` correctly. Because tier-2
drives the same `ClientEvent` types that tier-1 verifies `MatrixClient` emits,
the composition is sound: the UI will behave correctly in production.

---

## Done-when

- [ ] All 15 cassette files in `tests/fixtures/nio/synthetic/` are committed.
- [ ] `tests/matrix/helpers.py` has `stub_room_send`, `stub_room_redact`,
      `stub_login_flows`, `stub_room_messages` helpers.
- [ ] Each new cassette has at least one tier-1 test in `tests/matrix/test_client.py`
      that exercises it through real nio.
- [ ] `tests/fakes.py::FakeMatrixClient` has all 9 additions from §2.3.
- [ ] All existing tier-2 tests still pass after `FakeMatrixClient` changes.
- [ ] `uv run ruff check . && uv run mypy && pyright src/ && uv run pytest` all green.

## Dependencies

- Plan 0016 (cassette infrastructure) — **complete**
- Plan 0017 (public-API-only matrix tests) — **complete**
