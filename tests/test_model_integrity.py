"""Модель и её товары не расходятся — приёмка объединения «🛍 Товары» (2.10).

LM-01. Удаление модели в ту же секунду, что и завоз её на точку, оставляло
товар с остатком, ссылающийся на удалённую модель: завоз читал модель до
своей транзакции, а удаление проверяло товары по снимку на своё начало.
Теперь оба держат строку модели (catalog._держать_модель) — проверяем
настоящими параллельными транзакциями в обоих порядках.

LM-02. Фото галереи модели хранили номер товара, с которого пришли, и
удаление этой точки стирало галерею модели на всех остальных.

LM-03. «Сделать моделью» с привязкой к такой же модели заводил одну модель
на одной точке дважды: две цены, два остатка.
"""
import threading

from _common import db, client, Checker, as_admin
from partut.db import catalog


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "product_photos", "reviews", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _битых_связей():
    """Товары, чей model_id указывает на модель, которой нет. Проверка
    «model_id IS NULL» таких не видит."""
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM products p LEFT JOIN models m ON m.id = p.model_id "
                "WHERE p.model_id IS NOT NULL AND m.id IS NULL")
    n = int(cur.fetchone()["n"]); conn.close()
    return n


def _параллельно(первый, второй, держать):
    """Первый берёт строку модели и держит её; пока держит, стартует второй
    и упирается в замок; потом первый отпускает. Возвращает (итог1, итог2)."""
    взял, отпустить = threading.Event(), threading.Event()
    настоящее = catalog._держать_модель
    поток_первого = []

    def держит(cur, model_id):
        есть = настоящее(cur, model_id)
        if threading.current_thread() is поток_первого[0] and держать:
            взял.set()
            отпустить.wait(5)
        return есть

    итоги = {}

    def зап(имя, f):
        try:
            итоги[имя] = f()
        except Exception as e:          # noqa: BLE001 — итог проверит тест
            итоги[имя] = e

    catalog._держать_модель = держит
    try:
        т1 = threading.Thread(target=зап, args=("1", первый))
        поток_первого.append(т1)
        т1.start()
        assert взял.wait(5), "первый не взял модель"
        т2 = threading.Thread(target=зап, args=("2", второй))
        т2.start()
        т2.join(0.5)                     # второй должен ждать замка, а не пройти
        ждал = т2.is_alive()
        отпустить.set()
        т1.join(10); т2.join(10)
    finally:
        catalog._держать_модель = настоящее
        отпустить.set()
    return итоги.get("1"), итоги.get("2"), ждал


def run_удаление_и_завоз():
    c = Checker("LM-01: удаление модели и завоз в ту же секунду")
    _чисто(); as_admin()

    # Завоз первым: удаление дожидается его и видит товар.
    mid = db.add_model("accessories", "QA гонка 1")
    завоз, удаление, ждал = _параллельно(
        lambda: db.create_point_product(mid, "Минск", 20, cost=10, stock=3),
        lambda: db.delete_model(mid), держать=True)
    c(f"удаление ждало, пока завоз допишет товар: {ждал}", ждал)
    c(f"завоз прошёл ({завоз}), удаление отказало ({удаление})",
      isinstance(завоз, int) and удаление == "has_products")
    c("модель на месте, товар с остатком 3 ссылается на неё",
      db.get_model(mid) is not None and db.get_product(завоз)["model_id"] == mid and db.get_product(завоз)["stock"] == 3)

    # Удаление первым: завоз дожидается его и видит, что модели нет.
    mid2 = db.add_model("accessories", "QA гонка 2")
    удаление, завоз, ждал = _параллельно(
        lambda: db.delete_model(mid2),
        lambda: db.create_point_product(mid2, "Минск", 20, cost=10, stock=3), держать=True)
    c(f"завоз ждал, пока удаление закончит: {ждал}", ждал)
    c(f"удаление прошло ({удаление}), завоз — «модели нет» ({завоз})", удаление == "deleted" and завоз is None)
    c("битых ссылок на модель нет", _битых_связей() == 0)

    # Как в отчёте: ручка прочитала модель, и тут модель удалили — до транзакции завоза.
    mid3 = db.add_model("accessories", "QA гонка 3")
    настоящее = db.get_model

    def читает_и_удаляют(model_id):
        м = настоящее(model_id)
        if model_id == mid3:
            c("пока ручка завоза думает, модель удаляется", db.delete_model(mid3) == "deleted")
        return м
    db.get_model = читает_и_удаляют
    try:
        r = client.post("/api/admin/product/from-model", json={
            "initData": "x", "model_id": mid3, "city": "Минск", "price": 20, "cost": 10, "stock": 3})
    finally:
        db.get_model = настоящее
    d = r.get_json() or {}
    c(f"завоз отказал понятно: {r.status_code} {d}", r.status_code == 409 and d.get("error") == "model_gone" and d.get("message"))
    c("товара на удалённую модель нет", not any(p["model_id"] == mid3 for p in db.get_all_products()))
    c("битых ссылок на модель нет", _битых_связей() == 0)

    # Два завоза одной модели на одну точку: второй видит первый уже в транзакции.
    mid4 = db.add_model("accessories", "QA гонка 4")
    раз, два, ждал = _параллельно(
        lambda: db.create_point_product(mid4, "Минск", 20, cost=10, stock=1),
        lambda: db.create_point_product(mid4, "Минск", 25, cost=10, stock=4), держать=True)
    c(f"второй завоз той же модели на ту же точку — отказ ({раз}, {два})", isinstance(раз, int) and два == "already_here")
    c("на точке одна строка этой модели",
      len([p for p in db.get_all_products() if p["model_id"] == mid4 and p["city"] == "Минск"]) == 1)
    c("на другую точку — можно", isinstance(db.create_point_product(mid4, "Туров", 20, cost=10, stock=1), int))

    _чисто()
    return c.fails


