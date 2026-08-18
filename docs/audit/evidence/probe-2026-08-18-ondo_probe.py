"""Зонд: воспроизвести ответ бота по ONDO тем же путём, что answer() c картой леджера.

Не пересборка: read_map (как ветка «карта свежая»), те же бары, тот же фильтр,
тот же рендер и та же сводка. PNG сохраняются для осмотра глазами.
"""
import asyncio
from collections import Counter
from pathlib import Path

from hunter import clock
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.render import chart_png
from hunter.tgbot import (
    BARS_ON_CHART, CHART_TFS, compose_text, live_unique, pp_zones,
    read_map, tradable_counts, zones_for_chart,
)

OUT = Path(r"C:\Users\3EC2~1\AppData\Local\Temp\claude\C--Users-------Documents-hunter-v2\db8721f4-1545-4a47-9813-1ac82f4a3bd8\scratchpad")
SYMBOL = "ONDO/USDT:USDT"


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        got = read_map(SYMBOL)
        if isinstance(got, NotReady):
            print(f"карты нет: {got.reason}")
            return
        now = clock.now_ms()
        age_min = max(0, (now - got.last_seen_ms) // 60_000)
        print(f"карта: зон={len(got.zones)} возраст={age_min} мин")
        fetched = []
        price = 0.0
        for tf in CHART_TFS:
            bars = await ex.fetch_closed_ohlcv(SYMBOL, tf, limit=BARS_ON_CHART)
            if isinstance(bars, NotReady) or not bars.bars:
                print(f"{tf}: бары не пришли")
                continue
            price = bars.bars[-1].close
            fetched.append((tf, bars.bars))
        pps = []
        for tf, tf_bars in fetched:
            tf_pps = pp_zones(tf_bars, tf)
            pps += tf_pps
            shown = zones_for_chart(got.zones, tf, price, first_ms=tf_bars[0].open_ms,
                                    now_ms=now)
            longs, shorts = tradable_counts(got.zones, price, now)
            png = chart_png(SYMBOL, tf, tf_bars, shown + tf_pps,
                            OUT / f"ondo_{tf}.png",
                            caption=f"торгуемых уровней: {longs} лонг / {shorts} шорт")
            print(f"{tf}: зон на графике {len(shown)} "
                  f"{dict(Counter(z.timeframe for z in shown))} → {png}")
        live, unique, off = live_unique(got.zones, price, now)
        print(f"фильтр: живых={len(live)} уникальных={len(unique)} скрыто={len(off)}")
        print("---- СВОДКА ----")
        print(compose_text(SYMBOL, got.zones, pps,
                           f"Карта системы, обновлена {age_min} мин назад.",
                           price=price, stale_min=age_min, now_ms=now))
    finally:
        await ex.close()


asyncio.run(main())
