"""ГЕЙТ: инварианты геометрии сделки. FOUNDATION.md §2.7, §7.5.

Почему гейт появился (2026-08-10). Владелец трижды сказал, что «уровни, зоны и геометрия
сломаны судя по html». Разбор показал: сломан был РЕНДЕР (шкала растягивалась дальними
зонами), а движок держал инварианты — но выяснили мы это скриптом на коленке, который
нигде не сохранился и в CI не попал. Проверка, которую нельзя прогнать одной командой,
не прогоняется; проверка, не вписанная в `.github/workflows/ci.yml`, — не гейт, а скрипт.

Что проверяется — четыре инварианта, каждый выводится из курса, а не из кода:

  * стоп СЗАДИ входа по ходу сделки: лонг — ниже, шорт — выше (стр. 33: «стоп за дно
    структуры», стр. 50 для ПП: «со стопом за хай/лой»). Стоп впереди входа означает
    сделку, закрытую в момент открытия;
  * цель ВПЕРЕДИ входа: лонг — выше, шорт — ниже (стр. 24: цель — уровень
    противоположной стороны по ходу). Цель позади входа даёт отрицательный РР — ровно
    этот дефект поймал первый же дифф повтора у `build_pp_setup`;
  * РР строго положителен, когда цель есть, и None, когда цели нет (стр. 9: РР — мера
    сделки; ноль вместо отсутствия — подстановка вместо отказа, §4.3);
  * зона содержит свой ПОК: `zone_lo <= price <= zone_hi` (стр. 30: вход «на зону, и на
    уровень ПОК» — ПОК внутри зоны по построению профиля, VAL <= POC <= VAH).

⚠ УСТРОЙСТВО: проверки отделены от построения намеренно. Инвариант, выраженный тем же
кодом, что строит объект, проверяет сам себя и потому не способен упасть. Здесь функции
`check_*` не знают ни `build_setup`, ни `build_pp_setup`, а гейт применяет их к ДВУМ
источникам: к настоящим объектам расчёта и к ПОДСАЖЕННЫМ нарушениям. Второе обязательно:
гейт, ни разу не поймавший нарушения, неотличим от гейта, который ничего не проверяет
(история проекта: «нарушений 0» у гейта чистоты, не открывшего ни одного файла).
"""

from __future__ import annotations

import sys
from decimal import Decimal

from hunter.geometry import PPSetup, Setup, build_pp_setup, build_setup
from hunter.levels import (
    EntryRule,
    Level,
    LevelSide,
    LevelState,
    LevelStatus,
    MappedLevel,
)
from hunter.pereprior import Pereprior, PPKind, PPSide

T0 = 1_700_000_000_000
HOUR = 3_600_000


# --- инварианты: чистые проверки, ничего не строят ---------------------------


def check_level(lv: Level) -> list[str]:
    """Зона содержит ПОК, а база (ХАЙ…ЛОЙ) содержит и зону, и ПОК.

    ⚠ Проверки «ПОК внутри базы» и «зона внутри базы» добавлены 2026-08-11 по вопросу
    владельца «как ПОК может быть вне базы». Ответ: не может — на стр. 30 коробка
    подписана ХАЙ и ЛОЙ, профиль натянут на неё, зона нарисована двумя линиями ВНУТРИ
    неё, ПОК между ними. С 2026-08-18 границы уровня (`boundary_lo/hi`) — это САМА
    коробка ХАЙ…ЛОЙ, отдельных полей коробки больше нет: пока сущности были две, гейт
    сторожил коробку и был зелёным, а карточка печатала «границами» линии детекции — и
    владелец видел ПОК за границей у 7% уровней. Одна сущность закрыла эту вилку.
    """
    bad = []
    if lv.zone_lo > lv.zone_hi:
        bad.append(f"зона вывернута: lo {lv.zone_lo} > hi {lv.zone_hi}")
    if not (lv.zone_lo <= lv.price <= lv.zone_hi):
        bad.append(f"ПОК {lv.price} вне зоны [{lv.zone_lo}, {lv.zone_hi}]")
    if lv.boundary_lo > lv.boundary_hi:
        bad.append(f"база вывернута: ЛОЙ {lv.boundary_lo} > ХАЙ {lv.boundary_hi}")
    if not (lv.boundary_lo <= lv.price <= lv.boundary_hi):
        bad.append(f"ПОК {lv.price} ВНЕ БАЗЫ [{lv.boundary_lo}, {lv.boundary_hi}] — "
                   f"на стр. 30 ПОК всегда внутри коробки")
    if lv.zone_lo < lv.boundary_lo or lv.zone_hi > lv.boundary_hi:
        bad.append(f"зона [{lv.zone_lo}, {lv.zone_hi}] ШИРЕ базы "
                   f"[{lv.boundary_lo}, {lv.boundary_hi}] — на стр. 30 зона внутри коробки")
    return bad


