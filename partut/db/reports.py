"""
partut/db/reports.py — цифры магазина: выручка, прибыль, монеты, что с чем берут.

Десятый кусок, вынесенный из ядра базы. Здесь не хранится ничего — только
считается по уже записанному. Отдельный файл потому, что читают его иначе, чем
остальную базу: сюда смотрят, решая, чем торговать дальше, и ошибка тут стоит
не сломанной кнопки, а неверного решения.

Ровно здесь всплыла одинарная точность: SUM(real) в Postgres возвращает тоже
real, и выручка копилась в четырёх байтах — см. перенос 0005 в ядре.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime
import json

from partut import db
from partut import money


def inc_stat(key, delta=1):
    """Увеличивает счётчик игры (прокруты/ставки/выплаты)."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("""INSERT INTO game_stats (key, n) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET n = game_stats.n + EXCLUDED.n""", (key, int(delta)))
    else:
        cur.execute("INSERT INTO game_stats (key, n) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET n = n + ?",
                    (key, int(delta), int(delta)))
    conn.commit()
    conn.close()


def reset_statistics(orders=True, games=True):
    """Сброс тестовой статистики: удаляет заказы и/или обнуляет игровые счётчики.
    Возвращает {orders: сколько_удалено}."""
    conn = db.connect()
    cur = conn.cursor()
    n_orders = 0
    if orders:
        cur.execute("SELECT COUNT(*) AS c FROM orders")
        n_orders = cur.fetchone()["c"]
        cur.execute("DELETE FROM orders")
    if games:
        cur.execute("DELETE FROM game_stats")
    conn.commit()
    conn.close()
    return {"orders": n_orders}


def get_business_stats(days=None):
    """Сводная бизнес-аналитика за период (days=None → всё время). Считается в SQL.
    Возвращает выручку, заказы, средний чек, воронку статусов, по городам, по дням,
    топ товаров, метрики пользователей и монеты в обороте."""
    now = db.shop_now()
    cutoff = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00") if days else None
    conn = db.connect()
    cur = conn.cursor()

    # Выручка/заказы (выданные) за период
    if cutoff:
        cur.execute(db._q("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND created_at >= %s"), (cutoff,))
    else:
        cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued'")
    row = cur.fetchone()
    issued_count = row["c"]
    revenue = float(row["s"] or 0)
    avg_check = revenue / issued_count if issued_count else 0

    # В работе (текущий пайплайн — не зависит от периода)
    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status IN ('paid','confirmed')")
    row = cur.fetchone()
    inwork_count = row["c"]
    inwork_total = float(row["s"] or 0)

    # Воронка статусов за период
    if cutoff:
        cur.execute(db._q("SELECT status AS st, COUNT(*) AS c FROM orders WHERE created_at >= %s GROUP BY status"), (cutoff,))
    else:
        cur.execute("SELECT status AS st, COUNT(*) AS c FROM orders GROUP BY status")
    by_status = {r["st"]: r["c"] for r in cur.fetchall()}

    # Выручка по точкам (выданные, период)
    if cutoff:
        cur.execute(db._q("SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND created_at >= %s GROUP BY city ORDER BY s DESC"), (cutoff,))
    else:
        cur.execute("SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' GROUP BY city ORDER BY s DESC")
    revenue_by_city = [{"city": r["ct"], "total": round(float(r["s"] or 0), 2)} for r in cur.fetchall()]

    # По дням (для графика): последние N дней, пробелы = 0
    n_days = days if days else 30
    start = now - datetime.timedelta(days=n_days - 1)
    start_str = start.strftime("%Y-%m-%d 00:00")
    cur.execute(db._q("SELECT substr(created_at,1,10) AS d, COUNT(*) AS c, COALESCE(SUM(total),0) AS s "
                   "FROM orders WHERE status='issued' AND created_at >= %s GROUP BY substr(created_at,1,10)"), (start_str,))
    day_map = {r["d"]: (r["c"], float(r["s"] or 0)) for r in cur.fetchall()}
    daily = []
    for i in range(n_days):
        d = (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        c, s = day_map.get(d, (0, 0.0))
        daily.append({"date": d, "orders": c, "revenue": round(s, 2)})

    # Топ товаров (парсим JSON только выданных за период)
    if cutoff:
        cur.execute(db._q("SELECT items, coins_used, promo_discount FROM orders "
                       "WHERE status='issued' AND created_at >= %s"), (cutoff,))
    else:
        cur.execute("SELECT items, coins_used, promo_discount FROM orders WHERE status='issued'")
    qty_by_name, rev_by_name, profit_by_name = {}, {}, {}
    profit = 0.0            # прибыль ТОЛЬКО по позициям с известной закупочной ценой
    revenue_known = 0.0     # выручка этих же позиций — чтобы посчитать наценку
    revenue_unknown = 0.0   # выручка там, где закупочная цена не заполнена
    for r in cur.fetchall():
        try:
            # Как скидка ложится на позиции — правило одно на всех, оно в
            # partut/money.py. Выгрузка в файл считает тем же кодом, иначе
            # файл и экран однажды разойдутся в числах.
            for стр in money.разложить_заказ(json.loads(r["items"]), r["coins_used"],
                                             r["promo_discount"], db.COIN_VALUE):
                nm = стр["name"]
                qty_by_name[nm] = qty_by_name.get(nm, 0) + стр["qty"]
                rev_by_name[nm] = rev_by_name.get(nm, 0) + стр["revenue"]
                if стр["profit"] is None:
                    revenue_unknown += стр["revenue"]
                else:
                    profit += стр["profit"]
                    revenue_known += стр["revenue"]
                    profit_by_name[nm] = profit_by_name.get(nm, 0) + стр["profit"]
        except (TypeError, ValueError):
            pass
    top = [{"name": n, "qty": q, "revenue": round(rev_by_name.get(n, 0), 2),
            "profit": (round(profit_by_name[n], 2) if n in profit_by_name else None)}
           for n, q in sorted(qty_by_name.items(), key=lambda x: -x[1])[:8]]
    margin = (profit / revenue_known * 100) if revenue_known else 0

    # Пользователи
    cur.execute("SELECT COUNT(*) AS c FROM users")
    users_total = cur.fetchone()["c"]
    if cutoff:
        cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s"), (cutoff,))
        new_users = cur.fetchone()["c"]
        cur.execute(db._q("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued' AND created_at >= %s"), (cutoff,))
        buyers_period = cur.fetchone()["c"]
    else:
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at IS NOT NULL")
        new_users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued'")
        buyers_period = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM (SELECT user_id FROM orders WHERE status='issued' GROUP BY user_id HAVING COUNT(*) >= 2) t")
    repeat_buyers = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued'")
    total_buyers = cur.fetchone()["c"]

    # Монеты в обороте
    cur.execute("SELECT COALESCE(SUM(coins),0) AS s FROM users")
    coins_circulation = int(cur.fetchone()["s"] or 0)

    # Сравнение с ПРЕДЫДУЩИМ таким же окном (только для конкретного периода)
    prev = None
    if days:
        prev_start = (now - datetime.timedelta(days=2 * days - 1)).strftime("%Y-%m-%d 00:00")
        cur.execute(db._q("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders "
                       "WHERE status='issued' AND created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        r = cur.fetchone()
        p_cnt = r["c"]
        p_rev = float(r["s"] or 0)
        cur.execute(db._q("SELECT COUNT(DISTINCT user_id) AS c FROM orders "
                       "WHERE status='issued' AND created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        p_buyers = cur.fetchone()["c"]
        cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        p_new = cur.fetchone()["c"]
        prev = {"revenue": round(p_rev, 2), "orders": p_cnt,
                "avg_check": round(p_rev / p_cnt, 2) if p_cnt else 0,
                "buyers": p_buyers, "new_users": p_new}

    # Какие именно товары остались без закупочной цены. Предупреждение «выручка
    # на N Br в прибыль не попала» говорит размер беды, но не говорит, где она:
    # владелец видел цифру и не знал, что открыть. Теперь знает.
    cur.execute("""SELECT name, city FROM products
                    WHERE (cost IS NULL OR cost <= 0) AND (hidden IS NULL OR hidden = 0)
                    ORDER BY name""")
    без_закупки = [{"name": r["name"], "city": r["city"]} for r in cur.fetchall()]

    conn.close()
    return {
        "period_days": days, "prev": prev,
        "no_cost": без_закупки[:30], "no_cost_total": len(без_закупки),
        "revenue": round(revenue, 2), "orders": issued_count, "avg_check": round(avg_check, 2),
        "profit": round(profit, 2), "margin": round(margin, 1),
        "revenue_unknown_cost": round(revenue_unknown, 2),   # выручка без закупочной цены
        "inwork_total": round(inwork_total, 2), "inwork_count": inwork_count,
        "by_status": by_status, "revenue_by_city": revenue_by_city, "daily": daily, "top": top,
        "users_total": users_total, "new_users": new_users, "buyers_period": buyers_period,
        "repeat_buyers": repeat_buyers, "total_buyers": total_buyers,
        "coins_circulation": coins_circulation,
    }


def coin_flow(days=None):
    """Сколько монет роздано и списано за период, с разбивкой по причинам.

    Считается по летописи, а не по балансам: розданное и уже потраченное на
    балансах не видно вовсе, и раздача выглядела бы меньше, чем есть.
    """
    conn = db.connect()
    cur = conn.cursor()
    where, params = "", ()
    if days:
        cutoff = (db.shop_now() - datetime.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M")
        where, params = "WHERE created_at >= %s", (cutoff,)
    cur.execute(db._q(f"SELECT reason AS r, "
                   f"COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS plus, "
                   f"COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END), 0) AS minus "
                   f"FROM coin_log {where} GROUP BY reason"), params)
    rows = cur.fetchall()
    conn.close()
    granted = sum(int(r["plus"]) for r in rows)
    spent = sum(int(r["minus"]) for r in rows)
    by_reason = sorted(
        ({"reason": r["r"] or "other",
          "label": db.COIN_REASONS.get(r["r"] or "other", "Прочее"),
          "granted": int(r["plus"]), "spent": int(r["minus"])} for r in rows),
        key=lambda x: -(x["granted"] + x["spent"]))
    return {"granted": granted, "spent": spent, "by_reason": by_reason}


def also_bought(top=5, scan=500, min_count=2):
    """{товар: [товары, которые брали вместе с ним]} — по реальным выданным заказам.

    Считаем только пары, встретившиеся не меньше min_count раз: единственная
    совместная покупка — это совпадение, а не закономерность, и советовать по
    ней значит выдавать шум за рекомендацию.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT items FROM orders WHERE status = 'issued' ORDER BY id DESC LIMIT %s"), (scan,))
    pairs = {}
    for r in cur.fetchall():
        try:
            ids = {int(it.get("id", 0)) for it in json.loads(r["items"]) if it.get("id")}
        except (TypeError, ValueError):
            continue
        for a in ids:
            for b in ids:
                if a != b:
                    pairs.setdefault(a, {})
                    pairs[a][b] = pairs[a].get(b, 0) + 1
    conn.close()
    out = {}
    for a, others in pairs.items():
        best = [pid for pid, n in sorted(others.items(), key=lambda x: -x[1]) if n >= min_count][:top]
        if best:
            out[a] = best
    return out


