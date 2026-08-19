"""Продавец правит состав заказа и отклоняет с причиной.

У продавца было три кнопки: подтвердить, выдать, отклонить. Клиент говорит
«давайте одну вместо двух» — и единственным ходом было отклонить заказ целиком
и просить оформить заново. Заказ терялся на ровном месте.

Цена ошибки здесь высокая: правка двигает и склад, и деньги. Если они разъедутся,
магазин отдаст товар бесплатно или спишет то, чего не продавал.
"""
from _common import db, client, Checker, as_admin, deny_admin, SENT, reset_sent

from partut import cache


BUYER = 9601


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM product_variants")
    conn.commit(); conn.close()
    cache.bust()


def _order(items, total, status="paid", **kw):
    """Заказ как настоящий: товар в нём уже снят с полки.

    Фикстура обязана списывать остаток, иначе правка «вернула одну штуку»
    проверялась бы на складе, с которого ничего не брали."""
    oid = db.create_order(BUYER, "buyer", "Минск", items, total, "")
    for it in items:
        db.change_stock(it["id"], -int(it["qty"]))
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, oid))
    for k, v in kw.items():
        cur.execute(db._q(f"UPDATE orders SET {k} = %s WHERE id = %s"), (v, oid))
    conn.commit(); conn.close()
    return oid


def _order_flavor(pid, flavor, qty):
    """Заказ на товар со вкусами: списывается вариант, а не товар целиком."""
    items = [{"id": pid, "name": "Husky", "flavor": flavor, "price": 20.0, "qty": qty}]
    oid = db.create_order(BUYER, "buyer", "Минск", items, 20.0 * qty, "")
    db.change_variant_stock(pid, flavor, -qty)
    db.recalc_product_stock(pid)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'paid' WHERE id = %s"), (oid,))
    conn.commit(); conn.close()
    return oid


def _edit(oid, qty):
    return client.post("/api/admin/order/items", json={"initData": "x", "id": oid, "qty": qty})


