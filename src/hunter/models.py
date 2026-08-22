"""Контракты между слоями. FOUNDATION.md §10.1 — словари между слоями запрещены.

ЧИСТЫЙ МОДУЛЬ (§10.3): часы, сеть и глобальное состояние не трогаются.

Причина, названная в §10.1: 910 вхождений `dict[str, Any]` в прошлой реализации
породили класс «поле без продюсера» — ключ читается, никто его не пишет, ошибка молчит
годами. У модели такого не бывает: mypy падает на несуществующем атрибуте.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def tick_scale(tick: Decimal) -> tuple[int, int]:
    """Шаг цены в ЦЕЛЫХ числах: (множитель, шаг). ОДНО определение бина на весь проект (§5).

    ⚠ Здесь была развилка, из-за которой главный выход системы зависел от того, какой
    ветвью кода построен профиль. Живой поток считал бин через `Decimal`, архив — через
    `float`-деление с `floor`. Реальные цены сделок ВСЕГДА кратны `tickSize`, то есть
    всегда ложатся ровно на границу бина, а двоичное представление `k·tick` в `float64`
    сплошь и рядом чуть меньше точного значения — и `floor` уводил цену в бин `k−1`.

    Замер 2026-08-04 на реальных сутках, 21 символ вселенной, 504 часовых окна: ПОК
    расходился на 194 окнах (38%), максимум на 1.687% цены (AEVOUSDT). На BTC, ETH и BCH
    расхождения не было вовсе — мерить только на флагманах значило бы не увидеть дефекта.
    Разбор: docs/audit/critical-review-verified-2026-08-04.md

    Лечится не выбором «какая ветвь правильнее», а отказом от дробной арифметики:
    `tickSize` — это десятичная дробь с конечным числом знаков, значит и шаг, и цену
    можно выразить целыми в единицах последнего знака. Возвращается пара
    (множитель 10^знаков, шаг в этих единицах), и обе ветви строят бин ИЗ НЕЁ.

    Примеры: 0.00001 → (100000, 1); 0.005 → (1000, 5); 0.1 → (10, 1); 10 → (1, 10).
    """
    exp = tick.as_tuple().exponent
    digits = max(0, -int(exp)) if isinstance(exp, int) else 0
    scale = 10**digits
    step = int(tick * scale)
    if step <= 0:
        raise ValueError(f"шаг цены {tick} не выражается целым — бинировать нечем")
    return scale, step


def bin_index(price: float, tick: Decimal) -> int:
    """Номер бина цены. ЕДИНСТВЕННАЯ функция бинирования проекта (§5).

    `+0.5` перед `floor` — не округление «на всякий случай», а перевод цены в целые
    единицы последнего знака шага: на сетке биржи произведение `price·scale` отличается
    от целого только на ошибку представления, и половинка её поглощает с запасом в
    двенадцать порядков. Полярисовый двойник (`archive.bin_expr`) считает ТО ЖЕ выражение
    теми же двумя числами; согласованность проверяет gates/binning_agrees.py.
    """
    scale, step = tick_scale(tick)
    return math.floor(price * scale + 0.5) // step


class RawTrade(BaseModel):
    """Одна сделка, пришедшая от ccxt. Тип, в который превращается словарь биржи.

    ⚠ Заведено 2026-08-07, находка А-5 прошлого разбора, подтверждённая на HEAD.
    §10.1 запрещает словари между слоями: поток сделок оставался последним таким местом —
    `watch_agg_trades` отдавал `list[dict[str, Any]]`, а `run` читал `t.get("price")`,
    то есть ключи проверялись глазами, и гейту `no_loose_dicts` ловить было нечего.

    РАЗБОР СЛОВАРЯ ЖИВЁТ НЕ ЗДЕСЬ, а в `exchange.parse_trade` — в файле, который гейт
    `no_loose_dicts` уже объявил единственной границей с ccxt. Держать разбор здесь
    значило бы РАСШИРИТЬ список исключений гейта на `models.py`, то есть ослабить
    правило ради своей же правки.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: float = Field(gt=0)
    amount: float = Field(gt=0)
    timestamp: int = Field(gt=0)
    id: str | None = None

    side: str | None = None
    """Сторона АГРЕССОРА: `buy` — покупатель забрал ликвидность, `sell` — продавец.
    `None` — биржа стороны не дала.

    ⚠ ПОЛЕ ЗАВЕДЕНО 2026-08-11, И ДО ЭТОГО ОНО ВЫБРАСЫВАЛОСЬ. ccxt отдаёт `side` у каждой
    сделки aggTrade (выводя его из сырого `isBuyerMaker`), а `parse_trade` его не читал —
    поток нёс сторону, а мы её теряли на границе.

    ⚠ ЗАЧЕМ ОНО НУЖНО, И ЭТО РЕФЕРЕНТ ИЗ КОРПУСА, А НЕ ДОГАДКА. Курс о стороне сделок
    молчит, но корпус — второй голос ровно там, где первый молчит (§0.1). В расшифровке
    prizrak_btc_eth_keyzone.txt автор объясняет вывод так: «биток пока не выглядит на
    то, чтобы он достиг пика. А почему? По дельте, по дельте ордербука крупных продаж
    нету, крупняк не появлялся, при том, что откупы больш[ие]». То есть признак назван
    ПРИЧИНОЙ вывода, а не упомянут вскользь.

    ⚠⚠ И ГРАНИЦА, КОТОРУЮ ЗДЕСЬ НЕЛЬЗЯ ПЕРЕЙТИ МОЛЧА. Автор говорит «дельта ОРДЕРБУКА»,
    а сторона сделки даёт дельту ПОТОКА СДЕЛОК — это не одно и то же: первое про
    изменения лимитных заявок, второе про то, кто забирал ликвидность. Его же пояснение
    («крупных продаж нету, крупняк не появлялся, откупы большие») описывает именно
    агрессивный поток, но приравнивать одно к другому на этом основании — вывод, а не
    цитата. Поле хранит СТОРОНУ и ничего не утверждает про дельту; станет ли дельта
    элементом методики — вопрос обзора элемента, а не транспорта.
    """


