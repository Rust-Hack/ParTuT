"""Допродажа по реальным заказам: «с этим часто берут».

Раньше в корзине показывались просто хиты — всем одно и то же, к содержимому
корзины отношения не имеющее. Теперь подсказка считается по выданным заказам.

Ключевое правило здесь — про шум: единственная совместная покупка это
совпадение, а не закономерность. Если советовать по ней, магазин будет
уверенно рекомендовать случайность.
"""
from _common import db, client, Checker

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def _order(items, status="issued"):
    oid = db.create_order(7001, "buyer", "Минск",
                          [{"id": i, "name": f"Товар{i}", "price": 10.0, "qty": 1} for i in items], 10.0, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, oid))
    conn.commit(); conn.close()


def run():
    c = Checker("Что берут вместе")
    _clean()

    pod = db.add_product("Минск", "podsystem", "Под", 30.0, 5)
    liq = db.add_product("Минск", "liquid", "Жижа", 15.0, 5)
    coil = db.add_product("Минск", "coils", "Испаритель", 5.0, 5)
    rand = db.add_product("Минск", "disposable", "Случайная", 20.0, 5)

    # Пару «под + жижа» покупали дважды, «под + случайная» — один раз.
    _order([pod, liq])
    _order([pod, liq])
    _order([pod, rand])
    _order([liq, coil], status="new")      # незавершённый заказ — не покупка

    data = client.get("/api/also-bought").get_json()
    c("пара посчитана", liq in data.get(str(pod), []))
    c("и в обратную сторону", pod in data.get(str(liq), []))
    c("случайная пара отброшена — она встречалась раз", rand not in data.get(str(pod), []))
    c("незавершённый заказ не учтён", coil not in data.get(str(liq), []))

    # Вторая покупка делает пару закономерностью, но «под» с жижей берут чаще.
    _order([liq, coil])
    _order([liq, coil])
    _order([pod, liq])
    cache.bust()
    data = client.get("/api/also-bought").get_json()
    c("новая пара появилась", coil in data.get(str(liq), []))
    c("частая пара идёт первой", data[str(liq)][0] == pod)

    # --- Кэш и устойчивость ---
    c2 = Checker("Подсказки не мешают магазину")
    c2("ответ — словарь, даже когда заказов нет", isinstance(data, dict))
    _clean()
    cache.bust()
    c2("без заказов подсказок нет, но ошибки тоже", client.get("/api/also-bought").get_json() == {})

    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
