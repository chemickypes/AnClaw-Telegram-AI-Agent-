import pytest
from anclaw_telegram_agent.agent_router import _deterministic_route, _strip_architect_prefix


def route(msg: str) -> str | None:
    plan = _deterministic_route(msg)
    return plan.agents[0].name if plan else None


# ── ReminderAgent ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "ricordami di chiamare Marco alle 15",
    "Ricordami domani mattina di mandare la mail",
    "avvisami tra 30 minuti",
    "notificami alle 9 di domani",
    "promemoria: riunione con il team",
    "promemoria- compra il latte",
])
def test_reminder_match(msg):
    assert route(msg) == "ReminderAgent"


@pytest.mark.parametrize("msg", [
    "fai un riassunto di questo testo: ricordami di fare la spesa",
    "nel testo c'è scritto ricordami",
    "cosa significa avvisami in inglese?",
    "ho un promemoria nel calendario",
])
def test_reminder_no_false_positive(msg):
    assert route(msg) is None


# ── NotesAgent ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "salva nota: comprare il pane",
    "Salva una nota su questo argomento",
    "aggiungi una nota su React",
    "scrivi un appunto veloce",
    "crea una nota",
    "mostra le note",
    "mostra tutte le note",
    "lista note",
    "elenca gli appunti",
    "vedi le note",
    "cerca nelle note React",
    "trova nelle note qualcosa su Python",
    "elimina la nota 3",
    "cancella nota 5",
    "rimuovi questa nota",
])
def test_notes_match(msg):
    assert route(msg) == "NotesAgent"


@pytest.mark.parametrize("msg", [
    "fai un riassunto: nel quaderno c'è una nota interessante",
    "cosa significa prendere nota in inglese?",
    "analizza questo testo che parla di appunti",
    "cerca informazioni sulle note musicali",
    "ho trovato una nota sul tavolo, cosa devo fare?",
])
def test_notes_no_false_positive(msg):
    assert route(msg) is None


# ── RSSFeedsAgent ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "aggiungi feed https://example.com/rss",
    "aggiungi un feed rss",
    "mostra feed",
    "mostra i feed",
    "lista feed",
    "lista dei feed",
    "elimina feed 3",
    "rimuovi feed 2",
    "elenca feed rss",
    "feed rss",
])
def test_rss_match(msg):
    assert route(msg) == "RSSFeedsAgent"


@pytest.mark.parametrize("msg", [
    "ho trovato un buon feed per nutrire i pesci",
    "dammi le ultime notizie dal feed di TechCrunch",
    "cerca notizie nei feed rss che ho salvato",
    "cosa sono i feed rss?",
])
def test_rss_no_false_positive(msg):
    assert route(msg) is None


# ── Nessun match (passa all'LLM) ──────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "chi ha ucciso Lincoln?",
    "ultime notizie su OpenAI",
    "calcola 345 * 12",
    "aggiungi un evento al calendario domani alle 10",
    "riassumi questo articolo: https://example.com",
    "crea un file CSV con questi dati",
])
def test_no_match_goes_to_llm(msg):
    assert route(msg) is None


# ── Punto 1: case-insensitive ─────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "RICORDAMI di fare la spesa",
    "SALVA NOTA: meeting domani",
    "MOSTRA LE NOTE",
    "AGGIUNGI FEED https://example.com/rss",
    "ELENCA GLI APPUNTI",
])
def test_case_insensitive(msg):
    assert route(msg) is not None


# ── Punto 2: spazi iniziali ───────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "  ricordami di fare la spesa",
    "   salva nota: appunto importante",
    "  mostra le note",
    "  aggiungi feed https://example.com/rss",
])
def test_leading_spaces(msg):
    assert route(msg) is not None


# ── Punto 3: prefisso FILE SALVATO ────────────────────────────────────────────

def test_file_prefix_stripped_before_route():
    raw = "[FILE SALVATO: /tmp/dati.csv]\nsalva nota: analisi completata"
    stripped = _strip_architect_prefix(raw)
    assert route(stripped) == "NotesAgent"


def test_file_prefix_with_context_prefix():
    raw = "[Contesto: oggi è 21 aprile 2026, 10:00 CEST]\n\n[FILE SALVATO: /tmp/x.csv]\nmostra le note"
    stripped = _strip_architect_prefix(raw)
    assert route(stripped) == "NotesAgent"


def test_file_prefix_to_llm():
    raw = "[FILE SALVATO: /tmp/dati.csv]\nanalizza questo file CSV"
    stripped = _strip_architect_prefix(raw)
    assert route(stripped) is None


# ── Punto 4: reminder ricorrente → SchedulerAgent ────────────────────────────

@pytest.mark.parametrize("msg", [
    "ricordami ogni giorno alle 9 di controllare le email",
    "avvisami ogni lunedì mattina",
    "ogni giorno alle 8 mandami il meteo",
    "ogni settimana il lunedì inviami un report",
    "crea una sveglia per ogni venerdì alle 18",
    "imposta una sveglia ogni giorno alle 7",
    "programma un task ricorrente ogni mese",
])
def test_recurring_goes_to_scheduler(msg):
    assert route(msg) == "SchedulerAgent"


@pytest.mark.parametrize("msg", [
    "ricordami di chiamare Mario alle 15",
    "avvisami tra 30 minuti",
    "promemoria: riunione oggi alle 14",
])
def test_one_shot_stays_reminder(msg):
    assert route(msg) == "ReminderAgent"


# ── Punto 5: _strip_architect_prefix ─────────────────────────────────────────

def test_strip_context_prefix():
    raw = "[Contesto: oggi è 21 aprile 2026, 10:00 CEST]\n\nricordami di fare la spesa"
    assert _strip_architect_prefix(raw) == "ricordami di fare la spesa"


def test_strip_context_prefix_no_match():
    raw = "ricordami di fare la spesa"
    assert _strip_architect_prefix(raw) == "ricordami di fare la spesa"


def test_strip_file_prefix_only():
    raw = "[FILE SALVATO: /tmp/data.csv]\nanalizza questo file"
    assert _strip_architect_prefix(raw) == "analizza questo file"


def test_strip_both_prefixes():
    raw = "[Contesto: oggi è 21 aprile 2026, 10:00 CEST]\n\n[FILE SALVATO: /tmp/data.csv]\nanalizza questo file"
    assert _strip_architect_prefix(raw) == "analizza questo file"


def test_strip_preserves_rest_of_message():
    raw = "[Contesto: oggi è 21 aprile 2026]\n\nmostra le note\naltro testo"
    assert _strip_architect_prefix(raw) == "mostra le note\naltro testo"
