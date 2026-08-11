"""
db.py — хранение данных с автопереключением базы.

  • Если в .env ПУСТОЙ DATABASE_URL  → используем локальный SQLite (файл shop.db).
    Удобно для разработки: запускается сразу, ничего настраивать не надо.
  • Если DATABASE_URL ЗАПОЛНЕН (строка Neon) → используем облачный Postgres.
    Так нужно на хостинге (Render), чтобы данные не терялись.

Один и тот же код работает с обеими базами — мелкие различия в диалектах
SQL спрятаны в маленькие помощники ниже (_q, ID_COL, GREATEST, upsert).
"""

import os
import json
import datetime

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)           # True = Postgres, False = локальный SQLite

if USE_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3
    SQLITE_FILE = "shop.db"

# Диалектные различия, которые встречаются в наших запросах:
ID_COL = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
GREATEST = "GREATEST" if USE_PG else "max"   # ограничение остатка снизу нулём


def connect():
    """Открывает соединение с нужной базой. Строки возвращаются как словари."""
    if USE_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect(SQLITE_FILE)
    conn.row_factory = sqlite3.Row        # доступ к колонкам по имени: row["name"]
    return conn


def _q(sql):
    """SQLite ждёт '?' вместо '%s'. Пишем запросы с %s, а тут при необходимости меняем."""
    return sql if USE_PG else sql.replace("%s", "?")


def _insert_id(cur, sql, params):
    """Вставляет строку и возвращает id новой записи (по-разному в PG и SQLite)."""
    if USE_PG:
        cur.execute(_q(sql) + " RETURNING id", params)
        return cur.fetchone()["id"]
    cur.execute(_q(sql), params)
    return cur.lastrowid


def init_db():
    """Создаёт таблицы, если их ещё нет."""
    conn = connect()
    cur = conn.cursor()

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS products (
            id          {ID_COL},
            city        TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            name        TEXT    NOT NULL,
            price       REAL    NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            is_hit      INTEGER NOT NULL DEFAULT 0,
            photo       TEXT,
            description TEXT,
            brand       TEXT,
            flavor      TEXT,
            strength    TEXT,
            volume      TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            age_ok  INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS orders (
            id              {ID_COL},
            user_id         BIGINT  NOT NULL,
            username        TEXT,
            city            TEXT    NOT NULL,
            items           TEXT    NOT NULL,
            total           REAL    NOT NULL,
            pickup_time     TEXT,
            status          TEXT    NOT NULL DEFAULT 'new',
            receipt_file_id TEXT,
            created_at      TEXT    NOT NULL
        )
    """)

    # Локации (точки продаж) — теперь ими управляет админ, а не жёстко в коде.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS locations (
            id   {ID_COL},
            name TEXT    NOT NULL,
            sort INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Бренды со списком вкусов — «база» админа. flavors хранится как JSON-массив.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS brands (
            id       {ID_COL},
            name     TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'disposable',
            flavors  TEXT
        )
    """)

    # Варианты товара: у одной карточки-модели несколько вкусов, у каждого свой остаток.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS product_variants (
            id         {ID_COL},
            product_id INTEGER NOT NULL,
            flavor     TEXT    NOT NULL,
            stock      INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    _ensure_product_columns()   # доклеит новые колонки на старой базе (миграция)
    seed_locations()            # добавит стартовые точки, если таблица пустая


def _table_columns(cur, table):
    """Возвращает множество имён колонок таблицы (для аккуратной миграции)."""
    if USE_PG:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
        return {r["column_name"] for r in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table})")
    return {r["name"] for r in cur.fetchall()}


def _ensure_product_columns():
    """Добавляет новые колонки товара, если их ещё нет (работает и в SQLite, и в Postgres)."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "products")
    for c in ("brand", "flavor", "strength", "volume"):
        if c not in cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {c} TEXT")
    conn.commit()
    conn.close()


def seed_locations():
    """Стартовые локации — только если их ещё нет. Дальше админ меняет сам."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM locations")
    if cur.fetchone()["c"] == 0:
        for i, name in enumerate(["Минск", "Туров", "Лунинец"]):
            cur.execute(_q("INSERT INTO locations (name, sort) VALUES (%s, %s)"), (name, i))
        conn.commit()
    conn.close()


# ---------- 18+ ----------

def is_age_ok(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT age_ok FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["age_ok"] == 1)


def set_age_ok(user_id):
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            """INSERT INTO users (user_id, age_ok) VALUES (%s, 1)
               ON CONFLICT (user_id) DO UPDATE SET age_ok = 1""",
            (user_id,),
        )
    else:
        cur.execute("INSERT OR REPLACE INTO users (user_id, age_ok) VALUES (?, 1)", (user_id,))
    conn.commit()
    conn.close()


# ---------- Товары ----------

def get_products(city, category):
    """Товары города и категории: сначала в наличии, потом хиты, потом дешевле."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(
        """SELECT * FROM products
           WHERE city = %s AND category = %s
           ORDER BY (stock > 0) DESC, is_hit DESC, price ASC"""),
        (city, category),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_product(product_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM products WHERE id = %s"), (product_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_products():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products ORDER BY city, category, name")
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- Админка: добавить / изменить / удалить ----------

def add_product(city, category, name, price, stock, is_hit=0, description="",
                brand="", flavor="", strength="", volume=""):
    conn = connect()
    cur = conn.cursor()
    new_id = _insert_id(
        cur,
        """INSERT INTO products (city, category, name, price, stock, is_hit, description,
                                 brand, flavor, strength, volume)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (city, category, name, price, stock, is_hit, description,
         brand, flavor, strength, volume),
    )
    conn.commit()
    conn.close()
    return new_id


