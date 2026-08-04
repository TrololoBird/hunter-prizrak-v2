"""Зонды, воспроизводящие числа из docs/audit. FOUNDATION.md §7.3 и §7.4.

§7.3 требует, чтобы у каждого числового утверждения была команда воспроизведения.
§7.4 требует, чтобы зонд не оставался осиротевшим файлом. Одно с другим мирится так:
все зонды живут ЗДЕСЬ, каждый вызывается по имени, и протокол ссылается на команду.

    uv run python scripts/probes.py <имя>

⚠ Числа зависят от рынка на момент прогона. Совпадать с протоколом ПОБИТОВО они не будут
и не должны — протокол датирован. Воспроизводится СПОСОБ получения и порядок величины,
а не значение.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

from hunter import accumulation, breach, factors, indicators, levels, pereprior, swings
from hunter.bars import TIMEFRAME_MS
from hunter.exchange import Exchange
from hunter.models import Bar, NotReady, TradeHistogram
from hunter.swings import SwingKind

ARCHIVE = "https://data.binance.vision/data/futures/um/daily"
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT")
TFS = ("5m", "15m", "1h", "4h", "1d")

CACHE = Path("data/aggcache")
BUCKET_MS = 900_000
"""Корзина кэша 15 минут: окна структур 15м…1Д кратны ей, то есть срез ТОЧЕН.

