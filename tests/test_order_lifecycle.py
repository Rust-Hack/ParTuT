"""Жизненный цикл заказа: статусы, идемпотентность, кэшбэк, отмена, возвраты."""
import io as _io

from _common import db, client, Checker, as_user, as_admin

CLIENT = 555


def _чек(oid):
    """/api/receipt как его вызывает покупатель — тем же путём, что и в бою."""
    return client.post("/api/receipt",
                       data={"initData": "x", "order_id": str(oid),
                             "file": (_io.BytesIO(b"\x89PNG\r\n\x1a\nphoto"), "check.jpg")},
                       content_type="multipart/form-data")


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

    c6 = Checker("F. Повторный чек не воскрешает заказ и не задваивает кэшбэк")
    # Заказ дошёл до 'issued' обычным путём (paid → confirmed → issued),
    # кэшбэк уже начислен. Чек по нему присылают ЕЩЁ РАЗ — например, старым
    # запросом, который завис в пути, или потому что не заметили, что заказ
    # уже выдан. Раньше это молча откатывало статус на 'paid' — с кнопками
    # «Подтвердить»/«Выдать» СНОВА доступными продавцу.
    as_admin()
    oid, pid = make_order("new")
    as_user(CLIENT, "vasya")
    r = _чек(oid)
    c6("первый чек принят", r.get_json().get("ok") is True)
    c6("заказ стал paid", db.get_order(oid)["status"] == "paid")
    as_admin()
    act(oid, "confirm")
    before = db.get_coins(CLIENT)
    act(oid, "issued")
    gained = db.get_coins(CLIENT) - before
    c6("кэшбэк начислен один раз", gained > 0 and db.get_order(oid)["status"] == "issued")

    as_user(CLIENT, "vasya")
    r = _чек(oid)
    c6("повторный чек на уже выданный заказ отклонён", r.status_code == 409)
    c6("заказ остался issued, не откатился на paid", db.get_order(oid)["status"] == "issued")
    c6("кэшбэк не начислился второй раз", db.get_coins(CLIENT) == before + gained)

    # Тот же сценарий для отменённого заказа: склад и монеты уже вернулись,
    # чек «оживить» его не должен.
    as_admin()
    oid2, _ = make_order("new")
    db.cancel_order(oid2, ["new"])
    as_user(CLIENT, "vasya")
    r = _чек(oid2)
    c6("чек на отменённый заказ отклонён", r.status_code == 409)
    c6("отменённый заказ не воскрес", db.get_order(oid2)["status"] == "canceled")

    c7 = Checker("G. Отмена заказа: сбой у одного продавца не молчит остальных")
    # Раньше весь цикл рассылки был в одном try/except — если первый продавец
    # заблокировал бота, исключение прерывало цикл, и остальные продавцы того
    # же города вообще не узнавали об отмене (продолжили бы готовить заказ).
    from partut.integrations import tgsend
    from partut import config
    db.add_staff(90101, city="minsk")
    db.add_staff(90102, city="minsk")
    config.refresh_staff()
    real_send = tgsend.tg.send_message
    dошли = []

    def flaky_send(cid, text, **kw):
        if cid == 90101:
            raise RuntimeError("бот заблокирован")
        dошли.append(cid)
    tgsend.tg.send_message = flaky_send
    try:
        as_admin()
        oid, pid = make_order("new")
        as_user(CLIENT, "vasya")
        r = client.post("/api/order/cancel", json={"initData": "x", "order_id": oid})
        c7("отмена всё равно прошла", r.status_code == 200)
        c7("заказ отменён", db.get_order(oid)["status"] == "canceled")
        c7("второй продавец узнал об отмене, несмотря на сбой у первого", 90102 in dошли)
    finally:
        tgsend.tg.send_message = real_send
        db.remove_staff(90101); db.remove_staff(90102)
        config.refresh_staff()

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails + c7.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
