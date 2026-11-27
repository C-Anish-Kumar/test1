"""Bar loading and indicators shared by the Trading Desk skills.

Every function takes and returns pandas objects indexed by bar time, oldest
first. Definitions follow the common charting conventions (Wilder smoothing for
RSI and ATR, session-anchored VWAP on intraday bars) so values line up with
what a trader sees on other platforms, give or take the data feed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def load_bars(path: str | Path) -> pd.DataFrame:
    """Read bars from JSON or CSV into a DataFrame indexed by time.

    Accepts the price-data tool's response as saved (``{"data": [...]}``), a
    bare list of ``{date, open, high, low, close, volume}`` rows, or a CSV with
    those columns (``date``, ``time``, ``datetime`` or ``timestamp`` for time).
    """
    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raw = json.loads(path.read_text())
        rows = raw.get("data", raw) if isinstance(raw, dict) else raw
        if isinstance(raw, dict) and "error" in raw and not rows:
            raise ValueError(f"bars file holds an error response: {raw.get('error')}")
        df = pd.DataFrame(rows)
    df.columns = [str(c).strip().lower() for c in df.columns]
    time_col = next((c for c in ("date", "datetime", "time", "timestamp") if c in df.columns), None)
    if time_col is None:
        raise ValueError("bars need a date/datetime/time/timestamp column")
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bars are missing columns: {missing}")
    t = df[time_col]
    if pd.api.types.is_numeric_dtype(t):
        # Epoch seconds or milliseconds.
        unit = "ms" if t.max() > 1e11 else "s"
        idx = pd.to_datetime(t, unit=unit)
    else:
        idx = pd.to_datetime(t)
    if getattr(idx.dt, "tz", None) is not None:
        idx = idx.dt.tz_localize(None)
    out = df[COLUMNS].astype(float)
    out.index = pd.DatetimeIndex(idx, name="time")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])
    if out.empty:
        raise ValueError("no usable bars")
    return out


def is_intraday(df: pd.DataFrame) -> bool:
    """True when more than one bar shares a calendar day."""
    return bool(df.index.normalize().duplicated().any())


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return _wilder(tr, n)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = _wilder(d.clip(lower=0), n)
    down = _wilder(-d.clip(upper=0), n)
    rs = up / down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(down != 0, 100.0)


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP on intraday bars (resets each day), anchored VWAP from the
    first bar on daily or slower bars, from hlc3 x volume."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    if is_intraday(df):
        day = df.index.normalize()
        cum_v = df["volume"].groupby(day).cumsum()
        cum_pv = pv.groupby(day).cumsum()
    else:
        cum_v = df["volume"].cumsum()
        cum_pv = pv.cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def parse_overlay(spec: str) -> tuple[str, list[float]]:
    """'ema:200' -> ('ema', [200.0]); 'bb:20:2' -> ('bb', [20.0, 2.0])."""
    parts = [p for p in spec.strip().lower().split(":") if p]
    if not parts:
        raise ValueError("empty overlay spec")
    return parts[0], [float(p) for p in parts[1:]]


def compute_overlay(df: pd.DataFrame, spec: str) -> dict[str, pd.Series]:
    """Named series for one overlay spec; keys become legend labels."""
    name, a = parse_overlay(spec)
    c = df["close"]
    if name in ("ema", "sma"):
        n = int(a[0]) if a else 20
        f = ema if name == "ema" else sma
        return {f"{name.upper()} {n}": f(c, n)}
    if name == "vwap":
        return {"VWAP": vwap(df)}
    if name == "bb":
        n = int(a[0]) if a else 20
        k = a[1] if len(a) > 1 else 2.0
        up, mid, lo = bollinger(c, n, k)
        return {f"BB {n} upper": up, f"BB {n} mid": mid, f"BB {n} lower": lo}
    raise ValueError(f"unknown overlay '{spec}' (use ema:N, sma:N, vwap, bb:N:K)")


def compute_pane(df: pd.DataFrame, spec: str) -> dict:
    """An indicator drawn in its own pane under the price chart."""
    name, a = parse_overlay(spec)
    c = df["close"]
    if name == "rsi":
        n = int(a[0]) if a else 14
        return {"title": f"RSI {n}", "lines": {f"RSI {n}": rsi(c, n)}, "bands": [30, 70]}
    if name == "macd":
        f, s, g = (int(x) for x in (a + [12, 26, 9][len(a):])[:3])
        line, sig, hist = macd(c, f, s, g)
        return {"title": f"MACD {f} {s} {g}", "lines": {"MACD": line, "Signal": sig}, "hist": hist}
    if name == "atr":
        n = int(a[0]) if a else 14
        return {"title": f"ATR {n}", "lines": {f"ATR {n}": atr(df, n)}}
    raise ValueError(f"unknown pane '{spec}' (use rsi:N, macd:F:S:G, atr:N)")