def check_setup(s: Setup) -> list[str]:
    """Сделка от уровня: стоп сзади, цели впереди, РР положителен."""
    bad = []
    up = s.level.side is LevelSide.LONG
    if up and s.stop >= s.entry:
        bad.append(f"лонг: стоп {s.stop} не ниже входа {s.entry}")
    if not up and s.stop <= s.entry:
        bad.append(f"шорт: стоп {s.stop} не выше входа {s.entry}")
    if not (s.entry_zone_lo <= s.entry <= s.entry_zone_hi):
        bad.append(f"вход {s.entry} вне своей зоны "
                   f"[{s.entry_zone_lo}, {s.entry_zone_hi}]")
    if s.entry not in s.ladder:
        bad.append(f"вход {s.entry} не входит в лестницу закупа {s.ladder}")
    for t in s.targets:
        if up and t.price <= s.entry:
            bad.append(f"лонг: цель {t.price} ({t.timeframe}) не выше входа {s.entry}")
        if not up and t.price >= s.entry:
            bad.append(f"шорт: цель {t.price} ({t.timeframe}) не ниже входа {s.entry}")
    rr = s.rr()
    if rr is not None and rr <= 0:
        bad.append(f"РР {rr} не положителен при существующей цели")
    return bad


def check_pp(s: PPSetup) -> list[str]:
    """Сделка от переприора: то же самое на float-геометрии ПП (стр. 50)."""
    bad = []
    up = s.side == PPSide.LONG.value
    if up and s.stop >= s.entry:
        bad.append(f"лонг ПП: стоп {s.stop} не ниже входа {s.entry}")
    if not up and s.stop <= s.entry:
        bad.append(f"шорт ПП: стоп {s.stop} не выше входа {s.entry}")
    if s.target is not None:
        if up and s.target <= s.entry:
            bad.append(f"лонг ПП: цель {s.target} не выше входа {s.entry}")
        if not up and s.target >= s.entry:
            bad.append(f"шорт ПП: цель {s.target} не ниже входа {s.entry}")
        if s.rr is None or s.rr <= 0:
            bad.append(f"ПП: цель есть, а РР {s.rr}")
    elif s.rr is not None:
        bad.append(f"ПП: цели нет, а РР {s.rr} — подстановка вместо отказа (§4.3)")
    return bad


# --- материал: уровни и переприоры, построенные руками ------------------------


def level(side: LevelSide, poc: str, lo: str, hi: str, *, tf: str = "4h",
          b_lo: str | None = None, b_hi: str | None = None,
          born_h: int = 10) -> Level:
    """Уровень с известной геометрией. Границы по умолчанию шире зоны."""
    return Level(
        symbol="TEST/USDT:USDT", timeframe=tf, side=side,
        price=Decimal(poc), zone_lo=Decimal(lo), zone_hi=Decimal(hi),
        created_at_index=10, created_at_ms=T0 + born_h * HOUR,
        structure_first_index=0, structure_last_index=9,
        structure_from_ms=T0, structure_to_ms=T0 + born_h * HOUR,
        structure_volume=1000.0,
        boundary_lo=Decimal(b_lo if b_lo is not None else lo),
        boundary_hi=Decimal(b_hi if b_hi is not None else hi),
        boundary_narrowed=0, boundary_ladder=False,
    )


def mapped(lv: Level) -> MappedLevel:
    """Уровень с судьбой АКТИВЕН: цель обязана быть живой на момент сигнала (стр. 25,
    43) — иначе `build_targets` её законно отсеет, и проверять было бы нечего."""
    return MappedLevel(level=lv, status=LevelStatus(
        state=LevelState.ACTIVE, event=None, limit_orders_allowed=True,
        entry_rule=EntryRule.LIMIT, resolved_at_ms=None))


def pp(side: PPSide, lo: float, hi: float, *, confirmed: int = 5) -> Pereprior:
    return Pereprior(
        kind=PPKind.TRUE, side=side, broken_index=3, broken_price=(lo + hi) / 2,
        zone_lo=lo, zone_hi=hi, confirmed_at_index=confirmed, tested_at_index=confirmed + 2,
    )


def real_setups() -> list[tuple[str, Setup]]:
    """Сделки от уровня, построенные НАСТОЯЩИМ `build_setup`."""
    long_lv = level(LevelSide.LONG, "100", "99", "101", b_lo="98", b_hi="102")
    short_lv = level(LevelSide.SHORT, "120", "119", "121", b_lo="118", b_hi="122")
    # Пул целей: шортовый уровень выше лонгового и лонговый ниже шортового, оба
    # рождены РАНЬШЕ (иначе `build_targets` их законно отсеет).
    pool = (mapped(level(LevelSide.SHORT, "130", "129", "131", born_h=1)),
            mapped(level(LevelSide.LONG, "90", "89", "91", born_h=1)),
            mapped(level(LevelSide.SHORT, "125", "124", "126", tf="1h", born_h=1)))
    out = [
        ("лонг от 4ч уровня с якорем", build_setup(long_lv, pool,
                                                   structural_anchor=Decimal("97.5"))),
        ("лонг от 4ч уровня без якоря (запас 1-3%)", build_setup(long_lv, pool)),
        ("шорт от 4ч уровня", build_setup(short_lv, pool)),
        ("уровень без пула целей", build_setup(long_lv, ())),
    ]
    return out


