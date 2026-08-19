"""Почему срез композита пуст: разбор ПО ШАГАМ, а не правдоподобная причина.

Для каждого уровня печатается, где лежит его зона относительно ЦЕНОВОГО диапазона
композита и сколько бинов попало в срез. Классы взаимоисключающие, сумма сверяется
со знаменателем.
"""
from __future__ import annotations

import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, levels, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows  # noqa: E402


def one(run_id: str, dir_name: str) -> None:
    meta = store.read_meta(run_id, dir_name)
    if isinstance(meta, NotReady):
        print(f"{dir_name}: {meta.reason}")
        return
    symbol, tick, _ = meta
    tfs = store.saved_timeframes(run_id, dir_name)
    series = {tf: store.read_bars(run_id, dir_name, tf) for tf in tfs}
    manifest = store.read_source_meta(run_id, dir_name)
    if isinstance(manifest, NotReady):
        print(f"{dir_name}: {manifest.reason}")
        return
    profile_series = dict(series)
    profile_series.update(store.read_profile_bars(run_id, dir_name))
    src = TVWindows(symbol, tick, profile_series)

    dec = engine.decide(symbol, series, src, tfs)
    built = [d.level for d in dec.decisions]
    if not built:
        print(f"{symbol}: уровней нет")
        return

    widest = max(built, key=lambda lv: lv.structure_to_ms - lv.structure_from_ms)
    comp = src.window(widest.structure_from_ms, widest.structure_to_ms)
    if isinstance(comp, NotReady):
        print(f"{symbol}: композит не прочитан — {comp.reason}")
        return
    keys = sorted(comp.qty_by_bin)
    prices = [float(comp.bin_price(b)) for b in keys]
    lo_c, hi_c = prices[0], prices[-1]
    step = float(tick)

    cls = {"ниже композита": 0, "выше композита": 0,
           "внутри, но бинов нет": 0, "срез непустой": 0}
    narrow = 0
    examples: list[str] = []
    for lv in built:
        lo, hi = float(lv.zone_lo), float(lv.zone_hi)
        i = bisect.bisect_left(prices, lo)
        j = bisect.bisect_right(prices, hi)
        if j > i:
            cls["срез непустой"] += 1
            continue
        if hi < lo_c:
            k = "ниже композита"
        elif lo > hi_c:
            k = "выше композита"
        else:
            k = "внутри, но бинов нет"
            if hi - lo < step:
                narrow += 1
        cls[k] += 1
        if len(examples) < 3 and k != "срез непустой":
            examples.append(
                f"    {lv.timeframe:>3} зона {lo:.10g}…{hi:.10g} (ширина {hi-lo:.3g}, "
                f"тик {step:.3g}) — {k}")

    print(f"\n{symbol}: уровней {len(built)}; композит — структура "
          f"{widest.timeframe}, бинов {len(keys)}, цены {lo_c:.10g}…{hi_c:.10g}, "
          f"окно {widest.structure_from_ms}..{widest.structure_to_ms}")
    tot = sum(cls.values())
    for k, v in cls.items():
        print(f"  {k:22} {v:5d}  ({v / tot * 100:.1f}%)")
    print(f"  из «внутри, но бинов нет» зона УЖЕ шага бина: {narrow}")
    for e in examples:
        print(e)


def main() -> int:
    run_id = "last"
    for d in store.saved_symbols(run_id):
        one(run_id, d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
