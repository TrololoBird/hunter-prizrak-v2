"""ЗОНД: два вопроса о проколе против пробоя, оставшиеся после обзора 7 чужих реализаций.

Допуск и пороги зафиксированы ДО прогона: docs/audit/tolerance-breach.md,
sha256 b338f5df062ee2f866eaa2198f4395c637b13b226fc17ac72fb3a3c952912b36.

Расчёт НЕ меняется: альтернативное определение возврата считается рядом с текущим.

  Р1  распределение ГЛУБИНЫ захода за уровень в долях ATR — нужен ли порог глубины
      (два чужих проекта его имеют, у нас нет вовсе)
  Р2  что считать ВОЗВРАТОМ: бар целиком по эту сторону (наш) против ЗАКРЫТИЯ по эту
      сторону (4 проекта из 7)

КОНТРОЛИ:
  * Р1 — нуль: решётка произвольных равноотстоящих цен вместо уровней проекта. Проверяется,
    что нуль ломает свойство (число событий обязано отличаться).
  * Р2 — К-1: подсаженный бар, закрывающийся по эту сторону, но задевающий уровень тенью,
    обязан развести два определения.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_breach_questions_2026-08-07.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS, steps_between  # noqa: E402
from hunter.breach import (  # noqa: E402
    CONFIRM_BODIES,
    RETURN_BARS,
    BreachKind,
    Direction,
    body_beyond,
    first_breach,
)
from hunter.models import Bar  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402


def load() -> dict[tuple[str, str], list[Bar]]:
    out: dict[tuple[str, str], list[Bar]] = {}
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            sym = json.loads(mp.read_text(encoding="utf-8"))["symbol"]
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in pl.read_parquet(f).iter_rows(named=True)]
                if len(bars) > len(out.get((sym, tf), [])):
                    out[(sym, tf)] = bars
    return out


def atr14(bars: list[Bar]) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(bars[i].high - bars[i].low,
               abs(bars[i].high - bars[i - 1].close),
               abs(bars[i].low - bars[i - 1].close)) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def levels_of(bars: list[Bar]) -> list[tuple[float, Direction]]:
    """Уровни для замера — цены свинговых экстремумов ряда."""
    sw = detect_swings(bars)
    if not hasattr(sw, "swings"):
        return []
    return [(s.price, Direction.ABOVE if s.kind.value == "high" else Direction.BELOW)
            for s in sw.swings]


def breach_close_return(
    bars: list[Bar], level: float, direction: Direction, timeframe: str, *, from_index: int
) -> BreachKind | None:
    """ТА ЖЕ логика, но ВОЗВРАТ считается по ЗАКРЫТИЮ по эту сторону уровня.

    Копия `first_breach` с одним изменённым условием — иначе сравнивать было бы нечего.
    Отличие ровно одно и оно отмечено комментарием.
    """
    start: int | None = None
    run = 0
    for i in range(from_index, len(bars)):
        bar = bars[i]
        if i > from_index and steps_between(bars[i - 1], bar, timeframe) != 1:
            run = 0
        beyond = bar.high > level if direction is Direction.ABOVE else bar.low < level
        # ⚠ ЕДИНСТВЕННОЕ ОТЛИЧИЕ: возврат — по закрытию, а не по всему бару.
        returned = (bar.close <= level if direction is Direction.ABOVE
                    else bar.close >= level)
        if beyond and not returned:
            if start is None:
                start, run = i, 0
            run = run + 1 if body_beyond(bar, level, direction) else 0
            if run >= CONFIRM_BODIES:
                return BreachKind.BREAKOUT
            continue
        if start is None:
            continue
        back = steps_between(bars[start], bar, timeframe)
        return BreachKind.PUNCTURE if back <= RETURN_BARS else BreachKind.UNRESOLVED
    return BreachKind.OPEN if start is not None else None


def main() -> int:
    data = load()
    if not data:
        print("КАДРОВ НЕТ — НЕ СМОГ ПРОВЕРИТЬ")
        return 1
    print("=" * 78)
    print("Прокол против пробоя: два вопроса после обзора 7 чужих реализаций")
    print("=" * 78)
    print(f"кадров: {len(data)} рядов")
    print()

    depths: list[float] = []
    grid_depths: list[float] = []
    n_real = n_grid = 0
    differ = 0
    total = 0
    by_kind: dict[str, int] = defaultdict(int)
    by_kind_alt: dict[str, int] = defaultdict(int)
    rows_changed = 0

    for (sym, tf), bars in sorted(data.items()):
        a = atr14(bars)
        if a is None or a <= 0:
            continue
        lv = levels_of(bars)
        if not lv:
            continue
        changed_here = 0
        for price, direction in lv:
            ev = first_breach(bars, price, direction, tf)
            if ev is None:
                continue
            n_real += 1
            total += 1
            by_kind[ev.kind.value] += 1
            depths.append(abs(ev.extreme - price) / a)
            alt = breach_close_return(bars, price, direction, tf, from_index=0)
            by_kind_alt[alt.value if alt else "none"] += 1
            if alt is None or alt is not ev.kind:
                differ += 1
                changed_here += 1
        if changed_here:
            rows_changed += 1
        # НУЛЬ: решётка произвольных равноотстоящих цен, столько же и в том же диапазоне
        lo = min(b.low for b in bars)
        hi = max(b.high for b in bars)
        n = len(lv)
        grid = [lo + (hi - lo) * i / max(n - 1, 1) for i in range(n)]
        for k, price in enumerate(grid):
            direction = Direction.ABOVE if k % 2 == 0 else Direction.BELOW
            ev = first_breach(bars, price, direction, tf)
            if ev is None:
                continue
            n_grid += 1
            grid_depths.append(abs(ev.extreme - price) / a)

    print("--- Р1. Глубина захода за уровень (нужен ли порог) ---")
    if not depths:
        print("  событий нет — НЕ СМОГ ПРОВЕРИТЬ")
        return 1
    shallow = sum(1 for d in depths if d < 0.1)
    share = shallow / len(depths) * 100
    print(f"  событий на уровнях проекта: {len(depths)}")
    print(f"  глубина в ATR: медиана {statistics.median(depths):.3f}, "
          f"минимум {min(depths):.4f}, максимум {max(depths):.2f}")
    print(f"  мельче 0.1 ATR: {shallow} ({share:.1f}%)")
    print("  порог: <5 закрыть · 5-25 записать · >=25 предъявить")
    print(f"  ВЕРДИКТ: {'ЗАКРЫТЬ' if share < 5 else 'ЗАПИСАТЬ' if share < 25 else 'ПРЕДЪЯВИТЬ'}")
    print()
    print("  НУЛЬ (решётка произвольных цен):")
    print(f"    событий: {n_grid} против {n_real} на уровнях "
          f"{'✓ нуль ломает свойство' if n_grid != n_real else '⚠ ЧИСЛО СОБЫТИЙ ТО ЖЕ — нуль не ломает'}")
    if grid_depths:
        gshallow = sum(1 for d in grid_depths if d < 0.1) / len(grid_depths) * 100
        print(f"    глубина в ATR: медиана {statistics.median(grid_depths):.3f}; "
              f"мельче 0.1 ATR {gshallow:.1f}% (на уровнях {share:.1f}%)")
        print(f"    {'✗ распределения совпали — глубина мерит волатильность, не уровень' if abs(gshallow - share) < 3 else '✓ на уровнях иначе, чем на произвольных ценах'}")
    print()

    print("--- Р2. Что считать возвратом: бар целиком (наш) против закрытия ---")
    d2 = differ / total * 100 if total else 0.0
    print(f"  событий: {total}; исход РАЗЛИЧАЕТСЯ: {differ} ({d2:.1f}%)")
    print(f"  рядов, где хоть что-то изменилось: {rows_changed} из {len(data)}")
    print(f"  наш:  {dict(sorted(by_kind.items()))}")
    print(f"  их:   {dict(sorted(by_kind_alt.items()))}")
    print("  порог: <5 закрыть · 5-25 записать · >=25 предъявить")
    print(f"  ВЕРДИКТ: {'ЗАКРЫТЬ' if d2 < 5 else 'ЗАПИСАТЬ' if d2 < 25 else 'ПРЕДЪЯВИТЬ'}")
    print()

    print("=" * 78)
    print("КОНТРОЛЬ К-1 для Р2: бар закрывается внутри, но задевает уровень тенью")
    S = 300_000
    probe = [Bar(open_ms=i * S, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0)
             for i in range(6)]
    # Бар 1 уходит за уровень 103 и закрывается за ним. Бар 2 закрывается ВНУТРИ (100),
    # но тенью всё ещё выходит за уровень (high 104.2) — на нём определения и расходятся.
    # ⚠ Первая редакция строила бар с high < open, и его отверг валидатор `Bar`
    # («high/low не накрывают open/close»). Контракт сработал раньше, чем зонд успел
    # соврать: невозможный бар дал бы бессмысленный контроль.
    probe[1] = Bar(open_ms=S, open=100.0, high=105.0, low=99.5, close=104.0, volume=1.0)
    probe[2] = Bar(open_ms=2 * S, open=103.8, high=104.2, low=99.0, close=100.0, volume=1.0)
    ours = first_breach(probe, 103.0, Direction.ABOVE, "5m")
    theirs = breach_close_return(probe, 103.0, Direction.ABOVE, "5m", from_index=0)
    print(f"  наш исход: {ours.kind.value if ours else 'нет'}; "
          f"их исход: {theirs.value if theirs else 'нет'}")
    print(f"  {'✓ определения разводятся' if (ours and theirs and ours.kind is not theirs) else '⚠ на этом баре совпали — контроль слабый, смотреть на замер выше'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
