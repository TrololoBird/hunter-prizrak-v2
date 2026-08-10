"""Хранение: parquet для кадров, SQLite для леджера. FOUNDATION.md §10.2, §10.3.

Parquet — сырые кадры, без которых детерминированный повтор невозможен (§10.3).
SQLite — состояние и исходы, со схемой и ограничениями (§10.2).

Запись в боевую базу возможна ТОЛЬКО через `open_production_ledger`, и это ограничение
уровня СУБД, а не дисциплина: все прочие соединения открываются `mode=ro`, и попытка
записи через них падает с «attempt to write a readonly database». Кто зовёт
`open_production_ledger`, проверяет gates/production_writer.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ConfigDict

from . import log
from .bars import tf_ms
from .levels import Level, LevelState
from .models import Bar, BarBinnedTrades, NotReady, TradeHistogram

DATA_DIR = Path("data")
FRAMES_DIR = DATA_DIR / "frames"
LEDGER_PATH = DATA_DIR / "ledger.sqlite3"

# §10.2 задаёт ограничения дословно: NOT NULL на цене входа, CHECK (stop != entry),
# UNIQUE (symbol, opened_at). Остальные поля появятся на этапах 5–7 вместе с тем,
# что их производит; поля без продюсера здесь заводить нельзя (§0).
#
# ⚠ Ключ РАСШИРЕН против буквы §10.2 — с (symbol, opened_at) до
# (symbol, timeframe, direction, opened_at), — и это правка документа, а не вольность.
# Сетки ТФ вложены: метка открытия 4ч-бара ВСЕГДА совпадает с меткой какого-то 1ч-бара,
# 1ч — с 15м и 5м. Прогон: «1ч сигнал записан; 4ч сигнал в ту же метку — UNIQUE
# constraint failed». Совпадение здесь норма предметной области, а не бессмыслица,
# которую ограничение должно ловить. Замер и разбор: docs/audit/ledger-honesty-2026-08-04.md
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL DEFAULT 'level' CHECK (kind IN ('level', 'pp')),
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('long', 'short')),
    opened_at   INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    entry       REAL    NOT NULL,
    stop        REAL    NOT NULL,
    target      REAL,
    frames_ref  TEXT    NOT NULL,
    CHECK (stop != entry),
    CHECK (entry > 0),
    CHECK (stop > 0),
    CHECK (recorded_at > 0),
    UNIQUE (kind, symbol, timeframe, direction, opened_at)
);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id  INTEGER PRIMARY KEY REFERENCES signals(id),
    kind       TEXT    NOT NULL CHECK (kind IN ('stop', 'target', 'ambiguous')),
    closed_at  INTEGER NOT NULL,
    exit_price REAL,
    r          REAL,
    CHECK ((kind = 'ambiguous') = (r IS NULL)),
    CHECK ((kind = 'ambiguous') = (exit_price IS NULL))
);

-- Состояние сигнала, ещё НЕ ставшего исходом (v4, 2026-08-10). Ровно два значения:
-- `not_filled` — цена не дошла до входа, сделки не было (стр. 30: вход лимитками);
-- `open` — вход состоялся, ни стоп, ни цель не достигнуты.
-- Строка перезаписывается каждым прогоном (`as_of` — до какого бара считали): это
-- СОСТОЯНИЕ, а не событие. Событие необратимо и живёт в `outcomes`.
CREATE TABLE IF NOT EXISTS signal_states (
    signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
    state     TEXT    NOT NULL CHECK (state IN ('not_filled', 'open')),
    as_of     INTEGER NOT NULL CHECK (as_of > 0)
);

CREATE TABLE IF NOT EXISTS levels (
    symbol       TEXT    NOT NULL,
    timeframe    TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('long', 'short')),
    price        REAL    NOT NULL CHECK (price > 0),
    zone_lo      REAL    NOT NULL CHECK (zone_lo > 0),
    zone_hi      REAL    NOT NULL CHECK (zone_hi > 0),
    boundary_lo  REAL    NOT NULL CHECK (boundary_lo > 0),
    boundary_hi  REAL    NOT NULL CHECK (boundary_hi > 0),
    volume       REAL    NOT NULL CHECK (volume > 0),
    from_ms      INTEGER NOT NULL,
    to_ms        INTEGER NOT NULL,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    state        TEXT    NOT NULL CHECK (state IN ('active', 'worked_off', 'flipped')),
    retired_at   INTEGER,
    CHECK (zone_lo <= price AND price <= zone_hi),
    CHECK (boundary_lo < boundary_hi),
    CHECK (to_ms > from_ms),
    CHECK (last_seen >= first_seen),
    CHECK ((retired_at IS NULL) = (state = 'active')),
    PRIMARY KEY (symbol, timeframe, from_ms, to_ms)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# Таблица `levels` — КАРТА, живущая между прогонами (стр. 25, 31: уровень существует, пока
# не отработан, а не пока он виден в окне баров). Ключ — ОКНО СТРУКТУРЫ, а не цена ПОК:
# структура это то, чем уровень порождён, и она не меняется, тогда как ПОК может
# сдвинуться на бин при доливке сделок. Ключ по цене плодил бы дубликаты на каждый прогон.
#
# `retired_at` заполнен ровно тогда, когда состояние не `active`, и ограничение это
# требует: «уровень снят» без даты и «дата без снятия» — обе формы молчания (§4.3).
#
# Полей «лимитки выставлены» и «позиция взята» здесь НЕТ. У автора они в легенде есть
# (🟢, ✅), но продюсера у них в этой системе нет: ордера она не ставит (§1 — оператор
# торгует руками) и о них не узнаёт. Поле без продюсера запрещено §0 и есть фирменный
# дефект прошлого проекта.
# Исход «неоднозначно» (бар накрыл и стоп, и цель) хранится БЕЗ r и без цены выхода, и
# ограничение это требует: подставить туда ноль значило бы записать безубыток там, где
# результат неизвестен (§4.3). Открытые и несостоявшиеся сделки в таблицу не попадают
# вовсе — исход у них ещё не наступил, а строка означала бы, что наступил.
#
# `recorded_at` — МОМЕНТ, КОГДА СИСТЕМА УЗНАЛА О СИГНАЛЕ, и он не то же самое, что
# `opened_at` (бар рождения уровня, стр. 23). Разница между ними и есть разница между
# журналом и бэктестом: `opened_at` может быть на сотни баров в прошлом, потому что
# уровень строится по завершённой структуре, а `recorded_at` — всегда «сейчас».
#
# ⚠ Поле заведено 2026-08-04. До него исход считался по барам, которые УЖЕ ЛЕЖАЛИ В
# ПАМЯТИ на момент записи сигнала: каждый прогон заново переигрывал всю доступную
# историю и складывал результат в базу, которую §10.2 и §8 объявляют боевой. Владельцу
# при этом печаталось «средний R» по этой подвыборке — то есть по бэктесту под именем
# журнала. Теперь исход считается ТОЛЬКО по барам, закрывшимся ПОЗЖЕ `recorded_at`;
# следствие честное и неприятное: в первый прогон исходов не бывает вовсе.

SCHEMA_VERSION = "5"
"""Версия схемы леджера. Растёт, когда прежние строки перестают означать то же самое.

