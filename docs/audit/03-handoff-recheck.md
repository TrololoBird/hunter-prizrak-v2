# Перепроверка находок прошлого разбора ИСПОЛНЕНИЕМ на текущем HEAD

**Дата:** 2026-08-07. **Ветка:** `audit/2026-08-06`. **HEAD:** `ff56a9e`.
**Что это:** четырнадцать находок из раздела 2
[`docs/audit/HANDOFF-2026-08-05.md`](HANDOFF-2026-08-05.md), числившихся открытыми или
выведенными, проверены запуском кода, а не чтением. Свод разбора собирался на снимке от
2026-08-04 и прямо предупреждал: строки со статусом «выведено» подлежат проверке.

Каждый вердикт снабжён командой и кодом возврата. Для каждого числа выполнен контроль
**«способен ли прибор выдать иной ответ»** — без него ноль ничего не значит (CLAUDE.md).

⚠ **Условие замера.** Во время работы этой проверки в дереве параллельно правились
`gates/formula_reference.py`, `gates/indicator_oracle.py`,
`gates/indicator_availability.py`, `src/hunter/admission.py` и `src/hunter/card.py`
другим процессом. Ни одна из четырнадцати проверок ниже этих файлов не касается.
Собственные правки этой проверки (пробники в `.github/workflows/ci.yml`, в `scripts/` и
в `src/`) восстановлены полностью: `git diff` по ним пуст.

---

## Сводная таблица вердиктов

| ID | суть находки | вердикт на HEAD | чем закрыто / чем подтверждено |
|---|---|---|---|
| **Л-1** | `PRAGMA foreign_keys = 0`, исход на несуществующий сигнал | **ЗАКРЫТА** | store.py::_tune, строка `PRAGMA foreign_keys = ON` |
| **Л-2** | `UNIQUE (symbol, opened_at)` отвергает второй ТФ | **ЗАКРЫТА** | ключ расширен до `(symbol, timeframe, direction, opened_at)` |
| **Л-3** | `COALESCE(retired_at, ?)` + `CHECK` роняет прогон | **ЗАКРЫТА** | `retired_at` снимается; отказ возвращается числом, а не исключением |
| **Л-4** | нет транзакции сигнал+исход, нет WAL, нет `busy_timeout` | **ЗАКРЫТА ЧАСТИЧНО** | WAL и `busy_timeout` есть; **общей транзакции у пары НЕТ** |
| **Б-5** | кэш parquet пишется неатомарно | **ЗАКРЫТА** | archive.py::_write_atomic — временный файл рядом плюс `os.replace` |
| **Д-4** | один битый бар объявляет объяснёнными ВСЕ разрывы | **ЗАКРЫТА** | run.py::explained_gaps — поштучно, по `rejected_at_ms` |
| **Д-8** | блокирующий `urllib` внутри корутины | **ЗАКРЫТА** | run.py::backfill_trades — тело целиком в `asyncio.to_thread` |
| **А-5** | словарь ccxt пересекает границу слоёв | **ПОДТВЕРЖДЕНА НА HEAD** | `list[dict[str, Any]]` жив, `t.get("price")` жив, гейт слеп |
| **Э-2** | гейты не смотрят `scripts/` | **ПОДТВЕРЖДЕНА НА HEAD** | три гейта дали код 0 на заведомом нарушении в `scripts/` |
| **Э-4** | `ci_covers_gates` проверяет подстроку, а не шаг | **ПОДТВЕРЖДЕНА НА HEAD** | закомментированный шаг прошёл, код возврата 0 |
| **Н-10** | `.github/workflows` нет в `SEARCH` гейта `doc_refs` | **ЗАКРЫТА** | каталог добавлен 2026-08-05, ложного минуса нет |
| **Р-4** | `bin_price(idx) = idx*tick` — нижняя граница бина | **ПОДТВЕРЖДЕНА ЧАСТИЧНО** | ПОК на BTC сдвига **не имеет** (0.00); занижен ВЕРХНИЙ край зоны — ровно на бин |
| **Р-5** | структуры строго последовательны, перекрытие невозможно | **НЕ ВОСПРОИЗВОДИТСЯ как заявлено** | перекрытий 278 из 705 пар, но все на 1–2 бара; вложенных 0 из 5080 |
| **Р-6** | тренд считается по всей истории без окна | **ПОДТВЕРЖДЕНА НА HEAD** | окна нет; но `holds_for` докладывается и фактически равен 0–6 |

**Итог: восемь находок уже закрыты (одна — частично), пять подтверждены, одна не
воспроизводится в заявленной форме.**

---

## Как воспроизвести всё разом

```bash
cd /c/Users/Антон/Documents/hunter-v2
export PYTHONIOENCODING=utf-8
uv run python gates/ci_covers_gates.py;   echo "код возврата: $?"
uv run python gates/production_writer.py; echo "код возврата: $?"
uv run python gates/no_silent_failure.py; echo "код возврата: $?"
uv run python gates/public_data_only.py;  echo "код возврата: $?"
uv run python gates/doc_refs.py;          echo "код возврата: $?"
```

Отдельные пробники приведены при каждой находке. Все они работают на **временных**
базах и на **сохранённых кадрах**; `data/ledger.sqlite3` и `data/aggcache` только
читаются.

---

## Л-1. `PRAGMA foreign_keys` — ЗАКРЫТА

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib, sqlite3, tempfile
from hunter import store
db = pathlib.Path(tempfile.mkdtemp()) / "l.sqlite3"
conn = store.open_production_ledger(db)
print("foreign_keys =", conn.execute("PRAGMA foreign_keys").fetchone()[0])
try:
    conn.execute("INSERT INTO outcomes (signal_id,kind,closed_at,exit_price,r)"
                 " VALUES (999999,'stop',1,1.0,-1.0)")
    conn.commit(); print("вставка ПРОШЛА -> находка подтверждена")
except sqlite3.IntegrityError as e:
    print("IntegrityError:", e, "-> находка закрыта")
