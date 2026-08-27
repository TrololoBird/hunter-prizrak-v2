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
    build_all,
)
from hunter.models import Bar, NotReady
from hunter.pereprior import Pereprior, PPKind, PPSide
from hunter.profile_source import CandleWindows
from hunter.swings import detect as swings_detect
from hunter.trading_range import detect as range_detect
from hunter.volume_profile import row_height_of

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
    # ⚠⚠ РАБОЧАЯ ЗОНА. Заведено 2026-08-27: с 2026-08-25 вход рисуется по HVN-ядру
    # вокруг ПОК (`zone_work_*`, решение владельца), а сторожил его ТОЛЬКО `CHECK` схемы
    # леджера — то есть на записи и после того, как график уже нарисован. Ни один из 23
    # гейтов слова `zone_work` не знал вовсе: предъявляемая величина без прибора, ровно
    # тот класс, из-за которого зона вылезала за границы у половины уровней.
    lo, hi = lv.zone_work_lo, lv.zone_work_hi
    if (lo is None) != (hi is None):
        bad.append(f"рабочая зона задана наполовину: lo {lo}, hi {hi}")
    elif lo is not None and hi is not None:
        if lo >= hi:
            bad.append(f"рабочая зона вывернута либо пуста: [{lo}, {hi}]")
        if lo < lv.zone_lo or hi > lv.zone_hi:
            bad.append(f"рабочая зона [{lo}, {hi}] выходит за область стоимости "
                       f"[{lv.zone_lo}, {lv.zone_hi}] — ядро клипится по VAL…VAH")
        if not (lo <= lv.price <= hi):
            bad.append(f"ПОК {lv.price} вне СВОЕЙ рабочей зоны [{lo}, {hi}] — "
                       f"ядро строится ВОКРУГ ПОК")
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
        # Второе скопление объёма: у синтетического уровня профиля НЕТ вовсе,
        # поэтому ноль — это «вне зоны непустых строк не найдено», а не
        # «проверено и не нашлось». Пробникам величина безразлична.
        outside_peak_share=0.0,
        # Высота строки профиля — БОЕВОЙ формулой, ссылкой на единственное её место
        # (`volume_profile.row_height_of`). ⚠ До 2026-08-27 здесь стояла своя копия по
        # удалённому из боя режиму `TV_ROWS`; разбор — в докстроке самой константы.
        row_height=row_height_of(Decimal(b_lo if b_lo is not None else lo),
                                 Decimal(b_hi if b_hi is not None else hi),
                                 Decimal("0.1")),
        boundary_lo=Decimal(b_lo if b_lo is not None else lo),
        boundary_hi=Decimal(b_hi if b_hi is not None else hi),
        # Прокола у синтетической базы нет — расширенный край СОВПАДАЕТ с границей
        # (`BoundaryZone.extended_edge` возвращает `edge`, когда `puncture is None`).
        # Не ноль и не «шире на глазок»: инвариант «прокол не ближе границы» обязан
        # держаться и на пробнике, иначе он проверял бы поблажку.
        stop_edge_lo=Decimal(b_lo if b_lo is not None else lo),
        stop_edge_hi=Decimal(b_hi if b_hi is not None else hi),
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
    # ⚠ РАБОЧАЯ ЗОНА подсаживается `model_construct` В ОБХОД ВАЛИДАТОРА: с 2026-08-27
    # `Level._work_zone_inside_value_area` такие объекты не даёт построить вовсе, и это
    # правильно — но проверять надо САМУ ПРОВЕРКУ, а не валидатор. Обход намеренный.
    ok_lv = level(LevelSide.LONG, "100", "95", "105", b_lo="90", b_hi="110")
    work_out = ok_lv.model_construct(**{**ok_lv.__dict__,
                                        "zone_work_lo": Decimal("94"),
                                        "zone_work_hi": Decimal("102")})
    work_inverted = ok_lv.model_construct(**{**ok_lv.__dict__,
                                             "zone_work_lo": Decimal("102"),
                                             "zone_work_hi": Decimal("98")})
    work_half = ok_lv.model_construct(**{**ok_lv.__dict__,
                                         "zone_work_lo": Decimal("98"),
                                         "zone_work_hi": None})
    work_no_poc = ok_lv.model_construct(**{**ok_lv.__dict__,
                                           "zone_work_lo": Decimal("101"),
                                           "zone_work_hi": Decimal("104")})
    return [
        ("уровень: ПОК вне зоны", check_level(bad_level)),
        ("уровень: ПОК вне БАЗЫ (стр. 30)", check_level(poc_outside)),
        ("уровень: зона шире базы (стр. 30)", check_level(zone_wider)),
        ("сделка: стоп впереди входа", check_setup(stop_ahead)),
        ("сделка: вход вне своей зоны", check_setup(entry_out)),
        ("ПП: цель позади входа", check_pp(pp_target_back)),
        ("ПП: РР без цели", check_pp(pp_rr_no_target)),
        ("ПП: стоп впереди входа", check_pp(pp_stop_ahead)),
        ("уровень: рабочая зона вне VAL…VAH", check_level(work_out)),
        ("уровень: рабочая зона вывернута", check_level(work_inverted)),
        ("уровень: рабочая зона задана наполовину", check_level(work_half)),
        ("уровень: ПОК вне своей рабочей зоны", check_level(work_no_poc)),
    ]


