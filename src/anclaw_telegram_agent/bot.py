import asyncio
import io
import logging
import os

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agno.media import File, Image

from .agent import AIAgent
from .config import BotMode, Config
from .markdown_utils import md_to_telegram
from .sender import TelegramSender
from .transcriber import AudioTranscriber

logger = logging.getLogger(__name__)

# MIME types dei documenti supportati (no video)
_SUPPORTED_DOC_MIMES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
}

# MIME types immagine inviati come file (non compressi da Telegram)
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Mappa mime → formato stringa per agno Image
_IMAGE_FORMAT = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_TELEGRAM_MAX_LENGTH = 4096
_FILE_THRESHOLD = _TELEGRAM_MAX_LENGTH * 2  # > 8192 chars → send as file


def _chunk_text(text: str, max_length: int = _TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split text into chunks that fit within Telegram's message size limit.

    Tries to split at paragraph boundaries, then newlines, then spaces,
    falling back to a hard cut only if no whitespace is found.
    """
    if len(text) <= max_length:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return [c for c in chunks if c]


# Parole chiave che suggeriscono una richiesta di scheduling o promemoria
_SCHEDULING_KEYWORDS = frozenset({
    "imposta", "metti", "programma", "sveglia", "promemoria",
    "ricordami", "ricorda", "schedula", "schedule",
    "ogni giorno", "ogni mattina", "ogni sera", "ogni notte",
    "ogni settimana", "ogni mese", "ogni ora",
    "ogni lunedì", "ogni martedì", "ogni mercoledì",
    "ogni giovedì", "ogni venerdì", "ogni sabato", "ogni domenica",
    "lista sveglie", "mostra sveglie", "elimina sveglia", "cancella sveglia",
})

_REMINDER_KEYWORDS = frozenset({
    "promemoria", "ricordami", "avvisami", "notificami", "reminder",
    "avviso", "notifica", "tra ", "prima dell", "prima di",
    "lista promemoria", "mostra promemoria", "elimina promemoria", "cancella promemoria",
})


def _is_scheduling_hint(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _SCHEDULING_KEYWORDS)


def _is_reminder_hint(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _REMINDER_KEYWORDS)


class TelegramBot:
    def __init__(self, config: Config, agent: AIAgent):
        self.config = config
        self.agent = agent
        self.transcriber = AudioTranscriber()
        self.sender = TelegramSender(config)

        scheduler = agent.scheduler

        async def _post_init(app: Application) -> None:
            from .scheduler import init_schedules_table, load_jobs_from_db, set_executor_context
            from .reminders_store import init_reminders_table
            init_schedules_table()
            init_reminders_table()
            scheduler.start()
            load_jobs_from_db(scheduler)
            set_executor_context(agent, app)
            await app.bot.set_my_commands([
                BotCommand("start",      "Messaggio di benvenuto"),
                BotCommand("help",       "Guida all'uso del bot"),
                BotCommand("sveglie",    "Elenca le sveglie attive"),
                BotCommand("promemoria", "Elenca i promemoria one-shot attivi"),
                BotCommand("note",       "Mostra e gestisci gli appunti salvati"),
                BotCommand("ricordi",    "Mostra e gestisci i fatti memorizzati su di te"),
                BotCommand("status",     "Modalità bot, sveglie e sessioni in memoria"),
                BotCommand("reset",      "Cancella la memoria di sessione"),
            ])
            logger.info("APScheduler avviato, sveglie e promemoria caricati dal DB.")

        async def _post_shutdown(app: Application) -> None:
            if scheduler.running:
                scheduler.shutdown(wait=False)
            logger.info("APScheduler fermato.")

        self.app = (
            Application.builder()
            .token(config.telegram_token)
            .post_init(_post_init)
            .post_shutdown(_post_shutdown)
            .build()
        )
        self._register_handlers()

    def _register_handlers(self):
        allowed = self.config.allowed_user_ids
        if allowed:
            user_filter = filters.User(user_id=list(allowed))
            logger.info(f"Accesso limitato agli user ID: {sorted(allowed)}")
        else:
            user_filter = filters.ALL
            logger.warning("ALLOWED_USER_IDS non impostato: il bot risponde a tutti gli utenti.")

        self.app.add_handler(CommandHandler("start",      self._handle_start,      filters=user_filter))
        self.app.add_handler(CommandHandler("help",       self._handle_help,       filters=user_filter))
        self.app.add_handler(CommandHandler("reset",      self._handle_reset,      filters=user_filter))
        self.app.add_handler(CommandHandler("status",     self._handle_status,     filters=user_filter))
        self.app.add_handler(CommandHandler("sveglie",    self._handle_sveglie,    filters=user_filter))
        self.app.add_handler(CommandHandler("promemoria", self._handle_promemoria, filters=user_filter))
        self.app.add_handler(CommandHandler("note",       self._handle_note,       filters=user_filter))
        self.app.add_handler(CommandHandler("ricordi",    self._handle_ricordi,    filters=user_filter))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, self._handle_message)
        )
        # Vocali (OGG/OPUS)
        self.app.add_handler(
            MessageHandler(filters.VOICE & user_filter, self._handle_voice)
        )
        # File audio allegati (MP3, M4A, WAV…)
        self.app.add_handler(
            MessageHandler(filters.AUDIO & user_filter, self._handle_audio)
        )
        # Foto compresse da Telegram (sempre JPEG)
        self.app.add_handler(
            MessageHandler(filters.PHOTO & user_filter, self._handle_photo)
        )
        # Documenti: immagini non compresse + PDF/DOC/DOCX/XLSX/CSV
        self.app.add_handler(
            MessageHandler(filters.Document.ALL & user_filter, self._handle_document)
        )
        # Callback dai bottoni inline delle sveglie
        self.app.add_handler(
            CallbackQueryHandler(self._handle_schedule_callback, pattern=r"^sched_(del|ref):")
        )
        # Callback dai bottoni inline dei ricordi
        self.app.add_handler(
            CallbackQueryHandler(self._handle_memory_callback, pattern=r"^mem_del:")
        )
        # Callback dai bottoni inline delle note
        self.app.add_handler(
            CallbackQueryHandler(self._handle_note_callback, pattern=r"^note_del:")
        )
        # Callback dai bottoni inline dei promemoria
        self.app.add_handler(
            CallbackQueryHandler(self._handle_reminder_callback, pattern=r"^rem_del:")
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _safe_reply(message: Message, text: str) -> None:
        """Send a reply.

        - Up to 4096 chars: single message.
        - 4097–8192 chars: split into chunks.
        - Over 8192 chars: send as a .md file to keep the chat readable.
        """
        converted = md_to_telegram(text)

        if len(converted) > _FILE_THRESHOLD:
            buf = io.BytesIO(text.encode())
            buf.name = "response.md"
            await message.reply_document(
                document=buf,
                filename="response.md",
                caption="_La risposta è molto lunga, la trovi nel file allegato._",
                parse_mode="Markdown",
            )
            return

        for chunk in _chunk_text(converted):
            try:
                await message.reply_text(chunk, parse_mode="Markdown")
            except BadRequest:
                await message.reply_text(chunk)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Ciao! Sono il tuo assistente AI. Scrivimi qualcosa, mandami un vocale o un file."
        )

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Puoi inviarmi:\n"
            "• Testo\n"
            "• Vocali / file audio (MP3, M4A, WAV…)\n"
            "• Immagini (foto o file PNG/JPG/WEBP)\n"
            "• Documenti: PDF, DOC, DOCX, XLSX, CSV\n\n"
            "Usa la didascalia per darmi istruzioni sul file.\n\n"
            "*Comandi:*\n"
            "• /reset — cancella la memoria di sessione e riparte da zero\n"
            "• /status — mostra modalità bot, sveglie attive e sessioni in memoria\n"
            "• /sveglie — elenca le sveglie attive con pulsanti per eliminarle o aggiornarle\n\n"
            "*Sveglie ricorrenti:*\n"
            "• \"Programma ogni mattina alle 8 le notizie di HackerNews\"\n"
            "• \"Mostra le sveglie attive\"\n"
            "• \"Elimina la sveglia abc12345\"",
            parse_mode="Markdown",
        )

    async def _handle_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.agent.reset_user_sessions(user_id)
        logger.info(f"Reset sessione per user {user_id}")
        await update.message.reply_text(
            "Memoria di sessione cancellata. Ripartiamo da zero!"
        )

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import sqlite3
        from .scheduler import _all_schedules

        mode = self.config.mode.value.upper()

        schedules = _all_schedules()
        n_schedules = len(schedules)

        n_sessions = 0
        try:
            with sqlite3.connect(self.agent._db_path) as conn:
                for table in ("architect_sessions", "synth_sessions", "team_sessions"):
                    try:
                        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                        n_sessions += row[0] if row else 0
                    except sqlite3.OperationalError:
                        pass
        except Exception:
            logger.debug("Errore lettura sessioni per /status")

        text = (
            f"*Stato del bot*\n\n"
            f"Modalità: `{mode}`\n"
            f"Sveglie attive: `{n_schedules}`\n"
            f"Sessioni in memoria: `{n_sessions}`"
        )

        if schedules:
            text += "\n\n*Sveglie:*"
            for sid, user_msg, cron_expr, _ in schedules:
                preview = user_msg[:45] + ("…" if len(user_msg) > 45 else "")
                text += f"\n• `{sid}` — _{preview}_\n  ⏰ `{cron_expr}`"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_sveglie(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from datetime import datetime as _dt
        from .scheduler import _all_schedules

        rows = _all_schedules()
        if not rows:
            await update.message.reply_text("Nessuna sveglia attiva al momento.")
            return

        lines = ["*Sveglie attive:*\n"]
        keyboard = []
        for i, (sid, user_msg, cron_expr, created_at) in enumerate(rows, 1):
            dt = _dt.fromisoformat(created_at).strftime("%d/%m/%Y %H:%M")
            lines.append(
                f"{i}. _{user_msg}_\n   ⏰ `{cron_expr}` | creata {dt}\n   ID: `{sid}`\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"🗑 Elimina {sid}", callback_data=f"sched_del:{sid}"),
                InlineKeyboardButton(f"🔄 Refresh {sid}", callback_data=f"sched_ref:{sid}"),
            ])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_promemoria(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from datetime import datetime as _dt
        from .reminders_store import get_all_reminders
        from zoneinfo import ZoneInfo as _ZI

        _TZ = _ZI("Europe/Rome")
        rows = get_all_reminders()
        if not rows:
            await update.message.reply_text("Nessun promemoria attivo al momento.")
            return

        lines = ["*Promemoria attivi:*\n"]
        keyboard = []
        for rid, message, fire_at_iso, _, cal_title, _ in rows:
            try:
                dt = _dt.fromisoformat(fire_at_iso).astimezone(_TZ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                dt = fire_at_iso[:16]
            cal_tag = f" 📅 _{cal_title}_" if cal_title else ""
            preview = message[:60] + ("…" if len(message) > 60 else "")
            lines.append(f"⏰ {dt}{cal_tag}\n   {preview}\n   ID: `{rid}`\n")
            keyboard.append([
                InlineKeyboardButton(f"🗑 Elimina {rid}", callback_data=f"rem_del:{rid}"),
            ])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_reminder_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il pulsante di cancellazione di un promemoria."""
        query = update.callback_query
        await query.answer()

        _, reminder_id = query.data.split(":", 1)
        from .reminders_store import delete_reminder
        from .scheduler import _remove_reminder_job
        deleted = delete_reminder(reminder_id)
        _remove_reminder_job(self.agent.scheduler, reminder_id)

        await query.edit_message_reply_markup(reply_markup=None)
        if deleted:
            await query.message.reply_text(f"Promemoria `{reminder_id}` eliminato.", parse_mode="Markdown")
        else:
            await query.message.reply_text(f"Promemoria `{reminder_id}` non trovato (forse già eliminato).")

    async def _handle_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from datetime import datetime as _dt
        from .notes_store import get_all_notes

        notes = get_all_notes()
        if not notes:
            await update.message.reply_text(
                "Non hai ancora nessun appunto salvato.\n\n"
                "Puoi aggiungerne uno con:\n"
                "_\"Nota: chiamare il dentista\"_",
                parse_mode="Markdown",
            )
            return

        lines = ["*I tuoi appunti:*\n"]
        keyboard = []
        for note_id, content, created_at in notes:
            try:
                dt = _dt.fromisoformat(created_at).strftime("%d/%m/%Y %H:%M")
            except Exception:
                dt = created_at[:16]
            preview = content[:60] + ("…" if len(content) > 60 else "")
            lines.append(f"*[{note_id}]* {preview}  _({dt})_")
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 Elimina [{note_id}]: {content[:30]}{'…' if len(content) > 30 else ''}",
                    callback_data=f"note_del:{note_id}",
                )
            ])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_note_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il pulsante di cancellazione di una nota."""
        query = update.callback_query
        await query.answer()

        _, note_id_str = query.data.split(":", 1)
        from .notes_store import delete_note
        deleted = delete_note(int(note_id_str))

        await query.edit_message_reply_markup(reply_markup=None)
        if deleted:
            await query.message.reply_text(f"Nota {note_id_str} eliminata.")
        else:
            await query.message.reply_text(f"Nota {note_id_str} non trovata (forse già eliminata).")

    async def _handle_ricordi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from datetime import datetime as _dt
        from .memory_store import get_all_facts

        facts = get_all_facts()
        if not facts:
            await update.message.reply_text(
                "Non ho ancora memorizzato nessun fatto su di te.\n\n"
                "Puoi dirmi qualcosa con:\n"
                "_\"Ricordati che abito a Milano\"_",
                parse_mode="Markdown",
            )
            return

        lines = ["*Fatti che ricordo su di te:*\n"]
        keyboard = []
        for fact_id, fact, source, created_at in facts:
            try:
                dt = _dt.fromisoformat(created_at).strftime("%d/%m/%Y")
            except Exception:
                dt = created_at[:10]
            tag = "📌" if source == "explicit" else "🔍"
            lines.append(f"{tag} _{fact}_ ({dt})")
            keyboard.append([
                InlineKeyboardButton(f"🗑 Dimentica: {fact[:30]}{'…' if len(fact) > 30 else ''}",
                                     callback_data=f"mem_del:{fact_id}")
            ])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_memory_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il pulsante di cancellazione di un fatto memorizzato."""
        query = update.callback_query
        await query.answer()

        _, fact_id_str = query.data.split(":", 1)
        from .memory_store import delete_fact
        deleted = delete_fact(int(fact_id_str))

        await query.edit_message_reply_markup(reply_markup=None)
        if deleted:
            await query.message.reply_text("Fatto dimenticato.", parse_mode="Markdown")
        else:
            await query.message.reply_text("Fatto non trovato (forse già eliminato).")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        logger.info(f"Testo ricevuto da {update.effective_user.id}: {text!r}")

        # Aggiungi hint di scheduling se rilevato, per aiutare l'Architetto a instradare
        from .agent import _extract_explicit_fact, _extract_quick_note
        if _extract_explicit_fact(text):
            # Fatto esplicito: risposta diretta senza passare per il team
            await update.message.reply_text("Fatto memorizzato.", parse_mode="Markdown")
            return

        quick_note = _extract_quick_note(text)
        if quick_note is not None:
            # Nota rapida: salva direttamente senza passare per il team
            from .notes_store import save_note
            note_id = save_note(quick_note)
            await update.message.reply_text(
                f"Nota salvata (ID: {note_id}).", parse_mode="Markdown"
            )
            return

        if _is_reminder_hint(text):
            text = f"[HINT: possibile richiesta di promemoria one-shot]\n{text}"
        elif _is_scheduling_hint(text):
            text = f"[HINT: possibile richiesta di gestione sveglie/scheduling]\n{text}"

        await self._process_and_reply(update, context, text)

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"Vocale ricevuto da {update.effective_user.id} ({update.message.voice.duration}s)")
        audio_bytes = await self._download_file(update.message.voice, context)
        await self._transcribe_and_process(update, context, audio_bytes, mime_type="audio/ogg")

    async def _handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        audio = update.message.audio
        mime_type = audio.mime_type or "audio/mpeg"
        logger.info(f"Audio ricevuto da {update.effective_user.id} — {audio.file_name!r}")
        audio_bytes = await self._download_file(audio, context)
        await self._transcribe_and_process(update, context, audio_bytes, mime_type=mime_type)

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Telegram invia più risoluzioni: prendiamo la più alta (ultima della lista)
        photo = update.message.photo[-1]
        caption = update.message.caption or "Descrivi o analizza questa immagine."
        logger.info(f"Foto ricevuta da {update.effective_user.id} — caption: {caption!r}")

        photo_bytes = await self._download_file(photo, context)

        uploads_dir = os.path.join(os.path.dirname(__file__), "tmp", "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        saved_path = os.path.join(uploads_dir, f"{photo.file_unique_id}.jpg")
        with open(saved_path, "wb") as fh:
            fh.write(photo_bytes)
        caption = f"[FILE SALVATO: {saved_path}]\n{caption}"

        images = [Image(content=photo_bytes, format="jpeg")]
        await self._process_and_reply(update, context, caption, images=images)

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        doc = update.message.document
        mime_type = doc.mime_type or ""
        caption = update.message.caption or "Analizza questo file."
        logger.info(f"Documento ricevuto da {update.effective_user.id} — {doc.file_name!r}, mime: {mime_type}")

        if mime_type in _IMAGE_MIMES:
            # Immagine inviata come file (non compressa)
            img_bytes = await self._download_file(doc, context)
            fmt = _IMAGE_FORMAT.get(mime_type, "jpeg")
            images = [Image(content=img_bytes, format=fmt)]
            await self._process_and_reply(update, context, caption, images=images)

        elif mime_type in _SUPPORTED_DOC_MIMES:
            # Documento testuale / foglio di calcolo
            doc_bytes = await self._download_file(doc, context)

            # Salva su disco: CodeAgent e DriveAgent accedono al file tramite path
            uploads_dir = os.path.join(os.path.dirname(__file__), "tmp", "uploads")
            os.makedirs(uploads_dir, exist_ok=True)
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (doc.file_name or "file"))
            saved_path = os.path.join(uploads_dir, f"{doc.file_unique_id}_{safe_name}")
            with open(saved_path, "wb") as fh:
                fh.write(doc_bytes)
            caption = f"[FILE SALVATO: {saved_path}]\n{caption}"
            logger.info(f"File salvato in: {saved_path}")

            files = [File(content=doc_bytes, content_type=mime_type)]
            await self._process_and_reply(update, context, caption, files=files)

        else:
            await self._safe_reply(
                update.message,
                f"Formato non supportato: `{mime_type or doc.file_name}`\n"
                "Accetto: immagini, PDF, DOC, DOCX, XLSX, CSV.",
            )

    async def _handle_schedule_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce i pulsanti inline delle sveglie (elimina / refresh)."""
        query = update.callback_query
        await query.answer()

        data = query.data  # "sched_del:abc123" o "sched_ref:abc123"
        action, schedule_id = data.split(":", 1)

        from .scheduler import delete_schedule_and_job, refresh_schedule_plan

        if action == "sched_del":
            msg = delete_schedule_and_job(schedule_id, self.agent.scheduler)
        elif action == "sched_ref":
            msg = await refresh_schedule_plan(schedule_id, self.agent)
        else:
            msg = "Azione sconosciuta."

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ {msg}", parse_mode="Markdown")

    # ── Logica condivisa ───────────────────────────────────────────────────────

    async def _download_file(self, file_obj, context: ContextTypes.DEFAULT_TYPE) -> bytes:
        tg_file = await context.bot.get_file(file_obj.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        return buf.getvalue()

    async def _keep_typing(self, chat_id: int, bot, stop_event: asyncio.Event):
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)

    async def _process_and_reply(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        images: list[Image] | None = None,
        files: list[File] | None = None,
    ):
        chat_id = update.effective_chat.id

        async def on_event(status: str) -> None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=status, parse_mode="Markdown"
                )
            except Exception:
                logger.debug("Impossibile inviare stato evento Telegram")

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            self._keep_typing(chat_id, context.bot, stop_typing)
        )

        generated_files: list[File] = []
        try:
            response, generated_files = await self.agent.run(
                user_id=update.effective_user.id,
                chat_id=chat_id,
                message=text,
                images=images,
                files=files,
                on_event=on_event,
            )
        except Exception:
            logger.exception("Errore durante l'elaborazione")
            response = "Si è verificato un errore interno. Riprova più tardi."
        finally:
            stop_typing.set()
            typing_task.cancel()

        if response.strip():
            await self._safe_reply(update.message, response)
        await self._send_generated_files(update, generated_files)

    async def _transcribe_and_process(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        audio_bytes: bytes,
        mime_type: str,
    ):
        chat_id = update.effective_chat.id

        async def on_event(status: str) -> None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id, text=status, parse_mode="Markdown"
                )
            except Exception:
                logger.debug("Impossibile inviare stato evento Telegram")

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            self._keep_typing(chat_id, context.bot, stop_typing)
        )

        generated_files: list[File] = []
        try:
            transcription = await self.transcriber.transcribe(audio_bytes, mime_type)
            logger.info(f"Testo trascritto: {transcription!r}")
            await self._safe_reply(update.message, f"_Trascrizione:_ {transcription}")
            response, generated_files = await self.agent.run(
                user_id=update.effective_user.id,
                chat_id=chat_id,
                message=transcription,
                on_event=on_event,
            )
        except Exception:
            logger.exception("Errore durante la trascrizione o elaborazione audio")
            response = "Si è verificato un errore interno. Riprova più tardi."
        finally:
            stop_typing.set()
            typing_task.cancel()

        if response.strip():
            await self._safe_reply(update.message, response)
        await self._send_generated_files(update, generated_files)

    async def _send_generated_files(self, update: Update, files: list[File]) -> None:
        """Invia a Telegram i file generati dagli agenti."""
        for file in files:
            try:
                filename = file.filename or "file"

                if file.filepath:
                    import os as _os
                    if not _os.path.isfile(str(file.filepath)):
                        logger.warning(f"File non trovato su disco: {file.filepath}")
                        continue
                    with open(file.filepath, "rb") as fh:
                        data = fh.read()
                elif file.content:
                    data = (
                        file.content
                        if isinstance(file.content, bytes)
                        else file.content.encode()
                    )
                else:
                    logger.warning(f"File {filename!r} senza contenuto né percorso, skip.")
                    continue

                buf = io.BytesIO(data)
                buf.name = filename
                mime = file.mime_type or ""

                if mime in _IMAGE_MIMES:
                    await update.message.reply_photo(photo=buf)
                else:
                    await update.message.reply_document(document=buf, filename=filename)

                logger.info(f"File inviato a Telegram: {filename!r}")
            except Exception:
                logger.exception(f"Errore nell'invio del file {getattr(file, 'filename', '?')!r}")

    # ── Avvio ─────────────────────────────────────────────────────────────────

    def run(self):
        if self.config.mode is BotMode.WEBHOOK:
            self._run_webhook()
        else:
            self._run_polling()

    def _run_polling(self):
        logger.info("Avvio in modalità POLLING — nessun server richiesto.")
        self.app.run_polling(drop_pending_updates=True)

    def _run_webhook(self):
        wh = self.config.webhook
        logger.info(f"Avvio in modalità WEBHOOK — ascolto su porta {wh.port}, path {wh.path}")
        logger.info(f"Telegram invierà gli update a: {wh.full_url}")

        self.app.run_webhook(
            listen="0.0.0.0",
            port=wh.port,
            url_path=wh.path,
            webhook_url=wh.full_url,
            secret_token=wh.secret_token or None,
            drop_pending_updates=True,
        )
