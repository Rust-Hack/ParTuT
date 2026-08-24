"""
partut/db/shop.py — устройство магазина: точки, доставка, категории, доступы.

Одиннадцатый кусок, вынесенный из ядра базы. Здесь не товар и не заказ, а
рамка, внутри которой они живут: в каких городах магазин работает, как оттуда
получают заказ, какие бывают категории с их полями, кто из продавцов к какой
точке приписан и что он делал.

Заполнение по умолчанию (seed_*) тоже здесь: пустой магазин обязан открыться и
показать хоть что-то, иначе первый запуск выглядит как поломка.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import json

from partut import db


def get_delivery_methods(city):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM delivery_methods WHERE city = %s ORDER BY sort, id"), (city,))
    rows = cur.fetchall()
    conn.close()
    return rows


def add_delivery_method(city, name, needs_address, address_label, pickup_address, fee, needs_payment,
                        sort=0, needs_point=False):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("""INSERT INTO delivery_methods
        (city, name, needs_address, address_label, pickup_address, fee, needs_payment, sort, needs_point)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""),
        (city, name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, int(sort), 1 if needs_point else 0))
    conn.commit()
    conn.close()


def update_delivery_method(method_id, name, needs_address, address_label, pickup_address, fee, needs_payment,
                           needs_point=False):
    """Обновляет существующий способ получения (правка на месте)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("""UPDATE delivery_methods
        SET name = %s, needs_address = %s, address_label = %s,
            pickup_address = %s, fee = %s, needs_payment = %s, needs_point = %s
        WHERE id = %s"""),
        (name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, 1 if needs_point else 0, method_id))
    conn.commit()
    conn.close()


def get_delivery_method(method_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM delivery_methods WHERE id = %s"), (method_id,))
    row = cur.fetchone()
    conn.close()
    return row


def delete_delivery_method(method_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM delivery_methods WHERE id = %s"), (method_id,))
    conn.commit()
    conn.close()


def seed_delivery():
    """Дефолтные способы получения — только если таблица пуста."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM delivery_methods")
    if cur.fetchone()["c"] != 0:
        conn.close()
        return
    conn.close()
    # Адрес самовывоза здесь НЕ выдумываем: он живёт точками города, и пустой
    # список честно скажет админу «заведите точку». Строка-напоминание в этом
    # поле выглядела как настоящий адрес и уезжала покупателю.
    defaults = [
        ("Туров", "Самовывоз", 0, "", "", 0, 1, 0),
        ("Туров", "Доставка", 1, "Адрес", "", 0, 1, 1),
        ("Минск", "Самовывоз", 0, "", "", 0, 1, 0),
        ("Минск", "Доставка по метро", 1, "Станция метро", "", 2, 1, 1),
        ("Минск", "Доставка такси", 1, "Адрес", "", 0, 0, 2),
    ]
    for d in defaults:
        add_delivery_method(*d)


def set_order_delivery(order_id, method, address, fee, payment, comment="", phone=""):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("""UPDATE orders SET delivery_method = %s, delivery_address = %s,
                      delivery_fee = %s, payment_method = %s, comment = %s, phone = %s WHERE id = %s"""),
                (method, address, float(fee or 0), payment, (comment or "").strip()[:500], (phone or "").strip()[:40], order_id))
    conn.commit()
    conn.close()


def seed_locations():
    """Стартовые локации — только если их ещё нет. Дальше админ меняет сам."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM locations")
    if cur.fetchone()["c"] == 0:
        for i, name in enumerate(["Минск", "Туров", "Лунинец"]):
            cur.execute(db._q("INSERT INTO locations (name, sort) VALUES (%s, %s)"), (name, i))
        conn.commit()
    conn.close()


def _category_code(name):
    """Латинский код из названия: «Расходники» → «rashodniki».

    Код — внутреннее имя: он попадает в ссылки и хранится в каждом товаре.
    Кириллица в таких местах живёт плохо, поэтому переводим сразу."""
    out = []
    for ch in (name or "").strip().lower():
        if ch in db._TRANSLIT:
            out.append(db._TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("_")
    code = "".join(out).strip("_")[:24]
    return code or "cat"


def seed_categories():
    """Стартовые категории — только если таблица пустая. Дальше их ведёт админ."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM categories")
    if cur.fetchone()["c"] == 0:
        for code, name, emoji, sort in db.CATEGORY_SEED:
            cur.execute(db._q("INSERT INTO categories (code, name, emoji, sort) VALUES (%s, %s, %s, %s)"),
                        (code, name, emoji, sort))
        conn.commit()
    conn.close()


