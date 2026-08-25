"""Категории товара ведёт владелец, а не код.

Раньше «Расходники» появлялись только правкой файлов и деплоем. Здесь важно
две вещи: коды старых категорий не должны меняться (за ними все существующие
товары и особые поля в редакторе), а удалить непустую категорию нельзя —
иначе товары остались бы в разделе, которого нет, и пропали бы из витрины
молча.
"""
from _common import db, client, Checker, as_admin, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute(db._q("DELETE FROM categories WHERE code NOT IN (%s, %s, %s, %s, %s, %s)"),
                tuple(c[0] for c in db.CATEGORY_SEED))
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Категории товара")
    _clean()
    as_admin()

    codes = db.category_codes()
    c("старые коды на месте", {"disposable", "liquid", "podsystem"} <= codes)
    c("расходники заведены", "coils" in codes)
    c("устройства и аксессуары тоже", {"devices", "accessories"} <= codes)

    lst = client.get("/api/categories").get_json()
    c("витрина получает категории", len(lst) >= 6)
    c("порядок задан", [x["sort"] for x in lst] == sorted(x["sort"] for x in lst))
    c("значок отдаётся", next(x for x in lst if x["code"] == "coils")["emoji"] != "")

    # --- Добавление ---
    r = client.post("/api/admin/category", json={"initData": "x", "name": "Жвачки и паучи", "emoji": "🍬"})
    d = r.get_json()
    c("категория добавлена", d.get("ok"))
    c("код латиницей из названия", d["code"] == "zhvachki_i_pauchi")
    c("новая встала в конец", client.get("/api/categories").get_json()[-1]["code"] == d["code"])
    c("повторное имя отклонено",
      client.post("/api/admin/category", json={"initData": "x", "name": "жвачки и паучи"}).status_code == 400)
    c("пустое имя отклонено",
      client.post("/api/admin/category", json={"initData": "x", "name": "   "}).status_code == 400)

    new_code = d["code"]

    # --- Товар в новой категории ---
    r = client.post("/api/admin/product", json={"initData": "x", "city": "Минск", "category": new_code,
                                                "name": "Паучи Ice", "price": "9", "stock": "4",
                                                "cost": "5"})
    c("товар заводится в новой категории", (r.get_json() or {}).get("ok"))
    pid = r.get_json()["id"]
    cache.bust()
    c("товар виден витрине",
      any(p["id"] == pid for p in client.get("/api/products").get_json()))
    c("выдуманная категория отклонена",
      client.post("/api/admin/product", json={"initData": "x", "city": "Минск", "category": "нетакой",
                                              "name": "Ничто", "price": "1", "stock": "1",
                                              # закупку кладём намеренно: иначе отказ пришёл бы
                                              # из-за неё, и проверка категории прошла бы вхолостую
                                              "cost": "1"}).get_json().get("error") == "bad_data")
    c("перевод товара в выдуманную категорию отклонён",
      client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                                                     "field": "category", "value": "нетакой"}).status_code == 400)

    # --- Переименование не трогает код ---
    client.post("/api/admin/category/update", json={"initData": "x", "code": new_code, "name": "Паучи", "emoji": "🥤"})
    lst = client.get("/api/categories").get_json()
    ren = next(x for x in lst if x["code"] == new_code)
    c("название изменилось", ren["name"] == "Паучи")
    c("значок изменился", ren["emoji"] == "🥤")
    c("код остался прежним — товар не потерялся", db.get_product(pid)["category"] == new_code)

    # --- Удаление ---
    r = client.post("/api/admin/category/delete", json={"initData": "x", "code": new_code})
    c("непустую категорию не удалить", r.status_code == 400 and r.get_json()["error"] == "has_products")
    c("и сказано, сколько там товаров", r.get_json()["count"] == 1)
    db.delete_product(pid)
    cache.bust()
    c("пустая удаляется",
      client.post("/api/admin/category/delete", json={"initData": "x", "code": new_code}).get_json()["ok"])
    c("её больше нет", new_code not in db.category_codes())
    c("удаление несуществующей — 404",
      client.post("/api/admin/category/delete", json={"initData": "x", "code": "нетакой"}).status_code == 404)

    # --- Права ---
    deny_admin()
    c("посторонний не добавит",
      client.post("/api/admin/category", json={"initData": "x", "name": "Своё"}).status_code == 403)
    c("посторонний не переименует",
      client.post("/api/admin/category/update", json={"initData": "x", "code": "coils", "name": "Хлам"}).status_code == 403)
    c("посторонний не удалит",
      client.post("/api/admin/category/delete", json={"initData": "x", "code": "coils"}).status_code == 403)
    c("а список категорий открыт всем", client.get("/api/categories").status_code == 200)
    as_admin()

    # --- Последнюю категорию не удалить ---
    c2 = Checker("Последняя категория")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM categories WHERE code != %s"), ("liquid",))
    conn.commit(); conn.close()
    cache.bust()
    r = client.post("/api/admin/category/delete", json={"initData": "x", "code": "liquid"})
    c2("без категорий остаться нельзя", r.status_code == 400 and r.get_json()["error"] == "last_one")

    # Возвращаем стартовый набор руками: init_db сеет только ОДИН раз за жизнь
    # базы, и рассчитывать, что он подсыплет удалённое, больше нельзя.
    conn = db.connect(); cur = conn.cursor()
    for code, name, emoji, sort, вкусы in db.CATEGORY_SEED:
        if code != "liquid":
            cur.execute(db._q("INSERT INTO categories (code, name, emoji, sort, has_flavors) "
                              "VALUES (%s, %s, %s, %s, %s)"),
                        (code, name, emoji, sort, вкусы))
    conn.commit(); conn.close()
    cache.bust()
    c2("стартовый набор восстановлен", len(db.category_codes()) == len(db.CATEGORY_SEED))

    _clean()
    return c.fails + c2.fails


