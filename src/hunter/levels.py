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

from .accumulation import Accumulation, AccumulationScan, BoundaryZone
from .bars import TIMEFRAME_MS, tf_ms
from .breach import CONFIRM_BODIES, RETURN_BARS, Breach, BreachKind, Direction, first_breach
from .models import Bar, NotReady, TradeHistogram, TradeWindows
from .stop_volume import StopVolume
from .stop_volume import classify as classify_stop_volume
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

    created_at_ms: int
    """ЗАКРЫТИЕ бара подтверждения — момент, с которого уровень известен, в биржевом времени.

    Индекс выше живёт в своём ряду и между ТФ несравним; сравнивать моменты появления
    уровней РАЗНЫХ ТФ без общей шкалы нельзя, а сравнивать их приходится: цель по стр. 24
    берётся с другого ТФ, и она обязана существовать НА МОМЕНТ сигнала.

    ⚠ Поле заведено 2026-08-04 по внешнему разбору. До него `geometry.build_targets`
    момент появления не проверял вовсе, и замер на 374 уровнях дал 196 основных целей из
    366 (54%), созданных ПОЗЖЕ уровня, от которого строилась сделка: тейк-профит
    выбирался задним числом. Разбор: docs/audit/critical-review-verified-2026-08-04.md

    Именно закрытие, а не открытие: система работает по закрытым барам (§6), значит бар
    подтверждения становится известен в момент своей правой границы, а не левой.
    """

    structure_first_index: int
    structure_last_index: int

    structure_from_ms: int
    structure_to_ms: int
    """Окно структуры в биржевом времени — ТО ЖЕ, по которому натянут профиль (стр. 26).

    Индексы выше живут в СВОЁМ ряду баров и между ТФ несравнимы: индекс 40 на 15м и
    индекс 40 на 4ч — разные моменты. Вложенность уровней (стр. 32) без общей шкалы
    времени не выражается вовсе, поэтому окно хранится, а не пересчитывается.
    """

    stop_anchor: Decimal | None = None
    """Цена, ЗА которую курс велит прятать стоп. `None` — якоря нет, ставится запас 1-3%.

    Стр. 18 даёт ДВА повода сдвинуть стоп дальше границы, и оба вычисляются здесь, а не
    оставляются оператору:

      * «если на 3++ точках были **проколы** за границы — стоп ВСЕГДА ставится за этот
        прокол, т.к. это может быть расширением базы»;
      * «если в диапазоне **2-5% от границы** есть **стоповый объем** — база мелкого ТФ —
        или Лой того же ТФ или ТФ-1 — **идеально стоп прятать за них**».

    Берётся ДАЛЬНИЙ из применимых якорей: оба правила говорят «стоп за X», и стоп за
    самым дальним удовлетворяет обоим сразу.

    ⚠ Заведено 2026-08-05. До него `geometry.build_setup` печатал ТРИ стопа —
    безопасный ближний, безопасный дальний и рисковый, — и выбор оставался оператору.
    Выставить можно только один; значит РР был не определён, а все замеры РР и R
    считались по невыбранному стопу. При этом `stop_volume.py` (§2.3, стр. 34-40) был
    реализован целиком и НЕ ВЫЗЫВАЛСЯ ниоткуда, а `BoundaryZone.puncture` вычислялся и
    не читался: оба входа правила стр. 18 существовали и не были соединены с ним.
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


def created_at_ms(acc: Accumulation, bars: list[Bar], timeframe_ms: int) -> int:
    """Момент, с которого уровень существует: ЗАКРЫТИЕ бара подтверждения (стр. 23).

    Выделено в функцию, потому что число нужно двоим — `build_all` и любому зонду, — а
    вторая копия «плюс один таймфрейм» разошлась бы с первой на первой же правке.
    """
    return bars[acc.exit.confirmed_at_index].open_ms + timeframe_ms


def build_level(
    acc: Accumulation,
    hist: TradeHistogram,
    symbol: str,
    window_ms: tuple[int, int],
    born_ms: int,
) -> Level | NotReady:
    """Уровень из закрытого накопления.

    Гистограмма обязана покрывать ВСЕ бары структуры (стр. 26). Проверить это здесь
    нельзя — у гистограммы нет разбивки по барам, — поэтому ответственность на
    вызывающем, и он обязан строить её ровно по окну структуры.

    `window_ms` — то же окно, что подано гистограмме (`structure_window_ms`). Передаётся
    аргументом, а не берётся из `hist.first_ms`: у гистограммы это МОМЕНТЫ КРАЙНИХ
    СДЕЛОК, а нужны границы окна. Разница мала и потому опасна — она молча сместила бы
    проверку вложенности (стр. 32) там, где структура кончается тихим участком.

    `born_ms` — `created_at_ms(acc, bars, tf_ms)`. Тоже аргументом, и по той же причине,
    что и окно: ряда баров здесь нет, а выводить момент из `structure_to_ms` арифметикой
    («плюс два тела») неверно на ряду с разрывом.
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
        created_at_ms=born_ms,
        structure_first_index=acc.first_index,
        structure_last_index=acc.last_index,
        structure_from_ms=window_ms[0],
        structure_to_ms=window_ms[1],
        structure_volume=profile.total_volume,
        boundary_lo=Decimal(str(acc.lower.edge)),
        boundary_hi=Decimal(str(acc.upper.edge)),
    )


