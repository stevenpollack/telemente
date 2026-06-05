# Plan 0020 — Context menus and emoji picker

## Goal

Add right-click context menus to `MessageRow`, `RoomItem`, and the
`TabbedContent` tab bar, plus an emoji picker that replaces the bare
`#emoji-input` text field currently used for reactions.

---

## Dependencies

- Plans 0001–0009 (core UI) must be complete.
- Plan 0012 (OptionList migration) is a sibling; this plan is independent
  of it but both touch `RoomList`. If 0012 ships first, the context menu
  attaches to the new `OptionList` items instead of `RoomItem`/`ListView`.
  The interface contract (Part 4) is written to handle both cases.

---

## Architecture decisions

### Textual 8.2.7 — right-click support

Textual 8.2.7 has **no built-in `ContextMenu` widget** and **no dedicated
right-click event**. The raw mouse pipeline is:

1. `MouseDown` (`button=3`) → `MouseUp` (`button=3`) → `Click` (`button=3`).
2. All three events bubble up the widget tree; `button` is always the integer
   `3` for the right mouse button.
3. `widget.absolute_offset` exists on all `Widget` instances (used internally
   by tooltips) and is how Textual positions widgets at arbitrary screen
   coordinates — the same mechanism used by the `Tooltip` widget.

There is no `on_mouse_right_click` event. The correct intercept point is
`on_mouse_down` with a guard `if event.button == 3`. Using `on_click`
works too but is less immediate and, critically, `Tab._on_click(self)` has no
`event` parameter so it fires for any button — interception at `on_mouse_down`
lets us `event.stop()` before `Tab._on_click` runs.

### Context menu implementation: floating `Widget` in the screen layer

Two plausible approaches were evaluated:

1. **`ModalScreen`** — dims the full screen; correct semantics but wrong UX
   for a context menu (no dimming, dismiss on any outside click).
2. **Floating `Widget` mounted into the current `Screen`** — matches standard
   context menu UX: positioned at mouse coordinates, dismisses on Escape or
   outside click, renders on top of all other content via `layers`.

The plan uses approach 2: a `ContextMenu` widget that is mounted directly
into the active `Screen`, positioned via `styles.offset` (CSS absolute
positioning within a `Screen` whose `layout` is `vertical` — standard for
Textual screens). On dismiss it removes itself from the DOM.

Positioning: the handler reads `event.screen_x` / `event.screen_y` from the
`MouseDown` event and passes them to the `ContextMenu` constructor. The widget
sets `self.styles.offset = (screen_x, screen_y)` in `on_mount`. Clamping to
keep the menu fully on-screen is done by comparing `screen_x + menu_width` /
`screen_y + menu_height` against `self.app.console.width` / `app.console.height`
and subtracting the overflow.

The `ContextMenu` layer must sit above all content. Textual `Screen` already
has a system tooltip on a high layer; the plan adds a `"context-menu"` layer
to `MainScreen.LAYERS` (the `LAYERS` class variable on `Screen`). The widget
is assigned `self.styles.layer = "context-menu"`.

### Pilot test strategy for right-click

`pilot.click(widget, button=3)` generates `MouseDown(button=3)` + `MouseUp` +
`Click(button=3)` — confirmed from `pilot.py` source. This is the correct way
to simulate right-click in Textual tests as of 8.2.7.

### Mouse events on ListView items

`ListView` (`can_focus_children=False`) intercepts child clicks via
`ListItem._ChildClicked`. Right-click `MouseDown` events bubble normally from
child to parent. The `on_mouse_down` handler must be placed on `RoomItem` (the
`ListItem` subclass), not on `RoomList` or `ListView`, so the handler sees the
specific item that was right-clicked rather than the container.

### Matrix protocol: mute tag

The Matrix spec only standardises `m.favourite` and `m.lowpriority`. MSC2175
proposes `m.muted` (not yet merged). The existing codebase already uses
`m.mute`, which is the de-facto convention used by Element and other clients.
This plan keeps `m.mute`. A comment in the code should note the non-standard
status and reference MSC2175.

