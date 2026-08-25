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
import threading

from partut import config

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)           # True = Postgres, False = локальный SQLite

if USE_PG:
    # psycopg2 нужен и вынесенным модулям — они берут его как db.psycopg2,
    # чтобы драйвер в магазине был выбран один раз и в одном месте.
    import psycopg2                          # noqa: F401
    from psycopg2 import pool as _pgpool
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3
    SQLITE_FILE = "shop.db"

# Диалектные различия, которые встречаются в наших запросах:
ID_COL = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
# Деньги. Писать REAL нельзя: в Postgres это ОДИНАРНАЯ точность, четыре байта,
# около семи значащих цифр. Отдельная цена такое переживает, а вот суммы — нет:
# SUM(real) в Postgres возвращает тоже real, и выручка копится в четырёх байтах.
# Замерено на 3000 заказов: расхождение 8 копеек, и растёт с оборотом. То есть
# врали не чеки, а отчёты — там, где ошибку труднее всего заметить.
# В SQLite REAL и так восемь байт, DOUBLE PRECISION он принимает и понимает
# так же — поэтому тип один на обе базы, без развилки.
MONEY = "DOUBLE PRECISION"
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


_выданные = threading.local()


def _набор():
    """Соединения, выданные ЭТОМУ потоку и ещё не закрытые."""
    s = getattr(_выданные, "s", None)
    if s is None:
        s = _выданные.s = set()
    return s


def вернуть_забытые():
    """Подобрать всё, что осталось открытым к концу единицы работы.

    Двести функций базы написаны как «взял, поработал, закрыл», и close() у них
    стоит на счастливом пути. Стоит запросу упасть — а Neon рвёт соединения
    регулярно, — и соединение не вернётся в пул НИКОГДА.

    Двадцать таких падений (столько в пуле), и магазин мёртв: на всё подряд
    пятисотки, а /health при этом бодро говорит «ok», потому что базы не
    касается. Значит хостинг даже не перезапустит сервис — лечится только
    руками, и никто не понимает почему.

    Поэтому здесь сеть под канатоходцем: в конце запроса и в конце фоновой
    задачи всё забытое возвращается в пул само. Правильное место для close()
    это не отменяет — но потерять соединение больше нельзя.
    """
    забыты = list(_набор())
    for c in забыты:
        try:
            c.rollback()
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
    return len(забыты)


class _Conn:
    """Соединение с учётом: и взятое из пула Postgres, и файловое SQLite.

    Наружу торчат ровно те четыре метода, которыми пользуется код базы
    (cursor/commit/rollback/close), поэтому обёртка невидима для вызывающих —
    двести функций не пришлось трогать.
    """

    def __init__(self, raw, из_пула):
        self._raw, self._из_пула, self._закрыт = raw, из_пула, False
        _набор().add(self)

    def cursor(self, *a, **k):
        return self._raw.cursor(*a, **k)

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        """У SQLite — закрыть по-настоящему, у Postgres — вернуть в пул."""
        if self._закрыт:
            return                       # повторный close() не должен вредить
        self._закрыт = True
        _набор().discard(self)
        if not self._из_пула:
            try:
                self._raw.close()
            except Exception:
                pass
            return
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

    # Чтобы новый код можно было писать сразу правильно: with db.connect() as conn.
    def __enter__(self):
        return self

    def __exit__(self, *_беда):
        self.close()
        return False


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
        return _Conn(raw, из_пула=True)
    conn = sqlite3.connect(SQLITE_FILE)
    conn.row_factory = sqlite3.Row        # доступ к колонкам по имени: row["name"]
    return _Conn(conn, из_пула=False)


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


# --- Летопись изменений схемы -------------------------------------------
#
# Схема правится только добавлением: тридцать ADD COLUMN и ни одного DROP или
# RENAME. Поэтому откат кода назад сегодня безопасен — старый код просто не
# смотрит на новые колонки. Но это держится на дисциплине, а не на устройстве:
# первая же разрушительная правка, написанная тем же способом, сломает откат
# молча. Сторожит это tests/test_schema.py.
#
# Чего действительно не хватало — памяти. Разовые переносы данных заводили себе
# по отметке в настройках, каждый свою, и вопрос «а на какой схеме эта база»
# ответа не имел вовсе: ни у боевой, ни у поднятой из копии.
#
# Теперь ответ есть: таблица schema_migrations. Порядок шагов — порядок их
# вызова в коде, а этот список нужен, чтобы шаги нельзя было завести втихую.
SCHEMA_MIGRATIONS = [
    # (имя в летописи, старая отметка в настройках)
    ("0001-модели-собраны-из-товаров", "models_seeded"),
    ("0002-прогресс-колеса-в-рублях", "wheel_progress_in_money"),
    ("0003-история-по-времени-магазина", "history_shifted_to_shop_time"),
    ("0004-админы-из-окружения-в-базу", "staff_seeded"),
    ("0005-деньги-в-двойную-точность", None),
    ("0006-адрес-самовывоза-стал-точкой", None),
]


