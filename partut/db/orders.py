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

    # Способы оплаты — тут же, а не отдельным кэшированным чтением: кэш
    # спасает только тёплый случай, а первый заказ после старта процесса или
    # сброса кэша всё равно добавил бы ПЯТОЕ подключение на самый горячий
    # путь. Строка в settings крошечная, второй запрос той же связи ничего
    # не стоит.
    cur.execute(db._q("SELECT key, value FROM settings WHERE key IN (%s, %s)"),
                ("pay_cash", "pay_card"))
    способы = {r["key"]: r["value"] for r in cur.fetchall()}

    conn.close()
    return {"age_ok": age_ok, "coins": coins, "products": products,
            "variants": variants, "method": method, "points": points,
            "pay_cash": str(способы.get("pay_cash", "1")) != "0",
            "pay_card": str(способы.get("pay_card", "1")) != "0"}


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


class PointSaleRefused(Exception):
    """Продажа на точке не проведена — ничего не записано. code — для экрана,
    message — человеку, extra — подробности (какая строка и сколько осталось)."""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


# Название способа получения у продажи на точке — его видно в выгрузке.
ПРОДАЖА_НА_ТОЧКЕ = "Продажа на точке"


def record_point_sale(city, lines, admin_id, seller, payment="", client_token=""):
    """Продажа на точке мимо приложения — заказ без покупателя, сразу «выдан».

    Продавец не всегда продаёт через приложение: человек подошёл к прилавку,
    заплатил, ушёл. Раньше такую продажу можно было отразить только
    списанием с неподходящей причиной — остаток верный, а денег в статистике
    нет. Решение владельца (2.10.2026): учитывать как продажу. Поэтому это
    заказ (выручка, прибыль, сводка дня, выплаты продавцу считают его сами),
    но user_id = 0 и source = 'point': в покупательское — кэшбэк, рефералка,
    розыгрыш, «давно не заказывали», счётчик выданных заказов — он не попадает.

    Всё — ОДНОЙ транзакцией, как place_order: остаток списывается условно
    («...WHERE stock >= сколько»), не хватило хоть одной строки — не
    записано ничего, и человеку названо, чего и сколько осталось. Закупка
    запоминается в составе на момент продажи — прибыль не поедет, когда
    поставщик поднимет цену. Повтор с тем же ключом (ответ не дошёл, нажали
    ещё раз) возвращает ту же продажу, а не вторую.

    lines — [{"id", "flavor", "qty", "price"}], уже проверенные ручкой.
    Возвращает (order_id, total, повтор?)."""
    token = (client_token or "").strip()
    if token:
        prev = find_order_by_token(0, token, hours=None)
        if prev:
            return int(prev["id"]), float(prev["total"]), True
    created_at = db.shop_now().strftime("%Y-%m-%d %H:%M")
    conn = db.connect()
    cur = conn.cursor()
    try:
        items, total, с_вариантами = [], 0.0, set()
        for line in lines:
            pid, flavor, qty, price = int(line["id"]), (line.get("flavor") or None), int(line["qty"]), float(line["price"])
            cur.execute(db._q("SELECT * FROM products WHERE id = %s"), (pid,))
            p = cur.fetchone()
            if not p or p["city"] != city:
                raise PointSaleRefused("not_here", "Этого товара на точке больше нет — обновите список.", id=pid)
            if p["archived"]:
                raise PointSaleRefused("archived", f"«{p['name']}» в архиве — продать его нельзя, пока не вернули.", id=pid)
            cur.execute(db._q("SELECT flavor, stock FROM product_variants WHERE product_id = %s"), (pid,))
            варианты = {r["flavor"]: int(r["stock"] or 0) for r in cur.fetchall()}
            if варианты and not flavor:
                raise PointSaleRefused("need_variant", f"«{p['name']}»: выберите, какой вариант продан.", id=pid)
            if flavor and flavor not in варианты:
                raise PointSaleRefused("variant_missing", f"«{p['name']}»: варианта «{flavor}» больше нет — обновите список.",
                                       id=pid, flavor=flavor)
            имя = f"{p['name']} — {flavor}" if flavor else p["name"]
            if flavor:
                cur.execute(db._q("UPDATE product_variants SET stock = stock - %s "
                                  "WHERE product_id = %s AND flavor = %s AND stock >= %s"), (qty, pid, flavor, qty))
                с_вариантами.add(pid)
                есть = варианты[flavor]
            else:
                cur.execute(db._q("UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s"), (qty, pid, qty))
                есть = int(p["stock"] or 0)
            if cur.rowcount < 1:
                raise PointSaleRefused("short", f"«{имя}»: на полке {есть} шт, а продано {qty}. Сначала проверьте "
                                                "остаток — может, не проведён приход.", id=pid, flavor=flavor, left=есть)
            items.append({"id": pid, "flavor": flavor, "name": имя, "price": round(price, 2),
                          "cost": round(float(p["cost"] or 0), 2), "qty": qty})
            total += price * qty
        for pid in с_вариантами:
            cur.execute(db._q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (pid, pid))
        total = round(total, 2)
        order_id = db._insert_id(
            cur,
            """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status, created_at,
                                   coins_used, delivery_method, delivery_address, delivery_fee, payment_method,
                                   comment, phone, promo_code, promo_discount, client_token, source)
               VALUES (0, %s, %s, %s, %s, '', 'issued', %s, 0, %s, '', 0, %s, '', '', NULL, 0, %s, 'point')""",
            ((seller or "")[:64], city, json.dumps(items, ensure_ascii=False), total, created_at,
             ПРОДАЖА_НА_ТОЧКЕ, payment or "", token or None),
        )
        conn.commit()
    except PointSaleRefused:
        conn.rollback()
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        # Две одинаковые отправки одновременно: уникальный ключ пропустил одну.
        if token:
            prev = find_order_by_token(0, token, hours=None)
            if prev:
                return int(prev["id"]), float(prev["total"]), True
        raise
    conn.close()
    return order_id, total, False


