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


def run_отмена_удалённого_вкуса():
    """PS-02: вкус из чека удалили — отмена не «возвращает» штуки в пустоту.
    Многострочный чек: одна плохая строка — не меняется ничего."""
    c = Checker("Отмена продажи: вкуса больше нет")
    _чисто(); as_admin()
    mid = db.add_model("accessories", "QA Кабель", "QA", "", {}, ["Black", "White"])
    кабель = db.create_point_product(mid, "Минск", 10.0, 5.0, variants=[{"flavor": "Black", "stock": 2},
                                                                        {"flavor": "White", "stock": 1}])
    зарядка = db.create_point_product(db.add_model("accessories", "QA Зарядка 2"), "Минск", 15.0, 5.0, stock=3)
    d = _продать("Минск", [{"id": кабель, "flavor": "Black", "qty": 2, "price": 10},
                           {"id": зарядка, "qty": 1, "price": 15}]).get_json()
    r = client.post("/api/admin/product/variants/change", json={"initData": "x", "id": кабель, "add": [], "remove": ["Black"]})
    c(f"Black (0 шт) удалён из товара: {r.status_code}", r.status_code == 200
      and [v["flavor"] for v in db.get_variants(кабель)] == ["White"])
    r = client.post("/api/admin/sale/cancel", json={"initData": "x", "id": d["id"]})
    о = r.get_json() or {}
    c(f"отмена — отказ «варианта больше нет», с подсказкой: {о.get('message')!r}",
      r.status_code == 409 and о.get("error") == "variant_missing" and "Black" in о.get("message", "") and "с 0 шт" in о.get("message", ""))
    c("ничего не изменилось: продажа выдана, зарядка 2, White 1",
      db.get_order(d["id"])["status"] == "issued" and db.get_product(зарядка)["stock"] == 2
      and {v["flavor"]: v["stock"] for v in db.get_variants(кабель)} == {"White": 1})
    # Совет выполнен — вариант вернули — отмена проходит, штуки на месте.
    client.post("/api/admin/product/variants/change", json={"initData": "x", "id": кабель,
                                                           "add": [{"flavor": "Black", "qty": 0}], "remove": []})
    r = client.post("/api/admin/sale/cancel", json={"initData": "x", "id": d["id"]})
    c("вариант вернули — отмена прошла, Black 2, зарядка 3",
      r.status_code == 200 and {v["flavor"]: v["stock"] for v in db.get_variants(кабель)}.get("Black") == 2
      and db.get_product(зарядка)["stock"] == 3)
    _чисто()
    return c.fails


def run_отмена_и_архив_в_одну_секунду():
    """PS-01: отмена продажи и «в архив» в одну секунду. Раньше архив
    успевал между проверкой и возвратом — и 7 шт уходили на скрытую полку."""
    import threading
    from partut.db import orders as ordmod
    c = Checker("Отмена продажи и архив в одну секунду")
    _чисто(); as_admin()

    def наперегонки(первый, второй, модуль, имя):
        взял, отпустить, итоги, поток = threading.Event(), threading.Event(), {}, []
        настоящее = getattr(модуль, имя)

        def ждёт(*a, **kw):
            р = настоящее(*a, **kw)
            if threading.current_thread() is поток[0]:
                взял.set(); отпустить.wait(5)
            return р

        def зап(к, f):
            try:
                итоги[к] = f()
            except Exception as e:      # noqa: BLE001
                итоги[к] = e
        setattr(модуль, имя, ждёт)
        try:
            т1 = threading.Thread(target=зап, args=("1", первый)); поток.append(т1); т1.start()
            assert взял.wait(5)
            т2 = threading.Thread(target=зап, args=("2", второй)); т2.start()
            т2.join(0.5); ждал = т2.is_alive()
            отпустить.set(); т1.join(10); т2.join(10)
        finally:
            setattr(модуль, имя, настоящее); отпустить.set()
        return итоги.get("1"), итоги.get("2"), ждал

    # Отмена первой: архив ждёт и видит вернувшиеся 7 шт — отказ.
    p1 = db.create_point_product(db.add_model("accessories", "QA Гонка А"), "Минск", 10.0, 5.0, stock=7)
    s1 = db.record_point_sale("Минск", [{"id": p1, "qty": 7, "price": 10}], 1, "qa")[0]
    отмена, архив, ждал = наперегонки(lambda: db.cancel_point_sale(s1), lambda: db.archive_product(p1, True),
                                      ordmod, "_запереть_чек")
    c(f"архив ждал отмену: {ждал}", ждал)
    c(f"отмена прошла, архив отказал «на полке 7»: {архив!r}",
      not isinstance(отмена, Exception) and isinstance(архив, db.ArchiveRefused) and архив.code == "on_stock")
    c("товар не в архиве, 7 шт на полке", not db.get_product(p1)["archived"] and db.get_product(p1)["stock"] == 7)

    # Архив первым: отмена ждёт и видит архив — отказ, штуки не уходят на скрытую полку.
    p2 = db.create_point_product(db.add_model("accessories", "QA Гонка Б"), "Минск", 10.0, 5.0, stock=7)
    s2 = db.record_point_sale("Минск", [{"id": p2, "qty": 7, "price": 10}], 1, "qa")[0]
    архив, отмена, ждал = наперегонки(lambda: db.archive_product(p2, True), lambda: db.cancel_point_sale(s2),
                                      db, "open_orders_with_product")
    c(f"отмена ждала архив: {ждал}", ждал)
    c(f"архив прошёл, отмена отказала «в архиве»: {отмена!r}",
      isinstance(архив, dict) and архив["changed"] and isinstance(отмена, db.PointSaleRefused) and отмена.code == "archived")
    c("в архиве с остатком 0, продажа не отменена",
      db.get_product(p2)["archived"] == 1 and db.get_product(p2)["stock"] == 0 and db.get_order(s2)["status"] == "issued")
    _чисто()
    return c.fails
