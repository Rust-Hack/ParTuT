"""Переименование локации: вместо удалить (заблокировано при товарах) и
завести заново вручную со всеми товарами, продавцами и способами доставки.

city хранится строкой в нескольких таблицах (не внешним ключом), поэтому
переименование обязано перекатить их ВСЕ — иначе останется точка-призрак со
старым именем, по которому фильтры и продавец точки перестанут находить
свои же данные.
"""
from _common import db, client, Checker, as_admin


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "delivery_methods", "pickup_points", "staff"):
        cur.execute(f"DELETE FROM {t} WHERE city LIKE 'Ренейм%'")
    cur.execute("DELETE FROM locations WHERE name LIKE 'Ренейм%'")
    conn.commit(); conn.close()


def run():
    c = Checker("Переименование локации")
    _clean()
    as_admin()

    lid = db.add_location("Ренейм-Старое")
    pid = db.add_product("Ренейм-Старое", "pods", "Под", 20.0, 5)
    oid = db.create_order(9001, "u9001", "Ренейм-Старое",
                          [{"product_id": pid, "name": "Под", "price": 20.0, "qty": 1}], 20.0, "")
    db.add_staff(9002, city="Ренейм-Старое")
    db.add_pickup_point("Ренейм-Старое", "ул. Тестовая 1")
    db.add_delivery_method("Ренейм-Старое", "Самовывоз", 0, "", "", 0, 0)
    dm_id = db.get_delivery_methods("Ренейм-Старое")[0]["id"]

    r = client.post("/api/admin/location/rename", json={"initData": "x", "id": lid, "name": "Ренейм-Новое"})
    c("переименовано", (r.get_json() or {}).get("ok") is True)
    c("название в справочнике новое", db.get_location(lid)["name"] == "Ренейм-Новое")
    c("товар переехал", db.get_product(pid)["city"] == "Ренейм-Новое")
    c("заказ переехал (иначе продавец точки его не найдёт)",
      db.get_order(oid)["city"] == "Ренейм-Новое")
    c("продавец точки переехал", db.get_location(lid) and
      any(s["city"] == "Ренейм-Новое" for s in db.list_staff() if int(s["user_id"]) == 9002))
    c("точка самовывоза переехала", any(p["city"] == "Ренейм-Новое" for p in db.all_pickup_points()))
    c("способ доставки переехал", db.get_delivery_method(dm_id)["city"] == "Ренейм-Новое")
    c("под старым именем больше ничего нет",
      db.count_products_in_location("Ренейм-Старое") == 0)

    # Пустое имя — отказ, а не «оставить как было» молча.
    r = client.post("/api/admin/location/rename", json={"initData": "x", "id": lid, "name": "  "})
    c("пустое имя отклонено", r.get_json().get("ok") is False)

    # Совпадение с другой точкой — отказ, а не два города с одним именем.
    lid2 = db.add_location("Ренейм-Другая")
    r = client.post("/api/admin/location/rename", json={"initData": "x", "id": lid2, "name": "Ренейм-Новое"})
    c("совпадение с другим названием отклонено", r.get_json().get("ok") is False
      and r.get_json().get("error") == "exists")

    # Переименование в то же самое имя — не ошибка, а no-op.
    r = client.post("/api/admin/location/rename", json={"initData": "x", "id": lid, "name": "Ренейм-Новое"})
    c("переименование в то же имя не считается ошибкой", r.get_json().get("ok") is True)

    r = client.post("/api/admin/location/rename", json={"initData": "x", "id": 999999, "name": "Кто-то"})
    c("несуществующая точка — not_found", r.get_json().get("error") == "not_found")

    # add_location раньше сравнивал имя регистрозависимо — «Ренейм-Новое» и
    # «ренейм-новое» завелись бы как два разных города, не пересекаясь ни в
    # фильтрах, ни в остатках.
    same_case_id = db.add_location("ренейм-новое")
    c("регистр не создаёт вторую точку", same_case_id == lid)

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
