"""Фигуры курса: флаг, вымпел (треугольник), клин, ГИП, двойное/тройное дно.
Источник — мини-курс, стр. 56, 57, 58, 60, 61, 62.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

⚠ РАСХОЖДЕНИЕ ИСТОЧНИКОВ НАЗВАНО: в корпусе разборов видео автор говорит, что фигуры
классического теханализа не торгует, а курс отводит им семь страниц (56-62) с правилами
входа и стопа. Курс в иерархии выше корпуса, поэтому фигуры реализованы — но по курсу, а
не по классике: ни целей «на высоту фигуры», ни процентов отката классики здесь нет.

Своих проходов по барам у ГИП и у двойного дна тут НЕТ, и это требование самого курса.
Стр. 61: «фактически частный случай "Переприора - слома структуры"»; стр. 62: «это просто
накопление, но граница накопления являлась сломом структуры». Значит обе — признаки на
уже построенных `Pereprior` и `TradingRange`, а не отдельные детекторы.

Уровень входа сюда не импортируется: `FigureEntry.NEAREST_LEVEL` называет ПРАВИЛО курса
(«ближайший уровень… "если он есть"»), а какой именно уровень — разрешает вызывающий по
`levels.Level`. Так модуль остаётся без зависимости от профиля объёма и стопа.

Цены здесь `float`, как в `swings`, `range` и `pereprior`: все они приходят из
`Bar`, где цена float. Decimal живёт на границе с `Level`, а `Level` сюда не приходит.

Запаса за структуру (стр. 33, 58: 1-3%) этот модуль не считает — он уже есть в
`geometry.STOP_MARGIN_MIN_PCT`/`STOP_MARGIN_MAX_PCT`. Наружу отдаётся ЯКОРЬ стопа,
запас накладывает `geometry`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import NotReady
from .pereprior import Pereprior, PPKind, PPSide
from .priority import Agreement, Priority, agreement_of
from .swings import Swing, SwingKind, SwingSet, TrendDirection, trend
from .trading_range import (
    MIN_BOUNDARY_POINTS,
    MIN_BOUNDARY_POINTS_PER_SIDE,
    OpenStructure,
    TradingRange,
)

NO_TREND_REASON = (
    "тренда нет ни на своём ТФ, ни на старших, а стр. 57 велит «Торгуем по тренду»: "
    "из тренда берутся и сторона вымпела, и первая точка (стр. 58)"
)
"""Отказ вымпела по стр. 57 — КОНСТАНТОЙ, а не строкой на месте.

Считать такие отказы приходится вызывающему (§4.3: молчаливая деградация запрещена), а
сверка по куску текста разошлась бы с правкой формулировки и молча дала бы ноль.
"""

OWN_TF_SOURCE = "свой ТФ"
"""Значение `Pennant.side_source`, когда сторону задал тренд СВОЕГО ряда."""

TOUCH_FOR_ENTRY = 6
"""Стр. 57: «ждем 6 касание и берем на 6 касании». Стр. 58 повторяет: «берем на 6 касании».

