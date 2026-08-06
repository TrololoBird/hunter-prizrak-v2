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


MAX_SYNC_AGE_MS = 3_600_000
"""Возраст сведения, после которого оно считается устаревшим. ⚠ ЧИСЛО НЕ ЗАМЕРЕНО.

Час выбран как порядок величины: замер `clock_drift_ms` на прогонах даёт единицы
миллисекунд за десятки секунд, то есть уход порядка сотен миллисекунд в час. Прямого
замера дрейфа за час нет — он и есть задание на живой службе. Число названо здесь, а не
спрятано в условии, именно потому что не замерено.
"""


def local_ms() -> int:
    """Локальные НАСТЕННЫЕ часы. Читаются только для замера сдвига и для отчёта.

    ⚠ Для вычисления «сейчас» НЕ используются: см. `now_ms` и
    `ClockSync.measured_at_monotonic_ns`.
    """
    return time.time_ns() // 1_000_000  # noqa: TID251 — сама реализация часов, §6


def monotonic_ns() -> int:
    """Монотонные часы. Не идут назад и не прыгают при правке системного времени."""
    return time.monotonic_ns()


# ⚠ `is_synced()` УДАЛЕНА 2026-08-06: потребителя не было ни одного. Вопрос «сведены ли
# часы» ни разу не задавался булевым — `sync_state()` и `now_ms()` падают сами, и это
# правильнее: §4.3 требует названной причины, а `False` её не несёт.


def sync_state() -> ClockSync:
    if _sync is None:
        raise ClockNotSynced("часы не сведены с биржей — FOUNDATION.md §6")
    return _sync


def age_ms() -> int:
    """Сколько прошло с момента сведения, по МОНОТОННЫМ часам."""
    s = sync_state()
    if not s.measured_at_monotonic_ns:
        raise ClockNotSynced("сведение без монотонного якоря — возраст неизвестен")
    return (monotonic_ns() - s.measured_at_monotonic_ns) // 1_000_000


def is_stale(max_age_ms: int = MAX_SYNC_AGE_MS) -> bool:
    """Устарело ли сведение. Решение, что делать, принимает вызывающий."""
    return age_ms() > max_age_ms


def now_ms() -> int:
    """Текущее время в биржевой шкале: якорь плюс монотонно прошедшее.

    ⚠ Настенные часы здесь НЕ участвуют. Прежняя редакция считала
    `local_ms() + offset_ms`, и любой прыжок системного времени сдвигал результат — в том
    числе НАЗАД, что означало бы «закрытые свечи снова не закрыты». На пакетном прогоне
    это было теоретическим риском, на службе 24/7 (§8) — ежедневным.

    Возраст сведения здесь не проверяется намеренно: `now_ms` зовётся в горячих путях
    десятки раз на бар, и падение посреди расчёта хуже, чем устаревший на минуту сдвиг.
    Свежесть — дело того, кто сводит часы; для проверки есть `age_ms` и `is_stale`.
    """
    s = sync_state()
    if not s.measured_at_monotonic_ns:
        raise ClockNotSynced(
            "сведение без монотонного якоря — биржевое время не восстановимо (§6)"
        )
    elapsed_ms = (monotonic_ns() - s.measured_at_monotonic_ns) // 1_000_000
    return s.server_ms_at_sync + elapsed_ms


def set_sync(s: ClockSync) -> None:
    global _sync
    _sync = s


# ⚠ `reset()` УДАЛЕНА 2026-08-06: потребителя не было. Она возвращала модуль в
# несведённое состояние, и нужна была бы только пробнику §6 («без сведения падает, а не
# подставляет локальное время»). Пробнику она не нужна: `_sync` равен `None` сразу
# после импорта, то есть свежий процесс УЖЕ в этом состоянии.


async def measure(
    fetch_server_ms: Callable[[], Awaitable[int]],
    samples: int = 5,
) -> ClockSync:
    """Замерить сдвиг по NTP-схеме: сдвиг = server - (t_before + t_after)/2.

    Берётся замер с наименьшим rtt: он наименее искажён сетью.
    """
    if samples < 1:
        raise ValueError("samples >= 1")
    best: tuple[int, int, int, int, int] | None = None
    for _ in range(samples):
        t0 = local_ms()
        mono0 = monotonic_ns()
        server = await fetch_server_ms()
        t1 = local_ms()
        mono1 = monotonic_ns()
        rtt = t1 - t0
        offset = server - (t0 + t1) // 2
        if best is None or rtt < best[0]:
            # Якорь ставится на СЕРЕДИНУ замера: серверная метка относится к моменту
            # где-то внутри окна запроса, и середина — наименее смещённая её оценка,
            # та же, из которой считается сдвиг.
            best = (rtt, offset, t1, server, (mono0 + mono1) // 2)
    assert best is not None
    rtt, offset, t_after, server_ms, mono_mid = best
    s = ClockSync(offset_ms=offset, rtt_ms=rtt, measured_at_local_ms=t_after,
                  samples=samples, server_ms_at_sync=server_ms,
                  measured_at_monotonic_ns=mono_mid)
    set_sync(s)
    return s
