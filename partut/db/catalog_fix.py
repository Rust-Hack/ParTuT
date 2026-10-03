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
    print(f"[база] наводка 3.10: {сделано}", flush=True)
    _сделано.append(сделано)


def _в_журнал(кто=КТО):
    for x in _сделано:
        try:
            db.log_admin_action(0, кто, "catalog/fix", x)
        except Exception as e:                   # журнал — не повод не чинить
            print(f"[база] {кто}: не записал в журнал: {e}", flush=True)
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


# ---- Вторая наводка 3.10.2026 — витрина глазами покупателя ----
#
# После первой владелец открыл витрину и увидел то же, что и раньше: правка
# меняла бренды и описания, а покупатель видит название товара, подписи
# категорий и вкусы. Здесь — то, что видно на витрине.

ОТМЕТКА_ВИТРИНЫ = "catalog_fix_20261003b"
КТО_ВИТРИНА = "наводка витрины 3.10"

# Вкусы PILOW TALK, записанные с маленькой буквы (на всех точках, в описании
# и в бренде одинаково). «черная вишня» с маленькой — только в бренде.
PILOW_ВКУСЫ = {"клубника манго": "Клубника манго",
               "клубника банан": "Клубника банан",
               "экзотические фрукты клубника": "Экзотические фрукты клубника"}

# Подписи категорий: (таблица, условие, было, стало). Только если сейчас
# в точности «было» — поправленное владельцем руками не трогаем.
ПОДПИСИ = [
    ("категория «снюс» → «Снюс»", "UPDATE categories SET name = %s WHERE code = %s AND name = %s",
     ("Снюс", "snyus", "снюс")),
    ("у снюса выбор «крепость» → «Крепость»",
     "UPDATE categories SET variant_label2 = %s WHERE code = %s AND variant_label2 = %s",
     ("Крепость", "snyus", "крепость")),
    ("характеристика снюса «крепость» → «Крепость»",
     "UPDATE category_specs SET label = %s WHERE category = %s AND key = %s AND label = %s",
     ("Крепость", "snyus", "krepost", "крепость")),
    ("характеристика одноразок «крепость» → «Крепость»",
     "UPDATE category_specs SET label = %s WHERE category = %s AND key = %s AND label = %s",
     ("Крепость", "disposable", "krepost", "крепость")),
    ("характеристика одноразок «батарея» → «Батарея»",
     "UPDATE category_specs SET label = %s WHERE category = %s AND key = %s AND label = %s",
     ("Батарея", "disposable", "batareya", "батарея")),
    ("единица батареи одноразок «mah» → «мАч»",
     "UPDATE category_specs SET unit = %s WHERE category = %s AND key = %s AND unit = %s",
     ("мАч", "disposable", "batareya", "mah")),
]


def _имя_картриджа(cur):
    """Картридж: название «XROS» → «VAPORESSO XROS». Название товара в магазине
    — полное, с брендом («PILOW TALK IC40000»): так его и пишет «Новый товар»,
    и только оно стоит крупно на карточке. Голое «XROS» вышло из первой правки."""
    m = _модель(cur, "coils", "XROS", "VAPORESSO")
    if not m:
        return False
    cur.execute(db._q("UPDATE models SET name = %s WHERE id = %s"), ("VAPORESSO XROS", m["id"]))
    cur.execute(db._q("UPDATE products SET name = %s WHERE model_id = %s AND name = %s"), ("VAPORESSO XROS", m["id"], "XROS"))
    _журнал("картридж: название «XROS» → «VAPORESSO XROS» (описание и точки) — на витрине видно бренд")
    return True


