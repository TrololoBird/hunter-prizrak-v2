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
  * широкое окно (сутки и больше) — архив, ГДЕ ОН ЕСТЬ;
  * узкое окно (минуты) — REST уместен и работает.

⚠ Решение владельца 2026-08-11: живой контур НЕ ЖДЁТ публикации архива. Файл суток
выходит с отставанием до двух суток (замер: 2026-08-10 и 2026-08-11 отдавали HTTP 404
при прогоне 2026-08-11), и до этой правки карта младших ТФ систематически обрывалась
на позавчера — свежайшие структуры не получали уровней. Теперь сутки, которых в архиве
нет, добирает REST-ом ccxt (`Exchange.fetch_agg_trades_window`): закрытые — полным
файлом кэша, незавершённые — частичным с границей покрытия в имени (`part_path`).
Архив остаётся дешёвым путём для глубокой истории, но единственным источником он
больше не является.

Целостность проверяется приложенным биржей .CHECKSUM: замер 2026-08-03 на
BTCUSDT-aggTrades-2026-08-01.zip — sha256 совпал.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import os
import re
import urllib.error
import urllib.request
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

from .models import BarBinnedTrades, NotReady, TradeHistogram, tick_scale

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


FETCH_TRIES = 3
"""Попыток на файл. Обрыв закачки — не отказ сервера, а транзиентная помеха.

⚠ Число подобрано, а не замерено: обрывов на выборке было слишком мало, чтобы считать
частоту. Здесь оно названо тем, чем является, и не выдаётся за измеренное.
"""


def _fetch(url: str, timeout: int, tries: int = FETCH_TRIES) -> bytes | NotReady:
    """Скачать файл. Любой сетевой сбой — НАЗВАННЫЙ отказ, а не исключение наружу.

    ⚠ Прежняя редакция ловила только `HTTPError` и `OSError`. `http.client.IncompleteRead`
    (оборванная закачка) не относится ни к тому, ни к другому — это `HTTPException`, —
    и потому пролетала наружу и роняла ВЕСЬ прогон из-за одних суток. Замер 2026-08-04:
    прогон упал на `IncompleteRead(5711811 bytes read, 7057424 more expected)`, успев
    уложить в кэш большую часть горизонта.

    Класс дефекта тот же, что и всё остальное здесь, только вывернутый: не молчаливое
    проглатывание, а падение там, где деградация допустима и должна быть названа (§4.3).
    """
    last = ""
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return bytes(r.read())
        except urllib.error.HTTPError as e:
            # HTTP-код сервера повтором не лечится: 404 останется 404.
            return NotReady(reason=f"{url}: HTTP {e.code}")
        except (OSError, http.client.HTTPException) as e:
            last = f"{type(e).__name__} {e}"
            if attempt < tries:
                continue
    return NotReady(reason=f"{url}: {last} (попыток {tries})")


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


def bin_expr(price: pl.Expr, tick: Decimal) -> pl.Expr:
    """Бин цены в полярисе. ТОТ ЖЕ расчёт, что `models.bin_index`, теми же двумя числами.

    Своей арифметики здесь нет умышленно: множитель и целый шаг берутся из
    `models.tick_scale`, а выражение повторяет `floor(price·scale + 0.5) // step`
    один в один. Прежняя редакция считала `(price / float(tick)).floor()` — и это была
    ВТОРАЯ схема бинирования в проекте, дававшая другой ПОК на 38% часовых окон.

    Согласованность двух ветвей проверяет gates/binning_agrees.py на реальной сетке цен:
    равенство здесь держится на арифметике, а не на том, что обе строки писал один
    человек в один день.
    """
    scale, step = tick_scale(tick)
    return (price * scale + 0.5).floor().cast(pl.Int64) // step


CACHE_DIR = Path("data/aggcache")
CACHE_LAYOUT = "b2"
"""Метка СХЕМЫ БИНИРОВАНИЯ в имени файла кэша.

В кэш кладутся уже посчитанные НОМЕРА БИНОВ. Значит содержимое файла зависит не только
от суток и корзины, но и от того, как считался бин, — а этого в имени не было. Правка
бинирования (Б-1) сделала все 471 накопленных суток неверными молча: номера посчитаны
старой схемой, а `bin_price` умножил бы их на шаг по новой.

Метка растёт вместе со схемой; файлы прежней метки не читаются и не удаляются — они
просто перестают подходить, и это видно по имени, а не по расхождению чисел.
"""

