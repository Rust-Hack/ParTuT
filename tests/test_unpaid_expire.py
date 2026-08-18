"""Брошенный заказ картой: отмена через сутки — и продавец об этом узнаёт.

Заказ картой списывает товар со склада сразу, ещё до чека. Если чек так и не
приложили, через CANCEL_UNPAID_HOURS заказ отменяется сам: товар возвращается,
монеты тоже, клиенту уходит объяснение.

Но продавцу заказ теперь приходит в момент оформления, а не вместе с чеком.
Значит, у него в чате висит «🆕 Новый заказ», который через сутки перестал
существовать, — и об этом ему обязаны сказать. Иначе он держит под этот заказ
товар или звонит покупателю по отменённому заказу.
"""
import datetime

from _common import db, client, server, Checker, as_user, as_admin
import config


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    server._cache_bust()


def _make_old(oid, hours):
    """Состарить заказ: цикл смотрит на время создания."""
    old = (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET created_at = %s WHERE id = %s"), (old, oid))
    conn.commit(); conn.close()


def run():
    c = Checker("Брошенный заказ картой")
    _clean()
    import bot as botmod

    sent = []
    orig_safe = botmod._safe_send
    botmod._safe_send = lambda uid, text, parse_mode=None: sent.append((uid, text))
    db.add_staff(6698, "Минск", "продавец Минска")
    config.refresh_staff()
    try:
        db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
        mid = db.get_delivery_methods("Минск")[0]["id"]
        pid = db.add_product("Минск", "disposable", "Брошенный", 25.0, 4)
        db.set_age_ok(6611)
        db.add_coins(6611, 300)
        as_user(6611, "buyer")
        server._cache_bust()

        r = client.post("/api/order", json={"initData": "x", "city": "Минск",
                                            "delivery_method_id": mid,
                                            "payment_method": "card", "use_coins": True,
                                            "items": [{"id": pid, "qty": 2}]})
        d = r.get_json()
        oid = d["order_id"]
        c("заказ создан и ждёт чек", db.get_order(oid)["status"] == "new")
        c("товар списан сразу, ещё до чека", db.get_product(pid)["stock"] == 2)
        coins_after_order = db.get_coins(6611)

        # Свежий заказ трогать нельзя: человек ещё может приложить чек.
        c("свежий заказ не отменяется", not db.stale_new_orders(24))

        _make_old(oid, 25)
        stale = db.stale_new_orders(24)
        c("через сутки заказ считается брошенным", [o["id"] for o in stale] == [oid])

        # Зовём РОВНО то, что вызывает фоновый цикл бота, а не повторяем его
        # логику в тесте: иначе тест переживёт удаление самой отмены.
        sent.clear()
        done = botmod._expire_unpaid_orders()
        c("заказ отменён фоновой задачей", done == [oid])
        c("товар вернулся на склад", db.get_product(pid)["stock"] == 4)
        c("монеты вернулись", db.get_coins(6611) > coins_after_order)

        # --- Кого уведомили ---
        c2 = Checker("Кому сказали об отмене")
        c2("покупателю объяснили, что случилось",
           any(s[0] == 6611 and "чек не был загружен" in s[1] for s in sent))
        c2("продавец города узнал об отмене", any(s[0] == 6698 for s in sent))
        c2("и понятно, почему", any("чек не загружен" in s[1] for s in sent))
        c2("виден номер заказа", any(f"#{oid}" in s[1] for s in sent))

        # Отменённый заказ не должен попасть под отмену второй раз: иначе склад
        # вернётся дважды и в наличии появится товар, которого нет.
        sent.clear()
        c2("второй раз тот же заказ не отменяется", botmod._expire_unpaid_orders() == [])
        c2("и лишних сообщений нет", sent == [])
        c2("склад не раздулся", db.get_product(pid)["stock"] == 4)
    finally:
        botmod._safe_send = orig_safe
        db.remove_staff(6698)
        config.refresh_staff()
        as_admin()
        _clean()

    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
