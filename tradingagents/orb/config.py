"""Settings for the ORB bot, read from a TOML file (see orb_bot.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path

from .engine import OrbConfig

_TIME_FIELDS = {"session_start", "session_end", "time_exit"}


@dataclass(frozen=True)
class BotSettings:
    symbols: tuple[str, ...] = ("NVDA",)
    risk_per_trade_usd: float = 100.0
    max_position_usd: float = 20_000.0
    host: str = "127.0.0.1"
    port: int = 4002  # IB Gateway paper; 4001 is IB Gateway live
    client_id: int = 17
    bar_minutes: int = 5
    timezone: str = "America/New_York"
    kill_file: str = "STOP_ORB"
    log_file: str = "orb_bot.log"
    orb: OrbConfig = field(default_factory=OrbConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("list at least one symbol")
        if self.risk_per_trade_usd <= 0 or self.max_position_usd <= 0:
            raise ValueError("risk_per_trade_usd and max_position_usd must be positive")
        if self.bar_minutes <= 0 or self.orb.or_minutes % self.bar_minutes:
            raise ValueError("bar_minutes must divide the OR length (or_minutes)")


def _parse_time(name: str, value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be HH:MM, got {value!r}") from exc


def _check_keys(section: str, given: dict, allowed: set[str]) -> None:
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise ValueError(f"unknown [{section}] settings: {', '.join(unknown)}")


def settings_from_dict(data: dict) -> BotSettings:
    bot = dict(data.get("bot", {}))
    orb = dict(data.get("orb", {}))
    _check_keys("bot", bot, {f.name for f in fields(BotSettings)} - {"orb"})
    _check_keys("orb", orb, {f.name for f in fields(OrbConfig)})
    for key in _TIME_FIELDS & orb.keys():
        value = orb[key]
        if not isinstance(value, time):  # TOML may already have parsed "11:30:00"
            orb[key] = None if value in ("", None) else _parse_time(key, value)
    if "symbols" in bot:
        bot["symbols"] = tuple(s.upper() for s in bot["symbols"])
    return BotSettings(**bot, orb=OrbConfig(**orb))


def apply_overrides(data: dict, overrides: list[str]) -> dict:
    """Apply ``key=value`` overrides (e.g. ``stop_mode=mid``) to the [bot] / [orb] tables.

    Values are read as TOML (``true``, ``1.5``, ``["SPY", "QQQ"]``); anything
    that is not valid TOML is taken as a plain string, so ``stop_mode=mid`` and
    ``time_exit=11:30`` work without quotes.
    """
    bot_keys = {f.name for f in fields(BotSettings)} - {"orb"}
    orb_keys = {f.name for f in fields(OrbConfig)}
    out = {"bot": dict(data.get("bot", {})), "orb": dict(data.get("orb", {}))}
    for item in overrides:
        key, sep, raw = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--set needs key=value, got {item!r}")
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw.strip()
        if key in orb_keys:
            out["orb"][key] = value
        elif key in bot_keys:
            out["bot"][key] = value
        else:
            raise ValueError(f"unknown setting {key!r}")
    return out


def load_settings(path: str | Path, overrides: list[str] | None = None) -> BotSettings:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return settings_from_dict(apply_overrides(data, overrides) if overrides else data)
