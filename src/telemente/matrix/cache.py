"""SQLite-backed message cache for telemente (plan 0013).

Owned by MatrixClient; the UI never touches this directly.
Pure performance optimization — all data is recoverable from the homeserver.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import aiosqlite

from telemente.matrix.models import Message

logger = logging.getLogger(__name__)

# Required columns for schema validation.
_REQUIRED_COLUMNS = frozenset(
    {
        "room_id",
        "event_id",
        "sender",
        "sender_display_name",
        "body",
        "timestamp_ms",
        "media_url",
        "media_type",
        "reactions",
        "reply_to_event_id",
    }
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    room_id               TEXT    NOT NULL,
    event_id              TEXT    NOT NULL,
    sender                TEXT    NOT NULL,
    sender_display_name   TEXT    NOT NULL,
    body                  TEXT    NOT NULL,
    timestamp_ms          INTEGER NOT NULL,
    media_url             TEXT,
    media_type            TEXT,
    reactions             TEXT    NOT NULL DEFAULT '{}',
    reply_to_event_id     TEXT,
    PRIMARY KEY (room_id, event_id)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_messages_room_ts
    ON messages (room_id, timestamp_ms DESC)
"""


class MessageCache:
    """Thin async SQLite cache for Message objects, keyed by (room_id, event_id)."""

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None

    async def open(self, db_path: str) -> None:
        """Open the database, enable WAL mode, and create/validate the schema."""
        self._db = await aiosqlite.connect(db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._ensure_schema()
        await self._db.commit()

    async def _ensure_schema(self) -> None:
        """Create the messages table; drop and recreate on schema mismatch."""
        assert self._db is not None
        # Check whether the table already exists and has all required columns.
        async with self._db.execute("PRAGMA table_info(messages)") as cursor:
            rows = await cursor.fetchall()

        if rows:
            existing_columns = {row[1] for row in rows}
            if not _REQUIRED_COLUMNS.issubset(existing_columns):
                logger.warning(
                    "MessageCache: schema mismatch — dropping and recreating messages table"
                )
                await self._db.execute("DROP TABLE IF EXISTS messages")
                await self._db.execute("DROP INDEX IF EXISTS idx_messages_room_ts")

        await self._db.execute(_CREATE_TABLE)
        await self._db.execute(_CREATE_INDEX)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def put(self, message: Message) -> None:
        """INSERT OR REPLACE a single message."""
        assert self._db is not None
        await self._db.execute(
            """
            INSERT OR REPLACE INTO messages
              (room_id, event_id, sender, sender_display_name, body,
               timestamp_ms, media_url, media_type, reactions, reply_to_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _to_row(message),
        )
        await self._db.commit()

    async def put_many(self, messages: list[Message]) -> None:
        """Bulk INSERT OR REPLACE."""
        if not messages:
            return
        assert self._db is not None
        await self._db.executemany(
            """
            INSERT OR REPLACE INTO messages
              (room_id, event_id, sender, sender_display_name, body,
               timestamp_ms, media_url, media_type, reactions, reply_to_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [_to_row(m) for m in messages],
        )
        await self._db.commit()

    async def get_room(self, room_id: str, limit: int = 50) -> list[Message]:
        """Return up to `limit` messages for a room, sorted oldest-first."""
        assert self._db is not None
        # Subquery: newest `limit` rows per room, then outer sort ascending.
        async with self._db.execute(
            """
            SELECT room_id, event_id, sender, sender_display_name, body,
                   timestamp_ms, media_url, media_type, reactions, reply_to_event_id
            FROM (
                SELECT room_id, event_id, sender, sender_display_name, body,
                       timestamp_ms, media_url, media_type, reactions, reply_to_event_id
                FROM messages
                WHERE room_id = ?
                ORDER BY timestamp_ms DESC
                LIMIT ?
            )
            ORDER BY timestamp_ms ASC
            """,
            (room_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_from_row(tuple(row)) for row in rows]

    async def evict_old(self, room_id: str, keep: int = 500) -> None:
        """Delete all but the newest `keep` rows for a room."""
        assert self._db is not None
        await self._db.execute(
            """
            DELETE FROM messages
            WHERE room_id = ?
              AND event_id NOT IN (
                SELECT event_id FROM messages
                WHERE room_id = ?
                ORDER BY timestamp_ms DESC
                LIMIT ?
              )
            """,
            (room_id, room_id, keep),
        )
        await self._db.commit()

    async def is_cold(self, room_id: str) -> bool:
        """Return True if the room has no cached rows."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT 1 FROM messages WHERE room_id = ? LIMIT 1",
            (room_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row is None


# ---------------------------------------------------------------------------
# Row conversion helpers
# ---------------------------------------------------------------------------


def _to_row(
    m: Message,
) -> tuple[str, str, str, str, str, int, str | None, str | None, str, str | None]:
    timestamp_ms = int(m.timestamp.timestamp() * 1000)
    reactions_json = json.dumps(m.reactions)
    return (
        m.room_id,
        m.event_id,
        m.sender,
        m.sender_display_name,
        m.body,
        timestamp_ms,
        m.media_url,
        m.media_type,
        reactions_json,
        m.reply_to_event_id,
    )


def _from_row(row: tuple[object, ...]) -> Message:
    (
        room_id,
        event_id,
        sender,
        sender_display_name,
        body,
        timestamp_ms,
        media_url,
        media_type,
        reactions_json,
        reply_to_event_id,
    ) = row
    timestamp = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=UTC)  # type: ignore[arg-type]
    reactions: dict[str, list[str]] = json.loads(str(reactions_json))
    return Message(
        event_id=str(event_id),
        room_id=str(room_id),
        sender=str(sender),
        sender_display_name=str(sender_display_name),
        body=str(body),
        timestamp=timestamp,
        media_url=str(media_url) if media_url is not None else None,
        media_type=str(media_type) if media_type is not None else None,
        reactions=reactions,
        reply_to_event_id=str(reply_to_event_id) if reply_to_event_id is not None else None,
    )
