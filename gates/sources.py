"""ГЕЙТ: комплектность источников. FOUNDATION.md §0.1.

Правило владельца 2026-08-03: основа метода — мини-курс PrizrakTrade; всё остальное —
из авторитетных внешних источников, МИНИМУМ ТРИ на элемент, курс в эти три не входит.

Проверяется:
  1. у каждого элемента не меньше трёх источников;
  2. источники уровня 5 (блог, форум, сводка поисковой выдачи) не засчитываются вовсе;
  3. хотя бы один источник уровня 1–3 (замер, первоисточник, отраслевой эталон);
  4. источники не повторяются по домену — три ссылки на один сайт это один источник;
  5. у каждого есть дата, дословная цитата и пометка о способе получения.

Охват печатается числом.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

REGISTRY = Path("docs/sources.toml")
MIN_SOURCES = 3          # §0.1 дословно
NOT_A_SOURCE_TIER = 5    # §0.1: блог источником не является
STRONG_TIER_MAX = 3      # уровни 1–3 считаются сильными


def domain(url: str) -> str:
    if "://" not in url:
        return f"локально:{url}"  # замер или файл проекта
    return urlparse(url).netloc.lower()


def main() -> int:
    if not REGISTRY.exists():
        print(f"ПРОВАЛ: нет реестра {REGISTRY} — проверка не состоялась")
        return 1
    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    elements = data.get("element", [])
    if not elements:
        print("ПРОВАЛ: в реестре нет ни одного элемента — проверка не состоялась")
        return 1

    print(f"гейт комплектности источников: элементов {len(elements)}, "
          f"требуется {MIN_SOURCES} независимых на элемент")
    failed = 0
    for el in elements:
        name = el.get("name", "<без имени>")
        srcs = el.get("source", [])
        problems: list[str] = []

        for s in srcs:
            for field in ("url", "tier", "date", "quote", "fetched"):
                if not s.get(field):
                    problems.append(f"источник без поля {field}: {s.get('url', '?')}")

        counted = [s for s in srcs if int(s.get("tier", 9)) < NOT_A_SOURCE_TIER]
        dropped = len(srcs) - len(counted)
        domains = {domain(str(s["url"])) for s in counted if s.get("url")}
        strong = [s for s in counted if int(s.get("tier", 9)) <= STRONG_TIER_MAX]

        if len(domains) < MIN_SOURCES:
            problems.append(
                f"независимых источников {len(domains)}, нужно {MIN_SOURCES}"
            )
        if not strong:
            problems.append("нет ни одного источника уровня 1-3")

        mark = "OK " if not problems else "НЕТ"
        print(f"  {mark} {name:24} источников {len(srcs)} "
              f"(засчитано {len(domains)}, отброшено уровня 5: {dropped}, "
              f"сильных {len(strong)})")
        for p in problems:
            print(f"      НАРУШЕНИЕ {p}")
            failed += 1

    print(f"итого нарушений: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
