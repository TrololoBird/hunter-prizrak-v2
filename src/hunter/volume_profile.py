"""Профиль объёма: ПОК, VAH, VAL. FOUNDATION.md §2.2, §5.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

ИСТОЧНИКИ

ПОК — «the price of the peak cleared volume is identified as the Point of Control»,
https://en.wikipedia.org/wiki/Market_profile

Value Area — «the central seventy percent of trading activity about POC» (там же).
Wikipedia даёт ДОЛЮ, но не процедуру расширения.

⚠ ПРОЦЕДУРА РАСШИРЕНИЯ У ИСТОЧНИКОВ РАЗНАЯ. Это заявляется как РЕШЕНИЕ, а не
выводится, и ссылки ниже — на страницы, которые реально прочитаны:

  ПО ОДНОМУ УРОВНЮ (два независимых вендора):
    CQG, Market Profile Value Areas, help.cqg.com — «expands the value area one price
    at a time in either direction… expanded in the direction of the price having more
    TPOs»;
    TradingView, TPO indicator, tradingview.com/support/solutions/43000713306 —
    сравнивает строку над верхней границей со строкой под нижней, добавляет большую.

  ПАРАМИ (один образовательный сайт):
    eminimind.com/the-ultimate-guide-to-market-profile — «Add the TPOs of the two
    prices above and below the POC. Beginning with the larger number of combined two
    rows of TPOs, add this number to the POC number…».

⚠ CME Group — институциональный первоисточник (ему принадлежит CBOT, где метод создан;
«Market Profile» — его товарный знак). Его страница НЕДОСТУПНА отсюда: два запроса
завершились ECONNRESET. Источником не является и в ссылках не значится.

⚠ ПО УМОЛЧАНИЮ — SINGLE, и это следствие правила §0.1 (минимум три источника,
блог источником не является). У варианта «по одному» два независимых источника
уровня 3 (CQG, TradingView); у «парами» после отсева блога — НОЛЬ. Реализованы оба,
расхождение замерено и мало (0.0022% от цены ПОК на сутках BTC):
docs/audit/value-area-2026-08-03.md, реестр — docs/sources.toml

Бины привязаны к `tickSize` инструмента (§5, §10.2): фиксированное число бинов не
используется — при разном числе бинов один и тот же ПОК получается разным.

Окно профиля здесь НЕ задаётся: какие бары входят в зону, определяют границы
накопления (§2.1), то есть этап 3. Этот модуль считает профиль по уже поданной
гистограмме.

Метрик устойчивости, надёжности и качества ПОК здесь нет и не будет (§3, §4.2).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import NotReady, TradeHistogram

# Каноническая доля объёма в области стоимости. Источник — определение выше.
VALUE_AREA_FRACTION = 0.70


class Expansion(StrEnum):
    PAIRS = "pairs"
    SINGLE = "single"


class VolumeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    poc_bin: int
    poc_price: Decimal
    poc_volume: float = Field(gt=0)
    val_bin: int
    vah_bin: int
    val_price: Decimal
    vah_price: Decimal
    covered_volume: float = Field(gt=0)
    total_volume: float = Field(gt=0)
    bins_in_area: int = Field(gt=0)
    expansion: Expansion

    @property
    def covered_fraction(self) -> float:
        return self.covered_volume / self.total_volume


def point_of_control(hist: TradeHistogram) -> int | NotReady:
    """Бин с максимальным объёмом. Ничья — неоднозначность, а не повод выбрать первый."""
    if not hist.qty_by_bin:
        return NotReady(reason=f"{hist.symbol}: профиль пуст, сделок нет")
    peak = max(hist.qty_by_bin.values())
    if peak <= 0:
        return NotReady(reason=f"{hist.symbol}: суммарный объём в бинах не положителен")
    winners = [b for b, v in hist.qty_by_bin.items() if v == peak]
    if len(winners) > 1:
        return NotReady(
            reason=f"{hist.symbol}: ПОК неоднозначен — {len(winners)} бинов с равным "
                   f"объёмом {peak}, цены {[str(hist.bin_price(b)) for b in sorted(winners)[:5]]}"
        )
    return winners[0]


def build(
    hist: TradeHistogram,
    fraction: float = VALUE_AREA_FRACTION,
    expansion: Expansion = Expansion.SINGLE,
) -> VolumeProfile | NotReady:
    """ПОК + область стоимости. Пустая зона или неоднозначность → NotReady (§4.3)."""
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"доля области стоимости вне (0,1]: {fraction}")
    poc = point_of_control(hist)
    if isinstance(poc, NotReady):
        return poc

    total = sum(hist.qty_by_bin.values())
    target = total * fraction
    lo = hi = poc
    covered = hist.qty_by_bin[poc]
    low_bin, high_bin = min(hist.qty_by_bin), max(hist.qty_by_bin)
    step = 2 if expansion is Expansion.PAIRS else 1

    def block(start: int, direction: int) -> tuple[float, int]:
        """Объём следующих `step` бинов в направлении и до какого бина они доходят."""
        vol = 0.0
        end = start
        for k in range(1, step + 1):
            b = start + direction * k
            if b < low_bin or b > high_bin:
                break
            vol += hist.qty_by_bin.get(b, 0.0)
            end = b
        return vol, end

    while covered < target and (lo > low_bin or hi < high_bin):
        up_vol, up_end = block(hi, +1)
        down_vol, down_end = block(lo, -1)
        can_up = up_end != hi
        can_down = down_end != lo
        if not can_up and not can_down:
            break
        if can_up and (not can_down or up_vol >= down_vol):
            covered += up_vol
            hi = up_end
        else:
            covered += down_vol
            lo = down_end

    return VolumeProfile(
        poc_bin=poc,
        poc_price=hist.bin_price(poc),
        poc_volume=hist.qty_by_bin[poc],
        val_bin=lo,
        vah_bin=hi,
        val_price=hist.bin_price(lo),
        vah_price=hist.bin_price(hi),
        covered_volume=covered,
        total_volume=total,
        bins_in_area=hi - lo + 1,
        expansion=expansion,
    )
