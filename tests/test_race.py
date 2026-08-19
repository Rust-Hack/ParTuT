"""Последняя штука на полке и двое покупателей одновременно.

Самый дорогой сорт ошибки для магазина: оба заказа проходят, остаток
показывает ноль, а на полке одна штука. Кто-то приедет за товаром, которого
нет, — и это будет уже не техническая мелочь, а испорченный клиент.

Раньше склад списывался «не ниже нуля», то есть молча прощал перепродажу.
Теперь списание условное: не хватило — весь заказ откатывается.
"""
import json
import threading

from _common import db, client, Checker, as_admin

import auth

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "product_variants"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def _method():
    if not db.get_delivery_methods("Минск"):
        db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True)
    return db.get_delivery_methods("Минск")[0]["id"]


def _sold_units():
    return sum(int(i["qty"]) for o in db.get_orders(50) if o["status"] != "canceled"
               for i in json.loads(o["items"]))


def _buy_parallel(pid, mid, buyers, flavor=None):
    """Все жмут «Оформить» одновременно."""
    out = []
    def buy(uid):
        auth.get_user = lambda init, u=uid: {"id": u, "username": f"u{u}"}
        item = {"id": pid, "qty": 1}
        if flavor:
            item["flavor"] = flavor
        r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                            "payment_method": "cash", "items": [item]})
        out.append((r.status_code, r.get_json()))
    threads = [threading.Thread(target=buy, args=(u,)) for u in buyers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def run():
    c = Checker("Последняя штука")
    _clean()
    mid = _method()
    for uid in (8801, 8802, 8803):
        db.set_age_ok(uid)

    pid = db.add_product("Минск", "disposable", "Последняя", 10.0, 1)
    cache.bust()
    res = _buy_parallel(pid, mid, (8801, 8802))
    ok = [r for r in res if r[1].get("ok")]
    no = [r for r in res if not r[1].get("ok")]
    c("ровно один заказ прошёл", len(ok) == 1)
    c("второму честно отказано", len(no) == 1 and no[0][1]["error"] == "sold_out")
    c("и сказано, что именно кончилось", no[0][1].get("name") == "Последняя")
    c("отказ приходит с кодом 409 — это не поломка", no[0][0] == 409)
    c("продана одна штука, а не две", _sold_units() == 1)
    c("остаток ноль", db.get_product(pid)["stock"] == 0)
    c("лишний заказ не создан", len(db.get_orders(50)) == 1)

    # --- Товар со вкусами: остаток ведётся по каждому вкусу ---
    c2 = Checker("Последний вкус")
    _clean()
    fp = db.add_product("Минск", "liquid", "Husky", 20.0, 0)
    db.add_variant(fp, "Мята", 1)
    db.add_variant(fp, "Вишня", 5)
    db.recalc_product_stock(fp)
    cache.bust()
    res = _buy_parallel(fp, mid, (8801, 8802), flavor="Мята")
    c2("по вкусу тоже продана одна", sum(1 for r in res if r[1].get("ok")) == 1)
    stocks = {v["flavor"]: v["stock"] for v in db.get_variants(fp)}
    c2("вкус ушёл в ноль", stocks["Мята"] == 0)
    c2("соседний вкус не тронут", stocks["Вишня"] == 5)
    c2("общий остаток пересобран", db.get_product(fp)["stock"] == 5)

    # --- Отказ ничего за собой не тянет ---
    c3 = Checker("Откат заказа")
    _clean()
    pid = db.add_product("Минск", "disposable", "Одна", 10.0, 1)
    db.add_coins(8803, 500)
    cache.bust()
    coins_before = db.get_coins(8803)
    auth.get_user = lambda init: {"id": 8803, "username": "u8803"}
    # Забираем последнюю штуку «из-под носа» и пробуем оформить на неё заказ.
    db.change_stock(pid, -1)
    cache.bust()
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "use_coins": True,
                                        "items": [{"id": pid, "qty": 1}]})
    c3("заказ не создан", not r.get_json().get("ok"))
    c3("монеты не списаны", db.get_coins(8803) == coins_before)
    c3("заказов в базе нет", len(db.get_orders(50)) == 0)
    c3("остаток не ушёл в минус", db.get_product(pid)["stock"] == 0)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
