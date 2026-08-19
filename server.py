"""
server.py — веб-сервер Mini App (вся витрина внутри приложения).

Отдаёт:
  • страницу-витрину (webapp/: index.html + styles.css + app.js, склеиваются на лету)
  • /api/me       — узнать, подтверждён ли 18+ у пользователя
  • /api/age      — подтвердить 18+
  • /api/products — список товаров из базы
  • /api/photo    — картинку товара (прокси из Telegram по file_id)
  • /api/order    — создать заказ (возвращает реквизиты для оплаты)
  • /api/receipt  — принять фото чека, отправить продавцу города

Бот (bot.py) и этот сервер — отдельные процессы, общая база и настройки (config.py).
Кнопки статусов у продавца обрабатывает бот.

Запуск локально:  DEV_MODE=1 venv/bin/python server.py
"""

import os
import gzip
import hmac
import hashlib
import html
import json
import threading
import time
from urllib.parse import parse_qsl

import requests
import telebot
from flask import Flask, g, jsonify, request, Response

import config
import db
import errors
import notifications
from config import (BOT_TOKEN, PAYMENT_INFO, ADMIN_IDS, SUPER_ADMIN_IDS, SUPPORT_IDS, CONFIRM_MINUTES, is_admin, is_super_admin, admin_city, admin_role, all_admin_ids)

db.init_db()
config.seed_admins_from_env()   # разовый перенос админов из окружения в базу

# Отдельный экземпляр бота — ТОЛЬКО чтобы отправлять сообщения/картинки.
tg = telebot.TeleBot(BOT_TOKEN)

# Имя бота для реферальных ссылок (t.me/<bot>?startapp=...).
try:
    BOT_USERNAME = tg.get_me().username
except Exception as e:
    print(f"Не смог узнать имя бота: {e}")
    BOT_USERNAME = ""

REFERRAL_BONUS = 50        # vapecoins пригласившему за нового друга
COINS_PER_BYN = 1          # vapecoins клиенту за каждый Br выданного заказа
COIN_VALUE = 0.01          # сколько стоит 1 монета при списании (100 монет = 1 Br)
LOW_STOCK = 3              # с этого остатка товар считается «заканчивается» (везде одинаково)


app = Flask(__name__, static_folder="webapp", static_url_path="")

# DEV_MODE=1 — разрешить пользоваться из обычного браузера (без Telegram) для локальной проверки.
#
# Он подставляет ВЛАДЕЛЬЦА любому, кто открыл страницу без подписи Telegram.
# На боевом это означало бы админку без пароля для всего интернета: достаточно
# один раз скопировать переменные с локальной машины на сервер. Поэтому боевая
# база (DATABASE_URL) выключает DEV_MODE намертво, что бы ни стояло в env.
_IS_PRODUCTION = bool(os.environ.get("DATABASE_URL", "").strip())
DEV_MODE = os.environ.get("DEV_MODE") == "1" and not _IS_PRODUCTION
if os.environ.get("DEV_MODE") == "1" and _IS_PRODUCTION:
    print("DEV_MODE ИГНОРИРУЕТСЯ: подключена боевая база. Вход только через Telegram.")
DEV_USER_ID = next(iter(ADMIN_IDS), 0)

_file_path_cache = {}      # кэш путей к файлам Telegram (чтобы не звать get_file каждый раз)
_photo_cache = {}          # кэш самих картинок в памяти: file_id -> (bytes, content_type)
_photo_cache_lock = threading.Lock()
_photo_cache_bytes = 0     # сколько памяти занято картинками
PHOTO_CACHE_MAX_BYTES = int(os.environ.get("PHOTO_CACHE_MB", "48")) * 1024 * 1024

# --- Кэш в памяти для частых чтений (каталог/точки/доставка/бренды) ---
# Эти данные меняются редко (через админку), а читаются на каждом открытии.
# Кэш убирает лишние round-trip'ы к Neon. При любой правке — _cache_bust().
_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        if item:
            _cache.pop(key, None)
    return None


def _cache_set(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


def _cache_bust(*prefixes):
    """Сбросить кэш чтений. Без аргументов — весь; с префиксами — только нужные ключи.

    Точечный сброс важен для заказов: заказ меняет ТОЛЬКО остатки (каталог и статистику),
    а способы доставки/точки/бренды остаются прежними. Раньше любой заказ чистил всё,
    и следующий покупатель снова ждал Neon на экране «Способ получения»."""
    with _cache_lock:
        if not prefixes:
            _cache.clear()
            return
        for k in [k for k in _cache if k.startswith(prefixes)]:
            _cache.pop(k, None)


def _bg(fn, *args, **kwargs):
    """Запускает побочный эффект (уведомления в Telegram) в фоне — чтобы ответ клиенту
    не ждал сетевых обращений к Telegram. Заказ уже сохранён в БД до вызова."""
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            # Здесь живут уведомления продавцам о новых заказах. Молчаливое
            # падение тут означает «заказ пришёл, но никто о нём не узнал» —
            # худший сорт поломки, поэтому сообщаем владельцу.
            errors.report(tg, f"фоновая задача {getattr(fn, '__name__', fn)}", e)
    threading.Thread(target=_run, daemon=True).start()


# ============================================================
#  ПРОВЕРКА ПОДЛИННОСТИ (initData от Telegram)
# ============================================================

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


@app.before_request
def _guard_owner_only():
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


def _notify_supers_request(rid, admin, summary):
    """Шлёт супер-админам запрос на подтверждение с кнопками Разрешить/Отклонить."""
    text = (f"🔐 Запрос #{rid} на подтверждение\n"
            f"От админа: {_admin_display(admin)} (id {admin['id']})\n\n{summary}")
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("✅ Разрешить", callback_data=f"areq:ok:{rid}"),
           telebot.types.InlineKeyboardButton("✖️ Отклонить", callback_data=f"areq:no:{rid}"))
    for sid in SUPER_ADMIN_IDS:
        try:
            tg.send_message(sid, text, reply_markup=kb)
        except Exception as e:
            print(f"Не смог уведомить супер-админа {sid}: {e}")


def _gate(admin, action, payload, summary):
    """Супер-админ — выполняет сразу; обычный админ — создаёт заявку на подтверждение."""
    if is_super_admin(int(admin["id"])):
        return jsonify({"ok": True, "pending": False,
                        "result": notifications.run_admin_request(tg, action, payload)})
    rid = db.create_admin_request(int(admin["id"]), _admin_display(admin), action, payload, summary)
    _notify_supers_request(rid, admin, summary)
    return jsonify({"ok": True, "pending": True, "request_id": rid})