### Matrix protocol: redact power level

`PowerLevels.defaults.redact` defaults to `50` per the Matrix spec and is
confirmed in nio's `DefaultLevels`. To determine whether the current user may
redact another user's message, the UI needs to compare:

```
user_power_level >= room.power_levels.defaults.redact
```

`room.power_levels` is a `PowerLevels` object on `nio.MatrixRoom`, already
used by `MatrixClient.members()`. Rather than leaking nio types, a new
`MatrixClient.can_redact(room_id, target_sender)` method returns a `bool`:
`True` if `me()[0] == target_sender` OR `user_power_level >= redact_threshold`.
`FakeMatrixClient` adds a `can_redact_result: dict[tuple[str, str], bool]`
scripting dict with a default of `False`.

`redact_message(room_id, event_id, reason="")` already exists and accepts an
optional `reason`. No signature change needed.

### Emoji picker: no suitable library exists

Searched installed packages and PyPI for `textual-emoji`, `rich-emoji`. Neither
exists as a Textual-native component. The standard `emoji` Python package is not
installed in this project and adding a dependency purely to list emoji codepoints
is not justified when the required set for reactions is small and stable.

The plan uses a custom `EmojiPickerScreen(ModalScreen[str])` that:

- Embeds a curated list of ~80 frequently-used reaction emoji (the set used
  by Element web as a default reactions panel, covering all main categories).
- Renders them in a grid using a `DataTable` with one column per row (or a
  simple `Grid` of `Label` widgets — see Part 5 for the final choice).
- Provides a search `Input` that filters by emoji name (uses Python's
  `unicodedata.name()` for lookup — stdlib, no extra dependency).
- Returns the selected emoji string via `self.dismiss(emoji)`.
- The existing `#emoji-input` free-text field is **kept** as a fast path
  (power users can still type `👍` directly without opening the picker).

`send_reaction(room_id, event_id, emoji)` accepts any UTF-8 string as the
`key` field per the Matrix spec. No constraint to a fixed set.

---

## Part 1 — Shared context menu infrastructure

### New file: `src/telemente/tui/widgets/context_menu.py`

```python
class MenuItem:
    label: str
    action: Callable[[], None]
    enabled: bool = True   # greyed out if False, not selectable

@dataclass(frozen=True, slots=True)
class MenuSeparator:
    pass

MenuEntry = MenuItem | MenuSeparator

class ContextMenu(Widget):
    """Floating single-column menu. Mount into Screen, position at mouse coords."""

    class Dismissed(Message): ...  # no payload; fired before self.remove()

    def __init__(self, items: list[MenuEntry], screen_x: int, screen_y: int) -> None: ...

    def on_mount(self) -> None:
        # styles.layer = "context-menu"
        # styles.offset = clamped (screen_x, screen_y)
        # styles.position = "absolute"  (CSS absolute within screen)

    def compose(self) -> ComposeResult:
        # Render each MenuItem as a Button (or Static with mouse handler),
        # MenuSeparator as a horizontal Rule.

    def _dismiss(self) -> None:
        self.post_message(self.Dismissed())
        self.remove()

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            self._dismiss()

    def on_click(self, event: Click) -> None:
        # clicks outside the menu bubble to the Screen; the Screen handler
        # dismisses any active ContextMenu. This handler captures clicks
        # *inside* the menu to prevent the Screen handler from firing.
        event.stop()
```

**Positioning note:** CSS `position: absolute` is not a Textual CSS property
in the same sense as web CSS. Textual uses `offset` with an `auto`-layout
parent. The correct approach is to set `styles.offset = Offset(x, y)` where
`x` / `y` are relative to the `Screen`'s top-left. Because `Screen` uses
`layout: vertical`, children flow top-to-bottom. To place a widget at an
arbitrary position, the `ContextMenu` must be mounted as a child of the
`Screen` (not a panel), with `styles.offset` set and `styles.dock = ""` (no
docking). This is the same pattern used by the internal `Tooltip` widget
(line 1627 in `screen.py`): `tooltip.absolute_offset = self.app.mouse_position`.

