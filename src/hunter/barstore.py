"""Хранилище закрытых баров на диске. Накапливается между прогонами.

⚠ ЗАЧЕМ. До 2026-08-11 бары не сохранялись НИГДЕ. Каждый прогон качал заново все ряды
вселенной — 27 символов × 6 ТФ, — а история глубже одного запроса не накапливалась
никогда, потому что её некуда было класть. Отсюда два следствия, оба молчаливых:

  * глубина ряда равнялась тому, что биржа отдаёт за раз, и не росла со временем;
  * один и тот же горизонт применялся к рядам, различающимся по глубине в тысячи раз
    (1000 баров это 3.5 суток на 5м и 19 лет на 1Н).

Кэш сделок (`archive.py`) накапливался с самого начала — бары не накапливались. Эта
асимметрия и есть то, что здесь закрывается.

⚠ ПЛОЩАДКА СТОИТ В ПУТИ, и это не предусмотрительность. У спота и USDⓈ-M ОДИНАКОВЫЕ
идентификаторы рынков: `BTC/USDT` и `BTC/USDT:USDT` оба дают `BTCUSDT` (проверено
вызовом 2026-08-11). Без площадки в пути смена `venue` в конфигурации молча смешала бы
бары двух разных рынков в одном файле.
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from . import log
from .bars import tf_ms
from .models import Bar

BARS_DIR = Path("data/bars")

BARS_LAYOUT = "b1"
"""Метка схемы файла. По той же причине, что `archive.CACHE_LAYOUT`: содержимое зависит
от того, КАК оно посчитано, и смена схемы обязана менять имя, а не молча переопределять
смысл прежних чисел. Файлы прежней метки не читаются и не удаляются."""

COLUMNS = ("open_ms", "open", "high", "low", "close", "volume")

SCHEMA: dict[str, type[pl.DataType]] = {
    "open_ms": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}


def store_path(venue: str, market_id: str, timeframe: str) -> Path:
    """Файл ряда. Всё, от чего зависит содержимое, стоит в пути."""
    return BARS_DIR / venue / f"{market_id}-{timeframe}-{BARS_LAYOUT}.parquet"


def _read(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pl.read_parquet(path)
    except Exception as e:  # битый файл не должен ронять прогон
        # Молчать нельзя: пустой ответ читался бы как «истории нет», а история есть,
        # просто файл не открылся. Имя названо, чтобы владелец мог его удалить.
        log.error("файл хранилища баров не прочитан", файл=str(path),
                  причина=f"{type(e).__name__} {e}")
        return None


def coverage(venue: str, market_id: str, timeframe: str) -> tuple[int, int] | None:
    """Первая и последняя метка открытия в хранилище. `None` — ряда нет."""
    frame = _read(store_path(venue, market_id, timeframe))
    if frame is None or frame.is_empty():
        return None
    col = frame["open_ms"]
    return int(col.min()), int(col.max())  # type: ignore[arg-type]


def load(
    venue: str, market_id: str, timeframe: str,
    since_ms: int | None = None, upto_ms: int | None = None,
) -> list[Bar]:
    """Бары ряда в порядке возрастания метки. Границы включительны по левому краю бара."""
    frame = _read(store_path(venue, market_id, timeframe))
    if frame is None or frame.is_empty():
        return []
    if since_ms is not None:
        frame = frame.filter(pl.col("open_ms") >= since_ms)
    if upto_ms is not None:
        frame = frame.filter(pl.col("open_ms") <= upto_ms)
    frame = frame.sort("open_ms")
    return [
        Bar(open_ms=int(r[0]), open=float(r[1]), high=float(r[2]),
            low=float(r[3]), close=float(r[4]), volume=float(r[5]))
        for r in frame.select(COLUMNS).iter_rows()
    ]


def append(
    venue: str, market_id: str, timeframe: str, bars: list[Bar]
) -> tuple[int, int]:
    """Дописать бары. Возвращает `(добавлено новых, переписано существующих)`.

    ⚠ ПЕРЕПИСАННЫЕ СЧИТАЮТСЯ ОТДЕЛЬНО, и это не бухгалтерия. Закрытый бар неизменен: если
    биржа отдала по той же метке другие числа, это либо её правка задним числом, либо наша
    ошибка склейки. Молчаливая перезапись сделала бы оба случая невидимыми — ряд просто
    поменялся бы, и повтор карточки показал бы расхождение без причины. Вызывающий обязан
    напечатать это число.

    Приходящее ПОБЕЖДАЕТ хранимое: свежий ответ биржи — более поздний источник, чем наш
    старый файл. Выбор назван здесь, потому что обратный (хранимое побеждает) тоже
    защитим и дал бы противоположное поведение при правках биржи.
    """
    if not bars:
        return 0, 0
    path = store_path(venue, market_id, timeframe)
    fresh = pl.DataFrame(
        {
            "open_ms": [b.open_ms for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        schema=SCHEMA,
    ).unique(subset=["open_ms"], keep="last")

    old = _read(path)
    if old is None or old.is_empty():
        merged, added, rewritten = fresh.sort("open_ms"), fresh.height, 0
    else:
        old = old.select(COLUMNS)
        common = old.join(fresh, on="open_ms", how="inner", suffix="_new")
        rewritten = int(
            common.filter(
                (pl.col("open") != pl.col("open_new"))
                | (pl.col("high") != pl.col("high_new"))
                | (pl.col("low") != pl.col("low_new"))
                | (pl.col("close") != pl.col("close_new"))
                | (pl.col("volume") != pl.col("volume_new"))
            ).height
        )
        added = fresh.height - common.height
        merged = (
            pl.concat([old, fresh])
            .unique(subset=["open_ms"], keep="last")  # приходящее побеждает
            .sort("open_ms")
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(merged, path)
    return added, rewritten


def _write_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Запись через временный файл рядом плюс `os.replace`. Та же причина, что в
    `archive._write_atomic`: обрыв на записи оставлял бы обрезанный parquet, который
    читатель принял бы за полный ряд НАВСЕГДА."""
    tmp = path.with_suffix(f".part-{os.getpid()}")
    try:
        frame.write_parquet(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def missing_tail_since(
    venue: str, market_id: str, timeframe: str, want_from_ms: int
) -> int | None:
    """С какой метки просить у биржи, чтобы дополнить хранилище. `None` — просить всё.

    Возвращается метка СЛЕДУЮЩЕГО бара за последним сохранённым — не сам последний: биржа
    отдаёт `since` включительно, и повтор последнего бара не вреден, но и не нужен.

    ⚠ Если хранилище начинается ПОЗЖЕ, чем просят, дописать середину нечем: курсор идёт
    вперёд, а дыра позади. Тогда возвращается `None` — просить всё окно заново, и слияние
    в `append` уложит его вокруг имеющегося.
    """
    got = coverage(venue, market_id, timeframe)
    if got is None:
        return None
    first, last = got
    if first > want_from_ms:
        return None
    return last + tf_ms(timeframe)
