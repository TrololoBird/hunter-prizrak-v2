"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.

Кадры сохраняются в parquet (§10.3) — без них детерминированный повтор невозможен.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import ccxt

from . import archive, card, clock, emit, engine, geometry, levels, log, store
from .accumulation import detect as detect_accumulations
from .bars import TIMEFRAME_MS, expected_last_closed_open_ms, find_gaps, on_grid, tf_ms
from .config import Universe
from .exchange import (
    CATCHUP_MAX_BARS,
    CATCHUP_RETRY_S,
    POLL_LIMIT,
    POLL_OFFSET_S,
    REQUIRED_CAPABILITIES,
    Exchange,
)
from .models import (
    Bar,
    BarBinnedTrades,
    Instrument,
    NotReady,
    RunReport,
    SeriesState,
    TradeHistogram,
    TradeWindows,
)
from .outcome import OutcomeKind
from .outcome import resolve as outcome_resolve
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
            st.rejected_at_ms = got.rejected_at_ms
            st.gaps = find_gaps(got.bars, tf)
            report.seeded_bars += len(got.bars)
            if st.gaps:
                log.warn("разрыв сетки", символ=sym, тф=tf, разрывов=len(st.gaps),
                         первый_после=st.gaps[0][0])


def _sleep_until_next_poll_s(timeframe: str, now_ms: int) -> float:
    """Секунды до следующего опроса: правая граница текущего бара плюс отступ.

    Считается от СВЕДЁННЫХ часов и заново на каждой итерации, поэтому дрейф часов и
    задержка планировщика не накапливаются: промах одного опроса не сдвигает следующий.
    """
    step = tf_ms(timeframe)
    next_close_ms = expected_last_closed_open_ms(timeframe, now_ms) + 2 * step
    return max(0.0, (next_close_ms - now_ms) / 1000.0 + POLL_OFFSET_S)


