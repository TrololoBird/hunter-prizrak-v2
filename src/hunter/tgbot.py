"""Телеграм-бот доставки: тикер в любом виде → карта уровней; закреплённые — в канал.

Решение владельца 2026-08-10: пользователь отправляет боту название монеты, бот отвечает
графиками в стилистике обзоров автора и текстовой сводкой, похожей на формат канала.

⚠ РЕШЕНИЕ ВЛАДЕЛЬЦА 2026-08-17 расширило задачу, и расширение меняет УСТРОЙСТВО, а не
размер: бот живёт в группе с многими пользователями, принимает монету в ЛЮБОМ виде
(`BTC`, `btc`, `btcusdt`, `BTC/USDT:USDT`, `$BTC`, кириллическое `ВТС`), и если карты на
эту монету нет — СОБИРАЕТ данные сам; закреплённый список монет уходит в канал по
расписанию, без запроса.

Что из этого следует и чего в прежней редакции не было:

1. **Тикер ищется по РЫНКАМ ПЛОЩАДКИ, а не по вселенной.** Прежний `resolve_symbol` знал
   27 символов `config/universe.toml` и на всё остальное отвечал «не узнаю». Вселенная —
   это то, что система СЧИТАЕТ постоянно (§5), а не то, что ей позволено назвать.

2. **Карта берётся из двух разных мест, и бот обязан говорить, из какого.** Символ
   вселенной — карта прогонов из леджера (её строит служба); символ вне вселенной —
   сборка ПО ЗАПРОСУ тем же путём, что и `hunter run`, в память и НЕ в журнал. Второе
   называется в самом ответе: карта, посчитанная на лету, не равна карте, за которой
   следят непрерывно, и владелец обязан видеть разницу, не читая код.

   ⚠ Символ вселенной по запросу пересобирается, ЕСЛИ карта службы устарела
   (`FRESH_MAP_MAX_MIN`) — решение владельца 2026-08-17: «все карты должны обновляться
   по запросу и быть актуальными». Прежнее обоснование безопасности («проигравший
   писатель теряет только хвост, доберётся чтением») ОПРОВЕРГНУТО аудитом 2026-08-18:
   слияние в `barstore.append` — read-modify-write, проигравший терял ВСЕ свои бары,
   а покрытие по min/max не добрало бы внутреннюю дыру никогда; на Windows читатель
   вдобавок ловил битый parquet. Закрыто межпроцессным замком `_append_lock` и
   повтором `os.replace` — пробник: evidence/barstore-lock-probe-2026-08-18.py
   (с замком 600/600 меток, без — потеряно 450 и два битых чтения).

3. **Молчание в группе — свойство, а не забывчивость.** Прежний обработчик отвечал «Не
   узнаю тикер» на КАЖДОЕ сообщение чата. В группе с многими пользователями это делает
   бота генератором мусора и упирает его в лимит Telegram за минуту.

4. **Темп отправки выведен из документированных лимитов Telegram** (`Pacer`), а не из
   надежды: «In a group, bots are not be able to send more than 20 messages per minute»,
   «In a single chat, avoid sending more than one message per second»
   (core.telegram.org/bots/faq, прочитано 2026-08-17).

ЧТО БОТ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ (§1, §10.2):
  * расчёт живёт в ОДНОМ месте: и карта леджера, и сборка по запросу приходят из
    `run.collect` → `engine.decide`; второго экземпляра расчёта здесь нет;
  * бары для картинки — свежий REST через `exchange.Exchange` (тот же транспорт, что и
    у боевого прогона: сведённые часы, троттлер веса, отбрасывание незакрытой свечи);
  * переприоры для картинки считаются на лету ЧИСТЫМИ модулями по этим барам — это
    отображение §2.5 на свежие данные, не эмиссия;
  * бот НИЧЕГО не пишет ни в леджер, ни в карту; сигналов не порождает.

ОКРУЖЕНИЕ (секреты в git не лежат):
  TELEGRAM_BOT_TOKEN  — токен от @BotFather. Нет токена — названный отказ (§4.3).
  TELEGRAM_CHANNEL_ID — куда публиковать закреплённые: `-1001234567890` или `@имя_канала`.
                        Пусто — публикации нет, и это НАЗВАНО в логе, а не молчит.
Остальное — `config/bot.toml` (`config.BotConfig`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

import ccxt
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import FSInputFile, Message

from . import clock, engine, log, pereprior, run, service, store, swings
from .bars import TIMEFRAME_MS, expected_last_closed_open_ms, tf_ms
from .card import TF_LABEL
from .config import DEFAULT_PATH, BotConfig, Universe, load_bot_config, load_universe
from .exchange import CapabilityMissing, Exchange
from .levels import LevelState
from .models import Bar, NotReady
from .render import SENIOR_TFS, ZoneSpec, chart_png

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHANNEL_ENV = "TELEGRAM_CHANNEL_ID"

CHART_TFS = ("4h", "1h", "15m")
"""Какие графики отдаются на тикер: старший план → средний → локальный, как в обзорах."""

BARS_ON_CHART = 180
"""Ширина графика в барах. ⚠ ЧИСЛО РОДИЛОСЬ БЕЗ ИСТОЧНИКА (коммит 88827ac,
2026-08-10, «в стилистике автора» — стилистику никто не мерил) и найдено сплошным
пересчётом констант 2026-08-18 по вопросу владельца «сколько ещё таких выдумок?».

Замер по корпусу 2026-08-18 (12 скринов разметки автора с 12 дат, выгрузка
ТГ-канала prizzrak-tg/photos, оценка по шкале времени × шаг ТФ): видимых баров у
автора 110–625, медиана ≈ 280; на 1ч — стабильный кластер 230–300; и автор ВСЕГДА
держит ~30-40% кадра справа ПУСТЫМ под проекцию, чего это число не описывает.
Ограничение замера названо: все скрины 2475×1360 — один экран, привычка зума
одного человека. То есть 180 — нижняя треть авторского диапазона, не подтверждено
и не опровергнуто. РАЗВИЛКА ВЛАДЕЛЬЦУ (2026-08-18, открыта): оставить 180 или
перейти к ~280 с пустым полем справа — число также служит точкой отсчёта кадра
целей (`geometry.TARGET_STRUCTURE_FRAME_BARS`), решение «делай 1+2» принималось
при 180, менять его молча нельзя."""

DOMINANCE_SYMBOL = "BTCDOM/USDT:USDT"
"""Фьючерсный индекс доминации BTC на той же площадке. Проверено ВЫЗОВОМ 2026-08-17.

Автор в видео-обзоре (`docs/audit/video-2026-08-17-btc-review.md`, ~1:14–1:36) смотрит
USDT.D — но USDT.D и BTC.D это тикеры TradingView (CRYPTOCAP), в API Binance их НЕТ:
живой перебор рынков binanceusdm дал по «USDT.D»/«BTC.D» пусто, а `BTCDOM/USDT:USDT`
существует и отдаёт свечи (закрытия ~5 510 на момент проверки — совпадает с «BTCDOM
5515» в вотчлисте автора в том же видео). Индексы L1USDT/L2USDT из присланной владельцем
справки на площадке НЕ существуют (проверено тем же перебором); DEFI и MEME существуют,
но к доминации BTC отношения не имеют. BTCDOM растёт, когда BTC сильнее корзины альтов,
то есть направлен КАК BTC.D и ПРОТИВ USDT.D — читателю это названо в самой строке.
"""

DOMINANCE_WINDOW = 25
"""Закрытых свечей 1ч для строки доминации: «сейчас», «4 часа назад», «сутки назад».

Запрашивается на одну больше: `fetch_closed_ohlcv` отбрасывает НЕзакрытую последнюю
свечу, и запрос ровно 25 вернул 24 — первый же зонд 2026-08-17 поймал off-by-one.
"""

MESSAGES_PER_ANSWER = len(CHART_TFS) + 1
"""Три картинки и текст. Выводится из состава ответа, а не пишется числом.

⚠ 2026-08-17 эта константа была объявлена и НЕ ИСПОЛЬЗОВАНА НИГДЕ — то есть ровно тот
дефект («ключ без потребителя»), который в тот же день был найден в `universe.toml` у
`admission_required_bars` и годом раньше у `bars_per_timeframe`. Нашёл её механический
обход объявлений, а не чтение. Теперь она работает: `publish_pinned` считает ею, сколько
сообщений уйдёт в канал, и сравнивает с лимитом Telegram.
"""

PUBLISH_DELAY_S = float(service.CYCLE_SECONDS)
"""Через сколько после закрытия бара публиковать закреплённые. Выведено из такта службы.

Карту строит служба, а не бот, и делает это циклами по `service.CYCLE_SECONDS`. Публикация
ровно в момент закрытия бара показала бы карту, посчитанную ДО него, — то есть врала бы
на один бар при полностью исправном коде. Такт службы — минимальная задержка, при которой
новый бар успел войти в расчёт.
"""

MAX_FLOOD_WAIT_S = 60.0
"""Сколько ждать по просьбе Telegram (`retry_after`) и не дольше. ⚠ Не замерено.

Просьба подождать дольше минуты означает, что мы систематически превышаем лимит, а не
попали в пик; в этом случае честнее потерять сообщение с записью в лог, чем держать
задачу и наращивать очередь.
"""

HANDLERS_MAX = 16
"""Сколько запросов обрабатывать одновременно. ⚠ Не замерено, взято как предел РЕНДЕРА.

Картинки рисует matplotlib, и рисует по одной (`_RENDER_LOCK`): pyplot держит глобальное
состояние. Значит больше десятка одновременных запросов всё равно выстроятся в очередь —
разница лишь в том, где именно, и в очереди aiogram это видно, а в памяти процесса нет.
"""

_RENDER_LOCK = asyncio.Lock()
"""matplotlib рисует ПО ОДНОЙ картинке за раз.

`render.chart_png` работает через `pyplot`, а тот держит глобальный реестр фигур: два
рендера в разных потоках портят его молча — без исключения, кривой картинкой. Замок
дешёвый: рендер занимает доли секунды, а лишний одновременный запрос всё равно ждёт.
"""


# --- разбор запроса ---------------------------------------------------------------

HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J",
})
"""Кириллические двойники латинских букв. `ВТС` кириллицей — это `BTC`, набранный дома.

Не украшение: русская раскладка даёт визуально ТОТ ЖЕ тикер, и отказ «не узнаю BTC»
выглядел бы поломкой бота. Подмена применяется как ВАРИАНТ запроса, а не вместо него:
латинский текст ею не портится.
"""

ALIASES = {
    "БИТКОИН": "BTC", "БИТКОЙН": "BTC", "БИТОК": "BTC", "БТС": "BTC",
    "ЭФИР": "ETH", "ЭФИРИУМ": "ETH", "ЭФ": "ETH",
    "СОЛАНА": "SOL", "РИПЛ": "XRP", "ДОГИ": "DOGE",
}
"""Русские имена монет. ⚠ ЭТО УДОБСТВО, А НЕ ПРАВИЛО ИЗ ИСТОЧНИКА.

Список короткий умышленно: каждая строка — догадка о том, что имел в виду человек.
Ошибиться она не даёт молча — бот всегда печатает РАЗОБРАННЫЙ символ в первой строке
ответа, и подмена видна сразу.
"""

QUOTE_SUFFIXES = ("USDTM", "USDT", "USDC", "BUSD", "USD", "PERP", "SWAP")
"""Хвосты, которые человек дописывает к тикеру. `BTCUSDT`, `BTCPERP` — та же монета."""

TICKER_MAX_TOKENS = 2
"""Сколько слов в сообщении группы ещё считается тикером, а не разговором.

