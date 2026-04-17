import logging
import sqlite3
import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from agent import AIAgent, ArchitectPlan

_TZ = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)

_DB_PATH = "tmp/agent_data.db"
_APSCHEDULER_DB = "sqlite:///tmp/apscheduler_jobs.db"

# ── Executor context (set at startup) ─────────────────────────────────────────

_ai_agent: "AIAgent | None" = None
_bot_app = None


def set_executor_context(ai_agent: "AIAgent", bot_app) -> None:
    global _ai_agent, _bot_app
    _ai_agent = ai_agent
    _bot_app = bot_app


# ── SQLite store ───────────────────────────────────────────────────────────────

def init_schedules_table() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id          TEXT PRIMARY KEY,
                user_message TEXT NOT NULL,
                cron_expr   TEXT NOT NULL,
                architect_plan_json TEXT NOT NULL,
                chat_id     INTEGER NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)


def _all_schedules() -> list[tuple]:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, user_message, cron_expr, created_at FROM schedules ORDER BY created_at"
        ).fetchall()


def _get_schedule(schedule_id: str) -> tuple | None:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT id, user_message, cron_expr, architect_plan_json, chat_id "
            "FROM schedules WHERE id = ?",
            (schedule_id,),
        ).fetchone()


def _save_schedule(
    schedule_id: str,
    user_message: str,
    cron_expr: str,
    plan_json: str,
    chat_id: int,
) -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO schedules (id, user_message, cron_expr, architect_plan_json, chat_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (schedule_id, user_message, cron_expr, plan_json, chat_id, datetime.now(_TZ).isoformat()),
        )


def _delete_schedule_db(schedule_id: str) -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def _update_plan(schedule_id: str, plan_json: str) -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE schedules SET architect_plan_json = ? WHERE id = ?",
            (plan_json, schedule_id),
        )


# ── Scheduler setup ────────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=_APSCHEDULER_DB)},
        timezone=_TZ,
    )


def load_jobs_from_db(scheduler: AsyncIOScheduler) -> None:
    """Ensure every schedule in the DB has a corresponding APScheduler job."""
    for row in _all_schedules():
        schedule_id, _, cron_expr, _ = row
        if scheduler.get_job(schedule_id) is None:
            try:
                trigger = CronTrigger.from_crontab(cron_expr, timezone=_TZ)
                scheduler.add_job(
                    execute_schedule,
                    trigger=trigger,
                    id=schedule_id,
                    args=[schedule_id],
                    misfire_grace_time=300,
                    replace_existing=True,
                )
                logger.info("Loaded schedule %s (%s) from DB", schedule_id, cron_expr)
            except Exception:
                logger.exception("Failed to load schedule %s (%s) from DB", schedule_id, cron_expr)


# ── Executor ───────────────────────────────────────────────────────────────────

async def execute_schedule(schedule_id: str) -> None:
    if _ai_agent is None or _bot_app is None:
        logger.error("Executor context not set, skipping schedule %s", schedule_id)
        return

    row = _get_schedule(schedule_id)
    if not row:
        logger.warning("Schedule %s not found in DB, skipping", schedule_id)
        return

    _, user_message, _, plan_json, chat_id = row

    try:
        from agent import ArchitectPlan
        plan: ArchitectPlan = ArchitectPlan.model_validate_json(plan_json)
        result, _ = await _ai_agent.run_from_plan(plan, user_message)
        await _bot_app.bot.send_message(chat_id=chat_id, text=result, parse_mode="Markdown")
    except Exception:
        logger.exception("Error executing schedule %s", schedule_id)
        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text=f"Errore nell'esecuzione della sveglia `{schedule_id}`.",
            parse_mode="Markdown",
        )


# ── Public helpers for callback buttons ───────────────────────────────────────

def delete_schedule_and_job(schedule_id: str, scheduler: AsyncIOScheduler) -> str:
    if not _get_schedule(schedule_id):
        return f"Sveglia `{schedule_id}` non trovata."
    _delete_schedule_db(schedule_id)
    job = scheduler.get_job(schedule_id)
    if job:
        scheduler.remove_job(schedule_id)
    logger.info("Deleted schedule %s", schedule_id)
    return f"Sveglia `{schedule_id}` eliminata."


