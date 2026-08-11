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
import hmac
import hashlib
import json
import random
from urllib.parse import parse_qsl

import requests
import telebot
from flask import Flask, jsonify, request, Response, send_from_directory

import db
import notifications
from config import BOT_TOKEN, PAYMENT_INFO, ADMIN_IDS, CONFIRM_MINUTES, is_admin, CITIES, CATEGORIES

db.init_db()

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


# ============================================================
#  СТРАНИЦА
# ============================================================

@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")


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
    db.ensure_user(uid)

    # Реферал: friend открыл приложение по ссылке ...startapp=refN
    start_param = str(data.get("start_param") or "")
    if start_param.startswith("ref"):
        try:
            ref_id = int(start_param[3:])
            if db.set_referrer_once(uid, ref_id):
                db.add_coins(ref_id, REFERRAL_BONUS)
        except (TypeError, ValueError):
            pass

    return jsonify({"ok": True, "age_ok": db.is_age_ok(uid), "is_admin": is_admin(uid)})


@app.route("/api/age", methods=["POST"])
def api_age():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    db.set_age_ok(int(user["id"]))
    return jsonify({"ok": True})


# ============================================================
#  ЛОКАЦИИ (точки продаж)
# ============================================================

@app.route("/api/locations")
def api_locations():
    return jsonify([{"id": r["id"], "name": r["name"]} for r in db.get_locations()])


# ============================================================
#  ТОВАРЫ
# ============================================================

@app.route("/api/products")
def api_products():
    city = request.args.get("city")
    variants_by = {}
    for v in db.get_all_variants():
        variants_by.setdefault(v["product_id"], []).append({"flavor": v["flavor"], "stock": v["stock"]})
    out = []
    for p in db.get_all_products():
        if city and p["city"] != city:
            continue
        out.append({
            "id": p["id"], "name": p["name"], "price": p["price"],
            "stock": p["stock"], "is_hit": p["is_hit"],
            "category": p["category"], "city": p["city"],
            "description": p["description"] or "",
            "brand": p["brand"] or "", "flavor": p["flavor"] or "",
            "strength": p["strength"] or "", "volume": p["volume"] or "",
            "variants": variants_by.get(p["id"], []),
            "photo_url": (f"/api/photo?file_id={p['photo']}" if p["photo"] else None),
        })
    return jsonify(out)


@app.route("/api/photo")
def api_photo():
    file_id = request.args.get("file_id", "")
    if not file_id:
        return Response("no file_id", status=404)

    # 1. Если картинка уже скачивалась — отдаём из памяти (мгновенно).
    cached = _photo_cache.get(file_id)
    if cached:
        return _photo_response(cached[0], cached[1])

    # 2. Иначе тянем из Telegram один раз и запоминаем.
    try:
        path = _file_path_cache.get(file_id)
        if not path:
            path = tg.get_file(file_id).file_path
            _file_path_cache[file_id] = path
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "image/jpeg")
        if len(_photo_cache) < 200:              # простой предохранитель по размеру
            _photo_cache[file_id] = (r.content, ctype)
        return _photo_response(r.content, ctype)
    except Exception as e:
        _file_path_cache.pop(file_id, None)      # путь мог протухнуть — сбросим, чтобы взять заново
        print(f"Ошибка отдачи фото {file_id}: {e}")
        return Response("photo error", status=404)


def _photo_response(content, ctype):
    """Ответ с картинкой + заголовок кэша, чтобы браузер/Telegram не запрашивали её повторно."""
    resp = Response(content, content_type=ctype)
    resp.headers["Cache-Control"] = "public, max-age=86400"   # кэш на сутки
    return resp


# ============================================================
#  ЗАКАЗ
# ============================================================

def _payment_info():
    """Реквизиты оплаты: из настроек магазина, иначе — значение из config."""
    return db.get_setting("payment_info", PAYMENT_INFO)


def _confirm_minutes():
    """Через сколько минут продавец подтверждает: из настроек, иначе — из config."""
    try:
        return int(db.get_setting("confirm_minutes", CONFIRM_MINUTES))
    except (TypeError, ValueError):
        return CONFIRM_MINUTES


