"""
bot.py — тонкая оболочка вокруг Mini App.

Весь магазин (каталог, корзина, оплата, чек) теперь ВНУТРИ приложения (server.py + webapp).
Бот делает только:
  1. Открывает приложение (кнопка «🛍 Магазин»).
  2. Даёт владельцу админку товаров (/admin) — клиент её не видит.
  3. Уведомляет продавцов о новых заказах и НАПОМИНАЕТ каждые 10 мин, пока
     заказ не обработан. Управление заказами — в приложении (не в чате).
  4. Подтверждает заявки обычных админов супер-админу (кнопки Разрешить/Отклонить).

Покупок и управления заказами в самом чате нет — только приложение.
"""

import datetime
import html
import json
import os
import time
import threading
import telebot
from telebot import types
from dotenv import load_dotenv

import config
import db
import errors
import notifications

# --- Подготовка ---
load_dotenv()

# Общие настройки и справочники берём из config.py (их же использует server.py).
from config import (
    BOT_TOKEN, WEBAPP_URL, CITIES,
    ADMIN_IDS, SUPER_ADMIN_IDS, is_admin, is_super_admin, admin_city,
)


def _category_title(code):
    """Человеческое имя категории. Категорию могли переименовать или удалить —
    тогда показываем код, а не пустое место."""
    for c in db.list_categories():
        if c["code"] == code:
            return f"{c['emoji']} {c['name']}".strip()
    return code

bot = telebot.TeleBot(BOT_TOKEN)

db.init_db()
config.seed_admins_from_env()   # разовый перенос админов из окружения в базу

# Если задан адрес Mini App — ставим кнопку «🛍 Магазин» рядом с полем ввода.
if WEBAPP_URL:
    try:
        bot.set_chat_menu_button(
            menu_button=types.MenuButtonWebApp(
                type="web_app", text="🛍 Магазин",
                web_app=types.WebAppInfo(url=WEBAPP_URL),
            )
        )
    except Exception as e:
        print("Не смог установить кнопку меню Mini App:", e)


# Состояние админа во время добавления/редактирования товара.
admin_state = {}


# ============================================================
#  КНОПКА «ОТКРЫТЬ МАГАЗИН»
# ============================================================

def shop_keyboard():
    """Клавиатура с кнопкой запуска приложения (если задан адрес)."""
    if not WEBAPP_URL:
        return None
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🛍 Открыть магазин", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    return kb


def open_shop_prompt(chat_id, greeting=False):
    """Зовёт человека в приложение (одна дверь в магазин)."""
    if WEBAPP_URL:
        text = ("🌿 <b>Добро пожаловать!</b>\nНажмите кнопку ниже — откроется магазин 👇"
                if greeting else "Наш магазин открывается по кнопке ниже 👇")
        bot.send_message(chat_id, text, reply_markup=shop_keyboard(), parse_mode="HTML")
    else:
        bot.send_message(
            chat_id,
            "🌿 Магазин скоро откроется здесь. (Приложение ещё настраивается.)",
        )


# ============================================================
#  КОМАНДЫ
# ============================================================

@bot.message_handler(commands=["start"])
def on_start(message):
    # Человек написал боту — значит представился. Запоминаем имя сразу: до
    # первого заказа других источников имени у нас нет.
    try:
        db.ensure_user(message.from_user.id)
        db.remember_user_name(message.from_user.id,
                              message.from_user.username or "",
                              message.from_user.first_name or "")
    except Exception as e:
        print(f"Не смог запомнить имя пользователя: {e}")
    # Реферальная привязка по deep-link: t.me/<bot>?start=refN (надёжнее, чем startapp).
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload.startswith("ref"):
        try:
            ref_id = int(payload[3:])
            if db.set_referrer_once(message.from_user.id, ref_id):
                print(f"[ref] пользователь {message.from_user.id} приглашён {ref_id}")
        except (TypeError, ValueError):
            pass
    open_shop_prompt(message.chat.id, greeting=True)


