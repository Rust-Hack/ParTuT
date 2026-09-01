"""QA-аудит админки: цены/остатки при мусорном вводе, промокод при отмене заказа,
проверка загружаемых фото.

Эти три сценария НЕ были покрыты существующим набором (test_specs.py, test_promos.py,
test_permissions.py и т.д. проверяют другие грани тех же ручек). Часть проверок ниже
описывает то, как должна вести себя админка по логике самого проекта (см. `_закупка()`
в partut/web/catalog.py, которая для закупочной цены именно так и поступает) — и падает
на сегодняшнем коде. Это ожидаемо: тест фиксирует найденный баг, а не выдумывает
хотелку. Где ассерт помечен «БАГ», тест красный до исправления соответствующей ручки.
"""
from _common import db, client, Checker, as_admin, as_user, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM promos")
    cur.execute("DELETE FROM delivery_methods")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def _product(pid):
    cache.bust()
    return db.get_product(pid)


# ============================================================
# 1. Каталог: отрицательная цена / остаток
# ============================================================
#
# partut/web/catalog.py:
#   _закупка() (строки 260-282) явно отказывает при cost < 0 — «bad_number», 400.
#   api_admin_add() (строки 285-334) и api_admin_update() (строки 337-391) для
#   ОСНОВНОЙ цены (`price`) и остатка (`stock`) такой проверки не делают: число
#   молча проходит через max(0.0, price) / max(0, stock) и превращается в 0.
#   Админ получает {"ok": true} и решает, что товар сохранён с той ценой, что ввёл.
#
# Для контраста: api_admin_product_from_model() (строка 757) прямо отказывает
# `price <= 0` — то есть в одном и том же файле два похожих поля с ценой ведут
# себя по-разному на один и тот же плохой ввод.
def run_negative_price_and_stock():
    c = Checker("Каталог: отрицательная цена и остаток не отклоняются")
    _clean()
    as_admin()

    pid = db.add_product("Минск", "pods", "QA-под", 25.0, 10, cost=15.0)

    # --- Правка цены существующего товара на отрицательную ---
    r = client.post("/api/admin/product/update",
                     json={"initData": "x", "id": pid, "field": "price", "value": "-50"})
    d = r.get_json() or {}
    c("ответ вообще пришёл", r.status_code in (200, 400))
    # БАГ: сервер отвечает ok:true, как будто цена сохранена такой, как ввели.
    c("БАГ [catalog.py:355-356] отрицательная цена отклонена, а не принята",
      r.status_code == 400 and d.get("error") in ("bad_number", "bad_value"))
    # Ещё хуже: реальная сохранённая цена — 0, а не -50 и не прежние 25 —
    # то есть админ не узнает из ответа, что товар стал бесплатным.
    c("БАГ [catalog.py:356] цена не должна была молча стать 0",
      float(_product(pid)["price"]) != 0.0)

    # --- Правка остатка на отрицательный ---
    r = client.post("/api/admin/product/update",
                     json={"initData": "x", "id": pid, "field": "stock", "value": "-7"})
    d = r.get_json() or {}
    c("БАГ [catalog.py:357-358] отрицательный остаток отклонён, а не принят",
      r.status_code == 400 and d.get("error") in ("bad_number", "bad_value"))

    # --- То же в момент создания нового товара ---
    r = client.post("/api/admin/product", json={
        "initData": "x", "city": "Минск", "category": "podsystem", "name": "QA-новый",
        "price": "-100", "cost": "10", "stock": "5"})
    d = r.get_json() or {}
    # Раньше товар заводился, а цена молча становилась 0.00 — он уезжал на
    # витрину бесплатным. Теперь ручка отказывает и говорит почему.
    c("товар с отрицательной ценой не заводится", r.status_code == 400)
    c("и сказано, в чём дело", d.get("error") in ("bad_price", "bad_number"))
    if d.get("ok"):
        p2 = _product(d["id"])
        c("цена нового товара не стала нулём", float(p2["price"]) != 0.0)

    # --- Закупочная цена, для сравнения, отклоняется правильно ---
    r = client.post("/api/admin/product", json={
        "initData": "x", "city": "Минск", "category": "podsystem", "name": "QA-закупка",
        "price": "10", "cost": "-5", "stock": "1"})
    c("закупка < 0 отклонена (это уже работает верно, эталон поведения)",
      r.status_code == 400 and (r.get_json() or {}).get("error") == "bad_number")

    # --- Для контраста: завоз модели на точку цену <= 0 отклоняет правильно ---
    mid = db.add_model("pods", "QA-модель", "", "", {}, [])
    r = client.post("/api/admin/product/from-model",
                     json={"initData": "x", "model_id": mid, "city": "Минск",
                           "price": "-20", "cost": "1", "stock": "1"})
    c("product/from-model цену <= 0 отклоняет (эталон для сравнения с product/update)",
      r.status_code == 400 and (r.get_json() or {}).get("error") == "bad_price")

    # --- Права по-прежнему на месте (это не проверка безопасности, а sanity) ---
    deny_admin()
    c("посторонний по-прежнему не поменяет цену",
      client.post("/api/admin/product/update",
                  json={"initData": "x", "id": pid, "field": "price", "value": "1"}).status_code == 403)
    as_admin()

    # --- Название товара нельзя стереть в пустую строку правкой ---
    было_имя = _product(pid)["name"]
    r = client.post("/api/admin/product/update",
                     json={"initData": "x", "id": pid, "field": "name", "value": "   "})
    d = r.get_json() or {}
    c("пустое название отклонено", r.status_code == 400 and d.get("error") == "bad_value")
    c("название не стёрлось", _product(pid)["name"] == было_имя)

    _clean()
    return c.fails


