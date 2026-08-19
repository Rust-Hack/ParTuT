"""
server_promos.py — промокоды: ручки админки.

Пара к db_promos.py: там правила и списание кода, здесь — что владелец делает
с ними руками (завести, включить-выключить, удалить, посмотреть список).

Проверку промокода при оформлении здесь искать не надо: она живёт на пути
заказа, потому что код надо не только узнать, но и списать — одной транзакцией
вместе с самим заказом.

Помощники берутся ЧЕРЕЗ модуль (server.get_admin(), server._text()).
"""

from flask import jsonify, request

import db
import server
@server.app.route("/api/admin/promos", methods=["POST"])
def api_admin_promos():
    """Коды со статистикой: сколько заказов и выручки принёс каждый."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "promos": db.list_promos()})


@server.app.route("/api/admin/promo", methods=["POST"])
def api_admin_promo_add():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = server._text(data.get("code")).upper()
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


@server.app.route("/api/admin/promo/toggle", methods=["POST"])
def api_admin_promo_toggle():
    """Выключить код, не удаляя: статистика по нему должна остаться."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    db.set_promo_active((data.get("code") or ""), bool(data.get("active")))
    return jsonify({"ok": True})


@server.app.route("/api/admin/promo/delete", methods=["POST"])
def api_admin_promo_delete():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    db.delete_promo((data.get("code") or ""))
    return jsonify({"ok": True})
