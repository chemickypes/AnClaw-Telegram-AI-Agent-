"""
Gmail tools for EmailBriefingAgent and NewsletterAgent.
Requires credentials.json and token.json with gmail.modify scope.
Run setup_google_auth.py once to regenerate the token.
"""
import base64
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/Rome")
_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
]

_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

_TRACKING_KEYWORDS = (
    "unsubscrib", "track.", "open.", "click.", "pixel", "beacon",
    "1x1", ".gif", ".png", ".jpg", ".jpeg", "mailto:",
)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TOKEN_PATH = os.path.join(_PROJECT_ROOT, "token.json")
_DB_PATH = "tmp/agent_data.db"

_MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}


def _extract_links_from_payload(payload: dict, max_links: int = 3) -> list[str]:
    """Extracts unique content URLs from a Gmail message payload, filtering tracking links."""
    links: list[str] = []

    def _recurse(part: dict) -> None:
        mime = part.get("mimeType", "")
        if mime in ("text/plain", "text/html"):
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                    links.extend(_URL_RE.findall(text[:8000]))
                except Exception:
                    pass
        for p in part.get("parts", []):
            _recurse(p)

    _recurse(payload)

    seen: set[str] = set()
    result: list[str] = []
    for url in links:
        if url in seen:
            continue
        seen.add(url)
        lower = url.lower()
        if any(kw in lower for kw in _TRACKING_KEYWORDS):
            continue
        result.append(url)
        if len(result) >= max_links:
            break
    return result


def _extract_text_from_payload(payload: dict) -> str:
    """Extracts readable body text from a Gmail message payload (plain text preferred, HTML fallback)."""
    text = ""

    def _recurse_plain(part: dict) -> None:
        nonlocal text
        if text:
            return
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                except Exception:
                    pass
        for p in part.get("parts", []):
            _recurse_plain(p)

    _recurse_plain(payload)

    if not text:
        def _recurse_html(part: dict) -> None:
            nonlocal text
            if text:
                return
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    try:
                        html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                        text = re.sub(r'<[^>]+>', ' ', html)
                        text = re.sub(r'\s+', ' ', text).strip()
                    except Exception:
                        pass
            for p in part.get("parts", []):
                _recurse_html(p)
        _recurse_html(payload)

    return text.strip()


def fetch_unread_emails(target_date: str | None = None, max_results: int = 30) -> str:
    """
    Recupera le email non lette dalla inbox per la data indicata, con mittente, oggetto, anteprima e link.

    Args:
        target_date: Data in formato italiano ('oggi', 'ieri', '10 Maggio', '10 maggio 2026')
                     o ISO ('2026-05-10'). Se omesso, usa oggi.
        max_results: Numero massimo di email da recuperare (default 30).
    """
    try:
        day = _parse_date(target_date)
    except ValueError as e:
        return f"Errore nella data: {e}"

    next_day = day + timedelta(days=1)
    query = (
        f"is:unread in:inbox "
        f"after:{day.strftime('%Y/%m/%d')} before:{next_day.strftime('%Y/%m/%d')}"
    )

    try:
        service = _get_gmail_service()
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()
    except Exception as e:
        logger.exception("Errore nell'accesso a Gmail")
        return f"Errore nell'accesso a Gmail: {e}"

    messages = result.get("messages", [])
    if not messages:
        return f"Nessuna email non letta trovata per il {day.strftime('%d/%m/%Y')}."

    logger.info("[Gmail] Trovate %d email non lette del %s da processare", len(messages), day.strftime("%d/%m/%Y"))

    emails = []
    for msg in messages:
        try:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full",
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            from_addr = headers.get("From", "(mittente sconosciuto)")
            subject = headers.get("Subject", "(senza oggetto)")
            date_str = headers.get("Date", "")
            snippet = detail.get("snippet", "")
            links = _extract_links_from_payload(detail.get("payload", {}))

            logger.info("[Gmail] Email letta — ID: %s | Da: %s | Oggetto: %s", msg["id"], from_addr, subject)

            emails.append({
                "id": msg["id"],
                "from": from_addr,
                "subject": subject,
                "date": date_str,
                "snippet": snippet[:300],
                "links": links,
            })
        except Exception:
            logger.debug("Errore nel recupero email %s", msg["id"], exc_info=True)

    if not emails:
        return f"Nessuna email non letta trovata per il {day.strftime('%d/%m/%Y')}."

    lines = [f"Email non lette del {day.strftime('%d %B %Y')} ({len(emails)}):\n"]
    for e in emails:
        entry = (
            f"--- ID: {e['id']} ---\n"
            f"Da: {e['from']}\n"
            f"Oggetto: {e['subject']}\n"
            f"Data: {e['date']}\n"
            f"Anteprima: {e['snippet']}\n"
        )
        if e["links"]:
            entry += f"Link presenti: {', '.join(e['links'])}\n"
        lines.append(entry)

    return "\n".join(lines)


