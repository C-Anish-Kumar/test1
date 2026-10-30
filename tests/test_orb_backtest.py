"""The ORB backtest replays bars through the bot's engine and sizes trades like the bot."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradingagents.orb.backtest import (
    TradeRecord,
    bar_size,
    daily_atr_lookup,
    replay,
    summarize,
)
from tradingagents.orb.config import BotSettings
from tradingagents.orb.engine import Bar, OrbConfig

NY = ZoneInfo("America/New_York")


def bar(day, hhmm, o, h, lo, c, v=1000):
    start = datetime(2026, 10, day, int(hhmm[:2]), int(hhmm[3:]), tzinfo=NY)
    return Bar(start, start + timedelta(minutes=5), o, h, lo, c, v)


# Opening range 100-102, a close break at 102.5 (stop 100, 1R = 2.5), then a stop-out.
DAY = [
    bar(28, "09:30", 100.0, 101.0, 100.0, 100.5),
    bar(28, "09:35", 100.5, 102.0, 100.2, 101.5),
    bar(28, "09:40", 101.5, 101.8, 100.5, 101.0),
    bar(28, "09:45", 101.0, 102.8, 101.0, 102.5),
    bar(28, "09:50", 102.5, 102.6, 99.9, 101.0),
]


@pytest.mark.unit
def test_replay_records_the_trade_with_the_bots_sizing_and_costs():
    [t] = replay("NVDA", DAY, OrbConfig(), BotSettings(risk_per_trade_usd=100), cost_per_share=0.02)
    assert (t.symbol, t.entry, t.stop, t.exit, t.reason) == ("NVDA", 102.5, 100.0, 100.0, "Stop")
    assert t.r == -1.0
    assert t.r_net == pytest.approx(-1.0 - 0.02 / 2.5)
    assert (t.shares, t.risk_usd) == (40, 100.0)


@pytest.mark.unit
def test_replay_skips_a_signal_too_small_for_one_share():
    assert replay("NVDA", DAY, OrbConfig(), BotSettings(risk_per_trade_usd=1)) == []


@pytest.mark.unit
def test_summary_reports_halves_and_the_verdict():
    t0 = datetime(2026, 9, 1, 10, tzinfo=NY)

    def trade(i, r):
        return TradeRecord("MU", t0 + timedelta(days=i), t0 + timedelta(days=i, hours=1),
                           100, 99, 100 + r, "Session end", r, r, 10, 10.0)

    text = summarize([trade(0, 0.5), trade(1, -0.2), trade(2, 0.4), trade(3, 0.3)], NY)
    assert "Trades: 4" in text and "win rate 75%" in text
    assert "1st half +0.15R (2)" in text and "2nd half +0.35R (2)" in text
    assert text.endswith("YES")
    assert summarize([trade(0, 0.5), trade(1, -0.9)], NY).endswith("NO")
    assert summarize([]) == "No trades."


@pytest.mark.unit
def test_daily_atr_lookup_uses_the_prior_day_only():
    lookup = daily_atr_lookup([date(2026, 10, 27), date(2026, 10, 28)], [None, 2.0])
    assert lookup(date(2026, 10, 28)) is None
    assert lookup(date(2026, 10, 29)) == 2.0


@pytest.mark.unit
def test_bar_size_uses_ibkrs_spelling():
    assert (bar_size(1), bar_size(15)) == ("1 min", "15 mins")
