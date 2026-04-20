import sqlite3
import os

_DB_PATH = os.path.join(os.path.dirname(__file__), "tmp", "agent_data.db")


def init_rss_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rss_feeds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)


def seed_feeds(feeds: list[dict]) -> None:
    """Inserisce i feed della lista statica se la tabella è vuota."""
    with sqlite3.connect(_DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM rss_feeds").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT OR IGNORE INTO rss_feeds (url, name, description) VALUES (?, ?, ?)",
                [(f["url"], f["name"], f.get("description", "")) for f in feeds],
            )


def get_all_feeds() -> list[dict]:
    with sqlite3.connect(_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, url, name, description FROM rss_feeds ORDER BY id"
        ).fetchall()
    return [{"id": r[0], "url": r[1], "name": r[2], "description": r[3]} for r in rows]


def add_feed(url: str, name: str, description: str) -> int:
    """Aggiunge un feed. Ritorna l'ID assegnato. Lancia ValueError se URL già presente."""
    with sqlite3.connect(_DB_PATH) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO rss_feeds (url, name, description) VALUES (?, ?, ?)",
                (url.strip(), name.strip(), description.strip()),
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"URL già presente: {url}")


def delete_feed(feed_id: int) -> bool:
    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM rss_feeds WHERE id = ?", (feed_id,))
        return cursor.rowcount > 0


def feed_url_exists(url: str) -> bool:
    with sqlite3.connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM rss_feeds WHERE url = ?", (url.strip(),)
        ).fetchone()
    return row is not None
