import logging
import os
import zoneinfo
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.media import File, Image
from agno.models.google import Gemini
from agno.team import Team
from agno.run.team import TeamRunEvent
from agno.tools.crawl4ai import Crawl4aiTools
from agno.tools.hackernews import HackerNewsTools
from agno.tools.youtube import YouTubeTools
from agno.tools.webbrowser import WebBrowserTools
from agno.tools.websearch import WebSearchTools
from agno.tools.file_generation import FileGenerationTools

import memory_store
import notes_store


_DB_PATH = os.path.join(os.path.dirname(__file__), "tmp", "agent_data.db")
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

logger = logging.getLogger(__name__)

_CUTOFF = "agosto 2025"
_TZ = zoneinfo.ZoneInfo("Europe/Rome")


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
    "save_note": "salvataggio nota",
    "list_notes": "lettura note",
    "search_notes": "ricerca nelle note",
    "delete_note": "eliminazione nota",
}


# ── Schema output dell'Architetto ─────────────────────────────────────────────

class AgentSpec(BaseModel):
    name: str
    role: str
    instructions: str
    is_pure_llm: bool = False


class ArchitectPlan(BaseModel):
    goal: str
    intermediate_message: str
    team_name: str
    team_mode: Literal["coordinate", "route", "broadcast"]
    agents: list[AgentSpec]


_ARCHITECT_HINT = (
    "\n\nATTENZIONE: la tua risposta precedente non era un piano valido. "
    "Rispondi SOLO con un JSON che corrisponde esattamente allo schema ArchitectPlan. "
    "Campi obbligatori: goal (str), intermediate_message (str), team_name (str), "
    "team_mode (\"coordinate\"|\"route\"|\"broadcast\"), "
    "agents (lista di oggetti con name, role, instructions, is_pure_llm). "
    "Nessun testo fuori dal JSON."
)

_FALLBACK_PLAN = ArchitectPlan(
    goal="Rispondere alla richiesta dell'utente",
    intermediate_message="Elaboro la tua richiesta...",
    team_name="Fallback Team",
    team_mode="route",
    agents=[AgentSpec(
        name="SynthAgent",
        role="Sintetizzatore",
        instructions="Rispondi alla richiesta nel modo più utile possibile.",
        is_pure_llm=False,
    )],
)


# ── Estrazione fatti espliciti ────────────────────────────────────────────────

_EXPLICIT_TRIGGERS = (
    "ricordati che ", "ricordati: ", "ricorda che ", "ricorda: ",
    "nota che ", "nota: ", "memorizza che ", "memorizza: ",
    "tieni a mente che ", "tieni a mente: ",
)


_QUICK_NOTE_TRIGGERS = (
    "nota: ", "appunto: ", "nota- ", "appunto- ",
)


def _extract_quick_note(message: str) -> str | None:
    """
    Se il messaggio inizia con "Nota: ..." o "Appunto: ...",
    ritorna il testo della nota. Altrimenti None.
    """
    lower = message.lower().strip()
    for trigger in _QUICK_NOTE_TRIGGERS:
        if lower.startswith(trigger):
            return message[len(trigger):].strip()
    return None


def _extract_explicit_fact(message: str) -> str | None:
    """
    Se il messaggio inizia con una frase esplicita di memorizzazione,
    ritorna la parte da ricordare. Altrimenti None.
    """
    lower = message.lower().strip()
    for trigger in _EXPLICIT_TRIGGERS:
        if lower.startswith(trigger):
            return message[len(trigger):].strip()
    return None


# ── Catalogo agenti (factory functions, senza memoria) ────────────────────────

