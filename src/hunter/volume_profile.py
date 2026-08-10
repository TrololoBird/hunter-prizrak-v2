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

import math
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
    model_config = ConfigDict(frozen=True, extra="forbid")

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


TV_ROWS = 100
"""Число строк боевого профиля TV. Живого TradingView у владельца НЕТ — значение выбрано
по источникам (указание владельца 2026-08-09), и вот их сходимость:

  ЗАМЕР ПО САМОМУ КУРСУ (главный референт): нативные скриншоты страниц с нарисованным
  инструментом дают РАЗНОЕ число строк — ~24 (стр. 26, шаг строки 5.4 px — разрешено
  уверенно), ~50 (стр. 36, шаг 5.9), ~64 (стр. 33, шаг 2.5), ≥95 (стр. 31, шаг 2.2 px —
  предел разрешения скриншота, истинное число может быть выше). Единого умолчания у
  автора нет; воспроизведение замера — probes/probe_tv_rows_2026-08-09.py.

  Взято 100: нижняя граница стр. 31 — той самой страницы, где уровень строится двумя
  линиями от базы (ключевая для §2.2); живой TV показывает «около 100» (Trader Dale,
  02.2026); из 13 реимплементаций фиксированное N берут пять — 100, 50, 50, 24, 20,
  старшая из них (MarcosACH) — 100.

  Чувствительность к N ЗАМЕРЕНА (E-070): согласие сетки с тиковым ПОК — 29.1% при 24,
  28.6% при 50, 37.7% при 100, 49.1% при 200 (цена строки — центр). Константа вынесена,
  чтобы решение можно было пересмотреть одним числом, а его цену — замерить повтором.
"""


class RowSize(StrEnum):
    """Режим настройки Row Size у инструмента TradingView. Их РОВНО два (справка TV,
    Fixed Range Volume Profile → Inputs → Row Size)."""

    ROWS = "rows"
    """Number of Rows: задано число строк, высота выводится из диапазона."""

    TICKS = "ticks"
    """Ticks Per Row: задана ВЫСОТА строки в тиках, число строк выводится из диапазона.

    ⚠ Режим заведён 2026-08-10 по находке покадрового прохода видео автора: на его
    экране постоянна ЦЕНА СТРОКИ (~1.36 USD ≈ 0.07% цены при видимых ~56 строках), а не
    их число — это подпись режима Ticks Per Row, а не Number of Rows. Находка НЕ меняет
    боевой расчёт сама по себе: она даёт вторую гипотезу, цена которой замеряется
    (probes/probe_tv_row_size_2026-08-10.py), а решение о переключении — владельца.
    """


