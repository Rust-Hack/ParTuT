"""Настройки покупателя: телефон и подписки на поступление.

Две вещи, которые молча работали не так, как выглядели.

Телефон сохранялся любой. Написал человек «+375» и ушёл — огрызок осел в базе
и с тех пор подставлялся в каждый заказ. Продавец звонит в никуда и решает, что
клиент бросил трубку. Пустое поле при этом законно: «не хочу указывать» — это
ответ, а неполный номер — нет.

Подписка «сообщите, когда появится» ставилась одним нажатием, а снять её было
негде. Единственным способом перестать получать сообщения о товаре, который
уже не нужен, оставалось заблокировать бота — и заодно потерять покупателя.
"""
from _common import db, client, server, Checker, as_user, as_admin


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM stock_alerts")
    cur.execute("DELETE FROM users WHERE user_id >= 9600 AND user_id < 9700")
    conn.commit(); conn.close()
    server._cache_bust()


def _save(**kw):
    body = {"initData": "x"}
    body.update(kw)
    return client.post("/api/my-settings", json=body)


def run():
    c = Checker("Телефон в настройках")
    _clean()
    as_user(9601, "buyer")

    r = _save(phone="+375 29 111-22-33")
    c("нормальный номер сохраняется", r.get_json().get("ok") is True)
    c("и возвращается как есть", r.get_json().get("phone") == "+375 29 111-22-33")

    r = _save(phone="+375")
    c("огрызок номера отклонён", r.status_code == 400 and r.get_json()["error"] == "bad_phone")
    c("и старый номер не затёрт", db.get_user_phone(9601) == "+375 29 111-22-33")

    r = _save(phone="")
    c("пустое поле — законный ответ", r.get_json().get("ok") is True)
    c("телефон действительно очищен", db.get_user_phone(9601) == "")

    # --- Подписки на поступление ---
    c2 = Checker("Жду поступления")
    pid = db.add_product("Минск", "disposable", "Кончился", 20.0, 0)
    server._cache_bust()

    def notify(**kw):
        body = {"initData": "x", "product_id": pid}
        body.update(kw)
        return client.post("/api/notify-me", json=body)

    notify()
    c2("подписка встала", db.alerts_of_user(9601) == [pid])
    notify()
    c2("повторное нажатие не плодит дублей", db.alerts_of_user(9601) == [pid])

    notify(off=True)
    c2("отписка сработала", db.alerts_of_user(9601) == [])
    c2("отписка от того, на что не подписан, не ломается",
       notify(off=True).get_json().get("ok") is True)

    # Товар успели завезти. Отписаться всё равно нужно уметь: иначе подписка
    # висит до самой рассылки, и человек получает сообщение о ненужном товаре.
    notify()
    db.change_stock(pid, 5)
    server._cache_bust()
    notify(off=True)
    c2("от завезённого товара тоже можно отписаться", db.alerts_of_user(9601) == [])

    # Чужую подписку снять нельзя — отписка идёт только за себя.
    c3 = Checker("Чужие подписки")
    db.change_stock(pid, -5)
    as_user(9602, "other")
    notify()
    as_user(9601, "buyer")
    notify(off=True)
    c3("сосед остался подписан", db.alerts_of_user(9602) == [pid])

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
