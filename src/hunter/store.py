"""Хранение: parquet для кадров, SQLite для леджера. FOUNDATION.md §10.2, §10.3.

Parquet — сырые кадры, без которых детерминированный повтор невозможен (§10.3).
SQLite — состояние и исходы, со схемой и ограничениями (§10.2).

Запись в боевую базу возможна ТОЛЬКО через `open_production_ledger`, и это ограничение
уровня СУБД, а не дисциплина: все прочие соединения открываются `mode=ro`, и попытка
записи через них падает с «attempt to write a readonly database». Кто зовёт
`open_production_ledger`, проверяет gates/production_writer.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from . import log
from .bars import tf_ms
from .levels import EntryRule, Level, LevelState
from .models import Bar, BarBinnedTrades, NotReady, TradeHistogram

DATA_DIR = Path("data")
FRAMES_DIR = DATA_DIR / "frames"
LEDGER_PATH = DATA_DIR / "ledger.sqlite3"

# §10.2 задаёт ограничения дословно: NOT NULL на цене входа, CHECK (stop != entry),
# UNIQUE (symbol, opened_at). Остальные поля появятся на этапах 5–7 вместе с тем,
# что их производит; поля без продюсера здесь заводить нельзя (§0).
#
# ⚠ Ключ РАСШИРЕН против буквы §10.2 — с (symbol, opened_at) до
# (symbol, timeframe, direction, opened_at), — и это правка документа, а не вольность.
# Сетки ТФ вложены: метка открытия 4ч-бара ВСЕГДА совпадает с меткой какого-то 1ч-бара,
# 1ч — с 15м и 5м. Прогон: «1ч сигнал записан; 4ч сигнал в ту же метку — UNIQUE
# constraint failed». Совпадение здесь норма предметной области, а не бессмыслица,
# которую ограничение должно ловить. Замер и разбор: docs/audit/ledger-2026-08-04.md
# (⚠ имя исправлено 2026-08-17: прежде стояло ledger-honesty-…, файла с таким именем нет)
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL DEFAULT 'level' CHECK (kind IN ('level', 'pp')),
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('long', 'short')),
    opened_at   INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    entry       REAL    NOT NULL,
    stop        REAL    NOT NULL,
    target      REAL,
    -- Цена, по достижении которой стоп переносится в ТВХ (стр. 19, 15, 44). NULL —
    -- правила безубытка у сделки нет. ⚠ Это НЕ цель: до 2026-08-19 дорешивание
    -- подавало сюда `target`, и исход «в безубытке» становился недостижим — взведение
    -- срабатывало тем же баром, что и закрытие по цели, а цель проверяется первой.
    breakeven_at REAL,
    frames_ref  TEXT    NOT NULL,
    CHECK (stop != entry),
    CHECK (entry > 0),
    CHECK (stop > 0),
    CHECK (recorded_at > 0),
    UNIQUE (kind, symbol, timeframe, direction, opened_at)
);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id  INTEGER PRIMARY KEY REFERENCES signals(id),
    kind       TEXT    NOT NULL CHECK (kind IN ('stop', 'target', 'ambiguous',
                                                'breakeven')),
    closed_at  INTEGER NOT NULL,
    exit_price REAL,
    r          REAL,
    CHECK ((kind = 'ambiguous') = (r IS NULL)),
    CHECK ((kind = 'ambiguous') = (exit_price IS NULL))
);

-- Состояние сигнала, ещё НЕ ставшего исходом (v4, 2026-08-10). Ровно два значения:
-- `not_filled` — цена не дошла до входа, сделки не было (стр. 30: вход лимитками);
-- `open` — вход состоялся, ни стоп, ни цель не достигнуты.
-- Строка перезаписывается каждым прогоном (`as_of` — до какого бара считали): это
-- СОСТОЯНИЕ, а не событие. Событие необратимо и живёт в `outcomes`.
CREATE TABLE IF NOT EXISTS signal_states (
    signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
    state     TEXT    NOT NULL CHECK (state IN ('not_filled', 'open')),
    as_of     INTEGER NOT NULL CHECK (as_of > 0)
);

CREATE TABLE IF NOT EXISTS levels (
    symbol       TEXT    NOT NULL,
    timeframe    TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('long', 'short')),
    price        REAL    NOT NULL CHECK (price > 0),
    zone_lo      REAL    NOT NULL CHECK (zone_lo > 0),
    zone_hi      REAL    NOT NULL CHECK (zone_hi > 0),
    boundary_lo  REAL    NOT NULL CHECK (boundary_lo > 0),
    boundary_hi  REAL    NOT NULL CHECK (boundary_hi > 0),
    volume       REAL    NOT NULL CHECK (volume > 0),
    from_ms      INTEGER NOT NULL,
    to_ms        INTEGER NOT NULL,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    -- ⚠ ТРЕТЬЕ СОСТОЯНИЕ `stale_calc` ЗАВЕДЕНО 2026-08-21 (схема 15), и оно ОТЛИЧАЕТСЯ
    -- ОТ ДВУХ ПЕРВЫХ ПО РОДУ. `worked_off` (стр. 25) и `flipped` (стр. 43) говорят, что
    -- сделала ЦЕНА. `stale_calc` говорит, что сделали МЫ: структура, из которой уровень
    -- построен, в свежем разборе не находится, хотя её время лежит внутри собранного
    -- ряда. Личность уровня есть (ТФ, начало структуры, конец структуры), и она меняется
    -- вместе с детектором свингов — правка стр. 13 о равных хаях/лоях сдвинула границы
    -- структур и осиротила всю прежнюю карту.
    -- Замер, ради которого это заведено: активных уровней вселенной 1203, пересчитано
    -- свежим прогоном 391, а 812 (67.5%) судились правилом `resolve_carried`, которое
    -- СЛАБЕЕ обычного. Две трети живой карты оценивал не тот прибор.
    -- Писать им `worked_off` было бы враньём про рынок; оставлять `active` — враньём про
    -- нас. Поэтому третье имя, и оно называет ровно то, что произошло.
    state        TEXT    NOT NULL CHECK (state IN ('active', 'worked_off', 'flipped',
                                                   'stale_calc')),
    retired_at   INTEGER,
    -- ЧЕМ уровень торгуется. NULL — строка записана до схемы 6, правило не сохранялось.
    -- ⚠ Заведено 2026-08-11: `state='active'` НЕ означает «цена не касалась». Курс
    -- (стр. 25) снимает лимитки уже на первое касание, оставляя вход по слому младшего
    -- ТФ, — то есть у активного уровня два разных разрешения, и в карте их не было.
    -- Замер на BEAT: из 21 активного уровня 8 цена уже касалась, а бот показывал их
    -- владельцу наравне со свежими. Расчёт различал (`LevelStatus.entry_rule`), карта —
    -- нет, и потому различие до владельца не доходило.
    entry_rule   TEXT    CHECK (entry_rule IN ('limit', 'confirmation', 'retest_flipped')),
    -- Подтверждён ли слом структуры на младшем ТФ У ЭТОГО уровня: 1 — да, 0 — нет,
    -- NULL — вопрос неприменим (цена к уровню не приходила либо младшего ТФ нет:
    -- 5м младший в курсе, стр. 17). Нужен ДОСТАВКЕ: уведомление обязано сказать,
    -- что вход по слому безопаснее лимитки (стр. 19), а бары наблюдателю
    -- недоступны — он ходит одним `fetch_tickers`.
    mtf_break    INTEGER,
    -- Согласие со СТАРШИМ ТФ: 'by_trend' | 'against_trend' | 'no_priority' | NULL.
    -- Нужно ДОСТАВКЕ: стр. 47 разрешает встречную сделку только «имея позицию по
    -- тренду – в виде хэджа и/или на уменьшенный объем риска», а стр. 11 — только под
    -- прибыльную. Карточка это печатала, уведомление молчало, и читатель мог купить
    -- против приоритета, не узнав об этом.
    agreement    TEXT,
    -- Закрытие бара, на котором СОБЫТИЕ разрешилось (прокол/пробой). NULL — события нет
    -- либо строка до схемы 7.
    -- ⚠ Это НЕ `retired_at`. Тот хранит момент ПРОГОНА, в котором уровень сняли с карты,
    -- и у всех снятых он одинаков. Разница вскрылась 2026-08-17 при попытке нарисовать
    -- значки отработки: 663 значка встали бы в одну точку графика — время записи вместо
    -- времени события. Расчёт момент знает (`LevelStatus.resolved_at_ms`), карта его
    -- теряла.
    resolved_at  INTEGER,
    -- Плотность объёма в зоне: во сколько раз объём НА ЦЕНОВУЮ СТРОКУ профиля выше
    -- среднего по композиту (стр. 22: «Сила уровня определяется ТФ и объемом»).
    -- NULL — композит не построен либо строка до схемы 8.
    -- ⚠ Заведена 2026-08-19 по приказу владельца «примени силу по объёму в отборе
    -- уровней». Половина критерия силы существовала ТОЛЬКО в тексте карточки: карта
    -- леджера несла `volume` (объём структуры в монетах, величина другая), и отбор зон
    -- бота — какие уровни владелец увидит — про объём не знал ничего. Уровень с 51.8%
    -- композита и уровень с 0.002% различались в карточке и не различались нигде более.
    -- ⚠ Хранится ПЛОТНОСТЬ, а не доля объёма, и это исправление измерителя. Доля
    -- (`объём зоны / объём композита`) оказалась переодетой ШИРИНОЙ ЗОНЫ: ранговая
    -- корреляция с шириной +0.716 на 9715 уровнях, контроль по расстоянию до цены
    -- −0.107. Отбор по такой мере означал бы «показываем самую широкую полосу».
    -- Плотность безразмерна, сравнима между символами и от ширины не зависит по
    -- построению. Разбор — докстрока `levels.Level.vrvp_zone_bins`.
    vrvp_density REAL,
    -- ⚠⚠ ТРИ КОЛОНКИ НИЖЕ ДОБАВЛЕНЫ В ТЕКСТ СХЕМЫ 2026-08-21, И ЭТО ПОЧИНКА, А НЕ
    -- НОВОВВЕДЕНИЕ. Они заводились миграциями 12→13 и 13→14 через `ALTER TABLE ADD
    -- COLUMN`, а в этот текст вписаны не были. Следствие: СВЕЖАЯ база создавалась БЕЗ
    -- них — `CREATE TABLE` берётся отсюда, а лестница миграций на пустой базе не
    -- срабатывает ни разу (её первая же проверка отвечает «базы ещё нет»).
    -- Проверено прямым замером: новая база получала 20 колонок из 23 и штамповалась
    -- версией 14; первая же запись уровня упала бы на `no such column: stop_price`.
    -- Это ровно та ловушка, о которой предупреждает комментарий у `_schema_version`,
    -- только на шаг дальше: там следят за подъёмом константы, а здесь разошлись
    -- ТЕКСТ схемы и ЛЕСТНИЦА миграций.
    -- ⚠ Порядок именно такой (`stop_price`, `priority_tf`, `priority_depth`) — тот же,
    -- в каком их дописывал `ALTER TABLE`, чтобы у свежей и у мигрированной базы состав
    -- совпадал не только по именам.
    stop_price   REAL,
    priority_tf  TEXT,
    priority_depth INTEGER,
    CHECK (zone_lo <= price AND price <= zone_hi),
    CHECK (boundary_lo < boundary_hi),
    -- ⚠⚠ ЗОНА ВНУТРИ СТРУКТУРЫ. Ограничение добавлено 2026-08-23 (схема 16), и оно
    -- ровно то, что владелец зафиксировал капслоком 2026-08-18: «ЕСЛИ зона выходит за
    -- структуру то СТРУКТУРА ОПРЕДЛЕННА НЕ ВЕРНО». До этого дня инвариант сторожил
    -- ТОЛЬКО `gates/geometry_invariants.check_level` — то есть гейт на объектах в
    -- памяти, а карта, которая живёт МЕЖДУ прогонами и питает бота, пропускала
    -- нарушение молча. Проверка в схеме сильнее любого гейта: её исполняет SQLite на
    -- КАЖДОЙ настоящей записи, а не наш код на подобранных примерах.
    CHECK (boundary_lo <= zone_lo AND zone_hi <= boundary_hi),
    CHECK (to_ms > from_ms),
    CHECK (last_seen >= first_seen),
    CHECK ((retired_at IS NULL) = (state = 'active')),
    PRIMARY KEY (symbol, timeframe, from_ms, to_ms)
);

-- ⚠⚠ ИНДЕКСЫ ЗАВЕДЕНЫ 2026-08-23. До этого дня в проекте не было НИ ОДНОГО
-- (`grep "CREATE INDEX" src/hunter/store.py` → пусто), при том что бот на каждый ответ
-- ходит `WHERE symbol=? AND state='active' … ORDER BY price` по таблице, чей первичный
-- ключ — `(symbol, timeframe, from_ms, to_ms)`: по нему отбор по состоянию и сортировка
-- по цене не поддерживаются, и SQLite читает все строки символа. Сводки по времени
-- (`signals.recorded_at`, `outcomes.closed_at`) сканировали таблицу целиком.
--
-- `IF NOT EXISTS` и место в общем тексте схемы — не украшение: `executescript(SCHEMA)`
-- выполняется при КАЖДОМ открытии базы, поэтому индексы появляются и у свежей базы, и
-- у выросшей миграциями, без отдельной ступени и без риска разойтись с текстом схемы
-- (тот дефект здесь уже случался — см. комментарий у `stop_price`).
CREATE INDEX IF NOT EXISTS levels_symbol_state ON levels (symbol, state, price);
CREATE INDEX IF NOT EXISTS levels_last_seen ON levels (symbol, last_seen);
CREATE INDEX IF NOT EXISTS levels_retired ON levels (retired_at);
CREATE INDEX IF NOT EXISTS signals_recorded ON signals (recorded_at);
CREATE INDEX IF NOT EXISTS signals_lookup ON signals (kind, symbol, timeframe);
CREATE INDEX IF NOT EXISTS outcomes_closed ON outcomes (closed_at);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# Таблица `levels` — КАРТА, живущая между прогонами (стр. 25, 31: уровень существует, пока
# не отработан, а не пока он виден в окне баров). Ключ — ОКНО СТРУКТУРЫ, а не цена ПОК:
# структура это то, чем уровень порождён, и она не меняется, тогда как ПОК может
# сдвинуться на бин при доливке сделок. Ключ по цене плодил бы дубликаты на каждый прогон.
#
# `retired_at` заполнен ровно тогда, когда состояние не `active`, и ограничение это
# требует: «уровень снят» без даты и «дата без снятия» — обе формы молчания (§4.3).
#
# Полей «лимитки выставлены» и «позиция взята» здесь НЕТ. У автора они в легенде есть
# (🟢, ✅), но продюсера у них в этой системе нет: ордера она не ставит (§1 — оператор
# торгует руками) и о них не узнаёт. Поле без продюсера запрещено §0 и есть фирменный
# дефект прошлого проекта.
# Исход «неоднозначно» (бар накрыл и стоп, и цель) хранится БЕЗ r и без цены выхода, и
# ограничение это требует: подставить туда ноль значило бы записать безубыток там, где
# результат неизвестен (§4.3). Открытые и несостоявшиеся сделки в таблицу не попадают
# вовсе — исход у них ещё не наступил, а строка означала бы, что наступил.
#
# `recorded_at` — МОМЕНТ, КОГДА СИСТЕМА УЗНАЛА О СИГНАЛЕ, и он не то же самое, что
# `opened_at` (бар рождения уровня, стр. 23). Разница между ними и есть разница между
# журналом и бэктестом: `opened_at` может быть на сотни баров в прошлом, потому что
# уровень строится по завершённой структуре, а `recorded_at` — всегда «сейчас».
#
# ⚠ Поле заведено 2026-08-04. До него исход считался по барам, которые УЖЕ ЛЕЖАЛИ В
# ПАМЯТИ на момент записи сигнала: каждый прогон заново переигрывал всю доступную
# историю и складывал результат в базу, которую §10.2 и §8 объявляют боевой. Владельцу
# при этом печаталось «средний R» по этой подвыборке — то есть по бэктесту под именем
# журнала. Теперь исход считается ТОЛЬКО по барам, закрывшимся ПОЗЖЕ `recorded_at`;
# следствие честное и неприятное: в первый прогон исходов не бывает вовсе.

SCHEMA_VERSION = "16"
"""Версия схемы леджера. Растёт, когда прежние строки перестают означать то же самое.

