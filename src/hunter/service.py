"""Служба 24/7: сбор не прерывается, расчёт идёт циклами. FOUNDATION.md §8, находка А-1.

Что здесь появилось и чего не было. До 2026-08-06 у проекта была одна модель исполнения —
`collect(seconds)`: открыть соединение, собрать N секунд, снять задачи, посчитать, выйти.
Внешний разбор поставил это первой строкой списка работ («модели исполнения 24/7 нет») и
выводил из неё целый класс дефектов: Д-3 (разрывы считались только в засеве), Д-5 (часы
сводились один раз), Д-6 (пропущенное за обрыв не докачивалось), Д-8 (блокирующая
закачка в корутине). Каждый из них по отдельности уже закрыт — но закрывались они ПОД
службу, которой не было; проверить их было негде.

Разница между прогоном и службой одна, и она порождает всё остальное:

    прогон:  сбор → остановка сбора → расчёт → выход
    служба:  сбор ──────────────────────────────────────► (бесконечно)
                    расчёт ▲       расчёт ▲       расчёт ▲

Расчёт больше не имеет права останавливать сбор. Отсюда два требования, которых у
пакетной схемы не было вовсе:

1. **Расчёт идёт ВНЕ цикла событий.** Замер А-2 (2026-08-05): `levels.build_all` на BTC
   занимает 18.1 с, на ETH 8.8 с. Восемнадцать секунд на цикле событий — это восемнадцать
   секунд, когда не опрашиваются бары и не разбирается поток сделок. Цена измерима:
   `watch_trades` у ccxt складывает сделки в кэш ограниченной длины, и всё неразобранное
   вытесняется. Прибор — `RunReport.trade_gaps` (разрывы номеров aggTrade), контроль —
   `loop_stall_max_ms` (сердцебиение).

2. **Расчёт работает на СНИМКЕ.** Уйдя в рабочий поток, он читает те же словари, в
   которые пишет задача сделок, — а это не «неточные числа», а `RuntimeError: dictionary
   changed size during iteration`. `Collector.snapshot()` отдаёт срез, снятый между двумя
   состояниями сбора.

Остановка — по сигналу, и она ПОЛНАЯ: текущий цикл дорабатывается, задачи снимаются,
соединение закрывается, итог печатается. Прерванный на середине расчёт оставил бы кадры
от одного момента и карточку от другого, то есть сломал бы повтор §10.6.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from . import clock, log, run
from .config import Universe
from .models import RunReport, TradeWindows

CYCLE_SECONDS = 300
"""Как часто пересчитывать. Число ВЫВЕДЕНО из §2.8, а не подобрано.

Самый младший таймфрейм проекта — 5 минут, значит чаще пересчитывать нечего: новых
закрытых баров между циклами просто не появится, и повторный расчёт дал бы тот же ответ
на те же данные. Реже — значит узнавать о новом баре 5м с запозданием больше его
собственной длительности.

