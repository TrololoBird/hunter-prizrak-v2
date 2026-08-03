"""Чтение закреплённой вселенной. FOUNDATION.md §5."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .bars import TIMEFRAME_MS

DEFAULT_PATH = Path("config/universe.toml")


@dataclass(frozen=True, slots=True)
class Universe:
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    source: Path


def load_universe(path: Path = DEFAULT_PATH) -> Universe:
    if not path.exists():
        raise FileNotFoundError(f"нет файла вселенной {path} — §5 требует закреплённый набор")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("universe")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: нет секции [universe]")

    symbols = section.get("symbols")
    if not symbols:
        raise ValueError(f"{path}: пустой список symbols")
    timeframes = section.get("timeframes")
    if not timeframes:
        raise ValueError(f"{path}: пустой список timeframes")

    unknown = [t for t in timeframes if t not in TIMEFRAME_MS]
    if unknown:
        raise ValueError(f"{path}: таймфреймы {unknown} вне §2.8 ({sorted(TIMEFRAME_MS)})")

    dupes = {s for s in symbols if symbols.count(s) > 1}
    if dupes:
        raise ValueError(f"{path}: символы повторяются: {sorted(dupes)}")

    return Universe(tuple(symbols), tuple(timeframes), path)
