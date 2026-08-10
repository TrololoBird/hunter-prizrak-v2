"""ЗОНД-2 обзора уровней: НАСКОЛЬКО далеко уезжает ПОК, а не «уехал ли».

⚠ Заведён отдельным файлом, потому что первый зонд
(probes/probe_levels_price_2026-08-09.py) уже дал опубликованное число и по конвенции
`pyproject.toml` больше не правится.

ЗАЧЕМ. Замер У3-B первого зонда дал 100.0% из 3326 — «ПОК изменился у всех». Это
НАСЫЩЕННАЯ мера, и вердикт по ней выдавать нельзя: две сетки с разными границами имеют
разные центры строк, поэтому точное равенство `Decimal` расходится почти всегда СОДЕРЖАТЕЛЬНО
НИ О ЧЁМ не говоря. Сравнивалось выравнивание сетки, а не смещение уровня.

Здесь считается ВЕЛИЧИНА смещения в трёх шкалах, у каждой свой смысл:
  * в ВЫСОТАХ СТРОКИ сетки по границам — сместился ли ПОК дальше собственного разрешения
    прибора; смещение меньше половины строки прибор различить не может в принципе;
  * в процентах от цены — насколько это заметно оператору;
  * в долях ширины зоны — попадает ли новый ПОК всё ещё в старую зону, то есть меняется ли
    ТВХ по существу.

Плюс предпосылка замера У3-A: во сколько раз диапазон баров окна шире границ структуры.
Медиана прижатого объёма 44.39% выглядит слишком большой, и её надо либо объяснить
геометрией, либо признать дефектом.

Пороги — те же, из хешированного docs/audit/tolerance-levels.md
(sha256 b9fcbcde7cbc704c670233bf3c11c3f31c23443a143ad22cd68a048edc6f1c65):
заметно, если ПОК уезжает дальше строки более чем у 1% структур.

КОНТРОЛЬ. Сетка по границам против самой себя обязана дать смещение РОВНО ноль во всех
трёх шкалах; если хоть где-то не ноль — считается не то.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_levels_shift_2026-08-10.py
"""

from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter import archive  # noqa: E402
from hunter.accumulation import detect  # noqa: E402
from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.levels import structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady  # noqa: E402
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.volume_profile import TV_ROWS, build_tv  # noqa: E402


def load_frames() -> list[tuple[str, Decimal, dict[str, list[Bar]]]]:
    out: list[tuple[str, Decimal, dict[str, list[Bar]]]] = []
    frames = ROOT / "data" / "frames"
    if not frames.exists():
        return out
    for run in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(p for p in run.iterdir() if p.is_dir()):
            mp = sym_dir / "meta.json"
            if not mp.exists():
                continue
            meta = json.loads(mp.read_text(encoding="utf-8"))
            per_tf: dict[str, list[Bar]] = {}
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                per_tf[tf] = [
                    Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                        high=float(r["high"]), low=float(r["low"]),
                        close=float(r["close"]), volume=float(r["volume"]))
                    for r in pl.read_parquet(f).iter_rows(named=True)
                ]
            if per_tf:
                out.append((meta["symbol"], Decimal(meta["tick_size"]), per_tf))
    return out


def qs(xs: list[float], unit: str = "") -> str:
    if not xs:
        return "выборка пуста"
    s = sorted(xs)

    def q(p: float) -> float:
        return s[min(len(s) - 1, int(p * len(s)))]

    return (f"q10 {q(0.10):.3f}{unit}  q25 {q(0.25):.3f}{unit}  q50 {q(0.50):.3f}{unit}  "
            f"q75 {q(0.75):.3f}{unit}  q90 {q(0.90):.3f}{unit}  макс {s[-1]:.3f}{unit}")


