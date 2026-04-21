# AnClaw — Telegram AI Agent

A personal AI assistant on Telegram, built with [agno](https://github.com/agno-agi/agno) and Google Gemini.

## The idea

Most AI bots have a fixed set of agents defined upfront to handle any kind of request. AnClaw works differently: every incoming message is first analyzed by an **Architect agent**, which dynamically decides what team to assemble and what strategy to use.

The result is a system that doesn't waste resources on simple tasks (direct answer) and automatically scales up for complex ones (search + scraping + synthesis) — without the user doing anything different from just sending a message.

### Execution flow

```
User → Telegram
          │
          ▼
   ArchitectAgent  (Gemini 2.5 Flash)
   Analyzes the request and produces a JSON plan:
   - which mode to use (route / coordinate / broadcast)
   - which agents to involve
   - an intermediate status message to show the user
          │
          ▼
   Dynamic team  (agno Team, built at runtime)
          │
   ┌──────┴──────────────────────────────────┐
   │  Available agents in the catalog:       │
   │  • SearchAgent    (web, HN, Reddit)     │
   │  • ScraperAgent   (browser + crawl)     │
   │  • YouTubeAgent   (video + transcripts) │
   │  • FileAgent      (PDF, CSV, …)         │
   │  • SchedulerAgent  (cron schedules)     │
   │  • CalendarAgent   (Google Calendar)    │
   │  • CodeAgent       (math + CSV/Excel)   │
   │  • NotesAgent      (personal notes)     │
   │  • SynthAgent      (final synthesis)    │
   └─────────────────────────────────────────┘
          │
          ▼
   Response → Telegram
```

### Team modes

| Mode | When | Example |
|---|---|---|
| `route` | Single-agent task | Historical fact, file generation |
| `coordinate` | Sequential pipeline | Search → Scrape → Synthesize |
| `broadcast` | Independent parallel tasks | — |

### Session memory

- **ArchitectAgent** remembers the conversation to maintain context across follow-up requests.
- **SynthAgent** tracks the conversation to handle follow-ups ("and the previous one?").
- Persistence is handled via SQLite (`tmp/agent_data.db`).

### Long-term memory (user facts)

The bot automatically extracts and stores permanent facts about you from your messages (preferences, habits, location, profession, etc.) and injects them into every SynthAgent response, so it always has context about who you are.

You can also save facts explicitly:

> "Ricordati che abito a Milano"
> "Nota che sono vegetariano"

Facts are stored in the `user_facts` table in SQLite and can be reviewed or deleted with `/ricordi`.

### Personal notes

A simple, date-free notepad for reminders, TODOs, and anything you want to keep handy. The **NotesAgent** handles saving, listing, searching, and deleting notes via natural language:

> "Nota: chiamare il dentista"
> "Mostra le mie note"
> "Cerca nelle note dentista"
> "Elimina la nota 3"

The `Nota: ...` prefix saves a note instantly without going through the full agent pipeline. Notes are stored in the `notes` table in SQLite and can be managed with `/note`.

### Recurring schedules

The agent supports cron-based scheduled tasks. You can say, for example:

> "Every morning at 8 send me the top 10 Hacker News stories"

The SchedulerAgent creates the schedule, the Architect pre-bakes the execution plan, and APScheduler fires the task at the given time, sending the result directly to the chat.

### Code execution (RestrictedPython sandbox)

The **CodeAgent** runs Python code in a restricted, sandboxed environment — no filesystem access, no network, no `import` of arbitrary modules. It handles two categories of tasks:

**1. Math and statistics**

The agent generates Python code and executes it via [RestrictedPython](https://restrictedpython.readthedocs.io/). Available modules: `math`, `statistics`, `decimal.Decimal`. All common builtins are enabled (`sum`, `min`, `max`, `round`, `sorted`, etc.).

> "What is the standard deviation of [12, 45, 7, 23, 56, 34]?"
> "Compute the compound interest on €5000 at 3.5% over 10 years."
> "What is 15% of 1347?"

**2. CSV and Excel analysis**

When you send a `.csv` or `.xlsx` file, the bot saves it to `tmp/uploads/` and passes the path to the CodeAgent. Two tools are available:

- `search_in_file` — finds rows where a column contains a given value (case-insensitive partial match)
- `filter_file_rows` — filters rows with a Python expression using RestrictedPython; the code receives `rows` as a list of dicts and must assign the result to `result`

> _Attach `vendite.csv` with caption:_ "Which rows have city = Milano?"
> _Attach `clienti.xlsx` with caption:_ "How many customers are over 40?"
> _Attach `ordini.csv` with caption:_ "Total amount for orders where status is 'shipped'"

**Security model**

| What is allowed | What is blocked |
|---|---|
| `math`, `statistics`, `Decimal` | `import`, `open`, `os`, `subprocess` |
| List comprehensions, generators | Filesystem and network access |
| `sum`, `min`, `max`, `sorted`, … | `exec`, `eval`, `__import__` |
| 5-second execution timeout | Infinite loops (killed by timeout) |

---

### Google Calendar integration

The CalendarAgent reads and writes events on your Google Calendar. Examples:

> "What do I have this week?"
> "Add a meeting tomorrow at 3pm"
> "Create a 'Gym' event on Friday at 7:30"

---

## Bot commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | Usage guide and supported input types |
| `/sveglie` | List active schedules with inline buttons to delete or refresh each one |
| `/note` | List all saved notes with inline buttons to delete each one |
| `/ricordi` | List all memorized user facts with inline buttons to forget each one |
| `/status` | Show bot mode (polling/webhook), number of active schedules, and sessions in memory |
| `/reset` | Clear the current session memory for ArchitectAgent, SynthAgent, and Team — start fresh |

---

## Tech stack

| Component | Technology |
|---|---|
| Agent framework | [agno](https://github.com/agno-agi/agno) |
| AI models | Google Gemini 2.5 Pro / Flash |
| Audio transcription | Gemini 2.5 Flash (native API) |
| Telegram bot | python-telegram-bot v21 |
| Scheduler | APScheduler (AsyncIO) |
| Persistence | SQLite via SQLAlchemy |
| Calendar integration | Google Calendar API v3 (OAuth 2.0) |
| Code sandbox | RestrictedPython + openpyxl |

---

## Requirements

- Python 3.12+
- A Google AI Studio account (for `GOOGLE_API_KEY`)
- A Telegram bot created via [@BotFather](https://t.me/BotFather) (for `TELEGRAM_BOT_TOKEN`)
- A Google Cloud project with the Calendar API enabled (for Google Calendar integration)

---

## Running locally

### 1. Clone the repository

```bash
git clone <repo-url>
cd anclaw_telegram_agent
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -e .
```

To also install dev tools (pytest, ruff):

```bash
pip install -e ".[dev]"
```

> **Note:** `crawl4ai` requires Playwright browsers to be installed on first use:
> ```bash
> crawl4ai-setup
> ```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `GOOGLE_API_KEY` | Key from [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `ALLOWED_USER_IDS` | Your Telegram user ID (e.g. `12345678`). Find it by messaging [@userinfobot](https://t.me/userinfobot) |
| `BOT_MODE` | `polling` for local development (default) |

Webhook variables are only required in production (`BOT_MODE=webhook`).

### 5. Set up Google Calendar (optional)

This step is required to use the CalendarAgent. Skip it if you don't need calendar integration.

**5a. Create a Google Cloud project and enable the Calendar API**

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a new project
2. Navigate to **APIs & Services → Library**, search for `Google Calendar API`, and enable it
3. Go to **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - Fill in app name and email; skip scopes
   - Add your Google account as a **test user**
4. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Download the JSON file and save it as `credentials.json` in the project root

**5b. Authorize calendar access (one-time)**

```bash
python auth_calendar.py
```

A browser window will open. Sign in with your Google account and grant access. A `token.json` file will be saved in the project root — this is reused automatically on subsequent runs (with silent refresh).

You can delete `auth_calendar.py` after this step.

> **Note:** Keep `credentials.json` and `token.json` out of version control (they are already in `.gitignore`).

---

### 6. Start the bot

```bash
# Any of the following are equivalent:
python main.py
python -m anclaw_telegram_agent
anclaw-bot   # available after pip install -e .
```

The bot will respond to messages from whitelisted users. Open Telegram and write to your bot to test it.

---

## Project structure

```
anclaw_telegram_agent/
├── main.py                        # Entry point (alternative to anclaw-bot / python -m)
├── pyproject.toml                 # Package config, dependencies, ruff and pytest settings
├── src/
│   └── anclaw_telegram_agent/
│       ├── __main__.py            # Entry point for python -m anclaw_telegram_agent
│       ├── agent.py               # AIAgent: Architect + agent catalog + dynamic team logic
│       ├── agent_catalog.py       # Factory functions for each agent in the catalog
│       ├── agent_models.py        # Pydantic models for ArchitectPlan and AgentSpec
│       ├── agent_router.py        # Deterministic pre-routing (reminders, schedules, …)
│       ├── bot.py                 # Telegram handlers (text, voice, photos, documents, callbacks)
│       ├── calendar_tools.py      # Google Calendar tools (list/create/delete events)
│       ├── code_tools.py          # CodeAgent tools: execute_math, search_in_file, filter_file_rows
│       ├── config.py              # Configuration from environment variables
│       ├── memory_store.py        # Long-term user facts: save/get/delete + automatic extraction
│       ├── notes_store.py         # Personal notes: save/list/search/delete
│       ├── reminders_store.py     # One-shot reminders: save/list/delete
│       ├── rss_feeds.py           # Static list of RSS feeds available to SearchAgent
│       ├── rss_store.py           # RSS read-state persistence (SQLite)
│       ├── rss_tools.py           # Tool factory for RSS feed fetching
│       ├── scheduler.py           # Recurring schedules: APScheduler + SQLite store + tools
│       ├── sender.py              # TelegramSender: proactive message and file delivery
│       └── transcriber.py         # Audio transcription via Gemini
├── tests/
│   ├── test_agent_factories.py
│   └── test_deterministic_route.py
├── .env.example
├── credentials.json               # Google OAuth client credentials (not committed)
├── token.json                     # Google OAuth access/refresh token (not committed)
└── tmp/
    ├── agent_data.db              # SQLite: session memory, user facts, notes, schedules
    ├── uploads/                   # CSV/Excel files received from Telegram (for CodeAgent)
    └── …                          # Files generated by FileAgent
```

---

## Webhook mode (production)

To run in production with webhooks instead of polling:

1. Set `BOT_MODE=webhook` in `.env`
2. Set `WEBHOOK_URL` to your public HTTPS domain
3. For local testing you can use [ngrok](https://ngrok.com/): `ngrok http 8443`

Telegram only accepts ports: `443`, `80`, `88`, `8443`.

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

## Author

Angelo Moroni — [mor.angelo.mor@gmail.com](mailto:mor.angelo.mor@gmail.com)
