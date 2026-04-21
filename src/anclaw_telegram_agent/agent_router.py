import json
import logging
import re
from datetime import datetime

from agno.agent import Agent
from agno.models.google import Gemini
from agno.team import Team

from . import rss_store
from .agent_catalog import (
    _CATALOG_DESCRIPTIONS,
    _TZ,
    _CUTOFF,
    _base_instructions,
    _make_search_agent,
    _make_wikipedia_agent,
    _make_rss_agent,
)
from .agent_models import AgentSpec, ArchitectPlan, _ARCHITECT_HINT, _FALLBACK_PLAN

logger = logging.getLogger(__name__)

# ── Routing deterministico ────────────────────────────────────────────────────

_RE_CONTEXT_PREFIX = re.compile(r"^\[Contesto:[^\]]+\]\n\n", re.DOTALL)
_RE_FILE_PREFIX = re.compile(r"^\[FILE SALVATO:[^\]]+\]\n*", re.DOTALL)


def _strip_architect_prefix(message: str) -> str:
    """Rimuove i prefissi aggiunti da agent.py prima del testo utente reale."""
    m = _RE_CONTEXT_PREFIX.match(message)
    text = message[m.end():] if m else message
    m2 = _RE_FILE_PREFIX.match(text)
    return text[m2.end():] if m2 else text


_RE_SCHEDULER = re.compile(
    r"^\s*(ricordami|avvisami|notificami|crea|imposta|aggiungi|programma)\b.*"
    r"\bogni\s+(giorno|settimana|mese|ora|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica)\b"
    r"|^\s*ogni\s+(giorno|settimana|mese|ora|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica)\b"
    r"|^\s*(crea|imposta|aggiungi|programma)\s+(una?\s+)?(sveglia|task\s+ricorrente)\b",
    re.IGNORECASE,
)

_RE_REMINDER = re.compile(
    r"^\s*(ricordami|avvisami|notificami)\b"
    r"|^\s*promemoria\s*[:\-]",
    re.IGNORECASE,
)
_RE_NOTE_SAVE = re.compile(
    r"^\s*(salva|aggiungi|scrivi|crea)\s+(una?\s+)?(nota|appunto)\b",
    re.IGNORECASE,
)
_RE_NOTE_LIST = re.compile(
    r"^\s*(mostra|lista|elenca|vedi|leggi|dammi)\s+(le\s+|gli\s+|tutte?\s+le\s+)?(note|appunti)\b",
    re.IGNORECASE,
)
_RE_NOTE_SEARCH = re.compile(
    r"^\s*(cerca|trova)\s+(nelle?\s+)?(note|appunti)\b",
    re.IGNORECASE,
)
_RE_NOTE_DELETE = re.compile(
    r"^\s*(elimina|cancella|rimuovi)\s+(la\s+|questa\s+)?nota\b",
    re.IGNORECASE,
)
_RE_RSS = re.compile(
    r"^\s*(aggiungi|rimuovi|elimina|mostra|lista|elenca)\s+(un\s+)?feed(\s+rss)?\b"
    r"|^\s*(feed\s+rss|lista\s+(dei\s+)?feed|mostra\s+(i\s+)?feed)\b",
    re.IGNORECASE,
)


def _route_plan(agent_name: str, goal: str, intermediate: str, instructions: str) -> ArchitectPlan:
    return ArchitectPlan(
        goal=goal,
        intermediate_message=intermediate,
        team_name="AnClaw Direct Team",
        team_mode="route",
        agents=[AgentSpec(name=agent_name, role=agent_name, instructions=instructions, is_pure_llm=False)],
    )


def _deterministic_route(user_text: str) -> ArchitectPlan | None:
    if _RE_SCHEDULER.match(user_text):
        return _route_plan(
            "SchedulerAgent",
            "Gestire la sveglia o il task ricorrente",
            "Configuro la sveglia ricorrente...",
            "Crea o gestisci la sveglia ricorrente richiesta dall'utente.",
        )
    if _RE_REMINDER.match(user_text):
        return _route_plan(
            "ReminderAgent",
            "Creare il promemoria richiesto",
            "Creo il promemoria...",
            "Crea il promemoria richiesto dall'utente.",
        )
    if any(r.match(user_text) for r in (_RE_NOTE_SAVE, _RE_NOTE_LIST, _RE_NOTE_SEARCH, _RE_NOTE_DELETE)):
        return _route_plan(
            "NotesAgent",
            "Gestire gli appunti personali",
            "Gestisco le note...",
            "Esegui l'operazione richiesta sugli appunti.",
        )
    if _RE_RSS.match(user_text):
        return _route_plan(
            "RSSFeedsAgent",
            "Gestire i feed RSS",
            "Gestisco i feed RSS...",
            "Esegui l'operazione richiesta sui feed RSS.",
        )
    return None

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
   → coordinate: [SearchTeam → ScraperAgent → SynthAgent]
   SearchTeam ricerca in parallelo su web, Wikipedia e feed RSS e restituisce descrizione + lista URL,
   ScraperAgent apre le pagine rilevanti ed estrae il contenuto completo,
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

10. GESTIONE FEED RSS (aggiungi feed, mostra feed, elimina feed, lista feed RSS):
   Il messaggio contiene parole come "feed RSS", "aggiungi feed", "mostra feed", "elimina feed", "lista feed".
   → route: [RSSFeedsAgent] da solo