# Какие колонки разрешено менять (защита: имя колонки нельзя подставить параметром).
_EDITABLE = {"name", "price", "stock", "is_hit", "description", "photo",
             "brand", "flavor", "strength", "volume"}


def update_field(product_id, field, value):
    if field not in _EDITABLE:
        return False
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE products SET {field} = %s WHERE id = %s"), (value, product_id))
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM products WHERE id = %s"), (product_id,))
    conn.commit()
    conn.close()


def change_stock(product_id, delta):
    """Меняет остаток на delta, не опускаясь ниже нуля."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE products SET stock = {GREATEST}(0, stock + %s) WHERE id = %s"),
                (delta, product_id))
    conn.commit()
    conn.close()


# ---------- Заказы ----------

def create_order(user_id, username, city, items, total, pickup_time):
    """Создаёт заказ и возвращает его id. items -> строка JSON."""
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    order_id = _insert_id(
        cur,
        """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, 'new', %s)""",
        (user_id, username, city,
         json.dumps(items, ensure_ascii=False), total, pickup_time, created_at),
    )
    conn.commit()
    conn.close()
    return order_id


def get_order(order_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE id = %s"), (order_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_open_order(user_id):
    """Последний заказ пользователя, ждущий чек (status='new'). Для чека из Mini App."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(
        "SELECT * FROM orders WHERE user_id = %s AND status = 'new' ORDER BY id DESC LIMIT 1"),
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def set_order_status(order_id, status):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET status = %s WHERE id = %s"), (status, order_id))
    conn.commit()
    conn.close()


def set_order_receipt(order_id, file_id):
    """Сохраняет фото чека и переводит заказ в статус 'paid'."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET receipt_file_id = %s, status = 'paid' WHERE id = %s"),
                (file_id, order_id))
    conn.commit()
    conn.close()


# ---------- Локации (точки продаж) ----------

def get_locations():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM locations ORDER BY sort, id")
    rows = cur.fetchall()
    conn.close()
    return rows


def location_names():
    """Список названий локаций — для проверок и справочников."""
    return [r["name"] for r in get_locations()]


def get_location(location_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM locations WHERE id = %s"), (location_id,))
    row = cur.fetchone()
    conn.close()
    return row


def add_location(name):
    """Добавляет локацию (если такой ещё нет) и возвращает её id."""
    name = (name or "").strip()
    if not name:
        return None
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT id FROM locations WHERE name = %s"), (name,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        return existing["id"]
    cur.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM locations")
    s = cur.fetchone()["s"]
    new_id = _insert_id(cur, "INSERT INTO locations (name, sort) VALUES (%s, %s)", (name, s))
    conn.commit()
    conn.close()
    return new_id


def delete_location(location_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM locations WHERE id = %s"), (location_id,))
    conn.commit()
    conn.close()


def count_products_in_location(name):
    """Сколько товаров в этой локации — чтобы не удалить локацию с товарами."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM products WHERE city = %s"), (name,))
    n = cur.fetchone()["c"]
    conn.close()
    return n


# ---------- Бренды (со списком вкусов) ----------

def get_brands(category=None):
    conn = connect()
    cur = conn.cursor()
    if category:
        cur.execute(_q("SELECT * FROM brands WHERE category = %s ORDER BY name"), (category,))
    else:
        cur.execute("SELECT * FROM brands ORDER BY category, name")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_brand(brand_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM brands WHERE id = %s"), (brand_id,))
    row = cur.fetchone()
    conn.close()
    return row


def add_brand(name, category, flavors):
    """flavors — список строк; храним как JSON. Возвращает id."""
    conn = connect()
    cur = conn.cursor()
    new_id = _insert_id(
        cur, "INSERT INTO brands (name, category, flavors) VALUES (%s, %s, %s)",
        (name.strip(), category, json.dumps(flavors, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return new_id


def update_brand(brand_id, name, category, flavors):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE brands SET name = %s, category = %s, flavors = %s WHERE id = %s"),
                (name.strip(), category, json.dumps(flavors, ensure_ascii=False), brand_id))
    conn.commit()
    conn.close()


def delete_brand(brand_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM brands WHERE id = %s"), (brand_id,))
    conn.commit()
    conn.close()


# ---------- Варианты товара (вкус + остаток) ----------

def get_variants(product_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM product_variants WHERE product_id = %s ORDER BY id"), (product_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_variants():
    """Все варианты сразу — чтобы разложить по товарам без запроса на каждый."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_variants ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_variant(product_id, flavor, stock):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("INSERT INTO product_variants (product_id, flavor, stock) VALUES (%s, %s, %s)"),
                (product_id, flavor, max(0, stock)))
    conn.commit()
    conn.close()


def delete_variants(product_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM product_variants WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def change_variant_stock(product_id, flavor, delta):
    """Меняет остаток конкретного вкуса и пересчитывает общий остаток товара."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE product_variants SET stock = {GREATEST}(0, stock + %s) "
                   "WHERE product_id = %s AND flavor = %s"),
                (delta, product_id, flavor))
    conn.commit()
    conn.close()
    recalc_product_stock(product_id)


def recalc_product_stock(product_id):
    """Общий остаток товара-модели = сумма остатков его вкусов."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COALESCE(SUM(stock), 0) AS s FROM product_variants WHERE product_id = %s"),
                (product_id,))
    total = cur.fetchone()["s"]
    cur.execute(_q("UPDATE products SET stock = %s WHERE id = %s"), (total, product_id))
    conn.commit()
    conn.close()
