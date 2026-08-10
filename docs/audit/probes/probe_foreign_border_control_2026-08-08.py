"""ЗОНД-КОНТРОЛЬ, которым ОТМЕНЁН механизм чужой границы (коммит отката 0f07c3b).

ЗАМЕР ОТКАЧЕННОГО МЕХАНИЗМА: docs/audit/accumulation-projects-2026-08-07.md

⚠ ПОСЛЕ ОТКАТА НЕ ЗАПУСКАЕТСЯ, и это следствие его же вывода. Зонд звал
`engine.read_series(series, tfs, anchors)`; вместе со снятым механизмом ушёл и третий
аргумент. Файл сохранён как свидетельство: именно его числа отменили правку боевого
кода, и удалить их значило бы оставить откат без доказательства (гейт
`gates/probes_callable.py` считает такие файлы отдельной строкой, а не молчит о них).

⚠⚠ ЭТОТ ФАЙЛ ЗАКРЫВАЕТ ДЕФЕКТ, А НЕ ТОЛЬКО МЕРЯЕТ. Числа «BTC 30% против 25% и 19%,
ETH 22% против 23% НА СДВИНУТЫХ» были получены 2026-08-08 ЧЕРНОВЫМ СКРИПТОМ во временном
каталоге сессии, попали в сообщение коммита и в докстроку `BorderSource` — и зонда в
репозитории не имели. То есть число, которым обоснована правка боевого кода, было
невоспроизводимо: ровно то, что §7.3 запрещает. Скрипт перенесён сюда дословно, логика
замера не менялась ни на строку, и числа перепроверены прогоном.

ГДЕ ГОНЯТЬ. Механизм откачен, `engine.foreign_borders` и `BorderSource.FOREIGN` в дереве
больше нет. Зонд гоняется на коммите, где они были, — 7213f00:

    git worktree add --detach /tmp/pin 7213f00
    cmd //c mklink /J C:\\tmp\\pin\\data C:\\...\\hunter-v2\\data   # кадры 4.3 ГБ, не копировать
    cp docs/audit/probes/probe_foreign_border_control_2026-08-08.py /tmp/pin/
    cd /tmp/pin && uv run python probe_foreign_border_control_2026-08-08.py

⚠ Коммит именно 7213f00, а не f5e17b1 («Второй проход движка»). На f5e17b1 зонд падает:
поле `BoundaryZone.source` появилось только следующим коммитом, и без него считать долю
нечем. Найдено прогоном — сначала я закрепил зонд не на том коммите.

ЧТО МЕРЯЕТСЯ. Правило второго прохода было: цена уровня ЧУЖОГО ТФ становится границей
базы, если попадает между первыми двумя точками этой стороны. Доля границ, взятых таким
образом, считается на четырёх наборах анкеров:
  * настоящие уровни прохода 1;
  * те же уровни, СДВИНУТЫЕ на +0.7% — цена заведомо не та;
  * РЕШЁТКА равноотстоящих цен того же числа и в том же диапазоне;
  * случайные цены из того же диапазона (затравка 1, замер детерминирован).

КАК ЧИТАТЬ. CLAUDE.md: всякое «совпало N из M» обязано сопровождаться тем же замером на
данных, про которые заведомо известно, что совпадать они не должны. Если доли близки —
критерий не различает уровень и случайную цену, и «живость» признака была свойством
количества анкеров, а не их осмысленности.

ЧТО ПОЛУЧИЛОСЬ (прогон 2026-08-08 на коммите 7213f00, кадры прогона `last`):
    BTC  анкеров 665   настоящие 30%   сдвиг 27%   решётка 25%   случайные 19%
    ETH  анкеров 675   настоящие 22%   сдвиг 23%   решётка 20%   случайные 18%
На ETH заведомо неверные цены дали БОЛЬШЕ, чем настоящие. Механизм снят.

⚠ СИМВОЛОВ ПРОВЕРЕНО ДВА ИЗ ТРЁХ. У BCH проход 1 не даёт ни одного уровня, анкеров нет,
и контроль на нём невозможен вовсе. Значит вывод опирается на две выборки, а не на три,
и это ограничение замера, а не свойство рынка.

⚠ Первая редакция ЭТОГО файла несла третью строку — «BCH анкеров 675, настоящие 26%». Её
не существует: я выписал её по памяти, восстанавливая замер из черновика, и первый же
прогон её опроверг. Число, которое помнят, и число, которое меряют, — разные вещи; ровно
поэтому черновой скрипт обязан становиться зондом в тот же день, а не «когда понадобится».

⚠ Что это НЕ означает. Требование курса — чужой уровень БЫВАЕТ границей базы (стр. 39
ПОК стопового объёма, стр. 46 и 54 уровень старшего ТФ) — остаётся невыполненным.
Опровергнут мой критерий «любой анкер внутри полосы», которого курс не даёт. Вернуть
механизм можно только с избирательным критерием, прошедшим ЭТОТ ЖЕ контроль.
"""

