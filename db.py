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

    # Движение склада: приход и списание с причиной и автором.
    # Раньше остаток менялся только продажей и ручной правкой числа — разбитое,
    # украденное и просроченное учесть было негде, и склад тихо расходился с
    # реальностью. Автор важен отдельно: доступ теперь есть у продавцов.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS stock_moves (
            id         {ID_COL},
            product_id INTEGER NOT NULL,
            flavor     TEXT,
            delta      INTEGER NOT NULL,
            reason     TEXT    NOT NULL,
            cost       REAL    NOT NULL DEFAULT 0,
            note       TEXT,
            admin_id   BIGINT,
            created_at TEXT
        )
    """)

    # Промокоды. Владелец постит в свою группу вручную, и без кодов нельзя
    # понять, что из этого сработало: код превращает пост в измеримую кампанию.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS promos (
            id         {ID_COL},
            code       TEXT    NOT NULL,
            kind       TEXT    NOT NULL DEFAULT 'percent',   -- percent | fixed
            value      REAL    NOT NULL DEFAULT 0,
            min_total  REAL    NOT NULL DEFAULT 0,
            uses_left  INTEGER,                              -- NULL = без ограничения
            once_per_user INTEGER NOT NULL DEFAULT 1,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_promos_code ON promos (code)")

    # Точки самовывоза. Их несколько на город, и покупатель выбирает нужную при
    # заказе. Раньше у способа получения был ОДИН адрес текстом — на город с
    # несколькими точками это не годилось.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS pickup_points (
            id      {ID_COL},
            city    TEXT    NOT NULL,
            address TEXT    NOT NULL,
            note    TEXT,
            sort    INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Кто просил сообщить, когда товар снова появится.
    # Пара (товар, покупатель) уникальна: повторное нажатие ничего не портит.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id         {ID_COL},
            product_id INTEGER NOT NULL,
            user_id    BIGINT  NOT NULL,
            created_at TEXT
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_alerts ON stock_alerts (product_id, user_id)")

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

    # Журнал действий продавцов. Остаток менялся с записью в журнал движений, а
    # цена, удаление товара и правка настроек не оставляли следа вовсе: владелец
    # не мог ответить, почему вчера продавали по 12 и кто убрал модель.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS admin_log (
            id         {ID_COL},
            admin_id   BIGINT,
            admin_name TEXT,
            action     TEXT,
            details    TEXT,
            created_at TEXT
        )
    """)

    # Движение монет: кому, сколько и за что. Баланс отвечает «сколько сейчас»,
    # а владельцу нужно «сколько раздали за месяц и откуда» — по остаткам это не
    # посчитать, часть монет уже потрачена.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS coin_log (
            id         {ID_COL},
            user_id    BIGINT,
            delta      INTEGER,
            reason     TEXT,
            created_at TEXT
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

    # Категории товара. Раньше они были прошиты в коде, и добавить «Расходники»
    # можно было только правкой файлов и деплоем — то есть через меня.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            code  TEXT PRIMARY KEY,
            name  TEXT NOT NULL,
            emoji TEXT,
            sort  INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Модели («Ассортимент») — что магазин вообще продаёт: бренд, название,
    # характеристики, вкусы и фото. Товар на точке — это НАЛИЧИЕ модели: цена,
    # закупка, остаток. Раньше модели не было, и одна и та же подсистема на трёх
    # точках описывалась трижды — с тройным шансом описать её по-разному.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS models (
            id          {ID_COL},
            category    TEXT NOT NULL,
            brand       TEXT,
            name        TEXT NOT NULL,
            description TEXT,
            specs       TEXT,
            flavors     TEXT,
            photo       TEXT,
            photo_thumb TEXT,
            created_at  TEXT
        )
    """)

    # Характеристики категории. У картриджа сопротивление и совместимость,
    # у пода мощность и аккумулятор — общие «бренд и вкус» на всё это не годятся.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS category_specs (
            id       {ID_COL},
            category TEXT    NOT NULL,
            key      TEXT    NOT NULL,
            label    TEXT    NOT NULL,
            unit     TEXT,
            kind     TEXT    NOT NULL DEFAULT 'text',
            options  TEXT,
            sort     INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Отзывы. Пишет только тот, кто товар покупал, — иначе это не отзыв, а
    # анонимная запись в интернете, и цена ей соответствующая.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS reviews (
            id         {ID_COL},
            product_id INTEGER NOT NULL,
            user_id    BIGINT  NOT NULL,
            username   TEXT,
            rating     INTEGER NOT NULL,
            text       TEXT,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TEXT
        )
    """)

    # Дополнительные фото товара (главное лежит в products.photo). Одна картинка
    # не показывает ни размер, ни комплект, ни экран — покупателю приходится
    # верить на слово, а продавцу отвечать на одни и те же вопросы в чате.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS product_photos (
            id         {ID_COL},
            product_id INTEGER NOT NULL,
            file_id    TEXT    NOT NULL,
            thumb_id   TEXT,
            sort       INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    _ensure_product_columns()   # доклеит новые колонки на старой базе (миграция)
    _ensure_user_columns()      # coins / referred_by у пользователей
    _ensure_order_columns()     # coins_used / доставка у заказов
    seed_categories()           # стартовые категории, если таблица пустая
    _ensure_category_columns()  # has_flavors у категорий
    _ensure_photo_columns()     # галерея у модели, а не у товара
    seed_category_specs()       # характеристики категорий, если их ещё нет
    models_seeded_from_products()   # разово собирает ассортимент из прежних товаров
    _ensure_review_columns()    # отзыв принадлежит модели — ПОСЛЕ того, как модели собраны
    _ensure_raffle_columns()    # finished_at: когда розыгрыш реально подвели
    _ensure_raffle_uniques()    # один билет на человека, один активный розыгрыш
    _shift_history_to_shop_time()   # разово: старые записи были по UTC, см. ниже
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
    # specs — характеристики, свои у каждой категории (JSON). Старые колонки
    # strength/volume остаются на месте: по ним живут прежние товары, бот и
    # подписи в каталоге, и переносить их значило бы чинить то, что не сломано.
    for c in ("brand", "flavor", "strength", "volume", "photo_thumb", "specs"):
        if c not in cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {c} TEXT")
    # Ссылка на модель из «Ассортимента». NULL — товар заведён до её появления:
    # он продолжает работать сам по себе, просто не обновляется вместе с моделью.
    if "model_id" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN model_id INTEGER")
    # Закупочная цена. Без неё статистика показывает выручку, но никогда —
    # прибыль, и решения о закупке принимаются вслепую.
    if "cost" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN cost REAL DEFAULT 0")
    # Снят с витрины. Удалить было единственным способом убрать товар из
    # продажи — а удаление уносит и остаток, и историю. Теперь «больше не
    # продаём» и «этого не было» — разные действия.
    if "hidden" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN hidden INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def _ensure_photo_columns():
    """Галерея переехала с товара на модель: коробка одна и та же на всех точках,
    а фото у каждого наличия отдельно — это те же снимки в трёх экземплярах."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "product_photos")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE product_photos ADD COLUMN model_id INTEGER")
        # Прежние фото товаров переносим на их модели.
        cur.execute("UPDATE product_photos SET model_id = ("
                    "SELECT model_id FROM products WHERE products.id = product_photos.product_id)")
    conn.commit()
    conn.close()


def _ensure_category_columns():
    """has_flavors — заводится ли товар этой категории списком вкусов.

    Раньше это решал жёсткий список кодов в коде: вкусы были у одноразок и
    жидкостей, и новая категория такой возможности получить не могла."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "categories")
    if "has_flavors" not in cols:
        cur.execute("ALTER TABLE categories ADD COLUMN has_flavors INTEGER DEFAULT 0")
        cur.execute(_q("UPDATE categories SET has_flavors = 1 WHERE code IN (%s, %s)"),
                    ("disposable", "liquid"))
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
    if "no_reminders" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN no_reminders INTEGER DEFAULT 0")  # отписался от напоминаний
    if "reminded_at" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN reminded_at TEXT")  # когда напоминали в последний раз
    # Имя человека. Раньше его нигде не хранили: в списке пользователей оно
    # бралось из заказов, поэтому все, кто ещё не покупал, выглядели голым id —
    # написать им было некому и не от кого. У многих в Telegram нет @имени
    # вовсе, поэтому держим и имя из профиля.
    if "username" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "first_name" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    if "phone" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")        # телефон из настроек покупателя
    if "pickup_point_id" not in cols:
        # Своя точка самовывоза «по умолчанию»: покупатель выбирает её один раз
        # в профиле, а в заказе может поменять.
        cur.execute("ALTER TABLE users ADD COLUMN pickup_point_id INTEGER")
    # Тем, кто уже покупал, имя достаём из их заказов: оно там лежало всё это
    # время, просто в списке пользователей его никто не показывал.
    cur.execute("""UPDATE users SET username =
                     (SELECT o.username FROM orders o
                       WHERE o.user_id = users.user_id AND o.username <> ''
                       ORDER BY o.id DESC LIMIT 1)
                   WHERE username IS NULL OR username = ''""")
    conn.commit()
    conn.close()
    _migrate_wheel_progress_to_money()




def remember_user_name(user_id, username="", first_name=""):
    """Запоминает, как зовут человека. Имя в Telegram меняется, поэтому пишем
    свежее при каждом заходе, но не затираем его пустым: у части людей @имени
    нет вовсе, и терять из-за этого имя из профиля незачем."""
    username = (username or "").lstrip("@").strip()[:64]
    first_name = (first_name or "").strip()[:64]
    if not username and not first_name:
        return
    conn = connect()
    cur = conn.cursor()
    if username:
        cur.execute(_q("UPDATE users SET username = %s WHERE user_id = %s"), (username, user_id))
    if first_name:
        cur.execute(_q("UPDATE users SET first_name = %s WHERE user_id = %s"), (first_name, user_id))
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
    if "promo_code" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")    # каким кодом воспользовались
    if "promo_discount" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN promo_discount REAL DEFAULT 0")
    if "client_token" not in cols:
        # Ключ попытки оформления — против повторного заказа при потерянном ответе.
        cur.execute("ALTER TABLE orders ADD COLUMN client_token TEXT")
    # Последнее слово о дублях — за базой, а не за проверкой в коде: два запроса
    # уходят одновременно, и оба успевают не найти прежний заказ. Ключ частичный:
    # у всех прежних заказов client_token пуст, и мешать им он не должен.
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_client_token_uniq "
                "ON orders (user_id, client_token) "
                "WHERE client_token IS NOT NULL AND client_token <> ''")
    conn.commit()
    conn.close()
    _ensure_delivery_columns()


def _ensure_delivery_columns():
    """needs_point — покупатель сам выбирает точку самовывоза из списка админа."""
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "delivery_methods")
    if "needs_point" not in cols:
        cur.execute("ALTER TABLE delivery_methods ADD COLUMN needs_point INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def _ensure_review_columns():
    """Ответ продавца на отзыв. Спокойный ответ на тройку убеждает нового
    покупателя сильнее, чем её отсутствие.

    Плюс model_id: оценивают модель, а не наличие её на конкретной точке.
    Пока отзыв висел на товаре, один и тот же Elf Bar в Минске и Турове копил
    оценки раздельно — покупатель второй точки видел «отзывов пока нет» у
    товара, у которого их дюжина. product_id остаётся: он говорит, где именно
    человек покупал, и по нему же работают старые отзывы товаров без модели.
    """
    conn = connect()
    cur = conn.cursor()
    cols = _table_columns(cur, "reviews")
    if "reply" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN reply TEXT")
    if "replied_at" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN replied_at TEXT")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE reviews ADD COLUMN model_id INTEGER")
    # Проставляем модель уже написанным отзывам — иначе они останутся видны
    # только на той точке, где были оставлены.
    cur.execute("""UPDATE reviews SET model_id =
                     (SELECT p.model_id FROM products p WHERE p.id = reviews.product_id)
                   WHERE model_id IS NULL""")
    conn.commit()
    conn.close()