Два, а не одно: `BTC USDT` — обычная форма записи. Три и больше — это фраза, и отвечать
на неё значит вмешиваться в чужой разговор.
"""

TICKER_COMMANDS = ("/coin", "/c", "/монета", "/тикер")
HELP_COMMANDS = ("/start", "/help", "/помощь")
PINNED_COMMANDS = ("/pinned", "/закреплённые", "/закрепленные")
SIGNALS_COMMANDS = ("/signals", "/сигналы", "/сделки", "/журнал")
REFRESH_COMMANDS = ("/update", "/refresh", "/обнови", "/пересобери")
REFRESH_WORDS = ("ОБНОВИ", "ОБНОВИТЬ", "ПЕРЕСОБЕРИ", "UPDATE", "REFRESH")
"""Слова-приказы пересборки. ПРИКАЗ ВЛАДЕЛЬЦА 2026-08-18: «по запросу пользователя
в чате ОБНОВЛЯЛась любая карта любого тикера по всем таймфреймам». Свежесть из кэша
(`map_ttl_minutes`, `FRESH_MAP_MAX_MIN`) при таком запросе НЕ засчитывается — карта
пересобирается всегда; идущая сборка при этом переиспользуется: она и есть свежая."""

HELP_TEXT = (
    "Пришлите тикер монеты — отвечу картой уровней: три графика (4ч, 1ч, 15м) и сводка.\n"
    "Формат любой: BTC, btc, btcusdt, BTC/USDT:USDT.\n"
    "В группе можно так же или командой: /coin BTC.\n"
    "«обнови BTC» или /update BTC — пересобрать карту прямо сейчас, по всем таймфреймам.\n"
    "/сигналы — открытые сигналы системы и итог по закрытым;"
    " /сигналы btc — то же по одной монете.\n"
    "/pinned — какие монеты публикуются в канал сами.\n"
    "Уровни — карта системы по методу PrizrakTrade, а не торговая рекомендация."
)


@dataclass(frozen=True, slots=True)
class Request:
    """Что попросили. `explicit` — обратились к боту прямо (команда или личка)."""

    kind: str
    """ticker | help | pinned | signals"""

    query: str
    explicit: bool

    force: bool = False
    """Пересобрать карту, а не отдать свежую из кэша (приказ владельца 2026-08-18)."""


def normalize(text: str) -> str:
    """Сообщение → запрос: верхний регистр, всё, кроме букв, цифр и `/:`, долой.

    `$BTC`, `#btc`, `btc.`, `btc usdt` сводятся к одному виду. Кириллица НЕ выбрасывается:
    `isalnum` её пропускает, а подмена двойников делается отдельным вариантом.
    """
    up = text.strip().upper()
    return "".join(c for c in up if c.isalnum() or c in "/:")


def candidates(query: str) -> tuple[str, ...]:
    """Все формы, в которых мог быть записан один и тот же рынок. Порядок — приоритет.

    Сначала то, что человек написал, потом подстановки; поиск берёт ПЕРВОЕ совпадение,
    поэтому точная запись всегда сильнее догадки.
    """
    seeds = [query]
    alias = ALIASES.get(query)
    if alias is not None:
        seeds.append(alias)
    latin = query.translate(HOMOGLYPHS)
    if latin != query:
        seeds.append(latin)
        seeds.append(ALIASES.get(latin, latin))

    out: list[str] = []
    for seed in seeds:
        if not seed:
            continue
        forms = [seed]
        if "/" in seed:
            # `BTC/USDT:USDT` → `BTCUSDT` (идентификатор биржи) и `BTC` (база).
            forms.append(seed.replace("/", "").split(":")[0])
            forms.append(seed.split("/")[0])
        for form in list(forms):
            for suf in QUOTE_SUFFIXES:
                if form.endswith(suf) and len(form) > len(suf):
                    forms.append(form[: -len(suf)])
        for form in forms:
            if form and form not in out:
                out.append(form)
    return tuple(out)


def _prefer(a: str, b: str) -> str:
    """Какой из двух рынков одной базы отвечает на голый тикер. Правило детерминировано.

    Детерминизм здесь не догма, а условие того, чтобы один и тот же вопрос двух
    пользователей получал один и тот же ответ: порядок словаря рынков ccxt нам не обещан.
    """
    want = f"{a.split('/')[0]}/USDT:USDT"
    if a == want:
        return a
    if b == want:
        return b
    return min(a, b, key=lambda s: (len(s), s))


@dataclass(frozen=True, slots=True)
class MarketIndex:
    """Рынки площадки, разобранные для поиска по чему угодно. Строится один раз.

    ⚠ Читается у `Exchange`, а не у сырого ccxt: карты возможностей и списки рынков —
    ОБЪЯВЛЕНИЯ биржи, и весь разбор того, что из них можно верить, уже сделан там.
    """

    by_symbol: dict[str, str]
    by_id: dict[str, str]
    by_base: dict[str, str]
    total: int

    @classmethod
    def build(cls, ex: Exchange) -> MarketIndex:
        by_symbol: dict[str, str] = {}
        by_id: dict[str, str] = {}
        by_base: dict[str, str] = {}
        pairs = ex.markets_by_id()
        for sym, market_id in pairs.items():
            by_symbol[sym.upper()] = sym
            by_id[market_id.upper()] = sym
            base = sym.split("/")[0].upper()
            known = by_base.get(base)
            by_base[base] = sym if known is None else _prefer(sym, known)
        return cls(by_symbol=by_symbol, by_id=by_id, by_base=by_base, total=len(pairs))

    def resolve(self, text: str) -> str | None:
        """Текст пользователя → символ площадки. `None` — такого рынка нет."""
        query = normalize(text)
        if not query:
            return None
        for form in candidates(query):
            got = self.by_symbol.get(form) or self.by_id.get(form) or self.by_base.get(form)
            if got is not None:
                return got
        return None


def parse_request(text: str, *, is_private: bool, bot_name: str) -> Request | None:
    """Что делать с сообщением. `None` — МОЛЧАТЬ, и это штатный исход, а не отказ.

    ⚠ Молчание — главное отличие группы от лички. В личке любое сообщение адресовано боту,
    в группе — почти ни одно. Прежняя редакция отвечала на каждое, то есть в группе с
    многими пользователями была генератором мусора.
    """
    body = text.strip()
    if not body:
        return None
    if body.startswith("/"):
        head, _, rest = body.partition(" ")
        name, _, addressed_to = head.partition("@")
        if addressed_to and addressed_to.lower() != bot_name.lower():
            return None  # команда чужому боту в той же группе
        low = name.lower()
        if low in HELP_COMMANDS:
            return Request(kind="help", query="", explicit=True)
        if low in PINNED_COMMANDS:
            return Request(kind="pinned", query="", explicit=True)
        if low in SIGNALS_COMMANDS:
            # «/сигналы btc» — сводка по одной монете (2026-08-18): запрос уходит
            # дальше как есть, символ разрешает обработчик через MarketIndex.
            return Request(kind="signals", query=rest.strip(), explicit=True)
        if low in REFRESH_COMMANDS:
            return Request(kind="ticker", query=rest.strip(), explicit=True, force=True)
        if low in TICKER_COMMANDS:
            return Request(kind="ticker", query=rest.strip(), explicit=True)
        # `/btc` — тоже запрос тикера: команду с таким именем никто не заводил.
        return Request(kind="ticker", query=name[1:], explicit=True)
    # «обнови btc» без слэша — тот же приказ пересборки (владелец, 2026-08-18).
    # Слово-приказ отрезается ДО счёта слов: «обнови btc usdt» — три слова, но
    # тикером из них являются два, и молчать на такую форму значит не исполнить приказ.
    head, _, rest = body.partition(" ")
    if head.strip().upper() in REFRESH_WORDS and rest.strip():
        if is_private or len(rest.split()) <= TICKER_MAX_TOKENS:
            return Request(kind="ticker", query=rest.strip(),
                           explicit=True, force=True)
    if is_private:
        return Request(kind="ticker", query=body, explicit=True)
    if len(body.split()) <= TICKER_MAX_TOKENS:
        return Request(kind="ticker", query=body, explicit=False)
    return None


# --- карта уровней ----------------------------------------------------------------

# Словарь ENTRY_MARK и построчный `_fmt_zone` ЗАМЕЩЕНЫ 2026-08-17 вторым переписыванием
# формата («приведи к виду как у призрака»): роль зоны теперь называет `role()` внутри
# `compose_text` — словами автора («лимитки заранее», «вход по факту»), по `_waiting_break`,
# а не по сырому entry_rule. История первого переписывания (подписи «правило входа не
# записано (строка карты до схемы 6)» тридцать раз в сообщении) — в
# `docs/audit/bot-review-2026-08-17.md`.


def _fmt_price(x: float) -> str:
    """Цена так, как её читает человек: 63 460, 3.691, 0.00385.

    Знаков после запятой — по величине самой цены, а не константой: у BTC дробная часть
    шум, у ANKR она и есть вся цена. Разряды разделяются узким пробелом — в Telegram он
    не переносит строку.

    ⚠ Разрешение печати на всех диапазонах не грубее ~0.01% цены (2026-08-18).
    Прежние 4 значащие цифры схлопывали близкие числа в одну печать: сигнал с
    микро-стопом показывал вход и стоп ОДНИМ числом — по такому сообщению стоп
    не выставить. Печать не обязана различать всё (это не карточка), но вход и
    стоп одного сигнала обязана.
    """
    if x >= 10_000:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1000:
        return f"{x:,.1f}".replace(",", " ")
    if x >= 1:
        return f"{x:.5g}"
    return f"{x:.8f}".rstrip("0")


RETIRED_WINDOW_MS = BARS_ON_CHART * tf_ms(CHART_TFS[0])
"""Насколько давно снятый уровень ещё показывается значком. ВЫВЕДЕНО ИЗ ШИРИНЫ ГРАФИКА.

⚠ ЭТА ГРАНИЦА БЫЛА ОБЪЯВЛЕНА КОММЕНТАРИЕМ И НЕ СУЩЕСТВОВАЛА В КОДЕ. Запрос отбирал
`retired_at IS NOT NULL`, то есть ВСЕ снятые уровни за всю историю символа, а рядом стояла
строка «отбор по `retired_at` держит выборку свежей». Замер по BTC 2026-08-17: активных 69,
снятых **179**; на график 4ч попадало 53 зоны, из них 31 снятая, на 15м — 61, из них 38.
Разбор: `docs/audit/bot-review-2026-08-17.md`.

