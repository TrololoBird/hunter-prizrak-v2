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

   ⚠ Символ вселенной по запросу НЕ пересобирается никогда, и это не экономия. Служба
   (`hunter serve`) пишет в те же файлы `data/bars`; две записи из двух процессов по
   одному ряду — потерянные бары, отказ без единого исключения.

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

HELP_TEXT = (
    "Пришлите тикер монеты — отвечу картой уровней: три графика (4ч, 1ч, 15м) и сводка.\n"
    "Формат любой: BTC, btc, btcusdt, BTC/USDT:USDT.\n"
    "В группе можно так же или командой: /coin BTC.\n"
    "/pinned — какие монеты публикуются в канал сами.\n"
    "Уровни — карта системы по методу PrizrakTrade, а не торговая рекомендация."
)


@dataclass(frozen=True, slots=True)
class Request:
    """Что попросили. `explicit` — обратились к боту прямо (команда или личка)."""

    kind: str
    """ticker | help | pinned"""

    query: str
    explicit: bool


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
        if low in TICKER_COMMANDS:
            return Request(kind="ticker", query=rest.strip(), explicit=True)
        # `/btc` — тоже запрос тикера: команду с таким именем никто не заводил.
        return Request(kind="ticker", query=name[1:], explicit=True)
    if is_private:
        return Request(kind="ticker", query=body, explicit=True)
    if len(body.split()) <= TICKER_MAX_TOKENS:
        return Request(kind="ticker", query=body, explicit=False)
    return None


# --- карта уровней ----------------------------------------------------------------

ENTRY_MARK = {
    "limit": "лимитки",
    "confirmation": "по факту",
    "retest_flipped": "ретест",
    "": "",
}
"""Роль зоны СЛОВАМИ ЧИТАТЕЛЯ, а не ссылкой на страницу курса и не версией схемы БД.

⚠ ПЕРЕПИСАНО 2026-08-17 по разбору `docs/audit/bot-review-2026-08-17.md`. Прежние подписи
уходили в публичный канал в таком виде: «⚠ правило входа не записано (строка карты до
схемы 6)» — версия схемы SQLite, напечатанная ТРИДЦАТЬ РАЗ в одном сообщении. Пометка
нужна, но владельцу в логе, а читателю нужна роль зоны.

Слова взяты у автора: пост 2026-08-03 называет зоны «лимитки» и «работать по факту»
(`research/author_markup/2026-08-03_overcarder.md`), а не «entry_rule=confirmation».
Пустая строка у неизвестного правила намеренна: молчание честнее выдумки, а СЧЁТ таких
строк уходит в лог (`log.degraded`), где его видит владелец.
"""


def _fmt_price(x: float) -> str:
    """Цена так, как её читает человек: 63 460, 3.691, 0.00385.

    Знаков после запятой — по величине самой цены, а не константой: у BTC дробная часть
    шум, у ANKR она и есть вся цена. Разряды разделяются узким пробелом — в Telegram он
    не переносит строку.
    """
    if x >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1:
        return f"{x:.4g}"
    return f"{x:.6f}".rstrip("0")


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
    try:
        rows = conn.execute(
            # ⚠ Берутся НЕ ТОЛЬКО активные. Снятые уровни нужны разметке: отработанный
            # получает красную стрелку (стр. 25), пробитый — крестик отмены (стр. 43).
            # Без них график молчит о том, что с уровнем случилось, — а это половина
            # разметки автора. Отбор по `retired_at` держит выборку свежей: снятое
            # месяц назад на график сегодняшних баров всё равно не попадёт.
            "SELECT timeframe, side, price, zone_lo, zone_hi, entry_rule, last_seen,"
            " boundary_lo, boundary_hi, from_ms, to_ms, state,"
            " COALESCE(resolved_at, 0)"
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
                           state=r[11], retired_at_ms=int(r[12]))
                  for r in rows)
    return MapRead(zones=zones, last_seen_ms=max((int(r[6]) for r in rows), default=0))