def main() -> None:
    frames = load_frames()
    cache_dir = archive.CACHE_DIR
    cache_files = len(list(cache_dir.glob("*.parquet"))) if cache_dir.exists() else 0

    width_ratio: list[float] = []
    shift_rows: list[float] = []
    shift_pct: list[float] = []
    shift_zone: list[float] = []
    outside_old_zone = 0
    beyond_half_row = 0
    control_nonzero = 0
    n = 0

    for symbol, tick, per_tf in frames:
        market_id = symbol.split(":")[0].replace("/", "")
        src = archive.WindowSource(symbol, market_id, tick, cache_dir=cache_dir)
        for tf, bars in per_tf.items():
            if len(bars) < 30:
                continue
            for acc in detect(bars, detect_swings(bars), tf).closed:
                if acc.exit is None:
                    continue
                w = structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
                hist = src.window(w[0], w[1])
                if isinstance(hist, NotReady):
                    continue
                lo_e, hi_e = Decimal(str(acc.lower.edge)), Decimal(str(acc.upper.edge))
                seg = bars[acc.first_index:acc.last_index + 1]
                if not seg or hi_e <= lo_e:
                    continue
                lo_b = Decimal(str(min(b.low for b in seg)))
                hi_b = Decimal(str(max(b.high for b in seg)))

                p_edge = build_tv(hist, bottom=lo_e, top=hi_e, rows=TV_ROWS)
                p_bar = build_tv(hist, bottom=lo_b, top=hi_b, rows=TV_ROWS)
                p_ctl = build_tv(hist, bottom=lo_e, top=hi_e, rows=TV_ROWS)
                if isinstance(p_edge, NotReady) or isinstance(p_bar, NotReady):
                    continue
                n += 1

                width_ratio.append(float((hi_b - lo_b) / (hi_e - lo_e)))
                row_h = float(hist.tick_size) * p_edge.ticks_per_row
                d = abs(float(p_bar.poc_price) - float(p_edge.poc_price))
                shift_rows.append(d / row_h if row_h > 0 else 0.0)
                shift_pct.append(100.0 * d / float(p_edge.poc_price))
                zone_w = float(p_edge.vah_price) - float(p_edge.val_price)
                shift_zone.append(d / zone_w if zone_w > 0 else 0.0)
                if not (float(p_edge.val_price) <= float(p_bar.poc_price)
                        <= float(p_edge.vah_price)):
                    outside_old_zone += 1
                if d > row_h / 2:
                    beyond_half_row += 1
                if not isinstance(p_ctl, NotReady) and p_ctl.poc_price != p_edge.poc_price:
                    control_nonzero += 1

    print("ОТПЕЧАТОК ДАННЫХ")
    print(f"  кадров (прогон × символ): {len(frames)}")
    print(f"  файлов суток в aggcache:  {cache_files}")
    print(f"  структур с двумя профилями: {n}")
    print()

    print("КОНТРОЛЬ: сетка по границам против САМОЙ СЕБЯ")
    print(f"  расхождений: {control_nonzero} из {n} — обязано быть 0")
    print()

    print("ПРЕДПОСЫЛКА У3-A: во сколько раз диапазон баров ШИРЕ границ структуры")
    print(f"  {qs(width_ratio, '×')}")
    print()

    print("СМЕЩЕНИЕ ПОК при переходе на сетку по барам")
    print(f"  в высотах строки: {qs(shift_rows)}")
    print(f"  в процентах цены: {qs(shift_pct, '%')}")
    print(f"  в ширинах зоны:   {qs(shift_zone)}")
    print()
    if n:
        print(f"  дальше ПОЛОВИНЫ строки (различимо прибором): {beyond_half_row} из {n} = "
              f"{100.0 * beyond_half_row / n:.1f}%   порог заметности 1%")
        print(f"  новый ПОК ВНЕ старой зоны (ТВХ меняется по существу): {outside_old_zone} "
              f"из {n} = {100.0 * outside_old_zone / n:.1f}%")


if __name__ == "__main__":
    main()
