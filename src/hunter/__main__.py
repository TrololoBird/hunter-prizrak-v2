"""Точка входа. FOUNDATION.md §8 этап 1."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from . import log, replay, service, store
from .config import DEFAULT_PATH, Universe, load_universe
from .models import NotReady

T = TypeVar("T")
"""Возврат стадии конвейера — разметка Т-0 не меняет тип того, что оборачивает."""

HORIZON_DAYS = 180
"""Структуры, вышедшие раньше этого срока, в карту не идут. ⚠ ЧИСЛО ИЗ ЗАМЕРА ЦЕНЫ.

⚠ ДО 2026-08-11 ЗДЕСЬ СТОЯЛО 90, И У НЕГО НЕ БЫЛО РЕФЕРЕНТА ВОВСЕ. Девяносто значатся
«альтернативой» в `docs/audit/01-recreation-attempt.md` (A-15) и больше нигде не
обоснованы. Хуже: число противоречило собственному объяснению в коде — докстрока
`run.needed_days` оправдывает отсечение тем, что «структура, из которой цена ушла ГОД
назад, уровнем по стр. 25 уже не является», а отсекало по трём месяцам.

⚠ КУРС КАЛЕНДАРНОГО СРЕЗА НЕ ДАЁТ ВООБЩЕ. Стр. 25 говорит об отработке уровня касанием,
а не о старении по календарю. Значит это не правило методики, а РЕСУРСНЫЙ ВЕНТИЛЬ, и
выбирается он ценой.

Цена замерена (зонд probe_horizon_cost_2026-08-11 (удалён 19.08), 3 символа, 1000 баров
на ряд, 2026-08-11). «Суток сделок» — сколько суток должно лежать в кэше под структуры:

    горизонт   BTC структур/суток   ETH структур/суток   ARPA структур/суток
        30         82 / 33              73 / 55              63 / 42
        90        106 / 268             93 / 520             80 / 199
       180        122 / 268            105 / 520             84 / 199
       365        129 / 422            110 / 520             90 / 407

**Переход с 90 на 180 не стоит НИ ОДНИХ дополнительных суток у всех трёх символов, а
структур даёт на 12-15% больше.** Причина видна из чисел: структуры, закрывшиеся между
90 и 180 сутками назад, лежат окнами внутри суток, уже нужных более длинным структурам.
Даром взятая точность — редкий случай, и потому взята.

Переход на 365 даёт ещё +5-6% структур ценой +37% суток у BTC и вдвое у ARPA — размен
хуже, и он не взят.

⚠ ЧЕГО ЭТОТ ЗАМЕР НЕ ГОВОРИТ. Он мерит ТРЕБОВАНИЕ, а не время: сколько из этих суток уже
в кэше, зонд не спрашивает. На вселенную из 27 символов требование при 180 составляет
~8900 символ-суток — это не разовая пауза, а длительное наполнение, и кэш для того и
сделан накопительным. Три символа — маленькая выборка; на других соотношение может быть
иным.

⚠⚠ ЗДЕСЬ СТОЯЛА ТАБЛИЦА, ПОСТРОЕННАЯ НА НЕСУЩЕСТВУЮЩЕМ ЧИСЛЕ. ОТОЗВАНА 2026-08-11.

Она утверждала, что глубина ряда равна `bars_per_timeframe = 1000` из
`config/universe.toml`, и на этом основании выводила, что горизонт не влияет на 5м, 15м,
1ч и почти не влияет на 4ч. Проверка показала: **ключ `bars_per_timeframe` не читается
НИЧЕМ** — его нет ни в `config.py`, ни где-либо ещё. Глубину всё это время задавал
`--seed-limit` с умолчанием 500, то есть вдвое меньше заявленного:

    ТФ    500 баров это    1000 баров это (как ошибочно утверждалось)
    5м       1.7 сут            3.5 сут
    15м      5.2 сут           10.4 сут
    1ч      20.8 сут           41.7 сут
    4ч      83.3 сут          166.7 сут
    1Д       1.4 года           2.7 года
    1Н       9.6 года          19.2 года

