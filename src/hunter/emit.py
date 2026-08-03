"""Что подлежит эмиссии и с каким исходом. FOUNDATION.md §8 этапы 6-7.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются. Запись в леджер
и отправка — дело вызывающего; здесь только ОТБОР, и он обязан быть воспроизводим.

Отбирается по курсу, без единого собственного правила:
  стр. 23  уровня нет, пока цена не вышла из структуры → берутся только закрытые
  стр. 25  отработанный на первое касание уровень «удаляем»
  стр. 31  после отработки «уровень лимитными ордерами больше не торгуем»
  стр. 43  пробитый уровень флипается — прежним он больше не является
  стр. 18  «первая позиция всегда берётся по тренду»; стр. 47 — приоритет старшего ТФ

Согласие с приоритетом НЕ фильтрует: стр. 47 разрешает позицию против приоритета «в виде
хэджа и/или на уменьшенный объём риска». Поэтому она эмитится, но помечена — решение за
оператором, а не за кодом (§1: «Оператор торгует руками»).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from .geometry import Setup, build_setup
from .levels import Level, LevelSide, LevelState, status
from .models import Bar
from .outcome import Outcome, resolve
from .priority import Agreement, agreement
from .priority import resolve as resolve_priority
from .swings import Trend


class Emission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    setup: Setup
    agreement: Agreement

    @property
    def direction(self) -> str:
        return "long" if self.level.side is LevelSide.LONG else "short"

    @property
    def ledger_stop(self) -> Decimal:
        """Стоп для записи в леджер — РИСКОВЫЙ, за границу структуры (стр. 33).

        Не потому что он лучше, а потому что он единственный, который курс задаёт ОДНИМ
        числом: у безопасного стопа запас назван диапазоном 1-3%, и выбор внутри диапазона
        источником не сделан. Записать в леджер выдуманную середину значило бы измерять
        метод по числу, которого в нём нет.
        """
        return self.setup.stop_risky


def select(
    levels: tuple[Level, ...],
    series: dict[str, list[Bar]],
    trends: dict[str, Trend],
) -> tuple[Emission, ...]:
    """Уровни, пригодные к эмиссии: активные, с разрешёнными лимитками."""
    out: list[Emission] = []
    for lvl in levels:
        bars = series.get(lvl.timeframe)
        if not bars:
            continue
        st = status(lvl, bars)
        if st.state is not LevelState.ACTIVE or not st.limit_orders_allowed:
            continue
        pr = resolve_priority(trends, lvl.timeframe)
        out.append(Emission(level=lvl, setup=build_setup(lvl, levels),
                            agreement=agreement(lvl.side, pr)))
    return tuple(out)


def outcome_of(em: Emission, bars: list[Bar]) -> Outcome:
    """Исход по барам после появления уровня. Цель — первая ОСНОВНАЯ (стр. 24)."""
    primary = [t for t in em.setup.targets if t.role.value == "primary"]
    return resolve(
        side=em.level.side,
        entry=em.setup.entry,
        stop=em.ledger_stop,
        target=primary[0].price if primary else None,
        bars=bars,
        from_index=em.level.created_at_index + 1,
    )
