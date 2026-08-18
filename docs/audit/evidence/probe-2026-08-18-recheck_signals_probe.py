"""Перепроверка последних ответов бота (XAU, GPS, BTC) на текущем коде.

Воспроизводит путь `Bot.answer`: карта (леджер для вселенной / пересборка для чужих),
бары кадра, фильтры `zones_for_chart`/`live_unique`, текст `compose_text`, графики
`chart_png`. Сохраняет текст и PNG в scratchpad/recheck/<монета>/ и печатает замер
пересечений зон с классификацией причин.
"""
import asyncio
import itertools
import time
from dataclasses import replace
from pathlib import Path

from hunter import clock, run
from hunter.__main__ import HORIZON_DAYS
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.render import chart_png
from hunter.tgbot import (BARS_ON_CHART, CHART_TFS, compose_text, live_unique,
                          pp_zones, read_map, tradable_counts, zones_for_chart,
                          zones_of)

OUT = Path(__file__).with_name("recheck")

SYMBOLS = ("XAU/USDT:USDT", "GPS/USDT:USDT", "BTC/USDT:USDT")


def overlap(a, b) -> bool:
    return a.zone_lo <= b.zone_hi and b.zone_lo <= a.zone_hi


def time_overlap(a, b) -> bool:
    if min(a.to_ms, b.to_ms) <= 0:
        return False
    return a.from_ms < b.to_ms and b.from_ms < a.to_ms


def pair_report(tag: str, rows: list) -> None:
    pairs = [(a, b) for a, b in itertools.combinations(rows, 2) if overlap(a, b)]
    same = [(a, b) for a, b in pairs if a.side == b.side]
    cross = [(a, b) for a, b in pairs if a.side != b.side]
    tnest = [(a, b) for a, b in pairs if time_overlap(a, b)]
    difftf = [(a, b) for a, b in pairs if a.timeframe != b.timeframe]
    print(f"  {tag}: зон={len(rows)} пересекающихся_пар={len(pairs)} "
          f"одной_стороны={len(same)} противоположных={len(cross)} "
          f"разных_ТФ={len(difftf)} окна_структур_пересекаются_во_времени={len(tnest)}")
    for a, b in pairs:
        mark = "⚔" if a.side != b.side else " "
        tm = "⏱" if time_overlap(a, b) else " "
        print(f"    {mark}{tm} {a.side:5s} {a.timeframe:3s} [{a.zone_lo:.6g}..{a.zone_hi:.6g}] "
              f"× {b.side:5s} {b.timeframe:3s} [{b.zone_lo:.6g}..{b.zone_hi:.6g}]")


async def one(symbol: str, uni, ex: Exchange) -> None:
    name = symbol.split("/")[0].lower()
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {symbol} ===")
    t0 = time.perf_counter()
    if symbol in uni.symbols:
        got = read_map(symbol)
        if isinstance(got, NotReady) or not got.zones:
            print(f"  карта леджера не читается: {got}")
            return
        zones, origin = got.zones, "карта леджера"
        age_min = max(0, (clock.now_ms() - got.last_seen_ms) // 60_000)
    else:
        one_uni = replace(uni, symbols=(symbol,))
        report, sources = await run.collect(one_uni, 0, 0, HORIZON_DAYS,
                                            frame_bars=BARS_ON_CHART)
        decided = await asyncio.to_thread(run.decide_once, report, one_uni,
                                          sources, BARS_ON_CHART)
        dec = decided.get(symbol)
        if dec is None:
            print("  сборка не дала решения")
            return
        zones, origin, age_min = zones_of(dec), "пересборка по запросу (зонд)", 0
    t1 = time.perf_counter()
    print(f"  карта: {len(zones)} зон, возраст {age_min} мин, получена за {t1-t0:.1f} с")

    fetched = []
    price = 0.0
    for tf in CHART_TFS:
        bars = await ex.fetch_closed_ohlcv(symbol, tf, limit=BARS_ON_CHART)
        if isinstance(bars, NotReady) or not bars.bars:
            print(f"  бары {tf} не пришли")
            continue
        price = bars.bars[-1].close
        fetched.append((tf, bars.bars))
    now_ms = clock.now_ms()

    live, unique, off = live_unique(zones, price, now_ms)
    print(f"  цена={price:.6g} живых={len(live)} уникальных={len(unique)} скрыто_фильтром={len(off)}")
    pair_report("ТЕКСТ (unique после склейки)", unique)

    pps_all = []
    for tf, tf_bars in fetched:
        tf_pps = pp_zones(tf_bars, tf)
        pps_all += tf_pps
        shown = zones_for_chart(zones, tf, price, first_ms=tf_bars[0].open_ms,
                                now_ms=now_ms)
        pair_report(f"ГРАФИК {tf} (shown+ПП)", shown + tf_pps)
        longs, shorts = tradable_counts(zones, price, now_ms)
        chart_png(symbol, tf, tf_bars, shown + tf_pps, d / f"{tf}.png",
                  caption=f"торгуемых уровней: {longs} лонг / {shorts} шорт")
    text = compose_text(symbol, zones, pps_all, origin, (),
                        price=price, stale_min=age_min, now_ms=now_ms)
    (d / "text.txt").write_text(text, encoding="utf-8")
    print(f"  текст {len(text)} знаков → {d / 'text.txt'}")

    # ширина зон: доля от цены и от коробки структуры
    ws = sorted((z.zone_hi - z.zone_lo) / price * 100 for z in live if price > 0)
    if ws:
        print(f"  ширина зоны, % цены: медиана {ws[len(ws)//2]:.2f} "
              f"макс {ws[-1]:.2f} мин {ws[0]:.2f}")


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        for s in SYMBOLS:
            await one(s, uni, ex)
    finally:
        await ex.close()


asyncio.run(main())
