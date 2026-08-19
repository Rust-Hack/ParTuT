"""
server_orders.py — заказ от корзины до выдачи.

Самый ответственный путь магазина, и потому он собран в одном файле целиком:
оформление, чек, отмена покупателем, история заказов — и вторая половина,
управление заказом продавцом (подтвердить, выдать, отклонить, поправить состав).

Почему обе половины вместе, хотя одна для покупателя, а другая для продавца:
это один и тот же заказ и один и тот же денежный узел. Кэшбэк, прокрут колеса,
процент пригласившему и возврат товара на склад висят на смене статуса, а
защита от повторной отправки — на оформлении, и читать их порознь опаснее,
чем держать рядом.

Деньги здесь считаются один раз и в базе: место, где заказ действительно
возникает, — db.place_order(), одной транзакцией. Здесь же — только разбор
того, что прислало приложение, и человеческие ответы.

Помощники берутся ЧЕРЕЗ модуль (server.get_admin(), server._payment_info()),
а Flask, база и уведомления импортируются напрямую.
"""

import json

from flask import jsonify, request

import db
import notifications
import server
from config import admins_for_city

# Одно «заказ отклонён» на все случаи читалось одинаково и когда товара не
# оказалось, и когда не подошёл чек. Человек не понимает, что делать дальше,
# и либо уходит, либо пишет в чат — а продавец отвечает то же самое руками.
REJECT_REASONS = {
    "out": ("товара не оказалось в наличии",
            "Простите — товар разобрали раньше, чем мы успели отложить ваш. "
            "Монеты и оплата возвращены. Напишем, когда привезём снова."),
    "receipt": ("чек не подошёл",
                "Оплата по чеку не нашлась. Проверьте, что перевод прошёл, и оформите заказ снова "
                "— или пришлите чек нам в чат, разберёмся вместе."),
    "client": ("клиент передумал", "Заказ отменён по вашей просьбе. Ждём вас снова 🌿"),
    "duplicate": ("дубль заказа", "Это был повторный заказ — оставили один. Второй отменён."),
}

def _sold_out_message(gone, short):
    """Человеческое объяснение, почему заказ не оформился.

    Говорим названиями, а не кодами: «разобрали» и «осталось 2» покупатель
    понимает сразу, а «error: sold_out» — нет."""
    parts = []
    if gone:
        names = ", ".join(f"«{g['name']}»" for g in gone[:3])
        parts.append(f"{names} разобрали, пока вы оформляли заказ")
    for sh in short[:3]:
        parts.append(f"«{sh['name']}» осталось {sh['left']} шт")
    tail = "Мы поправили корзину — проверьте и оформите заново."
    return (". ".join(parts) + ". " + tail) if parts else tail


def _order_reply(order):
    """Ответ по заказу, который уже создан: повтор оформления должен привести
    человека ровно туда же — на экран оплаты или на «заказ принят»."""
    coins_used = int(order["coins_used"] or 0)
    discount = round(coins_used * server.COIN_VALUE, 2)
    fee = float(order["delivery_fee"] or 0)
    promo_off = float(order["promo_discount"] or 0)
    total = float(order["total"] or 0)
    # Обратный счёт от итога. Он неточен ровно в одном случае — когда скидка
    # перекрыла стоимость товаров целиком и итог упёрся в ноль; там это число
    # нужно только для показа.
    subtotal = round(max(0.0, total - fee) + discount + promo_off, 2)
    return {
        "ok": True,
        "order_id": int(order["id"]),
        "total": total,
        "subtotal": subtotal,
        "fee": fee,
        "coins_used": coins_used,
        "discount": discount,
        "delivery_method": order["delivery_method"] or "",
        "delivery_address": order["delivery_address"] or "",
        "payment_method": order["payment_method"] or "",
        "needs_receipt": (order["payment_method"] == "card") and not order["receipt_file_id"],
        "payment_info": server._payment_info(),
        "confirm_minutes": server._confirm_minutes(),
        "repeat": True,
    }


