"""Пробник замка записи `barstore.append`: два процесса пишут ОДИН ряд одновременно.

Каждый писатель дописывает свои 300 баров (чётные/нечётные метки) десятью порциями.
Без замка слияние read-modify-write теряет порции проигравшего; с замком в файле
обязаны оказаться ВСЕ 600 меток. Ряд пишется под именем `probe-venue` — боевые файлы
`data/bars/binanceusdm/…` не затрагиваются, каталог пробы стирается в конце.

Контроль «прибор мог ответить иначе»: тот же прогон с выключенным замком
(--no-lock подменяет _append_lock пустым контекстом) обязан потерять бары.

Воспроизведение:
    uv run python docs/audit/evidence/barstore-lock-probe-2026-08-18.py
    uv run python docs/audit/evidence/barstore-lock-probe-2026-08-18.py --no-lock
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

VENUE = "probe-venue"
MARKET, TF = "PROBEUSDT", "1m"
STEP = 60_000
PER_WRITER, CHUNKS = 300, 10

WORKER = """
import sys, contextlib
sys.path.insert(0, r"{src}")
from hunter import barstore
from hunter.models import Bar
if "{mode}" == "no-lock":
    barstore._append_lock = contextlib.nullcontext
parity = int(sys.argv[1])
per, chunks, step = {per}, {chunks}, {step}
for c in range(chunks):
    bars = [Bar(open_ms=(parity + 2 * (c * per // chunks + i)) * step,
                open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0)
            for i in range(per // chunks)]
    barstore.append("{venue}", "{market}", "{tf}", bars)
"""


def run(mode: str) -> int:
    from hunter import barstore
    shutil.rmtree(barstore.BARS_DIR / VENUE, ignore_errors=True)
    src = str(Path(__file__).resolve().parents[3] / "src")
    code = WORKER.format(src=src, mode=mode, per=PER_WRITER, chunks=CHUNKS,
                         step=STEP, venue=VENUE, market=MARKET, tf=TF)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(parity)])
             for parity in (0, 1)]
    for p in procs:
        p.wait()
    got = len(barstore.load(VENUE, MARKET, TF))
    shutil.rmtree(barstore.BARS_DIR / VENUE, ignore_errors=True)
    return got


if __name__ == "__main__":
    mode = "no-lock" if "--no-lock" in sys.argv else "lock"
    want = 2 * PER_WRITER
    got = run(mode)
    print(f"режим {mode}: баров в файле {got} из {want} "
          f"({'ПОЛНОСТЬЮ' if got == want else 'ПОТЕРЯНО ' + str(want - got)})")
    sys.exit(0 if (got == want) == (mode == "lock") else 1)