Вывод таблицы — «горизонт связывает не все ТФ» — был ВЕРЕН, и даже сильнее, чем сказано.
Неверны были числа, на которых он держался.

⚠ С 2026-08-11 вопрос снят механизмом, а не числом. Глубина каждого ряда ВЫВОДИТСЯ из
горизонта отдельно по ТФ (`run.seed_depth` → `bars.bars_needed`), и 180 суток означают
180 суток на всех ТФ. `--seed-limit` остаётся ручкой оператора и при значении больше
нуля перекрывает вывод, как раньше.

⚠ С 2026-08-23 к охвату ПРИБАВЛЯЕТСЯ прогрев, а не берётся строжайшее из двух. Прежнее
`max(прогрев, охват)` означало, что перед левым краем горизонта прогрева не остаётся
вовсе, и била эта арифметика по старшим ТФ — на 1Д ряд из 201 бара давал ema200 ровно на
ОДНОМ баре из 180. Разбор и замеры — в докстроке `bars.bars_needed`.

⚠ Прогрев в тот же день поднят с 700 до 1400 баров (`run.seed_warmup` = две длины
структуры): вторая длина нужна оглядке назад у якоря стопа, иначе окно кандидатов
упирается в начало загрузки. Глубины при горизонте 180 суток — 53 240 баров на 5м,
18 680 на 15м, 5720 на 1ч, 2480 на 4ч, 1580 на 1Д, 1425 на 1Н. Цена памяти названа в
разделе 12.7 плана docs/audit/plan-2026-08-21-geometry-transport.md.

