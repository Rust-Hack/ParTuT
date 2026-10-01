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
    for t in ("stock_moves", "product_variants", "products", "models"):
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

    # Регистр не разводит один вкус на два: «black cherry» — это уже заведённый
    # «Black Cherry», и второй записью он не станет.
    ответ = client.post("/api/admin/product/variants/change", json={
        "initData": "x", "id": pid, "add": [{"flavor": "black cherry", "qty": 5}]})
    c("тот же вкус в другом регистре — отказ «уже есть»",
      ответ.status_code == 409 and ответ.get_json().get("error") == "exists")
    c("на точке всё так же три вкуса", len(db.get_variants(pid)) == 3)
    c(f"дублей в модели нет: {db.get_model(mid)['flavors']}", len(db.get_model(mid)["flavors"]) == 3)

    # Убрали вкусы на точке — из модели они НЕ исчезают. Остаток при этом
    # уходит только с подтверждением и остаётся в истории склада.
    ответ = client.post("/api/admin/product/variants/change", json={
        "initData": "x", "id": pid, "remove": ["Black Cherry", "Sour apple ice"]})
    c("без подтверждения вкус с остатком не убирается",
      ответ.status_code == 409 and ответ.get_json().get("error") == "has_stock")
    ответ = client.post("/api/admin/product/variants/change", json={
        "initData": "x", "id": pid, "remove": ["Black Cherry", "Sour apple ice"], "writeoff": True})
    c("с подтверждением — убраны", ответ.status_code == 200 and ответ.get_json().get("ok"))
    c("на точке остался один вкус", len(db.get_variants(pid)) == 1)
    c("а в модели по-прежнему три", len(db.get_model(mid)["flavors"]) == 3)
    списано = {m["flavor"]: m["delta"] for m in db.get_stock_moves(pid, limit=20) if m["delta"] < 0}
    c(f"списанное — в истории склада: {списано}", списано == {"Black Cherry": -1, "Sour apple ice": -3})

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
    # То, что прежняя кнопка теряла: доп. фото и отзыв старого товара.
    db.add_product_photo(pid, "legacy-gal-1", "legacy-gal-1s")
    db.add_product_photo(pid, "legacy-gal-2", "legacy-gal-2s")
    db.ensure_user(9301)
    отзыв = db.add_review(pid, 9301, 5, "Отличный под")
    db.set_review_status(отзыв, "approved")
    c("до перевода отзыв виден у товара", [r["id"] for r in db.list_reviews(pid)] == [отзыв])

    # Заведение модели — это заведение записи в ОБЩЕМ ассортименте (то же,
    # что /api/admin/model), а не правка своей точки: продавцу должно быть
    # закрыто тем же правилом, что и прямому созданию модели.
    as_admin(uid=9200, username="продавец", role="staff", city="Минск")
    закрыто = client.post("/api/admin/product/to-model", json={"initData": "x", "id": pid})
    c("продавцу закрыто — это ассортимент, а не его точка", закрыто.status_code == 403)
    c("модель не завелась", db.get_product(pid)["model_id"] is None)
    as_admin()

    # Журнал берёт админа из g.admin — его ставит настоящий get_admin; подмена
    # делает то же самое, иначе журнал молчал бы и проверка шла бы вхолостую.
    from flask import g
    from partut.web import auth

    def владелец(init):
        g.admin = {"id": 100, "username": "owner", "role": "owner", "city": "", "name": "Владелец"}
        return g.admin
    старый, auth.get_admin = auth.get_admin, владелец
    try:
        ответ = client.post("/api/admin/product/to-model", json={"initData": "x", "id": pid})
    finally:
        auth.get_admin = старый
    c("ручка ответила", ответ.status_code == 200 and ответ.get_json()["ok"])
    mid = ответ.get_json()["model_id"]

    c("товар привязан к модели", db.get_product(pid)["model_id"] == mid)
    м = db.get_model(mid)
    c("название перенесено", м["name"] == "Старый под")
    c("бренд перенесён", м["brand"] == "OldBrand")
    c(f"вкусы перенесены: {м['flavors']}", set(м["flavors"]) == {"Мята", "Ваниль"})
    c("характеристики перенесены",
      str(м["specs"].get("strength")) == "20" and str(м["specs"].get("volume")) == "30")
    c("доп. фото товара — в галерее модели, по порядку",
      [g["file_id"] for g in db.model_photos(mid)] == ["legacy-gal-1", "legacy-gal-2"])
    витрина = next((x for x in client.get("/api/products").get_json() if x["id"] == pid), None)
    c("и витрина их показывает", витрина is not None and len(витрина.get("photos") or []) == 2)
    c("отзыв виден сразу, без перезапуска сервера", [r["id"] for r in db.list_reviews(pid)] == [отзыв])
    c("в ответе — что переехало", ответ.get_json().get("photos_moved") == 2 and ответ.get_json().get("reviews") == 1)
    журнал = [r for r in db.list_admin_log(limit=20) if "to-model" in (r["action"] or "")]
    c(f"в журнале одна запись, не две: {[r['details'] for r in журнал]}",
      len(журнал) == 1 and "новая модель" in (журнал[0]["details"] or ""))

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

    # --- Двойник: тот же под, заведённый когда-то отдельно в другом городе ---
    твин = db.add_product("Горки", "pods", "старый под", 26.0, 0, cost=15.0, brand="oldbrand")
    db.add_variant(твин, "Мята", 1)
    db.add_variant(твин, "Кола", 4)
    db.recalc_product_stock(твин)
    моделей = len(db.list_models())
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": твин})
    d = r.get_json() or {}
    c("такая модель уже есть — вторую не заводим, а называем её",
      r.status_code == 409 and d.get("error") == "exists" and d.get("model_id") == mid and len(db.list_models()) == моделей)
    c("и товар пока без модели", db.get_product(твин)["model_id"] is None)
    чужая = db.add_model("pods", "Совсем другой", "OldBrand")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": твин, "link_to": чужая})
    c("привязать к непохожей модели нельзя", r.status_code == 400 and (r.get_json() or {}).get("error") == "bad_link")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": твин, "link_to": mid})
    d = r.get_json() or {}
    c("с подтверждением — привязан к той же модели", d.get("ok") and d.get("linked") and db.get_product(твин)["model_id"] == mid)
    т = db.get_product(твин)
    c("описание стало как у модели, цена и остаток свои",
      т["name"] == "Старый под" and т["brand"] == "OldBrand" and float(т["price"]) == 26.0 and int(т["stock"]) == 5)
    c(f"вкус, которого модель не знала, добавлен в модель: {db.get_model(mid)['flavors']}",
      d.get("added_flavors") == ["Кола"] and "Кола" in db.get_model(mid)["flavors"])
    c("варианты на точке целы", {v["flavor"]: v["stock"] for v in db.get_variants(твин)} == {"Мята": 1, "Кола": 4})

    # --- Сбой посередине — ничего не меняется ---
    сирота = db.add_product("Минск", "pods", "Сбойный под", 10.0, 0, cost=5.0)
    моделей = len(db.list_models())
    настоящее = db.MAX_EXTRA_PHOTOS
    db.MAX_EXTRA_PHOTOS = None          # упадёт уже после того, как модель заведена
    try:
        client.post("/api/admin/product/to-model", json={"initData": "x", "id": сирота})
    except Exception:
        pass
    finally:
        db.MAX_EXTRA_PHOTOS = настоящее
    c("сбой посередине: модель не завелась, товар без модели — всё или ничего",
      len(db.list_models()) == моделей and db.get_product(сирота)["model_id"] is None)

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

    # Пустое имя и отрицательный остаток отбрасываются (старый вход «весь
    # список» — его ещё шлют страницы, открытые до обновления).
    client.post("/api/admin/product/variants", json={
        "initData": "x", "id": pid,
        "variants": [{"flavor": "Grape B - POP", "stock": 6},
                     {"flavor": "  ", "stock": 5}, {"flavor": "Cherry", "stock": -4}]})
    вар2 = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c(f"пустой вкус отброшен: {list(вар2)}", set(вар2) == {"Grape B - POP", "Cherry"})
    c("отрицательный остаток стал нулём", вар2.get("Cherry") == 0)

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


