"""
Notes store — SQLite-backed personal notes/memos.
"""
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)

_DB_PATH = "tmp/agent_data.db"


def init_notes_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def save_note(content: str) -> int:
    """Salva una nota. Ritorna l'ID inserito."""
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            (content.strip(), datetime.now(_TZ).isoformat()),
        )
        return cur.lastrowid


def get_all_notes() -> list[tuple]:
    """Ritorna lista di (id, content, created_at) ordinata per data decrescente."""
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, content, created_at FROM notes ORDER BY created_at DESC"
        ).fetchall()


def search_notes(query: str) -> list[tuple]:
    """Cerca note che contengono la query (case-insensitive)."""
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, content, created_at FROM notes "
            "WHERE LOWER(content) LIKE ? ORDER BY created_at DESC",
            (f"%{query.lower()}%",),
        ).fetchall()


def delete_note(note_id: int) -> bool:
    """Elimina una nota per ID. Ritorna True se trovata."""
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


def format_notes(notes: list[tuple]) -> str:
    """Formatta una lista di note per la risposta all'utente."""
    if not notes:
        return "Nessuna nota trovata."
    lines = []
    for note_id, content, created_at in notes:
        try:
            dt = datetime.fromisoformat(created_at).strftime("%d/%m/%Y %H:%M")
        except Exception:
            dt = created_at[:16]
        lines.append(f"[{note_id}] {content}  _(_{dt}_)_")
    return "\n".join(lines)