_НЕ_ЗАВЕДЁН = object()      # «переноса нет в списке» — не то же, что «нет старой отметки»


def _migration_table():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                       name       TEXT PRIMARY KEY,
                       applied_at TEXT NOT NULL)""")
    conn.commit()
    conn.close()


def _migrate(имя, шаг):
    """Прогоняет разовый перенос один раз за всю жизнь базы.

    Заявку подаём ДО работы, а не после: два процесса поднимаются одновременно
    (хостинг деплоит новую версию, старая ещё жива), и «проверить, потом
    сделать» означало бы удвоенный прогресс у покупателей. Кто вставил строку —
    тот и работает; остальные проходят мимо.

    Если шаг упал, заявку снимаем: иначе перенос считался бы сделанным, а
    данные остались бы наполовину старыми — и это никогда бы не всплыло.
    """
    # Отличаем «переноса нет в списке» от «у переноса нет старой отметки».
    # Раньше и то и другое было None, и первый же перенос, заведённый уже при
    # летописи (а не до неё), ронял магазин при старте: список его знал, а
    # проверка считала незарегистрированным.
    старая_метка = dict(SCHEMA_MIGRATIONS).get(имя, _НЕ_ЗАВЕДЁН)
    if старая_метка is _НЕ_ЗАВЕДЁН:
        raise RuntimeError(f"перенос {имя} не записан в SCHEMA_MIGRATIONS")

    _migration_table()
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT name FROM schema_migrations WHERE name = %s"), (имя,))
    if cur.fetchone():
        conn.close()
        return False

    # База, поднятая до летописи, носит старую отметку в настройках. Прогнать
    # шаг второй раз местами означало бы удвоить накопленное людям. У переносов,
    # заведённых уже при летописи, старой отметки нет — и искать её незачем.
    уже = bool(старая_метка) and bool(get_setting(старая_метка))

    try:
        cur.execute(_q("INSERT INTO schema_migrations (name, applied_at) VALUES (%s, %s)"),
                    (имя, _now_str()))
        conn.commit()
    except Exception:
        conn.close()
        return False        # успел другой процесс
    conn.close()

    if уже:
        return False        # уже сделано когда-то, только записали в летопись
    try:
        шаг()
    except Exception:
        conn = connect(); cur = conn.cursor()
        cur.execute(_q("DELETE FROM schema_migrations WHERE name = %s"), (имя,))
        conn.commit(); conn.close()
        raise
    return True


def schema_version():
    """На какой схеме эта база: что применено и когда.

    Нужно ровно в тот момент, когда думать некогда: магазин ведёт себя странно,
    или копию только что развернули в пустую базу и надо понять, доехало ли всё.
    """
    _migration_table()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name, applied_at FROM schema_migrations ORDER BY name")
    строки = [dict(r) for r in cur.fetchall()]
    conn.close()
    все = [и for и, _ in SCHEMA_MIGRATIONS]
    return {"применено": строки,
            "последняя": строки[-1]["name"] if строки else "",
            "ждут": [и for и in все if и not in {r["name"] for r in строки}]}


def _seed_admins_from_env():
    """Одноразово переносит админов из переменных окружения в базу.

    После переноса база — единственный источник прав, поэтому любого админа
    можно убрать прямо из приложения. Раньше записи из окружения удалить было
    нельзя вовсе: сервер читает их при запуске, и приложение до них не достаёт.

    Что перенос уже был — помнит летопись схемы, а НЕ проверка «таблица
    пуста»: иначе стоит владельцу убрать всех продавцов, как при следующем
    перезапуске они бы вернулись из окружения.

    Падение пробрасываем наверх: летопись снимет заявку, и на следующем запуске
    перенос повторится. Съесть ошибку здесь означало бы записать перенос
    сделанным, не сделав его, — и продавцы не появились бы уже никогда.

    Города при переносе переводим из кодов в названия: заказы носят название
    («Минск»), а переменные заданы кодом (ADMIN_MINSK), и раньше они не
    совпадали — продавцы городов из окружения не получали заказы никогда."""
    rows = [(uid, "") for uid in config.ADMIN_IDS]
    for code, ids in config.CITY_ADMINS.items():
        rows += [(uid, config.CITIES.get(code, code)) for uid in ids]
    for uid, city in rows:
        if uid in config.SUPER_ADMIN_IDS and not city:
            continue                      # владелец и так админ всегда
        add_staff(uid, city, "перенесён из настроек сервера", None)
    config.refresh_staff()
    if rows:
        print(f"Админы перенесены из настроек сервера в базу: {len(rows)}")


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
            price       {MONEY}    NOT NULL,
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
            total           {MONEY}    NOT NULL,
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
            threshold    {MONEY}    NOT NULL DEFAULT 25,
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
            fee            {MONEY}    NOT NULL DEFAULT 0,
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
            cost       {MONEY}    NOT NULL DEFAULT 0,
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
            value      {MONEY}    NOT NULL DEFAULT 0,
            min_total  {MONEY}    NOT NULL DEFAULT 0,
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
    _ensure_category_columns()  # has_flavors у категорий
    _ensure_photo_columns()     # галерея у модели, а не у товара
    _migrate("0001-модели-собраны-из-товаров", models_seeded_from_products)
    _migrate("0005-деньги-в-двойную-точность", _widen_money_columns)
    _ensure_review_columns()    # отзыв принадлежит модели — ПОСЛЕ того, как модели собраны
    _ensure_raffle_columns()    # finished_at: когда розыгрыш реально подвели
    _ensure_raffle_uniques()    # один билет на человека, один активный розыгрыш
    _migrate("0003-история-по-времени-магазина", _shift_history_to_shop_time)
    _засеять_однажды()          # стартовые данные — ТОЛЬКО в новую базу
    # После засева: на чистой базе способы появляются именно здесь, и перенос
    # должен увидеть их, а не пустую таблицу.
    _migrate("0006-адрес-самовывоза-стал-точкой", _pickup_addresses_to_points)

    # Одна строка в логе при старте. Смотрят на неё ровно тогда, когда магазин
    # ведёт себя странно или копию только что развернули в пустую базу: первый
    # вопрос в такую минуту — а та ли это схема.
    # Магазин обязан подняться, даже если перенос не удался: продавцы из
    # окружения — удобство, а не условие работы. Заявку летопись при этом
    # снимет сама, и на следующем запуске попробуем снова.
    try:
        _migrate("0004-админы-из-окружения-в-базу", _seed_admins_from_env)
    except Exception as e:
        print(f"Не удалось перенести админов из настроек сервера: {e}")

    _ensure_indexes()           # скорость чтения; ставится последним — колонки уже все на месте

    состояние = schema_version()
    ждут = состояние["ждут"]
    print(f"Схема базы: {состояние['последняя'] or 'чистая'}"
          + (f" · НЕ ПРИМЕНЕНО: {', '.join(ждут)}" if ждут else ""), flush=True)


# Индексы ради скорости — в отличие от пяти прежних, которые стоят ради
# уникальности (один билет на человека, один промокод на код).
#
# Почему это вообще понадобилось. Пока заказов тысячи, разницы не видно: база
# успевает перебрать всю таблицу за пару миллисекунд, и без индекса всё летает.
# Но перебор растёт вместе с магазином, а индекс — нет. Замерено на 30 000
# заказов: список заказов точки 3.03 мс → 0.32 мс, а ночная уборка (при жизненном
# раскладе, где «новых» шестьдесят из тридцати тысяч) — 4.14 мс → 0.026 мс,
# потому что перебор всей таблицы сменился попаданием по индексу.
#
# Числа сегодня незаметны человеку, и это правильный момент их поставить:
# заказы не удаляются никогда, таблица только растёт, и разница «перебор против
# индекса» из незаметной становится заметной сама, без единой правки кода.
# Чинить это тогда пришлось бы на живом магазине и в спешке.
#
# Только добавление: индекс не меняет ни одной строки и не мешает откату кода
# назад — старая версия просто им не пользуется.
_ИНДЕКСЫ = (
    # История заказов покупателя (профиль) и заказы точки (экран продавца).
    # Вторая колонка — id: по нему же идёт сортировка «новые сверху», и с ней
    # в индексе база не сортирует найденное отдельно.
    ("ix_orders_user", "orders (user_id, id)"),
    ("ix_orders_city", "orders (city, id)"),
    # Ночная уборка ищет «новые, залежавшиеся». Двухколоночный: по одному
    # статусу толку нет, если «новых» много, а вместе с датой — есть всегда.
    ("ix_orders_status_created", "orders (status, created_at)"),
    # Отзывы в карточке товара: берутся только опубликованные.
    ("ix_reviews_product", "reviews (product_id, status)"),
    # Вкусы, галерея и подписки — всё это читается по товару.
    ("ix_variants_product", "product_variants (product_id)"),
    ("ix_product_photos_product", "product_photos (product_id)"),
    # У подписок уже есть уникальный (product_id, user_id), но он не помогает
    # искать ПО ПОКУПАТЕЛЮ: по второй колонке индекс не ищет.
    ("ix_stock_alerts_user", "stock_alerts (user_id)"),
    # Летопись монет и движения склада: обе только растут, обе читаются
    # выборочно — по человеку и по товару.
    ("ix_coin_log_user", "coin_log (user_id)"),
    ("ix_stock_moves_product", "stock_moves (product_id, id)"),
)


def _ensure_indexes():
    """Ставит индексы скорости. IF NOT EXISTS — значит вызов дешёвый и повторный
    запуск ничего не пересоздаёт."""
    conn = connect()
    cur = conn.cursor()
    for имя, куда in _ИНДЕКСЫ:
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {имя} ON {куда}")
        except Exception as e:
            # Магазин обязан подняться и без индекса: это скорость, а не работа.
            conn.rollback()
            print(f"Индекс {имя} не поставлен: {e}", flush=True)
    conn.commit()
    conn.close()


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
        cur.execute(f"ALTER TABLE products ADD COLUMN cost {MONEY} DEFAULT 0")
    # Снят с витрины. Удалить было единственным способом убрать товар из
    # продажи — а удаление уносит и остаток, и историю. Теперь «больше не
    # продаём» и «этого не было» — разные действия.
    if "hidden" not in cols:
        cur.execute("ALTER TABLE products ADD COLUMN hidden INTEGER DEFAULT 0")
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
    _migrate("0002-прогресс-колеса-в-рублях", _migrate_wheel_progress_to_money)




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
    # Какая редакция документов действовала в момент заказа. Согласие без
    # указания, с ЧЕМ согласились, доказывает ровно ничего: тексты правятся.
    if "terms_version" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN terms_version INTEGER DEFAULT 0")
    if "delivery_address" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_address TEXT")
    if "delivery_fee" not in cols:
        cur.execute(f"ALTER TABLE orders ADD COLUMN delivery_fee {MONEY} DEFAULT 0")
    if "payment_method" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
    if "comment" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN comment TEXT")
    if "phone" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN phone TEXT")
    # Сколько денег реально пришло на счёт. Магазин принимает перевод на карту
    # и фото чека, а сверить это с выпиской банка было нечем: заказ и
    # поступление связывала только память продавца. Число записывает продавец
    # при подтверждении — у него в этот момент открыт банк.
    #
    # От подделки это не защищает и не должно: защита — дело банка. Это ловушка
    # на небрежность (пришло не столько, пришло не за тот заказ) и единственная
    # ниточка от заказа к строке выписки.
    if "paid_amount" not in cols:
        cur.execute(f"ALTER TABLE orders ADD COLUMN paid_amount {MONEY}")
    # payer_last4 осталась от первого захода, где эти данные спрашивали у
    # покупателя. От затеи отказались — лишнее поле на экране оплаты стоит
    # брошенных заказов, — а колонку не сносим: правило схемы «только
    # добавлять», и на нём держится безопасность отката (см. tests/test_schema).
    if "payer_last4" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN payer_last4 TEXT")
    if "reminded_at" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN reminded_at TEXT")   # для повторного напоминания продавцу
    if "promo_code" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")    # каким кодом воспользовались
    if "promo_discount" not in cols:
        cur.execute(f"ALTER TABLE orders ADD COLUMN promo_discount {MONEY} DEFAULT 0")
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













def set_order_coins_used(order_id, coins):
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("UPDATE orders SET coins_used = %s WHERE id = %s"), (int(coins), order_id))
    conn.commit()
    conn.close()






# ---------- Категории товара ----------

# Стартовый набор. Первые три были в коде с самого начала — их коды менять
# нельзя: по ним заведены все существующие товары и бренды, а у одноразок и
# жидкостей к коду привязаны свои поля (затяжки, крепость и объём).
# (код, название, значок, порядок, есть ли вкусы).
# Признак вкусов держим ЗДЕСЬ, а не отдельным UPDATE в миграции: миграция
# проставляла его в момент, когда категорий ещё не существовало, и на новой
# базе жидкости оставались без вкусов. Засев, зависящий от того, что раньше
# него отработала другая функция, ломается от любой перестановки строк.
CATEGORY_SEED = [
    ("disposable",  "Одноразки",   "🔋", 10, 1),
    ("liquid",      "Жидкости",    "💧", 20, 1),
    ("podsystem",   "Подсистемы",  "🧩", 30, 0),
    ("coils",       "Расходники",  "⚙️", 40, 0),   # испарители, картриджи, вата
    ("devices",     "Устройства",  "🔧", 50, 0),   # моды, боксы, наборы
    ("accessories", "Аксессуары",  "🔌", 60, 0),   # зарядки, аккумуляторы, чехлы
]

_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
             "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
             "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
             "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
             "ю": "yu", "я": "ya"}






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




def _spec_json(r):
    try:
        options = json.loads(r["options"]) if r["options"] else []
    except (TypeError, ValueError):
        options = []
    return {"id": r["id"], "category": r["category"], "key": r["key"], "label": r["label"],
            "unit": r["unit"] or "", "kind": r["kind"] or "text", "options": options, "sort": r["sort"]}










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














# ---------- 18+ ----------



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




# ---------- Пользователи: бонусы и рефералы ----------





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












# --- Рефералы 2.0: активация, проценты, заработок ---

REFERRAL_BONUS = 50                       # монет пригласившему за ПЕРВЫЙ заказ друга (по умолчанию)




REFERRAL_TIERS = [(15, 5), (10, 4), (5, 3), (0, 2)]   # (мин. активных, процент)




























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









# ---------- Промокоды ----------















# ---------- Точки самовывоза ----------



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








# ---------- Что подставить в оформление ----------



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


def advance_sequences(cur=None):
    """Подвинуть счётчики id за наибольший номер в таблицах. Только Postgres.

    Копия привозит СТРОКИ, но не счётчики. На SQLite это неважно: следующий id
    он берёт как max(id)+1, то есть смотрит на сами строки. У Postgres счётчик
    отдельный (SERIAL — это последовательность), в пустой базе он стоит на
    единице, и копия его не двигает.

    Чем это кончается, видно только в тот единственный день, ради которого
    копии и снимают: магазин восстановили, всё на месте — а первый же заказ
    падает с «duplicate key», потому что заказ №1 в базе уже есть. Проверено
    вживую: 40 восстановленных заказов, и НИ ОДНОГО нового принять нельзя.

    Возвращает список подвинутых счётчиков — чтобы восстановление могло о них
    отчитаться, а не молчать.
    """
    if not USE_PG:
        return []
    своё = cur is None
    if своё:
        conn = connect()
        cur = conn.cursor()
    подвинуто = []
    try:
        # Спрашиваем у базы, а не держим списком: новая таблица со своим id
        # попадёт сюда сама. Забытый в списке счётчик — та же беда, только
        # растянутая во времени.
        cur.execute("""SELECT table_name AS t, column_name AS c,
                              pg_get_serial_sequence(table_name, column_name) AS seq
                       FROM information_schema.columns
                       WHERE table_schema = 'public'""")
        свои = [(r["t"], r["c"], r["seq"]) for r in cur.fetchall() if r["seq"]]
        for таблица, колонка, счётчик in свои:
            # Только ВПЕРЁД. Поставить счётчик ровно на max(id)+1 заманчиво, но
            # опасно: если строки удаляли, счётчик ушёл дальше максимума, и
            # откат назад заставил бы новый заказ занять номер удалённого. А на
            # номера заказов ссылаются монеты, журнал и переписка — вернувшийся
            # номер притянул бы к новому заказу чужую историю.
            #
            # is_called=false у setval означает «это СЛЕДУЮЩИЙ номер», а не
            # «последний выданный»; pg_sequence_last_value даёт NULL, пока
            # счётчиком не пользовались, — отсюда COALESCE(...) + 1 с обеих
            # сторон, и пустая таблица честно начинает с единицы.
            cur.execute(f"SELECT setval(%s, GREATEST("
                        f"  COALESCE(pg_sequence_last_value(%s::regclass), 0) + 1,"
                        f"  COALESCE((SELECT MAX({колонка}) FROM {таблица}), 0) + 1"
                        f"), false) AS v", (счётчик, счётчик))
            подвинуто.append(f"{таблица}.{колонка} → {int(cur.fetchone()['v'])}")
        if своё:
            conn.commit()
    finally:
        if своё:
            conn.close()
    return подвинуто


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

    # Строки на месте — но на Postgres счётчики id остались там, где были, и
    # первый же новый заказ упал бы на чужом номере. Двигаем их здесь, а не в
    # tools/restore.py: заливок копии два места (скрипт и тесты), и счётчики
    # должны подтягиваться в обоих.
    подвинуто = advance_sequences(cur)
    if подвинуто:
        # Ключ кириллицей — чтобы в отчёте восстановления он встал последним,
        # после всех таблиц, и читался как итог, а не как ещё одна таблица.
        report["счётчики id"] = f"пересчитаны ({len(подвинуто)})"

    conn.commit()
    conn.close()
    return report


# ---------- «Сообщить о поступлении» ----------



def alerts_of_user(user_id):
    """Товары, поступления которых ждёт этот покупатель — чтобы витрина показала,
    что он уже подписан, и не предлагала нажать ещё раз."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT product_id FROM stock_alerts WHERE user_id = %s"), (user_id,))
    ids = [int(r["product_id"]) for r in cur.fetchall()]
    conn.close()
    return ids