class Unbuilt(BaseModel):
    """Почему уровень не построен. §4.3: пропуск обязан дойти до оператора с причиной."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    index: int | None
    """Индекс первого бара структуры. None — не дошли даже до структур."""

    reason: str = Field(min_length=1)


LevelBuild = tuple[tuple["Level", ...], tuple["Unbuilt", ...]]
"""Что отдаёт `build_all`: построенные уровни и НАЗВАННЫЕ причины, где не построен.

Имя заведено, чтобы этот результат можно было передать из рук в руки, не разворачивая:
`card.render` принимает его целиком (находка А-2, дублирование расчёта).
"""


def build_all(
    symbol: str,
    series: dict[str, list[Bar]],
    trades: TradeWindows | None,
    timeframes: tuple[str, ...],
    scans: dict[str, AccumulationScan],
) -> LevelBuild:
    """Все уровни по всем ТФ плюс НАЗВАННЫЕ причины, где уровень не построен.

    Живёт здесь, а не в карточке: уровни нужны и печати, и эмиссии, и доставке, а
    вторая копия этого цикла разошлась бы с первой на первой же правке.

    Причины возвращаются РАЗОБРАННЫМИ (ТФ отдельно от текста), а не готовой строкой:
    подпись ТФ — дело печати, и склеивать её здесь значит навязывать формат всем
    вызывающим.

    ⚠ `scans` подаётся ГОТОВЫМ и обязателен с 2026-08-06. До этого функция звала
    `swings.detect` и `accumulation.detect` сама — и звала их ВТОРОЙ раз: первый делала
    карточка по тем же барам. Разбор ряда теперь один на весь конвейер и живёт в
    `engine.read_series`; ТФ, не давшие разбора, туда просто не попадают, и причина
    называется там же (§4.3).
    """
    built: list[Level] = []
    unbuilt: list[Unbuilt] = []
    for tf in timeframes:
        bars = series.get(tf)
        scan = scans.get(tf)
        if not bars or scan is None:
            continue
        accs = scan.closed
        younger = _younger_tf(tf, timeframes)
        for acc in accs:
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
            lvl = build_level(acc, hist, symbol, (lo, hi),
                              created_at_ms(acc, bars, TIMEFRAME_MS[tf]))
            if isinstance(lvl, NotReady):
                unbuilt.append(Unbuilt(timeframe=tf, index=acc.first_index,
                                       reason=lvl.reason))
                continue
            # Стоповый объём — накопления ТФ-1, отнесённые к этой структуре (стр. 34-37).
            # Якорь стопа считается ЗДЕСЬ, где есть оба ряда баров: у `build_setup` их нет.
            svs: tuple[StopVolume, ...] = ()
            y_scan = scans.get(younger) if younger is not None else None
            y_bars = series.get(younger) if younger is not None else None
            if y_scan is not None and y_bars:
                svs = classify_stop_volume(y_scan.closed, y_bars, acc, bars, tf).items
            down = lvl.side is LevelSide.LONG
            anchor = stop_anchor(lvl.side, float(lvl.boundary_lo if down else lvl.boundary_hi),
                                 acc.lower if down else acc.upper, svs)
            built.append(lvl.model_copy(update={"stop_anchor": anchor}))
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
      * сам ПОК внутри границ родительской структуры;
      * он УЖЕ СУЩЕСТВОВАЛ, когда появился родитель (стр. 23).

    Последнее условие добавлено 2026-08-04: окно младшей структуры может лежать внутри
    родительского, а бар подтверждения её выхода — уже за ним. Лестница закупа (стр. 32)
    в этом случае содержала бы ордер на уровень, которого в момент сигнала ещё нет.

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
        and lvl.created_at_ms <= parent.created_at_ms
        and parent.boundary_lo <= lvl.price <= parent.boundary_hi
    ]
    return tuple(sorted(out, key=lambda x: x.price))


STOP_ANCHOR_BAND_MIN_PCT = 2.0
STOP_ANCHOR_BAND_MAX_PCT = 5.0
"""Полоса поиска стопового объёма у границы. Стр. 18 называет её прямо: «в диапазоне
2-5% от границы». Числа взяты у курса, не подобраны, и §4.1 их допускает как исключение
(«величины, которые курс задаёт в процентах прямо»)."""

MIN_PUNCTURE_POINTS = 3
"""Стр. 18: «если на 3++ точках были проколы за границы - стоп всегда ставится за этот прокол»."""


def stop_anchor(
    side: LevelSide,
    boundary: float,
    zone: BoundaryZone,
    stop_volumes: tuple[StopVolume, ...],
) -> Decimal | None:
    """Цена, за которую прячется стоп по стр. 18. `None` — якоря нет.

    Возвращается ДАЛЬНИЙ якорь: прокол и стоповый объём — оба вида «стоп за X», и стоп
    за самым дальним из них удовлетворяет обоим требованиям сразу.
    """
    down = side is LevelSide.LONG          # лонг: якоря НИЖЕ границы, шорт — выше
    lo_pct, hi_pct = STOP_ANCHOR_BAND_MIN_PCT, STOP_ANCHOR_BAND_MAX_PCT
    if down:
        band_lo, band_hi = boundary * (1 - hi_pct / 100), boundary * (1 - lo_pct / 100)
    else:
        band_lo, band_hi = boundary * (1 + lo_pct / 100), boundary * (1 + hi_pct / 100)

    found: list[float] = []
    if len(zone.point_indices) >= MIN_PUNCTURE_POINTS and zone.puncture is not None:
        found.append(zone.puncture)
    for sv in stop_volumes:
        edge = sv.accumulation.lower.edge if down else sv.accumulation.upper.edge
        if band_lo <= edge <= band_hi:
            found.append(edge)
    if not found:
        return None
    return Decimal(str(min(found) if down else max(found)))


def _younger_tf(tf: str, available: tuple[str, ...]) -> str | None:
    """Ближайший МЛАДШИЙ ТФ из имеющихся — «ТФ-1» курса (стр. 18, 24, 34).

    `None` — младше нечего взять, и тогда стоповый объём не ищется вовсе. Это НЕ значит
    «стопового объёма нет»: значит, что ряд, на котором он виден, не собран.
    """
    rank = _tf_rank(tf)
    younger = [t for t in available if _tf_rank(t) < rank]
    return max(younger, key=_tf_rank) if younger else None


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

    resolved_at_ms: int | None
    """ЗАКРЫТИЕ бара, на котором событие разрешилось. None — не разрешилось (или его нет).

    Без этого поля состояние отвечает только на вопрос «что с уровнем СЕЙЧАС», а нужен
    ещё и вопрос «что с ним было на момент сигнала»: отбор целей по нынешнему состоянию
    — такое же заглядывание вперёд, как и отбор целей из будущего (Н-1).
    """

    def state_at(self, as_of_ms: int) -> LevelState:
        """Состояние НА МОМЕНТ `as_of_ms`, а не на конец ряда.

        Эквивалентно пересчёту `status` по ряду, обрезанному этим моментом, и это не
        совпадение: у уровня по стр. 25/43 ОДНА судьба, её ищет `first_breach`, и первое
        событие ряда не зависит от того, насколько ряд продолжается вправо. Пока событие
        не разрешилось, `status` и так отдаёт `active` (ветки OPEN и UNRESOLVED).

        Считать обрезкой ряда было бы честно, но дорого: 374 уровня × пул целей — это
        десятки тысяч повторных проходов `first_breach` вместо одного сравнения.
        """
        if self.resolved_at_ms is None or self.resolved_at_ms > as_of_ms:
            return LevelState.ACTIVE
        return self.state


class MappedLevel(BaseModel):
    """Уровень ВМЕСТЕ с его судьбой. Пара, а не два независимых значения.

    Заведено 2026-08-04: `geometry.build_targets` брал целью любой уровень пула, а замер
    показал, что 83% целей — уровни в состоянии `worked_off` или `flipped`, то есть ровно
    те, про которые стр. 25 говорит «мы этот уровень удаляем», а стр. 43 — что он стал
    противоположным. Состояние было доступно вызывающему и просто не подавалось; пара
    закрывает эту возможность на уровне типа, а не дисциплины.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    status: LevelStatus

    def alive_at(self, as_of_ms: int) -> bool:
        """Существовал ли уровень на момент `as_of_ms` и был ли ещё в силе.

        Два условия, и оба из курса: уровня нет до подтверждения выхода (стр. 23) и нет
        после отработки или пробоя (стр. 25, 43).
        """
        return (self.level.created_at_ms <= as_of_ms
                and self.status.state_at(as_of_ms) is LevelState.ACTIVE)


