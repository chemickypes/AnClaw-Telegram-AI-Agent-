"""
Google Calendar tools for CalendarAgent.
Requires credentials.json and token.json in the project root.
"""
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/Rome")
_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
_CREDS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")


def _get_service():
    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def list_events(days_ahead: int = 7) -> str:
    """
    Legge gli eventi del calendario Google nei prossimi N giorni.

    Args:
        days_ahead: Quanti giorni in avanti guardare (default 7).
    """
    try:
        service = _get_service()
        now = datetime.now(_TZ)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = result.get("items", [])
        if not events:
            return f"Nessun evento nei prossimi {days_ahead} giorni."

        lines = [f"Eventi nei prossimi {days_ahead} giorni:\n"]
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))
            end = ev["end"].get("dateTime", ev["end"].get("date", ""))
            title = ev.get("summary", "(senza titolo)")
            location = ev.get("location", "")
            desc = ev.get("description", "")

            # Format datetime
            try:
                dt = datetime.fromisoformat(start).astimezone(_TZ)
                start_fmt = dt.strftime("%d/%m %H:%M")
            except Exception:
                start_fmt = start

            line = f"- {start_fmt} | {title}"
            if location:
                line += f" @ {location}"
            if desc:
                line += f"\n  {desc[:100]}"
            lines.append(line)

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Errore nel leggere gli eventi del calendario")
        return f"Errore nel leggere il calendario: {e}"


def create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    location: str = "",
) -> str:
    """
    Crea un evento nel calendario Google.

    Args:
        title: Titolo dell'evento.
        start_datetime: Data e ora di inizio in formato ISO 8601 (es. "2026-04-20T10:00:00").
        end_datetime: Data e ora di fine in formato ISO 8601 (es. "2026-04-20T11:00:00").
        description: Descrizione opzionale dell'evento.
        location: Luogo opzionale dell'evento.
    """
    try:
        service = _get_service()

        event_body = {
            "summary": title,
            "start": {"dateTime": start_datetime, "timeZone": "Europe/Rome"},
            "end": {"dateTime": end_datetime, "timeZone": "Europe/Rome"},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        event = service.events().insert(calendarId="primary", body=event_body).execute()

        link = event.get("htmlLink", "")
        start_fmt = start_datetime.replace("T", " ")[:16]
        return (
            f"Evento creato con successo!\n"
            f"Titolo: {title}\n"
            f"Inizio: {start_fmt}\n"
            f"ID: {event['id']}\n"
            f"Link: {link}"
        )

    except Exception as e:
        logger.exception("Errore nella creazione dell'evento")
        return f"Errore nella creazione dell'evento: {e}"


def delete_event(event_id: str) -> str:
    """
    Elimina un evento dal calendario Google dato il suo ID.

    Args:
        event_id: ID dell'evento Google Calendar da eliminare.
    """
    try:
        service = _get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return f"Evento `{event_id}` eliminato con successo."
    except Exception as e:
        logger.exception("Errore nell'eliminazione dell'evento")
        return f"Errore nell'eliminazione dell'evento: {e}"
