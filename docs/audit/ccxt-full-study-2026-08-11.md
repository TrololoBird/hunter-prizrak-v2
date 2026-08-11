# Документация ccxt и ccxt.pro целиком: что она даёт и что из этого мы не берём

**Задача поставлена владельцем 2026-08-11 дословно:** не хвататься за каждое расхождение,
а сначала скачать документацию ccxt и ccxt.pro ПОЛНОСТЬЮ и собрать всё, что касается
ccxt, ccxt.pro, REST, вебсокета, спота, фьючерсов и binance. Код при этом не менять.

Основание для подозрения владелец назвал сам: транспорт, возможно, реализован неверно, не
использует всего, что даёт библиотека, а используемая часть содержит ошибки или выдуманные
решения. Этот файл — сбор материала под такую проверку, а не её вывод.

⚠ **Установка, под которой всё это читалось:** истина — документация. Наш код истиной не
является: он порождён ИИ целиком, и всякое его утверждение о библиотеке — гипотеза.

---

## 1. Что скачано

Прежние заходы брали страницы через выдачу сайта, а она приходит **урезанной** —
руководство обрывается на середине раздела, спецификация binance теряет методы. Поэтому
взят исходник документации целиком, разреженным клоном репозитория ccxt:

```bash
git clone --filter=blob:none --no-checkout --depth 1 https://github.com/ccxt/ccxt.git
git sparse-checkout set wiki examples/py
git checkout
```

**Итого 1396 файлов, 8 687 969 байт.** Состав:

| раздел | файлов | байт | что это |
|---|---|---|---|
| корень вики | 21 | 1 248 128 | руководство, ccxt.pro, спецификация по методам, FAQ, установка, CLI |
| `wiki/exchanges/` | 102 | 3 828 599 | спецификация ЕДИНЫХ методов по каждой бирже |
| `wiki/exchanges-implicit/` | 103 | 1 283 123 | **сырые эндпоинты** по каждой бирже |
| `wiki/examples/` | 847 | 1 614 946 | описания примеров |
| `examples/py/` | 289 | 650 998 | сами примеры на Python |

Крупнейшие файлы: Manual.md 482 897 Б, baseSpec.md 376 204 Б, ccxt.pro.manual.md 124 217 Б,
exchanges/binance.md 183 202 Б.

⚠ **Каталога `exchanges-implicit` я до этого не открывал вовсе**, а именно он перечисляет
сырые эндпоинты. Как и `wiki/examples/` — 847 файлов описаний.

---

## 2. Полный перечень того, что доступно на нашей площадке

### 2.1. Единые методы binance

Всего в спецификации **143 метода**, из них **30 потоковых** (`watch*` и `unWatch*`).

Потоковые целиком:

```
watchTrades            watchTradesForSymbols      unWatchTrades      unWatchTradesForSymbols
watchOHLCV             watchOHLCVForSymbols       unWatchOHLCV       unWatchOHLCVForSymbols
watchOrderBook         watchOrderBookForSymbols   unWatchOrderBook   unWatchOrderBookForSymbols
watchTicker            watchTickers               unWatchTicker      unWatchTickers
watchMarkPrice         watchMarkPrices            unWatchMarkPrice   unWatchMarkPrices
watchBidsAsks          unWatchBidsAsks
watchLiquidations      watchLiquidationsForSymbols
watchMyLiquidations    watchMyLiquidationsForSymbols
watchBalance           watchOrders                watchPositions     watchMyTrades
```

### 2.2. Сырые публичные эндпоинты фьючерсов

Двадцать девять, из файла exchanges-implicit/binance.md:

```
fapiPublicGetAggTrades          fapiPublicGetKlines             fapiPublicGetTime
fapiPublicGetTrades             fapiPublicGetContinuousKlines   fapiPublicGetPing
fapiPublicGetHistoricalTrades   fapiPublicGetIndexPriceKlines   fapiPublicGetExchangeInfo
fapiPublicGetDepth              fapiPublicGetMarkPriceKlines    fapiPublicGetTicker24hr
fapiPublicGetRpiDepth           fapiPublicGetPremiumIndexKlines fapiPublicGetTickerPrice
fapiPublicGetOpenInterest       fapiPublicGetLvtKlines          fapiPublicGetTickerBookTicker
fapiPublicGetFundingRate        fapiPublicGetPremiumIndex       fapiPublicGetAssetIndex
fapiPublicGetFundingInfo        fapiPublicGetIndexInfo          fapiPublicGetConstituents
fapiPublicGetInsuranceBalance   fapiPublicGetSymbolAdlRisk      fapiPublicGetApiTradingStatus
fapiPublicGetTradingSchedule    fapiPublicGetConvertExchangeInfo
```

