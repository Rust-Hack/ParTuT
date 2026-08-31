"""
partut/web/customers.py — покупатель глазами магазина: монеты, рефералы, карточка.

Здесь всё, что магазин знает и делает с самим человеком, а не с его заказом:
баланс монет и реферальная ссылка (это видит покупатель), а также владельческая
часть — список покупателей, карточка, ручная правка баланса, разбор реферальных
связей и удаление человека из базы.

Одно важное про права. Начисления и правки баланса — это выдача денег, и они
намеренно закрыты жёстче остального: щедрость доступна только владельцу, а
продавцу оставлена одна щель — компенсация покупателю за испорченный заказ, с
потолком из настроек и только по своему городу. Проверяет это общий страж по
списку путей в server.py, а не эти ручки.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin(), inputs._text()), а Flask и
база импортируются напрямую.
"""

from flask import Blueprint, jsonify, request

from partut.web import auth
from partut import db
from partut.web import shopinfo
from partut.integrations import tgsend
from partut import inputs
from partut.config import is_super_admin

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("customers", __name__)
@bp.route("/api/bonus", methods=["POST"])
def api_bonus():
    """Бонусы клиента: баланс vapecoins, число приглашённых, реферальная ссылка."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    link = f"https://t.me/{tgsend.BOT_USERNAME}?start=ref{uid}" if tgsend.BOT_USERNAME else ""

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
                    # Ступени процента — с сервера, а не дублировать числа в JS:
                    # поменяются REFERRAL_TIERS в коде, полоска на экране не отстанет.
                    "ref_tiers": [{"from": м, "percent": п} for м, п in sorted(db.REFERRAL_TIERS)],
                    "referral_bonus": db.referral_bonus(),
                    "coin_value": shopinfo.COIN_VALUE})


@bp.route("/api/coins/history", methods=["POST"])
def api_coins_history():
    """Все движения монет покупателя одним списком — колесо, слот, кэшбэк,
    рефералы, компенсации. Раньше это было раскидано по разным экранам."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    rows = db.coin_history(int(user["id"]), 50)
    return jsonify({"ok": True, "history": [
        {"delta": r["delta"], "at": r["at"], "reason": db.COIN_REASONS.get(r["reason"], r["reason"])}
        for r in rows]})


@bp.route("/api/referral/history", methods=["POST"])
def api_referral_history():
    """Кому и сколько принёс каждый приглашённый — не только общая сумма
    ref_earned, а по датам и по друзьям."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    return jsonify({"ok": True, "history": db.referral_earnings(int(user["id"]), 50)})


@bp.route("/api/admin/grant", methods=["POST"])
def api_admin_grant():
    """Начислить пользователю монеты и/или прокруты колеса (по id). Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = inputs.целое(data.get("user_id"))
    if target is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    coins = spins = 0
    coins = inputs.целое(data.get("coins") or 0, 0)
    spins = inputs.целое(data.get("spins") or 0, 0)
    parts = []
    if coins:
        parts.append(f"{'убрать' if coins < 0 else 'начислить'} {abs(coins)} 🪙")
    if spins:
        parts.append(f"{'убрать' if spins < 0 else 'начислить'} {abs(spins)} прокрутов")
    summary = f"Пользователю id {target}: " + (", ".join(parts) if parts else "—")
    return auth._gate(admin, "grant", {"user_id": target, "coins": coins, "spins": spins}, summary)


@bp.route("/api/admin/order/compensate", methods=["POST"])
def api_admin_order_compensate():
    """Компенсация покупателю монетами по конкретному заказу.

    Единственное денежное действие, доступное продавцу, — и то через
    подтверждение владельца. Привязка к заказу не формальность: из него берутся
    и покупатель, и точка, поэтому продавец Турова не начислит ничего
    покупателю Минска, а владелец в заявке видит, о каком заказе речь.
    """
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        oid = int(data.get("order_id"))
        coins = int(data.get("coins"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_input"}), 400
    order = db.get_order(oid)
    if not order:
        return jsonify({"ok": False, "error": "not_found"}), 404
    denied = auth.deny_city(admin, order["city"])
    if denied:
        return denied
    cap = db.compensation_max()
    if coins < 1 or coins > cap:
        return jsonify({"ok": False, "error": "bad_amount",
                        "message": f"Компенсация — от 1 до {cap} 🪙 за раз."}), 400
    reason = inputs._text(data.get("reason"), 200)
    target = int(order["user_id"])
    summary = (f"Компенсация {coins} 🪙 покупателю id {target} по заказу #{oid}"
               + (f"\nПричина: {reason}" if reason else ""))
    return auth._gate(admin, "compensate",
                 {"user_id": target, "coins": coins, "order_id": oid, "reason": reason},
                 summary)


@bp.route("/api/admin/referrals", methods=["POST"])
def api_admin_referrals():
    """Список рефералов текущего админа (для управления/отвязки)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rows = db.list_referrals(int(admin["id"]))
    return jsonify({"ok": True, "referrals": [{"id": r["user_id"], "active": bool(r["ref_activated"])} for r in rows]})


@bp.route("/api/admin/coins/adjust", methods=["POST"])
def api_admin_coins_adjust():
    """Изменить баланс монет пользователя на delta (±). Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
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
    return auth._gate(admin, "coins_adjust", {"user_id": target, "delta": delta}, summary)


@bp.route("/api/admin/users", methods=["POST"])
def api_admin_users():
    """Список всех пользователей (поиск по id) — для админа (просмотр)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    users, total = db.list_users(str(data.get("search") or ""))
    for u in users:
        u["super"] = is_super_admin(u["id"])       # супер-админа фронт пометит и скроет кнопки
    return jsonify({"ok": True, "users": users, "total": total, "shown": len(users)})


@bp.route("/api/admin/customer", methods=["POST"])
def api_admin_customer():
    """Карточка покупателя: история заказов, суммы, любимые товары."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = inputs.целое(data.get("user_id"))
    if target is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    card = db.customer_card(target)
    if not card:
        return jsonify({"ok": False, "error": "not_found"}), 404
    card["super"] = is_super_admin(target)
    return jsonify({"ok": True, "card": card})


@bp.route("/api/admin/referral/unlink", methods=["POST"])
def api_admin_referral_unlink():
    """Отвязать конкретного реферала по его id. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = inputs.целое(data.get("user_id"))
    if target is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if is_super_admin(target):
        return jsonify({"ok": False, "error": "protected"}), 403     # супер-админа не трогаем
    return auth._gate(admin, "referral_unlink", {"user_id": target}, f"Отвязать реферала id {target}")


@bp.route("/api/admin/referral/clear", methods=["POST"])
def api_admin_referral_clear():
    """Отвязать ВСЕХ рефералов текущего админа. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    uid = int(admin["id"])
    return auth._gate(admin, "referral_clear", {"requester_id": uid}, f"Отвязать ВСЕХ рефералов админа id {uid}")


@bp.route("/api/admin/user/delete", methods=["POST"])
def api_admin_user_delete():
    """Полностью удалить пользователя по id. Обычный админ — через подтверждение."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = inputs.целое(data.get("user_id"))
    if target is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if target == int(admin["id"]):
        return jsonify({"ok": False, "error": "self"}), 400     # себя не удаляем
    if is_super_admin(target):
        return jsonify({"ok": False, "error": "protected"}), 403     # супер-админа удалить нельзя
    return auth._gate(admin, "user_delete", {"user_id": target}, f"Удалить пользователя id {target}")
