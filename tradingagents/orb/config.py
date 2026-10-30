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
        orb[key] = None if orb[key] in ("", None) else _parse_time(key, orb[key])
    if "symbols" in bot:
        bot["symbols"] = tuple(s.upper() for s in bot["symbols"])
    return BotSettings(**bot, orb=OrbConfig(**orb))


def load_settings(path: str | Path) -> BotSettings:
    with open(path, "rb") as fh:
        return settings_from_dict(tomllib.load(fh))
