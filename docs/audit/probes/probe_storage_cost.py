"""ЗОНД (задача 1.7): цена хранения — сырьё против гистограммы."""
import sys, tempfile, os
from datetime import date
from decimal import Decimal
sys.path.insert(0,'src')
from hunter.archive import fetch_agg_trades_day, histogram_from_day, histogram_to_frame
from hunter.quality import NotReady

DAY = date(2026,8,1)
CASES = [('BTCUSDT','BTC/USDT:USDT',Decimal('0.10')),
         ('ASTRUSDT','ASTR/USDT:USDT',Decimal('0.000010'))]

print(f"{'символ':10} {'сделок':>9} {'zip,МБ':>8} {'csv,МБ':>9} {'бинов':>8} {'гист,КБ':>9} {'сжатие':>9} {'расх.объёма':>12}")
for mid, sym, tick in CASES:
    d = fetch_agg_trades_day(mid, DAY)
    if isinstance(d, NotReady):
        print(f"{mid:10} НЕТ ДАННЫХ: {d.reason}"); continue
    h = histogram_from_day(d, sym, tick)
    f = histogram_to_frame(h)
    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        path = tmp.name
    f.write_parquet(path, compression='zstd')
    hist_bytes = os.path.getsize(path); os.unlink(path)
    err = h.reconciliation_error()
    print(f"{mid:10} {d.rows:>9,} {d.zip_bytes/1e6:>8.2f} {d.csv_bytes/1e6:>9.2f} "
          f"{len(h.qty_by_bin):>8,} {hist_bytes/1e3:>9.1f} {d.csv_bytes/hist_bytes:>8.0f}x {err:>12.3e}")
