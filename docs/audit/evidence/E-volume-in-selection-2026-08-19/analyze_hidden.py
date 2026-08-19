"""Что отбор выбрасывает ПО СИЛЕ. Ничья по ТФ = 0, значит дедубликация объёму голоса не
даёт; настоящий отсев — `near_structure` (376 активных → 36). Вопрос: попадают ли под
него уровни СИЛЬНЕЕ показанных, и насколько.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import tgbot
from hunter.render import ZoneSpec

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tot = {"активных": 0, "скрыто возрастом": 0,
       "скрытых сильнее ЛЮБОГО показанного": 0,
       "символов, где сильнейший актив скрыт": 0}

for symbol, d in sorted(data.items()):
    zones = [ZoneSpec(**z) for z in d["zones"]]
    price, now_ms = d["price"], d["now_ms"]
    live, unique, off = tgbot.live_unique(tuple(zones), price, now_ms)
    alive = [z for z in zones if z.state == "active"]
    shown_max = max((z.vrvp_density for z in unique), default=0.0)
    stronger = [z for z in off if z.vrvp_density > shown_max]
    off_max = max((z.vrvp_density for z in off), default=0.0)
    hid_best = max(off, key=lambda z: z.vrvp_density, default=None)
    print(f"  {symbol:16} активных {len(alive):4}  показано {len(unique):2} "
          f"(сильнейшая ×{shown_max:5.2f})  скрыто возрастом {len(off):4} "
          f"(сильнейшая ×{off_max:5.2f})  скрытых сильнее показанного: {len(stronger):3}")
    if hid_best is not None and hid_best.vrvp_density > shown_max:
        age_bars = "?"
        print(f"                   сильнейший СКРЫТ: {hid_best.side} "
              f"{hid_best.timeframe} цена {hid_best.price:.6g} доля "
              f"{hid_best.vrvp_density:.2f}% — против {shown_max:.2f}% у лучшего показанного")
    tot["активных"] += len(alive)
    tot["скрыто возрастом"] += len(off)
    tot["скрытых сильнее ЛЮБОГО показанного"] += len(stronger)
    tot["символов, где сильнейший актив скрыт"] += bool(stronger)

print()
for k, v in tot.items():
    print(f"  {k}: {v}")
print("\nКОНТРОЛЬ: если бы фильтр возраста был безразличен к силе, доля скрытых сильнее"
      "\nпоказанного была бы около нуля; она считается от ЗНАМЕНАТЕЛЯ скрытых.")
