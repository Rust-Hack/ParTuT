"""
notifications.py — отправка заказа продавцам города.

Одна и та же функция используется и ботом (когда заказ оформлен в чате),
и веб-сервером (когда заказ пришёл из Mini App). Так сообщение продавцу
и кнопки статусов выглядят одинаково, откуда бы заказ ни пришёл.

Функция принимает `bot` (экземпляр telebot) — тот, через который слать.
"""

import json
from telebot import types

import db
from config import CITIES, admins_for_city


def notify_sellers(bot, order_id):
    """Шлёт заказ (с чеком, если есть) продавцам нужного города, с кнопками статусов."""
    order = db.get_order(order_id)
    if not order:
        return
    items = json.loads(order["items"])

    lines = [f"🆕 <b>Новый заказ #{order['id']}</b>",
             f"🏙 Город: {CITIES.get(order['city'], order['city'])}", ""]
    for it in items:
        lines.append(f"• {it['name']} × {it['qty']} = {it['price'] * it['qty']:.2f} BYN")
    lines.append("")
    # Способ получения + адрес + оплата
    method = order["delivery_method"] or ""
    if method:
        addr = order["delivery_address"] or ""
        fee = order["delivery_fee"] or 0
        lines.append(f"🚚 {method}" + (f": {addr}" if addr else "") + (f" (+{fee:.2f} BYN)" if fee else ""))
    pm = order["payment_method"] or ""
    pm_ru = {"card": "💳 картой (чек)", "cash": "💵 наличными", "none": "🚕 при получении"}.get(pm, pm)
    if pm_ru:
        lines.append(f"Оплата: {pm_ru}")
    lines.append(f"<b>Итого: {order['total']:.2f} BYN</b>")

    uname = order["username"] or ""
    if uname and not uname.isdigit():
        lines.append(f"👤 Клиент: @{uname}")
    else:
        lines.append(f"👤 Клиент id: <code>{order['user_id']}</code>")
    phone = (order["phone"] or "").strip() if "phone" in order.keys() else ""
    if phone:
        lines.append(f"📞 Телефон: {phone}")
    comment = (order["comment"] or "").strip() if "comment" in order.keys() else ""
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    lines.append("")
    lines.append("🔧 Обработайте в приложении: Управление → Заказы")
    text = "\n".join(lines)

    for admin_id in admins_for_city(order["city"]):
        try:
            if order["receipt_file_id"]:
                bot.send_photo(admin_id, order["receipt_file_id"], caption=text, parse_mode="HTML")
            else:
                bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            print(f"Не смог отправить заказ #{order_id} админу {admin_id}: {e}")
    db.touch_order_reminded(order_id)     # запускаем отсчёт до напоминания


def remind_sellers(bot, order):
    """Короткое повторное напоминание продавцу, что заказ ещё не обработан."""
    method = order["delivery_method"] or ""
    st = "ждёт вашего подтверждения"
    text = (f"⏰ <b>Напоминание: заказ #{order['id']} {st}</b>\n"
            f"🏙 {CITIES.get(order['city'], order['city'])} · {order['total']:.2f} BYN"
            + (f" · {method}" if method else "")
            + "\n\n🔧 Обработайте в приложении: Управление → Заказы")
    for admin_id in admins_for_city(order["city"]):
        try:
            bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            print(f"Не смог напомнить о заказе #{order['id']} админу {admin_id}: {e}")
    db.touch_order_reminded(order["id"])
