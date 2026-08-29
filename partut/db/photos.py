"""
partut/db/photos.py — картинки магазина: витрина, галерея, кэш скачанного.

Третий кусок, вынесенный из db.py.

Здесь же граница, которую легко потерять из виду: картинки товаров и призов
магазин держит у себя (их немного, смотрят их все), а чеки об оплате — нет
(по штуке на заказ, смотрит один продавец один раз). За эту границу отвечает
is_shop_photo, и от неё же зависит ночная уборка: она сносит картинки, на
которые никто не ссылается.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не
копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime

from partut import db


def _ensure_photo_columns():
    """Галерея переехала с товара на модель: коробка одна и та же на всех точках,
    а фото у каждого наличия отдельно — это те же снимки в трёх экземплярах."""
    conn = db.connect()
    cur = conn.cursor()
    cols = db._table_columns(cur, "product_photos")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE product_photos ADD COLUMN model_id INTEGER")
        # Прежние фото товаров переносим на их модели.
        cur.execute("UPDATE product_photos SET model_id = ("
                    "SELECT model_id FROM products WHERE products.id = product_photos.product_id)")
    conn.commit()
    conn.close()


def get_photo_blob(file_id):
    """Картинка из базы: (данные, content_type). None — если её там ещё нет."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT data, content_type FROM photo_blobs WHERE file_id = %s"), (file_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    # Postgres отдаёт BYTEA как memoryview — Flask нужен обычный bytes.
    return bytes(row["data"]), row["content_type"]


def save_photo_blob(file_id, content_type, data):
    """Кладёт скачанную картинку в базу, чтобы больше не ходить за ней в Telegram."""
    payload = db.psycopg2.Binary(data) if db.USE_PG else db.sqlite3.Binary(data)
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute(
            """INSERT INTO photo_blobs (file_id, content_type, data, size, created_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (file_id) DO NOTHING""",
            (file_id, content_type, payload, len(data), db._now_str()),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO photo_blobs (file_id, content_type, data, size, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, content_type, payload, len(data), db._now_str()),
        )
    conn.commit()
    conn.close()


def is_shop_photo(file_id):
    """Это картинка магазина (витрина), а не чек об оплате?

    Картинки витрины стоит хранить у себя: их немного и их смотрят все
    покупатели. Чеки — наоборот, по штуке на заказ, и смотрит их один продавец
    один раз, поэтому в базу они не попадают, чтобы не забить бесплатное место.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT 1 AS x FROM products WHERE photo = %s OR photo_thumb = %s LIMIT 1"),
                (file_id, file_id))
    found = cur.fetchone() is not None
    if not found:
        # Дополнительные фото — такие же картинки товара: их тоже держим у себя,
        # иначе галерея после перезапуска качалась бы из Telegram заново.
        cur.execute(db._q("SELECT 1 AS x FROM product_photos WHERE file_id = %s OR thumb_id = %s LIMIT 1"),
                    (file_id, file_id))
        found = cur.fetchone() is not None
    if not found:
        # Фото приза в розыгрыше — тоже витрина: его смотрят все. Своя картинка
        # у каждого места (photo1/2/3); старая общая колонка (photo) проверяется
        # заодно — на случай записи, которую миграция ещё не перенесла.
        cur.execute(db._q("SELECT 1 AS x FROM raffles "
                          "WHERE photo = %s OR photo1 = %s OR photo2 = %s OR photo3 = %s LIMIT 1"),
                    (file_id, file_id, file_id, file_id))
        found = cur.fetchone() is not None
    conn.close()
    return found


# Прежнее имя: функция говорила про товар, а картинки витрины бывают не только
# у товаров. Оставлено, чтобы не ломать вызовы со стороны.
is_product_photo = is_shop_photo


MAX_EXTRA_PHOTOS = 5      # плюс главное фото — шесть картинок на карточку


def model_photos(model_id):
    """Галерея модели. Фото — свойство самого товара, а не точки: на всех
    точках это одна и та же коробка."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM product_photos WHERE model_id = %s ORDER BY sort, id"), (model_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def all_model_photos():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_photos WHERE model_id IS NOT NULL ORDER BY model_id, sort, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_model_photo(model_id, file_id, thumb_id=""):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c, COALESCE(MAX(sort), 0) AS mx FROM product_photos WHERE model_id = %s"),
                (model_id,))
    row = cur.fetchone()
    if int(row["c"]) >= MAX_EXTRA_PHOTOS:
        conn.close()
        return None
    pid = db._insert_id(cur, "INSERT INTO product_photos (product_id, model_id, file_id, thumb_id, sort) "
                          "VALUES (%s, %s, %s, %s, %s)",
                     (0, model_id, file_id, thumb_id or "", int(row["mx"]) + 1))
    conn.commit()
    conn.close()
    return pid


