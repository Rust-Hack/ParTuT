"""Снять с витрины — не то же самое, что удалить.

Удаление было единственным способом убрать товар из продажи, а оно уносит
остаток, движения склада и отзывы. «Мы это больше не возим» и «этого у нас
не было» — разные события, и путать их нельзя: вернуть удалённое неоткуда.

Главное здесь — что снятое НЕ попадает покупателю ни одной дорогой: ни на
витрину, ни в заказ. Полагаться на то, что каждый экран не забудет фильтр,
нельзя, поэтому фильтрует сервер.
"""
from _common import db, client, Checker, as_user, as_admin, deny_admin

import cache


BUYER = 9401


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM models")
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()
    cache.bust()


def _catalog():
    cache.bust()
    return client.get("/api/products").get_json()


def _admin_list():
    cache.bust()
    return client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]


def _hide(pid, on=True):
    return client.post("/api/admin/product/update",
                       json={"initData": "x", "id": pid, "field": "hidden", "value": 1 if on else 0})


def run():
    c = Checker("Снятие с витрины")
    _clean()
    as_admin()

    pid = db.add_product("Минск", "disposable", "Elf Bar", 10.0, 7)
    c("сначала товар на витрине", any(p["id"] == pid for p in _catalog()))

    c("снятие принято", _hide(pid).get_json().get("ok"))
    c("с витрины пропал", all(p["id"] != pid for p in _catalog()))
    c("а у продавца остался", any(p["id"] == pid for p in _admin_list()))
    c("и помечен как снятый", next(p for p in _admin_list() if p["id"] == pid)["hidden"] is True)
    c("остаток на месте — это реальный товар на полке", db.get_product(pid)["stock"] == 7)

    # --- Заказать снятое нельзя ---
    # Заказ обязан быть валидным во всём остальном: иначе отказ придёт из-за
    # доставки, а проверка «снятое не продаётся» окажется пустой.
    c2 = Checker("Снятое не продаётся")
    as_user(BUYER)
    db.set_age_ok(BUYER)
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True)
    mid_d = db.get_delivery_methods("Минск")[0]["id"]
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid_d,
                                        "payment_method": "cash", "items": [{"id": pid, "qty": 1}]})
    d = r.get_json()
    c2("заказ из одного снятого товара не проходит", not d.get("ok"))
    c2("и причина — пустая корзина, а не доставка", d.get("error") == "empty")
    c2("остаток не тронут", db.get_product(pid)["stock"] == 7)

    # Снятое в корзине не должно ронять заказ целиком — остальное продаём.
    live = db.add_product("Минск", "disposable", "Другой", 12.0, 5)
    cache.bust()
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid_d,
                                        "payment_method": "cash",
                                        "items": [{"id": pid, "qty": 1}, {"id": live, "qty": 1}]})
    d = r.get_json()
    c2("живой товар из той же корзины продан", d.get("ok"))
    c2("и списан только он", db.get_product(live)["stock"] == 4 and db.get_product(pid)["stock"] == 7)
    c2("снятого в заказе нет",
      all(it["id"] != pid for it in __import__("json").loads(db.get_order(d["order_id"])["items"])))

    # --- Возврат на витрину ---
    c3 = Checker("Возврат")
    as_admin()
    c3("вернули", _hide(pid, False).get_json().get("ok"))
    c3("снова на витрине", any(p["id"] == pid for p in _catalog()))

    # --- Модель целиком ---
    c4 = Checker("Модель на всех точках")
    mid = db.add_model("podsystem", "XROS")
    a = db.add_product_from_model(mid, "Минск", 30.0, stock=3)
    b = db.add_product_from_model(mid, "Туров", 32.0, stock=2)
    r = client.post("/api/admin/model/hide", json={"initData": "x", "id": mid, "hidden": True})
    d = r.get_json()
    c4("снята одним действием", d.get("ok") and d["count"] == 2)
    ids = {p["id"] for p in _catalog()}
    c4("нет на первой точке", a not in ids)
    c4("нет и на второй", b not in ids)
    c4("остатки целы", db.get_product(a)["stock"] == 3 and db.get_product(b)["stock"] == 2)
    client.post("/api/admin/model/hide", json={"initData": "x", "id": mid, "hidden": False})
    c4("вернулась везде", {a, b} <= {p["id"] for p in _catalog()})
    c4("несуществующая модель — 404",
      client.post("/api/admin/model/hide", json={"initData": "x", "id": 999999}).status_code == 404)

    # --- Права ---
    deny_admin()
    c4("посторонний не снимет", _hide(pid).status_code == 403)
    c4("посторонний не увидит полный список",
      client.post("/api/admin/products", json={"initData": "x"}).status_code == 403)
    c4("и модель не снимет",
      client.post("/api/admin/model/hide", json={"initData": "x", "id": mid, "hidden": True}).status_code == 403)
    as_admin()

    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


def run_locations():
    """Точку продаж нельзя удалить, пока в ней висят открытые заказы.

    Проверка на товары была, а на заказы — нет: покупатель ждёт выдачи, а
    города, в котором он заказывал, больше не существует."""
    c = Checker("Удаление точки")
    _clean()
    as_admin()
    lid = db.add_location("Временная")

    oid = db.create_order(BUYER, "buyer", "Временная", [{"id": 1, "name": "X", "price": 5.0, "qty": 1}], 5.0, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'paid' WHERE id = %s"), (oid,))
    conn.commit(); conn.close()

    r = client.post("/api/admin/location/delete", json={"initData": "x", "id": lid})
    c("точку с открытым заказом не удалить",
      r.status_code == 400 and r.get_json()["error"] == "has_orders")
    c("и сказано, сколько их", r.get_json()["count"] == 1)

    db.set_order_status(oid, "issued")
    r = client.post("/api/admin/location/delete", json={"initData": "x", "id": lid})
    c("когда заказ выдан — удаляется", r.get_json().get("ok"))
    c("точки больше нет", all(l["name"] != "Временная" for l in db.get_locations()))

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_locations()) else 0)