Rather than replicating the tooltip mechanism, the `ContextMenu` should use
`self.absolute_offset = Offset(screen_x, screen_y)` — the same `Widget`
attribute. This positions it at the correct screen coordinate without fighting
the normal layout flow.

**Outside-click dismissal:** The `Screen` receives a `Click` from any widget
that does not call `event.stop()`. Add `on_click` to `MainScreen` that calls
`_dismiss_context_menu()`. The `ContextMenu` itself calls `event.stop()` so
its own clicks do not propagate to the screen handler.

**MainScreen changes:**
- Add `"context-menu"` to `LAYERS: ClassVar[tuple[str, ...]] = ("context-menu",)`.
- Add `_active_context_menu: ContextMenu | None = None`.
- Add `def _show_context_menu(self, items, screen_x, screen_y)`.
- Add `def _dismiss_context_menu()` (called on Screen click and Escape).
- Override `on_click(event)` to call `_dismiss_context_menu()` when no
  `ContextMenu` is active or the click is outside the menu.

**Keyboard navigation (arrow keys):**
`ContextMenu` maintains `_focused_index: int`. `on_key` handles `up`/`down`
to move focus between enabled `MenuItem` entries; `enter` activates the
focused item; `escape` dismisses. The widget uses `can_focus=True` and calls
`self.focus()` in `on_mount`.

### CSS additions (`tui/styles/app.tcss`)

```css
ContextMenu {
    width: auto;
    height: auto;
    min-width: 24;
    background: $surface;
    border: round $primary;
    padding: 0 1;
    layer: context-menu;
}

ContextMenu .menu-item {
    height: 1;
    padding: 0 1;
    width: 1fr;
}

ContextMenu .menu-item:hover,
ContextMenu .menu-item.-focused {
    background: $accent 30%;
}

ContextMenu .menu-item.-disabled {
    color: $text-disabled;
}

ContextMenu Rule {
    height: 1;
    color: $primary;
}
```

### Test cases (tier-2, `tests/tui/test_context_menu.py`)

- `test_context_menu_appears_at_mouse_position`: mount a minimal app with a
  `ContextMenu(items, 10, 5)` in screen; verify `absolute_offset == Offset(10, 5)`.
- `test_context_menu_escape_dismisses`: press Escape, verify `ContextMenu`
  removed from DOM and `Dismissed` message was received.
- `test_context_menu_enter_activates_item`: arrow-down to an item, press Enter,
  verify the callback was called.
- `test_context_menu_disabled_item_not_activatable`: navigate to a disabled
  item, press Enter, verify callback not called.
- `test_context_menu_outside_click_dismisses`: pilot.click on the screen
  background, verify menu removed.

---

## Part 2 — Tab context menu

### Affected files
- `src/telemente/tui/screens/main.py`

### Mechanism

`TabbedContent` renders tabs as `Tab` widgets (from `textual.widgets._tabs`).
`Tab._on_click(self)` has no `event` parameter and fires for any button.

To intercept before Tab activates, add `on_mouse_down` to `MainScreen`:

```python
def on_mouse_down(self, event: events.MouseDown) -> None:
    if event.button != 3:
        return
    # Walk up from event.widget to find a Tab
    widget = event.widget
    while widget is not None:
        if isinstance(widget, Tab):
            event.stop()   # prevent Tab._on_click from firing
            self._show_tab_context_menu(widget, event.screen_x, event.screen_y)
            return
        widget = widget.parent  # type: ignore[assignment]
```

`_show_tab_context_menu(tab: Tab, screen_x: int, screen_y: int)`:
- Finds the `room_id` for the tab by matching `tab.id` against `open_tabs`.
- If no match (e.g. a system tab), returns silently.
- Builds `items = [MenuItem("Close tab", lambda: self.run_worker(self.close_tab(room_id)))]`.
- Calls `self._show_context_menu(items, screen_x, screen_y)`.

