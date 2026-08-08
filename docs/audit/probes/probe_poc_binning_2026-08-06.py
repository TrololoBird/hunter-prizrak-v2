"""ЗОНД аудита R-01: ПОК проекта против ПОК, построенного как у автора.

ВОПРОС. §5 FOUNDATION велит бинировать профиль по `tickSize`. Автор строит ПОК
инструментом "фиксированный профиль объема" TradingView (мини-курс, стр. 26 и 63) —
у него профиль делится на ФИКСИРОВАННОЕ ЧИСЛО СТРОК, а не на строки шириной в тик.
Это разные приборы. Замеряется, насколько разъезжается их ответ НА ОДНИХ И ТЕХ ЖЕ сделках.

Допуск зафиксирован ДО прогона: docs/audit/tolerance-R-01.md
    sha256 49670e1824d6b7fd931411b08b0da47467305c12ced57fb7e72d91501422dbf5
Совпадением считается |цена_П − цена_А| <= 0.10 · ATR14 своего ТФ.

Контроли из того же файла:
  К-1 прибор способен ответить иначе — обязаны встретиться и совпадения, и расхождения;
  К-2 ответ лучше случайного — тот же замер на РАВНОМЕРНОМ объёме внутри окна.

Прибор П — код проекта (`hunter.volume_profile.build` на `TradeHistogram`).
Прибор А — независимая реализация в этом файле: диапазон окна делится на N строк.
Реализации РАЗНЫЕ намеренно: сверять код с самим собой нельзя.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_poc_binning_2026-08-06.py
"""

from __future__ import annotations

import json
import statistics
import sys
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
"""Число строк профиля. 24 — умолчание «Фиксированного профиля объёма» TradingView."""

TOLERANCE_ATR = 0.10


def atr14(bars: list[Bar]) -> float | None:
    """ATR по Уайлдеру, скалярным циклом. Независимо от проектного кода намеренно."""
    if len(bars) < 15:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        p = bars[i - 1].close
        trs.append(max(bars[i].high - bars[i].low, abs(bars[i].high - p), abs(bars[i].low - p)))
    atr = sum(trs[:14]) / 14
    for tr in trs[14:]:
        atr = (atr * 13 + tr) / 14
    return atr


def poc_fixed_rows(prices: list[float], qtys: list[float], n_rows: int) -> float | None:
    """ПОК прибора А: диапазон делится на `n_rows` равных строк, берётся максимум.

    Возвращается ЦЕНТР строки — так рисует уровень TradingView. Ничья → None
    (неоднозначность не подменяется выбором первого).
    """
    if not prices:
        return None
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return lo
    width = (hi - lo) / n_rows
    acc = [0.0] * n_rows
    for p, q in zip(prices, qtys, strict=True):
        k = min(int((p - lo) / width), n_rows - 1)
        acc[k] += q
    peak = max(acc)
    winners = [k for k, v in enumerate(acc) if v == peak]
    if len(winners) != 1:
        return None
    return lo + (winners[0] + 0.5) * width


