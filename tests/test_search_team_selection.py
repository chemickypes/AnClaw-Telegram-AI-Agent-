"""
Test per la selezione SearchTeam vs NewsTeam.

- SearchTeam: web (no news, backend=auto) + HackerNews + Wikipedia — entità specifiche
- NewsTeam:   web (news, backend=auto, timelimit=w) + HackerNews + RSS — topic generici
"""
import pytest
from unittest.mock import patch, AsyncMock

from anclaw_telegram_agent.agent_catalog import (
    _make_search_agent,
    _make_news_search_agent,
    _make_wikipedia_agent,
)
from anclaw_telegram_agent.agent_router import make_search_team, make_news_team


# ── Helpers ────────────────────────────────────────────────────────────────────

def toolkit_of(agent, cls_name: str):
    return next((t for t in (agent.tools or []) if type(t).__name__ == cls_name), None)


def registered_functions(toolkit) -> set[str]:
    return set(getattr(toolkit, "functions", {}).keys())


# ── _make_search_agent ─────────────────────────────────────────────────────────

def test_search_agent_web_backend_auto():
    ws = toolkit_of(_make_search_agent(), "WebSearchTools")
    assert ws is not None
    assert ws.backend == "auto"


def test_search_agent_no_news_function():
    ws = toolkit_of(_make_search_agent(), "WebSearchTools")
    assert "search_news" not in registered_functions(ws)


def test_search_agent_has_hackernews():
    assert toolkit_of(_make_search_agent(), "HackerNewsTools") is not None


def test_search_agent_no_wikipedia():
    agent = _make_search_agent()
    types = {type(t).__name__ for t in (agent.tools or [])}
    assert "WikipediaTools" not in types


# ── _make_news_search_agent ────────────────────────────────────────────────────

def test_news_search_agent_web_backend_auto():
    ws = toolkit_of(_make_news_search_agent(), "WebSearchTools")
    assert ws is not None
    assert ws.backend == "auto"


def test_news_search_agent_has_news_function():
    ws = toolkit_of(_make_news_search_agent(), "WebSearchTools")
    assert "search_news" in registered_functions(ws)


def test_news_search_agent_timelimit_week():
    ws = toolkit_of(_make_news_search_agent(), "WebSearchTools")
    assert ws.timelimit == "w"


def test_news_search_agent_has_hackernews():
    assert toolkit_of(_make_news_search_agent(), "HackerNewsTools") is not None


def test_news_search_agent_no_wikipedia():
    agent = _make_news_search_agent()
    types = {type(t).__name__ for t in (agent.tools or [])}
    assert "WikipediaTools" not in types


# ── Differenza chiave tra i due agenti ────────────────────────────────────────

def test_search_vs_news_search_news_function_differs():
    ws_search = toolkit_of(_make_search_agent(), "WebSearchTools")
    ws_news = toolkit_of(_make_news_search_agent(), "WebSearchTools")
    assert "search_news" not in registered_functions(ws_search)
    assert "search_news" in registered_functions(ws_news)


# ── make_search_team ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_team_has_search_agent():
    team = await make_search_team("ultime notizie su Elon Musk")
    names = [m.name for m in team.members]
    assert "SearchAgent" in names


@pytest.mark.asyncio
async def test_search_team_has_wikipedia_agent():
    team = await make_search_team("ultime notizie su Elon Musk")
    names = [m.name for m in team.members]
    assert "WikipediaAgent" in names


@pytest.mark.asyncio
async def test_search_team_has_no_rss_agents():
    team = await make_search_team("chi è Sergio Mattarella")
    rss = [m for m in team.members if m.name.startswith("RSSAgent_")]
    assert rss == []


@pytest.mark.asyncio
async def test_search_team_has_exactly_two_members():
    team = await make_search_team("chi è Sergio Mattarella")
    assert len(team.members) == 2


# ── make_news_team ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_news_team_has_news_search_agent():
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=[]),
    ):
        team = await make_news_team("ultime notizie di calcio")
    names = [m.name for m in team.members]
    assert "NewsSearchAgent" in names


@pytest.mark.asyncio
async def test_news_team_has_no_wikipedia():
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=[]),
    ):
        team = await make_news_team("ultime notizie di calcio")
    names = [m.name for m in team.members]
    assert "WikipediaAgent" not in names


@pytest.mark.asyncio
async def test_news_team_includes_rss_agents_when_feeds_present():
    fake_feeds = [
        {"url": "https://example.com/rss1", "name": "Sport_ANSA", "description": "Calcio"},
        {"url": "https://example.com/rss2", "name": "Sport_Sky", "description": "Serie A"},
    ]
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=fake_feeds),
    ):
        team = await make_news_team("ultime notizie di calcio")
    rss = [m for m in team.members if m.name.startswith("RSSAgent_")]
    assert len(rss) == 2


@pytest.mark.asyncio
async def test_news_team_without_feeds_has_one_member():
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=[]),
    ):
        team = await make_news_team("notizie tech")
    assert len(team.members) == 1


@pytest.mark.asyncio
async def test_news_team_rss_agent_names_match_feed_names():
    fake_feeds = [
        {"url": "https://a.com/feed", "name": "TechCrunch_AI", "description": "AI news"},
    ]
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=fake_feeds),
    ):
        team = await make_news_team("notizie AI")
    rss_names = [m.name for m in team.members if m.name.startswith("RSSAgent_")]
    assert "RSSAgent_TechCrunch_AI" in rss_names


# ── I due team sono distinti ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_team_and_news_team_member_types_differ():
    search_team = await make_search_team("chi è Tim Cook")
    with patch(
        "anclaw_telegram_agent.agent_router.select_rss_feeds",
        new=AsyncMock(return_value=[]),
    ):
        news_team = await make_news_team("ultime notizie tech")

    search_names = {m.name for m in search_team.members}
    news_names = {m.name for m in news_team.members}

    assert "WikipediaAgent" in search_names
    assert "WikipediaAgent" not in news_names

    assert "NewsSearchAgent" in news_names
    assert "NewsSearchAgent" not in search_names
