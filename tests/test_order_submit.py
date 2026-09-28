"""Сабмит заказа /api/order: создаётся, склад списывается, уведомления — в фоне (не в ответе).

Отдельно проверяем БАТЧИНГ: заказ должен укладываться в считанные подключения к базе.
На Neon каждое подключение — это поездка по сети, и раньше их было ~20 → кнопка «Оформить» висла.
"""
import time
from _common import db, client, Checker, as_user, as_admin

from partut.integrations import tgsend
from partut import cache

CLIENT = 6161
COINS_CLIENT = 6162
VAR_CLIENT = 6163
PAY_CLIENT = 6164


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
    orig = tgsend.tg.send_message
    tgsend.tg.send_message = lambda *a, **k: time.sleep(0.5)

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
    tgsend.tg.send_message = orig

    # --- Батчинг: сколько раз ходим в базу за один заказ ---
    # Считаем ТОЛЬКО путь запроса. Уведомления продавцу и клиенту уходят фоновым
    # потоком и тоже читают заказ из базы; раньше их тут не было (заказ картой
    # ждал чека и никого не оповещал), и счёт случайно совпадал. Теперь заказ
    # картой уведомляет продавца сразу, поэтому фон нужно отключить явно —
    # иначе тест меряет не скорость оформления, а гонку с чужим потоком.
    orig_bg = tgsend.bg
    tgsend.bg = lambda fn, *a, **k: None
    calls, restore = _count_connects()
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "card", "items": [{"id": pid, "qty": 1}]})
    restore()
    tgsend.bg = orig_bg
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

    # монет намного больше, чем стоит заказ: списываем не больше 25% суммы
    # товаров (25 Br × 0.25 = 6.25 Br = 625 монет), а не всю сумму заказа.
    db.add_coins(COINS_CLIENT, 10000)               # 100 Br монетами на заказ в 25 Br
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c2("списано не больше 25% заказа (625 монет)", d.get("coins_used") == 625)
    c2("итого 25 − 6.25 = 18.75", abs(float(d.get("total", 0)) - 18.75) < 0.01)
    c2("остаток монет 9375", db.get_coins(COINS_CLIENT) == 9375)

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

    # Заказ больше, чем есть вкуса. Раньше он молча резался по остатку: человек
    # просил 99, получал 3 и узнавал об этом уже от продавца. Правило «остаток не
    # уходит в минус» осталось прежним, но соблюдается честно — отказом с
    # объяснением, а не подменой заказа. Клиент сам не даст набрать больше
    # остатка, так что сюда попадает только устаревшая корзина.
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": vpid, "qty": 99, "flavor": "Вишня"}]})
    d = r.get_json() or {}
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(vpid)}
    c3("больше остатка → честный отказ, а не подмена заказа",
       r.status_code == 409 and d.get("error") == "sold_out")
    c3("сказано, сколько осталось", (d.get("short") or [{}])[0].get("left") == 3)
    c3("Вишня не тронута и в минус не ушла", stocks.get("Вишня") == 3)

    # несуществующий вкус — не оформляем (корзина считается пустой)
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": vpid, "qty": 1, "flavor": "Нетакого"}]})
    c3("несуществующий вкус → 400 empty", r.status_code == 400)

    return c.fails + c2.fails + c3.fails


CAP_CLIENT = 6167


