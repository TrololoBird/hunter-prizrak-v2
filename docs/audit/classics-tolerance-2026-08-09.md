# Внешние источники: допуск "равенства экстремумов" и критерий ложного пробоя

Числа — цитаты из перечисленных книг и страниц (URL/страница у каждой строки таблиц).

```bash
grep -c 'http' docs/audit/classics-tolerance-2026-08-09.md
```


Собрано 2026-08-09 веб-поиском по заданию обзора. НИ ОДНА строка не является требованием
курса — это второй голос по §2 иерархии (курс о числе допуска молчит).

## Вопрос 1 — допуск "равно" для равных вершин/донышек (расширенная дивергенция = двойная вершина/дно по цене)

| # | Источник | Допуск "равенства" вершин | Ссылка |
|---|---|---|---|
| 1 | **Bulkowski, Encyclopedia of Chart Patterns** — Adam & Adam Double Top, гайд идентификации | "The variation between price peaks is small, **usually less than 3%**. The two tops should appear to peak near the same price." | https://thepatternsite.com/aadt.html |
| 2 | Bulkowski — разграничение triple top / head-and-shoulders | средний пик **в пределах 3%** от крайних → triple top; выше на 3–5% → H&S. То же число 3% как граница "одного уровня" | https://thepatternsite.com/MultiPeaks.html |
| 3 | **TradingView built-in** авто-паттерн Double Top (официальная справка) | "two consecutive highs **at the same level (slight difference in values is allowed)**" — числа НЕ публикует; пивоты 5/5 баров; подтверждение — close ниже neckline | https://www.tradingview.com/support/solutions/43000653211-chart-pattern-double-top/ |
| 4 | TradingView, Double Top/Bottom Detector (jobkouen, open source) | параметр **Height Tolerance, default 2.0%** — максимальная разница высот двух пивотов | https://www.tradingview.com/script/iCHneVZT/ |
| 5 | TradingView, Double Top/Bottom Fractals Detector (RezzoRedPriest) | допуск **нормирован ATR** (равенство = в пределах k·ATR), не процентом | https://www.tradingview.com/script/eYVzSimR-Double-Top-Bottom-Fractals-Detector/ |
| 6 | Гайды по double top (Alchemy Markets, AlphaEx и др.) | "Traders usually allow a **2–3% tolerance** between the highs" | https://alchemymarkets.com/education/strategies/double-top-pattern/ |
| 7 | **Edwards & Magee, Technical Analysis of Stock Trends** | числа для равенства вершин НЕ дают ("in the neighborhood of the same level"); их число — **3% как фильтр валидности пробоя** (см. вопрос 2) | https://books.google.com/books/about/Technical_Analysis_of_Stock_Trends.html?id=wklriRw9a1oC |
| 8 | Python: kelvonlys/Double-Top-and-Bottom | "peaks of **approximately the same price**" — численный порог в README не задан | https://github.com/kelvonlys/Double-Top-and-Bottom |
| 9 | Python: tristcoil/detect-double-bottom-in-stocks | порог = процентная полоса над глобальным минимумом для отбора локальных минимумов (параметризуется, фиксированного "равно" нет) | https://github.com/tristcoil/detect-double-bottom-in-stocks |
| 10 | Python-детекторы дивергенций: AKzar1el/rsi-divergence-detector, SpiralDevelopment/RSI-divergence-detector, ciptoj, bkawk, freqtrade RSIDivergence | классы — **только regular и hidden**; категории extended/exaggerated НЕТ ни в одном из просмотренных, допуска "равно" соответственно тоже нет | https://github.com/AKzar1el/rsi-divergence-detector , https://github.com/SpiralDevelopment/RSI-divergence-detector |
| 11 | Описания exaggerated divergence (LiteFinance, XS, NEPSE) | определяют её именно как **равные хаи/лои цены при наклонном индикаторе**, т.е. double top/bottom + дивергенция; свой допуск не дают, отсылают к допуску двойной вершины | https://www.litefinance.org/blog/for-professionals/what-is-divergence-on-forex/ , https://www.xs.com/en/blog/divergence-cheat-sheet/ |

### Сходимость по вопросу 1

**Число есть, и оно сходится: 2–3% разницы между вершинами, верхняя граница 3%.**
- Bulkowski (единственный классик, дающий цифру): **< 3%**;
- открытые детекторы TradingView: **default 2%**;
- популярные гайды: **2–3%**;
- альтернативная школа кодирования — **k·ATR** (масштабно-инвариантно, для крипты уместнее фикс-процента);
- E&M и Schabacker числа не дают ("примерно тот же уровень").

Отдельный факт в пользу текущего состояния factors.py: **ни один из просмотренных питон-детекторов
дивергенций extended-категорию не реализует** — отсутствие числа "равно" в отрасли системное, порог
приходится импортировать из литературы двойной вершины.

**Рекомендация с референтом:** допуск "равенства" пиков — Bulkowski, Encyclopedia of Chart Patterns
(Adam & Adam Double Top, identification guidelines): вариация вершин **менее 3%**; практический
default индустрии 2%. Для крипто-ТФ разумный эквивалент — ATR-нормировка (вариант источника №5),
но ЧИСЛОВОЙ референт в классике один — 3% Булковски. Решение о принятии — за владельцем (§10).

