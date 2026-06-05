# Plan 0024 — In-room message search

## Goal

Let the user search the message history of the currently active room for a
keyword or phrase, navigate between matches with `n`/`N`, and jump the
timeline to each match.  Triggered by `Ctrl+F` or the command palette.

---

## Dependencies

- Plans 0001–0022 complete (assumes codebase state after plan 0022).
- Plan 0013 (`MessageCache` / SQLite) — the primary data source for search.
- No hard dependency on 0023 (if it exists) but both touch `MessageView`.

---

## Background and motivation

Matrix is a distributed, end-to-end-encrypted protocol.  "Search" means very
different things depending on whether a room is encrypted, how many messages
are locally cached, and what the homeserver supports.  This plan resolves those
tensions before specifying an implementation.

---

## Research summary

### 1. Matrix Full-Text Search API — `POST /_matrix/client/v3/search`

The Matrix spec defines a general search endpoint at
`POST /_matrix/client/v3/search`.  The request body looks like:

```json
{
  "search_categories": {
    "room_events": {
      "search_term": "keyword",
      "filter": {
        "rooms": ["!roomid:server"]
      },
      "order_by": "rank",
      "include_state": false,
      "event_context": {
        "before_limit": 2,
        "after_limit": 2,
        "include_profile": false
      }
    }
  }
}
```

The response contains `results` (list of ranked `Result` objects, each with
the matching event and optional `context.events_before/after`) and a `next_batch`
token for pagination.  The spec also returns a `count` of total matches and a
`groups` map for future aggregation.

**Server-side limitations:**

- **Synapse** (reference implementation): full-text search is built-in via
  PostgreSQL `tsvector`/`tsquery` using the `GIN` index over the `events` table.
  It works out-of-the-box on Synapse + Postgres but is disabled on SQLite-backed
  Synapse deployments.  Synapse also supports external Elasticsearch/Opensearch
  via the `synapse-s3-storage-provider` plugin or a separately running Dendrite
  search worker.
- **Conduit** (Rust implementation): does not implement `/search` as of 2025.
  It returns 404 or an empty result set.
- **Dendrite** (Go implementation): partial `/search` support was merged in
  2023; it uses a built-in bleve-based index.  Coverage is incomplete and
  encrypted rooms are not indexed.
- **E2EE rooms**: the server never sees plaintext.  No server-side indexer can
  search encrypted content.  The spec explicitly acknowledges this gap; there
  is no standard mechanism for client-side key sharing that would let a server
  search on behalf of a user.

**Practical conclusion**: server-side search is available on Synapse+Postgres
deployments for unencrypted rooms, but is unreliable across the ecosystem.
Element Web falls back to local search for encrypted rooms.  Cinny, FluffyChat,
and nheko all implement local search for the same reason.

**nio support**: matrix-nio does **not** implement `POST /search`.  The
`AsyncClient` API has no `search()` or `room_search()` method.  Using the
server-side API would require a raw `aiohttp` call (as `set_room_tag` already
does) and a full response-parsing layer.  This is significant additional
complexity for an API that only works on a fraction of deployments and is
useless for encrypted rooms.

### 2. Client-side search in other clients

**Element Web**: maintains an in-memory index of the currently-loaded timeline.
For encrypted rooms it scans the decrypted event cache.  For unencrypted rooms
it calls `POST /search` first (server-side), then falls back to local for
encrypted rooms.  The result panel appears as a right-hand sidebar listing
matches with snippets; clicking a match scrolls the timeline to that event.

**Cinny**: local-only search.  Scans the messages loaded in the current room
panel using a simple `String.includes()` / `toLowerCase` comparison.  No
indexing.  Shows a filtered list of matching messages in the main panel.
Does not trigger additional pagination; only searches already-loaded history.

**FluffyChat** (Flutter): scans its local SQLite database (it uses a Drift/Moor
schema) with `LIKE '%query%'` on the `body` column.  Encrypted messages are
stored decrypted in the local store so this also covers E2EE.  Shows results
in a separate route (page).