@server.app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(force=True, silent=True) or {}
    user = server.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401

    user_id = int(user["id"])
    username = user.get("username") or user.get("first_name") or str(user_id)

    # Тот же заказ, отправленный второй раз. Проверяем ДО всего остального:
    # первая отправка уже сняла товар со склада, и обычная проверка наличия
    # ответила бы «разобрали» на собственный же заказ человека.
    client_token = server._text(data.get("client_token"), 64)
    if client_token:
        prev = db.find_order_by_token(user_id, client_token)
        if prev:
            return jsonify(_order_reply(prev))

    # Разбираем корзину клиента (id + количество), чтобы одним запросом взять товары.
    raw_items = []
    # Корзина обязана быть списком словарей. Если пришло что-то другое — это не
    # наше приложение, и падать с 500 на этом незачем: просто нечего заказывать.
    for ri in (data.get("items") if isinstance(data.get("items"), list) else []):
        if not isinstance(ri, dict):
            continue
        try:
            pid, qty = int(ri.get("id")), int(ri.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if qty > 0:
            raw_items.append((pid, qty, server._text(ri.get("flavor")) or None))
    try:
        method_id = int(data.get("delivery_method_id"))
    except (TypeError, ValueError):
        method_id = None

    # ОДИН поход в базу за всем сразу: 18+, монеты, товары, вкусы, способ получения.
    ctx = db.get_checkout_data(user_id, [pid for pid, _, _ in raw_items], method_id)
    if not ctx["age_ok"]:
        return jsonify({"ok": False, "error": "age"}), 403

    # Цены и наличие берём из БАЗЫ, а не из того, что прислал клиент.
    items, total, cities = [], 0.0, set()
    gone, short = [], []          # разобрали совсем / осталось меньше, чем просят
    for pid, qty, flavor in raw_items:
        p = ctx["products"].get(pid)
        if not p:
            continue
        # Снятое с витрины не продаём, даже если оно осталось в чьей-то корзине
        # с прошлой недели: витрина — не единственная дверь в заказ.
        if "hidden" in p.keys() and p["hidden"]:
            continue
        if flavor:
            # товар-модель со вкусами: остаток берём у нужного варианта
            known = ctx["variants"].get(pid, {})
            if flavor not in known:
                continue      # такого вкуса у товара нет вовсе — это не «разобрали»
            avail = int(known.get(flavor) or 0)
            name = f"{p['name']} — {flavor}"
        else:
            avail = int(p["stock"] or 0)
            name = p["name"]
        # Раньше не хватило остатка — товар молча выбрасывали из заказа, а
        # количество молча урезали. Человек получал «Корзина пуста» при полной
        # корзине или платил за меньшее, чем собирался, и узнавал об этом только
        # у продавца. Теперь честно отказываем и говорим, что изменилось.
        if avail <= 0:
            gone.append({"id": pid, "flavor": flavor, "name": name})
            continue
        if qty > avail:
            short.append({"id": pid, "flavor": flavor, "name": name, "left": avail})
            continue
        real_qty = qty
        # Закупочную цену ЗАПОМИНАЕМ в заказе, а не смотрим потом в товаре:
        # завтра поставщик поднимет цену, и прибыль по прошлым продажам поедет.
        items.append({"id": pid, "flavor": flavor, "name": name, "price": p["price"],
                      "cost": round(float(p["cost"] or 0), 2), "qty": real_qty})
        total += p["price"] * real_qty
        cities.add(p["city"])

    # Что-то разобрали или осталось меньше — не оформляем втихую. Приложение по
    # этому ответу поправит корзину, покажет сообщение и даст оформить заново.
    if gone or short:
        server._cache_bust("products")
        return jsonify({"ok": False, "error": "sold_out",
                        "name": (gone or short)[0]["name"],
                        "gone": gone, "short": short,
                        "message": _sold_out_message(gone, short)}), 409
    if not items:
        return jsonify({"ok": False, "error": "empty"}), 400
    if len(cities) > 1:
        return jsonify({"ok": False, "error": "multi_city"}), 400

    city = cities.pop()
    subtotal = round(total, 2)

    # 1. Способ получения (доставка/самовывоз) — метод точки, взят вместе с товарами.
    method = ctx["method"]
    if not method or method["city"] != city:
        return jsonify({"ok": False, "error": "bad_delivery"}), 400
    address = server._text(data.get("delivery_address"))
    if method["needs_address"] and not address:
        return jsonify({"ok": False, "error": "no_address"}), 400
    # На доставку телефон обязателен. Курьер стоит у подъезда, а связь с
    # покупателем — только через Telegram, который может быть выключен: заказ
    # уезжает обратно, деньги и время потеряны.
    phone = server._text(data.get("phone"))
    if method["needs_address"] and len(server._digits(phone)) < 7:
        return jsonify({"ok": False, "error": "no_phone"}), 400
    # Точку самовывоза сверяем со списком города, а не берём на слово: иначе в
    # заказ попадёт любой текст, и продавец поедет по несуществующему адресу.
    # Условие простое: у способа не спрашивают адрес, а у города есть точки —
    # значит покупатель обязан выбрать одну из них. Отдельного переключателя
    # нет намеренно: он бы означал «функция есть, но её надо найти».
    points = ctx["points"] if not method["needs_address"] else []
    if points:
        try:
            point_id = int(data.get("pickup_point_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "no_point"}), 400
        point = next((p for p in points if p["id"] == point_id), None)
        if not point:
            return jsonify({"ok": False, "error": "bad_point"}), 400
        address = point["address"]
    fee = round(method["fee"] or 0, 2)
    # Порог бесплатной доставки считаем ЗДЕСЬ, а не верим клиенту: иначе сумму
    # доставки можно обнулить подделанным запросом. Смотрим на стоимость
    # товаров ДО скидки монетами — иначе покупатель дотягивается до порога
    # своими же монетами, а платим за доставку мы.
    free_from = server._free_delivery_from()
    if fee and free_from and subtotal >= free_from:
        fee = 0.0

    # 2. Способ оплаты. Если способу оплата не нужна (такси) — payment = none.
    if method["needs_payment"]:
        payment = data.get("payment_method")
        if payment not in ("card", "cash"):
            return jsonify({"ok": False, "error": "bad_payment"}), 400
    else:
        payment = "none"

    # 3. Сколько монет пробуем списать: 1 монета = COIN_VALUE Br, но не больше суммы товаров.
    #    round() убирает float-погрешность (25/0.01 = 2499.999…). Само списание — внутри
    #    транзакции place_order (атомарно, защищает от гонки и двойного клика).
    # 3а. Промокод. Скидку считает сервер — присланную сумму принимать нельзя.
    promo_code = server._text(data.get("promo_code")).upper()
    promo_discount = 0.0
    if promo_code:
        promo_discount, promo_err = db.check_promo(promo_code, user_id, subtotal)
        if promo_err:
            return jsonify({"ok": False, "error": promo_err}), 400

    spend = 0
    if data.get("use_coins") and subtotal > 0:
        # Монетами добираем ТО, ЧТО ОСТАЛОСЬ после промокода: иначе две скидки
        # вместе перекрывают стоимость товаров, и монеты сгорают впустую.
        left = max(0.0, subtotal - promo_discount)
        spend = min(ctx["coins"], int(round(left / server.COIN_VALUE)))

    # Карта → клиент грузит чек (статус 'new'). Наличные/такси → сразу продавцу,
    # но статус 'paid' = ЖДЁТ подтверждения продавца, а НЕ авто-подтверждается.
    needs_receipt = (payment == "card")

    # Заказ, монеты и склад — одной транзакцией (один commit вместо десятка).
    try:
        order_id, coins_used, total, repeat = db.place_order(
            user_id, username, city, items, subtotal, fee, server.COIN_VALUE, spend,
            method["name"], address, payment,
            server._text(data.get("comment")), phone,
            "new" if needs_receipt else "paid",
            promo_code, promo_discount, client_token)
    except db.PromoGone as e:
        # Код разобрали, пока человек оформлял. Молча оформить без скидки нельзя:
        # он согласился на одну сумму, а заплатил бы другую.
        msgs = {"promo_once": "Этот промокод уже использован на вашем аккаунте.",
                "promo_used_up": "Промокод закончился — его разобрали.",
                "promo_unknown": "Промокод больше не действует."}
        return jsonify({"ok": False, "error": "promo_gone",
                        "message": msgs.get(e.reason, "Промокод больше не действует.")
                                   + " Уберите его и оформите заказ заново."}), 409
    except db.OutOfStock as e:
        # Пока человек оформлял, последнюю штуку забрал кто-то другой. Лучше
        # честно сказать сейчас, чем продать то, чего нет, и отказывать при выдаче.
        server._cache_bust("products")
        return jsonify({"ok": False, "error": "sold_out", "name": e.name,
                        "message": f"«{e.name}» разобрали, пока вы оформляли заказ. "
                                   "Обновите корзину — остальное на месте."}), 409
    discount = round(coins_used * server.COIN_VALUE, 2)
    # Списание применения промокода переехало ВНУТРЬ транзакции заказа
    # (db._reserve_promo): отдельным походом в базу оно позволяло применить один
    # и тот же код несколько раз одновременными заказами.

    # Уведомления (продавцам + клиенту) — в фоне, чтобы «Оформить» отвечал сразу.
    #
    # Про НОВЫЙ заказ шлём всегда, не дожидаясь чека: заказ картой раньше ждал
    # чека, и если клиент нажимал «оплачу позже» или просто закрывал приложение,
    # продавец не узнавал о заказе никогда. Чек придёт следом отдельным
    # сообщением.
    #
    # Единственное исключение — повтор потерянного запроса: заказ уже создан, и
    # продавец о нём знает. Второе такое же сообщение он прочтёт как второй заказ.
    if not repeat:
        server._bg(_notify_new_order, order_id, user_id)

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "total": total,
        "subtotal": subtotal,
        "fee": fee,
        "coins_used": coins_used,
        "discount": discount,
        "delivery_method": method["name"],
        "delivery_address": address,
        "payment_method": payment,
        "needs_receipt": needs_receipt,
        "payment_info": server._payment_info(),
        "confirm_minutes": server._confirm_minutes(),
    })


def _сумма_платежа(сырое):
    """Сколько покупатель говорит, что перевёл. Пусто и мусор = «не сказал».

    Не отказываем и не спорим: человек уже заплатил, и потерять чек из-за
    придирки к полю хуже, чем принять его без суммы. Расхождение увидит
    продавец — решать ему.
    """
    try:
        значение = float(str(сырое or "").replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return round(значение, 2) if значение > 0 else None


@server.app.route("/api/receipt", methods=["POST"])
def api_receipt():
    """Принимает фото чека (файлом), подтверждает клиенту, шлёт заказ продавцу города."""
    init_data = request.form.get("initData", "")
    user = server.get_user(init_data)
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    user_id = int(user["id"])

    try:
        order_id = int(request.form.get("order_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_order"}), 400

    order = db.get_order(order_id)
    if not order or order["user_id"] != user_id:
        return jsonify({"ok": False, "error": "not_found"}), 404

    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    photo_bytes = file.read()

    # След платежа. Спрашиваем ровно два поля: сумму (она подставлена итогом,
    # покупателю остаётся подтвердить) и последние четыре цифры карты, с
    # которой платили. По этой паре строка банковской выписки находит свой
    # заказ — сегодня их не связывает ничего, кроме памяти продавца.
    сумма = _сумма_платежа(request.form.get("amount"))
    last4 = server._digits(request.form.get("last4") or "")[-4:]

    # Отправляем чек самому клиенту (подтверждение) — заодно получаем file_id,
    # который переиспользуем при отправке продавцу.
    try:
        msg = server.tg.send_photo(
            user_id, photo_bytes,
            caption=(f"🧾 Чек по заказу #{order_id} получен.\n"
                     f"Продавец подтвердит обычно за ~{server._confirm_minutes()} минут."),
        )
        file_id = msg.photo[-1].file_id
    except Exception as e:
        print(f"Не смог отправить чек клиенту {user_id}: {e}")
        file_id = None

    if file_id:
        db.set_order_receipt(order_id, file_id, сумма, last4)   # статус -> paid
        # Сам заказ продавец уже получил при оформлении — теперь только чек.
        server._bg(notifications.notify_receipt, server.tg, order_id)
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "send_failed"}), 500


