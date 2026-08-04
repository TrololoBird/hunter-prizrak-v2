"""Уровень BUY/SELL = ПОК накопления §2.2. Источник — мини-курс, стр. 21-27, 30, 63.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются. Гистограмма сделок
подаётся снаружи — этот модуль её не добывает.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

Механика, взятая дословно:
  стр. 21  ПОК — «максимальный уровень проторговки базы»; в разборах используется ПОК
           КОНКРЕТНЫХ накоплений, а не видимого диапазона
  стр. 22  лонговое накопление — из которого вышли вверх; шортовое — вниз;
           «сила уровня определяется ТФ и объёмом» — ДВА факта, не одно число
  стр. 23  уровень появляется только после полноценного выхода
  стр. 24  уровень работает на своём ТФ и на младших; цель — сопоставимый уровень
           того же ТФ либо ближайший старший
  стр. 25  отработан на 1 касание → удаляется
  стр. 26  профиль натягивается на структуру, «важно захватить ВСЕ свечи структуры»
  стр. 63  фиксированный профиль, а не VRVP — «он более точный»
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .accumulation import Accumulation
from .accumulation import detect as detect_accumulations
from .bars import TIMEFRAME_MS, tf_ms
from .breach import CONFIRM_BODIES, RETURN_BARS, Breach, BreachKind, Direction, first_breach
from .models import Bar, NotReady, TradeHistogram, TradeWindows
from .swings import detect as detect_swings
from .volume_profile import VolumeProfile, build


class LevelSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class Level(BaseModel):
    """Уровень ПОК накопления. Живёт после структуры и ждёт теста (стр. 23, 25)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    side: LevelSide

    price: Decimal
    """ПОК — сам уровень. Стр. 30: «надёжнее всего брать от уровня ПОК»."""

    zone_lo: Decimal
    zone_hi: Decimal
    """Объёмная зона (стр. 30: «цена забирает объёмную зону»).

    ⚠ Отождествление «объёмная зона» = область стоимости 70% — НЕ из курса. Курс рисует
    зону на графике и доли не называет. Проверяется на корпусе (этап 3.3); до проверки
    зона сообщается рядом с ПОК, а не вместо него.
    """

    created_at_index: int
    """Бар подтверждения выхода. Раньше него уровня не существует (стр. 23)."""

    structure_first_index: int
    structure_last_index: int

    structure_from_ms: int
    structure_to_ms: int
    """Окно структуры в биржевом времени — ТО ЖЕ, по которому натянут профиль (стр. 26).

    Индексы выше живут в СВОЁМ ряду баров и между ТФ несравнимы: индекс 40 на 15м и
    индекс 40 на 4ч — разные моменты. Вложенность уровней (стр. 32) без общей шкалы
    времени не выражается вовсе, поэтому окно хранится, а не пересчитывается.
    """

    structure_volume: float = Field(gt=0)
    """Объём структуры. Стр. 22: сила = ТФ И объём, РАЗДЕЛЬНО.

    Свёртки ТФ и объёма в одно число здесь нет и не будет: стр. 22 приводит прямой
    контрпример — «в маленькой часовой наторговке может быть объём больше, чем в 4ч-1д
    накоплении». §3 запрещает составные метрики, курс это подтверждает.
    """

    boundary_lo: Decimal
    boundary_hi: Decimal
    """Границы структуры — за них ставится стоп (стр. 33)."""

    @property
    def breach_direction(self) -> Direction:
        """С какой стороны цена уходит ЗА уровень.

        Лонговый уровень — поддержка: цена подходит сверху, значит уйти за него значит
        уйти ВНИЗ. Шортовый — сопротивление, зеркально (стр. 22, 28).
        """
        return Direction.BELOW if self.side is LevelSide.LONG else Direction.ABOVE

    def flipped(self) -> Level:
        """Уровень после ПРОБОЯ. Стр. 43: «Уровень лонг/шорт менятся для нас на противоположный».

        Меняется только сторона: цена, геометрия и происхождение остаются те же — это
        тот же уровень, прочитанный наоборот, а не новый.
        """
        other = LevelSide.SHORT if self.side is LevelSide.LONG else LevelSide.LONG
        return self.model_copy(update={"side": other})


