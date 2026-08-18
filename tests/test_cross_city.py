"""Продавец одной точки и чужие заказы.

Границу города уже проверяли на товарах и складе. А заказы остались в стороне —
и это самое опасное место из всех: продавец Турова, дотянувшись до минского
заказа, может отметить его выданным (деньги «получены», никто не поедет),
отклонить его (товар вернётся на полку, покупателю уйдёт извинение за чужую
подпись) или переписать состав.

Проверяем не чтением кода, а попыткой: сервер обязан отказать сам, независимо
от того, показывает приложение чужой заказ на экране или нет.
"""
from _common import db, client, server, Checker, real_auth, deny_admin, as_admin
import config

OWNER = 7410         # владелец
TUROV = 7411         # продавец Турова
MINSK = 7412         # продавец Минска
BUYER = 7413


def _as(uid):
    """Настоящая проверка прав, только подменяем, кто стучится."""
    real_auth()
    server.get_user = lambda init, u=uid: {"id": u, "username": f"u{u}"}
    server.get_admin = lambda init, u=uid: (
        {"id": u, "username": f"u{u}", "role": config.admin_role(u), "city": config.admin_city(u)}
        if config.admin_role(u) else None)


def _post(path, **body):
    return client.post(path, json={"initData": "x", **body})


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    server._cache_bust()


def _order(city, pid):
    oid = db.create_order(BUYER, "buyer", city,
                          [{"id": pid, "name": "Тестовый", "price": 20.0, "qty": 1}], 20.0, "")
    db.set_order_delivery(oid, "Самовывоз", "", 0, "cash")
    db.set_order_status(oid, "paid")       # ждёт подтверждения продавца
    return oid


def run():
    _clean()
    old_owner = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = old_owner | {OWNER}
    db.add_staff(TUROV, "Туров", "продавец Турова")
    db.add_staff(MINSK, "Минск", "продавец Минска")
    config.refresh_staff()

    p_minsk = db.add_product("Минск", "disposable", "Минский", 20.0, 5)
    p_turov = db.add_product("Туров", "disposable", "Туровский", 20.0, 5)
    fails = []
    try:
        o_minsk = _order("Минск", p_minsk)
        o_turov = _order("Туров", p_turov)

        # ---------- Свой заказ ----------
        c = Checker("Свой заказ продавец ведёт")
        _as(TUROV)
        r = _post("/api/admin/order/status", id=o_turov, action="confirm")
        c("подтверждает свой заказ", r.status_code == 200 and r.get_json().get("ok"))
        c("статус изменился", db.get_order(o_turov)["status"] == "confirmed")

        # ---------- Чужой заказ ----------
        c2 = Checker("Чужой заказ закрыт")
        r = _post("/api/admin/order/status", id=o_minsk, action="confirm")
        c2("чужой заказ не подтвердить", r.status_code == 403)
        c2("статус чужого заказа не тронут", db.get_order(o_minsk)["status"] == "paid")

        r = _post("/api/admin/order/status", id=o_minsk, action="issued")
        c2("и выданным не отметить", r.status_code == 403)

        r = _post("/api/admin/order/status", id=o_minsk, action="reject",
                  reason="no_stock", note="")
        c2("и не отклонить", r.status_code == 403)
        c2("склад чужой точки цел", db.get_product(p_minsk)["stock"] == 5)
        c2("заказ по-прежнему ждёт своего продавца",
           db.get_order(o_minsk)["status"] == "paid")

        r = _post("/api/admin/order/items", id=o_minsk, items=[{"id": p_minsk, "qty": 5}])
        c2("состав чужого заказа не переписать", r.status_code == 403)

        # ---------- Список заказов ----------
        c3 = Checker("Список заказов продавца")
        seen = [o["id"] for o in _post("/api/admin/orders").get_json().get("orders", [])]
        c3("свой заказ в списке", o_turov in seen)
        c3("чужого в списке нет", o_minsk not in seen)

        # ---------- Товары ----------
        c4 = Checker("Чужой товар")
        r = _post("/api/admin/product/delete", id=p_minsk)
        c4("чужой товар не удалить", r.status_code == 403)
        c4("товар на месте", db.get_product(p_minsk) is not None)
        r = _post("/api/admin/product/specs", id=p_minsk, specs={"puffs": "9000"})
        c4("характеристики чужого товара не править", r.status_code == 403)
        r = _post("/api/admin/product/variants", id=p_minsk,
                  variants=[{"flavor": "Мята", "stock": 99}])
        c4("вкусы и остатки чужого товара не править", r.status_code == 403)
        c4("остаток чужого товара не изменился", db.get_product(p_minsk)["stock"] == 5)

        # ---------- Продавец всех точек ----------
        # Пустой город — это «все точки», и он обязан работать: иначе владельцы,
        # которые сами продают, не смогут вести свои же заказы.
        c5 = Checker("Продавец всех точек")
        db.add_staff(7414, "", "продавец всех точек")
        config.refresh_staff()
        _as(7414)
        r = _post("/api/admin/order/status", id=o_minsk, action="confirm")
        c5("ведёт заказы любой точки", r.status_code == 200 and r.get_json().get("ok"))
        db.remove_staff(7414)
        config.refresh_staff()

        # ---------- Посторонний ----------
        c6 = Checker("Покупатель")
        deny_admin()
        c6("к заказам не подойдёт", _post("/api/admin/order/status", id=o_turov,
                                          action="issued").status_code == 403)
        c6("и список не увидит", _post("/api/admin/orders").status_code == 403)

        fails = c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails
    finally:
        config.SUPER_ADMIN_IDS = old_owner
        for uid in (TUROV, MINSK):
            db.remove_staff(uid)
        config.refresh_staff()
        as_admin()
        _clean()
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
