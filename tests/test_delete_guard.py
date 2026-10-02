"""Удалить товар насовсем — только из архива и только без истории.

Раньше «Удалить с точки» стоял рядом с повседневными действиями: с
невыданными заказами — с вопросом «всё равно?», без них — сразу. Удаление
уносило историю склада (движения теряли товар) и отзывы. Теперь на «больше
не возим» отвечает архив (tests/test_archive.py), а удаление осталось для
товара, заведённого по ошибке. Заказ при этом переживает всё: его состав
хранится в нём самом.
"""
import json

from _common import db, client, Checker, as_admin


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("product_variants", "stock_moves", "products", "models", "orders"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _удалить(pid, **ещё):
    return client.post("/api/admin/product/delete", json={"initData": "x", "id": pid, **ещё})


def _в_архив(pid):
    return client.post("/api/admin/product/archive", json={"initData": "x", "id": pid, "archived": True})


def run():
    c = Checker("Удаление товара насовсем")
    _чисто(); as_admin()

    # Не из архива — нельзя, даже с force (его шлёт старая страница).
    pid = db.add_product("Минск", "pods", "По ошибке", 30.0, 0, cost=18.0)
    for ещё in ({}, {"force": True}):
        r = _удалить(pid, **ещё)
        c(f"не из архива — отказ «уберите в архив» {ещё}",
          r.status_code == 409 and r.get_json()["error"] == "use_archive" and "В архив" in r.get_json()["message"])
    c("товар на месте", db.get_product(pid) is not None)

    # Из архива, без истории — удаляется, и хвосты (избранное, «жду») с ним.
    db.add_favorite(pid, 700099)
    db.add_stock_alert(pid, 700099)
    c("в архив", _в_архив(pid).get_json()["ok"])
    c("из архива без истории — удаляется", _удалить(pid).get_json()["ok"])
    c("товара нет", db.get_product(pid) is None)
    c("избранное на удалённый товар не осталось", db.favorites_for_user(700099) == [])
    c("подписка на удалённый товар не осталась", db.alerts_of_user(700099) == [])

    # Был в заказе (уже выданном) — история, не удаляется.
    pid2 = db.add_product("Минск", "pods", "Ходовой", 30.0, 0, cost=18.0)
    oid = db.create_order(700001, "buyer", "Минск",
                          [{"id": pid2, "name": "Ходовой", "price": 30.0, "qty": 2}], 60.0, "")
    db.set_order_status(oid, "issued")
    # Номер 2 в чужом заказе не путается с 12 / 120: считаем по разбору состава.
    db.create_order(700002, "buyer", "Минск",
                    [{"id": pid2 * 10, "name": "Другой", "price": 1.0, "qty": 1}], 1.0, "")
    c("в архив (заказ выдан)", _в_архив(pid2).get_json()["ok"])
    r = _удалить(pid2)
    c(f"был в заказе — не удаляется: {r.get_json()}",
      r.status_code == 409 and r.get_json()["error"] == "has_history" and r.get_json()["orders"] == 1)
    c("и сказано, что он останется в архиве", "останется в архиве" in r.get_json()["message"])
    c("товар на месте, в архиве", db.get_product(pid2)["archived"] == 1)
    c("заказ цел", json.loads(db.get_order(oid)["items"])[0]["name"] == "Ходовой")

    # Есть движения склада — тоже история.
    mid = db.add_model("pods", "С приходом")
    pid3 = db.create_point_product(mid, "Минск", 30.0, 18.0, stock=2)
    db.stock_operation(pid3, "lost", 2, admin_id=1)
    c("остаток списан — в архив", _в_архив(pid3).get_json()["ok"])
    r = _удалить(pid3)
    c(f"есть движения склада — не удаляется: {r.get_json().get('moves')}",
      r.status_code == 409 and r.get_json()["error"] == "has_history" and r.get_json()["moves"] == 2)

    # Продавцу удалять насовсем нельзя — даже своё и без истории.
    pid4 = db.add_product("Минск", "pods", "Продавца", 30.0, 0, cost=18.0)
    _в_архив(pid4)
    as_admin(uid=9200, username="продавец", role="staff", city="Минск")
    r = _удалить(pid4)
    c("продавцу удалить насовсем нельзя", r.status_code == 403 and db.get_product(pid4) is not None)
    as_admin()

    _чисто()
    return c.fails
