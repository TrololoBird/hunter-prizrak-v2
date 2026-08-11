"""Работает ли транспорт на ВСЕХ трёх площадках Binance — спот, USDⓈ-M, COIN-M.

Повод. До 2026-08-11 класс биржи стоял в конструкторе константой (`ccxtpro.binanceusdm()`),
и никакой другой площадки транспорт не знал. Владелец: «полноценно подключи spot, futures,
rest, websocket». Площадка стала ключом конфигурации вселенной.

ЧТО МЕРЯЕТСЯ. На каждой площадке проходится ВЕСЬ путь открытия и обе дороги данных:
рынки, лимит веса (у каждой площадки СВОЙ путь к нему), статус биржи, сведение часов,
свечи REST, сделки REST, сделки вебсокетом, закрытие со снятием подписки.

КОНТРОЛЬ (обязателен по CLAUDE.md), двойной:

  1. `ЛИМИТ ВЕСА` читается по РАЗНЫМ путям (`fapiPublicGetExchangeInfo` у USDⓈ-M,
     `publicGetExchangeInfo` у спота, `dapiPublicGetExchangeInfo` у COIN-M). Если бы
     таблица площадок не работала, а класс оставался прежним, лимит на споте либо не
     прочитался бы, либо совпал с фьючерсным. Печатается и число, и путь;
  2. `ЦЕНА` одного и того же актива на разных площадках обязана РАЗЛИЧАТЬСЯ хотя бы в
     младших разрядах: спот и бессрочный фьючерс — разные рынки. Совпадение до цента
     означало бы, что все три ходят в одно место, и весь замер ничего не проверяет.

⚠ Замер живой: требует сети и занимает около минуты. Отказ ОДНОЙ площадки не отменяет
вывода про остальные — каждая печатает свою строку.

Воспроизведение:
    uv run python docs/audit/probes/probe_venues_2026-08-11.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter import clock
from hunter.exchange import VENUES, Exchange
from hunter.models import NotReady

# Один и тот же актив на трёх площадках — иначе контроль цены сравнивал бы разное.
SYMBOL = {
    "binance": "BTC/USDT",
    "binanceusdm": "BTC/USDT:USDT",
    "binancecoinm": "BTC/USD:BTC",
}


async def one(venue: str) -> dict[str, object]:
    ex = Exchange(venue)
    out: dict[str, object] = {"площадка": venue}
    try:
        sync = await ex.open()
        out["рынков"] = len(ex.markets_by_id())
        out["лимит веса"] = ex.weight_limit
        out["путь к лимиту"] = VENUES[venue].exchange_info
        out["сдвиг часов, мс"] = sync.offset_ms
        out["статус"] = await ex.read_status()

        sym = SYMBOL[venue]
        bars = await ex.fetch_closed_ohlcv(sym, "5m", limit=10)
        out["свечей REST"] = bars.reason if isinstance(bars, NotReady) else len(bars.bars)
        if not isinstance(bars, NotReady):
            out["последняя цена"] = bars.bars[-1].close

        now = clock.now_ms()
        got = await ex.fetch_agg_trades_window(sym, now - 120_000, now, lambda _p: None)
        out["сделок REST"] = got.reason if isinstance(got, NotReady) else got[0]

        seen = 0
        agen = ex.watch_agg_trades(sym)
        try:
            batch = await asyncio.wait_for(agen.__anext__(), timeout=30.0)
            seen = len(batch)
        except TimeoutError:
            seen = 0
        finally:
            await agen.aclose()
        out["сделок потоком"] = seen
    except Exception as e:  # noqa: BLE001 — отказ одной площадки не отменяет остальных
        out["ОТКАЗ"] = f"{type(e).__name__} {e}"
    finally:
        await ex.close()
        out["снято подписок"] = f"{ex.ws_unwatched}, не снято {ex.ws_unwatch_failed}"
    return out


async def main() -> int:
    rows = []
    for venue in ("binanceusdm", "binance", "binancecoinm"):
        print(f"--- {venue} ({VENUES[venue].human})")
        r = await one(venue)
        rows.append(r)
        for k, v in r.items():
            if k != "площадка":
                print(f"      {k:20} {v}")
        print()

    ok = [r for r in rows if "ОТКАЗ" not in r]
    print(f"площадок открылось: {len(ok)} из {len(rows)}")

    # --- КОНТРОЛЬ 1: пути к лимиту разные и лимит прочитан ---
    paths = {str(r.get("путь к лимиту")) for r in ok}
    limits = {r["площадка"]: r.get("лимит веса") for r in ok}
    print(f"\nКОНТРОЛЬ 1 — путь к лимиту различался: {len(paths)} разных на {len(ok)} "
          f"площадок; лимиты {limits}")
    control1 = len(paths) == len(ok) and all(v is not None for v in limits.values())

    # --- КОНТРОЛЬ 2: цены разные ---
    prices = {r["площадка"]: r.get("последняя цена") for r in ok
              if r.get("последняя цена") is not None}
    control2 = len(set(prices.values())) == len(prices) and len(prices) > 1
    print(f"КОНТРОЛЬ 2 — цены по площадкам: {prices}")

    print()
    if not ok:
        print("⚠ не открылась ни одна площадка — вывод не следует")
        return 1
    if not control1:
        print("⚠ КОНТРОЛЬ 1 ПРОВАЛЕН: пути к лимиту совпали или лимит не прочитан —")
        print("  таблица площадок могла не примениться, и вывод НЕ СЛЕДУЕТ.")
        return 1
    if not control2:
        print("⚠ КОНТРОЛЬ 2 ПРОВАЛЕН: цены совпали до разряда — похоже, все площадки")
        print("  ходят в одно место. Вывод о переключении НЕ СЛЕДУЕТ.")
        return 1
    print("Оба контроля пройдены: площадки различаются и путём к лимиту, и ценой,")
    print("то есть переключение состоялось, а не показалось.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
