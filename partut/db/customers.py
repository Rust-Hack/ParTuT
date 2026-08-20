"""
partut/db/customers.py — покупатель в базе: монеты, рефералы, карточка.

Девятый кусок, вынесенный из ядра базы. Здесь всё, что магазин помнит о
человеке: подтверждённое совершеннолетие, баланс монет и летопись начислений,
кто кого привёл и сколько на этом заработал, карточка покупателя для продавца.

Монеты — это деньги магазина, поэтому каждое движение пишется в coin_log:
балансы показывают остаток, а розданное и уже потраченное видно только по
летописи.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime
import json

from partut import db


def is_age_ok(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT age_ok FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["age_ok"] == 1)


def set_age_ok(user_id):
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute(
            """INSERT INTO users (user_id, age_ok) VALUES (%s, 1)
               ON CONFLICT (user_id) DO UPDATE SET age_ok = 1""",
            (user_id,),
        )
    else:
        # ON CONFLICT (а не REPLACE) — чтобы не затирать coins/referred_by
        cur.execute(
            "INSERT INTO users (user_id, age_ok) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET age_ok = 1", (user_id,))
    conn.commit()
    conn.close()


def ensure_user(user_id):
    """Создаёт строку пользователя, если её ещё нет (с датой первого захода)."""
    now = db.shop_now().strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    conn.commit()
    conn.close()


def get_user_row(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def log_coins(user_id, delta, reason="other"):
    """Запись в летопись монет. Без неё «роздано за месяц» пришлось бы угадывать
    по остаткам на балансах, а это неверно: часть монет уже потрачена."""
    if not delta:
        return
    try:
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(db._q("INSERT INTO coin_log (user_id, delta, reason, created_at) "
                       "VALUES (%s, %s, %s, %s)"),
                    (user_id, int(delta), reason if reason in db.COIN_REASONS else "other",
                     db._now_str()))
        conn.commit()
        conn.close()
    except Exception as e:
        # Летопись — это отчётность, а не работа магазина: если запись не удалась,
        # монеты всё равно должны начислиться.
        print(f"Не удалось записать движение монет ({user_id}, {delta}, {reason}): {e}")


def add_coins(user_id, n, reason="other"):
    """Меняет баланс на n (может быть отрицательным), не опускаясь ниже нуля."""
    ensure_user(user_id)
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE users SET coins = {db.GREATEST}(0, COALESCE(coins, 0) + %s) WHERE user_id = %s"),
                (int(n), user_id))
    conn.commit()
    conn.close()
    log_coins(user_id, n, reason)


def get_coins(user_id):
    row = get_user_row(user_id)
    return row["coins"] if row and row["coins"] is not None else 0


def spend_coins(user_id, amount):
    """Атомарно списывает amount монет — только если хватает баланса.
    Возвращает True при успехе, False если монет мало (защита от гонки/двойного списания)."""
    amount = int(amount)
    if amount <= 0:
        return True
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("""UPDATE users SET coins = COALESCE(coins,0) - %s
                       WHERE user_id = %s AND COALESCE(coins,0) >= %s
                       RETURNING 1""", (amount, user_id, amount))
        ok = cur.fetchone() is not None
        conn.commit()
        conn.close()
        return ok
    cur.execute("SELECT COALESCE(coins,0) AS c FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    if not r or r["c"] < amount:
        conn.close()
        return False
    cur.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    return True


def count_referrals(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s"), (user_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


def referral_bonus():
    """Бонус за первый заказ друга. Владелец меняет его в настройках магазина."""
    try:
        v = int(float(db.get_setting("referral_bonus", db.REFERRAL_BONUS)))
        return max(0, v)
    except (TypeError, ValueError):
        return db.REFERRAL_BONUS


def coins_per_byn():
    """Сколько монет начисляем за каждый Br выданного заказа (кэшбэк)."""
    try:
        v = float(db.get_setting("coins_per_byn", 1))
        return max(0.0, v)
    except (TypeError, ValueError):
        return 1.0


def ref_percent(active):
    for min_active, pct in db.REFERRAL_TIERS:
        if active >= min_active:
            return pct
    return 2


def get_bonus_stats(user_id):
    """Всё для вкладки Бонусы за ОДНО подключение: баланс, рефералы, заработок, список."""
    conn = db.connect()
    cur = conn.cursor()
    # создать пользователя при первом заходе
    now = db.shop_now().strftime("%Y-%m-%d %H:%M")
    if db.USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    cur.execute(db._q("SELECT coins, ref_earned FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    coins = (row["coins"] if row and row["coins"] else 0)
    ref_earned = (row["ref_earned"] if row and row["ref_earned"] else 0)
    cur.execute(db._q("SELECT ref_activated FROM users WHERE referred_by = %s ORDER BY ref_activated DESC"), (user_id,))
    refs = cur.fetchall()
    conn.commit()
    conn.close()
    total = len(refs)
    active = sum(1 for r in refs if r["ref_activated"])
    return {"coins": coins, "ref_earned": ref_earned, "referrals": total, "active": active,
            "referrals_list": [{"active": bool(r["ref_activated"])} for r in refs]}


def count_active_referrals(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s AND ref_activated = 1"), (user_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


def list_referrals(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT user_id, ref_activated FROM users WHERE referred_by = %s ORDER BY ref_activated DESC, user_id"), (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_ref_earned(user_id):
    row = get_user_row(user_id)
    return row["ref_earned"] if row and row["ref_earned"] else 0


def add_ref_earned(user_id, n):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET ref_earned = COALESCE(ref_earned, 0) + %s WHERE user_id = %s"), (int(n), user_id))
    conn.commit()
    conn.close()


def reward_referrer_for_order(buyer_id, order_total):
    """Начисляет пригласившему % от заказа + фикс за первый заказ друга.
    Возвращает dict {referrer, percent, pct_coins, first, bonus, earned} или None."""
    row = get_user_row(buyer_id)
    if not row or not row["referred_by"]:
        return None
    ref = row["referred_by"]
    percent = ref_percent(count_active_referrals(ref))
    pct_coins = round((order_total or 0) * percent)   # X Br * p% = X*p монет (1 Br = 100 монет)
    first = not row["ref_activated"]
    earned = 0
    if pct_coins > 0:
        add_coins(ref, pct_coins, "referral")
        earned += pct_coins
    if first:
        set_ref_activated(buyer_id)
        bonus = referral_bonus()
        add_coins(ref, bonus, "referral")
        earned += bonus
    if earned > 0:
        add_ref_earned(ref, earned)
    return {"referrer": ref, "percent": percent, "pct_coins": pct_coins,
            "first": first, "bonus": (referral_bonus() if first else 0), "earned": earned}


def set_ref_activated(user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET ref_activated = 1 WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()


def unlink_referral(user_id):
    """Отвязывает реферала: referred_by → NULL, ref_activated → 0.
    Возвращает True, если связь была и снялась (можно снова привязать)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET referred_by = NULL, ref_activated = 0 "
                   "WHERE user_id = %s AND referred_by IS NOT NULL"), (user_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def clear_referrals_of(ref_id):
    """Отвязывает ВСЕХ рефералов пригласившего ref_id. Возвращает число отвязанных."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET referred_by = NULL, ref_activated = 0 WHERE referred_by = %s"), (ref_id,))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def list_users(search="", limit=300):
    """Список пользователей для админа. search — подстрока id ИЛИ @username (из заказов).
    Возвращает (список, всего_в_базе)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users")
    total = cur.fetchone()["c"]
    base = ("SELECT user_id, COALESCE(age_ok,0) AS age_ok, COALESCE(coins,0) AS coins, "
            "referred_by, COALESCE(wheel_spins,0) AS wheel_spins, COALESCE(ref_earned,0) AS ref_earned, "
            "COALESCE(username,'') AS username, COALESCE(first_name,'') AS first_name FROM users ")
    search = (search or "").strip()
    if search:
        # ищем и по id, и по имени (через заказы) — сначала находим id, потом полные данные
        # Сравниваем и приведённое к нижнему регистру, и как набрали: LOWER()
        # в SQLite умеет только латиницу, и поиск по русскому имени молча не
        # находил бы ничего на одной базе и находил на другой.
        like = f"%{search.lower()}%"
        raw = f"%{search}%"
        cur.execute(db._q(
            "SELECT DISTINCT u.user_id AS user_id FROM users u "
            "LEFT JOIN orders o ON o.user_id = u.user_id "
            "WHERE CAST(u.user_id AS TEXT) LIKE %s "
            "   OR LOWER(COALESCE(o.username,'')) LIKE %s OR COALESCE(o.username,'') LIKE %s "
            "   OR LOWER(COALESCE(u.username,'')) LIKE %s OR COALESCE(u.username,'') LIKE %s "
            "   OR LOWER(COALESCE(u.first_name,'')) LIKE %s OR COALESCE(u.first_name,'') LIKE %s "
            "ORDER BY u.user_id DESC LIMIT %s"),
            (raw, like, raw, like, raw, like, raw, limit))
        match_ids = [r["user_id"] for r in cur.fetchall()]
        if not match_ids:
            conn.close()
            return [], total
        marks0 = ",".join(["%s"] * len(match_ids))
        cur.execute(db._q(base + f"WHERE user_id IN ({marks0}) ORDER BY user_id DESC"), tuple(match_ids))
    else:
        cur.execute(db._q(base + "ORDER BY user_id DESC LIMIT %s"), (limit,))
    users = cur.fetchall()
    ids = [u["user_id"] for u in users]
    orders_by, names, refcount, last_order = {}, {}, {}, {}
    if ids:
        marks = ",".join(["%s"] * len(ids))
        cur.execute(db._q(f"SELECT user_id, COUNT(*) AS cnt, COALESCE(SUM(total),0) AS spent "
                       f"FROM orders WHERE user_id IN ({marks}) AND status = 'issued' GROUP BY user_id"), tuple(ids))
        for r in cur.fetchall():
            orders_by[r["user_id"]] = (r["cnt"], r["spent"])
        cur.execute(db._q(f"SELECT user_id, username, created_at FROM orders WHERE user_id IN ({marks}) ORDER BY id DESC"), tuple(ids))
        for r in cur.fetchall():
            if r["user_id"] not in names and r["username"]:
                names[r["user_id"]] = r["username"]        # самый свежий username
            if r["user_id"] not in last_order and r["created_at"]:
                last_order[r["user_id"]] = r["created_at"]  # дата последнего заказа
        cur.execute(db._q(f"SELECT referred_by AS ref, COUNT(*) AS c FROM users WHERE referred_by IN ({marks}) GROUP BY referred_by"), tuple(ids))
        for r in cur.fetchall():
            refcount[r["ref"]] = r["c"]
    conn.close()
    out = []
    for u in users:
        cnt, spent = orders_by.get(u["user_id"], (0, 0))
        out.append({
            # Имя из профиля, а если его нет — из последнего заказа.
            "id": u["user_id"], "username": u["username"] or names.get(u["user_id"], ""),
            "first_name": u["first_name"],
            "coins": u["coins"], "age_ok": bool(u["age_ok"]),
            "wheel_spins": u["wheel_spins"], "ref_earned": u["ref_earned"],
            "referred_by": u["referred_by"], "referrals": refcount.get(u["user_id"], 0),
            "orders": cnt, "spent": round(spent or 0, 2),
            "last_order": last_order.get(u["user_id"], ""),
        })
    return out, total


def customer_card(user_id, limit=30):
    """Всё об одном покупателе на одном экране: кто он, что покупал, сколько принёс.

    Деньги считаем только по ВЫДАННЫМ заказам: «оформил и не забрал» — это не
    покупка, и складывать её в выручку значит завышать ценность клиента.
    Возвращает None, если про такого человека нечего показать.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM users WHERE user_id = %s"), (user_id,))
    u = cur.fetchone()
    # Берём все заказы, а не первые N: суммы и любимые товары должны считаться по
    # всей истории, даже если на экран попадёт только последняя страница.
    cur.execute(db._q("SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT 500"), (user_id,))
    rows = cur.fetchall()
    if not u and not rows:
        conn.close()
        return None
    cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s"), (user_id,))
    referrals = cur.fetchone()["c"]
    point = ""
    point_id = (u["pickup_point_id"] if u else None)
    if point_id:
        cur.execute(db._q("SELECT address FROM pickup_points WHERE id = %s"), (point_id,))
        p = cur.fetchone()
        point = p["address"] if p else ""
    conn.close()

    issued = [r for r in rows if r["status"] == "issued"]
    spent = sum(float(r["total"] or 0) for r in issued)
    qty_by_name, profit, revenue_known = {}, 0.0, 0.0
    for r in issued:
        try:
            items = json.loads(r["items"])
        except (TypeError, ValueError):
            items = []
        # Скидка монетами и промокодом — вычет из денег за этот заказ, а не
        # подарок «мимо кассы»: без неё карточка показывала бы, что покупатель
        # приносит больше, чем на самом деле.
        paid_for_goods = sum(float(i.get("price", 0) or 0) * int(i.get("qty", 0) or 0) for i in items)
        given = round(int(r["coins_used"] or 0) * db.COIN_VALUE + float(r["promo_discount"] or 0), 2) \
            if "coins_used" in r.keys() else 0.0
        given = min(given, paid_for_goods)
        for it in items:
            try:
                q = int(it.get("qty", 0))
            except (TypeError, ValueError):
                continue
            nm = it.get("name", "?")
            price = float(it.get("price", 0) or 0)
            cost = float(it.get("cost", 0) or 0)
            line = q * price
            off = (line / paid_for_goods * given) if paid_for_goods else 0.0
            qty_by_name[nm] = qty_by_name.get(nm, 0) + q
            # Нулевая закупочная — это «не заполнено», а не «досталось даром».
            if cost > 0:
                profit += line - off - q * cost
                revenue_known += line - off
    favorites = [{"name": n, "qty": q} for n, q in sorted(qty_by_name.items(), key=lambda x: -x[1])[:5]]

    dates = [r["created_at"] for r in issued if r["created_at"]]
    first_buy = min(dates) if dates else ""
    last_buy = max(dates) if dates else ""
    days_since = None
    if last_buy:
        try:
            days_since = (db.shop_now() - datetime.datetime.strptime(last_buy[:16], "%Y-%m-%d %H:%M")).days
        except ValueError:
            days_since = None

    # Имя из профиля, а если его нет — из последнего заказа: у покупателя без
    # заказов карточка иначе открывалась безымянной.
    username = (u["username"] or "") if (u and "username" in u.keys()) else ""
    first_name = (u["first_name"] or "") if (u and "first_name" in u.keys()) else ""
    if not username:
        for r in rows:                   # заказы идут новыми вверх — берём свежее имя
            if r["username"]:
                username = r["username"]
                break

    out_orders = []
    for r in rows[:limit]:
        try:
            items = [{"name": it.get("name", "?"), "flavor": it.get("flavor", ""),
                      "qty": int(it.get("qty", 0) or 0)} for it in json.loads(r["items"])]
        except (TypeError, ValueError):
            items = []
        out_orders.append({
            "id": r["id"], "created_at": r["created_at"], "status": r["status"],
            "total": round(float(r["total"] or 0), 2), "city": r["city"], "items": items,
            "delivery_method": r["delivery_method"] or "", "address": r["delivery_address"] or "",
            "promo_code": r["promo_code"] or "", "coins_used": int(r["coins_used"] or 0),
        })

    return {
        "id": user_id, "username": username, "first_name": first_name,
        "coins": int((u["coins"] if u else 0) or 0),
        "age_ok": bool(u["age_ok"]) if u else False,
        "phone": (u["phone"] if u else "") or "",
        "point": point,
        "referred_by": (u["referred_by"] if u else None),
        "referrals": referrals,
        "ref_earned": int((u["ref_earned"] if u else 0) or 0),
        "no_reminders": bool(u["no_reminders"]) if u else False,
        "joined": (u["created_at"] if u else "") or "",
        "orders_total": len(rows),
        "issued": len(issued),
        "canceled": sum(1 for r in rows if r["status"] == "canceled"),
        "open": sum(1 for r in rows if r["status"] in ("new", "paid", "confirmed")),
        "spent": round(spent, 2),
        "avg_check": round(spent / len(issued), 2) if issued else 0,
        "profit": round(profit, 2),
        "profit_known": revenue_known > 0,      # была ли хоть одна позиция с закупочной ценой
        "first_buy": first_buy, "last_buy": last_buy, "days_since": days_since,
        "favorites": favorites,
        "history": out_orders,
        "history_shown": len(out_orders),
    }


def delete_user(user_id):
    """Полностью удаляет запись пользователя (монеты, 18+, прокруты, реф-связь).
    Заказы остаются в истории. Возвращает True, если пользователь был удалён."""
    conn = db.connect()
    cur = conn.cursor()
    # Отвязать тех, кого он приглашал (чтобы не осталось «висячих» ссылок на удалённого).
    cur.execute(db._q("UPDATE users SET referred_by = NULL, ref_activated = 0 WHERE referred_by = %s"), (user_id,))
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (user_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
