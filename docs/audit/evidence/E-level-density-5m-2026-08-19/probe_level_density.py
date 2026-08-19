"""Сколько РАЗНЫХ физических уровней стоит за активными уровнями карты.

Повод. Разбор РР 2026-08-19 назвал механизм: цель — ближайший встречный уровень того же
ТФ, и на 5м он в 0.1-1% от входа, тогда как стоп — структура плюс запас 1-3% ОТ ЦЕНЫ.
Отсюда РР 0.18 на 5м против 2.13 на 1н. Осталось непроверенным, ПОЧЕМУ встречный уровень
так близко: рынок так устроен — или карта много раз пишет ОДИН И ТОТ ЖЕ уровень.

Прибор. Уровни одной стороны одного символа одного ТФ, чьи зоны ПЕРЕСЕКАЮТСЯ, склеиваются
в один кластер (транзитивно). Кластер — кандидат в «физический уровень». Отношение
уровней к кластерам показывает кратность записи.

КОНТРОЛИ (без них это не замер):
  1. способность ответить иначе — та же мера на 1д/1н/4ч. Если и там кратность та же,
     прибор меряет не 5м, а собственную склейку;
  2. заведомо неверная опора — те же ШИРИНЫ зон, но центры расставлены равномерно
     случайно в наблюдённом диапазоне цен символа. Если случайная расстановка даёт ту же
     кратность, склейка меряет тесноту ценовой оси, а не повтор уровня.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict

DB = "file:data/ledger.sqlite3?mode=ro"


def clusters(zones: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Склейка пересекающихся отрезков. Возвращает объединённые кластеры."""
    if not zones:
        return []
    out: list[tuple[float, float]] = []
    lo, hi = sorted(zones)[0]
    for a, b in sorted(zones)[1:]:
        if a <= hi:                       # пересекается или касается
            hi = max(hi, b)
        else:
            out.append((lo, hi))
            lo, hi = a, b
    out.append((lo, hi))
    return out


def load(tf: str) -> dict[tuple[str, str], list[tuple[float, float]]]:
    c = sqlite3.connect(DB, uri=True)
    g: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for sym, side, zlo, zhi in c.execute(
        "select symbol, side, zone_lo, zone_hi from levels "
        "where state='active' and timeframe=? and zone_lo is not null", (tf,)
    ):
        g[(sym, side)].append((float(zlo), float(zhi)))
    return g


def report(tf: str, g, label: str) -> tuple[int, int]:
    n_lv = sum(len(v) for v in g.values())
    n_cl = sum(len(clusters(v)) for v in g.values())
    if not n_lv:
        print(f"  {label:34} {tf:4} пусто")
        return 0, 0
    mult = n_lv / n_cl
    print(f"  {label:34} {tf:4} уровней {n_lv:5}, кластеров {n_cl:5}, "
          f"кратность {mult:.2f}")
    return n_lv, n_cl


class LCG:
    """Свой генератор, а не `random`: модуль запрещён правилом детерминизма (§10.3).

    Запрет по делу и здесь: контроль обязан воспроизводиться посимвольно на чужой
    машине и в другой версии Python, а `random.Random` таких обещаний не даёт.
    Линейный конгруэнтный генератор Кнута — три числа, воспроизводится везде.
    """

    def __init__(self, seed: int) -> None:
        self.s = seed & 0xFFFFFFFFFFFFFFFF

    def next(self) -> float:
        self.s = (6364136223846793005 * self.s + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        return (self.s >> 11) / float(1 << 53)


def randomized(g, seed: int):
    """Те же ширины зон, центры — равномерно в наблюдённом диапазоне символа."""
    rnd = LCG(seed)
    out: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for key, zs in g.items():
        lo = min(a for a, _ in zs)
        hi = max(b for _, b in zs)
        span = hi - lo
        new = []
        for a, b in zs:
            w = b - a
            c = lo + rnd.next() * max(span - w, 0.0)
            new.append((c, c + w))
        out[key] = new
    return out


def gap_survey(tf: str) -> None:
    """Расстояние до БЛИЖАЙШЕГО ВСТРЕЧНОГО уровня — та величина, что задаёт цель."""
    c = sqlite3.connect(DB, uri=True)
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sym, side, price in c.execute(
        "select symbol, side, price from levels where state='active' and timeframe=?", (tf,)
    ):
        by[sym][side].append(float(price))
    raw: list[float] = []
    clus: list[float] = []
    for sym, sides in by.items():
        z = load(tf)
        for side, prices in sides.items():
            other = "short" if side == "long" else "long"
            opp = sorted(sides.get(other, []))
            if not opp:
                continue
            oc = clusters(z.get((sym, other), []))
            centers = sorted((a + b) / 2 for a, b in oc)
            for p in prices:
                d = min(abs(p - q) for q in opp) / p * 100
                raw.append(d)
                if centers:
                    clus.append(min(abs(p - q) for q in centers) / p * 100)
    for name, xs in (("до ближайшего встречного уровня", raw),
                     ("до ближайшего встречного КЛАСТЕРА", clus)):
        if not xs:
            continue
        xs = sorted(xs)
        print(f"  {name:38} {tf:4} n={len(xs):5} медиана {statistics.median(xs):6.3f}%, "
              f"25% {xs[len(xs)//4]:6.3f}%, 75% {xs[3*len(xs)//4]:6.3f}%")


def main() -> int:
    c = sqlite3.connect(DB, uri=True)
    total = c.execute("select count(*) from levels").fetchone()[0]
    act = c.execute("select count(*) from levels where state='active'").fetchone()[0]
    print(f"ОТПЕЧАТОК ДАННЫХ: строк в levels {total}, активных {act}")
    print()
    print("КРАТНОСТЬ ЗАПИСИ УРОВНЯ (уровней на один кластер пересекающихся зон):")
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        g = load(tf)
        report(tf, g, "как есть")
    print()
    print("КОНТРОЛЬ 2 — те же ширины, СЛУЧАЙНЫЕ центры (три посева):")
    for tf in ("5m", "1h", "1d"):
        g = load(tf)
        for seed in (1, 2, 3):
            report(tf, randomized(g, seed), f"случайная опора, посев {seed}")
    print()
    print("РАССТОЯНИЕ ДО ВСТРЕЧНОГО (что и становится целью):")
    for tf in ("5m", "1h", "1d"):
        gap_survey(tf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