def _bar(i: int, o: float, h: float, low: float, c: float, v: float = 100.0) -> Bar:
    return Bar(open_ms=T0 + i * HOUR, open=o, high=h, low=low, close=c, volume=v)


def built_levels() -> tuple[list[Level], list[str]]:
    """Уровни, построенные НАСТОЯЩИМ конвейером: свинги → структуры → профиль → ПОК.

    ⚠⚠ ЗАВЕДЕНО 2026-08-23, И ЭТО ПОЧИНКА ОХВАТА, А НЕ НОВЫЙ ГЕЙТ. `check_level`
    применялся ТОЛЬКО к уровням, собранным вручную функцией `level()` в этом же файле,
    то есть проверял свои же литералы. `levels.build_level` гейт не звал НИ РАЗУ —
    считалось, что его не позвать без гистограммы. Инвариант «зона внутри базы», ради
    которого гейт и заведён после капслока владельца 2026-08-18 («ЕСЛИ зона выходит за
    структуру то СТРУКТУРА ОПРЕДЛЕННА НЕ ВЕРНО»), на боевом расчёте не измерялся вовсе.

    Гистограмма строится тем же классом, что в бою (`profile_source.CandleWindows`), из
    синтетического ряда: живых данных в CI нет и быть не может (Binance отдаёт раннерам
    `HTTP 451`), а ряд из литералов проходит РОВНО ТОТ ЖЕ путь — `swings.detect`,
    `trading_range.detect`, `levels.build_all`, `volume_profile.build_tv`.

    ⚠ `CandleWindows`, а не `TVWindows`: правило TV выбирает intrabar-ТФ по ДЛИНЕ окна и
    на трёхсуточной структуре требует минуток, которых у синтетического ряда нет. Это
    ограничение проверки, и оно названо: проверяется путь построения уровня, а не выбор
    intrabar-ступени.

    Ряд подобран так, чтобы структура ЗАКРЫЛАСЬ и ПОК был ОДНОЗНАЧЕН: шесть колебаний
    между 100 и 104 с плотным объёмом у 102.4 (иначе `build_tv` законно отказывает
    ничьёй за ПОК), затем выход вверх двумя телами и спокойный хвост.
    """
    shape: list[tuple[float, float, float, float, float]] = []
    for _ in range(6):
        shape.append((102.0, 104.0, 101.5, 103.0, 90.0))
        shape.append((102.4, 102.6, 102.3, 102.5, 900.0))
        shape += [(102.5, 103.4, 101.6, 102.2, 80.0)] * 4
        shape.append((101.5, 102.6, 100.0, 101.0, 90.0))
        shape += [(101.5, 102.4, 100.8, 102.0, 70.0)] * 5
    shape.append((102.0, 109.0, 101.9, 108.5, 100.0))
    shape.append((108.5, 113.0, 108.0, 112.0, 100.0))
    shape += [(112.0, 113.0, 111.0, 112.0, 100.0)] * 30
    bars: list[Bar] = [_bar(i, *v) for i, v in enumerate(shape)]

    swings = swings_detect(bars)
    if isinstance(swings, NotReady):
        return [], [f"свинги не построены: {swings.reason}"]
    scan = range_detect(bars, swings, "1h")
    source = CandleWindows("TEST/USDT:USDT", Decimal("0.01"), bars, "1h")
    built, unbuilt = build_all("TEST/USDT:USDT", {"1h": bars}, source, ("1h",),
                               {"1h": scan})
    return list(built), [u.reason for u in unbuilt]


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

    # ⚠ УРОВНИ БОЕВОГО КОНВЕЙЕРА — здесь и только здесь `check_level` видит объект,
    # которого не писал этот файл. Ноль построенных уровней — ПРОВАЛ, а не тишина:
    # проверка тогда не состоялась (тот же довод, что у «нарушений 0» гейта чистоты,
    # не открывшего ни одного файла).
    live, refused = built_levels()
    for lv in live:
        checked += 1
        violations += [f"уровень боевого конвейера {lv.side} {lv.price}: {v}"
                       for v in check_level(lv)]
    print(f"уровней БОЕВЫМ путём построено {len(live)}, отказов {len(refused)}")
    for r in refused:
        print(f"  отказ построения: {r}")
    if not live:
        print("ПРОВАЛ: боевым путём не построено ни одного уровня — инвариант зоны "
              "на настоящем расчёте не измерен")
        return 1

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
