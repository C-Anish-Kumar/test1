"""Opening-range breakout engine: the long side of dashboard/ORB_Suite_cleanroom.pine.

Feed it completed bars in order with ``on_bar``; it returns the actions a broker
layer must carry out (enter, move the stop, exit). It never talks to a broker,
so it runs the same on recorded bars as on live ones.

The rules follow the Pine indicator's defaults and order of evaluation: the
stop is checked before the targets, management starts on the bar after entry,
and a side re-arms only after a close back inside the opening range.
Stop-and-reverse is left out because it would open a short.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta

ENTRY_MODES = ("close_break", "break_retest")
STOP_MODES = ("opposite", "mid", "atr", "pct")
TARGET_MODES = ("r", "ext")


@dataclass(frozen=True)
class Bar:
    """One completed bar. ``start`` and ``end`` are aware datetimes in the session timezone."""

    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrbConfig:
    session_start: time = time(9, 30)
    session_end: time = time(16, 0)
    or_minutes: int = 15
    entry_mode: str = "close_break"
    buffer: float = 0.0
    use_vwap: bool = True
    use_ema: bool = False
    ema_len: int = 50
    use_rvol: bool = False
    rvol_min: float = 1.5
    rvol_len: int = 20
    use_width: bool = False
    width_min: float = 0.10
    width_max: float = 0.60
    stop_mode: str = "opposite"
    stop_atr_mult: float = 1.0
    atr_len: int = 14
    stop_pct: float = 50.0
    target_mode: str = "r"
    tp1: float = 1.0
    tp2: float = 2.0
    tp3: float = 3.0
    min_rr: float = 1.5
    be_after_tp1: bool = True
    exit_tp3: bool = True
    exit_opposite: bool = True
    time_exit: time | None = None
    eod_close: bool = True
    max_trades: int = 2
    loss_cap_r: float | None = 2.0

    def __post_init__(self) -> None:
        for name, value, allowed in (
            ("entry_mode", self.entry_mode, ENTRY_MODES),
            ("stop_mode", self.stop_mode, STOP_MODES),
            ("target_mode", self.target_mode, TARGET_MODES),
        ):
            if value not in allowed:
                raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
        if self.or_minutes <= 0 or self.max_trades <= 0:
            raise ValueError("or_minutes and max_trades must be positive")


@dataclass
class Trade:
    entry: float
    stop: float
    risk: float
    tp1: float
    tp2: float
    tp3: float
    seq: int
    tp1_hit: bool = False
    tp2_hit: bool = False


@dataclass(frozen=True)
class Action:
    """What the broker layer must do after a bar.

    kind is "enter", "move_stop", "exit" or "skip" (a signal that was not traded).
    For "exit", ``broker_handled`` is True when a resting bracket order (stop or
    target) already closed the position at ``price``; otherwise the broker layer
    must close it at market. ``r`` is the model result in R at ``price``.
    """

    kind: str
    at: datetime
    reason: str = ""
    price: float | None = None
    stop: float | None = None
    target: float | None = None
    tp1: float | None = None
    broker_handled: bool = False
    r: float | None = None


class _Ema:
    """Pine's ta.ema: seeded with the first value."""

    def __init__(self, length: int) -> None:
        self.alpha = 2 / (length + 1)
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value


class _Rma:
    """Pine's ta.rma (Wilder smoothing): seeded with the simple average of the first values."""

    def __init__(self, length: int) -> None:
        self.length = length
        self.seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.seed.append(x)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
        else:
            self.value = (self.value * (self.length - 1) + x) / self.length
        return self.value


def wilder_atr(bars: list[tuple[float, float, float]], length: int) -> list[float | None]:
    """ATR after each (high, low, close) bar, as Pine's ta.atr computes it."""
    rma = _Rma(length)
    out: list[float | None] = []
    prev_close: float | None = None
    for high, low, close in bars:
        tr = high - low if prev_close is None else max(
            high - low, abs(high - prev_close), abs(low - prev_close)
        )
        out.append(rma.update(tr))
        prev_close = close
    return out


def _minute_of_day(dt: datetime, day_start: datetime) -> float:
    return (dt - day_start).total_seconds() / 60


