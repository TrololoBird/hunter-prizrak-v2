"""Ширина зон (VA70) по всей карте леджера: по ТФ, в % цены, и доля пересечений.

Локальное чтение data/ledger.sqlite3 (readonly) — REST не трогается.
"""
import sqlite3
from collections import defaultdict
from statistics import median

conn = sqlite3.connect(r"file:C:\Users\Антон\Documents\hunter-v2\data\ledger.sqlite3?mode=ro",
                       uri=True)
rows = conn.execute(
    "SELECT symbol, timeframe, side, price, zone_lo, zone_hi, state"
    " FROM levels WHERE state='active'").fetchall()
conn.close()

by_tf: dict[str, list[float]] = defaultdict(list)
by_sym: dict[str, list[tuple]] = defaultdict(list)
for sym, tf, side, price, lo, hi, st in rows:
    p, lo, hi = float(price), float(lo), float(hi)
    if p > 0:
        by_tf[tf].append((hi - lo) / p * 100)
    by_sym[sym].append((tf, side, p, lo, hi))

print(f"активных уровней: {len(rows)}; символов: {len(by_sym)}")
print("\nширина зоны, % цены (медиана / p90 / макс / n):")
for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
    ws = sorted(by_tf.get(tf, []))
    if not ws:
        continue
    p90 = ws[int(len(ws) * 0.9)] if len(ws) > 1 else ws[0]
    print(f"  {tf:3s}: {median(ws):6.2f} / {p90:6.2f} / {ws[-1]:7.2f} / {len(ws)}")

# пересечения активных зон внутри символа: доля пар
tot_pairs = ov_pairs = cross_pairs = 0
for sym, zs in by_sym.items():
    for i in range(len(zs)):
        for j in range(i + 1, len(zs)):
            a, b = zs[i], zs[j]
            tot_pairs += 1
            if a[3] <= b[4] and b[3] <= a[4]:
                ov_pairs += 1
                if a[1] != b[1]:
                    cross_pairs += 1
print(f"\nпар зон внутри символов: {tot_pairs}; пересекаются: {ov_pairs} "
      f"({ov_pairs / tot_pairs * 100:.1f}%); из них встречные: {cross_pairs}")
