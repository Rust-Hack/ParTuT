"""
server_admin.py — экран владельца: настройки магазина, цифры, журнал.

Три вещи, которые смотрит и крутит владелец поверх всего магазина сразу:
настройки (реквизиты, щедрость, сроки), статистика продаж и журнал действий
персонала.

Про настройки стоит помнить одно. Экран собирает их одним запросом, и у
каждой обязано быть значение по умолчанию: пустое поле владелец сохранит — и
сотрёт реквизиты по-настоящему. На этом уже спотыкались, поэтому умолчания
живут прямо здесь, рядом с чтением, а не разбросаны по коду.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin(), auth._super()).
"""

from flask import Blueprint, jsonify, request

import cache
import auth
import db
import shopinfo
import inputs
from config import CONFIRM_MINUTES, PAYMENT_INFO, is_super_admin

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("admin", __name__)

PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "all": None}
@bp.route("/api/admin/stats", methods=["POST"])
def api_admin_stats():
    """Бизнес-аналитика для админа за период: KPI, графики, товары, юзеры, монеты, склад, игры."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    # Период уходит в ключ кэша: списком или словарём он быть не может.
    period = inputs._text(data.get("period")) or "30d"
    # Тяжёлый расчёт (~15 запросов) — кэшируем на 60с. Сбрасывается при изменении заказов
    # (через _WRITE_PATHS), так что цифры остаются актуальными после реальных продаж.
    cached = cache.get(f"stats:{period}")
    if cached is not None:
        return jsonify({"ok": True, "stats": cached})

    days = PERIOD_DAYS.get(period, 30)
    stats = db.get_business_stats(days)          # всё считается в SQL

    products = db.get_all_products()             # склад — не зависит от периода
    stats["low_stock"] = [{"name": p["name"], "city": p["city"], "stock": p["stock"]}
                          for p in products if 0 < p["stock"] <= shopinfo.LOW_STOCK][:12]
    stats["out_stock"] = [{"name": p["name"], "city": p["city"]}
                          for p in products if p["stock"] <= 0][:12]
    stats["out_of_stock"] = sum(1 for p in products if p["stock"] <= 0)
    stats["products_total"] = len(products)
    stats["games"] = db.get_game_stats()
    stats["period"] = period
    try:
        # Во что обходится лояльность: сколько монет роздали и сколько из них
        # вернулось скидками. Считаем по летописи движений, а не по балансам —
        # потраченного на балансах уже нет, и раздача выглядела бы меньше.
        stats["coins"] = db.coin_flow(days)
        stats["coin_value"] = shopinfo.COIN_VALUE
    except Exception as e:
        stats["coins"] = {"granted": 0, "spent": 0, "by_reason": []}
        print(f"Не удалось посчитать движение монет: {e}")
    try:
        stats["losses"] = db.stock_losses(days)      # во сколько обошлись списания
    except Exception as e:
        stats["losses"] = []
        print(f"Не удалось посчитать списания: {e}")
    cache.put(f"stats:{period}", stats, 60)
    return jsonify({"ok": True, "stats": stats})


@bp.route("/api/admin/stats/reset", methods=["POST"])
def api_admin_stats_reset():
    """Сброс тестовой статистики (заказы + счётчики игр) — только супер-админ."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id") or not is_super_admin(int(user["id"])):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    res = db.reset_statistics()
    return jsonify({"ok": True, **res})


