"""Подстановка адреса и телефона из прошлых заказов.

Смысл в том, чтобы постоянный покупатель не набирал одно и то же при каждой
покупке. Главная тонкость — адрес помнится ОТДЕЛЬНО по способу получения:
у метро это станция, у курьера улица, и перепутать их нельзя.
"""
from _common import db, client, Checker, as_user


BUYER = 8801
OTHER = 8802


def _order(uid, method, address, phone=""):
    oid = db.create_order(uid, f"u{uid}", "Минск",
                          [{"product_id": 1, "name": "Под", "price": 10.0, "qty": 1}], 10.0, "")
    db.set_order_delivery(oid, method, address, 0, "cash")
    if phone:
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE orders SET phone = %s WHERE id = %s"), (phone, oid))
        conn.commit(); conn.close()
    return oid


def run():
    c = Checker("Подстановка адреса и телефона")

    conn = db.connect(); conn.cursor().execute("DELETE FROM orders"); conn.commit(); conn.close()

    p = db.delivery_prefill(BUYER)
    c("у новичка подставлять нечего", p == {"phone": "", "addresses": {}})

    _order(BUYER, "Доставка по метро", "Немига", "+375 29 111-22-33")
    p = db.delivery_prefill(BUYER)
    c("телефон запомнен", p["phone"] == "+375 29 111-22-33")
    c("адрес запомнен по способу", p["addresses"]["Доставка по метро"] == "Немига")

    # Разные способы — разные адреса, путать нельзя.
    _order(BUYER, "Доставка курьером", "ул. Сурганова 5, кв. 12")
    p = db.delivery_prefill(BUYER)
    c("у курьера свой адрес", p["addresses"]["Доставка курьером"] == "ул. Сурганова 5, кв. 12")
    c("у метро остался свой", p["addresses"]["Доставка по метро"] == "Немига")

    # Помним ПОСЛЕДНИЙ адрес каждого способа, а не первый.
    _order(BUYER, "Доставка по метро", "Каменная Горка")
    p = db.delivery_prefill(BUYER)
    c("подставляем последний адрес", p["addresses"]["Доставка по метро"] == "Каменная Горка")

    # Телефон переживает заказы без него: берём самый свежий непустой.
    _order(BUYER, "Самовывоз", "")
    p = db.delivery_prefill(BUYER)
    c("телефон не теряется", p["phone"] == "+375 29 111-22-33")

    _order(BUYER, "Доставка по метро", "Пушкинская", "+375 44 999-88-77")
    p = db.delivery_prefill(BUYER)
    c("новый телефон вытесняет старый", p["phone"] == "+375 44 999-88-77")

    # Чужие заказы подставлять недопустимо.
    _order(OTHER, "Доставка по метро", "Малиновка", "+375 25 000-00-00")
    p = db.delivery_prefill(BUYER)
    c("чужой адрес не подставляется", p["addresses"]["Доставка по метро"] == "Пушкинская")
    c("чужой телефон не подставляется", p["phone"] == "+375 44 999-88-77")

    # Самовывоз адреса не имеет — пустые строки в память не попадают.
    c("пустой адрес не запоминается", "Самовывоз" not in p["addresses"])

    # Приложение получает всё это при входе.
    as_user(BUYER)
    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c("подстановка приходит в /api/me", me["prefill"]["phone"] == "+375 44 999-88-77")
    c("и адреса тоже", me["prefill"]["addresses"]["Доставка по метро"] == "Пушкинская")

    conn = db.connect(); conn.cursor().execute("DELETE FROM orders"); conn.commit(); conn.close()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
