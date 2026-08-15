"""Точки самовывоза: админ заводит список, покупатель выбирает при заказе.

Главное, что проверяем: точка сверяется со списком города. Иначе в заказ попадёт
любой присланный текст, и продавец поедет по несуществующему адресу.
"""
from _common import db, client, server, Checker, as_user, as_admin, deny_admin


BUYER = 7701


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM pickup_points")
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM delivery_methods")
    conn.commit(); conn.close()
    server._cache_bust()


def run():
    c = Checker("Точки самовывоза: настройка")
    _clean()
    as_admin()

    r = client.post("/api/admin/point", json={"initData": "x", "city": "Минск",
                                              "address": "ул. Немига 5", "note": "10:00–21:00"})
    c("точка добавлена", (r.get_json() or {}).get("ok"))
    client.post("/api/admin/point", json={"initData": "x", "city": "Минск", "address": "пр. Победителей 9"})
    client.post("/api/admin/point", json={"initData": "x", "city": "Туров", "address": "ул. Ленина 1"})

    минские = db.get_pickup_points("Минск")
    c("у Минска две точки", len(минские) == 2)
    c("примечание сохранено", минские[0]["note"] == "10:00–21:00")
    c("у каждого города свои точки", [p["address"] for p in db.get_pickup_points("Туров")] == ["ул. Ленина 1"])
    c("чужой город не подмешивается", "ул. Ленина 1" not in [p["address"] for p in минские])

    c("без адреса не добавляется",
      client.post("/api/admin/point", json={"initData": "x", "city": "Минск", "address": "  "}).status_code == 400)

    as_user(BUYER); deny_admin()
    c("посторонний не может добавить точку",
      client.post("/api/admin/point", json={"initData": "x", "city": "Минск", "address": "хакер"}).status_code == 403)

    # Витрина получает способы и точки одним ответом.
    d = client.get("/api/delivery?city=Минск").get_json()
    c("ответ содержит точки", len(d["points"]) == 2)
    c("и способы", "methods" in d)
    c("точки отдаются с примечанием", d["points"][0]["note"] == "10:00–21:00")

    # --- Заказ ---
    c2 = Checker("Заказ с выбором точки")
    as_admin()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "", 0, True, 0, needs_point=True)
    server._cache_bust()
    method = db.get_delivery_methods("Минск")[0]
    c2("флаг «выбирает точку» сохранён", bool(method["needs_point"]))

    pid = db.add_product("Минск", "pods", "ТочкаПод", 10.0, 5)
    точка = db.get_pickup_points("Минск")[0]
    as_user(BUYER, "buyer")
    db.set_age_ok(BUYER)

    заказ = {"initData": "x", "city": "Минск", "delivery_method_id": method["id"],
             "payment_method": "cash", "items": [{"id": pid, "qty": 1}]}

    r = client.post("/api/order", json=заказ)
    c2("без точки заказ не проходит", r.status_code == 400 and r.get_json()["error"] == "no_point")

    r = client.post("/api/order", json={**заказ, "pickup_point_id": 999999})
    c2("выдуманная точка отвергнута", r.status_code == 400 and r.get_json()["error"] == "bad_point")

    чужая = db.get_pickup_points("Туров")[0]["id"]
    r = client.post("/api/order", json={**заказ, "pickup_point_id": чужая})
    c2("точка чужого города отвергнута", r.status_code == 400 and r.get_json()["error"] == "bad_point")

    r = client.post("/api/order", json={**заказ, "pickup_point_id": точка["id"]})
    d = r.get_json()
    c2("с правильной точкой заказ оформлен", d.get("ok"))
    заказ_в_базе = db.get_orders_by_user(BUYER)[0]
    c2("адрес точки записан в заказ", заказ_в_базе["delivery_address"] == "ул. Немига 5")

    # Адрес хранится строкой, поэтому удаление точки не портит прошлые заказы.
    as_admin()
    client.post("/api/admin/point/delete", json={"initData": "x", "id": точка["id"]})
    c2("точка удалена", len(db.get_pickup_points("Минск")) == 1)
    c2("прошлый заказ не пострадал", db.get_orders_by_user(BUYER)[0]["delivery_address"] == "ул. Немига 5")

    # Прошлый выбор подставится в следующий раз — механизм тот же, что у адресов.
    as_user(BUYER)
    prefill = db.delivery_prefill(BUYER)
    c2("прошлая точка запомнена для подстановки", prefill["addresses"].get("Самовывоз") == "ул. Немига 5")

    _clean()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
