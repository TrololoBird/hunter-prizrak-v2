"""Живой прогон и сводка приёмки этапа 1. FOUNDATION.md §8.

Приёмка §8: «живой прогон, кадры свежие, ни одного молчаливого пропуска».
Сводка печатает числа, а не слово «ОК»: пустой список нарушений сопровождается
числом проверенного, иначе он неотличим от непроведённой проверки.

Кадры сохраняются в parquet (§10.3) — без них детерминированный повтор невозможен.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import ccxt
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from . import archive, barstore, card, clock, emit, engine, geometry, levels, log, store
from .bars import (
    TIMEFRAME_MS,
    bars_needed,
    expected_last_closed_open_ms,
    find_gaps,
    on_grid,
    tf_ms,
)
from .config import Universe
from .exchange import (
    CATCHUP_MAX_BARS,
    CATCHUP_RETRY_S,
    OHLCV_PAGE,
    POLL_LIMIT,
    POLL_OFFSET_S,
    REQUIRED_CAPABILITIES,
    CapabilityMissing,
    Exchange,
)
from .models import (
    Bar,
    BarBinnedTrades,
    Instrument,
    NotReady,
    RawTrade,
    RunReport,
    SeriesState,
    TradeHistogram,
    TradeWindows,
    bin_index,
)
from .outcome import OutcomeKind
from .outcome import resolve as outcome_resolve
from .profile_source import PROFILE_LADDER, TVWindows, intrabar_timeframe
from .swings import detect as detect_swings
from .trading_range import detect as detect_ranges


def seed_depth(timeframe: str, horizon_days: int, seed_limit: int) -> int:
    """Сколько баров просить на этом ТФ. Единственное место, где решается глубина ряда.

    `seed_limit > 0` — прямое указание оператора, действует как раньше и одинаково для
    всех ТФ. `seed_limit <= 0` — глубина ВЫВОДИТСЯ из горизонта отдельно для каждого ТФ
    (`bars.bars_needed`), и ряды становятся сопоставимыми: 180 суток на 5м это 51 840
    баров, на 1Д — 180, и оба накрывают одно и то же календарное окно.

    Нижняя граница — `admission.seed_floor()`: строжайшее требование среди величин,
    которые система СЧИТАЕТ (§2.9 плюс atr14), сейчас это ema200 с 200 барами. Ряд
    короче не даёт доп-факторов независимо от горизонта.

    ⚠ До 2026-08-17 здесь стояло `max(REQUIRED_BARS.values())` = 304 бара от `adx14`
    с подписью «требование §2.9» — ложной дважды: §2.9 ADX не называет, и adx14 не
    считается нигде в `src`. Разбор: docs/audit/session-2026-08-17-auto.md.
    """
    from .admission import seed_floor

    # +1: просимая глубина включает ФОРМИНГ-БАР, который `fetch_closed_ohlcv` снимет.
    # Без единицы ряд из ровно floor баров давал floor-1 закрытых, и ema200 честно
    # уходила в NotReady на всех ТФ, где глубину задаёт floor, а не горизонт (1Д, 1Н
    # при горизонте 180 суток) — перекос вдоль ТФ, найден rules-auditor 2026-08-17.
    floor = seed_floor() + 1
    if seed_limit > 0:
        return max(floor, seed_limit)
    return bars_needed(timeframe, horizon_days, floor)


async def seed(ex: Exchange, uni: Universe, report: RunReport, limit: int,
               horizon_days: int = 0) -> None:
    """Засев рядов: хранилище на диске плюс добор хвоста у биржи.

    ⚠ ДО 2026-08-11 БАРЫ НЕ СОХРАНЯЛИСЬ ВОВСЕ, и каждый прогон качал все 162 ряда заново.
    История глубже одного запроса не накапливалась никогда — её некуда было класть, — а
    кэш сделок при этом копился с самого начала. Эта асимметрия и закрывается здесь.

    Порядок: прочитать сохранённое → спросить у биржи ТОЛЬКО хвост за последним
    сохранённым баром → дописать → отдать объединённый ряд. Пустое хранилище даёт ровно
    прежнее поведение: хвоста нет, просится всё окно.

    ⚠ Переписанные бары считаются отдельно (`bars_rewritten`). Закрытый бар неизменен;
    если биржа отдала по той же метке другие числа, это её правка задним числом либо наша
    ошибка склейки, и оба случая обязаны быть видны числом, а не расхождением карточки.
    """
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            log.degraded("инструмент недоступен", причина=inst.reason)
        market_id = None if isinstance(inst, NotReady) else inst.market_id
        for tf in uni.timeframes:
            st = SeriesState(symbol=sym, timeframe=tf)
            report.series[(sym, tf)] = st
            report.seed_checked += 1

            # Глубина решается ЗДЕСЬ и по каждому ТФ отдельно: одна цифра на все ТФ
            # делала карту несопоставимой самой с собой (см. `seed_depth`).
            depth = seed_depth(tf, horizon_days, limit)
            want_from = clock.now_ms() - depth * tf_ms(tf)
            stored: list[Bar] = []
            since_ms: int | None = None
            if market_id is not None:
                stored = barstore.load(uni.venue, market_id, tf, since_ms=want_from)
                since_ms = barstore.missing_tail_since(
                    uni.venue, market_id, tf, want_from)
                report.bars_from_store += len(stored)

            # Размер хвоста считается ПО РАЗРЫВУ, а не берётся константой.
            #
            # ⚠ ЗДЕСЬ СТОЯЛО `CATCHUP_MAX_BARS` (100), И ЭТО БЫЛ ДЕФЕКТ. Сто баров 5м —
            # восемь часов; служба, простоявшая сутки, добрала бы восьмую часть пропуска,
            # а остальное осталось бы дырой НАВСЕГДА: засев отрабатывает один раз, второй
            # попытки нет. Дыра при этом не молчала бы (`find_gaps` её печатает), но и не
            # закрывалась бы никогда.
            #
            # Верхняя граница — та же `depth`: просить больше, чем нужно ряду, незачем.
            if since_ms is None:
                ask = depth
            else:
                behind = max(0, clock.now_ms() - since_ms) // tf_ms(tf)
                ask = min(depth, int(behind) + 2)  # +2: текущий незакрытый и запас на край
            got = await ex.fetch_closed_ohlcv(sym, tf, limit=ask, since_ms=since_ms)
            if isinstance(got, NotReady):
                if not stored:
                    st.not_ready = got
                    log.degraded("засев пропущен", причина=got.reason)
                    continue
                # Хранилище есть, хвост не пришёл: ряд стар, но не пуст. Молчать нельзя —
                # иначе «данные есть» неотличимо от «данные свежие».
                report.seed_tail_failed += 1
                log.degraded("хвост ряда не добран — ряд отдан из хранилища",
                             символ=sym, тф=tf, из_хранилища=len(stored),
                             причина=got.reason)
                fresh: list[Bar] = []
            else:
                fresh = got.bars
                st.rejected_bars = got.rejected
                st.rejected_at_ms = got.rejected_at_ms

            if market_id is not None and fresh:
                added, rewritten = barstore.append(uni.venue, market_id, tf, fresh)
                report.bars_stored += added
                report.bars_rewritten += rewritten
                if rewritten:
                    log.warn("биржа отдала ИНЫЕ числа по уже сохранённым барам",
                             символ=sym, тф=tf, переписано=rewritten)
                merged = barstore.load(uni.venue, market_id, tf, since_ms=want_from)
            else:
                merged = _merge_bars(stored, fresh)

            # ⚠ Отдаётся ровно `limit` ПОСЛЕДНИХ баров, а не всё окно по времени. Разница
            # не косметическая: биржа отдавала счётом, и переход на границу по времени
            # сдвинул бы левый край ряда на бар — то есть изменил бы расчёт УЖЕ ЗДЕСЬ,
            # тогда как этап 1 обязан быть безразличным к нему. Глубина меняется на
            # этапе 2, осознанно и с диффом повтора. Хранилище при этом копит ВСЁ, что
            # пришло: обрезается только выдача.
            merged = merged[-depth:]

            if not merged:
                st.not_ready = NotReady(reason=f"{sym} {tf}: ряд пуст и в хранилище, и у биржи")
                continue
            st.bars = merged
            st.gaps = find_gaps(merged, tf)
            report.seeded_bars += len(merged)
            if st.gaps:
                log.warn("разрыв сетки", символ=sym, тф=tf, разрывов=len(st.gaps),
                         первый_после=st.gaps[0][0])


def _merge_bars(stored: list[Bar], fresh: list[Bar]) -> list[Bar]:
    """Слияние по метке открытия, приходящее побеждает. Запасной путь для случая, когда
    рынка нет в списке инструментов и писать в хранилище некуда."""
    by_open = {b.open_ms: b for b in stored}
    by_open.update({b.open_ms: b for b in fresh})
    return [by_open[k] for k in sorted(by_open)]


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


def percentile(values: list[int], q: float) -> int:
    """Перцентиль БЕЗ интерполяции: ближайший ранг, как считает большинство приборов.

    Своя реализация, а не `statistics.quantiles`, по одной причине: та интерполирует
    между соседями и на выборке из двух-трёх значений выдаёт число, которого в замере
    не было. Здесь всякое напечатанное число — настоящая наблюдённая задержка.
    """
    if not values:
        raise ValueError("перцентиль пустой выборки не определён")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[k]


class LagCell(BaseModel):
    """Задержка прихода баров ОДНОГО ТФ: сколько и какие (Т-0)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    bars: int = Field(gt=0)
    """ЗНАМЕНАТЕЛЬ. Перцентиль по трём барам и по тысяче — разные утверждения, и без
    этого числа их не различить."""

    p50_ms: int
    p95_ms: int
    max_ms: int


