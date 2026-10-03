"""
partut/db/catalog_fix.py — разовая наводка порядка в каталоге (3.10.2026).

Владелец попросил поправить справочник за него (сверка выгрузки с боевого
сайта и сборочного листа № 03524). Делаем это кодом, а не руками в базе:
так правка проверена тестами и видна в истории.

Каждый шаг ищет запись по ИМЕНИ (номера записей на каждой базе свои) и
срабатывает, только если данные в точности такие, как в выгрузке владельца.
Что-то уже поправили руками или выглядит иначе — шаг пропускается, ничего
не ломая. Всё сделанное пишется в журнал действий («наводка порядка»).
Отметка в settings — второй раз не запускается.
"""

import json

from partut import db

ОТМЕТКА = "catalog_fix_20261003"
КТО = "наводка порядка 3.10"


# Что сделано — пишем в журнал ПОСЛЕ сохранения: запись журнала — своё
# соединение, и посреди нашей транзакции SQLite упёрся бы в замок базы.
_сделано = []


def _журнал(сделано):
    print(f"[база] {КТО}: {сделано}", flush=True)
    _сделано.append(сделано)


def _в_журнал():
    for x in _сделано:
        try:
            db.log_admin_action(0, КТО, "catalog/fix", x)
        except Exception as e:                   # журнал — не повод не чинить
            print(f"[база] {КТО}: не записал в журнал: {e}", flush=True)
    _сделано.clear()


def _модель(cur, category, name, brand):
    cur.execute(db._q("SELECT * FROM models WHERE category = %s AND name = %s AND COALESCE(brand, '') = %s"),
                (category, name, brand))
    rows = cur.fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def _вкусы(m):
    try:
        return list(json.loads(m["flavors"] or "[]"))
    except (TypeError, ValueError):
        return []


def _бренд_модели(cur, category, name, было, стало):
    """Описание (модель) и все её точки — на новый бренд."""
    m = _модель(cur, category, name, было)
    if not m:
        return False
    cur.execute(db._q("UPDATE models SET brand = %s WHERE id = %s"), (стало, m["id"]))
    cur.execute(db._q("UPDATE products SET brand = %s WHERE model_id = %s"), (стало, m["id"]))
    _журнал(f"«{name}»: бренд «{было or '—'}» → «{стало}» (описание и точки)")
    return True


def _убрать_вкусы_модели(cur, category, name, brand, лишние):
    """Убрать из описания вкусы, которых нет ни на одной точке этой модели."""
    m = _модель(cur, category, name, brand)
    if not m:
        return False
    cur.execute(db._q("SELECT DISTINCT v.flavor FROM product_variants v JOIN products p ON p.id = v.product_id "
                      "WHERE p.model_id = %s"), (m["id"],))
    на_точках = {r["flavor"] for r in cur.fetchall()}
    было = _вкусы(m)
    убрать = [f for f in было if f in лишние and f not in на_точках]
    if not убрать:
        return False
    стало = [f for f in было if f not in убрать]
    cur.execute(db._q("UPDATE models SET flavors = %s WHERE id = %s"), (json.dumps(стало, ensure_ascii=False), m["id"]))
    _журнал(f"«{name}»: из описания убраны вкусы старого вида — {', '.join(убрать)}")
    return True


def _glitch_туров(cur):
    """GLITCH в Турове: старые вкусы (по 0 шт) → «200 мг · …» с 0 шт. Владелец:
    «будем возить». Только если оба старых по нулям, новых ещё нет и по
    товару нет невыданных заказов."""
    m = _модель(cur, "snyus", "GLITCH", "GLITCH")
    if not m:
        return False
    cur.execute(db._q("SELECT * FROM products WHERE model_id = %s AND city = %s"), (m["id"], "Туров"))
    p = cur.fetchone()
    if not p:
        return False
    cur.execute(db._q("SELECT flavor, stock FROM product_variants WHERE product_id = %s"), (p["id"],))
    есть = {r["flavor"]: int(r["stock"] or 0) for r in cur.fetchall()}
    замены = {"Холодная груша": "200 мг · Холодная груша", "Двойная мята-вишня": "200 мг · Двойная мята-вишня"}
    if set(есть) != set(замены) or any(есть.values()) or db.open_orders_with_product(p["id"]):
        return False
    for старый, новый in замены.items():
        cur.execute(db._q("UPDATE product_variants SET flavor = %s WHERE product_id = %s AND flavor = %s AND stock = 0"),
                    (новый, p["id"], старый))
    _журнал("GLITCH · Туров: вкусы «Холодная груша», «Двойная мята-вишня» (0 шт) → «200 мг · …», "
            "товар остаётся снятым с витрины до поставки")
    return True


def _бренд(cur, name):
    cur.execute(db._q("SELECT * FROM brands WHERE name = %s"), (name,))
    r = cur.fetchone()
    return dict(r) if r else None


