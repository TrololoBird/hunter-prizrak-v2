"""Профиль объёма ИЗ СВЕЧЕЙ. Реализация `TradeWindows` поверх минутных баров.

⚠ ЧИСТЫЙ МОДУЛЬ: часы, сеть и глобальное состояние не трогаются. Бары передаются извне.

## Почему профиль строится по свечам, а не по сделкам

Решение владельца 2026-08-12. До него источником были отдельные сделки `aggTrade`, и это
держалось на ОДНОЙ фразе в скобках из первого коммита проекта («профиль объёма строится
на реальных сделках (aggTrade)») — без страницы курса, без цитаты корпуса, без строки
документации.

Три свидетельства против неё, все проверяемые:

1. **Жанр так не делает.** Двадцать чужих проектов склонированы и прогреплены: сигнальные
   боты берут свечи и отдельных сделок не берут вовсе (`CryptoSignal/Crypto-Signal` —
   5.6 тыс. звёзд, два вызова ccxt: `fetch_ohlcv` и `load_markets`). `aggTrade`
   встречается только у библиотек рыночных данных — cryptofeed, nautilus_trader,
   binance-public-data;
2. **Инструмент автора курса делает так же.** Документация TradingView: «Volume profile
   indicators are calculated using data from lower timeframes for the same symbol» — для
   дневной сессии грузятся МИНУТНЫЕ бары, а не тики;
3. **Замер против разметки автора не показал преимущества тиков.** Лучшая клетка таблицы
   свечная: ETH, 5м, 14 из 16 при равном числе уровней и медиане расстояния 0.03·ATR
   против 0.13·ATR у тикового; там же тиковый источник бьёт лишь 1 сдвиговый контроль из
   4, свечной — 4 из 4.

⚠ Замер при этом НЕ доказал и превосходства свечей: на BTC знак обратный. Разбор
называет это шумом и прямо говорит, что решение по таким числам принимать нельзя —
владелец принял его, зная это. Цена вопроса измерена и она на стороне свечей: сутки BTC
через `aggTrades` стоят 3500 единиц веса, через минутные свечи — 6.

Полный разбор: `docs/audit/poc-candles-vs-ticks-2026-08-12.md`

## Как объём свечи попадает в профиль

Объём бара раскладывается РАВНОМЕРНО по всем ценовым бинам его диапазона `low…high`.
Это семейство TradingView, и приближение здесь названо честно: внутри бара цена не
проводит одинаковое время на каждом уровне, и никакая свеча этого не знает. Чем младше
разрешение, тем меньше цена приближения, — поэтому умолчание `1m`.

Второй способ, «весь объём в бин цены закрытия» (так делает `volume_by_price` у
`Haehnchen/crypto-trading-bot`), ПРОВЕРЕН и ОТВЕРГНУТ: он хуже на обоих символах и всех
разрешениях замера.
"""

from __future__ import annotations

import bisect
from collections import OrderedDict
from decimal import Decimal

from .bars import tf_ms
from .models import Bar, NotReady, TradeHistogram, bin_index

MAX_BINS_PER_BAR = 200_000
"""Предел ширины одного бара в бинах. Свеча шире — свидетельство битых данных, а не
рынка: при шаге цены 0.1 это диапазон в 20 000 единиц котировки внутри одной минуты.
Такой бар в профиль НЕ идёт и СЧИТАЕТСЯ (`bars_too_wide`), потому что молчаливый пропуск
читался бы как «разложили всё»."""


_WindowKey = tuple[str, str, str, int, int, int, int]
_WINDOW_CACHE: OrderedDict[_WindowKey, TradeHistogram] = OrderedDict()
_WINDOW_CACHE_MAX = 40
_CACHE_MIN_BARS = 43_200
"""Кэш ДОРОГИХ окон профиля, переживающий циклы службы (2026-08-17).

Диагноз замером (evidence/profile-live-pipeline и зонд одного вызова): окно во весь
минутный ряд BTC стоит 9.2 с, 30-суточное — 0.9 с, и КАЖДЫЙ цикл службы строил их
заново — экземпляр `CandleWindows` живёт один цикл, а окна закрытых структур между
циклами НЕ МЕНЯЮТСЯ. Отсюда расчётная фаза в часы на 27 символах.

Устройство честности: ключ несёт ОТПЕЧАТОК ДАННЫХ (длина ряда и метка последнего бара
в окне) — дорос ряд, изменилось окно → ключ другой, пересчёт; никакой инвалидации по
времени. Кэшируются только окна от `_CACHE_MIN_BARS` минуток (30 суток): дешёвые
(63 мс на двух сутках) выгоднее пересчитать, чем держать. Ёмкость 40 — это композиты
VRVP всех символов вселенной плюс годовые окна старших ТФ.

⚠ Возвращается ОБЩИЙ объект: контракт потребителей — только чтение (build_level и
_with_vrvp гистограмму не мутируют). Значения при попадании в кэш БАЙТ-В-БАЙТ те же,
что при пересчёте, — это тот же объект, порядок сложений не менялся вовсе."""


