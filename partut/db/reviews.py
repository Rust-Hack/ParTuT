"""
partut/db/reviews.py — отзывы покупателей.

Четвёртый кусок, вынесенный из db.py.

Отзыв принадлежит МОДЕЛИ, а не товару на точке: человек оценивал вещь, а не
факт её наличия в Турове. Поэтому снятие товара с точки чужих слов не уносит —
это правило и живёт здесь.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не
копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import json

from partut import db


def _ensure_review_columns():
    """Ответ продавца на отзыв. Спокойный ответ на тройку убеждает нового
    покупателя сильнее, чем её отсутствие.

    Плюс model_id: оценивают модель, а не наличие её на конкретной точке.
    Пока отзыв висел на товаре, один и тот же Elf Bar в Минске и Турове копил
    оценки раздельно — покупатель второй точки видел «отзывов пока нет» у
    товара, у которого их дюжина. product_id остаётся: он говорит, где именно
    человек покупал, и по нему же работают старые отзывы товаров без модели.
    """
    conn = db.connect()
    cur = conn.cursor()
    cols = db._table_columns(cur, "reviews")
    if "reply" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN reply TEXT")
    if "replied_at" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN replied_at TEXT")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN model_id INTEGER")
    # Проставляем модель уже написанным отзывам — иначе они останутся видны
    # только на той точке, где были оставлены.
    cur.execute("""UPDATE reviews SET model_id =
                     (SELECT p.model_id FROM products p WHERE p.id = reviews.product_id)
                   WHERE model_id IS NULL""")
    conn.commit()
    conn.close()


REVIEW_MAX_TEXT = 500


def reviewable_products(user_id):
    """Что этот человек может оценить: купил (заказ выдан) и ещё не оценивал.

    Право на отзыв даёт покупка, а не желание высказаться: иначе конкурент
    поставит единицу, не потратив ни рубля, а оценка товара перестанет
    что-либо значить."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT items FROM orders WHERE user_id = %s AND status = 'issued'"), (user_id,))
    bought = {}
    for r in cur.fetchall():
        try:
            for it in json.loads(r["items"]):
                pid = int(it.get("id", 0))
                if pid:
                    bought[pid] = it.get("name", "")
        except (TypeError, ValueError):
            pass
    if not bought:
        conn.close()
        return []
    marks = ",".join(["%s"] * len(bought))
    # Названия берём из живого каталога: в старом заказе товар мог называться иначе.
    cur.execute(db._q(f"SELECT id, name, model_id FROM products WHERE id IN ({marks})"), tuple(bought.keys()))
    live = {r["id"]: (r["name"], r["model_id"]) for r in cur.fetchall()}
    cur.execute(db._q("SELECT product_id, model_id FROM reviews WHERE user_id = %s"), (user_id,))
    rated_products, rated_models = set(), set()
    for r in cur.fetchall():
        rated_products.add(r["product_id"])
        if r["model_id"]:
            rated_models.add(r["model_id"])
    conn.close()

    out, seen_models = [], set()
    for pid in bought:
        if pid not in live:
            continue
        name, mid = live[pid]
        # Оценивают модель: уже оценил её на другой точке — второй раз не предлагаем.
        # И один и тот же товар с двух точек не показываем дважды в одном списке.
        if mid and (mid in rated_models or mid in seen_models):
            continue
        if not mid and pid in rated_products:
            continue
        if mid:
            seen_models.add(mid)
        out.append({"id": pid, "name": name or bought[pid]})
    return out


def add_review(product_id, user_id, rating, text="", username=""):
    """Сохраняет отзыв в статусе «на модерации». Возвращает id или None, если уже оценивал."""
    conn = db.connect()
    cur = conn.cursor()
    mid = db._model_of(cur, product_id)
    # Один человек — один отзыв на модель. Иначе один и тот же покупатель
    # оценил бы её отдельно в Минске и отдельно в Турове.
    if mid:
        cur.execute(db._q("SELECT 1 AS x FROM reviews WHERE user_id = %s AND model_id = %s LIMIT 1"),
                    (user_id, mid))
    else:
        cur.execute(db._q("SELECT 1 AS x FROM reviews WHERE user_id = %s AND product_id = %s LIMIT 1"),
                    (user_id, product_id))
    if cur.fetchone():
        conn.close()
        return None
    rid = db._insert_id(cur, "INSERT INTO reviews (product_id, model_id, user_id, username, rating, text, status, created_at) "
                          "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)",
                     (product_id, mid, user_id, (username or "")[:64], int(rating),
                      (text or "").strip()[:REVIEW_MAX_TEXT], db._now_str()))
    conn.commit()
    conn.close()
    return rid


def list_reviews(product_id, status="approved", limit=50):
    """Отзывы о модели этого товара: на всех точках это одна и та же вещь.

    Для товара без модели — как раньше, по самому товару."""
    conn = db.connect()
    cur = conn.cursor()
    mid = db._model_of(cur, product_id)
    if mid:
        cur.execute(db._q("SELECT * FROM reviews WHERE model_id = %s AND status = %s ORDER BY id DESC LIMIT %s"),
                    (mid, status, limit))
    else:
        cur.execute(db._q("SELECT * FROM reviews WHERE product_id = %s AND model_id IS NULL "
                       "AND status = %s ORDER BY id DESC LIMIT %s"),
                    (product_id, status, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def list_reviews_by_user(user_id, limit=50):
    """Отзывы одного человека — чтобы показать ему его же оценку и её судьбу."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM reviews WHERE user_id = %s ORDER BY id DESC LIMIT %s"), (user_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def admin_reviews(status="pending", limit=100):
    """Отзывы для админа. status='all' — все, включая опубликованные и скрытые.

    Раньше админ видел только очередь на модерацию: опубликованный отзыв
    исчезал из его поля зрения навсегда, и убрать его было уже нельзя."""
    conn = db.connect()
    cur = conn.cursor()
    # Имя берём из модели, а не из товара: товар могли снять с точки, и тогда
    # отзыв в очереди оказывался безымянным — модерировать вслепую нельзя.
    sql = ("SELECT r.*, COALESCE(m.name, p.name) AS product_name FROM reviews r "
           "LEFT JOIN products p ON p.id = r.product_id "
           "LEFT JOIN models m ON m.id = r.model_id ")
    if status and status != "all":
        cur.execute(db._q(sql + "WHERE r.status = %s ORDER BY r.id DESC LIMIT %s"), (status, limit))
    else:
        cur.execute(db._q(sql + "ORDER BY r.id DESC LIMIT %s"), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def pending_reviews(limit=50):
    """Ждут решения — их видит админ."""
    return admin_reviews("pending", limit)


def delete_review(review_id):
    """Убирает отзыв насовсем. «Скрыть» оставляет запись (можно вернуть),
    удаление — для мусора, который держать незачем."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM reviews WHERE id = %s"), (review_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def set_review_reply(review_id, text):
    """Ответ магазина на отзыв. Пустой текст убирает ответ."""
    text = (text or "").strip()[:REVIEW_MAX_TEXT]
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE reviews SET reply = %s, replied_at = %s WHERE id = %s"),
                (text or None, (db._now_str() if text else None), review_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def count_pending_reviews():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM reviews WHERE status = 'pending'")
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def set_review_status(review_id, status):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE reviews SET status = %s WHERE id = %s"), (status, review_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_review(review_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM reviews WHERE id = %s"), (review_id,))
    row = cur.fetchone()
    conn.close()
    return row
