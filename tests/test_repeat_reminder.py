"""Напоминание о повторной покупке.

Цена ошибки здесь выше обычной: лишнее сообщение уходит живому покупателю, а
за назойливость Телеграм наказывает блокировкой — и человек потерян навсегда.
Поэтому проверяем не только «доходит», но и все ограничители.
"""
import datetime

from _common import db, client, Checker, as_user, SENT, reset_sent

import bot as botmod


def _order_days_ago(uid, days, status="issued"):
    """Заказ «в прошлом»: создаём и сдвигаем дату назад."""
    oid = db.create_order(uid, f"u{uid}", "Минск",
                          [{"product_id": 1, "name": "Под", "price": 10.0, "qty": 1}], 10.0, "")
    when = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET created_at = %s, status = %s WHERE id = %s"), (when, status, oid))
    conn.commit(); conn.close()
    return oid


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("UPDATE users SET reminded_at = NULL, no_reminders = 0")
    conn.commit(); conn.close()


def run():
    c = Checker("Кому напоминать о повторной покупке")
    _clean()

    _order_days_ago(9001, 40)                    # давно купил и получил — ждём напоминание
    _order_days_ago(9002, 3)                     # купил на днях — рано
    _order_days_ago(9003, 40, status="new")      # так и не оплатил — товара не получил
    _order_days_ago(9004, 40, status="canceled")  # отменён

    due = {int(r["user_id"]) for r in db.customers_to_remind(days=21, limit=50)}
    c("давний покупатель попал в список", 9001 in due)
    c("недавний — нет", 9002 not in due)
    c("неоплаченный заказ не считается покупкой", 9003 not in due)
    c("отменённый тоже", 9004 not in due)

    # --- Отписка ---
    db.set_no_reminders(9001, True)
    due = {int(r["user_id"]) for r in db.customers_to_remind(days=21, limit=50)}
    c("отписавшемуся не пишем", 9001 not in due)
    c("настройка читается обратно", db.get_no_reminders(9001) is True)
    db.set_no_reminders(9001, False)
    c("подписку можно вернуть", db.get_no_reminders(9001) is False)

    # --- Не чаще, чем раз в срок ---
    db.mark_reminded(9001)
    due = {int(r["user_id"]) for r in db.customers_to_remind(days=21, limit=50)}
    c("недавно напомнили — молчим", 9001 not in due)

    conn = db.connect(); cur = conn.cursor()
    long_ago = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m-%d %H:%M")
    cur.execute(db._q("UPDATE users SET reminded_at = %s WHERE user_id = %s"), (long_ago, 9001))
    conn.commit(); conn.close()
    due = {int(r["user_id"]) for r in db.customers_to_remind(days=21, limit=50)}
    c("через срок напоминаем снова", 9001 in due)

    # --- Суточный потолок ---
    # Ради него всё и затевалось: в день запуска просроченными окажутся ВСЕ
    # давние покупатели разом, и без потолка это веерная рассылка.
    _clean()
    for uid in range(9100, 9130):
        _order_days_ago(uid, 40)
    batch = db.customers_to_remind(days=21, limit=5)
    c("за прогон не больше потолка", len(batch) == 5)
    c("а всего просроченных больше", len(db.customers_to_remind(days=21, limit=100)) == 30)

    # --- Отправка ---
    c2 = Checker("Отправка напоминаний")
    _clean()
    _order_days_ago(9200, 40)
    _order_days_ago(9201, 40)
    db.set_setting("remind_after_days", 21)
    db.set_setting("remind_daily_cap", 1)         # проверяем, что потолок соблюдается
    db.set_setting(botmod._REPEAT_MARK, "")   # «новые сутки»: отметка живёт в базе
    reset_sent()

    real_hour, botmod.BACKUP_HOUR = botmod.BACKUP_HOUR, 0    # чтобы сработало сейчас
    try:
        botmod._maybe_send_repeat_reminders()
        c2("ушло ровно по потолку — одно", len(SENT) == 1)
        c2("текст зовёт повторить заказ", "Повторить заказ" in SENT[0][1])
        c2("и объясняет, как отписаться", "Напоминать о заказе" in SENT[0][1])

        # Второй раз за те же сутки — молчим.
        reset_sent()
        botmod._maybe_send_repeat_reminders()
        c2("дважды в сутки не шлём", not SENT)

        # Потолок 0 = напоминания выключены совсем.
        db.set_setting("remind_daily_cap", 0)
        db.set_setting(botmod._REPEAT_MARK, "")   # «новые сутки»: отметка живёт в базе
        reset_sent()
        botmod._maybe_send_repeat_reminders()
        c2("ноль в настройке выключает рассылку", not SENT)

        # Помечаем ДО отправки: заблокировавший бота не должен получать попытку
        # каждый день до конца времён.
        db.set_setting("remind_daily_cap", 5)
        db.set_setting(botmod._REPEAT_MARK, "")   # «новые сутки»: отметка живёт в базе
        _clean()
        _order_days_ago(9300, 40)
        real_send = botmod.bot.send_message
        botmod.bot.send_message = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("бот заблокирован"))
        try:
            botmod._maybe_send_repeat_reminders()
        finally:
            botmod.bot.send_message = real_send
        c2("после неудачной отправки человек всё равно помечен",
           9300 not in {int(r["user_id"]) for r in db.customers_to_remind(days=21, limit=50)})
    finally:
        botmod.BACKUP_HOUR = real_hour
        db.set_setting("remind_daily_cap", 20)
        db.set_setting("remind_after_days", 21)
        db.set_setting(botmod._REPEAT_MARK, "")   # не оставляем отметку следующим тестам

    # --- Отписка из приложения ---
    c3 = Checker("Отписка в приложении")
    as_user(9400)
    db.ensure_user(9400)
    r = client.post("/api/reminders", json={"initData": "x", "on": False})
    c3("выключили", (r.get_json() or {}).get("ok") and db.get_no_reminders(9400) is True)
    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c3("приложение видит, что выключено", me.get("reminders_on") is False)
    client.post("/api/reminders", json={"initData": "x", "on": True})
    c3("включили обратно", db.get_no_reminders(9400) is False)

    _clean()
    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