@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401

    user_id = int(user["id"])
    if not db.is_age_ok(user_id):
        return jsonify({"ok": False, "error": "age"}), 403

    username = user.get("username") or user.get("first_name") or str(user_id)
    pickup = (data.get("pickup_time") or "как можно скорее").strip()

    # Цены и наличие берём из БАЗЫ, а не из того, что прислал клиент.
    items, total, cities = [], 0.0, set()
    for ri in data.get("items", []):
        try:
            pid, qty = int(ri.get("id")), int(ri.get("qty", 0))
        except (TypeError, ValueError):
            continue
        flavor = (ri.get("flavor") or "").strip() or None
        p = db.get_product(pid)
        if not p or qty <= 0:
            continue
        if flavor:
            # товар-модель со вкусами: остаток берём у нужного варианта
            avail = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}.get(flavor, 0)
            if avail <= 0:
                continue
            real_qty = min(qty, avail)
            name = f"{p['name']} — {flavor}"
        else:
            if p["stock"] <= 0:
                continue
            real_qty = min(qty, p["stock"])
            name = p["name"]
        items.append({"id": pid, "flavor": flavor, "name": name, "price": p["price"], "qty": real_qty})
        total += p["price"] * real_qty
        cities.add(p["city"])

    if not items:
        return jsonify({"ok": False, "error": "empty"}), 400
    if len(cities) > 1:
        return jsonify({"ok": False, "error": "multi_city"}), 400

    city = cities.pop()
    total = round(total, 2)

    # Списание монет: 1 монета = COIN_VALUE Br, но не больше суммы заказа.
    coins_used, discount = 0, 0.0
    if data.get("use_coins") and total > 0:
        have = db.get_coins(user_id)
        spend = min(have, int(total / COIN_VALUE))     # столько монет реально можно списать
        if spend > 0:
            coins_used = spend
            discount = round(spend * COIN_VALUE, 2)
            total = round(total - discount, 2)
            db.add_coins(user_id, -spend)              # списываем сразу

    order_id = db.create_order(user_id, username, city, items, total, pickup)
    if coins_used:
        db.set_order_coins_used(order_id, coins_used)
    for it in items:
        if it.get("flavor"):
            db.change_variant_stock(it["id"], it["flavor"], -it["qty"])
        else:
            db.change_stock(it["id"], -it["qty"])

    # Реквизиты и итог отдаём приложению — оно покажет экран оплаты.
    return jsonify({
        "ok": True,
        "order_id": order_id,
        "total": total,
        "coins_used": coins_used,
        "discount": discount,
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
        notifications.notify_sellers(tg, order_id)  # заказ уходит продавцу города
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "send_failed"}), 500


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
    db.ensure_user(uid)
    link = f"https://t.me/{BOT_USERNAME}?startapp=ref{uid}" if BOT_USERNAME else ""
    return jsonify({"ok": True,
                    "coins": db.get_coins(uid),
                    "referrals": db.count_referrals(uid),
                    "ref_link": link,
                    "referral_bonus": REFERRAL_BONUS,
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


@app.route("/api/wheel/spin", methods=["POST"])
def api_wheel_spin():
    """Прокрут колеса: списываем прокрут, выбираем приз по весам, начисляем монеты."""
    data = request.get_json(force=True, silent=True) or {}
    user = get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    if not db.use_spin(uid):
        return jsonify({"ok": False, "error": "no_spins"}), 400

    weights = [s["weight"] for s in WHEEL_SECTORS]
    idx = random.choices(range(len(WHEEL_SECTORS)), weights=weights, k=1)[0]
    prize = WHEEL_SECTORS[idx]
    db.add_coins(uid, prize["coins"])
    w = db.get_wheel(uid)
    return jsonify({"ok": True, "index": idx, "coins": prize["coins"], "label": prize["label"],
                    "balance": db.get_coins(uid), "spins": w["spins"]})


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
    if city not in db.location_names() or category not in CATEGORIES or not name:
        return jsonify({"ok": False, "error": "bad_data"}), 400
    try:
        price = float(str(data.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400

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
                             brand=brand, flavor="", strength=strength, volume=vol)
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
                         brand=brand, flavor=flavor, strength=strength, volume=volume)
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
        if field == "price":
            value = max(0.0, float(str(raw).replace(",", ".")))
        elif field == "stock":
            value = max(0, int(raw))
        elif field in ("name", "description", "brand", "flavor", "strength", "volume"):
            value = str(raw).strip()
        elif field == "category":
            value = str(raw).strip()
            if value not in CATEGORIES:
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
        file_id = msg.photo[-1].file_id
    except Exception as e:
        print(f"Не смог обработать фото товара: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500

    db.update_field(pid, "photo", file_id)
    return jsonify({"ok": True})


# ------------------- Заказы (управление в приложении) -------------------

def _notify_client(user_id, text):
    """Сообщение клиенту о смене статуса заказа (не роняем запрос, если заблокировал бота)."""
    try:
        tg.send_message(int(user_id), text)
    except Exception as e:
        print(f"Не смог уведомить клиента {user_id}: {e}")


def _order_item_count(o):
    """Сколько единиц товара в заказе (для прогресса колеса)."""
    try:
        return sum(int(it.get("qty", 0)) for it in json.loads(o["items"]))
    except (TypeError, ValueError):
        return 0


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
    if action == "confirm":
        db.set_order_status(oid, "confirmed")
        _notify_client(client_id, f"✅ Оплата по заказу #{oid} подтверждена!\n"
                                  f"Ждём вас {order['pickup_time']}. Спасибо! 🌿")
    elif action == "issued":
        if order["status"] != "issued":                 # начисляем один раз
            db.add_coins(client_id, int(order["total"]) * COINS_PER_BYN)
            db.add_wheel_progress(client_id, _order_item_count(order))   # прогресс колеса
        db.set_order_status(oid, "issued")
        _notify_client(client_id, f"Заказ #{oid} выдан. Спасибо, что выбрали нас! 🙌")
    elif action == "reject":
        db.set_order_status(oid, "canceled")
        db.restore_order_stock(order)               # вернуть остаток (с учётом вкусов)
        if order["coins_used"]:                      # вернуть списанные монеты
            db.add_coins(client_id, order["coins_used"])
        _notify_client(client_id, f"К сожалению, заказ #{oid} отклонён продавцом. "
                                  "Если это ошибка — напишите нам, разберёмся.")
    else:
        return jsonify({"ok": False, "error": "bad_action"}), 400
    return jsonify({"ok": True})


# ------------------- Статистика -------------------

@app.route("/api/admin/stats", methods=["POST"])
def api_admin_stats():
    """Сводка для админа: выручка, статусы, топ товаров, склад."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    orders = db.get_orders(limit=100000)
    products = db.get_all_products()

    by_status = {}
    issued_total = inwork_total = 0.0
    qty_by_name = {}
    revenue_by_city = {}
    for o in orders:
        st = o["status"]
        by_status[st] = by_status.get(st, 0) + 1
        if st == "issued":
            issued_total += o["total"]
            revenue_by_city[o["city"]] = revenue_by_city.get(o["city"], 0.0) + o["total"]
        elif st in ("paid", "confirmed"):
            inwork_total += o["total"]
        if st != "canceled":
            try:
                for it in json.loads(o["items"]):
                    qty_by_name[it["name"]] = qty_by_name.get(it["name"], 0) + int(it.get("qty", 0))
            except (TypeError, ValueError):
                pass

    top = sorted(qty_by_name.items(), key=lambda x: -x[1])[:5]
    low = [{"name": p["name"], "city": p["city"], "stock": p["stock"]}
           for p in products if 0 < p["stock"] <= 3]
    out_of_stock = sum(1 for p in products if p["stock"] <= 0)

    return jsonify({"ok": True, "stats": {
        "issued_total": round(issued_total, 2),
        "inwork_total": round(inwork_total, 2),
        "orders_total": len(orders),
        "by_status": by_status,
        "top": [{"name": n, "qty": q} for n, q in top],
        "revenue_by_city": [{"city": c, "total": round(t, 2)} for c, t in
                            sorted(revenue_by_city.items(), key=lambda x: -x[1])],
        "low_stock": low,
        "out_of_stock": out_of_stock,
        "products_total": len(products),
    }})


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
    out = []
    for b in db.get_brands(category):
        try:
            flavors = json.loads(b["flavors"] or "[]")
        except Exception:
            flavors = []
        out.append({"id": b["id"], "name": b["name"], "category": b["category"], "flavors": flavors})
    return jsonify(out)


@app.route("/api/admin/brand", methods=["POST"])
def api_admin_brand():
    """Создать или обновить бренд (если пришёл id — обновляем)."""
    data = request.get_json(force=True, silent=True) or {}
    if not get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = (data.get("name") or "").strip()
    category = data.get("category") or "disposable"
    if not name or category not in CATEGORIES:
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
