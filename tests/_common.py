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


def _photo_size(file_id, width):
    return type("P", (), {"file_id": file_id, "width": width})()


def _send_photo(cid, *a, **kw):
    SENT.append((cid, kw.get("caption", ""), kw.get("parse_mode")))
    # Telegram возвращает несколько размеров одной картинки — самый большой последним.
    sizes = [_photo_size("fid_s", 90), _photo_size("fid_m", 800), _photo_size("fid", 1280)]
    return type("M", (), {"photo": sizes})()


server.tg.send_message = _send_message
server.tg.send_photo = _send_photo


def _send_document(cid, *a, **kw):
    SENT.append((cid, kw.get("caption", ""), kw.get("parse_mode")))


# У бота СВОЙ экземпляр telebot, и заглушки server.tg его не покрывают: без
# этого тест, дёрнувший функцию бота, уходит в настоящий Telegram с боевым
# токеном. Глушим здесь, чтобы об этом нельзя было забыть в отдельном модуле.
os.environ["WEBAPP_URL"] = ""          # иначе bot при импорте лезет в сеть за меню
import bot as _bot                     # noqa: E402
_bot.bot.send_message = _send_message
_bot.bot.send_photo = _send_photo
_bot.bot.send_document = _send_document
_bot.bot.reply_to = lambda msg, text, **kw: SENT.append((getattr(msg, "chat", None), text, None))
notifications.notify_sellers = lambda *a, **k: None   # не шумим при создании заказа

client = server.app.test_client()


def reset_sent():
    SENT.clear()


# Настоящие проверки прав — до того, как их подменит as_admin(). Тест про
# права обязан звать именно их, иначе он проверяет заглушку.
REAL_GET_ADMIN = server.get_admin
REAL_GET_USER = server.get_user


def real_auth():
    """Вернуть подлинную проверку прав (для тестов доступа)."""
    server.get_admin = REAL_GET_ADMIN
    server.get_user = REAL_GET_USER


def as_user(uid, username=None, first_name=None):
    """Следующие запросы будут «от» этого клиента."""
    server.get_user = lambda init: {"id": uid, "username": username, "first_name": first_name}


def as_admin(uid=100, username="seller"):
    """Админ-эндпоинты будут считать запрос от этого админа."""
    server.get_admin = lambda init: {"id": uid, "username": username}


def deny_admin():
    """Снять права админа. Нужен всегда, когда проверяем «постороннего»:
    as_admin() подменяет проверку НАВСЕГДА, и без этого тест на запрет проходит
    от имени админа — то есть проверяет не то, что написано."""
    server.get_admin = lambda init: None


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