def get_product_photos(product_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM product_photos WHERE product_id = %s ORDER BY sort, id"), (product_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def all_product_photos():
    """Все дополнительные фото разом — витрине нужен один поход в базу, а не по одному на товар."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_photos ORDER BY product_id, sort, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_product_photo(product_id, file_id, thumb_id=""):
    """Добавляет фото в галерею. Возвращает id записи или None, если места больше нет.

    Ограничение — не формальность: каждая картинка едет покупателю по мобильному
    интернету, и десяток фото превращает карточку в долгую загрузку."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c, COALESCE(MAX(sort), 0) AS mx FROM product_photos WHERE product_id = %s"),
                (product_id,))
    row = cur.fetchone()
    if int(row["c"]) >= MAX_EXTRA_PHOTOS:
        conn.close()
        return None
    pid = db._insert_id(cur, "INSERT INTO product_photos (product_id, file_id, thumb_id, sort) VALUES (%s, %s, %s, %s)",
                     (product_id, file_id, thumb_id or "", int(row["mx"]) + 1))
    conn.commit()
    conn.close()
    return pid


def delete_product_photo(photo_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM product_photos WHERE id = %s"), (photo_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def purge_orphan_photos(limit=200):
    """Убирает картинки, на которые больше никто не ссылается.

    Товар снимают с точки — строки о нём уходят, а картинка оставалась в базе
    навсегда. Место на бесплатной базе кончается тихо, и заметить это можно
    было бы только когда магазин перестанет принимать заказы.

    Ошибиться тут почти нечем: file_id в Telegram остаётся рабочим, и удалённая
    по недосмотру картинка просто скачается заново при первом показе. Поэтому
    достаточно одного условия — на неё никто не ссылается.

    Разом убираем не больше limit штук: ночная уборка не должна держать базу.
    Возвращает, сколько убрано.
    """
    # Сутки форы: картинка появляется в базе следом за товаром, и уборка не
    # должна успеть между этими двумя действиями.
    cutoff = (db.shop_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    # NOT IN и NULL несовместимы: один NULL в списке — и условие не выполнится
    # НИ ДЛЯ ОДНОЙ строки, уборка молча перестанет работать. Отсюда IS NOT NULL.
    cur.execute(db._q("""
        DELETE FROM photo_blobs WHERE file_id IN (
            SELECT file_id FROM photo_blobs
             WHERE (created_at IS NULL OR created_at < %s)
               AND file_id NOT IN (SELECT photo FROM products WHERE photo IS NOT NULL)
               AND file_id NOT IN (SELECT photo_thumb FROM products WHERE photo_thumb IS NOT NULL)
               AND file_id NOT IN (SELECT file_id FROM product_photos WHERE file_id IS NOT NULL)
               AND file_id NOT IN (SELECT thumb_id FROM product_photos WHERE thumb_id IS NOT NULL)
               AND file_id NOT IN (SELECT photo FROM raffles WHERE photo IS NOT NULL)
               AND file_id NOT IN (SELECT photo1 FROM raffles WHERE photo1 IS NOT NULL)
               AND file_id NOT IN (SELECT photo2 FROM raffles WHERE photo2 IS NOT NULL)
               AND file_id NOT IN (SELECT photo3 FROM raffles WHERE photo3 IS NOT NULL)
             LIMIT %s)
    """), (cutoff, limit))
    gone = cur.rowcount
    conn.commit()
    conn.close()
    return max(0, gone)


def photo_blob_stats():
    """Сколько картинок лежит в базе и сколько места занимают (для админ-статистики)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM photo_blobs")
    row = cur.fetchone()
    conn.close()
    return {"count": int(row["n"]), "bytes": int(row["bytes"])}


def set_model_photo(model_id, file_id, thumb_id=""):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE models SET photo = %s, photo_thumb = %s WHERE id = %s"),
                (file_id, thumb_id or "", model_id))
    conn.commit()
    conn.close()
    return db.propagate_model(model_id)
