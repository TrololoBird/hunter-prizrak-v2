import sqlite3, statistics
con=sqlite3.connect('file:data/ledger.sqlite3?mode=ro',uri=True); q=con.execute
med=lambda xs: statistics.median(xs) if xs else float('nan')
TF=["5m","15m","1h","4h","1d","1w"]
print("C. Риск > 2x высоты структуры => стоп НЕ из запаса (запас ограничен высотой), значит ЯКОРЬ 2-5% от цены")
for tf in TF:
    rows=q("""select s.id, s.entry, s.stop, l.boundary_lo, l.boundary_hi, l.price
              from signals s join levels l
                on l.symbol=s.symbol and l.timeframe=s.timeframe and l.side=s.direction
               and abs(l.price-s.entry)<1e-12
              where s.kind='level' and s.timeframe=?""",(tf,)).fetchall()
    seen={}
    for sid,e,s,bl,bh,p in rows: seen.setdefault(sid,(e,s,(bh-bl)/p*100))
    if not seen: continue
    over=[1 for (e,s,h) in seen.values() if h>0 and abs(e-s)/e*100 > 2*h]
    print(f"  {tf}: сигналов {len(seen)}, риск>2x высоты у {len(over)} ({len(over)/len(seen):.0%})")

print("\nD. R исхода 'target' == R:R сигнала? (тогда '1 исход >=1R' предопределён геометрией)")
rows=q("""select o.kind,o.r,s.entry,s.stop,s.target from signals s join outcomes o on o.signal_id=s.id
          where s.kind='level' and o.kind='target' and s.target is not null""").fetchall()
bad=[ (r, abs(t-e)/abs(e-s)) for k,r,e,s,t in rows if abs(r-abs(t-e)/abs(e-s))>1e-6 ]
print(f"  целевых исходов {len(rows)}, расхождений R с геометрией: {len(bad)}")
rr=[abs(t-e)/abs(e-s) for k,r,e,s,t in rows]
print(f"  медиана R целевых исходов {med(rr):.2f}; >=1R: {sum(1 for x in rr if x>=1)}")
# сколько ЗАПОЛНЕННЫХ сделок вообще могли дать >=1R
fill=q("""select count(*) from signals s join outcomes o on o.signal_id=s.id
          where s.kind='level' and s.target is not null and abs(s.target-s.entry)/abs(s.entry-s.stop)>=1""").fetchone()[0]
print(f"  из 73 исходов сигналов с геометрией R:R>=1 всего: {fill} -> потолок числа '>=1R'")

print("\nE. ИСХОДЫ по ТФ (знаменатель к среднему R=-0.34)")
for tf in TF:
    tot=q("select count(*) from signals where kind='level' and timeframe=?",(tf,)).fetchone()[0]
    got=q("select count(*) from signals s join outcomes o on o.signal_id=s.id where s.kind='level' and s.timeframe=?",(tf,)).fetchone()[0]
    rs=[r[0] for r in q("select o.r from signals s join outcomes o on o.signal_id=s.id where s.kind='level' and s.timeframe=? and o.r is not null",(tf,))]
    nf=q("select count(*) from signals s join signal_states st on st.signal_id=s.id where s.kind='level' and s.timeframe=? and st.state='not_filled'",(tf,)).fetchone()[0]
    if tot: print(f"  {tf}: сигналов {tot}, исходов {got} ({got/tot:.0%}), not_filled {nf} ({nf/tot:.0%}), средний R {statistics.mean(rs) if rs else float('nan'):+.2f}")

print("\nF. ЗОМБИ: сколько зомби-строк перекрыты СВЕЖЕЙ активной строкой той же структуры (тот же symbol+tf+from_ms)")
import time
now=int(time.time()*1000)
TFMS={"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000,"1w":604800000}
for tf in TF:
    z=q("select symbol,from_ms,to_ms from levels where state='active' and timeframe=? and to_ms < ?",(tf,now-180*TFMS[tf])).fetchall()
    if not z: continue
    fresh=set(q("select symbol,from_ms from levels where timeframe=? and to_ms >= ?",(tf,now-180*TFMS[tf])))
    cov=sum(1 for s,f,t in z if (s,f) in fresh)
    print(f"  {tf}: зомби {len(z)}, из них есть свежая версия ТОЙ ЖЕ структуры: {cov}")
print("\nG. активные строки: сколько РАЗНЫХ структур (symbol,tf,from_ms) против строк")
r=q("select count(*), count(distinct symbol||'|'||timeframe||'|'||from_ms), count(distinct symbol||'|'||timeframe||'|'||round(price,10)) from levels where state='active'").fetchone()
print(f"  строк {r[0]}, различных (symbol,tf,from_ms) {r[1]}, различных (symbol,tf,цена) {r[2]}")
