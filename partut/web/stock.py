"""
partut/web/stock.py — движение склада: приход, списание, бой.

Пара к partut/db/stock.py: там сама запись движения одной транзакцией вместе с
остатком, здесь — ручки, которыми продавец это делает, и журнал движений.

Почему это отдельно от ассортимента: завести товар и изменить его остаток —
разные права. Ассортимент ведёт владелец, а списать разбитый под может
продавец своей точки, и каждое такое движение остаётся в журнале с именем.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin(), auth.deny_city()).
"""

from flask import Blueprint, jsonify, request

from partut.web import auth
from partut import db
from partut import inputs

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("stock", __name__)
@bp.route("/api/admin/stock/move", methods=["POST"])
def api_admin_stock_move():
    """Приход или списание с причиной. Остаток меняется только так — тогда на
    любой вопрос «куда делось» есть ответ с именем и датой."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
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
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny

    # Приход прибавляет, всё остальное списывает. Знак задаёт причина, а не
    # клиент: иначе «списание» могло бы прийти с плюсом.
    delta = qty if reason == "in" else -qty
    try:
        cost = max(0.0, float(str(data.get("cost") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        cost = 0.0
    flavor = inputs._text(data.get("flavor")) or None
    stock = db.move_stock(pid, delta, reason, flavor=flavor, cost=cost,
                          note=data.get("note"), admin_id=int(admin["id"]))
    return jsonify({"ok": True, "stock": stock})


@bp.route("/api/admin/stock/moves", methods=["POST"])
def api_admin_stock_moves():
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id")) if data.get("id") else None
    except (TypeError, ValueError):
        pid = None
    if pid:
        # История чужой точки — тоже чужая: по ней видно завоз, списания и
        # закупочные цены соседей.
        deny = auth.deny_product(admin, pid)
        if deny:
            return deny
    # Без товара в запросе это «вся история магазина» — продавцу отдаём только
    # его точку. Раньше проверка стояла лишь на запрос по конкретному товару.
    moves = db.get_stock_moves(pid, 60, city=(admin.get("city") or None))
    return jsonify({"ok": True, "moves": moves, "reasons": db.STOCK_REASONS})
