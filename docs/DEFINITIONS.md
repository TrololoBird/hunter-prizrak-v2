# ОПРЕДЕЛЕНИЯ — реестр всех понятий проекта с источниками

Собрано 2026-08-11 по прямому требованию владельца: на каждое понятие, которым
пользуется система, — определение из мини-курса, из корпуса разборов и из внешних
источников, и рядом то, что делает КОД.

## Как читать этот файл

Каждая статья имеет одинаковые поля:

| поле | что означает |
|---|---|
| **Курс** | дословная цитата страницы. Если поля нет — **в курсе этого термина нет вовсе**, и это сказано прямо |
| **Корпус** | как автор употребляет слово на живом разборе (`research/prizrak_corpus/`) |
| **Внешние** | ссылки, найденные веб-поиском 2026-08-11; рядом — на чём источники сходятся |
| **В коде** | что реально исполняется, с файлом |
| **Статус** | одно из пяти, см. ниже |

Пять значений статуса, и они не синонимы:

* **СОВПАДАЕТ** — курс, внешние источники и код говорят одно и то же;
* **КУРС ЖЁСТЧЕ** / **КУРС МЯГЧЕ** — код следует курсу, а классика требует иного. Курс
  главнее по §0 FOUNDATION, но расхождение обязано быть видимым;
* **ВЫБОР НЕ АРБИТРИРОВАН** — источник допускает два прочтения и сам не выбирает.
  Выбрал код. Это самое опасное поле в файле: именно здесь живут ошибки, которые
  выглядят как решения;
* **НАШЕ ДОПУЩЕНИЕ** — числа или правила нет ни в курсе, ни в классике; придумали мы;
* **НЕ РЕАЛИЗОВАНО** — термин есть, механизма нет.

⚠ **Дословные цитаты стоят в «ёлочках» и проверяются машиной.** `gates/course_citations.py`
открывает PDF и сверяет каждую цитату рядом со ссылкой `стр. N` с текстом этой страницы
посимвольно. Кавычки вида "…" — мой пересказ, а не источник.

⚠ **Текстовый слой PDF — не весь курс.** Часть правил выражена ТОЛЬКО рисунком
(нумерация точек границ, лимитный барьер, ширина зоны). Там, где так, это названо.

## Из чего состоит файл

| раздел | о чём |
|---|---|
| §1 | словарь трейдера — 24 понятия, которым курс сам отвёл страницы 3–16 |
| §2 | структура, уровень, профиль — ядро методики, 20 статей |
| §3 | геометрия сделки: вход, стоп, цель, частичная фиксация |
| §4 | индикаторы — только доп-факторы, и ATR, которого в курсе нет |
| §5 | данные: бар, сделка, шаг цены, инструмент |
| §6 | инженерные понятия проекта: гейт, дифф повтора, контроль, исход |
| §7 | **сводная таблица мест, где расчёт стоит на нашем выборе, а не на источнике** |
| §8 | что осталось непроверенным |
| §9 | термины АВТОРА, которых в курсе нет — 11 статей (дополнение 2026-08-11) |
| §10 | понятия, введённые самим проектом — 21 строка |
| §11 | транспорт: три источника сделок и границы каждого (дополнение 2026-08-11) |
| §12 | указатель |

Если читать один раздел — читайте **§7**: там перечислено то, что может оказаться неверным
и чего никто, кроме машины, не поймает.

---

# §1. Словарь трейдера — базовые понятия

Курс отводит словарю страницы 3–16. Это его собственный раздел, и почти всё
здесь совпадает с классикой; расхождения отмечены.

### 1.1. Хай / Лой

**Курс:** «Хай - максимальная цена в определенный промежуток времени» и «Лой - нижнее
значение цены в определенный промежуток времени» (стр. 3).

