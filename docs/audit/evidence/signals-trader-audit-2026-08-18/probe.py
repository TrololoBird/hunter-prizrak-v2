"""Зонд аналитики сигналов трейдерским взглядом (2026-08-18).

Читает боевой леджер ТОЛЬКО НА ЧТЕНИЕ и печатает все числа протокола
docs/audit/signals-trader-audit-2026-08-18.md одним прогоном.

Воспроизведение:
    uv run python docs/audit/evidence/signals-trader-audit-2026-08-18/probe.py

⚠ Леджер растёт (бот и прогоны пишут), поэтому первым блоком печатается
ОТПЕЧАТОК ДАННЫХ: числа воспроизводятся точно только при том же отпечатке,
дальше — дрейф выборки, а не опровержение (правило от 2026-08-09).
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
BARS_ON_CHART = 180  # кадр графика бота, src/hunter/tgbot.py


def med(xs: list[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def main() -> None:
    con = sqlite3.connect("file:data/ledger.sqlite3?mode=ro", uri=True)
    q = con.execute

    n_sig = q("select count(*) from signals").fetchone()[0]
    n_out = q("select count(*) from outcomes").fetchone()[0]
    n_lvl = q("select count(*) from levels").fetchone()[0]
    n_lvl_active = q("select count(*) from levels where state='active'").fetchone()[0]
    last_rec = q("select max(recorded_at) from signals").fetchone()[0]
    # «Сейчас» зонда — последняя запись леджера, а не часы машины: иначе раздел
    # зомби невоспроизводим командой (замечание фальсификатора 2026-08-18).
    now_ms = last_rec
    print("== ОТПЕЧАТОК ДАННЫХ ==")
    print(f"сигналов: {n_sig}; исходов: {n_out}; строк levels: {n_lvl},"
          f" из них активных: {n_lvl_active}")
    print(f"последний recorded_at: {last_rec} — он же точка отсчёта возрастов")

    print("\n== ПОКРЫТИЕ ИСХОДАМИ по роду ==")
    for kind in ("level", "pp"):
        tot = q("select count(*) from signals where kind=?", (kind,)).fetchone()[0]
        got = q(
            "select count(*) from signals s join outcomes o on o.signal_id=s.id"
            " where s.kind=?",
            (kind,),
        ).fetchone()[0]
        null_r = q(
            "select count(*) from signals s join outcomes o on o.signal_id=s.id"
            " where s.kind=? and o.r is null",
            (kind,),
        ).fetchone()[0]
        ages = [
            (now_ms - r[0]) / 3_600_000
            for r in q(
                "select s.opened_at from signals s"
                " left join outcomes o on o.signal_id=s.id"
                " where s.kind=? and o.signal_id is null",
                (kind,),
            )
        ]
        print(
            f"{kind}: исход у {got} из {tot} ({got / tot:.0%}), r IS NULL"
            f" (ambiguous): {null_r}; без исхода {len(ages)} шт.,"
            f" средний возраст от opened_at {statistics.mean(ages):.0f} ч"
        )

    print("\n== ИСХОДЫ: распределение R по роду (только r IS NOT NULL) ==")
    for kind in ("level", "pp"):
        rs = [
            r[0]
            for r in q(
                "select o.r from signals s join outcomes o on o.signal_id=s.id"
                " where s.kind=? and o.r is not null",
                (kind,),
            )
        ]
        if not rs:
            continue
        wins = [r for r in rs if r > 0]
        stops = sum(1 for r in rs if r <= -0.999)
        small = sum(1 for r in rs if 0 < r < 0.5)
        big = sum(1 for r in rs if r >= 1)
        print(
            f"{kind}: n={len(rs)}, средний R={statistics.mean(rs):+.2f},"
            f" медиана {med(rs):+.2f}, в плюс {len(wins)}/{len(rs)}"
            f" ({len(wins) / len(rs):.0%}); полных стопов (R<=-1): {stops};"
            f" плюсов <0.5R: {small}; >=1R: {big}"
        )

    print("\n== ГЕОМЕТРИЯ ЭМИТИРОВАННЫХ: R:R = |цель-вход| / |вход-стоп| ==")
    for kind in ("level", "pp"):
        rrs = [
            abs(t - e) / abs(e - s)
            for (e, s, t) in q(
                "select entry, stop, target from signals"
                " where kind=? and target is not null",
                (kind,),
            )
        ]
        no_t = q(
            "select count(*) from signals where kind=? and target is null", (kind,)
        ).fetchone()[0]
        ge1 = sum(1 for x in rrs if x >= 1)
        print(
            f"{kind}: n={len(rrs)} (без цели {no_t}), медиана R:R {med(rrs):.2f},"
            f" R:R>=1 у {ge1}/{len(rrs)} ({ge1 / len(rrs):.0%})"
        )

    print("\n== LEVEL по ТФ: R:R, риск (|вход-стоп|/вход), доля R:R>=1 ==")
    for tf in TF_ORDER:
        rows = q(
            "select entry, stop, target from signals"
            " where kind='level' and timeframe=? and target is not null",
            (tf,),
        ).fetchall()
        if not rows:
            continue
        rrs = [abs(t - e) / abs(e - s) for (e, s, t) in rows]
        risk = [abs(e - s) / e * 100 for (e, s, _) in rows]
        ge1 = sum(1 for x in rrs if x >= 1)
        print(
            f"{tf}: n={len(rows)}, медиана R:R {med(rrs):.2f},"
            f" R:R>=1 {ge1 / len(rows):.0%}, медиана риска {med(risk):.2f}%"
        )

    print("\n== ВЫСОТА СТРУКТУРЫ уровня против риска сигнала (по ТФ) ==")
    for tf in TF_ORDER:
        hs = [
            (bh - bl) / p * 100
            for (bl, bh, p) in q(
                "select boundary_lo, boundary_hi, price from levels"
                " where timeframe=?",
                (tf,),
            )
        ]
        if hs:
            print(f"{tf}: уровней {len(hs)}, медиана высоты структуры {med(hs):.2f}%")

    print("\n== ПЕРЕКОС СТОРОН (level) ==")
    for side, n in q(
        "select direction, count(*) from signals where kind='level' group by direction"
    ):
        print(f"{side}: {n}")

    print("\n== ЗОМБИ-УРОВНИ: активные, чья структура старше кадра "
          f"{BARS_ON_CHART} баров своего ТФ ==")
    total = zombie = 0
    for tf in TF_ORDER:
        n_all = q(
            "select count(*) from levels where state='active' and timeframe=?", (tf,)
        ).fetchone()[0]
        n_z = q(
            "select count(*) from levels where state='active' and timeframe=?"
            " and to_ms < ?",
            (tf, now_ms - BARS_ON_CHART * TF_MS[tf]),
        ).fetchone()[0]
        total += n_all
        zombie += n_z
        print(f"{tf}: активных {n_all}, из них зомби {n_z}")
    print(f"итого: зомби {zombie} из {total} активных ({zombie / total:.0%})")
    print("(критерий взят от текущего момента; кадр бота кончается последним"
          " баром, разница <= 1 бара ТФ)")


if __name__ == "__main__":
    main()
