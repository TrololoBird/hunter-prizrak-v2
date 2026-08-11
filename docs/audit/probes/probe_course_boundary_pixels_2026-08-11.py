"""ЗОНД: где на рисунке КУРСА проходит линия границы базы — по внешнему краю или внутреннему.

Повод — вопрос владельца 2026-08-11: как ПОК может оказаться вне базы. Замер по нашим
данным ответа не дал: мера «доля ПОК внутри границы» МОНОТОННА ПО ШИРИНЕ (чем шире
граница, тем чаще ПОК внутри; «граница = весь график» дала бы 100%), поэтому выбирать
конвенцию по ней нельзя. Вопрос решается источником, а источник — рисунок.

Стр. 13 («СЛОВАРЬ ТРЕЙДЕРА», флэт): коробка флэта нарисована зелёными линиями, точки
границ пронумерованы 1…12. Вопрос ровно один: верхняя линия коробки проходит по САМОЙ
ВЫСОКОЙ из вершин или по более низкой?

Наш детектор берёт ВНУТРЕННИЙ край пары первых двух точек (`up_edge = min(hi_px[:2])`,
решение 2026-08-08), и из-за этого вторая точка пары автоматически становится проколом.

МЕТОД. Рисунок рендерится в высоком разрешении. Зелёные линии коробки и зелёные
ломаные-указатели различаются формой: линия коробки — ДЛИННЫЙ горизонтальный ряд
пикселей одного цвета. Свечи — красные и бирюзовые. Для полосы графика считается:
  * y горизонтальных зелёных линий (верх и низ коробки);
  * y самых высоких пикселей свечей внутри коробки (вершины хаёв) и самых низких (лои).
Сравнение этих y и есть ответ: если линия ВЫШЕ всех вершин, кроме проколов, — она по
внешнему краю; если ниже части вершин — по внутреннему.

⚠ КОНТРОЛЬ. Прибор обязан отличать линию от фона и свечей: печатается число найденных
линий и число столбцов со свечами. Ноль там или тут означает, что замер не состоялся, а
не что «линий нет».

Команда воспроизведения:
    uv run --group tools python docs/audit/probes/probe_course_boundary_pixels_2026-08-11.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
PDF = ROOT / "docs" / "course" / "Мини Курс по трейдингу от PrizrakTrade.pdf"
PAGE = 13
DPI = 300

GREEN_MIN_RUN = 60
"""Сколько подряд зелёных пикселей в строке считать ЛИНИЕЙ коробки, а не указателем."""


def is_green(r: int, g: int, b: int) -> bool:
    """Ярко-зелёный контур коробки: зелёный канал заметно выше обоих других."""
    return g > 120 and g - r > 50 and g - b > 50


def is_candle(r: int, g: int, b: int) -> bool:
    """Свеча: красная (r доминирует) либо бирюзовая (g и b вместе, но не зелёный контур)."""
    red = r > 110 and r - g > 40 and r - b > 40
    teal = g > 90 and b > 90 and g - r > 25 and b - r > 25 and abs(g - b) < 60
    return red or teal


def main() -> int:
    if not PDF.exists():
        print(f"ОТКАЗ: нет файла курса {PDF}")
        return 2
    doc = pymupdf.open(PDF)
    pix = doc[PAGE - 1].get_pixmap(dpi=DPI)
    w, h, n = pix.width, pix.height, pix.n
    buf = pix.samples

    def px(x: int, y: int) -> tuple[int, int, int]:
        i = (y * w + x) * n
        return buf[i], buf[i + 1], buf[i + 2]

    # 1. Горизонтальные зелёные линии — строки с длинным непрерывным зелёным пробегом.
    lines: list[tuple[int, int, int]] = []  # (y, x_от, длина)
    for y in range(h):
        run = start = 0
        for x in range(w):
            if is_green(*px(x, y)):
                run = run + 1 if run else 1
                if run == 1:
                    start = x
            else:
                if run >= GREEN_MIN_RUN:
                    lines.append((y, start, run))
                run = 0
        if run >= GREEN_MIN_RUN:
            lines.append((y, start, run))

    if not lines:
        print("ОТКАЗ: горизонтальных зелёных линий не найдено — замер не состоялся")
        return 2

    # ⚠ Отбор пары ПЕРЕПИСАН после того, как первая редакция выдала абсурд: «высота
    # коробки 5 px», «выход вниз 5180% высоты». Прибор поймал зелёную линию-подчёркивание
    # под заголовком страницы. Число, которое не может быть верным, — сигнал, что прибор
    # считает не то; здесь он ограничен полосой графика и обязан найти ПАРУ линий,
    # отстоящих друг от друга хотя бы на десятую часть высоты страницы.
    band_top, band_bottom = int(h * 0.10), int(h * 0.72)
    inside = [ln for ln in lines if band_top <= ln[0] <= band_bottom]
    if len(inside) < 2:
        print(f"ОТКАЗ: в полосе графика [{band_top}, {band_bottom}] найдено "
              f"{len(inside)} линий — коробку не собрать")
        return 2
    inside.sort(key=lambda t: -t[2])
    top = inside[0]
    pair = [ln for ln in inside if abs(ln[0] - top[0]) > h * 0.10]
    if not pair:
        print(f"ОТКАЗ: второй линии дальше {h * 0.10:.0f} px от первой (y={top[0]}) нет")
        return 2
    bottom = max(pair, key=lambda t: t[2])
    if bottom[0] < top[0]:
        top, bottom = bottom, top
    x_from, x_to = max(top[1], bottom[1]), min(top[1] + top[2], bottom[1] + bottom[2])

    # 2. Свечи внутри полосы коробки: для каждого столбца — самый верхний и нижний пиксель.
    tops: list[int] = []
    bottoms: list[int] = []
    cols = 0
    for x in range(x_from, x_to):
        ys = [y for y in range(max(top[0] - 260, 0), min(bottom[0] + 260, h))
              if is_candle(*px(x, y))]
        if not ys:
            continue
        cols += 1
        tops.append(min(ys))
        bottoms.append(max(ys))

    if not cols:
        print("ОТКАЗ: столбцов со свечами не найдено — замер не состоялся")
        return 2

    # 3. Сколько вершин ВЫШЕ верхней линии (в пикселях «выше» значит меньший y).
    above = sum(1 for t in tops if t < top[0] - 2)
    below = sum(1 for b in bottoms if b > bottom[0] + 2)
    hi_extreme = min(tops)
    lo_extreme = max(bottoms)

    print(f"стр. {PAGE}, рендер {DPI} dpi, размер {w}×{h}")
    print(f"найдено горизонтальных зелёных линий: {len(lines)} "
          f"(самая длинная {lines[0][2]} px)")
    print(f"столбцов со свечами внутри коробки: {cols}")
    print()
    print(f"верхняя линия коробки:      y = {top[0]}")
    print(f"самая высокая вершина свечи: y = {hi_extreme} "
          f"({top[0] - hi_extreme} px ВЫШЕ линии)")
    print(f"столбцов, где свеча выше линии: {above} из {cols} "
          f"({above / cols * 100:.1f}%)")
    print()
    print(f"нижняя линия коробки:       y = {bottom[0]}")
    print(f"самый низкий лой свечи:      y = {lo_extreme} "
          f"({lo_extreme - bottom[0]} px НИЖЕ линии)")
    print(f"столбцов, где свеча ниже линии: {below} из {cols} "
          f"({below / cols * 100:.1f}%)")
    print()
    height_px = bottom[0] - top[0]
    print(f"высота коробки: {height_px} px; выход вверх "
          f"{(top[0] - hi_extreme) / height_px * 100:.1f}% высоты, вниз "
          f"{(lo_extreme - bottom[0]) / height_px * 100:.1f}%")
    print()
    print("ЧТЕНИЕ: если бы линия шла по ВНЕШНЕМУ краю всех точек, столбцов со свечами "
          "за линией было бы около нуля. Если по ВНУТРЕННЕМУ — за линией оказывалась бы "
          "заметная доля вершин, а выход измерялся бы десятками процентов высоты.")
    dist = Counter(tops)
    print(f"КОНТРОЛЬ (прибор видит разные вершины, а не одну): различных y вершин "
          f"{len(dist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
