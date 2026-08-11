"""Продлевает ли живой поток покрытие кэша — и ОТКАЗЫВАЕТСЯ ли при дыре.

Повод. 2026-08-11 из проекта удалён суточный архив и снят холодный старт: принятое
вебсокетом должно ложиться в тот же суточный кэш, что и добранное REST-ом
(`archive.extend_day_from_live`). Механизм трогает КЭШ, то есть данные, из которых потом
строится профиль объёма, — и потому обязан быть проверен не на «работает вроде», а на
обоих исходах.

ЧТО ПРОВЕРЯЕТСЯ, три случая:

  1. СМЕЖНОЕ покрытие продлевается: кэш кончается там, где начинается поток → граница
     покрытия сдвигается вперёд, а объём прежних корзин не теряется;
  2. ДЫРА не заклеивается: кэш кончается раньше, чем начинается поток → отказ. Имя
     частичного файла объявляет покрытие ОТ НАЧАЛА СУТОК, и запись поверх дыры солгала бы
     о ней (§4.3);
  3. ЗАКРЫТЫЕ сутки не трогаются: у полного файла покрытие уже доведено до конца.

КОНТРОЛЬ (обязателен по CLAUDE.md). Случаи 1 и 2 отличаются РОВНО ОДНИМ: смежностью. Если
прибор отвечает одинаково на оба — он не различает дыру и её отсутствие, и «продлил
покрытие» в случае 1 ничего не значит. Поэтому оба случая печатаются рядом.

Данных биржи не требует: кэш пишется во временный каталог.

Воспроизведение:
    uv run python docs/audit/probes/probe_live_flush_2026-08-11.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import polars as pl  # noqa: E402

from hunter import archive  # noqa: E402

DAY = date(2026, 8, 1)
TICK = Decimal("0.1")
MARKET = "PROBEUSDT"
BUCKET = archive.CACHE_BUCKET_MS
DAY_START = int(datetime.combine(DAY, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)


def seed_part(root: Path, cover_to_ms: int) -> None:
    """Положить частичные сутки, покрытые от начала суток до `cover_to_ms`."""
    qty: dict[tuple[int, int], float] = {}
    cnt: dict[tuple[int, int], int] = {}
    bucket = DAY_START
    while bucket < cover_to_ms:
        qty[(bucket, 1000)] = 1.0
        cnt[(bucket, 1000)] = 1
        bucket += BUCKET
    frame = archive.frame_from_pairs(qty, cnt)
    path = archive.part_path(MARKET, DAY, TICK, cover_to_ms)
    archive._write_atomic(frame, root / path.name)


def live_from(first_bucket_ms: int, buckets: int) -> tuple[dict, dict]:
    """Живой поток: `buckets` корзин подряд, начиная с `first_bucket_ms`."""
    qty: dict[int, dict[int, float]] = {}
    cnt: dict[int, dict[int, int]] = {}
    for i in range(buckets):
        b = first_bucket_ms + i * BUCKET
        qty[b] = {2000: 5.0}
        cnt[b] = {2000: 3}
    return qty, cnt


def case(title: str, seed_to_buckets: int, live_start_buckets: int,
         live_buckets: int, *, full_day: bool = False) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed_to = DAY_START + seed_to_buckets * BUCKET
        if full_day:
            frame = archive.frame_from_pairs({(DAY_START, 1000): 1.0},
                                             {(DAY_START, 1000): 1})
            archive._write_atomic(frame, root / archive.cache_path(MARKET, DAY, TICK).name)
        else:
            seed_part(root, seed_to)

        before = archive.coverage_to_ms(MARKET, DAY, TICK, root)
        qty, cnt = live_from(DAY_START + live_start_buckets * BUCKET, live_buckets)
        upto = DAY_START + (live_start_buckets + live_buckets) * BUCKET
        got = archive.extend_day_from_live(MARKET, DAY, TICK, qty, cnt, upto, root)
        after = archive.coverage_to_ms(MARKET, DAY, TICK, root)

        kept = None
        if got is not None:
            found = archive.find_part(root, MARKET, DAY, TICK)
            if found is not None:
                fr = pl.read_parquet(found[0])
                kept = int(fr.filter(pl.col("bin") == 1000).height)

        def mins(ms: int | None) -> str:
            return "—" if ms is None else f"+{(ms - DAY_START)//60000}м"

        verdict = "ПРОДЛИЛ" if got is not None else "ОТКАЗ"
        line = (f"{title:<34} было {mins(before):>7}  стало {mins(after):>7}  "
                f"{verdict:<8}")
        if kept is not None:
            line += f" прежних корзин сохранено {kept}"
        return line


def main() -> int:
    print("случай                              покрытие до/после           итог")
    print("-" * 96)
    # 1. Поток начинается ровно там, где кончается кэш.
    print(case("1. смежное покрытие", 12, 12, 6))
    # 2. Между кэшем и потоком пропущены две корзины.
    print(case("2. ДЫРА в две корзины", 12, 14, 6))
    # 3. Сутки уже закрыты полным файлом.
    print(case("3. закрытые сутки", 0, 12, 6, full_day=True))
    print()
    print("КОНТРОЛЬ: случаи 1 и 2 отличаются РОВНО смежностью. Если оба «ПРОДЛИЛ» —")
    print("прибор не отличает дыру от её отсутствия, и случай 1 ничего не доказывает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
