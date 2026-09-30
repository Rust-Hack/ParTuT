"""QP-03: старый запрос цены не проходит поверх нового.

Приёмка 573d7cb (docs/product-row-acceptance-573d7cb): сверка цены по значению
не видит сохранения той же цены. Продавец жмёт «25 → 30», запрос застревает в
сети; открывает окно заново, оставляет 25 и сохраняет. Если сервер получит
второй запрос раньше первого, то «25 → 25, ожидаю 25» пройдёт, а следом и
застрявший «25 → 30, ожидаю 25» — там ведь по-прежнему 25. Итог 30, хотя
последним решением продавца было 25.

Теперь у цены номер версии (price_rev): любая запись цены его увеличивает,
даже той же самой, и запрос со старым номером сервер не пишет, а отвечает,
что там теперь. Здесь — настоящий обработчик и база; порядок запросов задан
прямо: сначала новый, потом старый.
"""
from _common import db, client, Checker, as_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM stock_moves")
    cur.execute("DELETE FROM product_variants")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def _правка(pid, fields, expected):
    return client.post("/api/admin/product/update",
                       json={"initData": "x", "id": pid, "fields": fields, "expected": expected}).get_json()


def _конфликт(ответ, поле="price"):
    return ((ответ.get("failed") or {}).get(поле) or {}).get("error") == "conflict"


def _версия(pid):
    return int(db.get_product(pid)["price_rev"])