15 → 16 (2026-08-23): у `levels` появилось ограничение «ЗОНА ВНУТРИ СТРУКТУРЫ»
(`boundary_lo <= zone_lo AND zone_hi <= boundary_hi`) — то самое, что владелец
зафиксировал капслоком 2026-08-18. До него инвариант сторожил только гейт, то есть
объекты в памяти, а карта между прогонами пропускала нарушение молча. Прежние строки,
нарушающие его, УДАЛЯЮТСЯ миграцией и их число НАЗЫВАЕТСЯ в журнале: они построены
расчётом, про который сам владелец сказал, что он неверен, и оставить их значило бы
показывать их боту дальше. Той же ступенью заводятся первые в проекте индексы.

8 → 9 (2026-08-19): у исхода появился БЕЗУБЫТОК (`breakeven`, стр. 14). До него схема
знала три исхода, и `CHECK (kind IN …)` отклонил бы четвёртый — то есть сделка, вышедшая
в ноль по правилу курса, либо не записалась бы вовсе, либо была бы записана стопом с
R = −1. Прежние строки не меняются: ни одна из них безубытком не была, потому что такого
исхода расчёт не производил. Ограничение перестраивается целиком — `CHECK` в SQLite на
месте не правится.

7 → 8 (2026-08-19): у уровня появилась `vrvp_density` — плотность объёма в зоне,
вторая половина критерия силы по стр. 22. Прежние строки получают NULL: композит у них
не считался вовсе, а подставить `volume` нельзя — это объём структуры в монетах.
Первый боевой прогон перепишет строку настоящим значением.

6 → 7 (2026-08-17): у уровня появился `resolved_at` — момент СОБЫТИЯ (прокола либо
пробоя), отличный от `retired_at` (момента ПРОГОНА, в котором уровень сняли). Без него
разметка отработки ставила бы все значки в одну точку: 663 снятых уровня BTW имели один
и тот же `retired_at`. Прежние строки получают NULL и переписываются первым прогоном.

5 → 6 (2026-08-11): у уровня карты появилось ПРАВИЛО ВХОДА (`entry_rule`). Без него
`state='active'` читался как "уровень свежий, цена не касалась", тогда как курс
(стр. 25) снимает лимитки на первое же касание, оставляя активным сам уровень. Замер на
BEAT: 8 активных уровней из 21 — уже касавшиеся, и в карте они были неотличимы от
нетронутых. Прежние строки получают NULL и переписываются первым же прогоном.

4 → 5 (2026-08-10): у сигнала появилась ЦЕЛЬ (`target`). Без неё исход сделки нельзя
досчитать ни в каком прогоне, кроме того, который её выдал, — и отсюда шло СМЕЩЕНИЕ
ОТБОРА, найденное сводкой исходов в тот же день. Исход считался только для сигналов,
эмитируемых ЗАНОВО (`decided[sym].emissions`); сигнал, чей уровень ушёл из карты
(отработан по стр. 25, пробит по стр. 43 или просто не отобран), не досчитывался
НИКОГДА. Замер на боевом леджере: 76 сигналов из 112 — то есть две трети журнала —
навсегда оставались без ответа, а «средний R» считался по оставшейся трети, смещённой
в сторону уровней, которые система продолжает отбирать.

Старые строки получают `target = NULL`: цель у них не сохранялась и восстановлению не
подлежит. Дорешать их нельзя, и сводка обязана называть их отдельно, а не молчать.

3 → 4 (2026-08-10): заведена `signal_states` — состояние сигнала, ЕЩЁ НЕ ставшего
исходом. До неё `emit.outcome_of` каждый прогон различал `not_filled` (цена не дошла до
входа) и `open` (сделка идёт), и оба ответа ВЫБРАСЫВАЛИСЬ: в леджер попадали только
`stop`/`target`/`ambiguous`, а различие жило одним счётчиком в отчёте прогона. Цена
потери вскрылась сводкой исходов: клетка «6 сигналов, 1441 бар прожито, ноль исходов»
неотличима от дефекта, хотя может означать «цена ни разу не дошла до входа» — то есть
законный ответ системы. Прежние строки не меняются: таблица новая и пустая, старые
сигналы состояния не получают, пока их не пересчитает прогон.

2 → 3 (2026-08-10): появился `kind` — тип сигнала: `level` (сделка от уровня ПОК) и
`pp` (сделка от переприора, стр. 50; реестр долга, строка 3). Уникальный ключ расширен
типом: сигнал от ПП и сигнал от уровня с совпавшими (символ, ТФ, сторона, бар) — разные
сигналы, и прежний ключ дедуплицировал бы их друг о друга. Прежние строки все от
уровней — при перестройке получают `kind = 'level'`, их смысл не меняется.