def main() -> int:
    runs = sorted(p.name for p in (ROOT / "data" / "frames").iterdir() if p.is_dir())
    rows_out: list[dict[str, object]] = []
    skipped: dict[str, int] = {}

    def skip(why: str) -> None:
        skipped[why] = skipped.get(why, 0) + 1

    for run in runs:
        for sym_dir in sorted((ROOT / "data" / "frames" / run).iterdir()):
            meta_p = sym_dir / "meta.json"
            trades_p = sym_dir / "trades_by_bar.parquet"
            if not meta_p.exists() or not trades_p.exists():
                skip("нет meta.json или trades_by_bar.parquet")
                continue
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            symbol, tick = meta["symbol"], Decimal(meta["tick_size"])
            bucket = int(meta["bucket_ms"])
            tdf = pl.read_parquet(trades_p)
            if tdf.is_empty():
                skip("раскладка сделок пуста")
                continue
            # bucket -> [(bin, qty)]
            by_bucket: dict[int, list[tuple[int, float]]] = {}
            for b, i, q in zip(tdf["bucket_ms"], tdf["bin"], tdf["qty"], strict=True):
                by_bucket.setdefault(int(b), []).append((int(i), float(q)))

            for tf, step in TIMEFRAME_MS.items():
                bar_p = sym_dir / f"{tf}.parquet"
                if not bar_p.exists():
                    continue
                bdf = pl.read_parquet(bar_p)
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in bdf.iter_rows(named=True)]
                if len(bars) < 15:
                    continue
                sw = detect_swings(bars)
                if isinstance(sw, NotReady):
                    continue
                scan = detect_accumulations(bars, sw, tf)
                a = atr14(bars)
                if a is None or a <= 0:
                    continue
                for acc in scan.closed:
                    lo_ms, hi_ms = structure_window_ms(acc, bars, step)
                    # собираем окно: все корзины, целиком лежащие в [lo_ms, hi_ms)
                    prices: list[float] = []
                    qtys: list[float] = []
                    hist = TradeHistogram(symbol=symbol, tick_size=tick)
                    covered = 0
                    for b_ms in range(lo_ms - lo_ms % bucket, hi_ms, bucket):
                        cell = by_bucket.get(b_ms)
                        if cell is None:
                            continue
                        covered += 1
                        for idx, q in cell:
                            hist.qty_by_bin[idx] = hist.qty_by_bin.get(idx, 0.0) + q
                            prices.append(float(Decimal(idx) * tick))
                            qtys.append(q)
                    need = max(1, (hi_ms - lo_ms) // bucket)
                    if covered < need:
                        skip(f"окно структуры покрыто не полностью ({tf})")
                        continue
                    prof = build_profile(hist)
                    if isinstance(prof, NotReady):
                        skip(f"прибор П отказал: {prof.reason.split(':')[-1].strip()[:40]}")
                        continue
                    poc_p = float(prof.poc_price)
                    height = float(acc.upper.hi - acc.lower.lo)
                    for n in ROWS:
                        poc_a = poc_fixed_rows(prices, qtys, n)
                        if poc_a is None:
                            skip(f"прибор А: ничья при N={n}")
                            continue
                        d = abs(poc_p - poc_a)
                        rows_out.append({
                            "run": run, "symbol": symbol, "tf": tf, "n": n,
                            "poc_p": poc_p, "poc_a": poc_a, "atr": a,
                            "d_abs": d, "d_atr": d / a,
                            "d_pct_price": d / poc_p * 100 if poc_p else 0.0,
                            "d_pct_height": d / height * 100 if height > 0 else float("nan"),
                            "in_va": float(prof.val_price) <= poc_a <= float(prof.vah_price),
                            "hit": d <= TOLERANCE_ATR * a,
                            "bins_p": len(hist.qty_by_bin),
                        })
                    # --- К-2: тот же замер на РАВНОМЕРНОМ объёме ---
                    if prices:
                        flat = [1.0] * len(prices)
                        hist_flat = TradeHistogram(symbol=symbol, tick_size=tick)
                        for p_, q_ in zip(prices, flat, strict=True):
                            i_ = hist_flat.bin_index(p_)
                            hist_flat.qty_by_bin[i_] = hist_flat.qty_by_bin.get(i_, 0.0) + q_
                        pf = build_profile(hist_flat)
                        if not isinstance(pf, NotReady):
                            for n in ROWS:
                                pa = poc_fixed_rows(prices, flat, n)
                                if pa is None:
                                    continue
                                dd = abs(float(pf.poc_price) - pa)
                                rows_out.append({
                                    "run": run, "symbol": symbol, "tf": tf, "n": n,
                                    "control": True, "d_atr": dd / a,
                                    "hit": dd <= TOLERANCE_ATR * a,
                                })

    real = [r for r in rows_out if not r.get("control")]
    ctrl = [r for r in rows_out if r.get("control")]

    print("=" * 78)
    print("R-01  ПОК по tickSize против ПОК по фиксированному числу строк")
    print("=" * 78)
    print(f"структур сравнено: {len({(r['run'], r['symbol'], r['tf'], r['poc_p']) for r in real})}")
    print(f"сравнений (структура × N): {len(real)}")
    if skipped:
        print("\nПРОПУЩЕНО (§4.3 — отказы называются, а не молчат):")
        for why, k in sorted(skipped.items(), key=lambda x: -x[1]):
            print(f"   {k:5d}  {why}")
    if not real:
        print("\nСРАВНЕНИЙ НЕТ — замер не состоялся.")
        return 1

    print()
    print(f"{'N строк':>8} {'совпало':>9} {'из':>5} {'доля':>7} "
          f"{'медиана Δ,ATR':>14} {'макс Δ,ATR':>11} {'медиана Δ,% выс.':>17} {'ПОК_А в VA':>11}")
    for n in ROWS:
        g = [r for r in real if r["n"] == n]
        if not g:
            continue
        hits = sum(1 for r in g if r["hit"])
        d = sorted(float(r["d_atr"]) for r in g)
        hpc = [float(r["d_pct_height"]) for r in g
               if isinstance(r["d_pct_height"], float) and r["d_pct_height"] == r["d_pct_height"]]
        inva = sum(1 for r in g if r["in_va"])
        print(f"{n:>8} {hits:>9} {len(g):>5} {hits/len(g)*100:>6.1f}% "
              f"{statistics.median(d):>14.3f} {d[-1]:>11.3f} "
              f"{(statistics.median(hpc) if hpc else float('nan')):>17.1f} "
              f"{inva/len(g)*100:>10.1f}%")

    print()
    print("КОНТРОЛЬ К-1: прибор способен ответить иначе?")
    all_hit = all(r["hit"] for r in real)
    none_hit = not any(r["hit"] for r in real)
    print(f"   совпадений {sum(1 for r in real if r['hit'])} из {len(real)}")
    if all_hit or none_hit:
        print("   ⚠ НЕ ПРОЙДЕН: ответ одинаков во всех случаях — мерится свойство кода.")
    else:
        print("   ✓ пройден: встречаются и совпадения, и расхождения")

    print()
    print("КОНТРОЛЬ К-2: ответ лучше случайного? (тот же замер на РАВНОМЕРНОМ объёме)")
    if not ctrl:
        print("   ⚠ контроль не состоялся")
    else:
        for n in ROWS:
            g = [r for r in real if r["n"] == n]
            c = [r for r in ctrl if r["n"] == n]
            if not g or not c:
                continue
            gr, cr = sum(r["hit"] for r in g) / len(g), sum(r["hit"] for r in c) / len(c)
            verdict = "✓ лучше" if gr > cr else ("= не лучше" if gr == cr else "✗ ХУЖЕ")
            print(f"   N={n:>4}: настоящий объём {gr*100:5.1f}%  "
                  f"равномерный {cr*100:5.1f}%   {verdict}")

    print()
    print("РАЗМЕР ПРОФИЛЯ ПРИБОРА П (сколько бинов шириной в тик)")
    b = sorted(int(r["bins_p"]) for r in real)
    print(f"   медиана {statistics.median(b):.0f}, минимум {b[0]}, максимум {b[-1]}")
    print(f"   для сравнения, у прибора А их {ROWS[0]}…{ROWS[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
