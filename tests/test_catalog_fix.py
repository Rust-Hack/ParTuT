"""Разовая наводка порядка в каталоге 3.10.2026 (partut/db/catalog_fix.py).

Данные — точная копия нужного куска выгрузки с боевого сайта (бренды,
описания, точки). Проверяем каждый шаг, что второй запуск ничего не делает,
что уже поправленное руками не трогается и что GLITCH в Турове не
переименовывается, если по нему появились штуки.
"""
import json

from _common import db, Checker

from partut.db import catalog_fix


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models", "brands"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (catalog_fix.ОТМЕТКА,))
    conn.commit(); conn.close()


def _как_на_сайте():
    """Состояние с боевой выгрузки 3.10.2026 — только то, что наводка трогает."""
    db.add_brand("ANNIMA LOVE", "liquid", ["Кислая вишня", "Клубника киви", "Морозные лесные ягоды", "Смородина вишня черника",
                                           "сочный персик", "Черника ежевика", "Энергетик ягоды"])
    db.add_brand("GLITCH", "snyus", ["Двойная мята вишня", "Холодная груша"])
    db.add_brand("VAPORESSO", "coils", ["0.4Ω", "0.6Ω"])
    db.add_brand("ЗЛАЯ МИЛФА", "liquid", ["Тропические фрукты", "Персик маракуйя", "Морс из лесных ягод",
                                          "Ежевика малина", "Лесные ягоды", "Черничный энергетик"])
    db.add_brand("ЗЛАЯ МИЛФА ACID", "liquid", ["Ежевика малина", "Лесные ягоды", "Черничный энергетик"])
    db.add_brand("ПОШЛАЯ РАБЫНЯ", "liquid", [])
    м = {}
    м["xros"] = db.add_model("coils", "Xros", "VAPORESSO XROS", "", {"kind": "Картридж"}, ["0.6Ω", "0.4Ω"])
    м["pilow"] = db.add_model("disposable", "PILOW TALK IC40000", "", "", {"volume": "40000"}, ["Виноград"])
    м["acid"] = db.add_model("liquid", "ЗЛАЯ МИЛФА ACID", "ЗЛАЯ МИЛФА ACID", "", {"strength": "70"}, ["Лесные ягоды"])
    м["drymost"] = db.add_model("snyus", "DRYMOST", "DRYMOST", "", {"krepost": "175-200"},
                                ["Ледяная груша-200", "Мята-200", "Сияющая вишня-175",
                                 "200 мг · Ледяная груша", "200 мг · Мята", "175 мг · Сияющая вишня"])
    м["glitch"] = db.add_model("snyus", "GLITCH", "GLITCH", "", {"krepost": "200"},
                               ["Холодная груша", "Двойная мята-вишня", "200 мг · Холодная груша",
                                "200 мг · Двойная мята-вишня", "200 мг · Двойная мята"])
    м["рабыня"] = db.add_model("liquid", "ПОШЛАЯ РАБЫНЯ", "ПОШЛАЯ РАБЫНЯ", "", {"strength": "83"},
                               ["Манго маракуйя", "Клюквенный морс", "Виноград смородина", "Апельсин виноград",
                                "Цитрусовый микс", "Энергетик", "Спелая малина"])
    т = {}
    т["xros_минск"] = db.create_point_product(м["xros"], "Минск", 8.0, 6.5, variants=[{"flavor": "0.6Ω", "stock": 9}])
    conn = db.connect(); cur = conn.cursor()       # на точках бренд уже переименован старым кодом
    cur.execute(db._q("UPDATE products SET brand = 'VAPORESSO' WHERE id = %s"), (т["xros_минск"],))
    conn.commit(); conn.close()
    т["pilow"] = db.create_point_product(м["pilow"], "Минск", 19.0, 12.0, variants=[{"flavor": "Виноград", "stock": 1}])
    т["acid"] = db.create_point_product(м["acid"], "Минск", 15.0, 8.8, variants=[{"flavor": "Лесные ягоды", "stock": 1}])
    т["drymost"] = db.create_point_product(м["drymost"], "Минск", 12.0, 7.9, variants=[{"flavor": "200 мг · Мята", "stock": 2}])
    т["glitch_минск"] = db.create_point_product(м["glitch"], "Минск", 12.0, 7.2,
                                                variants=[{"flavor": "200 мг · Холодная груша", "stock": 2}])
    т["glitch_туров"] = db.create_point_product(м["glitch"], "Туров", 12.0, 7.2,
                                                variants=[{"flavor": "Холодная груша", "stock": 0},
                                                          {"flavor": "Двойная мята-вишня", "stock": 0}])
    db.update_field(т["glitch_туров"], "hidden", 1)
    return м, т