4 → 5 (2026-08-10): у сигнала появилась ЦЕЛЬ (`target`). Без неё исход сделки нельзя
досчитать ни в каком прогоне, кроме того, который её выдал, — и отсюда шло СМЕЩЕНИЕ
ОТБОРА, найденное сводкой исходов в тот же день. Исход считался только для сигналов,
эмитируемых ЗАНОВО (`decided[sym].emissions`); сигнал, чей уровень ушёл из карты
(отработан по стр. 25, пробит по стр. 43 или просто не отобран), не досчитывался
НИКОГДА. Замер на боевом леджере: 76 сигналов из 112 — то есть две трети журнала —
навсегда оставались без ответа, а «средний R» считался по оставшейся трети, смещённой
в сторону уровней, которые система продолжает отбирать.

Старые строки получают `target = NULL`: цель у них не сохранялась и восстановлению не
подлежит. Дорешать их нельзя, и сводка обязана называть их отдельно, а не молчать.

3 → 4 (2026-08-10): заведена `signal_states` — состояние сигнала, ЕЩЁ НЕ ставшего
исходом. До неё `emit.outcome_of` каждый прогон различал `not_filled` (цена не дошла до
входа) и `open` (сделка идёт), и оба ответа ВЫБРАСЫВАЛИСЬ: в леджер попадали только
`stop`/`target`/`ambiguous`, а различие жило одним счётчиком в отчёте прогона. Цена
потери вскрылась сводкой исходов: клетка «6 сигналов, 1441 бар прожито, ноль исходов»
неотличима от дефекта, хотя может означать «цена ни разу не дошла до входа» — то есть
законный ответ системы. Прежние строки не меняются: таблица новая и пустая, старые
сигналы состояния не получают, пока их не пересчитает прогон.

2 → 3 (2026-08-10): появился `kind` — тип сигнала: `level` (сделка от уровня ПОК) и
`pp` (сделка от переприора, стр. 50; реестр долга, строка 3). Уникальный ключ расширен
типом: сигнал от ПП и сигнал от уровня с совпавшими (символ, ТФ, сторона, бар) — разные
сигналы, и прежний ключ дедуплицировал бы их друг о друга. Прежние строки все от
уровней — при перестройке получают `kind = 'level'`, их смысл не меняется.