# ---------- Розыгрыши ----------

# Магазин живёт по минскому времени, а сервер Render — по UTC, и часового пояса
# в его настройках нет. Пока время брали у сервера, заказ, сделанный в час ночи,
# записывался вчерашним и на три часа раньше: покупатель видел в приложении не
# то время, когда заказывал, а сутки магазина начинались в три часа ночи вместо
# полуночи — «Сегодня» у продавца до трёх показывал вчерашнюю выручку.
#
# SUMMARY_TZ_OFFSET оставлен как запасное имя: он уже мог быть выставлен в
# настройках сервиса, и молча поменять смысл этой настройки нельзя.
SHOP_TZ_OFFSET = int(os.environ.get("SHOP_TZ_OFFSET",
                                    os.environ.get("SUMMARY_TZ_OFFSET", "3")))


def shop_now():
    """Сейчас по времени магазина.

    Единственный источник «сейчас» во всей базе: время записи и границы суток
    обязаны считаться одинаково, иначе заказ попадает в один день, а ищут его в
    другом."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=SHOP_TZ_OFFSET)


def _now_str():
    return shop_now().strftime("%Y-%m-%d %H:%M")


_TZ_SHIFT_MARK = "history_shifted_to_shop_time"
# Как узнать колонку со временем: у всех таких имя кончается на _at или _time
# (created_at, reminded_at, starts_at, pickup_time). Правило, а не список: новая
# колонка попадёт под сдвиг сама, как и новая таблица — в резервную копию.
_TIME_COL = ("_at", "_time")


def _shift_history_to_shop_time():
    """Разово переводит записи, сделанные ДО перехода на время магазина.

    Прежде время брали у сервера, а он живёт по UTC: всё, что записано до
    перехода, отстаёт на SHOP_TZ_OFFSET часов. Дальше записи идут уже по
    минскому времени, и в базе оказались бы два разных времени сразу — заказ от
    вторника и заказ от среды в разных системах отсчёта. Сравнивать такое
    нельзя, поэтому историю приводим к одному времени.

    Правится вся история целиком: всё, что лежит в базе в момент первого запуска
    нового кода, записано старым. Отметка о выполнении — в самой базе, поэтому
    повторные запуски и перезапуски сервиса сдвиг не повторят: второй сдвиг
    испортил бы данные молча, и заметить это было бы уже не по чему.
    """
    if get_setting(_TZ_SHIFT_MARK) or not SHOP_TZ_OFFSET:
        return 0
    # Право сдвинуть историю забираем одним действием. Внутри процесса гонки нет
    # (init_db зовут последовательно), но при выкладке процессов может подняться
    # несколько, и «прочитать, потом записать» пропустило бы обоих — история
    # уехала бы на шесть часов вместо трёх. Если сдвиг сорвётся, отметку вернём
    # обратно: пропустить перевод не страшно, сделать его дважды — страшно.
    if not claim_setting(_TZ_SHIFT_MARK, _now_str()):
        return 0
    # Верхняя граница сдвига. Запись, сделанную старым кодом, видно по одному
    # признаку: она отстаёт от настоящего момента минимум на SHOP_TZ_OFFSET часов
    # — столько сервер и «терял». Значит всё, что новее этой границы, записано
    # уже новым кодом, и трогать его нельзя. Это важно, если новый код успел
    # поработать до того, как дошли руки до истории: без границы свежие заказы
    # уехали бы на три часа вперёд.
    cutoff = (shop_now() - datetime.timedelta(hours=SHOP_TZ_OFFSET)).strftime("%Y-%m-%d %H:%M")
    now_at = shop_now().strftime("%Y-%m-%d %H:%M")
    # Времена в будущем (срок окончания розыгрыша) старым кодом тоже записаны, и
    # граница «отстаёт на три часа» про них ничего не говорит. Пропускаем ровно
    # то окно, где старое от нового не отличить: последние SHOP_TZ_OFFSET часов.
    conn = connect()
    cur = conn.cursor()
    shifted = 0
    try:
        for table in _all_table_names(cur):
            if table == "settings":          # там даты-отметки, а не время событий
                continue
            for col in sorted(_table_columns(cur, table)):
                if not col.endswith(_TIME_COL):
                    continue
                if USE_PG:
                    cur.execute(
                        f"""UPDATE {table} SET "{col}" = to_char(
                                to_timestamp("{col}", 'YYYY-MM-DD HH24:MI')
                                + make_interval(hours => %s), 'YYYY-MM-DD HH24:MI')
                            WHERE "{col}" ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} [0-9]{{2}}:[0-9]{{2}}'
                              AND ("{col}" <= %s OR "{col}" > %s)""",
                        (SHOP_TZ_OFFSET, cutoff, now_at))
                else:
                    cur.execute(
                        f"""UPDATE {table} SET "{col}" = strftime('%Y-%m-%d %H:%M',
                                datetime(substr("{col}", 1, 16), '+{SHOP_TZ_OFFSET} hours'))
                            WHERE "{col}" GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]*'
                              AND ("{col}" <= ? OR "{col}" > ?)""", (cutoff, now_at))
                shifted += max(0, cur.rowcount)
        conn.commit()
        if shifted:
            print(f"История переведена на время магазина: записей {shifted}")
    except Exception as e:
        conn.rollback()
        # Не сдвинуть историю — неприятно, но терпимо: магазин работает дальше.
        # Отметку отпускаем, чтобы следующий запуск попробовал снова.
        set_setting(_TZ_SHIFT_MARK, "")
        print(f"Не смог перевести историю на время магазина: {e}")
    conn.close()
    return shifted




































# ---------- Способы получения (доставка/самовывоз) ----------

def get_delivery_methods(city):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM delivery_methods WHERE city = %s ORDER BY sort, id"), (city,))
    rows = cur.fetchall()
    conn.close()
    return rows


def add_delivery_method(city, name, needs_address, address_label, pickup_address, fee, needs_payment,
                        sort=0, needs_point=False):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""INSERT INTO delivery_methods
        (city, name, needs_address, address_label, pickup_address, fee, needs_payment, sort, needs_point)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""),
        (city, name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, int(sort), 1 if needs_point else 0))
    conn.commit()
    conn.close()


def update_delivery_method(method_id, name, needs_address, address_label, pickup_address, fee, needs_payment,
                           needs_point=False):
    """Обновляет существующий способ получения (правка на месте)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""UPDATE delivery_methods
        SET name = %s, needs_address = %s, address_label = %s,
            pickup_address = %s, fee = %s, needs_payment = %s, needs_point = %s
        WHERE id = %s"""),
        (name, 1 if needs_address else 0, address_label or "", pickup_address or "",
         float(fee or 0), 1 if needs_payment else 0, 1 if needs_point else 0, method_id))
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


# ---------- Категории товара ----------

# Стартовый набор. Первые три были в коде с самого начала — их коды менять
# нельзя: по ним заведены все существующие товары и бренды, а у одноразок и
# жидкостей к коду привязаны свои поля (затяжки, крепость и объём).
CATEGORY_SEED = [
    ("disposable",  "Одноразки",   "🔋", 10),
    ("liquid",      "Жидкости",    "💧", 20),
    ("podsystem",   "Подсистемы",  "🧩", 30),
    ("coils",       "Расходники",  "⚙️", 40),   # испарители, картриджи, вата
    ("devices",     "Устройства",  "🔧", 50),   # моды, боксы, наборы
    ("accessories", "Аксессуары",  "🔌", 60),   # зарядки, аккумуляторы, чехлы
]

_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
             "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
             "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
             "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
             "ю": "yu", "я": "ya"}


def _category_code(name):
    """Латинский код из названия: «Расходники» → «rashodniki».

    Код — внутреннее имя: он попадает в ссылки и хранится в каждом товаре.
    Кириллица в таких местах живёт плохо, поэтому переводим сразу."""
    out = []
    for ch in (name or "").strip().lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("_")
    code = "".join(out).strip("_")[:24]
    return code or "cat"


def seed_categories():
    """Стартовые категории — только если таблица пустая. Дальше их ведёт админ."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM categories")
    if cur.fetchone()["c"] == 0:
        for code, name, emoji, sort in CATEGORY_SEED:
            cur.execute(_q("INSERT INTO categories (code, name, emoji, sort) VALUES (%s, %s, %s, %s)"),
                        (code, name, emoji, sort))
        conn.commit()
    conn.close()


# Характеристики по категориям. Набор собран по тому, что реально указывают в
# карточках вейп-магазины: у картриджа спрашивают сопротивление, объём и
# совместимость, у пода — мощность, аккумулятор и тип затяжки. Владелец может
# всё это менять, здесь только разумная отправная точка.
# key strength/volume — особые: они лежат в своих колонках товара (см. SPEC_COLUMNS).
SPEC_SEED = {
    "disposable": [
        ("volume", "Затяжек", "", "number", None),
        ("strength", "Крепость", "мг", "number", None),
    ],
    "liquid": [
        ("strength", "Крепость", "мг", "number", None),
        ("volume", "Объём", "мл", "number", None),
        ("base", "Тип", "", "select", ["Солевая", "Классическая"]),
    ],
    "podsystem": [
        ("power", "Мощность", "Вт", "number", None),
        ("battery", "Аккумулятор", "мАч", "number", None),
        ("pod_volume", "Объём картриджа", "мл", "number", None),
        ("draw", "Затяжка", "", "select", ["MTL (сигаретная)", "RDL", "DL (прямая)", "Регулируемая"]),
        ("charge", "Зарядка", "", "select", ["USB-C", "Micro-USB", "Беспроводная"]),
        ("screen", "Экран", "", "select", ["Есть", "Нет"]),
    ],
    "coils": [
        ("kind", "Тип", "", "select", ["Картридж", "Испаритель", "Койл", "Вата"]),
        ("resistance", "Сопротивление", "Ом", "number", None),
        ("volume", "Объём", "мл", "number", None),
        ("fit", "Совместимость", "", "text", None),
        ("pack", "В упаковке", "шт", "number", None),
    ],
    "devices": [
        ("power", "Мощность", "Вт", "number", None),
        ("battery", "Аккумулятор", "мАч", "number", None),
        ("battery_type", "Питание", "", "select", ["Встроенный", "18650", "21700", "2×18650"]),
        ("screen", "Экран", "", "select", ["Есть", "Нет"]),
    ],
    "accessories": [
        ("kind", "Тип", "", "text", None),
        ("fit", "Совместимость", "", "text", None),
    ],
}

# Эти характеристики хранятся в собственных колонках товара, а не в JSON:
# по ним живут прежние товары, подписи в каталоге и карточка в боте.
SPEC_COLUMNS = ("strength", "volume")
SPEC_KINDS = ("number", "text", "select")