Номер СКВОЗНОЙ, через обе стороны: схема стр. 57 нумерует 1, 3, 5, 7 по одной границе и
2, 4, 6, 8 по другой. Так же считает `TradingRange.touches`, поэтому касания берутся у
структуры, а не пересчитываются здесь.
"""


class FigureKind(StrEnum):
    FLAG = "flag"
    """Флаг, стр. 56 — коррекция в наклонном коридоре, ширина которого НЕ сходится."""

    WEDGE = "wedge"
    """Клин, стр. 60: «Клин - выглядит как флаг, только в сужение.»"""


class FigureSide(StrEnum):
    """Сторона СДЕЛКИ, а не наклона: нисходящая коррекция торгуется в лонг (стр. 56, 60)."""

    LONG = "long"
    SHORT = "short"


class PennantBorders(StrEnum):
    EQUAL = "equal"
    """Стр. 58: «Треугольник с равными границами» — внутрь поехали ОБЕ стороны."""

    SQUEEZED = "squeezed"
    """Стр. 57: треугольник «может быть «с поджатием»» — внутрь едет одна сторона."""


class FigureEntry(StrEnum):
    """ТВХ, названные курсом. Значение — правило, а не цена: цену даёт вызывающий."""

    NEAREST_LEVEL = "nearest_level"
    """Стр. 56: вход «при коррекции к ближайшему уровню в ЛОНГ/ШОРТ "если он есть"».

    То же правило у вымпела (стр. 57: «От уровня в Лонг»), у клина (стр. 60: «вход на
    тесте лонг уровня»), у ГИП (стр. 61: «если снизу есть Лонг уровень») и у двойного дна
    (стр. 62). Оговорка «если он есть» означает, что вход может и не существовать.
    """

    EARLY_PP = "early_pp"
    """Стр. 56: вход на «подтверждение раннего ПереПриора»."""

    TREND_BREAK_TEST = "trend_break_test"
    """Стр. 60: «вход на тесте лонг уровня, или вход на тесте слома тенденции»."""

    TOUCH_6 = "touch_6"
    """Стр. 57: «ждем 6 касание и берем на 6 касании»."""

    ADD_ON = "add_on"
    """Стр. 57: «оставляем на доливку на случай, если цена решит расширить структуру»."""

    PP_TEST = "pp_test"
    """Стр. 62: «вход от теста пробитого уровня (ПереПриора)»; стр. 61 — то же у ГИП."""


class Channel(BaseModel):
    """Флаг (стр. 56) или клин (стр. 60): наклонный коридор из чередующихся точек.

    Отличие одно и оно из курса: у клина ширина коридора СХОДИТСЯ («только в сужение»,
    стр. 60), у флага — нет. Порога сужения не вводится: сравнивается монотонность, а не
    величина, поэтому выдуманного числа здесь нет.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FigureKind
    side: FigureSide
    first_index: int
    last_index: int
    high_indices: tuple[int, ...] = Field(min_length=2)
    low_indices: tuple[int, ...] = Field(min_length=2)
    hi: float
    lo: float
    """Крайние цены точек фигуры. Стопа курс для флага и клина не называет — здесь
    отдаётся геометрия, а не выдуманное правило стопа."""

    @property
    def points(self) -> int:
        return len(self.high_indices) + len(self.low_indices)

    @property
    def entries(self) -> tuple[FigureEntry, ...]:
        """Входы в том порядке, в каком их перечисляет курс (стр. 56 и стр. 60)."""
        if self.kind is FigureKind.WEDGE:
            return (FigureEntry.NEAREST_LEVEL, FigureEntry.TREND_BREAK_TEST)
        return (FigureEntry.NEAREST_LEVEL, FigureEntry.EARLY_PP)


def _alternating(swings: SwingSet) -> list[Swing]:
    """Точки, ЧЕРЕДУЮЩИЕ сторону, — то же правило схем, что в `range.detect`.

    ⚠ Порядок двух свингов ОДНОГО бара (внешний бар даёт и хай, и лой) наследуется от
    `swings.detect`, где сначала идёт HIGH: сортировка устойчива. Из OHLC «что было
    раньше» не следует, значит порядок детерминирован, но произволен, — то же оговорено
    в `pereprior._detect_side`.
    """
    out: list[Swing] = []
    for s in sorted(swings.swings, key=lambda x: x.index):
        if out and out[-1].kind is s.kind:
            continue
        out.append(s)
    return out


