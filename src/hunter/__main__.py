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

    led = sub.add_parser("ledger", help="три проверочных запроса к леджеру (§10.6)")
    led.add_argument("--init", action="store_true", help="создать базу со схемой")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "ledger":
        return _ledger(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
