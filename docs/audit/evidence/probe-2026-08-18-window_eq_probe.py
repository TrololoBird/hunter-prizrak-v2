"""Пробник-равенство: прежний СКАЛЯРНЫЙ расчёт окна профиля против нового ВЕКТОРНОГО.

Правка 2026-08-18 (п.2 приказа владельца): горячий цикл CandleWindows.window переведён
на numpy. Обещание правки — БАЙТ-В-БАЙТ те же числа. Здесь оно проверяется строгим `==`
(не допуском) на живых окнах обоих символов, всех ТФ карты плюс минутные ряды.

Старая реализация скопирована сюда ДОСЛОВНО из profile_source.py до правки.
"""
import asyncio
import bisect

from hunter import barstore
from hunter.config import load_universe
from hunter.exchange import Exchange
from hunter.models import NotReady, TradeHistogram, bin_index
from hunter.profile_source import MAX_BINS_PER_BAR, CandleWindows
from hunter.tgbot import read_map

SYMBOLS = ["ONDO/USDT:USDT", "BTC/USDT:USDT"]


def old_window(symbol, tick, chunk):
    """Дословная копия скалярного тела window() до правки 2026-08-18."""
    h = TradeHistogram(symbol=symbol, tick_size=tick)
    spans = []
    k_lo = None
    k_hi = None
    for b in chunk:
        k0 = bin_index(b.low, tick)
        k1 = bin_index(b.high, tick)
        if k1 - k0 + 1 > MAX_BINS_PER_BAR:
            continue
        spans.append((k0, k1, b.volume / (k1 - k0 + 1)))
        k_lo = k0 if k_lo is None else min(k_lo, k0)
        k_hi = k1 if k_hi is None else max(k_hi, k1)
        h.trades_seen += 1
        h.qty_seen += b.volume
    if k_lo is None or k_hi is None:
        return None
    width = k_hi - k_lo + 2
    diff = [0.0] * width
    for k0, k1, share in spans:
        diff[k0 - k_lo] += share
        diff[k1 - k_lo + 1] -= share
    run = 0.0
    for idx in range(width - 1):
        run += diff[idx]
        if run > 0.0:
            h.qty_by_bin[k_lo + idx] = run
    if not h.qty_by_bin:
        return None
    return h


async def main() -> None:
    uni = load_universe()
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        total = same = 0
        mismatches = []
        for symbol in SYMBOLS:
            inst = ex.instrument(symbol)
            if isinstance(inst, NotReady):
                print(f"{symbol}: инструмента нет — {inst.reason}")
                continue
            got = read_map(symbol)
            if isinstance(got, NotReady):
                print(f"{symbol}: карты нет — {got.reason}")
                continue
            for z in got.zones:
                if z.kind != "level" or z.from_ms <= 0 or z.to_ms <= z.from_ms:
                    continue
                for tf in (z.timeframe, "1m"):
                    bars = barstore.load(uni.venue, inst.market_id, tf,
                                         z.from_ms, z.to_ms)
                    if not bars:
                        continue
                    cw = CandleWindows(symbol, inst.tick_size, bars, tf)
                    new = cw.window(z.from_ms, z.to_ms)
                    keys = [b.open_ms for b in cw.bars]
                    i = bisect.bisect_left(keys, z.from_ms)
                    j = bisect.bisect_left(keys, z.to_ms)
                    old = old_window(symbol, inst.tick_size, cw.bars[i:j])
                    if isinstance(new, NotReady):
                        continue  # отказы (покрытие) считает основной путь, не сравнение
                    total += 1
                    ok = (
                        old is not None
                        and list(new.qty_by_bin.items()) == list(old.qty_by_bin.items())
                        and new.trades_seen == old.trades_seen
                        and new.qty_seen.hex() == old.qty_seen.hex()
                    )
                    if ok:
                        same += 1
                    elif len(mismatches) < 5:
                        mismatches.append((symbol, z.timeframe, tf, z.from_ms, z.to_ms))
        print(f"окон сравнено {total}, бит-в-бит совпало {same}, "
              f"разошлось {total - same}")
        for m in mismatches:
            print("расхождение:", m)
    finally:
        await ex.close()


asyncio.run(main())
