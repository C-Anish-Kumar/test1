"""Backtest the ORB bot's rules on IBKR history.

    python -m tradingagents.orb.backtest --config orb_bot.toml --months 6
    python -m tradingagents.orb.backtest --config orb_bot.toml --set stop_mode=mid

Replays IBKR bars (the timeframe in ``bar_minutes``, regular hours only)
through the same OrbEngine the live bot uses, sizes each trade the way the
bot would, subtracts a per-share cost, and prints win rate, average R, profit
factor, both halves of the period, and breakdowns by exit, entry hour and
symbol. Every trade goes to a CSV.

Fills are modelled as the engine sees them: entry at the signal bar's close,
stop and targets at their prices, other exits at the bar's close. Real fills
add slippage, which ``--cost-per-share`` stands in for. Needs IB Gateway or
TWS running (it only reads data) and the optional dependency:
pip install "tradingagents[orb]".
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import BotSettings, load_settings
from .engine import Bar, OrbConfig, OrbEngine, wilder_atr
from .ibkr import shares_for

MIN_AVG_R = 0.10


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry: float
    stop: float
    exit: float
    reason: str
    r: float  # before costs
    r_net: float  # after costs
    shares: int
    risk_usd: float  # shares * (entry - stop)


def daily_atr_lookup(days: list[date], atrs: list[float | None]) -> Callable[[date], float | None]:
    """ATR as of the last completed day before the given day (no look-ahead)."""
    pairs = [(d, a) for d, a in zip(days, atrs, strict=True) if a is not None]
    ds = [d for d, _ in pairs]
    vs = [a for _, a in pairs]

    def lookup(day: date) -> float | None:
        i = bisect.bisect_left(ds, day)
        return vs[i - 1] if i > 0 else None

    return lookup


def replay(
    symbol: str,
    bars: list[Bar],
    cfg: OrbConfig,
    settings: BotSettings,
    daily_atr: Callable[[date], float | None] = lambda _day: None,
    cost_per_share: float = 0.0,
) -> list[TradeRecord]:
    """Run completed bars through the engine and collect the closed trades."""
    engine = OrbEngine(cfg)
    trades: list[TradeRecord] = []
    opened = None
    for bar in bars:
        for a in engine.on_bar(bar, daily_atr(bar.start.date())):
            if a.kind == "enter":
                shares = shares_for(settings.risk_per_trade_usd, settings.max_position_usd, a.price, a.stop)
                if shares < 1:  # the bot skips these too
                    engine.drop_open_trade()
                    continue
                opened = (a, shares)
            elif a.kind == "exit" and opened is not None:
                entry, shares = opened
                risk_ps = entry.price - entry.stop
                trades.append(TradeRecord(
                    symbol, entry.at, a.at, entry.price, entry.stop, a.price, a.reason,
                    a.r, a.r - cost_per_share / risk_ps, shares, shares * risk_ps,
                ))
                opened = None
    return trades


def _fmt(r: float) -> str:
    return f"{r:+.2f}R"


def _group(trades: list[TradeRecord], key: Callable[[TradeRecord], object]) -> list[str]:
    groups: dict[object, list[float]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t.r_net)
    return [f"  {k!s:<16} {len(v):>4}  mean {_fmt(statistics.fmean(v))}  sum {_fmt(sum(v))}"
            for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))]


def summarize(trades: list[TradeRecord], tz: ZoneInfo | None = None) -> str:
    if not trades:
        return "No trades."
    trades = sorted(trades, key=lambda t: t.entry_time)
    rs = [t.r_net for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    pf = sum(wins) / -sum(losses) if losses else math.inf
    dollars = sum(t.r_net * t.risk_usd for t in trades)
    half = len(trades) // 2
    first, second = rs[:half], rs[half:]
    split_day = trades[half].entry_time.date()
    first_avg = statistics.fmean(first) if first else 0.0
    second_avg = statistics.fmean(second)
    passed = bool(first) and first_avg >= MIN_AVG_R and second_avg >= MIN_AVG_R

    def hour(t: TradeRecord) -> str:
        return f"{(t.entry_time.astimezone(tz) if tz else t.entry_time):%H}:00"

    lines = [
        f"Trades: {len(trades)}   win rate {len(wins) / len(rs):.0%}   avg {_fmt(statistics.fmean(rs))}"
        f"   median {_fmt(statistics.median(rs))}   profit factor {pf:.2f}",
        f"Total {_fmt(sum(rs))} = ${dollars:,.0f} at the bot's sizing"
        f" (avg $ risk {statistics.fmean(t.risk_usd for t in trades):,.0f} a trade)",
        f"1st half {_fmt(first_avg)} ({len(first)})   2nd half {_fmt(second_avg)} ({len(second)})"
        f"   (split at {split_day})",
        "",
        "By exit:",
        *_group(trades, lambda t: t.reason),
        "",
        "By entry hour:",
        *_group(trades, hour),
        "",
        "By symbol:",
        *_group(trades, lambda t: t.symbol),
        "",
        f"Worth paper trading only if avg R >= {_fmt(MIN_AVG_R)} in BOTH halves (after costs): "
        + ("YES" if passed else "NO"),
    ]
    return "\n".join(lines)


def write_csv(trades: list[TradeRecord], path: str) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(TradeRecord.__dataclass_fields__))
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def bar_size(minutes: int) -> str:
    """IBKR's barSizeSetting spelling."""
    return "1 min" if minutes == 1 else f"{minutes} mins"