def structure_window_ms(
    acc: Accumulation, bars: list[Bar], timeframe_ms: int
) -> tuple[int, int]:
    """Окно `[от, до)` для профиля: все бары структуры и только они (стр. 26).

    Свечи ВЫХОДА в окно не входят: по стр. 23 структура кончается там, где цена из неё
    вышла, — тела выхода это уже не накопление. Граница проводится по первому телу
    выхода, а не по бару подтверждения.
    """
    last_inside = acc.exit.first_body_index - 1
    return bars[acc.first_index].open_ms, bars[last_inside].open_ms + timeframe_ms


def build_level(
    acc: Accumulation,
    hist: TradeHistogram,
    symbol: str,
    window_ms: tuple[int, int],
) -> Level | NotReady:
    """Уровень из закрытого накопления.

    Гистограмма обязана покрывать ВСЕ бары структуры (стр. 26). Проверить это здесь
    нельзя — у гистограммы нет разбивки по барам, — поэтому ответственность на
    вызывающем, и он обязан строить её ровно по окну структуры.

    `window_ms` — то же окно, что подано гистограмме (`structure_window_ms`). Передаётся
    аргументом, а не берётся из `hist.first_ms`: у гистограммы это МОМЕНТЫ КРАЙНИХ
    СДЕЛОК, а нужны границы окна. Разница мала и потому опасна — она молча сместила бы
    проверку вложенности (стр. 32) там, где структура кончается тихим участком.
    """
    profile: VolumeProfile | NotReady = build(hist)
    if isinstance(profile, NotReady):
        return NotReady(reason=f"{symbol} {acc.timeframe}: ПОК не построен — {profile.reason}")

    return Level(
        symbol=symbol,
        timeframe=acc.timeframe,
        side=LevelSide.LONG if acc.is_long else LevelSide.SHORT,
        price=profile.poc_price,
        zone_lo=profile.val_price,
        zone_hi=profile.vah_price,
        created_at_index=acc.exit.confirmed_at_index,
        structure_first_index=acc.first_index,
        structure_last_index=acc.last_index,
        structure_from_ms=window_ms[0],
        structure_to_ms=window_ms[1],
        structure_volume=profile.total_volume,
        boundary_lo=Decimal(str(acc.lower.lo)),
        boundary_hi=Decimal(str(acc.upper.hi)),
    )


class Unbuilt(BaseModel):
    """Почему уровень не построен. §4.3: пропуск обязан дойти до оператора с причиной."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    index: int | None
    """Индекс первого бара структуры. None — не дошли даже до структур."""

    reason: str = Field(min_length=1)


def build_all(
    symbol: str,
    series: dict[str, list[Bar]],
    trades: TradeWindows | None,
    timeframes: tuple[str, ...],
) -> tuple[tuple[Level, ...], tuple[Unbuilt, ...]]:
    """Все уровни по всем ТФ плюс НАЗВАННЫЕ причины, где уровень не построен.

    Живёт здесь, а не в карточке: уровни нужны и печати, и эмиссии, и доставке, а
    вторая копия этого цикла разошлась бы с первой на первой же правке.

    Причины возвращаются РАЗОБРАННЫМИ (ТФ отдельно от текста), а не готовой строкой:
    подпись ТФ — дело печати, и склеивать её здесь значит навязывать формат всем
    вызывающим.
    """
    built: list[Level] = []
    unbuilt: list[Unbuilt] = []
    for tf in timeframes:
        bars = series.get(tf)
        if not bars:
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            unbuilt.append(Unbuilt(timeframe=tf, index=None, reason=sw.reason))
            continue
        for acc in detect_accumulations(bars, sw, tf).closed:
            if trades is None:
                unbuilt.append(Unbuilt(timeframe=tf, index=acc.first_index,
                                       reason="сделок не собрано"))
                continue
            lo, hi = structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
            hist = trades.window(lo, hi)
            if isinstance(hist, NotReady):
                unbuilt.append(Unbuilt(timeframe=tf, index=acc.first_index,
                                       reason=hist.reason))
                continue
            lvl = build_level(acc, hist, symbol, (lo, hi))
            if isinstance(lvl, NotReady):
                unbuilt.append(Unbuilt(timeframe=tf, index=acc.first_index,
                                       reason=lvl.reason))
                continue
            built.append(lvl)
    return tuple(built), tuple(unbuilt)


NESTED_MAX_STEPS = 1
"""На сколько ступеней ТФ вниз ищутся вложенные уровни. Число из курса, не подобранное.

