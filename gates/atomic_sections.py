"""ГЕЙТ: участки, объявленные неделимыми, не имеют права ждать. FOUNDATION.md §4.3.

Зачем нужен отдельный гейт под одно слово в докстроке.

Служба 24/7 (§8, находка А-1) собирает данные и считает ОДНОВРЕМЕННО. Расчёт работает не
на живых структурах сбора, а на СНИМКЕ, и правильность снимка держится ровно на одном
условии: пока он снимается, задачи наблюдения не выполняются. В одном цикле событий это
гарантировано, но только пока внутри нет `await` — на нём управление уходит другим
задачам, и срез перестаёт быть срезом одного момента, превращаясь в смесь двух.

Отказ при этом БЕСШУМНЫЙ. Ничего не падает, никаких исключений: просто бары в снимке
относятся к одному моменту, а сделки к другому, и профиль натягивается на окно, которого
не было. Именно поэтому проверка нужна машинная: условие держится на дисциплине
редактирования файла, а дисциплина не проверяется чтением.

Функция объявляет себя неделимой словами «БЕЗ ОЖИДАНИЯ» в докстроке — той же формой, что
`ЧИСТЫЙ МОДУЛЬ` в gates/purity.py. Охват печатается числом: гейт, не нашедший ни одного
объявления, — это не «нарушений нет», а непроведённая проверка.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKER = "БЕЗ ОЖИДАНИЯ"
SOURCE_ROOT = Path("src/hunter")

MIN_DECLARED = 1
"""Сколько объявлений обязано найтись. Ноль означал бы, что метку стёрли вместе с
проверкой, — и гейт зазеленел бы именно в тот момент, когда стал нужен."""


def _waits(node: ast.AST) -> list[tuple[int, str]]:
    """Ожидания ВНУТРИ функции, не заходя во вложенные функции.

    Вложенная корутина, объявленная внутри неделимого участка, ждать имеет право: она
    выполняется не здесь, а тогда, когда её запустят.
    """
    out: list[tuple[int, str]] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(cur, ast.Await):
            out.append((cur.lineno, "await"))
        elif isinstance(cur, ast.AsyncFor):
            out.append((cur.lineno, "async for"))
        elif isinstance(cur, ast.AsyncWith):
            out.append((cur.lineno, "async with"))
        stack.extend(ast.iter_child_nodes(cur))
    return out


def scan(path: Path) -> tuple[list[str], list[str]]:
    """(объявившие себя неделимыми, нарушения)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declared: list[str] = []
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if MARKER not in (ast.get_docstring(node) or ""):
            continue
        declared.append(f"{path}:{node.lineno}:{node.name}")
        for line, what in _waits(node):
            findings.append(f"{path}:{line}: `{what}` в участке {node.name}(), "
                            f"объявленном «{MARKER}»")
    return declared, findings


def main() -> int:
    files = sorted(SOURCE_ROOT.rglob("*.py"))
    declared: list[str] = []
    findings: list[str] = []
    for p in files:
        found_declared, found_bad = scan(p)
        declared.extend(found_declared)
        findings.extend(found_bad)

    print(f"гейт неделимых участков: просмотрено файлов {len(files)}, "
          f"объявлено «{MARKER}» {len(declared)}, нарушений {len(findings)}")
    for where in declared:
        print(f"  участок {where}")
    for why in findings:
        print(f"  НАРУШЕНИЕ {why}")
    if not files:
        print("ПРОВАЛ: не просмотрено ни одного файла — проверка не состоялась")
        return 1
    if len(declared) < MIN_DECLARED:
        print(f"ПРОВАЛ: объявлений найдено {len(declared)}, ожидалось не меньше "
              f"{MIN_DECLARED} — метку стёрли вместе с проверкой")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
