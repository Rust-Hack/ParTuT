"""Правка товара идёт ОДНИМ запросом, а не по запросу на поле.

Сохранение карточки меняло до десяти полей и слало на каждое отдельный запрос.
По мобильной сети это десять полных обменов подряд — на живом магазине замерено
0.7–2.6 с на запрос, то есть «Сохраняю…» висело около десяти секунд.

Проверяем не скорость (её в тесте не измерить честно), а то, ради чего пачка
заводилась: все поля применяются за один вызов, отказ по одному полю не теряет
остальные, а отказ по правам остаётся отказом по правам.
"""
from _common import db, client, Checker, as_admin, as_user, deny_admin, версия_цены

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM stock_moves")
    cur.execute("DELETE FROM product_variants")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Правка товара пачкой")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "Пачка-под", 20.0, 5, cost=10.0)

    # --- Всё сразу ---
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "25.5", "cost": "12", "stock": "7", "name": "Пачка-под 2", "is_hit": 1},
        "expected": {"price_rev": версия_цены(pid)}})
    d = r.get_json()
    c("пачка принята", r.status_code == 200 and d.get("ok"))
    c("сервер назвал, что сохранил", set(d.get("saved") or []) == {"price", "cost", "name", "is_hit"})
    p = db.get_product(pid)
    c("цена легла", abs(float(p["price"]) - 25.5) < 0.01)
    c("закупка легла", abs(float(p["cost"]) - 12) < 0.01)
    c("название легло", p["name"] == "Пачка-под 2")
    c("хит лёг", int(p["is_hit"]) == 1)
    # Остаток — не поле карточки: его меняет только склад, с записью в историю.
    c("остаток через карточку не принят", (d.get("failed") or {}).get("stock", {}).get("error") == "use_stock_moves")
    c("и остался прежним", int(p["stock"]) == 5)
    c("в истории склада ничего не появилось", not db.get_stock_moves(pid, limit=10))

    # --- Кривое поле не роняет всю пачку ---
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "-5", "description": "хорошая штука"}})
    d = r.get_json()
    c("пачка с одним кривым полем отвечает ok", d.get("ok"))
    c("хорошее поле сохранено", db.get_product(pid)["description"] == "хорошая штука")
    c("плохое названо отдельно", "price" in (d.get("failed") or {}))
    c("и цена осталась прежней", abs(float(db.get_product(pid)["price"]) - 25.5) < 0.01)

    # --- Одиночное поле работает как раньше: на нём переключатели в списке ---
    r = client.post("/api/admin/product/update",
                    json={"initData": "x", "id": pid, "field": "hidden", "value": 1})
    c("одиночное поле принимается", r.get_json().get("ok"))
    c("и применяется", int(db.get_product(pid)["hidden"]) == 1)

    r = client.post("/api/admin/product/update",
                    json={"initData": "x", "id": pid, "field": "price", "value": "-1"})
    c("одиночное кривое — 400 с текстом", r.status_code == 400 and r.get_json().get("message"))

    # --- Права: пачка не обходит проверку точки ---
    as_user(9500); deny_admin()
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {"price": "1"}})
    c("посторонний не правит пачкой", r.status_code == 403)
    c("цена цела", abs(float(db.get_product(pid)["price"]) - 25.5) < 0.01)

    as_admin()
    _clean()
    return c.fails