# ============================================================
# 2. Промокод: uses_left не возвращается при отмене/отклонении заказа
# ============================================================
#
# partut/db/promos.py:_reserve_promo() (152-156) списывает uses_left ВНУТРИ
# транзакции оформления заказа. partut/db/orders.py:cancel_order() (384-395)
# на отмену возвращает склад (restore_order_stock) и монеты (add_coins,
# "refund"), но нигде не увеличивает uses_left обратно. Значит промокод с
# ограничением «на первые N покупателей» тратится и на заказы, которые
# покупатель сам отменил или которые продавец отклонил (кончился товар,
# не подошёл чек) — то есть на заказы, где скидка никому фактически не
# досталась.
#
# once_per_user при этом починен верно: и check_promo(), и _reserve_promo()
# считают "COUNT(*) ... AND status != 'canceled'", так что отменённый заказ
# не блокирует повторное использование ОДНИМ и тем же покупателем. Проблема
# только в счётчике uses_left.
def run_promo_uses_left_not_restored_on_cancel():
    c = Checker("Промокод: остаток применений не возвращается при отмене")
    _clean()
    as_admin()

    db.add_delivery_method("Минск", "Самовывоз", False, "", "", 0, True, 0)
    method = db.get_delivery_methods("Минск")[0]
    pid = db.add_product("Минск", "pods", "QA-промо-товар", 50.0, 10)
    client.post("/api/admin/promo", json={"initData": "x", "code": "FIRST5", "kind": "fixed",
                                          "value": "10", "uses_left": "1", "once_per_user": False})
    cache.bust()

    BUYER, OTHER = 8801, 8802
    db.set_age_ok(BUYER); db.set_age_ok(OTHER)

    as_user(BUYER, "buyer")
    r = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": method["id"],
        "payment_method": "cash", "items": [{"id": pid, "qty": 1}], "promo_code": "FIRST5"})
    d = r.get_json()
    c("заказ со скидкой оформлен", d.get("ok"))
    oid = d["order_id"]
    c("применение сразу списано", db._promo_row("FIRST5")["uses_left"] == 0)

    # Покупатель передумал ДО подтверждения продавцом — заказ ещё new/paid.
    r = client.post("/api/order/cancel", json={"initData": "x", "order_id": oid})
    c("клиент отменил заказ", r.get_json().get("ok"))
    c("заказ правда отменён", db.get_order(oid)["status"] == "canceled")
    c("склад вернулся на полку (это уже работает верно)", db.get_product(pid)["stock"] == 10)

    c("БАГ [orders.py:384-395 / promos.py] uses_left должен вернуться после отмены "
      "заказа, где скидкой никто не воспользовался",
      db._promo_row("FIRST5")["uses_left"] == 1)

    # Видимое следствие: второй покупатель уже не может получить код, хотя
    # реально его не использовал никто.
    as_user(OTHER, "other")
    r = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": method["id"],
        "payment_method": "cash", "items": [{"id": pid, "qty": 1}], "promo_code": "FIRST5"})
    d = r.get_json()
    c("СЛЕДСТВИЕ БАГА: код показывает «разобран» другому покупателю, хотя скидку "
      "не получил ещё никто (ожидалось бы, что код доступен)",
      d.get("error") != "promo_used_up")

    # --- Тот же дефект — при отклонении заказа ПРОДАВЦОМ, не только при
    #     самостоятельной отмене клиентом. ---
    client.post("/api/admin/promo", json={"initData": "x", "code": "SELLERCANCEL", "kind": "fixed",
                                          "value": "5", "uses_left": "1", "once_per_user": False})
    as_user(BUYER)
    r = client.post("/api/order", json={
        "initData": "x", "city": "Минск", "delivery_method_id": method["id"],
        "payment_method": "cash", "items": [{"id": pid, "qty": 1}], "promo_code": "SELLERCANCEL"})
    oid2 = r.get_json()["order_id"]
    as_admin()
    # Заказ наличными сразу в статусе 'paid' — продавец отклоняет как "товара не оказалось".
    r = client.post("/api/admin/order/status",
                     json={"initData": "x", "id": oid2, "action": "reject", "reason": "out"})
    c("продавец отклонил заказ", r.get_json().get("ok"))
    c("БАГ [orders.py: api_admin_order_status → cancel_order] то же самое, когда "
      "заказ отклоняет продавец, а не клиент",
      db._promo_row("SELLERCANCEL")["uses_left"] == 1)

    _clean()
    return c.fails


