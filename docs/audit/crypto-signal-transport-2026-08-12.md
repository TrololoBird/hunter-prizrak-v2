# Транспорт crypto-signal и его форков: 151 строка на весь жанр

**Указание владельца 2026-08-12:** «В ПЕРВУЮ ОЧЕРЕДЬ ТРАНСПОРТ! изучи детально
crypto-signal проект и его форки!»

Репозитории склонированы (`--depth 1`) и прочитаны построчно, а не пересказаны. Форки
отобраны по звёздам и дате последней правки.

Команда воспроизведения:

```bash
gh api "repos/CryptoSignal/Crypto-Signal/forks?sort=stargazers&per_page=15" --jq '.[] | "\(.full_name)	\(.stargazers_count)	\(.pushed_at[:10])"'
git clone --depth 1 https://github.com/CryptoSignal/Crypto-Signal.git /tmp/cs-orig
git clone --depth 1 https://github.com/proper/crypto-signal.git /tmp/cs-proper
git clone --depth 1 https://github.com/w1ld3r/crypto-signal.git /tmp/cs-w1ld3r
git clone --depth 1 https://github.com/lfzCarlosC/crypto-signal.git /tmp/cs-lfz
wc -l /tmp/cs-*/app/exchange.py
diff -u /tmp/cs-orig/app/exchange.py /tmp/cs-proper/app/exchange.py
diff -u /tmp/cs-orig/app/exchange.py /tmp/cs-w1ld3r/app/exchange.py
```

---

## 1. Оригинал: CryptoSignal/Crypto-Signal, 5.6 тыс. звёзд

Весь транспорт — файл **app/exchange.py**, **151 строка**, два публичных метода.

### 1.1. Создание клиента

    new_exchange = getattr(ccxt, exchange)({
        "enableRateLimit": True
    })

Это всё. Ни таймаута, ни `options`, ни настройки темпа, ни выбора площадки.

### 1.2. Свечи

    historical_data = self.exchanges[exchange].fetch_ohlcv(
        market_pair,
        timeframe=time_unit,
        since=start_date
    )

**`limit` не передаётся.** Значит берётся умолчание биржи — у Binance это 500 баров, что
по таблице ccxt стоит вес 5, тогда как 499 бара стоят 2. Проект платит вдвое с лишним за
один лишний бар и об этом не знает.

### 1.3. Повтор при отказе

    @retry(retry=retry_if_exception_type(ccxt.NetworkError), stop=stop_after_attempt(3))

⚠ **Это ровно та ловушка, которую мы разобрали и от которой отказались.**
`RateLimitExceeded` наследует `NetworkError`, значит после `HTTP 429` запрос повторяется
трижды — то, за что Binance банит по IP на срок до трёх суток. Здесь она реализована
руками через `tenacity`, у ccxt — встроена под именем `maxRetriesOnFailure`. Механизм
разный, дефект один.

### 1.4. Пауза поверх троттлера

    time.sleep(self.exchanges[exchange].rateLimit / 1000)

После каждого вызова, в обоих методах. То есть встроенному ограничителю не доверяют и
добавляют свой — при том, что `enableRateLimit: True` уже включён. Пауза при этом не
знает ни о весе запроса, ни о заголовке потребления: `rateLimit` у ccxt — константа мс
между запросами, а Binance считает ВЕС.

### 1.5. Отсчёт окна — и дефект в нём

    timedelta_values = {'m': 'minutes', 'h': 'hours', 'd': 'days',
                        'w': 'weeks', 'M': 'months', 'y': 'years'}
    timedelta_args = {timedelta_values[time_period]: int(time_quantity)}
    start_date_delta = timedelta(**timedelta_args)

⚠ `datetime.timedelta` не принимает `months` и `years`. Таймфреймы `1M` и `1y` роняют
метод `TypeError`, и это не поймано ничем: перехватывается только `ccxt.NetworkError`.

### 1.6. Чего в транспорте НЕТ вовсе

* проверки, закрыта ли последняя свеча — берётся всё, что вернул API, вместе с текущей;
* чтения заголовков потребления лимита (`x-mbx-used-weight-1m`);
* чтения лимитов у самой биржи (`exchangeInfo.rateLimits`);
* таймаута соединения (умолчание ccxt 10 с не изменено);
* вебсокета — вообще, ни одного `watch_*`;
* сделок — ни `fetch_trades`, ни aggTrades, ни профиля объёма.

Единственная настоящая оптимизация во всём проекте — кэш в **behaviour.py**:

    if candle_period not in historical_data_cache:
        historical_data_cache[candle_period] = self._get_historical_data(...)

Одна выборка на пару и таймфрейм за цикл, сколько бы индикаторов её ни просили.
Цикл — `update_interval: 300` секунд, умолчание `candle_period: 1d`.

