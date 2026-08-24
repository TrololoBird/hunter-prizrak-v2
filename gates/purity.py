"""ГЕЙТ: чистота расчётных модулей. FOUNDATION.md §10.3.

§10.3 дословно: «Расчёт сигнала — чистые функции от кадров. Никакого обращения к часам,
сети или глобальному состоянию внутри расчёта. Время входит как аргумент, а не берётся
внутри. Никакой случайности.»

Без этого детерминированный повтор невозможен, а повтор — единственный инструмент,
которым владелец может проверить изменение расчёта, не читая код.

⚠⚠ ЧТО ГЕЙТ ПРОВЕРЯЕТ НА САМОМ ДЕЛЕ (расширено 2026-08-23). До этого дня он разбирал
ИСКЛЮЧИТЕЛЬНО импорты, при том что цитата выше называет ТРИ вещи — часы, сеть и
глобальное состояние. Гейт объявлял охват больше настоящего, то есть был прибором,
смотрящим не на ту величину. Теперь их три и каждая своя функция:

  `scan_imports`       — запрещённые импорты этого файла;
  `scan_module_state`  — МОДУЛЬНОЕ СОСТОЯНИЕ: величина уровня модуля, которую меняют
                         из тела функции, плюс `global`;
  `scan`               — то же самое ТРАНЗИТИВНО по цепочке импортов проекта.

Обе добавки заведены не «на всякий случай», а по найденному: три модульных изменяемых
словаря в чистых модулях (`_VOL_MEMO`, `_WINDOW_CACHE`, `_PREFIX_CACHE`) и
`geometry.py` → `render.py` → `matplotlib.use("Agg")` + `datetime`. Гейт был зелёным на
всём этом. Контроль подсаженным нарушением 2026-08-23: модульный словарь в
`stop_volume` и импорт `render` в `geometry` дают код возврата 1 и две названные строки.

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
    "src/hunter/trading_range.py",
    "src/hunter/levels.py",
    "src/hunter/stop_volume.py",
    "src/hunter/breach.py",
    "src/hunter/pereprior.py",
    "src/hunter/figures.py",
    "src/hunter/absorption.py",
    "src/hunter/geometry.py",
    "src/hunter/priority.py",
    "src/hunter/factors.py",
    "src/hunter/card.py",
    "src/hunter/outcome.py",
    "src/hunter/emit.py",
    "src/hunter/engine.py",
]

# ⚠ replay.py из списка УБРАН 2026-08-04, и это признание, а не послабление. Он читает
# кадры с диска и срез архива — то есть делает ввод-вывод по определению своей задачи.
# Чистой обязана быть `card.render`, которую он зовёт, и она в списке. Пока гейт не видел
# формы `from . import X`, модуль числился чистым и печатался как проверенный, импортируя
# `archive` и `store` — оба в собственном списке запрещённых этого гейта.

# Список выше ведётся руками, и 2026-08-03 он отстал: range.py объявил себя
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
card.py импорт `from . import clock` дал «нарушений 0» и код возврата 0, тогда как
`from .clock import now_ms` тот же гейт ловил. Форма `from . import x` в проекте
ОСНОВНАЯ (run.py, card.py, check.py, __main__.py) — то есть гейт был закрыт ровно
для того способа, которым нарушение и наступает.

И это не гипотетика: replay.py числился чистым и импортировал `archive` и `store`,
оба в собственном списке запрещённых.
"""


MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault",
    "add", "discard", "popitem", "move_to_end", "sort", "appendleft", "popleft",
}
"""Методы, которыми МЕНЯЮТ контейнер. Список закрытый и намеренно узкий: гейт ищет
изменение МОДУЛЬНОЙ величины из тела функции, а не всякий вызов метода."""


