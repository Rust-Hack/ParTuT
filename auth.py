"""
auth.py — кто это и что ему можно.

Листовой модуль: из своего проекта тянет только config и db, а про сервер не
знает ничего. Раньше всё это жило в server.py, и каждый модуль с ручками
импортировал server ради одной функции get_admin — из-за чего граф импортов
был кругом, а не деревом.

Четыре уровня прав, снизу вверх: покупатель → продавец города → владелец
(SUPER_ADMIN_IDS) → разработчик (DEV_IDS). Проверку «этот путь только для
владельца» делает общий страж по спискам ниже, а не каждая ручка сама: список
видно целиком, и новая ручка не может забыть про него молча.

ВАЖНО про тесты. Стенд подменяет get_admin и get_user, чтобы играть за разных
людей. Значит звать их надо ЧЕРЕЗ модуль (auth.get_admin(...)), а не копией
имени при импорте: копия подмены не заметит, и проверки прав пройдут вхолостую.
На этом уже спотыкались.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

import telebot
from flask import g, jsonify, request

import db
import notifications
import tgsend
from config import (BOT_TOKEN, DEV_MODE, DEV_USER_ID, SUPER_ADMIN_IDS, admin_city,
                    admin_role, is_admin, is_super_admin)


INIT_DATA_MAX_AGE = 24 * 3600      # сутки: дольше одной сессии приложения не живут


def validate_init_data(init_data):
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    # Подпись верна вечно, поэтому одна утёкшая строка входа работала бы всегда:
    # попала в чужой лог или на скриншот — и это постоянный ключ от аккаунта.
    # Телеграм выдаёт её при каждом открытии приложения, так что суток хватает.
    try:
        issued = int(pairs.get("auth_date", 0))
    except (TypeError, ValueError):
        return None
    if issued and time.time() - issued > INIT_DATA_MAX_AGE:
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def get_user(init_data):
    """Возвращает пользователя из initData (или подставного в DEV_MODE)."""
    user = validate_init_data(init_data)
    if not user and DEV_MODE:
        return {"id": DEV_USER_ID, "username": "dev"}
    return user


def get_admin(init_data):
    """Возвращает пользователя, ТОЛЬКО если он админ. Иначе None (доступ запрещён).

    Кладёт его же в g.admin: журнал действий и проверка города берут админа
    оттуда, чтобы не разбирать initData повторно на каждом запросе."""
    user = get_user(init_data)
    if not user or not user.get("id") or not is_admin(int(user["id"])):
        return None
    uid = int(user["id"])
    user = dict(user, city=admin_city(uid), role=admin_role(uid))
    g.admin = user
    return user


def _admin_display(admin):
    return admin.get("username") or admin.get("first_name") or str(admin.get("id"))


# Что меняет магазин целиком: витрину всех точек, деньги, права, настройки.
# Продавцу там делать нечего — даже тому, кто ведёт все точки сразу. Проверка
# централизованная: раскидать её по шестидесяти маршрутам значит забыть в одном.
_OWNER_ONLY = (
    # каталог — общий для всех точек
    "/api/admin/model", "/api/admin/brand", "/api/admin/category",
    "/api/admin/photo",                        # фото товара и галерея модели
    # деньги покупателей
    "/api/admin/grant", "/api/admin/coins/", "/api/admin/wheel/",
    # Весь розыгрыш целиком — правилом, а не перечислением: новая ручка
    # («начать розыгрыш») однажды уже не попала бы в список.
    "/api/admin/promo", "/api/admin/raffle/",
    # люди
    "/api/admin/users", "/api/admin/user/delete", "/api/admin/referral",
    "/api/admin/customer",          # история покупок и телефон — по всем точкам
    "/api/admin/staff/", "/api/admin/log",
    # заявки на подтверждение: решает их владелец. Маршруты и сами это проверяют,
    # но пусть правило будет и здесь — в одном месте видно всё, что не продавцу.
    "/api/admin/requests", "/api/admin/request/",
    # устройство магазина
    "/api/admin/location", "/api/admin/delivery", "/api/admin/point",
    "/api/admin/settings/update", "/api/admin/stats",
    # Отзыв виден на всех точках, поэтому публиковать и удалять — владельцу.
    # Ответить продавец может: это его разговор с покупателем.
    "/api/admin/review/decide", "/api/admin/review/delete",
)
# Ровно этот путь, без вложенных: /api/admin/product заводит товар мимо
# ассортимента (владельцу), а /api/admin/product/update — это цена на точке
# (продавцу), и по префиксу их не различить.
_OWNER_ONLY_EXACT = {
    "/api/admin/product",
    "/api/admin/promos",      # коды со статистикой: сколько выручки принёс каждый
    "/api/admin/raffle",      # настройка розыгрыша
    "/api/admin/settings",    # реквизиты и правила магазина
    "/api/admin/staff",       # кто ещё работает и с какими правами
}
# Единственное общее чтение, оставленное продавцу: ассортимент. Без него он не
# завезёт модель на свою точку. Всё остальное про магазин целиком — у владельца.
_OWNER_ONLY_READS = {"/api/admin/models"}
# Техническое: про программу, а не про магазин.
_DEV_ONLY = ("/api/admin/stats/reset",)


def guard_owner_only():
    """Общий страж: ставится приложением как before_request (см. server.py).

    Здесь он объявлен обычной функцией, а не декоратором: auth не знает про
    приложение и не должен — иначе снова получился бы круг.
    """
    path = request.path
    if not path.startswith("/api/admin/") or path in _OWNER_ONLY_READS:
        return None
    dev_only = path in _DEV_ONLY
    if not dev_only and path not in _OWNER_ONLY_EXACT \
            and not any(path.startswith(p) for p in _OWNER_ONLY):
        return None
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData") or request.form.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if dev_only and admin.get("role") != "dev":
        return jsonify({"ok": False, "error": "dev_only",
                        "message": "Техническое действие — только у разработчика."}), 403
    if admin.get("role") not in ("dev", "owner"):
        return jsonify({"ok": False, "error": "owner_only",
                        "message": "Это меняет магазин целиком — только у владельца."}), 403
    return None


def may_city(admin, city):
    """Продавец точки работает со своей точкой. Пустой город — продавец всех
    точек (и владелец, у которого точки нет по определению)."""
    scope = (admin or {}).get("city") or ""
    return not scope or scope == city


def _foreign():
    return jsonify({"ok": False, "error": "other_city",
                    "message": "Это товар другой точки."}), 403


def deny_city(admin, city):
    """Готовый ответ 403, если точка чужая, иначе None — чтобы в маршруте была
    одна строчка, а не четыре одинаковых на каждый эндпоинт."""
    return None if may_city(admin, city) else _foreign()


def deny_product(admin, pid):
    """То же, но город берётся у самого товара."""
    if not (admin or {}).get("city"):
        return None                      # полный доступ — читать товар незачем
    p = db.get_product(pid)
    return None if (p and may_city(admin, p["city"])) else _foreign()


# --- Заявки на подтверждение ---
# Продавец не может раздавать монеты сам, но и запрещать ему всё — значит
# оставить покупателя без компенсации до вечера. Поэтому щель: он оформляет
# заявку, владелец подтверждает одним нажатием.

def _notify_supers_request(rid, admin, summary):
    """Шлёт супер-админам запрос на подтверждение с кнопками Разрешить/Отклонить."""
    text = (f"🔐 Запрос #{rid} на подтверждение\n"
            f"От админа: {_admin_display(admin)} (id {admin['id']})\n\n{summary}")
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("✅ Разрешить", callback_data=f"areq:ok:{rid}"),
           telebot.types.InlineKeyboardButton("✖️ Отклонить", callback_data=f"areq:no:{rid}"))
    for sid in SUPER_ADMIN_IDS:
        try:
            tgsend.tg.send_message(sid, text, reply_markup=kb)
        except Exception as e:
            print(f"Не смог уведомить супер-админа {sid}: {e}")


def _gate(admin, action, payload, summary):
    """Супер-админ — выполняет сразу; обычный админ — создаёт заявку на подтверждение."""
    if is_super_admin(int(admin["id"])):
        return jsonify({"ok": True, "pending": False,
                        "result": notifications.run_admin_request(tgsend.tg, action, payload)})
    rid = db.create_admin_request(int(admin["id"]), _admin_display(admin), action, payload, summary)
    _notify_supers_request(rid, admin, summary)
    return jsonify({"ok": True, "pending": True, "request_id": rid})


def _super(data):
    """Проверка «это супер-админ» — общая для всех операций с правами."""
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return None
    return user