---

## 2. Форки: транспорт почти никто не трогал

| форк | звёзд | последняя правка | что сделано с транспортом |
|---|---:|---|---|
| `proper/crypto-signal` | 2 | 2019-01-04 | **ничего**; изменён только отбор рынков по котируемой валюте |
| `w1ld3r/crypto-signal` | 84 | 2024-02-03 | три правки, разбор ниже |
| `lfzCarlosC/crypto-signal` | 11 | 2026-08-04 | транспорт превращён в абстрактный класс |

### 2.1. `proper/crypto-signal` — «поддерживаемый» форк

Дифф **exchange.py** против оригинала не содержит ни одной правки в `get_historical_data`.
Метод побайтно тот же. Изменён только `get_exchange_markets`: добавлен отбор пар по
списку котируемых валют.

### 2.2. `w1ld3r/crypto-signal` — самый звёздный

Три содержательных изменения:

1. **фьючерсы:**

       parameters = {'enableRateLimit': True}
       if exchange_config[exchange].get('future') == True:
           parameters['options'] = {'defaultType': 'future'}

   Тот же ключ, который мы закрепили сегодня по спецификации;

2. `max_periods` со 100 до **240**;

3. **вселенная по обороту** — метод `get_top_markets`: `fetch_tickers()`, сортировка по
   `quoteVolume`, верхние N с чёрным списком. То есть список символов выбирается ДАННЫМИ,
   а не рукой.

Не изменилось ничего из перечисленного в §1.6: ни `limit`, ни таймаут, ни повтор после
429, ни ручная пауза. Гейт возможности при этом — `if self.exchanges[exchange].has['fetchTickers']`,
то есть карта `has`, которая нам солгала семь раз из семи.

### 2.3. `lfzCarlosC/crypto-signal` — самый свежий

**app/exchange.py** сведён к абстрактному классу в 25 строк с телами `pass`; реализации
разнесены по **exchange_cryptal.py** и **exchange_ashare.py**. Во вспомогательных скриптах
того же форка транспорт настроен иначе и лучше: `fetch_ohlcv(..., limit=200)`,
`ccxt.bitget({"enableRateLimit": True, "timeout": 15000})`.

⚠ И там же — наглядный дефект, который стоит запомнить. Файл **haoyangmao.py**, ставящий
РЕАЛЬНЫЕ рыночные ордера на фьючерсах Binance:

    bn = ccxt.binance({
        "apiKey": '…',
        'enableRateLimit': True,
        "timeout": 3000,
        "enableRateLimit": False
        })

Ключ задан ДВАЖДЫ. Python оставляет последний, значит ограничитель ВЫКЛЮЧЕН, а строка
`'enableRateLimit': True` двумя строками выше создаёт видимость обратного.

---

## 3. Что из этого берём

| приём | откуда | почему |
|---|---|---|
| одна выборка на (пару, ТФ) за цикл | оригинал | у нас засев так и устроен — совпало |
| `options: {'defaultType': …}` | w1ld3r | закреплено сегодня по спецификации, независимо |
| вселенная по `quoteVolume` из `fetch_tickers` | w1ld3r | **у нас список статичен с 2026-08-03 и ни разу не пересматривался** |

## 4. Что НЕ берём, и почему именно

| приём | почему нет |
|---|---|
| повтор по `ccxt.NetworkError` | `RateLimitExceeded` в этом семействе — повтор после 429 приближает бан по IP |
| `time.sleep(rateLimit/1000)` поверх троттлера | двойное торможение, и оно не знает веса запроса |
| `fetch_ohlcv` без `limit` | умолчание 500 стоит вес 5 вместо 2 у 499 |
| `has[...]` как гейт возможности | семь объявленных `True` оказались недоступны при вызове |
| `timedelta(months=…)` | падает `TypeError`; у нас длительности ТФ в таблице и сверены с ccxt 6 из 6 |

## 5. Вывод, который здесь важнее списков

Транспорт этого жанра тонок не по небрежности, а по задаче: **взять N свечей, посчитать
индикаторы, оповестить.** Для такой работы 151 строки достаточно, и восемь лет форков
это подтверждают — транспорт не трогали ни разу по существу.

Отсюда следует то, что режет в обе стороны и должно быть сказано прямо:

* довод «все делают через свечи» — **верен**: ни один проект жанра не берёт отдельных
  сделок;
* довод «значит и остальное надо делать как они» — **неверен**: ни один проект жанра не
  считает вес, не проверяет закрытость свечи, не читает лимиты у биржи и не различает
  площадки. Если мерить нас их транспортом, лишними окажутся не только сделки.

Разница не в аккуратности, а в том, что наша задача включает профиль объёма, леджер и
воспроизводимость расчёта. Сравнивать стоит задачи, а потом уже транспорты.
