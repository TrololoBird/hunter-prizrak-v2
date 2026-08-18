"""ГЕЙТ: чистота расчётных модулей. FOUNDATION.md §10.3.

§10.3 дословно: «Расчёт сигнала — чистые функции от кадров. Никакого обращения к часам,
сети или глобальному состоянию внутри расчёта. Время входит как аргумент, а не берётся
внутри. Никакой случайности.»

Без этого детерминированный повтор невозможен, а повтор — единственный инструмент,
которым владелец может проверить изменение расчёта, не читая код.

Охват печатается числом.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Модули, обязанные быть чистыми. Список растёт с этапами 2–5: примитивы, структура,
# уровни, геометрия сделки — всё это расчёт.
PURE_MODULES = [
    "src/hunter/bars.py",
    "src/hunter/models.py",
    "src/hunter/admission.py",
    "src/hunter/volume_profile.py",
    "src/hunter/profile_source.py",
    "src/hunter/swings.py",
    "src/hunter/accumulation.py",
    "src/hunter/levels.py",
    "src/hunter/stop_volume.py",
    "src/hunter/breach.py",
    "src/hunter/pereprior.py",
    "src/hunter/absorption.py",
    "src/hunter/geometry.py",
    "src/hunter/priority.py",
    "src/hunter/factors.py",
    "src/hunter/card.py",
    "src/hunter/outcome.py",
    "src/hunter/emit.py",
    "src/hunter/engine.py",
]

# ⚠ `replay.py` из списка УБРАН 2026-08-04, и это признание, а не послабление. Он читает
# кадры с диска и срез архива — то есть делает ввод-вывод по определению своей задачи.
# Чистой обязана быть `card.render`, которую он зовёт, и она в списке. Пока гейт не видел
# формы `from . import X`, модуль числился чистым и печатался как проверенный, импортируя
# `archive` и `store` — оба в собственном списке запрещённых этого гейта.

# Список выше ведётся руками, и 2026-08-03 он отстал: `accumulation.py` объявил себя
# чистым в докстроке, в список не попал, и гейт напечатал «нарушений 0», ни разу его не
# открыв. Охват проверки сам является утверждением — поэтому расхождение между «модуль
# объявил себя чистым» и «модуль в списке» теперь провал, а не тишина.
PURITY_MARKER = "ЧИСТЫЙ МОДУЛЬ"
SOURCE_ROOT = Path("src/hunter")

# Чего чистый модуль не имеет права импортировать.
FORBIDDEN_IMPORTS = {
    "hunter.clock", ".clock", "clock",
    "hunter.exchange", ".exchange", "exchange",
    "hunter.archive", ".archive", "archive",
    "hunter.store", ".store", "store",
    "ccxt", "ccxt.pro", "urllib", "urllib.request", "socket", "aiohttp",
    "random", "secrets", "time", "datetime",
}


FORBIDDEN_NAMES = {"clock", "exchange", "archive", "store"}
"""Имена модулей проекта, запрещённые к импорту ЛЮБОЙ формой записи.

⚠ Отдельно от `FORBIDDEN_IMPORTS`, потому что дыра была именно здесь. Для
`from . import clock` разбор давал `mod = "."` — строки, которой в списке нет, — а ИМЕНА
импортируемых сущностей не проверялись вовсе. Пробник 2026-08-04: подсаженный в чистый
`card.py` импорт `from . import clock` дал «нарушений 0» и код возврата 0, тогда как
`from .clock import now_ms` тот же гейт ловил. Форма `from . import x` в проекте
ОСНОВНАЯ (`run.py`, `card.py`, `check.py`, `__main__.py`) — то есть гейт был закрыт ровно
для того способа, которым нарушение и наступает.

И это не гипотетика: `replay.py` числился чистым и импортировал `archive` и `store`,
оба в собственном списке запрещённых.
"""


def scan(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_IMPORTS or a.name in FORBIDDEN_IMPORTS:
                    out.append((node.lineno, f"импорт {a.name} в чистом модуле"))
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            if mod in FORBIDDEN_IMPORTS or (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                out.append((node.lineno, f"импорт из {mod} в чистом модуле"))
                continue
            # ИМЕНА, а не только модуль: `from . import clock` — тоже импорт часов.
            for a in node.names:
                if a.name in FORBIDDEN_NAMES:
                    out.append((node.lineno,
                                f"импорт имени {a.name} из {mod or '.'} в чистом модуле"))
    return out


def declares_purity(path: Path) -> bool:
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return PURITY_MARKER in (doc or "")


def main() -> int:
    files = [Path(p) for p in PURE_MODULES]
    absent = [p for p in files if not p.exists()]
    findings: list[str] = []

    listed = {p.as_posix() for p in files}
    declared = {p.as_posix() for p in sorted(SOURCE_ROOT.rglob("*.py")) if declares_purity(p)}
    drift = sorted(declared - listed)
    for p in files:
        if not p.exists():
            continue
        for line, why in scan(p):
            findings.append(f"{p}:{line}: {why}")
    print(f"гейт чистоты расчёта: в списке {len(files)}, просмотрено "
          f"{len(files) - len(absent)}, помечено «{PURITY_MARKER}» {len(declared)}, "
          f"нарушений {len(findings)}")
    for a in absent:
        print(f"  ПРОВАЛ: модуль объявлен чистым, но отсутствует: {a}")
    for d in drift:
        print(f"  ПРОВАЛ: модуль помечен «{PURITY_MARKER}», но не в списке гейта: {d}")
    for f in findings:
        print(f"  НАРУШЕНИЕ {f}")
    if not files:
        print("ПРОВАЛ: список чистых модулей пуст — проверка не состоялась")
        return 1
    return 1 if (findings or absent or drift) else 0


if __name__ == "__main__":
    sys.exit(main())
