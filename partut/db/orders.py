"""
partut/db/orders.py — заказ в базе: оформление, состав, статусы, напоминания.

Седьмой кусок, вынесенный из ядра базы. Заказ — самое ответственное, что
магазин пишет: за одну транзакцию списывается склад, тратятся монеты, гасится
промокод. Половина этого файла — не запросы, а защита от того, чтобы списать
дважды (двойной клик, повторная отправка, две вкладки).

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db.add_coins()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime
import json

from partut import db


def create_order(user_id, username, city, items, total, pickup_time):
    """Создаёт заказ и возвращает его id. items -> строка JSON."""
    created_at = db.shop_now().strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    order_id = db._insert_id(
        cur,
        """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, 'new', %s)""",
        (user_id, username, city,
         json.dumps(items, ensure_ascii=False), total, pickup_time, created_at),
    )
    conn.commit()
    conn.close()
    return order_id


def get_checkout_data(user_id, product_ids, method_id):
    """Всё, что нужно для оформления заказа, — за ОДНО подключение и 4 запроса.

    Раньше сервер дёргал базу отдельно на каждый товар (get_product + get_variants),
    отдельно на 18+, монеты и способ доставки — на Neon это ~8 сетевых поездок,
    и кнопка «Оформить» заметно висла. Здесь всё берётся разом.

    Возвращает: {age_ok, coins, products: {id: row}, variants: {id: {flavor: stock}}, method}
    """
    ids = [int(i) for i in dict.fromkeys(product_ids)]     # уникальные, порядок сохраняем
    conn = db.connect()
    cur = conn.cursor()

    cur.execute(db._q("SELECT age_ok, COALESCE(coins, 0) AS coins FROM users WHERE user_id = %s"),
                (user_id,))
    u = cur.fetchone()
    age_ok = bool(u and u["age_ok"] == 1)
    coins = int(u["coins"]) if u else 0

    products, variants = {}, {}
    if ids:
        marks = ",".join(["%s"] * len(ids))
        cur.execute(db._q(f"SELECT * FROM products WHERE id IN ({marks})"), tuple(ids))
        products = {int(r["id"]): dict(r) for r in cur.fetchall()}
        cur.execute(db._q(f"SELECT * FROM product_variants WHERE product_id IN ({marks})"), tuple(ids))
        for v in cur.fetchall():
            variants.setdefault(int(v["product_id"]), {})[v["flavor"]] = v["stock"]

    method = None
    points = []
    if method_id is not None:
        cur.execute(db._q("SELECT * FROM delivery_methods WHERE id = %s"), (method_id,))
        row = cur.fetchone()
        method = dict(row) if row else None
        # Точки самовывоза берём ТУТ ЖЕ: отдельный запрос на оформлении — это
        # ещё одно подключение к базе на самом горячем пути.
        if method and not method["needs_address"]:
            cur.execute(db._q("SELECT * FROM pickup_points WHERE city = %s ORDER BY sort, id"),
                        (method["city"],))
            points = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {"age_ok": age_ok, "coins": coins, "products": products,
            "variants": variants, "method": method, "points": points}


class PromoGone(Exception):
    """Промокод перестал действовать, пока покупатель оформлял заказ.

    Проверка кода и его списание были двумя отдельными походами в базу, между
    которыми успевал вклиниться другой заказ. Из-за этого код «один раз на
    покупателя» срабатывал по нескольку раз подряд, а код на два применения —
    сколько угодно. Теперь и проверка, и списание живут внутри транзакции
    заказа, а если код уже разобрали — заказ честно отклоняется.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class OutOfStock(Exception):
    """Товар разобрали, пока покупатель оформлял заказ. Несёт его название,
    чтобы человеку можно было сказать, что именно кончилось."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


# Сколько времени повтор оформления считается тем же самым заказом. Сутки — с
# запасом: человек мог потерять связь, уйти в метро и вернуться к приложению.
ORDER_TOKEN_HOURS = 24


def find_order_by_token(user_id, token, hours=ORDER_TOKEN_HOURS):
    """Заказ, оформленный этой же попыткой, или None.

    Ключ ищется вместе с user_id: он приходит от клиента, и чужой заказ по нему
    достаться не должен ни по ошибке, ни нарочно.

    hours=None — искать без ограничения по времени. Так спрашивают, когда ключ
    уже отверг вставку: уникальный ключ в базе вечен, а окно поиска — сутки, и
    без этого повтор годовой давности отвечал бы ошибкой вместо своего заказа.
    """
    if not token:
        return None
    conn = db.connect()
    cur = conn.cursor()
    if hours is None:
        cur.execute(db._q("SELECT * FROM orders WHERE user_id = %s AND client_token = %s "
                       "ORDER BY id DESC LIMIT 1"), (user_id, token))
    else:
        cutoff = (db.shop_now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        cur.execute(db._q("SELECT * FROM orders WHERE user_id = %s AND client_token = %s "
                       "AND created_at >= %s ORDER BY id DESC LIMIT 1"),
                    (user_id, token, cutoff))
    row = cur.fetchone()
    conn.close()
    return row


def place_order(user_id, username, city, items, subtotal, fee, coin_value, coins_to_spend,
                method_name, address, payment, comment, phone, status,
                promo_code="", promo_discount=0.0, client_token=""):
    """Создаёт заказ целиком за ОДНУ транзакцию: списывает монеты, вставляет заказ
    со всеми полями доставки, снимает остатки со склада (и по вкусам).

    Раньше это были create_order + set_order_delivery + set_order_coins_used +
    change_stock на каждую позицию + set_order_status — каждая со своим commit'ом.
    Теперь один commit: меньше поездок к базе и заказ не может «застрять» наполовину.

    Возвращает (order_id, coins_used, total, повтор?). Последнее — правда, если
    заказ уже был создан этой же попыткой и мы просто отдаём его снова.
    """
    # Быстрый путь: человек нажал «Оформить», ответ не дошёл, он нажал снова.
    if client_token:
        prev = find_order_by_token(user_id, client_token)
        if prev:
            return int(prev["id"]), int(prev["coins_used"] or 0), float(prev["total"]), True

    created_at = db.shop_now().strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    try:
        # 1. Монеты — списываем условно (только если хватает баланса), это же и защита от гонки.
        coins_used = 0
        spend = int(coins_to_spend or 0)
        if spend > 0:
            cur.execute(db._q("""UPDATE users SET coins = COALESCE(coins, 0) - %s
                              WHERE user_id = %s AND COALESCE(coins, 0) >= %s"""),
                        (spend, user_id, spend))
            if cur.rowcount > 0:
                coins_used = spend

        discount = round(coins_used * coin_value, 2)
        promo_off = round(float(promo_discount or 0), 2)
        # Промокод занимаем здесь же, одной транзакцией с заказом: иначе между
        # проверкой и списанием успевает пройти чужой заказ.
        if promo_code and promo_off > 0:
            db._reserve_promo(cur, promo_code, user_id)
        # Итог не может уйти в минус: скидка монетами плюс промокод могут
        # перекрыть стоимость товаров, но доставку покупатель платит всё равно.
        total = round(max(0.0, subtotal - discount - promo_off) + fee, 2)

        # 2. Сам заказ — сразу со всеми полями (без последующих UPDATE).
        order_id = db._insert_id(
            cur,
            """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status,
                                   created_at, coins_used, delivery_method, delivery_address,
                                   delivery_fee, payment_method, comment, phone,
                                   promo_code, promo_discount, client_token, terms_version)
               VALUES (%s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, username, city, json.dumps(items, ensure_ascii=False), total, status,
             created_at, coins_used, method_name, address, float(fee or 0), payment,
             (comment or "").strip()[:500], (phone or "").strip()[:40],
             (promo_code or "").strip().upper() or None, promo_off,
             (client_token or "").strip() or None, db.documents_version()),
        )

        # 3. Склад: у товаров со вкусами списываем вариант, у обычных — сам товар.
        #
        # Списываем УСЛОВНО: «...WHERE stock >= сколько нужно». Если строк не
        # изменилось — товар разобрали, пока человек оформлял, и весь заказ
        # откатывается. Раньше остаток просто прижимался к нулю, и на последнюю
        # штуку могли одновременно оформиться двое: остаток 0, продано две,
        # а на полке одна. Кому-то из покупателей пришлось бы отказать.
        touched_variants = set()
        for it in items:
            if it.get("flavor"):
                cur.execute(db._q("UPDATE product_variants SET stock = stock - %s "
                               "WHERE product_id = %s AND flavor = %s AND stock >= %s"),
                            (it["qty"], it["id"], it["flavor"], it["qty"]))
                touched_variants.add(it["id"])
            else:
                cur.execute(db._q("UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s"),
                            (it["qty"], it["id"], it["qty"]))
            if cur.rowcount < 1:
                raise OutOfStock(it.get("name") or "товар")
        # общий остаток товара-модели = сумма остатков вкусов
        for pid in touched_variants:
            cur.execute(db._q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (pid, pid))

        conn.commit()
    except Exception:
        conn.rollback()      # ничего не применилось: ни монеты, ни склад, ни заказ
        conn.close()
        # Два одинаковых запроса ушли одновременно — так бывает при двойном
        # нажатии на плохой связи. Уникальный ключ пропустил ровно один; второму
        # отдаём тот же заказ, а не ошибку: человек оформлял один раз.
        if client_token:
            # Без ограничения по времени: раз ключ отверг вставку, заказ с этой
            # попыткой в базе есть — вопрос только в том, насколько он давний.
            prev = find_order_by_token(user_id, client_token, hours=None)
            if prev:
                return int(prev["id"]), int(prev["coins_used"] or 0), float(prev["total"]), True
        raise
    conn.close()
    # Списание монет за заказ — тоже движение, и в летописи ему место: иначе
    # «роздано» будет, а «на что потратили» — нет.
    if coins_used:
        db.log_coins(user_id, -coins_used, "order")
    return order_id, coins_used, total, False


