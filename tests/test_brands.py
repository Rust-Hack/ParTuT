"""Бренды и вкусы — справочник, из которого выбирают в товарах.

Три поломки, которые здесь закрыты (все проверены на живой базе до правки):
  • переименование бренда не трогало товары — у них оставалось старое имя,
    и в фильтре каталога появлялся бренд, которого в справочнике уже нет;
  • одно и то же имя заводилось сколько угодно раз («Vaporesso» под подсистемы
    и «Vaporesso» под картриджи), и в фильтре это выглядело как два бренда;
  • бренд удалялся молча, даже если на нём висели товары.
"""
from _common import db, client, Checker, as_admin, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM brands")
    conn.commit(); conn.close()
    cache.bust()


def _brands(category=None):
    cache.bust()
    url = "/api/brands" + (f"?category={category}" if category else "")
    return client.get(url).get_json()


def run():
    c = Checker("Бренды")
    _clean()
    as_admin()

    r = client.post("/api/admin/brand", json={"initData": "x", "name": "Vaporesso", "category": "",
                                              "flavors": ["Мята", "мята ", "Вишня"]})
    c("бренд создан", (r.get_json() or {}).get("ok"))
    bid = r.get_json()["id"]
    b = _brands()[0]
    c("повтор вкуса без учёта регистра отброшен", b["flavors"] == ["Мята", "Вишня"])
    c("бренд без категории — общий", b["category"] == "")

    # --- Общий бренд виден в любой категории ---
    c("общий бренд предлагается расходникам", any(x["id"] == bid for x in _brands("coils")))
    c("и подсистемам тоже", any(x["id"] == bid for x in _brands("podsystem")))
    client.post("/api/admin/brand", json={"initData": "x", "name": "ТолькоЖижа", "category": "liquid", "flavors": []})
    c("бренд категории виден своей категории", any(x["name"] == "ТолькоЖижа" for x in _brands("liquid")))
    c("и не виден чужой", all(x["name"] != "ТолькоЖижа" for x in _brands("coils")))

    # --- Дубли ---
    dup = client.post("/api/admin/brand", json={"initData": "x", "name": "vaporesso", "category": "coils", "flavors": []})
    c("дубль имени отклонён", dup.status_code == 400 and dup.get_json()["error"] == "exists")
    c("и сказано, какой бренд мешает", dup.get_json()["name"] == "Vaporesso")

    # То же самое кириллицей: SQL LOWER() у SQLite не трогает кириллицу —
    # сравнение обязано идти на стороне Python, иначе «Хаски»/«хаски» заведутся
    # как два разных бренда.
    client.post("/api/admin/brand", json={"initData": "x", "name": "Хаски", "category": "", "flavors": []})
    dup_ru = client.post("/api/admin/brand", json={"initData": "x", "name": "хаски", "category": "coils", "flavors": []})
    c("дубль кириллицей тоже отклонён", dup_ru.status_code == 400 and dup_ru.get_json()["error"] == "exists")

    # --- Переименование тянет за собой товары ---
    c2 = Checker("Переименование и удаление")
    pid = db.add_product("Минск", "coils", "Картридж XROS", 12.0, 3, brand="Vaporesso")
    r = client.post("/api/admin/brand", json={"initData": "x", "id": bid, "name": "Vaporesso Tech",
                                              "category": "", "flavors": ["Мята"]})
    c2("переименование прошло", (r.get_json() or {}).get("ok"))
    c2("товары перенесены", r.get_json()["moved"] == 1)
    c2("у товара новое имя бренда", db.get_product(pid)["brand"] == "Vaporesso Tech")
    c2("призрака в фильтре не осталось", db.count_products_of_brand("Vaporesso") == 0)

    # --- Удаление с товарами ---
    r = client.post("/api/admin/brand/delete", json={"initData": "x", "id": bid})
    c2("удаление бренда с товарами остановлено", r.status_code == 400 and r.get_json()["error"] == "has_products")
    c2("и сказано, сколько товаров", r.get_json()["count"] == 1)
    c2("бренд на месте", any(x["id"] == bid for x in _brands()))
    r = client.post("/api/admin/brand/delete", json={"initData": "x", "id": bid, "force": True})
    c2("с подтверждением удаляется", (r.get_json() or {}).get("ok"))
    c2("товар при этом цел", db.get_product(pid) is not None)
    c2("удаление несуществующего — 404",
      client.post("/api/admin/brand/delete", json={"initData": "x", "id": bid}).status_code == 404)

    # --- Справочник вкусов ---
    c3 = Checker("Справочник вкусов")
    db.add_brand("Husky", "liquid", ["Смородина"])
    vid = db.add_product("Минск", "liquid", "Husky Соль", 16.0, 0, brand="Husky")
    db.add_variant(vid, "Дыня", 3)
    db.add_product("Минск", "coils", "Картридж с ароматом", 5.0, 1, flavor="Табак")
    cache.bust()
    flavors = client.get("/api/flavors").get_json()
    c3("вкус из бренда попал в справочник", "Смородина" in flavors)
    c3("вкус из варианта тоже", "Дыня" in flavors)
    c3("и вкус обычного товара", "Табак" in flavors)
    c3("список отсортирован", flavors == sorted(flavors, key=lambda s: s.lower()))

    # --- Права ---
    deny_admin()
    c3("посторонний бренд не заведёт",
      client.post("/api/admin/brand", json={"initData": "x", "name": "Левый", "category": ""}).status_code == 403)
    c3("посторонний бренд не удалит",
      client.post("/api/admin/brand/delete", json={"initData": "x", "id": 1}).status_code == 403)
    c3("а справочники покупателю открыты",
      client.get("/api/brands").status_code == 200 and client.get("/api/flavors").status_code == 200)
    as_admin()

    # --- Слияние дублей от прежней схемы ---
    c4 = Checker("Слияние старых дублей")
    _clean()
    db.add_brand("Voopoo", "podsystem", ["Мята"])
    db.add_brand("Voopoo", "coils", ["Вишня"])
    db.add_brand("Одинокий", "liquid", ["Кола"])
    merged = db.merge_duplicate_brands()
    c4("дубль слит", merged == 1)
    rows = {b["name"]: b for b in _brands()}
    c4("остался один Voopoo", len(rows) == 2)
    c4("вкусы объединены", set(rows["Voopoo"]["flavors"]) == {"Мята", "Вишня"})
    c4("слитый бренд стал общим", rows["Voopoo"]["category"] == "")
    c4("одиночный бренд не тронут", rows["Одинокий"]["category"] == "liquid")

    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