def zones_of(decision: engine.SymbolDecision) -> tuple[ZoneSpec, ...]:
    """Активные уровни РЕШЕНИЯ → те же полосы, что кладутся в карту леджера.

    Поля берутся ровно те же и в том же виде, что пишет `store.sync_levels`: цена ПОК,
    границы зоны, состояние, правило входа. Два пути к одной картинке — один набор полей;
    иначе ответ по монете вселенной и по монете вне её различался бы неизвестно чем.
    """
    return tuple(sorted(
        (ZoneSpec(side=m.level.side.value, timeframe=m.level.timeframe,
                  price=float(m.level.price), zone_lo=float(m.level.zone_lo),
                  zone_hi=float(m.level.zone_hi), entry_rule=m.status.entry_rule.value,
                  boundary_lo=float(m.level.boundary_lo),
                  boundary_hi=float(m.level.boundary_hi),
                  from_ms=m.level.structure_from_ms, to_ms=m.level.structure_to_ms)
         for m in decision.mapped if m.status.state is LevelState.ACTIVE),
        key=lambda z: z.price))


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
    * **очередь ограничена** (`BotConfig.build_queue_max`): сборка стоит минут, и обещать
      ответ большему числу монет значит обещать то, чего никто не дождётся;
    * **результат живёт в памяти и не идёт в леджер.** Журнал (§10.2) — свидетельство о
      том, что система решала САМА и постоянно; сложить туда монету, спрошенную однажды
      в чате, значит смешать две разные выборки в одной статистике. Прецедент этого класса
      уже был: `docs/audit/outcome-survey-2026-08-10.md`.

    ⚠ ЦЕНА ЗАМЕРЕНА, И ОНА НЕ ТАМ, ГДЕ КАЖЕТСЯ (LTC/USDT:USDT, горизонт 180, 2026-08-17):
    холодная сборка 345 с, она же на прогретом кэше 289 с при шести добранных свечах
    вместо 266 100. То есть **закачка это порядка 16% времени, остальное — счёт**, и
    кэш её не удешевляет. Отсюда `map_ttl_minutes` экономит процессорные минуты, а не
    трафик, а очередь в восемь монет остаётся сорока минутами при любом кэше.

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

    async def map_of(self, symbol: str, now_ms: int) -> BuiltMap | NotReady:
        """Свежая карта символа: из памяти, из идущей сборки или новой сборкой."""
        got = self.fresh(symbol, now_ms)
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
            report, sources = await run.collect(one, 0, 0, self.horizon_days)
        except (ccxt.BaseError, CapabilityMissing) as e:
            self.failed += 1
            log.degraded("сборка по запросу не удалась", символ=symbol,
                         причина=f"{type(e).__name__} {e}")
            return NotReady(reason=f"{symbol}: данные не собраны ({type(e).__name__})")
        decided = await asyncio.to_thread(run.decide_once, report, one, sources)
        got = decided.get(symbol)
        if got is None:
            self.failed += 1
            log.degraded("сборка по запросу: рядов нет", символ=symbol)
            return NotReady(reason=f"{symbol}: рядов не собрано — карта не строится")
        spent = (clock.monotonic_ns() - started) / 1e9
        built = BuiltMap(zones=zones_of(got), built_at_ms=clock.now_ms(), seconds=spent)
        self._done[symbol] = built
        self.built += 1
        log.info("сборка по запросу завершена", символ=symbol, секунд=round(spent, 1),
                 уровней=len(built.zones))
        return built


# --- графики и текст --------------------------------------------------------------


def zones_for_chart(
    zones: tuple[ZoneSpec, ...], timeframe: str, last_price: float,
    near_local: int = 4,
) -> list[ZoneSpec]:
    """Отбор зон для графика ТФ X: как в обзорах автора — не вся карта разом.

    Правило: зоны СВОЕГО ТФ и старших — всегда; из них «локальные» (младше 4ч) —
    только ближайшие к цене (`near_local` сверху и снизу): скрин автора держит
    единицы зон, и полная карта на одном ТФ превращается в жёлтую заливку (проверено
    первым же рендером — 57 зон закрыли весь график).
    """
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    rank = order.get(timeframe, 0)
    fit = [z for z in zones if order.get(z.timeframe, 0) >= rank]
    senior = [z for z in fit if z.timeframe in SENIOR_TFS]
    local = [z for z in fit if z.timeframe not in SENIOR_TFS]
    above = sorted((z for z in local if z.price >= last_price), key=lambda z: z.price)
    below = sorted((z for z in local if z.price < last_price), key=lambda z: -z.price)
    return senior + above[:near_local] + below[:near_local]


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