@app.route("/api/admin/requests", methods=["POST"])
def api_admin_requests_pending():
    """Список ожидающих заявок — только супер-админ."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rows = db.list_admin_requests("pending")
    reqs = [{"id": r["id"], "requester_id": r["requester_id"], "requester_name": r["requester_name"],
             "summary": r["summary"], "created_at": r["created_at"]} for r in rows]
    return jsonify({"ok": True, "requests": reqs, "count": len(reqs)})


@app.route("/api/admin/request/decide", methods=["POST"])
def api_admin_request_decide():
    """Супер-админ разрешает/отклоняет заявку из приложения."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        rid = int(data.get("request_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    approve = data.get("decision") == "approve"
    req = db.get_admin_request(rid)
    if not req:
        return jsonify({"ok": False, "error": "not_found"}), 404
    new_status = "approved" if approve else "rejected"
    if not db.set_admin_request_status_if(rid, new_status, ["pending"]):
        return jsonify({"ok": False, "error": "already"}), 409     # уже обработана
    if approve:
        try:
            notifications.run_admin_request(tg, req["action"], json.loads(req["payload"]))
        except Exception as e:
            print(f"Ошибка выполнения заявки #{rid}: {e}")
        _notify_client(req["requester_id"], f"✅ Ваш запрос одобрен:\n{req['summary']}")
    else:
        _notify_client(req["requester_id"], f"✖️ Ваш запрос отклонён:\n{req['summary']}")
    return jsonify({"ok": True, "decided": new_status})


# ============================================================
#  СТРАНИЦА
# ============================================================

# Пути, которые меняют кэшируемые данные (каталог/точки/доставка/бренды/склад).
# Значение — какие ключи кэша сбросить после успешного запроса; пустой кортеж = весь кэш.
# Заказы трогают только остатки, поэтому чистят каталог и статистику, а не всё подряд.
_STOCK_KEYS = ("products", "stats")
_WRITE_PATHS = {
    "/api/admin/product": (), "/api/admin/product/update": (), "/api/admin/product/specs": (),
    "/api/admin/product/from-model": (), "/api/admin/model": (), "/api/admin/model/delete": (),
    "/api/admin/model/hide": (),
    "/api/admin/model/photo": (),
    "/api/admin/category/spec": ("categories",), "/api/admin/category/spec/update": ("categories",),
    "/api/admin/category/spec/delete": ("categories",),
    "/api/admin/product/variants": (), "/api/admin/product/delete": (),
    "/api/admin/photo": (), "/api/admin/photo/add": (), "/api/admin/photo/delete": (),
    # Оценка живёт в карточке товара, поэтому её публикация обновляет витрину.
    "/api/admin/review/decide": (), "/api/admin/review/delete": (),
    "/api/admin/location": (), "/api/admin/location/delete": (),
    "/api/admin/category": ("categories",), "/api/admin/category/update": ("categories",),
    "/api/admin/category/delete": ("categories",),
    "/api/admin/delivery": (), "/api/admin/delivery/update": (), "/api/admin/delivery/delete": (),
    "/api/admin/point": (), "/api/admin/point/update": (), "/api/admin/point/delete": (),
    "/api/admin/stock/move": _STOCK_KEYS,
    "/api/admin/brand": (), "/api/admin/brand/delete": (),
    "/api/admin/settings/update": (), "/api/admin/stats/reset": (),
    "/api/order": _STOCK_KEYS,                  # меняют остаток на складе
    "/api/order/cancel": _STOCK_KEYS,
    "/api/admin/order/status": _STOCK_KEYS,
    "/api/admin/order/items": _STOCK_KEYS,      # правка количеств двигает склад
    # Подписка «сообщить о поступлении» меняет счётчик ждущих в карточке товара:
    # без сброса продавец до полуминуты видел бы старое число.
    "/api/notify-me": ("products",),
}