def run_дубли_при_заведении_нового():
    """То же самое при заведении нового товара («✨ Новый товар»).

    Раньше это проверялось на прямом пути /api/admin/product — у него
    нормализации поначалу не было вовсе, и дубль написаний уходил в базу двумя
    строками. Тот путь закрыт; теперь товар заводит публикация, и написание
    из справочника бренда обязана держать она: и в модели, и на точке."""
    c = Checker("Дубли вкусов при заведении нового товара")
    _чисто(); as_admin()
    conn = db.connect(); conn.cursor().execute("DELETE FROM brands"); conn.commit(); conn.close()

    db.add_brand("PULSE", "disposable", ["Grape B - POP"])
    r = client.post("/api/admin/product/publish", json={
        "initData": "x", "client_token": "pulse-flavors-0001",
        "model": {"category": "disposable", "name": "PULSE 15000", "brand": "PULSE",
                  "flavors": ["grape b - pop", "  GRAPE B - POP  ", "Мята"]},
        "points": [{"city": "Минск", "price": "30", "cost": "18",
                    "variants": [{"flavor": "GRAPE B - POP", "stock": 2}, {"flavor": "Мята", "stock": 1}]}]})
    d = r.get_json() or {}
    c("товар создан", d.get("ok") is True)
    модель = db.get_model(d.get("model_id")) if d.get("ok") else None
    c(f"у модели вкус один, не три, и в написании справочника: {модель and модель['flavors']}",
      модель is not None and модель["flavors"] == ["Grape B - POP", "Мята"])
    вар = {v["flavor"]: v["stock"] for v in db.get_variants(d["products"][0]["id"])} if d.get("ok") else {}
    c(f"на точке — то же написание и свои остатки: {вар}", вар == {"Grape B - POP": 2, "Мята": 1})

    # Один вкус двумя строками на точке — не «сложить молча», а отказ: экран
    # повторов не шлёт, значит, прислал что-то не то.
    r = client.post("/api/admin/product/publish", json={
        "initData": "x", "client_token": "pulse-flavors-0002",
        "model": {"category": "disposable", "name": "PULSE 9000", "brand": "PULSE", "flavors": ["Grape B - POP"]},
        "points": [{"city": "Минск", "price": "30", "cost": "18",
                    "variants": [{"flavor": "grape b - pop", "stock": 2}, {"flavor": "Grape B - POP", "stock": 3}]}]})
    c("один вкус двумя строками на точке — отказ, а не тихая сумма",
      r.status_code == 400 and (r.get_json() or {}).get("error") == "bad_variant")

    conn = db.connect(); conn.cursor().execute("DELETE FROM brands"); conn.commit(); conn.close()
    _чисто()
    return c.fails
