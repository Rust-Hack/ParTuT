"""
partut/db/raffles.py — розыгрыши: сам розыгрыш, билеты участников, итоги.

Первый кусок, вынесенный из db.py (4787 строк). Шов выбран не по вкусу, а по
связям: розыгрышам нужны только примитивы работы с базой, а снаружи их зовёт
единственная функция — init_db, чтобы дописать колонки и ключи.

Примитивы берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не копиями имён.
Так сделано намеренно: тесты подменяют db.connect и задают db.SQLITE_FILE, и
копия имени такие подмены перестала бы замечать — стенд молча ушёл бы работать
с боевой базой вместо временной.
"""

import datetime
import json

from partut import db


def _ensure_raffle_columns():
    """finished_at — когда розыгрыш реально подвели.

    По ends_at этого не понять: владелец может завершить досрочно, и тогда срок
    остаётся в будущем. А знать нужно, чтобы неделю после итогов показывать
    участникам, кто выиграл: победителям бот пишет лично, остальные иначе не
    узнают о розыгрыше ничего."""
    conn = db.connect()
    cur = conn.cursor()
    cols = db._table_columns(cur, "raffles")
    if "finished_at" not in cols:
        cur.execute("ALTER TABLE raffles ADD COLUMN finished_at TEXT")
    if "photo" not in cols:
        # Фото разыгрываемого товара: «Одноразка» словами и она же на картинке —
        # разные по силе обещания.
        cur.execute("ALTER TABLE raffles ADD COLUMN photo TEXT")
    # Своё фото на каждое место: 1-2 место обычно вещь (одноразка, жидкость),
    # 3-е чаще монеты — но и оно бывает вещью, а общее фото на весь розыгрыш
    # молча подписывало любое место одной и той же картинкой.
    добавлен_photo1 = "photo1" not in cols
    if добавлен_photo1:
        cur.execute("ALTER TABLE raffles ADD COLUMN photo1 TEXT")
    if "photo2" not in cols:
        cur.execute("ALTER TABLE raffles ADD COLUMN photo2 TEXT")
    if "photo3" not in cols:
        cur.execute("ALTER TABLE raffles ADD COLUMN photo3 TEXT")
    if добавлен_photo1:
        # Старое общее фото всегда было фото ГЛАВНОГО приза — переносим в 1 место,
        # чтобы уже загруженная картинка не потерялась при обновлении.
        cur.execute("UPDATE raffles SET photo1 = photo WHERE photo IS NOT NULL AND photo1 IS NULL")
    conn.commit()
    conn.close()


def recent_finished_raffle():
    """Последний завершённый розыгрыш — его итоги висят до следующего.

    Убирать их по сроку нельзя: участник заходит в магазин не каждый день, а
    узнать, чем кончилось дело, должен. Пока новый розыгрыш не начат, вкладка
    показывает итоги прошлого — и это честнее пустой вкладки.
    """
    return get_last_finished_raffle()


def _ensure_raffle_uniques():
    """Честность розыгрыша — правилом базы, а не проверкой в коде.

    Было две дыры, и обе открывались одновременными нажатиями. «Участвую»
    сначала спрашивало «уже участвует?», потом вставляло билет — пять нажатий
    подряд давали одному человеку пять билетов и впятеро больше шансов. А когда
    у розыгрыша выходил срок, каждый, кто в этот момент открыл вкладку, запускал
    розыгрыш заново: победителю слали поздравление по разу от каждого, монеты за
    третье место начислялись столько же раз, и на месте одного розыгрыша
    оставалась пачка активных.

    Уникальный ключ закрывает и то, и другое: спорить с ним бесполезно.
    Дубли, которые успели накопиться, сначала разводим — иначе ключ не встанет.
    """
    conn = db.connect()
    cur = conn.cursor()
    try:
        # Лишние билеты одного человека: оставляем самый первый.
        cur.execute("""DELETE FROM raffle_entries WHERE id NOT IN
                       (SELECT MIN(id) FROM raffle_entries GROUP BY raffle_id, user_id)""")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS raffle_entries_uniq "
                    "ON raffle_entries (raffle_id, user_id)")
        # Лишние активные розыгрыши: настоящий — последний, остальные закрываем.
        cur.execute("""UPDATE raffles SET status = 'finished'
                        WHERE status = 'active' AND id <>
                              (SELECT MAX(id) FROM raffles WHERE status = 'active')""")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS raffles_one_active "
                    "ON raffles (status) WHERE status = 'active'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Не смог навести порядок в розыгрышах: {e}")
    conn.close()


def get_active_raffle():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM raffles WHERE status = 'active' ORDER BY id DESC LIMIT 1"))
    row = cur.fetchone()
    conn.close()
    return row


def get_last_finished_raffle():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM raffles WHERE status = 'finished' ORDER BY id DESC LIMIT 1"))
    row = cur.fetchone()
    conn.close()
    return row


