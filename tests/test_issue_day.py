"""День выдачи: «выдано сегодня», выручка и зарплата считают по нему (приёмка DAY-01).

Ревьюер оформил заказ вчера и выдал его сегодня настоящим запросом продавца —
сводка показала «0.00 Br выдано сегодня». Причина: всё считалось по дню
ОФОРМЛЕНИЯ. Решение владельца (7.10.2026): сводка дня, статистика, выгрузка
и зарплата — по дню ВЫДАЧИ. Время выдачи записывается с этого дня; у старых
заказов его нет, и для них остаётся дата оформления — выдумывать время, которого
никто не записал, нельзя.

Проверяется:
  • вчерашний заказ, выданный сегодня, — в сегодняшней выдаче ровно один раз;
  • выданный вчера — во вчерашней, открытые и отменённые — нигде;
  • старый заказ без времени выдачи считается по дню оформления;
  • граница месяца: оформлен в прошлом месяце, выдан в этом — зарплата этого;
  • статистика (карточка, график по дням, точки) и выгрузка — на том же правиле;
  • продажа на точке выдана в момент продажи и не считается покупателем.
"""
import datetime

from _common import db, client, Checker, as_admin
from partut import cache
from partut import export

CITY = "ДеньВыдачи"
SELLER = 7701


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute(db._q("DELETE FROM products WHERE city = %s"), (CITY,))
    cur.execute(db._q("DELETE FROM seller_payouts WHERE city = %s"), (CITY,))
    conn.commit(); conn.close()
    cache.bust()


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _order(total, status, created, issued=None, user=501):
    oid = db.create_order(user, "kl", CITY, [{"id": 1, "name": "Кабель", "price": total, "qty": 1}], total, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s, created_at = %s, issued_at = %s WHERE id = %s"),
                (status, created, issued, oid))
    conn.commit(); conn.close()
    return oid


def _today():
    return client.post("/api/admin/today", json={"initData": "x"}).get_json()["today"]


