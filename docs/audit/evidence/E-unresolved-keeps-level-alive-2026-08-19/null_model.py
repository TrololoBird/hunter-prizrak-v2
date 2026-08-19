"""НУЛЕВАЯ МОДЕЛЬ к правке: не объясняется ли падение числа встречных пар одной арифметикой.

Правка убрала 118 уровней из состояния `active` (181 → 63), и пар стало 406 → 2. Пары
считаются ПАРАМИ, то есть квадратично: втрое меньше уровней — примерно вдевятеро меньше
пар само по себе. Вопрос: наблюдённая двойка — свойство правки или свойство деления?

Контроль: из ТЕХ ЖЕ 181 уровня берём СЛУЧАЙНЫЕ 28 лонгов и 35 шортов (ровно тот состав,
что получился после правки) и считаем пары. Если правка ничего не улучшает, наблюдённое
обязано лечь в СЕРЕДИНУ случайного разброса.

Это тот же приём, что «решётка произвольных цен» в CLAUDE.md: замер на данных, про
которые заведомо известно, что совпадать они не должны.

Воспроизведение (карточки строятся `hunter replay --run-id <прогон> --card`):
    uv run python docs/audit/evidence/E-unresolved-keeps-level-alive-2026-08-19/null_model.py \
        КАРТОЧКА_ДО.txt КАРТОЧКА_ПОСЛЕ.txt

ОТПЕЧАТОК замера 2026-08-19: прогон `ondo-deep`, ONDO/USDT:USDT, 1797 строк уровней,
12612 строк карточки до и 12611 после. Зерно случайности зафиксировано — число
воспроизводится побайтно.
"""
import random
import statistics
import sys

from count_overlaps import overlaps, parse

DRAWS = 2000
SEED = 20260819


def main() -> int:
    if len(sys.argv) < 3:
        print("нужны два пути: карточка ДО и карточка ПОСЛЕ", file=sys.stderr)
        return 2
    before = [r for r in parse(sys.argv[1]) if r["state"] == "активен"]
    after = [r for r in parse(sys.argv[2]) if r["state"] == "активен"]
    if not before or not after:
        print("ПЛОХО: карточка не разобрана — числа были бы ложным нулём", file=sys.stderr)
        return 2
    nb, na = overlaps(before), overlaps(after)
    print(f"НАБЛЮДЕНО  было: активных {len(before)}, пар {sum(nb)} "
          f"(одного ТФ {nb[0]}, разных {nb[1]})")
    print(f"НАБЛЮДЕНО стало: активных {len(after)}, пар {sum(na)} "
          f"(одного ТФ {na[0]}, разных {na[1]})")

    longs = [r for r in before if r["side"] == "ЛОНГ"]
    shorts = [r for r in before if r["side"] == "ШОРТ"]
    n_long = sum(1 for r in after if r["side"] == "ЛОНГ")
    n_short = len(after) - n_long
    rnd = random.Random(SEED)
    totals: list[int] = []
    sames: list[int] = []
    for _ in range(DRAWS):
        pick = rnd.sample(longs, n_long) + rnd.sample(shorts, n_short)
        s, c = overlaps(pick)
        totals.append(s + c)
        sames.append(s)
    totals.sort()
    sames.sort()
    pct = lambda v, p: v[int(p * (len(v) - 1))]  # noqa: E731
    print(f"\nНУЛЕВАЯ МОДЕЛЬ ({DRAWS} выборок по {n_long} лонгов и {n_short} шортов "
          f"из тех же {len(before)}):")
    print(f"  пар всего    : медиана {statistics.median(totals):.0f}, "
          f"5%–95% {pct(totals, .05)}…{pct(totals, .95)}, минимум {totals[0]}")
    print(f"  пар одного ТФ: медиана {statistics.median(sames):.0f}, "
          f"5%–95% {pct(sames, .05)}…{pct(sames, .95)}, минимум {sames[0]}")
    not_worse = sum(1 for x in totals if x <= sum(na))
    print(f"\n  случайных выборок НЕ ХУЖЕ наблюдённых {sum(na)} пар: {not_worse} из {DRAWS}")
    print(f"  случайных выборок с нулём пар одного ТФ: {sum(1 for x in sames if x == 0)} "
          f"из {DRAWS}")
    if not_worse * 20 > DRAWS:  # более 5%
        print("\nПЛОХО: случайное прореживание даёт тот же результат — падение пар "
              "объясняется числом уровней, а не правкой.")
        return 1
    print("\nХОРОШО: наблюдённое лежит ЗА нижним краем случайного разброса — падение пар "
          "не объясняется тем, что уровней стало меньше.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
