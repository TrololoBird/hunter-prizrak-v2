"""Хранение: parquet для кадров, SQLite для леджера. FOUNDATION.md §10.2, §10.3.

Parquet — сырые кадры, без которых детерминированный повтор невозможен (§10.3).
SQLite — состояние и исходы, со схемой и ограничениями (§10.2).

Запись в боевую базу возможна ТОЛЬКО через `open_production_ledger`, и это ограничение
уровня СУБД, а не дисциплина: все прочие соединения открываются `mode=ro`, и попытка
записи через них падает с «attempt to write a readonly database». Кто зовёт
`open_production_ledger`, проверяет gates/production_writer.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import polars as pl

from .models import Bar, TradeHistogram

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

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
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
OWNER_QUERIES: dict[str, str] = {
    "сколько сделок": "SELECT COUNT(*) AS всего FROM signals;",
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
