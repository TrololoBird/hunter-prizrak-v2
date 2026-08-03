"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import pairwise

from . import clock, log
from .bars import Bar, expected_last_closed_open_ms, is_closed, on_grid, tf_ms
from .config import Universe
from .exchange import Exchange
from .profile import TradeHistogram
from .quality import NotReady


@dataclass(slots=True)
class SeriesState:
    symbol: str
    timeframe: str
    bars: list[Bar] = field(default_factory=list)
    gaps: list[tuple[int, int]] = field(default_factory=list)
    """Пары (open_ms предыдущего, open_ms следующего) там, где сетка разорвана."""

    not_ready: NotReady | None = None
    ws_bars: int = 0
    ws_unclosed_violations: int = 0
    ws_offgrid_violations: int = 0


@dataclass(slots=True)
class RunReport:
    sync: clock.ClockSync
    series: dict[tuple[str, str], SeriesState] = field(default_factory=dict)
    histograms: dict[str, TradeHistogram] = field(default_factory=dict)
    seeded_bars: int = 0
    seed_checked: int = 0
    clock_drift_ms: int | None = None
    clock_recheck_after_s: int | None = None


def find_gaps(bars: list[Bar], timeframe: str) -> list[tuple[int, int]]:
    step = tf_ms(timeframe)
    out: list[tuple[int, int]] = []
    for prev, cur in pairwise(bars):
        if cur.open_ms - prev.open_ms != step:
            out.append((prev.open_ms, cur.open_ms))
    return out


async def seed(ex: Exchange, uni: Universe, report: RunReport, limit: int) -> None:
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded(f"инструмент недоступен — {inst.reason}")
        for tf in uni.timeframes:
            st = SeriesState(sym, tf)
            report.series[(sym, tf)] = st
            got = await ex.fetch_closed_ohlcv(sym, tf, limit=limit)
            report.seed_checked += 1
            if isinstance(got, NotReady):
                st.not_ready = got
                log.degraded(f"засев пропущен — {got.reason}")
                continue
            st.bars = got
            st.gaps = find_gaps(got, tf)
            report.seeded_bars += len(got)
            if st.gaps:
                log.warn(f"{sym} {tf}: разрывов сетки {len(st.gaps)}, первый после "
                         f"{st.gaps[0][0]}")


async def _watch_bars(ex: Exchange, st: SeriesState, stop: asyncio.Event) -> None:
    agen = ex.watch_closed_ohlcv(st.symbol, st.timeframe)
    try:
        while not stop.is_set():
            bar = await anext(agen)
            st.ws_bars += 1
            if not is_closed(bar.open_ms, st.timeframe, clock.now_ms()):
                st.ws_unclosed_violations += 1
                log.error(f"{st.symbol} {st.timeframe}: отдан НЕзакрытый бар {bar.open_ms}")
            if not on_grid(bar.open_ms, st.timeframe):
                st.ws_offgrid_violations += 1
                log.error(f"{st.symbol} {st.timeframe}: бар вне сетки {bar.open_ms}")
            st.bars.append(bar)
    except asyncio.CancelledError:
        raise
    finally:
        await agen.aclose()


async def _watch_trades(ex: Exchange, sym: str, hist: TradeHistogram,
                        stop: asyncio.Event) -> None:
    agen = ex.watch_agg_trades(sym)
    try:
        while not stop.is_set():
            batch = await anext(agen)
            for t in batch:
                price, amount, ts = t.get("price"), t.get("amount"), t.get("timestamp")
                if price is None or amount is None or ts is None:
                    log.degraded(f"{sym}: сделка без цены/объёма/метки, пропущена")
                    continue
                hist.add(float(price), float(amount), int(ts))
    except asyncio.CancelledError:
        raise
    finally:
        await agen.aclose()


async def live_run(uni: Universe, seconds: int, seed_limit: int) -> RunReport:
    ex = Exchange()
    sync = await ex.open()
    report = RunReport(sync=sync)
    try:
        log.info(f"засев: {len(uni.symbols)} символов × {len(uni.timeframes)} ТФ, "
                 f"по {seed_limit} баров")
        await seed(ex, uni, report, seed_limit)
        log.info(f"засеяно баров {report.seeded_bars}, запросов {report.seed_checked}")

        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        for st in report.series.values():
            tasks.append(asyncio.create_task(_watch_bars(ex, st, stop)))
        for sym in uni.symbols:
            inst = ex.instrument(sym)
            if isinstance(inst, NotReady):
                continue
            h = TradeHistogram(symbol=sym, tick_size=inst.tick_size)
            report.histograms[sym] = h
            tasks.append(asyncio.create_task(_watch_trades(ex, sym, h, stop)))

        log.info(f"потоков поднято {len(tasks)}; наблюдение {seconds} с")
        await asyncio.sleep(seconds)

        # Повторный замер часов — оценить, насколько сдвиг уползает (задача 1.3).
        before = sync.offset_ms
        again = await clock.measure(ex.fetch_server_ms)
        report.clock_drift_ms = again.offset_ms - before
        report.clock_recheck_after_s = seconds
        clock.set_sync(again)

        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await ex.close()
    return report