def _бренд(name):
    b = next((x for x in db.get_brands() if x["name"] == name), None)
    return dict(b, flavors=json.loads(b["flavors"] or "[]")) if b else None


def run():
    c = Checker("Наводка порядка в каталоге 3.10")
    _чисто()
    м, т = _как_на_сайте()
    шагов = catalog_fix.apply_catalog_fix()
    c(f"шагов сделано: {шагов}", шагов == 11)

    x = db.get_model(м["xros"])
    c("картридж: бренд VAPORESSO, название XROS — и в описании, и на точке",
      (x["brand"], x["name"]) == ("VAPORESSO", "XROS")
      and (db.get_product(т["xros_минск"])["brand"], db.get_product(т["xros_минск"])["name"]) == ("VAPORESSO", "XROS"))
    c("PILOW TALK IC40000: бренд PILOW TALK, на точке тоже",
      db.get_model(м["pilow"])["brand"] == "PILOW TALK" and db.get_product(т["pilow"])["brand"] == "PILOW TALK")
    c("ЗЛАЯ МИЛФА ACID — линейка бренда ЗЛАЯ МИЛФА, название прежнее",
      db.get_model(м["acid"])["brand"] == "ЗЛАЯ МИЛФА" and db.get_model(м["acid"])["name"] == "ЗЛАЯ МИЛФА ACID"
      and db.get_product(т["acid"])["brand"] == "ЗЛАЯ МИЛФА")
    c("бренд «ЗЛАЯ МИЛФА ACID» удалён, «ЗЛАЯ МИЛФА» на месте", _бренд("ЗЛАЯ МИЛФА ACID") is None and _бренд("ЗЛАЯ МИЛФА"))
    c("DRYMOST: в описании только «… · …»",
      db.get_model(м["drymost"])["flavors"] == ["200 мг · Ледяная груша", "200 мг · Мята", "175 мг · Сияющая вишня"])
    c("GLITCH: в описании два вкуса «200 мг · …», без старых и без лишней «Двойной мяты»",
      db.get_model(м["glitch"])["flavors"] == ["200 мг · Холодная груша", "200 мг · Двойная мята-вишня"])
    c("GLITCH · Туров: вкусы «200 мг · …» по 0 шт, товар по-прежнему снят с витрины",
      {v["flavor"]: v["stock"] for v in db.get_variants(т["glitch_туров"])} == {"200 мг · Холодная груша": 0, "200 мг · Двойная мята-вишня": 0}
      and db.get_product(т["glitch_туров"])["hidden"] == 1)
    c("остатки нигде не тронуты: картридж 9, GLITCH Минск 2",
      db.get_product(т["xros_минск"])["stock"] == 9 and db.get_product(т["glitch_минск"])["stock"] == 2)
    c("VAPORESSO — «Во всех категориях»", not _бренд("VAPORESSO")["category"])
    c("ANNIMA LOVE: «Сочный персик» с большой", "Сочный персик" in _бренд("ANNIMA LOVE")["flavors"]
      and "сочный персик" not in _бренд("ANNIMA LOVE")["flavors"])
    c("GLITCH (бренд): «Двойная мята-вишня», как в товаре", _бренд("GLITCH")["flavors"] == ["Двойная мята-вишня", "Холодная груша"])
    c("ПОШЛАЯ РАБЫНЯ: 7 вкусов из описания", len(_бренд("ПОШЛАЯ РАБЫНЯ")["flavors"]) == 7)
    журнал = [r["details"] for r in db.list_admin_log(limit=50) if r["action"] == "catalog/fix"]
    c(f"всё записано в журнал действий: {len(журнал)}", len(журнал) == 12 and any("готово" in x for x in журнал))
    c("второй запуск — не запускается", catalog_fix.apply_catalog_fix() is None)

    # Уже поправлено руками (или данные другие) — шаги пропускаются, ничего не ломая.
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (catalog_fix.ОТМЕТКА,))
    conn.commit(); conn.close()
    c("на уже поправленных данных — ноль шагов", catalog_fix.apply_catalog_fix() == 0)

    # GLITCH в Турове со штуками — не переименовываем (вкус — складская позиция).
    _чисто()
    м, т = _как_на_сайте()
    db.stock_operation(т["glitch_туров"], "in", 3, flavor="Холодная груша", admin_id=1)
    catalog_fix.apply_catalog_fix()
    c("в Турове появились штуки — его вкусы не переименованы",
      {v["flavor"] for v in db.get_variants(т["glitch_туров"])} == {"Холодная груша", "Двойная мята-вишня"})
    c("…и эти вкусы остались в описании GLITCH — им есть кому принадлежать",
      "Холодная груша" in db.get_model(м["glitch"])["flavors"] and "200 мг · Двойная мята" not in db.get_model(м["glitch"])["flavors"])
    _чисто()
    return c.fails
