"""Цена внесения силы по объёму в ОТБОР уровней (приказ владельца 2026-08-19).

Мера снимается С АРТЕФАКТА — с ТЕКСТА сообщения бота, который владелец и читает, — а не
с объекта в памяти. Порядок: строим ZoneSpec из `decide` на сохранённых кадрах, затем
собираем сообщение ДВАЖДЫ — на настоящих долях и на ОБНУЛЁННЫХ. Обнулённая доля
вырождает новый ключ отбора в прежний (ТФ → ширина зоны), значит второй текст и есть
«как было».

ДВА КОНТРОЛЯ, оба обязательны:
  1. вырождение доказывается НЕ рассуждением: прежний ключ выписан сюда дословно
     (`pick_old`), и на обнулённых долях отбор нового кода обязан совпасть с ним
     поимённо. Разошлось — «как было» неверно, и число ничего не значит;
  2. прибор обязан быть способен ответить иначе: на настоящих долях расхождение
     обязано быть НЕнулевым хотя бы у части символов, иначе правка ничего не делает.
"""
from __future__ import annotations

import difflib
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from hunter import engine, store, tgbot
from hunter.bars import TIMEFRAME_MS
from hunter.models import NotReady
from hunter.profile_source import TVWindows
from hunter.render import ZoneSpec

ORDER = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}


def pick_old(zones: list[ZoneSpec], price: float, now_ms: int) -> list[ZoneSpec]:
    """ПРЕЖНИЙ отбор, переписанный дословно с редакции до правки 2026-08-19."""
    alive = [z for z in zones if z.state == "active"]
    live = [z for z in alive if tgbot.near_structure(z, now_ms)]
    best: dict[tuple[str, str], ZoneSpec] = {}
    for z in live:
        key = (z.side, tgbot._fmt_price(z.price))
        kept = best.get(key)
        if kept is None or ORDER.get(z.timeframe, 0) > ORDER.get(kept.timeframe, 0):
            best[key] = z
    kept_zones: list[ZoneSpec] = []
    for z in sorted(best.values(),
                    key=lambda z: (-ORDER.get(z.timeframe, 0), -(z.zone_hi - z.zone_lo))):
        if any(k.side == z.side and k.zone_lo <= z.price <= k.zone_hi
               and z.zone_lo <= k.price <= z.zone_hi for k in kept_zones):
            continue
        kept_zones.append(z)
    return sorted(kept_zones, key=lambda z: tgbot._away(z, price))


def ident(z: ZoneSpec) -> tuple[str, str, int, int]:
    return (z.side, z.timeframe, z.from_ms, z.to_ms)


def build(run_id: str, d: str) -> tuple[str, list[ZoneSpec], float, int] | None:
    meta = store.read_meta(run_id, d)
    if isinstance(meta, NotReady):
        return None
    symbol, tick, _ = meta
    tfs = store.saved_timeframes(run_id, d)
    if not tfs:
        return None
    series = {tf: store.read_bars(run_id, d, tf) for tf in tfs}
    manifest = store.read_source_meta(run_id, d)
    if isinstance(manifest, NotReady):
        return None
    profile_series = dict(series)
    profile_series.update(store.read_profile_bars(run_id, d))
    dec = engine.decide(symbol, series, TVWindows(symbol, tick, profile_series), tfs)
    fastest = min(series, key=lambda tf: TIMEFRAME_MS[tf])
    if not series[fastest]:
        return None
    last = series[fastest][-1]
    return symbol, list(tgbot.zones_of(dec, 0)), float(last.close), int(last.open_ms)


def text_of(zones: list[ZoneSpec], symbol: str, price: float, now_ms: int) -> str:
    return tgbot.compose_text(symbol, tuple(zones), [], "кадры", price=price, now_ms=now_ms)


def main() -> int:
    run_id = "last"
    dirs = store.saved_symbols(run_id)
    print(f"прогон {run_id}, символов {len(dirs)}\n")
    tot = {"зон всего": 0, "с долей > 0": 0, "в отборе": 0, "отбор выбрал другое": 0,
           "строк сообщения изменилось": 0, "строк «сильнейший в хвосте»": 0}
    ctrl_bad = 0
    for d in dirs:
        got = build(run_id, d)
        if got is None:
            print(f"  ОТКАЗ: {d}")
            continue
        symbol, zones, price, now_ms = got
        zeroed = [replace(z, vrvp_density=0.0) for z in zones]

        _, new_pick, _ = tgbot.live_unique(tuple(zones), price, now_ms)
        _, ctrl_pick, _ = tgbot.live_unique(tuple(zeroed), price, now_ms)
        old_pick = pick_old(zeroed, price, now_ms)
        ok = [ident(z) for z in ctrl_pick] == [ident(z) for z in old_pick]
        ctrl_bad += not ok

        diff_pick = sum(1 for a, b in zip(old_pick, new_pick, strict=False)
                        if ident(a) != ident(b)) + abs(len(old_pick) - len(new_pick))

        t_new = text_of(zones, symbol, price, now_ms).splitlines()
        t_old = text_of(zeroed, symbol, price, now_ms).splitlines()
        changed = sum(1 for ln in difflib.unified_diff(t_old, t_new, n=0)
                      if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")))
        tail = sum(1 for ln in t_new if "сильнейший по объёму" in ln)
        withvol = sum(1 for z in zones if z.vrvp_density > 0)

        print(f"  {symbol:16} зон {len(zones):5}  с долей {withvol:5}  "
              f"отбор {len(old_pick):3}→{len(new_pick):3}  выбрано другое {diff_pick:3}  "
              f"строк текста ± {changed:3}  хвост назван {tail}  "
              f"контроль: {'совпало' if ok else 'РАЗОШЛОСЬ'}")
        tot["зон всего"] += len(zones)
        tot["с долей > 0"] += withvol
        tot["в отборе"] += len(new_pick)
        tot["отбор выбрал другое"] += diff_pick
        tot["строк сообщения изменилось"] += changed
        tot["строк «сильнейший в хвосте»"] += tail
    print()
    for k, v in tot.items():
        print(f"  {k}: {v}")
    print(f"\nКОНТРОЛЬ 1 (обнулённая доля → прежний отбор поимённо): разошлось у "
          f"{ctrl_bad} символов, обязано быть 0")
    print(f"КОНТРОЛЬ 2 (прибор способен ответить иначе): расхождений "
          f"{tot['отбор выбрал другое']}, ноль означал бы, что правка не работает")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