@bot.message_handler(commands=["shop"])
def on_shop(message):
    open_shop_prompt(message.chat.id)


@bot.message_handler(commands=["myid"])
def on_myid(message):
    """Показывает Telegram-id — нужно, чтобы прописать админов/продавцов в .env."""
    bot.send_message(
        message.chat.id,
        f"Ваш Telegram-id: <code>{message.from_user.id}</code>\n\n"
        "Впишите это число в .env (ADMIN_IDS или продавец города) и перезапустите бота.",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["reply"])
def on_reply(message):
    """Ответ на вопрос в поддержку: /reply <user_id> текст.

    Только владельцам: вопросы из «Написать в поддержку» приходят им, а команда
    позволяет написать ЛЮБОМУ id — продавцу это не нужно, у него есть «✍️
    Написать» в заказе, и там проверяется его точка."""
    if not is_super_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        bot.send_message(message.chat.id, "Формат: /reply <id клиента> текст ответа")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "Неверный id клиента.")
        return
    # контакт ответившего админа — чтобы клиент мог сразу открыть чат в ТГ
    au = (message.from_user.username or "").strip()
    if au:
        contact = f'<a href="https://t.me/{au}">@{au}</a>'
    else:
        contact = f'<a href="tg://user?id={message.from_user.id}">написать менеджеру</a>'
    reply = (f"💬 Ответ от магазина:\n{html.escape(parts[2])}\n\n"
             f"По любым вопросам: {contact}")
    try:
        bot.send_message(target, reply, parse_mode="HTML")
        bot.send_message(message.chat.id, f"✅ Отправлено клиенту {target}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось отправить (клиент не запускал бота?): {e}")


@bot.message_handler(commands=["admin"])
def on_admin(message):
    """Админ-меню — только для тех, кто в списке админов."""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Эта команда только для админов 🙂")
        return
    admin_state.pop(message.from_user.id, None)
    show_admin_menu(message.chat.id)


@bot.message_handler(commands=["backup"])
def cmd_backup(message):
    """Копия по требованию — чтобы не ждать ночи перед рискованной правкой."""
    if not is_super_admin(message.from_user.id):
        return
    bot.reply_to(message, "Собираю копию…")
    err = _send_backup([message.chat.id], note="🗄 Резервная копия по запросу")
    if err:
        _safe_send(message.chat.id, f"Не получилось: {err}")


# ============================================================
#  ТЕКСТ И ФОТО (нужны только админу; остальных зовём в приложение)
# ============================================================

# ВНИМАНИЕ: этот обработчик ловит ЛЮБОЕ сообщение, а телебот перебирает их в
# порядке объявления. Значит все команды обязаны быть объявлены ВЫШЕ него —
# иначе команда до своего обработчика не доедет. Так и случилось с /backup:
# бот отвечал на неё «магазин открывается по кнопке ниже», и владелец думал,
# что копии не работают. Есть тест, который следит за этим порядком.
@bot.message_handler(func=lambda m: True)
def on_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    raw = (message.text or "").strip()

    # Админ вводит название/цену/остаток по шагам.
    if is_admin(user_id) and user_id in admin_state:
        if raw.lower() in ("отмена", "cancel", "стоп"):
            admin_state.pop(user_id, None)
            bot.send_message(chat_id, "Отменил.")
            show_admin_menu(chat_id)
        else:
            handle_admin_input(chat_id, user_id, raw)
        return

    # Обычный пользователь — зовём в приложение.
    open_shop_prompt(chat_id)


