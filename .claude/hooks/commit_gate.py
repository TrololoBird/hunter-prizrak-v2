"""ХУК: коммит невозможен при красных гейтах. CLAUDE.md, раздел «Проверка».

Зачем это существует. В CLAUDE.md записано дословно:

    «Перед коммитом гонять именно scripts/check.sh… Я так сделал ДВАЖДЫ за сутки
    (2026-08-04), оба раза провал был напечатан и оба раза проехал.
    Печать — не гейт; гейт — это код возврата.»

Правило было, соблюдал его я, и дважды не соблюл. Хук снимает вопрос: `PreToolUse` с
кодом возврата 2 ОТМЕНЯЕТ вызов инструмента, то есть `git commit` просто не выполнится.

⚠ ОТКАЗ САМОГО ХУКА ТОЖЕ БЛОКИРУЕТ. Если проверку не удалось запустить — нет `bash`,
не найден скрипт, — коммит запрещается, а не разрешается. Гейт, который при собственной
поломке пропускает, — это не гейт (§4.3: молчаливый пропуск запрещён).

Написан на Python, а не на bash, по замеренной причине: на машине владельца `python` из
Microsoft Store — заглушка, которая молча ничего не делает, поэтому запуск идёт через
`uv run python`, а сам скрипт кроссплатформенный.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

GATES = "scripts/check.sh"
TAIL_LINES = 25
"""Сколько строк провала показать. Весь вывод — сотни строк; нужен хвост с причиной."""


def _block(reason: str) -> NoReturn:
    """Отменить вызов. ⚠ Возвращаемый тип `NoReturn`, а не `None`, и это не украшение.

    Найдено CodeQL 2026-08-10 (`py/uninitialized-local-variable`, уровень error): при
    `-> None` анализатор считает, что после `_block(...)` в ветке `except` выполнение
    ПРОДОЛЖИТСЯ, и тогда ниже читается неприсвоенная `event`. Сегодня этого не
    случается — внутри `sys.exit(2)`, — но контракт функции об этом молчал, и первая же
    правка, убравшая выход, дала бы `UnboundLocalError` в хуке, который защищает
    коммиты. `NoReturn` делает обещание проверяемым: и mypy, и CodeQL теперь знают, что
    возврата нет.
    """
    print(reason, file=sys.stderr)
    sys.exit(2)


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _block(f"хук гейтов: не разобран вход ({e}). Коммит остановлен: проверка не "
               f"состоялась, а непроведённая проверка не пропускает.")

    command = str(event.get("tool_input", {}).get("command", ""))
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")

    # Подстраховка: хук объявлен с `if` на `git commit`, но полагаться только на неё
    # нельзя — правило могут изменить, а этот файл останется.
    if "git commit" not in command:
        return 0
    if "--no-verify" in command:
        _block("хук гейтов: `--no-verify` не обходит проверку. Гейты обязаны быть "
               "зелёными до коммита — CLAUDE.md, раздел «Проверка».")

    script = root / GATES
    if not script.is_file():
        _block(f"хук гейтов: не найден {script}. Коммит остановлен — проверить нечем.")

    bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
    if not Path(bash).is_file() and shutil.which("bash") is None:
        _block("хук гейтов: `bash` не найден, запустить scripts/check.sh нечем. "
               "Коммит остановлен: непроведённая проверка не пропускает.")

    # Путь к `bash` и имя скрипта заданы здесь, а не приходят извне: команда из
    # события используется только для решения «это коммит или нет».
    done = subprocess.run(
        [bash, GATES], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if done.returncode == 0:
        return 0

    tail = "\n".join((done.stdout + done.stderr).splitlines()[-TAIL_LINES:])
    _block(
        "КОММИТ ОСТАНОВЛЕН: гейты красные.\n"
        f"`bash {GATES}` вернул код {done.returncode}. Последние строки:\n\n{tail}\n\n"
        "Чинить причину, а не обходить проверку."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