def run_остаток_не_переписывается_чужой_продажей():
    """U-01: форма редактора держит числа с момента открытия. Раньше ЛЮБОЕ
    сохранение карточки (даже одной цены) слало устаревший остаток как новый
    — если за это время товар купили, продажа тихо исчезала со склада.

    Теперь остаток карточка не шлёт и сервер его не принимает вовсе: число
    меняет только склад. Защита от устаревшего снимка переехала туда же —
    в пересчёт (expected), а у цены появилась своя (expected по полям)."""
    c = Checker("Остаток: правка карточки не портит чужую продажу")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "СтокПод", 40.0, 5, cost=20.0)

    # Карточка открыта с остатком 5. Пока владелец её держит открытой,
    # покупатель разбирает одну штуку — остаток становится 4.
    db.change_stock(pid, -1)
    c("остаток и правда стал 4 (покупка)", db.get_product(pid)["stock"] == 4)

    # Владелец меняет ТОЛЬКО цену и сохраняет форму.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "45"}, "expected": {"price": 40, "price_rev": версия_цены(pid)}})
    d = r.get_json()
    c("цена сохранилась", d.get("ok") and "price" in (d.get("saved") or []))
    c("остаток НЕ вернулся к 5", db.get_product(pid)["stock"] == 4)

    # Старая страница, открытая до обновления, всё ещё шлёт остаток числом —
    # и со снимком, и без. Оба раза отказ, и число не трогается.
    for тело in ({"fields": {"stock": "6"}, "expected_stock": 5}, {"fields": {"stock": "3"}}):
        r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, **тело})
        d = r.get_json()
        c("остаток через карточку — понятный отказ",
          d.get("ok") and (d.get("failed") or {}).get("stock", {}).get("error") == "use_stock_moves")
        c("остаток не тронут", db.get_product(pid)["stock"] == 4)

    # Пересчёт по устаревшему снимку (человек видел 5, а на складе 4) — отказ.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 6,
                                                   "reason": "fix", "expected": 5})
    c("пересчёт по устаревшему снимку — конфликт",
      r.status_code == 409 and r.get_json().get("error") == "stock_conflict")
    c("остаток не тронут при конфликте", db.get_product(pid)["stock"] == 4)

    # Со свежим снимком — проходит, и ложится в историю с разницей.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 10,
                                                   "reason": "fix", "expected": 4})
    c("со свежим снимком пересчёт проходит", r.get_json().get("ok") and db.get_product(pid)["stock"] == 10)
    c("в истории разница +6", db.get_stock_moves(pid, limit=1)[0]["delta"] == 6)

    _clean()
    return c.fails


def run_цена_не_затирает_чужую_правку():
    """Два человека правят цену одного товара: владелец с телефона, продавец у
    прилавка. Раньше побеждал тот, кто сохранил последним, а правка первого
    исчезала молча. Теперь карточка присылает, какой цену видела, и если её
    уже поменяли — отказ с тем, что там теперь."""
    c = Checker("Цена: чужая правка не затирается")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "ЦенаПод", 40.0, 5, cost=20.0)

    # Первый открыл карточку (видит 40) и сохранил 42.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                    "fields": {"price": "42"}, "expected": {"price": "40", "price_rev": 0}})
    c("первый сохранил", "price" in (r.get_json().get("saved") or []))

    # Второй открыл карточку раньше (тоже видел 40) и сохраняет 45 и описание.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                    "fields": {"price": "45", "description": "новое"}, "expected": {"price": "40.00", "price_rev": 0}})
    d = r.get_json()
    отказ = (d.get("failed") or {}).get("price", {})
    c("цену второго не приняли — конфликт назван", отказ.get("error") == "conflict")
    c("в отказе видно, что сейчас", "42.00" in (отказ.get("message") or ""))
    c("цена осталась от первого", abs(float(db.get_product(pid)["price"]) - 42) < 0.01)
    c("описание второго всё равно сохранилось", db.get_product(pid)["description"] == "новое")

    # «40», «40.0» и «40.00» — одно и то же: без этого сверка ругалась бы на
    # каждое сохранение.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                    "fields": {"price": "43"}, "expected": {"price": 42.0, "price_rev": версия_цены(pid)}})
    c("то же число в другой записи — не конфликт", "price" in (r.get_json().get("saved") or []))

    _clean()
    return c.fails