import random
import sys
from collections import Counter

sys.path.insert(0, "src")

from hunter import archive, engine, levels, store  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.models import NotReady  # noqa: E402

RUN = "last"

SHIFT = 1.007
"""Сдвиг заведомо неверных анкеров. Мал настолько, что порядок цен сохраняется, —
иначе «не сошлось» объяснялось бы уходом за пределы ряда, а не неверностью цены."""


def rate(series, tfs, anchors):
    """Доля границ, взятых у чужого ТФ, при данном наборе анкеров."""
    reads, _ = engine.read_series(series, tfs, anchors)
    c = Counter()
    for r in reads.values():
        for a in r.scan.closed:
            for z in (a.upper, a.lower):
                c[z.source.value] += 1
    tot = sum(c.values())
    return c, tot


def main() -> int:
    checked = 0
    for d in store.saved_symbols(RUN):
        meta = store.read_meta(RUN, d)
        if isinstance(meta, NotReady):
            continue
        symbol, tick, bucket = meta
        tfs = tuple(sorted(store.saved_timeframes(RUN, d),
                           key=lambda t: TIMEFRAME_MS.get(t, 0)))
        series = {tf: store.read_bars(RUN, d, tf) for tf in tfs}
        tr = store.read_binned_trades(RUN, d, tick, bucket, symbol)
        binned = None if isinstance(tr, NotReady) else tr
        src = archive.WindowSource(symbol, symbol.split(":")[0].replace("/", ""), tick,
                                   live=binned, cache_dir=store.archive_dir(RUN, d))
        first_reads, _ = engine.read_series(series, tfs)
        first_levels, _ = levels.build_all(symbol, series, src, tfs,
                                           {tf: r.scan for tf, r in first_reads.items()})
        if not first_levels:
            print(f"{d}: уровней прохода 1 нет — контроль невозможен")
            continue
        real = engine.foreign_borders(first_levels, tfs)

        shifted = {tf: tuple((p * SHIFT, t) for p, t in v) for tf, v in real.items()}

        allp = [p for v in real.values() for p, _ in v]
        lo, hi = min(allp), max(allp)
        rnd = random.Random(1)
        grid = {}
        for tf, v in real.items():
            n = len(v)
            grid[tf] = tuple((lo + (hi - lo) * i / max(n - 1, 1), t)
                             for i, (_, t) in enumerate(sorted(v, key=lambda x: x[1])))
        noise = {tf: tuple((rnd.uniform(lo, hi), t) for _, t in v) for tf, v in real.items()}

        print(f"\n{d}  анкеров всего {sum(len(v) for v in real.values())}")
        shares = {}
        for tag, anc in (("настоящие уровни", real), (f"сдвиг +{(SHIFT - 1) * 100:.1f}%", shifted),
                         ("равномерная решётка", grid), ("случайные цены", noise)):
            c, tot = rate(series, tfs, anc)
            f = c.get("foreign", 0)
            shares[tag] = f / max(tot, 1)
            print(f"   {tag:22} границ {tot:4}  из них чужой ТФ {f:4}  ({f / max(tot, 1):.0%})")
        worst = max(v for k, v in shares.items() if k != "настоящие уровни")
        if shares["настоящие уровни"] <= worst:
            print("   ⚠ КРИТЕРИЙ НЕ РАЗЛИЧАЕТ: на заведомо неверных ценах вышло "
                  "не меньше, чем на настоящих.")
        checked += 1

    if checked == 0:
        print("Ни одного символа не проверено: кадров прогона `last` нет. Замер не выполнен.")
        return 1
    print(f"\nсимволов проверено: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
