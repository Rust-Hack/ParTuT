"""Себестоимость и прибыль.

Главное здесь — честность цифры. Незаполненная закупочная цена означает
«не знаю», а не «досталось даром»: если считать её нулём, магазин будет
рисовать себе прибыль, которой нет, и решения о закупке станут хуже, чем
вообще без цифры.
"""
from _common import db, client, server, Checker, as_user, as_admin


BUYER = 9501


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    server._cache_bust()


def _sell(pid, name, price, cost, qty=1):
    """Выданный заказ: закупка фиксируется в позиции, как в бою."""
    oid = db.create_order(BUYER, "buyer", "Минск",
                          [{"id": pid, "name": name, "price": price, "cost": cost, "qty": qty}],
                          price * qty, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE id = %s"), (oid,))
    conn.commit(); conn.close()
    return oid


def run():
    c = Checker("Прибыль и закупочная цена")
    _clean()
    as_admin()

    # Товар с закупкой заводится через админку целиком.
    r = client.post("/api/admin/product", json={"initData": "x", "city": "Минск", "category": "podsystem",
                                                "name": "МаржаПод", "price": "20", "cost": "12", "stock": "5"})
    c("товар с закупкой создан", (r.get_json() or {}).get("ok"))
    pid = (r.get_json() or {}).get("id")
    c("закупка сохранена", abs(float(db.get_product(pid)["cost"]) - 12.0) < 0.001)

    server._cache_bust()
    prod = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                if p["id"] == pid)
    c("админка видит закупку", abs(prod["cost"] - 12.0) < 0.001)
    # А витрина — нет: по закупке читается наша наценка.
    shopper = next(p for p in client.get("/api/products").get_json() if p["id"] == pid)
    c("покупателю закупка не видна", "cost" not in shopper)

    # Закупку можно поправить в редакторе.
    client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "cost", "value": "13,5"})
    c("закупка правится (и запятая понимается)", abs(float(db.get_product(pid)["cost"]) - 13.5) < 0.001)
    client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "cost", "value": "12"})

    # --- Считаем прибыль ---
    _sell(pid, "МаржаПод", 20.0, 12.0, qty=2)          # +16 прибыли с 40 выручки
    s = db.get_business_stats()
    c("выручка посчитана", abs(s["revenue"] - 40.0) < 0.01)
    c("прибыль посчитана", abs(s["profit"] - 16.0) < 0.01)
    c("наценка посчитана", abs(s["margin"] - 40.0) < 0.1)
    c("неизвестной выручки нет", s["revenue_unknown_cost"] == 0)

    # --- Товар без закупки в прибыль не попадает ---
    r = client.post("/api/admin/product", json={"initData": "x", "city": "Минск", "category": "podsystem",
                                                "name": "БезЗакупки", "price": "30", "stock": "5"})
    pid2 = (r.get_json() or {}).get("id")
    c("без закупки товар создаётся", pid2 is not None)
    c("закупка равна нулю = не заполнена", float(db.get_product(pid2)["cost"]) == 0)

    _sell(pid2, "БезЗакупки", 30.0, 0.0)
    s = db.get_business_stats()
    c("выручка выросла", abs(s["revenue"] - 70.0) < 0.01)
    c("ПРИБЫЛЬ НЕ ВЫРОСЛА — иначе это выдумка", abs(s["profit"] - 16.0) < 0.01)
    c("и владельцу сказано, сколько выручки без закупки", abs(s["revenue_unknown_cost"] - 30.0) < 0.01)
    c("наценка считается только по известному", abs(s["margin"] - 40.0) < 0.1)

    top = {t["name"]: t for t in s["top"]}
    c("в топе у товара с закупкой есть прибыль", abs(top["МаржаПод"]["profit"] - 16.0) < 0.01)
    c("а у товара без закупки — прочерк, а не ноль", top["БезЗакупки"]["profit"] is None)

    # --- Закупка фиксируется на момент продажи ---
    # Иначе завтрашнее подорожание переписало бы прибыль по вчерашним продажам.
    client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "cost", "value": "18"})
    s2 = db.get_business_stats()
    c("прошлая прибыль не поехала от новой закупки", abs(s2["profit"] - 16.0) < 0.01)

    # Новая продажа считается уже по новой цене.
    _sell(pid, "МаржаПод", 20.0, 18.0)
    s3 = db.get_business_stats()
    c("новая продажа считается по новой закупке", abs(s3["profit"] - 18.0) < 0.01)

    # --- Закупка не должна утекать покупателю ---
    # Раньше здесь стояло обратное: закупка приходила всем, а витрина её просто
    # не рисовала. Но /api/products открыт без входа — прочитать наценку мог кто
    # угодно, не заходя в приложение. Теперь поле вырезает сервер.
    as_user(BUYER)
    me_prod = client.get("/api/products").get_json()
    c("закупка покупателю не приходит", all("cost" not in p for p in me_prod))
    c("и число ожидающих тоже", all("waiting" not in p for p in me_prod))
    c("но цена и остаток на месте", "price" in me_prod[0] and "stock" in me_prod[0])

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
