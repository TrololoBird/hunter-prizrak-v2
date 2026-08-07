"""Проверка НЕЗАВИСИМОСТИ выборки обзора накопления: ядра, нормализация, хеши.

Зачем. Правило команды `/survey`: «двадцать форков одного файла хуже двенадцати
самостоятельных проектов». Сравнивать попарно вручную нельзя — у 18 копий это 153 пары.
Порядок: вырезать ядро → нормализовать → взять хеш → сравнить ХЕШИ, а не тексты.

Нормализация языконезависима, потому что выборка на трёх языках (Python, Pine, MQL4/5):
убираются комментарии, пустые строки, отступы; каждый идентификатор, не входящий в список
служебных слов, заменяется на позиционный `v0`, `v1`, … в порядке первого появления.
Строковые литералы и числа сохраняются: именно они отличают `boxp - 2` от `boxp - 3`.

⚠ Прибор обязан уметь ответить ИНАЧЕ. Контроль встроен и печатается всегда:
  * две заведомо РАЗНЫЕ реализации обязаны дать разные хеши;
  * один и тот же текст с переименованными переменными и другими отступами — один хеш.
Без этих двух строк «клонов не найдено» неотличимо от «нормализатор всё склеил в одно».

Чужой код в репозиторий не кладётся. Скрипт читает то, что скачано командами из
docs/audit/accumulation-projects-2026-08-07.md, и путь к каталогу передаётся аргументом.

Запуск:
    uv run python docs/audit/probes/probe_accumulation_clones_2026-08-07.py <каталог>
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

KEYWORDS = frozenset(
    """
    if else elif for while return break continue switch case default do
    int double float bool void long short static const input var true false na
    def class import from lambda try except pass and or not in is none
    float64 series simple index type export method strategy indicator study
    """.split()
)

IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
LINE_COMMENT = re.compile(r"(//|#).*$")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# Pine v5 развёл стандартную библиотеку по пространствам имён: `lowest` стал
# `ta.lowest`. Порт v4→v5 — это ТА ЖЕ реализация, и без снятия префикса хеши разойдутся.
PINE_NAMESPACE = re.compile(r"\b(ta|math|str|color|array|input|line|box|label)\.")


def read_text(path: Path) -> str:
    """Файлы CodeBase приходят в UTF-8-BOM, UTF-16LE и cp1252 вперемешку."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16-le", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if text.count("\n") > 5 and "\x00" not in text[:200]:
            return text
    return raw.decode("utf-8", errors="replace")


def cut(text: str, start: str, end: str | None, span: int) -> str:
    """Ядро: от строки со `start` и либо до строки с `end`, либо `span` строк."""
    lines = text.replace("\x00", "").splitlines()
    begin = next((i for i, ln in enumerate(lines) if start in ln), None)
    if begin is None:
        raise LookupError(f"не найдено начало ядра: {start!r}")
    if end is None:
        return "\n".join(lines[begin : begin + span])
    stop = next((i for i, ln in enumerate(lines[begin + 1 :], begin + 1) if end in ln), None)
    if stop is None:
        raise LookupError(f"не найден конец ядра: {end!r}")
    return "\n".join(lines[begin : stop + 1])


def normalize(core: str) -> str:
    core = PINE_NAMESPACE.sub("", BLOCK_COMMENT.sub(" ", core))
    out: list[str] = []
    names: dict[str, str] = {}
    for line in core.splitlines():
        line = LINE_COMMENT.sub("", line).strip()
        if not line:
            continue

        def rename(m: re.Match[str]) -> str:
            word = m.group(0)
            if word.lower() in KEYWORDS:
                return word.lower()
            return names.setdefault(word, f"v{len(names)}")

        line = IDENT.sub(rename, line)
        # Пробелы внутри строки тоже форматирование: `f( a , b )` и `f(a, b)` — одно.
        line = re.sub(r"\s+", " ", line)
        line = re.sub(r"\s*([^\w\s])\s*", r"\1", line)
        out.append(line)
    return "\n".join(out)


def fingerprint(core: str) -> str:
    return hashlib.sha1(normalize(core).encode()).hexdigest()[:12]


