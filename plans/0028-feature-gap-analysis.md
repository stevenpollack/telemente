# Plan 0028 — Feature gap analysis: telemente vs Element

## Purpose

This document is a read-only reference; it records the state of telemente
relative to Element Web/Desktop as of 2026-06-06, proposes priority tiers, and
identifies the next concrete implementation targets.  No code changes are
included here.

---

## 1. Telemente's current capabilities (as-built)

### Auth / session

- Password login with homeserver discovery (MXID, bare name, `.well-known`)
- SSO login — loopback browser flow plus manual loginToken paste
- Session persistence via OS keyring (fallback: 0600 file)
- Logout with credential clearing and return to login screen
- Multi-homeserver: rebuilds client on homeserver change

### Sync

- Initial full-state sync plus incremental `sync_forever`
- Progressive room list emission during large initial syncs (poll task)
- SQLite message cache (`aiosqlite`, WAL) for cold-room backfill avoidance
- `RoomCache` (JSON) for instant room-list display on restart before sync
- Room-list fingerprint deduplication; surgical unread badge patches

### Rooms

- Three-panel layout: collapsible room list | tabbed message views | collapsible
  member list
- Up to 8 concurrently open room tabs (LRU eviction)
- Room list: sort by recency or A-Z, live substring search with 150 ms debounce
- Room tags: `m.favourite`, `m.lowpriority`, `m.mute` — set/remove via context
  menu and command palette, with optimistic local update
- Leave room (confirmation modal)
- Encryption badge (`🔒`) in room list and notice when all messages are
  undecryptable

### Messages

- Send plain-text messages (multi-line composer; Enter submits, Shift+Enter
  newline)
- Receive and display text messages, date separators, sender colours
- URL linkification (OSC 8 hyperlinks)
- Media messages: image/video/audio/file displayed as labelled link with HTTP URL
- Reactions: send via emoji picker (searchable grid, skin-tone selector) or
  manual emoji input; receive and display reaction chips; optimistic update
- Replies (`m.in_reply_to`): send reply with quoted preview; display reply quote
  on received messages
- Message edit (`m.replace`): edit own messages in place via `E` key; optimistic
  UI update
- Message deletion / redaction: confirm modal, call `room_redact`; server-side
  tombstone rendering (`🗑️ Message deleted`, dimmed-italic CSS)
- Thread panel (MSC3440 `m.thread`): open via context menu or command palette,
  view and send replies, live new-message forwarding
- Typing indicator: display names of users typing in the active room
- Unread badge per room; cleared on room focus
- Toast notification for background-room messages
- In-room message search (`Ctrl+F`): LIKE-based SQLite search with highlight and
  n/N navigation

### E2EE

- Olm/Megolm store (requires system libolm)
- TOFU trust policy: all devices in a room auto-verified before each send
  (explicitly not MITM-safe; documented)
- Key upload and key query on first sync
- Undecryptable event placeholder + `request_room_key`

### UX / navigation

- Command palette (`Ctrl+P`) as canonical feature discovery; all features
  reachable there
