"""Точка закрыта на время — продавца нет на месте.

Решение владельца (2.10.2026): продавец закрывает точку «на час», «до 18:00»
или «пока не открою»; покупатели видят каталог и «закрыта до …», но заказ
не оформляется. Время вышло — точка открывается сама, без чьих-либо рук:
оформление сверяет время открытия с часами, а фоновый шаг лишь снимает
пометку и говорит об этом продавцам и владельцу.
"""
import datetime
import time

from _common import db, client, Checker, as_user, as_admin, SENT, reset_sent

from partut import cache, config
from partut.bot import handlers as botmod


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "product_variants"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    cur.execute("UPDATE locations SET closed = 0, closed_until = NULL, closed_note = NULL, closed_by = NULL")
    conn.commit(); conn.close()
    cache.bust()


def _пауза(**тело):
    return client.post("/api/admin/pause", json={"initData": "x", **тело})


def _точки():
    return {x["name"]: x for x in client.get("/api/locations").get_json()}


def run():
    c = Checker("Точка закрыта на время")
    _clean()
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    метод = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "disposable", "Под", 10.0, 5)
    db.set_age_ok(9501)
    db.add_staff(9502, "Минск", "продавец Минска")
    config.refresh_staff()

    def заказать():
        as_user(9501, "buyer")
        return client.post("/api/order", json={"initData": "x", "city": "Минск", "delivery_method_id": метод,
                                               "payment_method": "cash", "items": [{"id": pid, "qty": 1}]})

    try:
        # --- Продавец закрывает свою точку на час ---
        as_admin(uid=9502, username="продавец", role="staff", city="Минск")
        reset_sent()
        # Ожидание — из часов до и после запроса, а слова — по тому же правилу,
        # что у сервера: после 23:00 «на час» — это «до 4.10 00:30», и сравнение
        # с голым «до 00:30» ложно падало бы ночью (как и смена минуты посреди).
        было = db.shop_now().replace(second=0, microsecond=0)
        r = _пауза(minutes=60, note="ушёл на обед")
        стало = db.shop_now().replace(second=0, microsecond=0)
        d = r.get_json() or {}
        срок = (d.get("closed") or {}).get("until") or ""
        c(f"закрыта на час: {d}", r.status_code == 200 and срок in {
            (было + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M"),
            (стало + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M")})
        до = db.пауза_словами({"until": срок})[3:]
        c(f"подпись «{d['closed']['words']}» — по сроку", d["closed"]["words"] == f"до {до}")
        c("владельцу сказано, кто закрыл и до скольки",
          any(int(s[0]) in config.SUPER_ADMIN_IDS and f"закрыта до {до}" in s[1] and "ушёл на обед" in s[1] for s in SENT))
        к = _точки()["Минск"]["closed"]
        c(f"покупатель видит паузу в списке точек: {к}", к and к["words"] == f"до {до}" and к["note"] == "ушёл на обед"
          and abs(к["until_ms"] / 1000 - (time.time() + 3600)) < 120)
        r = заказать()
        d = r.get_json() or {}
        c(f"заказ на закрытую точку — отказ с временем открытия: {d.get('message')!r}",
          r.status_code == 409 and d["error"] == "point_closed" and f"до {до}" in d["message"] and "корзина сохранится" in d["message"])
        c("и ничего не списано", db.get_product(pid)["stock"] == 5)

        # Чужую точку продавец не закроет.
        c("чужую точку — отказ 403, а не молча своя", _пауза(city="Туров", minutes=30).status_code == 403
          and not db.location_pause("Туров"))

        # --- Открыть раньше ---
        reset_sent()
        r = client.post("/api/admin/pause/open", json={"initData": "x"})
        c("открыл раньше времени", (r.get_json() or {}).get("was_closed") is True and db.location_pause("Минск") is None)
        c("владельцу сказано, что открыта", any("снова открыта" in s[1] for s in SENT))
        c("заказ снова проходит", заказать().status_code == 200)

        # --- Владелец: «до 10:00» вечером — это завтра; «пока не открою» ---
        as_admin()
        # Часы — управляемые (приёмка 07d3ed5): проверка «час назад» по
        # настоящим часам между 00:00 и 00:59 давала 23:xx — ещё сегодня, —
        # и тест ложно падал по ночам. Днём и сразу после полуночи — отдельно.
        настоящие_часы = db.shop_now
        случаи = [
            ("12:30", "11:30", "2026-10-05 11:30", "днём: «до 11:30» уже прошло — значит завтра"),
            ("12:30", "13:00", "2026-10-04 13:00", "днём: «до 13:00» ещё впереди — сегодня"),
            ("00:30", "23:30", "2026-10-04 23:30", "после полуночи: «до 23:30» — ещё сегодня"),
            ("00:30", "00:10", "2026-10-05 00:10", "после полуночи: «до 00:10» уже прошло — завтра"),
            ("23:50", "00:15", "2026-10-05 00:15", "перед полуночью: «до 00:15» — завтра"),
        ]
        try:
            for сейчас, до, ждём, что in случаи:
                ч, м = (int(x) for x in сейчас.split(":"))
                db.shop_now = lambda ч=ч, м=м: datetime.datetime(2026, 10, 4, ч, м, 37)
                d = _пауза(city="Минск", at=до).get_json()
                c(f"{что}: {d['closed']['until']}", d["closed"]["until"] == ждём)
        finally:
            db.shop_now = настоящие_часы
        c("кривое время — отказ", _пауза(city="Минск", at="25 часов").status_code == 400)
        d = _пауза(city="Минск").get_json()
        c("без времени — «пока не откроют»", d["closed"]["until"] == "" and d["closed"]["words"] == "пока не откроют")
        c("в списке точек без времени", _точки()["Минск"]["closed"]["until_ms"] is None)
        список = client.post("/api/admin/pauses", json={"initData": "x"}).get_json()["points"]
        c("в «Управлении» видно, какая точка закрыта", any(x["city"] == "Минск" and x["closed"] for x in список))

        # --- Время вышло: открыта сама, даже если фон ещё не прошёл ---
        conn = db.connect(); cur = conn.cursor()
        былo = (db.shop_now() - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        cur.execute(db._q("UPDATE locations SET closed = 1, closed_until = %s WHERE name = 'Минск'"), (былo,))
        conn.commit(); conn.close()
        cache.bust()
        c("время вышло — заказ проходит сразу, без фона", заказать().status_code == 200)
        c("и в списке точек паузы уже нет", _точки()["Минск"]["closed"] is None)
        reset_sent()
        открыты = botmod._reopen_paused_points()
        c("фон снял пометку", [r["name"] for r in открыты] == ["Минск"])
        c("и сказал продавцу точки и владельцу",
          any(int(s[0]) == 9502 for s in SENT) and any(int(s[0]) in config.SUPER_ADMIN_IDS for s in SENT))
        reset_sent()
        c("второй проход ничего не повторяет", botmod._reopen_paused_points() == [] and not SENT)
    finally:
        as_admin()
        db.remove_staff(9502)
        config.refresh_staff()
        _clean()
    return c.fails
