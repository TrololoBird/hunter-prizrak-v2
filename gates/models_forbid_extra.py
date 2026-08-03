"""ГЕЙТ: у каждой модели `extra="forbid"`. FOUNDATION.md §10.1, §4.3.

§10.1 обещает: типизированная модель делает класс «поле без продюсера» невозможным,
потому что mypy падает на несуществующем атрибуте. Обещание неполно — на ЗАПИСИ pydantic
по умолчанию лишнее поле молча ИГНОРИРУЕТ, а не отвергает.

Замер 2026-08-03: конструктор `StructureExit(..., bodies=bodies)` с несуществующим полем
`bodies` отработал без единого сообщения, значение исчезло. Прогон был зелёный; поймал
mypy, но только потому, что вызов оказался в проверяемом файле — данные из TOML, JSON или
биржи mypy не видит вовсе.

`extra="forbid"` закрывает именно этот путь. Гейт проверяет наличие, потому что проза,
как показал сам этот случай, не удерживает.

Охват печатается числом: молчаливое «OK» без числа неотличимо от «ничего не просмотрено».
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = (Path("src/hunter"),)


def has_forbid(node: ast.ClassDef) -> bool:
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        for kw in stmt.value.keywords:
            if kw.arg == "extra" and isinstance(kw.value, ast.Constant):
                return bool(kw.value.value == "forbid")
    return False


def is_model(node: ast.ClassDef) -> bool:
    return any(isinstance(b, ast.Name) and b.id == "BaseModel" for b in node.bases)


def main() -> int:
    files = sorted(p for root in ROOTS for p in root.rglob("*.py"))
    if not files:
        print("ПРОВАЛ: не найдено ни одного файла — проверка не состоялась")
        return 1

    models = 0
    bad: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and is_model(node):
                models += 1
                if not has_forbid(node):
                    bad.append(f"{path}:{node.lineno} {node.name}")

    print(f'гейт extra="forbid": файлов {len(files)}, моделей {models}, нарушений {len(bad)}')
    for b in bad:
        print(f"  НАРУШЕНИЕ нет extra=\"forbid\": {b}")
    if models == 0:
        print("ПРОВАЛ: моделей не найдено — проверка не состоялась")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