# Статусы «заказ ещё живой»: до выдачи или отмены.
ОТКРЫТЫЕ = ("new", "paid", "confirmed")


def open_orders_with_product(pid):
    """Сколько незакрытых заказов содержат этот товар.

    Нужно перед удалением товара с точки. Заказ переживает удаление — состав
    хранится в самом заказе, — но продавец остаётся с обязательством выдать
    то, чего в магазине больше нет, и узнаёт об этом от покупателя.

    Считаем по составу заказа, а не по ссылке: связи «заказ — товар» в базе
    нет, состав лежит строкой JSON. Открытых заказов всегда немного, поэтому
    перебор здесь дешевле отдельной таблицы связей.
    """
    conn = db.connect()
    cur = conn.cursor()
    места = ", ".join(["%s"] * len(ОТКРЫТЫЕ))
    cur.execute(db._q(f"SELECT items FROM orders WHERE status IN ({места})"), ОТКРЫТЫЕ)
    строки = cur.fetchall()
    conn.close()
    сколько = 0
    for строка in строки:
        try:
            состав = json.loads(строка["items"] or "[]")
        except (TypeError, ValueError):
            continue
        if any(int(и.get("id") or 0) == int(pid) for и in состав if isinstance(и, dict)):
            сколько += 1
    return сколько