def _вкусы_бренда(cur, name, было, стало, пояснение):
    b = _бренд(cur, name)
    if not b:
        return False
    try:
        сейчас = json.loads(b["flavors"] or "[]")
    except (TypeError, ValueError):
        return False
    if сейчас != было:
        return False
    cur.execute(db._q("UPDATE brands SET flavors = %s WHERE id = %s"), (json.dumps(стало, ensure_ascii=False), b["id"]))
    _журнал(f"бренд «{name}»: {пояснение}")
    return True


def apply_catalog_fix():
    """Один раз за жизнь базы. Возвращает число сделанных шагов (или None, если уже было)."""
    if db.get_setting(ОТМЕТКА):
        return None
    _сделано.clear()
    conn = db.connect()
    cur = conn.cursor()
    шагов = 0
    try:
        # 1. Картридж: бренд переименован старым кодом — в описании остался
        #    «VAPORESSO XROS», и «Сохранить описание» вернуло бы его точкам.
        m = _модель(cur, "coils", "Xros", "VAPORESSO XROS")
        if m:
            cur.execute(db._q("UPDATE models SET brand = %s, name = %s WHERE id = %s"), ("VAPORESSO", "XROS", m["id"]))
            cur.execute(db._q("UPDATE products SET brand = %s, name = %s WHERE model_id = %s"), ("VAPORESSO", "XROS", m["id"]))
            _журнал("картридж «Xros»: бренд «VAPORESSO XROS» → «VAPORESSO», название → «XROS» (описание и точки)")
            шагов += 1
        # 2. PILOW TALK IC40000 — бренд был пустым.
        шагов += _бренд_модели(cur, "disposable", "PILOW TALK IC40000", "", "PILOW TALK")
        # 3. ЗЛАЯ МИЛФА ACID — линейка бренда ЗЛАЯ МИЛФА, не отдельный бренд.
        шагов += _бренд_модели(cur, "liquid", "ЗЛАЯ МИЛФА ACID", "ЗЛАЯ МИЛФА ACID", "ЗЛАЯ МИЛФА")
        acid = _бренд(cur, "ЗЛАЯ МИЛФА ACID")
        if acid and _бренд(cur, "ЗЛАЯ МИЛФА"):
            cur.execute(db._q("SELECT COUNT(*) AS n FROM products WHERE brand = %s"), ("ЗЛАЯ МИЛФА ACID",))
            n = int(cur.fetchone()["n"])
            cur.execute(db._q("SELECT COUNT(*) AS n FROM models WHERE brand = %s"), ("ЗЛАЯ МИЛФА ACID",))
            n += int(cur.fetchone()["n"])
            if not n:
                cur.execute(db._q("DELETE FROM brands WHERE id = %s"), (acid["id"],))
                _журнал("бренд «ЗЛАЯ МИЛФА ACID» удалён: его товар теперь линейка бренда «ЗЛАЯ МИЛФА»")
                шагов += 1
        # 4. GLITCH в Турове — до чистки описания GLITCH (там эти вкусы ещё есть).
        шагов += _glitch_туров(cur)
        # 5. Описания снюса: вкусы старого вида рядом с «200 мг · …».
        шагов += _убрать_вкусы_модели(cur, "snyus", "DRYMOST", "DRYMOST",
                                      {"Ледяная груша-200", "Мята-200", "Сияющая вишня-175"})
        шагов += _убрать_вкусы_модели(cur, "snyus", "GLITCH", "GLITCH",
                                      {"Холодная груша", "Двойная мята-вишня", "200 мг · Двойная мята"})
        # 6. Бренды.
        v = _бренд(cur, "VAPORESSO")
        if v and v["category"] == "coils":
            cur.execute(db._q("UPDATE brands SET category = '' WHERE id = %s"), (v["id"],))
            _журнал("бренд «VAPORESSO»: «Расходники» → «Во всех категориях» (у него и картриджи, и поды)")
            шагов += 1
        шагов += _вкусы_бренда(cur, "ANNIMA LOVE",
                               ["Кислая вишня", "Клубника киви", "Морозные лесные ягоды", "Смородина вишня черника",
                                "сочный персик", "Черника ежевика", "Энергетик ягоды"],
                               ["Кислая вишня", "Клубника киви", "Морозные лесные ягоды", "Смородина вишня черника",
                                "Сочный персик", "Черника ежевика", "Энергетик ягоды"],
                               "«сочный персик» → «Сочный персик», как на точках")
        шагов += _вкусы_бренда(cur, "GLITCH", ["Двойная мята вишня", "Холодная груша"],
                               ["Двойная мята-вишня", "Холодная груша"], "«Двойная мята вишня» → «Двойная мята-вишня», как в товаре")
        рабыня = _модель(cur, "liquid", "ПОШЛАЯ РАБЫНЯ", "ПОШЛАЯ РАБЫНЯ")
        if рабыня and _вкусы(рабыня):
            шагов += _вкусы_бренда(cur, "ПОШЛАЯ РАБЫНЯ", [], _вкусы(рабыня),
                                   "список вкусов был пуст — вписаны 7 вкусов из описания товара")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        _сделано.clear()
        raise
    conn.close()
    db.set_setting(ОТМЕТКА, db._now_str())
    if шагов:
        _журнал(f"готово, шагов: {шагов}")
    _в_журнал()
    return шагов
