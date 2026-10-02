"""Архив товаров: «больше не возим» без удаления.

Решения владельца (2 октября 2026): в архив убирают товар на точке, а
владелец — и сразу на всех точках; только пустую полку и без невыданных
заказов, обхода нет; ничего не стирается; вернуть — та же запись, ровно
такой, какой была; продавец — на своей точке; удалить насовсем — владелец,
из архива и без истории (tests/test_delete_guard.py).
"""
import threading

from _common import db, client, Checker, as_admin
from partut.db import stock as stockmod


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "product_photos", "reviews", "favorites",
              "stock_alerts", "orders", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _пост(путь, **тело):
    return client.post(путь, json={"initData": "x", **тело})


def _в_архив(pid, archived=True):
    return _пост("/api/admin/product/archive", id=pid, archived=archived)


def _архив():
    return {x["id"]: x for x in (_пост("/api/admin/archive").get_json() or {}).get("items", [])}


def run_в_архив_и_обратно():
    c = Checker("Архив: в архив, где его не видно, и обратно той же записью")
    _чисто(); as_admin()

    mid = db.add_model("liquid", "QA Жижа", "QA", "", {}, ["Мята", "Вишня"])
    pid = db.create_point_product(mid, "Минск", 20.0, 9.0, variants=[{"flavor": "Мята", "stock": 3}])
    db.ensure_user(9501)
    отзыв = db.add_review(pid, 9501, 5, "Вкусная")
    db.set_review_status(отзыв, "approved")
    db.add_model_photo(mid, "qa-arch-1", "")
    db.add_favorite(pid, 9501)

    # --- Пока на полке есть штуки — нельзя ---
    r = _в_архив(pid)
    d = r.get_json()
    c(f"на полке 3 шт — отказ, объяснено, что делать: {r.status_code} {d.get('error')}",
      r.status_code == 409 and d["error"] == "on_stock" and d["stock"] == 3
      and "спишите" in d["message"] and "Снять с витрины" in d["message"])
    c("товар не в архиве", not db.get_product(pid)["archived"])

    # --- Невыданный заказ — нельзя, и force не помогает ---
    db.stock_operation(pid, "lost", 2, flavor="Мята", admin_id=1)
    oid = db.create_order(9501, "buyer", "Минск", [{"id": pid, "flavor": "Мята", "name": "QA Жижа", "price": 20.0, "qty": 1}], 20.0, "")
    db.stock_operation(pid, "lost", 1, flavor="Мята", admin_id=1)      # последняя ушла «в заказ»
    for ещё in ({}, {"force": True}):
        r = _пост("/api/admin/product/archive", id=pid, archived=True, **ещё)
        c(f"невыданный заказ — отказ {ещё}", r.status_code == 409 and r.get_json()["error"] == "open_orders"
          and r.get_json()["count"] == 1)
    db.set_order_status(oid, "issued")

    # --- В архив ---
    движений_до = len(db.get_stock_moves(pid, 100))
    r = _в_архив(pid)
    c("пустая полка без заказов — в архиве", r.status_code == 200 and r.get_json()["changed"] and db.get_product(pid)["archived"] == 1)
    c("повтор — без изменений, не ошибка", _в_архив(pid).get_json() == {"ok": True, "changed": False, "archived": True})
    c("нет на витрине", not any(x["id"] == pid for x in client.get("/api/products").get_json()))
    c("нет в общем списке товаров", not any(p["id"] == pid for p in db.get_all_products()))
    c("нет в каталоге бота", not any(p["id"] == pid for p in db.get_products("Минск", "liquid")))
    c("есть в «🗄 Архив»", pid in _архив() and _архив()[pid]["city"] == "Минск")
    c("удалить насовсем нельзя: история есть", _архив()[pid]["can_delete"] is False)

    # --- Ничего не стёрто ---
    c("отзыв на месте", [x["id"] for x in db.list_reviews(pid)] == [отзыв])
    c("галерея модели на месте", [g["file_id"] for g in db.model_photos(mid)] == ["qa-arch-1"])
    c("история склада на месте", len(db.get_stock_moves(pid, 100)) == движений_до)
    c("избранное покупателя на месте", any(int(f["product_id"] if isinstance(f, dict) else f) == pid
                                           for f in db.favorites_for_user(9501)))

    # --- Архивному — ни прихода, ни состава ---
    r = _пост("/api/admin/stock/move", id=pid, qty=5, reason="in", flavor="Мята")
    c(f"приход на архивный — отказ «верните»: {r.status_code} {(r.get_json() or {}).get('error')}",
      r.status_code >= 400 and r.get_json()["error"] == "archived" and "верните" in r.get_json()["message"])
    r = _пост("/api/admin/product/variants/change", id=pid, add=[{"flavor": "Вишня", "qty": 2}], remove=[])
    c(f"новый вкус с остатком на архивный — отказ: {r.status_code}", r.status_code >= 400 and r.get_json()["error"] == "archived")
    c("остаток по-прежнему 0", db.get_product(pid)["stock"] == 0)

    # --- Завезти туда же — не двойник, а «верните» ---
    r = _пост("/api/admin/product/from-model", model_id=mid, city="Минск", price=21, cost=9,
              variants=[{"flavor": "Мята", "stock": 1}])
    d = r.get_json() or {}
    c(f"завоз на точку, где товар в архиве — 409 in_archive с его номером: {r.status_code} {d.get('error')}",
      r.status_code == 409 and d["error"] == "in_archive" and d["id"] == pid)
    c("второй строки не появилось",
      len([p for p in db.get_all_products(include_archived=True) if p["model_id"] == mid and p["city"] == "Минск"]) == 1)
    туров = db.create_point_product(mid, "Туров", 22.0, 9.0, variants=[{"flavor": "Мята", "stock": 0}])
    r = _пост("/api/admin/product/update", id=туров, field="city", value="Минск")
    c("перенести другую точку туда, где этот товар в архиве, нельзя",
      r.status_code >= 400 and db.get_product(туров)["city"] == "Туров")

    # --- Вернуть — та же запись, как была ---
    r = _в_архив(pid, False)
    c("вернули", r.status_code == 200 and r.get_json()["changed"] and db.get_product(pid)["archived"] == 0)
    c("тот же номер, та же цена", db.get_product(pid)["id"] == pid and float(db.get_product(pid)["price"]) == 20.0)
    c("снова в списке и на витрине (была на витрине)",
      any(p["id"] == pid for p in db.get_all_products()) and any(x["id"] == pid for x in client.get("/api/products").get_json()))
    c("отзыв при нём", [x["id"] for x in db.list_reviews(pid)] == [отзыв])
    c("приход снова принимается", _пост("/api/admin/stock/move", id=pid, qty=5, reason="in", flavor="Мята").status_code == 200)

    # Снятый с витрины возвращается снятым.
    db.stock_operation(pid, "lost", 5, flavor="Мята", admin_id=1)
    _пост("/api/admin/product/update", id=pid, field="hidden", value=1)
    _в_архив(pid); _в_архив(pid, False)
    c("снятый с витрины вернулся снятым", db.get_product(pid)["hidden"] == 1
      and not any(x["id"] == pid for x in client.get("/api/products").get_json()))

    _чисто()
    return c.fails


