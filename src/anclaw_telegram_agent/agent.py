import asyncio
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.media import File, Image
from agno.models.google import Gemini
from agno.team import Team
from agno.run.team import TeamRunEvent

from .rss_feeds import RSS_FEEDS

from . import memory_store
from . import notes_store
from . import rss_store

from .agent_models import AgentSpec, ArchitectPlan, _FALLBACK_PLAN  # noqa: F401 (re-export)
from .agent_catalog import (
    _TZ,
    _CUTOFF,
    _TOOL_LABELS,
    _AGENT_CATALOG,
    _base_instructions,
    _make_pure_llm_agent,
    _make_reminder_agent,
)
from .agent_router import (
    _ARCHITECT_INSTRUCTIONS,
    run_architect,
    get_plan,
    make_search_team,
    make_news_team,
)

_DB_PATH = "tmp/agent_data.db"
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

logger = logging.getLogger(__name__)

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
    lower = message.lower().strip()
    for trigger in _QUICK_NOTE_TRIGGERS:
        if lower.startswith(trigger):
            return message[len(trigger):].strip()
    return None


def _extract_explicit_fact(message: str) -> str | None:
    lower = message.lower().strip()
    for trigger in _EXPLICIT_TRIGGERS:
        if lower.startswith(trigger):
            return message[len(trigger):].strip()
    return None


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
        rss_store.init_rss_table()
        rss_store.seed_feeds(RSS_FEEDS)

        from .scheduler import create_scheduler
        self._scheduler = create_scheduler()

    @property
    def scheduler(self):
        return self._scheduler

    def _make_synth_agent(self) -> Agent:
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

    def _make_scheduler_agent(self) -> Agent:
        from .scheduler import make_scheduler_tools
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

    async def _extract_and_save_facts(self, message: str) -> None:
        try:
            response = await self._fact_extractor.arun(message)
            raw = (response.content or "").strip()
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return
            facts: list[str] = json.loads(raw[start:end + 1])
            for fact in facts:
                fact = fact.strip()
                if fact and not memory_store.fact_exists(fact):
                    memory_store.save_fact(fact, source="auto")
                    logger.info("Fatto salvato automaticamente: %r", fact)
        except Exception:
            logger.debug("Estrazione fatti fallita (non bloccante)", exc_info=True)

    def reset_user_sessions(self, user_id: int) -> None:
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

    async def _get_plan(self, task_description: str) -> ArchitectPlan:
        return await get_plan(self._architect, task_description)

    async def _build_members(self, plan: ArchitectPlan, message: str) -> list:
        members: list = []
        for spec in plan.agents:
            if spec.name == "SearchTeam":
                members.append(await make_search_team(message))
            elif spec.name == "NewsTeam":
                members.append(await make_news_team(message))
            elif spec.name == "SynthAgent":
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
        members = await self._build_members(plan, message)

        if not members:
            logger.warning("Nessun agente nel piano, fallback a risposta diretta")
            return "Non ho trovato agenti adatti per questa richiesta. Riprova.", []

        request_session_id = f"team_{user_id}_{uuid.uuid4().hex[:8]}"
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
            add_history_to_context=False,
            session_id=request_session_id,
            markdown=True,
            debug_mode=True,
            debug_level=2,
        )

        content_parts: list[str] = []
        generated_files: list[File] = []

        stream = team.arun(
            message,
            user_id=str(user_id),
            session_id=request_session_id,
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

        explicit_fact = _extract_explicit_fact(message)
        if explicit_fact:
            if not memory_store.fact_exists(explicit_fact):
                memory_store.save_fact(explicit_fact, source="explicit")
                logger.info("Fatto esplicito salvato: %r", explicit_fact)
        else:
            asyncio.create_task(self._extract_and_save_facts(message))

        now = datetime.now(_TZ)
        date_str = now.strftime("%d %B %Y, %H:%M %Z")
        architect_message = (
            f"[Contesto: oggi è {date_str}, knowledge cutoff modello {_CUTOFF}]\n\n{message}"
        )
        plan = await run_architect(
            self._architect,
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

        if on_event and plan.intermediate_message:
            await on_event(plan.intermediate_message)

        return await self.run_from_plan(
            plan,
            message,
            user_id=user_id,
            images=images,
            files=files,
            on_event=on_event,
        )