**nheko** (Qt/C++): maintains a local SQLite FTS5 virtual table (`body_fts`)
populated from the same SQLite store that caches timeline events.  A
`MATCH 'keyword'` query over the FTS5 table returns ranked results instantly.
Works for both plain and E2EE rooms (nheko stores decrypted bodies locally).
Results appear in a search panel showing sender + snippet + timestamp; clicking
jumps the main timeline.

### 3. Our `MessageCache` and SQLite FTS5

Our `MessageCache` (`matrix/cache.py`) uses `aiosqlite`.  The schema is a
single `messages` table with a `body TEXT NOT NULL` column.  There is
currently no FTS5 virtual table.

SQLite FTS5 is the standard approach for full-text search in SQLite.  A
content-less or content (external-content) FTS5 virtual table pointing at the
`messages.body` column would support efficient ranked queries via:

```sql
SELECT m.room_id, m.event_id, m.sender, m.sender_display_name, m.body, m.timestamp_ms
FROM messages_fts
JOIN messages m USING (rowid)
WHERE messages_fts MATCH ?
  AND m.room_id = ?
ORDER BY rank
```

**FTS5 tradeoffs:**

- Pros: instant ranked results; handles multi-word queries; stemming optional;
  `HIGHLIGHT` function for snippet generation.
