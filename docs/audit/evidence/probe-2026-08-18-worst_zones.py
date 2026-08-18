"""Десятка самых широких зон карты: кто породил, какова коробка структуры."""
import sqlite3
from datetime import UTC, datetime

conn = sqlite3.connect(r"file:C:\Users\Антон\Documents\hunter-v2\data\ledger.sqlite3?mode=ro",
                       uri=True)
cols = [r[1] for r in conn.execute("PRAGMA table_info(levels)").fetchall()]
print("колонки:", ", ".join(cols))
rows = conn.execute(
    "SELECT symbol, timeframe, side, price, zone_lo, zone_hi,"
    " boundary_lo, boundary_hi, from_ms, to_ms"
    " FROM levels WHERE state='active' AND price > 0"
    " ORDER BY (zone_hi - zone_lo) / price DESC LIMIT 12").fetchall()
conn.close()

for sym, tf, side, p, lo, hi, blo, bhi, fms, tms in rows:
    p, lo, hi, blo, bhi = map(float, (p, lo, hi, blo, bhi))
    w = (hi - lo) / p * 100
    bw = (bhi - blo) / p * 100 if bhi > blo else 0
    d0 = datetime.fromtimestamp(fms / 1000, UTC).strftime("%d.%m") if fms else "?"
    d1 = datetime.fromtimestamp(tms / 1000, UTC).strftime("%d.%m") if tms else "?"
    print(f"{sym:18s} {tf:3s} {side:5s} ПОК {p:<12g} зона {lo:g}..{hi:g} "
          f"({w:.0f}% цены) границы {blo:g}..{bhi:g} ({bw:.0f}%) окно {d0}–{d1}")
