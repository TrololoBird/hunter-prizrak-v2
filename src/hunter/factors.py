"""Индикаторы как ДОП-ФАКТОРЫ §2.9. Источник — мини-курс, стр. 64-69.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Курс повторяет на каждом слайде: «индикатор используется ТОЛЬКО как дополнительный
фактор к нашей точке входа» (стр. 64, 65, 66). Поэтому здесь нет и не может быть:
сигнала, гейта, балла, веса и любой свёртки нескольких факторов в одно число — это
запрещено §3 и §4.2 и противоречит самому курсу.

  стр. 65  «Дивергенция (классическая, основная) – хая на графике повышаются, а на
           индикаторе, наоборот, затухают… лои на графике понижаются, а на индикаторе,
           наоборот, лои повышаются» (второе — про конвергенцию; «хая» — опечатка автора,
           сохранена: правленая цитата перестаёт быть проверяемой)
  стр. 67  «RSI можно использовать, что бы найти наличие дивергенций/конвергенций.
           Также можно смотреть трендовые линии»
  стр. 68  Боллинджер: «используем, чтобы понять как скоро будет выход цены из
           накопления. Чем сильнее сузились эти линии — тем быстрее будет выход»
           ⚠ Фактор зовётся `band_narrowing`, а НЕ «сквиз»: сквиз — термин стр. 8 про
           резкое движение с каскадом ликвидаций, полосы к нему отношения не имеют.
  стр. 69  MA/EMA 200: «если местоположение этих скользящих или одной из них совпадает
           с нашей точкой входа/выхода, то для нас это дополнительный фактор»
           ⚠ «или одной из них» — не украшение: хватает ОДНОЙ скользящей. Прежняя
           редакция этой цитаты слова роняла и тем ужесточала правило автора.

Каждый фактор отдаётся ОТДЕЛЬНО и вместе с числом, по которому его посчитали.
"""

from __future__ import annotations

import math
import statistics
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .models import Bar
from .swings import SwingKind, SwingSet


def _absent(x: float | None) -> bool:
    """Величина не определена. ⚠ NaN — ТОЖЕ отсутствие, и это не педантизм.

    Замер 2026-08-06: `polars_talib.ema(200)` на ряду из 150 баров отдаёт **150 NaN и ноль
    None**. Все три функции ниже проверяли только `is None`, поэтому NaN проезжал насквозь,
    и это давало разный вред в каждой:

      * `ma_touch` — считал `abs(nan - entry) / entry * 100` и отдавал фактор с
        расстоянием `nan`. Карточка владельца печатала «EMA200 в nan% от цены»,
        то есть ЧИСЛО БЕЗ РЕФЕРЕНТА в тексте, который читает человек (§0, §4.3);
      * `band_narrowing` (тогда `squeeze`) — клал NaN в список ширин, а `w <= last` с
        NaN всегда ложно, и перцентиль считался по испорченной выборке МОЛЧА;
      * `divergences` — сравнения с NaN всегда ложны, то есть отсутствие индикатора
        выглядело как «дивергенции нет». Отсутствие, поданное как ответ (§4.3).

    Ни один из трёх случаев ничего не ронял и ни во что не логировался.
    """
    return x is None or math.isnan(x)


class DivergenceKind(StrEnum):
    DIVERGENCE = "divergence"
    """Хаи цены растут, хаи индикатора падают — «признак угасания силы тренда» (стр. 65)."""

    CONVERGENCE = "convergence"
    """Лои цены падают, лои индикатора растут — «признак усиления откупа» (стр. 65)."""

    HIDDEN_BEARISH = "hidden_bearish"
    """СКРЫТАЯ медвежья: хаи цены ПАДАЮТ, хаи индикатора РАСТУТ.

    ⚠ Заведено 2026-08-07, находка М-16. Схема на стр. 65 подписана НА САМОМ РИСУНКЕ
    тремя семействами: классическая, СКРЫТЫЕ, РАСШИРЕННЫЕ — по два случая в каждом.
    Слов "скрыт" и "расширенн" нет в текстовом слое НИ ОДНОЙ из 69 страниц, поэтому
    правило было невидимо для всякого, кто читал курс текстом.

    Геометрия взята С РИСУНКА и зеркальна классической: там, где классическая требует
    расхождения на новом экстремуме, скрытая требует его на НЕ обновлённом.
    """

    HIDDEN_BULLISH = "hidden_bullish"
    """СКРЫТАЯ бычья: лои цены РАСТУТ, лои индикатора ПАДАЮТ (стр. 65, рисунок)."""

    EXTENDED_BEARISH = "extended_bearish"
    """РАСШИРЕННАЯ медвежья: хаи цены РАВНЫ (двойная вершина), хаи индикатора падают.

    ⚠ Заведено 2026-08-09. Третье семейство схемы стр. 65 (М-16); «равные» получили
    допуск из классики — см. `EXTENDED_EQUAL_TOL_PCT`.
    """

    EXTENDED_BULLISH = "extended_bullish"
    """РАСШИРЕННАЯ бычья: лои цены РАВНЫ (двойное дно), лои индикатора растут (стр. 65)."""


