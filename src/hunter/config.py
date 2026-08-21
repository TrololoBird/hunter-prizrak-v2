"""Чтение закреплённой вселенной. FOUNDATION.md §5.

Здесь же — настройки ДОСТАВКИ (`BotConfig`), и лежат они в отдельном файле умышленно:
вселенная это то, ЧТО система считает (§5), а доставка — кому и когда она про это
рассказывает. Смешать их значило бы, что правка списка публикуемых монет меняет файл,
по которому воспроизводится расчёт.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from .bars import PROFILE_MS, TIMEFRAME_MS

DEFAULT_PATH = Path("config/universe.toml")
BOT_PATH = Path("config/bot.toml")


@dataclass(frozen=True, slots=True)
class Universe:
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    source: Path
    venue: str = "binanceusdm"
    """Площадка Binance: `binanceusdm` (бессрочные на USDT), `binancecoinm` (монетное
    обеспечение) или `binance` (спот). Список — в `exchange.VENUES`.

    ⚠ Ключ задаётся ОПЕРАТОРОМ, а не выводится из символов: `BTC/USDT` и `BTC/USDT:USDT` —
    разные рынки с разной ценой и разным объёмом, и угадывание подменило бы источник
    профиля молча. Умолчание сохраняет прежнее поведение для конфигураций, где ключа нет.
    """

    board: bool = False
    """Считать ли ВСЮ ДОСКУ площадки, а не только перечисленные символы.

    ПРИКАЗ ВЛАДЕЛЬЦА 2026-08-21, дословно: «всю доску, 696». Задан вопросом — сканер
    смотрит всю доску или хватит полусотни отобранных монет, — и ответ был «всю».

    ⚠ ЧТО ЭТО МЕНЯЕТ В СМЫСЛЕ `symbols`. Список остаётся ЯДРОМ, а не всей вселенной:
    ядро считается полной лестницей `timeframes`, остальная доска — укороченной
    `board_timeframes`. Раскрытие делает `run.expand_board` по живому вызову биржи, и
    после него `symbols` содержит уже всю доску, а ядро помнит поле `core`. Так все
    циклы `for sym in uni.symbols` продолжают означать «по всему, что считаем».

    ⚠ ЦЕНА ОДНОЙ ЛЕСТНИЦЫ НА ВСЕХ — арифметика, а не вкус. При горизонте 180 суток
    5м даёт 51 840 баров на символ, 15м — 17 280; вдвоём это 92.3% всей лестницы.
    На 696 символах полная лестница весит 52.1 млн баров и 3.89 ГБ ОЗУ при четырёх
    логических ядрах машины, тогда как 1ч/4ч/1Д/1Н — 4.0 млн и 0.30 ГБ (замер
    `run.seed_depth` и `sys.getsizeof(Bar)` = 80 байт, 2026-08-21). Засев по весу
    биржи: 116 минут против 9.
    """

    board_timeframes: tuple[str, ...] = ()
    """Лестница для символов ДОСКИ — тех, что не входят в ядро `symbols`.

    Обязана быть ПОДМНОЖЕСТВОМ `timeframes`: доска, считающая таймфрейм, которого нет у
    ядра, дала бы две несопоставимые карты под одним именем — тот самый класс «две
    сущности под одним человеческим именем», из-за которого 2026-08-18 полгода жил
    неверный стоп.
    """

    cap: int = 0
    """Потолок числа символов — ручка ОПЕРАТОРА (`--symbols N`), ноль = без потолка.

    ⚠ ЗАВЕДЕНО, ПОТОМУ ЧТО ПРЕЖНЯЯ РУЧКА УМЕРЛА БЫ МОЛЧА. `--symbols N` резал
    `uni.symbols[:N]` в разборе аргументов — то есть ДО того, как `run.expand_board`
    спросит у биржи всю доску и вернёт 696 обратно. Тормоз остался бы в справке и
    перестал бы тормозить: ровно тот дефект, из-за которого удалены `bars_per_timeframe`
    и `admission_required_bars`. Потолок применяется ПОСЛЕ раскрытия, там же, где
    известен окончательный порядок символов.

    ⚠ Ключом TOML НЕ является (см. `_known_keys`): это ручка запуска, а не свойство
    вселенной. В файле её место заняло бы решение, которое обязано быть видно в команде.
    """

    board_contracts: tuple[str, ...] = ()
    """Какие РОДА КОНТРАКТОВ пускать на доску. Пусто = доска выключена.

    ПРИКАЗ ВЛАДЕЛЬЦА 2026-08-21, дословно: «конечно только крипта! золото и серебро это
    исключение!». Сказано в ответ на состав доски: из 696 активных бессрочных
    binanceusdm крипта 525, акции США 137, Гонконг 12, Корея 8, Китай 2, товарные 8,
    доIPOшные 2 (ANTHROPIC, OPENAI).

    Значение сверяется с полем `contractType` в ответе биржи, а не с видом символа:
    `PERPETUAL` — крипта (525 монет плюс индексы BTCDOM и ALL), `TRADIFI_PERPETUAL` —
    всё остальное, от AAPL до нефти.

    ⚠ ИСКЛЮЧЕНИЯ ВЛАДЕЛЬЦА ОТДЕЛЬНОГО КЛЮЧА НЕ ТРЕБУЮТ, и это не экономия, а свойство
    устройства: ЯДРО (`symbols`) попадает во вселенную ВСЕГДА, мимо фильтра. XAU и XAG
    в ядре с 2026-08-18, значит золото и серебро остаются просто потому, что владелец
    их туда и вписал. Второй список исключений был бы второй сущностью под тем же
    смыслом — и разошёлся бы с первым в первый же день.
    """

    core: frozenset[str] = frozenset()
    """Символы, считаемые ПОЛНОЙ лестницей. Пишет загрузчик и `run.expand_board`.

    ⚠ Ключом TOML НЕ является (см. `_known_keys`): его не задаёт оператор, он выводится
    из `symbols` до раскрытия доски. Ровно та же причина, по которой не является ключом
    `source`.
    """

    profile_timeframe: str = "1m"
    """Разрешение свечей, ИЗ КОТОРЫХ строится профиль объёма. Не таймфрейм анализа.

    ⚠ Появился 2026-08-12 вместе с переводом профиля со сделок на свечи (решение
    владельца). Разрешение вынесено в конфигурацию, а не зашито, потому что оно —
    ЦЕНА ПРИБЛИЖЕНИЯ: объём свечи раскладывается по её диапазону равномерно, и чем
    младше свеча, тем меньше ошибка этого допущения. Умолчание `1m` — самое младшее,
    что отдаёт биржа, и то же, которым строит профиль TradingView.

    Допустимые значения — `bars.PROFILE_MS`. Список ТФ анализа (`timeframes`) сюда НЕ
    входит и входить не должен: профиль по дневным свечам размазал бы объём суток по
    всему их диапазону.
    """


    def ladder(self, symbol: str) -> tuple[str, ...]:
        """Лестница ИМЕННО ЭТОГО символа: полная у ядра, укороченная у доски.

        ⚠ Спрашивать `uni.timeframes` внутри цикла по символам с этого дня НЕЛЬЗЯ: при
        включённой доске это молча вернуло бы полную лестницу для всех 696 монет, то
        есть 52.1 млн баров вместо 4.0 млн. Прибор, который это ловит, — сама подпись
        метода: он требует символ, и забыть его нечем.
        """
        if not self.board or symbol in self.core:
            return self.timeframes
        return self.board_timeframes


def _reject_unknown(section: Mapping[str, object], known: frozenset[str], path: Path) -> None:
    """Неизвестный ключ — названный отказ, а не молчаливое умолчание.

    Дефект, от которого это защищает, случался ДВАЖДЫ: `bars_per_timeframe` (удалён
    2026-08-11) и `admission_required_bars` (удалён 2026-08-17) лежали в файле мёртвыми —
    их не читала ни одна строка, а докстроки ссылались на них как на действующие.
    Зеркальная половина того же дефекта — опечатка в имени ключа: загрузчик, молча
    пропускающий неизвестное, подставил бы умолчание вместо значения оператора.
    То же правило, что `extra="forbid"` у моделей (§10.1, гейт models_forbid_extra),
    перенесённое на TOML. Разбор: docs/audit/frozen-rules-2026-08-17.md §4."""
    unknown = sorted(set(section) - known)
    if unknown:
        raise ValueError(
            f"{path}: неизвестные ключи {unknown}; известны {sorted(known)}. "
            f"Мёртвый или опечатанный ключ не читается никем — это молчаливая деградация")


def _known_keys(cls: type) -> frozenset[str]:
    """Ключи TOML = поля датакласса минус служебный `source` (его пишет загрузчик).

    Выводится из полей, а не переписывается руками: рукописный список разошёлся бы с
    датаклассом молча — тот же дефект, от которого защищает `_reject_unknown`.

    ⚠ `core` и `cap` исключены по той же причине, что и `source`: в файле их нет.
    `core` выводится из `symbols` до раскрытия доски, `cap` приходит из `--symbols`."""
    return frozenset(f.name for f in fields(cls)) - {"source", "core", "cap"}


def _reject_unknown_sections(data: Mapping[str, object], known: str, path: Path) -> None:
    """Лишняя ТАБЛИЦА — тот же дефект, что лишний ключ: `[universo]` или вернувшийся
    `[bars]` молча пролежал бы мёртвым (найдено rules-auditor 2026-08-17)."""
    unknown = sorted(set(data) - {known})
    if unknown:
        raise ValueError(
            f"{path}: неизвестные секции {unknown}; здесь живёт только [{known}]")


def load_universe(path: Path = DEFAULT_PATH) -> Universe:
    if not path.exists():
        raise FileNotFoundError(f"нет файла вселенной {path} — §5 требует закреплённый набор")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _reject_unknown_sections(data, "universe", path)
    section = data.get("universe")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: нет секции [universe]")
    _reject_unknown(section, _known_keys(Universe), path)

    symbols = section.get("symbols")
    if not symbols:
        raise ValueError(f"{path}: пустой список symbols")
    timeframes = section.get("timeframes")
    if not timeframes:
        raise ValueError(f"{path}: пустой список timeframes")

    unknown = [t for t in timeframes if t not in TIMEFRAME_MS]
    if unknown:
        raise ValueError(f"{path}: таймфреймы {unknown} вне §2.8 ({sorted(TIMEFRAME_MS)})")

    dupes = {s for s in symbols if symbols.count(s) > 1}
    if dupes:
        raise ValueError(f"{path}: символы повторяются: {sorted(dupes)}")

    venue = section.get("venue", "binanceusdm")
    if not isinstance(venue, str):
        raise ValueError(f"{path}: venue обязан быть строкой, а не {type(venue).__name__}")

    # ⚠ Разрешение профиля проверяется по СВОЕЙ таблице, а не по §2.8: значение вроде
    # `1d` здесь синтаксически верно и содержательно разрушительно — объём суток лёг бы
    # ровным слоем по всему суточному диапазону.
    profile_tf = section.get("profile_timeframe", "1m")
    if profile_tf not in PROFILE_MS:
        raise ValueError(
            f"{path}: profile_timeframe={profile_tf!r} вне {sorted(PROFILE_MS)}; "
            f"профиль строится из МЛАДШИХ свечей, а не из таймфреймов анализа")

    # --- ДОСКА -------------------------------------------------------------------
    # Приказ владельца 2026-08-21: «всю доску, 696». Ключ выключен по умолчанию, потому
    # что включённая доска меняет ЦЕНУ прогона на два порядка, и это обязано быть
    # решением файла, а не умолчанием библиотеки.
    board = section.get("board", False)
    if not isinstance(board, bool):
        raise ValueError(f"{path}: board обязан быть true/false, а не {type(board).__name__}")

    board_tfs = tuple(section.get("board_timeframes", ()))
    unknown = [t for t in board_tfs if t not in TIMEFRAME_MS]
    if unknown:
        raise ValueError(
            f"{path}: board_timeframes {unknown} вне §2.8 ({sorted(TIMEFRAME_MS)})")
    # ⚠ ПОДМНОЖЕСТВО, А НЕ ПРОИЗВОЛЬНЫЙ СПИСОК. Таймфрейм, который считает доска и не
    # считает ядро, дал бы две карты под одним именем «уровень», и одна из них навсегда
    # осталась бы непроверенной — тот же класс дефекта, что границы уровня 2026-08-18.
    extra = [t for t in board_tfs if t not in timeframes]
    if extra:
        raise ValueError(
            f"{path}: board_timeframes {extra} нет в timeframes {list(timeframes)}; "
            f"лестница доски обязана быть подмножеством лестницы ядра")
    board_contracts = tuple(section.get("board_contracts", ()))
    if any(not isinstance(c, str) or not c for c in board_contracts):
        raise ValueError(f"{path}: board_contracts обязан быть списком непустых строк")
    if board and not board_contracts:
        raise ValueError(
            f"{path}: board=true без board_contracts — на доску пошли бы ВСЕ рынки "
            f"площадки, включая акции и товарные (167 из 696 на binanceusdm). "
            f"Владелец 2026-08-21: «конечно только крипта»")
    if board_contracts and not board:
        raise ValueError(
            f"{path}: board_contracts задан, а board=false — ключ не читался бы никем")
    if board and not board_tfs:
        raise ValueError(
            f"{path}: board=true без board_timeframes — доска считалась бы ПОЛНОЙ "
            f"лестницей по всем рынкам площадки (52.1 млн баров, 3.89 ГБ ОЗУ на 696 "
            f"символах). Укажите лестницу доски явно")
    if board_tfs and not board:
        raise ValueError(
            f"{path}: board_timeframes задан, а board=false — ключ не читался бы никем. "
            f"Это тот же мёртвый ключ, что bars_per_timeframe и admission_required_bars")

    # ⚠ ПО ИМЕНАМ, А НЕ ПО ПОРЯДКУ. Позиционный вызов уже сломался 2026-08-21, когда
    # между `board_timeframes` и `core` появилось поле `cap`: `frozenset` уехал в
    # целочисленный потолок. Поймал mypy, а не прогон, — и только потому, что типы
    # оказались разными; два соседних поля одного типа разъехались бы МОЛЧА.
    return Universe(symbols=tuple(symbols), timeframes=tuple(timeframes), source=path,
                    venue=venue, board=board, board_timeframes=board_tfs,
                    board_contracts=board_contracts,
                    core=frozenset(symbols), profile_timeframe=profile_tf)


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Настройки ДОСТАВКИ. Ни одно из этих чисел не участвует в расчёте.

    ⚠ Отделено от вселенной осознанно: universe.toml — файл, по которому воспроизводится
    расчёт (§5, §10.6), и правка списка публикуемых монет не имеет права его трогать.

    ⚠ Отсутствие файла — НЕ ошибка, а состояние «публиковать нечего», и оно НАЗЫВАЕТСЯ
    вызывающим в логе (§4.3): пустой `pinned` и «файла нет» различаются полем `source`.
    """

    pinned: tuple[str, ...] = ()
    """Закреплённые монеты: их карта уходит в канал по расписанию, без запроса.

    Формат записи — любой, какой понимает бот (`BTC`, `btcusdt`, `BTC/USDT:USDT`):
    список читает человек, а не программа. Неопознанная запись — НАЗВАННЫЙ отказ
    в логе публикации, а не молчаливый пропуск.
    """

    publish_timeframe: str = "4h"
    """К закрытию какого бара привязана публикация. Число ВЫВЕДЕНО, а не выбрано.

    Старший из трёх графиков бота — 4ч (`tgbot.CHART_TFS`), и публиковать чаще нечего:
    между закрытиями 4ч старшая картина не меняется, а младшие зоны пересылались бы
    в канал тем же составом. Реже — значит показывать карту, устаревшую на бар.
    """

    answer_cooldown_s: int = 60
    """Пауза между запросами ОДНОГО пользователя. Выведена из лимита Telegram, не из вкуса.

    Telegram документирует (core.telegram.org/bots/faq): «In a group, bots are not be able
    to send more than 20 messages per minute». Один ответ бота — четыре сообщения
    (три графика и текст), то есть ёмкость группы это пять ответов в минуту. Минута на
    пользователя означает, что один человек не может занять больше одной пятой ёмкости.

    ⚠ Само число паузы не замерено: замерять нечего, пока бот не работал в живой группе.
    Замер, который его уточнит, — доля отказов по перегреву в логе (`log.degraded`).
    """

    build_queue_max: int = 8
    """Сколько монет вне вселенной может ждать сборки одновременно.

    Сборка идёт ПО ОДНОЙ (общий лимит веса биржи на весь IP), и её цена ЗАМЕРЕНА:
    холодная монета на боевом горизонте 180 суток — 345 с (LTC/USDT:USDT, 2026-08-17;
    75 123 бара засева, 266 100 минутных свечей профиля, 165 уровней). Значит восемь в
    очереди — это до 45 минут ожидания у последнего; держать больше значит обещать ответ,
    которого никто не дождётся. Переполнение — названный отказ, а не молчаливое
    выбрасывание запроса. Протокол: `docs/audit/tgbot-channel-2026-08-17.md`.
    """

    map_ttl_minutes: int = 30
    """Сколько построенная по запросу карта считается свежей. ⚠ ЧИСЛО — РАЗМЕН, НЕ ЗАМЕР.

    Строго по §2.8 карта стареет с закрытием бара младшего ТФ, то есть за 5 минут. Так и
    считает служба. Но пересобирать карту символа вне вселенной каждые 5 минут нельзя:
    одна сборка стоит минут работы и сотен запросов к бирже. Тридцать минут — цена,
    которую платит монета, за которой система не следит постоянно; она НАЗЫВАЕТСЯ
    владельцу в самом ответе бота («карта построена по запросу … назад»), а не скрыта.
    """

    source: Path | None = None
    """Откуда прочитано. `None` — файла нет, и это отличается от «файл есть, список пуст»."""