def seed_category_specs():
    """Стартовые характеристики — только для категорий, у которых их ещё нет."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category AS c FROM category_specs")
    have = {r["c"] for r in cur.fetchall()}
    for category, rows in db.SPEC_SEED.items():
        if category in have:
            continue
        for i, (key, label, unit, kind, options) in enumerate(rows):
            cur.execute(db._q("INSERT INTO category_specs (category, key, label, unit, kind, options, sort) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s)"),
                        (category, key, label, unit, kind,
                         (json.dumps(options, ensure_ascii=False) if options else None), (i + 1) * 10))
    conn.commit()
    conn.close()


def list_category_specs(category=None):
    conn = db.connect()
    cur = conn.cursor()
    if category:
        cur.execute(db._q("SELECT * FROM category_specs WHERE category = %s ORDER BY sort, id"), (category,))
    else:
        cur.execute("SELECT * FROM category_specs ORDER BY category, sort, id")
    rows = [db._spec_json(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_category_spec(category, label, unit="", kind="text", options=None, key=None):
    """Добавляет характеристику категории. Возвращает id или None, если такая уже есть."""
    label = (label or "").strip()[:40]
    if not label:
        return None
    kind = kind if kind in db.SPEC_KINDS else "text"
    key = (key or _category_code(label))[:32]
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT 1 AS x FROM category_specs WHERE category = %s AND key = %s"), (category, key))
    if cur.fetchone():
        conn.close()
        return None
    cur.execute(db._q("SELECT COALESCE(MAX(sort), 0) AS mx FROM category_specs WHERE category = %s"), (category,))
    sort = int(cur.fetchone()["mx"]) + 10
    sid = db._insert_id(cur, "INSERT INTO category_specs (category, key, label, unit, kind, options, sort) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (category, key, label, (unit or "").strip()[:12], kind,
                      (json.dumps(options, ensure_ascii=False) if options else None), sort))
    conn.commit()
    conn.close()
    return sid


def update_category_spec(spec_id, label=None, unit=None, options=None, sort=None):
    """Правит подпись, единицу, варианты и порядок. Ключ не меняется — за ним значения товаров."""
    conn = db.connect()
    cur = conn.cursor()
    if label is not None:
        cur.execute(db._q("UPDATE category_specs SET label = %s WHERE id = %s"), ((label or "").strip()[:40], spec_id))
    if unit is not None:
        cur.execute(db._q("UPDATE category_specs SET unit = %s WHERE id = %s"), ((unit or "").strip()[:12], spec_id))
    if options is not None:
        cur.execute(db._q("UPDATE category_specs SET options = %s WHERE id = %s"),
                    (json.dumps(options, ensure_ascii=False) if options else None, spec_id))
    if sort is not None:
        cur.execute(db._q("UPDATE category_specs SET sort = %s WHERE id = %s"), (int(sort), spec_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_category_spec(spec_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM category_specs WHERE id = %s"), (spec_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def list_categories():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories ORDER BY sort, name")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def category_codes():
    return {c["code"] for c in list_categories()}


def add_category(name, emoji="", sort=0):
    """Добавляет категорию. Возвращает код или None, если такая уже есть."""
    name = (name or "").strip()[:40]
    if not name:
        return None
    code = _category_code(name)
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT 1 AS x FROM categories WHERE code = %s OR LOWER(name) = %s"), (code, name.lower()))
    if cur.fetchone():
        conn.close()
        return None
    if not sort:
        cur.execute("SELECT COALESCE(MAX(sort), 0) AS mx FROM categories")
        sort = int(cur.fetchone()["mx"]) + 10          # новая встаёт в конец списка
    cur.execute(db._q("INSERT INTO categories (code, name, emoji, sort) VALUES (%s, %s, %s, %s)"),
                (code, name, (emoji or "").strip()[:8], int(sort)))
    conn.commit()
    conn.close()
    return code


def update_category(code, name=None, emoji=None, sort=None, has_flavors=None):
    """Переименовать категорию или сменить значок. Код не меняется — за ним товары."""
    conn = db.connect()
    cur = conn.cursor()
    if has_flavors is not None:
        cur.execute(db._q("UPDATE categories SET has_flavors = %s WHERE code = %s"),
                    (1 if has_flavors else 0, code))
    if name is not None:
        cur.execute(db._q("UPDATE categories SET name = %s WHERE code = %s"), ((name or "").strip()[:40], code))
    if emoji is not None:
        cur.execute(db._q("UPDATE categories SET emoji = %s WHERE code = %s"), ((emoji or "").strip()[:8], code))
    if sort is not None:
        cur.execute(db._q("UPDATE categories SET sort = %s WHERE code = %s"), (int(sort), code))
    conn.commit()
    conn.close()


def count_products_in_category(code):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM products WHERE category = %s"), (code,))
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def delete_category(code):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM categories WHERE code = %s"), (code,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_pickup_points(city):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM pickup_points WHERE city = %s ORDER BY sort, id"), (city,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def all_pickup_points():
    """Все точки самовывоза всех городов — ОДНИМ запросом.

    Раньше их собирали циклом по городам, по запросу на каждый: три города —
    четыре похода в базу вместо одного. Локально не видно, а база магазина
    живёт по сети, и платит за это экран «Способ получения».
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pickup_points ORDER BY city, sort, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_pickup_point(city, address, note="", sort=0):
    conn = db.connect()
    cur = conn.cursor()
    pid = db._insert_id(cur, """INSERT INTO pickup_points (city, address, note, sort)
                             VALUES (%s, %s, %s, %s)""", (city, address, note or "", sort))
    conn.commit()
    conn.close()
    return pid


