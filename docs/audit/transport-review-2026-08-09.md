# Ревизия транспортного слоя (exchange.py / run.py / service.py) — 2026-08-09

Сверка кода с практиками по 11 источникам; из шести предложений ДВА исполнены в тот же
день (см. §6), остальные записаны с адресами.

## 1. Вердикт живой проверки

```bash
uv run python -m hunter check
```

Прогон 2026-08-09 (после правок §6), 27 символов × 6 ТФ, наблюдение 400 с, дословно:
«ИТОГ: всё в порядке. Проверок пройдено: 9.» Отстающих рядов 0 из 162; 73 680 баров без
необъяснённых разрывов; сделок принято 29 516, расхождение агрегации 9.7e-15; часы
сведены (+198 мс при точности ±167 мс). Один бар отклонён ГРОМКО — BCH/USDT:USDT 1Н
1578873600000, у которого high/low биржи не накрывают open/close: это работа защиты, не
дефект.

⚠ **Более ранний прогон того же дня напечатал «ТРЕБУЕТ ВНИМАНИЯ, плохо в 2 из 9» — и
КАКИЕ именно две проверки были красными, НЕ УСТАНОВЛЕНО.** Вывод того прогона утрачен
по моей вине: команда обрезала его `tail`-ом, сохранился только хвост со строками
«ХОРОШО». Известно, что в момент того прогона был жив ВТОРОЙ экземпляр `hunter check`
(запущенный ревизией параллельно, подтверждён списком процессов) — два засева по 162
ряда с одного IP толкаются в один минутный лимит веса, и это правдоподобный механизм.
Но правдоподобный — не значит установленный: это гипотеза без вывода-свидетельства.
Установлено другое: два последующих прогона, включая одиночный без параллельных
процессов, дали «всё в порядке, 9 из 9». Урок в копилку §7.3: обрезать вывод живой
проверки — терять свидетельство, которое нельзя восстановить.

## 2. Что уже решено и записано (прочитано перед сверкой)

* `transport-decision-2026-08-05.md` — бары REST-опросом, WS только на сделках; "один ряд — один источник"; задания Ж-1…Ж-11.
* `transport-rest-bars-2026-08-05.md` — правка перехода; `poll_late` как нетавтологичный прибор; Ж-11 построен (лимит читается из `exchangeInfo`, вес — из заголовка).
* `ccxt-conformance-2026-08-05.md` — полная сверка API-поверхности; `_fetch_ohlcv_guarded` разделяет классы отказов; `active`, `has[...]`, `Decimal`-тик; открытые пункты: `fetch_time()` без обработки, `Retry-After` не читается, 429/418 живьём не наблюдались.
* `service-24-7-2026-08-06.md` — служба: снимок, расчёт в потоке, `_supervise`, `TradeSequence`, сердцебиение.

## 3. Таблица: практика | источники | у нас | вердикт | правка

