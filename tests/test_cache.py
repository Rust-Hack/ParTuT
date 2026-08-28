"""Кэш чтений: каталог отдаётся из памяти, но сбрасывается при изменениях."""
from _common import db, client, Checker, as_admin, as_user

from partut import cache


def run():
    as_admin()
    cache.bust()
    c = Checker("Кэш каталога + инвалидация")

    # чистое состояние
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products"); conn.commit(); conn.close()

    n0 = len(client.get("/api/products").get_json())
    c("старт: список пуст", n0 == 0)

    # добавляем товар В ОБХОД эндпоинта → кэш не сброшен → витрина ещё пустая
    db.add_product("minsk", "pods", "CachedPod", 20, 5)
    n1 = len(client.get("/api/products").get_json())
    c("кэш работает: товар пока не виден", n1 == 0)

    # любой пишущий путь сбрасывает кэш (добавим способ доставки — он в _WRITE_PATHS)
    r = client.post("/api/admin/delivery", json={"initData": "x", "city": "minsk", "name": "Самовывоз"})
    c("write-path → 200", r.status_code == 200)

    n2 = len(client.get("/api/products").get_json())
    c("после записи кэш сброшен: товар виден", n2 == 1)

    # доставка тоже кэшируется и обновляется.
    # Ответ — словарь {methods, points}: точки самовывоза отдаются тем же запросом,
    # чтобы шторка оформления открывалась за один поход на сервер.
    d0 = len(client.get("/api/delivery?city=minsk").get_json()["methods"])
    c("доставка: 1 способ", d0 == 1)
    db.add_delivery_method("minsk", "Курьер", True, "Адрес", "", 5, True)   # в обход → кэш держит старое
    d1 = len(client.get("/api/delivery?city=minsk").get_json()["methods"])
    c("доставка из кэша: всё ещё 1", d1 == 1)
    client.post("/api/admin/delivery", json={"initData": "x", "city": "minsk", "name": "Ещё"})  # сброс
    d2 = len(client.get("/api/delivery?city=minsk").get_json()["methods"])
    c("после сброса: 3 способа", d2 == 3)

    # --- Заказ сбрасывает кэш ТОЧЕЧНО ---
    # Заказ меняет только остатки, поэтому обязан обновить каталог, но НЕ выбрасывать
    # способы доставки: иначе следующий покупатель снова ждёт базу на экране оформления.
    c2 = Checker("Заказ сбрасывает кэш точечно")
    as_user(7171, "cachebuyer")
    db.set_age_ok(7171)
    pid = db.add_product("minsk", "pods", "CacheOrderPod", 15, 4)
    mid = db.get_delivery_methods("minsk")[0]["id"]
    client.get("/api/products"); client.get("/api/delivery?city=minsk")   # прогрели кэш

    db.add_delivery_method("minsk", "Тайный способ", False, "", "", 0, True)  # в обход эндпоинта
    r = client.post("/api/order", json={"initData": "x", "delivery_method_id": mid,
                                        "payment_method": "cash", "items": [{"id": pid, "qty": 1}]})
    c2("заказ оформлен", (r.get_json() or {}).get("ok"))

    prods = {p["name"]: p for p in client.get("/api/products").get_json()}
    c2("каталог обновился: остаток 4 → 3", prods.get("CacheOrderPod", {}).get("stock") == 3)
    d3 = len(client.get("/api/delivery?city=minsk").get_json()["methods"])
    c2("кэш доставки НЕ сброшен заказом (всё ещё 3)", d3 == 3)

    return c.fails + c2.fails


def run_бренды():
    """Правка бренда обязана сбросить кэш /api/brands — до 28 августа не сбрасывала:
    список бренда мог отставать до 5 минут (TTL), а ETag на явный перезапрос
    отвечал 304 — та же стопка, отдающая старое, что и у кэша заказов выше."""
    as_admin()
    cache.bust()
    c = Checker("Кэш брендов + инвалидация")

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM brands"); conn.commit(); conn.close()

    n0 = len(client.get("/api/brands").get_json())
    c("старт: брендов нет", n0 == 0)

    db.add_brand("CacheBrand", "podsystem", [])   # в обход эндпоинта → кэш не знает
    n1 = len(client.get("/api/brands").get_json())
    c("кэш работает: бренд пока не виден", n1 == 0)

    r = client.post("/api/admin/brand", json={"initData": "x", "name": "RealBrand", "category": "podsystem"})
    c("write-path → 200", r.status_code == 200)
    n2 = len(client.get("/api/brands").get_json())
    c("после записи кэш сброшен: оба бренда видны", n2 == 2)

    bid = next(b["id"] for b in client.get("/api/brands").get_json() if b["name"] == "RealBrand")
    client.post("/api/admin/brand", json={"initData": "x", "id": bid, "name": "RealBrand 2", "category": "podsystem"})
    names = {b["name"] for b in client.get("/api/brands").get_json()}
    c("переименование тоже сбрасывает кэш", "RealBrand 2" in names and "RealBrand" not in names)
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_бренды()) else 0)
