"""Время магазина, а не сервера.

Магазин живёт по Минску, сервер Render — по UTC, часового пояса в его настройках
нет. Пока «сейчас» брали у сервера, расходилось две вещи.

Заказ, сделанный в час ночи, записывался вчерашним числом и на три часа раньше.
Покупатель открывал «Мои заказы» и видел не то время, когда заказывал.

Сутки магазина начинались в три часа ночи: до трёх плитка «Сегодня» показывала
продавцу вчерашнюю выручку, а ночные заказы попадали в позавчерашний день
статистики.

Проверка идёт с подставными часами, а не с настоящими: иначе она значила бы
разное в зависимости от того, в какое время суток её запустили.
"""
import datetime
import threading

from _common import db, Checker

# Час ночи — то самое время, когда серверные сутки ещё вчерашние.
НОЧЬ = datetime.datetime(2026, 8, 19, 1, 30)


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()


def run():
    c = Checker("Часы магазина")
    настоящие = db.shop_now
    старое_смещение = db.SHOP_TZ_OFFSET
    try:
        # Часы магазина не должны зависеть от пояса сервера — они считаются от UTC.
        ожидаемое = datetime.datetime.utcnow() + datetime.timedelta(hours=db.SHOP_TZ_OFFSET)
        c("часы магазина отсчитываются от UTC, а не от пояса сервера",
          abs((db.shop_now() - ожидаемое).total_seconds()) < 5)

        db.SHOP_TZ_OFFSET = 0
        по_гринвичу = db.shop_now()
        db.SHOP_TZ_OFFSET = 3
        по_минску = db.shop_now()
        c("смещение и правда двигает часы",
          2.9 < (по_минску - по_гринвичу).total_seconds() / 3600 < 3.1)
    finally:
        db.SHOP_TZ_OFFSET = старое_смещение

    # --- Ночной заказ ---
    c2 = Checker("Заказ в час ночи")
    _clean()
    db.shop_now = lambda: НОЧЬ
    try:
        oid = db.create_order(7301, "ночной", "Минск",
                              [{"product_id": 1, "name": "Под", "price": 40.0, "qty": 1}], 40.0, "")
        заказ = db.get_order(oid)
        c2("время заказа — минское, а не серверное",
           заказ["created_at"] == "2026-08-19 01:30")
        c2("и день тот, в который человек заказывал",
           заказ["created_at"].startswith("2026-08-19"))

        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE id = %s"), (oid,))
        conn.commit(); conn.close()

        t = db.seller_today("Минск")
        c2("плитка «Сегодня» считает ночной заказ сегодняшним", t["issued_today"] == 1)
        c2("и выручку тоже", t["revenue_today"] == 40.0)

        s = db.get_business_stats(1)
        c2("статистика за день видит его же", s["orders"] == 1 and s["revenue"] == 40.0)
    finally:
        db.shop_now = настоящие

    # --- Смена суток ---
    # Ровно то, что было сломано: в 01:30 сутки уже новые, и вчерашняя выручка
    # в «Сегодня» попадать не должна.
    c3 = Checker("Сутки кончаются в полночь, а не в три часа ночи")
    _clean()
    db.shop_now = lambda: datetime.datetime(2026, 8, 18, 23, 40)
    try:
        вчерашний = db.create_order(7302, "вечерний", "Минск",
                                    [{"product_id": 1, "name": "Под", "price": 25.0, "qty": 1}], 25.0, "")
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE id = %s"), (вчерашний,))
        conn.commit(); conn.close()
    finally:
        db.shop_now = настоящие

    db.shop_now = lambda: НОЧЬ
    try:
        t = db.seller_today("Минск")
        c3("вчерашний вечер в сегодняшнюю выручку не попал", t["issued_today"] == 0)
        c3("и денег там нет", t["revenue_today"] == 0)
    finally:
        db.shop_now = настоящие
        _clean()

    # --- Одни часы на всех ---
    c4 = Checker("Часы одни на весь магазин")
    from partut.bot import handlers as botmod
    db.shop_now = lambda: НОЧЬ
    try:
        c4("бот смотрит на те же часы, что и база", botmod._local_now() == НОЧЬ)
    finally:
        db.shop_now = настоящие
    c4("смещение у бота и базы одно", botmod.SUMMARY_TZ_OFFSET == db.SHOP_TZ_OFFSET)

    # --- Разовый перевод истории ---
    # Записи, сделанные до перехода, лежат по UTC. Оставить их так — значит
    # держать в базе два разных времени сразу: заказ вторника и заказ среды в
    # разных системах отсчёта, и сравнивать их нельзя.
    c5 = Checker("История переведена на время магазина")
    _clean()
    conn = db.connect(); cur = conn.cursor()
    for t in ("users", "raffles"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (db._TZ_SHIFT_MARK,))
    conn.commit(); conn.close()

    oid = db.create_order(7401, "старый", "Минск",
                          [{"product_id": 1, "name": "Под", "price": 10.0, "qty": 1}], 10.0, "")
    db.ensure_user(7401)
    rid = db.create_raffle(days=30)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET created_at = %s, reminded_at = %s, pickup_time = %s WHERE id = %s"),
                ("2026-08-14 04:34", "2026-08-14 05:00", "", oid))
    # Запись перед самой полуночью: сдвиг обязан перенести её на следующий день.
    cur.execute(db._q("UPDATE users SET created_at = %s WHERE user_id = %s"),
                ("2026-08-13 23:10", 7401))
    cur.execute(db._q("UPDATE raffles SET starts_at = %s, ends_at = %s WHERE id = %s"),
                ("2026-08-12 16:09", "2026-09-11 16:09", rid))
    conn.commit(); conn.close()

    def _взять():
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("SELECT created_at AS c, reminded_at AS r, pickup_time AS p FROM orders WHERE id = %s"), (oid,))
        o = dict(cur.fetchone())
        cur.execute(db._q("SELECT created_at AS c FROM users WHERE user_id = %s"), (7401,))
        u = dict(cur.fetchone())
        cur.execute(db._q("SELECT ends_at AS e FROM raffles WHERE id = %s"), (rid,))
        r = dict(cur.fetchone())
        conn.close()
        return o, u, r

    # Новый код мог поработать до того, как дошли руки до истории. Такую свежую
    # запись сдвигать нельзя — она уже в минском времени, и сдвиг унёс бы её на
    # три часа в будущее. Отличаем по признаку: старая запись отстаёт от
    # настоящего момента минимум на смещение.
    свежий = db.create_order(7402, "свежий", "Минск",
                             [{"product_id": 1, "name": "Под", "price": 5.0, "qty": 1}], 5.0, "")
    свежее_время = db.get_order(свежий)["created_at"]

    сдвинуто = db._shift_history_to_shop_time()
    c5("запись нового кода осталась нетронутой",
       db.get_order(свежий)["created_at"] == свежее_время)
    o, u, r = _взять()
    c5("время заказа сдвинуто на часы магазина", o["c"] == "2026-08-14 07:34")
    c5("и остальные отметки заказа тоже", o["r"] == "2026-08-14 08:00")
    c5("пустое поле осталось пустым", o["p"] == "")
    c5("запись перед полуночью переехала на следующий день", u["c"] == "2026-08-14 02:10")
    c5("будущие сроки сдвинуты вместе со всем", r["e"] == "2026-09-11 19:09")
    c5("сдвиг посчитан", сдвинуто >= 5)

    # Второй сдвиг испортил бы данные молча — заметить это было бы уже не по чему.
    c5("повторный запуск ничего не сдвигает", db._shift_history_to_shop_time() == 0)
    o2, u2, r2 = _взять()
    c5("и время осталось прежним", (o2, u2, r2) == (o, u, r))
    c5("отметка о переводе лежит в базе, а не в памяти",
       bool(db.get_setting(db._TZ_SHIFT_MARK)))

    # Перезапуск сервиса — это повторный init_db. Он тоже не должен сдвигать.
    db.init_db()
    c5("перезапуск сервиса историю не двигает", _взять() == (o, u, r))

    # --- Несколько процессов поднялись разом ---
    # При выкладке процессов может стартовать несколько. «Прочитать отметку,
    # потом записать» пропустило бы всех — история уехала бы на шесть часов
    # вместо трёх, и заметить это было бы уже не по чему.
    c6 = Checker("Сдвиг при одновременном старте")
    _clean()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (db._TZ_SHIFT_MARK,))
    conn.commit(); conn.close()
    oid2 = db.create_order(7403, "старый", "Минск",
                           [{"product_id": 1, "name": "Под", "price": 1.0, "qty": 1}], 1.0, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET created_at = %s WHERE id = %s"), ("2026-08-10 10:00", oid2))
    conn.commit(); conn.close()

    итоги = []
    потоки = [threading.Thread(target=lambda: итоги.append(db._shift_history_to_shop_time()))
              for _ in range(6)]
    for t in потоки:
        t.start()
    for t in потоки:
        t.join()
    c6("сдвиг выполнил ровно один запуск", sum(1 for x in итоги if x > 0) == 1)
    c6("и время сдвинулось один раз, а не шесть",
       db.get_order(oid2)["created_at"] == "2026-08-10 13:00")

    # Сорвался — отметку надо отпустить: пропустить перевод не страшно, а вот
    # пометить его сделанным, не сделав, — страшно.
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (db._TZ_SHIFT_MARK,))
    conn.commit(); conn.close()
    настоящий = db._all_table_names
    db._all_table_names = lambda cur: (_ for _ in ()).throw(RuntimeError("база отвалилась"))
    try:
        db._shift_history_to_shop_time()
    finally:
        db._all_table_names = настоящий
    c6("после срыва отметка отпущена", not db.get_setting(db._TZ_SHIFT_MARK))
    c6("и следующий запуск доводит дело до конца",
       db._shift_history_to_shop_time() > 0
       and db.get_order(oid2)["created_at"] == "2026-08-10 16:00")

    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