def _вкусы_pilow(cur):
    """Вкусы PILOW TALK IC40000 с большой буквы — везде, где лежит название
    вкуса: варианты на точках, описание, бренд, состав заказов, история склада.

    Заказы — обязательно: отмена и выдача находят вариант по ТОЧНОМУ названию,
    и заказ со старым «клубника манго» после переименования не вернул бы штуки
    на полку. Если у какой-то точки уже есть вкус с большой буквы (кто-то завёл
    руками), шаг не делается целиком: сливать два варианта — не наша задача."""
    m = _модель(cur, "disposable", "PILOW TALK IC40000", "PILOW TALK")
    if not m:
        return False
    cur.execute(db._q("SELECT id FROM products WHERE model_id = %s"), (m["id"],))
    pids = [int(r["id"]) for r in cur.fetchall()]
    if not pids:
        return False
    # Замки — как у заказа и прихода: сначала варианты, потом товар. Правка
    # идёт при старте, а прежняя копия сайта во время выкатки ещё принимает
    # заказы. Заказ, уже взявший вариант, мы дождёмся — и ниже увидим его в
    # списке заказов; новый будет ждать нас и потом не найдёт старый вкус —
    # честный отказ «нет в наличии», а не заказ со старым названием.
    if db.USE_PG:
        for pid in sorted(pids):
            cur.execute("SELECT id FROM product_variants WHERE product_id = %s ORDER BY id FOR UPDATE", (pid,))
            cur.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (pid,))
    места = ", ".join(["%s"] * len(pids))
    cur.execute(db._q(f"SELECT product_id, flavor FROM product_variants WHERE product_id IN ({места})"), pids)
    есть = {(int(r["product_id"]), r["flavor"]) for r in cur.fetchall()}
    if not any(f in PILOW_ВКУСЫ for _, f in есть):
        return False                      # старых названий нет — уже сделано или нечего делать
    if any((pid, старый) in есть and (pid, новый) in есть for pid in pids for старый, новый in PILOW_ВКУСЫ.items()):
        _журнал("PILOW TALK IC40000: вкусы НЕ переименованы — на точке есть и старое, и новое название одного вкуса")
        return False
    for старый, новый in PILOW_ВКУСЫ.items():
        cur.execute(db._q(f"UPDATE product_variants SET flavor = %s WHERE flavor = %s AND product_id IN ({места})"),
                    (новый, старый, *pids))
        cur.execute(db._q(f"UPDATE stock_moves SET flavor = %s WHERE flavor = %s AND product_id IN ({места})"),
                    (новый, старый, *pids))
        cur.execute(db._q(f"UPDATE products SET flavor = %s WHERE flavor = %s AND id IN ({места})"),
                    (новый, старый, *pids))
    вкусы = [PILOW_ВКУСЫ.get(f, f) for f in _вкусы(m)]
    cur.execute(db._q("UPDATE models SET flavors = %s WHERE id = %s"), (json.dumps(вкусы, ensure_ascii=False), m["id"]))
    # Состав заказов: строка JSON. LIKE отсекает заведомо не те заказы, а
    # решает разбор — по номеру товара и точному названию вкуса. Под замком:
    # иначе правка состава продавцом в ту же секунду (прежняя копия сайта)
    # затёрлась бы составом, прочитанным до неё.
    cur.execute(db._q("SELECT id, items FROM orders WHERE items LIKE %s" + (" FOR UPDATE" if db.USE_PG else "")),
                ("%PILOW TALK%",))
    заказов = 0
    for r in cur.fetchall():
        try:
            items = json.loads(r["items"] or "[]")
        except (TypeError, ValueError):
            continue
        тронут = False
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or int(it.get("id") or 0) not in pids or it.get("flavor") not in PILOW_ВКУСЫ:
                continue
            старый = it["flavor"]
            it["flavor"] = PILOW_ВКУСЫ[старый]
            if isinstance(it.get("name"), str) and it["name"].endswith(старый):
                it["name"] = it["name"][: -len(старый)] + PILOW_ВКУСЫ[старый]
            тронут = True
        if тронут:
            cur.execute(db._q("UPDATE orders SET items = %s WHERE id = %s"), (json.dumps(items, ensure_ascii=False), r["id"]))
            заказов += 1
    _журнал(f"PILOW TALK IC40000: вкусы с большой буквы — {', '.join(PILOW_ВКУСЫ.values())} "
            f"(точек: {len(pids)}, заказов: {заказов})")
    return True


def apply_storefront_fix():
    """Один раз за жизнь базы. Возвращает число шагов (или None, если уже было)."""
    if db.get_setting(ОТМЕТКА_ВИТРИНЫ):
        return None
    _сделано.clear()
    conn = db.connect()
    cur = conn.cursor()
    шагов = 0
    try:
        шагов += _имя_картриджа(cur)
        шагов += _вкусы_pilow(cur)
        b = _бренд(cur, "PILOW TALK")
        if b:
            было = _вкусы(b)
            стало = [PILOW_ВКУСЫ.get(f, "Черная вишня" if f == "черная вишня" else f) for f in было]
            if стало != было:
                cur.execute(db._q("UPDATE brands SET flavors = %s WHERE id = %s"), (json.dumps(стало, ensure_ascii=False), b["id"]))
                _журнал("бренд «PILOW TALK»: вкусы с большой буквы, как на точках")
                шагов += 1
        for что, sql, параметры in ПОДПИСИ:
            cur.execute(db._q(sql), параметры)
            if cur.rowcount:
                _журнал(что)
                шагов += 1
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        _сделано.clear()
        raise
    conn.close()
    db.set_setting(ОТМЕТКА_ВИТРИНЫ, db._now_str())
    if шагов:
        _журнал(f"готово, шагов: {шагов}")
    _в_журнал(КТО_ВИТРИНА)
    return шагов
