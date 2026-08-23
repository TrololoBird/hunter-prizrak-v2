"""Детерминированный повтор и дифф карточки. FOUNDATION.md §10.3, §10.6 условие 2.

§10.6 дословно: «Любое изменение расчёта предъявляется как дифф детерминированного повтора
на сохранённых кадрах — на этих сохранённых данных карточка была такой, стала такой. Вы
видите два текста и разницу между ними».

Это единственный инструмент проверки, не требующий чтения кода. Порядок работы:
  1. `hunter run` сохраняет кадры И карточку, порождённую этими кадрами;
  2. расчёт меняется;
  3. `hunter replay <прогон>` заново строит карточку ИЗ ТЕХ ЖЕ кадров и печатает разницу.

Совпадение построчное, а не «похоже»: карточка — чистая функция от кадров (`card.render`),
поэтому любое расхождение означает, что изменился расчёт, а не данные.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel, ConfigDict

from . import card, engine, store
from .models import Bar, NotReady
from .profile_source import TVWindows


class SymbolDiff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    identical: bool
    diff: str
    """Пустая строка при совпадении. Формат — унифицированный дифф."""

    lines_before: int
    lines_after: int


class ReplayResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    symbols: tuple[SymbolDiff, ...]
    skipped: tuple[str, ...]
    """Символы, которые повторить не удалось, с причиной. Молчания здесь быть не должно."""

    @property
    def changed(self) -> tuple[SymbolDiff, ...]:
        return tuple(s for s in self.symbols if not s.identical)


def replay_symbol(run_id: str, dir_name: str) -> SymbolDiff | NotReady:
    """Пересобрать карточку одного символа из кадров и сравнить с сохранённой."""
    meta = store.read_meta(run_id, dir_name)
    if isinstance(meta, NotReady):
        return meta
    symbol, tick, _bucket = meta

    saved = store.read_card(run_id, dir_name)
    if isinstance(saved, NotReady):
        return saved

    tfs = store.saved_timeframes(run_id, dir_name)
    if not tfs:
        return NotReady(reason=f"{symbol}: кадров баров в прогоне нет")
    series = {tf: store.read_bars(run_id, dir_name, tf) for tf in tfs}

    # Источник профиля — ТОТ ЖЕ КЛАСС и ТЕ ЖЕ СВЕЧИ, что у прогона: `TVWindows` над
    # аналитическими рядами из кадров плюс сохранённый профильный ряд (минутки,
    # `profile_bars_*.parquet`). Сначала МАНИФЕСТ (source.json): кадры, снятые кодом
    # без сохранения источника (до 2026-08-18), дают честный отказ, а не осмысленно
    # выглядящий дифф из неполных данных — аудит правки показал это на 45 прогонах
    # диска. Пустой манифест — другое, законное состояние: минуток у прогона не было,
    # повтор воспроизведёт те же отказы профиля.
    #
    # ⚠ До 2026-08-18 здесь строился `archive.WindowSource` по срезу СДЕЛОК — а прогон
    # с 2026-08-17 строит профиль из СВЕЧЕЙ (решение владельца от 2026-08-12), и срез
    # сделок с той же правки никто не писал (`persist_archive` молча пропускал свечные
    # источники). Повтор сравнивал расчёт с другим транспортом и печатал «ИЗМЕНИЛОСЬ
    # 5 из 5» при неизменённом коде: дифф §10.6 — единственная проверка, не требующая
    # чтения кода, — был слеп ко всем правкам расчёта с 2026-08-17. Поймано контролем
    # «неизменённый код обязан дать построчное совпадение».
    manifest = store.read_source_meta(run_id, dir_name)
    if isinstance(manifest, NotReady):
        return manifest
    profile_tfs, analysis_tfs, horizon_days = manifest
    # ⚠⚠ ГОРИЗОНТ ОБЯЗАН ПРИЙТИ ИЗ КАДРОВ, А НЕ ПОДРАЗУМЕВАТЬСЯ НУЛЁМ. ПРАВКА
    # 2026-08-23. Здесь его не было вовсе: `engine.decide` звался без `horizon_days`,
    # то есть повтор считал БЕЗ отсечки старых структур, тогда как боевой прогон
    # считает с ней. Замер на кадрах `rebuild-2026-08-23`: «структура закрылась раньше
    # горизонта» — 367 строк в сохранённой карточке и НОЛЬ в пересчитанной; следом
    # меняется отбор ближайших уровней и окно композита (433 против 213 суток).
    # Повтор печатал «ИЗМЕНИЛОСЬ 4 из 4» при неизменённом коде — то есть проверка
    # §10.6, единственная не требующая чтения кода, была структурно красной на всяком
    # прогоне с горизонтом. Найдено проверкой /verify, а не гейтом.
    #
    # Старые кадры (горизонт не записан) — ОТКАЗ, а не подстановка нуля: посчитать
    # другое и выдать это за сравнение хуже, чем не сравнить вовсе. Тот же выбор уже
    # сделан выше для пропавшего манифеста источника.
    if horizon_days is None:
        return NotReady(reason=(
            f"{symbol}: в манифесте кадров нет горизонта — кадры сняты до 2026-08-23; "
            f"повтор без него считал бы БЕЗ отсечки старых структур и печатал бы "
            f"«расчёт изменился» при неизменном коде"))
    # Манифест сверяется С СОДЕРЖАНИЕМ, а не только с существованием (2026-08-18):
    # до этого состав ТФ повтора выводился из наличия файлов, и пропавший после
    # прогона parquet менял набор молча — дифф из неполных кадров печатал «расчёт
    # изменился» при неизменном коде. Старый манифест без списка аналитических
    # рядов сверку состава пропускает, и это названо формой манифеста, а не молча.
    if analysis_tfs is not None and set(analysis_tfs) != set(tfs):
        return NotReady(reason=(
            f"{symbol}: состав кадров разошёлся с манифестом — записывались "
            f"{sorted(analysis_tfs)}, на диске {sorted(tfs)}; дифф из неполных "
            f"кадров был бы ложным «расчёт изменился»"))
    profile_read = store.read_profile_bars(run_id, dir_name)
    if set(profile_read) != set(profile_tfs):
        return NotReady(reason=(
            f"{symbol}: профильные ряды разошлись с манифестом — записывались "
            f"{sorted(profile_tfs)}, на диске {sorted(profile_read)}"))
    profile_series: dict[str, list[Bar]] = dict(series)
    profile_series.update(profile_read)
    source = TVWindows(symbol, tick, profile_series)

    # Решение строится ЗАНОВО из кадров, а не берётся готовым: §10.6 условие 2 требует
    # независимого пересчёта, иначе повтор сверял бы результат сам с собой.
    rebuilt = card.render(
        engine.decide(symbol, series, source, tfs, horizon_days=horizon_days), series)
    diff = "".join(difflib.unified_diff(
        saved.splitlines(keepends=True), rebuilt.splitlines(keepends=True),
        fromfile=f"{symbol} сохранено", tofile=f"{symbol} пересчитано", n=2,
    ))
    return SymbolDiff(
        symbol=symbol, identical=not diff, diff=diff,
        lines_before=len(saved.splitlines()), lines_after=len(rebuilt.splitlines()),
    )


def replay_run(run_id: str) -> ReplayResult | NotReady:
    dirs = store.saved_symbols(run_id)
    if not dirs:
        return NotReady(reason=f"прогон {run_id} не найден или пуст — "
                               f"{store.FRAMES_DIR / run_id}")
    done: list[SymbolDiff] = []
    skipped: list[str] = []
    for d in dirs:
        # Ошибка одного символа (битый parquet, нечитаемый кадр) не роняет весь
        # повтор: остальные символы всё равно сверяются, а отказ назван (§4.3).
        try:
            r = replay_symbol(run_id, d)
        except Exception as e:  # причина уходит в skipped, не глотается
            skipped.append(f"{d}: повтор упал — {type(e).__name__}: {e}")
            continue
        if isinstance(r, NotReady):
            skipped.append(r.reason)
        else:
            done.append(r)
    return ReplayResult(run_id=run_id, symbols=tuple(done), skipped=tuple(skipped))


def print_result(r: ReplayResult, show_diff: bool) -> int:
    """Печатает вердикт по-русски (§7.5). Код возврата различает исходы:

    0 — все символы повторены, все совпали;
    1 — расчёт изменился (есть построчные расхождения);
    2 — расхождений нет, но часть символов не повторена (проверка неполная).

    До 2026-08-18 возвращалась СУММА len(changed)+len(skipped): «2 пропущено»
    и «2 изменилось» давали одинаковый код, и вызывающий не мог отличить
    неполную проверку от проваленной.
    """
    print()
    print("=" * 78)
    print(f"ПОВТОР ПРОГОНА {r.run_id} — FOUNDATION.md §10.6, условие 2")
    print("=" * 78)
    print(f"\nсимволов повторено: {len(r.symbols)}")
    for s in r.symbols:
        mark = "совпало" if s.identical else "ИЗМЕНИЛОСЬ"
        print(f"  {mark:11} {s.symbol:22} строк {s.lines_before} → {s.lines_after}")
    if r.skipped:
        print(f"\nне повторено: {len(r.skipped)} (§4.3 — причина названа, не скрыта)")
        for why in r.skipped:
            print(f"  {why}")

    changed = r.changed
    print()
    if not r.symbols:
        print("ПЛОХО: не повторён ни один символ — проверка не состоялась")
        print("=" * 78)
        return 2
    if not changed:
        if r.skipped:
            print(f"НЕПОЛНО: {len(r.symbols)} карточек совпали построчно, но "
                  f"{len(r.skipped)} символов не повторено — по ним проверки НЕ БЫЛО.")
        else:
            print(f"ХОРОШО: расчёт не изменился — все {len(r.symbols)} карточек "
                  f"совпали построчно с сохранёнными.")
    else:
        print(f"ВНИМАНИЕ: расчёт изменился у {len(changed)} символов из {len(r.symbols)}.")
        print("Ниже — что было и что стало. Строки со знаком «-» были, со знаком «+» стали.")
        if show_diff:
            for s in changed:
                print()
                print(s.diff, end="")
        else:
            print("(показать разницу: добавьте --diff)")
    print("=" * 78)
    if changed:
        return 1
    return 2 if r.skipped else 0