def run_на_всех_точках_и_права():
    c = Checker("Архив: все точки разом, права продавца, подсказки")
    _чисто(); as_admin()

    mid = db.add_model("accessories", "QA Зарядка", "QA")
    пусто = db.create_point_product(mid, "Минск", 10.0, 5.0, stock=0)
    с_остатком = db.create_point_product(mid, "Туров", 10.0, 5.0, stock=2)
    с_заказом = db.create_point_product(mid, "Лунинец", 10.0, 5.0, stock=0)
    db.create_order(9502, "b", "Лунинец", [{"id": с_заказом, "name": "QA Зарядка", "price": 10.0, "qty": 1}], 10.0, "")

    r = _пост("/api/admin/model/archive", model_id=mid)
    d = r.get_json()
    c(f"в архив на всех: убрана пустая, две остались с причинами: {d}",
      d["archived"] == ["Минск"] and {x["city"]: x["error"] for x in d["left"]} == {"Туров": "on_stock", "Лунинец": "open_orders"})
    c("в базе — так же", db.get_product(пусто)["archived"] == 1 and not db.get_product(с_остатком)["archived"]
      and not db.get_product(с_заказом)["archived"])

    # Удаление описания, пока товар в архиве, — нельзя, и сказано честно.
    r = _пост("/api/admin/model/delete", id=mid)
    d = r.get_json()
    c(f"описание не удалить: названы и точки, и архив: {d.get('message')!r}",
      r.status_code == 400 and d["archived_cities"] == ["Минск"] and "в архиве на точках: Минск" in d["message"]
      and "стоит на точках: Лунинец, Туров" in d["message"])

    # «Новый товар» с тем же названием, когда модель только в архиве, подсказывает вернуть.
    ам = db.add_model("accessories", "QA Только архив", "QA")
    ап = db.create_point_product(ам, "Минск", 10.0, 5.0, stock=0)
    _в_архив(ап)
    r = _пост("/api/admin/product/publish", client_token="archive-publish-0001",
              model={"category": "accessories", "name": "QA Только архив", "brand": "QA"},
              points=[{"city": "Минск", "price": "10", "cost": "5", "stock": 1}], photos=[])
    d = r.get_json() or {}
    c(f"«Новый товар» двойника из архива — «верните»: {d.get('message')!r}",
      d.get("error") == "exists" and d.get("archived") is True and "в архиве" in d.get("message", ""))

    # Продавец: своя точка — да, чужая — нет, все точки разом и «удалить» — нет.
    своя = db.create_point_product(db.add_model("accessories", "QA Своя", "QA"), "Минск", 10.0, 5.0, stock=0)
    чужая = db.create_point_product(db.add_model("accessories", "QA Чужая", "QA"), "Туров", 10.0, 5.0, stock=0)
    as_admin(uid=9200, username="продавец", role="staff", city="Минск")
    c("продавец убирает свою точку", _в_архив(своя).get_json().get("ok") and db.get_product(своя)["archived"] == 1)
    c("и возвращает", _в_архив(своя, False).get_json().get("ok") and db.get_product(своя)["archived"] == 0)
    c("чужую — нет", _в_архив(чужая).status_code == 403 and not db.get_product(чужая)["archived"])
    c("«на всех точках» — нет", _пост("/api/admin/model/archive", model_id=mid).status_code == 403)
    _в_архив(своя)
    архив = _архив()
    c("в архиве продавец видит только свою точку", set(архив) == {своя} or all(x["city"] == "Минск" for x in архив.values()))
    c("и без «удалить насовсем»", all("can_delete" not in x for x in архив.values()))
    as_admin()
    c("владельцу — «удалить» у товара без истории", _архив()[своя]["can_delete"] is True)

    _чисто()
    return c.fails


