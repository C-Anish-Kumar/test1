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

## Live market data (Interactive Brokers)

Opened as the claude.ai artifact, the page uses your **Interactive Brokers** connector (read-only market data, no orders):

- **Today → Pull SPY / QQQ from IBKR** sets the index regime from the 200 EMA on the trend timeframe.
- **Setup check → Pull last 5m bar** fills the last completed regular-session 5m candle for the ticker, plus VWAP, Vol SMA(20), ATR(14) and the 200 EMA, then runs the rule check. **Refresh every minute** keeps it current.
- **Watchlist → Fill & scan from IBKR** fills pre-market volume, daily ATR(14), 200 EMA side, PDH / PDL and PMH / PML for every ticker, and shows whether the last 5m bar is a valid setup. Click a signal to open it in Setup check. **Rescan every 5 min** repeats it.

The page computes the indicators itself from IBKR bars: session VWAP from hlc3 × volume of today's regular-session bars, Vol SMA(20) including the current bar, Wilder ATR(14), and EMA(200) on 1H (three months of RTH bars) or daily (one year) closes. These follow TradingView's definitions, so values should match the chart closely but can differ slightly from TradingView's own feed. Prices are real-time or delayed depending on your IBKR market data subscription. The catalyst column stays manual.

Opened as a local file, there is no connector, so enter values by hand.

## Storage

Data is kept in this browser's `localStorage`. When the page is opened as a claude.ai artifact it saves to your account instead, so it follows you across devices.

## TradingView indicator

`vwap_ema_volume.pine` (Pine Script v5) plots the HTF 200 EMA and session VWAP on a 5-minute chart, shades the prime window and the 15:45 flatten bar, marks valid long / short triggers, draws stop, breakeven and target lines, and exposes alerts. Paste it into the Pine Editor and add it to the chart.
