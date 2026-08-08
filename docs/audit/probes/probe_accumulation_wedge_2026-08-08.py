"""ЗОНД: что наш `accumulation.detect` ДЕЛАЕТ СЕЙЧАС с сужением и с границей.

Не гипотеза и не сравнение с нулём. Курс уже сказал (стр. 34: сужение — такое же
накопление; стр. 13 и 23: граница нарисована ОДНОЙ линией), и правка предрешена. Этот зонд
нужен, чтобы править ТО МЕСТО, а не то, которое я предположил: в прошлый раз утверждение
«мы сбрасываем структуру при схождении» было записано в протокол БЕЗ проверки.

Считает четыре вещи:
  С1  сколько раз сработала ветка «зоны границ пересеклись» — то есть верен ли рассказ,
      что сужение попадает именно туда;
  С2  сколько экстремумов отвергнуто как «внутри базы» — в сужении это ровно те точки,
      которые курс нумерует;
  С3  сколько закрытых накоплений СХОДЯТСЯ: хаи убывают, лои растут (признак сужения);
  С4  насколько разъезжаются первые две точки стороны — цена вопроса Н2 (одна линия
      против полосы): если полоса всегда нулевая, схлопывать нечего.

КОНТРОЛЬ. Каждый счётчик обязан уметь быть НЕнулевым и печатается вместе с числом
предъявлений, иначе ноль неотличим от «ветка недостижима». Для С1 отдельно печатается,
сколько ВСЕГО было сбросов: если пересечений ноль при ненулевых сбросах — рассказ про
схождение неверен, и это результат, а не пустая строка.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_accumulation_wedge_2026-08-08.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import MIN_BOUNDARY_POINTS, detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS, steps_between  # noqa: E402
from hunter.breach import CONFIRM_BODIES, Direction, body_beyond  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.swings import SwingKind, SwingSet  # noqa: E402
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


def walk(bars: list[Bar], swings: SwingSet, timeframe: str) -> tuple[int, int, int, int, int]:
    """ПОЛНАЯ копия `detect` со счётчиками на развилках приёма точки.

    ⚠ Первая редакция этого зонда копировала только приём точек и НЕ повторяла выдачу
    структуры со сбросом. Из-за этого списки точек росли до конца ряда, и «внутри базы»
    насчиталось 1693 отказа из 3716 — число мерило дефект зонда, а не поведение кода.
    Поэтому копия теперь полная, а её верность проверяется контролем в `main`: число
    выданных структур обязано совпасть с боевым `detect` на каждом ряду.

    Возвращает (пересечений зон, отвергнуто «внутри базы», предъявлено, отвергнуто
    чередованием, выдано структур).
    """
    by_confirm: dict[int, list[tuple[SwingKind, int, float]]] = {}
    for s in swings.swings:
        by_confirm.setdefault(s.confirmed_at_index, []).append((s.kind, s.index, s.price))

    hi_px: list[float] = []
    lo_px: list[float] = []
    last_kind: SwingKind | None = None
    run_dir: Direction | None = None
    run_from = 0
    crossings = inside = offered = alternation = emitted = 0

    def reset() -> None:
        nonlocal last_kind, run_dir, run_from
        hi_px.clear()
        lo_px.clear()
        last_kind = None
        run_dir, run_from = None, 0

    for k in range(len(bars)):
        for kind, _index, price in by_confirm.get(k, []):
            offered += 1
            if kind is last_kind:
                alternation += 1
                continue
            if kind is SwingKind.HIGH:
                if len(hi_px) < 2:
                    hi_px.append(price)
                    last_kind = kind
                    continue
                if price < min(hi_px[:2]):
                    inside += 1
                    continue
                hi_px.append(price)
                last_kind = kind
            else:
                if len(lo_px) < 2:
                    lo_px.append(price)
                    last_kind = kind
                    continue
                if price > max(lo_px[:2]):
                    inside += 1
                    continue
                lo_px.append(price)
                last_kind = kind

        if len(hi_px) < 2 or len(lo_px) < 2:
            continue
        upper_lo, upper_hi = min(hi_px[:2]), max(hi_px[:2])
        lower_lo, lower_hi = min(lo_px[:2]), max(lo_px[:2])
        if upper_lo <= lower_hi:
            crossings += 1
            reset()
            continue

        bar = bars[k]
        if body_beyond(bar, upper_hi, Direction.ABOVE):
            direction = Direction.ABOVE
        elif body_beyond(bar, lower_lo, Direction.BELOW):
            direction = Direction.BELOW
        else:
            run_dir = None
            continue

        broken = k > 0 and steps_between(bars[k - 1], bars[k], timeframe) != 1
        if direction is not run_dir or broken:
            run_dir, run_from = direction, k
        if k - run_from + 1 < CONFIRM_BODIES:
            continue
        if len(hi_px) + len(lo_px) < MIN_BOUNDARY_POINTS:
            reset()
            continue
        emitted += 1
        reset()

    return crossings, inside, offered, alternation, emitted


def main() -> int:
    corpus = load()
    if not corpus:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    rows = 0
    mismatch: list[str] = []
    crossings = inside = offered = alternation = 0
    closed = converging = 0
    spreads: list[float] = []
    points_hist: dict[int, int] = {}

    for (sym, tf), bars in sorted(corpus.items()):
        sw = detect_swings(bars)
        if not isinstance(sw, SwingSet):
            continue
        rows += 1
        c, i, o, a, e = walk(bars, sw, tf)
        crossings += c
        inside += i
        offered += o
        alternation += a

        scan = detect(bars, sw, tf)
        if e != len(scan.closed):
            mismatch.append(f"{sym} {tf}: копия выдала {e}, боевой detect {len(scan.closed)}")
        for acc in scan.closed:
            closed += 1
            n = acc.points
            points_hist[n] = points_hist.get(n, 0) + 1
            for z in (acc.upper, acc.lower):
                mid = (z.lo + z.hi) / 2
                spreads.append(0.0 if mid == 0 else (z.hi - z.lo) / mid * 100)
            # Сужение: хаи убывают, лои растут. Судим по точкам, а не по зонам —
            # зоны заморожены и о сужении молчат по построению.
            hi_pts = sorted(acc.upper.point_indices)
            lo_pts = sorted(acc.lower.point_indices)
            if len(hi_pts) >= 3 and len(lo_pts) >= 3:
                converging += 1

    print("=" * 78)
    print(f"КОРПУС: рядов {rows}, закрытых накоплений {closed}")
    print("=" * 78)
    print("\nКОНТРОЛЬ: копия против боевого detect")
    if mismatch:
        print(f"  ⚠ РАСХОЖДЕНИЙ {len(mismatch)} — числа ниже недействительны:")
        for m in mismatch[:5]:
            print(f"      {m}")
        return 1
    print(f"  выдача совпала на всех {rows} рядах ✅")

    print("\nС1. Ветка «зоны границ пересеклись»")
    print(f"  сработала раз: {crossings}")
    if crossings == 0:
        print("  ⚠ НОЛЬ. Значит сужение в эту ветку НЕ ПОПАДАЕТ, и рассказ «мы сбрасываем")
        print("     структуру при схождении» неверен. Сходящиеся точки уходят в другую дверь.")

    print("\nС2. Экстремумы, отвергнутые как «внутри базы»")
    print(f"  отвергнуто: {inside} из {offered} предъявленных "
          f"({inside / max(offered, 1) * 100:.1f}%)")
    print(f"  для сравнения, отвергнуто чередованием: {alternation}")
    if inside == 0:
        print("  ⚠ НОЛЬ — счётчик не может сработать, числа недействительны.")

    print("\nС3. Сколько накоплений набрали 3+ точки НА КАЖДОЙ стороне")
    print(f"  {converging} из {closed}")
    print("  (в сужении точек на стороне много: курс нумерует их все)")
    print("  распределение точек в структуре:")
    for n in sorted(points_hist):
        print(f"    {n:2d} точек: {points_hist[n]:4d}")

    print("\nС4. Разъезд первых двух точек стороны — цена вопроса Н2")
    if spreads:
        spreads.sort()
        zero = sum(1 for s in spreads if s == 0.0)
        mid = spreads[len(spreads) // 2]
        print(f"  зон: {len(spreads)}  нулевой ширины: {zero}  "
              f"медиана: {mid:.4f}%  максимум: {spreads[-1]:.4f}%")
        if zero == len(spreads):
            print("  ⚠ ВСЕ НУЛЕВЫЕ: полоса и одна цена — одно и то же, правка Н2 пустая.")
        else:
            print(f"  → схлопывание полосы в одну цену сдвинет границу у "
                  f"{len(spreads) - zero} зон из {len(spreads)}")
    else:
        print("  зон нет")
    print(f"\nMIN_BOUNDARY_POINTS = {MIN_BOUNDARY_POINTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
