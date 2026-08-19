"""КОНТРОЛЬ: заперт ли `first_verdict` в одном ответе. Три ряда — три разных вердикта."""
from hunter.models import Bar
from hunter.breach import first_breach, first_verdict, BreachKind, Direction
L, TF = 100.0, "5m"
mk = lambda spec: [Bar(open_ms=i*300_000, open=o, high=h, low=l, close=c, volume=1.0)
                   for i, (o, h, l, c) in enumerate(spec)]
under = [(99.0, 99.5, 98.0, 99.0)]              # целиком под уровнем
# заход: ОДНО полное тело за уровнем, потом две свечи тенью за уровнем, поздний возврат
once = under*2 + [(100.5, 101.0, 100.2, 100.8),   # тело ЗА уровнем — run 1
                  (100.2, 100.6,  99.7,  99.8),   # тень за, тело нет — run сброшен
                  ( 99.8, 100.4,  99.6,  99.9),   # тень за, тело нет
                  ( 99.9,  99.95, 99.5,  99.7)]   # вернулась: 3 свечи от начала > окна 2
once += under*5
twice = once + [(100.5, 101.0, 100.3, 100.9),     # второй заход: тело 1
                (100.9, 101.4, 100.7, 101.2)]     # тело 2 -> пробой
# ⚠ Четвёртый случай добавлен аудитом 2026-08-19: самый ЧАСТЫЙ новый исход правки —
# `unresolved → puncture` (отработанных стало +53), а прибор его не показывал вовсе.
# Контроль, не покрывающий главный переход, доказывает не то, ради чего поставлен.
punct = once + [(99.9, 100.4, 99.8, 99.85)] + under*3  # тень ЗА, закрылась под — прокол
flat = under*10
for name, spec, want_fb, want_fv in (
    ("один заход, тел 1, поздний возврат, дальше тишина", once,
     BreachKind.UNRESOLVED, BreachKind.UNRESOLVED),
    ("то же + ВТОРОЙ заход на двух телах", twice,
     BreachKind.UNRESOLVED, BreachKind.BREAKOUT),
    ("то же + ВТОРОЙ заход тенью с возвратом", punct,
     BreachKind.UNRESOLVED, BreachKind.PUNCTURE),
    ("цена за уровень не заходила вовсе", flat, None, None),
):
    b = mk(spec)
    fb = first_breach(b, L, Direction.ABOVE, TF)
    fv = first_verdict(b, L, Direction.ABOVE, TF)
    gb, gv = (fb.kind if fb else None), (fv.kind if fv else None)
    ok = gb is want_fb and gv is want_fv
    print(f'{"ok   " if ok else "ПЛОХО"} {name}')
    print(f'        first_breach={gb.value if gb else "None":<10} '
          f'first_verdict={gv.value if gv else "None"}')
    assert ok, (gb, gv)
print()
print('ПРОЙДЕНО: прибор отдаёт unresolved, breakout, puncture и None на разных данных — в одном')
print("          ответе не заперт, и от first_breach отличается РОВНО теми случаями, ради")
print("          которых заведён — вторым и третьим.")
