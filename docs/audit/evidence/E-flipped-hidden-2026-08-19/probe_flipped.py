"""Сколько уровней бот НЕ ПОКАЗЫВАЕТ, хотя курс их торговать разрешает.

Стр. 43: пробитый уровень «Уровень лонг/шорт менятся для нас на противоположный», и
расчёт это знает — `entry_rule=retest_flipped`. Карточка такой уровень печатает, бот —
нет: `live_unique` берёт только `state == "active"`.

КОНТРОЛЬ: те же фильтры (`near_structure`, дедубликация) применяются к обеим выборкам.
Если бы прибор был заперт, обе колонки совпали бы; расхождение и есть замер.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import tgbot
from hunter.models import NotReady


def main() -> int:
    import sqlite3
    c = sqlite3.connect("file:data/ledger.sqlite3?mode=ro", uri=True)
    syms = [r[0] for r in c.execute(
        "select symbol, count(*) from levels group by symbol order by 2 desc limit 8")]
    # Часы сводятся с биржей (§6). Зонд читает леджер, но `read_map` и
    # `near_structure` спрашивают «сейчас» у биржевой шкалы, а не у настенных часов.
    from hunter import clock
    clock.set_sync(clock.ClockSync(
        offset_ms=0, rtt_ms=0, samples=1, measured_at_local_ms=clock.local_ms(),
        measured_at_monotonic_ns=clock.monotonic_ns()))
    now = clock.now_ms()
    print(f"{'символ':22} {'актив':>6} {'сейчас':>7} {'+флип':>7} {'прирост':>8}")
    tot_a = tot_b = 0
    for s in syms:
        m = tgbot.read_map(s)
        if isinstance(m, NotReady):
            print(f"  ОТКАЗ {s}: {m.reason}")
            continue
        price = next((z.price for z in m.zones), 0.0)
        _, uniq_now, _ = tgbot.live_unique(m.zones, price, now)
        # Предлагаемое: флипнутый входит СО СМЕНОЙ СТОРОНЫ (стр. 43).
        widened = tuple(
            dataclasses.replace(z, state="active",
                                side=("short" if z.side == "long" else "long"))
            if z.state == "flipped" and z.entry_rule == "retest_flipped" else z
            for z in m.zones)
        _, uniq_new, _ = tgbot.live_unique(widened, price, now)
        act = sum(1 for z in m.zones if z.state == "active")
        print(f"  {s:20} {act:6} {len(uniq_now):7} {len(uniq_new):7} "
              f"{len(uniq_new) - len(uniq_now):+8}")
        tot_a += len(uniq_now)
        tot_b += len(uniq_new)
    print(f"\n  ИТОГО показывается сейчас {tot_a}, стало бы {tot_b} "
          f"({tot_b - tot_a:+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
