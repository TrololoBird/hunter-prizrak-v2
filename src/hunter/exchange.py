"""Транспорт Binance USDⓈ-M. FOUNDATION.md §5 — только публичные потоки.

Единственное место в проекте, которое ходит в сеть за рыночными данными.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import ccxt
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

WS_SILENCE_S = 60.0
"""Молчание потока БАРОВ дольше этого — поломка. ЗАМЕР 2026-08-04, BTC/USDT:USDT.

`watch_ohlcv` отвечает med 0.40 с, p90 0.72 с, max 1.13 с на 1Н и med 0.38 / max 0.83 на
5м. Binance обновляет свечу ПО ТАЙМЕРУ, а не по сделке, поэтому тишина на барах означает
именно обрыв, а не тихий рынок. Запас ×53 к наибольшему наблюдённому промежутку.
"""

WS_TRADES_SILENCE_S = 300.0
"""Для СДЕЛОК порог отдельный, и это следствие замера, а не симметрии.

Сделки — поток СОБЫТИЙНЫЙ: тишина в нём означает «сделок не было», то есть данные, а не
протухание. Первая редакция брала общий порог 60 с, и он назывался сознательным разменом
«лучше ложная тревога, чем молчаливая смерть». Замер 2026-08-04 показал, что размен был
куда ближе к краю, чем казалось.

Самый тонкий символ вселенной — ASTR/USDT:USDT, оборот за сутки 0.46 млн USDT против
9263 млн у BTC (в 20 000 раз тоньше). Его `watch_trades` за 240 с: пакетов 24,
med 7.91 с, p90 21.63 с, p99 43.37 с, **max 43.37 с**. Промежутков ≥ 60 с — НОЛЬ, то
есть тревога не сработала бы; но запас составлял ×1.38, а не ×53 как на барах.

⚠ Выборка 240 секунд днём. Ночью и в выходные тонкий символ молчит дольше, и этого я не
мерил. 300 с — это ×6.9 к наблюдённому максимуму; число выбрано как запас к ЗАМЕРУ, но
сам замер короткий, и это его главное ограничение.
"""

WS_RETRY_S = 1.0
"""Пауза перед повторной подпиской. Число ПОДОБРАНО, а не замерено, и здесь названо так."""


class Exchange:
    def __init__(self) -> None:
        self._ex = ccxtpro.binanceusdm({
            "enableRateLimit": True,
            # FOUNDATION.md §5: профиль строится на aggTrade. По умолчанию ccxt.pro
            # подписан на поток 'trade' — здесь он переключён явно.
            "options": {"watchTrades": {"name": "aggTrade"}},
        })
        self._instruments: dict[str, Instrument] = {}
        self.ws_reconnects: Counter[str] = Counter()
        self.ws_errors: Counter[str] = Counter()
        """Переподключения и ошибки биржи по потокам. Читает отчёт прогона.

        Живут на объекте, а не внутри генератора: генератор о структуре отчёта не знает,
        а без счётчика переподключение неотличимо от бесперебойной работы.
        """

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
        self,
        symbol: str,
        timeframe: str,
        limit: int = CCXT_EFFECTIVE_LIMIT,
        since_ms: int | None = None,
    ) -> OhlcvFetch | NotReady:
        """REST-засев. Незакрытая свеча отбрасывается здесь же (§6).

        Битый бар (экстремумы не накрывают open/close) не роняет прогон и не
        проходит молча: он отклоняется, причина с числами уходит наверх и в лог.
        Замер 2026-08-03: 1 такой бар на 73 828 — BCH/USDT:USDT 1w 2020-01-13,
        подтверждён сырым ответом биржи, см. docs/audit/broken-bar-bch-2026-08-03.md

        `since_ms` отматывает окно назад. Нужен для приёмки этапа 3: разборы корпуса
        датированы июлем, а без него доступны только последние `limit` баров — для 15м
        это 10.4 суток, то есть до дат разборов окно не достаёт.
        """
        raw = await self._ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
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

    async def count_history(self, symbol: str, timeframe: str, *, cap: int) -> int:
        """Сколько баров биржа отдаёт по символу и ТФ.

        `cap` — считать до отсечки и остановиться: для допуска важно «хватает ли», а не
        точное число. Возвращённое значение при достижении отсечки означает «не меньше cap».

        ⚠ Параметр ОБЯЗАТЕЛЕН и только именованный. Раньше стояло `cap: int = 0`, то есть
        «без предела» по умолчанию: пагинация шла до исчерпания истории — по 5м это 15
        страниц на символ, на 27 символах × 6 ТФ сотни запросов подряд. Оба нынешних
        вызывающих отсечку передают, так что мина была скрытой; умолчание её и прятало.
        """
        if cap <= 0:
            raise ValueError(f"count_history({symbol} {timeframe}): cap обязан быть > 0")
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

    async def _watch_step(
        self, what: str, key: str, factory: Any, silence_s: float
    ) -> Any | None:
        """Одно ожидание WS с таймаутом и переподключением. `None` — попытка не удалась.

        ⚠ Раньше переподключения не было ВООБЩЕ: генератор делал `while True: yield await
        …`, и первое же сетевое исключение завершало его навсегда. Процесс, задуманный
        работать сутками, деградировал до нуля потоков без единого сообщения.

        Порог молчания подаётся аргументом, потому что у баров и сделок он РАЗНОЙ ПРИРОДЫ:
        бары идут по таймеру биржи (тишина = обрыв), сделки — по событию (тишина = данные).
        Числа и их замеры — в `WS_SILENCE_S` и `WS_TRADES_SILENCE_S`.
        """
        try:
            return await asyncio.wait_for(factory(), timeout=silence_s)
        except TimeoutError:
            self.ws_reconnects[key] += 1
            log.degraded(f"{what}: поток молчит дольше порога, переподключение",
                         символ=key, порог_с=silence_s)
        except ccxt.NetworkError as e:
            self.ws_reconnects[key] += 1
            log.degraded(f"{what}: сетевой сбой, переподключение",
                         символ=key, причина=f"{type(e).__name__} {e}")
        except ccxt.ExchangeError as e:
            self.ws_errors[key] += 1
            log.error(f"{what}: ошибка биржи", символ=key,
                      причина=f"{type(e).__name__} {e}")
        await asyncio.sleep(WS_RETRY_S)
        return None

    async def watch_closed_ohlcv(self, symbol: str, timeframe: str) -> AsyncGenerator[Bar]:
        """WS-поток. Отдаёт бар только после его закрытия (§6).

        Признак закрытия — биржевое время дошло до правой границы, а не «отбросить
        последний элемент кэша»: последний элемент бывает и закрытым, и тогда
        отбрасывание подало бы позапрошлый бар.

        Курсор `emitted` живёт ВНЕ попытки и потому переживает переподключение. Иначе
        после обрыва уже отданные бары ушли бы повторно, а потребитель их не различает.
        """
        emitted: int | None = None
        key = f"{symbol} {timeframe}"
        while True:
            raw = await self._watch_step(
                "бары", key, lambda: self._ex.watch_ohlcv(symbol, timeframe),
                WS_SILENCE_S)
            if raw is None:
                continue
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
            batch = await self._watch_step(
                "сделки", symbol, lambda: self._ex.watch_trades(symbol),
                WS_TRADES_SILENCE_S)
            if batch is not None:
                yield batch


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