class NotReady(BaseModel):
    """Данных нет. Причина обязательна — §4.3 запрещает молчаливый пропуск."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"не готово: {self.reason}"


@dataclass(slots=True, frozen=True)
class Bar:
    """Свеча. Не модель pydantic, и это РЕСУРСНОЕ решение с названной ценой.

    ⚠ ПЕРЕВЕДЁН С `BaseModel` НА СРЕЗОВЫЙ КЛАСС 2026-08-11. Повод — этап 2: глубина ряда
    стала выводиться из горизонта, и число баров в памяти выросло с 81 тыс. до 2.0 млн.
    Замер стоимости типа (`tracemalloc`, 50 000 экземпляров):

        pydantic BaseModel   1113 байт на бар  ->  2.10 ГБ на вселенную
        dataclass(slots)      121 байт на бар  ->  0.23 ГБ на вселенную

    Девятикратно, и это разница между «работает» и `MemoryError`, который в проекте уже
    случался на бэкфилле (2026-08-04). Шесть чисел не имеют права стоить килобайт.

    ⚠ ЧТО ПРИ ЭТОМ НЕ ПОТЕРЯНО. Проверка определения свечи осталась на месте и осталась
    ОБЯЗАТЕЛЬНОЙ: `__post_init__` бросает `ValueError` ровно там же, где прежде бросал
    валидатор pydantic, и перехватчик в `exchange.fetch_closed_ohlcv` его ловит —
    `pydantic.ValidationError` сам наследует `ValueError`, так что ветвь не расширялась,
    а сузилась до общего предка. Битый бар по-прежнему отклоняется поимённо, а не
    проезжает молча; замер 2026-08-03 нашёл такой один на 73 828 (BCH/USDT:USDT 1w).

    `frozen=True` сохранён: бар неизменяем, как и был. `slots=True` — то, ради чего всё.

    ⚠ Списки баров лежат ПОЛЯМИ моделей pydantic (`OhlcvFetch.bars`, `SeriesState.bars`).
    Проверено, что pydantic такие объекты пропускает ПО ССЫЛКЕ, не пересоздавая: накладные
    расходы на модель поверх 50 000 баров — 0.4 МБ, и `st.bars[0] is xs[0]` истинно.
    """

    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        # ⚠ Первой — проверка конечности (2026-08-18): все остальные проверки здесь
        # написаны сравнениями, а сравнение с NaN всегда ложно — NaN-бар проходил их
        # МОЛЧА и дальше отравлял min/max/суммы всего конвейера, где NaN не падает,
        # а тихо съедает результат.
        if not (math.isfinite(self.open) and math.isfinite(self.high)
                and math.isfinite(self.low) and math.isfinite(self.close)
                and math.isfinite(self.volume)):
            raise ValueError(
                f"бар {self.open_ms}: неконечное значение "
                f"(o={self.open} h={self.high} l={self.low} c={self.close} "
                f"v={self.volume})")
        if self.open_ms < 0:
            raise ValueError(f"бар {self.open_ms}: метка открытия отрицательна")
        if self.volume < 0:
            raise ValueError(f"бар {self.open_ms}: объём отрицателен ({self.volume})")
        # Не «разумное значение», а определение свечи: экстремумы обязаны накрывать
        # открытие и закрытие. Нарушение означает битые данные, а не редкий рынок.
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError(
                f"бар {self.open_ms}: high/low не накрывают open/close "
                f"(o={self.open} h={self.high} l={self.low} c={self.close})"
            )
        if self.high < self.low:
            raise ValueError(f"бар {self.open_ms}: high < low")


@dataclass(slots=True, frozen=True)
class BarDetail:
    """Свеча СО ВСЕМИ полями, которые присылает Binance. Шесть из них ccxt выбрасывает.

    ⚠ ЗАЧЕМ ОТДЕЛЬНЫЙ ТИП, А НЕ ПОЛЯ В `Bar`. Единый `fetch_ohlcv` возвращает ровно шесть
    чисел — так устроен контракт ccxt, и от него зависят ряд, повтор карточки и все
    индикаторы. Расширять `Bar` значило бы менять тип, который лежит в памяти два миллиона
    раз, ради данных, нужных далеко не везде. Здесь отдельный тип и отдельный запрос.

    ⚠ ОТКУДА ПОЛЯ. `parse_ohlcv` в ccxt/binance.py перечисляет ВСЕ двенадцать полей ответа
    в комментарии и берёт индексы 0–5; остальные шесть теряются:

        6  время закрытия свечи      -> `close_ms`
        7  оборот в валюте котировки -> `quote_volume`
        8  число сделок              -> `trades`
        9  объём ПОКУПОК по рынку    -> `taker_buy_base`
        10 то же в валюте котировки  -> `taker_buy_quote`
        11 «ignore»                  -> не берётся

    ⚠ ЧТО ЭТО ДАЁТ И ЧЕГО НЕ ДАЁТ. Индекс 9 — объём агрессивных ПОКУПОК за бар, поэтому
    объём продаж есть разность `volume - taker_buy_base`, а разделение объёма получается
    без единой сделки: вес 2 за 499 баров против 20 за один запрос `aggTrades`.

    Это НЕ то же самое, что дельта стакана: курс о разделении объёма не говорит вовсе, а
    корпус разборов говорит о «дельте ордербука». Транспорт поле отдаёт; станет ли оно
    признаком методики — вопрос к источникам (курс, корпус, классика), а не к
    транспорту, и здесь он не решается.

    ⚠ У mark/index/premium-свечей та же форма ответа, но поля 5, 7, 9, 10 равны нулю
    («Ignore» в комментарии ccxt). Поэтому эти ряды сюда НЕ идут — им хватает `Bar`.
    """

    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int
    taker_buy_base: float
    taker_buy_quote: float

    def __post_init__(self) -> None:
        # Та же проверка конечности, что у `Bar`, и по той же причине: остальные
        # проверки — сравнения, а сравнение с NaN всегда ложно.
        if not (math.isfinite(self.open) and math.isfinite(self.high)
                and math.isfinite(self.low) and math.isfinite(self.close)
                and math.isfinite(self.volume) and math.isfinite(self.quote_volume)
                and math.isfinite(self.taker_buy_base)
                and math.isfinite(self.taker_buy_quote)):
            raise ValueError(f"бар {self.open_ms}: неконечное значение в полях детали")
        if self.close_ms <= self.open_ms:
            raise ValueError(
                f"бар {self.open_ms}: закрытие {self.close_ms} не позже открытия")
        if self.taker_buy_base > self.volume:
            # Не «подозрительно много», а нарушение определения: покупки — ЧАСТЬ объёма.
            raise ValueError(
                f"бар {self.open_ms}: покупок {self.taker_buy_base} больше объёма "
                f"{self.volume}")
        if self.taker_buy_base < 0 or self.trades < 0:
            raise ValueError(f"бар {self.open_ms}: отрицательные покупки или число сделок")

    @property
    def taker_sell_base(self) -> float:
        """Объём агрессивных ПРОДАЖ: остаток объёма за вычетом покупок."""
        return self.volume - self.taker_buy_base

    @property
    def delta_base(self) -> float:
        """Покупки минус продажи в базовой валюте. Знак — перевес стороны за бар."""
        return 2 * self.taker_buy_base - self.volume

    def bar(self) -> Bar:
        """Тот же бар в обычном типе — чтобы ряд и индикаторы не знали о расширении."""
        return Bar(open_ms=self.open_ms, open=self.open, high=self.high,
                   low=self.low, close=self.close, volume=self.volume)


class Ticker24h(BaseModel):
    """Суточная статистика рынка. Публичный `ticker/24hr`.

    ⚠ Ни одна из этих величин НЕ ВХОДИТ в сигнал и входить не может: признака, которого
    нет в курсе, в методике не появляется (§0). Транспорт их отдаёт, потому что вселенная
    отбиралась ПО ОБОРОТУ (`config/universe.toml`), а проверить оборот было нечем — число
    в конфигурации снято руками 2026-08-03 и с тех пор не пересматривалось.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    last: float
    quote_volume: float
    """Оборот за сутки в валюте котировки. Именно по нему отбиралась вселенная."""
    change_pct: float | None = None
    timestamp_ms: int | None = None


class BookTop(BaseModel):
    """Вершина стакана: лучший бид и аск. Публичный `depth`.

    Отдаётся ВЕРШИНА, а не весь стакан, и это решение: полная книга — сотни уровней на
    символ, типизировать которые незачем, пока ни одно правило курса на них не ссылается.
    Понадобится глубина — расширится здесь, а не появится словарём у потребителя.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float
    timestamp_ms: int | None = None

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class OpenInterest(BaseModel):
    """Открытый интерес по контракту. Публичный `openInterest`, только контракты."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    amount: float
    """В контрактах (базовой валюте)."""
    value: float | None = None
    """В валюте котировки, если биржа его посчитала."""
    timestamp_ms: int | None = None


class FundingRate(BaseModel):
    """Ставка финансирования бессрочного контракта. Публичный `premiumIndex`.

    ⚠ ИМЕНА ПОЛЕЙ СВЕРЕНЫ С ИСХОДНИКОМ 2026-08-11, И ДВА ИЗ НИХ ЧИТАЛИСЬ БЫ НЕВЕРНО.
    Единая структура ccxt («Funding Rate Structure») называет их `fundingRate` и
    `fundingTimestamp`, но у Binance они берутся так (ccxt/binance.py,
    `parse_funding_rate`):

        fundingRate      <- lastFundingRate   ПОСЛЕДНЯЯ расчётная ставка, не текущая
        fundingTimestamp <- nextFundingTime   время СЛЕДУЮЩЕГО расчёта

    То есть ставка смотрит назад, а метка времени — вперёд. Поля названы здесь так, чтобы
    это было видно из имени: `last_rate` и `next_ms`, а не `rate` и `timestamp`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    last_rate: float | None = None
    """ПОСЛЕДНЯЯ расчётная ставка (`lastFundingRate` у Binance), а не прогноз."""
    next_ms: int | None = None
    """Время СЛЕДУЮЩЕГО расчёта (`nextFundingTime` у Binance)."""
    interval: str | None = None
    """Период расчёта, например `8h`. У Binance из `fundingIntervalHours`."""
    interest_rate: float | None = None
    mark: float | None = None
    index: float | None = None
    timestamp_ms: int | None = None


class BookLevel(BaseModel):
    """Одна ступень стакана."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: float
    qty: float


class OrderBook(BaseModel):
    """Стакан целиком. Ступени идут от лучшей цены вглубь."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    timestamp_ms: int | None = None
    nonce: int | None = None
    """Номер обновления биржи. Нужен, чтобы склеивать снимок с потоком дельт."""


class Quote(BaseModel):
    """Лучшая пара цен без глубины. Публичные `ticker/bookTicker` и `ticker/price`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bid: float | None = None
    bid_qty: float | None = None
    ask: float | None = None
    ask_qty: float | None = None
    last: float | None = None
    timestamp_ms: int | None = None


class MarkPrice(BaseModel):
    """Маркировочная и индексная цена. Только контракты."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    mark: float | None = None
    index: float | None = None
    timestamp_ms: int | None = None


class LongShortRatio(BaseModel):
    """Соотношение длинных и коротких позиций за период. Только контракты."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    ratio: float
    period: str | None = None
    timestamp_ms: int | None = None


