"""Переприор — слом структуры §2.5. Источник — мини-курс, стр. 49, 50, 51, 55.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

  стр. 49  «слом тенденции / структуры тренда, смена приоритета, иными словами
           это разворот цены в противоположное направление»
  стр. 50  ИСТИННЫЙ. «ПП в Шорт — цена ломает последний лой, ИЗ КОТОРОГО БЫЛ последний
           хай, и подтверждает его тестом снизу. ПП в Лонг — зеркально.»
  стр. 51  РАННИЙ. «Ранний ПП в Шорт — цена формирует хай, затем лой — затем НЕ обновляет
           хай и пробивает последний лой.»
  стр. 55  «Уровнем ПП является ВСЯ ЗОНА ТЕНИ свечи, образовавшей ХАЙ/ЛОЙ, а не только
           шпиль»; подтверждение — то же, что у любого уровня: 2-3 полных тела.
  стр. 55  «Если в лонг тренде цена не обновила хай — ЭТО НЕ ЯВЛЯЕТСЯ сломом тренда…
           Это может лишь являться доп фактором» → одного необновления мало, нужен слом.

Своего механизма подтверждения здесь НЕТ: стр. 55 говорит, что он один для всех уровней,
и он живёт в `breach.py`. Прокол сломанного уровня переприором не является — стр. 55:
«это просто прокол БЕЗ подтверждения, не берём позицию в таком случае».
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .breach import CONFIRM_BODIES, BreachKind, Direction, first_breach
from .models import Bar
from .swings import Swing, SwingKind, SwingSet


class PPKind(StrEnum):
    TRUE = "true"
    """Истинный (стр. 50): сломан лой, ИЗ КОТОРОГО был обновлённый хай."""

    EARLY = "early"
    """Ранний (стр. 51): хай НЕ обновлён, сломан предыдущий лой."""


class PPSide(StrEnum):
    SHORT = "short"
    LONG = "long"


class Pereprior(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PPKind
    side: PPSide

    broken_index: int
    """Бар сломанного экстремума."""

    broken_price: float

    zone_lo: float
    zone_hi: float
    """Уровень ПП — ВСЯ зона тени свечи экстремума (стр. 55), а не одна цена.

    У свечи без тени с нужной стороны зона вырождается в точку (`zone_lo == zone_hi`).
    Это не ошибка и не подставленное значение: тени действительно не было.
    """

    confirmed_at_index: int
    """Бар, на котором слом подтверждён телами (стр. 55)."""

    tested_at_index: int | None
    """Бар возврата в зону ПП — точка входа по стр. 50/51. None — теста ещё не было."""

    @property
    def zone_degenerate(self) -> bool:
        return self.zone_lo == self.zone_hi


def _shadow_zone(bar: Bar, kind: SwingKind) -> tuple[float, float]:
    """Зона тени свечи, образовавшей экстремум (стр. 55)."""
    if kind is SwingKind.LOW:
        return bar.low, min(bar.open, bar.close)
    return max(bar.open, bar.close), bar.high


def _test_index(bars: list[Bar], lo: float, hi: float, from_index: int) -> int | None:
    for i in range(from_index, len(bars)):
        if bars[i].low <= hi and bars[i].high >= lo:
            return i
    return None


def _detect_side(
    bars: list[Bar], swings: SwingSet, side: PPSide, confirm_bodies: int
) -> Pereprior | None:
    """Один ПП нужной стороны — самый поздний из подтверждённых."""
    broken_kind = SwingKind.LOW if side is PPSide.SHORT else SwingKind.HIGH
    other_kind = SwingKind.HIGH if side is PPSide.SHORT else SwingKind.LOW
    direction = Direction.BELOW if side is PPSide.SHORT else Direction.ABOVE

    ordered = sorted(swings.swings, key=lambda s: s.index)
    others = [s for s in ordered if s.kind is other_kind]
    if len(others) < 2:
        return None

    found: Pereprior | None = None
    for i in range(1, len(others)):
        prev_other, last_other = others[i - 1], others[i]

        # Стр. 50 против стр. 51: обновился ли хай (для шорта) / лой (для лонга).
        # ⚠ Считается по состоянию НА ТОТ МОМЕНТ, а не по последним двум экстремумам
        # ряда. Замер 2026-08-04 показал, к чему ведёт второе: после слома цена падает и
        # рисует новые, более НИЗКИЕ хаи, последний хай перестаёт быть обновлённым, и
        # каждый истинный ПП механически переклассифицируется в ранний. Истинных было
        # 0 из 32 конфигураций — не свойство рынка, а свойство порядка вычислений.
        updated = (last_other.price > prev_other.price if side is PPSide.SHORT
                   else last_other.price < prev_other.price)

        # Ломаемый экстремум — последний ПЕРЕД этим хаем: стр. 50 «лой, ИЗ КОТОРОГО был
        # последний хай», то есть тот, откуда начался рост. Условия «между двумя хаями»
        # курс не ставит, и оно отбрасывало 8 конфигураций из 32: фракталы Вильямса не
        # чередуются строго, между соседними хаями лоя может не быть вовсе.
        candidates = [s for s in ordered
                      if s.kind is broken_kind and s.index < last_other.index]
        if not candidates:
            continue
        broken: Swing = candidates[-1]

        # Конфигурация «последний хай + его лой» действует, пока не появился следующий
        # хай: после него «последним» становится уже он, и слом относился бы к другой
        # паре. Поэтому окно поиска слома ограничено сверху.
        end = others[i + 1].confirmed_at_index if i + 1 < len(others) else len(bars)
        start = max(broken.confirmed_at_index + 1, last_other.confirmed_at_index)
        if start >= end:
            continue

        lo, hi = _shadow_zone(bars[broken.index], broken_kind)
        edge = lo if side is PPSide.SHORT else hi
        ev = first_breach(bars[:end], edge, direction, from_index=start,
                          confirm_bodies=confirm_bodies)
        if ev is None or ev.kind is not BreachKind.BREAKOUT or ev.resolved_index is None:
            # Прокол подтверждением не является (стр. 55) — переприора нет.
            continue

        found = Pereprior(
            kind=PPKind.TRUE if updated else PPKind.EARLY, side=side,
            broken_index=broken.index, broken_price=broken.price,
            zone_lo=lo, zone_hi=hi,
            confirmed_at_index=ev.resolved_index,
            tested_at_index=_test_index(bars, lo, hi, ev.resolved_index + 1),
        )
    return found


def detect(
    bars: list[Bar], swings: SwingSet, *, confirm_bodies: int = CONFIRM_BODIES
) -> tuple[Pereprior, ...]:
    """Последний подтверждённый переприор каждой стороны.

    Не «все за историю»: ПП — это смена приоритета (стр. 49), и актуальна последняя.
    Обе стороны возвращаются, если обе нашлись, — выбирать между ними по одному лишь
    порядку было бы правилом, которого курс не даёт.
    """
    out = [pp for side in (PPSide.SHORT, PPSide.LONG)
           if (pp := _detect_side(bars, swings, side, confirm_bodies)) is not None]
    return tuple(out)


class PPFactor(BaseModel):
    """Необновлённый экстремум. Стр. 55: это НЕ слом, а доп-фактор.

    Отдельный тип, чтобы его нельзя было спутать с `Pereprior`: курс прямо
    предупреждает, что «цена не обновила хай» ошибочно принимают за слом тренда.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    side: PPSide
    last_price: float
    previous_price: float
    at_index: int
    watch_zone_lo: float
    watch_zone_hi: float
    """Зона, на которую стр. 55 советует «выставить оповещение»."""


def failed_update(bars: list[Bar], swings: SwingSet) -> tuple[PPFactor, ...]:
    """Экстремум не обновлён — повод следить, но не сигнал (стр. 55)."""
    out: list[PPFactor] = []
    for side in (PPSide.SHORT, PPSide.LONG):
        kind = SwingKind.HIGH if side is PPSide.SHORT else SwingKind.LOW
        seq = sorted(swings.of(kind), key=lambda s: s.index)
        if len(seq) < 2:
            continue
        last, prev = seq[-1], seq[-2]
        failed = last.price < prev.price if side is PPSide.SHORT else last.price > prev.price
        if not failed:
            continue
        opposite = SwingKind.LOW if side is PPSide.SHORT else SwingKind.HIGH
        marks = [s for s in sorted(swings.of(opposite), key=lambda s: s.index)
                 if s.index < last.index]
        if not marks:
            continue
        lo, hi = _shadow_zone(bars[marks[-1].index], opposite)
        out.append(PPFactor(side=side, last_price=last.price, previous_price=prev.price,
                            at_index=last.index, watch_zone_lo=lo, watch_zone_hi=hi))
    return tuple(out)


