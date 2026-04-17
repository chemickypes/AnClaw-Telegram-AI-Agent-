"""
TelegramSender — invia messaggi e file in modo proattivo (senza aspettare un update).

Utilizzo tipico:
    sender = TelegramSender(config)

    # Testo
    await sender.send_message(chat_id, "Ciao!")

    # File da bytes
    with open("report.pdf", "rb") as f:
        await sender.send_file(chat_id, f.read(), filename="report.pdf")

    # File da path
    await sender.send_file_from_path(chat_id, "/tmp/grafico.png", caption="Ecco il grafico")
"""

import mimetypes
import os
from io import BytesIO

from telegram import Bot
from telegram.constants import ParseMode

from config import Config

# MIME types che vengono inviati come foto (rendering inline in chat)
_PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class TelegramSender:
    def __init__(self, config: Config):
        self._bot = Bot(token=config.telegram_token)

    async def send_message(self, chat_id: int | str, text: str, markdown: bool = True) -> None:
        """Invia un messaggio di testo."""
        parse_mode = ParseMode.MARKDOWN if markdown else None
        async with self._bot:
            await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
            )

    async def send_file(
        self,
        chat_id: int | str,
        file_bytes: bytes,
        filename: str,
        caption: str | None = None,
    ) -> None:
        """
        Invia bytes come file Telegram.
        Usa send_photo per immagini, send_document per tutto il resto.
        """
        mime_type, _ = mimetypes.guess_type(filename)
        buf = BytesIO(file_bytes)
        buf.name = filename

        async with self._bot:
            if mime_type in _PHOTO_MIMES:
                await self._bot.send_photo(
                    chat_id=chat_id,
                    photo=buf,
                    caption=caption,
                )
            else:
                await self._bot.send_document(
                    chat_id=chat_id,
                    document=buf,
                    filename=filename,
                    caption=caption,
                )

    async def send_file_from_path(
        self,
        chat_id: int | str,
        file_path: str,
        caption: str | None = None,
        filename: str | None = None,
    ) -> None:
        """
        Legge un file dal disco e lo invia.
        Il filename visualizzato in chat è il nome del file se non specificato.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File non trovato: {file_path}")

        name = filename or os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        await self.send_file(chat_id, file_bytes, filename=name, caption=caption)
