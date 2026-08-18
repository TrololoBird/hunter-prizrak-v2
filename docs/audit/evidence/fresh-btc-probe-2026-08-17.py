"""Зонд: собрать карту BTC ТЕМ ЖЕ путём, что бот по запросу (collect + decide_once),
и напечатать статистику фильтра «уровень у структуры» + сводку.

Решающий контроль фильтра (вопрос фальсификатора 2026-08-17): на СВЕЖЕЙ карте
5м/15м обязаны получать уровни в кадр — иначе фильтр лишь лесенка ТФ.
Ничего не пишет в леджер. Повторяет OnDemand._build_now.
"""
import asyncio
from collections import Counter
from dataclasses import replace

from hunter import clock, run
from hunter.config import load_universe
from hunter.tgbot import compose_text, live_unique, near_structure, zones_of

SYMBOL = "BTC/USDT:USDT"
HORIZON_DAYS = 180  # как у бота: лог «сборка по запросу начата … горизонт_суток=180»


async def main() -> None:
    uni = load_universe()
    one = replace(uni, symbols=(SYMBOL,))
    report, sources = await run.collect(one, 0, 0, HORIZON_DAYS)
    decided = await asyncio.to_thread(run.decide_once, report, one, sources)
    got = decided.get(SYMBOL)
    if got is None:
        print("рядов не собрано")
        return
    zones = zones_of(got)
    now = clock.now_ms()
    print(f"отпечаток: уровней={len(zones)} now_ms={now} горизонт_сут={HORIZON_DAYS}")
    hidden = [z for z in zones if not near_structure(z, now)]
    kept = [z for z in zones if near_structure(z, now)]
    print(f"скрыто: {len(hidden)}; оставлено: {len(kept)}")
    print("скрыто по ТФ:   ", dict(Counter(z.timeframe for z in hidden)))
    print("оставлено по ТФ:", dict(Counter(z.timeframe for z in kept)))
    series = run.bars_of(report, SYMBOL)
    bars = series.get("15m") or series.get("1h") or []
    price = bars[-1].close if bars else 0.0
    print(f"цена: {price}")
    print("---- СВОДКА (как у бота, с фильтром) ----")
    print(compose_text(SYMBOL, zones, [], "Карта пересобрана по запросу (зонд).",
                       price=price, stale_min=0, now_ms=now))
    print("---- показанные уровни (после фильтра и склейки) ----")
    _, unique, _ = live_unique(zones, price, now)
    for z in unique:
        print(f"{z.side:5} {z.price:9.1f} {z.timeframe:>3} "
              f"зона {z.zone_lo:.0f}–{z.zone_hi:.0f} правило={z.entry_rule or '—'}")


asyncio.run(main())
