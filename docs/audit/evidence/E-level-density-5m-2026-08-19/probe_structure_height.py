"""Высота структуры по ТФ против запаса стопа — арифметика, закрывшая вопрос РР.

Курс на стр. 33: «Безопасный СТОП за дно структуры с запасом 1-3%». Запас — проценты
ОТ ЦЕНЫ, и ни одна из четырёх страниц (19, 33, 36, 58) другой единицы не называет.
Вопрос прибора: как этот запас соотносится с высотой самой структуры на каждом ТФ.

КОНТРОЛЬ. Способность ответить иначе видна в самой таблице: если бы мера была заперта,
все ТФ дали бы одно число. Разброс медиан от 0.911% (5м) до 37.363% (1Н) — на два
порядка — показывает, что мера следует за данными. Второй контроль — доля структур выше
3%: она монотонна по ТФ (7.4 → 100.0), то есть не артефакт округления на краю.

Замер идёт по боевому леджеру ТОЛЬКО НА ЧТЕНИЕ (`mode=ro`).
"""
from __future__ import annotations

import sqlite3
import statistics

DB = "file:data/ledger.sqlite3?mode=ro"


def main() -> int:
    c = sqlite3.connect(DB, uri=True)
    total = c.execute("select count(*) from levels").fetchone()[0]
    act = c.execute("select count(*) from levels where state='active'").fetchone()[0]
    print(f"ОТПЕЧАТОК ДАННЫХ: строк в levels {total}, активных {act}")
    print()
    print("ВЫСОТА СТРУКТУРЫ (boundary_hi−boundary_lo) в % от цены, активные уровни:")
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        xs = sorted(
            (float(hi) - float(lo)) / float(p) * 100
            for p, lo, hi in c.execute(
                "select price, boundary_lo, boundary_hi from levels "
                "where state='active' and timeframe=? and boundary_lo is not null", (tf,))
            if float(p) > 0
        )
        if not xs:
            continue
        print(f"  {tf:4} n={len(xs):5} медиана {statistics.median(xs):7.3f}%  "
              f"25% {xs[len(xs) // 4]:7.3f}%  75% {xs[3 * len(xs) // 4]:7.3f}%  "
              f"выше 3%: {sum(1 for x in xs if x > 3) / len(xs) * 100:5.1f}%  "
              f"запас 3% = {3 / statistics.median(xs):5.2f} высоты базы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