EXTENDED_EQUAL_TOL_PCT = 3.0
"""Допуск «равенства» экстремумов цены для расширенных дивергенций, % от цены.

Курс числа не даёт (стр. 65 рисует равные вершины/донья без допуска). По §0.1 молчание
курса заполняет классика; решение владельца 2026-08-09 («решения принимай сам по
источникам»), разбор — docs/audit/classics-tolerance-2026-08-09.md (11 источников):
Булковски, Encyclopedia of Chart Patterns (Adam & Adam Double Top): «variation between
price peaks is small, usually less than 3%» — то же 3% у него граница «одного уровня»
при разграничении triple top; индустриальный default открытых детекторов — 2%
(подмножество трёх процентов). Взята граница Булковски как единственная книжная.
Эдвардс-Маги и Шабакер числа не дают. Значение вынесено константой, чтобы
чувствительность к нему замерялась, а не предполагалась.
"""


class Divergence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DivergenceKind
    indicator: str
    first_index: int
    second_index: int
    price_first: float
    price_second: float
    indicator_first: float
    indicator_second: float


class BandNarrowingFactor(BaseModel):
    """Сужение полос Боллинджера (стр. 68). Предиктор ВЫХОДА, не сигнал входа.

    ⚠ Класс назывался `SqueezeFactor` до 2026-08-19, и это имя было ЗАНЯТИЕМ ЧУЖОГО
    ТЕРМИНА. «Сквиз» курс определяет на стр. 8 как совсем другое явление — «резкий
    рост/падение цены сразу на несколько % … Сопровождается ликвидациями позиций» с
    каскадом ликвидаций, — и на стр. 33 стоп ставится с запасом именно «от сквизов на
    рынке». Полосы Боллинджера (стр. 68) к ликвидациям отношения не имеют. Одно
    человеческое имя на две сущности — тот самый дефект, из-за которого 51.7% зон
    вылезали за границы: одна из сущностей всегда останется непроверенной.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    width_pct: float
    """Ширина полос в процентах от средней линии НА ПОСЛЕДНЕМ баре.

    ⚠ Именно на последнем: до 2026-08-19 недостающие значения выбрасывались из ряда, и
    `widths[-1]` мог оказаться шириной другого, более раннего бара — а печаталась она
    как текущая. Теперь ряд остаётся выровненным по барам, и если ширины последнего
    бара нет, фактор не выдаётся вовсе.
    """

    percentile: float
    """Место этой ширины среди ширин собственной истории символа, в процентах.

    §4.1: порог относительный — «перцентиль собственной истории». Абсолютного «узко»
    здесь нет и быть не может: у каждого инструмента своя волатильность.
    """

    exit_bars_median: float | None
    """«КАК СКОРО будет выход» (стр. 68) — В БАРАХ, а не словом.

    Курс требует именно этой величины: «Мы их используем, чтобы понять как скоро будет
    выход цены из накопления. Чем сильнее сузились эти линии - тем быстрее будет выход».
    Числа курс не даёт, и классика не даёт тоже: StockCharts ChartSchool, Bollinger Band
    Squeeze — сужение опознаётся как "BandWidth near the low end of its six-month range",
    выход как "Price breaks above the upper band or below the lower band", а СРОК не
    назван нигде («often followed by» без числа). Придумать срок нельзя (§0).

    Поэтому срок здесь не постулируется, а ЗАМЕРЯЕТСЯ по собственной истории символа:
    медиана числа баров от бара, чья ширина не больше текущей, до первого закрытия за
    полосой. Оба условия взяты у источников, а не выбраны: «не больше текущей» — это
    ординальное правило курса («чем сильнее сузились»), «закрытие за полосой» — выход по
    Боллинджеру. `None` — прошлых случаев не нашлось; знаменатель в `exit_cases`.
    """

    exit_cases: int
    """ЗНАМЕНАТЕЛЬ медианы: сколько прошлых случаев её образовали.

    Медиана по трём случаям и медиана по тремстам — разные утверждения, и без этого
    числа их не различить. Ноль означает, что срок не замерен, а не что выхода не будет.
    """


RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
"""Уровни RSI с настроек автора (скриншот стр. 64: пунктиры 70/50/30) и из классики —
Уайлдер, New Concepts in Technical Trading Systems, 1978: 70/30 как зоны
перекупленности/перепроданности. Середина 50 фактором не является: «RSI около 50»
требует допуска близости, которого нет ни у курса, ни у классики. Заведено 2026-08-09
(реестр долга, строка 8): уровни были сняты со скриншота проходом курса и нигде не
читались — RSI участвовал только в дивергенциях."""


class RsiZoneFactor(BaseModel):
    """RSI за уровнем 70/30. Доп-фактор §2.9 — сопровождает, не гейтит.

    ⚠ Числа 70/30 — НЕ из курса: сверка 2026-08-17 подтвердила, что числовых порогов RSI
    в курсе нет нигде (пунктир на скриншотах стр. 64/67 не подписан). Референт чисел —
    классика (Уайлдер 1978, см. шапку модуля; DEFINITIONS.md, RSI). Курс даёт сам
    индикатор и его роль: стр. 64 «индикатор используется только как дополнительный
    фактор к нашей точке входа». Протокол: docs/audit/indicators-course-2026-08-17.md"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    overbought: bool
    """True — выше 70, перекупленность; False — ниже 30, перепроданность."""


