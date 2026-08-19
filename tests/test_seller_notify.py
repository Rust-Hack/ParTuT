"""Уведомление продавцу о новом заказе.

Это сообщение — начало каждой продажи. Раньше оно заканчивалось строчкой
«Обработайте в приложении: Управление → Заказы», и продавец шёл туда руками:
открыть приложение, профиль, управление, заказы. Четыре тапа на каждый заказ,
и это при том, что заказ уже оплачен и человек ждёт.

Теперь под сообщением кнопка, которая открывает приложение сразу на заказах.
Проверяем и её, и то, что в тексте осталось всё, без чего продавец не поедет
на точку: город, состав, доставка, контакт клиента.
"""
import importlib

from _common import db, Checker

from partut import config
from partut import notifications

# Общий стенд глушит отправку продавцам, чтобы не шуметь при каждом заказе.
# Здесь проверяется как раз она — возвращаем настоящую функцию на время теста.
_stub = notifications.notify_sellers


class FakeBot:
    """Ловит отправленное вместе с клавиатурой — её общий стенд не сохраняет."""

    def __init__(self):
        self.sent = []

    def send_message(self, cid, text, **kw):
        self.sent.append((cid, text, kw.get("reply_markup")))

    def send_photo(self, cid, file_id, **kw):
        self.sent.append((cid, kw.get("caption", ""), kw.get("reply_markup")))


def _buttons(markup):
    """Плоский список кнопок клавиатуры: [(текст, url веб-аппа)]."""
    if markup is None:
        return []
    out = []
    for row in markup.keyboard:
        for b in row:
            web = getattr(b, "web_app", None)
            out.append((b.text, getattr(web, "url", None)))
    return out


def _order(city="Минск"):
    oid = db.create_order(7701, "buyer", city,
                          [{"product_id": 1, "name": "Elf Bar", "price": 15.0, "qty": 2}],
                          32.0, "")
    db.set_order_delivery(oid, "Доставка по метро", "Пушкинская", 2.0, "cash",
                          comment="позвонить заранее", phone="+375291112233")
    return oid


def run():
    c = Checker("Уведомление продавцу")
    db.add_staff(8801, "Минск", "продавец Минска")
    config.refresh_staff()          # права кэшируются на полминуты — иначе продавца «нет»
    importlib.reload(notifications)
    notifications.WEBAPP_URL = "https://shop.example"

    try:
        bot = FakeBot()
        oid = _order()
        notifications.notify_sellers(bot, oid)

        c("продавцу города ушло сообщение", any(s[0] == 8801 for s in bot.sent))
        text = bot.sent[0][1]
        c("виден номер заказа", f"#{oid}" in text)
        c("виден город", "Минск" in text)
        c("виден состав", "Elf Bar" in text and "× 2" in text)
        c("видна доставка с адресом", "Пушкинская" in text)
        c("виден способ оплаты", "наличными" in text)
        c("виден телефон", "+375291112233" in text)
        c("виден комментарий", "позвонить заранее" in text)

        btns = _buttons(bot.sent[0][2])
        c("кнопка «открыть заказы» есть", len(btns) == 1)
        c("и она ведёт в приложение", btns and btns[0][1] == "https://shop.example#orders")
        c("а не оставляет продавца искать раздел руками",
          "Управление → Заказы" not in text)

        # --- Повторное напоминание ---
        c2 = Checker("Напоминание о необработанном заказе")
        bot2 = FakeBot()
        notifications.remind_sellers(bot2, db.get_order(oid))
        c2("напоминание ушло", any(s[0] == 8801 for s in bot2.sent))
        c2("в нём тоже кнопка", len(_buttons(bot2.sent[0][2])) == 1)
        c2("и сказано, чего ждём", "ждёт вашего подтверждения" in bot2.sent[0][1])

        # --- Магазин без адреса приложения ---
        # На голой установке WEBAPP_URL пустой: кнопку строить не из чего, но
        # уведомление обязано дойти — иначе заказ просто потеряется.
        c3 = Checker("Без адреса приложения")
        notifications.WEBAPP_URL = ""
        bot3 = FakeBot()
        notifications.notify_sellers(bot3, oid)
        c3("сообщение всё равно ушло", any(s[0] == 8801 for s in bot3.sent))
        c3("просто без кнопки", _buttons(bot3.sent[0][2]) == [])
    finally:
        notifications.notify_sellers = _stub      # дальше по стенду снова тихо
        db.remove_staff(8801)
        config.refresh_staff()
        conn = db.connect(); cur = conn.cursor()
        cur.execute("DELETE FROM orders")
        conn.commit(); conn.close()

    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
