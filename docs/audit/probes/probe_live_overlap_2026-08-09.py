"""Зонд: двойной счёт при пересечении архива и живого буфера — и что даёт правка.

Вопрос один: если сутки D лежат И в архивном кэше, И в живом буфере (архив опубликован,
буфер ещё держит), сколько объёма получит окно, накрывающее D?

Строится подстроенный случай: окно из двух суток, D в архиве (объём 10.0), D и D+1 в
живом буфере (10.0 и 5.0). Верный ответ окна — 15.0. Прежний добор («живое всем окном,
любой успех обнуляет missing») давал 25.0 — объём D дважды. Посуточный добор
(правка 2026-08-09) даёт 15.0.

Контроль фальсифицируемости: зонд печатает ОБА способа на одних данных — прибор
доказуемо способен ответить иначе, значит 15.0 — свойство правки, а не тавтология.

Запуск (данные синтетические, сеть не нужна):

    uv run python docs/audit/probes/probe_live_overlap_2026-08-09.py
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter.archive import WindowSource, cache_path
from hunter.models import BarBinnedTrades, NotReady, TradeHistogram

BUCKET_MS = 300_000
TICK = Decimal("0.01")
DAY = date(2026, 8, 7)
DAY0_MS = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
DAY_MS = 86_400_000


def main() -> int:
    with TemporaryDirectory() as td:
        cache = Path(td)
        # Архив: сутки D, один бин (цена 100.00 → бин 10000), объём 10.0, 1 сделка.
        frame = pl.DataFrame({"bucket": [DAY0_MS], "bin": [10_000],
                              "qty": [10.0], "n": [1]})
        frame.write_parquet(cache / cache_path("TESTUSDT", DAY, TICK).name)

        # Живой буфер: D (10.0) И D+1 (5.0) — пересечение с архивом на сутках D.
        # Сделка D+1 лежит в ПОСЛЕДНЕЙ корзине суток: `BarBinnedTrades.window` отдаёт
        # окно только внутри собранного диапазона, и покрытие суток должно быть полным.
        live = BarBinnedTrades(symbol="TEST", tick_size=TICK, bucket_ms=BUCKET_MS)
        live.add(100.00, 10.0, DAY0_MS)
        live.add(100.00, 5.0, DAY0_MS + 2 * DAY_MS - 1)

        src = WindowSource("TEST", "TESTUSDT", TICK, live=live, cache_dir=cache)
        got = src.window(DAY0_MS, DAY0_MS + 2 * DAY_MS)
        assert isinstance(got, TradeHistogram), got
        fixed = got.qty_seen

        # Прежний способ на ТЕХ ЖЕ данных: архивные сутки + живое ВСЕМ окном.
        whole = live.window(DAY0_MS, DAY0_MS + 2 * DAY_MS)
        assert isinstance(whole, TradeHistogram), whole
        old = 10.0 + whole.qty_seen

        print("объём суток D в архиве 10.0; в живом буфере D=10.0, D+1=5.0")
        print("верный объём окна: 15.0")
        print(f"прежний добор (всем окном):  {old}")
        print(f"посуточный добор (правка):   {fixed}")
        if fixed != 15.0 or old != 25.0:
            print("ПРОВАЛ: ожидалось 15.0 против 25.0")
            return 1

        # Вторая половина контроля: недостающие сутки ВНЕ живого покрытия не должны
        # молча исчезать из missing — прежний код обнулял его любым успехом.
        far = src.window(DAY0_MS - DAY_MS, DAY0_MS + DAY_MS)
        if not isinstance(far, NotReady):
            print(f"ПРОВАЛ: окно с непокрытыми сутками обязано быть NotReady, а не {far.qty_seen}")
            return 1
        print(f"непокрытые сутки честно отказаны: {far.reason}")
        print("OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