PY
```

Вывод:

```
foreign_keys = 1
IntegrityError: FOREIGN KEY constraint failed -> находка закрыта
```

Код возврата 0.

**Контроль прибора.** На чистом соединении с явным `PRAGMA foreign_keys = OFF` та же
вставка проходит:

```
после явного OFF: foreign_keys = 0
при OFF вставка ПРОШЛА -> прибор способен выдать иной ответ
вернули ON: 1
при ON IntegrityError: FOREIGN KEY constraint failed
```

Прибор различает два состояния, значит «1» — замер, а не константа. Соединение без
store.py::_tune печатает `foreign_keys = 0`: умолчание SQLite не изменилось, включает
его именно код проекта.

---

## Л-2. `UNIQUE (symbol, opened_at)` — ЗАКРЫТА

Ключ в store.py::SCHEMA расширен до `UNIQUE (symbol, timeframe, direction, opened_at)`.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib, tempfile
from decimal import Decimal
from hunter import store
conn = store.open_production_ledger(pathlib.Path(tempfile.mkdtemp()) / "l.sqlite3")
print("1h  ->", store.record_signal(conn,"BTC/USDT:USDT","1h","long",1735689600000,
                                    Decimal("100"),Decimal("90"),"run",1735689600001))
print("4h  ->", store.record_signal(conn,"BTC/USDT:USDT","4h","short",1735689600000,
                                    Decimal("200"),Decimal("210"),"run",1735689600001))
print("строк:", conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
print("повтор 1h ->", store.record_signal(conn,"BTC/USDT:USDT","1h","long",1735689600000,
                                    Decimal("100"),Decimal("90"),"run",1735689600001))
print("строк:", conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
PY
```

Вывод:

```
1h  -> id=1 recorded_at=1735689600001 fresh=True
4h  -> id=2 recorded_at=1735689600001 fresh=True
строк: 2
повтор 1h -> id=1 recorded_at=1735689600001 fresh=False
строк: 2
```

Код возврата 0. Два таймфрейма с одной меткой записались оба.

**Контроль прибора.** Полный дубль ключа НЕ заводит третьей строки и возвращается с
`fresh=False` — то есть ключ действует и способен отличить совпадение от дубля. Ответ
«записались оба» не является следствием отсутствия ключа.

---

## Л-3. Возврат уровня в `active` — ЗАКРЫТА

store.py::sync_levels больше не пишет `COALESCE(retired_at, ?)`: при возврате в
`active` дата снятия снимается.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib, sqlite3, tempfile
from decimal import Decimal
from hunter import store
from hunter.levels import Level, LevelSide, LevelState
conn = store.open_production_ledger(pathlib.Path(tempfile.mkdtemp()) / "l.sqlite3")
lvl = Level(symbol="BTC/USDT:USDT", timeframe="1h", side=LevelSide.LONG,
    price=Decimal("100"), zone_lo=Decimal("99"), zone_hi=Decimal("101"),
    created_at_index=10, created_at_ms=1735689600000,
    structure_first_index=0, structure_last_index=9,
    structure_from_ms=1735600000000, structure_to_ms=1735680000000,
    stop_anchor=None, structure_volume=123.0,
    boundary_lo=Decimal("98"), boundary_hi=Decimal("102"))
for step, state in ((1_000, LevelState.ACTIVE), (2_000, LevelState.WORKED_OFF),
                    (3_000, LevelState.ACTIVE)):
    s = store.sync_levels(conn, "BTC/USDT:USDT", [(lvl, state)], step)
    print(state.value, "->", s, "| строка:",
          conn.execute("SELECT state, retired_at FROM levels").fetchone())
try:
    conn.execute("UPDATE levels SET state='active', retired_at=2000"); conn.commit()
    print("КОНТРОЛЬ: прошло -> CHECK не работает")
except sqlite3.IntegrityError as e:
    print("КОНТРОЛЬ IntegrityError:", e)
PY
```

Вывод:

```
active -> added=1 updated=0 retired=0 rejected=() | строка: ('active', None)
worked_off -> added=0 updated=1 retired=1 rejected=() | строка: ('worked_off', 2000)
active -> added=0 updated=1 retired=0 rejected=() | строка: ('active', None)
КОНТРОЛЬ IntegrityError: CHECK constraint failed: (retired_at IS NULL) = (state = 'active')
```

Код возврата 0.

**Контроль прибора — двойной.** Во-первых, `CHECK` жив: ручной `UPDATE` со старой
семантикой на нём падает, то есть прежняя редакция упала бы ровно здесь. Во-вторых,
отказ схемы теперь возвращается ЧИСЛОМ, а не исключением: заведомо битый уровень
(`zone_lo` больше цены) даёт

```
added=0 updated=0 retired=0 rejected=('ETHTEST 1h [1735600000000]: CHECK constraint failed: zone_lo <= price AND price <= zone_hi',)
```

Прогон не падает — значит и на неизученном пути возврата в `active` он не упал бы.

---

## Л-4. Транзакция, WAL, `busy_timeout` — ЗАКРЫТА ЧАСТИЧНО

**WAL и `busy_timeout` — есть.** store.py::_tune ставит оба.

```
journal_mode = wal
busy_timeout = 5000
```

Контроль: соединение, открытое `sqlite3.connect` мимо store.py::_tune, печатает
`journal_mode = delete`. Различие в `journal_mode` — замер. ⚠ Различия в
`busy_timeout` **нет**: голое соединение тоже печатает 5000, потому что это умолчание
модуля `sqlite3` в Python. Строка в коде есть и делает значение независимым от
умолчания, но подтвердить её ИСПОЛНЕНИЕМ этим пробником нельзя — числу нечем отличиться.

**Общей транзакции у пары сигнал+исход НЕТ.** store.py::record_signal и
store.py::record_outcome вызывают `conn.commit()` каждый сам, а run.py::record
вызывает их последовательно без обёртки.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib, sqlite3, tempfile
from decimal import Decimal
from hunter import store
tmp = pathlib.Path(tempfile.mkdtemp())
conn = store.open_production_ledger(tmp / "l.sqlite3")
sig = store.record_signal(conn,"BTC/USDT:USDT","1h","long",1735689600000,
                          Decimal("100"),Decimal("90"),"run",1735689600001)
print("in_transaction после record_signal:", conn.in_transaction)
conn.close()                       # имитация гибели процесса до записи исхода
ro = sqlite3.connect(f"file:{(tmp/'l.sqlite3').as_posix()}?mode=ro", uri=True)
print("signals =", ro.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
      "outcomes =", ro.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])
conn2 = store.open_production_ledger(tmp / "l2.sqlite3")
conn2.execute("BEGIN IMMEDIATE")
conn2.execute("INSERT INTO signals (symbol,timeframe,direction,opened_at,recorded_at,"
              "entry,stop,frames_ref) VALUES ('X','1h','long',1,1,100.0,90.0,'r')")
conn2.rollback()
print("КОНТРОЛЬ после BEGIN+ROLLBACK: signals =",
      conn2.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
PY
```

