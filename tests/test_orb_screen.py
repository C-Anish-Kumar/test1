"""The ticker screen picks on the first half only and scores the picks on the second."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradingagents.orb.backtest import TradeRecord
from tradingagents.orb.screen import Rules, report, select, split_by_date, stats

NY = ZoneInfo("America/New_York")
SPLIT = date(2026, 7, 1)


def trades(symbol, before, after):
    """Trades with the given R results before and after SPLIT."""
    out = []
    for i, r in enumerate(before):
        t = datetime(2026, 5, 1, 10, tzinfo=NY) + timedelta(days=i)
        out.append(TradeRecord(symbol, t, t, 100, 99, 100 + r, "Session end", r, r, 10, 10.0))
    for i, r in enumerate(after):
        t = datetime(2026, 8, 1, 10, tzinfo=NY) + timedelta(days=i)
        out.append(TradeRecord(symbol, t, t, 100, 99, 100 + r, "Session end", r, r, 10, 10.0))
    return out


BY_SYMBOL = {
    "GOOD": trades("GOOD", [0.5, -0.2] * 6, [0.4, -0.1] * 6),   # good in both halves
    "LATE": trades("LATE", [-0.3, 0.1] * 6, [1.0, 0.5] * 6),    # only good after the split
    "FEW": trades("FEW", [1.0, 1.0], [1.0]),                    # too few trades to judge
}


@pytest.mark.unit
def test_stats_and_split():
    before, after = split_by_date(BY_SYMBOL["GOOD"], SPLIT)
    assert (len(before), len(after)) == (12, 12)
    s = stats(before)
    assert (s.n, round(s.avg, 2), round(s.pf, 2), s.win_rate) == (12, 0.15, 2.5, 0.5)


@pytest.mark.unit
def test_selection_uses_only_the_first_half():
    # LATE did best overall but only after the split, so it must not be picked.
    assert select(BY_SYMBOL, SPLIT, Rules()) == ["GOOD"]


@pytest.mark.unit
def test_report_scores_the_picks_on_the_test_period():
    text = report(BY_SYMBOL, SPLIT, date(2026, 5, 1), date(2026, 10, 29), Rules())
    assert "Picked (1): GOOD" in text
    assert "Picked, test period:            12 trades, win rate 50%, avg +0.15R" in text
    assert "HELD UP" in text and 'symbols = ["GOOD"]' in text
    none = report({"LATE": BY_SYMBOL["LATE"]}, SPLIT, date(2026, 5, 1), date(2026, 10, 29), Rules())
    assert none.endswith("Verdict: NO ticker suits these settings.")