def seed_category_specs():
    """Стартовые характеристики — только для категорий, у которых их ещё нет."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category AS c FROM category_specs")
    have = {r["c"] for r in cur.fetchall()}
    for category, rows in SPEC_SEED.items():
        if category in have:
            continue
        for i, (key, label, unit, kind, options) in enumerate(rows):
            cur.execute(_q("INSERT INTO category_specs (category, key, label, unit, kind, options, sort) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s)"),
                        (category, key, label, unit, kind,
                         (json.dumps(options, ensure_ascii=False) if options else None), (i + 1) * 10))
    conn.commit()
    conn.close()


def _spec_json(r):
    try:
        options = json.loads(r["options"]) if r["options"] else []
    except (TypeError, ValueError):
        options = []
    return {"id": r["id"], "category": r["category"], "key": r["key"], "label": r["label"],
            "unit": r["unit"] or "", "kind": r["kind"] or "text", "options": options, "sort": r["sort"]}


def list_category_specs(category=None):
    conn = connect()
    cur = conn.cursor()
    if category:
        cur.execute(_q("SELECT * FROM category_specs WHERE category = %s ORDER BY sort, id"), (category,))
    else:
        cur.execute("SELECT * FROM category_specs ORDER BY category, sort, id")
    rows = [_spec_json(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_category_spec(category, label, unit="", kind="text", options=None, key=None):
    """Добавляет характеристику категории. Возвращает id или None, если такая уже есть."""
    label = (label or "").strip()[:40]
    if not label:
        return None
    kind = kind if kind in SPEC_KINDS else "text"
    key = (key or _category_code(label))[:32]
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT 1 AS x FROM category_specs WHERE category = %s AND key = %s"), (category, key))
    if cur.fetchone():
        conn.close()
        return None
    cur.execute(_q("SELECT COALESCE(MAX(sort), 0) AS mx FROM category_specs WHERE category = %s"), (category,))
    sort = int(cur.fetchone()["mx"]) + 10
    sid = _insert_id(cur, "INSERT INTO category_specs (category, key, label, unit, kind, options, sort) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (category, key, label, (unit or "").strip()[:12], kind,
                      (json.dumps(options, ensure_ascii=False) if options else None), sort))
    conn.commit()
    conn.close()
    return sid


def update_category_spec(spec_id, label=None, unit=None, options=None, sort=None):
    """Правит подпись, единицу, варианты и порядок. Ключ не меняется — за ним значения товаров."""
    conn = connect()
    cur = conn.cursor()
    if label is not None:
        cur.execute(_q("UPDATE category_specs SET label = %s WHERE id = %s"), ((label or "").strip()[:40], spec_id))
    if unit is not None:
        cur.execute(_q("UPDATE category_specs SET unit = %s WHERE id = %s"), ((unit or "").strip()[:12], spec_id))
    if options is not None:
        cur.execute(_q("UPDATE category_specs SET options = %s WHERE id = %s"),
                    (json.dumps(options, ensure_ascii=False) if options else None, spec_id))
    if sort is not None:
        cur.execute(_q("UPDATE category_specs SET sort = %s WHERE id = %s"), (int(sort), spec_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_category_spec(spec_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM category_specs WHERE id = %s"), (spec_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def product_specs(row):
    """Характеристики товара: JSON + значения из собственных колонок."""
    out = {}
    try:
        raw = row["specs"] if "specs" in row.keys() else None
    except (AttributeError, TypeError):
        raw = row.get("specs") if isinstance(row, dict) else None
    if raw:
        try:
            out.update({k: v for k, v in json.loads(raw).items() if str(v).strip() != ""})
        except (TypeError, ValueError):
            pass
    for col in SPEC_COLUMNS:
        try:
            val = row[col]
        except (KeyError, IndexError, TypeError):
            val = None
        if val not in (None, ""):
            out[col] = val
    return out


def set_product_specs(product_id, values):
    """Сохраняет характеристики товара. strength/volume уходят в свои колонки,
    остальное — в JSON: так прежние товары и подписи в каталоге остаются целы."""
    values = {str(k): ("" if v is None else str(v).strip()) for k, v in (values or {}).items()}
    extra = {k: v for k, v in values.items() if k not in SPEC_COLUMNS and v != ""}
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET specs = %s WHERE id = %s"),
                (json.dumps(extra, ensure_ascii=False) if extra else None, product_id))
    for col in SPEC_COLUMNS:
        if col in values:
            cur.execute(_q(f"UPDATE products SET {col} = %s WHERE id = %s"), (values[col], product_id))
    conn.commit()
    conn.close()


def list_categories():
    conn = connect()
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT 1 AS x FROM categories WHERE code = %s OR LOWER(name) = %s"), (code, name.lower()))
    if cur.fetchone():
        conn.close()
        return None
    if not sort:
        cur.execute("SELECT COALESCE(MAX(sort), 0) AS mx FROM categories")
        sort = int(cur.fetchone()["mx"]) + 10          # новая встаёт в конец списка
    cur.execute(_q("INSERT INTO categories (code, name, emoji, sort) VALUES (%s, %s, %s, %s)"),
                (code, name, (emoji or "").strip()[:8], int(sort)))
    conn.commit()
    conn.close()
    return code


def update_category(code, name=None, emoji=None, sort=None, has_flavors=None):
    """Переименовать категорию или сменить значок. Код не меняется — за ним товары."""
    conn = connect()
    cur = conn.cursor()
    if has_flavors is not None:
        cur.execute(_q("UPDATE categories SET has_flavors = %s WHERE code = %s"),
                    (1 if has_flavors else 0, code))
    if name is not None:
        cur.execute(_q("UPDATE categories SET name = %s WHERE code = %s"), ((name or "").strip()[:40], code))
    if emoji is not None:
        cur.execute(_q("UPDATE categories SET emoji = %s WHERE code = %s"), ((emoji or "").strip()[:8], code))
    if sort is not None:
        cur.execute(_q("UPDATE categories SET sort = %s WHERE code = %s"), (int(sort), code))
    conn.commit()
    conn.close()


def count_products_in_category(code):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM products WHERE category = %s"), (code,))
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def delete_category(code):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM categories WHERE code = %s"), (code,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ---------- 18+ ----------

def is_age_ok(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT age_ok FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["age_ok"] == 1)


def get_me_bundle(user_id, limit=20):
    """Всё, что нужно приложению при открытии, за ОДНО подключение.

    Раньше эти же данные собирались девятью походами в базу через восемь
    подключений: 18+, напоминания, телефон, точка — четыре отдельных чтения
    ОДНОЙ И ТОЙ ЖЕ строки покупателя. Локально это незаметно, а база магазина
    живёт по сети, и каждый поход — отдельный разговор с ней. Экран открытия
    платит за это первым.

    Тот же приём, что и в get_checkout_data: один поход за всем сразу.
    """
    now = shop_now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("INSERT INTO users (user_id, created_at) VALUES (%s, %s) "
                    "ON CONFLICT (user_id) DO NOTHING", (user_id, now))
    else:
        cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
                    (user_id, now))

    # Одна строка покупателя — одним чтением, а не четырьмя.
    cur.execute(_q("SELECT age_ok, no_reminders, phone, pickup_point_id "
                   "FROM users WHERE user_id = %s"), (user_id,))
    u = cur.fetchone() or {}

    cur.execute(_q("SELECT product_id FROM stock_alerts WHERE user_id = %s"), (user_id,))
    alerts = [int(r["product_id"]) for r in cur.fetchall()]

    cur.execute(_q("""SELECT delivery_method, delivery_address, phone
                      FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT %s"""),
                (user_id, limit))
    past_orders = cur.fetchall()

    cur.execute("SELECT 1 AS x FROM raffles LIMIT 1")
    raffle_on = cur.fetchone() is not None

    conn.commit()
    conn.close()

    # Телефон из настроек важнее: покупатель сам его туда вписал, а в старом
    # заказе мог быть чужой или устаревший номер.
    phone = (u["phone"] or "").strip() if u and u["phone"] else ""
    addresses = {}
    for r in past_orders:                # строки идут от новых к старым
        if not phone and (r["phone"] or "").strip():
            phone = r["phone"].strip()
        method = (r["delivery_method"] or "").strip()
        addr = (r["delivery_address"] or "").strip()
        if method and addr and method not in addresses:
            addresses[method] = addr

    return {
        "age_ok": bool(u and u["age_ok"] == 1),
        "reminders_on": not bool(u and u["no_reminders"]),
        "my_point": (int(u["pickup_point_id"]) if u and u["pickup_point_id"] else None),
        "alerts": alerts,
        "prefill": {"phone": phone, "addresses": addresses},
        "raffle_on": raffle_on,
    }


def get_settings(keys, defaults=None):
    """Несколько настроек одним запросом.

    Экран настроек читал по одному ключу за раз — восемь походов в базу подряд
    ради восьми строк из одной маленькой таблицы."""
    keys = list(keys)
    defaults = defaults or {}
    if not keys:
        return {}
    conn = connect()
    cur = conn.cursor()
    marks = ",".join(["%s"] * len(keys))
    cur.execute(_q(f"SELECT key, value FROM settings WHERE key IN ({marks})"), tuple(keys))
    got = {r["key"]: r["value"] for r in cur.fetchall()}
    conn.close()
    return {k: (got[k] if got.get(k) is not None else defaults.get(k)) for k in keys}


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
    now = shop_now().strftime("%Y-%m-%d %H:%M")
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


# За что двигались монеты. Нужен, чтобы владелец видел не «баланс у всех вырос»,
# а откуда именно берётся раздача и сколько она стоит.
COIN_REASONS = {
    "cashback":  "Кэшбэк с заказов",
    "wheel":     "Колесо фортуны",
    "slot":      "Слот «Облако Монет»",
    "referral":  "Реферальная программа",
    "raffle":    "Розыгрыш",
    "admin":     "Правка вручную",
    "compensation": "Компенсация покупателю",
    "refund":    "Возврат при отмене",
    "order":     "Оплата заказа монетами",
    "other":     "Прочее",
}


def log_coins(user_id, delta, reason="other"):
    """Запись в летопись монет. Без неё «роздано за месяц» пришлось бы угадывать
    по остаткам на балансах, а это неверно: часть монет уже потрачена."""
    if not delta:
        return
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute(_q("INSERT INTO coin_log (user_id, delta, reason, created_at) "
                       "VALUES (%s, %s, %s, %s)"),
                    (user_id, int(delta), reason if reason in COIN_REASONS else "other",
                     _now_str()))
        conn.commit()
        conn.close()
    except Exception as e:
        # Летопись — это отчётность, а не работа магазина: если запись не удалась,
        # монеты всё равно должны начислиться.
        print(f"Не удалось записать движение монет ({user_id}, {delta}, {reason}): {e}")


def add_coins(user_id, n, reason="other"):
    """Меняет баланс на n (может быть отрицательным), не опускаясь ниже нуля."""
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q(f"UPDATE users SET coins = {GREATEST}(0, COALESCE(coins, 0) + %s) WHERE user_id = %s"),
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

REFERRAL_BONUS = 50                       # монет пригласившему за ПЕРВЫЙ заказ друга (по умолчанию)


def referral_bonus():
    """Бонус за первый заказ друга. Владелец меняет его в настройках магазина."""
    try:
        v = int(float(get_setting("referral_bonus", REFERRAL_BONUS)))
        return max(0, v)
    except (TypeError, ValueError):
        return REFERRAL_BONUS


def coins_per_byn():
    """Сколько монет начисляем за каждый Br выданного заказа (кэшбэк)."""
    try:
        v = float(get_setting("coins_per_byn", 1))
        return max(0.0, v)
    except (TypeError, ValueError):
        return 1.0
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
    now = shop_now().strftime("%Y-%m-%d %H:%M")
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
        cur.execute(_q(
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM users WHERE user_id = %s"), (user_id,))
    u = cur.fetchone()
    # Берём все заказы, а не первые N: суммы и любимые товары должны считаться по
    # всей истории, даже если на экран попадёт только последняя страница.
    cur.execute(_q("SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT 500"), (user_id,))
    rows = cur.fetchall()
    if not u and not rows:
        conn.close()
        return None
    cur.execute(_q("SELECT COUNT(*) AS c FROM users WHERE referred_by = %s"), (user_id,))
    referrals = cur.fetchone()["c"]
    point = ""
    point_id = (u["pickup_point_id"] if u else None)
    if point_id:
        cur.execute(_q("SELECT address FROM pickup_points WHERE id = %s"), (point_id,))
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
        given = round(int(r["coins_used"] or 0) * COIN_VALUE + float(r["promo_discount"] or 0), 2) \
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
            days_since = (shop_now() - datetime.datetime.strptime(last_buy[:16], "%Y-%m-%d %H:%M")).days
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

COMPENSATION_MAX_DEFAULT = 1000     # монет за раз (при 1 монета = 0.01 Br это 10 Br)


def compensation_max():
    """Потолок одной компенсации. Продавец не должен раздать состояние опечаткой,
    а владелец не должен ради изменения потолка ходить к разработчику."""
    try:
        v = int(get_setting("compensation_max", COMPENSATION_MAX_DEFAULT))
        return max(0, v)
    except (TypeError, ValueError):
        return COMPENSATION_MAX_DEFAULT


def create_admin_request(requester_id, requester_name, action, payload, summary):
    """Создаёт заявку в статусе pending. Возвращает её id."""
    created = shop_now().strftime("%Y-%m-%d %H:%M")
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
        t = int(payload["user_id"]); add_coins(t, int(payload["delta"]), "admin")
        return {"coins": get_coins(t)}
    if action == "grant":
        t = int(payload["user_id"]); ensure_user(t)
        if int(payload.get("coins", 0)): add_coins(t, int(payload["coins"]), "admin")
        if int(payload.get("spins", 0)): add_spins(t, int(payload["spins"]))
        return {"coins": get_coins(t), "spins": get_wheel(t)["spins"]}
    if action == "compensate":
        # Компенсация — отдельная причина, а не «правка вручную»: владельцу важно
        # видеть в летописи монет, сколько ушло на извинения перед покупателями.
        t = int(payload["user_id"])
        ensure_user(t)
        add_coins(t, int(payload["coins"]), "compensation")
        return {"coins": get_coins(t), "user_id": t,
                "order_id": payload.get("order_id"), "granted": int(payload["coins"])}
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


















def set_referrer_once(user_id, ref_id):
    """Записывает пригласившего, если он ещё не задан и это не сам пользователь.
    Возвращает True, если запись применилась (тогда наградой занимается вызывающий)."""
    if not ref_id or ref_id == user_id:
        return False
    ensure_user(user_id)
    row = get_user_row(user_id)
    if row and row["referred_by"]:
        return False                      # реферер уже есть — второй раз не начисляем
    ref_row = get_user_row(ref_id)
    if not ref_row:
        return False                      # пригласивший должен существовать
    # «Я привёл того, кто привёл меня» — так не бывает. Двое заводили друг друга
    # по кругу и оба получали бонус за первый заказ: настоящей рекомендации тут
    # нет, есть два аккаунта одного человека.
    if ref_row["referred_by"] == user_id:
        return False
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
    now = shop_now()
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
        cur.execute(_q("SELECT items, coins_used, promo_discount FROM orders "
                       "WHERE status='issued' AND created_at >= %s"), (cutoff,))
    else:
        cur.execute("SELECT items, coins_used, promo_discount FROM orders WHERE status='issued'")
    qty_by_name, rev_by_name, profit_by_name = {}, {}, {}
    profit = 0.0            # прибыль ТОЛЬКО по позициям с известной закупочной ценой
    revenue_known = 0.0     # выручка этих же позиций — чтобы посчитать наценку
    revenue_unknown = 0.0   # выручка там, где закупочная цена не заполнена
    for r in cur.fetchall():
        try:
            items = json.loads(r["items"])
            # Монеты и промокод — это НЕ подарок покупателю за наш счёт «где-то
            # там», а прямой вычет из денег за этот заказ. Раньше прибыль
            # считалась по ценникам, будто скидки не было: заказ на 25.77 при
            # закупке 22 показывал прибыль 14 вместо 3.77 — и по таким числам
            # принимались решения о закупке.
            paid_for_goods = sum(float(i.get("price", 0) or 0) * int(i.get("qty", 0)) for i in items)
            given = round(int(r["coins_used"] or 0) * COIN_VALUE + float(r["promo_discount"] or 0), 2)
            given = min(given, paid_for_goods)
            for it in items:
                nm = it.get("name", "?")
                q = int(it.get("qty", 0))
                price = float(it.get("price", 0) or 0)
                cost = float(it.get("cost", 0) or 0)
                line = q * price
                # Скидка ложится на позиции пропорционально их доле в заказе.
                off = (line / paid_for_goods * given) if paid_for_goods else 0.0
                qty_by_name[nm] = qty_by_name.get(nm, 0) + q
                rev_by_name[nm] = rev_by_name.get(nm, 0) + line - off
                # Нулевая закупочная цена — это «не заполнено», а не «досталось
                # даром». Считать её прибылью значит рисовать себе доход,
                # которого нет, поэтому такие позиции идут отдельной строкой.
                if cost > 0:
                    earned = line - off - q * cost
                    profit += earned
                    revenue_known += line - off
                    profit_by_name[nm] = profit_by_name.get(nm, 0) + earned
                else:
                    revenue_unknown += line - off
        except (TypeError, ValueError):
            pass
    top = [{"name": n, "qty": q, "revenue": round(rev_by_name.get(n, 0), 2),
            "profit": (round(profit_by_name[n], 2) if n in profit_by_name else None)}
           for n, q in sorted(qty_by_name.items(), key=lambda x: -x[1])[:8]]
    margin = (profit / revenue_known * 100) if revenue_known else 0

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
        "profit": round(profit, 2), "margin": round(margin, 1),
        "revenue_unknown_cost": round(revenue_unknown, 2),   # выручка без закупочной цены
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


def claim_setting(key, value):
    """Занять отметку «сделано»: записать value, только если там было другое.
    True получает ровно один вызвавший, остальные — False.

    Нужно там, где по отметке решают, слать ли сообщения живым людям. Прочитать,
    а потом записать — это два действия, и между ними влезает второй экземпляр
    сервиса: Render при деплое некоторое время держит старый и новый вместе, и
    оба успевают увидеть «сегодня ещё не делали». Условие внутри UPDATE такой
    щели не оставляет — базa разрешает спор сама.
    """
    conn = connect()
    cur = conn.cursor()
    # UPDATE'у нужна строка, за которую можно зацепиться.
    if USE_PG:
        cur.execute("INSERT INTO settings (key, value) VALUES (%s, '') "
                    "ON CONFLICT (key) DO NOTHING", (key,))
    else:
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, '')", (key,))
    # COALESCE обязателен: сравнение с NULL не истинно и не ложно, и строка со
    # значением NULL не совпала бы никогда — отметку не удалось бы занять вовсе.
    cur.execute(_q("UPDATE settings SET value = %s "
                   "WHERE key = %s AND COALESCE(value, '') <> %s"),
                (str(value), key, str(value)))
    won = cur.rowcount > 0
    conn.commit()
    conn.close()
    return won


# ---------- Движение склада ----------

# Причины движения. Приход прибавляет, остальное списывает.
STOCK_REASONS = {
    "in":      "Приход",
    "broken":  "Брак или бой",
    "expired": "Просрочка",
    "lost":    "Недостача",
    "gift":    "Подарок или образец",
    "fix":     "Пересчёт",
}


def move_stock(product_id, delta, reason, flavor=None, cost=0, note="", admin_id=None):
    """Меняет остаток и ЗАПИСЫВАЕТ движение. Возвращает новый остаток.

    Всё одной транзакцией: остаток и запись о нём не должны разъезжаться —
    иначе появится изменение, которого «никто не делал».
    """
    # При списании цену никто не вводит — берём закупочную товара на этот момент,
    # иначе потеря посчитается нулём и убыток окажется невидимым.
    if delta < 0 and not cost:
        p0 = get_product(product_id)
        cost = float(p0["cost"] or 0) if p0 else 0

    conn = connect()
    cur = conn.cursor()
    try:
        if flavor:
            cur.execute(_q(f"UPDATE product_variants SET stock = {GREATEST}(0, stock + %s) "
                           "WHERE product_id = %s AND flavor = %s"), (delta, product_id, flavor))
        else:
            cur.execute(_q(f"UPDATE products SET stock = {GREATEST}(0, stock + %s) WHERE id = %s"),
                        (delta, product_id))
        # Приход по новой цене обновляет закупочную: считать прибыль по старой
        # цене после подорожания — значит обманывать себя.
        if reason == "in" and cost and cost > 0:
            cur.execute(_q("UPDATE products SET cost = %s WHERE id = %s"), (float(cost), product_id))
        cur.execute(_q("""INSERT INTO stock_moves (product_id, flavor, delta, reason, cost, note, admin_id, created_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""),
                    (product_id, flavor or None, int(delta), reason, float(cost or 0),
                     (note or "").strip()[:120], admin_id, _now_str()))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    if flavor:
        recalc_product_stock(product_id)
    p = get_product(product_id)
    return int(p["stock"]) if p else 0


