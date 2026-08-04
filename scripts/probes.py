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

            pocs: list[tuple[str, float, float, int]] = []
            all_pocs: list[tuple[str, float, float, int]] = []
            states: Counter[str] = Counter()
            skipped: Counter[str] = Counter()
            for tf, a, lo, hi in todo:
                d0 = datetime.fromtimestamp(lo / 1000, UTC).date()
                d1 = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
                want = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
                if any(d not in frames for d in want):
                    skipped[f"{tf}: суток нет в кэше"] += 1
                    continue
                span = [frames[d] for d in want]
                hist = _histogram_over(span, s, inst.tick_size, lo, hi)
                if isinstance(hist, NotReady):
                    skipped[f"{tf}: {hist.reason.split(':')[-1].strip()}"] += 1
                    continue
                lvl = levels.build_level(a, hist, s)
                if isinstance(lvl, NotReady):
                    skipped[f"{tf}: ПОК не построен"] += 1
                    continue
                p = float(lvl.price)
                st = levels.status(lvl, series[tf])
                all_pocs.append((tf, p, _resolution_noise(hist, p), hist.trades_seen))
                if st.state is levels.LevelState.ACTIVE:
                    pocs.append((tf, p, _resolution_noise(hist, p), hist.trades_seen))
                else:
                    states[st.state.value] += 1
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
    """data-channels: отдаёт ли REST исторические сделки и с какой скоростью."""
    import ccxt.pro as ccxtpro
    ex = ccxtpro.binanceusdm({"enableRateLimit": True})
    await ex.load_markets()
    try:
        for name, dt in (("18 суток назад", datetime(2026, 7, 17, 6, 15, tzinfo=UTC)),
                         ("трое суток назад", datetime(2026, 8, 1, tzinfo=UTC))):
            since = int(dt.timestamp() * 1000)
            t0 = time.perf_counter()
            tr = await ex.fetch_trades("BTC/USDT:USDT", since=since, limit=1000)
            el = time.perf_counter() - t0
            span = (tr[-1]["timestamp"] - tr[0]["timestamp"]) / 1000 if tr else 0
            print(f"{name:20} сделок {len(tr)} за {el:.2f} с, охват рынка {span:.1f} с")
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