1 → 2 (2026-08-04): появился `recorded_at`, ключ сигнала расширен ТФ и стороной, исход
считается только по будущим барам. Строки версии 1 — переигранная история, и сложить их
с новыми значило бы смешать бэктест с журналом в одном `AVG(r)`.
"""


# --- кадры для повтора (§10.3) ------------------------------------------------

def _safe(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def frames_path(run_id: str, symbol: str, timeframe: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / f"{timeframe}.parquet"


def bars_to_frame(bars: list[Bar]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_ms": [b.open_ms for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        schema={"open_ms": pl.Int64, "open": pl.Float64, "high": pl.Float64,
                "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64},
    )


def frame_to_bars(df: pl.DataFrame) -> list[Bar]:
    return [Bar(**row) for row in df.iter_rows(named=True)]


def write_bars(run_id: str, symbol: str, timeframe: str, bars: list[Bar]) -> Path:
    path = frames_path(run_id, symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars_to_frame(bars).write_parquet(path, compression="zstd")
    return path


def read_bars(run_id: str, symbol: str, timeframe: str) -> list[Bar]:
    return frame_to_bars(pl.read_parquet(frames_path(run_id, symbol, timeframe)))


def histogram_to_frame(h: TradeHistogram) -> pl.DataFrame:
    bins = sorted(h.qty_by_bin)
    return pl.DataFrame(
        {
            "bin": bins,
            "price": [float(h.bin_price(b)) for b in bins],
            "qty": [h.qty_by_bin[b] for b in bins],
            "n": [h.count_by_bin[b] for b in bins],
        },
        schema={"bin": pl.Int64, "price": pl.Float64, "qty": pl.Float64, "n": pl.Int64},
    )


def write_histogram(run_id: str, h: TradeHistogram) -> Path:
    path = FRAMES_DIR / run_id / _safe(h.symbol) / "profile.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    histogram_to_frame(h).write_parquet(path, compression="zstd")
    return path


def _binned_path(run_id: str, symbol: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / "trades_by_bar.parquet"


def write_binned_trades(run_id: str, t: BarBinnedTrades) -> Path:
    """Сделки по корзинам баров. Без них уровень §2.2 из кадров не восстановить."""
    rows = sorted((b, i, q) for b, bins in t.qty.items() for i, q in bins.items())
    path = _binned_path(run_id, t.symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "bucket_ms": [r[0] for r in rows],
            "bin": [r[1] for r in rows],
            "qty": [r[2] for r in rows],
            "n": [t.cnt.get(r[0], {}).get(r[1], 0) for r in rows],
        },
        schema={"bucket_ms": pl.Int64, "bin": pl.Int64, "qty": pl.Float64, "n": pl.Int64},
    ).write_parquet(path, compression="zstd")
    return path

# --- карточка: единица повтора (§10.3, §10.6 условие 2) -----------------------

def card_path(run_id: str, symbol: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / "card.txt"


def write_card(run_id: str, symbol: str, text: str) -> Path:
    path = card_path(run_id, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def read_card(run_id: str, symbol: str) -> str | NotReady:
    path = card_path(run_id, symbol)
    if not path.exists():
        return NotReady(reason=f"{symbol}: карточки прогона нет — {path}")
    return path.read_text(encoding="utf-8")


def write_meta(run_id: str, symbol: str, tick_size: Decimal, bucket_ms: int) -> Path:
    """Шаг цены и сетка корзин. Без них раскладку сделок из parquet не собрать обратно."""
    path = FRAMES_DIR / run_id / _safe(symbol) / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbol": symbol, "tick_size": str(tick_size),
                                "bucket_ms": bucket_ms}, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
    return path


def read_meta(run_id: str, symbol: str) -> tuple[str, Decimal, int] | NotReady:
    """Возвращает НАСТОЯЩЕЕ имя символа, шаг цены и сетку корзин.

    Имя каталога — это `_safe(symbol)`, из него исходное имя не восстановить (в нём
    подчёркивания вместо `/` и `:`), поэтому оно хранится явно.
    """
    path = FRAMES_DIR / run_id / _safe(symbol) / "meta.json"
    if not path.exists():
        return NotReady(reason=f"{symbol}: meta.json прогона нет — {path}")
    d = json.loads(path.read_text(encoding="utf-8"))
    return str(d["symbol"]), Decimal(d["tick_size"]), int(d["bucket_ms"])


def profile_bars_path(run_id: str, symbol: str, timeframe: str) -> Path:
    return FRAMES_DIR / run_id / _safe(symbol) / f"profile_bars_{timeframe}.parquet"


def write_profile_bars(run_id: str, symbol: str, timeframe: str,
                       bars: list[Bar]) -> Path:
    """Профильный ряд (интрабар-свечи `TVWindows`), которого нет среди аналитических
    кадров, — на боевом конфиге это минутки. Основа герметичности повтора после
    перевода профиля на свечи (решение владельца 2026-08-12, код переведён
    2026-08-17): аналитические ряды в кадрах и так лежат, а минутный до 2026-08-18
    не сохранял никто — повтор строил профиль из ДРУГОГО источника (среза сделок,
    который с 2026-08-17 перестал писаться) и печатал «ИЗМЕНИЛОСЬ 5 из 5» при
    неизменённом коде."""
    path = profile_bars_path(run_id, symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars_to_frame(bars).write_parquet(path, compression="zstd")
    return path


def write_source_meta(run_id: str, symbol: str, profile_tfs: list[str],
                      analysis_tfs: list[str] | None = None,
                      horizon_days: int | None = None) -> Path:
    """Манифест источника профиля: КАКИЕ профильные ряды прогон положил в кадры.

    Пустой список — честное «минуток в хранилище не было»: повтор тогда строит
    источник без них и воспроизводит те же отказы. ОТСУТСТВИЕ манифеста — другое
    состояние: кадры сняты кодом без сохранения источника (до 2026-08-18), и повтор
    обязан отказаться, а не молча собрать источник из неполных данных — иначе он
    печатает осмысленно выглядящий, но бессмысленный дифф (найдено аудитом правки:
    45 из 46 прогонов на диске без профильных рядов давали «расчёт изменился» при
    неизменном коде). Манифест же закрывает и обратный отказ: если запись рядов
    когда-нибудь снова потеряется молча, повтор таких кадров скажет об этом кодом
    возврата, а не диффом.

    `analysis_tfs` (добавлено 2026-08-18) — состав АНАЛИТИЧЕСКИХ рядов, записанных
    в кадры. Без него повтор выводил набор ТФ из наличия файлов на диске: пропавший
    после прогона parquet менял состав молча, и дифф читался как «расчёт изменился».
    None — манифест старой формы, сверка состава тогда не проводится (названно).

    ⚠⚠ `horizon_days` (добавлено 2026-08-23) — ГОРИЗОНТ, С КОТОРЫМ СЧИТАЛ ПРОГОН, и без
    него повтор §10.6 не мог совпасть НИ С ОДНИМ боевым прогоном. `replay` звал
    `engine.decide` без этого аргумента, то есть пересчитывал с `horizon_days=0` —
    отсечки старых структур не было вовсе. Замер на кадрах `rebuild-2026-08-23`:
    строка «структура закрылась раньше горизонта» стоит в сохранённой карточке 367 раз
    и в пересчитанной НОЛЬ раз; вслед за ней уезжает состав ближайших уровней и окно
    композита (433 против 213 суток). Повтор печатал «ИЗМЕНИЛОСЬ 4 из 4» при
    неизменённом коде — тот же класс отказа, что и потеря профильного ряда выше, и
    лечится так же: величина, от которой зависит ответ, кладётся В КАДРЫ.

    None — манифест старой формы (до 2026-08-23). Повтор таких кадров ОТКАЗЫВАЕТСЯ:
    подставить 0 значило бы молча посчитать другое и выдать это за сравнение."""
    path = FRAMES_DIR / run_id / _safe(symbol) / "source.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    d: dict[str, object] = {"transport": "tv_candles",
                            "profile_tfs": sorted(profile_tfs)}
    if analysis_tfs is not None:
        d["analysis_tfs"] = sorted(analysis_tfs)
    if horizon_days is not None:
        d["horizon_days"] = int(horizon_days)
    path.write_text(json.dumps(d, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
    return path


def read_source_meta(
    run_id: str, dir_name: str,
) -> tuple[list[str], list[str] | None, int | None] | NotReady:
    """Манифест: (профильные ТФ, аналитические ТФ, горизонт). `NotReady` — манифеста нет.

    Второй элемент `None` — манифест старой формы (до 2026-08-18), состав
    аналитических рядов тогда не записывался и сверить его повтору не с чем.
    Третий `None` — форма до 2026-08-23, горизонт не записывался; что из этого следует
    для повтора, разобрано в `write_source_meta`."""
    path = FRAMES_DIR / run_id / _safe(dir_name) / "source.json"
    if not path.exists():
        return NotReady(
            reason=f"{dir_name}: манифеста источника нет ({path}) — кадры сняты до "
                   f"2026-08-18, профильный ряд не сохранялся; сравнение свечного "
                   f"профиля из таких кадров не строится")
    d = json.loads(path.read_text(encoding="utf-8"))
    analysis = d.get("analysis_tfs")
    horizon = d.get("horizon_days")
    return ([str(tf) for tf in d.get("profile_tfs", [])],
            None if analysis is None else [str(tf) for tf in analysis],
            None if horizon is None else int(horizon))


def read_profile_bars(run_id: str, dir_name: str) -> dict[str, list[Bar]]:
    """Сохранённые профильные ряды прогона, по ТФ. Пуст ли словарь ЗАКОННО — отвечает
    манифест (`read_source_meta`), не этот вызов: без манифеста пустоту не отличить
    от кадров, снятых до появления записи рядов."""
    d = FRAMES_DIR / run_id / _safe(dir_name)
    if not d.is_dir():
        return {}
    return {p.stem.removeprefix("profile_bars_"): frame_to_bars(pl.read_parquet(p))
            for p in sorted(d.glob("profile_bars_*.parquet"))}


def archive_dir(run_id: str, symbol: str) -> Path:
    """Срез архива сделок, принадлежащий ЭТОМУ прогону.

    ⚠ ТИКОВЫЙ транспорт: с 2026-08-17 боевой прогон строит профиль из свечей и повтор
    этот каталог не читает; путь жив для `archive.WindowSource` (зонды)."""
    return FRAMES_DIR / run_id / _safe(symbol) / "aggcache"


def write_archive_slice(run_id: str, symbol: str, source: Path) -> Path:
    """Положить сутки архива в кадры прогона. ЖЁСТКОЙ ССЫЛКОЙ, копией — только если нельзя.

    ⚠ ТИКОВЫЙ транспорт: боевой прогон с 2026-08-17 свечной, здесь пишут только
    `WindowSource`-источники (зонды); герметичность боевого повтора теперь держит
    `write_profile_bars` + манифест `write_source_meta`.

    ⚠ Без этого повтор НЕ герметичен, и это показано прогоном: кадры (parquet) не
    трогались, из общего `data/aggcache` убраны одни сутки — карточка ETH поехала с 3244
    строк на 2785, 483 строки различий. `replay --diff` напечатал бы «расчёт изменился»
    при неизменном коде и неизменных кадрах, а §10.6 условие 2 объявляет этот дифф
    единственной проверкой, не требующей чтения кода.

    Прежняя докстрока `replay` доказывала обратное — что архив неизменяем и потому
    пригоден. Верно про СОДЕРЖИМОЕ суток и неверно про их НАЛИЧИЕ, а профиль зависит от
    обоих.

    ⚠ Копирование заменено ссылкой 2026-08-06, и заменено ЗАМЕРОМ, а не из аккуратности.
    Пакетный прогон складывал срез один раз, и цена не бросалась в глаза; служба 24/7
    (А-1) складывает его КАЖДЫЙ ЦИКЛ, стирая предыдущий. Замер на трёх символах при
    горизонте 5 суток: 248 МБ на цикл, из них 245 файлов среза — 138 МБ на одном BTC.
    При такте в 300 с и полной вселенной это порядка 2 ГБ каждые пять минут, то есть
    десятки гигабайт записи в час на данные, которые уже лежат в общем кэше.

    Жёсткая ссылка даёт ту же герметичность, потому что герметичность здесь про НАЛИЧИЕ
    файла, а не про его копию: удаление из общего кэша не трогает содержимое, пока на
    него ссылается срез прогона, а `archive._write_atomic` подменяет файл через
    `os.replace`, то есть создаёт НОВЫЙ inode и на старую ссылку не влияет.

    Откат на копирование — при `OSError`: другой том, файловая система без ссылок,
    исчерпанный лимит ссылок. Это деградация, а не отказ, и она НАЗВАНА в логе (§4.3).
    """
    dst = archive_dir(run_id, symbol) / source.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        os.link(source, dst)
    except OSError as e:
        log.degraded("срез архива скопирован, а не связан ссылкой",
                     файл=source.name, причина=f"{type(e).__name__} {e}")
        shutil.copyfile(source, dst)
    return dst


def clear_run(run_id: str, symbol: str) -> None:
    """Стереть кадры символа перед записью новых.

    ⚠ `--run-id` по умолчанию `last`, а `write_bars`/`write_card` перезаписывают файлы,
    но НЕ удаляют оставшиеся от прошлого прогона. После прогона с `--symbols 3` в каталоге
    оставались кадры всех 27 символов от прошлого раза, и `saved_symbols` перечисляла то,
    что лежит на диске: повтор сравнивал карточку одного прогона с кадрами двух (А-4).
    """
    d = FRAMES_DIR / run_id / _safe(symbol)
    if d.is_dir():
        shutil.rmtree(d)


def saved_runs() -> tuple[str, ...]:
    """Прогоны, у которых на диске ЕСТЬ кадры. Порядок — от старых к свежим по правке.

    Нужна проверке владельца (§7.5). Она сама кадров не пишет — их пишет `hunter run`, —
    поэтому спрашивать «сколько кадров записал этот прогон» ей нечего: ответ всегда ноль.
    Правильный вопрос другой: «есть ли на диске то, что можно повторить», и отвечает на
    него состояние каталога, а не текущий сбор.
    """
    if not FRAMES_DIR.is_dir():
        return ()
    runs = [d for d in FRAMES_DIR.iterdir()
            if d.is_dir() and any(d.rglob("*.parquet"))]
    return tuple(d.name for d in sorted(runs, key=lambda p: p.stat().st_mtime))


def saved_symbols(run_id: str) -> tuple[str, ...]:
    """Символы, у которых в прогоне есть кадры. Порядок — алфавитный, для дет-повтора."""
    root = FRAMES_DIR / run_id
    if not root.is_dir():
        return ()
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir()))


def saved_timeframes(run_id: str, symbol: str) -> tuple[str, ...]:
    d = FRAMES_DIR / run_id / _safe(symbol)
    if not d.is_dir():
        return ()
    skip = {"profile", "trades_by_bar"}
    return tuple(sorted(p.stem for p in d.glob("*.parquet")
                        if p.stem not in skip
                        and not p.stem.startswith("profile_bars_")))


# --- леджер (§10.2) -----------------------------------------------------------

def open_readonly(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение только на чтение. Умолчание для всего, кроме боевой эмиссии."""
    if not path.exists():
        raise FileNotFoundError(f"нет базы {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    # Тот же busy_timeout, что у боевого соединения (2026-08-18): база живёт в WAL,
    # и читатель без таймаута получает «database is locked» в момент чекпойнта
    # писателя — то есть /сигналы у бота падал бы ровно тогда, когда служба пишет.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _tune(conn: sqlite3.Connection) -> None:
    """Настройки соединения, без которых объявленные схемой гарантии не работают.

    `foreign_keys` в SQLite выключены ПО УМОЛЧАНИЮ и включаются в КАЖДОМ соединении.
    Прогон 2026-08-04 до этой строки: `PRAGMA foreign_keys = 0`, и строка исхода на
    несуществующий сигнал 999999 записалась. §10.2 обещает «записать бессмысленную строку
    нельзя» — обещание держала только схема на бумаге.

    `journal_mode=WAL` и `busy_timeout` — для того, что §8 называет целью: процесс,
    живущий сутками, и владелец, открывающий `hunter ledger` из другого окна. Без них
    второе соединение получает `database is locked` вместо ответа.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def _schema_version(conn: sqlite3.Connection) -> str:
    """Версия схемы существующей базы. `1` — база до появления `recorded_at`."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if not cols:
        return SCHEMA_VERSION  # базы ещё нет: создастся сразу свежей
    if "recorded_at" not in cols:
        return "1"
    if "kind" not in cols:
        return "2"
    # 3 → 4 отличается не колонкой `signals`, а НАЛИЧИЕМ таблицы состояний: сама
    # `signals` при этом переходе не меняется.
    has_states = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_states'"
    ).fetchone()
    if not has_states:
        return "3"
    if "target" not in cols:
        return "4"
    lvl_cols = {r[1] for r in conn.execute("PRAGMA table_info(levels)")}
    # Пустой набор — таблицы `levels` ещё нет: её создаст `CREATE TABLE IF NOT EXISTS`
    # сразу свежей, и мигрировать нечего.
    if lvl_cols and "entry_rule" not in lvl_cols:
        return "5"
    if lvl_cols and "resolved_at" not in lvl_cols:
        return "6"
    if lvl_cols and "vrvp_density" not in lvl_cols:
        return "7"
    # 8 → 9 не видно по колонкам: меняется ОГРАНИЧЕНИЕ, а не состав полей. Спрашивается
    # сам текст `CREATE TABLE`, который SQLite хранит в `sqlite_master`.
    out_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='outcomes'"
    ).fetchone()
    if out_sql is not None and "breakeven" not in (out_sql[0] or ""):
        return "8"
    if "breakeven_at" not in cols:
        return "9"
    if lvl_cols and "mtf_break" not in lvl_cols:
        return "10"
    if lvl_cols and "agreement" not in lvl_cols:
        return "11"
    if lvl_cols and "stop_price" not in lvl_cols:
        return "12"
    if lvl_cols and "priority_tf" not in lvl_cols:
        return "13"
    # 14 → 15 не видно по колонкам: меняется ОГРАНИЧЕНИЕ `CHECK` на `state`, а не состав
    # полей. Спрашивается сам текст `CREATE TABLE` — тот же приём, что у ступени 8 → 9.
    # Без этой ступени последняя строка вернула бы `SCHEMA_VERSION`, база версии 14
    # назвала бы себя пятнадцатой, миграция не сработала бы ни разу, а `schema_meta` в
    # конце всё равно проштамповалась бы новым числом. Ловушка описана абзацем ниже, и
    # она уже срабатывала однажды.
    lvl_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='levels'"
    ).fetchone()
    lvl_text = (lvl_sql[0] or "") if lvl_sql is not None else ""
    if lvl_sql is not None and "stale_calc" not in lvl_text:
        return "14"
    # 15 → 16 тоже не видно по колонкам: добавляется `CHECK` «зона внутри структуры».
    # Тот же приём, что у ступеней 8 → 9 и 14 → 15, и та же причина держать ступень
    # здесь: без неё база версии 15 назвала бы себя шестнадцатой, ограничение не
    # появилось бы НИ РАЗУ, а `schema_meta` всё равно проштамповалась бы новым числом.
    if lvl_sql is not None and "zone_hi <= boundary_hi" not in lvl_text:
        return "15"
    return SCHEMA_VERSION


# ⚠ ЛОВУШКА ЭТОЙ ЛЕСТНИЦЫ, найденная пробником 2026-08-19. Версия выводится ИЗ КОЛОНОК,
# а не читается из `schema_meta`, — и последняя строка возвращает `SCHEMA_VERSION`. Значит
# подъёма константы НЕДОСТАТОЧНО: пока новой ступени тут нет, база прежней версии называет
# себя новой, ветка миграции не срабатывает НИ РАЗУ, а `schema_meta` в конце всё равно
# штампуется новым числом — база помечена мигрированной, не будучи ею.
#
# Замер на копии боевого леджера (49735 строк, версия 7): подъём `SCHEMA_VERSION` до «8»
# без этой ступени дал «версия ПОСЛЕ: 8» и новой колонки НЕТ. Первая же запись
# уровня упала бы на `no such column`. Поймано ровно потому, что миграция была проверена
# прогоном на копии, а не прочитана глазами.