CACHE_BUCKET_MS = 300_000
"""Корзина кэша — 5 минут, самый младший ТФ проекта (§2.8: 5м/15м/1ч/4ч/1Д/1Н).

Окно любой структуры кратно своему ТФ, а все ТФ кратны пяти минутам, значит срез по
кэшу ТОЧЕН, а не приблизителен. Мельче — раздувает кэш без выигрыша; крупнее — сделало
бы окна 5м-структур приблизительными, а приблизительное окно профиля это ровно тот
дефект, что дал чужой ПОК 63 950 (docs/audit/stage3-corpus-acceptance-2026-08-03.md).

⚠ Гранулярность стоит в ИМЕНИ файла. Сменить её — значит обесценить весь накопленный
кэш: свернуть 15м обратно в 5м нельзя. Первая редакция кэша (в scripts/probes.py) была
на 900_000, и переход сюда потребовал перекачки 3.6 ГБ.
"""


def _tick_tag(tick: Decimal) -> str:
    """Шаг цены как часть имени файла. Точка в имени допустима, но путает глаз и glob."""
    return f"t{tick:f}".replace(".", "p")


def cache_path(market_id: str, day: date, tick: Decimal) -> Path:
    """Имя файла кэша. ВСЁ, от чего зависит содержимое, стоит в имени.

    ⚠ Шага цены здесь не было до 2026-08-04, и это дефект того же класса, что Б-1:
    молчаливая рассогласованность двух конвенций. Binance меняет `PRICE_FILTER` у
    символа — штатная операция при изменении цены на порядок, — и после такой смены
    весь накопленный кэш по символу становился неверным без единого признака: номера
    бинов посчитаны по старому шагу, а `bin_price` умножает их на новый.
    """
    return (CACHE_DIR
            / f"{market_id}-{day.isoformat()}-{CACHE_BUCKET_MS}-{_tick_tag(tick)}"
              f"-{CACHE_LAYOUT}.parquet")


