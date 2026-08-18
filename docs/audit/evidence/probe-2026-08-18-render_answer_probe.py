"""Зонд: воспроизвести ОТВЕТ бота на «btc» целиком — 3 графика + сводка.

Тот же путь: collect+decide_once (как OnDemand) → zones_for_chart(+фильтр) →
pp_zones → chart_png → compose_text. PNG сохраняются для осмотра глазами.
"""
import asyncio
from collections import Counter
from dataclasses import replace
from pathlib import Path

from hunter import clock, run
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.render import chart_png
from hunter.tgbot import (
    BARS_ON_CHART, CHART_TFS, DOMINANCE_SYMBOL, DOMINANCE_WINDOW, compose_text,
    pp_zones, tradable_counts, zones_for_chart, zones_of,
)

OUT = Path(r"C:\Users\3EC2~1\AppData\Local\Temp\claude\C--Users-------Documents-hunter-v2\db8721f4-1545-4a47-9813-1ac82f4a3bd8\scratchpad")
SYMBOL = "BTC/USDT:USDT"


async def main() -> None:
    uni = load_universe()
    one = replace(uni, symbols=(SYMBOL,))
    report, sources = await run.collect(one, 0, 0, 180)
    decided = await asyncio.to_thread(run.decide_once, report, one, sources)
    got = decided.get(SYMBOL)
    if got is None:
        print("рядов не собрано")
        return
    zones = zones_of(got)
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        fetched = []
        price = 0.0
        for tf in CHART_TFS:
            bars = await ex.fetch_closed_ohlcv(SYMBOL, tf, limit=BARS_ON_CHART)
            if isinstance(bars, NotReady) or not bars.bars:
                print(f"{tf}: бары не пришли")
                continue
            price = bars.bars[-1].close
            fetched.append((tf, bars.bars))
        now = clock.now_ms()
        pps = []
        for tf, tf_bars in fetched:
            tf_pps = pp_zones(tf_bars, tf)
            pps += tf_pps
            shown = zones_for_chart(zones, tf, price, first_ms=tf_bars[0].open_ms,
                                    now_ms=now)
            longs, shorts = tradable_counts(zones, price, now)
            png = chart_png(SYMBOL, tf, tf_bars, shown + tf_pps,
                            OUT / f"answer_{tf}.png",
                            caption=f"торгуемых уровней: {longs} лонг / {shorts} шорт")
            print(f"{tf}: зон на графике {len(shown)} "
                  f"{dict(Counter(z.timeframe for z in shown))} → {png}")
        dom = await ex.fetch_closed_ohlcv(DOMINANCE_SYMBOL, "1h",
                                          limit=DOMINANCE_WINDOW + 1)
        if isinstance(dom, NotReady) or len(dom.bars) < DOMINANCE_WINDOW:
            line = "⚠ доминация BTC не получена"
        else:
            c = [b.close for b in dom.bars]
            line = (f"📊 Доминация BTC (индекс BTCDOM; рост = BTC сильнее альтов):"
                    f" {c[-1]} · 4ч {(c[-1] / c[-5] - 1) * 100:+.2f}%"
                    f" · сутки {(c[-1] / c[-DOMINANCE_WINDOW] - 1) * 100:+.2f}%")
        print("---- СВОДКА ----")
        print(compose_text(SYMBOL, zones, pps, "Карта пересобрана по запросу (зонд).",
                           price=price, stale_min=0, now_ms=now, dominance=line))
    finally:
        await ex.close()


asyncio.run(main())