**Why `on_mouse_down` on `MainScreen` rather than on `Tab` directly:**
`Tab` is a Textual internal widget; subclassing it to override `_on_click` is
fragile across upgrades. Bubbling `MouseDown` to `MainScreen` is cleaner.

### Test cases (tier-2, `tests/tui/test_tab_context_menu.py`)

- `test_tab_right_click_shows_menu`: open a room tab; `pilot.click(Tab,
  button=3)`; verify `ContextMenu` is in the DOM.
- `test_tab_close_from_context_menu`: open tab, right-click, click "Close tab"
  in menu; verify tab is removed and `ContextMenu` is gone.
- `test_no_context_menu_on_left_click_tab`: `pilot.click(Tab, button=1)`;
  verify no `ContextMenu` in DOM.

---

## Part 3 — Message context menu

### Affected files
- `src/telemente/tui/widgets/message_view.py`
- `src/telemente/tui/screens/main.py` (passes `can_redact` to `MessageView`)

### Mechanism

Add `on_mouse_down` to `MessageRow`:

```python
def on_mouse_down(self, event: events.MouseDown) -> None:
    if event.button != 3:
        return
    event.stop()
    self.post_message(self.ContextMenuRequest(
        message=self._message,
        screen_x=event.screen_x,
        screen_y=event.screen_y,
    ))
```

New inner message class on `MessageRow`:

```python
class ContextMenuRequest(TextualMessage):
    def __init__(self, message: Message, screen_x: int, screen_y: int) -> None: ...
```

`MessageView` handles `on_message_row_context_menu_request`:

```python
def on_message_row_context_menu_request(
    self, event: MessageRow.ContextMenuRequest
) -> None:
    msg = event.message
    my_user_id = self._client.me()[0]
    items: list[MenuEntry] = [
        MenuItem("Reply", lambda m=msg: self.post_message(MessageRow.ReplyRequest(m))),
    ]
    if msg.sender == my_user_id:
        items.append(
            MenuItem("Edit", lambda m=msg: self.post_message(MessageRow.EditRequest(m)))
        )
    items.append(
        MenuItem("React", lambda m=msg: self._open_emoji_picker_for(m.event_id))
    )
    can_delete = (
        msg.sender == my_user_id
        or self._client.can_redact(self._current_room_id or "", msg.sender)
    )
    items.append(
        MenuItem(
            "Delete",
            lambda m=msg: self.post_message(MessageRow.DeleteRequest(m)),
            enabled=can_delete,
        )
    )
    # Post up to MainScreen to show the menu
    self.post_message(MessageView.ShowContextMenu(items, event.screen_x, event.screen_y))
```

New inner message class on `MessageView`:

```python
class ShowContextMenu(TextualMessage):
    def __init__(self, items: list[MenuEntry], screen_x: int, screen_y: int) -> None: ...
```

`MainScreen` handles `on_message_view_show_context_menu` by calling
`self._show_context_menu(event.items, event.screen_x, event.screen_y)`.

### Power level check

`MessageView` needs access to `can_redact`. The existing `_MessageViewClient`
protocol gains one new method:

```python
async def can_redact(self, room_id: str, target_sender: str) -> bool: ...
```

Wait — `can_redact` should be synchronous (reads in-memory power levels, no
network). Protocol method is `def can_redact(self, room_id, target_sender)
-> bool`. `MatrixClient.can_redact` reads `self._client.rooms[room_id]
.power_levels.defaults.redact` and compares against the current user's level
via `self._client.rooms[room_id].power_levels.users.get(user_id,
defaults.users_default)`. `FakeMatrixClient.can_redact` uses a
`can_redact_results: dict[tuple[str,str], bool]` dict defaulting to `False`.

### Integration with emoji picker

