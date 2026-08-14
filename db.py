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
    from psycopg2 import pool as _pgpool
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3
    SQLITE_FILE = "shop.db"

# Диалектные различия, которые встречаются в наших запросах:
ID_COL = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
GREATEST = "GREATEST" if USE_PG else "max"   # ограничение остатка снизу нулём
BLOB_COL = "BYTEA" if USE_PG else "BLOB"     # колонка для двоичных данных (картинок)


# --- Пул соединений к Postgres (Neon): держим их «тёплыми» и переиспользуем ---
_POOL = None


def _get_pool():
    global _POOL
    if _POOL is None:
        # keepalives — чтобы ОС замечала «мёртвые» соединения (Neon рвёт простаивающие)
        _POOL = _pgpool.ThreadedConnectionPool(
            1, 20, DATABASE_URL, cursor_factory=RealDictCursor,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    return _POOL


class _PooledConn:
    """Обёртка: .close() возвращает соединение в пул, а не закрывает его."""
    def __init__(self, raw):
        self._raw = raw

    def cursor(self, *a, **k):
        return self._raw.cursor(*a, **k)

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        try:
            self._raw.rollback()          # сброс любой незавершённой транзакции
        except Exception:
            pass
        try:
            _get_pool().putconn(self._raw)
        except Exception:
            try:
                self._raw.close()
            except Exception:
                pass


def connect():
    """Открывает соединение с нужной базой. Строки возвращаются как словари."""
    if USE_PG:
        pool = _get_pool()
        raw = pool.getconn()
        if getattr(raw, "closed", 0):     # уже закрыто — берём другое
            try:
                pool.putconn(raw, close=True)
            except Exception:
                pass
            raw = pool.getconn()
        return _PooledConn(raw)
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
            photo_thumb TEXT,
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

    # Заявки обычных админов на чувствительные операции — ждут подтверждения супер-админа.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS admin_requests (
            id             {ID_COL},
            requester_id   BIGINT,
            requester_name TEXT,
            action         TEXT,
            payload        TEXT,
            summary        TEXT,
            status         TEXT DEFAULT 'pending',
            created_at     TEXT
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

    # Настройки магазина (ключ-значение): реквизиты оплаты, время подтверждения и т.п.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Розыгрыши (раз в месяц): приз-места + участники.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS raffles (
            id           {ID_COL},
            title        TEXT,
            prize1       TEXT,
            prize2       TEXT,
            prize3_coins INTEGER NOT NULL DEFAULT 500,
            threshold    REAL    NOT NULL DEFAULT 25,
            starts_at    TEXT    NOT NULL,
            ends_at      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'active',
            winners      TEXT,
            created_at   TEXT
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS raffle_entries (
            id        {ID_COL},
            raffle_id INTEGER NOT NULL,
            user_id   BIGINT  NOT NULL
        )
    """)

    # Счётчики игр (колесо/слот): прокруты, ставки, выплаты.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            key TEXT PRIMARY KEY,
            n   BIGINT NOT NULL DEFAULT 0
        )
    """)

    # Способы получения на точку: самовывоз/доставка/метро/такси (настраивает админ).
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS delivery_methods (
            id             {ID_COL},
            city           TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            needs_address  INTEGER NOT NULL DEFAULT 0,
            address_label  TEXT,
            pickup_address TEXT,
            fee            REAL    NOT NULL DEFAULT 0,
            needs_payment  INTEGER NOT NULL DEFAULT 1,
            sort           INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Админы и продавцы, добавленные из приложения (супер-админом).
    # Те, кто прописан в переменных окружения на хостинге, живут отдельно и
    # отсюда НЕ удаляются — это защита от потери доступа: даже если из
    # приложения снести всех, владелец из настроек сервера останется админом.
    # city пустой = админ над всеми городами; заполненный = продавец города.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            user_id  BIGINT PRIMARY KEY,
            city     TEXT,
            note     TEXT,
            added_by BIGINT,
            added_at TEXT
        )
    """)

    # Сами картинки (товары, чеки). Telegram хранит их по file_id, но качать оттуда
    # долго — два запроса на каждое фото. Скачиваем ОДИН раз и держим тут, чтобы
    # перезапуск сервера не заставлял качать всё заново.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS photo_blobs (
            file_id      TEXT PRIMARY KEY,
            content_type TEXT NOT NULL,
            data         {BLOB_COL} NOT NULL,
            size         INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT
        )
    """)

    conn.commit()
    conn.close()
    _ensure_product_columns()   # доклеит новые колонки на старой базе (миграция)
    _ensure_user_columns()      # coins / referred_by у пользователей
    _ensure_order_columns()     # coins_used / доставка у заказов
    seed_locations()            # добавит стартовые точки, если таблица пустая
    seed_delivery()             # дефолтные способы получения для Минск/Туров


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
    # photo_thumb — file_id уменьшенной копии для сетки каталога (см. _pick_photo_sizes)
    for c in ("brand", "flavor", "strength", "volume", "photo_thumb"):
        if c not in cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {c} TEXT")
    conn.commit()
    conn.close()


