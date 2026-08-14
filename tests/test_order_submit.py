"""Сабмит заказа /api/order: создаётся, склад списывается, уведомления — в фоне (не в ответе)."""
import time
from _common import db, client, server, Checker, as_user

CLIENT = 6161


def run():
    c = Checker("Оформление заказа (cash) + фоновые уведомления")
    as_user(CLIENT, "buyer", "Покупатель")
    db.set_age_ok(CLIENT)

    # товар и способ получения в одном городе
    pid = db.add_product("ordercity", "pods", "OrderPod", 25, 10)
    db.add_delivery_method("ordercity", "Самовывоз", False, "", "ул. Тест", 0, True)
    mid = db.get_delivery_methods("ordercity")[0]["id"]

    # делаем отправку в Telegram МЕДЛЕННОЙ: если бы она шла в ответе — запрос завис бы на 0.5с.
    orig = server.tg.send_message
    server.tg.send_message = lambda *a, **k: time.sleep(0.5)

    t0 = time.time()
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "items": [{"id": pid, "qty": 2}]})
    elapsed = time.time() - t0
    d = r.get_json() or {}

    c("ответ ok", r.status_code == 200 and d.get("ok"))
    oid = d.get("order_id")
    c("заказ создан", bool(oid))
    o = db.get_order(oid)
    c("статус paid (ждёт продавца)", o and o["status"] == "paid")
    c("склад списан 10 → 8", db.get_product(pid)["stock"] == 8)
    c("итого = 50", abs(float(d.get("total", 0)) - 50) < 0.01)
    c(f"ответ быстрый ({elapsed*1000:.0f}мс), уведомления в фоне", elapsed < 0.4)

    time.sleep(0.6)                     # даём медленному фоновому потоку завершиться
    server.tg.send_message = orig
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