def rsi_zone(value: float | None) -> RsiZoneFactor | None:
    """None — RSI в середине диапазона либо не посчитан; фактора нет."""
    if value is None or math.isnan(value):
        return None
    if value >= RSI_OVERBOUGHT:
        return RsiZoneFactor(value=value, overbought=True)
    if value <= RSI_OVERSOLD:
        return RsiZoneFactor(value=value, overbought=False)
    return None


class MaTouchFactor(BaseModel):
    """MA/EMA 200 рядом с ТОЧКОЙ ВХОДА ИЛИ ТОЧКОЙ ВЫХОДА (стр. 69).

    ⚠ Правка 2026-08-19, и она про РАСЧЁТ, а не про имена. Курс: «если местоположение
    этих скользящих или одной из них совпадает с нашей точкой входа/выхода, то для нас
    это дополнительный фактор». Сравнивалась же скользящая с `close` последнего бара —
    то есть с текущей ценой, которая не есть ни ТВХ, ни цель. ТВХ курс определяет
    отдельным термином (стр. 4: «ТВХ – Точка Входа в позицию – то есть цена открытия
    сделки»), и ПОК-вход стоит лимиткой в стороне от текущей цены (стр. 30) — значит
    прежняя мера отвечала на вопрос, которого курс не задавал.

    Порога «совпадает» здесь НЕТ намеренно: числа для него не даёт ни курс, ни классика,
    а придуманный порог был бы фильтром поверх непроверенного входа. Печатаются
    расстояния, решение остаётся человеку.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ma_value: float
    entry: float
    target: float | None
    """Цель сделки — «точка выхода» стр. 69. `None` — цели у сетапа нет."""

    distance_entry_pct: float
    distance_target_pct: float | None
    nearest_pct: float
    """Ближайшее из двух расстояний: курс требует совпадения с ЛЮБОЙ из двух точек."""


class TrendlineKind(StrEnum):
    SUPPORT_BROKEN = "support_broken"
    """Восходящая трендовая по лоям индикатора пробита ВНИЗ (стр. 67, случай автора)."""

    RESISTANCE_BROKEN = "resistance_broken"
    """Нисходящая трендовая по хаям индикатора пробита ВВЕРХ — зеркальный случай."""


class TrendlineBreak(BaseModel):
    """Пробой трендовой линии, построенной ПО САМОМУ ИНДИКАТОРУ (стр. 67).

    Курс дословно: «RSI можно использовать, что бы найти наличие
    дивергенций/конвергенций. Также можно смотреть трендовые линии» — и дальше разбирает
    случай: «Актив формировал структуру с приоритетом выхода вверх, но по RSI был пробой
    трендовой, пробой случился заранее еще в структуре». Корпус подтверждает по рисунку:
    трендовая нарисована на панели RSI, не на цене (research/prizrak_corpus/course_notes/
    notes_p52-69.md, стр. 67).

    КАК строится линия, курс не говорит — это добирается из классики (§0.1):
      StockCharts ChartSchool, Trend Lines: "It takes two points to draw a trend line,
      and the third one confirms the validity"; "An uptrend line has a positive slope
      and is formed by connecting two or more low points"; "A break below the uptrend
      line indicates that net demand has weakened".
    Две точки — минимум по источнику, и именно он здесь взят: третьего касания курс не
    требует, а требовать его значило бы ужесточить правило автора.

    Экстремумы индикатора ищутся пятибарным фракталом (`INDICATOR_FRACTAL_BARS`) — это
    СВОЙ объект на своём ряду, а не свинг цены: с 2026-08-20 цену размечает зигзаг с
    окном `swings.ZIGZAG_DEPTH`, и довод в пользу его ширины (видно ли СТРУКТУРУ на
    своём ТФ, стр. 23) к ряду RSI не относится.

    Про «закрытие за линией» здесь нет отдельного условия и не может быть: RSI считается
    ПО ЗАКРЫТИЮ, у него нет тени, и значение бара — уже закрытие.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TrendlineKind
    indicator: str
    first_index: int
    second_index: int
    first_value: float
    second_value: float
    line_value: float
    """Куда линия пришла на последнем баре."""

    last_value: float
    """Значение индикатора на последнем баре — оно и оказалось за линией."""