---

## Вопрос 2 — чем классика считает ложный пробой: время возврата или закрытия

| # | Источник | Критерий ложного пробоя | Время или закрытие | Ссылка |
|---|---|---|---|---|
| 1 | **Bulkowski, busted patterns** | пробой = **CLOSE** за границей паттерна; busted = после пробоя цена прошла **< 10%** и закрылась за противоположной границей | **закрытия + величина хода**; счётчика баров нет | https://thepatternsite.com/Busted.html , https://thepatternsite.com/bustedsetup.html , https://thepatternsite.com/BustDoubleTops.html |
| 2 | **Sperandeo, "Trader Vic", правило 2B** (пересказ и тест у Bulkowski) | новый хай выше прежнего, затем цена падает **ниже прежнего хая**; тайминг: minor — возврат **в пределах ~1 дня**, intermediate — 3–5 дней, major — 7–10 дней | **уровень возврата + время** (закрытие не оговорено, уровень интрадейный) | https://thepatternsite.com/2B.html , https://technicalresources.in/trader-vics-2b-patterns/ |
| 3 | **Connors & Raschke, Turtle Soup** | новый 20-дневный лой; прежний 20-дневный лой **старше ≥ 4 сессий**; вход — стоп обратно выше прежнего лоя, **действителен только в тот же день** (Plus One — на следующий) | **время: возврат за уровень в 1 бар** (вариант +1 = 2 бара) | https://oxfordstrat.com/trading-strategies/turtle-soup-plus-2/ , https://www.netpicks.com/turtle-soup-fading-new-20-day-high-or-lows/ |
| 4 | **Wyckoff spring / upthrust** | цена уходит под поддержку (над сопротивление) и **быстро возвращается в диапазон**: "within a few bars", идеал — **та же сессия**; качество = глубина + скорость возврата + объём (низкий на проколе, высокий на возврате) | **время (1 – несколько баров)**, фикс-числа нет | https://tradingwyckoff.com/en/spring-shakeout/ , https://www.tradingview.com/script/Vj3nQB8Y-Smarter-Money-Concepts-Wyckoff-Springs-Upthrusts-PhenLabs/ |
| 5 | **Edwards & Magee** (правило 3%) | пробой валиден, если цена ушла **≥ 3% за уровень на закрытии** (вариация: удержание 3 дня); всё, что меньше/вернулось — не пробой | **закрытие + величина** (у последователей — и время: 3 дня) | https://phantran.net/breakouts-in-technical-analysis/ , https://www.angelone.in/finance-wiki/technicals/three-percent-rule |
| 6 | TradingView "False Breakouts" (vikprimus, кодированный детектор) | три класса: **single-bar** — тень прошла уровень, close вернулся; **two-bar** — close за уровнем, следующая свеча закрылась обратно; **multi-bar** — возврат в пределах Max Hold Bars | **оба: закрытия И счётчик баров**, как градация | https://www.tradingview.com/script/uTcKcZmL-false-breakouts/ |
| 7 | LuxAlgo, библиотека "False Breakout" | признаёт разнобой школ: от "любой прокол тенью с возвратом" до "close за уровнем, затем close внутри"; "closes back inside, **often within a few bars**"; фикс-числа сознательно не даёт | **оба, без числа** | https://www.luxalgo.com/library/concept/false-breakout/ |
| 8 | Python: pytrendline, FabTrader consolidation-box и др. | кодируют возврат как **close обратно в диапазон**; счётчик баров — настраиваемый параметр | **закрытие первично, время — параметр** | https://github.com/ednunezg/pytrendline , https://fabtrader.in/price-consolidation-boxes-identifying-ranges-breakouts-and-retests-using-python/ |

### Сходимость по вопросу 2

Единого критерия в классике НЕТ — есть **два независимых, и оба представлены**:

1. **критерий закрытий** (Bulkowski, Edwards & Magee, single-bar класс детекторов): пробой
   считается по close; "целая свеча за уровнем" ≈ close за уровнем — пока его нет, пробой не
   состоялся. Это в точности **рисунок стр. 6 курса** ("цена не уходит за уровень целыми свечами");
2. **критерий времени возврата** (Turtle Soup: тот же день / +1; Sperandeo 2B minor: ~1 день;
   Wyckoff: та же сессия — несколько баров; two-bar класс детекторов). Это в точности
   **текст стр. 6 курса** ("вернула 1–2 свечами").

Кодированные детекторы (стр. 6 таблицы) не выбирают между ними, а строят **градацию**:
1-барный (тень), 2-барный (закрытие+возврат), N-барный. То есть практика отрасли поддерживает
чтение схемы и текста курса как **двух независимых условий одного семейства**, а не как
конкурирующих формулировок: рисунок задаёт условие по закрытиям, текст — таймер 1–2 свечи, и
самые близкие референты таймера — Turtle Soup (same day / plus one) и 2B (minor: within a day).
