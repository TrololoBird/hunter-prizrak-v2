"""ГЕЙТ: чистота расчётных модулей. FOUNDATION.md §10.3.

§10.3 дословно: «Расчёт сигнала — чистые функции от кадров. Никакого обращения к часам,
сети или глобальному состоянию внутри расчёта. Время входит как аргумент, а не берётся
внутри. Никакой случайности.»

Без этого детерминированный повтор невозможен, а повтор — единственный инструмент,
которым владелец может проверить изменение расчёта, не читая код.

Охват печатается числом.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Модули, обязанные быть чистыми. Список растёт с этапами 2–5: примитивы, структура,
# уровни, геометрия сделки — всё это расчёт.
PURE_MODULES = ["src/hunter/bars.py", "src/hunter/models.py"]

# Чего чистый модуль не имеет права импортировать.
FORBIDDEN_IMPORTS = {
    "hunter.clock", ".clock", "clock",
    "hunter.exchange", ".exchange", "exchange",
    "hunter.archive", ".archive", "archive",
    "hunter.store", ".store", "store",
    "ccxt", "ccxt.pro", "urllib", "urllib.request", "socket", "aiohttp",
    "random", "secrets", "time", "datetime",
}


def scan(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_IMPORTS or a.name in FORBIDDEN_IMPORTS:
                    out.append((node.lineno, f"импорт {a.name} в чистом модуле"))
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            if mod in FORBIDDEN_IMPORTS or (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                out.append((node.lineno, f"импорт из {mod} в чистом модуле"))
    return out


def main() -> int:
    files = [Path(p) for p in PURE_MODULES]
    absent = [p for p in files if not p.exists()]
    findings: list[str] = []
    for p in files:
        if not p.exists():
            continue
        for line, why in scan(p):
            findings.append(f"{p}:{line}: {why}")
    print(f"гейт чистоты расчёта: объявлено чистыми {len(files)}, "
          f"просмотрено {len(files) - len(absent)}, нарушений {len(findings)}")
    for a in absent:
        print(f"  ПРОВАЛ: модуль объявлен чистым, но отсутствует: {a}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if not files:
        print("ПРОВАЛ: список чистых модулей пуст — проверка не состоялась")
        return 1
    return 1 if (findings or absent) else 0


if __name__ == "__main__":
    sys.exit(main())
