"""Как зовут покупателя.

В списке людей все, кто ещё ничего не купил, выглядели голым числом: имя
бралось ТОЛЬКО из заказов, а само по себе нигде не сохранялось. Владелец
открывал раздел и видел два десятка «ID 8448498059» — ни написать, ни узнать,
кто это, ни понять, живой ли это человек.

Telegram присылает имя при каждом открытии приложения. Осталось его запомнить.
"""
from _common import db, client, server, Checker, as_user, as_admin

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM users WHERE user_id >= 9700 AND user_id < 9800")
    conn.commit(); conn.close()
    cache.bust()


def _list():
    return {u["id"]: u for u in client.post("/api/admin/users", json={"initData": "x"}).get_json()["users"]}


def run():
    c = Checker("Имя покупателя")
    _clean()
    # Фоновые задачи в тесте выполняем сразу: иначе проверка гонится с потоком.
    orig_bg = server._bg
    server._bg = lambda fn, *a, **k: fn(*a, **k)
    try:
        # --- Человек просто открыл приложение, ничего не купив ---
        as_user(9701, username="vasya", first_name="Вася")
        client.post("/api/me", json={"initData": "x"})
        as_admin()
        u = _list().get(9701)
        c("новый человек попал в список", u is not None)
        c("и он с именем, а не голым id", u["username"] == "vasya")
        c("имя из профиля тоже сохранено", u["first_name"] == "Вася")
        c("заказов у него нет — имя всё равно есть", u["orders"] == 0)

        # --- У человека нет @имени в Telegram ---
        as_user(9702, username=None, first_name="Пётр")
        client.post("/api/me", json={"initData": "x"})
        as_admin()
        u = _list().get(9702)
        c("без @имени показываем имя из профиля", u["first_name"] == "Пётр")

        # --- Имя в Telegram сменилось ---
        as_user(9701, username="vasya_new", first_name="Вася")
        client.post("/api/me", json={"initData": "x"})
        as_admin()
        c("новое имя перезаписывает старое", _list()[9701]["username"] == "vasya_new")

        # Пустое имя не должно стирать то, что уже знаем.
        db.remember_user_name(9701, "", "")
        c("пустое имя ничего не затирает", _list()[9701]["username"] == "vasya_new")

        # --- Поиск ---
        c2 = Checker("Поиск по имени")
        found = client.post("/api/admin/users", json={"initData": "x", "search": "vasya_new"}).get_json()["users"]
        c2("ищется по @имени", any(u["id"] == 9701 for u in found))
        found = client.post("/api/admin/users", json={"initData": "x", "search": "Пётр"}).get_json()["users"]
        c2("и по имени из профиля", any(u["id"] == 9702 for u in found))
        found = client.post("/api/admin/users", json={"initData": "x", "search": "9702"}).get_json()["users"]
        c2("по id тоже, как и раньше", any(u["id"] == 9702 for u in found))

        # --- Кто покупал раньше: имя достаём из его заказов ---
        c3 = Checker("Старые покупатели")
        db.ensure_user(9703)
        db.create_order(9703, "oldbuyer", "Минск",
                        [{"id": 1, "name": "X", "price": 10.0, "qty": 1}], 10.0, "")
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE users SET username = NULL WHERE user_id = %s"), (9703,))
        conn.commit(); conn.close()
        db._ensure_user_columns()          # та же миграция, что при обновлении
        c3("имя подтянулось из старых заказов", _list()[9703]["username"] == "oldbuyer")

        # --- Карточка покупателя ---
        card = client.post("/api/admin/customer", json={"initData": "x", "user_id": 9701}).get_json()["card"]
        c3("в карточке покупателя тоже имя", card["username"] == "vasya_new")
    finally:
        server._bg = orig_bg
        as_admin()
        _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
