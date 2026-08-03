"""Бары и признак закрытости. FOUNDATION.md §6, §2.8."""

from __future__ import annotations

from dataclasses import dataclass

# FOUNDATION.md §2.8: «Основные ТФ: 5м, 15м, 1ч, 4ч, 1Д, 1Н».
TIMEFRAME_MS: dict[str, int] = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


@dataclass(frozen=True, slots=True)
class Bar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def tf_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MS[timeframe]
    except KeyError:
        raise ValueError(f"неизвестный таймфрейм {timeframe!r}; §2.8 задаёт "
                         f"{sorted(TIMEFRAME_MS)}") from None


def is_closed(open_ms: int, timeframe: str, now_ms: int) -> bool:
    """Бар закрыт, когда биржевое сейчас дошло до его правой границы.

    Проверка по времени, а не «отбросить последний элемент кэша»: последний элемент
    бывает и закрытым — тогда отбрасывание подало бы позапрошлый бар.
    """
    return now_ms >= open_ms + tf_ms(timeframe)


def closed_only(bars: list[Bar], timeframe: str, now_ms: int) -> list[Bar]:
    return [b for b in bars if is_closed(b.open_ms, timeframe, now_ms)]


# Сдвиг сетки от эпохи UTC. Замер 2026-08-03 на BTC/USDT:USDT: у недельных баров
# open_ms % 604800000 == 345600000 (=4 суток) — бар открывается в понедельник 00:00 UTC,
# а эпоха приходится на четверг. У остальных ТФ сдвига нет (проверяется гейтом сетки).
# Команда: uv run python -m hunter check-grid
GRID_ANCHOR_MS: dict[str, int] = {"1w": 4 * 24 * 60 * 60_000}


def grid_anchor_ms(timeframe: str) -> int:
    return GRID_ANCHOR_MS.get(timeframe, 0)


def on_grid(open_ms: int, timeframe: str) -> bool:
    return (open_ms - grid_anchor_ms(timeframe)) % tf_ms(timeframe) == 0


def expected_last_closed_open_ms(timeframe: str, now_ms: int) -> int:
    """Левая граница новейшего бара, который к этому моменту обязан быть закрыт."""
    step = tf_ms(timeframe)
    anchor = grid_anchor_ms(timeframe)
    return ((now_ms - anchor) // step) * step + anchor - step
