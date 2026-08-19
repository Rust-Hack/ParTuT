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
import hashlib
import html
import json
import threading
import time

import requests
from flask import Flask, g, jsonify, request, Response

import auth
import cache
import db
import photos
import inputs
import tgsend
import errors
import notifications
from config import (BOT_TOKEN, SUPPORT_IDS, is_admin, is_super_admin, admin_city, admin_role, all_admin_ids)

db.init_db()      # схема, разовые переносы и права из окружения — внутри

# Отдельный экземпляр бота — ТОЛЬКО чтобы отправлять сообщения/картинки.

# Имя бота для реферальных ссылок (t.me/<bot>?startapp=...).



app = Flask(__name__, static_folder="webapp", static_url_path="")
# Права проверяет auth, а вешает страж на приложение — тот, у кого приложение
# есть. Так auth не знает про сервер, и граф импортов остаётся деревом.
app.before_request(auth.guard_owner_only)

# DEV_MODE=1 — разрешить пользоваться из обычного браузера (без Telegram) для локальной проверки.
#
# Он подставляет ВЛАДЕЛЬЦА любому, кто открыл страницу без подписи Telegram.
# На боевом это означало бы админку без пароля для всего интернета: достаточно
# один раз скопировать переменные с локальной машины на сервер. Поэтому боевая
# база (DATABASE_URL) выключает DEV_MODE намертво, что бы ни стояло в env.

_file_path_cache = {}      # кэш путей к файлам Telegram (чтобы не звать get_file каждый раз)
_photo_cache = {}          # кэш самих картинок в памяти: file_id -> (bytes, content_type)
_photo_cache_lock = threading.Lock()
_photo_cache_bytes = 0     # сколько памяти занято картинками
PHOTO_CACHE_MAX_BYTES = int(os.environ.get("PHOTO_CACHE_MB", "48")) * 1024 * 1024

# ============================================================
#  ПРОВЕРКА ПОДЛИННОСТИ (initData от Telegram)
# ============================================================

