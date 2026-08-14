"""
Восстановление магазина из резервной копии.

Копию присылает бот раз в сутки в личку владельцу (и по команде /backup).
Это файл вида partut-2026-08-14_0400.json.gz.

Как пользоваться:

    venv/bin/python tools/restore.py ~/Downloads/partut-2026-08-14_0400.json.gz
        — только показывает, что внутри, и НИЧЕГО не меняет;

    venv/bin/python tools/restore.py файл.json.gz --yes
        — заливает копию в базу, ЗАМЕНЯЯ её содержимое.

Куда именно зальётся, определяет DATABASE_URL в .env: пусто — локальный
shop.db, заполнено — облачная база. Скрипт печатает это перед работой, чтобы
случайно не залить копию поверх боевого магазина.
"""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db     # noqa: E402


def load(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_it = "--yes" in sys.argv
    if not args:
        print(__doc__)
        return 1

    path = args[0]
    if not os.path.exists(path):
        print(f"Файл не найден: {path}")
        return 1

    data = load(path)
    target = "облачная база (DATABASE_URL)" if db.USE_PG else f"локальный файл {db.SQLITE_FILE}"

    print(f"Копия:  {path}")
    print(f"Куда:   {target}")
    print("Содержимое копии:")
    for table, rows in sorted(data.items()):
        print(f"   {table:18} {len(rows):>6} строк")

    if not apply_it:
        print("\nЭто предварительный просмотр — ничего не изменено.")
        print("Чтобы залить копию, повторите команду с --yes")
        return 0

    print("\nСоздаю недостающие таблицы…")
    db.init_db()
    print("Заливаю данные (прежнее содержимое таблиц удаляется)…")
    report = db.import_tables(data, wipe=True)
    for table, result in sorted(report.items()):
        print(f"   {table:18} {result}")
    print("\nГотово. Перезапустите сервис, чтобы сбросить кэши в памяти.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
