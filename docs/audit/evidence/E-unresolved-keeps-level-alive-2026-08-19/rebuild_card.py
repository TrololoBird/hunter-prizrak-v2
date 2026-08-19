"""Пересобрать карточку ТЕКУЩИМ кодом из сохранённых кадров прогона (§10.6 условие 2).

⚠ ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. В CLI такой команды НЕТ, и это найдено аудитом 2026-08-19:
`hunter replay --run-id <прогон> --card <каталог>` печатает СОХРАНЁННУЮ карточку
(`store.read_card`), то есть текст, порождённый кодом на момент прогона, а не текущим.
`hunter replay --diff` пересчитывает, но печатает только РАЗНИЦУ и не отдаёт сам текст,
а мера здесь снимается со СТРОК КАРТОЧКИ. Первая редакция этого каталога называла
командой воспроизведения `--card` — команда неисполнима в том смысле, в каком была
названа. Гейт `repro_commands` это не ловит: он проверяет наличие строки, похожей на
команду, а не то, что она делает.

Путь пересборки — ТОТ ЖЕ, что у `replay.replay_symbol`: те же кадры, тот же источник
профиля, `card.render(engine.decide(...))`. Отличие одно: результат пишется в файл.

    uv run python docs/audit/evidence/E-unresolved-keeps-level-alive-2026-08-19/rebuild_card.py \
        ondo-deep ONDO_USDT_USDT КУДА.txt
"""

import sys

from hunter import card, engine, store
from hunter.models import NotReady
from hunter.profile_source import TVWindows


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    run_id, dir_name, out = sys.argv[1], sys.argv[2], sys.argv[3]
    meta = store.read_meta(run_id, dir_name)
    if isinstance(meta, NotReady):
        print(f"ПЛОХО: {meta.reason}", file=sys.stderr)
        return 1
    symbol, tick, _bucket = meta
    tfs = store.saved_timeframes(run_id, dir_name)
    if not tfs:
        print(f"ПЛОХО: кадров баров в прогоне {run_id} нет", file=sys.stderr)
        return 1
    series = {tf: store.read_bars(run_id, dir_name, tf) for tf in tfs}
    profile_series = dict(series)
    profile_series.update(store.read_profile_bars(run_id, dir_name))
    text = card.render(
        engine.decide(symbol, series, TVWindows(symbol, tick, profile_series), tfs), series)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    rows = sum(1 for ln in text.splitlines() if ln.startswith(("  ЛОНГ ", "  ШОРТ ")))
    print(f"{out}: строк {len(text.splitlines())}, из них строк уровней {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