def _ensure_user_columns():
    """Добавляет колонки бонусов пользователю, если их ещё нет."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "users")
    if "coins" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0")
    if "referred_by" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN referred_by BIGINT")
    if "wheel_spins" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN wheel_spins INTEGER DEFAULT 0")
    if "wheel_progress" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN wheel_progress INTEGER DEFAULT 0")
    if "ref_activated" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN ref_activated INTEGER DEFAULT 0")
    if "ref_earned" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN ref_earned INTEGER DEFAULT 0")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT")   # дата первого захода (для «новые юзеры»)
    conn.commit()
    conn.close()


def _ensure_order_columns():
    """coins_used + поля доставки у заказа."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "orders")
    if "coins_used" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN coins_used INTEGER DEFAULT 0")
    if "delivery_method" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_method TEXT")
    if "delivery_address" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_address TEXT")
    if "delivery_fee" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_fee REAL DEFAULT 0")
    if "payment_method" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
    if "comment" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN comment TEXT")
    if "phone" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN phone TEXT")
    if "reminded_at" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN reminded_at TEXT")   # для повторного напоминания продавцу
    conn.commit()
    conn.close()


# ---------- Розыгрыши ----------

def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def get_active_raffle():
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM raffles WHERE status = 'active' ORDER BY id DESC LIMIT 1"))
    row = cur.fetchone()
    conn.close()
    return row


def get_last_finished_raffle():
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM raffles WHERE status = 'finished' ORDER BY id DESC LIMIT 1"))
    row = cur.fetchone()
    conn.close()
    return row


def create_raffle(title="Розыгрыш месяца", prize1="Одноразка", prize2="Жидкость",
                  prize3_coins=500, threshold=25, days=30):
    now = datetime.datetime.now()
    ends = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    rid = _insert_id(
        cur,
        """INSERT INTO raffles (title, prize1, prize2, prize3_coins, threshold, starts_at, ends_at, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)""",
        (title, prize1, prize2, int(prize3_coins), float(threshold),
         now.strftime("%Y-%m-%d %H:%M"), ends, now.strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    conn.close()
    return rid


_RAFFLE_EDITABLE = {"title", "prize1", "prize2", "prize3_coins", "threshold", "ends_at"}


def update_raffle_field(raffle_id, field, value):
    if field not in _RAFFLE_EDITABLE:
        return False
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE raffles SET {field} = %s WHERE id = %s"), (value, raffle_id))
    conn.commit()
    conn.close()
    return True


def finish_raffle(raffle_id, winners):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE raffles SET status = 'finished', winners = %s WHERE id = %s"),
                (json.dumps(winners, ensure_ascii=False), raffle_id))
    conn.commit()
    conn.close()


def add_raffle_entry(raffle_id, user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("INSERT INTO raffle_entries (raffle_id, user_id) VALUES (%s, %s)"), (raffle_id, user_id))
    conn.commit()
    conn.close()


def is_entered(raffle_id, user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT 1 FROM raffle_entries WHERE raffle_id = %s AND user_id = %s LIMIT 1"), (raffle_id, user_id))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def count_entries(raffle_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM raffle_entries WHERE raffle_id = %s"), (raffle_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


def get_raffle_user_ids(raffle_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT user_id FROM raffle_entries WHERE raffle_id = %s"), (raffle_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def spent_since(user_id, since):
    """Сумма ВЫДАННЫХ заказов клиента с момента since (для порога участия)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE user_id = %s AND status = 'issued' AND created_at >= %s"),
                (user_id, since))
    s = cur.fetchone()["s"]
    conn.close()
    return float(s or 0)


def get_raffle_state(user_id):
    """Активный розыгрыш + участники/участвую/потрачено/прошлые победители — за ОДНО подключение."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM raffles WHERE status = 'active' ORDER BY id DESC LIMIT 1"))
    r = cur.fetchone()
    if not r:
        conn.close()
        return None
    cur.execute(_q("SELECT COUNT(*) AS c, COALESCE(MAX(CASE WHEN user_id = %s THEN 1 ELSE 0 END), 0) AS mine "
                   "FROM raffle_entries WHERE raffle_id = %s"), (user_id, r["id"]))
    e = cur.fetchone()
    cur.execute(_q("SELECT COALESCE(SUM(total), 0) AS s FROM orders WHERE user_id = %s AND status = 'issued' AND created_at >= %s"),
                (user_id, r["starts_at"]))
    spent = cur.fetchone()["s"]
    cur.execute(_q("SELECT winners FROM raffles WHERE status = 'finished' ORDER BY id DESC LIMIT 1"))
    lw = cur.fetchone()
    conn.close()
    return {"raffle": r, "participants": e["c"], "entered": bool(e["mine"]),
            "spent": float(spent or 0), "last_winners_raw": (lw["winners"] if lw else None)}


# ---------- Способы получения (доставка/самовывоз) ----------

def get_delivery_methods(city):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM delivery_methods WHERE city = %s ORDER BY sort, id"), (city,))
    rows = cur.fetchall()
    conn.close()
    return rows


def add_delivery_method(city, name, needs_address, address_label, pickup_address, fee, needs_payment, sort=0):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""INSERT INTO delivery_methods
        (city, name, needs_address, address_label, pickup_address, fee, needs_payment, sort)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""),
        (city, name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, int(sort)))
    conn.commit()
    conn.close()


def update_delivery_method(method_id, name, needs_address, address_label, pickup_address, fee, needs_payment):
    """Обновляет существующий способ получения (правка на месте)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""UPDATE delivery_methods
        SET name = %s, needs_address = %s, address_label = %s,
            pickup_address = %s, fee = %s, needs_payment = %s
        WHERE id = %s"""),
        (name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, method_id))
    conn.commit()
    conn.close()


