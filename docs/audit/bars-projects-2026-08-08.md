# Обзор: СЕТКА БАРОВ И ЗАКРЫТОСТЬ — 2026-08-08

Элемент №5 очереди [survey-catalog.md](docs/audit/survey-catalog.md), модуль
[src/hunter/bars.py](src/hunter/bars.py). Команда: `.claude/commands/survey.md`.

⚠ Файл пишется ПО ХОДУ, а не в конце: обзор длинный, и строка, дописанная после каждого
проекта, переживает обрыв сессии, а держащаяся в голове — нет.

---

## Фаза 0. Вопросы, курс текстом, курс рисунком, как у нас

**Повестку чтения задал курс, а не код.** Грепом по самому PDF: про свечи, закрытие и
таймфреймы говорят **35 страниц из 69**, модуль ссылается на две. Команда воспроизведения:

```bash
uv run --group tools python -c "import pymupdf; d=pymupdf.open('docs/course/Мини Курс по трейдингу от PrizrakTrade.pdf'); keys=('свеч','закры','таймфрейм','тф','бар ','минут','часов','1ч','4ч','дневн','недел','тело','тел ','время','период'); [print(i,[k for k in keys if k in p.get_text().lower()]) for i,p in enumerate(d,1) if any(k in p.get_text().lower() for k in keys)]"
```

Прочитаны текстом стр. 4, 5, 6, 11, 14, 15, 17, 20, 28, 29, 30, 43, 44, 47, 48, 55, 65;
рисунками — стр. 17, 43, 48.

### Таблица фазы 0

| вопрос | курс, ТЕКСТ | курс, РИСУНОК | как у нас | обосновано ли |
|---|---|---|---|---|
| **Б1** какие ТФ основные | стр. 17: «Мы используем основные ТФ ( 5м/15м/час/4ч/1Д/1Н ).» Там же: «Но так же можно добавить разные дополнительные  ТФ 2ч/6ч/12ч/10мин/30 мин и т.д.» | скриншот TradingView со списком ТФ — это инструкция, КАК добавлять, а не перечень используемых | ровно эти шесть | ✅ дословная цитата, совпадение точное |
| **Б2** чем бар считается закрытым | стр. 55 требует «2-3 полных тел свечей ЭТОГО ТФ»; стр. 30: «Важно чтобы цена не закрывалась свечами за уровнем». Курс требует ЗАКРЫТЫХ свечей, но КАК их определить — не говорит | формирующейся свечи курс не рисует нигде | по времени: `now_ms >= open_ms + шаг ТФ` | частично — [ccxt-frame-error-2026-08-04.md](docs/audit/ccxt-frame-error-2026-08-04.md), и он же оставил открытым, успевает ли биржа закрыть свечу к первому опросу после границы |
| **Б3** от чего отсчитывается сетка | молчит | молчит | от эпохи UTC, кроме 1Н — сдвиг 4 суток (понедельник) | ✅ замер, [exchange-facts-2026-08-03.md](docs/audit/exchange-facts-2026-08-03.md) — проверено построчно |
| **Б4** что делать с дырой в ряду | молчит | на всех графиках ряд непрерывен | самый длинный непрерывный хвост | ⚠ **число есть, протокола нет** — см. находку 1 |
| **Б5** «две свечи» — индексы или время | стр. 5: «вернула обратно той 
же или следующей 1-2 свечами»; стр. 43: «Цена одной свечой пробила уровень, второй закрепилась, третьей делает ретест» | стр. 43, вариант 3: свечи нарисованы подряд и пересчитываются поштучно | по времени | ✅ Р-2 в [critical-review-2026-08-04.md](docs/audit/critical-review-2026-08-04.md) — проверено |
| **Б6** что считается дырой | молчит | молчит | любой шаг, не равный шагу ТФ | умолчание |
| **Б7** какой бар обязан быть последним закрытым | молчит | молчит | вычисляется из сетки и времени | умолчание |
| **Б8** что делать с баром ВНЕ сетки | молчит | молчит | проверка есть, решение у вызывающего | умолчание |
| **Б9** неизвестный ТФ | молчит | молчит | отказ с перечислением допустимых | умолчание |

