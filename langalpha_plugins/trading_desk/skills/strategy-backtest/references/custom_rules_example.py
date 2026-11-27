"""Example custom strategy for backtest.py --strategy custom --custom FILE.

signals(df, params) receives the bars as a DataFrame (columns open, high, low,
close, volume; time index, oldest first) and returns a Series aligned to df:
+1 = go long, -1 = go short, 0 = nothing, read on that bar's close and filled
at the next bar's open. It may also return (signals, exit) where exit is a
boolean Series whose True bars close any open trade at that bar's close.

Use only data up to each bar (rolling windows, .shift()); a value computed
from later bars leaks the future into the test and makes it look better than
it is.

This one: MACD line crossing its signal line, only with the trend (close above
/ below its 100-bar EMA), exiting when MACD crosses back.
"""

import pandas as pd


def signals(df: pd.DataFrame, params: dict):
    fast, slow, sig_n = int(params.get("fast", 12)), int(params.get("slow", 26)), int(params.get("signal", 9))
    trend_n = int(params.get("trend", 100))
    ema = lambda s, n: s.ewm(span=n, adjust=False, min_periods=n).mean()  # noqa: E731
    macd = ema(df.close, fast) - ema(df.close, slow)
    signal = ema(macd, sig_n)
    trend = ema(df.close, trend_n)

    above = macd > signal
    cross_up = above & ~above.shift(fill_value=False)
    cross_dn = ~above & above.shift(fill_value=False)
    ready = trend.notna() & signal.notna()

    entries = (cross_up & (df.close > trend) & ready).astype(int) \
        - (cross_dn & (df.close < trend) & ready).astype(int)
    exits = (cross_up | cross_dn) & ready
    return entries, exits
