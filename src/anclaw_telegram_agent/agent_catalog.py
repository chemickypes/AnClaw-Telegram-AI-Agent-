import asyncio
import json
import zoneinfo
from collections.abc import Callable
from datetime import datetime
from typing import Optional

from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools import Toolkit
from agno.tools.crawl4ai import Crawl4aiTools
from agno.tools.file_generation import FileGenerationTools
from agno.tools.hackernews import HackerNewsTools
from agno.tools.webbrowser import WebBrowserTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.youtube import YouTubeTools
from agno.utils.log import log_debug

from . import memory_store
from . import notes_store
from . import rss_store
from .agent_models import AgentSpec

_SEARCH_TIMEOUT = 15  # seconds per individual DDGS search call


class AsyncWebSearchTools(Toolkit):
    """WebSearchTools con entrypoint async: agno lo awaita senza bloccare l'event loop."""

    def __init__(
        self,
        enable_news: bool = True,
        backend: str = "duckduckgo",
        timeout: int = 10,
        fixed_max_results: Optional[int] = None,
        timelimit: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self._backend = backend
        self._http_timeout = timeout
        self._fixed_max_results = fixed_max_results
        self._timelimit = timelimit
        self._region = region
        tools = [self.web_search]
        if enable_news:
            tools.append(self.search_news)
        super().__init__(name="websearch", tools=tools)

    def _ddgs_text(self, query: str, max_results: int) -> str:
        from ddgs import DDGS
        kwargs: dict = {"query": query, "max_results": max_results, "backend": self._backend}
        if self._timelimit:
            kwargs["timelimit"] = self._timelimit
        if self._region:
            kwargs["region"] = self._region
        with DDGS(timeout=self._http_timeout) as ddgs:
            return json.dumps(ddgs.text(**kwargs), indent=2)

    def _ddgs_news(self, query: str, max_results: int) -> str:
        from ddgs import DDGS
        kwargs: dict = {"query": query, "max_results": max_results}
        if self._timelimit:
            kwargs["timelimit"] = self._timelimit
        if self._region:
            kwargs["region"] = self._region
        with DDGS(timeout=self._http_timeout) as ddgs:
            return json.dumps(ddgs.news(**kwargs), indent=2)

    async def web_search(self, query: str, max_results: int = 5) -> str:
        """Use this function to search the web for a query.

        Args:
            query(str): The query to search for.
            max_results (optional, default=5): The maximum number of results to return.

        Returns:
            The search results from the web.
        """
        n = self._fixed_max_results or max_results
        log_debug(f"Searching web for: {query} using backend: {self._backend}")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._ddgs_text, query, n),
                timeout=_SEARCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return json.dumps({"error": f"Search timed out after {_SEARCH_TIMEOUT}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def search_news(self, query: str, max_results: int = 5) -> str:
        """Use this function to get the latest news from the web.

        Args:
            query(str): The query to search for.
            max_results (optional, default=5): The maximum number of results to return.

        Returns:
            The latest news from the web.
        """
        n = self._fixed_max_results or max_results
        log_debug(f"Searching web news for: {query} using backend: {self._backend}")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._ddgs_news, query, n),
                timeout=_SEARCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return json.dumps({"error": f"News search timed out after {_SEARCH_TIMEOUT}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})


_TZ = zoneinfo.ZoneInfo("Europe/Rome")
_CUTOFF = "agosto 2025"

_TOOL_LABELS: dict[str, str] = {
    "web_search": "ricerca web",
    "search_news": "ricerca notizie",
    "duckduckgo_search": "ricerca web",
    "get_top_hackernews_stories": "Hacker News",
    "get_hackernews_story": "Hacker News",
    "search_hackernews": "Hacker News",
    "web_browser": "apertura pagina web",
    "crawl4ai": "scraping pagina web",
    "crawl_url": "scraping pagina web",
    "scrape_url": "scraping pagina web",
    "get_youtube_video_data": "YouTube",
    "search_youtube_videos": "YouTube",
    "get_youtube_video_captions": "YouTube",
    "search_wikipedia": "ricerca Wikipedia",
    "get_wikipedia_article": "lettura Wikipedia",
    "generate_file": "generazione file",
    "execute_math": "calcolo matematico",
    "search_in_file": "analisi file",
    "filter_file_rows": "filtraggio dati",
    "create_schedule": "creazione sveglia",
    "list_schedules": "lista sveglie",
    "delete_schedule": "eliminazione sveglia",
    "refresh_schedule": "aggiornamento sveglia",
    "create_reminder": "creazione promemoria",
    "create_calendar_reminder": "creazione promemoria calendario",
    "list_reminders": "lista promemoria",
    "delete_reminder": "eliminazione promemoria",
    "list_events": "lettura calendario",
    "create_event": "creazione evento calendario",
    "delete_event": "eliminazione evento calendario",
    "get_weather_forecast": "previsioni meteo",
    "get_verse_of_the_day": "versetto del giorno",
    "search_files": "ricerca su Drive",
    "read_file_content": "lettura file Drive",
    "download_file": "download da Drive",
    "create_text_file": "creazione file su Drive",
    "upload_file": "upload su Drive",
    "save_note": "salvataggio nota",
    "list_notes": "lettura note",
    "search_notes": "ricerca nelle note",
    "delete_note": "eliminazione nota",
    "delegate_task_to_member": "delega al team",
    "transfer_task_to_member": "delega al team",
}


def _base_instructions() -> str:
    now = datetime.now(_TZ)
    date_str = now.strftime("%d %B %Y, %H:%M %Z")
    return (
        "Sei un assistente AI personale di Angelo Moroni. "
        "Adatta sempre le risposte al contesto della richiesta e sii proattivo nel suggerire fonti utili. "
        f"Data e ora attuale: {date_str}. "
        f"Il tuo knowledge cutoff è {_CUTOFF}: per eventi o informazioni successive a tale data "
        "usa i tool di ricerca disponibili, oppure dichiara esplicitamente che potresti non essere aggiornato."
    )


def _make_search_agent() -> Agent:
    return Agent(
        name="SearchAgent",
        role=(
            "Esegue ricerche web e su Hacker News su entità specifiche (persone, aziende, eventi). "
            "Restituisce una lista di URL rilevanti con titoli e snippet per ogni risultato trovato."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Il tuo unico compito è cercare informazioni e restituire URL + snippet rilevanti. "
            "Usa web_search per ricerche generali, "
            "HackerNews per notizie tech. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati, sono necessari per il passo successivo."
        ),
        tools=[AsyncWebSearchTools(enable_news=False), HackerNewsTools()],
        debug_mode=True,
        debug_level=2,
    )


def _make_news_search_agent() -> Agent:
    return Agent(
        name="NewsSearchAgent",
        role=(
            "Cerca le ultime notizie su un topic o categoria generica via web e Hacker News. "
            "Restituisce URL recenti con titoli e snippet."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Il tuo unico compito è cercare notizie recenti e restituire URL + snippet. "
            "Usa search_news per risultati recenti, "
            "HackerNews per notizie tech. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati."
        ),
        tools=[AsyncWebSearchTools(enable_news=True, timelimit="w"), HackerNewsTools()],
        debug_mode=True,
        debug_level=2,
    )


def _make_wikipedia_agent() -> Agent:
    return Agent(
        name="WikipediaAgent",
        role="Ricerca su Wikipedia articoli e informazioni enciclopediche rilevanti per la query.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Cerca su Wikipedia le informazioni più rilevanti per la query ricevuta. "
            "Restituisci titoli degli articoli trovati, un breve estratto e l'URL della pagina Wikipedia. "
            "NON aprire altri link: limitati a Wikipedia."
        ),
        tools=[WikipediaTools()],
        debug_mode=True,
        debug_level=2,
    )


def _make_rss_agent(feed_url: str, feed_name: str, feed_description: str) -> Agent:
    from .rss_tools import make_rss_fetch_tool
    tool = make_rss_fetch_tool(feed_url, feed_name)
    return Agent(
        name=f"RSSAgent_{feed_name}",
        role=f"Agente RSS per '{feed_name}': {feed_description}",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + f" Sei l'agente RSS per il feed '{feed_name}'. "
            "Usa il tool disponibile per leggere gli ultimi articoli. "
            "Restituisci titoli, URL e sommari degli articoli trovati senza riassumere: "
            "dati grezzi completi per permettere al coordinatore di valutare la rilevanza."
        ),
        tools=[tool],
        debug_mode=True,
        debug_level=2,
    )


def _make_scraper_agent() -> Agent:
    return Agent(
        name="ScraperAgent",
        role=(
            "Apre e analizza pagine web dagli URL forniti, estraendo il contenuto testuale completo. "
            "Usa il browser per pagine dinamiche e Crawl4AI per pagine statiche."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Ricevi una lista di URL e aprili per estrarne il contenuto. "
            "Visita i top 3 URL più rilevanti. "
            "Prova prima con WebBrowserTools; se fallisce o il contenuto è scarso, usa Crawl4aiTools. "
            "Estrai tutto il testo utile: non riassumere, riporta il contenuto grezzo completo "
            "così che il sintetizzatore possa lavorare con dati accurati."
        ),
        tools=[WebBrowserTools(), Crawl4aiTools(max_length=None)],
        debug_mode=True,
        debug_level=2,
    )


def _make_youtube_agent() -> Agent:
    return Agent(
        name="YouTubeAgent",
        role="Specializzato nell'analisi e ricerca di video e canali YouTube",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Cerca video YouTube, analizza trascrizioni, riassumi contenuti "
            "di canali e video."
        ),
        tools=[YouTubeTools()],
        debug_mode=True,
        debug_level=2,
    )


