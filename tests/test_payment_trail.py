"""След платежа: чем заказ связан с поступлением денег на счёт.

Магазин принимает перевод на карту и фото чека. Сверить это с банковской
выпиской было нечем — заказ и строку поступления связывала только память
продавца. Отсюда две беды, обе тихие: деньги пришли не за тот заказ, и деньги
не пришли вовсе, а товар выдан.

Спрашиваем ровно два поля: сумму (она подставлена итогом, покупателю остаётся
подтвердить) и последние четыре цифры карты. По этой паре строка выписки
находит свой заказ.

Чего эта проверка НЕ делает и не должна: она не ловит подделку. Написать
правильную сумму может кто угодно — доказывает платёж банк, а не мы. Здесь
ловится небрежность и остаётся след для разбора.
"""
import io

from _common import db, client, server, Checker, as_user, as_admin, reset_sent

import notifications

BUYER = 9601


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()


def _заказ(total=43.0):
    return db.create_order(BUYER, "buyer", "Минск",
                           [{"product_id": 1, "name": "Под", "price": total, "qty": 1}], total, "")


def _чек(oid, **поля):
    """Загрузка чека — форма с файлом, как из приложения."""
    данные = {"initData": "x", "order_id": str(oid),
              "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nphoto"), "check.jpg")}
    данные.update({к: str(з) for к, з in поля.items()})
    return client.post("/api/receipt", data=данные, content_type="multipart/form-data")


def run():
    _clean()
    as_user(BUYER, "buyer")

    c = Checker("След платежа записывается")
    oid = _заказ(43.0)
    r = _чек(oid, amount="43.00", last4="1234")
    c("чек принят", (r.get_json() or {}).get("ok") is True)

    o = db.get_order(oid)
    c("сумма перевода сохранена", abs(float(o["paid_amount"]) - 43.0) < 0.001)
    c("последние цифры карты сохранены", o["payer_last4"] == "1234")
    c("заказ перешёл в «оплачен»", o["status"] == "paid")

    as_admin()
    карточка = client.post("/api/admin/orders", json={"initData": "x"}).get_json()["orders"]
    мой = next(z for z in карточка if z["id"] == oid)
    c("продавец видит сумму", abs(мой["paid_amount"] - 43.0) < 0.001)
    c("продавец видит карту", мой["payer_last4"] == "1234")
    c("и что всё сошлось", мой["payment_matches"] is True)

    # --- Несовпадение суммы ---
    c2 = Checker("Несовпадение суммы видно продавцу")
    as_user(BUYER, "buyer")
    oid2 = _заказ(50.0)
    _чек(oid2, amount="40", last4="9999")
    as_admin()
    карточка = client.post("/api/admin/orders", json={"initData": "x"}).get_json()["orders"]
    мой2 = next(z for z in карточка if z["id"] == oid2)
    c2("расхождение помечено", мой2["payment_matches"] is False)
    c2("но заказ НЕ отклонён — решает продавец", db.get_order(oid2)["status"] == "paid")
    c2("сказанная сумма сохранена как есть", abs(мой2["paid_amount"] - 40.0) < 0.001)

    # Копейка расхождения — не расхождение: суммы дробные.
    as_user(BUYER, "buyer")
    oid3 = _заказ(10.0)
    _чек(oid3, amount="10.001", last4="1111")
    as_admin()
    карточка = client.post("/api/admin/orders", json={"initData": "x"}).get_json()["orders"]
    c2("копейка погрешности расхождением не считается",
       next(z for z in карточка if z["id"] == oid3)["payment_matches"] is True)

    # --- Мусор в полях не должен ронять приём чека ---
    c3 = Checker("Кривой ввод не теряет чек")
    as_user(BUYER, "buyer")
    oid4 = _заказ(20.0)
    r = _чек(oid4, amount="не число", last4="abcd")
    c3("чек всё равно принят", (r.get_json() or {}).get("ok") is True)
    o4 = db.get_order(oid4)
    c3("сумма записана как «не сказал»", o4["paid_amount"] is None)
    c3("а не как ноль", o4["paid_amount"] != 0)

    as_user(BUYER, "buyer")
    oid5 = _заказ(20.0)
    _чек(oid5, amount="20", last4="87654321")
    c3("из длинного номера взяты последние четыре",
       db.get_order(oid5)["payer_last4"] == "4321")

    # --- Продавцу в чат ---
    c4 = Checker("Продавец видит след в сообщении")
    as_user(BUYER, "buyer")
    oid6 = _заказ(33.0)
    _чек(oid6, amount="30", last4="5555")
    reset_sent()
    отправлено = []
    настоящий = server.tg.send_photo
    server.tg.send_photo = lambda cid, ph, **kw: отправлено.append(kw.get("caption", "")) or type(
        "M", (), {"photo": [type("P", (), {"file_id": "x"})()]})()
    try:
        notifications.notify_receipt(server.tg, oid6)
    finally:
        server.tg.send_photo = настоящий
    текст = " ".join(отправлено)
    c4("сообщение ушло", bool(отправлено))
    c4("в нём есть сумма перевода", "30.00" in текст)
    c4("и карта", "5555" in текст)
    c4("и предупреждение о расхождении", "Не сходится" in текст)

    _clean()
    as_admin()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
