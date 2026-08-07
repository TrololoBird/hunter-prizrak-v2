# Как ПОК считают в чужих проектах — разбор 22 репозиториев на Python (2026-08-06)

Вопрос владельца: найти на GitHub проекты, где считается точка контроля (ПОК), и
разобрать, КАК она у них реализована.

Зачем это нужно именно здесь. У нас ПОК — не украшение графика, а вход в разметку
уровней (`src/hunter/volume_profile.py`, FOUNDATION §2.2). Чужие реализации полезны не
как образец, а как набор РАЗВИЛОК: каждая из них где-то приняла решение, которое мы тоже
принимали, и расхождение показывает, что именно в этом решении неочевидно.

## 1. Охват: что искали и что нашли

Искали три канала: тематические страницы GitHub (volume-profile, market-profile,
order-flow, footprint-chart), поиск по репозиториям и веб-поиск. Поиск ПО КОДУ был
недоступен: `grep.app`, `searchcode.com`, `codeload.github.com` и API GitHub для чужих
репозиториев из этой среды закрыты шлюзом (HTTP 403 на CONNECT). Работал только
`git clone --depth 1`, им и собирали.

```bash
# как воспроизвести сбор (в пустом каталоге)
for r in bfolkens/py-market-profile dws-data/nas-orb-backtester mgi25/volume-profile-bot; do
  git clone --depth 1 -q "https://github.com/$r" "$(echo $r | tr / _)"
done
# классификация: где ПОК ВЫЧИСЛЯЕТСЯ, а где только строится профиль
python - <<'PY'
import re, pathlib
COMPUTE = re.compile(r"(poc|point_of_control)\w*\s*=.*(idxmax|argmax|max)|find_peaks|midmax_idx")
PROFILE = re.compile(r"np\.histogram|np\.linspace|pd\.cut|price_pace|bin_size|row_size|price_step|nBins|num_bins|n_bins")
for d in sorted(p for p in pathlib.Path('.').iterdir() if p.is_dir()):
    t = "".join(f.read_text(encoding="utf-8", errors="replace") for f in d.rglob("*.py"))
    print(d.name, "ПОК" if COMPUTE.search(t) else ("профиль" if PROFILE.search(t) else "нет"))
PY
```

Замер 2026-08-06: попыток клонирования **50**, склонировано и просмотрено **28**
репозиториев. Из них:

* **17** вычисляют ПОК в коде;
* **5** строят профиль объёма, но ПОК не выделяют;
* **6** упоминают ПОК в описании или в Pine-скрипте, кода расчёта на Python нет.

⚠ Машинная сверка выше даёт **16**, а не 17. Семнадцатый — shahabahreini/tdc-charts —
найден чтением: там ПОК присваивается без `argmax`, строкой `poc_index = int(tied[len(tied)//2])`,
и регулярное выражение его не ловит. Число «16» без этой оговорки было бы занижено
свойством прибора, а не свойством выборки.

Итого разобрано **22 проекта с кодом профиля или ПОК**, и они распадаются на пять
семейств по тому, ГДЕ берётся цена и КАК режется сетка.

## 2. Семейство A — весь объём бара кладётся в цену закрытия

Профиль строится как гистограмма `Close`, взвешенная объёмом. Пять проектов:

