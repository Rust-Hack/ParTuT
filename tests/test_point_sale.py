"""Продажа на точке — человек купил у прилавка, мимо приложения.

Решение владельца (2.10.2026): учитывать как продажу. Значит, выручка,
прибыль, сводка дня и выплаты продавцу её видят, а всё покупательское —
кэшбэк, розыгрыш, «давно не заказывали», счётчик выданных заказов,
очередь заказов продавца — нет: покупателя у неё нет.
"""
from _common import db, client, Checker, as_admin


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _продать(город, строки, ключ=None, оплата="cash"):
    тело = {"initData": "x", "city": город, "lines": строки, "payment": оплата}
    if ключ:
        тело["client_token"] = ключ
    return client.post("/api/admin/sale", json=тело)


def run():
    c = Checker("Продажа на точке: остаток, выручка, но не покупательское")
    _чисто(); as_admin()
    mid = db.add_model("liquid", "QA ЖИЖА 50 мг", "QA", "", {}, ["Мята", "Вишня"])
    жижа = db.create_point_product(mid, "Минск", 25.0, 9.0, variants=[{"flavor": "Мята", "stock": 3},
                                                                       {"flavor": "Вишня", "stock": 1}])
    зарядка = db.create_point_product(db.add_model("accessories", "QA Зарядка"), "Минск", 15.0, 5.0, stock=4)
    выдано_до = db.issued_orders_count()

    r = _продать("Минск", [{"id": жижа, "flavor": "Мята", "qty": 2, "price": 25},
                           {"id": зарядка, "qty": 1, "price": "13,50"}], ключ="sale-key-000001")
    d = r.get_json() or {}
    c(f"проведена: {r.status_code} {d}", r.status_code == 200 and d.get("ok") and d.get("total") == 63.5)
    c("остаток списан: Мята 3→1, зарядка 4→3",
      {v["flavor"]: v["stock"] for v in db.get_variants(жижа)}["Мята"] == 1 and db.get_product(зарядка)["stock"] == 3)
    c("общий остаток жижи пересчитан из вкусов", db.get_product(жижа)["stock"] == 2)
    з = db.get_order(d["id"])
    c("это заказ без покупателя, сразу «выдан», с пометкой «на точке»",
      int(з["user_id"]) == 0 and з["status"] == "issued" and з["source"] == "point" and з["payment_method"] == "cash")
    import json
    состав = json.loads(з["items"])
    c("в составе закупка на момент продажи и цена со скидкой",
      состав[0]["cost"] == 9.0 and состав[0]["name"] == "QA ЖИЖА 50 мг — Мята" and состав[1]["price"] == 13.5)

    # --- Повтор той же попытки — та же продажа ---
    r2 = _продать("Минск", [{"id": жижа, "flavor": "Мята", "qty": 2, "price": 25},
                            {"id": зарядка, "qty": 1, "price": "13,50"}], ключ="sale-key-000001")
    c("повтор с тем же ключом — та же продажа, не вторая",
      (r2.get_json() or {}).get("id") == d["id"] and r2.get_json().get("replay") is True
      and db.get_product(зарядка)["stock"] == 3)

    r3 = _продать("Минск", [{"id": зарядка, "qty": 2, "price": 15}], ключ="sale-key-000001")
    d3 = r3.get_json() or {}
    c(f"тот же ключ, другой чек — не «записано», а отказ с номером прежней: {d3.get('message')!r}",
      r3.status_code == 409 and d3.get("error") == "token_reused" and d3.get("id") == d["id"]
      and db.get_product(зарядка)["stock"] == 3)

    # --- Выручка — да, покупательское — нет ---
    сегодня = db.seller_today("Минск")
    c(f"сводка дня: выручка с продажей, «выдано заказов» без неё: {сегодня}",
      сегодня["revenue_today"] == 63.5 and сегодня["issued_today"] == 0
      and сегодня["point_today"] == 1 and сегодня["point_revenue_today"] == 63.5)
    стат = db.get_business_stats(1)
    c(f"статистика: выручка {стат['revenue']}, прибыль {стат['profit']}",
      стат["revenue"] == 63.5 and стат["profit"] == round(63.5 - 2 * 9.0 - 5.0, 2))
    c("счётчик «магазин выдал N заказов» для новых покупателей не вырос", db.issued_orders_count() == выдано_до)
    c("в очереди заказов продавца её нет", not any(int(o["id"]) == d["id"] for o in db.get_orders(city="Минск")))
    c("напоминать «давно не заказывали» некому", all(int(r["user_id"]) != 0 for r in db.customers_to_remind(0, 50)))
    список = client.post("/api/admin/sales", json={"initData": "x", "city": "Минск"}).get_json()
    c("в списке продаж за сегодня — есть", [x["id"] for x in список["sales"]] == [d["id"]])

    # --- Не хватило одной строки — не записано ничего ---
    r = _продать("Минск", [{"id": зарядка, "qty": 1, "price": 15}, {"id": жижа, "flavor": "Вишня", "qty": 2, "price": 25}])
    d2 = r.get_json() or {}
    c(f"вишни 1, продано 2 — отказ с остатком: {d2.get('message')!r}",
      r.status_code == 409 and d2["error"] == "short" and d2["left"] == 1 and "на полке 1 шт" in d2["message"])
    c("и первая строка не списалась — всё или ничего", db.get_product(зарядка)["stock"] == 3)

    # --- Прочие отказы ---
    отказ = lambda строки, город="Минск": (_продать(город, строки).get_json() or {}).get("error")   # noqa: E731
    c("у товара со вкусами — без вкуса нельзя", отказ([{"id": жижа, "qty": 1, "price": 25}]) == "need_variant")
    c("несуществующий вкус", отказ([{"id": жижа, "flavor": "Дыня", "qty": 1, "price": 25}]) == "variant_missing")
    туров = db.create_point_product(db.add_model("accessories", "QA Туров"), "Туров", 10.0, 5.0, stock=2)
    c("товар другой точки", отказ([{"id": туров, "qty": 1, "price": 10}]) == "not_here")
    c("ноль штук", отказ([{"id": зарядка, "qty": 0, "price": 15}]) == "bad_lines")
    c("пустая продажа", отказ([]) == "bad_lines")
    c("непонятная точка", отказ([{"id": зарядка, "qty": 1, "price": 15}], "Нигдеград") == "bad_city")

    # --- Права: продавец — своя точка ---
    as_admin(uid=9200, username="продавец", role="staff", city="Минск")
    c("продавец продаёт на своей точке", _продать("Минск", [{"id": зарядка, "qty": 1, "price": 15}]).status_code == 200)
    c("на чужой — нет", _продать("Туров", [{"id": туров, "qty": 1, "price": 10}]).status_code == 403)
    c("и чужой список не видит — только свой",
      all(x["city"] == "Минск" for x in client.post("/api/admin/sales", json={"initData": "x", "city": "Туров"}).get_json()["sales"]))
    as_admin()

    # --- Отмена: штуки вернулись, из выручки ушла ---
    r = client.post("/api/admin/sale/cancel", json={"initData": "x", "id": d["id"]})
    c("отменена", r.status_code == 200 and db.get_order(d["id"])["status"] == "canceled")
    c("штуки вернулись: Мята 1→3, зарядка +1",
      {v["flavor"]: v["stock"] for v in db.get_variants(жижа)}["Мята"] == 3 and db.get_product(зарядка)["stock"] == 3)
    c("из выручки ушла", db.get_business_stats(1)["revenue"] == 15.0)
    c("второй раз не отменить", (client.post("/api/admin/sale/cancel", json={"initData": "x", "id": d["id"]}).get_json() or {}).get("error") == "already")
    обычный = db.create_order(9301, "b", "Минск", [{"id": зарядка, "name": "x", "price": 1, "qty": 1}], 1.0, "")
    c("обычный заказ так не отменить", (client.post("/api/admin/sale/cancel", json={"initData": "x", "id": обычный}).get_json() or {}).get("error") == "not_found")

    # Товар успели убрать в архив — отменять продажу нельзя: штуки ушли бы на невидимую полку.
    п = _продать("Туров", [{"id": туров, "qty": 2, "price": 10}]).get_json()
    db.archive_product(туров, True)
    r = client.post("/api/admin/sale/cancel", json={"initData": "x", "id": п["id"]})
    c("товар в архиве — отмена отказана", (r.get_json() or {}).get("error") == "archived" and db.get_product(туров)["stock"] == 0)
    c("в архивный товар не продать", отказ([{"id": туров, "qty": 1, "price": 10}], "Туров") == "archived")

    _чисто()
    return c.fails
