"""Зарплата продавца — процент от выручки его точки за календарный месяц.

Числа круглые нарочно (100/50/200 Br, 10%/20%), чтобы ошибку в самой доле
было видно сразу, а не искать в округлении. Проверяется весь смысл фичи:
выручка без доставки, только выданные заказы, только текущий месяц, точка
с одним продавцом — можно отметить выплаченным (дважды нельзя), точка с
несколькими — только показывается, авто-разбивки нет, а смена процента в
настройках не переписывает задним числом уже отмеченные выплаты.
"""
from _common import db, client, Checker, as_admin, deny_admin

CITY1 = "payrollcity1"   # один продавец
CITY2 = "payrollcity2"   # два продавца
CITY3 = "payrollcity3"   # для проверки смены процента


def _order(city, total, delivery_fee, status, months_ago=0):
    oid = db.create_order(9001, "u9001", city,
                          [{"product_id": 1, "name": "Товар", "price": total, "qty": 1}], total, "")
    conn = db.connect(); cur = conn.cursor()
    at = db.shop_now()
    if months_ago:
        y, m = at.year, at.month - months_ago
        while m < 1:
            m += 12; y -= 1
        at = at.replace(year=y, month=m, day=min(at.day, 28))
    cur.execute(db._q("UPDATE orders SET status=%s, delivery_fee=%s, created_at=%s WHERE id=%s"),
                (status, delivery_fee, at.strftime("%Y-%m-%d %H:%M"), oid))
    conn.commit(); conn.close()
    return oid


def run():
    c = Checker("Зарплата продавцов: расчёт")
    as_admin()  # владелец — заводит персонал и правит настройки

    db.add_staff(7001, CITY1, "Иван")
    db.add_staff(7002, CITY2, "Аня")
    db.add_staff(7003, CITY2, "Боря")

    # CITY1: 100-10=90 да 50-0=50 -> выручка 140. Плюс мусор, который не должен войти:
    _order(CITY1, 100, 10, "issued")
    _order(CITY1, 50, 0, "issued")
    _order(CITY1, 999, 0, "paid")          # не выдан — не считается
    _order(CITY1, 999, 0, "issued", months_ago=1)   # прошлый месяц — не считается

    period = db.shop_now().strftime("%Y-%m")
    d = client.post("/api/admin/payroll", json={"initData": "x", "period": period}).get_json()
    c("ответ ok", d.get("ok"))
    row1 = next((r for r in d["rows"] if r["city"] == CITY1), None)
    c("точка с продавцом попала в отчёт", row1 is not None)
    c("выручка = 140 (без доставки, без чужих статусов/месяцев)", abs(row1["revenue"] - 140.0) < 0.01)
    c("процент по умолчанию 10", row1["percent"] == 10)
    c("сумма к выплате = 14.00", abs(row1["amount"] - 14.0) < 0.01)
    c("продавец один — сумма посчитана, не оплачена", len(row1["sellers"]) == 1
      and row1["sellers"][0]["user_id"] == 7001 and row1["sellers"][0]["paid"] is None)

    row2 = next((r for r in d["rows"] if r["city"] == CITY2), None)
    c("точка без заказов тоже в отчёте (выручка 0)", row2 is not None and row2["revenue"] == 0)

    c2 = Checker("Зарплата продавцов: точка с одним продавцом — выплата")
    r = client.post("/api/admin/payroll/pay", json={"initData": "x", "period": period, "city": CITY1, "user_id": 7001})
    d2 = r.get_json()
    c2("выплата отмечена", d2.get("ok") and abs(d2["amount"] - 14.0) < 0.01)

    d3 = client.post("/api/admin/payroll", json={"initData": "x", "period": period}).get_json()
    row1b = next(r for r in d3["rows"] if r["city"] == CITY1)
    c2("теперь видно как выплаченное", row1b["sellers"][0]["paid"] is not None
       and abs(row1b["sellers"][0]["paid"]["amount"] - 14.0) < 0.01)

    r = client.post("/api/admin/payroll/pay", json={"initData": "x", "period": period, "city": CITY1, "user_id": 7001})
    c2("повторная попытка отбита", r.status_code == 400 and r.get_json().get("error") == "already_paid")

    c3 = Checker("Зарплата продавцов: точка с двумя продавцами — без авто-разбивки")
    _order(CITY2, 200, 0, "issued")
    d4 = client.post("/api/admin/payroll", json={"initData": "x", "period": period}).get_json()
    row2b = next(r for r in d4["rows"] if r["city"] == CITY2)
    c3("выручка посчитана", abs(row2b["revenue"] - 200.0) < 0.01)
    c3("сумма НЕ показана — делить некому кроме владельца", row2b["amount"] is None)
    c3("оба продавца перечислены", {s["user_id"] for s in row2b["sellers"]} == {7002, 7003})

    r = client.post("/api/admin/payroll/pay", json={"initData": "x", "period": period, "city": CITY2, "user_id": 7002})
    c3("выплатить с неразделённой точки нельзя", r.status_code == 400 and r.get_json().get("error") == "not_split")

    c4 = Checker("Зарплата продавцов: смена процента не переписывает старые выплаты")
    db.add_staff(7004, CITY3, "Света")
    _order(CITY3, 100, 0, "issued")
    d5 = client.post("/api/admin/payroll", json={"initData": "x", "period": period}).get_json()
    row3 = next(r for r in d5["rows"] if r["city"] == CITY3)
    c4("по умолчанию 10% от 100 = 10", abs(row3["amount"] - 10.0) < 0.01)

    client.post("/api/admin/settings/update", json={"initData": "x", "seller_commission_percent": 20})
    d6 = client.post("/api/admin/payroll", json={"initData": "x", "period": period}).get_json()
    row3b = next(r for r in d6["rows"] if r["city"] == CITY3)
    row1c = next(r for r in d6["rows"] if r["city"] == CITY1)
    c4("непочатая точка пересчиталась на новый процент (20% от 100 = 20)",
       abs(row3b["amount"] - 20.0) < 0.01)
    c4("уже выплаченная сумма CITY1 не изменилась задним числом (осталась 14.00)",
       abs(row1c["sellers"][0]["paid"]["amount"] - 14.0) < 0.01)
    client.post("/api/admin/settings/update", json={"initData": "x", "seller_commission_percent": 10})

    c5 = Checker("Зарплата продавцов: доступ и валидация")
    r = client.post("/api/admin/payroll", json={"initData": "x", "period": "мусор"})
    c5("кривой период отклонён", r.status_code == 400 and r.get_json().get("error") == "bad_period")

    as_admin(uid=555, username="seller", role="seller", city=CITY1)
    r = client.post("/api/admin/payroll", json={"initData": "x"})
    c5("продавцу (не владельцу) зарплата закрыта", r.status_code == 403)
    deny_admin()
    r = client.post("/api/admin/payroll", json={"initData": "x"})
    c5("постороннему тем более", r.status_code == 403)
    as_admin()

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
