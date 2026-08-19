"""КОНТРОЛЬ ПОЧИНЕННОГО ИЗМЕРИТЕЛЯ: плотность обязана НЕ следовать за шириной зоны.

Доля объёма шла за шириной с ранговой корреляцией +0.716 — то есть «сила по объёму»
означала «широкая полоса». Плотность (объём на ценовую строку профиля против среднего)
построена так, чтобы от ширины не зависеть. Проверяется это ТЕМ ЖЕ замером на ТЕХ ЖЕ
уровнях: если корреляция плотности с шириной осталась высокой, починки не произошло.

Второй контроль: плотность обязана РАЗЛИЧАТЬ уровни (иначе прибор заперт в одном
ответе) — печатаются квантили и доля зон плотнее среднего.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


allد: list[float] = []
allw: list[float] = []
for symbol, d in sorted(data.items()):
    dens = [z["vrvp_density"] for z in d["zones"]]
    wd = [(z["zone_hi"] - z["zone_lo"]) / ((z["zone_hi"] + z["zone_lo"]) / 2)
          for z in d["zones"]]
    q = sorted(dens)
    n = len(q)
    print(f"  {symbol:16} n={n:5}  ρ(плотность, ширина) = {spearman(dens, wd):+.3f}   "
          f"квантили ×: 10% {q[n//10]:.2f}  50% {q[n//2]:.2f}  90% {q[9*n//10]:.2f}  "
          f"макс {q[-1]:.1f}  плотнее среднего: {sum(1 for x in dens if x > 1)/n*100:.0f}%")
    allد += dens
    allw += wd

n = len(allد)
q = sorted(allد)
print(f"\n  ВСЕ 5 символов, n={n}:")
print(f"    ρ(плотность, ширина зоны) = {spearman(allد, allw):+.3f}"
      f"   (у ДОЛИ объёма было +0.716 — это и был дефект)")
print(f"    квантили плотности: 10% ×{q[n//10]:.2f}  50% ×{q[n//2]:.2f}  "
      f"90% ×{q[9*n//10]:.2f}  макс ×{q[-1]:.1f}")
print(f"    зон плотнее среднего композита: {sum(1 for x in allد if x > 1)} "
      f"({sum(1 for x in allد if x > 1)/n*100:.1f}%)")
