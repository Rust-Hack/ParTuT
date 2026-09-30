"""
partut/web/stock.py — движение склада: приход, списание, бой, пересчёт.

Пара к partut/db/stock.py: там сама запись движения одной транзакцией вместе с
остатком, здесь — ручки, которыми продавец это делает, и журнал движений.

Почему это отдельно от ассортимента: завести товар и изменить его остаток —
разные права. Ассортимент ведёт владелец, а списать разбитый под может
продавец своей точки, и каждое такое движение остаётся в журнале с именем.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin(), auth.deny_city()).
"""

import re

from flask import Blueprint, g, jsonify, request

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

# Ключ попытки придумывает экран: буквы, цифры, «-» и «_». Ключ другого вида —
# ошибка экрана, и молча работать без защиты от повтора хуже, чем отказать.
_КЛЮЧ = re.compile(r"[A-Za-z0-9_-]{8,64}")

# Отказы, которые означают «так нельзя», а не «сломалось»: их коды держим
# отдельно, чтобы экран не путал конфликт с кривыми данными.
_КОНФЛИКТЫ = {"stock_conflict", "orders_changed", "token_reused", "variant_missing"}


def _ключ(raw):
    """(ключ, беда): пустой ключ — None без беды; кривой — отказ."""
    if raw in (None, ""):
        return None, None
    s = str(raw)
    if not _КЛЮЧ.fullmatch(s):
        return None, {"ok": False, "error": "bad_token",
                      "message": "Экран прислал неверный ключ операции — обновите приложение."}
    return s, None


def _заказы(raw):
    """Номера заказов, отмеченных при пересчёте. None — если прислали не список чисел."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > 100:
        return None
    out = []
    for x in raw:
        n = inputs.целое(x)
        if n is None or n <= 0:
            return None
        out.append(n)
    return out


def _apply_move(admin, item, reason=None, token=None):
    """Одно движение склада — общая логика для одиночной ручки и пачки.

    reason=None значит «взять из item» (одиночная ручка); в пачке причина
    ОДНА на все позиции и передаётся снаружи — крупный завоз это всегда один
    и тот же приход, а не смесь причин. token — ключ попытки этой строки.
    Возвращает (код, тело ответа, товар для журнала или None).
    """
    try:
        pid = int(item.get("id"))
        qty = int(item.get("qty"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_number"}, None
    if qty < 0:
        return 400, {"ok": False, "error": "bad_number"}, None
    if qty > _MAX_STOCK_MOVE:
        return 400, {"ok": False, "error": "too_large",
                     "message": f"Больше {_MAX_STOCK_MOVE:,} шт.".replace(",", " ")
                                + " за одно движение — похоже на опечатку. Если это "
                                  "правда так много, запишите движение в несколько приёмов."}, None
    reason = reason or item.get("reason")
    if not isinstance(reason, str) or reason not in db.STOCK_REASONS:
        return 400, {"ok": False, "error": "bad_reason"}, None
    if reason != "fix" and qty <= 0:
        return 400, {"ok": False, "error": "bad_number"}, None
    товар = db.get_product(pid)
    if not товар:
        return 404, {"ok": False, "error": "not_found"}, None
    deny = auth.deny_product(admin, pid)
    if deny:
        # deny_product отдаёт готовый Flask-ответ (jsonify(...), код) — этот
        # помощник работает с (код, словарь), поэтому распаковываем обратно.
        ответ, код = deny
        return код, ответ.get_json(), None

    заказы = _заказы(item.get("counted_orders"))
    if заказы is None:
        return 400, {"ok": False, "error": "bad_input"}, None
    # Ответ человека на вопрос пересчёта: что именно посчитано (см. db.stock_operation).
    охват = item.get("counted_scope")
    if охват not in (None, "", "free", "orders"):
        return 400, {"ok": False, "error": "bad_input"}, None
    expected = item.get("expected")
    if expected is not None:
        expected = inputs.целое(expected)
        if expected is None:
            return 400, {"ok": False, "error": "bad_input"}, None
    cost = inputs.дробное(item.get("cost")) if item.get("cost") not in (None, "") else 0.0
    if cost is None or cost < 0:
        return 400, {"ok": False, "error": "bad_number",
                     "message": "Закупочная цена — неотрицательное число."}, None

    # Знак задаёт причина, а не клиент: иначе «брак» мог бы прийти с плюсом.
    # Пересчёт — продавец присылает РЕЗУЛЬТАТ, разницу считает база: считать
    # её на клиенте значит доверять чужой арифметике при записи на склад.
    try:
        итог = db.stock_operation(pid, reason, qty, flavor=inputs._text(item.get("flavor")) or None,
                                  cost=cost, note=inputs._text(item.get("note")),
                                  admin_id=int(admin["id"]), client_token=token,
                                  counted_orders=заказы, expected=expected, counted_scope=охват or None)
    except db.StockRefused as e:
        код = 404 if e.code == "not_found" else 409 if e.code in _КОНФЛИКТЫ else 400
        return код, {"ok": False, "error": e.code, "message": e.message, **e.extra}, None
    return 200, {"ok": True, **итог}, товар


def _строка_журнала(товар, reason, flavor, итог):
    """«Название» · точка · вариант: Приход +10 — то, что ищут в журнале глазами."""
    вариант = f" · {flavor}" if flavor else ""
    повтор = " (повтор, второй раз не записан)" if итог.get("replay") else ""
    посчитано = f" · насчитано с {итог['counted']} шт под заказы" if итог.get("counted") else ""
    return (f"«{товар['name']}» · {товар['city']}{вариант}: "
            f"{db.STOCK_REASONS.get(reason, reason)} {int(итог.get('delta') or 0):+d}{посчитано}{повтор}")


@bp.route("/api/admin/stock/move", methods=["POST"])
def api_admin_stock_move():
    """Приход, списание или пересчёт. Остаток меняется только так — тогда на
    любой вопрос «куда делось» есть ответ с именем и датой."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    token, беда = _ключ(data.get("client_token"))
    if беда:
        return jsonify(беда), 400
    код, тело, товар = _apply_move(admin, data, token=token)
    if товар is not None:
        g.log_note = _строка_журнала(товар, data.get("reason"), inputs._text(data.get("flavor")), тело)
    return jsonify(тело), код


