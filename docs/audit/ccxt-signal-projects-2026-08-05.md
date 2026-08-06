# Транспорт ccxt в десяти проектах жанра crypto signal

**Дата:** 2026-08-05.
**Что это:** самостоятельное исследование десяти сторонних проектов. Настоящий проект в
этом документе НЕ упоминается и ни с чем не сравнивается — сравнение вынесено отдельно
(`ccxt-conformance-2026-08-05.md`, §8). Здесь только то, как устроен транспорт у других.

---

## 0. Как отбирались проекты

**Требования к включению — два, и оба проверены, а не приняты на слово:**

1. проект относится к жанру crypto signal — сканирование рынков, индикаторы, оповещения,
   детекторы аномалий (а не исполнение ордеров как основная задача);
2. проект **действительно использует ccxt**, что подтверждено импортом в исходниках после
   поверхностного клонирования репозитория.

Поиск шёл по каталогу awesome-ccxt и по запросам GitHub `ccxt+signal`, `ccxt+scanner`,
`ccxt+alerts+crypto`. Рамка отбора намеренно НЕ содержит признаков транспорта (ни
`watch_ohlcv`, ни `fetch_ohlcv`): иначе выборка нашла бы только тех, кто уже сделал
определённый выбор, и подтвердила бы сама себя.

**Отсеяны после проверки — не используют ccxt:**

| проект | звёзд | чем оказался |
|---|---:|---|
| jesse-ai/jesse | 8293 | ccxt в списке зависимостей отсутствует; свои драйверы и websocket-client |
| CyberPunkMetalHead/Binance-volatility-trading-bot | 3491 | python-binance 0.7.9 |
| blueforest-capital/alert-bot-crypto-ccxt | 1 | ccxt есть, но свечей не берёт вовсе — только тикер и баланс |

**Итоговые десять:**

| # | проект | звёзд | язык | жанр |
|---|---|---:|---|---|
| 1 | CryptoSignal/crypto-signal | 5612 | Python | технический анализ, оповещения |
| 2 | Haehnchen/crypto-trading-bot | 3510 | TypeScript | сигналы по многим биржам |
| 3 | OfficialGIGA/crypto-signal-scanner | — | Python | сканер сигналов, RSI/EMA/Bollinger/ATR |
| 4 | stefanoviana/crypto-pump-scanner | 8 | Python | детектор «памп», 500+ пар |
| 5 | grayspace/cryptocurrency-exchange-scanner | 12 | JavaScript | сканер + технические индикаторы |
| 6 | puneeth714/fourgp_bot | 7 | Python | API сигналов по тренду |
| 7 | AccursedGalaxy/scanner | — | Python | анализ объёмов |
| 8 | thanhnguyennguyen/trading-indicator | — | JavaScript | генерация индикаторов из данных ccxt |
| 9 | ryomenhaider/vektor-detector | — | Python | детектор манипуляций рынком |
| 10 | San4ouzs/Crypto-volume-spike-bot | 2 | Python | всплески объёма, топ-50 |

---

## 1. Карточки проектов

### 1.1. CryptoSignal/crypto-signal — 5612★, Python

Создание клиента и запрос свечей:

    new_exchange = getattr(ccxt, exchange)({
        "enableRateLimit": True
    })
    ...
    historical_data = self.exchanges[exchange].fetch_ohlcv(
        market_pair,
        timeframe=time_unit,
        since=start_date
    )
    ...
    time.sleep(self.exchanges[exchange].rateLimit / 1000)

* **Транспорт:** только REST. Вебсокет не используется нигде.
* **Лимит:** встроенный троттлер включён **и сверх него** ручная пауза
  `rateLimit / 1000` после каждого запроса — то есть задержка удваивается сознательно.
* **Повтор:** декоратор `tenacity`
  `@retry(retry=retry_if_exception_type(ccxt.NetworkError), stop=stop_after_attempt(3))`.
* **Ошибки:** импортирует `from ccxt import ExchangeError`, ловит его отдельно.
* **Закрытость свечи:** не определяется.
* ⚠ **Свойство повтора.** `RateLimitExceeded` наследует `NetworkError`, а `tenacity`
  отбирает исключения по `isinstance`. Значит ответ «лимит превышен» попадает под тот же
  декоратор и повторяется трижды. Утверждение выведено из семантики
  `retry_if_exception_type` и иерархии классов ccxt; код не исполнялся.

### 1.2. Haehnchen/crypto-trading-bot — 3510★, TypeScript