def get_stock_moves(product_id=None, limit=100, city=None):
    """Движения склада. city ограничивает выборку одной точкой: без товара в
    запросе продавец иначе получал бы всю историю магазина — а по ней видно
    завоз и списания соседних точек."""
    conn = connect()
    cur = conn.cursor()
    if product_id:
        cur.execute(_q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE m.product_id = %s ORDER BY m.id DESC LIMIT %s"""), (product_id, limit))
    elif city:
        cur.execute(_q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE p.city = %s ORDER BY m.id DESC LIMIT %s"""), (city, limit))
    else:
        cur.execute(_q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          ORDER BY m.id DESC LIMIT %s"""), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def stock_losses(days=None):
    """Во сколько обошлись списания за период — по закупочной цене на момент
    движения. Это настоящие деньги, и владелец должен их видеть."""
    conn = connect()
    cur = conn.cursor()
    cutoff = ((shop_now() - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00")
              if days else None)
    sql = """SELECT reason, SUM(-delta) AS qty,
                    SUM(-delta * COALESCE(NULLIF(cost, 0), 0)) AS money
             FROM stock_moves WHERE delta < 0"""
    if cutoff:
        cur.execute(_q(sql + " AND created_at >= %s GROUP BY reason"), (cutoff,))
    else:
        cur.execute(sql + " GROUP BY reason")
    rows = [{"reason": r["reason"], "qty": int(r["qty"] or 0), "money": round(float(r["money"] or 0), 2)}
            for r in cur.fetchall()]
    conn.close()
    return sorted(rows, key=lambda r: -r["money"])


# ---------- Промокоды ----------

def _promo_row(code):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM promos WHERE code = %s"), (code.strip().upper(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def check_promo(code, user_id, subtotal):
    """Можно ли применить код. Возвращает (скидка, ошибка).

    Считаем ЗДЕСЬ, а не на клиенте: скидка — это деньги, и присланную сумму
    принимать на веру нельзя."""
    # Код приходит из запроса: строкой он быть обязан, но прислать могут что
    # угодно, а промокод — это деньги, и падать здесь нельзя.
    code = (code if isinstance(code, str) else "").strip().upper()
    if not code:
        return 0.0, None
    p = _promo_row(code)
    if not p or not p["active"]:
        return 0.0, "promo_unknown"
    if p["uses_left"] is not None and p["uses_left"] <= 0:
        return 0.0, "promo_used_up"
    if subtotal < (p["min_total"] or 0):
        return 0.0, "promo_min"
    if p["once_per_user"]:
        conn = connect()
        cur = conn.cursor()
        cur.execute(_q("SELECT COUNT(*) AS c FROM orders WHERE user_id = %s AND promo_code = %s "
                       "AND status != 'canceled'"), (user_id, code))
        used = cur.fetchone()["c"]
        conn.close()
        if used:
            return 0.0, "promo_once"

    if p["kind"] == "fixed":
        discount = float(p["value"] or 0)
    else:
        discount = subtotal * float(p["value"] or 0) / 100.0
    # Скидка не может превышать стоимость товаров: иначе магазин доплачивает.
    return round(min(discount, subtotal), 2), None


def consume_promo(code):
    """Списывает одно использование. Без ограничения по числу — ничего не делает."""
    code = (code or "").strip().upper()
    if not code:
        return
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE promos SET uses_left = uses_left - 1 "
                   "WHERE code = %s AND uses_left IS NOT NULL AND uses_left > 0"), (code,))
    conn.commit()
    conn.close()


def list_promos():
    """Коды со статистикой: сколько раз применили и сколько это принесло.
    Ради этой таблицы промокоды и заводятся — она отвечает, сработал ли пост."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM promos ORDER BY id DESC")
    promos = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT promo_code AS code, COUNT(*) AS n,
                          COALESCE(SUM(total), 0) AS revenue,
                          COALESCE(SUM(promo_discount), 0) AS given
                   FROM orders WHERE promo_code IS NOT NULL AND status = 'issued'
                   GROUP BY promo_code""")
    stats = {r["code"]: dict(r) for r in cur.fetchall()}
    conn.close()
    for p in promos:
        st = stats.get(p["code"], {})
        p["orders"] = int(st.get("n", 0))
        p["revenue"] = round(float(st.get("revenue", 0) or 0), 2)
        p["given"] = round(float(st.get("given", 0) or 0), 2)
    return promos


def add_promo(code, kind, value, min_total=0, uses_left=None, once_per_user=True):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""INSERT INTO promos (code, kind, value, min_total, uses_left, once_per_user, active, created_at)
                      VALUES (%s, %s, %s, %s, %s, %s, 1, %s)"""),
                (code.strip().upper(), kind, float(value or 0), float(min_total or 0),
                 uses_left, 1 if once_per_user else 0, _now_str()))
    conn.commit()
    conn.close()