@bot.message_handler(content_types=["photo"])
def on_photo(message):
    """Фото товара грузятся в приложении: они принадлежат модели, а не точке."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    if not (is_admin(user_id) and user_id in admin_state):
        return
    bot.send_message(chat_id, "Фото загружаются в приложении: 📚 Ассортимент → модель → «Главное фото».")


# ============================================================
#  КНОПКИ (callback): админка + статусы заказов
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def on_button(call):
    data = call.data
    chat_id = call.message.chat.id if call.message else call.from_user.id   # message=None для старых (>48ч)
    user_id = call.from_user.id

    # Подтверждение заявок обычных админов — только супер-админ.
    if data.startswith("areq:"):
        handle_approval(call, user_id, data)
        return

    # Админские кнопки (adm... — управление товарами в чате) — только для админов.
    if data.startswith("adm") and is_admin(user_id):
        handle_admin_callback(call, chat_id, user_id, data)
        return

    bot.answer_callback_query(call.id)


def handle_approval(call, user_id, data):
    """Супер-админ нажал Разрешить/Отклонить на заявке обычного админа."""
    if not is_super_admin(user_id):
        bot.answer_callback_query(call.id, "Только для супер-админа", show_alert=True)
        return
    try:
        _, decision, rid = data.split(":")
        rid = int(rid)
    except (ValueError, TypeError):
        bot.answer_callback_query(call.id)
        return
    req = db.get_admin_request(rid)
    if not req:
        bot.answer_callback_query(call.id, "Заявка не найдена", show_alert=True)
        return
    if req["status"] != "pending":
        bot.answer_callback_query(call.id, "Уже обработано", show_alert=True)
        return

    if decision == "ok":
        if not db.set_admin_request_status_if(rid, "approved", ["pending"]):
            bot.answer_callback_query(call.id, "Уже обработано", show_alert=True)
            return
        try:
            db.execute_admin_request(req["action"], json.loads(req["payload"]))
        except Exception as e:
            print(f"Ошибка выполнения заявки #{rid}: {e}")
        bot.answer_callback_query(call.id, "Разрешено ✅")
        _safe_send(req["requester_id"], f"✅ Ваш запрос одобрен:\n{req['summary']}")
        head = f"✅ РАЗРЕШЕНО #{rid}\nОт: {req['requester_name']}\n{req['summary']}"
    else:
        if not db.set_admin_request_status_if(rid, "rejected", ["pending"]):
            bot.answer_callback_query(call.id, "Уже обработано", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Отклонено")
        _safe_send(req["requester_id"], f"✖️ Ваш запрос отклонён:\n{req['summary']}")
        head = f"✖️ ОТКЛОНЕНО #{rid}\nОт: {req['requester_name']}\n{req['summary']}"
    try:
        if call.message:
            bot.edit_message_text(head, call.message.chat.id, call.message.message_id)
    except Exception:
        pass


def _safe_send(user_id, text, parse_mode=None):
    """Отправка клиенту с защитой: если он заблокировал бота — не роняем работу.
    Возвращает True, если сообщение ушло."""
    try:
        bot.send_message(user_id, text, parse_mode=parse_mode)
        return True
    except Exception as e:
        print(f"Не смог написать клиенту {user_id}: {e}")
        return False


# ============================================================
#  АДМИНКА: управление товарами из чата
# ============================================================

def show_admin_menu(chat_id):
    """Чат — это подстраховка, а не вторая админка.

    Раньше отсюда можно было завести товар и переписать ему название и фото. Но
    название, фото и характеристики принадлежат модели в «Ассортименте»: правка
    из чата рвала связь, а следующее сохранение модели её же и затирало. Осталось
    то, что у товара действительно своё на каждой точке, — цена и остаток.
    """
    kb = types.InlineKeyboardMarkup()
    if WEBAPP_URL:
        kb.add(types.InlineKeyboardButton("🛠 Открыть управление",
                                          web_app=types.WebAppInfo(url=WEBAPP_URL + "#admin")))
    kb.add(types.InlineKeyboardButton("⚡ Быстро: цена и остаток", callback_data="adm:list"))
    bot.send_message(chat_id,
                     "🛠 <b>Админка</b>\nВесь магазин — в приложении. Здесь только быстрая "
                     "правка цены и остатка, если приложение под рукой не открыть.",
                     reply_markup=kb, parse_mode="HTML")


def handle_admin_callback(call, chat_id, user_id, data):
    """Разбирает нажатия админских кнопок (все, что начинаются с 'adm')."""
    parts = data.split(":")     # напр. "admset:price:5" -> ["admset","price","5"]

    if data == "adm:menu":
        bot.answer_callback_query(call.id)
        admin_state.pop(user_id, None)
        show_admin_menu(chat_id)
        return

    if data == "adm:cancel":
        bot.answer_callback_query(call.id, "Отменено")
        admin_state.pop(user_id, None)
        show_admin_menu(chat_id)
        return

    if data == "adm:add":
        # Кнопки такой больше нет, но старое сообщение в чате пережить нажатие должно.
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id,
                         "Товары заводятся в приложении:\n\n"
                         "1. 📚 Ассортимент — описать модель (бренд, характеристики, вкусы, фото)\n"
                         "2. 📥 у модели — завезти её на точку: цена и остаток\n\n"
                         "Так описание модели одно на все точки и правится в одном месте.")
        return

    if data == "adm:list":
        bot.answer_callback_query(call.id)
        show_admin_list(chat_id, user_id)
        return

    if data.startswith("admcard:"):
        bot.answer_callback_query(call.id)
        if not _my_product(user_id, int(parts[1])):
            bot.send_message(chat_id, "Это товар другой точки.")
            return
        show_product_card(chat_id, int(parts[1]))
        return

    if data.startswith("admset:"):
        field = parts[1]           # только price / stock: остальное принадлежит модели
        product_id = int(parts[2])
        if field not in ("price", "stock"):
            bot.answer_callback_query(call.id, "Это правится в приложении")
            return
        if not _my_product(user_id, product_id):
            bot.answer_callback_query(call.id, "Товар другой точки", show_alert=True)
            return
        admin_state[user_id] = {"action": "edit", "field": field, "product_id": product_id}
        prompts = {
            "price": "Введите новую цену в BYN (например 18.5):",
            "stock": "Введите новый остаток (сколько штук):",
        }
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, prompts.get(field, "Введите значение:"))
        return

    if data.startswith("admhit:"):
        product_id = int(parts[1])
        if not _my_product(user_id, product_id):
            bot.answer_callback_query(call.id, "Товар другой точки", show_alert=True)
            return
        new_value = db.toggle_hit(product_id)
        _log_bot(user_id, "product/update", f"id={product_id} · field=is_hit · value={new_value}")
        bot.answer_callback_query(call.id, "🔥 Теперь хит" if new_value else "Убрал из хитов")
        show_product_card(chat_id, product_id)
        return

    if data.startswith("admdel:"):
        product_id = int(parts[1])
        product = _my_product(user_id, product_id)
        bot.answer_callback_query(call.id)
        if not product:
            bot.send_message(chat_id, "Товар уже не найден.")
            return
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🗑 Да, удалить", callback_data=f"admdelyes:{product_id}"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data=f"admcard:{product_id}"))
        bot.send_message(chat_id, f"Точно удалить «{product['name']}»?", reply_markup=kb)
        return

    if data.startswith("admdelyes:"):
        product_id = int(parts[1])
        product = _my_product(user_id, product_id)
        if not product:
            bot.answer_callback_query(call.id, "Товар другой точки", show_alert=True)
            return
        db.delete_product(product_id)
        _log_bot(user_id, "product/delete", f"id={product_id} · name={product['name']}")
        name = product["name"]
        bot.answer_callback_query(call.id, "Удалено")
        bot.send_message(chat_id, f"🗑 «{name}» удалён.")
        show_admin_list(chat_id, user_id)
        return


def _my_product(user_id, product_id):
    """Товар, если он на точке этого продавца. Иначе None.

    В приложении границы точек проверяются на каждом действии, а чат про них
    не знал вовсе: продавец Турова правил и удалял товары Минска через «⚡».
    """
    p = db.get_product(product_id)
    if not p:
        return None
    scope = admin_city(user_id)
    return p if (not scope or p["city"] == scope) else None


def _log_bot(user_id, action, details):
    """Правка из чата — такое же действие, как из приложения, и в журнале
    обязана быть. Иначе достаточно открыть бота, чтобы менять цены без следа."""
    try:
        db.log_admin_action(user_id, f"id {user_id} (бот)", action, details)
    except Exception as e:
        print(f"Не записал действие бота в журнал: {e}")


def show_admin_list(chat_id, user_id):
    scope = admin_city(user_id)
    products = [p for p in db.get_all_products() if not scope or p["city"] == scope]
    if not products:
        bot.send_message(chat_id, "Товаров пока нет — заведите их в приложении: "
                                  "📚 Ассортимент, затем 📥 завоз на точку.")
        return

    # Сначала то, что кончается: список открывают, чтобы поправить именно такие.
    products = sorted(products, key=lambda p: (p["stock"] > 3, p["stock"]))
    kb = types.InlineKeyboardMarkup()
    for p in products[:40]:
        city = CITIES.get(p["city"], p["city"])
        mark = "🔴 " if p["stock"] <= 0 else ("🟠 " if p["stock"] <= 3 else "")
        label = f"{mark}{city} · {p['name']} — {p['price']:.2f} (ост. {p['stock']})"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"admcard:{p['id']}"))
    tail = "" if len(products) <= 40 else f"\nПоказаны первые 40 из {len(products)} — остальные в приложении."
    bot.send_message(chat_id, "⚡ Цена и остаток (нажмите товар):" + tail, reply_markup=kb)


def show_product_card(chat_id, product_id):
    p = db.get_product(product_id)
    if not p:
        bot.send_message(chat_id, "Товар не найден.")
        return

    city = CITIES.get(p["city"], p["city"])
    category = _category_title(p["category"])
    hit = "🔥 да" if p["is_hit"] == 1 else "нет"
    has_photo = "есть" if p["photo"] else "нет"
    text = (
        f"<b>{p['name']}</b>\n"
        f"Город: {city}\n"
        f"Категория: {category}\n"
        f"Цена: {p['price']:.2f} BYN\n"
        f"Остаток: {p['stock']} шт.\n"
        f"Хит: {hit}\n"
        f"Фото: {has_photo}\n"
        f"Описание: {p['description'] or '—'}\n\n"
        f"<i>Название, фото и характеристики — в приложении, в «Ассортименте»: "
        f"они общие для всех точек.</i>"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✏️ Цена", callback_data=f"admset:price:{product_id}"),
        types.InlineKeyboardButton("📦 Остаток", callback_data=f"admset:stock:{product_id}"),
    )
    kb.add(
        types.InlineKeyboardButton("🔥 Хит вкл/выкл", callback_data=f"admhit:{product_id}"),
        types.InlineKeyboardButton("🗑 Убрать с точки", callback_data=f"admdel:{product_id}"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ К списку", callback_data="adm:list"))

    if p["photo"]:
        bot.send_photo(chat_id, p["photo"], caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def handle_admin_input(chat_id, user_id, raw_text):
    """Обрабатывает ТЕКСТ, который админ вводит после кнопки «Цена» или «Остаток»."""
    st = admin_state.get(user_id)
    if not st:
        return

    # ---------- Изменение существующего товара ----------
    if st["action"] == "edit":
        field = st["field"]
        product_id = st["product_id"]

        if field == "price":
            value = parse_number(raw_text)
            if value is None or value <= 0:
                bot.send_message(chat_id, "Не понял цену. Напишите числом больше нуля, например 18.5")
                return
        elif field == "stock":
            value = parse_int(raw_text)
            if value is None or value < 0:
                bot.send_message(chat_id, "Не понял количество. Напишите целым числом.")
                return
        else:
            admin_state.pop(user_id, None)
            bot.send_message(chat_id, "Это правится в приложении, в «Ассортименте».")
            return

        # Проверяем ещё раз здесь: между нажатием кнопки и вводом числа товар
        # мог переехать на другую точку, а состояние диалога живёт в памяти.
        if not _my_product(user_id, product_id):
            admin_state.pop(user_id, None)
            bot.send_message(chat_id, "Это товар другой точки.")
            return
        db.update_field(product_id, field, value)
        _log_bot(user_id, "product/update", f"id={product_id} · field={field} · value={value}")
        admin_state.pop(user_id, None)
        bot.send_message(chat_id, "✅ Изменено.")
        show_product_card(chat_id, product_id)
        return


def parse_number(text):
    """Текст -> дробное число (запятую считаем точкой). None, если не вышло."""
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


def parse_int(text):
    """Текст -> целое число. None, если не вышло."""
    try:
        return int(text.strip())
    except ValueError:
        return None


CANCEL_UNPAID_HOURS = 24   # авто-отмена карточных заказов без чека спустя столько часов

# Сводка дня владельцу: во сколько (час) и часовой пояс (сервер Render — UTC, Минск = UTC+3).
SUMMARY_HOUR = int(os.environ.get("SUMMARY_HOUR", "21"))
SUMMARY_TZ_OFFSET = int(os.environ.get("SUMMARY_TZ_OFFSET", "3"))

# --- Ночные задачи: сводка, копия базы, напоминания ---
# Все три случаются раз в сутки, и отметку о выполнении надо держать В БАЗЕ, а
# не в памяти процесса. На Render сервис поднимается заново при каждом деплое и
# после простоя — с отметкой в памяти «раз в сутки» превращается в «после
# каждого запуска». Копию так уже слало владельцу по три раза подряд; у
# напоминаний покупателям цена выше — каждый перезапуск отправлял НОВУЮ порцию
# по суточному потолку, а за такую рассылку Telegram отбирает покупателей
# блокировкой навсегда.
_SUMMARY_MARK = "last_summary_date"
_BACKUP_MARK = "last_backup_date"
_REPEAT_MARK = "last_repeat_date"


def _local_now():
    """Минское время: сервер Render живёт по UTC."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=SUMMARY_TZ_OFFSET)


