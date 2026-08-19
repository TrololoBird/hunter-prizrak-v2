# Сверка индикаторов с первоисточниками и TradingView — 2026-08-09

Числа TA-Lib-сверок взяты из прежних протоколов (команды — там); правка средней линии
Боллинджера предъявлена диффом повтора:

```bash
uv run python -m hunter replay --run-id last --diff
```


Настройки курса (со скриншотов TV): RSI 70/50/30; MACD; Bollinger (сжатие как предиктор
выхода); MA и EMA обе 200, источник close, отступ 0, "Сглаживание SMA 5", интервал
"График", Gaps вкл. Дивергенции: классическая / скрытая / расширенная (стр. 65).

Наш код: `src/hunter/indicators.py` (обёртки над `polars_talib` = TA-Lib; MACD собран из
EMA), `src/hunter/factors.py` (дивергенции, сжатие полос, MA-touch),
`src/hunter/card.py:340-361` (потребление). Уже сверено раньше:
`docs/audit/wilder-reference-2026-08-03.md` (ATR и RSI против формулы Уайлдера — 0 и
4.3e-16), `docs/audit/macd-talib-inconsistency-2026-08-03.md` (macd() TA-Lib противоречит
его же EMA; наш MACD = разность EMA, сверен с формулой до 8.3e-12).

## Сводная таблица