def _build_channel(
    run: list[Swing], side: FigureSide | None, min_points: int, min_points_per_side: int
) -> Channel | None:
    if side is None:
        return None
    # Стр. 58: «если тренд лонговый, первая точка идет сверху». Правило записано для
    # вымпела, но строится по нему ВСЯКАЯ фигура по тренду: стр. 60 приравнивает клин к
    # флагу, а флаг курс рисует коррекцией внутрь тренда. Значит у лонговой фигуры первая
    # точка — хай; пришедшая раньше точка другой стороны в фигуру не входит.
    head = SwingKind.HIGH if side is FigureSide.LONG else SwingKind.LOW
    pts = run[1:] if run and run[0].kind is not head else run
    highs = [s for s in pts if s.kind is SwingKind.HIGH]
    lows = [s for s in pts if s.kind is SwingKind.LOW]
    if (len(pts) < min_points
            or len(highs) < min_points_per_side
            or len(lows) < min_points_per_side):
        return None
    pairs = min(len(highs), len(lows))
    widths = [highs[i].price - lows[i].price for i in range(pairs)]
    narrowing = all(widths[i] < widths[i - 1] for i in range(1, pairs))
    return Channel(
        kind=FigureKind.WEDGE if narrowing else FigureKind.FLAG,
        side=side,
        first_index=pts[0].index,
        last_index=pts[-1].index,
        high_indices=tuple(s.index for s in highs),
        low_indices=tuple(s.index for s in lows),
        hi=max(s.price for s in highs),
        lo=min(s.price for s in lows),
    )


def detect_channels(
    swings: SwingSet,
    *,
    min_points: int = MIN_BOUNDARY_POINTS,
    min_points_per_side: int = MIN_BOUNDARY_POINTS_PER_SIDE,
) -> tuple[Channel, ...]:
    """Флаги (стр. 56) и клинья (стр. 60) — коридоры, наклонённые в одну сторону.

    Коридор — это отрезок чередующихся точек, у которого хаи и лои идут В ОДНУ сторону:
    падают оба (коррекция вниз, торгуется в ЛОНГ) либо растут оба (коррекция вверх, в
    ШОРТ). Равенство цен наклон не продолжает — как и у фрактала в `swings.detect`, где
    соседи сравниваются строго.

    Сходящийся коридор курс называет клином, расходящийся и параллельный — флагом.
    Отличие снимается монотонностью ширины, без порога: порог здесь был бы выдуман, а
    стр. 60 даёт только слово «в сужение».

    Минимум точек взят у накопления (стр. 22: «4 и более точек»), потому что курс
    приравнивает эти фигуры к структуре: стр. 57 «треугольник это структура накопления».
    Своего числа точек для флага курс не называет, и оно не выдумывается.
    """
    pts = _alternating(swings)
    found: list[Channel] = []
    run: list[Swing] = []
    side: FigureSide | None = None

    def prev_same(kind: SwingKind) -> Swing | None:
        for s in reversed(run):
            if s.kind is kind:
                return s
        return None

    for s in pts:
        base = prev_same(s.kind)
        if base is None:
            run.append(s)
            continue
        if s.price < base.price:
            cand: FigureSide | None = FigureSide.LONG
        elif s.price > base.price:
            cand = FigureSide.SHORT
        else:
            cand = None
        if cand is None or (side is not None and cand is not side):
            built = _build_channel(run, side, min_points, min_points_per_side)
            if built is not None:
                found.append(built)
            # Коридор кончился. Новый начинается с ПОСЛЕДНЕЙ точки старого и с этой:
            # двух точек разных сторон, наклон между которыми ещё не определён.
            run = [run[-1], s]
            side = None
            continue
        side = cand
        run.append(s)

    built = _build_channel(run, side, min_points, min_points_per_side)
    if built is not None:
        found.append(built)
    return tuple(found)


def channel_early_pp(channel: Channel, pps: Sequence[Pereprior]) -> Pereprior | NotReady:
    """Ранний ПП, на подтверждение которого стр. 56 разрешает вход во флаг.

    Берётся самый ранний ПП нужной стороны, подтверждённый не раньше начала фигуры:
    именно он и есть «подтверждение раннего ПереПриора» этой коррекции.
    """
    want = PPSide.LONG if channel.side is FigureSide.LONG else PPSide.SHORT
    fits = [p for p in pps
            if p.kind is PPKind.EARLY
            and p.side is want
            and p.confirmed_at_index >= channel.first_index]
    if not fits:
        return NotReady(
            reason=f"раннего ПП стороны {want.value} внутри фигуры нет — "
                   "вход по стр. 56 остаётся только от ближайшего уровня"
        )
    return min(fits, key=lambda p: p.confirmed_at_index)