# ---------- Админы и продавцы (управляются из приложения) ----------









# ---------- Журнал действий ----------

ADMIN_LOG_KEEP = 2000     # сколько последних записей держим






# ---------- Картинки ----------





def receipt_owner(file_id):
    """Чей это чек об оплате (user_id) или None. Нужен, чтобы картинку чека
    видели только продавец и сам покупатель, а не любой, кому попала ссылка."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("SELECT user_id FROM orders WHERE receipt_file_id = %s LIMIT 1"), (file_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["user_id"]) if row else None






# ---------- Отзывы ----------





def _model_of(cur, product_id):
    """Модель товара или None (товар заведён до «Ассортимента»)."""
    cur.execute(_q("SELECT model_id FROM products WHERE id = %s"), (product_id,))
    row = cur.fetchone()
    return row["model_id"] if row else None
























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






# ---------- Товары ----------







# ---------- Админка: добавить / изменить / удалить ----------



# Какие колонки разрешено менять (защита: имя колонки нельзя подставить параметром).
_EDITABLE = {"name", "price", "cost", "stock", "is_hit", "description", "photo", "photo_thumb",
             "brand", "flavor", "strength", "volume", "category", "city", "hidden"}












# ---------- Заказы ----------































ORDER_EDITABLE = ("new", "paid", "confirmed")     # до выдачи заказ ещё можно поправить


















# ---------- Локации (точки продаж) ----------













# ---------- Бренды (со списком вкусов) ----------





















# ---------- Ассортимент: модели товаров ----------























# Колонки с деньгами: таблица и поле. Список руками и намеренно — расширять
# тип «всему, что похоже на число» нельзя: id, остатки и количества деньгами
# не являются, и трогать их незачем.
ДЕНЕЖНЫЕ_КОЛОНКИ = [
    ("products", "price"), ("products", "cost"),
    ("orders", "total"), ("orders", "delivery_fee"),
    ("orders", "paid_amount"), ("orders", "promo_discount"),
    ("delivery_methods", "fee"),
    ("promos", "value"), ("promos", "min_total"),
    ("raffles", "threshold"),
    ("stock_moves", "cost"),
]


def _widen_money_columns():
    """Расширяет денежные колонки до двойной точности. Только Postgres.

    Зачем. REAL в Postgres — это ЧЕТЫРЕ байта, около семи значащих цифр.
    Отдельная цена такое переживает, а суммы нет: SUM(real) возвращает тоже
    real, и выручка копится в одинарной точности. На 3000 заказов замерено
    расхождение в 8 копеек, и оно растёт с оборотом. Врали не чеки, а отчёты.

    Расширение float4 → float8 не теряет ни бита: любое число, представимое в
    четырёх байтах, точно представимо в восьми. Обратный ход был бы потерей —
    поэтому только в эту сторону.

    В SQLite REAL и так восемь байт, менять нечего.
    """
    if not USE_PG:
        return 0
    conn = connect()
    cur = conn.cursor()
    сделано = 0
    for таблица, колонка in ДЕНЕЖНЫЕ_КОЛОНКИ:
        cur.execute("""SELECT data_type FROM information_schema.columns
                       WHERE table_schema = current_schema()
                         AND table_name = %s AND column_name = %s""",
                    (таблица, колонка))
        строка = cur.fetchone()
        if not строка or строка["data_type"] != "real":
            continue          # колонки нет или она уже двойной точности
        # ALTER COLUMN ... TYPE DOUBLE PRECISION — единственное расширение,
        # которое сторож схемы пропускает; см. tests/test_schema.py.
        cur.execute(f"ALTER TABLE {таблица} ALTER COLUMN {колонка} TYPE DOUBLE PRECISION")
        # Расширение не лечит того, что уже потеряно при записи: цена 44.27,
        # пролежавшая в четырёх байтах, возвращается как 44.27000045776367.
        # Показывали мы её всегда как 44.27 — это и есть настоящее значение,
        # поэтому дочищаем. Иначе следы одинарной точности остались бы в базе
        # навсегда и продолжали копиться в суммах.
        cur.execute(f"UPDATE {таблица} SET {колонка} = ROUND({колонка}::numeric, 2) "
                    f"WHERE {колонка} IS NOT NULL")
        сделано += 1
    conn.commit()
    conn.close()
    if сделано:
        print(f"Деньги переведены в двойную точность: колонок {сделано}", flush=True)
    return сделано


ЗАСЕВ = "seed_done"          # отметка «стартовые данные уже сеяли»


def _засеять_однажды():
    """Стартовые категории, точки и способы получения — только в НОВУЮ базу.

    Раньше каждый засев смотрел «пуста ли таблица» и сеял, если пуста. А
    init_db() выполняется при КАЖДОМ старте процесса — то есть при каждой
    выкатке и каждом перезапуске хостинга. Владелец удалял все категории или
    все точки, магазин перезапускался, и удалённое возвращалось само. Понять
    причину было невозможно: между удалением и возвратом проходили часы, и
    ничьего действия в этот момент не было.

    Пустая таблица — это НЕ «новый магазин». Это может быть магазин, который
    осознанно убрал у себя всё лишнее. Поэтому решает отметка, а не счётчик
    строк: посеяли один раз — больше никогда.

    Базе, которая жила до этой отметки, ставим её без засева: там уже всё
    своё, и подсыпать ей стартовые категории было бы тем самым возвратом.
    """
    if get_setting(ЗАСЕВ):
        return False

    conn = connect()
    cur = conn.cursor()
    обжитая = False
    for таблица in ("locations", "categories", "products", "orders"):
        cur.execute(f"SELECT COUNT(*) AS c FROM {таблица}")
        if cur.fetchone()["c"]:
            обжитая = True
            break
    conn.close()

    if обжитая:
        set_setting(ЗАСЕВ, "1")
        return False

    seed_categories()
    seed_category_specs()
    seed_locations()
    seed_delivery()
    set_setting(ЗАСЕВ, "1")
    print("Новая база: посеяны стартовые категории, точки и способы получения.", flush=True)
    return True


ЗАГЛУШКА_САМОВЫВОЗА = "Уточните адрес самовывоза в настройках"


def _pickup_addresses_to_points():
    """Адрес самовывоза жил в ДВУХ местах, и это была ловушка.

    Первое место — строка `pickup_address` внутри способа получения. Второе —
    список точек самовывоза города. Какое из них увидит покупатель, решала
    мелочь, невидимая из формы: есть ли в городе хоть одна точка. Заведёшь
    точку — вписанный в способ адрес молча перестаёт показываться, и понять
    это неоткуда.

    Оставляем одно место — точки. Перенос делает из каждой такой строки точку
    города, если точно такой ещё нет. Строку не трогаем: колонка остаётся в
    базе (схема правится только добавлением), а показывать её перестаёт код.

    Заглушку из стартовой засева не переносим: это не адрес, а напоминание
    заполнить настройки. Точка с таким «адресом» врала бы покупателю.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute(_q("""SELECT DISTINCT city, pickup_address FROM delivery_methods
                      WHERE needs_address = 0 AND pickup_address IS NOT NULL
                        AND pickup_address <> ''"""))
    кандидаты = [(r["city"], (r["pickup_address"] or "").strip()) for r in cur.fetchall()]
    заведено = 0
    for город, адрес in кандидаты:
        if not адрес or адрес == ЗАГЛУШКА_САМОВЫВОЗА:
            continue
        cur.execute(_q("SELECT id FROM pickup_points WHERE city = %s AND address = %s"),
                    (город, адрес))
        if cur.fetchone():
            continue
        cur.execute(_q("INSERT INTO pickup_points (city, address, note, sort) VALUES (%s, %s, %s, %s)"),
                    (город, адрес, "", 0))
        заведено += 1
    conn.commit()
    conn.close()
    if заведено:
        print(f"Адреса самовывоза стали точками: {заведено}", flush=True)
    return заведено


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













