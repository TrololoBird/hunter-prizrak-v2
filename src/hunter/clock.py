"""Часы, сведённые с биржей. FOUNDATION.md §6.

Единственная точка чтения локальных часов в проекте. Любое сравнение «сейчас против
биржевой метки» идёт через now_ms(); без сведения он падает, а не подставляет локальное
время (§4.3).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from .models import ClockSync


class ClockNotSynced(RuntimeError):
    pass


_sync: ClockSync | None = None


def local_ms() -> int:
    """Локальные часы. Единственное место, где они читаются напрямую."""
    return time.time_ns() // 1_000_000  # noqa: TID251 — сама реализация часов, §6


def is_synced() -> bool:
    return _sync is not None


def sync_state() -> ClockSync:
    if _sync is None:
        raise ClockNotSynced("часы не сведены с биржей — FOUNDATION.md §6")
    return _sync


def now_ms() -> int:
    """Текущее время в биржевой шкале."""
    return local_ms() + sync_state().offset_ms


def set_sync(s: ClockSync) -> None:
    global _sync
    _sync = s


def reset() -> None:
    global _sync
    _sync = None


async def measure(
    fetch_server_ms: Callable[[], Awaitable[int]],
    samples: int = 5,
) -> ClockSync:
    """Замерить сдвиг по NTP-схеме: сдвиг = server - (t_before + t_after)/2.

    Берётся замер с наименьшим rtt: он наименее искажён сетью.
    """
    if samples < 1:
        raise ValueError("samples >= 1")
    best: tuple[int, int, int] | None = None  # (rtt, offset, t_after)
    for _ in range(samples):
        t0 = local_ms()
        server = await fetch_server_ms()
        t1 = local_ms()
        rtt = t1 - t0
        offset = server - (t0 + t1) // 2
        if best is None or rtt < best[0]:
            best = (rtt, offset, t1)
    assert best is not None
    rtt, offset, t_after = best
    s = ClockSync(offset_ms=offset, rtt_ms=rtt, measured_at_local_ms=t_after, samples=samples)
    set_sync(s)
    return s
