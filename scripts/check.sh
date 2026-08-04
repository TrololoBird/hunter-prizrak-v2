#!/usr/bin/env bash
# Все проверки одной командой, с НЕНУЛЕВЫМ кодом возврата при любом провале.
#
# Написан 2026-08-04 после того, как я ДВАЖДЫ закоммитил при красном гейте. Причина оба
# раза одна: цикл `for g in gates/*.py; do ... done` возвращает код ПОСЛЕДНЕЙ итерации,
# а не худшей, и `&& git commit` после него срабатывал независимо от результата. Провал
# при этом ПЕЧАТАЛСЯ — то есть глазами он был виден, и это ровно та ситуация, против
# которой в CLAUDE.md записано «сводка обязана падать по коду возврата, а не печатать».
#
#   bash scripts/check.sh && git commit ...
#
set -u
fail=0

run() {
  local name="$1"; shift
  if ! "$@" >/tmp/hunter-check.log 2>&1; then
    echo "ПРОВАЛ  $name"
    tail -20 /tmp/hunter-check.log
    fail=1
  else
    echo "ok      $name"
  fi
}

run ruff uv run ruff check .
run mypy uv run mypy

n=0
for g in gates/*.py; do
  [ "$(basename "$g")" = "__init__.py" ] && continue
  run "гейт $(basename "$g" .py)" uv run --group gates python "$g"
  n=$((n + 1))
done

echo "-----------------------------------------------------------"
echo "гейтов прогнано: $n"
if [ "$fail" -ne 0 ]; then
  echo "ИТОГ: ЕСТЬ ПРОВАЛЫ — коммитить нельзя"
  exit 1
fi
echo "ИТОГ: всё зелёное"
