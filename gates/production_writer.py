"""ГЕЙТ: единственный писатель в боевую базу. FOUNDATION.md §10.2, §6.

§10.2: «Боевая база открывается на запись только боевым процессом; всё прочее —
read-only соединение». §6: «Единственная точка эмиссии карточек. Скрипты никогда не
пишут в боевые данные».

Инцидент, ради которого это: verify-скрипт записал фикстуры в боевой леджер, фильтр
загрязнения счёл их живыми сделками, отчёт показал +44,29% средней прибыли на файле,
состоявшем из выдумки целиком.

СУБД держит ограничение (все прочие соединения `mode=ro`); гейт держит список тех, кому
вообще позволено звать писательскую функцию. Охват печатается числом.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = ("src", "gates")
WRITER = "open_production_ledger"

# Кто имеет право звать писателя. Расширение списка — осознанное решение, видимое в дифе.
ALLOWED = {
    "src/hunter/store.py",     # объявление и init_ledger
    "src/hunter/__main__.py",  # боевая точка входа
}


def scan(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None
            )
            if name == WRITER:
                out.append(node.lineno)
    return out


def main() -> int:
    files = [p for r in ROOTS for p in sorted(Path(r).rglob("*.py"))]
    findings: list[str] = []
    callers: list[str] = []
    for p in files:
        if p.name == Path(__file__).name:
            continue
        for line in scan(p):
            where = f"{p.as_posix()}:{line}"
            callers.append(where)
            if p.as_posix() not in ALLOWED:
                findings.append(f"{where}: {WRITER} вне списка разрешённых")
    print(f"гейт «единственный писатель»: просмотрено файлов {len(files) - 1}, "
          f"вызовов {WRITER} найдено {len(callers)}, вне списка {len(findings)}")
    for c in callers:
        print(f"  вызов: {c}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if not files:
        print("ПРОВАЛ: не просмотрено ни одного файла — проверка не состоялась")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
