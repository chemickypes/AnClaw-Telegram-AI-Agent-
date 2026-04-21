import zoneinfo
from collections.abc import Callable
from datetime import datetime

from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools.crawl4ai import Crawl4aiTools
from agno.tools.file_generation import FileGenerationTools
from agno.tools.hackernews import HackerNewsTools
from agno.tools.webbrowser import WebBrowserTools
from agno.tools.websearch import WebSearchTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.youtube import YouTubeTools

from . import memory_store
from . import notes_store
from . import rss_store
from .agent_models import AgentSpec

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
    "search_files": "ricerca su Drive",
    "read_file_content": "lettura file Drive",
    "download_file": "download da Drive",
    "create_text_file": "creazione file su Drive",
    "upload_file": "upload su Drive",
    "save_note": "salvataggio nota",
    "list_notes": "lettura note",
    "search_notes": "ricerca nelle note",
    "delete_note": "eliminazione nota",
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
            "Usa WebSearchTools per ricerche generali (prova più backend automaticamente), "
            "HackerNews per notizie tech. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati, sono necessari per il passo successivo."
        ),
        tools=[WebSearchTools(enable_news=False, backend="auto"), HackerNewsTools()],
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
            "Usa WebSearchTools con la modalità news (search_news) per risultati recenti, "
            "HackerNews per notizie tech. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati."
        ),
        tools=[WebSearchTools(enable_news=True, backend="auto", timelimit="w"), HackerNewsTools()],
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
    "crea file di testo, carica file da Telegram su Drive"
)
