# Зонд фильтра «уровень у структуры» + строки доминации, по карте BTC из леджера.
# Контроль фальсифицируемости: фильтр обязан уметь И скрывать, И оставлять;
# сводка скрытых/оставленных — ПО ТФ (перекос вдоль измерения — дефект, не данные).
import asyncio
from collections import Counter

from hunter import clock
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady
from hunter.tgbot import (
    DOMINANCE_SYMBOL, DOMINANCE_WINDOW, compose_text, live_unique, near_structure,
    read_map, tradable_counts,
)


async def main() -> None:
    ex = Exchange(load_universe().venue)
    await ex.open()
    try:
        m = read_map("BTC/USDT:USDT")
        if isinstance(m, NotReady):
            print("карта:", m.reason)
            return
        zones = m.zones
        now = clock.now_ms()
        active = [z for z in zones if z.state == "active"]
        no_window = [z for z in active if z.to_ms <= 0]
        withw = [z for z in active if z.to_ms > 0]
        hidden = [z for z in withw if not near_structure(z, now)]
        kept = [z for z in withw if near_structure(z, now)]
        print(f"активных: {len(active)}; без окна (to_ms=0): {len(no_window)}")
        print(f"с окном: {len(withw)}; скрыто: {len(hidden)}; оставлено: {len(kept)}")
        print("скрыто по ТФ:   ", dict(Counter(z.timeframe for z in hidden)))
        print("оставлено по ТФ:", dict(Counter(z.timeframe for z in kept)))
        print("без окна по ТФ: ", dict(Counter(z.timeframe for z in no_window)))

        bars = await ex.fetch_closed_ohlcv("BTC/USDT:USDT", "15m", limit=2)
        price = bars.bars[-1].close if not isinstance(bars, NotReady) else 0.0
        live, unique, off = live_unique(zones, price, now)
        l, s = tradable_counts(zones, price, now)
        l0, s0 = tradable_counts(zones, price, 0)
        print(f"\nцена: {price}; живых после фильтра: {len(live)}; уникальных:"
              f" {len(unique)}; скрыто: {len(off)}; торгуемых: {l}/{s}"
              f" (без фильтра было {l0}/{s0})")

        dom = await ex.fetch_closed_ohlcv(DOMINANCE_SYMBOL, "1h", limit=DOMINANCE_WINDOW)
        if isinstance(dom, NotReady) or len(dom.bars) < DOMINANCE_WINDOW:
            line = f"доминация НЕ получена: {dom.reason if isinstance(dom, NotReady) else len(dom.bars)}"
        else:
            c = [b.close for b in dom.bars]
            line = (f"📊 Доминация BTC (индекс BTCDOM; рост = BTC сильнее альтов):"
                    f" {c[-1]} · 4ч {(c[-1] / c[-5] - 1) * 100:+.2f}%"
                    f" · сутки {(c[-1] / c[0] - 1) * 100:+.2f}%")
        print("\n---- сводка ----")
        print(compose_text("BTC/USDT:USDT", zones, [], "Источник: зонд.", (),
                           price=price, stale_min=0, now_ms=now, dominance=line))
    finally:
        await ex.close()


asyncio.run(main())
