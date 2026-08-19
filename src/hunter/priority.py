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

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .geometry import TF_ORDER
from .levels import Level, LevelSide, LevelState, MappedLevel
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


class CounterPlacement(StrEnum):
    """Где встречный уровень старшего ТФ стоит относительно базы этого. Стр. 46."""

    INSIDE = "inside"
    """ПОК встречного уровня ВНУТРИ коробки базы — вариант 2 стр. 46: структура выросла
    на самом уровне старшего ТФ, а на старшем графике это «просто прокол тенью»."""

    ABOVE = "above"
    """База прижата ПОД встречным уровнем — вариант 1 стр. 46."""

    BELOW = "below"
    """База стоит НАД встречным уровнем — зеркало варианта 1 (нижний ряд рисунка)."""


class CounterLevel(BaseModel):
    """Встречный уровень СТАРШЕГО ТФ вплотную к базе этого уровня. Ловушка стр. 46."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    side: LevelSide
    price: Decimal
    zone_lo: Decimal
    zone_hi: Decimal
    placement: CounterPlacement

    state: LevelState
    """Состояние встречного уровня НА МОМЕНТ рождения этого. Оно и есть суть ловушки:
    стр. 46 «цена просто сделал тест уровня и пошла на коррекцию» — это отработка
    старшего уровня, а не выход из него."""

    distance_pct: float
    """Расстояние ПОК-ПОК в процентах от ПОК этого уровня. Числа «рядом» курс не даёт,
    поэтому величина ПЕЧАТАЕТСЯ, а порога по ней нет."""


_PLACEMENT_WORD = {
    CounterPlacement.INSIDE: "проходит ЧЕРЕЗ базу",
    CounterPlacement.ABOVE: "база прижата ПОД ним",
    CounterPlacement.BELOW: "база стоит НАД ним",
}

_STATE_WORD = {
    LevelState.ACTIVE: "ещё не тронут",
    LevelState.WORKED_OFF: "цена его уже забрала",
    LevelState.FLIPPED: "уже пробит",
}

_TRAP_RULE = (
    "Стр. 46: «Всегда соблюдаем таймфрейм» — на старшем графике это может быть "
    "«просто прокол тенью и уход в коррекцию» (стр. 46). "
    "Стр. 40: «Всегда приоритетный тот ТФ, на котором находится текущее накопление»"
)


def counter_levels(level: Level, pool: tuple[MappedLevel, ...]) -> tuple[CounterLevel, ...]:
    """Встречные уровни СТАРШИХ ТФ, чья зона пересекает базу этого уровня. Стр. 46.

    Оба варианта стр. 46 нарисованы одной геометрией: линия уровня старшего ТФ проходит
    сквозь коробку младшего накопления либо касается её края. Пересечение объёмной зоны
    старшего уровня с коробкой базы — та же картинка без выдуманного расстояния.

    ⚠ Отработанные и пробитые встречные уровни НЕ отсеиваются, в отличие от целей: в
    обоих вариантах стр. 46 цена уже «забирает ШОРТ уровень ТФ 1д» либо «пробивает шорт
    уровень ТФ 1д», то есть ловушка живёт именно там, где старший уровень только что
    сняли. Проверяется одно — что уровень уже СУЩЕСТВОВАЛ (стр. 23), а его состояние на
    тот момент отдаётся полем `state`. Сторона берётся ИСХОДНАЯ: стр. 46 зовёт его
    «шорт уровень ТФ 1д» и после пробоя.
    """
    if level.timeframe not in TF_ORDER or level.price <= 0:
        return ()
    rank = TF_ORDER.index(level.timeframe)
    want = LevelSide.SHORT if level.side is LevelSide.LONG else LevelSide.LONG
    out: list[CounterLevel] = []
    for mapped in pool:
        other = mapped.level
        if other.symbol != level.symbol or other.side is not want:
            continue
        if other.timeframe not in TF_ORDER or TF_ORDER.index(other.timeframe) <= rank:
            continue
        if other.created_at_ms > level.created_at_ms:
            continue
        if other.zone_hi < level.boundary_lo or other.zone_lo > level.boundary_hi:
            continue
        if other.price > level.boundary_hi:
            placement = CounterPlacement.ABOVE
        elif other.price < level.boundary_lo:
            placement = CounterPlacement.BELOW
        else:
            placement = CounterPlacement.INSIDE
        out.append(CounterLevel(
            timeframe=other.timeframe, side=other.side, price=other.price,
            zone_lo=other.zone_lo, zone_hi=other.zone_hi, placement=placement,
            state=mapped.status.state_at(level.created_at_ms),
            distance_pct=float(abs(other.price - level.price) / level.price * 100)))
    return tuple(sorted(out, key=lambda c: (c.distance_pct, c.timeframe, str(c.price))))


def counter_warning(level: Level, pool: tuple[MappedLevel, ...]) -> str:
    """Готовая строка предупреждения о ловушке ТФ для карточки. Пусто — встречных нет.

    Предупреждение, а не отсев: стр. 46 велит ПЕРЕКЛЮЧИТЬСЯ на старший график и
    посмотреть, не тест ли это, — решение остаётся за оператором.
    """
    found = counter_levels(level, pool)
    if not found:
        return ""
    near = found[0]
    more = f"; всего таких уровней {len(found)}" if len(found) > 1 else ""
    return (f"ловушка таймфрейма: встречный {near.side.value}-уровень {near.timeframe} "
            f"{near.price:.8g} — {_PLACEMENT_WORD[near.placement]}, "
            f"{near.distance_pct:.2f}% от ПОК, {_STATE_WORD[near.state]}. "
            f"{_TRAP_RULE}{more}")
