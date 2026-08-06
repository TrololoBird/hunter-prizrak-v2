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
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('long', 'short')),
    opened_at   INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    entry       REAL    NOT NULL,
    stop        REAL    NOT NULL,
    frames_ref  TEXT    NOT NULL,
    CHECK (stop != entry),
    CHECK (entry > 0),
    CHECK (stop > 0),
    CHECK (recorded_at > 0),
    UNIQUE (symbol, timeframe, direction, opened_at)
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

SCHEMA_VERSION = "2"
"""Версия схемы леджера. Растёт, когда прежние строки перестают означать то же самое.

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
    return SCHEMA_VERSION if "recorded_at" in cols else "1"


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


def open_production_ledger(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение на запись. ЕДИНСТВЕННАЯ точка записи в боевую базу (§10.2).

    Список того, кому это разрешено, держит gates/production_writer.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    _tune(conn)
    if _schema_version(conn) == "1":
        _migrate_1_to_2(conn)
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
    key = (symbol, timeframe, direction, opened_at)
    row = conn.execute(
        "SELECT id, recorded_at FROM signals WHERE symbol=? AND timeframe=? AND"
        " direction=? AND opened_at=?", key,
    ).fetchone()
    if row is not None:
        return SignalRow(id=int(row[0]), recorded_at=int(row[1]), fresh=False)
    try:
        cur = conn.execute(
            "INSERT INTO signals (symbol, timeframe, direction, opened_at, recorded_at,"
            " entry, stop, frames_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, timeframe, direction, opened_at, recorded_at,
             float(entry), float(stop), frames_ref),
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


OWNER_QUERIES: dict[str, str] = {
    "сколько сделок": "SELECT COUNT(*) AS всего FROM signals;",
    "результат в R (стр. 9 курса)": (
        "SELECT (SELECT COUNT(*) FROM signals) AS всего_советов, "
        "COUNT(*) AS из_них_закрыто, "
        "(SELECT COUNT(*) FROM signals) - COUNT(*) AS ещё_без_исхода, "
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
