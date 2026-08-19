"""
tgsend.py — всё, что уходит наружу: сообщения людям и фоновые задачи.

Листовой по отношению к серверу: знает config и errors, но про приложение и
про ручки не знает ничего. Отправка нужна и веб-серверу, и боту, и пока она
жила в server.py, за одним send_message тянулось всё приложение целиком.

Здесь же живёт _bg — потому что почти всё, что уходит в фон, уходит именно в
Telegram, а Telegram отвечает через сеть. Ответ покупателю не должен ждать
чужого сервера: заказ к этому моменту уже в базе.
"""

import html
import threading

import telebot

from partut import errors
from partut.config import BOT_TOKEN

# Отдельный экземпляр бота — ТОЛЬКО чтобы отправлять сообщения и картинки.
# Опрос обновлений ведёт partut/bot/handlers.py, здесь его нет и быть не должно.
tg = telebot.TeleBot(BOT_TOKEN)

try:
    BOT_USERNAME = tg.get_me().username
except Exception as e:
    print(f"Не смог узнать имя бота: {e}")
    BOT_USERNAME = ""


def bg(fn, *args, **kwargs):
    """Запускает побочный эффект в фоне — чтобы ответ клиенту не ждал сети.

    Молчаливое падение здесь означало бы «заказ пришёл, но никто о нём не
    узнал» — худший сорт поломки, поэтому о сбое сообщаем владельцу.
    """
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            errors.report(tg, f"фоновая задача {getattr(fn, '__name__', fn)}", e)
    threading.Thread(target=_run, daemon=True).start()


def contact_link(username, uid, label=None):
    """HTML-ссылка «открыть чат в ТГ» по @username (t.me) или по id (tg://user).
    label — текст ссылки (по умолчанию @username или имя/id)."""
    username = (username or "").lstrip("@").strip()
    if username:
        url = f"https://t.me/{username}"
        text = label or f"@{username}"
    else:
        url = f"tg://user?id={uid}"
        text = label or str(uid)
    return f'<a href="{url}">{html.escape(text)}</a>'


def notify_client(user_id, text):
    """Сообщение клиенту (не роняем запрос, если он заблокировал бота)."""
    if not text:
        return
    try:
        tg.send_message(int(user_id), text)
    except Exception as e:
        print(f"Не смог уведомить клиента {user_id}: {e}")


def notify_new_admin(uid, city):
    """Сообщаем человеку, что доступ выдан: иначе он не узнает, что теперь админ."""
    where = f" по точке «{city}»" if city else ""
    try:
        tg.send_message(uid, f"🛠 Вам выдали доступ продавца{where}.\n"
                             f"Откройте приложение — появится раздел «Управление».")
    except Exception as e:
        print(f"Не смог уведомить нового админа {uid}: {e}")
