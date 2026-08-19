"""Сводка дня на входе в управление.

Продавец открывал управление и видел меню — чтобы понять, есть ли вообще
работа, надо было зайти в заказы и посчитать, потом в товары, потом в
статистику за месяц. Четыре числа, за которыми он приходит, теперь встречают
его сразу.

Числа обязаны совпадать с теми разделами, куда ведут плитки: сводка, которая
расходится со списком, хуже, чем её отсутствие.
"""
from _common import db, client, Checker, as_admin, deny_admin, real_auth, REAL_GET_USER

import auth

import cache

import config

SELLER = 7401


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def _order(city, status, total, when=None):
    oid = db.create_order(500, "kl", city, [{"id": 1, "name": "X", "price": total, "qty": 1}], total, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, oid))
    if when:
        cur.execute(db._q("UPDATE orders SET created_at = %s WHERE id = %s"), (when, oid))
    conn.commit(); conn.close()
    return oid


def _today():
    return client.post("/api/admin/today", json={"initData": "x"}).get_json()["today"]


def run():
    c = Checker("Сводка дня")
    _clean()
    as_admin()

    c("на пустом магазине всё по нулям",
      all(_today()[k] == 0 for k in ("waiting", "to_issue", "issued_today", "revenue_today")))

    _order("Минск", "paid", 15.0)          # ждёт продавца
    _order("Минск", "confirmed", 20.0)     # ждёт покупателя
    _order("Минск", "new", 30.0)           # ждёт чек — ход клиента, не наш
    _order("Минск", "issued", 25.0)        # выдан сегодня
    _order("Минск", "issued", 99.0, when="2020-01-01 10:00")   # выдан давно
    _order("Минск", "canceled", 50.0)

    t = _today()
    c("ждут подтверждения — только оплаченные", t["waiting"] == 1)
    c("к выдаче — подтверждённые", t["to_issue"] == 1)
    c("заказ без чека посчитан отдельно", t["unpaid"] == 1)
    c("выдано сегодня — один", t["issued_today"] == 1)
    c("выручка только за сегодня", t["revenue_today"] == 25.0)
    c("отменённый в выручку не попал", t["revenue_today"] != 75.0)

    # --- Склад ---
    c2 = Checker("Что завезти")
    db.add_product("Минск", "disposable", "Кончился", 10.0, 0)
    db.add_product("Минск", "disposable", "Мало", 10.0, 2)
    db.add_product("Минск", "disposable", "Хватает", 10.0, 50)
    cache.bust()
    t = _today()
    c2("кончившиеся посчитаны", t["out_stock"] == 1)
    c2("заканчивающиеся — тоже", t["low_stock"] == 1)
    c2("полные полки не считаются", t["out_stock"] + t["low_stock"] == 2)
    c2("порог тот же, что в статистике",
      t["low_stock"] == len(client.post("/api/admin/stats", json={"initData": "x"}).get_json()["stats"]["low_stock"]))

    # --- Продавец точки видит свою точку ---
    c3 = Checker("Сводка продавца точки")
    _order("Туров", "paid", 40.0)
    _order("Туров", "issued", 60.0)
    db.add_staff(SELLER, "Туров", "продавец")
    config.refresh_staff()
    real_auth()
    auth.get_user = lambda init: {"id": SELLER, "username": "seller"}
    t = _today()
    c3("город назван", t["city"] == "Туров")
    c3("считаются только свои ждущие", t["waiting"] == 1)
    c3("и своя выручка", t["revenue_today"] == 60.0)
    c3("чужие товары в остатке не учтены", t["out_stock"] == 0 and t["low_stock"] == 0)

    db.remove_staff(SELLER)
    config.refresh_staff()
    auth.get_user = REAL_GET_USER
    as_admin()

    deny_admin()
    c3("посторонний сводку не видит",
      client.post("/api/admin/today", json={"initData": "x"}).status_code == 403)
    as_admin()

    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
