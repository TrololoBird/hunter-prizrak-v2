"""Допуск символа: хватает ли истории, чтобы величина вообще существовала.

ЧИСТЫЙ МОДУЛЬ (§10.3): никаких часов, сети и глобального состояния.

Замечание владельца 2026-08-03: прогрев ограничивает вселенную, а не только помечает
`NotReady`. Верно — но порог оказался другим, чем в первой редакции отчёта.

ЧТО ИЗМЕРЕНО (docs/audit/formula-reference-2026-08-03.md):
Требований к истории ДВА, и их надо различать.
  1. «Определена» — раньше этого библиотека не выдаёт значения вовсе.
  2. «Канонична» — раньше этого значение зависит от выбора затравки, а не от
     формулы, и потому не имеет внешнего референта (§0).
У ATR, RSI, EMA и полос Боллинджера оба совпадают: затравка подтверждена сверкой
с опубликованной формулой. У MACD и ADX — нет.

Числа 144/193/111 из первой редакции отчёта мерили НЕ ЭТО: они мерили, за сколько
баров чужая затравка pandas-ta сходится к правильной. Для допуска не годятся.
"""

from __future__ import annotations

from .models import NotReady

# Сколько баров нужно, чтобы величина БЫЛА ОПРЕДЕЛЕНА библиотекой.
# Замер 2026-08-03, срез BTCUSDT-1h-500.parquet: индекс первого непустого значения + 1.
# Команда: uv run python gates/indicator_availability.py
DEFINED_FROM_BARS: dict[str, int] = {
    "atr14": 15,
    "rsi14": 15,
    "adx14": 28,
    "macd": 34,
    "ema200": 200,
}

# Сколько баров нужно, чтобы величина была КАНОНИЧНОЙ — то есть совпадала с
# опубликованной формулой, а не зависела от выбора затравки.
# Замер 2026-08-03 (gates/formula_reference.py, допуск 1e-9):
#   atr14, rsi14, ema200, bb_upper — затравка ПОДТВЕРЖДЕНА, каноничны сразу;
#   macd  — рекурсия сходится к 1e-13, но затравка расходится до индекса 160
#           включительно, то есть каноничен с 162-го бара;
#   adx14 — сходится к 1e-16, затравка расходится до индекса 302, каноничен с 304-го.
# Опубликованные источники затравку не задают вовсе (Wikipedia, Exponential
# smoothing: допускаются и s_0 = x_0, и среднее первых N). Поэтому значение
# MACD/ADX раньше точки схождения не имеет внешнего референта и по §0 не
# выдаётся как факт. Первичный источник (Appel для MACD, Wilder для ADX)
# закроет вопрос; до тех пор действует консервативное требование.
CANONICAL_FROM_BARS: dict[str, int] = {
    "atr14": 15,
    "rsi14": 15,
    "ema200": 200,
    "bb_upper": 20,
    "macd": 162,
    "adx14": 304,
}

# Для допуска берётся строгое из двух.
REQUIRED_BARS: dict[str, int] = {
    q: max(DEFINED_FROM_BARS.get(q, 0), CANONICAL_FROM_BARS.get(q, 0))
    for q in set(DEFINED_FROM_BARS) | set(CANONICAL_FROM_BARS)
}


# Величины, которые §2.9 называет ДОСЛОВНО: «RSI, MACD (дивергенция/конвергенция),
# полосы Боллинджера…, MA/EMA 200». ADX там НЕ значится — он сверен с формулой
# (§10.2 называет его среди тех, что не писать самим), но системой не используется
# и порог допуска задавать не должен. ATR — не индикатор §2.9, а единица измерения
# порогов по §4.1.
USED_BY_2_9: tuple[str, ...] = ("rsi14", "macd", "bb_upper", "ema200")


def strictest_requirement() -> int:
    """Самое длинное требование среди величин, которые §2.9 действительно называет."""
    return max(REQUIRED_BARS[q] for q in USED_BY_2_9)


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