def arrival_lag_survey(r: RunReport) -> tuple[LagCell, ...]:
    """Задержка «закрытие бара → бар в ряду» по ТФ (задание Т-0).

    ⚠⚠ ЧТО ЭТО ЧИСЛО ЗНАЧИТ И ЧЕГО НЕ ЗНАЧИТ. Владелец назвал жалобу так: «у нас
    сигналы приходят с западанием, а сбор и обработка данных проходит слишком долго».
    До 2026-08-21 западание не измерялось НИЧЕМ: `poll_late` считает случаи «биржа не
    успела к нашему отступу», а миллисекунды пути не считал никто.

    В величину входят ДВА слагаемых, и их нельзя путать:
      * НАШ ОТСТУП `POLL_OFFSET_S` — мы сами не спрашиваем свечу раньше него. Это нижняя
        граница ответа ПО ПОСТРОЕНИЮ, и прибор не может выдать значение меньше;
      * всё, что сверх отступа, — ответ биржи, сеть и очередь наших задач.
    Поэтому приёмка печатает отступ рядом с перцентилем: без него p50 читается как
    «транспорт медленный», хотя это может быть целиком наша собственная пауза.

    ⚠ Вторая половина пути (расчёт) здесь НЕ учитывается: у неё другая единица
    наблюдения — прогон, а не бар. Она в `RunReport.stage_ms`.
    """
    by_tf: dict[str, list[int]] = {}
    for st in r.series.values():
        if st.arrival_lags_ms:
            by_tf.setdefault(st.timeframe, []).extend(st.arrival_lags_ms)
    return tuple(
        LagCell(timeframe=tf, bars=len(v), p50_ms=percentile(v, 0.50),
                p95_ms=percentile(v, 0.95), max_ms=max(v))
        for tf, v in sorted(by_tf.items(), key=lambda kv: TIMEFRAME_MS.get(kv[0], 0))
    )


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
        # Часы снимаются ОДИН раз на ответ, а не на бар: бары одного ответа пришли
        # вместе, и разные метки у них означали бы длительность нашего цикла, а не
        # задержку биржи.
        arrived_ms = clock.now_ms()
        step_ms = tf_ms(st.timeframe)
        for bar in got.bars:
            if last is not None and bar.open_ms <= last:
                continue
            st.bars.append(bar)
            st.polled_bars += 1
            # Т-0: задержка прихода — от ЗАКРЫТИЯ бара до его принятия в ряд.
            st.arrival_lags_ms.append(arrived_ms - (bar.open_ms + step_ms))
            if len(st.arrival_lags_ms) > ARRIVAL_LAGS_KEPT:
                del st.arrival_lags_ms[:-ARRIVAL_LAGS_KEPT]
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

WATCH_TRADES = False
"""Поднимать ли поток сделок (`watch_trades`) в боевом сборе. Задание Т-1, 2026-08-21.

⚠⚠ ВЫКЛЮЧЕН, ПОТОМУ ЧТО ЕГО НЕ ЧИТАЕТ РАСЧЁТ. Не «пока не нужен», а именно не читает —
проверено поимённо по всем потребителям:

  ПРОФИЛЬ ОБЪЁМА переведён на СВЕЧИ 2026-08-12 решением владельца: `trade_source`
    возвращает `TVWindows` поверх `barstore`, и `levels.build_all` получает именно её
    (разбор — docs/audit/poc-candles-vs-ticks-2026-08-12.md);
  УТОРГОВКА ЗА ЗОНОЙ (`absorption.measure`) берёт тот же `TradeWindows`, то есть те же
    свечи, а не поток;
  ОСТАЛЬНЫЕ потребители `RunReport.histograms`/`binned` — это запись кадров на диск
    (`persist_frames`) и строки приёмки о самом потоке. Оба описывают ПОТОК, а не
    расчёт: убрав поток, мы не отнимаем у расчёта ничего.

ЦЕНА, КОТОРУЮ ЭТО СНИМАЕТ: по сокету на символ (вселенная 25), сверка номеров сделок,
суточный кэш `data/aggcache` и всё, что с ним связано — при нулевом вкладе в результат.

⚠ ФАЙЛЫ КЭША НЕ УДАЛЯЮТСЯ. Останавливается только ЗАПИСЬ. Удаление собранного —
отдельное решение владельца, и хук `guard_destructive` его же и сторожит.

⚠ КОД ПОТОКА НЕ УДАЛЁН, и это не половинчатость: тиковый путь остаётся единственным
способом перепроверить свечной профиль против сделок, а именно такой перепроверкой
2026-08-12 и было принято решение о свечах. Мёртвым кодом он не становится — он
становится ВЫКЛЮЧЕННЫМ, и выключатель назван здесь.

КОНТРОЛЬ В ОБЕ СТОРОНЫ: с `True` приёмка печатает принятые сделки и растут
`trades_total`, `trade_ids_checked`; с `False` они честные нули, а строка приёмки
называет причину. Уровни, ПОК и зоны в обоих случаях одни и те же — это и есть
утверждение «расчёт их не читает», и проверяется оно повтором карточки.
"""