Вывод:

```
in_transaction после record_signal: False
signals = 1 outcomes = 0
КОНТРОЛЬ после BEGIN+ROLLBACK: signals = 0
```

Код возврата 0. Контроль показывает, что откат доступен и просто не используется:
явная транзакция стирает строку, автокоммит — нет.

**Оценка тяжести.** Осиротевший сигнал без исхода неотличим от открытой сделки, но
восстановим: `record_signal` идемпотентен, а исход считается по барам, закрывшимся
позже `recorded_at`, — следующий расчёт допишет его. То есть дефект не теряет данных,
он лишь делает состояние «сигнал есть, исход не посчитан» неотличимым от «исход ещё не
наступил».

---

## Б-5. Атомарность записи parquet-кэша — ЗАКРЫТА

archive.py::_write_atomic пишет во временный файл В ТОМ ЖЕ каталоге и делает
`os.replace`.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib, tempfile
import polars as pl
from hunter import archive
tmpd = pathlib.Path(tempfile.mkdtemp())
frame = pl.DataFrame({"a": [1, 2, 3]})
archive._write_atomic(frame, tmpd / "x.parquet")
print("после нормальной записи:", sorted(p.name for p in tmpd.iterdir()),
      "| строк:", pl.read_parquet(tmpd / "x.parquet").height)
orig = pl.DataFrame.write_parquet
def exploding(self, path, *a, **kw):
    pathlib.Path(path).write_bytes(b"PAR1-oborvano")
    raise OSError("обрыв процесса посреди записи")
pl.DataFrame.write_parquet = exploding
try:
    archive._write_atomic(frame, tmpd / "y.parquet")
except OSError as e:
    print("КОНТРОЛЬ обрыва — поймано:", e)
finally:
    pl.DataFrame.write_parquet = orig
print("целевой y.parquet существует:", (tmpd / "y.parquet").exists())
print("каталог после обрыва:", sorted(p.name for p in tmpd.iterdir()))
PY
```

Вывод:

```
после нормальной записи: ['x.parquet'] | строк: 3
КОНТРОЛЬ обрыва — поймано: обрыв процесса посреди записи
целевой y.parquet существует: False
каталог после обрыва: ['x.parquet']
```

Код возврата 0.

**Контроль прибора.** Пробник ломает запись на середине: временный файл создаётся и
получает мусор, после чего бросается ошибка. Целевой путь при этом НЕ появляется, и
обрезанного `.part` в каталоге не остаётся — блок `finally` его убирает. Прежняя
редакция оставила бы `y.parquet` из тринадцати байт, а `binned_day` возвращал бы его
навсегда.

**Попутно закрыта Б-4** — `tick` вошёл в имя файла кэша:

```
tick=0.10 -> BTCUSDT-2026-08-01-300000-t0p10-b2.parquet
tick=0.01 -> BTCUSDT-2026-08-01-300000-t0p01-b2.parquet
```

---

## Д-4. Объяснённые разрывы — ЗАКРЫТА

run.py::explained_gaps сопоставляет разрывы с отклонёнными барами ПОШТУЧНО: разрыв
`(a, b)` объяснён только тем битым баром, чья метка лежит строго внутри интервала.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
from hunter.models import SeriesState
from hunter.run import explained_gaps
cases = [
    ("2 разрыва, битый бар ВНУТРИ первого", [(10,20),(100,200)], [15]),
    ("2 разрыва, битые бары внутри обоих  ", [(10,20),(100,200)], [15,150]),
    ("2 разрыва, битый бар ВНЕ обоих      ", [(10,20),(100,200)], [500]),
    ("3 разрыва, битых баров нет          ", [(10,20),(30,40),(50,60)], []),
    ("1 разрыв, битый бар на ГРАНИЦЕ      ", [(10,20)], [10]),
    ("5 разрывов, ОДИН битый бар в третьем", [(1,2),(3,4),(10,20),(30,40),(50,60)], [15]),
]
for name, g, rej in cases:
    st = SeriesState(symbol="X", timeframe="5m")
    st.gaps = list(g); st.rejected_at_ms = list(rej)
    print(f"{name} разрывов {len(g)}, битых {len(rej)}"
          f" -> НОВАЯ {len(explained_gaps(st))}, СТАРАЯ объявила бы {len(g) if rej else 0}")
PY
```

Вывод:

```
2 разрыва, битый бар ВНУТРИ первого разрывов 2, битых 1 -> НОВАЯ 1, СТАРАЯ объявила бы 2
2 разрыва, битые бары внутри обоих   разрывов 2, битых 2 -> НОВАЯ 2, СТАРАЯ объявила бы 2
2 разрыва, битый бар ВНЕ обоих       разрывов 2, битых 1 -> НОВАЯ 0, СТАРАЯ объявила бы 2
3 разрыва, битых баров нет           разрывов 3, битых 0 -> НОВАЯ 0, СТАРАЯ объявила бы 0
1 разрыв, битый бар на ГРАНИЦЕ       разрывов 1, битых 1 -> НОВАЯ 0, СТАРАЯ объявила бы 1
5 разрывов, ОДИН битый бар в третьем разрывов 5, битых 1 -> НОВАЯ 1, СТАРАЯ объявила бы 5
```