class Liquidation(BaseModel):
    """Публичная ликвидация. Только контракты и маржа.

    ⚠ Две особенности, найденные сверкой с исходником 2026-08-11.

    `side` в ДОКУМЕНТИРОВАННОЙ структуре ccxt («Liquidation Structure») ОТСУТСТВУЕТ, но в
    реализации есть: `parse_ws_liquidation` кладёт туда сырое поле `S` (BUY/SELL) строчными.
    Редкий случай, когда код шире документации; поле оставлено, потому что оно реально
    приходит, а не потому что я его ожидал.

    `quote_value` и `base_value` у Binance в потоке ВСЕГДА пусты — парсер подставляет туда
    `None`. Поле оставлено ради других площадок, но на нашей оно ничего не скажет, и это
    названо здесь, чтобы пустота не читалась как «объём нулевой».
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    price: float | None = None
    contracts: float | None = None
    quote_value: float | None = None
    side: str | None = None
    timestamp_ms: int | None = None


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    """Идентификатор биржи (BTCUSDT). Нужен для архива — там свои имена."""

    tick_size: Decimal = Field(gt=0)
    """Шаг цены из PRICE_FILTER. §5: бины профиля привязаны к нему."""

    created_ms: int | None = None
    """Когда рынок появился на бирже. `None` — биржа не сказала.

    ⚠ ЗАКРЫВАЕТ НАЗВАННУЮ, НО НЕ РЕШЁННУЮ НЕОДНОЗНАЧНОСТЬ. Докстрока `_fetch_ohlcv_paged`
    прямо признаёт: «„ряд вышел короче запрошенного“ неотличимо от „на бирже столько и
    есть“», — и до сих пор различить их было нечем, поэтому короткий ряд считался
    подозрительным всегда. Поле даёт третий ответ: ряд короче, ПОТОМУ ЧТО инструмент
    моложе запрошенного окна, и это не отказ.

    Источник — `market['created']` самой ccxt; на нашей площадке заполнен у 855 рынков
    из 855 (замер 2026-08-12). Пример из документации ccxt («fetch-first-ohlcv-timestamp»)
    предлагает вместо этого искать первую свечу двоичным поиском по истории — десятки
    запросов на символ; здесь то же самое приходит вместе с рынками, бесплатно.
    """


class ClockSync(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    offset_ms: int
    """Сдвиг: серверное время минус локальное. Положительный = локальные отстают."""

    rtt_ms: int = Field(ge=0)
    """Круговая задержка замера. Неопределённость сдвига — ±rtt/2."""

    measured_at_local_ms: int
    samples: int = Field(ge=1)

    server_ms_at_sync: int = 0
    """Биржевое время в момент сведения. ЯКОРЬ, от которого отсчитывается `now_ms`."""

    measured_at_monotonic_ns: int = 0
    """МОНОТОННЫЕ часы в момент сведения. Вместе с якорем выше задают биржевое время без
    участия настенных часов.

    ⚠ Зачем. До 2026-08-05 `now_ms` считался как `local_ms() + offset_ms`, то есть от
    настенных часов (`time.time_ns`). Настенные часы прыгают: шаг NTP, перевод времени,
    засыпание и восстановление машины, правка администратором. Прыжок НАЗАД означал бы,
    что уже закрытые свечи снова «не закрыты», — а на этом сравнении держится весь §6.
    Для пакетного прогона на 90 секунд риск был теоретическим; для службы 24/7 (§8) он
    ежедневный. Монотонные часы по определению не идут назад и не прыгают.

    Ноль в обоих полях — сведение, сделанное до появления якоря; `clock.now_ms` в этом
    случае падает, а не подставляет настенное время (§4.3).
    """


class TickChange(BaseModel):
    """Шаг цены инструмента изменился между двумя чтениями рынков."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    was: Decimal
    now: Decimal


class MarketsReload(BaseModel):
    """Что изменилось на бирже между двумя чтениями рынков. §5, находка Б-4.

    ⚠ Зачем это вообще нужно. `load_markets()` звался ОДИН раз при открытии, а
    `Exchange._instruments` не сбрасывался никогда. Для пакетного прогона на минуты это
    безразлично; для службы 24/7 (§8) значит, что ни смена `tickSize`, ни делистинг
    посреди работы не будут замечены — включая проверку `active`, которая иначе
    отрабатывает только на старте.

    Смена шага цены — не косметика: на нём стоит вся сетка бинов профиля (§5), и
    накопленный кэш архива под старый шаг к новому не относится. Б-4 закрыт тем, что шаг
    входит в имя файла кэша, поэтому старые сутки не портятся молча, — но уровни,
    построенные до смены, посчитаны по другой сетке, и знать об этом обязан оператор.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked: int
    """Сколько символов сверено. Знаменатель: пустые списки ниже без него неотличимы от
    непроведённой сверки."""

    tick_changed: tuple[TickChange, ...] = ()
    delisted: tuple[str, ...] = ()
    """Стали `active=False` либо исчезли с биржи между чтениями."""

    restored: tuple[str, ...] = ()
    """Были недоступны, снова доступны. Названы отдельно: это тоже изменение состава."""

    @property
    def changed(self) -> bool:
        return bool(self.tick_changed or self.delisted or self.restored)


class TradeHistogram(BaseModel):
    """Гистограмма «цена → объём» на реальных сделках. §5.

    Сторона сделки НЕ хранится умышленно: §3 запрещает CVD, а поле стороны —
    единственное, из чего он собирается.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    tick_size: Decimal = Field(gt=0)
    qty_by_bin: dict[int, float] = Field(default_factory=dict)
    count_by_bin: dict[int, int] = Field(default_factory=dict)
    trades_seen: int = 0
    qty_seen: float = 0.0
    """Контроль: сумма по сырью до агрегации. Сверяется с суммой по бинам."""

    first_ms: int | None = None
    last_ms: int | None = None

    def bin_index(self, price: float) -> int:
        return bin_index(price, self.tick_size)

    def bin_price(self, idx: int) -> Decimal:
        """Цена бина: его нижняя граница.

        Для сделок биржи это и есть цена сделки: реальные цены кратны `tickSize`, то
        есть ложатся ровно на границу. «Нижняя граница» здесь не приближение, а точное
        значение — приближением оно было, пока архив бинировал `float`-делением и
        сдвигал цену на бин вниз (см. `tick_scale`).
        """
        return Decimal(idx) * self.tick_size

    def add(self, price: float, qty: float, ts_ms: int) -> None:
        idx = self.bin_index(price)
        self.qty_by_bin[idx] = self.qty_by_bin.get(idx, 0.0) + qty
        self.count_by_bin[idx] = self.count_by_bin.get(idx, 0) + 1
        self.trades_seen += 1
        self.qty_seen += qty
        if self.first_ms is None or ts_ms < self.first_ms:
            self.first_ms = ts_ms
        if self.last_ms is None or ts_ms > self.last_ms:
            self.last_ms = ts_ms

    def binned_qty_total(self) -> float:
        return sum(self.qty_by_bin.values())

    def reconciliation_error(self) -> float:
        """|сумма по бинам − сумма по сырью| / сумма по сырью."""
        if self.qty_seen == 0.0:
            return 0.0
        return abs(self.binned_qty_total() - self.qty_seen) / self.qty_seen

    def absorb(self, other: TradeHistogram) -> None:
        """Влить накопленное другим экземпляром. Для службы 24/7 (§8, находка А-1).

        Зачем не копирование. Расчёт службы идёт в РАБОЧЕМ ПОТОКЕ и читает эти словари,
        пока задача сделок продолжает писать, — а `dict` в двух потоках сразу даёт
        `RuntimeError: dictionary changed size during iteration`, то есть не порчу чисел,
        а падение расчёта. Снимать копию на каждом цикле нельзя: копия стоит O(всего
        накопленного), а накапливается оно сутками.

        Поэтому контейнеров два: в один пишет поток, из другого читает расчёт, и на
        границе цикла первый ВЛИВАЕТСЯ во второй. Стоимость — O(нового за цикл).
        Слияние идёт синхронно между циклами, когда расчёт уже кончился, а новый ещё не
        начат: одновременного доступа не возникает ни на мгновение.

        Контроль сходимости при этом сохраняется: `reconciliation_error` сверяет сумму по
        бинам с суммой по сырью, и обе величины переносятся здесь вместе.
        """
        for idx, q in other.qty_by_bin.items():
            self.qty_by_bin[idx] = self.qty_by_bin.get(idx, 0.0) + q
        for idx, n in other.count_by_bin.items():
            self.count_by_bin[idx] = self.count_by_bin.get(idx, 0) + n
        self.trades_seen += other.trades_seen
        self.qty_seen += other.qty_seen
        if other.first_ms is not None:
            self.first_ms = (other.first_ms if self.first_ms is None
                             else min(self.first_ms, other.first_ms))
        if other.last_ms is not None:
            self.last_ms = (other.last_ms if self.last_ms is None
                            else max(self.last_ms, other.last_ms))

    def clear(self) -> None:
        """Забыть всё. Зовётся сразу после `absorb`: контейнер потока начинает цикл пустым."""
        self.qty_by_bin.clear()
        self.count_by_bin.clear()
        self.trades_seen = 0
        self.qty_seen = 0.0
        self.first_ms = None
        self.last_ms = None


