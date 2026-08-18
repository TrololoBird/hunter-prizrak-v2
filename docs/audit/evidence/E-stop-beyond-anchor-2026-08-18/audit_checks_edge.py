# Контроли аудита по новому диффу: стопы дальше 2 высот базы и за полосой 5%.
# Парсит контекст диффа: сторона, границы, новый стоп, источник якоря.
import re
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()

HDR = re.compile(r"^\s*(ЛОНГ|ШОРТ)\s+(\S+)\s+ПОК\s+(\S+)")
BND = re.compile(r"границы\s+(\S+?)…(\S+)")
STOP = re.compile(r"^\+\s+стоп\s+(\S+)\s+—")

side = tf = None
lo = hi = None
n = beyond_2h = beyond_band = 0
beyond_band_puncture = 0
worst = []
for ln in lines:
    m = HDR.match(ln.lstrip("-+ ")) if "ПОК" in ln else None
    if m:
        side, tf = m.group(1), m.group(2)
        lo = hi = None
    b = BND.search(ln)
    if b:
        lo, hi = float(b.group(1)), float(b.group(2))
    s = STOP.match(ln.strip()) if ln.lstrip().startswith("+") else None
    if s and lo is not None:
        stop = float(s.group(1))
        height = hi - lo
        edge = lo if side == "ЛОНГ" else hi
        dist = (edge - stop) if side == "ЛОНГ" else (stop - edge)
        n += 1
        if height > 0 and dist > 2 * height:
            beyond_2h += 1
            worst.append((tf, side, round(dist / height, 2)))
        if dist > edge * 0.05 * (1 + 1e-9):
            beyond_band += 1
            if "якорь: прокол" in ln:
                beyond_band_puncture += 1

print(f"новых якорных стопов в диффе: {n}")
print(f"дальше 2 высот базы: {beyond_2h}")
print(f"за полосой 5% от границы: {beyond_band} (из них проколы: {beyond_band_puncture})")
if worst:
    from collections import Counter
    print("дальше 2 высот, по ТФ:", dict(Counter(t for t, _, _ in worst)))
    print("максимум высот:", max(r for _, _, r in worst))