def level_columns(conn: sqlite3.Connection) -> set[str]:
    """Колонки таблицы `levels` В ЭТОЙ базе. Нужна ЧИТАТЕЛЯМ, а не писателю.

    ⚠ Заведена 2026-08-19, и повод боевой. Колонку добавляет МИГРАЦИЯ, а миграция
    живёт в `open_production_ledger` — то есть у ПИСАТЕЛЯ. Бот и `check` открывают
    леджер `mode=ro` и мигрировать не могут по построению. Значит между выкладкой кода
    и первым боевым прогоном база остаётся прежней схемы, и запрос, называющий новую
    колонку, отвечает `no such column` — у бота это «леджер не прочитан», то есть
    ПУСТАЯ КАРТА вместо уровней.

    Дефект был бы виден не сразу и не мне: у меня боевой леджер только для чтения.
    Поэтому читатели спрашивают схему, а не предполагают её.
    """
    return {r[1] for r in conn.execute("PRAGMA table_info(levels)")}


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Отложить строки версии 1 в сторону и завести таблицы заново.

    Строки НЕ удаляются: они настоящие в том смысле, что их произвёл настоящий код на
    настоящих барах, — но отвечают на другой вопрос («как отработала бы история»), и
    сложить их с журналом живых сигналов в одном `AVG(r)` значит повторить инцидент
    §10.2 с обратным знаком. Поэтому они переезжают в `*_backtest_v1` и остаются
    доступны запросом, а `OWNER_QUERIES` их больше не видит.
    """
    conn.executescript(
        "ALTER TABLE signals RENAME TO signals_backtest_v1;"
        "ALTER TABLE outcomes RENAME TO outcomes_backtest_v1;"
    )
    conn.commit()


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """Достроить `kind` перестройкой таблицы: SQLite не расширяет UNIQUE на месте.

    Строки сохраняются все и получают `kind = 'level'` — до версии 3 других типов не
    существовало. `id` переносятся как есть: на них смотрит `outcomes.signal_id`, и
    ровно поэтому внешние ключи на время перестройки выключаются (штатный порядок
    миграций SQLite: DROP старой таблицы при включённых FK отклоняется, хотя новая
    встаёт на её место тем же именем и теми же id).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        "BEGIN;"
        "CREATE TABLE signals_v3 ("
        " id INTEGER PRIMARY KEY,"
        " kind TEXT NOT NULL DEFAULT 'level' CHECK (kind IN ('level', 'pp')),"
        " symbol TEXT NOT NULL,"
        " timeframe TEXT NOT NULL,"
        " direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),"
        " opened_at INTEGER NOT NULL,"
        " recorded_at INTEGER NOT NULL,"
        " entry REAL NOT NULL,"
        " stop REAL NOT NULL,"
        " frames_ref TEXT NOT NULL,"
        " CHECK (stop != entry), CHECK (entry > 0), CHECK (stop > 0),"
        " CHECK (recorded_at > 0),"
        " UNIQUE (kind, symbol, timeframe, direction, opened_at));"
        "INSERT INTO signals_v3 (id, kind, symbol, timeframe, direction, opened_at,"
        " recorded_at, entry, stop, frames_ref)"
        " SELECT id, 'level', symbol, timeframe, direction, opened_at,"
        " recorded_at, entry, stop, frames_ref FROM signals;"
        "DROP TABLE signals;"
        "ALTER TABLE signals_v3 RENAME TO signals;"
        "COMMIT;"
    )
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_8_to_9(conn: sqlite3.Connection) -> None:
    """Расширить `CHECK` исходов безубытком (стр. 14) перестройкой таблицы.

    SQLite не правит `CHECK` на месте, поэтому таблица создаётся заново и строки
    переносятся один в один: их смысл не меняется — ни один прежний исход безубытком
    не был. Внешние ключи выключаются на время перестройки по той же причине, что и в
    `_migrate_2_to_3`: `DROP` таблицы, на которую смотрит `signal_id`, при включённых FK
    отклоняется.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        "BEGIN;"
        "CREATE TABLE outcomes_v9 ("
        " signal_id INTEGER PRIMARY KEY REFERENCES signals(id),"
        " kind TEXT NOT NULL CHECK (kind IN ('stop','target','ambiguous','breakeven')),"
        " closed_at INTEGER NOT NULL,"
        " exit_price REAL,"
        " r REAL,"
        " CHECK ((kind = 'ambiguous') = (r IS NULL)),"
        " CHECK ((kind = 'ambiguous') = (exit_price IS NULL)));"
        "INSERT INTO outcomes_v9 (signal_id, kind, closed_at, exit_price, r)"
        " SELECT signal_id, kind, closed_at, exit_price, r FROM outcomes;"
        "DROP TABLE outcomes;"
        "ALTER TABLE outcomes_v9 RENAME TO outcomes;"
        "COMMIT;"
    )
    conn.execute("PRAGMA foreign_keys = ON")


def open_production_ledger(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    """Соединение на запись. ЕДИНСТВЕННАЯ точка записи в боевую базу (§10.2).

    Список того, кому это разрешено, держит gates/production_writer.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    _tune(conn)
    if _schema_version(conn) == "1":
        _migrate_1_to_2(conn)
    if _schema_version(conn) == "2":
        _migrate_2_to_3(conn)
    # 3 → 4 отдельной функции не требует: переход добавляет ПУСТУЮ таблицу, а
    # `CREATE TABLE IF NOT EXISTS` ниже её и создаёт. Перестройка (как в 2 → 3) нужна
    # только там, где меняется смысл существующих строк — здесь не меняется ни одной.
    if _schema_version(conn) == "4":
        # 4 → 5: колонка добавляется НА МЕСТЕ. Перестройка не нужна — `UNIQUE` не
        # трогается, а `ALTER TABLE ADD COLUMN` в SQLite дёшев и не переписывает строк.
        conn.execute("ALTER TABLE signals ADD COLUMN target REAL")
        conn.commit()
    if _schema_version(conn) == "5":
        # 5 → 6: та же дешёвая правка на месте. Колонка NULLABLE осознанно — у прежних
        # строк правила входа НЕТ, и выдумать его нельзя: `active` не говорит, касалась
        # ли цена. NULL здесь читается как «не записано», а не как «лимитки»; первый же
        # прогон перепишет строку настоящим значением.
        conn.execute("ALTER TABLE levels ADD COLUMN entry_rule TEXT")
        conn.commit()
    if _schema_version(conn) == "6":
        # 6 → 7: колонка на месте, NULLABLE. Момент события у прежних строк неизвестен,
        # и подставить `retired_at` вместо него нельзя — это разные величины, ровно из-за
        # смешения которых колонка и заводится.
        conn.execute("ALTER TABLE levels ADD COLUMN resolved_at INTEGER")
        conn.commit()
    if _schema_version(conn) == "7":
        # 7 → 8: та же дешёвая правка на месте, NULLABLE. Плотность у прежних строк не
        # вычислялась — ноль был бы враньём (он означает «в зоне пусто»), а `volume`
        # подставить нельзя: другая величина. NULL читается как «не считалось».
        conn.execute("ALTER TABLE levels ADD COLUMN vrvp_density REAL")
        conn.commit()
    if _schema_version(conn) == "8":
        _migrate_8_to_9(conn)
    if _schema_version(conn) == "9":
        # 9 → 10: дешёвая правка на месте, NULLABLE. У прежних сигналов правила
        # безубытка не считалось — NULL и означает «не считалось», а не «нет правила».
        conn.execute("ALTER TABLE signals ADD COLUMN breakeven_at REAL")
        conn.commit()
    if _schema_version(conn) == "10":
        # 10 → 11: NULLABLE на месте. У прежних строк вопрос не задавался — NULL и
        # означает «не спрашивали», а не «слома не было».
        conn.execute("ALTER TABLE levels ADD COLUMN mtf_break INTEGER")
        conn.commit()
    if _schema_version(conn) == "11":
        # 11 → 12: NULLABLE на месте. У прежних строк согласие не считалось — NULL
        # означает «не спрашивали», а не «согласия нет».
        conn.execute("ALTER TABLE levels ADD COLUMN agreement TEXT")
        conn.commit()
    if _schema_version(conn) == "12":
        # 12 → 13: ЦЕНА СТОПА, посчитанная расчётом. NULLABLE на месте: у прежних строк
        # её не спрашивали.
        #
        # ⚠ ЗАЧЕМ. До этой колонки бот считал стоп САМ — по простой формуле «граница ±
        # запас», — тогда как карточка брала его из `geometry.build_setup`, где действует
        # ещё и ЯКОРЬ (стр. 18: «стоп всегда ставится за этот прокол» и «идеально стоп
        # прятать за них»). Замер 2026-08-20 на 68 сделках: якорь решает в 36 случаях из
        # 68 и уводит стоп ДАЛЬШЕ пола на 1.52% медианно, до 2.06%. То есть по одному и
        # тому же уровню два рта проекта называли РАЗНЫЕ цены стопа.
        #
        # Это ровно то, что запрещает раздел «прибор обязан смотреть на ТУ ЖЕ величину,
        # которую видит владелец»: величина считается ОДИН раз, записывается и печатается
        # обоими.
        conn.execute("ALTER TABLE levels ADD COLUMN stop_price REAL")
        conn.commit()
    if _schema_version(conn) == "13":
        # 13 → 14: ЧЕЙ приоритет и на скольких экстремумах он держится. NULLABLE на месте.
        #
        # ⚠ ЗАЧЕМ. Уведомление печатало «⚠ ПРОТИВ старшего ТФ» и молчало о том, ЧЬЕГО
        # старшего и насколько тот тренд обоснован, — а карточка того же прогона писала
        # «держится на N экстремумах». Опять одна величина в двух местах, и бедным
        # оказалось уведомление.
        #
        # Числа, ради которых это нужно (15 символов, замер 2026-08-20): приоритет берётся
        # с НЕДЕЛЬНОГО ТФ в 32 случаях из 50 (64%), и держится он на ДВУХ экстремумах у
        # 28% — то есть на минимуме, который вообще допускает стр. 12 («каждый следующий
        # ЛОЙ выше предыдущего»). Разница между «тренд из двух точек» и «из пяти» для
        # читателя решающая, и сам модуль `swings.Trend` заведён с оговоркой: «сообщается
        # ЗАМЕРЕННАЯ глубина, читающий видит, тренд это из двух точек или из десяти».
        conn.execute("ALTER TABLE levels ADD COLUMN priority_tf TEXT")
        conn.execute("ALTER TABLE levels ADD COLUMN priority_depth INTEGER")
        conn.commit()
    if _schema_version(conn) == "14":
        # 14 → 15: ТРЕТЬЕ СОСТОЯНИЕ УРОВНЯ — `stale_calc`.
        #
        # ⚠ ЭТО ЕДИНСТВЕННАЯ МИГРАЦИЯ, КОТОРУЮ НЕЛЬЗЯ СДЕЛАТЬ `ALTER TABLE ADD COLUMN`.
        # Меняется не набор колонок, а ОГРАНИЧЕНИЕ `CHECK` на `state`, а SQLite менять
        # ограничения не умеет — только пересоздать таблицу и перелить строки. Поэтому
        # ниже полный цикл, и порядок в нём не декоративный: `PRAGMA foreign_keys` у
        # этой базы не включён, но переименование делается ПОСЛЕ копирования и внутри
        # одной транзакции, чтобы обрыв не оставил базу с половиной строк.
        #
        # ⚠ ЗАЧЕМ. Замер живого леджера 2026-08-21: активных уровней вселенной 1203, из
        # них пересчитано свежим прогоном 391, а 812 (67.5%) — нет, и судило их
        # `resolve_carried`, правило СЛАБЕЕ обычного (стр. 43 и стр. 25 без глубины
        # захода и без ПОК-профиля). Разбор BTC показал причину: у ВСЕХ 38 отставших
        # строк структуры с таким окном нет в свежем разборе, при этом 37 из 38 лежат
        # ВНУТРИ собранного ряда. То есть дело не в глубине ряда и не в отказе профиля —
        # изменился САМ РАЗБОР, и прежняя карта осиротела.
        #
        # Существующие строки переносятся КАК ЕСТЬ: ни одна не переписывается в
        # `stale_calc` этой миграцией. Задним числом объявлять, каким расчётом была
        # построена запись прошлой недели, значит выдумывать — новое состояние
        # проставляет только свежий прогон, которому есть с чем сравнить.
        # Определение новой таблицы берётся ИЗ `SCHEMA`, а не пишется здесь второй
        # копией: две копии определения — тот самый дефект «одна величина в двух
        # местах», из-за которого этот леджер уже разъезжался (текст схемы против
        # лестницы миграций, см. комментарий у `stop_price`).
        head = "CREATE TABLE IF NOT EXISTS levels ("
        start = SCHEMA.index(head)
        tail = chr(10) + ");"
        end = SCHEMA.index(tail, start) + len(tail)
        create_v15 = SCHEMA[start:end].replace(head, "CREATE TABLE levels_v15 (", 1)
        # Колонки перечисляются ПОИМЁННО и берутся у ЖИВОЙ таблицы. `SELECT *` здесь
        # был бы тихой порчей данных: у базы, выросшей миграциями, порядок колонок
        # (…, resolved_at, vrvp_density, mtf_break, agreement, …) НЕ совпадает с
        # порядком в тексте схемы (…, mtf_break, agreement, resolved_at, vrvp_density),
        # и позиционная вставка разложила бы значения по чужим полям.
        live = [r[1] for r in conn.execute("PRAGMA table_info(levels)")]
        names = ", ".join(live)
        conn.execute("BEGIN")
        try:
            conn.execute(create_v15)
            conn.execute(f"INSERT INTO levels_v15 ({names}) SELECT {names} FROM levels")
            moved = conn.execute("SELECT COUNT(*) FROM levels_v15").fetchone()[0]
            had = conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0]
            if moved != had:
                raise RuntimeError(
                    f"миграция 14→15: перелито {moved} строк из {had} — таблица НЕ "
                    f"заменена, база осталась прежней")
            conn.execute("DROP TABLE levels")
            conn.execute("ALTER TABLE levels_v15 RENAME TO levels")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.commit()
    if _schema_version(conn) == "15":
        # 15 → 16: ЗОНА ВНУТРИ СТРУКТУРЫ — ограничение схемы, а не только гейта.
        #
        # ⚠ Как и 14→15, `ALTER TABLE` здесь бессилен: меняется `CHECK`, а SQLite их на
        # месте не правит. Определение новой таблицы берётся ИЗ `SCHEMA` по той же
        # причине, что и там: вторая копия определения — тот самый дефект, из-за
        # которого текст схемы уже разъезжался с лестницей миграций.
        #
        # ⚠⚠ СТРОКИ, НАРУШАЮЩИЕ ИНВАРИАНТ, УДАЛЯЮТСЯ, И ИХ ЧИСЛО НАЗЫВАЕТСЯ. Молча
        # перелить их нельзя — новая таблица их не примет; молча упасть тоже нельзя —
        # база осталась бы немигрированной без объяснения. Довод за удаление принадлежит
        # владельцу дословно (2026-08-18): «ЕСЛИ зона выходит за структуру то СТРУКТУРА
        # ОПРЕДЛЕННА НЕ ВЕРНО». Такой уровень построен расчётом, признанным неверным, и
        # держать его в карте значит и дальше показывать его боту. До слияния границ
        # (2026-08-18) таких уровней было 51.7% — на базе тех времён удаление будет
        # массовым, и потому оно ГРОМКОЕ, а не тихое.
        bad = conn.execute(
            "SELECT COUNT(*) FROM levels"
            " WHERE NOT (boundary_lo <= zone_lo AND zone_hi <= boundary_hi)"
        ).fetchone()[0]
        head = "CREATE TABLE IF NOT EXISTS levels ("
        start = SCHEMA.index(head)
        tail = chr(10) + ");"
        end = SCHEMA.index(tail, start) + len(tail)
        create_v16 = SCHEMA[start:end].replace(head, "CREATE TABLE levels_v16 (", 1)
        live = [r[1] for r in conn.execute("PRAGMA table_info(levels)")]
        names = ", ".join(live)
        conn.execute("BEGIN")
        try:
            conn.execute(create_v16)
            conn.execute(
                f"INSERT INTO levels_v16 ({names}) SELECT {names} FROM levels"
                f" WHERE boundary_lo <= zone_lo AND zone_hi <= boundary_hi")
            moved = conn.execute("SELECT COUNT(*) FROM levels_v16").fetchone()[0]
            had = conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0]
            if moved != had - bad:
                raise RuntimeError(
                    f"миграция 15→16: перелито {moved} строк из {had} при {bad} "
                    f"нарушающих — таблица НЕ заменена, база осталась прежней")
            conn.execute("DROP TABLE levels")
            conn.execute("ALTER TABLE levels_v16 RENAME TO levels")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.commit()
        if bad:
            log.degraded(
                "миграция 15→16: из карты УДАЛЕНЫ уровни, чья зона выходит за структуру",
                удалено=bad, осталось=moved,
                причина="владелец 2026-08-18: зона вне структуры = структура определена "
                        "неверно")
        else:
            log.info("миграция 15→16: ограничение «зона внутри структуры» добавлено",
                     нарушающих_строк=0, строк=moved)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def init_ledger(path: Path = LEDGER_PATH) -> Path:
    conn = open_production_ledger(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('foundation', ?)",
            ("§10.2",),
        )
        conn.commit()
    finally:
        conn.close()
    return path