# --- Розыгрыши ---
# Сам код — в partut/db/raffles.py, здесь только имена, чтобы весь магазин по-прежнему
# звал db.get_active_raffle() и не знал о переезде.
#
# Импорт внизу файла намеренно: db_raffles обращается к примитивам через db, и к
# этому моменту они уже определены. F401 подавлен осознанно — это переэкспорт,
# имена нужны не здесь, а тем, кто зовёт их через db.
# --- Склад ---
# Движения и подписки на поступление — см. partut/db/stock.py.
from partut.db.stock import (                                          # noqa: E402
    move_stock, get_stock_moves, stock_losses,                          # noqa: F401
    add_stock_alert, remove_stock_alert, stock_alerts_ready,            # noqa: F401
    clear_stock_alerts, stock_alert_counts, STOCK_REASONS,              # noqa: F401
)

# --- Промокоды ---
# Код занимается одной транзакцией с заказом — см. partut/db/promos.py.
from partut.db.promos import (                                         # noqa: E402
    _promo_row, check_promo, consume_promo, release_promo,              # noqa: F401
    list_promos, add_promo, set_promo_active,                           # noqa: F401
    delete_promo, _reserve_promo,                                       # noqa: F401
)

# --- Отзывы ---
# Отзыв принадлежит модели, а не товару на точке — см. partut/db/reviews.py.
from partut.db.reviews import (                                        # noqa: E402
    _ensure_review_columns, reviewable_products, add_review,            # noqa: F401
    list_reviews, list_reviews_by_user, admin_reviews,                  # noqa: F401
    pending_reviews, delete_review, set_review_reply,                   # noqa: F401
    count_pending_reviews, set_review_status, get_review,               # noqa: F401
    REVIEW_MAX_TEXT,                                                    # noqa: F401
)

