"""Промокоды: скидка, ограничения и измеримость кампаний.

Скидка — это деньги, поэтому всё считает сервер. Отдельно проверяем, что код
нельзя применить дважды, что он не уводит заказ в минус и что статистика
отвечает на главный вопрос владельца: сработал пост в группе или нет.
"""
from _common import db, client, server, Checker, as_user, as_admin, deny_admin


BUYER = 9701
OTHER = 9702


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM promos")
    cur.execute("DELETE FROM delivery_methods")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    server._cache_bust()


def run():
    c = Checker("Промокоды: создание и ограничения")
    _clean()
    as_admin()

    r = client.post("/api/admin/promo", json={"initData": "x", "code": "avgust10",
                                              "kind": "percent", "value": "10"})
    c("код создан", (r.get_json() or {}).get("ok"))
    c("код приведён к верхнему регистру", db._promo_row("AVGUST10") is not None)

    c("дубль не создаётся",
      client.post("/api/admin/promo", json={"initData": "x", "code": "AVGUST10", "kind": "percent", "value": "5"}).status_code == 400)
    c("код с пробелом отклонён",
      client.post("/api/admin/promo", json={"initData": "x", "code": "два слова", "kind": "percent", "value": "5"}).status_code == 400)
    c("скидка больше 100% отклонена",
      client.post("/api/admin/promo", json={"initData": "x", "code": "X1", "kind": "percent", "value": "150"}).status_code == 400)
    c("нулевая скидка отклонена",
      client.post("/api/admin/promo", json={"initData": "x", "code": "X2", "kind": "percent", "value": "0"}).status_code == 400)

    deny_admin()
    c("посторонний не создаёт коды",
      client.post("/api/admin/promo", json={"initData": "x", "code": "HACK", "kind": "percent", "value": "90"}).status_code == 403)
    as_admin()

    # --- Расчёт скидки ---
    c2 = Checker("Промокод в заказе")
    db.add_delivery_method("Минск", "Самовывоз", False, "", "", 0, True, 0)
    method = db.get_delivery_methods("Минск")[0]
    pid = db.add_product("Минск", "pods", "ПромоПод", 50.0, 30)
    db.set_age_ok(BUYER); db.set_age_ok(OTHER)
    server._cache_bust()
    as_user(BUYER, "buyer")

    def заказать(qty=1, code=None, use_coins=False):
        body = {"initData": "x", "city": "Минск", "delivery_method_id": method["id"],
                "payment_method": "cash", "use_coins": use_coins, "items": [{"id": pid, "qty": qty}]}
        if code:
            body["promo_code"] = code
        return client.post("/api/order", json=body)

    d = client.post("/api/promo/check", json={"initData": "x", "code": "AVGUST10", "subtotal": 50}).get_json()
    c2("проверка до заказа показывает скидку", abs(d["discount"] - 5.0) < 0.01)

    d = заказать(1, "AVGUST10").get_json()
    c2("заказ со скидкой 10%", abs(d["total"] - 45.0) < 0.01)
    заказ = db.get_orders_by_user(BUYER)[0]
    c2("код записан в заказ", заказ["promo_code"] == "AVGUST10")
    c2("скидка записана в заказ", abs(заказ["promo_discount"] - 5.0) < 0.01)

    # --- Один раз на покупателя ---
    r = заказать(1, "AVGUST10")
    c2("повторно тем же кодом нельзя", r.status_code == 400 and r.get_json()["error"] == "promo_once")

    as_user(OTHER, "other")
    d = заказать(1, "AVGUST10").get_json()
    c2("другому покупателю код доступен", abs(d["total"] - 45.0) < 0.01)

    # --- Несуществующий и выключённый ---
    as_user(BUYER)
    r = заказать(1, "НЕТТАКОГО")
    c2("несуществующий код отвергнут", r.get_json()["error"] == "promo_unknown")

    as_admin()
    client.post("/api/admin/promo", json={"initData": "x", "code": "OFF", "kind": "fixed", "value": "7"})
    client.post("/api/admin/promo/toggle", json={"initData": "x", "code": "OFF", "active": False})
    as_user(BUYER)
    r = заказать(1, "OFF")
    c2("выключённый код не работает", r.get_json()["error"] == "promo_unknown")

    # --- Порог суммы ---
    as_admin()
    client.post("/api/admin/promo", json={"initData": "x", "code": "BIG", "kind": "fixed",
                                          "value": "10", "min_total": "120"})
    as_user(BUYER)
    r = заказать(1, "BIG")
    c2("ниже порога код не применяется", r.get_json()["error"] == "promo_min")
    d = заказать(3, "BIG").get_json()          # 150 Br
    c2("выше порога применяется", abs(d["total"] - 140.0) < 0.01)

    # --- Ограничение по числу применений ---
    as_admin()
    client.post("/api/admin/promo", json={"initData": "x", "code": "ONE", "kind": "fixed",
                                          "value": "5", "uses_left": "1", "once_per_user": False})
    as_user(BUYER)
    d = заказать(1, "ONE").get_json()
    c2("первое применение прошло", abs(d["total"] - 45.0) < 0.01)
    r = заказать(1, "ONE")
    c2("второе — код разобран", r.get_json()["error"] == "promo_used_up")

    # --- Скидка не уводит заказ в минус ---
    as_admin()
    client.post("/api/admin/promo", json={"initData": "x", "code": "HUGE", "kind": "fixed",
                                          "value": "500", "once_per_user": False})
    as_user(BUYER)
    d = заказать(1, "HUGE").get_json()
    c2("итог не отрицательный", d["total"] >= 0)
    c2("итог ровно ноль при скидке больше суммы", abs(d["total"]) < 0.01)

    # --- Клиенту верить нельзя ---
    body = {"initData": "x", "city": "Минск", "delivery_method_id": method["id"],
            "payment_method": "cash", "items": [{"id": pid, "qty": 1}],
            "promo_code": "AVGUST10", "promo_discount": 49}
    as_user(OTHER)
    r = client.post("/api/order", json=body)
    c2("присланную скидку сервер игнорирует", r.get_json().get("error") == "promo_once")

    # --- Измеримость: ради этого всё и делается ---
    c3 = Checker("Что принёс код")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE promo_code = %s"), ("AVGUST10",))
    conn.commit(); conn.close()
    as_admin()
    promos = {p["code"]: p for p in client.post("/api/admin/promos", json={"initData": "x"}).get_json()["promos"]}
    c3("видно число заказов по коду", promos["AVGUST10"]["orders"] == 2)
    c3("видно выручку по коду", abs(promos["AVGUST10"]["revenue"] - 90.0) < 0.01)
    c3("видно, сколько скидок роздано", abs(promos["AVGUST10"]["given"] - 10.0) < 0.01)
    c3("неприменявшийся код показан нулём", promos["OFF"]["orders"] == 0)

    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
