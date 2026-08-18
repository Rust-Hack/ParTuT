"""Четыре уровня доступа и журнал действий.

  1. Разработчик — всё, что у владельца, плюс техническое (сброс статистики).
  2. Владелец    — весь магазин: ассортимент, цены, настройки, деньги, права.
  3. Продавец    — свои точки: заказы, склад, цены, завоз. Пустой город значит
     «продавец всех точек» и НЕ повышает до владельца.
  4. Покупатель  — ничего из админского.

Списки двух верхних уровней живут в переменных сервера, а не в базе: из
приложения их не отредактировать, поэтому никто не выдаст права сам себе.

Здесь проверяются НАСТОЯЩИЕ проверки прав, а не заглушка as_admin(): иначе
тест про доступ проверял бы сам себя.
"""
from _common import (db, client, server, Checker, as_admin, real_auth, REAL_GET_USER)

import config

DEV = 7300           # ведущий проекта
OWNER = 7301         # владелец магазина
SELLER = 7302        # продавец Турова
ROAMER = 7303        # продавец всех точек (доступ без города)
BUYER = 7304         # обычный покупатель


def _as(uid):
    """Запросы идут от этого человека — через настоящую проверку прав."""
    real_auth()
    server.get_user = lambda init: {"id": uid, "username": f"u{uid}"}


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "models", "orders", "admin_log", "reviews"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    server._cache_bust()


def _post(path, **body):
    return client.post(path, json={"initData": "x", **body})


def _code(path, **body):
    return _post(path, **body).status_code


