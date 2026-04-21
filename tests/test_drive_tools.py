"""
Test unitari per drive_tools e per il parser _extract_drive_downloads in agent.py.
Tutte le chiamate all'API Google Drive sono mockate.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from anclaw_telegram_agent import drive_tools
from anclaw_telegram_agent.agent import _extract_drive_downloads


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_service():
    return MagicMock()


# ── search_files ──────────────────────────────────────────────────────────────

def test_search_files_returns_results():
    svc = _mock_service()
    svc.files().list().execute.return_value = {
        "files": [
            {
                "id": "abc123",
                "name": "Budget 2026.xlsx",
                "mimeType": "application/vnd.ms-excel",
                "size": "51200",
                "modifiedTime": "2026-03-01T10:00:00Z",
                "webViewLink": "https://drive.google.com/file/abc123",
            }
        ]
    }

    with patch.object(drive_tools, "_get_service", return_value=svc):
        result = drive_tools.search_files("budget")

    assert "Budget 2026.xlsx" in result
    assert "abc123" in result
    assert "50 KB" in result


def test_search_files_no_results():
    svc = _mock_service()
    svc.files().list().execute.return_value = {"files": []}

    with patch.object(drive_tools, "_get_service", return_value=svc):
        result = drive_tools.search_files("xyz_inesistente")

    assert "Nessun file trovato" in result


def test_search_files_wraps_bare_query():
    """Query senza operatori Drive viene wrappata come name contains."""
    svc = _mock_service()
    svc.files().list().execute.return_value = {"files": []}

    with patch.object(drive_tools, "_get_service", return_value=svc):
        drive_tools.search_files("report")

    call_kwargs = svc.files().list.call_args.kwargs
    assert 'name contains "report"' in call_kwargs["q"]
    assert "trashed = false" in call_kwargs["q"]


def test_search_files_passes_drive_operator_as_is():
    """Query con operatore Drive non viene modificata."""
    svc = _mock_service()
    svc.files().list().execute.return_value = {"files": []}

    with patch.object(drive_tools, "_get_service", return_value=svc):
        drive_tools.search_files('fullText contains "budget"')

    call_kwargs = svc.files().list.call_args.kwargs
    assert 'fullText contains "budget"' in call_kwargs["q"]


def test_search_files_api_error():
    svc = _mock_service()
    svc.files().list().execute.side_effect = Exception("network error")

    with patch.object(drive_tools, "_get_service", return_value=svc):
        result = drive_tools.search_files("test")

    assert "Errore" in result


# ── read_file_content ─────────────────────────────────────────────────────────

def test_read_file_content_plain_text():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "note.txt",
        "mimeType": "text/plain",
    }
    svc.files().get_media().execute.return_value = None

    import io
    from unittest.mock import patch as _patch
    fake_buf_content = b"Contenuto del file di testo."

    with patch.object(drive_tools, "_get_service", return_value=svc):
        with _patch("anclaw_telegram_agent.drive_tools.MediaIoBaseDownload") as mock_dl:
            instance = mock_dl.return_value
            instance.next_chunk.return_value = (None, True)

            import io as _io
            real_buf = _io.BytesIO(fake_buf_content)

            def fake_init(buf, req):
                buf.write(fake_buf_content)
                return instance

            mock_dl.side_effect = fake_init
            result = drive_tools.read_file_content("file123")

    assert "note.txt" in result
    assert "Contenuto del file di testo." in result


def test_read_file_content_gdoc_export():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "Documento.gdoc",
        "mimeType": "application/vnd.google-apps.document",
    }
    svc.files().export().execute.return_value = b"Testo del documento Google."

    with patch.object(drive_tools, "_get_service", return_value=svc):
        result = drive_tools.read_file_content("gdoc123")

    assert "Documento.gdoc" in result
    assert "Testo del documento Google." in result


def test_read_file_content_truncates_long_text():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "lungo.gdoc",
        "mimeType": "application/vnd.google-apps.document",
    }
    long_text = "A" * 10000
    svc.files().export().execute.return_value = long_text.encode()

    with patch.object(drive_tools, "_get_service", return_value=svc):
        result = drive_tools.read_file_content("long123")

    assert "troncato" in result
    assert len(result) < 10000 + 500


def test_read_file_content_binary_file():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "foto.jpg",
        "mimeType": "image/jpeg",
    }

    with patch.object(drive_tools, "_get_service", return_value=svc):
        with patch("anclaw_telegram_agent.drive_tools.MediaIoBaseDownload") as mock_dl:
            instance = mock_dl.return_value
            instance.next_chunk.return_value = (None, True)

            def fake_init(buf, req):
                buf.write(b"\xff\xd8\xff\xe0")  # JPEG magic bytes
                return instance

            mock_dl.side_effect = fake_init
            result = drive_tools.read_file_content("img123")

    assert "binario" in result.lower() or "download_file" in result


# ── download_file ─────────────────────────────────────────────────────────────

def test_download_file_creates_file_and_returns_marker():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "report.pdf",
        "mimeType": "application/pdf",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(drive_tools, "_get_service", return_value=svc):
            with patch.object(drive_tools, "_DOWNLOADS_DIR", tmpdir):
                with patch("anclaw_telegram_agent.drive_tools.MediaIoBaseDownload") as mock_dl:
                    instance = mock_dl.return_value
                    instance.next_chunk.return_value = (None, True)

                    def fake_init(buf, req):
                        buf.write(b"%PDF-content")
                        return instance

                    mock_dl.side_effect = fake_init
                    result = drive_tools.download_file("file456")

    assert "[DRIVE_DOWNLOAD:" in result
    assert "report.pdf" in result
    assert "application/pdf" in result


def test_download_file_gdoc_export():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "Foglio",
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    svc.files().export().execute.return_value = b"col1,col2\n1,2"

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(drive_tools, "_get_service", return_value=svc):
            with patch.object(drive_tools, "_DOWNLOADS_DIR", tmpdir):
                result = drive_tools.download_file("sheet789")

    assert "[DRIVE_DOWNLOAD:" in result
    assert ".csv" in result


def test_download_file_custom_filename():
    svc = _mock_service()
    svc.files().get().execute.return_value = {
        "name": "original.txt",
        "mimeType": "text/plain",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(drive_tools, "_get_service", return_value=svc):
            with patch.object(drive_tools, "_DOWNLOADS_DIR", tmpdir):
                with patch("anclaw_telegram_agent.drive_tools.MediaIoBaseDownload") as mock_dl:
                    instance = mock_dl.return_value
                    instance.next_chunk.return_value = (None, True)

                    def fake_init(buf, req):
                        buf.write(b"hello")
                        return instance

                    mock_dl.side_effect = fake_init
                    result = drive_tools.download_file("file000", filename="custom.txt")

    assert "custom.txt" in result


# ── create_text_file ──────────────────────────────────────────────────────────

def test_create_text_file_success():
    svc = _mock_service()
    svc.files().create().execute.return_value = {
        "id": "newfile123",
        "name": "note.txt",
        "webViewLink": "https://drive.google.com/file/newfile123",
    }

    with patch.object(drive_tools, "_get_service", return_value=svc):
        with patch("anclaw_telegram_agent.drive_tools.MediaIoBaseUpload"):
            result = drive_tools.create_text_file("note.txt", "Contenuto della nota")

    assert "newfile123" in result
    assert "note.txt" in result
    assert "drive.google.com" in result


def test_create_text_file_with_folder():
    svc = _mock_service()
    svc.files().create().execute.return_value = {
        "id": "f1",
        "name": "doc.txt",
        "webViewLink": "https://drive.google.com/file/f1",
    }

    with patch.object(drive_tools, "_get_service", return_value=svc):
        with patch("anclaw_telegram_agent.drive_tools.MediaIoBaseUpload"):
            drive_tools.create_text_file("doc.txt", "testo", folder_id="folder999")

    body = svc.files().create.call_args.kwargs["body"]
    assert body.get("parents") == ["folder999"]


# ── upload_file ───────────────────────────────────────────────────────────────

def test_upload_file_missing_path():
    result = drive_tools.upload_file("/tmp/non_esiste_davvero_xyz.pdf")
    assert "non trovato" in result.lower()


def test_upload_file_success():
    svc = _mock_service()
    svc.files().create().execute.return_value = {
        "id": "uploaded1",
        "name": "foto.jpg",
        "size": "102400",
        "webViewLink": "https://drive.google.com/file/uploaded1",
    }

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"\xff\xd8\xff\xe0" * 100)
        tmp_path = f.name

    try:
        with patch.object(drive_tools, "_get_service", return_value=svc):
            with patch("anclaw_telegram_agent.drive_tools.MediaFileUpload"):
                result = drive_tools.upload_file(tmp_path, filename="foto.jpg")
    finally:
        os.unlink(tmp_path)

    assert "uploaded1" in result
    assert "drive.google.com" in result


def test_upload_file_uses_original_name_if_no_filename():
    svc = _mock_service()
    svc.files().create().execute.return_value = {
        "id": "u2", "name": "myfile.pdf", "size": "1024",
        "webViewLink": "https://drive.google.com/file/u2",
    }

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"pdf content")
        tmp_path = f.name

    try:
        with patch.object(drive_tools, "_get_service", return_value=svc):
            with patch("anclaw_telegram_agent.drive_tools.MediaFileUpload"):
                drive_tools.upload_file(tmp_path)

        body = svc.files().create.call_args.kwargs["body"]
        assert body["name"] == os.path.basename(tmp_path)
    finally:
        os.unlink(tmp_path)


# ── _extract_drive_downloads ──────────────────────────────────────────────────

def test_extract_drive_downloads_finds_marker():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"fake pdf")
        tmp_path = f.name

    try:
        text = (
            f"File scaricato.\n"
            f"[DRIVE_DOWNLOAD: {tmp_path} | report.pdf | application/pdf]\n"
            f"Puoi aprirlo."
        )
        files = _extract_drive_downloads(text)
        assert len(files) == 1
        assert files[0].filename == "report.pdf"
        assert files[0].mime_type == "application/pdf"
    finally:
        os.unlink(tmp_path)


def test_extract_drive_downloads_no_marker():
    files = _extract_drive_downloads("Risposta normale senza marker.")
    assert files == []


def test_extract_drive_downloads_missing_file_skipped():
    text = "[DRIVE_DOWNLOAD: /tmp/non_esiste_xyz.pdf | file.pdf | application/pdf]"
    files = _extract_drive_downloads(text)
    assert files == []


def test_extract_drive_downloads_multiple():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f1:
        f1.write(b"a")
        p1 = f1.name
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f2:
        f2.write(b"b")
        p2 = f2.name

    try:
        text = (
            f"[DRIVE_DOWNLOAD: {p1} | doc.txt | text/plain]\n"
            f"[DRIVE_DOWNLOAD: {p2} | data.csv | text/csv]"
        )
        files = _extract_drive_downloads(text)
        assert len(files) == 2
        names = [f.filename for f in files]
        assert "doc.txt" in names
        assert "data.csv" in names
    finally:
        os.unlink(p1)
        os.unlink(p2)