def get_delivery_method(method_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM delivery_methods WHERE id = %s"), (method_id,))
    row = cur.fetchone()
    conn.close()
    return row


def delete_delivery_method(method_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM delivery_methods WHERE id = %s"), (method_id,))
    conn.commit()
    conn.close()


def seed_delivery():
    """Дефолтные способы получения — только если таблица пуста."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM delivery_methods")
    if cur.fetchone()["c"] != 0:
        conn.close()
        return
    conn.close()
    defaults = [
        ("Туров", "Самовывоз", 0, "", "Уточните адрес самовывоза в настройках", 0, 1, 0),
        ("Туров", "Доставка", 1, "Адрес", "", 0, 1, 1),
        ("Минск", "Самовывоз", 0, "", "Уточните адрес самовывоза в настройках", 0, 1, 0),
        ("Минск", "Доставка по метро", 1, "Станция метро", "", 2, 1, 1),
        ("Минск", "Доставка такси", 1, "Адрес", "", 0, 0, 2),
    ]
    for d in defaults:
        add_delivery_method(*d)


def set_order_coins_used(order_id, coins):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET coins_used = %s WHERE id = %s"), (int(coins), order_id))
    conn.commit()
    conn.close()


def set_order_delivery(order_id, method, address, fee, payment, comment="", phone=""):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""UPDATE orders SET delivery_method = %s, delivery_address = %s,
                      delivery_fee = %s, payment_method = %s, comment = %s, phone = %s WHERE id = %s"""),
                (method, address, float(fee or 0), payment, (comment or "").strip()[:500], (phone or "").strip()[:40], order_id))
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


def ensure_user_get_age(user_id):
    """Создаёт пользователя (если нет) и возвращает его 18+ — за одно подключение."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    cur.execute(_q("SELECT age_ok FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.commit()
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
        # ON CONFLICT (а не REPLACE) — чтобы не затирать coins/referred_by
        cur.execute(
            "INSERT INTO users (user_id, age_ok) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET age_ok = 1", (user_id,))
    conn.commit()
    conn.close()


# ---------- Пользователи: бонусы и рефералы ----------

def ensure_user(user_id):
    """Создаёт строку пользователя, если её ещё нет (с датой первого захода)."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    conn.commit()
    conn.close()


def get_user_row(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def add_coins(user_id, n):
    """Меняет баланс на n (может быть отрицательным), не опускаясь ниже нуля."""
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE users SET coins = {GREATEST}(0, COALESCE(coins, 0) + %s) WHERE user_id = %s"),
                (int(n), user_id))
    conn.commit()
    conn.close()


def get_coins(user_id):
    row = get_user_row(user_id)
    return row["coins"] if row and row["coins"] is not None else 0


def spend_coins(user_id, amount):
    """Атомарно списывает amount монет — только если хватает баланса.
    Возвращает True при успехе, False если монет мало (защита от гонки/двойного списания)."""
    amount = int(amount)
    if amount <= 0:
        return True
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s"), (user_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


# --- Рефералы 2.0: активация, проценты, заработок ---

REFERRAL_BONUS = 50                       # монет пригласившему за ПЕРВЫЙ заказ друга
REFERRAL_TIERS = [(15, 5), (10, 4), (5, 3), (0, 2)]   # (мин. активных, процент)


def ref_percent(active):
    for min_active, pct in REFERRAL_TIERS:
        if active >= min_active:
            return pct
    return 2


