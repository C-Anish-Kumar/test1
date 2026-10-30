"""Run the ORB engine live against Interactive Brokers through IB Gateway or TWS.

    python -m tradingagents.orb --config orb_bot.toml            # paper account
    python -m tradingagents.orb --config orb_bot.toml --dry-run  # log orders, place none
    python -m tradingagents.orb --config orb_bot.toml --live     # real money

Start it before 09:30 each trading day; it exits after the session ends. Each
entry goes in as a bracket: a market buy with a resting stop and (when the
strategy exits at TP3) a resting limit at TP3, so a crash of this script still
leaves the position protected. Creating the kill file named in the settings
cancels this bot's orders, flattens its positions and stops it.

Needs the optional dependency: pip install "tradingagents[orb]".
"""

from __future__ import annotations

import argparse
import bisect
import logging
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import BotSettings, load_settings
from .engine import Action, Bar, OrbEngine, wilder_atr

log = logging.getLogger("orb_bot")


def shares_for(risk_usd: float, max_position_usd: float, entry: float, stop: float) -> int:
    """Whole shares so that a stop-out loses about ``risk_usd``, capped by position value."""
    per_share = entry - stop
    if per_share <= 0 or entry <= 0:
        return 0
    return max(0, min(math.floor(risk_usd / per_share), math.floor(max_position_usd / entry)))


def tick(price: float) -> float:
    return round(price, 2)


@dataclass
class _Bracket:
    qty: int
    stop_order: object
    target_order: object | None


class SymbolRunner:
    """Feeds one symbol's 5-minute bars to an engine and turns its actions into orders."""

    def __init__(self, ib, symbol: str, settings: BotSettings, dry_run: bool) -> None:
        from ib_async import Stock

        self.ib = ib
        self.symbol = symbol
        self.s = settings
        self.dry_run = dry_run
        self.tz = ZoneInfo(settings.timezone)
        self.engine = OrbEngine(settings.orb)
        self.contract = Stock(symbol, "SMART", "USD")
        self.bracket: _Bracket | None = None
        self._atr_days: list[date] = []
        self._atr_vals: list[float] = []
        self._last_fed: datetime | None = None
        self._trading = False

    # ── setup ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.ib.qualifyContracts(self.contract)
        daily = self.ib.reqHistoricalData(
            self.contract, endDateTime="", durationStr="60 D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1,
        )
        atrs = wilder_atr([(b.high, b.low, b.close) for b in daily], self.s.orb.atr_len)
        for b, a in zip(daily, atrs, strict=True):
            if a is not None:
                self._atr_days.append(b.date)
                self._atr_vals.append(a)

        self.bars = self.ib.reqHistoricalData(
            self.contract, endDateTime="", durationStr="5 D",
            barSizeSetting=f"{self.s.bar_minutes} mins", whatToShow="TRADES",
            useRTH=True, formatDate=2, keepUpToDate=True,
        )
        # Warm the indicators and today's opening range on history; the last bar may still be forming.
        for b in self.bars[:-1]:
            self._feed(b)
        if self.engine.trade is not None:
            log.info("%s: history shows a modelled trade already open; not taking it", self.symbol)
            self.engine.drop_open_trade()
        self._trading = True
        self.bars.updateEvent += self._on_update
        log.info("%s: ready (OR %s / %s, trades today %d)", self.symbol,
                 self.engine.or_high, self.engine.or_low, self.engine.trades_today)

    def _daily_atr(self, day: date) -> float | None:
        """ATR as of the last completed day before ``day`` (no look-ahead)."""
        i = bisect.bisect_left(self._atr_days, day)
        return self._atr_vals[i - 1] if i > 0 else None

    # ── bar handling ─────────────────────────────────────────────────────
    def _on_update(self, bars, has_new_bar: bool) -> None:
        if has_new_bar and len(bars) >= 2:
            self._feed(bars[-2])

    def _feed(self, raw) -> None:
        start = raw.date.astimezone(self.tz)
        if self._last_fed is not None and start <= self._last_fed:
            return
        self._last_fed = start
        bar = Bar(start, start + timedelta(minutes=self.s.bar_minutes),
                  raw.open, raw.high, raw.low, raw.close, raw.volume)
        if self._trading:
            self._reconcile()
        for action in self.engine.on_bar(bar, self._daily_atr(start.date())):
            if self._trading:
                self._act(action)

    def position(self) -> float:
        return sum(p.position for p in self.ib.positions() if p.contract.conId == self.contract.conId)

    def _reconcile(self) -> None:
        """Keep the model in step with the account: no modelled trade without shares."""
        if self.engine.trade is not None and not self.dry_run and self.position() <= 0:
            log.warning("%s: model is in a trade but the account holds no shares; resetting",
                        self.symbol)
            self._cancel_children()
            self.engine.drop_open_trade()

    # ── orders ───────────────────────────────────────────────────────────
    def _act(self, a: Action) -> None:
        if a.kind == "skip":
            log.info("%s %s: signal not taken (%s)", self.symbol, f"{a.at:%H:%M}", a.reason)
        elif a.kind == "enter":
            self._enter(a)
        elif a.kind == "move_stop":
            self._move_stop(a.stop)
        elif a.kind == "exit":
            self._exit(a)

    def _enter(self, a: Action) -> None:
        from ib_async import LimitOrder, MarketOrder, StopOrder

        qty = shares_for(self.s.risk_per_trade_usd, self.s.max_position_usd, a.price, a.stop)
        held = 0 if self.dry_run else self.position()
        if qty < 1 or held != 0:
            why = "position already open" if held else "risk too small for one share"
            log.info("%s: long signal at %.2f skipped (%s)", self.symbol, a.price, why)
            self.engine.drop_open_trade()
            return
        log.info("%s %s: BUY %d (%s) ~%.2f stop %.2f target %s tp1 %.2f", self.symbol,
                 f"{a.at:%H:%M}", qty, a.reason, a.price, a.stop,
                 "none" if a.target is None else f"{a.target:.2f}", a.tp1)
        if self.dry_run:
            return
        parent = MarketOrder("BUY", qty, tif="DAY", transmit=False)
        parent.orderId = self.ib.client.getReqId()
        oca = f"orb-{self.symbol}-{parent.orderId}"
        stop = StopOrder("SELL", qty, tick(a.stop), tif="DAY", parentId=parent.orderId,
                         ocaGroup=oca, ocaType=1, transmit=a.target is None)
        target = None
        if a.target is not None:
            target = LimitOrder("SELL", qty, tick(a.target), tif="DAY", parentId=parent.orderId,
                                ocaGroup=oca, ocaType=1, transmit=True)
        for order in (parent, stop, target):
            if order is not None:
                self.ib.placeOrder(self.contract, order)
        self.bracket = _Bracket(qty, stop, target)

    def _move_stop(self, price: float) -> None:
        log.info("%s: move stop to %.2f", self.symbol, price)
        if self.dry_run or self.bracket is None:
            return
        order = self.bracket.stop_order
        order.auxPrice = tick(price)
        order.transmit = True  # it went in untransmitted as part of the bracket
        self.ib.placeOrder(self.contract, order)

    def _cancel_children(self) -> None:
        if self.bracket is None:
            return
        for order in (self.bracket.stop_order, self.bracket.target_order):
            if order is not None:
                self.ib.cancelOrder(order)
        self.bracket = None

    def _exit(self, a: Action) -> None:
        log.info("%s %s: exit (%s) ~%.2f, %+.2fR, day %+.2fR", self.symbol, f"{a.at:%H:%M}",
                 a.reason, a.price, a.r, self.engine.day_r)
        if self.dry_run:
            return
        held = self.position()
        if a.broker_handled and held <= 0:
            self.bracket = None  # the resting stop or target already closed it
            return
        self.flatten(f"exit: {a.reason}")

    def flatten(self, why: str) -> None:
        from ib_async import MarketOrder

        self._cancel_children()
        held = 0 if self.dry_run else self.position()
        if held > 0:
            log.info("%s: SELL %d at market (%s)", self.symbol, held, why)
            self.ib.placeOrder(self.contract, MarketOrder("SELL", held, tif="DAY"))
        self.engine.drop_open_trade()


