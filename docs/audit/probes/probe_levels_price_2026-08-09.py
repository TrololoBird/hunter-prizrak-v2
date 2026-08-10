"""ЗОНД: ЦЕНА двух правок обзора уровней — У3 (диапазон сетки) и У6 (реакция на касание).

⚠ Это НЕ проверка гипотез. Обе правки предрешены курсом (фаза 3, пункт 3): по уровням курс
отвечает на все 11 вопросов, часть Б девятипунктовой шкалы пуста, и гипотез в обзоре ноль.
Порог здесь отвечает не на вопрос «вносить ли», а на вопрос «заметно ли изменение вообще»;
ответ ниже порога означает подозрение к РЕАЛИЗАЦИИ ЗАМЕРА, а не отмену требования курса.

Пороги записаны и хешированы ДО прогона: docs/audit/tolerance-levels.md,
sha256 b9fcbcde7cbc704c670233bf3c11c3f31c23443a143ad22cd68a048edc6f1c65

ЧТО СЧИТАЕТСЯ

  У3-A  доля объёма структуры, лежащего ЗА её границами (порог заметности 0.5%).
        Считается как clamped_volume профиля, построенного по границам: сам модуль
        документирует это поле словами «переданный диапазон у́же реального».

  У3-B  доля уровней, у которых меняется ПОК, если сетку натянуть на крайние цены баров
        окна, как рисует курс на стр. 26 и 30 (порог заметности 1%).

  У6-A  распределение отскока после ПЕРВОГО захода в зону: процент от экстремума бара
        касания и кратность ATR(14). Описательный, порога нет.

  У6-B  доля уровней, у которых лимитки СОХРАНИЛИСЬ БЫ при пороге реакции из набора
        0, 5, 10, 21.27, 100 процентов и 1.0×ATR (порог заметности: при 21.27% — от 5%).

КОНТРОЛИ (прибор обязан отвечать по-разному)

  У3   сетка по границам против самой себя обязана дать 0% изменений;
       сетка, расширенная на 50% в обе стороны, — больше нуля.
  У6   порог 0% обязан дать 0% сохранённых лимиток (сегодняшнее поведение),
       порог 100% — почти всё.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_levels_price_2026-08-09.py
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
from hunter.swings import detect as detect_swings  # noqa: E402
from hunter.levels import structure_window_ms  # noqa: E402
from hunter.models import Bar, NotReady  # noqa: E402
from hunter.volume_profile import TV_ROWS, build_tv  # noqa: E402

REACTION_BARS = 10
PERCENT_THRESHOLDS = (0.0, 5.0, 10.0, 21.27, 100.0)
ATR_PERIOD = 14


def load_frames() -> list[tuple[str, str, Decimal, dict[str, list[Bar]]]]:
    """(прогон, символ, тик, бары по ТФ) для всех сохранённых кадров."""
    out: list[tuple[str, str, Decimal, dict[str, list[Bar]]]] = []
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
                out.append((run.name, meta["symbol"], Decimal(meta["tick_size"]), per_tf))
    return out


def atr(bars: list[Bar], i: int, period: int = ATR_PERIOD) -> float | None:
    """ATR по классике Уайлдера, простым средним истинного диапазона за period баров."""
    if i < period:
        return None
    trs = []
    for j in range(i - period + 1, i + 1):
        prev_close = bars[j - 1].close
        trs.append(max(bars[j].high - bars[j].low,
                       abs(bars[j].high - prev_close),
                       abs(bars[j].low - prev_close)))
    a = statistics.fmean(trs)
    return a if a > 0 else None


def quantiles(xs: list[float]) -> str:
    if not xs:
        return "выборка пуста"
    s = sorted(xs)

    def q(p: float) -> float:
        return s[min(len(s) - 1, int(p * len(s)))]

    return (f"q10 {q(0.10):.2f}  q25 {q(0.25):.2f}  q50 {q(0.50):.2f}  "
            f"q75 {q(0.75):.2f}  q90 {q(0.90):.2f}")


def main() -> None:
    frames = load_frames()
    cache_dir = archive.CACHE_DIR
    cache_files = len(list(cache_dir.glob("*.parquet"))) if cache_dir.exists() else 0

    structures = 0
    profiled = 0
    clamp_shares: list[float] = []
    poc_changed = 0
    zone_changed = 0
    poc_same_control = 0
    poc_changed_wide = 0

    bounce_pct: list[float] = []
    bounce_atr: list[float] = []

    for _run, symbol, tick, per_tf in frames:
        market_id = symbol.split(":")[0].replace("/", "")
        src = archive.WindowSource(symbol, market_id, tick, cache_dir=cache_dir)
        for tf, bars in per_tf.items():
            if len(bars) < ATR_PERIOD + REACTION_BARS + 2:
                continue
            accs = detect(bars, detect_swings(bars), tf).closed
            for acc in accs:
                if acc.exit is None:
                    continue
                structures += 1
                w = structure_window_ms(acc, bars, TIMEFRAME_MS[tf])
                hist = src.window(w[0], w[1])
                if isinstance(hist, NotReady):
                    continue

                lo_e = Decimal(str(acc.lower.edge))
                hi_e = Decimal(str(acc.upper.edge))
                seg = bars[acc.first_index:acc.last_index + 1]
                if not seg:
                    continue
                lo_bar = Decimal(str(min(b.low for b in seg)))
                hi_bar = Decimal(str(max(b.high for b in seg)))

                p_edge = build_tv(hist, bottom=lo_e, top=hi_e, rows=TV_ROWS)
                p_bar = build_tv(hist, bottom=lo_bar, top=hi_bar, rows=TV_ROWS)
                p_edge2 = build_tv(hist, bottom=lo_e, top=hi_e, rows=TV_ROWS)
                span = hi_e - lo_e
                p_wide = build_tv(hist, bottom=lo_e - span / 2, top=hi_e + span / 2,
                                  rows=TV_ROWS)
                if isinstance(p_edge, NotReady) or isinstance(p_bar, NotReady):
                    continue
                profiled += 1

                clamp_shares.append(100.0 * p_edge.clamped_volume / p_edge.total_volume)
                if p_edge.poc_price != p_bar.poc_price:
                    poc_changed += 1
                if (p_edge.val_price, p_edge.vah_price) != (p_bar.val_price, p_bar.vah_price):
                    zone_changed += 1
                if not isinstance(p_edge2, NotReady) and p_edge2.poc_price == p_edge.poc_price:
                    poc_same_control += 1
                if not isinstance(p_wide, NotReady) and p_wide.poc_price != p_edge.poc_price:
                    poc_changed_wide += 1

                # У6: реакция после первого захода в зону уровня
                lo_z, hi_z = float(p_edge.val_price), float(p_edge.vah_price)
                start = acc.exit.confirmed_at_index + 1
                touch = None
                for i in range(start, len(bars) - REACTION_BARS):
                    if bars[i].low <= hi_z and bars[i].high >= lo_z:
                        touch = i
                        break
                if touch is None:
                    continue
                a = atr(bars, touch)
                nxt = bars[touch:touch + REACTION_BARS + 1]
                if acc.is_long:
                    base = bars[touch].low
                    move = max(b.high for b in nxt) - base
                else:
                    base = bars[touch].high
                    move = base - min(b.low for b in nxt)
                if base <= 0:
                    continue
                bounce_pct.append(100.0 * move / base)
                if a is not None:
                    bounce_atr.append(move / a)

    print("ОТПЕЧАТОК ДАННЫХ")
    print(f"  кадров (прогон × символ): {len(frames)}")
    print(f"  файлов суток в aggcache:  {cache_files}")
    print(f"  структур закрытых:        {structures}")
    print(f"  из них с профилем:        {profiled}")
    print()

    print("У3-A  объём ЗА границами структуры (порог заметности 0.5%)")
    if clamp_shares:
        print(f"  доля прижатого объёма: медиана {statistics.median(clamp_shares):.2f}%  "
              f"среднее {statistics.fmean(clamp_shares):.2f}%  макс {max(clamp_shares):.2f}%")
        share_nonzero = 100.0 * sum(1 for x in clamp_shares if x > 0) / len(clamp_shares)
        print(f"  структур с ненулевым прижатием: {share_nonzero:.1f}%")
    else:
        print("  выборка пуста")
    print()

    print("У3-B  сетка по крайним ценам баров против сетки по границам (порог 1%)")
    if profiled:
        print(f"  ПОК изменился:  {poc_changed} из {profiled} = "
              f"{100.0 * poc_changed / profiled:.1f}%")
        print(f"  зона изменилась: {zone_changed} из {profiled} = "
              f"{100.0 * zone_changed / profiled:.1f}%")
    print()

    print("КОНТРОЛЬ У3 — прибор обязан отвечать по-разному")
    if profiled:
        print(f"  сетка по границам против САМОЙ СЕБЯ: совпало {poc_same_control} из "
              f"{profiled} — ожидалось всё")
        print(f"  сетка, расширенная на 50%: ПОК изменился {poc_changed_wide} из "
              f"{profiled} = {100.0 * poc_changed_wide / profiled:.1f}% — ожидалось >0")
    print()

    print("У6-A  отскок после первого захода в зону (описательный, порога нет)")
    print(f"  уровней с касанием: {len(bounce_pct)}")
    print(f"  в процентах: {quantiles(bounce_pct)}")
    print(f"  в ATR(14):   {quantiles(bounce_atr)}")
    print()

    print("У6-B  доля СОХРАНЁННЫХ лимиток при пороге реакции")
    for t in PERCENT_THRESHOLDS:
        kept = sum(1 for x in bounce_pct if x < t)
        share = 100.0 * kept / len(bounce_pct) if bounce_pct else 0.0
        mark = ""
        if t == 0.0:
            mark = "  ← КОНТРОЛЬ, обязан быть 0.0%"
        if t == 100.0:
            mark = "  ← КОНТРОЛЬ, обязан быть близко к 100%"
        if t == 21.27:
            mark = "  ← порог заметности: от 5%"
        print(f"  порог {t:6.2f}%: сохранено {kept:5d} из {len(bounce_pct):5d} = "
              f"{share:5.1f}%{mark}")
    kept_atr = sum(1 for x in bounce_atr if x < 1.0)
    share_atr = 100.0 * kept_atr / len(bounce_atr) if bounce_atr else 0.0
    print(f"  порог 1.0×ATR: сохранено {kept_atr} из {len(bounce_atr)} = {share_atr:.1f}%")


if __name__ == "__main__":
    main()
