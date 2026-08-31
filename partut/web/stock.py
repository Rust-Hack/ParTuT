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


def _apply_move(admin, item, reason=None):
    """Одно движение склада — общая логика для одиночной ручки и пачки.

    reason=None значит «взять из item» (одиночная ручка); в пачке причина
    ОДНА на все позиции и передаётся снаружи — крупный завоз это всегда один
    и тот же приход, а не смесь причин. Возвращает (код, тело ответа).
    """
    try:
        pid = int(item.get("id"))
        qty = int(item.get("qty"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_number"}
    if qty < 0:
        return 400, {"ok": False, "error": "bad_number"}
    if qty > _MAX_STOCK_MOVE:
        return 400, {"ok": False, "error": "too_large",
                     "message": f"Больше {_MAX_STOCK_MOVE:,} шт.".replace(",", " ")
                                + " за одно движение — похоже на опечатку. Если это "
                                  "правда так много, запишите движение в несколько приёмов."}
    reason = reason or item.get("reason")
    if not isinstance(reason, str) or reason not in db.STOCK_REASONS:
        return 400, {"ok": False, "error": "bad_reason"}
    if reason != "fix" and qty <= 0:
        return 400, {"ok": False, "error": "bad_number"}
    товар = db.get_product(pid)
    if not товар:
        return 404, {"ok": False, "error": "not_found"}
    deny = auth.deny_product(admin, pid)
    if deny:
        # deny_product отдаёт готовый Flask-ответ (jsonify(...), код) — этот
        # помощник работает с (код, словарь), поэтому распаковываем обратно.
        ответ, код = deny
        return код, ответ.get_json()

    flavor = inputs._text(item.get("flavor")) or None
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
            return 400, {"ok": False, "error": "no_change",
                         "message": "Столько и числится — записывать нечего."}
    else:
        delta = qty if reason == "in" else -qty
    try:
        cost = max(0.0, float(str(item.get("cost") or 0).replace(",", ".")))
    except (TypeError, ValueError):
        cost = 0.0
    stock = db.move_stock(pid, delta, reason, flavor=flavor, cost=cost,
                          note=item.get("note"), admin_id=int(admin["id"]))
    return 200, {"ok": True, "stock": stock}


@bp.route("/api/admin/stock/move", methods=["POST"])
def api_admin_stock_move():
    """Приход или списание с причиной. Остаток меняется только так — тогда на
    любой вопрос «куда делось» есть ответ с именем и датой."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    код, тело = _apply_move(admin, data)
    return jsonify(тело), код


@bp.route("/api/admin/stock/move/batch", methods=["POST"])
def api_admin_stock_move_batch():
    """Приход (или другое движение) сразу по нескольким товарам одним запросом.

    Причина на всю пачку одна — крупный завоз это всегда один и тот же приход
    по разным позициям, а не смесь причин. Раньше на каждый товар в партии
    уходил отдельный запрос — завоз пятнадцати позиций был пятнадцатью тапами
    «Записать».

    Пачка сохраняет всё, что прошло, и честно называет, что не прошло: одна
    ошибочная позиция (чужая точка, опечатка в количестве) не должна отменять
    остальные девять, которые были в порядке."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    reason = data.get("reason")
    # isinstance ДО in: причина может прийти чем угодно (мусорный запрос
    # шлёт словарь/список вместо строки), а `x not in {словарь}` на
    # нехешируемом x падает TypeError раньше, чем строка «bad_reason».
    if not isinstance(reason, str) or reason not in db.STOCK_REASONS:
        return jsonify({"ok": False, "error": "bad_reason"}), 400
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "empty",
                        "message": "Список товаров пуст — записывать нечего."}), 400

    done, failed = [], {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            failed[str(i)] = {"error": "bad_item"}
            continue
        _, тело = _apply_move(admin, item, reason=reason)
        pid_str = str(item.get("id"))
        if тело.get("ok"):
            done.append({"id": item.get("id"), "stock": тело.get("stock")})
        else:
            failed[pid_str] = {"error": тело.get("error"), "message": тело.get("message")}
    return jsonify({"ok": True, "done": done, "failed": failed})


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