def _наперегонки(первый, второй, где_ждать):
    """Первый поток доходит до точки внутри своей транзакции (под замком) и
    ждёт; тем временем стартует второй и должен упереться в замок."""
    взял, отпустить = threading.Event(), threading.Event()
    модуль, имя = где_ждать
    настоящее = getattr(модуль, имя)
    поток = []

    def ждёт(*a, **kw):
        итог = настоящее(*a, **kw)
        if threading.current_thread() is поток[0]:
            взял.set()
            отпустить.wait(5)
        return итог

    итоги = {}

    def зап(ключ, f):
        try:
            итоги[ключ] = f()
        except Exception as e:      # noqa: BLE001 — итог проверит тест
            итоги[ключ] = e

    setattr(модуль, имя, ждёт)
    try:
        т1 = threading.Thread(target=зап, args=("1", первый)); поток.append(т1); т1.start()
        assert взял.wait(5), "первый не дошёл до замка"
        т2 = threading.Thread(target=зап, args=("2", второй)); т2.start()
        т2.join(0.5)
        ждал = т2.is_alive()
        отпустить.set()
        т1.join(10); т2.join(10)
    finally:
        setattr(модуль, имя, настоящее)
        отпустить.set()
    return итоги.get("1"), итоги.get("2"), ждал


def run_приход_и_архив_в_одну_секунду():
    c = Checker("Архив: приход по вкусу и «в архив» в одну секунду")
    _чисто(); as_admin()
    mid = db.add_model("liquid", "QA Гонка", "QA", "", {}, ["Мята"])

    # Приход первым: архив ждёт и видит остаток.
    pid = db.create_point_product(mid, "Минск", 20.0, 9.0, variants=[{"flavor": "Мята", "stock": 0}])
    приход, архив, ждал = _наперегонки(
        lambda: db.stock_operation(pid, "in", 4, flavor="Мята", admin_id=1),
        lambda: db.archive_product(pid, True), (stockmod, "_record_move"))
    c(f"архив ждал прихода: {ждал}", ждал)
    c(f"приход прошёл, архив отказал «на полке 4»: {архив!r}",
      isinstance(приход, dict) and isinstance(архив, db.ArchiveRefused) and архив.code == "on_stock")
    c("товар не в архиве, остаток 4", not db.get_product(pid)["archived"] and db.get_product(pid)["stock"] == 4)

    # Архив первым: приход ждёт и видит архив.
    pid2 = db.create_point_product(mid, "Туров", 20.0, 9.0, variants=[{"flavor": "Мята", "stock": 0}])
    архив, приход, ждал = _наперегонки(
        lambda: db.archive_product(pid2, True),
        lambda: db.stock_operation(pid2, "in", 4, flavor="Мята", admin_id=1), (db, "open_orders_with_product"))
    c(f"приход ждал архива: {ждал}", ждал)
    c(f"архив прошёл, приход отказал «в архиве»: {приход!r}",
      isinstance(архив, dict) and архив["changed"] and isinstance(приход, db.StockRefused) and приход.code == "archived")
    c("в архиве с остатком 0", db.get_product(pid2)["archived"] == 1 and db.get_product(pid2)["stock"] == 0)

    _чисто()
    return c.fails