**Курс отвечает на 3 вопроса из 9** (Б1 полностью, Б2 частично, Б5 полностью), **и на всех
трёх мы ему следуем.**

### Находки фазы 0 — до обращения к чужим проектам

**1. Число без протокола.** Докстрока `continuous_tail` приводит замер: EMA200 по целому
ряду 63625.08, по ряду с дырой 63628.18, по непрерывному хвосту из 279 баров 63657.07.
Ни зонда, ни протокола, ни команды воспроизведения. Числа существуют ровно в двух местах —
в самой докстроке и в теле коммита `c41a2d0`; поиск по всему дереву и по всей истории git
других вхождений не дал. §7.3 такое число не фиксирует.

Отдельно: этот замер обосновывает решение, которое сам же и называет разменом — «хвост
дальше от истины». Значит выбор сделан, цена измерена один раз и утеряна.

**2. Курс называет ТФ, которого у нас нет.** Стр. 30: «Если накопление очень большое ТФ
1Д-1Н-1М  - то закуп всегда стоит делить на зону и на уровень.» Месячного ТФ у нас нет.
Расхождением пока не называется: стр. 17 прямо относит всё сверх шести к дополнительным.
Вопрос идёт в фазу 3.

**3. Проверка утверждений о замерах в докстроках: три проверено, два подтвердились, одно
источника не имеет.** По §7.2 предыдущая реализация давала восемь опровергнутых из восьми.

**4. Мёртвых веток в модуле нет.** Все функции вызываются извне, кроме двух внутренних
помощников (`grid_anchor_ms`, `is_closed`), которые зовутся внутри самого модуля.

---

## Фаза 1. Сбор чужих реализаций

**Интернет проверен первым делом:** `github.com/topics/ohlcv` отвечает.

### ⚠ ОТЛИЧИЕ ОТ ПРОШЛЫХ ОБЗОРОВ: поиск ПО КОДУ ОТКРЫТ

В обзорах ПОК, свингов, слома и накопления поиск по коду был закрыт: `grep.app`,
`searchcode.com` и API GitHub отвечали `403`/`429`. Сегодня `grep.app` снова дал **429**,
но **API GitHub через аутентифицированный `gh` отвечает**:

```bash
gh api -X GET search/code -f q='ohlcv[:-1] language:python' --jq '.total_count'   # 215
```

Это лучшая выборка, чем поиск по названиям: он находит реализации по ПРИЁМУ, а не по
имени проекта. ⚠ Поиск капризен — часть запросов возвращает `503 too many shards failed`;
такие запросы переформулируются на одиночные термины.

### Поисковые запросы, дословно (все 2026-08-08)

По коду, через `gh api search/code`:

1. `ohlcv[:-1] language:python`
2. `"candle is closed" language:python`
3. `is_closed timeframe candle language:python` → `503`
4. `"drop the last" candle incomplete language:python` → `503`
5. `repo:freqtrade/freqtrade timeframe_to_prev_date`
6. `repo:freqtrade/freqtrade incomplete`
7. `repo:nautechsystems/nautilus_trader is_closed`

Тематические страницы и поиск репозиториев:

8. `github.com/topics/ohlcv`

---

## Каталог прочитанных реализаций

Строки пишутся по мере чтения. Прочитан ИСХОДНИК, а не README; путь к файлу назван.

### 1. freqtrade — [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)

Крупнейший открытый крипто-фреймворк. Прочитаны [exchange_utils_timeframe.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/exchange/exchange_utils_timeframe.py)
и [converter.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/data/converter/converter.py). Лицензия GPL-3.0.

* **Б1 набор ТФ** — не фиксирован, любой, который отдаёт биржа.
* **Б2 закрытость — ПО ПОЗИЦИИ, а не по времени.** Последняя свеча отбрасывается безусловно:

