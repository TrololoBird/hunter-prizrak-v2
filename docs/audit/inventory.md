# Реестр вычисляемых величин и обратная трассировка

**HEAD:** `5e0edb14cf47145fb98836533d436b74254ffacf`.

```bash
uv run python -c "import ast,pathlib; from collections import Counter; rows=[(f.stem,n.name) for f in sorted(pathlib.Path('src/hunter').glob('*.py')) if not f.name.startswith('__') for n in ast.parse(f.read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and not n.name.startswith('_')]; print('публичных имён:',len(rows)); print(Counter(m for m,_ in rows).most_common())"
```

**Публичных имён верхнего уровня в `src/hunter`: 255** (функции, классы, константы).

⚠ **Этот реестр НЕПОЛОН.** Полная величина-за-величиной трассировка 255 имён в объём
аудита не уместилась. Здесь — обратная трассировка (что в коде, чего нет в §2) по трём
классам, где нарушение наиболее вероятно, и прямая трассировка §2.1–§2.10 → код.

---

## 1. Прямая трассировка §2 → код

| пункт §2 | место в коде | статус |
|---|---|---|
| 2.1 накопление | `accumulation.detect` | реализовано; правило «первых 2 точек» прочитано спорно (М-01) |
| 2.2 уровень = ПОК | `volume_profile.build` → `levels.build_level` | реализовано; **прибор расходится с авторским** (М-03) |
| 2.3 стоповый объём | `stop_volume.classify` → `levels.stop_anchor` | реализовано, подключено 2026-08-05 |
| 2.4 прокол/пробой | `breach.first_breach` | реализовано; единственное определение на проект |
| 2.5 переприор | `pereprior.detect` | вычисляется, **в решение не входит** (А-02) |
| 2.6 фигуры | — | **отсутствует, и по построению невозможно** (С-02) |
| 2.7 геометрия | `geometry.build_setup`, `build_targets` | реализовано; «процент от чего» не арбитрирован (М-05) |
| 2.8 приоритет ТФ | `priority.resolve`, `agreement` | реализовано **верно**; неверен был §2.8 (D-03) |
| 2.9 индикаторы | `indicators.*` → `factors.*` → только `card.render` | реализовано, **сигнал не гейтит** — проверено |
| 2.10 отработанный уровень | `levels.status`, `emit.hold_reason` | реализовано; вход по слому МТФ назван, но не исполняется (А-02) |

---

## 2. Обратная трассировка: что в коде, чего в §2 нет

### 2.1. Подсистемы §3 — ни одной реализованной

```bash
uv run python -c "import pathlib,re; pat=re.compile(r'funding|фандинг|open_?interest|ликвидац|liquidat|доминац|dominance|cvd|footprint|футпринт|orderbook|стакан|confluence|long_?short|_score|_confidence|_quality|_pressure|_bias',re.I); hits=[(f.name,i+1,l.strip()) for f in pathlib.Path('src/hunter').glob('*.py') for i,l in enumerate(f.read_text(encoding='utf-8').splitlines()) if pat.search(l)]; print('совпадений:',len(hits)); [print(' ',*h) for h in hits[:10]]"
```

Совпадений **два**, и оба — не подсистемы, а текст:

* `models.py:185` — комментарий, ОБЪЯСНЯЮЩИЙ, почему сторона сделки не хранится
  («§3 запрещает CVD»);
* `run.py:1381` — строка, которую система ПЕЧАТАЕТ владельцу: «КОМИССИИ, ФАНДИНГ И
  ПРОСКАЛЬЗЫВАНИЕ НЕ МОДЕЛИРУЮТСЯ».

Слово `basis` встречается только как `geometry.StopBasis` (чем обоснован стоп) — это не
«базис» фьючерса.

**`NOT_IN_SPEC` по списку §3: пусто.** Ни одной запрещённой подсистемы, ни одного суффикса
`*_score`, `*_confidence`, `*_quality`, `*_pressure`, `*_bias`.

