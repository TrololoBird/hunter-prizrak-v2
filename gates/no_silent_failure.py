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

# ⚠ `scripts` добавлен 2026-08-07, находка Э-2 прошлого разбора,
# подтверждённая исполнением: три гейта давали 0 на нарушении в `scripts/`
# и 1 на том же файле в `src/`. `production_writer` написан из-за инцидента,
# где в боевой леджер написал именно СКРИПТ, — и до `scripts/` не дотягивался.
ROOTS = ("src", "gates", "scripts")


def swallows(handler: ast.ExceptHandler) -> str | None:
    """Тело гасит сбой, если в нём нет ни одного действия, оставляющего след.

    ⚠⚠ КРИТЕРИЙ РАСШИРЕН 2026-08-23, И ЭТО ПОЧИНКА ОХВАТА. Прежде «немым» считалось
    только тело из `pass`, одного `continue`, одного `break` или констант — то есть
    ровно четыре формы. Гейт же написан потому, что ruff S110/S112 давали НОЛЬ находок
    на 94 глушителях, а собственный его критерий оказался ненамного шире: `except ...:
    n = 0; continue` проходил как «след остаётся», хотя причина отказа не уходит никуда.

    Добавлена пятая форма: тело, все содержательные операторы которого — присваивания
    ЛОКАЛЬНЫМ именам, а сама причина (`as e`) не упомянута. Такое тело подменяет
    неизвестный результат назначенным значением и молчит — §4.3 это и запрещает.

    ⚠ ГРАНИЦА НАЗВАНА, а не подразумевается. НЕ считаются глушением:
      * `raise`, вызов (`log.degraded`, `print`, `NotReady(...)`) — след очевиден;
      * присваивание АТРИБУТУ или элементу (`self.unnumbered += 1`, `d[k] = ...`) —
        это счётчик или контейнер объекта, то есть отказ, названный ЧИСЛОМ, а число
        предъявляется приёмкой. Правило проекта прямо этого и требует;
      * возврат ЗНАЧЕНИЯ (`return False`, `return set()`) — вызывающий отличит его от
        успеха, и это штатный способ превратить сбой в ответ.

    Контроль 2026-08-23 (замер по `src`, `gates`, `scripts`, 59 файлов): новая форма
    поймала ОДИН обработчик — `run._watch_trades_impl`, где неразбираемый номер сделки
    писал `tid = None` и молча выбывал из поиска разрывов потока. Он исправлен в тот же
    день; прежний критерий его не видел.
    """
    body = [n for n in handler.body if not isinstance(n, ast.Pass)]
    if not body:
        return "тело пустое (pass)"
    if len(body) == 1 and isinstance(body[0], ast.Continue):
        return "тело — continue"
    if len(body) == 1 and isinstance(body[0], ast.Break):
        return "тело — break"
    if all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in body):
        return "тело — только строка/константа"
    # Содержательные операторы: без докстрок-констант и без «просто выйти».
    core = [
        n for n in body
        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        and not isinstance(n, ast.Continue | ast.Break)
        and not (isinstance(n, ast.Return) and n.value is None)
    ]
    if core and all(isinstance(n, ast.Assign | ast.AugAssign | ast.AnnAssign)
                    for n in core):
        targets: list[ast.expr] = []
        for n in core:
            if isinstance(n, ast.Assign):
                targets.extend(n.targets)
            elif isinstance(n, ast.AugAssign | ast.AnnAssign):
                targets.append(n.target)
        mentions_reason = bool(handler.name) and any(
            isinstance(x, ast.Name) and x.id == handler.name
            for n in core for x in ast.walk(n)
        )
        if all(isinstance(t, ast.Name) for t in targets) and not mentions_reason:
            return "тело — только запись в ЛОКАЛЬНОЕ имя: причина отказа никуда не уходит"
    # Есть raise, return значения, вызов, запись в атрибут — след остаётся.
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
