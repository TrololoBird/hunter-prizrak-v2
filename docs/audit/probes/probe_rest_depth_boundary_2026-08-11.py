"""ГДЕ у Binance USDⓈ-M кончается история `aggTrades` по `startTime` — двоичный поиск.

Повод. Константа `run.REST_BACKFILL_MAX_AGE_DAYS` стояла равной 30, и обоснована была
так: зонд `probe_rest_trade_depth_2026-08-11.py` подтвердил выдачу на глубине до 30 суток.
⚠ Но тот зонд проверял отступы 1 ч … 30 сут И НА ТРИДЦАТИ ОСТАНОВИЛСЯ — то есть граница
ВЫБОРКИ была записана как граница БИРЖИ. Ровно та подмена, о которой предупреждает
CLAUDE.md, и совершил её я сам накануне.

Здесь граница ищется, а не предполагается: сперва два опорных отступа (365 суток — есть,
730 — пусто), затем двоичный поиск между ними.

⚠ ЭТО РЕШАЛО СУДЬБУ АРХИВА. При глубине REST в 30 суток `data.binance.vision` был бы
незаменим для истории; при глубине в год — избыточен. Замер и позволил убрать его целиком
(docs/audit/archive-removed-2026-08-11.md).

КОНТРОЛЬ (обязателен по CLAUDE.md: прибор должен уметь ответить иначе). Опорные точки для
того и печатаются: ближняя обязана дать сделки, дальняя — пустоту. Если обе одинаковы,
прибор заперт, и найденная «граница» будет артефактом, а не свойством биржи.

⚠ Граница СКОЛЬЗЯЩАЯ: окно уезжает вместе с календарём, и повтор через сутки сдвинет
ответ на сутки. Поэтому в константу взято не найденное число, а ровное 365 с запасом.

Воспроизведение:
    uv run python docs/audit/probes/probe_rest_depth_boundary_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import ccxt.async_support as ccxt  # noqa: E402

SYMBOL = "BTC/USDT:USDT"
DAY_MS = 86_400_000
NEAR_DAYS = 365
"""Опора «сделки есть». Проверено отдельно; если провалится — двоичный поиск не имеет смысла."""
FAR_DAYS = 730
"""Опора «пусто». Между NEAR и FAR и ищется граница."""


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


async def has_trades(ex: ccxt.binanceusdm, now_ms: int, days: int) -> bool:
    page = await ex.fetch_trades(SYMBOL, since=now_ms - days * DAY_MS, limit=5)
    return bool(page)


async def main() -> int:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    try:
        await ex.load_markets()
        now = int(await ex.fetch_time())
        print(f"символ {SYMBOL}; время биржи {iso(now)}; ccxt {ccxt.__version__}\n")

        near = await has_trades(ex, now, NEAR_DAYS)
        far = await has_trades(ex, now, FAR_DAYS)
        print("КОНТРОЛЬ опорных точек:")
        print(f"  {NEAR_DAYS:>4} сут ({iso(now - NEAR_DAYS * DAY_MS)}): "
              f"{'есть' if near else 'ПУСТО'}")
        print(f"  {FAR_DAYS:>4} сут ({iso(now - FAR_DAYS * DAY_MS)}): "
              f"{'есть' if far else 'ПУСТО'}")
        if not near or far:
            print("\nКОНТРОЛЬ НЕ ПРОЙДЕН: прибор не различает ближнее и дальнее.")
            print("Двоичный поиск не запускается — его ответ был бы артефактом.")
            return 1
        print("  контроль пройден: прибор различает ближнее и дальнее\n")

        lo, hi = NEAR_DAYS, FAR_DAYS
        print("двоичный поиск границы:")
        while hi - lo > 3:
            mid = (lo + hi) // 2
            ok = await has_trades(ex, now, mid)
            print(f"  {mid:>4} сут ({iso(now - mid * DAY_MS)}): "
                  f"{'есть' if ok else 'ПУСТО'}")
            if ok:
                lo = mid
            else:
                hi = mid

        print(f"\nГРАНИЦА: последний рабочий отступ {lo} сут, первый пустой {hi} сут")
        print(f"то есть история кончается около {iso(now - lo * DAY_MS)}")
        print("\n⚠ Граница скользящая: повтор через сутки сдвинет ответ на сутки.")
        print("В `run.REST_BACKFILL_MAX_AGE_DAYS` взято ровное 365 с запасом до края.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