def _make_search_agent() -> Agent:
    return Agent(
        name="SearchAgent",
        role=(
            "Esegue ricerche web e su Hacker News. "
            "Restituisce una lista di URL rilevanti con titoli e snippet per ogni risultato trovato."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Il tuo unico compito è cercare informazioni e restituire URL + snippet rilevanti. "
            "Usa WebSearchTools per ricerche generali, HackerNews per notizie tech. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati, sono necessari per il passo successivo."
        ),
        tools=[WebSearchTools(enable_news=False), HackerNewsTools()],
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
    from code_tools import execute_math, search_in_file, filter_file_rows
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
    from calendar_tools import list_events, create_event, delete_event
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


def _make_reminder_agent(scheduler, get_chat_id) -> Agent:
    from scheduler import make_reminder_tools
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


_AGENT_CATALOG: dict[str, Callable[[], Agent]] = {
    "SearchAgent": _make_search_agent,
    "ScraperAgent": _make_scraper_agent,
    "YouTubeAgent": _make_youtube_agent,
    "FileAgent": _make_file_agent,
    "CalendarAgent": _make_calendar_agent,
    "CodeAgent": _make_code_agent,
    "NotesAgent": _make_notes_agent,
}

_CATALOG_DESCRIPTIONS = (
    "- SearchAgent: ricerca web, Hacker News — restituisce URL + snippet, NON apre le pagine\n"
    "- ScraperAgent: apre e legge pagine web dagli URL, estrae contenuto completo (WebBrowser + Crawl4AI)\n"
    "- SynthAgent: sintetizzatore finale con memoria di sessione — risponde a domande, elabora i dati raccolti\n"
    "- YouTubeAgent: analisi video YouTube, trascrizioni, ricerca canali\n"
    "- FileAgent: generazione di file (PDF, CSV, testo, ecc.)\n"
    "- SchedulerAgent: gestione sveglie e task ricorrenti (crea, lista, elimina, refresh piano)\n"
    "- ReminderAgent: gestione promemoria one-shot (scatto unico a data/ora precisa, anche collegati a eventi calendario)\n"
    "- CalendarAgent: lettura e gestione calendario Google (leggi eventi, crea eventi, elimina eventi)\n"
    "- CodeAgent: esegue operazioni matematiche/statistiche e analisi su file CSV/Excel "
    "(usa RestrictedPython — sicuro, nessun accesso a filesystem o internet)\n"
    "- NotesAgent: gestione appunti personali — salva note, mostra tutte le note, cerca nelle note, elimina note per ID"
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


# ── Istruzioni Architetto ─────────────────────────────────────────────────────

_ARCHITECT_INSTRUCTIONS = f"""
Sei l'agente Architetto di AnClaw, l'assistente AI personale di Angelo Moroni.
Il tuo UNICO compito è leggere la richiesta dell'utente e costruire il piano di esecuzione: chi fa cosa e in che ordine.
Non devi analizzare, valutare, rispondere né ragionare sul contenuto della richiesta. Quello spetta agli agenti del team.

AGENTI DISPONIBILI NEL CATALOGO:
{_CATALOG_DESCRIPTIONS}

MODALITÀ DEL TEAM:
- coordinate: agenti con dipendenze sequenziali (il risultato di uno serve all'altro)
- broadcast: agenti che lavorano in parallelo su task indipendenti
- route: un solo agente (task semplici o risposta diretta)

REGOLE DI ROUTING:

1. FATTI STORICI NOTI, definizioni, concetti stabili e consolidati (es. "chi ha ucciso John Lennon", "cos'è la fotosintesi"):
   → route: [SynthAgent] da solo — risponde direttamente con la sua conoscenza

2. NOTIZIE RECENTI, persone viventi, eventi attuali, informazioni che potrebbero essere cambiate, attualità:
   → coordinate: [SearchAgent → ScraperAgent → SynthAgent]
   SearchAgent cerca e raccoglie URL, ScraperAgent apre le pagine e ne estrae il contenuto,
   SynthAgent elabora tutto e produce la risposta finale.

3. VIDEO YOUTUBE:
   → route o coordinate con YouTubeAgent (+ SynthAgent se serve sintesi)

4. GENERAZIONE FILE:
   → route: [FileAgent]

5. GESTIONE SVEGLIE E TASK RICORRENTI:
   → route: [SchedulerAgent] da solo

5b. PROMEMORIA ONE-SHOT (ricordami, promemoria, avvisami, notifica tra X minuti/ore, prima di un evento):
   Il messaggio contiene "promemoria", "ricordami", "avvisami", "notificami", "reminder",
   o un riferimento a un orario preciso con richiesta di notifica singola.
   → route: [ReminderAgent] da solo

6. CALENDARIO GOOGLE (leggere eventi, aggiungere appuntamenti, eliminare eventi):
   → route: [CalendarAgent] da solo

7. CALCOLI MATEMATICI, statistiche, operazioni numeriche:
   → route: [CodeAgent] da solo

8. ANALISI DI FILE CSV o EXCEL (ricerca di righe/valori, filtri su dati):
   Il messaggio contiene [FILE SALVATO: path] quando l'utente ha allegato un file.
   → route: [CodeAgent] da solo

9. APPUNTI E NOTE PERSONALI (salva appunto, mostra note, cerca nelle note, elimina nota):
   Il messaggio contiene parole come "nota", "appunto", "mostra le note", "cerca nelle note", "elimina nota".
   → route: [NotesAgent] da solo

10. CRAWLING DI URL SPECIFICI già noti:
   → coordinate: [ScraperAgent → SynthAgent]

REGOLE GENERALI:
- Non creare agenti pure LLM aggiuntivi oltre a SynthAgent: è già il sintetizzatore.
- Scegli SOLO gli agenti strettamente necessari.
- intermediate_message: frase breve in italiano che descrive cosa sta per succedere (es. "Cerco le informazioni e analizzo le pagine rilevanti."). Nessuna analisi del contenuto.
- Il goal deve descrivere il risultato atteso.

ESEMPI DI OUTPUT JSON ATTESO:

Richiesta: "chi ha ucciso Lincoln?"
{{
  "goal": "Rispondere alla domanda su chi ha assassinato Abraham Lincoln",
  "intermediate_message": "Rispondo direttamente alla tua domanda.",
  "team_name": "AnClaw Direct Team",
  "team_mode": "route",
  "agents": [
    {{"name": "SynthAgent", "role": "Sintetizzatore", "instructions": "Rispondi alla domanda su chi ha assassinato Lincoln usando la tua conoscenza storica.", "is_pure_llm": false}}
  ]
}}

Richiesta: "ultime notizie su OpenAI"
{{
  "goal": "Raccogliere e sintetizzare le ultime notizie su OpenAI",
  "intermediate_message": "Cerco le ultime notizie su OpenAI e analizzo le fonti.",
  "team_name": "AnClaw News Team",
  "team_mode": "coordinate",
  "agents": [
    {{"name": "SearchAgent", "role": "Ricercatore web", "instructions": "Cerca le ultime notizie su OpenAI e restituisci URL e snippet rilevanti.", "is_pure_llm": false}},
    {{"name": "ScraperAgent", "role": "Lettore di pagine", "instructions": "Apri i top 3 URL trovati da SearchAgent ed estrai il contenuto testuale completo.", "is_pure_llm": false}},
    {{"name": "SynthAgent", "role": "Sintetizzatore", "instructions": "Elabora i contenuti estratti e produci un riassunto delle ultime notizie su OpenAI.", "is_pure_llm": false}}
  ]
}}
""".strip()


# ── AIAgent (facade pubblica) ─────────────────────────────────────────────────

class AIAgent:
    def __init__(self):
        self._db_path = _DB_PATH

        self._architect = Agent(
            name="ArchitectAgent",
            model=Gemini(id="gemini-2.5-flash", generation_config={"temperature": 0.2}),
            instructions=_ARCHITECT_INSTRUCTIONS,
            output_schema=ArchitectPlan,
            db=SqliteDb(
                db_file=_DB_PATH,
                session_table="architect_sessions",
                memory_table="architect_memories",
            ),
            add_history_to_context=True,
            debug_mode=True,
            debug_level=2,
        )

        # Agente leggero per estrazione fatti (nessuna memoria, nessun tool)
        self._fact_extractor = Agent(
            name="FactExtractor",
            model=Gemini(id="gemini-2.5-flash"),
            instructions=(
                "Sei un estrattore di fatti personali. "
                "Dato un messaggio di Angelo, estrai solo i fatti stabili e permanenti su di lui: "
                "preferenze, abitudini, dati anagrafici, luoghi, professione, hobby, ecc. "
                "Ignora domande, comandi, richieste temporanee o fatti non riguardanti Angelo. "
                "Rispondi SOLO con una lista JSON di stringhe (es. [\"Abita a Milano\"]). "
                "Se non ci sono fatti permanenti, rispondi con []."
            ),
            debug_mode=False,
        )

        self._current_chat_id: int | None = None

        memory_store.init_memory_table()
        notes_store.init_notes_table()

        from scheduler import create_scheduler
        self._scheduler = create_scheduler()

    @property
    def scheduler(self):
        return self._scheduler

    def _make_synth_agent(self) -> Agent:
        """Crea il SynthAgent iniettando i fatti correnti nelle istruzioni."""
        facts_text = memory_store.get_facts_text()
        memory_section = f"\n\n{facts_text}" if facts_text else ""
        return Agent(
            name="SynthAgent",
            role=(
                "Sintetizzatore finale con memoria di sessione. "
                "Elabora i dati raccolti da SearchAgent e ScraperAgent e produce risposte complete e accurate. "
                "Risponde direttamente a fatti storici noti senza attendere altri agenti."
            ),
            model=Gemini(id="gemini-2.5-flash"),
            instructions=(
                _base_instructions()
                + " Sei il sintetizzatore finale di AnClaw. "
                "Quando ricevi dati da SearchAgent e ScraperAgent, basati ESCLUSIVAMENTE su quelli "
                "per rispondere — non usare la tua conoscenza pregressa per fatti recenti o verificabili. "
                "Quando sei il solo agente nel team (fatti storici noti, definizioni), "
                "rispondi direttamente e con sicurezza usando la tua conoscenza. "
                "Ricordi la conversazione corrente: gestisci correttamente le domande di follow-up."
                + memory_section
            ),
            db=SqliteDb(
                db_file=self._db_path,
                session_table="synth_sessions",
                memory_table="synth_memories",
            ),
            add_history_to_context=True,
            markdown=True,
            debug_mode=True,
            debug_level=2,
        )

    async def _extract_and_save_facts(self, message: str) -> None:
        """Estrae fatti permanenti dal messaggio e li salva in background."""
        try:
            response = await self._fact_extractor.arun(message)
            raw = (response.content or "").strip()
            # Trova il blocco JSON anche se l'LLM aggiunge testo extra
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return
            import json
            facts: list[str] = json.loads(raw[start:end + 1])
            for fact in facts:
                fact = fact.strip()
                if fact and not memory_store.fact_exists(fact):
                    memory_store.save_fact(fact, source="auto")
                    logger.info("Fatto salvato automaticamente: %r", fact)
        except Exception:
            logger.debug("Estrazione fatti fallita (non bloccante)", exc_info=True)

    def reset_user_sessions(self, user_id: int) -> None:
        """Cancella le sessioni di ArchitectAgent, SynthAgent e Team per l'utente."""
        import sqlite3
        session_map = {
            "architect_sessions": f"architect_{user_id}",
            "synth_sessions": f"synth_{user_id}",
            "team_sessions": f"team_{user_id}",
        }
        with sqlite3.connect(self._db_path) as conn:
            for table, session_id in session_map.items():
                try:
                    conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
                except Exception:
                    logger.debug("Tabella %r non trovata o errore cancellazione, skip.", table)

    def _make_scheduler_agent(self) -> Agent:
        from scheduler import make_scheduler_tools
        tools = make_scheduler_tools(
            scheduler=self._scheduler,
            ai_agent=self,
            get_chat_id=lambda: self._current_chat_id,
        )
        return Agent(
            name="SchedulerAgent",
            role="Gestione sveglie e task programmati: creazione, lista, eliminazione e refresh del piano",
            model=Gemini(id="gemini-2.5-flash"),
            instructions=(
                _base_instructions()
                + """
Sei l'agente di gestione delle sveglie di AnClaw.
Puoi creare sveglie ricorrenti, listare quelle esistenti, eliminarle o aggiornare il loro piano.

Per CREARE una sveglia:
1. Estrai il task dalla richiesta (cosa deve eseguire la sveglia, in modo completo e autonomo)
2. Ricava il cron expression dal linguaggio naturale
3. Chiama create_schedule(task_description, cron_expr)

Formato cron (5 campi: minuto ora giorno_mese mese giorno_settimana):
- Ogni giorno alle 08:00  → "0 8 * * *"
- Ogni lunedì alle 09:30  → "30 9 * * 1"
- Ogni venerdì alle 18:00 → "0 18 * * 5"
- Ogni primo del mese     → "0 9 1 * *"
- Ogni ora                → "0 * * * *"

Per LISTARE chiama list_schedules().
Per ELIMINARE chiama delete_schedule(schedule_id) — l'ID è a 8 caratteri esadecimali.
Per AGGIORNARE il piano chiama refresh_schedule(schedule_id).
"""
            ),
            tools=tools,
            debug_mode=True,
            debug_level=2,
        )

    async def _run_architect(
        self,
        message: str,
        user_id: str,
        session_id: str,
        images: list | None = None,
        files: list | None = None,
    ) -> ArchitectPlan:
        """Chiama l'Architetto con retry (max 2 tentativi) e fallback automatico."""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                msg = message if attempt == 0 else message + _ARCHITECT_HINT
                response = await self._architect.arun(
                    msg,
                    user_id=user_id,
                    session_id=session_id,
                    images=images or None,
                    files=files or None,
                )
                plan = response.content
                if isinstance(plan, ArchitectPlan):
                    return plan
                logger.warning(
                    f"Architect tentativo {attempt + 1}: tipo inatteso {type(plan)}"
                )
                last_exc = ValueError(f"tipo inatteso: {type(plan)}")
            except Exception as e:
                logger.warning(f"Architect tentativo {attempt + 1} fallito: {e}")
                last_exc = e

        logger.error(
            f"Architect fallito dopo 2 tentativi, uso fallback. Ultimo errore: {last_exc}"
        )
        return _FALLBACK_PLAN

    async def _get_plan(self, task_description: str) -> ArchitectPlan:
        """Run just the Architect to get a pre-baked plan, without executing the team."""
        now = datetime.now(_TZ)
        date_str = now.strftime("%d %B %Y, %H:%M %Z")
        message = (
            f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n"
            f"{task_description}"
        )
        return await self._run_architect(
            message,
            user_id="scheduler",
            session_id="architect_scheduler",
        )

    def _build_members(self, plan: ArchitectPlan) -> list[Agent]:
        members: list[Agent] = []
        for spec in plan.agents:
            if spec.name == "SynthAgent":
                members.append(self._make_synth_agent())
            elif not spec.is_pure_llm and spec.name == "SchedulerAgent":
                members.append(self._make_scheduler_agent())
            elif not spec.is_pure_llm and spec.name == "ReminderAgent":
                members.append(_make_reminder_agent(
                    scheduler=self._scheduler,
                    get_chat_id=lambda: self._current_chat_id,
                ))
            elif not spec.is_pure_llm and spec.name in _AGENT_CATALOG:
                members.append(_AGENT_CATALOG[spec.name]())
            else:
                members.append(_make_pure_llm_agent(spec))
        return members

    async def run_from_plan(
        self,
        plan: ArchitectPlan,
        message: str,
        *,
        user_id: int | str = "scheduler",
        images: list[Image] | None = None,
        files: list[File] | None = None,
        on_event: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> tuple[str, list[File]]:
        """Execute a team directly from a pre-baked ArchitectPlan, skipping the Architect."""
        members = self._build_members(plan)

        if not members:
            logger.warning("Nessun agente nel piano, fallback a risposta diretta")
            return "Non ho trovato agenti adatti per questa richiesta. Riprova.", []

        team = Team(
            name=plan.team_name or "AnClaw Dynamic Team",
            mode=plan.team_mode,
            model=Gemini(id="gemini-2.5-flash"),
            members=members,
            instructions=f"{_base_instructions()}\n\nObiettivo: {plan.goal}",
            db=SqliteDb(
                db_file=_DB_PATH,
                session_table="team_sessions",
                memory_table="team_memories",
            ),
            add_history_to_context=True,
            session_id=f"team_{user_id}",
            markdown=True,
            debug_mode=True,
            debug_level=2,
        )

        content_parts: list[str] = []
        generated_files: list[File] = []

        stream = team.arun(
            message,
            user_id=str(user_id),
            session_id=f"team_{user_id}",
            images=images or None,
            files=files or None,
            stream=True,
            stream_events=True,
        )

        async for event in stream:
            if event.event == TeamRunEvent.run_content:
                content_parts.append(event.content or "")

            elif event.event == TeamRunEvent.tool_call_started and on_event:
                tool_name = (
                    event.tool.tool_name
                    if event.tool and event.tool.tool_name
                    else "tool"
                )
                label = _TOOL_LABELS.get(tool_name, tool_name)
                await on_event(f"_Uso {label}..._")

            elif event.event == TeamRunEvent.run_completed:
                for member_resp in getattr(event, "member_responses", []):
                    member_files = getattr(member_resp, "files", None) or []
                    generated_files.extend(member_files)

            elif event.event == TeamRunEvent.run_error:
                logger.error(f"Team run error: {getattr(event, 'error', 'unknown')}")

        return "".join(content_parts), generated_files

    async def run(
        self,
        user_id: int,
        message: str,
        chat_id: int | None = None,
        images: list[Image] | None = None,
        files: list[File] | None = None,
        on_event: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> tuple[str, list[File]]:
        self._current_chat_id = chat_id

        # 0. Gestione memoria a lungo termine
        explicit_fact = _extract_explicit_fact(message)
        if explicit_fact:
            if not memory_store.fact_exists(explicit_fact):
                memory_store.save_fact(explicit_fact, source="explicit")
                logger.info("Fatto esplicito salvato: %r", explicit_fact)
        else:
            # Estrazione automatica in background (non blocca la risposta)
            import asyncio
            asyncio.create_task(self._extract_and_save_facts(message))

        # 1. Architetto analizza la richiesta e produce il piano
        now = datetime.now(_TZ)
        date_str = now.strftime("%d %B %Y, %H:%M %Z")
        architect_message = (
            f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n{message}"
        )
        plan = await self._run_architect(
            architect_message,
            user_id=str(user_id),
            session_id=f"architect_{user_id}",
            images=images,
            files=files,
        )

        logger.info(
            f"Piano architetto: mode={plan.team_mode}, "
            f"agenti={[a.name for a in plan.agents]}, goal={plan.goal!r}"
        )

        # 2. Invia messaggio intermedio all'utente
        if on_event and plan.intermediate_message:
            await on_event(plan.intermediate_message)

        # 3-5. Esegui il team dal piano
        return await self.run_from_plan(
            plan,
            message,
            user_id=user_id,
            images=images,
            files=files,
            on_event=on_event,
        )