def _claim_daily(mark_key, hour):
    """Пора ли делать ночную задачу. Забирает право на сегодня: второй вызов за
    те же сутки вернёт False — в том числе после перезапуска сервиса.

    Право забирается ДО работы, а не после. Если Telegram в этот момент
    недоступен, задача пропустит день — и это лучше, чем попытка каждую минуту
    до утра."""
    local = _local_now()
    if local.hour < hour:
        return False
    # Отметка занимается одним действием: два экземпляра сервиса при деплое
    # живут бок о бок, и «прочитать, потом записать» пропустило бы обоих.
    return db.claim_setting(mark_key, local.date().isoformat())


def _daily_summary_text():
    """Короткая сводка по бизнесу за день для владельца."""
    s = db.get_business_stats(1)
    lines = [f"📅 <b>Итоги за день</b>",
             f"💰 Выручка (выдано): <b>{s['revenue']:.2f} Br</b>",
             f"✅ Выдано заказов: {s['orders']}",
             f"🧾 Средний чек: {s['avg_check']:.2f} Br",
             f"⏳ Ждут вас сейчас: {s['inwork_count']} на {s['inwork_total']:.2f} Br",
             f"🆕 Новых клиентов: {s['new_users']}"]
    if s.get("top"):
        top = "; ".join(f"{t['name']} ×{t['qty']}" for t in s["top"][:3])
        lines.append(f"🏆 Топ: {top}")
    return "\n".join(lines)