class BarBinnedTrades(BaseModel):
    """Сделки, разложенные по БАРАМ и бинам цены. §5 + §10.3.

    Зачем отдельно от `TradeHistogram`: та агрегатная и времени не хранит, поэтому вырезать
    из неё окно структуры нельзя. А профиль по стр. 26 натягивается ровно на бары структуры.
    Без этой раскладки уровень §2.2 на живом прогоне не построить вовсе — только из
    суточного архива.

    Сетка `bucket_ms` — самый младший используемый ТФ: бары старших ТФ кратны ему, значит
    окно любой структуры складывается из целых корзин без остатка.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    tick_size: Decimal = Field(gt=0)
    bucket_ms: int = Field(gt=0)
    qty: dict[int, dict[int, float]] = Field(default_factory=dict)
    """корзина (open_ms) → бин цены → объём."""

    cnt: dict[int, dict[int, int]] = Field(default_factory=dict)
    """То же по числу сделок. Хранится отдельно, потому что число сделок в окне нельзя
    восстановить из объёмов: попытка выдать за него число бинов была бы именем-ложью."""

    trades_seen: int = 0
    qty_seen: float = 0.0

    def add(self, price: float, qty: float, ts_ms: int) -> None:
        bucket = ts_ms - ts_ms % self.bucket_ms
        idx = bin_index(price, self.tick_size)
        self.qty.setdefault(bucket, {})
        self.cnt.setdefault(bucket, {})
        self.qty[bucket][idx] = self.qty[bucket].get(idx, 0.0) + qty
        self.cnt[bucket][idx] = self.cnt[bucket].get(idx, 0) + 1
        self.trades_seen += 1
        self.qty_seen += qty

    def merge_binned(self, buckets: list[tuple[int, int, float, int]]) -> None:
        """Влить уже свёрнутые сутки: (корзина, бин, объём, число сделок).

        Отдельно от `add`, потому что поштучный `add` на 1.26 млн сделок в сутках — это
        миллион вызовов Python на файл. Корзина архива (5 мин) обязана быть НЕ КРУПНЕЕ
        собственной; иначе окно 5м-структуры пришлось бы округлять, и профиль перестал
        бы соответствовать стр. 26. Несовпадение — исключение, а не тихое округление.
        """
        for bucket, idx, qty, cnt in buckets:
            if bucket % self.bucket_ms:
                raise ValueError(
                    f"{self.symbol}: корзина архива {bucket} не ложится на сетку "
                    f"{self.bucket_ms} — окно структуры пришлось бы округлять"
                )
            own = bucket - bucket % self.bucket_ms
            self.qty.setdefault(own, {})
            self.cnt.setdefault(own, {})
            self.qty[own][idx] = self.qty[own].get(idx, 0.0) + qty
            self.cnt[own][idx] = self.cnt[own].get(idx, 0) + cnt
            self.trades_seen += cnt
            self.qty_seen += qty

    def absorb(self, other: BarBinnedTrades) -> None:
        """Влить накопленное другим экземпляром. Пара к `TradeHistogram.absorb` (А-1).

        Сетка и шаг цены обязаны совпадать: сложение корзин разной ширины или бинов
        разного шага дало бы правдоподобный, но бессмысленный профиль. Несовпадение —
        исключение, а не тихое приведение.
        """
        if other.bucket_ms != self.bucket_ms or other.tick_size != self.tick_size:
            raise ValueError(
                f"{self.symbol}: слить нельзя — корзина {other.bucket_ms} против "
                f"{self.bucket_ms}, шаг {other.tick_size} против {self.tick_size}"
            )
        for bucket, bins in other.qty.items():
            own = self.qty.setdefault(bucket, {})
            for idx, q in bins.items():
                own[idx] = own.get(idx, 0.0) + q
        for bucket, counts in other.cnt.items():
            own_cnt = self.cnt.setdefault(bucket, {})
            for idx, n in counts.items():
                own_cnt[idx] = own_cnt.get(idx, 0) + n
        self.trades_seen += other.trades_seen
        self.qty_seen += other.qty_seen

    def clear(self) -> None:
        self.qty.clear()
        self.cnt.clear()
        self.trades_seen = 0
        self.qty_seen = 0.0

    def drop_before(self, from_ms: int) -> int:
        """Забыть корзины левее `from_ms`. Возвращает число отброшенных корзин.

        ⚠ Нужно только службе 24/7 (§8): пакетный прогон живёт минуты, а служба
        накапливает корзины бесконечно — по одной на каждые `bucket_ms` на каждый символ.
        Это не «оптимизация», а условие того, что процесс доживает до следующих суток.

        Отбрасывание НЕ молчаливое в двух смыслах сразу. Во-первых, счётчики
        `trades_seen`/`qty_seen` уменьшаются вместе с содержимым, поэтому сверка
        «бины против сырья» продолжает описывать то, что лежит в контейнере, а не то,
        что когда-то в него попало. Во-вторых, окно, которое после отбрасывания уже не
        покрыто, отдаётся как `NotReady` с названными границами (см. `window`) — то есть
        цена потери видна на месте, а не проявляется усечённым профилем (§4.3).
        """
        old = [b for b in self.qty if b < from_ms]
        for b in old:
            bins = self.qty.pop(b, {})
            counts = self.cnt.pop(b, {})
            self.qty_seen -= sum(bins.values())
            self.trades_seen -= sum(counts.values())
        return len(old)

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        """Профиль по окну `[from_ms, to_ms)`. Пустое окно — отказ, а не пустой профиль."""
        if not self.qty:
            return NotReady(reason=f"{self.symbol}: сделок не собрано вовсе")
        first, last = min(self.qty), max(self.qty)
        if from_ms < first or to_ms > last + self.bucket_ms:
            return NotReady(
                reason=f"{self.symbol}: окно [{from_ms},{to_ms}) выходит за собранное "
                       f"[{first},{last + self.bucket_ms})"
            )
        if from_ms % self.bucket_ms or to_ms % self.bucket_ms:
            # Перечисление окна корзинами верно только на выровненных границах. Сетка
            # ТФ кратна 5 минутам, поэтому в норме это выполняется всегда; несовпадение
            # означает ошибку вызывающего и обязано быть видно, а не тихо терять корзины.
            return NotReady(
                reason=f"{self.symbol}: границы окна [{from_ms},{to_ms}) не легли на "
                       f"сетку корзин {self.bucket_ms}"
            )
        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick_size)
        # ⚠ Перебираются корзины ОКНА, а не все собранные. Прежняя редакция шла по
        # `self.qty.items()` и отсеивала лишние внутри цикла — на трёх сутках это было
        # незаметно, а на полных окнах структур стало стеной: замер 2026-08-04 — 15 млн
        # пар (корзина, бин) у BTC за 102 суток, и `window` зовётся по разу на структуру
        # на каждом ТФ (~240 раз), то есть 3.6 млрд обходов. Прогон не дошёл до карточки
        # за 50 минут. Корзины лежат на фиксированной сетке, поэтому окно перечисляется
        # напрямую: 19-суточная структура 1Д это 5472 корзины вместо 15 млн пар.
        for bucket in range(from_ms, to_ms, self.bucket_ms):
            bins = self.qty.get(bucket)
            if not bins:
                continue
            counts = self.cnt.get(bucket, {})
            for idx, q in bins.items():
                n = counts.get(idx, 0)
                h.qty_by_bin[idx] = h.qty_by_bin.get(idx, 0.0) + q
                h.count_by_bin[idx] = h.count_by_bin.get(idx, 0) + n
                h.qty_seen += q
                h.trades_seen += n
        if not h.qty_by_bin:
            return NotReady(reason=f"{self.symbol}: в окне [{from_ms},{to_ms}) сделок нет")
        h.first_ms, h.last_ms = from_ms, to_ms
        return h


class TradeWindows(Protocol):
    """Что угодно, способное отдать профиль по окну. Контракт §2.2 сводится к одному методу.

    Введён 2026-08-04, когда правильный горизонт вскрыл, что накапливать все сделки в
    памяти нельзя (`MemoryError` на 102 сутках, docs/audit/backfill-window-2026-08-04.md).
    Раньше `build_all` требовал именно `BarBinnedTrades` — то есть материализованный
    контейнер, — и другого источника подставить было некуда.

    Реализации: `BarBinnedTrades` (живой поток прогона) и `archive.WindowSource`
    (кэш суточных архивов + живой поток), обе отдают `NotReady` с названной причиной,
    когда окно не покрыто (§4.3).
    """

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady: ...


class OhlcvFetch(BaseModel):
    """Результат REST-засева: что принято и что отклонено. §4.3."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bars: list[Bar]
    rejected: list[str] = Field(default_factory=list)
    """Причины отказа, готовые к печати. Для человека."""

    rejected_at_ms: list[int] = Field(default_factory=list)
    """Метки времени отклонённых баров. Для СОПОСТАВЛЕНИЯ, а не для печати.

    ⚠ Заведено 2026-08-05 по находке Д-4. Отказы хранились только строками, и потому
    отчёт не мог связать отклонённый бар с конкретным разрывом: он считал объяснёнными
    ВСЕ разрывы ряда, если в ряду был хоть один отказ. Разбирать метку обратно из строки
    было бы разбором собственного текста — формат печати стал бы контрактом.
    """


