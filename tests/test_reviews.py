"""Отзывы о товарах.

Главное правило: право на отзыв даёт покупка. Иначе оценка ничего не значит —
конкурент поставит единицу, не потратив ни рубля, а покупатель, который на неё
посмотрел, примет решение по выдумке.

Второе правило: пока отзыв не опубликован, он не влияет ни на среднюю оценку,
ни на витрину. Модерация без этого была бы декорацией.
"""
from _common import db, client, server, Checker, as_user, as_admin, deny_admin, SENT, reset_sent


BUYER = 9801
STRANGER = 9802


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM reviews")
    cur.execute("DELETE FROM orders")
    cur.execute("DELETE FROM products")
    cur.execute("DELETE FROM models")
    conn.commit(); conn.close()
    server._cache_bust()


def _buy(uid, pid, name, status="issued"):
    oid = db.create_order(uid, "buyer", "Минск", [{"id": pid, "name": name, "price": 20.0, "qty": 1}], 20.0, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s WHERE id = %s"), (status, oid))
    conn.commit(); conn.close()
    return oid


def _product(pid):
    server._cache_bust()
    return next(p for p in client.get("/api/products").get_json() if p["id"] == pid)


def run():
    c = Checker("Отзывы")
    _clean()

    pid = db.add_product("Минск", "podsystem", "ОтзывПод", 20.0, 5)
    other = db.add_product("Минск", "podsystem", "ДругойПод", 25.0, 5)

    # --- Не покупал — не оценивает ---
    as_user(STRANGER, username="chuzhoy")
    r = client.post("/api/review", json={"initData": "x", "product_id": pid, "rating": 1, "text": "фигня"})
    c("посторонний оценить не может", r.status_code == 403)
    c("у товара нет оценки", _product(pid)["rating"]["count"] == 0)

    # Незавершённый заказ — тоже ещё не покупка.
    as_user(BUYER, username="vasya")
    _buy(BUYER, pid, "ОтзывПод", status="new")
    c("до выдачи оценить нельзя",
      client.post("/api/review", json={"initData": "x", "product_id": pid, "rating": 5}).status_code == 403)

    # --- Покупал — оценивает ---
    _buy(BUYER, pid, "ОтзывПод")
    can = client.post("/api/my-reviews", json={"initData": "x"}).get_json()
    c("товар предложен к оценке", any(p["id"] == pid for p in can["can"]))
    c("некупленный товар не предложен", all(p["id"] != other for p in can["can"]))

    reset_sent()
    real_bg = server._bg
    server._bg = lambda fn, *a, **k: fn(*a, **k)   # уведомление — сразу, без потока
    try:
        r = client.post("/api/review", json={"initData": "x", "product_id": pid, "rating": 5, "text": "Держит долго, пар густой"})
    finally:
        server._bg = real_bg
    c("отзыв принят", (r.get_json() or {}).get("ok"))
    c("админу пришло уведомление", any("отзыв" in (t or "").lower() for _, t, _ in SENT))

    # --- До публикации отзыв не влияет ни на что ---
    c("на витрине оценки ещё нет", _product(pid)["rating"]["count"] == 0)
    c("в списке товара отзыва ещё нет",
      client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"] == [])

    # --- Дважды один и тот же человек не оценивает ---
    c("повторный отзыв отклонён",
      client.post("/api/review", json={"initData": "x", "product_id": pid, "rating": 1}).status_code == 403)
    c("оценённый товар больше не предлагают",
      all(p["id"] != pid for p in client.post("/api/my-reviews", json={"initData": "x"}).get_json()["can"]))
    c("свой отзыв покупателю виден со статусом",
      client.post("/api/my-reviews", json={"initData": "x"}).get_json()["mine"][0]["status"] == "pending")

    # --- Мусор на входе ---
    c("оценка вне 1..5 отклонена",
      client.post("/api/review", json={"initData": "x", "product_id": other, "rating": 9}).status_code == 400)
    c("нечисловая оценка отклонена",
      client.post("/api/review", json={"initData": "x", "product_id": other, "rating": "пять"}).status_code == 400)

    # --- Модерация ---
    c2 = Checker("Модерация отзывов")
    deny_admin()
    c2("посторонний модерацию не видит",
      client.post("/api/admin/reviews", json={"initData": "x"}).status_code == 403)
    c2("посторонний не публикует",
      client.post("/api/admin/review/decide", json={"initData": "x", "id": 1, "ok": True}).status_code == 403)
    as_admin()

    pend = client.post("/api/admin/reviews", json={"initData": "x"}).get_json()["reviews"]
    c2("отзыв ждёт решения", len(pend) == 1)
    c2("админ видит товар и оценку", pend[0]["product"] == "ОтзывПод" and pend[0]["rating"] == 5)
    c2("автор подписан ником", pend[0]["who"] == "@vasya")

    rid = pend[0]["id"]
    c2("опубликован", client.post("/api/admin/review/decide", json={"initData": "x", "id": rid, "ok": True}).get_json()["status"] == "approved")
    c2("очередь опустела", client.post("/api/admin/reviews", json={"initData": "x"}).get_json()["reviews"] == [])
    pub = client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"]
    c2("отзыв виден всем", len(pub) == 1 and pub[0]["text"].startswith("Держит"))
    c2("оценка появилась на витрине", _product(pid)["rating"] == {"avg": 5.0, "count": 1})

    # --- Средняя оценка считается по опубликованным ---
    as_user(STRANGER, username="petya")
    _buy(STRANGER, pid, "ОтзывПод")
    client.post("/api/review", json={"initData": "x", "product_id": pid, "rating": 2, "text": "Быстро сел"})
    c2("непроверенный отзыв среднюю не двигает", _product(pid)["rating"]["avg"] == 5.0)
    as_admin()
    rid2 = client.post("/api/admin/reviews", json={"initData": "x"}).get_json()["reviews"][0]["id"]
    client.post("/api/admin/review/decide", json={"initData": "x", "id": rid2, "ok": True})
    c2("после публикации средняя пересчитана", _product(pid)["rating"] == {"avg": 3.5, "count": 2})

    # --- Скрытый отзыв ---
    client.post("/api/admin/review/decide", json={"initData": "x", "id": rid2, "ok": False})
    c2("скрытый ушёл из списка", len(client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"]) == 1)
    c2("и из средней оценки", _product(pid)["rating"] == {"avg": 5.0, "count": 1})
    c2("скрытый не возвращается в очередь",
      client.post("/api/admin/reviews", json={"initData": "x"}).get_json()["reviews"] == [])
    c2("решение по несуществующему отзыву — 404",
      client.post("/api/admin/review/decide", json={"initData": "x", "id": 999999, "ok": True}).status_code == 404)

    # --- Опубликованный отзыв не пропадает из админки ---
    # Раньше админ видел только очередь: опубликованный уходил из поля зрения
    # навсегда, и убрать его было уже нечем.
    c3 = Checker("Управление опубликованными")
    приняты = client.post("/api/admin/reviews", json={"initData": "x", "status": "approved"}).get_json()
    c3("опубликованные видны админу", len(приняты["reviews"]) == 1)
    c3("статус отдаётся", приняты["reviews"][0]["status"] == "approved")
    c3("счётчик очереди отдельно от списка", приняты["pending"] == 0)
    скрытые = client.post("/api/admin/reviews", json={"initData": "x", "status": "hidden"}).get_json()["reviews"]
    c3("скрытые тоже видны", len(скрытые) == 1)
    c3("во «всех» оба", len(client.post("/api/admin/reviews", json={"initData": "x", "status": "all"}).get_json()["reviews"]) == 2)
    c3("выдуманный фильтр не роняет — показываем очередь",
      client.post("/api/admin/reviews", json={"initData": "x", "status": "мусор"}).get_json()["status"] == "pending")

    # Скрытый можно вернуть — на то он и скрытый, а не удалённый.
    client.post("/api/admin/review/decide", json={"initData": "x", "id": rid2, "ok": True})
    c3("скрытый вернулся в публикацию", _product(pid)["rating"]["count"] == 2)
    client.post("/api/admin/review/decide", json={"initData": "x", "id": rid2, "ok": False})

    # --- Ответ магазина ---
    c3("ответ сохранён",
      client.post("/api/admin/review/reply", json={"initData": "x", "id": rid, "text": "Спасибо! Партия свежая."}).get_json()["ok"])
    видно = client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"][0]
    c3("ответ виден покупателям", видно["reply"] == "Спасибо! Партия свежая.")
    client.post("/api/admin/review/reply", json={"initData": "x", "id": rid, "text": "   "})
    c3("пустой ответ убирает его",
      client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"][0]["reply"] == "")
    c3("ответ на несуществующий отзыв — 404",
      client.post("/api/admin/review/reply", json={"initData": "x", "id": 999999, "text": "?"}).status_code == 404)

    # --- Удаление ---
    c3("отзыв удаляется",
      client.post("/api/admin/review/delete", json={"initData": "x", "id": rid}).get_json()["ok"])
    c3("и пропадает с витрины", _product(pid)["rating"]["count"] == 0)
    c3("и из списка товара", client.get(f"/api/reviews?product_id={pid}").get_json()["reviews"] == [])
    c3("повторное удаление — 404",
      client.post("/api/admin/review/delete", json={"initData": "x", "id": rid}).status_code == 404)
    c3("после удаления покупатель может оценить заново",
      any(p["id"] == pid for p in (as_user(BUYER, username="vasya") or
                                   client.post("/api/my-reviews", json={"initData": "x"}).get_json()["can"])))
    as_admin()

    deny_admin()
    c3("посторонний не удаляет",
      client.post("/api/admin/review/delete", json={"initData": "x", "id": rid2}).status_code == 403)
    c3("посторонний не отвечает",
      client.post("/api/admin/review/reply", json={"initData": "x", "id": rid2, "text": "я тут главный"}).status_code == 403)
    as_admin()

    # --- Товар без модели удалён: его отзывы уже никому не показать ---
    db.delete_product(pid)
    c2("висячих отзывов не осталось", db.list_reviews(pid, "approved") == [] and db.count_pending_reviews() == 0)

    c4 = run_model_reviews()

    _clean()
    return c.fails + c2.fails + c3.fails + c4


def run_model_reviews():
    """Оценивают вещь, а не её наличие в конкретном городе.

    Пока отзыв висел на товаре, один и тот же Elf Bar в Минске и Турове копил
    оценки раздельно: покупатель второй точки видел «отзывов пока нет» у
    модели, которую в первой оценили дюжину раз. А снятие товара с точки
    стирало чужие слова навсегда.
    """
    c = Checker("Отзыв принадлежит модели")
    _clean()
    as_admin()

    mid = db.add_model("liquid", "Husky", brand="Husky")
    minsk = db.add_product_from_model(mid, "Минск", 20.0, stock=5)
    turov = db.add_product_from_model(mid, "Туров", 22.0, stock=5)

    as_user(BUYER, username="vasya")
    _buy(BUYER, minsk, "Husky")
    rid = client.post("/api/review", json={"initData": "x", "product_id": minsk,
                                           "rating": 5, "text": "Отличная"}).get_json()["id"]
    as_admin()
    client.post("/api/admin/review/decide", json={"initData": "x", "id": rid, "ok": True})

    c("оценка видна на точке, где купили", _product(minsk)["rating"]["count"] == 1)
    c("и на другой точке тоже", _product(turov)["rating"]["count"] == 1)
    c("средняя одна и та же", _product(turov)["rating"]["avg"] == 5)
    c("текст отзыва читается с обеих точек",
      client.get(f"/api/reviews?product_id={turov}").get_json()["reviews"][0]["text"] == "Отличная")

    # Тот же человек покупает ту же модель на второй точке.
    as_user(BUYER, username="vasya")
    _buy(BUYER, turov, "Husky")
    can = client.post("/api/my-reviews", json={"initData": "x"}).get_json()["can"]
    c("второй раз ту же модель оценить не предлагают", all(p["id"] != turov for p in can))
    c("и попытка отклоняется",
      client.post("/api/review", json={"initData": "x", "product_id": turov, "rating": 1}).status_code == 403)

    # --- Снятие с точки не стирает отзывы ---
    as_admin()
    db.delete_product(minsk)
    server._cache_bust()
    c("отзыв пережил снятие товара с точки",
      len(client.get(f"/api/reviews?product_id={turov}").get_json()["reviews"]) == 1)
    c("и оценка на оставшейся точке цела", _product(turov)["rating"]["count"] == 1)
    c("в очереди админа отзыв не потерял имя",
      db.admin_reviews("all")[0]["product_name"] == "Husky")

    # --- Разные модели не смешиваются ---
    other = db.add_model("liquid", "Другая", brand="Husky")
    op = db.add_product_from_model(other, "Минск", 15.0, stock=3)
    server._cache_bust()
    c("у чужой модели своя оценка", _product(op)["rating"] == {"avg": 0, "count": 0})

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