11. CRAWLING DI URL SPECIFICI già noti:
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
  "intermediate_message": "Cerco le ultime notizie su OpenAI su web, Wikipedia e feed RSS.",
  "team_name": "AnClaw News Team",
  "team_mode": "coordinate",
  "agents": [
    {{"name": "SearchTeam", "role": "Team di ricerca parallela", "instructions": "Cerca le ultime notizie su OpenAI su web, Wikipedia e feed RSS. Restituisci descrizione ampia e lista URL.", "is_pure_llm": false}},
    {{"name": "ScraperAgent", "role": "Lettore di pagine", "instructions": "Apri i top 3 URL trovati da SearchTeam ed estrai il contenuto testuale completo.", "is_pure_llm": false}},
    {{"name": "SynthAgent", "role": "Sintetizzatore", "instructions": "Elabora i contenuti estratti e produci un riassunto delle ultime notizie su OpenAI.", "is_pure_llm": false}}
  ]
}}
""".strip()


async def run_architect(
    architect: Agent,
    message: str,
    user_id: str,
    session_id: str,
    images: list | None = None,
    files: list | None = None,
) -> ArchitectPlan:
    """Chiama l'Architetto con retry (max 2 tentativi) e fallback automatico."""
    user_text = _strip_architect_prefix(message)
    fast_plan = _deterministic_route(user_text)
    if fast_plan:
        logger.info("[ROUTER] deterministico → %s | testo: %r", fast_plan.agents[0].name, user_text[:80])
        return fast_plan

    logger.info("[ROUTER] LLM architect → testo: %r", user_text[:80])
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            msg = message if attempt == 0 else message + _ARCHITECT_HINT
            response = await architect.arun(
                msg,
                user_id=user_id,
                session_id=session_id,
                images=images or None,
                files=files or None,
            )
            plan = response.content
            if isinstance(plan, ArchitectPlan):
                return plan
            logger.warning(f"Architect tentativo {attempt + 1}: tipo inatteso {type(plan)}")
            last_exc = ValueError(f"tipo inatteso: {type(plan)}")
        except Exception as e:
            logger.warning(f"Architect tentativo {attempt + 1} fallito: {e}")
            last_exc = e

    logger.error(f"Architect fallito dopo 2 tentativi, uso fallback. Ultimo errore: {last_exc}")
    return _FALLBACK_PLAN


async def get_plan(architect: Agent, task_description: str) -> ArchitectPlan:
    """Esegue solo l'Architetto per ottenere un piano pre-calcolato, senza eseguire il team."""
    now = datetime.now(_TZ)
    date_str = now.strftime("%d %B %Y, %H:%M %Z")
    message = (
        f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n"
        f"{task_description}"
    )
    return await run_architect(
        architect,
        message,
        user_id="scheduler",
        session_id="architect_scheduler",
    )


async def select_rss_feeds(query: str) -> list[dict]:
    feeds = rss_store.get_all_feeds()
    if not feeds:
        return []
    if len(feeds) <= 5:
        return feeds
    feeds_text = "\n".join(
        f"{i}. [{f['name']}] {f['description']}"
        for i, f in enumerate(feeds)
    )
    selector = Agent(
        name="FeedSelector",
        model=Gemini(id="gemini-2.5-flash", generation_config={"temperature": 0.1}),
        instructions=(
            "Sei un selettore di feed RSS. "
            "Data una query e una lista di feed con descrizioni, "
            "seleziona i feed più rilevanti. "
            "Rispondi SOLO con una lista JSON di indici interi, es: [0, 2, 4]. "
            "Nessun testo aggiuntivo."
        ),
    )
    try:
        response = await selector.arun(
            f"Feed disponibili:\n{feeds_text}\n\nQuery: {query}\n\n"
            "Seleziona i feed più rilevanti (massimo 5). Rispondi SOLO con lista JSON di indici."
        )
        raw = (response.content or "").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1:
            return feeds[:5]
        indices = json.loads(raw[start:end + 1])
        return [feeds[i] for i in indices if 0 <= i < len(feeds)][:5]
    except Exception:
        logger.debug("Selezione feed RSS fallita, uso i primi 5", exc_info=True)
        return feeds[:5]


async def make_search_team(query: str) -> Team:
    selected_feeds = await select_rss_feeds(query)

    members: list[Agent] = [
        _make_search_agent(),
        _make_wikipedia_agent(),
    ]
    for feed in selected_feeds:
        members.append(_make_rss_agent(feed["url"], feed["name"], feed.get("description", "")))

    return Team(
        name="Search Team",
        mode="broadcast",
        model=Gemini(id="gemini-2.5-flash"),
        members=members,
        instructions=(
            _base_instructions()
            + " Sei il coordinatore del Search Team. "
            "Ricevi i risultati paralleli di tutti gli agenti di ricerca (web, Wikipedia, RSS). "
            "Produci un unico messaggio strutturato con:\n"
            "1) Descrizione ampia di quanto trovato dalle varie fonti;\n"
            "2) Lista completa degli URL rilevanti da approfondire (formato: '## URL' seguito da elenco).\n"
            "Includi tutti gli URL trovati: verranno filtrati dallo ScraperAgent."
        ),
        debug_mode=True,
        debug_level=2,
    )