class SeriesState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    bars: list[Bar] = Field(default_factory=list)
    gaps: list[tuple[int, int]] = Field(default_factory=list)
    """Пары (open_ms предыдущего, open_ms следующего) там, где сетка разорвана."""

    not_ready: NotReady | None = None
    rejected_bars: list[str] = Field(default_factory=list)
    """Бары, отклонённые как битые. §4.3: пропуск виден, а не замалчивается."""

    rejected_at_ms: list[int] = Field(default_factory=list)
    """Метки времени тех же отклонённых баров — чтобы связать их с разрывами (Д-4)."""

    # Счётчики опроса баров. До 2026-08-05 бары шли вебсокетом, и здесь стояли
    # `ws_bars`, `ws_unclosed_violations`, `ws_offgrid_violations`. Первый переименован,
    # третий сохранён по смыслу, ВТОРОЙ УДАЛЁН как тавтологический: он не мог вырасти
    # (Д-1). Его место занял `poll_late`, у которого источник ответа независим.
    polled_bars: int = 0
    """Баров добрано опросом ПОСЛЕ засева."""

    poll_requests: int = 0
    """Опросов сделано. Знаменатель к `poll_late` и `poll_not_ready`: без него доля
    неизвестна, а голое число сработавших ни о чём не говорит."""

    poll_late: int = 0
    """Опросов, в которых биржа ещё НЕ отдала свечу, обязанную быть закрытой.

    Замер отступа `POLL_OFFSET_S` (задание Ж-1), и единственный счётчик этой группы,
    способный вырасти от свойств биржи, а не кода: ожидание строится по нашим часам,
    ответ приходит от биржи. Бар при этом не теряется — приедет следующим опросом.
    """

    poll_catchups: int = 0
    """Опросов, шедших в режиме ДОГОНА: ряд отставал от биржевых часов на момент запроса.

    Отдельно от `poll_requests`, потому что это разные события. Плановый опрос идёт по
    границе ТФ; догон идёт потому, что бар уже обязан быть в ряду, а его нет. Ноль здесь
    при ненулевом `poll_late` означал бы, что опоздание замечено и не исправлено.
    """

    poll_not_ready: int = 0
    """Опросов, вернувших `NotReady` целиком: пустой ответ, все бары битые, ЛИБО бар вне
    сетки ТФ (`fetch_closed_ohlcv` отвергает в этом случае весь ответ — асимметрия Д-10).

    Отдельного счётчика внесеточных баров при доборе НЕТ сознательно: до цикла добора
    такой бар не доходит, и счётчик оказался бы неспособен вырасти — та же тавтология,
    из-за которой удалён `ws_unclosed_violations`.
    """

    arrival_lags_ms: list[int] = Field(default_factory=list)
    """ЗАДЕРЖКА ПРИХОДА каждого добранного бара: `наши часы − (open_ms + шаг ТФ)`.

    ⚠⚠ ЗАВЕДЕНО 2026-08-21 (задание Т-0). Владелец сказал: «у нас сигналы приходят с
    западанием, а сбор и обработка данных проходит слишком долго» — и оказалось, что
    западание НЕ ИЗМЕРЯЛОСЬ НИЧЕМ. Существующие счётчики отвечают на другие вопросы:
    `poll_late` считает СЛУЧАИ, когда биржа не успела к нашему отступу (да/нет), а
    сколько миллисекунд бар шёл до расчёта — не говорил никто.

    Величина СОСТАВНАЯ и это надо помнить при чтении: в неё входит и наш отступ опроса
    `POLL_OFFSET_S` (мы сами не спрашиваем раньше), и время ответа биржи. Нижняя
    граница по построению — `POLL_OFFSET_S * 1000`; всё, что сверх, — это транспорт.
    Поэтому строка приёмки печатает оба числа рядом, а не одно.

    ⚠ Меряется момент ПРИНЯТИЯ бара в ряд, а не момент отправки сигнала. Вторая
    половина пути (расчёт) меряется отдельно — `RunReport.stage_ms`, — и складывать их
    в одно число здесь нельзя: у них разные единицы наблюдения (бар против прогона).

    Список ОБРЕЗАЕТСЯ (`ARRIVAL_LAGS_KEPT`): служба 24/7 живёт месяцами, и неограниченный
    список стал бы утечкой памяти ровно того класса, из-за которого заведён `keep_bars`.
    """


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync: ClockSync
    series: dict[tuple[str, str], SeriesState] = Field(default_factory=dict)
    histograms: dict[str, TradeHistogram] = Field(default_factory=dict)
    binned: dict[str, BarBinnedTrades] = Field(default_factory=dict)
    seeded_bars: int = 0
    seed_checked: int = 0
    clock_drift_ms: int | None = None
    clock_recheck_after_s: int | None = None
    stage_ms: dict[str, int] = Field(default_factory=dict)
    """Длительность стадий конвейера в миллисекундах: `collect`, `decide`, `cards`,
    `record`. Вторая половина замера Т-0 (первая — `SeriesState.arrival_lags_ms`).

    Словарь, а не поля, потому что это ЗАМЕР, а не модель предметной области: набор
    стадий меняется вместе с конвейером, и заводить поле под каждую значило бы менять
    схему отчёта при всякой перестановке шагов. Ключи — имена функций `run.py`.
    """

    frames_written: int = 0
    cards_written: int = 0
    archive_slices_written: int = 0
    """Суток архива положено в кадры прогона — ТИКОВЫЙ транспорт. С 2026-08-17 боевой
    прогон свечной и повтор срез сделок не читает: ноль здесь законен; растёт только
    у `WindowSource`-источников (зонды). Герметичность повтора держит преемник ниже."""

    profile_series_written: int = 0
    """Профильных рядов (интрабар-свечи `TVWindows`) положено в кадры. Преемник
    `archive_slices_written` после перевода профиля на свечи: без ряда повтор строил
    бы профиль из другого источника (дефект «ИЗМЕНИЛОСЬ 5/5», 2026-08-18). Потерю
    записи ловит не этот счётчик, а манифест: повтор кадров без source.json
    отказывает кодом возврата."""

    signals_recorded: int = 0
    signals_known: int = 0
    """Эмиссии, о которых леджер знал раньше. ЗНАМЕНАТЕЛЬ к `outcomes_recorded`.

    Без него «исходов записано 0» неотличимо от «сигналов не было»: в журнале живых
    сигналов ноль исходов — норма первого прогона, а не отказ.
    """

    pp_signals_recorded: int = 0
    pp_signals_known: int = 0
    """То же — для сигналов от переприора (`kind = 'pp'`, стр. 50; заведено 2026-08-10).

    Считаются ОТДЕЛЬНО от уровневых: сводка обязана видеть перекос по типу сигнала
    (правило сводки по измерению), а сумма двух типов в одном числе его прятала бы.
    """

    absorption_measured_by_tf: dict[str, int] = Field(default_factory=dict)
    absorption_refused_by_tf: dict[str, int] = Field(default_factory=dict)
    """Мера уторговки у зоны ПП (`absorption.measure`): измерено/отказано ПО ТФ.

    Заведено 2026-08-18 вместе с прибором: первый же смок показал перекос — на младших
    ТФ отказ сплошной (окно от подтверждения слома длиннее собранного ряда минуток),
    на старших мера есть. Сто честных отказов на одном ТФ без сводки читаются как
    «рынок такой» (правило сводки отказов, разбор backfill-window-2026-08-04).
    """

    outcomes_recorded: int = 0
    emitted_outcomes: dict[str, int] = Field(default_factory=dict)
    """Исход каждой эмиссии по `OutcomeKind`, включая те, что в леджер НЕ пишутся.

    Ключи — значения перечисления, а не свободный текст: печать обходит `OutcomeKind`,
    поэтому новый вид исхода появляется в сводке сам, а не забывается.

    Заведено 2026-08-04: в таблицу исходов не попадают `not_filled` и `open`, и потому
    «сколько раз система советовала» нигде не печаталось вовсе. Половина эмиссий не
    заполняется — а средний R считался по заполнившимся.
    """

    emitted_rr: list[float] = Field(default_factory=list)
    """РР каждой эмиссии по рисковому стопу — тому же, что уходит в леджер.

    Стр. 9 называет 1к3 «золотым стандартом». Система его печатала по уровням в карточке
    и не сводила НИКУДА, а в леджер РР не писала вовсе: сумма R выглядела результатом
    метода, хотя высокая доля выигрышей покупается близкой целью.
    """

    emitted_stop_pct: list[float] = Field(default_factory=list)
    """Дистанция стопа в процентах цены. Нужна, чтобы порядок комиссий был сопоставим с
    риском: круговая комиссия тейкера ~0.1% против медианного стопа — это доля R."""
    profile_windows: int = 0
    """Окон структур, под которые понадобился профиль. Знаменатель ко всему остальному:
    ноль добранных участков при нуле окон — это «структур не было», а при сотне окон —
    отказ сбора, и различать их обязано число, а не догадка."""

    profile_windows_dropped: int = 0
    """Окон, отброшенных горизонтом (структура старше него). Правее правого края окна
    профиль никем не читается."""

    profile_windows_senior_tf: int = 0
    """ОБНУЛЁН С 2026-08-18 (вечер): закачка идёт по всем ступеням лестницы intrabar-ТФ,
    и класса «окно старшей ступени — не качаем» больше нет. Прежний смысл: окна, которым
    минутки не нужны, читают ряды засева. Предпосылка оказалась дырявой — засев собирает
    ~180 суток, а окна дневных/недельных структур глубже (разбор ACE: возможная
    шорт-зона потеряна отказом покрытия). Поле оставлено ради стабильности перечня
    полей цикла (`CYCLE_FIELDS`), пишется всегда ноль."""

    profile_spans_by_tf: dict[str, int] = Field(default_factory=dict)
    """Добранных участков профиля ПО ТФ — измерение, вдоль которого возможен перекос
    (правило сводки отказов): сто отказов, все на одном ТФ, — дефект, а не рынок."""

    profile_windows_far: int = 0
    """Окон структур, отброшенных как ДАЛЁКИЕ ОТ ЦЕНЫ (дальше `LEVELS_PER_SIDE`-го по
    своей стороне). Заменило `profile_windows_out_of_frame` 2026-08-21 вместе с самим
    механизмом: рамка отбирала окна по ВОЗРАСТУ и выбрасывала 96.2% структур
    (`окон=1255, вне кадра=31639` на живом прогоне доски). Разбор — в докстроке
    `run.profile_windows`. Прежний текст поля:
    уровень скрыл бы фильтр «у структуры», профиль никем не читается. В боевом прогоне
    рамки нет и здесь ноль."""

    profile_spans_filled: int = 0
    profile_spans_cached: int = 0
    profile_spans_failed: int = 0
    """Слитых участков: добрано у биржи / взято из хранилища / отказ. Тройка, а не пара:
    без `cached` «добрано 0» неотличимо от «всё уже было», без `failed` — от «сбор молча
    не состоялся»."""

    profile_bars_stored: int = 0
    profile_bars_rewritten: int = 0
    """Свечей профиля записано и ПЕРЕЗАПИСАНО. Закрытая свеча неизменна; ненулевая
    перезапись — либо правка биржи задним числом, либо наша ошибка склейки, и оба случая
    обязаны быть видны числом."""

    profile_symbols_skipped: int = 0
    """Символов, для которых профиль не собирался (инструмент недоступен)."""

    profile_symbols_no_windows: int = 0
    """Символов, у которых НЕ НАШЛОСЬ НИ ОДНОГО окна структуры — значит профиль не
    качался, значит уровней у них не будет ВООБЩЕ.

    ⚠ ЗАВЕДЕНО 2026-08-17, И ЭТО ЗАКРЫТИЕ МОЛЧАЛИВОГО ПРОПУСКА. В `backfill_profile_bars`
    стояло `if not wins: continue` — без счётчика и без строки лога. Цена молчания
    замерена на полном проходе вселенной: свежую карту получили 9 символов из 27,
    остальные 18 не дали ни уровня, а сводка напечатала «новых уровней 14711» и ни слова
    о пропущенных. Разобрать постфактум было нечем.

    Ноль здесь значит «у всех символов окна нашлись», и только вместе с
    `profile_windows` (знаменатель) — см. соседнее поле."""

    backfill_days_loaded: int = 0
    backfill_days_missing: int = 0
    backfill_trades: int = 0
    backfill_structures: int = 0
    backfill_structures_old: int = 0
    backfill_days_capped: int = 0
    """Сутки, отброшенные предохранителем. §4.3: усечение обязано быть ЧИСЛОМ в отчёте."""

    backfill_rest_days: int = 0
    """Суток, добранных REST-ом ccxt ЦЕЛИКОМ (архив публикации их не отдал). Решение
    владельца 2026-08-11: живой контур не ждёт публикации архива."""

    backfill_rest_partial: int = 0
    """Суток, добранных REST-ом ЧАСТИЧНО (текущие сутки либо оборванный добор):
    в кэше — файл с границей покрытия в имени, остаток за границей — живой поток."""

    backfill_rest_trades: int = 0
    """Сделок, выкачанных REST-добором. Знаменатель к паре счётчиков выше."""

    backfill_missing_by_symbol: dict[str, int] = Field(default_factory=dict)
    """Недобранные сутки ПО СИМВОЛУ. Правило сводки по измерению (CLAUDE.md,
    backfill-window-2026-08-04): сто отказов на одном символе и сто на ста разных —
    разные диагнозы, а скаляр `backfill_days_missing` их не различает."""

    watch_failures: list[str] = Field(default_factory=list)
    """Задачи наблюдения, умершие не по остановке. ПЕРВЫЕ N штук — как примеры.

    До 2026-08-04 такие задачи исчезали в `gather(..., return_exceptions=True)`: символ
    переставал получать данные, а сводка печатала «0 нарушений».

    ⚠ Список ОГРАНИЧЕН сверху (`run.WATCH_FAILURES_KEPT`), а счёт ведёт `watch_deaths`.
    Для пакетного прогона это было одно и то же; для службы 24/7 (§8) — нет: список,
    растущий по одной строке на каждый сетевой сбой, за сутки станет самой большой
    структурой в памяти. Число обязано быть точным, текст — достаточно образцов.
    """

    watch_deaths: int = 0
    """Сколько раз задача наблюдения умирала. ТОЧНЫЙ счёт, в отличие от списка выше.

    Именно он входит в число нарушений приёмки: `len(watch_failures)` после введения
    предела считал бы не смерти, а сохранённые примеры, и в службе врал бы вниз.
    """

    watch_restarts: int = 0
    """Сколько раз умершая задача была ПОДНЯТА ЗАНОВО. Только служба 24/7 (А-1).

    ⚠ Разница между этим числом и `watch_deaths` — суть перехода от прогона к службе.
    В пакетном прогоне смерть задачи была концом наблюдения за символом: `_guarded`
    логировал и пробрасывал, задача больше не существовала, и остаток прогона символ
    молча не получал данных. На девяноста секундах это ещё видно в сводке; на сутках
    служба деградировала бы до нуля потоков, продолжая печатать бодрые числа по тому,
    что успела собрать раньше.

    Ноль здесь при ненулевом `watch_deaths` означал бы, что подъём не работает.
    """

    ws_reconnects: int = 0
    ws_stream_errors: int = 0

    weight_limit: int | None = None
    """Лимит веса за минуту, прочитанный у биржи. `None` — прочитать не удалось."""

    weight_peak: int = 0
    """Наибольшее наблюдённое потребление веса за минуту (`X-MBX-USED-WEIGHT-1M`)."""

    weight_reads: int = 0
    """Сколько ответов принесли заголовок веса. Знаменатель: `weight_peak = 0` при
    `weight_reads = 0` означает «не смотрели», а не «не подходили к лимиту»."""

    markets_reloads: int = 0
    """Сколько раз рынки перечитаны за прогон. 0 при прогоне короче `MARKETS_RELOAD_S`."""

    markets_reload_failures: int = 0
    markets_checked: int = 0
    """Символов сверено при последнем перечитывании. Знаменатель к спискам ниже."""

    tick_changes: list[str] = Field(default_factory=list)
    """Смены шага цены посреди прогона. Не пустой список — повод перестроить профили:
    сетка бинов уже посчитана по прежнему шагу."""

    delisted_mid_run: list[str] = Field(default_factory=list)
    """Символы, снятые с торгов посреди прогона. Их ряды дальше замораживаются, оставаясь
    внешне здоровыми, — см. `Exchange.instrument`."""

    clock_resyncs: int = 0
    """Сколько раз часы пересведены за прогон. 0 при прогоне короче `CLOCK_RESYNC_S`."""

    clock_resync_failures: int = 0
    """Отказов пересведения. Прежнее сведение при отказе сохраняется."""

    clock_age_ms: int = 0
    """Возраст сведения часов на момент снятия отчёта."""

    clock_stale: bool = False
    """Устарело ли сведение (`clock.is_stale`). ⚠ Прибор появился 2026-08-06.

    До этого `clock.is_stale` существовала и не спрашивалась НИКЕМ: считались отказы
    пересведения, но вывод из них не делался. Служба, у которой пересведение падает
    подряд два часа, продолжала бы считать `now_ms` от устаревшего якоря и печатать
    бодрые числа — а на сравнении «сейчас против биржевой метки» стоит весь §6.
    """

    clock_drift_max_ms: int = 0
    """Наибольший уход сдвига между двумя соседними сведениями. Это и есть замер дрейфа
    часов, которого §6 требовал и которого не было: прежде печаталась одна разница между
    началом и концом прогона."""

    capabilities_checked: int = 0
    """Возможностей ccxt проверено перед прогоном. Знаменатель к «отсутствует 0»:
    без него строка вердикта неотличима от непроведённой проверки."""

    rest_rate_limited: int = 0
    """Ответов «лимит превышен» (`RateLimitExceeded`/`DDoSProtection`)."""

    outcomes_resolved_late: int = 0
    """Исходов, ДОРЕШАННЫХ по барам у сигналов, которые этот прогон заново не эмитировал
    (схема v5, 2026-08-10). Раньше такие сигналы не досчитывались никогда — статистика
    считалась по подвыборке уровней, которые система продолжает отбирать."""

    pending_no_target: int = 0
    """Сигналов БЕЗ ЦЕЛИ среди дорешанных. ⚠ Смысл изменён 2026-08-11: раньше это было
    «дорешать нельзя», и такие сигналы пропускались целиком — то есть не получали даже
    СТОПА и оставались без ответа навсегда (поймано на BEAT: сигнал числился «мимо
    входа», тогда как вход состоялся). Теперь они дорешиваются, а число остаётся
    знаменателем к «по цели 0%»: цель у них недостижима по построению."""

    pending_no_bars: int = 0
    """Сигналов, которые дорешать нельзя: ряда их ТФ в этом прогоне нет."""

    states_recorded: int = 0
    """Состояний незакрытых сделок записано в леджер (`not_filled`/`open`, схема v4,
    2026-08-10). Знаменатель к «исходов 0»: раньше эти ответы вычислялись и терялись."""

    rest_gate_held: int = 0
    """REST-вызовов, придержанных ГЛОБАЛЬНОЙ паузой лимита (2026-08-10). Знаменатель
    осмысленности паузы: ноль при ненулевом `rest_rate_limited` значил бы, что тишина
    объявлялась, но никого не остановила."""

    rest_errors: dict[str, int] = Field(default_factory=dict)
    """Отказы REST по классу исключения ccxt. Порядок ключей сортируется при печати:
    карточка и отчёт обязаны быть детерминированными (§10.6)."""

    map_added: int = 0
    map_updated: int = 0
    map_retired: int = 0
    map_stale_calc: int = 0
    """Уровней снято как порождённые РАСЧЁТОМ, которого больше нет (`stale_calc`).

    ⚠ ЭТО НЕ РЫНОЧНЫЙ ВЕРДИКТ, и путать его с `worked_off`/`flipped` нельзя. Те два
    говорят, что сделала ЦЕНА (стр. 25 и стр. 43). Этот говорит, что сделали МЫ:
    структура, из которой уровень был построен, в свежем разборе не находится, хотя её
    время внутри собранного ряда. Причина — правка детектора: личность уровня есть
    `(ТФ, начало структуры, конец структуры)`, и она меняется вместе с разбором.

    Счётчик заведён 2026-08-21, когда замер показал, что таких строк 812 из 1203
    активных уровней вселенной (67.5%) — их судило правило `resolve_carried`, СЛАБЕЕ
    обычного, и они висели активными от правки до правки.

    Число это ОЖИДАЕМО скачет после всякой правки расчёта и ОЖИДАЕМО близко к нулю,
    пока расчёт не менялся. Ноль здесь читается как «карта построена тем же расчётом,
    что и сейчас», и это самое полезное, что счётчик может сказать.
    """

    map_rejected: list[str] = Field(default_factory=list)
    """Строки карты, отклонённые схемой. Раньше такое роняло прогон целиком (Л-3)."""

    taken_at_ms: int = 0
    """Момент, НА КОТОРЫЙ снят этот отчёт. 0 — отчёт построен не снимком.

    ⚠ Заведено 2026-08-06 после того, как контрольный прогон службы напечатал два
    отстающих ряда 5м, которых не было. Свежесть считалась от времени ПЕЧАТИ, а бары
    брались снимком за 25 секунд до неё — ровно столько шёл расчёт. Если за это время
    пересекалась граница 5м, ряд объявлялся просроченным, хотя сбор его уже добрал.

    Число было правдоподобным («просрочено 26 с» при допуске 13 с) и целиком выдуманным:
    оно измеряло длительность расчёта, а не отставание данных. Свежесть — свойство
    данных на момент их взятия, и момент обязан ехать вместе с ними.
    """

    cycles: int = 0
    """Циклов расчёта завершено. 0 у пакетного прогона: там расчёт идёт после сбора,
    один раз, и понятия цикла нет."""

    uptime_s: float = 0.0
    """Сколько служба живёт, по МОНОТОННЫМ часам. Знаменатель ко всему накопленному:
    «задач умерло 3» без длительности не отличает сутки от минуты."""

    cycle_seconds: float = 0.0
    """Длительность ПОСЛЕДНЕГО цикла расчёта. Замер, а не настройка.

    Он же ответ на вопрос, ради которого расчёт вынесен из цикла событий: если бы он шёл
    на цикле, ровно на столько цикл бы и вставал.
    """

    heartbeats: int = 0
    """Тактов сердцебиения. Знаменатель к `loop_stall_max_ms`: без него ноль задержки
    неотличим от неработающего измерителя."""

    loop_late_total_ms: int = 0
    """СУММА опозданий сердцебиения за цикл. Знаменатель тот же — `heartbeats`.

    ⚠ ЗАВЕДЕНО 2026-08-21 ПОТОМУ, ЧТО ПРИБОР НЕ УМЕЛ ОТВЕТИТЬ «ГДЕ». Прогон доски
    напечатал заминку 81328 мс за первый цикл и 116937 мс за второй, а назвать
    виновника было нечем: все тяжёлые шаги уже уходят в поток, на цикле сознательно
    оставлен только снимок сбора, и он стоит 2375 мс. Три правдоподобных объяснения
    проверены и СНЯТЫ: полная сборка мусора на куче в 4.66 млн баров держит цикл
    1250 мс (замер), тот же сбор в потоке даёт те же 1344 мс — GIL мешает, но на два
    порядка слабее нужного; `barstore.count` стоит 0.017 с на участок (замер в его
    докстроке), а участков за цикл 896, то есть около 15 с суммарно.

    ⚠⚠ И ПЕРВАЯ ПОПЫТКА ОТВЕТИТЬ БЫЛА НЕВЕРНОЙ ПО КОНСТРУКЦИИ. Я дал сердцебиению
    поле «какая стадия идёт сейчас» и снимал имя в момент рекорда. Контроль подсаженной
    блокировкой (четыре сценария) показал сдвиг на одну стадию вперёд, а на последней
    стадии — пустую строку. Перенос съёма на момент ЗАСЫПАНИЯ сдвинул ответ на стадию
    назад. Причина не в выборе момента: сон сердцебиения ПЕРЕКРЫВАЕТ границу стадий, и
    ни один его конец не знает, кто держал цикл в середине. Догадка тут невозможна —
    поэтому считает не сердцебиение, а сама стадия: разность этой суммы до и после."""

    loop_late_events: list[tuple[int, int]] = Field(default_factory=list)
    """Каждое опоздание сердцебиения: (монотонные нс ПРОБУЖДЕНИЯ, опоздание в мс).

    ⚠⚠ ХРАНИТСЯ СОБЫТИЯМИ, А НЕ РАЗНОСТЬЮ СЧЁТЧИКА — и это ТРЕТЬЯ редакция прибора,
    первые две были неверны ПО КОНСТРУКЦИИ, обе поймал один и тот же контроль
    (подсаженная блокировка на известном шаге, четыре сценария):

      1. «спросить сердцебиение, какая стадия идёт» — ответ сдвигался на шаг вперёд, а
         на последнем шаге выходил пустым;
      2. «снимать имя перед сном» — ответ сдвигался на шаг назад;
      3. «считать разностью счётчика на границах шага» — ответ снова сдвигался вперёд.

    Причина у всех трёх ОДНА, и она не в выборе момента: пока цикл событий заблокирован,
    сердцебиение ВЫПОЛНИТЬСЯ НЕ МОЖЕТ. Значит его счётчик обновляется уже после того,
    как граница шага пройдена, и любая привязка «по порядку выполнения» опаздывает
    ровно на один шаг. Гонку нельзя настроить — её надо убрать.

    Событие несёт СВОЁ ВРЕМЯ, шаг несёт своё окно, и приписывание делается потом,
    сверкой чисел. Порядок выполнения в нём не участвует вовсе."""

    stage_windows_ns: dict[str, tuple[int, int]] = Field(default_factory=dict)
    """Окно каждого шага расчёта в монотонных нс: (начало, конец). Знаменатель к
    `loop_late_events` при приписывании. Шаг, который ещё идёт, сюда не попадает."""

    loop_stall_max_ms: int = 0
    """Наибольшая ЗАДЕРЖКА ЦИКЛА СОБЫТИЙ ЗА ОДИН ЦИКЛ РАСЧЁТА, замеренная сердцебиением.

    ⚠⚠ БЫЛО «за время работы», СТАЛО «за цикл» — 2026-08-21, и это не косметика.
    Бегущий максимум за всю жизнь службы отвечал не на тот вопрос: раз поднявшись, он
    больше не опускался, и по нему нельзя было отличить «цикл держал засев» от «цикл
    держит расчёт». Живой прогон дал 962312 мс в первом отчёте и 1332109 мс во втором —
    и что из этого чем вызвано, число не говорило.

    Теперь счётчик обнуляется при взятии снимка, то есть в начале каждого цикла. Снимок
    уносит с собой величину, накопленную за ПРЕДЫДУЩИЙ цикл, и печатает именно её.
    Обнуляется ЖИВОЙ счётчик сбора, а не копия в снимке: копия — это и есть ответ.

    ⚠ Первый отчёт службы показывает заминку ЗАСЕВА, а не расчёта: до первого цикла
    ничего другого не было. Сравнивать надо второй отчёт с первым.

    ⚠ Прибор, без которого утверждение «расчёт больше не держит цикл» было бы словами.
    Задача сердцебиения просыпается через фиксированный такт и меряет, насколько
    опоздала; опоздание и есть время, которое цикл был занят чем-то одним.

    Цена задержки не абстрактна: `watch_trades` у ccxt складывает сделки в кэш на
    `tradesLimit` записей, и всё, что не разобрано вовремя, вытесняется. Прямой замер
    потери — `trade_gaps` (разрывы в номерах сделок биржи).
    """

    trade_gaps: int = 0
    """Сколько сделок ПОТЕРЯНО потоком — по разрывам в номерах aggTrade биржи.

    ⚠ Это единственное в проекте измерение полноты потока сделок, не зависящее от наших
    же представлений. Номер `aggTrade` у Binance строго последователен по символу, значит
    разрыв между соседними полученными номерами — сделки, до нас не дошедшие: вытеснены
    из кэша ccxt, пока цикл событий был занят, либо потеряны при обрыве сокета.

    До 2026-08-06 полнота потока не проверялась ничем: считались только принятые сделки,
    а «принято 40 000» одинаково выглядит и когда потеряно ноль, и когда потеряна треть.
    """

    trade_gap_events: int = 0
    """В скольких МЕСТАХ обнаружены разрывы. Отдельно от числа сделок: один обрыв на
    тысячу сделок и тысяча разрывов по одной — разные болезни."""

    trade_gaps_recovered: int = 0
    """Сколько потерянных потоком сделок ДОБРАНО REST-курсором fromId (2026-08-10).
    Раньше разрыв только считался, а профиль тех минут строился без сделок молча."""

    trade_gaps_unrecovered: int = 0
    """Сделок из разрывов, оставшихся НЕ добранными (разрыв шире предела догона или
    отказ REST). Пара к `trade_gaps_recovered`: «добрано N» без неё молчит об остатке."""

    trade_ids_checked: int = 0
    """Сделок, у которых номер прочитан и сверен. ЗНАМЕНАТЕЛЬ к `trade_gaps`.

    Без него «разрывов 0» неотличимо от «номера не читались вовсе»: перестань биржа или
    ccxt присылать `id`, счётчик разрывов остался бы нулём навсегда и выглядел бы
    здоровьем потока.
    """

    trades_total: int = 0
    """Сделок принято с запуска. Гистограммы службы живут ОДИН цикл (иначе растут без
    предела), поэтому суммарное число обязано жить отдельно от них."""

    bars_trimmed: int = 0
    """Баров отрезано от НАЧАЛА рядов, чтобы память не росла бесконечно (§4.3: усечение
    обязано быть числом)."""

    live_buckets_dropped: int = 0
    """Корзин живого потока сделок забыто по той же причине. См. `BarBinnedTrades.drop_before`."""

    cache_orphaned: int = 0
    """Суток кэша сделок, которые лежат на диске, но не подходят нынешнему ключу.

    Ключ содержит шаг цены и схему бинирования, потому что от них зависят номера бинов
    внутри файла. Смена `PRICE_FILTER` у символа — штатная операция Binance, и после неё
    накопленное перестаёт находиться. Ненулевое значение означает «данные есть, но
    непригодны», а не «данных нет»: разница в том, что во втором случае их надо качать, а
    в первом — ещё и понять, почему сменился шаг.
    """

    bars_from_store: int = 0
    """Баров, поднятых засевом ИЗ ХРАНИЛИЩА на диске, а не с биржи (`barstore`).

    Введено 2026-08-11 вместе с самим хранилищем. До него бары не сохранялись вовсе:
    каждый прогон качал все ряды заново, и глубина не могла вырасти со временем. Ноль
    здесь при непустом `seeded_bars` означает, что хранилище не работает — либо пусто,
    либо рынок не опознан и писать некуда.
    """

    bars_stored: int = 0
    """Баров ДОПИСАНО в хранилище за прогон. Пара к `bars_from_store`: без неё «поднято
    из хранилища N» не отличает растущее хранилище от застывшего."""

    bars_rewritten: int = 0
    """Баров, по которым биржа отдала ИНЫЕ числа, чем уже лежали в хранилище.

    ⚠ Закрытый бар неизменен. Ненулевое значение — это либо правка биржи задним числом,
    либо наша ошибка склейки, и молчаливая перезапись сделала бы оба случая невидимыми:
    ряд поменялся бы, а дифф повтора показал бы расхождение без названной причины.
    """

    seed_already_current: int = 0
    """Рядов, у которых хранилище УЖЕ дошло до последнего закрытого бара.

    Не отказ и не деградация — совпадение с биржей, то есть лучшее состояние ряда. Поле
    заведено 2026-08-22, потому что до него этот случай считался в `seed_tail_failed` и
    печатался владельцу как «БЕЗ свежего хвоста»: на живой службе 644 таких из 645
    отказов хвоста. Разбор — в комментарии у `expected_last_closed_open_ms` в `run.seed`.
    """

    seed_tail_failed: int = 0
    """Рядов, у которых хранилище есть, а свежий хвост не пришёл. Такой ряд отдаётся
    старым, и это НАЗВАННАЯ деградация: без счётчика «данные есть» неотличимо от
    «данные свежие»."""

    seed_gaps_found: int = 0
    """Дыр ВНУТРИ окна засева, найденных в хранилище (`barstore.missing_spans`).

    Введено 2026-08-21. До него дыру внутри окна не искал никто: засев спрашивал хвостовой
    курсор, а он отвечает «после последнего сохранённого бара» и середину не просит
    никогда. Ноль здесь означает, что ряды целы, — но только вместе с `seed_gaps_filled`
    и `seed_gaps_left`: без них ноль неотличим от «прибор не смотрел».
    """

    seed_gaps_filled: int = 0
    """Дыр засева, закрытых добором у биржи ПОЛНОСТЬЮ."""

    seed_gaps_left: int = 0
    """Дыр засева, оставшихся открытыми после добора. Причина у этого ровно две, и они
    РАЗНЫЕ: биржа отказала (сеть) либо у биржи там баров нет вовсе (пауза торгов,
    делистинг). Молчать нельзя — иначе повторный запрос того же участка каждый прогон
    выглядел бы работой, а не топтанием."""

    seed_gap_bars: int = 0
    """Баров, дописанных именно добором дыр. Отдельно от `bars_stored`, потому что
    «хранилище растёт» и «дыра закрылась» — разные утверждения."""

    live_days_flushed: int = 0
    """Суток, чьё покрытие ПРОДЛЕНО живым потоком в кэше (`run._flush_live_to_cache`).

    Введено 2026-08-11 вместе со снятием холодного старта. Число отвечает на вопрос
    «пригодилось ли принятое вебсокетом»: пока оно ноль при работающем потоке, служба
    по-прежнему выбрасывает принятое и добирает то же самое REST-ом при каждом запуске.
    Ноль здесь законен ровно в одном случае — покрытие кэша не смыкается с началом
    потока, и тогда дыру обязан закрыть REST-добор."""

    map_carried: dict[str, tuple[Any, ...]] = Field(default_factory=dict)
    """Уровни, ПЕРЕНЕСЁННЫЕ из прошлых прогонов: посчитать заново их не удалось.

    Тип элемента — `store.CarriedLevel`; здесь `Any`, потому что `store` импортирует
    модели, а не наоборот, и обратная ссылка замкнула бы цикл. Это ЕДИНСТВЕННОЕ место
    с `Any` в моделях, и оно отмечено умышленно: гейт `no_loose_dicts` запрещает
    `dict[str, Any]` как контракт между слоями, а здесь контейнер отчёта, не контракт.
    """
