"""Единая команда проверки для владельца. FOUNDATION.md §7.5 (поправка 2026-08-03).

Владелец не программист. Если проверка живого состояния требует помнить несколько
команд — её не будет. Здесь один вход, печатающий вердикт по-русски.

    uv run python -m hunter check

Живой прогон в CI невозможен: Binance отвечает раннерам GitHub HTTP 451
(замер 2026-08-03). Поэтому эта команда — то, чем живое состояние проверяется
на машине владельца.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3

from . import log, store
from .admission import REQUIRED_BARS, USED_BY_2_9, strictest_requirement
from .bars import expected_last_closed_open_ms, tf_ms
from .config import Universe
from .exchange import Exchange
from .models import NotReady, RunReport
from .run import collect, explained_gaps


def _verdict(lines: list[tuple[str, bool, str]]) -> int:
    print()
    print("=" * 72)
    print("ВЕРДИКТ")
    print("=" * 72)
    bad = 0
    for title, ok, detail in lines:
        mark = "   ХОРОШО " if ok else "   ПЛОХО  "
        if not ok:
            bad += 1
        print(f"{mark} {title}")
        print(f"            {detail}")
    print()
    if bad == 0:
        print(f"ИТОГ: всё в порядке. Проверок пройдено: {len(lines)}.")
    else:
        print(f"ИТОГ: ТРЕБУЕТ ВНИМАНИЯ. Плохо в {bad} из {len(lines)} проверок.")
        print("Покажите этот вывод целиком — по нему видно, что именно сломалось.")
    print("=" * 72)
    return bad


async def _admission_survey(
    uni: Universe, required: int
) -> dict[str, dict[str, int | NotReady]]:
    ex = Exchange(uni.venue)
    await ex.open()
    try:
        out: dict[str, dict[str, int | NotReady]] = {}
        for sym in uni.symbols:
            out[sym] = {tf: await ex.count_history(sym, tf, cap=required)
                        for tf in (uni.timeframes[-1],)}
        return out
    finally:
        await ex.close()


def _report_admission(
    counts: dict[str, dict[str, int | NotReady]], top_tf: str
) -> tuple[bool, str]:
    """§2.9 не гейтит сигнал — поэтому это не «плохо», а именно перечень недоступного.

    ⚠ НЕСОСТОЯВШИЙСЯ СЧЁТ отделён от малого счёта 2026-08-11 (обратная сверка с ccxt).
    До этого `count_history` не ловила ничего и роняла обзор стеком; починка ветвей без
    отдельного значения превратила бы отказ сети в маленькое число, и символ попал бы в
    строку «доп-факторов нет» по причине, не имеющей отношения к его истории. Такой отказ
    печатается своей строкой и своим числом — знаменатель у вывода обязан быть виден.
    """
    print("\n2. КАКИЕ ДОП-ФАКТОРЫ НЕДОСТУПНЫ (§2.9, §4.3)")
    print(f"   Проверяется старший ТФ: {top_tf}")
    print("   Индикаторы по §2.9 сигнал НЕ порождают и НЕ гейтят — сторону задаёт")
    print("   структура старшего ТФ. Недоступность фактора видна, но сигнал не блокирует.")
    missing_any = 0
    unknown: list[str] = []
    for sym, by_tf in sorted(counts.items()):
        bars = by_tf[top_tf]
        if isinstance(bars, NotReady):
            unknown.append(sym)
            print(f"   {sym:22} {top_tf}: счёт истории НЕ СОСТОЯЛСЯ — {bars.reason}")
            continue
        miss = [q for q in USED_BY_2_9 if bars < REQUIRED_BARS[q]]
        if miss:
            missing_any += 1
            print(f"   {sym:22} {top_tf}: {bars:4} баров → НЕТ {', '.join(miss)}")
    total = len(counts)
    counted = total - len(unknown)
    print(f"   символов со всеми доп-факторами: {counted - missing_any} из {counted} "
          f"сосчитанных (всего в выборке {total})")
    if unknown:
        print(f"   ⚠ счёт не состоялся у {len(unknown)} из {total}: "
              f"{', '.join(unknown)} — про них вывод НЕ СЛЕДУЕТ")
    # ⚠ До 2026-08-18 возвращалось безусловное `True` — строка вердикта не могла
    # стать красной (дефект Д-1). Нарушение здесь — несостоявшийся СЧЁТ (прибор не
    # ответил), а не отсутствие факторов: короткая история — состояние рынка, оно
    # печатается числом выше и нарушением не является.
    return len(unknown) == 0, (
        f"{counted - missing_any} из {counted} сосчитанных символов имеют все "
        f"доп-факторы; не сосчитано {len(unknown)} из {total}")


def _report_live(r: RunReport) -> list[tuple[str, bool, str]]:
    """Время берётся ИЗ ОТЧЁТА (`taken_at_ms`), а не с часов.

    ⚠ Здесь стояло `clock.now_ms()`, то есть момент ПЕЧАТИ, а бары относятся к моменту
    снятия снимка. Разница — длительность всего, что стоит между сбором и печатью, и
    если за неё пересекается граница ТФ, проверка объявляет отставание, которого нет.
    Разбор — в `RunReport.taken_at_ms`.
    """
    now_ms = r.taken_at_ms
    ready = [s for s in r.series.values() if s.not_ready is None]
    missing = [s for s in r.series.values() if s.not_ready is not None]
    stale = 0
    for st in ready:
        expected = expected_last_closed_open_ms(st.timeframe, now_ms)
        if now_ms and (expected - st.bars[-1].open_ms) // tf_ms(st.timeframe) > 0:
            stale += 1
    rejected = [x for s in r.series.values() for x in s.rejected_bars]
    # Последствие отклонения битых баров: принятые ряды обязаны быть чистыми.
    # Дефектная геометрия — нечисловая цена или экстремумы, не накрывающие тело;
    # это те же условия, по которым бар отвергается на приёме, посчитанные заново
    # НЕЗАВИСИМЫМ обходом (проверка не доверяет счётчику того, кого проверяет).
    kept_bars = sum(len(s.bars) for s in ready)
    bad_kept = sum(
        1 for s in ready for b in s.bars
        if not (math.isfinite(b.open) and math.isfinite(b.high)
                and math.isfinite(b.low) and math.isfinite(b.close)
                and math.isfinite(b.volume)
                and b.low <= min(b.open, b.close)
                and b.high >= max(b.open, b.close)))
    # Д-4: разрыв объясняется отклонённым баром ВНУТРИ него, а не самим фактом отказа
    # где-то в ряду. Формула живёт в `run.explained_gaps` — вторая копия разошлась бы.
    explained = sum(len(explained_gaps(s)) for s in ready)
    gaps = sum(len(s.gaps) for s in ready)
    unexplained = gaps - explained
    # ⚠ До 2026-08-05 здесь стоял `ws_unclosed_violations` — счётчик, который не мог
    # вырасти: поток отдавал бар при `now >= open_ms + tf`, а проверка спрашивала то же
    # самое и теми же часами (Д-1). Строка вердикта была зелёной по построению.
    # Бары теперь берутся опросом, и разделены ДВЕ разные величины:
    #   * `not_ready_polls` — нарушение: опрос не дал ряда (пусто, битый бар, вне
    #     сетки). Вердикт по нему, и вырасти он способен;
    #   * `late` — ЗАМЕР: сколько раз биржа не успела отдать свечу к нашему отступу.
    #     Ответ приходит от биржи, а ожидание строится по нашим часам, поэтому число
    #     способно быть любым. Нарушением оно не является — бар доедет следующим
    #     опросом, — и потому в вердикт не входит, а печатается рядом с ним.
    not_ready_polls = sum(s.poll_not_ready for s in r.series.values())
    late = sum(s.poll_late for s in r.series.values())
    requests = sum(s.poll_requests for s in r.series.values())
    trades = sum(h.trades_seen for h in r.histograms.values())
    runs = store.saved_runs()
    frames_on_disk = (sum(1 for _ in store.FRAMES_DIR.rglob("*.parquet"))
                      if store.FRAMES_DIR.is_dir() else 0)

    out = [
        ("Данные свежие",
         stale == 0,
         f"отстающих рядов {stale} из {len(ready)}; проверено {len(r.series)} рядов "
         f"(символ × таймфрейм)"),
        ("Ничего не пропущено молча",
         len(missing) == 0 and unexplained == 0,
         f"рядов без данных {len(missing)}; необъяснённых разрывов {unexplained} "
         f"(проверено {r.seeded_bars} баров)"),
        ("Добор баров не отвергнут биржей",
         not_ready_polls == 0,
         f"опросов без ряда {not_ready_polls} из {requests} "
         f"(пустой ответ, битый бар или бар вне сетки); добрано "
         f"{sum(s.polled_bars for s in r.series.values())} баров; "
         f"биржа опоздала с ожидаемой свечой {late} раз — это ЗАМЕР отступа "
         f"POLL_OFFSET_S, не нарушение"),
        # ⚠ До 2026-08-18 условием стояло `True` — строка не могла стать красной,
        # ровно дефект Д-1 («зелёная по построению»), воспроизведённый заново после
        # его разбора. Теперь проверяется ПОСЛЕДСТВИЕ отклонения: в принятых рядах
        # не должно остаться ни одного бара с дефектной геометрией. Счёт отклонённых
        # печатается рядом как замер, он нарушением не является.
        ("Битые бары биржи отклонены",
         bad_kept == 0,
         f"битых баров в принятых рядах {bad_kept} из {kept_bars}; "
         f"отклонено на приёме {len(rejected)} " +
         (f"— {rejected[0][:90]}…" if rejected else "(ни одного за этот прогон)")),
        ("Сделки принимаются",
         trades > 0,
         f"принято {trades} сделок по {len(r.histograms)} символам; "
         f"расхождение агрегации "
         f"{max((h.reconciliation_error() for h in r.histograms.values()), default=0):.1e}"),
        # ⚠ Спрашивается СОСТОЯНИЕ ДИСКА, а не работа этой команды. Прежняя редакция
        # требовала `r.frames_written > 0` — а `check` кадров не пишет с тех пор, как
        # конвейер разделили на четыре шага: она только собирает (см. `run_check`).
        # Условие стало невыполнимым по построению, и владелец с исправной системой видел
        # постоянное «ПЛОХО». Ложное красное хуже пропуска: оно учит не верить красному.
        ("Есть что повторить: кадры на диске",
         len(runs) > 0,
         f"прогонов с кадрами {len(runs)}, файлов parquet {frames_on_disk}; "
         f"свежайший — «{runs[-1] if runs else '—'}». Сама проверка кадров НЕ пишет "
         f"(§7.5: она отвечает, живы ли данные); кадры пишет `hunter run`"),
        # 5000 мс — НЕ выдуманное число, и источник назван 2026-08-08 обзором часов.
        # Умолчание `recvWindow` у Binance равно 5000 мс, у Bybit в его SDK — тоже: это
        # ровно та величина расхождения, при которой биржа перестала бы принимать наши
        # запросы. Внешний референт по §0. Разбор 2026-08-04 называл его «абсолютным
        # числом без источника», и это было верно: связь существовала, но записана не была.
        #
        # ⚠ Правило биржи НЕСИММЕТРИЧНО: метка принимается, если она меньше
        # `serverTime + 1000` И `serverTime − метка` не больше `recvWindow`. Наша проверка
        # симметрична. Расхождение ЗАМЕРЕНО и оказалось теоретическим: на 50 сведениях в
        # зону «мы впереди биржи больше секунды» не попало НИ ОДНО (наши часы стабильно
        # отстают на ~240 мс). Контроль достижимости зоны пройден: те же замеры со сдвигом
        # −2000 мс попадают в неё все 50. Порог оставлен симметричным сознательно, а не по
        # недосмотру. Обзор: docs/audit/clock-projects-2026-08-08.md
        ("Часы сведены с биржей",
         abs(r.sync.offset_ms) < 5000,
         f"сдвиг {r.sync.offset_ms:+d} мс при точности замера ±{r.sync.rtt_ms // 2} мс"),
    ]
    return out


def run_check(uni: Universe, seconds: int, seed_limit: int) -> int:
    log.configure()
    print("=" * 72)
    print("ПРОВЕРКА ЖИВОГО СОСТОЯНИЯ")
    print("=" * 72)
    print(f"Вселенная: {len(uni.symbols)} символов × {len(uni.timeframes)} таймфреймов")
    print(f"Наблюдение: {seconds} с. Это займёт примерно {seconds // 60 + 2} минут.")

    print("\n1. ЖИВОЙ ПРОГОН")
    # Здесь нужен ТОЛЬКО сбор: `check` отвечает на вопрос «живы ли данные», карточки и
    # леджер к нему отношения не имеют. До разделения конвейера отделить одно от другого
    # было нельзя, и проверка состояния попутно писала в боевую базу.
    report, _sources = asyncio.run(collect(uni, seconds, seed_limit, horizon_days=0))
    lines = _report_live(report)

    # Порог берётся у `admission`, а не пересчитывается здесь. Строка была дословной
    # копией тела `strictest_requirement()`: две копии одной формулы разошлись бы на
    # первой же правке `USED_BY_2_9`, и заметить это было бы нечем.
    required = strictest_requirement()
    counts = asyncio.run(_admission_survey(uni, required))
    ok, detail = _report_admission(counts, uni.timeframes[-1])
    lines.append(("Доп-факторы §2.9 перечислены поимённо", ok, detail))

    print("\n3. ЛЕДЖЕР")
    conn = None
    try:
        conn = store.open_readonly()
        n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        # Сводка исходов ПО ИЗМЕРЕНИЯМ (2026-08-10): «средний R» по всему леджеру
        # молчит о том, что весь плюс сделан одним ТФ или одной стороной. Перекос
        # виден только в разрезе — урок backfill-window-2026-08-04.
        survey = store.outcome_survey(conn)
        # Ширина зоны против корпуса (2026-08-18): самая широкая РАЗМЕЧЕННАЯ ЗОНА
        # ИНТЕРЕСА, опубликованная автором за 17 дней канала, — 38.7% от СЕРЕДИНЫ
        # диапазона (161 зона, разбор docs/audit/tg-prizrak-2026-08.md; у прибора
        # потолок 60% по построению, отбросы проверены поштучно). БАЗЫ автор
        # публикует и шире (LINK «база 7-11» = 44% прозой, мимо разметки) — порог
        # сравнивает наши зоны уровней именно с зонами интереса, ближайшим аналогом
        # по форме (диапазон + ПОК внутри). Линейка ТА ЖЕ — от середины зоны, не от
        # ПОК: ПОК не в центре, и деление на него завышает ширину (falsifier
        # 2026-08-18: 136% против 83% по одной и той же зоне). Порог 0.39 — максимум
        # корпусной выборки, а не подобранное число; строка — диагностика перекоса,
        # расчёт она не фильтрует. Способность ответить иначе проверена порогом выше
        # максимума леджера (пусто) и ниже медианы (тысячи).
        wide = conn.execute(
            "SELECT symbol, timeframe,"
            " (zone_hi-zone_lo)/((zone_hi+zone_lo)/2)*100 AS w FROM levels"
            " WHERE state='active' AND zone_hi+zone_lo > 0"
            " AND (zone_hi-zone_lo)/((zone_hi+zone_lo)/2) > 0.39 ORDER BY w DESC"
        ).fetchall()
        # Знаменатели по ТФ: одна худшая зона без «из скольких» скрыла бы измерение
        # перекоса (урок backfill-window-2026-08-04 — сводка вдоль измерения).
        tf_totals: dict[str, int] = dict(conn.execute(
            "SELECT timeframe, COUNT(*) FROM levels"
            " WHERE state='active' AND zone_hi+zone_lo > 0 GROUP BY timeframe"
        ).fetchall())
        lines.append(("Леджер читается", True, f"записей о сигналах: {n}"))
        print(f"   записей: {n}")
        if wide:
            worst = wide[0]
            per_tf: dict[str, int] = {}
            for _sym, tf, _w in wide:
                per_tf[tf] = per_tf.get(tf, 0) + 1
            tf_summary = ", ".join(
                f"{tf} {per_tf[tf]}/{tf_totals.get(tf, 0)}" for tf in sorted(per_tf)
            )
            wide_detail = (
                f"{len(wide)} из {sum(tf_totals.values())} активных (по ТФ: {tf_summary}); "
                f"худшая {worst[0]} {worst[1]} = {worst[2]:.0f}% от середины; корпусный "
                "максимум зоны интереса 38.7% (docs/audit/tg-prizrak-2026-08.md); "
                "красная до решения открытого вопроса о широких структурах"
            )
        else:
            wide_detail = (
                f"активных зон шире 39% от середины нет (из {sum(tf_totals.values())})"
            )
        lines.append(
            ("Зоны не шире корпусного максимума (39% от середины)", not wide, wide_detail)
        )
        # ⚠ ВТОРАЯ ПОЛОВИНА СИЛЫ — СВОДКА ПО ТФ, а не одно число (2026-08-19). Стр. 22:
        # «Сила уровня определяется ТФ и объемом». С этой смены плотность объёма
        # участвует в отборе уровней бота, значит владельцу нужно видеть, ЕСТЬ ли у
        # отбора данные: у строк карты до схемы 8 плотность NULL, и отбор по ним
        # вырождается в прежний. Знаменатель и разрез по ТФ — по уроку backfill-window-2026-08-04:
        # сто честных «не посчитано», все на одном ТФ, читаются как «рынок такой».
        vol_rows: dict[str, tuple[int, int]] = {}
        # Колонки может не быть: миграцию делает писатель, а `check` читает `mode=ro`.
        # Тогда посчитанных нет ни одной — это и есть честный ответ, а не отказ.
        have_col = ("COUNT(vrvp_density)"
                    if "vrvp_density" in store.level_columns(conn) else "0")
        for tf, total, have in conn.execute(
            f"SELECT timeframe, COUNT(*), {have_col} FROM levels"
            " WHERE state='active' GROUP BY timeframe"
        ).fetchall():
            vol_rows[tf] = (int(total), int(have))
        v_total = sum(t for t, _ in vol_rows.values())
        v_have = sum(h for _, h in vol_rows.values())
        vol_summary = ", ".join(f"{tf} {vol_rows[tf][1]}/{vol_rows[tf][0]}"
                                for tf in sorted(vol_rows))
        lines.append((
            "Сила по объёму посчитана у активных уровней (стр. 22)",
            v_total > 0 and v_have == v_total,
            (f"плотность объёма есть у {v_have} из {v_total} (по ТФ: {vol_summary}); "
             "у остальных NULL — карта записана до схемы 8, отбор по ним идёт "
             "прежним ключом (ТФ, ширина зоны)")
            if v_total else "активных уровней в карте нет — мерить нечего",
        ))
        for row in store.format_outcome_survey(survey):
            print(f"   {row}" if not row.startswith(" ") else row)
        skewed = [c for c in survey.cells if c.closed == 0 and c.signals > 0]
        lines.append((
            "Исходы сведены по ТФ, стороне и типу сигнала",
            not skewed,
            (f"клеток без единого исхода: {len(skewed)} из {len(survey.cells)} — "
             "перекос вдоль измерения, пока не показано обратное")
            if skewed else
            f"перекоса нет: во всех {len(survey.cells)} клетках есть закрытые сделки",
        ))
    except FileNotFoundError:
        lines.append(("Леджер читается", False,
                      "базы нет; создать: uv run python -m hunter ledger --init"))
        print("   базы нет")
    # ⚠ До 2026-08-18 ловился только FileNotFoundError: любая ошибка SQL (битая база,
    # старая схема без таблицы levels) роняла всю проверку трейсбеком и оставляла
    # соединение открытым — вместо красной строки с причиной (§4.3).
    except sqlite3.Error as e:
        lines.append(("Леджер читается", False,
                      f"база не прочитана: {type(e).__name__}: {e}"))
        print(f"   база не прочитана: {e}")
    finally:
        if conn is not None:
            conn.close()

    return _verdict(lines)
