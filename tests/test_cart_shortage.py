"""Товар кончился, пока человек оформлял заказ.

Найдено прогоном набора на настоящем Postgres: там сдвинулся тайминг, и стало
видно то, что SQLite прощал. Ошибка не в базе, а в самом оформлении.

Заказ брал из корзины только то, чего хватает на складе, а остальное молча
выбрасывал:
  • разобрали всё — покупатель видел «Корзина пуста» при полной корзине;
  • разобрали часть — заказ уходил урезанным, и человек узнавал об этом уже
    от продавца, получив меньше, чем собирался купить;
  • просил 5, осталось 2 — оформлялись 2, и снова без единого слова.

Теперь сервер отказывает и говорит, что именно изменилось, а приложение по
этому ответу правит корзину — иначе «Оформить» упиралось бы в тот же отказ.
"""
from _common import db, client, Checker, as_user, as_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "product_variants"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    cache.bust()


def _method():
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    return db.get_delivery_methods("Минск")[0]["id"]


def run():
    c = Checker("Разобрали, пока оформлял")
    _clean()
    mid = _method()
    db.set_age_ok(9401)
    as_user(9401, "buyer")

    def order(items):
        return client.post("/api/order", json={"initData": "x", "city": "Минск",
                                               "delivery_method_id": mid,
                                               "payment_method": "cash", "items": items})

    # --- Единственный товар разобрали ---
    pid = db.add_product("Минск", "disposable", "Последняя", 10.0, 1)
    db.change_stock(pid, -1)                  # кто-то забрал последнюю
    cache.bust()
    r = order([{"id": pid, "qty": 1}])
    d = r.get_json()
    c("заказ не оформлен", not d.get("ok"))
    c("это НЕ «корзина пуста»", d.get("error") == "sold_out")
    c("код 409 — не поломка, а занятая полка", r.status_code == 409)
    c("названо, что именно разобрали", d.get("name") == "Последняя")
    c("сказано человеческими словами", "разобрали" in (d.get("message") or ""))
    c("приложению сказано, что убрать из корзины",
      [g["id"] for g in d.get("gone", [])] == [pid])

    # --- Просят больше, чем осталось ---
    c2 = Checker("Осталось меньше, чем просят")
    db.change_stock(pid, 2)                   # завезли две
    cache.bust()
    r = order([{"id": pid, "qty": 5}])
    d = r.get_json()
    c2("заказ не проходит втихую", not d.get("ok") and d.get("error") == "sold_out")
    c2("сказано, сколько осталось", (d.get("short") or [{}])[0].get("left") == 2)
    c2("и это видно в сообщении", "осталось 2" in (d.get("message") or ""))
    c2("склад не тронут", db.get_product(pid)["stock"] == 2)
    c2("заказ не создан", len(db.get_orders(50)) == 0)

    # Просят ровно столько, сколько есть — это нормальная покупка.
    r = order([{"id": pid, "qty": 2}])
    c2("сколько есть — столько и продаём", r.get_json().get("ok") is True)
    c2("склад ушёл в ноль", db.get_product(pid)["stock"] == 0)

    # --- Часть корзины разобрали ---
    c3 = Checker("Разобрали часть корзины")
    _clean()
    mid = _method()
    есть = db.add_product("Минск", "disposable", "Есть", 10.0, 5)
    нету = db.add_product("Минск", "disposable", "Нету", 10.0, 0)
    cache.bust()
    r = order([{"id": есть, "qty": 1}, {"id": нету, "qty": 1}])
    d = r.get_json()
    c3("заказ целиком отклонён, а не урезан", not d.get("ok") and d.get("error") == "sold_out")
    c3("заказа в базе нет", len(db.get_orders(50)) == 0)
    c3("остаток доступного не списан", db.get_product(есть)["stock"] == 5)
    c3("названо недостающее", d.get("name") == "Нету")

    # --- Пустая корзина остаётся пустой корзиной ---
    c4 = Checker("Пустая корзина")
    r = order([])
    c4("пусто — это «пусто», а не «разобрали»",
       r.get_json().get("error") == "empty" and r.status_code == 400)
    r = order([{"id": 999999, "qty": 1}])
    c4("несуществующий товар — тоже «пусто»", r.get_json().get("error") == "empty")

    # --- Вкусы считаются по своему остатку ---
    c5 = Checker("Вкус кончился")
    _clean()
    mid = _method()
    fp = db.add_product("Минск", "liquid", "Husky", 20.0, 0)
    db.add_variant(fp, "Мята", 0)
    db.add_variant(fp, "Вишня", 3)
    db.recalc_product_stock(fp)
    cache.bust()
    r = order([{"id": fp, "flavor": "Мята", "qty": 1}])
    d = r.get_json()
    c5("кончившийся вкус не продаём молча", d.get("error") == "sold_out")
    c5("и вкус назван", "Мята" in (d.get("name") or ""))
    r = order([{"id": fp, "flavor": "Вишня", "qty": 5}])
    d = r.get_json()
    c5("по вкусу тоже видно, сколько осталось",
       (d.get("short") or [{}])[0].get("left") == 3)
    r = order([{"id": fp, "flavor": "Вишня", "qty": 3}])
    c5("сколько есть — продаём", r.get_json().get("ok") is True)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
