"""Деньги обязаны храниться в двойной точности.

REAL в Postgres — это ЧЕТЫРЕ байта, около семи значащих цифр. Отдельная цена
такое переживает: 19.99 и 1234.56 возвращаются из базы как есть. Ломаются
СУММЫ, и ломаются тихо: SUM(real) в Postgres возвращает тоже real, то есть
годовая выручка копится в одинарной точности.

Замерено на 3000 заказов средним чеком 64 BYN: расхождение 8 копеек, и оно
растёт с оборотом. Врали не чеки, а отчёты — там, где ошибку труднее всего
заметить и где на неё смотрят, решая, чем торговать дальше.

SQLite тут ни при чём: у него REAL и так восемь байт. Ровно поэтому набор
тестов на SQLite такую ошибку не увидит НИКОГДА — она живёт только на том
диалекте, на котором работает магазин.
"""
from decimal import Decimal

from _common import db, Checker


def run():
    c = Checker("Тип денежных колонок")
    if not db.USE_PG:
        # У SQLite одна плавающая точка на всё, и она восьмибайтовая.
        # Проверяем то, что тут вообще можно проверить: тип объявлен один.
        c("MONEY объявлен двойной точностью", db.MONEY == "DOUBLE PRECISION")
        c("денежные колонки перечислены", len(db.ДЕНЕЖНЫЕ_КОЛОНКИ) >= 10)
        return c.fails

    conn = db.connect(); cur = conn.cursor()
    одинарные = []
    пропали = []
    for таблица, колонка in db.ДЕНЕЖНЫЕ_КОЛОНКИ:
        cur.execute("""SELECT data_type FROM information_schema.columns
                       WHERE table_schema = current_schema()
                         AND table_name = %s AND column_name = %s""", (таблица, колонка))
        строка = cur.fetchone()
        if not строка:
            пропали.append(f"{таблица}.{колонка}")
        elif строка["data_type"] != "double precision":
            одинарные.append(f"{таблица}.{колонка} = {строка['data_type']}")
    conn.close()
    c(f"все денежные колонки на месте{'' if not пропали else ': нет ' + str(пропали)}", not пропали)
    c("ни одна не осталась одинарной точности"
      + ("" if not одинарные else f": {одинарные}"), not одинарные)
    return c.fails


def run_sum_is_exact():
    """Сумма трёх тысяч заказов обязана сойтись с точной до копейки.

    Проверка делом, а не по типу колонки: тип можно вернуть обратно, а эта
    проверка покажет последствие — неверную выручку в отчёте.
    """
    import random

    c = Checker("Выручка сходится")
    if not db.USE_PG:
        c("на SQLite проверять нечего — у него и так восемь байт", True)
        return c.fails

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()

    random.seed(7)
    суммы = [round(random.uniform(8, 120), 2) for _ in range(3000)]
    conn = db.connect(); cur = conn.cursor()
    for i, s in enumerate(суммы):
        cur.execute(db._q("""INSERT INTO orders (user_id, username, city, items, total,
                                                 pickup_time, status, created_at)
                             VALUES (%s, %s, %s, %s, %s, %s, 'new', %s)"""),
                    (900000 + i, "buyer", "Минск", "[]", s, "", db._now_str()))
    conn.commit()
    cur.execute("SELECT SUM(total) AS s FROM orders")
    из_базы = float(cur.fetchone()["s"])
    conn.close()

    точно = float(sum(Decimal(str(s)) for s in суммы))
    расхождение = abs(из_базы - точно)
    c(f"выручка по {len(суммы)} заказам сходится (разница {расхождение:.4f} BYN)",
      расхождение < 0.005)

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()
    return c.fails


def run_widen_existing():
    """Боевая база уже существует — перенос обязан починить ЕЁ, а не только новые.

    Новая база заводится правильно сама, и проверка типов выше это подтверждает.
    Но у магазина база одна, ей год, и колонки в ней созданы старым кодом. Тут
    и решается, была правка полезной или только красивой.

    Отдельная тонкость, из-за которой первая версия этой проверки была неверной.
    Одиночное значение из колонки real читается ПРАВИЛЬНО: Postgres печатает
    кратчайшее представление, и 44.27 возвращается как 44.27. Потеря видна
    только в сумме — ровно поэтому она и дожила до сих пор. Значит и проверять
    надо сумму, а не отдельные числа.
    """
    import random

    c = Checker("Перенос чинит существующую базу")
    if not db.USE_PG:
        c("на SQLite переносить нечего", db._widen_money_columns() == 0)
        return c.fails

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    # Возвращаем базу в состояние «до правки»: деньги одинарной точности.
    for таблица, колонка in db.ДЕНЕЖНЫЕ_КОЛОНКИ:
        cur.execute(f"ALTER TABLE {таблица} ALTER COLUMN {колонка} TYPE REAL")
    conn.commit(); conn.close()

    random.seed(7)
    суммы = [round(random.uniform(8, 120), 2) for _ in range(3000)]
    точно = float(sum(Decimal(str(s)) for s in суммы))
    conn = db.connect(); cur = conn.cursor()
    for i, s in enumerate(суммы):
        cur.execute(db._q("""INSERT INTO orders (user_id, username, city, items, total,
                                                 pickup_time, status, created_at)
                             VALUES (%s,%s,%s,%s,%s,%s,'new',%s)"""),
                    (910000 + i, "b", "Минск", "[]", s, "", db._now_str()))
    conn.commit()
    cur.execute("SELECT SUM(total) AS s FROM orders")
    было = float(cur.fetchone()["s"])
    conn.close()
    c(f"старая база и правда врёт в выручке: {было - точно:+.2f} BYN",
      abs(было - точно) > 0.005)

    сделано = db._widen_money_columns()
    c(f"перенос тронул колонки ({сделано})", сделано == len(db.ДЕНЕЖНЫЕ_КОЛОНКИ))

    conn = db.connect(); cur = conn.cursor()
    cur.execute("""SELECT data_type FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name = 'orders' AND column_name = 'total'""")
    c("тип стал двойной точностью", cur.fetchone()["data_type"] == "double precision")
    cur.execute("SELECT SUM(total) AS s, COUNT(*) AS n FROM orders")
    строка = cur.fetchone()
    стало, сколько = float(строка["s"]), int(строка["n"])
    cur.execute("SELECT total FROM orders ORDER BY id LIMIT 5")
    образцы = [float(r["total"]) for r in cur.fetchall()]
    conn.close()

    c(f"заказы все на месте ({сколько})", сколько == len(суммы))
    c(f"выручка сошлась: {стало - точно:+.4f} BYN", abs(стало - точно) < 0.005)
    # Расширение типа не лечит потерянного при записи — значения дочищаются.
    c(f"значения дочищены до копеек: {образцы}", образцы == [round(s, 2) for s in суммы[:5]])

    # Второй прогон не должен ничего трогать: колонки уже расширены.
    c("повторный перенос ничего не делает", db._widen_money_columns() == 0)

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()
    return c.fails
