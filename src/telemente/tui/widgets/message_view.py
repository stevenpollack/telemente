"""MessageView widget — scrollable message timeline with composer (plan 0007).

Displays the message history for a Matrix room and provides a text input
for composing and sending messages.  The widget never imports nio directly —
all protocol access goes through the injected client.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

if TYPE_CHECKING:
    from telemente.tui.app import TelementeApp

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, Link, Static, TextArea

from telemente.matrix.models import Message
from telemente.tui.colors import sender_color
from telemente.tui.widgets.context_menu import MenuEntry, MenuItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class _MessageViewClient(Protocol):
    """Structural protocol for the subset of MatrixClient used by MessageView."""

    async def messages(self, room_id: str, limit: int = 50) -> list[Message]: ...

    async def send_text(
        self,
        room_id: str,
        body: str,
        reply_to_event_id: str | None = None,
        thread_root_event_id: str | None = None,
    ) -> str: ...

    async def send_reaction(self, room_id: str, event_id: str, emoji: str) -> None: ...

    async def edit_message(self, room_id: str, event_id: str, new_body: str) -> str: ...

    async def redact_message(self, room_id: str, event_id: str, reason: str = "") -> None: ...

    async def search_messages(self, room_id: str, query: str) -> list[str]: ...

    def me(self) -> tuple[str, str]: ...

    def can_redact(self, room_id: str, target_sender: str) -> bool: ...


# ---------------------------------------------------------------------------
# Per-message widget
# ---------------------------------------------------------------------------


_URL_RE = re.compile(r"(https?://\S+)")


def _linkify(text: str) -> str:
    """Wrap bare https?:// URLs in Textual link markup for OSC 8 hyperlinks."""
    return _URL_RE.sub(lambda m: f'[link="{m.group(1)}"]{m.group(1)}[/link]', text)


class _DateSeparator(Static):
    """A date separator rendered between messages from different days."""

    DEFAULT_CSS = """
    _DateSeparator {
        height: 1;
        padding: 0 1;
        text-align: center;
        color: $text-muted;
        text-style: bold;
    }
    """