**Единственный из десяти, кто использует ccxt.pro.**

    const ExchangeClass = (ccxt.pro as any)[exchangeId];
    const instance: any = new ExchangeClass({ newUpdates: true });

    while (gen === this.generation) {
      try {
        const update = await instance.watchOHLCVForSymbols(pairs);
        ...
            const key = `${exchangeId}:${symbol}:${period}:${time}`;
            this.buffer.set(key, new ExchangeCandlestick(...));
      } catch (e: any) {
        // Back off before retrying so we don't spin on persistent errors
        await new Promise(resolve => setTimeout(resolve, 5000));
      }
    }

* **Транспорт:** `watchOHLCVForSymbols(pairs)` — **одна подписка на все пары сразу**,
  а не по подписке на пару.
* **`newUpdates: true`** задаётся явно при создании клиента.
* **Закрытость свечи: не определяется вовсе.** Свечи кладутся в буфер по ключу
  «биржа:символ:период:метка времени»; повторное значение той же метки перезаписывает
  прежнее. Формирующаяся свеча просто затирается своей финальной версией.
* **Ошибки:** пауза 5000 мс перед повтором, с комментарием о нежелании крутиться на
  устойчивых ошибках. Классы ccxt не разбираются.
* **Отмена:** счётчик поколений `generation`; при смене цикл выходит и зовёт
  `instance.close()`.
* **История:** отдельная служба предзаполнения по REST, с константой
  `// Delay between API calls to avoid rate limits (ms)`.
* **Сброс буфера:** `setInterval(() => this.flush(), 5000)`.

### 1.3. OfficialGIGA/crypto-signal-scanner — Python

    ex = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})
    ...
    ohlcv = ex.fetch_ohlcv(pair, timeframe="1d", limit=lookback + 5)

* **Транспорт:** только REST.
* **Запас баров:** просит `lookback + 5` — пять лишних свечей сверх нужного окна.
* **Таймаут** задаётся явно (10 с).
* **Паузы:** `time.sleep(1)` и `time.sleep(5)` между проходами сканера.
* **Закрытость свечи:** не определяется.

### 1.4. stefanoviana/crypto-pump-scanner — Python

    def _safe_fetch_ohlcv(self, symbol, *args, **kwargs):
        try:
            return self.client.fetch_ohlcv(symbol, *args, **kwargs)
        except Exception:
            return []

* **Транспорт:** только REST, свечи `1m`, `limit=20…30`.
* **Клиент внедряется снаружи** — готовый экземпляр `ccxt.bybit` с уже вызванным
  `load_markets`.
* ⚠ **Обработка ошибок:** голый `except Exception` с возвратом **пустого списка**. Любой
  отказ — сеть, лимит, неверный символ — неотличим от «свечей нет». Отказ не логируется
  и не считается.
* **Частота:** `SCAN_INTERVAL_SEC` по умолчанию **3 секунды** на 500+ пар.
* **Работа с формирующейся свечой:** `np.mean(candle_volumes[:-1])` — последняя свеча
  исключается из базового среднего. Это статистический приём, а не проверка закрытости.

### 1.5. grayspace/cryptocurrency-exchange-scanner — JavaScript

    let exchange = new ccxt[exchangeId]({ enableRateLimit: true, timeout: 30000, 'verbose': verbose })
    ...
    rateLimit = exchange.id == 'binance' ? exchange.rateLimit + 4000 : exchange.rateLimit
    await sleep(rateLimit)
    const ohlcv = await exchange.fetchOHLCV(ticker, '1h', since)

* **Транспорт:** только REST.
* ⚠ **Особая поправка на Binance:** к паузе добавляется **4000 мс** именно для этой
  биржи. Обоснование в коде не приведено; выглядит как эмпирический обход отказов.
* **`verbose`** ccxt выведен в настройку — отладочный режим библиотеки доступен снаружи.
* **Таймаут** 30 с.
* Второй скрипт использует `hitbtc2` и `await sleep(hitbtc.rateLimit)` в том же стиле.

### 1.6. puneeth714/fourgp_bot — Python

    self.connection_exchange = ccxt.binance()
    ...
    return self.connection_exchange.fetchOHLCV(market, timeframe=timeframe, limit=distance)
    ...
    return ccxt.binance().fetch_ticker(self.config["market_pair"][0])["last"]

* **Транспорт:** только REST.
* ⚠ **`enableRateLimit` не задаётся.** Клиент создаётся без настроек вовсе.
* ⚠ **Новый экземпляр биржи на каждый вызов тикера** — `ccxt.binance().fetch_ticker(...)`
  в теле метода. Каждый такой вызов заново поднимает объект и теряет состояние троттлера.
* **Ошибки:** голый `except Exception` в четырёх местах.