class SignalRow(BaseModel):
    """Строка сигнала: её номер и МОМЕНТ, когда система о сигнале узнала.

    `recorded_at` возвращается наружу, потому что от него зависит, по каким барам вообще
    можно считать исход: по тем, что закрылись позже. Взять его «сейчас» у вызывающего
    нельзя — для давно известного сигнала это будет не тот момент.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    recorded_at: int
    fresh: bool
    """True — строку завёл этот вызов. False — сигнал был записан раньше."""


# §10.6 условие 1: «Владелец может проверить состояние леджера тремя заготовленными
# SQL-запросами, не читая код». Вот эти три.
def record_signal(
    conn: sqlite3.Connection, symbol: str, timeframe: str, direction: str,
    opened_at: int, entry: Decimal, stop: Decimal, frames_ref: str, recorded_at: int,
    kind: str = "level", target: Decimal | None = None,
    breakeven_at: Decimal | None = None,
) -> SignalRow | NotReady:
    """Записать сигнал ИЛИ вернуть уже записанный. ЕДИНСТВЕННЫЙ писатель сигналов (§10.2, §6).

    Соединение обязано быть боевым: read-only СУБД отклонит запись сама. Строка,
    нарушающая схему, — названный отказ, а не падение процесса.

    ⚠ Повторный вызов по тому же сигналу больше НЕ отказ. Прежняя редакция возвращала
    на нём `NotReady`, вызывающий писал «сигнал не записан» и шёл дальше — а вместе с
    сигналом пропускался и его ИСХОД. То есть исход мог быть дописан ровно один раз, в
    том прогоне, где сигнал появился, и считался он тогда по уже известной истории.
    Именно из этой пары и получался бэктест под именем журнала.
    """
    key = (kind, symbol, timeframe, direction, opened_at)
    row = conn.execute(
        "SELECT id, recorded_at FROM signals WHERE kind=? AND symbol=? AND timeframe=?"
        " AND direction=? AND opened_at=?", key,
    ).fetchone()
    if row is not None:
        return SignalRow(id=int(row[0]), recorded_at=int(row[1]), fresh=False)
    try:
        cur = conn.execute(
            "INSERT INTO signals (kind, symbol, timeframe, direction, opened_at,"
            " recorded_at, entry, stop, target, breakeven_at, frames_ref)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, symbol, timeframe, direction, opened_at, recorded_at,
             float(entry), float(stop),
             None if target is None else float(target),
             None if breakeven_at is None else float(breakeven_at), frames_ref),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"{symbol} {opened_at}: строка отклонена схемой — {e}")
    if cur.lastrowid is None:
        return NotReady(reason=f"{symbol} {opened_at}: СУБД не вернула идентификатор строки")
    return SignalRow(id=cur.lastrowid, recorded_at=recorded_at, fresh=True)


def record_outcome(
    conn: sqlite3.Connection, signal_id: int, kind: str, closed_at: int,
    exit_price: Decimal | None, r: float | None,
) -> NotReady | None:
    """Записать исход. Открытые и несостоявшиеся сделки сюда НЕ пишутся (§4.3).

    ⚠ Исход — СОБЫТИЕ, и оно необратимо (в отличие от состояния `signal_states`).
    До 2026-08-18 здесь стоял `INSERT OR REPLACE`: прогон с изменённым расчётом мог
    МОЛЧА переписать уже записанный исход другим — журнал перестал бы быть журналом.
    Теперь повторная запись того же исхода — тихий успех (дорешивание идемпотентно),
    а попытка записать ДРУГОЙ исход — названный отказ, не перезапись.
    """
    px = None if exit_price is None else float(exit_price)
    prev = conn.execute(
        "SELECT kind, closed_at, exit_price, r FROM outcomes WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()
    if prev is not None:
        if prev[0] == kind and int(prev[1]) == closed_at and prev[2] == px and prev[3] == r:
            return None
        return NotReady(reason=(
            f"исход {signal_id} уже записан ({prev[0]} at {prev[1]}, r={prev[3]}) и "
            f"отличается от нового ({kind} at {closed_at}, r={r}) — перезапись исхода "
            f"запрещена, расхождение требует разбора"))
    try:
        conn.execute(
            "INSERT INTO outcomes (signal_id, kind, closed_at, exit_price, r)"
            " VALUES (?, ?, ?, ?, ?)",
            (signal_id, kind, closed_at, px, r),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"исход {signal_id}: строка отклонена схемой — {e}")
    return None


class PendingSignal(BaseModel):
    """Сигнал БЕЗ исхода, который надо дорешать по барам. Схема v5, 2026-08-10.

    Заведён вместе с проходом дорешивания: до него исход считался только у сигналов,
    эмитируемых заново, и две трети журнала не получали ответа никогда (см.
    `SCHEMA_VERSION`, переход 4 → 5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    symbol: str
    timeframe: str
    direction: str
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    breakeven_at: Decimal | None = None
    """Цена взведения безубытка (стр. 19, 15, 44). None — правила у сделки нет либо
    строка записана до схемы 10. ⚠ Целью НЕ является: цель закрывает сделку, а это
    условие переносит стоп в ТВХ и сделку продолжает."""
    """`None` — сигнал записан до схемы v5: цель не сохранялась. Такой не дорешать, и
    считать его «без объяснения» нельзя — причина известна и названа."""

    recorded_at: int


def pending_signals(
    conn: sqlite3.Connection, symbol: str | None = None
) -> tuple[PendingSignal, ...]:
    """Сигналы без исхода — все либо по одному символу. Только чтение.

    Состояние (`not_filled`/`open`) наличие здесь НЕ отменяет: оно временное и на
    следующих барах может смениться исходом. Отменяет только сам исход.
    """
    sql = ("SELECT s.id, s.symbol, s.timeframe, s.direction, s.entry, s.stop,"
           " s.target, s.recorded_at, s.breakeven_at FROM signals s"
           " LEFT JOIN outcomes o ON o.signal_id = s.id"
           " WHERE o.signal_id IS NULL")
    args: tuple[str, ...] = ()
    if symbol is not None:
        sql += " AND s.symbol = ?"
        args = (symbol,)
    rows = conn.execute(sql + " ORDER BY s.id", args).fetchall()
    return tuple(
        PendingSignal(
            id=int(r[0]), symbol=r[1], timeframe=r[2], direction=r[3],
            entry=Decimal(str(r[4])), stop=Decimal(str(r[5])),
            target=None if r[6] is None else Decimal(str(r[6])),
            recorded_at=int(r[7]),
            breakeven_at=None if r[8] is None else Decimal(str(r[8])),
        )
        for r in rows
    )


def record_signal_state(
    conn: sqlite3.Connection, signal_id: int, state: str, as_of: int
) -> NotReady | None:
    """Записать СОСТОЯНИЕ незакрытой сделки: `not_filled` или `open` (v4, 2026-08-10).

    Перезаписывается каждым прогоном — состояние сегодняшнее, а не событие. Сделка,
    ставшая исходом, состояние теряет: `outcomes` старше по определению, и держать оба
    ответа значило бы завести две правды об одной сделке.

    Зачем это в леджере, а не в отчёте прогона. `not_filled` — ЗАКОННЫЙ ответ системы
    (цена до входа не дошла; вход стоит лимитками на ПОК и зону, стр. 30), и на вопрос
    "сколько раз совет не сработал" он отвечает иначе, чем `stop`. Пока ответ жил
    счётчиком одного прогона, клетка сводки "сигналов 6, исходов 0" была неотличима от
    дефекта расчёта.
    """
    try:
        conn.execute(
            "INSERT OR REPLACE INTO signal_states (signal_id, state, as_of)"
            " VALUES (?, ?, ?)", (signal_id, state, as_of),
        )
        conn.execute("DELETE FROM signal_states WHERE signal_id IN"
                     " (SELECT signal_id FROM outcomes)")
        conn.commit()
    except sqlite3.IntegrityError as e:
        return NotReady(reason=f"состояние {signal_id}: строка отклонена схемой — {e}")
    return None


class MapSync(BaseModel):
    """Итог слияния карты. Отказы — ЧИСЛО и причина, а не исключение наверх.

    До 2026-08-04 `sync_levels` не был обёрнут ни во что, в отличие от `record_signal` и
    `record_outcome`, и `sqlite3.IntegrityError` из него валил ВЕСЬ прогон — уже после
    сбора данных, бэкфилла и печати карточек. Вернуть отказ числом дешевле, чем потерять
    прогон, и §4.3 требует именно этого: пропуск виден, а не замалчивается и не взрывается.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    added: int
    updated: int
    retired: int
    rejected: tuple[str, ...]


def sync_levels(
    conn: sqlite3.Connection,
    symbol: str,
    seen: list[tuple[Level, LevelState, EntryRule, int | None, int | None, str,
                     float | None, str | None, int | None]],
    now_ms: int,
) -> MapSync:
    """Слить свежепосчитанную карту с накопленной.

    Зачем накопление, а не пересборка каждый прогон — ЗАМЕР, а не удобство.
    Замер `map-drift` (зонд удалён 19.08): при сдвиге окна на 200 баров 2% карты
    BTC и 3% ETH
    исчезали не потому, что уровень отработан (стр. 25 — единственная причина, которую
    знает курс), а потому что структура уехала за край окна в 1000 баров. Уровень при
    этом оставался активным. Автор говорит ровно обратное: «зона остаётся актуальна».

    Что делает функция:
      * НОВЫЙ уровень (окна структуры ещё нет в таблице) — вставляется;
      * ВИДИМЫЙ снова — обновляется `last_seen` и состояние;
      * ПРОПАВШИЙ из расчёта — НЕ трогается: он остаётся в карте с прежним состоянием.
        Именно это и есть накопление; отсутствие в текущем окне не является событием.

    Снятие происходит ТОЛЬКО по состоянию из курса — `worked_off` или `flipped`, — и
    тогда проставляется `retired_at`. Ни одна строка не удаляется: история карты нужна,
    чтобы можно было спросить, когда уровень появился и когда перестал торговаться.

    ⚠ `retired_at` СНИМАЕТСЯ при возврате состояния в `active`. Прежняя редакция писала
    `COALESCE(retired_at, ?)`, то есть однажды проставленную дату не убирала никогда, —
    и такой `UPDATE` падал на `CHECK ((retired_at IS NULL) = (state = 'active'))`,
    роняя прогон целиком. Механизм, которым возврат в `active` объяснялся в первом
    разборе («бар прокола ушёл за левый край окна»), замером ОПРОВЕРГНУТ: 158 общих окон,
    возвратов ноль. Остающийся правдоподобный путь — сдвиг ПОК при доливке архива, из-за
    которого событие считается по новой цене. Он не замерен, и именно поэтому строка
    чинится: падать на неизученном пути хуже, чем пережить его и сообщить числом.
    """
    added = updated = retired = 0
    rejected: list[str] = []
    for lvl, state, rule, resolved, mtf, agree, stop_px, pr_tf, pr_depth in seen:
        key = (symbol, lvl.timeframe, lvl.structure_from_ms, lvl.structure_to_ms)
        row = conn.execute(
            "SELECT state, retired_at FROM levels WHERE symbol=? AND timeframe=? AND"
            " from_ms=? AND to_ms=?", key,
        ).fetchone()
        active = state is LevelState.ACTIVE
        try:
            if row is None:
                conn.execute(
                    "INSERT INTO levels (symbol, timeframe, side, price, zone_lo, zone_hi,"
                    " boundary_lo, boundary_hi, volume, from_ms, to_ms, first_seen,"
                    " last_seen, state, retired_at, entry_rule, resolved_at,"
                    " vrvp_density, mtf_break, agreement, stop_price, priority_tf,"
                    " priority_depth)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (symbol, lvl.timeframe, lvl.side.value, float(lvl.price),
                     float(lvl.zone_lo), float(lvl.zone_hi), float(lvl.boundary_lo),
                     float(lvl.boundary_hi), lvl.structure_volume, lvl.structure_from_ms,
                     lvl.structure_to_ms, now_ms, now_ms, state.value,
                     None if active else now_ms, rule.value, resolved,
                     lvl.vrvp_density, mtf, agree, stop_px, pr_tf, pr_depth),
                )
                added += 1
                retired += not active
                continue
            was_active = row[0] == LevelState.ACTIVE.value
            # Дата снятия либо снимается вовсе (уровень снова активен), либо
            # сохраняет ПЕРВОЕ снятие: «когда перестал торговаться» — это первый раз.
            retired_at = None if active else (row[1] if row[1] is not None else now_ms)
            # ⚠ ГЕОМЕТРИЯ ПЕРЕПИСЫВАЕТСЯ ЦЕЛИКОМ (2026-08-19). Прежняя редакция обновляла
            # `price` и `zone_*`, но НЕ `boundary_*`, `volume` и `side` — они писались
            # ровно раз в жизни строки, при вставке. После слияния границ 2026-08-18 у
            # ранее известных уровней зона стала новой, а структура осталась прежней,
            # узкой: 24884 строки из 49735 (50.0%) держали зону ВНЕ структуры — тот
            # самый дефект №1 владельца, — и сама бы эта строка не зажила никогда.
            #
            # Замер поимённо по полям (ONDO, 1797 общих строк леджера и расчёта):
            # переписываемые `price`/`zone_lo`/`zone_hi` разошлись у 0 — это контроль,
            # он и обязан дать ноль; не переписываемые `boundary_lo` у 1408 (78.4%),
            # `boundary_hi` у 1374 (76.5%), `volume` у 18 (1.0%). У `side` разошлось 0,
            # и он всё равно внесён: величина выводится из ТОГО ЖЕ окна структуры, а
            # устаревшая сторона в карте есть ровно наслоение встречных зон.
            # Воспроизведение: свидетельство E-stale-ledger-geometry-2026-08-19 (удалено 19.08)
            #
            # Правило, из которого это следует, — «прибор обязан смотреть на ТУ ЖЕ
            # величину, которую видит владелец»: карточка того же прогона печатала 0
            # нарушений, пока леджер держал 50%. Две геометрии под одним уровнем.
            conn.execute(
                "UPDATE levels SET last_seen=?, side=?, price=?, zone_lo=?, zone_hi=?,"
                " boundary_lo=?, boundary_hi=?, volume=?, vrvp_density=?, state=?,"
                " retired_at=?, entry_rule=?, resolved_at=?, mtf_break=?,"
                " agreement=?, stop_price=?, priority_tf=?, priority_depth=?"
                " WHERE symbol=? AND timeframe=? AND from_ms=? AND to_ms=?",
                (now_ms, lvl.side.value, float(lvl.price),
                 float(lvl.zone_lo), float(lvl.zone_hi),
                 float(lvl.boundary_lo), float(lvl.boundary_hi), lvl.structure_volume,
                 lvl.vrvp_density,
                 state.value, retired_at, rule.value, resolved, mtf, agree,
                 stop_px, pr_tf, pr_depth, *key),
            )
            updated += 1
            retired += was_active and not active
        except sqlite3.IntegrityError as e:
            rejected.append(f"{symbol} {lvl.timeframe} [{lvl.structure_from_ms}]: {e}")
    conn.commit()
    return MapSync(added=added, updated=updated, retired=retired,
                   rejected=tuple(rejected))


class CarriedLevel(BaseModel):
    """Уровень, перенесённый из прошлого прогона: посчитать заново его не удалось.

    Отдельный тип, а не `Level`: у `Level` есть поля, которых здесь взять неоткуда
    (индексы баров в ряду, которого в этом прогоне нет). Отдать `Level` с выдуманными
    индексами значило бы сфабриковать данные (§4.3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    side: str
    price: Decimal
    """Цена уровня — ПОК. Ею и только ею перенесённый уровень оценивается свежими барами:
    `levels.resolve_carried` спрашивает `breach.first_verdict` НА ЛИНИИ уровня, тот же
    вердикт, что у обычного уровня в `levels.status`.

    ⚠ ЗДЕСЬ БЫЛИ ЕЩЁ ЧЕТЫРЕ ПОЛЯ — `zone_lo`, `zone_hi`, `boundary_lo`, `boundary_hi`, —
    и они СНЯТЫ 2026-08-23 вместе с правкой `resolve_carried`. Их обоснование гласило:
    «коробка нужна, чтобы уровень МОЖНО БЫЛО ОЦЕНИТЬ по свежим барам: пробой за коробку —
    флип (стр. 43), заход в зону — отработка (стр. 25)». После правки оценка идёт по
    линии уровня, коробка и зона в ней не участвуют, и потребителей у полей не осталось
    ни одного (проверено грепом: ни `cl.zone_*`, ни `cl.boundary_*` в `src` больше нет).
    Оставить их значило бы держать четыре поля с ложным обоснованием — тот же дефект, что
    удалённый 2026-08-06 `available()` и снятый 2026-08-05 счётчик `ws_unclosed_violations`."""

    from_ms: int
    to_ms: int
    first_seen: int
    last_seen: int


def carried_levels(
    conn: sqlite3.Connection, symbol: str, now_ms: int
) -> tuple[CarriedLevel, ...]:
    """Активные уровни карты, которых в ЭТОМ прогоне посчитать не удалось.

    «Не удалось» определяется вызывающим по `last_seen`: строки, чей `last_seen` старше
    текущего прогона, — это и есть перенесённые. Возвращаются как есть, без пересчёта:
    ПОК у них старый, и выдавать его за свежий нельзя.
    """
    rows = conn.execute(
        "SELECT timeframe, side, price, from_ms, to_ms, first_seen, last_seen"
        " FROM levels WHERE symbol=? AND state='active' AND last_seen < ?"
        " ORDER BY price", (symbol, now_ms),
    ).fetchall()
    return tuple(
        CarriedLevel(
            timeframe=r[0], side=r[1], price=Decimal(str(r[2])),
            from_ms=r[3], to_ms=r[4], first_seen=r[5], last_seen=r[6],
        )
        for r in rows
    )


def retire_levels(conn: sqlite3.Connection, symbol: str,
                  keys: list[tuple[str, int, int, str]], now_ms: int) -> int:
    """Снять с карты перенесённые уровни, которые свежие бары УЖЕ разрешили.

    ⚠⚠ ЗАВЕДЕНО 2026-08-20 ПО НАЙДЕННОМУ ДЕФЕКТУ, и дефект был крупный. `carried_levels`
    возвращала непересчитанные уровни как есть и НИЧЕГО с ними не делала, поэтому уровень,
    чья структура вышла из рамки построения (`frame_bars`, 180 баров с 2026-08-18),
    больше не оценивался НИКОГДА и оставался `active` вечно.

    Замер на живом леджере 2026-08-20 (отпечаток: 3476 активных уровней, 25 символов):
    у 2317 из них (67%) структура старше 30 суток, в том числе 1142 ПЯТИМИНУТНЫХ. Прямое
    следствие — 4205 пар встречных уровней с ПЕРЕСЕЧЕНИЕМ зон, у многих ПОК совпадает до
    последнего знака. Это ровно то «НАСЛОЕНИЕ противоположных зон», которое владелец
    нашёл глазами и которое не поймал ни один гейт.

    Разобранный пример (ADA, все три активны, ПОК у всех 0.18685):
      5м лонг,  структура 03.08 08:45…12:45
      15м шорт, структура 11.08 22:30…12.08 06:00
      5м шорт,  структура 12.08 09:30…11:15
    Пятиминутный лонг от 3 августа держался живым 17 суток — за это время цена дважды
    построила на том же месте новые структуры.

    `keys` — (timeframe, from_ms, to_ms, state); состояние называет ПРИЧИНУ снятия по
    курсу: `worked_off` — стр. 25, `flipped` — стр. 43. Возвращается число снятых строк.
    """
    n = 0
    for tf, from_ms, to_ms, state in keys:
        cur = conn.execute(
            "UPDATE levels SET state=?, retired_at=? WHERE symbol=? AND timeframe=?"
            " AND from_ms=? AND to_ms=? AND state='active'",
            (state, now_ms, symbol, tf, from_ms, to_ms))
        n += cur.rowcount
    return n


class OutcomeCell(BaseModel):
    """Исходы одной клетки разреза: ТФ × сторона × тип сигнала.

    ⚠ Здесь ЧЕТЫРЕ знаменателя, и все обязательны. Урок backfill-window (2026-08-04):
    сто девяносто честных отказов подряд читались как «рынок такой», пока их не сложили
    ПО ТАЙМФРЕЙМУ — и оказалось, что 4ч и 1Д не дали НИ ОДНОГО уровня. То же самое
    возможно с исходами: «средний R = +0.4» по всему леджеру молчит о том, что весь плюс
    сделан одним ТФ, а другой стабильно минусовой.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    """Тип сигнала: level | pp."""

    timeframe: str
    direction: str

    signals: int
    """Сколько сигналов эмитировано. ГЛАВНЫЙ знаменатель."""

    closed: int
    """Сколько получило исход. `signals - closed` — ещё открытые или не наполненные:
    они в средний R не входят, и без этого числа доля выигрышей посчитана по другому
    множеству, чем «сколько раз система советовала»."""

    by_target: int
    by_stop: int
    by_breakeven: int
    """Вышли в безубыток (стр. 14). Не выигрыш и не проигрыш — третий исход, и без своей
    колонки он растворился бы в `closed`, где `closed != цель + стоп + неоднозначно`
    читалось бы как дефект счёта."""

    ambiguous: int
    """Неоднозначные (стоп и цель в одном баре) — у них `r` нет по схеме, и в сумму R
    они не идут. Считаются отдельно, а не растворяются в «не закрыто»."""

    sum_r: float | None
    avg_r: float | None
    """`None` — закрытых с числовым R нет вовсе. Не 0.0: ноль означал бы «в сумме
    вышли в ноль», а это другое утверждение (§4.3)."""

    not_filled: int
    """Сделок, где цена НЕ ДОШЛА до входа (стр. 30). Законный ответ системы, а не
    отсутствие ответа: исхода у них не будет никогда, и в знаменателе «средний R» им
    не место."""

    still_open: int
    """Сделок, где вход состоялся, а стоп и цель ещё не достигнуты."""

    unknown_state: int
    """Сигналов БЕЗ исхода и БЕЗ записанного состояния. Ровно они и есть подозрение на
    дефект: система про них не сказала ничего. Сюда же попадают сигналы, записанные до
    появления схемы v4, — и это видно по возрасту."""

    no_target: int
    """Сигналов, которые ДОРЕШАТЬ НЕЧЕМ: цель не сохранена (записаны до схемы v5).

    ⚠ Поле заведено 2026-08-11, и это правка прибора, а не данных. Первый же прогон
    после v5 показал: все клетки, которые сводка называла «БЕЗ ОБЪЯСНЕНИЯ», состояли
    ЦЕЛИКОМ из таких сигналов — то есть объяснение было известно, а прибор кричал о
    дефекте. Ложная тревога обесценивает приёмку ровно так же, как ложное «битых 0»
    обесценивает гейт: обе учат не верить показаниям.
    """

    age_bars_max: int
    """Сколько баров СВОЕГО ТФ прожил самый старый сигнал клетки к последнему моменту,
    о котором знает леджер.

    ⚠ Поле заведено вместе со сводкой и ровно затем, чтобы клетку без исходов нельзя
    было объяснить словами. Пустая клетка допускает две причины: «сигналы молоды —
    закрыться не успели» и «исход не считается вовсе». Различает их ТОЛЬКО возраст:
    ноль исходов при возрасте в три бара — данные, ноль исходов при возрасте в двести
    баров — дефект. Правило CLAUDE.md: ноль с красивой причиной опаснее всего, потому
    что не выглядит дефектом.
    """


class OutcomeSurvey(BaseModel):
    """Сводка исходов ПО ИЗМЕРЕНИЯМ, вдоль которых возможен систематический перекос.

    Заведено 2026-08-10. До этого исходы писались и складывались ТОЛЬКО целиком по
    леджеру (`OWNER_QUERIES['результат в R']`) плюс разрез по ТФ без стороны и без типа
    сигнала. Перекос вида «весь плюс сделан лонгами на 1ч, а ПП-сигналы стабильно
    минусовые» не был виден ничем.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: tuple[OutcomeCell, ...]
    signals_total: int
    closed_total: int
    fingerprint: str
    """ОТПЕЧАТОК ДАННЫХ: состав леджера на момент замера. Правило 2026-08-09 — замер по
    растущему хранилищу без отпечатка воспроизводится только до первой доливки."""


def outcome_survey(conn: sqlite3.Connection) -> OutcomeSurvey:
    """Исходы в разрезе тип × ТФ × сторона. Соединение — только на чтение.

    Клетки БЕЗ единого исхода тоже попадают в сводку (`closed = 0`): именно они и есть
    признак перекоса, а фильтрация по `INNER JOIN outcomes` их бы молча съела.
    """
    # «Последний момент, о котором знает леджер» — самая свежая запись. Часы здесь
    # не спрашиваются намеренно: сводка читается и из процессов без сведения с биржей
    # (§6 запрещает судить о времени по локальным часам), а для возраста в барах
    # достаточно внутренней шкалы самого леджера.
    horizon = conn.execute(
        "SELECT MAX(m) FROM (SELECT MAX(recorded_at) AS m FROM signals"
        " UNION ALL SELECT MAX(closed_at) FROM outcomes)"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT s.kind, s.timeframe, s.direction, COUNT(*) AS signals,"
        " SUM(CASE WHEN o.signal_id IS NOT NULL THEN 1 ELSE 0 END) AS closed,"
        " SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END) AS by_target,"
        " SUM(CASE WHEN o.kind='stop' THEN 1 ELSE 0 END) AS by_stop,"
        " SUM(CASE WHEN o.kind='ambiguous' THEN 1 ELSE 0 END) AS ambiguous,"
        " SUM(o.r) AS sum_r, AVG(o.r) AS avg_r, MIN(s.recorded_at) AS oldest,"
        " SUM(CASE WHEN st.state='not_filled' THEN 1 ELSE 0 END) AS not_filled,"
        " SUM(CASE WHEN st.state='open' THEN 1 ELSE 0 END) AS still_open,"
        " SUM(CASE WHEN o.signal_id IS NULL AND st.signal_id IS NULL"
        "          AND s.target IS NULL THEN 1 ELSE 0 END) AS no_target,"
        " SUM(CASE WHEN o.kind='breakeven' THEN 1 ELSE 0 END) AS by_breakeven"
        " FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id"
        " LEFT JOIN signal_states st ON st.signal_id = s.id"
        " GROUP BY s.kind, s.timeframe, s.direction"
        " ORDER BY s.kind, s.timeframe, s.direction"
    ).fetchall()
    cells = tuple(
        OutcomeCell(
            kind=r[0], timeframe=r[1], direction=r[2], signals=int(r[3]),
            closed=int(r[4]), by_target=int(r[5]), by_stop=int(r[6]),
            by_breakeven=int(r[14]), ambiguous=int(r[7]),
            sum_r=None if r[8] is None else float(r[8]),
            avg_r=None if r[9] is None else float(r[9]),
            not_filled=int(r[11]), still_open=int(r[12]), no_target=int(r[13]),
            # «Неизвестно» — это остаток ПОСЛЕ вычета всего, чему причина известна:
            # исхода, состояния и отсутствия цели. Иначе прибор кричал бы о дефекте
            # там, где ответ есть.
            unknown_state=(int(r[3]) - int(r[4]) - int(r[11]) - int(r[12])
                           - int(r[13])),
            age_bars_max=(0 if horizon is None or r[10] is None
                          else max(0, (int(horizon) - int(r[10])) // tf_ms(r[1]))),
        )
        for r in rows
    )
    n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_closed = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    n_levels = conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0]
    return OutcomeSurvey(
        cells=cells, signals_total=int(n_signals), closed_total=int(n_closed),
        fingerprint=f"сигналов {n_signals}, исходов {n_closed}, уровней карты {n_levels}",
    )


def format_outcome_survey(s: OutcomeSurvey) -> list[str]:
    """Сводка словами для владельца (§7.6). Отдаёт строки, печать — дело вызывающего.

    Перекос называется ЯВНО, а не оставляется читателю: клетки без исходов и клетки,
    где все закрытия одного знака, перечисляются отдельной строкой. Один отказ — данные;
    все отказы на одном ТФ — дефект (правило CLAUDE.md).
    """
    out = [f"ОТПЕЧАТОК ДАННЫХ: {s.fingerprint}"]
    if not s.cells:
        out.append("   сигналов нет — сводить нечего (это данные, а не отказ)")
        return out
    out.append(f"   всего сигналов {s.signals_total}, с исходом {s.closed_total}, "
               f"без исхода {s.signals_total - s.closed_total}")
    out.append("   тип   ТФ    сторона  сигн.  закр.  цель  стоп   б/у  неодн.  "
               "средний R  мимо  идёт  б/ц  ?  возраст")
    for c in s.cells:
        avg = "     —" if c.avg_r is None else f"{c.avg_r:6.3f}"
        out.append(f"   {c.kind:5} {c.timeframe:5} {c.direction:8} {c.signals:5} "
                   f"{c.closed:6} {c.by_target:5} {c.by_stop:5} {c.by_breakeven:5} "
                   f"{c.ambiguous:7} {avg}  "
                   f"{c.not_filled:4} {c.still_open:5} {c.no_target:4} "
                   f"{c.unknown_state:2} {c.age_bars_max:8}")
    out.append("   «б/у» — вышли в безубыток, R = 0 (стр. 14); "
               "«мимо» — цена не дошла до входа (стр. 30); «идёт» — вход был, "
               "исхода ещё нет; «б/ц» — цель не сохранена, дорешать нечем (до v5); "
               "«?» — система не сказала ничего")

    silent = [c for c in s.cells if c.closed == 0]
    if silent:
        out.append(f"   ⚠ клеток БЕЗ единого исхода: {len(silent)} — "
                   + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction} ({c.signals} сигн.,"
                               f" {c.age_bars_max} бар.)" for c in silent[:6])
                   + (f" и ещё {len(silent) - 6}" if len(silent) > 6 else ""))
        # Возраст и состояние РАЗЛИЧАЮТ три причины пустоты, и потому названы все три,
        # а не одна правдоподобная. Порог — один бар: раньше него исход невозможен по
        # построению (`outcome.resolve` смотрит только бары ПОСЛЕ `recorded_at`).
        young = [c for c in silent if c.age_bars_max <= 1]
        explained = [c for c in silent
                     if c.age_bars_max > 1 and c.unknown_state == 0]
        aged = [c for c in silent if c.age_bars_max > 1 and c.unknown_state > 0]
        if young:
            out.append(f"     МОЛОДЫЕ (≤1 бара своего ТФ): {len(young)} — исход "
                       "невозможен по построению, это данные")
        if explained:
            legacy = sum(c.no_target for c in explained)
            out.append(f"     ОБЪЯСНЁННЫЕ: {len(explained)} — "
                       + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                                   f" (мимо {c.not_filled}, идёт {c.still_open},"
                                   f" б/ц {c.no_target})"
                                   for c in explained[:6])
                       + "; система ответила, просто ответ не «стоп/цель»"
                       + (f". Из них {legacy} сигналов без сохранённой цели — их не "
                          f"дорешать никогда, это цена журнала, накопленного до v5"
                          if legacy else ""))
        if aged:
            out.append(f"     ⚠ БЕЗ ОБЪЯСНЕНИЯ: {len(aged)} — "
                       + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                                   f" ({c.unknown_state} сигн., {c.age_bars_max} бар.)"
                                   for c in aged[:6])
                       + "; ноль исходов при прожитых барах и без записанного состояния "
                         "объяснения не имеет и требует разбора по шагам "
                         "(backfill-window-2026-08-04)")
    # Знак есть только у цели и стопа: безубыток по определению ни то ни другое (стр. 14),
    # и считать его в пороге значило бы поднимать тревогу на клетке, где всё объяснено.
    one_sided = [c for c in s.cells
                 if c.by_target + c.by_stop >= 3
                 and (c.by_target == 0 or c.by_stop == 0)]
    if one_sided:
        out.append(f"   ⚠ клеток, где ВСЕ закрытия одного знака (≥3): {len(one_sided)} — "
                   + ", ".join(f"{c.kind}/{c.timeframe}/{c.direction}"
                               for c in one_sided[:6]))
    return out


