"""Транспорт Binance USDⓈ-M. FOUNDATION.md §5 — только публичные потоки.

Единственное место в проекте, которое ходит в сеть за рыночными данными.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import ccxt.pro as ccxtpro
from pydantic import ValidationError

from . import clock, log
from .bars import closed_only, on_grid, tf_ms
from .models import Bar, ClockSync, Instrument, NotReady, OhlcvFetch

# Замер 2026-08-03: /fapi/v1/klines принимает limit=1500, на 1501 отвечает
# HTTP 400 code -1130. ccxt при этом сам режет выдачу до 1000.
# Протокол: docs/audit/exchange-limits-2026-08-03.md
KLINES_MAX_LIMIT = 1500
CCXT_EFFECTIVE_LIMIT = 1000


class Exchange:
    def __init__(self) -> None:
        self._ex = ccxtpro.binanceusdm({
            "enableRateLimit": True,
            # FOUNDATION.md §5: профиль строится на aggTrade. По умолчанию ccxt.pro
            # подписан на поток 'trade' — здесь он переключён явно.
            "options": {"watchTrades": {"name": "aggTrade"}},
        })
        self._instruments: dict[str, Instrument] = {}

    async def open(self) -> ClockSync:
        await self._ex.load_markets()
        sync = await clock.measure(self.fetch_server_ms)
        log.info("часы сведены", сдвиг_мс=sync.offset_ms, rtt_мс=sync.rtt_ms,
                 замеров=sync.samples)
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
            return NotReady(reason=f"{symbol}: нет на бирже")
        tick = _price_filter_tick(market)
        if tick is None:
            return NotReady(reason=f"{symbol}: в PRICE_FILTER нет tickSize")
        market_id = market.get("id")
        if not market_id:
            return NotReady(reason=f"{symbol}: у рынка нет id для архива")
        inst = Instrument(symbol=symbol, market_id=str(market_id), tick_size=tick)
        self._instruments[symbol] = inst
        return inst

    def markets_by_id(self) -> dict[str, str]:
        """ccxt-символ → идентификатор биржи (BTCUSDT). Нужен для архива."""
        return {sym: str(mk.get("id")) for sym, mk in self._ex.markets.items()}

    # --- OHLCV -------------------------------------------------------------

    async def fetch_closed_ohlcv(
        self, symbol: str, timeframe: str, limit: int = CCXT_EFFECTIVE_LIMIT
    ) -> OhlcvFetch | NotReady:
        """REST-засев. Незакрытая свеча отбрасывается здесь же (§6).

        Битый бар (экстремумы не накрывают open/close) не роняет прогон и не
        проходит молча: он отклоняется, причина с числами уходит наверх и в лог.
        Замер 2026-08-03: 1 такой бар на 73 828 — BCH/USDT:USDT 1w 2020-01-13,
        подтверждён сырым ответом биржи, см. docs/audit/broken-bar-bch-2026-08-03.md
        """
        raw = await self._ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not raw:
            return NotReady(reason=f"{symbol} {timeframe}: биржа вернула пустой список")
        bars: list[Bar] = []
        rejected: list[str] = []
        for r in raw:
            try:
                bars.append(Bar(open_ms=int(r[0]), open=float(r[1]), high=float(r[2]),
                                low=float(r[3]), close=float(r[4]), volume=float(r[5])))
            except ValidationError as e:
                why = (f"{symbol} {timeframe} бар {int(r[0])}: o={r[1]} h={r[2]} "
                       f"l={r[3]} c={r[4]} — {e.errors()[0]['msg']}")
                rejected.append(why)
                log.error("бар отклонён как битый", причина=why)
        if not bars:
            return NotReady(
                reason=f"{symbol} {timeframe}: все {len(raw)} баров отклонены как битые"
            )
        off_grid = [b.open_ms for b in bars if not on_grid(b.open_ms, timeframe)]
        if off_grid:
            return NotReady(
                reason=f"{symbol} {timeframe}: {len(off_grid)} баров вне сетки, "
                       f"первый {off_grid[0]}"
            )
        closed = closed_only(bars, timeframe, clock.now_ms())
        if not closed:
            return NotReady(
                reason=f"{symbol} {timeframe}: все {len(bars)} баров ещё не закрыты"
            )
        return OhlcvFetch(bars=closed, rejected=rejected)

    async def count_history(self, symbol: str, timeframe: str, cap: int = 0) -> int:
        """Сколько баров биржа отдаёт по символу и ТФ.

        `cap > 0` — считать до отсечки и остановиться: для допуска важно «хватает
        ли», а не точное число. Без отсечки счёт по 5м занимает 15 страниц на символ.
        Возвращённое значение при достижении отсечки означает «не меньше cap».
        """
        total, since = 0, 0
        while True:
            r = await self._ex.fetch_ohlcv(symbol, timeframe, since=since,
                                           limit=CCXT_EFFECTIVE_LIMIT)
            if not r:
                break
            total += len(r)
            if cap and total >= cap:
                return total
            if len(r) < CCXT_EFFECTIVE_LIMIT:
                break
            since = int(r[-1][0]) + 1
        return total

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
                yield Bar(open_ms=open_ms, open=float(r[1]), high=float(r[2]),
                          low=float(r[3]), close=float(r[4]), volume=float(r[5]))

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