@bp.route("/api/admin/stock/move/batch", methods=["POST"])
def api_admin_stock_move_batch():
    """Движение сразу по нескольким строкам одним запросом: поставка на десять
    позиций, пересчёт всех вкусов товара.

    Причина на всю пачку одна — крупный завоз это всегда один и тот же приход
    по разным позициям, а не смесь причин.

    Пачка НЕ атомарна, и это сказано прямо: каждая строка проводится отдельно,
    ответ говорит про каждую, что с ней стало. Одна ошибочная строка (опечатка,
    чужая точка, вкус, который успели убрать) не должна отменять девять
    правильных. У каждой строки свой ключ попытки: повтор пачки после
    потерянного ответа доводит только непроведённые строки, проведённые второй
    раз не записываются.

    Ответ: done — [{index, id, flavor, ...итог}], failed — {индекс: {error,
    message, id, flavor}}. Индекс — место строки в присланном списке: одна и
    та же позиция товара с разными вкусами иначе была бы неразличима."""
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
    if len(items) > 300:
        return jsonify({"ok": False, "error": "too_many",
                        "message": "Больше 300 строк за раз — разбейте поставку на части."}), 400
    общий, беда = _ключ(data.get("client_token"))
    if беда:
        return jsonify(беда), 400
    общая_заметка = inputs._text(data.get("note"))

    done, failed, журнал = [], {}, []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            failed[str(i)] = {"error": "bad_item"}
            continue
        # Ключ строки: свой, если экран его прислал, иначе производный от
        # ключа пачки и места строки. Свой надёжнее: строку можно сдвинуть.
        свой, беда = _ключ(item.get("token"))
        if беда:
            failed[str(i)] = {"error": "bad_token", "message": беда["message"],
                              "id": item.get("id"), "flavor": item.get("flavor") or ""}
            continue
        token = свой or (f"{общий}.{i}" if общий else None)
        строка = dict(item)
        if общая_заметка and not строка.get("note"):
            строка["note"] = общая_заметка
        _, тело, товар = _apply_move(admin, строка, reason=reason, token=token)
        вкус = inputs._text(item.get("flavor"))
        if тело.get("ok"):
            done.append({"index": i, "id": item.get("id"), "flavor": вкус,
                         **{k: v for k, v in тело.items() if k != "ok"}})
            if товар is not None:
                журнал.append(_строка_журнала(товар, reason, вкус, тело))
        else:
            failed[str(i)] = {**{k: v for k, v in тело.items() if k != "ok"},
                              "id": item.get("id"), "flavor": вкус}
    g.log_note = (f"Пачкой ({len(done)} из {len(items)}): " + "; ".join(журнал)
                  + (f" · не прошло {len(failed)}" if failed else ""))
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
    ответ = {"ok": True, "moves": moves, "reasons": db.STOCK_REASONS}
    if pid:
        # Невыданные заказы с этим товаром — для пересчёта: продавец отмечает,
        # чей товар он посчитал вместе со свободным (см. db.reserved_orders).
        ответ["reserved_orders"] = db.reserved_orders(pid)
    return jsonify(ответ)
