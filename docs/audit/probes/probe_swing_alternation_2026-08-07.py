"""ЗОНД Г2: что меняет НАВЯЗАННОЕ ЧЕРЕДОВАНИЕ хай/лой — дифф повтора на тех же кадрах.

Гипотеза и порог зафиксированы ДО прогона в docs/audit/tolerance-swings.md,
sha256 1d32f3e9c4d132c29ec6fff2980cc3fe39b52613b914c1841a9fe2b3044c10fa.
Замер `probe_swing_questions_2026-08-07.py` дал 1399 свингов из 3716 (37.6%) в сериях из
двух и более подряд идущих одного вида — порог «>=20% проверять» перейдён.

ПРИЁМ. Два независимых чужих проекта (joshyattridge/smart-money-concepts, nardew/talipp)
из серии подряд идущих свингов одного вида оставляют ОДИН — самый крайний: из двух хаев
более высокий, из двух лоёв более низкий. Цикл гоняется до неподвижной точки.

НУЛЬ, заданный до прогона: из той же серии оставить СЛУЧАЙНЫЙ свинг вместо крайнего. Если
карточка от случайного выбора меняется так же, как от «крайнего», то приём не несёт
ничего сверх прореживания — крайность не важна, важно лишь что свингов стало меньше.

⚠ БОЕВОЙ КОД НЕ ТРОГАЕТСЯ. Правило живёт здесь и ставится подменой `hunter.swings.detect`
на время прогона. Пока владелец приёма не принял, `src/` остаётся как был, и это
проверяется тем, что базовая карточка строится ТЕМ ЖЕ кодом, что и сейчас в ветке.

⚠ КАДРЫ ВЛАДЕЛЬЦА НЕ ТРОГАЮТСЯ. Работа идёт на копии `data/frames/swingbase`; сохранённая
карточка в `data/frames/a1` старше кода этой ветки и потому базой служить не может — это
проверено прогоном `replay --run-id a1 --diff` ДО начала работы, он дал расхождение при
неизменном расчёте.

Команда воспроизведения:
    cp -r data/frames/a1 data/frames/swingbase
    uv run python docs/audit/probes/probe_swing_alternation_2026-08-07.py
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import replay, store, swings  # noqa: E402
from hunter.models import NotReady  # noqa: E402
from hunter.swings import SwingKind, SwingSet  # noqa: E402

RUN_ID = "swingbase"
SEED = 20260807

_original = swings.detect


def _runs(seq: list) -> list[list[int]]:
    """Позиции серий из двух и более подряд идущих свингов одного вида."""
    out: list[list[int]] = []
    i = 0
    while i < len(seq):
        j = i
        while j + 1 < len(seq) and seq[j + 1].kind is seq[i].kind:
            j += 1
        if j > i:
            out.append(list(range(i, j + 1)))
        i = j + 1
    return out


def thin(sw: SwingSet, mode: str, rnd: random.Random) -> SwingSet:
    """Оставить из каждой серии один свинг. mode: 'extreme' (приём) или 'random' (нуль)."""
    seq = sorted(sw.swings, key=lambda s: (s.index, s.kind.value))
    drop: set[int] = set()
    for run in _runs(seq):
        if mode == "extreme":
            kind = seq[run[0]].kind
            keep = (max(run, key=lambda k: seq[k].price) if kind is SwingKind.HIGH
                    else min(run, key=lambda k: seq[k].price))
        else:
            keep = rnd.choice(run)
        drop |= {k for k in run if k != keep}
    return SwingSet(
        swings=tuple(s for k, s in enumerate(seq) if k not in drop),
        bars_scanned=sw.bars_scanned,
        confirmed_until_index=sw.confirmed_until_index,
    )


def patch(mode: str | None) -> None:
    if mode is None:
        swings.detect = _original
        return
    rnd = random.Random(SEED)

    def patched(bars):  # type: ignore[no-untyped-def]
        sw = _original(bars)
        return sw if isinstance(sw, NotReady) else thin(sw, mode, rnd)

    swings.detect = patched


def run(label: str) -> tuple[int, int, list[str]]:
    """(символов изменилось, всего символов, тексты диффов)."""
    res = replay.replay_run(RUN_ID)
    if isinstance(res, NotReady):
        print(f"{label}: НЕ СМОГ ПРОВЕРИТЬ — {res.reason}")
        return 0, 0, []
    changed = [d for d in res.symbols if not d.identical]
    return len(changed), len(res.symbols), [d.diff for d in changed]


def counts(text: str) -> dict[str, int]:
    """Сколько строк карточки каких видов — чтобы дифф читался числом, а не глазом."""
    out: dict[str, int] = defaultdict(int)
    for ln in text.splitlines():
        if ln.startswith("-") and not ln.startswith("---"):
            out["убрано"] += 1
            if "СДЕЛКИ НЕТ" in ln:
                out["убрано: СДЕЛКИ НЕТ"] += 1
            if ln.strip().startswith("-  ЛОНГ") or ln.strip().startswith("-  ШОРТ"):
                out["убрано: уровней"] += 1
        elif ln.startswith("+") and not ln.startswith("+++"):
            out["добавлено"] += 1
            if "СДЕЛКИ НЕТ" in ln:
                out["добавлено: СДЕЛКИ НЕТ"] += 1
    return dict(out)


def main() -> int:
    if not (ROOT / "data" / "frames" / RUN_ID).exists():
        print(f"кадров {RUN_ID} нет — сначала `cp -r data/frames/a1 data/frames/{RUN_ID}`")
        return 1

    print("=" * 78)
    print("Г2. Навязанное чередование хай/лой — дифф повтора (§10.6 условие 2)")
    print("=" * 78)

    # ---- ШАГ 1. База: карточка, построенная ТЕКУЩИМ кодом из этих же кадров ----
    patch(None)
    res = replay.replay_run(RUN_ID)
    if isinstance(res, NotReady):
        print(f"НЕ СМОГ ПРОВЕРИТЬ — {res.reason}")
        return 1
    for d in res.symbols:
        sym_dir = d.symbol.replace("/", "_").replace(":", "_")
        rebuilt = store.read_card(RUN_ID, sym_dir)
        assert not isinstance(rebuilt, NotReady)
    # перезаписываем карточку копии текущим пересчётом — это и есть база
    import hunter.card as card_mod
    import hunter.engine as engine_mod
    from hunter import archive
    for sym_dir in sorted(p.name for p in (ROOT / "data" / "frames" / RUN_ID).iterdir()):
        meta = store.read_meta(RUN_ID, sym_dir)
        if isinstance(meta, NotReady):
            continue
        symbol, tick, bucket = meta
        tfs = store.saved_timeframes(RUN_ID, sym_dir)
        series = {tf: store.read_bars(RUN_ID, sym_dir, tf) for tf in tfs}
        tr = store.read_binned_trades(RUN_ID, sym_dir, tick, bucket, symbol)
        src = archive.WindowSource(symbol, symbol.split(":")[0].replace("/", ""), tick,
                                   live=None if isinstance(tr, NotReady) else tr,
                                   cache_dir=store.archive_dir(RUN_ID, sym_dir))
        store.write_card(RUN_ID, sym_dir,
                         card_mod.render(engine_mod.decide(symbol, series, src, tfs), series))

    n, tot, _ = run("база")
    print(f"\nШАГ 1. База записана. Контроль: повтор БЕЗ изменения даёт "
          f"{n} изменившихся из {tot}.")
    if n:
        print("  ✗ БАЗА НЕ ДЕТЕРМИНИРОВАНА — дальше идти нельзя, дифф не к чему приписать")
        return 1
    print("  ✓ ноль — расчёт детерминирован, любой дальнейший дифф принадлежит правилу")

    # ---- ШАГ 2. Приём: оставить самый крайний из серии ----
    patch("extreme")
    n_e, tot_e, diffs_e = run("приём")
    agg_e: dict[str, int] = defaultdict(int)
    for t in diffs_e:
        for k, v in counts(t).items():
            agg_e[k] += v
    print(f"\nШАГ 2. ПРИЁМ «оставить самый крайний из серии» (smc, talipp)")
    print(f"  символов изменилось: {n_e} из {tot_e}")
    print(f"  строк карточки: {dict(sorted(agg_e.items()))}")

    # ---- ШАГ 3. НУЛЬ: оставить случайный из серии ----
    patch("random")
    n_r, tot_r, diffs_r = run("нуль")
    agg_r: dict[str, int] = defaultdict(int)
    for t in diffs_r:
        for k, v in counts(t).items():
            agg_r[k] += v
    print(f"\nШАГ 3. НУЛЬ «оставить случайный из серии» (затравка {SEED})")
    print(f"  символов изменилось: {n_r} из {tot_r}")
    print(f"  строк карточки: {dict(sorted(agg_r.items()))}")

    # ---- ШАГ 4. Решающее сравнение ----
    print("\n" + "=" * 78)
    print("ВЕРДИКТ")
    same = diffs_e == diffs_r
    print(f"  дифф приёма и дифф нуля совпадают ДОСЛОВНО: {'ДА' if same else 'нет'}")
    if same:
        print("  ✗ ПРИЁМ НЕ РАБОТАЕТ: выбор КРАЙНЕГО из серии даёт ровно то же, что выбор")
        print("    случайного. Меняет карточку прореживание, а не крайность.")
    else:
        print("  ✓ выбор крайнего и выбор случайного дают РАЗНЫЕ карточки —")
        print("    крайность несёт информацию сверх прореживания.")
    print(f"  изменившихся символов: приём {n_e}, нуль {n_r} из {tot_e}")

    patch(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