def _maybe_send_daily_summary():
    """Раз в сутки, после SUMMARY_HOUR по минскому времени, шлём сводку супер-админам."""
    if not _claim_daily(_SUMMARY_MARK, SUMMARY_HOUR):
        return
    try:
        text = _daily_summary_text()
    except Exception as e:
        print(f"Не смог собрать сводку дня: {e}")
        return
    for admin_id in SUPER_ADMIN_IDS:
        _safe_send(admin_id, text, parse_mode="HTML")


# --- Резервная копия базы ---
# Заказы, покупатели, балансы монет живут в единственном экземпляре в облачной
# базе. Копию храним там, где её точно не потеряют и за неё не надо платить —
# в личке владельца в Telegram. Раз в сутки, ночью, когда никто не покупает.
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "4"))


def _backup_bytes():
    """Сжатый JSON со всем содержимым базы + имя файла."""
    import gzip
    payload = json.dumps(db.export_tables(), ensure_ascii=False, default=str).encode("utf-8")
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    return gzip.compress(payload), f"partut-{stamp}.json.gz"


def _send_backup(chat_ids, note=""):
    """Отправляет копию. Возвращает текст ошибки или None, если всё ушло."""
    try:
        blob, name = _backup_bytes()
    except Exception as e:
        print(f"Не смог собрать резервную копию: {e}")
        return f"не смог собрать копию: {e}"
    size = f"{len(blob) / 1024:.0f} КБ"
    caption = (note or "🗄 Резервная копия базы") + f"\nРазмер: {size}. Храните — по ней можно восстановить магазин."
    sent = False
    for chat_id in chat_ids:
        try:
            bot.send_document(chat_id, blob, visible_file_name=name, caption=caption)
            sent = True
        except Exception as e:
            print(f"Не смог отправить копию в чат {chat_id}: {e}")
    return None if sent else "не удалось отправить файл"


