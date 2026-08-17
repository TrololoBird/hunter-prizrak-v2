"""Чтение закреплённой вселенной. FOUNDATION.md §5.

Здесь же — настройки ДОСТАВКИ (`BotConfig`), и лежат они в отдельном файле умышленно:
вселенная это то, ЧТО система считает (§5), а доставка — кому и когда она про это
рассказывает. Смешать их значило бы, что правка списка публикуемых монет меняет файл,
по которому воспроизводится расчёт.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
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


def load_universe(path: Path = DEFAULT_PATH) -> Universe:
    if not path.exists():
        raise FileNotFoundError(f"нет файла вселенной {path} — §5 требует закреплённый набор")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("universe")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: нет секции [universe]")

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

    return Universe(tuple(symbols), tuple(timeframes), path, venue, profile_tf)


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Настройки ДОСТАВКИ. Ни одно из этих чисел не участвует в расчёте.

    ⚠ Отделено от вселенной осознанно: `universe.toml` — файл, по которому воспроизводится
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
    section = data.get("bot")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: нет секции [bot]")

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