### 1.7. AccursedGalaxy/scanner — Python

    self.exchange = getattr(ccxt, exchange_name)()
    ...
    def fetch_ohlcv(self, symbol, timeframe, since=None):
        ...
        new_data = self.exchange.fetch_ohlcv(symbol, timeframe, since)

* **Транспорт:** только REST, с пагинацией по `since` в цикле.
* ⚠ **`enableRateLimit` не задаётся**, настроек нет.
* **Ошибки:** голый `except Exception` в трёх местах, включая создание клиента.

### 1.8. thanhnguyennguyen/trading-indicator — JavaScript

* **Транспорт:** только REST; `fetchOHLCV` и `fetchTicker` — свечи берутся исключительно
  ради расчёта индикаторов.
* Самый тонкий слой выборки: `indicators/ohlcv.js` — 27 строк.
* **Ошибки:** `catch (err)` без разбора классов, 39 таких мест по проекту.

### 1.9. ryomenhaider/vektor-detector — Python

    self.exchange = getattr(ccxt, exchange_id)({
        'enableRateLimit' : True,
        ...
    })
    def fetch_ohlcv(self, symbol, timeframe=TIMEFRAME, limit=LOOKBACK, since=None):
        raw = self.exchange.fetch_ohlcv(...)
    ...
    time.sleep(0.5)

* **Транспорт:** только REST, обёртка вокруг `fetch_ohlcv` с параметрами по умолчанию.
* **Глубина:** `limit=500` для обучающего окна, `limit=100` для окна события.
* **Пауза** 0.5 с между символами — сверх троттлера.
* **Закрытость свечи:** не определяется.

### 1.10. San4ouzs/Crypto-volume-spike-bot — Python, асинхронный

    ex = cls({"enableRateLimit": True, "timeout": REQUEST_TIMEOUT*1000})
    ...
    return await ex.fetch_ohlcv(market, timeframe=timeframe, limit=limit)
    ...
    await asyncio.sleep(POLL_SECONDS)

* **Транспорт:** только REST, но через `ccxt.async_support` — асинхронный клиент.
* **Опрос:** `POLL_SECONDS` по умолчанию **60**.
* **Таймаут** выводится в настройку.
* **Ошибки:** голый `except Exception`.
* **Формирующаяся свеча:** `return agg[-1], agg[:-1]` — последняя отделяется от базовой
  выборки. Как и в 1.4, это статистика, а не проверка закрытости.

---

## 2. Сводка по десяти проектам

### 2.1. Транспорт

| признак | сколько из 10 |
|---|---:|
| берут свечи **только REST** | **9** |
| используют ccxt.pro (вебсокет) | **1** — Haehnchen |
| используют батч-подписку `watchOHLCVForSymbols` | **1** — Haehnchen |
| используют асинхронный клиент ccxt | 2 — Haehnchen (JS), Crypto-volume-spike-bot |

**Девять из десяти сигнальных проектов берут свечи запросом, а не подпиской.** Причина
видна из устройства задачи: сигнальная система обходит много рынков с невысокой частотой
(3 с … 120 с), и подписка на каждый рынок не окупается — а у Haehnchen, который подписку
всё же использует, она **одна на все пары сразу**.

### 2.2. Определение закрытости свечи

**Ни один из десяти не определяет, закрыта ли свеча.** Все берут то, что вернул API,
вместе с формирующейся свечой.

Двое (crypto-pump-scanner, Crypto-volume-spike-bot) отделяют последнюю свечу от базовой
выборки при расчёте среднего объёма — но это статистическая нормировка «текущее против
истории», а не проверка состояния свечи.

Haehnchen решает вопрос иначе: перезаписью по ключу метки времени. Формирующаяся свеча
попадает в буфер и позже затирается своей же окончательной версией. Явного понятия
«закрыта» в коде нет.

### 2.3. Лимиты запросов

| приём | сколько из 10 |
|---|---:|
| `enableRateLimit: true` задан явно | **7** |
| настроек нет вовсе | **2** — fourgp_bot, AccursedGalaxy/scanner |
| **ручная пауза СВЕРХ троттлера** | **6** |
| читают заголовки ответа о потреблении лимита | **0** |
| читают лимиты у биржи (`exchangeInfo` и подобное) | **0** |

Самое заметное: **шесть из десяти не доверяют встроенному троттлеру и добавляют
собственную паузу.** Формы разные:

* `time.sleep(exchange.rateLimit / 1000)` — crypto-signal, буквально дублирует троттлер;
* `exchange.rateLimit + 4000` для Binance — grayspace, эмпирическая поправка;
* `time.sleep(0.5)` между символами — vektor-detector;
* `setTimeout(5000)` после ошибки — Haehnchen;
* фиксированные `sleep(1)`, `sleep(5)` между проходами — crypto-signal-scanner.