# ============================================================
# 3. Загрузка фото товара/модели: тип и размер файла не проверяются
# ============================================================
#
# partut/web/catalog.py: api_admin_photo() (487-511), api_admin_photo_add()
# (514-541), api_admin_model_photo() (650-676) — везде один и тот же путь:
# `file = request.files.get("file"); ...; tgsend.tg.send_photo(uid, file.read(), ...)`.
# Ни MIME-тип (`file.content_type`/`file.mimetype`), ни расширение, ни размер
# байт нигде не проверяются перед отправкой в Telegram — единственная защита
# это ОБЩИЙ потолок тела запроса в 12 МБ на ВСЕ admin-ручки разом
# (server.py:69, MAX_CONTENT_LENGTH), не специфичный для фото.
#
# На фронтенде (06-catalog.js:519, 548) это только `accept="image/*"` —
# подсказка браузеру, а не проверка: обходится переименованием файла или
# прямым запросом мимо интерфейса.
def run_photo_upload_accepts_any_file():
    c = Checker("Загрузка фото: содержимое и тип файла не проверяются")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "QA-фото-товар", 10.0, 5)

    поддельный_файл = (b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64, "payload.exe")
    r = client.post("/api/admin/photo", data={
        "initData": "x", "id": str(pid),
        "file": (__import__("io").BytesIO(поддельный_файл[0]), поддельный_файл[1],
                 "application/octet-stream"),
    }, content_type="multipart/form-data")
    d = r.get_json() or {}
    c("БАГ [catalog.py:487-511] сервер должен отклонять не-изображение по "
      "content-type/расширению до отправки в Telegram, а не принимать что угодно",
      not d.get("ok"))

    # То же для галереи модели.
    mid = db.add_model("pods", "QA-фото-модель", "", "", {}, [])
    r = client.post("/api/admin/photo/add", data={
        "initData": "x", "model_id": str(mid),
        "file": (__import__("io").BytesIO(поддельный_файл[0]), "payload2.exe",
                 "application/octet-stream"),
    }, content_type="multipart/form-data")
    d = r.get_json() or {}
    c("БАГ [catalog.py:514-541] то же самое для фото галереи модели",
      not d.get("ok"))

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    fails = (run_negative_price_and_stock()
             + run_promo_uses_left_not_restored_on_cancel()
             + run_photo_upload_accepts_any_file())
    sys.exit(1 if fails else 0)
