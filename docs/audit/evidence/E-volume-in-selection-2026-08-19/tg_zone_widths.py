"""Ширины зон интереса из выгрузки Telegram-канала автора (01–17.08.2026), v2.

v1 считала мусор: «зона 4ч» и порядковые «зона 1 :» попадали в числа (ATOM
«Лонг зона 1 : 1,35 - 1,4» превращался в 1–1.4 = 33%). Правки v2:
- ТФ-токены (4ч, 15м, м15, 1Д, 1Нед, ЕМА50 и т.п.) и проценты вырезаются ДО чисел;
- если в строке есть «:», числа берутся только после первого «:»;
- кандидаты режутся по « / », « и », «+», «до» склеивается;
- дубли (символ, lo, hi) схлопываются — повторы зоны в соседних постах не веса.

Контроль: печатает отброшенных кандидатов и крайние зоны с текстом строки.
"""
import re
import statistics
from pathlib import Path

src = Path(__file__).with_name("prizrak_tg_flat.txt")
text = src.read_text(encoding="utf-8")

ZONE_MARK = ("🟢", "🟡", "🔴", "🔻", "🎯")
NUM = re.compile(r"\d+(?:[.,]\d+)?")
CLEAN = [
    re.compile(r"\d+\s?(?:ч|м|мин|Д|Нед|нед)\b", re.I),   # 4ч, 15м, 1Д, 1Нед
    re.compile(r"м\d+(?:-\d+ч)?", re.I),                    # м15, м15-1ч
    re.compile(r"[ЕE]М[АA]\s?\d+", re.I),                   # ЕМА50
    re.compile(r"\d+(?:[.,]\d+)?\s?%"),                     # проценты
    re.compile(r"\d+-(?:дневн|минут|секунд)\w*", re.I),     # 200-дневная
    re.compile(r"[ТT]Ф", re.I),
]

cur_tags: list[str] = []
seen: set[tuple[str, float, float]] = set()
zones: list[tuple[float, str, str]] = []   # width%, symbol, line-fragment
levels_only = 0
dropped: list[tuple[str, str]] = []

for raw in text.splitlines():
    line = raw.strip()
    if line.startswith("=== id"):
        cur_tags = []
        continue
    tags = re.findall(r"#([A-Za-z0-9]{2,12})", line)
    if tags and not cur_tags:
        cur_tags = [t for t in tags if not t.isdigit()]
    if not (line.startswith(ZONE_MARK) or "иапазон интереса" in line):
        continue
    body = line.split(":", 1)[1] if ":" in line else line
    for pat in CLEAN:
        body = pat.sub(" ", body)
    for cand in re.split(r"\s/\s|\sи\s|\+", body):
        nums = [float(n.replace(",", ".")) for n in NUM.findall(cand)]
        nums = [n for n in nums if n > 0]
        if not nums:
            continue
        if len(nums) == 1:
            levels_only += 1
            continue
        lo, hi = min(nums), max(nums)
        mid = (lo + hi) / 2
        w = (hi - lo) / mid * 100
        if w > 60:
            dropped.append((f"{w:.0f}%", cand.strip()[:90]))
            continue
        sym = cur_tags[0] if cur_tags else "?"
        key = (sym, lo, hi)
        if key in seen:
            continue
        seen.add(key)
        zones.append((w, sym, cand.strip()[:100]))

print(f"зон (уникальных диапазонов) = {len(zones)}, одиночных уровней = {levels_only}, "
      f"отброшено кандидатов (>60%) = {len(dropped)}")

ws = sorted(z[0] for z in zones)
print(f"\nширина зоны, % от середины: медиана {statistics.median(ws):.2f}, "
      f"мин {ws[0]:.3f}, макс {ws[-1]:.2f}")
qs = statistics.quantiles(ws, n=10)
q4 = statistics.quantiles(ws, n=4)
print(f"дециль 10% {qs[0]:.2f} | квартиль 25% {q4[0]:.2f} "
      f"| медиана {statistics.median(ws):.2f} | 75% {q4[2]:.2f} | 90% {qs[8]:.2f}")

maj = [z for z in zones if z[1].upper() in ("BTC", "ETH")]
alt = [z for z in zones if z[1].upper() not in ("BTC", "ETH", "XAU", "XAG", "?")]
for name, arr in (("BTC/ETH", maj), ("альты", alt)):
    a = sorted(x[0] for x in arr)
    if a:
        aq = statistics.quantiles(a, n=4)
        print(f"\n{name}: n={len(a)}, медиана {statistics.median(a):.2f}%, "
              f"25% {aq[0]:.2f}%, 75% {aq[2]:.2f}%, "
              f"мин {a[0]:.3f}%, макс {a[-1]:.2f}%")

print("\nтоп-5 узких:")
for w, sym, cand in sorted(zones)[:5]:
    print(f"  {sym:8s} {w:6.3f}%  {cand}")
print("топ-5 широких:")
for w, sym, cand in sorted(zones)[-5:]:
    print(f"  {sym:8s} {w:6.2f}%  {cand}")
print("\nотброшенные (проверка парсера):")
for w, cand in dropped:
    print(f"  {w:5s}  {cand}")
