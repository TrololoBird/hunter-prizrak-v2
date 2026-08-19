"""Наслоение встречных зон — счёт ПО ТЕКСТУ КАРТОЧКИ, а не по объекту в памяти.

Так требует правило «прибор обязан смотреть на ТУ ЖЕ величину, которую видит владелец»:
все четыре дефекта смены 17–18 августа нашлись глазами по карточке, и ни один — гейтом
по внутреннему полю.

Воспроизведение:
    uv run python docs/audit/evidence/E-unresolved-keeps-level-alive-2026-08-19/count_overlaps.py КАРТОЧКА.txt

Карточка строится из кадров: `uv run python -m hunter replay --run-id <прогон> --card`.

ОТПЕЧАТОК ДАННЫХ замера 2026-08-19: прогон `ondo-deep`, ONDO/USDT:USDT, кадров 8,
строк карточки 12612 (до правки) / 12611 (после).
"""
import re
import sys
from collections import Counter

# «  ШОРТ 15м  ПОК 0.32495  зона 0.3244…0.3252  объём 7229994.6  активен»
ROW = re.compile(
    r"^\s{2}(ЛОНГ|ШОРТ)\s+(\S+)\s+ПОК\s+(\S+)\s+зона\s+(\S+)…(\S+)\s+объём\s+(\S+)\s+(\S+)")


def parse(path: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in open(path, encoding="utf-8"):
        m = ROW.match(line.rstrip("\n"))
        if m:
            out.append({"side": m.group(1), "tf": m.group(2), "price": float(m.group(3)),
                        "lo": float(m.group(4)), "hi": float(m.group(5)),
                        "state": m.group(7)})
    return out


def overlaps(rows: list[dict[str, object]]) -> tuple[int, int]:
    """Пары ЛОНГ×ШОРТ с пересекающимися зонами: (одного ТФ, разных ТФ)."""
    longs = [r for r in rows if r["side"] == "ЛОНГ"]
    shorts = [r for r in rows if r["side"] == "ШОРТ"]
    same = cross = 0
    for a in longs:
        for b in shorts:
            if a["lo"] < b["hi"] and b["lo"] < a["hi"]:
                if a["tf"] == b["tf"]:
                    same += 1
                else:
                    cross += 1
    return same, cross


def main() -> int:
    if len(sys.argv) < 2:
        print("нужен путь к карточке", file=sys.stderr)
        return 2
    rows = parse(sys.argv[1])
    if not rows:
        print("ПЛОХО: в файле не разобрано ни одной строки уровня — "
              "формат карточки изменился, числа ниже были бы ложным нулём", file=sys.stderr)
        return 2
    states = Counter(str(r["state"]) for r in rows)
    active = [r for r in rows if r["state"] == "активен"]
    same, cross = overlaps(active)
    print(f"строк уровней разобрано: {len(rows)}")
    print(f"состояния: {dict(states.most_common())}")
    print(f"АКТИВНЫХ: {len(active)}  (лонгов {sum(1 for r in active if r['side'] == 'ЛОНГ')},"
          f" шортов {sum(1 for r in active if r['side'] == 'ШОРТ')})")
    print(f"ВСТРЕЧНЫХ ПАР ЗОН: {same + cross}  — одного ТФ {same}, разных ТФ {cross}")

    # КОНТРОЛЬ 1: прибор обязан различать выборки. Те же пары по ТФ поодиночке —
    # на старших ТФ списки непустые, а пар быть не должно.
    print("\nКОНТРОЛЬ 1 — по таймфреймам (непустой список без пар показывает, что "
          "прибор не печатает пары везде подряд):")
    for tf in sorted({str(r["tf"]) for r in active}):
        sub = [r for r in active if r["tf"] == tf]
        s, _ = overlaps(sub)
        print(f"  {tf:>4}: лонгов {sum(1 for r in sub if r['side'] == 'ЛОНГ'):>3},"
              f" шортов {sum(1 for r in sub if r['side'] == 'ШОРТ'):>3} → пар {s}")

    # КОНТРОЛЬ 2: заведомо наслаивающиеся данные. Если сдвинуть все шорты на цену
    # лонгов, пар обязано стать много — иначе прибор слеп к пересечению как таковому.
    longs = [r for r in active if r["side"] == "ЛОНГ"]
    if longs:
        width = (longs[0]["hi"] - longs[0]["lo"]) or 1e-9
        planted = longs + [dict(r, side="ШОРТ", tf=r["tf"],
                                lo=r["lo"] + width * 0.1, hi=r["hi"] + width * 0.1)
                           for r in longs]
        s, _ = overlaps(planted)
        print(f"\nКОНТРОЛЬ 2 — подсажено наслоение (каждый лонг продублирован шортом "
              f"со сдвигом 10% ширины зоны): пар одного ТФ {s} при {len(longs)} лонгах")
        if s < len(longs):
            print("  ПЛОХО: подсаженное наслоение не найдено — прибор слеп")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