1 → 2 (2026-08-04): появился `recorded_at`, ключ сигнала расширен ТФ и стороной, исход
считается только по будущим барам. Строки версии 1 — переигранная история, и сложить их
с новыми значило бы смешать бэктест с журналом в одном `AVG(r)`.
"""


# --- кадры для повтора (§10.3) ------------------------------------------------

def _safe(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def frames_path(run_id: str, symbol: str, timeframe: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / f"{timeframe}.parquet"


def bars_to_frame(bars: list[Bar]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_ms": [b.open_ms for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        schema={"open_ms": pl.Int64, "open": pl.Float64, "high": pl.Float64,
                "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64},
    )


def frame_to_bars(df: pl.DataFrame) -> list[Bar]:
    return [Bar(**row) for row in df.iter_rows(named=True)]


def write_bars(run_id: str, symbol: str, timeframe: str, bars: list[Bar]) -> Path:
    path = frames_path(run_id, symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars_to_frame(bars).write_parquet(path, compression="zstd")
    return path


def read_bars(run_id: str, symbol: str, timeframe: str) -> list[Bar]:
    return frame_to_bars(pl.read_parquet(frames_path(run_id, symbol, timeframe)))


def histogram_to_frame(h: TradeHistogram) -> pl.DataFrame:
    bins = sorted(h.qty_by_bin)
    return pl.DataFrame(
        {
            "bin": bins,
            "price": [float(h.bin_price(b)) for b in bins],
            "qty": [h.qty_by_bin[b] for b in bins],
            "n": [h.count_by_bin[b] for b in bins],
        },
        schema={"bin": pl.Int64, "price": pl.Float64, "qty": pl.Float64, "n": pl.Int64},
    )


def write_histogram(run_id: str, h: TradeHistogram) -> Path:
    path = FRAMES_DIR / run_id / _safe(h.symbol) / "profile.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    histogram_to_frame(h).write_parquet(path, compression="zstd")
    return path


def _binned_path(run_id: str, symbol: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / "trades_by_bar.parquet"


def write_binned_trades(run_id: str, t: BarBinnedTrades) -> Path:
    """Сделки по корзинам баров. Без них уровень §2.2 из кадров не восстановить."""
    rows = sorted((b, i, q) for b, bins in t.qty.items() for i, q in bins.items())
    path = _binned_path(run_id, t.symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "bucket_ms": [r[0] for r in rows],
            "bin": [r[1] for r in rows],
            "qty": [r[2] for r in rows],
            "n": [t.cnt.get(r[0], {}).get(r[1], 0) for r in rows],
        },
        schema={"bucket_ms": pl.Int64, "bin": pl.Int64, "qty": pl.Float64, "n": pl.Int64},
    ).write_parquet(path, compression="zstd")
    return path


def read_binned_trades(
    run_id: str, dir_name: str, tick_size: Decimal, bucket_ms: int,
    symbol: str | None = None,
) -> BarBinnedTrades | NotReady:
    """Восстановить раскладку сделок. Отсутствие файла — названная причина, не пустота.

    `dir_name` адресует каталог, `symbol` — НАСТОЯЩЕЕ имя. Их надо различать: имя каталога
    искажено (`BTC_USDT_USDT`), и попав в текст причины, оно уезжает в карточку владельца.
    Замер 2026-08-04: именно так и уехало, поймал повтор.
    """
    path = _binned_path(run_id, dir_name)
    name = symbol or dir_name
    if not path.exists():
        return NotReady(reason=f"{name}: файла сделок нет — {path}")
    df = pl.read_parquet(path)
    t = BarBinnedTrades(symbol=name, tick_size=tick_size, bucket_ms=bucket_ms)
    for row in df.iter_rows(named=True):
        b, i = int(row["bucket_ms"]), int(row["bin"])
        t.qty.setdefault(b, {})[i] = float(row["qty"])
        t.cnt.setdefault(b, {})[i] = int(row["n"])
        t.trades_seen += int(row["n"])
        t.qty_seen += float(row["qty"])
    return t


# --- карточка: единица повтора (§10.3, §10.6 условие 2) -----------------------

def card_path(run_id: str, symbol: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / "card.txt"


def write_card(run_id: str, symbol: str, text: str) -> Path:
    path = card_path(run_id, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def read_card(run_id: str, symbol: str) -> str | NotReady:
    path = card_path(run_id, symbol)
    if not path.exists():
        return NotReady(reason=f"{symbol}: карточки прогона нет — {path}")
    return path.read_text(encoding="utf-8")


def write_meta(run_id: str, symbol: str, tick_size: Decimal, bucket_ms: int) -> Path:
    """Шаг цены и сетка корзин. Без них раскладку сделок из parquet не собрать обратно."""
    path = FRAMES_DIR / run_id / _safe(symbol) / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbol": symbol, "tick_size": str(tick_size),
                                "bucket_ms": bucket_ms}, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
    return path


def read_meta(run_id: str, symbol: str) -> tuple[str, Decimal, int] | NotReady:
    """Возвращает НАСТОЯЩЕЕ имя символа, шаг цены и сетку корзин.

    Имя каталога — это `_safe(symbol)`, из него исходное имя не восстановить (в нём
    подчёркивания вместо `/` и `:`), поэтому оно хранится явно.
    """
    path = FRAMES_DIR / run_id / _safe(symbol) / "meta.json"
    if not path.exists():
        return NotReady(reason=f"{symbol}: meta.json прогона нет — {path}")
    d = json.loads(path.read_text(encoding="utf-8"))
    return str(d["symbol"]), Decimal(d["tick_size"]), int(d["bucket_ms"])


def archive_dir(run_id: str, symbol: str) -> Path:
    """Срез архива сделок, принадлежащий ЭТОМУ прогону. Основа герметичности повтора."""
    return FRAMES_DIR / run_id / _safe(symbol) / "aggcache"


def write_archive_slice(run_id: str, symbol: str, source: Path) -> Path:
    """Положить сутки архива в кадры прогона. ЖЁСТКОЙ ССЫЛКОЙ, копией — только если нельзя.

    ⚠ Без этого повтор НЕ герметичен, и это показано прогоном: кадры (parquet) не
    трогались, из общего `data/aggcache` убраны одни сутки — карточка ETH поехала с 3244
    строк на 2785, 483 строки различий. `replay --diff` напечатал бы «расчёт изменился»
    при неизменном коде и неизменных кадрах, а §10.6 условие 2 объявляет этот дифф
    единственной проверкой, не требующей чтения кода.

    Прежняя докстрока `replay` доказывала обратное — что архив неизменяем и потому
    пригоден. Верно про СОДЕРЖИМОЕ суток и неверно про их НАЛИЧИЕ, а профиль зависит от
    обоих.

    ⚠ Копирование заменено ссылкой 2026-08-06, и заменено ЗАМЕРОМ, а не из аккуратности.
    Пакетный прогон складывал срез один раз, и цена не бросалась в глаза; служба 24/7
    (А-1) складывает его КАЖДЫЙ ЦИКЛ, стирая предыдущий. Замер на трёх символах при
    горизонте 5 суток: 248 МБ на цикл, из них 245 файлов среза — 138 МБ на одном BTC.
    При такте в 300 с и полной вселенной это порядка 2 ГБ каждые пять минут, то есть
    десятки гигабайт записи в час на данные, которые уже лежат в общем кэше.

    Жёсткая ссылка даёт ту же герметичность, потому что герметичность здесь про НАЛИЧИЕ
    файла, а не про его копию: удаление из общего кэша не трогает содержимое, пока на
    него ссылается срез прогона, а `archive._write_atomic` подменяет файл через
    `os.replace`, то есть создаёт НОВЫЙ inode и на старую ссылку не влияет.

    Откат на копирование — при `OSError`: другой том, файловая система без ссылок,
    исчерпанный лимит ссылок. Это деградация, а не отказ, и она НАЗВАНА в логе (§4.3).
    """
    dst = archive_dir(run_id, symbol) / source.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        os.link(source, dst)
    except OSError as e:
        log.degraded("срез архива скопирован, а не связан ссылкой",
                     файл=source.name, причина=f"{type(e).__name__} {e}")
        shutil.copyfile(source, dst)
    return dst


def clear_run(run_id: str, symbol: str) -> None:
    """Стереть кадры символа перед записью новых.

    ⚠ `--run-id` по умолчанию `last`, а `write_bars`/`write_card` перезаписывают файлы,
    но НЕ удаляют оставшиеся от прошлого прогона. После прогона с `--symbols 3` в каталоге
    оставались кадры всех 27 символов от прошлого раза, и `saved_symbols` перечисляла то,
    что лежит на диске: повтор сравнивал карточку одного прогона с кадрами двух (А-4).
    """
    d = FRAMES_DIR / run_id / _safe(symbol)
    if d.is_dir():
        shutil.rmtree(d)


def saved_runs() -> tuple[str, ...]:
    """Прогоны, у которых на диске ЕСТЬ кадры. Порядок — от старых к свежим по правке.

    Нужна проверке владельца (§7.5). Она сама кадров не пишет — их пишет `hunter run`, —
    поэтому спрашивать «сколько кадров записал этот прогон» ей нечего: ответ всегда ноль.
    Правильный вопрос другой: «есть ли на диске то, что можно повторить», и отвечает на
    него состояние каталога, а не текущий сбор.
    """
    if not FRAMES_DIR.is_dir():
        return ()
    runs = [d for d in FRAMES_DIR.iterdir()
            if d.is_dir() and any(d.rglob("*.parquet"))]
    return tuple(d.name for d in sorted(runs, key=lambda p: p.stat().st_mtime))


def saved_symbols(run_id: str) -> tuple[str, ...]:
    """Символы, у которых в прогоне есть кадры. Порядок — алфавитный, для дет-повтора."""
    root = FRAMES_DIR / run_id
    if not root.is_dir():
        return ()
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir()))


def saved_timeframes(run_id: str, symbol: str) -> tuple[str, ...]:
    d = FRAMES_DIR / run_id / _safe(symbol)
    if not d.is_dir():
        return ()
    skip = {"profile", "trades_by_bar"}
    return tuple(sorted(p.stem for p in d.glob("*.parquet") if p.stem not in skip))


# --- леджер (§10.2) -----------------------------------------------------------

def open_readonly(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение только на чтение. Умолчание для всего, кроме боевой эмиссии."""
    if not path.exists():
        raise FileNotFoundError(f"нет базы {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _tune(conn: sqlite3.Connection) -> None:
    """Настройки соединения, без которых объявленные схемой гарантии не работают.

    `foreign_keys` в SQLite выключены ПО УМОЛЧАНИЮ и включаются в КАЖДОМ соединении.
    Прогон 2026-08-04 до этой строки: `PRAGMA foreign_keys = 0`, и строка исхода на
    несуществующий сигнал 999999 записалась. §10.2 обещает «записать бессмысленную строку
    нельзя» — обещание держала только схема на бумаге.

    `journal_mode=WAL` и `busy_timeout` — для того, что §8 называет целью: процесс,
    живущий сутками, и владелец, открывающий `hunter ledger` из другого окна. Без них
    второе соединение получает `database is locked` вместо ответа.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def _schema_version(conn: sqlite3.Connection) -> str:
    """Версия схемы существующей базы. `1` — база до появления `recorded_at`."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if not cols:
        return SCHEMA_VERSION  # базы ещё нет: создастся сразу свежей
    if "recorded_at" not in cols:
        return "1"
    if "kind" not in cols:
        return "2"
    # 3 → 4 отличается не колонкой `signals`, а НАЛИЧИЕМ таблицы состояний: сама
    # `signals` при этом переходе не меняется.
    has_states = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_states'"
    ).fetchone()
    if not has_states:
        return "3"
    return SCHEMA_VERSION if "target" in cols else "4"


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Отложить строки версии 1 в сторону и завести таблицы заново.

    Строки НЕ удаляются: они настоящие в том смысле, что их произвёл настоящий код на
    настоящих барах, — но отвечают на другой вопрос («как отработала бы история»), и
    сложить их с журналом живых сигналов в одном `AVG(r)` значит повторить инцидент
    §10.2 с обратным знаком. Поэтому они переезжают в `*_backtest_v1` и остаются
    доступны запросом, а `OWNER_QUERIES` их больше не видит.
    """
    conn.executescript(
        "ALTER TABLE signals RENAME TO signals_backtest_v1;"
        "ALTER TABLE outcomes RENAME TO outcomes_backtest_v1;"
    )
    conn.commit()


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """Достроить `kind` перестройкой таблицы: SQLite не расширяет UNIQUE на месте.

    Строки сохраняются все и получают `kind = 'level'` — до версии 3 других типов не
    существовало. `id` переносятся как есть: на них смотрит `outcomes.signal_id`, и
    ровно поэтому внешние ключи на время перестройки выключаются (штатный порядок
    миграций SQLite: DROP старой таблицы при включённых FK отклоняется, хотя новая
    встаёт на её место тем же именем и теми же id).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        "BEGIN;"
        "CREATE TABLE signals_v3 ("
        " id INTEGER PRIMARY KEY,"
        " kind TEXT NOT NULL DEFAULT 'level' CHECK (kind IN ('level', 'pp')),"
        " symbol TEXT NOT NULL,"
        " timeframe TEXT NOT NULL,"
        " direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),"
        " opened_at INTEGER NOT NULL,"
        " recorded_at INTEGER NOT NULL,"
        " entry REAL NOT NULL,"
        " stop REAL NOT NULL,"
        " frames_ref TEXT NOT NULL,"
        " CHECK (stop != entry), CHECK (entry > 0), CHECK (stop > 0),"
        " CHECK (recorded_at > 0),"
        " UNIQUE (kind, symbol, timeframe, direction, opened_at));"
        "INSERT INTO signals_v3 (id, kind, symbol, timeframe, direction, opened_at,"
        " recorded_at, entry, stop, frames_ref)"
        " SELECT id, 'level', symbol, timeframe, direction, opened_at,"
        " recorded_at, entry, stop, frames_ref FROM signals;"
        "DROP TABLE signals;"
        "ALTER TABLE signals_v3 RENAME TO signals;"
        "COMMIT;"
    )
    conn.execute("PRAGMA foreign_keys = ON")


