"""ХУК: что за инструкции реально попали в контекст и ПОЧЕМУ.

⚠⚠ ЗАВЕДЁН 2026-08-28, ЧТОБЫ ЗАКРЫТЬ УТВЕРЖДЕНИЕ, КОТОРОЕ ПРОЕКТ ДЕЛАЛ БЕЗ ЗАМЕРА.
В `CLAUDE.md` написано про `.claude/rules/*.md`: «файлы грузятся САМИ, когда открыт
подходящий файл, и не занимают контекст в остальное время». Это НИКОГДА не проверялось,
а в трекере Claude Code открыт баг #16299 ровно об обратном: правила с `paths:` грузятся
ГЛОБАЛЬНО при старте, вопреки фронтматтеру. Есть и #23478 — правило не грузится на Write,
только на Read. Цена ошибки не теоретическая: сегодняшний вынос разделов из свода в
правила имел бы смысл, только если условная загрузка работает.

Событие `InstructionsLoaded` отдаёт `reason`, и он прямо различает случаи:
`session_start` — загружено безусловно при старте; `path_glob_match` — сработал глоб;
`nested_traversal` — вложенный CLAUDE.md; `include` — импорт; `compact` — перезагрузка
после сжатия. Один прогон — и вопрос закрыт числом, а не догадкой.

⚠ ЭТО НЕ ГЕЙТ. Он ничего не блокирует и не может: для этого события код возврата
игнорируется по документации. Он ничего не утверждает о НАШЕМ коде — он измеряет
поведение СРЕДЫ, то есть внешний объект. Запрет «зонд ИИ не проверяет код ИИ» сюда не
распространяется: проверяемое написано не нами.

⚠ ЖУРНАЛ ЛЕЖИТ ВНЕ РЕПОЗИТОРИЯ — в `~/.claude/`. Каталоги `evidence/` и им подобные
запрещены сводом, и 2026-08-28 выяснилось, что один такой уже завёлся заново и был спрятан
правилами `.gitignore`. Повторять это нельзя.

⚠ У ЖУРНАЛА ЕСТЬ ПОТОЛОК. В тот же день найдено, что в проекте нет ни одного правила
хранения: 3.2 ГБ в `data/` растут монотонно и молча. Заводить прибор без потолка — значит
повторить тот же дефект собственной рукой.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

LOG = Path.home() / ".claude" / "instructions-loaded.log"

MAX_LINES = 2000
"""Потолок журнала. При превышении хвост сохраняется, голова отбрасывается.

Число выбрано так: одна сессия грузит порядка десяти файлов инструкций, значит потолка
хватает примерно на две сотни сессий — больше, чем нужно, чтобы увидеть закономерность,
и достаточно мало, чтобы файл не рос вечно. Урезание НАЗЫВАЕТСЯ строкой в самом журнале,
а не происходит молча.
"""


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        # ⚠ НЕ молча — по образцу `lint_edited`. Хук диагностический, но неразобранный
        # вход означает «замер не состоялся», и это надо видеть, а не считать нулём.
        print(f"хук instructions_loaded: вход не разобран ({e})", file=sys.stderr)
        return 0

    # ⚠⚠ ПОЛЯ ВЗЯТЫ У ЖИВОГО СОБЫТИЯ, А НЕ У ДОКУМЕНТАЦИИ. Замер 2026-08-28, первые два
    # срабатывания: документация обещает `reason` и `file_contents`, а приходит
    # `load_reason`, `file_contents` НЕТ ВОВСЕ, зато есть неописанные `globs`,
    # `memory_type` и `trigger_file_path`. Полный набор ключей живого события:
    # cwd, file_path, globs, hook_event_name, load_reason, memory_type, prompt_id,
    # session_id, transcript_path, trigger_file_path.
    #
    # Это ровно правило проекта об источнике №4, только про другую библиотеку:
    # руководство описывает НАМЕРЕНИЕ, живой ответ описывает ПОВЕДЕНИЕ. `reason`
    # оставлен запасным чтением на случай, если схема вернётся к описанной.
    path = str(event.get("file_path", ""))
    reason = str(event.get("load_reason") or event.get("reason") or "?")
    kind = str(event.get("memory_type", ""))
    trigger = str(event.get("trigger_file_path", ""))
    globs = event.get("globs") or []

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    try:
        shown = str(Path(path).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        shown = path

    # Исключение из запрета `datetime.now` — сознательное, и вот основание. Запрет стоит
    # ради §6: всякое сравнение «сейчас против биржевой метки» обязано идти через
    # `hunter.clock`. Здесь нет ни биржи, ни сравнения — это отметка в журнале
    # диагностики, живущем вне боевого пакета, и часы проекта тут недоступны в принципе:
    # хук поднимается `uv run --no-project`, то есть без пакета `hunter` вовсе.
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: TID251

    # ⚠ НЕЗНАКОМЫЕ КЛЮЧИ НАЗЫВАЮТСЯ, А НЕ ПРОГЛАТЫВАЮТСЯ. Схему события уже один раз
    # пришлось узнавать замером; если она сменится снова, журнал скажет об этом сам,
    # а не начнёт молча писать пустые поля.
    known = {"cwd", "file_path", "globs", "hook_event_name", "load_reason", "memory_type",
             "prompt_id", "reason", "session_id", "transcript_path", "trigger_file_path",
             "file_contents"}
    extra = sorted(set(event) - known) if isinstance(event, dict) else []

    line = f"{stamp}  {reason:16}  {kind:10}  {shown}"
    if trigger:
        line += f"  ← прочитан {trigger}"
    if globs:
        line += f"  [глоб {';'.join(str(g) for g in globs)}]"
    if extra:
        line += f"  | НОВЫЕ КЛЮЧИ: {','.join(extra)}"

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        old = LOG.read_text(encoding="utf-8").splitlines() if LOG.is_file() else []
        old.append(line)
        if len(old) > MAX_LINES:
            cut = len(old) - MAX_LINES
            old = [f"# ...отброшено {cut} строк по потолку MAX_LINES={MAX_LINES}",
                   *old[cut:]]
        LOG.write_text("\n".join(old) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"хук instructions_loaded: журнал не записан ({e})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
