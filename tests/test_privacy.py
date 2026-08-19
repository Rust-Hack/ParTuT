"""Чужое остаётся чужим.

В магазине лежат фотографии чеков об оплате — это платёжные документы живых
людей. Проверяем, что покупатель не дотянется до чужого заказа ни одним из
доступных ему способов: ни через список, ни по номеру, ни по ссылке на чек.

Отдельно про ссылку на чек. Она намеренно открывается по подписанному пропуску,
а не по строке входа Telegram: адреса картинок попадают в логи и историю
браузера, а строка входа — это ключ от аккаунта на сутки. Пропуск привязан к
конкретному файлу, подписан токеном бота и через полдня гаснет.
"""
import io

from _common import db, client, server, Checker, as_user, as_admin, real_auth, deny_admin

import auth

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    cache.bust()


ALICE, BOB = 8101, 8102


def run():
    _clean()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "disposable", "Товар", 10.0, 50)
    for uid in (ALICE, BOB):
        db.set_age_ok(uid)
    cache.bust()

    # Заказ Алисы с чеком.
    as_user(ALICE, "alice")
    oid = client.post("/api/order", json={"initData": "x", "city": "Минск",
                                          "delivery_method_id": mid,
                                          "payment_method": "card",
                                          "items": [{"id": pid, "qty": 1}]}).get_json()["order_id"]
    db.set_order_receipt(oid, "fid_alice")

    c = Checker("Свой заказ виден хозяину")
    mine = client.post("/api/orders", json={"initData": "x"}).get_json()["orders"]
    c("заказ в своём списке", any(o["id"] == oid for o in mine))
    url = [o.get("receipt_url") for o in mine if o["id"] == oid][0]
    c("и ссылка на чек выдана", bool(url))

    # --- Боб ---
    c2 = Checker("Чужой заказ недоступен")
    as_user(BOB, "bob")
    theirs = client.post("/api/orders", json={"initData": "x"}).get_json()["orders"]
    c2("чужого заказа нет в списке", not any(o["id"] == oid for o in theirs))
    c2("чужой заказ не отменить",
       client.post("/api/order/cancel", json={"initData": "x", "order_id": oid}).status_code == 404)
    fd = {"initData": "x", "order_id": str(oid), "file": (io.BytesIO(b"fake"), "c.jpg")}
    c2("свой чек в чужой заказ не подложить",
       client.post("/api/receipt", data=fd, content_type="multipart/form-data").status_code == 404)

    # --- Пропуск к чеку ---
    c3 = Checker("Пропуск к чеку")
    good = server.photo_token("fid_alice")
    c3("с верным пропуском чек открывается", server._may_see_photo("fid_alice", good) is True)
    c3("без пропуска — нет", server._may_see_photo("fid_alice", "") is False)
    c3("с подделанным — нет", server._may_see_photo("fid_alice", "12345.deadbeef") is False)
    c3("пропуск от другого файла не подходит",
       server._may_see_photo("fid_alice", server.photo_token("fid_other")) is False)
    c3("просроченный не подходит", server._token_ok("fid_alice", "1000000000.abc") is False)
    c3("картинки товаров открыты всем", server._may_see_photo("не_чек", "") is True)
    c3("чек без пропуска не отдаётся по ссылке",
       client.get("/api/photo?file_id=fid_alice").status_code == 404)

    # --- Админские адреса покупателю ---
    c4 = Checker("Админское — не покупателю")
    real_auth()
    auth.get_user = lambda init: {"id": BOB, "username": "bob"}
    deny_admin()
    for path in ("/api/admin/orders", "/api/admin/customer", "/api/admin/users",
                 "/api/admin/stats", "/api/admin/log"):
        c4(f"{path} закрыт",
           client.post(path, json={"initData": "x", "user_id": ALICE}).status_code == 403)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
