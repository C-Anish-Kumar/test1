---
name: price-chart
description: "Interactive candlestick chart as one HTML file: zoom and pan, drawing tools (levels, trendlines, measure), VWAP/EMA/SMA/Bollinger overlays, RSI/MACD/ATR panes, optional volume profile and key levels. Use for 'chart X with VWAP and the 200 EMA', 'give me a chart I can draw on', 'show RSI under the price', 'mark these levels on a chart'."
---

# Price chart

Builds a self-contained chart page into the task directory. The file panel renders it with JavaScript, so the user gets a working chart they can zoom, scroll and draw on, keep, and reopen later. Drawings the user makes are remembered by their browser per chart title.

This is not the live Market View chart. Annotating that one (levels and event markers saved per symbol in LangAlpha) is `.agents/skills/chart-annotation/SKILL.md`. Use this skill when the user wants a standalone chart with indicators, panes, a volume profile or backtest trades on it.

## 1. Get the bars (inside `execute_code`)

The script reads a file, so save the price-data response to the task directory first. Intraday intervals need both dates; how far back intraday history goes depends on the data provider.

```python
import json, os
from tools.price_data import get_stock_data

os.makedirs("spy_chart", exist_ok=True)
r = get_stock_data(symbol="SPY", interval="5min", start_date="2026-10-01", end_date="2026-11-26")
if "error" in r:
    raise SystemExit(r)
json.dump(r, open("spy_chart/bars.json", "w"))
print(r["count"], r.get("truncated"), r.get("incomplete"))
```

If `truncated` or `incomplete` is true, tell the user the chart covers less than they asked for.

## 2. Build the chart

```bash
python .agents/skills/price-chart/scripts/build_chart.py spy_chart/bars.json \
  --out spy_chart/chart.html --title "SPY 5m" --overlays vwap,ema:200 --panes rsi:14 --profile
```

- `--overlays` on the price chart: `ema:N`, `sma:N`, `vwap`, `bb:N:K`. Default is `vwap,ema:200` on intraday bars and `ema:50,ema:200` on daily. On intraday bars VWAP resets each session; on daily bars it is anchored to the first bar.
- `--panes` under the chart: `rsi:N`, `macd:F:S:G`, `atr:N`.
- `--profile` adds the volume profile on the right edge with POC, VAH and VAL lines; the user can switch it off on the page. Its numbers come from `.agents/skills/volume-profile/SKILL.md`, which also explains what they mean.
- `--levels FILE` draws dashed lines from a JSON list of `{"price": 712.4, "label": "PDH", "color": "#f5a623"}`, for levels you worked out (prior day high/low, pre-market high/low, support).
- `--last N` keeps the last N bars (default 5000). Indicators are still computed on the full history, so a 200 EMA is warmed up from the first bar shown.

The script prints the file path, the overlays and panes it drew, and how many trade markers it placed.

## 3. Deliver

Name the file and say in one line what is on it. Times are exchange-local, as the bars are. The chart loads its charting library from cdn.jsdelivr.net, so it needs a network connection to display.
