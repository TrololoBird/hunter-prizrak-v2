"""Исход сделки от уровня. FOUNDATION.md §8 этап 7, мини-курс стр. 9.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Единица — R, и она из курса, а не привнесена: стр. 9 «Р или R — "один Риск". Это единица
измерения эффективности сделки… позволяет вести расчёт профита/убытка, не указывая
конкретный размер позиций». Сумма процентов результатом не является: сделка со стопом
0.3% и сделка со стопом 30% несопоставимы, а в R сопоставимы по определению.

⚠ ГЛАВНОЕ ОГРАНИЧЕНИЕ, которое здесь НЕ обходится: по бару OHLC неизвестно, что случилось
раньше — стоп или цель. Если один бар накрывает и то и другое, исход НЕ назначается, а
помечается неоднозначным. Выбрать «обычно сначала стоп» значило бы придумать правило,
которого нет ни в данных, ни в курсе (§4.3).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .levels import LevelSide
from .models import Bar


class OutcomeKind(StrEnum):
    STOP = "stop"
    """Цена дошла до стопа. R = −1 по определению."""

    TARGET = "target"
    """Цена дошла до первой основной цели (стр. 24)."""

    AMBIGUOUS = "ambiguous"
    """Один бар накрыл и стоп, и цель. Что было раньше — из OHLC не следует."""

    OPEN = "open"
    """Ряд кончился, ни стоп, ни цель не достигнуты."""

    NOT_FILLED = "not_filled"
    """Цена так и не дошла до входа: сделки не было вовсе."""


class Outcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OutcomeKind
    filled_at_index: int | None
    closed_at_index: int | None
    exit_price: Decimal | None
    r: float | None
    """Результат в R. `None` у неразрешённых — ноль здесь означал бы безубыток."""


def _touched(bar: Bar, price: Decimal) -> bool:
    return bar.low <= float(price) <= bar.high


def resolve(
    side: LevelSide,
    entry: Decimal,
    stop: Decimal,
    target: Decimal | None,
    bars: list[Bar],
    from_index: int,
) -> Outcome:
    """Исход сделки по барам ПОСЛЕ появления уровня.

    Две фазы, и первая обязательна: сначала цена должна дойти до входа (лимитный ордер на
    ПОК, стр. 30), и только потом считается стоп или цель. Сделка, до которой цена не
    дошла, — это не убыток и не прибыль, это `NOT_FILLED`.
    """
    risk = abs(entry - stop)
    if risk == 0:
        return Outcome(kind=OutcomeKind.NOT_FILLED, filled_at_index=None,
                       closed_at_index=None, exit_price=None, r=None)

    long = side is LevelSide.LONG
    filled: int | None = None

    for i in range(from_index, len(bars)):
        bar = bars[i]
        if filled is None:
            if _touched(bar, entry):
                filled = i
            else:
                continue

        hit_stop = (bar.low <= float(stop)) if long else (bar.high >= float(stop))
        hit_tgt = (target is not None
                   and ((bar.high >= float(target)) if long else (bar.low <= float(target))))

        if hit_stop and hit_tgt:
            return Outcome(kind=OutcomeKind.AMBIGUOUS, filled_at_index=filled,
                           closed_at_index=i, exit_price=None, r=None)
        if hit_stop:
            return Outcome(kind=OutcomeKind.STOP, filled_at_index=filled,
                           closed_at_index=i, exit_price=stop, r=-1.0)
        if hit_tgt:
            assert target is not None
            gain = (target - entry) if long else (entry - target)
            return Outcome(kind=OutcomeKind.TARGET, filled_at_index=filled,
                           closed_at_index=i, exit_price=target, r=float(gain / risk))

    if filled is None:
        return Outcome(kind=OutcomeKind.NOT_FILLED, filled_at_index=None,
                       closed_at_index=None, exit_price=None, r=None)
    return Outcome(kind=OutcomeKind.OPEN, filled_at_index=filled,
                   closed_at_index=None, exit_price=None, r=None)