def print_report(uni: Universe, r: RunReport) -> int:
    """Печатает приёмку. Возвращает число нарушений."""
    now = clock.now_ms()
    print()
    print("=" * 78)
    print("ПРИЁМКА ЭТАПА 1 — FOUNDATION.md §8")
    print("=" * 78)

    print("\n1. ЧАСЫ (§6)")
    print(f"   сдвиг биржа−локальные : {r.sync.offset_ms:+d} мс")
    print(f"   неопределённость (±rtt/2): ±{r.sync.rtt_ms // 2} мс")
    print(f"   замеров: {r.sync.samples}")
    if r.clock_drift_ms is not None:
        print(f"   уход сдвига за {r.clock_recheck_after_s} с: {r.clock_drift_ms:+d} мс")

    ready = [s for s in r.series.values() if s.not_ready is None]
    missing = [s for s in r.series.values() if s.not_ready is not None]

    print(f"\n2. СВЕЖЕСТЬ КАДРОВ — проверено рядов {len(r.series)}")
    stale: list[str] = []
    offgrid_seed: list[str] = []
    for st in sorted(ready, key=lambda s: (s.timeframe, s.symbol)):
        last = st.bars[-1]
        age = now - (last.open_ms + tf_ms(st.timeframe))
        expected = expected_last_closed_open_ms(st.timeframe, now)
        behind = (expected - last.open_ms) // tf_ms(st.timeframe)
        if behind > 0:
            stale.append(f"{st.symbol} {st.timeframe}: отстаёт на {behind} баров")
        if not on_grid(last.open_ms, st.timeframe):
            offgrid_seed.append(f"{st.symbol} {st.timeframe}")
        _ = age
    print(f"   рядов со свежим последним закрытым баром: {len(ready) - len(stale)} из {len(ready)}")
    for s in stale:
        print(f"   ОТСТАЁТ: {s}")
    print(f"   баров вне сетки в засеве: {len(offgrid_seed)}")

    print("\n3. ПРОПУСКИ — молчание запрещено (§4.3)")
    print(f"   рядов без данных: {len(missing)} из {len(r.series)}")
    for st in missing:
        assert st.not_ready is not None
        print(f"   НЕТ ДАННЫХ: {st.symbol} {st.timeframe} — {st.not_ready.reason}")
    total_gaps = sum(len(s.gaps) for s in ready)
    print(f"   разрывов сетки внутри рядов: {total_gaps} (проверено баров {r.seeded_bars})")
    for st in ready:
        for a, b in st.gaps[:3]:
            print(f"   РАЗРЫВ: {st.symbol} {st.timeframe} между {a} и {b}")

    unclosed = sum(s.ws_unclosed_violations for s in r.series.values())
    offgrid_ws = sum(s.ws_offgrid_violations for s in r.series.values())
    ws_bars = sum(s.ws_bars for s in r.series.values())
    print("\n4. НЕЗАКРЫТАЯ СВЕЧА (§6)")
    print(f"   баров получено по WS: {ws_bars}")
    print(f"   из них незакрытых: {unclosed}")
    print(f"   из них вне сетки: {offgrid_ws}")

    print("\n5. СДЕЛКИ И ПРОФИЛЬ (§5)")
    print(f"   символов с потоком: {len(r.histograms)}")
    tot_tr = sum(h.trades_seen for h in r.histograms.values())
    worst = max((h.reconciliation_error() for h in r.histograms.values()), default=0.0)
    print(f"   сделок принято: {tot_tr}")
    print(f"   худшее расхождение «бины против сырья»: {worst:.3e}")
    for sym, h in sorted(r.histograms.items(), key=lambda kv: -kv[1].trades_seen)[:5]:
        print(f"   {sym:20} сделок {h.trades_seen:7d}  бинов {len(h.qty_by_bin):6d}  "
              f"tick {h.tick_size}")
    silent = [s for s, h in r.histograms.items() if h.trades_seen == 0]
    if silent:
        print(f"   БЕЗ ЕДИНОЙ СДЕЛКИ за прогон ({len(silent)}): {', '.join(sorted(silent))}")

    violations = len(stale) + len(missing) + total_gaps + unclosed + offgrid_ws + len(offgrid_seed)
    print("\n6. ИТОГ")
    print(f"   деградаций отмечено: {log.degraded_count()}")
    print(f"   нарушений приёмки: {violations}")
    print("=" * 78)
    return violations
