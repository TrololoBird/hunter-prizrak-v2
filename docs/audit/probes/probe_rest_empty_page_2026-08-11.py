"""Что означает ПУСТОЙ ответ `fetch_trades(since=T)` — «сделок больше нет» или «нет в час»?

Повод. `Exchange.fetch_agg_trades_window` на пустой странице делает вывод, что окно
покрыто целиком:

    if not raw:
        # Биржа говорит «после since сделок нет»: всё до to_ms покрыто, просто пусто.
        return total, to_ms

⚠ Вывод опирается на то, что запрос ограничен снизу и НЕ ограничен сверху. Исходник
ccxt 4.5.71 (`ccxt/async_support/binance.py::fetch_trades`) говорит обратное:

    if since is not None:
        request['startTime'] = since
        request['endTime'] = self.sum(since, 3600000)

То есть ccxt САМ дописывает верхнюю границу в ОДИН ЧАС (обход правила Binance «time
between startTime and endTime must be less than 1 hour»). Тогда пустой ответ означает
«в этот час сделок нет», и на тонком символе с часовой паузой добор остановится, ОБЪЯВИВ
ПОЛНОЕ ПОКРЫТИЕ, — молчаливое усечение, запрещённое §4.3.

ЧТО МЕРЯЕТСЯ. Для каждого символа берётся окно суточной давности и печатается:
  * сколько сделок вернула страница (упёрлась ли в limit=1000);
  * размах страницы по времени и её расстояние до границы «час от since».
Если страница НЕ упёрлась в limit, а последняя сделка стоит ровно у отметки since+1ч —
границу поставило ВРЕМЯ, а не исчерпание сделок.

Затем прямая проверка следствия: ищется час без сделок. Если для какого-то часа ответ
пуст, а в следующие часы сделки есть — вывод «после since сделок нет» опровергнут.

КОНТРОЛЬ (обязателен по CLAUDE.md). Тот же запрос на BTC обязан вернуть ровно 1000 сделок
и размах СИЛЬНО меньше часа: там сделок больше, чем помещается на страницу, и границу
ставит limit. Если и на BTC размах равен часу — меряется что-то другое.

Воспроизведение:
    uv run python docs/audit/probes/probe_rest_empty_page_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import ccxt.async_support as ccxt  # noqa: E402

HOUR_MS = 3_600_000
# Плотный (контроль) и заведомо тонкие символы вселенной.
SYMBOLS = ("BTC/USDT:USDT", "ARPA/USDT:USDT", "BICO/USDT:USDT", "1000RATS/USDT:USDT")


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%SZ")


async def main() -> int:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    try:
        await ex.load_markets()
        now = int(await ex.fetch_time())
        base = now - 26 * HOUR_MS  # старше суток: заведомо не «свежий хвост»
        print(f"время биржи {iso(now)}; окно от {iso(base)}\n")

        print(f"{'символ':<20} {'сделок':>7} {'размах стр.':>13} {'до +1ч':>9}  что поставило границу")
        print("-" * 92)
        empty_found: list[tuple[str, int]] = []
        for sym in SYMBOLS:
            page = await ex.fetch_trades(sym, since=base, limit=1000)
            if not page:
                print(f"{sym:<20} {0:>7} {'—':>13} {'—':>9}  ПУСТО за час")
                empty_found.append((sym, base))
                continue
            first = int(page[0]["timestamp"])
            last = int(page[-1]["timestamp"])
            span_s = (last - first) / 1000
            gap_s = (base + HOUR_MS - last) / 1000
            if len(page) >= 1000:
                why = "limit=1000 (сделок больше, чем страница)"
            elif gap_s < 60:
                why = "ВРЕМЯ: страница обрезана отметкой since+1ч"
            else:
                why = "сделки кончились раньше часа"
            print(f"{sym:<20} {len(page):>7} {span_s:>12.0f}с {gap_s:>8.0f}с  {why}")

        # Прямая проверка следствия: час без сделок при наличии сделок дальше.
        print("\nпоиск часа без сделок у самого тонкого символа:")
        thin = SYMBOLS[-1]
        for h in range(26, 26 + 12):
            t = now - h * HOUR_MS
            page = await ex.fetch_trades(thin, since=t, limit=1000)
            if not page:
                later = await ex.fetch_trades(thin, since=t + 2 * HOUR_MS, limit=1000)
                print(f"  {thin} час от {iso(t)}: ПУСТО")
                print(f"  тот же символ через 2 часа: {len(later)} сделок")
                if later:
                    print("  => ВЫВОД «после since сделок нет» ОПРОВЕРГНУТ:")
                    print("     пустая страница означает пустой ЧАС, а не конец сделок.")
                break
        else:
            print(f"  у {thin} за 12 проверенных часов пустых часов не нашлось —")
            print("  следствие не продемонстрировано прямо; вывод держится на")
            print("  исходнике ccxt и на строке 'ВРЕМЯ' в таблице выше.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
