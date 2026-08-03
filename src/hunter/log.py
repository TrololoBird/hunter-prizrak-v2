"""Вывод. Без зависимостей и без stdlib logging.

FOUNDATION.md §4.3 требует, чтобы пропуск был виден. Поэтому у вывода есть уровень
`degraded` — им отмечается всякий путь, который продолжил работу в урезанном виде.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .clock import local_ms

_stream: TextIO = sys.stderr
_degraded_count = 0


def _emit(level: str, msg: str) -> None:
    ms = local_ms()
    print(f"{ms // 1000 % 86400:05d}.{ms % 1000:03d} {level:8} {msg}", file=_stream, flush=True)


def info(msg: str) -> None:
    _emit("info", msg)


def warn(msg: str) -> None:
    _emit("warn", msg)


def error(msg: str) -> None:
    _emit("ERROR", msg)


def degraded(msg: str) -> None:
    """Работа продолжена в урезанном виде. Молчать об этом запрещено — §4.3."""
    global _degraded_count
    _degraded_count += 1
    _emit("DEGRADED", msg)


def degraded_count() -> int:
    return _degraded_count