Код возврата 0.

**Контроль прибора.** Прибор даёт четыре РАЗНЫХ ответа (0, 1, 2 и снова 1 при пяти
разрывах) — он не заперт ни в «всё объяснено», ни в «ничего не объяснено». Строка на
шести разрывах решающая: старая формула списала бы пять дыр одним битым баром, новая
списывает одну.

⚠ Строка про границу — не дефект, а выбор: сравнение строгое (`a < at < b`), и бар,
отклонённый ровно на левом краю дыры, дыру не объясняет. Это осторожная сторона.

---

## Д-8. Блокирующий `urllib` внутри корутины — ЗАКРЫТА

archive.py функция _fetch ⟨механизм удалён 2026-08-11, см. archive-removed-2026-08-11.md⟩ по-прежнему синхронный `urllib`, и это правильно: он больше не
вызывается с цикла событий. run.py::backfill_trades уводит в поток ВСЁ тело:

```python
    await asyncio.to_thread(_backfill_impl, insts, uni, report, horizon_days,
                            max_days_per_symbol)
```

На цикле остаётся только то, что трогает биржу, — сбор `Instrument`. Служба (файл
`src/hunter/service.py`) зовёт `backfill_trades` уже как корутину, а каждый следующий
тяжёлый шаг оборачивает отдельным `asyncio.to_thread`.

```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import inspect
from hunter import run
s = inspect.getsource(run.backfill_trades)
print('to_thread в backfill_trades:', 'asyncio.to_thread' in s)
print('binned_day вызывается в:', 'sync _backfill_impl' if 'archive.binned_day' in inspect.getsource(run._backfill_impl) else '?')
print('_backfill_impl корутина?', inspect.iscoroutinefunction(run._backfill_impl))
"
```

Вывод:

```
to_thread в backfill_trades: True
binned_day вызывается в: sync _backfill_impl
_backfill_impl корутина? False
```

Код возврата 0. Правка вдобавок расширена 2026-08-06: в поток ушёл и подсчёт нужных
суток, который гоняет настоящий расчёт по каждому ТФ.

---

## А-5. Словарь ccxt на границе слоёв — ПОДТВЕРЖДЕНА НА HEAD

```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import ast, inspect, pathlib
from hunter import exchange, run
print('watch_agg_trades ->', exchange.Exchange.watch_agg_trades.__annotations__['return'])
print([l.strip() for l in inspect.getsource(run._watch_trades_impl).splitlines() if '.get(' in l])
n = 0
for node in ast.walk(ast.parse(pathlib.Path('src/hunter/run.py').read_text('utf-8'))):
    if isinstance(node, ast.Subscript):
        b, sl = node.value, node.slice
        nm = getattr(b,'id',None) or getattr(b,'attr',None)
        if nm in {'dict','Dict'} and isinstance(sl, ast.Tuple) and len(sl.elts)==2:
            v = sl.elts[1]
            if getattr(v,'id',None)=='Any' or getattr(v,'attr',None)=='Any': n += 1
print('аннотаций dict[str, Any] в run.py:', n)
"
```

Вывод:

```
watch_agg_trades -> AsyncGenerator[list[dict[str, Any]]]
['price, amount, ts = t.get("price"), t.get("amount"), t.get("timestamp")', 'seq.note(t.get("id"))']
аннотаций dict[str, Any] в run.py: 0
```

Код возврата 0.

**Почему гейт молчит — проверено, а не предположено.** `gates/no_loose_dicts.py`
объявляет границу списком `BOUNDARY_FILES = {'src/hunter/exchange.py'}` и ищет
АННОТАЦИИ. Словарь объявлен в разрешённом файле, а читается в `src/hunter/run.py`, где
аннотации нет вовсе — ловить гейту нечего. Находка верна дословно: тип объявлен на
границе, но пересекает её и разбирается по строковым ключам во втором слое.

---

## Э-2. Охват гейтов каталогом `scripts/` — ПОДТВЕРЖДЕНА НА HEAD

`ROOTS = ("src", "gates")` во всех трёх гейтах: `gates/production_writer.py`,
`gates/no_silent_failure.py`, `gates/public_data_only.py`. Каталога `scripts` в охвате
нет.

Пробник: файл с тремя заведомыми нарушениями (вызов `open_production_ledger` вне списка
`ALLOWED`, немой `except`, приватный вызов ccxt) положен в `scripts/`, затем скопирован
в `src/`, затем оба удалены.

```bash
cat > scripts/_probe_e2_violation.py <<'PY'
from hunter import store
def bad() -> None:
    conn = store.open_production_ledger()
    try:
        conn.execute("SELECT 1")
    except Exception:
        pass
    ex = None
    ex.create_order("BTC/USDT", "limit", "buy", 1)
    print(ex.apiKey)
PY
for g in production_writer no_silent_failure public_data_only; do
  PYTHONIOENCODING=utf-8 uv run python gates/$g.py; echo "$g -> код возврата $?"
done
cp scripts/_probe_e2_violation.py src/_probe_e2_violation.py       # КОНТРОЛЬ
for g in production_writer no_silent_failure public_data_only; do
  PYTHONIOENCODING=utf-8 uv run python gates/$g.py; echo "$g в src -> код возврата $?"
done
rm -f scripts/_probe_e2_violation.py src/_probe_e2_violation.py
rm -rf scripts/__pycache__/_probe_e2_violation* src/__pycache__/_probe_e2_violation*
```

Вывод (нарушение в `scripts/`):

```
production_writer -> код возврата 0
гейт «единственный писатель»: просмотрено файлов 47, вызовов open_production_ledger найдено 2, вне списка 0
no_silent_failure -> код возврата 0
гейт «никакого молчания»: просмотрено файлов 48, обработчиков-глушителей 0
public_data_only -> код возврата 0
гейт «только публичные данные»: просмотрено файлов 47, нарушений 0
```

