"""Правка товара идёт ОДНИМ запросом, а не по запросу на поле.

Сохранение карточки меняло до десяти полей и слало на каждое отдельный запрос.
По мобильной сети это десять полных обменов подряд — на живом магазине замерено
0.7–2.6 с на запрос, то есть «Сохраняю…» висело около десяти секунд.

Проверяем не скорость (её в тесте не измерить честно), а то, ради чего пачка
заводилась: все поля применяются за один вызов, отказ по одному полю не теряет
остальные, а отказ по правам остаётся отказом по правам.
"""
from _common import db, client, Checker, as_admin, as_user, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Правка товара пачкой")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "Пачка-под", 20.0, 5, cost=10.0)

    # --- Всё сразу ---
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "25.5", "cost": "12", "stock": "7", "name": "Пачка-под 2", "is_hit": 1}})
    d = r.get_json()
    c("пачка принята", r.status_code == 200 and d.get("ok"))
    c("сервер назвал, что сохранил", set(d.get("saved") or []) == {"price", "cost", "stock", "name", "is_hit"})
    p = db.get_product(pid)
    c("цена легла", abs(float(p["price"]) - 25.5) < 0.01)
    c("закупка легла", abs(float(p["cost"]) - 12) < 0.01)
    c("остаток лёг", int(p["stock"]) == 7)
    c("название легло", p["name"] == "Пачка-под 2")
    c("хит лёг", int(p["is_hit"]) == 1)

    # --- Кривое поле не роняет всю пачку ---
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "-5", "description": "хорошая штука"}})
    d = r.get_json()
    c("пачка с одним кривым полем отвечает ok", d.get("ok"))
    c("хорошее поле сохранено", db.get_product(pid)["description"] == "хорошая штука")
    c("плохое названо отдельно", "price" in (d.get("failed") or {}))
    c("и цена осталась прежней", abs(float(db.get_product(pid)["price"]) - 25.5) < 0.01)

    # --- Одиночное поле работает как раньше: на нём переключатели в списке ---
    r = client.post("/api/admin/product/update",
                    json={"initData": "x", "id": pid, "field": "hidden", "value": 1})
    c("одиночное поле принимается", r.get_json().get("ok"))
    c("и применяется", int(db.get_product(pid)["hidden"]) == 1)

    r = client.post("/api/admin/product/update",
                    json={"initData": "x", "id": pid, "field": "price", "value": "-1"})
    c("одиночное кривое — 400 с текстом", r.status_code == 400 and r.get_json().get("message"))

    # --- Права: пачка не обходит проверку точки ---
    as_user(9500); deny_admin()
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {"price": "1"}})
    c("посторонний не правит пачкой", r.status_code == 403)
    c("цена цела", abs(float(db.get_product(pid)["price"]) - 25.5) < 0.01)

    as_admin()
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