def get_bonus_stats(user_id):
    """Всё для вкладки Бонусы за ОДНО подключение: баланс, рефералы, заработок, список."""
    conn = connect()
    cur = conn.cursor()
    # создать пользователя при первом заходе
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    cur.execute(_q("SELECT coins, ref_earned FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    coins = (row["coins"] if row and row["coins"] else 0)
    ref_earned = (row["ref_earned"] if row and row["ref_earned"] else 0)
    cur.execute(_q("SELECT ref_activated FROM users WHERE referred_by = %s ORDER BY ref_activated DESC"), (user_id,))
    refs = cur.fetchall()
    conn.commit()
    conn.close()
    total = len(refs)
    active = sum(1 for r in refs if r["ref_activated"])
    return {"coins": coins, "ref_earned": ref_earned, "referrals": total, "active": active,
            "referrals_list": [{"active": bool(r["ref_activated"])} for r in refs]}


def count_active_referrals(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s AND ref_activated = 1"), (user_id,))
    c = cur.fetchone()["c"]
    conn.close()
    return c


def list_referrals(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT user_id, ref_activated FROM users WHERE referred_by = %s ORDER BY ref_activated DESC, user_id"), (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_ref_earned(user_id):
    row = get_user_row(user_id)
    return row["ref_earned"] if row and row["ref_earned"] else 0


def add_ref_earned(user_id, n):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET ref_earned = COALESCE(ref_earned, 0) + %s WHERE user_id = %s"), (int(n), user_id))
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
        add_coins(ref, pct_coins)
        earned += pct_coins
    if first:
        set_ref_activated(buyer_id)
        add_coins(ref, REFERRAL_BONUS)
        earned += REFERRAL_BONUS
    if earned > 0:
        add_ref_earned(ref, earned)
    return {"referrer": ref, "percent": percent, "pct_coins": pct_coins,
            "first": first, "bonus": REFERRAL_BONUS if first else 0, "earned": earned}


def set_ref_activated(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET ref_activated = 1 WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()


def unlink_referral(user_id):
    """Отвязывает реферала: referred_by → NULL, ref_activated → 0.
    Возвращает True, если связь была и снялась (можно снова привязать)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET referred_by = NULL, ref_activated = 0 "
                   "WHERE user_id = %s AND referred_by IS NOT NULL"), (user_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def clear_referrals_of(ref_id):
    """Отвязывает ВСЕХ рефералов пригласившего ref_id. Возвращает число отвязанных."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET referred_by = NULL, ref_activated = 0 WHERE referred_by = %s"), (ref_id,))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def list_users(search="", limit=300):
    """Список пользователей для админа. search — подстрока id ИЛИ @username (из заказов).
    Возвращает (список, всего_в_базе)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users")
    total = cur.fetchone()["c"]
    base = ("SELECT user_id, COALESCE(age_ok,0) AS age_ok, COALESCE(coins,0) AS coins, "
            "referred_by, COALESCE(wheel_spins,0) AS wheel_spins, COALESCE(ref_earned,0) AS ref_earned FROM users ")
    search = (search or "").strip()
    if search:
        # ищем и по id, и по имени (через заказы) — сначала находим id, потом полные данные
        like = f"%{search.lower()}%"
        cur.execute(_q(
            "SELECT DISTINCT u.user_id AS user_id FROM users u "
            "LEFT JOIN orders o ON o.user_id = u.user_id "
            "WHERE CAST(u.user_id AS TEXT) LIKE %s OR LOWER(COALESCE(o.username,'')) LIKE %s "
            "ORDER BY u.user_id DESC LIMIT %s"), (f"%{search}%", like, limit))
        match_ids = [r["user_id"] for r in cur.fetchall()]
        if not match_ids:
            conn.close()
            return [], total
        marks0 = ",".join(["%s"] * len(match_ids))
        cur.execute(_q(base + f"WHERE user_id IN ({marks0}) ORDER BY user_id DESC"), tuple(match_ids))
    else:
        cur.execute(_q(base + "ORDER BY user_id DESC LIMIT %s"), (limit,))
    users = cur.fetchall()
    ids = [u["user_id"] for u in users]
    orders_by, names, refcount, last_order = {}, {}, {}, {}
    if ids:
        marks = ",".join(["%s"] * len(ids))
        cur.execute(_q(f"SELECT user_id, COUNT(*) AS cnt, COALESCE(SUM(total),0) AS spent "
                       f"FROM orders WHERE user_id IN ({marks}) AND status = 'issued' GROUP BY user_id"), tuple(ids))
        for r in cur.fetchall():
            orders_by[r["user_id"]] = (r["cnt"], r["spent"])
        cur.execute(_q(f"SELECT user_id, username, created_at FROM orders WHERE user_id IN ({marks}) ORDER BY id DESC"), tuple(ids))
        for r in cur.fetchall():
            if r["user_id"] not in names and r["username"]:
                names[r["user_id"]] = r["username"]        # самый свежий username
            if r["user_id"] not in last_order and r["created_at"]:
                last_order[r["user_id"]] = r["created_at"]  # дата последнего заказа
        cur.execute(_q(f"SELECT referred_by AS ref, COUNT(*) AS c FROM users WHERE referred_by IN ({marks}) GROUP BY referred_by"), tuple(ids))
        for r in cur.fetchall():
            refcount[r["ref"]] = r["c"]
    conn.close()
    out = []
    for u in users:
        cnt, spent = orders_by.get(u["user_id"], (0, 0))
        out.append({
            "id": u["user_id"], "username": names.get(u["user_id"], ""),
            "coins": u["coins"], "age_ok": bool(u["age_ok"]),
            "wheel_spins": u["wheel_spins"], "ref_earned": u["ref_earned"],
            "referred_by": u["referred_by"], "referrals": refcount.get(u["user_id"], 0),
            "orders": cnt, "spent": round(spent or 0, 2),
            "last_order": last_order.get(u["user_id"], ""),
        })
    return out, total


def delete_user(user_id):
    """Полностью удаляет запись пользователя (монеты, 18+, прокруты, реф-связь).
    Заказы остаются в истории. Возвращает True, если пользователь был удалён."""
    conn = connect()
    cur = conn.cursor()
    # Отвязать тех, кого он приглашал (чтобы не осталось «висячих» ссылок на удалённого).
    cur.execute(_q("UPDATE users SET referred_by = NULL, ref_activated = 0 WHERE referred_by = %s"), (user_id,))
    cur.execute(_q("DELETE FROM users WHERE user_id = %s"), (user_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# --- Заявки на подтверждение супер-админом (чувствительные операции обычных админов) ---

def create_admin_request(requester_id, requester_name, action, payload, summary):
    """Создаёт заявку в статусе pending. Возвращает её id."""
    created = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    rid = _insert_id(cur,
        """INSERT INTO admin_requests (requester_id, requester_name, action, payload, summary, status, created_at)
           VALUES (%s, %s, %s, %s, %s, 'pending', %s)""",
        (requester_id, requester_name, action, json.dumps(payload), summary, created))
    conn.commit()
    conn.close()
    return rid


def get_admin_request(rid):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM admin_requests WHERE id = %s"), (rid,))
    row = cur.fetchone()
    conn.close()
    return row


def set_admin_request_status_if(rid, new_status, allowed):
    """Атомарно меняет статус заявки только из allowed (approve/reject ровно один раз)."""
    conn = connect()
    cur = conn.cursor()
    marks = ",".join(["%s"] * len(allowed))
    cur.execute(_q(f"UPDATE admin_requests SET status = %s WHERE id = %s AND status IN ({marks})"),
                (new_status, rid, *allowed))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def list_admin_requests(status="pending", limit=50):
    """Заявки по статусу (для экрана супер-админа), новые сверху."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM admin_requests WHERE status = %s ORDER BY id DESC LIMIT %s"), (status, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def execute_admin_request(action, payload):
    """Выполняет одобренную операцию. Возвращает dict-результат."""
    if action == "coins_adjust":
        t = int(payload["user_id"]); add_coins(t, int(payload["delta"]))
        return {"coins": get_coins(t)}
    if action == "grant":
        t = int(payload["user_id"]); ensure_user(t)
        if int(payload.get("coins", 0)): add_coins(t, int(payload["coins"]))
        if int(payload.get("spins", 0)): add_spins(t, int(payload["spins"]))
        return {"coins": get_coins(t), "spins": get_wheel(t)["spins"]}
    if action == "user_delete":
        return {"deleted": delete_user(int(payload["user_id"]))}
    if action == "referral_unlink":
        return {"unlinked": unlink_referral(int(payload["user_id"]))}
    if action == "referral_clear":
        return {"count": clear_referrals_of(int(payload["requester_id"]))}
    if action == "wheel_grant_self":
        t = int(payload["user_id"]); add_spins(t, int(payload.get("spins", 3)))
        return {"spins": get_wheel(t)["spins"]}
    return {}


WHEEL_STEP = 5   # сколько купленных товаров нужно на один прокрут колеса


def get_wheel(user_id):
    row = get_user_row(user_id)
    return {
        "spins": (row["wheel_spins"] if row and row["wheel_spins"] else 0),
        "progress": (row["wheel_progress"] if row and row["wheel_progress"] else 0),
        "step": WHEEL_STEP,
    }


def add_wheel_progress(user_id, n):
    """Копит прогресс за купленные товары; каждые WHEEL_STEP превращает в прокрут."""
    ensure_user(user_id)
    row = get_user_row(user_id)
    prog = (row["wheel_progress"] or 0) + int(n)
    spins = (row["wheel_spins"] or 0)
    while prog >= WHEEL_STEP:
        prog -= WHEEL_STEP
        spins += 1
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET wheel_progress = %s, wheel_spins = %s WHERE user_id = %s"),
                (prog, spins, user_id))
    conn.commit()
    conn.close()


def add_spins(user_id, n):
    """Меняет число прокрутов на n (может быть отрицательным), не ниже нуля."""
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE users SET wheel_spins = {GREATEST}(0, COALESCE(wheel_spins, 0) + %s) WHERE user_id = %s"),
                (int(n), user_id))
    conn.commit()
    conn.close()


def use_spin(user_id):
    """Списывает один прокрут. True — если был доступен."""
    ensure_user(user_id)
    row = get_user_row(user_id)
    if (row["wheel_spins"] or 0) <= 0:
        return False
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET wheel_spins = wheel_spins - 1 WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()
    return True


def do_wheel_spin(user_id, prize_coins):
    """Атомарно за ОДИН запрос: если есть прокрут — списать 1 и начислить приз.
    Возвращает (coins, spins) или None, если прокрутов нет."""
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""UPDATE users SET wheel_spins = wheel_spins - 1, coins = COALESCE(coins,0) + %s
                       WHERE user_id = %s AND COALESCE(wheel_spins,0) > 0
                       RETURNING COALESCE(coins,0) AS coins, wheel_spins""", (prize_coins, user_id))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return (row["coins"], row["wheel_spins"]) if row else None
    # SQLite (локально, быстро) — проверка + обновление в одном подключении
    cur.execute("SELECT COALESCE(wheel_spins,0) AS s, COALESCE(coins,0) AS c FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    if not r or r["s"] <= 0:
        conn.close()
        return None
    new_coins = r["c"] + prize_coins
    cur.execute("UPDATE users SET wheel_spins = wheel_spins - 1, coins = ? WHERE user_id = ?", (new_coins, user_id))
    conn.commit()
    conn.close()
    return (new_coins, r["s"] - 1)


def do_slot_spin(user_id, cost, prize_coins):
    """Атомарно за ОДИН запрос: если хватает монет — списать cost и начислить приз.
    Возвращает новый баланс или None, если монет мало."""
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""UPDATE users SET coins = COALESCE(coins,0) - %s + %s
                       WHERE user_id = %s AND COALESCE(coins,0) >= %s
                       RETURNING COALESCE(coins,0) AS coins""", (cost, prize_coins, user_id, cost))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return row["coins"] if row else None
    cur.execute("SELECT COALESCE(coins,0) AS c FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    if not r or r["c"] < cost:
        conn.close()
        return None
    new_coins = r["c"] - cost + prize_coins
    cur.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_coins, user_id))
    conn.commit()
    conn.close()
    return new_coins


