"""Вторая наводка 3.10.2026 — витрина (partut/db/catalog_fix.py, apply_storefront_fix).

Данные — как на боевом сайте после первой наводки: картридж «XROS», вкусы
PILOW TALK с маленькой буквы, подписи категорий с маленькой и «mah».

Главное, что проверяем, кроме самих правок: заказ, оформленный ДО
переименования вкуса, после него отменяется и возвращает штуки на ту же
полку. Отмена ищет вариант по точному названию — без правки состава заказа
штуки пропали бы молча.
"""
import json
import threading
import time

from _common import db, Checker

from partut.db import catalog_fix

ПОКУПАТЕЛЬ = 77001


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models", "brands"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (catalog_fix.ОТМЕТКА_ВИТРИНЫ,))
    cur.execute(db._q("DELETE FROM categories WHERE code = %s"), ("snyus",))
    cur.execute(db._q("DELETE FROM category_specs WHERE category = %s"), ("snyus",))
    cur.execute(db._q("DELETE FROM category_specs WHERE category = %s AND key IN (%s, %s)"),
                ("disposable", "krepost", "batareya"))
    conn.commit(); conn.close()


def _подписи():
    """Подписи категорий — как их завёл владелец."""
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("INSERT INTO categories (code, name, emoji, sort, has_flavors, variant_label, variant_label2) "
                      "VALUES (%s, %s, %s, %s, %s, %s, %s)"), ("snyus", "снюс", "🍭", 30, 1, "Вкус", "крепость"))
    for cat, key, label, unit, sort in (("snyus", "krepost", "крепость", "мг", 10),
                                        ("disposable", "krepost", "крепость", "%", 20),
                                        ("disposable", "batareya", "батарея", "mah", 30)):
        cur.execute(db._q("INSERT INTO category_specs (category, key, label, unit, kind, options, sort) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s)"), (cat, key, label, unit, "number", "[]", sort))
    conn.commit(); conn.close()


ВКУСЫ_БЫЛО = ["клубника манго", "клубника банан", "экзотические фрукты клубника", "Виноград", "Черная вишня"]
ВКУСЫ_СТАЛО = ["Клубника манго", "Клубника банан", "Экзотические фрукты клубника", "Виноград", "Черная вишня"]


def _как_на_сайте():
    db.add_brand("PILOW TALK", "disposable", ["клубника манго", "клубника банан", "экзотические фрукты клубника",
                                              "Виноград", "черная вишня"])
    db.add_brand("VAPORESSO", "", ["0.4Ω", "0.6Ω"])
    xros = db.add_model("coils", "XROS", "VAPORESSO", "", {"kind": "Картридж"}, ["0.6Ω", "0.4Ω"])
    pilow = db.add_model("disposable", "PILOW TALK IC40000", "PILOW TALK", "", {"volume": "40000"}, ВКУСЫ_БЫЛО)
    т = {"xros": db.create_point_product(xros, "Минск", 14.0, 7.0, variants=[{"flavor": "0.6Ω", "stock": 9}])}
    for city in ("Минск", "Туров"):
        т[city] = db.create_point_product(pilow, city, 40.0, 20.0,
                                          variants=[{"flavor": f, "stock": 3} for f in ВКУСЫ_БЫЛО])
    _подписи()
    return xros, pilow, т


def _вкусы_точки(pid):
    return {v["flavor"]: v["stock"] for v in db.get_variants(pid)}


def _бренд(name):
    b = next((x for x in db.get_brands() if x["name"] == name), None)
    return json.loads(b["flavors"] or "[]") if b else None


def _категория(code):
    return next((c for c in db.list_categories() if c["code"] == code), None)


def _подпись(cat, key):
    return next((s for s in db.list_category_specs(cat) if s["key"] == key), None)


