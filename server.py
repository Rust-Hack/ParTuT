"""
server.py — веб-сервер Mini App (вся витрина внутри приложения).

Отдаёт:
  • страницу-витрину (webapp/index.html)
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
import random
import threading
import time
from urllib.parse import parse_qsl

import requests
import telebot
from flask import Flask, jsonify, request, Response, send_from_directory

import config
import db
import errors
import notifications
from config import (BOT_TOKEN, PAYMENT_INFO, ADMIN_IDS, SUPER_ADMIN_IDS, SUPPORT_IDS, CITY_ADMINS,
                   CONFIRM_MINUTES, is_admin, is_super_admin, admins_for_city, all_admin_ids,
                   CITIES)

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

# Колесо фортуны: секторы (монеты + вес вероятности). Малые призы — часто, крупные — редко.
WHEEL_SECTORS = [
    {"label": "100",  "coins": 100,  "weight": 26},
    {"label": "200",  "coins": 200,  "weight": 20},
    {"label": "300",  "coins": 300,  "weight": 16},
    {"label": "400",  "coins": 400,  "weight": 12},
    {"label": "500",  "coins": 500,  "weight": 9},
    {"label": "600",  "coins": 600,  "weight": 6},
    {"label": "700",  "coins": 700,  "weight": 4},
    {"label": "800",  "coins": 800,  "weight": 3},
    {"label": "900",  "coins": 900,  "weight": 2},
    {"label": "1000", "coins": 1000, "weight": 1},
    {"label": "SUPER","coins": 2000, "weight": 1},
]

app = Flask(__name__, static_folder="webapp", static_url_path="")

# DEV_MODE=1 — разрешить пользоваться из обычного браузера (без Telegram) для локальной проверки.
DEV_MODE = os.environ.get("DEV_MODE") == "1"
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
    if calc_hash != received_hash:
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
    """Возвращает пользователя, ТОЛЬКО если он админ. Иначе None (доступ запрещён)."""
    user = get_user(init_data)
    if not user or not user.get("id") or not is_admin(int(user["id"])):
        return None
    return user


def _admin_display(admin):
    return admin.get("username") or admin.get("first_name") or str(admin.get("id"))


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
        return jsonify({"ok": True, "pending": False, "result": db.execute_admin_request(action, payload)})
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
            db.execute_admin_request(req["action"], json.loads(req["payload"]))
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
    "/api/admin/product": (), "/api/admin/product/update": (),
    "/api/admin/product/variants": (), "/api/admin/product/delete": (),
    "/api/admin/photo": (), "/api/admin/photo/add": (), "/api/admin/photo/delete": (),
    # Оценка живёт в карточке товара, поэтому её публикация обновляет витрину.
    "/api/admin/review/decide": (),
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
    return resp


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


_INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "index.html")


@app.route("/")
def index():
    # Читаем файл в обычный Response (не passthrough), чтобы сработало gzip-сжатие.
    with open(_INDEX_PATH, "rb") as f:
        html_bytes = f.read()
    resp = Response(html_bytes, content_type="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache"    # всегда свежая версия после деплоя
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
    age_ok = db.ensure_user_get_age(uid)      # создать + узнать 18+ за одно подключение

    # Реферал: friend открыл Mini App с параметром start_param=refN (дубль к боту).
    # Только ЗАПОМИНАЕМ пригласившего — монеты дадим, когда друг сделает заказ.
    start_param = str(data.get("start_param") or "")
    if start_param.startswith("ref"):
        try:
            ok_ref = db.set_referrer_once(uid, int(start_param[3:]))
            print(f"[ref/miniapp] uid={uid} start_param={start_param} set={ok_ref}")
        except (TypeError, ValueError):
            print(f"[ref/miniapp] uid={uid} плохой start_param={start_param}")

    try:
        alerts = db.alerts_of_user(uid)   # чтобы витрина показала «вы уже ждёте»
    except Exception as e:
        alerts = []
        print(f"Не удалось прочитать подписки покупателя {uid}: {e}")
    try:
        reminders_on = not db.get_no_reminders(uid)
    except Exception as e:
        reminders_on = True
        print(f"Не удалось прочитать настройку напоминаний {uid}: {e}")
    try:
        prefill = db.delivery_prefill(uid)   # адрес и телефон из прошлых заказов
    except Exception as e:
        prefill = {"phone": "", "addresses": {}}
        print(f"Не удалось собрать данные для подстановки {uid}: {e}")
    try:
        my_point = db.get_user_point(uid)
    except Exception:
        my_point = None
    return jsonify({"ok": True, "age_ok": age_ok, "is_admin": is_admin(uid),
                    "is_super": is_super_admin(uid), "alerts": alerts,
                    "reminders_on": reminders_on, "prefill": prefill,
                    "my_point": my_point})


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
        db.set_user_phone(uid, data.get("phone"))
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
    """Отзывы на модерации."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rows = db.pending_reviews()
    return jsonify({"ok": True, "reviews": [{
        "id": r["id"], "product_id": r["product_id"], "product": r["product_name"] or "товар удалён",
        "rating": r["rating"], "text": r["text"] or "", "who": _review_author(r),
        "created_at": r["created_at"] or "",
    } for r in rows]})


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


# ============================================================
#  ЛОКАЦИИ (точки продаж)
# ============================================================

@app.route("/api/locations")
def api_locations():
    cached = _cache_get("locations")
    if cached is None:
        cached = _cache_set("locations",
                            [{"id": r["id"], "name": r["name"]} for r in db.get_locations()], 300)
    return jsonify(cached)


@app.route("/api/categories")
def api_categories():
    """Категории товара — витрина строит по ним фильтры, админка формы."""
    cached = _cache_get("categories")
    if cached is None:
        cached = _cache_set("categories", [{"code": c["code"], "name": c["name"],
                                            "emoji": c["emoji"] or "", "sort": c["sort"]}
                                           for c in db.list_categories()], 300)
    return jsonify(cached)


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


@app.route("/api/admin/category", methods=["POST"])
def api_admin_category_add():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    code = db.add_category(name, data.get("emoji") or "")
    if not code:
        return jsonify({"ok": False, "error": "exists"}), 400
    return jsonify({"ok": True, "code": code})


@app.route("/api/admin/category/update", methods=["POST"])
def api_admin_category_update():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = (data.get("code") or "").strip()
    if code not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    sort = data.get("sort")
    db.update_category(code, name=data.get("name"), emoji=data.get("emoji"),
                       sort=(int(sort) if str(sort or "").strip().lstrip("-").isdigit() else None))
    return jsonify({"ok": True})


