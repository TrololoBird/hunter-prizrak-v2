"""Приоритет таймфреймов §2.8. Источник — мини-курс, стр. 12, 17, 20, 24, 27, 46, 47, 48.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

  стр. 24  «уровни… работают на этом ТФ, А ТАК ЖЕ НА МЛАДШИХ по отношению к нему»
  стр. 27  «Если структура 4ч тф  - отработку смотрим на 4ч тф / Если 1д - отработка
           будет на 1д»
  стр. 47  «ПРИОРИТЕТ СТАРШИЙ ТАЙМФРЕЙМ»: локальная позиция против старшего ТФ выносится
           по стопу; её можно брать «имея позицию по тренду – в виде хэджа и/или на
           уменьшенный объем риска»
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

from .bars import TF_RANK
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

    ⚠⚠⚠ РАСХОЖДЕНИЕ СПЕЦИФИКАЦИИ С КЛАССИКОЙ, НАЗВАНО 2026-08-28 ПО ПРИКАЗУ ВЛАДЕЛЬЦА
    «проверь приоритет старшего тф». Правка НЕ вносится: свод велит такое расхождение
    предъявлять числом, а не закрывать выбором стороны.

    ЧТО ГОВОРИТ КУРС. Стр. 47 озаглавлена «ПРИОРИТЕТ СТАРШИЙ ТАЙМФРЕЙМ», но КАКОЙ именно
    старший — не уточняет. В её примере сделка идёт от уровня 1H, встречный уровень — 4ч,
    и он назван локальным: «цена забрала уровень СТФ, а локальные уровни отработала
    минимальной реакцией». Раз 4ч там локальный, приоритет стоял выше него — это довод
    ЗА нынешнее прочтение, но прямого указания на странице нет.

    ЧТО ГОВОРИТ КЛАССИКА. Тройной экран Элдера: между экранами множитель 4-6, тренд
    берётся с ОДНОЙ ступени вверх, а не с самой старшей («higher timeframe for trend
    identification, intermediate for entry timing, lower for execution»). Наша лестница
    и есть такие ступени: 5м→15м ×3, 15м→1ч ×4, 1ч→4ч ×4, 4ч→1Д ×6, 1Д→1н ×7.

    ЦЕНА РАСХОЖДЕНИЯ, замер на кадрах `zonesplit` (138 уровней, 118 с определённым
    приоритетом обоими способами):

        приоритет берётся с 1н                        110 из 138 (80%)
        самый старший == ближайший старший             45
        РАСХОДЯТСЯ по ТФ                               73
        из них НАПРАВЛЕНИЕ ПРОТИВОПОЛОЖНО              34 (29% всех сетапов)

    То есть у каждого третьего сетапа вердикт «по тренду / ПРОТИВ тренда» — тот самый,
    что владелец читает в каждом сообщении, — переворачивается от выбора прочтения.
    Разобранный случай: BCH 5м, самый старший (1н) говорит вниз, ближайший старший (15м)
    вверх.

    ⚠ И ещё одно число рядом: тренд приоритета держится на ДВУХ экстремумах у 50 уровней
    из 138. Оно печатается читателю (`holds_for`), то есть не спрятано, но слабость
    основания видна.
    """
    if above not in TF_RANK:
        return Priority(timeframe=None, direction=TrendDirection.NONE, holds_for=0)
    higher = TF_ORDER[TF_RANK[above] + 1:]
    for tf in reversed(higher):
        t = trends.get(tf)
        if t is not None and t.direction is not TrendDirection.NONE:
            return Priority(timeframe=tf, direction=t.direction, holds_for=t.holds_for)
    return Priority(timeframe=None, direction=TrendDirection.NONE, holds_for=0)


def agreement_of(direction: TrendDirection, priority: Priority) -> Agreement:
    """То же правило стр. 18 и 47, но по НАПРАВЛЕНИЮ, а не по стороне уровня.

    Заведено 2026-08-22 для вымпела: `figures.pennant` берёт сторону из тренда (стр. 57
    «Торгуем по тренду»), и своего `LevelSide` у фигуры нет. Правило остаётся ОДНИМ и в
    одном месте — иначе через месяц их станет два и они разойдутся, а разойдутся они
    молча: обе ветки вернут законный `Agreement`, и ни один инвариант не порвётся.
    """
    if priority.direction is TrendDirection.NONE or direction is TrendDirection.NONE:
        return Agreement.NO_PRIORITY
    return (Agreement.BY_TREND if direction is priority.direction
            else Agreement.AGAINST_TREND)


def agreement(side: LevelSide, priority: Priority) -> Agreement:
    """Согласуется ли сторона сделки с приоритетом старшего ТФ (стр. 18, 47)."""
    return agreement_of(
        TrendDirection.UP if side is LevelSide.LONG else TrendDirection.DOWN, priority)


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
    if level.timeframe not in TF_RANK or level.price <= 0:
        return ()
    rank = TF_RANK[level.timeframe]
    want = LevelSide.SHORT if level.side is LevelSide.LONG else LevelSide.LONG
    out: list[CounterLevel] = []
    for mapped in pool:
        other = mapped.level
        if other.symbol != level.symbol or other.side is not want:
            continue
        if other.timeframe not in TF_RANK or TF_RANK[other.timeframe] <= rank:
            continue
        if other.created_at_ms > level.created_at_ms:
            continue
        # ⚠⚠ БЛИЗОСТЬ МЕРЯЕТСЯ ДО САМОГО УРОВНЯ, А НЕ ДО КРАЯ ЕГО ЗОНЫ. Правка 2026-08-20.
        #
        # До неё условие было `зона встречного пересекает нашу коробку`, и при широкой
        # зоне оно срабатывало на любом расстоянии. Живой случай, найденный владельцем на
        # карточке BOME: у часового лонга 0.00087135 предупреждение называло встречным
        # недельный уровень 0.0020062 — «130.2% от ПОК». Ловушкой это не является ни по
        # какому чтению страницы.
        #
        # Стр. 46 описывает ловушку геометрически и через САМ УРОВЕНЬ: «Цена забирает ШОРТ
        # уровень ТФ 1д, ПОД НИМ формирует новые накопление на ТФ ниже 1-4ч» и «цена
        # пробивает шорт уровень ТФ 1д, НАД НИМ формирует структуру». То есть наша младшая
        # структура стоит вплотную к ЛИНИИ старшего уровня — к его ПОК, а не к дальнему
        # краю его области стоимости.
        #
        # Мера близости — СОБСТВЕННАЯ ВЫСОТА нашей структуры: коробка, расширенная на свою
        # высоту вверх и вниз. Числа не вводится, масштаб несёт сам объект — тот же довод,
        # что у `tgbot.zone_position`, где мерой близости служит ширина зоны. Для BOME:
        # высота часовой коробки около 0.00013, недельный ПОК отстоит от неё на восемь
        # таких высот — предупреждение молчит, как и должно.
        box = level.boundary_hi - level.boundary_lo
        if box <= 0 or not (level.boundary_lo - box <= other.price <= level.boundary_hi + box):
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
