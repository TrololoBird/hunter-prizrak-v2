"""Детекция свингов — фрактал Билла Вильямса. FOUNDATION.md §0.2 тип Б, §2.5.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Единого канона у детекции свингов НЕТ, есть конвенции. По указанию владельца выбрана
ОДНА и реализована БУКВАЛЬНО, без смешивания и без своих добавок.

ВЫБРАНА: фрактал Билла Вильямса. Это конвенция (тип Б по §0.2), поэтому счёт источников
действует — их три независимых:

  MetaTrader 5, справка по индикатору Fractals:
    «a series of at least five successive bars, with the highest HIGH in the middle,
     and two lower HIGHs on both sides» (верхний);
    «a series of at least five successive bars, with the lowest LOW in the middle,
     and two higher LOWs on both sides» (нижний).

  thinkorswim (Charles Schwab), Williams Fractal:
    «A sequence of at least five bars where the highest price is reached in the middle,
     preceded and followed by lower highs»; зеркально для нижнего.

  TradingView, Williams Fractal:
    «five candlesticks or bars… with the third candlestick always presenting as the
     highest high or lowest low».

Все три сходятся: ПЯТЬ баров, экстремум на СРЕДНЕМ, соседи строго слабее.

Почему ZigZag не выбран: его порог — свободный параметр, который §I-7 прошлого проекта
превращал в магическое число. У фрактала параметров нет вовсе.

⚠ ПОДТВЕРЖДЕНИЕ ТРЕБУЕТ ДВУХ БАРОВ СПРАВА. Фрактал на баре i становится известен только
после закрытия бара i+2 — это свойство определения, а не задержка реализации. Функция
возвращает `confirmed_until`, чтобы вызывающий не принял неподтверждённый экстремум за
подтверждённый (§4.3, I-5: заглядывание вперёд запрещено).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import Bar, NotReady

# Все три источника: пять баров, экстремум посередине. Число не настраивается.
FRACTAL_BARS = 5
SIDE_BARS = FRACTAL_BARS // 2


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Swing(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SwingKind
    index: int = Field(ge=0)
    """Позиция среднего бара в поданном ряду."""

    open_ms: int
    price: float
    confirmed_at_index: int
    """Индекс бара, после закрытия которого фрактал стал известен: index + 2."""


class SwingSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    swings: tuple[Swing, ...]
    bars_scanned: int
    confirmed_until_index: int
    """Дальше этого индекса фракталы ещё не могут быть подтверждены."""


def detect(bars: list[Bar]) -> SwingSet | NotReady:
    """Фракталы Вильямса на закрытых барах.

    Соседи сравниваются строго (`<` и `>`): определение говорит «lower HIGHs» и
    «higher LOWs», то есть равенство фрактал не образует. Плато из одинаковых
    максимумов фракталом не является ни по одному из трёх источников.
    """
    if len(bars) < FRACTAL_BARS:
        return NotReady(
            reason=f"фрактал требует {FRACTAL_BARS} баров, подано {len(bars)}"
        )

    found: list[Swing] = []
    for i in range(SIDE_BARS, len(bars) - SIDE_BARS):
        mid = bars[i]
        left = bars[i - SIDE_BARS:i]
        right = bars[i + 1:i + 1 + SIDE_BARS]

        if all(b.high < mid.high for b in left) and all(b.high < mid.high for b in right):
            found.append(Swing(kind=SwingKind.HIGH, index=i, open_ms=mid.open_ms,
                               price=mid.high, confirmed_at_index=i + SIDE_BARS))
        if all(b.low > mid.low for b in left) and all(b.low > mid.low for b in right):
            found.append(Swing(kind=SwingKind.LOW, index=i, open_ms=mid.open_ms,
                               price=mid.low, confirmed_at_index=i + SIDE_BARS))

    return SwingSet(
        swings=tuple(found),
        bars_scanned=len(bars),
        confirmed_until_index=len(bars) - 1 - SIDE_BARS,
    )
