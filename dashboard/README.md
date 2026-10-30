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

### Alerts

Tick **Auto-scan & alert every 5 min** on the Watchlist tab. The page scans 20 seconds after every 5m bar closes. When a ticker's just-closed bar passes every rule, it raises an alert once for that bar: a banner at the top of every tab with entry, stop, target and share size, an optional two-tone chime (**Sound**), and a line in **Today's alerts**. **Refresh every minute** on Setup check alerts the same way for its ticker. Bars older than 10 minutes never alert, the time-window and news-blackout rules keep alerts out of the first 15 minutes, the news blackout and the time after 15:45, and midday setups alert with a caution badge.

Alerts run inside the page, so keep it open (a background tab is fine). Browser notifications are not available in claude.ai artifacts. For phone or email alerts, use the TradingView indicator's alert conditions.

The page computes the indicators itself from IBKR bars: session VWAP from hlc3 × volume of today's regular-session bars, Vol SMA(20) including the current bar, Wilder ATR(14), and EMA(200) on 1H (three months of RTH bars) or daily (one year) closes. These follow TradingView's definitions, so values should match the chart closely but can differ slightly from TradingView's own feed. Prices are real-time or delayed depending on your IBKR market data subscription. The catalyst column stays manual.

Opened as a local file, there is no connector, so enter values by hand.

## Storage

Data is kept in this browser's `localStorage`. When the page is opened as a claude.ai artifact it saves to your account instead, so it follows you across devices.

## TradingView indicator

`vwap_ema_volume.pine` (Pine Script v5) plots the HTF 200 EMA and session VWAP on a 5-minute chart, shades the prime window and the 15:45 flatten bar, marks valid long / short triggers, draws stop, breakeven and target lines, and exposes alerts. Paste it into the Pine Editor and add it to the chart.

## ORB Suite indicator

`ORB_Suite_cleanroom.pine` (Pine Script v6) is an opening-range breakout indicator written from a public feature description, not from anyone's source code. It builds the OR for a custom session and timezone (5, 15, 30 or 60 minutes), shows it as BUILDING and then LOCKED, and only signals after the lock. Entries are a close break or a break + retest (◆ marks a break waiting for its retest), with an optional tick buffer, a long/short-only switch and re-arming after a close back inside the OR. Filters: VWAP side, EMA side, RVOL and OR width against the prior day's ATR. The stop is the opposite OR side, the OR midpoint, an ATR multiple or a % of the OR. TP1–3 are R multiples or OR extensions, and setups under the minimum R:R to TP2 show as a grey mark and are not traded. Exits: breakeven after TP1, TP3, an opposite OR break, a time exit (which also blocks new entries) and the session end. The stop is checked before the targets, from the bar after entry. Optional stop-and-reverse opens the other side at the stop price with risk capped at the original 1R. It counts toward max trades and never reverses again. Daily limits are max trades and a loss cap in R. Candles are colored by a seven-tier weighted conviction score (EMA, VWAP, RSI, MACD, RVOL, OR position, momentum) or by OR side. A dashboard and 11 alert conditions are included.

Use a chart timeframe at or below the OR length. The dashboard flags it when it isn't. Set alerts to "Once per bar close". The script hasn't been compiled in TradingView yet, so fix anything the Pine Editor flags.
