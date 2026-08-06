"""ХУК: ruff по ТОЛЬКО ЧТО изменённому файлу, сразу после правки.

Замер 2026-08-06 на этой машине: `ruff check <один файл>` — 1 с, `mypy` целиком — 3 с,
`bash scripts/check.sh` целиком — 45 с. Отсюда разделение труда:

  * здесь — только `ruff` и только по изменённому файлу. Секунда на правку, ошибка
    видна там же, где сделана;
  * `mypy` СЮДА НЕ ВХОДИТ намеренно, хотя и быстр. Он проверяет проект целиком, а
    посреди правки на несколько файлов половина проекта заведомо не сходится — хук
    ругался бы на каждое промежуточное состояние и научил бы себя игнорировать;
  * всё остальное — в `commit_gate.py`, где проверка полная и обязательная.

`PostToolUse` заблокировать ничего не может: инструмент уже отработал. Код возврата 2
здесь означает не запрет, а ПОКАЗАТЬ НАЙДЕННОЕ агенту (см. документацию по хукам).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WATCHED_ROOTS = ("src", "scripts", "gates")


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        # ⚠ НЕ молча. Эта ветка уже один раз съела проверку целиком (2026-08-06:
        # пробник подал JSON с неэкранированными обратными слэшами, хук вышел с нулём,
        # и «проверка прошла» означало «проверка не состоялась»). §4.3.
        print(f"хук ruff: вход не разобран ({e}) — файл НЕ ПРОВЕРЕН", file=sys.stderr)
        return 2

    path = str(event.get("tool_input", {}).get("file_path", ""))
    if not path.endswith(".py"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    try:
        rel = target.resolve().relative_to(root.resolve())
    except ValueError:
        # ⚠ Путь не лежит внутри проекта. В работе такого быть не должно: Claude Code
        # передаёт абсолютный путь. Но выходить отсюда МОЛЧА нельзя — так эта ветка уже
        # один раз съела проверку (2026-08-06, пробник подал путь в стиле Git Bash
        # `/c/Users/...`, и хук «прошёл», не проверив ничего). §4.3: пропуск называется.
        print(f"хук ruff: {path} вне корня {root} — НЕ ПРОВЕРЕН", file=sys.stderr)
        return 2
    if rel.parts[0] not in WATCHED_ROOTS:
        return 0

    # Команда фиксирована, путь получен через `relative_to` от корня проекта.
    done = subprocess.run(
        ["uv", "run", "ruff", "check", str(rel)], cwd=root,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if done.returncode == 0:
        return 0
    print(f"ruff по {rel}:\n{done.stdout}{done.stderr}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
