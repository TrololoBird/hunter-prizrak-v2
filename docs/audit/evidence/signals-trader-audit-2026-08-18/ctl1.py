import sqlite3, statistics
con=sqlite3.connect('file:data/ledger.sqlite3?mode=ro',uri=True); q=con.execute
med=lambda xs: statistics.median(xs) if xs else float('nan')
TF=["5m","15m","1h","4h","1d","1w"]
print("A. ЦЕЛЬ ЕСТЬ / НЕТ по ТФ (level)")
for tf in TF:
    tot=q("select count(*) from signals where kind='level' and timeframe=?", (tf,)).fetchone()[0]
    wt=q("select count(*) from signals where kind='level' and timeframe=? and target is not null",(tf,)).fetchone()[0]
    if tot: print(f"  {tf}: всего {tot}, с целью {wt} ({wt/tot:.0%}), БЕЗ цели {tot-wt} ({1-wt/tot:.0%})")

print("\nB. ПАРНОЕ: сигнал -> его уровень (symbol,tf,side,price==entry)")
for tf in TF:
    rows=q("""select s.entry, s.stop, l.boundary_lo, l.boundary_hi, l.price
              from signals s join levels l
                on l.symbol=s.symbol and l.timeframe=s.timeframe and l.side=s.direction
               and abs(l.price - s.entry) < 1e-12
              where s.kind='level' and s.timeframe=?""",(tf,)).fetchall()
    tot=q("select count(*) from signals where kind='level' and timeframe=?",(tf,)).fetchone()[0]
    if not rows: 
        print(f"  {tf}: сопоставлено 0 из {tot}"); continue
    # дедуп по (entry,stop): один сигнал может совпасть с несколькими версиями структуры
    seen={}
    for e,s,bl,bh,p in rows:
        seen.setdefault((e,s),[]).append((bh-bl)/p*100)
    risk=[abs(e-s)/e*100 for (e,s) in seen]
    h_med=[med(v) for v in seen.values()]
    ratio=[r/h for r,h in zip(risk,h_med) if h>0]
    versions=[len(v) for v in seen.values()]
    print(f"  {tf}: сопоставлено {len(seen)} из {tot} сигналов; медиана риска {med(risk):.2f}%,"
          f" медиана высоты ЕГО структуры {med(h_med):.2f}%, медиана отношения риск/высота {med(ratio):.2f};"
          f" версий структуры на сигнал: медиана {med(versions):.0f}, макс {max(versions)}")

print("\nB2. Высота структуры: все строки vs только active vs дедуп по (symbol,tf,from_ms)")
for tf in TF:
    allr=[(bh-bl)/p*100 for bl,bh,p in q("select boundary_lo,boundary_hi,price from levels where timeframe=?",(tf,))]
    act=[(bh-bl)/p*100 for bl,bh,p in q("select boundary_lo,boundary_hi,price from levels where timeframe=? and state='active'",(tf,))]
    ded=[(bh-bl)/p*100 for bl,bh,p in q("select boundary_lo,boundary_hi,price from levels where timeframe=? group by symbol,from_ms,side",(tf,))]
    if allr: print(f"  {tf}: все строки n={len(allr)} мед {med(allr):.2f}% | active n={len(act)} мед {med(act):.2f}% | дедуп from_ms n={len(ded)} мед {med(ded):.2f}%")