class WinRateCell(BaseModel):
    """Винрейт одного разреза. Мини-курс стр. 9.

    Курс дословно: «Винрейт - англ. win rate - доля успешных/профитных сделок от общего
    числа сделок. Например, винрейт 60% - значит, из 10 сделок 6 принесли профит и
    только 4 - убыток».

    ⚠ ЗНАМЕНАТЕЛЬ здесь — «СДЕЛОК», а не «сигналов», и это не придирка к слову. Сигнал,
    до которого цена не дошла, сделкой не стал вовсе (стр. 30: вход лимитками) — в
    примере курса таких строк нет: там 6 + 4 = 10, то есть знаменатель состоит из
    завершённых сделок. Поэтому `trades` считает ровно исходы, у которых сделка была И
    завершилась: цель, стоп, безубыток. Неоднозначные (стоп и цель в одном баре) в
    знаменатель НЕ идут — у них исход не назначен по построению, и отнести их к любой
    из двух частей значило бы придумать ответ; их число печатается рядом, чтобы
    подвыборка была названа (правило CLAUDE.md о подвыборке).

    ⚠ Безубыток В ЗНАМЕНАТЕЛЕ, но НЕ в числителе: сделка состоялась и завершилась, но
    успешной и профитной курс её не называет — стр. 14 «закрыть позицию в ноль».
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str
    """ТФ, либо `ВСЕ` у итоговой строки."""

    trades: int
    """ЗНАМЕНАТЕЛЬ: завершённых сделок (цель + стоп + безубыток)."""

    wins: int
    """ЧИСЛИТЕЛЬ: сделок, закрытых по цели."""

    losses: int
    breakeven: int
    ambiguous: int
    """Вне знаменателя: исход не назначен, порядок касаний из OHLC не следует."""

    not_filled: int
    """Вне знаменателя: сделки не было — цена не дошла до входа (стр. 30)."""

    still_open: int
    """Вне знаменателя: сделка идёт, исход ещё не наступил."""

    win_rate_pct: float | None
    """`None` при `trades == 0`. Доли без знаменателя не бывает, и ноль вместо неё
    означал бы «ни одна сделка не была прибыльной» — другое утверждение."""


class WinRate(BaseModel):
    """Винрейт целиком и в разрезе по ТФ (стр. 9 + стр. 48).

    Разрез по ТФ обязателен не для красоты: стр. 48 делает о нём прямое утверждение —
    «Чем старше ТФ – тем выше винрейт, но дольше отработка». Один общий процент это
    утверждение проверить не даёт и, хуже, СКРЫВАЕТ перекос: тот же класс дефекта, что
    нашёлся 2026-08-04, когда сто девяносто честных отказов подряд оказались собраны на
    одном таймфрейме.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: WinRateCell
    by_timeframe: tuple[WinRateCell, ...]
    fingerprint: str
    """ОТПЕЧАТОК ДАННЫХ: состав леджера на момент замера (правило 2026-08-09)."""