def orders_for_export(days=None, city=None):
    """Сырые заказы за период — для выгрузки в файл. days=None → всё время.
    city сужает выгрузку до одной точки — продавцу нужны только свои заказы,
    а не весь магазин.

    Отдаём ВСЕ заказы, а не только выданные, и статус кладём столбцом. Выгрузка
    из одних выданных выглядела бы аккуратнее, но скрывала бы отказы — а это
    ровно то, ради чего в файл и лезут: почему заказ не дошёл до выдачи.
    Сумма по строкам «Выдан» при этом сходится с выручкой на экране.

    Сортировка по дате, а не по id: в файле человек читает историю, а не
    внутренние номера.
    """
    now = db.shop_now()
    cutoff = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00") if days else None
    conn = db.connect()
    cur = conn.cursor()
    поля = ("id, created_at, city, status, username, user_id, items, total, "
            "coins_used, promo_code, promo_discount, delivery_method, delivery_fee, payment_method")
    условия, параметры = [], []
    if cutoff:
        условия.append("created_at >= %s"); параметры.append(cutoff)
    if city:
        условия.append("city = %s"); параметры.append(city)
    where = f" WHERE {' AND '.join(условия)}" if условия else ""
    cur.execute(db._q(f"SELECT {поля} FROM orders{where} ORDER BY created_at, id"), tuple(параметры))
    return [dict(r) for r in cur.fetchall()]
