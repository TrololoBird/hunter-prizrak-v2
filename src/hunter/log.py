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
    return _degraded_count
