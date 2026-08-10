"""Пробник: дорешивание исходов у сигналов, которые прогон заново не эмитировал.

Повод — находка сводки исходов 2026-08-10: исход считался ТОЛЬКО для сигналов из
`decided[sym].emissions`, поэтому сигнал, чей уровень ушёл из карты, не досчитывался
никогда (76 из 112 на боевом леджере). Схема v5 добавила цель в леджер, а `run` —
проход `_resolve_pending`.

Контроли фальсифицируемости (прибор обязан отвечать ПО-РАЗНОМУ):

1. сигнал с целью, цена доходит до ЦЕЛИ → исход `target`, R положителен;
2. тот же сигнал, но цена идёт в СТОП → исход `stop`, R отрицателен (прибор не заперт
   в одном ответе);
3. цена не доходит до входа → исхода НЕТ, записано состояние `not_filled`;
4. сигнал БЕЗ цели (запись до v5) → не дорешивается, счётчик `pending_no_target` растёт
   — то есть отказ назван числом, а не молчанием;
5. бары ДО `recorded_at` в расчёт не идут: тот же ряд, но сигнал записан позже всех
   баров → исхода нет (журнал, а не бэктест — §8 этап 7).

Боевая база не трогается: всё во временном леджере.

Запуск:
    uv run python docs/audit/probes/probe_resolve_pending_2026-08-10.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import run as hrun  # noqa: E402
from hunter import store  # noqa: E402
from hunter.config import Universe  # noqa: E402
from hunter.models import Bar, ClockSync, RunReport, SeriesState  # noqa: E402

TF = "1h"
STEP = 3_600_000
T0 = 1_700_000_000_000 // STEP * STEP
SYM = "TEST/USDT:USDT"


def bars(*ohlc: tuple[float, float, float, float]) -> list[Bar]:
    return [Bar(open_ms=T0 + i * STEP, open=o, high=h, low=lo, close=c, volume=1.0)
            for i, (o, h, lo, c) in enumerate(ohlc)]


def report_with(series: list[Bar]) -> RunReport:
    rep = RunReport(sync=ClockSync(offset_ms=0, rtt_ms=1, measured_at_local_ms=T0,
                                   samples=1))
    rep.series[(SYM, TF)] = SeriesState(symbol=SYM, timeframe=TF, bars=series)
    return rep


def case(name: str, series: list[Bar], *, target: Decimal | None,
         recorded_at: int) -> tuple[str, int | None, str, int, int]:
    """Прогнать дорешивание на подсадном леджере. Отдаёт (исход, R, состояние, отказы)."""
    # ⚠ Каталог убирается РУКАМИ, а не `with`: на Windows SQLite держит файл открытым
    # до закрытия соединения, и автоочистка временного каталога падает PermissionError
    # ещё до того, как будет виден настоящий результат случая.
    tmp = Path(tempfile.mkdtemp(prefix="hunter-pending-"))
    try:
        path = tmp / "ledger.sqlite3"
        conn = store.open_production_ledger(path)
        row = store.record_signal(
            conn, SYM, TF, "long", T0, Decimal("100"), Decimal("95"),
            "probe", recorded_at, target=target,
        )
        assert not isinstance(row, str) and hasattr(row, "id"), row
        rep = report_with(series)
        hrun._resolve_pending(conn, rep, Universe(symbols=(SYM,), timeframes=(TF,),
                                                  source=Path("пробник")))
        out = conn.execute(
            "SELECT kind, r FROM outcomes WHERE signal_id=?", (row.id,)).fetchone()
        st = conn.execute(
            "SELECT state FROM signal_states WHERE signal_id=?", (row.id,)).fetchone()
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    kind = out[0] if out else "нет"
    r = None if not out else out[1]
    return (kind, r, st[0] if st else "нет",
            rep.pending_no_target, rep.outcomes_resolved_late)


def main() -> int:
    # Сигнал: лонг, вход 100, стоп 95, цель 110. Бары идут ПОСЛЕ записи (recorded_at=T0).
    to_target = bars((101, 101, 99, 100), (100, 111, 99.5, 110))
    to_stop = bars((101, 101, 99, 100), (100, 101, 94, 94.5))
    never_filled = bars((105, 106, 101, 104), (104, 107, 102, 106))

    k1, r1, _, _, late1 = case("цель", to_target, target=Decimal("110"),
                               recorded_at=T0)
    assert k1 == "target" and r1 is not None and r1 > 0, (k1, r1)
    assert late1 == 1, late1

    k2, r2, _, _, _ = case("стоп", to_stop, target=Decimal("110"), recorded_at=T0)
    assert k2 == "stop" and r2 is not None and r2 < 0, (k2, r2)

    k3, _, s3, _, late3 = case("мимо входа", never_filled, target=Decimal("110"),
                               recorded_at=T0)
    assert k3 == "нет" and s3 == "not_filled" and late3 == 0, (k3, s3, late3)

    k4, _, _, nt4, late4 = case("без цели", to_target, target=None, recorded_at=T0)
    assert k4 == "нет" and nt4 == 1 and late4 == 0, (k4, nt4, late4)

    # Бары ДО записи сигнала: исход по ним не считается (журнал, а не бэктест).
    k5, _, _, _, late5 = case("бары до записи", to_target, target=Decimal("110"),
                              recorded_at=T0 + 99 * STEP)
    assert k5 == "нет" and late5 == 0, (k5, late5)

    print(f"OK 1. цена дошла до цели      → исход {k1}, R {r1:+.2f}, дорешано {late1}")
    print(f"OK 2. цена ушла в стоп        → исход {k2}, R {r2:+.2f} "
          f"(прибор не заперт в одном ответе)")
    print(f"OK 3. цена не дошла до входа  → исхода {k3}, состояние {s3}")
    print(f"OK 4. сигнал без цели         → исхода {k4}, отказ назван числом: "
          f"без_цели={nt4}")
    print(f"OK 5. бары до записи сигнала  → исхода {k5} (§8 этап 7: журнал, не бэктест)")
    print("OK все пять контролей прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
