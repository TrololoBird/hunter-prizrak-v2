"""ЗОНД Н6: цена выбора внутри диапазона курса «2-3 полных тел свечей ЭТОГО ТФ» (стр. 55).

Не гипотеза против нуля. Курс даёт ДИАПАЗОН и внутри него не выбирает, поэтому и два тела,
и три — исполнение одного и того же требования. Замер нужен, чтобы владелец выбирал по
числу, а не по умолчанию: `breach.CONFIRM_BODIES` прямо говорит, что выбор открыт.

Что считается:
  Б1  сколько закрытых накоплений при 2 телах и при 3;
  Б2  сколько структур СОВПАДАЮТ побитово (те же границы, точки, бар выхода);
  Б3  на сколько баров позже подтверждается выход при трёх телах;
  Б4  сколько структур ИСЧЕЗАЮТ при трёх телах — то есть цена не удержалась третьей свечой.

КОНТРОЛЬ. Прибор обязан уметь ответить «разницы нет»: если при 2 и 3 телах разметка
совпала полностью, это печатается прямо и означает, что вопрос пустой. Ноль различий —
законный ответ, но он должен быть НАЗВАН, а не выглядеть отсутствием строки.

⚠ Проверка ДРУГОЙ стороны стр. 55 — про возврат той же или следующей свечой, который курс
называет проколом без подтверждения, — здесь не нужна: требование двух ПОЛНЫХ тел его
выполняет по
построению, потому что ни первая, ни вторая свеча внутрь не вернулись. Это рассуждение, а
не замер, и оно помечено как рассуждение.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_accumulation_bodies_2026-08-08.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import Accumulation, detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.swings import SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402


def key(a: Accumulation) -> str:
    return (f"{a.timeframe}|{a.first_index}|{a.last_index}"
            f"|{a.upper.edge:.10g}|{a.lower.edge:.10g}"
            f"|{a.upper.point_indices}|{a.lower.point_indices}"
            f"|{a.exit.direction.value}|{a.exit.confirmed_at_index}")


def start(a: Accumulation) -> str:
    """Ключ БЕЗ бара подтверждения — чтобы поймать «та же структура, выход позже»."""
    return (f"{a.timeframe}|{a.first_index}"
            f"|{a.upper.edge:.10g}|{a.lower.edge:.10g}|{a.exit.direction.value}")


def main() -> int:
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    rows = 0
    two: list[Accumulation] = []
    three: list[Accumulation] = []
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run.iterdir()):
            if not (sym_dir / "meta.json").exists():
                continue
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                bars = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                            high=float(r["high"]), low=float(r["low"]),
                            close=float(r["close"]), volume=float(r["volume"]))
                        for r in pl.read_parquet(f).iter_rows(named=True)]
                sw = detect_swings(bars)
                if not isinstance(sw, SwingSet):
                    continue
                rows += 1
                two.extend(detect(bars, sw, tf, confirm_bodies=2).closed)
                three.extend(detect(bars, sw, tf, confirm_bodies=3).closed)

    k2, k3 = {key(a) for a in two}, {key(a) for a in three}
    s2 = {start(a): a for a in two}
    s3 = {start(a): a for a in three}
    later = [s3[s].exit.confirmed_at_index - s2[s].exit.confirmed_at_index
             for s in s2.keys() & s3.keys()]
    vanished = len(s2.keys() - s3.keys())
    appeared = len(s3.keys() - s2.keys())

    print("=" * 78)
    print(f"КОРПУС: рядов {rows}")
    print("=" * 78)
    print(f"\nБ1  закрытых накоплений при 2 телах: {len(two)}")
    print(f"    закрытых накоплений при 3 телах: {len(three)}")
    print(f"\nБ2  та же структура с тем же баром выхода: {len(k2 & k3)}")
    print("    ⚠ Это число НЕ ИЗМЕРЕНИЕ: полный ключ содержит бар подтверждения, а он при")
    print("      росте порога сдвигается у КАЖДОЙ дожившей структуры по построению. Ноль")
    print("      здесь неизбежен и о рынке ничего не говорит. Первая редакция зонда подавала")
    print("      его как результат — строка оставлена вместе с разоблачением.")
    print(f"    ПО СУЩЕСТВУ: та же структура (границы, начало, сторона выхода) есть в обеих "
          f"разметках у {len(s2.keys() & s3.keys())} из {len(s2)}")
    if s2.keys() == s3.keys():
        print("    ⚠ НАБОР СТРУКТУР СОВПАЛ — выбор между 2 и 3 меняет только момент")
        print("      подтверждения. Это законный ответ, и он назван.")
    print(f"\nБ3  структур, доживших до третьего тела: {len(later)}")
    if later:
        later.sort()
        print(f"    подтверждение позже на: медиана {later[len(later) // 2]} бар(а), "
              f"максимум {later[-1]}")
    print(f"\nБ4  структур ИСЧЕЗЛО при трёх телах: {vanished} "
          f"({vanished / max(len(s2), 1) * 100:.1f}%)")
    print(f"    структур ПОЯВИЛОСЬ при трёх телах: {appeared}")
    print("\n(исчезли — те, где цена не удержалась за границей третьей свечой;")
    print(" появились — те, чей более ранний выход при 2 телах закрыл структуру раньше)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
