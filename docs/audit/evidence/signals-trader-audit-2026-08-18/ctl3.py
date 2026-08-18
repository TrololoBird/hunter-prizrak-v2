import sqlite3, statistics, random
con=sqlite3.connect('file:data/ledger.sqlite3?mode=ro',uri=True); q=con.execute
med=lambda xs: statistics.median(xs) if xs else float('nan')
TF_ORDER=["5m","15m","1h","4h","1d","1w"]
TF_MS={"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000,"1w":604800000}
BARS=180; WIN=14
last=q("select max(recorded_at) from signals").fetchone()[0]; since=last-WIN*86400000
sigs=q("select id,symbol,timeframe,direction,opened_at,entry,stop from signals where kind='level' and opened_at>=?",(since,)).fetchall()
lvls=q("select symbol,timeframe,price,to_ms,first_seen,retired_at from levels").fetchall()
by_sym={}
for r in lvls: by_sym.setdefault(r[0],[]).append(r)

cands=[]  # на сигнал: список (price, alive_flag), risk
for sid,sym,tf,d,op,e,s in sigs:
    pool=set(TF_ORDER[TF_ORDER.index(tf):])
    c=[]
    for lsym,ltf,price,to_ms,fs,ra in by_sym.get(sym,()):
        if ltf not in pool: continue
        if fs>op or (ra is not None and ra<=op): continue
        if d=="long" and price<=e: continue
        if d=="short" and price>=e: continue
        c.append((price, to_ms >= op - BARS*TF_MS[ltf]))
    cands.append((c, e, abs(e-s)))

def rr_of(cset, e, risk):
    if not cset: return None
    best=min(cset, key=lambda p: abs(p-e))
    return abs(best-e)/risk

full=[]; alive=[]; pair_full=[]; pair_alive=[]
for c,e,risk in cands:
    a=[p for p,ok in c if ok]; f=[p for p,_ in c]
    rf=rr_of(f,e,risk); ra=rr_of(a,e,risk)
    if rf is not None: full.append(rf)
    if ra is not None: alive.append(ra)
    if rf is not None and ra is not None: pair_full.append(rf); pair_alive.append(ra)
print(f"воспроизведено probe2: вся карта n={len(full)} мед {med(full):.2f}; живая n={len(alive)} мед {med(alive):.2f}")
print(f"ПАРНО на общих {len(pair_full)} сигналах: вся карта мед {med(pair_full):.2f} -> живая мед {med(pair_alive):.2f}")
worse=sum(1 for a,b in zip(pair_full,pair_alive) if b<a-1e-12)
same=sum(1 for a,b in zip(pair_full,pair_alive) if abs(b-a)<=1e-12)
print(f"  из них R:R стал МЕНЬШЕ: {worse}, не изменился: {same}, вырос: {len(pair_full)-worse-same}  <- фильтр может только УБИРАТЬ кандидатов, значит рост предопределён")

# КОНТРОЛЬ 2: случайный отсев ТОЙ ЖЕ мощности вместо фильтра «живая структура»
rnd=random.Random(20260818)
meds=[]; ns=[]
for _ in range(200):
    xs=[]
    for c,e,risk in cands:
        k=sum(1 for _,ok in c if ok)
        if not c: continue
        pick=rnd.sample([p for p,_ in c], k) if k<=len(c) else [p for p,_ in c]
        r=rr_of(pick,e,risk)
        if r is not None: xs.append(r)
    meds.append(med(xs)); ns.append(len(xs))
print(f"СЛУЧАЙНЫЙ отсев той же мощности, 200 повторов: медиана медиан {med(meds):.2f}"
      f" (мин {min(meds):.2f}, макс {max(meds):.2f}); нашлась цель в среднем у {statistics.mean(ns):.0f}")
print(f"честный фильтр: {med(alive):.2f} — {'НЕ лучше' if med(alive)<=max(meds) else 'лучше'} случайного отсева той же мощности")

# сколько кандидатов вообще отсеивается
tot=sum(len(c) for c,_,_ in cands); al=sum(sum(1 for _,ok in c if ok) for c,_,_ in cands)
print(f"кандидатов всего {tot}, живых {al} ({al/tot:.0%})")
