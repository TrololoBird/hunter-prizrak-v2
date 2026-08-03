"""Сетка баров и признак закрытости. FOUNDATION.md §6, §2.8.

ЧИСТЫЙ МОДУЛЬ (§10.3): время входит аргументом, часы/сеть/глобальное состояние не
трогаются. Проверяется гейтом gates/purity.py.
"""

from __future__ import annotations

from itertools import pairwise

from .models import Bar

# FOUNDATION.md §2.8: «Основные ТФ: 5м, 15м, 1ч, 4ч, 1Д, 1Н».
TIMEFRAME_MS: dict[str, int] = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}

# Сдвиг сетки от эпохи UTC. Замер 2026-08-03 на BTC/USDT:USDT: у недельных баров
# open_ms % 604800000 == 345600000 (=4 суток) — бар открывается в понедельник 00:00 UTC,
# а эпоха приходится на четверг. У остальных ТФ сдвига нет.
# Протокол: docs/audit/exchange-facts-2026-08-03.md
GRID_ANCHOR_MS: dict[str, int] = {"1w": 4 * 24 * 60 * 60_000}


def tf_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MS[timeframe]
    except KeyError:
        raise ValueError(
            f"неизвестный таймфрейм {timeframe!r}; §2.8 задаёт {sorted(TIMEFRAME_MS)}"
        ) from None


def grid_anchor_ms(timeframe: str) -> int:
    return GRID_ANCHOR_MS.get(timeframe, 0)


def on_grid(open_ms: int, timeframe: str) -> bool:
    return (open_ms - grid_anchor_ms(timeframe)) % tf_ms(timeframe) == 0


def is_closed(open_ms: int, timeframe: str, now_ms: int) -> bool:
    """Бар закрыт, когда биржевое сейчас дошло до его правой границы.

    Проверка по времени, а не «отбросить последний элемент кэша»: последний элемент
    бывает и закрытым — тогда отбрасывание подало бы позапрошлый бар.
    """
    return now_ms >= open_ms + tf_ms(timeframe)


def closed_only(bars: list[Bar], timeframe: str, now_ms: int) -> list[Bar]:
    return [b for b in bars if is_closed(b.open_ms, timeframe, now_ms)]


def expected_last_closed_open_ms(timeframe: str, now_ms: int) -> int:
    """Левая граница новейшего бара, который к этому моменту обязан быть закрыт."""
    step = tf_ms(timeframe)
    anchor = grid_anchor_ms(timeframe)
    return ((now_ms - anchor) // step) * step + anchor - step


def find_gaps(bars: list[Bar], timeframe: str) -> list[tuple[int, int]]:
    step = tf_ms(timeframe)
    out: list[tuple[int, int]] = []
    for prev, cur in pairwise(bars):
        if cur.open_ms - prev.open_ms != step:
            out.append((prev.open_ms, cur.open_ms))
    return out
