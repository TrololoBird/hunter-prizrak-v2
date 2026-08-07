"""ЗОНД: где в модулях сидят НАСТРАИВАЕМЫЕ решения и у скольких записан источник.

Вопрос: можно ли отранжировать очередь `docs/audit/survey-catalog.md` числом вместо
рассуждения — чем больше в модуле решений без записанного источника, тем выше отдача
обзора чужих реализаций.

ОТВЕТ ЗОНДА: НЕЛЬЗЯ, и это записано в каталоге. Наверх он поднимает `archive.py` и
`exchange.py` — модули, где числовых ручек много (размеры страниц, таймауты, число
попыток), а метода нет. Прибор мерит ПЛОТНОСТЬ ЧИСЛОВЫХ РУЧЕК, и она обратна методической
важности. Ранжировать очередь им нельзя.

Годным оказалось другое его показание: у восьми модулей настраиваемых мест не найдено
ВООБЩЕ. Это не «решений нет», а «решения структурные» — какая ветка, какой элемент списка,
строгое сравнение против нестрогого. Следствие для фазы 0 команды `/survey` выписано в
каталоге.

⚠ ПЕРВАЯ РЕДАКЦИЯ СЧИТАЛА ТОЛЬКО КОНСТАНТЫ УРОВНЯ МОДУЛЯ и давала ноль у `pereprior.py`,
`priority.py`, `outcome.py`. Ноль означал не «решений нет», а «решения не названы
константами», то есть мерил стиль именования. Здесь считаются три места; восемь нулей
остались и после расширения — они настоящие, а не артефакт регулярки.

⚠ Это ПРОКСИ, а не перепись решений. Выбор «брать последний кандидат, а не первый» числа
не имеет и здесь не виден. Число годится для вопроса «есть ли тут вообще числовые ручки»,
и НЕ годится для отчёта «решений в модуле N».

Считаются три места:
  1. константа уровня модуля            POROG = 3
  2. значение по умолчанию у аргумента   def f(k: int = 5)
  3. голый числовой литерал в сравнении  if x > 0.7
Скучные значения (0, 1, -1, 2, 100) отброшены: это индексы и проценты, а не решения.

Место считается обоснованным, если в 5 строках над ним или 8 под ним есть «стр. N» (курс)
либо «§» (FOUNDATION).

Читает из ЗАКРЕПЛЁННОГО коммита (см. REV), а не из рабочего дерева и не из подвижной
ссылки: иначе число в каталоге перестаёт воспроизводиться при первом коммите в main.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_decision_sites_2026-08-07.py
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys

MODULES = [
    ("0  профиль объёма", "src/hunter/volume_profile.py"),
    ("1  свинги", "src/hunter/swings.py"),
    ("2  слом структуры", "src/hunter/pereprior.py"),
    ("3  прокол/пробой", "src/hunter/breach.py"),
    ("4  накопление", "src/hunter/accumulation.py"),
    ("5  сетка баров", "src/hunter/bars.py"),
    ("6  часы", "src/hunter/clock.py"),
    ("7  стоповый объём", "src/hunter/stop_volume.py"),
    ("8  уровни", "src/hunter/levels.py"),
    ("9  приоритет ТФ", "src/hunter/priority.py"),
    ("10 геометрия", "src/hunter/geometry.py"),
    ("11 исход", "src/hunter/outcome.py"),
    ("12 транспорт", "src/hunter/exchange.py"),
    ("13 хранение", "src/hunter/store.py"),
    ("13 архив", "src/hunter/archive.py"),
    ("14 доставка", "src/hunter/emit.py"),
    ("14 карточка", "src/hunter/card.py"),
    ("15 служба", "src/hunter/service.py"),
    ("15 движок", "src/hunter/engine.py"),
    ("16 повтор", "src/hunter/replay.py"),
    ("17 контракты", "src/hunter/models.py"),
]

SRC = re.compile(r"стр\.\s*\d|§")
BORING = {0, 1, -1, 2, 100, 0.0, 1.0, 2.0}


REV = "9b10055"
"""Состояние `main` на 2026-08-07, из которого посчитаны числа в каталоге.

⚠ Прибито к КОММИТУ, а не к `origin/main`. Первая редакция читала подвижную ссылку, и
число в каталоге переставало воспроизводиться при первом же коммите в main: читатель
получал бы другой ответ на ту же команду и не мог понять, кто из них верен.
"""


def read(path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{REV}:{path}"],
                       capture_output=True, text=True, encoding="utf-8")
    return r.stdout if r.returncode == 0 else None


def _numeric(node: ast.expr | None) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, int | float)
            and not isinstance(node.value, bool) and node.value not in BORING)


def sites(src: str) -> list[tuple[int, str]]:
    """(строка, вид) для каждого места, где принято настраиваемое решение."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and node.col_offset == 0:
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 2:
                    out.append((node.lineno, "константа"))
        elif isinstance(node, ast.AnnAssign) and node.col_offset == 0:
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                out.append((node.lineno, "константа"))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for d in list(node.args.defaults) + list(node.args.kw_defaults):
                if _numeric(d):
                    assert d is not None
                    out.append((d.lineno, "умолчание"))
        elif isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if _numeric(side):
                    out.append((side.lineno, "порог"))
    return sorted(set(out))


def main() -> int:
    print(f"{'элемент':<20} {'файл':<20} {'мест':>5} {'с источником':>13} "
          f"{'умолчанием':>11}  виды")
    rows: list[tuple[str, int, int]] = []
    for name, path in MODULES:
        src = read(path)
        if src is None:
            print(f"{name:<20} {path.split('/')[-1]:<20} — файла нет в {REV}")
            continue
        lines = src.splitlines()
        found = sites(src)
        cited = 0
        kinds: dict[str, int] = {}
        for ln, kind in found:
            kinds[kind] = kinds.get(kind, 0) + 1
            if SRC.search("\n".join(lines[max(0, ln - 6):ln + 8])):
                cited += 1
        n = len(found)
        rows.append((name, n, n - cited))
        ks = " ".join(f"{k}:{v}" for k, v in sorted(kinds.items()))
        print(f"{name:<20} {path.split('/')[-1]:<20} {n:>5} {cited:>13} {n - cited:>11}  {ks}")

    print()
    print("ЕСЛИ БЫ ОЧЕРЕДЬ РАНЖИРОВАЛАСЬ ЭТИМ ЧИСЛОМ (мест без источника, убывание):")
    for name, n, bare in sorted(rows, key=lambda r: -r[2]):
        if bare:
            print(f"   {bare:>3} из {n:<3}  {name}")
    print()
    print("КОНТРОЛЬ: способен ли прибор ответить иначе?")
    zero = [n for n, t, _ in rows if t == 0]
    full = [n for n, t, b in rows if t and b == 0]
    print(f"   модулей, где мест не найдено вовсе: {len(zero)}")
    for n in zero:
        print(f"      {n}")
    print(f"   модулей, где ВСЕ места обоснованы: {len(full)}")
    for n in full:
        print(f"      {n}")
    print()
    print("ВЕРДИКТ: ранжировать очередь этим числом НЕЛЬЗЯ — наверх поднимаются модули")
    print("с числовыми ручками (архив, транспорт), а не с методом. Годно только показание")
    print("«числовых ручек нет вовсе»: там решения структурные, и грепом их не найти.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
