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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import polars as pl

from . import log
from .bars import grid_anchor_ms, tf_ms
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


def _scan(path: Path) -> pl.LazyFrame | None:
    """Ленивый вариант `_read` для читателей с фильтром по `open_ms`.

    ⚠ ЗАЧЕМ ОТДЕЛЬНО. `read_parquet` поднимает файл ЦЕЛИКОМ на каждый вызов; в бою это
    один раз на ряд и незаметно, но всякий читатель, зовущий `load` в цикле по окнам,
    платит полное чтение за каждое окно (замер 2026-08-18: ~5600 вызовов = минуты).
    Файл пишется отсортированным по `open_ms` (`append` завершается `sort`), поэтому
    статистики min/max групп строк позволяют polars пропускать группы вне фильтра —
    предикат уезжает в чтение, значения на выходе те же."""
    if not path.exists():
        return None
    return pl.scan_parquet(path)


def coverage(venue: str, market_id: str, timeframe: str) -> tuple[int, int] | None:
    """Первая и последняя метка открытия в хранилище. `None` — ряда нет."""
    lazy = _scan(store_path(venue, market_id, timeframe))
    if lazy is None:
        return None
    try:
        frame = lazy.select(
            pl.col("open_ms").min().alias("lo"), pl.col("open_ms").max().alias("hi")
        ).collect()
    except Exception as e:  # битый файл не должен ронять прогон — как в `_read`
        log.error("файл хранилища баров не прочитан",
                  файл=str(store_path(venue, market_id, timeframe)),
                  причина=f"{type(e).__name__} {e}")
        return None
    lo, hi = frame["lo"][0], frame["hi"][0]
    if lo is None or hi is None:
        return None
    return int(lo), int(hi)


def load(
    venue: str, market_id: str, timeframe: str,
    since_ms: int | None = None, upto_ms: int | None = None,
) -> list[Bar]:
    """Бары ряда в порядке возрастания метки. Границы включительны по левому краю бара."""
    lazy = _scan(store_path(venue, market_id, timeframe))
    if lazy is None:
        return []
    if since_ms is not None:
        lazy = lazy.filter(pl.col("open_ms") >= since_ms)
    if upto_ms is not None:
        lazy = lazy.filter(pl.col("open_ms") <= upto_ms)
    try:
        frame = lazy.sort("open_ms").collect()
    except Exception as e:  # битый файл не должен ронять прогон — как в `_read`
        log.error("файл хранилища баров не прочитан",
                  файл=str(store_path(venue, market_id, timeframe)),
                  причина=f"{type(e).__name__} {e}")
        return []
    if frame.is_empty():
        return []
    return [
        Bar(open_ms=int(r[0]), open=float(r[1]), high=float(r[2]),
            low=float(r[3]), close=float(r[4]), volume=float(r[5]))
        for r in frame.select(COLUMNS).iter_rows()
    ]