@app.errorhandler(Exception)
def _report_unhandled(e):
    """Любая необработанная ошибка запроса. Раньше она уходила в логи Render,
    где её никто не видел, а покупатель просто получал пустой экран."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e                      # 404 и прочие штатные ответы — не поломка
    errors.report(tg, f"{request.method} {request.path}", e)
    return jsonify({"ok": False, "error": "server_error"}), 500


@app.after_request
def _bust_cache_on_write(resp):
    if request.method == "POST" and 200 <= resp.status_code < 300:
        keys = _WRITE_PATHS.get(request.path)
        if keys is not None:
            _cache_bust(*keys)
            # Остаток мог измениться где угодно: правка товара, замена вкусов,
            # отклонение заказа. Ловим это в ОДНОМ месте, а не в каждом маршруте
            # — иначе новый способ менять склад однажды забудут сюда вписать.
            if keys is _STOCK_KEYS or request.path.startswith("/api/admin/product"):
                _bg(_flush_stock_alerts)
        _write_admin_log(resp)
    return resp


# Админские маршруты, которые только читают. Список явный: угадывать по имени
# («заканчивается на -s») — верный способ однажды не записать изменение.
_ADMIN_READS = {
    "/api/admin/requests", "/api/admin/reviews", "/api/admin/users", "/api/admin/customer",
    "/api/admin/orders", "/api/admin/stats", "/api/admin/stock/moves", "/api/admin/promos",
    "/api/admin/staff", "/api/admin/raffle", "/api/admin/settings", "/api/admin/models",
    "/api/admin/referrals", "/api/admin/log", "/api/admin/products", "/api/admin/today",
}

# Поля запроса, которые стоит сохранить в журнале. Остальные — либо секреты
# (initData), либо шум (картинки в base64), либо и то и другое.
_LOG_FIELDS = ("id", "model_id", "product_id", "name", "field", "value", "city",
               "price", "cost", "stock", "code", "reason", "qty", "status", "action",
               "user_id", "delta", "coins", "spins", "text")


def _write_admin_log(resp):
    """Кто и что изменил. Пишем централизованно по факту успешного изменения:
    вписывать вызов в каждый маршрут — значит однажды забыть про новый."""
    if not request.path.startswith("/api/admin/") or request.path in _ADMIN_READS:
        return
    admin = getattr(g, "admin", None)
    if not admin:
        return                                   # действие не админа — не наш журнал
    try:
        src = request.get_json(silent=True) or request.form or {}
        parts = [f"{k}={str(src[k])[:40]}" for k in _LOG_FIELDS if k in src and src[k] not in ("", None)]
        db.log_admin_action(int(admin["id"]), _admin_display(admin),
                            request.path.replace("/api/admin/", ""), " · ".join(parts))
    except Exception as e:
        print(f"Журнал действий: {e}")


def _flush_stock_alerts():
    """Сообщает тем, кто ждал товар, что он снова в наличии."""
    try:
        ready = db.stock_alerts_ready()
    except Exception as e:
        print(f"Не удалось прочитать подписки на поступление: {e}")
        return
    if not ready:
        return
    done = set()
    for uid, pid, name in ready:
        try:
            tg.send_message(uid, f"🔔 «{name}» снова в наличии.\nОткройте приложение — товар доступен к заказу.")
        except Exception as e:
            print(f"Не смог сообщить о поступлении {pid} покупателю {uid}: {e}")
        done.add(pid)
    for pid in done:
        try:
            db.clear_stock_alerts(pid)
        except Exception as e:
            print(f"Не удалось очистить подписки на товар {pid}: {e}")


@app.route("/api/notify-me", methods=["POST"])
def api_notify_me():
    """«Сообщите, когда появится». Раньше на карточке отсутствующего товара
    покупателю было нечего нажать — он просто уходил."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "no_user"}), 403
    try:
        pid = int(data.get("product_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    p = db.get_product(pid)
    if not p:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # Тем же адресом и отписываемся: подписка ставилась одним нажатием, а снять
    # её было нельзя — «сообщите, когда появится» работало только в одну сторону.
    # Проверяем ДО остатка: отписаться нужно и от товара, который уже завезли.
    if data.get("off"):
        db.remove_stock_alert(pid, int(user["id"]))
        return jsonify({"ok": True, "off": True})
    if (p["stock"] or 0) > 0:
        # Пока покупатель раздумывал, товар завезли — подписка не нужна.
        return jsonify({"ok": True, "in_stock": True})
    db.add_stock_alert(pid, int(user["id"]))
    return jsonify({"ok": True, "in_stock": False})


@app.before_request
def _start_timer():
    request._t0 = time.time()


@app.after_request
def _log_slow(resp):
    """Пишем в лог только медленные запросы (>700 мс) — чтобы в логах Render было видно,
    ЧТО именно тормозит, а не гадать. Быстрые запросы лог не засоряют."""
    t0 = getattr(request, "_t0", None)
    if t0 is not None:
        ms = int((time.time() - t0) * 1000)
        if ms >= 700:
            print(f"[медленно] {request.method} {request.path} — {ms} мс")
    return resp


_GZIP_TYPES = ("text/html", "text/css", "application/javascript",
               "text/javascript", "application/json", "image/svg+xml")


@app.after_request
def _compress(resp):
    """Сжимаем текстовые ответы (html/js/json) gzip — меньше трафика, быстрее загрузка.
    Картинки/бинарники не трогаем (они уже сжаты)."""
    try:
        if "gzip" not in request.headers.get("Accept-Encoding", ""):
            return resp
        if resp.direct_passthrough or resp.status_code < 200 or resp.status_code >= 300:
            return resp
        if resp.headers.get("Content-Encoding"):
            return resp        # уже сжато на месте (index.html) — второй раз нельзя
        ctype = (resp.content_type or "").split(";")[0].strip()
        if ctype not in _GZIP_TYPES:
            return resp
        data = resp.get_data()
        if len(data) < 1024:                       # мелочь сжимать невыгодно
            return resp
        resp.set_data(gzip.compress(data, 5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        resp.headers["Content-Length"] = len(resp.get_data())
    except Exception as e:
        print(f"gzip пропущен: {e}")
    return resp


# Приложение лежит в трёх файлах, а к покупателю уходит одной страницей.
# Разложено ради чтения: 6600 строк разметки, стилей и кода в одном файле
# правились вслепую. Склеено ради скорости: Телеграм открывает приложение
# во встроенном браузере, и каждый лишний запрос — это лишняя задержка на
# мобильной сети, ещё до того как человек увидит витрину.
# Сборки при этом нет никакой: подстановка двух кусков в разметку, здесь и
# сейчас. Правишь файл — обновляешь страницу, как и раньше.
_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
_INDEX_PATH = os.path.join(_WEBAPP_DIR, "index.html")
_STYLES_PATH = os.path.join(_WEBAPP_DIR, "styles.css")
_APP_DIR = os.path.join(_WEBAPP_DIR, "app")


def _app_parts():
    """Куски приложения по порядку имён: 01-core.js, 02-games.js, …

    Порядок берётся из имени файла, а не из списка в коде: список пришлось бы
    править при каждом новом куске, а забытая строчка означала бы приложение
    без части кода — молча. Имена нумерованы, потому что это одна программа,
    разложенная по файлам, а не независимые модули: порядок склейки важен.
    """
    return [os.path.join(_APP_DIR, и) for и in sorted(os.listdir(_APP_DIR))
            if и.endswith(".js")]

_index_cache = {"key": None, "raw": b"", "gz": b"", "etag": ""}
_index_lock = threading.Lock()


def _index_payload():
    """Готовое приложение: сырые байты, сжатые байты и ETag.

    Файл на 430 КБ читался с диска и сжимался заново на КАЖДОЕ открытие
    приложения, хотя меняется он только при деплое. Держим результат в памяти
    и пересобираем, когда у любого из трёх файлов изменилось время правки
    или размер.
    """
    части = [_INDEX_PATH, _STYLES_PATH] + _app_parts()
    key = tuple((st.st_mtime_ns, st.st_size) for st in (os.stat(п) for п in части))
    with _index_lock:
        if _index_cache["key"] == key:
            return dict(_index_cache)

    def прочитать(путь):
        with open(путь, encoding="utf-8") as f:
            return f.read()

    страница = прочитать(_INDEX_PATH)
    вставки = (("/*{{СТИЛИ}}*/", прочитать(_STYLES_PATH)),
               ("//{{ПРИЛОЖЕНИЕ}}", "\n".join(прочитать(п) for п in _app_parts())))
    for метка, текст in вставки:
        # Молчаливая подстановка опаснее падения: не найдись метка — магазин
        # открылся бы без стилей или без кода, и понять почему было бы нечем.
        if метка not in страница:
            raise RuntimeError(f"в webapp/index.html нет метки {метка}")
        страница = страница.replace(метка, текст, 1)

    raw = страница.encode("utf-8")
    entry = {"key": key, "raw": raw, "gz": gzip.compress(raw, 6),
             "etag": '"%s"' % hashlib.md5(raw).hexdigest()}
    with _index_lock:
        _index_cache.update(entry)
    return entry


@app.route("/")
def index():
    entry = _index_payload()
    use_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    # Метка версии своя для сжатого и несжатого: тела разные, и одна метка на
    # оба — шанс получить от прокси не тот вариант, который просили.
    etag = entry["etag"][:-1] + '-gz"' if use_gzip else entry["etag"]

    # Приложение целиком качалось при каждом открытии — сотня килобайт по мобильной
    # сети только ради того, чтобы получить ровно тот же файл. Теперь браузер
    # присылает ETag, и, пока версия не сменилась, ответ — пустой 304.
    if request.headers.get("If-None-Match") == etag:
        resp = Response(status=304)
    else:
        resp = Response(entry["gz"] if use_gzip else entry["raw"],
                        content_type="text/html; charset=utf-8")
        if use_gzip:
            resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Cache-Control"] = "no-cache"    # всегда сверяемся: после деплоя нужна свежая
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["ETag"] = etag
    return resp


@app.route("/health")
def health():
    """Лёгкий адрес для пинга — держит сервис «разбуженным» (без обращения к базе)."""
    return "ok"


# ============================================================
#  18+
# ============================================================

@app.route("/api/me", methods=["POST"])
def api_me():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    # Всё, что нужно этому экрану, — одним походом в базу. Раньше их было девять
    # через восемь подключений, и платил за это первый экран приложения.
    try:
        me = db.get_me_bundle(uid)
    except Exception as e:
        print(f"Не удалось собрать данные покупателя {uid}: {e}")
        me = {"age_ok": False, "reminders_on": True, "my_point": None,
              "alerts": [], "prefill": {"phone": "", "addresses": {}}, "raffle_on": False}
    # Запоминаем, как человека зовут. Telegram присылает имя при каждом открытии
    # приложения, а мы его нигде не сохраняли: в списке покупателей все, кто ещё
    # не сделал заказ, выглядели голым числом.
    _bg(db.remember_user_name, uid, user.get("username") or "", user.get("first_name") or "")

    # Реферал: friend открыл Mini App с параметром start_param=refN (дубль к боту).
    # Только ЗАПОМИНАЕМ пригласившего — монеты дадим, когда друг сделает заказ.
    start_param = str(data.get("start_param") or "")
    if start_param.startswith("ref"):
        try:
            ok_ref = db.set_referrer_once(uid, int(start_param[3:]))
            print(f"[ref/miniapp] uid={uid} start_param={start_param} set={ok_ref}")
        except (TypeError, ValueError):
            print(f"[ref/miniapp] uid={uid} плохой start_param={start_param}")

    return jsonify({"ok": True, "age_ok": me["age_ok"], "is_admin": is_admin(uid),
                    "is_super": is_super_admin(uid), "alerts": me["alerts"],
                    # Роль и точка: приложение прячет по ним то, что сервер всё
                    # равно вернёт с 403. Пустой город у продавца — все точки.
                    "role": admin_role(uid),
                    "admin_city": admin_city(uid) if is_admin(uid) else "",
                    "reminders_on": me["reminders_on"], "prefill": me["prefill"],
                    # Идёт ли розыгрыш. Приложение прячет по этому флагу целую
                    # вкладку: показывать «Розыгрыши» там, где ничего не
                    # разыгрывают, — обещание, которого магазин не давал.
                    "raffle_on": me["raffle_on"],
                    "my_point": me["my_point"]})


@app.route("/api/age", methods=["POST"])
def api_age():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    db.set_age_ok(int(user["id"]))
    return jsonify({"ok": True})


@app.route("/api/my-settings", methods=["POST"])
def api_my_settings():
    """Настройки покупателя: своя точка самовывоза, телефон, напоминания.

    Всё это выбирается ОДИН раз и потом подставляется в заказ — но в самом
    заказе остаётся сменяемым: сегодня человеку удобна одна точка, завтра
    другая, и лезть ради этого в настройки он не станет."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])

    if "point_id" in data:
        raw = data.get("point_id")
        if raw in (None, "", 0, "0"):
            db.set_user_point(uid, None)             # «не выбрано» — законный вариант
        else:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "bad_id"}), 400
            # Точка должна существовать: иначе в профиле осядет ссылка в никуда.
            exists = any(p["id"] == pid for loc in db.get_locations()
                         for p in db.get_pickup_points(loc["name"]))
            if not exists:
                return jsonify({"ok": False, "error": "bad_point"}), 400
            db.set_user_point(uid, pid)

    if "phone" in data:
        # Пустой телефон — законный ответ («не хочу указывать»), а вот огрызок
        # вроде «+375» хуже пустого: он молча подставится в заказ, и продавец
        # будет звонить в никуда. Тот же порог, что при оформлении доставки.
        phone = _text(data.get("phone"))
        if phone and len(_digits(phone)) < 7:
            return jsonify({"ok": False, "error": "bad_phone"}), 400
        db.set_user_phone(uid, phone)
    if "reminders_on" in data:
        db.set_no_reminders(uid, not data.get("reminders_on"))

    return jsonify({"ok": True, "point_id": db.get_user_point(uid),
                    "phone": db.get_user_phone(uid),
                    "reminders_on": not db.get_no_reminders(uid)})


@app.route("/api/my-points")
def api_my_points():
    """Точки самовывоза всех городов — чтобы покупатель выбрал свою в настройках."""
    out = []
    for loc in db.get_locations():
        for p in db.get_pickup_points(loc["name"]):
            out.append({"id": p["id"], "city": loc["name"], "address": p["address"], "note": p["note"] or ""})
    return jsonify({"ok": True, "points": out})


@app.route("/api/promo/check", methods=["POST"])
def api_promo_check():
    """Проверить код до оформления, чтобы покупатель сразу видел скидку.
    Заказ всё равно пересчитает всё заново — это только показ."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    try:
        subtotal = max(0.0, float(data.get("subtotal") or 0))
    except (TypeError, ValueError):
        subtotal = 0.0
    discount, err = db.check_promo(data.get("code"), int(user["id"]), subtotal)
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True, "discount": discount})


@app.route("/api/reminders", methods=["POST"])
def api_reminders():
    """Включить/выключить напоминания о повторной покупке.

    Отписка обязана быть на виду и работать сразу: рассылка, от которой нельзя
    уйти, кончается не жалобой, а блокировкой бота — и тогда покупатель потерян
    вместе со всеми будущими заказами."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    on = bool(data.get("on"))
    db.set_no_reminders(int(user["id"]), not on)
    return jsonify({"ok": True, "on": on})


# ============================================================
#  ОТЗЫВЫ
# ============================================================

@app.route("/api/reviews")
def api_reviews():
    """Опубликованные отзывы о товаре — их видят все."""
    try:
        pid = int(request.args.get("product_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    rows = db.list_reviews(pid)
    return jsonify({"ok": True, "reviews": [{
        "id": r["id"], "rating": r["rating"], "text": r["text"] or "",
        "who": _review_author(r), "created_at": r["created_at"] or "",
        "reply": (r["reply"] or "") if "reply" in r else "",
    } for r in rows]})


def _review_author(r):
    """Как подписан отзыв. Полное имя не показываем — покупателю неприятно
    увидеть себя по имени под отзывом о вейпе."""
    name = (r["username"] or "").lstrip("@").strip()
    return f"@{name}" if name else "Покупатель"


@app.route("/api/my-reviews", methods=["POST"])
def api_my_reviews():
    """Что этот покупатель может оценить и что уже написал."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    mine = [{"product_id": r["product_id"], "rating": r["rating"], "status": r["status"]}
            for r in db.list_reviews_by_user(uid)]
    return jsonify({"ok": True, "can": db.reviewable_products(uid), "mine": mine})


@app.route("/api/review", methods=["POST"])
def api_review():
    """Оставить отзыв. Право даёт покупка, а не желание высказаться."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    try:
        pid = int(data.get("product_id"))
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_input"}), 400
    if rating < 1 or rating > 5:
        return jsonify({"ok": False, "error": "bad_rating"}), 400
    if not any(p["id"] == pid for p in db.reviewable_products(uid)):
        # Либо не покупал, либо уже оценивал — из ответа этого не видно специально.
        return jsonify({"ok": False, "error": "not_allowed"}), 403
    rid = db.add_review(pid, uid, rating, data.get("text") or "", user.get("username") or "")
    if not rid:
        return jsonify({"ok": False, "error": "not_allowed"}), 403
    _bg(_notify_new_review, rid)
    return jsonify({"ok": True, "id": rid})


def _notify_new_review(review_id):
    """Сообщаем админам, что отзыв ждёт решения: иначе он повиснет невидимым."""
    r = db.get_review(review_id)
    if not r:
        return
    p = db.get_product(r["product_id"])
    text = (f"⭐ Новый отзыв на модерации\n"
            f"{'★' * int(r['rating'])}{'☆' * (5 - int(r['rating']))} — {(p['name'] if p else 'товар')}\n"
            f"От: {_review_author(r)}\n\n{(r['text'] or '(без текста)')}\n\n"
            f"Опубликовать — в приложении: Админ → Отзывы")
    for aid in all_admin_ids():
        try:
            tg.send_message(aid, text)
        except Exception as e:
            print(f"Не смог сообщить админу {aid} об отзыве: {e}")


@app.route("/api/admin/reviews", methods=["POST"])
def api_admin_reviews():
    """Отзывы для админа: очередь на модерацию, опубликованные, скрытые."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    status = (data.get("status") or "pending").strip()
    if status not in ("pending", "approved", "hidden", "all"):
        status = "pending"
    rows = [r for r in db.admin_reviews(status) if _review_in_scope(admin, r)]
    return jsonify({"ok": True, "status": status, "pending": db.count_pending_reviews(), "reviews": [{
        "id": r["id"], "product_id": r["product_id"], "product": r["product_name"] or "товар удалён",
        "rating": r["rating"], "text": r["text"] or "", "who": _review_author(r),
        "created_at": r["created_at"] or "", "status": r["status"],
        "reply": r.get("reply") or "",
    } for r in rows]})


def _sold_here(city):
    """Что продаётся на точке: id товаров и id их моделей."""
    pids, mids = set(), set()
    for p in db.get_all_products():
        if p["city"] == city:
            pids.add(p["id"])
            mid = p["model_id"] if "model_id" in p.keys() else None
            if mid:
                mids.add(mid)
    return pids, mids


def _review_in_scope(admin, review):
    """Продавец отвечает за то, чем торгует. Отзыв о модели, которой на его
    точке нет, — не его разговор, и в очереди он только мешает."""
    scope = (admin or {}).get("city")
    if not scope:
        return True
    pids, mids = _sold_here(scope)
    # Отзыв приходит и строкой из базы, и готовым словарём — .keys() понимают оба.
    mid = review["model_id"] if "model_id" in review.keys() else None
    return review["product_id"] in pids or (mid is not None and mid in mids)


@app.route("/api/admin/review/delete", methods=["POST"])
def api_admin_review_delete():
    """Удалить отзыв насовсем. «Скрыть» оставляет его в базе — это для мусора."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        rid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.delete_review(rid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/review/reply", methods=["POST"])
def api_admin_review_reply():
    """Ответ магазина под отзывом. Пустой текст убирает ответ."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        rid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    review = db.get_review(rid)
    if not review:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if not _review_in_scope(admin, review):
        return jsonify({"ok": False, "error": "other_city",
                        "message": "Этого товара на вашей точке нет."}), 403
    if not db.set_review_reply(rid, data.get("text") or ""):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/review/decide", methods=["POST"])
def api_admin_review_decide():
    """Опубликовать отзыв или скрыть его."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        rid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    status = "approved" if data.get("ok") else "hidden"
    if not db.set_review_status(rid, status):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "status": status})


def _json_etag(payload):
    """JSON со сверкой версии: пока данные не менялись, браузер получает 304.

    Витрина и справочники перекачивались целиком при каждом открытии приложения,
    хотя меняются редко. Хэш считается от готового тела ответа, поэтому «не
    менялись» здесь значит буквально это — отдать устаревшее нельзя.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Метка версии зависит и от сжатия: ниже ответ может уйти gzip'ом, и одна
    # метка на два разных тела — это шанс однажды получить от прокси не тот
    # вариант, который просили.
    gz = "gzip" in request.headers.get("Accept-Encoding", "")
    etag = '"%s%s"' % (hashlib.md5(body.encode("utf-8")).hexdigest(), "-gz" if gz else "")
    if request.headers.get("If-None-Match") == etag:
        resp = Response(status=304)
    else:
        resp = Response(body, content_type="application/json")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


# ============================================================
#  ЛОКАЦИИ (точки продаж)
# ============================================================


@app.route("/api/categories")
def api_categories():
    """Категории товара — витрина строит по ним фильтры, админка формы."""
    cached = _cache_get("categories")
    if cached is None:
        by_cat = {}
        for s in db.list_category_specs():
            by_cat.setdefault(s["category"], []).append(s)
        cached = _cache_set("categories", [{"code": c["code"], "name": c["name"],
                                            "emoji": c["emoji"] or "", "sort": c["sort"],
                                            # Вкусы есть не у всего: у картриджей их нет,
                                            # а у жидкостей товар без них не завести.
                                            "has_flavors": bool(c.get("has_flavors")),
                                            "specs": by_cat.get(c["code"], [])}
                                           for c in db.list_categories()], 300)
    return _json_etag(cached)


@app.route("/api/also-bought")
def api_also_bought():
    """Что покупали вместе — для подсказки в корзине. Считается по выданным заказам."""
    cached = _cache_get("also_bought")
    if cached is None:
        try:
            data = db.also_bought()
        except Exception as e:
            data = {}                     # без подсказок корзина работает как раньше
            print(f"Не удалось посчитать совместные покупки: {e}")
        # Ключи в JSON всё равно станут строками — приводим сразу, чтобы фронт
        # не гадал, каким типом искать.
        cached = _cache_set("also_bought", {str(k): v for k, v in data.items()}, 600)
    return jsonify(cached)


def _delivery_json(m):
    return {
        "id": m["id"], "name": m["name"],
        "needs_address": bool(m["needs_address"]),
        "address_label": m["address_label"] or "Адрес",
        "pickup_address": m["pickup_address"] or "",
        "needs_point": bool(m["needs_point"]),     # покупатель выбирает точку из списка
        "fee": round(m["fee"] or 0, 2),
        "needs_payment": bool(m["needs_payment"]),
    }


# ============================================================
#  ТОВАРЫ
# ============================================================

def _all_products_payload():
    """Полный список товаров (все точки). Кэш 30с — витрина открывается без похода в базу.
    Заказ всё равно проверяет остаток по живой базе, так что кратковременный лаг склада не опасен."""
    cached = _cache_get("products")
    if cached is not None:
        return cached
    variants_by = {}
    for v in db.get_all_variants():
        variants_by.setdefault(v["product_id"], []).append({"flavor": v["flavor"], "stock": v["stock"]})
    try:
        waiting = db.stock_alert_counts()
    except Exception as e:
        waiting = {}                      # счётчик — не повод ронять витрину
        print(f"Не удалось посчитать ожидающих: {e}")
    try:
        ratings = db.product_ratings()
    except Exception as e:
        ratings = {}                      # без оценок витрина живёт
        print(f"Не удалось прочитать оценки товаров: {e}")
    gallery, model_gallery = {}, {}
    try:
        for ph in db.all_product_photos():
            gallery.setdefault(ph["product_id"], []).append(ph)
        for ph in db.all_model_photos():
            model_gallery.setdefault(ph["model_id"], []).append(ph)
    except Exception as e:
        print(f"Не удалось прочитать галерею товаров: {e}")   # без галереи витрина живёт
    out = []
    for p in db.get_all_products():
        # Главное фото всегда первое: покупатель видит ту же картинку, что и в каталоге.
        photos = ([{"id": 0, "url": f"/api/photo?file_id={p['photo']}",
                    "thumb": f"/api/photo?file_id={p['photo_thumb'] or p['photo']}"}] if p["photo"] else [])
        mid = p["model_id"] if "model_id" in p.keys() else None
        # Галерея — свойство модели; у товаров, заведённых до неё, остаётся своя.
        extra = model_gallery.get(mid) if mid else gallery.get(p["id"], [])
        for ph in (extra or []):
            photos.append({"id": ph["id"], "url": f"/api/photo?file_id={ph['file_id']}",
                           "thumb": f"/api/photo?file_id={ph['thumb_id'] or ph['file_id']}"})
        out.append({
            "photos": photos,
            "rating": ratings.get(p["id"], {"avg": 0, "count": 0}),
            "id": p["id"], "name": p["name"], "price": p["price"],
            # Ссылка на модель из «Ассортимента»: у товара, заведённого по ней,
            # описание правится там, а здесь остаются цена, закупка и остаток.
            "model_id": p["model_id"] if "model_id" in p.keys() else None,
            "stock": p["stock"], "is_hit": p["is_hit"],
            "category": p["category"], "city": p["city"],
            "description": p["description"] or "",
            "cost": round(float(p["cost"] or 0), 2),   # видит только админка
            "brand": p["brand"] or "", "flavor": p["flavor"] or "",
            "strength": p["strength"] or "", "volume": p["volume"] or "",
            # Характеристики своей категории: сопротивление у картриджа,
            # мощность и аккумулятор у пода.
            "specs": db.product_specs(p),
            "variants": variants_by.get(p["id"], []),
            # Снят с витрины: в каталог такой товар не попадает вовсе, но
            # остаток, история и отзывы при нём остаются.
            "hidden": bool(p["hidden"]) if "hidden" in p.keys() else False,
            # Сколько человек ждут поступления — админу видно, что завозить.
            "waiting": waiting.get(p["id"], 0),
            "photo_url": (f"/api/photo?file_id={p['photo']}" if p["photo"] else None),
            # Для сетки каталога — копия поменьше. У старых товаров её нет, тогда
            # отдаём полноразмерную: витрина в любом случае что-то покажет.
            "thumb_url": (f"/api/photo?file_id={p['photo_thumb'] or p['photo']}" if p["photo"] else None),
        })
    return _cache_set("products", out, 30)


@app.route("/api/products")
def api_products():
    """Витрина покупателя. Снятое с продажи сюда не попадает — не полагаемся на
    то, что каждый экран приложения не забудет его отфильтровать.

    Закупочная цена вырезается здесь же: она лежала в том же ответе, что и
    витрина, и любой покупатель мог прочитать, почём мы берём товар."""
    city = _text(request.args.get("city")) or None
    out = [_public_product(p) for p in _all_products_payload() if not p["hidden"]]
    if city:
        out = [p for p in out if p["city"] == city]
    return _json_etag(out)


# Что в товаре не для покупателя: закупка (наша маржа), число ждущих
# поступления (наша кухня) и пометка «снят с витрины» (его тут и не будет).
_ADMIN_ONLY_FIELDS = ("cost", "waiting", "hidden")


def _public_product(p):
    return {k: v for k, v in p.items() if k not in _ADMIN_ONLY_FIELDS}


RECEIPT_TOKEN_TTL = 6 * 3600       # ссылка на чек живёт полдня, не вечно


def photo_token(file_id):
    """Короткий пропуск к картинке чека. Выдаём его тому, кто уже доказал право
    на заказ; в самой ссылке пропуск ничего не раскрывает и через полдня гаснет.

    Класть в адрес картинки строку входа Telegram нельзя: адреса попадают в
    логи сервера и историю браузера, а она — ключ от аккаунта на сутки."""
    exp = int(time.time()) + RECEIPT_TOKEN_TTL
    sig = hmac.new(BOT_TOKEN.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def _token_ok(file_id, token):
    try:
        exp_s, sig = (token or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    want = hmac.new(BOT_TOKEN.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, sig)


def _text(value, limit=None):
    """Строка из того, что прислал клиент, — что бы он ни прислал.

    Раньше по всему серверу стояло `(data.get("x") or "").strip()`. Приложение
    шлёт строку, и всё работало; но стоило прийти списку или числу — обработчик
    падал с 500. Причём каждое такое падение ещё и отправляло разработчику
    письмо о сбое, то есть любой желающий мог завалить почту, зная адрес.
    Списки и словари строкой не считаем: это не «текст с опечаткой», а мусор.
    """
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    s = value if isinstance(value, str) else str(value)
    # Нулевой байт: SQLite его молча глотает, а Postgres отказывается принимать
    # такую строку вовсе — и запрос падает с 500. Пришёл он из адреса или из
    # тела, для нас это мусор в любом случае.
    s = s.replace("\x00", "").strip()
    return s[:limit] if limit else s


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _may_see_photo(file_id, token=""):
    """Картинки товаров открыты всем — это витрина. Всё остальное здесь —
    чеки об оплате: к ним нужен пропуск.

    Раньше по этой ссылке чек забирал кто угодно: адрес вида
    /api/photo?file_id=… ничем не защищён, а живёт он вечно — достаточно,
    чтобы он попал в чужой лог, историю браузера или на скриншот.
    """
    try:
        if db.is_product_photo(file_id):
            return True
        owner = db.receipt_owner(file_id)
    except Exception as e:
        print(f"Не смог проверить права на фото {file_id}: {e}")
        return True                     # база отвечает плохо — витрина важнее
    if owner is None:
        return True                     # ни товар, ни чек: старые картинки из чата
    return _token_ok(file_id, token)


@app.route("/api/photo")
def api_photo():
    file_id = request.args.get("file_id", "")
    if not file_id:
        return Response("no file_id", status=404)
    if not _may_see_photo(file_id, request.args.get("t", "")):
        return Response("not found", status=404)

    # file_id намертво привязан к содержимому картинки: оно никогда не меняется.
    # Значит браузеру достаточно один раз сверить ETag — и не качать заново.
    if request.headers.get("If-None-Match") == f'"{file_id}"':
        return _photo_not_modified(file_id)

    # 1. Уже в памяти этого процесса — отдаём мгновенно.
    cached = _photo_cache.get(file_id)
    if cached:
        return _photo_response(cached[0], cached[1], file_id)

    # 2. Есть в базе — значит когда-то качали. Перезапуск сервера это переживает.
    try:
        stored = db.get_photo_blob(file_id)
    except Exception as e:
        stored = None
        print(f"Не удалось прочитать фото {file_id} из базы: {e}")
    if stored:
        _photo_cache_put(file_id, stored[0], stored[1])
        return _photo_response(stored[0], stored[1], file_id)

    # 3. Первый раз: тянем из Telegram (два запроса) и сохраняем, чтобы это был
    #    последний раз — и для этого процесса, и для всех будущих.
    try:
        path = _file_path_cache.get(file_id)
        if not path:
            path = tg.get_file(file_id).file_path
            _file_path_cache[file_id] = path
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "image/jpeg")
        _photo_cache_put(file_id, r.content, ctype)
        _bg(_store_photo_blob, file_id, ctype, r.content)   # запись в базу не задерживает ответ
        return _photo_response(r.content, ctype, file_id)
    except Exception as e:
        _file_path_cache.pop(file_id, None)      # путь мог протухнуть — сбросим, чтобы взять заново
        print(f"Ошибка отдачи фото {file_id}: {e}")
        return Response("photo error", status=404)


GRID_PHOTO_MIN_WIDTH = 480     # карточка каталога ~190px, но экраны телефонов 2-3x


def _pick_photo_sizes(sizes):
    """Из набора копий, который вернул Telegram, берём две: большую и для сетки.

    Telegram сам хранит одну картинку в нескольких размерах (обычно 90/320/800/1280).
    Раньше мы всегда брали самую большую — и гоняли её в каталог, где она
    показывается в ~190 пикселей шириной. Теперь для сетки берём копию поменьше,
    а полноразмерную оставляем для карточки товара. Своего ресайза не нужно."""
    sizes = list(sizes or [])
    if not sizes:
        return None, None
    ordered = sorted(sizes, key=lambda s: getattr(s, "width", 0) or 0)
    full = ordered[-1]
    grid = next((s for s in ordered if (getattr(s, "width", 0) or 0) >= GRID_PHOTO_MIN_WIDTH), full)
    return full.file_id, grid.file_id


def _store_photo_blob(file_id, ctype, content):
    try:
        if db.is_product_photo(file_id):     # чеки в базе не держим, см. is_product_photo
            db.save_photo_blob(file_id, ctype, content)
    except Exception as e:
        print(f"Не удалось сохранить фото {file_id} в базу: {e}")


def _photo_cache_put(file_id, content, ctype):
    """Кладёт картинку в память под общий лимит по весу.

    Считаем именно байты, а не штуки: раньше лимит был «200 картинок», и при
    полноразмерных фото это могло съесть сотни мегабайт — на Render их нет."""
    with _photo_cache_lock:
        if file_id in _photo_cache:
            return
        global _photo_cache_bytes
        if _photo_cache_bytes + len(content) > PHOTO_CACHE_MAX_BYTES:
            return
        _photo_cache[file_id] = (content, ctype)
        _photo_cache_bytes += len(content)


def _photo_headers(resp, file_id):
    # immutable = «не перепроверяй вообще»: содержимое по этому file_id не изменится.
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["ETag"] = f'"{file_id}"'
    return resp


def _photo_response(content, ctype, file_id):
    """Ответ с картинкой + заголовки кэша, чтобы браузер не запрашивал её повторно."""
    return _photo_headers(Response(content, content_type=ctype), file_id)


def _photo_not_modified(file_id):
    """304: у браузера уже есть эта картинка — тело не шлём."""
    return _photo_headers(Response(status=304), file_id)


# ============================================================
#  ЗАКАЗ
# ============================================================

def _payment_info():
    """Реквизиты оплаты: из настроек магазина, иначе — значение из config.
    Кэшируем: настройки меняются раз в год, а читались на каждом оформлении заказа."""
    cached = _cache_get("settings:payment_info")
    if cached is None:
        cached = _cache_set("settings:payment_info", db.get_setting("payment_info", PAYMENT_INFO), 300)
    return cached


# Ниже этого числа хвастаться нечем: «выполнено 3 заказа» отпугивает сильнее,
# чем молчание. Показываем счётчик, только когда он работает на доверие.
ORDERS_DONE_MIN = 15


def _orders_done():
    cached = _cache_get("orders_done")
    if cached is None:
        try:
            n = db.issued_orders_count()
        except Exception:
            n = 0
        cached = _cache_set("orders_done", n if n >= ORDERS_DONE_MIN else 0, 300)
    return cached


def _free_delivery_from():
    """С какой суммы доставка бесплатна. 0 = порога нет.
    Кэшируем: читается на каждом оформлении, а меняется раз в год."""
    cached = _cache_get("settings:free_delivery_from")
    if cached is None:
        try:
            val = float(db.get_setting("free_delivery_from", 0) or 0)
        except (TypeError, ValueError):
            val = 0.0
        cached = _cache_set("settings:free_delivery_from", max(0.0, val), 300)
    return cached


def _confirm_minutes():
    """Через сколько минут продавец подтверждает: из настроек, иначе — из config."""
    cached = _cache_get("settings:confirm_minutes")
    if cached is None:
        try:
            val = int(db.get_setting("confirm_minutes", CONFIRM_MINUTES))
        except (TypeError, ValueError):
            val = CONFIRM_MINUTES
        cached = _cache_set("settings:confirm_minutes", val, 300)
    return cached


SUPPORT_COOLDOWN = 20          # антиспам: не чаще 1 сообщения в поддержку за столько секунд
_support_last = {}             # uid -> время последнего сообщения (в памяти процесса)


@app.route("/api/support", methods=["POST"])
def api_support():
    """Клиент пишет в поддержку — доставляем сообщение менеджеру(ам) через бота."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    text = _text(data.get("text"), 2000)
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    uid = int(user["id"])

    # Антиспам: не даём заваливать менеджера — один вопрос раз в SUPPORT_COOLDOWN сек.
    now = time.time()
    wait = SUPPORT_COOLDOWN - (now - _support_last.get(uid, 0))
    if wait > 0:
        return jsonify({"ok": False, "error": "cooldown", "retry_after": int(wait) + 1}), 429
    _support_last[uid] = now
    uname = user.get("username")
    name = user.get("first_name") or (f"@{uname}" if uname else "клиент")
    who = _contact_link(uname, uid, name)   # кликабельно: открыть чат с клиентом

    # Необязательная привязка к заказу: проверяем, что заказ принадлежит клиенту.
    order_tag = ""
    try:
        oid = int(data.get("order_id"))
        o = db.get_order(oid)
        if o and o["user_id"] == uid:
            order_tag = f" по заказу #{oid}"
    except (TypeError, ValueError):
        pass

    msg = (f"💬 Вопрос от {who} (id <code>{uid}</code>){order_tag}:\n"
           f"{html.escape(text)}\n\n"
           f"Открыть чат: {_contact_link(uname, uid, 'написать клиенту')}  ·  или /reply {uid} ваш текст")
    delivered = 0
    for sid in SUPPORT_IDS:
        try:
            tg.send_message(sid, msg, parse_mode="HTML")
            delivered += 1
        except Exception as e:
            print(f"Не смог доставить вопрос в поддержку {sid}: {e}")
    return jsonify({"ok": True, "delivered": delivered})


@app.route("/api/admin/message", methods=["POST"])
def api_admin_message():
    """Админ пишет клиенту — доставляем сообщение клиенту через бота."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    text = _text(data.get("text"), 2000)
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    # Продавец пишет по своим заказам, а не всей базе покупателей: иначе с одной
    # точки можно разослать что угодно всем клиентам магазина.
    scope = admin.get("city")
    if scope and not any(o["city"] == scope for o in db.get_orders_by_user(target, 50)):
        return jsonify({"ok": False, "error": "other_city",
                        "message": "Этот покупатель не заказывал на вашей точке."}), 403
    contact = _contact_link(admin.get("username"), int(admin["id"]), "написать менеджеру")
    msg = (f"💬 Сообщение от магазина:\n{html.escape(text)}\n\n"
           f"По любым вопросам: {contact}")
    try:
        tg.send_message(target, msg, parse_mode="HTML")
        return jsonify({"ok": True, "sent": True})
    except Exception as e:
        print(f"Не смог отправить сообщение клиенту {target}: {e}")
        return jsonify({"ok": True, "sent": False})     # клиент мог не запускать бота


# ============================================================
#  АДМИН-API (только для тех, кто в ADMIN_IDS)
# ============================================================

# ------------------- Связь с людьми -------------------
# Сами заказы уехали в server_orders.py, а эти двое остались здесь: ими
# пользуется и поддержка, и разбор запросов продавцов, не только заказы.

def _contact_link(username, uid, label=None):
    """HTML-ссылка «открыть чат в ТГ» по @username (t.me) или по id (tg://user).
    label — текст ссылки (по умолчанию @username или имя/id)."""
    username = (username or "").lstrip("@").strip()
    if username:
        url = f"https://t.me/{username}"
        text = label or f"@{username}"
    else:
        url = f"tg://user?id={uid}"
        text = label or str(uid)
    return f'<a href="{url}">{html.escape(text)}</a>'


def _notify_client(user_id, text):
    """Сообщение клиенту о смене статуса заказа (не роняем запрос, если заблокировал бота)."""
    if not text:
        return
    try:
        tg.send_message(int(user_id), text)
    except Exception as e:
        print(f"Не смог уведомить клиента {user_id}: {e}")


# ------------------- Админы и продавцы (только супер-админ) -------------------

def _super(data):
    """Проверка «это супер-админ» — общая для всех операций с правами."""
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return None
    return user


def _notify_new_admin(uid, city):
    """Сообщаем человеку, что доступ выдан: иначе он не узнает, что теперь админ."""
    where = f" по точке «{city}»" if city else ""
    try:
        tg.send_message(uid, f"🛠 Вам выдали доступ продавца{where}.\n"
                             f"Откройте приложение — появится раздел «Управление».")
    except Exception as e:
        print(f"Не смог уведомить нового админа {uid}: {e}")


if __name__ == "__main__":
    # «python server.py» делает из ЭТОГО файла модуль __main__. Вынесенные ниже
    # модули импортируют server — и Python заводит его ВТОРОЙ раз, отдельным
    # модулем со своим Flask-приложением. Маршруты регистрируются на нём, а
    # порт слушало бы это, первое: половина ручек молча отвечала бы 404.
    # Наступали ровно на это, поэтому порт отдаём настоящему модулю.
    import server as настоящий
    port = int(os.environ.get("PORT", 5000))
    настоящий.app.run(host="0.0.0.0", port=port)


# --- Развлечения ---
# Колесо, слот и розыгрыши — в server_games.py. Маршруты регистрируются на этом
# же приложении, права проверяет тот же общий страж. Импорт внизу намеренно:
# модуль обращается к помощникам через server, и к этому моменту они готовы.
import server_games        # noqa: E402,F401  (импорт ради регистрации маршрутов)

# --- Ассортимент ---
# Товары, модели, бренды, категории и фото — в server_catalog.py.
import server_catalog      # noqa: E402,F401

# --- Устройство магазина ---
# Точки, способы получения и продавцы — в server_shop.py.
import server_shop         # noqa: E402,F401

# --- Покупатели ---
# Монеты, рефералы, карточка покупателя — в server_customers.py.
import server_customers   # noqa: E402,F401

# --- Заказы ---
# Оформление, чек, отмена, история и управление заказом продавцом —
# в server_orders.py. Обе половины пути заказа лежат там вместе намеренно:
# это один денежный узел.
import server_orders       # noqa: E402,F401

# --- Промокоды ---
# Ручки админки к db_promos.py — в server_promos.py.
import server_promos       # noqa: E402,F401

# --- Движение склада ---
# Приход, списание и журнал движений — в server_stock.py.
import server_stock        # noqa: E402,F401

# --- Экран владельца ---
# Настройки магазина, статистика и журнал действий — в server_admin.py.
import server_admin        # noqa: E402,F401