```python
if drop_incomplete:
    dataframe.drop(dataframe.tail(1).index, inplace=True)
    logger.debug("Dropping last candle")
```

* **Б3 сетка** — для пересемплирования недели явно задан якорь понедельника (`1W-MON`), а
  месяц «липнет» к первому числу. То есть тот же ответ, что у нас, и введён он отдельно —
  поверх ccxt, который якоря не имеет (см. строку 2).
* **Б4 дыра — ЗАПОЛНЯЕТСЯ, а не обходится:**

```python
df["close"] = df["close"].ffill()
df.loc[:, ["open", "high", "low"]] = df[["open", "high", "low"]].fillna(
    value={"open": df["close"], "high": df["close"], "low": df["close"]})
```

  Объём пропущенной свечи — ноль. Это ТРЕТИЙ ответ, которого у нас нет вовсе: мы дыру
  обходим (берём непрерывный хвост), они её достраивают.
* **Б7** — `timeframe_to_prev_date` / `timeframe_to_next_date` через ccxt.

### 2. ccxt — [ccxt/ccxt](https://github.com/ccxt/ccxt)

Транспортная библиотека, которую мы же и используем (§10.1). Прочитан
[base/exchange.py](https://github.com/ccxt/ccxt/blob/master/python/ccxt/base/exchange.py). Лицензия MIT.

* **Б3 сетка — СЫРАЯ ЭПОХА, якоря нет:**

```python
@staticmethod
def round_timeframe(timeframe, timestamp, direction=ROUND_DOWN):
    ms = Exchange.parse_timeframe(timeframe) * 1000
    offset = timestamp % ms
    return timestamp - offset + (ms if direction == ROUND_UP else 0)
```

  Для недели это даёт бар, начинающийся В ЧЕТВЕРГ, потому что эпоха UTC приходится на
  четверг. Наш `GRID_ANCHOR_MS` чинит ровно это, и freqtrade чинит это же своим `1W-MON`.
* **Б9 неизвестный ТФ** — отказ, как и у нас: `raise NotSupported`.
* ⚠ **Б1 побочно:** `parse_timeframe` считает месяц как 30 суток, а год как 365 —
  фиксированные приближения, а не календарь.

### 3. hummingbot — [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot)

Прочитан [candles_base.py](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/data_feed/candles_feed/candles_base.py). Лицензия Apache-2.0.

* **Б2 закрытость — ДЕЛЕГИРУЕТСЯ БИРЖЕ.** У каждого коннектора свой флаг, включает ли
  REST-ответ формирующуюся свечу, и время сдвигается на него:

```python
@property
def _is_last_candle_not_included_in_rest_request(self):
    return False
...
return end_time + self.interval_in_seconds * self._is_last_candle_not_included_in_rest_request
```

  Это ТРЕТИЙ ответ: не «по времени» и не «отбросить последнюю», а «спросить у коннектора,
  что именно отдала биржа».
* **Б4, Б6 дыра — ВЕСЬ РЯД СБРАСЫВАЕТСЯ И КАЧАЕТСЯ ЗАНОВО:**

```python
timestamp_steps = np.unique(np.diff(timestamps))
interval_in_seconds = self.get_seconds_from_interval(self.interval)
if not np.all(timestamp_steps == interval_in_seconds):
    self.logger().warning("Candles are malformed. Restarting...")
    self._reset_candles()
```

  Дыра определяется так же, как у нас — через равенство шага сетке, — а вот ответ
  ЧЕТВЁРТЫЙ: не заполнить и не обойти, а выбросить всё и перекачать.

### 4. nautilus_trader — [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)

Прочитан [aggregation.rs](https://github.com/nautechsystems/nautilus_trader/blob/develop/crates/data/src/aggregation.rs) (ядро агрегатора переписано на Rust).
Лицензия LGPL-3.0.

* **Б3 сетка — ЯКОРЬ ЕСТЬ И ОН НАСТРАИВАЕМЫЙ:** `get_time_bar_start(now, &self.bar_type(),
  self.time_bars_origin_offset)`. Смещение начала сетки задаётся параметром, а не
  зашивается. Это тот же приём, что наш `GRID_ANCHOR_MS`, только вынесенный в настройку.
* **Б1 месяц и год — КАЛЕНДАРНЫЕ, а не приближения:** `add_n_months(start_time, step)` и
  `add_n_years(...)` — в отличие от ccxt, где месяц равен 30 суткам.
* **Б2 частичный бар — ДВА ЯВНЫХ ФЛАГА:** `build_with_no_updates` (строить ли бар, если
  сделок в интервале не было) и `skip_first_non_full_bar` (выбрасывать ли первый неполный
  бар на старте). Пустой интервал у них — самостоятельное решение, а не молчание.

### 5. jesse — [jesse-ai/jesse](https://github.com/jesse-ai/jesse)

Прочитан [candle_service.py](https://github.com/jesse-ai/jesse/blob/master/jesse/services/candle_service.py). Лицензия MIT.

* **Б2 закрытость — ПАРАМЕТР, зависящий от режима:** `accept_forming_candles: bool = False`;
  в живом режиме отдаются только завершённые свечи, в бэктесте формирующаяся добавляется.
  Пятый по счёту способ ответить на этот вопрос.
* **Б3 сетка — СЫРАЯ ЭПОХА, якоря нет:** `generated_candle[0] = candle[0] - (candle[0] % timeframe_ms)`.
  Тот же ответ, что у ccxt, и та же цена: недельный бар поедет на четверг.
* **Б4 дыра — ЗАПОЛНЯЕТСЯ пустой свечой от предыдущей:**
  `_generate_empty_candle_from_previous_candle()`. Семейство freqtrade.
* **Б6** — пропуск ловится сравнением `next_candle_timestamp(...) < now()`, то есть тоже
  по сетке, а не по числу элементов.

### 6. cryptofeed — [bmoscon/cryptofeed](https://github.com/bmoscon/cryptofeed)

Прочитан [binance.py](https://github.com/bmoscon/cryptofeed/blob/master/cryptofeed/exchanges/binance.py). Лицензия XFree86 1.1.

* **Б2 закрытость — ФЛАГ САМОЙ БИРЖИ.** Binance шлёт в свече поле `x` («закрыта»), и
  библиотека читает именно его:

```python
if self.candle_closed_only and not msg['k']['x']:
    return
```

  Шестой способ. От hummingbot отличается тем, что там флаг описывает КОННЕКТОР («включает
  ли REST формирующуюся свечу»), а здесь читается признак КОНКРЕТНОЙ свечи.
* ⚠ Выключенный `candle_closed_only` пропускает незакрытые свечи в расчёт — то есть по
  умолчанию это решение оператора, а не библиотеки.

### 7. trade_aggregation-rs — [MathisWellmann/trade_aggregation-rs](https://github.com/MathisWellmann/trade_aggregation-rs) ★117, Rust

Прочитан [time_rule.rs](https://github.com/MathisWellmann/trade_aggregation-rs/blob/main/src/aggregation_rules/time_rule.rs). Лицензия MIT/Apache-2.0.

* **Б3 сетки НЕТ ВООБЩЕ.** Первая свеча привязывается к ПЕРВОЙ СДЕЛКЕ, дальше граница
  двигается прибавлением периода:

```rust
let should_trigger =
    trade.timestamp() - self.reference_timestamp > self.period_in_units_from_trade;
if should_trigger {
    self.reference_timestamp = self.reference_timestamp + self.period_in_units_from_trade;
}
```

  В комментарии названа причина, по которой не берут время последней сделки: это дало бы
  дрейф. То есть регулярность шага они держат, а привязку к абсолютной сетке — нет.
  **Это ответ, которого нет ни у нас, ни у первых шести:** «бар начинается там, где начались
  данные». Свеча такого агрегатора несравнима со свечой биржи по границам.

### 8. pandas — [pandas-dev/pandas](https://github.com/pandas-dev/pandas)

Не торговая система, а инструмент, на котором строит сетку половина поля. Прочитан
[core/resample.py](https://github.com/pandas-dev/pandas/blob/main/pandas/core/resample.py). Лицензия BSD-3-Clause. Помечен как БИБЛИОТЕКА ОБЩЕГО НАЗНАЧЕНИЯ.

* **Б3 сетка — ЭТО ИМЕННО ПАРАМЕТР, и у него ПЯТЬ значений:** `origin` принимает `'epoch'`
  (начало 1970-01-01), `'start'` (первое значение ряда), `'start_day'` (полночь первого дня),
  `'end'`, `'end_day'` либо произвольную метку времени.
  Наш ответ — это `'epoch'`; ответ trade_aggregation-rs — это `'start'`. Поле не просто знает
  оба, оно их НАЗЫВАЕТ и даёт выбирать.
* ⚠ **Вопрос, которого у нас нет вовсе:** `closed` (какая сторона интервала включена — левая
  или правая) и `label` (какой границей бар подписан). Мы молча берём левую и там, и там —
  `open_ms` это левый край, — но нигде это не записано и не проверяется.

### 9. ohlc-resample — [adiled/ohlc-resample](https://github.com/adiled/ohlc-resample) ★27, TypeScript

Прочитан [src/lib.ts](https://github.com/adiled/ohlc-resample/blob/main/src/lib.ts). Лицензия MIT.

* **Б3 сетка — сырая эпоха:** `timeOpen = candle[OHLCVField.TIME] - candle[OHLCVField.TIME] % newFrame;`
* **Б2 закрытость — параметр, и УМОЛЧАНИЕ ПРОТИВОПОЛОЖНО нашему:**

```typescript
if (includeLatestCandle === false) {
    sortedCandles.pop();
}
```

  По умолчанию `includeLatestCandle` истинно, то есть незакрытая свеча ВКЛЮЧАЕТСЯ. Убирается
  она по позиции (`pop`), а не по времени. Вторая функция того же файла, `resampleOhlcvArray`,
  такой ветки не имеет вовсе — отдаёт всё как есть.

### 10. backtrader — [mementum/backtrader](https://github.com/mementum/backtrader)

Классическая библиотека бэктеста. Прочитан [resamplerfilter.py](https://github.com/mementum/backtrader/blob/master/backtrader/resamplerfilter.py). Лицензия GPL-3.0.

* **Б3 сетка — параметр `bar2edge`**, привязывающий бары к часам (xx:00, xx:05, xx:10), плюс
  `adjbartime`, подменяющий метку последней сделки меткой границы. Выравнивание по сетке —
  выбор, а не данность.
* **Б8 побочно — `rightedge`:** тот же вопрос, что `label` у pandas, — подписывать бар началом
  интервала или концом:

```python
point = point // self.p.compression
point += self.p.rightedge
point *= self.p.compression
```

* **Б2 закрытость — НЕЗАКРЫТЫЙ БАР ОТДАЁТСЯ.** В конце потока `last()` доставляет открытый
  бар, поправив ему время. Седьмой способ ответить.
* **Б1** — для дневных и старше границы СЕССИОННЫЕ, а не сеточные. Крипте это не нужно
  (торговля круглосуточная), но показывает, что «сетка» и «сессия» в поле различаются.

---

## Независимость выборки

Клонов не найдено: десять проектов — это десять самостоятельных организаций и кодовых баз
на четырёх языках (Python, Rust, TypeScript, плюс Rust-ядро внутри Python-проекта).
Совпадающих ядер расчёта нет.

⚠ **Но одна ЗАВИСИМОСТЬ есть, и на неё нельзя закрывать глаза при подсчёте голосов.**
freqtrade берёт сетку у ccxt (`ccxt.Exchange.round_timeframe`) и лишь ДОБАВЛЯЕТ поверх
собственный якорь понедельника для пересемплирования. Значит по вопросу Б3 их ответы — не
два независимых голоса, а полтора: базовое решение одно и то же, расходятся надстройки.
