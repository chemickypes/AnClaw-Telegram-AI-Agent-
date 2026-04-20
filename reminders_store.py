"""
Reminders store — SQLite-backed one-shot reminders.
"""
import logging
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)

_DB_PATH = "tmp/agent_data.db"


def init_reminders_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id                  TEXT PRIMARY KEY,
                message             TEXT NOT NULL,
                fire_at             TEXT NOT NULL,
                calendar_event_id   TEXT,
                calendar_event_title TEXT,
                chat_id             INTEGER NOT NULL,
                created_at          TEXT NOT NULL
            )
        """)


def save_reminder(
    message: str,
    fire_at: datetime,
    chat_id: int,
    calendar_event_id: str | None = None,
    calendar_event_title: str | None = None,
) -> str:
    reminder_id = uuid.uuid4().hex[:8]
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO reminders "
            "(id, message, fire_at, calendar_event_id, calendar_event_title, chat_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                reminder_id,
                message,
                fire_at.isoformat(),
                calendar_event_id,
                calendar_event_title,
                chat_id,
                datetime.now(_TZ).isoformat(),
            ),
        )
    return reminder_id


def get_reminder(reminder_id: str) -> tuple | None:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, message, fire_at, calendar_event_id, calendar_event_title, chat_id "
            "FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()


def get_all_reminders() -> list[tuple]:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, message, fire_at, calendar_event_id, calendar_event_title, chat_id "
            "FROM reminders ORDER BY fire_at"
        ).fetchall()


def get_calendar_reminders() -> list[tuple]:
    """Ritorna solo i reminder collegati a un evento calendario."""
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, message, fire_at, calendar_event_id, calendar_event_title, chat_id "
            "FROM reminders WHERE calendar_event_id IS NOT NULL ORDER BY fire_at"
        ).fetchall()


def delete_reminder(reminder_id: str) -> bool:
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return cur.rowcount > 0
