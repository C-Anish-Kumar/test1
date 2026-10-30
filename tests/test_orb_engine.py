"""The ORB bot's engine follows the ORB Suite indicator's long rules on completed bars."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradingagents.orb.config import settings_from_dict
from tradingagents.orb.engine import Bar, OrbConfig, OrbEngine, wilder_atr
from tradingagents.orb.ibkr import shares_for

NY = ZoneInfo("America/New_York")


def bar(day, hhmm, o, h, lo, c, v=1000):
    start = datetime(2026, 10, day, int(hhmm[:2]), int(hhmm[3:]), tzinfo=NY)
    return Bar(start, start + timedelta(minutes=5), o, h, lo, c, v)


# Opening range 09:30-09:45 on Oct 28: high 102, low 100.
OPENING = [
    bar(28, "09:30", 100.0, 101.0, 100.0, 100.5),
    bar(28, "09:35", 100.5, 102.0, 100.2, 101.5),
    bar(28, "09:40", 101.5, 101.8, 100.5, 101.0),
]
BREAK = bar(28, "09:45", 101.0, 102.8, 101.0, 102.5)


def feed(engine, bars):
    return [a for b in bars for a in engine.on_bar(b)]


def kinds(actions):
    return [a.kind for a in actions]


def drift(start_hhmm, end_hhmm, low=103.0, high=104.0, day=28):
    """Quiet bars that touch neither the stop nor TP1 of the BREAK trade."""
    out, t = [], datetime(2026, 10, day, int(start_hhmm[:2]), int(start_hhmm[3:]))
    end = datetime(2026, 10, day, int(end_hhmm[:2]), int(end_hhmm[3:]))
    while t <= end:
        out.append(bar(day, f"{t:%H:%M}", low, high, low, high - 0.5))
        t += timedelta(minutes=5)
    return out


@pytest.mark.unit
def test_opening_range_locks_and_a_close_break_enters_with_its_levels():
    engine = OrbEngine()
    assert feed(engine, OPENING) == []
    assert (engine.or_high, engine.or_low, engine.or_state) == (102.0, 100.0, "locked")

    [enter] = feed(engine, [BREAK])
    assert enter.kind == "enter"
    assert (enter.price, enter.stop, enter.tp1, enter.target) == (102.5, 100.0, 105.0, 110.0)
    assert engine.trades_today == 1


@pytest.mark.unit
def test_stop_is_checked_before_targets_from_the_bar_after_entry():
    engine = OrbEngine()
    feed(engine, [*OPENING, BREAK])
    [stop] = feed(engine, [bar(28, "09:50", 102.5, 111.0, 99.9, 101.0)])
    assert (stop.kind, stop.reason, stop.price, stop.r) == ("exit", "Stop", 100.0, -1.0)
    assert stop.broker_handled


@pytest.mark.unit
def test_tp1_moves_the_stop_to_breakeven():
    engine = OrbEngine()
    feed(engine, [*OPENING, BREAK])
    [move] = feed(engine, [bar(28, "09:50", 102.5, 105.5, 102.6, 105.0)])
    assert (move.kind, move.stop) == ("move_stop", 102.5)
    [exit_] = feed(engine, [bar(28, "09:55", 105.0, 105.2, 102.4, 102.6)])
    assert (exit_.reason, exit_.r, exit_.broker_handled) == ("Breakeven", 0.0, True)


@pytest.mark.unit
def test_an_open_trade_closes_at_market_on_the_last_session_bar():
    engine = OrbEngine()
    feed(engine, [*OPENING, BREAK])
    actions = feed(engine, drift("09:50", "15:55"))
    [exit_] = actions
    assert (exit_.reason, exit_.broker_handled, exit_.price) == ("Session end", False, 103.5)


@pytest.mark.unit
def test_rth_only_bars_reset_the_opening_range_each_day():
    """No out-of-session bars between days: the date change alone starts the new session."""
    engine = OrbEngine()
    feed(engine, [*OPENING, BREAK, *drift("09:50", "15:55")])
    feed(engine, [bar(29, "09:30", 90.0, 91.0, 89.0, 90.5)])
    assert (engine.or_high, engine.or_low, engine.or_state) == (91.0, 89.0, "building")
    assert engine.trades_today == 0


@pytest.mark.unit
def test_a_setup_below_the_minimum_reward_to_risk_is_not_traded():
    engine = OrbEngine(OrbConfig(min_rr=2.5))
    [skip] = feed(engine, [*OPENING, BREAK])
    assert skip.kind == "skip" and "R:R" in skip.reason
    assert engine.trade is None and engine.trades_today == 0


@pytest.mark.unit
def test_a_side_rearms_only_after_a_close_back_inside_the_range():
    engine = OrbEngine(OrbConfig(use_rvol=True, rvol_min=100.0, rvol_len=1))
    assert kinds(feed(engine, [*OPENING, BREAK])) == ["skip"]
    assert feed(engine, [bar(28, "09:50", 102.5, 103.0, 102.4, 102.9)]) == []
    assert kinds(feed(engine, [
        bar(28, "09:55", 102.9, 103.0, 101.0, 101.5),
        bar(28, "10:00", 101.5, 102.9, 101.4, 102.6),
    ])) == ["skip"]


@pytest.mark.unit
def test_max_trades_per_day_blocks_further_entries():
    engine = OrbEngine(OrbConfig(max_trades=1))
    feed(engine, [*OPENING, BREAK, bar(28, "09:50", 102.5, 102.6, 99.9, 101.0)])
    assert feed(engine, [bar(28, "09:55", 101.0, 102.8, 101.0, 102.6)]) == []


@pytest.mark.unit
def test_break_and_retest_waits_for_the_retest():
    engine = OrbEngine(OrbConfig(entry_mode="break_retest"))
    assert feed(engine, [*OPENING, BREAK]) == []
    assert engine.retest_pending
    [enter] = feed(engine, [bar(28, "09:50", 102.5, 102.7, 101.9, 102.4)])
    assert (enter.kind, enter.price) == ("enter", 102.4)


@pytest.mark.unit
def test_time_exit_stops_new_entries():
    engine = OrbEngine(OrbConfig(time_exit=time(9, 50)))
    assert feed(engine, [*OPENING, BREAK]) == []


@pytest.mark.unit
def test_wilder_atr_seeds_with_the_simple_average():
    atr = wilder_atr([(2.0, 1.0, 1.5), (3.0, 1.0, 2.0), (2.5, 2.0, 2.2)], 2)
    assert atr == [None, 1.5, 1.0]  # true ranges 1, 2, 0.5


@pytest.mark.unit
def test_shares_risk_the_set_amount_and_respect_the_position_cap():
    assert shares_for(100, 20_000, 102.5, 100.0) == 40
    assert shares_for(100, 2_000, 102.5, 100.0) == 19
    assert shares_for(100, 20_000, 100.0, 100.0) == 0


@pytest.mark.unit
def test_settings_parse_times_and_reject_unknown_or_inconsistent_values():
    s = settings_from_dict({
        "bot": {"symbols": ["nvda"], "risk_per_trade_usd": 50},
        "orb": {"time_exit": "11:30", "session_start": "09:30", "or_minutes": 30},
    })
    assert s.symbols == ("NVDA",)
    assert (s.orb.time_exit, s.orb.or_minutes) == (time(11, 30), 30)
    assert settings_from_dict({"orb": {"time_exit": ""}}).orb.time_exit is None
    with pytest.raises(ValueError, match="unknown"):
        settings_from_dict({"orb": {"stopmode": "atr"}})
    with pytest.raises(ValueError, match="divide"):
        settings_from_dict({"bot": {"bar_minutes": 10}, "orb": {"or_minutes": 15}})
    with pytest.raises(ValueError, match="stop_mode"):
        settings_from_dict({"orb": {"stop_mode": "wide"}})
