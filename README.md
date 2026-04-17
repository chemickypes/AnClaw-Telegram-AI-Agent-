# AnClaw — Telegram AI Agent

Assistente AI personale su Telegram, costruito con [agno](https://github.com/agno-agi/agno) e Google Gemini.

## Idea di fondo

La maggior parte dei bot AI ha un team di agenti fisso, pensato a priori per gestire qualsiasi tipo di richiesta. AnClaw funziona diversamente: ogni messaggio ricevuto viene prima analizzato da un **agente Architetto**, che decide dinamicamente quale team assembler e con quale strategia.

Il risultato è un sistema che non spreca risorse su task semplici (risposta diretta) e che scala automaticamente su quelli complessi (ricerca + scraping + sintesi), senza che l'utente debba fare nulla di diverso dal mandare un messaggio.

### Flusso di esecuzione

```
Utente → Telegram
           │
           ▼
    ArchitectAgent  (Gemini 2.5 Pro)
    Analizza la richiesta e produce un piano JSON:
    - quale modalità usare (route / coordinate / broadcast)
    - quali agenti coinvolgere
    - un messaggio intermedio da mostrare all'utente
           │
           ▼
    Team dinamico  (agno Team, runtime)
    Costruito al volo in base al piano
           │
    ┌──────┴──────────────────────────────┐
    │  Agenti disponibili nel catalogo:   │
    │  • SearchAgent   (web, HN, Reddit)  │
    │  • ScraperAgent  (browser + crawl)  │
    │  • YouTubeAgent  (video + transcript)│
    │  • FileAgent     (PDF, CSV, …)      │
    │  • SchedulerAgent (sveglie cron)    │
    │  • SynthAgent    (sintesi finale)   │
    └─────────────────────────────────────┘
           │
           ▼
    Risposta → Telegram
```

### Modalità del team

| Modalità | Quando | Esempio |
|---|---|---|
| `route` | Task semplice, un solo agente | Fatto storico, generazione file |
| `coordinate` | Pipeline sequenziale | Ricerca → Scraping → Sintesi |
| `broadcast` | Task paralleli indipendenti | — |

### Memoria di sessione

- **ArchitectAgent** ricorda la conversazione per mantenere il contesto nelle richieste successive.
- **SynthAgent** tiene traccia della conversazione per gestire i follow-up ("e quello precedente?").
- La persistenza usa SQLite (`tmp/agent_data.db`).

### Sveglie ricorrenti

L'agente supporta task programmati via cron. Puoi dire ad esempio:

> "Ogni mattina alle 8 mandami le top 10 notizie di Hacker News"

Lo SchedulerAgent crea la sveglia, l'Architetto pre-baked il piano di esecuzione e APScheduler esegue il task all'orario stabilito, inviando il risultato direttamente in chat.

---

## Stack tecnico

| Componente | Tecnologia |
|---|---|
| Framework agenti | [agno](https://github.com/agno-agi/agno) |
| Modelli AI | Google Gemini 2.5 Pro / Flash |
| Trascrizione audio | Gemini 2.5 Flash (API nativa) |
| Bot Telegram | python-telegram-bot v21 |
| Scheduler | APScheduler (AsyncIO) |
| Persistenza | SQLite via SQLAlchemy |

---

## Requisiti

- Python 3.11+
- Account Google AI Studio (per la `GOOGLE_API_KEY`)
- Un bot Telegram creato via [@BotFather](https://t.me/BotFather) (per il `TELEGRAM_BOT_TOKEN`)

---

## Esecuzione locale

### 1. Clona il repository

```bash
git clone <repo-url>
cd anclaw_telegram_agent
```

### 2. Crea e attiva il virtualenv

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Installa le dipendenze

```bash
pip install -r requirements.txt
```

> **Nota:** `crawl4ai` richiede l'installazione dei browser Playwright al primo utilizzo:
> ```bash
> crawl4ai-setup
> ```

### 4. Configura le variabili d'ambiente

```bash
cp .env.example .env
```

Apri `.env` e compila:

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot ottenuto da @BotFather |
| `GOOGLE_API_KEY` | Chiave da [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `ALLOWED_USER_IDS` | Il tuo ID Telegram (es. `12345678`). Trovi il tuo ID scrivendo a [@userinfobot](https://t.me/userinfobot) |
| `BOT_MODE` | `polling` per lo sviluppo locale (default) |

Le variabili webhook sono necessarie solo in produzione (`BOT_MODE=webhook`).

### 5. Avvia il bot

```bash
python main.py
```

Il bot risponderà ai messaggi degli utenti nella whitelist. Per testarlo, apri Telegram e scrivi al tuo bot.

---

## Struttura del progetto

```
anclaw_telegram_agent/
├── main.py          # Entry point: setup logging, avvio bot
├── bot.py           # Handler Telegram (testo, voce, foto, documenti, callback)
├── agent.py         # AIAgent: Architetto + catalogo agenti + logica team dinamico
├── config.py        # Configurazione da variabili d'ambiente
├── scheduler.py     # Sveglie ricorrenti: APScheduler + SQLite store + tools
├── transcriber.py   # Trascrizione audio tramite Gemini
├── sender.py        # TelegramSender: invio proattivo di messaggi e file
├── requirements.txt
├── .env.example
└── tmp/             # Generato a runtime: DB SQLite, file generati dagli agenti
```

---

## Modalità webhook (produzione)

Per eseguire in produzione con webhook invece del polling:

1. Imposta `BOT_MODE=webhook` nel `.env`
2. Configura `WEBHOOK_URL` con il tuo dominio HTTPS pubblico
3. In sviluppo puoi usare [ngrok](https://ngrok.com/): `ngrok http 8443`

Telegram accetta solo le porte: `443`, `80`, `88`, `8443`.

---

## Autore

Angelo Moroni — [mor.angelo.mor@gmail.com](mailto:mor.angelo.mor@gmail.com)