@app.route("/api/admin/requests", methods=["POST"])
def api_admin_requests_pending():
    """Список ожидающих заявок — только супер-админ."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
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
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rid = inputs.целое(data.get("request_id"))
    if rid is None:
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
            notifications.run_admin_request(tgsend.tg, req["action"], json.loads(req["payload"]))
        except Exception as e:
            print(f"Ошибка выполнения заявки #{rid}: {e}")
        tgsend.notify_client(req["requester_id"], f"✅ Ваш запрос одобрен:\n{req['summary']}")
    else:
        tgsend.notify_client(req["requester_id"], f"✖️ Ваш запрос отклонён:\n{req['summary']}")
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
    errors.report(tgsend.tg, f"{request.method} {request.path}", e)
    return jsonify({"ok": False, "error": "server_error"}), 500


@app.after_request
def _bust_cache_on_write(resp):
    if request.method == "POST" and 200 <= resp.status_code < 300:
        keys = _WRITE_PATHS.get(request.path)
        if keys is not None:
            cache.bust(*keys)
            # Остаток мог измениться где угодно: правка товара, замена вкусов,
            # отклонение заказа. Ловим это в ОДНОМ месте, а не в каждом маршруте
            # — иначе новый способ менять склад однажды забудут сюда вписать.
            if keys is _STOCK_KEYS or request.path.startswith("/api/admin/product"):
                tgsend.bg(_flush_stock_alerts)
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
        db.log_admin_action(int(admin["id"]), auth._admin_display(admin),
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
            tgsend.tg.send_message(uid, f"🔔 «{name}» снова в наличии.\nОткройте приложение — товар доступен к заказу.")
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
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "no_user"}), 403
    pid = inputs.целое(data.get("product_id"))
    if pid is None:
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
    user = auth.get_user(data.get("initData", ""))
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
    tgsend.bg(db.remember_user_name, uid, user.get("username") or "", user.get("first_name") or "")

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
    user = auth.get_user(data.get("initData", ""))
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
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])

    if "point_id" in data:
        raw = data.get("point_id")
        if raw in (None, "", 0, "0"):
            db.set_user_point(uid, None)             # «не выбрано» — законный вариант
        else:
            pid = inputs.целое(raw)
            if pid is None:
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
        phone = inputs._text(data.get("phone"))
        if phone and len(inputs._digits(phone)) < 7:
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
    user = auth.get_user(data.get("initData", ""))
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
    user = auth.get_user(data.get("initData", ""))
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
    pid = inputs.целое(request.args.get("product_id"))
    if pid is None:
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
    user = auth.get_user(data.get("initData", ""))
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
    user = auth.get_user(data.get("initData", ""))
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
    tgsend.bg(_notify_new_review, rid)
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
            tgsend.tg.send_message(aid, text)
        except Exception as e:
            print(f"Не смог сообщить админу {aid} об отзыве: {e}")


@app.route("/api/admin/reviews", methods=["POST"])
def api_admin_reviews():
    """Отзывы для админа: очередь на модерацию, опубликованные, скрытые."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
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
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rid = inputs.целое(data.get("id"))
    if rid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.delete_review(rid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/review/reply", methods=["POST"])
def api_admin_review_reply():
    """Ответ магазина под отзывом. Пустой текст убирает ответ."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rid = inputs.целое(data.get("id"))
    if rid is None:
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
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rid = inputs.целое(data.get("id"))
    if rid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    status = "approved" if data.get("ok") else "hidden"
    if not db.set_review_status(rid, status):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "status": status})


# ============================================================
#  ЛОКАЦИИ (точки продаж)
# ============================================================


@app.route("/api/categories")
def api_categories():
    """Категории товара — витрина строит по ним фильтры, админка формы."""
    cached = cache.get("categories")
    if cached is None:
        by_cat = {}
        for s in db.list_category_specs():
            by_cat.setdefault(s["category"], []).append(s)
        cached = cache.put("categories", [{"code": c["code"], "name": c["name"],
                                            "emoji": c["emoji"] or "", "sort": c["sort"],
                                            # Вкусы есть не у всего: у картриджей их нет,
                                            # а у жидкостей товар без них не завести.
                                            "has_flavors": bool(c.get("has_flavors")),
                                            "specs": by_cat.get(c["code"], [])}
                                           for c in db.list_categories()], 300)
    return cache.json_etag(cached)


@app.route("/api/also-bought")
def api_also_bought():
    """Что покупали вместе — для подсказки в корзине. Считается по выданным заказам."""
    cached = cache.get("also_bought")
    if cached is None:
        try:
            data = db.also_bought()
        except Exception as e:
            data = {}                     # без подсказок корзина работает как раньше
            print(f"Не удалось посчитать совместные покупки: {e}")
        # Ключи в JSON всё равно станут строками — приводим сразу, чтобы фронт
        # не гадал, каким типом искать.
        cached = cache.put("also_bought", {str(k): v for k, v in data.items()}, 600)
    return jsonify(cached)


# ============================================================
#  ТОВАРЫ
# ============================================================

# Что в товаре не для покупателя: закупка (наша маржа), число ждущих
# поступления (наша кухня) и пометка «снят с витрины» (его тут и не будет).
photos.RECEIPT_TOKEN_TTL = 6 * 3600       # ссылка на чек живёт полдня, не вечно


@app.route("/api/photo")
def api_photo():
    file_id = request.args.get("file_id", "")
    if not file_id:
        return Response("no file_id", status=404)
    if not photos._may_see_photo(file_id, request.args.get("t", "")):
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
            path = tgsend.tg.get_file(file_id).file_path
            _file_path_cache[file_id] = path
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "image/jpeg")
        _photo_cache_put(file_id, r.content, ctype)
        tgsend.bg(_store_photo_blob, file_id, ctype, r.content)   # запись в базу не задерживает ответ
        return _photo_response(r.content, ctype, file_id)
    except Exception as e:
        _file_path_cache.pop(file_id, None)      # путь мог протухнуть — сбросим, чтобы взять заново
        print(f"Ошибка отдачи фото {file_id}: {e}")
        return Response("photo error", status=404)


photos.GRID_PHOTO_MIN_WIDTH = 480     # карточка каталога ~190px, но экраны телефонов 2-3x


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

SUPPORT_COOLDOWN = 20          # антиспам: не чаще 1 сообщения в поддержку за столько секунд
_support_last = {}             # uid -> время последнего сообщения (в памяти процесса)


@app.route("/api/support", methods=["POST"])
def api_support():
    """Клиент пишет в поддержку — доставляем сообщение менеджеру(ам) через бота."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    text = inputs._text(data.get("text"), 2000)
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
    who = tgsend.contact_link(uname, uid, name)   # кликабельно: открыть чат с клиентом

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
           f"Открыть чат: {tgsend.contact_link(uname, uid, 'написать клиенту')}  ·  или /reply {uid} ваш текст")
    delivered = 0
    for sid in SUPPORT_IDS:
        try:
            tgsend.tg.send_message(sid, msg, parse_mode="HTML")
            delivered += 1
        except Exception as e:
            print(f"Не смог доставить вопрос в поддержку {sid}: {e}")
    return jsonify({"ok": True, "delivered": delivered})