def win_rate(conn: sqlite3.Connection) -> WinRate:
    """Винрейт по стр. 9. Соединение — только на чтение.

    ТФ берутся из `signals`, а не из `outcomes`: ТФ без единой завершённой сделки обязан
    попасть в разрез со своим `trades = 0` и `win_rate_pct = None`, иначе перекос
    «на 1Д не закрылось ничего» будет молча съеден соединением.
    """
    rows = conn.execute(
        "SELECT s.timeframe,"
        " SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END) AS wins,"
        " SUM(CASE WHEN o.kind='stop' THEN 1 ELSE 0 END) AS losses,"
        " SUM(CASE WHEN o.kind='breakeven' THEN 1 ELSE 0 END) AS be,"
        " SUM(CASE WHEN o.kind='ambiguous' THEN 1 ELSE 0 END) AS amb,"
        " SUM(CASE WHEN st.state='not_filled' THEN 1 ELSE 0 END) AS not_filled,"
        " SUM(CASE WHEN st.state='open' THEN 1 ELSE 0 END) AS still_open"
        " FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id"
        " LEFT JOIN signal_states st ON st.signal_id = s.id"
        " GROUP BY s.timeframe ORDER BY s.timeframe"
    ).fetchall()

    def cell(tf: str, wins: int, losses: int, be: int, amb: int,
             not_filled: int, still_open: int) -> WinRateCell:
        trades = wins + losses + be
        return WinRateCell(
            timeframe=tf, trades=trades, wins=wins, losses=losses, breakeven=be,
            ambiguous=amb, not_filled=not_filled, still_open=still_open,
            win_rate_pct=None if trades == 0 else wins / trades * 100,
        )

    cells = tuple(cell(r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]),
                       int(r[5]), int(r[6])) for r in rows)
    total = cell("ВСЕ", sum(c.wins for c in cells), sum(c.losses for c in cells),
                 sum(c.breakeven for c in cells), sum(c.ambiguous for c in cells),
                 sum(c.not_filled for c in cells), sum(c.still_open for c in cells))
    n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_closed = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    return WinRate(
        total=total, by_timeframe=cells,
        fingerprint=f"сигналов {n_signals}, исходов {n_closed}",
    )


