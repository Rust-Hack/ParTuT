"""Заказ картой доходит до продавца ДО чека.

Самая дорогая поломка из всех: уведомление продавцу уходило только вместе с
чеком — сервер молчал, пока клиент не приложит фото. А в приложении прямо под
реквизитами есть кнопка «Оплачу позже (чек — в „Мои заказы“)». Нажав её или
просто закрыв приложение, человек оставлял заказ, о котором не узнавал никто:
он лежал в базе со статусом «новый», в чате продавца было пусто, и звонить
покупателю никто не собирался.

Теперь заказ уходит продавцу в момент оформления, с пометкой «чек ещё не
загружен», а чек приходит следом отдельным сообщением.
"""
import importlib

from _common import db, client, server, Checker, as_user, as_admin

import cache
import config
import notifications
import server_orders


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Заказ картой без чека")
    _clean()

    # Ловим уведомления продавцам: общий стенд их глушит (это ЗАГЛУШКА, не
    # настоящая функция), а проверяем как раз их. Настоящие достаём перезагрузкой
    # модуля — так же, как в тесте про текст уведомления.
    stub = notifications.notify_sellers
    sent_orders, sent_receipts = [], []
    notifications.notify_sellers = lambda bot, oid: sent_orders.append(oid)
    notifications.notify_receipt = lambda bot, oid: sent_receipts.append(oid)
    orig_bg = server._bg
    server._bg = lambda fn, *a, **k: fn(*a, **k)     # фон выполняем сразу

    try:
        db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
        mid = db.get_delivery_methods("Минск")[0]["id"]
        pid = db.add_product("Минск", "disposable", "Карточный", 30.0, 5)
        db.set_age_ok(6601)
        as_user(6601, "buyer")
        cache.bust()

        r = client.post("/api/order", json={"initData": "x", "city": "Минск",
                                            "delivery_method_id": mid,
                                            "payment_method": "card",
                                            "items": [{"id": pid, "qty": 1}]})
        d = r.get_json()
        c("заказ картой оформлен", d.get("ok") is True)
        c("приложение просит чек", d.get("needs_receipt") is True)
        oid = d.get("order_id")
        c("продавец узнал о заказе СРАЗУ, не дожидаясь чека", sent_orders == [oid])
        c("чек отдельным сообщением пока не слали", sent_receipts == [])

        # --- Что именно видит продавец ---
        c2 = Checker("Текст уведомления")
        importlib.reload(notifications)              # вернуть НАСТОЯЩИЕ функции
        db.add_staff(6699, "Минск", "продавец Минска")
        config.refresh_staff()

        class FakeBot:
            def __init__(self): self.sent = []
            def send_message(self, cid, text, **kw): self.sent.append((cid, text))
            def send_photo(self, cid, fid, **kw): self.sent.append((cid, kw.get("caption", "")))

        fb = FakeBot()
        notifications.notify_sellers(fb, oid)
        text = fb.sent[0][1] if fb.sent else ""
        c2("уведомление дошло до продавца города", any(s[0] == 6699 for s in fb.sent))
        c2("в нём честно сказано, что чека ещё нет", "Чек ещё не загружен" in text)
        c2("и виден состав заказа", "Карточный" in text)

        # --- Клиенту тоже напоминаем про чек ---
        c3 = Checker("Напоминание клиенту")
        summary = server_orders._client_order_summary(oid)
        c3("клиент получил подтверждение заказа", f"#{oid}" in summary)
        c3("и напоминание приложить чек", "Ждём фото чека" in summary)

        # --- Чек приложили: приходит следом, заказ целиком не повторяется ---
        c4 = Checker("Чек пришёл следом")
        fb2 = FakeBot()
        db.set_order_receipt(oid, "fid_test")
        notifications.notify_receipt(fb2, oid)
        cap = fb2.sent[0][1] if fb2.sent else ""
        c4("чек ушёл продавцу", any(s[0] == 6699 for s in fb2.sent))
        c4("подписан номером заказа", f"#{oid}" in cap)
        c4("а весь заказ заново не пересылается", "Карточный" not in cap)

        # Теперь, когда чек есть, пометки «ждём чек» в заказе быть не должно.
        fb3 = FakeBot()
        notifications.notify_sellers(fb3, oid)
        c4("после чека пометка «ждём» пропала",
           "Чек ещё не загружен" not in (fb3.sent[0][1] if fb3.sent else ""))
        c4("и клиенту про чек больше не пишем",
           "Ждём фото чека" not in server_orders._client_order_summary(oid))

        db.remove_staff(6699)
        config.refresh_staff()
    finally:
        notifications.notify_sellers = stub      # дальше по стенду снова тихо
        server._bg = orig_bg
        as_admin()
        _clean()

    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
