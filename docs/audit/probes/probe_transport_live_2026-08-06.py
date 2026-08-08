"""ЗОНД аудита A-0: транспорт ЗАМЕРОМ, а не чтением кода. FOUNDATION.md §6, §8 этап 1.

Проверяются два требования §6:
  1. «Незакрытая свеча отбрасывается на всех путях»;
  2. «Часы корректируются постоянным сдвигом от серверного времени биржи».

⚠ КОНТРОЛЬ ФАЛЬСИФИЦИРУЕМОСТИ обязателен, иначе проверка тавтологична.

Для (1): недостаточно показать, что hunter отдал только закрытые бары. Надо показать,
что СЫРОЙ ответ биржи незакрытый бар СОДЕРЖАЛ, — иначе фильтр мог не сработать ни разу,
а результат выглядел бы так же. Зонд печатает оба списка и разницу между ними.

Для (2): недостаточно показать, что sync прошёл. Надо показать, что `now_ms()` от
локальных часов ОТЛИЧАЕТСЯ — то есть что сдвиг реально применяется. Зонд печатает
`now_ms() - local_ms()` и сверяет со сдвигом.

Команда воспроизведения:
    uv run python docs/audit/probes/probe_transport_live_2026-08-06.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from hunter import clock  # noqa: E402
from hunter.bars import is_closed, tf_ms  # noqa: E402
from hunter.exchange import Exchange  # noqa: E402
from hunter.models import NotReady  # noqa: E402

SYMBOL = "BTC/USDT:USDT"
TFS = ("5m", "15m", "1h")


async def main() -> int:
    ex = Exchange()
    await ex.open()

    print("=" * 72)
    print("A-0.2  ЧАСЫ: сдвиг реально применяется?")
    print("=" * 72)
    s = clock.sync_state()
    local = clock.local_ms()
    now = clock.now_ms()
    print(f"  сдвиг от биржи        offset_ms = {s.offset_ms}")
    print(f"  rtt замера            rtt_ms    = {s.rtt_ms}")
    print(f"  сэмплов               samples   = {s.samples}")
    print(f"  локальные часы        local_ms  = {local}")
    print(f"  биржевые часы         now_ms    = {now}")
    print(f"  РАЗНИЦА now_ms - local_ms       = {now - local}")
    print("  КОНТРОЛЬ: разница обязана быть ≈ offset_ms, а не 0.")
    print(f"  |разница - offset| = {abs((now - local) - s.offset_ms)} мс")

    print()
    print("=" * 72)
    print("A-0.1  НЕЗАКРЫТАЯ СВЕЧА: сырой ответ против отданного")
    print("=" * 72)
    bad = 0
    for tf in TFS:
        raw = await ex._fetch_ohlcv_guarded(SYMBOL, tf, None, 5)
        if isinstance(raw, NotReady):
            print(f"  {tf}: биржа не ответила — {raw.reason}")
            bad += 1
            continue
        t = clock.now_ms()
        raw_opens = [int(r[0]) for r in raw]
        raw_unclosed = [o for o in raw_opens if not is_closed(o, tf, t)]

        got = await ex.fetch_closed_ohlcv(SYMBOL, tf, limit=5)
        if isinstance(got, NotReady):
            print(f"  {tf}: fetch_closed_ohlcv → NotReady: {got.reason}")
            bad += 1
            continue
        out_opens = [b.open_ms for b in got.bars]
        out_unclosed = [o for o in out_opens if not is_closed(o, tf, clock.now_ms())]

        print(f"  {tf}:")
        print(f"     шаг ТФ                        {tf_ms(tf)} мс")
        print(f"     сырых баров от ccxt           {len(raw_opens)}")
        print(f"     из них НЕЗАКРЫТЫХ на момент t {len(raw_unclosed)}  {raw_unclosed}")
        print(f"     отдано hunter                 {len(out_opens)}")
        print(f"     из них незакрытых             {len(out_unclosed)}  {out_unclosed}")
        dropped = [o for o in raw_opens if o not in out_opens]
        print(f"     ОТБРОШЕНО                     {dropped}")
        if not raw_unclosed:
            print("     ⚠ КОНТРОЛЬ НЕ ПРОЙДЕН: сырой ответ незакрытых баров не содержал —")
            print("       проверка тавтологична, фильтр мог не сработать ни разу.")
            bad += 1
        elif out_unclosed:
            print("     ✗ ПРОВАЛ: незакрытый бар дошёл до потребителя.")
            bad += 1
        elif sorted(dropped) != sorted(raw_unclosed):
            print("     ✗ ПРОВАЛ: отброшено не то, что незакрыто.")
            bad += 1
        else:
            print("     ✓ отброшено РОВНО незакрытое, и незакрытое в сыром ответе БЫЛО")

    await ex.close()
    print()
    print("=" * 72)
    print(f"ИТОГ: провалов {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