**Контроль прибора** (тот же файл в `src/`):

```
production_writer -> код возврата 1
гейт «единственный писатель»: просмотрено файлов 48, вызовов open_production_ledger найдено 3, вне списка 1
  НАРУШЕНИЕ src/_probe_e2_violation.py:6: open_production_ledger вне списка разрешённых
no_silent_failure -> код возврата 1
гейт «никакого молчания»: просмотрено файлов 49, обработчиков-глушителей 1
  НАРУШЕНИЕ src\_probe_e2_violation.py:9: тело пустое (pass)
public_data_only -> код возврата 1
гейт «только публичные данные»: просмотрено файлов 48, нарушений 1
  НАРУШЕНИЕ src\_probe_e2_violation.py:12: приватный вызов CCXT: create_order
```

Все три гейта видят ровно то же нарушение и падают кодом 1, как только файл переезжает
в охват. Ноль в `scripts/` — свойство `ROOTS`, а не чистоты каталога. Оба пробных файла
удалены, дерево восстановлено.

---

## Э-4. `ci_covers_gates` проверяет подстроку — ПОДТВЕРЖДЕНА НА HEAD

`gates/ci_covers_gates.py` сравнивает имя файла с текстом рабочего процесса:
`missing = [n for n in files if n not in text]`. Комментарий шага от вызова шага
неотличим.

```bash
cp .github/workflows/ci.yml /tmp/ci.bak
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import pathlib
p = pathlib.Path('.github/workflows/ci.yml'); s = p.read_text(encoding='utf-8')
old = "      - name: 'гейт: цитаты курса дословны (§0)'\n        run: uv run python gates/course_citations.py\n"
new = "      # - name: 'гейт: цитаты курса дословны (§0)'\n      #   run: uv run python gates/course_citations.py\n"
p.write_text(s.replace(old, new, 1), encoding='utf-8')
PY
PYTHONIOENCODING=utf-8 uv run python gates/ci_covers_gates.py; echo "код возврата: $?"
cp /tmp/ci.bak .github/workflows/ci.yml
```

Вывод до правки (дерево не тронуто):

```
гейтов в каталоге 18, вызываются в CI 18
нарушений 0
код возврата: 0
```

Вывод при ЗАКОММЕНТИРОВАННОМ шаге:

```
гейтов в каталоге 18, вызываются в CI 18
нарушений 0
код возврата: 0
```

**Контроль прибора** — упоминание удалено целиком:

```
гейтов в каталоге 18, вызываются в CI 17
ПРОВАЛ: не вызываются в CI — 1
  gates/course_citations.py
код возврата: 1
```

Прибор способен ответить иначе, и отвечает — но только на удаление строки, не на её
отключение. Файл рабочего процесса восстановлен: `git diff` по нему пуст, повторный
прогон гейта снова даёт 18 из 18 и код 0.

Дыра одностороннего свойства: гейт ловит забытый в CI гейт и НЕ ловит выключенный.
Ровно та форма, о которой предупреждает собственная докстрока гейта.

---

## Н-10. `.github/workflows` в `SEARCH` гейта `doc_refs` — ЗАКРЫТА

`gates/doc_refs.py::SEARCH` содержит каталог с 2026-08-05, вместе с `scripts`:

```python
SEARCH = (Path(), Path("src"), Path("src/hunter"), Path("gates"), Path("scripts"),
          Path("docs"), Path("research/prizrak_corpus"), Path("config"),
          Path(".github/workflows"))
```

```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import sys; sys.path.insert(0,'gates')
import doc_refs, pathlib
print('.github/workflows в SEARCH:', pathlib.Path('.github/workflows') in doc_refs.SEARCH)
print('короткая ссылка ci.yml резолвится в:', doc_refs.resolve('ci.yml', pathlib.Path('docs/audit/x.md')))
"
```

Вывод:

```
.github/workflows в SEARCH: True
короткая ссылка ci.yml резолвится в: .github\workflows\ci.yml
```

Код возврата 0. Ложного минуса нет.

---

## Р-4. `bin_price` возвращает нижнюю границу бина — ПОДТВЕРЖДЕНА ЧАСТИЧНО

Код прежний: models.py::TradeHistogram.bin_price считает `Decimal(idx) * tick_size`.
Но замер меняет оценку тяжести, и меняет её в двух местах по-разному.

### Сдвиг ПОК на BTC: ноль

Боевой шаг цены BTC в кадрах — `Decimal('0.10')`, а НЕ `Decimal('0.1')`, и это важно:
models.py::tick_scale даёт для них разные пары.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
import random
from decimal import Decimal
from hunter.models import TradeHistogram, tick_scale
for s in ("0.1", "0.10", "0.01"):
    t = Decimal(s); scale, step = tick_scale(t)
    h = TradeHistogram(symbol="X", tick_size=t); rnd = random.Random(11)
    on, off = [], []
    for _ in range(50000):
        k = rnd.randint(1, 2_000_000)
        p_on = float(t * k)
        on.append(float(h.bin_price(h.bin_index(p_on))) - p_on)
        p_off = p_on + float(t) * rnd.uniform(0.01, 0.99)
        off.append(float(h.bin_price(h.bin_index(p_off))) - p_off)
    ft = float(t)
    print(f"tick={s:<6} scale={scale:<5} step={step:<4}")
    print(f"    НА СЕТКЕ : средн {sum(on)/len(on)/ft:+.5f} тика, макс|.| {max(abs(e) for e in on)/ft:.2e}")
    print(f"    ВНЕ СЕТКИ: средн {sum(off)/len(off)/ft:+.5f} тика, "
          f"диапазон [{min(off)/ft:+.4f}, {max(off)/ft:+.4f}]")
PY
```

Вывод:

```
tick=0.1    scale=10    step=1
    НА СЕТКЕ : средн +0.00000 тика, макс|.| 0.00e+00
    ВНЕ СЕТКИ: средн +0.00105 тика, диапазон [-0.5000, +0.5000]