def update_pickup_point(point_id, address, note=""):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE pickup_points SET address = %s, note = %s WHERE id = %s"),
                (address, note or "", point_id))
    conn.commit()
    conn.close()


def delete_pickup_point(point_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM pickup_points WHERE id = %s"), (point_id,))
    conn.commit()
    conn.close()


def delivery_prefill(user_id, limit=20):
    """Телефон и адреса из прошлых заказов этого покупателя.

    Новой таблицы не заводим — всё уже лежит в orders. Адрес помним ОТДЕЛЬНО
    для каждого способа получения: у «Доставки по метро» это станция, у курьера
    — улица с домом, и подставлять одно вместо другого нельзя.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("""SELECT delivery_method, delivery_address, phone
                      FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT %s"""),
                (user_id, limit))
    rows = cur.fetchall()
    conn.close()

    # Телефон из настроек важнее: покупатель сам его туда вписал, а в старом
    # заказе мог быть чужой или устаревший номер.
    phone = db.get_user_phone(user_id)
    addresses = {}
    for r in rows:                       # строки идут от новых к старым
        if not phone and (r["phone"] or "").strip():
            phone = r["phone"].strip()
        method = (r["delivery_method"] or "").strip()
        addr = (r["delivery_address"] or "").strip()
        if method and addr and method not in addresses:
            addresses[method] = addr
    return {"phone": phone, "addresses": addresses}


def list_staff():
    """Все, кого супер-админ добавил через приложение."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM staff ORDER BY city, user_id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_staff(user_id, city="", note="", added_by=None):
    """Добавляет админа/продавца. Повторный вызов обновляет город и подпись."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute(
            """INSERT INTO staff (user_id, city, note, added_by, added_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET city = EXCLUDED.city, note = EXCLUDED.note""",
            (user_id, city or "", note or "", added_by, db._now_str()),
        )
    else:
        cur.execute("INSERT OR REPLACE INTO staff (user_id, city, note, added_by, added_at) "
                    "VALUES (?, ?, ?, ?, ?)", (user_id, city or "", note or "", added_by, db._now_str()))
    conn.commit()
    conn.close()


def remove_staff(user_id):
    """Убирает из приложения. Если этот id прописан в настройках сервера —
    доступ у человека останется, поэтому список показывает источник записи."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM staff WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()


def staff_ids_by_city():
    """{'': {id,...}, 'minsk': {id,...}} — для проверки прав и рассылки заказов."""
    out = {}
    for row in list_staff():
        out.setdefault(row["city"] or "", set()).add(int(row["user_id"]))
    return out


def log_admin_action(admin_id, admin_name, action, details=""):
    """Записывает, кто и что изменил. Пишется молча: упавший журнал не должен
    ронять саму операцию — продавец не виноват, что мы не смогли записать."""
    try:
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(db._q("INSERT INTO admin_log (admin_id, admin_name, action, details, created_at) "
                       "VALUES (%s, %s, %s, %s, %s)"),
                    (admin_id, (admin_name or "")[:64], (action or "")[:64],
                     (details or "")[:300], db._now_str()))
        # Чистим хвост: журнал не должен расти без предела на бесплатной базе.
        cur.execute(db._q("DELETE FROM admin_log WHERE id <= "
                       "(SELECT MAX(id) FROM admin_log) - %s"), (db.ADMIN_LOG_KEEP,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Не смог записать действие в журнал: {e}")


def list_admin_log(limit=100, admin_id=None):
    conn = db.connect()
    cur = conn.cursor()
    if admin_id:
        cur.execute(db._q("SELECT * FROM admin_log WHERE admin_id = %s ORDER BY id DESC LIMIT %s"),
                    (int(admin_id), limit))
    else:
        cur.execute(db._q("SELECT * FROM admin_log ORDER BY id DESC LIMIT %s"), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_locations():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM locations ORDER BY sort, id")
    rows = cur.fetchall()
    conn.close()
    return rows


def location_names():
    """Список названий локаций — для проверок и справочников."""
    return [r["name"] for r in get_locations()]


def get_location(location_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM locations WHERE id = %s"), (location_id,))
    row = cur.fetchone()
    conn.close()
    return row


def add_location(name):
    """Добавляет локацию (если такой ещё нет) и возвращает её id."""
    name = (name or "").strip()
    if not name:
        return None
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT id FROM locations WHERE name = %s"), (name,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        return existing["id"]
    cur.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM locations")
    s = cur.fetchone()["s"]
    new_id = db._insert_id(cur, "INSERT INTO locations (name, sort) VALUES (%s, %s)", (name, s))
    conn.commit()
    conn.close()
    return new_id


def delete_location(location_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM locations WHERE id = %s"), (location_id,))
    conn.commit()
    conn.close()


def count_products_in_location(name):
    """Сколько товаров в этой локации — чтобы не удалить локацию с товарами."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM products WHERE city = %s"), (name,))
    n = cur.fetchone()["c"]
    conn.close()
    return n


# --- Оферта и политика обработки данных ---
# Тексты правит владелец из админки: настоящие документы пишет юрист, и ждать
# выкатки ради запятой нельзя. Пока владелец их не трогал, отдаём черновик из
# partut/documents.py — пусто быть не должно, отсутствие документов заметнее
# любого черновика.

КЛЮЧ_ОФЕРТЫ = "doc_offer"
КЛЮЧ_ПОЛИТИКИ = "doc_privacy"
КЛЮЧ_РЕДАКЦИИ = "doc_version"


def documents():
    """Тексты, действующие сейчас, и номер их редакции."""
    from partut import documents as черновики
    return {
        "offer": db.get_setting(КЛЮЧ_ОФЕРТЫ) or черновики.ОФЕРТА,
        "privacy": db.get_setting(КЛЮЧ_ПОЛИТИКИ) or черновики.ПОЛИТИКА,
        "version": documents_version(),
        "своими_словами": bool(db.get_setting(КЛЮЧ_ОФЕРТЫ) or db.get_setting(КЛЮЧ_ПОЛИТИКИ)),
    }


def documents_version():
    """Номер редакции. Растёт при каждой правке — его и пишем в заказ.

    Согласие без указания, С ЧЕМ согласились, не доказывает ничего: тексты
    правятся, и через год не восстановить, что человек видел при заказе.
    """
    try:
        return int(db.get_setting(КЛЮЧ_РЕДАКЦИИ) or 1)
    except (TypeError, ValueError):
        return 1


def set_documents(offer=None, privacy=None):
    """Сохраняет тексты и поднимает номер редакции. Возвращает новый номер.

    Редакцию поднимаем, даже если поменяли один документ из двух: заказы
    ссылаются на пару целиком, и разводить два счётчика значило бы хранить
    состояние, которое некому проверить.
    """
    менялось = False
    if offer is not None and offer.strip():
        db.set_setting(КЛЮЧ_ОФЕРТЫ, offer.strip())
        менялось = True
    if privacy is not None and privacy.strip():
        db.set_setting(КЛЮЧ_ПОЛИТИКИ, privacy.strip())
        менялось = True
    if not менялось:
        return documents_version()
    новая = documents_version() + 1
    db.set_setting(КЛЮЧ_РЕДАКЦИИ, str(новая))
    return новая
