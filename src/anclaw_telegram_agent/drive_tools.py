"""
Google Drive tools for DriveAgent.
Requires credentials.json and token.json (with Drive scope) in the project root.
Run setup_google_auth.py once to generate the token.
"""
import io
import logging
import mimetypes
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TOKEN_PATH = os.path.join(_PROJECT_ROOT, "token.json")
_CREDS_PATH = os.path.join(_PROJECT_ROOT, "credentials.json")

_DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "tmp", "drive_downloads")

# Google Workspace MIME types → export format
_GDOC_EXPORT = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}


def _get_service():
    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def search_files(query: str, max_results: int = 10) -> str:
    """
    Cerca file su Google Drive per nome o contenuto.

    Args:
        query: Testo da cercare (nome file, parola chiave). Supporta operatori Drive
               come 'name contains "report"' o 'fullText contains "budget"'.
        max_results: Numero massimo di risultati (default 10).
    """
    try:
        service = _get_service()

        # Wrap bare query as name search if no Drive operator detected
        if " contains " not in query and " = " not in query and " in " not in query:
            q = f'name contains "{query}" and trashed = false'
        else:
            q = query + (" and trashed = false" if "trashed" not in query else "")

        result = service.files().list(
            q=q,
            pageSize=max_results,
            fields="files(id, name, mimeType, size, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return f"Nessun file trovato per: \"{query}\"."

        lines = [f"File trovati ({len(files)}):\n"]
        for f in files:
            size = f.get("size", "")
            size_str = f" ({int(size) // 1024} KB)" if size else ""
            modified = f.get("modifiedTime", "")[:10]
            lines.append(
                f"• [{f['name']}]{size_str} — modificato {modified}\n"
                f"  ID: {f['id']}\n"
                f"  Tipo: {f['mimeType']}\n"
                f"  Link: {f.get('webViewLink', 'N/A')}"
            )
        return "\n\n".join(lines)

    except Exception as e:
        logger.exception("Errore nella ricerca su Drive")
        return f"Errore nella ricerca su Drive: {e}"


def read_file_content(file_id: str) -> str:
    """
    Legge il contenuto testuale di un file su Google Drive.
    Funziona con Google Docs, Fogli, file di testo e PDF.

    Args:
        file_id: ID del file Drive (visibile con search_files).
    """
    try:
        service = _get_service()

        meta = service.files().get(
            fileId=file_id, fields="name, mimeType"
        ).execute()
        name = meta.get("name", "senza nome")
        mime = meta.get("mimeType", "")

        if mime in _GDOC_EXPORT:
            export_mime, _ = _GDOC_EXPORT[mime]
            data = service.files().export(fileId=file_id, mimeType=export_mime).execute()
            text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        else:
            req = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            raw = buf.getvalue()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return f"Il file \"{name}\" è binario e non può essere letto come testo. Usa download_file per scaricarlo."

        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[...troncato a {max_chars} caratteri su {len(text)} totali]"

        return f"Contenuto di \"{name}\":\n\n{text}"

    except Exception as e:
        logger.exception("Errore nella lettura del file Drive")
        return f"Errore nella lettura del file: {e}"


def download_file(file_id: str, filename: str = "") -> str:
    """
    Scarica un file da Google Drive e lo invia come allegato su Telegram.

    Args:
        file_id: ID del file Drive.
        filename: Nome file opzionale (se omesso usa il nome originale su Drive).
    """
    try:
        service = _get_service()
        os.makedirs(_DOWNLOADS_DIR, exist_ok=True)

        meta = service.files().get(
            fileId=file_id, fields="name, mimeType"
        ).execute()
        original_name = meta.get("name", "file")
        mime = meta.get("mimeType", "application/octet-stream")

        dest_filename = filename.strip() if filename.strip() else original_name

        if mime in _GDOC_EXPORT:
            export_mime, ext = _GDOC_EXPORT[mime]
            data = service.files().export(fileId=file_id, mimeType=export_mime).execute()
            if not dest_filename.endswith(ext):
                dest_filename += ext
            mime = export_mime
        else:
            req = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buf.getvalue()

        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in dest_filename)
        dest_path = os.path.join(_DOWNLOADS_DIR, f"{file_id[:8]}_{safe_name}")

        with open(dest_path, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))

        logger.info(f"File Drive scaricato: {dest_path}")
        return (
            f"File \"{original_name}\" scaricato con successo.\n"
            f"[DRIVE_DOWNLOAD: {dest_path} | {dest_filename} | {mime}]"
        )

    except Exception as e:
        logger.exception("Errore nel download del file Drive")
        return f"Errore nel download: {e}"


def create_text_file(name: str, content: str, folder_id: str = "") -> str:
    """
    Crea un file di testo su Google Drive (nella cartella principale o in una specifica).

    Args:
        name: Nome del file da creare (es. "note.txt" o "report.md").
        content: Contenuto testuale del file.
        folder_id: ID della cartella Drive di destinazione (vuoto = root).
    """
    try:
        service = _get_service()

        metadata: dict = {"name": name}
        if folder_id.strip():
            metadata["parents"] = [folder_id.strip()]

        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/plain",
            resumable=False,
        )

        file = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        return (
            f"File creato su Drive!\n"
            f"Nome: {file['name']}\n"
            f"ID: {file['id']}\n"
            f"Link: {file.get('webViewLink', 'N/A')}"
        )

    except Exception as e:
        logger.exception("Errore nella creazione del file Drive")
        return f"Errore nella creazione del file: {e}"


def upload_file(file_path: str, filename: str = "", folder_id: str = "") -> str:
    """
    Carica un file locale su Google Drive.
    Usa questa funzione quando l'utente ha inviato un file su Telegram
    e vuole caricarlo su Drive. Il path del file si trova nel messaggio
    come [FILE SALVATO: path].

    Args:
        file_path: Percorso locale del file da caricare.
        filename: Nome da usare su Drive (vuoto = nome originale del file).
        folder_id: ID della cartella Drive di destinazione (vuoto = root).
    """
    try:
        if not os.path.isfile(file_path):
            return f"File non trovato sul disco: {file_path}"

        service = _get_service()

        dest_name = filename.strip() if filename.strip() else os.path.basename(file_path)
        mime, _ = mimetypes.guess_type(file_path)
        mime = mime or "application/octet-stream"

        metadata: dict = {"name": dest_name}
        if folder_id.strip():
            metadata["parents"] = [folder_id.strip()]

        media = MediaFileUpload(file_path, mimetype=mime, resumable=True)

        file = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, size, webViewLink",
        ).execute()

        size_kb = int(file.get("size", 0)) // 1024
        return (
            f"File caricato su Drive!\n"
            f"Nome: {file['name']}\n"
            f"Dimensione: {size_kb} KB\n"
            f"ID: {file['id']}\n"
            f"Link: {file.get('webViewLink', 'N/A')}"
        )

    except Exception as e:
        logger.exception("Errore nell'upload del file su Drive")
        return f"Errore nell'upload: {e}"