tick=0.10   scale=100   step=10
    НА СЕТКЕ : средн +0.00000 тика, макс|.| 0.00e+00
    ВНЕ СЕТКИ: средн -0.45911 тика, диапазон [-0.9500, +0.0500]
tick=0.01   scale=100   step=1
    НА СЕТКЕ : средн +0.00000 тика, макс|.| 0.00e+00
    ВНЕ СЕТКИ: средн +0.00105 тика, диапазон [-0.5000, +0.5000]
```

Код возврата 0. Читается так:

* цены сделок биржи кратны `tickSize` — на этой сетке `bin_price(bin_index(p)) == p`
  ТОЧНО, ошибка ноль при всех трёх шагах. **Систематического сдвига ПОК на BTC нет.**
* контроль прибора — цены ВНЕ сетки: там сдвиг появляется и он разный по знаку. При
  `step = 1` округление к ближайшему (среднее ноль, диапазон полтика в обе стороны), при
  `step = 10` — усечение вниз, среднее **−0.459 тика**. То есть прибор способен показать
  систематический сдвиг вниз и показывает его — просто не на тех данных, которые приходят
  с биржи.

Показательный случай на BTC: цена 118000.10 (на сетке) даёт `bin_price` 118000.10, сдвиг
0. Цена 118000.19 (вне сетки) даёт 118000.10, сдвиг −0.09, то есть −0.9 тика.

⚠ **Оговорка о доказуемости.** Кэш архива хранит уже НОМЕРА бинов, исходные цены из него
не восстановимы, поэтому «все цены сделок лежат на сетке» здесь опирается на
`PRICE_FILTER` биржи, а не на прямой замер по сырым сделкам. Косвенный признак из
кэша: в файле `BTCUSDT-2026-04-22-300000-t0p10-b2.parquet` заполнено 33 317 различных
бинов при диапазоне номеров 760786…794440, то есть 33 655 возможных, — сетка плотная и
регулярная.

```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import polars as pl
df = pl.read_parquet('data/aggcache/BTCUSDT-2026-04-22-300000-t0p10-b2.parquet')
b = df['bin']
print('строк', df.height, '| бинов различных', b.n_unique(),
      '| диапазон', b.min(), b.max(), '| возможных', b.max()-b.min()+1)
"
```

### Сдвиг верхней границы зоны: ровно один бин

Здесь находка верна. volume_profile.py::build пишет
`vah_price = hist.bin_price(hi)` — это НИЖНЯЯ граница ВЕРХНЕГО бина зоны стоимости.
Нижняя граница зоны (`val_price`) при этом точна. Асимметрия: зона занижена сверху ровно
на ширину бина.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
from hunter import store, volume_profile
from hunter.models import NotReady, TradeHistogram
for run_id in ("a1", "last", "stage1", "mkt3"):
    for sym in store.saved_symbols(run_id):
        meta = store.read_meta(run_id, sym)
        if isinstance(meta, NotReady): continue
        name, tick, bucket = meta
        t = store.read_binned_trades(run_id, sym, tick, bucket, symbol=name)
        if isinstance(t, NotReady): continue
        h = TradeHistogram(symbol=name, tick_size=tick)
        for _b, bins in t.qty.items():
            for i, q in bins.items():
                h.qty_by_bin[i] = h.qty_by_bin.get(i, 0.0) + q
                h.count_by_bin[i] = h.count_by_bin.get(i, 0) + 1
                h.trades_seen += 1; h.qty_seen += q
        va = volume_profile.build(h)
        if isinstance(va, NotReady): continue
        w = va.vah_price - va.val_price
        print(f"{run_id:<8}{sym:<16} tick={tick} ПОК={va.poc_price} VAL={va.val_price} "
              f"VAH={va.vah_price} ширина={w} тик={float(tick)/float(w)*100 if w else float('nan'):.4f}% "
              f"бинов={va.vah_bin-va.val_bin+1}")
PY
```

Вывод:

| прогон | символ | tick | ПОК | VAL | VAH | ширина | тик, % ширины | бинов |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| a1 | BCH | 0.01 | 214.80 | 214.57 | 214.86 | 0.29 | 3.4483 | 30 |
| a1 | BTC | 0.10 | 64655.70 | 64655.60 | 64679.90 | 24.30 | 0.4115 | 244 |
| a1 | ETH | 0.01 | 1910.60 | 1910.58 | 1911.73 | 1.15 | 0.8696 | 116 |
| last | BCH | 0.01 | 213.43 | 213.36 | 213.63 | 0.27 | 3.7037 | 28 |
| last | BTC | 0.10 | 64168.30 | 64168.20 | 64168.30 | 0.10 | 100.0000 | 2 |
| last | ETH | 0.01 | 1870.98 | 1870.97 | 1871.10 | 0.13 | 7.6923 | 14 |
| stage1 | BCH | 0.01 | 215.30 | 215.29 | 215.46 | 0.17 | 5.8824 | 18 |
| stage1 | BTC | 0.10 | 64868.70 | 64845.50 | 64882.90 | 37.40 | 0.2674 | 375 |
| stage1 | ETH | 0.01 | 1919.23 | 1918.65 | 1919.59 | 0.94 | 1.0638 | 95 |
| mkt3 | BTC | 0.10 | 64867.20 | 64867.20 | 64867.20 | 0.00 | — | 1 |
| mkt3 | ETH | 0.01 | 1918.58 | 1918.57 | 1918.79 | 0.22 | 4.5455 | 23 |

Число, о котором просили: на BTC при `tickSize = 0.10` **систематический сдвиг ПОК —
ноль**, а **верхняя граница зоны занижена на 0.10**, что составляет 0.27 % ширины зоны
на широких зонах (375 бинов) и до 100 % на вырожденных (2 бина). Сама ширина бина на BTC
— 0.10, то есть 0.000131 % цены при 76 000 и 0.0000847 % при 118 000.