def _make_file_agent() -> Agent:
    return Agent(
        name="FileAgent",
        role="Specializzato nella generazione e creazione di file e documenti",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Crea file e documenti su richiesta: genera PDF, CSV, testo e altri formati."
        ),
        tools=[FileGenerationTools(output_directory="tmp")],
        debug_mode=True,
        debug_level=2,
    )


def _make_code_agent() -> Agent:
    from .code_tools import execute_math, search_in_file, filter_file_rows
    return Agent(
        name="CodeAgent",
        role=(
            "Esegue operazioni matematiche/statistiche e analisi su file CSV/Excel "
            "in un ambiente Python ristretto e sicuro."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Sei l'agente di esecuzione codice di AnClaw.\n\n"
            "Usa execute_math per calcoli matematici e statistici: scrivi codice Python "
            "che assegna il risultato alla variabile 'result'.\n"
            "Usa search_in_file per cercare righe in un file CSV/Excel dato il path "
            "indicato nel messaggio (es. [FILE SALVATO: path]).\n"
            "Usa filter_file_rows per filtrare righe con una condizione Python: "
            "il codice riceve 'rows' (lista di dict) e deve scrivere in 'result'.\n\n"
            "Moduli math e statistics sono disponibili nel codice ristretto.\n"
            "Presenta sempre il risultato in modo chiaro e comprensibile."
        ),
        tools=[execute_math, search_in_file, filter_file_rows],
        debug_mode=True,
        debug_level=2,
    )


