# Та же проверка, но расстояние считается ОТ ВХОДА (ПОК), как мерил аудитор.
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
HDR = re.compile(r"^\s*(ЛОНГ|ШОРТ)\s+(\S+)\s+ПОК\s+(\S+)")
BND = re.compile(r"границы\s+(\S+?)…(\S+)")
STOP = re.compile(r"^\+\s+стоп\s+(\S+)\s+—")
side = tf = None; entry = lo = hi = None
n = far = 0
from collections import Counter
c = Counter()
for ln in lines:
    m = HDR.match(ln.lstrip("-+ ")) if "ПОК" in ln else None
    if m: side, tf, entry = m.group(1), m.group(2), float(m.group(3)); lo = hi = None
    b = BND.search(ln)
    if b: lo, hi = float(b.group(1)), float(b.group(2))
    if ln.lstrip().startswith("+"):
        s = STOP.match(ln.strip())
        if s and lo is not None and entry is not None:
            stop = float(s.group(1)); height = hi - lo
            dist = abs(stop - entry); n += 1
            if height > 0 and dist > 2 * height:
                far += 1; c[tf] += 1
print(f"стопов: {n}; дальше 2 высот ОТ ВХОДА: {far}; по ТФ: {dict(c)}")
