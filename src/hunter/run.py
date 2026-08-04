"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.

Кадры сохраняются в parquet (§10.3) — без них детерминированный повтор невозможен.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from . import archive, card, clock, emit, levels, log, store, swings
from .accumulation import detect as detect_accumulations
from .bars import TIMEFRAME_MS, expected_last_closed_open_ms, find_gaps, is_closed, on_grid, tf_ms
from .config import Universe
from .exchange import Exchange
from .models import (
    Bar,
    BarBinnedTrades,
    NotReady,
    RunReport,
    SeriesState,
    TradeHistogram,
    TradeWindows,
)
from .swings import detect as detect_swings


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


def needed_days(
    series: dict[str, list[Bar]], horizon_days: int
) -> tuple[set[date], int, int]:
    """Какие сутки архива нужны — ВЫВОДИТСЯ ИЗ СТРУКТУР, а не задаётся ручкой.

    Стр. 26: «важно захватить все свечи структуры». Значит набор суток определяют сами
    структуры, и спрашивать «сколько последних суток скачать» неправильно по существу.

    ⚠ Так и было до 2026-08-04, и цена этого замерена: при умолчании «последние 3 суток»
    уровень мог получить 15% структур, а на 4ч и 1Д — НОЛЬ из 28 и 21. Курс на стр. 48
    говорит «Чем старше ТФ - тем выше винрейт, но дольше отработка», то есть карта
    состояла ровно из тех ТФ, что слабее. Каждый пропуск честно логировался как
    `NotReady` — но по одному, и перекос по ТФ не суммировал никто. Разбор:
    docs/audit/backfill-window-2026-08-04.md

    `horizon_days` отсекает старое — и это НЕ порог качества, а граница набора: структура,
    из которой цена ушла год назад, уровнем по стр. 25 уже не является. Возвращается
    вместе с числом отсечённых структур, чтобы отсечение было видно, а не молчало.
    """
    days: set[date] = set()
    used = dropped = 0
    now = max((b[-1].open_ms for b in series.values() if b), default=0)
    cut = now - horizon_days * 86_400_000
    for tf, bars in series.items():
        if not bars:
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            continue
        for acc in detect_accumulations(bars, sw, tf).closed:
            lo, hi = levels.structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
            if hi < cut:
                dropped += 1
                continue
            used += 1
            d = datetime.fromtimestamp(lo / 1000, UTC).date()
            end = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
            while d <= end:
                days.add(d)
                d += timedelta(days=1)
    return days, used, dropped


def backfill_trades(
    ex: Exchange, uni: Universe, report: RunReport, horizon_days: int,
    max_days_per_symbol: int = 400,
) -> None:
    """Долить сделки под ИСТОРИЧЕСКИЕ окна структур. Набор суток — из `needed_days`.

    ⚠ Без этого шага уровней не бывает вовсе, и это не свойство рынка. Профиль по стр. 26
    натягивается на бары структуры, а структуры лежат в истории; живой поток покрывает
    только длительность прогона. Замер 2026-08-04 (3 символа, 120 с): найдено 200 структур
    и построено 0 уровней — 100% отказов «окно выходит за собранное».

    `max_days_per_symbol` — предохранитель от многолетних структур 1Н, и он ГРОМКИЙ:
    отброшенное печатается числом, а не проглатывается. Непокрытые окна остаются
    `NotReady` с названной причиной (§4.3), а не заполняются приблизительным профилем.
    """
    if horizon_days <= 0:
        return
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded("бэкфилл пропущен: нет инструмента", символ=sym)
            continue
        series = {tf: st.bars for (s, tf), st in report.series.items()
                  if s == sym and st.not_ready is None and st.bars}
        if not series:
            log.degraded("бэкфилл пропущен: нет баров", символ=sym)
            continue
        want, used, dropped = needed_days(series, horizon_days)
        if len(want) > max_days_per_symbol:
            keep = sorted(want)[-max_days_per_symbol:]
            log.warn("предохранитель бэкфилла", символ=sym, нужно_суток=len(want),
                     берём=len(keep), отброшено=len(want) - len(keep))
            report.backfill_days_capped += len(want) - len(keep)
            want = set(keep)
        report.backfill_structures += used
        report.backfill_structures_old += dropped

        cached = archive.cached_days(inst.market_id)
        log.info("бэкфилл: набор суток из структур", символ=sym, структур=used,
                 суток=len(want), из_кэша=len(want & cached),
                 качать=len(want - cached), старых_структур=dropped)
        # ⚠ Сутки только УКЛАДЫВАЮТСЯ В КЭШ, в память ничего не сливается. Прежняя
        # редакция копила весь горизонт в одном `BarBinnedTrades` и кончалась
        # `MemoryError` на 102 сутках: двадцать миллионов пар (корзина, бин) при том,
        # что каждой структуре нужно только её окно. Профиль теперь строит
        # `archive.WindowSource` по требованию.
        for day in sorted(want):
            got = archive.binned_day(inst.market_id, day, inst.tick_size)
            if isinstance(got, NotReady):
                log.degraded("сутки архива не получены", символ=sym, дата=str(day),
                             причина=got.reason)
                report.backfill_days_missing += 1
                continue
            report.backfill_days_loaded += 1
            report.backfill_trades += int(got["n"].sum())
        log.info("сутки уложены в кэш", символ=sym, суток=report.backfill_days_loaded)