ARRIVAL_LAGS_KEPT = 500
"""Сколько последних задержек прихода хранить на ряд (Т-0, `SeriesState.arrival_lags_ms`).

⚠ Число выбрано, а не замерено, и оно НЕ порог: обрезается только длина хранимого
списка, ни одна задержка при этом не объявляется нормальной или чрезмерной. Пятьсот —
это больше суток пятиминутных баров (288 в сутки) и годы дневных, то есть перцентиль
считается по окну, покрывающему хотя бы полный суточный цикл на самом частом ТФ.
Пакетный прогон живёт минуты и до обрезки не доходит вовсе; ограничение существует ради
службы 24/7, где неограниченный список — утечка памяти того же класса, что и ряд без
`keep_bars`.
"""

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
) -> tuple[set[date], int, int, int]:
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

    Четвёртым возвращается ПРАВЫЙ КРАЙ самого свежего окна (мс): REST-добору
    незавершённых суток (решение владельца 2026-08-11) нужно знать, докуда качать, —
    дальше правого края окон сделки никем не читаются.
    """
    days: set[date] = set()
    used = dropped = 0
    max_hi = 0
    now = max((b[-1].open_ms for b in series.values() if b), default=0)
    cut = now - horizon_days * 86_400_000
    for tf, bars in series.items():
        if not bars:
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            continue
        for acc in detect_ranges(bars, sw, tf).closed:
            lo, hi = levels.structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
            if hi < cut:
                dropped += 1
                continue
            used += 1
            max_hi = max(max_hi, hi)
            d = datetime.fromtimestamp(lo / 1000, UTC).date()
            end = datetime.fromtimestamp((hi - 1) / 1000, UTC).date()
            while d <= end:
                days.add(d)
                d += timedelta(days=1)
    return days, used, dropped, max_hi


def profile_windows(
    series: dict[str, list[Bar]], horizon_days: int,
    intrabar_tf: str | None = None, frame_bars: int | None = None,
) -> tuple[list[tuple[int, int]], int, int, int, int]:
    """Окна структур, под которые нужен профиль.
    Возвращает (окна, взято, отброшено, окон_старшего_ТФ, окон_вне_кадра).

    ⚠ ОКНА, А НЕ СУТКИ. Прежний источник профиля (сделки) хранился суточными файлами,
    поэтому `find_gaps` отвечал множеством ДАТ. Свечи хранятся рядом по таймфрейму, и
    просить у биржи сутки целиком там, где структуре нужен час, — платить за то, что
    никто не прочитает.

    Горизонт отсекает старые структуры так же, как в `find_gaps`: правее правого края
    окна профиль никем не читается.

    ⚠ `intrabar_tf` (2026-08-18, правило TV): если задан, остаются только окна, которым
    по `profile_source.intrabar_timeframe` нужны свечи ИМЕННО этого ТФ. Окна старших
    ступеней лестницы читают ряды, уже собранные засевом, — качать под них минутки
    значило платить за то, что профиль не прочитает; они СЧИТАЮТСЯ отдельно, а не молчат.

    ⚠ `frame_bars` (там же, сборка по запросу): окно, чей конец старше `frame_bars`
    баров своего ТФ, принадлежит структуре, чей уровень скрыл бы фильтр «у структуры», —
    его профиль не прочитает никто. Тоже считается, а не молчит.

    ⚠ При заданном `frame_bars` отсечка ГОРИЗОНТОМ НЕ применяется (2026-08-18, разбор
    ACE). Горизонт меряется СУТКАМИ и один на все ТФ, рамка — БАРАМИ СВОЕГО ТФ; для 1Н
    180 суток — это 25 баров, а рамка ответа — 180. Двойная отсечка означала: уровни
    строятся по рамке, а свечи под них качаются по горизонту, и недельные структуры
    возрастом 26–180 недель честно отказывали «не покрыто свечами» — систематически и
    только на старших ТФ. Потребитель у окон один (`levels` с той же рамкой), и резать
    закачку вторым, более узким критерием значило кормить его отказами. Боевой прогон
    (`frame_bars is None`) по-прежнему режется горизонтом: его охват — решение владельца
    о цене, и менять его отсюда нельзя.
    """
    out: list[tuple[int, int]] = []
    used = dropped = senior = out_of_frame = 0
    now = max((b[-1].open_ms for b in series.values() if b), default=0)
    cut = now - horizon_days * 86_400_000 if frame_bars is None else 0
    for tf, bars in series.items():
        if not bars:
            continue
        sw = detect_swings(bars)
        if isinstance(sw, NotReady):
            continue
        frame_lo = (bars[-1].open_ms - frame_bars * TIMEFRAME_MS[tf]
                    if frame_bars is not None else None)
        for acc in detect_ranges(bars, sw, tf).closed:
            lo, hi = levels.structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
            if hi < cut:
                dropped += 1
                continue
            if frame_lo is not None and hi < frame_lo:
                out_of_frame += 1
                continue
            if intrabar_tf is not None and intrabar_timeframe(hi - lo) != intrabar_tf:
                senior += 1
                continue
            used += 1
            out.append((lo, hi))
    return out, used, dropped, senior, out_of_frame


def merge_windows(spans: list[tuple[int, int]], gap_ms: int) -> list[tuple[int, int]]:
    """Слить пересекающиеся и близкие окна.

    ⚠ Порог слияния — НЕ вкус: это длительность одной страницы запроса. Пока разрыв между
    окнами короче страницы, отдельный запрос за вторым окном стоит столько же, сколько
    добор промежутка внутри первого, — значит сливать дешевле. Разрыв шире страницы
    сливать уже невыгодно: платили бы за бары, которых никто не спросит.
    """
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(spans):
        if out and lo - out[-1][1] <= gap_ms:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


async def backfill_profile_bars(ex: Exchange, uni: Universe, report: RunReport,
                                horizon_days: int,
                                frame_bars: int | None = None) -> None:
    """Долить СВЕЧИ ПРОФИЛЯ под окна исторических структур.

    ⚠ ЗАМЕНА `backfill_trades` С 2026-08-12 (решение владельца: «меняй транспорт на
    свечной»). Прежде под те же окна качались отдельные сделки `aggTrade`; основание
    этого выбора — одна фраза в скобках из первого коммита проекта, без референта.
    Разбор и замер: `docs/audit/poc-candles-vs-ticks-2026-08-12.md`, обоснование
    источника — докстрока модуля `profile_source`.

    Цена перехода замерена: сутки BTC через `aggTrades` — 3500 единиц веса, через
    минутные свечи — 6.

    ⚠ Отказ здесь НЕ роняет прогон и НЕ молчит: непокрытое окно позже отдаст
    `CandleWindows.window` как `NotReady` с числом недостающих свечей (§4.3), а сводка
    добора печатается в приёмке.

    ⚠ `horizon_days` — ПАРАМЕТР, а не ноль. Первая редакция передавала ноль в
    `profile_windows`, и это отбрасывало ВСЕ структуры: порог отсечки считается как
    «сейчас минус горизонт», значит при нуле он равен «сейчас», а окно закрытой
    структуры кончается раньше. Дымовой прогон напечатал «окон 0, отброшено 45» —
    прибор молчал бы, если бы отброшенные не считались отдельно.

    ⚠ С 2026-08-18 (вечер) качаются окна ВСЕХ ступеней лестницы intrabar-ТФ, а не
    только минутные. Прежняя редакция качала только `uni.profile_timeframe` и полагала,
    что окна старших ступеней «читают ряды, уже собранные засевом», — но засев собирает
    фиксированную глубину (`seed_depth`, ~180 суток на ТФ), а окна дневных и недельных
    структур уходят глубже. Найдено разбором ACE: недельная структура дек-2025..мар-2026
    (цены ВЫШЕ текущей — возможная шорт-зона) не получила уровня с отказом «окно покрыто
    свечами 1h не полностью — 417 из 2352», и отказы ложились систематически на старшие
    ТФ — ровно тот перекос по измерению, который правило сводки отказов обязано ловить
    (у монет вселенной он маскируется накопленным хранилищем, у монет по запросу — нет).
    Теперь недостающая часть окна ЛЮБОЙ ступени добирается у биржи в то же хранилище,
    из которого `trade_source` подмешивает ряды профиля.
    """
    for sym in uni.symbols:
        inst = ex.instrument(sym)
        if isinstance(inst, NotReady):
            report.profile_symbols_skipped += 1
            log.degraded("профиль: инструмент недоступен", символ=sym,
                         причина=inst.reason)
            continue
        series = bars_of(report, sym)
        wins, used, dropped, _senior, out_of_frame = profile_windows(
            series, horizon_days, frame_bars=frame_bars)
        report.profile_windows_out_of_frame += out_of_frame
        if not wins:
            # ⚠ ЗДЕСЬ СТОЯЛ МОЛЧАЛИВЫЙ `continue`. Символ без окон профиля не получает
            # свечей, а без свечей — ни одного уровня; при этом в отчёте не оставалось
            # ни счётчика, ни строки. Полный проход вселенной 2026-08-17: карту получили
            # 9 символов из 27, сводка напечатала «новых уровней 14711», и назвать
            # пропущенных было нечем. Причина у пустых окон ровно две, и они РАЗНЫЕ —
            # рядов нет вовсе против «структуры есть, но все вне кадра/горизонта», —
            # поэтому печатаются обе.
            if out_of_frame:
                log.info("профиль: все окна символа вне кадра ответа",
                         символ=sym, окон_вне_кадра=out_of_frame)
                continue
            report.profile_symbols_no_windows += 1
            log.degraded("профиль: окон структур нет — уровней у символа не будет",
                         символ=sym, рядов=len(series),
                         баров=sum(len(b) for b in series.values()),
                         отброшено_горизонтом=dropped, горизонт_суток=horizon_days)
            continue
        report.profile_windows += used
        report.profile_windows_dropped += dropped
        by_tf: dict[str, list[tuple[int, int]]] = {}
        for lo, hi in wins:
            by_tf.setdefault(intrabar_timeframe(hi - lo), []).append((lo, hi))
        for tf in PROFILE_LADDER:
            tf_wins = by_tf.get(tf)
            if not tf_wins:
                continue
            step = tf_ms(tf)
            for lo, hi in merge_windows(tf_wins, step * OHLCV_PAGE):
                # ⚠ КРИТЕРИЙ «УЖЕ ЕСТЬ» — СЧЁТ БАРОВ В САМОМ ОКНЕ, а не хвостовой
                # курсор (2026-08-18, разбор ACE). Прежний `missing_tail_since`
                # отвечает про хвост от последнего сохранённого бара и СЛЕП К ДЫРЕ
                # ВНУТРИ окна: хранилище минуток ACE начиналось раньше окна и
                # кончалось позже него, а три окна свежих 5м-структур лежали в его
                # внутренней дыре — участок объявлялся кэшированным, профиль отказывал
                # «покрыто не полностью», и чинить это было некому. Критерий теперь
                # тот же, что у читателя (`CandleWindows.window`: полных баров меньше
                # слотов окна — отказ): неполный участок перекачивается ЦЕЛИКОМ,
                # слияние в `append` уложит его вокруг имеющегося. Пустое хранилище
                # отдельно не различается: ноль баров — тоже «меньше, чем нужно».
                want = max(1, (hi - lo) // step)
                have = len(barstore.load(uni.venue, inst.market_id, tf,
                                         since_ms=lo, upto_ms=hi - 1))
                if have >= want:
                    report.profile_spans_cached += 1
                    continue
                got = await ex.fetch_closed_ohlcv(sym, tf, limit=int(want + 2),
                                                  since_ms=lo)
                if isinstance(got, NotReady):
                    report.profile_spans_failed += 1
                    log.degraded("профиль: свечи окна не добраны", символ=sym, тф=tf,
                                 от=lo, до=hi, причина=got.reason)
                    continue
                added, rewritten = barstore.append(uni.venue, inst.market_id, tf,
                                                   got.bars)
                report.profile_bars_stored += added
                report.profile_bars_rewritten += rewritten
                report.profile_spans_filled += 1
                report.profile_spans_by_tf[tf] = (
                    report.profile_spans_by_tf.get(tf, 0) + 1)
    # ⚠ ЗНАМЕНАТЕЛЬ В ЭТОЙ ЖЕ СТРОКЕ. Прежде она печатала только то, что УДАЛОСЬ, и
    # «окон 1970, добрано 1» читалось как успех при девяти обслуженных символах из
    # двадцати семи. Теперь видно, скольких символов сводка касается вообще.
    log.info("профиль: свечи под окна структур",
             символов=len(uni.symbols),
             символов_без_окон=report.profile_symbols_no_windows,
             символов_без_инструмента=report.profile_symbols_skipped,
             окон=report.profile_windows, отброшено_по_горизонту=report.profile_windows_dropped,
             окон_вне_кадра=report.profile_windows_out_of_frame,
             участков_добрано=report.profile_spans_filled,
             добрано_по_тф=dict(sorted(report.profile_spans_by_tf.items())),
             участков_из_хранилища=report.profile_spans_cached,
             участков_с_отказом=report.profile_spans_failed,
             баров_записано=report.profile_bars_stored)


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
    мелочь: он гоняет `detect_swings` и `detect_ranges` по каждому ТФ каждого
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
    gaps = await asyncio.to_thread(_backfill_impl, insts, uni, report, horizon_days,
                                   max_days_per_symbol)
    # ⚠ РЕШЕНИЕ ВЛАДЕЛЬЦА 2026-08-11: суточный архив удалён из проекта целиком, источник
    # сделок один — ccxt. Здесь добирается всё, чего нет в кэше. Шаг идёт на цикле
    # событий, а не в потоке: он ходит в сеть через `ex`.
    for sym, (days, cover_to) in gaps.items():
        gap_inst = insts.get(sym)
        if gap_inst is None:
            log.degraded("REST-добор пропущен: нет инструмента", символ=sym,
                         суток=len(days))
            continue
        for day in sorted(days):
            day_end = int(datetime(day.year, day.month, day.day,
                                   tzinfo=UTC).timestamp() * 1000) + 86_400_000
            age_days = (cover_to - day_end) / 86_400_000
            if age_days > REST_BACKFILL_MAX_AGE_DAYS:
                # Не молчание: сутки считаются и называются. Глубже замеренной глубины
                # `startTime` пустой ответ биржи неотличим от «сделок не было».
                log.degraded("сутки старше замеренной глубины REST — не добираются",
                             символ=sym, дата=str(day),
                             возраст_суток=round(age_days, 1),
                             предел=REST_BACKFILL_MAX_AGE_DAYS)
                report.backfill_days_missing += 1
                report.backfill_missing_by_symbol[sym] = (
                    report.backfill_missing_by_symbol.get(sym, 0) + 1)
                continue
            await _rest_fill_day(ex, sym, gap_inst, day, report)


REST_BACKFILL_MAX_AGE_DAYS = 365
"""Глубже скольких суток REST-добор НЕ применяется. Число — граница ЗАМЕРА, а не воля.

⚠ ДО 2026-08-11 ЗДЕСЬ СТОЯЛО 30, И ЭТО БЫЛА ОШИБКА НЕДОМЕРА, А НЕ ОСТОРОЖНОСТЬ.
Тридцать взялось из зонда probe_rest_trade_depth_2026-08-11.py, который проверял
отступы 1 ч … 30 сут и на тридцати остановился. Граница выборки была записана как
граница биржи — та же подмена, о которой предупреждает CLAUDE.md.