def set_promo_active(code, active):
    code = (code if isinstance(code, str) else "").strip().upper()
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE promos SET active = %s WHERE code = %s"),
                (1 if active else 0, code))
    conn.commit()
    conn.close()


def delete_promo(code):
    # Код может прийти чем угодно из запроса — промокоды правит человек руками.
    code = (code if isinstance(code, str) else "").strip().upper()
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM promos WHERE code = %s"), (code,))
    conn.commit()
    conn.close()


# ---------- Точки самовывоза ----------

def get_pickup_points(city):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM pickup_points WHERE city = %s ORDER BY sort, id"), (city,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_user_phone(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT phone FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return (row["phone"] or "") if row else ""


def set_user_phone(user_id, phone):
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET phone = %s WHERE user_id = %s"),
                ((phone or "").strip()[:40], user_id))
    conn.commit()
    conn.close()


def get_user_point(user_id):
    """Своя точка самовывоза покупателя (или None)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT pickup_point_id FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["pickup_point_id"]) if row and row["pickup_point_id"] else None


def set_user_point(user_id, point_id):
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET pickup_point_id = %s WHERE user_id = %s"),
                (int(point_id) if point_id else None, user_id))
    conn.commit()
    conn.close()


def add_pickup_point(city, address, note="", sort=0):
    conn = connect()
    cur = conn.cursor()
    pid = _insert_id(cur, """INSERT INTO pickup_points (city, address, note, sort)
                             VALUES (%s, %s, %s, %s)""", (city, address, note or "", sort))
    conn.commit()
    conn.close()
    return pid


def update_pickup_point(point_id, address, note=""):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE pickup_points SET address = %s, note = %s WHERE id = %s"),
                (address, note or "", point_id))
    conn.commit()
    conn.close()


def delete_pickup_point(point_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM pickup_points WHERE id = %s"), (point_id,))
    conn.commit()
    conn.close()


# ---------- Что подставить в оформление ----------

def delivery_prefill(user_id, limit=20):
    """Телефон и адреса из прошлых заказов этого покупателя.

    Новой таблицы не заводим — всё уже лежит в orders. Адрес помним ОТДЕЛЬНО
    для каждого способа получения: у «Доставки по метро» это станция, у курьера
    — улица с домом, и подставлять одно вместо другого нельзя.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""SELECT delivery_method, delivery_address, phone
                      FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT %s"""),
                (user_id, limit))
    rows = cur.fetchall()
    conn.close()

    # Телефон из настроек важнее: покупатель сам его туда вписал, а в старом
    # заказе мог быть чужой или устаревший номер.
    phone = get_user_phone(user_id)
    addresses = {}
    for r in rows:                       # строки идут от новых к старым
        if not phone and (r["phone"] or "").strip():
            phone = r["phone"].strip()
        method = (r["delivery_method"] or "").strip()
        addr = (r["delivery_address"] or "").strip()
        if method and addr and method not in addresses:
            addresses[method] = addr
    return {"phone": phone, "addresses": addresses}


# ---------- Напоминание о повторной покупке ----------

def issued_orders_count():
    """Сколько заказов магазин довёл до выдачи — единственное доказательство,
    которое видит новый покупатель, прежде чем перевести деньги незнакомцу."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM orders WHERE status = 'issued'")
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def customers_to_remind(days, limit, cooldown_days=None):
    """Кому пора напомнить: последний ВЫДАННЫЙ заказ старше `days` дней.

    Считаем только выданные: по неоплаченному или отклонённому заказу человек
    ничего не получил, и напоминать ему «пора пополнить» странно.

    limit — потолок за один прогон. Он важнее, чем кажется: в день запуска
    просроченными окажутся сразу ВСЕ давние покупатели, и без потолка это
    превратится в веерную рассылку, за которую Telegram наказывает.
    """
    cooldown_days = days if cooldown_days is None else cooldown_days
    now = shop_now()
    due_before = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    quiet_before = (now - datetime.timedelta(days=cooldown_days)).strftime("%Y-%m-%d %H:%M")

    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""
        SELECT o.user_id AS user_id, MAX(o.created_at) AS last_order
        FROM orders o
        WHERE o.status = 'issued'
        GROUP BY o.user_id
        HAVING MAX(o.created_at) < %s
        ORDER BY MAX(o.created_at) DESC
        LIMIT %s
    """), (due_before, limit * 5))          # берём с запасом: часть отсеется ниже
    rows = [dict(r) for r in cur.fetchall()]

    out = []
    for r in rows:
        cur.execute(_q("SELECT no_reminders, reminded_at FROM users WHERE user_id = %s"), (r["user_id"],))
        u = cur.fetchone()
        if u and u["no_reminders"]:
            continue                        # человек попросил не писать
        if u and u["reminded_at"] and u["reminded_at"] > quiet_before:
            continue                        # недавно уже напоминали
        out.append(r)
        if len(out) >= limit:
            break
    conn.close()
    return out


def mark_reminded(user_id):
    # ensure_user обязателен: покупатель мог оформить заказ, но строки в users
    # не иметь. Тогда UPDATE не задел бы ничего, отметка не сохранилась бы — и
    # человек получал бы напоминание КАЖДЫЙ день, пока не заблокирует бота.
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET reminded_at = %s WHERE user_id = %s"), (_now_str(), user_id))
    conn.commit()
    conn.close()


def set_no_reminders(user_id, value):
    ensure_user(user_id)
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET no_reminders = %s WHERE user_id = %s"), (1 if value else 0, user_id))
    conn.commit()
    conn.close()


def get_no_reminders(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT no_reminders FROM users WHERE user_id = %s"), (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["no_reminders"])


# ---------- Резервная копия ----------

# Картинки в копию НЕ кладём: это кэш, их всегда можно скачать заново из
# Telegram по file_id, зато весят они больше всех остальных данных вместе взятых.
COIN_VALUE = 0.01        # 100 монет = 1 Br (та же цена, что и при списании)

BACKUP_SKIP = {"photo_blobs"}


def _all_table_names(cur):
    """Имена таблиц спрашиваем У БАЗЫ, а не держим списком: иначе новая таблица
    однажды появится, а в резервную копию её забудут добавить — и потеряется
    именно она."""
    if USE_PG:
        cur.execute("SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return sorted(r["name"] for r in cur.fetchall())


def export_tables():
    """Всё содержимое базы: {таблица: [строки]}. Схему не сохраняем — её заново
    создаёт init_db(), поэтому копия переживает и обновления кода."""
    conn = connect()
    cur = conn.cursor()
    out = {}
    for table in _all_table_names(cur):
        if table in BACKUP_SKIP:
            continue
        cur.execute(f"SELECT * FROM {table}")
        out[table] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return out


def import_tables(data, wipe=True):
    """Заливает копию обратно. wipe=True — сначала чистит таблицы.

    Пропускает таблицы и колонки, которых в нынешней схеме уже нет: копия могла
    быть снята до обновления кода, и упасть на одном лишнем поле она не должна."""
    conn = connect()
    cur = conn.cursor()
    present = set(_all_table_names(cur))
    report = {}
    for table, rows in data.items():
        if table not in present:
            report[table] = "таблицы больше нет — пропущено"
            continue
        cols = _table_columns(cur, table)
        if wipe:
            cur.execute(f"DELETE FROM {table}")
        loaded = 0
        for row in rows:
            fields = [c for c in row if c in cols]
            if not fields:
                continue
            marks = ", ".join(["%s"] * len(fields))
            cur.execute(_q(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({marks})"),
                        tuple(row[f] for f in fields))
            loaded += 1
        report[table] = f"{loaded} строк"
    conn.commit()
    conn.close()
    return report


# ---------- «Сообщить о поступлении» ----------

def add_stock_alert(product_id, user_id):
    """Покупатель ждёт этот товар. Повторное нажатие не создаёт дубль."""
    conn = connect()
    cur = conn.cursor()
    sql = ("INSERT INTO stock_alerts (product_id, user_id, created_at) VALUES (%s, %s, %s) "
           + ("ON CONFLICT (product_id, user_id) DO NOTHING" if USE_PG else ""))
    if USE_PG:
        cur.execute(sql, (product_id, user_id, _now_str()))
    else:
        cur.execute("INSERT OR IGNORE INTO stock_alerts (product_id, user_id, created_at) "
                    "VALUES (?, ?, ?)", (product_id, user_id, _now_str()))
    conn.commit()
    conn.close()


def alerts_of_user(user_id):
    """Товары, поступления которых ждёт этот покупатель — чтобы витрина показала,
    что он уже подписан, и не предлагала нажать ещё раз."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT product_id FROM stock_alerts WHERE user_id = %s"), (user_id,))
    ids = [int(r["product_id"]) for r in cur.fetchall()]
    conn.close()
    return ids


