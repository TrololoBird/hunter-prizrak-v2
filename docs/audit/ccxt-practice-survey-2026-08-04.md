# Расширенная выборка: как закрытие свечи и надёжность WS решают пять независимых реализаций

**Дата:** 2026-08-04. Второе расширение выборки. Предыдущее: [`ccxt-review-2026-08-04.md`](ccxt-review-2026-08-04.md) (выборка n=1, freqtrade). Промежуточный шаг n=2 (добавлен OctoBot) отдельным файлом НЕ сохранён — его содержимое целиком вошло в настоящий документ.

Замечание владельца — выборка всё ещё узкая — принято. Ниже выборка расширена до **пяти реализаций, трёх спецификаций бирж и одного заявления сотрудника биржи**, и главный вывод от этого не смягчился, а усилился.

---

## 1. Что попало в выборку и почему именно это

| # | проект | язык | транспорт | зачем в выборке |
|---|---|---|---|---|
| 1 | **freqtrade** | Python | **ccxt.pro** | ближайший аналог: тот же язык, тот же транспорт, боевая эксплуатация |
| 2 | **OctoBot-Trading** | Python | **ccxt.pro** | второй крупный потребитель ccxt.pro, независим от 1 |
| 3 | **Hummingbot** | Python | свой WS | контроль: тот же язык, ДРУГОЙ транспорт — видно, что от ccxt, а что нет |
| 4 | **cryptofeed** | Python/Cython | свой WS | специализированная библиотека рыночных данных, не бот |
| 5 | **gocryptotrader** | Go | свой WS | другой язык и другая экосистема — контроль на «это не привычка Python» |

Плюс первоисточники бирж: **Binance USDⓈ-M**, **Bybit v5**, **OKX v5** — по спецификации потока свечей у каждой.

Отбор не случаен и это надо назвать: все пять — крупные зрелые проекты. Выборка смещена **в сторону хорошей практики**; «типовой бот с GitHub» в неё не попал. Для вопроса «как делают те, кто делает аккуратно» это то, что нужно; для вопроса «как делают вообще» — нет.

---

## 2. Главный вопрос: как определяется, что свеча закрыта

### 2.1. Сводная таблица — пять реализаций

| проект | правило | код |
|---|---|---|
| **freqtrade** | преемник в кэше **плюс** свежесть ≥ полсвечи, иначе откат на REST; наружу отдаётся `ticks[-2]` | `(len(candles) > 1 and candles[-1][0] >= prev_candle_ts) and last_refresh_time >= half_candle`; `idx = -2 if drop_incomplete and len(ticks) > 1 else -1` |
| **OctoBot** | преемник; наружу отдаётся **предыдущая** свеча; часов нет вовсе | `if previous_candle_time < current_candle_time:` → `# new candle is after the previous one: the previous one is now closed` → `push_to_channel(OHLCV_CHANNEL, ..., previous_candle)`, `# OHLCV_CHANNEL only takes closed candles` |
| **Hummingbot** | преемник по метке времени: новая метка → добавить, та же → **заменить последний элемент** | `if current_timestamp > latest_timestamp: self._candles.append(...)` / `elif current_timestamp == latest_timestamp: self._candles[-1] = candles_row` |
| **cryptofeed** | **флаг биржи** | `if self.candle_closed_only and not msg['k']['x']: return`; в типе — `cdef readonly bint closed` |
| **gocryptotrader** | не решает: отдаёт свечу как есть, флаг не переносит | `kline.Candle{Time, Open, Close, High, Low, Volume}` — поля `x` в структуре нет |
| **hunter-v2** | **настенные часы против границы ТФ** | `if now < open_ms + tf_ms(timeframe): continue` |

**Настенные часы — 0 из 5.** Ни одна из пяти реализаций не решает вопрос сравнением часов с границей таймфрейма. Три используют преемника, одна — флаг биржи, одна вопрос не решает и передаёт наверх.

### 2.2. Самое сильное наблюдение: флаг есть у всех бирж, но им пользуется одна реализация из пяти

Первоисточники:

