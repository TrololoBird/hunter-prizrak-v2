"""ЗОНД: цена выбора режима Row Size — Number of Rows против Ticks Per Row.

Повод. Покадровый проход видео автора (2026-08-10) дал инвариант его экрана: постоянна
ЦЕНА СТРОКИ (~1.36 USD на BTC ≈ 0.07% цены), а не их число. Это подпись режима
Ticks Per Row. Боевой расчёт стоит на Number of Rows с `TV_ROWS = 100` — референты у
него другие (стр. 31 курса ≥95 строк, живой TV 02.2026, старшая реимплементация), и
видео их больше НЕ подпирает (отзыв записан в tv-transfer-2026-08-09.md).

Вопрос зонда ровно один: НАСКОЛЬКО отличается ответ прибора в двух режимах. Это цена
решения, а не сам выбор: выбор — владельца (§0), и до него боевой расчёт не трогается.

Меры (все по СТРУКТУРАМ реального кэша, не по синтетике):
  * доля структур, где ПОК СОВПАЛ (попал в ту же строку базового профиля);
  * медиана |ΔПОК| в процентах цены — насколько расходятся, когда расходятся;
  * ⚠ ГЛАВНАЯ: доля структур, где расхождение режимов ПРЕВЫШАЕТ ДОПУСК ПРОЕКТА
    (0.10 · ATR14, docs/audit/tolerance-R-01.md). Она и отвечает на вопрос «важен ли
    выбор режима»: расхождение ниже допуска означает, что спор о режиме не про уровни,
    а про цифры после запятой;
  * сколько строк даёт ticks-режим на самом деле (сверка с ~56 из видео);
  * та же пара мер для VAL/VAH — зона важнее ПОК для входа (стр. 30).

КОНТРОЛЬ 1 (способен ли прибор ответить иначе). Тот же замер при трёх ценах строки
0.05% / 0.07% / 0.10%: если доля совпадений одинакова — прибор к настройке глух, и
число ничего не значит.

КОНТРОЛЬ 2 (не лучше ли случайного). Тот же ticks-режим, но сетка сдвинута на ПОЛСТРОКИ
(`bottom + row_height/2`) — заведомо рассогласованная разметка той же плотности. Согласие
обязано УПАСТЬ; если не падает, мера слепа к сетке и число выше ничего не значит (форма
«решётки произвольных цен» из CLAUDE.md).

⚠ Первая редакция контроля сравнивала сдвинутый вариант с базовым ПО ДРУГОМУ КРИТЕРИЮ
(допуск в полстроки против точного равенства цен) и напечатала у сдвинутого 57-63%
согласия против 3-11% честного. Это был дефект замера, а не свойство рынка: разные
критерии несравнимы. Мера здесь ОДНА для всех трёх колонок — «ПОК попал в ту же строку
базового профиля» (тот же критерий, что в E-070).

ОТПЕЧАТОК ДАННЫХ печатается вместе с числами: замер идёт по растущему кэшу, и без
отпечатка расхождение при повторе прочтётся как опровержение, а не как доливка данных.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_tv_row_size_2026-08-10.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import detect as detect_accumulations  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.levels import structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady, TradeHistogram  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.volume_profile import TV_ROWS, build_tv  # noqa: E402

BUCKET_MS = 300_000
CACHE = ROOT / "data" / "aggcache"
NAME_RE = re.compile(r"^([A-Z0-9]+)-(\d{4}-\d{2}-\d{2})-300000-t([0-9p]+)-b2\.parquet$")
ROW_PCTS = (0.05, 0.07, 0.10)
"""Цены строки в процентах цены. 0.07 — замер по кадру видео; соседние — контроль."""

TF = "1h"
"""ТФ структур замера. Часовой: структур много, окна покрываются кэшем чаще всего."""

SHIFT_PCT = 1.0
"""Сдвиг цен для контроля 2 — та же величина, что в сверке с обзором автора."""


TOLERANCE_ATR = 0.10
"""Допуск проекта: 0.10 · ATR14 (docs/audit/tolerance-R-01.md). Не изобретён здесь."""


def atr14(bars: list[Bar]) -> float | None:
    """ATR14 по Уайлдеру — тот же расчёт, что в замерах R-01/R-04."""
    if len(bars) < 15:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i - 1].close),
               abs(bars[i].low - bars[i - 1].close)) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def tick_from_tag(tag: str) -> Decimal:
    return Decimal(tag.replace("p", "."))


def cached() -> dict[tuple[str, Decimal], list[Path]]:
    out: dict[tuple[str, Decimal], list[Path]] = defaultdict(list)
    for p in sorted(CACHE.glob("*.parquet")):
        m = NAME_RE.match(p.name)
        if m:
            out[(m[1], tick_from_tag(m[3]))].append(p)
    return out


def bars_from_frames(frames: list[pl.DataFrame], tick: Decimal, tf: str) -> list[Bar]:
    """Бары ТФ из посуточных кэшей: корзина 5м сворачивается в свечу ТФ.

    Бар здесь нужен только для поиска СТРУКТУР (границы, выход) — цены берутся из
    середин бинов, и этого хватает: сам профиль считается по бинам, а не по барам.
    """
    step = TIMEFRAME_MS[tf]
    agg: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for f in frames:
        for row in f.iter_rows(named=True):
            price = float(int(row["bin"]) * float(tick))
            agg[int(row["bucket"]) // step * step].append(
                (int(row["bucket"]), price, float(row["qty"])))
    out: list[Bar] = []
    for open_ms in sorted(agg):
        items = sorted(agg[open_ms])
        prices = [p for _, p, _ in items]
        out.append(Bar(open_ms=open_ms, open=prices[0], high=max(prices),
                       low=min(prices), close=prices[-1],
                       volume=sum(q for _, _, q in items)))
    return out


def hist_for_window(frames: list[pl.DataFrame], tick: Decimal, symbol: str,
                    from_ms: int, to_ms: int) -> TradeHistogram | None:
    h = TradeHistogram(symbol=symbol, tick_size=tick)
    for f in frames:
        part = f.filter((pl.col("bucket") >= from_ms) & (pl.col("bucket") < to_ms))
        for row in part.iter_rows(named=True):
            idx = int(row["bin"])
            h.qty_by_bin[idx] = h.qty_by_bin.get(idx, 0.0) + float(row["qty"])
            h.count_by_bin[idx] = h.count_by_bin.get(idx, 0) + int(row["n"])
    if not h.qty_by_bin:
        return None
    h.qty_seen = sum(h.qty_by_bin.values())
    h.trades_seen = sum(h.count_by_bin.values())
    return h


def shifted(h: TradeHistogram, pct: float) -> TradeHistogram:
    """Та же гистограмма, СДВИНУТАЯ по цене на `pct`% — контроль 2.

    Сдвигаются номера бинов, то есть двигается вся разметка целиком: если мера
    отражает рынок, согласие обязано упасть.
    """
    out = TradeHistogram(symbol=h.symbol, tick_size=h.tick_size)
    prices = [h.bin_price(b) for b in h.qty_by_bin]
    mid = float(sum(prices) / len(prices))
    delta_bins = round(mid * pct / 100 / float(h.tick_size))
    for b, q in h.qty_by_bin.items():
        out.qty_by_bin[b + delta_bins] = q
        out.count_by_bin[b + delta_bins] = h.count_by_bin.get(b, 0)
    out.qty_seen, out.trades_seen = h.qty_seen, h.trades_seen
    return out


def structures(bars: list[Bar]) -> list[tuple[int, int, Decimal, Decimal, float | None]]:
    """Закрытые структуры ряда: (окно от, окно до, min low, max high, ATR14 на конец)."""
    sw = detect_swings(bars)
    if isinstance(sw, NotReady):
        return []
    scan = detect_accumulations(bars, sw, TF, min_points=MIN_POINTS)
    if isinstance(scan, NotReady):
        return []
    out = []
    for acc in scan.closed:  # только закрытые: у открытого хвоста окна профиля нет
        from_ms, to_ms = structure_window_ms(acc, bars, TIMEFRAME_MS[TF])
        span = bars[acc.first_index: acc.last_index + 1]
        if not span:
            continue
        out.append((from_ms, to_ms,
                    Decimal(str(min(b.low for b in span))),
                    Decimal(str(max(b.high for b in span))),
                    atr14(bars[: acc.last_index + 1])))
    return out


MIN_POINTS = 3


def main() -> int:
    files = cached()
    if not files:
        print("ОТКАЗ: кэш сделок пуст — замерять нечего")
        return 2

    # Отпечаток данных: с ним число воспроизводимо, без него — только до первой доливки.
    total_files = sum(len(v) for v in files.values())
    print(f"ОТПЕЧАТОК ДАННЫХ: символов {len(files)}, файлов суток {total_files}, "
          f"ТФ структур {TF}, TV_ROWS={TV_ROWS}")

    agree: dict[float, list[int]] = {p: [] for p in ROW_PCTS}
    d_poc: dict[float, list[float]] = {p: [] for p in ROW_PCTS}
    d_zone: dict[float, list[float]] = {p: [] for p in ROW_PCTS}
    rows_seen: dict[float, list[int]] = {p: [] for p in ROW_PCTS}
    agree_shift: dict[float, list[int]] = {p: [] for p in ROW_PCTS}
    over_tol: dict[float, list[int]] = {p: [] for p in ROW_PCTS}
    structs = 0

    for (market, tick), paths in sorted(files.items()):
        frames = [pl.read_parquet(p) for p in paths]
        bars = bars_from_frames(frames, tick, TF)
        if len(bars) < 40:
            continue
        for from_ms, to_ms, lo, hi, atr in structures(bars):
            h = hist_for_window(frames, tick, market, from_ms, to_ms)
            if h is None:
                continue
            base = build_tv(h, bottom=lo, top=hi, rows=TV_ROWS)
            if isinstance(base, NotReady):
                continue
            structs += 1
            mid = float(base.poc_price)
            for pct in ROW_PCTS:
                tpr = max(1, round(mid * pct / 100 / float(tick)))
                alt = build_tv(h, bottom=lo, top=hi, ticks_per_row=tpr)
                if isinstance(alt, NotReady):
                    continue
                rows_seen[pct].append(alt.rows_built)
                # ЕДИНАЯ мера согласия для всех колонок: ПОК попал в ту же строку
                # базового профиля, то есть расхождение меньше половины его строки.
                half_base = float(tick) * base.ticks_per_row / 2
                agree[pct].append(
                    1 if abs(float(alt.poc_price - base.poc_price)) <= half_base else 0)
                delta = abs(float(alt.poc_price - base.poc_price))
                d_poc[pct].append(delta / mid * 100)
                if atr is not None and atr > 0:
                    # ГЛАВНАЯ мера: превышает ли расхождение режимов допуск проекта.
                    over_tol[pct].append(1 if delta > TOLERANCE_ATR * atr else 0)
                d_zone[pct].append(
                    (abs(float(alt.val_price - base.val_price))
                     + abs(float(alt.vah_price - base.vah_price))) / 2 / mid * 100)

                # Контроль 2: та же плотность строк, но сетка сдвинута на ПОЛСТРОКИ.
                half_row = tick * tpr / 2
                alt_s = build_tv(h, bottom=lo + half_row, top=hi + half_row,
                                 ticks_per_row=tpr)
                if isinstance(alt_s, NotReady):
                    continue
                agree_shift[pct].append(
                    1 if abs(float(alt_s.poc_price - base.poc_price)) <= half_base else 0)

    if not structs:
        print("ОТКАЗ: ни одной структуры с покрытым окном — замер не состоялся")
        return 2

    def med(xs: list[float]) -> float:
        s = sorted(xs)
        return 0.0 if not s else (s[len(s) // 2] if len(s) % 2
                                  else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2)

    print(f"\nструктур замерено: {structs} (ТФ {TF}, порог точек границ {MIN_POINTS})")
    print("\nСРАВНЕНИЕ РЕЖИМОВ: Number of Rows (100) против Ticks Per Row")
    print("  цена строки   строк (медиана)   ПОК совпал   |ΔПОК| med%   |Δзоны| med%"
          "   сетка ±полстроки   ВЫШЕ ДОПУСКА")
    for pct in ROW_PCTS:
        n = len(agree[pct])
        if not n:
            print(f"  {pct:>10.2f}%   — нет замеров")
            continue
        share = sum(agree[pct]) / n * 100
        share_s = (sum(agree_shift[pct]) / len(agree_shift[pct]) * 100
                   if agree_shift[pct] else float("nan"))
        over = (sum(over_tol[pct]) / len(over_tol[pct]) * 100
                if over_tol[pct] else float("nan"))
        print(f"  {pct:>10.2f}%   {med([float(x) for x in rows_seen[pct]]):>15.0f}"
              f"   {share:>9.1f}%   {med(d_poc[pct]):>11.3f}   {med(d_zone[pct]):>12.3f}"
              f"   {share_s:>15.1f}%   {over:>11.1f}%")
    n_tol = len(over_tol[ROW_PCTS[0]])
    print(f"  «ВЫШЕ ДОПУСКА» — доля структур, где |ΔПОК| > {TOLERANCE_ATR} · ATR14 "
          f"(знаменатель {n_tol} структур с посчитанным ATR)")

    shares = [sum(agree[p]) / len(agree[p]) for p in ROW_PCTS if agree[p]]
    print("\nКОНТРОЛЬ 1 (прибор способен ответить иначе): доли совпадений по трём "
          f"настройкам {[f'{s * 100:.1f}%' for s in shares]}")
    if len(set(round(s, 4) for s in shares)) == 1:
        print("  ⚠ ПРОВАЛ КОНТРОЛЯ: настройка не меняет ответ — число выше ничего не значит")
    else:
        print("  ок: настройка меняет ответ, значит доли — свойство данных, а не кода")
    honest = [sum(agree[p]) / len(agree[p]) for p in ROW_PCTS if agree[p]]
    placebo = [sum(agree_shift[p]) / len(agree_shift[p])
               for p in ROW_PCTS if agree_shift[p]]
    print("КОНТРОЛЬ 2 (не лучше ли случайного): та же мера на сетке, сдвинутой на "
          f"полстроки — {[f'{s * 100:.1f}%' for s in placebo]} против честных "
          f"{[f'{s * 100:.1f}%' for s in honest]}")
    if placebo and honest and max(placebo) >= max(honest):
        print("  ⚠ ПРОВАЛ КОНТРОЛЯ: сдвинутая сетка согласуется не хуже — мера слепа")
    else:
        print("  ок: сдвиг сетки согласие роняет, значит мера различает разметку")
    return 0


if __name__ == "__main__":
    sys.exit(main())
