# Сведение диффа повтора: изменённые стопы с разрезами по источнику якоря и ТФ.
# Читает вывод `replay --run-id last --diff`, печатает числа для evidence.
import re
import statistics
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines()

HDR = re.compile(r"^[-+ ]?\s*(ЛОНГ|ШОРТ)\s+(\S+)\s+ПОК")
STOP = re.compile(r"^([-+])\s+стоп\s+(\S+)\s+—\s+(.*?)\s+\((\d+(?:\.\d+)?)% от входа\)")
RR = re.compile(r"^([-+])\s+РР 1к(\d+(?:\.\d+)?)")

ctx_tf = None
pending = {}  # ключ (tf, счётчик) не нужен: минус и плюс идут парой подряд
changed = []  # dict: tf, src, old_pct, new_pct
last_minus = None
rr_pairs = []
last_rr_minus = None
below_was = below_now = 0

for ln in lines:
    m = HDR.match(ln.strip()) or HDR.match(ln.lstrip("-+ "))
    if m:
        ctx_tf = m.group(2)
    s = STOP.match(ln.strip())
    if s:
        sign, price, basis, pct = s.group(1), s.group(2), s.group(3), float(s.group(4))
        if "якорь: прокол" in ln:
            src = "прокол"
        elif "якорь: стоповый объём" in ln or "якорь: стоповый объем" in ln:
            src = "стоповый объём"
        elif "якорь: лой/хай" in ln:
            src = "свинг"
        elif "за стоповым объёмом либо проколом" in basis:
            src = "якорь(?)"
        else:
            src = "запас"
        if sign == "-":
            last_minus = (ctx_tf, src, pct)
        else:
            if last_minus is not None:
                changed.append(
                    {"tf": ctx_tf, "src_old": last_minus[1], "src": src,
                     "old": last_minus[2], "new": pct})
                last_minus = None
    r = RR.match(ln.strip())
    if r:
        if r.group(1) == "-":
            last_rr_minus = float(r.group(2))
            if "НИЖЕ стандарта" in ln:
                below_was += 1
        else:
            if "НИЖЕ стандарта" in ln:
                below_now += 1
            if last_rr_minus is not None:
                rr_pairs.append((last_rr_minus, float(r.group(2))))
                last_rr_minus = None

n = len(changed)
print(f"изменённых стопов: {n}")
anch = [c for c in changed if c["src"] != "запас"]
marg = [c for c in changed if c["src"] == "запас"]
print(f"  якорных: {len(anch)}, запасных: {len(marg)}")
mism = [c for c in changed if c["src"] != c["src_old"]]
print(f"  сменивших основание: {len(mism)}")

def med(xs):
    return round(statistics.median(xs), 2) if xs else None

print(f"\nмедиана % от входа: {med([c['old'] for c in changed])} → "
      f"{med([c['new'] for c in changed])}")
print(f"медиана добавки (п.п. от входа): "
      f"{med([c['new'] - c['old'] for c in changed])}")
print(f"стопов, где добавка ≥ 0.9 п.п.: "
      f"{sum(1 for c in changed if c['new'] - c['old'] >= 0.9)}")
print(f"стопов, где добавка < 0.9 п.п. (срезана рамками): "
      f"{sum(1 for c in changed if c['new'] - c['old'] < 0.9)}")

print("\nразрез по источнику якоря:")
for src in sorted({c["src"] for c in changed}):
    g = [c for c in changed if c["src"] == src]
    print(f"  {src:16s} n={len(g):3d}  медиана {med([c['old'] for c in g])} → "
          f"{med([c['new'] for c in g])}  добавка {med([c['new']-c['old'] for c in g])}")

print("\nразрез по ТФ:")
for tf in ["5м", "15м", "1ч", "4ч", "1Д", "5m", "15m", "1h", "4h", "1d"]:
    g = [c for c in changed if c["tf"] == tf]
    if g:
        print(f"  {tf:4s} n={len(g):3d}  медиана {med([c['old'] for c in g])} → "
              f"{med([c['new'] for c in g])}  добавка {med([c['new']-c['old'] for c in g])}")

print(f"\nРР-пар изменено: {len(rr_pairs)}")
if rr_pairs:
    print(f"медиана РР: {med([a for a, _ in rr_pairs])} → {med([b for _, b in rr_pairs])}")
    print(f"РР < 0.3 было {sum(1 for a, _ in rr_pairs if a < 0.3)}, "
          f"стало {sum(1 for _, b in rr_pairs if b < 0.3)}")
print(f"строк «НИЖЕ стандарта»: было {below_was}, стало {below_now}")