# (метка, путь от каталога, начало ядра, конец ядра или None, сколько строк если конца нет)
MANIFEST: list[tuple[str, str, str, str | None, int]] = [
    # --- питон ---
    ("py/TradingPatternScanner detect_channel",
     "white07S_TradingPatternScanner/tradingpatterns/tradingpatterns.py",
     "def detect_channel", "return df", 0),
    ("py/Wyckoff-AI-Assistant spring/upthrust",
     "Eesita_Wyckoff-AI-Assistant/Wyckoff_chatbot/utils/data_processing.py",
     "for i in range(1, len(df)-1):", "Potential_Upthrust')] = 1", 0),
    ("py/smart-money-concepts liquidity",
     "joshyattridge_smart-money-concepts/smartmoneyconcepts/smc.py",
     "pip_range = (ohlc", None, 30),
    ("py/support_resistance cluster",
     "day0market_support_resistance/pricelevels/cluster.py",
     "def _cluster_prices_to_levels", "return grouped.to_dict", 0),
    ("py/Screeni-py validateConsolidation",
     "pranjal-joshi_Screeni-py/src/classes/Screener.py",
     "def validateConsolidation", "return round((abs((hc-lc)/hc)*100),1)", 0),
    # --- pine ---
    ("pine/Consolidation Zones Live (LonesomeTheBlue)",
     "fmzquant_strategies/Consolidation-Zones-Live.md",
     "if change(pp)", "condlow := min(condlow, low)", 0),
    ("pine/Darvas Box Buy Sell (ceyhun)",
     "fmzquant_strategies/Darvas-Box-Buy-Sell.md",
     "LL = lowest(low, boxp)", "BottomBox = valuewhen", 0),
    ("pine/Darvas Box Breakout+Risk (копия?)",
     "fmzquant_strategies/"
     "Darvas-Box-Breakout-and-Risk-Management-Strategy-Darvas-盒子突破与风险管理策略.md",
     "LL = ta.lowest(low, boxp)", "BottomBox = ta.valuewhen", 0),
    ("pine/52-Week High Low Box (копия?)",
     "fmzquant_strategies/52周高低盒子交易策略52-Week-High-Low-Box-Trading-Strategy.md",
     "LL = lowest(D_Low,boxp)", "BottomBox = valuewhen", 0),
    ("pine/TrendScalp FractalBox (vireshdb)",
     "fmzquant_strategies/TrendScalp-FractalBox-3EMA.md",
     "fractHigh := high[4]", "fractLevelLow := fractLow", 0),
    # --- mql ---
    ("mql/Darvas_Box MT5 (Scriptor)", "mql5/21706_Darvas_Box.mq5.txt",
     "if(state==1)", "state=0;", 0),
    ("mql/darvasboxes MT5 (GODZILLA, копия?)", "mql5/498_darvasboxes.mq5.txt",
     "switch(state)", "state=0; break;", 0),
    ("mql/darvas MT4 (Scriptor, копия?)", "mql5/7771_darvas.mq4.txt",
     "if (state==1)", "state=0;", 0),
    ("mql/BreakOutBox (DarkRyd3r)", "mql5/37415/BreakOutBox.mq5",
     "highest=high[0];", "highest=high[i];", 0),
    ("mql/Ranging Market Detector (phade)", "mql5/57684.txt",
     "bool weak_change = false;", "ZoneMark(i, i, time", 0),
    ("mql/Adaptive S/R Zones (TalalEissa)", "mql5/74421_src.txt",
     "void AddCandidate", None, 30),
    ("mql/SR Zone Scanner (FoxQCW)", "mql5/74492_src.txt",
     "input int    LookbackBars", None, 5),
    ("mql/Support and Resistance (Mullerp04)", "mql5/45132_src.txt",
     "double Resistance(int starting", "return low1;", 0),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    print("=" * 78)
    print("КОНТРОЛЬ ПРИБОРА (без него ноль клонов ничего не значит)")
    print("=" * 78)
    a = "top = highest(high, n)\nbot = lowest(low, n)"
    b = "  TOP   =  highest( high , n )\n\n  // комментарий\n  BOT = lowest( low , n )"
    c = "top = close > sma(close, n)\nbot = close < sma(close, n)"
    same = fingerprint(a) == fingerprint(b)
    diff = fingerprint(a) != fingerprint(c)
    d = "top = ta.highest(high, n)\nbot = ta.lowest(low, n)"
    ported = fingerprint(a) == fingerprint(d)
    print(f"переименование+отступы+комментарий дают ОДИН хеш: {same}  "
          f"({fingerprint(a)} / {fingerprint(b)})")
    print(f"порт Pine v4→v5 (префикс `ta.`) даёт ОДИН хеш:    {ported}  "
          f"({fingerprint(a)} / {fingerprint(d)})")
    print(f"разная логика даёт РАЗНЫЕ хеши:                  {diff}  "
          f"({fingerprint(a)} / {fingerprint(c)})")
    if not (same and diff and ported):
        print("\n⚠ КОНТРОЛЬ ПРОВАЛЕН: нормализатор не различает или склеивает всё подряд.")
        return 1

    # ⚠ Известный ПРЕДЕЛ прибора, найденный на этой же выборке: текстовый хеш ловит
    # копипасту и порт, но НЕ ловит переписывание той же логики другой конструкцией.
    # Три реализации Дарваса на MQL — одна и та же машина состояний, но 21706 и 7771
    # записаны цепочкой `if`, а 498 — через `switch`. Хеши разойдутся; глазами видно, что
    # это одно. Число «семей» ниже — НИЖНЯЯ ОЦЕНКА, а не итог.
    e = "if(v==1) a=1;\nif(v==2) a=2;"
    f = "switch(v){case 1: a=1; break; case 2: a=2; break;}"
    print(f"предел прибора: `if`-цепочка и `switch` НЕ склеиваются: "
          f"{fingerprint(e) != fingerprint(f)} (это НЕ ошибка, это граница метода)")

    print()
    print("=" * 78)
    print("ЯДРА ВЫБОРКИ")
    print("=" * 78)
    groups: dict[str, list[str]] = defaultdict(list)
    missing: list[str] = []
    for label, rel, start, end, span in MANIFEST:
        path = root / rel
        if not path.exists():
            missing.append(f"{label}: нет файла {rel}")
            continue
        try:
            core = cut(read_text(path), start, end, span)
        except LookupError as exc:
            missing.append(f"{label}: {exc}")
            continue
        h = fingerprint(core)
        groups[h].append(label)
        print(f"{h}  строк={len(normalize(core).splitlines()):3d}  {label}")

    print()
    print("=" * 78)
    print("СЕМЬИ (совпавший хеш = один проект)")
    print("=" * 78)
    families = {h: labels for h, labels in groups.items() if len(labels) > 1}
    for h, labels in sorted(families.items()):
        print(f"{h}:")
        for label in labels:
            print(f"    {label}")
    print(f"\nядер прочитано:      {sum(len(v) for v in groups.values())}")
    print(f"различных хешей:     {len(groups)}")
    print(f"семей клонов:        {len(families)}")
    print(f"копий внутри семей:  {sum(len(v) for v in families.values()) - len(families)}")

    if missing:
        print("\nНЕ ПРОЧИТАНО (каждая строка — дыра в проверке, а не ноль клонов):")
        for line in missing:
            print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
