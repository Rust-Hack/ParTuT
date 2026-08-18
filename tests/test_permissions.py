"""Границы продавца и журнал действий.

Город в «Доступе» выглядел как разграничение, а на деле решал одно: кому падает
уведомление о заказе. Продавец Турова мог править цены Минска, удалить модель,
поменять реквизиты оплаты и посмотреть выручку всего магазина.

Здесь проверяются НАСТОЯЩИЕ проверки прав, а не заглушка as_admin(): иначе тест
про доступ проверял бы сам себя.
"""
from _common import (db, client, server, Checker, as_admin, real_auth, REAL_GET_USER)

import config


SELLER = 7301        # продавец Турова
BOSS = 7302          # админ без города — полный доступ


def _as(uid):
    """Запросы идут от этого человека — через настоящую проверку прав."""
    real_auth()
    server.get_user = lambda init: {"id": uid, "username": f"u{uid}"}


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM models")
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM admin_log")
    conn.commit(); conn.close()
    server._cache_bust()


def _post(path, **body):
    return client.post(path, json={"initData": "x", **body})


def run():
    c = Checker("Продавец точки")
    _clean()
    db.add_staff(SELLER, "Туров", "продавец")
    db.add_staff(BOSS, "", "правая рука")
    config.refresh_staff()

    mid = db.add_model("podsystem", "XROS", brand="Vaporesso")
    minsk = db.add_product_from_model(mid, "Минск", 30.0, stock=5)
    turov = db.add_product_from_model(mid, "Туров", 32.0, stock=4)

    _as(SELLER)
    c("свою точку правит",
      _post("/api/admin/product/update", id=turov, field="price", value="35").get_json().get("ok"))
    r = _post("/api/admin/product/update", id=minsk, field="price", value="1")
    c("чужую — нет", r.status_code == 403 and r.get_json()["error"] == "other_city")
    c("и цена чужой точки не поменялась", db.get_product(minsk)["price"] == 30.0)
    c("чужой товар не удалит",
      _post("/api/admin/product/delete", id=minsk).status_code == 403)
    c("чужой склад не тронет",
      _post("/api/admin/stock/move", id=minsk, qty=1, reason="in").status_code == 403)
    c("свой склад — пожалуйста",
      _post("/api/admin/stock/move", id=turov, qty=2, reason="in").get_json().get("ok"))
    c("к себе на точку завозит",
      _post("/api/admin/product/from-model", model_id=mid, city="Туров", price="20").status_code
      == 400)     # already_here: модель уже стоит — но это не отказ по правам
    c("на чужую точку завезти не может",
      _post("/api/admin/product/from-model", model_id=mid, city="Лунинец", price="20").status_code == 403)
    r = _post("/api/admin/product/update", id=turov, field="city", value="Минск")
    c("и не может увезти товар на чужую точку", r.status_code == 403)
    c("товар остался на своей", db.get_product(turov)["city"] == "Туров")

    # --- Общее для магазина ---
    c2 = Checker("Что меняет магазин целиком")
    r = _post("/api/admin/model", category="podsystem", name="Своя модель")
    c2("модель не заведёт", r.status_code == 403 and r.get_json()["error"] == "owner_only")
    c2("и объяснено почему", "владельца" in (r.get_json().get("message") or ""))
    c2("модель не удалит", _post("/api/admin/model/delete", id=mid).status_code == 403)
    c2("бренд не тронет", _post("/api/admin/brand", name="Левый").status_code == 403)
    c2("категорию не заведёт", _post("/api/admin/category", name="Своя").status_code == 403)
    c2("реквизиты оплаты не поменяет",
      _post("/api/admin/settings/update", payment_info="мой кошелёк").status_code == 403)
    c2("точку продаж не создаст", _post("/api/admin/location", name="Своя").status_code == 403)
    c2("промокод не выпишет", _post("/api/admin/promo", code="SELF50").status_code == 403)
    c2("выручку всего магазина не увидит", _post("/api/admin/stats").status_code == 403)
    c2("но ассортимент посмотреть может", _post("/api/admin/models").get_json().get("ok"))
    c2("и реквизиты прочитать может — их спрашивает покупатель",
      _post("/api/admin/settings").get_json().get("ok"))

    # --- Заказы своего города ---
    c3 = Checker("Заказы своей точки")
    o_turov = db.create_order(500, "kl", "Туров", [{"id": turov, "name": "XROS", "price": 32.0, "qty": 1}], 32.0, "")
    o_minsk = db.create_order(501, "kl", "Минск", [{"id": minsk, "name": "XROS", "price": 30.0, "qty": 1}], 30.0, "")
    ids = {o["id"] for o in _post("/api/admin/orders").get_json()["orders"]}
    c3("свой заказ виден", o_turov in ids)
    c3("чужой — нет", o_minsk not in ids)
    c3("чужой заказ не подтвердить",
      _post("/api/admin/order/status", id=o_minsk, action="confirm").status_code == 403)

    # --- Админ без города ---
    c4 = Checker("Админ над всеми точками")
    _as(BOSS)
    c4("правит любую точку",
      _post("/api/admin/product/update", id=minsk, field="price", value="31").get_json().get("ok"))
    c4("видит все заказы",
      {o["id"] for o in _post("/api/admin/orders").get_json()["orders"]} >= {o_turov, o_minsk})
    c4("заводит модель", _post("/api/admin/model", category="podsystem", name="Общая").get_json().get("ok"))

    # --- Журнал ---
    c5 = Checker("Журнал действий")
    rows = db.list_admin_log(limit=50)
    prices = [r for r in rows if r["action"] == "product/update"]
    c5("правка цены записана", bool(prices))
    c5("видно, кто именно", any(int(r["admin_id"]) == SELLER for r in prices))
    c5("видно, что менялось", any("price" in (r["details"] or "") for r in prices))
    c5("видно, у какого товара", any(str(turov) in (r["details"] or "") for r in prices))
    c5("отказ по правам в журнал не попал",
      all(int(r["admin_id"]) != SELLER or "Минск" not in (r["details"] or "") for r in rows))
    c5("чтение не засоряет журнал", all(r["action"] not in ("orders", "models", "stats") for r in rows))
    c5("секрет не сохранён", all("initData" not in (r["details"] or "") for r in rows))
    c5("заведение модели записано", any(r["action"] == "model" for r in rows))

    # Журнал видит только владелец.
    _as(SELLER)
    c5("продавцу журнал закрыт", _post("/api/admin/log").status_code == 403)

    db.remove_staff(SELLER)
    db.remove_staff(BOSS)
    config.refresh_staff()
    as_admin()                 # вернуть общий стенд в исходное состояние
    server.get_user = REAL_GET_USER
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