# --- Картинки ---
# Витрина, галерея и кэш скачанного — в partut/db/photos.py.
from partut.db.photos import (                                         # noqa: E402
    MAX_EXTRA_PHOTOS, _ensure_photo_columns, get_photo_blob, save_photo_blob,   # noqa: F401
    is_shop_photo, is_product_photo, model_photos, all_model_photos,            # noqa: F401
    add_model_photo, get_product_photos, all_product_photos, add_product_photo,  # noqa: F401
    delete_product_photo, purge_orphan_photos, photo_blob_stats, set_model_photo,  # noqa: F401
)

# --- Игры ---
# Колесо и слот — в partut/db/games.py. Здесь только имена: магазин зовёт их через db.
from partut.db.games import (                                          # noqa: E402
    WHEEL_STEP_DEFAULT, WHEEL_ITEMS_STEP_OLD,                               # noqa: F401
    _migrate_wheel_progress_to_money, wheel_step, get_wheel,                # noqa: F401
    add_wheel_progress, add_spins, use_spin, do_wheel_spin,                 # noqa: F401
    do_slot_spin, get_game_stats,                                           # noqa: F401
)

from partut.db.raffles import (                                        # noqa: E402
    _RAFFLE_EDITABLE, _ensure_raffle_columns, _ensure_raffle_uniques,       # noqa: F401
    get_active_raffle, get_last_finished_raffle, recent_finished_raffle,    # noqa: F401
    create_raffle, update_raffle_field, claim_raffle_draw,                  # noqa: F401
    set_raffle_winners, finish_raffle, add_raffle_entry, is_entered,        # noqa: F401
    count_entries, get_raffle_user_ids, spent_since, get_raffle_state,      # noqa: F401
)


