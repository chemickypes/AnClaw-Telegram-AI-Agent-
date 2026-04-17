import asyncio
import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

_PROMPT = (
    "Trascrivi fedelmente questo audio. "
    "Rispondi SOLO con il testo trascritto, senza prefissi, note o spiegazioni."
)


class AudioTranscriber:
    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """Invia l'audio a Gemini e restituisce il testo trascritto."""
        logger.info(f"Trascrizione audio — {len(audio_bytes)} byte, mime: {mime_type}")

        response = await asyncio.to_thread(
            _client.models.generate_content,
            model="gemini-2.5-flash",
            contents=[
                _PROMPT,
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            ],
        )
        text = response.text.strip()
        logger.info(f"Trascrizione completata: {text!r}")
        return text