def open_production_ledger(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение на запись. ЕДИНСТВЕННАЯ точка записи в боевую базу (§10.2).

    Список того, кому это разрешено, держит gates/production_writer.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    _tune(conn)
    if _schema_version(conn) == "1":
        _migrate_1_to_2(conn)
    if _schema_version(conn) == "2":
        _migrate_2_to_3(conn)
    # 3 → 4 отдельной функции не требует: переход добавляет ПУСТУЮ таблицу, а
    # `CREATE TABLE IF NOT EXISTS` ниже её и создаёт. Перестройка (как в 2 → 3) нужна
    # только там, где меняется смысл существующих строк — здесь не меняется ни одной.
    if _schema_version(conn) == "4":
        # 4 → 5: колонка добавляется НА МЕСТЕ. Перестройка не нужна — `UNIQUE` не
        # трогается, а `ALTER TABLE ADD COLUMN` в SQLite дёшев и не переписывает строк.
        conn.execute("ALTER TABLE signals ADD COLUMN target REAL")
        conn.commit()
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def init_ledger(path: Path = LEDGER_PATH) -> Path:
    conn = open_production_ledger(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('foundation', ?)",
            ("§10.2",),
        )
        conn.commit()
    finally:
        conn.close()
    return path


class SignalRow(BaseModel):
    """Строка сигнала: её номер и МОМЕНТ, когда система о сигнале узнала.

    `recorded_at` возвращается наружу, потому что от него зависит, по каким барам вообще
    можно считать исход: по тем, что закрылись позже. Взять его «сейчас» у вызывающего
    нельзя — для давно известного сигнала это будет не тот момент.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    recorded_at: int
    fresh: bool
    """True — строку завёл этот вызов. False — сигнал был записан раньше."""


# §10.6 условие 1: «Владелец может проверить состояние леджера тремя заготовленными
# SQL-запросами, не читая код». Вот эти три.
def record_signal(
    conn: sqlite3.Connection, symbol: str, timeframe: str, direction: str,
    opened_at: int, entry: Decimal, stop: Decimal, frames_ref: str, recorded_at: int,
    kind: str = "level", target: Decimal | None = None,
) -> SignalRow | NotReady:
    """Записать сигнал ИЛИ вернуть уже записанный. ЕДИНСТВЕННЫЙ писатель сигналов (§10.2, §6).

    Соединение обязано быть боевым: read-only СУБД отклонит запись сама. Строка,
    нарушающая схему, — названный отказ, а не падение процесса.

    ⚠ Повторный вызов по тому же сигналу больше НЕ отказ. Прежняя редакция возвращала
    на нём `NotReady`, вызывающий писал «сигнал не записан» и шёл дальше — а вместе с
    сигналом пропускался и его ИСХОД. То есть исход мог быть дописан ровно один раз, в
    том прогоне, где сигнал появился, и считался он тогда по уже известной истории.
    Именно из этой пары и получался бэктест под именем журнала.
    """
    key = (kind, symbol, timeframe, direction, opened_at)
    row = conn.execute(
        "SELECT id, recorded_at FROM signals WHERE kind=? AND symbol=? AND timeframe=?"
        " AND direction=? AND opened_at=?", key,
    ).fetchone()
    if row is not None:
        return SignalRow(id=int(row[0]), recorded_at=int(row[1]), fresh=False)
    try:
        cur = conn.execute(
            "INSERT INTO signals (kind, symbol, timeframe, direction, opened_at,"
            " recorded_at, entry, stop, target, frames_ref)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, symbol, timeframe, direction, opened_at, recorded_at,
             float(entry), float(stop),
             None if target is None else float(target), frames_ref),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"{symbol} {opened_at}: строка отклонена схемой — {e}")
    if cur.lastrowid is None:
        return NotReady(reason=f"{symbol} {opened_at}: СУБД не вернула идентификатор строки")
    return SignalRow(id=cur.lastrowid, recorded_at=recorded_at, fresh=True)


def record_outcome(
    conn: sqlite3.Connection, signal_id: int, kind: str, closed_at: int,
    exit_price: Decimal | None, r: float | None,
) -> NotReady | None:
    """Записать исход. Открытые и несостоявшиеся сделки сюда НЕ пишутся (§4.3)."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO outcomes (signal_id, kind, closed_at, exit_price, r)"
            " VALUES (?, ?, ?, ?, ?)",
            (signal_id, kind, closed_at,
             None if exit_price is None else float(exit_price), r),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"исход {signal_id}: строка отклонена схемой — {e}")
    return None


