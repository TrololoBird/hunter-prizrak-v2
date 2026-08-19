"""Контроль правки: строка о пробитых появляется, когда они есть, и молчит, когда их нет."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import clock, tgbot
from hunter.models import NotReady

clock.set_sync(clock.ClockSync(offset_ms=0, rtt_ms=0, samples=1,
                               measured_at_local_ms=clock.local_ms(),
                               measured_at_monotonic_ns=clock.monotonic_ns()))
now = clock.now_ms()
m = tgbot.read_map("BTC/USDT:USDT")
if isinstance(m, NotReady):
    print("ОТКАЗ:", m.reason)
    raise SystemExit(1)
price = sorted(z.price for z in m.zones)[len(m.zones) // 2]

live, uniq, off, flip = tgbot.live_unique(m.zones, price, now)
print(f"зон в карте {len(m.zones)}: живых {len(live)}, показано {len(uniq)}, "
      f"вне кадра {len(off)}, ПРОБИТЫХ {len(flip)}")

txt = tgbot.compose_text("BTC/USDT:USDT", m.zones, [], "проба", price=price, now_ms=now)
line = [x for x in txt.splitlines() if "пробитых не в списке" in x]
print("\nЕСТЬ ФЛИПНУТЫЕ →", line[0].strip() if line else "СТРОКИ НЕТ — правка не работает")

# КОНТРОЛЬ: та же карта без единого флипнутого — строки быть НЕ ДОЛЖНО.
only_active = tuple(z for z in m.zones if z.state != "flipped")
txt2 = tgbot.compose_text("BTC/USDT:USDT", only_active, [], "проба", price=price, now_ms=now)
has = any("пробитых не в списке" in x for x in txt2.splitlines())
print("КОНТРОЛЬ, флипнутых нет →", "СТРОКА ЕСТЬ — прибор врёт" if has else "строки нет, верно")

# КОНТРОЛЬ 2: подсунуть флипнутый там, где его не было, — число обязано вырасти.
extra = dataclasses.replace(next(z for z in m.zones if z.state == "active"), state="flipped")
_, _, _, flip3 = tgbot.live_unique((*only_active, extra), price, now)
print(f"КОНТРОЛЬ 2, подсажен один флипнутый → насчитано {len(flip3)} "
      f"({'верно' if len(flip3) == 1 else 'ПРИБОР ЗАПЕРТ'})")