def finished_raffles(limit=15):
    """Архив прошлых розыгрышей, новые сверху.

    Раньше был виден только САМЫЙ последний (get_last_finished_raffle,
    LIMIT 1) — как только стартовал следующий розыгрыш, итоги предыдущего
    пропадали безвозвратно, и для покупателя, и для владельца в приложении."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM raffles WHERE status = 'finished' ORDER BY id DESC LIMIT %s"), (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def create_raffle(title="Розыгрыш месяца", prize1="Одноразка", prize2="Жидкость",
                  prize3_coins=500, threshold=25, days=30):
    now = db.shop_now()
    ends = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    try:
        rid = db._insert_id(
            cur,
            """INSERT INTO raffles (title, prize1, prize2, prize3_coins, threshold, starts_at, ends_at, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)""",
            (title, prize1, prize2, int(prize3_coins), float(threshold),
             now.strftime("%Y-%m-%d %H:%M"), ends, now.strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
    except Exception:
        # Активный розыгрыш уже завёл кто-то другой — их не может быть двое.
        conn.rollback()
        conn.close()
        existing = get_active_raffle()
        if existing:
            return int(existing["id"])
        raise
    conn.close()
    return rid


_RAFFLE_EDITABLE = {"title", "prize1", "prize2", "prize3_coins", "threshold", "ends_at",
                    "photo", "photo1", "photo2", "photo3"}


def update_raffle_field(raffle_id, field, value):
    if field not in _RAFFLE_EDITABLE:
        return False
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE raffles SET {field} = %s WHERE id = %s"), (value, raffle_id))
    conn.commit()
    conn.close()
    return True


def claim_raffle_draw(raffle_id):
    """Забирает право разыграть этот розыгрыш. True достаётся ровно одному.

    Розыгрыш запускается лениво — тем, кто первым открыл вкладку после срока.
    В час пик таких «первых» несколько, и без этого условия каждый раздавал
    призы заново."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE raffles SET status = 'finished', finished_at = %s "
                   "WHERE id = %s AND status = 'active'"), (db._now_str(), raffle_id))
    won = cur.rowcount > 0
    conn.commit()
    conn.close()
    return won


def cancel_raffle(raffle_id):
    """Отменяет розыгрыш без итогов — для тестового или заведённого по ошибке.

    В отличие от «подвести итоги»: победителей не выбираем, монет не
    начисляем, участникам и владельцу ничего не пишем. Строку удаляем
    совсем (а не помечаем статусом) — она не должна всплыть ни в архиве
    (finished_raffles), ни тизером «прошлый победитель» у следующего
    розыгрыша: отменённого розыгрыша для покупателя как будто не было.
    Фото приза, если успели загрузить, подберёт ночная уборка сирот —
    как только строка исчезает, ссылок на файл не остаётся.

    Возвращает True, если розыгрыш был активен и правда отменён."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM raffle_entries WHERE raffle_id = %s"), (raffle_id,))
    cur.execute(db._q("DELETE FROM raffles WHERE id = %s AND status = 'active'"), (raffle_id,))
    cancelled = cur.rowcount > 0
    conn.commit()
    conn.close()
    return cancelled


def set_raffle_winners(raffle_id, winners):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE raffles SET winners = %s WHERE id = %s"),
                (json.dumps(winners, ensure_ascii=False), raffle_id))
    conn.commit()
    conn.close()


def finish_raffle(raffle_id, winners):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE raffles SET status = 'finished', winners = %s WHERE id = %s"),
                (json.dumps(winners, ensure_ascii=False), raffle_id))
    conn.commit()
    conn.close()


def add_raffle_entry(raffle_id, user_id):
    """Билет участника. Повторное нажатие второго билета не даёт — за этим следит
    уникальный ключ, а не проверка перед вставкой: между проверкой и вставкой
    проходит второе нажатие."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("INSERT INTO raffle_entries (raffle_id, user_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (raffle_id, user_id))
    else:
        cur.execute("INSERT OR IGNORE INTO raffle_entries (raffle_id, user_id) VALUES (?, ?)",
                    (raffle_id, user_id))
    conn.commit()
    conn.close()


def is_entered(raffle_id, user_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT 1 FROM raffle_entries WHERE raffle_id = %s AND user_id = %s LIMIT 1"), (raffle_id, user_id))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def count_entries(raffle_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM raffle_entries WHERE raffle_id = %s"), (raffle_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


def get_raffle_user_ids(raffle_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT user_id FROM raffle_entries WHERE raffle_id = %s"), (raffle_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def spent_since(user_id, since):
    """Сумма ВЫДАННЫХ заказов клиента с момента since (для порога участия)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE user_id = %s AND status = 'issued' AND created_at >= %s"),
                (user_id, since))
    s = cur.fetchone()["s"]
    conn.close()
    return float(s or 0)


def get_raffle_state(user_id):
    """Активный розыгрыш + участники/участвую/потрачено/прошлые победители — за ОДНО подключение."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM raffles WHERE status = 'active' ORDER BY id DESC LIMIT 1"))
    r = cur.fetchone()
    if not r:
        conn.close()
        return None
    cur.execute(db._q("SELECT COUNT(*) AS c, COALESCE(MAX(CASE WHEN user_id = %s THEN 1 ELSE 0 END), 0) AS mine "
                   "FROM raffle_entries WHERE raffle_id = %s"), (user_id, r["id"]))
    e = cur.fetchone()
    cur.execute(db._q("SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE user_id = %s AND status = 'issued' AND created_at >= %s"),
                (user_id, r["starts_at"]))
    spent = cur.fetchone()["s"]
    cur.execute(db._q("SELECT winners FROM raffles WHERE status = 'finished' ORDER BY id DESC LIMIT 1"))
    lw = cur.fetchone()
    conn.close()
    return {"raffle": r, "participants": e["c"], "entered": bool(e["mine"]),
            "spent": float(spent or 0), "last_winners_raw": (lw["winners"] if lw else None)}
