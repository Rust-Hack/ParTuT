"""Характеристики у каждой категории свои.

«Бренд и вкус» на всё подряд — это описание одноразки, натянутое на картридж.
У картриджа спрашивают сопротивление, объём и совместимость; у пода — мощность,
аккумулятор и тип затяжки. Набор полей ведёт владелец, как и сами категории.

Отдельно проверяем, что старые товары не пострадали: крепость и объём
по-прежнему лежат в своих колонках — по ним живут прежние карточки и бот.
"""
from _common import db, client, Checker, as_admin, deny_admin

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute(db._q("DELETE FROM category_specs WHERE category = %s"), ("coils",))
    conn.commit(); conn.close()
    db.seed_category_specs()
    cache.bust()


def _cat(code):
    return next(c for c in client.get("/api/categories").get_json() if c["code"] == code)


def _product(pid):
    cache.bust()
    return next(p for p in client.get("/api/products").get_json() if p["id"] == pid)


def run():
    c = Checker("Характеристики категорий")
    _clean()
    as_admin()

    # --- Набор полей у каждой категории свой ---
    coils = {s["key"]: s for s in _cat("coils")["specs"]}
    c("у расходников есть сопротивление", "resistance" in coils)
    c("сопротивление в омах", coils["resistance"]["unit"] == "Ом")
    c("есть совместимость", coils["fit"]["kind"] == "text")
    c("есть тип с вариантами", "Картридж" in coils["kind"]["options"])

    pods = {s["key"]: s for s in _cat("podsystem")["specs"]}
    c("у подсистем мощность и аккумулятор", {"power", "battery"} <= set(pods))
    c("тип затяжки — из списка", any("MTL" in o for o in pods["draw"]["options"]))
    c("у подсистем нет сопротивления — это не картридж", "resistance" not in pods)

    disp = {s["key"]: s for s in _cat("disposable")["specs"]}
    c("у одноразок затяжки и крепость", {"volume", "strength"} <= set(disp))
    c("затяжки подписаны по-человечески", disp["volume"]["label"] == "Затяжек")

    # --- Вкусы не у всех ---
    c("у жидкостей вкусы есть", _cat("liquid")["has_flavors"] is True)
    c("у расходников вкусов нет", _cat("coils")["has_flavors"] is False)

    # --- Товар с характеристиками ---
    r = client.post("/api/admin/product", json={
        "initData": "x", "city": "Минск", "category": "coils", "name": "Картридж XROS",
        "price": "12", "cost": "7", "stock": "10", "brand": "Vaporesso",
        "specs": {"resistance": "0.8", "volume": "2", "fit": "XROS 2, XROS 3", "kind": "Картридж",
                  "power": "40"}})       # power у расходников нет — не должен сохраниться
    c("товар создан", (r.get_json() or {}).get("ok"))
    pid = r.get_json()["id"]
    p = _product(pid)
    c("сопротивление сохранено", p["specs"]["resistance"] == "0.8")
    c("совместимость сохранена", p["specs"]["fit"] == "XROS 2, XROS 3")
    c("чужая характеристика отброшена", "power" not in p["specs"])
    c("объём лёг в свою колонку — старые карточки целы", db.get_product(pid)["volume"] == "2")
    c("и всё равно виден среди характеристик", p["specs"]["volume"] == "2")

    # --- Правка характеристик ---
    r = client.post("/api/admin/product/specs", json={"initData": "x", "id": pid,
                                                      "specs": {"resistance": "1.2", "pack": "5"}})
    c("характеристики правятся", (r.get_json() or {}).get("ok"))
    p = _product(pid)
    c("новое сопротивление", p["specs"]["resistance"] == "1.2")
    c("добавилось количество в упаковке", p["specs"]["pack"] == "5")
    c("несуществующий товар — 404",
      client.post("/api/admin/product/specs", json={"initData": "x", "id": 999999, "specs": {}}).status_code == 404)

    # --- Владелец меняет набор полей ---
    c2 = Checker("Набор полей ведёт владелец")
    r = client.post("/api/admin/category/spec", json={"initData": "x", "category": "coils",
                                                      "label": "Материал намотки", "kind": "select",
                                                      "options": "Kanthal, Mesh, Ni80"})
    c2("характеристика добавлена", (r.get_json() or {}).get("ok"))
    sid = r.get_json()["id"]
    coils = {s["key"]: s for s in _cat("coils")["specs"]}
    c2("ключ сделан латиницей", "material_namotki" in coils)
    c2("варианты разобраны из строки", coils["material_namotki"]["options"] == ["Kanthal", "Mesh", "Ni80"])
    c2("новая встала в конец", _cat("coils")["specs"][-1]["id"] == sid)
    c2("повтор отклонён",
      client.post("/api/admin/category/spec", json={"initData": "x", "category": "coils",
                                                    "label": "Материал намотки"}).status_code == 400)
    c2("категории не существует — 404",
      client.post("/api/admin/category/spec", json={"initData": "x", "category": "нетакой",
                                                    "label": "Что-то"}).status_code == 404)

    # Теперь поле работает у товара.
    client.post("/api/admin/product/specs", json={"initData": "x", "id": pid, "specs": {"material_namotki": "Mesh"}})
    c2("значение сохраняется", _product(pid)["specs"]["material_namotki"] == "Mesh")

    client.post("/api/admin/category/spec/update", json={"initData": "x", "id": sid, "label": "Намотка", "unit": ""})
    c2("подпись изменилась", {s["id"]: s for s in _cat("coils")["specs"]}[sid]["label"] == "Намотка")
    c2("значение у товара не потерялось", _product(pid)["specs"]["material_namotki"] == "Mesh")

    client.post("/api/admin/category/spec/delete", json={"initData": "x", "id": sid})
    c2("поле убрано из категории", all(s["id"] != sid for s in _cat("coils")["specs"]))
    c2("удаление несуществующего — 404",
      client.post("/api/admin/category/spec/delete", json={"initData": "x", "id": sid}).status_code == 404)

    # --- Вкусы можно включить любой категории ---
    client.post("/api/admin/category/update", json={"initData": "x", "code": "coils", "has_flavors": True})
    c2("вкусы включаются", _cat("coils")["has_flavors"] is True)
    client.post("/api/admin/category/update", json={"initData": "x", "code": "coils", "has_flavors": False})
    c2("и выключаются", _cat("coils")["has_flavors"] is False)

    # --- Права ---
    deny_admin()
    c2("посторонний поля не добавит",
      client.post("/api/admin/category/spec", json={"initData": "x", "category": "coils", "label": "Своё"}).status_code == 403)
    c2("посторонний характеристики товара не тронет",
      client.post("/api/admin/product/specs", json={"initData": "x", "id": pid, "specs": {}}).status_code == 403)
    c2("но покупателю характеристики видны", "specs" in client.get("/api/products").get_json()[0])
    as_admin()

    _clean()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