def _fmt_zone(z: ZoneSpec, price: float) -> str:
    """Одна строка зоны глазами читателя: цена, ТФ, расстояние, границы, роль.

    ⚠ Расстояние до цены — главное, чего в прежней редакции не было вовсе. Уровень в
    процентах от текущей цены отвечает на вопрос «мне это сейчас важно?», а номер
    таймфрейма — нет.
    """
    band = "🟢" if z.side == "long" else "🔴"
    senior = " ★" if z.timeframe in SENIOR_TFS else ""
    away = (z.price - price) / price * 100 if price > 0 else 0.0
    parts = [f"{band} {_fmt_price(z.price)}", f"{TF_LABEL.get(z.timeframe, z.timeframe)}{senior}",
             f"{away:+.1f}%"]
    if abs(z.zone_hi - z.zone_lo) / max(z.price, 1e-12) >= 0.0005:
        parts.append(f"зона {_fmt_price(z.zone_lo)}–{_fmt_price(z.zone_hi)}")
    role = ENTRY_MARK.get(z.entry_rule, "")
    if role:
        parts.append(role)
    return " · ".join(parts)


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


def compose_text(symbol: str, zones: tuple[ZoneSpec, ...], pps: list[ZoneSpec],
                 origin: str, charts_missing: tuple[str, ...] = (), *,
                 price: float = 0.0, stale_min: int = 0) -> str:
    """Сводка ДЛЯ ЧИТАТЕЛЯ КАНАЛА. Устройство взято у автора, а не придумано.

    ⚠ ПЕРЕПИСАНО 2026-08-17 по разбору `docs/audit/bot-review-2026-08-17.md`. Прежняя
    редакция печатала все активные уровни подряд: замер по BTC — 69 уровней, 44 строки,
    3282 знака, из них «правило входа не записано (строка карты до схемы 6)» — тридцать
    раз. Ни текущей цены, ни расстояния до уровня в сообщении не было вовсе.

    Что взято из поста автора (`research/author_markup/2026-08-03_overcarder.md`):
      * ЦЕНА названа первой строкой — у автора она есть в каждом посте;
      * зон немного и у каждой РОЛЬ словами («лимитки», «работать по факту»);
      * порядок — от цены наружу, а не по таймфрейму: читателю важно, что близко.

    `price` — последняя цена закрытого бара младшего графика. Ноль означает «бары не
    пришли»: тогда расстояния не печатаются, а не подставляются нулём (§4.3).

    `stale_min` — возраст карты в минутах. Старше `FRESH_MAP_MAX_MIN` он выносится ПЕРВОЙ
    строкой: карта суточной давности выглядит как сегодняшняя, и молчать об этом нельзя.
    """
    base = symbol.split("/")[0]
    # ⚠ ТЕКСТ считает только ЖИВЫЕ уровни. С 2026-08-17 `read_map` отдаёт и снятые —
    # они нужны графику для стрелок отработки и крестиков отмены, — но в сводке
    # «активных уровней N» снятые сделали бы это число ложью.
    live = [z for z in zones if z.state == "active"]

    # СОРТИРОВКА ПО РАССТОЯНИЮ ДО ЦЕНЫ, а не по таймфрейму. Прежний порядок (ТФ, потом
    # цена) ставил первым уровень в 5% от цены, а ближайший — пятой строкой.
    def away(z: ZoneSpec) -> float:
        return abs(z.price - price) / price if price > 0 else 0.0

    # ⚠ ДЕДУБЛИКАЦИЯ ПО ЦЕНЕ. Один и тот же уровень строится на нескольких ТФ, и первая
    # редакция этого формата напечатала «63 800 · 15м» и «63 800 · 5м» подряд, а «62 600»
    # дважды с почти совпадающими зонами. Для читателя это ОДИН уровень; остаётся строка
    # старшего ТФ — курс на стр. 48 говорит, что старший сильнее.
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    best: dict[tuple[str, str], ZoneSpec] = {}
    for z in live:
        key = (z.side, _fmt_price(z.price))
        kept = best.get(key)
        if kept is None or order.get(z.timeframe, 0) > order.get(kept.timeframe, 0):
            best[key] = z
    unique = sorted(best.values(), key=away)
    hidden_dupes = len(live) - len(unique)

    head = f"👑 {base} · {_fmt_price(price)}" if price > 0 else f"👑 {base}"
    lines = [head, ""]
    if stale_min > FRESH_MAP_MAX_MIN:
        # Устаревшая карта — ПЕРВОЙ строкой. Внизу мелким шрифтом её не читают, а
        # уровни суточной давности выглядят как сегодняшние.
        lines.append(f"⚠ карта не обновлялась {_fmt_age(stale_min)} — уровни могли уже"
                     f" отработать")
        lines.append("")

    def tradable_first(z: ZoneSpec) -> tuple[int, float]:
        """Сначала зоны с ТОРГУЕМОЙ стороны цены, потом пройденные. Не новое правило.

        Лимитка на покупку ставится НИЖЕ цены, на продажу — ВЫШЕ (стр. 30: «вход от
        уровня»). Значит лонг-зона над ценой и шорт-зона под ней — это то, что цена уже
        прошла; показывать их первыми значит предлагать читателю неисполнимое.

        ⚠ Замер 2026-08-17, BTC, карта возрастом 17 часов: из 69 активных зон **12 стоят
        по неторгуемую сторону** (10 лонгов над ценой, 2 шорта под), и первая редакция
        этого формата вывела в «Покупки» пять зон, все выше цены. Сортировка по одному
        лишь расстоянию скрывала различие.
        """
        passed = (z.side == "long" and z.price >= price) or (
            z.side == "short" and z.price < price)
        return (1 if passed else 0, away(z))

    # Ближайшая — среди ТОРГУЕМЫХ, а не вообще. Первая редакция считала её по всем, и
    # метка «◀ ближайшая» доставалась зоне, которую цена уже прошла, то есть исчезала
    # из показанного списка совсем.
    tradable = [z for z in unique if tradable_first(z)[0] == 0]
    nearest = min(tradable, key=away, default=None)

    def block(side: str, title: str) -> list[str]:
        """Зоны ОДНОЙ стороны от ближней к дальней. Группировка по стороне, а не по
        «выше/ниже цены»: у автора зоны покупок и продаж — разные списки, и зелёная
        строка в красном блоке (так вышло в первой редакции) читается как ошибка."""
        rows = sorted((z for z in unique if z.side == side), key=tradable_first)
        if not rows:
            return [f"{title}: нет", ""]
        passed = [z for z in rows if tradable_first(z)[0] == 1]
        live_side = [z for z in rows if tradable_first(z)[0] == 0]
        shown = live_side[:ZONES_PER_SIDE]
        out = [f"{title} — {len(shown)} из {len(live_side)}:"]
        for z in shown:
            mark = " ◀ ближайшая" if z is nearest else ""
            out.append(f"   {_fmt_zone(z, price)}{mark}")
        rest = live_side[len(shown):]
        if rest:
            lo = abs(rest[0].price - price) / price * 100
            hi = abs(rest[-1].price - price) / price * 100
            out.append(f"   … ещё {len(rest)} — от {lo:.1f}% до {hi:.0f}% от цены")
        if passed:
            # Пройденные НАЗЫВАЮТСЯ, но не подаются как торгуемые: по карте цена их уже
            # прошла, а карта может быть старой — читатель имеет право знать оба факта.
            out.append(f"   ({len(passed)} цена уже прошла — на графике они есть)")
        return [*out, ""]

    lines += block("long", "🟢 Покупки")
    lines += block("short", "🔴 Продажи")

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

    # Свёрнутые дубликаты НАЗЫВАЮТСЯ числом: молчаливое сокращение списка неотличимо от
    # того, что этих уровней в карте не было (§4.3).
    dupes = f" · {hidden_dupes} совпали по цене на разных ТФ" if hidden_dupes else ""
    tail = ["★ — старший таймфрейм · «лимитки» — цена ещё не касалась зоны,"
            " «по факту» — уже касалась" + dupes,
            origin,
            "Это карта уровней, а не торговая рекомендация."]
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

    async def analysis(self, symbol: str) -> Analysis | NotReady:
        """Зоны символа плюс строка о том, откуда они. Две ветки, и они разные по смыслу."""
        now = clock.now_ms()
        if symbol in self.uni.symbols:
            got = await asyncio.to_thread(read_map, symbol)
            if isinstance(got, NotReady):
                return got
            if not got.zones:
                return NotReady(
                    reason=f"{symbol}: карта пуста. Символ во вселенной, значит его считает"
                           f" служба — а она либо не запускалась, либо ещё не досчитала"
                           f" (uv run python -m hunter serve)")
            age_min = max(0, (now - got.last_seen_ms) // 60_000)
            return Analysis(symbol=symbol, zones=got.zones, stale_min=age_min,
                            origin=f"Карта системы, обновлена {_fmt_age(age_min)} назад.")
        built = await self.on_demand.map_of(symbol, now)
        if isinstance(built, NotReady):
            return built
        age_min = max(0, (now - built.built_at_ms) // 60_000)
        return Analysis(
            symbol=symbol, zones=built.zones, stale_min=age_min,
            origin=f"Карта собрана по запросу {_fmt_age(age_min)} назад: за этой монетой"
                   f" система постоянно не следит.")

    # --- ответ -------------------------------------------------------------

    async def answer(self, bot: Bot, chat: int | str, symbol: str,
                     reply_to: int | None, *, announce_refusal: bool = True) -> bool:
        """Три графика и сводка. Отказ на любом шаге НАЗЫВАЕТСЯ, а не молчит.

        ⚠ `announce_refusal=False` НЕ делает отказ молчаливым: он всё равно уходит в лог и
        в счёт неудач публикации. Различие в адресате — отказ в ответ на чей-то вопрос
        нужен спросившему, а тот же отказ в канале с многими читателями это мусор,
        которого никто не просил.
        """
        got = await self.analysis(symbol)
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
                png, tf_pps = await self._chart(got, tf, bars.bars, Path(tmp))
                pps += tf_pps
                await self.pacer.send(chat, f"график {tf}", lambda p=png: bot.send_photo(  # type: ignore[misc]
                    chat_id=chat, photo=FSInputFile(p)))
            await self.pacer.send(chat, "сводка", lambda: bot.send_message(
                chat_id=chat,
                text=compose_text(symbol, got.zones, pps, got.origin, tuple(missing),
                                  price=price, stale_min=got.stale_min),
                reply_to_message_id=reply_to))
        self.answers += 1
        return True

    async def _chart(self, got: Analysis, timeframe: str, bars: list[Bar],
                     tmp: Path) -> tuple[Path, list[ZoneSpec]]:
        """Рендер в рабочем потоке и ПО ОДНОМУ. Обоснование — у `_RENDER_LOCK`.

        В поток уходит и расчёт ПП, и рисование: оба синхронные, а на цикле событий в это
        время живёт опрос Telegram. Та же причина, по которой расчёт службы не имеет права
        оставаться на цикле (`service`, находка А-1).
        """
        def build() -> tuple[Path, list[ZoneSpec]]:
            tf_pps = pp_zones(bars, timeframe)
            shown = zones_for_chart(got.zones, timeframe, bars[-1].close)
            longs = len([z for z in got.zones if z.side == "long"])
            shorts = len([z for z in got.zones if z.side == "short"])
            png = chart_png(got.symbol, timeframe, bars, shown + tf_pps,
                            tmp / f"{timeframe}.png",
                            caption=f"{longs} лонг / {shorts} шорт зон")
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
        ok = await self.answer(bot, chat, symbol, message.message_id)
        self._trace(message, req, "ответ доставлен" if ok else "ответа нет",
                    started_ns, символ=symbol)

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


async def alive_loop(delivery: Delivery, watch: NetworkWatch) -> None:
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
                 обрывов_связи=watch.failures)
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

        publisher = asyncio.create_task(publish_loop(delivery, bot),
                                        name="публикация закреплённых")
        alive = asyncio.create_task(alive_loop(delivery, watch), name="сводка бота")
        try:
            await dp.start_polling(bot, handle_as_tasks=True,
                                   tasks_concurrency_limit=HANDLERS_MAX)
        finally:
            publisher.cancel()
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