class Pennant(BaseModel):
    """Вымпел (треугольник), стр. 57 и 58: структура накопления в сужении.

    Своей геометрии не строит: стр. 57 говорит «треугольник это структура накопления
    (стоповый), просто в сужении», поэтому признак снимается с готового накопления —
    с тех самых полей, по которым накопление и предъявляется владельцу.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    borders: PennantBorders
    side: FigureSide
    first_index: int
    """Бар ПЕРВОГО касания в нумерации стр. 58 — точки со стороны тренда."""

    last_point_index: int
    touches: int
    """Сквозной счёт касаний обеих границ (стр. 57, схема).

    ⚠ Это НЕ `TradingRange.touches`, и с 2026-08-22 расходится с ним на единицу там, где
    структура началась точкой ПРОТИВ тренда: стр. 58 велит начинать счёт со стороны
    тренда, значит такая точка номера не получает. Иначе карточка печатала бы «касаний
    N» по одному счёту и «6-е на баре X» по другому — две величины под одним именем.
    """

    touch6_index: int | None
    """Бар ШЕСТОГО касания — ТВХ по стр. 57. `None` — касаний ещё меньше шести."""

    stop_anchor: float
    """Стр. 57: «прячем за лой всей структуры, или за лой стопового уровня перед структурой».

    Берётся первое: край РАСШИРЕННОГО диапазона структуры (`extended_lo`/`extended_hi`) —
    самая дальняя цена, до которой доходили точки границы, включая проколы (стр. 18).
    Второй вариант — стоповый уровень ПЕРЕД структурой — этому модулю не виден: он живёт
    в `levels.stop_anchor`, и выбор между двумя якорями делает вызывающий.

    Запас за структуру сюда не входит: стр. 58 «Стоп всегда прячем за всю структуру с
    запасом 1-3%», и этот запас накладывает `geometry`.
    """

    upper_edge: float
    lower_edge: float
    is_extended: bool
    """Структура уже расширялась (стр. 18) — тот случай, ради которого стр. 57 велит
    оставлять доливку: «цена решит расширить структуру»."""

    is_open: bool
    """Структура ещё не закрыта. Вход на 6 касании берётся ВНУТРИ неё, поэтому признак
    имеет смысл и до выхода цены (стр. 23 запрещает лишь УРОВЕНЬ, а не вход по стр. 57)."""

    side_source: str
    """ЧЕЙ тренд задал сторону: `OWN_TF_SOURCE` либо имя старшего ТФ (стр. 47).

    Печатается в карточке, а не хранится для отладки: сторона, взятая с 1Д для вымпела на
    5м, — это другое утверждение, чем сторона, взятая со своего ряда, и владелец обязан
    видеть, какое из двух перед ним.
    """

    agreement: Agreement
    """Согласие стороны со старшим ТФ (стр. 47) — `priority.agreement_of`.

    ⚠ Расхождение НЕ переворачивает сторону. Стр. 47 не говорит «торгуй наоборот», она
    говорит, что позиция против старшего берётся «имея позицию по тренду – в виде хэджа
    и/или на уменьшенный объем риска». Значит это ПОМЕТКА, а не правка расчёта; переворот
    был бы выдумкой сверх курса.
    """

    @property
    def entries(self) -> tuple[FigureEntry, ...]:
        """Стр. 57: от уровня; не успели — на 6 касании плюс доливка."""
        if self.touch6_index is None:
            return (FigureEntry.NEAREST_LEVEL,)
        return (FigureEntry.NEAREST_LEVEL, FigureEntry.TOUCH_6, FigureEntry.ADD_ON)


def pennant(
    structure: TradingRange | OpenStructure,
    swings: SwingSet,
    senior: Priority | None = None,
) -> Pennant | NotReady:
    """Вымпел из готовой структуры накопления (стр. 57, 58).

    Сужение читается по счётчикам `BoundaryZone.narrowed`, которые ведёт сама
    `range.detect`: сошлись обе стороны — треугольник «с равными границами»
    (стр. 58), сошлась одна — «с поджатием» (стр. 57).

    ⚠⚠ СТОРОНА С 2026-08-22 БЕРЁТСЯ ИЗ ТРЕНДА, А НЕ ИЗ ПЕРВОЙ ТОЧКИ, И ЭТО ПРАВКА
    МЕТОДА. Прежняя редакция объявляла себя «у курса дословно и без замера», а делала
    обратное курсу: выводила ТРЕНД из того, какая граница дала первую точку. Это
    ОБРАЩЕНИЕ импликации, а не её исполнение. Обе страницы называют тренд ВХОДОМ
    правила: стр. 57 «Торгуем по тренду. Если тренд Лонговый - ПЕРВАЯ точка берется
    сверху», стр. 58 «Строим по тренду: если тренд лонговый, первая точка идет сверху».

    Цена прежнего чтения предъявлена, а не оценена: на 776 вымпелах с определённым
    трендом старая сторона расходилась с трендом в 296 случаях — 38.1%, и в каждом
    карточка печатала не ту сторону сделки и прятала стоп за противоположный край
    структуры. Контроль: доли сторон 48.8% и 49.6% дают при независимости 50.0%
    совпадений, старый код давал 61.9%. Связь слабая — то есть первая точка тренд НЕ
    определяет, и обращать импликацию было нельзя.

    ⚠ ЗАМЕР ВЕДЁТСЯ БОЕВЫМ ОКНОМ (`now - depth*step`), а не загрузкой хранилища целиком,
    и это не педантизм: первая редакция этих чисел (777/302/38.9%) считалась по складу, а
    у BTCUSDT 1ч там дыра в 2.3 года между обрывком 2023 года и рабочим окном. Детектор
    строил структуры СКВОЗЬ неё. Бой так не грузит никогда — см. `run.bars_of`.

    Тренд снимается `swings.trend` (стр. 12) по экстремумам, ПОДТВЕРЖДЁННЫМ не позже
    первой точки структуры: «торгуем по тренду» — про тренд ДО фигуры, который она
    продолжает, а заглядывать вперёд нельзя.

    ⚠ СВОЙ ТФ МОЛЧИТ — СПРАШИВАЕМ СТАРШИЙ (стр. 47), заведено 2026-08-22. Причина
    измерена, а не предположена: `trend` возвращает NONE именно на СХОДЯЩЕЙСЯ цепочке, а
    вымпел и есть сужение, поэтому свой ряд молчал у 584 сужений из 1362 (42.9%). Разбор
    против контроля «та же цепочка на 200 баров раньше»: сходимость 66.4% против 20.9%, и
    там же тренд ЕСТЬ у 60.8% против нуля здесь.
    То есть сходимость начинается РАНЬШЕ первой принятой точки границы, и
    свой ТФ слеп не к рынку, а к собственному сужению. Старший ТФ этим сужением не занят,
    и спросить его велит сам курс: стр. 47 «ПРИОРИТЕТ СТАРШИЙ ТАЙМФРЕЙМ». Глубина взгляда
    при этом НЕ ВЫДУМЫВАЕТСЯ — числа лукбэка нет, есть готовая лестница ТФ проекта.

    ⚠ Расхождение своего ТФ со старшим сторону НЕ переворачивает, а помечается полем
    `agreement` — см. его докстроку.

    Тренда нет НИ НА СВОЁМ, НИ НА СТАРШИХ — вымпела нет. Стр. 57 начинается словами
    «Торгуем по тренду», а правила для случая без тренда курс не даёт, и оно не
    выдумывается: отказ НАЗЫВАЕТСЯ (§4.3) и считается вызывающим.

    `senior` — приоритет старшего ТФ НА МОМЕНТ начала структуры, а не сейчас. Считать его
    обязан вызывающий: этому модулю чужие ряды не видны, а «тренд сейчас» на старшем ТФ
    был бы заглядыванием вперёд для всякого вымпела, закрывшегося в прошлом.
    """
    up, low = structure.upper, structure.lower
    if not up.narrowed and not low.narrowed:
        return NotReady(
            reason="накопление без сужения: вымпел — это структура накопления в сужении (стр. 57)"
        )
    borders = (PennantBorders.EQUAL if up.narrowed and low.narrowed
               else PennantBorders.SQUEEZED)
    prior = tuple(s for s in swings.swings
                  if s.confirmed_at_index <= structure.first_index)
    direction = trend(SwingSet(
        swings=prior,
        bars_scanned=swings.bars_scanned,
        confirmed_until_index=structure.first_index,
    )).direction
    source = OWN_TF_SOURCE
    if direction is TrendDirection.NONE and senior is not None:
        direction = senior.direction
        source = senior.timeframe or OWN_TF_SOURCE
    if direction is TrendDirection.NONE:
        return NotReady(reason=NO_TREND_REASON)
    side = FigureSide.LONG if direction is TrendDirection.UP else FigureSide.SHORT
    # Стр. 58: «первая точка идет сверху» у лонга — значит счёт касаний начинается с
    # точки СТОРОНЫ ТРЕНДА, а пришедшая раньше точка другой стороны номера не получает.
    # Схема стр. 57 нумерует 1, 3, 5, 7 по одной границе и 2, 4, 6, 8 по другой, то есть
    # счёт чередуется строго от первой. То же прочтение той же фразы держит и
    # `_build_channel` для флага с клином — правило одно, и читается одинаково.
    head = up.point_indices if side is FigureSide.LONG else low.point_indices
    order = sorted(up.point_indices + low.point_indices)
    if order and order[0] not in head:
        order = order[1:]
    return Pennant(
        borders=borders,
        side=side,
        first_index=order[0],
        last_point_index=order[-1],
        touches=len(order),
        touch6_index=order[TOUCH_FOR_ENTRY - 1] if len(order) >= TOUCH_FOR_ENTRY else None,
        stop_anchor=(structure.extended_lo if side is FigureSide.LONG
                     else structure.extended_hi),
        upper_edge=up.edge,
        lower_edge=low.edge,
        is_extended=structure.is_extended,
        is_open=isinstance(structure, OpenStructure),
        side_source=source,
        agreement=(agreement_of(direction, senior) if senior is not None
                   else Agreement.NO_PRIORITY),
    )


class HeadShoulders(BaseModel):
    """Голова и плечи, стр. 61 — признак НА переприоре, а не отдельная фигура.

    Курс сам говорит: «фактически частный случай "Переприора - слома структуры"». Линия
    шеи здесь — уровень ПП, то есть вся зона тени сломанной свечи (стр. 55), поэтому
    своей цены у неё нет: она берётся у `Pereprior`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    side: FigureSide
    left_index: int
    head_index: int
    right_index: int
    """Левое плечо, голова, правое плечо — три экстремума одной стороны до слома."""

    head_price: float
    neckline_lo: float
    neckline_hi: float
    pp_kind: PPKind
    confirmed_at_index: int
    tested_at_index: int | None
    """Бар теста уровня ПП — стр. 61: «Пробой уровня, закрепление, тест - и выход по
    новому тренду». `None` — теста ещё не было, вход по стр. 61 пока не наступил."""

    @property
    def entries(self) -> tuple[FigureEntry, ...]:
        """Стр. 61: сначала от уровня, а «если не успели взять позицию от уровня» — на тесте ПП."""
        return (FigureEntry.NEAREST_LEVEL, FigureEntry.PP_TEST)