def real_pp_setups() -> list[tuple[str, PPSetup]]:
    """Сделки от ПП, построенные НАСТОЯЩИМ `build_pp_setup`."""
    short = pp(PPSide.SHORT, 100.0, 101.0)
    long_ = pp(PPSide.LONG, 90.0, 91.0)
    far_long = pp(PPSide.LONG, 110.0, 111.0)   # выше входа шорта — целью быть не может
    far_short = pp(PPSide.SHORT, 80.0, 81.0)   # ниже входа лонга — тоже
    return [
        ("шорт ПП с целью впереди", build_pp_setup(short, long_)),
        ("шорт ПП, противоположный ПОЗАДИ входа", build_pp_setup(short, far_long)),
        ("шорт ПП без противоположного", build_pp_setup(short, None)),
        ("лонг ПП с целью впереди", build_pp_setup(long_, short)),
        ("лонг ПП, противоположный ПОЗАДИ входа", build_pp_setup(long_, far_short)),
        ("лонг ПП без противоположного", build_pp_setup(long_, None)),
    ]


# --- контроль: подсаженные нарушения ------------------------------------------


def planted() -> list[tuple[str, list[str]]]:
    """Заведомо неверные объекты. Каждый ОБЯЗАН дать непустой список нарушений.

    Собираются в обход построителей — прямой сборкой моделей: смысл контроля в том,
    чтобы проверить сами проверки, а не расчёт.
    """
    bad_level = level(LevelSide.LONG, "105", "99", "101")  # ПОК вне своей зоны
    poc_outside = level(LevelSide.LONG, "100", "99", "101").model_copy(
        update={"boundary_lo": Decimal("101.5"), "boundary_hi": Decimal("103")})
    zone_wider = level(LevelSide.LONG, "100", "95", "105").model_copy(
        update={"boundary_lo": Decimal("99"), "boundary_hi": Decimal("101")})
    long_lv = level(LevelSide.LONG, "100", "99", "101", b_lo="98", b_hi="102")
    good = build_setup(long_lv, ())
    stop_ahead = good.model_copy(update={"stop": Decimal("103")})  # стоп ВЫШЕ входа лонга
    entry_out = good.model_copy(update={"entry": Decimal("140")})  # вход вне своей зоны
    pp_good = build_pp_setup(pp(PPSide.SHORT, 100.0, 101.0), pp(PPSide.LONG, 90.0, 91.0))
    pp_target_back = pp_good.model_copy(update={"target": 130.0})  # цель позади входа шорта
    pp_rr_no_target = pp_good.model_copy(update={"target": None, "rr": 2.0})
    pp_stop_ahead = pp_good.model_copy(update={"stop": 95.0})  # стоп НИЖЕ входа шорта
    return [
        ("уровень: ПОК вне зоны", check_level(bad_level)),
        ("уровень: ПОК вне БАЗЫ (стр. 30)", check_level(poc_outside)),
        ("уровень: зона шире базы (стр. 30)", check_level(zone_wider)),
        ("сделка: стоп впереди входа", check_setup(stop_ahead)),
        ("сделка: вход вне своей зоны", check_setup(entry_out)),
        ("ПП: цель позади входа", check_pp(pp_target_back)),
        ("ПП: РР без цели", check_pp(pp_rr_no_target)),
        ("ПП: стоп впереди входа", check_pp(pp_stop_ahead)),
    ]


def main() -> int:
    violations: list[str] = []
    checked = 0

    for name, s in real_setups():
        checked += 1
        violations += [f"{name}: {v}" for v in check_setup(s)]
        violations += [f"{name} (уровень): {v}" for v in check_level(s.level)]
    for name, ps in real_pp_setups():
        checked += 1
        violations += [f"{name}: {v}" for v in check_pp(ps)]

    # Контроль: подсаженное нарушение ОБЯЗАНО быть поймано. Молчащий здесь гейт
    # неотличим от гейта, который ничего не проверяет.
    blind: list[str] = []
    control = planted()
    for name, found in control:
        if not found:
            blind.append(name)

    print(f"гейт инвариантов геометрии: объектов расчёта {checked}, "
          f"нарушений {len(violations)}; подсажено {len(control)}, "
          f"поймано {len(control) - len(blind)}")
    for v in violations:
        print(f"  НАРУШЕНИЕ {v}")
    for b in blind:
        print(f"  ПРОВАЛ КОНТРОЛЯ: подсаженное нарушение не поймано — {b}")
    if not checked or not control:
        print("ПРОВАЛ: материала нет — проверка не состоялась")
        return 1
    return 1 if violations or blind else 0


if __name__ == "__main__":
    sys.exit(main())
