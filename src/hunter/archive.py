"""Исторические сделки и бары из публичного архива Binance. FOUNDATION.md §8 этап 3.

Зачем архив, а не REST — ЗАМЕР, а не цитата.

⚠ Прежняя редакция этой строки утверждала, что `/fapi/v1/aggTrades` отдаёт сделки «не
старше 24 часов». **Это неверно, и проверено 2026-08-04:** запрос с `since` от 17.07
(восемнадцать суток назад) вернул 1000 сделок за 0.45 с. Утверждение было взято из
документации по памяти и ни разу не измерялось — тот самый класс дефектов, ради которого
проект переписывался.

Настоящая причина другая — ПРОПУСКНАЯ СПОСОБНОСТЬ. Замер на BTC: 1000 сделок в ответе
покрывают 51-110 секунд рынка, запрос идёт 0.45-1.69 с. Значит сутки сделок это около
1440 запросов и ~36 минут против нескольких секунд на один суточный ZIP.

Отсюда разделение, а не запрет:
  * широкое окно (сутки и больше) — архив;
  * узкое окно (минуты) — REST уместен и работает.

Целостность проверяется приложенным биржей .CHECKSUM: замер 2026-08-03 на
BTCUSDT-aggTrades-2026-08-01.zip — sha256 совпал.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import polars as pl

from .models import NotReady, TradeHistogram

BASE = "https://data.binance.vision/data/futures/um/daily"

# Колонки CSV архива, замер 2026-08-03 по заголовку файла.
AGG_COLUMNS = [
    "agg_trade_id", "price", "quantity",
    "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker",
]


@dataclass(frozen=True, slots=True)
class ArchiveDay:
    market_id: str
    day: date
    zip_bytes: int
    csv_bytes: int
    rows: int
    frame: pl.DataFrame


def agg_trades_url(market_id: str, day: date) -> str:
    return f"{BASE}/aggTrades/{market_id}/{market_id}-aggTrades-{day.isoformat()}.zip"


def _fetch(url: str, timeout: int) -> bytes | NotReady:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return bytes(r.read())
    except urllib.error.HTTPError as e:
        return NotReady(reason=f"{url}: HTTP {e.code}")
    except OSError as e:
        return NotReady(reason=f"{url}: {type(e).__name__} {e}")


def fetch_agg_trades_day(
    market_id: str, day: date, timeout: int = 180
) -> ArchiveDay | NotReady:
    """Скачать сутки сделок и проверить контрольную сумму биржи."""
    url = agg_trades_url(market_id, day)
    blob = _fetch(url, timeout)
    if isinstance(blob, NotReady):
        return blob

    checksum = _fetch(url + ".CHECKSUM", timeout)
    if isinstance(checksum, NotReady):
        return NotReady(reason=f"{market_id} {day}: нет .CHECKSUM — целостность не проверяема")
    expected = checksum.decode().split()[0]
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        return NotReady(reason=f"{market_id} {day}: sha256 не сошёлся ({actual} против {expected})")

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        csv = z.read(name)

    frame = pl.read_csv(io.BytesIO(csv), columns=["price", "quantity", "transact_time"])
    return ArchiveDay(
        market_id=market_id, day=day, zip_bytes=len(blob), csv_bytes=len(csv),
        rows=frame.height, frame=frame,
    )


def histogram_from_window(
    day_data: ArchiveDay, symbol: str, tick: Decimal, from_ms: int, to_ms: int
) -> TradeHistogram | NotReady:
    """Гистограмма по ОКНУ внутри суток: `from_ms <= transact_time < to_ms`.

    Нужна для §2.2: профиль натягивается ровно на структуру (стр. 26 — «важно захватить
    все свечи структуры»), а не на сутки. Окно, не покрытое сутками, — отказ, а не
    молчаливо усечённый профиль (§4.3).
    """
    lo, hi = int(day_data.frame["transact_time"].min()), int(  # type: ignore[arg-type]
        day_data.frame["transact_time"].max()  # type: ignore[arg-type]
    )
    if from_ms < lo or to_ms > hi + 1:
        return NotReady(
            reason=f"{symbol}: окно [{from_ms},{to_ms}) выходит за сутки архива [{lo},{hi}]"
        )
    window = day_data.frame.filter(
        (pl.col("transact_time") >= from_ms) & (pl.col("transact_time") < to_ms)
    )
    if window.height == 0:
        return NotReady(reason=f"{symbol}: в окне [{from_ms},{to_ms}) нет ни одной сделки")
    return _histogram(window, symbol, tick, window.height)


def histogram_from_day(day_data: ArchiveDay, symbol: str, tick: Decimal) -> TradeHistogram:
    """Свернуть сутки сделок в гистограмму цена→объём с шагом tickSize (§5)."""
    return _histogram(day_data.frame, symbol, tick, day_data.rows)


def _histogram(
    frame: pl.DataFrame, symbol: str, tick: Decimal, rows: int
) -> TradeHistogram:
    binned = frame.with_columns(
        (pl.col("price") / pl.lit(float(tick))).floor().cast(pl.Int64).alias("bin")
    ).group_by("bin").agg(
        pl.col("quantity").sum().alias("qty"),
        pl.len().alias("n"),
    )
    h = TradeHistogram(symbol=symbol, tick_size=tick)
    for row in binned.iter_rows(named=True):
        h.qty_by_bin[int(row["bin"])] = float(row["qty"])
        h.count_by_bin[int(row["bin"])] = int(row["n"])
    h.trades_seen = rows
    h.qty_seen = float(frame["quantity"].sum())
    h.first_ms = int(frame["transact_time"].min())  # type: ignore[arg-type]
    h.last_ms = int(frame["transact_time"].max())  # type: ignore[arg-type]
    return h
