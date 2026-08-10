"""ЗОНД: сверка баров со ВТОРОЙ биржей — ловит ли она то, чего не ловим мы.

Повод. Ревизия транспорта назвала главную слабость конструкции: **источник цен ровно
один**. Bybit-эндпоинты публичны и ключей не требуют, а библиотека та же (ccxt), то есть
новой боевой зависимости здесь нет — есть новый ИСТОЧНИК СВЕРКИ.

Вопрос зонда: насколько расходятся одни и те же бары у двух бирж и способна ли сверка
поймать то, что наши проверки пропускают. Расчёта это НЕ касается: сверка — приёмка
данных, а не элемент методологии (§0), и решение о её встраивании — владельца.

Меры по каждому символу и ТФ:
  * доля баров, где закрытия расходятся сильнее допуска 0.10·ATR14;
  * медиана расхождения в процентах цены;
  * бары, которых у второй биржи НЕТ вовсе (разные листинги, разная история);
  * ⚠ ЗАМОРОЖЕННЫЙ РЯД: одна цена и нулевой объём. Ровно так выглядит делистнутый
    символ у Binance (замер 2026-08-05: OMG, WAVES, MKR, DEFI — 50 баров, одна цена,
    объём 0, при этом метки времени СВЕЖИЕ). Наши проверки такой ряд пропускают: он
    не битый, не с разрывами и не устаревший.

КОНТРОЛЬ 1 (способен ли прибор ответить иначе). Тот же расчёт для ЧУЖОЙ пары: бары BTC
сверяются с барами ETH. Расхождение обязано быть огромным; если мера и здесь покажет
согласие — она не меряет ничего.

КОНТРОЛЬ 2 (не выдаём ли совпадение за заслугу). Сдвиг ряда второй биржи на один бар:
согласие обязано УПАСТЬ. Иначе мера нечувствительна к выравниванию по времени, и
«сошлось» означало бы лишь «цены похожи вообще».

Команда воспроизведения (нужна сеть, ключей не требует):
    uv run python docs/audit/probes/probe_second_venue_2026-08-11.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from hunter.config import load_universe  # noqa: E402

TFS = ("1h", "4h")
LIMIT = 200
TOLERANCE_ATR = 0.10
"""Тот же допуск, что во всех замерах проекта: docs/audit/tolerance-R-01.md."""

SYMBOLS_CHECKED = 6
"""Сколько символов вселенной сверять. Каждый — два запроса к двум биржам."""


def atr14(bars: list[list[float]]) -> float | None:
    if len(bars) < 15:
        return None
    trs = [max(bars[i][2] - bars[i][3],
               abs(bars[i][2] - bars[i - 1][4]),
               abs(bars[i][3] - bars[i - 1][4])) for i in range(1, len(bars))]
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def med(xs: list[float]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def compare(ours: list[list[float]], theirs: list[list[float]],
            tol: float) -> tuple[int, int, float]:
    """(сверено, расхождений выше допуска, медиана расхождения в % цены)."""
    by_ms = {int(b[0]): b for b in theirs}
    checked = over = 0
    diffs: list[float] = []
    for b in ours:
        other = by_ms.get(int(b[0]))
        if other is None:
            continue
        checked += 1
        d = abs(float(b[4]) - float(other[4]))
        diffs.append(d / max(float(b[4]), 1e-12) * 100)
        if d > tol:
            over += 1
    return checked, over, med(diffs)


def frozen(bars: list[list[float]]) -> bool:
    """Ряд ЗАМОРОЖЕН: одна цена закрытия и нулевой объём. Форма делистинга."""
    if len(bars) < 10:
        return False
    closes = {round(float(b[4]), 10) for b in bars}
    volume = sum(float(b[5]) for b in bars)
    return len(closes) == 1 and volume == 0.0


def main() -> int:
    uni = load_universe()
    ours_ex = ccxt.binanceusdm()
    theirs_ex = ccxt.bybit()
    try:
        theirs_ex.load_markets()
    except ccxt.BaseError as e:
        print(f"ОТКАЗ: вторая биржа недоступна — {type(e).__name__} {e}")
        return 2

    print(f"сверка Binance USDⓈ-M против Bybit · символов {SYMBOLS_CHECKED}, "
          f"ТФ {', '.join(TFS)}, баров на ряд {LIMIT}, допуск {TOLERANCE_ATR}·ATR14")
    print(f"{'символ':16} {'ТФ':4} {'сверено':>8} {'выше допуска':>13} "
          f"{'медиана %':>10} {'нет у них':>10} {'заморожен':>10}")

    total_checked = total_over = 0
    missing_symbols: list[str] = []
    frozen_found: list[str] = []
    ctl_cross: list[float] = []
    ctl_shift: list[int] = []

    for sym in uni.symbols[:SYMBOLS_CHECKED]:
        if sym not in theirs_ex.markets:
            missing_symbols.append(sym)
            print(f"{sym.split('/')[0]:16} {'—':4} {'—':>8} {'—':>13} {'—':>10} "
                  f"{'ВЕСЬ':>10} {'—':>10}")
            continue
        for tf in TFS:
            try:
                ours = ours_ex.fetch_ohlcv(sym, tf, limit=LIMIT)
                theirs = theirs_ex.fetch_ohlcv(sym, tf, limit=LIMIT)
            except ccxt.BaseError as e:
                print(f"{sym.split('/')[0]:16} {tf:4} отказ биржи: {type(e).__name__}")
                continue
            atr = atr14(ours)
            if atr is None:
                continue
            tol = TOLERANCE_ATR * atr
            checked, over, m = compare(ours, theirs, tol)
            absent = len(ours) - checked
            fz = frozen(ours) or frozen(theirs)
            if fz:
                frozen_found.append(f"{sym} {tf}")
            total_checked += checked
            total_over += over
            print(f"{sym.split('/')[0]:16} {tf:4} {checked:>8} {over:>13} "
                  f"{m:>10.4f} {absent:>10} {'ДА' if fz else 'нет':>10}")

            # КОНТРОЛЬ 2: тот же ряд второй биржи, сдвинутый на бар.
            shifted = [[b[0], *b[1:]] for b in theirs]
            if len(shifted) > 1:
                step = int(shifted[1][0]) - int(shifted[0][0])
                shifted = [[int(b[0]) + step, *b[1:]] for b in shifted]
                _, over_s, _ = compare(ours, shifted, tol)
                ctl_shift.append(over_s)

        # КОНТРОЛЬ 1: сверка с ЧУЖИМ символом — расхождение обязано быть огромным.
        if sym == uni.symbols[0] and len(uni.symbols) > 1:
            other_sym = uni.symbols[1]
            if other_sym in theirs_ex.markets:
                try:
                    ours_b = ours_ex.fetch_ohlcv(sym, "1h", limit=LIMIT)
                    alien = theirs_ex.fetch_ohlcv(other_sym, "1h", limit=LIMIT)
                    _, _, m_alien = compare(ours_b, alien, 1e-9)
                    ctl_cross.append(m_alien)
                except ccxt.BaseError:
                    pass

    share = total_over / total_checked * 100 if total_checked else float("nan")
    print(f"\nИТОГО сверено баров {total_checked}, выше допуска {total_over} "
          f"({share:.2f}%)")
    if missing_symbols:
        print(f"⚠ символов НЕТ на второй бирже: {len(missing_symbols)} — "
              f"{', '.join(s.split('/')[0] for s in missing_symbols)}; "
              f"их сверить нечем, и это ограничение метода, а не свойство рынка")
    if frozen_found:
        print(f"⚠ ЗАМОРОЖЕННЫЕ ряды (одна цена, нулевой объём): {', '.join(frozen_found)}")
    else:
        print("замороженных рядов не найдено (проверка выполнена, знаменатель выше)")

    print("\nКОНТРОЛЬ 1 (прибор способен ответить иначе): сверка BTC с ЧУЖИМ символом "
          f"даёт медиану расхождения {med(ctl_cross):.2f}% против долей процента у "
          f"честной пары")
    if ctl_cross and med(ctl_cross) < 1.0:
        print("  ⚠ ПРОВАЛ КОНТРОЛЯ: чужой символ согласуется не хуже своего")
    print("КОНТРОЛЬ 2 (мера чувствительна к выравниванию): ряд второй биржи, сдвинутый "
          f"на бар, даёт {sum(ctl_shift)} расхождений выше допуска против {total_over} "
          f"у выровненного")
    if ctl_shift and sum(ctl_shift) <= total_over:
        print("  ⚠ ПРОВАЛ КОНТРОЛЯ: сдвиг не ухудшает согласие — мера слепа ко времени")
    return 0


if __name__ == "__main__":
    sys.exit(main())