def map_levels(
    built: tuple[Level, ...], series: dict[str, list[Bar]]
) -> tuple[MappedLevel, ...]:
    """Уровни со свежепосчитанной судьбой. Одно место, где `status` зовётся по всему пулу.

    Уровень, чьего ряда в кадрах нет, в карту НЕ попадает: состояние у него неизвестно, а
    подставить `active` значило бы объявить живым то, что не проверялось (§4.3).
    """
    return tuple(
        MappedLevel(level=lvl, status=status(lvl, bars))
        for lvl in built
        if (bars := series.get(lvl.timeframe))
    )


def status(
    level: Level,
    bars: list[Bar],
    *,
    confirm_bodies: int = CONFIRM_BODIES,
    return_bars: int = RETURN_BARS,
) -> LevelStatus:
    """Состояние уровня §2.10 — выводится из первого события на нём.

    Своей логики у §2.10 нет: прокол уже объявлен отработкой (стр. 6), пробой — флипом
    (стр. 43), первое касание — снятием лимиток (стр. 25). Здесь только связывание одного
    с другим, без новых правил.

    ⚠ КАСАНИЕ подключено 2026-08-06, и до этого правило стр. 25 не исполнялось вовсе.
    Состояние выводилось только из `first_breach`, то есть из того, ЗАШЛА ли цена ЗА
    уровень. Но стр. 25 говорит о другом событии — о приходе цены НА уровень:

        «Как только уровень был отработан на 1 касание (как на графике - увидели хорошую
        реакцию) этот уровень становиться больше не актуальным, т.е. мы этот уровень
        удаляем и ищем новые не отработанные уровни для входа в новую позицию.
        … можно рассматривать вход от 2 или 3 касания только по факту слома структуры на
        младшем ТФ»

    Замер на кадрах прогона `a1` (3 символа, 94 уровня): из 33 уровней, по которым система
    выставляла лимитки, **18 цена уже касалась** — больше половины. У всех восемнадцати
    событие было `unresolved`, то есть цена за уровень заходила, но курс вердикта
    прокол-или-пробой не давал, и `status` оставлял лимитки разрешёнными.

    Касание меняет РАЗРЕШЕНИЕ, а не состояние, и это существенно. Иначе флипов не было бы
    вовсе: любой пробой начинается с касания, и «первое событие — касание» объявляло бы
    отработанным даже то, что стр. 43 велит считать противоположным уровнем. Поэтому
    `state` по-прежнему выводит `first_breach`, а касание снимает лимитки и переводит вход
    на слом младшего ТФ.
    """
    ev = first_breach(bars, float(level.price), level.breach_direction, level.timeframe,
                      from_index=level.created_at_index + 1,
                      confirm_bodies=confirm_bodies, return_bars=return_bars)
    if ev is None or ev.kind in (BreachKind.OPEN, BreachKind.UNRESOLVED):
        if first_test_index(level, bars) is not None:
            return LevelStatus(state=LevelState.ACTIVE, event=ev,
                               limit_orders_allowed=False,
                               entry_rule=EntryRule.CONFIRMATION, resolved_at_ms=None)
        return LevelStatus(state=LevelState.ACTIVE, event=ev, limit_orders_allowed=True,
                           entry_rule=EntryRule.LIMIT, resolved_at_ms=None)
    assert ev.resolved_index is not None  # у разрешившегося события бар есть по построению
    at = bars[ev.resolved_index].open_ms + tf_ms(level.timeframe)
    if ev.kind is BreachKind.BREAKOUT:
        return LevelStatus(state=LevelState.FLIPPED, event=ev, limit_orders_allowed=False,
                           entry_rule=EntryRule.RETEST_FLIPPED, resolved_at_ms=at)
    return LevelStatus(state=LevelState.WORKED_OFF, event=ev, limit_orders_allowed=False,
                       entry_rule=EntryRule.CONFIRMATION, resolved_at_ms=at)


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
