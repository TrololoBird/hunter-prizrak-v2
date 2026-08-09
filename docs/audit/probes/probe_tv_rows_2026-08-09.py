"""ЗОНД: сколько строк у фиксированного профиля объёма НА СКРИНШОТАХ АВТОРА КУРСА.

Вопрос возник при переносе инструмента TV (tv-transfer-2026-08-09): официального
умолчания Row Size нет, живого TV у владельца нет — референт снимается с экрана автора,
то есть со страниц самого курса (истина №1 по иерархии §0.1).

Метод: из PDF извлекается ВСТРОЕННЫЙ скриншот страницы в нативном разрешении (рендер
размывает); бирюзовая рамка коробки инструмента находится по цвету; в полосе правее
левой грани считаются группы ярких (белый/жёлтый/оранжевый) горизонтальных штрихов —
строки профиля. Шаг строки в пикселях печатается: шаг ≤ 2.5 px — предел разрешения
скриншота, и число групп там НИЖНЯЯ ГРАНИЦА, а не счёт.

⚠ Автопоиск печатает ВСЕ бирюзовые рамки, включая ложные (подсветка пункта меню на
стр. 26 x≈554 — группы там это буквы текста; шкалы). Содержательные коробки сверены
глазами по кропам: стр. 26 x=918 (~22-24 строки), стр. 31 x=321 (≥95), стр. 33 x=443
(≥64), стр. 36 x=1329 (~50). Интерпретация — в докстроке `volume_profile.TV_ROWS`.

Запуск:
    uv run --group tools python docs/audit/probes/probe_tv_rows_2026-08-09.py
"""

from __future__ import annotations

import sys

import numpy as np
import pymupdf

PDF = "docs/course/Мини Курс по трейдингу от PrizrakTrade.pdf"
PAGES = (26, 31, 33, 36)


def analyze(doc: pymupdf.Document, page_no: int) -> list[tuple[int, int, int, float]]:
    page = doc[page_no - 1]
    pix = None
    for info in page.get_images(full=True):
        cand = pymupdf.Pixmap(doc, info[0])
        if cand.width > 1500:
            pix = cand
            break
    if pix is None:
        return []
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3).astype(int)
    teal = (a[:, :, 1] > 190) & (a[:, :, 2] > 140) & (a[:, :, 0] < 140)
    vc = sorted(((x, int(teal[:, x].sum())) for x in range(a.shape[1])),
                key=lambda t: -t[1])[:6]
    out: list[tuple[int, int, int, float]] = []
    seen: set[int] = set()
    for x_edge, length in vc:
        if length < 80 or any(abs(x_edge - s) < 30 for s in seen):
            continue
        seen.add(x_edge)
        ys = np.where(teal[:, x_edge])[0]
        top, bot = int(ys.min()), int(ys.max())
        if bot - top < 60:
            continue
        seg = a[top:bot, x_edge + 2:x_edge + 90]
        bright = (seg.sum(axis=2) > 430)
        rowmask = bright.any(axis=1)
        starts = [i for i in range(1, len(rowmask))
                  if rowmask[i] and not rowmask[i - 1]]
        if len(starts) > 5:
            out.append((x_edge, bot - top, len(starts),
                        round((bot - top) / len(starts), 2)))
    return out


def main() -> int:
    doc = pymupdf.open(PDF)
    any_found = False
    for page in PAGES:
        res = analyze(doc, page)
        for x, height, rows, pitch in res:
            any_found = True
            note = " (⚠ шаг у предела разрешения — счёт есть нижняя граница)" \
                if pitch <= 2.5 else ""
            print(f"стр. {page}: коробка x={x}, высота {height} px, "
                  f"групп строк {rows}, шаг {pitch} px{note}")
        if not res:
            print(f"стр. {page}: коробка с профилем не найдена автопоиском")
    if not any_found:
        print("ПРОВАЛ: ни одного профиля не найдено — метод сломан или PDF другой")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