| индикатор | наша реализация | источники | вердикт | что менять |
|---|---|---|---|---|
| **RSI(14)** | `indicators.py:55-56` → `plta.rsi(close, 14)`. TA-Lib: RMA/SMMA Уайлдера, затравка — простое среднее первых 14 приростов; сверено с формулой до 4.3e-16 (аудит 2026-08-03) | 1) Wilder, "New Concepts in Technical Trading Systems", 1978 (RSI, гл. 6; изложение — Wikipedia); 2) https://en.wikipedia.org/wiki/Relative_strength_index — "newval = (prevval·(n−1)+newdata)/n", затравка "simple moving average using the first n values", уровни 70/30, середина 50; 3) TradingView Pine `ta.rsi` — считается через `ta.rma` (альфа 1/length, затравка `ta.sma`): https://pineify.app/pine-script-ta-ema (семейство), https://rsimonitor.com/articles/wilder-smoothing; 4) TA-Lib `ta_RSI.c` (default compatibility = Metastock/Wilder, SMA-затравка); 5) pandas-ta: mamode по умолчанию 'rma' "to match TA-Lib" — https://github.com/xgboosted/pandas-ta-classic/blob/main/CHANGELOG.md, https://tradingstrategy.ai/docs/api/technical-analysis/overlap/help/pandas_ta.overlap.rma.html | **СОВПАДАЕТ** (формула, сглаживание, затравка = Wilder = TV) | Формулу не трогать. ⚠ Пробел потребления: уровни 70/50/30 из настроек курса нигде не читаются — проект берёт RSI только для дивергенций. Решение о факторе "RSI у уровня" — за курсом/владельцем, не за кодом |
| **MACD (линия)** | `indicators.py:75-77` `macd_line = ema(12) − ema(26)` из EMA TA-Lib (затравка SMA). Сигнальная линия удалена намеренно (§2.9 пересечений не называет) | 1) Appel G., "Technical Analysis: Power Tools for Active Investors", 2005, гл. 8 (создатель MACD); 2) https://en.wikipedia.org/wiki/MACD — "MACD line = EMA₁₂ − EMA₂₆; Signal line = EMA₉(MACD line)", гистограмма — Aspray; 3) StockCharts ChartSchool https://chartschool.stockcharts.com/...macd-moving-average-convergence-divergence-oscillator — "MACD Line: (12-day EMA − 26-day EMA)", затравка EMA — SMA; 4) TradingView `ta.macd` = разность `ta.ema(12/26)`; 5) TA-Lib `ta_MACD.c` — противоречит собственным EMA на первых ~160 барах (наш замер, 466 точек, худшее 5.9e-2) | **СОВПАДАЕТ с определением**; известное и задокументированное расхождение с `macd()` TA-Lib на первых ~160 барах — в нашу пользу. С TV расходится только затравкой EMA (см. строку EMA) | Ничего по формуле. Расхождение затравки — см. EMA |
| **Bollinger: верх/низ** | `indicators.py:91-98` `plta.bbands(close, 20, 2, 2)` → SMA20 ± 2σ, σ населения (делитель n, ddof=0 — TA-Lib `ta_STDDEV.c`) | 1) Bollinger J., "Bollinger on Bollinger Bands", 2001, гл. 7 (конструкция), гл. 9 "The Squeeze"; 2) https://en.wikipedia.org/wiki/Bollinger_Bands — "MA ± Kσ", N=20, K=2, "the proper divisor for the sigma calculation is n, not n−1" (population); 3) StockCharts https://chartschool.stockcharts.com/...bollinger-bands — средняя = 20-периодная SMA, "A simple moving average is used because the standard deviation formula also uses a simple moving average"; 4) TradingView `ta.bb` — basis `ta.sma`, отклонение `ta.stdev(…, biased=true)` (population); 5) https://www.bollingerbands.com (авторский сайт) | **СОВПАДАЕТ** (период, множитель, ddof=0) | — |
| **Bollinger: средняя линия для сжатия** | `card.py:350-352`: ширина полос считается как (upper − lower)/**EMA(20)**·100 — в `factors.squeeze` третьим аргументом передана `indicators.ema(20)` | Все пять источников выше единогласны: средняя линия — **SMA20**, и у Bollinger это принципиально (цитата StockCharts выше). TA-Lib `bbands` сам отдаёт `middleband` = SMA20 — struct уже содержит нужное поле | **РАСХОДИТСЯ** — единственная арифметическая ошибка сверки. EMA20 ≠ SMA20; ширина в % искажается на каждом баре (знаменатель другой), перцентиль истории — вслед за ней | В `card.py:352` передавать SMA20: либо `indicators.sma(20)`, либо добить в indicators.py `bbands_middle()` = `.struct.field("middleband")`. Правка расчёта → предъявлять диффом повтора (§10.6) |
| **EMA(200), EMA(12/26/20)** | `indicators.py:59-60` `plta.ema`. TA-Lib: затравка = SMA первых N, значение существует с бара N−1 (подтверждено чтением исходника) | 1) TA-Lib `ta_EMA.c` — "Use a simple MA of the first 'period'. This is the approach most widely documented" (https://raw.githubusercontent.com/TA-Lib/ta-lib/main/src/ta_func/ta_EMA.c); 2) TradingView, эталонный код `ta.ema`: `sum := na(sum[1]) ? src : alpha*src + (1−alpha)*nz(sum[1])`, alpha=2/(length+1) — затравка **первым значением источника**, не SMA (https://pineify.app/pine-script-ta-ema, https://www.tradingcode.net/tradingview/exponential-moving-average/); 3) StockCharts — затравка SMA ("a simple moving average is used as the previous period's EMA in the first calculation"); 4) Wikipedia EMA (обе конвенции описаны как допустимые); 5) pandas-ta `ema` — параметр `sma=True` по умолчанию (SMA-затравка, как TA-Lib) | **Формула совпадает; ЗАТРАВКА РАСХОДИТСЯ С TV.** TV сеет первым close и рисует EMA с бара 0; мы (TA-Lib/StockCharts) сеем SMA(N) и начинаем с бара N−1. Разница затухает как (1−α)^t: для EMA12/26 нуль к ~100-150 барам, для **EMA200** вес чужой затравки ещё ~5% через 300 баров после старта ряда | Это НЕ ошибка формулы — конвенция StockCharts/TA-Lib законна и уже сверена гейтом. Но карта "как в TV" требует решения владельца: чью затравку считать референтом для EMA200 на коротких хвостах. Минимум — зафиксировать число расхождения замером на срезе |
| **MA(200) простая** | `indicators.py:63-72` `plta.sma(close, 200)`; в карточке считаются ОБЕ (`card.py:357-361`) | 1) TradingView Moving Averages https://www.tradingview.com/support/solutions/43000502589-moving-averages/; 2) Wikipedia Moving average (SMA — среднее арифметическое N close); 3) TA-Lib `ta_SMA.c`; 4) StockCharts SMA; 5) скриншот курса стр. 69 (обе длиной 200, источник close, отступ 0) | **СОВПАДАЕТ.** Прежний пробел "MA200 не считается вовсе" ЗАКРЫТ 2026-08-07 (находка М-13) — сверка это подтвердила | — |
| **"Сглаживание SMA 5" у MA/EMA** | Не реализовано нигде | TradingView, "I see a "Smoothing" section in an indicator's settings" https://www.tradingview.com/support/solutions/43000742042-...: секция Smoothing добавляет ВТОРУЮ линию — MA от линии индикатора (тип/длина настраиваются); это отдельный график поверх, а не изменение самой MA/EMA | **ПРОБЕЛ-КАНДИДАТ.** На скриншоте курса настройка заполнена (SMA, 5). Семантика: SMA(5) от MA200/EMA200 — ещё одна линия, которая тоже может "совпасть с точкой входа" | Сначала выяснить по PDF, ВКЛЮЧЕНА ли галка Smoothing на скриншоте (заполненные поля ≠ включённая линия — у TV поля показываются и при выключенной). Если включена — это третья/четвёртая скользящая фактора стр. 69; решение за владельцем |
| **"Интервал: График", "Gaps: вкл"** | У нас индикатор всегда считается на баре своего ТФ; дыры данных отсекает `continuous_tail` (`card.py:324-334`) | TradingView: "Timeframe and Gaps options" https://www.tradingview.com/support/solutions/43000591555-... и https://www.tradingview.com/support/solutions/43000675999-gaps/; https://www.tradingcode.net/tradingview/time-frame-gaps-setting/ — Gaps действует ТОЛЬКО когда таймфрейм индикатора отличается от графика: вкл = na в незакрытых точках, выкл = протяжка последнего значения (fixnan) | **СОВПАДАЕТ / НЕ ПРИМЕНИМО.** При "Интервал: График" галка Gaps ни на что не влияет — курс держит дефолт. Наше поведение (не считать через дыру, не протягивать значение) строже и совместимо с "Gaps вкл" по духу | — |
| **Дивергенции: классическая** | `factors.py:117-163`: див. (хаи цены ↑, индикатор ↓) и конв. (лои ↓, индикатор ↑) на последней паре свингов, по RSI и линии MACD | 1) курс стр. 65 (дословно процитирован в модуле); 2) https://fxopen.com/blog/en/what-is-the-difference-between-regular-and-hidden-divergence/; 3) https://www.kraken.com/learn/rsi-divergences-what-they-how-they-work; 4) https://alchemymarkets.com/education/indicators/the-rsi-divergence-explained/; 5) https://www.xs.com/en/blog/divergence-cheat-sheet/ — regular bearish: HH цены + LH индикатора; regular bullish: LL цены + HL индикатора | **СОВПАДАЕТ** со стандартной классификацией и с курсом | — |
| **Дивергенции: скрытые** | `factors.py:62-75, 151-154`: hidden bearish = LH цены + HH индикатора (по хаям); hidden bullish = HL цены + LL индикатора (по лоям) | Те же 2-5 плюс https://stockstotrade.com/rsi-hidden-divergence/, https://www.stockgro.club/blogs/trading/rsi-divergence/ — hidden bullish: price higher low + indicator lower low; hidden bearish: price lower high + indicator higher high; сигнал ПРОДОЛЖЕНИЯ тренда | **СОВПАДАЕТ** (геометрия зеркальна классической, стороны выбраны верно: медвежья по хаям, бычья по лоям) | — |
| **Дивергенции: расширенные** | НЕ реализованы — решение, названное вслух (`factors.py:128-131`): "равные экстремумы" требуют допуска на равенство, курс его не даёт | 1) курс стр. 65 (рисунок: три семейства); 2) https://www.litefinance.org/blog/for-professionals/what-is-divergence-on-forex/ — extended/расширенная: равные хаи/лои цены (двойная вершина/дно) при расходящемся индикаторе, сигнал продолжения, типична для боковика; 3) https://www.xs.com/en/blog/divergence-cheat-sheet/ (exaggerated divergence — equal highs + lower indicator highs); 4) https://www.strike.money/technical-analysis/divergence; 5) https://alchemymarkets.com/education/strategies/double-top-pattern/ (равные вершины + LH индикатора) | **ПРОБЕЛ, задокументированный.** Внешние источники подтверждают: семейство существует и определяется через РАВНЫЕ экстремумы цены — то есть без допуска "равно" его не построить. Курс числа не даёт; внешние источники — тоже (все описывают качественно) | По иерархии источников: курс называет семейство ⇒ оно должно быть; допуск на равенство — величина, референта у которой нет ни в PDF, ни в классике. Кандидат на решение владельца (§10); при введении — контроль фальсифицируемости на решётке сдвигов |

## Отдельно: где чужая затравка EMA видна владельцу

Ряд карточки — "непрерывный хвост". Наши EMA200/MA200 существуют с 200-го бара хвоста и
с этого бара **точны по конвенции TA-Lib/StockCharts**. TV на том же хвосте нарисует
EMA200 с первого бара, но с примесью затравки-первого-close (через 300 баров ~5% веса).
Значит "EMA200 в X% от цены" в карточке и на экране TV у владельца — два разных числа,
пока ряд короток. Это надо либо принять и записать, либо пересеять по TV — но не молчать.

## Что осталось непроверенным

- Официальная страница Pine-reference недоступна WebFetch (отдаёт только шапку);
  эталонный код `ta.ema`/`ta.stdev(biased=true)` взят из двух вторичных изложений
  (pineify.app, tradingcode.net), согласных между собой. Живой замер против TV не делался.
- Книги Wilder 1978, Appel 2005, Bollinger 2001 в проекте нет — формулы взяты из
  вторичных изложений (Wikipedia, StockCharts), что уже оговорено в аудите 2026-08-03.
- Сигнальная линия и гистограмма MACD не сверялись (в проекте намеренно отсутствуют).
- Включена ли галка Smoothing на скриншоте стр. 69 — надо смотреть сам PDF-рисунок.


## Изолированный дифф правки SMA20 (дополнено в тот же день)

Первый предъявленный дифф повтора был СМЕШАННЫМ: последний сохранённый прогон старше
правок обзоров, и в диффе рядом с полосами стояли чужие изменения. Изолирование сделано
двумя реплеями по одним кадрам — с правкой и с временно откаченной правкой — и диффом
между ними:

```bash
uv run python -m hunter replay --run-id last --diff > with.txt
# card.py: bbands_middle() временно возвращён к ema(20)
uv run python -m hunter replay --run-id last --diff > without.txt
diff without.txt with.txt
```

Результат: расходятся ТОЛЬКО строки "полосы X% (уже Y% истории)" — ни одна другая строка
карточки не изменилась. Величина: на 4ч сотые доли (2.41→2.42), на 1Д десятые
(6.65→6.59), на 1Н до 7 п.п. (138.39→131.25; 46.07→46.62) — чем длиннее ТФ, тем дальше
EMA20 от SMA20. Перцентиль истории сдвигается вслед за шириной (93%→89% в худшем случае).
