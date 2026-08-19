"""РАЗБОР НУЛЯ: почему сила по объёму ни разу не изменила отбор.

Правило проекта: ноль разбирается по шагам, а не объясняется правдоподобно. Прибор
считает воронку отбора и, главное, СКОЛЬКО РАЗ у объёма вообще был голос — то есть
сколько раз два уровня встречались на ОДНОМ ТФ, где прежний ключ решал ничью шириной
зоны либо порядком строк.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import tgbot
from hunter.bars import TIMEFRAME_MS
from hunter.render import ZoneSpec

ORDER = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

tot = {"зон": 0, "активных": 0, "у структуры": 0, "после склейки по цене": 0,
       "после склейки по зоне": 0,
       # ⚠ ключи РАЗНЫЕ. В первой редакции оба звались «из них ничья по ТФ», и в
       # словаре оставался ОДИН: ничьи по цене и по зоне складывались в общий счётчик.
       # Поймал ruff (F601). Вывод «ничьих по ТФ ноль» устоял: сумма двух
       # неотрицательных величин равна нулю только когда нули обе.
       "ключей цены с ≥2 претендентами": 0, "ничья по ТФ (цена)": 0,
       "ничья решена объёмом иначе": 0,
       "пар склейки по зоне": 0, "ничья по ТФ (зона)": 0,
       "ничья зоны решена объёмом иначе": 0}

for symbol, d in sorted(data.items()):
    zones = [ZoneSpec(**z) for z in d["zones"]]
    price, now_ms = d["price"], d["now_ms"]
    alive = [z for z in zones if z.state == "active"]
    live = [z for z in alive if tgbot.near_structure(z, now_ms)]

    # --- Проход 1: склейка по строке цены.
    buckets: dict[tuple[str, str], list[ZoneSpec]] = {}
    for z in live:
        buckets.setdefault((z.side, tgbot._fmt_price(z.price)), []).append(z)
    multi = [v for v in buckets.values() if len(v) > 1]
    ties, ties_diff = 0, 0
    for cand in multi:
        top = max(ORDER.get(z.timeframe, 0) for z in cand)
        at_top = [z for z in cand if ORDER.get(z.timeframe, 0) == top]
        if len(at_top) < 2:
            continue
        ties += 1
        old_keep = at_top[0]                       # прежнее правило: первый встреченный
        new_keep = max(at_top, key=lambda z: z.vrvp_density)
        ties_diff += new_keep is not old_keep

    best = {k: max(v, key=lambda z: (ORDER.get(z.timeframe, 0), z.vrvp_density))
            for k, v in buckets.items()}

    # --- Проход 2: склейка по взаимному вложению.
    pairs, pair_ties, pair_diff = 0, 0, 0
    kept: list[ZoneSpec] = []
    for z in sorted(best.values(), key=lambda z: (-ORDER.get(z.timeframe, 0),
                                                  -z.vrvp_density,
                                                  -(z.zone_hi - z.zone_lo))):
        hit = [k for k in kept if k.side == z.side and k.zone_lo <= z.price <= k.zone_hi
               and z.zone_lo <= k.price <= z.zone_hi]
        if hit:
            pairs += 1
            k = hit[0]
            if ORDER.get(k.timeframe, 0) == ORDER.get(z.timeframe, 0):
                pair_ties += 1
                # Прежний ключ оставил бы ту, что ШИРЕ; новый — ту, у кого доля больше.
                by_width = max((k, z), key=lambda x: x.zone_hi - x.zone_lo)
                by_vol = max((k, z), key=lambda x: x.vrvp_density)
                pair_diff += by_width is not by_vol
            continue
        kept.append(z)

    shares = sorted((z.vrvp_density for z in live), reverse=True)
    sel = sorted((z.vrvp_density for z in kept), reverse=True)
    print(f"  {symbol:16} зон {len(zones):5} → активных {len(alive):5} → у структуры "
          f"{len(live):4} → по цене {len(best):3} → по зоне {len(kept):3}   "
          f"ничьих ТФ: цена {ties}, зона {pair_ties}   иначе: {ties_diff}/{pair_diff}")
    print(f"                   доли живых: макс {shares[0]:.2f}% медиана "
          f"{shares[len(shares)//2]:.2f}%; доли отобранных: "
          f"{', '.join(f'{x:.2f}' for x in sel)}")
    tot["зон"] += len(zones)
    tot["активных"] += len(alive)
    tot["у структуры"] += len(live)
    tot["после склейки по цене"] += len(best)
    tot["после склейки по зоне"] += len(kept)
    tot["ключей цены с ≥2 претендентами"] += len(multi)
    tot["ничья по ТФ (цена)"] += ties
    tot["ничья решена объёмом иначе"] += ties_diff
    tot["пар склейки по зоне"] += pairs
    tot["ничья по ТФ (зона)"] += pair_ties
    tot["ничья зоны решена объёмом иначе"] += pair_diff

print()
for k, v in tot.items():
    print(f"  {k}: {v}")