def _positive_int(section: Mapping[str, object], key: str, default: int, path: Path) -> int:
    """Целое строго больше нуля. Ноль здесь всегда означал бы выключение через опечатку."""
    raw = section.get(key, default)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise ValueError(f"{path}: {key}={raw!r} — нужно целое больше нуля")
    return raw


def load_bot_config(path: Path = BOT_PATH) -> BotConfig:
    """Настройки доставки. Нет файла — умолчания и `source=None`, а не отказ.

    ⚠ Именно так, а не `FileNotFoundError`, как у вселенной: без вселенной считать нечего,
    а без этого файла бот работает — он просто ничего не публикует сам. Разница видна
    вызывающему по `source`, и он обязан её назвать (см. `tgbot.main`).
    """
    if not path.exists():
        return BotConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _reject_unknown_sections(data, "bot", path)
    section = data.get("bot")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: нет секции [bot]")
    _reject_unknown(section, _known_keys(BotConfig), path)

    pinned = section.get("pinned", [])
    if not isinstance(pinned, list) or any(not isinstance(s, str) for s in pinned):
        raise ValueError(f"{path}: pinned обязан быть списком строк")

    tf = section.get("publish_timeframe", "4h")
    if tf not in TIMEFRAME_MS:
        raise ValueError(f"{path}: publish_timeframe={tf!r} вне §2.8 ({sorted(TIMEFRAME_MS)})")

    return BotConfig(
        pinned=tuple(pinned),
        publish_timeframe=tf,
        answer_cooldown_s=_positive_int(section, "answer_cooldown_s", 60, path),
        build_queue_max=_positive_int(section, "build_queue_max", 8, path),
        map_ttl_minutes=_positive_int(section, "map_ttl_minutes", 30, path),
        source=path,
    )