Двоичный поиск настоящей границы (BTC/USDT:USDT, биржевое время 2026-08-11 05:49Z,
`fetch_trades(since=…)`, 7 запросов):

    отступ    ответ
    365 сут   есть
    370 сут   есть
    373 сут   есть      ← последний рабочий
    376 сут   ПУСТО     ← первый пустой
    387 сут   ПУСТО
    410 сут   ПУСТО
    547 сут   ПУСТО
    730 сут   ПУСТО

То есть глубина `startTime` — около ГОДА и, судя по краю, скользящая. Взято 365: ровное
число внутри замеренного рабочего диапазона, с запасом 8 суток до первого пустого ответа.

Запас нужен именно потому, что граница скользит: за сутки работы службы окно уезжает, и
значение, поставленное вплотную к краю, назавтра оказалось бы за ним. Пустой же ответ
глубже границы неотличим от «сделок не было» и отравил бы кэш нулевыми сутками (см.
отказ на пустом кадре в `_rest_fill_day`) — потому предел и держится.

Сутки старше границы остаются названным отказом с причиной и попадают в сводку по
символу. Раньше в этот отказ проваливалось ВСЁ старше месяца."""

REST_CHECKPOINT_TRADES = 200_000
"""Сделок между контрольными точками REST-добора суток. ⚠ ЧИСЛО ПОДОБРАНО, не замерено:
~200 страниц по 1000 сделок ≈ пара минут закачки — столько прогресса теряется при
обрыве в худшем случае. Мельче — кэш дёргается записью на каждый чих; крупнее — обрыв
дорожает. Сам механизм обязателен: без точек обрыв терял ВСЮ выкачку суток, и при
коротких окнах исполнения добор крупного символа мог не завершиться никогда."""


async def _rest_fill_day(
    ex: Exchange, sym: str, inst: Instrument, day: date, report: RunReport,
) -> None:
    """Добрать одни сутки REST-ом ccxt: закрытые — полным файлом кэша, текущие — частичным.

    Решение владельца 2026-08-11 (и FOUNDATION §5 буквально: «Источник — Binance USD-M
    через ccxt/ccxt.pro»): архив публикации — ускоритель истории, а не условие карты.

    Частичный файл уже в кэше учитывается: качается только остаток за его границей,
    прочитанное раньше не выкачивается заново. Бинирование — `models.bin_index`, та же
    единственная функция, что у живого потока; согласие с архивной свёрткой держит
    gates/binning_agrees.py.
    """
    day0 = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    day_end = day0 + 86_400_000
    now = clock.now_ms()
    closed = day_end <= now
    # ⚠ ГРАНИЦА ДОБОРА — НЕ КОНЕЦ НОВЕЙШЕЙ СТРУКТУРЫ, А «ДОКУДА ВОЗМОЖНО». Правка
    # 2026-08-11, и она условие того, чтобы холодный старт действительно ушёл.
    #
    # Раньше здесь стояло `closed = day_end <= cover_to_ms; target = ... else cover_to_ms`,
    # то есть сутки добирались ровно до правого края САМОЙ СВЕЖЕЙ структуры символа.
    # Следствий было два, и оба вредные:
    #   * ПРОШЕДШИЕ сутки оставались частичными посреди дня. Замер 2026-08-11: файл
    #     BTCUSDT-2026-08-10 покрыт до 13:50 UTC, хотя сутки давно кончились;
    #   * СЕГОДНЯШНИЕ сутки обрывались раньше начала живого потока — значит между кэшем
    #     и потоком зияла дыра, `archive.extend_day_from_live` отказывался её заклеивать
    #     (и правильно делал), и принятое вебсокетом в кэш не ложилось НИКОГДА. Холодный
    #     старт при этом оставался бы на месте при снятом на словах.
    #
    # Теперь прошедшие сутки добираются до конца, а текущие — до «сейчас» по БИРЖЕВЫМ
    # часам. Ровно эта граница и смыкается с первой корзиной живого потока.
    target = day_end if closed else now
    if target <= day0:
        return

    qty: dict[tuple[int, int], float] = {}
    cnt: dict[tuple[int, int], int] = {}
    start = day0
    prior = archive.find_part(archive.CACHE_DIR, inst.market_id, day, inst.tick_size)
    if prior is not None:
        path, prior_cover = prior
        if prior_cover >= target:
            # Состояние названо, а не проглочено (находка аудита 2026-08-11): сутки уже
            # покрыты частичным файлом до нужной границы — качать нечего, это успех.
            log.info("сутки уже покрыты частичным файлом", символ=sym, дата=str(day),
                     покрыто_до=prior_cover, нужно_до=target)
            return
        for row in (await asyncio.to_thread(pl.read_parquet, path)).iter_rows(named=True):
            key = (int(row["bucket"]), int(row["bin"]))
            qty[key] = qty.get(key, 0.0) + float(row["qty"])
            cnt[key] = cnt.get(key, 0) + int(row["n"])
        start = prior_cover

    since_ckpt = 0
    max_ts = 0

    def on_page(trades: list[RawTrade]) -> None:
        nonlocal since_ckpt, max_ts
        for t in trades:
            bucket = t.timestamp - t.timestamp % archive.CACHE_BUCKET_MS
            key = (bucket, bin_index(t.price, inst.tick_size))
            qty[key] = qty.get(key, 0.0) + t.amount
            cnt[key] = cnt.get(key, 0) + 1
            max_ts = max(max_ts, t.timestamp)
        since_ckpt += len(trades)
        # КОНТРОЛЬНАЯ ТОЧКА. Сутки крупного символа качаются десятки минут; без точек
        # обрыв процесса (таймаут, Ctrl+C) терял всю выкачку, и повтор начинал с нуля —
        # то есть при коротких окнах исполнения добор МОГ НЕ ЗАВЕРШИТЬСЯ НИКОГДА.
        # Частичный файл с границей по последней ПОЛНОЙ корзине делает любой рестарт
        # продолжением. Порог подобран: ~200 страниц ≈ пара минут между точками.
        if since_ckpt >= REST_CHECKPOINT_TRADES:
            since_ckpt = 0
            ckpt = max_ts - max_ts % archive.CACHE_BUCKET_MS
            if ckpt > start:
                frame = archive.frame_from_pairs(qty, cnt).filter(
                    pl.col("bucket") < ckpt)
                archive.write_part_day(inst.market_id, day, inst.tick_size, frame, ckpt)

    got = await ex.fetch_agg_trades_window(sym, start, target, on_page)
    if isinstance(got, NotReady):
        log.degraded("сутки не добраны REST-ом", символ=sym, дата=str(day),
                     причина=got.reason)
        report.backfill_days_missing += 1
        report.backfill_missing_by_symbol[sym] = (
            report.backfill_missing_by_symbol.get(sym, 0) + 1)
        return
    fetched, covered = got
    if covered <= start:
        # Прогресса нет: писать нечего (при частичном файле это сохранило бы то же
        # покрытие вторым файлом), сутки остаются недобранными и посчитаны.
        log.degraded("сутки не добраны REST-ом", символ=sym, дата=str(day),
                     причина=f"покрытие не продвинулось ({covered} ≤ {start})")
        report.backfill_days_missing += 1
        report.backfill_missing_by_symbol[sym] = (
            report.backfill_missing_by_symbol.get(sym, 0) + 1)
        return
    frame = archive.frame_from_pairs(qty, cnt)
    if frame.height == 0:
        # ⚠ Пустые сутки НЕ записываются вовсе — ни полными, ни частичными. Находка
        # аудита 2026-08-11: пустой ответ может значить не «сделок не было», а «глубже
        # хранения биржи» — записанный по нему полный файл отравил бы кэш НАВСЕГДА
        # (binned_day возвращает существующий файл, не перекачивая). У бессрочного
        # контракта сутки без единой сделки — экзотика; честнее оставить их названным
        # отказом, чем рискнуть молчаливым нулём профиля.
        log.degraded("REST-добор дал пустые сутки — не записаны", символ=sym,
                     дата=str(day), покрыто_до=covered)
        report.backfill_days_missing += 1
        report.backfill_missing_by_symbol[sym] = (
            report.backfill_missing_by_symbol.get(sym, 0) + 1)
        return
    if closed and covered >= day_end:
        await asyncio.to_thread(archive.write_full_day_from_frame,
                                inst.market_id, day, inst.tick_size, frame)
        report.backfill_rest_days += 1
    else:
        # Корзина, в которую попала ПОСЛЕДНЯЯ сделка, не заявляется покрытой: она могла
        # оборваться на середине. В файл идут только корзины левее границы покрытия.
        #
        # ⚠ ГРАНИЦА ВЫРАВНИВАЕТСЯ ВНИЗ ПО КОРЗИНЕ (2026-08-11), и это условие того, чтобы
        # живой поток вообще мог продлить покрытие. REST кончает на произвольной
        # миллисекунде — 08:18:02 в замере, — а корзины потока стоят на сетке 5 минут.
        # Невыровненная граница делала стык невозможным по построению:
        #   * корзина 08:15 у потока начинается ЛЕВЕЕ границы, значит по правилу
        #     смежности отбрасывается;
        #   * следующая, 08:20, начинается ПРАВЕЕ — `extend_day_from_live` видит дыру и
        #     отказывается писать. Законно и навсегда.
        # Слить их «внахлёст» было бы хуже отказа: отрезок 08:15–08:18 попал бы в профиль
        # ДВАЖДЫ — один раз из REST, другой из потока, — и объём в бине вырос бы вдвое.
        #
        # После выравнивания граница стоит на 08:15, неполная корзина в файл не идёт
        # вовсе, а поток продолжает ровно с неё. Плата — покрытие отстаёт не больше чем
        # на одну корзину, и это честнее, чем заявлять покрытой половину корзины.
        cover = min(covered, target)
        cover -= cover % archive.CACHE_BUCKET_MS
        if cover <= start:
            log.degraded("сутки не добраны REST-ом", символ=sym, дата=str(day),
                         причина=f"нет ни одной полной корзины за границей ({cover} ≤ {start})")
            report.backfill_days_missing += 1
            report.backfill_missing_by_symbol[sym] = (
                report.backfill_missing_by_symbol.get(sym, 0) + 1)
            return
        frame = frame.filter(pl.col("bucket") < cover)
        await asyncio.to_thread(archive.write_part_day, inst.market_id, day,
                                inst.tick_size, frame, cover)
        report.backfill_rest_partial += 1
        if closed:
            # Закрытые сутки, добранные не до конца, — деградация, и она названа:
            # частичный файл честен (граница в имени), но сутки остаются непокрытыми.
            log.degraded("закрытые сутки добраны частично", символ=sym, дата=str(day),
                         покрыто_до=covered, конец_суток=day_end)
            report.backfill_days_missing += 1
            report.backfill_missing_by_symbol[sym] = (
                report.backfill_missing_by_symbol.get(sym, 0) + 1)
    report.backfill_rest_trades += fetched
    log.info("сутки добраны REST-ом", символ=sym, дата=str(day), сделок=fetched,
             целиком=closed and covered >= day_end)


def _backfill_impl(
    insts: dict[str, Instrument], uni: Universe, report: RunReport, horizon_days: int,
    max_days_per_symbol: int,
) -> dict[str, tuple[list[date], int]]:
    """Тело бэкфилла. Синхронно и целиком в рабочем потоке — см. `backfill_trades`.

    ⚠ Сети эта функция БОЛЬШЕ НЕ КАСАЕТСЯ. До 2026-08-11 она качала суточные ZIP архива
    и потому жила в потоке; теперь она только считает, каких суток недостаёт, а добор
    идёт REST-ом на цикле событий. Поток остаётся, потому что `needed_days` гоняет
    детекторы свингов и накоплений по каждому ТФ каждого символа — это настоящий расчёт,
    и на цикле событий он вытеснял бы сбор.

    ⚠ Без этого шага уровней не бывает вовсе, и это не свойство рынка. Профиль по стр. 26
    натягивается на бары структуры, а структуры лежат в истории; живой поток покрывает
    только длительность прогона. Замер 2026-08-04 (3 символа, 120 с): найдено 200 структур
    и построено 0 уровней — 100% отказов «окно выходит за собранное».

    `max_days_per_symbol` — предохранитель от многолетних структур 1Н, и он ГРОМКИЙ:
    отброшенное печатается числом, а не проглатывается. Непокрытые окна остаются
    `NotReady` с названной причиной (§4.3), а не заполняются приблизительным профилем.

    Возвращает по символу СУТКИ, которых архив не дал, и правый край нужного покрытия —
    их добирает REST-пас в `backfill_trades` (решение владельца 2026-08-11). Отказ архива
    здесь больше не финален, поэтому и не считается в `backfill_days_missing`: финальный
    счёт ведёт REST-пас.
    """
    gaps: dict[str, tuple[list[date], int]] = {}
    for sym in uni.symbols:
        inst = insts.get(sym)
        if inst is None:
            continue
        series = {tf: st.bars for (s, tf), st in report.series.items()
                  if s == sym and st.not_ready is None and st.bars}
        if not series:
            log.degraded("бэкфилл пропущен: нет баров", символ=sym)
            continue
        want, used, dropped, max_hi = needed_days(series, horizon_days)
        if len(want) > max_days_per_symbol:
            keep = sorted(want)[-max_days_per_symbol:]
            log.warn("предохранитель бэкфилла", символ=sym, нужно_суток=len(want),
                     берём=len(keep), отброшено=len(want) - len(keep))
            report.backfill_days_capped += len(want) - len(keep)
            want = set(keep)
        report.backfill_structures += used
        report.backfill_structures_old += dropped

        # ⚠ ТЕКУЩИЕ СУТКИ ДОБАВЛЯЮТСЯ ВСЕГДА, независимо от структур. Это второе
        # условие снятия холодного старта (2026-08-11).
        #
        # `needed_days` набирает сутки ИЗ ОКОН СТРУКТУР, и сегодняшних среди них может не
        # оказаться вовсе: у символа может не быть ни одной структуры, закрывшейся
        # сегодня. Тогда сегодняшние сутки не покрыты ничем, живому потоку не с чем
        # смыкаться, и принятое вебсокетом опять умирает вместе с процессом.
        #
        # Цена названа: при первом запуске это добор текущих суток с 00:00 UTC, до
        # ~6 минут на плотном символе. При каждом следующем — только дельта с прошлого
        # покрытия, то есть минуты простоя службы. Ровно этот размен и означает
        # «холодного старта нет»: платим один раз, а не при каждом запуске.
        today = datetime.fromtimestamp(clock.now_ms() / 1000, UTC).date()
        want.add(today)

        cached = archive.cached_days(inst.market_id, inst.tick_size)
        # ⚠ Осиротевшие сутки НАЗЫВАЮТСЯ, а не проглатываются. Binance меняет
        # `PRICE_FILTER` штатно, и в этот момент весь накопленный кэш символа перестаёт
        # находиться: номера бинов в нём посчитаны по прежнему шагу. Без этой строки
        # сбор объявил бы «в кэше ноль» и качал бы всё заново, а гигабайты лежали бы
        # рядом молча — правдоподобное объяснение вместо названной причины.
        n_orphan, why = archive.orphaned_days(inst.market_id, inst.tick_size)
        if n_orphan:
            report.cache_orphaned += n_orphan
            log.warn("КЭШ ОСИРОТЕЛ — сутки на диске есть, но не подходят ключу",
                     символ=sym, суток=n_orphan, причина=why)
        # ⚠ Текущие сутки НЕ считаются покрытыми по факту наличия файла: он частичный и
        # отстаёт от «сейчас» на всё время простоя. Их добор решается внутри
        # `_rest_fill_day` сравнением границы файла с целью.
        left = sorted((want - cached) | {today})
        log.info("набор суток из структур", символ=sym, структур=used,
                 суток=len(want), уже_в_кэше=len(want & cached),
                 добирать=len(left), старых_структур=dropped)
        # ⚠ Сутки только УКЛАДЫВАЮТСЯ В КЭШ, в память ничего не сливается. Прежняя
        # редакция копила весь горизонт в одном `BarBinnedTrades` и кончалась
        # `MemoryError` на 102 сутках: двадцать миллионов пар (корзина, бин) при том,
        # что каждой структуре нужно только её окно. Профиль строит `WindowSource`
        # по требованию.
        #
        # ⚠ 2026-08-11 отсюда УДАЛЁН путь суточного архива. Раньше сутки сначала
        # искались у `data.binance.vision`, и REST добирал лишь то, чего архив ещё не
        # опубликовал. Источник теперь один — ccxt, и потому здесь не осталось выбора
        # источника: всё, чего нет в кэше, добирается REST-ом. Основание и замер
        # глубины REST (около года) — в докстроке модуля `archive`.
        if left:
            gaps[sym] = (left, max_hi)
    return gaps


def trade_source(ex: Exchange, sym: str, report: RunReport,
                 uni: Universe) -> TradeWindows | None:
    """Источник профиля для символа — СВЕЧИ ПРОФИЛЯ из хранилища баров.

    ⚠ ПЕРЕВЕДЁН СО СДЕЛОК НА СВЕЧИ 2026-08-12 по решению владельца. Прежде здесь
    строился `archive.WindowSource` — суточный кэш `aggTrade` плюс живой поток сделок.
    Основанием того выбора была одна фраза в скобках из первого коммита проекта, и оно
    не выдержало проверки: ни один сигнальный бот жанра отдельных сделок не берёт, а
    инструмент автора курса строит профиль по свечам младшего разрешения. Разбор,
    замер и цена — в докстроке модуля `profile_source` и в
    `docs/audit/poc-candles-vs-ticks-2026-08-12.md`.

    ⚠ Бары читаются НА ВЫЗОВ и не удерживаются: минутный ряд за девяносто суток — это
    около 130 тысяч баров на символ, и держать их для всех двадцати семи разом значило бы
    вернуть ту самую `MemoryError`, ради которой источник когда-то и стал ленивым.

    Один на оба пути — печать карточки и запись в леджер.

    ⚠ С 2026-08-18 источник — `TVWindows` (п. 3 приказа владельца): intrabar-ТФ окна
    выбирает лестница по образцу TV (первый из 1м…1Д с < 5000 баров; число и лестница
    ПОДОБРАНЫ нами, TV публикует только принцип — см. `TV_INTRABAR_MAX_BARS`), а не
    всегда `1m`.

    ⚠ С 2026-08-18 (вечер) хранилище подмешивается по ВСЕМ ступеням лестницы, а не
    только по минуткам. Прежде аналитические ТФ читались только из рядов прогона
    (глубина `seed_depth` ~180 суток), и окна дневных/недельных структур, уходящие
    глубже, получали отказ покрытия, хотя `backfill_profile_bars` уже умеет докачивать
    их участки в хранилище (см. его докстроку — разбор ACE). Слияние: хранилище +
    ряд прогона, свежий бар прогона побеждает по метке (`_merge_bars`).
    """
    inst = ex.instrument(sym)
    if isinstance(inst, NotReady):
        return None
    series: dict[str, list[Bar]] = dict(bars_of(report, sym))
    for tf in PROFILE_LADDER:
        stored = barstore.load(uni.venue, inst.market_id, tf)
        if not stored:
            continue
        have = series.get(tf)
        series[tf] = _merge_bars(stored, have) if have else stored
    if not series:
        return None
    return TVWindows(sym, inst.tick_size, series)


def bars_of(report: RunReport, sym: str) -> dict[str, list[Bar]]:
    """Готовые ряды символа. Один фильтр на все три шага — иначе они разойдутся."""
    return {tf: st.bars for (s, tf), st in report.series.items()
            if s == sym and st.not_ready is None and st.bars}


def persist_frames(run_id: str, report: RunReport) -> None:
    """ШАГ 2 из трёх: сохранить СЫРЫЕ КАДРЫ. Карточку здесь не строим.

    ⚠ Раньше эта функция ещё и производила карточку. То есть сбор данных и производство
    сигнала жили в одном вызове — та самая слипшаяся точка, из которой в прошлом проекте
    вырос orchestrator.py на 2894 строки. Разделено 2026-08-04 по внешнему разбору,
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
                sources: dict[str, TradeWindows],
                frame_bars: int | None = None) -> dict[str, engine.SymbolDecision]:
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
        out[sym] = engine.decide(sym, series, sources.get(sym), tfs,
                                 frame_bars=frame_bars)
    # СВОДКА отказов «нет ряда нужного ТФ» по измерению возможного перекоса (правило
    # backfill-window-2026-08-04: сто честных отказов на одном ТФ читаются как «рынок
    # такой», пока их не сложили). До 2026-08-18 счётчик `windows_refused` у `TVWindows`
    # не читал никто — находка аудита.
    refused = {sym: r for sym, src in sources.items()
               if (r := getattr(src, "refused_by_tf", None))}
    if refused:
        by_tf: dict[str, int] = {}
        for per_tf in refused.values():
            for tf, n in per_tf.items():
                by_tf[tf] = by_tf.get(tf, 0) + n
        log.degraded("профиль: окнам не хватило ряда intrabar-ТФ",
                     всего=sum(by_tf.values()), по_тф=by_tf,
                     символов=len(refused))
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