Замер цены выше сохраняет силу как замер СДЕЛОК: он мерил, сколько суток сделок нужно под
структуры, и это не зависит от того, сколько баров лежит в ряду. Открытый вопрос о переходе
на 365 тоже остаётся: данные позволяют (глубина сделок по замеру ~373 суток), курс
календарного среза не даёт вовсе, а цена у BTC — 268 → 422 суток ради +6% структур."""


def _run(args: argparse.Namespace) -> int:
    uni = load_universe(args.universe)
    if args.symbols:
        # ⚠ ПОТОЛОК, А НЕ СРЕЗ СПИСКА, и это второе исправление одной и той же ручки.
        # Первое (2026-08-11): сборка `Universe(uni.symbols[:n], uni.timeframes,
        # uni.source)` МОЛЧА теряла `venue` и `profile_timeframe` — дефект ждал первой
        # правки конфигурации, чтобы `--symbols N` начал считать ДРУГОЙ рынок.
        # Второе (2026-08-21): срез здесь идёт ДО раскрытия доски, а раскрытие вернуло
        # бы все 696 обратно — тормоз остался бы в справке и перестал бы тормозить.
        # Потолок применяет `run._capped` там, где порядок символов уже окончателен.
        uni = uni.model_copy(update={"cap": args.symbols})
    from . import log
    from .run import (
        collect,
        decide_once,
        persist_frames,
        persist_source,
        print_report,
        produce_cards,
        record,
    )

    # ЧЕТЫРЕ ШАГА, и они видны здесь, а не спрятаны друг в друге. Раньше `live_run`
    # делал всё, причём карточку строил ВНУТРИ `persist` — сбор данных и производство
    # сигнала в одном вызове. Именно из такой слипшейся точки в прошлом проекте вырос
    # orchestrator.py на 2894 строки; разделено до реализации §2.4-2.7, пока дёшево.
    # ⚠ СТАДИИ РАЗМЕЧЕНЫ ПО ЧАСАМ 2026-08-21 (задание Т-0). Владелец сказал, что «сбор
    # и обработка данных проходит слишком долго», и до этого дня НИ ОДНА стадия не была
    # замерена: было известно только, сколько всего шёл прогон. Разметка отвечает, куда
    # именно уходит время, — без неё выбор между «ускорять транспорт» и «ускорять
    # расчёт» делался бы гаданием. Часы монотонные (`perf_counter`): настенные при
    # переводе времени дали бы отрицательную длительность.
    def _stage(name: str, fn: Callable[[], T]) -> T:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            report.stage_ms[name] = int((time.perf_counter() - t0) * 1000)

    t_collect = time.perf_counter()
    report, sources, detections = asyncio.run(
        collect(uni, args.seconds, args.seed_limit, args.horizon_days,
                decide_bars=args.decide_bars)
    )
    report.stage_ms["collect"] = int((time.perf_counter() - t_collect) * 1000)
    _stage("frames", lambda: persist_frames(args.run_id, report))
    # СИГНАЛ СЧИТАЕТСЯ ОДИН РАЗ и отдаётся обоим потребителям — карточке и леджеру.
    # До 2026-08-06 каждый считал его сам, и они расходились: карточка печатала
    # геометрию для 94 уровней, леджер эмитировал 33 (замер на кадрах прогона `a1`).
    decided = _stage("decide",
                     lambda: decide_once(report, uni, sources,
                                         detections=detections,
                                         horizon_days=args.horizon_days))
    _stage("cards", lambda: produce_cards(args.run_id, report, uni, decided))
    # Данные источника профиля кладутся ПОСЛЕ карточек. Без них повтор читает общее
    # хранилище и объявляет «расчёт изменился» на доливке (Н-6, рецидив 2026-08-18).
    _stage("source", lambda: persist_source(args.run_id, report, sources,
                                            horizon_days=args.horizon_days))
    _stage("record", lambda: record(args.run_id, report, uni, decided, detections))
    log.info("кадры сохранены", файлов=report.frames_written,
             карточек=report.cards_written,
             рядов_профиля=report.profile_series_written)
    return 1 if print_report(report) else 0


def _serve(args: argparse.Namespace) -> int:
    """Боевое исполнение: служба 24/7 (§8, находка А-1).

    Отличие от `run` не в длительности, а в устройстве: `run` собирает окно и считает
    один раз, служба собирает непрерывно и считает циклами, не останавливая сбор.
    """
    from .service import serve

    uni = load_universe(args.universe)
    if args.symbols:
        # ⚠ ПОТОЛОК, А НЕ СРЕЗ СПИСКА, и это второе исправление одной и той же ручки.
        # Первое (2026-08-11): сборка `Universe(uni.symbols[:n], uni.timeframes,
        # uni.source)` МОЛЧА теряла `venue` и `profile_timeframe` — дефект ждал первой
        # правки конфигурации, чтобы `--symbols N` начал считать ДРУГОЙ рынок.
        # Второе (2026-08-21): срез здесь идёт ДО раскрытия доски, а раскрытие вернуло
        # бы все 696 обратно — тормоз остался бы в справке и перестал бы тормозить.
        # Потолок применяет `run._capped` там, где порядок символов уже окончателен.
        uni = uni.model_copy(update={"cap": args.symbols})
    bad = asyncio.run(serve(uni, args.seed_limit, args.horizon_days, args.run_id,
                            cycle_seconds=args.cycle_seconds, max_cycles=args.cycles))
    return 1 if bad else 0


def _check(args: argparse.Namespace) -> int:
    """Единая команда владельца (§7.5, поправка 2026-08-03)."""
    from .check import run_check

    uni = load_universe(args.universe)
    if args.symbols:
        # ⚠ ПОТОЛОК, А НЕ СРЕЗ СПИСКА, и это второе исправление одной и той же ручки.
        # Первое (2026-08-11): сборка `Universe(uni.symbols[:n], uni.timeframes,
        # uni.source)` МОЛЧА теряла `venue` и `profile_timeframe` — дефект ждал первой
        # правки конфигурации, чтобы `--symbols N` начал считать ДРУГОЙ рынок.
        # Второе (2026-08-21): срез здесь идёт ДО раскрытия доски, а раскрытие вернуло
        # бы все 696 обратно — тормоз остался бы в справке и перестал бы тормозить.
        # Потолок применяет `run._capped` там, где порядок символов уже окончателен.
        uni = uni.model_copy(update={"cap": args.symbols})
    return 1 if run_check(uni, args.seconds, args.seed_limit) else 0


def _profile(args: argparse.Namespace) -> int:
    """Профиль объёма за сутки: ПОК, VAH, VAL (§2.2, §5).

    ⚠ Переписано 2026-08-11 вместе с удалением `data.binance.vision`. Раньше команда
    качала суточный ZIP архива; теперь сутки берутся тем же путём, что и в бою, — REST-ом
    ccxt. Диагностика обязана ходить туда же, куда боевой расчёт, иначе она проверяет
    не то, что работает.
    """
    import datetime as dt

    from .exchange import shared
    from .models import NotReady, RawTrade, TradeHistogram, bin_index
    from .volume_profile import Expansion, build

    day = dt.date.fromisoformat(args.day)
    start = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    end = start + 86_400_000

    async def day_histogram() -> tuple[Decimal, TradeHistogram] | NotReady:
        ex = shared(load_universe(args.universe).venue)
        await ex.open()
        try:
            symbol = next((s for s, mk in ex.markets_by_id().items()
                           if mk == args.symbol), None)
            if symbol is None:
                return NotReady(reason=f"{args.symbol}: нет такого рынка")
            inst = ex.instrument(symbol)
            if isinstance(inst, NotReady):
                return inst
            qty: dict[int, float] = {}
            cnt: dict[int, int] = {}
            seen = 0

            def on_page(page: list[RawTrade]) -> None:
                nonlocal seen
                for t in page:
                    b = bin_index(t.price, inst.tick_size)
                    qty[b] = qty.get(b, 0.0) + t.amount
                    cnt[b] = cnt.get(b, 0) + 1
                    seen += 1

            got = await ex.fetch_agg_trades_window(symbol, start, end, on_page)
            if isinstance(got, NotReady):
                return got
            _, covered = got
            if covered < end:
                pct = (covered - start) / (end - start) * 100
                return NotReady(reason=f"{args.symbol} {day}: покрыто {pct:.1f}% суток")
            return inst.tick_size, TradeHistogram(
                symbol=symbol, tick_size=inst.tick_size,
                qty_by_bin=qty, count_by_bin=cnt,
                trades_seen=seen, qty_seen=sum(qty.values()),
                first_ms=start, last_ms=end - 1)
        finally:
            await ex.close()

    res = asyncio.run(day_histogram())
    if isinstance(res, NotReady):
        print(f"НЕ ГОТОВО: {res.reason}")
        return 1
    tick, h = res
    print(f"{args.symbol} {day}: сделок {h.trades_seen:,}, бинов {len(h.qty_by_bin):,}, "
          f"объём {h.qty_seen:,.3f}, tickSize {tick}")
    for e in (Expansion.PAIRS, Expansion.SINGLE):
        vp = build(h, expansion=e)
        if isinstance(vp, NotReady):
            print(f"  {e.value:8} НЕ ГОТОВО: {vp.reason}")
            continue
        print(f"  {e.value:8} ПОК {vp.poc_price}  VAL {vp.val_price}  VAH {vp.vah_price}  "
              f"бинов {vp.bins_in_area}  покрыто {vp.covered_fraction:.4%}")
    return 0


def _admission(args: argparse.Namespace) -> int:
    """Хватает ли истории, чтобы величины §2.9 вообще существовали."""
    from .admission import REQUIRED_BARS, admits, seed_floor, unavailable_quantities
    from .exchange import shared

    uni = load_universe(args.universe)
    # ⚠ До 2026-08-17 умолчанием стоял max(REQUIRED_BARS.values()) = 304 — порог от
    # adx14, который система не считает; сборщик при этом гарантирует seed_floor().
    # Отчёт судил бы владельцу «не проходит» по планке, которой не требует ничто.
    required = args.required or seed_floor()

    async def survey() -> list[tuple[str, dict[str, int], tuple[str, ...]]]:
        """⚠ Третий элемент — ТФ, на которых счёт НЕ СОСТОЯЛСЯ (обратная сверка с ccxt,
        2026-08-11). `count_history` теперь отдаёт `NotReady` вместо стека при сетевом
        сбое, и без отдельного списка отказ печатался бы нулём баров, то есть читался бы
        как «у символа нет истории» (§4.3)."""
        from .models import NotReady

        ex = shared(uni.venue)
        await ex.open()
        try:
            out: list[tuple[str, dict[str, int], tuple[str, ...]]] = []
            for sym in uni.symbols:
                counts: dict[str, int] = {}
                unknown: list[str] = []
                # Лестница посимвольная: у доски она короче, и спрашивать биржу о
                # 5м-истории рынка, который на 5м не считается, — трата веса впустую.
                for tf in uni.ladder(sym):
                    got = await ex.count_history(sym, tf, cap=required)
                    if isinstance(got, NotReady):
                        unknown.append(tf)
                    else:
                        counts[tf] = got
                out.append((sym, counts, tuple(unknown)))
            return out
        finally:
            await ex.close()

    rows = asyncio.run(survey())
    tfs = list(uni.timeframes)
    print(f"ДОПУСК: порог {required} баров на каждом ТФ")
    # ⚠ Указатель уточнён 2026-08-17: wilder-reference покрывает только ATR/RSI (и сам
    # говорит об этом строкой 79); точки каноничности ВСЕХ величин держит гейт
    # gates/formula_reference.py на каждом прогоне CI.
    print(f"Требования замерены: {REQUIRED_BARS} (ATR/RSI — "
          f"docs/audit/wilder-reference-2026-08-03.md; все — gates/formula_reference.py)")
    print()
    print(f"(счёт с отсечкой на {required}: большее значение означает «не меньше»)")
    print()
    head = f"{'символ':22}" + "".join(f"{tf:>8}" for tf in tfs) + "  допуск  недостаёт"
    print(head)
    passed: list[str] = []
    failed: list[str] = []
    unknown_rows: list[tuple[str, tuple[str, ...]]] = []
    for sym, counts, unknown in sorted(rows,
                                       key=lambda x: min(x[1].values(), default=-1)):
        line = f"{sym:22}" + "".join(
            f"{counts[tf]:>8}" if tf in counts else f"{'?':>8}" for tf in tfs)
        if unknown:
            # Вердикта НЕТ: «не прошёл» и «не сосчитан» — разные ответы, и склеивать их
            # значило бы объявить отказ сети свойством символа.
            unknown_rows.append((sym, unknown))
            print(line + "   ?     счёт не состоялся: " + ", ".join(unknown))
            continue
        ok, short = admits(counts, required)
        line += f"  {'ДА ' if ok else 'НЕТ'}    {','.join(short) if short else '—'}"
        print(line)
        (passed if ok else failed).append(sym)
    judged = len(passed) + len(failed)
    print()
    print(f"проходят: {len(passed)} из {judged} с вердиктом   не проходят: {len(failed)}"
          f"   без вердикта: {len(unknown_rows)} из {len(rows)}")
    if failed:
        print(f"не проходят: {', '.join(failed)}")
    if unknown_rows:
        print(f"⚠ счёт не состоялся: {', '.join(s for s, _ in unknown_rows)} — "
              f"вывод о допуске этих символов НЕ СЛЕДУЕТ")
    print()
    print("Что именно недоступно у непрошедших (по самому старшему ТФ):")
    for sym, counts, _ in rows:
        top = tfs[-1]
        if top not in counts:
            continue
        miss = unavailable_quantities(counts[top])
        if miss:
            print(f"  {sym:22} {top}: {counts[top]} баров → нет {', '.join(miss)}")
    return 0


def _ledger(args: argparse.Namespace) -> int:
    """§10.6 условие 1: владелец проверяет леджер тремя запросами, не читая код."""
    if args.init:
        path = store.init_ledger()
        print(f"база создана: {path}")
        return 0
    try:
        conn = store.open_readonly()
    except FileNotFoundError as e:
        print(f"{e}\nсоздать: uv run python -m hunter ledger --init")
        return 1
    try:
        for title, sql in store.OWNER_QUERIES.items():
            print(f"\n### {title}")
            print(f"    {sql}")
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            print("    " + " | ".join(cols))
            if not rows:
                print("    (строк нет)")
            for r in rows[:50]:
                print("    " + " | ".join(str(x) for x in r))
        print("\nсоединение открыто ТОЛЬКО НА ЧТЕНИЕ (§10.2) — проверка:")
        try:
            conn.execute("INSERT INTO signals (symbol, timeframe, direction, opened_at,"
                         " entry, stop, frames_ref) VALUES ('X','1h','long',1,1,2,'x')")
            print("    ПРОВАЛ: запись прошла, хотя соединение read-only")
            return 1
        except sqlite3.OperationalError as e:
            print(f"    попытка записи отклонена СУБД: {e}")
        mixed = _journal_vs_universe(conn, load_universe(args.universe))
    finally:
        conn.close()
    return 1 if mixed else 0


def _journal_vs_universe(conn: sqlite3.Connection, uni: Universe) -> int:
    """ИЗ ЧЕГО СОСТОИТ ЖУРНАЛ: символы вселенной против всех прочих. Возвращает лишние.

    ⚠ ЗАЧЕМ ЭТО ЗДЕСЬ. Запрос «результат в R» считает по ВСЕЙ таблице исходов, и до
    2026-08-17 никто не спрашивал, из чего эта таблица состоит. Замер: 49 сигналов из 192
    (26%) записаны по символам ВНЕ закреплённой вселенной — `BTW`, `BEAT` и `XAU`, причём
    XAU это золото, которое FOUNDATION §5 называет дословно в списке исключённых. То есть
    владельцу печатался средний R, посчитанный в том числе по золоту, и узнать об этом по
    выдаче было нельзя.

    Это ровно тот класс, который CLAUDE.md называет главным: «всякая накопленная
    статистика обязана называть, по какой ПОДВЫБОРКЕ она посчитана».

    Строки НЕ удаляются и удалены здесь не будут: журнал боевой, удаление необратимо, и
    решение о нём принимает владелец. Задача этой функции — сделать смесь видимой числом.
    """
    known = set(uni.symbols)
    rows = conn.execute(
        "SELECT symbol, COUNT(*) FROM signals GROUP BY symbol ORDER BY 2 DESC").fetchall()
    total = sum(int(n) for _, n in rows)
    outside = [(str(s), int(n)) for s, n in rows if s not in known]
    extra = sum(n for _, n in outside)
    print(f"\n### из чего состоит журнал (вселенная {uni.source})")
    print(f"    символов в журнале: {len(rows)}, из них вне вселенной: {len(outside)}")
    print(f"    сигналов: {total}, из них вне вселенной: {extra}"
          f" ({extra / total * 100:.1f}%)" if total else "    сигналов: 0")
    if not outside:
        print("    ⚠ ноль лишних символов — это ЗАМЕР, а не отсутствие проверки:"
              f" сверено {len(rows)} символов со списком из {len(known)}")
        return 0
    for sym, n in outside:
        named = " — §5 называет его в списке ИСКЛЮЧЁННЫХ" if sym.split("/")[0] in {
            "XAU", "XAG", "PAXG"} else ""
        print(f"    ⚠ {sym}: {n} сигналов, во вселенной НЕ значится{named}")
    print("    СЛЕДСТВИЕ: «средний R» и «сколько сделок» выше посчитаны ВМЕСТЕ с этими")
    print("    символами. Число выше — не результат системы на её вселенной.")
    print("    Решение владельца: либо эти символы войдут во вселенную (§5), либо их")
    print("    строки уйдут из журнала. Ни того, ни другого код сам не делает.")
    return extra


def _replay(args: argparse.Namespace) -> int:
    """Повтор карточки из сохранённых кадров (§10.6 условие 2)."""
    if args.card:
        text = store.read_card(args.run_id, args.card)
        if isinstance(text, NotReady):
            print(f"ПЛОХО: {text.reason}")
            return 1
        print(text, end="")
        return 0
    res = replay.replay_run(args.run_id)
    if isinstance(res, NotReady):
        print(f"ПЛОХО: {res.reason}")
        return 1
    return replay.print_result(res, show_diff=args.diff)


def main(argv: list[str] | None = None) -> int:
    log.configure()
    p = argparse.ArgumentParser(prog="hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="живой прогон и сводка приёмки")
    run.add_argument("--seconds", type=int, default=90)
    run.add_argument("--seed-limit", type=int, default=0,
                     help="баров на ряд — и запроса, И ряда решения; НЕ МЕНЬШЕ пола "
                          "прогрева (1400), меньшее число молча поднимается до него — "
                          "значит КОНТРОЛЬ ГЛУБИНЫ им не делается, для него --decide-bars; "
                          "0 = вывести из --horizon-days отдельно по каждому ТФ")
    run.add_argument("--decide-bars", type=int, default=0,
                     help="предел длины ряда, идущего в РАСЧЁТ, БЕЗ пола прогрева — "
                          "ручка контроля глубины (свод: разметка не смеет быть функцией "
                          "того, сколько баров скачали); 0 = весь накопленный ряд")
    run.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    run.add_argument("--run-id", default="last")
    # ⚠ Раньше здесь было `--trade-days` = «сколько ПОСЛЕДНИХ суток скачать», умолчание 3.
    # Ручка задавала не глубину архива, а то, БУДЕТ ЛИ уровень вообще: при 3 сутках его
    # мог получить 15% структур, а на 4ч и 1Д — ноль из 28 и 21. Теперь набор суток
    # выводится из окон структур (`run.needed_days`), а этот параметр задаёт только
    # ГОРИЗОНТ: насколько старые структуры ещё считаются картой (стр. 25).
    run.add_argument("--horizon-days", type=int, default=HORIZON_DAYS,
                     help="структуры, вышедшие раньше чем N суток назад, в карту не идут; "
                          "0 = не доливать сделки вовсе")
    run.add_argument("--symbols", type=int, default=0,
                     help="взять только первые N символов вселенной")

    srv = sub.add_parser("serve", help="СЛУЖБА 24/7: сбор без остановки, расчёт циклами")
    srv.add_argument("--cycle-seconds", type=int, default=service.CYCLE_SECONDS,
                     help="такт расчёта; умолчание — младший ТФ проекта (§2.8)")
    srv.add_argument("--cycles", type=int, default=0,
                     help="остановиться после N циклов; 0 = работать до сигнала")
    srv.add_argument("--seed-limit", type=int, default=0,
                     help="баров на ряд — и запроса, И ряда решения; НЕ МЕНЬШЕ пола "
                          "прогрева (1400), меньшее число молча поднимается до него; "
                          "0 = вывести из --horizon-days отдельно по каждому ТФ")
    srv.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    srv.add_argument("--run-id", default="serve",
                     help="куда класть кадры и карточки; каждый цикл ПЕРЕЗАПИСЫВАЕТ их")
    srv.add_argument("--horizon-days", type=int, default=HORIZON_DAYS)
    srv.add_argument("--symbols", type=int, default=0,
                     help="взять только первые N символов вселенной")

    chk = sub.add_parser("check", help="ПРОВЕРКА: один вход, вердикт по-русски (§7.5)")
    chk.add_argument("--seconds", type=int, default=400)
    chk.add_argument("--seed-limit", type=int, default=500)
    chk.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    chk.add_argument("--symbols", type=int, default=0)

    prof = sub.add_parser("profile", help="профиль объёма за сутки: ПОК, VAH, VAL")
    prof.add_argument("--symbol", required=True, help="идентификатор биржи, напр. BTCUSDT")
    prof.add_argument("--day", required=True, help="дата YYYY-MM-DD")
    # Площадка берётся из вселенной: профиль спота и профиль бессрочного — разные числа.
    prof.add_argument("--universe", type=Path, default=DEFAULT_PATH)

    adm = sub.add_parser("admission", help="хватает ли истории на величины §2.9")
    adm.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    adm.add_argument("--required", type=int, default=0,
                     help="порог баров на каждом ТФ; 0 = самое строгое замеренное")

    led = sub.add_parser("ledger", help="три проверочных запроса к леджеру (§10.6)")
    led.add_argument("--init", action="store_true", help="создать базу со схемой")
    led.add_argument("--universe", type=Path, default=DEFAULT_PATH,
                     help="с чем сверять состав журнала: сигналы по символам вне "
                          "вселенной делают «средний R» числом по другой выборке")

    tg = sub.add_parser("bot", help="ТЕЛЕГРАМ-БОТ доставки: тикер → скриншоты карты + сводка")
    tg.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    tg.add_argument("--horizon-days", type=int, default=HORIZON_DAYS,
                    help="глубина структур при сборке монеты ВНЕ вселенной по запросу")
    tg.add_argument("--publish-now", action="store_true",
                    help="разово опубликовать закреплённые монеты в канал и выйти — "
                         "проверка канала одной командой, без ожидания закрытия бара")

    snt = sub.add_parser("sent",
                         help="ЧТО УШЛО В ТЕЛЕГРАМ: последние сообщения дословно")
    snt.add_argument("--limit", type=int, default=50,
                     help="сколько последних сообщений показать")

    rep = sub.add_parser("replay",
                         help="ПОВТОР: пересобрать карточку из кадров и показать разницу")
    rep.add_argument("--run-id", default="last")
    rep.add_argument("--diff", action="store_true", help="печатать саму разницу построчно")
    rep.add_argument("--card", default="", help="просто показать карточку символа")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "check":
        return _check(args)
    if args.cmd == "profile":
        return _profile(args)
    if args.cmd == "admission":
        return _admission(args)
    if args.cmd == "ledger":
        return _ledger(args)
    if args.cmd == "replay":
        return _replay(args)
    if args.cmd == "sent":
        # ⚠ Заведено 2026-08-19 по вопросу владельца «почему у тебя нигде не
        # сохраняется сообщения которые были отправлены в телеграм?». Ответ был —
        # никто этого не написал: лог держал СОБЫТИЕ отправки без текста, и проверить,
        # что прочитал читатель, было нечем.
        from .tgbot import last_sent
        rows = last_sent(args.limit)
        if not rows:
            print("Архив пуст: с момента заведения архива (2026-08-19) бот ничего не"
                  " отправлял, либо каталог data/sent ещё не создан.")
            return 0
        print(f"ПОСЛЕДНИЕ {len(rows)} СООБЩЕНИЙ, свежие сверху")
        print("=" * 78)
        for r in rows:
            at = datetime.fromtimestamp(int(r["at_ms"]) / 1000, tz=UTC)
            print(f"\n[{at:%Y-%m-%d %H:%M:%S} UTC] {r['kind']} → {r['chat']}")
            print("-" * 78)
            print(r["text"])
        return 0
    if args.cmd == "bot":
        from .tgbot import main as bot_main
        return asyncio.run(bot_main(horizon_days=args.horizon_days,
                                    publish_now=args.publish_now,
                                    universe=args.universe))
    return 2


if __name__ == "__main__":
    sys.exit(main())