def bars_behind(st: SeriesState, now_ms: int) -> int:
    """На сколько ЗАКРЫТЫХ баров ряд отстаёт от того, что обязано быть закрыто.

    Ноль — ряд догнал биржевые часы. Сравниваются два независимых источника: наши
    сведённые часы говорят, какая свеча обязана быть закрыта, а ряд показывает, какая
    последняя принята.

    ⚠ Нужно догону (2026-08-06). До него опрос ВСЕГДА спал до следующей границы, и бар,
    закрывшийся между засевом и первым опросом своего ТФ, подбирался только на следующей
    границе — до пяти минут на 5м, но до СУТОК на 1Д и до недели на 1Н. Поймано приёмкой:
    прогон, начавшийся в 00:00:48 UTC (48 секунд после границы, общей всем ТФ), дал 8
    отстающих рядов из 18; тот же прогон тремя минутами позже — ни одного.
    """
    if not st.bars:
        return 0
    step = tf_ms(st.timeframe)
    gap = expected_last_closed_open_ms(st.timeframe, now_ms) - st.bars[-1].open_ms
    return max(0, gap // step)


CLOCK_RESYNC_S = 900.0
"""Как часто пересводить часы. ⚠ ЧИСЛО НЕ ЗАМЕРЕНО — выбрано вчетверо чаще порога
устаревания `clock.MAX_SYNC_AGE_MS` (час), чтобы один пропущенный замер не делал часы
просроченными. Дрейф за час прямым замером не получен; `clock_drift_max_ms` в приёмке и
есть этот замер, снимаемый на боевых прогонах.

⚠ Обзор чужих реализаций 2026-08-08: периода пересведения не публикует НИКТО из двадцати.
Одиннадцать проектов часы не сводят вовсе (им хватает окна допуска биржи), у остальных
пересведение делается по вызову, а не по расписанию. Внешнего референта у этого числа нет —
в отличие от порога 5000 мс, у которого он нашёлся.

⚠ И поправка к самому обзору: в его фазе 0 я записал 900 с как «число без источника». Это
неточно — обоснование записано вот здесь, прямо над этой строкой, и оно выводит период из
порога устаревания. Без источника остаётся ПОРОГ, а не период.

⚠ Практическое: при штатном наблюдении в 400 с эта ветка не исполняется НИ РАЗУ, то есть
`clock_resyncs` всегда ноль, и уход сдвига между сведениями на боевых прогонах не
наблюдался никогда. Обзор: docs/audit/clock-projects-2026-08-08.md"""


async def _resync_clock_impl(ex: Exchange, report: RunReport, stop: asyncio.Event) -> None:
    """Периодически пересводить часы с биржей. Вторая половина находки Д-5.

    ⚠ До 2026-08-05 часы сводились ДВА раза за прогон — на старте и в конце, — и второй
    замер служил только отчётом о дрейфе. Для пакетного прогона на 90 секунд этого
    хватало; для службы 24/7 (§8) сведение, сделанное один раз, к концу суток
    произвольно устарело бы, а `now_ms` об этом молчал.

    Отказ замера НЕ роняет задачу и НЕ трогает прежнее сведение: старые часы с известным
    возрастом лучше, чем отсутствие часов. Число отказов идёт в отчёт.
    """
    while not stop.is_set():
        if await sleep_or_stopped(stop, CLOCK_RESYNC_S):
            return
        before = clock.sync_state().offset_ms
        try:
            again = await clock.measure(ex.fetch_server_ms)
        except ccxt.BaseError as e:
            report.clock_resync_failures += 1
            log.degraded("пересведение часов не удалось — оставлены прежние",
                         возраст_мс=clock.age_ms(), причина=f"{type(e).__name__} {e}")
            continue
        report.clock_resyncs += 1
        drift = again.offset_ms - before
        report.clock_drift_max_ms = max(report.clock_drift_max_ms, abs(drift))
        log.info("часы пересведены", сдвиг_мс=again.offset_ms, уход_мс=drift)


FRESHNESS_GRACE_S = POLL_OFFSET_S + 10.0
"""Сколько ряду позволено отставать после границы ТФ, прежде чем это НАРУШЕНИЕ.

Выводится из `POLL_OFFSET_S`, а не задаётся отдельно: опрос по построению приходит через
`POLL_OFFSET_S` после границы, и порог свежести обязан ехать вместе с ним — иначе правка
отступа молча ломает приёмку.

⚠ Добавка 10 с НЕ ЗАМЕРЕНА. Это допуск на сам запрос, на троттлер ccxt и на задержку
планировщика. Замер запроса у нас есть только косвенный (`rtt_ms` часов — сотни
миллисекунд), и 10 с — запас на порядок. Занижать нельзя: ложное «отстаёт» учит не верить
приёмке, ровно как ложное «битых 0» учит не верить гейту.
"""

MARKETS_RELOAD_S = 3600.0
"""Как часто перечитывать рынки. ⚠ ЧИСЛО НЕ ЗАМЕРЕНО.

Час выбран по природе события, а не по замеру: делистинг и смена `PRICE_FILTER` —
события уровня объявления биржи, они происходят раз в недели, и узнать о них через час
достаточно. Чаще значило бы платить весом за то, что почти никогда не меняется:
`load_markets(reload=True)` — это `exchangeInfo` на все ~850 рынков.

Замера «сколько проходит между делистингами» нет, и он бы ничего не дал: важна не частота
события, а то, что оно ЗАМЕЧЕНО до конца суток работы.
"""


async def _reload_markets_impl(
    ex: Exchange, uni: Universe, report: RunReport, stop: asyncio.Event
) -> None:
    """Периодически перечитывать рынки и называть изменения. Для службы 24/7 (§8).

    ⚠ До 2026-08-05 `load_markets()` звался ОДИН раз в `open()`, а кэш `_instruments` не
    сбрасывался никогда. Следствие для службы: смена шага цены и делистинг посреди работы
    оставались незамеченными — включая проверку `active`, добавленную в тот же день, но
    отрабатывавшую только на старте.

    Отказ чтения прежние рынки НЕ портит: `reload_markets` отдаёт `NotReady`, кэш и
    `markets` остаются какими были, число отказов идёт в отчёт.
    """
    while not stop.is_set():
        if await sleep_or_stopped(stop, MARKETS_RELOAD_S):
            return
        got = await ex.reload_markets(uni.symbols)
        if isinstance(got, NotReady):
            report.markets_reload_failures += 1
            log.degraded("рынки не перечитаны — оставлены прежние", причина=got.reason)
            continue
        report.markets_reloads += 1
        report.markets_checked = got.checked
        for ch in got.tick_changed:
            report.tick_changes.append(f"{ch.symbol}: {ch.was} → {ch.now}")
            log.error("ШАГ ЦЕНЫ ИЗМЕНИЛСЯ — сетка бинов профиля построена по прежнему",
                      символ=ch.symbol, было=str(ch.was), стало=str(ch.now))
        for s in got.delisted:
            report.delisted_mid_run.append(s)
            log.error("символ снят с торгов ПОСРЕДИ ПРОГОНА", символ=s)
        for s in got.restored:
            log.warn("символ снова доступен", символ=s)


async def sleep_or_stopped(stop: asyncio.Event, seconds: float) -> bool:
    """Ждать до `seconds` или прерваться раньше по остановке. `True` — остановлено.

    Таймаут здесь — ШТАТНЫЙ исход (пора опрашивать), а не сбой, поэтому он превращается
    в значение, а не глотается пустым `except`. Без этого разделения остановка прогона и
    срабатывание таймера были бы неотличимы, а прогон на `--seconds 90` ждал бы конца
    недельной свечи.
    """
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


async def _poll_bars_impl(ex: Exchange, st: SeriesState, report: RunReport,
                          stop: asyncio.Event, keep_bars: int = 0) -> None:
    """Добор баров REST-опросом по границам ТФ. Заменил WS-поток 2026-08-05.

    ⚠ Здесь стоит проверка, которая РАНЬШЕ БЫЛА ТАВТОЛОГИЕЙ. Прежний счётчик «отдан
    НЕзакрытый бар» не мог вырасти никогда: поток отдавал бар, только когда
    `now >= open_ms + tf`, а проверка спрашивала ровно то же и теми же часами — строка
    приёмки «из них незакрытых: 0» была свойством кода, а не рынка (находка Д-1).

    Теперь сравниваются ДВА независимых источника: наши часы говорят, какая свеча
    ОБЯЗАНА быть закрыта (`expected_last_closed_open_ms`), а биржа отвечает, какая у неё
    закрыта на самом деле. `poll_late` способен быть больше нуля — и это делает его
    замером, а не украшением. Он же и есть задание Ж-1: сколько раз отступа
    `POLL_OFFSET_S` не хватило.

    `keep_bars` — предел длины ряда, нужный ТОЛЬКО службе 24/7 (§8, находка А-1): опрос
    дописывает бар за баром и за год работы накопил бы по 105 тысяч баров на каждый ряд
    5м. Ноль означает «не отрезать» и оставлен пакетному прогону: тот живёт минуты, а
    любое отрезание изменило бы окно расчёта и, значит, дифф повтора (§10.6).
    Отрезанное считается в `bars_trimmed` — усечение обязано быть числом (§4.3).
    """
    progressed = True
    while not stop.is_set():
        behind = bars_behind(st, clock.now_ms())
        if behind == 0:
            wait = _sleep_until_next_poll_s(st.timeframe, clock.now_ms())
        elif progressed:
            # ДОГОН без паузы: ряд отстал, и предыдущая попытка (или засев) продвигала
            # его. Ждать здесь нечего — бар уже закрыт по нашим часам.
            wait = 0.0
        else:
            # Предыдущий догон не добавил ни бара: биржа его ещё не отдала. Повторять
            # вплотную нельзя — см. `CATCHUP_RETRY_S`, там арифметика лимита.
            wait = CATCHUP_RETRY_S
        if wait > 0 and await sleep_or_stopped(stop, wait):
            return

        # При догоне просим СТОЛЬКО, на сколько отстали: `POLL_LIMIT` рассчитан на
        # пропуск одного-двух баров, а после долгой остановки их могут быть десятки.
        want = POLL_LIMIT
        if behind:
            st.poll_catchups += 1
            want = min(max(POLL_LIMIT, behind + 1), CATCHUP_MAX_BARS)

        st.poll_requests += 1
        got = await ex.fetch_closed_ohlcv(st.symbol, st.timeframe, limit=want)
        if isinstance(got, NotReady):
            st.poll_not_ready += 1
            progressed = False
            log.degraded("опрос баров не дал ряда", символ=st.symbol, тф=st.timeframe,
                         причина=got.reason)
            continue

        expected = expected_last_closed_open_ms(st.timeframe, clock.now_ms())
        if got.bars[-1].open_ms < expected:
            st.poll_late += 1
            log.degraded("биржа ещё не отдала ожидаемую закрытую свечу",
                         символ=st.symbol, тф=st.timeframe,
                         ожидалась=expected, получена=got.bars[-1].open_ms)

        # ⚠ Проверки `on_grid` здесь НЕТ намеренно, и это не упущение. Первая редакция
        # этого цикла её содержала вместе со счётчиком `poll_offgrid_violations` — и
        # счётчик не мог вырасти НИКОГДА: `fetch_closed_ohlcv` отвергает весь ответ,
        # если хоть один бар вне сетки, то есть до этой строки доходят только сетевые.
        # Ровно форма Д-1, из-за которой этот цикл и переписывался. Внесеточный ответ
        # виден как `poll_not_ready` с причиной, и это единственный счётчик, способный
        # тут вырасти.
        #
        # Курсор — конец УЖЕ принятого ряда. Ответ на опрос перекрывается с ним всегда
        # (просим POLL_LIMIT баров), и без курсора ряд получал бы дубликаты.
        last = st.bars[-1].open_ms if st.bars else None
        added = 0
        for bar in got.bars:
            if last is not None and bar.open_ms <= last:
                continue
            st.bars.append(bar)
            st.polled_bars += 1
            added += 1
        # Продвинулись ли — решает СОДЕРЖИМОЕ ответа, а не его успешность. Ответ без
        # единого нового бара означает, что биржа его ещё не отдала, и следующая попытка
        # обязана подождать; иначе догон превращается в цикл вплотную.
        progressed = added > 0

        if keep_bars and len(st.bars) > keep_bars:
            cut = len(st.bars) - keep_bars
            del st.bars[:cut]
            report.bars_trimmed += cut

        # ⚠ Разрывы ПЕРЕСЧИТЫВАЮТСЯ после каждого добора (находка Д-3). Прежняя
        # редакция считала `st.gaps` только в засеве, и разрыв, возникший при
        # дописывании хвоста, не попадал ни в отчёт, ни в `continuous_tail`, то есть
        # доезжал до ATR и до стопа.
        # Считается ПОСЛЕ отрезания: разрыв, уехавший за левый край окна, разрывом
        # видимого ряда быть перестал, и печатать его значило бы называть нарушением то,
        # чего в расчёте нет.
        st.gaps = find_gaps(st.bars, st.timeframe)


class TradeSequence:
    """Номера aggTrade по одному символу. Прибор полноты потока (§4.3, находка А-1).

    ⚠ До 2026-08-06 полнота потока сделок не проверялась НИЧЕМ. Считались принятые
    сделки, и «принято 40 000» одинаково выглядело при нулевой потере и при потере трети:
    единственным источником сведений о потоке был сам поток.

    Номер `aggTrade` у Binance строго последователен по символу, поэтому разрыв между
    соседними полученными номерами — это сделки, до нас НЕ ДОШЕДШИЕ. Причин ровно две, и
    обе важны службе 24/7: `watch_trades` у ccxt складывает сделки в кэш ограниченной
    длины, и всё неразобранное вовремя вытесняется, пока цикл событий занят чем-то одним;
    плюс обрыв сокета теряет то, что шло во время обрыва.

    `checked` — ЗНАМЕНАТЕЛЬ. Без него «разрывов 0» неотличимо от «номера не читались»:
    если бы биржа или ccxt перестали присылать `id`, счётчик разрывов остался бы нулём
    навсегда и выглядел бы здоровьем.
    """

    __slots__ = ("checked", "gap_events", "gaps", "last", "symbol", "unnumbered")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.last: int | None = None
        self.gaps = 0
        self.gap_events = 0
        self.checked = 0
        self.unnumbered = 0

    def note(self, raw: object) -> None:
        try:
            tid = int(str(raw))
        except (TypeError, ValueError):
            self.unnumbered += 1
            return
        self.checked += 1
        if self.last is not None and tid > self.last + 1:
            self.gaps += tid - self.last - 1
            self.gap_events += 1
        if self.last is None or tid > self.last:
            self.last = tid


async def _watch_trades_impl(ex: Exchange, sym: str, hist: TradeHistogram,
                             binned: BarBinnedTrades, seq: TradeSequence,
                             stop: asyncio.Event) -> None:
    agen = ex.watch_agg_trades(sym)
    try:
        while not stop.is_set():
            batch = await anext(agen)
            # Границы разрывов снимаются ДО учёта: `seq.note` двигает курсор, и после
            # цикла спрашивать «где был разрыв» уже не у чего.
            gaps: list[tuple[int, int]] = []
            prev = seq.last
            for t in batch:
                try:
                    tid: int | None = int(str(t.id))
                except (TypeError, ValueError):
                    tid = None
                if tid is not None:
                    if prev is not None and tid > prev + 1:
                        gaps.append((prev + 1, tid))
                    prev = tid if prev is None else max(prev, tid)
                # Сделка уже разобрана в тип на границе с ccxt (А-5): проверять ключи
                # здесь больше не нужно и нельзя — их нет.
                seq.note(t.id)
                hist.add(t.price, t.amount, t.timestamp)
                binned.add(t.price, t.amount, t.timestamp)
            for lo, hi in gaps:
                # Догон потерянного КУРСОРОМ по номерам (2026-08-10): раньше разрыв
                # только считался, и профиль тех минут молча строился без сделок.
                # `gaps`-счётчики при этом НЕ трогаются — они меряют потерю ПОТОКА,
                # и добор её не отменяет, а компенсирует другим источником.
                got = await ex.fetch_agg_trades_from(sym, lo, hi)
                if isinstance(got, NotReady):
                    ex.trades_unrecovered += hi - lo
                    log.degraded("разрыв потока сделок не добран", символ=sym,
                                 потеряно=hi - lo, причина=got.reason)
                    continue
                for t in got:
                    hist.add(t.price, t.amount, t.timestamp)
                    binned.add(t.price, t.amount, t.timestamp)
                left = (hi - lo) - len(got)
                if left > 0:
                    ex.trades_unrecovered += left
                    log.degraded("разрыв потока сделок добран частично", символ=sym,
                                 добрано=len(got), осталось=left)
    except asyncio.CancelledError:
        raise
    finally:
        await agen.aclose()


HEARTBEAT_S = 1.0
"""Такт сердцебиения. Число не замерено и замеру не подлежит: это ЛИНЕЙКА, а не порог.

Такт задаёт лишь разрешающую способность измерителя задержки — задержка короче секунды
им не видна. Секунда выбрана потому, что цена промаха у нас начинается с единиц секунд:
кэш сделок ccxt на BTC вмещает порядка десятка секунд потока.
"""


async def _heartbeat_impl(report: RunReport, stop: asyncio.Event) -> None:
    """Измеритель ЗАДЕРЖКИ ЦИКЛА СОБЫТИЙ. Без него «расчёт не держит цикл» — слова.

    ⚠ Прибор появился вместе со службой (А-1) и отвечает на вопрос, который в пакетной
    схеме не стоял. Пакетный прогон собирал данные, а потом считал; собирать во время
    счёта было незачем. Служба считает НЕ ПРЕРЫВАЯ сбора, и каждая секунда, которую цикл
    событий провёл в чужом синхронном коде, — это секунда, когда не опрашивались бары и
    не разбирался поток сделок.

    Работает на монотонных часах и не зависит ни от сведения с биржей, ни от настенного
    времени: меряется опоздание собственного пробуждения относительно собственного такта.
    Отдельно считаются такты — знаменатель, без которого нулевая задержка неотличима от
    неработающего измерителя.

    ⚠ Прибор ЗАНИЖАЕТ задержку на величину до одного такта, и это его устройство, а не
    погрешность: часть блокировки приходится на время, которое сердцебиение и так спало
    законно. Замер пробником: блокировка цикла на 2000 мс, начатая через 200 мс после
    пробуждения, даёт `1203` — то есть `блокировка − остаток такта`. Значит число здесь —
    НИЖНЯЯ оценка задержки, и трактовать его надо так: настоящая остановка была не
    короче напечатанного.

    ⚠ И второе ограничение, найденное живым контролем 2026-08-06. Остановка попадает в
    замер только ПОСЛЕ того, как кончилась: пока цикл занят, сердцебиение по определению
    не выполняется. Значит остановка, за которой сразу идёт снятие задач, не измеряется
    вовсе. Контрольный прогон (расчёт возвращён на цикл событий) напечатал `16 мс` при
    двух остановках по 25-30 с: первая пришлась на самое начало и сердцебиение не успело
    тикнуть НИ РАЗУ, вторая кончилась вместе со службой. Отсюда `service.serve` даёт
    циклу событий мгновение перед остановкой — иначе последний замер теряется всегда.
    """
    while not stop.is_set():
        before = clock.monotonic_ns()
        if await sleep_or_stopped(stop, HEARTBEAT_S):
            return
        late_ms = int((clock.monotonic_ns() - before) / 1e6 - HEARTBEAT_S * 1000)
        report.heartbeats += 1
        report.loop_stall_max_ms = max(report.loop_stall_max_ms, late_ms)


WATCH_FAILURES_KEPT = 50
"""Сколько текстов смертей хранить как примеры. Точный счёт ведёт `watch_deaths`."""

RESTART_PAUSE_S = 5.0
"""Пауза перед подъёмом умершей задачи. ⚠ ЧИСЛО НЕ ЗАМЕРЕНО.

Нужна не «на всякий случай», а против кругового отказа: задача, умирающая сразу при
запуске (снятый символ, отвергнутая подписка), без паузы поднималась бы тысячи раз в
секунду и била бы по лимиту биржи — тому самому, за превышение которого банят по IP до
трёх суток. Пять секунд — это порядок величины, при котором подъём остаётся быстрым для
человека и редким для биржи; замера, из которого следовало бы иное число, у нас нет.
"""


def _note_death(report: RunReport, name: str, exc: BaseException) -> None:
    """Записать смерть задачи: точное число плюс ограниченный список примеров."""
    report.watch_deaths += 1
    if len(report.watch_failures) < WATCH_FAILURES_KEPT:
        report.watch_failures.append(f"{name}: {type(exc).__name__} {exc}")


async def _supervise(name: str, factory: Callable[[], Awaitable[None]],
                     report: RunReport, stop: asyncio.Event) -> None:
    """Держать задачу наблюдения ЖИВОЙ. Заменил `_guarded` 2026-08-06 (находка А-1).

    ⚠ Что было. `_guarded` логировал смерть и пробрасывал её дальше; задача переставала
    существовать, а счёт вёл разбор `gather` после остановки. Для прогона на девяносто
    секунд это верно: прогон вот-вот кончится, и «умерло 2» в сводке — исчерпывающий
    ответ. Для службы 24/7 (§8) это означает необратимую деградацию: один сетевой сбой на
    третьем часу — и символ до конца суток не получает сделок, а сводка продолжает
    печатать бодрые числа по тому, что он успел собрать раньше. Ровно тот тихий отказ,
    который запрещает §4.3, только растянутый во времени.

    Поэтому задача теперь ПОДНИМАЕТСЯ. `factory` — не корутина, а её изготовитель:
    корутину нельзя ожидать дважды, и подъём требует новой.

    Смерть при этом не становится дешевле: она считается (`watch_deaths`), попадает в
    примеры и входит в число нарушений приёмки. Подъём — не «починка», а продолжение
    наблюдения; разница между `watch_deaths` и `watch_restarts` показывает, сколько
    смертей осталось непокрытыми (остановка застала задачу мёртвой).
    """
    while not stop.is_set():
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _note_death(report, name, e)
            log.error("задача наблюдения умерла", задача=name,
                      причина=f"{type(e).__name__} {e}")
            if await sleep_or_stopped(stop, RESTART_PAUSE_S):
                return
            report.watch_restarts += 1
            log.warn("задача наблюдения поднята заново", задача=name,
                     подъёмов_всего=report.watch_restarts)


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


async def backfill_trades(
    ex: Exchange, uni: Universe, report: RunReport, horizon_days: int,
    max_days_per_symbol: int = 400,
) -> None:
    """Долить сделки под ИСТОРИЧЕСКИЕ окна структур. Обёртка: вся работа — в потоке.

    ⚠ Функция АСИНХРОННА с 2026-08-05 (находка Д-8): `archive.binned_day` качает суточный
    ZIP блокирующим `urllib`, а зовётся из корутины — пока идёт закачка, цикл событий
    стоит целиком. В пакетной схеме это было почти безобидно (долив шёл после снятия
    задач наблюдения), для службы 24/7 — нет.

    ⚠ 2026-08-06, при переходе на службу (А-1), выяснилось, что правка Д-8 была НЕПОЛНОЙ.
    В поток уходила только закачка, а `needed_days` остался на цикле событий — и это не
    мелочь: он гоняет `detect_swings` и `detect_accumulations` по каждому ТФ каждого
    символа, то есть настоящий расчёт, а не разбор ответа. Пакетному прогону это было
    безразлично по той же причине, по которой была безразлична закачка: наблюдение уже
    остановлено. Служба считает НЕ ПРЕРЫВАЯ сбора, и здесь пакетная привычка стоила бы
    ровно того же — замершего опроса и вытесненных из кэша ccxt сделок.

    Поэтому в поток уходит ВСЁ тело целиком, а на цикле остаётся только то, что трогает
    биржу: инструменты берутся здесь. Читать `ex.markets` из потока нельзя — задача
    перечитывания рынков подменяет этот словарь на цикле (`load_markets(reload=True)`).
    """
    if horizon_days <= 0:
        return
    insts: dict[str, Instrument] = {}
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded("бэкфилл пропущен: нет инструмента", символ=sym)
            continue
        insts[sym] = inst
    await asyncio.to_thread(_backfill_impl, insts, uni, report, horizon_days,
                            max_days_per_symbol)


def _backfill_impl(
    insts: dict[str, Instrument], uni: Universe, report: RunReport, horizon_days: int,
    max_days_per_symbol: int,
) -> None:
    """Тело бэкфилла. Синхронно и целиком в рабочем потоке — см. `backfill_trades`.

    Закачка ПОСЛЕДОВАТЕЛЬНА. Уводить её в поток — не то же самое, что качать в несколько
    потоков: второе изменило бы нагрузку на `data.binance.vision`, а такого замера нет.

    ⚠ Без этого шага уровней не бывает вовсе, и это не свойство рынка. Профиль по стр. 26
    натягивается на бары структуры, а структуры лежат в истории; живой поток покрывает
    только длительность прогона. Замер 2026-08-04 (3 символа, 120 с): найдено 200 структур
    и построено 0 уровней — 100% отказов «окно выходит за собранное».

    `max_days_per_symbol` — предохранитель от многолетних структур 1Н, и он ГРОМКИЙ:
    отброшенное печатается числом, а не проглатывается. Непокрытые окна остаются
    `NotReady` с названной причиной (§4.3), а не заполняются приблизительным профилем.
    """
    for sym in uni.symbols:
        inst = insts.get(sym)
        if inst is None:
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

        cached = archive.cached_days(inst.market_id, inst.tick_size)
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


def bars_of(report: RunReport, sym: str) -> dict[str, list[Bar]]:
    """Готовые ряды символа. Один фильтр на все три шага — иначе они разойдутся."""
    return {tf: st.bars for (s, tf), st in report.series.items()
            if s == sym and st.not_ready is None and st.bars}


def persist_frames(run_id: str, report: RunReport) -> None:
    """ШАГ 2 из трёх: сохранить СЫРЫЕ КАДРЫ. Карточку здесь не строим.

    ⚠ Раньше эта функция ещё и производила карточку. То есть сбор данных и производство
    сигнала жили в одном вызове — та самая слипшаяся точка, из которой в прошлом проекте
    вырос `orchestrator.py` на 2894 строки. Разделено 2026-08-04 по внешнему разбору,
    ДО реализации §2.4-2.7: позже это перестало бы быть десятиминутной правкой.

    Каталог символа СТИРАЕТСЯ перед записью: `--run-id` по умолчанию `last`, и кадры
    прошлого прогона иначе остаются рядом с новыми (А-4).
    """
    for sym in {s for s, _ in report.series}:
        store.clear_run(run_id, sym)
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


def explained_gaps(st: SeriesState) -> tuple[tuple[int, int], ...]:
    """Разрывы ряда, объяснённые ОТКЛОНЁННЫМ БАРОМ ВНУТРИ них. Находка Д-4.

    Разрыв `(a, b)` — это отсутствие баров между `a` и `b`. Он объясняется дефектом
    данных биржи только тогда, когда отклонённый бар лежит СТРОГО ВНУТРИ интервала: тогда
    дыра и есть след выброшенного бара, а не наш пропуск.

    ⚠ Прежняя редакция считала `sum(1 for s in ready if s.rejected_bars for _ in s.gaps)`
    — то есть при наличии в ряду ХОТЬ ОДНОГО отказа объявляла объяснёнными ВСЕ его
    разрывы. Один битый бар списывал любое число дыр, а `unexplained` входит в счёт
    нарушений приёмки: молчаливый пропуск получал право не считаться (§4.3).

    На прогонах, где отказ ровно один и разрыв ровно один, обе формулы дают одно и то же
    — поэтому дефект и не проявлялся. Он ждал второго разрыва.
    """
    if not st.rejected_at_ms:
        return ()
    return tuple((a, b) for a, b in st.gaps
                 if any(a < at < b for at in st.rejected_at_ms))


def decide_once(report: RunReport, uni: Universe,
                sources: dict[str, TradeWindows]) -> dict[str, engine.SymbolDecision]:
    """ШАГ 2 из четырёх: посчитать сигнал. ОДИН раз на символ, для обоих потребителей.

    ⚠ Заменил `build_levels_once` 2026-08-06. Тот считал один раз только УРОВНИ (находка
    А-2), а решение — приоритет, согласие и геометрию — карточка и леджер продолжали
    считать каждый своим обходом. Замер на кадрах прогона `a1`: карточка печатала
    геометрию для 94 уровней, леджер эмитировал 33.

    Теперь считается всё решение целиком (`engine.decide`), а `produce_cards` и `record`
    только читают. Расходиться нечему: обход один.

    Результат — словарь символ → решение. Это НЕ словарь между слоями в смысле §10.1:
    значение типизировано (`engine.SymbolDecision`), а ключ — символ.
    """
    tfs = tuple(uni.timeframes)
    out: dict[str, engine.SymbolDecision] = {}
    for sym in uni.symbols:
        series = bars_of(report, sym)
        if not series:
            continue
        out[sym] = engine.decide(sym, series, sources.get(sym), tfs)
    return out


def produce_cards(run_id: str, report: RunReport, uni: Universe,
                  decided: dict[str, engine.SymbolDecision]) -> None:
    """ШАГ 3 из четырёх: напечатать карточки и сохранить рядом с кадрами.

    Рядом с кадрами — потому что §10.6 требует сравнивать именно карточку: «на этих
    сохранённых данных карточка была такой, стала такой».
    """
    for sym in uni.symbols:
        d = decided.get(sym)
        if d is None:
            continue
        store.write_card(run_id, sym, card.render(d, bars_of(report, sym)))
        report.cards_written += 1


def persist_archive(run_id: str, report: RunReport,
                    sources: dict[str, TradeWindows]) -> None:
    """ШАГ 3б: положить в кадры ТЕ САМЫЕ сутки архива, из которых построена карточка.

    Зовётся ПОСЛЕ карточек, а не до: до них неизвестно, какие сутки понадобились. Набор
    берётся у самого источника (`WindowSource.used_days`), а не пересчитывается «какие
    должны были быть» — второй расчёт разошёлся бы с первым ровно там, где это важно.

    Без этого шага повтор читает ОБЩИЙ кэш, который между прогонами меняется: бэкфилл
    доливает сутки, а очистка их убирает. Прогон-пробник: кадры не тронуты, из кэша
    убраны одни сутки — карточка ETH изменилась на 483 строки (Н-6 разбора).
    """
    for sym, src in sources.items():
        if not isinstance(src, archive.WindowSource):
            continue
        for day in sorted(src.used_days):
            store.write_archive_slice(run_id, sym,
                                      archive.cache_path(src.market_id, day, src.tick))
            report.archive_slices_written += 1


def _resolve_pending(conn: sqlite3.Connection, report: RunReport,
                     uni: Universe) -> None:
    """Дорешать сигналы леджера, у которых исхода ещё нет. Схема v5, 2026-08-10.

    ⚠ ЗАЧЕМ ЭТО ЕСТЬ. До 2026-08-10 исход считался ТОЛЬКО у сигналов, эмитируемых в
    этом же прогоне (`decided[sym].emissions`). Сигнал, чей уровень ушёл из карты —
    отработан (стр. 25), пробит (стр. 43) или просто не отобран, — не досчитывался
    НИКОГДА. Замер на боевом леджере: 76 сигналов из 112 навсегда без ответа, то есть
    «средний R» считался по трети журнала, смещённой в сторону уровней, которые система
    продолжает отбирать. Это форма дефекта «смещение отбора в леджер» из реестра
    CLAUDE.md, обнаруженная сводкой исходов.

    Здесь исход считается ПО БАРАМ и никак не зависит от того, живёт ли ещё уровень.
    Правило §8 этапа 7 сохранено полностью: бары берутся только те, что закрылись ПОЗЖЕ
    `recorded_at`, — журнал, а не бэктест.

    Отказы называются числами, а не молчанием: сигнал без цели (записан до v5), символ
    без рядов в этом прогоне и ТФ без баров считаются раздельно.
    """
    no_target = no_bars = 0
    for sym in uni.symbols:
        series = bars_of(report, sym)
        if not series:
            continue
        for p in store.pending_signals(conn, sym):
            bars = series.get(p.timeframe)
            if not bars:
                no_bars += 1
                continue
            if p.target is None:
                no_target += 1
                continue
            side = (levels.LevelSide.LONG if p.direction == "long"
                    else levels.LevelSide.SHORT)
            res = outcome_resolve(
                side=side, entry=p.entry, stop=p.stop, target=p.target, bars=bars,
                from_index=emit.first_bar_after(bars, p.timeframe, p.recorded_at, 0),
            )
            if res.kind.value in ("stop", "target", "ambiguous"):
                assert res.closed_at_index is not None
                err = store.record_outcome(
                    conn, p.id, res.kind.value, bars[res.closed_at_index].open_ms,
                    res.exit_price, res.r)
                if isinstance(err, NotReady):
                    log.degraded("дорешанный исход не записан", причина=err.reason)
                else:
                    report.outcomes_recorded += 1
                    report.outcomes_resolved_late += 1
            else:
                serr = store.record_signal_state(
                    conn, p.id, res.kind.value, bars[-1].open_ms)
                if isinstance(serr, NotReady):
                    log.degraded("состояние не записано", причина=serr.reason)
                else:
                    report.states_recorded += 1
    report.pending_no_target = no_target
    report.pending_no_bars = no_bars
    if no_target or no_bars:
        log.degraded("часть сигналов дорешать нельзя", без_цели=no_target,
                     без_баров=no_bars)


def record(run_id: str, report: RunReport, uni: Universe,
           decided: dict[str, engine.SymbolDecision]) -> None:
    """ШАГ 4 из четырёх: записать эмиссии и их исходы в боевой леджер (§8 этап 7).

    Единственное место в проекте, которое пишет сигналы. Открытые и несостоявшиеся
    сделки в исходы НЕ пишутся: исход у них ещё не наступил (§4.3).

    ⚠ Ничего НЕ СЧИТАЕТ. До 2026-08-06 здесь заново звались `build_all`, `map_levels`,
    `swings.detect`/`trend` и `emit.select` — то есть леджер считал сигнал вторым
    экземпляром, рядом с тем, что печатала карточка. Теперь берётся готовое решение
    (`engine.decide`), и разойтись двум потребителям нечем.

    ⚠ Леджер — ЖУРНАЛ, а не бэктест, и разница держится ровно на двух строках ниже:
    сигнал записывается один раз (повтор возвращает уже записанную строку вместе с
    моментом записи), а исход считается ТОЛЬКО по барам, закрывшимся позже этого момента.
    Прежняя редакция вычисляла исход вперёд по истории, лежавшей в памяти, и каждый
    прогон переигрывал её заново — числа владельцу печатались по бэктесту.
    """
    conn = store.open_production_ledger()
    stamp_ms = clock.now_ms()
    try:
        _resolve_pending(conn, report, uni)
        for sym in uni.symbols:
            d = decided.get(sym)
            if d is None:
                continue
            series = bars_of(report, sym)

            # Карта живёт МЕЖДУ прогонами (стр. 25, 31). Замер `probes.py map-drift`:
            # при сдвиге окна на 200 баров 2-3% активных уровней исчезали не потому, что
            # отработаны, а потому что структура уехала за край окна в 1000 баров.
            # ⚠ Отметка времени берётся ОДНА на прогон, а не по вызову. Первая редакция
            # звала `clock.now_ms()` дважды, и второй ответ был на миллисекунды позже
            # первого: строки, только что записанные этим же прогоном, попадали под
            # условие `last_seen < now` и объявлялись «перенесёнными из прошлых
            # прогонов». Живой прогон на ПУСТОЙ карте напечатал «перенесено 4» — число
            # правдоподобное и целиком выдуманное.
            seen = [(m.level, m.status.state) for m in d.mapped]
            sync = store.sync_levels(conn, sym, seen, stamp_ms)
            carried = store.carried_levels(conn, sym, stamp_ms)
            report.map_added += sync.added
            report.map_updated += sync.updated
            report.map_retired += sync.retired
            report.map_rejected.extend(sync.rejected)
            report.map_carried[sym] = carried

            for em in d.emissions:
                bars = series[em.level.timeframe]
                opened_at = bars[em.level.created_at_index].open_ms
                targets = [t for t in em.setup.targets
                           if t.role is geometry.TargetRole.PRIMARY]
                sig = store.record_signal(
                    conn, sym, em.level.timeframe, em.direction, opened_at,
                    em.setup.entry, em.ledger_stop, run_id, stamp_ms,
                    # Цель — та же ПЕРВАЯ основная, по которой считается РР (стр. 9).
                    # В леджер она пошла с v5: без неё исход сделки нельзя досчитать
                    # ни в одном прогоне, кроме выдавшего сигнал.
                    target=targets[0].price if targets else None,
                )
                if isinstance(sig, NotReady):
                    log.degraded("сигнал не записан", причина=sig.reason)
                    continue
                if sig.fresh:
                    report.signals_recorded += 1
                else:
                    report.signals_known += 1
                res = emit.outcome_of(em, bars, sig.recorded_at)
                report.emitted_outcomes[res.kind.value] = (
                    report.emitted_outcomes.get(res.kind.value, 0) + 1
                )
                rr = em.setup.rr(em.ledger_stop)
                if rr is not None:
                    report.emitted_rr.append(rr)
                if em.setup.entry:
                    report.emitted_stop_pct.append(
                        float(abs(em.setup.entry - em.ledger_stop) / em.setup.entry * 100)
                    )
                if res.kind.value in ("stop", "target", "ambiguous"):
                    assert res.closed_at_index is not None
                    err = store.record_outcome(
                        conn, sig.id, res.kind.value,
                        bars[res.closed_at_index].open_ms, res.exit_price, res.r,
                    )
                    if isinstance(err, NotReady):
                        log.degraded("исход не записан", причина=err.reason)
                    else:
                        report.outcomes_recorded += 1
                else:
                    # Состояние незакрытой сделки — в леджер (v4). До 2026-08-10 ответ
                    # «цена до входа не дошла» вычислялся и выбрасывался, из-за чего
                    # клетка сводки без исходов была неотличима от дефекта расчёта.
                    serr = store.record_signal_state(
                        conn, sig.id, res.kind.value, bars[-1].open_ms)
                    if isinstance(serr, NotReady):
                        log.degraded("состояние сигнала не записано", причина=serr.reason)
                    else:
                        report.states_recorded += 1

            # Сигналы от переприора — второй тип (kind='pp', стр. 50; реестр строка 3,
            # эмиссия добавлена 2026-08-10). В леджер идут только сделки С ЦЕЛЬЮ:
            # исход без цели не измерим. opened_at — бар ПОДТВЕРЖДЕНИЯ слома: раньше
            # него сигнала не существовало, а бар теста может быть далеко позже.
            for pssig in d.pp_signals:
                ps = pssig.setup
                if ps.target is None:
                    continue
                pbars = series.get(pssig.timeframe)
                if not pbars:
                    continue
                opened_at = pbars[pssig.pp.confirmed_at_index].open_ms
                row = store.record_signal(
                    conn, sym, pssig.timeframe, ps.side, opened_at,
                    Decimal(str(ps.entry)), Decimal(str(ps.stop)), run_id, stamp_ms,
                    kind="pp",
                    target=None if ps.target is None else Decimal(str(ps.target)),
                )
                if isinstance(row, NotReady):
                    log.degraded("ПП-сигнал не записан", причина=row.reason)
                    continue
                if row.fresh:
                    report.pp_signals_recorded += 1
                else:
                    report.pp_signals_known += 1
                pres = outcome_resolve(
                    side=(levels.LevelSide.LONG if ps.side == "long"
                          else levels.LevelSide.SHORT),
                    entry=Decimal(str(ps.entry)), stop=Decimal(str(ps.stop)),
                    target=Decimal(str(ps.target)), bars=pbars,
                    from_index=emit.first_bar_after(
                        pbars, pssig.timeframe, row.recorded_at,
                        pssig.pp.confirmed_at_index + 1),
                )
                if pres.kind.value in ("stop", "target", "ambiguous"):
                    assert pres.closed_at_index is not None
                    err = store.record_outcome(
                        conn, row.id, pres.kind.value,
                        pbars[pres.closed_at_index].open_ms, pres.exit_price, pres.r,
                    )
                    if isinstance(err, NotReady):
                        log.degraded("исход ПП-сигнала не записан", причина=err.reason)
                    else:
                        report.outcomes_recorded += 1
                else:
                    serr = store.record_signal_state(
                        conn, row.id, pres.kind.value, pbars[-1].open_ms)
                    if isinstance(serr, NotReady):
                        log.degraded("состояние ПП-сигнала не записано",
                                     причина=serr.reason)
                    else:
                        report.states_recorded += 1
    finally:
        conn.close()


LIVE_TRADES_KEEP_DAYS = 3
"""Сколько суток живого потока сделок держать в памяти. ⚠ ЧИСЛО НЕ ЗАМЕРЕНО ТОЧНО.

Живой поток нужен ровно там, где архив ещё не покрывает: суточный файл публикуется с
задержкой (замер 2026-08-05: 03.08 и 04.08 отдавали HTTP 404, то есть отставание доходило
до двух суток). Трое суток — это наблюдённое отставание плюс запас в сутки.

Цена ошибки в сторону «мало» НЕ молчаливая, и это главное: окно, потерявшее покрытие,
отдаётся `WindowSource` как `NotReady` с перечислением недостающих суток (§4.3), а не
усечённым профилем. Цена ошибки в сторону «много» — память, растущая линейно по суткам,
и она-то и есть причина, по которой предел вообще существует.
"""

CYCLE_FIELDS = (
    "frames_written", "cards_written", "archive_slices_written",
    "signals_recorded", "signals_known", "pp_signals_recorded", "pp_signals_known",
    "outcomes_recorded", "outcomes_resolved_late", "states_recorded",
    "pending_no_target", "pending_no_bars", "emitted_outcomes",
    "emitted_rr", "emitted_stop_pct",
    "map_added", "map_updated", "map_retired", "map_rejected", "map_carried",
    "backfill_days_loaded", "backfill_days_missing", "backfill_trades",
    "backfill_structures", "backfill_structures_old", "backfill_days_capped",
)
"""Поля отчёта, относящиеся к ОДНОМУ расчёту, а не ко всему времени работы.

В снимке они обнуляются. Иначе служба за сутки накопила бы в `emitted_rr` по записи на
каждую эмиссию каждого цикла — список, растущий без предела, — а «карточек записано»
означало бы сумму по всем циклам, то есть не отвечало бы ни на один вопрос.

⚠ Список ведётся руками. Новое поле расчёта, забытое здесь, будет накапливаться молча.
"""


def _copy_series(st: SeriesState) -> SeriesState:
    """Копия состояния ряда для снимка: изменяемые списки отвязываются от живых.

    Копируются именно списки, не бары: `Bar` заморожен, и делить его между снимком и
    сбором безопасно. Без отвязки списков расчёт в рабочем потоке читал бы ряд, в который
    задача опроса дописывает.
    """
    return st.model_copy(update={
        "bars": list(st.bars),
        "gaps": list(st.gaps),
        "rejected_bars": list(st.rejected_bars),
        "rejected_at_ms": list(st.rejected_at_ms),
    })


class Collector:
    """Сбор данных, живущий ДОЛЬШЕ одного расчёта. Основа службы 24/7 (§8, находка А-1).

    ⚠ Что здесь изменилось по существу. `collect(seconds)` был ПАКЕТНЫМ заданием: открыть
    соединение, собрать N секунд, снять задачи, посчитать, выйти. Разбор 2026-08-04 назвал
    это первой строкой списка («модели исполнения 24/7 нет»), и из неё же выводил класс
    дефектов Д-3, Д-5, Д-6: сведение часов один раз за прогон, разрывы, считаемые только
    в засеве, пропущенное за обрыв — всё это не проявляется на девяноста секундах.

    Разделение здесь ровно одно и оно всё объясняет: СБОР идёт непрерывно, РАСЧЁТ идёт
    циклами. Отсюда три свойства, которых у пакетной схемы быть не могло:

      * `snapshot()` отдаёт расчёту согласованный срез, не останавливая сбор;
      * контейнеры сделок ДВОЙНЫЕ: в один пишет задача, из другого читает расчёт;
      * умершая задача поднимается (`_supervise`), а не остаётся мёртвой до конца.

    Пакетный `collect` теперь тоже построен на этом классе — иначе путей стало бы два, и
    расходиться они начали бы там, где это не проверяется.
    """

    def __init__(self, uni: Universe, seed_limit: int, *, keep_bars: int = 0,
                 keep_trade_days: int = LIVE_TRADES_KEEP_DAYS) -> None:
        self.uni = uni
        self.seed_limit = seed_limit
        self.keep_bars = keep_bars
        self.keep_trade_days = keep_trade_days
        self.ex = Exchange()
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []
        self.seq: dict[str, TradeSequence] = {}

        self.binned: dict[str, BarBinnedTrades] = {}
        """Раскладка сделок по корзинам, НАКОПЛЕННАЯ. Читает расчёт."""

        self.live_hist: dict[str, TradeHistogram] = {}
        self.live_binned: dict[str, BarBinnedTrades] = {}
        """Куда пишет задача сделок. Вливаются в накопленное на границе цикла."""

        self._report: RunReport | None = None
        self._started_ns = 0
        self._absorbed_trades = 0
        """Сделки, уже перенесённые в снимки. Живые прибавляются при чтении счётчиков."""

    @property
    def report(self) -> RunReport:
        if self._report is None:
            raise RuntimeError("сборщик не запущен — отчёт появляется после start()")
        return self._report

    async def start(self) -> None:
        """Открыть биржу, засеять ряды и поднять задачи наблюдения."""
        sync = await self.ex.open()
        self._report = RunReport(sync=sync)
        self._started_ns = clock.monotonic_ns()
        log.info("засев", символов=len(self.uni.symbols), тф=len(self.uni.timeframes),
                 баров_на_ряд=self.seed_limit)
        await seed(self.ex, self.uni, self.report, self.seed_limit)
        log.info("засеяно", баров=self.report.seeded_bars,
                 запросов=self.report.seed_checked)
        self._launch()
        log.info("наблюдение начато", задач=len(self.tasks))

    def _spawn(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        self.tasks.append(asyncio.create_task(
            _supervise(name, factory, self.report, self.stop), name=name))

    def _launch(self) -> None:
        """Поднять задачи наблюдения.

        ⚠ `lambda st=st:` — не украшение. Замыкание в Python держит ПЕРЕМЕННУЮ, а не её
        значение, поэтому без связывания через умолчание все задачи опрашивали бы
        последний ряд цикла, а остальные не опрашивались бы вовсе. Отказ был бы полностью
        молчаливым: задач столько же, счётчики растут, приёмка зелёная.
        """
        rep = self.report
        for st in rep.series.values():
            # Ряды, не давшие засева, не опрашиваются: без базы добирать нечего, а
            # `NotReady` уже назван в отчёте. Иначе опрос молча создавал бы ряд
            # из трёх баров там, где засев честно отказал.
            if st.not_ready is not None:
                continue
            self._spawn(
                f"бары {st.symbol} {st.timeframe}",
                lambda st=st: _poll_bars_impl(  # type: ignore[misc]
                    self.ex, st, rep, self.stop, self.keep_bars),
            )
        for sym in self.uni.symbols:
            inst = self.ex.instrument(sym)
            if isinstance(inst, NotReady):
                continue
            # Корзина — самый младший ТФ вселенной: бары старших кратны ему, значит
            # окно любой структуры складывается из целых корзин (см. BarBinnedTrades).
            bucket = min(TIMEFRAME_MS[tf] for tf in self.uni.timeframes)
            self.binned[sym] = BarBinnedTrades(
                symbol=sym, tick_size=inst.tick_size, bucket_ms=bucket)
            h = TradeHistogram(symbol=sym, tick_size=inst.tick_size)
            b = BarBinnedTrades(symbol=sym, tick_size=inst.tick_size, bucket_ms=bucket)
            self.live_hist[sym] = h
            self.live_binned[sym] = b
            seq = self.seq.setdefault(sym, TradeSequence(sym))
            self._spawn(
                f"сделки {sym}",
                lambda sym=sym, h=h, b=b, seq=seq: _watch_trades_impl(  # type: ignore[misc]
                    self.ex, sym, h, b, seq, self.stop),
            )
        self._spawn("пересведение часов",
                    lambda: _resync_clock_impl(self.ex, rep, self.stop))
        self._spawn("перечитывание рынков",
                    lambda: _reload_markets_impl(self.ex, self.uni, rep, self.stop))
        self._spawn("сердцебиение", lambda: _heartbeat_impl(rep, self.stop))

    def refresh_counters(self) -> None:
        """Перенести в отчёт всё, что живёт на объекте биржи и в приборах потока.

        Зовётся и при снятии снимка, и при остановке. ⚠ Второе — не аккуратность: до
        2026-08-06 итоговая сводка службы печатала значения ПОСЛЕДНЕГО СНИМКА, то есть
        всё, что случилось после него, в неё не попадало. Для «потеряно потоком» это
        означало бы ноль ровно в том прогоне, где потеря случилась под конец.
        """
        rep = self.report
        rep.taken_at_ms = clock.now_ms()
        rep.uptime_s = (clock.monotonic_ns() - self._started_ns) / 1e9
        rep.sync = clock.sync_state()
        rep.clock_age_ms = clock.age_ms()
        rep.clock_stale = clock.is_stale()
        rep.weight_limit = self.ex.weight_limit
        rep.weight_peak = self.ex.weight_peak
        rep.weight_reads = self.ex.weight_reads
        rep.rest_rate_limited = self.ex.rest_rate_limited
        rep.rest_gate_held = self.ex.rest_gate_held
        rep.rest_errors = dict(sorted(self.ex.rest_errors.items()))
        rep.ws_reconnects = sum(self.ex.ws_reconnects.values())
        rep.ws_stream_errors = sum(self.ex.ws_errors.values())
        rep.trade_gaps = sum(s.gaps for s in self.seq.values())
        rep.trade_gap_events = sum(s.gap_events for s in self.seq.values())
        rep.trade_gaps_recovered = self.ex.trades_recovered
        rep.trade_gaps_unrecovered = self.ex.trades_unrecovered
        rep.trade_ids_checked = sum(s.checked for s in self.seq.values())
        # ⚠ Принятое считается ВМЕСТЕ с ещё не перенесённым в снимок. Иначе «принято» и
        # «номеров сверено» описывают разные окна: первое — до последнего снимка, второе
        # — до сейчас. Живой прогон 2026-08-06 напечатал «принято 302, номеров сверено
        # 635», и это выглядело как потеря половины, хотя было расхождением знаменателей.
        rep.trades_total = (self._absorbed_trades
                            + sum(h.trades_seen for h in self.live_hist.values()))
        # Прогон досюда доходит только при пройденной проверке: `open()` иначе бросает
        # `CapabilityMissing`. Число нужно, чтобы «отсутствует 0» имело знаменатель.
        rep.capabilities_checked = len(REQUIRED_CAPABILITIES)

    def snapshot(self) -> RunReport:
        """Согласованный срез собранного. Участок БЕЗ ОЖИДАНИЯ — это условие правильности.

        Пока функция не отдаёт управление, задачи наблюдения выполниться не могут: в
        одном цикле событий переключение происходит только на `await`. Значит всё, что
        здесь сделано, сделано между двумя состояниями сбора, а не поперёк них.
        Появление `await` внутри превратит срез в смесь двух моментов — тихо, без
        единого исключения. Поэтому условие держит не докстрока, а gates/atomic_sections.py:
        метка в первой строке выше — ровно то, что он ищет.

        Что именно делается:
          * живые контейнеры сделок вливаются в накопленные и обнуляются;
          * корзины старше горизонта забываются (иначе память растёт без предела);
          * ряды копируются — списки отвязываются от тех, в которые пишет опрос;
          * счётчики биржи и потоков переносятся в отчёт;
          * поля ОДНОГО расчёта обнуляются (`CYCLE_FIELDS`).
        """
        rep = self.report
        self.refresh_counters()

        cycle_hist: dict[str, TradeHistogram] = {}
        for sym, live in self.live_hist.items():
            h = TradeHistogram(symbol=sym, tick_size=live.tick_size)
            h.absorb(live)
            live.clear()
            self._absorbed_trades += h.trades_seen
            cycle_hist[sym] = h
        for sym, live_b in self.live_binned.items():
            self.binned[sym].absorb(live_b)
            live_b.clear()

        if self.keep_trade_days > 0:
            cutoff = clock.now_ms() - self.keep_trade_days * 86_400_000
            for acc in self.binned.values():
                rep.live_buckets_dropped += acc.drop_before(cutoff)

        snap = rep.model_copy()
        snap.series = {k: _copy_series(st) for k, st in rep.series.items()}
        snap.histograms = cycle_hist
        snap.binned = self.binned
        for name in CYCLE_FIELDS:
            setattr(snap, name,
                    RunReport.model_fields[name].get_default(call_default_factory=True))
        return snap

    async def shutdown(self) -> None:
        """Остановить наблюдение и закрыть соединение. Разбирает, кто чем кончил.

        Терпима к незапущенному сборщику: `start()` мог упасть на `open()` (например
        `CapabilityMissing`), и соединение всё равно обязано закрыться — иначе служба
        оставляет висеть сокет и печатает предупреждение aiohttp вместо причины отказа.
        """
        self.stop.set()
        for t in self.tasks:
            t.cancel()
        if self._report is None:
            await self.ex.close()
            return
        self.refresh_counters()
        # ⚠ Результаты РАЗБИРАЮТСЯ, а не выбрасываются. `return_exceptions=True` нужен,
        # чтобы дождаться всех, но проглатывать его молча нельзя: до 2026-08-04 именно
        # здесь исчезали умершие задачи. `CancelledError` — штатная остановка, всё
        # остальное — отказ, о котором обязана знать сводка (§4.3).
        #
        # После введения надзора сюда доходит только то, что пробило сам надзор, — то
        # есть его собственный сбой. Смерти поднадзорных задач считает `_supervise`, и
        # двойного счёта не возникает.
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        for task, res in zip(self.tasks, results, strict=True):
            if isinstance(res, asyncio.CancelledError) or not isinstance(res, BaseException):
                continue
            _note_death(self.report, task.get_name(), res)
        await self.ex.close()


async def collect(uni: Universe, seconds: int, seed_limit: int,
                  horizon_days: int = 90) -> tuple[RunReport, dict[str, TradeWindows]]:
    """ШАГ 1 из четырёх: ТОЛЬКО добыть данные. Ни карточки, ни леджера.

    Возвращает отчёт и источники профиля. Сеть трогается только здесь — дальше три шага
    работают на кадрах и кэше, и потому проверяемы отдельно от биржи.

    ⚠ Это ПАКЕТНЫЙ путь: собрать `seconds` секунд и остановиться. Он остаётся ради
    `hunter run` и `hunter check` — проверки на фиксированном окне, которую владелец
    запускает руками, — но боевое исполнение теперь `hunter serve` (`service.serve`).
    Оба построены на `Collector`, и различаются ровно тем, сколько раз берётся снимок:
    здесь один, там — по одному на цикл, бесконечно.
    """
    c = Collector(uni, seed_limit)
    try:
        await c.start()
        log.info("наблюдение", потоков=len(c.tasks), секунд=seconds)
        await asyncio.sleep(seconds)

        # Повторный замер часов — оценить, насколько сдвиг уползает (задача 1.3).
        # ⚠ Обёрнут 2026-08-10 (хвост ревизии транспорта): сетевой сбой ЗДЕСЬ ронял
        # весь пакетный прогон уже ПОСЛЕ удавшегося сбора — часы с известным возрастом
        # лучше, чем потерянные данные. Служба (`_resync_clock_impl`) обёрнута давно.
        before = c.report.sync.offset_ms
        try:
            again = await clock.measure(c.ex.fetch_server_ms)
        except ccxt.BaseError as e:
            c.report.clock_resync_failures += 1
            log.degraded("повторный замер часов не удался — оставлены прежние",
                         возраст_мс=clock.age_ms(), причина=f"{type(e).__name__} {e}")
        else:
            c.report.clock_drift_ms = again.offset_ms - before
            c.report.clock_recheck_after_s = seconds
            clock.set_sync(again)

        report = c.snapshot()
        # Долив архива — ПОСЛЕ наблюдения и до сборки карточки: без него окна
        # исторических структур не покрыты и уровней не бывает вовсе.
        await backfill_trades(c.ex, uni, report, horizon_days)
        # Источники строятся ДО закрытия соединения: им нужен `market_id` инструмента.
        # Сеть после этого не трогается — читается только кэш на диске.
        sources = {sym: src for sym in uni.symbols
                   if (src := trade_source(c.ex, sym, report)) is not None}
    finally:
        await c.shutdown()
    # Смерти, разобранные при ОСТАНОВКЕ, обязаны попасть в возвращаемый отчёт: снимок
    # сделан раньше неё, и без переноса приёмка их бы не увидела.
    report.watch_deaths = c.report.watch_deaths
    report.watch_restarts = c.report.watch_restarts
    report.watch_failures = list(c.report.watch_failures)
    return report, sources


OUTCOME_LABEL = {
    "not_filled": "цена не дошла до входа",
    "open": "ещё не закрыта",
    "stop": "по стопу",
    "target": "по цели",
    "ambiguous": "неоднозначно (стоп и цель в одном баре)",
}
"""Подписи исходов для владельца, который не программист (§7.6).

Полнота обеспечена не глазами: ключи сверяются с `OutcomeKind` при печати, и отсутствие
подписи падает по `KeyError`, а не печатает пустую строку.
"""


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _spread(xs: list[float]) -> str:
    """Медиана и края. Одно среднее по такой выборке скрывает ровно то, что важно."""
    if not xs:
        return "нечего считать (0 значений)"
    return (f"значений {len(xs)}, медиана {_median(xs):.3f}, "
            f"от {min(xs):.3f} до {max(xs):.3f}")


def print_report(r: RunReport) -> int:
    """Печатает приёмку. Возвращает число нарушений.

    ⚠ Время НЕ читается с часов и больше НЕ передаётся аргументом: оно берётся из самого
    отчёта (`taken_at_ms`). §10.3 требует, чтобы время входило как данные, а не бралось
    внутри, — и момент снятия снимка это данные ровно в том же смысле, что и бары.

    Аргумент был опасен именно тем, что выглядел правильным. Вызывающий передавал
    `clock.now_ms()`, то есть момент ПЕЧАТИ, а бары в отчёте относились к моменту СНЯТИЯ.
    В пакетном прогоне разница была секундами и терялась в допуске; у службы между ними
    лежит весь расчёт — замер 2026-08-06: 25 секунд, — и если за это время пересекалась
    граница 5м, отчёт печатал отставание, которого не было. Разбор — в `taken_at_ms`.
    """
    now_ms = r.taken_at_ms
    print()
    print("=" * 78)
    print("ПРИЁМКА ЭТАПА 1 — FOUNDATION.md §8")
    print("=" * 78)

    print("\n0. РЕЖИМ ИСПОЛНЕНИЯ (§8, находка А-1)")
    if r.cycles:
        print(f"   СЛУЖБА: работает {r.uptime_s / 3600:.2f} ч, циклов расчёта {r.cycles}")
        print(f"   длительность последнего цикла расчёта: {r.cycle_seconds:.1f} с")
        print("   ⚠ числа СБОРА ниже — с запуска службы; числа РАСЧЁТА — за последний цикл")
    else:
        print(f"   ПАКЕТНЫЙ ПРОГОН: собрано {r.uptime_s:.0f} с, расчёт один, после сбора")
    print(f"   сделок принято всего: {r.trades_total}, "
          f"баров отрезано хвостом: {r.bars_trimmed}, "
          f"корзин сделок забыто: {r.live_buckets_dropped}")

    print("\n1. ЧАСЫ (§6)")
    print(f"   сдвиг биржа−локальные    : {r.sync.offset_ms:+d} мс")
    print(f"   неопределённость (±rtt/2): ±{r.sync.rtt_ms // 2} мс")
    print(f"   замеров: {r.sync.samples}")
    print("   якорь: МОНОТОННЫЕ часы — прыжок системного времени сдвиг не меняет (Д-5)")
    print(f"   пересведений за прогон: {r.clock_resyncs} "
          f"(каждые {CLOCK_RESYNC_S:.0f} с), отказов {r.clock_resync_failures}")
    print(f"   возраст сведения на момент снимка: {r.clock_age_ms / 1000:.0f} с "
          f"(порог устаревания {clock.MAX_SYNC_AGE_MS / 1000:.0f} с) — "
          + ("УСТАРЕЛО" if r.clock_stale else "свежо"))
    if r.clock_resyncs:
        print(f"   наибольший уход сдвига между сведениями: {r.clock_drift_max_ms:+d} мс")
    else:
        print(f"   ⚠ прогон короче {CLOCK_RESYNC_S:.0f} с — дрейф между сведениями "
              f"не измерялся, ноль ниже ничего не значит")
    if r.clock_drift_ms is not None:
        print(f"   уход сдвига за {r.clock_recheck_after_s} с: {r.clock_drift_ms:+d} мс")

    ready = [s for s in r.series.values() if s.not_ready is None]
    missing = [s for s in r.series.values() if s.not_ready is not None]

    print(f"\n2. СВЕЖЕСТЬ КАДРОВ — проверено рядов {len(r.series)}")
    print(f"   судится НА МОМЕНТ СНЯТИЯ снимка ({now_ms}), а не на момент печати")
    stale: list[str] = []
    offgrid_seed: list[str] = []
    if now_ms == 0:
        # Отчёт построен не снимком: момента, на который судить, нет. Молча подставить
        # сюда часы значило бы измерить длительность расчёта и назвать её отставанием.
        print("   ⚠ момент снятия НЕ ПРОСТАВЛЕН — свежесть не измерена, "
              "строки ниже ничего не значат")
    for st in sorted(ready, key=lambda s: (s.timeframe, s.symbol)):
        last = st.bars[-1]
        step = tf_ms(st.timeframe)
        expected = expected_last_closed_open_ms(st.timeframe, now_ms)
        behind = (expected - last.open_ms) // step
        # ⚠ Отставание меряется ВРЕМЕНЕМ, а не только числом баров. Сразу после границы
        # ТФ каждый ряд отстаёт на бар — ровно до того, как отработает опрос через
        # `POLL_OFFSET_S`. Прежняя редакция считала нарушением ЛЮБОЕ `behind > 0`, и
        # прогон 2026-08-05 на 420 с дал три отставших ряда 5м из трёх: он кончился
        # между границей и опросом, задачи были сняты, и отчёт напечатал артефакт
        # остановки как дефект данных. «Свежесть» без времени не определена.
        # Просрочка считается от ПЕРВОЙ НЕДОСТАЮЩЕЙ свечи, а не от ожидаемой последней:
        # недостающая открывается в `last + step` и закрывается в `last + 2*step`. Первая
        # редакция этой правки брала `expected + step`, и величина не росла с
        # отставанием — ряд, отставший на час, нарушением не считался. Поймано пробником.
        overdue_ms = now_ms - (last.open_ms + 2 * step)
        if now_ms and behind > 0 and overdue_ms > FRESHNESS_GRACE_S * 1000:
            stale.append(f"{st.symbol} {st.timeframe}: отстаёт на {behind} баров, "
                         f"просрочено {overdue_ms / 1000:.0f} с")
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
    explained = sum(len(explained_gaps(s)) for s in ready)
    unexplained = total_gaps - explained
    print(f"   разрывов сетки внутри рядов: {total_gaps} "
          f"(объяснено отклонёнными барами {explained}, необъяснённых {unexplained}; "
          f"проверено баров {r.seeded_bars})")
    for st in ready:
        for a, b in st.gaps[:3]:
            print(f"   РАЗРЫВ: {st.symbol} {st.timeframe} между {a} и {b}")

    polled = sum(s.polled_bars for s in r.series.values())
    requests = sum(s.poll_requests for s in r.series.values())
    late = sum(s.poll_late for s in r.series.values())
    not_ready = sum(s.poll_not_ready for s in r.series.values())
    catchups = sum(s.poll_catchups for s in r.series.values())
    print("\n4. ЗАКРЫТАЯ СВЕЧА: ОПРОС ПРОТИВ ЧАСОВ (§6)")
    print(f"   опросов сделано: {requests}, баров добрано: {polled}")
    print(f"   из них ДОГОНЯЮЩИХ (ряд отставал на момент запроса): {catchups}")
    print(f"   биржа опоздала с ожидаемой свечой: {late} из {requests}"
          f"{f' ({late / requests * 100:.1f}%)' if requests else ''}")
    print(f"   опрос не дал ряда (пусто/битый бар/вне сетки): {not_ready}")
    print(f"   отступ опроса POLL_OFFSET_S = {POLL_OFFSET_S} с — НЕ ЗАМЕРЕН, "
          f"верхняя оценка; строка «биржа опоздала» и есть его замер (Ж-1)")

    if requests == 0:
        print("   ⚠ ОПРОСОВ НЕ БЫЛО: прогон короче одного шага младшего ТФ — "
              "число выше ничего не измеряет")


    # Ж-11: после перехода на REST лимит биржи — главный отказ системы. Превышение даёт
    # HTTP 429, продолжение после него — бан по IP на срок до трёх суток. Лимит СПРОШЕН
    # у биржи, а не зашит: 2400/мин во всех прежних расчётах бюджета были «общеизвестным».
    print(f"   возможности ccxt проверены до прогона: {r.capabilities_checked}, "
          f"отсутствует 0 — иначе прогона бы не было")

    print("\n4а. ЛИМИТ БИРЖИ (Ж-11)")
    if r.weight_reads == 0:
        print("   ⚠ заголовок X-MBX-USED-WEIGHT-1M не пришёл НИ РАЗУ — "
              "потребление не измерено, ноль ниже ничего не значит")
    if r.weight_limit is None:
        print(f"   лимит у биржи НЕ ПРОЧИТАН; пик потребления {r.weight_peak} "
              f"(замеров {r.weight_reads}) — сравнивать не с чем")
    else:
        weight_share = r.weight_peak / r.weight_limit * 100
        print(f"   лимит биржи: {r.weight_limit} веса в минуту (прочитан у неё же)")
        print(f"   пик потребления: {r.weight_peak} — {weight_share:.1f}% лимита "
              f"(замеров {r.weight_reads})")
    print(f"   ответов «лимит превышен»: {r.rest_rate_limited}"
          f" (придержано глобальной паузой: {r.rest_gate_held})")
    if r.rest_errors:
        print(f"   отказы REST по классам: "
              f"{', '.join(f'{k} {v}' for k, v in r.rest_errors.items())}")
    else:
        print("   отказов REST по классам: ни одного")
    print("\n4в. СОСТАВ БИРЖИ ПОСРЕДИ ПРОГОНА (§5, Б-4)")
    print(f"   рынки перечитаны: {r.markets_reloads} раз (каждые {MARKETS_RELOAD_S:.0f} с), "
          f"отказов {r.markets_reload_failures}")
    if not r.markets_reloads:
        print(f"   ⚠ прогон короче {MARKETS_RELOAD_S:.0f} с — состав не сверялся ни разу, "
              f"пустые списки ниже ничего не значат")
    else:
        print(f"   символов сверено: {r.markets_checked}")
    print(f"   ШАГ ЦЕНЫ изменился у: {len(r.tick_changes)}")
    for x in r.tick_changes:
        print(f"     {x}  ← сетка бинов профиля посчитана по ПРЕЖНЕМУ шагу")
    print(f"   снято с торгов посреди прогона: {len(r.delisted_mid_run)}")
    for s in r.delisted_mid_run:
        print(f"     {s}")

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
    # Полнота потока меряется НЕ нашим счётом, а номерами биржи: aggTrade нумерует
    # сделки подряд, значит разрыв номеров — сделки, до нас не дошедшие (А-1).
    if r.trade_ids_checked == 0:
        print("   ⚠ номера сделок не сверялись НИ РАЗУ — полнота потока не измерена, "
              "ноль разрывов ниже ничего не значит")
    print(f"   ПОТЕРЯНО потоком (разрывы номеров aggTrade): {r.trade_gaps} сделок "
          f"в {r.trade_gap_events} местах; номеров сверено {r.trade_ids_checked}")
    if r.trade_gaps:
        print(f"   из них добрано REST-догоном fromId: {r.trade_gaps_recovered}, "
              f"осталось потерянными: {r.trade_gaps_unrecovered}")

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
    print(f"   сигналов записано ВПЕРВЫЕ: {r.signals_recorded}, "
          f"было известно раньше: {r.signals_known}")
    print(f"   сигналов от ПП (kind=pp): впервые {r.pp_signals_recorded}, "
          f"известно раньше: {r.pp_signals_known}")
    print(f"   исходов записано: {r.outcomes_recorded}, из них ДОРЕШАНО у сигналов, "
          f"которые прогон заново не эмитировал: {r.outcomes_resolved_late}")
    print(f"   состояний незакрытых сделок записано: {r.states_recorded} "
          f"(мимо входа / сделка идёт — §4.3, схема v4)")
    if r.pending_no_target or r.pending_no_bars:
        print(f"   дорешать НЕЛЬЗЯ: без сохранённой цели {r.pending_no_target} "
              f"(записаны до схемы v5), без ряда ТФ в этом прогоне {r.pending_no_bars}")
    print("   исход считается ТОЛЬКО по барам, закрывшимся после записи сигнала:")
    print("   у свежего сигнала исхода нет и быть не может — это журнал, а не бэктест.")
    emitted = sum(r.emitted_outcomes.values())
    print("\n7а. ЧТО СТОИТ ЗА СУММОЙ R — знаменатель (§4.3, стр. 9)")
    print(f"   эмиссий всего: {emitted}")
    for kind in OutcomeKind:
        n = r.emitted_outcomes.get(kind.value, 0)
        share = f"{n / emitted * 100:.0f}%" if emitted else "—"
        print(f"     {OUTCOME_LABEL[kind.value]:24} {n:5d}  {share:>5}")
    print(f"   РР (рисковый стоп, тот же, что в леджере): {_spread(r.emitted_rr)}")
    golden = sum(1 for x in r.emitted_rr if x >= geometry.GOLDEN_RR)
    print(f"     из них ≥ 1к{geometry.GOLDEN_RR:.0f} — «золотым стандартом» стр. 9: "
          f"{golden} из {len(r.emitted_rr)}")
    print(f"   дистанция стопа, % цены: {_spread(r.emitted_stop_pct)}")
    print("   КОМИССИИ, ФАНДИНГ И ПРОСКАЛЬЗЫВАНИЕ НЕ МОДЕЛИРУЮТСЯ НИГДЕ.")
    if r.emitted_stop_pct:
        med = _median(r.emitted_stop_pct)
        print(f"     ориентир: круговая комиссия тейкера ~0.1% цены при медианном стопе "
              f"{med:.3f}% — это около {0.1 / med * 100:.0f}% риска на сделку")

    carried = sum(len(v) for v in r.map_carried.values())
    print("\n7б. КАРТА УРОВНЕЙ МЕЖДУ ПРОГОНАМИ (стр. 25, 31)")
    print(f"   новых уровней: {r.map_added}, подтверждено прежних: {r.map_updated}")
    print(f"   снято по курсу (отработан/пробит): {r.map_retired}")
    print(f"   отклонено схемой карты: {len(r.map_rejected)}")
    for why in r.map_rejected[:5]:
        print(f"     ОТКЛОНЕНО {why}")
    print(f"   ПЕРЕНЕСЕНО из прошлых прогонов: {carried} "
          f"(активны, но в этом окне баров не пересчитаны)")
    for sym, rows in sorted(r.map_carried.items()):
        for c in rows[:3]:
            print(f"     {sym:16} {c.timeframe:>3} {c.side:5} ПОК {c.price} "
                  f"(окно {c.from_ms}…{c.to_ms})")

    print("\n4б. ЗАДАЧИ СБОРА — живы ли (§4.3)")
    print(f"   задач наблюдения умерло: {r.watch_deaths}, из них поднято заново: "
          f"{r.watch_restarts}")
    if r.watch_deaths > len(r.watch_failures):
        print(f"     (примеров сохранено {len(r.watch_failures)} из {r.watch_deaths} — "
              f"предел WATCH_FAILURES_KEPT)")
    for why in r.watch_failures[:10]:
        print(f"     {why}")
    # Задержка цикла событий: прибор, без которого «расчёт не держит цикл» — слова.
    if r.heartbeats == 0:
        print("   ⚠ сердцебиение не тикнуло НИ РАЗУ — задержка цикла событий не "
              "измерена, ноль ниже ничего не значит")
    else:
        print(f"   наибольшая задержка цикла событий: {r.loop_stall_max_ms} мс "
              f"(тактов {r.heartbeats} по {HEARTBEAT_S:.0f} с)")
    # ⚠ Печатается ТО, ЧТО СЧИТАЕТСЯ. `ws_reconnects` инкрементируется на таймауте
    # ожидания и на сетевом сбое — то есть считает ПРЕРВАННЫЕ ОЖИДАНИЯ, а не
    # состоявшиеся переподключения: сокет переустанавливает ccxt по своему ping/pong, и
    # проект об этом не знает (находка Д-7). Строка «переподключений: N» владельцу лгала.
    # Поле не переименовано, чтобы не смешивать с переходом на REST; лжёт имя, не число.
    print(f"   прерванных ожиданий потока сделок (таймаут или сетевой сбой): "
          f"{r.ws_reconnects}, ошибок биржи: {r.ws_stream_errors}")

    # `poll_late` в нарушения НЕ входит: это замер отступа, а не дефект — бар доедет
    # следующим опросом. Входят внесеточные бары и опросы, не давшие ряда.
    #
    # ⚠ Смерти считает `watch_deaths`, а не длина списка примеров: список ограничен
    # сверху (`WATCH_FAILURES_KEPT`), и на службе, где смертей может быть больше предела,
    # он занижал бы число нарушений — тихо и в безопасную сторону.
    #
    # Потерянные потоком сделки — нарушение: профиль объёма §2.2 строится на сделках, и
    # дыра в них искажает ПОК, ничем себя не выдавая.
    #
    # Устаревшее сведение часов — тоже нарушение, а не замечание: §6 строит на нём ВСЁ
    # сравнение «сейчас против биржевой метки», и просроченный якорь означает, что
    # свежесть кадров выше измерялась неизвестно чем.
    violations = (r.watch_deaths
                  + len(stale) + len(missing) + unexplained + not_ready
                  + len(offgrid_seed) + r.trade_gap_events + int(r.clock_stale))
    print("\n8. ИТОГ")
    print(f"   деградаций отмечено: {log.degraded_count()}")
    print(f"   нарушений приёмки: {violations}")
    print("=" * 78)
    return violations
