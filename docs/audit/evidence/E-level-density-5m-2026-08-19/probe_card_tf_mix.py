"""Состав карточки по ТФ — замер по ТЕКСТУ артефакта, а не по объекту в памяти.

Правило смены: мера снимается с того, что ЧИТАЕТ ВЛАДЕЛЕЦ. Инвариант, снятый с
внутреннего поля, о предъявленном не говорит ничего.

Повод: стр. 48 требует «должен соблюдаться баланс кол-ва сделок/отложек по СТФ,
локальным уровням и МТФ» и предупреждает «Важно не выставлять сразу много отложек по
МТФ» (стр. 48). МТФ курс определяет на стр. 4 как младший таймфрейм. Меры равновесия
в проекте нет — этот зонд показывает цену её отсутствия.

КОНТРОЛЬ: разбор идёт по РЕГУЛЯРКЕ строки уровня, и сумма по каждому ТФ обязана
сходиться: «сделки нет» + «со сделкой» = уровней. Расхождение печатается — молчаливой
потери строк быть не может.
"""
from __future__ import annotations

import collections
import glob
import re

TFMAP = {"5м": "5m", "15м": "15m", "1ч": "1h", "4ч": "4h", "1Д": "1d", "1Н": "1w"}
HEAD = re.compile(r"\s{2}(ЛОНГ|ШОРТ)\s+(5м|15м|1ч|4ч|1Д|1Н)\s+ПОК")
DEAL = re.compile(r"СДЕЛКА|вход .*стоп|РР ")


def main() -> int:
    lvl: collections.Counter[str] = collections.Counter()
    setup: collections.Counter[str] = collections.Counter()
    nodeal: collections.Counter[str] = collections.Counter()
    files = sorted(glob.glob("data/frames/last/*/card.txt"))
    for f in files:
        cur: str | None = None
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                m = HEAD.match(line)
                if m:
                    cur = TFMAP[m.group(2)]
                    lvl[cur] += 1
                    continue
                if cur and "СДЕЛКИ НЕТ" in line:
                    nodeal[cur] += 1
                    cur = None
                    continue
                if cur and DEAL.search(line):
                    setup[cur] += 1
                    cur = None
    print(f"ОТПЕЧАТОК ДАННЫХ: карточек прочитано {len(files)}, уровней {sum(lvl.values())}")
    print()
    print("СОСТАВ КАРТОЧКИ ПО ТФ:")
    tot = sum(lvl.values())
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        if not lvl[tf]:
            continue
        mark = "" if nodeal[tf] + setup[tf] == lvl[tf] else "  ⚠ СТРОКИ ПОТЕРЯНЫ"
        print(f"  {tf:4} уровней {lvl[tf]:5} ({lvl[tf] / tot * 100:5.1f}%)   "
              f"без сделки {nodeal[tf]:5}   со сделкой {setup[tf]:4}{mark}")
    jun = lvl["5m"] + lvl["15m"]
    js, ts = setup["5m"] + setup["15m"], sum(setup.values())
    print(f"\n  младшие ТФ в карте:   {jun} из {tot} = {jun / tot * 100:.1f}%")
    if ts:
        print(f"  младшие ТФ в сделках: {js} из {ts} = {js / ts * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
