"""ГЕЙТ: конфигурация Claude Code проверяется машиной, а не глазами.

CLAUDE.md, раздел «Проверка»: правило исполняет ХУК, а не дисциплина. Но сам хук
объявлен строкой в `.claude/settings.json`, и эта строка ничем не проверялась. Формы
отказа, каждая из которых МОЛЧАЛИВА (§4.3) и потому опаснее падения:

  1. опечатка в имени ключа. Приложение игнорирует неизвестный ключ без единого слова:
     настройка выглядит заданной, а её нет. Сверка идёт со списком признаваемых ключей
     `config/claude-code-settings-keys.txt`, снятым из машиночитаемой схемы;
  2. хук объявлен, а файла нет. `PreToolUse` отменяет вызов ТОЛЬКО кодом 2; запуск
     несуществующего файла даёт код 1, то есть `git commit` при красных гейтах пройдёт.
     Ровно то, против чего хук и написан;
  3. файл хука есть, а в настройках не объявлен — хук не запускается никогда. Это
     аргумент `ci_covers_gates.py` («гейт вне CI — не гейт»), применённый к хукам;
  4. навык с битой шапкой. Claude Code берёт `name` и `description` из YAML-шапки
     `SKILL.md`; без них навык не поднимется, и узнать об этом можно только по тому,
     что он «почему-то не срабатывает»;
  5. сервер MCP без `type`. Документация называет это ошибкой конфигурации: запись с
     `url`, но без `type`, читается как stdio-сервер и пропускается.

Охват печатается числом по каждому классу: «нарушений 0» без числа проверенного
неотличимо от непроведённой проверки.

Контроль обратной стороны (CLAUDE.md: способен ли прибор выдать ИНОЙ ответ) —
`docs/audit/claude-desktop-2026-08-06.md`, раздел «Контроль гейта».
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

SETTINGS = Path(".claude/settings.json")
MCP = Path(".mcp.json")
KNOWN_KEYS = Path("config/claude-code-settings-keys.txt")
HOOKS_DIR = Path(".claude/hooks")
SKILLS_DIR = Path(".claude/skills")
COMMANDS_DIR = Path(".claude/commands")

# Значения permissions.defaultMode из той же схемы, что и список ключей.
DEFAULT_MODES = {"acceptEdits", "bypassPermissions", "default", "delegate",
                 "dontAsk", "plan", "auto", "manual"}
# Транспорты, для которых запись обязана нести `url`. `streamable-http` — псевдоним
# `http` из спецификации MCP, приложение принимает оба имени.
URL_TRANSPORTS = {"http", "sse", "ws", "streamable-http"}

HOOK_REF = re.compile(r"\.claude/hooks/([A-Za-z0-9_-]+\.py)")
SKILL_NAME = re.compile(r"^[a-z0-9-]{1,64}$")
# Зарезервированные слова в имени навыка — требование формата Agent Skills.
RESERVED_IN_NAME = ("anthropic", "claude")
MAX_DESCRIPTION = 1024


def load_json(path: Path, bad: list[str]) -> dict[str, Any] | None:
    """Прочитать JSON. Любая беда — нарушение с названной причиной, а не пустой словарь."""
    if not path.exists():
        bad.append(f"нет файла {path.as_posix()} — проверять нечего")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        bad.append(f"{path.as_posix()}: не разобран как JSON ({e})")
        return None
    if not isinstance(data, dict):
        bad.append(f"{path.as_posix()}: на верхнем уровне не объект")
        return None
    return data


def known_keys(bad: list[str]) -> set[str]:
    if not KNOWN_KEYS.exists():
        bad.append(f"нет списка ключей {KNOWN_KEYS.as_posix()} — сверять не с чем")
        return set()
    lines = KNOWN_KEYS.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def strings_of(node: Any) -> list[str]:
    """Все строки дерева JSON. Обход общий: структура хуков меняется, ссылки — нет."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [s for item in node for s in strings_of(item)]
    if isinstance(node, dict):
        return [s for item in node.values() for s in strings_of(item)]
    return []


