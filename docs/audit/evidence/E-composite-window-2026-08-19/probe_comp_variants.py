"""Три варианта окна композита на ОДНИХ И ТЕХ ЖЕ кадрах: сколько зон покрыто.

A — как сейчас: [начало самой ДОЛГОЙ структуры … её конец]
B — «видимая область»: [начало самой ДОЛГОЙ структуры … последний бар]
C — «видимая область» шире: [начало САМОЙ СТАРОЙ структуры … последний бар]

Печатается покрытие зон и ОТКАЗ, если окно не читается: вариант, отказывающий по
всем уровням сразу, — не победа, а другой способ не ответить.
"""
from __future__ import annotations

import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows, intrabar_timeframe  # noqa: E402

DAY = 86_400_000


def covered(src: TVWindows, lo_ms: int, hi_ms: int, built: list) -> str:
    comp = src.window(lo_ms, hi_ms)
    tf = intrabar_timeframe(hi_ms - lo_ms)
    if isinstance(comp, NotReady):
        return f"ОТКАЗ ({tf}): {comp.reason[:90]}"
    keys = sorted(comp.qty_by_bin)
    prices = [float(comp.bin_price(b)) for b in keys]
    hit = 0
    for lv in built:
        i = bisect.bisect_left(prices, float(lv.zone_lo))
        j = bisect.bisect_right(prices, float(lv.zone_hi))
        if j > i:
            hit += 1
    return (f"свечи {tf}, окно {(hi_ms - lo_ms) / DAY:.0f} сут, бинов {len(keys)}, "
            f"коридор {prices[0]:.8g}…{prices[-1]:.8g} — покрыто "
            f"{hit}/{len(built)} ({hit / len(built) * 100:.1f}%)")


def main() -> int:
    run_id = "last"
    for d in store.saved_symbols(run_id):
        meta = store.read_meta(run_id, d)
        if isinstance(meta, NotReady):
            continue
        symbol, tick, _ = meta
        tfs = store.saved_timeframes(run_id, d)
        series = {tf: store.read_bars(run_id, d, tf) for tf in tfs}
        profile_series = dict(series)
        profile_series.update(store.read_profile_bars(run_id, d))
        src = TVWindows(symbol, tick, profile_series)
        dec = engine.decide(symbol, series, src, tfs)
        built = [x.level for x in dec.decisions]
        if not built:
            continue
        last_bar = max((s[-1] for s in series.values() if s), key=lambda b: b.open_ms)
        widest = max(built, key=lambda lv: lv.structure_to_ms - lv.structure_from_ms)
        oldest = min(lv.structure_from_ms for lv in built)
        print(f"\n{symbol}  уровней {len(built)}, последний бар {last_bar.open_ms}")
        print(f"  A как сейчас : {covered(src, widest.structure_from_ms, widest.structure_to_ms, built)}")
        print(f"  B до сейчас  : {covered(src, widest.structure_from_ms, last_bar.open_ms, built)}")
        print(f"  C от старейшей: {covered(src, oldest, last_bar.open_ms, built)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