**Контроль прибора.** Диапазон ответов от 0.2674 % до 100 % на одиннадцати замерах —
прибор не заперт в одном числе. Вырожденные строки (`last`, `mkt3`) — короткие прогоны,
где зона стоимости уложилась в один-два бина; они и показывают, что при бедном профиле
одна недосчитанная ширина бина перестаёт быть мелочью.

---

## Р-5. Последовательность структур — НЕ ВОСПРОИЗВОДИТСЯ как заявлено

Механизм в accumulation.py::detect описан верно: после каждой найденной структуры
вызывается `reset()`, состояние границ очищается. Но вывод «перекрытие и вложенность на
одном ТФ невозможны ПО ПОСТРОЕНИЮ» замер опровергает.

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
from hunter import accumulation, store, swings
from hunter.models import NotReady
tot = pairs = over = nested = 0; maxover = 0
for r in ("a1", "last", "stage1", "mkt3"):
    for sym in store.saved_symbols(r):
        for tf in store.saved_timeframes(r, sym):
            bars = store.read_bars(r, sym, tf)
            if len(bars) < 5: continue
            sw = swings.detect(bars)
            if isinstance(sw, NotReady): continue
            iv = [(a.first_index, a.last_index)
                  for a in accumulation.detect(bars, sw, tf).closed]
            tot += len(iv)
            for i in range(len(iv) - 1):
                pairs += 1
                if iv[i+1][0] <= iv[i][1]: over += 1
            for i in range(len(iv)):
                for j in range(i+1, len(iv)):
                    if iv[j][0] >= iv[i][0] and iv[j][1] <= iv[i][1]: nested += 1
                    maxover = max(maxover,
                                  min(iv[i][1],iv[j][1]) - max(iv[i][0],iv[j][0]) + 1)
print(f"структур {tot}, соседних пар {pairs}, перекрывающихся {over}, "
      f"вложенных {nested}, макс. перекрытие {maxover} бар")
PY
```

Вывод:

```
структур найдено ВСЕГО: 771
соседних пар проверено: 705
ПЕРЕКРЫВАЮЩИХСЯ пар: 278
    a1/BCH_USDT_USDT/15m: [68,101] и [101,133] перекрываются на 1 бар(ов)
    a1/BCH_USDT_USDT/15m: [101,133] и [132,191] перекрываются на 2 бар(ов)
    a1/BCH_USDT_USDT/1d: [125,208] и [207,259] перекрываются на 2 бар(ов)
структур по ТФ: {'15m': 133, '1d': 125, '1h': 148, '1w': 94, '4h': 133, '5m': 138}
пар структур одного ТФ проверено: 5080
ВЛОЖЕННЫХ (одна целиком внутри другой): 0
максимальное перекрытие любой пары, баров: 2
```

Код возврата 0. Четыре прогона, три символа, шесть таймфреймов, 771 структура.

**Как это читать.** Перекрытие есть у 278 пар из 705 (39 %), но оно **никогда не
превышает двух баров**, а вложенных структур ноль из 5 080 пар. Причина механическая и
проверяемая: `first_index` — это индекс СВИНГА, а свинг Вильямса подтверждается двумя
барами позже (`SIDE_BARS = 2` в `src/hunter/swings.py`). Сразу после `reset()` в новую
структуру попадают фракталы, чей индекс лежит на один-два бара РАНЬШЕ точки сброса.
Максимум перекрытия ровно 2 = `SIDE_BARS` — это подпись механизма, а не совпадение.

**Контроль прибора.** На подставном списке `[(0,10),(9,20),(25,30)]` зонд насчитал 1
перекрытие — он умеет их видеть. И на настоящих данных он их видит: ответ «278» не
получен из-под палки.

**Вердикт по существу.** Утверждение «структуры строго последовательны» подтверждено:
вложенности нет, содержательного перекрытия нет. Утверждение «перекрытие невозможно по
построению» — неверно: интервалы, которые печатаются наружу, перекрываются в 39 %
случаев. Это дефект ОТЧЁТНОСТИ, а не расчёта: `first_index` называет момент рождения
первого свинга, а не момент, с которого структура наблюдалась. Требование раздела 6
свода — «проверить последовательность структур на корпусе» — выполнено, и ответ:
последовательны, но отчётные интервалы про это врут на 1–2 бара.

---

## Р-6. Тренд без окна — ПОДТВЕРЖДЕНА НА HEAD

Окна нет: ни swings.py::detect, ни swings.py::trend не принимают параметра окна и не
режут ряд.

```
swings.trend  : (swings: 'SwingSet') -> 'Trend'
swings.detect : (bars: 'list[Bar]') -> 'SwingSet | NotReady'
```

Тренд строится по всем свингам поданного ряда. Замер на кадрах прогона `a1` (499–500
баров на ряд — таков боевой засев):

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'PY'
from hunter import store, swings
from hunter.models import NotReady
for sym in store.saved_symbols("a1"):
    for tf in store.saved_timeframes("a1", sym):
        bars = store.read_bars("a1", sym, tf)
        sw = swings.detect(bars)
        if isinstance(sw, NotReady): continue
        tr = swings.trend(sw)
        lows, highs = sw.of(swings.SwingKind.LOW), sw.of(swings.SwingKind.HIGH)
        seq = lows if tr.direction is swings.TrendDirection.UP else highs
        hold = (seq[-1].open_ms - seq[-tr.holds_for].open_ms) if tr.holds_for and len(seq) >= tr.holds_for else 0
        print(f"{sym:<16}{tf:<4} баров {len(bars):>4} ряд {(bars[-1].open_ms-bars[0].open_ms)/86400000:>8.2f} сут"
              f" лоёв {len(lows):>3} хаёв {len(highs):>3} тренд {tr.direction.value:>5}"
              f" holds {tr.holds_for} охват {hold/86400000:.2f} сут")
PY
```

Вывод:

