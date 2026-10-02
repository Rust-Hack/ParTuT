"""
partut/web/catalog.py — админка ассортимента: товары, модели, бренды, категории, фото.

Второй кусок, вынесенный из server.py. Здесь владелец ведёт ассортимент: что
магазин вообще продаёт (модели, бренды, категории) и что стоит на конкретной
точке (товары с ценой и остатком).

Граница прав проходит ровно посередине и потому важна: ассортимент — общий для
всех точек, им распоряжается владелец; цена и остаток на точке — дело продавца.
Проверяет это общий страж по списку путей в server.py, а не эти ручки.

Помощники берутся ЧЕРЕЗ модуль (auth.get_admin(), inputs._text()), а Flask и
база импортируются напрямую — это внешние библиотеки, а не состояние сервера.
"""

import hashlib
import json
import re

from flask import Blueprint, g, jsonify, request

from partut import cache
from partut.web import auth
from partut import db
from partut.web import photos
from partut.integrations import tgsend
from partut import inputs

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("catalog", __name__)


def _all_products_payload():
    """Полный список товаров (все точки). Кэш 30с — витрина открывается без похода в базу.
    Заказ всё равно проверяет остаток по живой базе, так что кратковременный лаг склада не опасен."""
    cached = cache.get("products")
    if cached is not None:
        return cached
    variants_by = {}
    for v in db.get_all_variants():
        variants_by.setdefault(v["product_id"], []).append({"flavor": v["flavor"], "stock": v["stock"]})
    try:
        waiting = db.stock_alert_counts()
    except Exception as e:
        waiting = {}                      # счётчик — не повод ронять витрину
        print(f"Не удалось посчитать ожидающих: {e}")
    try:
        favored = db.favorite_counts()
    except Exception as e:
        favored = {}                      # счётчик — не повод ронять витрину
        print(f"Не удалось посчитать избранное: {e}")
    try:
        ratings = db.product_ratings()
    except Exception as e:
        ratings = {}                      # без оценок витрина живёт
        print(f"Не удалось прочитать оценки товаров: {e}")
    gallery, model_gallery = {}, {}
    try:
        for ph in db.all_product_photos():
            gallery.setdefault(ph["product_id"], []).append(ph)
        for ph in db.all_model_photos():
            model_gallery.setdefault(ph["model_id"], []).append(ph)
    except Exception as e:
        print(f"Не удалось прочитать галерею товаров: {e}")   # без галереи витрина живёт
    out = []
    for p in db.get_all_products():
        # Главное фото всегда первое: покупатель видит ту же картинку, что и в каталоге.
        photos = ([{"id": 0, "url": f"/api/photo?file_id={p['photo']}",
                    "thumb": f"/api/photo?file_id={p['photo_thumb'] or p['photo']}"}] if p["photo"] else [])
        mid = p["model_id"] if "model_id" in p.keys() else None
        # Галерея — свойство модели; у товаров, заведённых до неё, остаётся своя.
        extra = model_gallery.get(mid) if mid else gallery.get(p["id"], [])
        for ph in (extra or []):
            photos.append({"id": ph["id"], "url": f"/api/photo?file_id={ph['file_id']}",
                           "thumb": f"/api/photo?file_id={ph['thumb_id'] or ph['file_id']}"})
        out.append({
            "photos": photos,
            "rating": ratings.get(p["id"], {"avg": 0, "count": 0}),
            "id": p["id"], "name": p["name"], "price": p["price"],
            # Ссылка на модель из «Ассортимента»: у товара, заведённого по ней,
            # описание правится там, а здесь остаются цена, закупка и остаток.
            "model_id": p["model_id"] if "model_id" in p.keys() else None,
            "stock": p["stock"], "is_hit": p["is_hit"],
            # Номер версии цены: экран управления шлёт его с правкой цены,
            # и сервер не пропустит застрявший старый запрос поверх нового.
            "price_rev": int(p["price_rev"] or 0) if "price_rev" in p.keys() else 0,
            "category": p["category"], "city": p["city"],
            "description": p["description"] or "",
            "cost": round(float(p["cost"] or 0), 2),   # видит только админка
            "brand": p["brand"] or "", "flavor": p["flavor"] or "",
            "strength": p["strength"] or "", "volume": p["volume"] or "",
            # Характеристики своей категории: сопротивление у картриджа,
            # мощность и аккумулятор у пода.
            "specs": db.product_specs(p),
            "variants": variants_by.get(p["id"], []),
            # Снят с витрины: в каталог такой товар не попадает вовсе, но
            # остаток, история и отзывы при нём остаются.
            "hidden": bool(p["hidden"]) if "hidden" in p.keys() else False,
            # Сколько человек ждут поступления — админу видно, что завозить.
            "waiting": waiting.get(p["id"], 0),
            # Сколько раз товар в избранном — раньше это видел только браузер
            # покупателя (localStorage), владелец не видел спрос вовсе.
            "favored": favored.get(p["id"], 0),
            "photo_url": (f"/api/photo?file_id={p['photo']}" if p["photo"] else None),
            # Для сетки каталога — копия поменьше. У старых товаров её нет, тогда
            # отдаём полноразмерную: витрина в любом случае что-то покажет.
            "thumb_url": (f"/api/photo?file_id={p['photo_thumb'] or p['photo']}" if p["photo"] else None),
        })
    return cache.put("products", out, 30)


# --- Витрина покупателя ---
# Живёт здесь же: и витрина, и админский список собираются из одного
# _all_products_payload(), и держать их порознь значило бы тянуть его через
# server и снова замкнуть круг импортов.
@bp.route("/api/products")
def api_products():
    """Витрина покупателя. Снятое с продажи сюда не попадает — не полагаемся на
    то, что каждый экран приложения не забудет его отфильтровать.

    Закупочная цена вырезается здесь же: она лежала в том же ответе, что и
    витрина, и любой покупатель мог прочитать, почём мы берём товар."""
    city = inputs._text(request.args.get("city")) or None
    out = [_public_product(p) for p in _all_products_payload() if not p["hidden"]]
    if city:
        out = [p for p in out if p["city"] == city]
    return cache.json_etag(out)


def _public_product(p):
    return {k: v for k, v in p.items() if k not in _ADMIN_ONLY_FIELDS}


_ADMIN_ONLY_FIELDS = ("cost", "waiting", "favored", "hidden", "price_rev")