# --- Устройство магазина ---
# Точки, способы получения, категории, доступы продавцов — в partut/db/shop.py.
from partut.db.shop import (                                            # noqa: E402
    get_delivery_methods, add_delivery_method, update_delivery_method,      # noqa: F401
    get_delivery_method, delete_delivery_method, seed_delivery,             # noqa: F401
    set_order_delivery, delivery_prefill,                                   # noqa: F401
    seed_locations, get_locations, location_names, get_location,            # noqa: F401
    add_location, delete_location, count_products_in_location,              # noqa: F401
    get_pickup_points, all_pickup_points, add_pickup_point,                 # noqa: F401
    update_pickup_point, delete_pickup_point,                               # noqa: F401
    _category_code, seed_categories, seed_category_specs,                   # noqa: F401
    list_category_specs, add_category_spec, update_category_spec,           # noqa: F401
    delete_category_spec, list_categories, category_codes, add_category,    # noqa: F401
    update_category, count_products_in_category, delete_category,           # noqa: F401
    list_staff, add_staff, remove_staff, staff_ids_by_city,                 # noqa: F401
    log_admin_action, list_admin_log,                                       # noqa: F401
    documents, documents_version, set_documents,                            # noqa: F401
)


# --- Цифры магазина ---
# Выручка, прибыль, движение монет, «что берут вместе» — в partut/db/reports.py.
from partut.db.reports import (                                         # noqa: E402
    inc_stat, reset_statistics, get_business_stats, coin_flow, also_bought,  # noqa: F401
)


