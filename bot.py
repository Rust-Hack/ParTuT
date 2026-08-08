"""
bot.py — тонкая оболочка вокруг Mini App.

Весь магазин (каталог, корзина, оплата, чек) теперь ВНУТРИ приложения (server.py + webapp).
Бот делает только три вещи:
  1. Открывает приложение (кнопка «🛍 Магазин»).
  2. Даёт владельцу админку товаров (/admin) — клиент её не видит.
  3. Присылает продавцам заказы и кнопки статусов (Подтвердить/Выдан/Отклонить).

Покупок в самом чате больше нет — только приложение.
"""

import json
import telebot
from telebot import types
from dotenv import load_dotenv

import db

# --- Подготовка ---
load_dotenv()

# Общие настройки и справочники берём из config.py (их же использует server.py).
from config import (
    BOT_TOKEN, WEBAPP_URL, CATEGORIES, CITIES,
    ADMIN_IDS, is_admin,
)

bot = telebot.TeleBot(BOT_TOKEN)

db.init_db()

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
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # Все админские кнопки (adm..., admord...) — только для админов/продавцов.
    if data.startswith("adm") and is_admin(user_id):
        handle_admin_callback(call, chat_id, user_id, data)
        return

    bot.answer_callback_query(call.id)


# ============================================================
#  ЗАКАЗЫ: действия продавца
# ============================================================

def handle_order_action(call, chat_id, action, order_id):
    """Продавец нажал кнопку статуса заказа (подтвердить / выдан / отклонить)."""
    order = db.get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "Заказ не найден", show_alert=True)
        return

    client_id = order["user_id"]

    if action == "confirm":
        db.set_order_status(order_id, "confirmed")
        bot.answer_callback_query(call.id, "Оплата подтверждена ✅")
        _safe_send(client_id,
                   f"✅ Оплата по заказу #{order_id} подтверждена!\n"
                   f"Ждём вас {order['pickup_time']}. Спасибо! 🌿")
    elif action == "issued":
        db.set_order_status(order_id, "issued")
        bot.answer_callback_query(call.id, "Отмечено: выдан 📦")
        _safe_send(client_id,
                   f"Заказ #{order_id} выдан. Спасибо, что выбрали нас! 🙌")
    elif action == "reject":
        db.set_order_status(order_id, "canceled")
        for it in json.loads(order["items"]):
            db.change_stock(it["id"], it["qty"])       # вернуть товар на склад
        bot.answer_callback_query(call.id, "Заказ отклонён")
        _safe_send(client_id,
                   f"К сожалению, заказ #{order_id} отклонён продавцом. "
                   "Если это ошибка — напишите нам, разберёмся.")
    else:
        bot.answer_callback_query(call.id)
        return

    status_ru = {"confirmed": "оплата подтверждена ✅",
                 "issued": "выдан 📦", "canceled": "отклонён ✖️"}
    try:
        bot.send_message(chat_id, f"Заказ #{order_id}: {status_ru.get(action, action)}")
    except Exception:
        pass


def _safe_send(user_id, text):
    """Отправка клиенту с защитой: если он заблокировал бота — не роняем работу."""
    try:
        bot.send_message(user_id, text)
    except Exception as e:
        print(f"Не смог написать клиенту {user_id}: {e}")


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
    for code, title in CITIES.items():
        kb.add(types.InlineKeyboardButton(title, callback_data=f"admcity:{code}"))
    kb.add(types.InlineKeyboardButton("✖️ Отмена", callback_data="adm:cancel"))
    return kb


def admin_cat_keyboard():
    kb = types.InlineKeyboardMarkup()
    for code, title in CATEGORIES.items():
        kb.add(types.InlineKeyboardButton(title, callback_data=f"admcat:{code}"))
    kb.add(types.InlineKeyboardButton("✖️ Отмена", callback_data="adm:cancel"))
    return kb


def handle_admin_callback(call, chat_id, user_id, data):
    """Разбирает нажатия админских кнопок (все, что начинаются с 'adm')."""
    parts = data.split(":")     # напр. "admset:price:5" -> ["admset","price","5"]

    # --- Действия продавца над заказом ---
    if data.startswith("admord:"):
        handle_order_action(call, chat_id, parts[1], int(parts[2]))
        return

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
        bot.answer_callback_query(call.id)
        admin_state[user_id] = {"action": "add", "step": "city", "draft": {}}
        bot.send_message(chat_id, "Новый товар. В каком городе он продаётся?",
                         reply_markup=admin_city_keyboard())
        return

    if data.startswith("admcity:"):
        st = admin_state.get(user_id)
        if not st or st.get("action") != "add":
            bot.answer_callback_query(call.id)
            return
        st["draft"]["city"] = parts[1]
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
    category = CATEGORIES.get(p["category"], p["category"])
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


# --- Запуск ---
def run():
    """Запускает бота (long polling). Зовётся при прямом запуске bot.py или из run.py."""
    print("Бот-оболочка запущен. (Ctrl+C — остановить)")
    bot.infinity_polling()


if __name__ == "__main__":
    run()
