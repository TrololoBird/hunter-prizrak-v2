"""Выгрузка Telegram (result.json) → плоский текст: один блок на сообщение.

Формат блока: `=== id N | дата | фото...` затем текст. Entity-массив склеивается
как есть, без потерь текста (bold/italic/emoji — просто их text).
"""
import json
import sys
from pathlib import Path

src = Path("prizzrak-tg/result.json")  # запуск из корня репозитория
out = src.with_name("prizrak_tg_flat.txt")

data = json.loads(src.read_text(encoding="utf-8"))
blocks = []
for m in data["messages"]:
    if m.get("type") != "message":
        continue
    parts = m.get("text", "")
    if isinstance(parts, str):
        text = parts
    else:
        text = "".join(p if isinstance(p, str) else p.get("text", "") for p in parts)
    photo = m.get("photo", "")
    blocks.append(f"=== id {m['id']} | {m.get('date','')} | {photo}\n{text}\n")
out.write_text("\n".join(blocks), encoding="utf-8")
print(f"сообщений: {len(blocks)}; файл: {out}")