**Ни один не смотрит, сколько лимита израсходовано на самом деле.** Заголовки ответа
(`X-MBX-USED-WEIGHT-1M` у Binance и аналоги) не читает никто.

### 2.4. Обработка ошибок

| приём | сколько из 10 |
|---|---:|
| голый `except Exception` / `catch (err)` | **7** |
| разбирают классы ccxt (`NetworkError`, `ExchangeError`) | **2** — crypto-signal, Haehnchen (частично) |
| ловят класс превышения лимита отдельно | **0** |
| отказ возвращается как пустой результат | **1** — crypto-pump-scanner |

Преобладающий образец — поглотить любое исключение. Крайний случай:
`except Exception: return []`, где отказ сети и отсутствие данных становятся одним и тем
же значением.

### 2.5. Прочие наблюдения

* **`timeout` выводят в настройку 4 проекта** (10–30 с). Умолчание ccxt никто явно не
  обсуждает.
* **`verbose` ccxt** выведен наружу у одного (grayspace) — отладка библиотеки как штатный
  режим.
* **Запас баров сверх нужного** просит один (`lookback + 5`).
* **Пагинация по `since`** реализована у двоих (AccursedGalaxy/scanner, opentrader-подобная
  логика у crypto-signal через `since=start_date`).
* **`market["active"]`** (признак делистинга) не проверяет **ни один**.
* **`exchange.has[...]`** перед вызовом метода не проверяет **ни один** из этих десяти.
* **Отписка (`unWatch*`)** не встречается — у единственного пользователя вебсокета
  подписка живёт до смены поколения, после чего соединение просто закрывается.

---

## 3. Как перепроверить

```bash

# клонировать выборку и убедиться, что ccxt действительно используется
for r in CryptoSignal/crypto-signal Haehnchen/crypto-trading-bot \
         OfficialGIGA/crypto-signal-scanner stefanoviana/crypto-pump-scanner \
         grayspace/cryptocurrency-exchange-scanner puneeth714/fourgp_bot \
         AccursedGalaxy/scanner thanhnguyennguyen/trading-indicator \
         ryomenhaider/vektor-detector San4ouzs/Crypto-volume-spike-bot; do
  git clone --depth 1 -q "https://github.com/$r" "/tmp/$(basename $r)"
  echo "$r: $(grep -rl ccxt "/tmp/$(basename $r)" --include=*.py --include=*.js --include=*.ts | wc -l) файлов"
done

# какие методы ccxt зовут
grep -rhoE "\.(fetch|watch|load)[A-Za-z_]{2,30}\(" /tmp/*/ --include=*.py --include=*.js --include=*.ts \
  | sort | uniq -c | sort -rn | head -20

# ручные паузы сверх троттлера
grep -rn "rateLimit" /tmp/*/ --include=*.py --include=*.js | grep -i "sleep\|+ *[0-9]"

# разбор классов ошибок ccxt
grep -rn "except ccxt\|ccxt\.NetworkError\|ccxt\.ExchangeError\|RateLimitExceeded\|DDoSProtection" \
  /tmp/*/ --include=*.py --include=*.js --include=*.ts

# проверка делистинга и наличия метода
grep -rn "\['active'\]\|\.active\b\|has\[" /tmp/*/ --include=*.py --include=*.js --include=*.ts
```

---

## 4. Чем это исследование ограничено

1. **Ни один проект не исполнялся.** Все утверждения о поведении получены чтением
   исходников. Там, где вывод особенно нагружен (повтор `tenacity` в 1.1), это отмечено
   отдельно.
2. **Читались ветки по умолчанию на 2026-08-05**, версии не закреплены. Через месяц
   выводы могут не воспроизвестись.
3. **Греп считает употребления, а не поведение.** «39 мест с `catch (err)`» говорит о
   частоте образца, но не доказывает, что он применён везде, где нужен.
4. **Размер выборки смещён вниз.** Помимо двух крупных проектов (5612★ и 3510★), восемь
   остальных — малые репозитории от 0 до 12 звёзд. Это цена требования «именно сигнальный
   жанр»: крупные проекты этого жанра почти все оказываются исполняющими ботами. Выборка
   отвечает на вопрос «как пишут сигнальные системы вообще», а не «как пишут лучшие».
5. **Три кандидата отсеяны** за отсутствием ccxt (раздел 0). Если бы отсев не делался,
   доли в разделе 2 были бы посчитаны по проектам, которые к вопросу отношения не имеют.
6. **Один язык — один образец.** TypeScript представлен единственным проектом, и все
   выводы про ccxt.pro опираются на него одного.