**Внешние (10):** [StockCharts: Introduction to Candlesticks](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/introduction-to-candlesticks) ·
[TradingView: анатомия свечи](https://my.tradingview.com/chart/EURUSD/E8LohJiU-A-CANDLESTICK-TUTORIAL) ·
[Anatomy of a Candle](https://trading-charts.com/learn/anatomy-of-a-candle) ·
[FinWiz: Candlestick Anatomy](https://finwiz.io/chart-patterns/candlestick-anatomy) ·
[Forex For Starters](https://forexforstarters.com/learn/candlesticks/anatomy-body-wicks/) ·
[Domo: Candlestick Chart Guide](https://www.domo.com/learn/charts/candlestick-charts) ·
[JournalPlus: Wick](https://journalplus.co/learn/glossary/wick/) ·
[DayTradingToolkit](https://daytradingtoolkit.com/beginners-guide/introduction-candlestick-charts-reading-price) ·
[MetaTrader 5: Fractals](https://www.metatrader5.com/en/terminal/help/indicators/bw_indicators/fractals) ·
[TradingView: Williams Fractal](https://www.tradingview.com/support/solutions/43000591663-williams-fractal/)

**Консенсус:** «high» бара — верх диапазона за период бара; «swing high» — локальный
максимум, и его определение требует ОКНА (сколько баров слева и справа). Это две разные
величины, и классика их не путает.

**В коде:** `src/hunter/models.py` — `Bar.high/low` (первое значение);
`src/hunter/swings.py` — фрактал Билла Вильямса на 5 барах (второе).

**Статус:** СОВПАДАЕТ для бара. Для свинга — см. 2.9.

⚠ **Поправка к нашему же документу.** `docs/FOUNDATION.md` §2.5 утверждает, что
определения хая и лоя курс "не даёт вовсе". Это неточно: стр. 3 определение даёт, оно
просто не операционное — «в определенный промежуток времени», а промежуток не назван.
Верная формулировка: курс определяет хай бара и НЕ определяет окно свинга.

### 1.2. ATH / ATL

**Курс:** «ATH – Average Time High – максимум цены за всю историю» (стр. 3).

**Внешние:** общепринятая расшифровка — All-Time High / All-Time Low.

**Статус:** **курс ошибается в расшифровке**, но не в смысле: описание «максимум цены за
всю историю» верное. В код расшифровка не переносится (проверено `gates/sources.py`).

### 1.3. Лонг / Шорт

**Курс:** «Лонг - покупка с целью продать выше (зарабатываем на росте цены)» и
«Шорт - продажа с целью купить ниже (зарабатываем на падении цены)» (стр. 4).

**В коде:** `LevelSide.LONG/SHORT` в `src/hunter/levels.py`. Сторона уровня, не позиции:
лонговый уровень — тот, от которого ждут покупки.

**Статус:** СОВПАДАЕТ.

### 1.4. Стоп (стоп-лосс)

**Курс:** «Стоп - отложенная заявка, которая выставляется трейдером с целью ограничить
убыток в случае движения цены в противоположном открытой сделке направлении» (стр. 4).

**Внешние (8):** [TradeZella: Stop Loss Strategies](https://www.tradezella.com/blog/stop-loss-strategies) ·
[Traders Second Brain: ATR vs Structure vs Percentage](https://traderssecondbrain.com/guides/stop-loss-placement-methods) ·
[LuxAlgo: 5 ATR Stop-Loss Strategies](https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/) ·
[The Bull: Stop-Loss Placement](https://thebull.com.au/trading-guides/stop-loss-placement-guide/) ·
[BTCC: Perfect Stop-Loss Formula](https://www.btcc.com/en-US/academy/crypto-trading/trading-guide/how-to-calculate-a-perfect-stop-loss-a-3-step-formula) ·
[JournalPlus: Stop Loss Guide](https://journalplus.co/learn/guides/stop-loss-strategy-guide/) ·
[Trading Strategies Academy: Pine Script](https://trading-strategies.academy/archives/2844) ·
[TradingView: ATR For Stop Loss](https://jp.tradingview.com/script/2FddLSoL-ATR-For-Stop-Loss-Overlay)

**Консенсус:** стоп ставится ЗА точкой инвалидации (свинг-лой для лонга) плюс буфер, и
буфер классика меряет в **ATR** (0.3–0.5 ATR) или в тиках, а НЕ в процентах цены.

**В коде:** `src/hunter/geometry.py` — `Setup.stop`, `StopBasis`.

**Статус:** **КУРС РАСХОДИТСЯ С КЛАССИКОЙ В ЕДИНИЦЕ.** Курс задаёт запас в процентах цены
(стр. 33), классика — в долях ATR. Подробно — 3.4.

### 1.5. ТВХ (точка входа)

**Курс:** «ТВХ – Точка Входа в позицию – то есть цена открытия сделки» (стр. 4).

**В коде:** `Setup.entry` = ПОК уровня (`src/hunter/geometry.py`).

**Статус:** СОВПАДАЕТ.

### 1.6. Тейк-профит

**Курс:** «Тейк-профит (ТП) - это отложенная заявка, которая выставляется заранее, чтобы
в случае роста рынка зафиксировать прибыль по активу» (стр. 4).

**В коде:** `geometry.Target`, роли `PRIMARY` / `INTERMEDIATE`.

**Статус:** СОВПАДАЕТ.

### 1.7. Сетап

**Курс:** «Сетап - рабочий сценарий движения цены, содержащий ТВХ, Стоп и Тейки» (стр. 4).

**В коде:** `geometry.Setup` — ровно эти три поля плюс обоснование стопа.

**Статус:** СОВПАДАЕТ. Редкий случай, когда структура данных повторяет определение
дословно.

### 1.8. МТФ / СТФ

**Курс:** «МТФ/СТФ - младший таймфрейм / старший таймфрейм» (стр. 4, повтор на стр. 17).

**В коде:** `geometry.TF_ORDER`, `levels._tf_rank`, `priority.resolve`.

**Статус:** СОВПАДАЕТ. См. 2.13 о том, что отсчёт ОТНОСИТЕЛЬНЫЙ.

### 1.9. Ловушка

**Курс:** «Ловушка - называют ситуацию, когда рынок выдаёт ложный сигнал на покупку или
продажу актива, предполагая его рост или падение, но затем актив неожиданно
разворачивается» (стр. 4). И отдельная глава: «Ловушка – ситуация НЕ отработки сетапов, к
которым нужно быть готовым и уметь с ними работать» (стр. 42).

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "И эта ловушка уже не один раз давала
поддержку, и есть поджатие, уровень фактически уже отработан."

**Внешние (9):** [LiteFinance: Bull Trap](https://www.litefinance.org/blog/for-professionals/100-most-efficient-forex-chart-patterns/what-is-a-bull-trap/) ·
[LiteFinance: Bear Trap](https://www.litefinance.org/blog/for-professionals/100-most-efficient-forex-chart-patterns/what-is-a-bear-trap/) ·
[Bit.com: Bull Trap & Bear Trap](https://www.bit.com/insights/knowledge-hub/bull-trap-bear-trap) ·
[Trade Nation: Bull Trap](https://tradenation.com/articles/what-is-a-bull-trap/) ·
[Trade Nation: Bear Trap](https://tradenation.com/en-gb/articles/what-is-a-bear-trap/) ·
[Warrior Trading](https://www.warriortrading.com/bull-trap-definition-day-trading-terminology/) ·
[Strike.money](https://www.strike.money/stock-market/bulltrap-vs-beartrap) ·
[LuxAlgo: Breakouts vs False Breakouts](https://www.luxalgo.com/blog/breakouts-vs-false-breakouts-key-differences/) ·
[CoinMarketCap: Breakout vs Fakeout](https://coinmarketcap.com/academy/article/breakout-vs-fakeout-false-breakout-spot-the-difference-and-increase-accuracy)

**Консенсус:** «ловушка» курса = bull trap / bear trap классики. Совпадение полное,
включая механику: ложный пробой уровня, за которым разворот.

**В коде:** отдельного механизма нет. Ловушка выражена через `breach.BreachKind.BREAKOUT`
и флип уровня (`LevelState.FLIPPED`, `EntryRule.RETEST_FLIPPED`).

**Статус:** СОВПАДАЕТ по смыслу; отдельного типа в коде нет намеренно — курс сам говорит,
что базовый вариант ловушки есть пробой (стр. 42).

### 1.10. Коррекция

**Курс:** «Коррекция - это движение цены в противоположную сторону по отношению к
действующему в данный момент тренду» (стр. 4).

**Статус:** НЕ РЕАЛИЗОВАНО как отдельная величина. Термин употребляется в тексте карточки,
механизма за ним нет — и это верно, потому что курс не даёт ни глубины, ни длительности.

### 1.11. Залоченный игрок

**Курс:** «Залоченный игрок - трейдер, который находиться в минусовой позиции, ожидающий
возврат цены в зону безубытка или выхода в профит» (стр. 5). Это обоснование, почему ПОК
работает: «когда цена будет возвращаться к этому уровню - они сразу будут выходить из
своих позиций в бу» (стр. 21).

**Статус:** ОБЪЯСНЕНИЕ, а не величина. В код не переносится и не должен.

### 1.12. Волатильность

**Курс:** «Волатильность – Высокая динамика изменения цены за короткий промежуток
времени» (стр. 5).

**Внешние (10):** ATR Уайлдера — см. 4.5.

**В коде:** `src/hunter/indicators.py` — ATR как мера волатильности для §4.1 FOUNDATION
(пороги в кратных ATR, а не в процентах).

**Статус:** КУРС МЯГЧЕ — даёт качественное описание, числа не даёт; число взято из
классики (Уайлдер, 1978).

### 1.13. Кредитное плечо

**Курс:** «Кредитное плечо - это займ, который биржа дает трейдеру для совершения сделки
на более крупную сумму, чем собственный капитал трейдера на счете» (стр. 5).

**Статус:** НЕ РЕАЛИЗОВАНО намеренно — §1 FOUNDATION: система "не управляет позицией".
Объём позиции решает пользователь канала.

### 1.14. Прокол

**Курс, дважды дословно:** «Прокол - термин применяется к любому уровню на графике (ПОК,
горизонтальный, ПП и т.д.). Обозначает ситуацию, в которой цена прошла за уровень - и
вернула обратно той же или следующей 1-2 свечами» (стр. 5, повтор стр. 6). И там же:
«Прокол является одним из вариантов отработки уровня – т.к. по итогу цена получает
реакцию от уровня, даже если прокол был достаточно глубоким (сквиз)» (стр. 6).

**Корпус:** *[prizrak_btc_1h_20260725]* "Мы сейчас получаем реакцию именно просто от
нижней границы, без каких-либо проколов." · *[prizrak_btc_heatmap]* "Потом вышли из неё,
точнее из неё мы по сути не вышли, потому что вот у нас были фитилии, проколы." —
прокол НЕ засчитывается выходом из структуры.

**Внешние (8):** [LuxAlgo](https://www.luxalgo.com/blog/breakouts-vs-false-breakouts-key-differences/) ·
[CoinMarketCap](https://coinmarketcap.com/academy/article/breakout-vs-fakeout-false-breakout-spot-the-difference-and-increase-accuracy) ·
[For Traders: False Breakouts](https://fortraders.com/blog/false-breakouts-why-they-happen-how-to-trade) ·
[Equiti: fakeouts guide](https://www.equiti.com/sc-en/news/trading-ideas/how-to-identify-and-trade-fakeouts-a-complete-traders-guide/) ·
[ICFM India](https://www.icfmindia.com/blog/real-vs-fake-breakouts-how-to-identify-and-trade-genuine-breakouts-tradesmart) ·
[TradingView: Breakout or Fakeout](https://www.tradingview.com/chart/ETHUSDT.P/MrLaMqF9-Breakout-or-Fakeout-How-to-Spot-the-Difference-and-Trade/) ·
[Trading Fuel](https://www.tradingfuel.com/breakout-trading-and-fake-out-trading/) ·
[TradingView: True vs False Breakout](https://vn.tradingview.com/chart/NIFTY/U0ENoK4Y-How-to-Identify-a-True-Breakout-vs-a-False-Breakout)

**Консенсус:** fakeout = выход за уровень с возвратом внутрь, но окно возврата классика
называет ШИРЕ: типично пять баров ("we use five bars as the cutoff across our data").

**В коде:** `src/hunter/breach.py` — `RETURN_BARS = 2`, `BreachKind.PUNCTURE`.

**Статус:** **КУРС ЖЁСТЧЕ.** Взято 2 бара по курсу («той же или следующей 1-2 свечами»),
а не 5 по классике. Курс главнее — но разница вдвое-впятеро, и на неё стоит смотреть,
если доля проколов окажется подозрительно низкой.

### 1.15. Пробой

**Курс:** «Пробой – цена прошла за какой-то уровень и в данный момент остается за ним. Как
правило, Пробой сильных уровней цена подтверждает с обратной стороны» (стр. 5, 7, 42).
И «Пробой является вариантом Ловушки – то есть НЕ отработки уровня, когда цена проходит
уровень без ожидаемой нами реакции» (стр. 7). Число тел свечей курс называет в главе ПП:
«то есть закрытия под/над уровнем ПП 2-3 полных тел свечей ЭТОГО ТФ» (стр. 55).

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "То есть в любой момент может быть пробой
этого уровня, проход ниже."

**Внешние:** те же 8, что в 1.14. Консенсус: «true breakout closes beyond the level, not
just wicks through it», подтверждение — второй-третий бар в ту же сторону телами.

**В коде:** `src/hunter/breach.py` — `CONFIRM_BODIES = 2`, `body_beyond()` считает тело
(open и close), а не тень.

**Статус:** СОВПАДАЕТ с классикой; внутри курсового диапазона «2-3» взята НИЖНЯЯ граница.
Это ВЫБОР ВНУТРИ ДИАПАЗОНА — курс между 2 и 3 не выбирает.

### 1.16. Маркетмейкер

**Курс:** «Маркетмейкер(ММ) - это профессиональный участник рынка, задача которого –
обеспечивать ликвидность, то есть «заполнять» биржевой стакан» (стр. 8).

**Внешние (8):** [XBTO: how market makers provide liquidity](https://www.xbto.com/resources/how-market-makers-provide-liquidity-and-stabilize-crypto-markets) ·
[ChainUp](https://www.chainup.com/blog/enhancing-liquidity-in-crypto-exchanges) ·
[Whaleportal: Order Book Depth](https://whaleportal.com/blog/order-book-depth-explained/) ·
[WazirX: Market Depth](https://wazirx.com/blog/what-is-market-depth-in-crypto-a-complete-guide/) ·
[Altrady](https://www.altrady.com/crypto-trading/fundamental-analysis/liquidity-order-book-depth) ·
[SimpleSwap](https://simpleswap.io/blog/liquidity-crypto-metrics-reading-order-books-market-depth-and-volume) ·
[Coinpedia](https://coinpedia.org/information/how-serious-crypto-exchanges-build-deep-liquidity-and-why-most-get-it-wrong/) ·
[arXiv: Funding mechanics and 4h context](https://arxiv.org/pdf/2601.06084)

**Статус:** ОБЪЯСНЕНИЕ. **Стакан и его метрики §3 FOUNDATION запрещает явно** — в
методике их нет, добавление — решение владельца.

### 1.17. Сквиз

**Курс:** «Сквиз – резкий рост/падение цены сразу на несколько % (или десятков %) за очень
короткий промежуток времени (обычно менее часа). Сопровождается ликвидациями позиций
трейдеров» (стр. 8).

**Внешние (10):** [Bit.com: Cascade Liquidation](https://www.bit.com/insights/knowledge-hub/cascade-liquidation) ·
[Bybit Wiki: Short Squeeze](https://www.bybit.com/en/wiki/article/what-is-a-short-squeeze-guide-risk-management/) ·
[Bybit: Short Squeeze in Crypto](https://www.bybit.com/en/wiki/article/short-squeeze-in-crypto-complete-trading-guide/) ·
[Kraken: Short squeeze](https://www.kraken.com/learn/short-squeeze) ·
[Mudrex: Liquidation Cascade](https://mudrex.com/learn/what-is-liquidation-cascade-in-crypto-futures/) ·
[Phemex](https://phemex.com/blogs/crypto-liquidations-guide) ·
[KuCoin: BTC Liquidations](https://www.kucoin.com/blog/en-understanding-btc-liquidations-how-traders-identify-crypto-market-traps-before-they-happen) ·
[XT Exchange: Market Microstructure](https://medium.com/@XT_com/bitcoin-futures-market-microstructure-liquidation-cascades-funding-regimes-and-open-interest-978b107b4889) ·
[OneSafe](https://www.onesafe.io/blog/btc-short-squeezes-market-stability) ·
[Bybit: What Is a Short Squeeze](https://www.bybit.com/en/wiki/article/what-is-a-short-squeeze-crypto-guide/)

**Консенсус:** сквиз = вынужденное закрытие позиций одной стороны; каскад ликвидаций —
механизм его усиления. Определение курса совпадает.

**В коде:** сквиз — **обоснование запаса стопа** (стр. 33: «Это вас обезопасит от сквизов
на рынке»), отдельной величиной не является. Ликвидации §3 FOUNDATION запрещает.

**Статус:** СОВПАДАЕТ; в коде живёт как причина, а не как признак.

### 1.18. РР (Risk-Reward) и R

**Курс:** «РР или RR - от англ. Risk-Reward. Соотношение риска к прибыли» и «"Золотым
стандартом" считаются сделки с РР 1к3 и выше, т.к. они позволяют долгосрочно торговать в
профит даже при ВинРейте 30-40%» (стр. 9). И «Р или R - "один Риск". Это единица измерения
эффективности сделки» (стр. 9).

**Внешние (9):** [LuxAlgo: Win Rate and Risk/Reward](https://www.luxalgo.com/blog/win-rate-and-riskreward-connection-explained/) ·
[TradeZella: Risk-Reward Ratio](https://www.tradezella.com/blog/risk-reward-ratio) ·
[Traders Second Brain: Win Rate vs RR](https://traderssecondbrain.com/guides/win-rate-vs-risk-reward) ·
[Capital Companion: R-Multiple & Breakeven](https://capitalcompanion.ai/tools/risk-reward-calculator/) ·
[Swingfolio calculator](https://swingfolio.com/tools/risk-reward-calculator) ·
[Smart Trading Software](https://smarttradingsoftware.com/en/calculators/risk-reward-ratio-calculator/) ·
[For Traders](https://fortraders.com/blog/risk-reward-ratio-how-to-use-it-to-your-advantage) ·
[JournalPlus](https://journalplus.co/learn/guides/win-rate-vs-risk-reward/) ·
[TradingView: RR — the simple math](https://www.tradingview.com/chart/GOLD/1AJVJaSD-Risk-Reward-Ratio-The-Simple-Math-Most-Traders-Get-Wrong/)

**Консенсус и ПРОВЕРКА АРИФМЕТИКИ КУРСА:** безубыточный винрейт = риск / (риск + прибыль).
Для РР 1к3 это 1/(1+3) = **25%**. Курс называет 30–40% — то есть **утверждение курса
арифметически верно** с запасом 5–15 процентных пунктов. Это одно из немногих
количественных утверждений курса, которое можно проверить не рынком, а арифметикой, — и
оно проверку проходит.

**В коде:** `geometry.Setup.rr()`, `GOLDEN_RR = 3.0`, `src/hunter/outcome.py` — исход в R.

**Статус:** СОВПАДАЕТ.

### 1.19. Винрейт

**Курс:** «Винрейт - англ. win rate - доля успешных/профитных сделок от общего числа
сделок» (стр. 9).

**В коде:** `src/hunter/store.py` — `outcome_survey()`. ⚠ **Смотреть только вместе с
подвыборкой**: разбор `docs/audit/outcome-survey-2026-08-10.md` показал, что 76 сигналов
из 112 не получали исхода никогда, и «средний R» считался по смещённой трети.

**Статус:** СОВПАДАЕТ по определению; ловушка — в знаменателе.

### 1.20. Хеджирование

**Курс:** «Хеджи́рование — это торговая стратегия, которую трейдеры используют для
управления рыночными рисками» (стр. 10, пример на стр. 11).

**Статус:** НЕ РЕАЛИЗОВАНО. Основание — §3.1 FOUNDATION: описывает управление уже
открытой позицией, а §1 говорит, что система позицией не управляет.

### 1.21. Восходящий и нисходящий тренд

**Курс:** «Восходящий тренд (лонг-тренд) - движение цены на выбранном ТФ, при котором
каждый следующий ЛОЙ выше предыдущего» и «Нисходящий тренд (шорт-тренд) - движение цены
на выбранном ТФ, при котором каждый следующий ХАЙ ниже предыдущего» (стр. 12).

**Внешние (9):** [Fidelity: Basic concepts of trend](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/basic-concepts-trend) ·
[Investors Intelligence: Concept of Trend](https://www.investorsintelligence.com/help/education/technical-analysis/concept-of-trend) ·
[Dow Theory — TNFX](https://tnfx.co/dow-theory/) ·
[Verified Investing: Dow Theory](https://verifiedinvesting.com/blogs/education/what-is-the-dow-theory) ·
[Espresso: Dow Theory](https://www.myespresso.com/bootcamp/module/technical-analysis-basics/dow-theory-in-technical-analysis) ·
[AlphaEx: Lower Highs and Lower Lows](https://www.alphaexcapital.com/forex/price-action/lower-highs-and-lower-lows) ·
[JournalPlus: Higher High / Lower Low](https://journalplus.co/learn/glossary/higher-high-lower-low/) ·
[Indicator.trading: trend types](https://indicator.trading/en/concept/trend-types) ·
[TradingView: Is the Dow Theory Still in Play](https://it.tradingview.com/chart/GWLC/FooLwciu-Is-the-Dow-Theory-Still-in-Play)

**Консенсус классики (Дow, 1890-е):** восходящий тренд требует **обоих** условий —
higher highs **И** higher lows; "losing either condition signals the trend is weakening".

**В коде:** `src/hunter/swings.py::trend()` — по курсу: восходящий = растут ЛОИ (хаи не
проверяются), нисходящий = падают ХАИ. Если выполняются оба сразу — направление
не назначается (`TrendDirection.NONE`).

**Статус:** **КУРС МЯГЧЕ КЛАССИКИ**, и это существенно: по курсу тренд восходящий даже
если хаи падают. Код следует курсу (§0 FOUNDATION). Возврат `NONE` при одновременном
выполнении обоих условий — **НАШЕ ДОПУЩЕНИЕ**: курс такого случая не называет.

### 1.22. Флэт / рэндж / база / консолидация / структура / накопление

**Это шесть слов для одного понятия, и курс сам их отождествляет — трижды, разными
наборами:**

* «Флэт (рэндж, база, консолидация, структура) - движение цены в горизонтальном ценовом
  диапазоне. Флэт - это боковой тренд, при котором в течении периода времени локальные
  хаи/лои повторяются и не меняются» (стр. 13);
* «Флэт (рэндж, база, накопление) — движение цены в горизонтальном ценовом диапазоне.
  Флэт — это боковой тренд» (стр. 18);
* «Флет/накопление (ренж/бaза) - накопление с конкретными границами, имеющая в рамках
  своего ТФ понятные 4 и более точек» (стр. 22).

**Три определения не тождественны.** Стр. 13 даёт КАЧЕСТВЕННЫЙ признак (локальные
экстремумы повторяются), стр. 22 — СЧЁТНЫЙ (4+ точек границ). Первое — то, что делает
флэт флэтом; второе — то, что делает его распознаваемым.

**Корпус:** *[prizrak_ondo]* "цена уже, возможно, уходит формировать какое-то накопление в
текущем ценовом диапазоне, где мы полноценно видим первую точку, вторую, третью" — автор
считает точки вслух и на трёх ещё не считает структуру состоявшейся.

**Внешние (9):** [AvaTrade: Support and Resistance](https://www.avatrade.com/education/technical-analysis-indicators-strategies/support-and-resistance) ·
[Capital.com: S/R strategy](https://capital.com/en-int/learn/technical-analysis/support-and-resistance-trading-strategy) ·
[NordFX: Consolidation Trading](https://nordfx.com/useful-articles/consolidation-trading-a-practical-guide-for-day-and-swing-traders) ·
[The Balance: Trade Based on S/R](https://www.thebalancemoney.com/how-to-trade-based-on-support-and-resistance-levels-4043477) ·
[FasterCapital: Consolidation Range](https://fastercapital.com/keyword/consolidation-range.html) ·
[Trade-Ideas: Position in Consolidation](https://www.trade-ideas.com/help/filter/RCon/) ·
[TradingView: Mastering S/R Levels](https://vn.tradingview.com/chart/SOLUSDT/JUsoXN1T-Mastering-Trading-with-Support-and-Resistance-Levels) ·
[LuxAlgo: Wyckoff Accumulation](https://www.luxalgo.com/blog/wyckoff-accumulation-a-pattern-essentials-guide/) ·
[TrendSpider: Wyckoff Accumulation](https://trendspider.com/learning-center/chart-patterns-wyckoff-accumulation/)

**Консенсус:** диапазон подтверждается минимум **двумя касаниями каждой границы** (то есть
четырьмя точками всего) — совпадает с «4 и более» курса; часть источников требует трёх на
сторону. Ближайший методологический аналог — **торговый диапазон Вайкоффа**: горизонтальная
фаза, где «smart money accumulates», с границами по значимым поддержке и сопротивлению.

**В коде:** `src/hunter/accumulation.py` — `MIN_BOUNDARY_POINTS = 4`.

**Статус:** СОВПАДАЕТ по числу точек. **Признак стр. 13 «повторяются и не меняются»
реализован как ЗАМОРОЗКА границы** — граница, назначенная первыми точками, дальше не
двигается. Это прочтение, а не буква: см. 2.2, где оно оспаривается.

### 1.23. Безубыток (БУ)

**Курс:** «Безубыток (БУ) – это когда цена вернулась к точке открытия сделки» (стр. 14, 15).

**Внешние (9):** [The Trapped Trader: Trailing Stops, Partials, Breakeven](https://thetrappedtrader.com/learn/foundations/risk-management/9) ·
[Forex Mechanics: Managing open positions](https://forexmechanics.com/risk-management/managing-open-positions/) ·
[HeyGoTrade: Partial Profit Taking](https://www.heygotrade.com/en/blog/partial-profit-taking-explained/) ·
[ForexTester: Scaling In and Out](https://forextester.com/blog/scale-out-and-scale-in-trading/) ·
[Trade With The Pros](https://tradewiththepros.com/scaling-in-and-out-of-trades/) ·
[ChartingPark: Partial Take Profits](https://chartingpark.com/articles/partial-take-profits-explained-scale-out) ·
[TradeFundrr: Scaling Out](https://tradefundrr.com/blog/scaling-out-taking-partial-profits) ·
[SnappChart](https://www.snappchart.app/blog/risk-psychology/scaling-out-partial-profits) ·
[MondFx: Lock in Profits](https://mondfx.com/lock-in-profits-in-forex-trading)

**Консенсус:** перенос стопа в БУ после ~1R — стандартная практика.

**Статус:** НЕ РЕАЛИЗОВАНО — управление открытой позицией (§1 FOUNDATION). Исход
`OutcomeKind` знает только стоп, цель, неоднозначность, открыт и незаполнен.

### 1.24. Доливка / добор

**Курс:** «Доливка/добор - это метод усиления уже имеющейся позиции на рынке» (стр. 16).

**Статус:** НЕ РЕАЛИЗОВАНО — §3.1 FOUNDATION, управление позицией.

---

# §2. Структура, уровень, профиль — ядро методики

Здесь живут все понятия, ошибка в которых портит всё остальное. Владелец 2026-08-11
потребовал пересобрать этот раздел с нуля после того, как ПОК стал оказываться ВНЕ базы.

### 2.1. Таймфрейм

**Курс:** «Таймфрейм (TФ) – это временной диапазон, в котором анализируется движение
цены» и «Мы используем основные ТФ ( 5м/15м/час/4ч/1Д/1Н )» (стр. 17).

**Внешние (9):** [Tradeciety: Multiple Time Frame Analysis](https://tradeciety.com/how-to-perform-a-multiple-time-frame-analysis) ·
[HeyGoTrade: Multi-Timeframe Analysis](https://www.heygotrade.com/en/blog/multi-timeframe-analysis-explained-for-traders/) ·
[VT Markets: Top-Down Analysis](https://www.vtmarkets.com/en-asia/discover/top-down-analysis-in-trading-a-cfd-traders-guide/) ·
[VT Markets: MTF Complete Guide](https://www.vtmarkets.com/en-ca/discover/multi-timeframe-analysis-the-complete-trading-guide/) ·
[MQL5: Multi-Timeframe Market Structure](https://www.mql5.com/en/blogs/post/772110) ·
[Collin Seow: Ultimate Guide to MTF](https://collinseow.com/multi-timeframe-analysis/) ·
[Olix Academy](https://olixacademy.com/technical-analysis/multi-timeframe-analysis-how-pros-align-higher-and-lower-charts/) ·
[ShopForexEA](https://shopforexea.com/multi-timeframe-analysis/) ·
[TradingView: Top-Down MTF Alignment](https://my.tradingview.com/script/96ZLQnu1-Top-Down-Analysis-Multi-Timeframe-Alignment)

**Консенсус:** соседние ТФ берут с кратностью 4–6×. Проверка курсового набора:
5м→15м ×3, 15м→1ч ×4, 1ч→4ч ×4, 4ч→1Д ×6, 1Д→1Н ×7. **Пять переходов из шести попадают
в классический интервал; выбивается 5м→15м (×3).**

**В коде:** `src/hunter/bars.py::TIMEFRAME_MS`, `geometry.TF_ORDER`.

**Статус:** СОВПАДАЕТ.

### 2.2. ⚠ ГРАНИЦА БАЗЫ — главное неарбитрированное место проекта

**Курс, единственная фраза:** «Границы базы (хай и лой) чаще всего определяются первыми
2-мя точками» (стр. 18).

**Эта фраза допускает ДВА прочтения, и курс словом их не различает:**

| | прочтение А | прочтение Б |
|---|---|---|
| что значит "(хай и лой)" | ИМЕНА двух границ: верхняя зовётся хай, нижняя лой | ОПЕРАЦИЯ: взять максимум и минимум |
| "первыми 2-мя точками" | по одной точке на каждую границу | по две точки на каждую сторону |
| верхняя граница = | цена первой верхней точки | максимум двух первых верхних точек |
| что поддерживает | нумерация схемы (1,3,5 сверху; 2,4,6 снизу) и признак флэта: локальные экстремумы повторяются — повторяется тот уровень, на котором ОБЕ точки побывали, то есть внутренний край | слово "хай" в скобке; при внутреннем крае вторая точка стороны становится проколом автоматически, ещё до всякого рынка |

Признак, на который опирается прочтение А, стоит дословно: «Флэт - это боковой тренд, при
котором в течении периода времени локальные хаи/лои  повторяются и не меняются» (стр. 13).

**История правок — и она сама есть предмет вопроса владельца:**

* до 2026-08-08 границей считалась вся полоса между двумя точками, наружу отдавался
  дальний край;
* 2026-08-08 взят ВНУТРЕННИЙ край (`min(hi_px[:2])`), обоснование — признак стр. 13;
* **2026-08-11 взят ВНЕШНИЙ край (`max(hi_px)`), обоснование — слово "хай" в стр. 18.**
  Замер: доля структур, у которых ПОК попал внутрь базы, 92.5% против 75.3%; контроль на
  заведомо неверной границе — 61.0%. Разбор: [`docs/audit/poc-inside-base-2026-08-11.md`](audit/poc-inside-base-2026-08-11.md).

**Что об этом надо сказать честно.** Замер показал, что новая граница ЛУЧШЕ старой по
выбранной мере. Он **не** показал, что прочтение Б верно: мера "ПОК внутри базы" растёт от
расширения границы монотонно, то есть у прибора есть тривиальный способ её улучшить —
раздвинуть границу. Внешний край её и раздвигает. Правильный контроль здесь — не "стало
больше", а сравнение с разметкой автора на корпусе, и он **не проведён**.

**Внешние (14):** те же 9, что в 1.22, плюс
[Trading Wyckoff: Accumulation](https://tradingwyckoff.com/en/accumulation/) ·
[StoicFX: Wyckoff Accumulation](https://stoicfx.com/learn/wyckoff-accumulation) ·
[Alchemy Markets: Wyckoff Accumulation](https://alchemymarkets.com/education/guides/wyckoff-accumulation/) ·
[LiteFinance: Wyckoff Method](https://www.litefinance.org/blog/for-professionals/wyckoff-method/) ·
[Traders Mastermind: Wyckoff Method](https://tradersmastermind.com/wyckoff-method/)

**Консенсус:** классика границу диапазона проводит по ЭКСТРЕМУМАМ — у Вайкоффа границы
задают automatic rally и secondary test, то есть максимум отскока и минимум теста, а не
внутренний край пары. **Внешние источники поддерживают прочтение Б.** Но классика и не
знает правила "первыми 2-мя точками": там граница уточняется всей историей диапазона.

**В коде:** `src/hunter/accumulation.py` — `up_edge = max(hi_px)`, `lo_edge = min(lo_px)`.
⚠ **Докстрока `BoundaryZone` в этом же файле до сих пор описывает прочтение А** и приводит
доводы против нынешнего кода. Расхождение кода и его собственной докстроки — ровно то, от
чего предостерегает CLAUDE.md: докстрока не истина.

**Статус:** **ВЫБОР НЕ АРБИТРИРОВАН.** Ни курс, ни корпус его не решают. Внешняя классика
склоняет к Б, схема стр. 18 — к А. Решение стоит на слове "хай" и на замере, который по
построению не мог ответить иначе.

### 2.3. Точка границы

**Курс словом не определяет.** Правило выражено ТОЛЬКО рисунком: на схемах стр. 18, 21, 23,
35 точки пронумерованы сквозным счётом и строго чередуют сторону — 1, 3, 5 сверху, 2, 4, 6
снизу. Число точек курс называет трижды и по-разному: «понятные 
4 и более точек» (стр. 22), «(4+6+ точки границ)» (стр. 23), «(4+ 
точки границ)» (стр. 24).

**Корпус:** *[prizrak_ondo]* "мы полноценно видим первую точку, вторую, третью" — счёт
сквозной, через обе стороны, как на схеме.

**В коде:** `accumulation.MIN_BOUNDARY_POINTS = 4` (счёт 2:1 в пользу 4+); точка границы —
фрактал Вильямса (см. 2.9); чередование стороны обязательное.

**Статус:** СОВПАДАЕТ с рисунком. **Само отождествление "точка границы = фрактал" — НАШЕ
ДОПУЩЕНИЕ**: курс не говорит, чем распознавать точку.

### 2.4. Прокол за границу и расширение базы

**Курс:** «Но если на 3++ точках были проколы за границы - стоп всегда ставится за этот
прокол, т.к. это может 
быть расширением базы и в будущем цена может ходить уже в этом расширенном диапазоне»
(стр. 18).

**Фраза говорит о ДВУХ вещах, и реализована пока ОДНА:**

1. стоп ставится за прокол — **реализовано** (`levels.StopAnchorSource.PUNCTURE`);
2. это может быть расширением базы — **НЕ РЕАЛИЗОВАНО**: сама граница проколом не
   двигается.

**В коде:** `accumulation.MIN_PUNCTURE_ORDINAL = 3` — номер точки сквозной, через обе
стороны, по схеме стр. 18.

**Статус:** **РЕАЛИЗОВАНА ПОЛОВИНА ПРАВИЛА.** Вторая половина — открытая работа; курс
формулирует её осторожно ("может быть") и порога, с которого расширение считается
состоявшимся, не даёт.

### 2.5. Выход из структуры

**Курс:** «Пока цена не вышла из структуры - у нас нет уровня, уровень появляется когда
цена полноценно 
выходит из структуры на данном ТФ - тогда мы натягиваем профиль объема и выделяем
уровень» (стр. 23).

Слово "полноценно" курс раскрывает в другом месте — через подтверждение телами: «то есть
закрытия под/над уровнем ПП 2-3 полных тел свечей ЭТОГО ТФ» (стр. 55).

**Корпус:** *[prizrak_btc_heatmap]* "из неё мы по сути не вышли, потому что вот у нас были
фитилии, проколы" — тени выходом не считаются. *[prizrak_btc_eth_keyzone]* "Биток проколол
вот эту канальную структуру, но первого выхода как такового еще не было."

**В коде:** `accumulation.StructureExit`, `breach.CONFIRM_BODIES = 2`, `body_beyond()`.

**Статус:** СОВПАДАЕТ. Перенос правила стр. 55 (оно про уровень ПП) на выход из структуры —
**НАШЕ ДОПУЩЕНИЕ**, но курс сам ставит эти сущности в один ряд: «Уровень ПП как, и уровни
границ накопления или объемные уровни, могут Прокалываться или Пробиваться, 
и требуют подтверждения» (стр. 55).

### 2.6. ПОК (Point of Control)

**Курс:** «ПОК = POC = Point Of Control - 
максимальный уровень ликвидности 
(проторговки, где было больше всего 
продаж/покупок) какой-то зоны на 
графике» и «ПОК конкретных накоплений, т.е. 
максимальный уровень проторговки 
базы» (стр. 21).

**Корпус:** *[prizrak_alts_10_overview]* "BTC просто сходил за ретестом POCO недельной
базы, под которым он стоит." · *[prizrak_bch_praktikum]* "И если мы на нее натягиваем
профиль объема, мы видим, что ее пок и ее зона выше, чем всей нашей структуры" — у
вложенной структуры свой ПОК, и он может лежать выше ПОК объемлющей.

**Внешние (10):** [TradingView: Volume profile basic concepts](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/) ·
[TrendSpider: Understanding Point of Control](https://trendspider.com/learning-center/understanding-point-of-control-a-guide-for-investors-and-traders/) ·
[GoCharting: POC Trading Guide](https://gocharting.com/blog/volume-profile/point-of-control-trading-guide) ·
[Ventura Securities](https://www.venturasecurities.com/share-market-glossary/point-of-control-poc/) ·
[Angel One](https://www.angelone.in/knowledge-center/online-share-trading/point-of-control-poc-in-trading) ·
[Zaye Capital Markets](https://zayecapitalmarkets.com/what-is-volume-profile-in-trading/) ·
[Quantum-Algo: What Is POC](https://www.quantum-algo.com/blog/guides/what-is-poc-in-trading/) ·
[Trader Dale](https://www.trader-dale.com/how-to-trade-using-volume-profile-and-poc-a-beginners-guide/) ·
[JournalPlus: Point of Control](https://journalplus.co/learn/glossary/point-of-control/) ·
[TradingView script: POC](https://my.tradingview.com/script/q7X68NuW-Point-of-Control-POC)

**Консенсус:** POC — ценовой уровень с максимальным ТОРГОВЫМ ОБЪЁМОМ за период; на
гистограмме это самая длинная полоса; происхождение — Market Profile Питера Стейдлмайера
(CBOT, 1980-е).

⚠ **Расхождение в слове.** Курс говорит про максимальный уровень ликвидности, классика —
про максимальный объём. Ликвидность (глубина стакана) и объём (совершённые сделки) —
разные величины. Скобка курса про проторговку и продажи/покупки снимает двусмысленность в
пользу объёма, и код берёт объём.

**В коде:** `src/hunter/volume_profile.py::build_tv()` — сетка и ПОК по справке TradingView;
объём кладётся по реальным сделкам (aggTrade), а не по объёму бара.

**Статус:** СОВПАДАЕТ. ⚠ Число строк гистограммы — см. 2.7.

### 2.7. Профиль объёма: фиксированный и VRVP

**Курс:** «VRVP - Профиль объема видимой области — это расширенное исследование графиков,
которое 
отображает торговую активность за определенный период времени на определенных уровнях
цен» и «Для определения 
ТВХ и уровней – используем индикатор «фиксированный профиль объема», т.к. он более
точный» (стр. 63). Окно профиля: «"Натягивая" профиль на структуру – важно захватить все
свечи структуры!» (стр. 26).

**Корпус:** *[prizrak_btc_eth_keyzone]* "вот он натянут профиль объема, вот на вот эту
структуру, из которой мы предполагаем, возможно, вышли."

**Внешние (7):** [TradingView: Fixed Range Volume Profile indicator](https://www.tradingview.com/support/solutions/43000480324-fixed-range-volume-profile-indicator/) ·
[TradingView: Fixed range VP drawing tool](https://www.tradingview.com/support/solutions/43000707985-fixed-range-volume-profile-drawing-tool/) ·
[TradingView India: Fixed range VP](https://in.tradingview.com/support/solutions/43000707985-fixed-range-volume-profile/) ·
[TradingView: Visible Range Volume Profile](https://www.tradingview.com/support/solutions/43000703076-visible-range-volume-profile/) ·
[TradingView: Periodic Volume Profile](https://www.tradingview.com/support/solutions/43000703071-periodic-volume-profile/) ·
[TradingView: Session Volume Profile](https://www.tradingview.com/support/solutions/43000703072-session-volume-profile/) ·
[TradingView India: Anchored Volume Profile](https://in.tradingview.com/support/solutions/43000707989-anchored-volume-profile/)

**Документация дословно (перевод):** при Rows Layout = Number of Rows размер строки
считается как (верх − низ) / число строк / размер тика, с округлением до целого числа
тиков и возможным добавлением строк, чтобы покрыть весь диапазон.

**В коде:** `volume_profile.RowSize.ROWS/TICKS`, `build_tv()`; боевое значение задано
константой `TV_ROWS`.

**Статус:** ФОРМУЛА СОВПАДАЕТ с документацией TradingView. **Само число строк — НАШЕ
ДОПУЩЕНИЕ**: курс его не называет нигде, оно снято замером с нативных скриншотов автора
(разброс ~24…≥95) и выбрано одно. От этого числа зависит цена ПОК.

### 2.8. Объёмная зона

**Курс:** зона появляется только на рисунке и в подписях к нему: «1 - цена забирает
объемную зону, (выделено желтым цветом) и идет в 
нужном направлении» (стр. 30); «чтобы точно «забрало» ваш ордер - используем 2-3 ордера,
на зону, и на уровень ПОК» (стр. 30). **Ширины зоны и доли объёма курс не называет ни
разу.**

**Корпус:** *[prizrak_bch_praktikum]* "Вот здесь самый первый уровень, особенно который над
объемной зоной базы." · *[prizrak_alts_10_overview]* "сразу всю зону на случай, там
сквизов, проколов".

**Внешние — две РАЗНЫЕ вещи, которые нельзя путать.**

*Value Area (Market Profile), 10 ссылок:*
[Overcharts: Volume Profile Value Area](https://www.overcharts.com/en/helpcenter/docs/volume-profile-value-area/) ·
[CQG: Market Profile Value Areas](https://help.cqg.com/cqgic/25/Documents/marketprofilevalueareasmpva.htm) ·
[thinkorswim: VolumeProfile](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VolumeProfile) ·
[MarketProfile.info: Value Area Explained](https://marketprofile.info/articles/value-area-explained) ·
[Chart Guys: Market Profile](https://www.chartguys.com/articles/market-profile) ·
[Apex Trader Funding](https://apextraderfunding.com/module-2/market-profile-and-value-area/) ·
[Bookmap](https://bookmap.com/blog/market-profile-trading-understanding-its-power-and-impact) ·
[HowToTrade: Value Area](https://howtotrade.com/trading-strategies/value-area/) ·
[QuantVPS](https://www.quantvps.com/blog/value-area-trading-strategy-guide) ·
[eMinimind: Ultimate Guide to Market Profile](https://eminimind.com/the-ultimate-guide-to-market-profile/)

*Supply/Demand zone, 7 ссылок:*
[FXOpen](https://fxopen.com/blog/en/supply-and-demand-trading-patterns-and-strategies/) ·
[Strike.money](https://www.strike.money/technical-analysis/demand-and-supply-zone) ·
[5paisa](https://www.5paisa.com/stock-market-guide/online-trading/supply-and-demand-zones) ·
[LuxAlgo](https://www.luxalgo.com/blog/supply-and-demand-zones-a-simple-guide/) ·
[TradeFundrr](https://tradefundrr.com/understanding-supply-and-demand-zones/) ·
[Colibri Trader](https://www.colibritrader.com/supply-and-demand-zones/) ·
[TradingView: Volumetric S/D Zones](https://www.tradingview.com/script/GayjgBf7-Volumetric-Supply-and-Demand-Zones-BOSWaves/)

**Консенсус:** Value Area — диапазон вокруг ПОК, вмещающий 70% объёма; 70% примерно
соответствует одному стандартному отклонению. Алгоритм: от ПОК расширяться, каждый шаг
добавляя ПАРУ строк с той стороны, где объём больше, пока не набрано 70%. Зона
спроса/предложения — совсем другое понятие: след одного решительного движения, а не
область согласия.

**В коде:** `Level.zone_lo/zone_hi` — Value Area 70%; `volume_profile.Expansion.PAIRS`
реализует парное расширение по документации.

**Статус:** **ВЫБОР НЕ АРБИТРИРОВАН.** Отождествление "объёмная зона автора = Value Area
70%" взято нами. Курс рисует зону и доли не называет; по картинке стр. 30 зона заметно
уже 70% профиля. Помечено прямо в докстроке `Level.zone_lo`.

### 2.9. Свинг (точка экстремума)

**Курс молчит.** Хай бара определён (стр. 3), окно локального экстремума — нет.

**Внешние (10):** [MetaTrader 5: Fractals](https://www.metatrader5.com/en/terminal/help/indicators/bw_indicators/fractals) ·
[thinkorswim: WilliamsFractal](https://toslc.thinkorswim.com/center/reference/Patterns/candlestick-patterns-library/bearish-and-bullish/WilliamsFractal) ·
[TradingView: Williams Fractal](https://www.tradingview.com/support/solutions/43000591663-williams-fractal/) ·
[LinnSoft: Fractals — Swing Highs, Swing Lows](https://www.linnsoft.com/techind/fractals-swing-highs-swing-lows) ·
[LuxAlgo: Williams Fractal](https://www.luxalgo.com/blog/williams-fractal-spotting-reversal-in-trends/) ·
[MQL5 Article 17334](https://www.mql5.com/en/articles/17334) ·
[Get Together Finance](https://www.gettogetherfinance.com/blog/fractal-indicator/) ·
[Trading Strategy Guides](https://tradingstrategyguides.com/fractal-trading-strategy/) ·
[Medium: Williams Fractals Swing Strategy](https://medium.com/algorithmic-and-quantitative-trading/the-williams-fractals-swing-trading-strategy-pinpointing-support-resistance-for-high-probability-c0ced6c2e7c2) ·
[Pro-Scalper](https://www.pro-scalper.com/indicators/fractals-gold-trading)

**Консенсус:** фрактал — пять баров подряд, экстремум на среднем, по два слабее с каждой
стороны. Три платформенные справки (MT5, thinkorswim, TradingView) формулируют одинаково.

**В коде:** `src/hunter/swings.py` — `FRACTAL_BARS = 5`, сравнение строгое, подтверждение
требует двух баров справа (`confirmed_at_index`).

**Статус:** **НАШЕ ДОПУЩЕНИЕ, признанное типом Б по §0.2 FOUNDATION** (конвенция при трёх
независимых источниках). Замер чувствительности: при другой законной конвенции число
структур меняется от 186 до 470. Это самый влиятельный параметр, которого курс не выбирает.

### 2.10. Уровень BUY / SELL

**Курс:** «Лонговое накопление - из которого цена вышла вверх.
Шортовое накопление - из которого цена вышла вниз. 
Соответственно, ПОК таких накоплений образуют Лонговые/Шортовые уровни» (стр. 22).

**В коде:** `levels.Level` — `side`, `price` (ПОК), `zone_lo/hi`, окно структуры,
`created_at_ms` (момент подтверждения выхода).

**Статус:** СОВПАДАЕТ.

### 2.11. Сила уровня

**Курс:** «Сила уровня определяется ТФ и объемом (смотрим по VRVP, иногда в маленькой
часовой наторговке может 
быть объем больше, чем в 4ч-1д накоплении - от таких структур стараемся брать, ожидая
импульсной реакции)» (стр. 22).

**В коде:** ТФ и объём хранятся и печатаются РАЗДЕЛЬНО. Свёртка в одно число запрещена
§4.2 FOUNDATION, и курс сам приводит контрпример в той же фразе.

**Статус:** СОВПАДАЕТ. Единственная в проекте величина, где запрет на скоринг подкреплён
прямой цитатой источника.

### 2.12. Отработанный уровень и касание

**Курс:** «Как только уровень был отработан на 1 касание (как на графике - увидели хорошую
реакцию ) 
этот уровень становиться больше не актуальным, т.е. мы этот уровень удаляем и ищем новые 
не отработанные уровни для входа в новую позицию» и продолжение — «можно рассматривать
вход от 2 или 3 касания только по факту 
слома структуры на младшем ТФ» (стр. 25). Повтор запрета: «уровень 
лимитными ордерами больше не торгуем - т.к. уровень стал слабее, и в след раз может не
отработать» (стр. 31).

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "уровень фактически уже отработан" — автор
употребляет ровно в курсовом смысле.

**В коде:** `levels.LevelState.WORKED_OFF`, `EntryRule.CONFIRMATION`, `first_test_index()`.

**Статус:** СОВПАДАЕТ по запрету. ⚠ **Разрешающая половина не исполняется**: вход от 2-3
касания требует слома структуры на младшем ТФ, а §2.5 в расчёт сигнала не входит
(находка А-02 в FOUNDATION).

### 2.13. Приоритет таймфреймов, ТФ-1 и ТФ-2

**Курс:** «Все уровни накопления относятся к тому ТФ, на котором четко видно их структуру
(4+ 
точки границ) и работают на этом ТФ, а так же на младших по отношению к нему» и
«Уровни ТФ-1 (т.е. 1ч ТФ для 4-часовика) могут быть взяты как промежуточные цели с 
небольшими тейками,» и «Уровни ТФ-2 (15м и ниже) обычно не берутся в расчет, т.к. на
старшем ТФ их вообще "нет”» (стр. 24). Приоритет назван дважды: «Приоритет торговли – по
тренду» (стр. 19) и «ПРИОРИТЕТ СТАРШИЙ ТАЙМФРЕЙМ» (стр. 47).

**В коде:** `src/hunter/priority.py` — `Agreement.BY_TREND / AGAINST_TREND / NO_PRIORITY`;
`geometry._tf_step` для ТФ-1 и ТФ-2.

**Статус:** СОВПАДАЕТ. Скобка про 4-часовик делает отсчёт относительным явно — раньше
документ проекта читал это как абсолютное правило и был исправлен.

### 2.14. Флип уровня и ретест

**Курс:** «3 - цена пробила уровень, пролетела мимо, выбила стоп и ушла выше. Спустя время
- вернулась за тестом 
данного уровня с обратной стороны - теперь для нас это уровень поддержки (в лонг) для
новой позиции» (стр. 28). И в главе ловушек: «Уровень лонг/шорт менятся для нас на 
противоположный. По возврату цены на ретест уровня -  открываем позицию и ставим СТОП за
накопление» (стр. 43).

**Корпус:** *[prizrak_alts_10_overview]* "BTC просто сходил за ретестом POCO недельной
базы" · *[prizrak_marketcap_factor]* "цена забрала, сделав ретест тени от дампа".

**Внешние (8):** [AnalystPrep (CFA L1): Trend, Support & Resistance, Change in Polarity](https://analystprep.com/cfa-level-1-exam/portfolio-management/trend-support-resistance-lines-change-polarity/) ·
[AnalystNotes (CFA)](https://analystnotes.com/cfa-study-notes-explain-uses-of-trend-support-resistance-lines-and-change-in-polarity.html) ·
[All Star Charts: Principle of Polarity](https://www.allstarcharts.com/2016-03-31/principle-polarity-supply-demand-101) ·
[Bajaj Finserv](https://www.bajajfinserv.in/principle-of-polarity) ·
[Angel One: Principle of Polarity](https://www.angelone.in/knowledge-center/share-market/what-is-principle-of-polarity) ·
[Trading Tuitions](https://tradingtuitions.com/change-in-polarity-principle-in-technical-analysis/) ·
[FOREX.com](https://www.forex.com/en/news-and-analysis/eurusd-polarity-principle-points-to-plunging-prices/) ·
[Ironclad Research: Pullbacks & Retests](https://www.ironcladresearch.com/learn/technical-analysis/pullbacks-and-retests)

**Консенсус:** это **принцип полярности** — понятие настолько устоявшееся, что входит в
программу CFA Level 1. Определение курса совпадает с классическим полностью.

**В коде:** `levels.LevelState.FLIPPED`, `EntryRule.RETEST_FLIPPED`.

**Статус:** СОВПАДАЕТ.

### 2.15. Вложенные уровни и лестница закупа

**Курс:** «Но если мы перейдем на МТФ, то в одной большой структуре могут быть
дополнительные уровни и цена 
может забрать как и основной уровень, так и пройтись по всем локальным уровням которые
находятся в 
одной большой структуре» и «лучше закуп делать на все уровни, что бы ваша средняя твх
была максимально безопасная» (стр. 32).

**Корпус:** *[prizrak_bch_praktikum]* "мы видим, что ее пок и ее зона выше, чем всей нашей
структуры" — подтверждает, что вложенный ПОК живёт своей ценой.

**В коде:** `levels.nested()` с `NESTED_MAX_STEPS = 1`; `Setup.ladder`;
`average_entry_equal_shares`.

**Статус:** СОВПАДАЕТ по правилу. **Равные доли на ступенях лестницы — НАШЕ ДОПУЩЕНИЕ**,
названное прямо в имени метода: курс говорит о цели, долей не задаёт. Ограничение
вложенности одним шагом ТФ — тоже наше.

### 2.16. Стоповый объём

**Курс:** «Стоповый объем - небольшое накопление, которое останавливает цену для того,
чтобы «крупные игроки» добрали 
свою позицию, или набрали «силу» для продолжения движения» и «Стоповый объем может быть в
сужении, в поджатии, с четкими границами – это такое же накопление (база), но на 
более мелком ТФ, чем основное движение актива, и обычно более «плотное» (часто большой
объем в мелком 
диапазоне)» (стр. 34). Торгуется как обычное накопление (стр. 35), стоп так же: «Стоп
ставим также, как и в торговле накоплений - за структуру 1-3%» (стр. 36).

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "вот этот уровень, его стоповый расторговали"
· *[prizrak_btc_heatmap]* "цена может реакцию реально получить как от стопового, так и от
всей этой" · *[prizrak_marketcap_factor]* "И прокол был, и стоповый забрали".

**Внешние (8):** [StockCharts: The Stopping Action of a Downtrend](https://stockcharts.com/articles/wyckoff/2015/05/the-stopping-of-a-downtrend.html) ·
[Wyckoff Analytics: Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/) ·
[Trading Wyckoff: Glossary](https://tradingwyckoff.com/en/glossary/) ·
[CMC Markets: Wyckoff method](https://www.cmcmarkets.com/en-gb/trading-strategy/what-is-wyckoff-method) ·
[Capital.com: Wyckoff Method](https://capital.com/en-int/learn/technical-analysis/the-wyckoff-method) ·
[LuxAlgo: Wyckoff Distribution](https://www.luxalgo.com/blog/wyckoff-distribution-key-pattern-explained/) ·
[StockAlarm: Wyckoff volume analysis](https://pro.stockalarm.io/blog/wyckoff-volume-analysis-method) ·
[Alchemy Markets: Wyckoff Accumulation](https://alchemymarkets.com/education/guides/wyckoff-accumulation/)

**Консенсус и ⚠ ВАЖНАЯ ОГОВОРКА:** ближайший классический термин — вайкоффовский *stopping
volume* / *stopping action*: всплеск объёма, ОСТАНАВЛИВАЮЩИЙ тренд. Он описывает
**всплеск объёма**, а стоповый объём курса — **маленькую плотную структуру**. Общее у них
только слово "останавливает". **Отождествлять их нельзя**, и в коде оно не сделано:
`stop_volume.py` ищет маленькое накопление, а не всплеск.

**В коде:** `src/hunter/stop_volume.py` — `Placement.INSIDE/ABOVE/BELOW/BEFORE` (четыре
положения со стр. 36, 37, 39), классификация через тот же `accumulation.detect`.

**Статус:** СОВПАДАЕТ с курсом. Классического аналога с тем же смыслом НЕТ — это
собственный термин методики.

### 2.17. Переприор (ПП) — истинный и ранний

**Курс:** «Переприор (далее сокращ. - ПП) - это слом тенденции / структуры тренда, смена
приоритета , иными                 
словами это разворот цены в противоположное направление» (стр. 49).

*Истинный:* «ПП в Шорт – цена ломает последний лой, из которого был последний хай, и
подтверждает его тестом снизу» (стр. 50).

*Ранний:* «Ранний ПП в Шорт – цена формирует хай, затем лой – затем НЕ обновляет хай и
пробивает последний лой» (стр. 51).

*Отрицательное правило:* «Если в лонг тренде цена не обновила хай - это не является сломом
тренда, как часто принято ошибочно 
считать» (стр. 55).

⚠ **Курс определяет слом дважды и по-разному.** Стр. 5: «Слом тренда в Шорт - пробой с
подтверждением последнего ЛОЯ на выбранном ТФ» — без условия про хай. Стр. 50 условие
добавляет. Код взял версию стр. 50 как более полную.

**Корпус:** *[prizrak_bch_praktikum]* "здесь эта структура называется база переприор, когда
у нас вся база является уровнем смены приоритета, точнее, ее границы" — **термин
"база-переприор" есть у автора, отсутствует в курсе и отсутствует в коде.**

**Внешние (9):** [FXOpen: Break of Structure](https://fxopen.com/blog/en/what-is-a-break-of-structure-and-how-can-you-trade-it/) ·
[Alchemy Markets: BOS](https://alchemymarkets.com/education/strategies/break-of-structure-bos-trading/) ·
[Mind Math Money: BOS & CHoCH](https://www.mindmathmoney.com/articles/break-of-structure-change-of-character-explained) ·
[FluxCharts: Break of Structures](https://www.fluxcharts.com/articles/Trading-Concepts/Price-Action/Break-of-Structures) ·
[Traze: BOS vs CHOCH](https://traze.com/academy/advanced-strategies-forex-brokers/break-of-structure-vs-change-of-character/) ·
[Quantum-Algo: BOS/CHoCH guide](https://www.quantum-algo.com/blog/guides/bos-choch-complete-trading-guide/) ·
[InnerCircleTrader: BOS vs CHOCH](https://innercircletrader.net/tutorials/break-of-structure-vs-change-of-character/) ·
[Trade The Pool: Market Structure Shift](https://tradethepool.com/technical-skill/ict-market-structure-shift/) ·
[TradingView: SMC CHoCH/BOS indicator](https://www.tradingview.com/script/hY8stoih-Smart-Money-Concept-Change-of-Character-Break-of-Structure/)

**Консенсус и ⚠ ГЛАВНОЕ СООТВЕТСТВИЕ:** в терминах Smart Money Concepts переприор — это
**CHoCH (Change of Character)**, а НЕ BOS. BOS означает ПРОДОЛЖЕНИЕ тренда (пробой хая в
восходящем), CHoCH — РАЗВОРОТ (пробой лоя в восходящем). Курсовой ПП определён как
разворот, значит соответствие однозначно: **ПП = CHoCH**. Наши внутренние разборы названы
`bos-projects-*`, и это неудачное имя: сравнивались проекты с механикой CHoCH.

**В коде:** `src/hunter/pereprior.py` — `PPKind.TRUE/EARLY`, `PPSide`, `failed_update()`
для отрицательного правила стр. 55.

**Статус:** СОВПАДАЕТ. Именование внутренних документов через BOS — терминологическая
ошибка, на расчёт не влияющая.

### 2.18. Уровень ПП — зона тени

**Курс:** «Уровнем ПП является вся зона Тени свечи, образовавшей ХАЙ/ЛОЙ, а не только
"шпиль”» (стр. 55).

**Внешние (10):** те же, что в 1.1 (анатомия свечи). Консенсус: тень (shadow/wick) —
отрезок от края тела до экстремума.

**В коде:** `pereprior._shadow_zone()` — зона от края тела до экстремума, а не одна цена.

**Статус:** СОВПАДАЕТ.

### 2.19. Лимитный барьер

**Курс:** этих слов в тексте курса **нет ни разу**. Правило выражено ТОЛЬКО подписью на
схеме стр. 18 — полоса снаружи границы базы, куда допустим прокол и где стоят лимитные
ордера.

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "если кому-то интересно закинуть лимитки, я
бы лимитки с точки зрения шортов закидывал только туда, где есть живые уровни."

**Статус:** **НЕ РЕАЛИЗОВАНО.** Референт есть (рисунок), числа нет: ширину полосы курс не
называет. По §0 FOUNDATION величина без референта в код не пишется.

### 2.20. Фигуры

**Курс:** флаг (стр. 56), вымпел-треугольник (стр. 57–58), клин (стр. 60), голова и плечи
(стр. 61), двойное и тройное дно и вершина (стр. 62). У вымпела собственные правила:
«Если тренд Лонговый - ПЕРВАЯ точка берется сверху» (стр. 57) и «то ждем 6 касание и
берем на 6 касании» (стр. 57).

**Внешние (8):** [Bulkowski: Visual Index of Chart Patterns](https://thepatternsite.com/visualcpindex.html) ·
[Wiley: Encyclopedia of Chart Patterns, 3rd ed.](https://www.wiley.com/en-us/Encyclopedia+of+Chart+Patterns,+3rd+Edition-p-9781119739685) ·
[Google Books](https://books.google.com/books/about/Encyclopedia_of_Chart_Patterns.html?id=tIwlEAAAQBAJ) ·
[Barnes & Noble](https://www.barnesandnoble.com/w/encyclopedia-of-chart-patterns-thomas-n-bulkowski/1119433468) ·
[Bulkowski Chart Patterns (PDF)](https://ftp.richmondbizsense.com/filedownload.ashx/mL6G25/603317/Bulkowski%20Chart%20Patterns.pdf) ·
[Liberated Stock Trader: 12 data-proven patterns](https://www.liberatedstocktrader.com/chart-patterns-reliable-profitable/) ·
[VT Markets: Essential Chart Patterns](https://www.global-vtrader.com/en/discover/essential-chart-pattern-every-trader-must-know/) ·
[SlideShare: Encyclopedia of Chart Patterns](https://www.slideshare.net/slideshow/encyclopediaofchartpatterns2005thomasbulkowskipdf/252082393)

**Консенсус:** канонический справочник — Балковски, 65 фигур со статистикой. Он же
предупреждает, что вымпел отрабатывает лишь в ~46% случаев — то есть фигура, которой курс
даёт собственное правило входа, у классики одна из худших.

**Статус:** **НЕ РЕАЛИЗОВАНО**, основание — §2.6 и §3.1 FOUNDATION: механизм накопления
выражает только ГОРИЗОНТАЛЬНЫЕ границы, а вымпел и клин сходящиеся, и при схождении
детектор структуру бросает. Треугольники исключены по построению, а не обработаны.

**Корпус же показывает, что автор ими пользуется постоянно:** *[prizrak_astr_razbor]*
"снизу мы видим вот такое вот активное поджатие" · *[prizrak_alts_10_overview]* "останутся
в сужении, на ключевых поддержках". Это самая большая непокрытая часть методики.

---

# §3. Геометрия сделки

### 3.1. Вход от ПОК и деление закупа

**Курс:** «надежнее всего брать от уровня ПОК (немного выше/ниже, т.к. идеально может не
доходить)» и «чтобы точно «забрало» ваш ордер - используем 2-3 ордера, на зону, и на
уровень ПОК» (стр. 30). Правило деления: «Если накопление очень большое ТФ 1Д-1Н-1М  - то
закуп всегда стоит делить на зону и на уровень» и «Если накопление маленькое - 5м-1ч –
эффективнее входить 1 ордером от уровня» (стр. 30).

**В коде:** `geometry.Setup.entry` = ПОК; `entry_zone_lo/hi` = зона; `split_orders`
включается для `BIG_STRUCTURE_TFS = {1d, 1w}`.

**Статус:** СОВПАДАЕТ. ⚠ Курс называет крупными 1Д-1Н-1М, код — 1Д и 1Н: **месячного ТФ в
системе нет** (стр. 17 перечисляет основные ТФ без него), поэтому строка курса усечена, а
не переиначена.

### 3.2. Стоп безопасный и рисковый

**Курс:** «Безопасный СТОП за дно структуры с запасом 1-3%. Это вас обезопасит от сквизов
на рынке, и на случай пробоя уровня и закрепа у вас будет больше шансов выйти в бу» и
«Рисковый СТОП: прямо за лой структуры или же за объемную зону. Такой стоп легко могут
вынести сквизом» (стр. 33).

**В коде:** `geometry.Setup` — `stop_safe_near`, `stop_safe_far`, `stop_risky`, и ОДИН
выставляемый `stop` с обоснованием `stop_basis`.

⚠ **Почему один, а не меню.** До 2026-08-05 карточка печатала три цены и выбор оставался
оператору. Выставить можно только один стоп; значит РР был не определён, и все замеры РР
считались по невыбранному стопу. Это записано в докстроке `Setup.stop`.

**Статус:** СОВПАДАЕТ. ⚠ **"Дно структуры" и "лой структуры" — одно и то же место или
разные?** Курс употребляет оба выражения в соседних предложениях. Код читает оба как
ГРАНИЦУ (а не как минимум всех баров), и довод к этому: если бы речь шла о минимуме всех
баров, правило про прокол (стр. 18) было бы избыточным — прокол уже включён в минимум.

### 3.3. Якорь стопа

**Курс:** «Если в диапазоне 2-5% от границы есть стоповый объем – база мелкого ТФ - или
Лой того же              ТФ или ТФ-1 - идеально стоп прятать за них» (стр. 18). И
безусловное: «стоп всегда ставится за этот прокол» (стр. 18).

**В коде:** `levels.StopAnchorSource` — `PUNCTURE`, `STOP_VOLUME`, `SWING`; полоса поиска
`STOP_ANCHOR_BAND_MIN_PCT = 2.0`, `STOP_ANCHOR_BAND_MAX_PCT = 5.0`; берётся ДАЛЬНИЙ из
применимых якорей — оба правила говорят "стоп за X", и стоп за самым дальним удовлетворяет
обоим сразу.

**Статус:** СОВПАДАЕТ. Все три вида якоря названы курсом в одной фразе.

### 3.4. ⚠ Запас за структуру — процентов ЧЕГО

**Курс даёт ДВА диапазона:** «Безопасный СТОП за дно структуры с запасом 1-3%» (стр. 33),
повтор на стр. 36 и «Стоп всегда прячем за всю структуру с запасом 1-3%» (стр. 58); и
отдельно для лимитной торговли у границ флэта — «СТОП прятать с запасом за структуру
(границы) 1-5%» (стр. 19). Счёт 3:1 в пользу 1–3%.

**⚠ Чего именно проценты — курс не говорит ни разу.** Возможных прочтения два: процент от
ЦЕНЫ границы или процент от ВЫСОТЫ структуры. На узкой структуре они отличаются в разы.

**Внешние (8):** см. 1.4. **Консенсус классики: ни то, ни другое** — буфер меряется в долях
ATR (0.3–0.5 ATR сверх уровня) или в тиках, потому что процент от цены не переносится
между инструментами и режимами. Это же требование стоит в §4.1 FOUNDATION, и курс здесь
из него выпадает — что §4.1 и оговаривает как исключение.

**В коде:** `geometry.STOP_MARGIN_MIN_PCT = 1.0`, `STOP_MARGIN_MAX_PCT = 3.0`, процент от
ЦЕНЫ границы; выставляется дальний край (`DEFAULT_MARGIN_PCT = 3.0`).

**⚠ НАШЕ ДОПУЩЕНИЕ поверх курса, введённое 2026-08-11:** запас дополнительно ограничен
ВЫСОТОЙ базы. Повод — замер: медианная высота базы на 5м 0.32%, и запас 3% от цены
отнесён бы на 9.5 высоты базы; такая сделка проиграна геометрией до открытия. Курс такого
ограничения не даёт, число из него не выводится, и в коде это названо прямо
(`geometry.with_margin`). **Это третья форма запаса, которой нет ни у курса, ни у
классики.**

**Статус:** **ВЫБОР НЕ АРБИТРИРОВАН** (процент чего) + **НАШЕ ДОПУЩЕНИЕ** (потолок по
высоте). Оба обстоятельства влияют на РР каждого сигнала.

### 3.5. Цель (тейк-профит)

**Курс:** «Целью позиции от уровня 4ч ТФ - должен быть в первую очередь другой
сопоставимый уровень 4ч ТФ, либо уровень 1Д тф, если он ближайший» и «Уровни ТФ-1 (т.е. 1ч
ТФ для 4-часовика) могут быть взяты как промежуточные цели с небольшими тейками,»
(стр. 24).

**В коде:** `geometry.TargetRole.PRIMARY` (свой ТФ или старший) и `INTERMEDIATE` (ТФ−1);
`build_targets()` отбирает только уровни, существовавшие НА МОМЕНТ сигнала.

⚠ **Почему проверка момента вообще есть.** До 2026-08-04 её не было, и замер на 374
уровнях дал 196 основных целей из 366 (54%), созданных ПОЗЖЕ уровня, от которого строилась
сделка: тейк-профит выбирался задним числом. Разбор:
[`docs/audit/critical-review-verified-2026-08-04.md`](audit/critical-review-verified-2026-08-04.md).

**Статус:** СОВПАДАЕТ.

### 3.6. Частичная фиксация

**Курс:** «По верхней границе – делаете тейк 50% - но не 100% - т.к. приоритет Лонг и цена
может больше не вернуться к нижней границе и выйти из базы вверх» (стр. 19). И «где кроем
часть 30-50%» (стр. 39).

**Внешние (9):** см. 1.23 (управление позицией). Консенсус: частичная фиксация плюс
перенос стопа в БУ — стандарт.

**В коде:** `geometry.PARTIAL_TAKE_PCT = 50.0` — число названо курсом прямо.

**Статус:** СОВПАДАЕТ по числу. Само исполнение (перенос стопа, добор) не реализуется —
§1 FOUNDATION.

---

# §4. Индикаторы — только доп-факторы

**Общее правило курса, повторённое трижды:** «ВАЖНО! : индикатор используется только как
дополнительный фактор к нашей точке входа» (стр. 64), «ВАЖНО: индикатор используется
только как 
дополнительный фактор к нашей точке входа» (стр. 65), «индикатор является дополнительным
фактором к ТВХ!» (стр. 66).

**В коде:** индикаторы живут в `src/hunter/factors.py` и **не порождают сигнал и не
гейтят его** — только сопровождают карточку.

### 4.1. RSI

**Курс:** названия параметров не даёт; предписывает добавить индикатор «Индекс
относительной силы» (стр. 64) и смотреть по нему дивергенции.

**Внешние (10):** [StockCharts ChartSchool: RSI](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi) ·
[TradingView: RSI](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) ·
[thinkorswim: RSI](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/R-S/RSI) ·
[AAII: Wilder's RSI](https://www.aaii.com/journal/article/measuring-internal-strength-wilder-s-rsi-indicator) ·
[Steema: RSI function](https://www.steema.com/docs/financialFunctionsRef/RSIFunction.htm) ·
[OANDA: Understanding RSI](https://www.oanda.com/us-en/trade-tap-blog/trading-knowledge/understanding-the-relative-strength-index/) ·
[For Traders: RSI overbought/oversold](https://fortraders.com/blog/rsi-indicator-identifying-overbought-and-oversold-conditions) ·
[Quantum-Algo: RSI guide](https://www.quantum-algo.com/blog/guides/rsi-indicator-complete-guide/) ·
[TradingView: RSI indicator](https://es.tradingview.com/chart/HDFCBANK/UVCUlPAV-Relative-Strength-Index-RSI-Indicator) ·
[TradingView: RSI How To Use It](https://es.tradingview.com/chart/EURAUD/CT6w3LZV-RSI-How-To-Use-It)

**Консенсус:** Уайлдер, 1978, «New Concepts in Technical Trading Systems»; период 14;
RSI = 100 − 100/(1 + RS); сглаживание — уайлдеровское (α = 1/period), НЕ обычная EMA;
пороги 70 и 30.

**В коде:** `indicators.rsi(period=14)` с уайлдеровским сглаживанием;
`factors.RSI_OVERBOUGHT = 70.0`, `RSI_OVERSOLD = 30.0`.

**Статус:** **КУРС МОЛЧИТ О ЧИСЛАХ — взяты из классики** (§0.2 тип Б). Пороги 70/30 в курсе
не названы; они канонические у Уайлдера и в трёх платформенных справках.

### 4.2. MACD

**Курс:** предписывает добавить «Схождение/расхождение скользящих средних» (стр. 64);
параметров не называет.

**Внешние (10):** [Investing.com: What Is MACD](https://www.investing.com/academy/analysis/macd-definition-uses/) ·
[OANDA: MACD](https://www.oanda.com/us-en/learn/indicators-oscillators/determining-entry-and-exit-points-with-macd/) ·
[NAAIM: MACD-V (Spiroglou, PDF)](https://www.naaim.org/wp-content/uploads/2022/05/MACD-V-Alex-Spiroglou-WEB.pdf) ·
[Bajaj AMC](https://www.bajajamc.com/knowledge-centre/macd-indicator) ·
[Algomatic Trading](https://www.algomatictrading.com/post/macd-moving-average-convergence-divergence) ·
[T3 Live](https://www.t3live.com/macd-trading/) ·
[Lumley Trading](https://www.lumleytrading.com/macd) ·
[Monkeytrade](https://www.monkeytrade.com/learn/technical-analysis/macd-indicator-explained) ·
[TradingView script (KR)](https://kr.tradingview.com/script/nBALBdNa-faiz-MACD) ·
[TradingView script (VN)](https://vn.tradingview.com/script/nBALBdNa-faiz-MACD)

**Консенсус:** Джеральд Аппель, 1977; MACD = EMA(12) − EMA(26); сигнальная линия — EMA(9)
от MACD; гистограмма добавлена Томасом Аспреем в 1986.

**В коде:** `indicators.MACD_FAST = 12`, `MACD_SLOW = 26`, `MACD_SIGNAL = 9`.

**Статус:** **КУРС МОЛЧИТ О ЧИСЛАХ — взяты канонические.** Именно они стоят умолчанием в
TradingView, куда курс и отсылает.

### 4.3. Дивергенция и конвергенция

**Курс:** «Дивергенция и конвергенция – ситуации, когда тренд 
графика и тренд индикатора идут разнонаправлено, что 
может являться доп фактором к предстоящей смене 
тренда» (стр. 65). Дивергенция: «хая на графике повышаются, а на индикаторе, наоборот,
затухают – признак угасания силы тренда и возможного разворота в 
Шорт» (стр. 65). Конвергенция: «лои на графике понижаются, а на индикаторе, наоборот, 
лои повышаются – признак усиления откупа и возможного 
разворота в Лонг» (стр. 65).

**Внешние (8):** [FXOpen: Hidden vs Regular Divergence](https://fxopen.com/blog/en/what-is-the-difference-between-regular-and-hidden-divergence/) ·
[TradingSim: Divergence](https://www.tradingsim.com/blog/divergence) ·
[Alchemy Markets: Bullish Divergence](https://alchemymarkets.com/education/strategies/bullish-divergence/) ·
[Alchemy Markets: Hidden Bullish](https://alchemymarkets.com/education/strategies/hidden-bullish-divergence/) ·
[Alchemy Markets: Hidden Bearish](https://alchemymarkets.com/education/strategies/hidden-bearish-divergence/) ·
[Strike.money: Hidden Divergence](https://www.strike.money/technical-analysis/hidden-divergence) ·
[Kavout: RSI vs MACD divergence](https://www.kavout.com/market-lens/rsi-divergence-explained-bullish-and-bearish-signals-and-how-it-differs-from-macd-divergence) ·
[BrokerRank: Divergence Strategy](https://brokerrank.net/guides/divergence-trading-strategy)

**Консенсус и ⚠ РАСХОЖДЕНИЕ В НАЗВАНИЯХ.** Классика знает ЧЕТЫРЕ вида: обычная бычья
(цена ниже — индикатор выше), обычная медвежья, скрытая бычья (цена выше — индикатор
ниже, продолжение) и скрытая медвежья. **То, что курс зовёт "конвергенцией", классика
зовёт "обычной бычьей дивергенцией"** — курс это сам и оговаривает скобкой на стр. 65.
Скрытых дивергенций курс не знает вовсе.

**В коде:** `factors.DivergenceKind` — шесть значений: `DIVERGENCE`, `CONVERGENCE`
(имена курса) плюс `HIDDEN_BEARISH`, `HIDDEN_BULLISH`, `EXTENDED_BEARISH`,
`EXTENDED_BULLISH` (имена классики).

**Статус:** СОВПАДАЕТ по механике; **скрытые и расширенные виды — НАШЕ ДОБАВЛЕНИЕ из
классики**, курс их не называет. Они помечаются отдельно и на сигнал не влияют.

### 4.4. Полосы Боллинджера и сжатие

**Курс:** «Мы их используем, чтобы понять как скоро будет выход цены из накопления. Чем
сильнее сузились эти линии 
- тем быстрее будет выход из структуры» (стр. 68).

**Внешние (10):** [StockCharts: Bollinger Band Squeeze](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/bollinger-band-squeeze) ·
[TradingView: Bollinger BandWidth](https://www.tradingview.com/support/solutions/43000501972-bollinger-bandwidth-bbw/) ·
[Trading Setups Review](https://www.tradingsetupsreview.com/bollinger-squeeze/) ·
[LuxAlgo: Squeeze then Surge](https://www.luxalgo.com/blog/bollinger-bands-strategy-squeeze-then-surge/) ·
[Forex Training Group](https://forextraininggroup.com/catching-breakouts-with-the-bollinger-band-squeeze/) ·
[WallStreetMojo](https://www.wallstreetmojo.com/bollinger-band-squeeze/) ·
[Think Capital](https://www.thinkcapital.com/bollinger-bands-squeeze-strategy/) ·
[BrokerAnalysis](https://brokeranalysis.com/blog/bollinger-bands-guide-squeeze-breakouts-trading-strategies/) ·
[TradingView (TR)](https://tr.tradingview.com/chart/ADANIPORTS1%21/PcObNOUB-Adani-Ports) ·
[TradingView (IT)](https://it.tradingview.com/chart/ADANIPORTS1%21/PcObNOUB-Adani-Ports)

**Консенсус:** Джон Боллинджер, 1980-е; период 20, отклонение 2σ; сжатие — узкая
BandWidth, у Боллинджера ориентир «меньше 4% от цены»; за сжатием следует расширение.

**В коде:** `indicators.bbands_upper/middle/lower(period=20, dev=2.0)`;
`factors.SqueezeFactor` — сжатие как перцентиль СОБСТВЕННОЙ истории символа, а не как
абсолютные 4% (§4.1 FOUNDATION запрещает абсолютные пороги).

**Статус:** СОВПАДАЕТ по механике. **Форма порога отличается от классической намеренно** —
это требование §4.1, а не расхождение по недосмотру.

### 4.5. Скользящие средние 200

**Курс:** «заходим в настройка МА и ЕМА и выбираем длина 200» и «если местоположение этих
скользящих или одной из них совпадает с нашей точкой 
входа/выхода, то для нас это дополнительный фактор, который усиливает вероятность
отработки уровня» (стр. 69).

**Внешние (9):** [ChartMill: Using the 200 SMA](https://www.chartmill.com/documentation/technical-analysis/indicators/458-using-the-200-simple-moving-average-200-sma) ·
[Trade-Ideas: 200 Day SMA](https://www.trade-ideas.com/help/filter/SMA200/) ·
[GraniteShares: 200 MA Strategy](https://graniteshares.com/research/the-200-moving-average-strategy-explained/) ·
[Benzinga](https://www.benzinga.com/money/200-day-moving-average) ·
[TradeAlgo: Moving Averages](https://www.tradealgo.com/trading-guides/technical-analysis/moving-averages-guide) ·
[ChartingLens: SMA vs EMA](https://chartinglens.com/blog/sma-vs-ema-moving-averages) ·
[WaveAlert: SMA200](https://www.wavealert.app/en/features/200-day-moving-average) ·
[TradingView (TR)](https://tr.tradingview.com/chart/ETHUSD/ejITRAHt-200-DAILY-SIMPLE-MOVING-AVERAGE) ·
[TradingView (IT)](https://it.tradingview.com/chart/ETHUSD/ejITRAHt-200-DAILY-SIMPLE-MOVING-AVERAGE)

**Консенсус:** 200 — приближение торгового года; SMA(200) — самый наблюдаемый уровень на
рынке. Курс требует ОБЕ линии — и SMA, и EMA; классика чаще берёт одну.

**В коде:** `indicators.sma(200)` и `ema(200)`; `factors.MaTouchFactor` — фактор совпадения
входа с любой из двух («или одной из них» в тексте курса).

**Статус:** СОВПАДАЕТ. ⚠ Вторая линия — сглаживание SMA 5 — видна на скриншоте диалога
настроек (стр. 69) и НЕ реализована: включённость галки со снимка не определяется, а текст
страницы велит выбрать только длину 200 (§3.1 FOUNDATION).

### 4.6. Трендовые линии по RSI

**Курс:** «RSI можно использовать, что бы найти наличие дивергенций/конвергенций. Также
можно смотреть трендовые 
линии» (стр. 67).

**Статус:** **НЕ РЕАЛИЗОВАНО.** Курс формулирует опционально, и у детектора линий по
точкам индикатора нет ни одного параметра с референтом: ни числа точек, ни допуска
касания, ни правила выбора точек — ни в курсе, ни в классике числом.

### 4.7. ATR — понятие НЕ из курса

**Курса нет.** Слова ATR в курсе не встречается ни разу. Величина введена §4.1 FOUNDATION:
пороги выражаются в кратных ATR, а не в абсолютных процентах, потому что 12% ширины
означают разное для BTC и для мем-коина.

**Внешние (10):** [StockCharts: ATR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr) ·
[StockCharts: ATR and ATRP](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr-and-average-true-range-percent-atrp) ·
[TradingView: ATR](https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/) ·
[Macroption: ATR calculation](https://www.macroption.com/atr-calculation/) ·
[Steema: ATR function](https://www.steema.com/docs/financialFunctionsRef/ATRFunction.htm) ·
[HowTheMarketWorks](https://www.howthemarketworks.com/rewrites/average-true-range-atr/) ·
[Zaye Capital Markets](https://zayecapitalmarkets.com/what-is-the-average-true-range/) ·
[Grokipedia: Average true range](https://grokipedia.com/page/Average_true_range) ·
[LuxAlgo: ATR stop-loss](https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/) ·
[TradingView: ATR multiple history](https://de.tradingview.com/script/pAUEytKL-ATR-multiple-history)

**Консенсус:** Уайлдер, 1978. True Range — максимум из трёх величин (текущий диапазон,
|хай − пред. закрытие|, |лой − пред. закрытие|); ATR — уайлдеровское сглаживание TR за 14
периодов.

**Статус:** **ВНЕ КУРСА, взято из классики по §0.2** — и это законно, потому что §4.1
требует относительной формы порогов, а курс формы не задаёт.

---

# §5. Данные

Курс об этом не говорит ничего: он написан для человека с TradingView. Всё здесь — из
документации бирж и библиотек.

➡ **Про сам транспорт — откуда берутся сделки, где у каждого источника граница и почему
живой контур не ждёт архива — отдельный раздел §11.** Он добавлен позже, после того как
владелец назвал прежнюю схему ключевой архитектурной ошибкой.

### 5.1. Бар (свеча) OHLCV

**Внешние (10):** те же, что в 1.1 (анатомия свечи).

**Консенсус:** тело — между open и close; тени — от краёв тела до high и low. Объём —
пятая величина, к самой свече не относящаяся.

**В коде:** `models.Bar` — `open_ms`, `open/high/low/close`, `volume`; поле `open_ms`
именно ОТКРЫТИЯ, а моменты появления величин считаются по ЗАКРЫТИЮ (`bars.is_closed`).

**Статус:** СОВПАДАЕТ.

### 5.2. Закрытый бар и сетка таймфрейма

**Внешние (7):** [CCXT issue 6742: volume still changing after closing time](https://github.com/ccxt/ccxt/issues/6742) ·
[CCXT issue 4136: since + limit](https://github.com/ccxt/ccxt/issues/4136) ·
[CCXT issue 4165](https://github.com/ccxt/ccxt/issues/4165) ·
[CCXT issue 12855](https://github.com/ccxt/ccxt/issues/12855) ·
[CCXT issue 19900](https://github.com/ccxt/ccxt/issues/19900) ·
[Freqtrade issue 3993](https://github.com/freqtrade/freqtrade/issues/3993) ·
[Manuel Levi: more data with ccxt](https://manuellevi.com/how-to-get-more-data-price-data-using-ccxt/)

**Консенсус:** `fetchOHLCV` возвращает последней НЕЗАКРЫТУЮ свечу, и её надо отбрасывать;
без параметра `since` поведение зависит от момента внутри текущей свечи.

**В коде:** `bars.is_closed()`, `closed_only()`, `expected_last_closed_open_ms()`;
`grid_anchor_ms` — недельная сетка привязана к четвергу (эпоха Unix — четверг).

**Статус:** СОВПАДАЕТ. Заглядывание вперёд запрещено гейтом чистоты.

### 5.3. aggTrade — агрегированная сделка

**Внешние (9):** [Binance: Compressed Aggregate Trades List (USDⓈ-M)](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Compressed-Aggregate-Trades-List) ·
[Binance: Aggregate Trade Streams (USDⓈ-M)](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams) ·
[Binance: Market Data endpoints](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints) ·
[Binance: WebSocket market data requests](https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/market-data-requests) ·
[Binance: Aggregated Trades (Alpha)](https://developers.binance.com/docs/alpha/market-data/rest-api/aggregated-trades) ·
[Binance.US API docs](https://docs.binance.us/) ·
[DeepWiki: go-binance aggregate trades](https://deepwiki.com/ccxt/go-binance/9.5-aggregate-trades-and-price-tickers) ·
[hexdocs: Binance.Market](https://hexdocs.pm/binance/Binance.Market.html) ·
[Binance API change log](https://binance-docs.github.io/apidocs/delivery_testnet/en/)

**Консенсус и ⚠ РАЗНИЦА СПОТА И ФЬЮЧЕРСОВ:** на споте агрегируются сделки, закрытые
одновременно, одним тейкер-ордером, по одной цене. **На деривативах правило другое:
сделки, закрывшиеся в пределах 100 мс по одной цене и с одной стороны тейкера.** Система
работает на USDⓈ-M, то есть действует вторая формулировка.

**В коде:** `models.RawTrade`, `models.BarBinnedTrades`; `archive.py` качает суточные
архивы `data.binance.vision/data/futures/um/daily`; `exchange.fetch_agg_trades_from()`
доборает разрывы по `aggId`.

**Статус:** СОВПАДАЕТ. Профиль строится по СДЕЛКАМ, а не по объёму бара — это вернее
самого инструмента TradingView, и решение записано в
[`docs/audit/tv-transfer-2026-08-09.md`](audit/tv-transfer-2026-08-09.md).

### 5.4. tickSize (шаг цены)

**Внешние (9):** [Binance: Filters](https://developers.binance.com/docs/binance-spot-api-docs/filters) ·
[Binance: Exchange Information (COIN-M)](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Exchange-Information) ·
[Binance: Common Definition](https://developers.binance.com/docs/derivatives/portfolio-margin/common-definition) ·
[binance-us-api-docs: filters.md](https://github.com/binance-us/binance-us-api-docs/blob/master/filters.md) ·
[sammchardy: Binance Order Filters](https://sammchardy.github.io/binance-order-filters/) ·
[Binance Academy: Price Filter](https://www.binance.com/en/academy/articles/binance-api-responses-price-filter-and-percent-price) ·
[Medium: Binance Futures client](https://medium.com/@joaotx/building-a-binance-futures-client-in-python-part-ii-fc5fc644b6ab) ·
[binance-api-node PR 649](https://github.com/ViewBlock/binance-api-node/pull/649) ·
[Wikipedia: Commodity tick](https://en.wikipedia.org/wiki/Commodity_tick)

**Консенсус:** `tickSize` из фильтра `PRICE_FILTER` задаёт шаг, которым может меняться
цена; условие корректности — `(price − minPrice) % tickSize == 0`.

**В коде:** `models.tick_scale()`, `models.bin_index()`; `models.Instrument.tick`;
`exchange.TickChange` — смена шага биржей отслеживается отдельным событием.

**Статус:** СОВПАДАЕТ. Шаг цены — основание сетки профиля (см. 2.7).

### 5.5. Бессрочный фьючерс USDⓈ-M

**Внешние (6):** [Binance: Funding rates of USDⓈ-M Perpetual](https://www.binance.com/en/support/announcement/detail/98d6b24d3e5c4f84a8ed04087997d8d0) ·
[Binance Academy: What Are Funding Rates](https://www.binance.com/en/square/post/11894481059057) ·
[Binance Academy: funding rates (2)](https://www.binance.com/en/square/post/13424184363930) ·
[Elm Wealth: Perpetual Futures](https://elmwealth.com/perpetual-futures/) ·
[Medium: Inside the Perpetual](https://medium.com/@joaotx/inside-the-perpetual-the-mechanics-of-funding-rates-3896384695c7) ·
[Binance Futures Services Agreement](https://www.scribd.com/document/982509567/Binance-Futures)

**Консенсус:** бессрочный контракт не имеет даты экспирации; к цене спота его удерживает
фандинг — периодический платёж между сторонами.

**В коде:** `config/universe.toml` — вселенная символов; §5 FOUNDATION ограничивает
источник публичными потоками Binance USD-M.

**Статус:** СОВПАДАЕТ. ⚠ **Сам фандинг §3 FOUNDATION запрещает** как величину: в методике
его нет. Здесь он назван только как механизм рынка, а не как признак.

### 5.6. Вселенная символов

**Курса нет.** §5 FOUNDATION: закреплённый набор символов, задаваемый оператором;
не-крипто инструменты (XAU, XAG, PAXG) исключены — микроструктурные предпосылки метода
для них не выполняются.

**В коде:** `src/hunter/config.py`, `config/universe.toml`.

**Статус:** РЕШЕНИЕ ВЛАДЕЛЬЦА, не источник.

---

# §6. Инженерные понятия проекта

Этих слов нет ни в курсе, ни в классике ТА — они введены самим проектом. Каждое отвечает
на вопрос "как проверить", а не "что торговать".

### 6.1. NotReady — отсутствие данных

**§4.3 FOUNDATION:** пропуск остаётся пропуском и доходит до карточки как явный признак
неготовности; ноль допустим только как измеренный ноль.

**В коде:** `models.NotReady` с полем `reason`; возвращается вместо величины.

⚠ **Почему этого мало.** Разбор [`docs/audit/backfill-window-2026-08-04.md`](audit/backfill-window-2026-08-04.md)
показал: каждый отдельный отказ был честным, а сто девяносто отказов подряд, все на
старших ТФ, читались как "рынок такой". **Правило: у отказов обязана быть СВОДКА по
измерению, вдоль которого возможен перекос.**

### 6.2. Гейт

Проверка, которая **падает по коду возврата**, а не печатает вывод. Печать — не гейт.
Сейчас их 22, все вписаны в `.github/workflows/ci.yml` (это проверяет
`gates/ci_covers_gates.py`: гейт, не вписанный в CI, — скрипт, а не гейт).

### 6.3. Дифф повтора (replay diff)

**§10.6 FOUNDATION, условие 2.** Единственная проверка изменения расчёта, не требующая
чтения кода: `run` сохраняет кадры и порождённую ими карточку → расчёт меняется →
`replay` строит карточку из ТЕХ ЖЕ кадров и печатает разницу.

Требует детерминированной карточки: никаких множеств, словарей в случайном порядке и
отметок времени.

### 6.4. Контроль фальсифицируемости

**Правило CLAUDE.md:** число не докладывается, пока не проверено, что прибор СПОСОБЕН
выдать иной ответ. Для "совпало N из M" нужен второй контроль — тот же замер на данных,
про которые заведомо известно, что совпадать они не должны.

⚠ Контроль показывает, что прибор не заперт в одном ответе. Он НЕ показывает, что число
верно: отвечающий прибор может быть откалиброван неправильно. Ровно этот случай — 2.2.

### 6.5. Исход сигнала

**В коде:** `outcome.OutcomeKind` — `STOP`, `TARGET`, `AMBIGUOUS` (бар задел обе цены),
`OPEN`, `NOT_FILLED` (цена до входа не дошла).

⚠ **`AMBIGUOUS` — честное признание границы прибора:** по барам нельзя узнать, что цена
задела раньше внутри бара. Замена его на "стоп" или на "цель" была бы выдумкой.

### 6.6. Допуск (tolerance)

Заранее объявленный порог, при котором реализация считается сошедшейся с источником.
Файлы `docs/audit/tolerance-*.md` — по одному на модуль.

### 6.7. Отпечаток данных

Число прогонов, окон и файлов на момент замера, записанное рядом с числом. Введено
2026-08-09: замер по растущему кэшу воспроизводится только до первой доливки данных, и
без отпечатка дрейф выборки читается как опровержение.

---

# §7. Сводка: где код опирается не на источник

Это главная таблица файла. В ней нет ни одного пункта, который был бы ошибкой сам по
себе, — но каждый есть место, где ошибиться можно и никто не заметит.

| # | что | почему так | цена ошибки |
|---|---|---|---|
| 2.2 | **граница базы = внешний край пары** | слово "хай" в стр. 18; за три дня прочтение менялось дважды | сдвигает ВСЁ: окно профиля, ПОК, стоп, РР |
| 2.9 | свинг = фрактал Вильямса на 5 барах | курс окна не даёт; три платформенные справки сходятся | число структур меняется от 186 до 470 |
| 2.7 | число строк профиля | курс не называет; снято с чужих скриншотов, разброс 24…≥95 | цена ПОК, то есть цена входа |
| 2.8 | объёмная зона = Value Area 70% | курс рисует зону и доли не называет | ширина зоны входа |
| 3.4 | запас 1–3% — процент ОТ ЦЕНЫ | курс не говорит, чего проценты | на узкой структуре разница в разы |
| 3.4 | потолок запаса по высоте базы | наше решение 2026-08-11; в курсе числа нет | РР каждого сигнала на младших ТФ |
| 2.3 | точка границы = фрактал | курс не говорит, чем распознавать точку | сколько структур вообще найдётся |
| 2.15 | равные доли на лестнице | курс называет цель, долей не задаёт | средняя ТВХ |
| 2.5 | подтверждение выхода = 2 тела | правило стр. 55 про уровень ПП перенесено на выход | когда появляется уровень |
| 1.21 | тренд по одной стороне | так у курса; классика требует обеих | направление приоритета |

**Не реализовано при существующем правиле курса:** расширение базы проколами (2.4),
вход от 2-3 касания по слому на младшем ТФ (2.12), лимитный барьер (2.19), фигуры (2.20),
трендовые линии по RSI (4.6).

---

# §8. Что осталось непроверенным

Раздел обязателен по §7.6 FOUNDATION. Здесь то, чего этот файл НЕ делает.

1. **Правила, выраженные только рисунком, сверены по конспектам, а не заново по
   пикселям.** Нумерация точек границ, ширина лимитного барьера, ширина объёмной зоны на
   стр. 30 — всё это читается с картинок. Пиксельный зонд стр. 13 (2026-08-11) разрешения
   спора о границе НЕ дал: первые две точки там лежат почти на одном уровне.

2. **Главный вопрос 2.2 не закрыт.** Чтобы его закрыть, нужен замер против РАЗМЕТКИ
   АВТОРА на корпусе: взять структуры, которые автор обвёл на видео, и сравнить границы
   при обоих прочтениях. Такой замер не проведён.

3. **Корпус прочитан грепом, а не целиком.** 15 транскриптов, цитаты отбирались по
   вхождению термина. Утверждение "автор всегда делает так" на такой выборке не строится.

4. **Внешние источники — вторичные.** Первоисточники (Уайлдер 1978, Стейдлмайер, Вайкофф,
   Балковски) не читались; читались справки платформ и обзоры, которые на них ссылаются.
   Для конвенций типа Б по §0.2 этого достаточно по счёту источников, для спорных мест —
   нет.

5. **Ни одно число этого файла не является замером.** Всё количественное здесь —
   пересказ уже проведённых замеров со ссылкой на протокол. Новых замеров при составлении
   файла не делалось.

6. **Термины корпуса, которых нет в курсе, разобраны в §9** (дополнение 2026-08-11).
   Из одиннадцати восемь не реализованы, и два из них — **не реализуемы при нынешних
   данных**, а не просто отложены: внешняя ликвидность (9.4) и тепловая карта
   ликвидаций (9.5) требуют стакана и данных о плечевых позициях, которые §3 FOUNDATION
   запрещает поимённо. Разница между «не сделано» и «сделать нечем» существенная.

7. **Раздел §9 собран грепом по корпусу, а не сплошным чтением.** Термин, который автор
   употребил один раз и не в тех словах, которые я искал, в список не попал. Полный
   словарь автора потребовал бы прочесть 15 транскриптов целиком.

---

# §9. Термины АВТОРА, которых в курсе нет

Раздел добавлен 2026-08-11. Курс — мини-курс; на живых разборах автор пользуется словами,
которых в нём нет ни разу. Их проверка тем важнее: слово, которого нет в курсе, попадает
в код только через мою догадку, а догадку проверить нечем, кроме внешнего источника.

⚠ Ни один термин этого раздела в расчёте НЕ участвует. Раздел существует, чтобы
непокрытое было видно списком, а не всплывало по одному.

### 9.1. Поджатие / сужение

**Курса нет.** Ближайшее, что курс говорит, — про форму стопового объёма: «Стоповый объем
может быть в сужении, в поджатии, с четкими границами» (стр. 34). То есть слово названо,
а определения ему не дано.

**Корпус:** *[prizrak_astr_razbor]* "снизу мы видим вот такое вот активное поджатие, снизу
ликвидность копится." · *[overcarder_btc_eth_2026-07-27]* "эта ловушка уже не один раз
давала поддержку, и есть поджатие" · *[prizrak_alts_10_overview]* "останутся в сужении, на
ключевых поддержках".

**Внешние (9):** [TrendSpider: VCP](https://trendspider.com/learning-center/volatility-contraction-pattern-vcp/) ·
[TraderLion: Mastering VCP](https://traderlion.com/technical-analysis/volatility-contraction-pattern/) ·
[TradingSim: VCP guide](https://www.tradingsim.com/blog/volatility-contraction-pattern) ·
[Deepvue: tight VCP consolidations](https://deepvue.com/screener/volatility-contraction-pattern/) ·
[Finer Market Points: Minervini VCP](https://www.finermarketpoints.com/post/what-is-a-vcp-pattern-mark-minervini-s-volatility-contraction-pattern-explained) ·
[Ultima Markets](https://www.ultimamarkets.com/academy/volatility-contraction-pattern-vcp-explained/) ·
[Defcofx](https://www.defcofx.com/volatility-contraction-pattern/) ·
[CLT Academy](https://clt-academy.com/blogs/rare-trading-strategy-volatility-contraction-pattern-vcp-in-breakout-trading) ·
[TradingView: VCP breakout strategy](https://in.tradingview.com/chart/BSE/0ZlXMYhd-Volatility-Contraction-Pattern-VCP-Breakout-Strategy-Explained/)

**Консенсус:** ближайший названный аналог — Volatility Contraction Pattern Марка Минервини:
серия всё более мелких откатов с падающим объёмом, «сжатая пружина» перед выходом. Классика
даёт и число — откаты вида 18% → 12% → 6%, то есть каждый примерно вдвое меньше прежнего.

**В коде:** прямого механизма нет. Косвенно есть `BoundaryZone.narrowed` — счётчик того,
сколько раз граница сдвинулась ВНУТРЬ (`accumulation.py`). Это признак сужения, но не
фигура: он считает сдвиги, а не проверяет убывание амплитуды.

**Статус:** **НЕ РЕАЛИЗОВАНО.** Референт у величины появился бы легко (Минервини даёт
числа), но курс их не подтверждает, а §0 требует источника методики, а не любой классики.
Решение о внесении — владельца.

### 9.2. Плотный и размазанный уровень

**Курса нет.** Курс говорит только о СИЛЕ уровня через ТФ и объём (стр. 22) и о форме
стопового объёма — «обычно более «плотное» (часто большой объем в мелком диапазоне)»
(стр. 34).

**Корпус:** *[prizrak_alts_10_overview]* "Первая плотненький уровень, вторая немножко
размазанная, вторую лучше смотреть под фьючи по факту, первую, в принципе, можно вкинуть
какую-то лимитку." — **разная плотность меняет способ входа: по плотному ставится лимитка,
по размазанному вход по факту.**

**Внешние (9):** [OrderFlow Labs: HVN, LVN and Value](https://orderflowlabs.com/blogs/theblog/volume-profile-guide) ·
[Angel One: High Volume Nodes](https://www.angelone.in/knowledge-center/online-share-trading/high-volume-nodes-hvn) ·
[Chart Champions: Volume Profile guide](https://chartchampions.com/volume-profile-guide-for-beginners/) ·
[Trading Wyckoff: Volume Profile](https://tradingwyckoff.com/en/volume-profile-2/) ·
[TradeZella: Volume profile](https://www.tradezella.com/learning-items/volume-profile) ·
[TradingSim: Volume Profile strategies](https://www.tradingsim.com/blog/advanced-day-trading-strategies-using-volume-profile) ·
[JumpStart Trading](https://www.jumpstarttrading.com/volume-profile/) ·
[Quantum-Algo: Volume Profile guide](https://www.quantum-algo.com/blog/guides/volume-profile-trading-complete-guide/) ·
[WH SelfInvest: Volume Profile tool](https://www.whselfinvest.com/en-lu/trading-platform/store/trading-strategies/daytrading-volume-profile)

**Консенсус:** это **High Volume Node и Low Volume Node**. HVN — пик гистограммы, область
принятия цены: цена там тормозит и пилит. LVN — провал, область отвержения: цена проходит
быстро. Соответствие с автором прямое: «плотный» = HVN, «размазанный» = профиль без
выраженного пика.

**В коде:** гистограмма профиля считается (`volume_profile.TVProfile`), но **из неё берётся
только ПОК и зона**; узлы не выделяются, плотность не измеряется и в карточку не попадает.

**Статус:** **НЕ РЕАЛИЗОВАНО, и это самый близкий к внедрению пункт раздела:** данные уже
есть, внешний термин канонический, а различие меняет РЕШЕНИЕ автора (лимитка против входа
по факту). Не внесено потому, что курс порога плотности не даёт, а §0 запрещает число без
референта.

### 9.3. Живой уровень

**Курса нет.** Родственное правило есть — отработанный уровень (стр. 25, см. 2.12).

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "я бы лимитки с точки зрения шортов
закидывал только туда, где есть живые уровни."

**Статус:** по смыслу это `LevelState.ACTIVE` — уровень, ещё не отработанный на первое
касание. **Реализовано под другим именем**; отдельного понятия не нужно.

### 9.4. Ликвидность внешняя и внутренняя

**Курса нет.** Слово «ликвидность» в курсе есть дважды и в другом смысле: у ПОК —
«максимальный уровень ликвидности» (стр. 21) и у маркетмейкера — «обеспечивать
ликвидность» (стр. 8).

**Корпус:** *[prizrak_astr_razbor]* "актив двигается в боковике, работает с внешней
ликвидностью, а снизу мы видим вот такое вот активное поджатие, снизу ликвидность
копится." · *[prizrak_astr_razbor]* "ликвидность у нас как и здесь копится, так и снизу".

**Внешние (10):** [Inner Circle Trader: Internal & External Range Liquidity](https://innercircletrader.net/tutorials/ict-internal-external-liquidity/) ·
[Liquidity Provider: Internal vs External Range Liquidity](https://liquidity-provider.com/articles/internal-vs-external-range-liquidity-in-ict-trading/) ·
[Inner Circle Traders: what smart money targets](https://innercircletraders.net/ict-internal-vs-external-range-liquidity/) ·
[ICT Killzone: liquidity](https://www.ictkillzone.com/ict-liquidity) ·
[Aron Groups: liquidity in ICT](https://arongroups.co/technical-analyze/liquidity-in-ict/) ·
[Liquidity Provider: buy side and sell side](https://liquidity-provider.com/articles/buy-side-liquidity-and-sell-side-liquidity-what-are-they/) ·
[Writo Finance](https://www.writofinance.com/buy-side-and-sell-side-liquidity-ict-and-smc/) ·
[Inner Circle Trader: liquidity pool](https://innercircletrader.net/tutorials/ict-liquidity-pool/) ·
[Inner Circle Trader: liquidity in forex](https://innercircletrader.net/tutorials/liquidity-in-forex-trading/) ·
[OpoFinance: ICT liquidity pool trading](https://blog.opofinance.com/en/ict-liquidity-pool-trading/)

**Консенсус:** термин из Smart Money Concepts. Внешняя (ERL) — за границами диапазона, у
прежних хая и лоя, где стоят стопы; внутренняя (IRL) — внутри диапазона. «Ликвидность
копится» значит «скапливаются стоп-заявки», то есть речь о НЕИСПОЛНЕННЫХ заявках.

⚠ **И потому в системе этого быть не может.** §3 FOUNDATION запрещает стакан и его метрики
прямо, а §5 ограничивает источник публичными потоками сделок. Скопление стопов по
совершённым сделкам не наблюдаемо в принципе.

**Статус:** **НЕ РЕАЛИЗУЕМО при нынешних данных**, а не просто не реализовано. Это разные
вещи, и разница здесь существенная.

### 9.5. Тепловая карта ликвидаций

**Курса нет.**

**Корпус:** отдельный разбор `prizrak_btc_heatmap` целиком построен на ней.

**Внешние (7):** [Zipmex: What Is a Liquidation Heatmap](https://zipmex.com/blog/what-is-a-liquidation-heatmap/) ·
[B2Broker](https://b2broker.com/news/how-to-use-the-bitcoin-liquidation-heatmap/) ·
[Webopedia](https://www.webopedia.com/crypto/learn/crypto-liquidation-heatmap-explained/) ·
[Kalena](https://blog.kalena.ai/liquidation-heatmap-where-leveraged-positions-go-to-die-and-how-smart-traders-get-there-first) ·
[Trading Different](https://tradingdifferent.com/) ·
[StopSaving](https://www.stopsaving.com/liquidation-heatmap-crypto-explained/) ·
[Quadcode](https://quadcode.com/blog/bitcoin-liquidation-heatmap-and-how-to-use-it-for-profitable-trading)

**Консенсус:** оценка уровней, где скопились ликвидационные цены плечевых позиций.
Считается по данным нескольких бирж о позициях и плече — то есть по данным, которых у нас
нет и которые §3 FOUNDATION запрещает (ликвидации перечислены там поимённо).

**Статус:** **НЕ РЕАЛИЗУЕМО**, как и 9.4.

### 9.6. Доминация: BTC.D, USDT.D, TOTAL3

**Курса нет.**

**Корпус:** *[overcarder_btc_eth_2026-07-27]* — стейблкоины и доминация разбираются в
первые же минуты; кадр `stables-dominance-0803.png` в свидетельствах.

**Внешние (8):** [TradingView: BTC.D](https://www.tradingview.com/symbols/BTC.D/) ·
[TradingView: TOTAL3](https://www.tradingview.com/symbols/TOTAL3/) ·
[TradingView: TOTAL](https://www.tradingview.com/symbols/TOTAL/) ·
[TradingView: TOTAL3ES](https://www.tradingview.com/symbols/TOTAL3ES/) ·
[TradingView: где искать капитализацию и доминацию](https://www.tradingview.com/support/solutions/43000550480-where-do-i-find-crypto-market-capitalization-and-dominance/) ·
[TradingView: график доминации](https://www.tradingview.com/markets/cryptocurrencies/dominance/) ·
[Ledger Academy: TOTAL3](https://www.ledger.com/academy/glossary/total3) ·
[TradingView: идеи по TOTAL3](https://www.tradingview.com/symbols/TOTAL3/ideas/?sort=recent&video=yes)

**Консенсус:** BTC.D — доля капитализации биткоина в общей (по топ-125 монетам у
TradingView); TOTAL3 — капитализация без BTC и ETH. У BTC.D названный изъян: знаменатель
включает и стейблкоины, поэтому приток стейблов сам по себе двигает показатель.

**Статус:** **НЕ РЕАЛИЗОВАНО, запрещено §3 FOUNDATION поимённо** («доминация BTC.D/TOTAL3 ·
капитализация»). Автор ими пользуется — значит это осознанный разрыв с методикой, и он
записан здесь, а не умолчан.

### 9.7. База-переприор

**Курса нет.** Курс даёт ПП как уровень (стр. 50, 55) и базу как структуру (стр. 22)
раздельно.

**Корпус:** *[prizrak_bch_praktikum]* "здесь эта структура называется база переприор, когда
у нас вся база является уровнем смены приоритета, точнее, ее границы."

**Внешние:** ближайшее в классике — двойное или тройное дно, и курс сам проводит эту связь:
«По сути - это просто накопление, но граница накопления являлась сломом структуры -
"переприором"» (стр. 62). То есть у автора это НЕ новый термин, а имя для случая со стр. 62.

**Статус:** **НЕ РЕАЛИЗОВАНО** — вместе с фигурами (2.20). Механизм требует связать
`accumulation` и `pereprior`, а §2.5 в расчёт сигнала не входит вовсе (находка А-02).

### 9.8. Канальная структура

**Курса нет** — курс знает только ГОРИЗОНТАЛЬНЫЕ границы (стр. 18, 22).

**Корпус:** *[prizrak_btc_eth_keyzone]* "Биток проколол вот эту канальную структуру, но
первого выхода как такового еще не было."

**Статус:** **НЕ РЕАЛИЗОВАНО и исключено по построению** — та же причина, что у вымпела и
клина (2.20): детектор выражает только горизонтальные границы.

### 9.9. Зона интереса

**Курса нет.**

**Корпус:** *[prizrak_alts_10_overview]* "протестировали верхнюю границу, верхнюю зону
интереса текущего боковика" · *[overcarder_btc_eth_2026-07-27]* "совпадают с критической
зоной по рынку в целом".

**Внешние (7):** те же, что в 2.8 про зоны спроса и предложения.

**Статус:** у автора это, судя по употреблению, синоним объёмной зоны у границы. Отдельного
понятия не заводится: **словарь не расширяется догадкой** (§0).

### 9.10. Проторговка, наторговка, расторговал

**Курс даёт корень:** ПОК — «максимальный уровень проторговки базы» (стр. 21), и «в
маленькой часовой наторговке может быть объем больше» (стр. 22). Глагола «расторговал» в
курсе нет.

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "вот этот уровень, его стоповый расторговали,
вот этот уровень и тест вот этой штуки расторговал CME." · *[prizrak_bch_praktikum]*
"проторговался под уровнем слома".

**Внешние (8):** [Fisdom: Congestion Area](https://www.fisdom.com/glossary/congestion-area/) ·
[Angel One: Congestion Area or Pattern](https://www.angelone.in/finance-wiki/trading-terms/congestion-area-or-pattern) ·
[The Robust Trader: What Is Congestion](https://therobusttrader.com/what-is-a-congestion-in-trading/) ·
[Financial Dictionary: congestion area](https://financial-dictionary.thefreedictionary.com/congestion+area) ·
[CMLviz: Congestion Lines](https://patternfinderhelp.cmlviz.com/docs/congestion-lines-points) ·
[Edwards & Magee, Technical Analysis of Stock Trends (8-е изд.)](http://anyflip.com/gfisv/hsoi/basic/201-250) ·
[TradingView: congestion script](https://jp.tradingview.com/script/WKcCPH92) ·
[TradingView: тот же скрипт](https://il.tradingview.com/script/WKcCPH92)

**Консенсус:** классический термин — **congestion area** (Эдвардс и Мэйги): боковой участок
с убывающим объёмом и без расширения диапазона, где спрос равен предложению. Совпадает с
«проторговкой» автора. «Расторговал уровень» — глагол того же корня: цена построила на
уровне такую область и тем его исчерпала.

**В коде:** «проторговка» = то, по чему строится профиль; «расторговал» ближе всего к
`LevelState.WORKED_OFF` плюс `PressedStructure` (структура, прижатая к уровню).

**Статус:** СОВПАДАЕТ по смыслу, отдельного механизма не требует.

### 9.11. CME и гэп CME

**Курс определяет площадку:** «СМЕ (Chicago Mercantile Exchange) – глобальная
международная торговая площадка деривативов» (стр. 8). **Про ГЭП курс не говорит ничего.**

**Корпус:** *[overcarder_btc_eth_2026-07-27]* "тест вот этой штуки расторговал CME".

**Внешние (9):** [Phemex: CME Gap Crypto Explained](https://phemex.com/academy/cme-futures-gap) ·
[Whaleportal: how to trade BTC CME gaps](https://whaleportal.com/blog/bitcoin-cme-gaps-and-cme-trading-strategy-explained/) ·
[CoinDesk: CME gap to the upside](https://www.coindesk.com/markets/2026/02/02/futures-price-gap-on-cme-offers-bitcoin-bulls-a-glimmer-of-hope) ·
[CoinDesk: CME переходит на 24/7](https://www.coindesk.com/markets/2026/05/28/bitcoin-s-famous-cme-gaps-are-about-to-disappear-though-three-remain-unresolved) ·
[CryptoSlate: всегда ли гэпы закрываются](https://cryptoslate.com/do-cme-gaps-always-have-to-fill-bitcoins-60k-flush-says-no/) ·
[Ki Ecke: how to spot fills](https://ki-ecke.com/crypto-insights/cme-bitcoin-futures-gap-explained-how-to-spot-fills/) ·
[Complete Trader's Edge](https://completetradersedge.com/cme-gaps-trading-guide/) ·
[Yahoo Finance: первый понедельник без гэпа](https://finance.yahoo.com/markets/crypto/articles/bitcoin-first-cme-gap-free-174649486.html) ·
[IndexBox](https://www.indexbox.io/blog/cme-bitcoin-futures-open-lower-after-weekend-slide-creating-pricing-gap/)

**Консенсус и ⚠ ВАЖНОЕ ИЗМЕНЕНИЕ РЫНКА:** гэп CME возникал оттого, что фьючерс не торговался
по выходным, а спот торговался. Приводимая доля закрытия — около 77%. **Но с мая 2026 CME
перевела биткоин-фьючерсы на круглосуточную торговлю, и выходные гэпы перестали
образовываться.** То есть признак, которым автор пользуется в разборе от 27.07, к моменту
составления этого файла уже исторический.

**Статус:** **НЕ РЕАЛИЗОВАНО.** Требует данных CME — другой площадки; §5 FOUNDATION задаёт
единственный источник. И вносить было бы поздно: механизм гэпа исчез вместе с расписанием.

---

# §10. Понятия, введённые самим проектом

Их нет ни в курсе, ни в классике: они появились из инженерных решений. Каждое здесь
названо, потому что «буквально каждое» — это и они тоже.

| понятие | где | что означает |
|---|---|---|
| **Лесенка границ** | `accumulation.BorderSource.LADDER` | ребро прежней структуры служит ребром следующей. Из рисунка стр. 40, где обе коробки подписаны одним ТФ |
| **Сужение границы** | `BoundaryZone.narrowed` | сколько раз граница сдвинулась ВНУТРЬ. Признак формы стопового объёма (стр. 34), см. 9.1 |
| **Сквозной номер точки** | `MIN_PUNCTURE_ORDINAL` | точки нумеруются через обе стороны, как на схеме стр. 18. Определяет, с какой точки прокол идёт в стоп |
| **Открытая структура** | `accumulation.OpenStructure` | границы набраны, выхода ещё не было. Уровня по стр. 23 нет, но молчать о ней нельзя: замер 2026-08-04 дал структуру, не закрывавшуюся 316 баров |
| **Прижатая структура** | `levels.PressedKind` | накопление у уровня: под ним, над ним или верхом на нём. Три случая со стр. 28 (пункты 2, 5, 7) |
| **Правило входа** | `levels.EntryRule` | чем разрешён вход: лимиткой, по подтверждению слома на младшем ТФ (стр. 25) или ретестом флипнутого (стр. 43) |
| **Якорь стопа** | `levels.StopAnchorSource` | за что прячется стоп: прокол, стоповый объём или свинг. Все три из одной фразы стр. 18 |
| **Роль цели** | `geometry.TargetRole` | основная (свой ТФ или старший) или промежуточная (ТФ−1), по стр. 24 |
| **ПП-сетап** | `geometry.PPSetup` | сделка от переприора: вход тестом ПП, стоп за хай или лой места слома (стр. 50) |
| **Расширение профиля** | `volume_profile.Expansion` | как набирается Value Area: парами строк или по одной. Парами — по документации TradingView |
| **Допуск по барам** | `hunter/admission.py` | сколько баров нужно величине, чтобы вообще существовать. `ema200` требует 200 — на 1Н столько истории есть не у всех символов |
| **Часы биржи** | `clock.ClockSync` | сдвиг локальных часов относительно биржевых. Своим часам система не верит |
| **Непрерывный хвост** | `bars.continuous_tail` | самый свежий кусок ряда без дыр. Расчёт идёт по нему, а не по всему собранному |
| **Разрыв потока** | `TradeSequence.gaps` | пропуск в номерах aggTrade. Считается и добирается REST-ом, а не проглатывается |
| **Корзина кэша** | `archive.CACHE_BUCKET_MS` | 5 минут — младший ТФ проекта. Срез окна по кэшу от этого ТОЧЕН, а не приблизителен |
| **Метка схемы бинирования** | `archive.CACHE_LAYOUT` | версия способа считать номер бина, вписанная в ИМЯ файла. Без неё правка бинирования обесценила бы кэш молча |
| **Частичные сутки** | `archive.part_path` | сутки, добранные не до конца: граница покрытия стоит в имени файла. Введено 2026-08-11, см. §11 |
| **Кадры прогона** | `run.persist_frames` | сырые входные данные, сохранённые отдельно от карточки. Без них повтор невозможен |
| **Состояние сигнала** | `store.signal_states` | `not_filled` (цена до входа не дошла) и `open` (сделка идёт). Схема v5 |
| **Сводка исходов** | `store.OutcomeCell` | тип × ТФ × сторона с возрастом клетки в барах. Ловит перекос, который поштучные отказы прячут |
| **Отпечаток данных** | протоколы `docs/audit/` | число окон и файлов на момент замера. Без него дрейф выборки читается как опровержение |

---

# §11. Транспорт: откуда берутся сделки

Раздел добавлен 2026-08-11 после того, как владелец назвал ключевой архитектурной ошибкой
зависимость живого контура от суточных архивов. **Курс об этом не говорит ничего** — он
написан для человека с TradingView. Всё здесь из документации Binance и ccxt и из замеров.

### 11.1. Три источника, и у каждого своя граница

| источник | что даёт | граница | замер |
|---|---|---|---|
| **Архив** `data.binance.vision` | сутки одним ZIP, sha256 приложен | **публикуется с отставанием**: 10–11 августа отдавал HTTP 404 | сутки BTC ≈ 15 МБ, секунды на закачку |
| **REST** `fapiPublicGetAggTrades` через ccxt | любое окно | глубина ≥ 30 суток по `startTime`; по `fromId` — **только 24 часа** | сутки BTC ≈ 275 страниц ≈ 6 мин |

⚠ **Шесть минут — это НЕ скорость сети и не предел биржи, а пол, заданный нашим же
ограничителем.** У ccxt `rateLimit` есть пауза в миллисекундах, умножаемая на ВЕС
эндпоинта: у `binanceusdm` умолчание 50 мс, вес `aggTrades` равен 20, значит перед каждым
запросом сделок ждётся ровно секунда. Лимит биржи при этом вдвое щедрее — 2400 веса в
минуту, то есть 120 запросов, а мы делаем 60. Разбор:
[`docs/audit/ccxt-manual-2026-08-11.md`](audit/ccxt-manual-2026-08-11.md) §1.
| **Поток** `watchTrades` ccxt.pro | сделки в реальном времени | только пока служба жива | — |

### 11.2. Дефект, который это исправило

Архив покрывал историю, поток покрывал настоящее, а **между ними была дыра в одни-двое
суток, и она двигалась вместе с календарём**. Поток живёт только с момента запуска службы,
значит после каждого перезапуска дыра открывалась заново. Структуры, чьё окно попадало в
неё, уровня не получали.

⚠ **Перекос здесь по САМОМУ ОПАСНОМУ измерению — по свежести.** Отказы были честными
поштучно (§4.3 соблюдён), но обрывалась карта именно младших ТФ и именно на свежих
структурах — то есть на тех, которые и торгуются. Это тот же класс, что разбор
[`backfill-window-2026-08-04`](audit/backfill-window-2026-08-04.md), только вдоль другой оси.

### 11.3. Что показали замеры

Три зонда, все в `docs/audit/probes/`:

1. **[probe_rest_trade_depth](audit/probes/probe_rest_trade_depth_2026-08-11.py)** — REST
   отдаёт сделки минимум 30-суточной давности. ⚠ Проверены ВРЕМЕНА сделок, а не только их
   число: прежний замер 2026-08-04 записал «вернул 1000 сделок» и тем ничего не доказал.
   Контроль пройден: час назад прибор отдаёт часовые сделки, 30 суток назад — 30-суточные.

2. **[probe_backfill_window](audit/probes/probe_backfill_window_2026-08-11.py)** — курсор
   `fromId` старше суток отклоняется кодом −1000, и это **падение, а не деградация**:
   ccxt относит его к `OperationFailed`, а тот наследуется от `BaseError` напрямую, мимо
   и `NetworkError`, и `ExchangeError`. Теперь зонд служит регресс-проверкой.

3. **[probe_rest_empty_page](audit/probes/probe_rest_empty_page_2026-08-11.py)** — ccxt для
   binance САМ дописывает `endTime = since + 3600000`, обходя правило Binance про час.
   Значит пустой ответ означает пустой ЧАС, а не конец сделок. Контроль различает случаи:
   у BTC страницу обрезал `limit` (1000 сделок за 75 с), у ARPA — время (792 сделки при
   лимите 1000, размах 3589 с, до отметки `since+1ч` остаётся 10 с).

### 11.4. Как устроено теперь

* сутки, которых нет в архиве, **добираются REST-ом** и ложатся в тот же кэш;
* сутки, добранные не до конца (текущие или оборванные), пишутся **частичным файлом с
  границей покрытия в имени** — `-part<мс>`. Полный и частичный файл различимы по имени,
  а не по содержимому, поэтому усечение не может стать молчаливым;
* глубже 30 суток REST не применяется: это **граница замера**, и за ней стоит названный
  отказ, а не попытка;
* у отказов есть **сводка по символу** (`backfill_missing_by_symbol`) — правило CLAUDE.md
  про сводку вдоль оси возможного перекоса.

### 11.5. Что при этом нельзя перепутать

**Поток и REST обязаны отдавать ОДНУ И ТУ ЖЕ агрегацию.** У ccxt.pro для binance умолчание
`watchTrades` — `'trade'`, то есть поштучные сделки, а REST и архив дают `aggTrade`. Смешать
их в одной гистограмме значит посчитать объём дважды. Проект задаёт явно:

```python
"options": {"watchTrades": {"name": "aggTrade"}}
```

⚠ **И сама агрегация у спота и деривативов РАЗНАЯ** (см. 5.3): на споте объединяются сделки
одного тейкер-ордера по одной цене, на USDⓈ-M — сделки в пределах 100 мс по одной цене и
одной стороне тейкера. Система работает на USDⓈ-M.

**Глубокая история публично доступна только через `aggTrades`.** Альтернатива
`fapiPublicGetHistoricalTrades` проверена вызовом и отпадает: `AuthenticationError,
historicalTrades endpoint requires apiKey`, а ключей у системы нет и не будет (§5, гейт
`public_data_only`).

**Встроенная пагинация ccxt не подходит как есть:** `params={'paginate': True}` уходит в
`fetch_paginated_call_dynamic`, у которого `paginationCalls` по умолчанию **10** — около
десяти часов рынка BTC вместо суток. Сверх того ccxt **не объявляет** для сделок на USDⓈ-M
никакой пагинации: `features['swap']['linear']['fetchTrades']` равно `null`.

**Поток обязан отдавать ТОЛЬКО НОВОЕ, и это закреплено явно.** У `watch_trades` есть два
режима: при `newUpdates = False` возвращается весь скользящий кэш (до `tradesLimit`
записей) на каждом вызове, при `True` — только пришедшее с прошлого раза. Наш потребитель
кладёт каждую сделку в гистограмму, значит первый режим сосчитал бы один объём десятки
раз, и ни один гейт этого не поймал бы: числа остались бы правдоподобными, а ПОК уехал бы
туда, где потребитель чаще просыпался. ⚠ **Руководство ccxt и его же код здесь
расходятся** — руководство описывает умолчание как `False`, установленная 4.5.71 отдаёт
`True`. Полагаться на такое умолчание нельзя, поэтому оно задано в конструкторе.

**Пагинация отклоняется от рецепта руководства намеренно.** Канонический рецепт —
`since = последняя.timestamp + 1` — теряет сделки, стоящие на той же миллисекунде за
границей страницы. Для БАРОВ он безопасен (отметка бара уникальна), для СДЕЛОК нет. Наш
курсор ставится на `page_max_ts` без прибавления, а повтор граничной миллисекунды
отсеивается по номерам `aggTrade`.

---

# §12. Указатель

Курсивом — то, чего в системе нет. Число — раздел этого файла.

ATH и ATL 1.2 · ATR *4.7* · aggTrade 5.3, 11.5 · BOS и CHoCH 2.17 · CME *9.11* ·
HVN и LVN *9.2* · POC 2.6 · tickSize 5.4 · Value Area 2.8 · VRVP 2.7 ·
безубыток *1.23* · бар 5.1 · база 1.22, 2.2 · база-переприор *9.7* ·
бессрочный фьючерс 5.5 · винрейт 1.19 · вложенные уровни 2.15 · возврат свечой 1.14 ·
волатильность 1.12 · вселенная 5.6 · выход из структуры 2.5 · вымпел *2.20* ·
гейт 6.2 · геометрия сделки 3.1–3.6 · гэп CME *9.11* · дивергенция 4.3 ·
дифф повтора 6.3 · доливка *1.24* · доминация *9.6* · допуск 6.6 · живой уровень 9.3 ·
зона входа 3.1 · зона интереса *9.9* · зона объёмная 2.8 · зона тени ПП 2.18 ·
исход 6.5 · канальная структура *9.8* · касание 2.12 · клин *2.20* · консолидация 1.22 ·
конвергенция 4.3 · коррекция *1.10* · кредитное плечо *1.13* · лестница закупа 2.15 ·
ликвидность внешняя и внутренняя *9.4* · лимитный барьер *2.19* · лой 1.1 · лонг 1.3 ·
ловушка 1.9 · маркетмейкер 1.16 · МТФ и СТФ 1.8, 2.13 · накопление 1.22, 2.2 ·
отработанный уровень 2.12 · переприор 2.17 · плотный уровень *9.2* · поджатие *9.1* ·
ПОК 2.6 · полосы Боллинджера 4.4 · приоритет ТФ 2.13 · прокол 1.14, 2.4 ·
проторговка 9.10 · профиль объёма 2.7 · пробой 1.15 · разрыв потока §10 ·
размазанный уровень *9.2* · ретест 2.14 · РР и R 1.18 · свинг 2.9 · сетап 1.7 ·
сила уровня 2.11 · сквиз 1.17 · скользящие 200 4.5 · сужение *9.1* · стоп 1.4, 3.2 ·
стоповый объём 2.16 · структура 1.22 · сужение границы §10 · таймфрейм 2.1 ·
тейк-профит 1.6, 3.5 · тепловая карта *9.5* · точка границы 2.3 · транспорт §11 ·
тренд 1.21 · ТВХ 1.5, 3.1 · уровень BUY и SELL 2.10 · фигуры *2.20* · флип 2.14 ·
флэт 1.22 · хай 1.1 · хеджирование *1.20* · частичная фиксация 3.6 · шорт 1.3 ·
якорь стопа 3.3 · ячейка сводки §10
