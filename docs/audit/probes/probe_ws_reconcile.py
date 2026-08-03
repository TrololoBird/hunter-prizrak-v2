"""ЗОНД: полнота WS-потока aggTrade против REST-сырья за то же окно (задача 1.5)."""
import asyncio, json, urllib.request as u
import ccxt.pro as ccxtpro
import sys
sys.path.insert(0, 'src')
from hunter import clock

SYM_CCXT, SYM_RAW, WINDOW_S = 'BTC/USDT:USDT', 'BTCUSDT', 60

def rest_agg(sym, start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        url = (f'https://fapi.binance.com/fapi/v1/aggTrades?symbol={sym}'
               f'&startTime={cur}&endTime={min(cur+59_000, end_ms)}&limit=1000')
        with u.urlopen(url, timeout=30) as r:
            chunk = json.load(r)
        if not chunk:
            cur += 59_000; continue
        out.extend(chunk)
        nxt = chunk[-1]['T'] + 1
        cur = nxt if nxt > cur else cur + 1
        if len(chunk) < 1000 and cur >= end_ms:
            break
    return out

async def main():
    ex = ccxtpro.binanceusdm({'enableRateLimit': True,
                              'options': {'watchTrades': {'name':'aggTrade'}}})
    await ex.load_markets()
    await clock.measure(lambda: ex.fetch_time())
    ws = {}
    t0 = clock.now_ms()
    try:
        while clock.now_ms() - t0 < WINDOW_S*1000:
            b = await asyncio.wait_for(ex.watch_trades(SYM_CCXT), timeout=30)
            for t in b:
                ws[int(t['id'])] = (float(t['price']), float(t['amount']), int(t['timestamp']))
        t1 = clock.now_ms()
    finally:
        await ex.close()

    # берём заведомо внутреннее окно, чтобы не ловить края подписки
    lo, hi = t0 + 5_000, t1 - 5_000
    rest = {int(x['a']): (float(x['p']), float(x['q']), int(x['T'])) for x in rest_agg(SYM_RAW, lo, hi)}
    ws_in = {k: v for k, v in ws.items() if lo <= v[2] <= hi}

    only_rest = set(rest) - set(ws_in)
    only_ws = set(ws_in) - set(rest)
    print(f'окно {hi-lo} мс')
    print(f'WS  сделок в окне: {len(ws_in)}')
    print(f'REST сделок в окне: {len(rest)}')
    print(f'ЕСТЬ В REST, НЕТ В WS: {len(only_rest)}')
    print(f'есть в WS, нет в REST: {len(only_ws)}')
    common = set(rest) & set(ws_in)
    bad = [i for i in common if abs(rest[i][0]-ws_in[i][0])>1e-9 or abs(rest[i][1]-ws_in[i][1])>1e-12]
    print(f'общих {len(common)}, расходятся цена/объём: {len(bad)}')
    qw = sum(v[1] for v in ws_in.values()); qr = sum(v[1] for v in rest.values())
    print(f'объём WS {qw:.6f} | REST {qr:.6f} | расхождение {abs(qw-qr)/qr if qr else 0:.3e}')
    print(f'темп: {len(rest)/((hi-lo)/1000):.1f} сделок/с')

asyncio.run(main())