@server.app.route("/api/order/cancel", methods=["POST"])
def api_order_cancel():
    """Клиент отменяет свой заказ ДО подтверждения продавцом (статус new/paid)."""
    data = request.get_json(force=True, silent=True) or {}
    user = server.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    try:
        oid = int(data.get("order_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    order = db.get_order(oid)
    if not order or order["user_id"] != int(user["id"]):
        return jsonify({"ok": False, "error": "not_found"}), 404
    if not db.cancel_order(oid, ["new", "paid"]):   # после подтверждения — только через продавца
        return jsonify({"ok": False, "error": "too_late"}), 400
    # сообщим продавцам города, чтобы не обрабатывали
    try:
        for admin_id in admins_for_city(order["city"]):
            server.tg.send_message(admin_id, f"❌ Клиент отменил заказ #{oid}.")
    except Exception as e:
        print(f"Не смог уведомить об отмене #{oid}: {e}")
    return jsonify({"ok": True})


@server.app.route("/api/orders", methods=["POST"])
def api_my_orders():
    """История заказов текущего клиента (для вкладки Профиль)."""
    data = request.get_json(force=True, silent=True) or {}
    user = server.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    orders = [_order_json(o, data.get("initData", "")) for o in db.get_orders_by_user(int(user["id"]))]
    # Реквизиты нужны и здесь: кто выбрал «оплачу позже», возвращается сюда, а
    # номер счёта видел один раз на экране оформления и больше нигде.
    return jsonify({"ok": True, "orders": orders, "payment_info": server._payment_info(),
                    "confirm_minutes": server._confirm_minutes()})


def _notify_new_order(order_id, user_id):
    """Побочные эффекты нового заказа: уведомить продавцов и клиента (вызывается в фоне)."""
    notifications.notify_sellers(server.tg, order_id)
    server._notify_client(user_id, _client_order_summary(order_id))


def _client_order_summary(order_id):
    """Сводка заказа для клиента (подтверждение оформления в чате)."""
    o = db.get_order(order_id)
    if not o:
        return None
    try:
        items = json.loads(o["items"])
    except (TypeError, ValueError):
        items = []
    lines = [f"🧾 Заказ #{o['id']} принят!", ""]
    for it in items:
        lines.append(f"• {it['name']} × {it['qty']}")
    method = o["delivery_method"] or ""
    if method:
        addr = o["delivery_address"] or ""
        lines.append("")
        lines.append(f"🚚 {method}" + (f": {addr}" if addr else ""))
    pm = {"card": "💳 картой", "cash": "💵 наличными", "none": "🚕 при получении"}.get(o["payment_method"] or "", "")
    if pm:
        lines.append(f"Оплата: {pm}")
    lines.append(f"💰 Итого: {o['total']:.2f} Br")
    lines.append("")
    # Клиент закрыл приложение, не приложив чек, — напоминаем прямо в чате,
    # иначе про недоплаченный заказ он вспомнит только когда позвонит продавец.
    if (o["payment_method"] or "") == "card" and not o["receipt_file_id"]:
        lines.append("⏳ Ждём фото чека: Профиль → Мои заказы → «Загрузить чек».")
        lines.append("")
    lines.append("Статус — в приложении: Профиль → Мои заказы. Уведомим об изменениях 🔔")
    return "\n".join(lines)


def _reward_referrer(buyer_id, order_total):
    """Начислить пригласившему % от заказа + бонус за первый заказ, уведомить его."""
    rr = db.reward_referrer_for_order(buyer_id, order_total)
    if rr and rr["earned"] > 0:
        extra = f" (+{rr['bonus']} 🪙 за первый заказ друга)" if rr["first"] else ""
        server._notify_client(rr["referrer"], f"🎉 Ваш реферал сделал заказ! +{rr['earned']} 🪙{extra}")


def _order_item_count(o):
    """Сколько единиц товара в заказе (для прогресса колеса)."""
    try:
        return sum(int(it.get("qty", 0)) for it in json.loads(o["items"]))
    except (TypeError, ValueError):
        return 0


def _order_subtotal(o):
    """Стоимость ТОЛЬКО товаров (без доставки) — база для кэшбэка."""
    try:
        return sum(float(it.get("price", 0)) * int(it.get("qty", 0)) for it in json.loads(o["items"]))
    except (TypeError, ValueError):
        return float(o["total"] or 0)


def _order_json(o, init_data=""):
    """Ссылка на чек выдаётся с коротким пропуском: без него картинку не отдадут.
    Пропуск получает только тот, кому этот заказ и так показывают."""
    try:
        items = json.loads(o["items"])
    except (TypeError, ValueError):
        items = []
    return {
        "id": o["id"],
        "user_id": o["user_id"],
        "username": o["username"] or "",
        "city": o["city"],
        "items": items,
        "total": o["total"],
        "pickup_time": o["pickup_time"] or "",
        "status": o["status"],
        "created_at": o["created_at"],
        "delivery_method": (o["delivery_method"] or ""),
        "delivery_address": (o["delivery_address"] or ""),
        "delivery_fee": round(o["delivery_fee"] or 0, 2),
        "payment_method": (o["payment_method"] or ""),
        "comment": (o["comment"] or "") if "comment" in o.keys() else "",
        "phone": (o["phone"] or "") if "phone" in o.keys() else "",
        "receipt_url": (f"/api/photo?file_id={o['receipt_file_id']}&t={server.photo_token(o['receipt_file_id'])}"
                        if o["receipt_file_id"] else None),
        # Скидки — чтобы правка состава показывала ту же сумму, что посчитает сервер.
        "promo_discount": round(o["promo_discount"] or 0, 2) if "promo_discount" in o.keys() else 0,
        "coins_discount": round(int(o["coins_used"] or 0) * server.COIN_VALUE, 2) if "coins_used" in o.keys() else 0,
        **_след_платежа(o),
    }


def _след_платежа(o):
    """Что покупатель сказал о переводе — и сошлось ли это с заказом.

    Расхождение не отказ и не обвинение: человек мог заплатить двумя
    переводами, ошибиться на копейку или оплатить не тот заказ. Но продавец
    обязан увидеть это ДО того, как выдаст товар, а не при пересчёте кассы.
    """
    ключи = o.keys()
    сумма = o["paid_amount"] if "paid_amount" in ключи else None
    last4 = (o["payer_last4"] or "") if "payer_last4" in ключи else ""
    итог = float(o["total"] or 0)
    # Копейка расхождения — не расхождение: суммы хранятся дробными числами.
    сошлось = None if сумма is None else abs(float(сумма) - итог) < 0.01
    return {"paid_amount": (round(float(сумма), 2) if сумма is not None else None),
            "payer_last4": last4,
            "payment_matches": сошлось}


@server.app.route("/api/admin/orders", methods=["POST"])
def api_admin_orders():
    """Заказы для админ-панели (новые сверху). Продавец города видит свои."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    orders = [o for o in db.get_orders() if server.may_city(admin, o["city"])]
    return jsonify({"ok": True, "orders": [_order_json(o, data.get("initData", "")) for o in orders]})


@server.app.route("/api/admin/today", methods=["POST"])
def api_admin_today():
    """Сводка дня для входа в управление: что ждёт и чем кончился день."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    scope = admin.get("city") or None
    out = db.seller_today(scope)
    products = [p for p in db.get_all_products() if not scope or p["city"] == scope]
    # Тот же порог, что в статистике и в фильтре товаров: три экрана с разным
    # понятием «мало» — это три разных ответа на один вопрос.
    out["out_stock"] = sum(1 for p in products if p["stock"] <= 0)
    out["low_stock"] = sum(1 for p in products if 0 < p["stock"] <= server.LOW_STOCK)
    out["city"] = scope or ""
    return jsonify({"ok": True, "today": out})


@server.app.route("/api/admin/order/status", methods=["POST"])
def api_admin_order_status():
    """Продавец меняет статус заказа из приложения (confirm / issued / reject)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        oid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400

    order = db.get_order(oid)
    if not order:
        return jsonify({"ok": False, "error": "not_found"}), 404
    deny = server.deny_city(admin, order["city"])
    if deny:
        return deny

    action = data.get("action")
    client_id = order["user_id"]
    OPEN = ["new", "paid", "confirmed"]        # состояния до выдачи/отмены (для отклонения)
    if action == "confirm":
        # подтвердить можно ТОЛЬКО оплаченный/готовый заказ (paid).
        # 'new' = карточный заказ без чека → сначала оплата, иначе нельзя.
        if not db.set_order_status_if(oid, "confirmed", ["paid"]):
            return jsonify({"ok": False, "error": "closed"}), 409
        msg = (f"✅ Оплата по заказу #{oid} подтверждена! Готовим к выдаче. Спасибо! 🌿"
               if order["payment_method"] == "card"
               else f"✅ Заказ #{oid} подтверждён! Готовим к выдаче. Спасибо! 🌿")
        server._bg(server._notify_client, client_id, msg)
    elif action == "issued":
        # выдать можно только оплаченный (paid) или уже подтверждённый (confirmed) заказ,
        # но НЕ 'new' (неоплаченный картой) — иначе кэшбэк без оплаты.
        if not db.set_order_status_if(oid, "issued", ["paid", "confirmed"]):   # применится один раз
            return jsonify({"ok": False, "error": "closed"}), 409
        db.add_coins(client_id, int(_order_subtotal(order) * db.coins_per_byn()), "cashback")
        # Прогресс колеса — от потраченного на ТОВАРЫ (без доставки), как и кэшбэк:
        # платить призами за дорогу магазину незачем.
        db.add_wheel_progress(client_id, _order_subtotal(order))
        _reward_referrer(client_id, order["total"])   # % и бонус пригласившему
        server._bg(server._notify_client, client_id, f"Заказ #{oid} выдан. Спасибо, что выбрали нас! 🙌")
    elif action == "reject":
        if not db.cancel_order(oid, OPEN):          # атомарно: canceled + возврат склада/монет
            return jsonify({"ok": False, "error": "closed"}), 409
        server._bg(server._notify_client, client_id, _reject_text(oid, data.get("reason"), data.get("note")))
    else:
        return jsonify({"ok": False, "error": "bad_action"}), 400
    return jsonify({"ok": True})


def _reject_text(oid, reason, note):
    """Что придёт покупателю. Причина — из списка, чтобы формулировку не
    сочиняли заново каждый раз, но приписку продавца тоже передаём."""
    head = f"Заказ #{oid} отклонён."
    body = REJECT_REASONS.get(reason, (None, None))[1] \
        or "Если это ошибка — напишите нам, разберёмся."
    tail = (note or "").strip()[:200]
    return "\n\n".join(x for x in (head, body, tail) if x)


@server.app.route("/api/admin/order/items", methods=["POST"])
def api_admin_order_items():
    """Продавец правит количества в заказе: «осталась одна» или «добавьте ещё»."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        oid = int(data.get("id"))
        quantities = {int(k): int(v) for k, v in (data.get("qty") or {}).items()}
    except (TypeError, ValueError, AttributeError):
        return jsonify({"ok": False, "error": "bad_data"}), 400
    order = db.get_order(oid)
    if not order:
        return jsonify({"ok": False, "error": "not_found"}), 404
    deny = server.deny_city(admin, order["city"])
    if deny:
        return deny

    updated, res = db.update_order_items(oid, quantities, server.COIN_VALUE)
    if not updated:
        err = str(res)
        if err.startswith("no_stock:"):
            _, name, have = err.split(":", 2)
            return jsonify({"ok": False, "error": "no_stock", "name": name, "have": int(have)}), 400
        code = 409 if err in ("closed",) else 400
        return jsonify({"ok": False, "error": err}), code

    lines = "\n".join(f"• {ch}" for ch in res)
    server._bg(server._notify_client, int(updated["user_id"]),
        f"Продавец изменил заказ #{oid}:\n{lines}\n\n💰 Итого: {updated['total']:.2f} Br")
    return jsonify({"ok": True, "order": _order_json(updated, data.get("initData", "")), "changes": res})
