"""Правка способа доставки на месте: /api/admin/delivery/update."""
from _common import db, client, Checker, as_admin, as_user

from partut import cache


def upd(payload):
    r = client.post("/api/admin/delivery/update", json={"initData": "x", **payload})
    return r.status_code, (r.get_json() or {})


def run():
    as_admin()
    c = Checker("Правка способа доставки")

    db.add_delivery_method("testcity", "Самовывоз", False, "", "ул. Старая", 0, True)
    m = db.get_delivery_methods("testcity")[0]
    mid = m["id"]

    sc, d = upd({"id": mid, "name": "Доставка курьером", "needs_address": True,
                 "address_label": "Адрес", "pickup_address": "", "fee": "3.5",
                 "needs_payment": False})
    c("update → ok", sc == 200 and d.get("ok"))

    m2 = db.get_delivery_method(mid)
    c("имя обновилось", m2["name"] == "Доставка курьером")
    c("needs_address = 1", m2["needs_address"] == 1)
    c("address_label обновился", m2["address_label"] == "Адрес")
    c("fee = 3.5", abs(float(m2["fee"]) - 3.5) < 0.01)
    c("needs_payment = 0", m2["needs_payment"] == 0)

    sc, d = upd({"id": mid, "name": "   "})
    c("пустое имя → 400", sc == 400)
    sc, d = upd({"id": 999999, "name": "Нечто"})
    c("несуществующий id → 400", sc == 400)
    sc, d = upd({"id": "abc", "name": "X"})
    c("кривой id → 400", sc == 400)

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)


def run_phone():
    """На доставку телефон обязателен, на самовывоз — нет.

    Курьер стоит у подъезда, а связь с покупателем только через Telegram,
    который может быть выключен: заказ уезжает обратно, деньги и время потеряны.
    """
    c = Checker("Телефон для курьера")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()

    db.add_delivery_method("Минск", "Курьер", True, "Адрес", "", 3.0, True, 0)
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    methods = {m["name"]: m["id"] for m in db.get_delivery_methods("Минск")}
    pid = db.add_product("Минск", "pods", "ТелефонПод", 20.0, 50)
    db.set_age_ok(4401)
    as_user(4401, "buyer")
    cache.bust()

    def заказ(mid, **kw):
        body = {"initData": "x", "city": "Минск", "delivery_method_id": mid,
                "payment_method": "cash", "items": [{"id": pid, "qty": 1}]}
        body.update(kw)
        return client.post("/api/order", json=body)

    r = заказ(methods["Курьер"], delivery_address="ул. Тестовая 1")
    c("курьер без телефона — отказ", r.status_code == 400 and r.get_json()["error"] == "no_phone")
    r = заказ(methods["Курьер"], delivery_address="ул. Тестовая 1", phone="12")
    c("огрызок вместо номера тоже не проходит", r.status_code == 400)
    r = заказ(methods["Курьер"], delivery_address="ул. Тестовая 1", phone="+375 29 111-22-33")
    c("с телефоном заказ проходит", r.get_json().get("ok"))
    c("телефон сохранён в заказе",
      "111" in (db.get_order(r.get_json()["order_id"])["phone"] or ""))

    r = заказ(methods["Самовывоз"])
    c("самовывоз без телефона — можно, человек придёт сам", r.get_json().get("ok"))

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()
    return c.fails