### 2.2. Абсолютные проценты (§4.1)

Шесть констант, все названы курсом прямо, то есть подпадают под исключение §4.1:

| константа | значение | источник |
|---|---|---|
| `geometry.STOP_MARGIN_MIN_PCT` / `MAX_PCT` | 1.0 / 3.0 | стр. 33, 36, 58 |
| `geometry.DEFAULT_MARGIN_PCT` | = MAX | ⚠ выбор ВНУТРИ диапазона, курсом не сделанный; назван в коде явно |
| `geometry.PARTIAL_TAKE_PCT` | 50.0 | стр. 19 |
| `levels.STOP_ANCHOR_BAND_MIN_PCT` / `MAX_PCT` | 2.0 / 5.0 | стр. 18 |

### 2.3. Константы БЕЗ референта §0 — подтверждают находку Э-6 прошлого разбора

Операционные величины транспорта, у которых внешнего референта нет и быть не может, но §0
формально запрещает их писать:

`archive.FETCH_TRIES` · `clock.MAX_SYNC_AGE_MS` · `exchange.POLL_OFFSET_S` ·
`exchange.CATCHUP_MAX_BARS` · `exchange.CATCHUP_RETRY_S` · `exchange.WS_TRADES_SILENCE_S` ·
`exchange.WS_RETRY_S` · `exchange.RATE_LIMIT_BACKOFF_S` · `breach.CONFIRM_BODIES` ·
`breach.RETURN_BARS` · `levels.NESTED_MAX_STEPS`

Все названы в коде явно, у большинства стоит пометка «не замерено» (образцово —
`clock.MAX_SYNC_AGE_MS`). Но §0 говорит «не пишется», а они написаны.

**Вывод тот же, что у прошлого разбора: править §0, а не код.** Документу недостаёт
категории «выбор внутри диапазона, названного источником» и категории «операционная
величина транспорта». Аудит эту правку НЕ вносил: §0 — конституция проекта, и её изменение
решение владельца, а не аудитора.

### 2.4. Величины без потребителя

Проверено грепом вызовов по всему `src/`:

| величина | потребитель | вердикт |
|---|---|---|
| `factors.divergences`, `squeeze`, `ma_touch` | только `card.render` | норма: §2.9 требует именно отображения |
| `pereprior.detect`, `failed_update` | только `card.render` | **находка А-02** — §2.5 не входит в решение |
| `Breach.worked_off` | **никто** (упомянут только в докстроке гейта) | мёртвое свойство, P3 |
| `VolumeProfile.covered_fraction` | `__main__.py:118` (команда `profile`) | живое |
| `Setup.average_entry_equal_shares` | `card.render` | живое |
| `OpenStructure.points` | `card.py:108` | живое |
| `Accumulation.points` | `gates/course_rules.py:200` | живое (только гейт) |

⚠ **Первая редакция этой таблицы объявляла мёртвыми четыре свойства; при проверке
мёртвым оказалось ОДНО.** Ошибка была в грепе: `worked_off` я искал без точки и ловил
константу `LevelState.WORKED_OFF`, а `.points` — без границы слова. Уточнённая проверка:

```bash
uv run python -c "import subprocess; print(subprocess.run(['git','grep','-n','--','\.worked_off\b','src','gates','scripts'],capture_output=True).stdout.decode('utf-8') or 'вызовов нет')"
```

Проверка всё равно **слабая**: обращение через `getattr` или шаблон печати грепом не
видно. Единственное найденное мёртвое свойство не исправлялось — оно безвредно.

---

## 3. Чего этот реестр НЕ содержит

* Величина-за-величиной разбор всех 255 публичных имён с формулой, источником и
  потребителем. Сделаны только три класса выше.
* Разбор run.py (1 444 строки) и store.py (709) — прочитаны выборочно.
* Проверка, что каждая величина, попадающая в карточку, имеет продюсера: это гарантируется
  типами (`extra="forbid"` + mypy strict), но отдельно не проверялось.
