"""
Общая настройка для тестов: временная SQLite-база + импорт server со стабами.

Тесты НЕ ходят в сеть и НЕ трогают боевую базу:
  • DATABASE_URL пустой → db работает на временном SQLite-файле;
  • tg.send_message / send_photo подменены на заглушки, которые пишут в SENT.

Запуск всех тестов:  python tests/run_all.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = ""      # локальный SQLite (не Postgres)
os.environ["DEV_MODE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db                             # noqa: E402
db.SQLITE_FILE = tempfile.mktemp(suffix=".db")
db.init_db()

import server                        # noqa: E402
import notifications                 # noqa: E402

# Заглушки Telegram: копим отправленное, ничего не шлём наружу.
SENT = []   # список (chat_id, text, parse_mode)


def _send_message(cid, text, **kw):
    SENT.append((cid, text, kw.get("parse_mode")))


def _send_photo(cid, *a, **kw):
    SENT.append((cid, kw.get("caption", ""), kw.get("parse_mode")))
    return type("M", (), {"photo": [type("P", (), {"file_id": "fid"})()]})()


server.tg.send_message = _send_message
server.tg.send_photo = _send_photo
notifications.notify_sellers = lambda *a, **k: None   # не шумим при создании заказа

client = server.app.test_client()


def reset_sent():
    SENT.clear()


def as_user(uid, username=None, first_name=None):
    """Следующие запросы будут «от» этого клиента."""
    server.get_user = lambda init: {"id": uid, "username": username, "first_name": first_name}


def as_admin(uid=100, username="seller"):
    """Админ-эндпоинты будут считать запрос от этого админа."""
    server.get_admin = lambda init: {"id": uid, "username": username}


class Checker:
    """Копит результаты проверок и печатает их по ходу."""
    def __init__(self, title=""):
        if title:
            print(f"\n=== {title} ===")
        self.fails = []

    def __call__(self, name, cond):
        print(("✅" if cond else "❌") + " " + name)
        if not cond:
            self.fails.append(name)
        return cond
