"""ЗОНД R-04: эмуляция ИНСТРУМЕНТА автора и чувствительность к соглашениям.

Повод — обзор чужих реализаций ПОК (22 проекта, 2026-08-06). Он вскрыл в замере R-01 два
места, которых я не проверял:

1. **Прибор А в R-01 не эмулировал инструмент автора.** Он бинировал ТИКОВЫЕ СДЕЛКИ
   проекта в N строк. Фиксированный профиль объёма TradingView так не работает: он
   раскладывает объём БАРА по диапазону бара (семейство B обзора: восемь проектов из 22
   делают именно это, и `dws-data/nas-orb-backtester` прямо пишет, что 24 строки выбраны
   ради совпадения с TradingView Fixed Range). То есть R-01 сравнивал НАШИ данные в двух
   разрешениях, а не наш ПОК с авторским.

2. **Цена внутри строки — соглашение, и оно может превышать допуск.** Прибор П отдаёт
   НИЖНЮЮ границу бина (`bin_price(idx) = idx*tick`), прибор А отдавал ЦЕНТР строки.
   При N=24 строка шириной около 0.2% цены; на BTC это порядка 60 долларов при допуске
   0.10·ATR ≈ 54. Обзор отмечает ту же развилку у bfolkens (округление ВВЕРХ) как
   «систематический сдвиг на половину шага сетки».

Плюс третий вопрос, поставленный обзором: **как часто случается НИЧЬЯ** при выборе
максимума. У двух авторов из 22 тай-брейк закодирован отдельно, у нас ничья даёт
`NotReady`, то есть уровень не выдаётся вовсе. Если ничьи часты — это молчаливый отказ
той же формы, что М-02.

Допуск НЕ менялся: 0.10 · ATR14, `docs/audit/tolerance-R-01.md`.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_poc_tv_emulation_2026-08-07.py
"""

from __future__ import annotations

import json
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
from hunter.volume_profile import build as build_profile  # noqa: E402
from hunter.volume_profile import point_of_control  # noqa: E402

ROWS = (24, 50, 100, 200)
TOLERANCE_ATR = 0.10
BUCKET_MS = 300_000
CACHE = ROOT / "data" / "aggcache"
NAME_RE = re.compile(r"^([A-Z0-9]+)-(\d{4}-\d{2}-\d{2})-300000-t([0-9p]+)-b2\.parquet$")


def atr14(bars: list[Bar]) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i - 1].close),
               abs(bars[i].low - bars[i - 1].close)) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def poc_rows(edges: tuple[float, float], acc: list[float],
             where: str) -> tuple[float, float, float] | None:
    """(цена ПОК по соглашению `where`, низ строки, верх строки). Ничья → None."""
    lo, hi = edges
    n = len(acc)
    width = (hi - lo) / n
    peak = max(acc)
    win = [k for k, v in enumerate(acc) if v == peak]
    if len(win) != 1:
        return None
    k = win[0]
    rlo, rhi = lo + k * width, lo + (k + 1) * width
    price = {"низ": rlo, "центр": (rlo + rhi) / 2, "верх": rhi}[where]
    return price, rlo, rhi


def rows_from_trades(bins: list[int], qtys: list[float], tick: Decimal,
                     n: int) -> tuple[tuple[float, float], list[float]]:
    """Прибор А-ТИК: тиковые сделки раскладываются в N строк (как в R-01)."""
    t = float(tick)
    lo, hi = min(bins) * t, max(bins) * t
    if hi <= lo:
        hi = lo + t
    width = (hi - lo) / n
    acc = [0.0] * n
    for b, q in zip(bins, qtys, strict=True):
        acc[min(int((b * t - lo) / width), n - 1)] += q
    return (lo, hi), acc


def rows_from_bars(bars: list[Bar], n: int) -> tuple[tuple[float, float], list[float]] | None:
    """Прибор А-БАР: объём КАЖДОГО БАРА размазывается по его диапазону high-low.

    Это семейство B обзора — способ, которым работают инструменты на барных данных, в том
    числе фиксированный профиль объёма TradingView. Размазывание РАВНОМЕРНОЕ, и это
    приближение: внутри бара цена не проводит одинаковое время на каждом уровне.
    Приближение названо самими авторами `dws-data/nas-orb-backtester` в их документации.
    """
    if not bars:
        return None
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    if hi <= lo:
        return None
    width = (hi - lo) / n
    acc = [0.0] * n
    for b in bars:
        i0 = min(int((b.low - lo) / width), n - 1)
        i1 = min(int((b.high - lo) / width), n - 1)
        share = b.volume / (i1 - i0 + 1)
        for k in range(i0, i1 + 1):
            acc[k] += share
    return (lo, hi), acc


def load_cache(market_id: str) -> tuple[Decimal, dict[int, dict[int, float]]] | None:
    tick: Decimal | None = None
    cells: dict[int, dict[int, float]] = defaultdict(dict)
    for p in sorted(CACHE.iterdir()):
        m = NAME_RE.match(p.name)
        if not m or m.group(1) != market_id:
            continue
        tick = Decimal(m.group(3).replace("p", "."))
        df = pl.read_parquet(p)
        for b, i, q in zip(df["bucket"], df["bin"], df["qty"], strict=True):
            c = cells[int(b)]
            c[int(i)] = c.get(int(i), 0.0) + float(q)
    return None if tick is None else (tick, dict(cells))