def run():
    c = Checker("Правка состава заказа")
    _clean()
    as_admin()

    # На полке было 10, две штуки ушли в заказ → осталось 8.
    pid = db.add_product("Минск", "disposable", "Elf Bar", 10.0, 10)
    oid = _order([{"id": pid, "name": "Elf Bar", "price": 10.0, "qty": 2}], 20.0)

    # --- Убавить ---
    r = _edit(oid, {"0": 1})
    d = r.get_json()
    c("правка принята", d.get("ok"))
    c("сумма пересчитана", d["order"]["total"] == 10.0)
    c("в заказе осталась одна штука", d["order"]["items"][0]["qty"] == 1)
    c("лишняя штука вернулась на полку", db.get_product(pid)["stock"] == 9)
    c("покупателю сказали, что изменилось",
      any("изменил заказ" in (t or "") and "Elf Bar" in (t or "") for _, t, _ in SENT))

    # --- Прибавить ---
    r = _edit(oid, {"0": 3})
    c("добавить можно", r.get_json().get("ok"))
    c("две штуки сняты с полки", db.get_product(pid)["stock"] == 7)
    c("сумма выросла", r.get_json()["order"]["total"] == 30.0)

    # --- Больше, чем есть на полке ---
    r = _edit(oid, {"0": 99})
    d = r.get_json()
    c("больше остатка не даст", r.status_code == 400 and d["error"] == "no_stock")
    c("и скажет, сколько есть", d["have"] == 7 and d["name"] == "Elf Bar")
    c("остаток не тронут", db.get_product(pid)["stock"] == 7)
    c("заказ не тронут", db.get_order(oid)["total"] == 30.0)

    # --- Пустой заказ — это отмена, а не правка ---
    r = _edit(oid, {"0": 0})
    c("в ноль не обнулить", r.status_code == 400 and r.get_json()["error"] == "empty")
    c("заказ цел", db.get_order(oid)["total"] == 30.0)
    c("и остаток цел", db.get_product(pid)["stock"] == 7)

    # --- Мусор ---
    c("несуществующая позиция отклонена", _edit(oid, {"7": 1}).status_code == 400)
    c("пустая правка отклонена", _edit(oid, {"0": 3}).get_json()["error"] == "no_changes")
    c("несуществующий заказ — 404", _edit(999999, {"0": 1}).status_code == 404)
    deny_admin()
    c("посторонний состав не правит", _edit(oid, {"0": 1}).status_code == 403)
    as_admin()

    # --- Выданный заказ не правим ---
    c2 = Checker("Закрытый заказ")
    done = _order([{"id": pid, "name": "Elf Bar", "price": 10.0, "qty": 1}], 10.0, status="issued")
    r = _edit(done, {"0": 2})
    c2("выданный заказ не изменить", r.status_code == 409 and r.get_json()["error"] == "closed")

    # --- Заказ из нескольких позиций и со скидками ---
    c3 = Checker("Сумма со скидками")
    p1 = db.add_product("Минск", "disposable", "Elf Bar", 10.0, 10)
    p2 = db.add_product("Минск", "liquid", "Husky", 20.0, 5)
    multi = _order([{"id": p1, "name": "Elf Bar", "price": 10.0, "qty": 2},
                    {"id": p2, "name": "Husky", "price": 20.0, "qty": 1}], 42.0,
                   coins_used=500, delivery_fee=2.0, promo_discount=3.0)
    # товары 40, монеты -5 (500 монет), промокод -3, доставка +2
    r = _edit(multi, {"1": 0})
    d = r.get_json()
    c3("позиция убрана", len(d["order"]["items"]) == 1)
    c3("скидка монетами сохранена, промокод урезан по сумме товаров",
      d["order"]["total"] == round(max(0, 20 - 5 - 3) + 2, 2))
    c3("убранная позиция вернулась на полку", db.get_product(p2)["stock"] == 5)
    c3("оставшуюся не тронули", db.get_product(p1)["stock"] == 8)

    # --- Товар со вкусами: склад ведётся по каждому вкусу отдельно ---
    c5 = Checker("Правка вкуса")
    fp = db.add_product("Минск", "liquid", "Husky", 20.0, 0)
    db.add_variant(fp, "Мята", 6)
    db.add_variant(fp, "Вишня", 4)
    db.recalc_product_stock(fp)
    fo = _order_flavor(fp, "Мята", 2)
    r = _edit(fo, {"0": 1})
    c5("правка вкуса принята", r.get_json().get("ok"))
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(fp)}
    c5("вернулось именно в свой вкус", stocks["Мята"] == 5)
    c5("чужой вкус не тронут", stocks["Вишня"] == 4)
    c5("общий остаток пересобран", db.get_product(fp)["stock"] == 9)
    r = _edit(fo, {"0": 99})
    c5("больше, чем есть этого вкуса, не даст",
      r.status_code == 400 and r.get_json()["have"] == 5)

    # --- Отказ с причиной ---
    c4 = Checker("Отказ с причиной")
    reset_sent()
    rej = _order([{"id": pid, "name": "Elf Bar", "price": 10.0, "qty": 1}], 10.0)
    stock_before = db.get_product(pid)["stock"]
    r = client.post("/api/admin/order/status", json={"initData": "x", "id": rej, "action": "reject",
                                                     "reason": "out", "note": "привезём в четверг"})
    c4("заказ отклонён", r.get_json().get("ok"))
    text = next((t for _, t, _ in SENT if f"#{rej}" in (t or "")), "")
    c4("причина названа", "разобрали" in text)
    c4("приписка продавца дошла", "привезём в четверг" in text)
    c4("товар вернулся на склад", db.get_product(pid)["stock"] == stock_before + 1)

    reset_sent()
    rej2 = _order([{"id": pid, "name": "Elf Bar", "price": 10.0, "qty": 1}], 10.0)
    client.post("/api/admin/order/status", json={"initData": "x", "id": rej2, "action": "reject"})
    text = next((t for _, t, _ in SENT if f"#{rej2}" in (t or "")), "")
    c4("без причины сообщение всё равно понятное", "напишите нам" in text)

    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
