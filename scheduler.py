import logging
import sqlite3
import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

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

    _load_reminders_from_db(scheduler)
    _setup_calendar_sync(scheduler)


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


# ── Reminder (one-shot) ────────────────────────────────────────────────────────

def _load_reminders_from_db(scheduler: AsyncIOScheduler) -> None:
    from reminders_store import get_all_reminders
    for row in get_all_reminders():
        reminder_id, _, fire_at_iso, _, _, _ = row
        job_id = f"rem_{reminder_id}"
        if scheduler.get_job(job_id) is not None:
            continue
        try:
            fire_at = datetime.fromisoformat(fire_at_iso).astimezone(_TZ)
            scheduler.add_job(
                execute_reminder,
                trigger=DateTrigger(run_date=fire_at, timezone=_TZ),
                id=job_id,
                args=[reminder_id],
                misfire_grace_time=3600,
                replace_existing=True,
            )
            logger.info("Loaded reminder %s (fire_at=%s) from DB", reminder_id, fire_at_iso)
        except Exception:
            logger.exception("Failed to load reminder %s from DB", reminder_id)


def _setup_calendar_sync(scheduler: AsyncIOScheduler) -> None:
    """Registra un job ricorrente (ogni 30 min) che rimuove reminder orfani."""
    job_id = "_calendar_reminder_sync"
    if scheduler.get_job(job_id) is None:
        scheduler.add_job(
            _sync_calendar_reminders,
            trigger=CronTrigger(minute="*/30", timezone=_TZ),
            id=job_id,
            replace_existing=True,
        )
        logger.info("Calendar reminder sync job registered (every 30 min)")


async def execute_reminder(reminder_id: str) -> None:
    if _bot_app is None:
        logger.error("Bot app not set, skipping reminder %s", reminder_id)
        return

    from reminders_store import get_reminder, delete_reminder
    row = get_reminder(reminder_id)
    if not row:
        logger.warning("Reminder %s not found in DB, skipping", reminder_id)
        return

    _, message, _, _, calendar_event_title, chat_id = row

    prefix = ""
    if calendar_event_title:
        prefix = f"📅 *{calendar_event_title}* — tra poco!\n\n"

    try:
        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ *Promemoria*\n\n{prefix}{message}",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Error sending reminder %s", reminder_id)
    finally:
        delete_reminder(reminder_id)
        job = _bot_app.bot  # just to avoid unused var — actual removal below
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler as _Sched
        except Exception:
            pass
        logger.info("Reminder %s fired and deleted", reminder_id)


async def _sync_calendar_reminders() -> None:
    """Rimuove reminder collegati a eventi calendario che non esistono più."""
    from reminders_store import get_calendar_reminders, delete_reminder
    rows = get_calendar_reminders()
    if not rows:
        return

    try:
        from calendar_tools import _get_service
        service = _get_service()
    except Exception:
        logger.debug("Calendar sync: impossibile connettersi a Google Calendar")
        return

    for row in rows:
        reminder_id, _, _, calendar_event_id, calendar_event_title, _ = row
        try:
            service.events().get(calendarId="primary", eventId=calendar_event_id).execute()
        except Exception:
            logger.info(
                "Calendar event %s not found, removing reminder %s", calendar_event_id, reminder_id
            )
            delete_reminder(reminder_id)
            job_id = f"rem_{reminder_id}"
            if _bot_app is not None:
                pass  # scheduler reference needed — handled via global below


