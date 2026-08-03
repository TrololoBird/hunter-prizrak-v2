"""ЗОНД: какая монета торговалась около 3.0 на дату разбора prizrak_res (2026-07-12)."""
import asyncio, datetime as dt
import ccxt.async_support as ccxt

LO, HI = 2.90, 3.20          # окрестность уровня «3.0, 30, 46»
START = dt.datetime(2026,7,5,tzinfo=dt.UTC)

async def main():
    ex = ccxt.binanceusdm({'enableRateLimit': True})
    hits = []
    try:
        m = await ex.load_markets()
        cands = [s for s,mk in m.items()
                 if mk.get('active') and mk.get('settle')=='USDT'
                 and (mk.get('info') or {}).get('underlyingType')=='COIN'
                 and (mk.get('info') or {}).get('contractType')=='PERPETUAL']
        print('кандидатов:', len(cands), flush=True)
        since = int(START.timestamp()*1000)
        for i, s in enumerate(cands):
            try:
                r = await ex.fetch_ohlcv(s,'1d',since=since,limit=10)
            except Exception:
                continue
            if not r: continue
            lo = min(k[3] for k in r); hi = max(k[2] for k in r)
            if lo <= 3.05 <= hi and LO <= (lo+hi)/2 <= HI*1.4:
                hits.append((s, lo, hi))
                print(f'  СОВПАЛО {s:24} диапазон {lo} .. {hi}', flush=True)
            if i % 100 == 0: print(f'  ...{i}/{len(cands)}', flush=True)
    finally:
        await ex.close()
    print()
    print(f'НАЙДЕНО: {len(hits)}')
    for s, lo, hi in hits: print(f'  {s:24} {lo} .. {hi}')

asyncio.run(main())
