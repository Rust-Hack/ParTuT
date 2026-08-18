"""Компенсация покупателю: единственные деньги в руках продавца.

Механизм заявок в магазине был построен, но недостижим: все денежные ручки
закрыты правилом «только владельцу» раньше, чем дело доходит до заявки.
Продавец, у которого покупатель получил протёкший под, звонил владельцу.

Теперь щель открыта ровно на один случай и обставлена так, чтобы ей нельзя было
злоупотребить: компенсация привязана к заказу (значит, к покупателю и к точке),
ограничена потолком, и без подтверждения владельца монеты не начисляются.

Владелец подтверждает из чата — там же, где узнаёт о заказах. Значит и путь из
чата должен доводить дело до конца: начислить и сказать об этом покупателю.
"""
import json
import types

from _common import (db, client, server, Checker, as_user, as_admin, deny_admin,
                     SENT, reset_sent)
import bot as botmod
import config

ПОКУПАТЕЛЬ, ПРОДАВЕЦ, ВЛАДЕЛЕЦ = 9601, 9602, 9603


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("admin_requests", "orders", "coin_log"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute(db._q("DELETE FROM users WHERE user_id BETWEEN %s AND %s"), (9600, 9699))
    conn.commit(); conn.close()


def _order(city):
    return db.create_order(ПОКУПАТЕЛЬ, "кто", city,
                           [{"product_id": 1, "name": "Под", "price": 10.0, "qty": 1}], 10.0, "")


def run():
    _clean()
    db.ensure_user(ПОКУПАТЕЛЬ)
    db.set_setting("compensation_max", 1000)
    свой = _order("Туров")
    чужой = _order("Минск")

    настоящий_супер = server.is_super_admin
    старые_суперы = config.SUPER_ADMIN_IDS
    server.is_super_admin = lambda uid: int(uid) == ВЛАДЕЛЕЦ
    config.SUPER_ADMIN_IDS = старые_суперы | {ВЛАДЕЛЕЦ}
    try:
        c = Checker("Продавец просит, а не берёт")
        as_admin(uid=ПРОДАВЕЦ, username="продавец Турова", role="staff", city="Туров")
        reset_sent()
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": 300,
                              "reason": "под потёк"}).get_json()
        c("заявка принята", r.get("ok") is True)
        c("но выполнена не сразу", r.get("pending") is True)
        c("монеты покупателю пока не начислены", db.get_coins(ПОКУПАТЕЛЬ) == 0)

        заявки = db.list_admin_requests("pending")
        c("заявка ждёт владельца", len(заявки) == 1)
        текст = заявки[0]["summary"] if заявки else ""
        c("в заявке видно сумму", "300" in текст)
        c("и номер заказа", f"#{свой}" in текст)
        c("и причина, а не голое число", "под потёк" in текст)

        c2 = Checker("Границы")
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": чужой, "coins": 100}).get_json()
        c2("по заказу чужой точки — отказ", r.get("error") == "other_city")
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": 99999}).get_json()
        c2("сверх потолка — отказ", r.get("error") == "bad_amount")
        c2("и человеку сказано, каков потолок", "1000" in (r.get("message") or ""))
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": -500}).get_json()
        c2("отрицательная «компенсация» не проходит", r.get("error") == "bad_amount")
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": 999999, "coins": 10}).get_json()
        c2("по несуществующему заказу — отказ", r.get("error") == "not_found")
        c2("ничего из этого монет не прибавило", db.get_coins(ПОКУПАТЕЛЬ) == 0)
        c2("и лишних заявок не наплодило", len(db.list_admin_requests("pending")) == 1)

        deny_admin()
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": 10})
        c2("постороннему компенсации недоступны", r.status_code == 403)

        # --- Владелец подтверждает из чата ---
        c3 = Checker("Владелец подтверждает в боте")
        rid = заявки[0]["id"]
        orig = (botmod.bot.answer_callback_query, botmod.bot.edit_message_text)
        botmod.bot.answer_callback_query = lambda *a, **k: None
        botmod.bot.edit_message_text = lambda *a, **k: None
        reset_sent()
        try:
            botmod.handle_approval(
                types.SimpleNamespace(data=f"areq:ok:{rid}", id="c1",
                                      from_user=types.SimpleNamespace(id=ВЛАДЕЛЕЦ, username="хозяин"),
                                      message=types.SimpleNamespace(
                                          chat=types.SimpleNamespace(id=ВЛАДЕЛЕЦ), message_id=1)),
                ВЛАДЕЛЕЦ, f"areq:ok:{rid}")
        finally:
            (botmod.bot.answer_callback_query, botmod.bot.edit_message_text) = orig

        c3("монеты начислены", db.get_coins(ПОКУПАТЕЛЬ) == 300)
        сказано = " | ".join(str(x[1]) for x in SENT)
        c3("покупатель узнал о компенсации", "компенсации" in сказано)
        c3("и за какой заказ", f"#{свой}" in сказано)
        c3("продавцу сказали, что одобрено", "одобрен" in сказано)
        c3("заявка больше не в очереди", not db.list_admin_requests("pending"))

        # Летопись монет: владельцу важно видеть, сколько уходит на извинения.
        c3("в летописи это компенсация, а не «правка вручную»",
           any(x["reason"] == "compensation" and x["granted"] == 300
               for x in db.coin_flow(30)["by_reason"]))

        # --- Дважды одну заявку не выполнить ---
        c4 = Checker("Повторное подтверждение")
        # Решение заявки проходит и через общего стража «только владельцу»: ему
        # мало get_user — нужна роль владельца.
        as_user(ВЛАДЕЛЕЦ); as_admin(uid=ВЛАДЕЛЕЦ, username="хозяин", role="owner", city="")
        r = client.post("/api/admin/request/decide",
                        json={"initData": "x", "request_id": rid, "decision": "approve"})
        c4("уже решённую заявку не провести снова", r.status_code == 409)
        c4("и монет не прибавилось", db.get_coins(ПОКУПАТЕЛЬ) == 300)

        # --- Отклонение ---
        c5 = Checker("Владелец отказывает")
        as_admin(uid=ПРОДАВЕЦ, username="продавец Турова", role="staff", city="Туров")
        client.post("/api/admin/order/compensate",
                    json={"initData": "x", "order_id": свой, "coins": 900, "reason": "просто так"})
        rid2 = db.list_admin_requests("pending")[0]["id"]
        as_user(ВЛАДЕЛЕЦ); as_admin(uid=ВЛАДЕЛЕЦ, username="хозяин", role="owner", city="")
        reset_sent()
        r = client.post("/api/admin/request/decide",
                        json={"initData": "x", "request_id": rid2, "decision": "reject"}).get_json()
        c5("отказ принят", r.get("decided") == "rejected")
        c5("монеты не начислены", db.get_coins(ПОКУПАТЕЛЬ) == 300)
        c5("продавцу сказали об отказе", "отклонён" in " ".join(str(x[1]) for x in SENT))

        # --- Владелец делает это сам, без заявки ---
        c6 = Checker("Владелец начисляет сразу")
        as_admin(uid=ВЛАДЕЛЕЦ, username="хозяин", role="owner", city="")
        reset_sent()
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": 100,
                              "reason": "извинения"}).get_json()
        c6("выполнено сразу, без подтверждения", r.get("ok") and not r.get("pending"))
        c6("монеты начислены", db.get_coins(ПОКУПАТЕЛЬ) == 400)
        c6("покупателя всё равно предупредили",
           any("компенсации" in str(x[1]) for x in SENT))

        # --- Ноль в настройке выключает компенсации ---
        c7 = Checker("Потолок ноль")
        db.set_setting("compensation_max", 0)
        as_admin(uid=ПРОДАВЕЦ, username="продавец Турова", role="staff", city="Туров")
        r = client.post("/api/admin/order/compensate",
                        json={"initData": "x", "order_id": свой, "coins": 1}).get_json()
        c7("при нулевом потолке компенсаций нет", r.get("error") == "bad_amount")
        c7("и монет не прибавилось", db.get_coins(ПОКУПАТЕЛЬ) == 400)
    finally:
        server.is_super_admin = настоящий_супер
        config.SUPER_ADMIN_IDS = старые_суперы
        db.set_setting("compensation_max", db.COMPENSATION_MAX_DEFAULT)
        as_admin()
        _clean()

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails + c7.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
