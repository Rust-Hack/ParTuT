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
from config import CITIES, WEBAPP_URL, admins_for_city


def _orders_kb():
    """Кнопка «открыть заказы» прямо под уведомлением.

    Без неё продавец читал в сообщении «Управление → Заказы» и шёл туда руками:
    открыть приложение, профиль, управление, заказы — четыре тапа на каждый заказ.
    Хэш читает приложение при запуске и сразу открывает нужный экран.
    """
    if not WEBAPP_URL:
        return None
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📦 Открыть заказы",
                                      web_app=types.WebAppInfo(url=WEBAPP_URL + "#orders")))
    return kb


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
    uid = order["user_id"]
    if uname and not uname.isdigit():
        # @username кликабелен; ссылка t.me открывает чат сразу
        lines.append(f'👤 Клиент: <a href="https://t.me/{uname}">@{uname}</a> (id <code>{uid}</code>)')
    else:
        lines.append(f'👤 Клиент: <a href="tg://user?id={uid}">открыть чат</a> (id <code>{uid}</code>)')
    phone = (order["phone"] or "").strip() if "phone" in order.keys() else ""
    if phone:
        lines.append(f"📞 Телефон: {phone}")
    comment = (order["comment"] or "").strip() if "comment" in order.keys() else ""
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    text = "\n".join(lines)
    kb = _orders_kb()

    for admin_id in admins_for_city(order["city"]):
        try:
            if order["receipt_file_id"]:
                bot.send_photo(admin_id, order["receipt_file_id"], caption=text,
                               parse_mode="HTML", reply_markup=kb)
            else:
                bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"Не смог отправить заказ #{order_id} админу {admin_id}: {e}")
    db.touch_order_reminded(order_id)     # запускаем отсчёт до напоминания


def remind_sellers(bot, order):
    """Короткое повторное напоминание продавцу, что заказ ещё не обработан."""
    method = order["delivery_method"] or ""
    st = "ждёт вашего подтверждения"
    text = (f"⏰ <b>Напоминание: заказ #{order['id']} {st}</b>\n"
            f"🏙 {CITIES.get(order['city'], order['city'])} · {order['total']:.2f} BYN"
            + (f" · {method}" if method else ""))
    kb = _orders_kb()
    for admin_id in admins_for_city(order["city"]):
        try:
            bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"Не смог напомнить о заказе #{order['id']} админу {admin_id}: {e}")
    db.touch_order_reminded(order["id"])
