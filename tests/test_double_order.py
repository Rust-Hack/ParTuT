"""Второй заказ вместо одного.

Кнопка «Оформить» блокируется на время отправки — но это защита внутри
приложения. Телефон в метро теряет ответ, а не запрос: заказ на сервере уже
создан, человек видит «Сеть недоступна» и жмёт снова. Получалось два заказа,
товар списывался дважды, монеты — тоже, а продавец видел два одинаковых заказа
и не знал, какой настоящий.

Поэтому у каждой попытки оформления есть ключ, один на все повторы. По нему
сервер возвращает ТОТ ЖЕ заказ. Последнее слово — за уникальным ключом в базе:
двойное нажатие отправляет два запроса разом, и оба успевают не найти прежний
заказ.
"""
import datetime
import threading

from _common import db, client, server, Checker, as_user, as_admin, SENT, reset_sent

UID = 8801


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (UID,))
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    server._cache_bust()


def _count_orders():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM orders")
    n = int(cur.fetchone()["n"])
    conn.close()
    return n


def run():
    _clean()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "pods", "Под", 25.0, 2)
    db.set_age_ok(UID)
    db.add_coins(UID, 100, "other")
    server._cache_bust()
    as_user(UID, "спешит")

    def order(token, qty=2, coins=False):
        return client.post("/api/order", json={
            "initData": "x", "city": "Минск", "delivery_method_id": mid,
            "payment_method": "cash", "items": [{"id": pid, "qty": qty}],
            "use_coins": coins, "client_token": token}).get_json()

    c = Checker("Ответ потерялся, человек нажал снова")
    reset_sent()
    первый = order("попытка-1")
    повтор = order("попытка-1")
    c("первый заказ создан", первый.get("ok") is True)
    c("повтор вернул тот же заказ", повтор.get("order_id") == первый.get("order_id"))
    c("и честно помечен как повтор", повтор.get("repeat") is True)
    c("заказ в базе один", _count_orders() == 1)
    c("товар списан один раз", db.get_product(pid)["stock"] == 0)
    c("продавцу не пришёл второй заказ",
      sum(1 for x in SENT if "Новый заказ" in str(x[1])) <= 1)

    # Повтор обязан привести человека туда же, куда привёл бы первый ответ:
    # на экран оплаты или на «заказ принят». Значит в ответе те же числа.
    c("сумма в повторе та же", повтор.get("total") == первый.get("total"))
    c("способ получения тот же", повтор.get("delivery_method") == первый.get("delivery_method"))
    c("способ оплаты тот же", повтор.get("payment_method") == первый.get("payment_method"))

    # --- Двойное нажатие: два запроса разом ---
    c2 = Checker("Два запроса вышли одновременно")
    _clean()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "pods", "Под", 25.0, 20)
    db.set_age_ok(UID)
    server._cache_bust()

    ответы = []
    потоки = [threading.Thread(target=lambda: ответы.append(order("попытка-2"))) for _ in range(6)]
    for t in потоки:
        t.start()
    for t in потоки:
        t.join()
    номера = {r.get("order_id") for r in ответы if r.get("ok")}
    c2("все шесть получили один и тот же номер заказа", len(номера) == 1)
    c2("никому не отказано", all(r.get("ok") for r in ответы))
    c2("в базе ровно один заказ", _count_orders() == 1)
    c2("товар списан за один заказ", db.get_product(pid)["stock"] == 18)

    # --- Намеренный второй заказ ---
    c3 = Checker("Второй заказ по-настоящему")
    другой = order("попытка-3")
    c3("новая попытка создаёт новый заказ", другой.get("order_id") not in номера)
    c3("и он не помечен повтором", not другой.get("repeat"))
    c3("заказов стало два", _count_orders() == 2)

    # Без ключа всё работает как раньше: старое приложение в чьём-то телефоне
    # не должно перестать оформлять заказы после обновления сервера.
    без_ключа = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": mid,
        "payment_method": "cash", "items": [{"id": pid, "qty": 1}]}).get_json()
    c3("старое приложение без ключа оформляет как прежде", без_ключа.get("ok") is True)

    # --- Ключ старше суток ---
    # Уникальный ключ в базе вечен, а окно поиска — сутки. Повтор давней попытки
    # проваливался бы мимо поиска, упирался в ключ и отвечал ошибкой сервера
    # вместо собственного заказа человека.
    c35 = Checker("Повтор давней попытки")
    свежий = order("попытка-старая")
    conn = db.connect(); cur = conn.cursor()
    давно = (datetime.datetime.now() - datetime.timedelta(days=40)).strftime("%Y-%m-%d %H:%M")
    cur.execute(db._q("UPDATE orders SET created_at = %s WHERE id = %s"),
                (давно, свежий["order_id"]))
    conn.commit(); conn.close()
    было = _count_orders()
    r = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": mid,
        "payment_method": "cash", "items": [{"id": pid, "qty": 2}],
        "client_token": "попытка-старая"})
    c35("ошибки сервера нет", r.status_code == 200)
    c35("вернулся тот же заказ", r.get_json().get("order_id") == свежий["order_id"])
    c35("второго заказа не появилось", _count_orders() == было)

    # --- Чужой ключ ---
    # Ключ придумывает клиент, значит его могут прислать чужой. Чужой заказ по
    # нему доставаться не должен.
    c4 = Checker("Чужой ключ")
    as_user(8802, "другой")
    db.set_age_ok(8802)
    чужой = order("попытка-3")
    c4("другому человеку отдан его собственный заказ",
       чужой.get("ok") and чужой.get("order_id") != другой.get("order_id"))
    c4("и он не помечен повтором", not чужой.get("repeat"))

    as_admin()
    _clean()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (8802,))
    conn.commit(); conn.close()
    return c.fails + c2.fails + c35.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
