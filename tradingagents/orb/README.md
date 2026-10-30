# ORB bot (IBKR)

Trades the **long** signals of the ORB Suite TradingView indicator
(`dashboard/ORB_Suite_cleanroom.pine`) on an Interactive Brokers account. The
bot does not read TradingView. It runs the same rules itself on IBKR's bars
(15-minute in the example settings; set `bar_minutes` to the timeframe of the
chart you compare it with, since signals fire on bar closes), so it works with
a free TradingView plan and keeps trading when the chart is closed.

> **Paper first.** The bot connects to your paper (simulated money) account
> unless you pass `--live`, and it refuses to start on a live account without
> it. Run it on paper for at least two weeks and compare its trades with the
> chart before you use real money. Trading carries real risk, and a bot can fail.

## What it does

Each trading day, for each symbol in your settings:

1. Builds the opening range from the first `or_minutes` (15 by default) and locks it.
2. When a bar **closes** above the range high (or retests it, in
   `break_retest` mode) and the filters pass, it buys with a **bracket order**.
   A bracket is three orders sent together: a market buy, a resting **stop**
   (a sell that triggers if price falls to the stop) and a resting **limit**
   sell at TP3. The stop and target sit at IBKR, so the position stays
   protected even if your computer or the bot stops.
3. Sizes the trade so that hitting the stop loses about `risk_per_trade_usd`:
   shares = risk ÷ (entry − stop), capped by `max_position_usd`.
4. After TP1 it moves the stop to the entry price (breakeven).
5. Sells at market on a close back below the range low, at the time exit if you
   set one, and on the last bar of the session.
6. Takes at most `max_trades` per day and stops for the day once losses reach
   `loss_cap_r` R. (R is one unit of the risk you set: a stop-out is −1R.)

Shorts and stop-and-reverse are not traded.

## Setup

1. **IB Gateway.** Install IB Gateway from the IBKR website and log in to your
   **paper** account. In *Configure → Settings → API → Settings*:
   - tick *Enable ActiveX and Socket Clients*
   - untick *Read-Only API*
   - note the port: **4002** for paper (4001 is live)
   - add `127.0.0.1` to *Trusted IPs*

   TWS works too, on ports 7497 (paper) and 7496 (live).
2. **Install** the repository with the bot's extra:
   ```bash
   pip install -e ".[orb]"
   ```
3. **Settings.** Copy the example and edit the symbols and risk amounts:
   ```bash
   cp orb_bot.example.toml orb_bot.toml
   ```
   The `[orb]` section uses the indicator's input names and defaults. Change
   both together if you want the chart and the bot to agree.

## Backtest first

Before paper trading, replay months of IBKR history through the bot's exact
rules and settings:

```bash
python -m tradingagents.orb.backtest --config orb_bot.toml --months 6
```

It needs IB Gateway or TWS running, but only reads data. It connects
read-only with its own client id, so it can run next to the bot. It prints
trades, win rate, average R, median R and profit factor. It also shows each
half of the period and breaks results down by exit reason, entry hour and
symbol, then writes every trade to `orb_backtest_trades.csv`. Results are
after `--cost-per-share` (default $0.02 round trip, for commission and
slippage) and use the bot's sizing.

Entries are modelled at the signal bar's close and stops and targets at their
prices, so real fills will be somewhat worse. The report ends with a verdict:
worth paper trading only if average R is at least +0.10R in **both** halves.
A strategy that only worked in one half is likely fitted to that period.

## Run

Start it before 09:30 ET on a trading day. It exits on its own after the close.

```bash
python -m tradingagents.orb --config orb_bot.toml --dry-run
```
Logs the orders it would place without placing any. A good first run.

```bash
python -m tradingagents.orb --config orb_bot.toml
```
Trades your paper account.

```bash
python -m tradingagents.orb --config orb_bot.toml --live
```
Real money. Only after paper trading has looked right.

Everything is logged to the console and to `orb_bot.log`.

**Stopping**
- **Kill switch:** create a file named `STOP_ORB` in the folder you started the
  bot from. Within a few seconds it cancels its orders, sells its positions at
  market and exits. Delete the file before the next start.
  ```bash
  touch STOP_ORB
  ```
- **Ctrl+C** stops the bot but leaves the resting stop and target orders at
  IBKR, so an open position stays protected until they fill or expire at the
  close.

To run it every day, schedule the command for about 09:20 ET on weekdays with
cron (macOS/Linux) or Task Scheduler (Windows). IB Gateway must be running and
logged in. It logs out daily, so enable its auto-restart setting.

## Differences from the chart

- Entries are market orders placed when a bar closes, so the fill is near, but
  not exactly at, the close the indicator shows.
- The stop and TP3 fill at IBKR as soon as price trades there, inside the bar.
  The indicator only checks them when the bar closes.
- IBKR's bars and the daily ATR can differ slightly from TradingView's feed.
- If the bot starts after the open, it rebuilds the day's range from history
  but does not join a trade the chart already took.
- The bot only manages positions it opened. If you already hold shares in a
  symbol, it skips that symbol's signals.
