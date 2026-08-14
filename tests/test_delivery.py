"""Правка способа доставки на месте: /api/admin/delivery/update."""
from _common import db, client, Checker, as_admin


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