⚠ Это НЕ верхняя граница по стоимости. Если расчёт длится дольше такта, следующий цикл
начинается сразу по его окончании, и `cycle_seconds` в приёмке это показывает числом.
Подгонять такт под стоимость расчёта нельзя: такт задаётся данными, а не нашей скоростью.
"""

SETTLE_S = 0.05
"""Сколько дать циклу событий перед снятием задач, чтобы измерители сняли последнее
показание. Не пауза «на всякий случай»: без неё остановка цикла, кончившаяся вместе со
службой, не попадает в замер никогда — см. `run._heartbeat_impl`."""


def _install_stop_handlers(stop: asyncio.Event) -> tuple[str, ...]:
    """Поставить обработчики сигналов остановки. Возвращает поставленные — ЧИСЛОМ в отчёт.

    ⚠ Именно `signal.signal`, а не `loop.add_signal_handler`: второго на Windows нет
    вовсе (`NotImplementedError`), а служба разрабатывается и запускается там.

    Обработчик не делает НИЧЕГО, кроме передачи события в цикл: он выполняется между
    байткодами в произвольной точке, и логировать или закрывать что-либо оттуда значит
    писать в те же структуры, которые прямо сейчас кто-то меняет.
    """
    loop = asyncio.get_running_loop()

    def stopping(name: str) -> None:
        log.warn("получен сигнал остановки — цикл дорабатывается, служба закрывается",
                 сигнал=name)
        stop.set()

    def handler(signum: int, _frame: Any) -> None:
        loop.call_soon_threadsafe(stopping, signal.Signals(signum).name)

    installed: list[str] = []
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError) as e:
            log.degraded("обработчик сигнала не поставлен", сигнал=name,
                         причина=f"{type(e).__name__} {e}")
            continue
        installed.append(name)
    return tuple(installed)


async def cycle(c: run.Collector, run_id: str, uni: Universe,
                horizon_days: int) -> tuple[RunReport, int]:
    """Один цикл расчёта на снимке. Возвращает отчёт цикла и число нарушений.

    Порядок шагов тот же, что у `hunter run`, и это не совпадение: расходиться им нельзя,
    иначе служба и ручной прогон считали бы по-разному, а сверять было бы нечем.

    ⚠ Каждый тяжёлый шаг уходит в рабочий поток отдельным `to_thread`. Не «весь расчёт
    одним куском» — между шагами цикл событий обязан получать управление, иначе задержка
    складывается из всех шагов сразу. Шаги строго последовательны: следующий читает
    результат предыдущего, и параллелить тут нечего.

    Все шаги работают на СНИМКЕ (`report`, `sources`), а не на живых структурах сбора.
    """
    started = clock.monotonic_ns()
    c.report.cycles += 1
    report = c.snapshot()

    # Долив архива — до сборки карточки: без него окна исторических структур не покрыты
    # и уровней не бывает вовсе. Внутри — свой `to_thread` (Д-8 и его дополнение).
    await run.backfill_trades(c.ex, uni, report, horizon_days)
    # Источники строятся на цикле: им нужен `market_id` инструмента, а рынки живут на
    # объекте биржи, который задача перечитывания меняет именно здесь.
    sources: dict[str, TradeWindows] = {
        sym: src for sym in uni.symbols
        if (src := run.trade_source(c.ex, sym, report)) is not None
    }

    await asyncio.to_thread(run.persist_frames, run_id, report)
    decided = await asyncio.to_thread(run.decide_once, report, uni, sources)
    await asyncio.to_thread(run.produce_cards, run_id, report, uni, decided)
    await asyncio.to_thread(run.persist_archive, run_id, report, sources)
    await asyncio.to_thread(run.record, run_id, report, uni, decided)

    spent = (clock.monotonic_ns() - started) / 1e9
    report.cycle_seconds = spent
    c.report.cycle_seconds = spent
    # Приёмка судит о свежести на момент СНЯТИЯ снимка, который лежит в самом отчёте:
    # часы здесь показали бы момент печати, то есть на весь расчёт позже (см. print_report).
    return report, run.print_report(report)


async def serve(uni: Universe, seed_limit: int, horizon_days: int, run_id: str,
                cycle_seconds: int = CYCLE_SECONDS, max_cycles: int = 0) -> int:
    """Боевое исполнение: собирать бесконечно, пересчитывать циклами (§8, А-1).

    `max_cycles = 0` — работать до сигнала. Ненулевое значение нужно приёмке: без него
    проверить службу можно было бы только остановив её руками, а проверка, которую нельзя
    прогнать одной командой, не прогоняется.

    Возвращает число ЦИКЛОВ С НАРУШЕНИЯМИ, а не нарушений последнего цикла. Служба живёт
    сутками, и «на выходе было чисто» — не ответ на вопрос, было ли чисто всё время.
    """
    # Сборщик создаётся ПЕРВЫМ, но сети ещё не трогает: `start()` ниже. Порядок нужен,
    # чтобы обработчики сигналов встали на его событие остановки и Ctrl+C ловился уже
    # на засеве — самой долгой части запуска (162 REST-запроса на полной вселенной).
    c = run.Collector(uni, seed_limit, keep_bars=seed_limit)
    stop_signals = _install_stop_handlers(c.stop)
    log.info("служба запускается", такт_с=cycle_seconds, циклов=max_cycles or "без предела",
             сигналы_остановки=",".join(stop_signals) or "НИ ОДНОГО")
    if not stop_signals:
        log.degraded("остановить службу сигналом НЕЛЬЗЯ — обработчики не поставлены")

    bad_cycles = 0
    try:
        await c.start()
        while not c.stop.is_set():
            report, violations = await cycle(c, run_id, uni, horizon_days)
            if violations:
                bad_cycles += 1
            log.info("цикл расчёта завершён", номер=report.cycles,
                     секунд=f"{report.cycle_seconds:.1f}", нарушений=violations,
                     циклов_с_нарушениями=bad_cycles)
            if report.cycle_seconds > cycle_seconds:
                log.warn("расчёт длиннее такта — следующий цикл начнётся без паузы",
                         такт_с=cycle_seconds, расчёт_с=f"{report.cycle_seconds:.1f}")
            if max_cycles and report.cycles >= max_cycles:
                log.info("задан предел циклов — служба останавливается",
                         циклов=report.cycles)
                break
            pause = max(0.0, cycle_seconds - report.cycle_seconds)
            if await run.sleep_or_stopped(c.stop, pause):
                break
    finally:
        # ⚠ Мгновение перед снятием задач — не вежливость, а условие полноты замера.
        # Сердцебиение фиксирует остановку цикла только ПОСЛЕ того, как та кончилась;
        # снять его сразу значит потерять последнюю остановку всегда. Живой контроль
        # 2026-08-06: без этой строки прогон с расчётом на цикле событий напечатал
        # «16 мс» при двух остановках по 25-30 с — то есть прибор молчал ровно там, где
        # был нужен. Ноль с красивой причиной опаснее всего.
        await asyncio.sleep(SETTLE_S)
        await c.shutdown()

    print()
    print("=" * 78)
    print("СЛУЖБА ОСТАНОВЛЕНА")
    print("=" * 78)
    print(f"   проработала: {c.report.uptime_s / 3600:.2f} ч "
          f"({c.report.uptime_s:.0f} с)")
    print(f"   циклов расчёта: {c.report.cycles}, из них с нарушениями: {bad_cycles}")
    print(f"   задач наблюдения умерло: {c.report.watch_deaths}, "
          f"поднято заново: {c.report.watch_restarts}")
    print(f"   наибольшая задержка цикла событий: {c.report.loop_stall_max_ms} мс "
          f"(тактов {c.report.heartbeats})")
    print(f"   сделок принято: {c.report.trades_total}, потеряно потоком: "
          f"{c.report.trade_gaps} (номеров сверено {c.report.trade_ids_checked})")
    if c.report.trade_gaps:
        print(f"   из потерянного добрано REST-догоном: {c.report.trade_gaps_recovered}, "
              f"осталось: {c.report.trade_gaps_unrecovered}")
    print("=" * 78)
    return bad_cycles