@app.route("/api/admin/message", methods=["POST"])
def api_admin_message():
    """Админ пишет клиенту — доставляем сообщение клиенту через бота."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = inputs.целое(data.get("user_id"))
    if target is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    text = inputs._text(data.get("text"), 2000)
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    # Продавец пишет по своим заказам, а не всей базе покупателей: иначе с одной
    # точки можно разослать что угодно всем клиентам магазина.
    scope = admin.get("city")
    if scope and not any(o["city"] == scope for o in db.get_orders_by_user(target, 50)):
        return jsonify({"ok": False, "error": "other_city",
                        "message": "Этот покупатель не заказывал на вашей точке."}), 403
    contact = tgsend.contact_link(admin.get("username"), int(admin["id"]), "написать менеджеру")
    msg = (f"💬 Сообщение от магазина:\n{html.escape(text)}\n\n"
           f"По любым вопросам: {contact}")
    try:
        tgsend.tg.send_message(target, msg, parse_mode="HTML")
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

# ------------------- Админы и продавцы (только супер-админ) -------------------



# ============================================================
#  СБОРКА ПРИЛОЖЕНИЯ
# ============================================================
# Раньше здесь стояли импорты «ради регистрации маршрутов»: модули лезли в
# server.app, а server внизу импортировал их обратно. Четырнадцать кругов,
# работавших только потому, что импорты стояли в самом низу файла.
#
# Теперь каждый модуль объявляет свои маршруты на Blueprint и про сервер не
# знает ничего. Стрелка одна: server подключает их, они его — нет. Забыть
# модуль в этом списке нельзя незаметно — сторожит tests/test_server_split.
import server_admin        # noqa: E402
import server_catalog      # noqa: E402
import server_customers    # noqa: E402
import server_games        # noqa: E402
import server_orders       # noqa: E402
import server_promos       # noqa: E402
import server_shop         # noqa: E402
import server_stock        # noqa: E402

for _модуль in (server_admin, server_catalog, server_customers, server_games,
                server_orders, server_promos, server_shop, server_stock):
    app.register_blueprint(_модуль.bp)


if __name__ == "__main__":
    # «python server.py» делает из ЭТОГО файла модуль __main__, и вынесенные
    # модули, импортируй они server, завели бы его вторым экземпляром. Круга
    # больше нет, но привычку оставляем: порт отдаём настоящему модулю.
    import server as настоящий
    port = int(os.environ.get("PORT", 5000))
    настоящий.app.run(host="0.0.0.0", port=port)