def set_referrer_once(user_id, ref_id):
    """Записывает пригласившего, если он ещё не задан и это не сам пользователь.
    Возвращает True, если запись применилась (тогда наградой занимается вызывающий)."""
    if not ref_id or ref_id == user_id:
        return False
    ensure_user(user_id)
    row = get_user_row(user_id)
    if row and row["referred_by"]:
        return False                      # реферер уже есть — второй раз не начисляем
    if not get_user_row(ref_id):
        return False                      # пригласивший должен существовать
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET referred_by = %s WHERE user_id = %s"), (ref_id, user_id))
    conn.commit()
    conn.close()
    return True


# ---------- Настройки магазина ----------

def get_setting(key, default=None):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT value FROM settings WHERE key = %s"), (key,))
    row = cur.fetchone()
    conn.close()
    if not row or row["value"] is None:
        return default
    return row["value"]


def inc_stat(key, delta=1):
    """Увеличивает счётчик игры (прокруты/ставки/выплаты)."""
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""INSERT INTO game_stats (key, n) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET n = game_stats.n + EXCLUDED.n""", (key, int(delta)))
    else:
        cur.execute("INSERT INTO game_stats (key, n) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET n = n + ?",
                    (key, int(delta), int(delta)))
    conn.commit()
    conn.close()


def get_game_stats():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT key, n FROM game_stats")
    rows = cur.fetchall()
    conn.close()
    return {r["key"]: r["n"] for r in rows}


def reset_statistics(orders=True, games=True):
    """Сброс тестовой статистики: удаляет заказы и/или обнуляет игровые счётчики.
    Возвращает {orders: сколько_удалено}."""
    conn = connect()
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
    now = datetime.datetime.now()
    cutoff = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00") if days else None
    conn = connect()
    cur = conn.cursor()

    # Выручка/заказы (выданные) за период
    if cutoff:
        cur.execute(_q("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND created_at >= %s"), (cutoff,))
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
        cur.execute(_q("SELECT status AS st, COUNT(*) AS c FROM orders WHERE created_at >= %s GROUP BY status"), (cutoff,))
    else:
        cur.execute("SELECT status AS st, COUNT(*) AS c FROM orders GROUP BY status")
    by_status = {r["st"]: r["c"] for r in cur.fetchall()}

    # Выручка по точкам (выданные, период)
    if cutoff:
        cur.execute(_q("SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND created_at >= %s GROUP BY city ORDER BY s DESC"), (cutoff,))
    else:
        cur.execute("SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' GROUP BY city ORDER BY s DESC")
    revenue_by_city = [{"city": r["ct"], "total": round(float(r["s"] or 0), 2)} for r in cur.fetchall()]

    # По дням (для графика): последние N дней, пробелы = 0
    n_days = days if days else 30
    start = now - datetime.timedelta(days=n_days - 1)
    start_str = start.strftime("%Y-%m-%d 00:00")
    cur.execute(_q("SELECT substr(created_at,1,10) AS d, COUNT(*) AS c, COALESCE(SUM(total),0) AS s "
                   "FROM orders WHERE status='issued' AND created_at >= %s GROUP BY substr(created_at,1,10)"), (start_str,))
    day_map = {r["d"]: (r["c"], float(r["s"] or 0)) for r in cur.fetchall()}
    daily = []
    for i in range(n_days):
        d = (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        c, s = day_map.get(d, (0, 0.0))
        daily.append({"date": d, "orders": c, "revenue": round(s, 2)})

    # Топ товаров (парсим JSON только выданных за период)
    if cutoff:
        cur.execute(_q("SELECT items FROM orders WHERE status='issued' AND created_at >= %s"), (cutoff,))
    else:
        cur.execute("SELECT items FROM orders WHERE status='issued'")
    qty_by_name, rev_by_name = {}, {}
    for r in cur.fetchall():
        try:
            for it in json.loads(r["items"]):
                nm = it.get("name", "?")
                q = int(it.get("qty", 0))
                qty_by_name[nm] = qty_by_name.get(nm, 0) + q
                rev_by_name[nm] = rev_by_name.get(nm, 0) + q * float(it.get("price", 0) or 0)
        except (TypeError, ValueError):
            pass
    top = [{"name": n, "qty": q, "revenue": round(rev_by_name.get(n, 0), 2)}
           for n, q in sorted(qty_by_name.items(), key=lambda x: -x[1])[:8]]

    # Пользователи
    cur.execute("SELECT COUNT(*) AS c FROM users")
    users_total = cur.fetchone()["c"]
    if cutoff:
        cur.execute(_q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s"), (cutoff,))
        new_users = cur.fetchone()["c"]
        cur.execute(_q("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued' AND created_at >= %s"), (cutoff,))
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
        cur.execute(_q("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders "
                       "WHERE status='issued' AND created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        r = cur.fetchone()
        p_cnt = r["c"]
        p_rev = float(r["s"] or 0)
        cur.execute(_q("SELECT COUNT(DISTINCT user_id) AS c FROM orders "
                       "WHERE status='issued' AND created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        p_buyers = cur.fetchone()["c"]
        cur.execute(_q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        p_new = cur.fetchone()["c"]
        prev = {"revenue": round(p_rev, 2), "orders": p_cnt,
                "avg_check": round(p_rev / p_cnt, 2) if p_cnt else 0,
                "buyers": p_buyers, "new_users": p_new}

    conn.close()
    return {
        "period_days": days, "prev": prev,
        "revenue": round(revenue, 2), "orders": issued_count, "avg_check": round(avg_check, 2),
        "inwork_total": round(inwork_total, 2), "inwork_count": inwork_count,
        "by_status": by_status, "revenue_by_city": revenue_by_city, "daily": daily, "top": top,
        "users_total": users_total, "new_users": new_users, "buyers_period": buyers_period,
        "repeat_buyers": repeat_buyers, "total_buyers": total_buyers,
        "coins_circulation": coins_circulation,
    }


def set_setting(key, value):
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            """INSERT INTO settings (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (key, str(value)),
        )
    else:
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


