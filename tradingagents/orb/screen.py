"""Pick tickers that suit the ORB rules, judged on data the picking never saw.

    python -m tradingagents.orb.screen --config orb_bot.toml --months 12

Backtests each candidate ticker over ``--months`` of IBKR history and splits
the period in two by date. Tickers are selected on the first half only, by
rules fixed before the run (enough trades, average R and profit factor above
the thresholds). The selected basket is then scored on the second half, which
played no part in the choice. That second-half number is the honest estimate:
choosing tickers by their full-period backtest would just pick the ones that
happened to do well in the past.

Accepts the backtest's ``--set`` overrides, so a variation (e.g. the volume
filter) can be screened the same way. Needs IB Gateway or TWS running; it only
reads data.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from .backtest import MIN_AVG_R, TradeRecord, daily_atr_lookup, fetch_bars, replay, write_csv
from .config import load_settings
from .engine import wilder_atr

# Liquid, actively traded US names and ETFs that tend to move after the open.
DEFAULT_CANDIDATES = (
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "MU", "TSLA",
    "META", "AMZN", "GOOGL", "NFLX", "PLTR", "COIN", "SMCI", "CRWD", "UBER", "BA",
)


@dataclass(frozen=True)
class Rules:
    min_trades: int = 10
    min_avg_r: float = MIN_AVG_R
    min_profit_factor: float = 1.2


@dataclass(frozen=True)
class Stats:
    n: int
    avg: float
    pf: float
    win_rate: float


def stats(trades: list[TradeRecord]) -> Stats:
    rs = [t.r_net for t in trades]
    if not rs:
        return Stats(0, 0.0, 0.0, 0.0)
    wins = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    pf = wins / losses if losses else math.inf
    return Stats(len(rs), statistics.fmean(rs), pf, sum(r > 0 for r in rs) / len(rs))


def split_by_date(trades: list[TradeRecord], split: date) -> tuple[list[TradeRecord], list[TradeRecord]]:
    before = [t for t in trades if t.entry_time.date() < split]
    return before, [t for t in trades if t.entry_time.date() >= split]


def select(by_symbol: dict[str, list[TradeRecord]], split: date, rules: Rules) -> list[str]:
    """Tickers that pass the rules on the selection period (before ``split``) only."""
    chosen = []
    for symbol, trades in by_symbol.items():
        s = stats(split_by_date(trades, split)[0])
        if s.n >= rules.min_trades and s.avg >= rules.min_avg_r and s.pf >= rules.min_profit_factor:
            chosen.append(symbol)
    return sorted(chosen)


def _row(label: str, s: Stats) -> str:
    pf = "inf" if math.isinf(s.pf) else f"{s.pf:.2f}"
    return f"{label:<7}{s.n:>4} {s.avg:>+7.2f}R {pf:>5}"


def report(by_symbol: dict[str, list[TradeRecord]], split: date, first: date, last: date,
           rules: Rules) -> str:
    chosen = select(by_symbol, split, rules)
    lines = [
        f"Selection period {first} to {split}   |   test period {split} to {last}",
        f"Selection rules (selection period only): trades >= {rules.min_trades}, "
        f"avg >= {rules.min_avg_r:+.2f}R, profit factor >= {rules.min_profit_factor:.2f}",
        "",
        "ticker  selection: n     avg    pf  |  test: n     avg    pf  | picked",
    ]
    for symbol in sorted(by_symbol, key=lambda s: -stats(split_by_date(by_symbol[s], split)[0]).avg):
        before, after = split_by_date(by_symbol[symbol], split)
        lines.append(f"{symbol:<7} {_row('', stats(before))}  |  {_row('', stats(after))}  | "
                     + ("YES" if symbol in chosen else ""))
    all_after = [t for trades in by_symbol.values() for t in split_by_date(trades, split)[1]]
    picked_after = [t for s in chosen for t in split_by_date(by_symbol[s], split)[1]]
    every, picked = stats(all_after), stats(picked_after)
    held = picked.n >= rules.min_trades and picked.avg >= MIN_AVG_R
    lines += [
        "",
        f"All candidates, test period:    {every.n} trades, win rate {every.win_rate:.0%}, "
        f"avg {every.avg:+.2f}R, pf {every.pf:.2f}",
    ]
    if not chosen:
        lines += ["Picked: none passed the selection rules.", "",
                  "Verdict: NO ticker suits these settings."]
        return "\n".join(lines)
    lines += [
        f"Picked ({len(chosen)}): {', '.join(chosen)}",
        f"Picked, test period:            {picked.n} trades, win rate {picked.win_rate:.0%}, "
        f"avg {picked.avg:+.2f}R, pf {picked.pf:.2f}",
        "",
        f"Verdict: the picked tickers {'HELD UP' if held else 'did NOT hold up'} on data they were"
        f" not chosen on (needs >= {rules.min_trades} trades and avg >= {MIN_AVG_R:+.2f}R).",
    ]
    if held:
        lines.append(f"To paper trade them, set symbols = {json.dumps(chosen)} in orb_bot.toml")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradingagents.orb.screen",
                                     description="Pick tickers that suit the ORB rules, out of sample.")
    parser.add_argument("--config", required=True, help="TOML settings file (same as the bot)")
    parser.add_argument("--months", type=int, default=12, help="months of history (default 12)")
    parser.add_argument("--symbols", default=",".join(DEFAULT_CANDIDATES),
                        help="comma-separated candidates (default: 20 liquid US names and ETFs)")
    parser.add_argument("--cost-per-share", type=float, default=0.02)
    parser.add_argument("--min-trades", type=int, default=Rules.min_trades)
    parser.add_argument("--min-avg-r", type=float, default=Rules.min_avg_r)
    parser.add_argument("--min-pf", type=float, default=Rules.min_profit_factor)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a setting for this run, e.g. --set use_rvol=true")
    parser.add_argument("--out", default="orb_screen_trades.csv", help="CSV of every trade")
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
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    ib = IB()
    ib.connect(settings.host, settings.port, clientId=settings.client_id + 51, readonly=True)
    by_symbol: dict[str, list[TradeRecord]] = {}
    days: set[date] = set()
    try:
        for symbol in symbols:
            contract = Stock(symbol, "SMART", "USD")
            if not ib.qualifyContracts(contract):
                print(f"{symbol}: not found, skipped")
                continue
            daily = ib.reqHistoricalData(contract, endDateTime="", durationStr="2 Y",
                                         barSizeSetting="1 day", whatToShow="TRADES",
                                         useRTH=True, formatDate=1)
            lookup = daily_atr_lookup(
                [b.date for b in daily],
                wilder_atr([(b.high, b.low, b.close) for b in daily], settings.orb.atr_len),
            )
            bars = fetch_bars(ib, contract, settings.bar_minutes, args.months, tz)
            if not bars:
                print(f"{symbol}: no data, skipped")
                continue
            days |= {b.start.date() for b in bars}
            by_symbol[symbol] = replay(symbol, bars, settings.orb, settings, lookup,
                                       args.cost_per_share)
            print(f"{symbol}: {len(bars)} bars, {len(by_symbol[symbol])} trades")
    finally:
        ib.disconnect()
    if not by_symbol:
        raise SystemExit("No data for any symbol.")

    first, last = min(days), max(days)
    split = first + (last - first) / 2
    print(f"\n{settings.bar_minutes}m bars, longs only, cost ${args.cost_per_share}/share"
          + (f", overrides: {', '.join(args.set)}" if args.set else ""))
    print(report(by_symbol, split, first, last,
                 Rules(args.min_trades, args.min_avg_r, args.min_pf)))
    write_csv([t for trades in by_symbol.values() for t in trades], args.out)
    print(f"Trades written to {args.out}")


if __name__ == "__main__":
    main()