def head_and_shoulders(pp: Pereprior, swings: SwingSet) -> HeadShoulders | NotReady:
    """Признак ГИП на уже найденном переприоре (стр. 61).

    Проверяется ровно то, что делает ПП «головой и плечами»: три последних экстремума
    ТОЙ ЖЕ стороны до подтверждения слома стоят как плечо-голова-плечо, то есть средний
    выделяется над обоими соседними. Порога «насколько выделяется» курс не даёт, и он не
    выдумывается: сравнение строгое, как у фрактала в `swings.detect`.

    Экстремумы берутся только подтверждённые не позже слома — заглядывать вперёд нельзя.
    """
    kind = SwingKind.HIGH if pp.side is PPSide.SHORT else SwingKind.LOW
    # ⚠ ОТБОР ДВОИЧНЫМ ПОИСКОМ С 2026-08-21 — ТОТ ЖЕ ОТВЕТ, ДРУГАЯ ЦЕНА. Прежде здесь
    # стояло `[s for s in sorted(swings.of(kind), key=lambda x: x.index)
    #           if s.confirmed_at_index <= pp.confirmed_at_index]`,
    # то есть на КАЖДОМ переприоре заново собирался, сортировался и фильтровался весь
    # список экстремумов ряда. Замер BTC на боевых глубинах (`cProfile`, 178 с на
    # символ): 3208 вызовов этой функции стоили 33.0 с — 20% всего расчёта, из них
    # 8 181 291 вызов генератора `of()` и столько же вызовов ключа сортировки.
    #
    # Почему ответ ТОТ ЖЕ, а не «почти тот же»:
    #   `of(kind)` отдаёт экстремумы в порядке появления, и порядок этот — проверяемое
    #   условие `SwingSet._ordered`, а не совпадение. Значит `sorted` по `index` был
    #   перестановкой отсортированного, то есть тождеством;
    #   `confirmed_of(kind)` неубывающий (то же условие), значит `bisect_right` даёт
    #   РОВНО число элементов с `confirmed_at_index <= порога` — тот же префикс, что
    #   отбирал фильтр. Дальше берутся последние три этого префикса, как и раньше.
    # КОНТРОЛЬ В ОБЕ СТОРОНЫ, боевые ряды BTC (6 ТФ, 51840 баров 5м, 3208 переприоров):
    #   прежняя формула против новой — разошлось 0 из 3208;
    #   тот же прибор с подсаженным нарушением (порог сдвинут на −5 баров) — разошлось
    #     2584 из 3208; со сдвигом на −50 — 3208 из 3208.
    # Второй пункт и есть проверка «мог ли прибор ответить иначе», и он же вскрыл
    # РАЗРЕШЕНИЕ прибора: сдвиг на −1 бар даёт 0 расхождений, потому что метки
    # подтверждения экстремума и переприора в этих данных не совпадают вплотную. Значит
    # «разошлось 0» означает «нет расхождений крупнее одного бара», а не «нет никаких», —
    # и заявлять больше этого нельзя.
    ordered = swings.of(kind)
    cut = bisect_right(swings.confirmed_of(kind), pp.confirmed_at_index)
    seq = ordered[:cut]
    if len(seq) < 3:
        return NotReady(
            reason=f"до слома подтверждено {len(seq)} экстремумов нужной стороны, "
                   "на плечо-голову-плечо нужно три (стр. 61)"
        )
    left, head, right = seq[-3], seq[-2], seq[-1]
    if pp.side is PPSide.SHORT:
        shaped = head.price > left.price and head.price > right.price
    else:
        shaped = head.price < left.price and head.price < right.price
    if not shaped:
        return NotReady(
            reason="средний из трёх экстремумов не выделяется над соседними — переприор есть, "
                   "фигуры ГИП нет (стр. 61)"
        )
    return HeadShoulders(
        side=FigureSide.SHORT if pp.side is PPSide.SHORT else FigureSide.LONG,
        left_index=left.index,
        head_index=head.index,
        right_index=right.index,
        head_price=head.price,
        neckline_lo=pp.zone_lo,
        neckline_hi=pp.zone_hi,
        pp_kind=pp.kind,
        confirmed_at_index=pp.confirmed_at_index,
        tested_at_index=pp.tested_at_index,
    )


