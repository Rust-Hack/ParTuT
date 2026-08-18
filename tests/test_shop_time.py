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
    import bot as botmod
    db.shop_now = lambda: НОЧЬ
    try:
        c4("бот смотрит на те же часы, что и база", botmod._local_now() == НОЧЬ)
    finally:
        db.shop_now = настоящие
    c4("смещение у бота и базы одно", botmod.SUMMARY_TZ_OFFSET == db.SHOP_TZ_OFFSET)

    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