Стр. 32 говорит «если мы перейдем на МТФ» — на МЛАДШИЙ ТФ, в единственном числе, то есть
одна ступень. Стр. 24 то же самое запрещает распространять дальше прямо: «Уровни ТФ-2
(15м и ниже) обычно не берутся в расчет, т.к. на старшем ТФ их вообще "нет"».

⚠ Замер 2026-08-04 показал, ЧТО именно это ограничивает. Без него (все младшие ТФ сразу)
лестницы доходили до 15, 17 и 22 ступеней: суточная структура вмещает десятки пятиминутных.
«лучше закуп делать на все уровни» из стр. 32 при двадцати двух ордерах перестаёт быть
исполнимым планом, и это признак того, что читается стр. 32 неверно, а не что рынок такой.
"""


def nested(
    parent: Level, pool: tuple[Level, ...], *, max_steps: int = NESTED_MAX_STEPS
) -> tuple[Level, ...]:
    """Дополнительные уровни ВНУТРИ большой структуры. Источник — стр. 32.

    Дословно: «если у нас есть большая структура на СТФ - и у нее есть основной уровень
    ПОК. Но если мы перейдем на МТФ, то в одной большой структуре могут быть
    дополнительные уровни и цена может забрать как и основной уровень, так и пройтись по
    всем локальным уровням которые находятся в одной большой структуре».

    ⚠ Это НЕ вторые пики того же профиля объёма. Читать стр. 32 так — распространённая
    ошибка (я сам так её понял, пока не прочёл дословно); курс говорит про ПЕРЕХОД НА
    МЛАДШИЙ ТФ, где внутри одной большой структуры видны свои накопления со своими ПОК.
    Механизм уже есть целиком — структуры считаются на всех ТФ; недоставало только
    отношения вложенности, и оно здесь.

    Вложенным считается уровень, у которого:
      * ТФ младше родительского (иначе это не «переход на МТФ»);
      * окно структуры целиком внутри окна родителя — по биржевому времени;
      * сам ПОК внутри границ родительской структуры.

    Проверяется положение ПОК, а не всей младшей структуры: курс говорит про «локальные
    уровни, которые находятся в одной большой структуре», то есть про уровни. Младшая
    структура может выходить за границу проколом (стр. 18), уровень при этом внутри.

    Сторона НЕ фильтруется: стр. 32 велит «закуп делать на все уровни», не различая их
    происхождение. Отбор по стороне — работа вызывающего, здесь его нет.
    """
    rank = _tf_rank(parent.timeframe)
    out = [
        lvl for lvl in pool
        if lvl.symbol == parent.symbol
        and lvl is not parent
        and 1 <= rank - _tf_rank(lvl.timeframe) <= max_steps
        and lvl.structure_from_ms >= parent.structure_from_ms
        and lvl.structure_to_ms <= parent.structure_to_ms
        and parent.boundary_lo <= lvl.price <= parent.boundary_hi
    ]
    return tuple(sorted(out, key=lambda x: x.price))


def _tf_rank(tf: str) -> int:
    """Старшинство ТФ по порядку `TIMEFRAME_MS` — он и есть список курса со стр. 17.

    Своей копии порядка здесь нет умышленно: вторая копия разошлась бы с первой на первой
    же правке. Неизвестный ТФ падает через `tf_ms`, а не становится молча самым младшим.
    """
    tf_ms(tf)
    return list(TIMEFRAME_MS).index(tf)


class LevelState(StrEnum):
    ACTIVE = "active"
    """Уровень жив: цена за него ещё не заходила."""

    WORKED_OFF = "worked_off"
    """Отработан на первое касание — стр. 25: «мы этот уровень удаляем».

    Стр. 31 уточняет, чем именно нельзя торговать: «уровень лимитными ордерами больше не
    торгуем… Позицию от уровня смотрим только по факту слома структуры на более мелких
    ТФ». То есть уровень не исчезает совсем, но лимитки по нему сняты.
    """

    FLIPPED = "flipped"
    """Пробит — стр. 43: «Уровень лонг/шорт менятся для нас на противоположный»."""


class EntryRule(StrEnum):
    """ЧЕМ уровень торгуется в этом состоянии. У каждого значения своя страница.

    Введено 2026-08-04 после разметки автора: у него в легенде 🟢 «лимитки стоят» и
    🟡 «можем проколоть без реакции, работать по факту» — ДВА РАЗНЫХ разрешения, а не
    «активен/неактивен». Мой `limit_orders_allowed` их различал наполовину: он говорил,
    чего делать НЕЛЬЗЯ, и молчал о том, что можно. Молчание о доступном действии — та же
    потеря, что молчание о деградации (§4.3).

    ⚠ Красного 🔴 «шорт-зона в перезакуп» здесь НЕТ и не будет. «Шорт-зона» — это
    `side`, он уже есть; «перезакуп» — рыночная оценка автора, которой курс не даёт ни
    определения, ни порога. Завести под неё состояние значило бы придумать метрику (§3).
    """

    LIMIT = "limit"
    """Лимитки на ПОК и зону — стр. 30: «надежнее всего брать от уровня ПОК»."""

    CONFIRMATION = "confirmation"
    """Только по слому структуры на младшем ТФ — стр. 31.

    Дословно: «уровень лимитными ордерами больше не торгуем… Позицию от уровня смотрим
    только по факту слома структуры на более мелких ТФ». Стр. 25 говорит то же про
    повторные касания: «можно рассматривать вход от 2 или 3 касания только по факту
    слома структуры на младшем ТФ».
    """

    RETEST_FLIPPED = "retest_flipped"
    """По ретесту с обратной стороны, в ПРОТИВОПОЛОЖНУЮ сторону — стр. 43.

    Дословно: «По возврату цены на ретест уровня -  открываем позицию и ставим СТОП за
    накопление». Сам уровень при этом уже другой стороны — `Level.flipped()`.
    """


class LevelStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: LevelState
    event: Breach | None
    """Событие, приведшее к состоянию. None — уровень активен, событий не было."""

    limit_orders_allowed: bool
    """Стр. 25 и 31: после отработки лимитками уровень больше не торгуется."""

    entry_rule: EntryRule
    """Чем уровень торгуется СЕЙЧАС. Не выводится из `state` вызывающим — выводится здесь.

    Вывод отработан-значит-ничего-нельзя напрашивается и НЕВЕРЕН: стр. 25 и 31 прямо
    оставляют вход по слому младшего ТФ, а стр. 43 — вход по ретесту после пробоя.
    """


def status(
    level: Level,
    bars: list[Bar],
    *,
    confirm_bodies: int = CONFIRM_BODIES,
    return_bars: int = RETURN_BARS,
) -> LevelStatus:
    """Состояние уровня §2.10 — выводится из первого события на нём.

    Своей логики у §2.10 нет: прокол уже объявлен отработкой (стр. 6), пробой — флипом
    (стр. 43). Здесь только связывание одного с другим, без новых правил.
    """
    ev = first_breach(bars, float(level.price), level.breach_direction,
                      from_index=level.created_at_index + 1,
                      confirm_bodies=confirm_bodies, return_bars=return_bars)
    if ev is None or ev.kind in (BreachKind.OPEN, BreachKind.UNRESOLVED):
        return LevelStatus(state=LevelState.ACTIVE, event=ev, limit_orders_allowed=True,
                           entry_rule=EntryRule.LIMIT)
    if ev.kind is BreachKind.BREAKOUT:
        return LevelStatus(state=LevelState.FLIPPED, event=ev, limit_orders_allowed=False,
                           entry_rule=EntryRule.RETEST_FLIPPED)
    return LevelStatus(state=LevelState.WORKED_OFF, event=ev, limit_orders_allowed=False,
                       entry_rule=EntryRule.CONFIRMATION)


def first_test_index(level: Level, bars: list[Bar]) -> int | None:
    """Первое касание уровня после его появления. Стр. 25: дальше уровень удаляется.

    Касанием считается заход цены на ПОК: `low <= ПОК <= high`. Прокол объёмной зоны
    без достижения ПОК касанием уровня НЕ считается — стр. 30 разделяет эти события:
    «цена забирает объемную зону» и «цена забирает идеально уровень ПОК».

    `None` означает «тестов ещё не было», а не «нет данных»: диапазон поиска задан
    явно и пуст только если уровень моложе конца ряда.
    """
    poc = float(level.price)
    for i in range(level.created_at_index + 1, len(bars)):
        if bars[i].low <= poc <= bars[i].high:
            return i
    return None