def remove_stock_alert(product_id, user_id):
    """Покупатель передумал ждать. Подписка ставилась одним нажатием, а снять её
    было нельзя вовсе — оставалось терпеть сообщение о товаре, который уже не
    нужен, или блокировать бота."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM stock_alerts WHERE product_id = %s AND user_id = %s"),
                (product_id, user_id))
    conn.commit()
    conn.close()


def stock_alerts_ready():
    """Кого пора обрадовать: подписки на товары, которые СНОВА в наличии.
    Возвращает [(user_id, product_id, название)]."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("""SELECT a.user_id, a.product_id, p.name
                   FROM stock_alerts a JOIN products p ON p.id = a.product_id
                   WHERE p.stock > 0""")
    rows = [(int(r["user_id"]), int(r["product_id"]), r["name"]) for r in cur.fetchall()]
    conn.close()
    return rows


def clear_stock_alerts(product_id):
    """Сообщили — подписки на этот товар больше не нужны."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM stock_alerts WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def coin_flow(days=None):
    """Сколько монет роздано и списано за период, с разбивкой по причинам.

    Считается по летописи, а не по балансам: розданное и уже потраченное на
    балансах не видно вовсе, и раздача выглядела бы меньше, чем есть.
    """
    conn = connect()
    cur = conn.cursor()
    where, params = "", ()
    if days:
        cutoff = (shop_now() - datetime.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M")
        where, params = "WHERE created_at >= %s", (cutoff,)
    cur.execute(_q(f"SELECT reason AS r, "
                   f"COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS plus, "
                   f"COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END), 0) AS minus "
                   f"FROM coin_log {where} GROUP BY reason"), params)
    rows = cur.fetchall()
    conn.close()
    granted = sum(int(r["plus"]) for r in rows)
    spent = sum(int(r["minus"]) for r in rows)
    by_reason = sorted(
        ({"reason": r["r"] or "other",
          "label": COIN_REASONS.get(r["r"] or "other", "Прочее"),
          "granted": int(r["plus"]), "spent": int(r["minus"])} for r in rows),
        key=lambda x: -(x["granted"] + x["spent"]))
    return {"granted": granted, "spent": spent, "by_reason": by_reason}


def stock_alert_counts():
    """{товар: сколько ждут} — админу видно, что именно стоит завезти."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT product_id, COUNT(*) AS n FROM stock_alerts GROUP BY product_id")
    out = {int(r["product_id"]): int(r["n"]) for r in cur.fetchall()}
    conn.close()
    return out


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


# ---------- Журнал действий ----------

ADMIN_LOG_KEEP = 2000     # сколько последних записей держим


def log_admin_action(admin_id, admin_name, action, details=""):
    """Записывает, кто и что изменил. Пишется молча: упавший журнал не должен
    ронять саму операцию — продавец не виноват, что мы не смогли записать."""
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute(_q("INSERT INTO admin_log (admin_id, admin_name, action, details, created_at) "
                       "VALUES (%s, %s, %s, %s, %s)"),
                    (admin_id, (admin_name or "")[:64], (action or "")[:64],
                     (details or "")[:300], _now_str()))
        # Чистим хвост: журнал не должен расти без предела на бесплатной базе.
        cur.execute(_q("DELETE FROM admin_log WHERE id <= "
                       "(SELECT MAX(id) FROM admin_log) - %s"), (ADMIN_LOG_KEEP,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Не смог записать действие в журнал: {e}")


def list_admin_log(limit=100, admin_id=None):
    conn = connect()
    cur = conn.cursor()
    if admin_id:
        cur.execute(_q("SELECT * FROM admin_log WHERE admin_id = %s ORDER BY id DESC LIMIT %s"),
                    (int(admin_id), limit))
    else:
        cur.execute(_q("SELECT * FROM admin_log ORDER BY id DESC LIMIT %s"), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


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


def receipt_owner(file_id):
    """Чей это чек об оплате (user_id) или None. Нужен, чтобы картинку чека
    видели только продавец и сам покупатель, а не любой, кому попала ссылка."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT user_id FROM orders WHERE receipt_file_id = %s LIMIT 1"), (file_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["user_id"]) if row else None


def is_shop_photo(file_id):
    """Это картинка магазина (витрина), а не чек об оплате?

    Картинки витрины стоит хранить у себя: их немного и их смотрят все
    покупатели. Чеки — наоборот, по штуке на заказ, и смотрит их один продавец
    один раз, поэтому в базу они не попадают, чтобы не забить бесплатное место.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT 1 AS x FROM products WHERE photo = %s OR photo_thumb = %s LIMIT 1"),
                (file_id, file_id))
    found = cur.fetchone() is not None
    if not found:
        # Дополнительные фото — такие же картинки товара: их тоже держим у себя,
        # иначе галерея после перезапуска качалась бы из Telegram заново.
        cur.execute(_q("SELECT 1 AS x FROM product_photos WHERE file_id = %s OR thumb_id = %s LIMIT 1"),
                    (file_id, file_id))
        found = cur.fetchone() is not None
    if not found:
        # Фото приза в розыгрыше — тоже витрина: его смотрят все.
        cur.execute(_q("SELECT 1 AS x FROM raffles WHERE photo = %s LIMIT 1"), (file_id,))
        found = cur.fetchone() is not None
    conn.close()
    return found


# Прежнее имя: функция говорила про товар, а картинки витрины бывают не только
# у товаров. Оставлено, чтобы не ломать вызовы со стороны.
is_product_photo = is_shop_photo


# ---------- Отзывы ----------

REVIEW_MAX_TEXT = 500


def reviewable_products(user_id):
    """Что этот человек может оценить: купил (заказ выдан) и ещё не оценивал.

    Право на отзыв даёт покупка, а не желание высказаться: иначе конкурент
    поставит единицу, не потратив ни рубля, а оценка товара перестанет
    что-либо значить."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT items FROM orders WHERE user_id = %s AND status = 'issued'"), (user_id,))
    bought = {}
    for r in cur.fetchall():
        try:
            for it in json.loads(r["items"]):
                pid = int(it.get("id", 0))
                if pid:
                    bought[pid] = it.get("name", "")
        except (TypeError, ValueError):
            pass
    if not bought:
        conn.close()
        return []
    marks = ",".join(["%s"] * len(bought))
    # Названия берём из живого каталога: в старом заказе товар мог называться иначе.
    cur.execute(_q(f"SELECT id, name, model_id FROM products WHERE id IN ({marks})"), tuple(bought.keys()))
    live = {r["id"]: (r["name"], r["model_id"]) for r in cur.fetchall()}
    cur.execute(_q("SELECT product_id, model_id FROM reviews WHERE user_id = %s"), (user_id,))
    rated_products, rated_models = set(), set()
    for r in cur.fetchall():
        rated_products.add(r["product_id"])
        if r["model_id"]:
            rated_models.add(r["model_id"])
    conn.close()

    out, seen_models = [], set()
    for pid in bought:
        if pid not in live:
            continue
        name, mid = live[pid]
        # Оценивают модель: уже оценил её на другой точке — второй раз не предлагаем.
        # И один и тот же товар с двух точек не показываем дважды в одном списке.
        if mid and (mid in rated_models or mid in seen_models):
            continue
        if not mid and pid in rated_products:
            continue
        if mid:
            seen_models.add(mid)
        out.append({"id": pid, "name": name or bought[pid]})
    return out


def _model_of(cur, product_id):
    """Модель товара или None (товар заведён до «Ассортимента»)."""
    cur.execute(_q("SELECT model_id FROM products WHERE id = %s"), (product_id,))
    row = cur.fetchone()
    return row["model_id"] if row else None


def add_review(product_id, user_id, rating, text="", username=""):
    """Сохраняет отзыв в статусе «на модерации». Возвращает id или None, если уже оценивал."""
    conn = connect()
    cur = conn.cursor()
    mid = _model_of(cur, product_id)
    # Один человек — один отзыв на модель. Иначе один и тот же покупатель
    # оценил бы её отдельно в Минске и отдельно в Турове.
    if mid:
        cur.execute(_q("SELECT 1 AS x FROM reviews WHERE user_id = %s AND model_id = %s LIMIT 1"),
                    (user_id, mid))
    else:
        cur.execute(_q("SELECT 1 AS x FROM reviews WHERE user_id = %s AND product_id = %s LIMIT 1"),
                    (user_id, product_id))
    if cur.fetchone():
        conn.close()
        return None
    rid = _insert_id(cur, "INSERT INTO reviews (product_id, model_id, user_id, username, rating, text, status, created_at) "
                          "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)",
                     (product_id, mid, user_id, (username or "")[:64], int(rating),
                      (text or "").strip()[:REVIEW_MAX_TEXT], _now_str()))
    conn.commit()
    conn.close()
    return rid


def list_reviews(product_id, status="approved", limit=50):
    """Отзывы о модели этого товара: на всех точках это одна и та же вещь.

    Для товара без модели — как раньше, по самому товару."""
    conn = connect()
    cur = conn.cursor()
    mid = _model_of(cur, product_id)
    if mid:
        cur.execute(_q("SELECT * FROM reviews WHERE model_id = %s AND status = %s ORDER BY id DESC LIMIT %s"),
                    (mid, status, limit))
    else:
        cur.execute(_q("SELECT * FROM reviews WHERE product_id = %s AND model_id IS NULL "
                       "AND status = %s ORDER BY id DESC LIMIT %s"),
                    (product_id, status, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def list_reviews_by_user(user_id, limit=50):
    """Отзывы одного человека — чтобы показать ему его же оценку и её судьбу."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM reviews WHERE user_id = %s ORDER BY id DESC LIMIT %s"), (user_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def admin_reviews(status="pending", limit=100):
    """Отзывы для админа. status='all' — все, включая опубликованные и скрытые.

    Раньше админ видел только очередь на модерацию: опубликованный отзыв
    исчезал из его поля зрения навсегда, и убрать его было уже нельзя."""
    conn = connect()
    cur = conn.cursor()
    # Имя берём из модели, а не из товара: товар могли снять с точки, и тогда
    # отзыв в очереди оказывался безымянным — модерировать вслепую нельзя.
    sql = ("SELECT r.*, COALESCE(m.name, p.name) AS product_name FROM reviews r "
           "LEFT JOIN products p ON p.id = r.product_id "
           "LEFT JOIN models m ON m.id = r.model_id ")
    if status and status != "all":
        cur.execute(_q(sql + "WHERE r.status = %s ORDER BY r.id DESC LIMIT %s"), (status, limit))
    else:
        cur.execute(_q(sql + "ORDER BY r.id DESC LIMIT %s"), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def pending_reviews(limit=50):
    """Ждут решения — их видит админ."""
    return admin_reviews("pending", limit)


def delete_review(review_id):
    """Убирает отзыв насовсем. «Скрыть» оставляет запись (можно вернуть),
    удаление — для мусора, который держать незачем."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM reviews WHERE id = %s"), (review_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def set_review_reply(review_id, text):
    """Ответ магазина на отзыв. Пустой текст убирает ответ."""
    text = (text or "").strip()[:REVIEW_MAX_TEXT]
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE reviews SET reply = %s, replied_at = %s WHERE id = %s"),
                (text or None, (_now_str() if text else None), review_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def count_pending_reviews():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM reviews WHERE status = 'pending'")
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def set_review_status(review_id, status):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE reviews SET status = %s WHERE id = %s"), (status, review_id))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_review(review_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM reviews WHERE id = %s"), (review_id,))
    row = cur.fetchone()
    conn.close()
    return row


def also_bought(top=5, scan=500, min_count=2):
    """{товар: [товары, которые брали вместе с ним]} — по реальным выданным заказам.

    Считаем только пары, встретившиеся не меньше min_count раз: единственная
    совместная покупка — это совпадение, а не закономерность, и советовать по
    ней значит выдавать шум за рекомендацию.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT items FROM orders WHERE status = 'issued' ORDER BY id DESC LIMIT %s"), (scan,))
    pairs = {}
    for r in cur.fetchall():
        try:
            ids = {int(it.get("id", 0)) for it in json.loads(r["items"]) if it.get("id")}
        except (TypeError, ValueError):
            continue
        for a in ids:
            for b in ids:
                if a != b:
                    pairs.setdefault(a, {})
                    pairs[a][b] = pairs[a].get(b, 0) + 1
    conn.close()
    out = {}
    for a, others in pairs.items():
        best = [pid for pid, n in sorted(others.items(), key=lambda x: -x[1]) if n >= min_count][:top]
        if best:
            out[a] = best
    return out


def product_ratings():
    """{товар: {avg, count}} по опубликованным отзывам — одним проходом на всю витрину.

    Оценка принадлежит модели, поэтому одна и та же цифра стоит у товара на
    каждой точке. Раньше витрина Турова показывала «нет отзывов» у модели,
    которую в Минске оценили дюжину раз."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT product_id, model_id, rating FROM reviews WHERE status = 'approved'")
    by_model, by_product = {}, {}
    for r in cur.fetchall():
        bucket = by_model.setdefault(r["model_id"], []) if r["model_id"] \
            else by_product.setdefault(r["product_id"], [])
        bucket.append(int(r["rating"]))
    cur.execute("SELECT id, model_id FROM products")
    products = [(r["id"], r["model_id"]) for r in cur.fetchall()]
    conn.close()

    out = {}
    for pid, mid in products:
        marks = by_model.get(mid) if mid else by_product.get(pid)
        if marks:
            out[pid] = {"count": len(marks), "avg": round(sum(marks) / len(marks), 1)}
    return out


