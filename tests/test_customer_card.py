"""Карточка покупателя: вся история одного человека на одном экране.

Смысл экрана — ответить продавцу на вопросы «кто это, что берёт и сколько
принёс». Поэтому здесь важнее всего, чтобы деньги считались по ВЫДАННЫМ
заказам: отменённый и брошенный заказ покупкой не является, и складывать его
в сумму значит завышать ценность клиента и звонить не тем людям.
"""
import datetime

from _common import db, client, server, Checker, as_admin, deny_admin


BUYER = 9701
OTHER = 9702


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    cur.execute(db._q("DELETE FROM users WHERE user_id IN (%s, %s)"), (BUYER, OTHER))
    conn.commit(); conn.close()
    server._cache_bust()


def _order(uid, items, total, status, created=None, username="vasya"):
    oid = db.create_order(uid, username, "Минск", items, total, "")
    conn = db.connect(); cur = conn.cursor()
    if created:
        cur.execute(db._q("UPDATE orders SET status = %s, created_at = %s WHERE id = %s"),
                    (status, created, oid))
    else:
        cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, oid))
    conn.commit(); conn.close()
    return oid


def _days_ago(n):
    return (datetime.datetime.now() - datetime.timedelta(days=n)).strftime("%Y-%m-%d %H:%M")


def run():
    c = Checker("Карточка покупателя")
    _clean()
    as_admin()

    db.ensure_user(BUYER)
    db.add_coins(BUYER, 120)
    db.set_user_phone(BUYER, "+375291112233")

    pod = {"id": 1, "name": "Эльфбар", "price": 20.0, "cost": 12.0, "qty": 2, "flavor": "Мята"}
    liq = {"id": 2, "name": "Жижа", "price": 15.0, "cost": 0.0, "qty": 1}

    _order(BUYER, [pod], 40.0, "issued", _days_ago(30))
    _order(BUYER, [dict(pod, qty=1), liq], 35.0, "issued", _days_ago(3))
    _order(BUYER, [liq], 15.0, "canceled", _days_ago(2))
    _order(BUYER, [liq], 15.0, "new", _days_ago(1))
    _order(OTHER, [dict(pod, qty=5)], 100.0, "issued", _days_ago(1), username="petya")

    card = client.post("/api/admin/customer", json={"initData": "x", "user_id": BUYER}).get_json()["card"]

    # --- Деньги ---
    c("потрачено только по выданным", abs(card["spent"] - 75.0) < 0.01)
    c("выданных заказов двое", card["issued"] == 2)
    c("отменённый посчитан отдельно", card["canceled"] == 1)
    c("незакрытый заказ виден отдельно", card["open"] == 1)
    c("всего заказов четыре", card["orders_total"] == 4)
    c("средний чек посчитан", abs(card["avg_check"] - 37.5) < 0.01)
    # Прибыль: 3 пода по (20-12) = 24. Жижа без закупки в прибыль не идёт.
    c("прибыль только по известной закупке", abs(card["profit"] - 24.0) < 0.01)
    c("отмечено, что закупка известна", card["profit_known"] is True)
    c("чужой заказ не приплюсовался", abs(card["spent"] - 75.0) < 0.01)

    # --- Кто это ---
    c("имя взято из заказов", card["username"] == "vasya")
    c("монеты видны", card["coins"] == 120)
    c("телефон виден", card["phone"] == "+375291112233")

    # --- Когда покупал ---
    c("дата последней покупки — от выданного заказа", card["last_buy"][:10] == _days_ago(3)[:10])
    c("первая покупка — самая старая", card["first_buy"][:10] == _days_ago(30)[:10])
    c("посчитано, сколько дней молчит", card["days_since"] == 3)

    # --- Что берёт ---
    fav = {f["name"]: f["qty"] for f in card["favorites"]}
    c("любимый товар посчитан по штукам", fav["Эльфбар"] == 3)
    c("второй товар тоже в списке", fav["Жижа"] == 1)
    c("сверху — тот, кого берут чаще", card["favorites"][0]["name"] == "Эльфбар")

    # --- История ---
    c("история отдана", len(card["history"]) == 4)
    c("новые сверху", card["history"][0]["status"] == "new")
    c("в заказе видно позиции", card["history"][0]["items"][0]["name"] == "Жижа")
    поды = next(h for h in card["history"] if h["items"][0]["name"] == "Эльфбар")
    c("вкус сохранён в истории", поды["items"][0]["flavor"] == "Мята")

    # --- Мусор и права ---
    c("несуществующий покупатель — 404",
      client.post("/api/admin/customer", json={"initData": "x", "user_id": 424242}).status_code == 404)
    c("нечисловой id отклонён",
      client.post("/api/admin/customer", json={"initData": "x", "user_id": "абв"}).status_code == 400)

    deny_admin()
    c("посторонний карточку не видит",
      client.post("/api/admin/customer", json={"initData": "x", "user_id": BUYER}).status_code == 403)
    as_admin()

    # --- Покупатель без закупочных цен ---
    c2 = Checker("Покупатель без закупки")
    db.ensure_user(OTHER)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM orders WHERE user_id = %s"), (OTHER,))
    conn.commit(); conn.close()
    _order(OTHER, [liq], 15.0, "issued", _days_ago(1), username="petya")
    card2 = client.post("/api/admin/customer", json={"initData": "x", "user_id": OTHER}).get_json()["card"]
    c2("выручка есть", abs(card2["spent"] - 15.0) < 0.01)
    c2("прибыль не выдумана", abs(card2["profit"]) < 0.01)
    c2("и честно помечено, что считать не из чего", card2["profit_known"] is False)

    # --- Покупатель, который ничего не купил ---
    c2("новичок без заказов открывается",
      client.post("/api/admin/customer", json={"initData": "x", "user_id": BUYER + 50}).status_code == 404)
    db.ensure_user(BUYER + 50)
    card3 = client.post("/api/admin/customer", json={"initData": "x", "user_id": BUYER + 50}).get_json()["card"]
    c2("у новичка нули, а не ошибка", card3["spent"] == 0 and card3["issued"] == 0)
    c2("и нет даты последней покупки", card3["days_since"] is None)

    _clean()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