def count(
    venue: str, market_id: str, timeframe: str,
    since_ms: int | None = None, upto_ms: int | None = None,
) -> int:
    """СКОЛЬКО баров в диапазоне — без построения самих баров.

    ⚠ ЗАВЕДЕНА 2026-08-21 ПО ЗАМЕРУ. `backfill_profile_bars` спрашивал покрытие участка
    как `len(load(...))`, то есть строил каждый бар окна ради одного числа. Замер на
    минутном ряде BTC: окно в 1500 баров стоит 0.153 с через `load` и 0.017 с через
    счёт — девятикратно, и это на каждом участке каждого символа каждого цикла.

    Ответ ТОТ ЖЕ по построению: `load` создаёт ровно один `Bar` на строку и ни одной не
    отбрасывает, значит длина его списка есть число строк в том же диапазоне.

    ⚠ Битый файл ведёт себя как в `load` — ноль и НАЗВАННАЯ причина в журнале, а не
    исключение: непрочитанный файл означает «покрытия нет», и участок будет перекачан.
    Молчаливый ноль здесь читался бы как «данных нет», и это тот же ответ, но по другой
    причине; поэтому причина печатается.
    """
    lazy = _scan(store_path(venue, market_id, timeframe))
    if lazy is None:
        return 0
    if since_ms is not None:
        lazy = lazy.filter(pl.col("open_ms") >= since_ms)
    if upto_ms is not None:
        lazy = lazy.filter(pl.col("open_ms") <= upto_ms)
    try:
        return int(lazy.select(pl.len()).collect().item())
    except Exception as e:  # тот же разбор, что в `load`
        log.error("файл хранилища баров не прочитан (счёт)",
                  файл=str(store_path(venue, market_id, timeframe)),
                  причина=f"{type(e).__name__} {e}")
        return 0


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

    ⚠ ЗАПИСЬ ПОД МЕЖПРОЦЕССНЫМ ЗАМКОМ (2026-08-18, находка аудита). С тех пор как
    символ вселенной пересобирается по запросу при устаревшей карте, служба и бот
    МОГУТ писать один файл. Слияние здесь — read-modify-write: без замка проигравший
    терял бы ВСЕ дописанные им бары (не «только хвост», как утверждала прежняя
    докстрока tgbot), а `coverage` считает покрытие по min/max — внутреннюю дыру
    следующее чтение не добрало бы НИКОГДА, и окно навсегда осталось бы «покрыто не
    полностью» — форма молчаливой деградации из backfill-window-2026-08-04.
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

    with _append_lock(path):
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


_LOCK_WAIT_S = 30.0
"""Сколько ждать чужой замок, прежде чем отобрать его с предупреждением. Запись ряда
занимает доли секунды, поэтому долгое ожидание означает брошенный замок упавшего
процесса. Отбор, а не отказ: молчаливо пропущенная запись была бы той самой потерей
баров, от которой замок защищает. Часы для возраста замка НЕ используются нарочно:
`clock.now_ms` требует сведения с биржей и падал бы в путях без него — пробник это
поймал первым же прогоном."""


@contextmanager
def _append_lock(path: Path) -> Iterator[None]:
    """Межпроцессный замок на файл ряда: `<файл>.lock` через `O_CREAT|O_EXCL`.

    Заведён 2026-08-18: служба и сборка по запросу могут писать один ряд, а слияние
    в `append` — read-modify-write, атомарность одной лишь замены файла его не
    защищает. Замок живёт рядом с файлом и снимается в `finally`; ожидание дольше
    `_LOCK_WAIT_S` печатается и замок отбирается. Проверено пробником двух
    конкурирующих писателей: evidence/barstore-lock-probe-2026-08-18.py — с замком
    600 из 600 меток, без замка теряется половина (журнал смены, раздел 22).
    """
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                log.warn("замок ряда отобран после ожидания", файл=str(lock),
                            ожидание_с=_LOCK_WAIT_S)
                lock.unlink(missing_ok=True)
                deadline = time.monotonic() + _LOCK_WAIT_S
                continue
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Запись через временный файл рядом плюс `os.replace`. Та же причина, что в
    `archive._write_atomic`: обрыв на записи оставлял бы обрезанный parquet, который
    читатель принял бы за полный ряд НАВСЕГДА.

    ⚠ `os.replace` повторяется при `PermissionError` (2026-08-18): на Windows замена
    файла, который в этот миг ОТКРЫТ читателем (служба и бот делят `data/bars`),
    отказывает — пробник barstore-lock-probe поймал это живым прогоном. Чтение
    parquet — доли секунды, поэтому короткие повторы; исчерпание повторов роняет
    запись ИМЕНОВАННО, а не молча (§4.3)."""
    tmp = path.with_suffix(f".part-{os.getpid()}")
    try:
        frame.write_parquet(tmp)
        for attempt in range(40):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
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

    ⚠⚠ ОТВЕЧАЕТ ТОЛЬКО ПРО ХВОСТ и слеп к дыре ВНУТРИ окна: `coverage` считает покрытие
    по min/max, а между ними может не быть ничего. Для добора пользуйтесь
    `missing_spans` — она отвечает на тот же вопрос без этой слепоты. Функция оставлена,
    потому что вопрос «докуда ряд доведён» законен сам по себе.
    """
    got = coverage(venue, market_id, timeframe)
    if got is None:
        return None
    first, last = got
    if first > want_from_ms:
        return None
    return last + tf_ms(timeframe)


