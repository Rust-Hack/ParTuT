"""
server_catalog.py — админка ассортимента: товары, модели, бренды, категории, фото.

Второй кусок, вынесенный из server.py. Здесь владелец ведёт ассортимент: что
магазин вообще продаёт (модели, бренды, категории) и что стоит на конкретной
точке (товары с ценой и остатком).

Граница прав проходит ровно посередине и потому важна: ассортимент — общий для
всех точек, им распоряжается владелец; цена и остаток на точке — дело продавца.
Проверяет это общий страж по списку путей в server.py, а не эти ручки.

Помощники берутся ЧЕРЕЗ модуль (server.get_admin(), server._text()), а Flask и
база импортируются напрямую — это внешние библиотеки, а не состояние сервера.
"""

import json

from flask import jsonify, request

import db
import server


@server.app.route("/api/admin/category", methods=["POST"])
def api_admin_category_add():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = server._text(data.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    code = db.add_category(name, data.get("emoji") or "")
    if not code:
        return jsonify({"ok": False, "error": "exists"}), 400
    return jsonify({"ok": True, "code": code})


@server.app.route("/api/admin/category/update", methods=["POST"])
def api_admin_category_update():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = server._text(data.get("code"))
    if code not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    sort = data.get("sort")
    db.update_category(code, name=data.get("name"), emoji=data.get("emoji"),
                       sort=(int(sort) if str(sort or "").strip().lstrip("-").isdigit() else None),
                       has_flavors=(bool(data.get("has_flavors")) if "has_flavors" in data else None))
    return jsonify({"ok": True})


@server.app.route("/api/admin/category/spec", methods=["POST"])
def api_admin_category_spec_add():
    """Добавить характеристику категории («Сопротивление, Ом» у расходников)."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    category = server._text(data.get("category"))
    if category not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    options = data.get("options")
    if isinstance(options, str):
        options = [o.strip() for o in options.split(",") if o.strip()]
    sid = db.add_category_spec(category, data.get("label") or "", data.get("unit") or "",
                               data.get("kind") or "text", options or None)
    if not sid:
        return jsonify({"ok": False, "error": "exists"}), 400
    return jsonify({"ok": True, "id": sid})


@server.app.route("/api/admin/category/spec/update", methods=["POST"])
def api_admin_category_spec_update():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        sid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    options = data.get("options")
    if isinstance(options, str):
        options = [o.strip() for o in options.split(",") if o.strip()]
    sort = data.get("sort")
    if not db.update_category_spec(sid, label=data.get("label"), unit=data.get("unit"),
                                   options=options,
                                   sort=(int(sort) if str(sort or "").strip().lstrip("-").isdigit() else None)):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@server.app.route("/api/admin/category/spec/delete", methods=["POST"])
def api_admin_category_spec_delete():
    """Убрать характеристику из категории. Значения у товаров остаются в базе:
    вернули поле — вернулись и они, а удалять чужие данные молча нельзя."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        sid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.delete_category_spec(sid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@server.app.route("/api/admin/category/delete", methods=["POST"])
def api_admin_category_delete():
    """Удалить можно только пустую категорию: иначе товары остались бы в разделе,
    которого нет, и пропали бы из витрины молча."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = server._text(data.get("code"))
    if code not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    used = db.count_products_in_category(code) + len(db.list_models(code))
    if used:
        # Считаем и модели: удалить категорию, оставив модели без полей и
        # раздела, значит потерять их описание молча.
        return jsonify({"ok": False, "error": "has_products", "count": used}), 400
    if len(db.category_codes()) <= 1:
        return jsonify({"ok": False, "error": "last_one"}), 400     # без категорий товар не завести
    db.delete_category(code)
    return jsonify({"ok": True})


@server.app.route("/api/admin/products", methods=["POST"])
def api_admin_products():
    """То же, но целиком — со снятыми с витрины. Только для админов."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    out = [p for p in server._all_products_payload() if server.may_city(admin, p["city"])]
    return jsonify({"ok": True, "products": out})


@server.app.route("/api/admin/product/specs", methods=["POST"])
def api_admin_product_specs():
    """Сохранить характеристики товара (все разом)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    p = db.get_product(pid)
    if not p:
        return jsonify({"ok": False, "error": "not_found"}), 404
    deny = server.deny_city(admin, p["city"])
    if deny:
        return deny
    _save_specs(pid, p["category"], data.get("specs"))
    return jsonify({"ok": True})


def _закупка(data):
    """Закупочная цена: заполнить обязаны, ноль — только осознанно.

    Раньше пустое поле молча означало ноль, и товар навсегда выпадал из
    подсчёта прибыли: отчёт занижал заработок, а решения о закупке
    принимались вслепую. Молчание тут дороже отказа.

    Ноль по-прежнему принимаем — подарок, образец, замена по гарантии
    бывают, — но только если его вписали руками, а не забыли поле.
    Возвращает (цена, ошибка).
    """
    сырое = data.get("cost")
    if сырое is None or str(сырое).strip() == "":
        return None, (jsonify({"ok": False, "error": "cost_required",
                               "message": "Впишите закупочную цену — без неё прибыль по этому "
                                          "товару не посчитается. Если закупки не было (подарок, "
                                          "образец), поставьте 0."}), 400)
    try:
        цена = float(str(сырое).replace(",", "."))
    except (TypeError, ValueError):
        return None, (jsonify({"ok": False, "error": "bad_number"}), 400)
    if цена < 0:
        return None, (jsonify({"ok": False, "error": "bad_number"}), 400)
    return цена, None


@server.app.route("/api/admin/product", methods=["POST"])
def api_admin_add():
    """Добавить товар из приложения."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    city = data.get("city")
    category = data.get("category")
    name = server._text(data.get("name"))
    if city not in db.location_names() or category not in db.category_codes() or not name:
        return jsonify({"ok": False, "error": "bad_data"}), 400
    try:
        price = float(str(data.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400
    cost, беда = _закупка(data)
    if беда:
        return беда

    is_hit = 1 if data.get("is_hit") else 0
    desc = server._text(data.get("description"))
    brand = server._text(data.get("brand"))
    strength = server._text(data.get("strength"))

    # Товар-модель со вкусами (одноразки/жидкости): список variants + свои поля.
    # Объём: у одноразок приходит как puffs (затяжки), у жидкостей как volume (мл).
    variants = data.get("variants")
    if isinstance(variants, list) and variants:
        vol = str(data.get("puffs") or data.get("volume") or "").strip()
        pid = db.add_product(city, category, name, max(0.0, price), 0, is_hit, desc,
                             brand=brand, flavor="", strength=strength, volume=vol, cost=cost)
        for v in variants:
            fl = str(v.get("flavor", "")).strip()
            try:
                st = int(v.get("stock", 0))
            except (TypeError, ValueError):
                st = 0
            if fl:
                db.add_variant(pid, fl, max(0, st))
        db.recalc_product_stock(pid)
        _save_specs(pid, category, data.get("specs"))
        return jsonify({"ok": True, "id": pid})

    # Обычный товар (одно количество, без вкусов).
    try:
        stock = int(data.get("stock"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_number"}), 400
    flavor = server._text(data.get("flavor"))
    volume = server._text(data.get("volume"))
    pid = db.add_product(city, category, name, max(0.0, price), max(0, stock), is_hit, desc,
                         brand=brand, flavor=flavor, strength=strength, volume=volume, cost=cost)
    _save_specs(pid, category, data.get("specs"))
    return jsonify({"ok": True, "id": pid})


@server.app.route("/api/admin/product/update", methods=["POST"])
def api_admin_update():
    """Изменить одно поле товара (price / stock / name / description / is_hit)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = server.deny_product(admin, pid)
    if deny:
        return deny

    field = data.get("field")
    raw = data.get("value")
    try:
        if field in ("price", "cost"):
            value = max(0.0, float(str(raw or 0).replace(",", ".")))
        elif field == "stock":
            value = max(0, int(raw))
        elif field in ("name", "description", "brand", "flavor", "strength", "volume"):
            value = str(raw).strip()
        elif field == "category":
            value = str(raw).strip()
            if value not in db.category_codes():
                return jsonify({"ok": False, "error": "bad_value"}), 400
        elif field == "city":
            value = str(raw).strip()
            names = {loc["name"] for loc in db.get_locations()}
            if value not in names:
                return jsonify({"ok": False, "error": "bad_value"}), 400
            # Перенос — это и есть смена точки: чужую нельзя ни как источник,
            # ни как цель, иначе товар уезжает туда, где продавец не отвечает.
            deny = server.deny_city(admin, value)
            if deny:
                return deny
            cur = db.get_product(pid)
            mid = (cur["model_id"] if cur and "model_id" in cur.keys() else None)
            if mid and value != cur["city"] and any(
                    p["city"] == value and p["id"] != pid
                    and (p["model_id"] if "model_id" in p.keys() else None) == mid
                    for p in db.get_all_products()):
                # Перенос на точку, где эта модель уже стоит, создал бы двойника.
                return jsonify({"ok": False, "error": "already_here"}), 400
        elif field in ("is_hit", "hidden"):
            value = 1 if raw else 0
        else:
            return jsonify({"ok": False, "error": "bad_field"}), 400
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_value"}), 400

    db.update_field(pid, field, value)
    return jsonify({"ok": True})


@server.app.route("/api/admin/product/variants", methods=["POST"])
def api_admin_variants():
    """Заменяет список вкусов товара целиком (добавить/убрать/изменить остаток)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = server.deny_product(admin, pid)
    if deny:
        return deny

    db.delete_variants(pid)
    for v in (data.get("variants") or []):
        fl = str(v.get("flavor", "")).strip()
        try:
            st = int(v.get("stock", 0))
        except (TypeError, ValueError):
            st = 0
        if fl:
            db.add_variant(pid, fl, max(0, st))
    db.recalc_product_stock(pid)
    return jsonify({"ok": True})


@server.app.route("/api/admin/product/delete", methods=["POST"])
def api_admin_delete():
    """Удалить товар."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = server.deny_product(admin, pid)
    if deny:
        return deny
    db.delete_variants(pid)
    db.delete_product(pid)
    return jsonify({"ok": True})


@server.app.route("/api/admin/photo", methods=["POST"])
def api_admin_photo():
    """Загрузить фото товара. Отправляем картинку админу (тихо), чтобы получить file_id."""
    init_data = request.form.get("initData", "")
    user = server.get_admin(init_data)
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        pid = int(request.form.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400

    try:
        msg = server.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото товара сохранено", disable_notification=True)
        file_id, thumb_id = server._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото товара: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500

    db.update_field(pid, "photo", file_id)
    db.update_field(pid, "photo_thumb", thumb_id)
    return jsonify({"ok": True})


@server.app.route("/api/admin/photo/add", methods=["POST"])
def api_admin_photo_add():
    """Добавить фото в галерею МОДЕЛИ (главное фото при этом не меняется)."""
    user = server.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(request.form.get("model_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    if len(db.model_photos(mid)) >= db.MAX_EXTRA_PHOTOS:
        # Проверяем ДО отправки в Telegram: иначе картинка уедет впустую.
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    try:
        msg = server.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото модели сохранено", disable_notification=True)
        file_id, thumb_id = server._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото модели: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500
    photo_id = db.add_model_photo(mid, file_id, thumb_id)
    if not photo_id:
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    return jsonify({"ok": True, "photo_id": photo_id})


@server.app.route("/api/admin/photo/delete", methods=["POST"])
def api_admin_photo_delete():
    """Убрать фото из галереи. Главное фото (id 0) так не удаляется — его заменяют."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        photo_id = int(data.get("photo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if photo_id <= 0:
        return jsonify({"ok": False, "error": "main_photo"}), 400
    return jsonify({"ok": True, "deleted": db.delete_product_photo(photo_id)})


@server.app.route("/api/admin/models", methods=["POST"])
def api_admin_models():
    """Ассортимент: что магазин вообще продаёт (независимо от наличия на точках)."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    models = db.list_models()
    for m in models:
        m["products"] = db.count_products_of_model(m["id"])
        m["gallery"] = [{"id": g["id"], "url": f"/api/photo?file_id={g['file_id']}",
                         "thumb": f"/api/photo?file_id={g['thumb_id'] or g['file_id']}"}
                        for g in db.model_photos(m["id"])]
        m["photo_url"] = f"/api/photo?file_id={m['photo']}" if m["photo"] else None
        m["thumb_url"] = f"/api/photo?file_id={m['photo_thumb'] or m['photo']}" if m["photo"] else None
    return jsonify({"ok": True, "models": models})


@server.app.route("/api/admin/model", methods=["POST"])
def api_admin_model_save():
    """Создать или изменить модель. Правка расходится по всем её товарам."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    category = server._text(data.get("category"))
    name = server._text(data.get("name"))
    if category not in db.category_codes() or not name:
        return jsonify({"ok": False, "error": "bad_data"}), 400
    specs = _clean_specs(category, data.get("specs"))
    flavors, seen = [], set()
    for f in (data.get("flavors") or []):
        f = str(f).strip()
        if f and f.lower() not in seen:
            seen.add(f.lower())
            flavors.append(f)
    mid = data.get("id")
    # Две одинаковые модели в одной категории — это раздвоенная витрина и
    # раздвоенная статистика: остатки и продажи разъедутся по двум карточкам.
    twin = next((m for m in db.list_models(category)
                 if m["name"].strip().lower() == name.lower()
                 and (m["brand"] or "").strip().lower() == server._text(data.get("brand")).lower()
                 and (not mid or int(m["id"]) != int(mid))), None)
    if twin:
        return jsonify({"ok": False, "error": "exists", "name": twin["name"]}), 400
    if mid:
        if not db.get_model(int(mid)):
            return jsonify({"ok": False, "error": "not_found"}), 404
        moved = db.update_model(int(mid), category=category, name=name, brand=data.get("brand") or "",
                                description=data.get("description") or "", specs=specs, flavors=flavors)
        # Вкус, убранный из модели, продолжает лежать и продаваться на точке.
        # Стирать остаток нельзя, но сказать об этом обязаны.
        return jsonify({"ok": True, "id": int(mid), "updated": moved,
                        "orphans": db.orphan_flavors(int(mid))})
    new_id = db.add_model(category, name, data.get("brand") or "", data.get("description") or "", specs, flavors)
    return jsonify({"ok": True, "id": new_id})


@server.app.route("/api/admin/model/hide", methods=["POST"])
def api_admin_model_hide():
    """Снять модель с витрины на всех точках сразу (или вернуть).

    «Больше не возим» — это не «этого не было»: удаление уносит остаток,
    историю движений и отзывы, а снятие оставляет всё на месте."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    hidden = bool(data.get("hidden"))
    return jsonify({"ok": True, "hidden": hidden, "count": db.hide_model_products(mid, hidden)})


@server.app.route("/api/admin/model/delete", methods=["POST"])
def api_admin_model_delete():
    """Убрать модель из ассортимента. Товары на точках остаются: их снимают
    с продажи отдельно, иначе одно нажатие обнуляло бы все точки разом."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    used = db.count_products_of_model(mid)
    if used and not data.get("force"):
        return jsonify({"ok": False, "error": "has_products", "count": used}), 400
    if not db.delete_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "count": used})


@server.app.route("/api/admin/model/photo", methods=["POST"])
def api_admin_model_photo():
    """Фото модели — оно же появляется у всех её товаров на точках."""
    user = server.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        # Форма шлёт id, программные вызовы — model_id: принимаем оба, чтобы
        # фото не терялось из-за названия поля.
        mid = int(request.form.get("model_id") or request.form.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    try:
        msg = server.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото модели сохранено", disable_notification=True)
        file_id, thumb_id = server._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото модели: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500
    db.set_model_photo(mid, file_id, thumb_id)
    return jsonify({"ok": True})


@server.app.route("/api/admin/product/from-model", methods=["POST"])
def api_admin_product_from_model():
    """Завоз: модель появляется на точке с ценой и остатком."""
    data = request.get_json(force=True, silent=True) or {}
    admin = server.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("model_id"))
        price = float(str(data.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_data"}), 400
    m = db.get_model(mid)
    city = server._text(data.get("city"))
    if not m or city not in db.location_names():
        return jsonify({"ok": False, "error": "bad_data"}), 400
    # Завозить на свою точку продавец вправе — это его работа. На чужую нет.
    deny = server.deny_city(admin, city)
    if deny:
        return deny
    # Один товар на точке — одна запись. Иначе на витрине две одинаковые
    # карточки с разными остатками, и продавец не знает, какую вести.
    if any(p["city"] == city and (p["model_id"] if "model_id" in p.keys() else None) == mid
           for p in db.get_all_products()):
        return jsonify({"ok": False, "error": "already_here"}), 400
    if price <= 0:
        return jsonify({"ok": False, "error": "bad_price"}), 400
    cost, беда = _закупка(data)
    if беда:
        return беда
    variants = data.get("variants") if isinstance(data.get("variants"), list) else []
    try:
        stock = max(0, int(data.get("stock") or 0))
    except (TypeError, ValueError):
        stock = 0
    pid = db.add_product_from_model(mid, city, max(0.0, price), cost,
                                    0 if variants else stock, 1 if data.get("is_hit") else 0)
    if variants:
        for v in variants:
            fl = str(v.get("flavor", "")).strip()
            try:
                st = max(0, int(v.get("stock", 0)))
            except (TypeError, ValueError):
                st = 0
            if fl:
                db.add_variant(pid, fl, st)
        db.recalc_product_stock(pid)
    return jsonify({"ok": True, "id": pid})


@server.app.route("/api/admin/brand", methods=["POST"])
def api_admin_brand():
    """Создать или обновить бренд (если пришёл id — обновляем)."""
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = server._text(data.get("name"))
    # Пустая категория — бренд общий: Vaporesso делает и поды, и картриджи,
    # и заводить его в каждой категории заново незачем.
    category = server._text(data.get("category"))
    if not name or (category and category not in db.category_codes()):
        return jsonify({"ok": False, "error": "bad_data"}), 400
    # Вкусы храним без повторов и лишних пробелов: «Мята» и «мята » в фильтре
    # выглядели бы как два разных вкуса.
    flavors, seen = [], set()
    for f in (data.get("flavors") or []):
        f = str(f).strip()
        if f and f.lower() not in seen:
            seen.add(f.lower())
            flavors.append(f)

    bid = data.get("id")
    twin = db.find_brand_by_name(name, except_id=bid)
    if twin:
        return jsonify({"ok": False, "error": "exists", "name": twin["name"]}), 400
    if bid:
        old = db.get_brand(int(bid))
        if not old:
            return jsonify({"ok": False, "error": "not_found"}), 404
        db.update_brand(int(bid), name, category, flavors)
        # Товар хранит бренд строкой: без переноса у него осталось бы старое имя,
        # и в фильтре каталога появился бы бренд, которого в справочнике нет.
        moved = db.rename_brand_in_products(old["name"], name)
        return jsonify({"ok": True, "id": int(bid), "moved": moved})
    new_id = db.add_brand(name, category, flavors)
    return jsonify({"ok": True, "id": new_id})


@server.app.route("/api/admin/brand/delete", methods=["POST"])
def api_admin_brand_delete():
    data = request.get_json(force=True, silent=True) or {}
    if not server.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        bid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_id"}), 400
    b = db.get_brand(bid)
    if not b:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # У товаров бренд записан строкой и после удаления справочника никуда не
    # денется — молча оставлять «ничей» бренд в фильтре нельзя, поэтому
    # предупреждаем и требуем подтверждения.
    used = db.count_products_of_brand(b["name"]) + sum(1 for m in db.list_models() if m["brand"] == b["name"])
    if used and not data.get("force"):
        return jsonify({"ok": False, "error": "has_products", "count": used}), 400
    db.delete_brand(bid)
    return jsonify({"ok": True, "count": used})


# --- Бренды, вкусы и характеристики ---
# Приехали из server.py последними: это тот же ассортимент, только
# читаемый покупателем, и держать его отдельно было незачем.

def _save_specs(product_id, category, values):
    """Пишет только те характеристики, которые заведены у этой категории.

    Иначе в товар попало бы что угодно из запроса, и карточка бы показывала
    поля, которых в категории нет."""
    if not isinstance(values, dict):
        return
    allowed = {s["key"] for s in db.list_category_specs(category)}
    clean = {k: v for k, v in values.items() if k in allowed}
    if clean:
        db.set_product_specs(product_id, clean)


@server.app.route("/api/brands")
def api_brands():
    category = server._text(request.args.get("category")) or None
    key = f"brands:{category or 'all'}"
    cached = server._cache_get(key)
    if cached is not None:
        return server._json_etag(cached)
    out = []
    for b in db.get_brands(category):
        try:
            flavors = json.loads(b["flavors"] or "[]")
        except Exception:
            flavors = []
        out.append({"id": b["id"], "name": b["name"], "category": b["category"] or "", "flavors": flavors})
    return server._json_etag(server._cache_set(key, out, 300))


@server.app.route("/api/flavors")
def api_flavors():
    """Все вкусы, которые уже встречались — для подсказок при вводе.
    Без них одна и та же «Мята» набирается по-разному и дробит фильтр."""
    cached = server._cache_get("flavors")
    if cached is None:
        cached = server._cache_set("flavors", db.known_flavors(), 300)
    return server._json_etag(cached)


def _clean_specs(category, values):
    """Оставляет только характеристики, заведённые у этой категории."""
    if not isinstance(values, dict):
        return {}
    allowed = {s["key"] for s in db.list_category_specs(category)}
    return {k: str(v).strip() for k, v in values.items() if k in allowed and str(v).strip() != ""}
