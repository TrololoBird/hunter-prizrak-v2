"""ЗОНД: правда ли, что стороны базы «застревают на двух точках» и что структур стало +57%.

ПОВОД. Критический разбор 2026-08-08 (агент) выдал числа, которых я не проверял:
    сторон, застрявших на ДВУХ точках: 11705 из 16690 (70%) в ветке против 4112 из 10602
    (39%) в main; структур 8345 против 5301 (+57%); проколов 4272 против 4502.
    «Порог MIN_PUNCTURE_POINTS = 3 при двух точках не срабатывает никогда, то есть
    правило стр. 18 отключено у 70% сторон.»
Числа были получены черновиком `scratchpad/probe_ab.py` и в репозиторий не попали.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Механизм назван верно и существует: `levels.stop_anchor` берёт
`zone.puncture` только при `len(zone.point_indices) >= MIN_PUNCTURE_POINTS` (levels.py).
Сторона с двумя точками отдаёт прокол в стоп НИКОГДА. Значит доля таких сторон — это
доля, у которой требование стр. 18 про стоп за проколом выключено.

ПОРТИРУЕМОСТЬ. Зонд намеренно трогает только `point_indices` и `puncture` — поля, которые
есть у `BoundaryZone` во ВСЕХ сравниваемых версиях. Ни `edge`, ни `narrowed`, ни `source`
здесь не упоминаются, поэтому один и тот же файл гоняется в дереве `main`, на коммите
разбора и на текущем HEAD:

    git worktree add --detach /tmp/pin <коммит>
    cmd //c mklink /J C:\\tmp\\pin\\data C:\\...\\hunter-v2\\data   # кадры 4.3 ГБ, не копировать
    cp docs/audit/probes/probe_two_point_sides_2026-08-08.py /tmp/pin/
    cd /tmp/pin && uv run python probe_two_point_sides_2026-08-08.py

КОРПУС. ВСЕ ряды под `data/frames`, а не «самый длинный на (символ, ТФ)»: числа агента
(8345 структур) на одном наборе кадров получиться не могут, и сравнивать надо на том же
корпусе, на каком считал он. Размер корпуса печатается — если он не совпадёт, расхождение
чисел объясняется выборкой, а не кодом.

КОНТРОЛИ:
  * СВОДКА ПО ТАЙМФРЕЙМУ обязательна. Прецедент 2026-08-04: сто девяносто честных отказов
    подряд читались как «рынок такой», пока их не разложили по ТФ и не увидели, что 4ч и
    1Д дают ноль из 28 и 21. Доля сторон с двумя точками обязана быть разложена так же:
    если она собрана на одном ТФ, это дефект, а не свойство рынка;
  * распределение точек на стороне печатается целиком, а не одним процентом: «70%» без
    хвоста не отличить от «все стороны ровно по две»;
  * считается не только доля двухточечных сторон, но и ПРЯМОЕ СЛЕДСТВИЕ — сколько зон
    имеют прокол, который `stop_anchor` обязан проигнорировать. Это и есть цена вопроса,
    а доля сторон — лишь её косвенный признак.

РЕЗУЛЬТАТ ПРОГОНА 2026-08-08 (корпус 324 ряда, 160126 баров, один и тот же во всех трёх):

                                   main 5e0edb1   7213f00        HEAD fa9977c
    структур закрыто                     3595       5623            5644
    сторон                               7190      11246           11288
    сторон ровно с ДВУМЯ точками   2725 (37.9%)  7762 (69.0%)   7587 (67.2%)
    зон с проколом                 3088 (42.9%)  3003 (26.7%)  11271 (99.8%)
    прокол есть, но игнорируется            0          0        7570 (67.1%)

Проценты агента подтвердились (69.0 против 37.9; структур +56.4%), АБСОЛЮТНЫЕ ЧИСЛА — нет:
он назвал 8345/5301 структур и 16690/10602 сторон. Его корпус примерно в полтора раза
больше того, что вообще собирается из кадров, и восстановить выборку нечем.

⚠ ГЛАВНОЕ — ВЫВОД АГЕНТА ОБРАТЕН ИЗМЕРЕННОМУ. На разбираемом коммите порог не отбрасывал
НИ ОДНОГО прокола: сторона с двумя точками прокола тогда не имела вовсе. Утверждение
«правило стр. 18 отключено у 70% сторон» стало правдой только ПОСЛЕ правки 1, которой на
момент разбора ещё не было. Поведение стопа при этом не изменилось: и до, и после
`stop_anchor` якоря не даёт — сперва потому что прокола нет, теперь потому что точек мало.

Команда воспроизведения (в текущем дереве):
    uv run python docs/audit/probes/probe_two_point_sides_2026-08-08.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.accumulation import detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.models import Bar  # noqa: E402
from hunter.swings import SwingSet  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402

MIN_PUNCTURE_POINTS = 3
"""Копия константы из levels.py. Копия, а не импорт, — чтобы зонд не зависел от того,
как называется модуль уровней в сравниваемой версии дерева. Совпадение проверяется
печатью: если в levels.py число иное, оно видно рядом."""


def series() -> list[tuple[str, str, str, list[Bar]]]:
    """ВСЕ ряды под data/frames: (набор кадров, символ, ТФ, бары)."""
    out: list[tuple[str, str, str, list[Bar]]] = []
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
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
                out.append((run.name, sym, tf, bars))
    return out


def main() -> int:
    rows = series()
    if not rows:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    structures = 0
    sides = 0
    two_point_sides = 0
    with_puncture = 0
    puncture_ignored = 0
    hist: dict[int, int] = {}
    by_tf: dict[str, list[int]] = {}
    skipped = 0

    for _run, _sym, tf, bars in rows:
        sw = detect_swings(bars)
        if not isinstance(sw, SwingSet):
            skipped += 1  # NotReady — ряд короче окна фрактала (§4.3)
            continue
        scan = detect(bars, sw, tf)
        agg = by_tf.setdefault(tf, [0, 0, 0])
        for acc in scan.closed:
            structures += 1
            agg[0] += 1
            for z in (acc.upper, acc.lower):
                n = len(z.point_indices)
                sides += 1
                agg[1] += 1
                hist[n] = hist.get(n, 0) + 1
                if n == 2:
                    two_point_sides += 1
                    agg[2] += 1
                if z.puncture is not None:
                    with_puncture += 1
                    if n < MIN_PUNCTURE_POINTS:
                        puncture_ignored += 1

    print("=" * 78)
    print(f"КОРПУС: рядов {len(rows)} (отброшено NotReady {skipped}), "
          f"баров {sum(len(b) for _r, _s, _t, b in rows)}")
    print("=" * 78)

    print(f"\nструктур закрыто: {structures}")
    print(f"сторон (по 2 на структуру): {sides}")
    if sides == 0:
        print("⚠ СТОРОН НОЛЬ — считать нечего, числа недействительны.")
        return 1

    print(f"\nсторон ровно с ДВУМЯ точками: {two_point_sides} из {sides} "
          f"({two_point_sides / sides * 100:.1f}%)")
    print("распределение точек на стороне:")
    for n in sorted(hist):
        print(f"    {n:2d} точек: {hist[n]:6d}  ({hist[n] / sides * 100:5.1f}%)")
    if len(hist) == 1:
        print("  ⚠ ОДНО ЗНАЧЕНИЕ — счётчик заперт, число ничего не различает.")

    print(f"\nзон с проколом: {with_puncture} ({with_puncture / sides * 100:.1f}%)")
    print(f"ПРЯМОЕ СЛЕДСТВИЕ — прокол есть, но stop_anchor его ИГНОРИРУЕТ "
          f"(точек < {MIN_PUNCTURE_POINTS}):")
    print(f"    {puncture_ignored} зон ({puncture_ignored / sides * 100:.1f}% всех сторон, "
          f"{puncture_ignored / max(with_puncture, 1) * 100:.1f}% зон с проколом)")

    print("\nСВОДКА ПО ТАЙМФРЕЙМУ (перекос вдоль ТФ — известный дефект проекта):")
    print(f"    {'ТФ':>5} {'структур':>9} {'сторон':>8} {'из них 2 точки':>15}")
    for tf in sorted(by_tf, key=lambda t: TIMEFRAME_MS.get(t, 0)):
        st, sd, tp = by_tf[tf]
        print(f"    {tf:>5} {st:>9} {sd:>8} {tp:>9} ({tp / max(sd, 1) * 100:5.1f}%)")
    shares = [tp / max(sd, 1) for _st, sd, tp in by_tf.values() if sd]
    if shares and max(shares) - min(shares) < 0.05:
        print("    доля ровная по всем ТФ — перекоса вдоль этого измерения нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
