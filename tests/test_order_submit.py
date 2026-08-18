"""Сабмит заказа /api/order: создаётся, склад списывается, уведомления — в фоне (не в ответе).

Отдельно проверяем БАТЧИНГ: заказ должен укладываться в считанные подключения к базе.
На Neon каждое подключение — это поездка по сети, и раньше их было ~20 → кнопка «Оформить» висла.
"""
import time
from _common import db, client, server, Checker, as_user

CLIENT = 6161
COINS_CLIENT = 6162
VAR_CLIENT = 6163


def _count_connects():
    """Подменяет db.connect на счётчик. Возвращает (счётчик-список, функция-возврат)."""
    calls = []
    orig = db.connect

    def counting():
        calls.append(1)
        return orig()

    db.connect = counting
    return calls, (lambda: setattr(db, "connect", orig))


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
    c("способ получения сохранён", o and o["delivery_method"] == "Самовывоз")
    c(f"ответ быстрый ({elapsed*1000:.0f}мс), уведомления в фоне", elapsed < 0.4)

    time.sleep(0.6)                     # даём медленному фоновому потоку завершиться
    server.tg.send_message = orig

    # --- Батчинг: сколько раз ходим в базу за один заказ ---
    # Считаем ТОЛЬКО путь запроса. Уведомления продавцу и клиенту уходят фоновым
    # потоком и тоже читают заказ из базы; раньше их тут не было (заказ картой
    # ждал чека и никого не оповещал), и счёт случайно совпадал. Теперь заказ
    # картой уведомляет продавца сразу, поэтому фон нужно отключить явно —
    # иначе тест меряет не скорость оформления, а гонку с чужим потоком.
    orig_bg = server._bg
    server._bg = lambda fn, *a, **k: None
    calls, restore = _count_connects()
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "card", "items": [{"id": pid, "qty": 1}]})
    restore()
    server._bg = orig_bg
    n = len(calls)
    d = r.get_json() or {}
    c(f"заказ укладывается в ≤3 подключения к базе (сейчас {n})", d.get("ok") and n <= 3)

    # --- Заказ картой: ждёт чек, статус new ---
    c("карта → needs_receipt", d.get("ok") and d.get("needs_receipt") is True)
    c("карта → статус new (ждёт чек)", db.get_order(d["order_id"])["status"] == "new")

    # --- Списание монет одной транзакцией ---
    c2 = Checker("Оплата монетами при оформлении")
    as_user(COINS_CLIENT, "coiner")
    db.set_age_ok(COINS_CLIENT)
    db.add_coins(COINS_CLIENT, 500)                 # 500 монет = 5 Br при COIN_VALUE 0.01
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c2("ответ ok", d.get("ok"))
    c2("списано 500 монет", d.get("coins_used") == 500)
    c2("скидка 5 Br", abs(float(d.get("discount", 0)) - 5) < 0.01)
    c2("итого 25 − 5 = 20", abs(float(d.get("total", 0)) - 20) < 0.01)
    c2("баланс обнулён", db.get_coins(COINS_CLIENT) == 0)
    c2("coins_used записан в заказ", db.get_order(d["order_id"])["coins_used"] == 500)

    # монет нет — заказ всё равно оформляется, просто без скидки
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c2("без монет: заказ ok, скидки нет", d.get("ok") and d.get("coins_used") == 0
       and abs(float(d.get("total", 0)) - 25) < 0.01)

    # монет больше, чем стоит заказ: списываем не больше суммы товаров
    db.add_coins(COINS_CLIENT, 10000)               # 100 Br монетами на заказ в 25 Br
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c2("списано не больше суммы заказа", d.get("coins_used") == 2500)
    c2("итого 0", abs(float(d.get("total", 0))) < 0.01)
    c2("остаток монет 7500", db.get_coins(COINS_CLIENT) == 7500)

    # --- Товар со вкусами: списывается нужный вариант + пересчёт общего остатка ---
    c3 = Checker("Заказ товара со вкусами (варианты)")
    as_user(VAR_CLIENT, "varbuyer")
    db.set_age_ok(VAR_CLIENT)
    vpid = db.add_product("ordercity", "disposable", "VarPod", 30, 0)
    db.add_variant(vpid, "Мята", 5)
    db.add_variant(vpid, "Вишня", 3)
    db.recalc_product_stock(vpid)
    c3("общий остаток = 8", db.get_product(vpid)["stock"] == 8)

    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": vpid, "qty": 2, "flavor": "Мята"}]})
    d = r.get_json() or {}
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(vpid)}
    c3("ответ ok", d.get("ok"))
    c3("Мята 5 → 3", stocks.get("Мята") == 3)
    c3("Вишня не тронута", stocks.get("Вишня") == 3)
    c3("общий остаток пересчитан 8 → 6", db.get_product(vpid)["stock"] == 6)
    c3("вкус попал в название позиции",
       "Мята" in (db.get_order(d["order_id"])["items"] or ""))

    # заказ больше, чем есть вкуса — режется по остатку, а не уходит в минус
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": vpid, "qty": 99, "flavor": "Вишня"}]})
    d = r.get_json() or {}
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(vpid)}
    c3("больше остатка → продали только 3", abs(float(d.get("total", 0)) - 90) < 0.01)
    c3("Вишня обнулилась, не ушла в минус", stocks.get("Вишня") == 0)

    # несуществующий вкус — не оформляем (корзина считается пустой)
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": vpid, "qty": 1, "flavor": "Нетакого"}]})
    c3("несуществующий вкус → 400 empty", r.status_code == 400)

    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