def get_order(order_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM orders WHERE id = %s"), (order_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_orders(limit=200, city=None):
    """Заказы, новые сверху — для админ-панели. Город — В ЗАПРОСЕ, а не фильтром
    после.

    Раньше маршрут брал двести последних заказов по ВСЕМУ магазину и только
    потом отсеивал чужие города. Пока точка одна, разницы нет. Но стоит Минску
    сделать двести заказов за день, и продавец Турова открывает пустой список
    — заказы есть, а он их не видит и не обработает. Лимит обязан считаться
    внутри того города, для которого он и нужен.
    """
    conn = db.connect()
    cur = conn.cursor()
    if city:
        cur.execute(db._q("SELECT * FROM orders WHERE city = %s "
                          "ORDER BY id DESC LIMIT %s"), (city, limit))
    else:
        cur.execute(db._q("SELECT * FROM orders ORDER BY id DESC LIMIT %s"), (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def seller_today(city=None):
    """Сводка дня: что ждёт продавца прямо сейчас и чем закончился день.

    Раньше на эти четыре числа уходило два экрана: заказы открой и посчитай,
    остаток посмотри в товарах, деньги — в статистике за месяц.

    «Сегодня» считается по дате создания заказа — так же, как в «Статистике»:
    два экрана с одинаковой подписью и разными числами хуже, чем небольшая
    неточность в редком случае «заказали вчера, забрали сегодня».
    """
    today = db.shop_now().strftime("%Y-%m-%d")
    where_city = " AND city = %s" if city else ""
    args_city = (city,) if city else ()
    conn = db.connect()
    cur = conn.cursor()

    cur.execute(db._q(f"SELECT status, COUNT(*) AS c FROM orders "
                   f"WHERE status IN ('new', 'paid', 'confirmed'){where_city} GROUP BY status"),
                args_city)
    open_by = {r["status"]: int(r["c"]) for r in cur.fetchall()}

    cur.execute(db._q(f"SELECT COUNT(*) AS c, COALESCE(SUM(total), 0) AS s FROM orders "
                   f"WHERE status = 'issued' AND created_at LIKE %s{where_city}"),
                (today + "%", *args_city))
    row = cur.fetchone()
    conn.close()
    return {
        "waiting": open_by.get("paid", 0),        # ждут подтверждения — работа на продавце
        "to_issue": open_by.get("confirmed", 0),  # подтверждены, ждут покупателя
        "unpaid": open_by.get("new", 0),          # картой без чека — ход клиента
        "issued_today": int(row["c"]),
        "revenue_today": round(float(row["s"] or 0), 2),
    }


def get_orders_by_user(user_id, limit=50):
    """Заказы конкретного клиента, новые сверху — для истории в профиле."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT %s"), (user_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def restore_order_stock(order):
    """Возвращает остаток по всем позициям заказа (учитывает вкусы-варианты)."""
    try:
        items = json.loads(order["items"])
    except (TypeError, ValueError):
        return
    for it in items:
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        if it.get("flavor"):
            db.change_variant_stock(it["id"], it["flavor"], qty)
            db.recalc_product_stock(it["id"])
        else:
            db.change_stock(it["id"], qty)


def get_open_order(user_id):
    """Последний заказ пользователя, ждущий чек (status='new'). Для чека из Mini App."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(
        "SELECT * FROM orders WHERE user_id = %s AND status = 'new' ORDER BY id DESC LIMIT 1"),
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def cancel_order(order_id, allowed=("new", "paid", "confirmed")):
    """Атомарно отменяет заказ из разрешённых состояний: возврат склада + монет.
    Возвращает order (для уведомления) или None, если уже закрыт/недопустимо."""
    order = get_order(order_id)
    if not order:
        return None
    if not set_order_status_if(order_id, "canceled", list(allowed)):
        return None
    restore_order_stock(order)
    if order["coins_used"]:
        db.add_coins(order["user_id"], order["coins_used"], "refund")
    return order


def update_order_items(order_id, quantities, coin_value):
    """Продавец меняет количества в заказе. Возвращает (order, changes) или (None, ошибка).

    Раньше у продавца было три кнопки: подтвердить, выдать, отклонить. Клиент
    просит «одну вместо двух» или «добавьте ещё» — и единственным ходом было
    отклонить заказ целиком и просить оформить заново, потеряв и заказ, и время.

    Считается одной транзакцией, как и оформление: остаток и сумма не должны
    разъехаться, если что-то упадёт посередине.
    """
    conn = db.connect()
    cur = conn.cursor()
    try:
        cur.execute(db._q("SELECT * FROM orders WHERE id = %s"), (order_id,))
        o = cur.fetchone()
        if not o:
            return None, "not_found"
        if o["status"] not in db.ORDER_EDITABLE:
            return None, "closed"           # выданный или отменённый не правим
        try:
            items = json.loads(o["items"])
        except (TypeError, ValueError):
            return None, "bad_items"

        changes = []
        for idx, want in quantities.items():
            if not (0 <= idx < len(items)):
                return None, "bad_index"
            it = items[idx]
            was, now = int(it.get("qty", 0)), max(0, int(want))
            if now == was:
                continue
            delta = now - was
            pid, flavor = it.get("id"), it.get("flavor")
            if delta > 0:                   # добавить можно только то, что есть на полке
                if flavor:
                    cur.execute(db._q("SELECT stock FROM product_variants WHERE product_id = %s AND flavor = %s"),
                                (pid, flavor))
                else:
                    cur.execute(db._q("SELECT stock FROM products WHERE id = %s"), (pid,))
                row = cur.fetchone()
                have = int(row["stock"]) if row else 0
                if have < delta:
                    return None, f"no_stock:{it.get('name', '')}:{have}"
            if flavor:
                cur.execute(db._q(f"UPDATE product_variants SET stock = {db.GREATEST}(0, stock - %s) "
                               "WHERE product_id = %s AND flavor = %s"), (delta, pid, flavor))
                cur.execute(db._q("""UPDATE products SET stock =
                                  (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                                  WHERE id = %s"""), (pid, pid))
            else:
                cur.execute(db._q(f"UPDATE products SET stock = {db.GREATEST}(0, stock - %s) WHERE id = %s"),
                            (delta, pid))
            name = it.get("name", "") + (f" · {flavor}" if flavor else "")
            changes.append(f"{name}: {was} → {now}" if now else f"{name}: убрано")
            it["qty"] = now

        if not changes:
            return None, "no_changes"
        items = [it for it in items if int(it.get("qty", 0)) > 0]
        if not items:
            return None, "empty"            # пустой заказ — это отмена, а не правка

        subtotal = sum(float(it.get("price", 0)) * int(it.get("qty", 0)) for it in items)
        # Скидку монетами не трогаем: монеты уже списаны с баланса, и урезать её
        # значило бы забрать их молча. Промокод ограничиваем новой суммой товаров,
        # иначе после урезания заказа он ушёл бы в минус.
        promo_off = round(min(float(o["promo_discount"] or 0), subtotal), 2)
        discount = round(int(o["coins_used"] or 0) * coin_value, 2)
        fee = float(o["delivery_fee"] or 0)
        total = round(max(0.0, subtotal - discount - promo_off) + fee, 2)

        cur.execute(db._q("UPDATE orders SET items = %s, total = %s, promo_discount = %s WHERE id = %s"),
                    (json.dumps(items, ensure_ascii=False), total, promo_off, order_id))
        conn.commit()
        cur.execute(db._q("SELECT * FROM orders WHERE id = %s"), (order_id,))
        return cur.fetchone(), changes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stale_new_orders(hours=24):
    """Карточные заказы, застрявшие в 'new' (чек не загружен) дольше `hours` — на авто-отмену."""
    cutoff = (db.shop_now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM orders WHERE status = 'new' AND created_at <= %s"), (cutoff,))
    rows = cur.fetchall()
    conn.close()
    return rows


def touch_order_reminded(order_id):
    """Отмечает, что по заказу только что отправлено уведомление/напоминание продавцу."""
    now = db.shop_now().strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET reminded_at = %s WHERE id = %s"), (now, order_id))
    conn.commit()
    conn.close()


def orders_needing_reminder(minutes=10):
    """Заказы, ждущие ОДОБРЕНИЯ продавца (status='paid'), по которым напоминание
    не отправлялось дольше `minutes`. Напоминаем до одобрения (потом продавец сам ведёт заказ)."""
    cutoff = (db.shop_now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM orders WHERE status = 'paid' "
                   "AND (reminded_at IS NULL OR reminded_at <= %s) ORDER BY id"), (cutoff,))
    rows = cur.fetchall()
    conn.close()
    return rows


def set_order_status(order_id, status):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, order_id))
    conn.commit()
    conn.close()


