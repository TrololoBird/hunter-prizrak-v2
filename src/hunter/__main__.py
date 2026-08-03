"""Точка входа. FOUNDATION.md §8 этап 1."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import DEFAULT_PATH, load_universe
from .run import live_run, print_report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="живой прогон и сводка приёмки")
    run.add_argument("--seconds", type=int, default=90)
    run.add_argument("--seed-limit", type=int, default=500)
    run.add_argument("--universe", type=Path, default=DEFAULT_PATH)
    run.add_argument("--symbols", type=int, default=0,
                     help="взять только первые N символов вселенной (для быстрой проверки)")

    args = p.parse_args(argv)
    if args.cmd == "run":
        uni = load_universe(args.universe)
        if args.symbols:
            uni = type(uni)(uni.symbols[: args.symbols], uni.timeframes, uni.source)
        report = asyncio.run(live_run(uni, args.seconds, args.seed_limit))
        violations = print_report(uni, report)
        return 1 if violations else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