`MessageView._open_emoji_picker_for(event_id: str)` calls
`self.app.push_screen(EmojiPickerScreen(), callback)` where `callback` sets
`self._react_target_event_id = event_id` and then calls
`self._handle_emoji_submitted_value(emoji)`. This replaces the current
`#emoji-input` flow for the context-menu path; the raw text input is kept.

### Test cases (tier-2, `tests/tui/test_message_context_menu.py`)

- `test_right_click_own_message_shows_edit_delete`: fake client with
  `me()` returning the message sender; right-click a `MessageRow`; verify
  "Edit" and "Delete" are enabled in the menu.
- `test_right_click_other_message_no_edit_delete_for_normal_user`:
  `can_redact` returns `False`; right-click other's message; verify no "Edit"
  and "Delete" is disabled (present but greyed).
- `test_right_click_other_message_delete_for_moderator`: `can_redact`
  returns `True`; verify "Delete" is enabled.
- `test_react_item_opens_emoji_picker`: click "React" in context menu;
  verify `EmojiPickerScreen` is pushed.
- `test_reply_item_posts_reply_request`: click "Reply"; verify
  `MessageRow.ReplyRequest` was handled (reply indicator shown).

---

## Part 4 — Room list context menu

### Affected files
- `src/telemente/tui/widgets/room_list.py`
- `src/telemente/tui/screens/main.py`

### Mechanism

Add `on_mouse_down` to `RoomItem`:

```python
def on_mouse_down(self, event: events.MouseDown) -> None:
    if event.button != 3:
        return
    event.stop()
    self.post_message(RoomItem.ContextMenuRequest(
        room=self._room,
        screen_x=event.screen_x,
        screen_y=event.screen_y,
    ))
```

New inner message class on `RoomItem`:

```python
class ContextMenuRequest(TextualMessage):
    def __init__(self, room: RoomSummary, screen_x: int, screen_y: int) -> None: ...
```

`RoomList` handles `on_room_item_context_menu_request` and re-posts it
upward as `RoomList.RoomContextMenu` (so it reaches `MainScreen`):

```python
class RoomContextMenu(TextualMessage):
    def __init__(self, room: RoomSummary, screen_x: int, screen_y: int) -> None: ...
```

`MainScreen` handles `on_room_list_room_context_menu`:

```python
def on_room_list_room_context_menu(
    self, event: RoomList.RoomContextMenu
) -> None:
    room = event.room
    tags = room.tags
    items: list[MenuEntry] = [
        MenuItem(
            "★ Unfavourite" if "m.favourite" in tags else "★ Favourite",
            lambda: self._toggle_tag_for(room.room_id, "m.favourite"),
        ),
        MenuItem(
            "↓ Remove low priority" if "m.lowpriority" in tags else "↓ Low priority",
            lambda: self._toggle_tag_for(room.room_id, "m.lowpriority"),
        ),
        MenuItem(
            "🔕 Unmute" if "m.mute" in tags else "🔕 Mute",  # m.mute: de-facto standard
            lambda: self._toggle_tag_for(room.room_id, "m.mute"),
        ),
        MenuSeparator(),
        MenuItem(
            "Leave room",
            lambda: self._confirm_leave_room(room.room_id),
        ),
    ]
    self._show_context_menu(items, event.screen_x, event.screen_y)
```

`_toggle_tag_for(room_id, tag)` is extracted from the existing
`TelementeCommands._do_toggle_tag` logic and placed on `MainScreen` so both
the palette commands and the context menu can call it. The palette commands
delegate to this method.

`_confirm_leave_room(room_id)` is extracted from `TelementeCommands.cmd_leave_room`
and placed on `MainScreen` for the same reason. It pushes `_ConfirmScreen`
(already in `commands.py`; move to `tui/widgets/confirm_screen.py` so both
`commands.py` and `main.py` can import it without circular imports).

**If plan 0012 (OptionList migration) has shipped:**
`RoomItem` (ListItem subclass) is replaced by `OptionList` options. In that
case, attach `on_mouse_down` to the `OptionList` widget itself in `RoomList`,
identify which option is under `event.y` using
`option_list.get_option_at_line(event.y)`, and build the `RoomContextMenu`
from that option's associated `RoomSummary`.

