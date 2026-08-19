"""Какие страницы курса проект не читал ВОВСЕ.

Повестку чтения задаёт курс, а не ссылки кода. Этот зонд переворачивает обычный вопрос:
не «на что ссылается модуль», а «о чём курс говорит, а код молчит». За один прогон он
нашёл три целых понятия, отсутствующих в проекте: безубыток (стр. 14, 15), доливка
(стр. 16) и почти вся глава ФИГУРЫ (стр. 56, 59-62).

КОНТРОЛЬ. Прибор обязан различать, а не печатать нули: страницы, читанные плотно, дают
частоты в десятках (стр. 18 — 73 упоминания, стр. 25 — 64, стр. 43 — 51). Если бы разбор
ссылок был сломан, нулём стали бы ВСЕ страницы, а не шестнадцать.
"""
from __future__ import annotations

import collections
import pathlib
import re

CITE = re.compile(r"[Сс]тр\.\s?(\d+(?:\s*[,/]\s*\d+)*)")
"""Та же регулярка, что у гейта `course_citations`, включая заглавную «С»: без неё
первая редакция гейта пропускала 53 ссылки из 271 — все, что стоят в начале предложения."""

ROOTS = ("src/hunter", "gates")
PAGES = 69


def main() -> int:
    seen: collections.Counter[int] = collections.Counter()
    files = 0
    for root in ROOTS:
        for p in sorted(pathlib.Path(root).rglob("*.py")):
            files += 1
            for m in CITE.finditer(p.read_text(encoding="utf-8")):
                for n in re.split(r"[,/]", m.group(1)):
                    seen[int(n.strip())] += 1
    print(f"ОТПЕЧАТОК: файлов прочитано {files}, страниц в курсе {PAGES}, "
          f"ссылок всего {sum(seen.values())}")
    never = [n for n in range(1, PAGES + 1) if not seen[n]]
    thin = [n for n in range(1, PAGES + 1) if 0 < seen[n] <= 2]
    print(f"\nНЕ УПОМЯНУТЫ ВОВСЕ — {len(never)} из {PAGES}:")
    print(f"  {never}")
    print(f"\nупомянуты 1-2 раза (прочитано по касательной) — {len(thin)}:")
    print(f"  {thin}")
    print("\nКОНТРОЛЬ — плотно читанные страницы (прибор различает, а не молчит):")
    for n, c in seen.most_common(8):
        print(f"  стр. {n:2}: {c} упоминаний")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
