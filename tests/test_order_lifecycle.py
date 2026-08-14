"""Жизненный цикл заказа: статусы, идемпотентность, кэшбэк, отмена, возвраты."""
from _common import db, client, Checker, as_user, as_admin

CLIENT = 555


def make_order(status, price=10, qty=2, coins_used=0):
    """Создаёт заказ в нужном статусе, списывает склад. Возвращает (order_id, product_id)."""
    pid = db.add_product("minsk", "pods", "TestPod", price, 5)
    oid = db.create_order(CLIENT, "vasya", "minsk",
                          [{"id": pid, "flavor": None, "name": "TestPod", "price": price, "qty": qty}],
                          price * qty, "")
    db.set_order_delivery(oid, "Самовывоз", "", 0, "cash", "", "")
    db.change_stock(pid, -qty)
    if coins_used:
        db.set_order_coins_used(oid, coins_used)
        db.spend_coins(CLIENT, coins_used)
    if status != "new":
        db.set_order_status(oid, status)
    return oid, pid


def act(oid, action):
    r = client.post("/api/admin/order/status", json={"initData": "x", "id": oid, "action": action})
    return r.status_code, (r.get_json() or {})


def run():
    as_admin()
    c = Checker("A. 'new' (неоплачен картой) нельзя подтвердить/выдать")
    oid, pid = make_order("new")
    sc, d = act(oid, "confirm")
    c("new + confirm → 409", sc == 409 and d.get("error") == "closed")
    c("new + confirm: статус остался new", db.get_order(oid)["status"] == "new")
    sc, d = act(oid, "issued")
    c("new + issued → 409", sc == 409)
    c("new + issued: кэшбэк НЕ начислен", db.get_coins(CLIENT) == 0)
    sc, d = act(oid, "reject")
    c("new + reject → ok, склад вернулся", sc == 200 and db.get_product(pid)["stock"] == 5)

    c2 = Checker("B. paid → confirmed → issued (+идемпотентность)")
    db.add_coins(CLIENT, -db.get_coins(CLIENT))
    oid, pid = make_order("paid")
    sc, d = act(oid, "confirm")
    c2("paid + confirm → confirmed", sc == 200 and db.get_order(oid)["status"] == "confirmed")
    sc, d = act(oid, "confirm")
    c2("confirm повторно → 409", sc == 409)
    before = db.get_coins(CLIENT)
    sc, d = act(oid, "issued")
    c2("confirmed + issued → issued", sc == 200 and db.get_order(oid)["status"] == "issued")
    gained = db.get_coins(CLIENT) - before
    c2("кэшбэк начислен", gained > 0)
    sc, d = act(oid, "issued")
    c2("issued повторно → 409", sc == 409)
    c2("повторно не начислило", db.get_coins(CLIENT) == before + gained)

    c3 = Checker("C. paid → issued напрямую (быстрая продажа)")
    oid, pid = make_order("paid")
    sc, d = act(oid, "issued")
    c3("paid + issued → ok", sc == 200 and db.get_order(oid)["status"] == "issued")

    c4 = Checker("D. reject из paid: склад и монеты возвращаются")
    db.add_coins(CLIENT, 50)
    bal0 = db.get_coins(CLIENT)
    oid, pid = make_order("paid", coins_used=5)
    c4("монеты списаны при оформлении", db.get_coins(CLIENT) == bal0 - 5)
    sc, d = act(oid, "reject")
    c4("reject → canceled", sc == 200 and db.get_order(oid)["status"] == "canceled")
    c4("reject: склад вернулся", db.get_product(pid)["stock"] == 5)
    c4("reject: монеты вернулись", db.get_coins(CLIENT) == bal0)

    c5 = Checker("E. клиент отменяет свой заказ; чужой/подтверждённый — нельзя")
    as_user(CLIENT, "vasya")
    oid, pid = make_order("paid")
    r = client.post("/api/order/cancel", json={"initData": "x", "order_id": oid})
    c5("свой paid → отменён", r.status_code == 200 and db.get_order(oid)["status"] == "canceled")
    oid2, _ = make_order("paid")
    as_user(777, "chuzhoy")
    r = client.post("/api/order/cancel", json={"initData": "x", "order_id": oid2})
    c5("чужой заказ → 404", r.status_code == 404)
    c5("чужой остался paid", db.get_order(oid2)["status"] == "paid")
    as_user(CLIENT, "vasya")
    oid3, _ = make_order("confirmed")
    r = client.post("/api/order/cancel", json={"initData": "x", "order_id": oid3})
    c5("confirmed клиент отменить не может → too_late",
       r.status_code == 400 and (r.get_json() or {}).get("error") == "too_late")

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