Ширина старшего графика (180 баров 4ч ≈ 30 суток) — естественная граница: событие, не
попадающее даже на самый длинный из трёх кадров, показать всё равно негде.
"""


@dataclass(frozen=True, slots=True)
class MapRead:
    """Карта символа из леджера вместе с её возрастом. Возраст — не украшение.

    Карту строит служба; если она стоит, уровни остаются в базе и выглядят свежими.
    Без отметки «обновлена N назад» остановленная служба неотличима от работающей.
    """

    zones: tuple[ZoneSpec, ...]
    last_seen_ms: int


def read_map(symbol: str) -> MapRead | NotReady:
    """Активные уровни символа из карты леджера — все ТФ разом, С ПРАВИЛОМ ВХОДА.

    ⚠ Правило входа берётся с 2026-08-11, и это не украшение. `state='active'` НЕ
    означает "цена уровня не касалась": курс на стр. 25 снимает лимитки на первое же
    касание, оставляя вход по слому младшего ТФ, — уровень при этом остаётся активным.
    Замер на BEAT: из 21 активного уровня 8 цена уже касалась, и бот показывал их
    владельцу наравне со свежими. Расчёт различие знал, карта его не хранила.

    Отсутствие базы — НАЗВАННЫЙ отказ, а не пустая карта: «уровней нет» и «леджера нет»
    для владельца разные ответы, и второй чинится одной командой (§4.3).
    """
    try:
        conn = store.open_readonly()
    except FileNotFoundError as e:
        return NotReady(reason=f"{e} — создать: uv run python -m hunter ledger --init")
    # Колонка плотности объёма появилась в схеме 8, а мигрирует базу ПИСАТЕЛЬ: до
    # первого боевого прогона бот видит прежнюю схему, и запрос с `vrvp_density` вернул
    # бы «леджер не прочитан», то есть пустую карту. Спрашиваем схему, а не предполагаем.
    dens_col = ("COALESCE(vrvp_density, 0)"
                if "vrvp_density" in store.level_columns(conn) else "0")
    try:
        rows = conn.execute(
            # ⚠ Берутся НЕ ТОЛЬКО активные. Снятые уровни нужны разметке: отработанный
            # получает красную стрелку (стр. 25), пробитый — крестик отмены (стр. 43).
            # Без них график молчит о том, что с уровнем случилось, — а это половина
            # разметки автора. Отбор по `retired_at` держит выборку свежей: снятое
            # месяц назад на график сегодняшних баров всё равно не попадёт.
            "SELECT timeframe, side, price, zone_lo, zone_hi, entry_rule, last_seen,"
            " boundary_lo, boundary_hi, from_ms, to_ms, state,"
            " COALESCE(resolved_at, 0), " + dens_col +
            " FROM levels WHERE symbol=? AND (state='active' OR retired_at >= ?)"
            " ORDER BY price", (symbol, clock.now_ms() - RETIRED_WINDOW_MS),
        ).fetchall()
    except sqlite3.DatabaseError as e:
        return NotReady(reason=f"леджер не прочитан: {type(e).__name__} {e}")
    finally:
        conn.close()
    # Границы структуры идут на график пунктиром рядом с зоной (запрос владельца
    # 2026-08-17): зона — Value Area 70%, структура — боковик, её породивший, и
    # сливать их в одну полосу значит прятать различие.
    zones = tuple(ZoneSpec(side=r[1], timeframe=r[0], price=float(r[2]),
                           zone_lo=float(r[3]), zone_hi=float(r[4]),
                           entry_rule=r[5] or "",
                           boundary_lo=float(r[7]), boundary_hi=float(r[8]),
                           from_ms=int(r[9]), to_ms=int(r[10]),
                           state=r[11], retired_at_ms=int(r[12]),
                           vrvp_density=float(r[13]))
                  for r in rows)
    return MapRead(zones=zones, last_seen_ms=max((int(r[6]) for r in rows), default=0))


def zones_of(decision: engine.SymbolDecision,
             now_ms: int = 0) -> tuple[ZoneSpec, ...]:
    """Уровни РЕШЕНИЯ → те же полосы, что кладутся в карту леджера.

    Поля берутся ровно те же и в том же виде, что пишет `store.sync_levels`: цена ПОК,
    границы зоны, состояние, правило входа. Два пути к одной картинке — один набор полей;
    иначе ответ по монете вселенной и по монете вне её различался бы неизвестно чем.

    ⚠ С 2026-08-18 отдаются и НЕАКТИВНЫЕ уровни (в окне `RETIRED_WINDOW_MS` от
    `now_ms`; ноль — окно выключено, отдаётся всё) — ровно как в `read_map`, который
    берёт снятые ради разметки: отработанный уровень получает красную стрелку
    (стр. 25), пробитый — крестик отмены (стр. 43). Прежняя редакция отдавала только
    активные, и сборка по запросу молчала о судьбе снятых уровней там, где ответ по
    монете вселенной её показывал, — а текст ответа не мог назвать, почему сторона
    пуста. `retired_at_ms` здесь — момент СОБЫТИЯ (`resolved_at_ms`), как и в
    `read_map` (столбец `resolved_at`): значок ставится на бар пробоя, не на бар
    прогона."""
    out = []
    for m in decision.mapped:
        active = m.status.state is LevelState.ACTIVE
        resolved = int(m.status.resolved_at_ms or 0)
        if not active and now_ms and resolved < now_ms - RETIRED_WINDOW_MS:
            continue
        out.append(ZoneSpec(
            side=m.level.side.value, timeframe=m.level.timeframe,
            price=float(m.level.price), zone_lo=float(m.level.zone_lo),
            zone_hi=float(m.level.zone_hi), entry_rule=m.status.entry_rule.value,
            state=m.status.state.value,
            retired_at_ms=0 if active else resolved,
            boundary_lo=float(m.level.boundary_lo),
            boundary_hi=float(m.level.boundary_hi),
            from_ms=m.level.structure_from_ms, to_ms=m.level.structure_to_ms,
            vrvp_density=m.level.vrvp_density or 0.0))
    return tuple(sorted(out, key=lambda z: z.price))


@dataclass(frozen=True, slots=True)
class BuiltMap:
    """Карта, посчитанная ПО ЗАПРОСУ. Хранит, во сколько обошлась, — это цена ответа."""

    zones: tuple[ZoneSpec, ...]
    built_at_ms: int
    seconds: float


class OnDemand:
    """Сборка карты для монеты ВНЕ вселенной. Тем же путём, что `hunter run`.

    Три ограничения, и каждое — не осторожность, а следствие:

    * **ОДНА сборка за раз.** Лимит веса Binance считается на IP, а троттлер живёт внутри
      экземпляра `Exchange`; две сборки разом обходят собственный ограничитель и упираются
      в биржевой, за превышение которого банят по IP до трёх суток (см. `exchange`);
    * **очередь ограничена** (`BotConfig.build_queue_max`): сборка стоит десятков
      секунд (после ускорения — журнал смены 2026-08-17, раздел 20; раньше — минут),
      и обещать ответ большему числу монет значит обещать то, чего никто не дождётся;
    * **результат живёт в памяти и не идёт в леджер.** Журнал (§10.2) — свидетельство о
      том, что система решала САМА и постоянно; сложить туда монету, спрошенную однажды
      в чате, значит смешать две разные выборки в одной статистике. Прецедент этого класса
      уже был: `docs/audit/outcome-survey-2026-08-10.md`.

    ⚠ ЦЕНА ЗАМЕРЕНА ДВАЖДЫ, и числа первой смены ОТОЗВАНЫ. 2026-08-17 (LTC/USDT:USDT,
    горизонт 180): холодная сборка 345 с, на прогретом кэше 289 с — тогда «закачка это
    порядка 16% времени, остальное — счёт» и очередь в восемь монет была сорока
    минутами. После ускорения счёта (журнал смены, раздел 20: лестница интрабар-ТФ
    по образцу TV, ленивый parquet, numpy-гистограмма) живые сборки 2026-08-18 — 18–90 с (ONDO
    23.5 с, XAU 47.1 с, GPS 89.8 с; evidence/bot-restart7-2026-08-18.log). Вывод
    «кэш экономит процессорные минуты, а не трафик» пережил ускорение; численная
    пропорция закачки/счёта — нет, и заново она не мерилась.

    ⚠ Бот при этом НЕ замирает: расчёт уходит в рабочий поток, цикл событий живёт всю
    сборку (268 тактов из 278), но отдельные паузы доходят до 4.2 с при 16 мс холостого
    хода. Протокол и команды воспроизведения: `docs/audit/tgbot-channel-2026-08-17.md`.
    """

    def __init__(self, uni: Universe, cfg: BotConfig, horizon_days: int) -> None:
        self.uni = uni
        self.cfg = cfg
        self.horizon_days = horizon_days
        self.built = 0
        self.failed = 0
        self._done: dict[str, BuiltMap] = {}
        self._running: dict[str, asyncio.Task[BuiltMap | NotReady]] = {}
        self._one_at_a_time = asyncio.Lock()

    def fresh(self, symbol: str, now_ms: int) -> BuiltMap | None:
        got = self._done.get(symbol)
        if got is None:
            return None
        if now_ms - got.built_at_ms > self.cfg.map_ttl_minutes * 60_000:
            return None
        return got

    def waiting(self) -> int:
        return len(self._running)

    async def map_of(self, symbol: str, now_ms: int, *,
                     force: bool = False) -> BuiltMap | NotReady:
        """Свежая карта символа: из памяти, из идущей сборки или новой сборкой.

        `force` — пересобрать, игнорируя кэш (приказ владельца 2026-08-18: «по запросу
        пользователя обновлялась любая карта»). Идущая сборка переиспользуется и при
        `force`: она началась позже запроса и свежее любого кэша.
        """
        got = None if force else self.fresh(symbol, now_ms)
        if got is not None:
            return got
        task = self._running.get(symbol)
        if task is None:
            if len(self._running) >= self.cfg.build_queue_max:
                return NotReady(
                    reason=f"очередь сборки заполнена ({self.cfg.build_queue_max} монет) —"
                           f" сборка идёт по одной и занимает минуты; попробуйте позже")
            task = asyncio.create_task(self._build(symbol), name=f"сборка {symbol}")
            self._running[symbol] = task
        # ⚠ `shield`: тот, кто ждёт, может уйти (Telegram отменит задачу обработчика), а
        # сборка обязана доработать — её ждут и другие, и её результат кэшируется.
        return await asyncio.shield(task)

    async def _build(self, symbol: str) -> BuiltMap | NotReady:
        try:
            async with self._one_at_a_time:
                return await self._build_now(symbol)
        finally:
            self._running.pop(symbol, None)

    async def _build_now(self, symbol: str) -> BuiltMap | NotReady:
        """Собрать и посчитать. Ровно четыре шага `hunter run`, кроме записи в леджер.

        ⚠ `seconds=0` означает «поток сделок не слушаем»: профиль строится по СВЕЧАМ с
        2026-08-12 (решение владельца), и живой поток карте уровней больше не нужен.
        Задачи наблюдения при этом всё равно поднимаются и тут же снимаются — цена в один
        сокет за сборку, зато путь остаётся ТЕМ ЖЕ, что у боевого прогона.
        """
        started = clock.monotonic_ns()
        one = replace(self.uni, symbols=(symbol,))
        log.info("сборка по запросу начата", символ=symbol, горизонт_суток=self.horizon_days)
        try:
            # ⚠ `frame_bars=BARS_ON_CHART` (2026-08-18, п. 3 приказа владельца): сборка
            # по запросу не качает минутки и не строит уровни структур, чей конец старше
            # кадра графика своего ТФ, — фильтр «у структуры» (`near_structure`, та же
            # рамка 180) всё равно скрыл бы их из ответа. Боевой прогон рамки не передаёт
            # и строит всё: леджер считается по полной карте, а не по кадру ответа.
            report, sources = await run.collect(one, 0, 0, self.horizon_days,
                                                frame_bars=BARS_ON_CHART)
        except (ccxt.BaseError, CapabilityMissing) as e:
            self.failed += 1
            log.degraded("сборка по запросу не удалась", символ=symbol,
                         причина=f"{type(e).__name__} {e}")
            return NotReady(reason=f"{symbol}: данные не собраны ({type(e).__name__})")
        decided = await asyncio.to_thread(run.decide_once, report, one, sources,
                                          BARS_ON_CHART)
        got = decided.get(symbol)
        if got is None:
            self.failed += 1
            log.degraded("сборка по запросу: рядов нет", символ=symbol)
            return NotReady(reason=f"{symbol}: рядов не собрано — карта не строится")
        spent = (clock.monotonic_ns() - started) / 1e9
        built = BuiltMap(zones=zones_of(got, clock.now_ms()),
                         built_at_ms=clock.now_ms(), seconds=spent)
        self._done[symbol] = built
        self.built += 1
        log.info("сборка по запросу завершена", символ=symbol, секунд=round(spent, 1),
                 уровней=len(built.zones))
        return built


# --- графики и текст --------------------------------------------------------------


def _beyond_price(z: ZoneSpec, last_price: float) -> bool:
    """Цена прошла СКВОЗЬ структуру зоны: лонговая целиком выше цены, шортовая ниже.

    Границы, а не зона: коробка ХАЙ…ЛОЙ и есть структура (стр. 30), а заход внутрь неё
    проходом сквозь не является. То же правило и тем же способом считает
    `levels.status` (`LevelStatus.price_beyond`) — один смысл обязан считаться одинаково
    в тексте и на картинке, иначе они снова разойдутся.
    """
    return z.boundary_lo > last_price if z.side == "long" else z.boundary_hi < last_price


def zones_for_chart(
    zones: tuple[ZoneSpec, ...], timeframe: str, last_price: float,
    near_local: int = 4, near_senior: int = 6, first_ms: int = 0,
    now_ms: int = 0,
) -> list[ZoneSpec]:
    """Отбор зон для графика ТФ X: как в обзорах автора — не вся карта разом.

    Правило: зоны СВОЕГО ТФ и старших; «локальные» (младше 4ч) — только ближайшие к
    цене (`near_local` сверху и снизу): скрин автора держит единицы зон, и полная карта
    на одном ТФ превращается в жёлтую заливку (проверено первым же рендером — 57 зон
    закрыли весь график).

    ⚠ С 2026-08-17 кап есть И НА СТАРШИЕ (`near_senior` на сторону). Прежняя редакция
    пропускала старшие ВСЕ, и живой ответ по BTC показал цену этого: карта выросла до
    2177 строк, на график 4ч шло 79 зон, плашки цен выезжали за верх кадра, компоновка
    ломалась (снимок разбора: пол-экрана пустых, столбец плашек до 66817 при шкале до
    64100). Сами числа 4 и 6 ПОДОБРАНЫ ПО ЧИТАЕМОСТИ КАРТИНКИ, а не замерены: замер
    дал только «кап нужен» (79 зон ломали кадр), а сколько именно — решено глазами;
    старших больше, чем локальных, потому что курс стр. 48 считает старший ТФ
    сильнее, но «сильнее» не значит «все разом».

    `first_ms` — левый край окна графика. Снятый уровень, чьё событие (`retired_at_ms`)
    старше кадра, не рисуется вовсе: значок поставить негде (`bar_index` вернёт None),
    и уровень лёг бы полной линией — как живой, которым не является.

    `now_ms` — рамка фильтра «уровень у структуры» (`near_structure`): живой уровень,
    скрытый из СВОДКИ, не имеет права остаться на графике — картинка и текст обязаны
    говорить об одном наборе уровней (тот же класс расхождения, что «57 на графике
    против 56 в тексте»). Снятых фильтр не касается: их судьбу решает `retired_at_ms`.
    """
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    rank = order.get(timeframe, 0)
    fit = [z for z in zones if order.get(z.timeframe, 0) >= rank]
    if first_ms > 0:
        fit = [z for z in fit if z.state == "active" or z.retired_at_ms >= first_ms]
    if now_ms > 0:
        fit = [z for z in fit if z.state != "active" or near_structure(z, now_ms)]
    # 2026-08-18, приказ владельца «допускаешь НАСЛОЕНИЕ противоположных зон!»: активная
    # зона, СКВОЗЬ которую цена уже прошла, на график не идёт. Лонговая зона выше цены —
    # сопротивление, а не поддержка (стр. 43 «Уровень лонг/шорт менятся для нас на
    # противоположный»), и именно такие зоны накладывались на встречные: замер по карте
    # 5 символов дал 385–418 пар наслоения у одного символа при 25–35% активных уровней
    # не с той стороны от цены. Снятых фильтр не касается — у них своя разметка (стрелка
    # отработки, крестик отмены), она и рассказывает историю цены.
    fit = [z for z in fit if z.state != "active" or not _beyond_price(z, last_price)]

    def nearest(cands: list[ZoneSpec], cap: int) -> list[ZoneSpec]:
        above = sorted((z for z in cands if z.price >= last_price), key=lambda z: z.price)
        below = sorted((z for z in cands if z.price < last_price), key=lambda z: -z.price)
        return above[:cap] + below[:cap]

    senior = nearest([z for z in fit if z.timeframe in SENIOR_TFS], near_senior)
    local = nearest([z for z in fit if z.timeframe not in SENIOR_TFS], near_local)
    return senior + local


def pp_zones(bars: list[Bar], timeframe: str) -> list[ZoneSpec]:
    """Зоны подтверждённых ПП по свежим барам — чистый расчёт для отображения."""
    sw = swings.detect(bars)
    if isinstance(sw, NotReady):
        return []
    return [ZoneSpec(side=pp.side.value, timeframe=timeframe,
                     price=float(pp.zone_hi if pp.side.value == "short" else pp.zone_lo),
                     zone_lo=float(pp.zone_lo), zone_hi=float(pp.zone_hi), kind="pp")
            for pp in pereprior.detect(bars, sw, timeframe)]


ZONES_PER_SIDE = 5
"""Сколько зон показывать на сторону. Число взято У АВТОРА, а не выбрано.

Пост 2026-08-03 (`research/author_markup/2026-08-03_overcarder.md`) называет по BTC четыре
зоны словами и по ETH — шесть. Пять — середина замеренного, и именно столько человек
удерживает глазами. Прежняя редакция печатала ВСЕ активные: замер по BTC 2026-08-17 —
69 уровней, 44 строки, 3282 знака в одном сообщении канала.
"""

FRESH_MAP_MAX_MIN = 60
"""Старше скольких минут карта называется УСТАРЕВШЕЙ первой строкой, а не последней.

Выведено из такта службы (`service.CYCLE_SECONDS` = 5 мин): карта, не обновлявшаяся
двенадцать тактов, означает, что служба стоит. Первая публикация 2026-08-17 ушла с картой
возрастом 1035 минут, и сказано об этом было последней строкой мелким шрифтом.
"""


# `_fmt_zone` (строка «🟢 63 595 · 15м ★ · -1.2% · зона … · лимитки») ЗАМЕЩЁН
# `span_line` внутри `compose_text` — см. комментарий у бывшего ENTRY_MARK выше.


TEXT_LIMIT = 4096
"""Предел одного сообщения Telegram (Bot API, `sendMessage`: 1-4096 characters).

