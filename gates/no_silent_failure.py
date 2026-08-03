"""ГЕЙТ: обработчик ошибки, который глушит сбой. FOUNDATION.md §4.3.

Почему свой обход, а не правило линтера: замер в предыдущей реализации показал, что
ruff S110/S112 видят только `except Exception:` и голый `except:`. На дереве с 94
проглатывающими обработчиками, все из которых типизированы, линтер давал НОЛЬ находок.
Здесь разбор идёт по AST и форма except значения не имеет.

Охват печатается числом: «ОК» без числа проверенных файлов неотличимо от непроведённой
проверки.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = ("src", "gates")


def swallows(handler: ast.ExceptHandler) -> str | None:
    """Тело гасит сбой, если в нём нет ни одного действия, оставляющего след."""
    body = [n for n in handler.body if not isinstance(n, ast.Pass)]
    if not body:
        return "тело пустое (pass)"
    if len(body) == 1 and isinstance(body[0], ast.Continue):
        return "тело — continue"
    if len(body) == 1 and isinstance(body[0], ast.Break):
        return "тело — break"
    if all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in body):
        return "тело — только строка/константа"
    # Есть raise, return, вызов, присваивание — след остаётся, обработчик не «немой».
    return None


def scan(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            why = swallows(node)
            if why:
                out.append((node.lineno, why))
    return out


def main() -> int:
    files = [p for r in ROOTS for p in Path(r).rglob("*.py")]
    findings: list[str] = []
    for p in files:
        for line, why in scan(p):
            findings.append(f"{p}:{line}: {why}")
    print(f"гейт «никакого молчания»: просмотрено файлов {len(files)}, "
          f"обработчиков-глушителей {len(findings)}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if not files:
        print("ПРОВАЛ: не просмотрено ни одного файла — проверка не состоялась")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