class PendingSignal(BaseModel):
    """Сигнал БЕЗ исхода, который надо дорешать по барам. Схема v5, 2026-08-10.

    Заведён вместе с проходом дорешивания: до него исход считался только у сигналов,
    эмитируемых заново, и две трети журнала не получали ответа никогда (см.
    `SCHEMA_VERSION`, переход 4 → 5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    symbol: str
    timeframe: str
    direction: str
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    """`None` — сигнал записан до схемы v5: цель не сохранялась. Такой не дорешать, и
    считать его «без объяснения» нельзя — причина известна и названа."""

    recorded_at: int


def pending_signals(
    conn: sqlite3.Connection, symbol: str | None = None
) -> tuple[PendingSignal, ...]:
    """Сигналы без исхода — все либо по одному символу. Только чтение.

    Состояние (`not_filled`/`open`) наличие здесь НЕ отменяет: оно временное и на
    следующих барах может смениться исходом. Отменяет только сам исход.
    """
    sql = ("SELECT s.id, s.symbol, s.timeframe, s.direction, s.entry, s.stop,"
           " s.target, s.recorded_at FROM signals s"
           " LEFT JOIN outcomes o ON o.signal_id = s.id"
           " WHERE o.signal_id IS NULL")
    args: tuple[str, ...] = ()
    if symbol is not None:
        sql += " AND s.symbol = ?"
        args = (symbol,)
    rows = conn.execute(sql + " ORDER BY s.id", args).fetchall()
    return tuple(
        PendingSignal(
            id=int(r[0]), symbol=r[1], timeframe=r[2], direction=r[3],
            entry=Decimal(str(r[4])), stop=Decimal(str(r[5])),
            target=None if r[6] is None else Decimal(str(r[6])),
            recorded_at=int(r[7]),
        )
        for r in rows
    )


def record_signal_state(
    conn: sqlite3.Connection, signal_id: int, state: str, as_of: int
) -> NotReady | None:
    """Записать СОСТОЯНИЕ незакрытой сделки: `not_filled` или `open` (v4, 2026-08-10).

    Перезаписывается каждым прогоном — состояние сегодняшнее, а не событие. Сделка,
    ставшая исходом, состояние теряет: `outcomes` старше по определению, и держать оба
    ответа значило бы завести две правды об одной сделке.

    Зачем это в леджере, а не в отчёте прогона. `not_filled` — ЗАКОННЫЙ ответ системы
    (цена до входа не дошла; вход стоит лимитками на ПОК и зону, стр. 30), и на вопрос
    "сколько раз совет не сработал" он отвечает иначе, чем `stop`. Пока ответ жил
    счётчиком одного прогона, клетка сводки "сигналов 6, исходов 0" была неотличима от
    дефекта расчёта.
    """
    try:
        conn.execute(
            "INSERT OR REPLACE INTO signal_states (signal_id, state, as_of)"
            " VALUES (?, ?, ?)", (signal_id, state, as_of),
        )
        conn.execute("DELETE FROM signal_states WHERE signal_id IN"
                     " (SELECT signal_id FROM outcomes)")
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"состояние {signal_id}: строка отклонена схемой — {e}")
    return None


class MapSync(BaseModel):
    """Итог слияния карты. Отказы — ЧИСЛО и причина, а не исключение наверх.

    До 2026-08-04 `sync_levels` не был обёрнут ни во что, в отличие от `record_signal` и
    `record_outcome`, и `sqlite3.IntegrityError` из него валил ВЕСЬ прогон — уже после
    сбора данных, бэкфилла и печати карточек. Вернуть отказ числом дешевле, чем потерять
    прогон, и §4.3 требует именно этого: пропуск виден, а не замалчивается и не взрывается.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    added: int
    updated: int
    retired: int
    rejected: tuple[str, ...]


