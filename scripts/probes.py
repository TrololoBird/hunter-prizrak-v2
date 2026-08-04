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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import polars as pl

from hunter import accumulation, breach, factors, indicators, levels, pereprior, swings
from hunter.bars import TIMEFRAME_MS
from hunter.exchange import Exchange
from hunter.models import Bar, NotReady
from hunter.swings import SwingKind

ARCHIVE = "https://data.binance.vision/data/futures/um/daily"
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT")
TFS = ("5m", "15m", "1h", "4h", "1d")

# Уровни, снятые с кадров автора 03.08.2026 — research/author_markup/2026-08-03_overcarder.md
AUTHOR = {
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
