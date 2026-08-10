"""Пробник хвостов ревизии транспорта — 2026-08-10.

Три правки, три контроля «способен ли прибор ответить иначе»:

1. ГЛОБАЛЬНАЯ пауза лимита: без объявленной тишины ворота не держат (контроль),
   с объявленной — держат не короче объявленного, и вторая, более короткая тишина
   первую НЕ укорачивает.
2. Догон сделок по fromId: отдаёт ровно номера разрыва (хвост страницы за границей
   отбрасывается), предел `TRADE_RECOVER_MAX_IDS` режет широкий разрыв, пустой ответ
   биржи не выдаётся за добор (контроль).
3. Впрыск добранного в поток: подсаженный разрыв 4-6 добирается в гистограмму, счётчики
   потери потока при этом НЕ обнуляются; отказ добора считается в `trades_unrecovered`
   (контроль — прибор отвечает по-разному на добор и на отказ).

Сеть не трогается: биржевые методы подменены заглушками.

Запуск:
    uv run python docs/audit/probes/probe_transport_tails_2026-08-10.py
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter import clock
from hunter.exchange import TRADE_RECOVER_MAX_IDS, Exchange
from hunter.models import BarBinnedTrades, NotReady, RawTrade, TradeHistogram
from hunter.run import TradeSequence, _watch_trades_impl

TS0 = 1_700_000_000_000


def _raw(i: int) -> dict[str, Any]:
    return {"id": str(i), "price": 100.0 + (i % 50) * 0.01, "amount": 1.0,
            "timestamp": TS0 + i}


def _trade(i: int) -> RawTrade:
    return RawTrade(price=100.0 + (i % 50) * 0.01, amount=1.0,
                    timestamp=TS0 + i, id=str(i))


async def probe_quiet_gate() -> None:
    ex = Exchange()
    t0 = clock.monotonic_ns()
    await ex._quiet_gate()
    fast_s = (clock.monotonic_ns() - t0) / 1e9
    assert fast_s < 0.2 and ex.rest_gate_held == 0, \
        f"контроль: без тишины ворота держать не должны ({fast_s=})"

    ex._declare_quiet(1.0)
    ex._declare_quiet(0.2)  # короче уже объявленной — укорачивать не должна
    t0 = clock.monotonic_ns()
    await ex._quiet_gate()
    held_s = (clock.monotonic_ns() - t0) / 1e9
    assert held_s >= 0.8, f"тишина 1.0 с не выдержана: {held_s=}"
    assert ex.rest_gate_held == 1, f"придержанный вызов не сосчитан: {ex.rest_gate_held=}"
    await ex.close()
    print(f"OK ворота: без тишины {fast_s * 1000:.0f} мс, с тишиной {held_s:.2f} с, "
          f"придержано {ex.rest_gate_held}")


async def probe_recover_bounds() -> None:
    ex = Exchange()
    calls: list[tuple[int, int]] = []

    async def fake_fetch_trades(symbol: str, limit: int = 0,
                                params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        start = int((params or {})["fromId"])
        calls.append((start, limit))
        # Биржа отдаёт страницу ЦЕЛИКОМ — в том числе номера за границей разрыва.
        return [_raw(i) for i in range(start, start + limit)]

    ex._ex.fetch_trades = fake_fetch_trades  # type: ignore[method-assign]

    got = await ex.fetch_agg_trades_from("X", 100, 110)
    assert not isinstance(got, NotReady)
    ids = [int(t.id or 0) for t in got]
    assert ids == list(range(100, 110)), f"границы разрыва нарушены: {ids}"
    assert ex.trades_recovered == 10

    wide = await ex.fetch_agg_trades_from("X", 0, 5000)
    assert not isinstance(wide, NotReady)
    assert len(wide) == TRADE_RECOVER_MAX_IDS, \
        f"предел догона не сработал: {len(wide)} против {TRADE_RECOVER_MAX_IDS}"

    async def fake_empty(symbol: str, limit: int = 0,
                         params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    ex._ex.fetch_trades = fake_empty  # type: ignore[method-assign]
    empty = await ex.fetch_agg_trades_from("X", 200, 210)
    assert not isinstance(empty, NotReady) and len(empty) == 0, \
        "контроль: пустой ответ биржи не должен выдаваться за добор"
    await ex.close()
    print(f"OK догон: точный разрыв 10/10, широкий срезан до {len(wide)}, "
          f"пустой ответ дал 0, страниц запрошено {len(calls)}")


def _containers() -> tuple[TradeHistogram, BarBinnedTrades, TradeSequence]:
    tick = Decimal("0.01")
    return (TradeHistogram(symbol="X", tick_size=tick),
            BarBinnedTrades(symbol="X", tick_size=tick, bucket_ms=300_000),
            TradeSequence("X"))


async def probe_stream_injection() -> None:
    async def run_case(recover_ok: bool) -> tuple[TradeHistogram, TradeSequence, Exchange]:
        ex = Exchange()
        stop = asyncio.Event()

        async def fake_stream(sym: str) -> Any:
            yield [_trade(i) for i in (1, 2, 3)]
            stop.set()
            yield [_trade(i) for i in (7, 8)]  # подсаженный разрыв: потеряны 4-6

        async def fake_recover(sym: str, lo: int, hi: int) -> list[RawTrade] | NotReady:
            if not recover_ok:
                return NotReady(reason="пробник: REST отказал")
            return [_trade(i) for i in range(lo, hi)]

        ex.watch_agg_trades = fake_stream  # type: ignore[method-assign]
        ex.fetch_agg_trades_from = fake_recover  # type: ignore[method-assign]
        hist, binned, seq = _containers()
        await _watch_trades_impl(ex, "X", hist, binned, seq, stop)
        await ex.close()
        return hist, seq, ex

    hist, seq, ex = await run_case(recover_ok=True)
    assert seq.gaps == 3 and seq.gap_events == 1, \
        f"счётчик потери потока обязан остаться честным: {seq.gaps=}, {seq.gap_events=}"
    assert hist.trades_seen == 8, f"добор не дошёл до гистограммы: {hist.trades_seen=}"
    assert ex.trades_unrecovered == 0

    hist2, seq2, ex2 = await run_case(recover_ok=False)
    assert hist2.trades_seen == 5, "контроль: при отказе REST добора быть не должно"
    assert ex2.trades_unrecovered == 3, f"остаток не сосчитан: {ex2.trades_unrecovered=}"
    print(f"OK впрыск: добор 8 сделок при потере {seq.gaps}, "
          f"отказ REST оставил {ex2.trades_unrecovered} потерянными")


async def main() -> None:
    await probe_quiet_gate()
    await probe_recover_bounds()
    await probe_stream_injection()
    print("OK все три контроля прошли")


if __name__ == "__main__":
    asyncio.run(main())
