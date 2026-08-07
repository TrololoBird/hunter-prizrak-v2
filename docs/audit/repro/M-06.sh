#!/usr/bin/env bash
# Воспроизведение находки М-06 (= Н-5 разбора 2026-08-04):
# индикаторные гейты не покрывали `hunter/indicators.py`.
#
# ДО правки: все три гейта возвращали 0 на подсаженном нарушении.
# ПОСЛЕ правки: indicator_oracle и formula_reference возвращают 1.
# indicator_availability возвращает 0 ЗАКОННО — умножение на 1.5 не двигает бар,
# с которого величина определена; его непустота проверяется вторым пробником ниже.
set -u
export PYTHONIOENCODING=utf-8
BAK=$(mktemp)
cp src/hunter/indicators.py "$BAK"
restore() { cp "$BAK" src/hunter/indicators.py; rm -f "$BAK"; }
trap restore EXIT

echo "== ПРОБНИК 1: тело rsi умножается на 1.5 =="
python - <<'PY'
import pathlib
p = pathlib.Path('src/hunter/indicators.py'); s = p.read_text(encoding='utf-8')
old = 'return _expr(plta.rsi(pl.col("close"), timeperiod=period))'
assert old in s, 'тело rsi изменилось — пробник надо переписать'
p.write_text(s.replace(old, old + ' * 1.5'), encoding='utf-8')
PY
fail=0
for g in indicator_oracle formula_reference; do
  uv run --group gates python "gates/$g.py" >/dev/null 2>&1; c=$?
  echo "  $g -> $c (ожидается 1)"
  [ "$c" -eq 1 ] || fail=1
done
restore; trap - EXIT; BAK=$(mktemp); cp src/hunter/indicators.py "$BAK"; trap restore EXIT

echo "== ПРОБНИК 2: indicator_availability не вакуумен — период *3 =="
python - <<'PY'
import pathlib
p = pathlib.Path('src/hunter/indicators.py'); s = p.read_text(encoding='utf-8')
old = 'return _expr(plta.rsi(pl.col("close"), timeperiod=period))'
p.write_text(s.replace(old, 'return _expr(plta.rsi(pl.col("close"), timeperiod=period * 3))'),
             encoding='utf-8')
PY
uv run --group gates python gates/indicator_availability.py >/dev/null 2>&1; c=$?
echo "  indicator_availability -> $c (ожидается 1)"
[ "$c" -eq 1 ] || fail=1
restore; trap - EXIT

echo "== КОНТРОЛЬ: на чистом дереве все три зелёные =="
for g in indicator_oracle formula_reference indicator_availability; do
  uv run --group gates python "gates/$g.py" >/dev/null 2>&1; c=$?
  echo "  $g -> $c (ожидается 0)"
  [ "$c" -eq 0 ] || fail=1
done
echo "ИТОГ: $([ $fail -eq 0 ] && echo 'находка воспроизведена и правка подтверждена' || echo 'НЕ ВОСПРОИЗВЕЛОСЬ')"
exit $fail
