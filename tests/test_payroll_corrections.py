"""Зарплата: выплата только за закончившийся месяц и перерасчёт после неё (приёмка DAY-02).

Ревьюер отметил выплату посреди месяца (2 Br), потом продал ещё — доплатить
было нельзя («already_paid»); отменил обе продажи — начислено 0, выплачено 2,
и разницы не видно нигде.

Решение владельца (7.10.2026):
  • выплату отмечают только за закончившийся месяц;
  • отменили продажу уже выплаченного месяца — выплату не трогаем (деньги у
    человека), а разница переходит в ближайшую выплату отдельной строкой.

Проверяется весь путь: отказ за идущий месяц, выплата, повтор без дубля,
поздняя отмена, перерасчёт в следующей выплате, переплата больше начисленного,
одна запись в журнале на выплату и гонка двух выплат одному продавцу.
"""
import json
import threading

from _common import db, client, Checker, real_auth, as_admin, REAL_GET_USER
from partut import cache
from partut import config
from partut.db import reports
from partut.web import auth

CITY = "Перерасчёт"
OWNER = 7811
SELLER = 7801


def _as(uid):
    real_auth()
    auth.get_user = lambda init: {"id": uid, "username": f"u{uid}"}


def _post(path, **body):
    return client.post(path, json={"initData": "x", **body})


def _месяц(k):
    """Период k месяцев назад и середина этого месяца строкой."""
    d = db.shop_now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    y, m = d.year, d.month - k
    while m < 1:
        m += 12
        y -= 1
    d = d.replace(year=y, month=m)
    return d.strftime("%Y-%m"), d.strftime("%Y-%m-%d %H:%M")


def _продажа(pid, цена, k):
    """Продажа на точке, перенесённая на k месяцев назад."""
    oid, _, _ = db.record_point_sale(CITY, [{"id": pid, "qty": 1, "price": цена}], OWNER, "owner", "cash", "")
    if k:
        _, когда = _месяц(k)
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE orders SET created_at = %s, issued_at = %s WHERE id = %s"), (когда, когда, oid))
        conn.commit(); conn.close()
    return oid


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM orders WHERE city = %s"), (CITY,))
    cur.execute(db._q("DELETE FROM products WHERE city = %s"), (CITY,))
    cur.execute(db._q("DELETE FROM seller_payouts WHERE city = %s"), (CITY,))
    cur.execute(db._q("DELETE FROM admin_log WHERE action = %s"), ("payroll/pay",))
    conn.commit(); conn.close()
    cache.bust()


def _продавец(period):
    d = _post("/api/admin/payroll", period=period).get_json()
    строка = next(r for r in d["rows"] if r["city"] == CITY)
    return d, строка, строка["sellers"][0]


