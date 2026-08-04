"""ГЕЙТ: словари между слоями запрещены. FOUNDATION.md §10.1.

§10.1: в прошлой реализации 910 вхождений `dict[str, Any]` против 85 моделей, и именно
отсюда родился класс «поле без продюсера» — ключ читается, никто его не пишет, ошибка
молчит годами.

Разрешено ровно одно место: граница с ccxt, который отдаёт словари. Граница объявлена
списком ниже — то есть видима, а не растворена по дереву.

Охват печатается числом.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path("src")

# Граница с внешним миром: ccxt возвращает dict, деться от этого некуда.
# Файл в списке — заявление «здесь сырые словари приходят снаружи», а не поблажка
# на внутренние структуры.
BOUNDARY_FILES = {"src/hunter/exchange.py"}


def _is_loose_dict(node: ast.expr) -> bool:
    """dict[str, Any] в любой форме записи."""
    if not isinstance(node, ast.Subscript):
        return False
    base = node.value
    name = base.attr if isinstance(base, ast.Attribute) else (
        base.id if isinstance(base, ast.Name) else None
    )
    if name not in {"dict", "Dict", "Mapping", "MutableMapping"}:
        return False
    sl = node.slice
    if not isinstance(sl, ast.Tuple) or len(sl.elts) != 2:
        return False
    val = sl.elts[1]
    val_name = val.attr if isinstance(val, ast.Attribute) else (
        val.id if isinstance(val, ast.Name) else None
    )
    return val_name == "Any"


def scan(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and _is_loose_dict(node.annotation):
            out.append((node.lineno, "поле объявлено как dict[str, Any]"))
        # ⚠ `ast.FunctionType` тут стоял ошибочно: это узел ТИПОВОГО КОММЕНТАРИЯ
        # (`# type: (int) -> str`), у него нет ни `.name`, ни `.args`. Появись он —
        # гейт упал бы с AttributeError. Не появлялся: `ast.parse` даёт его только в
        # режиме `func_type`. Ошибку нашёл mypy, когда охват расширили на gates/.
        elif isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            if node.returns is not None and _is_loose_dict(node.returns):
                out.append((node.lineno, f"{node.name}: возвращает dict[str, Any]"))
            for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
                if a.annotation is not None and _is_loose_dict(a.annotation):
                    out.append((node.lineno, f"{node.name}: аргумент {a.arg} — dict[str, Any]"))
    return out


def main() -> int:
    files = sorted(ROOT.rglob("*.py"))
    findings: list[str] = []
    skipped = 0
    for p in files:
        if p.as_posix() in BOUNDARY_FILES:
            skipped += 1
            continue
        for line, why in scan(p):
            findings.append(f"{p}:{line}: {why}")
    print(f"гейт «без словарей между слоями»: просмотрено {len(files) - skipped} из "
          f"{len(files)} (граница с ccxt исключена: {sorted(BOUNDARY_FILES)}), "
          f"нарушений {len(findings)}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if not files:
        print("ПРОВАЛ: не просмотрено ни одного файла — проверка не состоялась")
        return 1
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
