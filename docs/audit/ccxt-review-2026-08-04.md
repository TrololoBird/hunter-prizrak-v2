# ccxt: документация, исходник 4.5.70 и практика схожих проектов — против `hunter/exchange.py`

**Дата:** 2026-08-04. Третий документ серии; предыдущие — `critical-review-2026-08-04.md` и `critical-review-verified-2026-08-04.md`.

**Что изучено:**

* официальный CCXT Pro Manual (`docs.ccxt.com/docs/pro-manual`) и справочник по `watchOHLCV`;
* **исходник установленной версии `ccxt==4.5.70`** — той самой, что закреплена в `pyproject.toml`;
* открытые и закрытые issue в репозитории ccxt по интересующим механизмам;
* **freqtrade** — самый используемый открытый торговый бот на Python поверх ccxt: реализация freqtrade/exchange/exchange_ws.py (297 строк) и путь принятия решения в `exchange.py`.

**Почему исходник, а не только документация.** Первое же сопоставление показало, что документация ccxt отстаёт от кода по ключевому для проекта параметру (раздел 1). §10.1 FOUNDATION говорит: «Документация ccxt — внешний референт, самописный вебсокет-клиент — нет». Это верно как принцип, но **референтом здесь работает исходник**, а не сайт документации, и это надо зафиксировать явно.

---

## 1. Где документация ccxt расходится с кодом 4.5.70

| что | документация (pro-manual) | код 4.5.70 | следствие для проекта |
|---|---|---|---|
| `newUpdates` | «**you should instantiate the exchange with the newUpdates flag set to true**… *Deprecation Warning*: in the future `newUpdates: true` will be the default» | `newUpdates = True` — атрибут класса, `ccxt/async_support/base/exchange.py:68`. Проверено: `ccxt.pro.binanceusdm().newUpdates → True` | «будущее» уже наступило. `watch_ohlcv` **не** отдаёт весь кэш, а отдаёт только новые обновления. Комментариев об этом в `exchange.py` проекта нет — поведение принято на веру и оказалось верным случайно |
| закрытие свечи | **не упоминается вовсе** (проверено запросом к тексту руководства) | `handle_ohlcv` разбирает `k` в шесть чисел, поле `k.x` не сохраняет | §6 «незакрытая свеча отбрасывается» реализуема только обходным путём; какой именно — ccxt не советует |
| rate limit на WS-подписки | **не описан** | `Throttler` (leaky bucket) + `options['ws']['cost'] = 5`, `tokenBucket.refillRate = 0.02` | замер в разделе 2.4 |
| переподключение | «Upon a critical exception, a disconnect or a connection timeout/failure, the next iteration of the tick function will call the `watch` method that will **trigger a reconnection**. This way the library handles disconnections and reconnections for the user **transparently**» | подтверждается: `Client.ping_loop` c `keepAlive = 5000` мс и `maxPingPongMisses = 2.0` поднимает `RequestTimeout`; `Client.future()` пересоздаёт отменённое будущее | цикл «поймал исключение → позвал `watch` снова» **и есть документированный паттерн**. Проект делает правильно, но называет это неправильно (раздел 4, C-10) |

**Вывод для §0.** Правило «внешним референтом является документация ccxt» на этой версии даёт неверный ответ минимум по одному пункту. Формулировку стоит уточнить: **референт — исходник закреплённой версии; документация — вторичный источник, который может отставать.** Это ровно та же поправка, что проект уже сделал для `pandas-ta` («оракул ОБЯЗАН звать pandas-ta с talib=False, иначе он сверяет TA-Lib сам с собой»).

---

## 2. Что ccxt гарантирует и чего не гарантирует (по исходнику)

### 2.1. Признак «свеча закрыта» ccxt не отдаёт, и это известная незакрытая заявка

ccxt/pro/binance.py::handle_ohlcv:

```python
parsed = [
    self.safe_integer(kline, 't'),   # open time
    self.safe_float(kline, 'o'),
    self.safe_float(kline, 'h'),
    self.safe_float(kline, 'l'),
    self.safe_float(kline, 'c'),
    self.safe_float(kline, 'v'),
]
```

Поле `k.x` (`"x": False` в комментарии-примере прямо над этим кодом) в унифицированный формат не попадает.

