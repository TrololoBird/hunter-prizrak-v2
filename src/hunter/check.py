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

from . import log, store
from .admission import REQUIRED_BARS, USED_BY_2_9, strictest_requirement
from .bars import expected_last_closed_open_ms, tf_ms
from .config import Universe
from .exchange import Exchange
from .models import RunReport
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


async def _admission_survey(uni: Universe, required: int) -> dict[str, dict[str, int]]:
    ex = Exchange()
    await ex.open()
    try:
        out: dict[str, dict[str, int]] = {}
        for sym in uni.symbols:
            out[sym] = {tf: await ex.count_history(sym, tf, cap=required)
                        for tf in (uni.timeframes[-1],)}
        return out
    finally:
        await ex.close()


def _report_admission(counts: dict[str, dict[str, int]], top_tf: str) -> tuple[bool, str]:
    """§2.9 не гейтит сигнал — поэтому это не «плохо», а именно перечень недоступного."""
    print("\n2. КАКИЕ ДОП-ФАКТОРЫ НЕДОСТУПНЫ (§2.9, §4.3)")
    print(f"   Проверяется старший ТФ: {top_tf}")
    print("   Индикаторы по §2.9 сигнал НЕ порождают и НЕ гейтят — сторону задаёт")
    print("   структура старшего ТФ. Недоступность фактора видна, но сигнал не блокирует.")
    missing_any = 0
    for sym, by_tf in sorted(counts.items()):
        bars = by_tf[top_tf]
        miss = [q for q in USED_BY_2_9 if bars < REQUIRED_BARS[q]]
        if miss:
            missing_any += 1
            print(f"   {sym:22} {top_tf}: {bars:4} баров → НЕТ {', '.join(miss)}")
    total = len(counts)
    print(f"   символов со всеми доп-факторами: {total - missing_any} из {total}")
    return True, (f"{total - missing_any} из {total} символов имеют все доп-факторы; "
                  f"у остальных недостающие названы поимённо")


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
        ("Битые бары биржи отклонены",
         True,
         f"отклонено {len(rejected)} " +
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
    try:
        conn = store.open_readonly()
        n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        conn.close()
        lines.append(("Леджер читается", True, f"записей о сигналах: {n}"))
        print(f"   записей: {n}")
    except FileNotFoundError:
        lines.append(("Леджер читается", False,
                      "базы нет; создать: uv run python -m hunter ledger --init"))
        print("   базы нет")

    return _verdict(lines)
