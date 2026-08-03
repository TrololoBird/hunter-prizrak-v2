"""Транспорт Binance USDⓈ-M. FOUNDATION.md §5 — только публичные потоки.

Единственное место в проекте, которое ходит в сеть за рыночными данными.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import ccxt.pro as ccxtpro

from . import clock, log
from .bars import Bar, closed_only, on_grid, tf_ms
from .quality import NotReady

# Замер 2026-08-03: /fapi/v1/klines принимает limit=1500, на 1501 отвечает
# HTTP 400 code -1130. ccxt при этом сам режет выдачу до 1000.
# Протокол: docs/audit/exchange-limits-2026-08-03.md
KLINES_MAX_LIMIT = 1500
CCXT_EFFECTIVE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    tick_size: Decimal
    """Шаг цены из фильтра PRICE_FILTER. §5: бины профиля привязаны к нему."""


class Exchange:
    def __init__(self) -> None:
        self._ex = ccxtpro.binanceusdm({
            "enableRateLimit": True,
            # FOUNDATION.md §5: профиль строится на aggTrade. По умолчанию ccxt.pro
            # подписан на поток 'trade' — здесь он переключён явно.
            "options": {"watchTrades": {"name": "aggTrade"}},
        })
        self._instruments: dict[str, Instrument] = {}

    async def open(self) -> clock.ClockSync:
        await self._ex.load_markets()
        sync = await clock.measure(self.fetch_server_ms)
        log.info(
            f"часы сведены: сдвиг {sync.offset_ms:+d} мс, rtt {sync.rtt_ms} мс, "
            f"замеров {sync.samples}"
        )
        return sync

    async def close(self) -> None:
        await self._ex.close()

    async def fetch_server_ms(self) -> int:
        return int(await self._ex.fetch_time())

    # --- инструменты -------------------------------------------------------

    def instrument(self, symbol: str) -> Instrument | NotReady:
        if symbol in self._instruments:
            return self._instruments[symbol]
        market = self._ex.markets.get(symbol)
        if market is None:
            return NotReady(f"{symbol}: нет на бирже")
        tick = _price_filter_tick(market)
        if tick is None:
            return NotReady(f"{symbol}: в PRICE_FILTER нет tickSize")
        inst = Instrument(symbol=symbol, tick_size=tick)
        self._instruments[symbol] = inst
        return inst

    # --- OHLCV -------------------------------------------------------------

    async def fetch_closed_ohlcv(
        self, symbol: str, timeframe: str, limit: int = CCXT_EFFECTIVE_LIMIT
    ) -> list[Bar] | NotReady:
        """REST-засев. Незакрытая свеча отбрасывается здесь же (§6)."""
        raw = await self._ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not raw:
            return NotReady(f"{symbol} {timeframe}: биржа вернула пустой список")
        bars = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in raw]
        off_grid = [b.open_ms for b in bars if not on_grid(b.open_ms, timeframe)]
        if off_grid:
            return NotReady(
                f"{symbol} {timeframe}: {len(off_grid)} баров вне сетки, первый {off_grid[0]}"
            )
        closed = closed_only(bars, timeframe, clock.now_ms())
        if not closed:
            return NotReady(f"{symbol} {timeframe}: все {len(bars)} баров ещё не закрыты")
        return closed

    async def watch_closed_ohlcv(self, symbol: str, timeframe: str) -> AsyncGenerator[Bar]:
        """WS-поток. Отдаёт бар только после его закрытия (§6).

        Признак закрытия — биржевое время дошло до правой границы, а не «отбросить
        последний элемент кэша»: последний элемент бывает и закрытым, и тогда
        отбрасывание подало бы позапрошлый бар.
        """
        emitted: int | None = None
        while True:
            raw = await self._ex.watch_ohlcv(symbol, timeframe)
            now = clock.now_ms()
            for r in raw:
                open_ms = int(r[0])
                if emitted is not None and open_ms <= emitted:
                    continue
                if now < open_ms + tf_ms(timeframe):
                    continue
                emitted = open_ms
                yield Bar(open_ms, float(r[1]), float(r[2]), float(r[3]),
                          float(r[4]), float(r[5]))

    # --- сделки ------------------------------------------------------------

    async def watch_agg_trades(self, symbol: str) -> AsyncGenerator[list[dict[str, Any]]]:
        while True:
            yield await self._ex.watch_trades(symbol)


def _price_filter_tick(market: dict[str, Any]) -> Decimal | None:
    info = market.get("info") or {}
    for f in info.get("filters", []):
        if f.get("filterType") == "PRICE_FILTER":
            raw = f.get("tickSize")
            if raw is None:
                return None
            tick = Decimal(str(raw))
            return tick if tick > 0 else None
    return None
