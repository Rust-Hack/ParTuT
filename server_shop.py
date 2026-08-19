"""
server_shop.py — устройство магазина: точки, способы получения, продавцы.

Третий кусок, вынесенный из server.py. Здесь настраивают не товар, а сам
магазин: где он работает, как оттуда получают заказ и кто за этим стоит.

Две ручки отсюда — единственные, открытые всем: список точек и способы
получения нужны покупателю ещё до того, как он что-то выбрал.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin()), а Flask и база
импортируются напрямую — это внешние библиотеки, а не состояние сервера.
"""

from flask import Blueprint, jsonify, request

import config
import cache
import auth
import db
import shopinfo
import tgsend
import inputs
from config import CITIES, SUPER_ADMIN_IDS, is_super_admin

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("shop", __name__)


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


@bp.route("/api/locations")
def api_locations():
    cached = cache.get("locations")
    if cached is None:
        cached = cache.put("locations",
                            [{"id": r["id"], "name": r["name"]} for r in db.get_locations()], 300)
    return cache.json_etag(cached)


@bp.route("/api/delivery")
def api_delivery():
    """Способы получения для точки (для оформления заказа).

    Отдаём вместе с точками самовывоза этого города: покупатель выбирает нужную
    прямо в заказе, а не роется в настройках. Один запрос вместо двух — шторка
    оформления должна открываться мгновенно."""
    city = inputs._text(request.args.get("city"))
    key = f"delivery:{city}"
    cached = cache.get(key)
    if cached is None:
        cached = cache.put(key, {
            "methods": [_delivery_json(m) for m in db.get_delivery_methods(city)],
            "points": [{"id": p["id"], "address": p["address"], "note": p["note"] or ""}
                       for p in db.get_pickup_points(city)],
            "free_from": shopinfo._free_delivery_from(),   # с какой суммы доставка бесплатна
            "orders_done": shopinfo._orders_done(),         # доверие: сколько заказов уже выдано
        }, 300)
    return jsonify(cached)


@bp.route("/api/admin/point", methods=["POST"])
def api_admin_point_add():
    """Добавить точку самовывоза городу."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    city = inputs._text(data.get("city"))
    address = inputs._text(data.get("address"))
    if not city or not address:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    db.add_pickup_point(city, address, inputs._text(data.get("note"), 80),
                        int(data.get("sort") or 0))
    return jsonify({"ok": True})


@bp.route("/api/admin/point/update", methods=["POST"])
def api_admin_point_update():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    address = inputs._text(data.get("address"))
    if not address:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    db.update_pickup_point(pid, address, inputs._text(data.get("note"), 80))
    return jsonify({"ok": True})


@bp.route("/api/admin/point/delete", methods=["POST"])
def api_admin_point_delete():
    """Удаление точки не трогает прежние заказы: адрес в них сохранён строкой,
    поэтому продавец по-прежнему видит, куда человек приедет."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_pickup_point(pid)
    return jsonify({"ok": True})


@bp.route("/api/admin/staff", methods=["POST"])
def api_admin_staff():
    """Список тех, у кого есть доступ. Все они живут в базе и убираются отсюда же
    — кроме владельца: его права держатся на настройках сервера, чтобы доступ к
    магазину нельзя было потерять ни по ошибке, ни злым умыслом."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth._super(data):
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


@bp.route("/api/admin/staff/add", methods=["POST"])
def api_admin_staff_add():
    data = request.get_json(force=True, silent=True) or {}
    if not (su := auth._super(data)):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        uid = int(str(data.get("user_id", "")).strip())
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if uid <= 0:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    city = inputs._text(data.get("city"))
    if city and city not in CITIES and city not in {l["name"] for l in db.get_locations()}:
        return jsonify({"ok": False, "error": "bad_city"}), 400
    db.add_staff(uid, city, inputs._text(data.get("note"), 64), int(su["id"]))
    config.refresh_staff()       # права должны действовать сразу, а не через полминуты
    tgsend.bg(tgsend.notify_new_admin, uid, city)
    return jsonify({"ok": True})


@bp.route("/api/admin/staff/remove", methods=["POST"])
def api_admin_staff_remove():
    data = request.get_json(force=True, silent=True) or {}
    if not auth._super(data):
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


@bp.route("/api/admin/location", methods=["POST"])
def api_admin_location_add():
    """Добавить локацию (точку продаж)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = inputs._text(data.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    lid = db.add_location(name)
    return jsonify({"ok": True, "id": lid})


@bp.route("/api/admin/delivery", methods=["POST"])
def api_admin_delivery_add():
    """Добавить способ получения к точке."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    city = inputs._text(data.get("city"))
    name = inputs._text(data.get("name"))
    if not city or not name:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    try:
        fee = float(str(data.get("fee") or 0).replace(",", "."))
    except (TypeError, ValueError):
        fee = 0.0
    db.add_delivery_method(
        city, name,
        bool(data.get("needs_address")),
        inputs._text(data.get("address_label")),
        inputs._text(data.get("pickup_address")),
        max(0.0, fee),
        bool(data.get("needs_payment", True)),
        int(data.get("sort") or 0),
        bool(data.get("needs_point")),
    )
    return jsonify({"ok": True})


@bp.route("/api/admin/delivery/update", methods=["POST"])
def api_admin_delivery_update():
    """Правка способа получения на месте (по id)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    name = inputs._text(data.get("name"))
    if not name or not db.get_delivery_method(mid):
        return jsonify({"ok": False, "error": "bad_input"}), 400
    try:
        fee = float(str(data.get("fee") or 0).replace(",", "."))
    except (TypeError, ValueError):
        fee = 0.0
    db.update_delivery_method(
        mid, name,
        bool(data.get("needs_address")),
        inputs._text(data.get("address_label")),
        inputs._text(data.get("pickup_address")),
        max(0.0, fee),
        bool(data.get("needs_payment", True)),
        bool(data.get("needs_point")),
    )
    return jsonify({"ok": True})


@bp.route("/api/admin/delivery/delete", methods=["POST"])
def api_admin_delivery_delete():
    """Удалить способ получения."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    db.delete_delivery_method(mid)
    return jsonify({"ok": True})


@bp.route("/api/admin/location/delete", methods=["POST"])
def api_admin_location_delete():
    """Удалить локацию. Нельзя, если в ней есть товары."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
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
    # Открытые заказы этой точки повиснут в городе, которого больше нет: покупатель
    # ждёт выдачи, а продавец даже не найдёт заказ в списке своей точки.
    open_orders = [o for o in db.get_orders()
                   if o["city"] == loc["name"] and o["status"] in ("new", "paid", "confirmed")]
    if open_orders:
        return jsonify({"ok": False, "error": "has_orders", "count": len(open_orders)}), 400
    db.delete_location(lid)
    return jsonify({"ok": True})