def run_варианты_не_переписываются_чужой_продажей():
    """То же для товара со вкусами. Раньше /api/admin/product/variants менял
    список целиком, и добавление нового вкуса стирало остаток по уже
    проданному в это же время.

    Теперь эта ручка (её ещё шлют страницы, открытые до обновления) меняет
    только СОСТАВ: новый вкус добавляется, проданное не возвращается, а
    попытка поменять число у заведённого вкуса — отказ с объяснением."""
    c = Checker("Варианты: сохранение не портит чужую продажу")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "liquid", "СтокЖидкость", 30.0, 0)
    db.add_variant(pid, "Мята", 5)
    db.add_variant(pid, "Вишня", 3)
    db.recalc_product_stock(pid)
    снимок = [{"flavor": "Мята", "stock": 5}, {"flavor": "Вишня", "stock": 3}]

    # Пока форма открыта (снимок — 5 и 3), покупатель берёт Мяту — 5 → 4.
    db.change_variant_stock(pid, "Мята", -1)
    db.recalc_product_stock(pid)
    c("Мята правда стала 4", {v["flavor"]: v["stock"] for v in db.get_variants(pid)}["Мята"] == 4)

    # Владелец добавляет третий вкус; список шлёт со старым снимком Мяты (5).
    r = client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Мята", "stock": 5}, {"flavor": "Вишня", "stock": 3}, {"flavor": "Лимон", "stock": 2}],
        "expected": снимок})
    d = r.get_json()
    c("добавление вкуса проходит — продажа ему не помеха", r.status_code == 200 and d.get("ok"))
    остатки = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c("Мята осталась 4 (проданное не вернулось на полку)", остатки.get("Мята") == 4)
    c("Лимон появился с первым приходом", остатки.get("Лимон") == 2)
    c("общий остаток пересчитан (4+3+2=9)", db.get_product(pid)["stock"] == 9)
    приход = [m for m in db.get_stock_moves(pid, limit=10) if m["flavor"] == "Лимон"]
    c("первый приход Лимона в истории", len(приход) == 1 and приход[0]["delta"] == 2 and приход[0]["reason"] == "in")

    # А поменять число у заведённого вкуса этой ручкой — нельзя.
    r = client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Мята", "stock": 9}, {"flavor": "Вишня", "stock": 3}, {"flavor": "Лимон", "stock": 2}],
        "expected": [{"flavor": "Мята", "stock": 4}, {"flavor": "Вишня", "stock": 3}, {"flavor": "Лимон", "stock": 2}]})
    d = r.get_json()
    c("правка числа — отказ с объяснением", r.status_code == 400 and d.get("error") == "use_stock_moves")
    c("Мята не тронута", {v["flavor"]: v["stock"] for v in db.get_variants(pid)}["Мята"] == 4)

    _clean()
    return c.fails


def run_два_сотрудника_правят_один_товар_одновременно():
    """Два продавца пересчитывают один товар почти одновременно, оба видели
    один и тот же остаток. Сохраниться должен только первый, второй обязан
    получить внятный отказ, а не тихо переписать то, что уже записал первый."""
    import threading
    c = Checker("Гонка: два сотрудника пересчитывают один товар")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "ГонкаПод", 20.0, 5, cost=10.0)

    out = {}

    def пересчитать(новый, ключ):
        r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": новый,
                                                       "reason": "fix", "expected": 5})
        out[ключ] = r.get_json()

    ta = threading.Thread(target=пересчитать, args=(8, "a"))
    tb = threading.Thread(target=пересчитать, args=(12, "b"))
    ta.start(); tb.start()
    ta.join(); tb.join()

    успехов = sum(1 for d in out.values() if d.get("ok"))
    отказов = sum(1 for d in out.values() if d.get("error") == "stock_conflict")
    c("ровно один сохранился", успехов == 1)
    c("второй получил внятный отказ конфликтом, а не тихо проиграл", отказов == 1)
    c("итоговый остаток — от того, кто сохранился первым (8 или 12), не смесь", db.get_product(pid)["stock"] in (8, 12))
    c("в истории ровно одно движение", len(db.get_stock_moves(pid, limit=10)) == 1)

    as_admin()
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_остаток_не_переписывается_чужой_продажей()
                    + run_варианты_не_переписываются_чужой_продажей()
                    + run_два_сотрудника_правят_один_товар_одновременно()) else 0)