⚠ Не запас «на всякий случай», а граница отказа: сообщение длиннее предела Telegram НЕ
режет — он отвечает `Bad Request: message is too long`, и вся сводка пропадает целиком.
Замер на живой карте BTC (69 активных уровней, 2026-08-17): 3398 знаков, то есть до
предела оставалось меньше сотни строк, а старших уровней в отборе не ограничено ничем.
"""


def _fit(lines: list[str], keep_tail: int) -> str:
    """Уложить сводку в предел Telegram, НАЗВАВ выброшенное числом.

    Выбрасываются строки с конца перечня зон — то есть самые локальные и самые дальние
    от цены; шапка со счётом уровней и хвост (источник карты, оговорка) не трогаются
    никогда: без них сообщение перестаёт отвечать на вопрос «откуда это».
    """
    text = "\n".join(lines)
    if len(text) <= TEXT_LIMIT:
        return text
    head, tail = lines[:-keep_tail], lines[-keep_tail:]
    dropped = 0
    while head:
        note = f"  … и ещё {dropped} строк не поместились в сообщение Telegram"
        text = "\n".join([*head, note, *tail])
        if len(text) <= TEXT_LIMIT:
            return text
        head.pop()
        dropped += 1
    return "\n".join(tail)[:TEXT_LIMIT]


def _away(z: ZoneSpec, price: float) -> float:
    """Расстояние уровня до цены в долях. Ноль цены — все на равном нуле (§4.3)."""
    return abs(z.price - price) / price if price > 0 else 0.0


def _passed(z: ZoneSpec, price: float) -> bool:
    """Цена уже прошла уровень: лонг над ценой / шорт под ней (стр. 30: вход ОТ уровня)."""
    return (z.side == "long" and z.price >= price) or (
        z.side == "short" and z.price < price)


def _waiting_break(z: ZoneSpec) -> bool:
    """Уровень ждёт подтверждения сломом, лимиткой он больше не торгуется.

    Стр. 25: лимитки снимаются на первое касание — остаётся вход по слому младшего ТФ.
    Значит «по факту»-уровень (`confirmation`) — НЕ готовый вход, и в списке он обязан
    стоять ПОСЛЕ лимиток. Живой ответ BTC 2026-08-17 показывал его первой строкой —
    приглашение войти там, где методология входа не даёт. Пустое правило (строка карты
    до схемы 6) считается тем же классом: раз не записано, что лимитка жива, честнее
    не подавать её как готовую.
    """
    return z.entry_rule not in ("limit", "retest_flipped")


def near_structure(z: ZoneSpec, now_ms: int) -> bool:
    """Уровень «у структуры»: окно породившей его структуры ещё в кадре графика своего ТФ.

    Фильтр взят у автора, а не придуман. Видео-обзор BTC 17.08.2026
    (`docs/audit/video-2026-08-17-btc-review.md`, находка №2): уровни он размечает
    ТОЛЬКО у структур, в речи дважды — «мне нужна структурка, без неё шорты не
    рассматриваю», «выглядит не структурно — в счёт брать не буду». У него на 1ч один
    шорт — у нас на тот же момент 54: карта копила уровни давно отработанных структур.

    Критерий геометрический, без выдуманного порога: рамка — тот же `BARS_ON_CHART`
    (180 баров), что определяет кадр графика ответа. Структура, чей конец (`to_ms`)
    старше кадра СВОЕГО ТФ, читателю не видна — автор такие уровни не показывает.

    ⚠ Честная семантика (проверка фальсификатора 2026-08-17): `to_ms` пишется при
    РОЖДЕНИИ уровня (`levels.py`: `structure_to_ms` = конец окна) и не обновляется,
    поэтому де-факто фильтр отвечает «уровень моложе 180 баров своего ТФ». Рамка
    180×шаг даёт допуск от 15 ч (5м) до 180 сут (1Д) — лесенка оставленных по
    старшинству ТФ есть свойство рамки, а не рынка. Зоны ПП (`pp_zones`) фильтр
    минуют законно: они считаются по свежескачанным барам, их структура в кадре
    по построению.

    ⚠ Уровень без записанного окна (`to_ms == 0`, строки карты до появления поля) НЕ
    скрывается: молчаливое исчезновение уровня из-за возраста строки в базе неотличимо
    от «уровня не было» (§4.3). Неизвестный ТФ — по той же причине.
    """
    if z.to_ms <= 0:
        return True
    step = TIMEFRAME_MS.get(z.timeframe)
    if step is None:
        return True
    return z.to_ms >= now_ms - BARS_ON_CHART * step


def tradable_counts(
    zones: tuple[ZoneSpec, ...], price: float, now_ms: int,
) -> tuple[int, int]:
    """Торгуемые уровни по сторонам — ОДНО число на текст и подпись графика.

    Считается тем же путём, что заголовки блоков сводки («Покупки — 5 из N»): живые →
    фильтр структур → склейка → отсев пройденных. Живой ответ BTC 2026-08-17 печатал на
    графике «91 лонг / 133 шорт», а в тексте «36 покупок» — два несовместимых числа в
    одном ответе. `now_ms` обязателен по той же причине: забытый фильтр в одном из двух
    путей вернул бы это расхождение.
    """
    _, unique, _ = live_unique(zones, price, now_ms)
    t = [z for z in unique if not _passed(z, price)]
    return (len([z for z in t if z.side == "long"]),
            len([z for z in t if z.side == "short"]))


def _strength(z: ZoneSpec, order: dict[str, int]) -> tuple[int, float]:
    """Сила уровня по стр. 22: «Сила уровня определяется ТФ и объемом».

    Кортеж, а НЕ одно число: курс называет два признака и шкалы между ними не даёт.
    Свёртка вида `вес_тф * k + плотность` требовала бы придуманного `k` — а придуманный
    порог в этом проекте запрещён наравне с «результат плохой, добавим фильтр».
    Поэтому ТФ решает первым (стр. 48: старший сильнее), плотность объёма — вторым.

    ⚠ Плотность, а НЕ доля объёма. Доля («сколько композита лежит в зоне») растёт вместе
    с шириной зоны — ранговая корреляция +0.716 на 9715 уровнях, — и отбор по ней
    означал бы «показываем самую широкую полосу». Разбор: `levels.Level.vrvp_zone_bins`.
    """
    return order.get(z.timeframe, 0), z.vrvp_density


def live_unique(
    zones: tuple[ZoneSpec, ...], price: float, now_ms: int,
) -> tuple[list[ZoneSpec], list[ZoneSpec], list[ZoneSpec]]:
    """ЖИВЫЕ уровни и их дедубликация по цене — ОДНА на текст и подпись графика.

    С 2026-08-17 `read_map` отдаёт и снятые уровни (графику нужны значки судьбы), и
    считать «активных N» по всей карте — ложь: живой ответ по BTC напечатал в подписи
    «1094 лонг / 1083 шорт» при 36/113 в тексте. Дедубликация — как в сводке: один и
    тот же уровень на нескольких ТФ для читателя ОДИН, остаётся строка старшего ТФ
    (курс, стр. 48: старший сильнее). Пока подпись и текст считали по-разному, читатель
    видел два несовместимых числа в одном ответе.

    С фильтром «уровень у структуры» (`near_structure`, 2026-08-17): скрытые им уровни
    возвращаются ТРЕТЬИМ списком — сводка обязана назвать их числом, а не растворить.
    """
    alive = [z for z in zones if z.state == "active"]
    live = [z for z in alive if near_structure(z, now_ms)]
    off_struct = [z for z in alive if not near_structure(z, now_ms)]
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    best: dict[tuple[str, str], ZoneSpec] = {}
    # ⚠ СИЛА ПО ОБЪЁМУ РЕШАЕТ ПРИ РАВНОМ ТФ (2026-08-19, приказ владельца: "примени
    # силу по объёму в отборе уровней"). Стр. 22: «Сила уровня определяется ТФ и объемом» —
    # два измерения, и второе в отборе не участвовало вовсе. При равном ТФ прежняя
    # редакция оставляла ПЕРВЫЙ встреченный, то есть решал порядок строк из SQL
    # (`ORDER BY price`), а не свойство уровня.
    #
    # Измерения остаются РАЗДЕЛЬНЫМИ, в одно число не сворачиваются: ТФ старше — сильнее
    # (стр. 48), и объём его не перебивает. Курс единой шкалы «ТФ + объём» не даёт, а
    # выдуманный вес одного против другого был бы придумкой из головы.
    for z in live:
        key = (z.side, _fmt_price(z.price))
        kept = best.get(key)
        if kept is None or _strength(z, order) > _strength(kept, order):
            best[key] = z
    # ⚠ ВТОРОЙ ПРОХОД — склейка по ЗОНЕ, а не по строке цены (2026-08-17, разбор живого
    # ответа BTC): «64 100 · 15м» и «64 099 · 15м» с перекрывающимися зонами прошли
    # дедубликацию как разные уровни и заняли 2 из 5 видимых строк. Зона — это и есть
    # область уровня (стр. 23-26: уровень строится ЗОНОЙ, не линией), поэтому два уровня
    # одной стороны, у которых цена КАЖДОГО лежит в зоне ДРУГОГО, для читателя один.
    # Вложение обязано быть ВЗАИМНЫМ: первая редакция клеила по одностороннему («цена
    # внутри чужой зоны») — и зона 1Д шириной 60 751–64 100 проглотила уровни 4ч/1ч с
    # ценами на 5% выше собственной цены 60 800; трейдер терял ближний уровень, получая
    # взамен дальний. Остаётся старший ТФ (стр. 48), при равном — более широкая зона.
    # Порог не выдумывается — критерий геометрический.
    kept_zones: list[ZoneSpec] = []
    # Порядок ключа: ТФ → ПЛОТНОСТЬ ОБЪЁМА → ширина зоны. Ширина — не критерий курса, а
    # разрешение ничьей, придуманное проектом; объём назван курсом прямо (стр. 22),
    # поэтому стоит выше. При нулевой плотности (композит не считался) ключ вырождается
    # в прежний, то есть старые карты ведут себя как раньше. Сила берётся ТОЙ ЖЕ
    # функцией, что и в первом проходе: два ключа отбора, написанных порознь, разъедутся
    # при первой же правке — а разъехавшись, дадут разный ответ на один вопрос.
    for z in sorted(best.values(),
                    key=lambda z: (*(-v for v in _strength(z, order)),
                                   -(z.zone_hi - z.zone_lo))):
        if any(k.side == z.side and k.zone_lo <= z.price <= k.zone_hi
               and z.zone_lo <= k.price <= z.zone_hi
               for k in kept_zones):
            continue
        kept_zones.append(z)
    unique = sorted(kept_zones, key=lambda z: _away(z, price))
    return live, unique, off_struct


def compose_text(symbol: str, zones: tuple[ZoneSpec, ...], pps: list[ZoneSpec],
                 origin: str, charts_missing: tuple[str, ...] = (), *,
                 price: float = 0.0, stale_min: int = 0, now_ms: int = 0,
                 dominance: str = "") -> str:
    """Сводка ДЛЯ ЧИТАТЕЛЯ КАНАЛА. Устройство взято у автора, а не придумано.

    ⚠ ПЕРЕПИСАНО 2026-08-17 по разбору `docs/audit/bot-review-2026-08-17.md`. Прежняя
    редакция печатала все активные уровни подряд: замер по BTC — 69 уровней, 44 строки,
    3282 знака, из них «правило входа не записано (строка карты до схемы 6)» — тридцать
    раз. Ни текущей цены, ни расстояния до уровня в сообщении не было вовсе.

    Что взято из поста автора (`research/author_markup/2026-08-03_overcarder.md`):
      * ЦЕНА названа первой строкой — у автора она есть в каждом посте;
      * зон немного и у каждой РОЛЬ словами («лимитки», «работать по факту»);
      * порядок — от цены наружу, а не по таймфрейму: читателю важно, что близко.

    ⚠ ПЕРЕПИСАНО ВТОРОЙ РАЗ 2026-08-17 по приказу владельца «приведи наш формат ответа
    и графики к виду как у призрака» — после сравнения нашего ответа с постом автора
    от 17.08.2026. Разница была не в содержании (наши 64 948·4ч + 65 200·1ч ≈ его
    «64890–65200»), а в подаче: у нас таблица прибора построчно, у него — «Диапазон
    интереса для открытия шортовых позиций: 64890–65200» одной строкой. Отсюда:
      * шапка «🪙 #BTC» — как у автора («🪙#BTC - разбор от Prizrak»);
      * пересекающиеся зоны одной стороны склеены в «диапазон интереса» (`spans`);
      * шорты первым блоком (пост 17.08 начинается с шорт-сценария);
      * легенда прибора («★ — старший таймфрейм…») убрана, роли — словами в строке.

    `price` — последняя цена закрытого бара младшего графика. Ноль означает «бары не
    пришли»: тогда расстояния не печатаются, а не подставляются нулём (§4.3).

    `stale_min` — возраст карты в минутах. Старше `FRESH_MAP_MAX_MIN` он выносится ПЕРВОЙ
    строкой: карта суточной давности выглядит как сегодняшняя, и молчать об этом нельзя.

    `now_ms` — момент ответа, рамка фильтра «уровень у структуры» (`near_structure`).
    Ноль — фильтр выключен (все структуры «в кадре»); боевой путь передаёт всегда.

    `dominance` — готовая строка о доминации BTC (см. `Bot._dominance_line`); пустая —
    не печатается. Шаг взят у автора: видео 17.08.2026, ~1:14–1:36 — переход на график
    доминации как отдельный шаг разбора.
    """
    base = symbol.split("/")[0]
    live, unique, off_struct = live_unique(zones, price, now_ms)
    hidden_dupes = len(live) - len(unique)

    # Шапка — как в посте автора 17.08.2026: «🪙#BTC …», цена рядом.
    head = f"🪙 #{base} · {_fmt_price(price)}" if price > 0 else f"🪙 #{base}"
    lines = [head, ""]
    if stale_min > FRESH_MAP_MAX_MIN:
        # Устаревшая карта — ПЕРВОЙ строкой. Внизу мелким шрифтом её не читают, а
        # уровни суточной давности выглядят как сегодняшние.
        lines.append(f"⚠ карта не обновлялась {_fmt_age(stale_min)} — уровни могли уже"
                     f" отработать")
        lines.append("")
    if dominance:
        lines.append(dominance)
        lines.append("")

    def tradable_first(z: ZoneSpec) -> tuple[int, int, float]:
        """Порядок: торгуемая сторона → лимитки раньше «по факту» → ближе к цене.

        Первый ключ — сторона цены: лимитка на покупку ставится НИЖЕ цены, на продажу —
        ВЫШЕ (стр. 30: «вход от уровня»); лонг-зона над ценой — уже пройденное. Второй
        ключ — готовность входа (стр. 25, см. `_waiting_break`): живой ответ BTC
        2026-08-17 ставил «по факту»-уровень первой строкой покупок, а лимитки — ниже,
        то есть приоритет был обратен торгуемости.

        ⚠ Замер 2026-08-17, BTC, карта возрастом 17 часов: из 69 активных зон **12 стоят
        по неторгуемую сторону** (10 лонгов над ценой, 2 шорта под), и первая редакция
        этого формата вывела в «Покупки» пять зон, все выше цены. Сортировка по одному
        лишь расстоянию скрывала различие.
        """
        return (1 if _passed(z, price) else 0,
                1 if _waiting_break(z) else 0,
                _away(z, price))

    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}

    def spans(rows: list[ZoneSpec]) -> list[list[ZoneSpec]]:
        """Склейка ПЕРЕСЕКАЮЩИХСЯ зон одной стороны в «диапазон интереса».

        Взято из поста автора 17.08.2026: «Диапазон интереса для открытия шортовых
        позиций: 64890–65200» — ОДНА строка, хотя это структура 4ч плюс локальная
        внутри неё, то есть два уровня. У нас те же два: 64 948·4ч и 65 200·1ч с
        перекрывающимися зонами. Склейка транзитивная по перекрытию зон; порог не
        выдумывается — критерий геометрический (зона либо перекрывается, либо нет).

        ⚠ С 2026-08-18 зона старшего ТФ, целиком ВМЕЩАЮЩАЯ зону младшего, в склейку
        не входит — печатается своей строкой. Причина замером: транзитивная склейка
        сквозь зону 1Д 60 751–64 100 (5.4% цены) давала «диапазон интереса
        60 800–64 095 (1Д+4ч+15м)» — коридор в десять раз шире авторских (~0.5%).
        Основание — рисунок курса стр. 32: у большой структуры 1Д свой уровень
        (красная линия), у локальных внутри неё — свои (жёлтые), нарисованы ВСЕ и
        ОТДЕЛЬНО, в один диапазон не слиты; «закуп делать на все уровни». Критерий
        геометрический (полное вложение зоны младшего ТФ), порог не выдумывается.
        Частичное перекрытие зон сопоставимых ТФ клеится как раньше — это и есть
        случай поста автора 17.08.
        """
        containing = [z for z in rows if any(
            o is not z
            and order.get(z.timeframe, 0) > order.get(o.timeframe, 0)
            and z.zone_lo <= o.zone_lo and o.zone_hi <= z.zone_hi
            for o in rows)]
        ctx_ids = {id(z) for z in containing}
        groups: list[list[ZoneSpec]] = []
        for z in sorted((z for z in rows if id(z) not in ctx_ids),
                        key=lambda z: z.zone_lo):
            if groups and z.zone_lo <= max(k.zone_hi for k in groups[-1]):
                groups[-1].append(z)
            else:
                groups.append([z])
        return groups + [[z] for z in containing]

    def role(g: list[ZoneSpec]) -> str:
        """Роль СЛОВАМИ — как у автора («лимитками заранее», «работать по факту»)."""
        waiting = [z for z in g if _waiting_break(z)]
        if not waiting:
            return "лимитки заранее"
        if len(waiting) == len(g):
            return "вход по факту — после слома на младшем ТФ"
        return "лимитки; часть — по факту"

    def span_line(g: list[ZoneSpec]) -> str:
        by_seniority = sorted(g, key=lambda z: -order.get(z.timeframe, 0))
        tfs = "+".join(dict.fromkeys(
            TF_LABEL.get(z.timeframe, z.timeframe) for z in by_seniority))
        g_lo, g_hi = min(z.zone_lo for z in g), max(z.zone_hi for z in g)
        if len(g) > 1:
            # Края диапазона — по ЗОНАМ, а не по ПОКам (исправлено 2026-08-18).
            # Прежняя редакция печатала min–max ПОКов, а метку «цена уже здесь»
            # считала по зонам: живой BTC показал «Шорты: 64 948–65 200 · цена уже
            # здесь» при цене 64 447 — цена была В ЗОНЕ, но ВНЕ напечатанного
            # коридора, читатель видел противоречие. У автора края диапазона — тоже
            # края зон: его «64890–65200» шире расстояния между нашими ПОКами тех же
            # структур и совпадает с кромками зон (пост 17.08.2026).
            core = (f"— Диапазон интереса: {_fmt_price(g_lo)}–{_fmt_price(g_hi)}"
                    f" ({tfs})")
        else:
            z = g[0]
            core = (f"— {_fmt_price(z.price)} ({tfs}), зона "
                    f"{_fmt_price(z.zone_lo)}–{_fmt_price(z.zone_hi)}")
        marks = ""
        if g_lo <= price <= g_hi:
            marks += " · цена уже здесь"
        # Метка встречной — только когда УРОВЕНЬ (ПОК) одной стороны лежит в ЗОНЕ
        # другой, а не при любом касании зон (исправлено 2026-08-18: касание в
        # 3 пункта у зон по 600 пунктов помечало половину карты). Сосуществование
        # встречных структур в одном районе — норма курса (стр. 39: «стоповый
        # объем… и на его зоне/ПОКе формирует новое большое накопление»; стр. 43 —
        # переворот); значимое отношение курс называет через «на зоне/ПОКе».
        if any(o.side != g[0].side
               and (g_lo <= o.price <= g_hi
                    or any(o.zone_lo <= z.price <= o.zone_hi for z in g))
               for o in unique if not _passed(o, price)):
            marks += " · ⚠ пересекается со встречной зоной"
        # ⚠ `_away` возвращает ДОЛЮ, не проценты: первая редакция этой строки сравнивала
        # долю с 5 и не пометила бы дальним ни один уровень никогда. Порог 5% ПОДОБРАН,
        # а не замерен: помечать дальнее надо (пост автора называет расстояние), а
        # где начинается «дальнее» — ни курс, ни корпус числа не дают.
        away = min(_away(z, price) for z in g) * 100
        far = f" · {away:.0f}% от цены" if away > 5 else ""
        # ⚠ Сила по объёму ПЕЧАТАЕТСЯ (2026-08-19). С этой правки плотность объёма
        # решает, какой из двух уровней одного ТФ читатель увидит, — а всё, на чём стоит
        # решение, обязано быть предъявлено.
        vol = max(z.vrvp_density for z in g)
        # «×N к среднему», а не проценты: печатается ПЛОТНОСТЬ — во сколько раз объём
        # на ценовую строку в зоне выше среднего по композиту. Единица означает «зона
        # ничем не выделяется», поэтому называется только сгущение. Ноль — «не
        # считалось» (карта до схемы 8), и тогда строки нет: печатать «×0» значило бы
        # назвать пустым непосчитанное.
        strength = f" · объём ×{vol:.1f} к среднему" if vol >= 1.2 else ""
        return f"{core} · {role(g)}{strength}{far}{marks}"

    def block(side: str, title: str) -> list[str]:
        """Зоны ОДНОЙ стороны, склеенные в диапазоны, от ближнего к дальнему.
        Группировка по стороне, а не по «выше/ниже цены»: у автора покупки и продажи —
        разные части поста, и зелёная строка в красном блоке читается как ошибка."""
        rows = sorted((z for z in unique if z.side == side), key=tradable_first)
        if not rows:
            # Пустая сторона НАЗЫВАЕТ ПРИЧИНУ, а не молчит «пока нет» (2026-08-18,
            # вопрос владельца «почему по ACE не найдены шорт-зоны?!»). У ACE все
            # шорт-уровни имели статус worked_off/flipped, ответ показывал пустоту —
            # и «зон нет» читалось как «прибор ничего не видит», хотя прибор видел
            # и снял их по правилам (отработка — стр. 25, переворот — стр. 43).
            # Считается только то, что лежит в самой карте; будущего никто не обещает.
            worked = sum(1 for z in zones
                         if z.side == side and z.state == "worked_off")
            flipped = sum(1 for z in zones
                          if z.side == side and z.state == "flipped")
            hidden = sum(1 for z in off_struct if z.side == side)
            why = []
            if worked:
                why.append(f"{worked} отработано ценой")
            if flipped:
                why.append(f"{flipped} перевёрнуто пробоем")
            if hidden:
                why.append(f"{hidden} скрыто — структура старше кадра графика")
            if why:
                return [f"{title} свежих нет ({', '.join(why)})", ""]
            return [f"{title} пока нет — структур этой стороны карта не нашла", ""]
        passed = [z for z in rows if tradable_first(z)[0] == 1]
        live_side = [z for z in rows if tradable_first(z)[0] == 0]
        groups = sorted(spans(live_side),
                        key=lambda g: min(_away(z, price) for z in g))
        shown = groups[:ZONES_PER_SIDE]
        out = [title]
        out += [span_line(g) for g in shown]
        rest = groups[len(shown):]
        if rest:
            # Свёрнутый хвост НАЗЫВАЕТСЯ числом (§4.3): молчаливое сокращение списка
            # неотличимо от того, что этих уровней в карте не было.
            aways = sorted(min(_away(z, price) for z in g) * 100 for g in rest)
            out.append(f"… ещё {len(rest)} дальше — от {aways[0]:.1f}% до "
                       f"{aways[-1]:.0f}% от цены")
            # ⚠ САМЫЙ СИЛЬНЫЙ ПО ОБЪЁМУ В ХВОСТЕ — НАЗЫВАЕТСЯ (2026-08-19). Показанное
            # отбирается по РАССТОЯНИЮ до цены, и это правильно для очереди работы, но
            # расстояние силой не является: стр. 22 называет силой ТФ и объём. Уровень
            # сильнее всех показанных, ушедший в «… ещё N дальше», был бы скрыт ровно
            # тем же способом, каким скрывались все четыре дефекта смены, — молчаливым
            # сокращением. Порог не выдумывается: называется тогда и только тогда, когда
            # хвост сильнее ЛЮБОГО показанного.
            best_rest = max(rest, key=lambda g: max(z.vrvp_density for z in g))
            rest_vol = max(z.vrvp_density for z in best_rest)
            shown_vol = max((z.vrvp_density for g in shown for z in g), default=0.0)
            if rest_vol > shown_vol and rest_vol >= 1.2:
                out.append(f"   ⚠ сильнейший по объёму — не выше: "
                           f"{span_line(best_rest)[2:]}")
        if passed:
            # Пройденные НАЗЫВАЮТСЯ, но не подаются как торгуемые: по карте цена их
            # уже прошла, а карта может быть старой — читатель вправе знать оба факта.
            out.append(f"({len(passed)} цена уже прошла — на графике они есть)")
        return [*out, ""]

    lines += block("short", "🔴 Шорты:")
    lines += block("long", "🟢 Лонги:")

    if pps:
        lines.append("🟣 Переприор по свежим барам: " + ", ".join(
            f"{'лонг' if p.side == 'long' else 'шорт'} {_fmt_price(p.zone_lo)}–"
            f"{_fmt_price(p.zone_hi)} ({TF_LABEL.get(p.timeframe, p.timeframe)})"
            for p in pps))
        lines.append("")
    if charts_missing:
        lines.append(f"⚠ график {', '.join(charts_missing)} не построен — биржа не отдала"
                     f" бары; зоны выше от этого не зависят")
        lines.append("")

    # Служебный хвост ОДНОЙ строкой (решение владельца 2026-08-17 «приведи к виду как
    # у призрака»: пост автора не носит легенду прибора). Но скрытое по-прежнему
    # НАЗЫВАЕТСЯ числом (§4.3): «уровня нет в списке» и «уровень скрыт» — разные
    # ответы, а молчаливое сокращение неотличимо от отсутствия в карте.
    notes = []
    if hidden_dupes:
        notes.append(f"{hidden_dupes} слились с соседями по зоне")
    if off_struct:
        note = f"{len(off_struct)} скрыто — структура старше кадра графика"
        # ⚠ СИЛЬНЕЙШИЙ ПО ОБЪЁМУ СРЕДИ СКРЫТЫХ — НАЗЫВАЕТСЯ (2026-08-19). Возрастной
        # фильтр снимает основную массу карты: замер на кадрах прогона `last` — 340
        # активных уровней из 376 у пяти символов. В четырёх символах он согласен с
        # силой (сильнейший актив показан), в пятом — нет: у POL сильнейший актив
        # (шорт 4ч, 18.32% композита) скрыт возрастом, а лучший показанный несёт 7.29%.
        # Владелец видел «43 скрыто» и не мог узнать, что среди них сильнейший.
        #
        # Фильтр НЕ отменяется: он взят у автора (видео-обзор 17.08.2026 — «мне нужна
        # структурка, без неё шорты не рассматриваю»), и заменять правило источника
        # собственным замером запрещено. Скрытое именно НАЗЫВАЕТСЯ — как свёрнутый
        # хвост строкой выше.
        shown_max = max((z.vrvp_density for z in unique), default=0.0)
        top_hidden = max(off_struct, key=lambda z: z.vrvp_density, default=None)
        if top_hidden is not None and top_hidden.vrvp_density > max(shown_max, 1.2):
            note += (f"; сильнейший из них по объёму — "
                     f"{'лонг' if top_hidden.side == 'long' else 'шорт'} "
                     f"{_fmt_price(top_hidden.price)} "
                     f"({TF_LABEL.get(top_hidden.timeframe, top_hidden.timeframe)}, "
                     f"объём ×{top_hidden.vrvp_density:.1f} к среднему против "
                     f"×{shown_max:.1f} у показанных)")
        notes.append(note)
    service = ("⚙ " + " · ".join(notes)) if notes else ""
    tail = [x for x in (service, origin,
                        "Это карта уровней, а не торговая рекомендация.") if x]
    return _fit(lines + tail, len(tail))


def _fmt_age(minutes: int) -> str:
    """Возраст человеческими словами: «17 ч», «3 сут», а не «1035 мин»."""
    if minutes < 90:
        return f"{minutes} мин"
    if minutes < 60 * 36:
        return f"{minutes // 60} ч"
    return f"{minutes // 1440} сут"


# --- отправка ---------------------------------------------------------------------


class Pacer:
    """Темп отправки по ДОКУМЕНТИРОВАННЫМ лимитам Telegram, а не по надежде.

    core.telegram.org/bots/faq (прочитано 2026-08-17):
      «In a group, bots are not be able to send more than 20 messages per minute»;
      «In a single chat, avoid sending more than one message per second».

    Оба соблюдаются ЗДЕСЬ, в одной точке, потому что превышение стоит не отказа, а
    молчания: Telegram отвечает `retry_after`, сообщение не доходит, и без этой очереди
    оно терялось бы тем тише, чем больше в группе людей.

    Отложенные секунды и просьбы подождать СЧИТАЮТСЯ: «лимит не мешал» без числа
    неотличимо от «никто не смотрел».
    """

    PER_MINUTE = 20
    MIN_INTERVAL_S = 1.0

    def __init__(self) -> None:
        self._sent: dict[str, deque[float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.delayed_s = 0.0
        self.flood_waits = 0
        self.lost = 0

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def send(self, chat: int | str, what: str,
                   call: Callable[[], Awaitable[object]]) -> bool:
        key = str(chat)
        async with self._lock(key):
            await self._wait_turn(key)
            ok = await self._deliver(key, what, call)
            self._sent.setdefault(key, deque()).append(clock.monotonic_ns() / 1e9)
            return ok

    async def _wait_turn(self, key: str) -> None:
        now = clock.monotonic_ns() / 1e9
        queue = self._sent.setdefault(key, deque())
        while queue and now - queue[0] > 60.0:
            queue.popleft()
        wait = 0.0
        if queue:
            wait = max(wait, self.MIN_INTERVAL_S - (now - queue[-1]))
        if len(queue) >= self.PER_MINUTE:
            wait = max(wait, 60.0 - (now - queue[0]))
        if wait > 0:
            self.delayed_s += wait
            await asyncio.sleep(wait)

    async def _deliver(self, key: str, what: str,
                       call: Callable[[], Awaitable[object]]) -> bool:
        for attempt in (1, 2):
            try:
                await call()
                return True
            except TelegramRetryAfter as e:
                self.flood_waits += 1
                log.degraded("Telegram просит подождать", чат=key, что=what,
                             секунд=e.retry_after, попытка=attempt)
                if e.retry_after > MAX_FLOOD_WAIT_S:
                    break
                await asyncio.sleep(float(e.retry_after))
            except TelegramAPIError as e:
                self.lost += 1
                log.degraded("сообщение не доставлено", чат=key, что=what,
                             причина=f"{type(e).__name__} {e}")
                return False
        self.lost += 1
        log.degraded("сообщение потеряно: ожидание дольше предела", чат=key, что=what,
                     предел_с=MAX_FLOOD_WAIT_S)
        return False


class Cooldown:
    """Пауза между запросами одного пользователя. Против спама, а не против людей.

    ⚠ Об отказе сообщается ОДИН раз за окно. Иначе бот усиливает спам: пятьдесят
    сообщений подряд превратились бы в пятьдесят ответов «подождите».
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.refused = 0
        self._last: dict[str, float] = {}
        self._notified: set[str] = set()

    def take(self, key: str) -> float:
        """0 — можно отвечать (отметка обновлена); больше нуля — сколько ещё ждать."""
        now = clock.monotonic_ns() / 1e9
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            self.refused += 1
            return self.seconds - (now - last)
        self._last[key] = now
        self._notified.discard(key)
        return 0.0

    def should_notify(self, key: str) -> bool:
        if key in self._notified:
            return False
        self._notified.add(key)
        return True


