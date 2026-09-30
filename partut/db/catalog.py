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
    # Цену так меняет бот. Номер версии растёт и здесь: иначе застрявший
    # запрос из приложения со старым номером прошёл бы поверх правки из бота.
    версия = ", price_rev = price_rev + 1" if field == "price" else ""
    cur.execute(db._q(f"UPDATE products SET {field} = %s{версия} WHERE id = %s"), (value, product_id))
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


def _то_же_значение(поле, было, стало):
    """Одно ли это значение поля: «20», 20 и 20.0 — одно и то же, иначе
    сверка со снимком ругалась бы на каждое сохранение."""
    if поле in ("price", "cost"):
        try:
            return round(float(было or 0), 2) == round(float(стало or 0), 2)
        except (TypeError, ValueError):
            return False
    if поле in ("is_hit", "hidden"):
        try:
            return bool(int(было or 0)) == bool(int(стало or 0))
        except (TypeError, ValueError):
            return False            # непонятный снимок — считаем, что поле трогали
    return str(было if было is not None else "").strip() == str(стало if стало is not None else "").strip()


def _та_же_версия(было, ждали):
    """Тот ли номер версии цены, что видел человек. Непонятный номер — не тот:
    лучше лишний раз показать «цену уже поменяли», чем пропустить старый
    запрос поверх нового."""
    try:
        return int(было or 0) == int(ждали)
    except (TypeError, ValueError):
        return False


def update_fields(product_id, fields, expected=None):
    """Несколько полей товара — одним UPDATE, то есть разом или никак.

    Раньше каждое поле уходило своим запросом со своим коммитом: сбой
    посередине оставлял карточку наполовину сохранённой.

    expected — {поле: значение}, каким человек видел поле, открывая карточку.
    Если с тех пор его успел поменять кто-то другой (второй продавец, владелец
    с телефона), это поле НЕ перезаписываем, а возвращаем в конфликтах:
    молча затереть чужую правку цены — та же беда, что затереть чужую
    продажу. Остальные поля сохраняются.

    Для цены в expected может быть ещё price_rev — номер версии, который видел
    человек. Сверка по значению не видит сохранения той же цены (25 → 25), и
    застрявший старый запрос прошёл бы поверх него; сверка по номеру — видит.
    Любая запись цены номер увеличивает.

    Возвращает (сохранённые поля, {поле: текущее значение} для конфликтов,
    значения до правки — для журнала «было → стало» и номера версии цены).
    """
    поля = {k: v for k, v in (fields or {}).items() if k in db._EDITABLE}
    conn = db.connect()
    cur = conn.cursor()
    try:
        if db.USE_PG:
            cur.execute("SELECT * FROM products WHERE id = %s FOR UPDATE", (product_id,))
        else:
            cur.execute("UPDATE products SET id = id WHERE id = ?", (product_id,))
            cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        до = cur.fetchone()
        if not до:
            conn.rollback()
            conn.close()
            return [], {}, {}
        до = dict(до)
        конфликты = {}
        for поле, ждали in (expected or {}).items():
            if поле in поля and not _то_же_значение(поле, до.get(поле), ждали):
                конфликты[поле] = до.get(поле)
                поля.pop(поле)
        if "price" in поля and "price_rev" in (expected or {}) \
                and not _та_же_версия(до.get("price_rev"), expected["price_rev"]):
            конфликты["price"] = до.get("price")
            поля.pop("price")
        if поля:
            sets = ", ".join(f"{k} = %s" for k in поля)
            if "price" in поля:
                sets += ", price_rev = price_rev + 1"
            cur.execute(db._q(f"UPDATE products SET {sets} WHERE id = %s"), (*поля.values(), product_id))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return sorted(поля), конфликты, до


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
    return create_point_product(model_id, city, price, cost, is_hit=is_hit, stock=stock)


