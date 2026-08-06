"""Приоритет таймфреймов §2.8. Источник — мини-курс, стр. 12, 17, 20, 24, 27, 46, 47, 48.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

  стр. 24  «уровни… работают на этом ТФ, А ТАК ЖЕ НА МЛАДШИХ по отношению к нему»
  стр. 27  «Если структура 4ч тф  - отработку смотрим на 4ч тф / Если 1д - отработка
           будет на 1д»
  стр. 47  «ПРИОРИТЕТ СТАРШИЙ ТАЙМФРЕЙМ»: локальная позиция против старшего ТФ выносится
           по стопу; её можно брать «имея позицию по тренду — в виде хэджа и/или на
           уменьшенный объём риска»
  стр. 18  «Если тренд восходящий, то флет торгуем от нижней границы до верхней (Лонг)…
           Можно торговать в обе стороны, но ПЕРВАЯ ПОЗИЦИЯ ВСЕГДА БЕРЁТСЯ ПО ТРЕНДУ»
  стр. 46  ловушка ТФ: структура на 1-4ч под уровнем 1д — это может быть просто тест или
           прокол тенью на старшем; «Всегда соблюдаем таймфрейм»
  стр. 48  «Чем старше ТФ — тем выше винрейт, но дольше отработка»

Никакого веса, балла или свёртки: стр. 47 задаёт правило детерминированно — сторону
назначает старший ТФ, точка. §4.2 запрещает весовые суммы, и здесь их нет.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .geometry import TF_ORDER
from .levels import LevelSide
from .swings import Trend, TrendDirection


class Agreement(StrEnum):
    BY_TREND = "by_trend"
    """Сторона совпадает с приоритетом старшего ТФ — «первая позиция» (стр. 18)."""

    AGAINST_TREND = "against_trend"
    """Против приоритета. Стр. 47: только как хедж или на уменьшенный риск, не первой."""

    NO_PRIORITY = "no_priority"
    """Приоритет не определён: ни на одном старшем ТФ тренда нет. Не «согласие»."""


class Priority(BaseModel):
    """Кто задаёт сторону. Стр. 47: старший ТФ, у которого тренд определён."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str | None
    """ТФ, задавший приоритет. None — приоритета нет, и это НЕ равно «лонг»."""

    direction: TrendDirection
    holds_for: int


# ⚠ `works_on(level_timeframe)` УДАЛЕНА 2026-08-06: потребителя не было. Она отдавала
# перечень ТФ, на которых уровень действует — свой и младшие, — и это верный факт, но ни
# одно правило системы его не спрашивает. Отбор целей идёт в обратную сторону, по своему
# ТФ и СТАРШИМ, и делает его geometry.build_targets.


def resolve(trends: dict[str, Trend], above: str) -> Priority:
    """Приоритет по стр. 47: САМЫЙ СТАРШИЙ ТФ выше `above`, где тренд определён.

    Идём сверху вниз и берём первый определившийся: «приоритет старший таймфрейм»
    означает именно старшинство, а не голосование нескольких ТФ (§4.2 — весовых сумм нет).
    """
    if above not in TF_ORDER:
        return Priority(timeframe=None, direction=TrendDirection.NONE, holds_for=0)
    higher = TF_ORDER[TF_ORDER.index(above) + 1:]
    for tf in reversed(higher):
        t = trends.get(tf)
        if t is not None and t.direction is not TrendDirection.NONE:
            return Priority(timeframe=tf, direction=t.direction, holds_for=t.holds_for)
    return Priority(timeframe=None, direction=TrendDirection.NONE, holds_for=0)


def agreement(side: LevelSide, priority: Priority) -> Agreement:
    """Согласуется ли сторона сделки с приоритетом старшего ТФ (стр. 18, 47)."""
    if priority.direction is TrendDirection.NONE:
        return Agreement.NO_PRIORITY
    by_trend = ((side is LevelSide.LONG and priority.direction is TrendDirection.UP)
                or (side is LevelSide.SHORT and priority.direction is TrendDirection.DOWN))
    return Agreement.BY_TREND if by_trend else Agreement.AGAINST_TREND
