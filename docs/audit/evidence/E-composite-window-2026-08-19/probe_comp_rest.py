"""Остаток: что за 4 строки печатают 0.0% при ненулевой зоне."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import card, engine, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows  # noqa: E402

COMP = re.compile(r"сила по композиту .*?зона ([\d.eE+-]+) — ([\d.]+)% композита")

for d in store.saved_symbols("last"):
    meta = store.read_meta("last", d)
    if isinstance(meta, NotReady):
        continue
    symbol, tick, _ = meta
    tfs = store.saved_timeframes("last", d)
    series = {tf: store.read_bars("last", d, tf) for tf in tfs}
    ps = dict(series)
    ps.update(store.read_profile_bars("last", d))
    text = card.render(engine.decide(symbol, series, TVWindows(symbol, tick, ps), tfs),
                       series)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = COMP.search(line)
        if m and float(m.group(2)) == 0.0:
            zone = float(m.group(1))
            # строка уровня — выше по карточке
            head = next((lines[j].strip() for j in range(i - 1, max(0, i - 6), -1)
                         if "ПОК" in lines[j]), "?")
            print(f"{symbol}: зона {zone:g} — {head[:110]}")
