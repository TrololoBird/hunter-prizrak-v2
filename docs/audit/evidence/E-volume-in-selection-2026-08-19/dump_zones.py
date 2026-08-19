"""Выгрузить ZoneSpec из `decide` на сохранённых кадрах в JSON — чтобы разбор отбора
не требовал 10-минутного пересчёта на каждый вопрос."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, store, tgbot
from hunter.bars import TIMEFRAME_MS
from hunter.models import NotReady
from hunter.profile_source import TVWindows

OUT = Path(sys.argv[1])


def main() -> int:
    run_id = "last"
    data = {}
    for d in store.saved_symbols(run_id):
        meta = store.read_meta(run_id, d)
        if isinstance(meta, NotReady):
            continue
        symbol, tick, _ = meta
        tfs = store.saved_timeframes(run_id, d)
        if not tfs:
            continue
        series = {tf: store.read_bars(run_id, d, tf) for tf in tfs}
        manifest = store.read_source_meta(run_id, d)
        if isinstance(manifest, NotReady):
            continue
        ps = dict(series)
        ps.update(store.read_profile_bars(run_id, d))
        dec = engine.decide(symbol, series, TVWindows(symbol, tick, ps), tfs)
        fastest = min(series, key=lambda tf: TIMEFRAME_MS[tf])
        if not series[fastest]:
            continue
        last = series[fastest][-1]
        data[symbol] = {
            "price": float(last.close), "now_ms": int(last.open_ms),
            "zones": [asdict(z) for z in tgbot.zones_of(dec, 0)],
        }
        print(f"  {symbol}: зон {len(data[symbol]['zones'])}", flush=True)
    OUT.write_text(json.dumps(data), encoding="utf-8")
    print(f"записано в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