def run_галерея_модели_не_уходит_с_точкой():
    c = Checker("LM-02: удаление точки не трогает галерею модели")
    _чисто(); as_admin()

    старый = db.add_product("Минск", "accessories", "QA галерея", 20, 0, cost=10)
    db.add_product_photo(старый, "qa-extra-1", "qa-extra-1s")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": старый})
    mid = (r.get_json() or {}).get("model_id")
    c("товар стал моделью, фото — в её галерее", mid and [g["file_id"] for g in db.model_photos(mid)] == ["qa-extra-1"])
    c("фото принадлежит модели, а не точке (product_id = 0)", [g["product_id"] for g in db.model_photos(mid)] == [0])
    r = client.post("/api/admin/product/from-model", json={
        "initData": "x", "model_id": mid, "city": "Туров", "price": 20, "cost": 10, "stock": 0})
    второй = (r.get_json() or {}).get("id")
    c("модель завезена на вторую точку", bool(второй))
    c("исходная точка удалена", (client.post("/api/admin/product/delete", json={"initData": "x", "id": старый}).get_json() or {}).get("ok"))
    c("галерея модели цела — её видит вторая точка", [g["file_id"] for g in db.model_photos(mid)] == ["qa-extra-1"])
    витрина = next((x for x in client.get("/api/products").get_json() if x["id"] == второй), {})
    c("и витрина показывает фото на второй точке", any("qa-extra-1" in ph["url"] for ph in витрина.get("photos") or []))

    # Строки, уже записанные по-старому (миграция на модели, «Сделать моделью»
    # до 2.10): номер товара при фото модели. Уборка при запуске их чинит.
    м2 = db.add_model("accessories", "QA старая галерея")
    точка = db.create_point_product(м2, "Минск", 20, cost=10, stock=0)
    db.create_point_product(м2, "Туров", 20, cost=10, stock=0)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("INSERT INTO product_photos (product_id, model_id, file_id, thumb_id, sort) VALUES (%s, %s, %s, %s, %s)"),
                (точка, м2, "qa-old-1", "qa-old-1s", 1))
    conn.commit(); conn.close()
    from partut.db import photos
    photos._ensure_photo_columns()
    photos._ensure_photo_columns()                     # второй запуск ничего не меняет
    c("старая строка поправлена: фото модели без номера товара", [g["product_id"] for g in db.model_photos(м2)] == [0])
    db.delete_product(точка)
    c("удалили точку — галерея модели на месте", [g["file_id"] for g in db.model_photos(м2)] == ["qa-old-1"])

    # Даже если такая строка проскочит мимо уборки — удаление точки её не тронет.
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE product_photos SET product_id = %s WHERE model_id = %s"), (точка, м2))
    conn.commit(); conn.close()
    db.delete_product(точка)
    c("удаление точки не трогает фото с model_id, даже с её номером", len(db.model_photos(м2)) == 1)

    # А своя галерея товара без модели уходит вместе с ним, как раньше.
    одиночка = db.add_product("Минск", "accessories", "QA одиночка", 20, 0, cost=10)
    db.add_product_photo(одиночка, "qa-own-1", "")
    db.delete_product(одиночка)
    c("галерея товара без модели уходит вместе с ним", not any(ph["file_id"] == "qa-own-1" for ph in db.all_product_photos()))

    _чисто()
    return c.fails


