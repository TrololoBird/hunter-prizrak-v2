"""Гейт: каждый файл `gates/*.py` действительно вызывается в CI.

Повод — замер, а не опасение. 2026-08-04 сверка списка файлов со списком шагов
`.github/workflows/ci.yml` показала: из четырнадцати гейтов в CI вписаны десять.
Три (`models_forbid_extra`, `doc_refs`, `repro_commands`) были написаны накануне и НИ
РАЗУ не отработали на чужой машине — они запускались только вручную, тогда, когда о них
помнили. Гейт вне CI неотличим от скрипта: он не мешает влить нарушение.

Проверка тривиальна и потому надёжна: имя файла обязано встретиться в тексте ci.yml.

⚠ Собственную вписанность этот гейт проверяет наравне с остальными, но если ЕГО самого
удалить из CI, проверять станет некому. Дыра сжимается до одного файла и до строки в
diff'е `ci.yml` — совсем убрать её нельзя, и здесь она названа, а не умолчана.
"""

from __future__ import annotations

import sys
from pathlib import Path

CI = Path(".github/workflows/ci.yml")
GATES = Path("gates")


def main() -> int:
    if not CI.exists():
        print(f"ПРОВАЛ: нет {CI} — проверка не состоялась")
        return 1
    text = CI.read_text(encoding="utf-8")
    files = sorted(p.name for p in GATES.glob("*.py") if p.name != "__init__.py")
    missing = [n for n in files if n not in text]
    print(f"гейтов в каталоге {len(files)}, вызываются в CI {len(files) - len(missing)}")
    if missing:
        print(f"ПРОВАЛ: не вызываются в CI — {len(missing)}")
        for n in missing:
            print(f"  gates/{n}")
        return 1
    print("нарушений 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
