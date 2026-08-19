"""Сверка суммы: связь между заказом и деньгами на счёте.

К заказу и так прикрепляется фото чека — продавец открывает картинку и видит
сумму. Для ОДНОГО заказа этого достаточно. Не достаточно для бухгалтерии:
сумма лежит внутри JPEG, и система о ней не знает ничего. Нельзя взять выписку
банка и сопоставить её с заказами, нельзя сравнить «продано за месяц» с
«поступило за месяц», а проверка «сошлось ли» держится на внимании продавца в
каждом заказе подряд.

Поэтому одно число записывается как число — в момент подтверждения заказа.
Спрашиваем ПРОДАВЦА, а не покупателя: у продавца в этот момент открыт банк, а
покупатель уже заплатил и хочет закончить (лишнее поле на экране оплаты стоит
брошенных заказов), да и написать он может что угодно.

Чего это не делает: не ловит подделку. Платёж доказывает банк, а не мы.
"""
from _common import db, client, Checker, as_user, as_admin

BUYER = 9601


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()


def _оплаченный(total=43.0):
    """Заказ картой, чек приложен — то есть готовый к подтверждению."""
    oid = db.create_order(BUYER, "buyer", "Минск",
                          [{"product_id": 1, "name": "Под", "price": total, "qty": 1}], total, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET payment_method = 'card' WHERE id = %s"), (oid,))
    conn.commit(); conn.close()
    db.set_order_receipt(oid, "file_чек")
    return oid


def _подтвердить(oid, **поля):
    return client.post("/api/admin/order/status",
                       json=dict({"initData": "x", "id": oid, "action": "confirm"}, **поля))


def _карточка(oid):
    заказы = client.post("/api/admin/orders", json={"initData": "x"}).get_json()["orders"]
    return next(z for z in заказы if z["id"] == oid)


def run():
    _clean()
    as_admin()

    c = Checker("Продавец записывает, сколько пришло")
    oid = _оплаченный(43.0)
    r = _подтвердить(oid, paid_amount="43.00")
    c("заказ подтверждён", (r.get_json() or {}).get("ok") is True)
    c("сумма записана", abs(float(db.get_order(oid)["paid_amount"]) - 43.0) < 0.001)
    к = _карточка(oid)
    c("в карточке видно сумму", abs(к["paid_amount"] - 43.0) < 0.001)
    c("и что всё сошлось", к["payment_matches"] is True)

    c2 = Checker("Расхождение видно до выдачи")
    oid2 = _оплаченный(50.0)
    _подтвердить(oid2, paid_amount="40")
    к2 = _карточка(oid2)
    c2("расхождение помечено", к2["payment_matches"] is False)
    c2("записано столько, сколько сказал продавец", abs(к2["paid_amount"] - 40.0) < 0.001)
    c2("но заказ подтверждён — решает продавец", db.get_order(oid2)["status"] == "confirmed")

    oid3 = _оплаченный(10.0)
    _подтвердить(oid3, paid_amount="10.001")
    c2("копейка погрешности расхождением не считается",
       _карточка(oid3)["payment_matches"] is True)

    c3 = Checker("«Не сверял» остаётся пустым, а не нулём")
    # Подставить сюда итог заказа значило бы нарисовать проверку, которой не
    # было: отчёт «всё сходится» стал бы враньём по умолчанию.
    oid4 = _оплаченный(20.0)
    r = _подтвердить(oid4)                      # поля нет вовсе
    c3("заказ всё равно подтверждается", (r.get_json() or {}).get("ok") is True)
    c3("сумма осталась пустой", db.get_order(oid4)["paid_amount"] is None)
    c3("и это не ноль", db.get_order(oid4)["paid_amount"] != 0)
    c3("карточка не утверждает, что сошлось", _карточка(oid4)["payment_matches"] is None)

    oid5 = _оплаченный(20.0)
    _подтвердить(oid5, paid_amount="мусор")
    c3("мусор не записывается и не роняет подтверждение",
       db.get_order(oid5)["paid_amount"] is None
       and db.get_order(oid5)["status"] == "confirmed")

    c4 = Checker("Покупателя ни о чём не спрашивают")
    # Экран оплаты остался прежним: человек уже заплатил, и лишнее поле там
    # стоит брошенных заказов.
    import io as _io
    as_user(BUYER, "buyer")
    oid6 = db.create_order(BUYER, "buyer", "Минск",
                           [{"product_id": 1, "name": "Под", "price": 15.0, "qty": 1}], 15.0, "")
    r = client.post("/api/receipt",
                    data={"initData": "x", "order_id": str(oid6),
                          "file": (_io.BytesIO(b"\x89PNG\r\n\x1a\nphoto"), "check.jpg")},
                    content_type="multipart/form-data")
    c4("чек принимается без всяких полей", (r.get_json() or {}).get("ok") is True)
    c4("заказ стал оплаченным", db.get_order(oid6)["status"] == "paid")
    c4("сумма пока не сверялась", db.get_order(oid6)["paid_amount"] is None)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
