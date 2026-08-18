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
    ADMIN_IDS, SUPER_ADMIN_IDS, is_admin, is_super_admin,
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
    """Ответ клиенту на его вопрос: /reply <user_id> текст (только для админов)."""
    if not is_admin(message.from_user.id):
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


# ============================================================
#  ТЕКСТ И ФОТО (нужны только админу; остальных зовём в приложение)
# ============================================================

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
    """Фото нужны только админу — при добавлении товара или замене фото."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    if not (is_admin(user_id) and user_id in admin_state):
        return
    file_id = message.photo[-1].file_id      # самый большой размер картинки
    st = admin_state[user_id]

    if st.get("action") == "add" and st.get("step") == "photo":
        finalize_add(chat_id, user_id, photo_file_id=file_id)
    elif st.get("action") == "editphoto":
        product_id = st["product_id"]
        db.update_field(product_id, "photo", file_id)
        admin_state.pop(user_id, None)
        bot.send_message(chat_id, "✅ Фото обновлено.")
        show_product_card(chat_id, product_id)
    else:
        bot.send_message(chat_id, "Сейчас фото не жду. Откройте /admin.")


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
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data="adm:add"))
    kb.add(types.InlineKeyboardButton("📋 Товары / редактировать", callback_data="adm:list"))
    bot.send_message(chat_id, "🛠 <b>Админка</b>\nЧто делаем с товарами?",
                     reply_markup=kb, parse_mode="HTML")


def admin_city_keyboard():
    kb = types.InlineKeyboardMarkup()
    for loc in db.get_locations():
        kb.add(types.InlineKeyboardButton(loc["name"], callback_data=f"admcity:{loc['id']}"))
    kb.add(types.InlineKeyboardButton("✖️ Отмена", callback_data="adm:cancel"))
    return kb


def admin_cat_keyboard():
    kb = types.InlineKeyboardMarkup()
    # Категории живут в базе — владелец заводит их сам, и кнопки должны это видеть.
    for c in db.list_categories():
        title = f"{c['emoji']} {c['name']}".strip()
        kb.add(types.InlineKeyboardButton(title, callback_data=f"admcat:{c['code']}"))
    kb.add(types.InlineKeyboardButton("✖️ Отмена", callback_data="adm:cancel"))
    return kb


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
        # Товар заводится в приложении: сначала модель в «Ассортименте», потом
        # завоз на точку. Второй путь через бота создавал товар в обход модели —
        # такой не обновлялся вместе с ней и жил своей жизнью.
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id,
                         "Товары теперь заводятся в приложении:\n\n"
                         "1. 📚 Ассортимент — описать модель (бренд, характеристики, вкусы, фото)\n"
                         "2. 📥 у модели — завезти её на точку: цена и остаток\n\n"
                         "Так описание модели одно на все точки и правится в одном месте.")
        return

    if data.startswith("admcity:"):
        st = admin_state.get(user_id)
        if not st or st.get("action") != "add":
            bot.answer_callback_query(call.id)
            return
        loc = db.get_location(int(parts[1]))
        st["draft"]["city"] = loc["name"] if loc else parts[1]
        st["step"] = "category"
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Категория товара?", reply_markup=admin_cat_keyboard())
        return

    if data.startswith("admcat:"):
        st = admin_state.get(user_id)
        if not st or st.get("action") != "add":
            bot.answer_callback_query(call.id)
            return
        st["draft"]["category"] = parts[1]
        st["step"] = "name"
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Напишите <b>название</b> товара:", parse_mode="HTML")
        return

    if data == "adm:list":
        bot.answer_callback_query(call.id)
        show_admin_list(chat_id)
        return

    if data.startswith("admcard:"):
        bot.answer_callback_query(call.id)
        show_product_card(chat_id, int(parts[1]))
        return

    if data.startswith("admset:"):
        field = parts[1]           # price / stock / name
        product_id = int(parts[2])
        admin_state[user_id] = {"action": "edit", "field": field, "product_id": product_id}
        prompts = {
            "price": "Введите новую цену в BYN (например 18.5):",
            "stock": "Введите новый остаток (сколько штук):",
            "name":  "Введите новое название:",
        }
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, prompts.get(field, "Введите значение:"))
        return

    if data.startswith("admphoto:"):
        product_id = int(parts[1])
        admin_state[user_id] = {"action": "editphoto", "product_id": product_id}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Пришлите новое фото 📷 (или напишите «-», чтобы убрать текущее):")
        return

    if data.startswith("admhit:"):
        product_id = int(parts[1])
        new_value = db.toggle_hit(product_id)
        bot.answer_callback_query(call.id, "🔥 Теперь хит" if new_value else "Убрал из хитов")
        show_product_card(chat_id, product_id)
        return

    if data.startswith("admdel:"):
        product_id = int(parts[1])
        product = db.get_product(product_id)
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
        product = db.get_product(product_id)
        db.delete_product(product_id)
        name = product["name"] if product else "товар"
        bot.answer_callback_query(call.id, "Удалено")
        bot.send_message(chat_id, f"🗑 «{name}» удалён.")
        show_admin_list(chat_id)
        return


def show_admin_list(chat_id):
    products = db.get_all_products()
    if not products:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data="adm:add"))
        bot.send_message(chat_id, "Товаров пока нет.", reply_markup=kb)
        return

    kb = types.InlineKeyboardMarkup()
    for p in products:
        city = CITIES.get(p["city"], p["city"])
        label = f"{city} · {p['name']} — {p['price']:.2f} (ост. {p['stock']})"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"admcard:{p['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data="adm:add"))
    bot.send_message(chat_id, "📋 Товары (нажмите, чтобы изменить):", reply_markup=kb)


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
        f"Описание: {p['description'] or '—'}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✏️ Цена", callback_data=f"admset:price:{product_id}"),
        types.InlineKeyboardButton("📦 Остаток", callback_data=f"admset:stock:{product_id}"),
    )
    kb.add(
        types.InlineKeyboardButton("✏️ Название", callback_data=f"admset:name:{product_id}"),
        types.InlineKeyboardButton("🔥 Хит вкл/выкл", callback_data=f"admhit:{product_id}"),
    )
    kb.add(
        types.InlineKeyboardButton("🖼 Фото", callback_data=f"admphoto:{product_id}"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"admdel:{product_id}"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ К списку", callback_data="adm:list"))

    if p["photo"]:
        bot.send_photo(chat_id, p["photo"], caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def finalize_add(chat_id, user_id, photo_file_id=None):
    """Сохраняет собранный товар в базу. Вызывается после шага фото (с фото или без)."""
    st = admin_state.get(user_id)
    if not st or st.get("action") != "add":
        return
    d = st["draft"]
    new_id = db.add_product(
        d["city"], d["category"], d["name"], d["price"], d["stock"],
        is_hit=0, description=d.get("description", ""),
    )
    if photo_file_id:
        db.update_field(new_id, "photo", photo_file_id)
    admin_state.pop(user_id, None)
    bot.send_message(chat_id, "✅ Товар добавлен!")
    show_product_card(chat_id, new_id)


def handle_admin_input(chat_id, user_id, raw_text):
    """Обрабатывает ТЕКСТ, который админ вводит по шагам (название, цена, остаток...)."""
    st = admin_state.get(user_id)
    if not st:
        return

    # ---------- Добавление нового товара ----------
    if st["action"] == "add":
        step = st["step"]
        draft = st["draft"]

        if step in ("city", "category"):
            bot.send_message(chat_id, "Пожалуйста, выберите вариант кнопкой выше 👆")
            return

        if step == "name":
            draft["name"] = raw_text
            st["step"] = "price"
            bot.send_message(chat_id, "Цена в BYN? Например 18.5")
            return

        if step == "price":
            price = parse_number(raw_text)
            if price is None or price < 0:
                bot.send_message(chat_id, "Не понял цену. Напишите числом, например 18.5")
                return
            draft["price"] = price
            st["step"] = "stock"
            bot.send_message(chat_id, "Сколько штук в наличии?")
            return

        if step == "stock":
            stock = parse_int(raw_text)
            if stock is None or stock < 0:
                bot.send_message(chat_id, "Не понял количество. Напишите целым числом, например 10")
                return
            draft["stock"] = stock
            st["step"] = "description"
            bot.send_message(chat_id, "Короткое описание (или напишите «-», чтобы пропустить):")
            return

        if step == "description":
            draft["description"] = "" if raw_text.strip() == "-" else raw_text
            st["step"] = "photo"
            bot.send_message(chat_id, "Пришлите фото товара 📷 (или напишите «-», чтобы пропустить):")
            return

        if step == "photo":
            if raw_text.strip() == "-":
                finalize_add(chat_id, user_id)
            else:
                bot.send_message(chat_id, "Пришлите именно фото 📷 или «-», чтобы пропустить.")
            return

    # ---------- Замена фото у существующего товара ----------
    if st["action"] == "editphoto":
        if raw_text.strip() == "-":
            product_id = st["product_id"]
            db.update_field(product_id, "photo", None)
            admin_state.pop(user_id, None)
            bot.send_message(chat_id, "✅ Фото убрано.")
            show_product_card(chat_id, product_id)
        else:
            bot.send_message(chat_id, "Пришлите фото 📷 или «-», чтобы убрать фото.")
        return

    # ---------- Изменение существующего товара ----------
    if st["action"] == "edit":
        field = st["field"]
        product_id = st["product_id"]

        if field == "price":
            value = parse_number(raw_text)
            if value is None or value < 0:
                bot.send_message(chat_id, "Не понял цену. Напишите числом, например 18.5")
                return
        elif field == "stock":
            value = parse_int(raw_text)
            if value is None or value < 0:
                bot.send_message(chat_id, "Не понял количество. Напишите целым числом.")
                return
        else:  # name
            value = raw_text

        db.update_field(product_id, field, value)
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
_last_summary_date = None   # дата последней отправленной сводки (в памяти процесса)


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
    global _last_summary_date
    local = datetime.datetime.utcnow() + datetime.timedelta(hours=SUMMARY_TZ_OFFSET)
    if local.hour < SUMMARY_HOUR or _last_summary_date == local.date():
        return
    _last_summary_date = local.date()
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
_last_backup_date = None


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
    global _last_backup_date
    local = datetime.datetime.utcnow() + datetime.timedelta(hours=SUMMARY_TZ_OFFSET)
    if local.hour < BACKUP_HOUR or _last_backup_date == local.date():
        return
    _last_backup_date = local.date()      # ставим ДО отправки: неудача не должна
    _send_backup(SUPER_ADMIN_IDS)         # заставить бота слать копию каждую минуту


@bot.message_handler(commands=["backup"])
def cmd_backup(message):
    """Копия по требованию — чтобы не ждать ночи перед рискованной правкой."""
    if not is_super_admin(message.from_user.id):
        return
    bot.reply_to(message, "Собираю копию…")
    err = _send_backup([message.chat.id], note="🗄 Резервная копия по запросу")
    if err:
        _safe_send(message.chat.id, f"Не получилось: {err}")


# --- Напоминание покупателю: «пора пополнить» ---
# Расходники заканчиваются предсказуемо, и человек, купивший месяц назад, скорее
# всего уже докупил у кого-то другого. Напоминание — самый сильный денежный
# рычаг, но и самый опасный: назойливость Телеграм наказывает блокировками,
# а заблокировавшего покупателя вернуть нельзя ничем. Отсюда три ограничения:
# срок, суточный потолок и возможность отписаться.
_last_repeat_date = None


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
    """Раз в сутки, в тот же тихий час, что и резервная копия."""
    global _last_repeat_date
    local = datetime.datetime.utcnow() + datetime.timedelta(hours=SUMMARY_TZ_OFFSET)
    if local.hour < BACKUP_HOUR or _last_repeat_date == local.date():
        return
    _last_repeat_date = local.date()

    days, cap = _repeat_settings()
    if cap == 0:                       # 0 в настройках = напоминания выключены
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


def _reminder_loop():
    """Раз в минуту: напоминает продавцам о заказах, ждущих одобрения (раз в 10 мин на заказ),
    и авто-отменяет брошенные карточные заказы без чека (спустя CANCEL_UNPAID_HOURS)."""
    while True:
        try:
            for order in db.orders_needing_reminder(10):
                notifications.remind_sellers(bot, order)
            for order in db.stale_new_orders(CANCEL_UNPAID_HOURS):
                o = db.cancel_order(order["id"], ["new"])   # вернёт склад/монеты
                if o:
                    _safe_send(o["user_id"], f"⏳ Заказ #{o['id']} отменён — чек не был загружен. "
                                             "Товар вернулся в наличие, монеты возвращены. Оформите заново, если нужно.")
            _maybe_send_daily_summary()
            _maybe_send_backup()
            _maybe_send_repeat_reminders()
        except Exception as e:
            # Этот цикл шлёт напоминания, отменяет брошенные заказы и делает
            # резервную копию. Если он сломается тихо, не работать будет всё
            # сразу — и узнать об этом было бы неоткуда.
            errors.report(bot, "фоновый цикл заказов", e)
        time.sleep(60)


# --- Запуск ---
def run():
    """Запускает бота (long polling). Зовётся при прямом запуске bot.py или из run.py."""
    print("Бот-оболочка запущен. (Ctrl+C — остановить)")
    threading.Thread(target=_reminder_loop, daemon=True).start()   # повтор-напоминания продавцам
    bot.infinity_polling()


if __name__ == "__main__":
    run()
