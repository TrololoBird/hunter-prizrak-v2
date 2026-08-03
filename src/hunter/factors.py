"""Индикаторы как ДОП-ФАКТОРЫ §2.9. Источник — мини-курс, стр. 64-69.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Курс повторяет на каждом слайде: «индикатор используется ТОЛЬКО как дополнительный
фактор к нашей точке входа» (стр. 64, 65, 66). Поэтому здесь нет и не может быть:
сигнала, гейта, балла, веса и любой свёртки нескольких факторов в одно число — это
запрещено §3 и §4.2 и противоречит самому курсу.

  стр. 65  «Дивергенция — хаи на графике повышаются, а на индикаторе, наоборот,
           затухают… Конвергенция — лои на графике понижаются, а на индикаторе,
           наоборот, лои повышаются»
  стр. 68  Боллинджер: «используем, чтобы понять как скоро будет выход цены из
           накопления. Чем сильнее сузились эти линии — тем быстрее будет выход»
  стр. 69  MA/EMA 200: «если местоположение этих скользящих совпадает с нашей точкой
           входа/выхода, то для нас это дополнительный фактор»

Каждый фактор отдаётся ОТДЕЛЬНО и вместе с числом, по которому его посчитали.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .models import Bar
from .swings import SwingKind, SwingSet


class DivergenceKind(StrEnum):
    DIVERGENCE = "divergence"
    """Хаи цены растут, хаи индикатора падают — «признак угасания силы тренда» (стр. 65)."""

    CONVERGENCE = "convergence"
    """Лои цены падают, лои индикатора растут — «признак усиления откупа» (стр. 65)."""


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
    из истории. Значение индикатора берётся на баре экстремума; если оно `None`
    (величина ещё не определена), фактор НЕ выдаётся — подставлять число нельзя (§4.3).
    """
    out: list[Divergence] = []
    for kind in (DivergenceKind.DIVERGENCE, DivergenceKind.CONVERGENCE):
        sk = SwingKind.HIGH if kind is DivergenceKind.DIVERGENCE else SwingKind.LOW
        seq = sorted(swings.of(sk), key=lambda s: s.index)
        if len(seq) < 2:
            continue
        prev, last = seq[-2], seq[-1]
        if last.index >= len(indicator) or prev.index >= len(indicator):
            continue
        i_prev, i_last = indicator[prev.index], indicator[last.index]
        if i_prev is None or i_last is None:
            continue
        if kind is DivergenceKind.DIVERGENCE:
            hit = last.price > prev.price and i_last < i_prev
        else:
            hit = last.price < prev.price and i_last > i_prev
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
        if u is None or low is None or m is None or m == 0:
            continue
        widths.append((u - low) / m * 100)
    if not widths:
        return None
    last = widths[-1]
    return SqueezeFactor(
        width_pct=last,
        percentile=sum(1 for w in widths if w <= last) / len(widths) * 100,
    )


def ma_touch(ma: list[float | None], entry: float) -> MaTouchFactor | None:
    """MA200 против точки входа (стр. 69). `None` — MA ещё не определена."""
    last = ma[-1] if ma else None
    if last is None or entry == 0:
        return None
    return MaTouchFactor(ma_value=last, entry=entry,
                         distance_pct=abs(last - entry) / entry * 100)
