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
  стр. 68  Боллинджер: «используем, чтобы понять как скоро будет выход цены из
           накопления. Чем сильнее сузились эти линии — тем быстрее будет выход»
  стр. 69  MA/EMA 200: «если местоположение этих скользящих или одной из них совпадает
           с нашей точкой входа/выхода, то для нас это дополнительный фактор»
           ⚠ «или одной из них» — не украшение: хватает ОДНОЙ скользящей. Прежняя
           редакция этой цитаты слова роняла и тем ужесточала правило автора.

Каждый фактор отдаётся ОТДЕЛЬНО и вместе с числом, по которому его посчитали.
"""

from __future__ import annotations

import math
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
        `distance_pct = nan`. Карточка владельца печатала «EMA200 в nan% от цены»,
        то есть ЧИСЛО БЕЗ РЕФЕРЕНТА в тексте, который читает человек (§0, §4.3);
      * `squeeze` — клал NaN в список ширин, а дальше `w <= last` с NaN всегда ложно,
        и перцентиль считался по испорченной выборке МОЛЧА;
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


class SqueezeFactor(BaseModel):
    """Сужение полос Боллинджера (стр. 68). Предиктор ВЫХОДА, не сигнал входа."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    width_pct: float
    """Ширина полос в процентах от средней линии на последнем баре."""

    percentile: float
    """Место этой ширины среди ширин собственной истории символа, в процентах.

    §4.1: порог относительный — «перцентиль собственной истории». Абсолютного «узко»
    здесь нет и быть не может: у каждого инструмента своя волатильность.
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
    """MA/EMA 200 рядом с точкой входа (стр. 69)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ma_value: float
    entry: float
    distance_pct: float


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


def squeeze(upper: list[float | None], lower: list[float | None],
            middle: list[float | None]) -> SqueezeFactor | None:
    """Сужение полос на последнем баре и его место в собственной истории (стр. 68).

    `None` — ширину на последнем баре посчитать не из чего. Это не «широко».
    """
    widths: list[float] = []
    for u, low, m in zip(upper, lower, middle, strict=True):
        if _absent(u) or _absent(low) or _absent(m) or m == 0:
            continue
        assert u is not None and low is not None and m is not None
        widths.append((u - low) / m * 100)
    if not widths:
        return None
    last = widths[-1]
    return SqueezeFactor(
        width_pct=last,
        percentile=sum(1 for w in widths if w <= last) / len(widths) * 100,
    )


def ma_touch(ma: list[float | None], entry: float) -> MaTouchFactor | None:
    """MA200 против точки входа (стр. 69). `None` — MA ещё не определена.

    Не определена — это и `None`, и `NaN`: см. `_absent`. Раньше проверялся только
    `None`, и карточка печатала «EMA200 в nan% от цены».
    """
    last = ma[-1] if ma else None
    if _absent(last) or entry == 0:
        return None
    assert last is not None
    return MaTouchFactor(ma_value=last, entry=entry,
                         distance_pct=abs(last - entry) / entry * 100)
