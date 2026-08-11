"""ЗОНД (задача 1.7): цена хранения — сырьё против гистограммы.

ЗАМЕР ОТКАЧЕННОГО МЕХАНИЗМА: docs/audit/archive-removed-2026-08-11.md

⚠ Чинить здесь нечего. Зонд мерил цену хранения СЫРЫХ СУТОК АРХИВА против гистограммы и
звал `archive.fetch_agg_trades_day` и `archive.histogram_from_day`. Обеих функций больше
нет: 2026-08-11 суточный архив `data.binance.vision` удалён из проекта целиком, источник
сделок — только ccxt REST и вебсокет. Замер при этом остаётся свидетельством: именно он
показал, во сколько раз гистограмма дешевле сырья, и на этом решении стоит вся схема кэша,
которая никуда не делась.

⚠ ПОЧИНЕН 2026-08-10, и вот что было сломано. Пробник писался 2026-08-03, когда модуль
отказов назывался `hunter.quality`, а `histogram_to_frame` лежала в `hunter.archive`.
С тех пор `quality` переименован в `models`, а `histogram_to_frame` переехала в
`hunter.store` — и пробник перестал импортироваться ВООБЩЕ, то есть команда
воспроизведения из `docs/audit/storage-cost-2026-08-03.md` падала `ImportError` до
первой строки расчёта.

Никто этого не замечал, потому что пробники стояли вне mypy (он покрывал `src`,
`scripts`, `gates`). Найдено CodeQL в первый же прогон 2026-08-10; чтобы не искать
глазами впредь, заведён гейт `gates/probes_callable.py`.

Числа протокола НЕ пересчитывались: правка чисто импортная, расчёт тот же.

Команда воспроизведения (нужна сеть — качает сутки архива):
    uv run python docs/audit/probes/probe_storage_cost.py
"""

import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter.archive import fetch_agg_trades_day, histogram_from_day  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.store import histogram_to_frame  # noqa: E402

DAY = date(2026, 8, 1)
CASES = [('BTCUSDT', 'BTC/USDT:USDT', Decimal('0.10')),
         ('ASTRUSDT', 'ASTR/USDT:USDT', Decimal('0.000010'))]

print(f"{'символ':10} {'сделок':>9} {'zip,МБ':>8} {'csv,МБ':>9} {'бинов':>8} "
      f"{'гист,КБ':>9} {'сжатие':>9} {'расх.объёма':>12}")
for mid, sym, tick in CASES:
    d = fetch_agg_trades_day(mid, DAY)
    if isinstance(d, NotReady):
        print(f"{mid:10} НЕТ ДАННЫХ: {d.reason}")
        continue
    h = histogram_from_day(d, sym, tick)
    f = histogram_to_frame(h)
    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        path = tmp.name
    f.write_parquet(path, compression='zstd')
    hist_bytes = os.path.getsize(path)
    os.unlink(path)
    err = h.reconciliation_error()
    print(f"{mid:10} {d.rows:>9,} {d.zip_bytes / 1e6:>8.2f} {d.csv_bytes / 1e6:>9.2f} "
          f"{len(h.qty_by_bin):>8,} {hist_bytes / 1e3:>9.1f} "
          f"{d.csv_bytes / hist_bytes:>8.0f}x {err:>12.3e}")
