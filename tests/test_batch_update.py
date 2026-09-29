"""Правка товара идёт ОДНИМ запросом, а не по запросу на поле.

Сохранение карточки меняло до десяти полей и слало на каждое отдельный запрос.
По мобильной сети это десять полных обменов подряд — на живом магазине замерено
0.7–2.6 с на запрос, то есть «Сохраняю…» висело около десяти секунд.

Проверяем не скорость (её в тесте не измерить честно), а то, ради чего пачка
заводилась: все поля применяются за один вызов, отказ по одному полю не теряет
остальные, а отказ по правам остаётся отказом по правам.
"""
from _common import db, client, Checker, as_admin, as_user, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
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
        "price": "25.5", "cost": "12", "stock": "7", "name": "Пачка-под 2", "is_hit": 1}})
    d = r.get_json()
    c("пачка принята", r.status_code == 200 and d.get("ok"))
    c("сервер назвал, что сохранил", set(d.get("saved") or []) == {"price", "cost", "stock", "name", "is_hit"})
    p = db.get_product(pid)
    c("цена легла", abs(float(p["price"]) - 25.5) < 0.01)
    c("закупка легла", abs(float(p["cost"]) - 12) < 0.01)
    c("остаток лёг", int(p["stock"]) == 7)
    c("название легло", p["name"] == "Пачка-под 2")
    c("хит лёг", int(p["is_hit"]) == 1)

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
    """U-01: форма редактора держит остаток с момента открытия. Раньше ЛЮБОЕ
    сохранение карточки (даже одной цены) слало это устаревшее число как
    новое — а если за это время товар купили, продажа тихо исчезала со
    склада. Теперь клиент шлёт expected_stock (то, что видел при открытии),
    и сервер меняет остаток, только если он всё ещё таков."""
    c = Checker("Остаток: правка карточки не портит чужую продажу")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "СтокПод", 40.0, 5, cost=20.0)

    # Карточка открыта с остатком 5. Пока владелец её держит открытой,
    # покупатель разбирает одну штуку — остаток становится 4.
    db.change_stock(pid, -1)
    c("остаток и правда стал 4 (покупка)", db.get_product(pid)["stock"] == 4)

    # Владелец меняет ТОЛЬКО цену и сохраняет форму, где остаток всё ещё
    # показывает старые 5 (форму не перезагружали).
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "price": "45"}})
    d = r.get_json()
    c("цена сохранилась", d.get("ok") and "price" in (d.get("saved") or []))
    c("остаток НЕ вернулся к 5 — клиент его вообще не прислал", db.get_product(pid)["stock"] == 4)

    # Владелец ДЕЙСТВИТЕЛЬНО меняет остаток (внёс новое число), но по-прежнему
    # ссылаясь на устаревший снимок 5 (5 → 6, будто уже 5 на складе) — сервер
    # обязан отказать: на складе 4, а не 5.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "stock": "6"}, "expected_stock": 5})
    d = r.get_json()
    c("конфликт назван явно", d.get("ok") and (d.get("failed") or {}).get("stock", {}).get("error") == "stock_conflict")
    c("остаток не тронут при конфликте", db.get_product(pid)["stock"] == 4)

    # Обновил форму (снимок теперь верный 4) — правка проходит.
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "stock": "10"}, "expected_stock": 4})
    d = r.get_json()
    c("с верным снимком правка проходит", d.get("ok") and "stock" in (d.get("saved") or []))
    c("остаток стал 10", db.get_product(pid)["stock"] == 10)

    # Без expected_stock вовсе — старое поведение (обратная совместимость).
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid, "fields": {
        "stock": "3"}})
    c("без expected_stock — как раньше, безусловно", r.get_json().get("ok") and db.get_product(pid)["stock"] == 3)

    _clean()
    return c.fails


def run_варианты_не_переписываются_чужой_продажей():
    """То же самое для товара со вкусами: /api/admin/product/variants меняет
    список целиком, и раньше добавление нового вкуса стирало остаток по уже
    проданному в это же время. С expected — конфликт явный и атомарный:
    либо применилось всё, либо ничего."""
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

    # Владелец добавляет третий вкус, но список шлёт со старым снимком Мяты (5).
    r = client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Мята", "stock": 5}, {"flavor": "Вишня", "stock": 3}, {"flavor": "Лимон", "stock": 2}],
        "expected": снимок})
    d = r.get_json()
    c("конфликт: сервер отказал, а не переписал", r.status_code == 409 and d.get("error") == "stock_conflict")
    остатки = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c("Мята осталась 4 (не откатилась на 5)", остатки.get("Мята") == 4)
    c("Лимон НЕ появился — правка не применилась частично", "Лимон" not in остатки)

    # С верным снимком (Мята уже 4) — та же правка проходит целиком.
    r = client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Мята", "stock": 4}, {"flavor": "Вишня", "stock": 3}, {"flavor": "Лимон", "stock": 2}],
        "expected": [{"flavor": "Мята", "stock": 4}, {"flavor": "Вишня", "stock": 3}]})
    d = r.get_json()
    c("с верным снимком проходит", d.get("ok"))
    остатки = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c("Лимон появился", остатки.get("Лимон") == 2)
    c("общий остаток пересчитан (4+3+2=9)", db.get_product(pid)["stock"] == 9)

    _clean()
    return c.fails


def run_два_сотрудника_правят_один_товар_одновременно():
    """Два продавца открывают одну карточку почти одновременно и оба меняют
    остаток по-своему. Оба видели один и тот же снимок при открытии — значит
    сохраниться должен только первый, второй обязан получить внятный отказ, а
    не тихо переписать то, что уже сохранил первый."""
    import threading
    c = Checker("Гонка: два сотрудника правят один товар")
    _clean()
    as_admin()
    pid = db.add_product("Минск", "pods", "ГонкаПод", 20.0, 5, cost=10.0)

    out = {}

    def сохранить(новый, ключ):
        r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                        "fields": {"stock": новый}, "expected_stock": 5})
        out[ключ] = r.get_json()

    ta = threading.Thread(target=сохранить, args=(8, "a"))
    tb = threading.Thread(target=сохранить, args=(12, "b"))
    ta.start(); tb.start()
    ta.join(); tb.join()

    успехов = sum(1 for d in out.values() if "stock" in (d.get("saved") or []))
    отказов = sum(1 for d in out.values() if (d.get("failed") or {}).get("stock", {}).get("error") == "stock_conflict")
    c("ровно один сохранился", успехов == 1)
    c("второй получил внятный отказ конфликтом, а не тихо проиграл", отказов == 1)
    c("итоговый остаток — от того, кто сохранился первым (8 или 12), не смесь", db.get_product(pid)["stock"] in (8, 12))

    as_admin()
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() + run_остаток_не_переписывается_чужой_продажей()
                    + run_варианты_не_переписываются_чужой_продажей()
                    + run_два_сотрудника_правят_один_товар_одновременно()) else 0)
