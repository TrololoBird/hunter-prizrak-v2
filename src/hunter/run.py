"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.

Кадры сохраняются в parquet (§10.3) — без них детерминированный повтор невозможен.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from . import card, clock, emit, levels, log, store, swings
from .archive import fetch_agg_trades_day
from .bars import TIMEFRAME_MS, expected_last_closed_open_ms, find_gaps, is_closed, on_grid, tf_ms
from .config import Universe
from .exchange import Exchange
from .models import BarBinnedTrades, NotReady, RunReport, SeriesState, TradeHistogram


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
                        binned: BarBinnedTrades, stop: asyncio.Event) -> None:
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
                binned.add(float(price), float(amount), int(ts))
    except asyncio.CancelledError:
        raise
    finally:
        await agen.aclose()


def backfill_trades(ex: Exchange, uni: Universe, report: RunReport, days: int) -> None:
    """Долить сделки из суточных архивов биржи под ИСТОРИЧЕСКИЕ окна структур.

    ⚠ Без этого шага уровней не бывает вовсе, и это не свойство рынка. Профиль по стр. 26
    натягивается на бары структуры, а структуры лежат в истории; живой поток покрывает
    только длительность прогона. Замер 2026-08-04 (3 символа, 120 с): найдено 200 структур
    и построено 0 уровней — 100% отказов «окно выходит за собранное».

    Архив ограничен `days` умышленно: суточный файл BTC — 15.5 МБ и 1.26 млн сделок,
    а структура на 1Н тянется на годы. Непокрытые окна остаются `NotReady` с названной
    причиной (§4.3), а не заполняются приблизительным профилем.
    """
    if days <= 0:
        return
    last_ms = max((st.bars[-1].open_ms for st in report.series.values() if st.bars),
                  default=0)
    if not last_ms:
        log.degraded("бэкфилл сделок пропущен: нет ни одного бара")
        return
    last_day = datetime.fromtimestamp(last_ms / 1000, UTC).date()
    wanted = [last_day - timedelta(days=k) for k in range(days)]

    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded("бэкфилл пропущен: нет инструмента", символ=sym)
            continue
        target = report.binned.get(sym)
        if target is None:
            bucket = min(TIMEFRAME_MS[tf] for tf in uni.timeframes)
            target = BarBinnedTrades(symbol=sym, tick_size=inst.tick_size, bucket_ms=bucket)
            report.binned[sym] = target
        for day in wanted:
            got = fetch_agg_trades_day(inst.market_id, day)
            if isinstance(got, NotReady):
                log.degraded("сутки архива не получены", символ=sym, дата=str(day),
                             причина=got.reason)
                report.backfill_days_missing += 1
                continue
            for r in got.frame.iter_rows(named=True):
                target.add(float(r["price"]), float(r["quantity"]), int(r["transact_time"]))
            report.backfill_days_loaded += 1
            report.backfill_trades += got.rows
        log.info("сделки долиты", символ=sym, корзин=len(target.qty),
                 сделок=target.trades_seen)


def persist(run_id: str, report: RunReport, uni: Universe) -> None:
    """Сохранить кадры И карточку для детерминированного повтора (§10.3).

    Карточка сохраняется рядом с кадрами, потому что §10.6 требует сравнивать именно её:
    «на этих сохранённых данных карточка была такой, стала такой».
    """
    for (sym, tf), st in report.series.items():
        if st.not_ready is not None or not st.bars:
            continue
        store.write_bars(run_id, sym, tf, st.bars)
        report.frames_written += 1
    for h in report.histograms.values():
        if h.trades_seen:
            store.write_histogram(run_id, h)
            report.frames_written += 1
    for sym, t in report.binned.items():
        store.write_meta(run_id, sym, t.tick_size, t.bucket_ms)
        if t.trades_seen:
            store.write_binned_trades(run_id, t)
            report.frames_written += 1

    tfs = tuple(uni.timeframes)
    for sym in uni.symbols:
        series = {tf: st.bars for (s, tf), st in report.series.items()
                  if s == sym and st.not_ready is None and st.bars}
        if not series:
            continue
        text = card.render(sym, series, report.binned.get(sym), tfs)
        store.write_card(run_id, sym, text)
        report.cards_written += 1


def record(run_id: str, report: RunReport, uni: Universe) -> None:
    """Записать эмиссии и их исходы в боевой леджер (§8 этап 7).

    Единственное место в проекте, которое пишет сигналы. Открытые и несостоявшиеся
    сделки в исходы НЕ пишутся: исход у них ещё не наступил (§4.3).
    """
    conn = store.open_production_ledger()
    try:
        for sym in uni.symbols:
            series = {tf: st.bars for (s, tf), st in report.series.items()
                      if s == sym and st.not_ready is None and st.bars}
            if not series:
                continue
            trades = report.binned.get(sym)
            lv, _ = levels.build_all(sym, series, trades, tuple(uni.timeframes))
            trends = {tf: swings.trend(sw) for tf, bars in series.items()
                      if not isinstance(sw := swings.detect(bars), NotReady)}
            for em in emit.select(lv, series, trends):
                bars = series[em.level.timeframe]
                opened_at = bars[em.level.created_at_index].open_ms
                sid = store.record_signal(
                    conn, sym, em.level.timeframe, em.direction, opened_at,
                    em.setup.entry, em.ledger_stop, run_id,
                )
                if isinstance(sid, NotReady):
                    log.degraded("сигнал не записан", причина=sid.reason)
                    continue
                report.signals_recorded += 1
                res = emit.outcome_of(em, bars)
                if res.kind.value in ("stop", "target", "ambiguous"):
                    assert res.closed_at_index is not None
                    err = store.record_outcome(
                        conn, sid, res.kind.value,
                        bars[res.closed_at_index].open_ms, res.exit_price, res.r,
                    )
                    if isinstance(err, NotReady):
                        log.degraded("исход не записан", причина=err.reason)
                    else:
                        report.outcomes_recorded += 1
    finally:
        conn.close()


async def live_run(uni: Universe, seconds: int, seed_limit: int, run_id: str,
                   trade_days: int = 3) -> RunReport:
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
            # Корзина — самый младший ТФ вселенной: бары старших кратны ему, значит
            # окно любой структуры складывается из целых корзин (см. BarBinnedTrades).
            bucket = min(TIMEFRAME_MS[tf] for tf in uni.timeframes)
            b = BarBinnedTrades(symbol=sym, tick_size=inst.tick_size, bucket_ms=bucket)
            report.binned[sym] = b
            tasks.append(asyncio.create_task(_watch_trades(ex, sym, h, b, stop)))

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

        # Долив архива — ПОСЛЕ наблюдения и до сборки карточки: без него окна
        # исторических структур не покрыты и уровней не бывает вовсе.
        backfill_trades(ex, uni, report, trade_days)
    finally:
        await ex.close()

    persist(run_id, report, uni)
    record(run_id, report, uni)
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
    print(f"   карточек сохранено: {r.cards_written}")

    print("\n7. ЛЕДЖЕР (§8 этап 7)")
    print(f"   сигналов записано: {r.signals_recorded}")
    print(f"   исходов записано: {r.outcomes_recorded} "
          f"(открытые и несостоявшиеся не пишутся — §4.3)")

    violations = (len(stale) + len(missing) + unexplained + unclosed + offgrid_ws
                  + len(offgrid_seed))
    print("\n8. ИТОГ")
    print(f"   деградаций отмечено: {log.degraded_count()}")
    print(f"   нарушений приёмки: {violations}")
    print("=" * 78)
    return violations
