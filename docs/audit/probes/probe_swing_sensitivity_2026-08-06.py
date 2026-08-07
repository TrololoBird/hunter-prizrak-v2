"""ЗОНД аудита R-02: чувствительность разметки к определению свинга.

ВОПРОС. Курс не определяет, какие экстремумы считаются хаем и лоем. На этом стоят тренд,
истинный ПП, ранний ПП и «последний экстремум». Проект выбрал фрактал Билла Вильямса с
ПЯТЬЮ барами (`swings.FRACTAL_BARS = 5`, то есть k=2 соседа с каждой стороны) и назвал три
независимых источника — MetaTrader 5, thinkorswim, TradingView. Все три сходятся на пяти
барах, но ни один из них не является курсом: это конвенция теханализа, выбранная по §0.2
там, где источник метода молчит.

Замеряется, НАСКОЛЬКО результат зависит от этого выбора. Меняется только определение
свинга; весь остальной код проекта — тот же.

Параметризации (каждая — строка в docs/audit/trials.md):
  * фрактал k = 1, 2 (боевой), 3, 4 — то есть 3, 5, 7, 9 баров;
  * ZigZag с порогом 0.5·ATR14 и 1.0·ATR14 — принципиально другая конвенция, у которой
    экстремумы отбираются по величине хода, а не по числу соседей.

Измеряется: число свингов, число структур, число уровней-ПОК, число переприоров с
разбивкой на истинные и ранние, и — главное — СМЕЩЕНИЕ ЦЕНЫ ПОК относительно боевой
параметризации, в долях ATR.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_swing_sensitivity_2026-08-06.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import detect as detect_accumulations  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.pereprior import PPKind  # noqa: E402
from hunter.pereprior import detect as detect_pp  # noqa: E402
from hunter.swings import Swing, SwingKind, SwingSet, trend  # noqa: E402


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


def fractal(bars: list[Bar], k: int) -> SwingSet:
    """Фрактал с k соседями с каждой стороны. k=2 — боевая параметризация проекта."""
    found: list[Swing] = []
    for i in range(k, len(bars) - k):
        mid = bars[i]
        left, right = bars[i - k:i], bars[i + 1:i + 1 + k]
        if all(b.high < mid.high for b in left + right):
            found.append(Swing(kind=SwingKind.HIGH, index=i, open_ms=mid.open_ms,
                               price=mid.high, confirmed_at_index=i + k))
        if all(b.low > mid.low for b in left + right):
            found.append(Swing(kind=SwingKind.LOW, index=i, open_ms=mid.open_ms,
                               price=mid.low, confirmed_at_index=i + k))
    return SwingSet(swings=tuple(found), bars_scanned=len(bars),
                    confirmed_until_index=max(0, len(bars) - 1 - k))


def zigzag(bars: list[Bar], atr: float, mult: float) -> SwingSet:
    """ZigZag: экстремум фиксируется, когда цена отошла от него на `mult`·ATR.

    Подтверждение назначается тому бару, на котором порог пройден, — иначе разметка
    заглядывала бы вперёд (I-5), и сравнение с фракталом было бы нечестным в её пользу.
    """
    thr = atr * mult
    if thr <= 0 or len(bars) < 3:
        return SwingSet(swings=(), bars_scanned=len(bars), confirmed_until_index=0)
    found: list[Swing] = []
    up = True
    ext_i, ext_p = 0, bars[0].high
    for i in range(1, len(bars)):
        b = bars[i]
        if up:
            if b.high > ext_p:
                ext_i, ext_p = i, b.high
            elif ext_p - b.low >= thr:
                found.append(Swing(kind=SwingKind.HIGH, index=ext_i,
                                   open_ms=bars[ext_i].open_ms, price=ext_p,
                                   confirmed_at_index=i))
                up, ext_i, ext_p = False, i, b.low
        else:
            if b.low < ext_p:
                ext_i, ext_p = i, b.low
            elif b.high - ext_p >= thr:
                found.append(Swing(kind=SwingKind.LOW, index=ext_i,
                                   open_ms=bars[ext_i].open_ms, price=ext_p,
                                   confirmed_at_index=i))
                up, ext_i, ext_p = True, i, b.high
    found.sort(key=lambda s: s.index)
    return SwingSet(swings=tuple(found), bars_scanned=len(bars),
                    confirmed_until_index=max(0, len(bars) - 1))


def main() -> int:
    frames = ROOT / "data" / "frames"
    series: dict[str, dict[str, list[Bar]]] = defaultdict(dict)
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
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

    variants: list[tuple[str, object]] = [
        ("фрактал k=1 (3 бара)", ("f", 1)),
        ("фрактал k=2 (5 баров) ← БОЕВАЯ", ("f", 2)),
        ("фрактал k=3 (7 баров)", ("f", 3)),
        ("фрактал k=4 (9 баров)", ("f", 4)),
        ("ZigZag 0.5·ATR14", ("z", 0.5)),
        ("ZigZag 1.0·ATR14", ("z", 1.0)),
    ]
    agg: dict[str, dict[str, float]] = {n: defaultdict(float) for n, _ in variants}
    # структура: (символ, тф, first_index) → название варианта → (ПОК-суррогат, ATR)
    struct_mid: dict[str, dict[tuple[str, str, int], float]] = {n: {} for n, _ in variants}
    atr_of: dict[tuple[str, str], float] = {}
    rows = 0

    for sym, tfs in sorted(series.items()):
        for tf, bars in sorted(tfs.items(), key=lambda x: TIMEFRAME_MS[x[0]]):
            a = atr14(bars)
            if a is None or a <= 0 or len(bars) < 60:
                continue
            atr_of[(sym, tf)] = a
            rows += 1
            for name, spec in variants:
                kind, val = spec  # type: ignore[misc]
                sw = (fractal(bars, int(val)) if kind == "f"
                      else zigzag(bars, a, float(val)))
                scan = detect_accumulations(bars, sw, tf)
                pps = detect_pp(bars, sw, tf)
                t = trend(sw)
                d = agg[name]
                d["рядов"] += 1
                d["свингов"] += len(sw.swings)
                d["структур"] += len(scan.closed)
                d["распадов"] += scan.resets
                d["ПП всего"] += len(pps)
                d["ПП истинных"] += sum(1 for p in pps if p.kind is PPKind.TRUE)
                d["ПП ранних"] += sum(1 for p in pps if p.kind is PPKind.EARLY)
                d["тренд определён"] += 1 if t.direction.value != "none" else 0
                for acc in scan.closed:
                    mid = (acc.upper.hi + acc.lower.lo) / 2
                    struct_mid[name][(sym, tf, acc.first_index)] = mid

    print("=" * 78)
    print("R-02  Чувствительность разметки к определению свинга")
    print("=" * 78)
    print(f"рядов (символ × ТФ): {rows}")
    print()
    hdr = ("вариант", "свингов", "структур", "распадов", "ПП", "истин", "ранних", "тренд")
    print(f"{hdr[0]:<30}{hdr[1]:>9}{hdr[2]:>10}{hdr[3]:>10}{hdr[4]:>6}{hdr[5]:>7}"
          f"{hdr[6]:>8}{hdr[7]:>7}")
    base = "фрактал k=2 (5 баров) ← БОЕВАЯ"
    for name, _ in variants:
        d = agg[name]
        print(f"{name:<30}{d['свингов']:>9.0f}{d['структур']:>10.0f}{d['распадов']:>10.0f}"
              f"{d['ПП всего']:>6.0f}{d['ПП истинных']:>7.0f}{d['ПП ранних']:>8.0f}"
              f"{d['тренд определён']:>7.0f}")

    print()
    print("ОТНОШЕНИЕ К БОЕВОЙ ПАРАМЕТРИЗАЦИИ (во сколько раз)")
    b = agg[base]
    for name, _ in variants:
        if name == base:
            continue
        d = agg[name]
        def r(k: str) -> str:
            return f"{d[k]/b[k]:.2f}×" if b[k] else "—"
        print(f"  {name:<28} свингов {r('свингов'):>7}  структур {r('структур'):>7}  "
              f"ПП {r('ПП всего'):>7}  истинных {r('ПП истинных'):>7}")

    print()
    print("СОВПАДАЮТ ЛИ САМИ СТРУКТУРЫ (одинаковый первый бар) и где встают их середины")
    for name, _ in variants:
        if name == base:
            continue
        common = set(struct_mid[base]) & set(struct_mid[name])
        only_base = len(struct_mid[base]) - len(common)
        only_this = len(struct_mid[name]) - len(common)
        if not common:
            print(f"  {name:<28} общих структур НЕТ "
                  f"(у боевой {only_base}, у этой {only_this})")
            continue
        dev = []
        for key in common:
            a = atr_of.get((key[0], key[1]))
            if a:
                dev.append(abs(struct_mid[base][key] - struct_mid[name][key]) / a)
        med = statistics.median(dev) if dev else float("nan")
        print(f"  {name:<28} общих {len(common):>4}  только у боевой {only_base:>4}  "
              f"только у этой {only_this:>4}  медиана сдвига середины {med:.3f} ATR")

    print()
    print("КОНТРОЛЬ: способен ли замер дать иной ответ?")
    uniq = {round(agg[n]['структур']) for n, _ in variants}
    print(f"   различных значений «структур» среди {len(variants)} вариантов: {len(uniq)}")
    print("   ✓ пройден: варианты дают разные числа" if len(uniq) > 1
          else "   ⚠ НЕ ПРОЙДЕН: все варианты дают одно число")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
