"""Связь клиент↔магазин: привязка к заказу, кликабельные контакты, экранирование, антиспам."""
from _common import db, client, server, SENT, reset_sent, Checker, as_user, as_admin

from partut import limits
from partut.integrations import tgsend

CLIENT = 555


def support(payload, clear_cooldown=True):
    if clear_cooldown:
        server._support_пауза.забыть()
    reset_sent()
    r = client.post("/api/support", json={"initData": "x", **payload})
    return r, (r.get_json() or {})


def admin_msg(payload):
    reset_sent()
    r = client.post("/api/admin/message", json={"initData": "x", **payload})
    return r, (r.get_json() or {})


def run():
    server.SUPPORT_IDS = {999000}
    # заказ #10 принадлежит CLIENT, #20 — чужому
    orig_get_order = db.get_order
    db.get_order = lambda oid: ({"id": 10, "user_id": CLIENT} if int(oid) == 10
                                else {"id": 20, "user_id": 999} if int(oid) == 20 else None)

    c = Checker("A. Вопрос по заказу + контакт клиента")
    as_user(CLIENT, "vasya", "Вася")
    r, d = support({"text": "где заказ?", "order_id": 10})
    txt = SENT[0][1]
    c("доставлено в поддержку", d.get("delivered") == 1 and SENT[0][0] == 999000)
    c("HTML parse_mode", SENT[0][2] == "HTML")
    c("тег заказа #10", "по заказу #10" in txt)
    c("t.me ссылка на клиента", "https://t.me/vasya" in txt)
    c("есть /reply", "/reply 555" in txt)

    c2 = Checker("B. Чужой/несущ. заказ — тег не проставляется")
    r, d = support({"text": "?", "order_id": 20})
    c2("чужой заказ: нет тега", "по заказу" not in SENT[0][1])
    r, d = support({"text": "?", "order_id": 99999})
    c2("несущ. заказ: нет тега, но доставлено", "по заказу" not in SENT[0][1] and d.get("delivered") == 1)

    c3 = Checker("C. Клиент без username → tg://user ссылка")
    as_user(CLIENT, None, "Аноним")
    r, d = support({"text": "вопрос"})
    c3("tg://user?id=555", "tg://user?id=555" in SENT[0][1])

    c4 = Checker("D. Магазин → клиент: контакт менеджера")
    as_admin(100, "manager_ivan")
    r, d = admin_msg({"user_id": CLIENT, "text": "заказ готов"})
    txt = SENT[0][1]
    c4("доставлено клиенту", SENT[0][0] == CLIENT)
    c4("t.me ссылка на менеджера", "https://t.me/manager_ivan" in txt)
    c4("HTML parse_mode", SENT[0][2] == "HTML")
    as_admin(100, None)
    r, d = admin_msg({"user_id": CLIENT, "text": "привет"})
    c4("менеджер без username → tg://user?id=100", "tg://user?id=100" in SENT[0][1])

    c5 = Checker("E. HTML-экранирование текста (защита от инъекции)")
    as_user(CLIENT, "vasya")
    r, d = support({"text": "<b>hack</b> & <script>"})
    c5("< > & экранированы",
       "&lt;b&gt;hack&lt;/b&gt; &amp; &lt;script&gt;" in SENT[0][1])

    c6 = Checker("F. Антиспам поддержки (кулдаун)")
    # Стенд паузы выключает (см. _common), иначе каждая проверка упиралась бы
    # в антиспам. Здесь проверяем сам антиспам — значит включаем обратно.
    limits.включить()
    try:
        r, d = support({"text": "первое"}, clear_cooldown=True)
        c6("первое сообщение → ok", d.get("ok"))
        r2 = client.post("/api/support", json={"initData": "x", "text": "второе подряд"})
        d2 = r2.get_json() or {}
        c6("второе сразу → 429 cooldown", r2.status_code == 429 and d2.get("error") == "cooldown")
        c6("есть retry_after", isinstance(d2.get("retry_after"), int) and d2["retry_after"] > 0)
        # прошло время → снова можно
        server._support_пауза.забыть(CLIENT)
        r3 = client.post("/api/support", json={"initData": "x", "text": "спустя время"})
        c6("после паузы → снова ok", (r3.get_json() or {}).get("ok"))
    finally:
        limits.выключить()

    c7 = Checker("G. Пустой текст отклоняется")
    r, d = support({"text": "   "})
    c7("пустой → 400", r.status_code == 400)

    # H. Несколько менеджеров.
    #
    # Ручка ждёт ОДНУ удачную отправку и уходит, дописывая остальным в фоне:
    # цикл по всем держал поток запроса на каждом по очереди, а их восемь.
    # Проверяем обе половины обещания — что доходит до всех и что ответ
    # «доставлено» не выдаётся авансом.
    c8 = Checker("H. Несколько менеджеров: ждём одного, остальным пишем в фоне")
    tgsend.дождаться_фона()          # хвосты прошлых проверок — не наши
    as_user(CLIENT, "vasya")
    server.SUPPORT_IDS = {999000, 999001, 999002}
    r, d = support({"text": "вопрос всем"})
    c8("вопрос дошёл до всех троих", {x[0] for x in SENT} == {999000, 999001, 999002})
    c8("покупателю сказано «доставлено»", d.get("delivered") == 1)

    настоящая_отправка = tgsend.tg.send_message

    def молчит_первый(cid, text, **kw):
        if cid == 999000:
            raise RuntimeError("бот заблокирован")
        return настоящая_отправка(cid, text, **kw)

    def молчат_все(cid, text, **kw):
        raise RuntimeError("Telegram недоступен")

    try:
        # Первый в списке заблокировал бота. Врать покупателю нельзя: ответ
        # «доставлено» обязан опираться на живую отправку, а не на попытку.
        tgsend.tg.send_message = молчит_первый
        r, d = support({"text": "первый недоступен"})
        c8("вопрос подхватил следующий менеджер", {x[0] for x in SENT} == {999001, 999002})
        c8("и это по-прежнему «доставлено»", d.get("delivered") == 1)

        tgsend.tg.send_message = молчат_все
        r, d = support({"text": "никого нет"})
        c8("никто не принял — доставку не обещаем", d.get("delivered") == 0)
        c8("и запрос всё равно не падает", r.status_code == 200)
    finally:
        tgsend.tg.send_message = настоящая_отправка
        server.SUPPORT_IDS = {999000}

    db.get_order = orig_get_order
    return (c.fails + c2.fails + c3.fails + c4.fails + c5.fails + c6.fails
            + c7.fails + c8.fails)


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