Заявка **[ccxt#21885 «Add a `candle_closed_only` parameter or return boolean `closed` parameter with the candle data from websockets»](https://github.com/ccxt/ccxt/issues/21885)** (открыта 23.03.2024) **до сих пор открыта и не реализована**. В ней же зафиксировано, что флаг есть в сыром потоке у Binance, Coinbase и Bybit и отсутствует у Kraken и KuCoin — то есть унифицировать его ccxt пока не готов.

**Что это значит для проекта.** Решение `exchange.py` определять закрытость по часам — не самодеятельность, а вынужденный обход известного пробела библиотеки. Но докстрока (`exchange.py:219–226`) подаёт его как выбор в пользу лучшего варианта («признак закрытия — биржевое время дошло до правой границы, **а не** отбросить последний элемент кэша»). Правильная формулировка: **ccxt не даёт признака закрытия; из двух доступных обходных путей выбран второй, и у него есть известная гонка.**

### 2.2. `ArrayCacheByTimestamp`: что именно возвращает `watch_ohlcv`

ccxt/async_support/base/ws/cache.py:

```python
def getLimit(self, symbol, limit):
    self._clear_updates = True
    if limit is None:
        return self._new_updates
    return min(self._new_updates, limit)

def append(self, item):
    if item[0] in self.hashmap:
        reference = self.hashmap[item[0]]
        if reference != item:
            reference[0:len(item)] = item      # ОБНОВЛЕНИЕ НА МЕСТЕ
    else:
        ...
    if self._clear_updates:
        self._clear_updates = False
        self._size_tracker.clear()
    self._size_tracker.add(item[0])
    self._new_updates = len(self._size_tracker)
```

Три следствия, важные для `watch_closed_ohlcv`:

1. **`_new_updates` — это число РАЗЛИЧНЫХ меток времени, тронутых с прошлого чтения.** `watch_ohlcv` отдаёт последние `_new_updates` свечей. Значит, если потребитель молчал три минуты на 1м, он получит все накопившиеся свечи, а не одну. **Бары кэшем не теряются** — пока разрыв меньше `OHLCVLimit = 1000` свечей.
2. Отсюда же: **таймаут в `_watch_step` баров не теряет.** `getLimit()` при отмене `wait_for` не вызывается, счётчик продолжает копиться, и следующий успешный `watch_ohlcv` отдаст всё. Это лучше, чем я предполагал в первом отчёте.
3. **Обновление идёт «на месте»** — в деке лежат ссылки на те же списки. `hunter.exchange` сразу копирует значения в `Bar(...)`, поэтому проблемы алиасинга нет. Для сравнения: freqtrade делает `deepcopy` кэша именно из-за этого (`exchange_ws.py:117`).

### 2.3. Переподключение делает библиотека, а не пользователь

`Client.ping_loop`:

```python
while self.keepAlive and not self.closed():
    await sleep(self.keepAlive / 1000)          # keepAlive = 5000 мс
    ...
    if (self.lastPong + self.keepAlive * self.maxPingPongMisses) < now:   # maxPingPongMisses = 2.0
        self.on_error(RequestTimeout('... timed out due to a ping-pong keepalive missing on time'))
```

`RequestTimeout → NetworkError → OperationFailed → BaseError`. То есть мёртвый сокет ccxt обнаруживает **за ~10 секунд** сам и роняет ожидание исключением, которое пользователь ловит и снова зовёт `watch`.

Отдельно проверено: отмена `asyncio.wait_for` **не отравляет** будущее ccxt —

```python
def future(self, message_hash):
    if message_hash not in self.futures or self.futures[message_hash].cancelled():
        self.futures[message_hash] = Future()
```

Это типовая ошибка в самописных обёртках; здесь её нет.

### 2.4. Троттлинг подписок: 4 подписки в секунду — ЗАМЕРЕНО

`Exchange.client()` создаёт `Throttler(self.tokenBucket, ...)` на каждое WS-соединение. Для `binanceusdm`:

```
tokenBucket = {'delay': 0.001, 'capacity': 1, 'cost': 1,
               'refillRate': 0.02, 'algorithm': 'leakyBucket', 'rateLimit': 50}
options['ws'] = {'cost': 5}
```

Прямой замер настоящим `Throttler` из 4.5.70, без сети:

```
  27 подписок (cost=5) ->   6.50 с  (4.2 подписок/с)
 100 подписок (cost=5) ->  24.75 с  (4.0 подписок/с)
 189 подписок (cost=5) ->  47.00 с  (4.0 подписок/с)
```

Вселенная проекта — 27 символов × 6 ТФ = 162 потока баров плюс 27 потоков сделок = **189 подписок ⇒ 47 секунд разгона**.

### 2.5. Пакетная подписка одним кадром — `watchOHLCVForSymbols`

`binanceusdm` в 4.5.70 умеет (проверено по `exchange.has`):

```
watchOHLCV  watchOHLCVForSymbols  watchTrades  watchTradesForSymbols
unWatchOHLCV  unWatchOHLCVForSymbols  unWatchTrades  unWatchTradesForSymbols
```

`watch_ohlcv(symbol, tf)` внутри вызывает `watch_ohlcv_for_symbols([[symbol, tf]])`, то есть **один кадр `SUBSCRIBE` на каждую пару**. А `watch_ohlcv_for_symbols` с полным списком собирает **один** кадр на всё:

```python
for ...:
    rawHashes.append(marketId + '@' + klineType + '_' + interval + utcSuffix)
    messageHashes.append('ohlcv::' + market['symbol'] + '::' + timeframeString)
request = {'method': 'SUBSCRIBE', 'params': rawHashes, 'id': requestId}
res = await self.watch_multiple(url, messageHashes, self.extend(request, params), messageHashes, subscribe)
...
return self.create_ohlcv_object(symbol, timeframe, filtered)   # {symbol: {timeframe: candles}}
```

То есть 162 кадра и 47 секунд превращаются в **один кадр и порядка четверти секунды**.

### 2.6. Лимит подписок на поток — и дыра в его учёте

```python
options['streamLimits']             = {'spot': 50, 'margin': 50, 'future': 50, 'delivery': 50}
options['subscriptionLimitByStream'] = {'spot': 200, 'margin': 200, 'future': 200, 'delivery': 200}
```

`binance.stream()` при превышении бросает `BadRequest('reached the limit of subscriptions by stream')`.

⚠ Но **для OHLCV этот учёт не работает**: `watch_ohlcv_for_symbols` зовёт `self.stream(type, 'multipleOHLCV')` с константным `subscriptionHash` и `numSubscriptions=1` по умолчанию. Первый вызов записывает «1 подписка», все последующие находят готовый поток и счётчик не трогают. `watch_trades_for_symbols`, наоборот, передаёт `subParamsLength` — там учёт настоящий. То есть **все 162 kline-подписки живут на одном соединении, а ccxt считает их за одну.** Документированный биржей лимит — 1024 стрима на соединение (он записан и в FOUNDATION §6), так что запаса пока хватает, но защиты со стороны ccxt по этому пути нет.

### 2.7. Известные отказы на большом числе подписок

[ccxt#23929](https://github.com/ccxt/ccxt/issues/23929): **416 подписок `watchOHLCV`** на Bybit → `«timed out due to a ping-pong keepalive missing on time»` → повторная подписка → `«throttle queue is over maxCapacity (1000)»` → по словам автора, утечка памяти и падение процесса. Заявка закрыта как *not planned*.

Это не приговор ccxt, но это **измеренный порядок величины, при котором связка «много подписок + агрессивный retry» ломается**, и он всего вдвое выше нынешних 189 проекта. Сопутствующие заявки того же класса: [#10786](https://github.com/ccxt/ccxt/issues/10786) (утечки при самодельном пересоздании WS), [#25451](https://github.com/ccxt/ccxt/issues/25451), [#20946](https://github.com/ccxt/ccxt/issues/20946) (зависание сокета на `watch_ohlcv`), [#15266](https://github.com/ccxt/ccxt/issues/15266) (`watch_ohlcv` возвращается за 1.6–5.9 с там, где ожидалось мгновенно).

---

## 3. Практика: как это устроено во freqtrade

freqtrade/exchange/exchange_ws.py — эталонная точка сравнения: тот же ccxt.pro, тот же Python, годы боевой эксплуатации, включено по умолчанию (`exchange.enable_ws` defaults to true).

### 3.1. WS живёт в ОТДЕЛЬНОМ ПОТОКЕ со своим event loop

```python
self._thread = Thread(name="ccxt_ws", target=self._start_forever)
self._thread.start()

def _start_forever(self) -> None:
    self._loop = asyncio.new_event_loop()
    self._loop_ready.set()
    self._loop.run_forever()
```

Смысл прямой: обработка данных в основном потоке **не может задержать приём вебсокета**. Это то самое, чего нет в `hunter.run.collect` — там 189 потоков и поштучная обработка каждой сделки (`hist.add` + `binned.add`) делят один event loop.

### 3.2. `watch_ohlcv` используется как ТРИГГЕР, а не как источник данных

```python
async def _continuously_async_watch_ohlcv(self, pair, timeframe, candle_type) -> None:
    while True:
        ...
        data = await self._ccxt_object.watch_ohlcv(pair, timeframe)   # результат не используется
        self._klines_last_refresh[(pair, timeframe, candle_type)] = dt_ts()
```

Данные потом читаются из кэша ccxt напрямую и **копируются глубоко**:

```python
def ohlcvs(self, pair, timeframe) -> list[list]:
    return deepcopy(self._ccxt_object.ohlcvs.get(pair, {}).get(timeframe, []))
```

### 3.3. Критерий «свечу можно брать» — преемник плюс свежесть, иначе REST

Ключевой фрагмент, freqtrade/exchange/exchange.py, функция `_try_build_from_websocket`
(файл ЧУЖОГО проекта; в этом дереве `src/hunter/exchange.py` — другой файл с тем же
именем, и путать их нельзя):

```python
candle_ts      = dt_ts(timeframe_to_prev_date(timeframe))   # открытие ТЕКУЩЕЙ (формирующейся)
prev_candle_ts = dt_ts(date_minus_candles(timeframe, 1))    # открытие ПРЕДЫДУЩЕЙ
candles, last_refresh_time = self._exchange_ws.get_ohlcv_with_refresh(...)
half_candle = int(candle_ts - (candle_ts - prev_candle_ts) * 0.5)

if (candles
        and ((len(candles) > 1 and candles[-1][0] >= prev_candle_ts)
             # Edgecase on reconnect, where 1 candle is available but it's the current one
             or (len(candles) == 1 and candles[-1][0] < candle_ts))
        and last_refresh_time >= half_candle):
    return self._exchange_ws.get_ohlcv(pair, timeframe, candle_type, candle_ts)
logger.info(f"Couldn't reuse watch for {pair}, {timeframe}, falling back to REST api. ...")
return None
```

Три правила, и все три — то, чего нет в `hunter/exchange.py`:

1. **Преемник обязателен.** Свеча считается пригодной, только если в кэше есть более новая. Часы для этого не нужны вовсе.
2. **Свежесть измеряется В ДОЛЯХ ТАЙМФРЕЙМА** (`last_refresh_time >= half_candle`), а не фиксированными 60 секундами.
3. **Не выполнилось — откат на REST**, а не «пропустим этот бар».

А окончательный отбор незакрытой свечи делается снятием последнего элемента:

```python
idx = -2 if drop_incomplete and len(ticks) > 1 else -1
```

⚠ Это ровно тот `cache[:-1]`, который FOUNDATION §6 называет неверным решением прошлой реализации. Возражение проекта («последний элемент бывает и закрытым, тогда отбрасывание подало бы позапрошлый бар») **справедливо для потоковой архитектуры hunter и несправедливо для снимочной архитектуры freqtrade**: там кэш перечитывается целиком на каждом цикле обновления, поэтому отбросить последний элемент ничего не теряет. То есть спор не о том, какое правило верно, а о том, какая архитектура выбрана.

### 3.4. Периодическое пересоздание соединений

```python
def reset_connections(self, cleanup: bool = False) -> None:
    """
    Reset all connections - avoids "connection-reset" errors that happen after ~9 days
    """
    ...
async def _cleanup_async(self) -> None:
    await self._ccxt_object.close()
    self._ccxt_object.ohlcvs.clear()   # Clear the cache.
```

В обсуждении PR [#10273](https://github.com/freqtrade/freqtrade/pull/10273) это сформулировано так: *«after ~8-9 days, I've observed the connection becoming unstable»*, поэтому *«the websocket connections will currently reset once a day»*.

Для системы, которая по цели владельца обязана работать 24/7, это самая существенная строчка всей практики.

### 3.5. Снятие подписки и чистка кэша

```python
def cleanup_expired(self) -> None:
    # Remove pairs from watchlist if they've not been requested within the last timeframe (+ offset)
    if last_refresh > 0 and (dt_ts() - last_refresh) > ((timeframe_s + 20) * 1000):
        self._klines_watching.discard(p)
        self._pop_history(p)          # Pop history to avoid getting stale data
```

и явное `un_watch_ohlcv_for_symbols`, если биржа его поддерживает. `binanceusdm` поддерживает.

### 3.6. Расхождение часов трактуется как СИМПТОМ

```python
if refresh_date and received_ts > refresh_date:
    logger.warning(f"{pair}, {timeframe} - Candle date > last refresh ... "
                   "This usually suggests a problem with time synchronization.")
```

freqtrade сверяет метку биржи с **локальным** временем и при расхождении предупреждает. hunter вместо этого сводит часы с биржей (§6) — подход строже и лучше, **но у него нет ни периодической пересинхронизации, ни монотонного якоря** (Д-5 первого отчёта). Практика подтверждает, что вопрос реальный: у freqtrade он вылезает достаточно часто, чтобы под него завели отдельное предупреждение.

### 3.7. Обработка ошибок

```python
except ccxt.ExchangeClosedByUser:
    logger.debug("Exchange connection closed by user")
except ccxt.BaseError:
    logger.exception(f"Exception in continuously_async_watch_ohlcv for {pair}, {timeframe}")
```

Ловится **`ccxt.BaseError` целиком**, а штатное закрытие отделено в свою ветку.

---

## 4. Расхождения `hunter/exchange.py` с документацией и практикой

### C-1. Разгон подписок съедает половину окна наблюдения

**Статус: ЗАМЕРЕНО. Тяжесть: высокая (искажает все живые замеры проекта).**

189 подписок × 4/с = **47.00 с**. Умолчание `hunter run --seconds 90` даёт 90-секундное окно, из которого 47 с уходит на подписку. Последние символы вселенной наблюдаются вдвое меньше первых, и в приёмке это никак не отражено: `report.histograms` покажет у них меньше сделок, и это будет прочитано как «тонкий символ», а не как «поздно подписались».

Строка приёмки «БЕЗ ЕДИНОЙ СДЕЛКИ за прогон» (`run.py:495`) при 90-секундном окне для символов из конца списка означает «наблюдали 43 секунды», а не «сделок не было».

### C-2. Не используется пакетная подписка

**Статус: подтверждено чтением исходника ccxt. Тяжесть: высокая (снимает C-1 целиком).**

`exchange.watch_closed_ohlcv` вызывает `self._ex.watch_ohlcv(symbol, timeframe)` — по подписке на пару. `watch_ohlcv_for_symbols` со списком из 162 пар отправляет один кадр. Это **не оптимизация ради скорости**, а устранение источника систематического перекоса из C-1.

### C-3. Нет отката на REST

**Статус: расхождение с практикой. Тяжесть: высокая для 24/7.**

freqtrade при невыполнении условий пригодности WS-данных берёт свечу через `fetch_ohlcv`. hunter при таймауте/сбое просто ждёт снова; пропущенное за время обрыва не добирается никогда (Д-6 первого отчёта). Между тем `fetch_closed_ohlcv` в проекте уже есть и умеет `since_ms` — механизм готов, не подключён.

### C-4. Нет периодического пересоздания соединений

**Статус: расхождение с практикой, подтверждённое чужим замером. Тяжесть: высокая для 24/7.**

freqtrade сбрасывает соединения раз в сутки, потому что после ~9 суток они деградируют. У hunter соединение живёт столько, сколько живёт процесс. В нынешней пакетной модели (прогон 90–400 с) это неважно; при переходе к 24/7 — первое, что сломается.

### C-5. Порог тишины фиксированный, а не производный от таймфрейма

**Статус: расхождение с практикой. Тяжесть: средняя.**

`WS_SILENCE_S = 60` — одно число на 5м и на 1Н. У freqtrade порог свежести — «половина текущей свечи», то есть 150 с для 5м и 2.5 суток для 1Н. Замер проекта (`watch_ohlcv` отвечает med 0.40 с) верен, но он мерит частоту обновлений форминг-бара, а не то, через сколько отсутствие обновления означает поломку.

### C-6. Два класса ошибок ccxt не ловятся

**Статус: ПОДТВЕРЖДЕНО (проверка иерархии классов). Тяжесть: средняя.**

`_watch_step` ловит `TimeoutError`, `ccxt.NetworkError`, `ccxt.ExchangeError`. Проверка полного дерева ошибок 4.5.70:

| класс | цепочка наследования | ловится `_watch_step` |
|---|---|---|
| `RequestTimeout`, `DDoSProtection`, `ExchangeNotAvailable`, `RateLimitExceeded`, `InvalidNonce` | → `NetworkError` → `OperationFailed` → `BaseError` | да |
| `BadRequest`, `NotSupported`, `AuthenticationError`, `ExchangeClosedByUser` | → `ExchangeError` → `BaseError` | да |
| **`OperationFailed`** | → `BaseError` | **НЕТ** |
| **`UnsubscribeError`** | → `BaseError` | **НЕТ** |

`OperationFailed` — родитель `NetworkError`; ccxt может поднять его напрямую или добавить под него нового потомка мимо `NetworkError`. `UnsubscribeError` возникает на пути `unWatch*`. Любой из них завершает асинхронный генератор навсегда — то есть возвращает ровно тот дефект, ради которого `_watch_step` и написан («Раньше переподключения не было ВООБЩЕ… первое же сетевое исключение завершало его навсегда»).

freqtrade ловит `ccxt.BaseError` целиком. Это правильный уровень.

⚠ Отдельно: `BadRequest` («reached the limit of subscriptions by stream») **ловится**, но обрабатывается как временный сбой — лог, пауза 1 с, повтор. Это отказ, который повтором не лечится: получится вечный цикл ошибок и символ без данных.

### C-7. WS и обработка сделок делят один event loop

**Статус: расхождение с практикой. Тяжесть: средняя-высокая; усиливает Д-2.**

`run.collect` создаёт 189 задач в том же цикле, где `_watch_trades_impl` для каждой сделки делает `hist.add(...)` и `binned.add(...)` — два словарных обновления и `Decimal`-деление на сделку. На BTC это тысячи вызовов в секунду. Любая такая пачка задерживает пробуждение корутины баров, а именно от момента пробуждения зависит вердикт «бар закрыт» (`now = clock.now_ms()` после `await`). То есть **нагрузка на цикл напрямую повышает вероятность отдать бар с непоследними значениями**, и заметить это нечем (Д-1).

freqtrade выносит весь WS в отдельный поток именно поэтому.

### C-8. Кэш ccxt не чистится

**Статус: расхождение с практикой. Тяжесть: низкая сейчас, средняя при 24/7.**

`self._ex.ohlcvs` растёт до `OHLCVLimit = 1000` на каждую пару symbol×timeframe — 162 пары × 1000 свечей. При пересоздании соединения (которого сейчас нет) старый кэш надо чистить, иначе после переподключения в него подмешиваются устаревшие свечи. freqtrade делает `self._ccxt_object.ohlcvs.clear()` в `_cleanup_async` и `_pop_history` при снятии подписки, с комментарием «Pop history to avoid getting stale data».

### C-9. Запас до жёсткого лимита ccxt — 11 подписок

**Статус: ПОДТВЕРЖДЕНО (чтение исходника). Тяжесть: средняя.**

`subscriptionLimitByStream['future'] = 200`. Сейчас 189. Добавление одного таймфрейма (+27) или семи символов (+42) упрётся в `BadRequest`. Учёт при этом ведётся неверно (раздел 2.6): kline-подписки считаются за одну, поэтому предохранитель сработает не там, где ожидается, — на потоке сделок.

### C-10. «Переподключение» — имя, не действие (подтверждено документацией)

Документация: *«the next iteration of the tick function will call the `watch` method that will trigger a reconnection… the library handles disconnections and reconnections for the user transparently»*.

То есть цикл `_watch_step` **делает ровно то, что предписано**, и это надо записать в его пользу. Неверны только две вещи: слово «переподключение» в логе (переподключается ccxt, и по своему ping/pong, а не по 60-секундному таймауту) и счётчик `ws_reconnects`, который в отчёте владельцу считает **таймауты ожидания**, а не переподключения.

### C-11. Что в `hunter/exchange.py` сделано ЛУЧШЕ типовой практики

Перечисляю, чтобы при переделке не потерять:

* **Курсор `emitted` живёт вне попытки** и переживает переподключение — типовые обёртки этого не делают и после обрыва повторно отдают уже отданные бары.
* **Проверка сетки (`on_grid`)** на каждом баре — ни freqtrade, ни примеры ccxt так не делают.
* **Отбраковка битого бара по определению свечи** (`Bar._ohlc_consistent`) на границе транспорта — правильный уровень; в практике это обычно не проверяется вовсе, и один битый бар портит индикаторы молча.
* **Явный перевод потока сделок на `aggTrade`** через `options.watchTrades.name` — умолчание ccxt `trade`, и §5 требует именно `aggTrade`. Проверено: `options['watchTrades'] = {'name': 'trade'}` по умолчанию, то есть переключение необходимо и сделано верно.
* **Отдельный порог тишины для событийного потока сделок** — рассуждение о разной природе потоков верное, вопрос только к числу (C-5).
* **Часы, сведённые с биржей** — строже, чем локальные часы freqtrade.

---

## 5. Что это меняет в прежних выводах

| прежний вывод | что показало изучение ccxt |
|---|---|
| **Д-2** (бар может быть отдан с непоследними значениями) | подтверждается механизмом и остаётся; но **бары при таймауте не теряются** — `_new_updates` копится, пока `getLimit` не вызван. Формулировку «окончательное обновление отбрасывается курсором `emitted` навсегда» надо сузить: теряется только та версия свечи, что пришла после эмиссии, а не сама свеча |
| **Д-7** («переподключение» — не переподключение) | подтверждено документацией; **но действие правильное** — это документированный паттерн. Претензия сужается до имени и счётчика |
| **Д-11** (разгон подписок ~47 с) | из гипотезы стало замером: **47.00 с ровно**, 4.0 подписки/с |
| **Д-9** (порог тишины сделок) | усиливается: практика привязывает свежесть к длительности таймфрейма, а не к абсолютному числу |
| §10.1 «документация ccxt — внешний референт» | требует уточнения: документация отстаёт от кода 4.5.70 как минимум по `newUpdates` |

---

## 6. Конкретные правки

### 6.1. Одна пакетная подписка вместо 162

```python
async def watch_all_closed_ohlcv(
    self, pairs: list[tuple[str, str]]
) -> AsyncGenerator[tuple[str, str, Bar]]:
    """Один SUBSCRIBE на всю вселенную вместо одного на пару.

    ЗАМЕР: 189 отдельных подписок = 47.00 с разгона (Throttler ccxt 4.5.70,
    options['ws']['cost']=5, tokenBucket.refillRate=0.02). Пакетная — один кадр.
    """
    emitted: dict[tuple[str, str], int] = {}
    while True:
        got = await self._watch_step(
            "бары", "пакет",
            lambda: self._ex.watch_ohlcv_for_symbols([[s, tf] for s, tf in pairs]),
            WS_SILENCE_S)
        if got is None:
            continue
        now = clock.now_ms()
        for symbol, by_tf in got.items():          # {symbol: {timeframe: candles}}
            for timeframe, raw in by_tf.items():
                key = (symbol, timeframe)
                for r in raw:
                    open_ms = int(r[0])
                    if open_ms <= emitted.get(key, -1):
                        continue
                    if now < open_ms + tf_ms(timeframe):
                        continue
                    emitted[key] = open_ms
                    yield symbol, timeframe, Bar(open_ms=open_ms, open=float(r[1]),
                                                 high=float(r[2]), low=float(r[3]),
                                                 close=float(r[4]), volume=float(r[5]))
```

### 6.2. Признак закрытия — преемник, а часы — только страховка

Устраняет гонку Д-2 и делает счётчик Д-1 способным сработать.

```python
def confirmed_closed(raw: list[list], timeframe: str, now_ms: int) -> list[list]:
    """Свеча закрыта, если в кэше есть БОЛЕЕ НОВАЯ (её преемник).

    ccxt не отдаёт биржевой флаг `k.x` (handle_ohlcv его не сохраняет; заявка
    ccxt#21885 открыта с 2024-03). Поэтому признак строится из данных, а не из часов:
    появление свечи N+1 означает, что N биржей закрыта, независимо от точности часов.
    Часы остаются ВТОРЫМ условием — на случай, когда преемник ещё не пришёл, а
    таймфрейм давно истёк (тонкий символ, обрыв).
    """
    if not raw:
        return []
    newest = max(int(r[0]) for r in raw)
    step = tf_ms(timeframe)
    return [r for r in raw
            if int(r[0]) < newest                        # преемник существует
            or now_ms >= int(r[0]) + step + step // 2]   # либо прошло 1.5 ТФ
```

После этой правки проверка в `run._watch_bars_impl` перестаёт быть тавтологией: эмиссия идёт по преемнику, проверка — по часам, это **разные приборы**, и расхождение между ними станет измеримым числом.

### 6.3. Ловить `BaseError`, отделив штатное закрытие и неповторимые отказы

```python
except ccxt.ExchangeClosedByUser:
    raise                                   # штатная остановка, не сбой
except ccxt.BadRequest as e:                # лимит подписок и т.п. — повтором не лечится
    self.ws_errors[key] += 1
    log.error(f"{what}: отказ, который повтором не лечится", символ=key, причина=str(e))
    raise
except ccxt.NetworkError as e:
    ...
except ccxt.BaseError as e:                 # было ExchangeError — мимо проходили
    self.ws_errors[key] += 1                # OperationFailed и UnsubscribeError
    log.error(f"{what}: ошибка биржи", символ=key, причина=f"{type(e).__name__} {e}")
```

### 6.4. Порог тишины — от таймфрейма

```python
def silence_threshold_ms(timeframe: str) -> int:
    """Практика freqtrade: свежесть меряется в долях свечи, а не абсолютным числом.

    Замер проекта (med 0.40 с) описывает частоту обновлений форминг-бара, а не срок,
    после которого молчание означает поломку.
    """
    return max(tf_ms(timeframe) // 2, 60_000)
```

### 6.5. Откат на REST и суточный сброс соединений — для режима 24/7

```python
# после каждого восстановления связи и раз в сутки:
#   1) закрыть соединение и очистить кэш ccxt
#      await self._ex.close(); self._ex.ohlcvs.clear()
#      (freqtrade: "avoids connection-reset errors that happen after ~9 days")
#   2) добрать пропущенное REST-ом: fetch_closed_ohlcv(sym, tf, since_ms=last_seen+1)
#      (freqtrade: "Couldn't reuse watch ... falling back to REST api")
```

### 6.6. Вынести приём WS из общего цикла

Минимальный вариант без отдельного потока: не обрабатывать сделки поштучно в корутине, а складывать пачки в очередь и разбирать их отдельной задачей с `await asyncio.sleep(0)` между пачками. Полный вариант — отдельный поток со своим event loop, как у freqtrade.

---

## 7. Что осталось непроверенным

1. **Ничего из этого не проверено на живой бирже** — `fapi.binance.com` отвечает `HTTP 451` из этого окружения. Проверены: исходник ccxt (исполнением), троттлер (исполнением, 47.00 с), иерархия ошибок (исполнением), возможности `has` (исполнением). Поведение `watch_ohlcv_for_symbols` на реальном соединении, фактическая частота гонки Д-2 и работа отката на REST требуют прогона на машине владельца.
2. **Практика взята из одного проекта.** freqtrade — самый крупный и подходящий образец (Python + ccxt.pro + торговля), но это **один** источник, а §0.1 требует трёх независимых. Другие крупные системы (Hummingbot, Jesse) собственные WS-клиенты пишут сами и потому сравнением не служат; OctoBot использует ccxt, но его реализацию я не читал.
3. **Заявка ccxt#21885 прочитана по странице issue**, комментарии сопровождающих в выдаче отсутствовали — вывод «не реализовано» сделан по статусу *Open* и по отсутствию флага в коде 4.5.70, а не по ответу мейнтейнера.
4. **Числа из ccxt#23929 (416 подписок → отказ)** — сообщение пользователя, не воспроизведённый мной замер. Заявка закрыта как *not planned*, подтверждения от мейнтейнеров в выдаче не было.
5. **Не проверялось**, как `watch_ohlcv_for_symbols` ведёт себя при частичном отказе (одна пара из 162 недоступна) — это существенно, потому что сейчас отказ изолирован на уровне пары, а после пакетной подписки может стать общим.
6. **Не измерялась** фактическая задержка пробуждения корутины баров под нагрузкой 189 задач (C-7). Это измеримо офлайн подстановкой фиктивного потока и стоит замера до переделки.

---

## Источники

* [CCXT Pro Manual](https://docs.ccxt.com/docs/pro-manual)
* [ccxt#21885 — Add a `candle_closed_only` parameter or return boolean `closed` parameter with the candle data from websockets](https://github.com/ccxt/ccxt/issues/21885)
* [ccxt#23929 — «Ping-pong keep alive missing on time» lead to «throttle queue is over maxCapacity» in bybit ccxt pro on watchOHLCV](https://github.com/ccxt/ccxt/issues/23929)
* [ccxt#6631 — watchOHLCV doesn't return latest data after limit reached](https://github.com/ccxt/ccxt/issues/6631)
* [ccxt#15266 — python watchOHLCV unexpectedly slow](https://github.com/ccxt/ccxt/issues/15266)
* [ccxt#10786 — memory leaks on workarounds for reinit websockets](https://github.com/ccxt/ccxt/issues/10786)
* [ccxt#20946 — The socket hangs on Bybit's watch_ohlcv](https://github.com/ccxt/ccxt/issues/20946)
* [freqtrade PR#10273 — ccxt.pro support: using websockets to get data](https://github.com/freqtrade/freqtrade/pull/10273)
* [freqtrade — Configuration: websocket support](https://www.freqtrade.io/en/stable/configuration/)
* [freqtrade/exchange/exchange_ws.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/exchange/exchange_ws.py)
* Исходник `ccxt==4.5.70`: ccxt/pro/binance.py, `ccxt/async_support/base/exchange.py`, ccxt/async_support/base/ws/client.py, ccxt/async_support/base/ws/cache.py, ccxt/async_support/base/ws/future.py, ccxt/async_support/base/throttler.py