| символ | ТФ | баров | ряд, суток | лоёв | хаёв | тренд | `holds_for` | охват тренда, суток |
|---|---|---:|---:|---:|---:|---|---:|---:|
| BCH | 5m | 500 | 1.73 | 64 | 60 | none | 0 | 0.00 |
| BCH | 15m | 500 | 5.20 | 71 | 75 | up | 6 | 0.53 |
| BCH | 1h | 499 | 20.75 | 72 | 71 | up | 3 | 0.38 |
| BCH | 4h | 499 | 83.00 | 73 | 64 | none | 0 | 0.00 |
| BCH | 1d | 499 | 498.00 | 69 | 69 | none | 0 | 0.00 |
| BCH | 1w | 345 | 2415.00 | 50 | 49 | down | 2 | 63.00 |
| BTC | 5m | 500 | 1.73 | 66 | 62 | down | 3 | 0.07 |
| BTC | 15m | 500 | 5.20 | 69 | 71 | none | 0 | 0.00 |
| BTC | 1h | 499 | 20.75 | 64 | 68 | none | 0 | 0.00 |
| BTC | 4h | 499 | 83.00 | 65 | 67 | up | 3 | 2.33 |
| BTC | 1d | 499 | 498.00 | 73 | 69 | down | 3 | 10.00 |
| BTC | 1w | 361 | 2520.00 | 47 | 51 | none | 0 | 0.00 |
| ETH | 5m | 500 | 1.73 | 65 | 62 | down | 2 | 0.02 |
| ETH | 15m | 500 | 5.20 | 59 | 74 | none | 0 | 0.00 |
| ETH | 1h | 499 | 20.75 | 68 | 72 | none | 0 | 0.00 |
| ETH | 4h | 499 | 83.00 | 70 | 68 | up | 4 | 3.33 |
| ETH | 1d | 499 | 498.00 | 69 | 67 | down | 2 | 3.00 |
| ETH | 1w | 349 | 2436.00 | 46 | 41 | up | 2 | 21.00 |

Код возврата 0.

**Два ответа на вопрос «на каком интервале строится тренд».**

* **Окно ПОИСКА** — весь ряд: на 5м это 1.73 суток при 500 барах, на 1ч — 20.75 суток,
  на 1Н — 2 520 суток, то есть 6.9 года. (⚠ Свод разбора называл «19 лет на 1Н»; это
  ошибка ярлыка: 19 лет получается на НЕДЕЛЬНОМ ряде в 1 000 баров, а часовой ряд в
  1 000 баров — это 41.7 суток. Боевой засев к тому же даёт 499–500 баров, а не 1 000.)
* **Фактическая ГЛУБИНА** — `holds_for` от 0 до 6 экстремумов, и её временной охват
  куда скромнее окна поиска: 0.02–3.3 суток на 5м/15м/1ч/4ч и 3–63 суток на 1Д/1Н.
  То есть направление почти всегда опирается на 2–4 последних экстремума, а не на весь
  ряд.

**Контроль прибора.** Ответы разные и по направлению (up / down / none), и по глубине
(0…6), и по охвату (0.02…63 суток). Прибор не заперт.

**Что из находки остаётся.** Смягчающее обстоятельство: глубина ДОКЛАДЫВАЕТСЯ —
swings.py::Trend несёт поле `holds_for`, и priority.py::resolve протаскивает его в
`Priority`. Остающийся дефект: priority.py::agreement использует только `direction` и
на `holds_for` не смотрит вовсе, поэтому тренд из двух экстремумов, охватывающих 0.02
суток (ETH 5м в таблице), даёт согласие сделки ровно с тем же весом, что тренд из шести.
Порог глубины отсутствует, и на этой строке находка Р-6 живёт.

---

## Что оказалось УЖЕ ЗАКРЫТО против того, что подтвердилось

**Закрыто к HEAD (восемь позиций).** Л-1, Л-2, Л-3, Б-5, Д-4, Д-8, Н-10 закрыты
полностью; Л-4 — на две трети (WAL и `busy_timeout` есть, общей транзакции у пары
сигнал+исход нет). Попутно подтверждено закрытие Б-4: `tick` вошёл в имя файла кэша.
Все восемь числились в своде как **выведено** и проверке подлежали — вывод свода был
верным, но подтвердить его без исполнения было нельзя.

**Подтверждено на HEAD (пять позиций).** А-5, Э-2, Э-4, Р-6, и наполовину Р-4. Все пять
числились в своде как **открыто** — файлы, которых они касаются, между 04.08 и 07.08 не
менялись в нужной части.

**Не воспроизводится как заявлено (одна позиция).** Р-5: механизм верен, вывод «по
построению невозможно» неверен, но перекрытие не содержательное — 1–2 бара, ровно
задержка подтверждения фрактала.

**Переоценка тяжести — две позиции.**

* **Р-4** была записана как систематический сдвиг вниз до тика на ЦЕНЕ УРОВНЯ. Замер:
  на ценах биржевой сетки сдвиг ПОК ровно ноль, при любом из трёх шагов. Остаётся
  занижение ВЕРХНЕГО края зоны на один бин — 0.27 % ширины зоны на широких профилях BTC
  и до 100 % на вырожденных. Это меньше и в другом месте, чем заявлено.
* **Р-6** записана как «тренд по всей истории». Формально верно, но `holds_for`
  показывает, что фактическая опора — 2–4 экстремума. Настоящий адрес дефекта не в
  ширине окна, а в priority.py::agreement, которое глубину игнорирует.

**Ошибка свода, найденная попутно.** Цифра «19 лет на 1Н» в разделе 2.5 свода относится
к недельному ряду, а не к часовому, и к тому же взята для 1 000 баров при боевом засеве
в 499–500.

---

## Гигиена этой проверки

Пробники, требовавшие правки дерева, восстановлены:

* `.github/workflows/ci.yml` — закомментирован и удалён один шаг, файл возвращён из
  копии, `git diff` по нему пуст, повторный прогон гейта снова даёт 18 из 18 и код 0;
* два файла-нарушителя в `scripts/` и `src/` — созданы, замерены, удалены вместе с
  байткодом.

Ничего не коммичено. `data/ledger.sqlite3` и `data/aggcache` только читались; все
операции с леджером шли на базах в каталоге `tempfile.mkdtemp()`.