def local_imports(path: Path) -> set[str]:
    """Модули ПРОЕКТА, которые этот файл импортирует. Для обхода цепочки."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and not mod:
                # `from . import x` — модулями являются сами имена
                out.update(a.name for a in node.names)
            elif node.level or mod.startswith("hunter."):
                out.add(mod.split(".")[-1] if "." in mod else mod)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("hunter."):
                    out.add(a.name.split(".")[-1])
    return {m for m in out if m and (SOURCE_ROOT / f"{m}.py").exists()}


def scan_imports(path: Path) -> list[tuple[int, str]]:
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


def scan_module_state(path: Path) -> list[tuple[int, str]]:
    """МОДУЛЬНОЕ СОСТОЯНИЕ: величина уровня модуля, которую МЕНЯЮТ из тела функции.

    ⚠⚠ ЗАВЕДЕНО 2026-08-23, И ЭТО ПОЧИНКА ОХВАТА, А НЕ НОВЫЙ ГЕЙТ. Шапка этого файла с
    первого дня цитирует §10.3: «Никакого обращения к часам, сети или ГЛОБАЛЬНОМУ
    СОСТОЯНИЮ внутри расчёта», — а `scan` разбирал ИСКЛЮЧИТЕЛЬНО импорты. То есть
    половина процитированного требования не проверялась ничем, и гейт объявлял охват
    больше настоящего. Прибор, смотрящий не на ту величину, — тот самый класс дефекта,
    ради которого этот проект и держит гейты.

    Счёт, подтверждающий делом: три модульных изменяемых словаря жили в модулях,
    объявленных ЧИСТЫМИ, и гейт был зелёным всё это время —
    `stop_volume._VOL_MEMO` (рос до 100 000 записей), `profile_source._WINDOW_CACHE`
    (переживал циклы службы и отдавал ОБЩИЙ мутабельный объект) и
    `swings._PREFIX_CACHE`. Все три вынесены во владельцев 2026-08-23.

    Что ищется: `ИМЯ[...] = ...`, `ИМЯ += ...`, `ИМЯ.append/clear/update/...()` и
    `global ИМЯ` внутри функции, где `ИМЯ` объявлено на уровне модуля.

    ⚠ Что НЕ ищется и почему: модульный словарь-таблица, который только ЧИТАЮТ
    (`TIMEFRAME_MS`, `TF_LABEL`, `DIV_LABEL`), нарушением не считается. §10.3 запрещает
    состояние, то есть величину, МЕНЯЮЩУЮСЯ между вызовами; неизменяемая по факту
    таблица от кортежа отличается только синтаксисом. Граница названа, а не подразумевается.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            top.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top.add(node.target.id)
    out: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_Global(self, node: ast.Global) -> None:
            if self.depth:
                out.append((node.lineno,
                            f"global {', '.join(node.names)} в чистом модуле"))

        def visit_Assign(self, node: ast.Assign) -> None:
            if self.depth:
                for t in node.targets:
                    if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                            and t.value.id in top):
                        out.append((node.lineno, f"запись в модульную {t.value.id}[...] "
                                                 f"из функции чистого модуля"))
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            if (self.depth and isinstance(node.target, ast.Name)
                    and node.target.id in top):
                out.append((node.lineno, f"изменение модульной {node.target.id} "
                                         f"из функции чистого модуля"))
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if (self.depth and isinstance(node.func, ast.Attribute)
                    and node.func.attr in MUTATING_METHODS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in top):
                out.append((node.lineno,
                            f"{node.func.value.id}.{node.func.attr}() — изменение "
                            f"модульного состояния в чистом модуле"))
            self.generic_visit(node)

    Visitor().visit(tree)
    return out


def scan(path: Path, listed: set[str]) -> list[tuple[int, str]]:
    """Импорты, модульное состояние и ТРАНЗИТИВНАЯ цепочка импортов.

    ⚠⚠ ЦЕПОЧКА ЗАВЕДЕНА 2026-08-23. Гейт разбирал импорты каждого файла ПООТДЕЛЬНОСТИ,
    и этого хватало ровно до тех пор, пока запрещённое приходило прямым импортом.
    Пойманный случай: `geometry.py` (в списке чистых) импортировал `render` ради одной
    константы, а `render.py` на импорте выполняет `matplotlib.use("Agg")` — правку
    ГЛОБАЛЬНОГО СОСТОЯНИЯ библиотеки — и импортирует `datetime`, который этот же гейт
    держит в `FORBIDDEN_IMPORTS`. Гейт был зелёным.

    Цепочка обходится ТОЛЬКО по модулям проекта и ТОЛЬКО за пределы списка чистых:
    чистый модуль, импортирующий чистый, ничего не нарушает, а его собственные импорты
    проверены отдельно — иначе одно нарушение печаталось бы столько раз, сколько на
    него ссылок.
    """
    out = scan_imports(path) + scan_module_state(path)
    seen: set[str] = set()
    queue = [m for m in local_imports(path) if m not in listed]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        child = SOURCE_ROOT / f"{name}.py"
        if not child.exists():
            continue
        for line, why in scan_imports(child):
            out.append((0, f"через импорт {name} ({name}.py:{line}): {why}"))
        for line, why in scan_module_state(child):
            out.append((0, f"через импорт {name} ({name}.py:{line}): {why}"))
        queue.extend(m for m in local_imports(child)
                     if m not in listed and m not in seen)
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
    names = {Path(p).stem for p in PURE_MODULES}
    for p in files:
        if not p.exists():
            continue
        for line, why in scan(p, names):
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
