"""Щедрость программы: колесо от суммы, летопись монет, настройки.

Три связанные вещи.

1. Прокрут колеса раньше давали за 5 купленных ШТУК, без оглядки на цену. Пять
   одноразок по 8 Br и пять подов по 30 Br приносили один и тот же средний приз:
   8.6% от заказа против 2.3%. Магазин доплачивал тем, кто меньше тратит. Теперь
   прокрут даётся за потраченные рубли.

2. Накопленный людьми прогресс при переходе нельзя терять, поэтому он
   пересчитывается в той же доле: 3 штуки из 5 — это 60% пути, значит 60 Br
   из 100.

3. «Роздано за месяц» по балансам не посчитать: потраченные монеты на балансах
   уже не лежат, и раздача выглядела бы меньше, чем есть. Поэтому каждое
   движение пишется в летопись.
"""
from _common import db, client, Checker, as_user, as_admin

import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "coin_log"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM users WHERE user_id BETWEEN %s AND %s"), (8700, 8799))
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    cache.bust()


UID = 8701


def run():
    _clean()
    db.set_setting("wheel_step", 100)
    db.set_setting("coins_per_byn", 1)
    db.set_setting("referral_bonus", 50)
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    cheap = db.add_product("Минск", "disposable", "Дешёвый", 8.0, 99)
    db.set_age_ok(UID)
    cache.bust()

    def buy(pid, qty):
        as_user(UID, "u")
        d = client.post("/api/order", json={"initData": "x", "city": "Минск",
                                            "delivery_method_id": mid,
                                            "payment_method": "cash",
                                            "items": [{"id": pid, "qty": qty}]}).get_json()
        as_admin()
        client.post("/api/admin/order/status", json={"initData": "x", "id": d["order_id"], "action": "confirm"})
        client.post("/api/admin/order/status", json={"initData": "x", "id": d["order_id"], "action": "issued"})
        return d

    c = Checker("Колесо считает рубли, а не штуки")
    buy(cheap, 5)                       # 5 штук по 8 Br = 40 Br
    w = db.get_wheel(UID)
    c("шаг колеса измеряется в Br", w["step"] == 100)
    c("пять дешёвых штук прокрут НЕ дают", w["spins"] == 0)
    c("зато копится сумма покупок", w["progress"] == 40)

    buy(cheap, 8)                       # ещё 64 Br → всего 104 Br
    w = db.get_wheel(UID)
    c("на сотне рублей прокрут появился", w["spins"] == 1)
    c("остаток пути сохранён", w["progress"] == 4)

    # --- Летопись монет ---
    c2 = Checker("Летопись монет")
    flow = db.coin_flow()
    c2("кэшбэк записан", any(r["reason"] == "cashback" and r["granted"] > 0 for r in flow["by_reason"]))
    c2("роздано больше нуля", flow["granted"] > 0)
    c2("роздано совпадает с балансом (ничего не тратили)",
       flow["granted"] - flow["spent"] == db.get_coins(UID))

    # Оплата монетами тоже движение — и она обязана попасть в «списано».
    spent_before = db.coin_flow()["spent"]
    as_user(UID, "u")
    client.post("/api/order", json={"initData": "x", "city": "Минск",
                                    "delivery_method_id": mid, "payment_method": "cash",
                                    "use_coins": True, "items": [{"id": cheap, "qty": 1}]})
    as_admin()
    c2("оплата монетами попала в «списано»", db.coin_flow()["spent"] > spent_before)
    c2("и подписана как оплата заказа",
       any(r["reason"] == "order" and r["spent"] > 0 for r in db.coin_flow()["by_reason"]))

    # --- Настройки ---
    c3 = Checker("Щедрость меняется из админки")
    r = client.post("/api/admin/settings/update",
                    json={"initData": "x", "coins_per_byn": 2, "wheel_step": 50, "referral_bonus": 10})
    c3("настройки сохранились", r.get_json().get("ok") is True)
    c3("кэшбэк применился", db.coins_per_byn() == 2)
    c3("шаг колеса применился", db.wheel_step() == 50)
    c3("бонус за друга применился", db.referral_bonus() == 10)

    st = client.post("/api/admin/settings", json={"initData": "x"}).get_json()["settings"]
    c3("админке видны текущие значения", st["wheel_step"] == 50 and st["coins_per_byn"] == 2)

    # Незаполненная настройка обязана показывать то, чем магазин и живёт, а не
    # пустоту: пустое поле владелец сохранит — и сотрёт реквизиты по-настоящему.
    # Экран собирает все настройки одним запросом, и на этом однажды потерялись
    # значения по умолчанию.
    import config
    conn = db.connect(); cur = conn.cursor()
    for ключ in ("payment_info", "confirm_minutes", "wheel_step", "referral_bonus",
                 "coins_per_byn", "compensation_max"):
        cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (ключ,))
    conn.commit(); conn.close()
    st = client.post("/api/admin/settings", json={"initData": "x"}).get_json()["settings"]
    c3("реквизиты берутся из настроек магазина", st["payment_info"] == config.PAYMENT_INFO)
    c3("срок подтверждения не пустой", st["confirm_minutes"] > 0)
    c3("шаг колеса не пустой", st["wheel_step"] > 0)
    c3("бонус за друга не пустой", st["referral_bonus"] > 0)
    c3("кэшбэк не пустой", st["coins_per_byn"] > 0)
    c3("потолок компенсации не пустой", st["compensation_max"] > 0)

    # Границы: ноль в шаге колеса означал бы прокрут за каждую покупку.
    client.post("/api/admin/settings/update", json={"initData": "x", "wheel_step": 0})
    c3("нулевой шаг не принимается", db.wheel_step() >= 1)
    client.post("/api/admin/settings/update", json={"initData": "x", "coins_per_byn": 9999})
    c3("кэшбэк ограничен сверху", db.coins_per_byn() <= 10)
    client.post("/api/admin/settings/update", json={"initData": "x", "coins_per_byn": -5})
    c3("отрицательный кэшбэк не принимается", db.coins_per_byn() >= 0)

    # --- Перенос старого прогресса ---
    c4 = Checker("Перенос прогресса со штук на рубли")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET wheel_progress = 3 WHERE user_id = %s"), (UID,))
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), ("wheel_progress_in_money",))
    conn.commit(); conn.close()
    db._migrate_wheel_progress_to_money()
    c4("3 штуки из 5 стали 60 Br из 100", db.get_wheel(UID)["progress"] == 60)
    db._migrate_wheel_progress_to_money()
    c4("повторный запуск ничего не удваивает", db.get_wheel(UID)["progress"] == 60)

    db.set_setting("wheel_step", 100)
    db.set_setting("coins_per_byn", 1)
    db.set_setting("referral_bonus", 50)
    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
