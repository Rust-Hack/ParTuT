"""«Сообщить о поступлении»: подписка и рассылка, когда товар вернулся.

Раньше покупатель, попавший на закончившийся товар, просто уходил. Теперь он
оставляет заявку, а продавец видит, сколько человек ждут — что и завозить.
"""
from _common import db, client, server, Checker, as_user, as_admin, SENT, reset_sent


BUYER = 660001
BUYER2 = 660002


def run():
    c = Checker("Сообщить о поступлении")

    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    server._cache_bust()
    # Рассылка уходит в фоновом потоке — в тесте выполняем сразу, иначе проверка
    # обгонит поток и результат будет случайным.
    real_bg = server._bg
    server._bg = lambda fn, *a, **k: fn(*a, **k)

    pid = db.add_product("Минск", "pods", "ЖдуновТовар", 20, 0)     # закончился
    have = db.add_product("Минск", "pods", "ЕстьТовар", 20, 5)      # в наличии

    # --- Подписка ---
    as_user(BUYER, "buyer")
    r = client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    d = r.get_json()
    c("заявка принята", d.get("ok") and d.get("in_stock") is False)
    c("записана в базе", db.alerts_of_user(BUYER) == [pid])

    r = client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    c("повторное нажатие не ломает", (r.get_json() or {}).get("ok"))
    c("дубля не появилось", db.alerts_of_user(BUYER) == [pid])

    r = client.post("/api/notify-me", json={"initData": "x", "product_id": have})
    c("на товар в наличии подписка не нужна", (r.get_json() or {}).get("in_stock") is True)
    c("и не записалась", db.alerts_of_user(BUYER) == [pid])

    c("несуществующий товар → 404",
      client.post("/api/notify-me", json={"initData": "x", "product_id": 987654}).status_code == 404)
    c("мусор вместо id → 400",
      client.post("/api/notify-me", json={"initData": "x", "product_id": "абв"}).status_code == 400)

    as_user(BUYER2, "buyer2")
    client.post("/api/notify-me", json={"initData": "x", "product_id": pid})
    c("ждут двое", db.stock_alert_counts().get(pid) == 2)

    # Продавцу видно, сколько ждут — ради этого счётчик и нужен. Кэш каталога
    # сбрасывается самой подпиской, руками его тут НЕ чистим: иначе тест не
    # заметит, если инвалидацию забудут, и продавец увидит старое число.
    as_admin()
    prod = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                if p["id"] == pid)
    c("счётчик ожидающих виден сразу, без ожидания кэша", prod["waiting"] == 2)
    # А покупателю это знать незачем: сколько человек ждут товар — наша кухня.
    shopper = next(p for p in client.get("/api/products").get_json() if p["id"] == pid)
    c("покупателю счётчик ожидающих не показываем", "waiting" not in shopper)
    c("и закупочную цену тоже", "cost" not in shopper)

    # Витрина должна знать, что покупатель уже подписан.
    as_user(BUYER, "buyer")
    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c("подписки приходят покупателю", me.get("alerts") == [pid])

    # --- Товар вернулся ---
    reset_sent()
    as_admin()
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "stock", "value": 7})
    c("остаток пополнен", (r.get_json() or {}).get("ok"))

    got = {chat for chat, _text, _pm in SENT}
    c("сообщили первому", BUYER in got)
    c("сообщили второму", BUYER2 in got)
    text = next((t for chat, t, _ in SENT if chat == BUYER), "")
    c("в сообщении есть название", "ЖдуновТовар" in text)

    c("подписки погашены", db.stock_alert_counts().get(pid) is None)
    c("повторно не рассылаем", not db.stock_alerts_ready())

    # Второй раз то же событие не должно ничего слать.
    reset_sent()
    client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "stock", "value": 9})
    c("второй раз молчим", not SENT)

    server._bg = real_bg
    conn = db.connect(); conn.cursor().execute("DELETE FROM stock_alerts"); conn.commit(); conn.close()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