def sync_levels(
    conn: sqlite3.Connection,
    symbol: str,
    seen: list[tuple[Level, LevelState]],
    now_ms: int,
) -> MapSync:
    """Слить свежепосчитанную карту с накопленной.

    Зачем накопление, а не пересборка каждый прогон — ЗАМЕР, а не удобство.
    `scripts/probes.py map-drift`: при сдвиге окна на 200 баров 2% карты BTC и 3% ETH
    исчезали не потому, что уровень отработан (стр. 25 — единственная причина, которую
    знает курс), а потому что структура уехала за край окна в 1000 баров. Уровень при
    этом оставался активным. Автор говорит ровно обратное: «зона остаётся актуальна».

    Что делает функция:
      * НОВЫЙ уровень (окна структуры ещё нет в таблице) — вставляется;
      * ВИДИМЫЙ снова — обновляется `last_seen` и состояние;
      * ПРОПАВШИЙ из расчёта — НЕ трогается: он остаётся в карте с прежним состоянием.
        Именно это и есть накопление; отсутствие в текущем окне не является событием.

    Снятие происходит ТОЛЬКО по состоянию из курса — `worked_off` или `flipped`, — и
    тогда проставляется `retired_at`. Ни одна строка не удаляется: история карты нужна,
    чтобы можно было спросить, когда уровень появился и когда перестал торговаться.

    ⚠ `retired_at` СНИМАЕТСЯ при возврате состояния в `active`. Прежняя редакция писала
    `COALESCE(retired_at, ?)`, то есть однажды проставленную дату не убирала никогда, —
    и такой `UPDATE` падал на `CHECK ((retired_at IS NULL) = (state = 'active'))`,
    роняя прогон целиком. Механизм, которым возврат в `active` объяснялся в первом
    разборе («бар прокола ушёл за левый край окна»), замером ОПРОВЕРГНУТ: 158 общих окон,
    возвратов ноль. Остающийся правдоподобный путь — сдвиг ПОК при доливке архива, из-за
    которого событие считается по новой цене. Он не замерен, и именно поэтому строка
    чинится: падать на неизученном пути хуже, чем пережить его и сообщить числом.
    """
    added = updated = retired = 0
    rejected: list[str] = []
    for lvl, state in seen:
        key = (symbol, lvl.timeframe, lvl.structure_from_ms, lvl.structure_to_ms)
        row = conn.execute(
            "SELECT state, retired_at FROM levels WHERE symbol=? AND timeframe=? AND"
            " from_ms=? AND to_ms=?", key,
        ).fetchone()
        active = state is LevelState.ACTIVE
        try:
            if row is None:
                conn.execute(
                    "INSERT INTO levels (symbol, timeframe, side, price, zone_lo, zone_hi,"
                    " boundary_lo, boundary_hi, volume, from_ms, to_ms, first_seen,"
                    " last_seen, state, retired_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (symbol, lvl.timeframe, lvl.side.value, float(lvl.price),
                     float(lvl.zone_lo), float(lvl.zone_hi), float(lvl.boundary_lo),
                     float(lvl.boundary_hi), lvl.structure_volume, lvl.structure_from_ms,
                     lvl.structure_to_ms, now_ms, now_ms, state.value,
                     None if active else now_ms),
                )
                added += 1
                retired += not active
                continue
            was_active = row[0] == LevelState.ACTIVE.value
            # Дата снятия либо снимается вовсе (уровень снова активен), либо
            # сохраняет ПЕРВОЕ снятие: «когда перестал торговаться» — это первый раз.
            retired_at = None if active else (row[1] if row[1] is not None else now_ms)
            conn.execute(
                "UPDATE levels SET last_seen=?, price=?, zone_lo=?, zone_hi=?, state=?,"
                " retired_at=? WHERE symbol=? AND timeframe=? AND from_ms=? AND to_ms=?",
                (now_ms, float(lvl.price), float(lvl.zone_lo), float(lvl.zone_hi),
                 state.value, retired_at, *key),
            )
            updated += 1
            retired += was_active and not active
        except sqlite3.IntegrityError as e:
            rejected.append(f"{symbol} {lvl.timeframe} [{lvl.structure_from_ms}]: {e}")
    conn.commit()
    return MapSync(added=added, updated=updated, retired=retired,
                   rejected=tuple(rejected))


class CarriedLevel(BaseModel):
    """Уровень, перенесённый из прошлого прогона: посчитать заново его не удалось.

    Отдельный тип, а не `Level`: у `Level` есть поля, которых здесь взять неоткуда
    (индексы баров в ряду, которого в этом прогоне нет). Отдать `Level` с выдуманными
    индексами значило бы сфабриковать данные (§4.3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    side: str
    price: Decimal
    zone_lo: Decimal
    zone_hi: Decimal
    from_ms: int
    to_ms: int
    first_seen: int
    last_seen: int


def carried_levels(
    conn: sqlite3.Connection, symbol: str, now_ms: int
) -> tuple[CarriedLevel, ...]:
    """Активные уровни карты, которых в ЭТОМ прогоне посчитать не удалось.

    «Не удалось» определяется вызывающим по `last_seen`: строки, чей `last_seen` старше
    текущего прогона, — это и есть перенесённые. Возвращаются как есть, без пересчёта:
    ПОК у них старый, и выдавать его за свежий нельзя.
    """
    rows = conn.execute(
        "SELECT timeframe, side, price, zone_lo, zone_hi, from_ms, to_ms, first_seen,"
        " last_seen FROM levels WHERE symbol=? AND state='active' AND last_seen < ?"
        " ORDER BY price", (symbol, now_ms),
    ).fetchall()
    return tuple(
        CarriedLevel(
            timeframe=r[0], side=r[1], price=Decimal(str(r[2])),
            zone_lo=Decimal(str(r[3])), zone_hi=Decimal(str(r[4])),
            from_ms=r[5], to_ms=r[6], first_seen=r[7], last_seen=r[8],
        )
        for r in rows
    )


class OutcomeCell(BaseModel):
    """Исходы одной клетки разреза: ТФ × сторона × тип сигнала.

    ⚠ Здесь ЧЕТЫРЕ знаменателя, и все обязательны. Урок backfill-window (2026-08-04):
    сто девяносто честных отказов подряд читались как «рынок такой», пока их не сложили
    ПО ТАЙМФРЕЙМУ — и оказалось, что 4ч и 1Д не дали НИ ОДНОГО уровня. То же самое
    возможно с исходами: «средний R = +0.4» по всему леджеру молчит о том, что весь плюс
    сделан одним ТФ, а другой стабильно минусовой.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    """Тип сигнала: level | pp."""

    timeframe: str
    direction: str

    signals: int
    """Сколько сигналов эмитировано. ГЛАВНЫЙ знаменатель."""

    closed: int
    """Сколько получило исход. `signals - closed` — ещё открытые или не наполненные:
    они в средний R не входят, и без этого числа доля выигрышей посчитана по другому
    множеству, чем «сколько раз система советовала»."""

    by_target: int
    by_stop: int
    ambiguous: int
    """Неоднозначные (стоп и цель в одном баре) — у них `r` нет по схеме, и в сумму R
    они не идут. Считаются отдельно, а не растворяются в «не закрыто»."""

    sum_r: float | None
    avg_r: float | None
    """`None` — закрытых с числовым R нет вовсе. Не 0.0: ноль означал бы «в сумме
    вышли в ноль», а это другое утверждение (§4.3)."""

    not_filled: int
    """Сделок, где цена НЕ ДОШЛА до входа (стр. 30). Законный ответ системы, а не
    отсутствие ответа: исхода у них не будет никогда, и в знаменателе «средний R» им
    не место."""

    still_open: int
    """Сделок, где вход состоялся, а стоп и цель ещё не достигнуты."""

    unknown_state: int
    """Сигналов БЕЗ исхода и БЕЗ записанного состояния. Ровно они и есть подозрение на
    дефект: система про них не сказала ничего. Сюда же попадают сигналы, записанные до
    появления схемы v4, — и это видно по возрасту."""

    age_bars_max: int
    """Сколько баров СВОЕГО ТФ прожил самый старый сигнал клетки к последнему моменту,
    о котором знает леджер.

    ⚠ Поле заведено вместе со сводкой и ровно затем, чтобы клетку без исходов нельзя
    было объяснить словами. Пустая клетка допускает две причины: «сигналы молоды —
    закрыться не успели» и «исход не считается вовсе». Различает их ТОЛЬКО возраст:
    ноль исходов при возрасте в три бара — данные, ноль исходов при возрасте в двести
    баров — дефект. Правило CLAUDE.md: ноль с красивой причиной опаснее всего, потому
    что не выглядит дефектом.
    """