def point_sales(city=None, day=None):
    """Продажи на точке за день (по умолчанию — сегодня), новые сверху."""
    day = day or db.shop_now().strftime("%Y-%m-%d")
    conn = db.connect()
    cur = conn.cursor()
    if city:
        cur.execute(db._q("SELECT * FROM orders WHERE source = 'point' AND created_at LIKE %s AND city = %s "
                          "ORDER BY id DESC"), (day + "%", city))
    else:
        cur.execute(db._q("SELECT * FROM orders WHERE source = 'point' AND created_at LIKE %s ORDER BY id DESC"),
                    (day + "%",))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def cancel_point_sale(order_id):
    """Отменить продажу на точке (ошиблись) — штуки вернутся на полку.

    Только продажу на точке и только не отменённую. Товар уже в архиве —
    нет: штуки вернулись бы на полку, которой никто не видит. Отмена — та
    же cancel_order: статус и возврат склада одной транзакцией."""
    order = get_order(order_id)
    if not order or (order["source"] if "source" in order.keys() else None) != "point":
        raise PointSaleRefused("not_found", "Такой продажи нет — обновите список.")
    try:
        состав = json.loads(order["items"] or "[]")
    except (TypeError, ValueError):
        состав = []
    for it in состав:
        p = db.get_product(int(it.get("id") or 0))
        if p and p["archived"]:
            raise PointSaleRefused("archived", f"«{p['name']}» уже в архиве — сначала верните его, потом отменяйте продажу.")
    отменён = cancel_order(order_id, allowed=("issued",))
    if not отменён:
        raise PointSaleRefused("already", "Эта продажа уже отменена.")
    return отменён


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
    # Продажи на точке — не работа с заказами: им не нужен ни статус, ни
    # покупатель, и в очереди продавца они только мешали бы. Их список — в
    # «🧾 Продаже на точке» (point_sales).
    conn = db.connect()
    cur = conn.cursor()
    if city:
        cur.execute(db._q("SELECT * FROM orders WHERE city = %s AND COALESCE(source, '') <> 'point' "
                          "ORDER BY id DESC LIMIT %s"), (city, limit))
    else:
        cur.execute(db._q("SELECT * FROM orders WHERE COALESCE(source, '') <> 'point' ORDER BY id DESC LIMIT %s"),
                    (limit,))
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

    cur.execute(db._q(f"SELECT COALESCE(source, '') = 'point' AS на_точке, COUNT(*) AS c, "
                      f"COALESCE(SUM(total), 0) AS s FROM orders "
                      f"WHERE status = 'issued' AND created_at LIKE %s{where_city} "
                      f"GROUP BY COALESCE(source, '') = 'point'"),
                (today + "%", *args_city))
    по = {bool(r["на_точке"]): (int(r["c"]), float(r["s"] or 0)) for r in cur.fetchall()}
    conn.close()
    заказы, точка = по.get(False, (0, 0.0)), по.get(True, (0, 0.0))
    row = {"c": заказы[0], "s": заказы[1] + точка[1]}     # выручка дня — вместе с продажами на точке
    return {
        "waiting": open_by.get("paid", 0),        # ждут подтверждения — работа на продавце
        "to_issue": open_by.get("confirmed", 0),  # подтверждены, ждут покупателя
        "unpaid": open_by.get("new", 0),          # картой без чека — ход клиента
        "issued_today": int(row["c"]),                 # заказы из приложения
        "revenue_today": round(float(row["s"] or 0), 2),
        "point_today": точка[0],                        # продажи на точке
        "point_revenue_today": round(точка[1], 2),
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
    """Атомарно отменяет заказ из разрешённых состояний: статус + возврат склада,
    монет и промокода — ОДНОЙ транзакцией, как place_order.

    Раньше это были четыре отдельных коммита (статус, склад, монеты, промокод).
    Сбой посередине оставлял заказ 'canceled' без возврата — а повтор отклонялся:
    set_order_status_if требует старый статус, а он уже сменился. Доделать
    оставшееся было уже нечем. Возвращает order (для уведомления) или None,
    если заказа нет / статус не из allowed."""
    conn = db.connect()
    cur = conn.cursor()
    try:
        marks = ",".join(["%s"] * len(allowed))
        if db.USE_PG:
            cur.execute(db._q(f"SELECT * FROM orders WHERE id = %s AND status IN ({marks}) FOR UPDATE"),
                        (order_id, *allowed))
        else:
            cur.execute("UPDATE orders SET id = id WHERE id = ?", (order_id,))
            cur.execute(db._q(f"SELECT * FROM orders WHERE id = %s AND status IN ({marks})"),
                        (order_id, *allowed))
        order = cur.fetchone()
        if not order:
            conn.close()
            return None
        cur.execute(db._q("UPDATE orders SET status = 'canceled' WHERE id = %s"), (order_id,))

        # Возврат склада (см. restore_order_stock) — та же логика, но внутри
        # этой же транзакции, а не отдельными коммитами по функции на строку.
        try:
            items = json.loads(order["items"])
        except (TypeError, ValueError):
            items = []
        touched_variants = set()
        for it in items:
            try:
                qty = int(it.get("qty", 0))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            if it.get("flavor"):
                cur.execute(db._q(f"UPDATE product_variants SET stock = {db.GREATEST}(0, stock + %s) "
                               "WHERE product_id = %s AND flavor = %s"), (qty, it["id"], it["flavor"]))
                touched_variants.add(it["id"])
            else:
                cur.execute(db._q(f"UPDATE products SET stock = {db.GREATEST}(0, stock + %s) WHERE id = %s"),
                            (qty, it["id"]))
        for pid in touched_variants:
            cur.execute(db._q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (pid, pid))

        # Возврат монет.
        coins_used = int(order["coins_used"] or 0)
        if coins_used:
            cur.execute(db._q(f"UPDATE users SET coins = {db.GREATEST}(0, COALESCE(coins, 0) + %s) "
                           "WHERE user_id = %s"), (coins_used, order["user_id"]))

        # Промокод возвращаем так же, как склад и монеты. Скидкой никто не
        # воспользовался — значит и запас кода тратить не за что. Иначе код на
        # три применения сгорал на отменённых заказах, и следующему покупателю
        # магазин честно отвечал «разобрали», хотя не получил её ещё никто.
        код = (order["promo_code"] or "") if "promo_code" in order.keys() else ""
        скидка = float(order["promo_discount"] or 0) if "promo_discount" in order.keys() else 0.0
        if код and скидка > 0:
            cur.execute(db._q("UPDATE promos SET uses_left = uses_left + 1 "
                              "WHERE code = %s AND uses_left IS NOT NULL"), (код,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    # Летопись монет — отдельным (уже не критичным) походом в базу, как и
    # везде: если запись в coin_log не удалась, возврат самих монет уже применён.
    if coins_used:
        db.log_coins(order["user_id"], coins_used, "refund")
    return order


def issue_order(order_id, allowed=("paid", "confirmed")):
    """Атомарно выдаёт заказ: статус + кэшбэк + прогресс колеса + бонус
    рефереру — ОДНОЙ транзакцией, как place_order/cancel_order.

    Раньше статус коммитился первым (set_order_status_if), а начисления шли
    следом отдельными вызовами. Сбой между ними оставлял заказ 'issued' без
    единого бонуса — а повтор отклонялся: set_order_status_if требует старый
    статус, а он уже сменился, доделать было уже нечем. Возвращает
    (order, referral_info) или (None, None), если заказа нет / статус не из
    allowed. referral_info — то же, что раньше отдавал reward_referrer_for_order
    (для уведомления пригласившему), или None, если реферала нет."""
    # Настройки — чистое чтение, вне транзакции: они не часть отката.
    per_byn = db.coins_per_byn()
    step = int(db.wheel_step())
    bonus_first = db.referral_bonus()
    now = db.shop_now().strftime("%Y-%m-%d %H:%M")

    conn = db.connect()
    cur = conn.cursor()
    try:
        marks = ",".join(["%s"] * len(allowed))
        if db.USE_PG:
            cur.execute(db._q(f"SELECT * FROM orders WHERE id = %s AND status IN ({marks}) FOR UPDATE"),
                        (order_id, *allowed))
        else:
            cur.execute("UPDATE orders SET id = id WHERE id = ?", (order_id,))
            cur.execute(db._q(f"SELECT * FROM orders WHERE id = %s AND status IN ({marks})"),
                        (order_id, *allowed))
        order = cur.fetchone()
        if not order:
            conn.close()
            return None, None
        cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE id = %s"), (order_id,))

        try:
            items = json.loads(order["items"])
            subtotal = sum(float(it.get("price", 0)) * int(it.get("qty", 0)) for it in items)
        except (TypeError, ValueError):
            subtotal = float(order["total"] or 0)
        user_id = order["user_id"]

        # Строка покупателя обязана уже существовать (см. ensure_user) до правки coins/wheel.
        if db.USE_PG:
            cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                        (user_id, now))
        else:
            cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))

        # 1. Кэшбэк — % от суммы товаров (без доставки), как и везде.
        cashback = int(subtotal * per_byn)
        if cashback:
            cur.execute(db._q(f"UPDATE users SET coins = {db.GREATEST}(0, COALESCE(coins, 0) + %s) "
                           "WHERE user_id = %s"), (cashback, user_id))

        # 2. Прогресс колеса — та же база, что у кэшбэка.
        cur.execute(db._q("SELECT wheel_progress, wheel_spins FROM users WHERE user_id = %s"), (user_id,))
        wrow = cur.fetchone()
        prog = int((wrow["wheel_progress"] or 0) if wrow else 0) + int(subtotal)
        spins = int((wrow["wheel_spins"] or 0) if wrow else 0)
        while step > 0 and prog >= step:
            prog -= step
            spins += 1
        cur.execute(db._q("UPDATE users SET wheel_progress = %s, wheel_spins = %s WHERE user_id = %s"),
                    (prog, spins, user_id))

        # 3. Бонус пригласившему — % от суммы + фикс за первый заказ друга.
        referral_info = None
        cur.execute(db._q("SELECT referred_by FROM users WHERE user_id = %s"), (user_id,))
        urow = cur.fetchone()
        ref = urow["referred_by"] if urow else None
        if ref:
            cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s AND ref_activated = 1"),
                        (ref,))
            active = cur.fetchone()["c"]
            percent = db.ref_percent(active)
            pct_coins = round(subtotal * percent)
            earned = 0
            if pct_coins > 0:
                cur.execute(db._q(f"UPDATE users SET coins = {db.GREATEST}(0, COALESCE(coins, 0) + %s) "
                               "WHERE user_id = %s"), (pct_coins, ref))
                earned += pct_coins
            # Условие — в самом UPDATE, не «прочитали-потом-написали»: см. set_ref_activated.
            cur.execute(db._q("UPDATE users SET ref_activated = 1 "
                           "WHERE user_id = %s AND COALESCE(ref_activated, 0) = 0"), (user_id,))
            first = cur.rowcount > 0
            bonus = bonus_first if first else 0
            if first and bonus:
                cur.execute(db._q(f"UPDATE users SET coins = {db.GREATEST}(0, COALESCE(coins, 0) + %s) "
                               "WHERE user_id = %s"), (bonus, ref))
                earned += bonus
            if earned > 0:
                cur.execute(db._q("UPDATE users SET ref_earned = COALESCE(ref_earned, 0) + %s "
                               "WHERE user_id = %s"), (earned, ref))
            referral_info = {"referrer": ref, "percent": percent, "pct_coins": pct_coins,
                              "first": first, "bonus": bonus, "earned": earned}

        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    # Летопись — отчётность, не работа магазина (см. log_coins): пишем уже
    # после коммита, отдельным шагом, как и в cancel_order.
    if cashback:
        db.log_coins(user_id, cashback, "cashback")
    if referral_info:
        # Двумя отдельными строками, а не суммой: «история начислений» должна
        # показать процент и фикс за первого друга как разные события, а не
        # одно слитное число (как и раньше делали два отдельных add_coins).
        if referral_info["pct_coins"] > 0:
            db.log_coins(referral_info["referrer"], referral_info["pct_coins"], "referral", related_id=user_id)
        if referral_info["first"] and referral_info["bonus"]:
            db.log_coins(referral_info["referrer"], referral_info["bonus"], "referral", related_id=user_id)
    return order, referral_info


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
        # Блокировка строки заказа — тот же приём, что и у промокода
        # (_reserve_promo): на Postgres SELECT ... FOR UPDATE, на SQLite её роль
        # играет запись. Без неё два продавца, правящих состав ОДНОГО заказа
        # одновременно, оба читают старые items, и чей коммит позже — тот и
        # выигрывает: правки первого просто исчезают, включая уже списанный
        # остаток склада.
        if db.USE_PG:
            cur.execute("SELECT * FROM orders WHERE id = %s FOR UPDATE", (order_id,))
        else:
            cur.execute("UPDATE orders SET id = id WHERE id = ?", (order_id,))
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
            if delta > 0:
                # Добавить можно только то, что есть на полке — списываем УСЛОВНО,
                # одним запросом с «...WHERE stock >= сколько нужно» (как в
                # place_order), а не «прочитали остаток, потом списали». Иначе
                # правка ДВУХ РАЗНЫХ заказов на один и тот же товар читает одно и
                # то же число, обе проверки проходят до чьего-либо коммита — и
                # вместе заказы обещают больше, чем было физически.
                if flavor:
                    cur.execute(db._q("UPDATE product_variants SET stock = stock - %s "
                                   "WHERE product_id = %s AND flavor = %s AND stock >= %s"),
                                (delta, pid, flavor, delta))
                else:
                    cur.execute(db._q("UPDATE products SET stock = stock - %s "
                                   "WHERE id = %s AND stock >= %s"), (delta, pid, delta))
                if cur.rowcount < 1:
                    if flavor:
                        cur.execute(db._q("SELECT stock FROM product_variants "
                                       "WHERE product_id = %s AND flavor = %s"), (pid, flavor))
                    else:
                        cur.execute(db._q("SELECT stock FROM products WHERE id = %s"), (pid,))
                    row = cur.fetchone()
                    have = int(row["stock"]) if row else 0
                    return None, f"no_stock:{it.get('name', '')}:{have}"
                if flavor:
                    cur.execute(db._q("""UPDATE products SET stock =
                                      (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                                      WHERE id = %s"""), (pid, pid))
            else:
                # Возврат на склад (уменьшили количество) — гонки не боится,
                # нехватки тут не бывает.
                if flavor:
                    cur.execute(db._q(f"UPDATE product_variants SET stock = {db.GREATEST}(0, stock - %s) "
                                   "WHERE product_id = %s AND flavor = %s"), (delta, pid, flavor))
                    cur.execute(db._q("""UPDATE products SET stock =
                                      (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                                      WHERE id = %s"""), (pid, pid))
                else:
                    cur.execute(db._q(f"UPDATE products SET stock = {db.GREATEST}(0, stock - %s) WHERE id = %s"),
                                (delta, pid))
            # Вкус уже вписан в название («Cuvie Plus — Арбуз») — второй раз
            # его приписывать незачем: это увидит и покупатель в сообщении, и
            # владелец в журнале.
            имя = it.get("name", "")
            name = имя if (not flavor or flavor.lower() in имя.lower()) else f"{имя} · {flavor}"
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
    """Сохраняет фото чека и переводит заказ в статус 'paid'.

    Только из 'new': без этого условия повторная (или запоздавшая — заказ
    успели отменить по неоплате, пока чек летел до Телеграма и обратно)
    отправка чека воскрешала бы ЛЮБОЙ заказ обратно в 'paid' — включая уже
    подтверждённый, выданный (кэшбэк и % рефереру уже начислены — продавец
    нажал бы «Выдан» ещё раз и начислил бы их повторно) или уже отменённый
    (склад и монеты уже возвращены на полку). Возвращает True, только если
    заказ действительно был в 'new' и чек принят."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET receipt_file_id = %s, status = 'paid' "
                   "WHERE id = %s AND status = 'new'"), (file_id, order_id))
    принято = cur.rowcount > 0
    conn.commit()
    conn.close()
    return принято


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
