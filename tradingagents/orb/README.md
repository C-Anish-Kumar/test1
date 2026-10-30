# ORB bot (IBKR)

Trades the **long** signals of the ORB Suite TradingView indicator
(`dashboard/ORB_Suite_cleanroom.pine`) on an Interactive Brokers account. The
bot does not read TradingView. It runs the same rules itself on IBKR's 5-minute
bars, so it works with a free TradingView plan and keeps trading when the chart
is closed.

> **Paper first.** The bot connects to your paper (simulated money) account
> unless you pass `--live`, and it refuses to start on a live account without
> it. Run it on paper for at least two weeks and compare its trades with the
> chart before you use real money. Trading carries real risk, and a bot can fail.

## What it does

Each trading day, for each symbol in your settings:

1. Builds the opening range from the first `or_minutes` (15 by default) and locks it.
2. When a 5-minute bar **closes** above the range high (or retests it, in
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