def persist_source(run_id: str, report: RunReport,
                   sources: dict[str, TradeWindows]) -> None:
    """ШАГ 3б: положить в кадры ТО, из чего строился профиль карточки.

    До перевода профиля на свечи (решение владельца 2026-08-12, код — 2026-08-17;
    переименована из `persist_archive` 2026-08-18) — «те самые сутки архива сделок».
    Переводом этот шаг был потерян МОЛЧА: функция фильтровала по
    `isinstance(WindowSource)` и на свечных источниках не писала ничего, а повтор
    продолжал читать срез сделок, которого больше не было. Поймано контролем
    2026-08-18: `replay` с НЕИЗМЕНЁННЫМ кодом печатал «ИЗМЕНИЛОСЬ 5 из 5» — дифф
    §10.6, единственная проверка без чтения кода, был слеп ко всем правкам расчёта
    с 2026-08-17.

    Для `TVWindows` кладутся ряды, которых нет среди аналитических кадров (шаг 2), —
    на боевом конфиге это минутки из хранилища. Набор берётся у САМОГО источника
    (`series_by_tf`), а не пересчитывается «какие должны были быть»: второй расчёт
    разошёлся бы с первым ровно там, где это важно. Рядом пишется МАНИФЕСТ
    (source.json): повтор кадров без манифеста отказывается кодом возврата, так
    что повторение потери этого шага больше не может остаться молчаливым.

    Отказ «минуток у символа нет» НАЗЫВАЕТСЯ по символу (§4.3 и правило сводки
    отказов): сто честных пропусков подряд читались бы как «так и надо».

    Без этого шага повтор читает ОБЩЕЕ хранилище, которое между прогонами меняется.
    Прогон-пробник ещё архивной эпохи: кадры не тронуты, из кэша убраны одни сутки —
    карточка ETH изменилась на 483 строки (Н-6 разбора).
    """
    for sym, src in sources.items():
        if isinstance(src, TVWindows):
            saved = set(bars_of(report, sym))
            extra = {tf: bars for tf, bars in src.series_by_tf().items()
                     if tf not in saved and bars}
            for tf, bars in extra.items():
                store.write_profile_bars(run_id, sym, tf, bars)
                report.profile_series_written += 1
            # Состав аналитических рядов пишется в манифест: повтор сверяет его с
            # файлами на диске, иначе пропавший parquet менял бы набор ТФ молча и
            # дифф читался бы как «расчёт изменился» (2026-08-18).
            store.write_source_meta(run_id, sym, list(extra),
                                    analysis_tfs=sorted(saved))
            if not extra:
                log.degraded("профильный ряд в кадры не положен — минуток в "
                             "хранилище нет, повтор воспроизведёт те же отказы",
                             символ=sym)
        elif isinstance(src, archive.WindowSource):
            # Кладутся ПУТИ, прочитанные источником, а не даты: сутки бывают полными и
            # частичными файлами, и повтору нужен именно читанный файл (граница покрытия
            # частичных стоит в имени — иначе пересборка разошлась бы на этой границе).
            for path in sorted(src.used_paths):
                store.write_archive_slice(run_id, sym, path)
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
                # ⚠ ЗДЕСЬ БЫЛ `continue`, и это была молчаливая потеря ответа. Найдено
                # 2026-08-11 на живом BEAT: сигнал #140 (лонг 4ч, вход 1.2186) числился
                # «цена не дошла до входа», тогда как тот же `outcome.resolve` по тем же
                # барам отвечал `open` — вход состоялся баром 4ч 00:00-04:00 (диапазон
                # 1.217…1.813). Контроль: по шорту #141 с входом 7.1329 прибор отвечал
                # `not_filled`, то есть различать он способен.
                #
                # Отсутствие цели закрывает ровно ОДИН исход из четырёх: `target`.
                # `resolve` принимает `target=None` и корректно считает `stop`, `open` и
                # `not_filled` — они зависят только от входа и стопа. Пропуская такой
                # сигнал целиком, леджер не записывал даже СТОП, то есть сигнал без цели
                # не получал ответа НИКОГДА. Это ровно тот дефект, ради которого заведён
                # `_resolve_pending` (76 сигналов из 112 без ответа), только по другому
                # измерению — не «уровень ушёл из карты», а «у сделки нет цели».
                no_target += 1
            side = (levels.LevelSide.LONG if p.direction == "long"
                    else levels.LevelSide.SHORT)
            # Безубыток (стр. 19: «Цена показала реакцию и ушла внутрь базы – ставите
            # стоп в б/у»). Порог взведения — ПЕРВАЯ цель: именно на ней курс велит
            # крыть часть и двигать стоп (стр. 15). Цели нет — безубытка тоже нет, и
            # это отказ с причиной, а не подставленный вход.
            res = outcome_resolve(
                side=side, entry=p.entry, stop=p.stop, target=p.target, bars=bars,
                from_index=emit.first_bar_after(bars, p.timeframe, p.recorded_at, 0),
                breakeven_at=p.breakeven_at,
            )
            if res.kind.value in ("stop", "target", "ambiguous", "breakeven"):
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
    if no_bars:
        log.degraded("часть сигналов дорешать нельзя", без_баров=no_bars)
    if no_target:
        # НЕ деградация: такие сигналы дорешиваются, просто исход `цель` у них
        # невозможен по построению. Знать их число всё равно нужно — оно знаменатель
        # к «по цели 0%»: без него ноль целей читался бы как «цели не достигаются».
        log.info("сигналы без цели дорешаны по стопу и состоянию", сигналов=no_target)


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
            # Правило входа пишется вместе с состоянием (схема 6, 2026-08-11): без него
            # `active` читался как «свежий, цена не касалась», а курс снимает лимитки уже
            # на первое касание (стр. 25). Замер на BEAT: 8 активных из 21 — касавшиеся.
            # Четвёртым идёт МОМЕНТ СОБЫТИЯ (схема 7): `retired_at` хранит время прогона
            # и у всех снятых уровней одинаков, а разметке нужен бар, на котором прокол
            # или пробой действительно случился.
            # Пятым — ПРИЗНАК СЛОМА на младшем ТФ (схема 11). Он живёт на РЕШЕНИИ, а
            # не на уровне, поэтому связывается по ключу структуры: доставке он нужен,
            # чтобы сказать это читателю: стр. 19 «также можно смотреть слом структуры
            # на мтф, и брать более безопасную позицию с хорошим соотношением РР». Бары
            # наблюдателю недоступны.
            broke: dict[tuple[str, int, int], int] = {}
            for one in d.decisions:
                note = one.mtf_break or ""
                if not note:
                    continue
                k = (one.level.timeframe, one.level.structure_from_ms,
                     one.level.structure_to_ms)
                broke[k] = 1 if "подтверждён" in note else 0
            # Шестым — СОГЛАСИЕ СО СТАРШИМ ТФ (схема 12): доставке нужно сказать, что
            # сделка встречная (стр. 47), а тренды наблюдателю недоступны.
            agree: dict[tuple[str, int, int], str] = {}
            for one in d.decisions:
                k = (one.level.timeframe, one.level.structure_from_ms,
                     one.level.structure_to_ms)
                agree[k] = one.agreement.value
            # Седьмым — ЦЕНА СТОПА, посчитанная геометрией (схема 13). До неё бот считал
            # стоп САМ по простой формуле «граница ± запас», а карточка брала его из
            # `geometry.build_setup`, где действует ещё и ЯКОРЬ (стр. 18). Замер
            # 2026-08-20 на 68 сделках: якорь решает в 36 случаях и уводит стоп ДАЛЬШЕ
            # пола на 1.52% медианно, до 2.06% — то есть два рта проекта называли по
            # одному уровню РАЗНЫЕ цены стопа. Величина считается один раз и печатается
            # обоими («прибор обязан смотреть на ТУ ЖЕ величину, которую видит владелец»).
            stops: dict[tuple[str, int, int], float] = {}
            for one in d.decisions:
                if one.setup is None:
                    continue
                k = (one.level.timeframe, one.level.structure_from_ms,
                     one.level.structure_to_ms)
                stops[k] = float(one.setup.stop)
            # Восьмым и девятым — ЧЕЙ приоритет и на скольких экстремумах он держится
            # (схема 14). Уведомление печатало «против старшего ТФ» и молчало о том,
            # чьего и насколько тот тренд обоснован, — карточка того же прогона это
            # писала. Числа: приоритет с недельного ТФ в 64% случаев, держится на ДВУХ
            # экстремумах у 28% (замер 2026-08-20).
            prio: dict[tuple[str, int, int], tuple[str | None, int | None]] = {}
            for one in d.decisions:
                k = (one.level.timeframe, one.level.structure_from_ms,
                     one.level.structure_to_ms)
                prio[k] = (one.priority.timeframe, one.priority.holds_for)
            seen = [(m.level, m.status.state, m.status.entry_rule,
                     m.status.resolved_at_ms,
                     broke.get((m.level.timeframe, m.level.structure_from_ms,
                                m.level.structure_to_ms)),
                     agree.get((m.level.timeframe, m.level.structure_from_ms,
                                m.level.structure_to_ms), ""),
                     stops.get((m.level.timeframe, m.level.structure_from_ms,
                                m.level.structure_to_ms)),
                     *prio.get((m.level.timeframe, m.level.structure_from_ms,
                                m.level.structure_to_ms), (None, None)))
                    for m in d.mapped]
            sync = store.sync_levels(conn, sym, seen, stamp_ms)
            carried = store.carried_levels(conn, sym, stamp_ms)
            # ⚠⚠ ПЕРЕНЕСЁННЫЕ ОЦЕНИВАЮТСЯ, А НЕ ПРОСТО ПЕРЕНОСЯТСЯ (2026-08-20).
            # `carried_levels` отдаёт уровни, которых этот прогон не считал: их структура
            # вышла из рамки построения (`frame_bars`). До этой правки с ними НЕ ДЕЛАЛОСЬ
            # НИЧЕГО, поэтому такой уровень не оценивался больше никогда и оставался
            # активным вечно. Замер на живом леджере в тот день: 2317 активных из 3476
            # (67%) имели структуру старше 30 суток, из них 1142 пятиминутных, и это
            # давало 4205 пар ВСТРЕЧНЫХ уровней с пересечением зон.
            #
            # Свежие бары у нас есть — значит правила стр. 43 (пробой → флип) и стр. 25
            # (заход в зону с уходом → отработка) применимы и к ним. Берутся бары ПОСЛЕ
            # конца структуры: то, что было до, уровень уже пережил.
            resolved: list[tuple[str, int, int, str]] = []
            for cl in carried:
                cbars = series.get(cl.timeframe)
                if not cbars:
                    continue
                after = [b for b in cbars if b.open_ms > cl.to_ms]
                verdict = levels.resolve_carried(
                    cl.side, float(cl.zone_lo), float(cl.zone_hi),
                    float(cl.boundary_lo), float(cl.boundary_hi), after)
                if verdict is not None:
                    resolved.append((cl.timeframe, cl.from_ms, cl.to_ms, verdict))
            if resolved:
                report.map_retired += store.retire_levels(conn, sym, resolved, stamp_ms)
                done = {(t, f, u) for t, f, u, _ in resolved}
                carried = tuple(c for c in carried
                                if (c.timeframe, c.from_ms, c.to_ms) not in done)
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
                    # ⚠ ЦЕНА ВЗВЕДЕНИЯ БЕЗУБЫТКА — НЕ ЦЕЛЬ. Берётся ПЕРВОЕ по ходу
                    # сделки правило стр. 19 («цена показала реакцию и ушла внутрь
                    # базы»): оно наступает раньше цели и потому вообще способно
                    # сработать. Подача сюда `target` (первая редакция 2026-08-19)
                    # делала исход «в безубытке» недостижимым: взведение и закрытие
                    # по цели приходятся на один бар, а цель проверяется первой.
                    breakeven_at=(em.setup.breakeven_rules[0].watch_price
                                  if em.setup.breakeven_rules else None),
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
                if res.kind.value in ("stop", "target", "ambiguous", "breakeven"):
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
                # Сводка меры уторговки — по ВСЕМ ПП-сигналам, до отсева бесцелевых:
                # перекос отказов идёт вдоль ТФ (окно длиннее ряда минуток), и сводка
                # обязана его показывать, а не прятать за отбором (правило сводки).
                ab_bucket = (report.absorption_measured_by_tf if pssig.absorption
                             is not None else report.absorption_refused_by_tf)
                ab_bucket[pssig.timeframe] = ab_bucket.get(pssig.timeframe, 0) + 1
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
                if pres.kind.value in ("stop", "target", "ambiguous", "breakeven"):
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
    "profile_series_written",
    "signals_recorded", "signals_known", "pp_signals_recorded", "pp_signals_known",
    "absorption_measured_by_tf", "absorption_refused_by_tf",
    "outcomes_recorded", "outcomes_resolved_late", "states_recorded",
    "pending_no_target", "pending_no_bars", "emitted_outcomes",
    "emitted_rr", "emitted_stop_pct",
    "map_added", "map_updated", "map_retired", "map_rejected", "map_carried",
    "backfill_days_loaded", "backfill_days_missing", "backfill_trades",
    "backfill_structures", "backfill_structures_old", "backfill_days_capped",
    "backfill_rest_days", "backfill_rest_partial", "backfill_rest_trades",
    "backfill_missing_by_symbol",
    "profile_windows", "profile_windows_dropped", "profile_windows_senior_tf",
    "profile_windows_out_of_frame", "profile_spans_filled",
    "profile_spans_cached", "profile_spans_failed", "profile_bars_stored",
    "profile_bars_rewritten", "profile_symbols_skipped",
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
                 keep_trade_days: int = LIVE_TRADES_KEEP_DAYS,
                 horizon_days: int = 0) -> None:
        self.uni = uni
        self.seed_limit = seed_limit
        self.horizon_days = horizon_days
        """Горизонт нужен ЗДЕСЬ, а не только доборщику сделок: с 2026-08-11 из него
        выводится глубина каждого ряда (`seed_depth`). Ноль означает «глубину задаёт
        `seed_limit`», то есть прежнее поведение."""
        self.keep_bars = keep_bars
        self.keep_trade_days = keep_trade_days
        self.ex = Exchange(uni.venue)
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
        # ⚠ Соответствие вселенной площадке проверяется ДО сбора и ОДИН раз. Иначе
        # неверная пара «площадка + список символов» проявлялась бы отдельным отказом на
        # каждый символ в засеве, то есть выглядела бы проблемой данных, а не
        # конфигурации. `BTC/USDT` и `BTC/USDT:USDT` — разные рынки.
        if bad := self.ex.check_symbols(self.uni.symbols):
            log.error("СИМВОЛЫ ВСЕЛЕННОЙ НЕ СООТВЕТСТВУЮТ ПЛОЩАДКЕ",
                      площадка=self.uni.venue, недоступно=len(bad),
                      всего=len(self.uni.symbols), примеры="; ".join(bad[:3]))
            if len(bad) == len(self.uni.symbols):
                raise CapabilityMissing(
                    f"площадка {self.uni.venue!r}: недоступны ВСЕ {len(bad)} символов "
                    f"вселенной — похоже, площадка и список символов из разных миров. "
                    f"Первый отказ: {bad[0]}")
        self._report = RunReport(sync=sync)
        self._started_ns = clock.monotonic_ns()
        log.info("засев", символов=len(self.uni.symbols), тф=len(self.uni.timeframes),
                 баров_на_ряд=self.seed_limit)
        await seed(self.ex, self.uni, self.report, self.seed_limit,
                   self.horizon_days)
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
            # Т-1: поток сделок поднимается только при `WATCH_TRADES`. Структуры выше
            # создаются в обоих случаях — иначе счётчики приёмки печатали бы не «ноль
            # сделок», а «нет такого символа», то есть отказ вместо замера.
            if WATCH_TRADES:
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

    def _flush_live_to_cache(self) -> None:
        """Уложить принятое потоком в суточный кэш. Тем и снимается холодный старт.

        Кладутся только ЗАВЕРШЁННЫЕ корзины: текущая ещё наполняется, и записать её
        значило бы объявить покрытым то, что покрыто наполовину. Граница берётся от
        БИРЖЕВЫХ часов (`clock.now_ms`), а не от локальных, — §6 запрещает судить о
        закрытости по своим.

        Продлевается только СМЕЖНОЕ покрытие: если кэш кончается раньше, чем начинается
        поток, между ними дыра, и `archive.extend_day_from_live` отказывается писать.
        Дыру закрывает REST-добор — он для того и остался. Отказ здесь не деградация, а
        нормальный ход: при первом запуске службы кэша просто нет.
        """
        if not WATCH_TRADES:
            # Т-1: потока нет — сливать нечего. Возврат ЗДЕСЬ, а не пустой цикл ниже:
            # без потока `self.binned` пуст, цикл и так ничего бы не сделал, но читателю
            # видно РЕШЕНИЕ, а не случайность.
            return
        now = clock.now_ms()
        edge = now - now % archive.CACHE_BUCKET_MS
        for sym, acc in self.binned.items():
            inst = self.ex.instrument(sym)
            if isinstance(inst, NotReady) or not acc.qty:
                continue
            days = {datetime.fromtimestamp(b / 1000, UTC).date()
                    for b in acc.qty if b < edge}
            for day in sorted(days):
                got = archive.extend_day_from_live(
                    inst.market_id, day, inst.tick_size, acc.qty, acc.cnt, edge)
                if got is not None:
                    self.report.live_days_flushed += 1

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

        # ⚠ СНЯТИЕ ХОЛОДНОГО СТАРТА (решение владельца 2026-08-11). Принятое потоком
        # ложится в тот же суточный кэш, что и добранное REST-ом. До этой правки поток
        # жил только в памяти прогона: всё, что служба приняла вебсокетом, умирало вместе
        # с процессом, и следующий запуск выкачивал те же сутки заново — ~6 минут на
        # символ-сутки за данные, которые уже были получены и выброшены.
        self._flush_live_to_cache()

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
                  horizon_days: int = 90,
                  frame_bars: int | None = None,
                  ) -> tuple[RunReport, dict[str, TradeWindows]]:
    """ШАГ 1 из четырёх: ТОЛЬКО добыть данные. Ни карточки, ни леджера.

    Возвращает отчёт и источники профиля. Сеть трогается только здесь — дальше три шага
    работают на кадрах и кэше, и потому проверяемы отдельно от биржи.

    ⚠ Это ПАКЕТНЫЙ путь: собрать `seconds` секунд и остановиться. Он остаётся ради
    `hunter run` и `hunter check` — проверки на фиксированном окне, которую владелец
    запускает руками, — но боевое исполнение теперь `hunter serve` (`service.serve`).
    Оба построены на `Collector`, и различаются ровно тем, сколько раз берётся снимок:
    здесь один, там — по одному на цикл, бесконечно.
    """
    c = Collector(uni, seed_limit, horizon_days=horizon_days)
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
        # `frame_bars` — кадр ответа сборки по запросу (2026-08-18): окна структур вне
        # кадра не качаются, их уровни не строятся — фильтр «у структуры» их скрыл бы.
        await backfill_profile_bars(c.ex, uni, report, horizon_days,
                                    frame_bars=frame_bars)
        # Источники строятся ДО закрытия соединения: им нужен `market_id` инструмента.
        # Сеть после этого не трогается — читается только хранилище баров на диске.
        sources = {sym: src for sym in uni.symbols
                   if (src := trade_source(c.ex, sym, report, uni)) is not None}
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
    # Заведено 2026-08-19 вместе с исходом BREAKEVEN (стр. 14: «цена вернулась к точке
    # открытия сделки»). ⚠ Отсутствие этой строки уронило первый же цикл службы после
    # правки — KeyError через 79 минут расчёта. Защита сработала как обещает докстрока
    # ниже, но цена ошибки — потерянный круг по всей вселенной.
    "breakeven": "в безубытке",
}
"""Подписи исходов для владельца, который не программист (§7.6).

Полнота обеспечена не глазами: ключи сверяются с `OutcomeKind` при печати, и отсутствие
подписи падает по `KeyError`, а не печатает пустую строку.
"""