def run_двойник_на_той_же_точке():
    c = Checker("LM-03: привязка двойника не даёт двух строк на точке")
    _чисто(); as_admin()

    mid = db.add_model("accessories", "QA дубль", "QA Brand")
    есть = db.create_point_product(mid, "Минск", 20, cost=10, stock=2)
    старый = db.add_product("Минск", "accessories", "QA дубль", 25, 4, cost=12, brand="QA Brand")
    for link_to in (None, mid):
        r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": старый, "link_to": link_to})
        d = r.get_json() or {}
        c(f"{'с подтверждением' if link_to else 'без подтверждения'}: отказ «уже на этой точке», "
          f"а не вопрос «привязать?» ({r.status_code} {d.get('error')})",
          r.status_code == 400 and d.get("error") == "twin_on_point" and d.get("product_id") == есть
          and "📦 Склад" in (d.get("message") or ""))
    c("старый товар остался без модели, цена и остаток свои",
      db.get_product(старый)["model_id"] is None and float(db.get_product(старый)["price"]) == 25.0)
    c("на точке одна строка модели",
      [p["id"] for p in db.get_all_products() if p["model_id"] == mid and p["city"] == "Минск"] == [есть])

    # LM-04: совет из отказа выполняется буквально — и ничего не теряет.
    # Раньше он звал удалить старый товар и обещал, что отзывы сохранятся, а
    # удаление стирало отзыв: к модели он не привязан, привязки ведь не было.
    db.ensure_user(9401)
    отзыв = db.add_review(старый, 9401, 5, "Хороший, беру второй раз")
    db.set_review_status(отзыв, "approved")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": старый})
    совет = (r.get_json() or {}).get("message") or ""
    c(f"LM-04: совет не зовёт удалять и не обещает сохранить отзывы: {совет!r}",
      "удалите" not in совет and "сохранятся" not in совет and "Не удаляйте" in совет)
    c("LM-04: совет называет, что унесло бы удаление: отзывы (1)", "отзывы (1)" in совет)
    c("LM-04: совет — снять с витрины", "Снять с витрины" in совет)
    шаги = [client.post("/api/admin/stock/move", json={"initData": "x", "id": старый, "qty": 4, "reason": "lost"}),
            client.post("/api/admin/stock/move", json={"initData": "x", "id": есть, "qty": 4, "reason": "in"}),
            client.post("/api/admin/product/update", json={"initData": "x", "id": старый, "field": "hidden", "value": 1})]
    c(f"шаги совета прошли: {[ш.status_code for ш in шаги]}", all(ш.status_code == 200 for ш in шаги))
    c("остаток перенесён: старый 0, модель 6",
      db.get_product(старый)["stock"] == 0 and db.get_product(есть)["stock"] == 6)
    c("старого товара нет на витрине", not any(x["id"] == старый for x in client.get("/api/products").get_json()))
    c("LM-04: отзыв на месте", [x["id"] for x in db.list_reviews(старый)] == [отзыв])

    # Двойник на ДРУГОЙ точке привязывается как раньше.
    туров = db.add_product("Туров", "accessories", "qa дубль", 22, 1, cost=12, brand="qa brand")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": туров})
    c("на другой точке — обычный вопрос «привязать?»", r.status_code == 409 and (r.get_json() or {}).get("error") == "exists")
    r = client.post("/api/admin/product/to-model", json={"initData": "x", "id": туров, "link_to": mid})
    c("и привязка проходит", (r.get_json() or {}).get("ok") and db.get_product(туров)["model_id"] == mid)

    _чисто()
    return c.fails
