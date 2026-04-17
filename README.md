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
   │  • CalendarAgent   (Google Calendar)   │
   │  • SynthAgent      (final synthesis)   │
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

### Recurring schedules

The agent supports cron-based scheduled tasks. You can say, for example:

> "Every morning at 8 send me the top 10 Hacker News stories"

The SchedulerAgent creates the schedule, the Architect pre-bakes the execution plan, and APScheduler fires the task at the given time, sending the result directly to the chat.

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

---

## Requirements

- Python 3.11+
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
pip install -r requirements.txt
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
python main.py
```

The bot will respond to messages from whitelisted users. Open Telegram and write to your bot to test it.

---

## Project structure

```
anclaw_telegram_agent/
├── main.py          # Entry point: logging setup, bot startup
├── bot.py           # Telegram handlers (text, voice, photos, documents, callbacks)
├── agent.py         # AIAgent: Architect + agent catalog + dynamic team logic
├── config.py        # Configuration from environment variables
├── scheduler.py      # Recurring schedules: APScheduler + SQLite store + tools
├── calendar_tools.py # Google Calendar tools (list/create/delete events)
├── transcriber.py    # Audio transcription via Gemini
├── sender.py         # TelegramSender: proactive message and file delivery
├── requirements.txt
├── .env.example
├── credentials.json  # Google OAuth client credentials (not committed)
├── token.json        # Google OAuth access/refresh token (not committed)
└── tmp/              # Created at runtime: SQLite DB, files generated by agents
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