def _make_calendar_agent() -> Agent:
    from .calendar_tools import list_events, create_event, delete_event
    return Agent(
        name="CalendarAgent",
        role="Gestione del calendario Google: legge eventi futuri, crea nuovi eventi, elimina eventi.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Sei l'agente del calendario di AnClaw. "
            "Usa list_events per leggere gli appuntamenti futuri, "
            "create_event per aggiungere nuovi eventi (ricava data e ora precisa dalla richiesta), "
            "delete_event per eliminare un evento dato il suo ID. "
            "Quando crei un evento, ricava start e end datetime in formato ISO 8601 (es. '2026-04-20T10:00:00'). "
            "Se l'ora di fine non è specificata, usa 1 ora dopo l'inizio come default. "
            "Conferma sempre all'utente l'azione eseguita con titolo, data e ora."
        ),
        tools=[list_events, create_event, delete_event],
        debug_mode=True,
        debug_level=2,
    )


def _make_notes_agent() -> Agent:
    def save_note(content: str) -> str:
        """
        Salva una nuova nota/appunto.

        Args:
            content: Testo della nota da salvare.
        """
        note_id = notes_store.save_note(content)
        return f"Nota salvata (ID: {note_id}): {content}"

    def list_notes() -> str:
        """Mostra tutte le note salvate, dalla più recente."""
        notes = notes_store.get_all_notes()
        return notes_store.format_notes(notes)

    def search_notes(query: str) -> str:
        """
        Cerca nelle note quelle che contengono una parola o frase.

        Args:
            query: Parola o frase da cercare nelle note.
        """
        notes = notes_store.search_notes(query)
        if not notes:
            return f"Nessuna nota trovata per: \"{query}\"."
        return f"Note trovate per \"{query}\":\n" + notes_store.format_notes(notes)

    def delete_note(note_id: int) -> str:
        """
        Elimina una nota dato il suo ID numerico.

        Args:
            note_id: ID della nota da eliminare (intero).
        """
        if notes_store.delete_note(note_id):
            return f"Nota {note_id} eliminata."
        return f"Nota {note_id} non trovata."

    return Agent(
        name="NotesAgent",
        role="Gestione appunti personali: salva, mostra, cerca ed elimina note.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Sei l'agente degli appunti di AnClaw. "
            "Usa save_note per salvare una nota, list_notes per mostrare tutte le note, "
            "search_notes per cercare nelle note, delete_note per eliminarne una per ID. "
            "Quando mostri le note, presentale in modo leggibile. "
            "Conferma sempre l'azione eseguita."
        ),
        tools=[save_note, list_notes, search_notes, delete_note],
        debug_mode=True,
        debug_level=2,
    )


