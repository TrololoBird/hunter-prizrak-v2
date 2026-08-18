"""Контроли к замеру ширин зон автора (tg-zone-widths-2026-08-18.py), по вердикту
falsifier 2026-08-18. Три контроля:

1. СТРОГИЙ критерий ширины: только первая пара «число–тире–число» строки (сам
   диапазон), а не размах всех чисел. Показывает, насколько числа зависят от
   критерия: «размах всех чисел» тянет в зону ключевые уровни и цели (GRAM: зона
   2,35–2,51 + ключевой уровень 1,76 = 35% вместо 6.6%).
2. ПЕРЕСТАНОВОЧНЫЙ тест разделения BTC/ETH против альт: метки символов
   перемешиваются, разница медиан пересчитывается. Отвечает, могла ли разница
   получиться случайной разметкой (seed фиксирован — детерминизм).
3. Леджер ДВУМЯ линейками: (hi-lo)/ПОК (как боевой price) и (hi-lo)/середина (как
   мерян автор — у него ПОК не в центре, id 7660: ПОК на 40% от низа). Плюс
   отпечаток леджера: размер файла и разбивка уровней по состояниям — база растёт,
   без отпечатка команда воспроизведения соврёт после первого прогона.

Разбор строк продублирован из tg-zone-widths-2026-08-18.py (щадящая копия: зонды
evidence самодостаточны, импортов друг из друга не имеют).
"""
import random
import re
import sqlite3
import statistics
from pathlib import Path

src = Path("prizzrak-tg/prizrak_tg_flat.txt")
text = src.read_text(encoding="utf-8")

ZONE_MARK = ("🟢", "🟡", "🔴", "🔻", "🎯")
NUM = re.compile(r"\d+(?:[.,]\d+)?")
PAIR = re.compile(r"(\d+(?:[.,]\d+)?)\s*[–\-—]\s*(\d+(?:[.,]\d+)?)")
CLEAN = [
    re.compile(r"\d+\s?(?:ч|м|мин|Д|Нед|нед)\b", re.I),
    re.compile(r"м\d+(?:-\d+ч)?", re.I),
    re.compile(r"[ЕE]М[АA]\s?\d+", re.I),
    re.compile(r"\d+(?:[.,]\d+)?\s?%"),
    re.compile(r"\d+-(?:дневн|минут|секунд)\w*", re.I),
    re.compile(r"[ТT]Ф", re.I),
]


def parse(strict: bool) -> list[tuple[float, str]]:
    cur_tags: list[str] = []
    seen: set[tuple[str, float, float]] = set()
    zones: list[tuple[float, str]] = []
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
            if strict:
                m = PAIR.search(cand)
                if not m:
                    continue
                nums = [float(m.group(1).replace(",", ".")),
                        float(m.group(2).replace(",", "."))]
            else:
                nums = [float(n.replace(",", ".")) for n in NUM.findall(cand)]
            nums = [n for n in nums if n > 0]
            if len(nums) < 2:
                continue
            lo, hi = min(nums), max(nums)
            w = (hi - lo) / ((lo + hi) / 2) * 100
            if w > 60 or w == 0:
                continue
            sym = cur_tags[0] if cur_tags else "?"
            key = (sym, lo, hi)
            if key in seen:
                continue
            seen.add(key)
            zones.append((w, sym))
    return zones


def stats_line(name: str, ws: list[float]) -> str:
    ws = sorted(ws)
    if len(ws) < 2:
        return f"{name}: n={len(ws)}"
    q = statistics.quantiles(ws, n=4)
    return (f"{name}: n={len(ws)}, медиана {statistics.median(ws):.2f}%, "
            f"25% {q[0]:.2f}%, 75% {q[2]:.2f}%, макс {ws[-1]:.2f}%")


for label, strict in (("критерий «размах всех чисел» (как в основном зонде)", False),
                      ("критерий СТРОГИЙ (только первая пара «число–тире–число»)", True)):
    zones = parse(strict)
    maj = [w for w, s in zones if s.upper() in ("BTC", "ETH")]
    alt = [w for w, s in zones if s.upper() not in ("BTC", "ETH", "XAU", "XAG", "?")]
    print(f"--- {label}")
    print("  " + stats_line("все зоны", [w for w, _ in zones]))
    print("  " + stats_line("BTC/ETH", maj))
    print("  " + stats_line("альты  ", alt))

# 2. Перестановочный тест на основном (нестрогом) критерии.
zones = parse(strict=False)
labeled = [(w, s.upper() in ("BTC", "ETH")) for w, s in zones
           if s.upper() not in ("XAU", "XAG", "?")]
ws_all = [w for w, _ in labeled]
n_maj = sum(1 for _, is_maj in labeled if is_maj)
real = (statistics.median([w for w, m in labeled if not m])
        - statistics.median([w for w, m in labeled if m]))
rng = random.Random(20260818)
hits = 0
ROUNDS = 2000
for _ in range(ROUNDS):
    rng.shuffle(ws_all)
    diff = statistics.median(ws_all[n_maj:]) - statistics.median(ws_all[:n_maj])
    if diff >= real:
        hits += 1
print(f"\n--- перестановочный тест (seed 20260818, {ROUNDS} перестановок)")
print(f"  реальная разница медиан альты-BTC/ETH = {real:.2f} п.п.; "
      f"случайная метка даёт не меньше в {hits} из {ROUNDS}")

# 3. Леджер двумя линейками + отпечаток.
db = Path("data/ledger.sqlite3")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
states = conn.execute(
    "SELECT state, COUNT(*) FROM levels GROUP BY state ORDER BY state").fetchall()
print(f"\n--- леджер: отпечаток {db.stat().st_size} байт; уровней по состояниям: "
      + ", ".join(f"{s}={n}" for s, n in states))
rows = conn.execute(
    "SELECT state, price, zone_lo, zone_hi FROM levels WHERE price > 0"
).fetchall()
conn.close()
for state in ("active",):
    by_pok = sorted((hi - lo) / p * 100 for s, p, lo, hi in rows if s == state)
    by_mid = sorted((hi - lo) / ((hi + lo) / 2) * 100
                    for s, p, lo, hi in rows if s == state and hi + lo > 0)
    over_pok = sum(1 for w in by_pok if w > 38.67)
    over_mid = sum(1 for w in by_mid if w > 38.67)
    print(f"  {state}: /ПОК медиана {statistics.median(by_pok):.2f}% макс {by_pok[-1]:.2f}% "
          f"шире 38.67%: {over_pok} из {len(by_pok)}")
    print(f"  {state}: /середина медиана {statistics.median(by_mid):.2f}% макс {by_mid[-1]:.2f}% "
          f"шире 38.67%: {over_mid} из {len(by_mid)}")