def run_удаление_насовсем_и_возврат():
    """AR-01: «удалить насовсем» проверяло историю, а удаляло после — в этот
    промежуток товар возвращали из архива и принимали на него 7 шт, и
    удаление стирало живой товар с остатком. Теперь проверка и удаление —
    одна транзакция под тем же замком, что у архива и возврата."""
    from partut.db import catalog
    c = Checker("Архив: удалить насовсем и вернуть в одну секунду")
    _чисто(); as_admin()
    mid = db.add_model("liquid", "QA Удаление", "QA", "", {}, ["Mint"])

    # Удаление первым: возврат ждёт и узнаёт, что товара нет; прихода некуда.
    pid = db.create_point_product(mid, "Минск", 20.0, 9.0, variants=[{"flavor": "Mint", "stock": 0}])
    db.archive_product(pid, True)

    def вернуть_и_принять():
        db.archive_product(pid, False)
        return db.stock_operation(pid, "in", 7, flavor="Mint", admin_id=1)
    удаление, возврат, ждал = _наперегонки(lambda: db.delete_archived_product(pid), вернуть_и_принять,
                                           (catalog, "_товар_под_замком"))
    c(f"возврат ждал удаления: {ждал}", ждал)
    c(f"удаление прошло, возврат — «товара нет»: {возврат!r}",
      isinstance(удаление, dict) and isinstance(возврат, db.ArchiveRefused) and возврат.code == "not_found")
    c("товара и его вариантов нет, прихода без товара нет",
      db.get_product(pid) is None and not db.get_variants(pid) and not db.get_stock_moves(pid, 10))

    # Возврат первым: удаление ждёт и видит, что товар уже не в архиве.
    pid2 = db.create_point_product(mid, "Туров", 20.0, 9.0, variants=[{"flavor": "Mint", "stock": 0}])
    db.archive_product(pid2, True)
    возврат, удаление, ждал = _наперегонки(lambda: db.archive_product(pid2, False),
                                           lambda: db.delete_archived_product(pid2), (catalog, "_товар_под_замком"))
    c(f"удаление ждало возврата: {ждал}", ждал)
    c(f"возврат прошёл, удаление отказало «не в архиве»: {удаление!r}",
      isinstance(возврат, dict) and возврат["changed"] and isinstance(удаление, db.ArchiveRefused) and удаление.code == "use_archive")
    c("товар цел и на точке", db.get_product(pid2) is not None and not db.get_product(pid2)["archived"])
    c("и принимает приход", db.stock_operation(pid2, "in", 7, flavor="Mint", admin_id=1)["stock"] == 7)

    # Приход держит вариант и товар — удаление (запирающее варианты первыми)
    # ждёт, а не сцепляется с ним намертво; потом видит, что товар не в архиве.
    приход, удаление, ждал = _наперегонки(lambda: db.stock_operation(pid2, "in", 1, flavor="Mint", admin_id=1),
                                          lambda: db.delete_archived_product(pid2), (stockmod, "_record_move"))
    c(f"удаление ждало прихода, без взаимной блокировки: {удаление!r}",
      ждал and isinstance(приход, dict) and isinstance(удаление, db.ArchiveRefused) and удаление.code == "use_archive")
    c("остаток 8", db.get_product(pid2)["stock"] == 8)

    # С историей — не удаляется, даже если в архиве.
    db.stock_operation(pid2, "lost", 8, flavor="Mint", admin_id=1)
    db.archive_product(pid2, True)
    try:
        db.delete_archived_product(pid2)
        c("с историей — отказ", False)
    except db.ArchiveRefused as e:
        c("с историей — отказ has_history", e.code == "has_history" and e.extra["moves"] >= 3)
    c("товар на месте", db.get_product(pid2) is not None)

    _чисто()
    return c.fails
