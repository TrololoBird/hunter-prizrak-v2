"""Наши ширины зон из леджера: BTC/ETH отдельно от альт, по ТФ (readonly).

Запуск из корня репозитория: uv run python docs/audit/evidence/ledger-width-compare-2026-08-18.py

⚠ Линейка таблицы — (hi-lo)/ПОК, как в первой версии сравнения. Авторские числа из
tg-zone-widths меряны от СЕРЕДИНЫ; медианы у линеек совпадают до сотых, экстремумы
расходятся (136% против 83% на одной зоне) — обе линейки печатает
tg-widths-controls-2026-08-18.py, и сравнивать с автором надо по ней.

Леджер — растущий источник: отпечаток (размер базы, уровни по состояниям) печатается
в шапке; без него команда воспроизведения соврёт после первой доливки данных.
"""
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import median, quantiles

db = Path("data/ledger.sqlite3")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
states = conn.execute(
    "SELECT state, COUNT(*) FROM levels GROUP BY state ORDER BY state").fetchall()
print(f"отпечаток: {db} = {db.stat().st_size} байт; уровней: "
      + ", ".join(f"{s}={n}" for s, n in states))
rows = conn.execute(
    "SELECT symbol, timeframe, price, zone_lo, zone_hi FROM levels WHERE state='active'"
).fetchall()
conn.close()

MAJ = ("BTC/", "ETH/")
groups: dict[tuple[str, str], list[float]] = defaultdict(list)
for sym, tf, price, lo, hi in rows:
    p = float(price)
    if p <= 0:
        continue
    cls = "BTC/ETH" if sym.startswith(MAJ) else "альты"
    groups[(cls, tf)].append((float(hi) - float(lo)) / p * 100)

print(f"{'класс':8s} {'ТФ':4s} {'n':>5s} {'25%':>7s} {'медиана':>8s} {'75%':>7s} {'макс':>8s}")
for cls in ("BTC/ETH", "альты"):
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        ws = sorted(groups.get((cls, tf), []))
        if len(ws) < 2:
            continue
        q = quantiles(ws, n=4)
        print(f"{cls:8s} {tf:4s} {len(ws):5d} {q[0]:7.2f} {median(ws):8.2f} {q[2]:7.2f} {ws[-1]:8.2f}")
