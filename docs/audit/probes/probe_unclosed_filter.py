"""ЗОНД: сколько НЕзакрытых обновлений отсекает фильтр (задача 1.4).

Гейт, который ни разу не сработал, неотличим от гейта, который не может сработать.
"""
import asyncio, sys
sys.path.insert(0,'src')
import ccxt.pro as ccxtpro
from hunter import clock
from hunter.bars import tf_ms

SYM, TF, SECS = 'BTC/USDT:USDT', '5m', 330

async def main():
    ex = ccxtpro.binanceusdm({'enableRateLimit': True})
    await ex.load_markets()
    await clock.measure(lambda: ex.fetch_time())
    raw_updates = 0; raw_rows = 0; unclosed = 0; closed = 0
    seen_closed = set()
    t0 = clock.now_ms()
    try:
        while clock.now_ms() - t0 < SECS*1000:
            r = await asyncio.wait_for(ex.watch_ohlcv(SYM, TF), timeout=60)
            raw_updates += 1
            now = clock.now_ms()
            for row in r:
                raw_rows += 1
                if now < int(row[0]) + tf_ms(TF): unclosed += 1
                else:
                    closed += 1; seen_closed.add(int(row[0]))
    finally:
        await ex.close()
    print(f'обновлений от WS      : {raw_updates}')
    print(f'строк в них всего     : {raw_rows}')
    print(f'ОТСЕЧЕНО как незакрытые: {unclosed}')
    print(f'прошло как закрытые    : {closed} (уникальных баров {len(seen_closed)})')
    print(f'доля отсечённого       : {unclosed/raw_rows:.1%}' if raw_rows else 'нет данных')

asyncio.run(main())
