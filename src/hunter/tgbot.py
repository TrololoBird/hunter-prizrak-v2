"""Телеграм-бот доставки: тикер → скриншоты карты уровней + текст в формате канала.

Решение владельца 2026-08-10: пользователь отправляет боту название монеты, бот отвечает
графиками в стилистике обзоров автора и текстовой сводкой, похожей на формат канала.

ЧТО БОТ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ (§1, §10.2):
  * уровни берутся ИЗ КАРТЫ ЛЕДЖЕРА (только чтение, `store.open_readonly`) — то, что
    построили прогоны/служба; бот НЕ считает уровни сам: расчёт живёт в одном месте;
  * бары — свежий REST-запрос закрытых свечей (только для КАРТИНКИ, в расчёт не идут);
  * переприоры для картинки и текста считаются на лету ЧИСТЫМИ модулями по этим барам
    (swings → pereprior) — это отображение §2.5 на свежие данные, не эмиссия;
  * бот ничего не пишет ни в леджер, ни в карту; сигналы не порождает.

Токен — переменная окружения TELEGRAM_BOT_TOKEN. Нет токена — названный отказ (§4.3).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import ccxt
from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile, Message

from . import log, pereprior, store, swings
from .bars import TIMEFRAME_MS
from .config import load_universe
from .models import Bar
from .render import SENIOR_TFS, ZoneSpec, chart_png

CHART_TFS = ("4h", "1h", "15m")
"""Какие графики отдаются на тикер: старший план → средний → локальный, как в обзорах."""

BARS_ON_CHART = 180


def resolve_symbol(text: str, symbols: tuple[str, ...]) -> str | None:
    """'btc' | 'BTCUSDT' | 'BTC/USDT:USDT' → символ вселенной. None — не нашли."""
    q = text.strip().upper().replace(" ", "")
    for sym in symbols:
        base = sym.split("/")[0]
        if q in (base, f"{base}USDT", sym.upper(), sym.split(":")[0].replace("/", "")):
            return sym
    return None


def closed_bars(ex: ccxt.binanceusdm, symbol: str, timeframe: str) -> list[Bar]:
    """Закрытые бары для КАРТИНКИ. Последний бар всегда отбрасывается.

    Честная закрытость требует сведённых часов (§6) — здесь их нет, поэтому вместо
    оценки «закрыт ли последний» он отбрасывается всегда: картинка на бар короче, зато
    ни одного незакрытого бара на ней нет по построению.
    """
    raw = ex.fetch_ohlcv(symbol, timeframe, limit=BARS_ON_CHART + 1)
    return [Bar(open_ms=int(r[0]), open=float(r[1]), high=float(r[2]),
                low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            for r in raw[:-1]]


def map_zones(symbol: str) -> list[ZoneSpec]:
    """Активные уровни символа из карты леджера — все ТФ разом, С ПРАВИЛОМ ВХОДА.

    ⚠ Правило входа берётся с 2026-08-11, и это не украшение. `state='active'` НЕ
    означает «цена уровня не касалась»: курс на стр. 25 снимает лимитки на первое же
    касание, оставляя вход по слому младшего ТФ, — уровень при этом остаётся активным.
    Замер на BEAT: из 21 активного уровня 8 цена уже касалась, и бот показывал их
    владельцу наравне со свежими. Расчёт различие знал, карта его не хранила.
    """
    conn = store.open_readonly()
    try:
        rows = conn.execute(
            "SELECT timeframe, side, price, zone_lo, zone_hi, entry_rule FROM levels"
            " WHERE symbol=? AND state='active' ORDER BY price", (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return [ZoneSpec(side=r[1], timeframe=r[0], price=float(r[2]),
                     zone_lo=float(r[3]), zone_hi=float(r[4]),
                     entry_rule=r[5] or "") for r in rows]


def zones_for_chart(
    zones: list[ZoneSpec], timeframe: str, last_price: float,
    near_local: int = 4,
) -> list[ZoneSpec]:
    """Отбор зон для графика ТФ X: как в обзорах автора — не вся карта разом.

    Правило: зоны СВОЕГО ТФ и старших — всегда; из них «локальные» (младше 4ч) —
    только ближайшие к цене (`near_local` сверху и снизу): скрин автора держит
    единицы зон, и полная карта на одном ТФ превращается в жёлтую заливку (проверено
    первым же рендером — 57 зон закрыли весь график).
    """
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    rank = order.get(timeframe, 0)
    fit = [z for z in zones if order.get(z.timeframe, 0) >= rank]
    senior = [z for z in fit if z.timeframe in SENIOR_TFS]
    local = [z for z in fit if z.timeframe not in SENIOR_TFS]
    above = sorted((z for z in local if z.price >= last_price), key=lambda z: z.price)
    below = sorted((z for z in local if z.price < last_price), key=lambda z: -z.price)
    return senior + above[:near_local] + below[:near_local]


def pp_zones(bars: list[Bar], timeframe: str) -> list[ZoneSpec]:
    """Зоны подтверждённых ПП по свежим барам — чистый расчёт для отображения."""
    sw = swings.detect(bars)
    if not hasattr(sw, "of"):
        return []
    out = []
    for pp in pereprior.detect(bars, sw, timeframe):  # type: ignore[arg-type]
        out.append(ZoneSpec(side=pp.side.value, timeframe=timeframe,
                            price=pp.zone_hi if pp.side.value == "short" else pp.zone_lo,
                            zone_lo=pp.zone_lo, zone_hi=pp.zone_hi, kind="pp"))
    return out


ENTRY_MARK = {
    "limit": "",
    "confirmation": " ⏳ уже касались — лимиток нет, только по слому младшего ТФ (стр. 31)",
    "retest_flipped": " ↩ пробит и флипнут — вход по ретесту в другую сторону (стр. 43)",
    "": " ⚠ правило входа не записано (строка карты до схемы 6)",
}
"""Подпись правила входа. Пустая строка у `limit` намеренно: свежий уровень с лимитками —
норма, и помечать надо ОТКЛОНЕНИЕ от неё, а не каждую строку."""


def _fmt_zone(z: ZoneSpec) -> str:
    band = "🟢" if (z.timeframe in SENIOR_TFS and z.side == "long") else (
        "🔴" if (z.timeframe in SENIOR_TFS and z.side == "short") else "🟡")
    if abs(z.zone_hi - z.zone_lo) / max(z.price, 1e-12) < 0.0005:
        rng = f"{z.price:g}"
    else:
        rng = f"{z.zone_lo:g}–{z.zone_hi:g} (ПОК {z.price:g})"
    return f"{band} {rng} · {z.timeframe}{ENTRY_MARK.get(z.entry_rule, '')}"


def compose_text(symbol: str, zones: list[ZoneSpec], pps: list[ZoneSpec]) -> str:
    """Сводка в формате, близком к каналу автора: Лонги / Шорты по старшинству ТФ."""
    base = symbol.split("/")[0]
    order = {tf: i for i, tf in enumerate(TIMEFRAME_MS)}
    longs = sorted((z for z in zones if z.side == "long"),
                   key=lambda z: (-order.get(z.timeframe, 0), -z.price))
    shorts = sorted((z for z in zones if z.side == "short"),
                    key=lambda z: (-order.get(z.timeframe, 0), z.price))
    def pick(rows: list[ZoneSpec]) -> tuple[list[str], int]:
        senior = [z for z in rows if z.timeframe in SENIOR_TFS]
        local = [z for z in rows if z.timeframe not in SENIOR_TFS][:6]
        dropped = len(rows) - len(senior) - len(local)
        return [f"  {_fmt_zone(z)}" for z in senior + local], max(dropped, 0)

    lines = [f"👑 {base} — карта уровней hunter", ""]
    lines.append("Лонги:")
    ls, dl = pick(longs)
    lines += ls or ["  активных нет"]
    if dl:
        lines.append(f"  … и ещё {dl} локальных")
    lines.append("")
    lines.append("Шорты:")
    ss, ds = pick(shorts)
    lines += ss or ["  активных нет"]
    if ds:
        lines.append(f"  … и ещё {ds} локальных")
    if pps:
        lines.append("")
        lines.append("Переприоры (по свежим барам):")
        lines += [f"  🟣 {p.side} зона {p.zone_lo:g}–{p.zone_hi:g} · {p.timeframe}"
                  for p in pps]
    lines.append("")
    lines.append("Уровни — из карты прогонов (§2.2, зоны VAL–VAH, ПОК линией). "
                 "Это карта системы, не торговая рекомендация.")
    return "\n".join(lines)


async def handle_ticker(message: Message, symbols: tuple[str, ...]) -> None:
    text = message.text or ""
    sym = resolve_symbol(text, symbols)
    if sym is None:
        known = ", ".join(s.split("/")[0] for s in symbols)
        await message.answer(f"Не узнаю тикер «{text.strip()}». Вселенная: {known}")
        return
    zones = await asyncio.to_thread(map_zones, sym)
    ex = ccxt.binanceusdm()
    tmp = Path(tempfile.mkdtemp(prefix="hunter-tg-"))
    all_pp: list[ZoneSpec] = []
    try:
        for tf in CHART_TFS:
            bars = await asyncio.to_thread(closed_bars, ex, sym, tf)
            pps = pp_zones(bars, tf)
            all_pp += pps
            shown = zones_for_chart(zones, tf, bars[-1].close)
            png = chart_png(sym, tf, bars, shown + pps, tmp / f"{tf}.png",
                            caption=f"{len([z for z in zones if z.side == 'long'])} лонг"
                                    f" / {len([z for z in zones if z.side == 'short'])} шорт зон")
            await message.answer_photo(FSInputFile(png))
        await message.answer(compose_text(sym, zones, all_pp))
    except ccxt.BaseError as e:
        await message.answer(f"Биржа не ответила: {type(e).__name__} — попробуйте позже")
        log.degraded("бот: биржа не ответила", символ=sym, причина=str(e))


async def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("ОТКАЗ: не задан TELEGRAM_BOT_TOKEN — боту не с чем подключаться (§4.3)")
        return 2
    uni = load_universe()
    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message()
    async def _on_message(message: Message) -> None:
        await handle_ticker(message, uni.symbols)

    log.info("бот доставки запущен", символов=len(uni.symbols),
             графики="/".join(CHART_TFS))
    await dp.start_polling(bot)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
