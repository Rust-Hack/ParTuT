"""
partut/db/catalog.py — ассортимент в базе: товары, модели, бренды, вкусы.

Восьмой кусок, вынесенный из ядра базы. Здесь живёт то, чем магазин торгует:
товар на точке, модель (тот же товар в разных городах), бренд со списком
вкусов и варианты — вкус с собственным остатком.

Локации сюда не переехали намеренно: точка продаж — это не товар, а устройство
магазина, и лежит она рядом со способами получения.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import json

from partut import db


def get_products(city, category):
    """Товары города и категории: сначала в наличии, потом хиты, потом дешевле."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(
        """SELECT * FROM products
           WHERE city = %s AND category = %s
           ORDER BY (stock > 0) DESC, is_hit DESC, price ASC"""),
        (city, category),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_product(product_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM products WHERE id = %s"), (product_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_products():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products ORDER BY city, category, name")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_product(city, category, name, price, stock, is_hit=0, description="",
                brand="", flavor="", strength="", volume="", cost=0):
    conn = db.connect()
    cur = conn.cursor()
    new_id = db._insert_id(
        cur,
        """INSERT INTO products (city, category, name, price, stock, is_hit, description,
                                 brand, flavor, strength, volume, cost)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (city, category, name, price, stock, is_hit, description,
         brand, flavor, strength, volume, float(cost or 0)),
    )
    conn.commit()
    conn.close()
    return new_id


def hide_model_products(model_id, hidden):
    """Снять модель с витрины сразу на всех точках (или вернуть). Возвращает,
    скольких товаров коснулось: продавцу важно понимать масштаб действия."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET hidden = %s WHERE model_id = %s"),
                (1 if hidden else 0, model_id))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def update_field(product_id, field, value):
    if field not in db._EDITABLE:
        return False
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE products SET {field} = %s WHERE id = %s"), (value, product_id))
    conn.commit()
    conn.close()
    return True


def toggle_hit(product_id):
    product = get_product(product_id)
    if not product:
        return None
    new_value = 0 if product["is_hit"] == 1 else 1
    update_field(product_id, "is_hit", new_value)
    return new_value


