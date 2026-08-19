"""Касса сходится.

Три числа обязаны совпадать до копейки: то, что назвали покупателю при
оформлении, то, что легло в базу, и то, что складывается из составляющих
(товары минус монеты минус промокод плюс доставка). Разойдись они — и магазин
либо недосчитается денег, либо возьмёт с человека больше, чем показал.

Проверяются все сочетания скидок сразу, потому что ломается обычно не одна
скидка, а их встреча: монеты вместе с промокодом на заказе с доставкой, где
доставка ещё и бесплатна от порога.

Отдельно сверяется итог: сумма выданных заказов в базе против выручки, которую
показывает статистика. Это два разных запроса, и однажды они разойдутся.
"""
import itertools

from _common import db, client, Checker, as_user, as_admin

from partut.web import shopinfo

from partut import cache

ПОКУПАТЕЛЬ = 7501


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "promos"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (ПОКУПАТЕЛЬ,))
    conn.commit(); conn.close()
    cache.bust()


def run():
    _clean()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Т", 0, True, 0)
    db.add_delivery_method("Минск", "Доставка", True, "Адрес", "", 5.0, True, 1)
    методы = db.get_delivery_methods("Минск")
    самовывоз = next(m["id"] for m in методы if m["name"] == "Самовывоз")
    доставка = next(m["id"] for m in методы if m["name"] == "Доставка")
    pid = db.add_product("Минск", "pods", "Под", 30.0, 999)
    db.add_promo("СКИДКА10", "percent", 10, 0, None, False)
    db.set_age_ok(ПОКУПАТЕЛЬ)
    старый_порог = db.get_setting("free_delivery_from", 0)
    cache.bust()

    c = Checker("Названное, записанное и пересчитанное — одно и то же")
    расхождения = []
    заказов = 0
    try:
        for порог, метод, монеты, промо in itertools.product(
                (0, 50), (самовывоз, доставка), (False, True), ("", "СКИДКА10")):
            db.set_setting("free_delivery_from", порог)
            cache.bust()
            conn = db.connect(); cur = conn.cursor()
            cur.execute(db._q("UPDATE users SET coins = %s WHERE user_id = %s"), (500, ПОКУПАТЕЛЬ))
            conn.commit(); conn.close()

            as_user(ПОКУПАТЕЛЬ, "касса")
            тело = {"initData": "x", "city": "Минск", "delivery_method_id": метод,
                    "payment_method": "cash", "items": [{"id": pid, "qty": 2}],
                    "use_coins": монеты, "delivery_address": "ул. Тестовая, 1",
                    "phone": "+375291234567"}
            if промо:
                тело["promo_code"] = промо
            d = client.post("/api/order", json=тело).get_json()
            какой = f"порог {порог}, {'доставка' if метод == доставка else 'самовывоз'}, " \
                    f"монеты {'да' if монеты else 'нет'}, промо {'да' if промо else 'нет'}"
            if not d.get("ok"):
                расхождения.append(f"{какой}: заказ не прошёл ({d.get('error')})")
                continue
            заказов += 1
            o = db.get_order(d["order_id"])
            свой = round(max(0.0, d["subtotal"] - d["discount"] - float(o["promo_discount"] or 0))
                         + d["fee"], 2)
            if not (round(d["total"], 2) == round(float(o["total"]), 2) == свой):
                расхождения.append(f"{какой}: назвали {d['total']}, в базе {o['total']}, "
                                   f"пересчёт {свой}")
            if монеты and d["discount"] != round(d["coins_used"] * shopinfo.COIN_VALUE, 2):
                расхождения.append(f"{какой}: скидка монетами не равна списанным монетам")

            as_admin()
            for действие in ("confirm", "issued"):
                client.post("/api/admin/order/status",
                            json={"initData": "x", "id": d["order_id"], "action": действие})

        c(f"все {заказов} сочетаний скидок сошлись"
          + ("" if not расхождения else ": " + "; ".join(расхождения[:3])), not расхождения)
        c("проверены все шестнадцать сочетаний", заказов == 16)

        # --- Порог бесплатной доставки должен и правда срабатывать ---
        # Без этой проверки предыдущая сошлась бы и на магазине, где порог
        # не работает вовсе: ноль равен нулю.
        c2 = Checker("Порог бесплатной доставки")
        db.set_setting("free_delivery_from", 0)
        cache.bust()
        as_user(ПОКУПАТЕЛЬ, "касса")
        платно = client.post("/api/order", json={
            "initData": "x", "city": "Минск", "delivery_method_id": доставка,
            "payment_method": "cash", "items": [{"id": pid, "qty": 2}],
            "delivery_address": "ул. Тестовая, 1", "phone": "+375291234567"}).get_json()
        c2("без порога доставка платная", платно.get("fee") == 5.0)

        db.set_setting("free_delivery_from", 50)
        cache.bust()
        даром = client.post("/api/order", json={
            "initData": "x", "city": "Минск", "delivery_method_id": доставка,
            "payment_method": "cash", "items": [{"id": pid, "qty": 2}],
            "delivery_address": "ул. Тестовая, 1", "phone": "+375291234567"}).get_json()
        c2("выше порога доставка бесплатна", даром.get("fee") == 0)
        c2("и в итог она не попала", даром.get("total") == даром.get("subtotal"))

        as_admin()
        for d in (платно, даром):
            for действие in ("confirm", "issued"):
                client.post("/api/admin/order/status",
                            json={"initData": "x", "id": d["order_id"], "action": действие})

        # --- Итог по кассе против статистики ---
        c3 = Checker("Итог по кассе против статистики")
        conn = db.connect(); cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(total), 0) AS s, COUNT(*) AS n FROM orders WHERE status = 'issued'")
        r = cur.fetchone()
        conn.close()
        по_базе, выдано = round(float(r["s"]), 2), int(r["n"])
        статистика = db.get_business_stats(30)
        c3("выручка в статистике равна сумме выданных заказов",
           abs(по_базе - round(статистика["revenue"], 2)) < 0.01)
        c3("и число заказов тоже", статистика["orders"] == выдано)
        c3("средний чек — это выручка, делённая на заказы",
           abs(статистика["avg_check"] - (по_базе / выдано)) < 0.01)
    finally:
        db.set_setting("free_delivery_from", старый_порог)
        as_admin()
        _clean()

    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
