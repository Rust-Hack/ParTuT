"""Избранное на сервере: раньше жило только в localStorage браузера и
пропадало при смене устройства, а владелец не видел спрос вовсе.
"""
from _common import db, client, Checker, as_user, as_admin

from partut import cache


BUYER = 660101
BUYER2 = 660102


def run():
    c = Checker("Избранное")

    conn = db.connect(); conn.cursor().execute("DELETE FROM favorites"); conn.commit(); conn.close()
    cache.bust()

    pid = db.add_product("Минск", "pods", "ИзбранныйПод", 20, 5)
    pid2 = db.add_product("Минск", "pods", "ДругойПод", 15, 5)

    # --- Добавление и повтор без дублей ---
    as_user(BUYER, "buyer")
    r = client.post("/api/favorite", json={"initData": "x", "product_id": pid})
    c("добавлено", (r.get_json() or {}).get("ok"))
    c("записано в базе", db.favorites_for_user(BUYER) == [pid])

    client.post("/api/favorite", json={"initData": "x", "product_id": pid})
    c("повторное нажатие не плодит дубль", db.favorites_for_user(BUYER) == [pid])

    c("несуществующий id → 400 (мусор)",
      client.post("/api/favorite", json={"initData": "x", "product_id": "абв"}).status_code == 400)

    # --- Снятие ---
    client.post("/api/favorite", json={"initData": "x", "product_id": pid2})
    c("второй товар добавлен", set(db.favorites_for_user(BUYER)) == {pid, pid2})
    r = client.post("/api/favorite", json={"initData": "x", "product_id": pid, "off": True})
    c("снято", (r.get_json() or {}).get("ok"))
    c("остался только второй", db.favorites_for_user(BUYER) == [pid2])

    # --- Список приходит в /api/me ---
    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c("избранное приходит покупателю", me.get("favorites") == [pid2])

    # --- Счётчик виден владельцу, не покупателю ---
    as_user(BUYER2, "buyer2")
    client.post("/api/favorite", json={"initData": "x", "product_id": pid2})
    c("двое в избранном", db.favorite_counts().get(pid2) == 2)

    as_admin()
    prod = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                if p["id"] == pid2)
    c("счётчик виден админу", prod["favored"] == 2)
    shopper = next(p for p in client.get("/api/products").get_json() if p["id"] == pid2)
    c("покупателю счётчик не показываем", "favored" not in shopper)

    conn = db.connect(); conn.cursor().execute("DELETE FROM favorites"); conn.commit(); conn.close()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