async def refresh_schedule_plan(schedule_id: str, ai_agent: "AIAgent") -> str:
    row = _get_schedule(schedule_id)
    if not row:
        return f"Sveglia `{schedule_id}` non trovata."
    _, user_message, _, _, _ = row
    plan = await ai_agent._get_plan(user_message)
    _update_plan(schedule_id, plan.model_dump_json())
    logger.info("Refreshed plan for schedule %s", schedule_id)
    return f"Piano della sveglia `{schedule_id}` aggiornato."


# ── Tools factory for SchedulerAgent ──────────────────────────────────────────

def make_scheduler_tools(scheduler: AsyncIOScheduler, ai_agent: "AIAgent", get_chat_id):
    """
    Returns async tool functions to be passed to the SchedulerAgent.
    - scheduler: the running AsyncIOScheduler
    - ai_agent: AIAgent instance (for pre-baking Architect plans)
    - get_chat_id: callable() -> int, returns the current user's chat_id
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    async def create_schedule(task_description: str, cron_expr: str) -> str:
        """
        Crea una sveglia ricorrente.

        Args:
            task_description: Descrizione completa del task da eseguire ad ogni scatto (es. "Recupera le top 10 notizie di Hacker News e riassumile").
            cron_expr: Espressione cron standard a 5 campi (es. "0 8 * * *" per ogni giorno alle 08:00 CET).
        """
        chat_id = get_chat_id()
        schedule_id = uuid.uuid4().hex[:8]

        plan = await ai_agent._get_plan(task_description)
        plan_json = plan.model_dump_json()

        _save_schedule(schedule_id, task_description, cron_expr, plan_json, chat_id)

        trigger = CronTrigger.from_crontab(cron_expr, timezone=_TZ)
        scheduler.add_job(
            execute_schedule,
            trigger=trigger,
            id=schedule_id,
            args=[schedule_id],
            misfire_grace_time=300,
            replace_existing=True,
        )

        logger.info("Created schedule %s: %r @ %s", schedule_id, task_description, cron_expr)
        return (
            f"Sveglia creata con successo!\n"
            f"ID: `{schedule_id}`\n"
            f"Task: {task_description}\n"
            f"Schedule: `{cron_expr}` (CET)"
        )

    async def list_schedules() -> str:
        """Mostra le sveglie attive con pulsanti per eliminarle o aggiornare il piano."""
        rows = _all_schedules()
        if not rows:
            return "Nessuna sveglia attiva al momento."

        chat_id = get_chat_id()
        lines = ["*Sveglie attive:*\n"]
        keyboard = []

        for i, (sid, user_msg, cron_expr, created_at) in enumerate(rows, 1):
            dt = datetime.fromisoformat(created_at).strftime("%d/%m/%Y %H:%M")
            lines.append(f"{i}. _{user_msg}_\n   ⏰ `{cron_expr}` | creata {dt}\n   ID: `{sid}`\n")
            keyboard.append([
                InlineKeyboardButton(f"🗑 Elimina {sid}", callback_data=f"sched_del:{sid}"),
                InlineKeyboardButton(f"🔄 Refresh {sid}", callback_data=f"sched_ref:{sid}"),
            ])

        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ""

    async def delete_schedule(schedule_id: str) -> str:
        """
        Elimina una sveglia dato il suo ID.

        Args:
            schedule_id: ID della sveglia (8 caratteri esadecimali).
        """
        return delete_schedule_and_job(schedule_id, scheduler)

    async def refresh_schedule(schedule_id: str) -> str:
        """
        Rigenera il piano dell'Architetto per una sveglia esistente.
        Utile dopo aver aggiunto nuovi agenti al catalogo.

        Args:
            schedule_id: ID della sveglia (8 caratteri esadecimali).
        """
        return await refresh_schedule_plan(schedule_id, ai_agent)

    return [create_schedule, list_schedules, delete_schedule, refresh_schedule]
