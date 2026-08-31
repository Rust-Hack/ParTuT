"""Вкус, заведённый на точке, обязан попасть в модель.

Вкусы жили в двух списках: у модели свой, у товара на точке свои варианты.
Ручка правки вкусов меняла только точку — и списки расходились МОЛЧА. В Горках
вкус есть, а завезти его в Минск нельзя: модель о нём не знает, и в выборе он
не появляется. Владелец при этом видит вкус в карточке и не понимает, почему
на другой точке его нет.

Только добавляем. Кончился вкус в одном городе — не повод считать, что его
больше не бывает: остальные точки его ещё продают.
"""
from _common import db, client, Checker, as_admin


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("product_variants", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def run():
    c = Checker("Вкус с точки попадает в модель")
    _чисто(); as_admin()

    mid = db.add_model("disposable", "PULSE", "PULSE RETURNS 15000", "", {}, ["Grape B - POP"])
    pid = db.add_product_from_model(mid, "Минск", 30.0, cost=18.0)
    c("модель заведена с одним вкусом", len(db.get_model(mid)["flavors"]) == 1)

    # Продавец добавляет вкусы в карточке товара — так это и делают на деле.
    ответ = client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Grape B - POP", "stock": 2},
                     {"flavor": "Black Cherry", "stock": 1},
                     {"flavor": "Sour apple ice", "stock": 3}]})
    c("вкусы сохранены", ответ.status_code == 200)

    вкусы_модели = db.get_model(mid)["flavors"]
    c(f"модель узнала новые вкусы: {вкусы_модели}", len(вкусы_модели) == 3)
    c("Black Cherry теперь в модели", "Black Cherry" in вкусы_модели)
    c("сервер сказал, что добавил", set(ответ.get_json().get("added_to_model", []))
      == {"Black Cherry", "Sour apple ice"})

    # Повтор ничего не двоит, и регистр не разводит один вкус на два.
    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "black cherry", "stock": 5},
                     {"flavor": "Grape B - POP", "stock": 1}]})
    c(f"дублей нет: {db.get_model(mid)['flavors']}", len(db.get_model(mid)["flavors"]) == 3)

    # Убрали вкус на точке — из модели он НЕ исчезает.
    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid, "variants": [{"flavor": "Grape B - POP", "stock": 1}]})
    c("на точке остался один вкус", len(db.get_variants(pid)) == 1)
    c("а в модели по-прежнему три", len(db.get_model(mid)["flavors"]) == 3)

    _чисто()
    return c.fails


def run_new_point_offers_flavors():
    """Ради этого всё и делалось: на новой точке вкусы предлагаются."""
    c = Checker("Новая точка знает вкусы")
    _чисто(); as_admin()

    mid = db.add_model("disposable", "PULSE", "PULSE RETURNS 15000", "", {}, [])
    pid = db.add_product_from_model(mid, "Минск", 30.0, cost=18.0)
    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Meta moon", "stock": 4}, {"flavor": "Ecuking fab", "stock": 2}]})

    # Модель заводили БЕЗ вкусов — они появились только на точке.
    c("модель подобрала вкусы с точки", len(db.get_model(mid)["flavors"]) == 2)

    ответ = client.post("/api/admin/product/from-model", json={
        "initData": "x", "model_id": mid, "city": "Туров", "price": "32", "cost": "18",
        "variants": [{"flavor": "Meta moon", "stock": 1}]})
    c("товар заведён на второй точке", ответ.status_code == 200 and ответ.get_json()["ok"])
    новый = ответ.get_json()["id"]
    c("на новой точке только выбранный вкус", len(db.get_variants(новый)) == 1)
    c("а в модели оба — есть из чего выбирать", len(db.get_model(mid)["flavors"]) == 2)

    _чисто()
    return c.fails


def run_product_to_model():
    """Одиночный товар превращается в модель — и после этого едет на точки.

    Товары, заведённые до «Ассортимента», модели не имеют, и продать их в
    другом городе можно было только заведя товар заново, руками, с теми же
    полями. Это и была исходная жалоба владельца.
    """
    c = Checker("Товар без модели превращается в модель")
    _чисто(); as_admin()

    pid = db.add_product("Минск", "pods", "Старый под", 25.0, 0, cost=15.0,
                         description="Описание", brand="OldBrand",
                         strength="20", volume="30")
    db.add_variant(pid, "Мята", 3)
    db.add_variant(pid, "Ваниль", 2)
    db.recalc_product_stock(pid)
    c("у товара нет модели", db.get_product(pid)["model_id"] is None)

    ответ = client.post("/api/admin/product/to-model", json={"initData": "x", "id": pid})
    c("ручка ответила", ответ.status_code == 200 and ответ.get_json()["ok"])
    mid = ответ.get_json()["model_id"]

    c("товар привязан к модели", db.get_product(pid)["model_id"] == mid)
    м = db.get_model(mid)
    c("название перенесено", м["name"] == "Старый под")
    c("бренд перенесён", м["brand"] == "OldBrand")
    c(f"вкусы перенесены: {м['flavors']}", set(м["flavors"]) == {"Мята", "Ваниль"})
    c("характеристики перенесены",
      str(м["specs"].get("strength")) == "20" and str(м["specs"].get("volume")) == "30")

    # Ради чего всё: теперь товар едет на вторую точку.
    ответ2 = client.post("/api/admin/product/from-model", json={
        "initData": "x", "model_id": mid, "city": "Туров", "price": "27", "cost": "15",
        "variants": [{"flavor": "Мята", "stock": 4}]})
    c("на второй точке заведён", ответ2.status_code == 200 and ответ2.get_json()["ok"])
    c("на двух точках", len([p for p in db.get_all_products() if p["model_id"] == mid]) == 2)

    # Повторное превращение — отказ, а не вторая модель.
    ещё = client.post("/api/admin/product/to-model", json={"initData": "x", "id": pid})
    c("второй раз нельзя", ещё.status_code == 400)
    c("и модель осталась одна", db.get_product(pid)["model_id"] == mid)

    _чисто()
    return c.fails