def set_order_status_if(order_id, new_status, allowed):
    """Атомарно меняет статус ТОЛЬКО если текущий статус ∈ allowed.
    Возвращает True, если переход применился (тогда вызывающий делает побочные эффекты
    — начисление/возврат — РОВНО один раз; защита от двойного клика и гонки)."""
    conn = db.connect()
    cur = conn.cursor()
    marks = ",".join(["%s"] * len(allowed))
    cur.execute(db._q(f"UPDATE orders SET status = %s WHERE id = %s AND status IN ({marks})"),
                (new_status, order_id, *allowed))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_order_receipt(order_id, file_id):
    """Сохраняет фото чека и переводит заказ в статус 'paid'."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET receipt_file_id = %s, status = 'paid' WHERE id = %s"),
                (file_id, order_id))
    conn.commit()
    conn.close()


def set_order_paid_amount(order_id, amount):
    """Сколько денег реально пришло на счёт — со слов продавца.

    Записывается в момент подтверждения заказа: продавец только что смотрел
    чек и банк, и другого такого момента не будет. Пусто здесь означает «не
    сверяли» и таким и остаётся — подставить сюда итог заказа значило бы
    нарисовать проверку, которой не было.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET paid_amount = %s WHERE id = %s"),
                (float(amount), order_id))
    conn.commit()
    conn.close()
