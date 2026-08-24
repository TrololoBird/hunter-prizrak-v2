"""Логи через structlog. FOUNDATION.md §10.1.

§4.3 требует, чтобы пропуск был виден, поэтому здесь есть уровень `degraded`: им
отмечается всякий путь, продолживший работу в урезанном виде. Счётчик деградаций
печатается в приёмке — «деградаций 0» без счётчика неотличимо от «никто не считал».
"""

from __future__ import annotations

import sys
from typing import Any

import structlog

_degraded_count = 0


def configure(json_output: bool = False) -> None:
    # ⚠⚠ ПОСТРОЧНАЯ ВЫДАЧА ВКЛЮЧАЕТСЯ ЯВНО (2026-08-21), И ЭТО НЕ КОСМЕТИКА. Python
    # буферизует `stdout` блоками по 8 КБ, когда он НЕ терминал, — а служба 24/7
    # запускается именно так (вывод в файл, в journald, в окно без консоли). Приёмка
    # прогона печатается `print`-ом, и при таком запуске она не появлялась ВООБЩЕ:
    # проверено 2026-08-21 — за 25 минут работы службы файл `stdout` имел РОВНО НОЛЬ
    # байт, тогда как в `stderr` (логи structlog, он не буферизуется) было 25 КБ.
    #
    # То есть весь раздел «ЗАПАДАНИЕ» и вся сводка приёмки — то, ради чего они и
    # печатаются, — были невидимы всякому, кто не смотрит в живой терминал. Молчание
    # прибора неотличимо от «нарушений нет» (§4.3), и здесь оно было ровно таким.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, ValueError) as e:
        # Поток подменён (перехват в зондах, `io.StringIO`) — буферизацией управляет
        # тот, кто подменил. Молчать всё равно нельзя: если это случится в бою, приёмка
        # снова станет невидимой, и списать её пропажу будет не на что. Пишем в
        # `stderr` напрямую — structlog здесь ещё не настроен.
        print(f"[warning  ] построчная выдача stdout НЕ включена: "
              f"{type(e).__name__}: {e} — приёмка может не появиться до конца процесса",
              file=sys.stderr)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            (structlog.processors.JSONRenderer() if json_output
             else structlog.dev.ConsoleRenderer(colors=False)),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


_log = structlog.get_logger()


def info(event: str, **kw: Any) -> None:
    _log.info(event, **kw)


def warn(event: str, **kw: Any) -> None:
    _log.warning(event, **kw)


def error(event: str, **kw: Any) -> None:
    _log.error(event, **kw)


def degraded(event: str, **kw: Any) -> None:
    """Работа продолжена в урезанном виде. Молчать об этом запрещено — §4.3."""
    global _degraded_count
    _degraded_count += 1
    _log.warning(event, degraded=True, **kw)


def degraded_count() -> int:
    """Деградаций С НАЧАЛА ПРОЦЕССА. Монотонно растёт и не сбрасывается никогда."""
    return _degraded_count


_degraded_mark = 0


def degraded_since_mark() -> int:
    """Деградаций С ПРОШЛОГО ВЫЗОВА этой функции. Читает приёмка ЦИКЛА.

    ⚠⚠ ЗАВЕДЕНО 2026-08-23. Приёмка печатала `degraded_count()` — процессный счётчик —
    в разделе «8. ИТОГ» рядом с числами ЗА ЦИКЛ, а `service.serve` зовёт приёмку каждый
    цикл. У службы, живущей сутками, строка «деградаций отмечено» росла монотонно и не
    отвечала на вопрос, который в этом разделе задаётся, — «что было в ЭТОМ цикле».
    Тот же класс, что и `stage_ms["backfill"]`, считавшийся от начала цикла: величина
    из другого окна под именем, обещающим это окно.

    Оба числа печатаются рядом: за цикл — чтобы судить о цикле, всего — чтобы видеть
    накопление. Ни одно не заменяет другого.
    """
    global _degraded_mark
    delta = _degraded_count - _degraded_mark
    _degraded_mark = _degraded_count
    return delta
