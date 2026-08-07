"""ЗОНД аудита R-01 (редакция 2): ПОК проекта против ПОК «как у автора».

Что изменилось против первой редакции и ПОЧЕМУ — обе причины методические, допуск НЕ
трогался (docs/audit/tolerance-R-01.md, sha256 49670e18…22dbf5 — тот же файл):

  1. ИСТОЧНИК СДЕЛОК. Первая редакция брала только `trades_by_bar.parquet` кадров прогона —
     это сделки, пойманные вживую за минуты наблюдения. Из-за этого 99.6% структур
     отпадали с «окно покрыто не полностью», и число 12 говорило о моём выборе источника,
     а не о системе. Здесь читается суточный кэш `data/aggcache` — тот же, что у боевого
     `archive.WindowSource`: BTC и ETH покрыты с 2026-03-16 по 2026-08-04.

  2. КОНТРОЛЬ К-2 БЫЛ ПОСТРОЕН НЕВЕРНО. Я подавал «равномерный объём» как единицу на
     занятую ячейку (корзина, бин). Это не нулевая гипотеза: получалась ПЛОТНОСТЬ ПРИСУТСТВИЯ
     сделок — величина, у которой пик там же, где у объёма, только глаже. Оба прибора её
     находили, согласие выходило 100%, и я едва не доложил «ответ хуже случайного».
     Правильный нуль обязан сохранить и множество занятых цен, и распределение объёмов, но
     разорвать связь между ними. Здесь: объёмы занятых бинов ПОВОРАЧИВАЮТСЯ по кругу на
     половину длины (детерминированно — случайность запрещена §10.3). Пик после этого
     стоит на произвольной занятой цене, и если приборы всё равно согласны — согласие есть
     свойство геометрии бинирования, а не находка настоящего пика.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_poc_binning_v2_2026-08-06.py
"""

from __future__ import annotations

import re
import statistics
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
    atr = sum(trs[:14]) / 14
    for tr in trs[14:]:
        atr = (atr * 13 + tr) / 14
    return atr


def poc_fixed_rows(bins: list[int], qtys: list[float], tick: Decimal,
                   n_rows: int) -> float | None:
    """ПОК прибора А: диапазон окна делится на `n_rows` равных строк. Ничья → None."""
    if not bins:
        return None
    t = float(tick)
    lo, hi = min(bins) * t, max(bins) * t
    if hi <= lo:
        return lo
    width = (hi - lo) / n_rows
    acc = [0.0] * n_rows
    for b, q in zip(bins, qtys, strict=True):
        acc[min(int((b * t - lo) / width), n_rows - 1)] += q
    peak = max(acc)
    win = [k for k, v in enumerate(acc) if v == peak]
    return None if len(win) != 1 else lo + (win[0] + 0.5) * width


def load_cache(market_id: str) -> tuple[Decimal, dict[int, dict[int, float]]] | None:
    """Все сутки кэша символа: корзина → бин → объём. Шаг цены берётся из имени файла."""
    tick: Decimal | None = None
    out: dict[int, dict[int, float]] = defaultdict(dict)
    found = 0
    for p in sorted(CACHE.iterdir()):
        m = NAME_RE.match(p.name)
        if not m or m.group(1) != market_id:
            continue
        t = Decimal(m.group(3).replace("p", "."))
        if tick is None:
            tick = t
        elif tick != t:
            raise ValueError(f"{market_id}: в кэше два шага цены — {tick} и {t}")
        df = pl.read_parquet(p)
        for b, i, q in zip(df["bucket"], df["bin"], df["qty"], strict=True):
            cell = out[int(b)]
            cell[int(i)] = cell.get(int(i), 0.0) + float(q)
        found += 1
    return None if tick is None or not found else (tick, dict(out))


def rotated(vals: list[float]) -> list[float]:
    """Детерминированный нуль: тот же набор объёмов, разорванная связь с ценой."""
    k = len(vals) // 2
    return vals[k:] + vals[:k]