class TVProfile(BaseModel):
    """Профиль по сетке инструмента TradingView «Фиксированный профиль объема».

    Перенос прибора автора курса (решение владельца 2026-08-09). Спецификация собрана
    по 12 источникам — свод: docs/audit/tv-transfer-2026-08-09.md. Дословно по справке
    TradingView взяты:

      СЕТКА — режим Number of Rows: высота строки в тиках =
      round((top − bottom) / rows / tickSize), строк может выйти больше или меньше
      заданного (support/solutions/43000502040, «basic concepts»);

      VALUE AREA 70% — ПОСТРОЧНОЕ расширение (не парное CBOT): сравниваются строка над
      верхней границей и строка под нижней, берётся большая; ничья решается близостью
      к ПОК, при равном расстоянии — верхняя; строка, добавление которой ПЕРЕВАЛИВАЕТ
      цель, не добавляется (support/solutions/43000480324, Fixed Range VP indicator).

    Чего справка НЕ документирует — и что взято РЕШЕНИЕМ, названным вслух:

      РАСКЛАДКА ОБЪЁМА. TV кладёт объём intrabar-баров младшего ТФ (первый из
      1м…1Д, дающий < 5000 баров), и как объём одного бара делится по строкам —
      не сказано нигде. У нас есть сделки, поэтому объём кладётся ПО СДЕЛКАМ — это
      вернее самого прибора и точка расхождения с ним записана явно;

      ЦЕНА СТРОКИ — центр. Замер E-070: при 24 строках центр даёт наилучшее согласие
      с тиковым ПОК (29.1% против 18.3% у низа), у TV конвенция не документирована;

      НИЧЬЯ ПОК — NotReady, как у тикового профиля (§4.3): справка молчит, а выбор
      «первая попавшаяся строка» был бы молчаливым решением.

    ⚠ Число строк ПАРАМЕТР БЕЗ УМОЛЧАНИЯ: прежнее допущение «24 по умолчанию»
    официальной справкой не подтверждено (Trader Dale по живому TV 02.2026 — «около
    100»; LuxAlgo — «24…100, зависит от платформы»). Значение снимается с живого TV
    владельца, до этого боевой расчёт уровней остаётся на тиковом профиле.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_size: RowSize
    """Каким режимом Row Size построена сетка. Без него `ticks_per_row` в отчёте
    неотличим — задан он или выведен из числа строк."""

    rows_requested: int | None = Field(default=None, gt=0)
    """Сколько строк ЗАПРОШЕНО. `None` в режиме Ticks Per Row: там число строк не
    запрашивается, а выводится из диапазона."""

    ticks_per_row: int = Field(gt=0)
    rows_built: int = Field(gt=0)
    poc_row: int
    poc_price: Decimal
    poc_volume: float = Field(gt=0)
    val_price: Decimal
    vah_price: Decimal
    covered_volume: float = Field(gt=0)
    total_volume: float = Field(gt=0)
    clamped_volume: float = Field(ge=0)
    """Объём сделок вне [bottom, top], прижатый к крайним строкам. TV такого объёма не
    видит вовсе (его диапазон — экстремумы баров); ненулевое значение здесь означает,
    что переданный диапазон у́же реального и это видно, а не молчит."""


def build_tv(
    hist: TradeHistogram,
    bottom: Decimal,
    top: Decimal,
    rows: int | None = None,
    fraction: float = VALUE_AREA_FRACTION,
    *,
    ticks_per_row: int | None = None,
) -> TVProfile | NotReady:
    """Профиль по сетке TV: строки поверх тиковой гистограммы, VA по алгоритму TV.

    `bottom`/`top` — диапазон инструмента (у TV это экстремумы выделенных баров).
    Объём агрегируется из тиковых бинов `hist`: сделка ложится в строку по своей цене.

    Режим сетки — как у прибора, РОВНО ОДИН из двух (см. `RowSize`): либо `rows`
    (Number of Rows), либо `ticks_per_row` (Ticks Per Row). Оба сразу или ни одного —
    ошибка вызывающего, а не рыночный отказ, поэтому `ValueError`, а не `NotReady`.
    """
    if (rows is None) == (ticks_per_row is None):
        raise ValueError(
            "build_tv: задаётся РОВНО ОДИН режим Row Size — либо rows, либо ticks_per_row"
        )
    if rows is not None and rows <= 0:
        raise ValueError(f"build_tv: rows обязан быть > 0, получено {rows}")
    if ticks_per_row is not None and ticks_per_row <= 0:
        raise ValueError(
            f"build_tv: ticks_per_row обязан быть > 0, получено {ticks_per_row}")
    if top <= bottom:
        return NotReady(reason=f"{hist.symbol}: диапазон вырожден — top {top} ≤ bottom {bottom}")
    if not hist.qty_by_bin:
        return NotReady(reason=f"{hist.symbol}: профиль пуст, сделок нет")

    ticks_total = int((top - bottom) / hist.tick_size)
    if ticks_total < 1:
        return NotReady(reason=f"{hist.symbol}: диапазон у́же одного тика")
    if ticks_per_row is None:
        assert rows is not None
        mode = RowSize.ROWS
        ticks_per_row = max(1, round(ticks_total / rows))
    else:
        mode = RowSize.TICKS
    rows_built = -(-ticks_total // ticks_per_row)  # ceil: хвост диапазона — тоже строка

    row_height = hist.tick_size * ticks_per_row
    vol_by_row: dict[int, float] = {}
    clamped = 0.0
    for bin_idx, qty in hist.qty_by_bin.items():
        price = hist.bin_price(bin_idx)
        # floor, а не int(): int() усекает К НУЛЮ, и цена на полтика НИЖЕ bottom попала
        # бы в строку 0 как своя, а не как прижатая.
        row = math.floor((price - bottom) / row_height)
        if row < 0 or row >= rows_built:
            clamped += qty
            row = min(max(row, 0), rows_built - 1)
        vol_by_row[row] = vol_by_row.get(row, 0.0) + qty

    peak = max(vol_by_row.values())
    winners = [r for r, v in vol_by_row.items() if v == peak]
    if len(winners) > 1:
        return NotReady(
            reason=f"{hist.symbol}: ПОК по сетке TV неоднозначен — {len(winners)} строк "
                   f"с равным объёмом {peak}"
        )
    poc = winners[0]

    total = sum(vol_by_row.values())
    target = total * fraction
    lo = hi = poc
    covered = vol_by_row[poc]
    while covered < target:
        up = vol_by_row.get(hi + 1) if hi + 1 < rows_built else None
        down = vol_by_row.get(lo - 1, 0.0) if lo - 1 >= 0 else None
        up_v = up if up is not None else (0.0 if hi + 1 < rows_built else None)
        down_v = down if lo - 1 >= 0 else None
        if up_v is None and down_v is None:
            break
        # Выбор стороны — дословно по справке TV: большая строка; ничья — ближняя к
        # ПОК; при равном расстоянии — верхняя. Расстояния |hi+1−poc| и |poc−(lo−1)|.
        if down_v is None or (up_v is not None and (
                up_v > down_v
                or (up_v == down_v and (hi + 1 - poc) <= (poc - (lo - 1))))):
            chosen, at_top = up_v, True
        else:
            chosen, at_top = down_v, False
        assert chosen is not None
        if covered + chosen > target:
            break  # переваливающая строка не добавляется (справка TV)
        covered += chosen
        if at_top:
            hi += 1
        else:
            lo -= 1

    def row_center(r: int) -> Decimal:
        return bottom + row_height * r + row_height / 2

    return TVProfile(
        row_size=mode,
        rows_requested=rows,
        ticks_per_row=ticks_per_row,
        rows_built=rows_built,
        poc_row=poc,
        poc_price=row_center(poc),
        poc_volume=peak,
        val_price=bottom + row_height * lo,
        vah_price=bottom + row_height * (hi + 1),
        covered_volume=covered,
        total_volume=total,
        clamped_volume=clamped,
    )


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
        # ⚠ ВЕРХНЯЯ граница области стоимости — верхний край ВЕРХНЕГО бина, а не нижний.
        # Правка аудита 2026-08-07, находка Р-4 прошлого разбора, подтверждённая
        # исполнением: `bin_price(idx)` отдаёт НИЖНЮЮ границу бина, поэтому `vah` был
        # занижен ровно на один тик. На широкой зоне это 0.27% её ширины, на вырожденной
        # (два бина) — половина. Асимметрия была не замечена, потому что `val` от той же
        # функции получается верно: у нижней границы нижний край и есть граница.
        vah_price=hist.bin_price(hi) + hist.tick_size,
        covered_volume=covered,
        total_volume=total,
        bins_in_area=hi - lo + 1,
        expansion=expansion,
    )
