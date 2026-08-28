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

# Магазин работает на Postgres, а тесты по умолчанию — на SQLite: он не требует
# установки и стартует мгновенно. Но диалекты расходятся (регистр в LIKE, строгий
# GROUP BY, ON CONFLICT вместо INSERT OR IGNORE), и такое расхождение весь набор
# тестов не увидит в принципе. Поэтому перед релизом тот же набор гоняется на
# настоящем Postgres: TEST_DATABASE_URL=postgresql://... python tests/run_all.py
_PG = os.environ.get("TEST_DATABASE_URL", "").strip()
os.environ["DATABASE_URL"] = _PG     # пусто — локальный SQLite
os.environ["DEV_MODE"] = "0"
# Кто в этом прогоне разработчик и владелец. Раньше номер был вшит в config, и
# тесты молча опирались на него — то есть проверяли не правила доступа, а
# конкретного человека. Теперь он назван здесь, и видно, что это стенд.
os.environ["DEV_IDS"] = "716030279"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from partut import limits  # noqa: E402
# Проверки шлют запросы очередями, живой человек так не умеет. Антиспам этого
# не различает и справедливо отказывает — а проверяем мы магазин, не терпение.
# Сами паузы проверяются отдельно, в tests/test_limits.py.
limits.выключить()

from partut import db  # noqa: E402
if _PG:
    # Чистим схему целиком: тесты рассчитывают на пустую базу, а прошлый прогон
    # мог оставить и таблицы, и данные.
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.commit(); conn.close()
else:
    db.SQLITE_FILE = tempfile.mktemp(suffix=".db")
db.init_db()

from partut.web import auth  # noqa: E402
from partut.web import server  # noqa: E402
from partut.integrations import tgsend  # noqa: E402
from partut import notifications  # noqa: E402

# Заглушки Telegram: копим отправленное, ничего не шлём наружу.
class _Отправленное(list):
    """Список отправленного, который ДОЖИДАЕТСЯ фоновых дел при чтении.

    Уведомления магазин шлёт в фоне, и это правильно: ответ покупателю не
    должен ждать Telegram. Но проверка, читающая этот список сразу после
    запроса, полагалась бы на то, что фоновый поток успел раньше следующей
    строчки, — то есть проходила бы или нет в зависимости от загрузки машины.
    Так и было: тесты держались на расторопности планировщика.

    Ждём именно при ЧТЕНИИ, а не после запроса: иначе сломалась бы проверка,
    которая как раз и меряет, что ответ приходит быстро, не дожидаясь Telegram.

    Забыть про ожидание нельзя: оно не в тестах, а здесь.
    """

    def _дождаться(self):
        tgsend.дождаться_фона()

    def __iter__(self):
        self._дождаться()
        return list.__iter__(self)

    def __len__(self):
        self._дождаться()
        return list.__len__(self)

    def __getitem__(self, i):
        self._дождаться()
        return list.__getitem__(self, i)

    def __contains__(self, x):
        self._дождаться()
        return list.__contains__(self, x)

    def __repr__(self):
        self._дождаться()
        return list.__repr__(self)


SENT = _Отправленное()   # список (chat_id, text, parse_mode)


def _send_message(cid, text, **kw):
    SENT.append((cid, text, kw.get("parse_mode")))


def _photo_size(file_id, width):
    return type("P", (), {"file_id": file_id, "width": width})()


def _send_photo(cid, *a, **kw):
    SENT.append((cid, kw.get("caption", ""), kw.get("parse_mode")))
    # Telegram возвращает несколько размеров одной картинки — самый большой последним.
    sizes = [_photo_size("fid_s", 90), _photo_size("fid_m", 800), _photo_size("fid", 1280)]
    return type("M", (), {"photo": sizes})()


tgsend.tg.send_message = _send_message
tgsend.tg.send_photo = _send_photo


def _send_document(cid, *a, **kw):
    # Файл кладём целиком: выгрузку проверяют по содержимому, а не по факту
    # отправки. a[0] — файловый объект, каким его получает Telegram.
    тело = a[0].read() if a and hasattr(a[0], "read") else b""
    SENT.append((cid, kw.get("caption", ""), kw.get("parse_mode")))
    ДОКУМЕНТЫ.append((cid, getattr(a[0], "name", "") if a else "", тело))


ДОКУМЕНТЫ = []       # (chat_id, имя файла, байты) — что ушло документом


# Заглушка нужна ОБОИМ экземплярам бота. tgsend.tg — тот, через который шлёт
# веб-сервер: без подмены выгрузка статистики в тестах ушла бы в настоящий
# Telegram с боевым токеном из .env.
tgsend.tg.send_document = _send_document


# У бота СВОЙ экземпляр telebot, и заглушки tgsend.tg его не покрывают: без
# этого тест, дёрнувший функцию бота, уходит в настоящий Telegram с боевым
# токеном. Глушим здесь, чтобы об этом нельзя было забыть в отдельном модуле.
os.environ["WEBAPP_URL"] = ""          # иначе bot при импорте лезет в сеть за меню
from partut.bot import handlers as _bot  # noqa: E402
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
REAL_GET_ADMIN = auth.get_admin
REAL_GET_USER = auth.get_user


def real_auth():
    """Вернуть подлинную проверку прав (для тестов доступа)."""
    auth.get_admin = REAL_GET_ADMIN
    auth.get_user = REAL_GET_USER


def as_user(uid, username=None, first_name=None):
    """Следующие запросы будут «от» этого клиента."""
    auth.get_user = lambda init: {"id": uid, "username": username, "first_name": first_name}


def as_admin(uid=100, username="owner", role="owner", city=""):
    """Админ-эндпоинты будут считать запрос от этого админа.

    По умолчанию — владелец: большинство тестов проверяют операции магазина.
    Роль обязана быть в заглушке, иначе проверка прав увидит админа без роли
    и откажет — а тест решит, что сломалась сама операция."""
    auth.get_admin = lambda init: {"id": uid, "username": username, "role": role, "city": city}


def deny_admin():
    """Снять права админа. Нужен всегда, когда проверяем «постороннего»:
    as_admin() подменяет проверку НАВСЕГДА, и без этого тест на запрет проходит
    от имени админа — то есть проверяет не то, что написано."""
    auth.get_admin = lambda init: None


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
