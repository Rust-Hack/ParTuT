"""
partut/db/favorites.py — избранное покупателя на сервере.

Раньше избранное жило только в localStorage браузера: пропадало при смене
устройства или переустановке Telegram, а владелец не видел вовсе, что
покупатели откладывают на потом — хотя ровно такой же сигнал спроса для
«Жду поступления» (stock_alerts) виден и продавцу, и владельцу.

Пара (товар, покупатель) уникальна — повторный тап по сердечку не плодит
дубли и просто ничего не делает.

Примитивы берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не копиями имён:
копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

from partut import db


def add_favorite(product_id, user_id):
    """Покупатель отложил товар. Повторное нажатие не создаёт дубль."""
    conn = db.connect()
    cur = conn.cursor()
    sql = ("INSERT INTO favorites (product_id, user_id, created_at) VALUES (%s, %s, %s) "
           + ("ON CONFLICT (product_id, user_id) DO NOTHING" if db.USE_PG else ""))
    if db.USE_PG:
        cur.execute(sql, (product_id, user_id, db._now_str()))
    else:
        cur.execute("INSERT OR IGNORE INTO favorites (product_id, user_id, created_at) "
                    "VALUES (?, ?, ?)", (product_id, user_id, db._now_str()))
    conn.commit()
    conn.close()


def remove_favorite(product_id, user_id):
    """Покупатель убрал товар из избранного."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM favorites WHERE product_id = %s AND user_id = %s"),
                (product_id, user_id))
    conn.commit()
    conn.close()


def favorites_for_user(user_id):
    """Список id товаров в избранном покупателя."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT product_id FROM favorites WHERE user_id = %s"), (user_id,))
    out = [int(r["product_id"]) for r in cur.fetchall()]
    conn.close()
    return out


def favorite_counts():
    """{товар: сколько раз в избранном} — сигнал спроса владельцу, как и у
    «Жду поступления» (stock_alert_counts)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT product_id, COUNT(*) AS n FROM favorites GROUP BY product_id")
    out = {int(r["product_id"]): int(r["n"]) for r in cur.fetchall()}
    conn.close()
    return out
