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
