"""Точка входа. FOUNDATION.md §8 этап 1."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

from . import clock, log, store
from .config import DEFAULT_PATH, Universe, load_universe


def _run(args: argparse.Namespace) -> int:
    uni = load_universe(args.universe)
    if args.symbols:
        uni = Universe(uni.symbols[: args.symbols], uni.timeframes, uni.source)
    from .run import live_run, print_report

    report = asyncio.run(live_run(uni, args.seconds, args.seed_limit, args.run_id))
    return 1 if print_report(report, clock.now_ms()) else 0


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
                counts = {tf: await ex.count_history(sym, tf) for tf in uni.timeframes}
                out.append((sym, counts))
            return out
        finally:
            await ex.close()

    rows = asyncio.run(survey())
    tfs = list(uni.timeframes)
    print(f"ДОПУСК: порог {required} баров на каждом ТФ")
    print(f"Требования замерены: {REQUIRED_BARS} (docs/audit/wilder-reference-2026-08-03.md)")
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


def main(argv: list[str] | None = None) -> int:
    log.configure()
    p = argparse.ArgumentParser(prog="hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="живой прогон и сводка приёмки")
    run.add_argument("--seconds", type=int, default=90)
    run.add_argument("--seed-limit", type=int, default=500)
    run.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    run.add_argument("--run-id", default="last")
    run.add_argument("--symbols", type=int, default=0,
                     help="взять только первые N символов вселенной")

    adm = sub.add_parser("admission", help="хватает ли истории на величины §2.9")
    adm.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    adm.add_argument("--required", type=int, default=0,
                     help="порог баров на каждом ТФ; 0 = самое строгое замеренное")

    led = sub.add_parser("ledger", help="три проверочных запроса к леджеру (§10.6)")
    led.add_argument("--init", action="store_true", help="создать базу со схемой")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "admission":
        return _admission(args)
    if args.cmd == "ledger":
        return _ledger(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