def main() -> int:
    frames = ROOT / "data" / "frames"
    series: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
    market: dict[str, str] = {}
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            market[sym] = sym.split("/")[0] + "USDT"
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                bb = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                          high=float(r["high"]), low=float(r["low"]),
                          close=float(r["close"]), volume=float(r["volume"]))
                      for r in df.iter_rows(named=True)]
                if len(bb) > len(series[sym].get(tf, [])):
                    series[sym][tf] = bb

    hit: dict[tuple[str, int, str], list[bool]] = defaultdict(list)
    same_peak: dict[tuple[str, int], list[bool]] = defaultdict(list)
    ties_p = ties_at = ties_ab = 0
    n_struct = 0
    bins_hist: list[int] = []

    for sym, tfs in sorted(series.items()):
        loaded = load_cache(market[sym])
        if loaded is None:
            continue
        tick, cache = loaded
        for tf, bars in sorted(tfs.items(), key=lambda x: TIMEFRAME_MS[x[0]]):
            step = TIMEFRAME_MS[tf]
            sw = detect_swings(bars)
            if isinstance(sw, NotReady):
                continue
            a = atr14(bars)
            if a is None or a <= 0:
                continue
            for acc_s in detect_accumulations(bars, sw, tf).closed:
                lo_ms, hi_ms = structure_window_ms(acc_s, bars, step)
                agg: dict[int, float] = {}
                missing = 0
                for b_ms in range(lo_ms - lo_ms % BUCKET_MS, hi_ms, BUCKET_MS):
                    cell = cache.get(b_ms)
                    if cell is None:
                        missing += 1
                        continue
                    for i, q in cell.items():
                        agg[i] = agg.get(i, 0.0) + q
                if missing or not agg:
                    continue
                hist = TradeHistogram(symbol=sym, tick_size=tick)
                hist.qty_by_bin.update(agg)
                if isinstance(point_of_control(hist), NotReady):
                    ties_p += 1
                    continue
                prof = build_profile(hist)
                if isinstance(prof, NotReady):
                    continue
                n_struct += 1
                bins_hist.append(len(agg))
                poc_p = float(prof.poc_price)
                inside = [b for b in bars
                          if lo_ms <= b.open_ms and b.open_ms + step <= hi_ms]
                ordered = sorted(agg.items())
                b_list = [b for b, _ in ordered]
                q_list = [q for _, q in ordered]

                for n in ROWS:
                    e_t, a_t = rows_from_trades(b_list, q_list, tick, n)
                    fb = rows_from_bars(inside, n)
                    for where in ("низ", "центр", "верх"):
                        r = poc_rows(e_t, a_t, where)
                        if r is None:
                            if where == "центр":
                                ties_at += 1
                            continue
                        hit[("А-тик", n, where)].append(abs(poc_p - r[0]) <= TOLERANCE_ATR * a)
                        if where == "центр":
                            same_peak[("А-тик", n)].append(r[1] <= poc_p <= r[2])
                    if fb is None:
                        continue
                    e_b, a_b = fb
                    for where in ("низ", "центр", "верх"):
                        r = poc_rows(e_b, a_b, where)
                        if r is None:
                            if where == "центр":
                                ties_ab += 1
                            continue
                        hit[("А-бар", n, where)].append(abs(poc_p - r[0]) <= TOLERANCE_ATR * a)
                        if where == "центр":
                            same_peak[("А-бар", n)].append(r[1] <= poc_p <= r[2])

    print("=" * 82)
    print("R-04  Эмуляция инструмента автора и чувствительность к соглашениям")
    print("=" * 82)
    print(f"структур замерено: {n_struct}")
    if not n_struct:
        print("ЗАМЕР НЕ СОСТОЯЛСЯ")
        return 1

    print()
    print("ВОПРОС 1. Как часто НИЧЬЯ при выборе максимума? (обзор: 2 автора из 22 её кодируют)")
    tot = n_struct + ties_p
    print(f"   прибор П, бины по tickSize:  ничьих {ties_p} из {tot} "
          f"({ties_p / tot * 100:.1f}%)   бинов в профиле: медиана "
          f"{sorted(bins_hist)[len(bins_hist) // 2]}")
    print(f"   прибор А-тик, N строк:       ничьих {ties_at}")
    print(f"   прибор А-бар, N строк:       ничьих {ties_ab}")
    print("   ЧЕМ МЕНЬШЕ БИНОВ, ТЕМ ЧАЩЕ НИЧЬЯ — и наоборот. Это и объясняет, почему")
    print("   тай-брейк кодируют те, у кого строк 24-50, и не кодируем мы.")

    print()
    print("ВОПРОС 2. Меняет ли ответ соглашение о цене ВНУТРИ строки?")
    print(f"{'прибор':>8} {'N':>5} {'низ строки':>12} {'центр':>9} {'верх строки':>13}")
    for arm in ("А-тик", "А-бар"):
        for n in ROWS:
            row = []
            for where in ("низ", "центр", "верх"):
                v = hit.get((arm, n, where))
                row.append("  —  " if not v else f"{sum(v) / len(v) * 100:5.1f}%")
            print(f"{arm:>8} {n:>5} {row[0]:>12} {row[1]:>9} {row[2]:>13}")

    print()
    print("ВОПРОС 3. Приближает ли ЭМУЛЯЦИЯ ИНСТРУМЕНТА (объём бара по диапазону) ответ?")
    print("   критерий честный: тиковый ПОК лежит ВНУТРИ выигравшей строки")
    print(f"{'N':>5} {'А-тик (было в R-01)':>22} {'А-бар (как TradingView)':>26}")
    for n in ROWS:
        t = same_peak.get(("А-тик", n))
        b = same_peak.get(("А-бар", n))
        ts = "  —  " if not t else f"{sum(t) / len(t) * 100:5.1f}%"
        bs = "  —  " if not b else f"{sum(b) / len(b) * 100:5.1f}%"
        print(f"{n:>5} {ts:>22} {bs:>26}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
