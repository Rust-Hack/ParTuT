"""Выкладка новой версии поверх работающего магазина.

При каждом запуске сервер зовёт init_db(): он создаёт недостающие таблицы и
дописывает недостающие колонки. Это и есть механизм обновления — отдельных
миграций нет. Значит проверять надо именно его, причём на настоящей базе:
ALTER TABLE в SQLite и в Postgres ведут себя по-разному, а магазин работает
на Postgres.

Что должно быть верно:
  • повторный запуск ничего не ломает (сервер перезапускается часто);
  • база СТАРОЙ версии дообновляется, а не падает;
  • данные при этом целы — иначе выкладка стирает магазин.
"""
from _common import db, server, Checker

# Колонки, которые появились уже после первой версии: на них и проверяем
# дообновление старой базы.
LATE_COLUMNS = [("users", "username"), ("users", "first_name"), ("users", "no_reminders"),
                ("users", "ref_activated"), ("products", "hidden"), ("reviews", "model_id"),
                ("categories", "has_flavors"), ("orders", "promo_code"), ("orders", "phone"),
                ("orders", "client_token")]
LATE_TABLES = ["admin_log"]

UID = 9001


def _has_column(cur, table, column):
    if db.USE_PG:
        cur.execute("SELECT 1 AS x FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = %s", (table, column))
        return cur.fetchone() is not None
    cur.execute(f"PRAGMA table_info({table})")
    return any(r["name"] == column for r in cur.fetchall())


def _has_table(cur, table):
    if db.USE_PG:
        cur.execute("SELECT to_regclass(%s) AS t", (f"public.{table}",))
        return cur.fetchone()["t"] is not None
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,))
    return cur.fetchone() is not None


def run():
    c = Checker("Повторный запуск сервера")
    try:
        db.init_db()
        db.init_db()
        c("init_db дважды подряд не ломается", True)
    except Exception as e:
        c(f"init_db дважды подряд не ломается — упал: {e}", False)

    # SQLite не умеет DROP COLUMN в старых версиях, поэтому «старую базу»
    # изображаем только там, где это возможно без пересборки таблиц.
    if not db.USE_PG:
        c("проверка дообновления идёт на Postgres — здесь пропущена", True)
        return c.fails

    c2 = Checker("Обновление базы старой версии")
    db.ensure_user(UID)
    conn = db.connect(); cur = conn.cursor()
    cur.execute("UPDATE users SET coins = 777 WHERE user_id = %s", (UID,))
    conn.commit(); conn.close()
    oid = db.create_order(UID, "старый", "Минск",
                          [{"id": 1, "name": "Товар", "price": 10.0, "qty": 1}], 10.0, "")

    # Отматываем схему назад — как будто код обновили на давно работавшем магазине.
    conn = db.connect(); cur = conn.cursor()
    for table, column in LATE_COLUMNS:
        cur.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    for table in LATE_TABLES:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit(); conn.close()

    try:
        db.init_db()
        c2("выкладка на старую базу прошла", True)
    except Exception as e:
        c2(f"выкладка на старую базу прошла — упала: {e}", False)
        return c.fails + c2.fails

    conn = db.connect(); cur = conn.cursor()
    missing = [f"{t}.{col}" for t, col in LATE_COLUMNS if not _has_column(cur, t, col)]
    c2(f"все колонки вернулись{'' if not missing else ': нет ' + ', '.join(missing)}", not missing)
    c2("таблицы вернулись", all(_has_table(cur, t) for t in LATE_TABLES))

    # Защита от двойного заказа держится на уникальном ключе, а не только на
    # колонке: без ключа два одновременных запроса создадут два заказа, и
    # заметно это будет только по жалобе покупателя.
    cur.execute("SELECT 1 AS x FROM pg_indexes WHERE indexname = 'orders_client_token_uniq'")
    c2("уникальный ключ против дублей заказа на месте", cur.fetchone() is not None)

    cur.execute("SELECT coins AS c FROM users WHERE user_id = %s", (UID,))
    row = cur.fetchone()
    c2("монеты покупателя целы", row is not None and row["c"] == 777)
    cur.execute("SELECT COUNT(*) AS n FROM orders WHERE id = %s", (oid,))
    c2("заказ цел", cur.fetchone()["n"] == 1)
    conn.close()

    c2("магазин отвечает после обновления",
       server.app.test_client().get("/api/products").status_code == 200)

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id = %s", (oid,))
    cur.execute("DELETE FROM users WHERE user_id = %s", (UID,))
    conn.commit(); conn.close()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
