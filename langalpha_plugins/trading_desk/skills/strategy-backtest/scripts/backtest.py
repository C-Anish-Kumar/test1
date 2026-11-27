"""Backtest a trading strategy on OHLCV bars.

Usage:
    python .agents/skills/strategy-backtest/scripts/backtest.py BARS \
        --strategy NAME [--params '{"fast": 20}'] [--custom my_rules.py] \
        [--side long|short|both] [--stop-atr 1.5] [--target-r 2] \
        [--capital 100000] [--risk-pct 1] [--fee-bps 1] [--slippage-bps 2] \
        [--no-flatten] [--max-bars N] [--out DIR] [--chart]

Strategies (signal on a bar's close, entry at the next bar's open):
    ema_cross      fast/slow EMA cross                 params: fast=20, slow=50
    vwap_ema       VWAP reclaim/rejection in the 200 EMA trend with a volume
                   surge                               params: ema=200, vol_mult=1.5
    orb            opening-range breakout, one trade a session (intraday only)
                                                       params: minutes=15
    rsi_reversion  RSI back above oversold / below overbought, exit at the mid
                                                       params: n=14, lo=30, hi=70
    donchian       close beyond the prior N-bar high/low  params: n=20
    custom         --custom FILE defining signals(df, params) -> Series of
                   +1 (go long), -1 (go short), 0 (nothing), aligned to df

Writes stats.json, trades.csv, trades.json and equity.json to --out (default
<bars>_backtest/) and prints the headline stats. --chart also builds an
interactive chart.html there with the trades marked and the equity curve.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CHART_DIR = Path(__file__).resolve().parents[2] / "price-chart" / "scripts"
sys.path.insert(0, str(CHART_DIR))
from indicators import atr, ema, is_intraday, load_bars, rsi, sma, vwap  # noqa: E402

# ── Signals ─────────────────────────────────────────────────────────────────
# Each returns a Series aligned to df: +1 long entry, -1 short entry, 0 none.
# An optional second Series ("exit") flags bars whose close ends a position.

def sig_ema_cross(df, p):
    f, s = ema(df.close, int(p.get("fast", 20))), ema(df.close, int(p.get("slow", 50)))
    valid = f.notna() & s.notna()
    above = (f > s).astype(int)
    # A cross needs both EMAs warmed up on this bar and the one before, so the
    # end of the warm-up period is not read as a cross.
    cross = above.diff().where(valid & valid.shift(fill_value=False), 0).fillna(0)
    return cross.clip(-1, 1).astype(int), None


def sig_vwap_ema(df, p):
    trend = ema(df.close, int(p.get("ema", 200)))
    v = vwap(df)
    vol_ok = df.volume >= float(p.get("vol_mult", 1.5)) * sma(df.volume, 20)
    reclaim = (df.close > v) & (df.close.shift() <= v.shift())
    reject = (df.close < v) & (df.close.shift() >= v.shift())
    long_ = reclaim & (df.close > trend) & vol_ok
    short = reject & (df.close < trend) & vol_ok
    return long_.astype(int) - short.astype(int), None


def sig_orb(df, p):
    if not is_intraday(df):
        raise SystemExit("orb needs intraday bars (e.g. 5min)")
    minutes = int(p.get("minutes", 15))
    day = df.index.normalize()
    start = df.groupby(day).apply(lambda g: g.index[0])
    first = pd.Series(day.map(start), index=df.index)
    in_range = df.index < first + pd.Timedelta(minutes=minutes)
    hi = df.high.where(in_range).groupby(day).transform("max")
    lo = df.low.where(in_range).groupby(day).transform("min")
    sig = pd.Series(0, index=df.index)
    traded = set()
    for t, c, range_hi, range_lo, r in zip(df.index, df.close, hi, lo, in_range, strict=True):
        d = t.normalize()
        if r or d in traded or np.isnan(range_hi):
            continue
        if c > range_hi:
            sig[t] = 1
            traded.add(d)
        elif c < range_lo:
            sig[t] = -1
            traded.add(d)
    return sig, None


def sig_rsi_reversion(df, p):
    n, lo, hi = int(p.get("n", 14)), float(p.get("lo", 30)), float(p.get("hi", 70))
    r = rsi(df.close, n)
    long_ = (r > lo) & (r.shift() <= lo)
    short = (r < hi) & (r.shift() >= hi)
    mid = (lo + hi) / 2
    exit_ = ((r >= mid) & (r.shift() < mid)) | ((r <= mid) & (r.shift() > mid))
    return long_.astype(int) - short.astype(int), exit_


def sig_donchian(df, p):
    n = int(p.get("n", 20))
    up = df.high.rolling(n).max().shift()
    dn = df.low.rolling(n).min().shift()
    return (df.close > up).astype(int) - (df.close < dn).astype(int), None


STRATEGIES = {
    "ema_cross": sig_ema_cross,
    "vwap_ema": sig_vwap_ema,
    "orb": sig_orb,
    "rsi_reversion": sig_rsi_reversion,
    "donchian": sig_donchian,
}


def load_custom(path: str):
    spec = importlib.util.spec_from_file_location("custom_rules", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "signals"):
        raise SystemExit(f"{path} must define signals(df, params)")

    def run(df, p):
        out = mod.signals(df.copy(), p)
        exit_ = None
        if isinstance(out, tuple):
            out, exit_ = out
        out = pd.Series(out, index=df.index).fillna(0).astype(int).clip(-1, 1)
        return out, exit_

    return run


# ── Engine ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, signal: pd.Series, exit_flag, *, side="both",
                 stop_atr=1.5, target_r=2.0, capital=100_000.0, risk_pct=1.0,
                 fee_bps=1.0, slip_bps=2.0, flatten=None, max_bars=None):
    """Bar-by-bar simulation. Returns (trades DataFrame, equity Series)."""
    if flatten is None:
        flatten = is_intraday(df)
    a = atr(df, 14)
    o, h, lw, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig = signal.reindex(df.index).fillna(0).to_numpy()
    ex = exit_flag.reindex(df.index).fillna(False).to_numpy() if exit_flag is not None else None
    days = df.index.normalize()
    last_of_day = np.r_[days[1:] != days[:-1], True]
    cost = (fee_bps + slip_bps) / 1e4

    equity = capital
    curve = np.full(len(df), np.nan)
    trades = []
    pos = None  # dict while in a trade

    def close(i, price, reason):
        nonlocal equity, pos
        d = pos["dir"]
        fill = price * (1 - d * cost)
        pnl = d * (fill - pos["fill"]) * pos["qty"]
        equity += pnl
        r = pnl / pos["risk_cash"] if pos["risk_cash"] else np.nan
        trades.append({
            "entry_time": str(df.index[pos["i"]]), "exit_time": str(df.index[i]),
            "side": "long" if d > 0 else "short", "entry": round(pos["fill"], 6),
            "exit": round(fill, 6), "qty": round(pos["qty"], 6), "stop": pos["stop"],
            "target": pos["target"], "pnl": round(pnl, 2), "r": None if np.isnan(r) else round(r, 3),
            "bars": int(i - pos["i"] + 1), "reason": reason,
        })
        pos = None

    for i in range(len(df)):
        # Enter at this bar's open on the previous bar's signal.
        if pos is None and i > 0:
            s = sig[i - 1]
            allowed = (s > 0 and side in ("long", "both")) or (s < 0 and side in ("short", "both"))
            if s and allowed and not (flatten and last_of_day[i - 1] and days[i] != days[i - 1]):
                d = 1 if s > 0 else -1
                fill = o[i] * (1 + d * cost)
                ai = a.iloc[i - 1]
                stop = target = None
                if stop_atr and not np.isnan(ai) and ai > 0:
                    stop = fill - d * stop_atr * ai
                    if target_r:
                        target = fill + d * target_r * stop_atr * ai
                per_share = abs(fill - stop) if stop is not None else fill
                risk_cash = equity * risk_pct / 100 if stop is not None else equity
                qty = risk_cash / per_share if per_share > 0 else 0
                qty = min(qty, equity / fill)  # no leverage
                if qty > 0:
                    pos = {"i": i, "dir": d, "fill": fill, "qty": qty,
                           "stop": None if stop is None else round(stop, 6),
                           "target": None if target is None else round(target, 6),
                           "risk_cash": qty * per_share}
        if pos is not None:
            d, stop, target = pos["dir"], pos["stop"], pos["target"]
            hit_stop = stop is not None and ((d > 0 and lw[i] <= stop) or (d < 0 and h[i] >= stop))
            hit_tgt = target is not None and ((d > 0 and h[i] >= target) or (d < 0 and lw[i] <= target))
            if hit_stop:  # stop first when both touch: the conservative read
                gap = (d > 0 and o[i] < stop) or (d < 0 and o[i] > stop)
                close(i, o[i] if gap else stop, "stop")
            elif hit_tgt:
                gap = (d > 0 and o[i] > target) or (d < 0 and o[i] < target)
                close(i, o[i] if gap else target, "target")
            elif ex is not None and ex[i]:
                close(i, c[i], "exit signal")
            elif sig[i] == -d:
                # An opposite signal always ends the trade; with --side both
                # the next bar's open then enters the other way.
                close(i, c[i], "reverse signal")
            elif flatten and last_of_day[i]:
                close(i, c[i], "session end")
            elif max_bars and i - pos["i"] + 1 >= max_bars:
                close(i, c[i], "max bars")
        mark = equity
        if pos is not None:
            mark += pos["dir"] * (c[i] - pos["fill"]) * pos["qty"]
        curve[i] = mark
    if pos is not None:
        close(len(df) - 1, c[-1], "end of data")
        curve[-1] = equity
    return pd.DataFrame(trades), pd.Series(curve, index=df.index)


def bars_per_year(df: pd.DataFrame) -> float:
    days = df.index.normalize().nunique()
    span_years = max((df.index[-1] - df.index[0]).days / 365.25, 1 / 365.25)
    per_day = len(df) / max(days, 1)
    trading_days = min(days / span_years, 365)
    return per_day * trading_days


def summarize(df, trades, curve, capital):
    st = {"bars": int(len(df)), "from": str(df.index[0]), "to": str(df.index[-1]),
          "trades": int(len(trades))}
    end = float(curve.iloc[-1])
    st["start_equity"] = capital
    st["end_equity"] = round(end, 2)
    st["total_return_pct"] = round((end / capital - 1) * 100, 2)
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    st["cagr_pct"] = round(((end / capital) ** (1 / years) - 1) * 100, 2) if end > 0 and years >= 0.25 else None
    peak = curve.cummax()
    st["max_drawdown_pct"] = round(float(((curve / peak) - 1).min() * 100), 2)
    rets = curve.pct_change().dropna()
    if rets.std() > 0:
        st["sharpe"] = round(float(rets.mean() / rets.std() * math.sqrt(bars_per_year(df))), 2)
    else:
        st["sharpe"] = None
    st["buy_hold_return_pct"] = round((df.close.iloc[-1] / df.close.iloc[0] - 1) * 100, 2)
    if len(trades):
        wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        st["win_rate_pct"] = round(len(wins) / len(trades) * 100, 1)
        gp, gl = wins.pnl.sum(), -losses.pnl.sum()
        st["profit_factor"] = round(float(gp / gl), 2) if gl > 0 else None
        r = trades.r.dropna()
        st["expectancy_r"] = round(float(r.mean()), 3) if len(r) else None
        st["avg_win_r"] = round(float(r[r > 0].mean()), 3) if (r > 0).any() else None
        st["avg_loss_r"] = round(float(r[r <= 0].mean()), 3) if (r <= 0).any() else None
        st["avg_bars_held"] = round(float(trades.bars.mean()), 1)
        streak = longest = 0
        for p in trades.pnl:
            streak = streak + 1 if p <= 0 else 0
            longest = max(longest, streak)
        st["longest_losing_streak"] = int(longest)
        st["exits"] = trades.reason.value_counts().to_dict()
        st["by_side"] = {
            s: {"trades": int(len(g)), "win_rate_pct": round((g.pnl > 0).mean() * 100, 1),
                "pnl": round(float(g.pnl.sum()), 2)}
            for s, g in trades.groupby("side")
        }
        if len(trades) < 30:
            st["warning"] = f"only {len(trades)} trades: too few to judge the strategy"
    return st


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest a strategy on OHLCV bars.")
    ap.add_argument("bars")
    ap.add_argument("--strategy", required=True, choices=[*STRATEGIES, "custom"])
    ap.add_argument("--params", default="{}", help="JSON object of strategy parameters")
    ap.add_argument("--custom", help="Python file defining signals(df, params)")
    ap.add_argument("--side", default="both", choices=["long", "short", "both"])
    ap.add_argument("--stop-atr", type=float, default=1.5, help="stop distance in ATR(14); 0 = no stop")
    ap.add_argument("--target-r", type=float, default=2.0, help="target as a multiple of the stop; 0 = none")
    ap.add_argument("--capital", type=float, default=100_000)
    ap.add_argument("--risk-pct", type=float, default=1.0, help="equity risked per trade, %%")
    ap.add_argument("--fee-bps", type=float, default=1.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--no-flatten", action="store_true", help="hold intraday trades overnight")
    ap.add_argument("--max-bars", type=int)
    ap.add_argument("--out")
    ap.add_argument("--chart", action="store_true", help="also build chart.html with trades marked")
    a = ap.parse_args(argv)

    params = json.loads(a.params)
    if a.strategy == "custom":
        if not a.custom:
            ap.error("--strategy custom needs --custom FILE")
        fn = load_custom(a.custom)
    else:
        fn = STRATEGIES[a.strategy]
    df = load_bars(a.bars)
    signal, exit_flag = fn(df, params)
    trades, curve = run_backtest(
        df, signal, exit_flag, side=a.side, stop_atr=a.stop_atr, target_r=a.target_r,
        capital=a.capital, risk_pct=a.risk_pct, fee_bps=a.fee_bps, slip_bps=a.slippage_bps,
        flatten=False if a.no_flatten else None, max_bars=a.max_bars,
    )
    stats = summarize(df, trades, curve, a.capital)
    stats["strategy"] = a.strategy
    stats["params"] = params
    stats["settings"] = {"side": a.side, "stop_atr": a.stop_atr, "target_r": a.target_r,
                         "risk_pct": a.risk_pct, "fee_bps": a.fee_bps,
                         "slippage_bps": a.slippage_bps,
                         "flatten_daily": (not a.no_flatten) and is_intraday(df)}

    out = Path(a.out) if a.out else Path(a.bars).with_name(Path(a.bars).stem + "_backtest")
    out.mkdir(parents=True, exist_ok=True)
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    trades.to_csv(out / "trades.csv", index=False)
    (out / "trades.json").write_text(trades.to_json(orient="records"))
    eq = [{"time": str(t), "equity": round(float(v), 2)} for t, v in curve.items()]
    (out / "equity.json").write_text(json.dumps(eq))
    if a.chart:
        cmd = [sys.executable, str(CHART_DIR / "build_chart.py"), a.bars,
               "--out", str(out / "chart.html"), "--trades", str(out / "trades.json"),
               "--equity", str(out / "equity.json"),
               "--title", f"{a.strategy} backtest", "--stats", str(out / "stats.json")]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        stats["chart"] = str(out / "chart.html")
    print(json.dumps({k: stats[k] for k in stats if k not in ("params", "settings")}, indent=1))
    print(f"files: {out}/stats.json trades.csv trades.json equity.json"
          + (" chart.html" if a.chart else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