def run_монеты_ограничены_долей_заказа():
    """Монетами гасим не больше 25% суммы товаров (shopinfo.COIN_MAX_SHARE) —
    даже если монет на балансе с запасом и/или сумма ещё уменьшена промокодом.
    Числа круглые нарочно: 100 Br × 25% = 25 Br = 2500 монет — ошибку в доле
    видно сразу, не только в округлении."""
    c = Checker("Монеты: потолок 25% от заказа")
    as_user(CAP_CLIENT, "capbuyer")
    db.set_age_ok(CAP_CLIENT)
    db.add_delivery_method("coincapcity", "Самовывоз", False, "", "ул. Тест", 0, True)
    mid = db.get_delivery_methods("coincapcity")[-1]["id"]
    pid = db.add_product("coincapcity", "pods", "CapPod", 100, 5)

    # С запасом монет (в разы больше 25% заказа) — списывается ровно потолок.
    db.add_coins(CAP_CLIENT, 100000)                # 1000 Br монетами на заказ в 100 Br
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c("списано ровно 25% (2500 монет), а не всё, что есть", d.get("coins_used") == 2500)
    c("скидка 25 Br", abs(float(d.get("discount", 0)) - 25) < 0.01)
    c("итого 100 − 25 = 75", abs(float(d.get("total", 0)) - 75) < 0.01)
    c("остаток монет 97500", db.get_coins(CAP_CLIENT) == 97500)

    # Потолок считается от суммы ТОВАРОВ, а не от остатка после промокода —
    # иначе промокод рядом с монетами давал бы ещё и больше монет впридачу.
    db.add_promo("CAP40", "fixed", 40, once_per_user=False)
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "promo_code": "CAP40", "items": [{"id": pid, "qty": 1}]})
    d = r.get_json() or {}
    c("потолок не съезжает из-за промокода: те же 2500 монет",
      d.get("coins_used") == 2500)
    c("итого 100 − 40 (промо) − 25 (монеты) = 35", abs(float(d.get("total", 0)) - 35) < 0.01)

    return c.fails


def run_способ_оплаты_можно_выключить():
    """Владелец выключает наличные/карту — покупатель не может заказать этим способом."""
    c = Checker("Выключенный способ оплаты сервер не принимает")
    as_user(PAY_CLIENT, "paybuyer")
    db.set_age_ok(PAY_CLIENT)
    pid = db.add_product("paycity", "pods", "PayPod", 20, 5)
    db.add_delivery_method("paycity", "Самовывоз", False, "", "ул. Тест", 0, True)
    mid = db.get_delivery_methods("paycity")[-1]["id"]

    try:
        as_admin()
        d = client.post("/api/admin/settings/update",
                        json={"initData": "x", "pay_cash": False}).get_json()
        c("владелец выключил наличные", d.get("applied", {}).get("pay_cash") is False)
        настройки = client.post("/api/admin/settings", json={"initData": "x"}).get_json()["settings"]
        c("в настройках это видно", настройки["pay_cash"] is False and настройки["pay_card"] is True)
        # Способ выключен один раз — общий на весь магазин, кэш очищен полным
        # сбросом на settings/update: город тут ни при чём, но всё равно бьём
        # явно, чтобы тест не зависел от TTL кэша /api/delivery.
        cache.bust()
        доставка = client.get("/api/delivery?city=paycity").get_json()
        c("покупатель видит это в /api/delivery", доставка.get("pay_cash") is False
          and доставка.get("pay_card") is True)

        as_user(PAY_CLIENT, "paybuyer")
        r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                            "payment_method": "cash", "items": [{"id": pid, "qty": 1}]})
        d2 = r.get_json() or {}
        # Отказ, а не тихое переключение на карту: заказ наличными, присланный
        # в обход экрана (старая кнопка, чужой клиент), не должен уехать
        # продавцу, которому нечем его закрыть.
        c("заказ наличными отбит сервером", r.status_code == 400 and d2.get("error") == "payment_off")
        c("склад не тронут", db.get_product(pid)["stock"] == 5)

        r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                            "payment_method": "card", "items": [{"id": pid, "qty": 1}]})
        d3 = r.get_json() or {}
        c("картой заказ проходит как раньше", d3.get("ok") is True)

        as_admin()
        d4 = client.post("/api/admin/settings/update",
                         json={"initData": "x", "pay_card": False}).get_json()
        c("оба способа сразу выключить нельзя", d4.get("ok") is True
          and "pay" in (d4.get("failed") or {}))
        настройки2 = client.post("/api/admin/settings", json={"initData": "x"}).get_json()["settings"]
        c("карта осталась включённой", настройки2["pay_card"] is True)
        return c.fails
    finally:
        as_admin()
        client.post("/api/admin/settings/update",
                    json={"initData": "x", "pay_cash": True, "pay_card": True})
        cache.bust()


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_монеты_ограничены_долей_заказа()
                    + run_способ_оплаты_можно_выключить()) else 0)
