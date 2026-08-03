"""Допуск символа: хватает ли истории, чтобы величина вообще существовала.

ЧИСТЫЙ МОДУЛЬ (§10.3): никаких часов, сети и глобального состояния.

Замечание владельца 2026-08-03: прогрев ограничивает вселенную, а не только помечает
`NotReady`. Верно — но порог оказался другим, чем в первой редакции отчёта.

ЧТО ИЗМЕРЕНО (docs/audit/wilder-reference-2026-08-03.md):
Проектный ATR и RSI совпадают с ОПУБЛИКОВАННЫМ определением Уайлдера побитово
(расхождение 0.0 и 4.3e-16), то есть верны с первого бара, на котором определены.
Числа 144/193/111 из первой редакции мерили не это: они мерили, за сколько баров
ЧУЖАЯ затравка pandas-ta сходится к правильной. Для допуска они не годятся.

Годится другое: сколько баров нужно, чтобы величина БЫЛА ОПРЕДЕЛЕНА. Замер на срезе
BTCUSDT 1h — индекс первого непустого значения:
    atr14  14   → нужно 15 баров
    rsi14  14   → нужно 15 баров
    macd   33   → нужно 34 бара
    ema200 199  → нужно 200 баров
Раньше этого библиотека не выдаёт значения вовсе, и подставлять на его место число
запрещено (§4.3).
"""

from __future__ import annotations

from .models import NotReady

# Замер 2026-08-03, срез BTCUSDT-1h-500.parquet: индекс первого непустого значения + 1.
# Команда: uv run python gates/indicator_availability.py
REQUIRED_BARS: dict[str, int] = {
    "atr14": 15,
    "rsi14": 15,
    "macd": 34,
    "ema200": 200,
}


def strictest_requirement() -> int:
    """Самое длинное требование среди величин §2.9. Сейчас его задаёт EMA200."""
    return max(REQUIRED_BARS.values())


def available(quantity: str, bars: int) -> bool:
    try:
        return bars >= REQUIRED_BARS[quantity]
    except KeyError:
        raise ValueError(
            f"неизвестная величина {quantity!r}; замерены {sorted(REQUIRED_BARS)}"
        ) from None


def check(quantity: str, bars: int, symbol: str, timeframe: str) -> NotReady | None:
    """None — величину считать можно. NotReady — нельзя, с причиной (§4.3)."""
    need = REQUIRED_BARS.get(quantity)
    if need is None:
        return NotReady(reason=f"{quantity}: требование к истории не замерено")
    if bars < need:
        return NotReady(
            reason=f"{symbol} {timeframe}: {quantity} требует {need} баров, есть {bars}"
        )
    return None


def unavailable_quantities(bars: int) -> list[str]:
    return sorted(q for q, need in REQUIRED_BARS.items() if bars < need)


def admits(bars_by_timeframe: dict[str, int], required: int) -> tuple[bool, list[str]]:
    """Проходит ли символ порог `required` баров на КАЖДОМ заявленном ТФ.

    Возвращает (проходит, список ТФ, которые не дотягивают). Порог передаётся
    аргументом, а не берётся из константы: это решение владельца о политике, а не
    свойство данных.
    """
    short = sorted(tf for tf, n in bars_by_timeframe.items() if n < required)
    return (not short), short
