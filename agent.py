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
from agno.tools.reddit import RedditTools
from agno.tools.youtube import YouTubeTools
from agno.tools.webbrowser import WebBrowserTools
from agno.tools.websearch import WebSearchTools
from agno.tools.file_generation import FileGenerationTools


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
    "get_subreddit_posts": "Reddit",
    "search_reddit": "Reddit",
    "get_reddit_post": "Reddit",
    "web_browser": "apertura pagina web",
    "crawl4ai": "scraping pagina web",
    "crawl_url": "scraping pagina web",
    "scrape_url": "scraping pagina web",
    "get_youtube_video_data": "YouTube",
    "search_youtube_videos": "YouTube",
    "get_youtube_video_captions": "YouTube",
    "generate_file": "generazione file",
    "create_schedule": "creazione sveglia",
    "list_schedules": "lista sveglie",
    "delete_schedule": "eliminazione sveglia",
    "refresh_schedule": "aggiornamento sveglia",
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


# ── Catalogo agenti (factory functions, senza memoria) ────────────────────────

def _make_search_agent() -> Agent:
    return Agent(
        name="SearchAgent",
        role=(
            "Esegue ricerche web, su Hacker News e Reddit. "
            "Restituisce una lista di URL rilevanti con titoli e snippet per ogni risultato trovato."
        ),
        model=Gemini(id="gemini-2.5-flash"),
        instructions=(
            _base_instructions()
            + " Il tuo unico compito è cercare informazioni e restituire URL + snippet rilevanti. "
            "Usa WebSearchTools per ricerche generali, HackerNews per notizie tech, Reddit per discussioni. "
            "NON aprire le pagine: limitati a elencare i risultati con URL, titolo e snippet. "
            "Restituisci sempre gli URL completi trovati, sono necessari per il passo successivo."
        ),
        tools=[WebSearchTools(enable_news=False), HackerNewsTools(), RedditTools()],
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


_AGENT_CATALOG: dict[str, Callable[[], Agent]] = {
    "SearchAgent": _make_search_agent,
    "ScraperAgent": _make_scraper_agent,
    "YouTubeAgent": _make_youtube_agent,
    "FileAgent": _make_file_agent,
}

_CATALOG_DESCRIPTIONS = (
    "- SearchAgent: ricerca web, Hacker News, Reddit — restituisce URL + snippet, NON apre le pagine\n"
    "- ScraperAgent: apre e legge pagine web dagli URL, estrae contenuto completo (WebBrowser + Crawl4AI)\n"
    "- SynthAgent: sintetizzatore finale con memoria di sessione — risponde a domande, elabora i dati raccolti\n"
    "- YouTubeAgent: analisi video YouTube, trascrizioni, ricerca canali\n"
    "- FileAgent: generazione di file (PDF, CSV, testo, ecc.)\n"
    "- SchedulerAgent: gestione sveglie e task ricorrenti (crea, lista, elimina, refresh piano)"
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

6. CRAWLING DI URL SPECIFICI già noti:
   → coordinate: [ScraperAgent → SynthAgent]

REGOLE GENERALI:
- Non creare agenti pure LLM aggiuntivi oltre a SynthAgent: è già il sintetizzatore.
- Scegli SOLO gli agenti strettamente necessari.
- intermediate_message: frase breve in italiano che descrive cosa sta per succedere (es. "Cerco le informazioni e analizzo le pagine rilevanti."). Nessuna analisi del contenuto.
- Il goal deve descrivere il risultato atteso.
""".strip()


# ── AIAgent (facade pubblica) ─────────────────────────────────────────────────

class AIAgent:
    def __init__(self):
        self._db_path = _DB_PATH

        self._architect = Agent(
            name="ArchitectAgent",
            model=Gemini(id="gemini-2.5-pro"),
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

        # SynthAgent statico con session memory (ricorda la conversazione corrente)
        self._synth_agent = Agent(
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
            ),
            db=SqliteDb(
                db_file=_DB_PATH,
                session_table="synth_sessions",
                memory_table="synth_memories",
            ),
            add_history_to_context=True,
            markdown=True,
            debug_mode=True,
            debug_level=2,
        )

        self._current_chat_id: int | None = None

        from scheduler import create_scheduler
        self._scheduler = create_scheduler()

    @property
    def scheduler(self):
        return self._scheduler

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

    async def _get_plan(self, task_description: str) -> ArchitectPlan:
        """Run just the Architect to get a pre-baked plan, without executing the team."""
        now = datetime.now(_TZ)
        date_str = now.strftime("%d %B %Y, %H:%M %Z")
        message = (
            f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n"
            f"{task_description}"
        )
        response = await self._architect.arun(
            message,
            user_id="scheduler",
            session_id="architect_scheduler",
        )
        plan = response.content
        if not isinstance(plan, ArchitectPlan):
            raise ValueError(f"Architect returned unexpected type: {type(plan)}")
        return plan

    def _build_members(self, plan: ArchitectPlan) -> list[Agent]:
        members: list[Agent] = []
        for spec in plan.agents:
            if spec.name == "SynthAgent":
                members.append(self._synth_agent)
            elif not spec.is_pure_llm and spec.name == "SchedulerAgent":
                members.append(self._make_scheduler_agent())
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

        # 1. Architetto analizza la richiesta e produce il piano
        now = datetime.now(_TZ)
        date_str = now.strftime("%d %B %Y, %H:%M %Z")
        architect_message = (
            f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n{message}"
        )
        architect_response = await self._architect.arun(
            architect_message,
            user_id=str(user_id),
            session_id=f"architect_{user_id}",
            images=images or None,
            files=files or None,
        )

        plan = architect_response.content
        if not isinstance(plan, ArchitectPlan):
            logger.error(f"Architect ha restituito un tipo inatteso: {type(plan)}")
            return "Errore nella pianificazione. Riprova più tardi.", []

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