| биржа | поле | формулировка спецификации |
|---|---|---|
| Binance USDⓈ-M | `k.x` | «Is this kline closed?»; **Update Speed: 250ms** |
| Bybit v5 | `confirm` | «Whether the tick is ended or not»; «If confirm=true, this means that the candle has closed. Otherwise, the candle is still open and updating» |
| OKX v5 | `confirm` (индекс 7) | «0: represents that it is uncompleted, 1: represents that it is completed» |

Признак закрытия есть у **всех трёх** проверенных бирж. При этом:

* **Hummingbot** разбирает сырой payload Binance и читает из него `t, o, l, h, c, v, q, n, V, Q` — **поле `x` пропускает**, хотя оно рядом. То же на Bybit (`confirm` не читается) и на OKX (берутся индексы 0–4, 6, 7 как `quote_asset_volume`, а не `confirm`).
* **gocryptotrader** так же: в структуру `kline.Candle` флаг не переносится.
* **cryptofeed** — единственная из пяти, кто им пользуется, и делает это через явную опцию `candle_closed_only`.

То есть моё прежнее утверждение «булев флаг закрытости — нормальное представление в этом классе библиотек» было **половиной правды**. Точная формулировка:

> Флаг закрытия — нормальное представление **на стороне биржи** (3 из 3). На стороне потребителей он используется редко (1 из 5): большинство выводит закрытость структурно, из появления следующей свечи. Причина этого выбора нигде не объяснена, но она угадывается по составу выборки — правило преемника работает одинаково на всех биржах и на всех транспортах, включая те, где флага нет (Kraken, KuCoin — по перечню в ccxt#21885), а флаг требует отдельной ветки на каждую биржу.

### 2.3. Почему у часов нет шансов — первоисточник

Binance USDⓈ-M: **Update Speed = 250 ms**. То есть форминг-свеча обновляется 4 раза в секунду, и последнее до-граничное обновление отстоит от границы не более чем на 250 мс.

Ветка [Binance Developer Community](https://dev.binance.vision/t/when-using-websocket-the-closed-signal-of-candlesticks-is-too-delayed/15475): пользователь сообщает о задержке прихода `x=true` **до 3 секунд**, сотрудник Binance отвечает:

> «The received time depends on many external factors during the transmission that can be out of Binance control.»

В соседней ветке в чистом случае задержка — **5 мс** (свеча с `T = 1673599679999` получает `x=true` в `1673599680004`).

Разброс 5 мс … 3 с при шаге обновлений 250 мс означает: правило «отдать бар в момент `now >= open_ms + tf_ms`» опережает окончательное обновление всякий раз, когда доставка последнего до-граничного сообщения задерживается больше, чем осталось до границы. `emitted` после этого блокирует окончательную версию навсегда. Это не редкий угол — это **свойство транспорта, признанное биржей**.

---

## 3. Где выборка НЕ даёт согласия — и где я раньше выдал одно мнение за практику

### 3.1. Порог тишины: четыре реализации — четыре разных ответа

| проект | как меряется «поток жив» | число |
|---|---|---|
| freqtrade | свежесть последнего обновления **относительно длительности свечи** | `last_refresh_time >= half_candle` |
| OctoBot | время с последнего сообщения **любого фида**, абсолютное | `NO_MESSAGE_DISCONNECTED_TIMEOUT = 4 * 60` с |
| Hummingbot | таймаут на чтении **→ отправить ping**, а не переподключаться | `asyncio.wait_for(..., timeout=self._ping_timeout)` → `await websocket_assistant.send(ping_request)` |
| hunter-v2 | фиксированный порог на ожидании конкретной пары | `WS_SILENCE_S = 60`, `WS_TRADES_SILENCE_S = 300` |

**Консенсуса нет.** Моя прежняя рекомендация «порог должен быть долей ТФ» (C-5) — это позиция одного проекта из четырёх, и я снимаю её как вывод из практики. Что общего у всех четырёх: **все меряют свежесть и все считают, что одного фиксированного числа на все таймфреймы мало** — но лечат это по-разному, и двое из четырёх вообще не считают тишину поводом переподключаться.

### 3.2. Откат на REST: 2 из 5, и в разных ролях

* **freqtrade** — при непригодности WS-данных берёт свечу через `fetch_ohlcv` («falling back to REST api»).
* **Hummingbot** — заполняет историю через REST (`fill_historical_candles`) и сшивает с WS-потоком по метке времени: `candles_df.drop_duplicates(subset=["timestamp"])`.
* **OctoBot** — в WS-коннекторе отката нет, только переподключение.
* cryptofeed, gocryptotrader — библиотеки данных, у них это забота потребителя.

Общее у двоих: **REST используется как источник того, чего WS дать не может, и сшивка идёт по метке времени с дедупликацией.** Это уже не n=1, но и не «все так делают».

### 3.3. Плановый сброс соединений: по-прежнему 1 из 5

Только freqtrade (`reset_connections()`, «avoids connection-reset errors that happen after ~9 days»). OctoBot закрывает соединение **по событию** (`_close_exchange_to_force_reconnect` в ветке `NetworkError`). Остальные три не проверялись на этот счёт.

Понижаю окончательно: **плановый сброс — решение одного проекта; принудительное закрытие ради переподключения — двух из двух потребителей ccxt.**

---

## 4. Что подтверждается и остаётся в силе

| утверждение | подтверждений | источники |
|---|---|---|
| закрытость определять по преемнику, а не по часам | **3 реализации + 0 против** | freqtrade, OctoBot, Hummingbot; cryptofeed решает через флаг, gocryptotrader не решает; часами — никто |
| часы не годятся, потому что признак закрытия приходит с переменной задержкой | **первоисточник** | Binance: Update Speed 250 ms; сотрудник Binance про внешние факторы; замер 5 мс … 3 с |
| ccxt теряет биржевой флаг, и это известно | **код + заявка** | `handle_ohlcv` 4.5.70; ccxt#21885 открыта с 03.2024 |
| ловить всё дерево ошибок ccxt, а не подмножество | **2 из 2 потребителей ccxt** | freqtrade `except ccxt.BaseError`; OctoBot `except Exception` с особым случаем `ExchangeClosedByUser` |
| `BadRequest` не повторять в цикле | **1 явно + 1 неявно** | OctoBot: `# there is a real issue when connecting to the feed. Don't loop` |
| закрывать соединение явно ради переподключения | **2 из 2 потребителей ccxt** | freqtrade `reset_connections`; OctoBot `_close_exchange_to_force_reconnect` |
| копировать буферы ccxt перед обработкой | **2 из 2** | оба `deepcopy`, с одинаковым обоснованием про внутренние буферы ccxt |
| WS в отдельном event loop | **2 из 2 потребителей ccxt + 1** | freqtrade — отдельный поток; OctoBot — `local_loop`; Hummingbot — отдельная задача с собственным жизненным циклом |
| считать свечи, пришедшие не по порядку | **1 из 5** | OctoBot: `«{n} unordered candles in a row»`, с защитой от спама логов |
| сшивать WS и REST по метке времени с дедупликацией | **2 из 5** | freqtrade, Hummingbot (`drop_duplicates(subset=["timestamp"])`) |
| `newUpdates=True` — фактическое поведение 4.5.70 | **замер + чужой отчёт** | мой замер; ccxt#24658 («returns only 1 bar despite limit=10») |

---

## 5. Что это означает для `hunter/exchange.py` — в порядке уверенности

1. **Правило закрытия свечи надо менять.** Это единственная рекомендация, у которой согласованное большинство (3 из 3 решающих реализаций) плюс подтверждение первоисточника о том, почему часы не годятся. hunter здесь одинок в выборке.
2. **Ловить `ccxt.BaseError`, отделив `ExchangeClosedByUser` и не зацикливая `BadRequest`.** 2 из 2 сопоставимых проектов; плюс доказано деревом классов, что `OperationFailed` и `UnsubscribeError` сейчас проходят мимо.
3. **Явно закрывать соединение при сетевом сбое.** 2 из 2 сопоставимых проектов.
4. **Считать свечи не по порядку.** 1 из 5, но у OctoBot этот код отлажен и снабжён защитой от спама — значит явление встречается. У hunter тот же случай гасится строкой `if open_ms <= emitted: continue` без счётчика, что противоречит §4.3 самого проекта.
5. **WS вынести из общего цикла.** 3 из 5 так делают; у hunter 189 задач и поштучная обработка сделок делят один цикл, а от задержки пробуждения зависит вердикт «бар закрыт».
6. **Порог тишины** — консенсуса нет, решение остаётся за владельцем; фиксировать надо только то, что 60 с на все ТФ не поддержаны ни одной из четырёх реализаций.
7. **Откат на REST и плановый сброс** — 2 из 5 и 1 из 5. Как аргумент из практики слабы; как аргумент из устройства системы (пропущенное за обрыв не добирается ничем) остаются в силе, и это надо честно разделять.

---

## 6. Чем эта выборка ВСЁ ЕЩЁ ограничена

1. **Потребителей именно ccxt.pro — только два.** Для ccxt-специфичных утверждений (классы ошибок, троттлер, переподключение) выборка по-прежнему n=2. Hummingbot, cryptofeed и gocryptotrader сюда не считаются: у них свой транспорт. Найти третьего крупного потребителя ccxt.pro мне не удалось — поиск выдаёт либо заявки в самом ccxt, либо небольшие репозитории, качество которых я не проверял.
2. **Выборка смещена в сторону зрелых проектов.** Все пять — известные системы с многолетней историей. Это отвечает на вопрос «как делают аккуратно», а не «как делают обычно».
3. **Комментарии в заявках GitHub прочитать не удалось** — инструмент отдаёт только тело заявки. Позиция сопровождающих ccxt по флагу закрытия (#21885), по `newUpdates` (#24658) и по отказам при большом числе подписок (#23929) выведена из статуса заявок и из кода, а не из их слов. Это существенное ограничение, и я его не обхожу.
4. **Задержка `x=true` до 3 секунд** — сообщение пользователя форума, подтверждённое сотрудником Binance качественно, но не количественно. Собственного замера нет: `fapi.binance.com` из этого окружения отвечает `HTTP 451`.
5. **Прочитаны разделы, а не проекты целиком.** У OctoBot — пути свечей, ошибок и переподключения из 1350 строк; у Hummingbot — candles_base.py и три коннектора; у freqtrade — exchange_ws.py целиком и ws-путь в `exchange.py`. Механизмы вне этих путей я не видел.
6. **Ни одна из бирж выборки не проверена на живом соединении** — только по спецификации.
7. **Кросс-версионность не проверялась**: изучены текущие ветки `master`/`develop`. Как эти решения выглядели год назад и почему менялись — не смотрел, хотя история изменений exchange_ws.py и ccxt_websocket_connector.py была бы сильным источником: она показывает, какие подходы отбрасывались.

---

## 7. Как перепроверить числа этого документа (§7.3)

Числа здесь — не замеры прибора, а **счёт по чужому коду**: «0 из 5», «1 из 5», «3 из 3».
Воспроизводятся они не прогоном hunter, а получением тех же файлов и поиском тех же
фрагментов. Ветки — подвижные (`master`/`develop`), поэтому расхождение результата с
таблицей ниже означает не ошибку документа, а изменение чужого проекта; дата съёма —
2026-08-04.

```bash
# Пять реализаций: получить и сложить рядом
mkdir -p /tmp/practice && cd /tmp/practice
curl -sO https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/exchange/exchange_ws.py
curl -sO https://raw.githubusercontent.com/Drakkar-Software/OctoBot-Trading/master/octobot_trading/exchanges/connectors/ccxt/ccxt_websocket_connector.py
curl -sO https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/candles_base.py
curl -sO https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/binance_perpetual_candles/binance_perpetual_candles.py
curl -sO https://raw.githubusercontent.com/bmoscon/cryptofeed/master/cryptofeed/exchanges/binance.py
curl -sO https://raw.githubusercontent.com/thrasher-corp/gocryptotrader/master/exchanges/binance/binance_websocket.go

# §2.1, столбец «правило» — по одной строке на реализацию
grep -n 'prev_candle_ts\|drop_incomplete'      exchange_ws.py                   # freqtrade: преемник + свежесть
grep -n 'previous_candle_time\|closed candles' ccxt_websocket_connector.py      # OctoBot: преемник
grep -n 'current_timestamp >\|current_timestamp ==' candles_base.py             # Hummingbot: преемник по метке
grep -n "candle_closed_only\|\['x'\]"          binance.py                       # cryptofeed: ФЛАГ биржи
grep -n 'kline.Candle{'                        binance_websocket.go             # gocryptotrader: флага в структуре нет

# §2.2 «поле x пропускается»: у Hummingbot рядом с разбором payload его нет
grep -n '"t"\|"o"\|"h"\|"l"\|"c"\|"v"\|"x"'    binance_perpetual_candles.py

# §2.1, строка hunter-v2 — единственная, что считается в ЭТОМ дереве
grep -n 'def is_closed' -A6 src/hunter/bars.py
```

Ожидаемый счёт по §2.1: настенные часы — **0 из 5**, преемник — **3 из 5**, флаг биржи —
**1 из 5**, не решает вопрос — **1 из 5**. Строки спецификаций бирж (`k.x`, `confirm`,
`confirm`) перепроверяются по ссылкам раздела «Первоисточники бирж» — они за пределами
кода и командой не берутся.

⚠ Ограничение §6.1 действует и здесь: потребителей именно ccxt.pro в выборке два, и
никакая команда этого не исправит.

---

## Источники

**Реализации**

* [freqtrade — exchange_ws.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/exchange/exchange_ws.py) · [`exchange.py`](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/exchange/exchange.py) · [PR#10273](https://github.com/freqtrade/freqtrade/pull/10273)
* [OctoBot-Trading — ccxt_websocket_connector.py](https://raw.githubusercontent.com/Drakkar-Software/OctoBot-Trading/master/octobot_trading/exchanges/connectors/ccxt/ccxt_websocket_connector.py)
* [Hummingbot — candles_base.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/candles_base.py) · [binance_perpetual_candles.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/binance_perpetual_candles/binance_perpetual_candles.py) · [bybit_perpetual_candles.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/bybit_perpetual_candles/bybit_perpetual_candles.py) · [okx_perpetual_candles.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/data_feed/candles_feed/okx_perpetual_candles/okx_perpetual_candles.py)
* [cryptofeed — `types.pyx`](https://raw.githubusercontent.com/bmoscon/cryptofeed/master/cryptofeed/types.pyx) · [exchanges/binance.py](https://raw.githubusercontent.com/bmoscon/cryptofeed/master/cryptofeed/exchanges/binance.py) · [cryptofeed#782](https://github.com/bmoscon/cryptofeed/issues/782)
* [gocryptotrader — `binance_websocket.go`](https://raw.githubusercontent.com/thrasher-corp/gocryptotrader/master/exchanges/binance/binance_websocket.go)

**Первоисточники бирж**

* [Binance USDⓈ-M — Kline/Candlestick Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams)
* [Bybit v5 — Kline websocket](https://bybit-exchange.github.io/docs/v5/websocket/public/kline)
* [OKX — Candlesticks channel](https://web3.okx.com/onchainos/dev-docs-v5/dex-api/dex-websocket-candlesticks-channel)
* [Binance Developer Community — «closed signal of candlesticks is too delayed»](https://dev.binance.vision/t/when-using-websocket-the-closed-signal-of-candlesticks-is-too-delayed/15475) · [«Websocket Kline when is IsClosed true?»](https://dev.binance.vision/t/websocket-kline-when-is-isclosed-true/14367)

**ccxt**

* [CCXT Pro Manual](https://docs.ccxt.com/docs/pro-manual) · исходник `ccxt==4.5.70`
* [#21885 флаг закрытия (открыт)](https://github.com/ccxt/ccxt/issues/21885) · [#24658 newUpdates отдаёт 1 бар](https://github.com/ccxt/ccxt/issues/24658) · [#23929](https://github.com/ccxt/ccxt/issues/23929) · [#12861](https://github.com/ccxt/ccxt/issues/12861) · [#6631](https://github.com/ccxt/ccxt/issues/6631) · [#10786](https://github.com/ccxt/ccxt/issues/10786) · [#20946](https://github.com/ccxt/ccxt/issues/20946) · [#15266](https://github.com/ccxt/ccxt/issues/15266)
