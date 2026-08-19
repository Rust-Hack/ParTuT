"""
db_promos.py — промокоды.

Пятый кусок, вынесенный из db.py.

Главное здесь — занять код одной транзакцией вместе с заказом (_reserve_promo).
Отдельным походом в базу между проверкой и списанием успевает пройти чужой
заказ, и один код применяется дважды. Поэтому функция принимает готовый курсор
чужой транзакции, а не открывает своё подключение.

Примитивы берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не копиями имён:
копия не заметила бы подмены в тестах — см. db_raffles.py.
"""

import db


def _promo_row(code):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM promos WHERE code = %s"), (code.strip().upper(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def check_promo(code, user_id, subtotal):
    """Можно ли применить код. Возвращает (скидка, ошибка).

    Считаем ЗДЕСЬ, а не на клиенте: скидка — это деньги, и присланную сумму
    принимать на веру нельзя."""
    # Код приходит из запроса: строкой он быть обязан, но прислать могут что
    # угодно, а промокод — это деньги, и падать здесь нельзя.
    code = (code if isinstance(code, str) else "").strip().upper()
    if not code:
        return 0.0, None
    p = _promo_row(code)
    if not p or not p["active"]:
        return 0.0, "promo_unknown"
    if p["uses_left"] is not None and p["uses_left"] <= 0:
        return 0.0, "promo_used_up"
    if subtotal < (p["min_total"] or 0):
        return 0.0, "promo_min"
    if p["once_per_user"]:
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(db._q("SELECT COUNT(*) AS c FROM orders WHERE user_id = %s AND promo_code = %s "
                       "AND status != 'canceled'"), (user_id, code))
        used = cur.fetchone()["c"]
        conn.close()
        if used:
            return 0.0, "promo_once"

    if p["kind"] == "fixed":
        discount = float(p["value"] or 0)
    else:
        discount = subtotal * float(p["value"] or 0) / 100.0
    # Скидка не может превышать стоимость товаров: иначе магазин доплачивает.
    return round(min(discount, subtotal), 2), None


def consume_promo(code):
    """Списывает одно использование. Без ограничения по числу — ничего не делает."""
    code = (code or "").strip().upper()
    if not code:
        return
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE promos SET uses_left = uses_left - 1 "
                   "WHERE code = %s AND uses_left IS NOT NULL AND uses_left > 0"), (code,))
    conn.commit()
    conn.close()


def list_promos():
    """Коды со статистикой: сколько раз применили и сколько это принесло.
    Ради этой таблицы промокоды и заводятся — она отвечает, сработал ли пост."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM promos ORDER BY id DESC")
    promos = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT promo_code AS code, COUNT(*) AS n,
                          COALESCE(SUM(total), 0) AS revenue,
                          COALESCE(SUM(promo_discount), 0) AS given
                   FROM orders WHERE promo_code IS NOT NULL AND status = 'issued'
                   GROUP BY promo_code""")
    stats = {r["code"]: dict(r) for r in cur.fetchall()}
    conn.close()
    for p in promos:
        st = stats.get(p["code"], {})
        p["orders"] = int(st.get("n", 0))
        p["revenue"] = round(float(st.get("revenue", 0) or 0), 2)
        p["given"] = round(float(st.get("given", 0) or 0), 2)
    return promos


def add_promo(code, kind, value, min_total=0, uses_left=None, once_per_user=True):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("""INSERT INTO promos (code, kind, value, min_total, uses_left, once_per_user, active, created_at)
                      VALUES (%s, %s, %s, %s, %s, %s, 1, %s)"""),
                (code.strip().upper(), kind, float(value or 0), float(min_total or 0),
                 uses_left, 1 if once_per_user else 0, db._now_str()))
    conn.commit()
    conn.close()


def set_promo_active(code, active):
    code = (code if isinstance(code, str) else "").strip().upper()
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE promos SET active = %s WHERE code = %s"),
                (1 if active else 0, code))
    conn.commit()
    conn.close()


def delete_promo(code):
    # Код может прийти чем угодно из запроса — промокоды правит человек руками.
    code = (code if isinstance(code, str) else "").strip().upper()
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM promos WHERE code = %s"), (code,))
    conn.commit()
    conn.close()


def _reserve_promo(cur, code, user_id):
    """Занять одно применение промокода. Вызывается ВНУТРИ транзакции заказа.

    Сначала берём блокировку на строку кода: на Postgres — SELECT ... FOR UPDATE,
    на SQLite её роль играет запись (она переводит транзакцию в режим writer, и
    вторая ждёт). Без блокировки два одновременных заказа оба видят «код ещё не
    использован» и оба его применяют.
    """
    code = (code if isinstance(code, str) else "").strip().upper()
    if not code:
        return
    if db.USE_PG:
        cur.execute("SELECT * FROM promos WHERE code = %s FOR UPDATE", (code,))
    else:
        cur.execute("UPDATE promos SET code = code WHERE code = ?", (code,))
        cur.execute("SELECT * FROM promos WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row or not row["active"]:
        raise db.PromoGone("promo_unknown")
    if row["once_per_user"]:
        cur.execute(db._q("SELECT COUNT(*) AS c FROM orders WHERE user_id = %s AND promo_code = %s "
                       "AND status != 'canceled'"), (user_id, code))
        if cur.fetchone()["c"]:
            raise db.PromoGone("promo_once")
    if row["uses_left"] is not None:
        cur.execute(db._q("UPDATE promos SET uses_left = uses_left - 1 "
                       "WHERE code = %s AND uses_left > 0"), (code,))
        if cur.rowcount < 1:
            raise db.PromoGone("promo_used_up")
