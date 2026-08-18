"""Реферальная программа: за что платим и за что нет.

Приглашение приносит монеты, значит на нём будут пробовать зарабатывать.
Проверяем границы:

  • себя пригласить нельзя;
  • пригласившего нельзя сменить задним числом на того, кто платит больше;
  • «я привёл того, кто привёл меня» — тоже нельзя: настоящей рекомендации тут
    нет, это два аккаунта одного человека, и оба получали бонус за первый заказ;
  • деньги приходят только за ВЫДАННЫЙ заказ, а не за оформленный: иначе схема
    «оформил — получил монеты — отменил» печатает монеты из воздуха;
  • бонус за первый заказ друга даётся один раз, дальше только процент.
"""
from _common import db, client, server, Checker, as_user, as_admin

A, B, C = 8301, 8302, 8303


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM users WHERE user_id BETWEEN %s AND %s"), (8300, 8399))
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    server._cache_bust()


def _open_app(uid, start=None):
    as_user(uid, f"u{uid}")
    body = {"initData": "x"}
    if start:
        body["start_param"] = start
    return client.post("/api/me", json=body).get_json()


def run():
    _clean()
    for uid in (A, B, C):
        db.ensure_user(uid)
        db.set_age_ok(uid)

    c = Checker("Кто кого пригласил")
    _open_app(A, f"ref{A}")
    c("себя пригласить нельзя", db.get_user_row(A)["referred_by"] is None)

    _open_app(B, f"ref{A}")
    c("обычное приглашение записывается", db.get_user_row(B)["referred_by"] == A)

    _open_app(A, f"ref{B}")
    c("взаимное приглашение запрещено", db.get_user_row(A)["referred_by"] is None)

    _open_app(C, f"ref{A}")
    _open_app(C, f"ref{B}")
    c("пригласившего не сменить задним числом", db.get_user_row(C)["referred_by"] == A)

    _open_app(C, "ref999999")
    c("несуществующий пригласивший не записывается", db.get_user_row(C)["referred_by"] == A)

    # --- Когда приходят деньги ---
    c2 = Checker("Награда только за выданный заказ")
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "disposable", "Товар", 10.0, 99)
    server._cache_bust()

    def order_by(uid):
        as_user(uid, f"u{uid}")
        return client.post("/api/order", json={"initData": "x", "city": "Минск",
                                               "delivery_method_id": mid,
                                               "payment_method": "cash",
                                               "items": [{"id": pid, "qty": 1}]}).get_json()

    def status(oid, action):
        as_admin()
        return client.post("/api/admin/order/status",
                           json={"initData": "x", "id": oid, "action": action}).get_json()

    before = db.get_coins(A)
    d = order_by(B)
    c2("за оформленный заказ пригласившему не платят", db.get_coins(A) == before)
    status(d["order_id"], "confirm")
    c2("за подтверждённый — тоже", db.get_coins(A) == before)
    status(d["order_id"], "issued")
    earned_first = db.get_coins(A) - before
    c2("платят за выданный", earned_first > 0)

    # Отменить выданный заказ нельзя, но проверим, что повторная выдача
    # не начисляет второй раз: статус меняется ровно один раз.
    status(d["order_id"], "issued")
    c2("повторная выдача не платит второй раз", db.get_coins(A) - before == earned_first)

    # --- Бонус за первый заказ друга — один раз ---
    c3 = Checker("Бонус за первого друга")
    before2 = db.get_coins(A)
    d2 = order_by(B)
    status(d2["order_id"], "confirm")
    status(d2["order_id"], "issued")
    earned_second = db.get_coins(A) - before2
    c3("второй заказ друга приносит меньше первого", 0 < earned_second < earned_first)
    c3("отметка «первый заказ» проставлена", db.get_user_row(B)["ref_activated"] == 1)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