def _write_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Запись через временный файл в ТОМ ЖЕ каталоге плюс `os.replace`.

    ⚠ Прежняя редакция писала прямо по целевому пути. Обрыв процесса (а качается до
    сотен файлов по ~15 МБ, с таймаутом 900 с и тремя попытками) оставлял обрезанный
    parquet, и дальше `binned_day` видел `path.exists()` и возвращал его НАВСЕГДА, не
    перекачивая, а `cached_days` считал эти сутки загруженными. Причина потом выглядела
    бы как «битый файл», а не как «недокачано».

    `os.replace` атомарна в пределах одной файловой системы — отсюда требование класть
    временный файл рядом, а не в системный временный каталог.
    """
    tmp = path.with_suffix(f".part-{os.getpid()}")
    try:
        frame.write_parquet(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def binned_day(
    market_id: str, day: date, tick: Decimal, timeout: int = 900
) -> pl.DataFrame | NotReady:
    """Сутки сделок, свёрнутые в (корзина, бин) → объём и число, С КЭШЕМ на диске.

    Кэш обязателен, а не удобен: боевой бэкфилл без него качал 15 МБ на символ-сутки
    ЗАНОВО каждый прогон, и потому был вынужден ограничиваться тремя сутками — а трёх
    суток хватает на 15% структур и на НОЛЬ структур 4ч и 1Д (замер 2026-08-04,
    docs/audit/backfill-window-2026-08-04.md).

    sha256 сверяется ДО свёртки и только при скачивании: в кэш кладётся уже проверенное.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(market_id, day, tick)
    if path.exists():
        return pl.read_parquet(path)

    got = fetch_agg_trades_day(market_id, day, timeout)
    if isinstance(got, NotReady):
        return got
    binned = got.frame.with_columns(
        (pl.col("transact_time") // CACHE_BUCKET_MS * CACHE_BUCKET_MS).alias("bucket"),
        bin_expr(pl.col("price"), tick).alias("bin"),
    ).group_by("bucket", "bin").agg(
        pl.col("quantity").sum().alias("qty"), pl.len().alias("n")
    )
    _write_atomic(binned, path)
    return binned


def frame_from_pairs(
    qty: dict[tuple[int, int], float], cnt: dict[tuple[int, int], int]
) -> pl.DataFrame:
    """Свернуть пары (корзина, бин) в кадр ТОЙ ЖЕ схемы, что у `binned_day`.

    Нужен REST-добору: он бинит сделки поштучно через `models.bin_index` (единственную
    функцию бинирования; согласие с `bin_expr` держит gates/binning_agrees.py), а кэш
    хранит кадры. Строки сортируются по (корзина, бин) — порядок в файле детерминирован,
    как того требует §10.6.
    """
    keys = sorted(qty)
    return pl.DataFrame({
        "bucket": [k[0] for k in keys],
        "bin": [k[1] for k in keys],
        "qty": [qty[k] for k in keys],
        "n": [cnt[k] for k in keys],
    }, schema={"bucket": pl.Int64, "bin": pl.Int64, "qty": pl.Float64, "n": pl.UInt32})


def write_full_day_from_frame(
    market_id: str, day: date, tick: Decimal, frame: pl.DataFrame
) -> Path:
    """Положить ЗАКРЫТЫЕ сутки, добранные REST-ом, в кэш под именем полных.

    Право на имя полных даёт вызывающий: сюда пишутся только сутки, чьё покрытие
    доведено до конца суток ВРЕМЕННЫМ курсором (`fetch_agg_trades_window`) — страница
    просится с миллисекунды последней принятой сделки, повтор границы отсеивается по
    номерам aggTrade, пустой ответ засчитывает покрытым ровно час (верхнюю границу
    `endTime = since + 1 ч` дописывает сама ccxt). ⚠ Это гарантия слабее архивной:
    sha256 у REST-пути нет, а непрерывность держится на поведении пагинации, а не на
    номерах от первого до последнего. Сутки без ЕДИНОЙ сделки под это имя не пишутся
    вовсе (`_rest_fill_day` отказывает): пустота глубже хранения биржи неотличима от
    настоящей, а файл с нулём отравил бы кэш навсегда.

    Частичные файлы этих суток после появления полного подчищаются: они целиком
    входят в полный и больше никогда не будут выбраны (`_day` смотрит полный раньше).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(market_id, day, tick)
    _write_atomic(frame, path)
    for p in CACHE_DIR.glob(f"{path.stem}-part*.parquet"):
        p.unlink(missing_ok=True)
    return path


def part_path(market_id: str, day: date, tick: Decimal, cover_to_ms: int) -> Path:
    """Имя ЧАСТИЧНЫХ суток: как у полных, плюс граница покрытия в миллисекундах.

    Введено 2026-08-11 решением владельца: живой контур не ждёт публикации архива —
    сутки, которых у `data.binance.vision` ещё нет, добираются REST-ом через ccxt.
    Незавершённые сутки нельзя класть под именем полных: следующий прогон счёл бы их
    покрытыми целиком и построил усечённый профиль МОЛЧА. Поэтому граница покрытия
    стоит в имени — всё, от чего зависит содержимое, обязано стоять в имени (та же
    логика, что у шага цены и метки схемы).
    """
    base = cache_path(market_id, day, tick)
    return base.with_name(f"{base.stem}-part{cover_to_ms}.parquet")


def find_part(
    cache_dir: Path, market_id: str, day: date, tick: Decimal
) -> tuple[Path, int] | None:
    """Найти частичные сутки в каталоге; при нескольких — с наибольшим покрытием.

    Несколько файлов одних суток — след оборванного добора, а не ошибка: новый файл
    пишется раньше, чем стираются старые. Побеждает наибольшее покрытие.
    """
    base = cache_path(market_id, day, tick)
    best: tuple[Path, int] | None = None
    for p in cache_dir.glob(f"{base.stem}-part*.parquet"):
        m = re.fullmatch(r".*-part(\d+)", p.stem)
        if not m:
            continue
        cover = int(m.group(1))
        if best is None or cover > best[1]:
            best = (p, cover)
    return best


def write_part_day(
    market_id: str, day: date, tick: Decimal, frame: pl.DataFrame, cover_to_ms: int
) -> Path:
    """Положить частичные сутки в общий кэш; прежние частичные файлы этих суток убрать.

    Убирается только то, что этим же вызовом заменено файлом с БОЛЬШИМ покрытием, —
    это смена поколения кэша, а не потеря данных: содержимое старого файла целиком
    входит в новый (та же пагинация по fromId с начала суток).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = part_path(market_id, day, tick, cover_to_ms)
    _write_atomic(frame, path)
    base = cache_path(market_id, day, tick)
    for p in CACHE_DIR.glob(f"{base.stem}-part*.parquet"):
        m = re.fullmatch(r".*-part(\d+)", p.stem)
        if m and int(m.group(1)) < cover_to_ms:
            p.unlink(missing_ok=True)
    return path


CACHE_STEM = re.compile(r"^(?P<market>.+)-(?P<day>\d{4}-\d{2}-\d{2})$")
"""Стебель имени ПОСЛЕ отрезания хвоста `-корзина-шаг-схема`: рынок и дата, больше ничего.

⚠ До 2026-08-11 шаблон требовал ещё один сегмент ПОСЛЕ даты (`-(?P<rest>.+)`), который
к моменту сопоставления уже отрезан, — и потому `cached_days` возвращала пусто ВСЕГДА,
при любом состоянии кэша. Прибор, запертый в одном ответе (реестр CLAUDE.md): сводка
бэкфилла честно печатала «из_кэша=0, качать=75» поверх 73 лежащих на диске суток.
На закачку дефект не влиял (`binned_day` проверяет файл сам), врала только сводка.
Пойман сверкой строки лога с `ls data/aggcache` на втором прогоне BEAT."""


def cached_days(market_id: str, tick: Decimal) -> set[date]:
    """Какие сутки уже лежат в кэше — чтобы отчёт мог назвать цену прогона заранее.

    Шаг цены — часть вопроса, а не подробность: файл, посчитанный по другому шагу, этим
    суткам не годится, и считать его загруженным значит соврать в отчёте.
    """
    tail = f"-{CACHE_BUCKET_MS}-{_tick_tag(tick)}-{CACHE_LAYOUT}"
    out: set[date] = set()
    for p in CACHE_DIR.glob(f"{market_id}-*{tail}.parquet"):
        m = CACHE_STEM.match(p.stem[: -len(tail)] if p.stem.endswith(tail) else "")
        if m and m["market"] == market_id:
            out.add(date.fromisoformat(m["day"]))
    return out


class WindowSource:
    """Профиль по окну ПО ТРЕБОВАНИЮ: суточный кэш на диске плюс живой поток прогона.

    Заменяет накопление всего горизонта в одном `BarBinnedTrades`. Замер, из-за которого
    это переделано (docs/audit/backfill-window-2026-08-04.md): 102 суток BTC — это
    двадцать миллионов пар (корзина, бин), слияние идёт ~10.5 с на миллион и кончается
    `MemoryError`. При этом КАЖДОЙ структуре нужно только её собственное окно, а окна
    вместе покрывают горизонт лишь потому, что их много, — держать всё сразу незачем.

    Источников два, и порядок важен:
      * архив — история, неизменяемая и сверенная по sha256 при укладке в кэш;
      * живой поток — последние сутки, которых в архиве ещё нет (замер: файл суток
        публикуется с задержкой, 03.08 и 04.08 отдавали HTTP 404).

    Окно, не покрытое ни тем ни другим, — `NotReady` с перечислением недостающих суток,
    а не молча усечённый профиль (§4.3).

    Кэш прочитанных суток ограничен `max_open`: без него 40 структур × до 19 суток дают
    сотни повторных чтений parquet, с ним — по одному на сутки.
    """

    def __init__(
        self,
        symbol: str,
        market_id: str,
        tick: Decimal,
        live: BarBinnedTrades | None = None,
        max_open: int = 48,
        cache_dir: Path | None = None,
    ) -> None:
        self.symbol = symbol
        self.market_id = market_id
        self.tick = tick
        self.live = live
        self.max_open = max_open
        self.cache_dir = CACHE_DIR if cache_dir is None else cache_dir
        """Откуда читать сутки. Прогон читает ОБЩИЙ кэш, повтор — срез, сохранённый
        в кадрах этого прогона: иначе повтор зависит от того, что успел долить бэкфилл
        соседнего прогона (А-3, Н-6 разбора)."""

        self.used_paths: set[Path] = set()
        """ФАЙЛЫ, ФАКТИЧЕСКИ прочитанные при построении окон, — их кладёт в кадры
        `persist_archive`, и на этом стоит герметичность повтора.

        Именно пути, а не даты (до 2026-08-11 здесь был `used_days`): сутки бывают
        двумя видами файлов — полными и частичными (`-part<мс>`), и повтору нужен
        ровно тот файл, что читался, иначе пересборка разошлась бы на границе покрытия.
        """

        self._open: OrderedDict[date, pl.DataFrame] = OrderedDict()
        self._open_parts: dict[date, tuple[pl.DataFrame, int] | None] = {}

    def _day(self, day: date) -> pl.DataFrame | None:
        """Сутки из кэша. Скачивания здесь НЕТ: это работа бэкфилла, а не расчёта."""
        if day in self._open:
            self._open.move_to_end(day)
            return self._open[day]
        path = self.cache_dir / cache_path(self.market_id, day, self.tick).name
        if not path.exists():
            return None
        frame = pl.read_parquet(path)
        self.used_paths.add(path)
        self._open[day] = frame
        while len(self._open) > self.max_open:
            self._open.popitem(last=False)
        return frame

    def _part(self, day: date) -> tuple[pl.DataFrame, int] | None:
        """Частичные сутки из кэша: (кадр, покрыто_до_мс). Скачивания здесь тоже нет.

        Появляются у суток, которых нет в архиве публикации: REST-добор бэкфилла пишет
        их с границей покрытия в имени (решение владельца 2026-08-11). Остаток суток за
        границей добирает живой поток — см. `window`.
        """
        if day in self._open_parts:
            return self._open_parts[day]
        found = find_part(self.cache_dir, self.market_id, day, self.tick)
        if found is None:
            self._open_parts[day] = None
            return None
        path, cover = found
        frame = pl.read_parquet(path)
        self.used_paths.add(path)
        self._open_parts[day] = (frame, cover)
        return self._open_parts[day]

    def window(self, from_ms: int, to_ms: int) -> TradeHistogram | NotReady:
        d0 = datetime.fromtimestamp(from_ms / 1000, UTC).date()
        d1 = datetime.fromtimestamp((to_ms - 1) / 1000, UTC).date()
        span = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]

        qty: dict[int, float] = {}
        cnt: dict[int, int] = {}
        # Непокрытое — СПИСКОМ ДИАПАЗОНОВ, а не суток: у частичных суток дыра начинается
        # с границы покрытия, а не с полуночи, и просить у живого потока всю дату значило
        # бы сложить уже сложенное дважды (тот же класс дефекта, что «живой добор на всё
        # окно» до 2026-08-09).
        missing: list[tuple[date, int, int]] = []
        partial_at: dict[date, int] = {}
        """Сутки, у которых есть частичный файл, и его граница: отказ обязан называть
        «частично до X», а не «нет суток» (§4.3 — причина, а не ближайшая похожая)."""
        for day in span:
            day0 = int(datetime(day.year, day.month, day.day, tzinfo=UTC)
                       .timestamp() * 1000)
            day_end = day0 + 86_400_000
            frame = self._day(day)
            if frame is None:
                got_part = self._part(day)
                if got_part is None:
                    missing.append((day, max(from_ms, day0), min(to_ms, day_end)))
                    continue
                frame, cover = got_part
                rem_from = max(from_ms, cover)
                rem_to = min(to_ms, day_end)
                if rem_from < rem_to:
                    missing.append((day, rem_from, rem_to))
                    partial_at[day] = cover
                frame = frame.filter(pl.col("bucket") < cover)
            # ⚠ `maintain_order=True` — не украшение, а условие §10.6. Без него polars
            # группирует параллельно и отдаёт группы в РАЗНОМ ПОРЯДКЕ от прогона к
            # прогону; порядок уходит в `qty_by_bin`, оттуда — в порядок сложения float,
            # и суммы объёма расходятся в последнем знаке.
            #
            # Найдено 2026-08-10 самопроверкой диффа повтора в обзоре уровней: две
            # пересборки карточки ОДНИМ И ТЕМ ЖЕ кодом из одних и тех же кадров дали
            # 4 расходящихся строки из 1086 — «объём 30698.31» против «30698.32»,
            # «794984.9» против «794984.89». Расчёт при этом не менялся.
            #
            # Почему это важнее, чем выглядит: §10.6 условие 2 называет дифф повтора
            # единственной проверкой, не требующей чтения кода. Шумящий на ровном месте
            # дифф эту проверку обесценивает — владелец не может отличить правку от
            # порядка сложения.
            part = frame.filter(
                (pl.col("bucket") >= from_ms) & (pl.col("bucket") < to_ms)
            ).group_by("bin", maintain_order=True).agg(pl.col("qty").sum(),
                                                       pl.col("n").sum())
            for row in part.iter_rows(named=True):
                idx = int(row["bin"])
                qty[idx] = qty.get(idx, 0.0) + float(row["qty"])
                cnt[idx] = cnt.get(idx, 0) + int(row["n"])

        if missing and self.live is not None:
            # Сутки без архива добираются из живого потока — РОВНО недостающие, а не всё
            # окно. До 2026-08-09 живой добор просился на всё окно [from_ms, to_ms), и
            # любой непустой ответ обнулял весь `missing`. Это несло два дефекта сразу:
            #   * ДВОЙНОЙ СЧЁТ: живой буфер держит LIVE_TRADES_KEEP_DAYS суток, включая
            #     те, что УЖЕ сложены из архива строками выше; при пересечении покрытий
            #     (архив суток опубликован, а live их ещё помнит) объём этих суток
            #     складывался дважды — молча, профиль просто «тяжелел»;
            #   * ЛОЖНАЯ ПОЛНОТА: `missing = []` ставился без проверки, что живой ответ
            #     покрыл именно недостающие сутки.
            # Смягчало обе беды только то, что `BarBinnedTrades.window` отказывает, когда
            # окно шире собранного, — на старых недостающих сутках добор не срабатывал
            # вовсе. Ревизия транспорта 2026-08-09 (§5 п.4) и собственное правило
            # transport-decision §3.3 требуют добора по суткам. Контроль:
            # docs/audit/probes/probe_live_overlap_2026-08-09.py — на подстроенном
            # пересечении прежний способ удваивает, посуточный нет.
            still: list[tuple[date, int, int]] = []
            for day, gap_from, gap_to in missing:
                got = self.live.window(gap_from, gap_to)
                if isinstance(got, NotReady):
                    still.append((day, gap_from, gap_to))
                    continue
                for idx, q in got.qty_by_bin.items():
                    qty[idx] = qty.get(idx, 0.0) + q
                    cnt[idx] = cnt.get(idx, 0) + got.count_by_bin.get(idx, 0)
            missing = still

        if missing:
            days = [d.isoformat() + (f" (частично, до {partial_at[d]})"
                                     if d in partial_at else "")
                    for d, _, _ in missing]
            return NotReady(
                reason=f"{self.symbol}: окно [{from_ms},{to_ms}) не покрыто — нет суток "
                       f"{', '.join(days[:5])}"
                       + (f" и ещё {len(days) - 5}" if len(days) > 5 else "")
            )
        if not qty:
            return NotReady(reason=f"{self.symbol}: в окне [{from_ms},{to_ms}) сделок нет")

        h = TradeHistogram(symbol=self.symbol, tick_size=self.tick)
        h.qty_by_bin = qty
        h.count_by_bin = cnt
        h.qty_seen = sum(qty.values())
        h.trades_seen = sum(cnt.values())
        h.first_ms, h.last_ms = from_ms, to_ms
        return h


# ⚠ `histogram_from_window(day, symbol, tick, from_ms, to_ms)` УДАЛЕНА 2026-08-06:
# потребителя не было. Профиль по окну строит `WindowSource.window` — он читает кэш
# суток, а не держит их целиком в памяти, и именно из-за этого был написан
# (docs/audit/backfill-window-2026-08-04.md, MemoryError на 102 сутках).


def histogram_from_day(day_data: ArchiveDay, symbol: str, tick: Decimal) -> TradeHistogram:
    """Свернуть сутки сделок в гистограмму цена→объём с шагом tickSize (§5)."""
    return _histogram(day_data.frame, symbol, tick, day_data.rows)


def _histogram(
    frame: pl.DataFrame, symbol: str, tick: Decimal, rows: int
) -> TradeHistogram:
    binned = frame.with_columns(
        bin_expr(pl.col("price"), tick).alias("bin")
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