def _setup_logging(path: str) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(path)):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _check_account(accounts: list[str], live: bool) -> None:
    """Paper accounts start with D (e.g. DU1234567). Refuse a mismatch with --live."""
    paper = all(a.startswith("D") for a in accounts)
    if not live and not paper:
        raise SystemExit(f"Connected to a live account ({', '.join(accounts)}) without --live. "
                         "Use the paper port (4002) or pass --live.")
    if live and paper:
        log.warning("--live given but the account is a paper account; trading paper")


def run(settings: BotSettings, live: bool, dry_run: bool) -> None:
    from ib_async import IB

    _setup_logging(settings.log_file)
    kill = Path(settings.kill_file)
    if kill.exists():
        raise SystemExit(f"Kill file {kill} exists; delete it to start the bot.")

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    session_over = now.replace(hour=settings.orb.session_end.hour,
                               minute=settings.orb.session_end.minute, second=0, microsecond=0)
    session_over += timedelta(minutes=2 * settings.bar_minutes)
    if now.weekday() >= 5 or now >= session_over:
        raise SystemExit("No session left today; start the bot before 09:30 on a trading day.")

    ib = IB()
    ib.connect(settings.host, settings.port, clientId=settings.client_id)
    _check_account(ib.managedAccounts(), live)
    mode = "DRY RUN" if dry_run else ("LIVE" if live else "PAPER")
    log.info("connected to %s:%d as %s (%s)", settings.host, settings.port,
             ",".join(ib.managedAccounts()), mode)

    runners = [SymbolRunner(ib, sym, settings, dry_run) for sym in settings.symbols]
    for r in runners:
        r.start()
    try:
        while datetime.now(tz) < session_over:
            ib.sleep(5)
            if kill.exists():
                log.warning("kill file found: cancelling orders and flattening")
                for r in runners:
                    r.flatten("kill switch")
                ib.sleep(3)
                break
        else:
            log.info("session over")
    except KeyboardInterrupt:
        log.warning("stopped by Ctrl+C; resting stop/target orders stay at IBKR")
    finally:
        for r in runners:
            log.info("%s: trades %d, day %+.2fR", r.symbol, r.engine.trades_today, r.engine.day_r)
        ib.disconnect()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradingagents.orb",
                                     description="Trade ORB Suite long signals on IBKR.")
    parser.add_argument("--config", required=True, help="TOML settings file")
    parser.add_argument("--live", action="store_true", help="allow a live (real money) account")
    parser.add_argument("--dry-run", action="store_true", help="log orders without placing them")
    args = parser.parse_args(argv)
    try:
        import ib_async  # noqa: F401
    except ImportError:
        raise SystemExit('ib_async is not installed: pip install "tradingagents[orb]"') from None
    run(load_settings(args.config), live=args.live, dry_run=args.dry_run)