- Cons: the virtual table must be kept in sync with the `messages` table
  (triggers or manual `INSERT INTO messages_fts(...) VALUES (...)` on each
  `put()`); adds schema complexity; FTS5 is not available in all SQLite builds
  (though it is present in CPython's bundled sqlite3 for all major platforms).
- A simpler `WHERE body LIKE '%?%'` query scans every row in the room (at most
  ~500 rows per room, per `evict_old`) and is fast enough at this scale.
  Benchmark: 500 rows × avg 100 bytes body ≈ 50 KB — sub-millisecond on any
  modern CPU.  No index needed.

**Recommendation**: for the first iteration, use a simple `LIKE` query on the
existing `body` column scoped to `room_id`.  This avoids FTS5 schema migration
complexity while being fast enough for our cache size.  An FTS5 upgrade path
is noted as a future optimisation (see Open Questions).

### 4. Incremental / paginated results

The cache holds at most `evict_old` rows per room (default 500).  A user may
have searched for a term that appears in messages older than those 500.

**Options:**

A. **Search only the cache (MVP)**: show results only for locally-cached
   messages.  Display a notice: "Showing N matches in loaded history. Use
   `Ctrl+U` to load more history and search again."  This is what Cinny does.

B. **Trigger background pagination**: when the search query is submitted, run
   `client.messages()` / `room_messages` in a background worker to fetch
   additional history, cache it, then re-run the search.  The UI shows interim
   results and refreshes.  This is closer to FluffyChat's behaviour.

C. **Server-side search for unencrypted rooms**: use `POST /search` first, then
   for results not in cache, backfill those events by fetching context around
   each result event_id.

Options B and C add significant complexity.  Option A is the correct MVP.

**OPEN QUESTION 1**: Should the first implementation search only cached history
(option A) or trigger automatic pagination (option B)?  Option A is recommended.

### 5. UX in a TUI

Studied `vim` `/pattern`, `less`'s `/pattern`, `ranger`'s `:search`, and
`lazygit`'s filter mode:

- **vim**: inline search bar at the bottom; matches highlighted in buffer;
  `n`/`N` to navigate.  This is the closest model to what the user asked for
  ("Ctrl+F in a browser").
- **less**: similar to vim; the original document stays visible; match is
  highlighted and scrolled into view.
- **ranger**: `/` opens a filter input that hides non-matching rows.
- **lazygit**: `/` opens a filter that narrows the list.

Two viable TUI options for our `MessageView`:

**Option 1 — Filter mode**: hide all `MessageRow` widgets whose body doesn't
match; show only matching rows.  This is conceptually simple and requires no
new widget.  Disadvantage: the timeline loses context (the sequence of messages
becomes confusing when non-matching rows disappear).

**Option 2 — Highlight-and-navigate mode**: all `MessageRow` widgets remain;
matching rows receive a highlight CSS class; a search bar at the top of
`MessageView` shows "N/M matches" and `n`/`N` navigate between highlighted
rows (scrolling them into view).  This preserves temporal context and matches
the vim/browser model.

**Option 3 — Separate search results panel**: a `ModalScreen` or sidebar lists
matching messages (sender + date + snippet); clicking a result scrolls the
timeline.  This is how Element Web works.

Option 2 (highlight-and-navigate) is recommended: it is the most familiar UX,
preserves context, and is implementable with a narrow widget change to
`MessageView`.

**OPEN QUESTION 2**: Confirm Option 2 (highlight-and-navigate in-view mode) vs.
Option 3 (separate results panel).  Option 2 is the recommendation.

### 6. Keyboard UX

- `Ctrl+F` opens search (browser convention; familiar to most users).
- Alternatively `/` (vim convention) — but `/` may conflict with future
  command-mode bindings, and `Ctrl+F` is a well-established standard in chat
  applications (Slack, Discord, Telegram all use it).
- `Escape` closes search bar, removes highlights, restores normal mode.
- `Enter` (in the search bar) confirms the query and jumps to the first match.
- `n` / `N` navigate forward / backward between matches while the search bar
  is visible.  These should only fire when focus is not in the composer.
- `Ctrl+F` again (or `Enter` from the search bar) cycles forward (same as `n`).

**OPEN QUESTION 3**: `Ctrl+F` vs `/` as the primary trigger keybinding.
`Ctrl+F` is recommended.

---

## Recommended approach

**Client-side `LIKE` search over the `MessageCache` SQLite database, scoped to
the active room, with highlight-and-navigate UX in `MessageView`.**

Rationale:
- Works for all rooms including E2EE (we store decrypted bodies).
- No dependency on server capabilities or server-side indexing.
- Our cache size (≤500 rows/room) makes `LIKE` fast enough; no FTS5 needed yet.
- The highlight-and-navigate model is familiar and preserves message context.
- Keeps all complexity inside the client layer (cache + `MessageView`).

---

## Architecture

### New method: `MessageCache.search_room(room_id, query) -> list[str]`

Returns a list of `event_id` strings (matching messages in `room_id`, sorted by
`timestamp_ms ASC`) — not full `Message` objects.  The UI already has the
messages rendered; it only needs event IDs to know which rows to highlight.

```python
async def search_room(self, room_id: str, query: str) -> list[str]:
    """Return event_ids of messages whose body matches query (case-insensitive).

    Uses LIKE for simplicity; suitable for our ≤500-row-per-room cache size.
    Returns event_ids in chronological order.
    """
```

SQL: `SELECT event_id FROM messages WHERE room_id = ? AND body LIKE ? ORDER BY timestamp_ms ASC`
with `%{query}%` as the pattern (lowercased on both sides via `LOWER()`).

### New method: `MatrixClient.search_messages(room_id, query) -> list[str]`

Wraps `MessageCache.search_room`.  If the cache is not open (no `cache_path`),
returns an empty list and logs a warning.

```python
async def search_messages(self, room_id: str, query: str) -> list[str]:
    """Search cached message bodies for query in room_id.

    Returns event_ids of matching messages, chronological order.
    Returns [] if cache is unavailable or query is empty.
    """
```

This is a pure query method — no `ClientEvent` emitted.

### New `SearchResult` in `models.py` — NOT needed

The return type is `list[str]` (event IDs).  No new model type is needed; this
avoids inflating `models.py` with a type only used by one caller.

### Protocol extension: `_MessageViewClient`

Add `search_messages` to the protocol:

```python
async def search_messages(self, room_id: str, query: str) -> list[str]: ...
```

`FakeMatrixClient.search_messages` will accept a scripted
`search_results: dict[str, list[str]]` mapping `room_id -> [event_id, ...]`.

### `MessageView` search mode

Add to `MessageView`:

- `_search_active: bool = False`
- `_search_query: str = ""`
- `_search_match_ids: list[str] = []`  — ordered list of matching event IDs
- `_search_cursor: int = -1`           — index into `_search_match_ids`

New widget in the `compose()` layout (hidden by default):

```python
yield Horizontal(
    Input(id="search-input", placeholder="Search messages…"),
    Static("", id="search-count"),
    id="search-bar",
)
```

CSS: `#search-bar` is hidden initially (`display: none`).

New CSS classes on `MessageRow`:
- `-search-match`: row matches query (highlighted border/background)
- `-search-current`: the currently-focused match (stronger highlight)

New action `action_open_search()` triggered by `Ctrl+F`:
- Sets `_search_active = True`
- Shows `#search-bar`
- Focuses `#search-input`

On `Input.Changed` in `#search-input`:
- Debounced 150 ms (same pattern as room list search)
- Calls `_run_search(query)`

`_run_search(query: str)`:
- Clears all `-search-match` / `-search-current` classes from rendered rows
- If `query` is empty: hides search bar, clears state, returns
- Calls `self.run_worker(self._do_search(room_id, query), exclusive=True)`

`_do_search(room_id: str, query: str)` (async worker):
- Calls `await self._client.search_messages(room_id, query)`
- Saves result to `_search_match_ids`, resets `_search_cursor = 0`
- Calls `_apply_search_highlights()` on the Textual thread via
  `self.call_after_refresh(_apply_search_highlights)` or by posting a
  `_SearchResultsReady` Textual message to `self`

`_apply_search_highlights()`:
- Iterates `self.query(MessageRow)`
- For each row: adds `-search-match` if `row.message.event_id in set(match_ids)`, else removes it
- Updates `#search-count` Static: `"N / M"` where M = total matches, N = cursor+1
- Calls `_jump_to_cursor()` to scroll the current match into view

`_jump_to_cursor()`:
- If `_search_match_ids` is empty: noop
- Removes `-search-current` from all rows
- Gets `current_id = _search_match_ids[_search_cursor]`
- Finds the `MessageRow` with that event_id
- Adds `-search-current` to it
- Calls `row.scroll_visible()` to bring it into view

`action_search_next()` / `action_search_prev()` (keybindings `n` / `N` when
search is active):
- Advance/decrement `_search_cursor` (wrapping)
- Call `_jump_to_cursor()`

Escape handler (extend existing `on_key`):
- If `_search_active`: clear search state, hide `#search-bar`, remove all
  `-search-match` / `-search-current` classes, refocus composer

### Command palette entry

New entry in `TelementeCommands._commands()`:

```python
("Search in room", self.cmd_search_in_room, "Search message history of the active room (Ctrl+F)"),
```

`cmd_search_in_room()`:
- Gets the active room's `MessageView`
- Calls `view.action_open_search()`

### `FakeMatrixClient` additions

```python
# Scripted search results: room_id -> list[event_id]
search_results: dict[str, list[str]] = {}

async def search_messages(self, room_id: str, query: str) -> list[str]:
    self._check_fail("search_messages")
    await self._maybe_block("search_messages")
    return list(self.search_results.get(room_id, []))
```

`reset_spies()` does NOT clear `search_results` (scripted state, not a spy).

---

## Tier-1 tests — `tests/matrix/test_search.py` (new file)

All use the real `MessageCache` opened against a tmp-path SQLite database.
No aioresponses needed — these are pure cache tests.

**`test_search_room_empty_cache_returns_empty`**
- Open a fresh cache; call `search_room("!r:s", "foo")`
- Assert returns `[]`

**`test_search_room_single_match`**
- `await cache.put(msg)` with `body="hello world"`
- `result = await cache.search_room(msg.room_id, "world")`
- Assert `result == [msg.event_id]`

**`test_search_room_case_insensitive`**
- `body="Hello World"`, query `"hello"`
- Assert match returned

**`test_search_room_no_match`**
- `body="hello"`, query `"goodbye"`
- Assert `result == []`

**`test_search_room_multiple_matches_in_order`**
- Put 3 messages with timestamps t1 < t2 < t3, bodies `"foo"`, `"bar"`, `"foo bar"`
- Query `"foo"`
- Assert returns event_ids for messages 1 and 3 in that order

**`test_search_room_scoped_to_room`**
- Two rooms with overlapping bodies
- Query in room A returns only room A's event_ids

**`test_search_room_empty_query_returns_empty`**
- Non-empty cache; query `""`
- Assert returns `[]`

**`test_matrix_client_search_messages_delegates_to_cache`**
- Use `restore_client()` with an in-memory `MessageCache`; pre-populate it
- Call `await client.search_messages(room_id, "foo")`
- Assert returns the expected event IDs

**`test_matrix_client_search_messages_no_cache_returns_empty`**
- Build `MatrixClient` without `cache_path`
- Assert `await client.search_messages("!r:s", "foo")` returns `[]`

---

## Tier-2 tests — `tests/tui/test_message_search.py` (new file)

Use `FakeMatrixClient` injected via a `SearchHostApp`.  Follow the
`RedactionHostApp` / `TypingHostApp` pattern from plans 0021/0022.

```python
class SearchHostApp(App[None]):
    def __init__(self, client: FakeMatrixClient, room_id: str) -> None:
        super().__init__()
        self._client = client
        self._room_id = room_id

    def compose(self) -> ComposeResult:
        yield MessageView(self._client, id="message-panel")

    def on_mount(self) -> None:
        view = self.query_one(MessageView)
        view._current_room_id = self._room_id
```

**`test_ctrl_f_opens_search_bar`**
- Mount `SearchHostApp` with one message; `pilot.press("ctrl+f")`
- `await pilot.pause()`
- Assert `view.query_one("#search-bar").display is True`
- Assert `#search-input` has focus

**`test_search_highlights_matching_row`**
- `fake.search_results = {room_id: ["$e1"]}`; one message with `event_id="$e1"`
- Open search, type `"hello"`, `await pilot.pause()`
- Assert the `MessageRow` with `event_id="$e1"` has class `-search-match`

**`test_search_count_label_updated`**
- `fake.search_results = {room_id: ["$e1", "$e2"]}`; two matching messages
- After search: assert `#search-count` Static text is `"1 / 2"`

**`test_n_advances_to_next_match`**
- Two matches; after search, `pilot.press("n")`
- Assert `#search-count` shows `"2 / 2"` and `-search-current` is on `$e2`

**`test_N_goes_to_prev_match`**
- Two matches; after search (cursor at 0), `pilot.press("N")`
- Assert cursor wraps to last match

**`test_search_wraps_forward`**
- Two matches; cursor at last; `pilot.press("n")`
- Assert cursor wraps to first match

**`test_escape_closes_search_bar`**
- Open search; `pilot.press("escape")`
- Assert `#search-bar` is hidden and no `-search-match` classes remain

**`test_empty_query_clears_highlights`**
- Set matches; open search; type `"foo"`; clear input; `await pilot.pause()`
- Assert no `-search-match` classes on any row

**`test_search_in_wrong_room_matches_ignored`**
- `fake.search_results = {"!other:s": ["$e1"]}`; active room is `"!this:s"`
- After search: assert no `-search-match` classes (results scoped to active room)

**`test_command_palette_search_in_room`**
- Open command palette (`ctrl+p`), type `"Search in room"`, press `Enter`
- Assert `#search-bar` becomes visible

**`test_search_non_matching_rows_not_highlighted`**
- Three messages; only one matches; assert the other two lack `-search-match`

---

## Implementation steps

1. **`MessageCache.search_room`** — add the `LIKE` query method to `cache.py`.
   No schema change needed.

2. **`MatrixClient.search_messages`** — add the thin wrapper to `client.py`.
   Extend `_MessageViewClient` protocol.

3. **Stub extension** — no nio stub changes needed (search is a pure cache call,
   no nio involvement).

4. **`FakeMatrixClient`** — add `search_results` dict and `search_messages`
   method to `tests/fakes.py`.

5. **Tier-1 tests** — write `tests/matrix/test_search.py` (all failing).

6. **Implement steps 1–2** until Tier-1 tests pass.

7. **`MessageView` search mode** — add `#search-bar` to compose, add
   `_search_active` state, add `action_open_search`, `_run_search`,
   `_do_search`, `_apply_search_highlights`, `_jump_to_cursor`,
   `action_search_next`, `action_search_prev`.  Extend `on_key` for Escape.
   Add `BINDINGS` entry for `ctrl+f`.

8. **CSS** — add `MessageRow.-search-match` and `MessageRow.-search-current`
   rules to `app.tcss`.  Add `#search-bar` layout rules.

9. **Command palette** — add `"Search in room"` entry to
   `TelementeCommands._commands()` and implement `cmd_search_in_room`.

10. **Tier-2 tests** — write `tests/tui/test_message_search.py` (all failing).
    Run full feedback loop until green.

---

## Decisions

1. **Scope**: Search only the cached history (≤500 rows/room). Display a notice
   `"Showing matches in loaded history"` in `#search-count` when results are
   found (or when query is non-empty). Pagination-triggered deep search is a
   future follow-up plan.

2. **UX model**: Highlight-and-navigate within the existing `MessageView`
   (Option 2). All rows remain visible; matching rows get `-search-match` CSS;
   `n`/`N` scroll between them. No separate panel or modal.

3. **Keybinding**: `Ctrl+F` opens search (browser/chat convention).

4. **FTS5**: Deferred. Use `LIKE '%query%'` on the existing `body` column for
   now. Note FTS5 as a known upgrade path in a comment at the `search_room`
   method.

5. **Highlight style**: `-search-match` uses a bold border (avoids readability
   issues in light themes); `-search-current` uses a filled `$accent` background.

---

## Files to create / modify

| File | Action | Changes |
|---|---|---|
| `src/telemente/matrix/cache.py` | Modify | Add `search_room(room_id, query) -> list[str]` method |
| `src/telemente/matrix/client.py` | Modify | Add `search_messages(room_id, query) -> list[str]` method |
| `src/telemente/tui/widgets/message_view.py` | Modify | Add `#search-bar`, search state, `action_open_search`, `action_search_next`, `action_search_prev`, `_run_search`, `_do_search`, `_apply_search_highlights`, `_jump_to_cursor`; extend `on_key`; extend `_MessageViewClient` protocol |
| `src/telemente/tui/commands.py` | Modify | Add `"Search in room"` entry; add `cmd_search_in_room` method |
| `src/telemente/tui/styles/app.tcss` | Modify | Add `MessageRow.-search-match`, `MessageRow.-search-current`, `#search-bar` CSS rules |
| `tests/fakes.py` | Modify | Add `search_results` dict; add `search_messages` method |
| `tests/matrix/test_search.py` | Create | Nine Tier-1 tests for `MessageCache.search_room` and `MatrixClient.search_messages` |
| `tests/tui/test_message_search.py` | Create | Eleven Tier-2 tests for `MessageView` search UX |

---

## Done-when checklist

- [ ] `MessageCache.search_room` exists; returns `list[str]` of event IDs sorted
  by `timestamp_ms ASC`; `LIKE` is case-insensitive.
- [ ] `MessageCache.search_room("!r:s", "")` returns `[]` without querying.
- [ ] `MatrixClient.search_messages` delegates to `_cache.search_room`; returns
  `[]` when cache is `None`.
- [ ] `_MessageViewClient` protocol has `search_messages(room_id, query) -> list[str]`.
- [ ] `FakeMatrixClient.search_messages` reads from `search_results` dict.
- [ ] All nine Tier-1 tests in `tests/matrix/test_search.py` are green.
- [ ] `MessageView` has `action_open_search`; `Ctrl+F` triggers it.
- [ ] `#search-bar` is hidden by default; shown when search is active.
- [ ] `#search-input` takes focus when search bar opens.
- [ ] `MessageRow.-search-match` CSS class is applied to matching rows.
- [ ] `MessageRow.-search-current` CSS class is applied to the active match.
- [ ] `#search-count` Static shows `"N / M"` format.
- [ ] `n` advances cursor (wrapping); `N` reverses cursor (wrapping).
- [ ] Escape closes search bar, clears all match classes, returns focus to composer.
- [ ] Empty query (clear the input) clears all highlights.
- [ ] `"Search in room"` appears in the command palette.
- [ ] `cmd_search_in_room` correctly finds the active room's `MessageView` and
  calls `action_open_search()`.
- [ ] All eleven Tier-2 tests in `tests/tui/test_message_search.py` are green.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run ruff format .` clean.
- [ ] `uv run mypy` clean.
- [ ] `pyright src/` clean.
- [ ] `uv run pytest` all green.