def create_point_product(model_id, city, price, cost=0, is_hit=0, stock=0, variants=None, admin_id=None):
    """Модель появляется на точке — ОДНОЙ транзакцией, с первым приходом в истории.

    Раньше это были отдельные записи подряд: товар, привязка к модели, каждый
    вариант, пересчёт итога — каждая со своим коммитом. Сбой посередине
    оставлял на точке товар без вариантов: на витрине он выглядел
    раскупленным, и почему — было не понять. А первый приход в историю склада
    не попадал вовсе: остаток появлялся из ниоткуда, и первое же «куда делось»
    упиралось в пустоту.

    variants — [{"flavor", "stock"}] у модели с вариантами; у остальных — stock.
    Возвращает id товара или None, если модели нет.
    """
    m = get_model(model_id)
    if not m:
        return None
    specs = m["specs"] or {}
    extra = {k: v for k, v in specs.items() if k not in db.SPEC_COLUMNS and str(v).strip() != ""}
    варианты = [{"flavor": str(v.get("flavor") or "").strip(), "stock": max(0, int(v.get("stock") or 0))}
                for v in (variants or []) if str(v.get("flavor") or "").strip()]
    штук = 0 if варианты else max(0, int(stock or 0))
    закупка = float(cost or 0)
    conn = db.connect()
    cur = conn.cursor()
    try:
        pid = db._insert_id(
            cur,
            """INSERT INTO products (city, category, name, price, stock, is_hit, description, brand,
                                     flavor, strength, volume, cost, model_id, specs, photo, photo_thumb)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (city, m["category"], m["name"], price, штук, 1 if is_hit else 0, m["description"],
             m["brand"], "", str(specs.get("strength", "") or ""), str(specs.get("volume", "") or ""),
             закупка, model_id, json.dumps(extra, ensure_ascii=False) if extra else None,
             m["photo"] or None, m["photo_thumb"] or None),
        )
        for v in варианты:
            cur.execute(db._q("INSERT INTO product_variants (product_id, flavor, stock) VALUES (%s, %s, %s)"),
                        (pid, v["flavor"], v["stock"]))
            if v["stock"]:
                db._record_move(cur, pid, v["flavor"], v["stock"], "in", закупка,
                                "первый завоз на точку", admin_id)
        if варианты:
            cur.execute(db._q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (pid, pid))
        elif штук:
            db._record_move(cur, pid, None, штук, "in", закупка, "первый завоз на точку", admin_id)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
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


def change_variants(product_id, add=None, remove=None, admin_id=None, writeoff=False):
    """Состав вариантов товара на точке: добавить новые, убрать ненужные.

    Остатки уже заведённых вариантов здесь НЕ меняются — это работа склада
    (приход, списание, пересчёт). Раньше карточка слала весь список с числами,
    и любая правка состава заодно переписывала остатки: добавил вкус — и
    вернул на полку то, что успели купить, пока карточка была открыта.

    add — [{"flavor", "qty"}]: новый вариант, qty — его первый приход (≥ 0);
        приход ложится в историю склада, как любой другой.
    remove — ["вкус", ...]: вариант уходит из товара.
        • Под невыданные заказы — нельзя: отмена такого заказа вернула бы
          товар варианту, которого больше нет, и штуки пропали бы молча.
        • С остатком — только с writeoff=True: остаток списывается
          пересчётом до нуля, с записью в историю. Без подтверждения — отказ
          с объяснением, а не тихая потеря.
    Товар без единого варианта оставить нельзя: остаток такого товара не посчитать.

    Всё одной транзакцией. Нельзя — db.StockRefused, и не меняется ничего.
    Возвращает {"stock", "added": [...], "removed": [...], "written_off": {вкус: штук}}.
    """
    add, remove = list(add or []), list(remove or [])
    conn = db.connect()
    cur = conn.cursor()
    try:
        # Замок в том же порядке, что у заказа: сначала варианты, потом товар.
        if db.USE_PG:
            cur.execute("SELECT id FROM product_variants WHERE product_id = %s ORDER BY id FOR UPDATE",
                        (product_id,))
            cur.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (product_id,))
        else:
            cur.execute("UPDATE products SET id = id WHERE id = ?", (product_id,))
        cur.execute(db._q("SELECT * FROM products WHERE id = %s"), (product_id,))
        товар = cur.fetchone()
        if not товар:
            raise db.StockRefused("not_found", "Товар не найден — возможно, его уже убрали с точки.")
        cur.execute(db._q("SELECT flavor, stock FROM product_variants WHERE product_id = %s ORDER BY id"),
                    (product_id,))
        было = {r["flavor"]: int(r["stock"] or 0) for r in cur.fetchall()}
        # Товар без вариантов с остатком: сумма новых вариантов заменила бы
        # его остаток, и штуки на полке исчезли бы без следа.
        if not было and int(товар["stock"] or 0) > 0 and add:
            raise db.StockRefused("has_stock",
                                  f"У товара остаток {int(товар['stock'])} шт без вариантов. Сначала "
                                  f"пересчитайте его до нуля в «Складе», потом заводите варианты.")
        по_имени = {f.lower(): f for f in было}

        уберём = []
        for f in remove:
            имя = по_имени.get(str(f or "").strip().lower())
            if имя is None:
                raise db.StockRefused("variant_missing",
                                      f"Варианта «{str(f).strip()}» у товара уже нет — обновите экран.")
            if имя not in уберём:
                уберём.append(имя)
        новые, видели = [], set()
        for a in add:
            имя = str((a or {}).get("flavor") or "").strip()
            if not имя or имя.lower() in видели:
                continue
            видели.add(имя.lower())
            есть = по_имени.get(имя.lower())
            if есть is not None and есть not in уберём:
                raise db.StockRefused("exists",
                                      f"Вариант «{есть}» уже есть — приход по нему записывают в «Складе».")
            try:
                штук = int((a or {}).get("qty") or 0)
            except (TypeError, ValueError):
                raise db.StockRefused("bad_number", f"«{имя}»: количество — целое число.")
            if штук < 0:
                raise db.StockRefused("bad_number", f"«{имя}»: количество не может быть меньше нуля.")
            новые.append((имя, штук))
        if not новые and not уберём:
            raise db.StockRefused("no_change", "Менять нечего.")
        if len(было) - len(уберём) + len(новые) <= 0:
            raise db.StockRefused("no_variants",
                                  "Нужен хотя бы один вариант — со всеми убранными остаток посчитать нечем.")

        резерв = db.reserved_stock(cur)
        for имя in уберём:
            обещано = резерв.get((int(product_id), имя), 0)
            if обещано:
                raise db.StockRefused("reserved",
                                      f"«{имя}»: {обещано} шт в невыданных заказах. Уберите вариант после "
                                      f"их выдачи или отмены — иначе отмена вернёт товар в никуда.")
            if было[имя] > 0 and not writeoff:
                raise db.StockRefused("has_stock",
                                      f"«{имя}»: на остатке {было[имя]} шт. Сначала спишите их "
                                      f"(«Склад» → списание или пересчёт), потом уберите вариант.")

        закупка = float(товар["cost"] or 0)
        списано = {}
        for имя in уберём:
            if было[имя] > 0:
                db._record_move(cur, product_id, имя, -было[имя], "fix", закупка,
                                "вариант убран из карточки", admin_id)
                списано[имя] = было[имя]
            cur.execute(db._q("DELETE FROM product_variants WHERE product_id = %s AND flavor = %s"),
                        (product_id, имя))
        for имя, штук in новые:
            cur.execute(db._q("INSERT INTO product_variants (product_id, flavor, stock) VALUES (%s, %s, %s)"),
                        (product_id, имя, штук))
            if штук:
                db._record_move(cur, product_id, имя, штук, "in", закупка, "новый вариант", admin_id)
        cur.execute(db._q("""UPDATE products SET stock =
                          (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                          WHERE id = %s"""), (product_id, product_id))
        cur.execute(db._q("SELECT stock FROM products WHERE id = %s"), (product_id,))
        итог = int(cur.fetchone()["stock"] or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return {"stock": итог, "added": [и for и, _ in новые], "removed": уберём, "written_off": списано}


def replace_variants_if(product_id, variants, expected=None, admin_id=None):
    """Старый вход «весь список вариантов с числами» — для страниц, открытых до
    обновления приложения. Они ещё какое-то время будут слать запросы по-старому.

    Теперь он только переводит список в change_variants: новые варианты — с
    первым приходом, пропавшие — убираются по тем же правилам. Изменить число
    у уже заведённого варианта так больше нельзя: остаток меняет только склад,
    иначе правка шла бы мимо истории. Такой запрос получает отказ с
    объяснением, а не молчаливую перезапись.

    expected — снимок, каким человек видел список при открытии карточки: с ним
    сравниваем, трогал ли он числа. Продажа, случившаяся за это время, правкой
    не считается и отказом не оборачивается.
    """
    по_имени = {str(v["flavor"]).strip().lower(): v for v in variants}
    сейчас = {v["flavor"]: int(v["stock"] or 0) for v in get_variants(product_id)}
    снимок = ({str(v["flavor"]).strip().lower(): int(v["stock"]) for v in expected}
              if expected is not None else {f.lower(): s for f, s in сейчас.items()})
    тронули = [f for f in сейчас
               if f.lower() in по_имени and f.lower() in снимок
               and int(по_имени[f.lower()]["stock"]) != снимок[f.lower()]]
    if тронули:
        raise db.StockRefused("use_stock_moves",
                              "Остаток уже заведённого варианта меняется только в «Складе» (приход, "
                              "списание, пересчёт) — чтобы каждое изменение осталось в истории. "
                              "Обновите приложение. Не сохранилось: " + ", ".join(тронули) + ".")
    известные = {f.lower() for f in сейчас}
    добавить = [{"flavor": v["flavor"], "qty": v["stock"]} for к, v in по_имени.items() if к not in известные]
    убрать = [f for f in сейчас if f.lower() not in по_имени]
    if not добавить and not убрать:
        return {"stock": sum(сейчас.values()), "added": [], "removed": [], "written_off": {}}
    return change_variants(product_id, добавить, убрать, admin_id=admin_id)


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
