"""The Agent SDK research agent's tools and options, without calling Claude."""

import asyncio

import pytest

pytest.importorskip("claude_agent_sdk")

from tradingagents import research_agent  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402

pytestmark = pytest.mark.unit

TRADE_DATE = "2026-09-01"


def _tools(trade_date=TRADE_DATE):
    return {t.name: t for t in research_agent.market_tools(trade_date)}


def _run(tool, args):
    return asyncio.run(tool.handler(args))


def _text(result):
    return result["content"][0]["text"]


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_route(method, *args):
        seen.append((method, *args))
        return f"{method} ok"

    monkeypatch.setattr(research_agent, "route_to_vendor", fake_route)
    return seen


def test_stock_data_window_is_clamped_to_trade_date(calls):
    result = _run(
        _tools()["get_stock_data"],
        {"ticker": "NVDA", "start_date": "2026-08-01", "end_date": "2026-10-15"},
    )
    assert _text(result) == "get_stock_data ok"
    assert calls == [("get_stock_data", "NVDA", "2026-08-01", TRADE_DATE)]


def test_unreadable_start_date_is_a_tool_error(calls):
    result = _run(
        _tools()["get_news"], {"ticker": "NVDA", "start_date": "soon", "end_date": TRADE_DATE}
    )
    assert result["is_error"] is True
    assert calls == []


def test_dated_tools_always_use_trade_date(calls):
    tools = _tools()
    _run(tools["get_fundamentals"], {"ticker": "AAPL"})
    _run(tools["get_financial_statement"], {"ticker": "AAPL", "statement": "cashflow"})
    _run(tools["get_insider_transactions"], {"ticker": "AAPL"})
    _run(tools["get_global_news"], {"look_back_days": 7, "limit": 5})
    assert calls == [
        ("get_fundamentals", "AAPL", TRADE_DATE),
        ("get_cashflow", "AAPL", "quarterly", TRADE_DATE),
        ("get_insider_transactions", "AAPL", TRADE_DATE),
        ("get_global_news", TRADE_DATE, 7, 5),
    ]


def test_indicators_are_fetched_one_at_a_time(calls):
    result = _run(
        _tools()["get_indicators"], {"ticker": "NVDA", "indicator": "RSI, macd", "look_back_days": 14}
    )
    assert calls == [
        ("get_indicators", "NVDA", "rsi", TRADE_DATE, 14),
        ("get_indicators", "NVDA", "macd", TRADE_DATE, 14),
    ]
    assert _text(result) == "get_indicators ok\n\nget_indicators ok"


def test_snapshot_uses_trade_date(monkeypatch):
    seen = []
    monkeypatch.setattr(
        research_agent,
        "build_verified_market_snapshot",
        lambda *args: seen.append(args) or "snapshot",
    )
    result = _run(_tools()["get_verified_market_snapshot"], {"ticker": "NVDA", "look_back_days": 10})
    assert _text(result) == "snapshot"
    assert seen == [("NVDA", TRADE_DATE, 10)]


@pytest.mark.parametrize(
    ("error", "prefix"),
    [
        (NoMarketDataError("ZZZZ"), "NO_DATA_AVAILABLE"),
        (VendorUnavailableError("rate limited"), "DATA_UNAVAILABLE"),
    ],
)
def test_data_errors_become_sentinels(monkeypatch, error, prefix):
    def fail(*args):
        raise error

    monkeypatch.setattr(research_agent, "route_to_vendor", fail)
    result = _run(_tools()["get_fundamentals"], {"ticker": "ZZZZ"})
    assert _text(result).startswith(prefix)
    assert "is_error" not in result


def test_unexpected_errors_are_reported_not_raised(monkeypatch):
    def fail(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(research_agent, "route_to_vendor", fail)
    result = _run(_tools()["get_fundamentals"], {"ticker": "NVDA"})
    assert result["is_error"] is True
    assert "boom" in _text(result)


def test_options_expose_only_research_tools():
    opts = research_agent.build_options(TRADE_DATE)
    assert opts.model == research_agent.DEFAULT_MODEL
    assert opts.tools == ["WebSearch", "WebFetch"]
    assert set(opts.allowed_tools) == {
        *(f"mcp__market__{name}" for name in _tools()),
        "WebSearch",
        "WebFetch",
    }
    assert opts.setting_sources == []
    assert TRADE_DATE in opts.system_prompt
    assert opts.max_budget_usd == 2.0


def test_no_web_option_removes_builtin_tools():
    opts = research_agent.build_options(TRADE_DATE, web=False)
    assert opts.tools == []
    assert all(name.startswith("mcp__market__") for name in opts.allowed_tools)