def divergences(
    bars: list[Bar], swings: SwingSet, indicator: list[float | None], name: str
) -> tuple[Divergence, ...]:
    """Дивергенции и конвергенции по стр. 65 — на ПОСЛЕДНЕЙ паре экстремумов.

    Сравниваются два последних свинга одной стороны: курс говорит «хаи повышаются, а на
    индикаторе затухают», то есть речь о соседних экстремумах, а не о произвольной паре
    из истории. Значение индикатора берётся на баре экстремума; если его нет (`None`
    ЛИБО `NaN` — см. `_absent`), фактор НЕ выдаётся: подставлять число нельзя (§4.3), а
    сравнивать с NaN — значит молча получить «дивергенции нет».
    """
    # Три семейства стр. 65 (М-16). Расширенные добавлены 2026-08-09: допуск равенства
    # получил референт из классики (`EXTENDED_EQUAL_TOL_PCT`). В ПОЛОСЕ равенства
    # приоритет у расширенной: пара «хаи отличаются на 1%, индикатор ниже» — это
    # двойная вершина по Булковски, а не «хаи растут», и выдавать обе значило бы
    # посчитать одно наблюдение дважды.
    ON_HIGHS = (DivergenceKind.DIVERGENCE, DivergenceKind.HIDDEN_BEARISH,
                DivergenceKind.EXTENDED_BEARISH)
    out: list[Divergence] = []
    for kind in (DivergenceKind.DIVERGENCE, DivergenceKind.CONVERGENCE,
                 DivergenceKind.HIDDEN_BEARISH, DivergenceKind.HIDDEN_BULLISH,
                 DivergenceKind.EXTENDED_BEARISH, DivergenceKind.EXTENDED_BULLISH):
        sk = SwingKind.HIGH if kind in ON_HIGHS else SwingKind.LOW
        seq = sorted(swings.of(sk), key=lambda s: s.index)
        if len(seq) < 2:
            continue
        prev, last = seq[-2], seq[-1]
        if last.index >= len(indicator) or prev.index >= len(indicator):
            continue
        i_prev, i_last = indicator[prev.index], indicator[last.index]
        if _absent(i_prev) or _absent(i_last):
            continue
        assert i_prev is not None and i_last is not None
        equal = abs(last.price - prev.price) / prev.price * 100 < EXTENDED_EQUAL_TOL_PCT
        if kind is DivergenceKind.DIVERGENCE:
            hit = not equal and last.price > prev.price and i_last < i_prev
        elif kind is DivergenceKind.CONVERGENCE:
            hit = not equal and last.price < prev.price and i_last > i_prev
        elif kind is DivergenceKind.HIDDEN_BEARISH:
            hit = not equal and last.price < prev.price and i_last > i_prev
        elif kind is DivergenceKind.HIDDEN_BULLISH:
            hit = not equal and last.price > prev.price and i_last < i_prev
        elif kind is DivergenceKind.EXTENDED_BEARISH:
            hit = equal and i_last < i_prev
        else:
            hit = equal and i_last > i_prev
        if not hit:
            continue
        out.append(Divergence(
            kind=kind, indicator=name,
            first_index=prev.index, second_index=last.index,
            price_first=prev.price, price_second=last.price,
            indicator_first=i_prev, indicator_second=i_last,
        ))
    return tuple(out)