def _make_rss_feeds_agent() -> Agent:
    def add_rss_feed(url: str, name: str, description: str) -> str:
        """
        Aggiunge un nuovo feed RSS alla lista.

        Args:
            url: URL del feed RSS (es. https://example.com/feed.rss).
            name: Nome identificativo breve senza spazi (es. TechCrunch_Tech).
            description: Breve descrizione del contenuto del feed.
        """
        try:
            feed_id = rss_store.add_feed(url, name, description)
            return f"Feed aggiunto (ID: {feed_id}): [{name}] {url}"
        except ValueError as e:
            return f"Errore: {e}"

    def list_rss_feeds() -> str:
        """Mostra tutti i feed RSS salvati con ID, nome e descrizione."""
        feeds = rss_store.get_all_feeds()
        if not feeds:
            return "Nessun feed RSS salvato."
        lines = [f"ID {f['id']} — [{f['name']}] {f['url']}\n  {f['description']}" for f in feeds]
        return f"Feed RSS salvati ({len(feeds)}):\n\n" + "\n\n".join(lines)

    def delete_rss_feed(feed_id: int) -> str:
        """
        Elimina un feed RSS dato il suo ID.

        Args:
            feed_id: ID numerico del feed da eliminare (visibile con list_rss_feeds).
        """
        if rss_store.delete_feed(feed_id):
            return f"Feed {feed_id} eliminato."
        return f"Feed {feed_id} non trovato."

    return Agent(
        name="RSSFeedsAgent",
        role="Gestione feed RSS: aggiunge, mostra ed elimina i feed della lista.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Sei l'agente di gestione dei feed RSS di AnClaw. "
            "Usa add_rss_feed per aggiungere un nuovo feed (chiedi URL, nome e descrizione se mancanti), "
            "list_rss_feeds per mostrare tutti i feed salvati, "
            "delete_rss_feed per eliminarne uno dato l'ID. "
            "Il nome deve essere un identificativo breve senza spazi, idealmente 'Fonte_Categoria' "
            "(es. ANSA_Sport, TechCrunch_AI). "
            "Conferma sempre l'azione eseguita."
        ),
        tools=[add_rss_feed, list_rss_feeds, delete_rss_feed],
        debug_mode=True,
        debug_level=2,
    )


