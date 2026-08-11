"""Насколько ГЛУБОКО REST Binance USDⓈ-M отдаёт aggTrades — замер, а не документация.

Повод. Владелец назвал ключевой архитектурной ошибкой то, что живой прогон берёт сделки
из суточных архивов data.binance.vision: архив публикуется с задержкой, и 10-11 августа
отдавал HTTP 404, из-за чего самые свежие структуры 5м/15м/1ч уровней не получили.
Правильный источник для живого — ccxt REST/WS.

Но прежде чем переписывать транспорт, надо знать, ЧТО REST способен отдать. Здесь два
утверждения, и они противоречат друг другу:

  * документация Binance (USDⓈ-M, Compressed/Aggregate Trades List): "support querying
    futures trade histories that are not older than 24 hours";
  * докстрока `src/hunter/archive.py`: это неверно, замер 2026-08-04 запросом с `since`
    от 17.07 вернул 1000 сделок за 0.45 с.

⚠ В том замере записано ТОЛЬКО число сделок и время ответа. Времена самих сделок не
проверялись — то есть не проверено, что биржа ответила на ЗАДАННЫЙ вопрос, а не отдала
свежий хвост. Ровно этот класс дефекта («прибор ответил» ≠ «ответ верен») перечислен в
CLAUDE.md.

ЧТО МЕРЯЕТСЯ. Для набора отступов назад (1 ч, 12 ч, 26 ч, 48 ч, 7 сут, 30 сут) три формы
запроса, и у каждой сверяется ВРЕМЯ ПЕРВОЙ ОТДАННОЙ СДЕЛКИ с запрошенным:

  A `since=T`               — как это делает ccxt по умолчанию;
  B `startTime`/`endTime`   — окно 59 минут, форма, разрешённая документацией;
  C `fromId`                — курсор по номеру, если номер за ту дату известен.

КОНТРОЛЬ (обязателен по CLAUDE.md: прибор должен уметь ответить иначе). Тот же запрос с
отступом 1 час обязан вернуть сделки ЧАСОВОЙ давности, а не свежие. Если и близкий, и
далёкий запрос дают одно и то же «сейчас» — прибор заперт, и никакие его ответы про
глубину не значат ничего.

Воспроизведение:
    uv run python docs/audit/probes/probe_rest_trade_depth_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import ccxt.async_support as ccxt  # noqa: E402

SYMBOL = "BTC/USDT:USDT"
HOUR_MS = 3_600_000
OFFSETS_H = (1, 12, 26, 48, 24 * 7, 24 * 30)


def iso(ms: int | None) -> str:
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def verdict(asked_ms: int, got_ms: int | None) -> str:
    """Сошлось ли отданное с запрошенным. Допуск — час: сделок в секунде тысячи."""
    if got_ms is None:
        return "ПУСТО"
    drift_h = (got_ms - asked_ms) / HOUR_MS
    if abs(drift_h) <= 1:
        return f"СОШЛОСЬ (сдвиг {drift_h:+.2f} ч)"
    return f"НЕ СОШЛОСЬ (сдвиг {drift_h:+.1f} ч)"


async def main() -> int:
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    rows: list[tuple[str, str, str, int, str]] = []
    try:
        await ex.load_markets()
        server_ms = int((await ex.fetch_time()))
        print(f"символ {SYMBOL}; время биржи {iso(server_ms)}")
        print(f"ccxt {ccxt.__version__}\n")

        for off_h in OFFSETS_H:
            asked = server_ms - off_h * HOUR_MS
            label = f"{off_h} ч назад" if off_h < 48 else f"{off_h // 24} сут назад"

            # A: since — то, как зовёт ccxt по умолчанию.
            t0 = time.perf_counter()
            try:
                a = await ex.fetch_trades(SYMBOL, since=asked, limit=1000)
                a_ms = int(a[0]["timestamp"]) if a else None
                rows.append((label, "since", iso(a_ms), len(a), verdict(asked, a_ms)))
            except Exception as e:  # noqa: BLE001 — тип отказа и есть результат замера
                rows.append((label, "since", f"ОТКАЗ {type(e).__name__}", 0, str(e)[:70]))
            dt_a = time.perf_counter() - t0

            # B: startTime/endTime, окно 59 минут — форма из документации.
            try:
                b = await ex.fetch_trades(SYMBOL, limit=1000, params={
                    "startTime": asked, "endTime": asked + 59 * 60_000})
                b_ms = int(b[0]["timestamp"]) if b else None
                rows.append((label, "start/endTime", iso(b_ms), len(b),
                             verdict(asked, b_ms)))
            except Exception as e:  # noqa: BLE001
                rows.append((label, "start/endTime", f"ОТКАЗ {type(e).__name__}", 0,
                             str(e)[:70]))

            print(f"  {label}: запрошено {iso(asked)}, since за {dt_a:.2f} с")

        print()
        print(f"{'отступ':<14} {'форма':<14} {'первая сделка':<22} {'штук':>5}  вердикт")
        print("-" * 100)
        for label, form, first, n, v in rows:
            print(f"{label:<14} {form:<14} {first:<22} {n:>5}  {v}")

        print()
        print("КОНТРОЛЬ: строка '1 ч назад' обязана дать сделки часовой давности.")
        print("Если она даёт 'сейчас' — прибор заперт и остальные строки ничего не значат.")
    finally:
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
