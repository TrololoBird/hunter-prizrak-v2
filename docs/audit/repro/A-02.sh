#!/usr/bin/env bash
# Воспроизведение находки А-02: §2.5 (переприор) не входит в расчёт сигнала.
# `engine.decide` объявлен единственной точкой расчёта; `pereprior` там не вызывается.
set -u
export PYTHONIOENCODING=utf-8
echo "== вызовы pereprior во всём src/hunter, кроме самого модуля =="
grep -rn "pereprior" src/hunter/*.py | grep -v "^src/hunter/pereprior.py" || echo "(нет)"
echo
echo "== есть ли pereprior в engine.py (единственная точка расчёта)? =="
if grep -q "pereprior" src/hunter/engine.py; then
  echo "НАЙДЕН — находка НЕ воспроизводится"; exit 1
fi
echo "НЕ НАЙДЕН — находка воспроизведена: переприор считается только при печати карточки"
exit 0
