"""Данные для сводки дня: get_business_stats корректно считает выручку/заказы/топ."""
from _common import db, Checker

CLIENT = 4242


def run():
    c = Checker("Статистика: выданные заказы формируют выручку и топ")

    # чистим заказы, чтобы период был предсказуем
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); conn.commit(); conn.close()

    pid = db.add_product("minsk", "pods", "SummaryPod", 30, 10)
    # два выданных заказа: 2шт и 1шт → выручка 90, топ 3шт
    for qty in (2, 1):
        oid = db.create_order(CLIENT, "buyer", "minsk",
                              [{"id": pid, "name": "SummaryPod", "price": 30, "qty": qty}], 30 * qty, "")
        db.set_order_status(oid, "issued")
    # один неоплаченный (paid) — в выручку НЕ идёт, но в «ждут»
    db.create_order(CLIENT, "buyer", "minsk",
                    [{"id": pid, "name": "SummaryPod", "price": 30, "qty": 1}], 30, "")
    oid_paid = db.get_orders()[0]["id"]
    db.set_order_status(oid_paid, "paid")

    s = db.get_business_stats(1)
    c("выручка = 90 (только выданные)", abs(s["revenue"] - 90) < 0.01)
    c("выдано заказов = 2", s["orders"] == 2)
    c("средний чек = 45", abs(s["avg_check"] - 45) < 0.01)
    c("в работе (paid) = 1", s["inwork_count"] == 1)
    c("топ: SummaryPod ×3", s["top"] and s["top"][0]["name"] == "SummaryPod" and s["top"][0]["qty"] == 3)

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