class OrbEngine:
    """Long-only ORB rules. One engine per symbol."""

    def __init__(self, config: OrbConfig | None = None) -> None:
        self.cfg = config or OrbConfig()
        self._ema = _Ema(self.cfg.ema_len)
        self._atr = _Rma(self.cfg.atr_len)
        self._vol = deque(maxlen=self.cfg.rvol_len)
        self._prev_close: float | None = None
        self._prev_in_sess = False
        self._prev_day = None
        self._seq = 0
        self._pv = 0.0
        self._v = 0.0
        self.or_high: float | None = None
        self.or_low: float | None = None
        self.or_state = "none"  # none | building | locked
        self._or_end: datetime | None = None
        self.long_armed = True
        self.retest_pending = False
        self.trades_today = 0
        self.day_r = 0.0
        self.trade: Trade | None = None

    def drop_open_trade(self) -> None:
        """Forget the modelled position, e.g. when the broker holds none (warm-up, rejected order)."""
        self.trade = None

    def _plan(self, entry: float, atr: float | None) -> tuple[float, float, float, float, float] | None:
        c = self.cfg
        hi, lo = self.or_high, self.or_low
        rng = hi - lo
        if c.stop_mode == "opposite":
            stop = lo
        elif c.stop_mode == "mid":
            stop = (hi + lo) / 2
        elif c.stop_mode == "atr":
            if atr is None:
                return None
            stop = entry - c.stop_atr_mult * atr
        else:
            stop = entry - c.stop_pct / 100 * rng
        risk = entry - stop
        if c.target_mode == "r":
            t1, t2, t3 = (entry + k * risk for k in (c.tp1, c.tp2, c.tp3))
        else:
            t1, t2, t3 = (hi + k * rng for k in (c.tp1, c.tp2, c.tp3))
        return stop, risk, t1, t2, t3

    def on_bar(self, bar: Bar, daily_atr: float | None = None) -> list[Action]:
        """Process one completed bar. ``daily_atr`` is the prior day's ATR (for the width filter)."""
        c = self.cfg
        acts: list[Action] = []
        self._seq += 1
        at = bar.end

        day = bar.start.date()
        day_start = bar.start.replace(hour=0, minute=0, second=0, microsecond=0)
        in_sess = c.session_start <= bar.start.time() < c.session_end
        new_sess = in_sess and (not self._prev_in_sess or day != self._prev_day)
        self._prev_in_sess, self._prev_day = in_sess, day

        # Indicators include the current bar, as in Pine.
        if new_sess:
            self._pv = self._v = 0.0
        self._pv += (bar.high + bar.low + bar.close) / 3 * bar.volume
        self._v += bar.volume
        vwap = self._pv / self._v if self._v > 0 else bar.close
        ema = self._ema.update(bar.close)
        tr = bar.high - bar.low if self._prev_close is None else max(
            bar.high - bar.low, abs(bar.high - self._prev_close), abs(bar.low - self._prev_close)
        )
        atr = self._atr.update(tr)
        self._prev_close = bar.close
        self._vol.append(bar.volume)
        vol_sma = sum(self._vol) / len(self._vol) if len(self._vol) == self._vol.maxlen else None
        rvol = bar.volume / vol_sma if vol_sma else None

        # A position left over from the previous session closes at this open.
        if new_sess and self.trade is not None:
            acts.append(self._close(at, bar.open, "Carry-over", broker_handled=False))

        if new_sess:
            self.or_high, self.or_low = bar.high, bar.low
            self.or_state = "building"
            self._or_end = bar.start + timedelta(minutes=c.or_minutes)
            self.long_armed = True
            self.retest_pending = False
            self.trades_today = 0
            self.day_r = 0.0
        elif in_sess and self.or_state == "building":
            self.or_high = max(self.or_high, bar.high)
            self.or_low = min(self.or_low, bar.low)

        lock_bar = False
        if in_sess and self.or_state == "building" and bar.end >= self._or_end:
            self.or_state = "locked"
            lock_bar = True

        active = in_sess and self.or_state == "locked" and not lock_bar
        end_min = _minute_of_day(bar.end, day_start)
        eod_bar = in_sess and end_min >= c.session_end.hour * 60 + c.session_end.minute
        past_cutoff = in_sess and c.time_exit is not None and (
            end_min >= c.time_exit.hour * 60 + c.time_exit.minute
        )

        # Manage the open trade, from the bar after entry; the stop comes first.
        t = self.trade
        if active and t is not None and self._seq > t.seq:
            exit_px, reason, handled = None, "", False
            if bar.low <= t.stop:
                exit_px, handled = t.stop, True
                reason = "Breakeven" if t.tp1_hit and c.be_after_tp1 else "Stop"
            else:
                if not t.tp1_hit and bar.high >= t.tp1:
                    t.tp1_hit = True
                    if c.be_after_tp1:
                        t.stop = t.entry
                        acts.append(Action("move_stop", at, "TP1 hit: stop to breakeven", stop=t.stop))
                if not t.tp2_hit and bar.high >= t.tp2:
                    t.tp2_hit = True
                if bar.high >= t.tp3 and c.exit_tp3:
                    exit_px, reason, handled = t.tp3, "TP3", True
                if exit_px is None and c.exit_opposite and bar.close < self.or_low - c.buffer:
                    exit_px, reason = bar.close, "Opposite break"
                if exit_px is None and past_cutoff:
                    exit_px, reason = bar.close, "Time"
                if exit_px is None and c.eod_close and eod_bar:
                    exit_px, reason = bar.close, "Session end"
            if exit_px is not None:
                acts.append(self._close(at, exit_px, reason, broker_handled=handled))

        loss_hit = c.loss_cap_r is not None and self.day_r <= -c.loss_cap_r

        if active and self.trade is None:
            hi, lo = self.or_high, self.or_low
            if lo <= bar.close <= hi:
                self.long_armed = True
                self.retest_pending = False
            trigger = False
            if self.long_armed and bar.close > hi + c.buffer:
                self.long_armed = False
                if c.entry_mode == "close_break":
                    trigger = True
                else:
                    self.retest_pending = True
            elif self.retest_pending and bar.low <= hi + c.buffer and bar.close > hi:
                self.retest_pending = False
                trigger = True

            can_enter = (
                self.trades_today < c.max_trades and not loss_hit and not past_cutoff and not eod_bar
            )
            if trigger and can_enter:
                acts.append(self._try_enter(bar, at, vwap, ema, rvol, atr, daily_atr))
        return acts

    def _try_enter(self, bar, at, vwap, ema, rvol, atr, daily_atr) -> Action:
        c = self.cfg
        width = (self.or_high - self.or_low) / daily_atr if daily_atr else None
        failed = [
            name
            for name, ok in (
                ("VWAP", not c.use_vwap or bar.close > vwap),
                ("EMA", not c.use_ema or bar.close > ema),
                ("RVOL", not c.use_rvol or (rvol is not None and rvol >= c.rvol_min)),
                ("OR width", not c.use_width or (width is not None and c.width_min <= width <= c.width_max)),
            )
            if not ok
        ]
        if failed:
            return Action("skip", at, "filters: " + ", ".join(failed), price=bar.close)
        plan = self._plan(bar.close, atr)
        if plan is None:
            return Action("skip", at, "ATR not ready", price=bar.close)
        stop, risk, t1, t2, t3 = plan
        if risk <= 0 or (t2 - bar.close) / risk < c.min_rr:
            return Action("skip", at, "below min R:R to TP2", price=bar.close, stop=stop)
        self.trade = Trade(bar.close, stop, risk, t1, t2, t3, self._seq)
        self.trades_today += 1
        return Action(
            "enter", at, "close break" if c.entry_mode == "close_break" else "break + retest",
            price=bar.close, stop=stop, target=t3 if c.exit_tp3 else None, tp1=t1,
        )

    def _close(self, at: datetime, price: float, reason: str, broker_handled: bool) -> Action:
        t = self.trade
        r = (price - t.entry) / t.risk
        self.day_r += r
        self.trade = None
        return Action("exit", at, reason, price=price, broker_handled=broker_handled, r=r)