def mark_emails_as_read(message_ids: list[str]) -> str:
    """
    Segna le email indicate come lette rimuovendo il label UNREAD.

    Args:
        message_ids: Lista degli ID delle email da segnare come lette.
    """
    if not message_ids:
        return "Nessun ID email fornito."
    try:
        service = _get_gmail_service()
        service.users().messages().batchModify(
            userId="me",
            body={"ids": message_ids, "removeLabelIds": ["UNREAD"]},
        ).execute()
        logger.info("[Gmail] Segnate come lette %d email: %s", len(message_ids), message_ids[:5])
        return f"Segnate come lette {len(message_ids)} email."
    except Exception as e:
        logger.exception("Errore nel segnare le email come lette")
        return f"Errore nel segnare le email come lette: {e}"


def get_email_by_id(message_id: str) -> str:
    """
    Recupera il contenuto completo di una email dato il suo ID univoco.

    Args:
        message_id: ID univoco dell'email (es. 18f2abc123).
    """
    try:
        service = _get_gmail_service()
        detail = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()
    except Exception as e:
        logger.exception("Errore nel recupero email %s", message_id)
        return f"Errore nel recupero dell'email {message_id}: {e}"

    headers = {
        h["name"]: h["value"]
        for h in detail.get("payload", {}).get("headers", [])
    }
    from_addr = headers.get("From", "(mittente sconosciuto)")
    subject = headers.get("Subject", "(senza oggetto)")
    date_str = headers.get("Date", "")

    body = _extract_text_from_payload(detail.get("payload", {}))
    links = _extract_links_from_payload(detail.get("payload", {}), max_links=10)

    logger.info("[Gmail] Email letta per dettaglio — ID: %s | Da: %s | Oggetto: %s", message_id, from_addr, subject)

    lines = [
        f"Email ID: {message_id}",
        f"Da: {from_addr}",
        f"Oggetto: {subject}",
        f"Data: {date_str}",
        f"\n--- CORPO ---\n{body[:4000]}",
    ]
    if links:
        lines.append("\n--- LINK TROVATI ---\n" + "\n".join(links))

    return "\n".join(lines)


