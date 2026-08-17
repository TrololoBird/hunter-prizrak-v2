"""ГЕЙТ: ссылка «§N» существует, а императив о методике несёт референт рядом.

Написан 2026-08-17 по предписанию фальсификации (session-2026-08-17-auto.md): разбор
«адрес или основание» жил в голове, повторить его было нельзя, и ноль оснований оказался
свойством критерия, а не кода. Здесь критерий записан кодом и проверяется подсаженным
нарушением на каждом прогоне.

Две проверки:

1. СУЩЕСТВОВАНИЕ. Каждый «§N[.M]» в src и gates обязан существовать в docs/FOUNDATION.md
   как раздел. Ловит ссылку на несуществующий или исчезнувший параграф: документ
   переживает правки, и такая ссылка молча становится указателем в пустоту.
   Исключаются ссылки, привязанные на той же строке к ДРУГОМУ документу
   (DEFINITIONS.md §7, transport-decision §3.3, §I-7 прошлого проекта).

2. ИМПЕРАТИВ О МЕТОДИКЕ. Оборот «§X требует/запрещает/задаёт/велит/обязывает» в ПРОЗЕ
   (докстроки и комментарии; строки-сообщения кода не считаются — они адрес для
   оператора) для параграфов МЕТОДИКИ И ДАННЫХ обязан иметь референт в том же файле не
   далее WINDOW строк: «стр. N», путь docs/…, либо цитату в «ёлочках». Прецедент класса —
   exchange.py:490: «§5 требует строить профиль на aggTrade» стояло основанием и было
   отозвано 2026-08-12.

К параграфам методики и данных отнесены (каждый вход обоснован):
  §2.*  — сама методика: её правила берутся из курса, параграф лишь протоколирует;
  §5    — данные: что взято у биржи и почему — вопрос к исходнику ccxt и замеру;
  §4.1  — ФОРМА рыночных порогов (ATR-кратные, перцентили): от неё зависит, срабатывает
          ли фактор, то есть выход расчёта, — а внешнего референта у §4.1 нет.
Остальные (§0, §1, §3, §4.2, §4.3, §6, §7.*, §8, §9, §10.*) — правила устройства ЭТОЙ
системы (детерминизм, запрет молчания, запрет весовых сумм, схема работ): их референт —
само записанное решение, императив к ним есть адрес. Это НЕ объявляет их неприкасаемыми —
противоречие параграфа источнику разбирается по стоп-листу CLAUDE.md, — но гейтом оно не
ловится: у решения об устройстве нет «страницы курса», которую можно спросить механически.
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

ROOTS = (Path("src/hunter"), Path("gates"))
FOUNDATION = Path("docs/FOUNDATION.md")

WINDOW = 15
"""Сколько строк прозы вокруг императива просматривается в поисках референта."""

IMPERATIVE = re.compile(
    r"§(2(?:\.\d+)?|5|4\.1)\s+(?:требует|запрещает|задаёт|велит|обязывает)")
ANY_REF = re.compile(r"§(\d+(?:\.\d+)*)")
REFERENT = re.compile(r"стр\.\s*\d|docs/|«[^»]{3,}»")
OTHER_DOC = re.compile(r"DEFINITIONS|transport-decision|§I-|прошлого проекта")

PLANTED = '"""Порог накопления — три касания (§2.3 требует).\n' + "x" * 0 + '"""'
"""Позитивный контроль: строка-нарушение, на которой гейт ОБЯЗАН сработать."""


def foundation_sections() -> set[str]:
    """Существующие разделы FOUNDATION: «## 2.», «### 0.1.», «**2.1.**», «**7.6.1.**»."""
    text = FOUNDATION.read_text(encoding="utf-8")
    found: set[str] = set()
    for m in re.finditer(r"^#{2,3}\s+(\d+(?:\.\d+)*)[.\s]", text, re.MULTILINE):
        found.add(m.group(1))
    for m in re.finditer(r"\*\*(\d+(?:\.\d+)+)\.", text):
        found.add(m.group(1))
    # §N.M существует и как родитель §N; §2.x подразумевает §2
    for s in list(found):
        while "." in s:
            s = s.rsplit(".", 1)[0]
            found.add(s)
    return found


def prose_lines(path: Path) -> dict[int, str]:
    """Строки ПРОЗЫ файла: докстроки/строковые литералы уровня модуля и комментарии.

    Берутся токены COMMENT и STRING; из строк исключаются f-строки-сообщения не нужно —
    решает не вид строки, а то, что императив ищется только здесь, а сообщения об
    ошибках формой «§N задаёт {…}» остаются адресами для оператора и не попадают под
    IMPERATIVE из-за фигурных скобок вместо глагольного оборота. Проза возвращается
    построчно с номерами строк файла."""
    out: dict[int, str] = {}
    text = path.read_text(encoding="utf-8")
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError:  # pragma: no cover — такой файл раньше уронит ruff
        return {}
    for tok in toks:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        if tok.type == tokenize.STRING and not tok.string.lstrip("rbuRBU").startswith(
                ('"""', "'''")):
            continue  # короткие строки кода — не проза
        for i, line in enumerate(tok.string.splitlines()):
            out[tok.start[0] + i] = line
    return out


def main() -> int:
    sections = foundation_sections()
    if len(sections) < 20:
        print(f"ПРОВАЛ: из FOUNDATION.md извлечено всего {len(sections)} разделов — "
              f"парсер сломан, проверка не состоялась")
        return 1

    files = sorted(p for root in ROOTS for p in root.rglob("*.py"))
    refs = 0
    imperatives = 0
    bad: list[str] = []

    for path in files:
        prose = prose_lines(path)
        all_lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(all_lines, start=1):
            for m in ANY_REF.finditer(line):
                if OTHER_DOC.search(line):
                    continue
                refs += 1
                if m.group(1) not in sections:
                    bad.append(f"{path}:{lineno}: §{m.group(1)} не существует в FOUNDATION.md")
        for lineno, line in sorted(prose.items()):
            if not IMPERATIVE.search(line):
                continue
            imperatives += 1
            window = [prose.get(n, "") for n in range(lineno - WINDOW, lineno + WINDOW + 1)]
            if not any(REFERENT.search(w) for w in window):
                bad.append(
                    f"{path}:{lineno}: императив о методике без референта рядом "
                    f"(нет ни «стр. N», ни docs/, ни цитаты в ±{WINDOW} строках): "
                    f"{line.strip()[:80]}")

    # Позитивный контроль: критерий обязан ловить подсаженное нарушение.
    planted_hit = bool(IMPERATIVE.search(PLANTED)) and not REFERENT.search(PLANTED)
    if not planted_hit:
        print("ПРОВАЛ: подсаженное нарушение НЕ поймано — критерий ослеп")
        return 1

    print(f"файлов {len(files)}, ссылок §N {refs}, императивов о методике {imperatives}, "
          f"нарушений {len(bad)}; подсаженное нарушение поймано")
    for b in bad:
        print(f"  {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