def band_narrowing(upper: list[float | None], lower: list[float | None],
                   middle: list[float | None],
                   close: list[float]) -> BandNarrowingFactor | None:
    """Сужение полос на последнем баре, его место в истории и СРОК выхода (стр. 68).

    `None` — ширину НА ПОСЛЕДНЕМ баре посчитать не из чего. Это не «широко».

    Срок считается так: бар-случай — тот, чья ширина не больше текущей и чей `close`
    внутри полос (бар, уже вышедший за полосу, — не накопление); ожидание — расстояние
    в барах до первого следующего закрытия за полосой. Медиана этих ожиданий и есть
    ответ на «как скоро», а `exit_cases` — сколько их было.
    """
    widths: list[float | None] = []
    for u, low, m in zip(upper, lower, middle, strict=True):
        if _absent(u) or _absent(low) or _absent(m) or m == 0:
            widths.append(None)
            continue
        assert u is not None and low is not None and m is not None
        widths.append((u - low) / m * 100)
    last = widths[-1] if widths else None
    if last is None:
        return None
    known = [w for w in widths if w is not None]

    n = len(widths)
    # Выход по Боллинджеру — закрытие ЗА полосой. Ближайший такой бар справа ищется
    # одним проходом назад: иначе на каждый случай пришлось бы сканировать хвост, и
    # цена вопроса стала бы квадратичной по длине ряда.
    outside: list[bool] = []
    for i in range(n):
        u, low = upper[i], lower[i]
        if _absent(u) or _absent(low):
            outside.append(False)
            continue
        assert u is not None and low is not None
        outside.append(close[i] > u or close[i] < low)
    next_out: list[int | None] = [None] * n
    nxt: int | None = None
    for i in range(n - 1, -1, -1):
        if outside[i]:
            nxt = i
        next_out[i] = nxt
    waits: list[int] = []
    for i in range(n - 1):
        w, out_at = widths[i], next_out[i]
        if w is None or outside[i] or w > last or out_at is None:
            continue
        waits.append(out_at - i)

    return BandNarrowingFactor(
        width_pct=last,
        percentile=sum(1 for w in known if w <= last) / len(known) * 100,
        exit_bars_median=statistics.median(waits) if waits else None,
        exit_cases=len(waits),
    )


def ma_touch(ma: list[float | None], entry: float,
             target: float | None) -> MaTouchFactor | None:
    """MA200 против ТВХ и цели (стр. 69). `None` — MA ещё не определена.

    Не определена — это и `None`, и `NaN`: см. `_absent`. Раньше проверялся только
    `None`, и карточка печатала «EMA200 в nan% от цены».

    ⚠ Второй аргумент — ЦЕНА ВХОДА, а не последняя цена: вызов с `bars[-1].close`
    отвечает не на тот вопрос (см. докстроку `MaTouchFactor`).
    """
    last = ma[-1] if ma else None
    if _absent(last) or entry == 0:
        return None
    assert last is not None
    d_entry = abs(last - entry) / entry * 100
    d_target = None if target is None or target == 0 else abs(last - target) / target * 100
    return MaTouchFactor(
        ma_value=last, entry=entry, target=target,
        distance_entry_pct=d_entry, distance_target_pct=d_target,
        nearest_pct=d_entry if d_target is None else min(d_entry, d_target),
    )


