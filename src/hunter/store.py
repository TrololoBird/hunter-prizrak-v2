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
import sqlite3
from decimal import Decimal
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ConfigDict

from .levels import Level, LevelState
from .models import Bar, BarBinnedTrades, NotReady, TradeHistogram

DATA_DIR = Path("data")
FRAMES_DIR = DATA_DIR / "frames"
LEDGER_PATH = DATA_DIR / "ledger.sqlite3"

# §10.2 задаёт ограничения дословно: NOT NULL на цене входа, CHECK (stop != entry),
# UNIQUE (symbol, opened_at). Остальные поля появятся на этапах 5–7 вместе с тем,
# что их производит; поля без продюсера здесь заводить нельзя (§0).
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('long', 'short')),
    opened_at   INTEGER NOT NULL,
    entry       REAL    NOT NULL,
    stop        REAL    NOT NULL,
    frames_ref  TEXT    NOT NULL,
    CHECK (stop != entry),
    CHECK (entry > 0),
    CHECK (stop > 0),
    UNIQUE (symbol, opened_at)
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


def open_production_ledger(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение на запись. ЕДИНСТВЕННАЯ точка записи в боевую базу (§10.2).

    Список того, кому это разрешено, держит gates/production_writer.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
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


# §10.6 условие 1: «Владелец может проверить состояние леджера тремя заготовленными
# SQL-запросами, не читая код». Вот эти три.
def record_signal(
    conn: sqlite3.Connection, symbol: str, timeframe: str, direction: str,
    opened_at: int, entry: Decimal, stop: Decimal, frames_ref: str,
) -> int | NotReady:
    """Записать сигнал. ЕДИНСТВЕННЫЙ писатель сигналов (§10.2, §6).

    Соединение обязано быть боевым: у read-only СУБД сама отклонит запись. Повтор по
    ключу (символ, время) — не ошибка выполнения, а названный отказ: гейт §10.2 требует,
    чтобы бессмысленную строку записать было НЕЛЬЗЯ, а не чтобы процесс падал.
    """
    try:
        cur = conn.execute(
            "INSERT INTO signals (symbol, timeframe, direction, opened_at, entry, stop,"
            " frames_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, timeframe, direction, opened_at, float(entry), float(stop), frames_ref),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"{symbol} {opened_at}: строка отклонена схемой — {e}")
    if cur.lastrowid is None:
        return NotReady(reason=f"{symbol} {opened_at}: СУБД не вернула идентификатор строки")
    return cur.lastrowid


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


def sync_levels(
    conn: sqlite3.Connection,
    symbol: str,
    seen: list[tuple[Level, LevelState]],
    now_ms: int,
) -> tuple[int, int, int]:
    """Слить свежепосчитанную карту с накопленной. Возвращает (новых, обновлено, снято).

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
    """
    added = updated = retired = 0
    for lvl, state in seen:
        key = (symbol, lvl.timeframe, lvl.structure_from_ms, lvl.structure_to_ms)
        row = conn.execute(
            "SELECT state FROM levels WHERE symbol=? AND timeframe=? AND from_ms=?"
            " AND to_ms=?", key,
        ).fetchone()
        retired_at = None if state is LevelState.ACTIVE else now_ms
        if row is None:
            conn.execute(
                "INSERT INTO levels (symbol, timeframe, side, price, zone_lo, zone_hi,"
                " boundary_lo, boundary_hi, volume, from_ms, to_ms, first_seen, last_seen,"
                " state, retired_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, lvl.timeframe, lvl.side.value, float(lvl.price),
                 float(lvl.zone_lo), float(lvl.zone_hi), float(lvl.boundary_lo),
                 float(lvl.boundary_hi), lvl.structure_volume, lvl.structure_from_ms,
                 lvl.structure_to_ms, now_ms, now_ms, state.value, retired_at),
            )
            added += 1
            retired += state is not LevelState.ACTIVE
            continue
        was_active = row[0] == LevelState.ACTIVE.value
        conn.execute(
            "UPDATE levels SET last_seen=?, price=?, zone_lo=?, zone_hi=?, state=?,"
            " retired_at=COALESCE(retired_at, ?) WHERE symbol=? AND timeframe=?"
            " AND from_ms=? AND to_ms=?",
            (now_ms, float(lvl.price), float(lvl.zone_lo), float(lvl.zone_hi),
             state.value, retired_at, *key),
        )
        updated += 1
        retired += was_active and state is not LevelState.ACTIVE
    conn.commit()
    return added, updated, retired


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
        "SELECT COUNT(*) AS закрыто, "
        "SUM(CASE WHEN kind='target' THEN 1 ELSE 0 END) AS по_цели, "
        "SUM(CASE WHEN kind='stop' THEN 1 ELSE 0 END) AS по_стопу, "
        "SUM(CASE WHEN kind='ambiguous' THEN 1 ELSE 0 END) AS неоднозначно, "
        "ROUND(SUM(COALESCE(r,0)), 3) AS сумма_R, "
        "ROUND(AVG(r), 3) AS средний_R "
        "FROM outcomes;"
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
