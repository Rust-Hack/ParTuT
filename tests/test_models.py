"""Ассортимент: модель заводится один раз, товар — это её наличие на точке.

Раньше одна и та же подсистема на трёх точках описывалась трижды: три раза
вбить характеристики, три шанса вбить их по-разному. Теперь описание живёт
в модели, а у товара остаётся то, что у каждой точки своё: цена, закупка и
остаток.
"""
from _common import db, client, server, Checker, as_admin, deny_admin


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM models")
    conn.commit(); conn.close()
    server._cache_bust()


def _models():
    return client.post("/api/admin/models", json={"initData": "x"}).get_json()["models"]


def _product(pid):
    server._cache_bust()
    return next(p for p in client.get("/api/products").get_json() if p["id"] == pid)


def run():
    c = Checker("Ассортимент и наличие")
    _clean()
    as_admin()

    # --- Модель ---
    r = client.post("/api/admin/model", json={
        "initData": "x", "category": "coils", "name": "Картридж XROS", "brand": "Vaporesso",
        "description": "Оригинальный картридж",
        "specs": {"resistance": "0.8", "fit": "XROS 2, XROS 3", "power": "40"},
        "flavors": ["Мята", "мята "]})
    c("модель создана", (r.get_json() or {}).get("ok"))
    mid = r.get_json()["id"]
    m = _models()[0]
    c("характеристики сохранены", m["specs"]["resistance"] == "0.8")
    c("чужая характеристика отброшена", "power" not in m["specs"])
    c("повтор вкуса убран", m["flavors"] == ["Мята"])
    c("товаров пока нет", m["products"] == 0)
    c("без названия модель не создать",
      client.post("/api/admin/model", json={"initData": "x", "category": "coils", "name": " "}).status_code == 400)
    c("с выдуманной категорией тоже",
      client.post("/api/admin/model", json={"initData": "x", "category": "нет", "name": "X"}).status_code == 400)

    # --- Завоз на две точки ---
    r1 = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": mid,
                                                            "city": "Минск", "price": "12", "cost": "7", "stock": "10"})
    r2 = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": mid,
                                                            "city": "Туров", "price": "14", "stock": "3"})
    c("завоз на первую точку", (r1.get_json() or {}).get("ok"))
    c("завоз на вторую точку", (r2.get_json() or {}).get("ok"))
    p1, p2 = r1.get_json()["id"], r2.get_json()["id"]
    a, b = _product(p1), _product(p2)
    c("описание взято из модели", a["name"] == "Картридж XROS" and a["brand"] == "Vaporesso")
    c("характеристики тоже", a["specs"]["resistance"] == "0.8")
    c("цена у каждой точки своя", (a["price"], b["price"]) == (12.0, 14.0))
    c("остаток у каждой точки свой", (a["stock"], b["stock"]) == (10, 3))
    c("модель знает про свои товары", _models()[0]["products"] == 2)

    # --- Правка модели расходится по точкам ---
    c2 = Checker("Правка модели")
    r = client.post("/api/admin/model", json={"initData": "x", "id": mid, "category": "coils",
                                              "name": "Картридж XROS Pro", "brand": "Vaporesso Tech",
                                              "specs": {"resistance": "1.0", "fit": "XROS Pro"}})
    c2("правка прошла", (r.get_json() or {}).get("ok"))
    c2("сказано, скольких товаров коснулась", r.get_json()["updated"] == 2)
    a, b = _product(p1), _product(p2)
    c2("название обновилось на обеих точках", a["name"] == b["name"] == "Картридж XROS Pro")
    c2("бренд обновился", a["brand"] == "Vaporesso Tech")
    c2("характеристика обновилась", a["specs"]["resistance"] == "1.0")
    c2("убранная характеристика исчезла", "fit" in a["specs"] and a["specs"]["fit"] == "XROS Pro")
    c2("ЦЕНА не тронута — она у каждой точки своя", (a["price"], b["price"]) == (12.0, 14.0))
    c2("ОСТАТОК не тронут", (a["stock"], b["stock"]) == (10, 3))

    # --- Товар со вкусами ---
    c3 = Checker("Модель со вкусами")
    r = client.post("/api/admin/model", json={"initData": "x", "category": "liquid", "name": "Husky",
                                              "brand": "Husky", "specs": {"strength": "20", "volume": "30"},
                                              "flavors": ["Мята", "Вишня"]})
    lid = r.get_json()["id"]
    r = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid, "city": "Минск",
                                                           "price": "16", "cost": "9",
                                                           "variants": [{"flavor": "Мята", "stock": "4"},
                                                                        {"flavor": "Вишня", "stock": "2"}]})
    pid = r.get_json()["id"]
    p = _product(pid)
    c3("вкусы заведены с остатками", {v["flavor"]: v["stock"] for v in p["variants"]} == {"Мята": 4, "Вишня": 2})
    c3("общий остаток собран", p["stock"] == 6)
    c3("крепость и объём попали в товар", (p["strength"], p["volume"]) == ("20", "30"))

    # --- Удаление модели ---
    c4 = Checker("Удаление модели")
    r = client.post("/api/admin/model/delete", json={"initData": "x", "id": mid})
    c4("модель с товарами так не удалить", r.status_code == 400 and r.get_json()["error"] == "has_products")
    c4("и сказано, сколько точек затронуто", r.get_json()["count"] == 2)
    r = client.post("/api/admin/model/delete", json={"initData": "x", "id": mid, "force": True})
    c4("с подтверждением удаляется", (r.get_json() or {}).get("ok"))
    c4("товары на точках остались", db.get_product(p1) is not None and db.get_product(p2) is not None)
    c4("но с моделью больше не связаны", db.get_product(p1)["model_id"] is None)
    c4("удаление несуществующей — 404",
      client.post("/api/admin/model/delete", json={"initData": "x", "id": 999999}).status_code == 404)

    # --- Вкус убрали из модели, а на точке он остался ---
    c6 = Checker("Осиротевшие вкусы")
    r = client.post("/api/admin/model", json={"initData": "x", "id": lid, "category": "liquid",
                                              "name": "Husky", "brand": "Husky",
                                              "specs": {"strength": "20", "volume": "30"},
                                              "flavors": ["Мята"]})
    orphans = {o["flavor"]: o["stock"] for o in r.get_json()["orphans"]}
    c6("про оставшийся вкус сказано", orphans.get("Вишня") == 2)
    c6("остаток НЕ стёрт — это товар на полке",
      {v["flavor"]: v["stock"] for v in db.get_variants(pid)}.get("Вишня") == 2)
    c6("вкус из модели в сироты не попал", "Мята" not in orphans)

    # --- Чего админ может натворить и что мы теперь ловим ---
    c5 = Checker("Защиты при заведении")
    r = client.post("/api/admin/model", json={"initData": "x", "category": "liquid",
                                              "name": "husky", "brand": "Husky"})
    c5("дубль модели отклонён (регистр не спасает)",
      r.status_code == 400 and r.get_json()["error"] == "exists")
    r = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid,
                                                           "city": "Минск", "price": "20"})
    c5("повторный завоз на ту же точку отклонён",
      r.status_code == 400 and r.get_json()["error"] == "already_here")
    r = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid,
                                                           "city": "Туров", "price": "0"})
    c5("нулевая цена отклонена", r.status_code == 400 and r.get_json()["error"] == "bad_price")
    r = client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid,
                                                           "city": "Нетакого", "price": "10"})
    c5("выдуманная точка отклонена", r.status_code == 400)

    # Перенос товара на точку, где эта модель уже стоит
    client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid,
                                                       "city": "Туров", "price": "17"})
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                                                       "field": "city", "value": "Туров"})
    c5("перенос в точку с двойником отклонён",
      r.status_code == 400 and r.get_json()["error"] == "already_here")
    c5("а на свободную точку переносится",
      client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                                                     "field": "city", "value": "Лунинец"}).get_json()["ok"])

    # Категорию и бренд с моделями удалять нельзя
    r = client.post("/api/admin/category/delete", json={"initData": "x", "code": "liquid"})
    c5("категорию с моделями не удалить", r.status_code == 400 and r.get_json()["count"] >= 1)

    # --- Права ---
    deny_admin()
    c4("посторонний ассортимент не видит",
      client.post("/api/admin/models", json={"initData": "x"}).status_code == 403)
    c4("посторонний модель не заведёт",
      client.post("/api/admin/model", json={"initData": "x", "category": "coils", "name": "X"}).status_code == 403)
    c4("посторонний не завезёт товар",
      client.post("/api/admin/product/from-model", json={"initData": "x", "model_id": lid,
                                                         "city": "Минск", "price": "1"}).status_code == 403)
    as_admin()

    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
