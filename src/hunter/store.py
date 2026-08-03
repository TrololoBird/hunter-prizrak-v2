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
    run_id: str, symbol: str, tick_size: Decimal, bucket_ms: int
) -> BarBinnedTrades | NotReady:
    """Восстановить раскладку сделок. Отсутствие файла — названная причина, не пустота."""
    path = _binned_path(run_id, symbol)
    if not path.exists():
        return NotReady(reason=f"{symbol}: файла сделок нет — {path}")
    df = pl.read_parquet(path)
    t = BarBinnedTrades(symbol=symbol, tick_size=tick_size, bucket_ms=bucket_ms)
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