# --- служба доставки --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Analysis:
    """Готовый ответ по монете: зоны и НАЗВАННОЕ происхождение карты."""

    symbol: str
    zones: tuple[ZoneSpec, ...]
    origin: str

    stale_min: int = 0
    """Возраст карты в минутах — ОТДЕЛЬНЫМ числом, а не только словами в `origin`.

    Строкой возраст уже был, и первая же публикация показала, чего это стоит: карта
    возрастом 1035 минут ушла в канал, а сказано об этом было последней строкой. Числом
    его можно СРАВНИТЬ с порогом и вынести наверх (`FRESH_MAP_MAX_MIN`).
    """


class Delivery:
    """Всё состояние бота в одном месте: рынки, карта, темп, очередь сборки."""

    def __init__(self, ex: Exchange, uni: Universe, cfg: BotConfig, index: MarketIndex,
                 on_demand: OnDemand, channel: str, bot_name: str) -> None:
        self.ex = ex
        self.uni = uni
        self.cfg = cfg
        self.index = index
        self.on_demand = on_demand
        self.channel = channel
        self.bot_name = bot_name
        self.pacer = Pacer()
        self.cooldown = Cooldown(float(cfg.answer_cooldown_s))
        self.answers = 0
        self.unknown = 0
        self.requests = 0

    # --- карта -------------------------------------------------------------

    async def _will_build(self, symbol: str, *, force: bool = False) -> bool:
        """Понадобится ли долгая сборка. Дешёвая проверка тех же условий, что `analysis`."""
        if force:
            return True
        now = clock.now_ms()
        if self.on_demand.fresh(symbol, now) is not None:
            return False
        if symbol in self.uni.symbols:
            got = await asyncio.to_thread(read_map, symbol)
            if not isinstance(got, NotReady) and got.zones:
                age_min = max(0, (now - got.last_seen_ms) // 60_000)
                if age_min <= FRESH_MAP_MAX_MIN:
                    return False
        return True

    async def analysis(self, symbol: str, *,
                       force: bool = False) -> Analysis | NotReady:
        """Зоны символа плюс строка о том, откуда они. Две ветки, и они разные по смыслу.

        `force` — приказ пересборки («обнови BTC», 2026-08-18): свежесть карты службы
        и кэша сборки не засчитывается, карта считается заново по всем ТФ вселенной.
        """
        now = clock.now_ms()
        in_uni = symbol in self.uni.symbols
        if in_uni and not force:
            got = await asyncio.to_thread(read_map, symbol)
            if not isinstance(got, NotReady) and got.zones:
                age_min = max(0, (now - got.last_seen_ms) // 60_000)
                if age_min <= FRESH_MAP_MAX_MIN:
                    return Analysis(
                        symbol=symbol, zones=got.zones, stale_min=age_min,
                        origin=f"Карта системы, обновлена {_fmt_age(age_min)} назад.")
            # ⚠ РЕШЕНИЕ ВЛАДЕЛЬЦА 2026-08-17: «все карты должны обновляться по запросу
            # и быть актуальными». Устаревшая (или пустая, или нечитаемая) карта
            # вселенной пересобирается тем же путём, что монета вне вселенной, а не
            # отдаётся с извинением. Прежний запрет пересборки держался на конфликте
            # записи `data/bars` со службой; конфликт перечитан по коду:
            # `barstore.append` — слияние + атомарная замена (`os.replace` из
            # `.part-{pid}`), проигравший из двух одновременных писателей теряет
            # только ХВОСТ КЭША, который `missing_tail_since` доберёт следующим
            # чтением. Деградация самолечащаяся и видимая (счётчики добавлено/
            # переписано в логе), а не «потерянные бары навсегда».
        built = await self.on_demand.map_of(symbol, now, force=force)
        if isinstance(built, NotReady):
            return built
        age_min = max(0, (now - built.built_at_ms) // 60_000)
        if in_uni:
            why = ("по приказу «обнови»" if force
                   else "карта службы отстала")
            return Analysis(
                symbol=symbol, zones=built.zones, stale_min=age_min,
                origin=f"Карта пересобрана по запросу {_fmt_age(age_min)} назад:"
                       f" {why}, посчитано заново.")
        return Analysis(
            symbol=symbol, zones=built.zones, stale_min=age_min,
            origin=f"Карта собрана по запросу {_fmt_age(age_min)} назад: за этой монетой"
                   f" система постоянно не следит.")

    # --- ответ -------------------------------------------------------------

    async def _dominance_line(self, symbol: str) -> str:
        """Строка доминации BTC для сводки — ФАКТЫ без торгового вывода.

        Шаг взят у автора (видео 17.08.2026: доминация — отдельный шаг разбора), но
        вывод («держит уровень — довод за шорт») он делает сам, и мы его не имитируем:
        бот отдаёт карту, а не рекомендацию. Отказ транспорта НАЗЫВАЕТСЯ строкой в той
        же сводке (§4.3): пропавшая доминация без слов неотличима от «её не бывает».
        По запросу самого BTCDOM строка не печатается — число уже в заголовке.
        """
        if symbol.split("/")[0] == "BTCDOM":
            return ""
        got = await self.ex.fetch_closed_ohlcv(DOMINANCE_SYMBOL, "1h",
                                               limit=DOMINANCE_WINDOW + 1)
        if isinstance(got, NotReady) or len(got.bars) < DOMINANCE_WINDOW:
            reason = (got.reason if isinstance(got, NotReady)
                      else f"свечей {len(got.bars)} из {DOMINANCE_WINDOW}")
            log.degraded("бот: доминация не получена", символ=symbol, причина=reason)
            return f"⚠ доминация BTC не получена: {reason}"
        closes = [b.close for b in got.bars]
        d4 = (closes[-1] / closes[-5] - 1) * 100
        d24 = (closes[-1] / closes[-DOMINANCE_WINDOW] - 1) * 100
        return (f"📊 Доминация BTC (индекс BTCDOM; рост = BTC сильнее альтов):"
                f" {_fmt_price(closes[-1])} · 4ч {d4:+.2f}% · сутки {d24:+.2f}%")

    async def answer(self, bot: Bot, chat: int | str, symbol: str,
                     reply_to: int | None, *, announce_refusal: bool = True,
                     force: bool = False) -> bool:
        """Три графика и сводка. Отказ на любом шаге НАЗЫВАЕТСЯ, а не молчит.

        ⚠ `announce_refusal=False` НЕ делает отказ молчаливым: он всё равно уходит в лог и
        в счёт неудач публикации. Различие в адресате — отказ в ответ на чей-то вопрос
        нужен спросившему, а тот же отказ в канале с многими читателями это мусор,
        которого никто не просил.
        """
        # ⏳ ПРЕДУПРЕЖДЕНИЕ О ДОЛГОЙ СБОРКЕ — до неё, а не после. Читатель, не
        # получивший ни слова, уходит. Прежние числа (104–345 с, «2–6 минут») ОТОЗВАНЫ
        # 2026-08-18: после ускорения сборки (журнал смены, раздел 20: лестница
        # интрабар-ТФ, ленивый parquet, numpy) живые ответы — 18–90 с (XAU 47.1 с,
        # GPS 89.8 с, ONDO 23.5 с;
        # evidence/bot-restart7-2026-08-18.log). Только при ответе человеку
        # (`announce_refusal`): публикации канала ждать умеют.
        if announce_refusal and await self._will_build(symbol, force=force):
            what = ("обновляю карту по вашему запросу"
                    if force else "карта устарела или ещё не строилась — собираю свежую")
            await self.pacer.send(chat, "предупреждение о сборке", lambda: bot.send_message(
                chat_id=chat,
                text=f"⏳ {symbol.split('/')[0]}: {what}. Обычно это 1–2 минуты.",
                reply_to_message_id=reply_to))
        got = await self.analysis(symbol, force=force)
        if isinstance(got, NotReady):
            log.degraded("бот: ответа по монете нет", символ=symbol, чат=str(chat),
                         причина=got.reason)
            if announce_refusal:
                await self.pacer.send(chat, "отказ", lambda: bot.send_message(
                    chat_id=chat, text=f"НЕ ГОТОВО: {got.reason}",
                    reply_to_message_id=reply_to))
            return False

        missing: list[str] = []
        pps: list[ZoneSpec] = []
        price = 0.0
        """Цена берётся ИЗ ТЕХ ЖЕ БАРОВ, что ушли на график, а не отдельным запросом.

        Иначе сообщение и картинка говорили бы о разных моментах: между двумя запросами
        проходит время, и подпись «цена 63 460» под графиком, нарисованным по другой
        свече, — расхождение, которое никто не заметит. Порядок `CHART_TFS` от старшего к
        младшему, поэтому последнее присвоение — самый свежий бар.
        """
        with tempfile.TemporaryDirectory(prefix="hunter-tg-") as tmp:
            fetched: list[tuple[str, list[Bar]]] = []
            for tf in CHART_TFS:
                bars = await self.ex.fetch_closed_ohlcv(symbol, tf, limit=BARS_ON_CHART)
                if isinstance(bars, NotReady) or not bars.bars:
                    missing.append(tf)
                    # ⚠ Пустой ряд проверяется ОТДЕЛЬНО от `NotReady`: `chart_png` считает
                    # min/max по барам и на пустом списке упал бы `ValueError` внутри
                    # рабочего потока — то есть ответ пропал бы целиком, а причина
                    # осталась бы в стеке, а не в сообщении.
                    reason = bars.reason if isinstance(bars, NotReady) else "ряд пуст"
                    log.degraded("бот: бары для графика не пришли", символ=symbol, тф=tf,
                                 причина=reason)
                    continue
                price = bars.bars[-1].close
                fetched.append((tf, bars.bars))
            # ⚠ Сперва СКАЧАНЫ все ряды, и только потом рисование: подпись каждого
            # графика считает торгуемые уровни, и если считать её по закрытию СВОЕГО ТФ,
            # цена успевает сдвинуться между запросами — живой прогон 2026-08-17
            # напечатал «57 шорт» на графике против «56» в тексте. Одна цена (самый
            # свежий бар, `price`) — одно число везде. Момент `now_ms` (рамка фильтра
            # структур) берётся ОДИН раз по той же причине.
            now_ms = clock.now_ms()
            dominance = await self._dominance_line(symbol)
            for tf, tf_bars in fetched:
                png, tf_pps = await self._chart(got, tf, tf_bars, Path(tmp), price,
                                                now_ms)
                pps += tf_pps
                await self.pacer.send(chat, f"график {tf}", lambda p=png: bot.send_photo(  # type: ignore[misc]
                    chat_id=chat, photo=FSInputFile(p)))
            await self.pacer.send(chat, "сводка", lambda: bot.send_message(
                chat_id=chat,
                text=compose_text(symbol, got.zones, pps, got.origin, tuple(missing),
                                  price=price, stale_min=got.stale_min, now_ms=now_ms,
                                  dominance=dominance),
                reply_to_message_id=reply_to))
        self.answers += 1
        return True

    async def _chart(self, got: Analysis, timeframe: str, bars: list[Bar],
                     tmp: Path, price: float, now_ms: int,
                     ) -> tuple[Path, list[ZoneSpec]]:
        """Рендер в рабочем потоке и ПО ОДНОМУ. Обоснование — у `_RENDER_LOCK`.

        В поток уходит и расчёт ПП, и рисование: оба синхронные, а на цикле событий в это
        время живёт опрос Telegram. Та же причина, по которой расчёт службы не имеет права
        оставаться на цикле (`service`, находка А-1).
        """
        def build() -> tuple[Path, list[ZoneSpec]]:
            tf_pps = pp_zones(bars, timeframe)
            shown = zones_for_chart(got.zones, timeframe, price,
                                    first_ms=bars[0].open_ms, now_ms=now_ms)
            # Подпись — той же функцией, по той же ЦЕНЕ и тому же МОМЕНТУ, что текст
            # сводки (см. докстроку `tradable_counts` и комментарий у сбора `fetched`).
            longs, shorts = tradable_counts(got.zones, price, now_ms)
            png = chart_png(got.symbol, timeframe, bars, shown + tf_pps,
                            tmp / f"{timeframe}.png",
                            # «по карте», потому что на графике лишь ближайшие
                            # (кап `zones_for_chart`): число всей карты при усечённом
                            # рисунке без оговорки — расхождение текста с картинкой.
                            caption=f"торгуемых уровней по карте: {longs} лонг /"
                                    f" {shorts} шорт; на графике — ближайшие",
                            price=price)
            return png, tf_pps

        async with _RENDER_LOCK:
            return await asyncio.to_thread(build)

    # --- сообщения ---------------------------------------------------------

    async def on_message(self, bot: Bot, message: Message) -> None:
        """Единственная точка входа сообщений. Молчание — законный исход (см. `parse_request`).

        ⚠ КАЖДЫЙ разобранный запрос оставляет СЛЕД (`_trace`). До 2026-08-17 следа не было
        вовсе: живой запрос по BTW виден в журнале только потому, что у него была побочная
        сборка на 175 с, а ответ по монете вселенной не оставлял ни строки — доставлено или
        нет, узнать было нельзя. Разбор: `docs/audit/bot-review-2026-08-17.md`.
        """
        # ⚠ Сообщения ботов не обрабатываются вовсе, и это защита от петли: собственная
        # публикация в канале приходит обратно как `channel_post`, а бот, отвечающий на
        # свой ответ, — классическая форма отказа, которую не ловит ни один гейт.
        if message.from_user is not None and message.from_user.is_bot:
            return
        text = message.text or message.caption or ""
        is_private = message.chat.type == "private"
        req = parse_request(text, is_private=is_private, bot_name=self.bot_name)
        if req is None:
            return
        chat = message.chat.id
        started_ns = clock.monotonic_ns()

        # Пауза считается ДО разбора запроса и одинаково для всех его видов: иначе
        # `/help` остаётся бесплатным каналом спама, а он такое же сообщение бота.
        user = message.from_user.id if message.from_user is not None else chat
        key = f"{chat}:{user}"
        left = self.cooldown.take(key)
        if left > 0:
            if self.cooldown.should_notify(key):
                await self.pacer.send(chat, "перегрев", lambda: bot.send_message(
                    chat_id=chat, text=f"Подождите {left:.0f} с — я отвечаю не чаще"
                                       f" раза в {self.cooldown.seconds:.0f} с на человека.",
                    reply_to_message_id=message.message_id))
            self._trace(message, req, "отказ по паузе", started_ns, ждать_с=round(left))
            return

        if req.kind == "help":
            await self.pacer.send(chat, "справка", lambda: bot.send_message(
                chat_id=chat, text=HELP_TEXT))
            self._trace(message, req, "справка", started_ns)
            return
        if req.kind == "pinned":
            await self.pacer.send(chat, "закреплённые", lambda: bot.send_message(
                chat_id=chat, text=self._pinned_text()))
            self._trace(message, req, "список закреплённых", started_ns)
            return
        if req.kind == "signals":
            # «/сигналы btc» — сводка по одной монете; пустой хвост — весь журнал.
            sig_symbol: str | None = None
            if req.query.strip():
                sig_symbol = self.index.resolve(req.query)
                if sig_symbol is None:
                    await self.pacer.send(chat, "не узнаю", lambda: bot.send_message(
                        chat_id=chat,
                        text=f"Не узнаю монету «{req.query.strip()[:32]}». Сводка по"
                             f" всем: /сигналы. Пример монеты: BTC или btcusdt.",
                        reply_to_message_id=message.message_id))
                    self._trace(message, req, "сигналы: тикер не опознан", started_ns)
                    return
            # Чтение леджера синхронное (sqlite) — уводится с цикла событий.
            text = await asyncio.to_thread(signals_summary, sig_symbol)
            await self.pacer.send(chat, "сигналы", lambda: bot.send_message(
                chat_id=chat, text=text, reply_to_message_id=message.message_id))
            self._trace(message, req, "сводка сигналов", started_ns)
            return

        symbol = self.index.resolve(req.query)
        if symbol is None:
            self.unknown += 1
            if req.explicit or is_private:
                await self.pacer.send(chat, "не узнаю", lambda: bot.send_message(
                    chat_id=chat,
                    text=f"Не узнаю монету «{req.query.strip()[:32]}». На площадке"
                         f" {self.uni.venue} таких рынков нет. Пример: BTC или btcusdt.",
                    reply_to_message_id=message.message_id))
            self._trace(message, req, "тикер не опознан", started_ns)
            return
        inst = self.ex.instrument(symbol)
        if isinstance(inst, NotReady):
            await self.pacer.send(chat, "инструмент недоступен", lambda: bot.send_message(
                chat_id=chat, text=f"НЕ ГОТОВО: {inst.reason}",
                reply_to_message_id=message.message_id))
            self._trace(message, req, "инструмент недоступен", started_ns, символ=symbol)
            return
        if (symbol not in self.uni.symbols
                and self.on_demand.fresh(symbol, clock.now_ms()) is None
                and self.on_demand.waiting() < self.cfg.build_queue_max):
            # Ожидание НАЗЫВАЕТСЯ до начала работы: сборка идёт минуты, и молчание в это
            # время неотличимо от поломки. ⚠ Обещание даётся только при СВОБОДНОЙ очереди:
            # иначе за «собираю данные» сразу приходило бы «очередь заполнена», то есть
            # бот обещал бы и тут же отказывал.
            await self.pacer.send(chat, "приняли", lambda: bot.send_message(
                chat_id=chat,
                text=f"{symbol}: карты нет — собираю данные. Это минуты (в очереди"
                     f" {self.on_demand.waiting()}). Пришлю сюда, как посчитаю.",
                reply_to_message_id=message.message_id))
        ok = await self.answer(bot, chat, symbol, message.message_id, force=req.force)
        self._trace(message, req, "ответ доставлен" if ok else "ответа нет",
                    started_ns, символ=symbol, пересборка=req.force)

    def _trace(self, message: Message, req: Request, исход: str, started_ns: int,
               **kw: object) -> None:
        """СЛЕД ЗАПРОСА: одна строка на каждый разобранный запрос, чем бы он ни кончился.

        ⚠ Это не «логирование для полноты». До 2026-08-17 бот не писал о запросах НИЧЕГО:
        единственный живой запрос за первый час (`BTW`) виден в журнале только по побочной
        строке сборки, а был бы он по монете вселенной — не осталось бы и её. Отказ
        отправки при этом выглядел бы точно так же, как успех, то есть §4.3 нарушался
        самой формой наблюдения.

        Текст пользователя обрезается до 32 знаков: он нужен, чтобы понять РАЗБОР
        («почему это стало BTC»), а не чтобы хранить переписку.
        """
        self.requests += 1
        log.info("запрос", исход=исход,
                 чат=message.chat.id, тип_чата=message.chat.type,
                 пользователь=message.from_user.id if message.from_user else 0,
                 текст=req.query.strip()[:32], явный=req.explicit,
                 мс=int((clock.monotonic_ns() - started_ns) / 1e6), **kw)

    def _pinned_text(self) -> str:
        if not self.cfg.pinned:
            return ("Закреплённых монет нет: список пуст в config/bot.toml"
                    if self.cfg.source is not None else
                    "Закреплённых монет нет: файла config/bot.toml нет вовсе.")
        where = self.channel or "НИКУДА — не задан TELEGRAM_CHANNEL_ID"
        return (f"Закреплённые монеты ({len(self.cfg.pinned)}): "
                f"{', '.join(self.cfg.pinned)}\n"
                f"Публикуются в {where} по закрытию бара {self.cfg.publish_timeframe}.")

    # --- публикация закреплённых -------------------------------------------

    async def publish_pinned(self, bot: Bot) -> int:
        """Закреплённые монеты → канал. Возвращает число НЕудач, а не «ок».

        ⚠ Неопознанная запись списка — отказ с именем записи, а не пропуск: `pinned`
        правит человек, и опечатка в тикере обязана быть видна, иначе монета просто
        перестаёт публиковаться и никто этого не замечает.
        """
        if not self.channel:
            log.degraded(f"публикация закреплённых невозможна: пуст {CHANNEL_ENV}",
                         закреплено=len(self.cfg.pinned))
            return 1
        if not self.cfg.pinned:
            log.degraded("публикация закреплённых пуста: список pinned не задан",
                         файл=str(self.cfg.source) if self.cfg.source else "нет файла")
            return 1
        # Объём публикации считается ДО неё и сравнивается с лимитом: 20 сообщений в
        # минуту в один чат — предел Telegram, и очередь `Pacer` его соблюдает растягивая
        # отправку. Значит длинный список закреплённых не теряется, а РАСТЯГИВАЕТСЯ, и
        # владелец обязан знать, на сколько.
        planned = len(self.cfg.pinned) * MESSAGES_PER_ANSWER
        if planned > Pacer.PER_MINUTE:
            log.warn("публикация длиннее минуты: сообщений больше лимита Telegram",
                     сообщений=planned, лимит_в_минуту=Pacer.PER_MINUTE,
                     монет=len(self.cfg.pinned),
                     примерно_минут=round(planned / Pacer.PER_MINUTE, 1))
        sent = failed = 0
        for raw in self.cfg.pinned:
            symbol = self.index.resolve(raw)
            if symbol is None:
                failed += 1
                log.error("закреплённая монета не опознана", запись=raw,
                          площадка=self.uni.venue)
                continue
            if await self.answer(bot, self.channel, symbol, None, announce_refusal=False):
                sent += 1
            else:
                failed += 1
        log.info("публикация закреплённых", канал=self.channel, отправлено=sent,
                 отказов=failed, всего=len(self.cfg.pinned))
        return failed


def seconds_until_publish(timeframe: str, now_ms: int) -> float:
    """Сколько спать до следующей публикации: закрытие бара плюс такт службы.

    Считается ЗАНОВО на каждой итерации и от сведённых часов — так же, как опрос баров
    (`run._sleep_until_next_poll_s`): промах одной публикации не сдвигает следующую.
    """
    step = tf_ms(timeframe)
    next_close_ms = expected_last_closed_open_ms(timeframe, now_ms) + 2 * step
    return max(60.0, (next_close_ms - now_ms) / 1000.0 + PUBLISH_DELAY_S)


class NetworkWatch(logging.Handler):
    """Считает отказы связи, о которых aiogram сообщает СВОИМ логгером, а не нашим.

    ⚠ ЗАЧЕМ ЭТО ЕСТЬ. Живой запуск 2026-08-17: за первый час журнал бота содержал
    **18 строк** `Failed to fetch updates — TelegramNetworkError … Cannot connect to host
    api.telegram.org:443`. Пока идёт такой отказ, бот ГЛУХ: обновления не забираются. Наши
    приборы об этом не знали ничего — строки пишет `logging` самой aiogram, а
    `log.degraded_count()` считает только наши вызовы. Сводка напечатала бы «потеряно 0»
    при восемнадцати обрывах, то есть показывала бы здоровье там, где его нет.

    Обработчик НЕ дублирует текст в наш лог целиком: он считает и называет ПЕРВЫЙ отказ
    подряд, иначе один сетевой провал на сутки залил бы журнал сотнями одинаковых строк.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failures = 0
        self.reported = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if "fetch updates" not in text and "TelegramNetworkError" not in text:
            return
        self.failures += 1
        if not self.reported:
            self.reported = True
            log.degraded("связь с Telegram оборвана — бот не забирает сообщения",
                         причина=text[:120])

    def note_recovery(self) -> None:
        """Связь восстановилась: следующий обрыв снова назовём. Зовётся из сводки."""
        self.reported = False


ALIVE_EVERY_S = 900.0
"""Как часто печатать сводку РАБОТАЮЩЕГО бота. Выведено, а не выбрано.

Прежде счётчики печатались только в `finally` при остановке polling — то есть у бота 24/7
не печатались никогда. Пятнадцать минут — та частота, при которой сутки работы дают 96
строк (читаемо), а молчание дольше четверти часа уже означает, что процесс мёртв, и это
видно по отсутствию строки, а не по её содержанию.
"""


async def alive_loop(delivery: Delivery, watch: NetworkWatch,
                     notifier: Notifier) -> None:
    """Периодическая сводка: бот жив, и вот чем он занимался."""
    while True:
        await asyncio.sleep(ALIVE_EVERY_S)
        log.info("бот жив", запросов=delivery.requests, ответов=delivery.answers,
                 неопознано=delivery.unknown,
                 отказов_по_паузе=delivery.cooldown.refused,
                 сборок=delivery.on_demand.built, сборок_с_отказом=delivery.on_demand.failed,
                 в_очереди_сборки=delivery.on_demand.waiting(),
                 сообщений_потеряно=delivery.pacer.lost,
                 ожиданий_по_лимиту=delivery.pacer.flood_waits,
                 обрывов_связи=watch.failures,
                 уведомлений_сигналы=notifier.sent_signals,
                 уведомлений_исходы=notifier.sent_outcomes,
                 уведомлений_зоны=notifier.sent_zone_events,
                 тактов_наблюдателя_с_отказом=notifier.ticks_failed)
        watch.note_recovery()


async def publish_loop(delivery: Delivery, bot: Bot) -> None:
    """Вечная публикация закреплённых. Сбой одной итерации не убивает следующие.

    ⚠ Сбой при этом НЕ проглатывается: он логируется как отказ. Разница между «упало и
    молчит» и «упало, названо, продолжаем» — та же, ради которой в службе появился
    надзор за задачами (`run._supervise`).
    """
    while True:
        wait = seconds_until_publish(delivery.cfg.publish_timeframe, clock.now_ms())
        log.info("следующая публикация закреплённых", через_мин=round(wait / 60),
                 по_закрытию=delivery.cfg.publish_timeframe, монет=len(delivery.cfg.pinned))
        await asyncio.sleep(wait)
        try:
            await delivery.publish_pinned(bot)
        except (TelegramAPIError, ccxt.BaseError) as e:
            log.error("публикация закреплённых не состоялась",
                      причина=f"{type(e).__name__} {e}")


# --- уведомления: сигнал активирован, исход, цена у зоны ---------------------------
# ПРИКАЗ ВЛАДЕЛЬЦА 2026-08-18: «бот должен присылать уведомления о том что цена близко
# к зоне или сигнал активирован и так далее». Закрывает и находку №3 самоаудита
# 2026-08-18 (журнал исходов есть — уведомлений о них не было).

WATCH_EVERY_S = float(service.CYCLE_SECONDS)
"""Такт наблюдателя = такту службы. Сигналы, исходы и карту пишет служба своим циклом —
чаще леджер проверять не о чем; живая цена берётся тем же тактом ОДНИМ запросом
`fetch_tickers` (вес 40 за все рынки — дешевле, чем свечи по каждому символу)."""

SIDE_WORD = {"long": "лонг", "short": "шорт"}
OUTCOME_WORD = {"stop": "🟥 стоп", "target": "🟩 цель",
                "ambiguous": "⬜ неоднозначно (бар накрыл и стоп, и цель)"}

NOTIFY_LINES_MAX = 30
"""Строк событий в одном сообщении. Предел Telegram — 4096 знаков (`TEXT_LIMIT`);
строка события ≤ ~120 знаков, тридцать строк — с запасом. Хвост НАЗЫВАЕТСЯ числом."""


@dataclass(frozen=True, slots=True)
class LevelRow:
    """Активный уровень из карты леджера — то, с чем сравнивается живая цена."""

    symbol: str
    timeframe: str
    side: str
    price: float
    zone_lo: float
    zone_hi: float
    from_ms: int
    to_ms: int


@dataclass(frozen=True, slots=True)
class LedgerNews:
    """Что появилось в леджере со прошлого такта, плюс новые водяные знаки."""

    signals: tuple[tuple[str, str, str, float, float, float | None], ...]
    """(symbol, timeframe, direction, entry, stop, target)"""

    outcomes: tuple[tuple[str, str, str, str, float | None], ...]
    """(symbol, timeframe, direction, kind, r)"""

    states: tuple[tuple[int, str, str, str, str, float], ...]
    """(signal_id, symbol, timeframe, direction, state, entry) — состояния ВСЕХ
    незакрытых сигналов. Наблюдатель ловит переход not_filled→open: «вход
    состоялся» — событие, о котором просил владелец (2026-08-18)."""

    levels: tuple[LevelRow, ...]
    max_signal_id: int
    max_closed_ms: int


def _read_ledger_news(after_id: int | None, after_ms: int | None,
                      symbols: tuple[str, ...]) -> LedgerNews | NotReady:
    """Один заход в леджер (только чтение) за всё, что нужно такту наблюдателя.

    `after_id is None` — первый такт: водяные знаки ВЗВОДЯТСЯ по текущему максимуму,
    события не читаются. Иначе перезапуск бота вылил бы в канал всю историю журнала,
    и сегодняшние события утонули бы в ней.
    """
    try:
        conn = store.open_readonly()
    except FileNotFoundError as e:
        return NotReady(reason=str(e))
    try:
        max_id = int(conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM signals").fetchone()[0])
        max_closed = int(conn.execute(
            "SELECT COALESCE(MAX(closed_at), 0) FROM outcomes").fetchone()[0])
        sig_rows: list[tuple[str, str, str, float, float, float | None]] = []
        out_rows: list[tuple[str, str, str, str, float | None]] = []
        if after_id is not None:
            sig_rows = [
                (r[0], r[1], r[2], float(r[3]), float(r[4]),
                 None if r[5] is None else float(r[5]))
                for r in conn.execute(
                    "SELECT symbol, timeframe, direction, entry, stop, target"
                    " FROM signals WHERE id > ? ORDER BY id", (after_id,))]
        if after_ms is not None:
            out_rows = [
                (r[0], r[1], r[2], r[3], None if r[4] is None else float(r[4]))
                for r in conn.execute(
                    "SELECT s.symbol, s.timeframe, s.direction, o.kind, o.r"
                    " FROM outcomes o JOIN signals s ON s.id = o.signal_id"
                    " WHERE o.closed_at > ? ORDER BY o.closed_at", (after_ms,))]
        state_rows = [
            (int(r[0]), r[1], r[2], r[3], r[4], float(r[5]))
            for r in conn.execute(
                "SELECT st.signal_id, s.symbol, s.timeframe, s.direction, st.state,"
                " s.entry FROM signal_states st JOIN signals s ON s.id = st.signal_id"
                " ORDER BY st.signal_id")]
        marks = ",".join("?" * len(symbols))
        lvl_rows = conn.execute(
            f"SELECT symbol, timeframe, side, price, zone_lo, zone_hi, from_ms, to_ms"
            f" FROM levels WHERE state='active' AND symbol IN ({marks})",
            symbols).fetchall()
    except sqlite3.DatabaseError as e:
        return NotReady(reason=f"леджер не прочитан: {type(e).__name__} {e}")
    finally:
        conn.close()
    levels = tuple(LevelRow(symbol=r[0], timeframe=r[1], side=r[2], price=float(r[3]),
                            zone_lo=float(r[4]), zone_hi=float(r[5]),
                            from_ms=int(r[6]), to_ms=int(r[7])) for r in lvl_rows)
    return LedgerNews(signals=tuple(sig_rows), outcomes=tuple(out_rows),
                      states=tuple(state_rows), levels=levels,
                      max_signal_id=max_id, max_closed_ms=max_closed)


def zone_position(price: float, lo: float, hi: float) -> str:
    """Где цена относительно зоны: `inside` | `near` | `far`.

    «Близко» — ближе ОДНОЙ ШИРИНЫ зоны от её края. Порог не выдуман числом в
    процентах: курс строит уровень ЗОНОЙ (стр. 23–26), и ширина зоны — единственная
    мера того, сколько это близко, которая лежит в самой карте. Узкая зона зовёт цену с
    малого расстояния, широкая — с большого: масштаб задаёт сама структура, а не
    константа. Зона нулевой ширины (вырожденный профиль) отвечает `far` всюду, кроме
    точного равенства, — и это честно: у неё нет собственной меры расстояния.
    """
    if lo <= price <= hi:
        return "inside"
    width = hi - lo
    if width > 0 and min(abs(price - lo), abs(price - hi)) <= width:
        return "near"
    return "far"


SIGNALS_SHOW_MAX = 12
"""Строк на раздел сводки сигналов. Кап читаемости: предел сообщения 4096 знаков
(`TEXT_LIMIT`) страхует `_fit`, но сто строк «ждут входа» не прочтёт никто; хвост
НАЗЫВАЕТСЯ числом (§4.3)."""


def signals_summary(symbol: str | None = None) -> str:
    """Сводка журнала сигналов ДЛЯ ЧАТА: в позиции, ждут входа, итог закрытых.

    Закрывает дыру №4 самоаудита 2026-08-18 (журнал есть — спросить «что сейчас
    живо» нечем). Читается из леджера, только чтение (§10.2). Каждое число называет
    свою ПОДВЫБОРКУ (урок `outcome-survey-2026-08-10`): «R по закрытым» — не «R
    системы», открытые и не наполненные исхода ещё не имеют; сигнал без записи
    состояния называется отдельно, а не растворяется в «ждут входа».

    `symbol` — полный символ рынка («BTC/USDT:USDT»): сводка по одной монете,
    включая итог закрытых по ней же (подвыборка называется в заголовке).
    """
    try:
        conn = store.open_readonly()
    except FileNotFoundError as e:
        return f"НЕ ГОТОВО: {e} — создать: uv run python -m hunter ledger --init"
    sym_where = "" if symbol is None else " AND s.symbol = ?"
    sym_args: tuple[str, ...] = () if symbol is None else (symbol,)
    try:
        open_rows = conn.execute(
            "SELECT s.symbol, s.timeframe, s.direction, s.entry, s.stop, s.target,"
            " st.state, s.recorded_at"
            " FROM signals s"
            " LEFT JOIN outcomes o ON o.signal_id = s.id"
            " LEFT JOIN signal_states st ON st.signal_id = s.id"
            " WHERE o.signal_id IS NULL" + sym_where +
            " ORDER BY s.recorded_at DESC", sym_args).fetchall()
        n_total = int(conn.execute(
            "SELECT COUNT(*) FROM signals s WHERE 1=1" + sym_where,
            sym_args).fetchone()[0])
        closed = conn.execute(
            "SELECT COUNT(*),"
            " SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN o.kind='stop' THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN o.kind='ambiguous' THEN 1 ELSE 0 END),"
            " SUM(o.r) FROM outcomes o JOIN signals s ON s.id = o.signal_id"
            " WHERE 1=1" + sym_where, sym_args).fetchone()
    except sqlite3.DatabaseError as e:
        return f"НЕ ГОТОВО: леджер не прочитан ({type(e).__name__} {e})"
    finally:
        conn.close()
    if symbol is not None and n_total == 0:
        # Пустая подвыборка называется прямо, а не сводкой из одних нулей.
        return (f"По {symbol.split('/')[0]} в журнале сигналов пока ничего нет."
                f" Сводка по всем: /сигналы")
    now = clock.now_ms()

    def line(r: tuple[str, str, str, float, float, float | None, str | None,
                      int]) -> str:
        sym, tf, d, entry, stop, target, _state, rec = r
        age = _fmt_age(max(0, (now - int(rec)) // 60_000))
        tg = f", цель {_fmt_price(float(target))}" if target is not None else ""
        return (f"• {sym.split('/')[0]} {SIDE_WORD.get(d, d)} "
                f"{TF_LABEL.get(tf, tf)}: вход {_fmt_price(float(entry))}, "
                f"стоп {_fmt_price(float(stop))}{tg} · {age} назад")

    def section(
        title: str,
        rows: list[tuple[str, str, str, float, float, float | None, str | None,
                         int]],
    ) -> list[str]:
        out = [f"{title}: {len(rows)}"]
        out += [line(r) for r in rows[:SIGNALS_SHOW_MAX]]
        if len(rows) > SIGNALS_SHOW_MAX:
            out.append(f"… и ещё {len(rows) - SIGNALS_SHOW_MAX}")
        return [*out, ""]

    in_pos = [r for r in open_rows if r[6] == "open"]
    waiting = [r for r in open_rows if r[6] == "not_filled"]
    stateless = [r for r in open_rows if r[6] is None]
    head = ("📒 Сигналы системы" if symbol is None
            else f"📒 Сигналы системы — {symbol.split('/')[0]}")
    lines = [head, ""]
    lines += section("В позиции (вход состоялся)", in_pos)
    lines += section("Ждут входа (цена не дошла)", waiting)
    if stateless:
        lines += section("Без записи состояния (прогон их ещё не пересчитывал)",
                         stateless)
    n_closed = int(closed[0] or 0)
    n_amb = int(closed[3] or 0)
    r_sum = float(closed[4] or 0.0)
    lines.append(
        f"Закрытых: {n_closed} из {n_total} в журнале"
        f" (🟩 цель {int(closed[1] or 0)} · 🟥 стоп {int(closed[2] or 0)}"
        f" · ⬜ неоднозначно {n_amb})"
        # У неоднозначных R по схеме отсутствует — подвыборка называется явно.
        f" · суммарный R по {n_closed - n_amb} со стопом/целью: {r_sum:+.2f}")
    tail = ["Сводка журнала, а не торговая рекомендация."]
    return _fit(lines + tail, len(tail))


class Notifier:
    """События для канала: новые сигналы, исходы и переходы цены у активных зон.

    Всё берётся из ЛЕДЖЕРА (только чтение) и живых тикеров; наблюдатель ничего не
    считает и не пишет — расчёт живёт в службе (§10.2). Уведомление — это ПЕРЕХОД,
    а не состояние: «цена внутри зоны» шлётся один раз при входе, а не каждые пять
    минут, пока она там стоит. Перевзвод — уход в `far`.

    Первый такт взводит водяные знаки и состояния зон МОЛЧА (иначе перезапуск бота
    выливал бы в канал историю), уровень, впервые появившийся в карте, — тоже:
    его рождение уже видно в публикации закреплённых, событие здесь — движение цены.
    """

    def __init__(self) -> None:
        self._last_signal_id: int | None = None
        self._last_closed_ms: int | None = None
        self._zone_state: dict[tuple[str, str, str, int, int], str] = {}
        self._signal_state: dict[int, str] = {}
        """Последнее виденное состояние незакрытых сигналов — для перехода
        not_filled→open («вход состоялся», приказ владельца 2026-08-18)."""
        self.sent_signals = 0
        self.sent_outcomes = 0
        self.sent_entries = 0
        self.sent_zone_events = 0
        self.ticks_failed = 0

    def _cap(self, lines: list[str]) -> list[str]:
        if len(lines) <= NOTIFY_LINES_MAX:
            return lines
        rest = len(lines) - NOTIFY_LINES_MAX
        return [*lines[:NOTIFY_LINES_MAX], f"… и ещё {rest} — не поместились"]

    async def tick(self, ex: Exchange, uni: Universe) -> list[str]:
        """Один такт: прочитать, сравнить, вернуть готовые сообщения (может быть пусто)."""
        news = await asyncio.to_thread(
            _read_ledger_news, self._last_signal_id, self._last_closed_ms, uni.symbols)
        if isinstance(news, NotReady):
            self.ticks_failed += 1
            log.degraded("наблюдатель: леджер не прочитан", причина=news.reason)
            return []
        priming = self._last_signal_id is None
        prev_max_id = self._last_signal_id or 0
        self._last_signal_id = news.max_signal_id
        self._last_closed_ms = news.max_closed_ms

        out: list[str] = []
        if news.signals:
            lines = [f"• {s.split('/')[0]} {SIDE_WORD.get(d, d)} "
                     f"{TF_LABEL.get(tf, tf)}: вход {_fmt_price(e)}, "
                     f"стоп {_fmt_price(st)}"
                     + (f", цель {_fmt_price(tg)}" if tg is not None else "")
                     for s, tf, d, e, st, tg in news.signals]
            self.sent_signals += len(news.signals)
            out.append("\n".join(["⚡ Новые сигналы:", *self._cap(lines)]))
        if news.outcomes:
            lines = [f"• {s.split('/')[0]} {SIDE_WORD.get(d, d)} "
                     f"{TF_LABEL.get(tf, tf)}: {OUTCOME_WORD.get(k, k)}"
                     + (f" · R {r:+.2f}" if r is not None else "")
                     for s, tf, d, k, r in news.outcomes]
            self.sent_outcomes += len(news.outcomes)
            out.append("\n".join(["📕 Исходы сигналов:", *self._cap(lines)]))

        # «Вход состоялся» — переход not_filled→open (приказ владельца 2026-08-18).
        # Событие, а не состояние: шлётся один раз, на такте, где переход увиден.
        # Первый такт взводит память молча — как и остальные водяные знаки: иначе
        # перезапуск бота объявил бы «входами» все давно открытые позиции.
        entry_lines: list[str] = []
        alive: set[int] = set()
        for sid, sym, tf, d, state, entry in news.states:
            alive.add(sid)
            was = self._signal_state.get(sid)
            self._signal_state[sid] = state
            if priming or state != "open":
                continue
            # Свежий сигнал, чьё ПЕРВОЕ виденное состояние уже open, — вход состоялся
            # в первый же такт; переход был, просто между двумя чтениями.
            fresh_filled = was is None and sid > prev_max_id
            if was == "not_filled" or fresh_filled:
                entry_lines.append(
                    f"• {sym.split('/')[0]} {SIDE_WORD.get(d, d)} "
                    f"{TF_LABEL.get(tf, tf)}: вход {_fmt_price(entry)} исполнен — "
                    f"сигнал активирован")
        # Сигналы, ставшие исходами, из таблицы состояний удалены — чистим память.
        for sid in [s for s in self._signal_state if s not in alive]:
            del self._signal_state[sid]
        if entry_lines:
            self.sent_entries += len(entry_lines)
            out.append("\n".join(["✅ Входы состоялись:", *self._cap(entry_lines)]))

        tickers = await ex.fetch_tickers(uni.symbols)
        if isinstance(tickers, NotReady):
            log.degraded("наблюдатель: тикеры не получены", причина=tickers.reason)
            self.ticks_failed += 1
            return out
        now_ms = clock.now_ms()
        rank = {"far": 0, "near": 1, "inside": 2}
        zone_lines: list[str] = []
        seen: set[tuple[str, str, str, int, int]] = set()
        for lv in news.levels:
            t = tickers.get(lv.symbol)
            if t is None:
                continue
            key = (lv.symbol, lv.timeframe, lv.side, lv.from_ms, lv.to_ms)
            seen.add(key)
            pos = zone_position(t.last, lv.zone_lo, lv.zone_hi)
            was = self._zone_state.get(key)
            self._zone_state[key] = pos
            # Событие — только СБЛИЖЕНИЕ (far→near, near→inside, far→inside).
            # Откат inside→near — не событие: «цена подходит к зоне», напечатанное
            # при выходе ИЗ неё, врало бы направлением (поймано зондом 2026-08-18).
            if priming or was is None or rank[pos] <= rank[was]:
                continue
            # Тот же фильтр «уровень у структуры», что в ответах бота
            # (`near_structure`): автор размечает уровни только у структур в кадре,
            # и звать ценой к зоне, которую сам бот в ответе прячет, — расхождение
            # каналов доставки. Без него первый же зонд дал россыпь событий по
            # давно отработанным 5м-структурам.
            step = TIMEFRAME_MS.get(lv.timeframe)
            if lv.to_ms > 0 and step is not None and (
                    lv.to_ms < now_ms - BARS_ON_CHART * step):
                continue
            base = lv.symbol.split("/")[0]
            side = SIDE_WORD.get(lv.side, lv.side)
            what = (f"вошла в {side}-зону" if pos == "inside"
                    else f"подходит к {side}-зоне")
            zone_lines.append(
                f"• {base}: цена {_fmt_price(t.last)} {what} "
                f"{_fmt_price(lv.zone_lo)}–{_fmt_price(lv.zone_hi)} "
                f"({TF_LABEL.get(lv.timeframe, lv.timeframe)}, "
                f"ПОК {_fmt_price(lv.price)})")
        # Снятые с карты уровни выбывают из памяти состояний — иначе она росла бы вечно.
        for key in list(self._zone_state):
            if key not in seen:
                del self._zone_state[key]
        if zone_lines:
            self.sent_zone_events += len(zone_lines)
            out.append("\n".join(["📍 Цена у зон:", *self._cap(zone_lines)]))
        return out


async def notify_loop(delivery: Delivery, bot: Bot, notifier: Notifier) -> None:
    """Вечный наблюдатель. Сбой такта НАЗЫВАЕТСЯ и не убивает следующие."""
    if not delivery.channel:
        log.degraded(f"уведомления слать некуда: пуст {CHANNEL_ENV} — наблюдатель"
                     f" не запущен")
        return
    while True:
        try:
            for text in await notifier.tick(delivery.ex, delivery.uni):
                await delivery.pacer.send(
                    delivery.channel, "уведомление",
                    lambda t=text: bot.send_message(  # type: ignore[misc]
                        chat_id=delivery.channel, text=t))
        except (ccxt.BaseError, CapabilityMissing) as e:
            notifier.ticks_failed += 1
            log.error("наблюдатель: такт не удался", причина=f"{type(e).__name__} {e}")
        await asyncio.sleep(WATCH_EVERY_S)


async def main(*, horizon_days: int, publish_now: bool = False,
               universe: Path = DEFAULT_PATH) -> int:
    """Запуск бота. `publish_now` — разовая публикация и выход: проверка канала одной
    командой, без ожидания закрытия бара (§7.5)."""
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        print(f"ОТКАЗ: не задан {TOKEN_ENV} — боту не с чем подключаться (§4.3)")
        return 2
    channel = os.environ.get(CHANNEL_ENV, "").strip()

    uni = load_universe(universe)
    cfg = load_bot_config()
    if cfg.source is None:
        log.degraded("настроек доставки нет — взяты умолчания", ожидался="config/bot.toml")
    if not channel:
        log.degraded(f"{CHANNEL_ENV} не задан — закреплённые монеты публиковать НЕКУДА",
                     закреплено=len(cfg.pinned))

    ex = Exchange(uni.venue)
    await ex.open()
    bot = Bot(token=token)
    try:
        index = MarketIndex.build(ex)
        me = await bot.get_me()
        delivery = Delivery(ex=ex, uni=uni, cfg=cfg, index=index,
                            on_demand=OnDemand(uni, cfg, horizon_days),
                            channel=channel, bot_name=me.username or "")
        log.info("бот доставки", имя=me.username, площадка=uni.venue,
                 рынков=index.total, вселенная=len(uni.symbols),
                 закреплено=len(cfg.pinned), канал=channel or "НЕ ЗАДАН",
                 графики="/".join(CHART_TFS))
        if publish_now:
            return 1 if await delivery.publish_pinned(bot) else 0

        dp = Dispatcher()

        @dp.message()
        async def _on_message(message: Message) -> None:
            await delivery.on_message(bot, message)

        @dp.channel_post()
        async def _on_post(message: Message) -> None:
            await delivery.on_message(bot, message)

        # Наблюдатель за связью ставится на логгер aiogram ДО опроса: обрывы начинаются
        # с первого же запроса к api.telegram.org, и поставленный позже он их пропустит.
        watch = NetworkWatch()
        logging.getLogger("aiogram").addHandler(watch)
        logging.getLogger("aiogram.dispatcher").addHandler(watch)

        notifier = Notifier()
        publisher = asyncio.create_task(publish_loop(delivery, bot),
                                        name="публикация закреплённых")
        watcher = asyncio.create_task(notify_loop(delivery, bot, notifier),
                                      name="наблюдатель зон и сигналов")
        alive = asyncio.create_task(alive_loop(delivery, watch, notifier),
                                    name="сводка бота")
        try:
            await dp.start_polling(bot, handle_as_tasks=True,
                                   tasks_concurrency_limit=HANDLERS_MAX)
        finally:
            publisher.cancel()
            watcher.cancel()
            alive.cancel()
            log.info("бот остановлен", запросов=delivery.requests,
                     ответов=delivery.answers,
                     неопознанных=delivery.unknown,
                     отказов_по_паузе=delivery.cooldown.refused,
                     сборок=delivery.on_demand.built,
                     сборок_с_отказом=delivery.on_demand.failed,
                     сообщений_потеряно=delivery.pacer.lost,
                     ожиданий_по_лимиту=delivery.pacer.flood_waits,
                     обрывов_связи=watch.failures,
                     отложено_с=round(delivery.pacer.delayed_s, 1))
    finally:
        await bot.session.close()
        await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(horizon_days=180)))
