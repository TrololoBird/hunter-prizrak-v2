# Транспорт ccxt в шестнадцати проектах жанра «торговый бот»

**Дата:** 2026-08-05. **Что это:** исследование транспорта у проектов жанра «торговый бот».
Сигнальный жанр разобран отдельно в
[`ccxt-signal-projects-2026-08-05.md`](ccxt-signal-projects-2026-08-05.md).
Сравнения с настоящим проектом здесь нет — см. последний абзац.

**Метод.** Список собран из каталога awesome-ccxt (признак включения — «использует ccxt»,
к способу получения свечей отношения не имеет) плюс крупные боты и репозитории
сигнального жанра. Скачано **95 файлов** транспортного слоя. Файлы отбирались не по имени,
а поиском по коду (`gh api search/code?q=repo:…+ccxt`), после того как отбор по имени пути
у трёх проектов промахнулся мимо транспорта.

**Проверка принадлежности к выборке.** Прежде чем считать проект источником о ccxt,
проверено, что он ccxt действительно использует. Три кандидата отсеяны:

| проект | звёзд | чем оказался |
|---|---:|---|
| jesse-ai/jesse | 8293 | **ccxt в его requirements.txt НЕТ** — свои драйверы и websocket-client |
| CyberPunkMetalHead/Binance-volatility-trading-bot | 3491 | `python-binance==0.7.9`, не ccxt |
| nguyennoapp/tuntunbot | — | манифеста нет, импорт ccxt в скачанном не найден |

Это та же ошибка, что была допущена во внешнем разборе 04.08 (Hummingbot, cryptofeed и
gocryptotrader попали в выборку «про ccxt», не используя ccxt). Здесь она предотвращена
проверкой, а не памятью.

---

## 1. Карточки проектов

Порядок — по содержательности транспортного слоя, а не по звёздам.

### 1.1. freqtrade/freqtrade — Python, самый полный транспорт выборки

* **Свечи:** гибрид. `watchOHLCV` при `exchange_has("watchOHLCV") and _ft_has["ws_enabled"]`,
  иначе `fetchOHLCV`.
* **Свеча закрыта:** преемник **плюс свежесть**, иначе откат на REST:
  `(len(candles) > 1 and candles[-1][0] >= prev_candle_ts) and last_refresh_time >= half_candle`,
  где `half_candle` — середина текущей свечи. При провале — `«Couldn't reuse watch for … falling back to REST api»`.
* **Незакрытая свеча:** `idx = -2 if drop_incomplete and len(ticks) > 1 else -1`.
* **Ошибки:** `except ccxt.DDoSProtection` СТОИТ ОТДЕЛЬНО И ВЫШЕ `except ccxt.BaseError`
  во всех точках; в файле 30 упоминаний класса лимита.
* **`has[...]`:** отдельный метод `exchange_has()`, 57 употреблений, с переопределением
  через `exchange_has_overrides`.
* **Гигиена ccxt:** `self._ccxt_object.ohlcvs.clear()` — чистит кэш свечей ccxt;
  `_unwatch_ohlcv`, `cleanup_expired` — снимает подписки с пар, которых давно не просили.

### 1.2. Haehnchen/crypto-trading-bot — TypeScript, 3510★

* **Свечи:** `instance.watchOHLCVForSymbols(pairs)` — **ОДНА подписка на все пары сразу**.
* **Явно `{ newUpdates: true }`** при создании клиента.
* **Свеча закрыта: НЕ ОПРЕДЕЛЯЕТСЯ ВОВСЕ.** Все пришедшие свечи кладутся в буфер по
  ключу `exchange:symbol:period:time`, и повторное значение той же метки перезаписывает
  прежнее. Формирующаяся свеча просто затирается своей же финальной версией.
* **Ошибки:** пауза **5000 мс** перед повтором с комментарием
  `// Back off before retrying so we don't spin on persistent errors`.
* **Отмена:** счётчик `generation`; при смене поколения цикл выходит и зовёт `instance.close()`.
* **Догон истории:** отдельный `ccxt_candle_prefill_service` с константой
  `// Delay between API calls to avoid rate limits (ms)` — ручная пауза между запросами.

### 1.3. Drakkar-Software/OctoBot-Trading — Python

* **Свечи:** WS, правило преемника; наружу отдаётся **предыдущая** свеча
  (`# OHLCV_CHANNEL only takes closed candles`). Часов не использует вовсе.
* **Заголовки лимита:** единственный проект выборки, который к ним обращается, — но
  только строкой отладочного лога `f"Last response headers: {self.client.last_response_headers} "`
  и сбросом `client.last_response_headers = {}`. **Управляющего решения не принимает.**
* **Ошибки:** `RateLimitExceeded` и `DDoSProtection` ловятся; известен комментарий про
  отказ зацикливаться на `BadRequest`.
* **`has[...]`:** 31 употребление.

### 1.4. CryptoSignal/crypto-signal — Python, 5612★, буквально «сигнальный» репозиторий

* **Свечи:** только REST `fetch_ohlcv(market_pair, timeframe=time_unit, since=start_date)`.
* **Лимит:** `time.sleep(self.exchanges[exchange].rateLimit / 1000)` после каждого
  запроса — **вручную и СВЕРХ** встроенного троттлера ccxt.
* **Повтор:** `@retry(retry=retry_if_exception_type(ccxt.NetworkError), stop=stop_after_attempt(3))`.
* ⚠ **Дефект.** `RateLimitExceeded` наследует `NetworkError`, а `tenacity` отбирает по
  `isinstance` — значит ответ «лимит превышен» **повторяется три раза подряд**. Это ровно
  то, за что Binance банит по IP.
  (Утверждение выведено из семантики `retry_if_exception_type` и проверенной иерархии
  ccxt; код читан, не исполнен.)

