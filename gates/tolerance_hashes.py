"""ГЕЙТ: опубликованный хеш файла допуска сходится с самим файлом. FOUNDATION.md §7.3.

Порог гипотезы записывается в `docs/audit/tolerance-*.md` ДО прогона, и с файла снимается
sha256. Весь смысл — в том, что порог нельзя подогнать задним числом: изменишь файл —
разойдётся хеш. Проверка эта до 2026-08-08 не исполнялась НИКОГДА, и держалась она на
внимательности человека, читающего два шестидесятичетырёхзначных числа подряд.

⚠⚠ ПОЧЕМУ ХЕШИРУЮТСЯ БАЙТЫ С ПРИВЕДЁННЫМИ ПЕРЕВОДАМИ СТРОК. Команда, которая
публиковалась в протоколах, хеширует байты РАБОЧЕЙ КОПИИ. На Windows git разворачивает
файл с CRLF, и хеш, снятый до коммита, после коммита не воспроизводится: замер 2026-08-08
показал расхождение у ВСЕХ СЕМИ файлов допусков сразу. Содержание при этом не менялось ни
у одного. Инструмент, который сообщает о подделке там, где её нет, не лучше того, который
её не замечает, — поэтому здесь сравнивается содержание, а не представление на диске.

Разбор: docs/audit/bars-projects-2026-08-08.md

Гейт печатает ЧИСЛА — файлов, найденных публикаций, расхождений — и падает по коду
возврата. Строка «всё сошлось» без числа проверенного неотличима от непроведённой проверки.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

TOLERANCE_DIR = Path("docs/audit")
SCAN_GLOBS = ("docs/**/*.md", "docs/audit/probes/*.py")

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")

WINDOW = 400
"""Сколько знаков после имени файла считать местом публикации его хеша.

Формы разные — «файл, sha256 X.», таблица `| допуск | файл | X |`, ссылка на отдельной
строке, — и все они укладываются в это окно. Больше брать нельзя: начнёт цепляться хеш
соседней записи.
"""


KNOWN_STALE: dict[tuple[str, str], str] = {
    ("tolerance-R-01.md", "49670e1824d6b7fd931411b08b0da47467305c12ced57fb7e72d91501422dbf5"):
        "файл дополнен ПОСЛЕ прогона разделом «Команда воспроизведения», которого требует "
        "гейт repro_commands; сам допуск 0.10 ATR не менялся. Расхождение названо в "
        "docs/audit/04-05-measurements.md и разобрано в docs/audit/04-poc-corrected.md",
    ("tolerance-R-03.md", "285af7bffbdb547dbc3dcad6c821a9e90f57e2cd82701e0b7aecad4ac62e7e63"):
        "файл дополнен ПОСЛЕ прогона R-03 предрегистрацией поправки R-05 (коммит 7bde898); "
        "раздел R-03 не тронут, что проверяется git diff. Названо в "
        "docs/audit/06-conformance.md",
    ("tolerance-accumulation.md",
     "59eb5ca665debc8d084400999886a552f78f0c2c44ceba603e8b9f3fe5118c71"):
        "хеш ПЕРВОЙ редакции, снятый с неполного файла: в нём не было команды "
        "воспроизведения, чего потребовал гейт repro_commands. Замер после дописывания "
        "перегнан заново, пороги не менялись. Названо в docs/audit/tolerance-accumulation-alt.md "
        "и в docs/audit/probes/probe_accumulation_questions_2026-08-07.py",
}
"""Публикации, разошедшиеся с файлом ЗАКОННО и объяснённые в корпусе.

⚠ Это не «список исключений, чтобы правка прошла». Условие попадания сюда одно: расхождение
уже РАЗОБРАНО в документе, на который здесь есть ссылка. Каждая запись печатается отдельной
строкой с числом — молча они не проходят.
"""


def digests(path: Path) -> tuple[str, str]:
    """Два хеша ОДНОГО И ТОГО ЖЕ содержания: с переводами строк LF и CRLF.

    Совпадение с любым из них означает, что содержание не менялось: различаются только
    представление на диске, а не текст. Различающая сила от этого не страдает — правка
    текста меняет ОБА хеша.

    Обе формы нужны потому, что в корпусе опубликованы и те, и другие: часть снята до
    коммита (LF), часть — командой по рабочей копии на Windows (CRLF).
    """
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(lf).hexdigest(), hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()


def published(name: str, sources: list[Path]) -> list[tuple[str, str, int]]:
    """Где и какой хеш опубликован для файла `name`: (документ, хеш, номер строки)."""
    out: list[tuple[str, str, int]] = []
    for src in sources:
        text = src.read_text(encoding="utf-8")
        for m in re.finditer(re.escape(name), text):
            tail = text[m.end():m.end() + WINDOW]
            found = HEX64.search(tail)
            if found is None:
                continue
            line = text.count("\n", 0, m.start()) + 1
            out.append((src.as_posix(), found.group(0), line))
    return out


def main() -> int:
    files = sorted(TOLERANCE_DIR.glob("tolerance-*.md"))
    if not files:
        print("гейт хешей допусков: файлов допусков нет — проверять нечего")
        return 1

    sources: list[Path] = []
    for pattern in SCAN_GLOBS:
        sources.extend(sorted(Path().glob(pattern)))

    mismatched: list[str] = []
    unpublished: list[str] = []
    stale: list[str] = []
    checked = 0
    as_lf = 0
    as_crlf = 0

    for f in files:
        lf, crlf = digests(f)
        pubs = published(f.name, sources)
        # Одну и ту же публикацию часто дублируют протокол и зонд — считаем разные хеши.
        distinct = sorted({h for _s, h, _ln in pubs})
        if not distinct:
            unpublished.append(f.name)
            continue
        for h in distinct:
            checked += 1
            if h == lf:
                as_lf += 1
                continue
            if h == crlf:
                as_crlf += 1
                continue
            where = ", ".join(f"{s}:{ln}" for s, _h, ln in pubs if _h == h)
            reason = KNOWN_STALE.get((f.name, h))
            if reason is not None:
                stale.append(f"{f.name}: {h[:8]}… — {reason} [{where}]")
            else:
                mismatched.append(
                    f"{f.name}: опубликован {h[:12]}…, файл даёт {lf[:12]}… [{where}]"
                )

    print(f"гейт хешей допусков: файлов {len(files)}, публикаций проверено {checked}, "
          f"сошлось (LF {as_lf}, CRLF {as_crlf}), без публикации {len(unpublished)}, "
          f"исторических {len(stale)}, РАСХОЖДЕНИЙ {len(mismatched)}")
    for s in stale:
        print(f"  историческое (объяснено в корпусе) {s}")
    for u in unpublished:
        print(f"  НАРУШЕНИЕ {u}: хеш не опубликован НИГДЕ — порог не зафиксирован")
    for m in mismatched:
        print(f"  РАСХОЖДЕНИЕ {m}")
    return 1 if (mismatched or unpublished) else 0


if __name__ == "__main__":
    sys.exit(main())