class MultipleBase(BaseModel):
    """Двойное/тройное дно и вершина, стр. 62 — признак на накоплении.

    Курс сводит фигуру к известному: «это просто накопление, но граница накопления
    являлась сломом структуры». Отдельного детектора поэтому нет, есть совпадение двух
    уже построенных вещей — границы накопления и зоны переприора.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    side: FigureSide
    base_touches: int
    """Касаний ПРОТИВОПОЛОЖНОЙ границы — это и есть число доньев/вершин фигуры."""

    boundary_edge: float
    """Граница накопления, оказавшаяся уровнем ПП, — то, что курс зовёт пробитым уровнем."""

    pp_zone_lo: float
    pp_zone_hi: float
    pp_kind: PPKind
    pp_tested_at_index: int | None
    level_side_edge: float
    """Противоположная граница — «от Лонг уровня всего накопления» (стр. 62), второй закуп."""

    @property
    def course_name(self) -> str | None:
        """Как фигуру называет курс. `None` — касаний не два и не три, а больше: стр. 62
        такую не именует, и своего имени ей здесь не выдумывается."""
        if self.side is FigureSide.LONG:
            return {2: "двойное дно", 3: "тройное дно"}.get(self.base_touches)
        return {2: "двойная вершина", 3: "тройная вершина"}.get(self.base_touches)

    @property
    def entries(self) -> tuple[FigureEntry, ...]:
        """Стр. 62: «в несколько закупов» — от теста ПП И от уровня всего накопления."""
        return (FigureEntry.PP_TEST, FigureEntry.NEAREST_LEVEL)


def multiple_base(acc: TradingRange, pps: Sequence[Pereprior]) -> MultipleBase | NotReady:
    """Признак двойного/тройного дна или вершины на закрытом накоплении (стр. 62).

    Условие ровно одно и оно из курса: граница, ЧЕРЕЗ которую цена вышла, лежит внутри
    зоны переприора — «граница накопления являлась сломом структуры». Зона берётся вся
    (стр. 55: уровень ПП — вся тень свечи), поэтому допуска здесь не вводится.

    Переприор обязан быть подтверждён не раньше выхода: выход из структуры и есть тот
    слом, о котором говорит стр. 62. Из подходящих берётся самый ранний — ближайший к
    выходу; порядок детерминирован и от порядка аргумента не зависит.
    """
    if acc.is_long:
        want, edge, base_touches = PPSide.LONG, acc.upper.edge, acc.lower.touches
        side, level_edge = FigureSide.LONG, acc.lower.edge
    else:
        want, edge, base_touches = PPSide.SHORT, acc.lower.edge, acc.upper.touches
        side, level_edge = FigureSide.SHORT, acc.upper.edge
    fits = [p for p in pps
            if p.side is want
            and p.zone_lo <= edge <= p.zone_hi
            and p.confirmed_at_index >= acc.exit.confirmed_at_index]
    if not fits:
        return NotReady(
            reason="граница выхода не совпала ни с одной зоной ПП — накопление есть, "
                   "двойного/тройного дна по стр. 62 нет"
        )
    pp = min(fits, key=lambda p: p.confirmed_at_index)
    return MultipleBase(
        side=side,
        base_touches=base_touches,
        boundary_edge=edge,
        pp_zone_lo=pp.zone_lo,
        pp_zone_hi=pp.zone_hi,
        pp_kind=pp.kind,
        pp_tested_at_index=pp.tested_at_index,
        level_side_edge=level_edge,
    )