def run():
    c = Checker("QP-03: старый запрос цены не проходит поверх нового")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "accessories", "Порядок запросов", 25, 3)
    c("у нового товара версия цены 0", _версия(pid) == 0)

    # --- Запросы пришли в обратном порядке: сначала новый, потом старый ---
    новый = _правка(pid, {"price": 25}, {"price": 25, "price_rev": 0})
    c("новый («25 → 25», версия 0) сохранён", новый.get("saved") == ["price"])
    c("и сервер назвал новую версию — 1", новый.get("price_rev") == 1)
    старый = _правка(pid, {"price": 30}, {"price": 25, "price_rev": 0})
    отказ = (старый.get("failed") or {}).get("price") or {}
    c("старый («25 → 30», версия 0) не записан — конфликт", старый.get("saved") == [] and _конфликт(старый))
    c("в отказе — что там сейчас: 25, версия 1",
      float(отказ.get("current")) == 25 and отказ.get("current_rev") == 1)
    c("и сказано «сохраняли», а не «поменяли»: число-то то же", "сохраняли" in (отказ.get("message") or ""))
    p = db.get_product(pid)
    c("итог — 25, последнее решение продавца", float(p["price"]) == 25)
    c("остаток не тронут", int(p["stock"]) == 3)

    # --- Та же цена со свежим номером — тоже новая версия ---
    r = _правка(pid, {"price": 25}, {"price": 25, "price_rev": 1})
    c("та же цена со свежей версией сохранена, версия 2", r.get("saved") == ["price"] and r.get("price_rev") == 2)

    # --- Старое приложение без номера версии (QP-03-L) ---
    # Приёмка 8ae2ea9: запрос без номера сверялся только по значению, и
    # застрявший запрос приложения, открытого до выкатки номера, проходил
    # поверх свежей цены. Теперь цену без номера сервер не меняет вовсе и
    # просит открыть приложение заново; остальные поля запроса сохраняются.
    цена_до, версия_до = float(db.get_product(pid)["price"]), _версия(pid)
    r = _правка(pid, {"price": 26, "description": "без номера"}, {"price": 25})
    отказ = (r.get("failed") or {}).get("price") or {}
    c("без номера: цена не принята — «приложение устарело, откройте заново»",
      отказ.get("error") == "stale_app" and "заново" in (отказ.get("message") or ""))
    c("без номера: цена и версия не тронуты", float(db.get_product(pid)["price"]) == цена_до and _версия(pid) == версия_до)
    c("без номера: остальные поля того же запроса сохранены", r.get("saved") == ["description"])
    # Сценарий проверяющего: старый запрос «30, ожидаю 25» дошёл после двух
    # свежих правок — «27», затем снова «25».
    _правка(pid, {"price": 27}, {"price": 25, "price_rev": _версия(pid)})
    _правка(pid, {"price": 25}, {"price": 27, "price_rev": _версия(pid)})
    свежая = _версия(pid)
    r = _правка(pid, {"price": 30}, {"price": 25})
    c("запоздавший запрос старого приложения не проходит поверх свежей цены",
      ((r.get("failed") or {}).get("price") or {}).get("error") == "stale_app"
      and float(db.get_product(pid)["price"]) == 25 and _версия(pid) == свежая)
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "field": "price", "value": "31"})
    c("одиночное поле с ценой без номера — 409 и понятный текст",
      r.status_code == 409 and r.get_json().get("error") == "stale_app" and r.get_json().get("message"))
    c("и цена цела", float(db.get_product(pid)["price"]) == 25)

    # --- Бот меняет цену своим путём — версия растёт и там ---
    было = _версия(pid)
    db.update_field(pid, "price", 27)
    c("правка цены из бота увеличила версию", _версия(pid) == было + 1)
    r = _правка(pid, {"price": 28}, {"price": 27, "price_rev": было})
    c("запрос из приложения со старой версией после правки бота — конфликт",
      _конфликт(r) and float(db.get_product(pid)["price"]) == 27)
    db.update_field(pid, "description", "не цена")
    c("правка другого поля из бота версию цены не трогает", _версия(pid) == было + 1)

    # --- Прочие поля: номер цены им не мешает и не растёт от них ---
    v = _версия(pid)
    r = _правка(pid, {"cost": 12, "description": "описание"}, {"price_rev": 0})
    c("закупка и описание сохранены, хоть номер цены и старый",
      set(r.get("saved") or []) == {"cost", "description"} and not r.get("failed"))
    c("и номер цены от них не вырос", _версия(pid) == v and "price_rev" not in r)

    # --- Карточка: цена со старым номером — конфликт, остальное сохраняется ---
    r = _правка(pid, {"price": 31, "cost": 13}, {"price": 27, "cost": 12, "price_rev": 0})
    c("карточка: цена со старым номером не записана, закупка записана",
      _конфликт(r) and r.get("saved") == ["cost"] and float(db.get_product(pid)["price"]) == 27)

    # --- Непонятный номер — не тот ---
    for кривой in ("abc", None, [1]):
        r = _правка(pid, {"price": 29}, {"price": 27, "price_rev": кривой})
        c(f"номер версии {кривой!r} — конфликт, а не запись", _конфликт(r))
    c("цена от кривых номеров не поменялась", float(db.get_product(pid)["price"]) == 27)

    # --- Журнал: несохранённая цена так и названа ---
    from flask import g
    from partut.web import auth

    def владелец(init):
        u = {"id": 779, "username": "owner", "role": "owner", "city": "", "name": "Владелец"}
        g.admin = u               # как настоящий get_admin: журнал берёт админа отсюда
        return u

    старый = auth.get_admin
    auth.get_admin = владелец
    try:
        _правка(pid, {"price": 40}, {"price": 27, "price_rev": 0})
        запись = db.list_admin_log(limit=1)[0]["details"] or ""
        c(f"в журнале — «не сохранено», число то же — «сохраняли»: {запись}",
          "не сохранено — её уже сохраняли, сейчас 27.00 Br" in запись)
        _правка(pid, {"price": 40}, {"price": 20, "price_rev": _версия(pid)})
        запись = db.list_admin_log(limit=1)[0]["details"] or ""
        c(f"число другое — «поменяли»: {запись}", "не сохранено — уже поменяли, сейчас 27.00 Br" in запись)
    finally:
        auth.get_admin = старый

    # --- Список управления отдаёт номер, витрина покупателя — нет ---
    cache.bust()
    админ = client.post("/api/admin/products", json={"initData": "x"}).get_json()
    мой = [x for x in админ.get("products", []) if x["id"] == pid]
    c("в списке управления есть номер версии цены", мой and мой[0].get("price_rev") == _версия(pid))
    витрина = client.get("/api/products").get_json()
    c("витрине покупателя номер не отдаётся", витрина and all("price_rev" not in x for x in витрина))

    # --- Миграция на базе без колонки — как боевая до выкатки ---
    c2 = Checker("QP-03: колонка версии цены появляется на старой базе")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("ALTER TABLE products DROP COLUMN price_rev")      # только тестовая база
    conn.commit(); conn.close()
    c2("колонки нет — как на старой базе", "price_rev" not in db.get_product(pid).keys())
    db._ensure_price_rev_column()
    c2("колонка добавлена, у старого товара версия 0", _версия(pid) == 0)
    db._ensure_price_rev_column()
    c2("повторный запуск ничего не ломает", _версия(pid) == 0)
    r = _правка(pid, {"price": 33}, {"price": 27, "price_rev": 0})
    c2("после миграции правка цены работает", r.get("saved") == ["price"] and r.get("price_rev") == 1)
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
