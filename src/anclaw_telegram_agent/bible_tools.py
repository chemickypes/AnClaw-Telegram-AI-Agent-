import logging

import httpx

logger = logging.getLogger(__name__)

_BIBLE_ORG_URL = "https://labs.bible.org/api/"
_TIMEOUT = 8.0


def get_verse_of_the_day() -> str:
    """
    Recupera il versetto del giorno dalla Bible.org API (testo NLT in inglese).
    Restituisce riferimento biblico e testo NLT; il BibleAgent userà la sua
    conoscenza per fornire la traduzione italiana Nuova Riveduta (NR).
    """
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(
                _BIBLE_ORG_URL,
                params={"passage": "votd", "type": "json"},
                headers={"User-Agent": "AnClaw-TelegramBot/1.0"},
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                verse = data[0]
                ref = (
                    f"{verse.get('bookname', '')} "
                    f"{verse.get('chapter', '')}:{verse.get('verse', '')}"
                ).strip()
                text = verse.get("text", "")
                return f"Riferimento: {ref}\nTesto (NLT): {text}"
            return "Nessun versetto trovato."
    except Exception:
        logger.debug("get_verse_of_the_day fallita", exc_info=True)
        return (
            "Impossibile recuperare il versetto del giorno dall'API. "
            "Proponi un versetto significativo dalla tua conoscenza."
        )
