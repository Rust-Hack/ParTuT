"""
partut/web/admin.py — экран владельца: настройки магазина, цифры, журнал.

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

from partut import cache
from partut.web import auth
from partut import db
from partut.web import shopinfo
from partut import inputs
from partut.config import CONFIRM_MINUTES, PAYMENT_INFO, is_super_admin

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


@bp.route("/api/admin/docs", methods=["POST"])
def api_admin_docs():
    """Прочитать или переписать оферту и политику обработки данных.

    Только владелец, не продавец точки: это документы магазина, а не настройка
    смены. Правка поднимает номер редакции, и он попадает в каждый следующий
    заказ — иначе через год не восстановить, с чем именно человек соглашался.
    """
    # Права проверяет общий страж: путь стоит в _OWNER_ONLY (partut/web/auth.py).
    # Своя проверка здесь была бы вторым местом, где живёт одно правило, — и
    # однажды они разошлись бы.
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    if "offer" not in data and "privacy" not in data:
        return jsonify({"ok": True, "docs": db.documents()})

    # Пустой текст — не «удалить», а промах: очищенное поле оставило бы магазин
    # без документов молча. Пусто пропускаем, о чём и говорим.
    оферта = inputs._text(data.get("offer"), 40000) if "offer" in data else None
    политика = inputs._text(data.get("privacy"), 40000) if "privacy" in data else None
    if (оферта is not None and not оферта) or (политика is not None and not политика):
        return jsonify({"ok": False, "error": "empty",
                        "message": "Пустой документ не сохраняем — текст обязан быть."}), 400

    редакция = db.set_documents(оферта, политика)
    cache.bust()
    db.log_admin_action(int(admin["id"]), admin.get("name", ""), "docs/update",
                        f"редакция {редакция}")
    return jsonify({"ok": True, "version": редакция, "docs": db.documents()})


# Реквизиты показываются покупателю на экране оплаты. Две тысячи знаков — это
# уже полстраницы: больше не бывает, а вот случайная вставка бывает.
PAYMENT_INFO_MAX = 2000


@bp.route("/api/admin/settings/update", methods=["POST"])
def api_admin_settings_update():
    """Сохранить настройки магазина (реквизиты оплаты, время подтверждения)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    применено = {}
    if "payment_info" in data:
        # Потолок длины не придирка: эта строка уходит КАЖДОМУ покупателю в
        # ответе на оформление и в истории заказов. Двести тысяч знаков туда
        # однажды попадут не злым умыслом, а случайной вставкой из буфера.
        реквизиты = inputs._text(data.get("payment_info"), PAYMENT_INFO_MAX)
        db.set_setting("payment_info", реквизиты)
        применено["payment_info"] = реквизиты

    # Границы стоят не «на всякий случай»: 100 монет = 1 Br, и лишний ноль в
    # кэшбэке превращает 1% в 10% на каждом заказе. Цену монеты
    # (shopinfo.COIN_VALUE) намеренно НЕ отдаём в настройки: она задним числом
    # меняет стоимость всех уже накопленных балансов, а это не настройка, а
    # переоценка обязательств.
    #
    # Где нижняя граница ноль — это осознанно, а не забывчивость:
    #   free_delivery_from = 0  — порога нет, доставка платная всегда;
    #   remind_daily_cap   = 0  — напоминания выключены целиком;
    #   compensation_max   = 0  — продавцам компенсации запрещены.
    # А вот wheel_step и confirm_minutes с нуля начинаться не могут: нулевой шаг
    # колеса означал бы прокрут за каждую покупку.
    ЧИСЛА = [
        # ключ,                 разбор,           минимум, максимум
        ("confirm_minutes",     inputs.целое,     1,       None),
        ("free_delivery_from",  inputs.дробное,   0.0,     None),
        ("remind_after_days",   inputs.целое,     1,       None),
        ("remind_daily_cap",    inputs.целое,     0,       None),
        ("coins_per_byn",       inputs.дробное,   0.0,     10.0),
        ("wheel_step",          inputs.дробное,   1.0,     100000.0),
        ("referral_bonus",      inputs.целое,     0,       100000),
        ("compensation_max",    inputs.целое,     0,       100000),
    ]
    for ключ, разбор, нижняя, верхняя in ЧИСЛА:
        if ключ not in data:
            continue
        значение = разбор(data.get(ключ), минимум=нижняя, максимум=верхняя)
        # None здесь — «прислали не число». Молча записать ноль было бы хуже
        # отказа: нулевой потолок компенсаций запрещает их совсем, и владелец
        # узнал бы об этом от продавца, а не от нас.
        if значение is not None:
            db.set_setting(ключ, значение)
            применено[ключ] = значение

    # Отдаём НЕ то, что прислали, а то, что легло. Границы тут прижимают молча:
    # владелец вводил 9999 кэшбэка, видел «Сохранено ✅» и уходил уверенный, что
    # так и есть, — а в базе лежала десятка. Ошибиться на порядок в проценте от
    # каждого заказа легко, а узнать об этом было неоткуда, кроме выручки через
    # неделю. Теперь приложение подставит в поля вернувшееся и скажет, что
    # именно поправлено.
    return jsonify({"ok": True, "applied": применено})
