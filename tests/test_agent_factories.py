"""
Testa che ogni factory produca l'agente corretto e che la catena
comando → piano deterministico → factory → agente sia coerente.
"""
import pytest
from unittest.mock import MagicMock

from anclaw_telegram_agent.agent_catalog import (
    _AGENT_CATALOG,
    _make_notes_agent,
    _make_rss_feeds_agent,
    _make_code_agent,
    _make_calendar_agent,
    _make_search_agent,
    _make_scraper_agent,
    _make_youtube_agent,
    _make_file_agent,
    _make_reminder_agent,
)
from anclaw_telegram_agent.agent_router import _deterministic_route


def tool_names(agent) -> list[str]:
    return [t.__name__ for t in (agent.tools or [])]


# ── Factory: nome agente corretto ─────────────────────────────────────────────

@pytest.mark.parametrize("name,factory", list(_AGENT_CATALOG.items()))
def test_factory_produces_correct_name(name, factory):
    agent = factory()
    assert agent.name == name


# ── Factory: tool attesi ──────────────────────────────────────────────────────

def test_notes_agent_tools():
    agent = _make_notes_agent()
    names = tool_names(agent)
    assert "save_note" in names
    assert "list_notes" in names
    assert "search_notes" in names
    assert "delete_note" in names


def test_rss_feeds_agent_tools():
    agent = _make_rss_feeds_agent()
    names = tool_names(agent)
    assert "add_rss_feed" in names
    assert "list_rss_feeds" in names
    assert "delete_rss_feed" in names


def test_code_agent_tools():
    agent = _make_code_agent()
    names = tool_names(agent)
    assert "execute_math" in names
    assert "search_in_file" in names
    assert "filter_file_rows" in names


def test_calendar_agent_tools():
    agent = _make_calendar_agent()
    names = tool_names(agent)
    assert "list_events" in names
    assert "create_event" in names
    assert "delete_event" in names


def test_reminder_agent_tools():
    mock_scheduler = MagicMock()
    agent = _make_reminder_agent(scheduler=mock_scheduler, get_chat_id=lambda: 123)
    names = tool_names(agent)
    assert "create_reminder" in names
    assert "list_reminders" in names
    assert "delete_reminder" in names


# ── Catena completa: comando → piano → factory → agente ──────────────────────

@pytest.mark.parametrize("command,expected_agent,expected_tools", [
    (
        "mostra le note",
        "NotesAgent",
        ["save_note", "list_notes", "search_notes", "delete_note"],
    ),
    (
        "salva nota: meeting con cliente",
        "NotesAgent",
        ["save_note", "list_notes", "search_notes", "delete_note"],
    ),
    (
        "cerca nelle note Python",
        "NotesAgent",
        ["save_note", "list_notes", "search_notes", "delete_note"],
    ),
    (
        "aggiungi feed https://example.com/rss",
        "RSSFeedsAgent",
        ["add_rss_feed", "list_rss_feeds", "delete_rss_feed"],
    ),
    (
        "lista dei feed",
        "RSSFeedsAgent",
        ["add_rss_feed", "list_rss_feeds", "delete_rss_feed"],
    ),
    (
        "elimina feed 2",
        "RSSFeedsAgent",
        ["add_rss_feed", "list_rss_feeds", "delete_rss_feed"],
    ),
])
def test_command_to_agent_chain(command, expected_agent, expected_tools):
    plan = _deterministic_route(command)
    assert plan is not None, f"Nessun piano deterministico per: {command!r}"
    assert plan.agents[0].name == expected_agent

    factory = _AGENT_CATALOG[expected_agent]
    agent = factory()
    assert agent.name == expected_agent

    names = tool_names(agent)
    for tool in expected_tools:
        assert tool in names, f"Tool {tool!r} mancante in {expected_agent}"


def test_command_reminder_chain():
    command = "ricordami di chiamare Marco alle 15"
    plan = _deterministic_route(command)
    assert plan is not None
    assert plan.agents[0].name == "ReminderAgent"

    mock_scheduler = MagicMock()
    agent = _make_reminder_agent(scheduler=mock_scheduler, get_chat_id=lambda: 0)
    assert agent.name == "ReminderAgent"
    assert "create_reminder" in tool_names(agent)


def test_command_scheduler_chain():
    command = "ricordami ogni giorno alle 9 di controllare le email"
    plan = _deterministic_route(command)
    assert plan is not None
    assert plan.agents[0].name == "SchedulerAgent"


# ── Coerenza _AGENT_CATALOG ───────────────────────────────────────────────────

def test_catalog_keys_match_agent_names():
    """Ogni chiave del catalogo corrisponde al nome dell'agente prodotto."""
    for name, factory in _AGENT_CATALOG.items():
        agent = factory()
        assert agent.name == name, f"Catalogo key {name!r} produce agente {agent.name!r}"


def test_catalog_agents_have_tools():
    """Ogni agente nel catalogo ha almeno un tool."""
    for name, factory in _AGENT_CATALOG.items():
        agent = factory()
        assert agent.tools, f"{name} non ha tool"
