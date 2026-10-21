# VWAP Trend Desk

A one-page working dashboard for the 200 EMA + Session VWAP + Volume day-trading playbook (US equities, 5-minute chart).

Open `index.html` in a browser. No build step and no server.

## Tabs

| Tab | What it does |
| --- | --- |
| **Today** | Live Eastern-time clock and session timeline (pre-market, observe, prime 09:45–11:30, midday lull, afternoon, flatten at 15:45, review). Set SPY / QQQ against their 200 EMA to get the day's bias, mark a tier-1 news release for the ±5 min blackout, and tick off the pre-market, in-market and post-market checklists. |
| **Setup check** | Enter the 5m trigger candle (OHLC, volume, Vol SMA20, ATR14), the session VWAP and the HTF 200 EMA. It names the trigger (Reclaim / Bounce / Breakdown / Rejection), checks every rule (trend, VWAP trigger, ≥ 1.5× volume, time window, news blackout, index alignment, distance from VWAP) and builds the order: entry, 1.5 × ATR stop beyond the swing extreme, 2R target, +1R breakeven level and share size for your $ risk. |
| **Watchlist** | Pre-market screen: pre-market volume > 500k, daily ATR ≥ $2, catalyst noted, plus PDH / PDL / PMH / PML. Names that pass and match the index bias are flagged A-list. |
| **Journal** | Trade log with planned and realized R, slippage, P&L and rule-compliance flags. Stats compare you to the expected profile: win rate 40–46 %, expectancy vs. +0.26R, losing streaks (4–6 is normal), and a cumulative-R curve against the expected line. Copy the log as CSV. |
| **Playbook** | The rules, the expected statistical profile, and an expectancy calculator. |

"Distance from VWAP" flags an entry more than 0.5 ATR away from VWAP as a caution and more than 1 ATR as a chase. That threshold is the dashboard's reading of "don't chase candles far from VWAP"; change it in `evaluate()` if you use a different one.

## Storage

Data is kept in this browser's `localStorage`. When the page is opened as a claude.ai artifact it saves to your account instead, so it follows you across devices.

## TradingView indicator

`vwap_ema_volume.pine` (Pine Script v5) plots the HTF 200 EMA and session VWAP on a 5-minute chart, shades the prime window and the 15:45 flatten bar, marks valid long / short triggers, draws stop, breakeven and target lines, and exposes alerts. Paste it into the Pine Editor and add it to the chart.
