---
name: strategy-backtest
description: "Backtest a trading strategy on historical bars with stops, targets, position sizing, fees and slippage: win rate, expectancy in R, profit factor, drawdown, Sharpe, equity curve and a chart with every trade marked. Built in: EMA cross, VWAP + 200 EMA reclaim, opening-range breakout, RSI reversion, Donchian breakout, or custom rules in Python. Use for 'backtest this strategy', 'would this setup have worked on X', 'test my rules on SPY 5m', 'win rate of an ORB on QQQ'."
---

# Strategy backtest

Simulates a strategy bar by bar on the bars in a file and writes the results to a folder: `stats.json`, `trades.csv`, `trades.json`, `equity.json`, and with `--chart` an interactive `chart.html` with entries, exits (in R) and the equity curve.

## How it trades

These are fixed so results are honest; tell the user them when they matter:

- A signal is read on a bar's close and filled at the **next bar's open**, never on the bar that produced it.
- The stop is `--stop-atr` × ATR(14) from the fill (default 1.5), and the target is `--target-r` times that distance (default 2R). When one bar touches both, the **stop is assumed hit first**. A gap through either fills at the open.
- Each trade risks `--risk-pct` of equity (default 1%), capped at the equity on hand: there is no leverage, so on tight intraday stops a trade often risks less than the setting. With `--stop-atr 0` each trade uses all equity and R is measured against it.
- `--fee-bps` and `--slippage-bps` (defaults 1 and 2) are charged on entry and exit.
- On intraday bars every trade is closed at the session's last bar unless `--no-flatten`. An opposite signal always closes a trade; with `--side both` the next open enters the other way.

## Run it

Save bars to the task directory as in `.agents/skills/price-chart/SKILL.md` step 1, then:

```bash
python .agents/skills/strategy-backtest/scripts/backtest.py spy_bt/bars.json \
  --strategy vwap_ema --side both --stop-atr 1.5 --target-r 2 --chart --out spy_bt/vwap_ema
```

| `--strategy` | Signal | `--params` (JSON) |
|---|---|---|
| `ema_cross` | fast EMA crosses the slow EMA | `fast` 20, `slow` 50 |
| `vwap_ema` | close crosses VWAP in the direction of the 200 EMA, on volume ≥ `vol_mult` × its 20-bar average | `ema` 200, `vol_mult` 1.5 |
| `orb` | close breaks the opening range; one trade a session; intraday bars only | `minutes` 15 |
| `rsi_reversion` | RSI crosses back up through `lo` (long) or down through `hi` (short); exits at the midpoint | `n` 14, `lo` 30, `hi` 70 |
| `donchian` | close beyond the prior `n`-bar high or low | `n` 20 |
| `custom` | your rules, `--custom FILE` | anything your file reads |

For rules none of these fit, write a file defining `signals(df, params)` and pass `--strategy custom --custom FILE`. `references/custom_rules_example.py` is a working template that shows the return format and the one rule that matters: compute every value only from bars up to the current one.

## Report it

- Lead with the number of trades, win rate, expectancy (R per trade), profit factor, max drawdown and the return **next to buy-and-hold** over the same bars.
- Under about 30 trades the stats say little; the script adds a `warning` then, and you pass it on.
- A strategy tuned on these bars will look better here than it will going forward. When the user tries several parameter sets, keep the last part of the history aside and test the chosen set on it before calling it good.
- Past results are not a forecast. Do not present a backtest as a reason to place a trade.