### Leave confirmation

Reuse the existing `_ConfirmScreen` from `commands.py`, refactored to
`tui/widgets/confirm_screen.py`. The same modal is used by both the command
palette and the context menu.

### Test cases (tier-2, `tests/tui/test_room_context_menu.py`)

- `test_room_right_click_shows_menu`: set rooms, right-click a `RoomItem`;
  verify `ContextMenu` in DOM with expected items.
- `test_favourite_toggle_tags_room`: room without `m.favourite`; click
  "★ Favourite"; verify `fake.set_tags` contains `(room_id, "m.favourite", None)`.
- `test_unfavourite_toggle_removes_tag`: room with `m.favourite`; click
  "★ Unfavourite"; verify `fake.removed_tags`.
- `test_mute_toggle`: room without `m.mute`; click "🔕 Mute"; verify
  `fake.set_tags`.
- `test_leave_shows_confirm_dialog`: click "Leave room"; verify
  `_ConfirmScreen` is pushed.
- `test_leave_confirmed_calls_leave_room`: confirm the dialog; verify
  `fake.left_rooms`.
- `test_leave_cancelled_does_nothing`: cancel the dialog; verify
  `fake.left_rooms` is empty.

---

## Part 5 — Emoji picker

### New file: `src/telemente/tui/screens/emoji_picker.py`

```python
class EmojiPickerScreen(ModalScreen[str]):
    """A searchable emoji grid for reactions."""
    BINDINGS = [Binding("escape", "dismiss_empty", "Cancel")]
```

### Curated emoji list

Embed a module-level constant `REACTION_EMOJI: list[tuple[str, str]]` — a
list of `(codepoint, name)` tuples. Use ~80 entries covering the standard
reaction set (thumbs up/down, heart, laugh, cry, surprised, clap, etc.). Names
are used for search; displayed label is the codepoint only.

Do not depend on `unicodedata.name()` for the embedded list — names are
pre-baked. `unicodedata.name()` is used only for the "search all Unicode"
extended mode (out of scope for this plan).

### Layout

```
EmojiPickerScreen
  Vertical (id="picker-container")
    Input (id="emoji-search", placeholder="Search emoji…")
    Grid (id="emoji-grid")   ← 8 columns, auto rows
      [Label or Button per emoji]
    Label (id="emoji-hint")  "Press Enter or click to react"
```

`Grid` uses CSS `grid-size: 8;` (8 columns). Each emoji is a `Button` with
`variant="default"`, label = the emoji codepoint, width=3. The `Grid`
scrolls vertically if the filtered set exceeds the visible area.

### Search/filter

`on_input_changed` on the `Input` filters `REACTION_EMOJI` by
`query.lower() in name.lower()` and rebuilds the grid (call
`grid.remove_children()` then mount new `Button` children).
Debounce is not needed for a small fixed list (~80 items).

### Return value

`on_button_pressed(event)` in `EmojiPickerScreen`:
```python
self.dismiss(event.button.label)
```

### Integration with `MessageView`

New method `_open_emoji_picker_for(event_id: str)`:
```python
def _open_emoji_picker_for(self, event_id: str) -> None:
    self._react_target_event_id = event_id
    def _on_picked(emoji: str | None) -> None:
        if emoji:
            self._handle_emoji_value(emoji)
    self.app.push_screen(EmojiPickerScreen(), _on_picked)
```

`_handle_emoji_value(emoji: str)` extracts the network-call logic from
`_handle_emoji_submitted` so both the text-input path and picker path
share it.

### Test cases (tier-2, `tests/tui/test_emoji_picker.py`)

- `test_emoji_picker_displays_emoji_grid`: mount `EmojiPickerScreen`, verify
  `Button` widgets are present in `#emoji-grid`.
- `test_emoji_picker_search_filters_results`: type "heart" in search;
  verify only heart-related emoji buttons remain.