INDICATOR_FRACTAL_BARS = 5
"""Окно экстремума НА РЯДУ ИНДИКАТОРА: пять баров, экстремум на среднем.

⚠ Это НЕ окно зигзага цены (`swings.ZIGZAG_DEPTH`), и с 2026-08-20 числа разошлись
намеренно. Зигзаг размечает СТРУКТУРУ — там ширина окна отвечает за то, видно ли
структуру на своём ТФ (стр. 23), и владелец сменил примитив ради этого. Здесь же
экстремум ищется на RSI ради трендовой линии по стр. 67: это другой объект на другом
ряду, и переносить на него ширину структурного окна значило бы взять чужой довод.

Пятёрка осталась той же, что была у обоих до разделения, — три источника в шапке
`swings.py` (MetaTrader, thinkorswim, TradingView).
"""


def _fractal_pivots(values: list[float | None], lows: bool) -> list[int]:
    """Индексы фрактальных экстремумов РЯДА ИНДИКАТОРА.

    Пять баров, экстремум на среднем, соседи строго слабее — `INDICATOR_FRACTAL_BARS`.
    Бар с неизвестным значением в окне экстремума не даёт: сравнивать с NaN значит молча
    получить «экстремума нет» (`_absent`).
    """
    wing = INDICATOR_FRACTAL_BARS // 2
    out: list[int] = []
    for i in range(wing, len(values) - wing):
        window = values[i - wing:i + wing + 1]
        if any(_absent(v) for v in window):
            continue
        mid = values[i]
        assert mid is not None
        side = [v for j, v in enumerate(window) if j != wing]
        if all(v is not None and (v > mid if lows else v < mid) for v in side):
            out.append(i)
    return out


def trendline_breaks(values: list[float | None], name: str) -> tuple[TrendlineBreak, ...]:
    """Пробой трендовой по самому индикатору на ПОСЛЕДНЕМ баре (стр. 67).

    Линия строится по двум последним фрактальным экстремумам одной стороны и требует
    наклона в свою сторону: восходящая поддержка — лои растут, нисходящее сопротивление
    — хаи падают. Линия, которую значения индикатора уже пересекали МЕЖДУ её точками,
    не считается линией: классика строит трендовую так, чтобы ряд держался с одной её
    стороны, — иначе «пробой» на последнем баре был бы не первым.
    """
    out: list[TrendlineBreak] = []
    if len(values) < INDICATOR_FRACTAL_BARS + 1:
        return ()
    last = values[-1]
    if _absent(last):
        return ()
    assert last is not None
    n = len(values)
    for kind in (TrendlineKind.SUPPORT_BROKEN, TrendlineKind.RESISTANCE_BROKEN):
        on_lows = kind is TrendlineKind.SUPPORT_BROKEN
        piv = _fractal_pivots(values, lows=on_lows)
        if len(piv) < 2:
            continue
        i1, i2 = piv[-2], piv[-1]
        v1, v2 = values[i1], values[i2]
        assert v1 is not None and v2 is not None
        if i2 <= i1 or i2 >= n - 1:
            continue
        if (v2 <= v1) if on_lows else (v2 >= v1):
            continue
        slope = (v2 - v1) / (i2 - i1)

        def line(i: int, *, _v1: float = v1, _i1: int = i1, _s: float = slope) -> float:
            return _v1 + _s * (i - _i1)

        pierced = False
        for j in range(i1 + 1, i2):
            v = values[j]
            if v is None or math.isnan(v):
                continue
            if (v < line(j)) if on_lows else (v > line(j)):
                pierced = True
                break
        if pierced:
            continue
        here = line(n - 1)
        if (last < here) if on_lows else (last > here):
            out.append(TrendlineBreak(
                kind=kind, indicator=name, first_index=i1, second_index=i2,
                first_value=v1, second_value=v2, line_value=here, last_value=last,
            ))
    return tuple(out)
