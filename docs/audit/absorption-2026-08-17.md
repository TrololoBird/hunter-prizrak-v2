# Уторговка у зоны: сырьё для правила (техника выбрана владельцем 2026-08-17)

**Статус: ЗАГОТОВКА ПРАВИЛА.** Владелец выбрал технику «отмена ПП уторговкой /
наторговка при касании уровня» из одиннадцати кандидатов корпуса. По §0 правило не
выдумывается — здесь собираются референты; в код пойдёт только то, что стоит на них.

## 1. Классика: три источника на измеримую меру (добыто 2026-08-17)

| школа | цитата (дословно, проверено живым чтением страницы) | измеримая величина |
|---|---|---|
| Wyckoff, закон effort vs result (StockCharts ChartSchool, «The Wyckoff Method: A Tutorial») | «The law of effort versus result provides an early warning of a possible trend change in the near future. Divergences between volume and price often signal a change in the direction of a price trend»; про поддержку: «…this indicates absorption of supply by large interests, and is considered bullish» | объём бара (усилие) против приращения/диапазона цены (результат); поглощение = большой объём при малом ходе у уровня |
| Bookmap, официальная документация индикатора Absorption | «If within a configurable time period the instrument's traded volume for a certain price level was greater than a configurable size, the Absorption Indicator will add a mark to the chart»; «Buys and sells are calculated separately…» | объём НА ЦЕНОВОМ УРОВНЕ за окно T больше порога S, стороны считаются раздельно |
| Dalton (сайт автора «Mind Over Markets») | «Price—advertises opportunity…»; «Time—regulates all advertised opportunities»; «Volume—measures the success or failure of these advertised opportunities» | принятие/отвержение цены: время у уровня + объём как подтверждение |

Ссылки: chartschool.stockcharts.com (Wyckoff Method: A Tutorial),
bookmap.com/knowledgebase (Absorption Indicator), jimdaltontrading.com
(What is the Market Profile).

⚠ Честные пометки прибора: archive.org из этой среды недоступен — первоисточник
Wyckoff 1910 («Studies in Tape Reading») сверен только через вторичную страницу
(blog.afraidtotrade.com), это помечено; цитата из самой книги Dalton не добыта
(borrow-only) — дословна триада с сайта автора.

## 2. Корпус: как автор формулирует уторговку

(заполняется — идёт извлечение цитат с локаторами)

## 3. Что уже есть в нашем приборе

(заполняется по сверке с src/hunter — живой буфер сделок, поля)

## 4. Чего в источниках НЕТ (закрывается замером, не цитатой)

* численный ПОРОГ поглощения (S) и ОКНО (T) — Bookmap оставляет их настройками;
  по §0.2 (пробел в ЧИСЛЕ) закрываются замером на корпусе/истории касаний;
* какой из трёх мер отдать приоритет — решается сверкой с корпусом (§0.2 тип В:
  арбитр — разметка автора).

## 5. План внедрения (доп-фактор, не гейт — как §2.9)

1. корпусные цитаты → выбор меры (сверка с §1);
2. прибор: величина у зоны из живого буфера сделок при событии «цена в зоне»;
3. замер порога/окна на истории касаний с контролем фальсифицируемости
   (решётка случайных порогов);
4. в карточку — строкой доп-фактора; в леджер — полем; дифф повтора.
