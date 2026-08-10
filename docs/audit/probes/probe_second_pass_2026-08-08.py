"""ЗОНД: второй проход движка за ЧУЖОЙ границей — живой ли он и не зависит ли от порядка ТФ.

ЗАМЕР ОТКАЧЕННОГО МЕХАНИЗМА: docs/audit/accumulation-projects-2026-08-07.md

⚠ ЭТОТ ПРОБНИК БОЛЬШЕ НЕ ЗАПУСКАЕТСЯ, и это не поломка, а след отката. Механизм
«граница базы с чужого ТФ» был внесён 2026-08-08 и в тот же день СНЯТ: он не прошёл
контроль на заведомо неверных данных — доля границ, взятых у чужого ТФ, на ETH
составила 22% против 23% НА СДВИНУТЫХ ценах, то есть прибор не различал уровень и
случайную цену (разбор — в докстроке `accumulation.BorderSource`). Вместе с механизмом
ушёл третий аргумент `engine.read_series(series, tfs, anchors)`, и вызовы ниже
обращаются к сигнатуре, которой больше нет.

Файл сохранён намеренно: он и есть СВИДЕТЕЛЬСТВО замера, из-за которого механизм сняли.
Починить его нельзя — чинить нечего; вернуть работоспособность можно только вместе с
самим механизмом, а его вернуть не за что.

Курс называет границей базы чужой уровень: ПОК стопового объёма (стр. 39) и уровень
старшего ТФ (стр. 46, 54). Уровни считаются ИЗ структур, поэтому подать их обратно можно
только вторым проходом. Правка предрешена курсом; этот зонд проверяет ДВЕ вещи, без которых
её нельзя показывать.

  П1  ЖИВОСТЬ. Сколько структур получили границу от ЧУЖОГО ТФ. Ноль означал бы, что второй
      проход — удвоение работы без единого следствия, и правку надо снимать.
  П2  НЕЗАВИСИМОСТЬ ОТ ПОРЯДКА ТФ. Разметка прохода 2 при обратном порядке ТФ обязана
      совпасть побитово. Это ровно тот класс дефекта, который у проекта уже был
      («контрольный скрипт считал старшие ТФ позже младших»), и утверждение из докстроки
      `engine.foreign_borders` без этой проверки остаётся обещанием.

      ⚠ Первая редакция П2 переставляла ТФ на входе `decide` — и это почти ничего не
      проверяло: `decide` сортирует ТФ первой же строкой, так что перестановка гасла до
      механизма чужих границ. Порядок теперь ломается в самом проходе 2, а сверка карточки
      оставлена вторым, более слабым слоем.

⚠ П2 — не формальность. Утверждение «порядок не важен» держится на том, что все чужие цены
берутся из ПРОХОДА 1, где чужих границ не было ни у кого. Если кто-то однажды подаст в
проход 2 результат прохода 2, зонд это поймает.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_second_pass_2026-08-08.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import archive, card, engine, levels, store  # noqa: E402
from hunter.models import NotReady  # noqa: E402


def main() -> int:
    frames = ROOT / "data" / "frames"
    runs = sorted(p.name for p in frames.iterdir() if p.is_dir()) if frames.exists() else []
    if not runs:
        print("Кадров нет: data/frames пуст. Замер не выполнен.")
        return 1

    # ⚠ ПРЕДЕЛ НАЗВАН, а не спрятан. Каждый символ здесь стоит ТРЁХ полных проходов
    # движка (проход 1, проход 2 и две карточки для П2), и полный корпус из 25 наборов
    # кадров считался больше десяти минут; четыре набора тоже не уложились в десять.
    # Берётся ОДИН набор, названный явно, и печатается, сколько осталось за бортом.
    # Молчаливая усечёнка читалась бы как «покрыто всё».
    #
    # Почему одного достаточно для П2: независимость от порядка ТФ — свойство УСТРОЙСТВА
    # (все чужие цены берутся из прохода 1), а не статистики. Один символ с шестью ТФ либо
    # ловит зависимость, либо её нет. Для П1 одного набора мало, и это сказано в выводе.
    RUNS_LIMIT = 1
    prefer = "alt-base"
    if prefer in runs:
        runs = [prefer] + [r for r in runs if r != prefer]

    checked = 0
    own = foreign = 0
    order_bad: list[str] = []
    skipped_runs = max(0, len(runs) - RUNS_LIMIT)

    for run in runs[:RUNS_LIMIT]:
        for sym_dir in sorted(p.name for p in (frames / run).iterdir() if p.is_dir()):
            meta = store.read_meta(run, sym_dir)
            if isinstance(meta, NotReady):
                continue
            symbol, tick, bucket = meta
            tfs = store.saved_timeframes(run, sym_dir)
            if len(tfs) < 2:
                continue
            series = {tf: store.read_bars(run, sym_dir, tf) for tf in tfs}
            tr = store.read_binned_trades(run, sym_dir, tick, bucket, symbol)
            src = archive.WindowSource(
                symbol, symbol.split(":")[0].replace("/", ""), tick,
                live=None if isinstance(tr, NotReady) else tr,
                cache_dir=store.archive_dir(run, sym_dir),
            )
            checked += 1

            # --- П1: живость чужой границы ---------------------------------
            first_reads, _ = engine.read_series(series, tfs)
            first_levels, _ = levels.build_all(
                symbol, series, src, tfs,
                {tf: r.scan for tf, r in first_reads.items()},
            )
            anchors = engine.foreign_borders(first_levels, tfs)
            second_reads, _ = engine.read_series(series, tfs, anchors)
            for tf, r in second_reads.items():
                base = {a.first_index: a for a in first_reads[tf].scan.closed}
                for acc in r.scan.closed:
                    for side in (acc.upper, acc.lower):
                        if not side.inherited:
                            continue
                        was = base.get(acc.first_index)
                        same = was is not None and (
                            side.edge in (was.upper.edge, was.lower.edge)
                        )
                        if same:
                            own += 1      # такая же граница была и без чужих цен
                        else:
                            foreign += 1  # граница появилась только со вторым проходом

            # --- П2: независимость от порядка ТФ ---------------------------
            # ⚠ Проверять через `decide` почти бесполезно: он СОРТИРУЕТ ТФ первой строкой,
            # и перестановка на входе гаснет раньше, чем дойдёт до чужих границ. Такая
            # проверка подтверждала бы сортировку, а не механизм. Поэтому порядок ломается
            # там, где он мог бы навредить, — в самом проходе 2.
            back = tuple(reversed(tfs))
            rev_reads, _ = engine.read_series(series, back, anchors)
            for tf in tfs:
                a = second_reads.get(tf)
                b = rev_reads.get(tf)
                if (a is None) != (b is None):
                    order_bad.append(f"{run}/{symbol} {tf}: ТФ пропал при перестановке")
                    continue
                if a is None or b is None:
                    continue
                if a.scan != b.scan:
                    order_bad.append(f"{run}/{symbol} {tf}: разметка зависит от порядка ТФ")

            # Сверх того — карточка целиком, уже через decide: это проверяет не механизм,
            # а что ничей другой шаг конвейера порядок не подхватил.
            straight = card.render(engine.decide(symbol, series, src, tfs), series)
            flipped = card.render(engine.decide(symbol, series, src, back), series)
            if straight != flipped:
                order_bad.append(f"{run}/{symbol}: карточка зависит от порядка ТФ")

    print("=" * 78)
    print(f"СИМВОЛОВ ПРОВЕРЕНО: {checked} (наборов кадров {min(len(runs), RUNS_LIMIT)} "
          f"из {len(runs)}, за бортом {skipped_runs})")
    print("=" * 78)

    print("\nП1. ЖИВОСТЬ чужой границы")
    print(f"  границ, изменившихся ТОЛЬКО из-за второго прохода: {foreign}")
    print(f"  границ, совпавших с тем, что было и без него:      {own}")
    if foreign == 0:
        print("  ⚠ НОЛЬ: второй проход удваивает работу и не меняет ни одной границы.")
        print("     Правку надо снимать, а не объяснять.")

    print("\nП2. НЕЗАВИСИМОСТЬ ОТ ПОРЯДКА ТФ")
    if order_bad:
        print(f"  ⚠ ПРОВАЛ на {len(order_bad)} символах — карточка зависит от порядка ТФ:")
        for s in order_bad[:5]:
            print(f"      {s}")
        print("     Это тот самый класс дефекта, который уже был у проекта.")
        return 1
    print(f"  карточка совпала побитово при обратном порядке ТФ на всех {checked} символах ✅")
    return 0 if foreign else 1


if __name__ == "__main__":
    raise SystemExit(main())
