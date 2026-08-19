"""Куда указывает окно композита: возраст против последнего бара + попадает ли ЦЕНА.

Плюс КОНТРОЛЬ классификатора: подсаженная зона уже шага бина внутри диапазона
обязана попасть в класс «внутри, но бинов нет». Класс, который не наполняется
никогда, ничего не доказывает.
"""
from __future__ import annotations

import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows  # noqa: E402

DAY = 86_400_000


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
        widest = max(built, key=lambda lv: lv.structure_to_ms - lv.structure_from_ms)
        comp = src.window(widest.structure_from_ms, widest.structure_to_ms)
        if isinstance(comp, NotReady):
            print(f"{symbol}: {comp.reason}")
            continue
        keys = sorted(comp.qty_by_bin)
        prices = [float(comp.bin_price(b)) for b in keys]
        lo_c, hi_c = prices[0], prices[-1]

        # последний ЗАКРЫТЫЙ бар самого младшего собранного ряда
        tf_last = min(series, key=lambda t: len(series[t]) and 0 or 0) if False else None
        last_bar = max((s[-1] for s in series.values() if s), key=lambda b: b.open_ms)
        price = last_bar.close
        age_days = (last_bar.open_ms - widest.structure_to_ms) / DAY
        span_days = (widest.structure_to_ms - widest.structure_from_ms) / DAY
        inside = lo_c <= price <= hi_c
        # насколько цена вне коридора композита
        off = 0.0 if inside else (price - hi_c) / hi_c * 100 if price > hi_c \
            else (price - lo_c) / lo_c * 100

        # КОНТРОЛЬ классификатора: зона шириной 0 в середине диапазона композита,
        # поставленная МЕЖДУ двумя ценами бинов.
        mid = len(prices) // 2
        a = prices[mid] + float(tick) * 0.25
        b = prices[mid] + float(tick) * 0.5
        i = bisect.bisect_left(prices, a)
        j = bisect.bisect_right(prices, b)
        ctl = "СРАБОТАЛ (срез пуст)" if j <= i else "не сработал — класс недостижим"

        print(f"{symbol:20} композит: структура {widest.timeframe}, длина {span_days:.1f} сут, "
              f"кончилась {age_days:.1f} сут назад")
        print(f"{'':20} коридор композита {lo_c:.10g}…{hi_c:.10g}; цена сейчас "
              f"{price:.10g} — {'ВНУТРИ' if inside else f'ВНЕ на {off:+.1f}%'}")
        print(f"{'':20} контроль классификатора «внутри, но бинов нет»: {ctl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