def _maybe_send_backup():
    """Раз в сутки после BACKUP_HOUR по минскому времени."""
    if not _claim_daily(_BACKUP_MARK, BACKUP_HOUR):
        return
    err = _send_backup(SUPER_ADMIN_IDS)
    if err:
        # День уже занят, второй попытки сегодня не будет — и молчать об этом
        # нельзя. Магазин без копии живёт спокойно ровно до первой потери базы,
        # а узнать, что копий больше нет, было бы неоткуда. Владельцу — словами
        # и с выходом, разработчику — подробности.
        for admin_id in SUPER_ADMIN_IDS:
            _safe_send(admin_id, f"⚠️ Резервная копия за сегодня не ушла: {err}.\n"
                                 "Снимите её вручную командой /backup.")
        errors.report(bot, "резервная копия", RuntimeError(err))


# --- Напоминание покупателю: «пора пополнить» ---
# Расходники заканчиваются предсказуемо, и человек, купивший месяц назад, скорее
# всего уже докупил у кого-то другого. Напоминание — самый сильный денежный
# рычаг, но и самый опасный: назойливость Телеграм наказывает блокировками,
# а заблокировавшего покупателя вернуть нельзя ничем. Отсюда три ограничения:
# срок, суточный потолок и возможность отписаться.