| проект | сетка | ПОК |
|---|---|---|
| [bfolkens/py-market-profile](https://github.com/bfolkens/py-market-profile) (401★) | шаг `tick_size × prices_per_row`, по умолчанию 0.05 | `midmax_idx` — см. §5 |
| [letianzj/QuantResearch](https://github.com/letianzj/QuantResearch) | `np.arange(min, max, price_pace)` | в ноутбуке |
| [cenobar/TPO](https://github.com/cenobar/TPO) (22★) | `price_pace = 0.25` | в ноутбуке |
| [VanHes1ng/Cryptocurrencies-volume-profile](https://github.com/VanHes1ng/Cryptocurrencies-volume-profile) | `np.linspace(min, max, 100)` | не выделяется |
| [MESKONE0722/mt5-ai-volume-trading-bot](https://github.com/MESKONE0722/mt5-ai-volume-trading-bot) | `np.histogram(close, bins=50)` | `idxmax` ПОСЛЕ сглаживания |

Канонический вид (letianzj, cenobar — код совпадает по смыслу):

```python
price_buckets = np.arange(cmin_int, cmax_int, price_pace)
vol_bars = np.histogram(df.Close, bins=price_buckets, weights=df.Volume)[0]
```

**Что это измеряет.** Не «где торговали», а «где закрывались бары». На часовом ряду весь
часовой объём приписывается одной цене — той, что случилась в последнюю миллисекунду
часа. Это самый популярный подход в выборке и одновременно самый грубый; у нас он
невозможен по построению, потому что гистограмма набирается из СДЕЛОК (§5), а не из
баров.

## 3. Семейство B — объём размазывается по диапазону бара

Восемь проектов. Объём бара делится между бинами, которые задевает диапазон `high–low`.

```python
# dws-data/nas-orb-backtester — равномерно, число строк фиксировано
bucket_size = (high_ceil - low_floor) / num_rows          # num_rows = 24
lo_idx = int(np.floor((lows[i]  - low_floor) / bucket_size))
hi_idx = int(np.floor((highs[i] - low_floor) / bucket_size))
volume_arr[lo_idx:hi_idx + 1] += volumes[i] / (hi_idx - lo_idx + 1)
```

В его же документации сказано прямо: «Volume is distributed evenly across price buckets
within each bar's high/low range. This is an approximation — tick data would be more
accurate», и число строк 24 выбрано, чтобы совпасть с TradingView Fixed Range.

Та же идея у [e41c/forex-volume-profile](https://github.com/e41c/forex-volume-profile)
(200 бинов, «proven-best resolution»), [Mmadrb/VPOFA](https://github.com/Mmadrb/Volume-Profile-Order-Flow-Analysis-VPOFA)
(`searchsorted` по краям бинов), [mahmoud20138/IFC-Trading-System](https://github.com/mahmoud20138/IFC-Trading-System)
(`np.linspace(min, max, num_bins+1)`), [pedrobraiti/volume-profile-trading](https://github.com/pedrobraiti/volume-profile-trading)
(`n_bins=50`), [berkant1863-netizen/automated-trading-system](https://github.com/berkant1863-netizen/automated-trading-system)
(`np.linspace(low, high, n_bins)` на каждый бар), [zyairemiller](https://github.com/zyairemiller/Multi-dimensional-support-and-resistance-resonance-analyzer---Data-source-okx)
(доля пересечения диапазона с бином), [goldscanco/volprofile](https://github.com/goldscanco/volprofile)
(`getVPWithOHLC` с множителем точности), [fatfingererr/chip-whisperer](https://github.com/fatfingererr/chip-whisperer)
(`price_step = price_range / price_levels`).

**Развилка, которую они все проходят молча:** равномерное размазывание предполагает, что
внутри бара цена провела одинаковое время на каждом уровне. Это неверно для любого бара с
телом, и ошибка тем больше, чем крупнее таймфрейм. Проверить это утверждение внутри их
кода нечем — тиков там нет.

## 4. Семейство C — по сделкам и тикам

Пять проектов, и это единственное семейство, сравнимое с нашим по исходным данным.

| проект | цена сделки → уровень | сетка |
|---|---|---|
| [mahmoud20138/OrderFlow-Scalper](https://github.com/mahmoud20138/OrderFlow-Scalper) | `(trades["last"] / tick_size).round() * tick_size` | шаг тика инструмента из MT5 |
| [mgi25/volume-profile-bot](https://github.com/mgi25/volume-profile-bot) | `(bid+ask)/2`, округление к `PRICE_STEP` | константа в коде |
| [srlcarlg/srl-python-indicators](https://github.com/srlcarlg/srl-python-indicators) (48★) | сегменты высотой `row_height` | параметр |
| [shahabahreini/tdc-charts](https://github.com/shahabahreini/tdc-charts) | время-в-цене, `np.histogram` с весами | `np.linspace(low, high, nbins+1)` |
| [Gregos5/Volume_profile_bybit](https://github.com/Gregos5/Volume_profile_bybit) | средняя цена бара `df.avg` | `price_pace = 1` |

Округление к шагу тика (первые два) — то же решение, что у нас: сетка привязана к
инструменту, а не к числу бинов. Разница в том, что `tick_size` они берут у брокера
(MT5), а мы — из спецификации инструмента биржи (§5, §10.2).

## 5. Семейство D — ядерная оценка плотности вместо гистограммы

Два проекта — [gregyjames/VolumeProfiles](https://github.com/gregyjames/VolumeProfiles) и
[GrantWise/VolumeProfileAnalysis](https://github.com/GrantWise/VolumeProfileAnalysis) —
сеток не строят вовсе:

```python
kde = stats.gaussian_kde(close, weights=volume, bw_method=kde_factor)  # 0.05 и 0.07
kdy = kde(xr)
peaks, _ = signal.find_peaks(kdy)
```

Вместо одного ПОК получается НАБОР пиков сглаженной плотности. Число пиков задаётся
шириной ядра `bw_method`, и она в обоих проектах подобрана на глаз (0.05 против 0.07 при
прочих равных). Это честный способ искать HVN, но ПОК он не даёт: «главный» пик здесь —
свойство сглаживания.

## 6. Пять развилок, на которых проекты расходятся

### 6.1. Ничья при выборе максимума

Три разных ответа на один вопрос — что делать, если несколько бинов имеют равный объём:

```python
# bfolkens: из всех максимумов берётся БЛИЖАЙШИЙ К СЕРЕДИНЕ профиля
maxima_idxs = np.argwhere(array == np.amax(array))[:, 0]
midpoint = len(array) / 2
maximum_idx = np.argmin(v_norm(maxima_idxs - midpoint))

# shahabahreini/tdc-charts: СРЕДНИЙ из связанных
poc_index = int(tied[len(tied) // 2])

# все остальные: np.argmax / .idxmax() — молча первый по индексу
```

У нас — четвёртый ответ, и он единственный, который не выбирает:
`point_of_control` возвращает `NotReady` с перечислением спорных цен
(`src/hunter/volume_profile.py`). Обоснование то же, что и везде в проекте: молча
выбранный первый бин неотличим от честно найденного.

### 6.2. Величина области стоимости

Восемнадцать проектов из тех, где область считается, берут 0.70. Исключение —
tdc-charts: `value_area_ratio: float = 0.68`, без ссылки на источник. Ещё одно —
pedrobraiti: 0.70 и 0.80 как перебираемый параметр оптимизации.

### 6.3. Процедура расширения области — три разных определения

```python
# (а) по одному уровню, сравнивая соседей — bfolkens, dws-data, Mmadrb, e41c, chip-whisperer
if can_go_up and (not can_go_down or next_up >= next_down):
    upper += 1; accumulated += next_up
else:
    lower -= 1; accumulated += next_down

# (б) СОРТИРОВКА ПО УБЫВАНИЮ ОБЪЁМА — mgi25
for p, v in vp.sort_values(ascending=False).items():
    cum_volume += v; va_bins.append(p)
    if cum_volume >= 0.7 * total_volume: break
val, vah = min(va_bins), max(va_bins)

# (в) перебор непрерывных отрезков с выбором ближайшего к якорю — tdc-charts
```

Вариант (б) — не «другая реализация», а другое ОПРЕДЕЛЕНИЕ: набираются самые объёмные
бины, где бы они ни лежали, поэтому набор может быть несмежным, а `min`/`max` по нему
дают границы шире фактической области. При двугорбом профиле (а) и (б) расходятся не на
округление, а на структуру.

У нас реализованы оба канонических варианта (`Expansion.SINGLE` и `Expansion.PAIRS`),
расхождение замерено и записано: `docs/audit/value-area-2026-08-03.md`.

### 6.4. Число бинов против шага цены

Тринадцать проектов из 22 задают ФИКСИРОВАННОЕ ЧИСЛО бинов: 20 (goldscanco), 24
(dws-data), 50 (Mmadrb, pedrobraiti, berkant, zyairemiller, MESKONE), 99–100 (VanHes1ng),
150 (gregyjames, GrantWise), 200 (e41c). При одних и тех же данных ПОК у них будет
разным просто потому, что параметр разный.

В нашем модуле это запрещено явной строкой:
«Бины привязаны к `tickSize` инструмента: фиксированное число бинов не используется — при
разном числе бинов один и тот же ПОК получается разным».

Обзор эту строку подтверждает эмпирически: разброс параметра у 13 проектов — от 20 до
200, то есть в десять раз, и ни один из них не обосновывает своё число замером. Самое
близкое к обоснованию — комментарий e41c «200 — the proven-best resolution» и dws-data
«matching TradingView Fixed Range with Row Size = 24»: первое ссылается на неопубликованную
проверку, второе — на чужое умолчание.

### 6.5. Сглаживание перед поиском максимума

MESKONE0722 делает две вещи, которых больше нет ни у кого:

```python
weights = [decay ** i for i in range(len(subset))]      # decay = 0.95, свежесть важнее
subset['weighted_vol'] = subset[vol_col] * weights
hist, bin_edges = np.histogram(subset['close'], bins=50, weights=subset['weighted_vol'])
hist_smooth = pd.Series(hist).rolling(window=3, center=True, min_periods=1).mean()
self.poc = self.profile_data.loc[self.profile_data['vol'].idxmax(), 'price']
```

После скользящего среднего по трём бинам ПОК стоит не там, где максимум объёма, а там,
где максимум сглаженной кривой. Название при этом сохраняется прежнее.

## 7. Единственный проект с контролем случайностью

Из 22 только [pedrobraiti/volume-profile-trading](https://github.com/pedrobraiti/volume-profile-trading)
проверяет, что сигнал не лучше случайного. В его отчёте перечислены четыре теста:
«(1) volume permutation; (2) price-only ablation; (3) random-entry control; (4) excess
return over exposure and risk-free», и вывод по одному из активов сформулирован как «real
PF» против распределения перемешанного объёма.

Это ровно тот контроль, который CLAUDE.md требует для всякого «совпало N из M», и который
у нас уже один раз спас от ложного вывода (решётка произвольных цен сходилась не хуже
разметки автора). Двадцать один проект из двадцати двух его не делает — то есть публикует
числа, про которые неизвестно, отличимы ли они от шума.

## 8. Что из обзора стоит взять, а что не стоит

**Взять к сведению — одно.** Тай-брейк `midmax_idx` (bfolkens) и «средний из связанных»
(tdc-charts) показывают, что ничья на реальных данных встречается достаточно часто, чтобы
её отдельно кодировали два независимых автора. Наш `NotReady` от этого не отменяется, но
частоту ничьих стоит замерить на своей вселенной: если она велика, «неоднозначно» будет
приходить чаще, чем полезно.

**Не брать — четыре.** Фиксированное число бинов; объём бара в цену закрытия; равномерное
размазывание по диапазону как основной способ; сглаживание профиля перед `argmax`. Каждое
из них делает ПОК свойством параметра, а не рынка, и ни одно не сопровождается замером у
авторов.

**Отдельно: округление вверх.** У bfolkens уровень получается как
`math.ceil(x * roundoff) / roundoff` — цена всегда поднимается к верхней границе строки.
Систематический сдвиг на половину шага сетки, одинаковый для всех уровней; на профиле он
не виден, а в сравнении с чужой разметкой даст постоянную ошибку.

## 9. Границы этого обзора

* Поиск ПО КОДУ был недоступен (шлюз), поэтому выборка собрана по названиям и описаниям
  репозиториев. Проекты, где ПОК считается, но в описании не назван, в неё не попали.
  Это смещение в сторону «профильных» проектов, и оно не оценено.
* Крупные библиотеки в выборку не вошли по внешней причине: `twopirllc/pandas-ta`
  (в нём есть индикатор `vp`) из этой среды не клонируется — GitHub отвечает запросом
  учётных данных, то есть репозитория по этому пути больше нет.
* Разобран КОД, а не поведение: ни один из 22 проектов здесь не запускался, числа их
  профилей не сравнивались между собой. Утверждения вида «ПОК будет разным при разном
  числе бинов» опираются на наш собственный замер, записанный в модуле, а не на прогон
  чужого кода.
