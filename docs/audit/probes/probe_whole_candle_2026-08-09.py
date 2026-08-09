"""ЗОНД цены вопроса М-15: сколько UNRESOLVED удовлетворяют условию схемы стр. 6.

ВОПРОС. Схема стр. 6 несёт надпись (только на рисунке): "Во всех случаях - цена не
уходит за уровень целыми свечами". Текст той же страницы даёт другое: возврат «той же
или следующей 1-2 свечами». Код следует тексту (`RETURN_BARS = 2`), поздний возврат
даёт `UNRESOLVED` — «источник вердикта не даёт». Ослабление до «прокол = не было целых
свечей» пробовалось 2026-08-07 и ОТКАЧЕНО (разбор — докстрока `breach.first_breach`):
надпись схемы — необходимое условие, а не достаточное.

Этот зонд НЕ предлагает правку. Он мерит ЦЕНУ открытого вопроса владельцу: среди
событий `UNRESOLVED` на реальных уровнях — у скольких за уровнем НЕ БЫЛО ни одной
целой свечи (`bodies == 0`), то есть условие схемы выполнено и вопрос «прокол ли это»
стоит; и у скольких целые свечи БЫЛИ (`bodies > 0`) — там даже схема прокола не видит.

КОНТРОЛЬ. Прибор способен ответить иначе: печатаются обе группы; если бы все
UNRESOLVED имели bodies == 0, разбиение было бы вырожденным и это было бы видно.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_whole_candle_2026-08-09.py

⚠ Отпечаток данных обязателен (правило CLAUDE.md от 2026-08-09): зонд печатает число
прогонов и уровней, по которым посчитан.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.bars import TIMEFRAME_MS  # noqa: E402
from hunter.breach import BreachKind, first_breach  # noqa: E402
from hunter.engine import decide  # noqa: E402
from hunter.models import Bar, NotReady  # noqa: E402
from hunter.store import read_binned_trades  # noqa: E402


def main() -> int:
    frames = ROOT / "data" / "frames"
    kinds: dict[str, int] = defaultdict(int)
    unresolved_no_body = 0
    unresolved_with_body = 0
    by_tf_no_body: dict[str, int] = defaultdict(int)
    runs = 0
    levels_total = 0

    for run_dir in sorted(p for p in frames.iterdir() if p.is_dir()):
        for sym_dir in sorted(run_dir.iterdir()):
            meta_p = sym_dir / "meta.json"
            if not meta_p.exists():
                continue
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            symbol = meta["symbol"]
            trades = read_binned_trades(run_dir.name, sym_dir.name,
                                        Decimal(meta["tick_size"]),
                                        int(meta["bucket_ms"]), symbol=symbol)
            series: dict[str, list[Bar]] = {}
            for tf in TIMEFRAME_MS:
                f = sym_dir / f"{tf}.parquet"
                if not f.exists():
                    continue
                df = pl.read_parquet(f)
                series[tf] = [Bar(open_ms=int(r["open_ms"]), open=float(r["open"]),
                                  high=float(r["high"]), low=float(r["low"]),
                                  close=float(r["close"]), volume=float(r["volume"]))
                              for r in df.iter_rows(named=True)]
            if not series:
                continue
            runs += 1
            d = decide(symbol, series,
                       None if isinstance(trades, NotReady) else trades, tuple(series))
            for m in d.mapped:
                bars = series.get(m.level.timeframe)
                if not bars:
                    continue
                levels_total += 1
                ev = first_breach(bars, float(m.level.price),
                                  m.level.breach_direction, m.level.timeframe,
                                  from_index=m.level.created_at_index + 1)
                if ev is None:
                    kinds["none"] += 1
                    continue
                kinds[ev.kind.value] += 1
                if ev.kind is BreachKind.UNRESOLVED:
                    if ev.bodies == 0:
                        unresolved_no_body += 1
                        by_tf_no_body[m.level.timeframe] += 1
                    else:
                        unresolved_with_body += 1

    print("=" * 78)
    print("Цена вопроса М-15: UNRESOLVED против условия схемы стр. 6")
    print(f"отпечаток данных: прогонов {runs}, уровней {levels_total}")
    print(f"исходы первого события: {dict(sorted(kinds.items()))}")
    unresolved = unresolved_no_body + unresolved_with_body
    print(f"UNRESOLVED всего: {unresolved}")
    print(f"  из них БЕЗ целых свечей за уровнем (условие схемы выполнено, вопрос "
          f"владельцу стоит): {unresolved_no_body}")
    print(f"  с целыми свечами (даже схема прокола не видит): {unresolved_with_body}")
    print(f"  без целых свечей — по ТФ: {dict(sorted(by_tf_no_body.items()))}")
    if unresolved and (unresolved_no_body == 0 or unresolved_with_body == 0):
        print("⚠ разбиение вырождено — прибор мог быть заперт в одном ответе, разобрать")
    return 0


if __name__ == "__main__":
    sys.exit(main())