@bp.route("/api/admin/category", methods=["POST"])
def api_admin_category_add():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = inputs._text(data.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    code = db.add_category(name, data.get("emoji") or "")
    if not code:
        return jsonify({"ok": False, "error": "exists"}), 400
    return jsonify({"ok": True, "code": code})


@bp.route("/api/admin/category/update", methods=["POST"])
def api_admin_category_update():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = inputs._text(data.get("code"))
    if code not in db.category_codes():
        return jsonify({"ok": False, "error": "not_found"}), 404
    sort = data.get("sort")
    ok = db.update_category(code, name=data.get("name"), emoji=data.get("emoji"),
                       sort=(int(sort) if str(sort or "").strip().lstrip("-").isdigit() else None),
                       has_flavors=(bool(data.get("has_flavors")) if "has_flavors" in data else None),
                       variant_label=(inputs._text(data.get("variant_label")) if "variant_label" in data else None),
                       variant_label2=(inputs._text(data.get("variant_label2")) if "variant_label2" in data else None))
    if not ok:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    return jsonify({"ok": True})


@bp.route("/api/admin/category/spec", methods=["POST"])
def api_admin_category_spec_add():
    """Добавить характеристику категории («Сопротивление, Ом» у расходников)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    category = inputs._text(data.get("category"))
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


@bp.route("/api/admin/category/spec/update", methods=["POST"])
def api_admin_category_spec_update():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    sid = inputs.целое(data.get("id"))
    if sid is None:
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


@bp.route("/api/admin/category/spec/delete", methods=["POST"])
def api_admin_category_spec_delete():
    """Убрать характеристику из категории. Значения у товаров остаются в базе:
    вернули поле — вернулись и они, а удалять чужие данные молча нельзя."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    sid = inputs.целое(data.get("id"))
    if sid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.delete_category_spec(sid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/admin/category/restore", methods=["POST"])
def api_admin_category_restore():
    """Вернуть стартовые категории, которых сейчас нет.

    Засев работает один раз за жизнь базы: удалённое не возвращается само. Это
    правильно, но способ вернуть его осознанно нужен — иначе единственным
    выходом остаётся правка базы руками.
    """
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    добавлены = db.restore_seed_categories()
    return jsonify({"ok": True, "added": добавлены})


@bp.route("/api/admin/category/delete", methods=["POST"])
def api_admin_category_delete():
    """Удалить можно только пустую категорию: иначе товары остались бы в разделе,
    которого нет, и пропали бы из витрины молча."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    code = inputs._text(data.get("code"))
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


@bp.route("/api/admin/products", methods=["POST"])
def api_admin_products():
    """То же, но целиком — со снятыми с витрины. Только для админов."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    # Сколько обещано в невыданных заказах — рядом с остатком. Остаток в базе
    # показывает то, что можно продать, а продавцу у полки нужно и второе
    # число: на полке-то лежит больше, но часть уже чужая. Считаем свежим, мимо
    # кэша витрины, и складываем в НОВЫЕ словари: кэш общий с покупательской
    # витриной, и дописать в него — значило бы показать это покупателям.
    try:
        резерв = db.reserved_stock()
    except Exception as e:
        резерв = {}                     # без этого числа список всё равно нужен
        print(f"Не удалось посчитать товар в невыданных заказах: {e}")
    out = []
    for p in _all_products_payload():
        if not auth.may_city(admin, p["city"]):
            continue
        варианты = [dict(v, reserved=резерв.get((p["id"], v["flavor"]), 0)) for v in p["variants"]]
        всего = (sum(v["reserved"] for v in варианты) if варианты else резерв.get((p["id"], ""), 0))
        out.append(dict(p, variants=варианты, reserved=всего))
    return jsonify({"ok": True, "products": out})


@bp.route("/api/admin/product/specs", methods=["POST"])
def api_admin_product_specs():
    """Сохранить характеристики товара (все разом)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    p = db.get_product(pid)
    if not p:
        return jsonify({"ok": False, "error": "not_found"}), 404
    deny = auth.deny_city(admin, p["city"])
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
    цена = inputs.дробное(сырое)
    if цена is None:
        return None, (jsonify({"ok": False, "error": "bad_number"}), 400)
    if цена < 0:
        return None, (jsonify({"ok": False, "error": "bad_number"}), 400)
    return цена, None


@bp.route("/api/admin/product", methods=["POST"])
def api_admin_add():
    """Прежний путь «завести товар» — закрыт.

    Он заводил товар мимо ассортимента — без модели, вторым сортом, который
    потом приходится вести отдельными формами, — и ставил остаток числом, мимо
    истории склада. Экран им давно не пользуется: новый товар заводится через
    «✨ Новый товар» (/api/admin/product/publish), уже заведённый —
    «📥 Завезти на точку» (/api/admin/product/from-model)."""
    return jsonify({"ok": False, "error": "gone",
                    "message": "Новый товар заводится кнопкой «✨ Новый товар» в «🛍 Товарах». "
                               "Если видите это сообщение — закройте приложение и откройте заново."}), 410


def _проверить_поле(admin, pid, field, raw):
    """Проверяет одно поле товара. Возвращает (значение, ответ-отказ).

    Вынесено из ручки, чтобы одна и та же проверка работала и для одного поля,
    и для пачки: правило, продублированное для «быстрого пути», однажды
    разойдётся с медленным — и дыра появится ровно там, где её не ищут.
    """
    try:
        if field in ("price", "cost"):
            # Раньше здесь стоял max(0, ...) — и «-50» молча становилось нулём,
            # а ручка отвечала «сохранено». Товар уезжал на витрину бесплатным,
            # и узнать об этом было неоткуда: в ответе ошибки нет, в списке
            # стоит 0.00, будто так и задумано. Отказ дешевле молчания.
            value = float(str(raw or 0).replace(",", "."))
            if value < 0:
                return None, ("bad_value", "Цена не может быть отрицательной.")
            if field == "price" and value == 0:
                return None, ("bad_value", "Цена должна быть больше нуля.")
        elif field == "stock":
            # Остаток — не поле карточки, а итог движений склада. Правка числом
            # шла мимо истории, а у товара с вариантами ещё и сбивала итог.
            # Страницы, открытые до обновления, ещё пришлют его — отвечаем
            # понятным отказом, остальные поля сохраняются.
            return None, ("use_stock_moves",
                          "Остаток меняется только в «Складе»: приход, списание или пересчёт — "
                          "так каждое изменение остаётся в истории. Обновите приложение.")
        elif field == "name":
            value = str(raw).strip()
            if not value:
                return None, ("bad_value", "Название не может быть пустым.")
        elif field in ("description", "brand", "flavor", "strength", "volume"):
            value = str(raw).strip()
        elif field == "category":
            value = str(raw).strip()
            if value not in db.category_codes():
                return None, ("bad_value", None)
        elif field == "city":
            value = str(raw).strip()
            names = {loc["name"] for loc in db.get_locations()}
            if value not in names:
                return None, ("bad_value", None)
            # Перенос — это и есть смена точки: чужую нельзя ни как источник,
            # ни как цель, иначе товар уезжает туда, где продавец не отвечает.
            if not auth.may_city(admin, value):
                return None, ("other_city", "Это другая точка — её ведёт другой продавец.")
            cur = db.get_product(pid)
            mid = (cur["model_id"] if cur and "model_id" in cur.keys() else None)
            if mid and value != cur["city"] and any(
                    p["city"] == value and p["id"] != pid
                    and (p["model_id"] if "model_id" in p.keys() else None) == mid
                    for p in db.get_all_products(include_archived=True)):
                # Перенос на точку, где эта модель уже стоит (или лежит в
                # архиве), создал бы двойника.
                return None, ("already_here", "На этой точке товар уже есть.")
        elif field in ("is_hit", "hidden"):
            value = 1 if raw else 0
        else:
            return None, ("bad_field", None)
    except (TypeError, ValueError):
        return None, ("bad_value", None)
    return value, None

@bp.route("/api/admin/product/update", methods=["POST"])
def api_admin_update():
    """Изменить поля товара. Можно одно (field/value) или сразу пачкой (fields).

    Пачкой — потому что сохранение карточки меняло до десяти полей и слало на
    каждое отдельный запрос. По мобильной сети это десять полных обменов с
    сервером подряд: секунда на каждый, и «Сохраняю…» висит десять секунд. Одно
    поле по-прежнему принимается — им пользуются переключатели в списке.
    """
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny

    пачка = data.get("fields")
    if not isinstance(пачка, dict):
        пачка = {data.get("field"): data.get("value")}
        одиночное = True
    else:
        одиночное = False

    отказы = {}
    приняты = {}
    for field, raw in пачка.items():
        value, беда = _проверить_поле(admin, pid, field, raw)
        if беда:
            отказы[field] = {"error": беда[0], "message": беда[1]}
        else:
            приняты[field] = value

    # expected — каким человек видел каждое поле, открывая карточку. Если с
    # тех пор поле успел поменять кто-то другой (второй продавец, владелец с
    # телефона), его НЕ перезаписываем: молча затереть чужую цену — та же
    # беда, что затереть чужую продажу. Остальные поля сохраняются, а про
    # конфликт экран скажет прямо и покажет, что там теперь.
    ожидали = data.get("expected") if isinstance(data.get("expected"), dict) else {}

    # Цену приложение меняет только с номером версии (price_rev), который
    # видел человек. Без номера — это приложение, открытое ещё до выкатки
    # номера: его запрос мог застрять в сети и прийти позже свежей правки,
    # а сверка по одному значению его бы пропустила (QP-03-L). Номер за него
    # не подставляем — просим открыть приложение заново. Остальные поля того
    # же запроса сохраняются как обычно; бот меняет цену своим путём
    # (db.update_field) и сюда не приходит.
    if "price" in приняты and "price_rev" not in ожидали:
        приняты.pop("price")
        отказы["price"] = {"error": "stale_app",
                           "message": "Приложение устарело: цена не сохранена. Закройте приложение "
                                      "и откройте заново — тогда цену можно будет поменять."}

    # Одиночное поле отвечает как раньше — кодом и текстом: на него завязаны
    # переключатели, которые ждут именно такой ответ.
    if одиночное and отказы:
        _, беда = next(iter(отказы.items()))
        ответ = {"ok": False, "error": беда["error"]}
        if беда["message"]:
            ответ["message"] = беда["message"]
        # «Чужая точка» — это отказ в праве, а не кривые данные: коды должны
        # различаться. Устаревшее приложение — конфликт с тем, что на сервере.
        код = 403 if беда["error"] in ("other_city", "forbidden") else 409 if беда["error"] == "stale_app" else 400
        return jsonify(ответ), код
    сохранено, конфликты, до = db.update_fields(pid, приняты, expected=ожидали)
    # Номер версии цены сменился, а число то же: цену за это время сохраняли
    # заново. «Поменяли» было бы неправдой.
    заново = {поле for поле, сейчас in конфликты.items()
              if поле == "price" and "price" in ожидали and _то_же_число(сейчас, ожидали.get("price"))}
    for поле, сейчас in конфликты.items():
        отказы[поле] = {"error": "conflict", "current": сейчас,
                        "message": f"{_ИМЕНА_ПОЛЕЙ.get(поле) or поле}: пока карточка была открыта, "
                                   + ("её уже сохраняли" if поле in заново else "значение уже поменяли")
                                   + f" — сейчас {_как_показать(поле, сейчас)}. "
                                   f"Проверьте и сохраните ещё раз, если нужно."}
        if поле == "price" and до:
            отказы[поле]["current_rev"] = int(до.get("price_rev") or 0)
    if до:
        g.log_note = _было_стало(до, {k: приняты[k] for k in сохранено}, конфликты, заново)

    ответ = {"ok": True, "saved": сохранено}
    # Новый номер версии цены — экран запомнит его и со следующим
    # сохранением пришлёт: так застрявший старый запрос не пройдёт поверх.
    if "price" in сохранено and до:
        ответ["price_rev"] = int(до.get("price_rev") or 0) + 1
    # Пачка сохраняет всё, что прошло, и честно называет, что не прошло:
    # отказать в цене — не повод потерять только что вписанное описание.
    if отказы:
        ответ["failed"] = отказы
    return jsonify(ответ)


def _то_же_число(а, б):
    try:
        return round(float(а), 2) == round(float(б), 2)
    except (TypeError, ValueError):
        return False


# Как поля называются по-человечески — для журнала и отказов. None — служебное
# поле, в журнал его не пишем (у фото есть уменьшенная копия, и строка «фото
# изменено» дважды никому не нужна).
_ИМЕНА_ПОЛЕЙ = {"price": "Цена", "cost": "Закупка", "name": "Название", "category": "Категория",
                "city": "Точка", "brand": "Бренд", "flavor": "Вкус", "strength": "Крепость",
                "volume": "Объём", "description": "Описание", "is_hit": "«Хит»",
                "hidden": "Витрина", "photo": "Фото", "photo_thumb": None}


def _как_показать(поле, значение):
    if поле in ("price", "cost"):
        return f"{float(значение or 0):.2f} Br"
    if поле in ("is_hit", "hidden"):
        return "да" if int(значение or 0) else "нет"
    return f"«{значение}»" if значение not in (None, "") else "пусто"


def _было_стало(до, стало, конфликты=None, заново=()):
    """Строка журнала: «Название» · точка: Цена 20.00 Br → 25.00 Br; …

    Раньше журнал писал «product/update · id=5» — ни что изменили, ни каким
    оно было. На вопрос «кто и когда поднял цену» ответить было нечем.

    Конфликт (поле успел поменять другой) тоже пишем: попытка была, и без
    этой строки в журнале стояло бы пустое «без изменений»."""
    части = []
    for поле, новое in стало.items():
        имя = _ИМЕНА_ПОЛЕЙ.get(поле, поле)
        if имя is None:
            continue
        было = до.get(поле)
        if _как_показать(поле, было) == _как_показать(поле, новое):
            continue
        if поле == "hidden":
            части.append("снят с витрины" if int(новое or 0) else "возвращён на витрину")
        elif поле == "is_hit":
            части.append("отмечен «Хит»" if int(новое or 0) else "снята отметка «Хит»")
        elif поле in ("description", "photo"):
            части.append(f"{имя.lower()} изменено")
        else:
            части.append(f"{имя} {_как_показать(поле, было)} → {_как_показать(поле, новое)}")
    for поле, сейчас in (конфликты or {}).items():
        имя = _ИМЕНА_ПОЛЕЙ.get(поле) or поле
        что = "её уже сохраняли" if поле in заново else "уже поменяли"
        части.append(f"{имя}: не сохранено — {что}, сейчас {_как_показать(поле, сейчас)}")
    return f"«{до.get('name')}» · {до.get('city')}: " + ("; ".join(части) if части else "без изменений")


def _свести_вкусы(сырые, эталон=None):
    """Приводит присланные вкусы к одному написанию и склеивает повторы.

    «Grape» и «grape» — один вкус. Модель это уже понимает и дублей не заводит,
    а вот у товара они становились двумя записями: покупатель видел в списке
    один и тот же вкус дважды, а фильтр по вкусу делил остаток пополам.

    Остатки повторов складываем, а не берём последний: если продавец вписал
    один вкус двумя строками, он привёз сумму, и потерять половину хуже.

    Написание берём из модели, когда она этот вкус знает: пусть во всех городах
    он выглядит одинаково.
    """
    правильное = {inputs.ключ_варианта(f): str(f).strip() for f in (эталон or [])}
    свод = {}
    порядок = []
    for v in сырые:
        имя = str((v or {}).get("flavor", "")).strip()
        if not имя:
            continue
        ключ = inputs.ключ_варианта(имя)          # «0,6» = «0.6»
        if ключ not in свод:
            свод[ключ] = {"flavor": правильное.get(ключ, имя), "stock": 0}
            порядок.append(ключ)
        свод[ключ]["stock"] += max(0, inputs.целое((v or {}).get("stock", 0), 0))
    return [свод[к] for к in порядок]


@bp.route("/api/admin/product/variants", methods=["POST"])
def api_admin_variants():
    """Заменяет список вкусов товара целиком (добавить/убрать/изменить остаток)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny

    товар_ = db.get_product(pid)
    модель_ = товар_["model_id"] if товар_ and "model_id" in товар_.keys() else None
    известные = (db.get_model(модель_) or {}).get("flavors", []) if модель_ else []

    # Браузер не даёт сохранить пустой список — но браузер не единственный,
    # кто умеет послать этот запрос. Без проверки здесь пустой variants[]
    # проходил молча: товар получал остаток 0 без единого отказа, а если
    # список был непустым — стирал настоящий остаток тем же путём. Считаем
    # ПОСЛЕ свода (дубли/пустые имена схлопнутся), но ДО удаления старого —
    # отказ не должен успевать снести то, что уже стояло на точке.
    норм_вкусы = _свести_вкусы(data.get("variants") or [], известные)
    if not норм_вкусы:
        return jsonify({"ok": False, "error": "no_variants",
                        "message": "Нужен хотя бы один вариант — со всеми пустыми остаток посчитать нечем."}), 400

    # expected — снимок остатков по вкусам, который клиент видел при открытии
    # карточки. Раньше список менялся целиком (удалить всё, вставить заново),
    # и числа из формы ложились поверх склада: карточка открыта, кто-то купил
    # вкус, продавец добавил ещё один вкус и сохранил — купленное тихо
    # возвращалось на полку. Теперь эта ручка меняет только СОСТАВ (новые
    # варианты с первым приходом, убранные — по правилам склада), а число у
    # заведённого варианта меняет только склад. Снимок нужен, чтобы отличить
    # «человек поправил число» (отказ с объяснением) от «товар продался, пока
    # была открыта карточка» (это не правка и не повод отказывать).
    expected = data.get("expected")
    if isinstance(expected, list):
        expected = _свести_вкусы(expected, известные)
    else:
        expected = None
    try:
        итог = db.replace_variants_if(pid, норм_вкусы, expected, admin_id=int(admin["id"]))
    except db.StockRefused as e:
        return jsonify({"ok": False, "error": e.code, "message": e.message}), _код_отказа(e.code)
    g.log_note = _журнал_вариантов(товар_, итог)

    # Вкус, заведённый на точке, обязан попасть в модель — иначе списки
    # расходятся молча: в Горках вкус есть, а завезти его в Минск нельзя,
    # потому что модель о нём не знает. Наступали ровно на это.
    добавлено = db.merge_model_flavors(модель_, итог["added"]) if модель_ else []
    return jsonify({"ok": True, "added_to_model": добавлено, **итог})


def _код_отказа(code):
    """HTTP-код для отказа склада: конфликт — 409, «не найдено» — 404, прочее — 400."""
    if code == "not_found":
        return 404
    if code in ("stock_conflict", "reserved", "has_stock", "variant_missing", "exists"):
        return 409
    return 400


def _журнал_вариантов(товар, итог):
    """«Название» · точка: + Манго (приход 10); − Мята (списано 3)."""
    части = [f"+ {имя}" for имя in итог.get("added", [])]
    for имя in итог.get("removed", []):
        штук = (итог.get("written_off") or {}).get(имя)
        части.append(f"− {имя}" + (f" (списано {штук} шт)" if штук else ""))
    return f"«{товар['name']}» · {товар['city']}: варианты " + ("; ".join(части) or "без изменений")


@bp.route("/api/admin/product/variants/change", methods=["POST"])
def api_admin_variants_change():
    """Состав вариантов товара: добавить новые (с первым приходом), убрать ненужные.

    Число у уже заведённого варианта здесь не меняется — это склад. Поэтому
    продажа, случившаяся, пока карточка открыта, этому сохранению не мешает:
    сравнивать нечего, ничьё число не переписывается.

    add — [{"flavor", "qty"}], remove — ["вкус"], writeoff — подтверждение:
    убрать вариант с остатком и списать этот остаток (с записью в историю).
    """
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny
    товар_ = db.get_product(pid)
    if not товар_:
        return jsonify({"ok": False, "error": "not_found"}), 404
    add, remove = data.get("add") or [], data.get("remove") or []
    if not isinstance(add, list) or not isinstance(remove, list) or len(add) > 200 or len(remove) > 200:
        return jsonify({"ok": False, "error": "bad_input"}), 400
    модель_ = товар_["model_id"] if "model_id" in товар_.keys() else None
    известные = (db.get_model(модель_) or {}).get("flavors", []) if модель_ else []
    # Написание — как в модели: «мята» и «Мята» в разных городах выглядели бы
    # как два разных вкуса. Количество не сводим: отрицательное — это отказ.
    правильное = {inputs.ключ_варианта(f): str(f).strip() for f in известные}
    новые = []
    for a in add:
        if not isinstance(a, dict):
            return jsonify({"ok": False, "error": "bad_input"}), 400
        имя = inputs._text(a.get("flavor"), 120)
        # Пусто — значит «пока без остатка», а мусор вместо числа — отказ:
        # молча считать его нулём значило бы потерять привезённое.
        сырое = a.get("qty")
        штук = 0 if сырое in (None, "") else inputs.целое(сырое)
        if штук is None or штук > 100_000:
            return jsonify({"ok": False, "error": "bad_number",
                            "message": f"«{имя}»: проверьте количество."}), 400
        if имя:
            новые.append({"flavor": правильное.get(inputs.ключ_варианта(имя), имя), "qty": штук})
    try:
        итог = db.change_variants(pid, новые, [inputs._text(x, 120) for x in remove],
                                  admin_id=int(admin["id"]), writeoff=bool(data.get("writeoff")))
    except db.StockRefused as e:
        return jsonify({"ok": False, "error": e.code, "message": e.message}), _код_отказа(e.code)
    g.log_note = _журнал_вариантов(товар_, итог)
    добавлено = db.merge_model_flavors(модель_, итог["added"]) if модель_ else []
    return jsonify({"ok": True, "added_to_model": добавлено, **итог})


@bp.route("/api/admin/product/delete", methods=["POST"])
def api_admin_delete():
    """Удалить товар насовсем — только из архива и только без истории.

    Раньше «Удалить с точки» стоял рядом с повседневными действиями и уносил
    историю склада (движения теряли товар: выпадали из журнала точки) и
    отзывы. На «больше не возим» теперь отвечает архив; удаление осталось
    для товара, заведённого по ошибке: ни одного движения склада и ни одного
    заказа. Только владелец (auth._OWNER_ONLY_EXACT)."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    # Проверка и удаление — одной транзакцией в db.delete_archived_product
    # (AR-01): прочитать здесь «в архиве, истории нет», а удалить потом —
    # значило стереть товар, который в этот промежуток вернули и пополнили.
    try:
        товар_ = db.delete_archived_product(pid)
    except db.ArchiveRefused as e:
        статус = {"not_found": 404}.get(e.code, 409)
        return jsonify({"ok": False, "error": e.code, "message": e.message, **e.extra}), статус
    g.log_note = f"«{товар_['name']}» · {товар_['city']}: удалён из архива насовсем (истории не было)"
    return jsonify({"ok": True})


@bp.route("/api/admin/product/archive", methods=["POST"])
def api_admin_product_archive():
    """В архив («больше не возим») или обратно — db.archive_product.

    Продавцу — на своей точке: раньше он мог удалить свой товар, архив
    мягче удаления."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny
    в_архив = bool(data.get("archived", True))
    try:
        итог = db.archive_product(pid, в_архив)
    except db.ArchiveRefused as e:
        статус = {"not_found": 404}.get(e.code, 409)
        return jsonify({"ok": False, "error": e.code, "message": e.message, **e.extra}), статус
    p = итог["product"]
    if итог["changed"]:
        g.log_note = f"«{p['name']}» · {p['city']}: " + ("убран в архив" if в_архив else "возвращён из архива")
    return jsonify({"ok": True, "changed": итог["changed"], "archived": в_архив})


@bp.route("/api/admin/model/archive", methods=["POST"])
def api_admin_model_archive():
    """Товар в архив на всех точках — одним нажатием, по точке за раз.

    Каждая точка проверяется сама: где остаток или невыданные заказы, товар
    остаётся, и человек получает список «где и почему»."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    mid = inputs.целое(data.get("model_id"))
    модель = db.get_model(mid) if mid is not None else None
    if not модель:
        return jsonify({"ok": False, "error": "not_found", "message": "Этого товара больше нет — обновите список."}), 404
    убраны, остались = [], []
    for p in db.get_all_products():
        if p["model_id"] != mid:
            continue
        try:
            if db.archive_product(p["id"], True)["changed"]:
                убраны.append(p["city"])
        except db.ArchiveRefused as e:
            остались.append({"city": p["city"], "id": p["id"], "error": e.code, "message": e.message})
    if убраны:
        g.log_note = (f"«{модель['name']}»: в архив — {', '.join(убраны)}"
                      + (f"; осталось: {', '.join(x['city'] for x in остались)}" if остались else ""))
    return jsonify({"ok": True, "archived": убраны, "left": остались})


@bp.route("/api/admin/archive", methods=["POST"])
def api_admin_archive_list():
    """Блок «🗄 Архив» внизу «🛍 Товаров». Продавцу — своя точка."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    владелец = admin.get("role") in ("dev", "owner")       # «удалить насовсем» — только ему
    out = []
    for p in db.archived_products(admin.get("city") or None):
        строка = {"id": p["id"], "name": p["name"], "brand": p["brand"] or "", "city": p["city"],
                  "category": p["category"], "model_id": p["model_id"], "price": float(p["price"] or 0),
                  "hidden": bool(p["hidden"]),
                  "thumb": f"/api/photo?file_id={p['photo_thumb'] or p['photo']}" if p["photo"] else ""}
        if владелец:
            история = db.product_history(p["id"])
            строка["can_delete"] = not (история["moves"] or история["orders"])
        out.append(строка)
    return jsonify({"ok": True, "items": out})


def _не_картинка(file):
    """Готовый отказ, если прислали не изображение, иначе None.

    Само правило живёт в photos — им пользуется и розыгрыш, а тащить ради
    одной проверки catalog в games значило бы завести лишнее ребро в графе
    зависимостей.
    """
    if photos.это_картинка(file):
        return None
    return jsonify({"ok": False, "error": "not_image",
                    "message": "Это не изображение. Нужен файл jpg, png или webp."}), 400


@bp.route("/api/admin/photo", methods=["POST"])
def api_admin_photo():
    """Загрузить фото товара. Отправляем картинку админу (тихо), чтобы получить file_id."""
    init_data = request.form.get("initData", "")
    user = auth.get_admin(init_data)
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(request.form.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    беда = _не_картинка(file)
    if беда:
        return беда

    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото товара сохранено", disable_notification=True)
        file_id, thumb_id = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото товара: {e}")
        return jsonify({"ok": False, "error": "send_failed",
                        "message": "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи."}), 502

    db.update_field(pid, "photo", file_id)
    db.update_field(pid, "photo_thumb", thumb_id)
    return jsonify({"ok": True})


@bp.route("/api/admin/photo/draft", methods=["POST"])
def api_admin_photo_draft():
    """Фото для черновика нового товара: товара ещё нет, а фото нужно
    сохранить сразу — чтобы оно пережило закрытие приложения и не грузилось
    заново при публикации. Черновик хранит file_id, а не сам файл: File из
    браузера после перезапуска не восстановить."""
    user = auth.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    беда = _не_картинка(file)
    if беда:
        return беда
    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                                   caption="🖼 Фото для нового товара", disable_notification=True)
        file_id, thumb_id = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото нового товара: {e}")
        return jsonify({"ok": False, "error": "send_failed",
                        "message": "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи."}), 502
    db.add_draft_photo(user["id"], file_id, thumb_id)
    g.log_note = "фото для нового товара загружено"
    return jsonify({"ok": True, "file_id": file_id, "thumb_id": thumb_id,
                    "url": f"/api/photo?file_id={file_id}", "thumb": f"/api/photo?file_id={thumb_id or file_id}"})


@bp.route("/api/admin/photo/add", methods=["POST"])
def api_admin_photo_add():
    """Добавить фото в галерею МОДЕЛИ (главное фото при этом не меняется)."""
    user = auth.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    mid = inputs.целое(request.form.get("model_id"))
    if mid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    беда = _не_картинка(file)
    if беда:
        return беда
    if len(db.model_photos(mid)) >= db.MAX_EXTRA_PHOTOS:
        # Проверяем ДО отправки в Telegram: иначе картинка уедет впустую.
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото модели сохранено", disable_notification=True)
        file_id, thumb_id = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото модели: {e}")
        return jsonify({"ok": False, "error": "send_failed",
                        "message": "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи."}), 502
    photo_id = db.add_model_photo(mid, file_id, thumb_id)
    if not photo_id:
        return jsonify({"ok": False, "error": "too_many", "max": db.MAX_EXTRA_PHOTOS}), 400
    return jsonify({"ok": True, "photo_id": photo_id})


@bp.route("/api/admin/photo/delete", methods=["POST"])
def api_admin_photo_delete():
    """Убрать фото из галереи. Главное фото (id 0) так не удаляется — его заменяют."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    photo_id = inputs.целое(data.get("photo_id"))
    if photo_id is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if photo_id <= 0:
        return jsonify({"ok": False, "error": "main_photo"}), 400
    return jsonify({"ok": True, "deleted": db.delete_product_photo(photo_id)})


@bp.route("/api/admin/models", methods=["POST"])
def api_admin_models():
    """Ассортимент: что магазин вообще продаёт (независимо от наличия на точках)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
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


@bp.route("/api/admin/model", methods=["POST"])
def api_admin_model_save():
    """Создать или изменить модель. Правка расходится по всем её товарам."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    category = inputs._text(data.get("category"))
    name = inputs._text(data.get("name"))
    if category not in db.category_codes() or not name:
        return jsonify({"ok": False, "error": "bad_data"}), 400
    specs = _clean_specs(category, data.get("specs"))
    flavors, seen = [], set()
    for f in (data.get("flavors") or []):
        f = str(f).strip()
        if f and inputs.ключ_варианта(f) not in seen:      # «0,6» = «0.6», «Мята» = «мята»
            seen.add(inputs.ключ_варианта(f))
            flavors.append(f)
    mid = data.get("id")
    # Две одинаковые модели в одной категории — это раздвоенная витрина и
    # раздвоенная статистика: остатки и продажи разъедутся по двум карточкам.
    twin = next((m for m in db.list_models(category)
                 if m["name"].strip().lower() == name.lower()
                 and (m["brand"] or "").strip().lower() == inputs._text(data.get("brand")).lower()
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


_КЛЮЧ_ПУБЛИКАЦИИ = re.compile(r"[A-Za-z0-9_-]{8,64}")
_МАКС_ШТУК = 100000


def _отказ(код, сообщение, поле=None, статус=400):
    тело = {"ok": False, "error": код, "message": сообщение}
    if поле:
        тело["field"] = поле
    return jsonify(тело), статус


@bp.route("/api/admin/product/publish", methods=["POST"])
def api_admin_product_publish():
    """Новый товар одним маршрутом: модель, фото, точки, варианты и первый
    приход — одной транзакцией (db.publish_product), с ключом повтора.

    Раньше это были два раздела и несколько запросов подряд, и сбой
    посередине оставлял полтовара. Всё, что прислал экран, проверяется здесь
    же — форма может ошибиться или оказаться старой."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    token = inputs._text(data.get("client_token"))
    if not _КЛЮЧ_ПУБЛИКАЦИИ.fullmatch(token or ""):
        return _отказ("bad_token", "Экран прислал неверный ключ публикации — обновите приложение.")

    модель = data.get("model") if isinstance(data.get("model"), dict) else {}
    категория = inputs._text(модель.get("category"))
    категории = {c["code"]: c for c in db.list_categories()}
    if категория not in категории:
        return _отказ("bad_category", "Выберите категорию.", "category")
    имя = inputs._text(модель.get("name"), 120)
    if not имя:
        return _отказ("no_name", "Впишите название товара.", "name")
    бренд = inputs._text(модель.get("brand"), 80)
    описание = inputs._text(модель.get("description"), 2000)
    specs = _clean_specs(категория, модель.get("specs"))
    # Вкусы — в написании справочника брендов, если бренд там есть: «grape b -
    # pop» и «Grape B - POP» — один вкус, и во всех товарах бренда он должен
    # выглядеть одинаково (как в редакторе вариантов, _свести_вкусы). Ради
    # этого справочник и заведён.
    эталон = {}
    найденный = db.find_brand_by_name(бренд) if бренд else None
    if найденный:
        try:
            эталон = {inputs.ключ_варианта(f): str(f).strip() for f in json.loads(найденный["flavors"] or "[]") if str(f).strip()}
        except (TypeError, ValueError):
            эталон = {}

    # «0,6» и «0.6», «Мята» и «мята» — один вариант (inputs.ключ_варианта): в
    # модели остаётся первое написание (или справочника), и вариант на точке,
    # присланный другим написанием, ложится на него же.
    видели = {}

    def по_эталону(вкус):
        к = inputs.ключ_варианта(вкус)
        return видели.get(к) or эталон.get(к, вкус)

    вкусы = []
    сырые = модель.get("flavors") if isinstance(модель.get("flavors"), list) else []
    for f in сырые:
        f = по_эталону(inputs._text(f, 60))
        if f and inputs.ключ_варианта(f) not in видели:
            видели[inputs.ключ_варианта(f)] = f
            вкусы.append(f)
    if len(вкусы) > 200:
        return _отказ("too_many", "Больше 200 вариантов у одного товара — похоже на ошибку.", "flavors")
    if вкусы and not int(категории[категория].get("has_flavors") or 0):
        return _отказ("no_variants", "У этой категории нет вариантов — уберите их или выберите другую категорию.", "flavors")

    фото = data.get("photos") if isinstance(data.get("photos"), list) else []
    фото = [inputs._text(f, 300) for f in фото]
    if any(not f for f in фото) or len(set(фото)) != len(фото) or len(фото) > 1 + db.MAX_EXTRA_PHOTOS:
        return _отказ("bad_photo", f"Фото — не больше {1 + db.MAX_EXTRA_PHOTOS}, без повторов.", "photos")

    сырые_точки = data.get("points") if isinstance(data.get("points"), list) else []
    if not сырые_точки:
        return _отказ("no_point", "Выберите точку, где товар будет продаваться.", "points")
    известные = set(db.location_names())
    точки, города = [], set()
    for i, т in enumerate(сырые_точки):
        т = т if isinstance(т, dict) else {}
        город = inputs._text(т.get("city"), 80)
        if город not in известные:
            return _отказ("bad_point", "Такой точки нет — выберите из списка.", f"points.{i}.city")
        if город in города:
            return _отказ("bad_point", f"Точка «{город}» выбрана дважды.", f"points.{i}.city")
        if not auth.may_city(admin, город):
            return _отказ("other_city", "Это точка другого продавца.", f"points.{i}.city", 403)
        города.add(город)
        цена = inputs.дробное(т.get("price"))
        if цена is None or цена <= 0 or цена > _МАКС_ШТУК:
            return _отказ("bad_price", f"«{город}»: цена — число больше нуля, например 18.5.", f"points.{i}.price")
        # Закупку обязаны вписать, как и при завозе (_закупка): пустая молча
        # выбрасывала бы товар из подсчёта прибыли. Ноль — можно, но руками.
        if str(т.get("cost") if т.get("cost") is not None else "").strip() == "":
            return _отказ("cost_required", f"«{город}»: впишите закупку за штуку — без неё прибыль по товару "
                                           "не посчитается. Если закупки не было (подарок, образец), поставьте 0.",
                          f"points.{i}.cost")
        закупка = inputs.дробное(т.get("cost"))
        if закупка is None or закупка < 0 or закупка > _МАКС_ШТУК:
            return _отказ("bad_cost", f"«{город}»: закупка — неотрицательное число.", f"points.{i}.cost")
        точка = {"city": город, "price": round(цена, 2), "cost": round(закупка, 2), "is_hit": 1 if т.get("is_hit") else 0}
        if вкусы:
            варианты, есть = [], set()
            for j, v in enumerate(т.get("variants") if isinstance(т.get("variants"), list) else []):
                v = v if isinstance(v, dict) else {}
                вкус = по_эталону(inputs._text(v.get("flavor"), 60))
                if вкус not in вкусы or вкус in есть:
                    return _отказ("bad_variant", f"«{город}»: вариант «{вкус}» не из списка товара или повторяется.",
                                  f"points.{i}.variants.{j}")
                штук = inputs.целое(v.get("stock")) if v.get("stock") not in (None, "") else 0
                if штук is None or штук < 0 or штук > _МАКС_ШТУК:
                    return _отказ("bad_number", f"«{город}», «{вкус}»: количество — целое число от 0.",
                                  f"points.{i}.variants.{j}")
                есть.add(вкус)
                варианты.append({"flavor": вкус, "stock": штук})
            if not варианты:
                return _отказ("no_variants", f"«{город}»: отметьте хотя бы один вариант, который там продаётся.",
                              f"points.{i}.variants")
            точка["variants"] = варианты
        else:
            штук = inputs.целое(т.get("stock")) if т.get("stock") not in (None, "") else 0
            if штук is None or штук < 0 or штук > _МАКС_ШТУК:
                return _отказ("bad_number", f"«{город}»: количество — целое число от 0.", f"points.{i}.stock")
            точка["stock"] = штук
        точки.append(точка)

    содержимое = {"model": {"category": категория, "name": имя, "brand": бренд, "description": описание,
                            "specs": specs, "flavors": вкусы}, "photos": фото, "points": точки}
    отпечаток = hashlib.sha256(json.dumps(содержимое, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        итог = db.publish_product(int(admin["id"]), token, отпечаток, содержимое["model"], фото, точки)
    except db.PublishRefused as e:
        return jsonify({"ok": False, "error": e.code, "message": e.message, **e.extra}), \
            409 if e.code in ("exists", "token_reused") else 400

    части = []
    for т in точки:
        штук = sum(v["stock"] for v in т["variants"]) if "variants" in т else т["stock"]
        части.append(f"{т['city']} — {т['price']:.2f} Br, первый приход {штук} шт")
    g.log_note = (f"Новый товар «{имя}»" + (f" · {бренд}" if бренд else "") + ": " + "; ".join(части)
                  + (" (повтор, второй раз не создан)" if итог.get("replay") else ""))
    return jsonify({"ok": True, **итог})


@bp.route("/api/admin/model/hide", methods=["POST"])
def api_admin_model_hide():
    """Снять модель с витрины на всех точках сразу (или вернуть).

    «Больше не возим» — это не «этого не было»: удаление уносит остаток,
    историю движений и отзывы, а снятие оставляет всё на месте."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    mid = inputs.целое(data.get("id"))
    if mid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    if not db.get_model(mid):
        return jsonify({"ok": False, "error": "not_found"}), 404
    hidden = bool(data.get("hidden"))
    return jsonify({"ok": True, "hidden": hidden, "count": db.hide_model_products(mid, hidden)})


@bp.route("/api/admin/model/delete", methods=["POST"])
def api_admin_model_delete():
    """Убрать модель из ассортимента — только если её нет ни на одной точке.

    Модель с товарами не удаляется вовсе, «force» больше не действует: товары
    остались бы без модели, а их галерея и отзывы — потеряны (см.
    db.delete_model). Человеку называем точки и что делать вместо."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    mid = inputs.целое(data.get("id"))
    if mid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    модель = db.get_model(mid)
    if not модель:
        return jsonify({"ok": False, "error": "not_found"}), 404
    итог = db.delete_model(mid)
    if итог == "has_products":
        города = db.cities_of_model(mid)
        в_архиве = sorted({p["city"] for p in db.archived_products() if p["model_id"] == mid})
        стоит = [г for г in города if г not in в_архиве]
        где = "; ".join(x for x in (f"стоит на точках: {', '.join(стоит)}" if стоит else "",
                                    f"в архиве на точках: {', '.join(в_архиве)}" if в_архиве else "") if x)
        return jsonify({"ok": False, "error": "has_products", "count": db.count_products_of_model(mid), "cities": города,
                        "archived_cities": в_архиве,
                        "message": f"«{модель['name']}» {где}. Удалить описание вместе с товаром нельзя — пропали бы "
                                   "история склада и отзывы. Больше не продаёте — уберите в архив "
                                   "(⋯ → «🗄 В архив на всех точках»): там всё сохранится, и вернуть можно в любой момент."}), 400
    if итог == "not_found":
        return jsonify({"ok": False, "error": "not_found"}), 404
    g.log_note = f"модель «{модель['name']}» удалена из ассортимента"
    return jsonify({"ok": True, "count": 0})


@bp.route("/api/admin/model/photo", methods=["POST"])
def api_admin_model_photo():
    """Фото модели — оно же появляется у всех её товаров на точках."""
    user = auth.get_admin(request.form.get("initData", ""))
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
    беда = _не_картинка(file)
    if беда:
        return беда
    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото модели сохранено", disable_notification=True)
        file_id, thumb_id = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото модели: {e}")
        return jsonify({"ok": False, "error": "send_failed",
                        "message": "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи."}), 502
    db.set_model_photo(mid, file_id, thumb_id)
    return jsonify({"ok": True})


@bp.route("/api/admin/product/to-model", methods=["POST"])
def api_admin_product_to_model():
    """Сделать из одиночного товара модель — чтобы он мог стоять на точках.

    Товары, заведённые до «Ассортимента», модели не имеют, и продавать их в
    другом городе можно было только заведя товар заново, руками, с теми же
    полями. Ровно на это владелец и жаловался.

    Описание берём из самого товара: название, бренд, характеристики, вкусы,
    фото. Ничего не спрашиваем заново — всё это уже введено, и просить второй
    раз значит не уважать чужое время. Как именно и что переезжает вместе с
    товаром (галерея, отзывы) — db.product_to_model.

    Такая же модель уже есть — 409 с её номером; повтор с link_to — привязка.
    """
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    pid = inputs.целое(data.get("id"))
    if pid is None:
        return jsonify({"ok": False, "error": "bad_id"}), 400
    deny = auth.deny_product(admin, pid)
    if deny:
        return deny
    link_to = inputs.целое(data.get("link_to")) if data.get("link_to") not in (None, "") else None
    try:
        итог = db.product_to_model(pid, link_to)
    except db.ToModelRefused as e:
        статус = {"not_found": 404, "exists": 409}.get(e.code, 400)
        return jsonify({"ok": False, "error": e.code, "message": e.message, **e.extra}), статус
    товар = db.get_product(pid)
    g.log_note = (f"товар {pid} «{товар['name']}» · {товар['city']} → " +
                  (f"привязан к модели {итог['model_id']}" if итог["linked"] else f"новая модель {итог['model_id']}") +
                  (f"; вкусы в модель: {', '.join(итог['added_flavors'])}" if итог["added_flavors"] else "") +
                  (f"; фото в галерею: {итог['photos_moved']}" if итог["photos_moved"] else "") +
                  (f"; отзывов: {итог['reviews']}" if итог["reviews"] else ""))
    return jsonify({"ok": True, **итог})


@bp.route("/api/admin/product/from-model", methods=["POST"])
def api_admin_product_from_model():
    """Завоз: модель появляется на точке с ценой и остатком."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        mid = int(data.get("model_id"))
        price = float(str(data.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_data"}), 400
    m = db.get_model(mid)
    city = inputs._text(data.get("city"))
    if not m or city not in db.location_names():
        return jsonify({"ok": False, "error": "bad_data"}), 400
    # Завозить на свою точку продавец вправе — это его работа. На чужую нет.
    deny = auth.deny_city(admin, city)
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
    raw_variants = data.get("variants") if isinstance(data.get("variants"), list) else []
    # Модель со вкусами — это её механика, не выбор продавца на конкретной
    # точке: раз модель заводит остаток по вариантам, завоз обязан привезти
    # хотя бы один. Определяем по МОДЕЛИ (m["flavors"]), а не по тому, пришли
    # ли variants в запросе — иначе пустой список [] молча уводил завоз на
    # путь «обычный остаток», и модель со вкусами получала точку без единого
    # варианта: остаток 0, вкусов нет, а отказа не было вовсе.
    норм_вкусы = _свести_вкусы(raw_variants, (m["flavors"] or [])) if m["flavors"] else []
    if m["flavors"] and not норм_вкусы:
        return jsonify({"ok": False, "error": "no_variants",
                        "message": "Нужен хотя бы один вариант — со всеми пустыми остаток посчитать нечем."}), 400
    try:
        stock = max(0, int(data.get("stock") or 0))
    except (TypeError, ValueError):
        stock = 0
    # Одной транзакцией: товар, привязка к модели, варианты и первый приход в
    # историю склада. Раньше это были отдельные записи, и сбой посередине
    # оставлял на точке товар без вариантов.
    pid = db.create_point_product(mid, city, max(0.0, price), cost,
                                  is_hit=1 if data.get("is_hit") else 0,
                                  stock=0 if норм_вкусы else stock, variants=норм_вкусы,
                                  admin_id=int(admin["id"]))
    # Проверки выше — по прочитанному до транзакции; окончательный ответ даёт
    # она сама: модель могли удалить, а ту же модель — завезти сюда же.
    if pid is None:
        return jsonify({"ok": False, "error": "model_gone",
                        "message": "Эту модель только что удалили из ассортимента — обновите список."}), 409
    if pid == "already_here":
        return jsonify({"ok": False, "error": "already_here"}), 400
    if pid == "in_archive":
        архивный = next((p for p in db.archived_products(city) if p["model_id"] == mid), None)
        return jsonify({"ok": False, "error": "in_archive", "id": архивный["id"] if архивный else None,
                        "message": f"На точке «{city}» этот товар в архиве. Верните его оттуда — с прежними "
                                   "отзывами и историей, — а не заводите второй: «🛍 Товары» → внизу «🗄 Архив»."}), 409
    if норм_вкусы:
        db.merge_model_flavors(mid, [v["flavor"] for v in норм_вкусы])
    всего = sum(v["stock"] for v in норм_вкусы) if норм_вкусы else stock
    g.log_note = (f"«{m['name']}» → {city}: цена {price:.2f} Br, закупка {cost:.2f} Br, "
                  f"первый приход {всего} шт"
                  + (f" ({', '.join(v['flavor'] + ' ' + str(v['stock']) for v in норм_вкусы)})"
                     if норм_вкусы else ""))
    return jsonify({"ok": True, "id": pid})


@bp.route("/api/admin/brand", methods=["POST"])
def api_admin_brand():
    """Создать или обновить бренд (если пришёл id — обновляем)."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    name = inputs._text(data.get("name"))
    # Пустая категория — бренд общий: Vaporesso делает и поды, и картриджи,
    # и заводить его в каждой категории заново незачем.
    category = inputs._text(data.get("category"))
    if not name or (category and category not in db.category_codes()):
        return jsonify({"ok": False, "error": "bad_data"}), 400
    # Вкусы храним без повторов и лишних пробелов: «Мята» и «мята » в фильтре
    # выглядели бы как два разных вкуса.
    flavors, seen = [], set()
    for f in (data.get("flavors") or []):
        f = str(f).strip()
        if f and inputs.ключ_варианта(f) not in seen:      # «0,6» = «0.6», «Мята» = «мята»
            seen.add(inputs.ключ_варианта(f))
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


@bp.route("/api/admin/brand/delete", methods=["POST"])
def api_admin_brand_delete():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    bid = inputs.целое(data.get("id"))
    if bid is None:
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


@bp.route("/api/brands")
def api_brands():
    category = inputs._text(request.args.get("category")) or None
    key = f"brands:{category or 'all'}"
    cached = cache.get(key)
    if cached is not None:
        return cache.json_etag(cached)
    out = []
    for b in db.get_brands(category):
        try:
            flavors = json.loads(b["flavors"] or "[]")
        except Exception:
            flavors = []
        out.append({"id": b["id"], "name": b["name"], "category": b["category"] or "", "flavors": flavors})
    return cache.json_etag(cache.put(key, out, 300))


@bp.route("/api/flavors")
def api_flavors():
    """Все вкусы, которые уже встречались — для подсказок при вводе.
    Без них одна и та же «Мята» набирается по-разному и дробит фильтр."""
    cached = cache.get("flavors")
    if cached is None:
        cached = cache.put("flavors", db.known_flavors(), 300)
    return cache.json_etag(cached)


def _clean_specs(category, values):
    """Оставляет только характеристики, заведённые у этой категории."""
    if not isinstance(values, dict):
        return {}
    allowed = {s["key"] for s in db.list_category_specs(category)}
    return {k: str(v).strip() for k, v in values.items() if k in allowed and str(v).strip() != ""}