### 2.3. Что из этого зовёт наш код

**Всего четыре единых метода и один сырой:**

| зовём | где | зачем |
|---|---|---|
| `load_markets` / `reload_markets` | `exchange.open`, `reload_markets` | инструменты, шаг цены |
| `fetch_time` | `fetch_server_ms` | сведение часов |
| `fetch_ohlcv` | `fetch_closed_ohlcv` | бары всех ТФ |
| `fetch_trades` | `fetch_agg_trades_from`, `fetch_agg_trades_window` | добор сделок |
| `watch_trades` | `watch_agg_trades` | поток сделок |
| `fapiPublicGetExchangeInfo` (сырой) | `_read_weight_limit` | лимит веса |

**Отношение: пять из ста сорока трёх.** Само по себе это ничего не доказывает — большая
часть остального относится к торговле, которой система не ведёт. Ниже разобрано то, что
относится К ДЕЛУ.

---

## 3. Функционал, относящийся к делу, которым мы НЕ пользуемся

Ниже — только то, что касается публичных рыночных данных и могло бы менять устройство
транспорта. Каждый пункт — **гипотеза к проверке, а не установленный дефект.**

### 3.1. Подписка на МНОГО символов одним вызовом

**Документация:** `watchTradesForSymbols` — «Similar to `watchTrades` but allows
subscribing to multiple symbols in a single call». То же есть у баров
(`watchOHLCVForSymbols`) и у стакана.

**У нас:** вселенная — 27 символов, и на каждый заведена своя задача наблюдения. Докстрока
`desync_s` прямо описывает следствие: «27 задач сделок, разбуженных ОДНИМ сетевым
событием, переподписываются синхронной волной» — и против этого написан собственный
механизм фазового сдвига по crc32.

⚠ Стоит проверить, не решается ли исходная задача одной подпиской вместо двадцати семи
плюс самодельного разведения фаз.

### 3.2. Снятие подписки

**Документация:** «Even if you stop getting the return value from the `watchX` method, the
stream will keep sending that, which is handled and stored in the background. To stop those
background subscriptions, you should use `unWatch` method».

**У нас:** `unWatchTrades` не зовётся нигде. При остановке задачи наблюдения подписка,
по документации, остаётся жить в фоне до закрытия экземпляра.

### 3.3. Бары из сделок вместо баров от биржи

**Документация, раздел «Notes On Latency», дословно:** «**tickers and OHLCVs are always
slower than orderbooks and trades**». Причина названа там же: OHLCV — данные ВТОРОГО
порядка, биржа считает их из сделок, и на это нужно время, своё у каждой биржи. И вывод:
«the developers of time-critical trading strategies don't have to rely on secondary data
from the exchanges and can calculate the OHLCVs and tickers in the userland».

**ccxt даёт для этого готовый метод** — `build_ohlcvc(trades, timeframe, since, limit)` в
базовом классе.

**У нас:** бары берутся REST-опросом у биржи, причём с отступом `POLL_OFFSET_S = 3.0` от
границы ТФ, и докстрока этого числа сама признаёт: «⚠ ЧИСЛО НЕ ЗАМЕРЕНО», взято по
косвенному свидетельству.

⚠ **При этом сделки мы уже собираем — для профиля объёма.** То есть первичные данные, из
которых бар и считается, у нас есть. Проверить стоит три вещи разом: снимется ли
незамеренный отступ, совпадут ли наши бары с биржевыми, и не окажется ли, что бар и
профиль наконец считаются ИЗ ОДНОГО ИСТОЧНИКА.

### 3.4. Переподключение

**Документация:** «Upon a critical exception, a disconnect or a connection
timeout/failure, the next iteration of the tick function will call the `watch` method that
will trigger a reconnection. This way the library handles disconnections and reconnections
for the user transparently. **CCXT Pro applies the necessary rate-limiting and exponential
backoff reconnection delays.**»

**У нас:** собственный надзиратель `_supervise` с `RESTART_PAUSE_S`, счётчиками смертей и
подъёмов, плюс `desync_s`.

⚠ Проверить, что из этого дублирует библиотеку, а что нужно из-за нашей обёртки-генератора.
Дублирование само по себе не ошибка; ошибкой было бы приписывать себе то, что делает ccxt.

