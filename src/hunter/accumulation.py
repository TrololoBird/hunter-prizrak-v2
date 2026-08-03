"""Накопление §2.1 и выход из него §2.2. Источник — мини-курс, стр. 13, 18, 22, 23, 55.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

Механика, взятая дословно:
  стр. 13  флэт — «локальные хаи/лои повторяются и не меняются» → точка границы это фрактал
  стр. 18  «границы базы (хай и лой) чаще всего определяются первыми 2-мя точками»
  стр. 18  прокол за границу — «расширение базы», а не слом
  стр. 22  «4 и более точек» в рамках своего ТФ
  стр. 23  «пока цена не вышла из структуры — у нас нет уровня»
  стр. 55  подтверждение = «2-3 полных тел свечей ЭТОГО ТФ» за уровнем;
           возврат той же или следующей свечой — прокол БЕЗ подтверждения

Свободных порогов здесь нет: ширина границы приходит из первых двух точек, а не из
константы. Обоснование — Р-6 в разборе источника.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import Bar
from .swings import SwingKind, SwingSet

MIN_BOUNDARY_POINTS = 4
"""Стр. 22, 23, 24: «4 и более точек границ». Курс называет только это число."""

CONFIRM_BODIES = 2
"""Стр. 55 даёт диапазон «2-3 полных тела». Двойка ЗАМЕРЕНА, а не выбрана.

Замер 2026-08-03 на структуре, которую автор обвёл сам (BTC 15м, 17.07): при 2 телах
ПОК = 62 800.0, до названного им вслух уровня 62 837.3 расхождение −0.059% — внутри
шума разрешения прибора. При 3 телах выход не подтверждается, структура вбирает ещё
сутки, ПОК = 64 000.0, расхождение +1.850% и ни одной линии автора рядом.
Протокол: docs/audit/stage3-corpus-acceptance-2026-08-03.md, результат 5.
"""


class ExitDirection(StrEnum):
    UP = "up"
    DOWN = "down"


class BoundaryZone(BaseModel):
    """Граница базы: зона, заданная ПЕРВЫМИ ДВУМЯ точками и дальше НЕИЗМЕННАЯ (стр. 18).

    Заморозка — не оптимизация, а требование стр. 13: «локальные хаи/лои повторяются и
    НЕ МЕНЯЮТСЯ». Первая редакция расширяла зону каждым новым экстремумом, и замер
    2026-08-04 показал, к чему это ведёт: на тренде BTC 15м накопилось 39 верхних точек
    против 6 нижних, «граница» расползлась на 1.82%, структура не закрывалась 316 баров
    подряд, а `detect` об этом молчал. Протокол: docs/audit/stuck-structure-2026-08-04.md
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: float
    hi: float
    point_indices: tuple[int, ...] = Field(min_length=2)

    puncture: float | None = None
    """Самый дальний заход ЗА зону без подтверждения выхода.

    Стр. 18: «если на 3++ точках были проколы за границы — стоп всегда ставится за этот
    прокол». `None` означает «проколов не было», а не «неизвестно»: продюсер здесь один
    и он всегда отрабатывает.
    """

    @property
    def width_pct(self) -> float:
        """Ширина зоны в процентах от её середины. Замеряется, а не ограничивается.

        Курс задаёт критерий «чётко видно структуру» визуально; механического порога
        он не даёт, поэтому число сообщается наружу для калибровки на корпусе (этап 3),
        а не превращается в выдуманную отсечку.
        """
        mid = (self.lo + self.hi) / 2
        return 0.0 if mid == 0 else (self.hi - self.lo) / mid * 100


class StructureExit(BaseModel):
    """Полноценный выход из структуры (стр. 23) = подтверждение по стр. 55."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: ExitDirection
    first_body_index: int
    confirmed_at_index: int
    """Бар, на котором набралось CONFIRM_BODIES тел подряд.

    Числа «сколько всего тел вышло» здесь нет умышленно: на момент подтверждения оно
    всегда равно CONFIRM_BODIES, а узнать продолжение можно только заглянув вперёд (I-5).
    Первая редакция несла такое поле, и живой прогон показал в нём 2 у всех 16 структур.
    """


class Accumulation(BaseModel):
    """Закрытая структура: границы известны, выход подтверждён.

    Незакрытых структур этот модуль не отдаёт — стр. 23: «пока цена не вышла из
    структуры, у нас нет уровня».
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    first_index: int
    last_index: int
    upper: BoundaryZone
    lower: BoundaryZone
    exit: StructureExit

    @property
    def points(self) -> int:
        return len(self.upper.point_indices) + len(self.lower.point_indices)

    @property
    def is_long(self) -> bool:
        """Лонговое накопление — из которого цена вышла вверх (стр. 22)."""
        return self.exit.direction is ExitDirection.UP