# ---------- Админы и продавцы (управляются из приложения) ----------

def list_staff():
    """Все, кого супер-админ добавил через приложение."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM staff ORDER BY city, user_id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_staff(user_id, city="", note="", added_by=None):
    """Добавляет админа/продавца. Повторный вызов обновляет город и подпись."""
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            """INSERT INTO staff (user_id, city, note, added_by, added_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET city = EXCLUDED.city, note = EXCLUDED.note""",
            (user_id, city or "", note or "", added_by, _now_str()),
        )
    else:
        cur.execute("INSERT OR REPLACE INTO staff (user_id, city, note, added_by, added_at) "
                    "VALUES (?, ?, ?, ?, ?)", (user_id, city or "", note or "", added_by, _now_str()))
    conn.commit()
    conn.close()


def remove_staff(user_id):
    """Убирает из приложения. Если этот id прописан в настройках сервера —
    доступ у человека останется, поэтому список показывает источник записи."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM staff WHERE user_id = %s"), (user_id,))
    conn.commit()
    conn.close()


def staff_ids_by_city():
    """{'': {id,...}, 'minsk': {id,...}} — для проверки прав и рассылки заказов."""
    out = {}
    for row in list_staff():
        out.setdefault(row["city"] or "", set()).add(int(row["user_id"]))
    return out