| практика | источники (URL) | у нас | вердикт | правка |
|---|---|---|---|---|
| Троттлер включён, лимиты не игнорируются | ccxt Manual (https://github.com/ccxt/ccxt/wiki/Manual, раздел Rate limit); Binance LIMITS (https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) | `enableRateLimit: True` (exchange.py:171), лимит прочитан из `exchangeInfo.rateLimits` (exchange.py:252-281), вес читается из заголовка после каждого вызова (exchange.py:283-302) | СООТВЕТСТВУЕТ, местами лучше практики (0 из 26 проектов выборки читают лимит у биржи) | — |
| 429 → пауза, повтора того же запроса нет; продолжение после 429 = бан 418 | Binance USDⓈ-M General Info (https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info); Binance Academy (https://academy.binance.com/en/articles/how-to-avoid-getting-banned-by-rate-limits) | `RateLimitExceeded`/`DDoSProtection` — отдельная ветка ВЫШЕ `NetworkError`, пауза 60 с, `NotReady` (exchange.py:486-493, 577-585; reload_markets 369-373) | СООТВЕТСТВУЕТ по классификации; ДВА пробела ниже | см. строки "Retry-After" и "глобальный стоп" |
| Читать `Retry-After` при 429/418 — биржа сама называет длину паузы | Binance General Info (там же): "Retry-After header … number of seconds required to wait" | НЕ читается; пауза фиксированная `RATE_LIMIT_BACKOFF_S = 60.0` (exchange.py:127-138) — упрощение ПРИЗНАНО в докстроке и в conformance §6.5 | ОСОЗНАННОЕ упрощение, но источник прямо даёт лучшее число даром | exchange.py:486 и 577: в ветке лимита прочитать `Retry-After` из `self._ex.last_response_headers` (регистр — строчный, как у веса) и спать `max(RATE_LIMIT_BACKOFF_S, retry_after)`; при 418 заголовок = длина бана |
| Бэкофф с ДЖИТТЕРОМ против синхронного стада | AWS "Exponential Backoff And Jitter" (https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/); Amazon Builders' Library "Timeouts, retries and backoff with jitter" (https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/) | ВСЕ паузы фиксированные и одинаковые у всех задач: `WS_RETRY_S = 1.0` (exchange.py:124), `CATCHUP_RETRY_S = 30.0` (exchange.py:86), `RATE_LIMIT_BACKOFF_S = 60.0` (exchange.py:127). 162 задачи баров + 27 задач сделок, проснувшиеся от ОДНОГО сетевого события, повторяют синхронно | ПРОБЕЛ. Смягчён тем, что REST-запросы сериализует троттлер ccxt (стадо давит на очередь, а не на биржу), но 27 WS-переподписок через ровно 1.0 с — стадо в чистом виде | exchange.py:607: `await asyncio.sleep(WS_RETRY_S)` → `WS_RETRY_S * (0.5 + random())` (full/equal jitter); run.py:255: к `CATCHUP_RETRY_S` добавить джиттер ±20%; в 429-ветке паузу рассыпать так же. Внимание §10.6: джиттер не трогает расчёт, только расписание — дифф повтора обязан быть пуст |
| Классы ошибок ccxt не смешивать: NetworkError → повтор штатен, ExchangeError → повтор бесполезен | ccxt Manual "Error handling" (https://github.com/ccxt/ccxt/wiki/Manual); пример kucoin-rate-limit (https://github.com/ccxt/ccxt/wiki/examples/py/kucoin-rate-limit) | `_fetch_ohlcv_guarded` (exchange.py:457-507) и `_watch_step` (exchange.py:537-608): лимит / сеть / биржа / `BaseError`-замыкание, все считаются по классам | СООТВЕТСТВУЕТ, порядок ветвей разобран по `__mro__` (находка C-6 закрыта) | — |
| `ExchangeNotAvailable` = временный отказ, повторяемый | ccxt Manual (там же) | наследник `NetworkError` → ветка "сеть", `NotReady`, следующий опрос повторит (подтверждено подстановкой в conformance §3.1) | СООТВЕТСТВУЕТ | — |
| `fetch_time()` тоже может отказать | ccxt Manual (те же классы летят из любого метода) | `fetch_server_ms` (exchange.py:307-310) БЕЗ try; отказ на контрольном сведении часов роняет прогон ПОСЛЕ сбора, до сохранения кадров | ПРОБЕЛ, ПРИЗНАН открытым в conformance §3.4 и с тех пор не закрыт | exchange.py:307: обернуть в те же классы, отдать `NotReady`; решить в `clock.measure`, что делает прогон с несведённым контрольным замером (это решение по часам, не по транспорту — потому и вынести отдельной правкой) |
| Пагинация fetchOHLCV: `since = last + 1`, вручную, не доверять "автоматике" | ccxt пример gateio-fetch-ohlcv-pagination (https://github.com/ccxt/ccxt/wiki/examples/py/gateio-fetch-ohlcv-pagination); ccxt Manual "Assuming Pagination Works Automatically" | `count_history` (exchange.py:509-535): `since = int(r[-1][0]) + 1`, выход по короткой странице, обязательный `cap` | СООТВЕТСТВУЕТ | — |
| Частичный (незакрытый) последний бар не пускать в расчёт | ccxt Manual (последняя свеча fetchOHLCV может быть незакрытой); практика OctoBot "правило преемника" | `closed_only` в `fetch_closed_ohlcv` (exchange.py:449) — отбрасывается по сведённым часам; плюс НЕЗАВИСИМАЯ проверка ответа биржи против часов → `poll_late` (run.py:275-280) | СООТВЕТСТВУЕТ и сильнее практики (0 из 23 проектов сверяют с независимым источником) | — |
| Дедупликация догрузки баров | ccxt validate-paginated-data (https://github.com/ccxt/ccxt/wiki/examples/ts/validate-paginated-data) — проверка непрерывности меток | курсор `st.bars[-1].open_ms`, `bar.open_ms <= last → skip` (run.py:292-299); `on_grid` и разрывы пересчитываются после КАЖДОГО добора (run.py:317) | СООТВЕТСТВУЕТ; контроль заглушкой в rest-bars §3.1 показал 3 добранных из 121 опроса без дублей | — |
| Gap-fill сделок после реконнекта: держать `fromId` последнего aggTrade и добирать REST-ом | Binance dev community (https://dev.binance.vision/t/how-to-avoid-losing-data-across-user-data-stream-disconnect-reconnects/12354); Binance USDⓈ-M Market Data, Compressed/Aggregate Trades List — `fromId` (https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api); сам протокол решения §2.2 предлагал курсор `fromId` | НЕ РЕАЛИЗОВАН. `TradeSequence` (run.py:320-359) разрывы ВИДИТ и считает (`trade_gaps`), но никто их не ЗАКРЫВАЕТ: потерянное при обрыве сокета/вытеснении кэша ccxt потеряно навсегда, профиль текущих суток занижается молча-для-профиля (громко-для-счётчика) | ПРОБЕЛ — самый крупный из найденных. Прибор есть, руки нет. Для службы 24/7 обрыв ночью = дыра в профиле до публикации суточного архива (до 2 суток) | run.py: рядом с `_watch_trades_impl` — задача догона: при `seq.gap_events > 0` звать `ex._ex.fetch_trades(sym, params={"fromId": seq.last_gap_from})` (вес 20, лимит 1000) и доливать в `hist`/`binned` те же `add`; дедуп по `agg_trade_id` уже напрашивался в decision §3.3 ("сделки из трёх источников со старшинством и дедупом по agg_trade_id" — правило записано, кода нет) |
| Дедуп сделок между источниками (архив / живой поток) по `agg_trade_id` | transport-decision §3.3 (собственное правило проекта); Binance aggTrades `a` строго последователен | `WindowSource.window` (archive.py:324-353): если суток нет в архиве, ДОБАВЛЯЕТСЯ живое окно поверх уже просуммированных архивных суток. Сегодня безопасно (окно live = длительность прогона, архив этих суток ещё не опубликован), но при доживании службы до публикации архива за сутки, которые live ещё держит (`LIVE_TRADES_KEEP_DAYS = 3` > задержка публикации ~1-2 суток), одно окно может сложиться ДВАЖДЫ | ЛАТЕНТНЫЙ РИСК того же класса, что `active=False`: ждёт молча | archive.py:345: добирать live только по СУТКАМ из `missing`, а не всем окном; либо run.py:1062 — резать live не по возрасту, а по факту "сутки легли в архивный кэш" (предложение уже названо в service-24-7 §7.8) |
| recvWindow / -1021 (рассинхрон часов на подписанных запросах) | Binance General Info (там же) | Н/П: только публичные эндпоинты (§1, §5), подписанных запросов нет; часы всё равно сведены с биржей (clock, вне этого обзора) | ОСОЗНАННО не нужно | — |
| HTTP 451 у раннеров GitHub CI | ccxt issue #15891 (https://github.com/ccxt/ccxt/issues/15891); dev.binance.vision (https://dev.binance.vision/t/service-unavailable-from-a-restricted-location/13813) | Признан блокером в CLAUDE.md (внешний отказ). Гейты CI живой биржи не требуют; live-замеры — на машине владельца. 451 приходит как `ExchangeNotAvailable` → ветка "сеть" → бесконечный штатный повтор | ОСОЗНАННО (блокер внешний), но 451 — НЕ временный отказ: повтор его не лечит | необязательно: в ветке `NetworkError` различать 451 по тексту/`e.args` и печатать деградацию "регион заблокирован — повтор бесполезен", чтобы служба в запрещённом регионе не крутила пустые повторы сутками |
| Глобальная реакция на 429: остальные задачи не должны продолжать давить | Binance Academy (там же): "failing to back off after receiving 429s → IP ban"; AWS Builders' Library (circuit breaking) | Пауза 60 с — ПЕР-ЗАДАЧНАЯ (`asyncio.sleep` внутри `_fetch_ohlcv_guarded`). Пока один ряд спит после 429, остальные 161 задача продолжают слать запросы с того же IP (лимит — по IP) | ПРОБЕЛ, смягчён троттлером ccxt (1200/мин, вдвое строже лимита — conformance §2.2), поэтому 429 маловероятен; но если пришёл, значит троттлер уже не спас, и per-task пауза не выход | exchange.py: одно `asyncio.Event`/метка `banned_until_ms` на объекте Exchange; ветка лимита ставит её, `_fetch_ohlcv_guarded`/`_watch_step` проверяют перед запросом. Плюс порог тревоги долей лимита (Ж-11 п.3 — "порог не заведён" до сих пор, rest-bars §10.5) |
| WS: таймаут молчания + переподключение + учёт | ccxt Manual (watch_* пересоединяет ccxt сам); практика freqtrade/OctoBot | `_watch_step` (exchange.py:537-608): таймаут `WS_TRADES_SILENCE_S=300`, все классы ccxt закрыты, `UnsubscribeError` отдельно, счётчики по потокам | СООТВЕТСТВУЕТ; открыт C-5: порог 300 с абсолютный, а не производный от активности инструмента (признано в rest-bars §6) | exchange.py:106: порог = перцентиль собственной истории межсделочных интервалов (задача признана, не сделана; решение §4.1) |

## 4. Отдельно затребованные пункты

1. **Retry без джиттера** — да, все три паузы фиксированные (exchange.py:86, 124, 127), без экспоненты и без джиттера; для 27 WS-задач это синхронное стадо по AWS-классификации. Смягчение: REST сериализован троттлером. Правка тривиальна и расчёта не трогает.
2. **Частичный последний бар** — закрыт дважды: `closed_only` по сведённым часам (exchange.py:449) и независимая сверка с ответом биржи (`poll_late`, run.py:275-280). Лучше выборки из 23 проектов.
3. **Дедупликация догрузки** — для баров закрыта курсором (run.py:292-299, контроль заглушкой: 3 бара из 121 опроса, 0 дублей). Для СДЕЛОК правило "дедуп по agg_trade_id" записано в decision §3.3, но кода нет: живой поток и архив складываются без сверки id (archive.py:345-353) — латентное двойное сложение при службе, дожившей до публикации архива за live-сутки.

## 5. Топ предложений (файл:строка → что → источник)

1. **run.py (~362) → догон потерянных сделок по `fromId` после разрыва** — `TradeSequence` видит дыру, но не закрывает; Binance dev community + собственный протокол решения §2.2 велят держать курсор `fromId`.
2. **exchange.py:486, 577 → читать `Retry-After` при 429/418** — Binance General Info даёт точную длину паузы; фиксированные 60 с при 418 (бан до 3 суток) означают 4320 бесполезных запросов-догонов.
3. **exchange.py (объект Exchange) → глобальная пауза после 429 вместо пер-задачной** — лимит по IP, спящая одна задача из 162 не защищает; Binance Academy: "failing to back off → ban".
4. **archive.py:345 → live-добор по суткам из `missing`, а не всем окном** — латентный двойной счёт профиля при пересечении live-хвоста с опубликованным архивом; собственное правило decision §3.3.
5. **exchange.py:607, run.py:255 → джиттер в паузы повторов** — AWS Exponential Backoff And Jitter; 27 синхронных переподписок через ровно 1.0 с.
6. (меньшее) exchange.py:307 `fetch_server_ms` без try — conformance §3.4, открыт с 05.08; порог тревоги долей лимита — Ж-11 п.3, не заведён.

## 6. Что из предложений ИСПОЛНЕНО 2026-08-09 — и что намеренно нет

* **Исполнено: чтение `Retry-After` при 429/418** — `Exchange._rate_limit_pause_s()`:
  заголовок ищется с явным регистром (та же ловушка асинхронного клиента, что у веса),
  пауза = max(минута, Retry-After), нечитаемое значение деградирует в минуту с
  named-логом. Обе ветки лимита (`_fetch_ohlcv_guarded`, `_watch_step`) переведены.
* **Исполнено: рассинхронизация WS-повторов** — `desync_s(key, base)`: вместо джиттера
  (`random` запрещён §10.3, забанен линтером) — ДЕТЕРМИНИРОВАННЫЙ сдвиг фазы [0.5, 1.5)
  от базы по crc32 ключа задачи. Декорреляция стада та же, воспроизводимость не тронута.
  Дифф повтора после правок — без изменений против диффа до них (расписание ≠ расчёт).
* **Намеренно НЕ исполнено: сдвиг `CATCHUP_RETRY_S`** — докстрока константы выводит 30 с
  из арифметики лимита, укорачивать её сдвигом вниз нельзя, а REST и так сериализован
  троттлером — стадо там давит на очередь, не на биржу.
* **Исполнено (вторым заходом того же дня): live-добор по суткам из `missing`** —
  `WindowSource.window` больше не просит живой буфер всем окном и не обнуляет `missing`
  любым успехом; добор идёт посуточно, непокрытые сутки остаются в отказе. Контроль:
  `docs/audit/probes/probe_live_overlap_2026-08-09.py` — на подстроенном пересечении
  покрытий прежний способ даёт 25.0 при верных 15.0, посуточный даёт 15.0; окно с
  непокрытыми сутками — NotReady. Дифф повтора пуст (сверен с сохранённым диффом до
  правки построчно).
* Остальное (догон сделок по `fromId`, глобальная пауза 429, `fetch_server_ms` без
  try) — предложения с адресами в §5, каждое трогает больше одного модуля и заслуживает
  отдельной правки с контролем.

## Источники (сводно)

1. ccxt Manual — rate limit, error handling, pagination: https://github.com/ccxt/ccxt/wiki/Manual
2. ccxt пример retry на RateLimitExceeded: https://github.com/ccxt/ccxt/wiki/examples/py/kucoin-rate-limit
3. ccxt пример пагинации since=last+1: https://github.com/ccxt/ccxt/wiki/examples/py/gateio-fetch-ohlcv-pagination
4. ccxt проверка непрерывности OHLCV: https://github.com/ccxt/ccxt/wiki/examples/ts/validate-paginated-data
5. Binance USDⓈ-M General Info (429/418/Retry-After/вес/по-IP): https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
6. Binance LIMITS (спот, та же механика заголовков): https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits
7. AWS Exponential Backoff And Jitter: https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
8. Amazon Builders' Library — Timeouts, retries, and backoff with jitter: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
9. Binance dev community — потери на реконнекте и добор: https://dev.binance.vision/t/how-to-avoid-losing-data-across-user-data-stream-disconnect-reconnects/12354
10. ccxt issue #15891 — HTTP 451 из ограниченных регионов (наш блокер CI): https://github.com/ccxt/ccxt/issues/15891
11. Binance Academy — как не попасть в бан по лимитам: https://academy.binance.com/en/articles/how-to-avoid-getting-banned-by-rate-limits
