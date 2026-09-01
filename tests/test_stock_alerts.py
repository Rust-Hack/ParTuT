"""«Сообщить о поступлении»: подписка и рассылка, когда товар вернулся.

Раньше покупатель, попавший на закончившийся товар, просто уходил. Теперь он
оставляет заявку, а продавец видит, сколько человек ждут — что и завозить.
"""
from _common import db, client, Checker, as_user, as_admin, SENT, reset_sent

from partut.integrations import tgsend

from partut import cache


BUYER = 660001
BUYER2 = 660002


def run():
    c = Checker("Сообщить о поступлении")

    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    cache.bust()
    # Рассылка уходит в фоновом потоке — в тесте выполняем сразу, иначе проверка
    # обгонит поток и результат будет случайным.
    real_bg = tgsend.bg
    tgsend.bg = lambda fn, *a, **k: fn(*a, **k)

    pid = db.add_product("Минск", "pods", "ЖдуновТовар", 20, 0)     # закончился
    have = db.add_product("Минск", "pods", "ЕстьТовар", 20, 5)      # в наличии

    # --- Подписка ---
    as_user(BUYER, "buyer")
    r = client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    d = r.get_json()
    c("заявка принята", d.get("ok") and d.get("in_stock") is False)
    c("записана в базе", db.alerts_of_user(BUYER) == [pid])

    r = client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    c("повторное нажатие не ломает", (r.get_json() or {}).get("ok"))
    c("дубля не появилось", db.alerts_of_user(BUYER) == [pid])

    r = client.post("/api/notify-me", json={"initData": "x", "product_id": have})
    c("на товар в наличии подписка не нужна", (r.get_json() or {}).get("in_stock") is True)
    c("и не записалась", db.alerts_of_user(BUYER) == [pid])

    c("несуществующий товар → 404",
      client.post("/api/notify-me", json={"initData": "x", "product_id": 987654}).status_code == 404)
    c("мусор вместо id → 400",
      client.post("/api/notify-me", json={"initData": "x", "product_id": "абв"}).status_code == 400)

    as_user(BUYER2, "buyer2")
    client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    c("ждут двое", db.stock_alert_counts().get(pid) == 2)

    # Продавцу видно, сколько ждут — ради этого счётчик и нужен. Кэш каталога
    # сбрасывается самой подпиской, руками его тут НЕ чистим: иначе тест не
    # заметит, если инвалидацию забудут, и продавец увидит старое число.
    as_admin()
    prod = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                if p["id"] == pid)
    c("счётчик ожидающих виден сразу, без ожидания кэша", prod["waiting"] == 2)
    # А покупателю это знать незачем: сколько человек ждут товар — наша кухня.
    shopper = next(p for p in client.get("/api/products").get_json() if p["id"] == pid)
    c("покупателю счётчик ожидающих не показываем", "waiting" not in shopper)
    c("и закупочную цену тоже", "cost" not in shopper)

    # Витрина должна знать, что покупатель уже подписан.
    as_user(BUYER, "buyer")
    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c("подписки приходят покупателю", me.get("alerts") == [pid])

    # --- Товар вернулся ---
    reset_sent()
    as_admin()
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "stock", "value": 7})
    c("остаток пополнен", (r.get_json() or {}).get("ok"))

    got = {chat for chat, _text, _pm in SENT}
    c("сообщили первому", BUYER in got)
    c("сообщили второму", BUYER2 in got)
    text = next((t for chat, t, _ in SENT if chat == BUYER), "")
    c("в сообщении есть название", "ЖдуновТовар" in text)

    c("подписки погашены", db.stock_alert_counts().get(pid) is None)
    c("повторно не рассылаем", not db.stock_alerts_ready())

    # Второй раз то же событие не должно ничего слать.
    reset_sent()
    client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "stock", "value": 9})
    c("второй раз молчим", not SENT)

    tgsend.bg = real_bg
    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    return c.fails


def run_send_failure_keeps_subscription():
    """Раньше вся пачка подписок на товар снималась одним махом ПОСЛЕ рассылки,
    даже для тех, кому отправка упала (заблокировал бота) — человек ждал
    уведомление, но не получал его ни разу и тихо терял подписку без права
    переспросить. Теперь снимается только то, что реально дошло."""
    c = Checker("Сбой отправки не теряет подписку")
    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    cache.bust()
    real_bg = tgsend.bg
    tgsend.bg = lambda fn, *a, **k: fn(*a, **k)
    real_send = tgsend.tg.send_message

    pid = db.add_product("Минск", "pods", "ГлючныйТовар", 20, 0)
    as_user(BUYER, "buyer")
    client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    as_user(BUYER2, "buyer2")
    client.post("/api/notify-me", json={"initData": "x", "product_id": pid})

    def flaky(cid, text, **kw):
        if cid == BUYER:
            raise RuntimeError("бот заблокирован")
        real_send(cid, text, **kw)
    tgsend.tg.send_message = flaky

    try:
        reset_sent()
        as_admin()
        r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "stock", "value": 3})
        c("запрос всё равно успешен, сбой у одного не роняет ручку",
          (r.get_json() or {}).get("ok"))
        got = {chat for chat, _t, _p in SENT}
        c("второму сообщили", BUYER2 in got)
        c("первому не дошло — send упал", BUYER not in got)
        c("подписка первого осталась — попробуем при следующем завозе",
          db.alerts_of_user(BUYER) == [pid])
        c("подписка второго снята — сообщение реально дошло",
          db.alerts_of_user(BUYER2) == [])
    finally:
        tgsend.tg.send_message = real_send
        tgsend.bg = real_bg
        conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    return c.fails


def run_concurrent_flush_does_not_double_notify():
    """Завоз двух позиций подряд запускает _flush_stock_alerts дважды почти
    одновременно (фон дёргается на каждое действие продавца, меняющее
    остаток). Без замка оба захода читали бы одну и ту же готовность и
    слали письмо дважды."""
    c = Checker("Гонка: параллельный завоз не шлёт письмо дважды")
    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    cache.bust()

    import time
    import threading
    from partut.web import server as _server

    pid = db.add_product("Минск", "pods", "ГоночныйТовар", 20, 3)   # уже в наличии
    db.add_stock_alert(pid, BUYER)

    real_ready = db.stock_alerts_ready

    def slow_ready():
        # Пауза внутри чтения готовности: без замка оба потока успели бы
        # прочитать одну и ту же готовность до того, как первый её погасит.
        # С замком второй поток в это время просто ждёт своей очереди снаружи.
        time.sleep(0.15)
        return real_ready()
    db.stock_alerts_ready = slow_ready

    reset_sent()
    try:
        t1 = threading.Thread(target=_server._flush_stock_alerts)
        t2 = threading.Thread(target=_server._flush_stock_alerts)
        t1.start()
        time.sleep(0.02)   # первый поток успевает войти в замок раньше второго
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        got = [chat for chat, _t, _p in SENT if chat == BUYER]
        c("сообщили ровно один раз, не дважды", len(got) == 1)
        c("подписка снята один раз", db.alerts_of_user(BUYER) == [])
    finally:
        db.stock_alerts_ready = real_ready
        conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_send_failure_keeps_subscription()
                    + run_concurrent_flush_does_not_double_notify()) else 0)