@bp.route("/api/admin/log", methods=["POST"])
def api_admin_log():
    """Кто и что менял. Остаток всегда писался в журнал движений, а цена,
    удаление товара и правка настроек не оставляли следа вовсе."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth._super(data):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    rows = db.list_admin_log(limit=150)
    return jsonify({"ok": True, "log": [{
        "who": r["admin_name"] or str(r["admin_id"] or ""),
        "admin_id": r["admin_id"],
        "action": r["action"] or "",
        "details": r["details"] or "",
        "at": r["created_at"] or "",
    } for r in rows]})


@bp.route("/api/admin/settings", methods=["POST"])
def api_admin_settings():
    """Текущие настройки магазина для админ-панели."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    # Все настройки — одним запросом. По одному ключу за раз это было восемь
    # походов в базу подряд ради восьми строк из одной маленькой таблицы.
    # Значения по умолчанию обязаны быть теми же, что у обычного чтения: на
    # незаполненной настройке экран должен показывать то, чем магазин и живёт,
    # а не пустоту. Пустое поле владелец сохранит — и сотрёт реквизиты
    # по-настоящему.
    opts = db.get_settings(
        ["payment_info", "confirm_minutes", "free_delivery_from", "remind_after_days",
         "remind_daily_cap", "coins_per_byn", "wheel_step", "referral_bonus",
         "compensation_max"],
        {"payment_info": PAYMENT_INFO, "confirm_minutes": CONFIRM_MINUTES,
         "free_delivery_from": 0, "remind_after_days": 21, "remind_daily_cap": 20,
         "coins_per_byn": 1, "wheel_step": db.WHEEL_STEP_DEFAULT,
         "referral_bonus": db.REFERRAL_BONUS,
         "compensation_max": db.COMPENSATION_MAX_DEFAULT})
    return jsonify({"ok": True, "settings": {
        "payment_info": opts["payment_info"] or "",
        "confirm_minutes": inputs._num(opts["confirm_minutes"], CONFIRM_MINUTES, as_int=True),
        "free_delivery_from": inputs._num(opts["free_delivery_from"], 0),
        "remind_after_days": inputs._num(opts["remind_after_days"], 21, as_int=True),
        "remind_daily_cap": inputs._num(opts["remind_daily_cap"], 20, as_int=True),
        # Щедрость программы лояльности. Раньше эти числа жили в коде, и любая
        # правка требовала выкладки новой версии.
        "coins_per_byn": inputs._num(opts["coins_per_byn"], 1.0),
        "wheel_step": inputs._num(opts["wheel_step"], db.WHEEL_STEP_DEFAULT),
        "referral_bonus": inputs._num(opts["referral_bonus"], db.REFERRAL_BONUS, as_int=True),
        # Потолок компенсации: сколько монет продавец может начислить покупателю
        # за раз. Раньше эта строка по ошибке стояла в экране бонусов покупателя,
        # а здесь её не было вовсе — поле в настройках всегда пустовало.
        "compensation_max": inputs._num(opts["compensation_max"], db.COMPENSATION_MAX_DEFAULT, as_int=True),
        "coin_value": shopinfo.COIN_VALUE,          # только для показа: менять нельзя, см. ниже
    }})


@bp.route("/api/admin/settings/update", methods=["POST"])
def api_admin_settings_update():
    """Сохранить настройки магазина (реквизиты оплаты, время подтверждения)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if "payment_info" in data:
        db.set_setting("payment_info", inputs._text(data.get("payment_info")))
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
    # --- Щедрость программы лояльности ---
    # Границы стоят не «на всякий случай»: 100 монет = 1 Br, и лишний ноль в
    # кэшбэке превращает 1% в 10% на каждом заказе. Цену монеты (shopinfo.COIN_VALUE)
    # намеренно НЕ отдаём в настройки: она задним числом меняет стоимость всех
    # уже накопленных балансов, а это не настройка, а переоценка обязательств.
    if "coins_per_byn" in data:
        try:
            db.set_setting("coins_per_byn", min(10.0, max(0.0, float(
                str(data.get("coins_per_byn") or 0).replace(",", ".")))))
        except (TypeError, ValueError):
            pass
    if "wheel_step" in data:
        try:
            # Ноль означал бы прокрут за каждую покупку — держим нижнюю границу.
            db.set_setting("wheel_step", min(100000.0, max(1.0, float(
                str(data.get("wheel_step") or 0).replace(",", ".")))))
        except (TypeError, ValueError):
            pass
    if "referral_bonus" in data:
        try:
            db.set_setting("referral_bonus", min(100000, max(0, int(data.get("referral_bonus")))))
        except (TypeError, ValueError):
            pass
    if "compensation_max" in data:
        try:
            # Ноль допустим и означает «продавцам компенсации запрещены».
            db.set_setting("compensation_max", min(100000, max(0, int(data.get("compensation_max")))))
        except (TypeError, ValueError):
            pass
    return jsonify({"ok": True})
