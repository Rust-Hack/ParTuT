"""Удаление товара, по которому есть незакрытые заказы.

Заказ удаление переживает: состав хранится в самом заказе строкой, связи с
таблицей товаров нет. Но продавец остаётся с обязательством выдать то, чего в
магазине больше нет, и узнаёт об этом от покупателя — худший способ.

Поэтому сервер придерживает удаление и называет число. Не запрещает: бывает,
что убрать надо именно сейчас, и решать это человеку, а не нам за него.
"""
from _common import db, client, Checker, as_admin


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("product_variants", "products", "models", "orders"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def run():
    c = Checker("Удаление товара с открытыми заказами")
    _чисто(); as_admin()

    pid = db.add_product("Минск", "pods", "Ходовой", 30.0, 5, cost=18.0)
    c("без заказов удаляется сразу",
      client.post("/api/admin/product/delete", json={"initData": "x", "id": pid}).get_json()["ok"])

    pid2 = db.add_product("Минск", "pods", "Ходовой", 30.0, 5, cost=18.0)
    oid = db.create_order(700001, "buyer", "Минск",
                          [{"id": pid2, "name": "Ходовой", "price": 30.0, "qty": 2}], 60.0, "")
    ответ = client.post("/api/admin/product/delete", json={"initData": "x", "id": pid2})
    c("с открытым заказом — придержано", ответ.status_code == 409)
    тело = ответ.get_json()
    c("сказано, сколько заказов", тело.get("count") == 1)
    c("и сказано по-человечески", "незакрыт" in (тело.get("message") or ""))
    c("товар на месте", db.get_product(pid2) is not None)

    # Выданный заказ держать удаление не должен — он уже закрыт.
    db.set_order_status(oid, "issued")
    c("закрытый заказ не мешает",
      client.post("/api/admin/product/delete", json={"initData": "x", "id": pid2}).get_json()["ok"])

    # Настойчивость: force удаляет, а заказ остаётся цел.
    pid3 = db.add_product("Минск", "pods", "Третий", 30.0, 5, cost=18.0)
    oid3 = db.create_order(700002, "buyer", "Минск",
                           [{"id": pid3, "name": "Третий", "price": 30.0, "qty": 1}], 30.0, "")
    c("без force — придержано",
      client.post("/api/admin/product/delete", json={"initData": "x", "id": pid3}).status_code == 409)
    c("с force — удаляется",
      client.post("/api/admin/product/delete",
                  json={"initData": "x", "id": pid3, "force": True}).get_json()["ok"])
    заказ = db.get_order(oid3)
    c("заказ пережил удаление товара", заказ is not None)
    import json
    c("и состав заказа цел", json.loads(заказ["items"])[0]["name"] == "Третий")

    _чисто()
    return c.fails
