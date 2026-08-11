"""Способен ли `Exchange.fetch_agg_trades_window` выкачать сутки СТАРШЕ 24 часов.

Повод. Владелец назвал ключевой архитектурной ошибкой зависимость живого контура от
суточных архивов data.binance.vision: архив публикуется с отставанием, 10-11 августа
отдавал HTTP 404, и самые свежие структуры 5м/15м/1ч уровней не получили. REST-добор
`fetch_agg_trades_window` написан именно под это.

⚠ Замер `probe_rest_trade_depth_2026-08-11.py` показал, что у Binance USDⓈ-M ДВЕ формы
запроса aggTrades ведут себя ПО-РАЗНОМУ:
  * `startTime` (ccxt `since`) — отдаёт сделки как минимум 30-суточной давности;
  * `fromId` — старше 24 часов отклоняется: код -1000, «Only recent trade history
    within the past 24 hours is supported for aggTrades».

⚠ ИСТОРИЯ. ПЕРВАЯ редакция `fetch_agg_trades_window` брала первую страницу по `since`,
а последующие — курсором `fromId`, и этот пробник поймал её падение вживую
(2026-08-11, свидетельство: docs/audit/evidence/E-rest-backfill-2026-08-11/):

    ПРОВЕРЯЕМОЕ, 48 часов назад: ПАДЕНИЕ, не деградация: ccxt.base.errors.OperationFailed
    binanceusdm {"code":-1000,...}  — мимо ОБОИХ перехватчиков (`OperationFailed`
    наследуется от `BaseError` напрямую, не от `NetworkError`/`ExchangeError`)

После этого метод переведён на курсор ПО ВРЕМЕНИ и получил ловца `OperationFailed`.
Теперь пробник — РЕГРЕСС-ПРОВЕРКА: обе строки обязаны печатать «ПОЛНОЕ»; всякий
возврат «ПАДЕНИЕ» или «ЧАСТИЧНОЕ» на этих окнах — регресс пагинации.

КОНТРОЛЬ (обязателен по CLAUDE.md). Свежее окно проверяется рядом со старым: если не
качается и оно — сломан транспорт вообще, и вывод про глубину не следует.

Воспроизведение:
    uv run python docs/audit/probes/probe_backfill_window_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter.exchange import Exchange  # noqa: E402
from hunter.models import NotReady  # noqa: E402

SYMBOL = "BTC/USDT:USDT"
HOUR_MS = 3_600_000
WINDOW_MS = HOUR_MS  # час рынка: заведомо больше одной страницы (замер: ~314 с)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%SZ")


async def one(ex: Exchange, label: str, from_ms: int) -> None:
    to_ms = from_ms + WINDOW_MS
    got_n = 0

    def on_page(page: list[object]) -> None:
        nonlocal got_n
        got_n += len(page)

    print(f"\n=== {label}: окно {iso(from_ms)} … {iso(to_ms)} ===")
    try:
        res = await ex.fetch_agg_trades_window(SYMBOL, from_ms, to_ms, on_page)
    except Exception as e:  # noqa: BLE001 — тип падения И ЕСТЬ результат замера
        print(f"  ПАДЕНИЕ, не деградация: {type(e).__module__}.{type(e).__name__}")
        print(f"  {str(e)[:150]}")
        print(f"  сделок успело прийти: {got_n}")
        return
    if isinstance(res, NotReady):
        print(f"  NotReady: {res.reason}")
        print(f"  сделок успело прийти: {got_n}")
        return
    total, covered = res
    pct = (covered - from_ms) / WINDOW_MS * 100
    verdict = "ПОЛНОЕ" if covered >= to_ms else f"ЧАСТИЧНОЕ {pct:.1f}%"
    print(f"  сделок {total}, покрыто до {iso(covered)} — {verdict}")


async def main() -> int:
    ex = Exchange()
    await ex.open()
    try:
        now = await ex.fetch_server_ms()
        print(f"время биржи {iso(now)}")
        # Контроль: свежее суток — обязано выкачаться целиком.
        await one(ex, "КОНТРОЛЬ, 3 часа назад", now - 3 * HOUR_MS)
        # Проверяемый случай: ровно то, ради чего метод написан.
        await one(ex, "ПРОВЕРЯЕМОЕ, 48 часов назад", now - 48 * HOUR_MS)
        print(f"\nотказы REST, учтённые объектом: {dict(ex.rest_errors)}")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
