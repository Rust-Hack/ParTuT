"""Бесплатная доставка от суммы.

Порог считает СЕРВЕР. Клиент показывает то же самое, но верить ему нельзя:
иначе доставку можно обнулить подделанным запросом.

Второе важное: порог берётся от стоимости товаров ДО скидки монетами. Иначе
покупатель дотягивается до бесплатной доставки своими же монетами, а за дорогу
платит магазин.
"""
from _common import db, client, server, Checker, as_user, as_admin


BUYER = 9601


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM delivery_methods")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    db.set_setting("free_delivery_from", 0)
    server._cache_bust()


def run():
    c = Checker("Бесплатная доставка от суммы")
    _clean()

    db.add_delivery_method("Минск", "Курьер", True, "Адрес", "", 5.0, True, 0)
    method = db.get_delivery_methods("Минск")[0]
    pid = db.add_product("Минск", "pods", "ПорогПод", 20.0, 50)
    db.set_age_ok(BUYER)
    as_user(BUYER, "buyer")
    server._cache_bust()

    def заказать(qty, use_coins=False):
        return client.post("/api/order", json={
            "initData": "x", "city": "Минск", "delivery_method_id": method["id"],
            "delivery_address": "ул. Тестовая 1", "phone": "+375291112233", "payment_method": "cash",
            "use_coins": use_coins, "items": [{"id": pid, "qty": qty}]}).get_json()

    # --- Порога нет: доставка платная всегда ---
    d = заказать(1)
    c("без порога доставка платная", abs(d["total"] - 25.0) < 0.01)

    # --- Ставим порог 50 ---
    db.set_setting("free_delivery_from", 50)
    server._cache_bust()

    d = заказать(2)                       # 40 Br товаров — не дотянул
    c("ниже порога доставка платная", abs(d["total"] - 45.0) < 0.01)

    d = заказать(3)                       # 60 Br товаров — порог взят
    c("на пороге доставка бесплатна", abs(d["total"] - 60.0) < 0.01)

    # Ровно на границе тоже бесплатно.
    db.set_setting("free_delivery_from", 40)
    server._cache_bust()
    d = заказать(2)
    c("ровно на границе — бесплатно", abs(d["total"] - 40.0) < 0.01)

    # --- Монеты не должны «дотягивать» до порога ---
    db.set_setting("free_delivery_from", 50)
    server._cache_bust()
    db.add_coins(BUYER, 5000)             # 50 Br монетами
    d = заказать(2, use_coins=True)       # товаров на 40 — порог НЕ взят
    c("заказ со списанием прошёл", d.get("ok"))
    c("монеты не дотягивают до порога — доставка платная",
      abs(d["total"] - (40.0 - d["discount"] + 5.0)) < 0.01)

    # А вот честная сумма товаров порог берёт, даже если монеты потом её уменьшат.
    d = заказать(3, use_coins=True)       # товаров на 60
    c("товаров хватило — доставка бесплатна даже со списанием",
      abs(d["total"] - (60.0 - d["discount"])) < 0.01)

    # --- Клиенту верить нельзя ---
    # Порог берётся из настроек магазина, а не из присланного запроса.
    d = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": method["id"],
        "delivery_address": "ул. Тестовая 1", "phone": "+375291112233", "payment_method": "cash",
        "delivery_fee": 0, "free_delivery": True,
        "items": [{"id": pid, "qty": 1}]}).get_json()
    c("подделанный запрос доставку не обнуляет", abs(d["total"] - 25.0) < 0.01)

    # --- Порог виден витрине ---
    d = client.get("/api/delivery?city=Минск").get_json()
    c("порог отдаётся приложению", abs(d["free_from"] - 50.0) < 0.01)

    # --- Настройка правится админом ---
    as_admin()
    client.post("/api/admin/settings/update", json={"initData": "x", "free_delivery_from": "75,5"})
    c("порог сохранён (и запятая понята)", abs(float(db.get_setting("free_delivery_from")) - 75.5) < 0.01)
    client.post("/api/admin/settings/update", json={"initData": "x", "free_delivery_from": "0"})
    c("нулём порог выключается", float(db.get_setting("free_delivery_from")) == 0)

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)


def run_trust():
    """Счётчик выполненных заказов: доверие новичку, но без вранья."""
    c = Checker("Сколько заказов магазин выполнил")
    _clean()
    server._cache_bust()

    pid = db.add_product("Минск", "pods", "ДовериеПод", 10.0, 200)
    db.add_delivery_method("Минск", "Самовывоз", False, "", "", 0, True, 0)

    def выдать(n):
        for _ in range(n):
            oid = db.create_order(9800, "b", "Минск",
                                  [{"id": pid, "name": "ДовериеПод", "price": 10.0, "qty": 1}], 10.0, "")
            conn = db.connect(); cur = conn.cursor()
            cur.execute(db._q("UPDATE orders SET status='issued' WHERE id = %s"), (oid,))
            conn.commit(); conn.close()

    выдать(3)
    server._cache_bust()
    d = client.get("/api/delivery?city=Минск").get_json()
    c("при трёх заказах счётчик молчит", d["orders_done"] == 0)

    выдать(20)
    server._cache_bust()
    d = client.get("/api/delivery?city=Минск").get_json()
    c("при двадцати трёх — показывает", d["orders_done"] == 23)

    _clean()
    return c.fails
