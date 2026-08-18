"""Возраст структуры уровня на момент РОЖДЕНИЯ сигнала (в барах своего ТФ).

Связывает разделы «зомби» и «геометрия»: меряется по СИГНАЛАМ, а не по строкам
карты, то есть отвечает на вопрос «от какой структуры мы реально торгуем».
Сшивка сигнал→уровень та же, что в ctl1.py: symbol+timeframe+side+price==entry;
при нескольких версиях структуры берётся самая свежая (max to_ms) из живших
на момент рождения сигнала.

Воспроизведение:
    uv run python docs/audit/evidence/signals-trader-audit-2026-08-18/ctl4_structure_age.py
"""

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
FRAME = 180

con = sqlite3.connect("file:data/ledger.sqlite3?mode=ro", uri=True)
q = con.execute
print("отпечаток: сигналов", q("select count(*) from signals").fetchone()[0],
      "; строк levels", q("select count(*) from levels").fetchone()[0])
for tf, ms in TF_MS.items():
    ages = []
    fresh = 0  # структура достроилась ПОЗЖЕ рождения сигнала — возраст ~0
    for (opened, to_ms) in q(
        """select s.opened_at,
                  (select max(l.to_ms) from levels l
                    where l.symbol=s.symbol and l.timeframe=s.timeframe
                      and l.side=s.direction and abs(l.price - s.entry) < 1e-12
                      and l.to_ms <= s.opened_at)
           from signals s where s.kind='level' and s.timeframe=?""",
        (tf,),
    ):
        if to_ms is None:
            fresh += 1
        else:
            ages.append((opened - to_ms) / ms)
    if not ages:
        continue
    older = sum(1 for a in ages if a > FRAME)
    n = len(ages) + fresh
    print(
        f"{tf}: сигналов {n}, структура моложе сигнала у {fresh};"
        f" по остальным {len(ages)}: медианный возраст структуры"
        f" {statistics.median(ages):.0f} баров, старше {FRAME} баров:"
        f" {older} ({older / n:.0%} от всех)"
    )