- Context menus (right-click or keyboard) on messages and rooms
- Confirmation modal (`ConfirmScreen`) for destructive actions
- Log viewer panel (`Ctrl+\`) tailing the rotating log file
- Collapsible panels (`Ctrl+B` rooms, `Ctrl+R` members)
- Member list with power-level markers (admin `~`, mod `+`), sorted by power
  then name

---

## 2. Feature gap table

Legend — telemente status:
- **Done** — fully implemented
- **Partial** — scaffolding or basic version exists
- **Planned** — a plan document exists but not implemented
- **Missing** — no plan, no code

Priority tiers:
- **P0** — blocks daily use for a power user; should be next on the roadmap
- **P1** — important quality-of-life; medium-term
- **P2** — nice-to-have; long-term
- **P3** — probably out of scope for a TUI client

### Messaging

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Send plain text | Yes | Done | — | Yes | |
| Multi-line composer (Shift+Enter) | Yes | Done | — | Yes | |
| Receive/display text | Yes | Done | — | Yes | |
| Markdown rendering | Yes | Missing | P1 | Partial | Textual markup is not CommonMark; would need a parser that emits Textual markup or Rich markup |
| Replies (`m.in_reply_to`) — send & display | Yes | Done | — | Yes | |
| Threads (MSC3440) — view & reply | Yes | Done | — | Yes | |
| Thread root detection (show badge on messages that have replies) | Yes | Missing | P1 | Yes | Requires tracking `m.thread` relations on root events |
| Message edits — send & receive | Yes | Partial | P0 | Yes | Send done; incoming edits from other users not applied to existing rows (no `m.replace` live-update handler) |
| Message deletion / redaction | Yes | Done | — | Yes | |
| Emoji reactions — send | Yes | Done | — | Yes | |
| Emoji reactions — receive live update | Yes | Partial | P0 | Yes | Backfill reactions attached on load; but live incoming `m.reaction` events are not handled (no `ReactionEvent` callback in `_register_callbacks`) |
| Message formatting (bold, italic, code) | Yes | Missing | P1 | Partial | Plain text only; no `formatted_body` parsing or sending |
| Code block rendering | Yes | Missing | P1 | Yes | Terminal-friendly via Rich syntax highlighting |
| Message permalinks / copy event ID | Yes | Missing | P2 | Yes | |
| Pagination / load-older-messages | Yes | Missing | P0 | Yes | Fixed 50-message backfill only; no scroll-up-to-load-more |
| Jump-to-unread | Yes | Missing | P1 | Yes | |
| Search across all rooms (global) | Yes | Missing | P2 | Yes | Current search is per-room, SQLite-local only |
| Pinned messages | Yes | Missing | P2 | Yes | |
| Polls (MSC3381) | Yes | Missing | P2 | Yes | Renderable as text with vote counter |
| Voice messages (playback label) | Yes | Partial | P2 | Partial | Media type shown as labelled link; no inline playback |
| File upload (send) | Yes | Missing | P1 | Yes | Requires file picker path input and `m.file` upload |
| Image upload (send) | Yes | Missing | P1 | Yes | Same as file upload |
| Link previews (OpenGraph) | Yes | Missing | P2 | Partial | Text-only preview feasible; images out of scope |
| Message timestamps (hover/detail) | Yes | Partial | P2 | Yes | Time shown in `HH:MM`; full date/seconds not surfaced |

### Rooms

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Join room by ID / alias | Yes | Missing | P0 | Yes | Requires `client.join_room()` and a join dialog |
| Create room | Yes | Missing | P1 | Yes | Requires a creation wizard screen |
| Room directory (public rooms) | Yes | Missing | P2 | Yes | `publicRooms` API; table widget |
| Invite user to room | Yes | Missing | P1 | Yes | `client.room_invite()` |
| Room settings (name, topic, avatar) | Yes | Missing | P2 | Partial | Read-only display of room name done |
| Room topic display | Yes | Missing | P1 | Yes | `MatrixRoom.topic` accessible from nio |
| Knock / request to join | Yes | Missing | P3 | Yes | MSC2403; rare |
| Leave room | Yes | Done | — | Yes | |
| Room aliases | Yes | Missing | P2 | Yes | |
| DM indicator | Yes | Missing | P1 | Yes | `m.direct` account data event |
| Spaces support | Yes | Missing | P3 | Partial | Space hierarchy is complex; basic grouping may be feasible |
| Room notifications: mention highlights | Yes | Missing | P1 | Yes | `m.push_rules` — highlight mentions in message body |
| Knock on room | Yes | Missing | P3 | Yes | MSC2403 |

### Members / user profiles

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Member list with power levels | Yes | Done | — | Yes | |
| Click member — view profile | Yes | Missing | P1 | Yes | Popup or panel with `@user:server`, display name, power level |
| Set display name | Yes | Missing | P1 | Yes | `PUT /profile/{userId}/displayname` |
| Set avatar (description) | Yes | Missing | P3 | No | Cannot display images; could show mxc:// URL |
| Kick member | Yes | Missing | P1 | Yes | Requires power level ≥ 50; `room_kick` in nio |
| Ban / unban member | Yes | Missing | P2 | Yes | |
| Ignore user (block) | Yes | Missing | P2 | Yes | `m.ignored_user_list` account data |
| Presence / status indicator | Yes | Missing | P2 | Partial | Online/offline could be rendered as ASCII marker |
| User search (global) | Yes | Missing | P2 | Yes | `/_matrix/client/v3/user_directory/search` |

### Read receipts & notifications

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Send read receipts | Yes | Missing | P0 | Yes | `POST /rooms/{roomId}/receipt/m.read/{eventId}` — should fire on room open |
| Display read receipts (who read) | Yes | Missing | P2 | Yes | Show user IDs or initials below message |
| Fully-read marker | Yes | Missing | P2 | Yes | `POST /rooms/{roomId}/read_markers` |
| Unread count accurate | Yes | Partial | P0 | Yes | Counts from nio but not decremented by sending a read receipt |
| Desktop / system notifications | Yes | Missing | P2 | Partial | TUI has no D-Bus/notify-send integration; `notify-send` shim feasible |
| Notification rules / push rules | Yes | Missing | P2 | Yes | Mention highlight rendering is the highest-value subset |
| Mention highlights | Yes | Missing | P1 | Yes | Bold/colour own MXID occurrences in message body |

### E2EE

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Olm setup + TOFU trust | Yes | Done | — | Yes | |
| Interactive SAS verification | Yes | Missing | P1 | Yes | Emoji comparison feasible in TUI; requires `VerificationRequest` handling in nio |
| Cross-signing (import/bootstrap) | Yes | Missing | P2 | Yes | Complex but TUI-feasible |
| Key backup (SSSS) | Yes | Missing | P2 | Yes | Critical for key recovery; requires passphrase/recovery-key UI |
| Secure backup recovery on login | Yes | Missing | P2 | Yes | Dependent on key backup |
| Verified session badge | Yes | Missing | P2 | Yes | ASCII indicator next to room name |
| Device management (list/sign-out) | Yes | Missing | P2 | Yes | `GET /devices`; list + revoke |
| Warn on unverified devices | Yes | Missing | P2 | Yes | Currently silently TOFU |

### Settings / account

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Logout | Yes | Done | — | Yes | |
| Change homeserver in-app | Yes | Partial | P1 | Yes | Must re-login; could detect and redirect |
| Appearance (theme selection) | Yes | Missing | P2 | Yes | Textual supports multiple CSS themes |
| Notification settings | Yes | Missing | P2 | Yes | Push rules API |
| Account deactivation | Yes | Missing | P3 | Yes | Destructive; low priority |
| Settings screen (centralised) | Yes | Missing | P1 | Yes | Currently settings.toml edited manually |
| Default homeserver in settings.toml | Yes | Partial | — | Yes | Exists but no in-app editor |

### VoIP / media

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| 1:1 voice/video calls | Yes | Missing | P3 | No | WebRTC in a terminal is not feasible |
| Group calls (Jitsi/MSC3401) | Yes | Missing | P3 | No | Same constraint |
| Audio playback | Yes | Missing | P3 | Partial | Could open an external player via `xdg-open` |

### Moderation

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Redact any message (with power level) | Yes | Done | — | Yes | `can_redact` checks power level |
| Report message | Yes | Missing | P2 | Yes | `POST /rooms/{roomId}/report/{eventId}` |
| Room moderation (power levels UI) | Yes | Missing | P2 | Yes | Read-only display done; set power level is missing |
| Server admin tools | Yes | Missing | P3 | Yes | Out of scope for a regular client |

### Integrations / bots

| Feature | Element | Telemente | Priority | TUI feasible? | Notes |
|---------|---------|-----------|----------|---------------|-------|
| Bot/bridge messages displayed | Yes | Done | — | Yes | They are ordinary messages |
| Widgets (iframes) | Yes | Missing | P3 | No | No browser in terminal |
| Integration manager | Yes | Missing | P3 | No | Web UI; out of scope |

---

## 3. Priority ordering and rationale

### P0 — Blocks productive daily use

These gaps mean telemente cannot function as a primary Matrix client even for
power users comfortable with a TUI.

1. **Incoming `m.replace` (edit) live update** — Other users' edits silently
   disappear. Requires an `add_event_callback` for a synthetic edit-event type
   (nio parses edits as ordinary `RoomMessageText` with `rel_type: m.replace`
   in `event.source`; they are already filtered out of display but there is no
   handler that updates the original row).

2. **Incoming `m.reaction` live update** — Reactions from other users never
   appear during a session. Needs `add_event_callback(self._on_reaction, nio.ReactionEvent)`
   and a `ReactionsChanged` client event (or extending `NewMessage`) to route
   the update to the right `MessageRow`.

3. **Read receipt sending** — Rooms never get marked read on the server, so
   unread counts accumulate forever across sessions.

4. **Pagination / load older messages** — The 50-message window is a hard
   constraint; any conversation over 50 messages old is inaccessible.

5. **Join room by ID or alias** — There is no way to enter a new room without
   being invited first. This is the single most blocking discovery gap.

### P1 — Important, should follow P0

6. **Mention highlights** — Rendering own MXID in bold/colour is a basic
   usability expectation in any chat client.

7. **File/image upload (send)** — Sending non-text content is expected daily.

8. **Incoming markdown / `formatted_body`** — Many bridges and bots send
   `formatted_body`; without parsing, output is unformatted HTML.

9. **Thread root detection** — Messages that have thread replies should show a
   visual indicator so users know a thread exists before right-clicking.

10. **Invite user to room** — Needed for private rooms to be useful.

11. **Room topic display** — The topic is a primary context signal in many rooms.

12. **DM indicator** — Without distinguishing DMs from rooms, the room list is
    harder to navigate.

13. **Interactive SAS device verification** — TOFU is documented as unsafe;
    verification is needed for security-conscious users.

14. **Settings screen** — Currently settings.toml is edited manually; a basic
    in-app screen for homeserver and device name would help new users.

### P2 — Nice-to-have, medium/long-term

- Global message search
- Key backup and cross-signing
- Read receipt display
- Presence indicators
- Kick/ban member
- Ignore user
- Room directory
- Pinned messages
- Polls rendering
- Link preview (text)
- Device management
- System notifications via `notify-send`
- Verified session badge
- Power-level setting UI
- Report message

### P3 — Probably out of scope for a TUI

- VoIP / video calls (no WebRTC in terminal)
- Widgets / iframe embeds
- Integration manager
- Audio playback inline (can delegate to `xdg-open`)
- Spaces hierarchy (complex; basic grouping may be P2)
- Account deactivation

---

## 4. Recommended next plans

Based on the P0 gap list, the recommended next implementation plans (in order)
are:

| Suggested plan # | Feature | Dependency |
|-----------------|---------|------------|
| 0029 | Incoming edit live-update (`m.replace` callback) | 0028 |
| 0030 | Incoming reaction live-update (`m.reaction` callback) | 0028 |
| 0031 | Read receipt sending on room open | 0028 |
| 0032 | Load older messages (scroll-up pagination) | 0013, 0028 |
| 0033 | Join room by ID / alias | 0028 |
| 0034 | Mention highlights | 0033 |
| 0035 | File / image upload | 0033 |

Each plan should follow the standard format: Goal, Files, Public interface,
Behavior, Test cases, Mocking strategy, Done-when, Dependencies.

---

## 5. TUI feasibility notes

Several features deserve explicit notes on what is and is not achievable in a
terminal environment:

**Markdown / formatted_body**: Rich (the Textual rendering backend) supports
bold, italic, code spans, and code blocks natively. A CommonMark parser
(e.g., `mistletoe`) could emit Rich markup. The main limitation is that HTML
`<a href>` links need mapping to Textual's `[link=...]` syntax. This is P1
because it affects readability of bot messages and bridges.

**Images and video**: Cannot be displayed inline. The current approach (labelled
OSC-8 link) is the correct terminal answer. Audio can be delegated to an
external player.

**VoIP**: Ruled out. WebRTC requires a browser engine; there is no path to in-
terminal audio/video. Element's Jitsi integration is similarly browser-based.

**Presence**: Can be rendered as an ASCII marker (e.g., `● ` for online,
`○ ` for offline) in the member list. The presence API is simple.

**Device verification (SAS)**: The emoji comparison step maps naturally to a
TUI modal. The QR-code step does not, but SAS (text/emoji) is sufficient for
the security goal.

**Key backup**: A passphrase or recovery-key input dialog is feasible. The
interaction is a one-time flow on login and can be a dedicated screen.

**Notifications**: Textual's built-in `notify()` toasts are already in use.
System-level notifications via `subprocess` + `notify-send` (Linux) or
`osascript` (macOS) are achievable as an optional enhancement.

**Spaces**: The space hierarchy (rooms nested inside spaces) could be rendered
as collapsible sections in the room list. This is architecturally non-trivial
because it requires tracking `m.space.child` state events and grouping the
`RoomList` by space. Deferred to P3 for now but P2 if the room list becomes
unwieldy at scale.
