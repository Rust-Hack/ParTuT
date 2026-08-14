"""Кэш чтений: каталог отдаётся из памяти, но сбрасывается при изменениях."""
from _common import db, client, server, Checker, as_admin


def run():
    as_admin()
    server._cache_bust()
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

    # доставка тоже кэшируется и обновляется
    d0 = len(client.get("/api/delivery?city=minsk").get_json())
    c("доставка: 1 способ", d0 == 1)
    db.add_delivery_method("minsk", "Курьер", True, "Адрес", "", 5, True)   # в обход → кэш держит старое
    d1 = len(client.get("/api/delivery?city=minsk").get_json())
    c("доставка из кэша: всё ещё 1", d1 == 1)
    client.post("/api/admin/delivery", json={"initData": "x", "city": "minsk", "name": "Ещё"})  # сброс
    d2 = len(client.get("/api/delivery?city=minsk").get_json())
    c("после сброса: 3 способа", d2 == 3)

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
