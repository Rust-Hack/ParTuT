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

# Разумный потолок на одно движение склада. Денежные настройки в admin.py уже
# зажаты границами на сервере — здесь того же не было: случайный лишний ноль
# в приходе (10 000 вместо 1 000) проходил без единого предупреждения.
_MAX_STOCK_MOVE = 100_000


def _сколько_числится(товар, flavor):
    """Остаток той полки, о которой идёт речь: у товара со вкусами — по вкусу.

    Берём из базы, а не из присланного числа: между открытием окна и нажатием
    «Записать» проходит время, и за него мог случиться заказ.
    """
    if not flavor:
        return int(товар["stock"] or 0)
    for v in db.get_variants(int(товар["id"])):
        if v["flavor"] == flavor:
            return int(v["stock"] or 0)
    return 0


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
    if qty < 0:
        return jsonify({"ok": False, "error": "bad_number"}), 400
    if qty > _MAX_STOCK_MOVE:
        return jsonify({"ok": False, "error": "too_large",
                        "message": f"Больше {_MAX_STOCK_MOVE:,} шт.".replace(",", " ")
                                   + " за одно движение — похоже на опечатку. Если это "
                                     "правда так много, запишите движение в несколько приёмов."}), 400
    reason = data.get("reason")
    if reason not in db.STOCK_REASONS:
        return jsonify({"ok": False, "error": "bad_reason"}), 400
    if reason != "fix" and qty <= 0:
        return jsonify({"ok": False, "error": "bad_number"}), 400
    товар = db.get_product(pid)
    if not товар:
        return jsonify({"ok": False, "error": "not_found"}), 404
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny

    flavor = inputs._text(data.get("flavor")) or None
    # Приход прибавляет, списания вычитают. Знак задаёт причина, а не клиент:
    # иначе «брак» мог бы прийти с плюсом.
    #
    # Пересчёт стоит особняком: продавец присылает РЕЗУЛЬТАТ пересчёта, а
    # разницу считаем здесь. Считать её на клиенте — значит доверять чужой
    # арифметике при записи на склад, а ошибка в знаке вскроется только
    # следующей недостачей. Разница бывает и в плюс: пересчёт находит не
    # только пропажу, но и лишнее — раньше поправить это было нечем, кроме
    # «Прихода», а тот переписывает закупочную цену и врёт про завоз.
    if reason == "fix":
        было = _сколько_числится(товар, flavor)
        delta = qty - было
        if delta == 0:
            return jsonify({"ok": False, "error": "no_change",
                            "message": "Столько и числится — записывать нечего."}), 400
    else:
        delta = qty if reason == "in" else -qty
    try:
        cost = max(0.0, float(str(data.get("cost") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        cost = 0.0
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