def frontmatter(text: str) -> dict[str, str] | None:
    """Шапка YAML в простейшей форме `ключ: значение`. Многострочных значений нет."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    out: dict[str, str] = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            return out
        key, sep, value = ln.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            out[key.strip()] = value.strip()
    return None  # шапка не закрыта


def check_settings(bad: list[str]) -> tuple[int, int]:
    """Ключи настроек и ссылки на хуки. Возвращает (ключей, хуков)."""
    data = load_json(SETTINGS, bad)
    if data is None:
        return 0, 0
    allowed = known_keys(bad)
    for key in sorted(data):
        if allowed and key not in allowed:
            bad.append(f"{SETTINGS.as_posix()}: ключ {key!r} не признаётся приложением "
                       f"— оно молча его проигнорирует")
    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        mode = permissions.get("defaultMode")
        if mode is not None and mode not in DEFAULT_MODES:
            bad.append(f"{SETTINGS.as_posix()}: permissions.defaultMode={mode!r} — "
                       f"такого режима нет, допустимы {sorted(DEFAULT_MODES)}")

    declared: set[str] = set()
    for s in strings_of(data):
        declared.update(HOOK_REF.findall(s))
    for name in sorted(declared):
        if not (HOOKS_DIR / name).is_file():
            bad.append(f"{SETTINGS.as_posix()}: объявлен хук {name}, файла нет. "
                       f"Запуск отсутствующего файла даёт код 1, а отменяет вызов только 2")
    on_disk = {p.name for p in HOOKS_DIR.glob("*.py")} if HOOKS_DIR.is_dir() else set()
    for name in sorted(on_disk - declared):
        bad.append(f"{HOOKS_DIR.as_posix()}/{name}: файл есть, в настройках не объявлен "
                   f"— значит не запускается никогда")
    return len(data), len(declared | on_disk)


def check_mcp(bad: list[str]) -> int:
    if not MCP.exists():
        return 0
    data = load_json(MCP, bad)
    if data is None:
        return 0
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        bad.append(f"{MCP.as_posix()}: нет объекта mcpServers")
        return 0
    for name, spec in sorted(servers.items()):
        if not isinstance(spec, dict):
            bad.append(f"{MCP.as_posix()}: сервер {name} описан не объектом")
            continue
        kind = spec.get("type")
        if kind is None:
            bad.append(f"{MCP.as_posix()}: у сервера {name} нет type — запись с url и "
                       f"без type читается как stdio и пропускается")
        elif kind in URL_TRANSPORTS and not spec.get("url"):
            bad.append(f"{MCP.as_posix()}: у сервера {name} тип {kind}, но нет url")
        elif kind == "stdio" and not spec.get("command"):
            bad.append(f"{MCP.as_posix()}: у сервера {name} тип stdio, но нет command")
    return len(servers)


def check_skills(bad: list[str]) -> int:
    if not SKILLS_DIR.is_dir():
        return 0
    found = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    for path in found:
        head = frontmatter(path.read_text(encoding="utf-8"))
        if head is None:
            bad.append(f"{path.as_posix()}: нет закрытой шапки YAML — навык не поднимется")
            continue
        name = head.get("name", "")
        desc = head.get("description", "")
        folder = path.parent.name
        if not SKILL_NAME.match(name):
            bad.append(f"{path.as_posix()}: имя {name!r} — нужны строчные латинские "
                       f"буквы, цифры и дефис, не длиннее 64")
        if name != folder:
            bad.append(f"{path.as_posix()}: имя {name!r} не совпадает с каталогом {folder!r}")
        if any(word in name for word in RESERVED_IN_NAME):
            bad.append(f"{path.as_posix()}: имя {name!r} содержит зарезервированное слово")
        if not desc:
            bad.append(f"{path.as_posix()}: пустое description — по нему навык и находят")
        elif len(desc) > MAX_DESCRIPTION:
            bad.append(f"{path.as_posix()}: description длиной {len(desc)}, предел "
                       f"{MAX_DESCRIPTION}")
    return len(found)


def check_commands(bad: list[str]) -> int:
    if not COMMANDS_DIR.is_dir():
        return 0
    found = sorted(COMMANDS_DIR.glob("*.md"))
    for path in found:
        head = frontmatter(path.read_text(encoding="utf-8"))
        if head is None or not head.get("description"):
            bad.append(f"{path.as_posix()}: нет описания в шапке — в списке команд "
                       f"владелец увидит голое имя (§7.5)")
    return len(found)


def main() -> int:
    bad: list[str] = []
    keys, hooks = check_settings(bad)
    servers = check_mcp(bad)
    skills = check_skills(bad)
    commands = check_commands(bad)

    print(f"гейт конфигурации Claude Code: ключей настроек {keys}, хуков {hooks}, "
          f"серверов MCP {servers}, навыков {skills}, команд {commands}, "
          f"нарушений {len(bad)}")
    for b in bad:
        print(f"  НАРУШЕНИЕ {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
