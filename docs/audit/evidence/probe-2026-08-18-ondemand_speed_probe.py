"""Секундомер сборки по запросу: тот же путь, что у бота (collect + decide_once, frame_bars)."""
import asyncio
import time
from dataclasses import replace

from hunter import run
from hunter.__main__ import HORIZON_DAYS
from hunter.config import load_universe
from hunter.tgbot import BARS_ON_CHART

SYMBOL = "ONDO/USDT:USDT"


async def main() -> None:
    uni = load_universe()
    one = replace(uni, symbols=(SYMBOL,))
    t0 = time.perf_counter()
    report, sources = await run.collect(one, 0, 0, HORIZON_DAYS, frame_bars=BARS_ON_CHART)
    t1 = time.perf_counter()
    decided = await asyncio.to_thread(run.decide_once, report, one, sources, BARS_ON_CHART)
    t2 = time.perf_counter()
    card = decided.get(SYMBOL)
    lines = 0 if card is None else len(card.splitlines()) if isinstance(card, str) else -1
    print(f"collect: {t1 - t0:.1f} c")
    print(f"decide:  {t2 - t1:.1f} c")
    print(f"итого:   {t2 - t0:.1f} c")
    print("окон_старших_тф:", report.profile_windows_senior_tf,
          "окон_вне_кадра:", report.profile_windows_out_of_frame)
    print("карточка:", type(card).__name__, lines)


asyncio.run(main())
