"""ГЕЙТ: каждый модуль импортируется.

Ловит удаление живого модуля. Импорта пакета для этого мало — он не заметит пропавший
подмодуль, поэтому импортируется каждый файл поимённо. Охват печатается числом.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path("src")


def module_names() -> list[str]:
    out: list[str] = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts[-1] == "__main__":
            continue  # исполняется, а не импортируется
        if parts:
            out.append(".".join(parts))
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    names = module_names()
    failed: list[str] = []
    for n in names:
        try:
            importlib.import_module(n)
        except Exception as e:
            failed.append(f"{n}: {type(e).__name__}: {e}")
    print(f"смоук-импорт: модулей {len(names)}, провалов {len(failed)}")
    for f in failed:
        print(f"  ПРОВАЛ {f}")
    if not names:
        print("ПРОВАЛ: не найдено ни одного модуля — проверка не состоялась")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