def run():
    _clean()
    old_dev, old_owner = config.DEV_IDS, config.SUPER_ADMIN_IDS
    config.DEV_IDS = old_dev | {DEV}
    config.SUPER_ADMIN_IDS = old_owner | {DEV, OWNER}
    db.add_staff(SELLER, "Туров", "продавец точки")
    db.add_staff(ROAMER, "", "продавец всех точек")
    config.refresh_staff()

    mid = db.add_model("podsystem", "XROS", brand="Vaporesso")
    minsk = db.add_product_from_model(mid, "Минск", 30.0, stock=5)
    turov = db.add_product_from_model(mid, "Туров", 32.0, stock=4)
    fails = []

    try:
        # ---------- Кто есть кто ----------
        c = Checker("Роли")
        for uid, role in ((DEV, "dev"), (OWNER, "owner"), (SELLER, "seller"),
                          (ROAMER, "seller"), (BUYER, "")):
            _as(uid)
            me = client.post("/api/me", json={"initData": "x"}).get_json()
            c(f"{uid} → {role or 'покупатель'}", me["role"] == role)
        _as(SELLER)
        c("у продавца точки видна его точка",
          client.post("/api/me", json={"initData": "x"}).get_json()["admin_city"] == "Туров")
        _as(ROAMER)
        c("у продавца всех точек точки нет",
          client.post("/api/me", json={"initData": "x"}).get_json()["admin_city"] == "")

        # ---------- Продавец точки ----------
        c2 = Checker("Продавец точки")
        _as(SELLER)
        c2("свою точку правит",
          _post("/api/admin/product/update", id=turov, field="price", value="35").get_json().get("ok"))
        r = _post("/api/admin/product/update", id=minsk, field="price", value="1")
        c2("чужую — нет", r.status_code == 403 and r.get_json()["error"] == "other_city")
        c2("цена чужой точки цела", db.get_product(minsk)["price"] == 30.0)
        c2("чужой склад не тронет", _code("/api/admin/stock/move", id=minsk, qty=1, reason="in") == 403)
        c2("свой склад — пожалуйста",
          _post("/api/admin/stock/move", id=turov, qty=2, reason="in").get_json().get("ok"))
        c2("историю чужого склада не прочтёт", _code("/api/admin/stock/moves", id=minsk) == 403)
        c2("свою историю читает", _post("/api/admin/stock/moves", id=turov).get_json().get("ok"))
        c2("на чужую точку не завезёт",
          _code("/api/admin/product/from-model", model_id=mid, city="Лунинец", price="20") == 403)
        c2("товар на чужую точку не увезёт",
          _code("/api/admin/product/update", id=turov, field="city", value="Минск") == 403)

        # ---------- Продавец всех точек ----------
        # Раньше пустой город означал почти владельца: он правил ассортимент,
        # промокоды и реквизиты оплаты. Теперь это просто продавец пошире.
        c3 = Checker("Продавец всех точек")
        _as(ROAMER)
        c3("правит любую точку",
          _post("/api/admin/product/update", id=minsk, field="price", value="31").get_json().get("ok"))
        c3("видит заказы всех точек", _post("/api/admin/orders").get_json().get("ok"))
        r = _post("/api/admin/model", category="podsystem", name="Своя модель")
        c3("но ассортимент ему закрыт", r.status_code == 403 and r.get_json()["error"] == "owner_only")
        c3("и объяснено почему", "владельца" in (r.get_json().get("message") or ""))
        c3("реквизиты оплаты не поменяет",
          _code("/api/admin/settings/update", payment_info="мой кошелёк") == 403)
        c3("промокод не выпишет", _code("/api/admin/promo", code="SELF50") == 403)
        c3("монеты никому не начислит", _code("/api/admin/coins/adjust", user_id=BUYER, delta=1000) == 403)
        c3("прокруты колеса себе не выпишет", _code("/api/admin/wheel/grant") == 403)
        c3("список пользователей не увидит", _code("/api/admin/users") == 403)
        c3("реферала не отвяжет", _code("/api/admin/referral/unlink", user_id=BUYER) == 403)
        c3("выручку магазина не увидит", _code("/api/admin/stats") == 403)
        c3("журнал ему закрыт", _code("/api/admin/log") == 403)
        c3("доступ другим не выдаст", _code("/api/admin/staff/add", user_id=999, city="") == 403)
        c3("фото модели не тронет", _code("/api/admin/photo/delete", photo_id=1) == 403)
        c3("точку продаж не создаст", _code("/api/admin/location", name="Своя") == 403)
        c3("но ассортимент посмотреть может", _post("/api/admin/models").get_json().get("ok"))
        c3("и реквизиты прочитать — их спрашивает покупатель",
          _post("/api/admin/settings").get_json().get("ok"))
        c3("и сводку своего дня видит", _post("/api/admin/today").get_json().get("ok"))

        # ---------- Отзывы: отвечает продавец, решает владелец ----------
        c4 = Checker("Отзывы")
        rid = db.add_review(minsk, BUYER, 3, "Средне", "vasya")
        _as(SELLER)
        c4("продавец видит отзывы", _post("/api/admin/reviews", status="all").get_json().get("ok"))
        c4("и отвечает покупателю",
          _post("/api/admin/review/reply", id=rid, text="Спасибо, разберёмся").get_json().get("ok"))
        c4("но не публикует", _code("/api/admin/review/decide", id=rid, ok=True) == 403)
        c4("и не удаляет", _code("/api/admin/review/delete", id=rid) == 403)
        _as(OWNER)
        c4("владелец публикует",
          _post("/api/admin/review/decide", id=rid, ok=True).get_json().get("ok"))
        c4("владелец удаляет", _post("/api/admin/review/delete", id=rid).get_json().get("ok"))

        # ---------- Владелец ----------
        c5 = Checker("Владелец")
        _as(OWNER)
        c5("заводит модель",
          _post("/api/admin/model", category="podsystem", name="Общая").get_json().get("ok"))
        c5("меняет реквизиты",
          _post("/api/admin/settings/update", payment_info="Карта 1234").get_json().get("ok"))
        c5("видит статистику", _post("/api/admin/stats").get_json().get("ok"))
        c5("видит журнал", _post("/api/admin/log").get_json().get("ok"))
        c5("выдаёт доступ продавцу",
          _post("/api/admin/staff/add", user_id=7399, city="Туров").get_json().get("ok"))
        c5("начисляет монеты", _post("/api/admin/coins/adjust", user_id=BUYER, delta=50).get_json().get("ok"))
        r = _post("/api/admin/stats/reset")
        c5("но техническое — не его", r.status_code == 403 and r.get_json()["error"] == "dev_only")
        db.remove_staff(7399)

        # ---------- Разработчик ----------
        c6 = Checker("Разработчик")
        _as(DEV)
        c6("делает всё, что владелец", _post("/api/admin/log").get_json().get("ok"))
        c6("и техническое тоже", _post("/api/admin/stats/reset").get_json().get("ok"))

        # ---------- Покупатель ----------
        c7 = Checker("Покупатель")
        _as(BUYER)
        for path in ("/api/admin/orders", "/api/admin/products", "/api/admin/today",
                     "/api/admin/models", "/api/admin/reviews", "/api/admin/stats",
                     "/api/admin/log", "/api/admin/settings"):
            c7(f"{path} закрыт", _code(path) == 403)
        c7("товар не тронет", _code("/api/admin/product/update", id=minsk, field="price", value="1") == 403)
        c7("цена цела", db.get_product(minsk)["price"] == 31.0)

        # ---------- Журнал ----------
        c8 = Checker("Журнал действий")
        rows = db.list_admin_log(limit=80)
        prices = [r for r in rows if r["action"] == "product/update"]
        c8("правка цены записана", bool(prices))
        c8("видно, кто именно", any(int(r["admin_id"]) == SELLER for r in prices))
        c8("видно, что менялось", any("price" in (r["details"] or "") for r in prices))
        c8("отказ по правам в журнал не попал",
          all(int(r["admin_id"]) != BUYER for r in rows))
        c8("чтение не засоряет журнал",
          all(r["action"] not in ("orders", "models", "stats", "today", "log") for r in rows))
        c8("секрет не сохранён", all("initData" not in (r["details"] or "") for r in rows))
        c8("действия владельца тоже записаны", any(r["action"] == "model" for r in rows))

        fails = (c.fails + c2.fails + c3.fails + c4.fails + c5.fails
                 + c6.fails + c7.fails + c8.fails)
    finally:
        config.DEV_IDS, config.SUPER_ADMIN_IDS = old_dev, old_owner
        db.remove_staff(SELLER)
        db.remove_staff(ROAMER)
        config.refresh_staff()
        as_admin()                 # вернуть общий стенд в исходное состояние
        server.get_user = REAL_GET_USER
        _clean()
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
