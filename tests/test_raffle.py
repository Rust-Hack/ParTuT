"""Розыгрыш: честность при одновременных нажатиях.

Целая часть магазина, которая раздаёт призы, и до сих пор она не была покрыта
ничем. Обстрел нашёл две дыры, и обе открывались одним и тем же — несколькими
нажатиями разом.

«Участвую» сначала спрашивало «уже участвует?», потом вставляло билет: пять
нажатий давали одному человеку пять билетов и впятеро больше шансов, а заодно
позволяли ему занять все три места сразу.

Розыгрыш запускается лениво — тем, кто первым откроет вкладку после срока. В
час пик таких «первых» несколько, и каждый проводил розыгрыш заново: пять
поздравлений одному человеку, монеты за третье место по разу от каждого, а на
месте одного розыгрыша — пачка активных.
"""
import datetime
import json
import threading

from _common import (db, client, server, Checker, as_user, as_admin, deny_admin,
                     SENT, reset_sent)

UIDS = [8901, 8902, 8903, 8904]


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("raffles", "raffle_entries", "orders"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM users WHERE user_id BETWEEN %s AND %s"), (8900, 8999))
    conn.commit(); conn.close()


def _spend(uid, amount):
    """Выданный заказ: только он считается в порог участия."""
    oid = db.create_order(uid, f"u{uid}", "Минск",
                          [{"product_id": 1, "name": "т", "price": amount, "qty": 1}], amount, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'issued' WHERE id = %s"), (oid,))
    conn.commit(); conn.close()


def _expire(rid):
    вчера = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE raffles SET ends_at = %s WHERE id = %s"), (вчера, rid))
    conn.commit(); conn.close()


def _states():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT status AS st, COUNT(*) AS n FROM raffles GROUP BY status")
    out = {r["st"]: int(r["n"]) for r in cur.fetchall()}
    conn.close()
    return out


def _parallel(fn, n=5):
    out = []
    threads = [threading.Thread(target=lambda: out.append(fn())) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def run():
    _clean()
    for uid in UIDS:
        db.ensure_user(uid)
        db.set_age_ok(uid)

    # --- Порог участия ---
    c = Checker("Кого пускают в розыгрыш")
    rid = db.create_raffle(threshold=25, prize3_coins=500, days=30)
    as_user(UIDS[0], "бедный")
    r = client.post("/api/raffle/join", json={"initData": "x"})
    c("не набравшему порог отказано", r.get_json().get("error") == "not_eligible")
    c("и билета у него нет", db.count_entries(rid) == 0)

    _spend(UIDS[0], 30.0)
    r = client.post("/api/raffle/join", json={"initData": "x"}).get_json()
    c("набравшему порог — билет", r.get("entered") is True and db.count_entries(rid) == 1)

    st = client.post("/api/raffle", json={"initData": "x"}).get_json()["raffle"]
    c("приложение видит, что человек участвует", st["entered"] is True)
    c("и сколько он потратил", st["spent"] == 30.0)

    # --- Пять нажатий разом ---
    c2 = Checker("Пять нажатий «Участвую» разом")
    _clean()
    for uid in UIDS:
        db.ensure_user(uid); db.set_age_ok(uid)
    rid = db.create_raffle(threshold=10, prize3_coins=500, days=30)
    _spend(UIDS[0], 50.0)
    as_user(UIDS[0], "быстрый")
    _parallel(lambda: client.post("/api/raffle/join", json={"initData": "x"}).get_json())
    c2("билет всё равно один", db.count_entries(rid) == 1)

    # --- Один человек не берёт несколько мест ---
    c3 = Checker("Одно место в одни руки")
    # Ключ в базе второй билет уже не даст, но в розыгрышах, начатых ДО него,
    # дубли могли накопиться. Забрать все три места такой участник не должен.
    настоящие = db.get_raffle_user_ids
    db.get_raffle_user_ids = lambda _rid: [UIDS[0], UIDS[0], UIDS[0]]
    try:
        reset_sent()
        server._draw_raffle(db.get_active_raffle())
    finally:
        db.get_raffle_user_ids = настоящие
    winners = json.loads(db.get_last_finished_raffle()["winners"] or "[]")
    c3("победитель один, а не три", len(winners) == 1)
    c3("места не достались одному человеку дважды",
       len({w["user_id"] for w in winners}) == len(winners))
    c3("и поздравление ушло один раз",
       len([x for x in SENT if "место в розыгрыше" in str(x[1])]) == 1)

    # --- Старые дубли разводит выкладка ---
    c31 = Checker("Выкладка наводит порядок в старых дублях")
    _clean()
    rid = db.create_raffle(threshold=0, days=30)
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS raffle_entries_uniq")
    cur.execute("DROP INDEX IF EXISTS raffles_one_active")
    for _ in range(3):                      # как было до появления ключа
        cur.execute(db._q("INSERT INTO raffle_entries (raffle_id, user_id) VALUES (%s, %s)"),
                    (rid, UIDS[0]))
    conn.commit(); conn.close()
    лишний = db.create_raffle(threshold=0, days=30)   # второй активный — тоже наследие
    c31("до выкладки дублей трое", db.count_entries(rid) == 3)
    c31("и активных розыгрышей двое", _states().get("active") == 2)

    db._ensure_raffle_uniques()
    c31("билет остался один", db.count_entries(rid) == 1)
    c31("активный розыгрыш остался один", _states().get("active") == 1)
    c31("остался именно последний", int(db.get_active_raffle()["id"]) == int(лишний))
    c31("и ключ больше не даёт задвоить",
        db.add_raffle_entry(rid, UIDS[0]) is None and db.count_entries(rid) == 1)

    # --- Срок вышел, зашли разом ---
    c4 = Checker("Срок вышел, вкладку открыли пятеро")
    _clean()
    for uid in UIDS:
        db.ensure_user(uid); db.set_age_ok(uid)
    rid = db.create_raffle(threshold=0, prize3_coins=500, days=30)
    for uid in UIDS:
        db.add_raffle_entry(rid, uid)
    _expire(rid)
    монет_до = {uid: db.get_coins(uid) for uid in UIDS}
    reset_sent()

    def открыть(uid):
        as_user(uid, f"u{uid}")
        return client.post("/api/raffle", json={"initData": "x"}).get_json()

    потоки = [threading.Thread(target=открыть, args=(UIDS[i % len(UIDS)],)) for i in range(5)]
    for t in потоки:
        t.start()
    for t in потоки:
        t.join()

    состояния = _states()
    c4("розыгрыш проведён ровно один раз", состояния.get("finished") == 1)
    c4("и на смену пришёл ровно один новый", состояния.get("active") == 1)

    winners = json.loads(db.get_last_finished_raffle()["winners"] or "[]")
    c4("победителей трое", len(winners) == 3)
    c4("все разные", len({w["user_id"] for w in winners}) == 3)

    # Монеты за третье место — деньги магазина: начислить их пять раз нельзя.
    третий = next((w for w in winners if w["place"] == 3), None)
    c4("третье место разыграно", третий is not None)
    if третий:
        начислено = db.get_coins(третий["user_id"]) - монет_до[третий["user_id"]]
        c4("монеты за третье место начислены ровно один раз", начислено == 500)
    поздравления = [s for s in SENT if "место в розыгрыше" in str(s[1])]
    c4("каждому победителю написали по одному разу", len(поздравления) == 3)

    # --- Прошлый розыгрыш виден в приложении ---
    c5 = Checker("Итоги прошлого розыгрыша")
    as_user(UIDS[0], "смотрит")
    st = client.post("/api/raffle", json={"initData": "x"}).get_json()["raffle"]
    c5("прошлые победители показаны", len(st["last_winners"]) == 3)
    c5("новый розыгрыш начат с чистого листа", st["participants"] == 0 and st["entered"] is False)

    # --- Права ---
    c6 = Checker("Кто правит розыгрыш")
    # as_admin() подменяет проверку прав НАВСЕГДА, и без этого «посторонний»
    # оказался бы админом — тест на запрет проверял бы не то, что написано.
    deny_admin()
    r = client.post("/api/admin/raffle/update", json={"initData": "x", "prize3_coins": 999999})
    c6("покупателю править нельзя", r.status_code == 403)
    r = client.post("/api/admin/raffle/draw", json={"initData": "x"})
    c6("и разыграть досрочно тоже", r.status_code == 403)

    as_admin()
    client.post("/api/admin/raffle/update",
                json={"initData": "x", "title": "Августовский", "prize3_coins": 300, "threshold": 40})
    настройки = client.post("/api/admin/raffle", json={"initData": "x"}).get_json()["raffle"]
    c6("владелец меняет название", настройки["title"] == "Августовский")
    c6("и приз за третье место", настройки["prize3_coins"] == 300)
    c6("и порог участия", float(настройки["threshold"]) == 40.0)
    client.post("/api/admin/raffle/update", json={"initData": "x", "prize3_coins": -100})
    настройки = client.post("/api/admin/raffle", json={"initData": "x"}).get_json()["raffle"]
    c6("отрицательный приз не принимается", настройки["prize3_coins"] >= 0)

    было = _states().get("finished", 0)
    client.post("/api/admin/raffle/draw", json={"initData": "x"})
    c7 = Checker("Розыгрыш по кнопке владельца")
    c7("прошлый закрыт", _states().get("finished", 0) == было + 1)
    c7("и сразу начат следующий", _states().get("active") == 1)

    _clean()
    return c.fails + c2.fails + c3.fails + c31.fails + c4.fails + c5.fails + c6.fails + c7.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