# ---------- Картинки ----------

def get_photo_blob(file_id):
    """Картинка из базы: (данные, content_type). None — если её там ещё нет."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT data, content_type FROM photo_blobs WHERE file_id = %s"), (file_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    # Postgres отдаёт BYTEA как memoryview — Flask нужен обычный bytes.
    return bytes(row["data"]), row["content_type"]


def save_photo_blob(file_id, content_type, data):
    """Кладёт скачанную картинку в базу, чтобы больше не ходить за ней в Telegram."""
    payload = psycopg2.Binary(data) if USE_PG else sqlite3.Binary(data)
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            """INSERT INTO photo_blobs (file_id, content_type, data, size, created_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (file_id) DO NOTHING""",
            (file_id, content_type, payload, len(data), _now_str()),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO photo_blobs (file_id, content_type, data, size, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, content_type, payload, len(data), _now_str()),
        )
    conn.commit()
    conn.close()


def is_product_photo(file_id):
    """Это картинка товара (а не чек об оплате)?

    Фото товаров стоит хранить у себя: их немного и их смотрят все покупатели.
    Чеки — наоборот, по штуке на заказ и смотрит их один продавец один раз,
    поэтому в базу они не попадают, чтобы не забить бесплатное место."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT 1 AS x FROM products WHERE photo = %s OR photo_thumb = %s LIMIT 1"),
                (file_id, file_id))
    found = cur.fetchone() is not None
    conn.close()
    return found


