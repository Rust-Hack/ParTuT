"""
partut/db/stock.py — движение склада и подписки на поступление.

Шестой кусок, вынесенный из db.py.

Каждое списание записывается с закупочной ценой НА МОМЕНТ движения: цена
меняется, а во что обошёлся бой прошлого месяца — уже нет. Поэтому потери
считаются по сохранённой цене, а не по нынешней.

Примитивы берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не копиями имён:
копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime

from partut import db


# Причины движения. Приход прибавляет, остальное списывает.
STOCK_REASONS = {
    "in":      "Приход",
    "broken":  "Брак или бой",
    "expired": "Просрочка",
    "lost":    "Недостача",
    "gift":    "Подарок или образец",
    "fix":     "Пересчёт",
}


def move_stock(product_id, delta, reason, flavor=None, cost=0, note="", admin_id=None):
    """Меняет остаток и ЗАПИСЫВАЕТ движение. Возвращает новый остаток.

    Всё одной транзакцией: остаток и запись о нём не должны разъезжаться —
    иначе появится изменение, которого «никто не делал».
    """
    # При списании цену никто не вводит — берём закупочную товара на этот момент,
    # иначе потеря посчитается нулём и убыток окажется невидимым.
    if delta < 0 and not cost:
        p0 = db.get_product(product_id)
        cost = float(p0["cost"] or 0) if p0 else 0

    conn = db.connect()
    cur = conn.cursor()
    try:
        if flavor:
            cur.execute(db._q(f"UPDATE product_variants SET stock = {db.GREATEST}(0, stock + %s) "
                           "WHERE product_id = %s AND flavor = %s"), (delta, product_id, flavor))
        else:
            cur.execute(db._q(f"UPDATE products SET stock = {db.GREATEST}(0, stock + %s) WHERE id = %s"),
                        (delta, product_id))
        # Приход по новой цене обновляет закупочную: считать прибыль по старой
        # цене после подорожания — значит обманывать себя.
        if reason == "in" and cost and cost > 0:
            cur.execute(db._q("UPDATE products SET cost = %s WHERE id = %s"), (float(cost), product_id))
        cur.execute(db._q("""INSERT INTO stock_moves (product_id, flavor, delta, reason, cost, note, admin_id, created_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""),
                    (product_id, flavor or None, int(delta), reason, float(cost or 0),
                     (note or "").strip()[:120], admin_id, db._now_str()))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    if flavor:
        db.recalc_product_stock(product_id)
    p = db.get_product(product_id)
    return int(p["stock"]) if p else 0


def get_stock_moves(product_id=None, limit=100, city=None):
    """Движения склада. city ограничивает выборку одной точкой: без товара в
    запросе продавец иначе получал бы всю историю магазина — а по ней видно
    завоз и списания соседних точек."""
    conn = db.connect()
    cur = conn.cursor()
    if product_id:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE m.product_id = %s ORDER BY m.id DESC LIMIT %s"""), (product_id, limit))
    elif city:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE p.city = %s ORDER BY m.id DESC LIMIT %s"""), (city, limit))
    else:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          ORDER BY m.id DESC LIMIT %s"""), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def stock_losses(days=None):
    """Во сколько обошлись списания за период — по закупочной цене на момент
    движения. Это настоящие деньги, и владелец должен их видеть."""
    conn = db.connect()
    cur = conn.cursor()
    cutoff = ((db.shop_now() - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00")
              if days else None)
    sql = """SELECT reason, SUM(-delta) AS qty,
                    SUM(-delta * COALESCE(NULLIF(cost, 0), 0)) AS money
             FROM stock_moves WHERE delta < 0"""
    if cutoff:
        cur.execute(db._q(sql + " AND created_at >= %s GROUP BY reason"), (cutoff,))
    else:
        cur.execute(sql + " GROUP BY reason")
    rows = [{"reason": r["reason"], "qty": int(r["qty"] or 0), "money": round(float(r["money"] or 0), 2)}
            for r in cur.fetchall()]
    conn.close()
    return sorted(rows, key=lambda r: -r["money"])


def add_stock_alert(product_id, user_id):
    """Покупатель ждёт этот товар. Повторное нажатие не создаёт дубль."""
    conn = db.connect()
    cur = conn.cursor()
    sql = ("INSERT INTO stock_alerts (product_id, user_id, created_at) VALUES (%s, %s, %s) "
           + ("ON CONFLICT (product_id, user_id) DO NOTHING" if db.USE_PG else ""))
    if db.USE_PG:
        cur.execute(sql, (product_id, user_id, db._now_str()))
    else:
        cur.execute("INSERT OR IGNORE INTO stock_alerts (product_id, user_id, created_at) "
                    "VALUES (?, ?, ?)", (product_id, user_id, db._now_str()))
    conn.commit()
    conn.close()


def remove_stock_alert(product_id, user_id):
    """Покупатель передумал ждать. Подписка ставилась одним нажатием, а снять её
    было нельзя вовсе — оставалось терпеть сообщение о товаре, который уже не
    нужен, или блокировать бота."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM stock_alerts WHERE product_id = %s AND user_id = %s"),
                (product_id, user_id))
    conn.commit()
    conn.close()


def stock_alerts_ready():
    """Кого пора обрадовать: подписки на товары, которые СНОВА в наличии.
    Возвращает [(user_id, product_id, название)]."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("""SELECT a.user_id, a.product_id, p.name
                   FROM stock_alerts a JOIN products p ON p.id = a.product_id
                   WHERE p.stock > 0""")
    rows = [(int(r["user_id"]), int(r["product_id"]), r["name"]) for r in cur.fetchall()]
    conn.close()
    return rows


def clear_stock_alerts(product_id):
    """Сообщили — подписки на этот товар больше не нужны."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM stock_alerts WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def stock_alert_counts():
    """{товар: сколько ждут} — админу видно, что именно стоит завезти."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT product_id, COUNT(*) AS n FROM stock_alerts GROUP BY product_id")
    out = {int(r["product_id"]): int(r["n"]) for r in cur.fetchall()}
    conn.close()
    return out