Меньшая корзина раздула бы кэш без выигрыша, большая сделала бы срез приблизительным —
а приблизительное окно профиля это ровно тот дефект, который дал ПОК 63 950 у старого
проекта (docs/audit/stage3-corpus-acceptance-2026-08-03.md).
"""

# Уровни, снятые с кадров автора 03.08.2026 — research/author_markup/2026-08-03_overcarder.md
AUTHOR: dict[str, list[float]] = {
    "ETH/USDT:USDT": [2020, 2014, 1987, 1961.5, 1945.5, 1935.5, 1919, 1905, 1895, 1883,
                      1817, 1804, 1785, 1771, 1741, 1700],
    "BTC/USDT:USDT": [68000, 67860, 67130, 66850, 66610, 66310, 65950, 65190, 64830,
                      64360, 63950, 63680, 62010, 61550, 61000, 60800, 59920, 59000,
                      58630, 57790],
}
NOISE = 0.153  # замеренный разброс ПОК по разрешениям, docs/audit/stage3-corpus-acceptance


async def _bars(symbols: tuple[str, ...], tfs: tuple[str, ...],
                limit: int = 1000) -> dict[tuple[str, str], list[Bar]]:
    ex = Exchange()
    await ex.open()
    out: dict[tuple[str, str], list[Bar]] = {}
    try:
        for s in symbols:
            for tf in tfs:
                f = await ex.fetch_closed_ohlcv(s, tf, limit=limit)
                if not isinstance(f, NotReady):
                    out[(s, tf)] = f.bars
    finally:
        await ex.close()
    return out


def _series(bars: list[Bar], expr: pl.Expr) -> list[float | None]:
    df = pl.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars],
                       "low": [b.low for b in bars], "close": [b.close for b in bars]})
    return list(df.select(expr.alias("v"))["v"])


def _fetch_day(market_id: str, day: date, tick: Decimal, bucket_ms: int) -> pl.DataFrame | None:
    """Сутки сделок из архива, сразу свёрнутые в (корзина, бин) → объём."""
    url = f"{ARCHIVE}/aggTrades/{market_id}/{market_id}-aggTrades-{day.isoformat()}.zip"
    try:
        with urllib.request.urlopen(url, timeout=900) as r:
            blob = bytes(r.read())
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=300) as r:
            expected = r.read().decode().split()[0]
    except OSError as e:
        print(f"    {day}: не получено — {type(e).__name__}")
        return None
    if hashlib.sha256(blob).hexdigest() != expected:
        print(f"    {day}: sha256 НЕ СОШЁЛСЯ — отброшено")
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv = z.read(z.namelist()[0])
    return pl.read_csv(io.BytesIO(csv), columns=["price", "quantity", "transact_time"]) \
        .with_columns(
            (pl.col("transact_time") // bucket_ms * bucket_ms).alias("bucket"),
            (pl.col("price") / pl.lit(float(tick))).floor().cast(pl.Int64).alias("bin"),
        ).group_by("bucket", "bin").agg(pl.col("quantity").sum().alias("qty"))


def _cached_day(market_id: str, day: date, tick: Decimal) -> pl.DataFrame | None:
    """Сутки сделок, свёрнутые в (корзина, бин) → объём и число сделок, с кэшем на диске.

    Кэш нужен не для скорости ради скорости: без него сверка ПОК по 220 суткам архива
    (3.4 ГБ) не переигрывается, а значит §7.3 «воспроизводится одной командой» становится
    обещанием на двадцать минут трафика при каждом чтении протокола.

    Сумма sha256 проверяется ДО свёртки и только при скачивании: в кэш кладётся уже
    проверенное. Несошедшаяся сумма — отказ (`None`), а не тихо усечённые сутки.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{market_id}-{day.isoformat()}-{BUCKET_MS}.parquet"
    if path.exists():
        return pl.read_parquet(path)

    url = f"{ARCHIVE}/aggTrades/{market_id}/{market_id}-aggTrades-{day.isoformat()}.zip"
    try:
        with urllib.request.urlopen(url, timeout=900) as r:
            blob = bytes(r.read())
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=300) as r:
            expected = r.read().decode().split()[0]
    except OSError as e:
        print(f"    {market_id} {day}: не получено — {type(e).__name__} {e}")
        return None
    if hashlib.sha256(blob).hexdigest() != expected:
        print(f"    {market_id} {day}: sha256 НЕ СОШЁЛСЯ — отброшено")
        return None

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv = z.read(z.namelist()[0])
    binned = pl.read_csv(io.BytesIO(csv), columns=["price", "quantity", "transact_time"]) \
        .with_columns(
            (pl.col("transact_time") // BUCKET_MS * BUCKET_MS).alias("bucket"),
            (pl.col("price") / pl.lit(float(tick))).floor().cast(pl.Int64).alias("bin"),
        ).group_by("bucket", "bin").agg(
            pl.col("quantity").sum().alias("qty"), pl.len().alias("n")
        )
    binned.write_parquet(path)
    print(f"    {market_id} {day}: {len(blob)/1e6:5.2f} МБ → строк {binned.height}")
    return binned


def _histogram_over(
    frames: list[pl.DataFrame], symbol: str, tick: Decimal, from_ms: int, to_ms: int
) -> TradeHistogram | NotReady:
    """Гистограмма по окну `[from_ms, to_ms)` поверх кэшированных суток."""
    parts = [
        f.filter((pl.col("bucket") >= from_ms) & (pl.col("bucket") < to_ms)) for f in frames
    ]
    w = pl.concat([p for p in parts if p.height]) if any(p.height for p in parts) else None
    if w is None:
        return NotReady(reason=f"{symbol}: в окне [{from_ms},{to_ms}) нет ни одной сделки")
    agg = w.group_by("bin").agg(pl.col("qty").sum(), pl.col("n").sum())
    h = TradeHistogram(symbol=symbol, tick_size=tick)
    for row in agg.iter_rows(named=True):
        h.qty_by_bin[int(row["bin"])] = float(row["qty"])
        h.count_by_bin[int(row["bin"])] = int(row["n"])
    h.trades_seen = int(agg["n"].sum())
    h.qty_seen = float(agg["qty"].sum())
    h.first_ms, h.last_ms = from_ms, to_ms - 1
    return h


def _resolution_noise(hist: TradeHistogram, poc: float) -> float:
    """Допуск сравнения с автором = разброс СОБСТВЕННОГО ПОК по числу строк профиля.

    Правило из docs/audit/stage3-corpus-acceptance-2026-08-03.md: допуск не константа,
    он считается для каждой структуры. Автор смотрит TradingView, где профиль строится
    по фиксированному ЧИСЛУ СТРОК, а не по шагу цены инструмента.
    """
    bins = hist.qty_by_bin
    lo, hi = min(bins), max(bins)
    tick = float(hist.tick_size)
    pocs = [poc]
    for rows in (24, 30, 60, 120, 240):
        width = max(1, (hi - lo + 1) // rows)
        coarse: dict[int, float] = {}
        for b, q in bins.items():
            k = (b - lo) // width
            coarse[k] = coarse.get(k, 0.0) + q
        peak = max(coarse.values())
        k = min(kk for kk, v in coarse.items() if v == peak)
        pocs.append((lo + k * width) * tick)
    return (max(pocs) - min(pocs)) / poc * 100


def _levels_over(
    symbol: str,
    tick: Decimal,
    todo: list[tuple[str, accumulation.Accumulation, int, int]],
    frames: dict[date, pl.DataFrame],
    series: dict[str, list[Bar]],
) -> tuple[list[tuple[levels.Level, float, int, bool]], Counter[str], Counter[str]]:
    """Уровни по списку структур: сам `Level`, допуск, число сделок, активен ли.

    Возвращает НЕ отфильтрованный список, а признак `active` рядом: отбор — дело зонда,
    и разные зонды отбирают по-разному (сверка с автором берёт активные, замер лестницы —
    все, потому что стр. 32 про состав структуры, а не про торгуемость).
    """
    out: list[tuple[levels.Level, float, int, bool]] = []
    states: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for tf, a, lo, hi in todo:
        d0 = datetime.fromtimestamp(lo / 1000, UTC).date()
        d1 = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
        want = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        if any(d not in frames for d in want):
            skipped[f"{tf}: суток нет в кэше"] += 1
            continue
        hist = _histogram_over([frames[d] for d in want], symbol, tick, lo, hi)
        if isinstance(hist, NotReady):
            skipped[f"{tf}: {hist.reason.split(':')[-1].strip()}"] += 1
            continue
        lvl = levels.build_level(a, hist, symbol, (lo, hi))
        if isinstance(lvl, NotReady):
            skipped[f"{tf}: ПОК не построен"] += 1
            continue
        st = levels.status(lvl, series[tf])
        active = st.state is levels.LevelState.ACTIVE
        if not active:
            states[st.state.value] += 1
        out.append((lvl, _resolution_noise(hist, float(lvl.price)), hist.trades_seen,
                    active))
    return out, states, skipped


async def _archive_levels(
    ex: Exchange, symbol: str, horizon_days: int = 90
) -> tuple[list[tuple[levels.Level, float, int, bool]], Counter[str], Counter[str], int]:
    """Все уровни символа в горизонте: бары → структуры → архив → ПОК. Общая часть зондов."""
    inst = ex.instrument(symbol)
    if isinstance(inst, NotReady):
        raise RuntimeError(inst.reason)
    series: dict[str, list[Bar]] = {}
    for tf in TFS:
        f = await ex.fetch_closed_ohlcv(symbol, tf, limit=1000)
        if not isinstance(f, NotReady):
            series[tf] = f.bars

    now = max(b[-1].open_ms for b in series.values())
    cut = now - horizon_days * 86400000
    todo: list[tuple[str, accumulation.Accumulation, int, int]] = []
    days: set[date] = set()
    for tf, bars in series.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        for a in accumulation.detect(bars, sw, tf).closed:
            lo, hi = levels.structure_window_ms(a, bars, TIMEFRAME_MS[tf])
            if hi < cut:
                continue
            todo.append((tf, a, lo, hi))
            d = datetime.fromtimestamp(lo / 1000, UTC).date()
            end = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
            while d <= end:
                days.add(d)
                d += timedelta(days=1)

    def pull(d: date, mid: str = inst.market_id, tk: Decimal = inst.tick_size
             ) -> tuple[date, pl.DataFrame | None]:
        return d, _cached_day(mid, d, tk)

    with ThreadPoolExecutor(max_workers=4) as pool:
        pulled = list(pool.map(pull, sorted(days)))
    frames = {d: f for d, f in pulled if f is not None}
    built, states, skipped = _levels_over(symbol, inst.tick_size, todo, frames, series)
    return built, states, skipped, len(todo)


# --- зонды -------------------------------------------------------------------

async def structures() -> None:
    """course-complete, stuck-structure: структуры, распады, хвост, тренд по ТФ."""
    data = await _bars(SYMS, TFS)
    for (s, tf), bars in data.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            print(f"{s:16} {tf:4} {sw.reason}")
            continue
        sc = accumulation.detect(bars, sw, tf)
        tr = swings.trend(sw)
        tail = "нет" if sc.open_tail is None else f"{sc.open_tail.bars_open} баров"
        w = [z.width_pct for a in sc.closed for z in (a.upper, a.lower)]
        med = sorted(w)[len(w) // 2] if w else 0.0
        print(f"{s:16} {tf:4} структур {len(sc.closed):3} распадов {sc.resets:2} "
              f"хвост {tail:10} тренд {tr.direction.value:5}({tr.holds_for}) "
              f"медиана ширины границы {med:.2f}%")


async def pereprior_counts() -> None:
    """pereprior: истинные против ранних, тесты, доп-факторы."""
    data = await _bars(SYMS, TFS)
    c: Counter[str] = Counter()
    for (s, tf), bars in data.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        for pp in pereprior.detect(bars, sw):
            c[pp.kind.value] += 1
            c["с тестом"] += pp.tested_at_index is not None
            c["вырожденная зона"] += pp.zone_degenerate
            print(f"{s:16} {tf:4} {pp.kind.value:5}/{pp.side.value:5} "
                  f"слом {pp.broken_price:>10.4f}")
        c["доп-фактор"] += len(pereprior.failed_update(bars, sw))
    print(f"\nпар символ/ТФ {len(data)}: {dict(c)}")


async def breach_kinds() -> None:
    """breach, falsifiability: исходы событий на границах структур (стр. 55)."""
    data = await _bars(SYMS, TFS)
    c: Counter[str] = Counter()
    for (_s, tf), bars in data.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        for a in accumulation.detect(bars, sw, tf).closed:
            for edge, d in ((a.upper.hi, breach.Direction.ABOVE),
                            (a.lower.lo, breach.Direction.BELOW)):
                e = breach.first_breach(bars, edge, d, from_index=a.last_index + 1)
                c[e.kind.value if e else "заходов не было"] += 1
    print(f"пар {len(data)}, проверок {sum(c.values())}: {dict(c)}")


async def factor_coverage() -> None:
    """falsifiability: способны ли доп-факторы §2.9 выдать оба исхода."""
    data = await _bars(SYMS, TFS)
    kinds: Counter[str] = Counter()
    sq: list[float] = []
    for (_s, _tf), bars in data.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        for name, expr in (("rsi", indicators.rsi()), ("macd", indicators.macd_line())):
            for d in factors.divergences(bars, sw, _series(bars, expr), name):
                kinds[f"{name}:{d.kind.value}"] += 1
        z = factors.squeeze(_series(bars, indicators.bbands_upper()),
                            _series(bars, indicators.bbands_lower()),
                            _series(bars, indicators.ema(20)))
        if z:
            sq.append(z.percentile)
    print(f"пар {len(data)}, дивергенции/конвергенции: {dict(kinds)}")
    print(f"сужение посчитано {len(sq)} из {len(data)}, "
          f"перцентили {min(sq):.0f}…{max(sq):.0f}%" if sq else "сужение: ни разу")


async def convergence() -> None:
    """figures §2.6: доля сходящихся структур по ВСЕМ фракталам окна.

    ⚠ Считать по зачтённым точкам границы НЕЛЬЗЯ — детектор их не принимает по
    построению, и получится тавтологический ноль (первая редакция зонда так и дала).
    """
    data = await _bars(SYMS, ("15m", "1h", "4h"))
    c: Counter[str] = Counter()
    for (_s, tf), bars in data.items():
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        for a in accumulation.detect(bars, sw, tf).closed:
            inside = [x for x in sorted(sw.swings, key=lambda z: z.index)
                      if a.first_index <= x.index <= a.last_index]
            hs = [x.price for x in inside if x.kind is SwingKind.HIGH]
            ls = [x.price for x in inside if x.kind is SwingKind.LOW]
            if len(hs) < 3 or len(ls) < 3:
                c["мало фракталов"] += 1
                continue
            de = all(hs[i] < hs[i - 1] for i in range(1, len(hs)))
            asc = all(ls[i] > ls[i - 1] for i in range(1, len(ls)))
            c["обе стороны" if de and asc else "одна сторона" if de or asc else "нет"] += 1
    print(f"пар {len(data)}: {dict(c)}")


async def author_coverage() -> None:
    """author-markup: накрывают ли структуры уровни автора (без тиков, только бары)."""
    data = await _bars(SYMS, ("4h",))
    for (s, tf), bars in data.items():
        lines = AUTHOR[s]
        sw = swings.detect(bars)
        if isinstance(sw, NotReady):
            continue
        sc = accumulation.detect(bars, sw, tf)
        covered = sum(1 for L in lines
                      if any(a.lower.lo <= L <= a.upper.hi for a in sc.closed))
        edges = [e for a in sc.closed for e in (a.lower.lo, a.upper.hi)]
        on_edge = sum(1 for L in lines
                      if edges and abs(min(edges, key=lambda x: abs(x - L)) - L) / L * 100 < NOISE)
        print(f"{s:16} {tf}: структур {len(sc.closed)}, уровней автора {len(lines)}, "
              f"накрыто {covered}, на границе {on_edge}")


async def author_poc() -> None:
    """author-markup: ПОК моих структур против уровней автора, по ВСЕМ ТФ.

    Это сверка по существу: `author-coverage` показывает лишь, что структура накрывает
    цену уровня, а линии автора — ПОК ВНУТРИ структур, не их края.

    Первый прогон качает архив (~3.4 ГБ, порядка получаса), дальше читает кэш.
    """
    horizon_days = 90
    ex = Exchange()
    await ex.open()
    try:
        for s in SYMS:
            inst = ex.instrument(s)
            if isinstance(inst, NotReady):
                print(f"{s}: {inst.reason}")
                continue
            lines = AUTHOR[s]
            series: dict[str, list[Bar]] = {}
            for tf in TFS:
                f = await ex.fetch_closed_ohlcv(s, tf, limit=1000)
                if not isinstance(f, NotReady):
                    series[tf] = f.bars

            now = max(b[-1].open_ms for b in series.values())
            cut = now - horizon_days * 86400000
            todo: list[tuple[str, accumulation.Accumulation, int, int]] = []
            days: set[date] = set()
            for tf, bars in series.items():
                sw = swings.detect(bars)
                if isinstance(sw, NotReady):
                    continue
                for a in accumulation.detect(bars, sw, tf).closed:
                    lo, hi = levels.structure_window_ms(a, bars, TIMEFRAME_MS[tf])
                    if hi < cut:
                        continue
                    todo.append((tf, a, lo, hi))
                    d = datetime.fromtimestamp(lo / 1000, UTC).date()
                    end = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
                    while d <= end:
                        days.add(d)
                        d += timedelta(days=1)

            print(f"\n=== {s}: структур в горизонте {horizon_days} сут — {len(todo)}, "
                  f"суток архива — {len(days)}")
            def pull(d: date, mid: str = inst.market_id, tk: Decimal = inst.tick_size
                     ) -> tuple[date, pl.DataFrame | None]:
                return d, _cached_day(mid, d, tk)

            with ThreadPoolExecutor(max_workers=4) as pool:
                pulled = list(pool.map(pull, sorted(days)))
            frames = {d: f for d, f in pulled if f is not None}
            missing = sorted(d for d, f in pulled if f is None)
            if missing:
                print(f"  ⚠ суток не получено: {len(missing)} — {missing[:5]}")

            built, states, skipped = _levels_over(
                s, inst.tick_size, todo, frames, series)
            pocs = [(x.timeframe, float(x.price), n, t)
                    for x, n, t, active in built if active]
            all_pocs = [(x.timeframe, float(x.price), n, t) for x, n, t, _ in built]
            # ⚠ Отработанные и флипнутые уровни в карту не идут: стр. 25 — «уровень был
            # отработан на 1 касание… мы этот уровень удаляем». Без этого фильтра ПОК
            # получается вчетверо больше, чем публикует автор, и сверка вырождается:
            # ближайший ПОК находится всегда, и совпадение перестаёт что-либо значить.
            print(f"  ПОК посчитано {len(all_pocs)}, из них АКТИВНЫХ {len(pocs)} "
                  f"(отсеяно {dict(states)})"
                  + (f", пропущено {dict(skipped)}" if skipped else ""))
            if not pocs:
                continue

            def match(level: float, found: list[tuple[str, float, float, int]] = pocs
                      ) -> tuple[bool, str, float, float]:
                tf_, p_, noise_, _ = min(found, key=lambda r: abs(r[1] - level))
                d = (p_ - level) / level * 100
                return abs(d) <= noise_, tf_, p_, noise_

            hit = 0
            for L in lines:
                ok, tf, p, noise = match(L)
                hit += ok
                print(f"  {L:>9.1f} ← ПОК {p:>10.4f} {tf:>3} "
                      f"{(p - L) / L * 100:+7.3f}% допуск ±{noise:.3f}% "
                      f"{'СОШЁЛСЯ' if ok else ''}")
            print(f"  ИТОГО {s}: сошлось {hit} из {len(lines)}")

            # ⚠ КОНТРОЛЬ. Без него «сошлось 10 из 16» не является результатом: при 74
            # ПОК и допусках до 1.4% совпасть может и произвольная цена. Сравнение идёт
            # с РЕШЁТКОЙ равноотстоящих уровней в том же диапазоне — она детерминирована
            # (§10.3 запрещает случайность) и не подстроена ни под ПОК, ни под автора.
            def dist(level: float, found: list[tuple[str, float, float, int]] = pocs
                     ) -> float:
                return min(abs(r[1] - level) / level * 100 for r in found)

            def med(xs: list[float]) -> float:
                return sorted(xs)[len(xs) // 2]

            band_lo, band_hi = min(lines), max(lines)
            real = med([dist(x) for x in lines])
            print(f"  у автора: сошлось {hit / len(lines) * 100:.0f}%, "
                  f"медиана расстояния до ближайшего ПОК {real:.3f}%")
            for k in (len(lines), len(lines) * 4):
                grid = [band_lo + (band_hi - band_lo) * i / (k - 1) for i in range(k)]
                n_ok = sum(match(x)[0] for x in grid)
                print(f"  контроль-решётка {k}: сошлось {n_ok / k * 100:.0f}%, "
                      f"медиана {med([dist(x) for x in grid]):.3f}%")

            # ⚠ ГЛАВНЫЙ КОНТРОЛЬ — сдвиг. Решётка не сохраняет ни числа уровней, ни их
            # расстановки, поэтому сравнение с ней слабое. Сдвиг ВСЕЙ разметки автора на
            # k% сохраняет и то и другое: если исходное положение лучше сдвинутых, дело
            # в самих ценах, а не в том, что их много и они в нужном диапазоне.
            shifts = [i / 10 for i in range(-30, 31) if abs(i) >= 3]
            worse = sum(med([dist(x * (1 + k / 100)) for x in lines]) > real for k in shifts)
            best = min((med([dist(x * (1 + k / 100)) for x in lines]), k) for k in shifts)
            print(f"  контроль-сдвиг: исходное положение лучше {worse} из {len(shifts)} "
                  f"сдвигов на ±0.3…3.0%; лучший сдвиг {best[1]:+.1f}% даёт {best[0]:.3f}%")
    finally:
        await ex.close()


async def ladder() -> None:
    """§2.2 стр. 32: срабатывает ли лестница вложенных уровней и какого она размера.

    Поле, у которого всегда одно значение, инертно — это проверка на то, что `nested`
    вообще что-то находит, а не украшает модель. Сравнение — с составом зон автора:
    он публикует «63680-63950-64360» и «1935-1945-1960», то есть по 2-3 линии.
    """
    ex = Exchange()
    await ex.open()
    try:
        for s in SYMS:
            built, _states, _skipped, n_struct = await _archive_levels(ex, s)
            pool = tuple(x for x, _n, _t, _a in built)
            print(f"\n=== {s}: структур {n_struct}, уровней {len(pool)}")
            for steps, label in ((1, "ТФ-1 (по стр. 32/24)"), (4, "ВСЕ младшие ТФ")):
                sizes: Counter[int] = Counter()
                by_tf: Counter[str] = Counter()
                examples: list[str] = []
                for lvl in pool:
                    inner = levels.nested(lvl, pool, max_steps=steps)
                    sizes[1 + len(inner)] += 1
                    if inner:
                        by_tf[lvl.timeframe] += 1
                        if steps == 1 and len(examples) < 3 and len(inner) >= 2:
                            rung = " · ".join(
                                f"{x:.6g}" for x in
                                sorted({lvl.price, *(i.price for i in inner)}))
                            examples.append(f"{lvl.timeframe:>3} {rung}")
                total = sum(sizes.values())
                multi = total - sizes[1]
                big = sum(n for k, n in sizes.items() if k > 4)
                print(f"  {label:22}: с вложенными {multi} ({multi / total * 100:.0f}%), "
                      f"длиннее 4 ступеней — {big}, размеры {dict(sorted(sizes.items()))}")
                print(f"  {'':22}  родители по ТФ: {dict(by_tf)}")
                for e in examples:
                    print(f"  {'':22}  пример: {e}")
    finally:
        await ex.close()


async def states() -> None:
    """§2.10: встречается ли КАЖДОЕ состояние и КАЖДОЕ правило входа.

    Значение перечисления, которого код не выдаёт ни разу, — свойство кода, а не рынка.
    До этого замера `EntryRule.RETEST_FLIPPED` мог бы никогда не возникнуть, и заметить
    это было бы нечем: карточка печатала бы оставшиеся два и выглядела исправной.
    """
    ex = Exchange()
    await ex.open()
    try:
        for s in SYMS:
            built, _st, _sk, n_struct = await _archive_levels(ex, s)
            by_state: Counter[str] = Counter()
            by_rule: Counter[str] = Counter()
            pairs: Counter[str] = Counter()
            series: dict[str, list[Bar]] = {}
            for tf in TFS:
                f = await ex.fetch_closed_ohlcv(s, tf, limit=1000)
                if not isinstance(f, NotReady):
                    series[tf] = f.bars
            for lvl, _n, _t, _a in built:
                st = levels.status(lvl, series[lvl.timeframe])
                by_state[st.state.value] += 1
                by_rule[st.entry_rule.value] += 1
                pairs[f"{st.state.value}/{st.entry_rule.value}"] += 1
            print(f"\n=== {s}: структур {n_struct}, уровней {len(built)}")
            print(f"  состояния:      {dict(by_state)}")
            print(f"  правила входа:  {dict(by_rule)}")
            missing = [r.value for r in levels.EntryRule if r.value not in by_rule]
            print(f"  НЕ ВСТРЕТИЛОСЬ: {missing or 'нет — все значения выданы'}")
            print(f"  пары:           {dict(pairs)}")
    finally:
        await ex.close()


async def map_drift() -> None:
    """Сколько уровней исчезает из карты по НЕВЕРНОЙ причине — уход окна баров.

    Курс знает ровно одну причину убрать уровень: стр. 25 — «уровень был отработан на 1
    касание… мы этот уровень удаляем». Моя карта строится заново каждый прогон из
    последних 1000 баров, поэтому есть и вторая, которой в курсе нет: структура просто
    уехала за край окна. Различить их можно только замером — снаружи обе выглядят как
    «уровня больше нет».

    Замер: два окна по 800 баров, сдвинутые на 200. Уровень из раннего окна ищется в
    позднем по (ТФ, сторона, цена в пределах шага цены).
    """
    shift, min_bars = 200, 400
    # ⚠ Ширина окна считается ОТ ФАКТИЧЕСКОЙ длины ряда, а не берётся константой.
    # Первая редакция ставила `width = 800` и проверяла `len(bars) < shift + width`:
    # `fetch_closed_ohlcv(limit=1000)` отдаёт 999 баров (незакрытый отброшен по §6), и
    # условие `999 < 1000` пропускало ВСЕ ТФ. Зонд напечатал «уровней 0» и выглядел
    # исправным — ровно тот ноль с правдоподобным видом, про который CLAUDE.md.
    ex = Exchange()
    await ex.open()
    try:
        for s in SYMS:
            inst = ex.instrument(s)
            if isinstance(inst, NotReady):
                continue
            gone: Counter[str] = Counter()
            kept = 0
            for tf in TFS:
                f = await ex.fetch_closed_ohlcv(s, tf, limit=1000)
                if isinstance(f, NotReady) or len(f.bars) < shift + min_bars:
                    continue
                width = len(f.bars) - shift
                early, late = f.bars[:width], f.bars[shift:shift + width]
                maps: list[list[tuple[levels.Level, list[Bar]]]] = []
                for bars in (early, late):
                    sw = swings.detect(bars)
                    if isinstance(sw, NotReady):
                        maps.append([])
                        continue
                    todo = []
                    days: set[date] = set()
                    for a in accumulation.detect(bars, sw, tf).closed:
                        lo, hi = levels.structure_window_ms(a, bars, TIMEFRAME_MS[tf])
                        todo.append((tf, a, lo, hi))
                        d = datetime.fromtimestamp(lo / 1000, UTC).date()
                        end = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
                        while d <= end:
                            days.add(d)
                            d += timedelta(days=1)
                    frames = {}
                    for d in sorted(days):
                        path = CACHE / f"{inst.market_id}-{d.isoformat()}-{BUCKET_MS}.parquet"
                        if path.exists():
                            frames[d] = pl.read_parquet(path)
                    built, _st, sk = _levels_over(
                        s, inst.tick_size, todo, frames, {tf: bars})
                    if not built:
                        print(f"  ⚠ {tf}: структур {len(todo)}, суток нужно {len(days)}, "
                              f"в кэше {len(frames)}, уровней 0 — {dict(sk)}")
                    maps.append([(x, bars) for x, _n, _t, _a in built])
                early_map, late_map = maps
                late_prices = {(x.timeframe, x.side, round(float(x.price), 8))
                               for x, _b in late_map}
                cutoff = late[0].open_ms
                for lvl, _b in early_map:
                    if (lvl.timeframe, lvl.side, round(float(lvl.price), 8)) in late_prices:
                        kept += 1
                        continue
                    st = levels.status(lvl, early)
                    if st.state is not levels.LevelState.ACTIVE:
                        gone[f"по курсу: {st.state.value}"] += 1
                    elif lvl.structure_from_ms < cutoff:
                        gone[f"ОКНО УЕХАЛО ({tf})"] += 1
                    else:
                        gone[f"необъяснённо ({tf})"] += 1
            total = kept + sum(gone.values())
            wrong = sum(v for k, v in gone.items() if k.startswith("ОКНО"))
            print(f"\n=== {s}: уровней в раннем окне {total}, дожило {kept}")
            print(f"  исчезло: {dict(gone)}")
            print(f"  ПО НЕВЕРНОЙ ПРИЧИНЕ (окно уехало): {wrong}"
                  + (f" — {wrong / total * 100:.0f}% карты" if total else ""))
    finally:
        await ex.close()


async def archive_need() -> None:
    """map-depth, data-channels: сколько суток архива нужно при разных горизонтах."""
    mb = {"BTC/USDT:USDT": 15.5, "ETH/USDT:USDT": 6.0}
    data = await _bars(SYMS, TFS)
    for s in SYMS:
        lines = AUTHOR[s]
        now = max(b[-1].open_ms for (sym, _), b in data.items() if sym == s)
        print(f"\n{s}")
        for horizon in (40, 60, 90, None):
            cut = now - horizon * 86400000 if horizon else 0
            need: set[date] = set()
            n = 0
            covered: set[float] = set()
            for (sym, tf), bars in data.items():
                if sym != s:
                    continue
                sw = swings.detect(bars)
                if isinstance(sw, NotReady):
                    continue
                for a in accumulation.detect(bars, sw, tf).closed:
                    lo, hi = levels.structure_window_ms(a, bars, TIMEFRAME_MS[tf])
                    if hi < cut:
                        continue
                    hits = [L for L in lines if a.lower.lo <= L <= a.upper.hi]
                    if not hits:
                        continue
                    n += 1
                    covered |= set(hits)
                    d = datetime.fromtimestamp(lo / 1000, UTC).date()
                    end = datetime.fromtimestamp(hi / 1000, UTC).date()
                    while d <= end:
                        need.add(d)
                        d += timedelta(days=1)
            name = f"{horizon} сут" if horizon else "без границы"
            print(f"  {name:>12}: структур {n:3}, суток {len(need):3}, "
                  f"{len(need)*mb[s]:6.0f} МБ, уровней накрыто {len(covered)} из {len(lines)}")


async def rest_history() -> None:
    """data-channels: что REST покрывает сам, а где нужен архив.

    Отвечает на вопрос владельца 2026-08-04: «нужны ли нам вообще запросы к data, если
    rest покрывает необходимый набор свечей даже на дневном таймфрейме?» Ответ разный
    для БАРОВ и для СДЕЛОК, и здесь он меряется, а не выводится.
    """
    import ccxt.pro as ccxtpro
    ex = ccxtpro.binanceusdm({"enableRateLimit": True})
    await ex.load_markets()
    try:
        print("БАРЫ — REST, архив не нужен:")
        for tf in ("1d", "4h"):
            t0 = time.perf_counter()
            b = await ex.fetch_ohlcv("BTC/USDT:USDT", tf, limit=1000)
            el = time.perf_counter() - t0
            print(f"  {tf:>3}: {len(b)} баров за {el:.2f} с, "
                  f"охват {(b[-1][0] - b[0][0]) / 86400000:.0f} суток")

        print("\nСДЕЛКИ — REST историю ОТДАЁТ, но окно структуры им не закрыть:")
        for name, dt in (("18 суток назад", datetime(2026, 7, 17, 6, 15, tzinfo=UTC)),
                         ("трое суток назад", datetime(2026, 8, 1, tzinfo=UTC))):
            since = int(dt.timestamp() * 1000)
            t0 = time.perf_counter()
            tr = await ex.fetch_trades("BTC/USDT:USDT", since=since, limit=1000)
            el = time.perf_counter() - t0
            span = (tr[-1]["timestamp"] - tr[0]["timestamp"]) / 1000 if tr else 0
            print(f"  {name:20} сделок {len(tr)} за {el:.2f} с, охват рынка {span:.1f} с")

        # Цена ОДНОГО окна структуры: 8 страниц замеряются, остальное экстраполируется.
        lo = int(datetime(2026, 7, 17, 6, 15, tzinfo=UTC).timestamp() * 1000)
        hi = int(datetime(2026, 7, 17, 17, 0, tzinfo=UTC).timestamp() * 1000)
        cur, pages, got, t0 = lo, 0, 0, time.perf_counter()
        while pages < 8:
            tr = await ex.fetch_trades("BTC/USDT:USDT", since=cur, limit=1000)
            if not tr or tr[-1]["timestamp"] + 1 <= cur:
                break
            pages, got, cur = pages + 1, got + len(tr), tr[-1]["timestamp"] + 1
        el = time.perf_counter() - t0
        est = (hi - lo) / (cur - lo) * pages
        print(f"\n  окно структуры BTC 15м 17.07 ({(hi - lo) / 3600000:.1f} ч):")
        print(f"    замерено {pages} страниц, {got} сделок за {el:.2f} с "
              f"(покрыто {(cur - lo) / 1000:.0f} с рынка)")
        print(f"    на ВСЁ окно ≈ {est:.0f} запросов ≈ {est * el / pages / 60:.0f} мин")
        print("    те же сутки одним ZIP: 15.5 МБ за 5.3 с")
    finally:
        await ex.close()


async def bar_vs_tick() -> None:
    """map-depth: ПОК из тиков против ПОК из 1м-баров на структуре BTC 17.07."""
    tick = Decimal("0.1")
    lo = int(datetime(2026, 7, 17, 6, 15, tzinfo=UTC).timestamp() * 1000)
    hi = int(datetime(2026, 7, 17, 16, 0, tzinfo=UTC).timestamp() * 1000)
    key = 62837.3

    fr = _fetch_day("BTCUSDT", date(2026, 7, 17), tick, 300_000)
    if fr is None:
        return
    w = fr.filter((pl.col("bucket") >= lo) & (pl.col("bucket") < hi)) \
          .group_by("bin").agg(pl.col("qty").sum())
    tick_poc = float(Decimal(int(w.filter(pl.col("qty") == w["qty"].max())["bin"][0])) * tick)
    print(f"тики: ПОК {tick_poc:.1f}, к уровню автора {(tick_poc-key)/key*100:+.3f}% "
          f"[{'в шуме' if abs((tick_poc-key)/key*100) < NOISE else 'вне шума'}]")

    url = f"{ARCHIVE}/klines/BTCUSDT/1m/BTCUSDT-1m-2026-07-17.zip"
    with urllib.request.urlopen(url, timeout=300) as r:
        blob = bytes(r.read())
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        kl = pl.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    kw = kl.filter((pl.col("open_time") >= lo) & (pl.col("open_time") < hi))
    print(f"баров 1м в окне {kw.height}, размер суток klines {len(blob)/1e6:.3f} МБ")
    for how in ("close", "spread"):
        bins: dict[int, float] = {}
        for r2 in kw.iter_rows(named=True):
            v = float(r2["volume"])
            if how == "close":
                b = int(Decimal(str(r2["close"])) // tick)
                bins[b] = bins.get(b, 0.0) + v
            else:
                a = int(Decimal(str(r2["low"])) // tick)
                b2 = int(Decimal(str(r2["high"])) // tick)
                for k in range(a, b2 + 1):
                    bins[k] = bins.get(k, 0.0) + v / (b2 - a + 1)
        peak = max(bins.values())
        p = float(Decimal(min(b for b, v in bins.items() if v == peak)) * tick)
        dev = (p - key) / key * 100
        print(f"бары ({how}): ПОК {p:.1f}, к автору {dev:+.3f}% "
              f"[{'в шуме' if abs(dev) < NOISE else 'вне шума'}]")


PROBES = {
    "structures": structures,
    "pereprior": pereprior_counts,
    "breach": breach_kinds,
    "factors": factor_coverage,
    "convergence": convergence,
    "author-coverage": author_coverage,
    "author-poc": author_poc,
    "ladder": ladder,
    "states": states,
    "map-drift": map_drift,
    "archive-need": archive_need,
    "rest-history": rest_history,
    "bar-vs-tick": bar_vs_tick,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PROBES:
        print("зонды:")
        for k, f in PROBES.items():
            doc = (f.__doc__ or "").splitlines()[0]
            print(f"  {k:18} {doc}")
        return 2
    asyncio.run(PROBES[sys.argv[1]]())
    return 0


if __name__ == "__main__":
    sys.exit(main())