def trade_source(ex: Exchange, sym: str, report: RunReport) -> TradeWindows | None:
    """Источник профиля для символа: кэш архива плюс живой поток прогона.

    Один на оба пути — печать карточки и запись в леджер. Раньше туда передавался
    материализованный `BarBinnedTrades`, и он же был причиной `MemoryError`.
    """
    inst = ex.instrument(sym)
    if isinstance(inst, NotReady):
        return report.binned.get(sym)
    return archive.WindowSource(sym, inst.market_id, inst.tick_size,
                                live=report.binned.get(sym))


def persist(run_id: str, report: RunReport, uni: Universe,
            sources: dict[str, TradeWindows]) -> None:
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
        text = card.render(sym, series, sources.get(sym), tfs)
        store.write_card(run_id, sym, text)
        report.cards_written += 1


def record(run_id: str, report: RunReport, uni: Universe,
           sources: dict[str, TradeWindows]) -> None:
    """Записать эмиссии и их исходы в боевой леджер (§8 этап 7).

    Единственное место в проекте, которое пишет сигналы. Открытые и несостоявшиеся
    сделки в исходы НЕ пишутся: исход у них ещё не наступил (§4.3).
    """
    conn = store.open_production_ledger()
    stamp_ms = clock.now_ms()
    try:
        for sym in uni.symbols:
            series = {tf: st.bars for (s, tf), st in report.series.items()
                      if s == sym and st.not_ready is None and st.bars}
            if not series:
                continue
            lv, _ = levels.build_all(sym, series, sources.get(sym),
                                     tuple(uni.timeframes))
            trends = {tf: swings.trend(sw) for tf, bars in series.items()
                      if not isinstance(sw := swings.detect(bars), NotReady)}

            # Карта живёт МЕЖДУ прогонами (стр. 25, 31). Замер `probes.py map-drift`:
            # при сдвиге окна на 200 баров 2-3% активных уровней исчезали не потому, что
            # отработаны, а потому что структура уехала за край окна в 1000 баров.
            # ⚠ Отметка времени берётся ОДНА на прогон, а не по вызову. Первая редакция
            # звала `clock.now_ms()` дважды, и второй ответ был на миллисекунды позже
            # первого: строки, только что записанные этим же прогоном, попадали под
            # условие `last_seen < now` и объявлялись «перенесёнными из прошлых
            # прогонов». Живой прогон на ПУСТОЙ карте напечатал «перенесено 4» — число
            # правдоподобное и целиком выдуманное.
            seen = [(x, levels.status(x, series[x.timeframe]).state) for x in lv]
            added, upd, retired = store.sync_levels(conn, sym, seen, stamp_ms)
            carried = store.carried_levels(conn, sym, stamp_ms)
            report.map_added += added
            report.map_updated += upd
            report.map_retired += retired
            report.map_carried[sym] = carried

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
                   horizon_days: int = 90) -> RunReport:
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
        backfill_trades(ex, uni, report, horizon_days)
        # Источники строятся ДО закрытия соединения: им нужен `market_id` инструмента.
        # Сеть после этого не трогается — читается только кэш на диске.
        sources = {sym: src for sym in uni.symbols
                   if (src := trade_source(ex, sym, report)) is not None}
    finally:
        await ex.close()

    persist(run_id, report, uni, sources)
    record(run_id, report, uni, sources)
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

    print("\n5б. АРХИВ СДЕЛОК ПОД ОКНА СТРУКТУР (стр. 26)")
    print(f"   структур в горизонте: {r.backfill_structures}, "
          f"старше горизонта отброшено: {r.backfill_structures_old}")
    print(f"   суток загружено: {r.backfill_days_loaded}, не получено: "
          f"{r.backfill_days_missing}, отброшено предохранителем: {r.backfill_days_capped}")
    print(f"   сделок влито: {r.backfill_trades}")

    print("\n7. ЛЕДЖЕР (§8 этап 7)")
    print(f"   сигналов записано: {r.signals_recorded}")
    print(f"   исходов записано: {r.outcomes_recorded} "
          f"(открытые и несостоявшиеся не пишутся — §4.3)")

    carried = sum(len(v) for v in r.map_carried.values())
    print("\n7б. КАРТА УРОВНЕЙ МЕЖДУ ПРОГОНАМИ (стр. 25, 31)")
    print(f"   новых уровней: {r.map_added}, подтверждено прежних: {r.map_updated}")
    print(f"   снято по курсу (отработан/пробит): {r.map_retired}")
    print(f"   ПЕРЕНЕСЕНО из прошлых прогонов: {carried} "
          f"(активны, но в этом окне баров не пересчитаны)")
    for sym, rows in sorted(r.map_carried.items()):
        for c in rows[:3]:
            print(f"     {sym:16} {c.timeframe:>3} {c.side:5} ПОК {c.price} "
                  f"(окно {c.from_ms}…{c.to_ms})")

    violations = (len(stale) + len(missing) + unexplained + unclosed + offgrid_ws
                  + len(offgrid_seed))
    print("\n8. ИТОГ")
    print(f"   деградаций отмечено: {log.degraded_count()}")
    print(f"   нарушений приёмки: {violations}")
    print("=" * 78)
    return violations