# Самописная `_median` удалена 2026-08-17 (правило библиотеки): она дословно
# дублировала `statistics.median` (та же семантика чётного n — среднее двух средних,
# проверено на [1,2,3,4] → 2.5 обоими). Дубль без строки-обоснования — дефект.


def _spread(xs: list[float]) -> str:
    """Медиана и края. Одно среднее по такой выборке скрывает ровно то, что важно."""
    if not xs:
        return "нечего считать (0 значений)"
    return (f"значений {len(xs)}, медиана {statistics.median(xs):.3f}, "
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
    # ⚠ Печатается ВСЕГДА, в том числе нулём, и вот зачем. Ноль здесь означает, что
    # принятое вебсокетом в кэш не легло, — то есть следующий запуск выкачает те же
    # сутки REST-ом заново, хотя данные уже были у нас в руках. Невидимый счётчик
    # позволил бы холодному старту вернуться молча (§4.3).
    print(f"   суток продлено живым потоком: {r.live_days_flushed}"
          + ("   ⚠ ноль: принятое потоком в кэш НЕ ЛОЖИТСЯ"
             if r.live_days_flushed == 0 and r.trades_total > 0 else ""))

    # ⚠ Хранилище баров печатается ВСЕГДА и тремя числами сразу. Одно «поднято из
    # хранилища N» не отличает растущее хранилище от застывшего, а молчаливая перезапись
    # уже сохранённого бара изменила бы ряд без названной причины.
    print(f"   бары: из хранилища {r.bars_from_store}, дописано {r.bars_stored}, "
          f"переписано {r.bars_rewritten}"
          + ("   ⚠ ноль поднято: хранилище пусто или в него не пишется"
             if r.bars_from_store == 0 and r.seeded_bars > 0 else ""))
    if r.bars_rewritten:
        print(f"     ⚠ биржа отдала ИНЫЕ числа по {r.bars_rewritten} уже сохранённым "
              f"барам — закрытый бар неизменен, причина обязана быть найдена")
    if r.cache_orphaned:
        print(f"     ⚠ суток кэша сделок на диске есть, но НЕ ПОДХОДЯТ ключу: "
              f"{r.cache_orphaned} — у символа сменился шаг цены или схема бинирования, "
              f"и они будут скачаны заново")
    if r.seed_tail_failed:
        print(f"     ⚠ рядов отдано из хранилища БЕЗ свежего хвоста: {r.seed_tail_failed}")

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

    print("\n4б. ЗАПАДАНИЕ: СКОЛЬКО БАР ИДЁТ ДО РАСЧЁТА (Т-0)")
    lags = arrival_lag_survey(r)
    if not lags:
        print("   баров опросом не добрано — задержку мерить не на чем "
              "(прогон короче шага младшего ТФ)")
    else:
        floor_ms = int(POLL_OFFSET_S * 1000)
        print(f"   отсчёт от ЗАКРЫТИЯ бара до его принятия в ряд. Нижняя граница по "
              f"построению — наш отступ {floor_ms} мс: раньше него мы не спрашиваем")
        print("   ТФ     баров    p50        p95        максимум   сверх отступа (p50)")
        for c in lags:
            print(f"   {c.timeframe:6} {c.bars:6} {c.p50_ms:8} мс {c.p95_ms:8} мс "
                  f"{c.max_ms:8} мс {c.p50_ms - floor_ms:8} мс")
    if r.stage_ms:
        total = sum(r.stage_ms.values())
        print("   стадии конвейера: "
              + ", ".join(f"{k} {v} мс" for k, v in r.stage_ms.items())
              + f"; всего {total} мс")
    else:
        print("   стадии конвейера не замерены: этот путь их не размечает")

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
    if not WATCH_TRADES:
        print("   ПОТОК СДЕЛОК ВЫКЛЮЧЕН (Т-1, 2026-08-21): расчёт его не читает — "
              "профиль объёма и уторговка идут по СВЕЧАМ с 2026-08-12. Нули ниже — "
              "следствие решения, а не отказа биржи. Выключатель: run.WATCH_TRADES")
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

    print("\n5б. СВЕЧИ ПРОФИЛЯ ПОД ОКНА СТРУКТУР")
    print(f"   окон структур: {r.profile_windows}, "
          f"старше горизонта отброшено: {r.profile_windows_dropped}, "
          f"символов пропущено: {r.profile_symbols_skipped}")
    print(f"   окон вне кадра ответа: {r.profile_windows_out_of_frame}")
    print(f"   участков: добрано у биржи {r.profile_spans_filled}, "
          f"взято из хранилища {r.profile_spans_cached}, "
          f"с отказом {r.profile_spans_failed}")
    if r.profile_spans_by_tf:
        spans = ", ".join(f"{tf}: {n}"
                          for tf, n in sorted(r.profile_spans_by_tf.items()))
        print(f"   добрано по ТФ (перекос виден здесь): {spans}")
    print(f"   свечей записано: {r.profile_bars_stored}, "
          f"ПЕРЕЗАПИСАНО: {r.profile_bars_rewritten}")
    if r.profile_windows and not (r.profile_spans_filled + r.profile_spans_cached):
        print("   ⚠ окна есть, а участков профиля НЕТ — уровней не будет ни одного")

    print("\n5в. АРХИВ СДЕЛОК (источником профиля НЕ является с 2026-08-12)")
    print(f"   структур в горизонте: {r.backfill_structures}, "
          f"старше горизонта отброшено: {r.backfill_structures_old}")
    print(f"   суток загружено: {r.backfill_days_loaded}, не получено: "
          f"{r.backfill_days_missing}, отброшено предохранителем: {r.backfill_days_capped}")
    print(f"   добрано REST-ом ccxt (архив не отдал; решение владельца 2026-08-11): "
          f"суток целиком {r.backfill_rest_days}, частично {r.backfill_rest_partial}, "
          f"сделок {r.backfill_rest_trades:,}")
    if r.backfill_missing_by_symbol:
        by_sym = sorted(r.backfill_missing_by_symbol.items(), key=lambda x: -x[1])
        print("   недобранные сутки по символам (сводка по измерению): "
              + ", ".join(f"{s} {n}" for s, n in by_sym))
    print(f"   сделок влито: {r.backfill_trades}")

    print("\n7. ЛЕДЖЕР (§8 этап 7)")
    print(f"   сигналов записано ВПЕРВЫЕ: {r.signals_recorded}, "
          f"было известно раньше: {r.signals_known}")
    print(f"   сигналов от ПП (kind=pp): впервые {r.pp_signals_recorded}, "
          f"известно раньше: {r.pp_signals_known}")
    if r.absorption_measured_by_tf or r.absorption_refused_by_tf:
        # Сводка по измерению, вдоль которого возможен перекос: отказ меры уторговки
        # идёт по ТФ (окно от подтверждения слома длиннее собранного ряда минуток).
        tfs_seen = sorted(
            set(r.absorption_measured_by_tf) | set(r.absorption_refused_by_tf),
            key=lambda t: TIMEFRAME_MS.get(t, 0))
        parts = (f"{tf} {r.absorption_measured_by_tf.get(tf, 0)}/"
                 f"{r.absorption_measured_by_tf.get(tf, 0) + r.absorption_refused_by_tf.get(tf, 0)}"
                 for tf in tfs_seen)
        print("   уторговка у зон ПП, измерено/всего по ТФ: " + ", ".join(parts))
    print(f"   исходов записано: {r.outcomes_recorded}, из них ДОРЕШАНО у сигналов, "
          f"которые прогон заново не эмитировал: {r.outcomes_resolved_late}")
    print(f"   состояний незакрытых сделок записано: {r.states_recorded} "
          f"(мимо входа / сделка идёт — §4.3, схема v4)")
    if r.pending_no_target:
        print(f"   БЕЗ ЦЕЛИ (геометрия её не дала либо запись до схемы v5): "
              f"{r.pending_no_target} — дорешиваются по стопу и состоянию, исход «по "
              f"цели» у них невозможен по построению; это знаменатель к «по цели 0%»")
    if r.pending_no_bars:
        print(f"   дорешать НЕЛЬЗЯ: без ряда ТФ в этом прогоне {r.pending_no_bars}")
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
        med = statistics.median(r.emitted_stop_pct)
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
