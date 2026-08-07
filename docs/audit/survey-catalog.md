# Каталог обзоров чужих реализаций

Очередь работы по команде `/survey <элемент>` (`.claude/commands/survey.md`): для каждого
элемента — не меньше 20 НЕЗАВИСИМЫХ проектов, каталог, сверка с нашим кодом, живая
проверка гипотез, решение.

Порядок задан ожидаемой отдачей, а не размером модуля. Отдача тем выше, чем больше
решений в модуле держится на умолчании и чем больше публичных реализаций того же
предмета существует. Первым идёт то, от чего зависит всё остальное.

⚠ Столбец «слова для поиска» — не украшение. Прошлый обзор показал, что выборка
собирается по формулировкам ПРЕДМЕТНОЙ ОБЛАСТИ, а не по именам наших функций: русского
«переприор» в чужих репозиториях нет, а `break of structure` есть в сотнях.

```bash
# состояние очереди: сколько разобрано, сколько осталось
grep -c '| готов' docs/audit/survey-catalog.md; grep -c '| не начат' docs/audit/survey-catalog.md
```

| # | элемент | модуль | слова для поиска | статус | протокол |
|---|---|---|---|---|---|
| 0 | профиль объёма, ПОК | `src/hunter/volume_profile.py` | volume profile, point of control, market profile, TPO, value area | готов | [poc-projects-survey-2026-08-06.md](poc-projects-survey-2026-08-06.md) |
| 1 | свинги | `src/hunter/swings.py` | swing high low, fractal indicator, zigzag, pivot points, williams fractals | не начат | — |
| 2 | слом структуры (переприор) | `src/hunter/pereprior.py` | break of structure, BOS, CHoCH, market structure shift, smart money concepts | не начат | — |
| 3 | прокол против пробоя | `src/hunter/breach.py` | false breakout, fakeout filter, wick vs close breakout, level retest | не начат | — |
| 4 | накопление и выход из него | `src/hunter/accumulation.py` | accumulation range, consolidation detection, wyckoff accumulation, range breakout | не начат | — |
| 5 | сетка баров и закрытость | `src/hunter/bars.py` | partial candle, closed candle only, resample ohlcv, repainting indicator | не начат | — |
| 6 | часы и сдвиг биржи | `src/hunter/clock.py` | exchange server time drift, clock sync trading bot, timestamp skew | не начат | — |
| 7 | стоповый объём | `src/hunter/stop_volume.py` | stop run, liquidity sweep, stop hunt detection, volume spike at level | не начат | — |
| 8 | уровни BUY/SELL | `src/hunter/levels.py` | support resistance detection, price level clustering, level strength scoring | не начат | — |
| 9 | приоритет таймфреймов | `src/hunter/priority.py` | multi timeframe confluence, higher timeframe bias, mtf alignment | не начат | — |
| 10 | геометрия сделки | `src/hunter/geometry.py` | entry stop target placement, risk reward calculation, position sizing | не начат | — |
| 11 | исход сделки | `src/hunter/outcome.py` | trade labeling, triple barrier method, look-ahead bias backtest, path dependency | не начат | — |
| 12 | транспорт и опрос биржи | `src/hunter/exchange.py` | ccxt polling interval, rate limit backoff, websocket reconnect, gap filling ohlcv | не начат | — |
| 13 | хранение и архив | `src/hunter/store.py`, `src/hunter/archive.py` | ohlcv storage parquet, tick data storage, incremental backfill, dedupe trades | не начат | — |
| 14 | доставка сигналов | `src/hunter/emit.py`, `src/hunter/card.py` | telegram trading alerts, signal deduplication, alert throttling | не начат | — |
| 15 | служба без остановки | `src/hunter/service.py`, `src/hunter/engine.py` | 24/7 trading bot loop, graceful restart, backfill on reconnect | не начат | — |
| 16 | детерминированный повтор | `src/hunter/replay.py` | deterministic backtest replay, snapshot testing, golden file test | не начат | — |
| 17 | контракты между слоями | `src/hunter/models.py` | pydantic trading models, strict schema, forbid extra fields | не начат | — |

## Почему очередь именно такая

**1–4 идут первыми, потому что от них зависит остальное.** Свинг — вход в разметку
структуры; слом структуры — вход в переприор; прокол — то, что отличает событие от шума.
Ошибка здесь распространяется вниз по конвейеру и выглядит как ошибка расчёта уровня.

**5–6 — про корректность, а не про метод.** Публичных проектов, которые считают по
незакрытому бару и потому перерисовываются, много; это самый частый дефект в чужом коде и
самый дешёвый в проверке.

**11 стоит отдельно.** Разметка исхода — единственное место, где сравнение с чужими
проектами может дать не приём, а ЗАПРЕТ: заглядывание вперёд в разметке результатов
встречается в публичных бэктестах постоянно, и полезно знать формы, в которых оно
маскируется.

**17 идёт последним умышленно.** Контракты уже накрыты гейтами `models_forbid_extra` и
`no_loose_dicts`; обзор здесь скорее подтвердит сделанное, чем найдёт новое.

## Что записывается в строку после разбора

Статус — `готов`, ссылка на протокол, и вердикт одной фразой в самом протоколе:
принято / отвергнуто / решение владельца. **Отрицательный результат закрывает строку
так же, как положительный:** обзор не обязан заканчиваться изменением, и строка «взято
ничего, причины перечислены» — законченная работа, а не незавершённая.