def main() -> int:
    frames = ROOT / "data" / "frames"
    # символ → самый длинный ряд каждого ТФ среди всех сохранённых прогонов
    series: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
    market: dict[str, str] = {}
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            if not (sym_dir / "meta.json").exists():
                continue
            import json
            meta = json.loads((sym_dir / "meta.json").read_text(encoding="utf-8"))
            sym = meta["symbol"]
            market[sym] = sym.split("/")[0] + "USDT"
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in df.iter_rows(named=True)]
                if len(bars) > len(series[sym].get(tf, [])):
                    series[sym][tf] = bars

    rows: list[dict[str, object]] = []
    ctrl: list[dict[str, object]] = []
    skipped: dict[str, int] = defaultdict(int)
    by_tf_total: dict[str, int] = defaultdict(int)
    by_tf_used: dict[str, int] = defaultdict(int)

    for sym, tfs in sorted(series.items()):
        loaded = load_cache(market[sym])
        if loaded is None:
            skipped[f"{sym}: суточного кэша нет"] += 1
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
            for acc in detect_accumulations(bars, sw, tf).closed:
                by_tf_total[tf] += 1
                lo_ms, hi_ms = structure_window_ms(acc, bars, step)
                agg: dict[int, float] = {}
                need = missing = 0
                for b_ms in range(lo_ms - lo_ms % BUCKET_MS, hi_ms, BUCKET_MS):
                    need += 1
                    cell = cache.get(b_ms)
                    if cell is None:
                        missing += 1
                        continue
                    for i, q in cell.items():
                        agg[i] = agg.get(i, 0.0) + q
                if missing:
                    skipped[f"{tf}: окно не покрыто кэшем ({missing}/{need} корзин)"] += 1
                    continue
                if not agg:
                    skipped[f"{tf}: в окне нет сделок"] += 1
                    continue
                hist = TradeHistogram(symbol=sym, tick_size=tick)
                hist.qty_by_bin.update(agg)
                prof = build_profile(hist)
                if isinstance(prof, NotReady):
                    skipped[f"{tf}: прибор П отказал (ничья ПОК)"] += 1
                    continue
                by_tf_used[tf] += 1
                poc_p = float(prof.poc_price)
                height = float(acc.upper.hi - acc.lower.lo)
                ordered = sorted(agg.items())
                b_list = [b for b, _ in ordered]
                q_list = [q for _, q in ordered]
                q_null = rotated(q_list)
                hist_null = TradeHistogram(symbol=sym, tick_size=tick)
                hist_null.qty_by_bin.update(dict(zip(b_list, q_null, strict=True)))
                prof_null = build_profile(hist_null)
                for n in ROWS:
                    pa = poc_fixed_rows(b_list, q_list, tick, n)
                    if pa is not None:
                        d = abs(poc_p - pa)
                        rows.append({
                            "symbol": sym, "tf": tf, "n": n, "d_atr": d / a,
                            "d_pct_price": d / poc_p * 100,
                            "d_pct_height": (d / height * 100) if height > 0 else None,
                            "in_va": float(prof.val_price) <= pa <= float(prof.vah_price),
                            "hit": d <= TOLERANCE_ATR * a,
                            "bins": len(agg),
                        })
                    if not isinstance(prof_null, NotReady):
                        pn = poc_fixed_rows(b_list, q_null, tick, n)
                        if pn is not None:
                            dn = abs(float(prof_null.poc_price) - pn)
                            ctrl.append({"n": n, "hit": dn <= TOLERANCE_ATR * a})

    print("=" * 78)
    print("R-01 (ред. 2)  ПОК по tickSize против ПОК по фиксированному числу строк")
    print("=" * 78)
    print(f"структур найдено:  {sum(by_tf_total.values())}")
    print(f"структур замерено: {sum(by_tf_used.values())}")
    print(f"сравнений:         {len(rows)}")

    print("\nСВОДКА ОТКАЗОВ ПО ТАЙМФРЕЙМУ — измерение, вдоль которого возможен перекос")
    print(f"  {'ТФ':>4} {'структур':>9} {'замерено':>9} {'доля':>7}")
    for tf in TIMEFRAME_MS:
        t, u = by_tf_total.get(tf, 0), by_tf_used.get(tf, 0)
        if t:
            print(f"  {tf:>4} {t:>9} {u:>9} {u/t*100:>6.1f}%")
    print("\n  причины отказов:")
    for why, k in sorted(skipped.items(), key=lambda x: -x[1])[:8]:
        print(f"   {k:5d}  {why}")

    if not rows:
        print("\nСРАВНЕНИЙ НЕТ — замер не состоялся.")
        return 1

    print()
    print(f"{'N строк':>8} {'совпало':>9} {'из':>5} {'доля':>7} "
          f"{'мед Δ,ATR':>10} {'95-й Δ,ATR':>11} {'мед Δ,% выс.':>13} {'ПОК_А в VA':>11}")
    for n in ROWS:
        g = [r for r in rows if r["n"] == n]
        if not g:
            continue
        h = sum(1 for r in g if r["hit"])
        d = sorted(float(r["d_atr"]) for r in g)
        hp = [float(r["d_pct_height"]) for r in g if r["d_pct_height"] is not None]
        iv = sum(1 for r in g if r["in_va"])
        print(f"{n:>8} {h:>9} {len(g):>5} {h/len(g)*100:>6.1f}% "
              f"{statistics.median(d):>10.3f} {d[int(len(d)*0.95) - 1]:>11.3f} "
              f"{(statistics.median(hp) if hp else float('nan')):>13.1f} "
              f"{iv/len(g)*100:>10.1f}%")

    print("\nКОНТРОЛЬ К-1: прибор способен ответить иначе?")
    hh = sum(1 for r in rows if r["hit"])
    print(f"   совпадений {hh} из {len(rows)}")
    print("   ✓ пройден" if 0 < hh < len(rows)
          else "   ⚠ НЕ ПРОЙДЕН: ответ одинаков во всех случаях")

    print("\nКОНТРОЛЬ К-2: лучше ли ответ случайного?")
    print("   нуль = те же объёмы на тех же ценах, связь между ними разорвана поворотом")
    for n in ROWS:
        g = [r for r in rows if r["n"] == n]
        c = [r for r in ctrl if r["n"] == n]
        if not g or not c:
            continue
        gr, cr = sum(r["hit"] for r in g) / len(g), sum(r["hit"] for r in c) / len(c)
        v = "✓ лучше" if gr > cr else ("= НЕ ЛУЧШЕ" if gr == cr else "✗ ХУЖЕ")
        print(f"   N={n:>4}: настоящий {gr*100:5.1f}%   нуль {cr*100:5.1f}%   {v}")

    b = sorted(int(r["bins"]) for r in rows)
    print("\nРАЗМЕР ПРОФИЛЯ ПРИБОРА П (бинов шириной в тик)")
    print(f"   медиана {statistics.median(b):.0f}, мин {b[0]}, макс {b[-1]}; "
          f"у прибора А их {ROWS[0]}…{ROWS[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