def run_restore():
    """Возврат стартового набора: добавляет только отсутствующее.

    Засев работает один раз за жизнь базы — удалённое не возвращается само.
    Способ вернуть его осознанно нужен, но он не должен затирать чужую работу:
    свои категории владельца и его переименования обязаны уцелеть.
    """
    c = Checker("Возврат стартовых категорий")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM category_specs")
    cur.execute("DELETE FROM categories")
    conn.commit(); conn.close()
    cache.bust()
    as_admin()

    # Своя категория и переименованная стандартная — их трогать нельзя.
    свой = db.add_category("Паучи", "🟤")
    db.add_category("Одноразки")            # тот же код, что у стандартной
    db.update_category("odnorazki", name="Одноразочки")
    cache.bust()
    было = len(db.category_codes())

    r = client.post("/api/admin/category/restore", json={"initData": "x"})
    d = r.get_json()
    cache.bust()
    c("возврат отработал", r.status_code == 200 and d.get("ok"))
    c("добавлены все шесть стандартных", len(d.get("added") or []) == len(db.CATEGORY_SEED))
    c("своё не тронуто", свой in db.category_codes())
    c("переименованная цела",
      next(x for x in db.list_categories() if x["code"] == "odnorazki")["name"] == "Одноразочки")
    c("итого прибавилось ровно на возвращённые", len(db.category_codes()) == было + len(db.CATEGORY_SEED))
    c("у жидкостей вкусы включены",
      next(x for x in db.list_categories() if x["code"] == "liquid")["has_flavors"] == 1)
    c("у расходников слово «Сопротивление»",
      next(x for x in db.list_categories() if x["code"] == "coils")["variant_label"] == "Сопротивление")
    c("характеристики досыпаны", len([s for s in db.list_category_specs() if s["category"] == "coils"]) > 0)

    # Повторное нажатие ничего не удваивает.
    d2 = client.post("/api/admin/category/restore", json={"initData": "x"}).get_json()
    cache.bust()
    c("повтор ничего не добавил", d2.get("added") == [])

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