def delete_product(product_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM products WHERE id = %s"), (product_id,))
    # Галерея без товара никому не видна, но место занимает и мешает считать
    # картинки — убираем вместе с товаром.
    cur.execute(db._q("DELETE FROM product_photos WHERE product_id = %s"), (product_id,))
    # Отзывы о модели переживают снятие с точки: человек оценивал вещь, а не
    # факт её наличия в Турове. Раньше товар уносил с собой чужие слова —
    # вернул модель на точку через месяц, а отзывов уже нет.
    cur.execute(db._q("DELETE FROM reviews WHERE product_id = %s AND model_id IS NULL"), (product_id,))
    # Хвосты: кто-то ждал этот товар или отметил его сердечком — товара больше
    # нет, ждать и показывать в избранном нечего.
    cur.execute(db._q("DELETE FROM stock_alerts WHERE product_id = %s"), (product_id,))
    cur.execute(db._q("DELETE FROM favorites WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def change_stock(product_id, delta):
    """Меняет остаток на delta, не опускаясь ниже нуля."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE products SET stock = {db.GREATEST}(0, stock + %s) WHERE id = %s"),
                (delta, product_id))
    conn.commit()
    conn.close()


def get_brands(category=None):
    """Бренды. category — фильтр «для этой категории»: бренд с пустой категорией
    общий (Vaporesso делает и поды, и картриджи) и попадает в любой список."""
    conn = db.connect()
    cur = conn.cursor()
    if category:
        cur.execute(db._q("SELECT * FROM brands WHERE category = %s OR category IS NULL OR category = '' ORDER BY name"),
                    (category,))
    else:
        cur.execute("SELECT * FROM brands ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_brand(brand_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM brands WHERE id = %s"), (brand_id,))
    row = cur.fetchone()
    conn.close()
    return row


def find_brand_by_name(name, except_id=None):
    """Бренд с таким именем (без учёта регистра). Нужен, чтобы не плодить дубли:
    «Vaporesso» и «vaporesso» в фильтре выглядят как два разных бренда.

    Сравниваем на стороне Python, а не через SQL LOWER(): у SQLite он приводит
    к нижнему регистру только ASCII, кириллица проходит как есть — «Хаски» и
    «хаски» для него разные строки (Postgres с этим справляется сам, но код
    должен работать одинаково на обоих)."""
    target = (name or "").strip().lower()
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM brands")
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        if (r["name"] or "").strip().lower() == target and (except_id is None or int(r["id"]) != int(except_id)):
            return r
    return None


def count_products_of_brand(name):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM products WHERE brand = %s"), ((name or "").strip(),))
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def rename_brand_in_products(old_name, new_name):
    """Переносит товары на новое имя бренда.

    Товар хранит бренд строкой, и без этого переименование в справочнике
    оставляло у товаров старое имя: в фильтре каталога появлялся «призрак» —
    бренд, которого в справочнике уже нет."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return 0
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET brand = %s WHERE brand = %s"), (new_name, old_name))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def known_flavors(limit=200):
    """Все вкусы, которые уже встречались: в брендах, в вариантах и у товаров.

    Нужны для подсказок при вводе — иначе «Мята», «мята» и «Мята ❄️» живут
    в базе как три разных вкуса, и фильтр по вкусу разваливается."""
    conn = db.connect()
    cur = conn.cursor()
    out = {}
    cur.execute("SELECT flavors FROM brands")
    for r in cur.fetchall():
        try:
            for f in json.loads(r["flavors"] or "[]"):
                out.setdefault(str(f).strip().lower(), str(f).strip())
        except (TypeError, ValueError):
            pass
    cur.execute("SELECT DISTINCT flavor AS f FROM product_variants")
    for r in cur.fetchall():
        if r["f"]:
            out.setdefault(r["f"].strip().lower(), r["f"].strip())
    cur.execute("SELECT DISTINCT flavor AS f FROM products WHERE flavor IS NOT NULL AND flavor != ''")
    for r in cur.fetchall():
        if r["f"]:
            out.setdefault(r["f"].strip().lower(), r["f"].strip())
    conn.close()
    return sorted(out.values(), key=lambda s: s.lower())[:limit]


def merge_duplicate_brands():
    """Разовое слияние: один бренд — одна запись.

    Раньше бренд заводился внутри категории, поэтому «Vaporesso» приходилось
    создавать отдельно для подсистем и отдельно для картриджей. Теперь бренд
    общий, а дубли, оставшиеся от прежней схемы, сливаем: вкусы объединяем,
    категорию у слитого бренда очищаем («во всех категориях»)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM brands ORDER BY id")
    by_name = {}
    for r in cur.fetchall():
        key = (r["name"] or "").strip().lower()
        by_name.setdefault(key, []).append(r)
    merged = 0
    for rows in by_name.values():
        if len(rows) < 2:
            continue
        keep = rows[0]
        flavors = []
        for r in rows:
            try:
                for f in json.loads(r["flavors"] or "[]"):
                    if f not in flavors:
                        flavors.append(f)
            except (TypeError, ValueError):
                pass
        cur.execute(db._q("UPDATE brands SET flavors = %s, category = %s WHERE id = %s"),
                    (json.dumps(flavors, ensure_ascii=False), "", keep["id"]))
        for r in rows[1:]:
            cur.execute(db._q("DELETE FROM brands WHERE id = %s"), (r["id"],))
            merged += 1
    conn.commit()
    conn.close()
    return merged


def add_brand(name, category, flavors):
    """flavors — список строк; храним как JSON. Возвращает id."""
    conn = db.connect()
    cur = conn.cursor()
    new_id = db._insert_id(
        cur, "INSERT INTO brands (name, category, flavors) VALUES (%s, %s, %s)",
        (name.strip(), category, json.dumps(flavors, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return new_id


def update_brand(brand_id, name, category, flavors):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE brands SET name = %s, category = %s, flavors = %s WHERE id = %s"),
                (name.strip(), category, json.dumps(flavors, ensure_ascii=False), brand_id))
    conn.commit()
    conn.close()


def delete_brand(brand_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM brands WHERE id = %s"), (brand_id,))
    conn.commit()
    conn.close()


def _model_json(r):
    def _load(raw, default):
        try:
            return json.loads(raw) if raw else default
        except (TypeError, ValueError):
            return default
    return {"id": r["id"], "category": r["category"], "brand": r["brand"] or "", "name": r["name"],
            "description": r["description"] or "", "specs": _load(r["specs"], {}),
            "flavors": _load(r["flavors"], []),
            "photo": r["photo"] or "", "photo_thumb": r["photo_thumb"] or ""}


def list_models(category=None):
    conn = db.connect()
    cur = conn.cursor()
    if category:
        cur.execute(db._q("SELECT * FROM models WHERE category = %s ORDER BY brand, name"), (category,))
    else:
        cur.execute("SELECT * FROM models ORDER BY category, brand, name")
    rows = [_model_json(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_model(model_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM models WHERE id = %s"), (model_id,))
    row = cur.fetchone()
    conn.close()
    return _model_json(row) if row else None


def add_model(category, name, brand="", description="", specs=None, flavors=None):
    conn = db.connect()
    cur = conn.cursor()
    mid = db._insert_id(cur, "INSERT INTO models (category, brand, name, description, specs, flavors, created_at) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (category, (brand or "").strip(), (name or "").strip(), (description or "").strip(),
                      json.dumps(specs or {}, ensure_ascii=False),
                      json.dumps(flavors or [], ensure_ascii=False), db._now_str()))
    conn.commit()
    conn.close()
    return mid


def merge_model_flavors(model_id, вкусы):
    """Добавляет вкусы в список модели, не трогая уже записанные.

    Вкус, заведённый на точке, обязан попасть в модель — иначе списки
    расходятся молча: в Горках вкус есть, а завезти его в Минск нельзя, потому
    что модель о нём не знает. Ровно на это и наступили.

    Только добавляем. Кончился вкус в одном городе — не повод считать, что его
    больше не бывает: остальные точки его ещё продают, и предлагать его надо.

    Сравниваем без учёта регистра: «Мята» и «мята» — один вкус, и разводить их
    значит развалить фильтр по вкусу на витрине.
    """
    m = get_model(model_id)
    if not m:
        return []
    было = list(m["flavors"] or [])
    известно = {str(f).strip().lower() for f in было}
    добавлено = []
    for f in вкусы:
        имя = str(f or "").strip()
        if имя and имя.lower() not in известно:
            известно.add(имя.lower())
            было.append(имя)
            добавлено.append(имя)
    if добавлено:
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(db._q("UPDATE models SET flavors = %s WHERE id = %s"),
                    (json.dumps(было, ensure_ascii=False), model_id))
        conn.commit()
        conn.close()
    return добавлено


def update_model(model_id, category=None, name=None, brand=None, description=None, specs=None, flavors=None):
    """Правит модель и переносит изменения на все её товары.

    Ради этого модель и заводится: описание живёт в одном месте, а не в трёх
    копиях по точкам, которые расходятся при первой же правке."""
    conn = db.connect()
    cur = conn.cursor()
    sets, params = [], []
    for col, val in (("category", category), ("name", name), ("brand", brand), ("description", description)):
        if val is not None:
            sets.append(f"{col} = %s")
            params.append(str(val).strip())
    if specs is not None:
        sets.append("specs = %s")
        params.append(json.dumps(specs, ensure_ascii=False))
    if flavors is not None:
        sets.append("flavors = %s")
        params.append(json.dumps(flavors, ensure_ascii=False))
    if sets:
        cur.execute(db._q(f"UPDATE models SET {', '.join(sets)} WHERE id = %s"), (*params, model_id))
    conn.commit()
    conn.close()
    return propagate_model(model_id)


def propagate_model(model_id):
    """Разносит описание модели по её товарам на точках. Цену, закупку и остаток
    не трогает — это как раз то, что у каждой точки своё."""
    m = get_model(model_id)
    if not m:
        return 0
    specs = {k: v for k, v in (m["specs"] or {}).items() if k not in db.SPEC_COLUMNS}
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET category = %s, brand = %s, name = %s, description = %s, specs = %s "
                   "WHERE model_id = %s"),
                (m["category"], m["brand"], m["name"], m["description"],
                 json.dumps(specs, ensure_ascii=False) if specs else None, model_id))
    n = cur.rowcount
    for col in db.SPEC_COLUMNS:
        cur.execute(db._q(f"UPDATE products SET {col} = %s WHERE model_id = %s"),
                    (str((m["specs"] or {}).get(col, "") or ""), model_id))
    if m["photo"]:
        cur.execute(db._q("UPDATE products SET photo = %s, photo_thumb = %s WHERE model_id = %s"),
                    (m["photo"], m["photo_thumb"], model_id))
    conn.commit()
    conn.close()
    return n


def orphan_flavors(model_id):
    """Вкусы, которые остались на точках, но из модели уже убраны.

    Остаток стирать нельзя — это реальный товар на полке. Но и молчать нельзя:
    вкус продолжает продаваться, а в модели его нет, и следующий завоз про
    него не вспомнит."""
    m = get_model(model_id)
    if not m:
        return []
    known = {f.strip().lower() for f in m["flavors"]}
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT v.flavor AS flavor, SUM(v.stock) AS stock FROM product_variants v "
                   "JOIN products p ON p.id = v.product_id WHERE p.model_id = %s "
                   "GROUP BY v.flavor"), (model_id,))
    out = [{"flavor": r["flavor"], "stock": int(r["stock"] or 0)}
           for r in cur.fetchall() if r["flavor"].strip().lower() not in known]
    conn.close()
    return out


def count_products_of_model(model_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS c FROM products WHERE model_id = %s"), (model_id,))
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def delete_model(model_id):
    """Убирает модель из ассортимента. Товары на точках остаются — их снимают
    с продажи отдельно, иначе одно нажатие стирало бы остатки всех точек."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET model_id = NULL WHERE model_id = %s"), (model_id,))
    # Галерея модели — не товара: без этого фото оставались бы в базе навсегда,
    # ничем больше не удерживаемые.
    cur.execute(db._q("DELETE FROM product_photos WHERE model_id = %s"), (model_id,))
    cur.execute(db._q("DELETE FROM models WHERE id = %s"), (model_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def add_product_from_model(model_id, city, price, cost=0, stock=0, is_hit=0):
    """Заводит наличие модели на точке. Описание берётся из модели целиком."""
    m = get_model(model_id)
    if not m:
        return None
    specs = m["specs"] or {}
    pid = add_product(city, m["category"], m["name"], price, stock, is_hit, m["description"],
                      brand=m["brand"], flavor="",
                      strength=str(specs.get("strength", "") or ""),
                      volume=str(specs.get("volume", "") or ""), cost=cost)
    extra = {k: v for k, v in specs.items() if k not in db.SPEC_COLUMNS and str(v).strip() != ""}
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET model_id = %s, specs = %s, photo = %s, photo_thumb = %s WHERE id = %s"),
                (model_id, json.dumps(extra, ensure_ascii=False) if extra else None,
                 m["photo"] or None, m["photo_thumb"] or None, pid))
    conn.commit()
    conn.close()
    return pid


def get_variants(product_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM product_variants WHERE product_id = %s ORDER BY id"), (product_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_variants():
    """Все варианты сразу — чтобы разложить по товарам без запроса на каждый."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_variants ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_variant(product_id, flavor, stock):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("INSERT INTO product_variants (product_id, flavor, stock) VALUES (%s, %s, %s)"),
                (product_id, flavor, max(0, stock)))
    conn.commit()
    conn.close()


def delete_variants(product_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM product_variants WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def change_variant_stock(product_id, flavor, delta):
    """Меняет остаток конкретного вкуса и пересчитывает общий остаток товара."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q(f"UPDATE product_variants SET stock = {db.GREATEST}(0, stock + %s) "
                   "WHERE product_id = %s AND flavor = %s"),
                (delta, product_id, flavor))
    conn.commit()
    conn.close()
    recalc_product_stock(product_id)


def recalc_product_stock(product_id):
    """Общий остаток товара-модели = сумма остатков его вкусов."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT COALESCE(SUM(stock), 0) AS s FROM product_variants WHERE product_id = %s"),
                (product_id,))
    total = cur.fetchone()["s"]
    cur.execute(db._q("UPDATE products SET stock = %s WHERE id = %s"), (total, product_id))
    conn.commit()
    conn.close()
