"""
Persistent user facts store — SQLite-backed long-term memory.
"""
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)

_DB_PATH = "tmp/agent_data.db"


def init_memory_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                fact       TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL
            )
        """)


def save_fact(fact: str, source: str = "auto") -> int:
    """Salva un fatto. Ritorna l'ID inserito."""
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO user_facts (fact, source, created_at) VALUES (?, ?, ?)",
            (fact.strip(), source, datetime.now(_TZ).isoformat()),
        )
        return cur.lastrowid


def get_all_facts() -> list[tuple]:
    """Ritorna lista di (id, fact, source, created_at) ordinata per data."""
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, fact, source, created_at FROM user_facts ORDER BY created_at"
        ).fetchall()


def delete_fact(fact_id: int) -> bool:
    """Elimina un fatto per ID. Ritorna True se trovato."""
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
        return cur.rowcount > 0


def fact_exists(fact: str) -> bool:
    """Controlla se un fatto simile esiste già (match esatto)."""
    with sqlite3.connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM user_facts WHERE fact = ?", (fact.strip(),)
        ).fetchone()
        return row is not None


def get_facts_text() -> str:
    """Formatta i fatti per l'iniezione nelle istruzioni dell'agente."""
    facts = get_all_facts()
    if not facts:
        return ""
    lines = ["Fatti noti su Angelo:"] + [f"- {f[1]}" for f in facts]
    return "\n".join(lines)
