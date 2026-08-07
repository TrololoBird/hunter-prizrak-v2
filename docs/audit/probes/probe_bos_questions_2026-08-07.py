"""ЗОНД: три вопроса о сломе структуры, оставшиеся после обзора 10 чужих проектов.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-bos.md,
sha256 a838645d4637b8629c4e19169bdd5583414c6ec39ac0b46ec98e20279218bcac.

Расчёт НЕ меняется: альтернативные определения считаются РЯДОМ с текущим.

  Д1  с какого края зоны считать пробой — дальний (наш), ближний, случайный (нуль)
  Д2  ретест по ПЕРЕСЕЧЕНИЮ диапазона (наш) против ЗАКРЫТИЯ в зоне (семейство A обзора)
  Д3  сколько подтверждённых ПП на сторону существует против одного возвращаемого

КОНТРОЛИ:
  * Д1 — нуль: край, взятый случайно внутри зоны. Проверяется, что он ломает свойство.
  * Д2 — К-1: подсаженный бар, задевающий зону тенью и закрывающийся вне, обязан развести
    два определения.
  * Д3 — К-1: счётчик обязан уметь дать больше единицы.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_bos_questions_2026-08-07.py
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.breach import CONFIRM_BODIES, BreachKind, Direction, first_breach  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.pereprior import PPSide, _shadow_zone  # noqa: E402
from hunter.swings import SwingKind, detect as detect_swings  # noqa: E402

SEED = 20260807


def load() -> dict[tuple[str, str], list[Bar]]:
    out: dict[tuple[str, str], list[Bar]] = {}
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
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
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in pl.read_parquet(f).iter_rows(named=True)]
                if len(bars) > len(out.get((sym, tf), [])):
                    out[(sym, tf)] = bars
    return out


def count_pp(bars: list[Bar], tf: str, edge_mode: str, rnd: random.Random) -> tuple[int, list]:
    """Все подтверждённые ПП обеих сторон при заданном выборе края зоны.

    edge_mode: 'far' (наш), 'near' (граница тела), 'random' (нуль).
    Возвращает (число, список (side, broken_index, lo, hi, confirmed_index)).
    """
    sw = detect_swings(bars)
    if not hasattr(sw, "swings"):
        return 0, []
    found: list = []
    for side in (PPSide.SHORT, PPSide.LONG):
        broken_kind = SwingKind.LOW if side is PPSide.SHORT else SwingKind.HIGH
        other_kind = SwingKind.HIGH if side is PPSide.SHORT else SwingKind.LOW
        direction = Direction.BELOW if side is PPSide.SHORT else Direction.ABOVE
        ordered = sorted(sw.swings, key=lambda s: s.index)
        others = [s for s in ordered if s.kind is other_kind]
        if len(others) < 2:
            continue
        for i in range(1, len(others)):
            last_other = others[i]
            cands = [s for s in ordered
                     if s.kind is broken_kind and s.index < last_other.index]
            if not cands:
                continue
            broken = cands[-1]
            end = others[i + 1].confirmed_at_index if i + 1 < len(others) else len(bars)
            start = max(broken.confirmed_at_index + 1, last_other.confirmed_at_index)
            if start >= end:
                continue
            lo, hi = _shadow_zone(bars[broken.index], broken_kind)
            far = lo if side is PPSide.SHORT else hi
            near = hi if side is PPSide.SHORT else lo
            if edge_mode == "far":
                edge = far
            elif edge_mode == "near":
                edge = near
            else:
                edge = rnd.uniform(min(lo, hi), max(lo, hi))
            ev = first_breach(bars[:end], edge, direction, tf, from_index=start,
                              confirm_bodies=CONFIRM_BODIES)
            if ev is None or ev.kind is not BreachKind.BREAKOUT or ev.resolved_index is None:
                continue
            found.append((side, broken.index, lo, hi, ev.resolved_index))
    return len(found), found


def test_cross(bars: list[Bar], lo: float, hi: float, frm: int) -> int | None:
    for i in range(frm, len(bars)):
        if bars[i].low <= hi and bars[i].high >= lo:
            return i
    return None


def test_close(bars: list[Bar], lo: float, hi: float, frm: int) -> int | None:
    for i in range(frm, len(bars)):
        if lo <= bars[i].close <= hi:
            return i
    return None


def main() -> int:
    data = load()
    if not data:
        print("КАДРОВ НЕТ — НЕ СМОГ ПРОВЕРИТЬ")
        return 1
    print("=" * 78)
    print("Слом структуры: три вопроса после обзора 10 чужих проектов")
    print("=" * 78)
    print(f"кадров: {len(data)} рядов")
    print()

    far = near = null = 0
    diff_rows = 0
    per_side: list[int] = []
    cross_ne_close = 0
    total_pp = 0
    by_tf: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    rnd = random.Random(SEED)

    for (sym, tf), bars in sorted(data.items()):
        f, rows = count_pp(bars, tf, "far", rnd)
        n, _ = count_pp(bars, tf, "near", rnd)
        z, _ = count_pp(bars, tf, "random", rnd)
        far += f
        near += n
        null += z
        if f != z:
            diff_rows += 1
        by_tf[tf][0] += f
        by_tf[tf][1] += n
        for side in (PPSide.SHORT, PPSide.LONG):
            per_side.append(sum(1 for r in rows if r[0] is side))
        for _side, _bi, lo, hi, conf in rows:
            total_pp += 1
            if test_cross(bars, lo, hi, conf + 1) != test_close(bars, lo, hi, conf + 1):
                cross_ne_close += 1

    print("--- Д1. С какого края зоны считать пробой ---")
    print(f"  ДАЛЬНИЙ край (наш):   ПП {far}")
    print(f"  БЛИЖНИЙ край:         ПП {near}")
    d = abs(near - far) / far * 100 if far else 0.0
    print(f"  разница: {d:.1f}%")
    print(f"  порог: <5 закрыть · 5-25 записать · >=25 предъявить владельцу")
    print(f"  ВЕРДИКТ: {'ЗАКРЫТЬ' if d < 5 else 'ЗАПИСАТЬ' if d < 25 else 'ПРЕДЪЯВИТЬ'}")
    print()
    print(f"  НУЛЬ (случайный край внутри зоны): ПП {null}")
    print(f"    ломает ли нуль свойство: число ПП отличается от нашего "
          f"у {diff_rows} рядов из {len(data)}")
    if diff_rows == 0:
        print("    ⚠ НУЛЬ НЕ ЛОМАЕТ СВОЙСТВО — сравнивать нечего, замер Д1 недействителен")
    else:
        nd = abs(null - far) / far * 100 if far else 0.0
        print(f"    отличие нуля от нашего: {nd:.1f}% (у ближнего края {d:.1f}%)")
        print(f"    {'✗ выбор края не отличим от случайного' if nd >= d else '✓ выбор края даёт больше, чем случайный'}")
    print()

    print("--- Д2. Ретест: пересечение против закрытия в зоне ---")
    share = cross_ne_close / total_pp * 100 if total_pp else 0.0
    print(f"  ПП всего: {total_pp}; бар теста РАЗЛИЧАЕТСЯ: {cross_ne_close} ({share:.1f}%)")
    print(f"  ВЕРДИКТ: {'ЗАКРЫТЬ' if share < 5 else 'ЗАПИСАТЬ' if share < 25 else 'ПРЕДЪЯВИТЬ'}")
    print()

    print("--- Д3. Сколько ПП на сторону существует ---")
    if per_side:
        print(f"  среднее {statistics.mean(per_side):.2f}, максимум {max(per_side)}, "
              f"рядов-сторон {len(per_side)}")
        print(f"  порог: <=1.5 закрыть · >1.5 назвать число вслух")
        print(f"  ВЕРДИКТ: "
              f"{'ЗАКРЫТЬ' if statistics.mean(per_side) <= 1.5 else 'НАЗВАТЬ ЧИСЛО'}")
    print()

    print("--- Сводка по ТФ (измерение, вдоль которого возможен перекос) ---")
    print(f"  {'ТФ':<5} {'ПП дальний':>11} {'ПП ближний':>11}")
    for tf in sorted(by_tf, key=lambda t: TIMEFRAME_MS[t]):
        a, b = by_tf[tf]
        print(f"  {tf:<5} {a:>11} {b:>11}")
    print()

    print("=" * 78)
    print("КОНТРОЛЬ К-1")
    probe = [Bar(open_ms=i * 300_000, open=100.0, high=101.0, low=99.0,
                 close=100.0, volume=1.0) for i in range(5)]
    # бар задевает зону 105…106 тенью, закрывается вне её
    probe[2] = Bar(open_ms=2 * 300_000, open=100.0, high=105.5, low=99.0,
                   close=100.0, volume=1.0)
    c1 = test_cross(probe, 105.0, 106.0, 0)
    c2 = test_close(probe, 105.0, 106.0, 0)
    print(f"  Д2: тень задевает зону, закрытие вне — пересечение={c1}, закрытие={c2} "
          f"{'✓ определения расходятся' if c1 != c2 else '✗ ПРИБОР ИХ НЕ РАЗВОДИТ'}")
    print(f"  Д3: максимум ПП на сторону {max(per_side) if per_side else 0} "
          f"{'✓ счётчик не заперт в единице' if per_side and max(per_side) > 1 else '✗ СЧЁТЧИК ВСЕГДА ДАЁТ 1'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
