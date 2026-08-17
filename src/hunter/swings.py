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

⚠⚠ ОДИН БАР МОЖЕТ ДАТЬ ОБА ФРАКТАЛА, И ЭТО НЕ ПОБОЧНЫЙ ЭФФЕКТ — так у САМОГО АВТОРА
определения. Названо 2026-08-17: до этого докстрока обсуждала только равенство соседей, а
про внешний бар не говорила ничего, хотя он встречается у ~3% свингов (на 1Н 5.52%).
Перемер 2026-08-17 с сохранённым зондом и отпечатком данных:
docs/audit/evidence/probe-samebar-2026-08-17.txt — 3.16% на 2.13 млн баров без 1m
(1Н ровно 5.52%); исходные 3.06%/«7.6 млн» были сняты на меньшем кэше без свидетеля.

⚠ Ёлочки ниже НЕ ставятся намеренно: в этом проекте они зарезервированы за цитатами
мини-курса (гейт course_citations проверяет их дословность по PDF). Книжные цитаты идут в
английских кавычках.

Bill Williams, "New Trading Dimensions", Wiley 1998, page 68 — дословно:
    "Up and down fractals may share bars. The same bar can be part of both an up and a
     down fractal."

Там же, page 70 — ПОЧЕМУ так выходит:
    "Bar LOWs have no significance on UP-FRACTALS and bar HIGHS have no significance on
     DOWN-FRACTALS."

Williams & Gregory-Williams, "Trading Chaos" 2-е изд., Wiley 2004, page 137:
    "Fractals can share bars." … "middle finger is an up and down Fractal."

То есть верхний фрактал считается ИСКЛЮЧИТЕЛЬНО по `high`, нижний ИСКЛЮЧИТЕЛЬНО по `low`,
и внешний бар просто проходит две независимые проверки. Считается это ДВУМЯ фракталами, а
не одним. Наши два `if` подряд (а не `elif`) реализуют именно это.

Согласуется и с разметкой курса: на стр. 13 автор пометил хай широкой свечи номером 11, а
лой ТОЙ ЖЕ свечи — номером 12 (разбор: docs/audit/boundary-point-same-bar-2026-08-17.md).

⚠ Отрасль единого правила НЕ имеет: из 62 проверенных источников 13 считают такой бар
двумя точками (QuantConnect Lean имеет для этого отдельное значение `PivotPointType.Both`
с юнит-тестом), 17 схлопывают в одну, остальные — 9 неприменимы к вопросу и 23 молчат
(полная разбивка 13+17+9+23 — boundary-point-same-bar-2026-08-17.md). Мы следуем автору
выбранного определения — это основание, а не большинство голосов.

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
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SwingKind
    index: int = Field(ge=0)
    """Позиция среднего бара в поданном ряду."""

    open_ms: int
    price: float
    confirmed_at_index: int
    """Индекс бара, после закрытия которого фрактал стал известен: index + 2."""


class SwingSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    swings: tuple[Swing, ...]
    bars_scanned: int
    confirmed_until_index: int
    """Дальше этого индекса фракталы ещё не могут быть подтверждены."""

    def of(self, kind: SwingKind) -> tuple[Swing, ...]:
        return tuple(s for s in self.swings if s.kind is kind)


class TrendDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    NONE = "none"
    """Ни того, ни другого. Курс такого случая не называет — и здесь он не выдумывается."""


class Trend(BaseModel):
    """Тренд по стр. 12 — определение курса, а не индикатор.

    Дословно: «Восходящий тренд — движение цены на выбранном ТФ, при котором **каждый
    следующий ЛОЙ выше предыдущего**. Нисходящий — при котором **каждый следующий ХАЙ
    ниже предыдущего**.»
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: TrendDirection
    holds_for: int = Field(ge=0)
    """На скольких подряд идущих экстремумах свойство выполняется, считая от последнего.

    Окна здесь нет умышленно: курс говорит «каждый следующий», но не говорит, сколько
    их брать. Вместо выдуманного окна сообщается ЗАМЕРЕННАЯ глубина, на которой свойство
    держится, — читающий видит, тренд это из двух точек или из десяти (§I-7).
    """


def trend(swings: SwingSet) -> Trend:
    """Направление по стр. 12 и глубина, на которой оно держится.

    Минимальная проверка — два последних экстремума нужной стороны: «следующий выше
    предыдущего» меньше чем на двух не определено. Если выполняются оба условия сразу
    (лои растут И хаи падают) — это не тренд, а сходящаяся структура, и направление
    не назначается.
    """
    lows = [s.price for s in swings.of(SwingKind.LOW)]
    highs = [s.price for s in swings.of(SwingKind.HIGH)]

    def depth(seq: list[float], rising: bool) -> int:
        n = 0
        for i in range(len(seq) - 1, 0, -1):
            if (seq[i] > seq[i - 1]) if rising else (seq[i] < seq[i - 1]):
                n += 1
            else:
                break
        return n + 1 if n else 0

    up = depth(lows, rising=True)
    down = depth(highs, rising=False)
    if up and down:
        return Trend(direction=TrendDirection.NONE, holds_for=0)
    if up:
        return Trend(direction=TrendDirection.UP, holds_for=up)
    if down:
        return Trend(direction=TrendDirection.DOWN, holds_for=down)
    return Trend(direction=TrendDirection.NONE, holds_for=0)


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
