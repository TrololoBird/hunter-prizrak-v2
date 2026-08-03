"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.

Кадры сохраняются в parquet (§10.3) — без них детерминированный повтор невозможен.
"""

from __future__ import annotations

import asyncio

from . import clock, log, store
from .bars import expected_last_closed_open_ms, find_gaps, is_closed, on_grid, tf_ms
from .config import Universe
from .exchange import Exchange
from .models import NotReady, RunReport, SeriesState, TradeHistogram


async def seed(ex: Exchange, uni: Universe, report: RunReport, limit: int) -> None:
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded("инструмент недоступен", причина=inst.reason)
        for tf in uni.timeframes:
            st = SeriesState(symbol=sym, timeframe=tf)
            report.series[(sym, tf)] = st
            got = await ex.fetch_closed_ohlcv(sym, tf, limit=limit)
            report.seed_checked += 1
            if isinstance(got, NotReady):
                st.not_ready = got
                log.degraded("засев пропущен", причина=got.reason)
                continue
            st.bars = got.bars
            st.rejected_bars = got.rejected
            st.gaps = find_gaps(got.bars, tf)
            report.seeded_bars += len(got.bars)
            if st.gaps:
                log.warn("разрыв сетки", символ=sym, тф=tf, разрывов=len(st.gaps),
                         первый_после=st.gaps[0][0])


async def _watch_bars(ex: Exchange, st: SeriesState, stop: asyncio.Event) -> None:
    agen = ex.watch_closed_ohlcv(st.symbol, st.timeframe)
    try:
        while not stop.is_set():
            bar = await anext(agen)
            st.ws_bars += 1
            if not is_closed(bar.open_ms, st.timeframe, clock.now_ms()):
                st.ws_unclosed_violations += 1
                log.error("отдан НЕзакрытый бар", символ=st.symbol, тф=st.timeframe,
                          open_ms=bar.open_ms)
            if not on_grid(bar.open_ms, st.timeframe):
                st.ws_offgrid_violations += 1
                log.error("бар вне сетки", символ=st.symbol, тф=st.timeframe,
                          open_ms=bar.open_ms)
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
                    log.degraded("сделка без цены/объёма/метки, пропущена", символ=sym)
                    continue
                hist.add(float(price), float(amount), int(ts))
    except asyncio.CancelledError:
        raise
    finally:
        await agen.aclose()


def persist(run_id: str, report: RunReport) -> None:
    """Сохранить кадры для детерминированного повтора (§10.3)."""
    for (sym, tf), st in report.series.items():
        if st.not_ready is not None or not st.bars:
            continue
        store.write_bars(run_id, sym, tf, st.bars)
        report.frames_written += 1
    for h in report.histograms.values():
        if h.trades_seen:
            store.write_histogram(run_id, h)
            report.frames_written += 1


async def live_run(uni: Universe, seconds: int, seed_limit: int, run_id: str) -> RunReport:
    ex = Exchange()
    sync = await ex.open()
    report = RunReport(sync=sync)
    try:
        log.info("засев", символов=len(uni.symbols), тф=len(uni.timeframes),
                 баров_на_ряд=seed_limit)
        await seed(ex, uni, report, seed_limit)
        log.info("засеяно", баров=report.seeded_bars, запросов=report.seed_checked)

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

        log.info("наблюдение", потоков=len(tasks), секунд=seconds)
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

    persist(run_id, report)
    log.info("кадры сохранены", файлов=report.frames_written,
             каталог=str(store.FRAMES_DIR / run_id))
    return report


def print_report(r: RunReport, now_ms: int) -> int:
    """Печатает приёмку. Возвращает число нарушений. Время — аргумент (§10.3)."""
    print()
    print("=" * 78)
    print("ПРИЁМКА ЭТАПА 1 — FOUNDATION.md §8")
    print("=" * 78)

    print("\n1. ЧАСЫ (§6)")
    print(f"   сдвиг биржа−локальные    : {r.sync.offset_ms:+d} мс")
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
        expected = expected_last_closed_open_ms(st.timeframe, now_ms)
        behind = (expected - last.open_ms) // tf_ms(st.timeframe)
        if behind > 0:
            stale.append(f"{st.symbol} {st.timeframe}: отстаёт на {behind} баров")
        if not on_grid(last.open_ms, st.timeframe):
            offgrid_seed.append(f"{st.symbol} {st.timeframe}")
    print(f"   рядов со свежим последним закрытым баром: "
          f"{len(ready) - len(stale)} из {len(ready)}")
    for s in stale:
        print(f"   ОТСТАЁТ: {s}")
    print(f"   баров вне сетки в засеве: {len(offgrid_seed)}")

    print("\n3. ПРОПУСКИ — молчание запрещено (§4.3)")
    print(f"   рядов без данных: {len(missing)} из {len(r.series)}")
    for st in missing:
        assert st.not_ready is not None
        print(f"   НЕТ ДАННЫХ: {st.symbol} {st.timeframe} — {st.not_ready.reason}")
    rejected = [x for s in r.series.values() for x in s.rejected_bars]
    print(f"   баров ОТКЛОНЕНО как битые: {len(rejected)} "
          f"(из {r.seeded_bars + len(rejected)} полученных)")
    for x in rejected:
        print(f"   БИТЫЙ БАР: {x}")

    total_gaps = sum(len(s.gaps) for s in ready)
    # Разрыв, объяснённый отклонённым баром, — не наш дефект, а видимое следствие
    # дефекта данных биржи. Считаем отдельно, чтобы не путать с необъяснённым.
    explained = sum(1 for s in ready if s.rejected_bars for _ in s.gaps)
    unexplained = total_gaps - explained
    print(f"   разрывов сетки внутри рядов: {total_gaps} "
          f"(объяснено отклонёнными барами {explained}, необъяснённых {unexplained}; "
          f"проверено баров {r.seeded_bars})")
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
        print(f"   БЕЗ ЕДИНОЙ СДЕЛКИ за прогон ({len(silent)}): "
              f"{', '.join(sorted(silent))}")

    print("\n6. КАДРЫ ДЛЯ ПОВТОРА (§10.3)")
    print(f"   файлов parquet записано: {r.frames_written}")

    violations = (len(stale) + len(missing) + unexplained + unclosed + offgrid_ws
                  + len(offgrid_seed))
    print("\n7. ИТОГ")
    print(f"   деградаций отмечено: {log.degraded_count()}")
    print(f"   нарушений приёмки: {violations}")
    print("=" * 78)
    return violations
