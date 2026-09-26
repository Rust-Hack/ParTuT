"""Второе измерение варианта (например у снюса — крепость и вкус).

Один товар, одна карточка продажи — а выбираются ДВЕ вещи: сначала крепость,
потом вкус. Решили не заводить вторую колонку в product_variants и не трогать
корзину/заказ/склад/выгрузку — они и так работают со «вкусом» как с текстом.
Вместо этого крепость и вкус склеиваются в ОДНУ строку через AXIS_SEP (" · ")
уже на фронтенде, и дальше по всему приложению это обычный вариант, как у
одноразок. Здесь проверяем ровно то, что доказывает: это безопасно — весь путь
заказа не видит разницы между «Мята» и «20mg · Мята».
"""
from _common import db, client, Checker, as_user

from partut import cache

CLIENT = 9301


def run():
    c = Checker("Категория с двумя измерениями")

    code = db.add_category("Снюс", "🧊")
    c("категория создана", bool(code))
    db.update_category(code, has_flavors=True, variant_label="Вкус", variant_label2="Крепость")

    cats = {row["code"]: row for row in db.list_categories()}
    c("labels сохранены в базе", cats[code]["variant_label"] == "Вкус"
      and cats[code]["variant_label2"] == "Крепость")

    cache.bust()
    resp = client.get("/api/categories").get_json()
    found = next((x for x in resp if x["code"] == code), None)
    c("категория отдаётся клиенту", found is not None)
    c("variant_label2 приходит в /api/categories", found and found["variant_label2"] == "Крепость")

    # Пустая строка — осознанный возврат к одному измерению, а не мусор.
    db.update_category(code, variant_label2="")
    cats2 = {row["code"]: row for row in db.list_categories()}
    c("пустая строка возвращает к одному измерению", not cats2[code]["variant_label2"])
    db.update_category(code, variant_label2="Крепость")   # вернуть для следующей части

    # --- Заказ товара с составным вариантом "крепость · вкус" ---
    c2 = Checker("Заказ снюса: остаток списывается по составному варианту")
    as_user(CLIENT, "snusbuyer")
    db.set_age_ok(CLIENT)

    db.add_delivery_method("snuscity", "Самовывоз", False, "", "ул. Тест", 0, True)
    mid = db.get_delivery_methods("snuscity")[-1]["id"]

    pid = db.add_product("snuscity", code, "Chapman", 12, 0)
    db.add_variant(pid, "20mg · Мята", 5)
    db.add_variant(pid, "20mg · Черника", 4)
    db.add_variant(pid, "10mg · Мята", 3)
    db.recalc_product_stock(pid)
    c2("общий остаток = 12", db.get_product(pid)["stock"] == 12)

    cache.bust()
    prod = next(p for p in client.get("/api/products").get_json() if p["id"] == pid)
    имена = {v["flavor"] for v in prod["variants"]}
    c2("витрина видит все три составных варианта",
       имена == {"20mg · Мята", "20mg · Черника", "10mg · Мята"})

    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash",
                                        "items": [{"id": pid, "qty": 2, "flavor": "20mg · Мята"}]})
    d = r.get_json() or {}
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c2("заказ прошёл", d.get("ok") is True)
    c2("списан именно этот вариант: 5 → 3", stocks.get("20mg · Мята") == 3)
    c2("соседние варианты не тронуты", stocks.get("20mg · Черника") == 4
       and stocks.get("10mg · Мята") == 3)
    c2("общий остаток пересчитан 12 → 10", db.get_product(pid)["stock"] == 10)
    c2("вся строка варианта попала в название позиции",
       "20mg · Мята" in (db.get_order(d["order_id"])["items"] or ""))

    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