def _init_senders_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS newsletter_senders (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE
            )
        """)


def _get_gmail_service():
    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _parse_date(date_str: str | None) -> date:
    if not date_str:
        return datetime.now(_TZ).date()
    s = date_str.strip().lower()
    if s in ("oggi", "today"):
        return datetime.now(_TZ).date()
    if s in ("ieri", "yesterday"):
        return (datetime.now(_TZ) - timedelta(days=1)).date()
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # "10 Maggio" or "10 Maggio 2026"
    parts = s.split()
    if len(parts) >= 2:
        month = _MONTHS_IT.get(parts[1])
        if month:
            year = int(parts[2]) if len(parts) > 2 else datetime.now(_TZ).year
            return date(year, month, int(parts[0]))
    raise ValueError(f"Data non riconosciuta: {date_str!r}")


def get_newsletter_senders() -> str:
    """Mostra la lista dei mittenti newsletter configurati."""
    _init_senders_table()
    with sqlite3.connect(_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name FROM newsletter_senders ORDER BY name COLLATE NOCASE"
        ).fetchall()
    if not rows:
        return "Nessun mittente newsletter configurato. Aggiungine uno con add_newsletter_sender."
    lines = [f"[{r[0]}] {r[1]}" for r in rows]
    return f"Mittenti newsletter ({len(rows)}):\n" + "\n".join(lines)


def add_newsletter_sender(name: str) -> str:
    """
    Aggiunge un mittente alla lista delle newsletter.

    Args:
        name: Nome (o parte del nome) del mittente da includere nel riassunto,
              es. 'Medium', 'Substack', 'Morning Brew'. Il matching è parziale
              e case-insensitive: 'Medium' cattura anche 'Medium Daily Digest'.
    """
    _init_senders_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute("INSERT INTO newsletter_senders (name) VALUES (?)", (name.strip(),))
        return f"Mittente aggiunto: {name.strip()}"
    except sqlite3.IntegrityError:
        return f"Mittente già presente: {name.strip()}"


def remove_newsletter_sender(sender_id: int) -> str:
    """
    Rimuove un mittente dalla lista delle newsletter dato il suo ID.

    Args:
        sender_id: ID del mittente (visibile con get_newsletter_senders).
    """
    _init_senders_table()
    with sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM newsletter_senders WHERE id = ?", (sender_id,))
    if cur.rowcount:
        return f"Mittente {sender_id} rimosso."
    return f"Mittente {sender_id} non trovato."


def list_emails(target_date: str | None = None, unread_only: bool = True) -> str:
    """
    Elenca le email ricevute per la data indicata mostrando mittente e oggetto.

    Args:
        target_date: Data in formato italiano ('oggi', 'ieri', '10 Maggio', '10 maggio 2026')
                     o ISO ('2026-05-10'). Se omesso, usa oggi.
        unread_only: Se True (default) mostra solo le non lette; False per tutte.
    """
    try:
        day = _parse_date(target_date)
    except ValueError as e:
        return f"Errore nella data: {e}"

    next_day = day + timedelta(days=1)
    query = f"after:{day.strftime('%Y/%m/%d')} before:{next_day.strftime('%Y/%m/%d')}"
    if unread_only:
        query += " is:unread"

    try:
        service = _get_gmail_service()
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=100,
        ).execute()
    except Exception as e:
        logger.exception("Errore nell'accesso a Gmail")
        return f"Errore nell'accesso a Gmail: {e}"

    messages = result.get("messages", [])
    if not messages:
        label = "non lette " if unread_only else ""
        return f"Nessuna email {label}trovata per il {day.strftime('%d/%m/%Y')}."

    emails = []
    for msg in messages[:100]:
        try:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            emails.append({
                "from": headers.get("From", "(mittente sconosciuto)"),
                "subject": headers.get("Subject", "(senza oggetto)"),
            })
        except Exception:
            logger.debug("Errore nel recupero email %s", msg["id"], exc_info=True)

    if not emails:
        return f"Nessuna email trovata per il {day.strftime('%d/%m/%Y')}."

    label = "non lette " if unread_only else ""
    lines = [f"Email {label}del {day.strftime('%d %B %Y')} ({len(emails)}):"]
    for i, e in enumerate(emails, 1):
        lines.append(f"{i}. {e['from']} — {e['subject']}")
    return "\n".join(lines)


def get_newsletter_summary(target_date: str | None = None) -> str:
    """
    Recupera le email newsletter per la data indicata e restituisce i dati per il riassunto.

    Args:
        target_date: Data in formato italiano ('oggi', 'ieri', '10 Maggio', '10 maggio 2026')
                     o ISO ('2026-05-10'). Se omesso, usa oggi.
    """
    _init_senders_table()
    try:
        day = _parse_date(target_date)
    except ValueError as e:
        return f"Errore nella data: {e}"

    with sqlite3.connect(_DB_PATH) as conn:
        senders = [
            r[0].lower()
            for r in conn.execute("SELECT name FROM newsletter_senders").fetchall()
        ]

    if not senders:
        return (
            "Lista mittenti newsletter vuota. "
            "Aggiungi mittenti con add_newsletter_sender prima di richiedere il riassunto."
        )

    next_day = day + timedelta(days=1)
    # Gmail date filter format: YYYY/MM/DD
    after = day.strftime("%Y/%m/%d")
    before = next_day.strftime("%Y/%m/%d")
    sender_terms = " OR ".join(f"from:{s}" for s in senders)
    query = f"after:{after} before:{before} ({sender_terms})"

    try:
        service = _get_gmail_service()
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=50,
        ).execute()
    except Exception as e:
        logger.exception("Errore nell'accesso a Gmail")
        return f"Errore nell'accesso a Gmail: {e}"

    messages = result.get("messages", [])
    if not messages:
        date_fmt = day.strftime("%d/%m/%Y")
        return (
            f"Nessuna email newsletter trovata per il {date_fmt}.\n"
            f"Mittenti cercati: {', '.join(senders)}"
        )

    emails = []
    for msg in messages[:30]:
        try:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            from_addr = headers.get("From", "")
            subject = headers.get("Subject", "(senza oggetto)")
            snippet = detail.get("snippet", "")

            # Python-side fuzzy filter: sender name must appear in the From field
            from_lower = from_addr.lower()
            if not any(s in from_lower for s in senders):
                continue

            emails.append({
                "from": from_addr,
                "subject": subject,
                "snippet": snippet[:400],
            })
        except Exception:
            logger.debug("Errore nel recupero email %s", msg["id"], exc_info=True)

    if not emails:
        date_fmt = day.strftime("%d/%m/%Y")
        return (
            f"Nessuna email newsletter corrispondente trovata per il {date_fmt}.\n"
            f"Mittenti configurati: {', '.join(senders)}"
        )

    date_fmt = day.strftime("%d %B %Y")
    lines = [f"Newsletter del {date_fmt} ({len(emails)} email trovate):\n"]
    for i, e in enumerate(emails, 1):
        lines.append(
            f"--- [{i}] ---\n"
            f"Da: {e['from']}\n"
            f"Oggetto: {e['subject']}\n"
            f"Anteprima: {e['snippet']}\n"
        )
    return "\n".join(lines)
