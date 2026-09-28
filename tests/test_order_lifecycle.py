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


def _boom_after_marker(marker):
    """Патчит db.connect: запрос СРАЗУ ПОСЛЕ того, где в SQL встретился marker,
    взрывается — остальное в этой же транзакции не выполняется и не коммитится
    (conn.rollback() в except). Так проверяем НАСТОЯЩИЙ откат «всё или ничего»,
    а не гадаем номер запроса по счёту (он меняется от одной правки кода)."""
    orig_connect = db.connect
    state = {"armed": False}

    class _КурсорСВзрывателем:
        """sqlite3.Cursor не даёт подменить .execute на экземпляре (атрибут
        только для чтения) — оборачиваем объект вместо патча метода."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a, **k):
            if state["armed"]:
                state["armed"] = False
                raise RuntimeError("симулированный сбой базы")
            if marker in sql:
                state["armed"] = True
            return self._real.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def patched():
        conn = orig_connect()
        orig_cursor = conn.cursor
        conn.cursor = lambda *a, **k: _КурсорСВзрывателем(orig_cursor(*a, **k))
        return conn
    db.connect = patched
    return lambda: setattr(db, "connect", orig_connect)


def run_атомарность_выдачи_и_отмены():
    """Сбой БАЗЫ сразу после того, как статус в памяти транзакции сменился на
    issued/canceled — но ДО коммита. Раньше статус коммитился первым же
    отдельным запросом (set_order_status_if), и сбой в начислениях следом
    оставлял заказ 'issued' без единого бонуса — а повтор отклонялся (409,
    «не тот статус»), доделать было нечем. Теперь это одна транзакция: сбой
    посередине откатывает ВСЁ, включая сам статус, и чистый повтор доводит
    операцию до конца ровно один раз."""
    as_admin()
    c = Checker("H. Выдача: сбой посередине откатывает всё, повтор доводит до конца")
    db.add_coins(CLIENT, -db.get_coins(CLIENT))
    oid, pid = make_order("paid", price=100, qty=1)
    before_coins = db.get_coins(CLIENT)

    undo = _boom_after_marker("status = 'issued'")
    try:
        raised = False
        try:
            db.issue_order(oid, ["paid", "confirmed"])
        except RuntimeError:
            raised = True
    finally:
        undo()
    c("сбой действительно произошёл", raised)
    c("статус НЕ сменился — откат целиком, не наполовину", db.get_order(oid)["status"] == "paid")
    c("кэшбэк не начислен (транзакция не коммитилась)", db.get_coins(CLIENT) == before_coins)

    # Повтор — уже без сбоя — обязан довести до конца, а не получить 409
    # («статус уже не тот»), как было раньше.
    order2, _ = db.issue_order(oid, ["paid", "confirmed"])
    c("повтор проходит", order2 is not None and db.get_order(oid)["status"] == "issued")
    c("кэшбэк начислен ровно один раз", db.get_coins(CLIENT) > before_coins)
    gained = db.get_coins(CLIENT) - before_coins
    order3, _ = db.issue_order(oid, ["paid", "confirmed"])
    c("повторная выдача уже выданного не проходит и не начисляет снова",
      order3 is None and db.get_coins(CLIENT) == before_coins + gained)

    c2 = Checker("I. Отмена: тот же сбой — тот же откат целиком")
    oid2, pid2 = make_order("paid", price=50, qty=2, coins_used=10)
    db.add_coins(CLIENT, 100)
    before_coins2 = db.get_coins(CLIENT)
    before_stock = db.get_product(pid2)["stock"]

    undo = _boom_after_marker("status = 'canceled'")
    try:
        raised = False
        try:
            db.cancel_order(oid2, ["new", "paid", "confirmed"])
        except RuntimeError:
            raised = True
    finally:
        undo()
    c2("сбой произошёл", raised)
    c2("статус остался paid", db.get_order(oid2)["status"] == "paid")
    c2("склад НЕ вернулся (транзакция не коммитилась)", db.get_product(pid2)["stock"] == before_stock)
    c2("монеты НЕ вернулись", db.get_coins(CLIENT) == before_coins2)

    canceled = db.cancel_order(oid2, ["new", "paid", "confirmed"])
    c2("повтор проходит", canceled is not None and db.get_order(oid2)["status"] == "canceled")
    c2("склад вернулся", db.get_product(pid2)["stock"] == before_stock + 2)
    c2("монеты вернулись ровно один раз", db.get_coins(CLIENT) == before_coins2 + 10)

    return c.fails + c2.fails


def run_отказ_без_обмана_про_возврат():
    """Сообщение об отказе не должно обещать возврат оплаты, которого не было.
    Раньше «Монеты и оплата возвращены» уходило ВСЕГДА для причины «товара не
    оказалось» — даже когда монет не тратили и денег никто не платил (наличные
    до выдачи, карта без загруженного чека). Магазин и не делает автоматический
    банковский возврат — обещать «уже вернули» нечестно."""
    from partut.integrations import tgsend
    c = Checker("J. Отказ: сообщение о возврате соответствует фактам")
    as_admin()
    sent = []
    orig = tgsend.tg.send_message
    tgsend.tg.send_message = lambda cid, text, **kw: sent.append(text)
    def reject_out(oid):
        r = client.post("/api/admin/order/status",
                        json={"initData": "x", "id": oid, "action": "reject", "reason": "out"})
        tgsend.дождаться_фона()   # уведомление уходит в фоне — дожидаемся, иначе sent пуст
        return r
    try:
        # Наличные, ничего не платили — про возврат денег речи быть не должно.
        oid, pid = make_order("paid")     # cash, coins_used=0
        reject_out(oid)
        text = sent[-1]
        c("монеты не упомянуты (их и не было)", "Монеты" not in text)
        c("про возврат денег ни слова (наличными ещё не платили)", "вернём" not in text and "возвращен" not in text)

        # Монеты были — про них сказать обязаны (это правда: coin-баланс вернулся).
        db.add_coins(CLIENT, 20)
        oid2, pid2 = make_order("paid", coins_used=5)
        reject_out(oid2)
        text2 = sent[-1]
        c("монеты упомянуты честно (они правда вернулись)", "Монеты возвращены" in text2)
        c("про банковский возврат по-прежнему ни слова (наличные)", "вернём" not in text2)

        # Карта БЕЗ чека — платежа ещё не было, обещать возврат тоже нельзя.
        oid3 = db.create_order(CLIENT, "vasya", "minsk",
                               [{"id": pid, "flavor": None, "name": "TestPod", "price": 10, "qty": 1}],
                               10, "")
        db.set_order_delivery(oid3, "Доставка", "", 0, "card", "", "")
        db.change_stock(pid, -1)
        db.set_order_status(oid3, "paid")
        reject_out(oid3)
        text3 = sent[-1]
        c("карта без чека: про возврат денег не сказано", "вернём" not in text3)
    finally:
        tgsend.tg.send_message = orig
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_атомарность_выдачи_и_отмены()
                    + run_отказ_без_обмана_про_возврат()) else 0)