class CandleWindows:
    """Профиль по окну из свечей. Контракт `TradeWindows`: один метод `window`.

    ⚠ ПОКРЫТИЕ ПРОВЕРЯЕТСЯ, А НЕ ПРЕДПОЛАГАЕТСЯ. Если в окне нет баров или в нём есть
    дыра длиннее одного шага разрешения, отдаётся `NotReady` с числом пропущенных баров.
    Без этой проверки неполное окно дало бы профиль, неотличимый от полного, — ровно тот
    класс дефекта, из-за которого §4.3 требует называть причину, а не отдавать пустое.
    """

    def __init__(self, symbol: str, tick: Decimal, bars: list[Bar],
                 timeframe: str = "1m") -> None:
        self.symbol = symbol
        self.tick = tick
        self.timeframe = timeframe
        self.step = tf_ms(timeframe)
        self.bars = sorted(bars, key=lambda b: b.open_ms)
        self._keys = [b.open_ms for b in self.bars]
        self.bars_too_wide = 0
        """Баров, отвергнутых как неправдоподобно широкие. Пара к построенным профилям."""

        self.windows_built = 0
        self.windows_refused = 0
        """Сколько окон построено и сколько отвергнуто. Ноль отказов при ненулевых
        построениях — свидетельство покрытия, а не отсутствия проверки."""

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        i = bisect.bisect_left(self._keys, from_ms)
        j = bisect.bisect_left(self._keys, to_ms)
        cache_key = None
        if j - i >= _CACHE_MIN_BARS:
            cache_key = (self.symbol, self.timeframe, str(self.tick), from_ms, to_ms,
                         j - i, self._keys[j - 1] if j > i else 0)
            hit = _WINDOW_CACHE.get(cache_key)
            if hit is not None:
                _WINDOW_CACHE.move_to_end(cache_key)
                self.windows_built += 1
                return hit
        chunk = self.bars[i:j]
        if not chunk:
            self.windows_refused += 1
            return NotReady(
                reason=f"{self.symbol}: окно {from_ms}..{to_ms} не покрыто свечами "
                       f"{self.timeframe}")
        want = max(1, (to_ms - from_ms) // self.step)
        if len(chunk) < want:
            self.windows_refused += 1
            return NotReady(
                reason=f"{self.symbol}: окно {from_ms}..{to_ms} покрыто свечами "
                       f"{self.timeframe} не полностью — {len(chunk)} из {want}")

        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick)
        # ⚠ РАЗНОСТНЫЙ МАССИВ, А НЕ ЦИКЛ ПО БИНАМ КАЖДОГО БАРА. Прямое добавление доли
        # объёма в каждый бин диапазона стоит O(баров × бинов): на минутных свечах BTC
        # это порядка тринадцати миллионов операций НА ОДНО ОКНО, и зонд с такой
        # реализацией не досчитывался вовсе. Здесь каждому бару правятся только концы
        # его отрезка, а один проход префиксной суммы разворачивает это в профиль —
        # O(баров + ширина диапазона).
        #
        # Равенство обоих способов ПРОВЕРЕНО, а не предположено: наибольшее расхождение
        # 9.1e-13 при масштабе 1.5e+03 на шести прогонах зонда
        # `docs/audit/probes/probe_author_candles_2026-08-12.py`.
        spans: list[tuple[int, int, float]] = []
        k_lo: int | None = None
        k_hi: int | None = None
        for b in chunk:
            k0 = bin_index(b.low, self.tick)
            k1 = bin_index(b.high, self.tick)
            if k1 - k0 + 1 > MAX_BINS_PER_BAR:
                self.bars_too_wide += 1
                continue
            spans.append((k0, k1, b.volume / (k1 - k0 + 1)))
            k_lo = k0 if k_lo is None else min(k_lo, k0)
            k_hi = k1 if k_hi is None else max(k_hi, k1)
            h.trades_seen += 1
            h.qty_seen += b.volume
        if k_lo is None or k_hi is None:
            self.windows_refused += 1
            return NotReady(
                reason=f"{self.symbol}: окно {from_ms}..{to_ms} — все {len(chunk)} свечей "
                       f"отвергнуты как неправдоподобно широкие")
        width = k_hi - k_lo + 2
        diff = [0.0] * width
        for k0, k1, share in spans:
            diff[k0 - k_lo] += share
            diff[k1 - k_lo + 1] -= share
        run = 0.0
        for idx in range(width - 1):
            run += diff[idx]
            if run > 0.0:
                h.qty_by_bin[k_lo + idx] = run
        if not h.qty_by_bin:
            self.windows_refused += 1
            return NotReady(reason=f"{self.symbol}: в окне {from_ms}..{to_ms} нет объёма")
        self.windows_built += 1
        if cache_key is not None:
            _WINDOW_CACHE[cache_key] = h
            while len(_WINDOW_CACHE) > _WINDOW_CACHE_MAX:
                _WINDOW_CACHE.popitem(last=False)
        return h
