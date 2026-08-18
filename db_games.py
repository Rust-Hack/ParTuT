"""
db_games.py — колесо фортуны и слот «Облако Монет».

Второй кусок, вынесенный из db.py. Игры раздают монеты, то есть деньги
магазина, и потому живут отдельно: сюда смотрят, когда считают щедрость.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db.log_coins()),
а не копиями имён: копия не заметила бы подмены в тестах — см. db_raffles.py.
"""

import db


def _migrate_wheel_progress_to_money():
    """Разовый перенос прогресса колеса со штук на рубли.

    Прокрут раньше давали за 5 купленных штук, теперь — за потраченную сумму.
    Накопленное людьми терять нельзя, поэтому пересчитываем в той же доле:
    3 штуки из 5 — это 60% пути, значит 60 Br из 100. Отметку о переносе держим
    в настройках, иначе при каждом запуске прогресс умножался бы снова.
    """
    try:
        if db.get_setting("wheel_progress_in_money"):
            return
        factor = int(WHEEL_STEP_DEFAULT / WHEEL_ITEMS_STEP_OLD)   # 100 Br / 5 штук = 20
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(db._q("UPDATE users SET wheel_progress = COALESCE(wheel_progress, 0) * %s "
                       "WHERE COALESCE(wheel_progress, 0) > 0"), (factor,))
        moved = cur.rowcount
        conn.commit()
        conn.close()
        db.set_setting("wheel_progress_in_money", "1")
        if moved:
            print(f"Прогресс колеса переведён со штук на рубли: {moved} покупателей")
    except Exception as e:
        print(f"Не удалось перенести прогресс колеса: {e}")


# Прокрут даётся за ПОТРАЧЕННЫЕ рубли, а не за штуки. Раньше считались штуки, и
# раздача выходила тем щедрее, чем дешевле корзина: пять одноразок по 8 Br и пять
# подов по 30 Br приносили один и тот же средний приз — 8.6% от заказа против
# 2.3%. Магазин больше всего доплачивал тем, кто меньше всех тратит.
WHEEL_STEP_DEFAULT = 100      # Br на один прокрут


WHEEL_ITEMS_STEP_OLD = 5      # сколько штук требовалось раньше — нужно для переноса


def wheel_step():
    """Шаг колеса в Br. Владелец меняет его в настройках магазина."""
    try:
        v = float(db.get_setting("wheel_step", WHEEL_STEP_DEFAULT))
        return v if v > 0 else WHEEL_STEP_DEFAULT
    except (TypeError, ValueError):
        return WHEEL_STEP_DEFAULT


def get_wheel(user_id):
    row = db.get_user_row(user_id)
    return {
        "spins": (row["wheel_spins"] if row and row["wheel_spins"] else 0),
        "progress": (row["wheel_progress"] if row and row["wheel_progress"] else 0),
        "step": wheel_step(),
    }


def add_wheel_progress(user_id, amount):
    """Копит прогресс на сумму заказа; каждый полный шаг превращается в прокрут."""
    db.ensure_user(user_id)
    # Колонка целочисленная, поэтому копим целые рубли: копейки отбрасываются
    # (меньше рубля с заказа при шаге в сотню — доли процента, зато не нужно
    # менять тип колонки на живой базе).
    step = int(wheel_step())
    row = db.get_user_row(user_id)
    prog = (row["wheel_progress"] or 0) + int(amount or 0)
    spins = (row["wheel_spins"] or 0)
    while prog >= step:
        prog -= step
        spins += 1
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET wheel_progress = %s, wheel_spins = %s WHERE user_id = %s"),
                (prog, spins, user_id))
    conn.commit()
    conn.close()


def add_spins(user_id, n):
    """Меняет число прокрутов на n (может быть отрицательным), не ниже нуля."""
    db.ensure_user(user_id)
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE users SET wheel_spins = {db.GREATEST}(0, COALESCE(wheel_spins, 0) + %s) WHERE user_id = %s"),
                (int(n), user_id))
    conn.commit()
    conn.close()


def use_spin(user_id):
    """Списывает один прокрут. True — если был доступен."""
    db.ensure_user(user_id)
    row = db.get_user_row(user_id)
    if (row["wheel_spins"] or 0) <= 0:
        return False
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET wheel_spins = wheel_spins - 1 WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()
    return True


def do_wheel_spin(user_id, prize_coins):
    """Атомарно за ОДИН запрос: если есть прокрут — списать 1 и начислить приз.
    Возвращает (coins, spins) или None, если прокрутов нет."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("""UPDATE users SET wheel_spins = wheel_spins - 1, coins = COALESCE(coins,0) + %s
                       WHERE user_id = %s AND COALESCE(wheel_spins,0) > 0
                       RETURNING COALESCE(coins,0) AS coins, wheel_spins""", (prize_coins, user_id))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        if row:
            db.log_coins(user_id, prize_coins, "wheel")
        return (row["coins"], row["wheel_spins"]) if row else None
    # SQLite. Условие — В САМОМ UPDATE, как и в ветке Postgres: раньше здесь
    # сначала читали остаток прокрутов, потом писали, и пять одновременных
    # нажатий проходили втроём — счётчик уходил в минус, а монеты начислялись
    # за прокруты, которых не было. Монеты — это скидка, то есть деньги.
    cur.execute("UPDATE users SET wheel_spins = wheel_spins - 1, "
                "coins = COALESCE(coins,0) + ? "
                "WHERE user_id = ? AND COALESCE(wheel_spins,0) > 0", (prize_coins, user_id))
    if cur.rowcount < 1:
        conn.commit()
        conn.close()
        return None
    conn.commit()
    cur.execute("SELECT COALESCE(coins,0) AS c, COALESCE(wheel_spins,0) AS s FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    conn.close()
    db.log_coins(user_id, prize_coins, "wheel")
    return (r["c"], r["s"]) if r else None


def do_slot_spin(user_id, cost, prize_coins):
    """Атомарно за ОДИН запрос: если хватает монет — списать cost и начислить приз.
    Возвращает новый баланс или None, если монет мало."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("""UPDATE users SET coins = COALESCE(coins,0) - %s + %s
                       WHERE user_id = %s AND COALESCE(coins,0) >= %s
                       RETURNING COALESCE(coins,0) AS coins""", (cost, prize_coins, user_id, cost))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        if row:
            db.log_coins(user_id, -cost, "slot")
            db.log_coins(user_id, prize_coins, "slot")
        return row["coins"] if row else None
    # Та же история, что и у колеса: проверка баланса живёт внутри UPDATE,
    # иначе одновременные ставки списываются с устаревшего остатка.
    cur.execute("UPDATE users SET coins = COALESCE(coins,0) - ? + ? "
                "WHERE user_id = ? AND COALESCE(coins,0) >= ?",
                (cost, prize_coins, user_id, cost))
    if cur.rowcount < 1:
        conn.commit()
        conn.close()
        return None
    conn.commit()
    cur.execute("SELECT COALESCE(coins,0) AS c FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    conn.close()
    db.log_coins(user_id, -cost, "slot")
    db.log_coins(user_id, prize_coins, "slot")
    return r["c"] if r else None


def get_game_stats():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT key, n FROM game_stats")
    rows = cur.fetchall()
    conn.close()
    return {r["key"]: r["n"] for r in rows}
