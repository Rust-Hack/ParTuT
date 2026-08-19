"""
notifications.py — отправка заказа продавцам города.

Одна и та же функция используется и ботом (когда заказ оформлен в чате),
и веб-сервером (когда заказ пришёл из Mini App). Так сообщение продавцу
и кнопки статусов выглядят одинаково, откуда бы заказ ни пришёл.

Функция принимает `bot` (экземпляр telebot) — тот, через который слать.
"""

import random
import json
from telebot import types

from partut import db
from partut.config import CITIES, WEBAPP_URL, admins_for_city


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
    # Заказ картой приходит продавцу СРАЗУ, ещё до чека: раньше он ждал чека и,
    # если клиент выбирал «оплачу позже», не приходил вовсе — заказ висел в базе,
    # и о нём никто не знал. Лучше показать «ждём чек», чем промолчать.
    if pm == "card" and not order["receipt_file_id"]:
        lines.append("⏳ <i>Чек ещё не загружен — придёт следующим сообщением.</i>")
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


def notify_receipt(bot, order_id):
    """Чек по уже отправленному заказу — отдельным сообщением следом.

    Сам заказ продавец получил в момент оформления, поэтому повторять его
    целиком незачем: нужен только чек и номер, к которому он относится."""
    order = db.get_order(order_id)
    if not order or not order["receipt_file_id"]:
        return
    text = (f"🧾 <b>Чек по заказу #{order['id']}</b>\n"
            f"🏙 {CITIES.get(order['city'], order['city'])} · {order['total']:.2f} BYN")

    kb = _orders_kb()
    for admin_id in admins_for_city(order["city"]):
        try:
            bot.send_photo(admin_id, order["receipt_file_id"], caption=text,
                           parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"Не смог отправить чек по заказу #{order_id} админу {admin_id}: {e}")


def notify_compensation(bot, user_id, coins, order_id=None, reason=""):
    """Покупателю — что ему начислили монеты и за что.

    Молча менять человеку баланс нельзя: компенсация имеет смысл только если о
    ней узнали. Зовётся из обоих мест, где заявка может выполниться — из
    приложения и из чата."""
    about_order = f" по заказу #{order_id}" if order_id else ""
    why = f"\nПричина: {reason}" if reason else ""
    text = (f"🎁 Вам начислено {int(coins)} 🪙 в качестве компенсации{about_order}.{why}\n"
            "Монетами можно оплатить часть следующего заказа.")
    try:
        bot.send_message(user_id, text)
    except Exception as e:
        print(f"Не смог сообщить о компенсации клиенту {user_id}: {e}")


def draw_raffle(bot, raffle):
    """Подводит итоги розыгрыша: победители, призы, поздравления.

    Живёт здесь, а не в сервере, потому что зовут отсюда двое: приложение (когда
    кто-то открыл вкладку после срока) и ночные дела бота (когда не открыл
    никто). Розыгрыш, который не подвели, — это призы, которых люди не увидели.

    Возвращает True, если итоги подвели именно мы: право забирается у базы, в
    час пик «первых» бывает несколько."""
    if not db.claim_raffle_draw(raffle["id"]):
        return False
    # Один человек — одно место. Ключ в базе не даст ему второй билет, но в
    # розыгрышах, начатых до него, дубли могли остаться.
    uids = list(dict.fromkeys(db.get_raffle_user_ids(raffle["id"])))
    random.shuffle(uids)
    places = [(1, raffle["prize1"] or "Приз за 1 место", 0),
             (2, raffle["prize2"] or "Приз за 2 место", 0),
             (3, f"{raffle['prize3_coins']} монет", raffle["prize3_coins"])]
    winners = []
    for i, (place, prize, coins) in enumerate(places):
        if i >= len(uids):
            break
        wid = uids[i]
        winners.append({"place": place, "user_id": wid, "prize": prize})
        if coins:
            db.add_coins(wid, coins, "raffle")
        try:
            bot.send_message(wid, f"🏆 Вы заняли {place} место в розыгрыше! Приз: {prize}. "
                                  + ("Монеты начислены." if coins else "Продавец свяжется с вами."))
        except Exception as e:
            print(f"Не смог поздравить победителя {wid}: {e}")
    db.set_raffle_winners(raffle["id"], winners)
    return True


def close_expired_raffle(bot):
    """Подводит итоги, если срок вышел. Нового розыгрыша НЕ заводит: он идёт
    только тогда, когда владелец его начал."""
    r = db.get_active_raffle()
    if r and r["ends_at"] and db._now_str() >= r["ends_at"]:
        return draw_raffle(bot, r)
    return False


def run_admin_request(bot, action, payload):
    """Выполнить одобренное действие и сказать о нём тому, кого оно касается.

    Заявку могут одобрить из двух мест — из приложения и кнопкой в чате. Если
    писать покупателю в каждом из них по отдельности, однажды напишут только в
    одном, и человек получит монеты молча."""
    result = db.execute_admin_request(action, payload)
    if action == "compensate" and result.get("granted"):
        notify_compensation(bot, result["user_id"], result["granted"],
                            payload.get("order_id"), payload.get("reason", ""))
    return result


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
