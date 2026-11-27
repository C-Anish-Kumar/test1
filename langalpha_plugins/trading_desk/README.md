# Trading Desk plugin for LangAlpha

Chart-trader tools for LangAlpha's agent, written for this repo. They cover the same kind of ground as a charting platform such as LuxAlgo's Vela, without copying its code, indicators or design.

| Skill | Ask the agent | It makes |
|---|---|---|
| `price-chart` | "Chart SPY 5m with VWAP, the 200 EMA and RSI, with the volume profile" | One HTML chart file: zoom and pan, levels, trendlines and a measure tool, overlays (VWAP, EMA, SMA, Bollinger), panes (RSI, MACD, ATR), volume profile, key levels |
| `strategy-backtest` | "Backtest the VWAP + 200 EMA reclaim on SPY 5m for the last two months" | `stats.json`, `trades.csv`, an equity curve, and a chart with every trade marked |
| `volume-profile` | "Where is the volume in NVDA this month? Give me POC and value area" | POC, VAH/VAL, high and low volume nodes, per-session profiles, an estimated delta |

Built-in backtest strategies: `ema_cross`, `vwap_ema` (the VWAP Trend Desk playbook), `orb` (opening-range breakout), `rsi_reversion`, `donchian`, and `custom` rules in Python (`skills/strategy-backtest/references/custom_rules_example.py`).

## Install

`scripts/langalpha_setup.sh` installs it: once LangAlpha is up, it uploads every plugin under `langalpha_plugins/` through LangAlpha's Plugins API, and re-uploads one only when its files changed. To install by hand instead, zip this folder's contents and upload the zip on LangAlpha's Plugins page.

## How it works

The skills run as Python scripts in the agent's sandbox (pandas and numpy, already in the sandbox image). The agent fetches bars with LangAlpha's price-data tools, saves them, and runs a script. Charts are self-contained HTML files rendered in LangAlpha's file panel with [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0), loaded from cdn.jsdelivr.net.

## Limits

- The volume profile and delta are estimates from bars: each bar's volume is spread over its range and split by where it closed. True footprint/orderflow needs trade-by-trade data.
- Backtests fill at the next bar's open, assume the stop when a bar touches both stop and target, use no leverage, and charge 1 bp fees and 2 bp slippage by default. Past results are not a forecast.
- Self-hosted LangAlpha's market data is delayed (Yahoo Finance or FMP), and intraday history is limited by the provider.

## Test it without LangAlpha

```bash
python skills/price-chart/scripts/build_chart.py bars.json --out chart.html --profile --panes rsi:14
```

```bash
python skills/strategy-backtest/scripts/backtest.py bars.json --strategy orb --chart
```

`bars.json` is a list of `{date, open, high, low, close, volume}` rows, oldest first, or `{"data": [...]}` as LangAlpha's `get_stock_data` returns. A CSV with those columns works too.
