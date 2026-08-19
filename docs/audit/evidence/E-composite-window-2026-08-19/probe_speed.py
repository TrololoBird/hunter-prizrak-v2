"""Цена правки по времени: decide на BTC, новый расчёт против прежнего окна.

Прежнее окно воспроизводится ЗДЕСЬ же (окно самой длинной структуры) — чтобы
сравнение шло на одной машине и одних кадрах, а не с числом из другого дня.
"""
from __future__ import annotations

import bisect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows  # noqa: E402

d = "BTC_USDT_USDT"
symbol, tick, _ = store.read_meta("last", d)  # type: ignore[misc]
tfs = store.saved_timeframes("last", d)
series = {tf: store.read_bars("last", d, tf) for tf in tfs}
ps = dict(series)
ps.update(store.read_profile_bars("last", d))

for label in ("прогрев", "замер"):
    src = TVWindows(symbol, tick, ps)
    t0 = time.perf_counter()
    dec = engine.decide(symbol, series, src, tfs)
    dt = time.perf_counter() - t0
    print(f"{label}: decide целиком {dt:.2f} с, уровней {len(dec.decisions)}")

# отдельно — сама сортировка/срез композита, обе редакции окна
built = [x.level for x in dec.decisions]
src = TVWindows(symbol, tick, ps)
last_ms = max(s[-1].open_ms for s in series.values() if s)
oldest = min(lv.structure_from_ms for lv in built)
widest = max(built, key=lambda lv: lv.structure_to_ms - lv.structure_from_ms)

for name, lo, hi in (("прежнее окно (widest)", widest.structure_from_ms, widest.structure_to_ms),
                     ("новое окно (видимая область)", oldest, last_ms)):
    t0 = time.perf_counter()
    comp = src.window(lo, hi)
    if isinstance(comp, NotReady):
        print(f"{name}: отказ — {comp.reason[:60]}")
        continue
    keys = sorted(comp.qty_by_bin)
    prices = [float(comp.bin_price(b)) for b in keys]
    for lv in built:
        i = bisect.bisect_left(prices, float(lv.zone_lo))
        j = bisect.bisect_right(prices, float(lv.zone_hi))
        _ = sum(comp.qty_by_bin[b] for b in keys[i:j])
    print(f"{name}: бинов {len(keys)}, весь композит на {len(built)} уровней "
          f"{time.perf_counter() - t0:.2f} с")
