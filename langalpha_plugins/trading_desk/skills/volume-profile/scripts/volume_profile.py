"""Volume profile and approximate delta from OHLCV bars.

Usage:
    python .agents/skills/volume-profile/scripts/volume_profile.py BARS \
        [--bins 60] [--value-area 0.70] [--per-session] [--out profile.json]

BARS is a JSON or CSV file of {date, open, high, low, close, volume} rows.
Prints a short summary and writes the profile JSON (default: next to BARS).

Bars carry no trade-by-trade data, so two things here are estimates:
- each bar's volume is spread evenly across its high-low range, and
- each bar's volume is split into buying and selling by where it closed in its
  range (close at the high = all buying, at the low = all selling).
True footprint/orderflow needs tick or trade data, which bars do not have.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "price-chart" / "scripts"))
from indicators import is_intraday, load_bars  # noqa: E402


def profile(df: pd.DataFrame, bins: int = 60, value_area: float = 0.70) -> dict:
    """Volume at price over the bars in ``df``.

    Returns bin edges and volumes, the point of control (POC: the busiest
    price), the value area (VAH/VAL: the price band holding ``value_area`` of
    the volume, grown outward from the POC) and high/low volume nodes.
    """
    lo, hi = float(df["low"].min()), float(df["high"].max())
    if hi <= lo:
        hi = lo + max(abs(lo) * 1e-6, 1e-9)
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    buy = np.zeros(bins)
    width = edges[1] - edges[0]
    for h, lw, c, v in df[["high", "low", "close", "volume"]].itertuples(index=False):
        if not v or v <= 0:
            continue
        rng = h - lw
        share_buy = 0.5 if rng <= 0 else (c - lw) / rng
        if rng <= 0:
            i = min(int((c - lo) / width), bins - 1)
            vol[i] += v
            buy[i] += v * share_buy
            continue
        # Overlap of [lw, h] with each bin, as a share of the bar's range.
        i0 = max(int((lw - lo) / width), 0)
        i1 = min(int((h - lo) / width), bins - 1)
        for i in range(i0, i1 + 1):
            overlap = min(h, edges[i + 1]) - max(lw, edges[i])
            if overlap > 0:
                part = v * overlap / rng
                vol[i] += part
                buy[i] += part * share_buy

    total = vol.sum()
    poc_i = int(vol.argmax())
    # Value area: grow from the POC, adding the larger neighbour each step.
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    target = value_area * total
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]

    mids = (edges[:-1] + edges[1:]) / 2
    # Volume nodes: local peaks/troughs of a lightly smoothed profile.
    smooth = np.convolve(vol, np.ones(3) / 3, mode="same")
    mean = smooth.mean()
    hvn = [float(mids[i]) for i in range(1, bins - 1)
           if smooth[i] > smooth[i - 1] and smooth[i] >= smooth[i + 1] and smooth[i] > 1.2 * mean]
    lvn = [float(mids[i]) for i in range(1, bins - 1)
           if smooth[i] < smooth[i - 1] and smooth[i] <= smooth[i + 1] and smooth[i] < 0.6 * mean]

    return {
        "bars": int(len(df)),
        "from": str(df.index[0]),
        "to": str(df.index[-1]),
        "bins": bins,
        "edges": [float(x) for x in edges],
        "volume": [float(x) for x in vol],
        "buy_volume": [float(x) for x in buy],
        "sell_volume": [float(x) for x in vol - buy],
        "total_volume": float(total),
        "poc": float(mids[poc_i]),
        "vah": float(edges[hi_i + 1]),
        "val": float(edges[lo_i]),
        "value_area_pct": float(acc / total) if total else 0.0,
        "hvn": hvn,
        "lvn": lvn,
    }


def delta(df: pd.DataFrame) -> pd.DataFrame:
    """Approximate per-bar delta (buy minus sell volume) and its running sum.

    On intraday bars the cumulative delta resets each session.
    """
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    share_buy = ((df["close"] - df["low"]) / rng).fillna(0.5)
    d = df["volume"] * (2 * share_buy - 1)
    cum = d.groupby(df.index.normalize()).cumsum() if is_intraday(df) else d.cumsum()
    return pd.DataFrame({"delta": d, "cum_delta": cum})


def session_profiles(df: pd.DataFrame, bins: int, value_area: float) -> list[dict]:
    """One profile per trading day (intraday bars only)."""
    out = []
    for day, g in df.groupby(df.index.normalize()):
        p = profile(g, bins=bins, value_area=value_area)
        out.append({"session": str(day.date()), "poc": p["poc"], "vah": p["vah"],
                    "val": p["val"], "volume": p["total_volume"]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bars")
    ap.add_argument("--bins", type=int, default=60)
    ap.add_argument("--value-area", type=float, default=0.70)
    ap.add_argument("--per-session", action="store_true",
                    help="also profile each trading day (intraday bars)")
    ap.add_argument("--out", help="output JSON (default: <bars>_profile.json)")
    a = ap.parse_args(argv)

    if not 5 <= a.bins <= 500:
        ap.error("--bins must be between 5 and 500")
    if not 0.1 <= a.value_area <= 0.99:
        ap.error("--value-area must be between 0.1 and 0.99")

    df = load_bars(a.bars)
    result = profile(df, a.bins, a.value_area)
    d = delta(df)
    result["delta"] = {
        "net": float(d["delta"].sum()),
        "last_cum": float(d["cum_delta"].iloc[-1]),
        "method": "close-location estimate from bars, not trade data",
    }
    if a.per_session and is_intraday(df):
        result["sessions"] = session_profiles(df, max(10, a.bins // 2), a.value_area)

    out = Path(a.out) if a.out else Path(a.bars).with_name(Path(a.bars).stem + "_profile.json")
    out.write_text(json.dumps(result, indent=1))
    last = float(df["close"].iloc[-1])
    where = "above value" if last > result["vah"] else "below value" if last < result["val"] else "inside value"
    print(json.dumps({
        "file": str(out), "bars": result["bars"], "range": [result["from"], result["to"]],
        "poc": round(result["poc"], 4), "vah": round(result["vah"], 4), "val": round(result["val"], 4),
        "last_close": last, "last_close_vs_value": where,
        "hvn": [round(x, 4) for x in result["hvn"][:6]], "lvn": [round(x, 4) for x in result["lvn"][:6]],
        "net_delta_estimate": round(result["delta"]["net"]),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
