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
    lines.append(f"<b>Итого: {order['total']:.2f} BYN</b>")
    lines.append(f"⏰ Забрать: {order['pickup_time']}")

    uname = order["username"] or ""
    if uname and not uname.isdigit():
        lines.append(f"👤 Клиент: @{uname}")
    else:
        lines.append(f"👤 Клиент id: <code>{order['user_id']}</code>")
    text = "\n".join(lines)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"admord:confirm:{order_id}"))
    kb.add(
        types.InlineKeyboardButton("📦 Выдан", callback_data=f"admord:issued:{order_id}"),
        types.InlineKeyboardButton("✖️ Отклонить", callback_data=f"admord:reject:{order_id}"),
    )

    for admin_id in admins_for_city(order["city"]):
        try:
            if order["receipt_file_id"]:
                bot.send_photo(admin_id, order["receipt_file_id"], caption=text,
                               reply_markup=kb, parse_mode="HTML")
            else:
                bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            print(f"Не смог отправить заказ #{order_id} админу {admin_id}: {e}")
