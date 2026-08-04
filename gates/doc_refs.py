"""ГЕЙТ: ссылки в документации указывают на существующее. FOUNDATION.md §7.2.

Прошлый проект держал 22 несуществующих пути в `docs/` и правило «ссылайся символом, а не
номером строки» — потому что 6 ссылок из 8 умерли за неделю. Здесь то же лечится гейтом,
а не дисциплиной.

Проверяется:
  * путь в обратных кавычках или в markdown-ссылке — файл существует;
  * ссылка вида `file.py::symbol` — файл существует И символ в нём объявлен.

Охват печатается числом: «битых 0» без числа проверенных неотличимо от «ничего не смотрели».
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOTS = (Path("docs"), Path("CLAUDE.md"), Path("research/author_markup"))

PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|toml|txt|yml|json))`")
SYM_RE = re.compile(r"`([A-Za-z0-9_/.]+\.py)::([A-Za-z_][A-Za-z0-9_.]*)`")
MDLINK = re.compile(r"\]\(([^)]+\.(?:md|py|toml|txt))\)")

# Где искать упомянутый файл. Документация ссылается коротким именем, и это нормально:
# требовать полный путь значило бы ломать текст ради инструмента.
SEARCH = (Path(), Path("src"), Path("src/hunter"), Path("gates"),
          Path("docs"), Path("research/prizrak_corpus"), Path("config"))

# Пути, которых в ЭТОМ дереве нет умышленно: протокол переноса описывает, что осталось
# в старом проекте. Список явный, чтобы исключение было видно, а не молчало.
KNOWN_ABSENT = {
    "scripts/ingest_manipulation_video.py",  # старый проект: чем собирался корпус
    "docs/PRIZRAK_METHODOLOGY.md",           # старый проект: не переносился
    ".razbor.md",                            # это маска имени, а не путь
}


def symbols_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def resolve(ref: str, near: Path) -> Path | None:
    for base in (near.parent, *SEARCH):
        cand = base / ref
        if cand.exists():
            return cand
    tail = Path(ref).name
    for base in SEARCH:
        if (base / tail).exists():
            return base / tail
    return None


def main() -> int:
    files = []
    for r in ROOTS:
        if r.is_file():
            files.append(r)
        elif r.is_dir():
            files += sorted(r.rglob("*.md"))
    if not files:
        print("ПРОВАЛ: документации не найдено — проверка не состоялась")
        return 1

    paths = syms = 0
    bad: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for ref in sorted(set(PATH_RE.findall(text)) | set(MDLINK.findall(text))):
            if ref.startswith("http") or ref in KNOWN_ABSENT:
                continue
            paths += 1
            if resolve(ref, f) is None:
                bad.append(f"{f.as_posix()}: нет файла {ref}")
        for mod, sym in sorted(set(SYM_RE.findall(text))):
            syms += 1
            hit = resolve(mod, f)
            if hit is None:
                bad.append(f"{f.as_posix()}: нет файла {mod} (ссылка {mod}::{sym})")
            elif sym.split(".")[0] not in symbols_of(hit):
                bad.append(f"{f.as_posix()}: в {hit.as_posix()} нет символа {sym}")

    print(f"гейт ссылок документации: файлов {len(files)}, путей {paths}, "
          f"символов {syms}, исключений {len(KNOWN_ABSENT)}, битых {len(bad)}")
    for b in bad:
        print(f"  БИТАЯ ССЫЛКА {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