### 1.5. Open-Trader/opentrader — TypeScript

* **Свечи:** REST `fetchOHLCV(symbol, timeframe, since)` в `ccxt-candles.provider.ts`,
  далее фильтр по диапазону дат и `normalizeCandle`.
* Проверяет `has[...]` перед использованием методов; при отсутствии `watchTrades`
  откатывается на `fetchTrades`.

### 1.6. enarjord/passivbot — Python

* **Свечи:** только REST, 25 обращений `fetch_ohlcv`; `has[...]` — 6.
* Известен комментарий о намеренном использовании REST для закрытия разрывов после
  переподключения вместо переигрывания большого снимка WS.

### 1.7–1.13. Остальные

| проект | язык | свечи | заметное |
|---|---|---|---|
| superalgos/superalgos | JS | REST, 16 обращений | `has[]` 3 |
| Dave-Vallance/bt-ccxt-store | Python | REST, 15 | мост в backtrader; `has[]` 1 |
| jordantete/grid_trading_bot | Python | REST, 4 | ловит `NetworkError`, `ExchangeError`, `BaseError` раздельно |
| ztomsy/ztom | Python | REST, 4 | набор для бэктеста и управления ордерами |
| OfficialGIGA/crypto-signal-scanner | Python | REST | сигнальный сканер: RSI, EMA, Bollinger, ATR-перцентиль |
| thanhnguyennguyen/trading-indicator | JS | REST | `indicators/ohlcv.js` — свечи только ради индикаторов |
| ix-ai/crypto-exporter | Python | — | экспортёр в Prometheus; ловит `DDoSProtection`; `has[]` 2 |
| wardbradt/peregrine, hzjken/crypto-arbitrage-framework, suenot/profitmaker | Python/JS | тикеры | арбитраж; свечи не нужны |

---

## 2. Сводка по выборке

Из 13 проектов с пригодной выборкой файлов:

| признак | сколько | кто |
|---|---:|---|
| берут свечи **только REST** | **6** | passivbot, superalgos, bt-ccxt-store, ztom, grid_trading_bot, opentrader |
| используют WS-свечи | **3** | freqtrade, OctoBot, Haehnchen |
| используют **батч**-подписку `watchOHLCVForSymbols` | **1** | Haehnchen |
| читают заголовки лимита | **1** (и только в лог) | OctoBot |
| ловят `DDoSProtection`/`RateLimitExceeded` | **4** | freqtrade, OctoBot, crypto-exporter, peregrine |
| проверяют `has[...]` | **6** | freqtrade, OctoBot, opentrader, passivbot, superalgos, bt-ccxt-store |
| проверяют `market["active"]` | **0** | — |

**Свечи вебсокетом не берёт ни один проект СИГНАЛЬНОГО жанра** (crypto-signal,
crypto-signal-scanner, trading-indicator). WS-свечи встречаются только у исполняющих
ботов, которым важна задержка входа.

**Способ определить «свеча закрыта» — три разных ответа на три проекта:**

| проект | правило |
|---|---|
| freqtrade | преемник + свежесть ≥ полсвечи, иначе REST |
| OctoBot | преемник, наружу отдаётся предыдущая |
| Haehnchen | **никак**: перезапись по ключу метки времени |

Правило преемника — самое частое. Сравнения ответа биржи с собственными часами не
делает никто; настенные часы как единственный признак — тоже.

---

## 3. Как перепроверить

```bash
# какие файлы каждого проекта реально импортируют ccxt
gh api "search/code?q=repo:freqtrade/freqtrade+ccxt" --jq '.items[].path'

# правило «свеча закрыта» у freqtrade
gh api repos/freqtrade/freqtrade/contents/freqtrade/exchange/exchange.py   -H "Accept: application/vnd.github.raw" | grep -n "prev_candle_ts\|half_candle\|drop_incomplete"

# отдельная ветка на превышение лимита
gh api repos/freqtrade/freqtrade/contents/freqtrade/exchange/exchange.py   -H "Accept: application/vnd.github.raw" | grep -c "DDoSProtection"

# батч-подписка у Haehnchen
gh api repos/Haehnchen/crypto-trading-bot/contents/src/modules/system/ccxt_candle_watch_service.ts   -H "Accept: application/vnd.github.raw" | grep -n "watchOHLCVForSymbols\|newUpdates"

# иерархия классов ccxt, объясняющая раздел 1.4
uv run python -c "import ccxt; print(isinstance(ccxt.RateLimitExceeded('x'), ccxt.NetworkError))"
```

## 4. Чем это исследование ограничено

1. **Греп считает употребления, а не поведение.** «freqtrade: 30 упоминаний
   `DDoSProtection`» говорит о частоте образца, но не доказывает, что он применён везде,
   где нужен.
2. **Ни один чужой проект не исполнялся.** Все утверждения о поведении выведены из
   чтения кода. Помечено там, где вывод особенно нагружен (crypto-signal, §1.4).
3. **Из каждого проекта прочитаны отдельные файлы**, а не проект целиком. Нулевое
   значение в таблицах означает «в прочитанном не найдено», а не «в проекте нет».
4. **Выборка смещена в сторону зрелых проектов** — отвечает на вопрос «как делают
   аккуратно», а не «как делают вообще».
5. **Версии чужих проектов не зафиксированы** — читались ветки по умолчанию на
   2026-08-05.

---

Сравнение с настоящим проектом в этом документе НАМЕРЕННО отсутствует: оно собрано
в [`ccxt-conformance-2026-08-05.md`](ccxt-conformance-2026-08-05.md), §8, вместе со
сравнением по сигнальной выборке.