def run_case_duplicates():
    """«Grape» и «grape» — один вкус, а не два.

    Модель это понимала и дублей не заводила, а у товара они становились двумя
    записями: покупатель видел в списке один и тот же вкус дважды, а остаток
    делился между ними. Написание берём из модели, чтобы во всех городах вкус
    выглядел одинаково.

    Остатки повторов складываем, а не берём последний: если продавец вписал
    один вкус двумя строками, он привёз сумму, и потерять половину хуже.
    """
    c = Checker("Один вкус в разном регистре")
    _чисто(); as_admin()

    mid = db.add_model("disposable", "PULSE", "PULSE", "", {}, ["Grape B - POP"])
    pid = db.add_product_from_model(mid, "Минск", 30.0, cost=18.0)

    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Grape B - POP", "stock": 2},
                     {"flavor": "grape b - pop", "stock": 3},
                     {"flavor": "  GRAPE B - POP  ", "stock": 1}]})
    вар = db.get_variants(pid)
    c(f"вкус один: {[v['flavor'] for v in вар]}", len(вар) == 1)
    c(f"остатки сложились: {вар[0]['stock']}", вар[0]["stock"] == 6)
    c("написание взято из модели", вар[0]["flavor"] == "Grape B - POP")
    c("и модель не раздвоилась", len(db.get_model(mid)["flavors"]) == 1)

    # Пустое имя и отрицательный остаток отбрасываются.
    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "  ", "stock": 5}, {"flavor": "Cherry", "stock": -4}]})
    вар2 = db.get_variants(pid)
    c(f"пустой вкус отброшен: {[v['flavor'] for v in вар2]}", len(вар2) == 1)
    c("отрицательный остаток стал нулём", вар2[0]["stock"] == 0)

    # То же при заведении на вторую точку.
    ответ = client.post("/api/admin/product/from-model", json={
        "initData": "x", "model_id": mid, "city": "Туров", "price": "32", "cost": "19",
        "variants": [{"flavor": "GRAPE B - POP", "stock": 1},
                     {"flavor": "Grape B - POP", "stock": 2}]})
    новый = ответ.get_json()["id"]
    вар3 = db.get_variants(новый)
    c(f"на новой точке тоже один вкус: {[v['flavor'] for v in вар3]}", len(вар3) == 1)
    c(f"с суммой остатков: {вар3[0]['stock']}", вар3[0]["stock"] == 3)

    _чисто()
    return c.fails


def run_дубли_при_создании_напрямую():
    """То же самое, но при ЗАВОЗЕ через /api/admin/product (без model_id) —
    старый прямой путь создания товара, у которого нормализации не было
    вовсе: дубль писаний уходил в базу двумя строками и оставался там, пока
    кто-то не отредактирует товар вручную через /api/admin/product/variants
    (у него нормализация уже была)."""
    c = Checker("Дубли вкусов при прямом создании товара")
    _чисто(); as_admin()
    conn = db.connect(); conn.cursor().execute("DELETE FROM brands"); conn.commit(); conn.close()

    db.add_brand("PULSE", "disposable", ["Grape B - POP"])
    r = client.post("/api/admin/product", json={
        "initData": "x", "city": "Минск", "category": "disposable", "name": "PULSE 15000",
        "price": "30", "cost": "18", "brand": "PULSE", "puffs": "15000",
        "variants": [{"flavor": "Grape B - POP", "stock": 2},
                     {"flavor": "grape b - pop", "stock": 3},
                     {"flavor": "  GRAPE B - POP  ", "stock": 1}]})
    d = r.get_json() or {}
    c("товар создан", d.get("ok") is True)
    вар = db.get_variants(d.get("id"))
    c(f"вкус один, не три: {[v['flavor'] for v in вар]}", len(вар) == 1)
    c(f"остатки сложились: {вар[0]['stock'] if вар else None}", вар and вар[0]["stock"] == 6)
    c("написание взято из бренда", вар and вар[0]["flavor"] == "Grape B - POP")

    conn = db.connect(); conn.cursor().execute("DELETE FROM brands"); conn.commit(); conn.close()
    _чисто()
    return c.fails