class OpenStructure(BaseModel):
    """Структура, из которой цена ещё не вышла. Уровня по стр. 23 у неё нет.

    Существует, чтобы её было ВИДНО (§4.3). Незакрытая структура — это не «ничего не
    нашли»: она может стоять сутками, и молчать о ней значит выдавать пустоту за покой.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_index: int
    bars_open: int
    upper: BoundaryZone
    lower: BoundaryZone

    @property
    def points(self) -> int:
        return len(self.upper.point_indices) + len(self.lower.point_indices)


class AccumulationScan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    closed: tuple[Accumulation, ...]
    open_tail: OpenStructure | None
    bars_scanned: int
    resets: int
    """Сколько раз структура распалась, не дав уровня (зоны сошлись либо точек не хватило)."""


def _body_beyond(bar: Bar, level: float, direction: ExitDirection) -> bool:
    """Тело свечи ЦЕЛИКОМ за уровнем (стр. 55 «полных тел», не «закрытий»). Р-8."""
    if direction is ExitDirection.UP:
        return min(bar.open, bar.close) > level
    return max(bar.open, bar.close) < level


def detect(
    bars: list[Bar],
    swings: SwingSet,
    timeframe: str,
    *,
    min_points: int = MIN_BOUNDARY_POINTS,
    confirm_bodies: int = CONFIRM_BODIES,
) -> AccumulationScan:
    """Накопления, из которых цена уже вышла, плюс незакрытый хвост.

    Проход строго слева направо по закрытым барам. Фрактал участвует только с того
    бара, на котором он стал известен (`confirmed_at_index`) — иначе структура
    опиралась бы на будущее (I-5).

    Границы каждой стороны задаются ПЕРВЫМИ ДВУМЯ её точками и дальше не двигаются
    (стр. 18). Экстремум за зоной — прокол: он считается точкой границы и запоминается
    для стопа, но зону НЕ расширяет. Выход проверяется против замороженной границы.

    Пороги вынесены в аргументы НЕ для настройки: умолчания взяты из курса и меняться
    не должны. Они вынесены, чтобы чувствительность результата к ним можно было
    ЗАМЕРИТЬ — курс даёт «4+» и «2-3», то есть диапазоны, и без замера выбор внутри
    диапазона остаётся магическим числом.
    """
    by_confirm: dict[int, list[tuple[SwingKind, int, float]]] = {}
    for s in swings.swings:
        by_confirm.setdefault(s.confirmed_at_index, []).append((s.kind, s.index, s.price))

    found: list[Accumulation] = []
    hi_px: list[float] = []
    hi_idx: list[int] = []
    lo_px: list[float] = []
    lo_idx: list[int] = []
    hi_punct: float | None = None
    lo_punct: float | None = None
    run_dir: ExitDirection | None = None
    run_from = 0
    resets = 0

    def reset() -> None:
        nonlocal run_dir, run_from, hi_punct, lo_punct, resets
        hi_px.clear()
        hi_idx.clear()
        lo_px.clear()
        lo_idx.clear()
        hi_punct = lo_punct = None
        run_dir, run_from = None, 0
        resets += 1

    def upper_zone() -> tuple[float, float]:
        return min(hi_px[:2]), max(hi_px[:2])

    def lower_zone() -> tuple[float, float]:
        return min(lo_px[:2]), max(lo_px[:2])

    for k in range(len(bars)):
        for kind, index, price in by_confirm.get(k, []):
            if kind is SwingKind.HIGH:
                if len(hi_px) < 2:
                    hi_px.append(price)
                    hi_idx.append(index)
                    continue
                zlo, zhi = upper_zone()
                if price < zlo:
                    continue  # локальный хай внутри базы — не точка границы
                hi_px.append(price)
                hi_idx.append(index)
                if price > zhi:
                    # Прокол за границу (стр. 18): точка засчитана, зона НЕ расширена,
                    # глубина прокола запомнена — за неё ставится стоп.
                    hi_punct = price if hi_punct is None else max(hi_punct, price)
            else:
                if len(lo_px) < 2:
                    lo_px.append(price)
                    lo_idx.append(index)
                    continue
                zlo, zhi = lower_zone()
                if price > zhi:
                    continue
                lo_px.append(price)
                lo_idx.append(index)
                if price < zlo:
                    lo_punct = price if lo_punct is None else min(lo_punct, price)

        if len(hi_px) < 2 or len(lo_px) < 2:
            continue

        upper_lo, upper_hi = upper_zone()
        lower_lo, lower_hi = lower_zone()
        if upper_lo <= lower_hi:
            # Зоны границ пересеклись — горизонтального диапазона нет (стр. 13).
            reset()
            continue

        bar = bars[k]
        if _body_beyond(bar, upper_hi, ExitDirection.UP):
            direction = ExitDirection.UP
        elif _body_beyond(bar, lower_lo, ExitDirection.DOWN):
            direction = ExitDirection.DOWN
        else:
            # Возврат внутрь структуры обрывает серию тел: стр. 55 — «возвращается той
            # же или следующей свечой» это прокол БЕЗ подтверждения.
            run_dir = None
            continue

        if direction is not run_dir:
            run_dir, run_from = direction, k
        bodies = k - run_from + 1
        if bodies < confirm_bodies:
            continue
        if len(hi_px) + len(lo_px) < min_points:
            # Цена ушла раньше, чем структура набрала 4 точки — накопления не было.
            reset()
            continue

        found.append(
            Accumulation(
                timeframe=timeframe,
                first_index=min(hi_idx + lo_idx),
                last_index=k,
                upper=BoundaryZone(lo=upper_lo, hi=upper_hi,
                                   point_indices=tuple(hi_idx), puncture=hi_punct),
                lower=BoundaryZone(lo=lower_lo, hi=lower_hi,
                                   point_indices=tuple(lo_idx), puncture=lo_punct),
                exit=StructureExit(
                    direction=direction,
                    first_body_index=run_from,
                    confirmed_at_index=k,
                ),
            )
        )
        reset()
        resets -= 1  # эмиссия — не распад

    tail: OpenStructure | None = None
    if len(hi_px) >= 2 and len(lo_px) >= 2:
        ulo, uhi = upper_zone()
        llo, lhi = lower_zone()
        first = min(hi_idx + lo_idx)
        tail = OpenStructure(
            first_index=first,
            bars_open=len(bars) - first,
            upper=BoundaryZone(lo=ulo, hi=uhi, point_indices=tuple(hi_idx), puncture=hi_punct),
            lower=BoundaryZone(lo=llo, hi=lhi, point_indices=tuple(lo_idx), puncture=lo_punct),
        )

    return AccumulationScan(
        closed=tuple(found), open_tail=tail, bars_scanned=len(bars), resets=resets
    )