@app.route("/api/admin/category/delete", methods=["POST"])
def api_admin_category_delete():
    """Удалить можно только пустую категорию: иначе товары остались бы в разделе,
    которого нет, и пропали бы из витрины молча."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = (data.get("code") or "").strip()
    if code not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    used = db.count_products_in_category(code)
    if used:
        return jsonify({"ok": False, "error": "has_products", "count": used}), 400
    if len(db.category_codes()) <= 1:
        return jsonify({"ok": False, "error": "last_one"}), 400     # без категорий товар не завести
    db.delete_category(code)
    return jsonify({"ok": True})


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


@app.route("/api/delivery")
def api_delivery():
    """Способы получения для точки (для оформления заказа).

    Отдаём вместе с точками самовывоза этого города: покупатель выбирает нужную
    прямо в заказе, а не роется в настройках. Один запрос вместо двух — шторка
    оформления должна открываться мгновенно."""
    city = request.args.get("city", "")
    key = f"delivery:{city}"
    cached = _cache_get(key)
    if cached is None:
        cached = _cache_set(key, {
            "methods": [_delivery_json(m) for m in db.get_delivery_methods(city)],
            "points": [{"id": p["id"], "address": p["address"], "note": p["note"] or ""}
                       for p in db.get_pickup_points(city)],
            "free_from": _free_delivery_from(),   # с какой суммы доставка бесплатна
            "orders_done": _orders_done(),         # доверие: сколько заказов уже выдано
        }, 300)
    return jsonify(cached)


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
    gallery = {}
    try:
        for ph in db.all_product_photos():
            gallery.setdefault(ph["product_id"], []).append(ph)
    except Exception as e:
        print(f"Не удалось прочитать галерею товаров: {e}")   # без галереи витрина живёт
    out = []
    for p in db.get_all_products():
        # Главное фото всегда первое: покупатель видит ту же картинку, что и в каталоге.
        photos = ([{"id": 0, "url": f"/api/photo?file_id={p['photo']}",
                    "thumb": f"/api/photo?file_id={p['photo_thumb'] or p['photo']}"}] if p["photo"] else [])
        for ph in gallery.get(p["id"], []):
            photos.append({"id": ph["id"], "url": f"/api/photo?file_id={ph['file_id']}",
                           "thumb": f"/api/photo?file_id={ph['thumb_id'] or ph['file_id']}"})
        out.append({
            "photos": photos,
            "rating": ratings.get(p["id"], {"avg": 0, "count": 0}),
            "id": p["id"], "name": p["name"], "price": p["price"],
            "stock": p["stock"], "is_hit": p["is_hit"],
            "category": p["category"], "city": p["city"],
            "description": p["description"] or "",
            "cost": round(float(p["cost"] or 0), 2),   # видит только админка
            "brand": p["brand"] or "", "flavor": p["flavor"] or "",
            "strength": p["strength"] or "", "volume": p["volume"] or "",
            "variants": variants_by.get(p["id"], []),
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
    city = request.args.get("city")
    out = _all_products_payload()
    if city:
        out = [p for p in out if p["city"] == city]
    return jsonify(out)


@app.route("/api/photo")
def api_photo():
    file_id = request.args.get("file_id", "")
    if not file_id:
        return Response("no file_id", status=404)

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


@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401

    user_id = int(user["id"])
    username = user.get("username") or user.get("first_name") or str(user_id)

    # Разбираем корзину клиента (id + количество), чтобы одним запросом взять товары.
    raw_items = []
    for ri in data.get("items", []):
        try:
            pid, qty = int(ri.get("id")), int(ri.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if qty > 0:
            raw_items.append((pid, qty, (ri.get("flavor") or "").strip() or None))
    try:
        method_id = int(data.get("delivery_method_id"))
    except (TypeError, ValueError):
        method_id = None

    # ОДИН поход в базу за всем сразу: 18+, монеты, товары, вкусы, способ получения.
    ctx = db.get_checkout_data(user_id, [pid for pid, _, _ in raw_items], method_id)
    if not ctx["age_ok"]:
        return jsonify({"ok": False, "error": "age"}), 403

    # Цены и наличие берём из БАЗЫ, а не из того, что прислал клиент.
    items, total, cities = [], 0.0, set()
    for pid, qty, flavor in raw_items:
        p = ctx["products"].get(pid)
        if not p:
            continue
        if flavor:
            # товар-модель со вкусами: остаток берём у нужного варианта
            avail = ctx["variants"].get(pid, {}).get(flavor, 0)
            if avail <= 0:
                continue
            real_qty = min(qty, avail)
            name = f"{p['name']} — {flavor}"
        else:
            if p["stock"] <= 0:
                continue
            real_qty = min(qty, p["stock"])
            name = p["name"]
        # Закупочную цену ЗАПОМИНАЕМ в заказе, а не смотрим потом в товаре:
        # завтра поставщик поднимет цену, и прибыль по прошлым продажам поедет.
        items.append({"id": pid, "flavor": flavor, "name": name, "price": p["price"],
                      "cost": round(float(p["cost"] or 0), 2), "qty": real_qty})
        total += p["price"] * real_qty
        cities.add(p["city"])

    if not items:
        return jsonify({"ok": False, "error": "empty"}), 400
    if len(cities) > 1:
        return jsonify({"ok": False, "error": "multi_city"}), 400

    city = cities.pop()
    subtotal = round(total, 2)

    # 1. Способ получения (доставка/самовывоз) — метод точки, взят вместе с товарами.
    method = ctx["method"]
    if not method or method["city"] != city:
        return jsonify({"ok": False, "error": "bad_delivery"}), 400
    address = (data.get("delivery_address") or "").strip()
    if method["needs_address"] and not address:
        return jsonify({"ok": False, "error": "no_address"}), 400
    # Точку самовывоза сверяем со списком города, а не берём на слово: иначе в
    # заказ попадёт любой текст, и продавец поедет по несуществующему адресу.
    # Условие простое: у способа не спрашивают адрес, а у города есть точки —
    # значит покупатель обязан выбрать одну из них. Отдельного переключателя
    # нет намеренно: он бы означал «функция есть, но её надо найти».
    points = ctx["points"] if not method["needs_address"] else []
    if points:
        try:
            point_id = int(data.get("pickup_point_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "no_point"}), 400
        point = next((p for p in points if p["id"] == point_id), None)
        if not point:
            return jsonify({"ok": False, "error": "bad_point"}), 400
        address = point["address"]
    fee = round(method["fee"] or 0, 2)
    # Порог бесплатной доставки считаем ЗДЕСЬ, а не верим клиенту: иначе сумму
    # доставки можно обнулить подделанным запросом. Смотрим на стоимость
    # товаров ДО скидки монетами — иначе покупатель дотягивается до порога
    # своими же монетами, а платим за доставку мы.
    free_from = _free_delivery_from()
    if fee and free_from and subtotal >= free_from:
        fee = 0.0

    # 2. Способ оплаты. Если способу оплата не нужна (такси) — payment = none.
    if method["needs_payment"]:
        payment = data.get("payment_method")
        if payment not in ("card", "cash"):
            return jsonify({"ok": False, "error": "bad_payment"}), 400
    else:
        payment = "none"

    # 3. Сколько монет пробуем списать: 1 монета = COIN_VALUE Br, но не больше суммы товаров.
    #    round() убирает float-погрешность (25/0.01 = 2499.999…). Само списание — внутри
    #    транзакции place_order (атомарно, защищает от гонки и двойного клика).
    # 3а. Промокод. Скидку считает сервер — присланную сумму принимать нельзя.
    promo_code = (data.get("promo_code") or "").strip().upper()
    promo_discount = 0.0
    if promo_code:
        promo_discount, promo_err = db.check_promo(promo_code, user_id, subtotal)
        if promo_err:
            return jsonify({"ok": False, "error": promo_err}), 400

    spend = 0
    if data.get("use_coins") and subtotal > 0:
        # Монетами добираем ТО, ЧТО ОСТАЛОСЬ после промокода: иначе две скидки
        # вместе перекрывают стоимость товаров, и монеты сгорают впустую.
        left = max(0.0, subtotal - promo_discount)
        spend = min(ctx["coins"], int(round(left / COIN_VALUE)))

    # Карта → клиент грузит чек (статус 'new'). Наличные/такси → сразу продавцу,
    # но статус 'paid' = ЖДЁТ подтверждения продавца, а НЕ авто-подтверждается.
    needs_receipt = (payment == "card")

    # Заказ, монеты и склад — одной транзакцией (один commit вместо десятка).
    order_id, coins_used, total = db.place_order(
        user_id, username, city, items, subtotal, fee, COIN_VALUE, spend,
        method["name"], address, payment,
        (data.get("comment") or "").strip(), (data.get("phone") or "").strip(),
        "new" if needs_receipt else "paid",
        promo_code, promo_discount)
    discount = round(coins_used * COIN_VALUE, 2)
    if promo_code and promo_discount:
        db.consume_promo(promo_code)      # одно использование потрачено

    if not needs_receipt:
        # уведомления (продавцам + клиенту) — в фоне, чтобы «Оформить» отвечал сразу
        _bg(_notify_new_order, order_id, user_id)

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "total": total,
        "subtotal": subtotal,
        "fee": fee,
        "coins_used": coins_used,
        "discount": discount,
        "delivery_method": method["name"],
        "delivery_address": address,
        "payment_method": payment,
        "needs_receipt": needs_receipt,
        "payment_info": _payment_info(),
        "confirm_minutes": _confirm_minutes(),
    })


@app.route("/api/receipt", methods=["POST"])
def api_receipt():
    """Принимает фото чека (файлом), подтверждает клиенту, шлёт заказ продавцу города."""
    init_data = request.form.get("initData", "")
    user = get_user(init_data)
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    user_id = int(user["id"])

    try:
        order_id = int(request.form.get("order_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_order"}), 400

    order = db.get_order(order_id)
    if not order or order["user_id"] != user_id:
        return jsonify({"ok": False, "error": "not_found"}), 404

    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    photo_bytes = file.read()

    # Отправляем чек самому клиенту (подтверждение) — заодно получаем file_id,
    # который переиспользуем при отправке продавцу.
    try:
        msg = tg.send_photo(
            user_id, photo_bytes,
            caption=(f"🧾 Чек по заказу #{order_id} получен.\n"
                     f"Продавец подтвердит обычно за ~{_confirm_minutes()} минут."),
        )
        file_id = msg.photo[-1].file_id
    except Exception as e:
        print(f"Не смог отправить чек клиенту {user_id}: {e}")
        file_id = None

    if file_id:
        db.set_order_receipt(order_id, file_id)     # статус -> paid, чек сохранён
        _bg(notifications.notify_sellers, tg, order_id)  # продавцам — в фоне, не тормозим ответ
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "send_failed"}), 500


@app.route("/api/order/cancel", methods=["POST"])
def api_order_cancel():
    """Клиент отменяет свой заказ ДО подтверждения продавцом (статус new/paid)."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    try:
        oid = int(data.get("order_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    order = db.get_order(oid)
    if not order or order["user_id"] != int(user["id"]):
        return jsonify({"ok": False, "error": "not_found"}), 404
    if not db.cancel_order(oid, ["new", "paid"]):   # после подтверждения — только через продавца
        return jsonify({"ok": False, "error": "too_late"}), 400
    # сообщим продавцам города, чтобы не обрабатывали
    try:
        for admin_id in admins_for_city(order["city"]):
            tg.send_message(admin_id, f"❌ Клиент отменил заказ #{oid}.")
    except Exception as e:
        print(f"Не смог уведомить об отмене #{oid}: {e}")
    return jsonify({"ok": True})


SUPPORT_COOLDOWN = 20          # антиспам: не чаще 1 сообщения в поддержку за столько секунд
_support_last = {}             # uid -> время последнего сообщения (в памяти процесса)


@app.route("/api/support", methods=["POST"])
def api_support():
    """Клиент пишет в поддержку — доставляем сообщение менеджеру(ам) через бота."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    text = (data.get("text") or "").strip()[:2000]
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
    text = (data.get("text") or "").strip()[:2000]
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    contact = _contact_link(admin.get("username"), int(admin["id"]), "написать менеджеру")
    msg = (f"💬 Сообщение от магазина:\n{html.escape(text)}\n\n"
           f"По любым вопросам: {contact}")
    try:
        tg.send_message(target, msg, parse_mode="HTML")
        return jsonify({"ok": True, "sent": True})
    except Exception as e:
        print(f"Не смог отправить сообщение клиенту {target}: {e}")
        return jsonify({"ok": True, "sent": False})     # клиент мог не запускать бота


@app.route("/api/orders", methods=["POST"])
def api_my_orders():
    """История заказов текущего клиента (для вкладки Профиль)."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    orders = [_order_json(o) for o in db.get_orders_by_user(int(user["id"]))]
    return jsonify({"ok": True, "orders": orders})


@app.route("/api/bonus", methods=["POST"])
def api_bonus():
    """Бонусы клиента: баланс vapecoins, число приглашённых, реферальная ссылка."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}" if BOT_USERNAME else ""

    st = db.get_bonus_stats(uid)              # всё за одно подключение
    active = st["active"]
    percent = db.ref_percent(active)
    next_need, next_pct = None, None
    for m, p in sorted(db.REFERRAL_TIERS):    # ближайший тир выше текущего
        if m > active:
            next_need, next_pct = m - active, p
            break

    return jsonify({"ok": True,
                    "coins": st["coins"],
                    "referrals": st["referrals"],
                    "active_referrals": active,
                    "ref_earned": st["ref_earned"],
                    "ref_percent": percent,
                    "next_need": next_need,
                    "next_percent": next_pct,
                    "referrals_list": st["referrals_list"],
                    "ref_link": link,
                    "referral_bonus": db.REFERRAL_BONUS,
                    "coin_value": COIN_VALUE})


@app.route("/api/wheel", methods=["POST"])
def api_wheel():
    """Состояние колеса: секторы, доступные прокруты, прогресс."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    w = db.get_wheel(uid)
    return jsonify({"ok": True,
                    "sectors": [{"label": s["label"], "coins": s["coins"]} for s in WHEEL_SECTORS],
                    "spins": w["spins"], "progress": w["progress"], "step": w["step"]})


@app.route("/api/admin/wheel/grant", methods=["POST"])
def api_admin_wheel_grant():
    """Тест: начислить админу 3 прокрута колеса."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    uid = int(admin["id"])
    return _gate(admin, "wheel_grant_self", {"user_id": uid, "spins": 3},
                 f"+3 прокрута колеса админу id {uid}")


@app.route("/api/admin/grant", methods=["POST"])
def api_admin_grant():
    """Начислить пользователю монеты и/или прокруты колеса (по id). Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    coins = spins = 0
    try:
        coins = int(data.get("coins") or 0)
    except (TypeError, ValueError):
        coins = 0
    try:
        spins = int(data.get("spins") or 0)
    except (TypeError, ValueError):
        spins = 0
    parts = []
    if coins:
        parts.append(f"{'убрать' if coins < 0 else 'начислить'} {abs(coins)} 🪙")
    if spins:
        parts.append(f"{'убрать' if spins < 0 else 'начислить'} {abs(spins)} прокрутов")
    summary = f"Пользователю id {target}: " + (", ".join(parts) if parts else "—")
    return _gate(admin, "grant", {"user_id": target, "coins": coins, "spins": spins}, summary)


@app.route("/api/admin/referrals", methods=["POST"])
def api_admin_referrals():
    """Список рефералов текущего админа (для управления/отвязки)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rows = db.list_referrals(int(admin["id"]))
    return jsonify({"ok": True, "referrals": [{"id": r["user_id"], "active": bool(r["ref_activated"])} for r in rows]})


@app.route("/api/admin/coins/adjust", methods=["POST"])
def api_admin_coins_adjust():
    """Изменить баланс монет пользователя на delta (±). Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
        delta = int(data.get("delta"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_input"}), 400
    if is_super_admin(target) and not is_super_admin(int(admin["id"])):
        return jsonify({"ok": False, "error": "protected"}), 403     # монеты супер-админа не трогаем
    summary = (f"Убрать {abs(delta)} 🪙 у id {target}" if delta < 0 else f"Начислить {delta} 🪙 id {target}")
    return _gate(admin, "coins_adjust", {"user_id": target, "delta": delta}, summary)


@app.route("/api/admin/users", methods=["POST"])
def api_admin_users():
    """Список всех пользователей (поиск по id) — для админа (просмотр)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    users, total = db.list_users(str(data.get("search") or ""))
    for u in users:
        u["super"] = is_super_admin(u["id"])       # супер-админа фронт пометит и скроет кнопки
    return jsonify({"ok": True, "users": users, "total": total, "shown": len(users)})


@app.route("/api/admin/customer", methods=["POST"])
def api_admin_customer():
    """Карточка покупателя: история заказов, суммы, любимые товары."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    card = db.customer_card(target)
    if not card:
        return jsonify({"ok": False, "error": "not_found"}), 404
    card["super"] = is_super_admin(target)
    return jsonify({"ok": True, "card": card})


@app.route("/api/admin/referral/unlink", methods=["POST"])
def api_admin_referral_unlink():
    """Отвязать конкретного реферала по его id. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if is_super_admin(target):
        return jsonify({"ok": False, "error": "protected"}), 403     # супер-админа не трогаем
    return _gate(admin, "referral_unlink", {"user_id": target}, f"Отвязать реферала id {target}")


@app.route("/api/admin/referral/clear", methods=["POST"])
def api_admin_referral_clear():
    """Отвязать ВСЕХ рефералов текущего админа. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    uid = int(admin["id"])
    return _gate(admin, "referral_clear", {"requester_id": uid}, f"Отвязать ВСЕХ рефералов админа id {uid}")


@app.route("/api/admin/user/delete", methods=["POST"])
def api_admin_user_delete():
    """Полностью удалить пользователя по id. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if target == int(admin["id"]):
        return jsonify({"ok": False, "error": "self"}), 400     # себя не удаляем
    if is_super_admin(target):
        return jsonify({"ok": False, "error": "protected"}), 403     # супер-админа удалить нельзя
    return _gate(admin, "user_delete", {"user_id": target}, f"Удалить пользователя id {target}")


@app.route("/api/wheel/spin", methods=["POST"])
def api_wheel_spin():
    """Прокрут колеса: списываем прокрут, выбираем приз по весам, начисляем монеты."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    weights = [s["weight"] for s in WHEEL_SECTORS]
    idx = random.choices(range(len(WHEEL_SECTORS)), weights=weights, k=1)[0]
    prize = WHEEL_SECTORS[idx]
    res = db.do_wheel_spin(uid, prize["coins"])     # списание+начисление за 1 запрос
    if res is None:
        return jsonify({"ok": False, "error": "no_spins"}), 400
    coins, spins = res
    db.inc_stat("wheel_spins", 1); db.inc_stat("wheel_paid", prize["coins"])
    return jsonify({"ok": True, "index": idx, "coins": prize["coins"], "label": prize["label"],
                    "balance": coins, "spins": spins})


# ------------------- Слот «Облако Монет» -------------------

# Ставка регулируется игроком: 2..10 с шагом 2. Приз = ставка × множитель символа.
SLOT_BETS = [2, 4, 6, 8, 10]
SLOT_MIN_BET = SLOT_BETS[0]
# Экономика: суммарный шанс выигрыша = 30%, средневзвеш. множитель ≈ 2.67 → RTP ≈ 79% (house edge ~21%).
# Множители целые, а ставки чётные → приз всегда целый. Символы от частых/мелких к редким/крупным.
SLOT_SYMBOLS = [
    {"key": "cart",   "emoji": "🔋", "label": "Картридж",  "mult": 2,  "percent": 9},
    {"key": "cig",    "emoji": "🚬", "label": "Сигарета",  "mult": 2,  "percent": 7},
    {"key": "snus",   "emoji": "🟤", "label": "Снюс",      "mult": 2,  "percent": 5},
    {"key": "liquid", "emoji": "🧪", "label": "Жижа",      "mult": 3,  "percent": 4},
    {"key": "disp",   "emoji": "💨", "label": "Одноразка", "mult": 3,  "percent": 2.5},
    {"key": "pod",    "emoji": "📦", "label": "Под",       "mult": 5,  "percent": 1.5},
    {"key": "coin",   "emoji": "🪙", "label": "Монетка",   "mult": 8,  "percent": 0.7},
    {"key": "crown",  "emoji": "👑", "label": "Корона",    "mult": 15, "percent": 0.3},
]


# Линии слота (клетки [row, col]): 3 ряда, 2 диагонали и 2 зигзага (галочка ∨ / крышка ∧).
SLOT_LINES = [
    [[0, 0], [0, 1], [0, 2]],   # верхний ряд
    [[1, 0], [1, 1], [1, 2]],   # центральный ряд
    [[2, 0], [2, 1], [2, 2]],   # нижний ряд
    [[0, 0], [1, 1], [2, 2]],   # диагональ ↘
    [[0, 2], [1, 1], [2, 0]],   # диагональ ↙
    [[0, 0], [1, 1], [0, 2]],   # галочка ∨ (верх-слева → центр → верх-справа)
    [[2, 0], [1, 1], [2, 2]],   # крышка ∧ (низ-слева → центр → низ-справа)
]
SLOT_LINE_NAMES = ["Верхний ряд", "Центр", "Нижний ряд", "Диагональ ↘", "Диагональ ↙", "Галочка ∨", "Крышка ∧"]


def _line_vals(grid, line):
    return [grid[r][c] for r, c in line]


def _slot_grid(win_emoji, line_idx):
    """Строит 3×3. Если win_emoji задан — выкладывает его по линии line_idx и ломает
    любые ДРУГИЕ случайно совпавшие линии. При проигрыше — гарантирует, что НИ ОДНА
    линия не совпала (чтобы не показать неоплаченный «выигрыш»)."""
    emojis = [s["emoji"] for s in SLOT_SYMBOLS]
    grid = [[random.choice(emojis) for _ in range(3)] for _ in range(3)]
    if win_emoji is not None:
        for r, c in SLOT_LINES[line_idx]:
            grid[r][c] = win_emoji
        target = {(r, c) for r, c in SLOT_LINES[line_idx]}
        for _ in range(50):     # убрать ЧУЖИЕ совпадения, не трогая целевую линию
            bad = [i for i, ln in enumerate(SLOT_LINES)
                   if i != line_idx and len(set(_line_vals(grid, ln))) == 1]
            if not bad:
                break
            free = [(r, c) for r, c in SLOT_LINES[bad[0]] if (r, c) not in target]
            if not free:
                break
            r, c = random.choice(free)
            cur = grid[r][c]
            grid[r][c] = random.choice([e for e in emojis if e != cur] or emojis)
        return grid
    # проигрыш — ломаем любые случайно совпавшие линии
    for _ in range(50):
        bad = [i for i, ln in enumerate(SLOT_LINES) if len(set(_line_vals(grid, ln))) == 1]
        if not bad:
            break
        r, c = SLOT_LINES[bad[0]][random.randint(0, 2)]
        cur = grid[r][c]
        grid[r][c] = random.choice([e for e in emojis if e != cur] or emojis)
    return grid


@app.route("/api/slot", methods=["POST"])
def api_slot():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    return jsonify({"ok": True, "bets": SLOT_BETS, "balance": db.get_coins(uid),
                    "symbols": [{"emoji": s["emoji"], "label": s["label"], "mult": s["mult"]} for s in SLOT_SYMBOLS],
                    "lines": [{"name": n, "cells": ln} for n, ln in zip(SLOT_LINE_NAMES, SLOT_LINES)]})


@app.route("/api/slot/spin", methods=["POST"])
def api_slot_spin():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    try:
        bet = int(data.get("bet", SLOT_MIN_BET))
    except (TypeError, ValueError):
        bet = SLOT_MIN_BET
    if bet not in SLOT_BETS:
        return jsonify({"ok": False, "error": "bad_bet"}), 400
    roll = random.random() * 100.0
    cum, win = 0.0, None
    for s in SLOT_SYMBOLS:
        cum += s["percent"]
        if roll < cum:
            win = s
            break
    prize_coins = bet * win["mult"] if win else 0     # приз = ставка × множитель
    balance = db.do_slot_spin(uid, bet, prize_coins)   # списание+приз за 1 запрос
    if balance is None:
        return jsonify({"ok": False, "error": "no_coins"}), 400
    db.inc_stat("slot_spins", 1); db.inc_stat("slot_bet", bet); db.inc_stat("slot_paid", prize_coins)

    # Сетка 3×3. Выигрыш выкладывается по случайной линии (ряд или диагональ).
    if win:
        line_idx = random.randint(0, len(SLOT_LINES) - 1)
        grid = _slot_grid(win["emoji"], line_idx)
        win_cells = SLOT_LINES[line_idx]
    else:
        grid = _slot_grid(None, 0)
        win_cells = []
    return jsonify({"ok": True, "win": bool(win), "grid": grid, "win_cells": win_cells,
                    "coins": prize_coins, "label": win["label"] if win else "",
                    "bet": bet, "balance": balance})


# ------------------- Розыгрыши (раз в месяц) -------------------

def _draw_raffle(raffle):
    """Выбирает победителей 1-3 мест, начисляет монеты за 3 место, уведомляет, завершает."""
    uids = db.get_raffle_user_ids(raffle["id"])
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
            db.add_coins(wid, coins)
        _notify_client(wid, f"🏆 Вы заняли {place} место в розыгрыше! Приз: {prize}. "
                            + ("Монеты начислены." if coins else "Продавец свяжется с вами."))
    db.finish_raffle(raffle["id"], winners)


def _ensure_raffle():
    """Ленивый планировщик: создаёт розыгрыш, если нет; разыгрывает и запускает новый, если срок вышел."""
    r = db.get_active_raffle()
    if not r:
        db.create_raffle()
        return
    if r["ends_at"] and db._now_str() >= r["ends_at"]:
        _draw_raffle(r)
        db.create_raffle()


def _raffle_public_from_state(st):
    r = st["raffle"]
    threshold = round(r["threshold"] or 0, 2)
    spent = round(st["spent"], 2)
    try:
        last_winners = json.loads(st["last_winners_raw"]) if st["last_winners_raw"] else []
    except (TypeError, ValueError):
        last_winners = []
    return {
        "id": r["id"], "title": r["title"] or "Розыгрыш месяца",
        "prize1": r["prize1"] or "", "prize2": r["prize2"] or "", "prize3_coins": r["prize3_coins"],
        "ends_at": r["ends_at"], "threshold": threshold,
        "participants": st["participants"],
        "spent": spent, "remaining": round(max(0, threshold - spent), 2),
        "eligible": spent >= threshold, "entered": st["entered"],
        "last_winners": last_winners,
    }


@app.route("/api/raffle", methods=["POST"])
def api_raffle():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    _ensure_raffle()
    st = db.get_raffle_state(uid)      # всё за одно подключение
    if not st:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    return jsonify({"ok": True, "raffle": _raffle_public_from_state(st)})


@app.route("/api/raffle/join", methods=["POST"])
def api_raffle_join():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    _ensure_raffle()
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    if db.is_entered(r["id"], uid):
        return jsonify({"ok": True, "entered": True})
    if db.spent_since(uid, r["starts_at"]) < (r["threshold"] or 0):
        return jsonify({"ok": False, "error": "not_eligible"}), 400
    db.add_raffle_entry(r["id"], uid)
    return jsonify({"ok": True, "entered": True})


# ============================================================
#  АДМИН-API (только для тех, кто в ADMIN_IDS)
# ============================================================

@app.route("/api/admin/product", methods=["POST"])
def api_admin_add():
    """Добавить товар из приложения."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    city = data.get("city")
    category = data.get("category")
    name = (data.get("name") or "").strip()
    if city not in db.location_names() or category not in db.category_codes() or not name:
        return jsonify({"ok": False, "error": "bad_data"}), 400
    try:
        price = float(str(data.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400
    try:
        # Закупочная цена необязательна: пустое поле = «не знаю», и прибыль по
        # такому товару честно считается неизвестной, а не нулевой.
        cost = max(0.0, float(str(data.get("cost") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        cost = 0.0

    is_hit = 1 if data.get("is_hit") else 0
    desc = (data.get("description") or "").strip()
    brand = (data.get("brand") or "").strip()
    strength = (data.get("strength") or "").strip()

    # Товар-модель со вкусами (одноразки/жидкости): список variants + свои поля.
    # Объём: у одноразок приходит как puffs (затяжки), у жидкостей как volume (мл).
    variants = data.get("variants")
    if isinstance(variants, list) and variants:
        vol = str(data.get("puffs") or data.get("volume") or "").strip()
        pid = db.add_product(city, category, name, max(0.0, price), 0, is_hit, desc,
                             brand=brand, flavor="", strength=strength, volume=vol, cost=cost)
        for v in variants:
            fl = str(v.get("flavor", "")).strip()
            try:
                st = int(v.get("stock", 0))
            except (TypeError, ValueError):
                st = 0
            if fl:
                db.add_variant(pid, fl, max(0, st))
        db.recalc_product_stock(pid)
        return jsonify({"ok": True, "id": pid})

    # Обычный товар (одно количество, без вкусов).
    try:
        stock = int(data.get("stock"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400
    flavor = (data.get("flavor") or "").strip()
    volume = (data.get("volume") or "").strip()
    pid = db.add_product(city, category, name, max(0.0, price), max(0, stock), is_hit, desc,
                         brand=brand, flavor=flavor, strength=strength, volume=volume, cost=cost)
    return jsonify({"ok": True, "id": pid})


@app.route("/api/admin/product/update", methods=["POST"])
def api_admin_update():
    """Изменить одно поле товара (price / stock / name / description / is_hit)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400

    field = data.get("field")
    raw = data.get("value")
    try:
        if field in ("price", "cost"):
            value = max(0.0, float(str(raw or 0).replace(",", ".")))
        elif field == "stock":
            value = max(0, int(raw))
        elif field in ("name", "description", "brand", "flavor", "strength", "volume"):
            value = str(raw).strip()
        elif field == "category":
            value = str(raw).strip()
            if value not in db.category_codes():
                return jsonify({"ok": False, "error": "bad_value"}), 400
        elif field == "city":
            value = str(raw).strip()
            names = {loc["name"] for loc in db.get_locations()}
            if value not in names:
                return jsonify({"ok": False, "error": "bad_value"}), 400
        elif field == "is_hit":
            value = 1 if raw else 0
        else:
            return jsonify({"ok": False, "error": "bad_field"}), 400
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_value"}), 400

    db.update_field(pid, field, value)
    return jsonify({"ok": True})


@app.route("/api/admin/product/variants", methods=["POST"])
def api_admin_variants():
    """Заменяет список вкусов товара целиком (добавить/убрать/изменить остаток)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400

    db.delete_variants(pid)
    for v in (data.get("variants") or []):
        fl = str(v.get("flavor", "")).strip()
        try:
            st = int(v.get("stock", 0))
        except (TypeError, ValueError):
            st = 0
        if fl:
            db.add_variant(pid, fl, max(0, st))
    db.recalc_product_stock(pid)
    return jsonify({"ok": True})


@app.route("/api/admin/product/delete", methods=["POST"])
def api_admin_delete():
    """Удалить товар."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_variants(pid)
    db.delete_product(pid)
    return jsonify({"ok": True})


@app.route("/api/admin/photo", methods=["POST"])
def api_admin_photo():
    """Загрузить фото товара. Отправляем картинку админу (тихо), чтобы получить file_id."""
    init_data = request.form.get("initData", "")
    user = get_admin(init_data)
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(request.form.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400

    try:
        msg = tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото товара сохранено", disable_notification=True)
        file_id, thumb_id = _pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото товара: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500

    db.update_field(pid, "photo", file_id)
    db.update_field(pid, "photo_thumb", thumb_id)
    return jsonify({"ok": True})


@app.route("/api/admin/photo/add", methods=["POST"])
def api_admin_photo_add():
    """Добавить фото в галерею товара (главное фото при этом не меняется)."""
    user = get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(request.form.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_product(pid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    if len(db.get_product_photos(pid)) >= db.MAX_EXTRA_PHOTOS:
        # Проверяем ДО отправки в Telegram: иначе картинка уедет впустую.
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    try:
        msg = tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото товара сохранено", disable_notification=True)
        file_id, thumb_id = _pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото товара: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500
    photo_id = db.add_product_photo(pid, file_id, thumb_id)
    if not photo_id:
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    return jsonify({"ok": True, "photo_id": photo_id})


@app.route("/api/admin/photo/delete", methods=["POST"])
def api_admin_photo_delete():
    """Убрать фото из галереи. Главное фото (id 0) так не удаляется — его заменяют."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        photo_id = int(data.get("photo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if photo_id <= 0:
        return jsonify({"ok": False, "error": "main_photo"}), 400
    return jsonify({"ok": True, "deleted": db.delete_product_photo(photo_id)})


# ------------------- Заказы (управление в приложении) -------------------

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


def _notify_new_order(order_id, user_id):
    """Побочные эффекты нового заказа: уведомить продавцов и клиента (вызывается в фоне)."""
    notifications.notify_sellers(tg, order_id)
    _notify_client(user_id, _client_order_summary(order_id))


def _client_order_summary(order_id):
    """Сводка заказа для клиента (подтверждение оформления в чате)."""
    o = db.get_order(order_id)
    if not o:
        return None
    try:
        items = json.loads(o["items"])
    except (TypeError, ValueError):
        items = []
    lines = [f"🧾 Заказ #{o['id']} принят!", ""]
    for it in items:
        lines.append(f"• {it['name']} × {it['qty']}")
    method = o["delivery_method"] or ""
    if method:
        addr = o["delivery_address"] or ""
        lines.append("")
        lines.append(f"🚚 {method}" + (f": {addr}" if addr else ""))
    pm = {"card": "💳 картой", "cash": "💵 наличными", "none": "🚕 при получении"}.get(o["payment_method"] or "", "")
    if pm:
        lines.append(f"Оплата: {pm}")
    lines.append(f"💰 Итого: {o['total']:.2f} Br")
    lines.append("")
    lines.append("Статус — в приложении: Профиль → Мои заказы. Уведомим об изменениях 🔔")
    return "\n".join(lines)


def _reward_referrer(buyer_id, order_total):
    """Начислить пригласившему % от заказа + бонус за первый заказ, уведомить его."""
    rr = db.reward_referrer_for_order(buyer_id, order_total)
    if rr and rr["earned"] > 0:
        extra = f" (+{rr['bonus']} 🪙 за первый заказ друга)" if rr["first"] else ""
        _notify_client(rr["referrer"], f"🎉 Ваш реферал сделал заказ! +{rr['earned']} 🪙{extra}")


def _order_item_count(o):
    """Сколько единиц товара в заказе (для прогресса колеса)."""
    try:
        return sum(int(it.get("qty", 0)) for it in json.loads(o["items"]))
    except (TypeError, ValueError):
        return 0


def _order_subtotal(o):
    """Стоимость ТОЛЬКО товаров (без доставки) — база для кэшбэка."""
    try:
        return sum(float(it.get("price", 0)) * int(it.get("qty", 0)) for it in json.loads(o["items"]))
    except (TypeError, ValueError):
        return float(o["total"] or 0)


def _order_json(o):
    try:
        items = json.loads(o["items"])
    except (TypeError, ValueError):
        items = []
    return {
        "id": o["id"],
        "user_id": o["user_id"],
        "username": o["username"] or "",
        "city": o["city"],
        "items": items,
        "total": o["total"],
        "pickup_time": o["pickup_time"] or "",
        "status": o["status"],
        "created_at": o["created_at"],
        "delivery_method": (o["delivery_method"] or ""),
        "delivery_address": (o["delivery_address"] or ""),
        "delivery_fee": round(o["delivery_fee"] or 0, 2),
        "payment_method": (o["payment_method"] or ""),
        "comment": (o["comment"] or "") if "comment" in o.keys() else "",
        "phone": (o["phone"] or "") if "phone" in o.keys() else "",
        "receipt_url": (f"/api/photo?file_id={o['receipt_file_id']}" if o["receipt_file_id"] else None),
    }


@app.route("/api/admin/orders", methods=["POST"])
def api_admin_orders():
    """Список всех заказов для админ-панели (новые сверху)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "orders": [_order_json(o) for o in db.get_orders()]})


@app.route("/api/admin/order/status", methods=["POST"])
def api_admin_order_status():
    """Продавец меняет статус заказа из приложения (confirm / issued / reject)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        oid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400

    order = db.get_order(oid)
    if not order:
        return jsonify({"ok": False, "error": "not_found"}), 404

    action = data.get("action")
    client_id = order["user_id"]
    OPEN = ["new", "paid", "confirmed"]        # состояния до выдачи/отмены (для отклонения)
    if action == "confirm":
        # подтвердить можно ТОЛЬКО оплаченный/готовый заказ (paid).
        # 'new' = карточный заказ без чека → сначала оплата, иначе нельзя.
        if not db.set_order_status_if(oid, "confirmed", ["paid"]):
            return jsonify({"ok": False, "error": "closed"}), 409
        msg = (f"✅ Оплата по заказу #{oid} подтверждена! Готовим к выдаче. Спасибо! 🌿"
               if order["payment_method"] == "card"
               else f"✅ Заказ #{oid} подтверждён! Готовим к выдаче. Спасибо! 🌿")
        _bg(_notify_client, client_id, msg)
    elif action == "issued":
        # выдать можно только оплаченный (paid) или уже подтверждённый (confirmed) заказ,
        # но НЕ 'new' (неоплаченный картой) — иначе кэшбэк без оплаты.
        if not db.set_order_status_if(oid, "issued", ["paid", "confirmed"]):   # применится один раз
            return jsonify({"ok": False, "error": "closed"}), 409
        db.add_coins(client_id, int(_order_subtotal(order)) * COINS_PER_BYN)   # кэшбэк с товаров (без доставки)
        db.add_wheel_progress(client_id, _order_item_count(order))   # прогресс колеса
        _reward_referrer(client_id, order["total"])   # % и бонус пригласившему
        _bg(_notify_client, client_id, f"Заказ #{oid} выдан. Спасибо, что выбрали нас! 🙌")
    elif action == "reject":
        if not db.cancel_order(oid, OPEN):          # атомарно: canceled + возврат склада/монет
            return jsonify({"ok": False, "error": "closed"}), 409
        _bg(_notify_client, client_id, f"К сожалению, заказ #{oid} отклонён продавцом. "
                                       "Если это ошибка — напишите нам, разберёмся.")
    else:
        return jsonify({"ok": False, "error": "bad_action"}), 400
    return jsonify({"ok": True})


# ------------------- Статистика -------------------

PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "all": None}


@app.route("/api/admin/stats", methods=["POST"])
def api_admin_stats():
    """Бизнес-аналитика для админа за период: KPI, графики, товары, юзеры, монеты, склад, игры."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    period = data.get("period", "30d")
    # Тяжёлый расчёт (~15 запросов) — кэшируем на 60с. Сбрасывается при изменении заказов
    # (через _WRITE_PATHS), так что цифры остаются актуальными после реальных продаж.
    cached = _cache_get(f"stats:{period}")
    if cached is not None:
        return jsonify({"ok": True, "stats": cached})

    days = PERIOD_DAYS.get(period, 30)
    stats = db.get_business_stats(days)          # всё считается в SQL

    products = db.get_all_products()             # склад — не зависит от периода
    stats["low_stock"] = [{"name": p["name"], "city": p["city"], "stock": p["stock"]}
                          for p in products if 0 < p["stock"] <= 3][:12]
    stats["out_stock"] = [{"name": p["name"], "city": p["city"]}
                          for p in products if p["stock"] <= 0][:12]
    stats["out_of_stock"] = sum(1 for p in products if p["stock"] <= 0)
    stats["products_total"] = len(products)
    stats["games"] = db.get_game_stats()
    stats["period"] = period
    try:
        stats["losses"] = db.stock_losses(days)      # во сколько обошлись списания
    except Exception as e:
        stats["losses"] = []
        print(f"Не удалось посчитать списания: {e}")
    _cache_set(f"stats:{period}", stats, 60)
    return jsonify({"ok": True, "stats": stats})


@app.route("/api/admin/stats/reset", methods=["POST"])
def api_admin_stats_reset():
    """Сброс тестовой статистики (заказы + счётчики игр) — только супер-админ."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    res = db.reset_statistics()
    return jsonify({"ok": True, **res})


# ------------------- Движение склада -------------------

@app.route("/api/admin/stock/move", methods=["POST"])
def api_admin_stock_move():
    """Приход или списание с причиной. Остаток меняется только так — тогда на
    любой вопрос «куда делось» есть ответ с именем и датой."""
    data = request.get_json(force=True, silent=True) or {}
    admin = get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
        qty = int(data.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400
    if qty <= 0:
        return jsonify({"ok": False, "error": "bad_number"}), 400
    reason = data.get("reason")
    if reason not in db.STOCK_REASONS:
        return jsonify({"ok": False, "error": "bad_reason"}), 400
    if not db.get_product(pid):
        return jsonify({"ok": False, "error": "not_found"}), 404

    # Приход прибавляет, всё остальное списывает. Знак задаёт причина, а не
    # клиент: иначе «списание» могло бы прийти с плюсом.
    delta = qty if reason == "in" else -qty
    try:
        cost = max(0.0, float(str(data.get("cost") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        cost = 0.0
    flavor = (data.get("flavor") or "").strip() or None
    stock = db.move_stock(pid, delta, reason, flavor=flavor, cost=cost,
                          note=data.get("note"), admin_id=int(admin["id"]))
    return jsonify({"ok": True, "stock": stock})


@app.route("/api/admin/stock/moves", methods=["POST"])
def api_admin_stock_moves():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id")) if data.get("id") else None
    except (TypeError, ValueError):
        pid = None
    moves = db.get_stock_moves(pid, 60)
    return jsonify({"ok": True, "moves": moves, "reasons": db.STOCK_REASONS})


# ------------------- Промокоды (админ) -------------------

@app.route("/api/admin/promos", methods=["POST"])
def api_admin_promos():
    """Коды со статистикой: сколько заказов и выручки принёс каждый."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "promos": db.list_promos()})


@app.route("/api/admin/promo", methods=["POST"])
def api_admin_promo_add():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = (data.get("code") or "").strip().upper()
    if not code or len(code) > 24 or " " in code:
        return jsonify({"ok": False, "error": "bad_code"}), 400
    kind = "fixed" if data.get("kind") == "fixed" else "percent"
    try:
        value = float(str(data.get("value") or 0).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_value"}), 400
    if value <= 0 or (kind == "percent" and value > 100):
        return jsonify({"ok": False, "error": "bad_value"}), 400
    try:
        min_total = max(0.0, float(str(data.get("min_total") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        min_total = 0.0
    uses = data.get("uses_left")
    try:
        uses_left = int(uses) if str(uses or "").strip() else None   # пусто = без ограничения
    except (TypeError, ValueError):
        uses_left = None
    if db._promo_row(code):
        return jsonify({"ok": False, "error": "exists"}), 400
    db.add_promo(code, kind, value, min_total, uses_left, bool(data.get("once_per_user", True)))
    return jsonify({"ok": True})


@app.route("/api/admin/promo/toggle", methods=["POST"])
def api_admin_promo_toggle():
    """Выключить код, не удаляя: статистика по нему должна остаться."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    db.set_promo_active((data.get("code") or ""), bool(data.get("active")))
    return jsonify({"ok": True})


@app.route("/api/admin/promo/delete", methods=["POST"])
def api_admin_promo_delete():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    db.delete_promo((data.get("code") or ""))
    return jsonify({"ok": True})


# ------------------- Точки самовывоза -------------------

@app.route("/api/admin/point", methods=["POST"])
def api_admin_point_add():
    """Добавить точку самовывоза городу."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    city = (data.get("city") or "").strip()
    address = (data.get("address") or "").strip()
    if not city or not address:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    db.add_pickup_point(city, address, (data.get("note") or "").strip()[:80],
                        int(data.get("sort") or 0))
    return jsonify({"ok": True})


@app.route("/api/admin/point/update", methods=["POST"])
def api_admin_point_update():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    db.update_pickup_point(pid, address, (data.get("note") or "").strip()[:80])
    return jsonify({"ok": True})


@app.route("/api/admin/point/delete", methods=["POST"])
def api_admin_point_delete():
    """Удаление точки не трогает прежние заказы: адрес в них сохранён строкой,
    поэтому продавец по-прежнему видит, куда человек приедет."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_pickup_point(pid)
    return jsonify({"ok": True})


# ------------------- Админы и продавцы (только супер-админ) -------------------

def _super(data):
    """Проверка «это супер-админ» — общая для всех операций с правами."""
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return None
    return user


@app.route("/api/admin/staff", methods=["POST"])
def api_admin_staff():
    """Список тех, у кого есть доступ. Все они живут в базе и убираются отсюда же
    — кроме владельца: его права держатся на настройках сервера, чтобы доступ к
    магазину нельзя было потерять ни по ошибке, ни злым умыслом."""
    data = request.get_json(force=True, silent=True) or {}
    if not _super(data):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    rows = []
    for r in db.list_staff():
        uid = int(r["user_id"])
        rows.append({"user_id": uid, "city": r["city"] or "", "note": r["note"] or "",
                     "can_remove": not is_super_admin(uid), "is_super": is_super_admin(uid)})

    # Владелец может не значиться в таблице — админом он всё равно остаётся,
    # и в списке должен быть виден, иначе непонятно, у кого ещё есть доступ.
    known = {r["user_id"] for r in rows}
    for uid in SUPER_ADMIN_IDS:
        if uid not in known:
            rows.append({"user_id": uid, "city": "", "note": "",
                         "can_remove": False, "is_super": True})

    return jsonify({"ok": True, "staff": rows, "cities": CITIES})


@app.route("/api/admin/staff/add", methods=["POST"])
def api_admin_staff_add():
    data = request.get_json(force=True, silent=True) or {}
    if not (su := _super(data)):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        uid = int(str(data.get("user_id", "")).strip())
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if uid <= 0:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    city = (data.get("city") or "").strip()
    if city and city not in CITIES and city not in {l["name"] for l in db.get_locations()}:
        return jsonify({"ok": False, "error": "bad_city"}), 400
    db.add_staff(uid, city, (data.get("note") or "").strip()[:64], int(su["id"]))
    config.refresh_staff()       # права должны действовать сразу, а не через полминуты
    _bg(_notify_new_admin, uid, city)
    return jsonify({"ok": True})


@app.route("/api/admin/staff/remove", methods=["POST"])
def api_admin_staff_remove():
    data = request.get_json(force=True, silent=True) or {}
    if not _super(data):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        uid = int(str(data.get("user_id", "")).strip())
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    # Супер-админа не трогаем ничем и никогда — это последний ключ от магазина.
    if is_super_admin(uid):
        return jsonify({"ok": False, "error": "super_protected"}), 400
    db.remove_staff(uid)
    config.refresh_staff()
    return jsonify({"ok": True})


def _notify_new_admin(uid, city):
    """Сообщаем человеку, что доступ выдан: иначе он не узнает, что теперь админ."""
    where = f" по точке «{city}»" if city else ""
    try:
        tg.send_message(uid, f"🛠 Вам выдали доступ продавца{where}.\n"
                             f"Откройте приложение — появится раздел «Управление».")
    except Exception as e:
        print(f"Не смог уведомить нового админа {uid}: {e}")


# ------------------- Розыгрыш (админ) -------------------

@app.route("/api/admin/raffle", methods=["POST"])
def api_admin_raffle():
    """Текущий розыгрыш для настройки."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _ensure_raffle()
    r = db.get_active_raffle()
    return jsonify({"ok": True, "raffle": {
        "id": r["id"], "title": r["title"] or "", "prize1": r["prize1"] or "", "prize2": r["prize2"] or "",
        "prize3_coins": r["prize3_coins"], "threshold": round(r["threshold"] or 0, 2),
        "ends_at": r["ends_at"], "participants": db.count_entries(r["id"]),
    }})


@app.route("/api/admin/raffle/update", methods=["POST"])
def api_admin_raffle_update():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _ensure_raffle()
    r = db.get_active_raffle()
    for field in ("title", "prize1", "prize2"):
        if field in data:
            db.update_raffle_field(r["id"], field, str(data[field]).strip())
    if "prize3_coins" in data:
        try:
            db.update_raffle_field(r["id"], "prize3_coins", max(0, int(data["prize3_coins"])))
        except (TypeError, ValueError):
            pass
    if "threshold" in data:
        try:
            db.update_raffle_field(r["id"], "threshold", max(0.0, float(str(data["threshold"]).replace(",", "."))))
        except (TypeError, ValueError):
            pass
    return jsonify({"ok": True})


@app.route("/api/admin/raffle/draw", methods=["POST"])
def api_admin_raffle_draw():
    """Разыграть текущий розыгрыш сейчас и запустить новый."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _ensure_raffle()
    r = db.get_active_raffle()
    _draw_raffle(r)
    db.create_raffle()
    return jsonify({"ok": True})


# ------------------- Настройки магазина -------------------

@app.route("/api/admin/settings", methods=["POST"])
def api_admin_settings():
    """Текущие настройки магазина для админ-панели."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "settings": {
        "payment_info": _payment_info(),
        "confirm_minutes": _confirm_minutes(),
        "free_delivery_from": db.get_setting("free_delivery_from", 0),
        "remind_after_days": db.get_setting("remind_after_days", 21),
        "remind_daily_cap": db.get_setting("remind_daily_cap", 20),
    }})


@app.route("/api/admin/settings/update", methods=["POST"])
def api_admin_settings_update():
    """Сохранить настройки магазина (реквизиты оплаты, время подтверждения)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if "payment_info" in data:
        db.set_setting("payment_info", str(data.get("payment_info") or "").strip())
    if "confirm_minutes" in data:
        try:
            db.set_setting("confirm_minutes", max(1, int(data.get("confirm_minutes"))))
        except (TypeError, ValueError):
            pass
    if "free_delivery_from" in data:
        try:
            # 0 — законное значение: порог выключен, доставка платная всегда.
            db.set_setting("free_delivery_from", max(0.0, float(str(data.get("free_delivery_from") or 0).replace(",", "."))))
        except (TypeError, ValueError):
            pass
    if "remind_after_days" in data:
        try:
            db.set_setting("remind_after_days", max(1, int(data.get("remind_after_days"))))
        except (TypeError, ValueError):
            pass
    if "remind_daily_cap" in data:
        try:
            # 0 — законное значение: так напоминания выключаются целиком.
            db.set_setting("remind_daily_cap", max(0, int(data.get("remind_daily_cap"))))
        except (TypeError, ValueError):
            pass
    return jsonify({"ok": True})


@app.route("/api/admin/location", methods=["POST"])
def api_admin_location_add():
    """Добавить локацию (точку продаж)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    lid = db.add_location(name)
    return jsonify({"ok": True, "id": lid})


@app.route("/api/admin/delivery", methods=["POST"])
def api_admin_delivery_add():
    """Добавить способ получения к точке."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    city = (data.get("city") or "").strip()
    name = (data.get("name") or "").strip()
    if not city or not name:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    try:
        fee = float(str(data.get("fee") or 0).replace(",", "."))
    except (TypeError, ValueError):
        fee = 0.0
    db.add_delivery_method(
        city, name,
        bool(data.get("needs_address")),
        (data.get("address_label") or "").strip(),
        (data.get("pickup_address") or "").strip(),
        max(0.0, fee),
        bool(data.get("needs_payment", True)),
        int(data.get("sort") or 0),
        bool(data.get("needs_point")),
    )
    return jsonify({"ok": True})


@app.route("/api/admin/delivery/update", methods=["POST"])
def api_admin_delivery_update():
    """Правка способа получения на месте (по id)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    name = (data.get("name") or "").strip()
    if not name or not db.get_delivery_method(mid):
        return jsonify({"ok": False, "error": "bad_input"}), 400
    try:
        fee = float(str(data.get("fee") or 0).replace(",", "."))
    except (TypeError, ValueError):
        fee = 0.0
    db.update_delivery_method(
        mid, name,
        bool(data.get("needs_address")),
        (data.get("address_label") or "").strip(),
        (data.get("pickup_address") or "").strip(),
        max(0.0, fee),
        bool(data.get("needs_payment", True)),
        bool(data.get("needs_point")),
    )
    return jsonify({"ok": True})


@app.route("/api/admin/delivery/delete", methods=["POST"])
def api_admin_delivery_delete():
    """Удалить способ получения."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_delivery_method(mid)
    return jsonify({"ok": True})


@app.route("/api/admin/location/delete", methods=["POST"])
def api_admin_location_delete():
    """Удалить локацию. Нельзя, если в ней есть товары."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        lid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    loc = db.get_location(lid)
    if not loc:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if db.count_products_in_location(loc["name"]) > 0:
        return jsonify({"ok": False, "error": "has_products"}), 400
    db.delete_location(lid)
    return jsonify({"ok": True})


# ---------- Бренды со вкусами ----------

@app.route("/api/brands")
def api_brands():
    category = request.args.get("category")
    key = f"brands:{category or 'all'}"
    cached = _cache_get(key)
    if cached is not None:
        return jsonify(cached)
    out = []
    for b in db.get_brands(category):
        try:
            flavors = json.loads(b["flavors"] or "[]")
        except Exception:
            flavors = []
        out.append({"id": b["id"], "name": b["name"], "category": b["category"], "flavors": flavors})
    return jsonify(_cache_set(key, out, 300))


@app.route("/api/admin/brand", methods=["POST"])
def api_admin_brand():
    """Создать или обновить бренд (если пришёл id — обновляем)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = (data.get("name") or "").strip()
    category = data.get("category") or "disposable"
    if not name or category not in db.category_codes():
        return jsonify({"ok": False, "error": "bad_data"}), 400
    flavors = [str(f).strip() for f in (data.get("flavors") or []) if str(f).strip()]

    bid = data.get("id")
    if bid:
        db.update_brand(int(bid), name, category, flavors)
        return jsonify({"ok": True, "id": int(bid)})
    new_id = db.add_brand(name, category, flavors)
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/admin/brand/delete", methods=["POST"])
def api_admin_brand_delete():
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        bid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_brand(bid)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