def _make_reminder_agent(scheduler, get_chat_id) -> Agent:
    from .scheduler import make_reminder_tools
    tools = make_reminder_tools(scheduler=scheduler, get_chat_id=get_chat_id)
    return Agent(
        name="ReminderAgent",
        role="Gestione promemoria one-shot: crea, lista ed elimina promemoria con scatto singolo, anche collegati a eventi calendario.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + """
Sei l'agente dei promemoria one-shot di AnClaw.

Per CREARE un promemoria generico:
1. Estrai il messaggio da inviare e la data/ora dalla richiesta
2. Converti la data/ora in formato ISO 8601 (es. "2026-04-20T09:00:00") nel fuso Europe/Rome
3. Chiama create_reminder(message, fire_at_iso)

Per CREARE un promemoria da un evento calendario:
1. Identifica il titolo o ID dell'evento nella richiesta
2. Chiama create_calendar_reminder(event_title_or_id, message, minutes_before)
   - minutes_before default: 10
   - message: lascia vuoto per usare il titolo dell'evento

Per LISTARE chiama list_reminders().
Per ELIMINARE chiama delete_reminder(reminder_id).

Conferma sempre all'utente l'azione eseguita con data e ora formattate in italiano.
"""
        ),
        tools=tools,
        debug_mode=True,
        debug_level=2,
    )


def _make_weather_agent() -> Agent:
    from .location_tools import get_weather_forecast
    return Agent(
        name="WeatherAgent",
        role="Fornisce previsioni meteo per qualsiasi città, oggi o nei prossimi giorni.",
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Sei l'agente meteo di AnClaw. "
            "Usa get_weather_forecast per ottenere le previsioni di una città. "
            "Scegli il numero di giorni in base alla richiesta: "
            "'oggi' → days=1, 'domani' → days=2, 'dopodomani' → days=3, "
            "'questa settimana' o 'prossimi giorni' → days=7. "
            "Presenta le previsioni in modo chiaro e amichevole, evidenziando i giorni richiesti. "
            "Se la domanda riguarda un giorno specifico, mostra solo quello."
        ),
        tools=[get_weather_forecast],
        debug_mode=True,
        debug_level=2,
    )


def _make_drive_agent() -> Agent:
    from .drive_tools import search_files, read_file_content, download_file, create_text_file, upload_file
    return Agent(
        name="DriveAgent",
        role=(
            "Gestione Google Drive: cerca file, legge contenuti, scarica file come allegato Telegram, "
            "crea file di testo e carica file su Drive."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + """
Sei l'agente Google Drive di AnClaw.

Usa search_files per cercare file per nome o parola chiave.
Usa read_file_content per leggere il testo di un file (Docs, Fogli, testo, PDF).
Usa download_file per scaricare un file — verrà inviato come allegato su Telegram.
Usa create_text_file per creare un nuovo file di testo su Drive.
Usa upload_file per caricare un file locale su Drive — il path è nel messaggio come [FILE SALVATO: path].

Quando cerchi file, mostra sempre ID, nome e link.
Quando scarichi un file, conferma il nome e lascia che il sistema lo invii automaticamente.
Quando carichi o crei un file, fornisci il link Drive al termine.
"""
        ),
        tools=[search_files, read_file_content, download_file, create_text_file, upload_file],
        debug_mode=True,
        debug_level=2,
    )


def _make_bible_agent() -> Agent:
    from .bible_tools import get_verse_of_the_day
    return Agent(
        name="BibleAgent",
        role=(
            "Assistente biblico evangelico: versetto del giorno con spiegazione, "
            "ricerca di versetti specifici, commento contestuale, parole chiave in greco (NT) o ebraico (AT)."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + """
Sei l'agente biblico evangelico di AnClaw. Usa sempre la traduzione italiana Nuova Riveduta (NR) come default.

Per il VERSETTO DEL GIORNO:
1. Chiama get_verse_of_the_day per ottenere il riferimento e il testo NLT
2. Fornisci il testo completo in italiano (NR)
3. Breve spiegazione del contesto storico e spirituale (3-5 righe)
4. Evidenzia 1-2 parole chiave con il termine originale (greco per NT, ebraico per AT),
   la traslitterazione e il significato letterale

Per VERSETTI SPECIFICI (es. "Giovanni 3:16", "Romani 8:28"):
- Cita il testo in italiano NR
- Fornisci contesto e spiegazione
- Includi la parola chiave con originale greco/ebraico

Per RICERCA TEMATICA (es. "versetti sulla grazia", "cosa dice la Bibbia sul perdono"):
- Cita 3-5 versetti rilevanti con riferimento e testo NR
- Breve commento per ciascuno

Presenta sempre le risposte in modo chiaro ed edificante, con tono evangelico.
"""
        ),
        tools=[get_verse_of_the_day],
        debug_mode=True,
        debug_level=2,
    )


def _make_email_briefing_agent() -> Agent:
    from .gmail_tools import fetch_unread_emails, mark_emails_as_read, get_email_by_id
    return Agent(
        name="EmailBriefingAgent",
        role=(
            "Gestione email Gmail: briefing conversazionale delle email non lette di un giorno, "
            "oppure lettura del contenuto completo di una email specifica per ID."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + """
Sei l'Executive Assistant per la gestione email di Angelo Moroni.

HAI DUE MODALITÀ:

── MODALITÀ BRIEFING (richiesta: "leggi le email", "email di oggi", ecc.) ──
1. Determina la data dalla richiesta:
   - "oggi" → target_date="oggi", "ieri" → target_date="ieri", data specifica → quella data.
   - Se non specificata, usa "oggi".
2. Chiama fetch_unread_emails(target_date).
3. Produci un BRIEFING CONVERSAZIONALE E NARRATIVO:
   - NON elencare in modo secco: tessile in un racconto coerente.
   - Dopo ogni riferimento a un'email, includi subito il suo ID tra parentesi (ID: 18f2abc123).
   - Se un'email contiene link, menzionane l'esistenza.
   - Tono: professionale ma conversazionale.
4. Chiama mark_emails_as_read() con tutti gli ID processati.
5. Conferma quante email sono state segnate come lette.

── MODALITÀ LETTURA SINGOLA (richiesta: "approfondisci/leggi email id X") ──
1. Estrai l'ID dal messaggio dell'utente.
2. Chiama get_email_by_id(message_id).
3. Restituisci mittente, oggetto, data, il corpo completo dell'email
   e la lista esplicita di tutti i link trovati (formato: "## LINK\n- url1\n- url2").
   Non riassumere: restituisci dati grezzi completi per il passo successivo.
"""
        ),
        tools=[fetch_unread_emails, mark_emails_as_read, get_email_by_id],
        debug_mode=True,
        debug_level=2,
    )


def _make_newsletter_agent() -> Agent:
    from .gmail_tools import (
        get_newsletter_summary,
        get_newsletter_senders,
        add_newsletter_sender,
        remove_newsletter_sender,
        list_emails,
    )
    return Agent(
        name="NewsletterAgent",
        role=(
            "Recupera e riassume le email newsletter per un giorno specifico via Gmail. "
            "Gestisce la lista dei mittenti newsletter (aggiunge, mostra, rimuove)."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + """
Sei l'agente Gmail di AnClaw. Hai accesso a Gmail in sola lettura.

Per ELENCARE LE EMAIL DI UN GIORNO:
- Chiama list_emails(target_date) per le email non lette (default).
- Chiama list_emails(target_date, unread_only=False) se l'utente vuole tutte le email.
- Presenta il risultato come lista numerata: mittente — oggetto.

Per il RIASSUNTO NEWSLETTER:
1. Chiama get_newsletter_summary(target_date) con la data richiesta.
   Esempi: "oggi", "ieri", "10 Maggio", "10 maggio 2026", "2026-05-10".
2. Analizza le email restituite e produci un riassunto strutturato:
   - Raggruppa per mittente/fonte
   - Per ogni email: oggetto + sintesi del contenuto in 2-3 righe
   - Evidenzia gli articoli o temi più interessanti

Per GESTIRE LA LISTA MITTENTI NEWSLETTER:
- get_newsletter_senders(): mostra i mittenti configurati con ID
- add_newsletter_sender(name): aggiunge un mittente (es. "Medium", "Morning Brew")
  Il matching è parziale e case-insensitive: "Medium" cattura anche "Medium Daily Digest"
- remove_newsletter_sender(sender_id): rimuove un mittente per ID

Presenta le email in modo chiaro e leggibile.
Conferma sempre le azioni di aggiunta/rimozione mittenti.
"""
        ),
        tools=[list_emails, get_newsletter_summary, get_newsletter_senders, add_newsletter_sender, remove_newsletter_sender],
        debug_mode=True,
        debug_level=2,
    )


def _make_pure_llm_agent(spec: AgentSpec) -> Agent:
    return Agent(
        name=spec.name,
        role=spec.role,
        model=Gemini(id="gemini-2.5-flash"),
        instructions=_base_instructions() + " " + spec.instructions,
        debug_mode=True,
        debug_level=2,
    )


_AGENT_CATALOG: dict[str, Callable[[], Agent]] = {
    "SearchAgent": _make_search_agent,
    "ScraperAgent": _make_scraper_agent,
    "YouTubeAgent": _make_youtube_agent,
    "FileAgent": _make_file_agent,
    "CalendarAgent": _make_calendar_agent,
    "CodeAgent": _make_code_agent,
    "NotesAgent": _make_notes_agent,
    "RSSFeedsAgent": _make_rss_feeds_agent,
    "DriveAgent": _make_drive_agent,
    "WeatherAgent": _make_weather_agent,
    "BibleAgent": _make_bible_agent,
    "EmailBriefingAgent": _make_email_briefing_agent,
    "NewsletterAgent": _make_newsletter_agent,
}

_CATALOG_DESCRIPTIONS = (
    "- SearchTeam: team di ricerca parallela su web (multi-backend) + HackerNews + Wikipedia — "
    "per ricerche su entità specifiche (persone, aziende, eventi, fatti recenti); "
    "restituisce descrizione ampia + lista URL rilevanti da approfondire\n"
    "- NewsTeam: team di ricerca notizie su web news + HackerNews + feed RSS personali — "
    "per richieste di notizie su topic/categorie generiche (es. calcio, tech, politica); "
    "restituisce titoli, sommari e URL delle ultime notizie\n"
    "- ScraperAgent: apre e legge pagine web dagli URL, estrae contenuto completo (WebBrowser + Crawl4AI)\n"
    "- SynthAgent: sintetizzatore finale con memoria di sessione — risponde a domande, elabora i dati raccolti\n"
    "- YouTubeAgent: analisi video YouTube, trascrizioni, ricerca canali\n"
    "- FileAgent: generazione di file (PDF, CSV, testo, ecc.)\n"
    "- SchedulerAgent: gestione sveglie e task ricorrenti (crea, lista, elimina, refresh piano)\n"
    "- ReminderAgent: gestione promemoria one-shot (scatto unico a data/ora precisa, anche collegati a eventi calendario)\n"
    "- CalendarAgent: lettura e gestione calendario Google (leggi eventi, crea eventi, elimina eventi)\n"
    "- CodeAgent: esegue operazioni matematiche/statistiche e analisi su file CSV/Excel "
    "(usa RestrictedPython — sicuro, nessun accesso a filesystem o internet)\n"
    "- NotesAgent: gestione appunti personali — salva note, mostra tutte le note, cerca nelle note, elimina note per ID\n"
    "- RSSFeedsAgent: gestione feed RSS — aggiunge nuovi feed, mostra la lista, elimina feed per ID\n"
    "- DriveAgent: gestione Google Drive — cerca file, legge contenuti, scarica file (allegato Telegram), "
    "crea file di testo, carica file da Telegram su Drive\n"
    "- WeatherAgent: previsioni meteo per qualsiasi città — oggi, domani o fino a 7 giorni; "
    "usa Open-Meteo (no API key); fornisce temperatura min/max, condizioni, precipitazioni, vento, alba e tramonto\n"
    "- BibleAgent: assistente biblico evangelico — versetto del giorno con spiegazione, "
    "ricerca di versetti specifici per riferimento o tema, commento contestuale, "
    "parole chiave in greco (NT) o ebraico (AT); usa traduzione italiana Nuova Riveduta (NR)\n"
    "- EmailBriefingAgent: briefing conversazionale inbox Gmail — recupera TUTTE le email non lette, "
    "crea un riassunto narrativo con ID di ogni email tra parentesi, segnala link presenti, "
    "poi segna tutte le email come lette; usare per 'leggi le mie email', 'briefing email', 'email non lette'\n"
    "- NewsletterAgent: gestione newsletter Gmail — elenca email per giorno, riassume newsletter "
    "filtrate per mittente; gestisce la lista dei mittenti newsletter (aggiunge, mostra, rimuove)"
)