class OutcomeSurvey(BaseModel):
    """Сводка исходов ПО ИЗМЕРЕНИЯМ, вдоль которых возможен систематический перекос.

    Заведено 2026-08-10. До этого исходы писались и складывались ТОЛЬКО целиком по
    леджеру (`OWNER_QUERIES['результат в R']`) плюс разрез по ТФ без стороны и без типа
    сигнала. Перекос вида «весь плюс сделан лонгами на 1ч, а ПП-сигналы стабильно
    минусовые» не был виден ничем.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: tuple[OutcomeCell, ...]
    signals_total: int
    closed_total: int
    fingerprint: str
    """ОТПЕЧАТОК ДАННЫХ: состав леджера на момент замера. Правило 2026-08-09 — замер по
    растущему хранилищу без отпечатка воспроизводится только до первой доливки."""


def outcome_survey(conn: sqlite3.Connection) -> OutcomeSurvey:
    """Исходы в разрезе тип × ТФ × сторона. Соединение — только на чтение.

    Клетки БЕЗ единого исхода тоже попадают в сводку (`closed = 0`): именно они и есть
    признак перекоса, а фильтрация по `INNER JOIN outcomes` их бы молча съела.
    """
    # «Последний момент, о котором знает леджер» — самая свежая запись. Часы здесь
    # не спрашиваются намеренно: сводка читается и из процессов без сведения с биржей
    # (§6 запрещает судить о времени по локальным часам), а для возраста в барах
    # достаточно внутренней шкалы самого леджера.
    horizon = conn.execute(
        "SELECT MAX(m) FROM (SELECT MAX(recorded_at) AS m FROM signals"
        " UNION ALL SELECT MAX(closed_at) FROM outcomes)"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT s.kind, s.timeframe, s.direction, COUNT(*) AS signals,"
        " SUM(CASE WHEN o.signal_id IS NOT NULL THEN 1 ELSE 0 END) AS closed,"
        " SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END) AS by_target,"
        " SUM(CASE WHEN o.kind='stop' THEN 1 ELSE 0 END) AS by_stop,"
        " SUM(CASE WHEN o.kind='ambiguous' THEN 1 ELSE 0 END) AS ambiguous,"
        " SUM(o.r) AS sum_r, AVG(o.r) AS avg_r, MIN(s.recorded_at) AS oldest,"
        " SUM(CASE WHEN st.state='not_filled' THEN 1 ELSE 0 END) AS not_filled,"
        " SUM(CASE WHEN st.state='open' THEN 1 ELSE 0 END) AS still_open"
        " FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id"
        " LEFT JOIN signal_states st ON st.signal_id = s.id"
        " GROUP BY s.kind, s.timeframe, s.direction"
        " ORDER BY s.kind, s.timeframe, s.direction"
    ).fetchall()
    cells = tuple(
        OutcomeCell(
            kind=r[0], timeframe=r[1], direction=r[2], signals=int(r[3]),
            closed=int(r[4]), by_target=int(r[5]), by_stop=int(r[6]),
            ambiguous=int(r[7]),
            sum_r=None if r[8] is None else float(r[8]),
            avg_r=None if r[9] is None else float(r[9]),
            not_filled=int(r[11]), still_open=int(r[12]),
            unknown_state=int(r[3]) - int(r[4]) - int(r[11]) - int(r[12]),
            age_bars_max=(0 if horizon is None or r[10] is None
                          else max(0, (int(horizon) - int(r[10])) // tf_ms(r[1]))),
        )
        for r in rows
    )
    n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_closed = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    n_levels = conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0]
    return OutcomeSurvey(
        cells=cells, signals_total=int(n_signals), closed_total=int(n_closed),
        fingerprint=f"сигналов {n_signals}, исходов {n_closed}, уровней карты {n_levels}",
    )


def format_outcome_survey(s: OutcomeSurvey) -> list[str]:
    """Сводка словами для владельца (§7.6). Отдаёт строки, печать — дело вызывающего.

    Перекос называется ЯВНО, а не оставляется читателю: клетки без исходов и клетки,
    где все закрытия одного знака, перечисляются отдельной строкой. Один отказ — данные;
    все отказы на одном ТФ — дефект (правило CLAUDE.md).
    """
    out = [f"ОТПЕЧАТОК ДАННЫХ: {s.fingerprint}"]
    if not s.cells:
        out.append("   сигналов нет — сводить нечего (это данные, а не отказ)")
        return out
    out.append(f"   всего сигналов {s.signals_total}, с исходом {s.closed_total}, "
               f"без исхода {s.signals_total - s.closed_total}")
    out.append("   тип   ТФ    сторона  сигн.  закр.  цель  стоп  неодн.  средний R  "
               "мимо  идёт  ?  возраст")
    for c in s.cells:
        avg = "     —" if c.avg_r is None else f"{c.avg_r:6.3f}"
        out.append(f"   {c.kind:5} {c.timeframe:5} {c.direction:8} {c.signals:5} "
                   f"{c.closed:6} {c.by_target:5} {c.by_stop:5} {c.ambiguous:7} {avg}  "
                   f"{c.not_filled:4} {c.still_open:5} {c.unknown_state:2} "
                   f"{c.age_bars_max:8}")
    out.append("   «мимо» — цена не дошла до входа (стр. 30); «идёт» — вход был, "
               "исхода ещё нет; «?» — система не сказала ничего")

    silent = [c for c in s.cells if c.closed == 0]
    if silent:
        out.append(f"   ⚠ клеток БЕЗ единого исхода: {len(silent)} — "
                   + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction} ({c.signals} сигн.,"
                               f" {c.age_bars_max} бар.)" for c in silent[:6])
                   + (f" и ещё {len(silent) - 6}" if len(silent) > 6 else ""))
        # Возраст и состояние РАЗЛИЧАЮТ три причины пустоты, и потому названы все три,
        # а не одна правдоподобная. Порог — один бар: раньше него исход невозможен по
        # построению (`outcome.resolve` смотрит только бары ПОСЛЕ `recorded_at`).
        young = [c for c in silent if c.age_bars_max <= 1]
        explained = [c for c in silent
                     if c.age_bars_max > 1 and c.unknown_state == 0]
        aged = [c for c in silent if c.age_bars_max > 1 and c.unknown_state > 0]
        if young:
            out.append(f"     МОЛОДЫЕ (≤1 бара своего ТФ): {len(young)} — исход "
                       "невозможен по построению, это данные")
        if explained:
            out.append(f"     ОБЪЯСНЁННЫЕ состоянием: {len(explained)} — "
                       + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                                   f" (мимо {c.not_filled}, идёт {c.still_open})"
                                   for c in explained[:6])
                       + "; система ответила, просто ответ не «стоп/цель»")
        if aged:
            out.append(f"     ⚠ БЕЗ ОБЪЯСНЕНИЯ: {len(aged)} — "
                       + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                                   f" ({c.unknown_state} сигн., {c.age_bars_max} бар.)"
                                   for c in aged[:6])
                       + "; ноль исходов при прожитых барах и без записанного состояния "
                         "объяснения не имеет и требует разбора по шагам "
                         "(backfill-window-2026-08-04)")
    one_sided = [c for c in s.cells
                 if c.closed >= 3 and (c.by_target == 0 or c.by_stop == 0)]
    if one_sided:
        out.append(f"   ⚠ клеток, где ВСЕ закрытия одного знака (≥3): {len(one_sided)} — "
                   + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                               for c in one_sided[:6]))
    return out


OWNER_QUERIES: dict[str, str] = {
    "сколько сделок": "SELECT COUNT(*) AS всего FROM signals;",
    "результат в R (стр. 9 курса)": (
        "SELECT (SELECT COUNT(*) FROM signals) AS всего_советов, "
        "COUNT(*) AS из_них_закрыто, "
        "(SELECT COUNT(*) FROM signals) - COUNT(*) AS ещё_без_исхода, "
        # ⚠ Подвыборка называется ЗДЕСЬ, а не в чьей-то голове: 2026-08-10 выяснилось,
        # что «средний R» считался по трети журнала — исход досчитывался лишь у
        # сигналов, чей уровень система ещё отбирает. Три столбца ниже показывают, из
        # чего состоит остаток, чтобы «ещё_без_исхода» не читалось как «скоро закроются».
        "(SELECT COUNT(*) FROM signal_states WHERE state='not_filled') AS мимо_входа, "
        "(SELECT COUNT(*) FROM signal_states WHERE state='open') AS сделка_идёт, "
        "(SELECT COUNT(*) FROM signals WHERE target IS NULL) AS без_цели_не_дорешать, "
        "SUM(CASE WHEN kind='target' THEN 1 ELSE 0 END) AS по_цели, "
        "SUM(CASE WHEN kind='stop' THEN 1 ELSE 0 END) AS по_стопу, "
        "SUM(CASE WHEN kind='ambiguous' THEN 1 ELSE 0 END) AS неоднозначно, "
        "ROUND(SUM(COALESCE(r,0)), 3) AS сумма_R, "
        "ROUND(AVG(r), 3) AS средний_R "
        "FROM outcomes;"
    ),
    # ⚠ `всего_советов` стоит ПЕРВЫМ столбцом умышленно. Прежняя редакция печатала
    # «закрыто N, средний_R X» и умалчивала знаменатель: сделки, до которых цена не
    # дошла, и сделки, ещё не закрывшиеся, в таблицу исходов не попадают вовсе. Замер
    # 2026-08-04: половина эмиссий не заполнялась, и владелец видел долю выигрышей,
    # посчитанную по другому множеству, чем «сколько раз система советовала».
    "чем куплены выигрыши — геометрия сделок": (
        "SELECT s.timeframe AS тф, COUNT(*) AS сделок, "
        "ROUND(AVG(ABS(s.entry - s.stop) / s.entry * 100), 3) AS средняя_дистанция_стопа_проц, "
        "ROUND(AVG(o.r), 3) AS средний_R, "
        "ROUND(AVG(s.recorded_at - s.opened_at) / 3600000.0, 1) AS часов_от_уровня_до_записи "
        "FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id "
        "GROUP BY s.timeframe ORDER BY сделок DESC;"
    ),
    "какие символы": (
        "SELECT symbol AS символ, COUNT(*) AS сделок, "
        "MIN(opened_at) AS первая, MAX(opened_at) AS последняя "
        "FROM signals GROUP BY symbol ORDER BY сделок DESC;"
    ),
    "подозрительная геометрия": (
        "SELECT id, symbol AS символ, direction AS сторона, entry AS вход, stop AS стоп, "
        "ROUND(ABS(entry - stop) / entry * 100, 4) AS дистанция_стопа_проц "
        "FROM signals "
        "WHERE (direction = 'long'  AND stop >= entry) "
        "   OR (direction = 'short' AND stop <= entry) "
        "   OR ABS(entry - stop) / entry < 0.0005 "
        "   OR ABS(entry - stop) / entry > 0.5 "
        "ORDER BY дистанция_стопа_проц;"
    ),
}