def fetch_bars(ib, contract, minutes: int, months: int, tz: ZoneInfo) -> list[Bar]:
    """Regular-hours bars for the last ``months`` months, oldest first, completed bars only."""
    raw = {}
    end = ""
    for _ in range(months):
        chunk = ib.reqHistoricalData(
            contract, endDateTime=end, durationStr="1 M", barSizeSetting=bar_size(minutes),
            whatToShow="TRADES", useRTH=True, formatDate=2,
        )
        if not chunk:
            break
        for b in chunk:
            raw[b.date] = b
        end = chunk[0].date
    now = datetime.now(tz)
    bars = []
    for key in sorted(raw):
        b = raw[key]
        start = key.astimezone(tz)
        stop = start + timedelta(minutes=minutes)
        if stop <= now:
            bars.append(Bar(start, stop, b.open, b.high, b.low, b.close, b.volume))
    return bars


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradingagents.orb.backtest",
                                     description="Backtest the ORB bot's rules on IBKR history.")
    parser.add_argument("--config", required=True, help="TOML settings file (same as the bot)")
    parser.add_argument("--months", type=int, default=6, help="months of history (default 6)")
    parser.add_argument("--cost-per-share", type=float, default=0.02,
                        help="round-trip commission + slippage per share in $ (default 0.02)")
    parser.add_argument("--out", default="orb_backtest_trades.csv", help="CSV of every trade")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a setting for this run, e.g. --set stop_mode=mid"
                             " (repeatable; any [bot] or [orb] key)")
    args = parser.parse_args(argv)
    try:
        from ib_async import IB, Stock
    except ImportError:
        raise SystemExit('ib_async is not installed: pip install "tradingagents[orb]"') from None

    try:
        settings = load_settings(args.config, args.set)
    except ValueError as exc:
        raise SystemExit(f"Bad --set: {exc}") from None
    tz = ZoneInfo(settings.timezone)
    ib = IB()
    # A separate client id, so a running bot keeps its connection.
    ib.connect(settings.host, settings.port, clientId=settings.client_id + 50, readonly=True)
    trades: list[TradeRecord] = []
    sessions: set[date] = set()
    try:
        for symbol in settings.symbols:
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            daily = ib.reqHistoricalData(contract, endDateTime="", durationStr="2 Y",
                                         barSizeSetting="1 day", whatToShow="TRADES",
                                         useRTH=True, formatDate=1)
            lookup = daily_atr_lookup(
                [b.date for b in daily],
                wilder_atr([(b.high, b.low, b.close) for b in daily], settings.orb.atr_len),
            )
            bars = fetch_bars(ib, contract, settings.bar_minutes, args.months, tz)
            sessions |= {b.start.date() for b in bars}
            found = replay(symbol, bars, settings.orb, settings, lookup, args.cost_per_share)
            print(f"{symbol}: {len(bars)} bars, {len(found)} trades")
            trades += found
    finally:
        ib.disconnect()

    if sessions:
        print(f"Sessions: {len(sessions)} ({min(sessions)} to {max(sessions)}), "
              f"{settings.bar_minutes}m bars, longs only, cost ${args.cost_per_share}/share")
    if args.set:
        print("Overrides: " + ", ".join(args.set))
    print(summarize(trades, tz))
    write_csv(trades, args.out)
    print(f"Trades written to {args.out}")


if __name__ == "__main__":
    main()