def run():
    _clean()
    старые_владельцы = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = старые_владельцы | {OWNER}
    db.add_staff(SELLER, CITY, "Лена")
    config.refresh_staff()
    db.set_setting("seller_commission_percent", "10")
    pid = db.add_product(CITY, "accessories", "Кабель", 10.0, 50)
    cache.bust()
    M0 = _месяц(0)[0]
    M1, M2, M3 = _месяц(1)[0], _месяц(2)[0], _месяц(3)[0]
    try:
        _as(OWNER)
        c = Checker("Выплата: только за закончившийся месяц")
        _продажа(pid, 20.0, 0)
        r = _post("/api/admin/payroll/pay", period=M0, city=CITY, user_id=SELLER)
        c(f"идущий месяц: отказ month_open ({r.status_code} {r.get_json().get('error')})",
          r.status_code == 400 and r.get_json().get("error") == "month_open")
        c("и в базе ничего не записано", not db.seller_payouts_for_period(M0))
        d, _, s = _продавец(M0)
        c(f"экран идущего месяца: open, «с {d.get('pay_from')}», без суммы к выплате",
          d["open"] is True and d["pay_from"].startswith("1 ") and s["to_pay"] is None)

        c2 = Checker("Выплата за прошлый месяц, повтор без дубля, журнал")
        продажа_M2 = _продажа(pid, 20.0, 2)
        _продажа(pid, 50.0, 1)
        _продажа(pid, 30.0, 3)
        d, строка, s = _продавец(M2)
        c2(f"позапрошлый месяц: начислено 2.00, к выплате 2.00 ({строка['amount']}, {s['to_pay']})",
           строка["amount"] == 2.0 and s["to_pay"] == 2.0 and not s["settle"])
        r = _post("/api/admin/payroll/pay", period=M2, city=CITY, user_id=SELLER)
        c2(f"выплата принята: {r.get_json()}", r.status_code == 200 and r.get_json()["amount"] == 2.0)
        r = _post("/api/admin/payroll/pay", period=M2, city=CITY, user_id=SELLER)
        c2(f"повтор — already_paid с понятным текстом ({r.get_json().get('message')})",
           r.status_code == 400 and r.get_json().get("error") == "already_paid"
           and "уже отмечено" in r.get_json().get("message", ""))
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("SELECT COUNT(*) AS n FROM seller_payouts WHERE user_id = %s AND period = %s"), (SELLER, M2))
        записей = int(cur.fetchone()["n"])
        cur.execute(db._q("SELECT details FROM admin_log WHERE action = %s"), ("payroll/pay",))
        журнал = [r["details"] for r in cur.fetchall()]
        conn.close()
        c2(f"запись о выплате одна ({записей})", записей == 1)
        c2(f"в журнале одна строка, по-человечески: {журнал}",
           len(журнал) == 1 and "зарплата за" in журнал[0] and "Лена (7801)" in журнал[0] and "2.00 Br" in журнал[0])

        c3 = Checker("Поздняя отмена продажи выплаченного месяца")
        r = _post("/api/admin/sale/cancel", id=продажа_M2)
        c3(f"владелец отменил продажу позапрошлого месяца: {r.get_json()}",
           r.status_code == 200 and "уже отмечена" in (r.get_json().get("note") or ""))
        _, _, s = _продавец(M2)
        п = s["paid"]
        c3(f"выплата не тронута: 2.00 ({п['amount']})", п["amount"] == 2.0)
        c3(f"видно, что изменилось: сейчас 0.00 вместо 2.00, разница −2.00, ждёт выплаты ({п['now']}, {п['diff']}, {п['left']})",
           п["now"] == 0.0 and п["diff"] == -2.0 and п["left"] == -2.0 and not п["settled"])
        _, _, s = _продавец(M0)
        c3(f"идущий месяц показывает перерасчёт как «учтётся при выплате» ({s['carry']})",
           [x["period"] for x in s["carry"]] == [M2] and s["carry"][0]["amount"] == -2.0 and s["to_pay"] is None)
        _, строка, s = _продавец(M1)
        c3(f"прошлый месяц: начислено 5.00, перерасчёт −2.00, к выплате 3.00 ({строка['amount']}, {s['settle']}, {s['to_pay']})",
           строка["amount"] == 5.0 and s["to_pay"] == 3.0 and [(x["period"], x["amount"]) for x in s["settle"]] == [(M2, -2.0)])

        c4 = Checker("Следующая выплата закрывает разницу один раз")
        r = _post("/api/admin/payroll/pay", period=M1, city=CITY, user_id=SELLER)
        j = r.get_json()
        c4(f"выплачено 3.00: начислено 5.00 и перерасчёт −2.00 ({j})",
           r.status_code == 200 and j["amount"] == 3.0 and j["base"] == 5.0 and j["corrections"] == [{"period": M2, "amount": -2.0}])
        _, _, s = _продавец(M2)
        c4(f"позапрошлый: разница учтена в выплате прошлого месяца ({s['paid']['settled']}, осталось {s['paid']['left']})",
           s["paid"]["left"] == 0.0 and [(x["period"], x["amount"]) for x in s["paid"]["settled"]] == [(M1, -2.0)])
        _, _, s = _продавец(M1)
        c4(f"прошлый: в выплате видна строка перерасчёта ({s['paid']['corrections']})",
           s["paid"]["base"] == 5.0 and [(x["period"], x["amount"]) for x in s["paid"]["corrections"]] == [(M2, -2.0)])
        _, _, s = _продавец(M0)
        c4("идущему месяцу переносить больше нечего", s["carry"] == [])

        c5 = Checker("Переплата больше начисленного")
        # Отменяем продажу прошлого месяца (50): переплата 5.00, а за третий
        # месяц назад начислено всего 3.00 — выплата 0, остаток −2.00 ждёт дальше.
        прошлого = [o for o in db.point_sales(CITY, _месяц(1)[1][:10]) if o["status"] == "issued"]
        r = _post("/api/admin/sale/cancel", id=прошлого[0]["id"])
        c5("продажу прошлого месяца отменили", r.status_code == 200)
        _, строка, s = _продавец(M3)
        c5(f"три месяца назад: начислено 3.00, к выплате 0, закрывается −3.00 ({строка['amount']}, {s['to_pay']}, {s['settle']})",
           строка["amount"] == 3.0 and s["to_pay"] == 0.0 and [(x["period"], x["amount"]) for x in s["settle"]] == [(M1, -3.0)])
        r = _post("/api/admin/payroll/pay", period=M3, city=CITY, user_id=SELLER)
        c5(f"«закрыть месяц» с выплатой 0 ({r.get_json()})", r.status_code == 200 and r.get_json()["amount"] == 0.0)
        _, _, s = _продавец(M0)
        c5(f"остаток переплаты −2.00 ждёт следующей выплаты ({s['carry']})",
           [(x["period"], x["amount"]) for x in s["carry"]] == [(M1, -2.0)])

        c6 = Checker("Зачёт — чистая функция")
        c6("всё сходится: 5 − 2 = 3, закрыто всё", reports._зачесть(5.0, {"a": -2.0}) == (3.0, {"a": -2.0}))
        c6("переплата больше: 1 и −2 → 0, закрыто −1", reports._зачесть(1.0, {"a": -2.0}) == (0.0, {"a": -1.0}))
        c6("начислено 0, переплата −2 → 0, ничего не закрыто", reports._зачесть(0.0, {"a": -2.0}) == (0.0, {}))
        c6("недоплата закрывается целиком, переплата — старые первыми",
           reports._зачесть(1.0, {"b": 1.0, "a": -5.0, "c": -1.0}) == (0.0, {"b": 1.0, "a": -2.0}))

        c7 = Checker("Продавец видит свой перерасчёт, но не отмечает выплату")
        _as(SELLER)
        d = _post("/api/admin/payroll", period=M0).get_json()
        мой = d["rows"][0]["sellers"]
        c7(f"видит только себя и свой перерасчёт ({мой})",
           len(мой) == 1 and мой[0]["user_id"] == SELLER and [x["period"] for x in мой[0]["carry"]] == [M1])
        r = _post("/api/admin/payroll/pay", period=M1, city=CITY, user_id=SELLER)
        c7(f"отметить выплату сам себе не может ({r.status_code})", r.status_code == 403)

        c8 = Checker("Две выплаты одному продавцу одновременно")
        # Переплата −2.00 (за прошлый месяц) не закрыта. Шесть и семь месяцев
        # назад — по продаже, обе не выплачены. Платим за оба месяца разом:
        # без замка обе выплаты увидели бы одну и ту же переплату и вычли её
        # дважды. Замок заставляет вторую ждать первую.
        _as(OWNER)
        M6, M7 = _месяц(6)[0], _месяц(7)[0]
        _продажа(pid, 40.0, 6)
        _продажа(pid, 40.0, 7)
        настоящий = reports._перерасчёт
        посчитал_второй = threading.Event()
        второй_запущен, ответы, потоки = [], {}, []

        def платить(период):
            try:
                ответы[период] = db.pay_seller(SELLER, CITY, период, OWNER)
            except Exception as e:      # PayrollRefused и всё прочее — в ответ, тест разберёт
                ответы[период] = e
            finally:
                db.вернуть_забытые()

        def с_задержкой(cur, uid, кэш):
            итог = настоящий(cur, uid, кэш)
            имя = threading.current_thread().name
            if имя == "первая" and not второй_запущен:
                второй_запущен.append(1)
                t = threading.Thread(target=платить, args=(M6,), name="вторая")
                потоки.append(t)
                t.start()
                посчитал_второй.wait(2.0)      # с замком вторая ждёт — сюда не дойдёт
            elif имя == "вторая":
                посчитал_второй.set()
            return итог

        reports._перерасчёт = с_задержкой
        try:
            первая = threading.Thread(target=платить, args=(M7,), name="первая")
            первая.start()
            первая.join(15)
            for t in потоки:
                t.join(15)
        finally:
            reports._перерасчёт = настоящий
        c8(f"обе выплаты прошли ({ответы})", all(isinstance(ответы.get(п), dict) for п in (M6, M7)))
        закрыто = sum(json.loads(в.get("corrections") or "{}").get(M1, 0.0)
                      for п in (M3, M6, M7) for в in db.seller_payouts_for_period(п).values() if в["city"] == CITY)
        # −3.00 закрыла выплата трёх месяцев назад, ещё −2.00 — одна из двух
        # новых. Вычти обе — вышло бы −7.00.
        c8(f"переплату за прошлый месяц закрыли один раз: всего −5.00, а не −7.00 ({закрыто:.2f})",
           abs(закрыто - (-5.0)) < 0.001)
        суммы = sorted(ответы[п]["amount"] for п in (M6, M7) if isinstance(ответы.get(п), dict))
        c8(f"одна выплата 2.00 (4 − 2), другая 4.00 ({суммы})", суммы == [2.0, 4.0])
    finally:
        config.SUPER_ADMIN_IDS = старые_владельцы
        db.remove_staff(SELLER)
        config.refresh_staff()
        auth.get_user = REAL_GET_USER
        as_admin()
        _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails + c7.fails + c8.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