def run():
    c = Checker("Наводка витрины 3.10")
    _чисто()
    xros, pilow, т = _как_на_сайте()
    db.ensure_user(ПОКУПАТЕЛЬ)
    # Заказ ДО правки: 2 шт «клубника манго» в Минске — ждёт выдачи.
    заказ, *_ = db.place_order(ПОКУПАТЕЛЬ, "qa", "Минск",
                               [{"id": т["Минск"], "name": "PILOW TALK IC40000 — клубника манго", "price": 40.0,
                                 "qty": 2, "flavor": "клубника манго"}],
                               80.0, 0, 0.01, 0, "Самовывоз", "", "cash", "", "", "confirmed")
    # И продажа на точке в Турове — её тоже можно отменить «ошиблись».
    продажа = db.record_point_sale("Туров", [{"id": т["Туров"], "flavor": "клубника банан", "qty": 1, "price": 40}],
                                   1, "qa")[0]
    c("до правки: в Минске манго 1 (2 в заказе), в Турове банан 2",
      _вкусы_точки(т["Минск"])["клубника манго"] == 1 and _вкусы_точки(т["Туров"])["клубника банан"] == 2)

    шагов = catalog_fix.apply_storefront_fix()
    c(f"шагов сделано: {шагов}", шагов == 9)

    m = db.get_model(xros)
    c("картридж называется «VAPORESSO XROS» — и в описании, и на точке",
      m["name"] == "VAPORESSO XROS" and db.get_product(т["xros"])["name"] == "VAPORESSO XROS"
      and db.get_product(т["xros"])["brand"] == "VAPORESSO")

    c("PILOW TALK, Минск: вкусы с большой, штуки те же",
      _вкусы_точки(т["Минск"]) == {"Клубника манго": 1, "Клубника банан": 3, "Экзотические фрукты клубника": 3,
                                   "Виноград": 3, "Черная вишня": 3})
    c("PILOW TALK, Туров: тоже", set(_вкусы_точки(т["Туров"])) == set(ВКУСЫ_СТАЛО))
    c("общий остаток товара не изменился", db.get_product(т["Минск"])["stock"] == 13 and db.get_product(т["Туров"])["stock"] == 14)
    c("описание PILOW TALK — вкусы с большой, порядок прежний", db.get_model(pilow)["flavors"] == ВКУСЫ_СТАЛО)
    c("бренд PILOW TALK — вкусы с большой, и «Черная вишня» тоже", _бренд("PILOW TALK") == ВКУСЫ_СТАЛО)
    it = json.loads(db.get_order(заказ)["items"])[0]
    c("в заказе вкус и название позиции — с большой", it["flavor"] == "Клубника манго"
      and it["name"] == "PILOW TALK IC40000 — Клубника манго")

    # Главное: отмена старого заказа возвращает штуки на переименованную полку.
    db.cancel_order(заказ)
    c("отмена заказа, оформленного до правки, вернула 2 шт на «Клубника манго»",
      db.get_order(заказ)["status"] == "canceled" and _вкусы_точки(т["Минск"])["Клубника манго"] == 3)
    db.cancel_point_sale(продажа)
    c("отмена продажи на точке тоже вернула штуку на «Клубника банан»", _вкусы_точки(т["Туров"])["Клубника банан"] == 3)
    c("старых названий не осталось нигде на точках",
      not any(f in catalog_fix.PILOW_ВКУСЫ for p in (т["Минск"], т["Туров"]) for f in _вкусы_точки(p)))
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("SELECT COUNT(*) AS n FROM stock_moves WHERE flavor IN (%s, %s, %s)"), tuple(catalog_fix.PILOW_ВКУСЫ))
    старых_движений = int(cur.fetchone()["n"])
    conn.close()
    c("история склада — тоже с большой", старых_движений == 0)

    c("категория «Снюс», выбор «Крепость»", _категория("snyus")["name"] == "Снюс" and _категория("snyus")["variant_label2"] == "Крепость")
    c("характеристики: «Крепость» у снюса и одноразок, «Батарея», «мАч»",
      _подпись("snyus", "krepost")["label"] == "Крепость" and _подпись("disposable", "krepost")["label"] == "Крепость"
      and _подпись("disposable", "batareya")["label"] == "Батарея" and _подпись("disposable", "batareya")["unit"] == "мАч")
    журнал = [r["details"] for r in db.list_admin_log(limit=50)
              if r["action"] == "catalog/fix" and r["admin_name"] == catalog_fix.КТО_ВИТРИНА]
    c(f"всё в журнале действий: {len(журнал)}", len(журнал) == 10 and any("готово, шагов: 9" in x for x in журнал))
    c("второй запуск — не запускается", catalog_fix.apply_storefront_fix() is None)

    # Отметку сняли (как на базе, где уже всё поправлено) — ноль шагов, ничего не ломает.
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (catalog_fix.ОТМЕТКА_ВИТРИНЫ,))
    conn.commit(); conn.close()
    c("на уже поправленных данных — ноль шагов", catalog_fix.apply_storefront_fix() == 0)
    журнал = [r["details"] for r in db.list_admin_log(limit=80)
              if r["action"] == "catalog/fix" and r["admin_name"] == catalog_fix.КТО_ВИТРИНА]
    c("…и в журнале нет ложного «НЕ переименованы»", not any("НЕ переименованы" in x for x in журнал))

    # На точке уже есть «Клубника манго» с большой рядом со старой — вкусы не
    # трогаем вовсе (сливать два варианта — не наша задача), остальное делаем.
    # Через экран такого двойника не завести (вариант, отличный только
    # регистром, приложение не примет), поэтому — прямо в базу, как в старых данных.
    _чисто()
    xros, pilow, т = _как_на_сайте()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("INSERT INTO product_variants (product_id, flavor, stock) VALUES (%s, %s, %s)"),
                (т["Туров"], "Клубника манго", 0))
    conn.commit(); conn.close()
    catalog_fix.apply_storefront_fix()
    c("в журнале — честно: «НЕ переименованы»", any("НЕ переименованы" in r["details"] for r in db.list_admin_log(limit=20)
                                                    if r["admin_name"] == catalog_fix.КТО_ВИТРИНА))
    c("на точке уже есть «Клубника манго» — вкусы PILOW TALK не переименованы нигде",
      "клубника манго" in _вкусы_точки(т["Минск"]) and "клубника манго" in _вкусы_точки(т["Туров"])
      and db.get_model(pilow)["flavors"] == ВКУСЫ_БЫЛО)
    c("…а картридж и подписи — поправлены", db.get_model(xros)["name"] == "VAPORESSO XROS" and _категория("snyus")["name"] == "Снюс")

    # Гонка (только Postgres; в SQLite замок один на всю базу): продавец правит
    # состав заказа прежней копией сайта ровно во время правки. Правка обязана
    # дождаться его и переименовать вкус в НОВОМ составе, а не затереть его
    # составом, прочитанным до правки продавца.
    if db.USE_PG:
        _чисто()
        xros, pilow, т = _как_на_сайте()
        заказ, *_ = db.place_order(ПОКУПАТЕЛЬ, "qa", "Минск",
                                   [{"id": т["Минск"], "name": "PILOW TALK IC40000 — клубника манго", "price": 40.0,
                                     "qty": 1, "flavor": "клубника манго"}],
                                   40.0, 0, 0.01, 0, "Самовывоз", "", "cash", "", "", "confirmed")
        держит, готово = threading.Event(), threading.Event()

        def продавец():
            conn = db.connect(); cur = conn.cursor()
            cur.execute("SELECT items FROM orders WHERE id = %s FOR UPDATE", (заказ,))
            items = json.loads(cur.fetchone()["items"])
            items[0]["qty"] = 3                                   # продавец: «не 1, а 3»
            cur.execute("UPDATE orders SET items = %s WHERE id = %s", (json.dumps(items, ensure_ascii=False), заказ))
            держит.set()
            time.sleep(0.8)
            conn.commit(); conn.close()
            готово.set()

        поток = threading.Thread(target=продавец)
        поток.start()
        держит.wait(5)
        catalog_fix.apply_storefront_fix()
        поток.join(5)
        it = json.loads(db.get_order(заказ)["items"])[0]
        c("правка дождалась продавца: в заказе его «3 шт», и вкус уже «Клубника манго»",
          готово.is_set() and it["qty"] == 3 and it["flavor"] == "Клубника манго")

    # Подписи, которые владелец уже поменял по-своему, не трогаем.
    _чисто()
    _как_на_сайте()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE categories SET name = %s WHERE code = %s"), ("Снюс и пэкки", "snyus"))
    cur.execute(db._q("UPDATE category_specs SET unit = %s WHERE category = %s AND key = %s"), ("mAh", "disposable", "batareya"))
    conn.commit(); conn.close()
    catalog_fix.apply_storefront_fix()
    c("своё название категории и своя единица владельца — не тронуты",
      _категория("snyus")["name"] == "Снюс и пэкки" and _подпись("disposable", "batareya")["unit"] == "mAh")
    _чисто()
    return c.fails
