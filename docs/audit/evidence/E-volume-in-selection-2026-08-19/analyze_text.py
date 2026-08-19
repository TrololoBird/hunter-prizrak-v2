"""Цена правки НА АРТЕФАКТЕ — на тексте сообщения бота, который читает владелец.

«Как было» получается обнулением доли: при нулевой доле новый ключ отбора вырождается в
прежний (доказано поимённо в `probe_volume_pick.py`, контроль 1), строки силы не
печатаются, хвост и скрытое не называются.
"""
from __future__ import annotations

import difflib
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import tgbot
from hunter.render import ZoneSpec

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
show = sys.argv[2] if len(sys.argv) > 2 else ""
plus = minus = 0

for symbol, d in sorted(data.items()):
    zones = [ZoneSpec(**z) for z in d["zones"]]
    price, now_ms = d["price"], d["now_ms"]
    zeroed = [replace(z, vrvp_density=0.0) for z in zones]
    new = tgbot.compose_text(symbol, tuple(zones), [], "кадры", price=price,
                             now_ms=now_ms).splitlines()
    old = tgbot.compose_text(symbol, tuple(zeroed), [], "кадры", price=price,
                             now_ms=now_ms).splitlines()
    dl = [ln for ln in difflib.unified_diff(old, new, n=0)
          if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    plus += sum(1 for ln in dl if ln.startswith("+"))
    minus += sum(1 for ln in dl if ln.startswith("-"))
    print(f"  {symbol:16} строк было {len(old):2} стало {len(new):2}; "
          f"изменилось +{sum(1 for ln in dl if ln.startswith('+'))}"
          f"/-{sum(1 for ln in dl if ln.startswith('-'))}")
    if show and show in symbol:
        print("\n───────── БЫЛО ─────────")
        print("\n".join(old))
        print("\n───────── СТАЛО ─────────")
        print("\n".join(new))
        print("────────────────────────\n")

print(f"\n  всего строк: +{plus} / -{minus}")
