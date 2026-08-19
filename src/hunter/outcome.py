"""Исход сделки от уровня. FOUNDATION.md §8 этап 7, мини-курс стр. 9 и 14.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Единица — R, и она из курса, а не привнесена: стр. 9 «Р или R - "один Риск". Это единица
измерения эффективности сделки… Позволяет вести расчет профита/убытка в сделке».
Сумма процентов результатом не является: сделка со стопом
0.3% и сделка со стопом 30% несопоставимы, а в R сопоставимы по определению.

⚠ ГЛАВНОЕ ОГРАНИЧЕНИЕ, которое здесь НЕ обходится: по бару OHLC неизвестно, что случилось
раньше — стоп или цель. Если один бар накрывает и то и другое, исход НЕ назначается, а
помечается неоднозначным. Выбрать «обычно сначала стоп» значило бы придумать правило,
которого нет ни в данных, ни в курсе (§4.3).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .levels import LevelSide
from .models import Bar


class OutcomeKind(StrEnum):
    STOP = "stop"
    """Цена дошла до стопа. R = −1 по определению."""

    TARGET = "target"
    """Цена дошла до первой основной цели (стр. 24)."""

    BREAKEVEN = "breakeven"
    """Стоп был перенесён в ТВХ, и цена туда вернулась. R = 0 по определению курса.

    Стр. 14 дословно: «Безубыток (БУ) – это когда цена вернулась к точке открытия
    сделки. Выйти в б/у = закрыть позицию в ноль когда цена пошла против вашего
    сценария плана». Это ТРЕТИЙ исход, а не разновидность стопа и не «почти цель»:
    курс называет им отдельное событие и приписывает ему ноль, а не убыток.

    Курс возвращается к нему ещё пять раз, и всякий раз как к штатному завершению
    сделки: стр. 19 «Цена показала реакцию и ушла внутрь базы – ставите стоп в б/у»;
    стр. 30 «если цена сделает ловушку - она будет тестировать его еще раз с обратной
    стороны, давая выйти в б/у»; стр. 33 «на случай пробоя уровня и закрепа у вас будет
    больше шансов выйти в бу»; стр. 44 «Приоритет – выйти в б/у на тесте уровня с
    обратной стороны»; стр. 16 «вас выбьет в безубыток и вы ничего не потеряете».

    ⚠ Без этого исхода перенос стопа в БУ считался бы СТОПОМ с R = −1, то есть журнал
    приписывал бы системе убыток там, где курс велит выходить в ноль.
    """

    AMBIGUOUS = "ambiguous"
    """Один бар накрыл и стоп, и цель. Что было раньше — из OHLC не следует."""

    OPEN = "open"
    """Ряд кончился, ни стоп, ни цель не достигнуты."""

    NOT_FILLED = "not_filled"
    """Цена так и не дошла до входа: сделки не было вовсе."""


class Outcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OutcomeKind
    filled_at_index: int | None
    closed_at_index: int | None
    exit_price: Decimal | None
    r: float | None
    """Результат в R. `None` у неразрешённых.

    ⚠ Ноль — НЕ «нет ответа», а ответ: с 2026-08-19 он означает ровно безубыток
    (стр. 14 «закрыть позицию в ноль»). Именно поэтому неразрешённые несут `None`.
    """


def _filled(bar: Bar, price: Decimal, *, long: bool) -> bool:
    """Исполнилась ли лимитка входа на этом баре. Условие ОДНОСТОРОННЕЕ.

    ⚠ Было `bar.low <= price <= bar.high` — ДВУСТОРОННЕЕ касание диапазона, и это
    приписывало бирже поведение, которого у неё нет. Лимитка на покупку исполняется,
    как только цена опустилась ДО НЕЁ ИЛИ НИЖЕ; на продажу — как только поднялась до
    неё или выше. Цена, ПЕРЕПРЫГНУВШАЯ вход гэпом (открытие бара уже за лимиткой),
    двусторонним тестом не ловилась вовсе, и сделка получала исход «цена не дошла до
    входа» там, где ордер на бирже давно бы исполнился.

    Стоп и цель в этой же функции всегда проверялись односторонне (`bar.low <= stop`,
    `bar.high >= target`) — вход был единственным местом с другой меркой.
    """
    return (bar.low <= float(price)) if long else (bar.high >= float(price))


def resolve(
    side: LevelSide,
    entry: Decimal,
    stop: Decimal,
    target: Decimal | None,
    bars: list[Bar],
    from_index: int,
    breakeven_at: Decimal | None = None,
) -> Outcome:
    """Исход сделки по барам ПОСЛЕ появления уровня.

    Две фазы, и первая обязательна: сначала цена должна дойти до входа (лимитный ордер на
    ПОК, стр. 30), и только потом считается стоп или цель. Сделка, до которой цена не
    дошла, — это не убыток и не прибыль, это `NOT_FILLED`.

    `breakeven_at` — цена, по достижении которой стоп переносится в ТВХ (стр. 19: «Цена
    показала реакцию и ушла внутрь базы – ставите стоп в б/у»). Уровень этой цены здесь
    НЕ вычисляется и не угадывается: он принадлежит структуре, и его подаёт вызывающий.
    `None` — переноса нет, расчёт ровно прежний.

    ⚠ Перенос вступает в силу СО СЛЕДУЮЩЕГО бара, а не с того, на котором сработал. Это
    не осторожность, а то же ограничение OHLC, что и у `AMBIGUOUS`: внутри бара порядок
    касаний неизвестен, а бар срабатывания часто и есть бар входа — считать по нему
    возврат к ТВХ значило бы закрывать сделку тем же движением, которым её открыли.
    """
    risk = abs(entry - stop)
    if risk == 0:
        return Outcome(kind=OutcomeKind.NOT_FILLED, filled_at_index=None,
                       closed_at_index=None, exit_price=None, r=None)

    long = side is LevelSide.LONG
    filled: int | None = None
    be_armed = False

    for i in range(from_index, len(bars)):
        bar = bars[i]
        if filled is None:
            if _filled(bar, entry, long=long):
                filled = i
            else:
                continue

        if be_armed:
            hit_be = (bar.low <= float(entry)) if long else (bar.high >= float(entry))
            hit_tgt_be = (target is not None
                          and ((bar.high >= float(target)) if long
                               else (bar.low <= float(target))))
            if hit_be and hit_tgt_be:
                return Outcome(kind=OutcomeKind.AMBIGUOUS, filled_at_index=filled,
                               closed_at_index=i, exit_price=None, r=None)
            if hit_be:
                return Outcome(kind=OutcomeKind.BREAKEVEN, filled_at_index=filled,
                               closed_at_index=i, exit_price=entry, r=0.0)
            if hit_tgt_be:
                assert target is not None
                gain = (target - entry) if long else (entry - target)
                return Outcome(kind=OutcomeKind.TARGET, filled_at_index=filled,
                               closed_at_index=i, exit_price=target,
                               r=float(gain / risk))
            continue

        if breakeven_at is not None:
            be_armed = ((bar.high >= float(breakeven_at)) if long
                        else (bar.low <= float(breakeven_at)))

        hit_stop = (bar.low <= float(stop)) if long else (bar.high >= float(stop))
        hit_tgt = (target is not None
                   and ((bar.high >= float(target)) if long else (bar.low <= float(target))))

        if hit_stop and hit_tgt:
            return Outcome(kind=OutcomeKind.AMBIGUOUS, filled_at_index=filled,
                           closed_at_index=i, exit_price=None, r=None)
        if hit_stop:
            return Outcome(kind=OutcomeKind.STOP, filled_at_index=filled,
                           closed_at_index=i, exit_price=stop, r=-1.0)
        if hit_tgt:
            assert target is not None
            gain = (target - entry) if long else (entry - target)
            return Outcome(kind=OutcomeKind.TARGET, filled_at_index=filled,
                           closed_at_index=i, exit_price=target, r=float(gain / risk))

    if filled is None:
        return Outcome(kind=OutcomeKind.NOT_FILLED, filled_at_index=None,
                       closed_at_index=None, exit_price=None, r=None)
    return Outcome(kind=OutcomeKind.OPEN, filled_at_index=filled,
                   closed_at_index=None, exit_price=None, r=None)
