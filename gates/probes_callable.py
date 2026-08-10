"""ГЕЙТ: пробники ВЫЗЫВАЮТ то, что существует. FOUNDATION.md §7.3.

Почему гейт появился (2026-08-10, находка CodeQL в первый же прогон).

§7.3 требует: «число без команды воспроизведения не фиксируется». Команды записаны — по
одной у каждого протокола, и указывают они на файлы `docs/audit/probes/*.py`. Никто
никогда не проверял, что эти файлы ЗАПУСКАЮТСЯ.

Замер того же дня: `probe_second_pass_2026-08-08.py` и
`probe_foreign_border_control_2026-08-08.py` зовут `engine.read_series(series, tfs,
anchors)` — три аргумента при сигнатуре из двух. Механизм «граница с чужого ТФ» был
внесён 2026-08-08 и в тот же день ОТКАЧЕН (не прошёл контроль на заведомо неверных
данных), сигнатура вернулась к прежней, а пробники остались. То есть команда
воспроизведения, записанная в протоколе, падает `TypeError` — протокол утверждает
число, доказательство которого не исполняется.

⚠ Это ТОТ ЖЕ класс, что уже ловили 2026-08-08 на команде проверки хеша допуска, и та же
причина: инструмент измерения стоял вне проверки. `mypy` покрывает `src`, `scripts` и
`gates` — пробников среди них нет.

ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ: вызовы к `hunter.*` согласованы с настоящими сигнатурами, и
упоминаемые атрибуты существуют. Полного запуска НЕ делается намеренно: пробник может
требовать сеть, кэш сделок на гигабайты или часы счёта — гейт обязан быть быстрым и
детерминированным. Значит он ловит «функция изменилась, вызов остался», но не ловит
дефект внутри логики пробника.

Строгость намеренно НЕ боевая: пробник — свидетельство, писавшееся один раз под один
вопрос. Требовать от 46 файлов полной аннотации значило бы либо переписать улики, либо
не проверять их вовсе. Оставлен ровно тот класс ошибок, который делает команду
воспроизведения неработающей.

Контроль (CLAUDE.md: способен ли прибор выдать ИНОЙ ответ) — в
docs/audit/probes-callable-2026-08-10.md: подсаженный вызов несуществующей функции даёт
код 1 и названный файл.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROBES = Path("docs/audit/probes")

RETIRED_MARK = "ЗАМЕР ОТКАЧЕННОГО МЕХАНИЗМА:"
"""Пометка в шапке пробника, чей механизм СНЯТ с проекта.

Такой пробник починить нельзя — чинить нечего: функции, которую он звал, больше не
существует, и это осознанный откат, а не поломка. Удалить его тоже нельзя: он
СВИДЕТЕЛЬСТВО замера, из-за которого механизм и сняли.

⚠ Пометка не прячет файл, а переводит его в отдельный счёт, который печатается всегда.
Иначе она стала бы способом заглушить гейт: «пометил — и тихо». Рядом с пометкой
обязана стоять ссылка на разбор, иначе гейт её не признаёт.
"""

CODES = (
    "call-arg",      # «Too many arguments», «Missing named argument» — сигнатура ушла
    "attr-defined",  # функции или поля больше нет в модуле
    "name-defined",  # имя не определено вовсе
    "union-attr",    # обращение к полю у типа, который может быть NotReady
)
"""Классы ошибок, из-за которых пробник НЕ ЗАПУСТИТСЯ. Остальное — стиль."""

MUTED = (
    # Аннотаций от пробников не требуем — см. шапку.
    "no-untyped-def", "no-untyped-call", "type-arg", "no-any-return", "has-type",
    "var-annotated", "annotation-unchecked",
    # Присваивания и арифметика внутри самого пробника — его дело: он одноразовый и
    # свой ответ уже дал. Гейт про СВЯЗЬ С КОДОМ, а не про чистоту улики.
    "assignment", "index", "misc", "operator", "list-item", "return-value", "arg-type",
    "unused-ignore", "method-assign",
)


def main() -> int:
    if not PROBES.is_dir():
        print(f"ПРОВАЛ: нет каталога {PROBES.as_posix()} — проверять нечего")
        return 1
    files = sorted(PROBES.glob("*.py"))
    if not files:
        print("ПРОВАЛ: пробников не найдено — проверка не состоялась")
        return 1

    retired: dict[str, str] = {}
    for path in files:
        head = path.read_text(encoding="utf-8")[:2000]
        if RETIRED_MARK in head:
            line = next(ln for ln in head.splitlines() if RETIRED_MARK in ln)
            reason = line.split(RETIRED_MARK, 1)[1].strip()
            if not reason or ".md" not in reason:
                print(f"  НАРУШЕНИЕ {path.as_posix()}: пометка "
                      f"«{RETIRED_MARK}» без ссылки на разбор — так её можно было бы "
                      f"поставить кому угодно")
                return 1
            retired[path.name] = reason

    cmd = [sys.executable, "-m", "mypy", str(PROBES), "--ignore-missing-imports"]
    cmd += [f"--disable-error-code={c}" for c in MUTED]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    out = (proc.stdout or "") + (proc.stderr or "")

    hits = [ln for ln in out.splitlines() if any(f"[{c}]" in ln for c in CODES)]
    bad = [ln for ln in hits if not any(name in ln for name in retired)]
    excused = len(hits) - len(bad)

    print(f"гейт вызовов в пробниках: файлов {len(files)}, "
          f"несуществующих вызовов {len(bad)}, "
          f"замеров откаченных механизмов {len(retired)} (у них {excused} вызовов)")
    for name, why in sorted(retired.items()):
        print(f"  ОТКАЧЕН {name} — {why}")
    for line in bad:
        print(f"  НАРУШЕНИЕ {line.strip()}")
    if bad:
        print("  ⚠ команда воспроизведения из протокола НЕ ИСПОЛНЯЕТСЯ: "
              "число есть, доказательства нет (§7.3)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