- `test_emoji_picker_click_dismisses_with_emoji`: click an emoji button;
  verify screen is dismissed with that emoji string.
- `test_emoji_picker_escape_dismisses_with_none`: press Escape; verify screen
  dismissed with `None`.
- `test_emoji_picker_integration_sends_reaction`: via `MessageView` test —
  trigger "React" from context menu, click emoji in picker, verify
  `fake.sent_reactions` contains the expected tuple.

---

## Part 6 — MatrixClient changes

### New method: `MatrixClient.can_redact(room_id, target_sender) -> bool`

```python
def can_redact(self, room_id: str, target_sender: str) -> bool:
    """True if the logged-in user may redact target_sender's message.

    Checks own-message ownership first (always allowed), then compares the
    user's power level against the room's redact threshold (default 50).
    Not logged-in → False (conservative; avoids raising).
    """
```

**No new `ClientEvent` types are needed.** All new actions (react, edit,
redact, tag, leave) already exist on `MatrixClient`.

### `FakeMatrixClient` additions

```python
# Scripting dict: (room_id, target_sender) -> bool; default False
can_redact_results: dict[tuple[str, str], bool] = {}

def can_redact(self, room_id: str, target_sender: str) -> bool:
    if target_sender == self._me[0]:
        return True
    return self.can_redact_results.get((room_id, target_sender), False)
```

`reset_spies()` does not clear `can_redact_results` (it is scripted state,
not a spy).

### Stubs update (`stubs/nio/`)

`PowerLevels` and `DefaultLevels` attributes used by `can_redact` must be
present in the stubs. `stubs/nio/events/room_events.pyi` gains:

```python
@dataclass
class DefaultLevels:
    redact: int
    users_default: int

@dataclass
class PowerLevels:
    defaults: DefaultLevels
    users: dict[str, int]
    events: dict[str, int]
```

`stubs/nio/rooms.pyi` already has `power_levels: PowerLevels` on
`MatrixRoom` — verify this is present and add if not.

---

## Part 7 — Command palette entries

All new context-menu actions must also appear in the command palette per
the architecture invariant. Additions to `TelementeCommands._commands()`:

| Name | Help text |
|------|-----------|
| `"React to message"` | `"Open emoji picker to react to the focused message"` |
| `"Close tab"` | already exists |
| `"Toggle favourite ★"` | already exists |
| `"Toggle low priority ↓"` | already exists |
| `"Toggle mute 🔕"` | already exists |
| `"Leave room"` | already exists |

`"React to message"` is new. Its callback:
```python
def cmd_react_to_message(self) -> None:
    screen = self.app.screen
    if not isinstance(screen, MainScreen):
        return
    active_room = screen.active_room_id
    if active_room is None:
        self.app.notify("No room selected", severity="warning")
        return
    view = screen.message_view_for(active_room)
    if view is None:
        return
    # Find the focused MessageRow, or fall back to the last one
    focused = view.query("MessageRow:focus")
    if focused:
        row = focused.first(MessageRow)
    else:
        rows = list(view.query(MessageRow))
        if not rows:
            return
        row = rows[-1]
    view._open_emoji_picker_for(row.message.event_id)
```

---

## Mocking strategy

| Test area | Tier | Approach |
|-----------|------|----------|
| `ContextMenu` widget | Tier-2 | No client needed; test as standalone widget |
| Tab context menu | Tier-2 | `FakeMatrixClient`; drive with `pilot.click(Tab, button=3)` |
| Message context menu | Tier-2 | `FakeMatrixClient` with scripted `me()` and `can_redact_results` |
| Room context menu | Tier-2 | `FakeMatrixClient` with `rooms_data`, spy on `set_tags`/`removed_tags`/`left_rooms` |
| Emoji picker | Tier-2 | No client needed for the screen itself; integration test uses `FakeMatrixClient` |
| `MatrixClient.can_redact` | Tier-1 | Synthetic cassette with `m.room.power_levels` event; assert return value |