def run():
    c = Checker("День выдачи: сводка продавца")
    _clean()
    db.add_staff(SELLER, CITY, "Кира")
    сейчас = db.shop_now()
    вчера = сейчас - datetime.timedelta(days=1)
    сегодня = сейчас.strftime("%Y-%m-%d")

    # Как у ревьюера: заказ на кабель за 10 Br оформлен вчера, сегодня выдан
    # настоящим запросом продавца.
    as_admin(uid=SELLER, username="kira", role="seller", city=CITY)
    вчерашний = _order(10.0, "confirmed", _fmt(вчера))
    r = client.post("/api/admin/order/status", json={"initData": "x", "id": вчерашний, "action": "issued"})
    c(f"сервер принял выдачу: {r.status_code}", r.status_code == 200 and r.get_json().get("ok"))
    заказ = db.get_order(вчерашний)
    c(f"время выдачи записано сегодняшним ({заказ['issued_at']})", (заказ["issued_at"] or "").startswith(сегодня))
    c("дата оформления не тронута — вчерашняя", заказ["created_at"] == _fmt(вчера))

    _order(7.0, "issued", _fmt(вчера), _fmt(вчера))          # выдан вчера — не сегодняшний
    _order(30.0, "confirmed", _fmt(сейчас))                    # ждёт покупателя
    _order(40.0, "paid", _fmt(вчера))                          # ждёт продавца
    _order(50.0, "canceled", _fmt(сейчас))                     # отменён
    _order(3.0, "issued", _fmt(сейчас))                        # старый: время выдачи не записано, оформлен сегодня
    _order(4.0, "issued", _fmt(вчера))                         # старый: оформлен вчера — во вчерашнем

    t = _today()
    c(f"выдано сегодня: вчерашний заказ + старый сегодняшний = 2 ({t['issued_today']})", t["issued_today"] == 2)
    c(f"выручка сегодня 13.00 — 10 за вчерашний заказ и 3 за старый ({t['revenue_today']})",
      abs(t["revenue_today"] - 13.0) < 0.001)

    # Повторная выдача не проходит и ничего не удваивает.
    r = client.post("/api/admin/order/status", json={"initData": "x", "id": вчерашний, "action": "issued"})
    c("повторная выдача отклонена", r.status_code == 409)
    t = _today()
    c("и сегодняшняя выдача не удвоилась", t["issued_today"] == 2 and abs(t["revenue_today"] - 13.0) < 0.001)

    # Карточка заказа у продавца показывает, когда выдан.
    as_admin()
    заказы = client.post("/api/admin/orders", json={"initData": "x"}).get_json()["orders"]
    карточка = next(o for o in заказы if o["id"] == вчерашний)
    c("в карточке заказа есть время выдачи", (карточка.get("issued_at") or "").startswith(сегодня))

    c2 = Checker("День выдачи: статистика и выгрузка")
    s1 = db.get_business_stats(days=1)
    c2(f"«Выручка · сегодня» 13.00 ({s1['revenue']}), «Заказов» 2 ({s1['orders']})",
       abs(s1["revenue"] - 13.0) < 0.001 and s1["orders"] == 2)
    по_дням = {d["date"]: d["revenue"] for d in db.get_business_stats(days=7)["daily"]}
    c2(f"график: сегодня 13, вчера 11 (выдан вчера 7 + старый вчерашний 4) — {по_дням.get(сегодня)}, "
       f"{по_дням.get(вчера.strftime('%Y-%m-%d'))}",
       abs(по_дням.get(сегодня, 0) - 13.0) < 0.001 and abs(по_дням.get(вчера.strftime("%Y-%m-%d"), 0) - 11.0) < 0.001)
    точки = {x["city"]: x["total"] for x in s1["revenue_by_city"]}
    c2("выручка по точкам — та же сумма", abs(точки.get(CITY, 0) - 13.0) < 0.001)
    # Воронка — про оформленные заказы: вчерашний выданный в сегодняшней воронке не числится.
    c2(f"оформленные сегодня по статусам — по дню оформления ({s1['by_status']})",
       s1["by_status"].get("issued") == 1 and s1["by_status"].get("confirmed") == 1)

    строки = export.строки_заказов(db.orders_for_export(days=1), db.COIN_VALUE)
    выданные = [с for с in строки if с[3] == "Выдан"]
    сумма = sum(float(с[13].replace(",", ".")) for с in выданные)
    c2(f"выгрузка за сегодня: строки «Выдан» дают ту же выручку 13.00 ({сумма})", abs(сумма - 13.0) < 0.001)
    c2("последний столбец — когда выдан", export.ЗАГОЛОВКИ[-1] == "Выдан"
       and any(с[1] == вчерашний and с[-1].startswith(сегодня) for с in выданные))
    c2("у старого заказа время выдачи пустое, не выдуманное",
       any(с[-1] == "" and с[3] == "Выдан" for с in выданные))

    c3 = Checker("День выдачи: граница месяца в зарплате")
    _clean()
    первое = сейчас.replace(day=1, hour=0, minute=5)
    конец_прошлого = первое - datetime.timedelta(minutes=10)          # 23:55 последнего дня прошлого месяца
    прошлый = конец_прошлого.strftime("%Y-%m")
    этот = сейчас.strftime("%Y-%m")
    _order(100.0, "issued", _fmt(конец_прошлого), _fmt(первое))       # оформлен 31-го, выдан 1-го
    _order(20.0, "issued", _fmt(конец_прошлого), _fmt(конец_прошлого))
    _order(5.0, "issued", _fmt(конец_прошлого))                       # старый — по дню оформления
    строка = lambda p: next(r for r in db.payroll_for_period(p) if r["city"] == CITY)
    c3(f"прошлый месяц: 20 + старый 5 = 25 ({строка(прошлый)['revenue']})", abs(строка(прошлый)["revenue"] - 25.0) < 0.001)
    c3(f"этот месяц: заказ, выданный 1-го, — 100 ({строка(этот)['revenue']})", abs(строка(этот)["revenue"] - 100.0) < 0.001)

    c4 = Checker("Продажа на точке: выдана в момент продажи")
    _clean()
    pid = db.add_product(CITY, "accessories", "Кабель", 10.0, 5)
    cache.bust()
    oid, _, _ = db.record_point_sale(CITY, [{"id": pid, "qty": 2, "price": 10.0}], 100, "owner", "cash", "")
    продажа = db.get_order(oid)
    c4("время выдачи = время продажи", продажа["issued_at"] == продажа["created_at"])
    c4("продажа — в «выдано сегодня» как продажа на точке",
       _today()["point_today"] == 1 and abs(_today()["point_revenue_today"] - 20.0) < 0.001)
    s = db.get_business_stats(days=1)
    c4(f"в выручке есть ({s['revenue']}), а в покупателях — нет ({s['buyers_period']})",
       abs(s["revenue"] - 20.0) < 0.001 and s["buyers_period"] == 0 and s["total_buyers"] == 0)

    _clean()
    db.remove_staff(SELLER)
    as_admin()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
