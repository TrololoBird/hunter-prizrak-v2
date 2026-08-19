"""Цена правки окна композита — по СТРОКАМ карточки, как их видит владелец."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import card, engine, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.profile_source import TVWindows  # noqa: E402

COMP = re.compile(r"сила по композиту .*?зона ([\d.eE+-]+) — ([\d.]+)% композита")
NOTE = re.compile(r"композит .*не прочитан|композит не построен")


def survey(text: str) -> dict[str, int]:
    out = {"строк композита": 0, "из них 0.0%": 0, "из них РОВНЫЙ ноль": 0,
           "отказов": 0}
    for line in text.splitlines():
        m = COMP.search(line)
        if m:
            out["строк композита"] += 1
            if float(m.group(2)) == 0.0:
                out["из них 0.0%"] += 1
                if float(m.group(1)) == 0.0:
                    out["из них РОВНЫЙ ноль"] += 1
        elif NOTE.search(line):
            out["отказов"] += 1
    return out


def main() -> int:
    run_id = "last"
    was = {k: 0 for k in ("строк композита", "из них 0.0%", "из них РОВНЫЙ ноль", "отказов")}
    now = dict(was)
    shares_now: list[float] = []
    for d in store.saved_symbols(run_id):
        saved = store.read_card(run_id, d)
        if not isinstance(saved, NotReady):
            for k, v in survey(saved).items():
                was[k] += v
        meta = store.read_meta(run_id, d)
        if isinstance(meta, NotReady):
            continue
        symbol, tick, _ = meta
        tfs = store.saved_timeframes(run_id, d)
        series = {tf: store.read_bars(run_id, d, tf) for tf in tfs}
        profile_series = dict(series)
        profile_series.update(store.read_profile_bars(run_id, d))
        src = TVWindows(symbol, tick, profile_series)
        text = card.render(engine.decide(symbol, series, src, tfs), series)
        for k, v in survey(text).items():
            now[k] += v
        for m in COMP.finditer(text):
            shares_now.append(float(m.group(2)))

    print("КОНТРОЛЬ — сохранённые карточки (окно самой длинной структуры):")
    for k, v in was.items():
        print(f"  {k}: {v}")
    print("\nПЕРЕСЧЁТ — те же кадры, окно видимой области:")
    for k, v in now.items():
        print(f"  {k}: {v}")
    if shares_now:
        shares_now.sort()
        n = len(shares_now)
        print(f"\nдоли композита после правки: медиана {shares_now[n // 2]:.2f}%, "
              f"максимум {shares_now[-1]:.2f}%, нулевых {sum(1 for x in shares_now if x == 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
