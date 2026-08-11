"""Точка входа. FOUNDATION.md §8 этап 1."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

from . import log, replay, service, store
from .config import DEFAULT_PATH, Universe, load_universe
from .models import NotReady


def _run(args: argparse.Namespace) -> int:
    uni = load_universe(args.universe)
    if args.symbols:
        uni = Universe(uni.symbols[: args.symbols], uni.timeframes, uni.source)
    from . import log
    from .run import (
        collect,
        decide_once,
        persist_archive,
        persist_frames,
        print_report,
        produce_cards,
        record,
    )

    # ЧЕТЫРЕ ШАГА, и они видны здесь, а не спрятаны друг в друге. Раньше `live_run`
    # делал всё, причём карточку строил ВНУТРИ `persist` — сбор данных и производство
    # сигнала в одном вызове. Именно из такой слипшейся точки в прошлом проекте вырос
    # `orchestrator.py` на 2894 строки; разделено до реализации §2.4-2.7, пока дёшево.
    report, sources = asyncio.run(
        collect(uni, args.seconds, args.seed_limit, args.horizon_days)
    )
    persist_frames(args.run_id, report)
    # СИГНАЛ СЧИТАЕТСЯ ОДИН РАЗ и отдаётся обоим потребителям — карточке и леджеру.
    # До 2026-08-06 каждый считал его сам, и они расходились: карточка печатала
    # геометрию для 94 уровней, леджер эмитировал 33 (замер на кадрах прогона `a1`).
    decided = decide_once(report, uni, sources)
    produce_cards(args.run_id, report, uni, decided)
    # Срез архива кладётся ПОСЛЕ карточек: до них неизвестно, какие сутки понадобились.
    # Без него повтор читает общий кэш и объявляет «расчёт изменился» на доливке (Н-6).
    persist_archive(args.run_id, report, sources)
    record(args.run_id, report, uni, decided)
    log.info("кадры сохранены", файлов=report.frames_written, карточек=report.cards_written)
    return 1 if print_report(report) else 0


def _serve(args: argparse.Namespace) -> int:
    """Боевое исполнение: служба 24/7 (§8, находка А-1).

    Отличие от `run` не в длительности, а в устройстве: `run` собирает окно и считает
    один раз, служба собирает непрерывно и считает циклами, не останавливая сбор.
    """
    from .service import serve

    uni = load_universe(args.universe)
    if args.symbols:
        uni = Universe(uni.symbols[: args.symbols], uni.timeframes, uni.source)
    bad = asyncio.run(serve(uni, args.seed_limit, args.horizon_days, args.run_id,
                            cycle_seconds=args.cycle_seconds, max_cycles=args.cycles))
    return 1 if bad else 0


def _check(args: argparse.Namespace) -> int:
    """Единая команда владельца (§7.5, поправка 2026-08-03)."""
    from .check import run_check

    uni = load_universe(args.universe)
    if args.symbols:
        uni = Universe(uni.symbols[: args.symbols], uni.timeframes, uni.source)
    return 1 if run_check(uni, args.seconds, args.seed_limit) else 0


def _profile(args: argparse.Namespace) -> int:
    """Профиль объёма за сутки: ПОК, VAH, VAL (§2.2, §5).

    ⚠ Переписано 2026-08-11 вместе с удалением `data.binance.vision`. Раньше команда
    качала суточный ZIP архива; теперь сутки берутся тем же путём, что и в бою, — REST-ом
    ccxt. Диагностика обязана ходить туда же, куда боевой расчёт, иначе она проверяет
    не то, что работает.
    """
    import datetime as dt

    from .exchange import Exchange
    from .models import NotReady, RawTrade, TradeHistogram, bin_index
    from .volume_profile import Expansion, build

    day = dt.date.fromisoformat(args.day)
    start = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    end = start + 86_400_000

    async def day_histogram() -> tuple[Decimal, TradeHistogram] | NotReady:
        ex = Exchange()
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
    from .admission import REQUIRED_BARS, admits, unavailable_quantities
    from .exchange import Exchange

    uni = load_universe(args.universe)
    required = args.required or max(REQUIRED_BARS.values())

    async def survey() -> list[tuple[str, dict[str, int]]]:
        ex = Exchange()
        await ex.open()
        try:
            out: list[tuple[str, dict[str, int]]] = []
            for sym in uni.symbols:
                counts = {tf: await ex.count_history(sym, tf, cap=required)
                          for tf in uni.timeframes}
                out.append((sym, counts))
            return out
        finally:
            await ex.close()

    rows = asyncio.run(survey())
    tfs = list(uni.timeframes)
    print(f"ДОПУСК: порог {required} баров на каждом ТФ")
    print(f"Требования замерены: {REQUIRED_BARS} (docs/audit/wilder-reference-2026-08-03.md)")
    print()
    print(f"(счёт с отсечкой на {required}: большее значение означает «не меньше»)")
    print()
    head = f"{'символ':22}" + "".join(f"{tf:>8}" for tf in tfs) + "  допуск  недостаёт"
    print(head)
    passed: list[str] = []
    failed: list[str] = []
    for sym, counts in sorted(rows, key=lambda x: min(x[1].values())):
        ok, short = admits(counts, required)
        line = f"{sym:22}" + "".join(f"{counts[tf]:>8}" for tf in tfs)
        line += f"  {'ДА ' if ok else 'НЕТ'}    {','.join(short) if short else '—'}"
        print(line)
        (passed if ok else failed).append(sym)
    print()
    print(f"проходят: {len(passed)} из {len(rows)}   не проходят: {len(failed)}")
    if failed:
        print(f"не проходят: {', '.join(failed)}")
    print()
    print("Что именно недоступно у непрошедших (по самому старшему ТФ):")
    for sym, counts in rows:
        top = tfs[-1]
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
    finally:
        conn.close()
    return 0


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
    run.add_argument("--seed-limit", type=int, default=500)
    run.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    run.add_argument("--run-id", default="last")
    # ⚠ Раньше здесь было `--trade-days` = «сколько ПОСЛЕДНИХ суток скачать», умолчание 3.
    # Ручка задавала не глубину архива, а то, БУДЕТ ЛИ уровень вообще: при 3 сутках его
    # мог получить 15% структур, а на 4ч и 1Д — ноль из 28 и 21. Теперь набор суток
    # выводится из окон структур (`run.needed_days`), а этот параметр задаёт только
    # ГОРИЗОНТ: насколько старые структуры ещё считаются картой (стр. 25).
    run.add_argument("--horizon-days", type=int, default=90,
                     help="структуры, вышедшие раньше чем N суток назад, в карту не идут; "
                          "0 = не доливать сделки вовсе")
    run.add_argument("--symbols", type=int, default=0,
                     help="взять только первые N символов вселенной")

    srv = sub.add_parser("serve", help="СЛУЖБА 24/7: сбор без остановки, расчёт циклами")
    srv.add_argument("--cycle-seconds", type=int, default=service.CYCLE_SECONDS,
                     help="такт расчёта; умолчание — младший ТФ проекта (§2.8)")
    srv.add_argument("--cycles", type=int, default=0,
                     help="остановиться после N циклов; 0 = работать до сигнала")
    srv.add_argument("--seed-limit", type=int, default=500)
    srv.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    srv.add_argument("--run-id", default="serve",
                     help="куда класть кадры и карточки; каждый цикл ПЕРЕЗАПИСЫВАЕТ их")
    srv.add_argument("--horizon-days", type=int, default=90)
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

    adm = sub.add_parser("admission", help="хватает ли истории на величины §2.9")
    adm.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    adm.add_argument("--required", type=int, default=0,
                     help="порог баров на каждом ТФ; 0 = самое строгое замеренное")

    led = sub.add_parser("ledger", help="три проверочных запроса к леджеру (§10.6)")
    led.add_argument("--init", action="store_true", help="создать базу со схемой")

    sub.add_parser("bot", help="ТЕЛЕГРАМ-БОТ доставки: тикер → скриншоты карты + сводка")

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
    if args.cmd == "bot":
        from .tgbot import main as bot_main
        return asyncio.run(bot_main())
    return 2


if __name__ == "__main__":
    sys.exit(main())