def format_win_rate(w: WinRate) -> list[str]:
    """Винрейт словами для владельца (§7.5). Доля НЕ печатается без знаменателя.

    Строка «винрейт 60%» сама по себе не проверяема; строка «6 из 10» проверяема, и
    именно в таком виде число называет курс на стр. 9.
    """
    out = [f"ВИНРЕЙТ (стр. 9) — доля сделок по цели. ОТПЕЧАТОК: {w.fingerprint}"]
    out.append("   ТФ     сделок  по цели  по стопу  б/у   винрейт   "
               "вне счёта: неодн.  мимо  идёт")
    for c in (w.total, *w.by_timeframe):
        share = ("      —" if c.win_rate_pct is None
                 else f"{c.wins} из {c.trades} = {c.win_rate_pct:.1f}%")
        out.append(f"   {c.timeframe:6} {c.trades:6} {c.wins:8} {c.losses:9} "
                   f"{c.breakeven:4}   {share:18} {c.ambiguous:5} {c.not_filled:5} "
                   f"{c.still_open:5}")
    if w.total.trades == 0:
        out.append("   завершённых сделок нет — доли не существует, и это данные, "
                   "а не ноль процентов")
        return out
    out.append("   знаменатель — ЗАВЕРШЁННЫЕ сделки (цель + стоп + б/у). Не вошли: "
               "неоднозначные (исход не назначен), «мимо» (сделки не было, стр. 30), "
               "«идёт» (исход ещё не наступил)")
    # Утверждение стр. 48 проверяемо ровно тогда, когда разрез напечатан, — но
    # проверять его здесь нечем: порядок ТФ по старшинству знает `bars`, а клеток с
    # нулевым знаменателем в сравнении быть не должно. Печатается разрез, вывод — за
    # владельцем и за отдельным замером.
    # Порога «мало сделок» здесь нет: числа для него не даёт ни курс, ни классика, а
    # придуманный порог отсекал бы клетки от глаз владельца. Вместо порога печатается
    # арифметика — на сколько процентных пунктов двигает долю ОДНА сделка. Клетка, где
    # это число сравнимо с самой долей, читателю видна без всякой отсечки.
    out.append("   одна сделка двигает долю на: "
               + ", ".join(f"{c.timeframe} {100 / c.trades:.1f} п.п."
                           for c in w.by_timeframe if c.trades)
               + (f"; по всем {100 / w.total.trades:.1f} п.п."))
    return out


class SymbolFreshness(BaseModel):
    """Свежесть карты ОДНОГО символа: сколько активных уровней и когда их видели.

    ⚠ Порога «протухло» здесь нет умышленно. Ни курс, ни классика числа для него не
    дают, а придуманная отсечка спрятала бы от владельца ровно те строки, ради которых
    сводка и заводится. Печатается ВОЗРАСТ, вывод делает читающий.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    active: int = Field(ge=0)
    last_seen_ms: int = Field(ge=0)
    """Момент последнего среза, в который символ участвовал. 0 — активных строк нет."""

    in_universe: bool
    """Стоит ли символ в `config/universe.toml` СЕЙЧАС."""

    def age_hours(self, as_of_ms: int) -> float | None:
        if self.last_seen_ms <= 0:
            return None
        return (as_of_ms - self.last_seen_ms) / 3_600_000.0


class LevelFreshness(BaseModel):
    """Сводка свежести активной карты по символам — знаменатель для всякой аналитики.

    ⚠⚠ ЗАВЕДЕНО 2026-08-21 ПО НАЙДЕННОМУ ПЕРЕКОСУ, и перекос был крупный: 1422
    `active`-уровня девяти символов, которых во вселенной УЖЕ НЕТ (HYPE, DOGE, BNB, ADA,
    BLESS, BICO, 1000RATS, BTW, BEAT), не обновлялись от 39.5 до 237.4 часа. Их
    геометрия относится к версиям расчёта, которых больше не существует, — у BEAT ПОК
    лежит вне коробки. При этом активных строк ВСЕЙ текущей вселенной было 898, то есть
    больше 60% активной карты принадлежало мёртвым эпохам, и ни одно число леджера об
    этом не говорило.

    Это ровно урок `docs/audit/backfill-window-2026-08-04.md`: один отказ — данные, сто
    отказов вдоль одного измерения — дефект. Здесь измерение — принадлежность символа
    вселенной, и без разреза вдоль него любая сводка считается по подвыборке, которую
    никто не называл (`docs/audit/outcome-survey-2026-08-10.md`).

    ⚠ ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ — снятия таких строк с карты. Состояние уровня в схеме
    отвечает на РЫНОЧНЫЙ вопрос: `worked_off` — стр. 25, `flipped` — стр. 43. "Символ
    выбыл из вселенной" — факт про НАС, а не про рынок; записать его в то же поле
    значило бы завести две сущности под одним именем — дефект, разобранный в CLAUDE.md
    («прибор обязан смотреть на ТУ ЖЕ величину»). Поэтому строки остаются как есть, а
    подвыборка НАЗЫВАЕТСЯ.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[SymbolFreshness, ...]
    as_of_ms: int
    universe_size: int = Field(ge=0)

    @property
    def outside(self) -> tuple[SymbolFreshness, ...]:
        return tuple(r for r in self.rows if not r.in_universe and r.active)

    @property
    def inside_active(self) -> int:
        return sum(r.active for r in self.rows if r.in_universe)

    @property
    def outside_active(self) -> int:
        return sum(r.active for r in self.outside)


def level_freshness(conn: sqlite3.Connection, symbols: tuple[str, ...],
                    as_of_ms: int) -> LevelFreshness:
    """Активные уровни по символам с возрастом последнего среза.

    `symbols` — вселенная НА МОМЕНТ ВЫЗОВА, а не список из базы: вопрос ровно в том,
    расходятся ли они.
    """
    uni = set(symbols)
    rows = [
        SymbolFreshness(symbol=sym, active=int(n), last_seen_ms=int(last or 0),
                        in_universe=sym in uni)
        for sym, n, last in conn.execute(
            "SELECT symbol, COUNT(*), MAX(last_seen) FROM levels"
            " WHERE state='active' GROUP BY symbol")
    ]
    # Символ вселенной БЕЗ активных строк тоже строка сводки: его отсутствие — такой же
    # ответ, как большое число, и молчать о нём значит печатать долю без знаменателя.
    have = {r.symbol for r in rows}
    rows.extend(SymbolFreshness(symbol=sym, active=0, last_seen_ms=0, in_universe=True)
                for sym in symbols if sym not in have)
    rows.sort(key=lambda r: (r.in_universe, -r.active, r.symbol))
    return LevelFreshness(rows=tuple(rows), as_of_ms=as_of_ms,
                          universe_size=len(symbols))


def format_level_freshness(f: LevelFreshness) -> list[str]:
    """Сводка свежести словами для владельца (§7.5)."""
    out = [f"СВЕЖЕСТЬ АКТИВНОЙ КАРТЫ — {len(f.rows)} символов, вселенная {f.universe_size}"]
    if f.outside:
        out.append("   ВНЕ ВСЕЛЕННОЙ (геометрия прежних эпох расчёта, в сводки не годится):")
        for r in f.outside:
            age = r.age_hours(f.as_of_ms)
            when = "среза нет" if age is None else f"срез {age:.1f} ч назад"
            out.append(f"      {r.symbol:<20} {r.active:5} активных, {when}")
        out.append(f"      ИТОГО вне вселенной {f.outside_active} из "
                   f"{f.outside_active + f.inside_active} активных строк")
    else:
        out.append("   вне вселенной активных строк нет")
    # Порога «протухло» нет (см. докстроку SymbolFreshness): печатается РАЗБРОС
    # возрастов по вселенной и самый старый срез поимённо. Читающий видит, идёт ли
    # карта одним прогоном или часть символов отстала, и делает вывод сам.
    aged = sorted((r.age_hours(f.as_of_ms) or 0.0, r.symbol, r.active)
                  for r in f.rows if r.in_universe and r.active)
    if aged:
        out.append(f"   вселенная: срез от {aged[0][0]:.1f} до {aged[-1][0]:.1f} ч назад; "
                   f"старший — {aged[-1][1]} ({aged[-1][2]} активных)")
    empty = [r.symbol for r in f.rows if r.in_universe and not r.active]
    if empty:
        out.append(f"   без активных уровней {len(empty)} из {f.universe_size}: "
                   + ", ".join(s.split("/")[0] for s in empty))
    return out


OWNER_QUERIES: dict[str, str] = {
    "сколько сделок": "SELECT COUNT(*) AS всего FROM signals;",
    "результат в R (стр. 9 курса)": (
        "SELECT (SELECT COUNT(*) FROM signals) AS всего_советов, "
        "COUNT(*) AS из_них_закрыто, "
        "(SELECT COUNT(*) FROM signals) - COUNT(*) AS ещё_без_исхода, "
        # ⚠ Подвыборка называется ЗДЕСЬ, а не в чьей-то голове: 2026-08-10 выяснилось,
        # что «средний R» считался по трети журнала — исход досчитывался лишь у
        # сигналов, чей уровень система ещё отбирает. Три столбца ниже показывают, из
        # чего состоит остаток, чтобы «ещё_без_исхода» не читалось как «скоро закроются».
        "(SELECT COUNT(*) FROM signal_states WHERE state='not_filled') AS мимо_входа, "
        "(SELECT COUNT(*) FROM signal_states WHERE state='open') AS сделка_идёт, "
        "(SELECT COUNT(*) FROM signals WHERE target IS NULL) AS без_цели_не_дорешать, "
        "SUM(CASE WHEN kind='target' THEN 1 ELSE 0 END) AS по_цели, "
        "SUM(CASE WHEN kind='stop' THEN 1 ELSE 0 END) AS по_стопу, "
        "SUM(CASE WHEN kind='breakeven' THEN 1 ELSE 0 END) AS в_безубыток, "
        "SUM(CASE WHEN kind='ambiguous' THEN 1 ELSE 0 END) AS неоднозначно, "
        "ROUND(SUM(COALESCE(r,0)), 3) AS сумма_R, "
        "ROUND(AVG(r), 3) AS средний_R "
        "FROM outcomes;"
    ),
    # ⚠ `всего_советов` стоит ПЕРВЫМ столбцом умышленно. Прежняя редакция печатала
    # «закрыто N, средний_R X» и умалчивала знаменатель: сделки, до которых цена не
    # дошла, и сделки, ещё не закрывшиеся, в таблицу исходов не попадают вовсе. Замер
    # 2026-08-04: половина эмиссий не заполнялась, и владелец видел долю выигрышей,
    # посчитанную по другому множеству, чем «сколько раз система советовала».
    # ⚠ Доля идёт ПОСЛЕ своего знаменателя и рядом с ним, а не вместо него: стр. 9
    # называет винрейт как «6 из 10», и в таком виде его можно перепроверить руками.
    # Разрез по ТФ — не украшение: стр. 48 утверждает «Чем старше ТФ – тем выше
    # винрейт», и один общий процент это утверждение скрывает.
    "винрейт по ТФ (стр. 9)": (
        "SELECT s.timeframe AS тф, "
        "SUM(CASE WHEN o.kind IN ('target','stop','breakeven') THEN 1 ELSE 0 END) "
        "  AS завершённых_сделок, "
        "SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END) AS по_цели, "
        "SUM(CASE WHEN o.kind='stop' THEN 1 ELSE 0 END) AS по_стопу, "
        "SUM(CASE WHEN o.kind='breakeven' THEN 1 ELSE 0 END) AS в_безубыток, "
        "ROUND(100.0 * SUM(CASE WHEN o.kind='target' THEN 1 ELSE 0 END) / "
        "  NULLIF(SUM(CASE WHEN o.kind IN ('target','stop','breakeven') "
        "               THEN 1 ELSE 0 END), 0), 1) AS винрейт_проц, "
        "SUM(CASE WHEN o.kind='ambiguous' THEN 1 ELSE 0 END) AS вне_счёта_неоднозначных, "
        "COUNT(*) AS всего_сигналов_на_этом_тф "
        "FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id "
        "GROUP BY s.timeframe ORDER BY s.timeframe;"
    ),
    "чем куплены выигрыши — геометрия сделок": (
        "SELECT s.timeframe AS тф, COUNT(*) AS сделок, "
        "ROUND(AVG(ABS(s.entry - s.stop) / s.entry * 100), 3) AS средняя_дистанция_стопа_проц, "
        "ROUND(AVG(o.r), 3) AS средний_R, "
        "ROUND(AVG(s.recorded_at - s.opened_at) / 3600000.0, 1) AS часов_от_уровня_до_записи "
        "FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id "
        "GROUP BY s.timeframe ORDER BY сделок DESC;"
    ),
    "какие символы": (
        "SELECT symbol AS символ, COUNT(*) AS сделок, "
        "MIN(opened_at) AS первая, MAX(opened_at) AS последняя "
        "FROM signals GROUP BY symbol ORDER BY сделок DESC;"
    ),
    "подозрительная геометрия": (
        "SELECT id, symbol AS символ, direction AS сторона, entry AS вход, stop AS стоп, "
        "ROUND(ABS(entry - stop) / entry * 100, 4) AS дистанция_стопа_проц "
        "FROM signals "
        "WHERE (direction = 'long'  AND stop >= entry) "
        "   OR (direction = 'short' AND stop <= entry) "
        "   OR ABS(entry - stop) / entry < 0.0005 "
        "   OR ABS(entry - stop) / entry > 0.5 "
        "ORDER BY дистанция_стопа_проц;"
    ),
}
