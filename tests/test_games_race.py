"""Колесо и слоты при быстрых нажатиях.

Найдено обстрелом на гонки. У колеса было две разные реализации: для Postgres
условие «прокрут есть» стояло внутри UPDATE (правильно), а для SQLite сначала
читался остаток, потом писался новый. Пять одновременных нажатий проходили
втроём: счётчик прокрутов уходил в минус, а монеты начислялись за прокруты,
которых не было. То же самое было у слотов со ставкой.

Монеты — это скидка в заказе, то есть деньги магазина. Тест обязателен к
прогону на ОБЕИХ базах: ошибка жила ровно в той половине, которую вторая база
не проверяет.
"""
import threading

from _common import db, client, server, Checker, as_user, as_admin

UID = 8551


def _parallel(fn, n=5):
    out = []
    threads = [threading.Thread(target=lambda: out.append(fn())) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _set_coins(n):
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET coins = %s WHERE user_id = %s"), (n, UID))
    conn.commit(); conn.close()


def run():
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (UID,))
    conn.commit(); conn.close()
    db.ensure_user(UID)
    db.set_age_ok(UID)
    as_user(UID, "gamer")

    # --- Колесо ---
    c = Checker("Колесо: один прокрут — один приз")
    c("без прокрутов колесо не крутится",
      client.post("/api/wheel/spin", json={"initData": "x"}).get_json().get("error") == "no_spins")

    db.add_spins(UID, 1)
    before = db.get_coins(UID)
    res = _parallel(lambda: client.post("/api/wheel/spin", json={"initData": "x"}).get_json())
    ok = [r for r in res if r.get("ok")]
    c("из пяти одновременных нажатий прошло ровно одно", len(ok) == 1)
    c("остальным честно отказано",
      all(r.get("error") == "no_spins" for r in res if not r.get("ok")))
    c("счётчик прокрутов не ушёл в минус", db.get_user_row(UID)["wheel_spins"] == 0)
    # Приз мог быть и нулевым — важно, что он начислен не больше одного раза.
    c("монеты начислены не больше чем за один прокрут",
      db.get_coins(UID) - before == (ok[0].get("coins", 0) if ok else 0))

    # Три прокрута — три прокрута, не больше.
    db.add_spins(UID, 3)
    res = _parallel(lambda: client.post("/api/wheel/spin", json={"initData": "x"}), 8)
    c("при трёх прокрутах проходит ровно три",
      sum(1 for r in res if r.get_json().get("ok")) == 3)
    c("и счётчик снова ноль", db.get_user_row(UID)["wheel_spins"] == 0)

    # --- Слоты ---
    c2 = Checker("Слоты: ставка не уходит в минус")
    _set_coins(10)
    _parallel(lambda: client.post("/api/slot/spin", json={"initData": "x", "bet": 10}).get_json())
    c2("баланс не ушёл в минус", db.get_coins(UID) >= 0)

    _set_coins(0)
    r = client.post("/api/slot/spin", json={"initData": "x", "bet": 10}).get_json()
    c2("без монет ставку не сделать", not r.get("ok"))
    c2("и баланс остался нулевым", db.get_coins(UID) == 0)

    c3 = Checker("Слоты: кривая ставка")
    _set_coins(200)
    for bet in (-50, 0, 10 ** 9):
        r = client.post("/api/slot/spin", json={"initData": "x", "bet": bet})
        c3(f"ставка {bet} отклонена", r.status_code == 400)
    c3("баланс от кривых ставок не пострадал", db.get_coins(UID) == 200)

    as_admin()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (UID,))
    conn.commit(); conn.close()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