# --- Покупатели ---
# Монеты, рефералы, карточка — в partut/db/customers.py.
from partut.db.customers import (                                       # noqa: E402
    is_age_ok, set_age_ok, ensure_user, get_user_row,                       # noqa: F401
    log_coins, add_coins, get_coins, spend_coins,                           # noqa: F401
    count_referrals, referral_bonus, coins_per_byn, ref_percent,            # noqa: F401
    get_bonus_stats, count_active_referrals, list_referrals,                # noqa: F401
    get_ref_earned, add_ref_earned, reward_referrer_for_order,              # noqa: F401
    set_ref_activated, unlink_referral, clear_referrals_of,                 # noqa: F401
    list_users, customer_card, delete_user,                                 # noqa: F401
)


# --- Ассортимент ---
# Товары, модели, бренды и варианты — в partut/db/catalog.py.
from partut.db.catalog import (                                         # noqa: E402
    get_products, get_product, get_all_products, add_product,               # noqa: F401
    hide_model_products, update_field, toggle_hit, delete_product,          # noqa: F401
    change_stock,                                                           # noqa: F401
    get_brands, get_brand, find_brand_by_name, count_products_of_brand,     # noqa: F401
    rename_brand_in_products, known_flavors, merge_duplicate_brands,        # noqa: F401
    add_brand, update_brand, delete_brand,                                  # noqa: F401
    _model_json, list_models, get_model, add_model, update_model,           # noqa: F401
    merge_model_flavors,                                                    # noqa: F401
    propagate_model, orphan_flavors, count_products_of_model, delete_model, # noqa: F401
    add_product_from_model,                                                 # noqa: F401
    get_variants, get_all_variants, add_variant, delete_variants,           # noqa: F401
    change_variant_stock, recalc_product_stock,                             # noqa: F401
)


# --- Заказы ---
# Оформление, состав, статусы и напоминания — в partut/db/orders.py. Здесь
# только имена: магазин зовёт их через db, и переезд для него незаметен.
from partut.db.orders import (                                          # noqa: E402
    PromoGone, OutOfStock, ORDER_TOKEN_HOURS,                               # noqa: F401
    create_order, get_checkout_data, find_order_by_token, place_order,      # noqa: F401
    get_order, get_orders, get_orders_by_user, get_open_order,              # noqa: F401
    seller_today, restore_order_stock, cancel_order, update_order_items,    # noqa: F401
    stale_new_orders, touch_order_reminded, orders_needing_reminder,        # noqa: F401
    set_order_status, set_order_status_if, set_order_receipt,               # noqa: F401
    set_order_paid_amount, open_orders_with_product,                        # noqa: F401
)


# Кто читает продавцов для проверки прав. config лежит в основании и про базу
# ничего не знает — функцию ему приносим отсюда, при загрузке db.
config.set_staff_source(staff_ids_by_city)