# ---------- Галерея товара ----------

MAX_EXTRA_PHOTOS = 5      # плюс главное фото — шесть картинок на карточку


def model_photos(model_id):
    """Галерея модели. Фото — свойство самого товара, а не точки: на всех
    точках это одна и та же коробка."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM product_photos WHERE model_id = %s ORDER BY sort, id"), (model_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def all_model_photos():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_photos WHERE model_id IS NOT NULL ORDER BY model_id, sort, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_model_photo(model_id, file_id, thumb_id=""):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c, COALESCE(MAX(sort), 0) AS mx FROM product_photos WHERE model_id = %s"),
                (model_id,))
    row = cur.fetchone()
    if int(row["c"]) >= MAX_EXTRA_PHOTOS:
        conn.close()
        return None
    pid = _insert_id(cur, "INSERT INTO product_photos (product_id, model_id, file_id, thumb_id, sort) "
                          "VALUES (%s, %s, %s, %s, %s)",
                     (0, model_id, file_id, thumb_id or "", int(row["mx"]) + 1))
    conn.commit()
    conn.close()
    return pid


def get_product_photos(product_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM product_photos WHERE product_id = %s ORDER BY sort, id"), (product_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def all_product_photos():
    """Все дополнительные фото разом — витрине нужен один поход в базу, а не по одному на товар."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_photos ORDER BY product_id, sort, id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_product_photo(product_id, file_id, thumb_id=""):
    """Добавляет фото в галерею. Возвращает id записи или None, если места больше нет.

    Ограничение — не формальность: каждая картинка едет покупателю по мобильному
    интернету, и десяток фото превращает карточку в долгую загрузку."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c, COALESCE(MAX(sort), 0) AS mx FROM product_photos WHERE product_id = %s"),
                (product_id,))
    row = cur.fetchone()
    if int(row["c"]) >= MAX_EXTRA_PHOTOS:
        conn.close()
        return None
    pid = _insert_id(cur, "INSERT INTO product_photos (product_id, file_id, thumb_id, sort) VALUES (%s, %s, %s, %s)",
                     (product_id, file_id, thumb_id or "", int(row["mx"]) + 1))
    conn.commit()
    conn.close()
    return pid


def delete_product_photo(photo_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM product_photos WHERE id = %s"), (photo_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# Летопись монет нужна для отчёта «роздано за период», а не навсегда: держим
# с запасом больше года, чтобы сравнение «этот август против прошлого» работало.
COIN_LOG_KEEP_DAYS = 400


def trim_coin_log(days=COIN_LOG_KEEP_DAYS):
    """Убирает движения монет старше срока. Возвращает, сколько убрано."""
    cutoff = (shop_now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM coin_log WHERE created_at < %s"), (cutoff,))
    gone = cur.rowcount
    conn.commit()
    conn.close()
    return max(0, gone)


def purge_orphan_photos(limit=200):
    """Убирает картинки, на которые больше никто не ссылается.

    Товар снимают с точки — строки о нём уходят, а картинка оставалась в базе
    навсегда. Место на бесплатной базе кончается тихо, и заметить это можно
    было бы только когда магазин перестанет принимать заказы.

    Ошибиться тут почти нечем: file_id в Telegram остаётся рабочим, и удалённая
    по недосмотру картинка просто скачается заново при первом показе. Поэтому
    достаточно одного условия — на неё никто не ссылается.

    Разом убираем не больше limit штук: ночная уборка не должна держать базу.
    Возвращает, сколько убрано.
    """
    # Сутки форы: картинка появляется в базе следом за товаром, и уборка не
    # должна успеть между этими двумя действиями.
    cutoff = (shop_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    # NOT IN и NULL несовместимы: один NULL в списке — и условие не выполнится
    # НИ ДЛЯ ОДНОЙ строки, уборка молча перестанет работать. Отсюда IS NOT NULL.
    cur.execute(_q("""
        DELETE FROM photo_blobs WHERE file_id IN (
            SELECT file_id FROM photo_blobs
             WHERE (created_at IS NULL OR created_at < %s)
               AND file_id NOT IN (SELECT photo FROM products WHERE photo IS NOT NULL)
               AND file_id NOT IN (SELECT photo_thumb FROM products WHERE photo_thumb IS NOT NULL)
               AND file_id NOT IN (SELECT file_id FROM product_photos WHERE file_id IS NOT NULL)
               AND file_id NOT IN (SELECT thumb_id FROM product_photos WHERE thumb_id IS NOT NULL)
               AND file_id NOT IN (SELECT photo FROM raffles WHERE photo IS NOT NULL)
             LIMIT %s)
    """), (cutoff, limit))
    gone = cur.rowcount
    conn.commit()
    conn.close()
    return max(0, gone)


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
                brand="", flavor="", strength="", volume="", cost=0):
    conn = connect()
    cur = conn.cursor()
    new_id = _insert_id(
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


# Какие колонки разрешено менять (защита: имя колонки нельзя подставить параметром).
_EDITABLE = {"name", "price", "cost", "stock", "is_hit", "description", "photo", "photo_thumb",
             "brand", "flavor", "strength", "volume", "category", "city", "hidden"}


def hide_model_products(model_id, hidden):
    """Снять модель с витрины сразу на всех точках (или вернуть). Возвращает,
    скольких товаров коснулось: продавцу важно понимать масштаб действия."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET hidden = %s WHERE model_id = %s"),
                (1 if hidden else 0, model_id))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


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
    # Галерея без товара никому не видна, но место занимает и мешает считать
    # картинки — убираем вместе с товаром.
    cur.execute(_q("DELETE FROM product_photos WHERE product_id = %s"), (product_id,))
    # Отзывы о модели переживают снятие с точки: человек оценивал вещь, а не
    # факт её наличия в Турове. Раньше товар уносил с собой чужие слова —
    # вернул модель на точку через месяц, а отзывов уже нет.
    cur.execute(_q("DELETE FROM reviews WHERE product_id = %s AND model_id IS NULL"), (product_id,))
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
    created_at = shop_now().strftime("%Y-%m-%d %H:%M")
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
    points = []
    if method_id is not None:
        cur.execute(_q("SELECT * FROM delivery_methods WHERE id = %s"), (method_id,))
        row = cur.fetchone()
        method = dict(row) if row else None
        # Точки самовывоза берём ТУТ ЖЕ: отдельный запрос на оформлении — это
        # ещё одно подключение к базе на самом горячем пути.
        if method and not method["needs_address"]:
            cur.execute(_q("SELECT * FROM pickup_points WHERE city = %s ORDER BY sort, id"),
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


def _reserve_promo(cur, code, user_id):
    """Занять одно применение промокода. Вызывается ВНУТРИ транзакции заказа.

    Сначала берём блокировку на строку кода: на Postgres — SELECT ... FOR UPDATE,
    на SQLite её роль играет запись (она переводит транзакцию в режим writer, и
    вторая ждёт). Без блокировки два одновременных заказа оба видят «код ещё не
    использован» и оба его применяют.
    """
    code = (code if isinstance(code, str) else "").strip().upper()
    if not code:
        return
    if USE_PG:
        cur.execute("SELECT * FROM promos WHERE code = %s FOR UPDATE", (code,))
    else:
        cur.execute("UPDATE promos SET code = code WHERE code = ?", (code,))
        cur.execute("SELECT * FROM promos WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row or not row["active"]:
        raise PromoGone("promo_unknown")
    if row["once_per_user"]:
        cur.execute(_q("SELECT COUNT(*) AS c FROM orders WHERE user_id = %s AND promo_code = %s "
                       "AND status != 'canceled'"), (user_id, code))
        if cur.fetchone()["c"]:
            raise PromoGone("promo_once")
    if row["uses_left"] is not None:
        cur.execute(_q("UPDATE promos SET uses_left = uses_left - 1 "
                       "WHERE code = %s AND uses_left > 0"), (code,))
        if cur.rowcount < 1:
            raise PromoGone("promo_used_up")


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
    conn = connect()
    cur = conn.cursor()
    if hours is None:
        cur.execute(_q("SELECT * FROM orders WHERE user_id = %s AND client_token = %s "
                       "ORDER BY id DESC LIMIT 1"), (user_id, token))
    else:
        cutoff = (shop_now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        cur.execute(_q("SELECT * FROM orders WHERE user_id = %s AND client_token = %s "
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

    created_at = shop_now().strftime("%Y-%m-%d %H:%M")
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
        promo_off = round(float(promo_discount or 0), 2)
        # Промокод занимаем здесь же, одной транзакцией с заказом: иначе между
        # проверкой и списанием успевает пройти чужой заказ.
        if promo_code and promo_off > 0:
            _reserve_promo(cur, promo_code, user_id)
        # Итог не может уйти в минус: скидка монетами плюс промокод могут
        # перекрыть стоимость товаров, но доставку покупатель платит всё равно.
        total = round(max(0.0, subtotal - discount - promo_off) + fee, 2)

        # 2. Сам заказ — сразу со всеми полями (без последующих UPDATE).
        order_id = _insert_id(
            cur,
            """INSERT INTO orders (user_id, username, city, items, total, pickup_time, status,
                                   created_at, coins_used, delivery_method, delivery_address,
                                   delivery_fee, payment_method, comment, phone,
                                   promo_code, promo_discount, client_token)
               VALUES (%s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, username, city, json.dumps(items, ensure_ascii=False), total, status,
             created_at, coins_used, method_name, address, float(fee or 0), payment,
             (comment or "").strip()[:500], (phone or "").strip()[:40],
             (promo_code or "").strip().upper() or None, promo_off,
             (client_token or "").strip() or None),
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
                cur.execute(_q("UPDATE product_variants SET stock = stock - %s "
                               "WHERE product_id = %s AND flavor = %s AND stock >= %s"),
                            (it["qty"], it["id"], it["flavor"], it["qty"]))
                touched_variants.add(it["id"])
            else:
                cur.execute(_q("UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s"),
                            (it["qty"], it["id"], it["qty"]))
            if cur.rowcount < 1:
                raise OutOfStock(it.get("name") or "товар")
        # общий остаток товара-модели = сумма остатков вкусов
        for pid in touched_variants:
            cur.execute(_q("""UPDATE products SET stock =
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
        log_coins(user_id, -coins_used, "order")
    return order_id, coins_used, total, False


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


def seller_today(city=None):
    """Сводка дня: что ждёт продавца прямо сейчас и чем закончился день.

    Раньше на эти четыре числа уходило два экрана: заказы открой и посчитай,
    остаток посмотри в товарах, деньги — в статистике за месяц.

    «Сегодня» считается по дате создания заказа — так же, как в «Статистике»:
    два экрана с одинаковой подписью и разными числами хуже, чем небольшая
    неточность в редком случае «заказали вчера, забрали сегодня».
    """
    today = shop_now().strftime("%Y-%m-%d")
    where_city = " AND city = %s" if city else ""
    args_city = (city,) if city else ()
    conn = connect()
    cur = conn.cursor()

    cur.execute(_q(f"SELECT status, COUNT(*) AS c FROM orders "
                   f"WHERE status IN ('new', 'paid', 'confirmed'){where_city} GROUP BY status"),
                args_city)
    open_by = {r["status"]: int(r["c"]) for r in cur.fetchall()}

    cur.execute(_q(f"SELECT COUNT(*) AS c, COALESCE(SUM(total), 0) AS s FROM orders "
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
        add_coins(order["user_id"], order["coins_used"], "refund")
    return order


ORDER_EDITABLE = ("new", "paid", "confirmed")     # до выдачи заказ ещё можно поправить


def update_order_items(order_id, quantities, coin_value):
    """Продавец меняет количества в заказе. Возвращает (order, changes) или (None, ошибка).

    Раньше у продавца было три кнопки: подтвердить, выдать, отклонить. Клиент
    просит «одну вместо двух» или «добавьте ещё» — и единственным ходом было
    отклонить заказ целиком и просить оформить заново, потеряв и заказ, и время.

    Считается одной транзакцией, как и оформление: остаток и сумма не должны
    разъехаться, если что-то упадёт посередине.
    """
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(_q("SELECT * FROM orders WHERE id = %s"), (order_id,))
        o = cur.fetchone()
        if not o:
            return None, "not_found"
        if o["status"] not in ORDER_EDITABLE:
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
                    cur.execute(_q("SELECT stock FROM product_variants WHERE product_id = %s AND flavor = %s"),
                                (pid, flavor))
                else:
                    cur.execute(_q("SELECT stock FROM products WHERE id = %s"), (pid,))
                row = cur.fetchone()
                have = int(row["stock"]) if row else 0
                if have < delta:
                    return None, f"no_stock:{it.get('name', '')}:{have}"
            if flavor:
                cur.execute(_q(f"UPDATE product_variants SET stock = {GREATEST}(0, stock - %s) "
                               "WHERE product_id = %s AND flavor = %s"), (delta, pid, flavor))
                cur.execute(_q("""UPDATE products SET stock =
                                  (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                                  WHERE id = %s"""), (pid, pid))
            else:
                cur.execute(_q(f"UPDATE products SET stock = {GREATEST}(0, stock - %s) WHERE id = %s"),
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

        cur.execute(_q("UPDATE orders SET items = %s, total = %s, promo_discount = %s WHERE id = %s"),
                    (json.dumps(items, ensure_ascii=False), total, promo_off, order_id))
        conn.commit()
        cur.execute(_q("SELECT * FROM orders WHERE id = %s"), (order_id,))
        return cur.fetchone(), changes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stale_new_orders(hours=24):
    """Карточные заказы, застрявшие в 'new' (чек не загружен) дольше `hours` — на авто-отмену."""
    cutoff = (shop_now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM orders WHERE status = 'new' AND created_at <= %s"), (cutoff,))
    rows = cur.fetchall()
    conn.close()
    return rows


def touch_order_reminded(order_id):
    """Отмечает, что по заказу только что отправлено уведомление/напоминание продавцу."""
    now = shop_now().strftime("%Y-%m-%d %H:%M")
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET reminded_at = %s WHERE id = %s"), (now, order_id))
    conn.commit()
    conn.close()


def orders_needing_reminder(minutes=10):
    """Заказы, ждущие ОДОБРЕНИЯ продавца (status='paid'), по которым напоминание
    не отправлялось дольше `minutes`. Напоминаем до одобрения (потом продавец сам ведёт заказ)."""
    cutoff = (shop_now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
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
    """Бренды. category — фильтр «для этой категории»: бренд с пустой категорией
    общий (Vaporesso делает и поды, и картриджи) и попадает в любой список."""
    conn = connect()
    cur = conn.cursor()
    if category:
        cur.execute(_q("SELECT * FROM brands WHERE category = %s OR category IS NULL OR category = '' ORDER BY name"),
                    (category,))
    else:
        cur.execute("SELECT * FROM brands ORDER BY name")
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


def find_brand_by_name(name, except_id=None):
    """Бренд с таким именем (без учёта регистра). Нужен, чтобы не плодить дубли:
    «Vaporesso» и «vaporesso» в фильтре выглядят как два разных бренда."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM brands WHERE LOWER(name) = %s"), ((name or "").strip().lower(),))
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        if except_id is None or int(r["id"]) != int(except_id):
            return r
    return None


def count_products_of_brand(name):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM products WHERE brand = %s"), ((name or "").strip(),))
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET brand = %s WHERE brand = %s"), (new_name, old_name))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def known_flavors(limit=200):
    """Все вкусы, которые уже встречались: в брендах, в вариантах и у товаров.

    Нужны для подсказок при вводе — иначе «Мята», «мята» и «Мята ❄️» живут
    в базе как три разных вкуса, и фильтр по вкусу разваливается."""
    conn = connect()
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
    conn = connect()
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
        cur.execute(_q("UPDATE brands SET flavors = %s, category = %s WHERE id = %s"),
                    (json.dumps(flavors, ensure_ascii=False), "", keep["id"]))
        for r in rows[1:]:
            cur.execute(_q("DELETE FROM brands WHERE id = %s"), (r["id"],))
            merged += 1
    conn.commit()
    conn.close()
    return merged


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


# ---------- Ассортимент: модели товаров ----------

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
    conn = connect()
    cur = conn.cursor()
    if category:
        cur.execute(_q("SELECT * FROM models WHERE category = %s ORDER BY brand, name"), (category,))
    else:
        cur.execute("SELECT * FROM models ORDER BY category, brand, name")
    rows = [_model_json(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_model(model_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM models WHERE id = %s"), (model_id,))
    row = cur.fetchone()
    conn.close()
    return _model_json(row) if row else None


def add_model(category, name, brand="", description="", specs=None, flavors=None):
    conn = connect()
    cur = conn.cursor()
    mid = _insert_id(cur, "INSERT INTO models (category, brand, name, description, specs, flavors, created_at) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (category, (brand or "").strip(), (name or "").strip(), (description or "").strip(),
                      json.dumps(specs or {}, ensure_ascii=False),
                      json.dumps(flavors or [], ensure_ascii=False), _now_str()))
    conn.commit()
    conn.close()
    return mid


def update_model(model_id, category=None, name=None, brand=None, description=None, specs=None, flavors=None):
    """Правит модель и переносит изменения на все её товары.

    Ради этого модель и заводится: описание живёт в одном месте, а не в трёх
    копиях по точкам, которые расходятся при первой же правке."""
    conn = connect()
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
        cur.execute(_q(f"UPDATE models SET {', '.join(sets)} WHERE id = %s"), (*params, model_id))
    conn.commit()
    conn.close()
    return propagate_model(model_id)


def set_model_photo(model_id, file_id, thumb_id=""):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE models SET photo = %s, photo_thumb = %s WHERE id = %s"),
                (file_id, thumb_id or "", model_id))
    conn.commit()
    conn.close()
    return propagate_model(model_id)


def propagate_model(model_id):
    """Разносит описание модели по её товарам на точках. Цену, закупку и остаток
    не трогает — это как раз то, что у каждой точки своё."""
    m = get_model(model_id)
    if not m:
        return 0
    specs = {k: v for k, v in (m["specs"] or {}).items() if k not in SPEC_COLUMNS}
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET category = %s, brand = %s, name = %s, description = %s, specs = %s "
                   "WHERE model_id = %s"),
                (m["category"], m["brand"], m["name"], m["description"],
                 json.dumps(specs, ensure_ascii=False) if specs else None, model_id))
    n = cur.rowcount
    for col in SPEC_COLUMNS:
        cur.execute(_q(f"UPDATE products SET {col} = %s WHERE model_id = %s"),
                    (str((m["specs"] or {}).get(col, "") or ""), model_id))
    if m["photo"]:
        cur.execute(_q("UPDATE products SET photo = %s, photo_thumb = %s WHERE model_id = %s"),
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
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT v.flavor AS flavor, SUM(v.stock) AS stock FROM product_variants v "
                   "JOIN products p ON p.id = v.product_id WHERE p.model_id = %s "
                   "GROUP BY v.flavor"), (model_id,))
    out = [{"flavor": r["flavor"], "stock": int(r["stock"] or 0)}
           for r in cur.fetchall() if r["flavor"].strip().lower() not in known]
    conn.close()
    return out


def count_products_of_model(model_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) AS c FROM products WHERE model_id = %s"), (model_id,))
    n = int(cur.fetchone()["c"])
    conn.close()
    return n


def delete_model(model_id):
    """Убирает модель из ассортимента. Товары на точках остаются — их снимают
    с продажи отдельно, иначе одно нажатие стирало бы остатки всех точек."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET model_id = NULL WHERE model_id = %s"), (model_id,))
    cur.execute(_q("DELETE FROM models WHERE id = %s"), (model_id,))
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
    extra = {k: v for k, v in specs.items() if k not in SPEC_COLUMNS and str(v).strip() != ""}
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE products SET model_id = %s, specs = %s, photo = %s, photo_thumb = %s WHERE id = %s"),
                (model_id, json.dumps(extra, ensure_ascii=False) if extra else None,
                 m["photo"] or None, m["photo_thumb"] or None, pid))
    conn.commit()
    conn.close()
    return pid


def models_seeded_from_products():
    """Разовый перенос: из существующих товаров собираем модели.

    Один и тот же товар мог быть заведён на нескольких точках — это одна
    модель, поэтому группируем по «категория + бренд + название»."""
    if get_setting("models_seeded"):
        return 0
    made = 0
    groups = {}
    for p in get_all_products():
        key = (p["category"], (p["brand"] or "").strip(), p["name"].strip())
        groups.setdefault(key, []).append(p)
    for (category, brand, name), rows in groups.items():
        first = rows[0]
        mid = add_model(category, name, brand, first["description"] or "", product_specs(first), [])
        if first["photo"]:
            set_model_photo(mid, first["photo"], first["photo_thumb"] or "")
        conn = connect()
        cur = conn.cursor()
        for p in rows:
            cur.execute(_q("UPDATE products SET model_id = %s WHERE id = %s"), (mid, p["id"]))
        conn.commit()
        conn.close()
        made += 1
    set_setting("models_seeded", "1")
    return made


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


# --- Розыгрыши ---
# Сам код — в db_raffles.py, здесь только имена, чтобы весь магазин по-прежнему
# звал db.get_active_raffle() и не знал о переезде.
#
# Импорт внизу файла намеренно: db_raffles обращается к примитивам через db, и к
# этому моменту они уже определены. F401 подавлен осознанно — это переэкспорт,
# имена нужны не здесь, а тем, кто зовёт их через db.
# --- Игры ---
# Колесо и слот — в db_games.py. Здесь только имена: магазин зовёт их через db.
from db_games import (                                          # noqa: E402
    WHEEL_STEP_DEFAULT, WHEEL_ITEMS_STEP_OLD,                               # noqa: F401
    _migrate_wheel_progress_to_money, wheel_step, get_wheel,                # noqa: F401
    add_wheel_progress, add_spins, use_spin, do_wheel_spin,                 # noqa: F401
    do_slot_spin, get_game_stats,                                           # noqa: F401
)

from db_raffles import (                                        # noqa: E402
    _RAFFLE_EDITABLE, _ensure_raffle_columns, _ensure_raffle_uniques,       # noqa: F401
    get_active_raffle, get_last_finished_raffle, recent_finished_raffle,    # noqa: F401
    create_raffle, update_raffle_field, claim_raffle_draw,                  # noqa: F401
    set_raffle_winners, finish_raffle, add_raffle_entry, is_entered,        # noqa: F401
    count_entries, get_raffle_user_ids, spent_since, get_raffle_state,      # noqa: F401
)