### 3.5. Параметры выборки, которые мы не передаём

* **`params.until`** у `fetch_ohlcv` — верхняя граница окна. Мы её не передаём и режем
  лишнее сами.
* **`params.paginate`** — у баров это `fetch_paginated_call_deterministic`, то есть
  запросы идут ПАРАЛЛЕЛЬНО с предвычисленными границами. У сделок не заявлено
  (`features['swap']['linear']['fetchTrades']` равно `null`), у баров — заявлено.
* **`fetchMarkOHLCV`, `fetchIndexOHLCV`** и `params.price` — маркировочная и индексная
  цена. Курс о них не говорит; отмечены как существующие.
* **`fapiPublicGetContinuousKlines`** — бары непрерывного контракта, без разрыва на
  экспирации. Для бессрочных не критично, но существует.

---

## 4. Расхождения между документацией ccxt и кодом ccxt

Найдено три. Все проверены вызовом или чтением исходника.

| что | документация говорит | код 4.5.71 делает | цена для нас |
|---|---|---|---|
| `newUpdates` | «in the future `newUpdates: true` will be the default» — то есть сейчас ложь | `True` | при `False` объём попал бы в профиль многократно. **Закреплено явно** |
| ключ настройки построителя баров | `options['buildOHLCV']` | читает `options['buildOHLCVC']` | документированный ключ не сработал бы вовсе |
| верхний предел баров | Binance даёт 1500 на фьючерсах, комментарий ccxt это признаёт | `maxLimit = 1000` жёстко | наш `CCXT_EFFECTIVE_LIMIT = 1000` верен |

Плюс одно расхождение у **Binance**, не у ccxt: фраза про `Retry-After` есть только в
спотовой документации, страница USDⓈ-M её не содержит. Наш код ссылался на фьючерсную —
исправлено, разбор в [`docs/audit/ccxt-manual-2026-08-11.md`](ccxt-manual-2026-08-11.md).

---

## 5. Числа нашего транспорта: у чего есть референт, а у чего нет

Сведено из докстрок, без правок.

| число | референт | статус |
|---|---|---|
| `KLINES_MAX_LIMIT = 1500` | Binance, Kline Data | подтверждено |
| `CCXT_EFFECTIVE_LIMIT = 1000` | исходник ccxt | подтверждено |
| `TRADE_RECOVER_PAGE = 1000` | спецификация binance | подтверждено |
| `REST_TRADES_WINDOW_MS = 3600000` | исходник ccxt | подтверждено |
| `RATE_LIMIT_BACKOFF_S = 60.0` | окно веса `REQUEST_WEIGHT/MINUTE/1` | подтверждено вызовом |
| `POLL_OFFSET_S = 3.0` | **нет замера**, косвенное свидетельство | ⚠ по докстроке — задание Ж-1 |
| `CATCHUP_MAX_BARS = 100` | замер ступени веса | по докстроке замерено |
| `BACKFILL_DAY_MAX_PAGES = 10 000` | **нет замера**, предохранитель | ⚠ названо в докстроке |
| `REST_CHECKPOINT_TRADES = 200 000` | **нет замера** | ⚠ названо в докстроке |
| `WS_TRADES_SILENCE_S = 300.0` | требует проверки | не сверялось в этом заходе |
| `tradesLimit` (не задан) | умолчание ccxt 1000 | оставлено осознанно |

---

## 6. Что осталось непрочитанным

Говорю точно, потому что «изучил полностью» я уже один раз сказал напрасно.

1. **Прочитаны целиком:** разделы руководства про ограничитель, пагинацию, публичные
   сделки, OHLCV и задержки, загрузку рынков; разделы ccxt.pro про специфику потоков,
   инкрементальные структуры, `newUpdates`, режимы реального времени и троттлинга,
   обработку ошибок, уборку ресурсов, `watchTradesForSymbols`; спецификация binance по
   методам, которые мы зовём; перечни всех методов и сырых эндпоинтов.
2. **Перечислено, но не прочитано построчно:** baseSpec.md (376 КБ) — читан выборочно;
   `wiki/examples/` (847 файлов) — только оглавление; `examples/py/` (289 файлов) —
   прочитано пять.
3. **Не открывалось:** спецификации остальных 101 биржи, приватная часть API, разделы про
   предсказательные рынки, CLI, установку.
4. **Не проверено на живой бирже:** всё из §3 — это гипотезы по документации. Ни одна не
   замерена, и ни одна не должна попасть в код без замера и диффа повтора.