def _repeat_settings():
    """Срок и потолок берём из настроек магазина — владелец меняет их сам,
    без правки кода: у одноразок и жидкостей разный срок жизни."""
    try:
        days = int(db.get_setting("remind_after_days", 21))
    except (TypeError, ValueError):
        days = 21
    try:
        cap = int(db.get_setting("remind_daily_cap", 20))
    except (TypeError, ValueError):
        cap = 20
    return max(1, days), max(0, cap)


REPEAT_TEXT = (
    "Давно не виделись 👋\n"
    "Если запасы подходят к концу — повторить прошлый заказ можно в два нажатия: "
    "откройте магазин, «Профиль» → «Мои заказы» → «Повторить заказ».\n\n"
    "Не хотите таких напоминаний — «Профиль» → выключите «Напоминать о заказе»."
)


def _maybe_send_repeat_reminders():
    """Раз в сутки, в тот же тихий час, что и резервная копия.

    «Раз в сутки» тут — не про аккуратность, а про потолок: он и есть вся
    защита от веерной рассылки, и каждый лишний прогон за день пробивает его
    на целую порцию."""
    days, cap = _repeat_settings()
    if cap == 0:                       # 0 в настройках = напоминания выключены
        return
    if not _claim_daily(_REPEAT_MARK, BACKUP_HOUR):
        return
    sent = 0
    for row in db.customers_to_remind(days, cap):
        uid = int(row["user_id"])
        # Помечаем ДО отправки: если Telegram ответит ошибкой (человек заблокировал
        # бота), повторная попытка завтра ему всё равно не дойдёт — а пометка
        # убережёт от бесконечных попыток каждый день.
        db.mark_reminded(uid)
        if _safe_send(uid, REPEAT_TEXT):
            sent += 1
    if sent:
        print(f"Напоминаний о повторной покупке отправлено: {sent}")


