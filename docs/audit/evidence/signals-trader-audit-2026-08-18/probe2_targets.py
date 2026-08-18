"""Контроль: что было бы с R:R, если цели брать только по уровням с ЖИВОЙ структурой.

Прибор обязан уметь ответить иначе — поэтому один и тот же пересчёт выполняется
дважды: с фильтром живой структуры и без него. Если бы числа совпали, фильтр
ничего не решает; расхождение показывает ЦЕНУ зомби-целей.

Реконструкция цели повторяет геометрию боевого build_targets УПРОЩЁННО:
цель = ближайший встречный уровень того же символа со своего или старшего ТФ,
живой на момент рождения сигнала (first_seen <= opened_at < retired_at|∞).
Промежуточные цели ТФ-1 не берутся — сравнение только по главной цели.
«Живая структура» = to_ms не старше 180 баров своего ТФ от момента рождения
сигнала (тот же критерий, что фильтр показа бота).

Воспроизведение:
    uv run python docs/audit/evidence/signals-trader-audit-2026-08-18/probe2_targets.py
"""

from __future__ import annotations

import sqlite3
import statistics

TF_MS = {
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}
TF_ORDER = ["5m", "15m", "1h", "4h", "1d", "1w"]
BARS_FRAME = 180
WINDOW_DAYS = 14


def median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def main() -> None:
    con = sqlite3.connect("file:data/ledger.sqlite3?mode=ro", uri=True)
    q = con.execute

    last_rec = q("select max(recorded_at) from signals").fetchone()[0]
    since = last_rec - WINDOW_DAYS * 86_400_000
    sigs = q(
        "select id, symbol, timeframe, direction, opened_at, entry, stop"
        " from signals where kind='level' and opened_at >= ?",
        (since,),
    ).fetchall()
    print("== ОТПЕЧАТОК ==")
    print(f"последний recorded_at: {last_rec}; окно: {WINDOW_DAYS} суток")
    print(f"уровневых сигналов в окне: {len(sigs)}")
    with_target = q(
        "select count(*) from signals where kind='level' and opened_at >= ?"
        " and target is not null",
        (since,),
    ).fetchone()[0]
    print(
        f"из них эмитировано С ЦЕЛЬЮ в леджере: {with_target}"
        " — реконструкция ниже найдёт МЕНЬШЕ, потому что таблица levels"
        " хранит текущие версии структур (from_ms/to_ms сдвигаются),"
        " часть исторических целей из неё уже ушла. Сравнение «вся карта"
        " против живой структуры» законно: обе ветки считаются по ОДНОМУ"
        " реконструированному пулу. Абсолютные доли покрытия — нет."
    )

    lvls = q(
        "select symbol, timeframe, price, to_ms, first_seen, retired_at from levels"
    ).fetchall()

    def nearest_target(
        sym: str, tf: str, direction: str, opened: int, entry: float,
        need_alive_structure: bool,
    ) -> float | None:
        pool_tfs = TF_ORDER[TF_ORDER.index(tf):]
        best: float | None = None
        for lsym, ltf, price, to_ms, first_seen, retired_at in lvls:
            if lsym != sym or ltf not in pool_tfs:
                continue
            if first_seen > opened or (retired_at is not None and retired_at <= opened):
                continue
            if need_alive_structure and to_ms < opened - BARS_FRAME * TF_MS[ltf]:
                continue
            if direction == "long" and price <= entry:
                continue
            if direction == "short" and price >= entry:
                continue
            if best is None or abs(price - entry) < abs(best - entry):
                best = price
        return best

    for label, alive in (("цели по ВСЕЙ карте", False),
                         ("цели только по ЖИВОЙ структуре", True)):
        rrs: list[float] = []
        lost = 0
        for _sid, sym, tf, direction, opened, entry, stop in sigs:
            t = nearest_target(sym, tf, direction, opened, entry, alive)
            if t is None:
                lost += 1
                continue
            rrs.append(abs(t - entry) / abs(entry - stop))
        ge1 = sum(1 for x in rrs if x >= 1)
        print(f"\n== {label} ==")
        print(
            f"цель нашлась у {len(rrs)} из {len(sigs)} (без цели {lost});"
            f" медиана R:R {median(rrs):.2f},"
            f" R:R>=1 у {ge1}/{len(rrs)}"
            f" ({(ge1 / len(rrs)) if rrs else 0:.0%})"
        )


if __name__ == "__main__":
    main()