def _grid_up(ms: int, step: int, anchor: int) -> int:
    """Первый слот сетки не раньше `ms`."""
    return ((ms - anchor + step - 1) // step) * step + anchor


def _grid_down(ms: int, step: int, anchor: int) -> int:
    """Последний слот сетки не позже `ms`."""
    return ((ms - anchor) // step) * step + anchor


def missing_spans(
    venue: str, market_id: str, timeframe: str, want_from_ms: int, upto_ms: int
) -> list[tuple[int, int]]:
    """Участки окна, которых в хранилище НЕТ: полуинтервалы `[lo, hi)` на сетке ТФ.

    Пустое хранилище даёт один участок на всё окно; целый ряд — пустой список; ряд с
    дырой — участок на дыру и участок на хвост.

    ⚠ ЗАЧЕМ ОТДЕЛЬНО ОТ `missing_tail_since`. Тот отвечает курсором «после последнего
    сохранённого бара», то есть НИКОГДА не просит середину: `coverage` знает только
    min и max, а дыра лежит между ними. Пока хранилище начиналось позже запрошенного
    окна, слепота не проявлялась — курсор возвращал `None`, и окно просилось целиком.
    Замер 2026-08-21 по `data/bars`: у 144 рабочих рядов из 150 (96%) хранилище уже
    начинается РАНЬШЕ окна, то есть все они перешли на хвостовой курсор, и всякая дыра,
    возникшая в окне, осталась бы в нём навсегда. На соседних рядах того же диска дыры
    и лежат — 204 штуки в 183 рядах, 655 602 бара; контроль живым запросом к бирже дал
    бары во всех восьми проверенных дырах (100 из 100), то есть это НАША потеря, а не
    отсутствие торгов.

    Критерий здесь тот же, что у читателя окна профиля (`run.backfill_profile_bars`:
    «полных баров меньше слотов окна — участок перекачать»), и это не совпадение: два
    пути добора обязаны спрашивать об одной и той же величине, иначе один из них
    окажется слепым — ровно то, что и произошло. Разбор дыры внутри окна, найденной на
    ACE: docs/audit/plan-2026-08-21-geometry-transport.md

    Границы выравниваются на сетку ТФ НАШИМ якорем (`bars.grid_anchor_ms`), а не эпохой:
    у недельного бара эпоха даёт четверг вместо понедельника.
    """
    step = tf_ms(timeframe)
    anchor = grid_anchor_ms(timeframe)
    start = _grid_up(want_from_ms, step, anchor)
    end = _grid_down(upto_ms, step, anchor)
    if end < start:
        return []
    lazy = _scan(store_path(venue, market_id, timeframe))
    if lazy is None:
        return [(start, end + step)]
    try:
        frame = (
            lazy.filter((pl.col("open_ms") >= start) & (pl.col("open_ms") <= end))
            .select("open_ms")
            .sort("open_ms")
            .collect()
        )
    except Exception as e:  # тот же разбор, что в `load`: битый файл не роняет прогон
        log.error("файл хранилища баров не прочитан (участки)",
                  файл=str(store_path(venue, market_id, timeframe)),
                  причина=f"{type(e).__name__} {e}")
        return [(start, end + step)]
    spans: list[tuple[int, int]] = []
    cursor = start
    for raw in frame["open_ms"]:
        have = int(raw)
        if have > cursor:
            spans.append((cursor, have))
        if have >= cursor:
            cursor = have + step
    if cursor <= end:
        spans.append((cursor, end + step))
    return spans