def _expire_unpaid_orders():
    """Отменить брошенные заказы картой и сказать об этом обеим сторонам.

    Отдельной функцией, а не строчками внутри вечного цикла: цикл не вызвать из
    теста, и любая правка здесь проверялась бы только на живом магазине.
    Возвращает номера отменённых заказов."""
    done = []
    for order in db.stale_new_orders(CANCEL_UNPAID_HOURS):
        o = db.cancel_order(order["id"], ["new"])   # вернёт склад/монеты
        if not o:
            continue                                # успели оплатить или отменить
        done.append(o["id"])
        _safe_send(o["user_id"], f"⏳ Заказ #{o['id']} отменён — чек не был загружен. "
                                 "Товар вернулся в наличие, монеты возвращены. Оформите заново, если нужно.")
        # Продавец узнаёт о заказе картой сразу, ещё до чека, — значит обязан
        # узнать и о том, что заказ отменился сам. Иначе у него в чате навсегда
        # остаётся «новый заказ», которого больше нет, и он держит под него
        # товар или звонит покупателю зря.
        for admin_id in config.admins_for_city(o["city"]):
            _safe_send(admin_id, f"⏳ Заказ #{o['id']} отменён автоматически — "
                                 f"чек не загружен за {CANCEL_UNPAID_HOURS} ч. "
                                 "Товар вернулся на склад.")
    return done


_CLEANUP_MARK = "last_cleanup_date"


def _nightly_cleanup():
    """Раз в сутки убираем то, что копится само: картинки снятых с точки товаров
    и старые движения монет. База бесплатная, место на ней кончается тихо."""
    if not _claim_daily(_CLEANUP_MARK, BACKUP_HOUR):
        return
    photos = db.purge_orphan_photos()
    coins = db.trim_coin_log()
    if photos or coins:
        print(f"Ночная уборка: картинок {photos}, движений монет {coins}")


def _remind_sellers():
    """Продавцу — про заказы, которые ждут его решения. Раз в 10 минут на заказ."""
    for order in db.orders_needing_reminder(10):
        notifications.remind_sellers(bot, order)


# Всё, что магазин делает сам, без нажатия кнопки. Каждый шаг выполняется
# ОТДЕЛЬНО и в своей защите: раньше они стояли под одним try, и поломка в
# первом же шаге отменяла все следующие — включая ночную копию базы. Тихо, без
# единой строчки о том, что копий больше нет.
_BACKGROUND_STEPS = [
    ("напоминания продавцам", _remind_sellers),
    ("авто-отмена неоплаченных", _expire_unpaid_orders),
    ("сводка дня", _maybe_send_daily_summary),
    ("резервная копия", _maybe_send_backup),
    ("напоминания покупателям", _maybe_send_repeat_reminders),
    ("ночная уборка", _nightly_cleanup),
]


def _background_tick():
    """Один оборот фоновых дел. Вынесен из вечного цикла, чтобы его можно было
    вызвать из теста: цикл со sleep не проверить никак."""
    for name, step in _BACKGROUND_STEPS:
        try:
            step()
        except Exception as e:
            # Про поломку надо узнать от бота, а не случайно: логи Render
            # никто не читает. Одинаковые ошибки не спамят — errors копит их.
            errors.report(bot, f"фоновые дела: {name}", e)


def _reminder_loop():
    """Раз в минуту прокручивает фоновые дела магазина."""
    while True:
        _background_tick()
        time.sleep(60)


# --- Запуск ---
def run():
    """Запускает бота (long polling). Зовётся при прямом запуске bot.py или из run.py."""
    print("Бот-оболочка запущен. (Ctrl+C — остановить)")
    threading.Thread(target=_reminder_loop, daemon=True).start()   # повтор-напоминания продавцам
    bot.infinity_polling()


if __name__ == "__main__":
    run()