No new tier-1 (cassette) tests are required unless `can_redact` needs a
network path — it reads in-memory state, so a single synthetic cassette that
includes a `m.room.power_levels` event in the sync response is sufficient to
verify the in-memory path.

---

## Done-when checklist

- [ ] `ContextMenu` widget mounts, positions, and dismisses correctly (escape, outside click, action callback).
- [ ] Right-click on a `Tab` shows the menu; "Close tab" closes the tab.
- [ ] Right-click on a `MessageRow` shows the menu; items are conditionally enabled per sender and power level.
- [ ] "React" from message context menu opens `EmojiPickerScreen`; selecting an emoji calls `send_reaction`.
- [ ] Right-click on a `RoomItem` shows the menu; all four items function correctly.
- [ ] Leave confirmation reuses the existing `_ConfirmScreen` (now in `tui/widgets/confirm_screen.py`).
- [ ] `EmojiPickerScreen` filters by name, returns selected emoji, dismisses on Escape.
- [ ] `MatrixClient.can_redact` returns correct booleans for own/other messages.
- [ ] `FakeMatrixClient.can_redact` added; `can_redact_results` scripting dict works.
- [ ] All new actions are reachable via the command palette (including "React to message").
- [ ] `ruff check .` passes.
- [ ] `uv run mypy` passes.
- [ ] `pyright src/` passes.
- [ ] `uv run pytest` passes (all new test cases green).

---

## Files to create

| File | Purpose |
|------|---------|
| `src/telemente/tui/widgets/context_menu.py` | `ContextMenu`, `MenuItem`, `MenuSeparator`, `MenuEntry` |
| `src/telemente/tui/widgets/confirm_screen.py` | Extract `_ConfirmScreen` from `commands.py` |
| `src/telemente/tui/screens/emoji_picker.py` | `EmojiPickerScreen`, `REACTION_EMOJI` |
| `tests/tui/test_context_menu.py` | Tier-2 tests for shared `ContextMenu` widget |
| `tests/tui/test_tab_context_menu.py` | Tier-2 tests for tab right-click |
| `tests/tui/test_message_context_menu.py` | Tier-2 tests for message right-click |
| `tests/tui/test_room_context_menu.py` | Tier-2 tests for room list right-click |
| `tests/tui/test_emoji_picker.py` | Tier-2 tests for emoji picker |

## Files to modify

| File | Changes |
|------|---------|
| `src/telemente/matrix/client.py` | Add `can_redact(room_id, target_sender) -> bool` |
| `src/telemente/matrix/models.py` | No changes needed |
| `src/telemente/tui/screens/main.py` | Add `LAYERS`, `_show_context_menu`, `_dismiss_context_menu`, `_toggle_tag_for`, `_confirm_leave_room`, handlers for `ShowContextMenu`, `RoomContextMenu`, `on_mouse_down` (tab), `on_click` (dismiss) |
| `src/telemente/tui/widgets/message_view.py` | Add `_MessageViewClient.can_redact`, `MessageRow.ContextMenuRequest`, `MessageView.ShowContextMenu`, `_open_emoji_picker_for`, `_handle_emoji_value`; update `on_message_row_context_menu_request` |
| `src/telemente/tui/widgets/room_list.py` | Add `RoomItem.ContextMenuRequest`, `RoomList.RoomContextMenu`, `on_room_item_context_menu_request` |
| `src/telemente/tui/commands.py` | Import `_ConfirmScreen` from new location; add `cmd_react_to_message`; refactor `_do_toggle_tag` and `cmd_leave_room` to delegate to `MainScreen` methods |
| `src/telemente/tui/styles/app.tcss` | Add `ContextMenu` and `EmojiPickerScreen` CSS |
| `tests/fakes.py` | Add `can_redact_results`, `can_redact()` |
| `stubs/nio/events/room_events.pyi` | Add `DefaultLevels`, `PowerLevels` if missing |
| `stubs/nio/rooms.pyi` | Verify `power_levels: PowerLevels` on `MatrixRoom` |