def _remove_reminder_job(scheduler: AsyncIOScheduler, reminder_id: str) -> None:
    job_id = f"rem_{reminder_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def make_reminder_tools(scheduler: AsyncIOScheduler, get_chat_id):
    """
    Ritorna i tool per ReminderAgent.
    - scheduler: running AsyncIOScheduler
    - get_chat_id: callable() -> int
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    async def create_reminder(message: str, fire_at_iso: str) -> str:
        """
        Crea un promemoria one-shot che scatta una volta alla data/ora specificata.

        Args:
            message: Testo del promemoria da inviare.
            fire_at_iso: Data e ora in formato ISO 8601 (es. "2026-04-20T09:00:00"). Usa il fuso orario Europe/Rome.
        """
        from reminders_store import save_reminder
        try:
            fire_at = datetime.fromisoformat(fire_at_iso).replace(tzinfo=_TZ) \
                if datetime.fromisoformat(fire_at_iso).tzinfo is None \
                else datetime.fromisoformat(fire_at_iso).astimezone(_TZ)
        except ValueError:
            return f"Formato data non valido: {fire_at_iso!r}. Usa ISO 8601 (es. '2026-04-20T09:00:00')."

        chat_id = get_chat_id()
        reminder_id = save_reminder(message=message, fire_at=fire_at, chat_id=chat_id)

        scheduler.add_job(
            execute_reminder,
            trigger=DateTrigger(run_date=fire_at, timezone=_TZ),
            id=f"rem_{reminder_id}",
            args=[reminder_id],
            misfire_grace_time=3600,
            replace_existing=True,
        )

        fire_fmt = fire_at.strftime("%d/%m/%Y alle %H:%M")
        logger.info("Created reminder %s: %r @ %s", reminder_id, message, fire_at_iso)
        return (
            f"Promemoria creato!\n"
            f"ID: `{reminder_id}`\n"
            f"Messaggio: {message}\n"
            f"Scatta: {fire_fmt}"
        )

    async def create_calendar_reminder(
        event_title_or_id: str,
        message: str = "",
        minutes_before: int = 10,
    ) -> str:
        """
        Crea un promemoria one-shot collegato a un evento del calendario Google.
        Il promemoria scatta N minuti prima dell'evento.

        Args:
            event_title_or_id: Titolo (anche parziale) o ID dell'evento Google Calendar.
            message: Testo del promemoria. Se vuoto, usa il titolo dell'evento.
            minutes_before: Minuti prima dell'evento a cui inviare il promemoria (default 10).
        """
        from calendar_tools import get_event_by_title_or_id
        from reminders_store import save_reminder
        from datetime import timedelta

        event = get_event_by_title_or_id(event_title_or_id)
        if not event:
            return f"Nessun evento trovato per: \"{event_title_or_id}\"."

        event_title = event.get("summary", "(senza titolo)")
        event_id = event["id"]
        start_raw = event["start"].get("dateTime", event["start"].get("date", ""))

        try:
            event_start = datetime.fromisoformat(start_raw).astimezone(_TZ)
        except Exception:
            return f"Impossibile leggere la data dell'evento: {start_raw!r}"

        fire_at = event_start - timedelta(minutes=minutes_before)
        now = datetime.now(_TZ)
        if fire_at <= now:
            fire_fmt = event_start.strftime("%d/%m/%Y %H:%M")
            return (
                f"L'evento \"{event_title}\" inizia il {fire_fmt}, "
                f"troppo presto per un promemoria di {minutes_before} min."
            )

        reminder_message = message.strip() or f"Tra {minutes_before} minuti: {event_title}"
        chat_id = get_chat_id()
        reminder_id = save_reminder(
            message=reminder_message,
            fire_at=fire_at,
            chat_id=chat_id,
            calendar_event_id=event_id,
            calendar_event_title=event_title,
        )

        scheduler.add_job(
            execute_reminder,
            trigger=DateTrigger(run_date=fire_at, timezone=_TZ),
            id=f"rem_{reminder_id}",
            args=[reminder_id],
            misfire_grace_time=3600,
            replace_existing=True,
        )

        fire_fmt = fire_at.strftime("%d/%m/%Y alle %H:%M")
        logger.info(
            "Created calendar reminder %s for event %s @ %s", reminder_id, event_id, fire_at
        )
        return (
            f"Promemoria calendario creato!\n"
            f"Evento: {event_title}\n"
            f"Scatta: {fire_fmt} ({minutes_before} min prima)\n"
            f"ID: `{reminder_id}`"
        )

    async def list_reminders() -> str:
        """Mostra i promemoria one-shot attivi con pulsanti per eliminarli."""
        from reminders_store import get_all_reminders

        rows = get_all_reminders()
        if not rows:
            return "Nessun promemoria attivo al momento."

        chat_id = get_chat_id()
        lines = ["*Promemoria attivi:*\n"]
        keyboard = []

        for i, (rid, message, fire_at_iso, _, cal_title, _) in enumerate(rows, 1):
            try:
                dt = datetime.fromisoformat(fire_at_iso).astimezone(_TZ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                dt = fire_at_iso[:16]
            cal_tag = f" 📅 _{cal_title}_" if cal_title else ""
            preview = message[:60] + ("…" if len(message) > 60 else "")
            lines.append(f"{i}. ⏰ {dt}{cal_tag}\n   {preview}\n   ID: `{rid}`\n")
            keyboard.append([
                InlineKeyboardButton(f"🗑 Elimina {rid}", callback_data=f"rem_del:{rid}"),
            ])

        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        )
        return ""

    async def delete_reminder(reminder_id: str) -> str:
        """
        Elimina un promemoria dato il suo ID.

        Args:
            reminder_id: ID del promemoria (8 caratteri esadecimali).
        """
        from reminders_store import delete_reminder as _del
        deleted = _del(reminder_id)
        _remove_reminder_job(scheduler, reminder_id)
        if deleted:
            logger.info("Deleted reminder %s", reminder_id)
            return f"Promemoria `{reminder_id}` eliminato."
        return f"Promemoria `{reminder_id}` non trovato."

    return [create_reminder, create_calendar_reminder, list_reminders, delete_reminder]