def photo_blob_stats():
    """Сколько картинок лежит в базе и сколько места занимают (для админ-статистики)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM photo_blobs")
    row = cur.fetchone()
    conn.close()
    return {"count": int(row["n"]), "bytes": int(row["bytes"])}


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
_EDITABLE = {"name", "price", "stock", "is_hit", "description", "photo", "photo_thumb",
             "brand", "flavor", "strength", "volume", "category", "city"}


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


def get_checkout_data(user_id, product_ids, method_id):
    """Всё, что нужно для оформления заказа, — за ОДНО подключение и 4 запроса.

    Раньше сервер дёргал базу отдельно на каждый товар (get_product + get_variants),
    отдельно на 18+, монеты и способ доставки — на Neon это ~8 сетевых поездок,
    и кнопка «Оформить» заметно висла. Здесь всё берётся разом.

    Возвращает: {age_ok, coins, products: {id: row}, variants: {id: {flavor: stock}}, method}
    """
    ids = [int(i) for i in dict.fromkeys(product_ids)]     # уникальные, порядок сохраняем
    conn = connect()
    cur = conn.cursor()

    cur.execute(_q("SELECT age_ok, COALESCE(coins, 0) AS coins FROM users WHERE user_id = %s"),
                (user_id,))
    u = cur.fetchone()
    age_ok = bool(u and u["age_ok"] == 1)
    coins = int(u["coins"]) if u else 0

    products, variants = {}, {}
    if ids:
        marks = ",".join(["%s"] * len(ids))
        cur.execute(_q(f"SELECT * FROM products WHERE id IN ({marks})"), tuple(ids))
        products = {int(r["id"]): dict(r) for r in cur.fetchall()}
        cur.execute(_q(f"SELECT * FROM product_variants WHERE product_id IN ({marks})"), tuple(ids))
        for v in cur.fetchall():
            variants.setdefault(int(v["product_id"]), {})[v["flavor"]] = v["stock"]

    method = None
    if method_id is not None:
        cur.execute(_q("SELECT * FROM delivery_methods WHERE id = %s"), (method_id,))
        row = cur.fetchone()
        method = dict(row) if row else None

    conn.close()
    return {"age_ok": age_ok, "coins": coins, "products": products,
            "variants": variants, "method": method}


def place_order(user_id, username, city, items, subtotal, fee, coin_value, coins_to_spend,
                method_name, address, payment, comment, phone, status):
    """Создаёт заказ целиком за ОДНУ транзакцию: списывает монеты, вставляет заказ
    со всеми полями доставки, снимает остатки со склада (и по вкусам).

    Раньше это были create_order + set_order_delivery + set_order_coins_used +
    change_stock на каждую позицию + set_order_status — каждая со своим commit'ом.
    Теперь один commit: меньше поездок к базе и заказ не может «застрять» наполовину.

    Возвращает (order_id, coins_used, total).
    """
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    try:
        # 1. Монеты — списываем условно (только если хватает баланса), это же и защита от гонки.
        coins_used = 0
        spend = int(coins_to_spend or 0)
        if spend > 0:
            cur.execute(_q("""UPDATE users SET coins = COALESCE(coins, 0) - %s
                              WHERE user_id = %s AND COALESCE(coins, 0) >= %s"""),
                        (spend, user_id, spend))
            if cur.rowcount > 0:
                coins_used = spend

        discount = round(coins_used * coin_value, 2)
        total = round(subtotal - discount + fee, 2)

        # 2. Сам заказ — сразу со всеми полями (без последующих UPDATE).
        order_id = _insert_id(
            cur,
            """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status,
                                   created_at, coins_used, delivery_method, delivery_address,
                                   delivery_fee, payment_method, comment, phone)
               VALUES (%s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, username, city, json.dumps(items, ensure_ascii=False), total, status,
             created_at, coins_used, method_name, address, float(fee or 0), payment,
             (comment or "").strip()[:500], (phone or "").strip()[:40]),
        )

        # 3. Склад: у товаров со вкусами списываем вариант, у обычных — сам товар.
        touched_variants = set()
        for it in items:
            if it.get("flavor"):
                cur.execute(_q(f"UPDATE product_variants SET stock = {GREATEST}(0, stock - %s) "
                               "WHERE product_id = %s AND flavor = %s"),
                            (it["qty"], it["id"], it["flavor"]))
                touched_variants.add(it["id"])
            else:
                cur.execute(_q(f"UPDATE products SET stock = {GREATEST}(0, stock - %s) WHERE id = %s"),
                            (it["qty"], it["id"]))
        # общий остаток товара-модели = сумма остатков вкусов
        for pid in touched_variants:
            cur.execute(_q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (pid, pid))

        conn.commit()
    except Exception:
        conn.rollback()      # ничего не применилось: ни монеты, ни склад, ни заказ
        conn.close()
        raise
    conn.close()
    return order_id, coins_used, total


def get_order(order_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE id = %s"), (order_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_orders(limit=200):
    """Все заказы, новые сверху — для админ-панели."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders ORDER BY id DESC LIMIT %s"), (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_orders_by_user(user_id, limit=50):
    """Заказы конкретного клиента, новые сверху — для истории в профиле."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT %s"), (user_id, limit))
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
            change_variant_stock(it["id"], it["flavor"], qty)
            recalc_product_stock(it["id"])
        else:
            change_stock(it["id"], qty)


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
        add_coins(order["user_id"], order["coins_used"])
    return order


def stale_new_orders(hours=24):
    """Карточные заказы, застрявшие в 'new' (чек не загружен) дольше `hours` — на авто-отмену."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE status = 'new' AND created_at <= %s"), (cutoff,))
    rows = cur.fetchall()
    conn.close()
    return rows


def touch_order_reminded(order_id):
    """Отмечает, что по заказу только что отправлено уведомление/напоминание продавцу."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET reminded_at = %s WHERE id = %s"), (now, order_id))
    conn.commit()
    conn.close()


def orders_needing_reminder(minutes=10):
    """Заказы, ждущие ОДОБРЕНИЯ продавца (status='paid'), по которым напоминание
    не отправлялось дольше `minutes`. Напоминаем до одобрения (потом продавец сам ведёт заказ)."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE status = 'paid' "
                   "AND (reminded_at IS NULL OR reminded_at <= %s) ORDER BY id"), (cutoff,))
    rows = cur.fetchall()
    conn.close()
    return rows


def set_order_status(order_id, status):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET status = %s WHERE id = %s"), (status, order_id))
    conn.commit()
    conn.close()


def set_order_status_if(order_id, new_status, allowed):
    """Атомарно меняет статус ТОЛЬКО если текущий статус ∈ allowed.
    Возвращает True, если переход применился (тогда вызывающий делает побочные эффекты
    — начисление/возврат — РОВНО один раз; защита от двойного клика и гонки)."""
    conn = connect()
    cur = conn.cursor()
    marks = ",".join(["%s"] * len(allowed))
    cur.execute(_q(f"UPDATE orders SET status = %s WHERE id = %s AND status IN ({marks})"),
                (new_status, order_id, *allowed))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


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
