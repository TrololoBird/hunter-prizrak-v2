"""Стоповый объём §2.3. Источник — мини-курс, стр. 34-40.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Разбор источника с цитатами: docs/audit/course-reading-2026-08-03.md

Механика, взятая дословно:
  стр. 34  «небольшое накопление, которое останавливает цену… это такое же накопление
           (база), но на более мелком ТФ, чем основное движение актива, и обычно более
           плотное (часто большой объём в мелком диапазоне)»
  стр. 34  «часто формируется над/под большим будущим накоплением — и в последствии
           удерживает цену внутри него как поддержка/сопротивление»
  стр. 35  «после выхода из стопового вверх у нас также появляется уровень в лонг…
           стоповый объём — это такое же накопление, и торговать его можно по тренду
           как обычное накопление»
  стр. 36  стоповый ВНУТРИ структуры — «когда в структуре несколько уровней»
  стр. 37  стоповый ЗА пределами границ базы
  стр. 39  стоповый ПЕРЕД накоплением: «на его зоне/ПОКе формирует новое большое накопление»

Отдельного детектора здесь НЕТ и быть не должно: стр. 34 и 35 дважды говорят, что это
«такое же накопление». Модуль только КЛАССИФИЦИРУЕТ уже найденные накопления младшего ТФ
по отношению к структуре старшего и меряет плотность.

⚠ Порога плотности здесь нет. Курс говорит «обычно», «часто» — это не порог, а наблюдение;
§4.1 запрещает абсолютные пороги. Плотность сообщается числом и рангом среди структур
самого символа, а решение принимает читающий.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .accumulation import Accumulation
from .models import Bar


class Placement(StrEnum):
    """Где стоповый стоит относительно структуры старшего ТФ. Стр. 34, 36, 37, 39."""

    INSIDE = "inside"
    """Внутри границ (стр. 36) — это и есть «в структуре несколько уровней»."""

    ABOVE = "above"
    """Над верхней границей (стр. 34, 37)."""

    BELOW = "below"
    """Под нижней границей (стр. 34, 37)."""

    BEFORE = "before"
    """Раньше по времени, чем началась структура (стр. 39)."""


class StopVolume(BaseModel):
    """Накопление младшего ТФ, отнесённое к структуре старшего."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accumulation: Accumulation
    host_timeframe: str = Field(min_length=1)
    placement: Placement

    price_range: float = Field(gt=0)
    """Высота структуры: верх верхней границы минус низ нижней."""

    bar_volume: float = Field(ge=0)
    """Сумма объёма БАРОВ структуры.

    ⚠ Это НЕ объём профиля: профиль по §5 строится на сделках (aggTrade), а здесь берётся
    объём свечей. Величины близкие, но не тождественные, и путать их нельзя — сюда объём
    сделок не тянется умышленно, чтобы классификация не требовала скачивания архива.
    """

    @property
    def density(self) -> float:
        """Объём на единицу цены. Стр. 34: «большой объём в мелком диапазоне»."""
        return self.bar_volume / self.price_range


class StopVolumeSet(BaseModel):
    """Результат классификации с рангами плотности внутри самого набора."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[StopVolume, ...]
    densities: tuple[float, ...]
    """Плотности всех поданных накоплений младшего ТФ — база для ранга."""

    def density_percentile(self, sv: StopVolume) -> float:
        """Доля структур того же прогона, чья плотность НЕ выше данной, в процентах.

        §4.1: порог — «перцентиль собственной истории символа», а не абсолютное число.
        Ранг считается по поданному набору; на одном элементе он равен 100 и смысла
        не несёт — это видно по длине `densities`, она не прячется.
        """
        d = sv.density
        return sum(1 for x in self.densities if x <= d) / len(self.densities) * 100


def _placement(small: Accumulation, host: Accumulation,
               small_bars: list[Bar], host_bars: list[Bar]) -> Placement:
    if small_bars[small.last_index].open_ms < host_bars[host.first_index].open_ms:
        return Placement.BEFORE
    mid = (small.upper.hi + small.lower.lo) / 2
    if mid > host.upper.hi:
        return Placement.ABOVE
    if mid < host.lower.lo:
        return Placement.BELOW
    return Placement.INSIDE


def classify(
    small: tuple[Accumulation, ...],
    small_bars: list[Bar],
    host: Accumulation,
    host_bars: list[Bar],
    host_timeframe: str,
) -> StopVolumeSet:
    """Отнести накопления младшего ТФ к структуре старшего.

    Ничего не отсеивается: курс не даёт признака, по которому накопление младшего ТФ
    НЕ является стоповым. Отсев по выдуманному порогу плотности здесь был бы ровно тем
    «магическим числом», от которого правило §0 и защищает.
    """
    items: list[StopVolume] = []
    for a in small:
        rng = a.upper.hi - a.lower.lo
        if rng <= 0:
            continue
        vol = sum(b.volume for b in small_bars[a.first_index:a.last_index + 1])
        items.append(
            StopVolume(
                accumulation=a,
                host_timeframe=host_timeframe,
                placement=_placement(a, host, small_bars, host_bars),
                price_range=rng,
                bar_volume=vol,
            )
        )
    return StopVolumeSet(
        items=tuple(items),
        densities=tuple(i.density for i in items),
    )