class MessageRow(Widget, can_focus=True):
    """A single rendered message in the timeline."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("e", "react", "React"),
        Binding("r", "reply", "Reply"),
        Binding("E", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
    ]

    DEFAULT_CSS = """
    MessageRow {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }
    MessageRow Static {
        height: auto;
        text-wrap: wrap;
    }
    MessageRow Link {
        height: 1;
        padding: 0;
        margin: 0;
    }
    MessageRow:focus {
        border: tall $accent;
    }
    """

    class ReactRequest(TextualMessage):
        def __init__(self, event_id: str) -> None:
            super().__init__()
            self.event_id = event_id

    class ReplyRequest(TextualMessage):
        def __init__(self, message: Message) -> None:
            super().__init__()
            self.message = message

    class EditRequest(TextualMessage):
        def __init__(self, message: Message) -> None:
            super().__init__()
            self.message = message

    class DeleteRequest(TextualMessage):
        def __init__(self, message: Message) -> None:
            super().__init__()
            self.message = message

    class ContextMenuRequest(TextualMessage):
        def __init__(self, message: Message, screen_x: int, screen_y: int) -> None:
            super().__init__()
            self.message = message
            self.screen_x = screen_x
            self.screen_y = screen_y

    def __init__(self, message: Message, reply_quoted: Message | None = None) -> None:
        super().__init__()
        self._message = message
        self._reply_quoted = reply_quoted
        # Live reactions dict (updated optimistically)
        self._reactions: dict[str, list[str]] = dict(message.reactions)

    @property
    def message(self) -> Message:
        return self._message

    def compose(self) -> ComposeResult:
        msg = self._message
        local_ts: datetime = msg.timestamp.astimezone()
        time_str = local_ts.strftime("%H:%M")
        color = sender_color(msg.sender)
        if self._reply_quoted is not None:
            q = self._reply_quoted
            preview = q.body[:60]
            yield Static(
                f"[dim]↩ {q.sender_display_name}: {preview}[/dim]",
                markup=True,
                classes="reply-quote",
            )
        elif msg.reply_to_event_id:
            yield Static(
                "[dim]↩ (original message not loaded)[/dim]",
                markup=True,
                classes="reply-quote",
            )
        body = _linkify(msg.body)
        header_body = (
            f"[bold {color}]{msg.sender_display_name}[/bold {color}] [dim]{time_str}[/dim]\n{body}"
        )
        yield Static(header_body, markup=True, id="body-static")
        if self._reactions:
            chips = "  ".join(
                f"{emoji} {len(senders)}" for emoji, senders in self._reactions.items()
            )
            yield Static(chips, classes="reaction-chips", id="reaction-chips")
        if msg.media_url:
            icon = {"image": "🖼", "video": "🎬", "audio": "🎵"}.get(msg.media_type or "", "📎")
            label = f"{icon} {msg.body or msg.media_type or 'attachment'}"
            yield Link(label, url=msg.media_url)

    def update_reaction(self, emoji: str, user_id: str) -> None:
        """Optimistically add a reaction and refresh the chips display."""
        senders = self._reactions.setdefault(emoji, [])
        if user_id not in senders:
            senders.append(user_id)
        chips = "  ".join(f"{e} {len(s)}" for e, s in self._reactions.items())
        existing = self.query("#reaction-chips")
        if existing:
            existing.first(Static).update(chips)
        else:
            self.mount(Static(chips, classes="reaction-chips", id="reaction-chips"))

    def update_body(self, new_body: str) -> None:
        """Update the displayed message body after an edit."""
        self._message = dataclasses.replace(self._message, body=new_body)
        self._refresh_body_static()

    def update_sender_display_name(self, display_name: str) -> None:
        """Patch the sender name in-place without rebuilding the row."""
        if display_name == self._message.sender_display_name:
            return
        self._message = dataclasses.replace(self._message, sender_display_name=display_name)
        self._refresh_body_static()

    def _refresh_body_static(self) -> None:
        color = sender_color(self._message.sender)
        local_ts: datetime = self._message.timestamp.astimezone()
        time_str = local_ts.strftime("%H:%M")
        body = _linkify(self._message.body)
        header_body = (
            f"[bold {color}]{self._message.sender_display_name}[/bold {color}]"
            f" [dim]{time_str}[/dim]\n{body}"
        )
        self.query_one("#body-static", Static).update(header_body)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        event.stop()
        logger.debug("MessageRow: right-click on event_id=%s", self._message.event_id)
        self.post_message(
            self.ContextMenuRequest(
                message=self._message,
                screen_x=event.screen_x,
                screen_y=event.screen_y,
            )
        )

    def action_react(self) -> None:
        self.post_message(self.ReactRequest(event_id=self._message.event_id))

    def action_reply(self) -> None:
        self.post_message(self.ReplyRequest(message=self._message))

    def action_edit(self) -> None:
        self.post_message(self.EditRequest(message=self._message))

    def action_delete(self) -> None:
        self.post_message(self.DeleteRequest(message=self._message))


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


class ComposerArea(TextArea):
    """Multi-line composer: Enter submits, Shift+Enter inserts a newline."""

    class Submitted(TextualMessage):
        def __init__(self, area: ComposerArea, value: str) -> None:
            super().__init__()
            self.area = area
            self.value = value

    DEFAULT_CSS = """
    ComposerArea {
        height: auto;
        max-height: 10;
        border: tall $border;
    }
    ComposerArea:focus {
        border: tall $accent;
    }
    """

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.rstrip("\n")
            if text:
                self.post_message(self.Submitted(self, text))
                self.clear()
        # shift+enter → let TextArea's default newline insertion run


# ---------------------------------------------------------------------------
# MessageView
# ---------------------------------------------------------------------------


class MessageView(Widget):
    """Center panel: scrollable message timeline + composer input.

    Public API
    ----------
    load_room(room_id)       Fetch history and render it (replaces current content).
    append_message(message)  Add a live-arriving message (filtered by current room).
    clear()                  Remove all rendered messages.
    current_room_id          The currently loaded room, or None.
    """

    class ScrolledToBottom(TextualMessage):
        """Posted when the timeline is scrolled to the bottom and a room is loaded."""

        def __init__(self, room_id: str, event_id: str) -> None:
            super().__init__()
            self.room_id = room_id
            self.event_id = event_id

    class ShowContextMenu(TextualMessage):
        """Requests the parent screen to display a context menu."""

        def __init__(
            self,
            items: list[MenuEntry],
            screen_x: int,
            screen_y: int,
        ) -> None:
            super().__init__()
            self.items = items
            self.screen_x = screen_x
            self.screen_y = screen_y

    class OpenThread(TextualMessage):
        """Requests the parent screen to open the thread panel for a thread root."""

        def __init__(self, room_id: str, root_event_id: str) -> None:
            super().__init__()
            self.room_id = room_id
            self.root_event_id = root_event_id

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("G", "scroll_latest", "Latest"),
        Binding("ctrl+f", "open_search", "Search"),
        Binding("n", "next_match", "Next match", show=False),
        Binding("N", "prev_match", "Prev match", show=False),
    ]

    @property
    def app(self) -> TelementeApp:  # type: ignore[override]
        return cast("TelementeApp", super().app)  # pyright: ignore[reportUnknownMemberType]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        client: _MessageViewClient,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._client = client
        self._current_room_id: str | None = None
        self._rendered_event_ids: set[str] = set()
        self._msgs_by_id: dict[str, Message] = {}
        self._react_target_event_id: str | None = None
        self._replying_to: Message | None = None
        self._editing: Message | None = None
        self._match_event_ids: list[str] = []
        self._match_index: int = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="message-timeline"):
            pass
        yield Static("", id="encryption-notice", classes="encryption-notice")
        yield Static("", id="typing-indicator", classes="typing-indicator")
        with Horizontal(id="search-bar"):
            yield Input(id="search-input", placeholder="Search messages…")
            yield Static("", id="search-count")
        yield Static("", id="reply-indicator", classes="reply-banner")
        yield Input(id="emoji-input", placeholder="React…")
        yield ComposerArea(id="composer", soft_wrap=True)

    def on_mount(self) -> None:
        self.query_one("#reply-indicator", Static).display = False
        self.query_one("#emoji-input", Input).display = False
        self.query_one("#typing-indicator", Static).display = False
        self.query_one("#search-bar").display = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_room_id(self) -> str | None:
        return self._current_room_id

    @property
    def newest_event_id(self) -> str | None:
        """The event_id of the most recently rendered message, or None.

        _msgs_by_id preserves insertion order; the last key is the newest.
        """
        if not self._msgs_by_id:
            return None
        return next(reversed(self._msgs_by_id))

    async def load_room(self, room_id: str) -> None:
        """Fetch message history for *room_id* and render it."""
        logger.info("load_room: room_id=%s", room_id)
        self._current_room_id = room_id
        self.clear()
        messages = await self._client.messages(room_id)
        logger.info("load_room: fetched %d messages room_id=%s", len(messages), room_id)
        timeline = self.query_one("#message-timeline", VerticalScroll)
        self._render_messages(timeline, messages)
        self._scroll_to_bottom()
        self._update_encryption_notice(messages)

    def append_message(self, message: Message) -> None:
        """Append a live message if it belongs to the current room (deduplicates)."""
        if message.room_id != self._current_room_id:
            return
        if message.event_id in self._rendered_event_ids:
            return
        self._rendered_event_ids.add(message.event_id)
        self._msgs_by_id[message.event_id] = message
        reply_quoted = (
            self._msgs_by_id.get(message.reply_to_event_id) if message.reply_to_event_id else None
        )
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.mount(MessageRow(message, reply_quoted=reply_quoted))
        self._scroll_to_bottom()

    def patch_sender_names(self, names: dict[str, str]) -> None:
        """Update sender display names in already-rendered rows without rebuilding.

        ``names`` maps user_id → resolved display_name.  Only rows whose
        sender_display_name differs from the resolved name are touched.
        """
        for row in self.query(MessageRow):
            resolved = names.get(row.message.sender)
            if resolved is not None:
                row.update_sender_display_name(resolved)

    def clear(self) -> None:
        """Remove all rendered message rows and date separators."""
        timeline = self.query_one("#message-timeline", VerticalScroll)
        for widget in list(timeline.query("MessageRow, _DateSeparator")):
            widget.remove()
        self._rendered_event_ids.clear()
        self._msgs_by_id.clear()
        self.query_one("#encryption-notice", Static).display = False

    def remove_message(self, event_id: str) -> None:
        """Remove a message row by event_id (for server-side redactions)."""
        self._rendered_event_ids.discard(event_id)
        self._msgs_by_id.pop(event_id, None)
        for row in list(self.query(MessageRow)):
            if row.message.event_id == event_id:
                row.remove()
                break

    def set_typing(self, room_id: str, user_ids: list[str]) -> None:
        """Update the typing indicator for *room_id*.

        Only shown when *room_id* matches the active room and *user_ids* is non-empty.
        """
        if room_id != self._current_room_id:
            return
        indicator = self.query_one("#typing-indicator", Static)
        if not user_ids:
            indicator.display = False
            return
        names = ", ".join(user_ids)
        indicator.update(f"Typing: {names}…")
        indicator.display = True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_scroll_latest(self) -> None:
        self._scroll_to_bottom(notify=True)
        self.query_one("#composer", ComposerArea).focus()

    def action_open_search(self) -> None:
        self.query_one("#search-bar").display = True
        self.query_one("#search-input", Input).focus()

    def action_next_match(self) -> None:
        if not self._match_event_ids:
            return
        self._match_index = (self._match_index + 1) % len(self._match_event_ids)
        self._apply_current_match()

    def action_prev_match(self) -> None:
        if not self._match_event_ids:
            return
        self._match_index = (self._match_index - 1) % len(self._match_event_ids)
        self._apply_current_match()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("next_match", "prev_match"):
            return len(self._match_event_ids) > 0
        return True

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_message_row_react_request(self, event: MessageRow.ReactRequest) -> None:
        self._react_target_event_id = event.event_id
        emoji_input = self.query_one("#emoji-input", Input)
        emoji_input.clear()
        emoji_input.display = True
        emoji_input.focus()

    def on_message_row_reply_request(self, event: MessageRow.ReplyRequest) -> None:
        self._replying_to = event.message
        indicator = self.query_one("#reply-indicator", Static)
        msg = event.message
        indicator.update(f"↩ Replying to {msg.sender_display_name}: {msg.body[:40]}")
        indicator.display = True
        self.query_one("#composer", ComposerArea).focus()

    def on_message_row_edit_request(self, event: MessageRow.EditRequest) -> None:
        """Pre-fill the composer with the message body for editing."""
        my_user_id = self._client.me()[0]
        if event.message.sender != my_user_id:
            return
        self._editing = event.message
        composer = self.query_one("#composer", ComposerArea)
        composer.clear()
        composer.insert(event.message.body)
        composer.focus()

    def on_message_row_delete_request(self, event: MessageRow.DeleteRequest) -> None:
        """Show confirmation before redacting (Bug 5 fix)."""
        msg = event.message
        room_id = self._current_room_id
        if not room_id:
            return
        from telemente.tui.widgets.confirm_screen import ConfirmScreen

        def _on_confirmed(confirmed: bool | None) -> None:
            if confirmed is True:
                self.run_worker(self._do_redact_and_remove(room_id, msg), exclusive=False)

        self.app.push_screen(
            ConfirmScreen("Delete this message?"),
            _on_confirmed,
        )

    def on_message_row_context_menu_request(self, event: MessageRow.ContextMenuRequest) -> None:
        msg = event.message
        logger.debug("MessageView: context menu opened for event_id=%s", msg.event_id)
        my_user_id = self._client.me()[0]
        room_id = self._current_room_id or ""

        def _reply() -> None:
            self.post_message(MessageRow.ReplyRequest(msg))

        def _edit() -> None:
            self.post_message(MessageRow.EditRequest(msg))

        def _react() -> None:
            logger.debug("MessageView: 'React' selected for event_id=%s", msg.event_id)
            self.open_emoji_picker_for(msg.event_id)

        def _delete() -> None:
            self.post_message(MessageRow.DeleteRequest(msg))

        thread_root_id = msg.thread_root_id

        def _view_thread() -> None:
            if thread_root_id is not None:
                self.post_message(MessageView.OpenThread(room_id, thread_root_id))

        items: list[MenuEntry] = [MenuItem("Reply", _reply)]
        if msg.sender == my_user_id:
            items.append(MenuItem("Edit", _edit))
        items.append(MenuItem("React", _react))
        can_delete = msg.sender == my_user_id or self._client.can_redact(room_id, msg.sender)
        items.append(MenuItem("Delete", _delete, enabled=can_delete))
        if thread_root_id is not None:
            items.append(MenuItem("View thread", _view_thread))
        self.post_message(MessageView.ShowContextMenu(items, event.screen_x, event.screen_y))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        self.run_worker(self._do_search(event.value), exclusive=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "emoji-input":
            self._handle_emoji_submitted(event)

    def on_composer_area_submitted(self, event: ComposerArea.Submitted) -> None:
        self._handle_composer_submitted(event)

    def on_key(self, event: object) -> None:
        """ESC dismisses search bar, emoji input, reply indicator, or edit mode."""
        from textual.events import Key

        if not isinstance(event, Key) or event.key != "escape":
            return
        emoji_input = self.query_one("#emoji-input", Input)
        if emoji_input.display:
            emoji_input.display = False
            self._react_target_event_id = None
            return
        if self.query_one("#search-bar").display:
            self._close_search()
            return
        indicator = self.query_one("#reply-indicator", Static)
        if indicator.display:
            indicator.display = False
            self._replying_to = None
            return
        if self._editing is not None:
            self._editing = None
            self.query_one("#composer", ComposerArea).clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def open_emoji_picker_for(self, event_id: str) -> None:
        """Open the emoji picker modal and send the selected reaction.

        Uses call_next so push_screen runs after the current message-pump
        turn completes. If called from a ContextMenu action, the menu is
        being dismissed in the same turn — deferring ensures the callback is
        registered with MessageView as the active pump, not the (soon-removed)
        ContextMenu.
        """
        from telemente.tui.screens.emoji_picker import EmojiPickerScreen

        logger.debug("MessageView: opening emoji picker for event_id=%s", event_id)
        self._react_target_event_id = event_id

        def _on_picked(emoji: str | None) -> None:
            if emoji:
                self._handle_emoji_value(emoji)

        self.call_next(self.app.push_screen, EmojiPickerScreen(), _on_picked)

    def _handle_emoji_value(self, emoji: str) -> None:
        """Send a reaction for the currently targeted event ID."""
        room_id = self._current_room_id
        target_event_id = self._react_target_event_id
        self._react_target_event_id = None
        if emoji and room_id and target_event_id:
            my_user_id = self._client.me()[0]
            for row in self.query(MessageRow):
                if row.message.event_id == target_event_id:
                    row.update_reaction(emoji, my_user_id)
                    break
            self.run_worker(self._do_react(room_id, target_event_id, emoji), exclusive=False)

    def _handle_emoji_submitted(self, event: Input.Submitted) -> None:
        emoji = event.value.strip()
        room_id = self._current_room_id
        target_event_id = self._react_target_event_id
        event.input.display = False
        event.input.clear()
        self._react_target_event_id = None
        if emoji and room_id and target_event_id:
            # Optimistic local update on the target row
            my_user_id = self._client.me()[0]
            for row in self.query(MessageRow):
                if row.message.event_id == target_event_id:
                    row.update_reaction(emoji, my_user_id)
                    break
            self.run_worker(self._do_react(room_id, target_event_id, emoji), exclusive=False)

    def _handle_composer_submitted(self, event: ComposerArea.Submitted) -> None:
        text = event.value.strip()
        if not text or self._current_room_id is None:
            return
        room_id = self._current_room_id

        if self._editing is not None:
            editing = self._editing
            self._editing = None
            event.area.clear()
            self.run_worker(self._do_edit(room_id, editing, text), exclusive=False)
            return

        event.area.clear()
        reply_to = self._replying_to.event_id if self._replying_to else None
        if self._replying_to is not None:
            self._replying_to = None
            self.query_one("#reply-indicator", Static).display = False
        self.run_worker(self._do_send(room_id, text, reply_to_event_id=reply_to), exclusive=False)

    async def _do_react(self, room_id: str, event_id: str, emoji: str) -> None:
        logger.debug("Reacting to %s in %s with %s", event_id, room_id, emoji)
        await self._client.send_reaction(room_id, event_id, emoji)

    async def _do_edit(self, room_id: str, original: Message, new_body: str) -> None:
        logger.debug("Editing %s in %s", original.event_id, room_id)
        new_event_id = await self._client.edit_message(room_id, original.event_id, new_body)
        # Suppress the sync echo for the replacement event.
        self._rendered_event_ids.add(new_event_id)
        # Optimistic local update on the row.
        for row in self.query(MessageRow):
            if row.message.event_id == original.event_id:
                row.update_body(new_body)
                self._msgs_by_id[original.event_id] = row.message
                break

    async def _do_redact_and_remove(self, room_id: str, msg: Message) -> None:
        """Send the redact RPC; remove the row only on success."""
        logger.debug("Redacting %s in %s", msg.event_id, room_id)
        try:
            await self._client.redact_message(room_id, msg.event_id)
        except Exception as exc:
            logger.warning("redact_message failed for %s: %s", msg.event_id, exc)
            self.app.notify(f"Could not delete message: {exc}", severity="error")
            return
        for row in list(self.query(MessageRow)):
            if row.message.event_id == msg.event_id:
                row.remove()
                self._rendered_event_ids.discard(msg.event_id)
                self._msgs_by_id.pop(msg.event_id, None)
                break

    async def _do_send(
        self, room_id: str, body: str, *, reply_to_event_id: str | None = None
    ) -> None:
        """Send a message and echo it immediately into the local timeline."""
        logger.debug("Sending to %s: %.60r", room_id, body)
        user_id, display_name = self._client.me()
        event_id = await self._client.send_text(room_id, body, reply_to_event_id=reply_to_event_id)
        if not event_id:
            event_id = f"$local:{room_id}:{body[:16]}"
        local_msg = Message(
            event_id=event_id,
            room_id=room_id,
            sender=user_id,
            sender_display_name=display_name,
            body=body,
            timestamp=datetime.now(tz=UTC),
            reply_to_event_id=reply_to_event_id,
        )
        self.append_message(local_msg)

    def _render_messages(self, timeline: VerticalScroll, messages: list[Message]) -> None:
        """Render messages with date separators, resolving reply-to parent references."""
        last_date: date | None = None
        for msg in messages:
            self._rendered_event_ids.add(msg.event_id)
            self._msgs_by_id[msg.event_id] = msg
        for msg in messages:
            msg_date = msg.timestamp.astimezone().date()
            if msg_date != last_date:
                timeline.mount(_DateSeparator(self._format_date(msg_date)))
                last_date = msg_date
            reply_quoted = (
                self._msgs_by_id.get(msg.reply_to_event_id) if msg.reply_to_event_id else None
            )
            timeline.mount(MessageRow(msg, reply_quoted=reply_quoted))

    @staticmethod
    def _format_date(d: date) -> str:
        """Format a date for the separator. Shows relative labels for recent dates."""
        today = date.today()
        delta = (today - d).days
        if delta == 0:
            return "— Today —"
        if delta == 1:
            return "— Yesterday —"
        if delta < 7:
            return f"— {d.strftime('%A')} —"
        return f"— {d.strftime('%d %b %Y')} —"

    def _update_encryption_notice(self, messages: list[Message]) -> None:
        """Show a notice if all messages are undecryptable."""
        notice = self.query_one("#encryption-notice", Static)
        if not messages:
            notice.display = False
            return
        all_encrypted = all("\U0001f512 Unable to decrypt" in m.body for m in messages)
        if all_encrypted:
            notice.update(
                "\U0001f512 All messages in this room are encrypted. "
                "Session keys from other devices have not been shared "
                "with telemente yet — messages will appear once keys arrive."
            )
            notice.display = True
        else:
            notice.display = False

    async def _do_search(self, query: str) -> None:
        room_id = self._current_room_id
        for row in self.query(MessageRow):
            row.remove_class("-search-match", "-search-current")
        if not query or not room_id:
            self._match_event_ids = []
            self._match_index = 0
            self.query_one("#search-count", Static).update("")
            self.query_one("#message-timeline", VerticalScroll).focus()
            return
        match_ids = await self._client.search_messages(room_id, query)
        self._match_event_ids = match_ids
        self._match_index = 0
        self._apply_search_highlights()
        # Transfer focus to a non-Input widget so n/N bindings survive _binding_chain filtering
        self.query_one("#message-timeline", VerticalScroll).focus()

    def _apply_search_highlights(self) -> None:
        match_set = set(self._match_event_ids)
        for row in self.query(MessageRow):
            if row.message.event_id in match_set:
                row.add_class("-search-match")
            else:
                row.remove_class("-search-match")
        self._apply_current_match()

    def _apply_current_match(self) -> None:
        for row in self.query(MessageRow):
            row.remove_class("-search-current")
        if not self._match_event_ids:
            self.query_one("#search-count", Static).update("")
            return
        current_id = self._match_event_ids[self._match_index]
        for row in self.query(MessageRow):
            if row.message.event_id == current_id:
                row.add_class("-search-current")
                break
        total = len(self._match_event_ids)
        self.query_one("#search-count", Static).update(f"{self._match_index + 1} / {total}")

    def _close_search(self) -> None:
        self.query_one("#search-bar").display = False
        self.query_one("#search-input", Input).clear()
        self._match_event_ids = []
        self._match_index = 0
        for row in self.query(MessageRow):
            row.remove_class("-search-match", "-search-current")
        self.query_one("#search-count", Static).update("")

    def _scroll_to_bottom(self, *, notify: bool = False) -> None:
        """Scroll the timeline to the bottom.

        notify: when True, post ScrolledToBottom so MainScreen can send a
        read receipt. Only set by action_scroll_latest (explicit user
        scroll); load_room and append_message use the default False.
        """
        timeline = self.query_one("#message-timeline", VerticalScroll)
        timeline.scroll_end(animate=False)
        if notify:
            newest = self.newest_event_id
            if newest and self._current_room_id:
                self.post_message(self.ScrolledToBottom(self._current_room_id, newest))
