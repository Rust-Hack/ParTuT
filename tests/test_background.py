"""Что магазин делает сам, без нажатия кнопки.

Раз в минуту бот прокручивает фоновые дела: напоминает продавцам про заказы,
отменяет брошенные, шлёт владельцу сводку дня, снимает копию базы и зовёт
давних покупателей вернуться. Всё это никто не запускает руками — и потому
поломка здесь не видна ни на экране, ни в логах.

Проверяем то, что обстрелом оказалось сломано.

1. «Раз в сутки» обязано переживать перезапуск. Отметку о выполнении держали в
   памяти процесса, а Render поднимает сервис заново на каждом деплое. Сводка
   дублировалась владельцу; хуже — напоминания покупателям уходили НОВОЙ
   порцией по потолку после каждого запуска: при потолке 3 за вечер ушло 10.
   Потолок и есть вся защита от веерной рассылки, а за неё Telegram отбирает
   покупателей блокировкой навсегда.

2. Шаги не должны зависеть друг от друга. Они стояли под одним try, и поломка в
   первом отменяла все следующие — включая ночную копию базы. Магазин остался
   бы без копий молча.
"""
import datetime
import threading

from _common import db, Checker, SENT, reset_sent

from partut.bot import handlers as botmod

MARKS = (botmod._SUMMARY_MARK, botmod._BACKUP_MARK, botmod._REPEAT_MARK)


def _reset_marks():
    for key in MARKS:
        db.set_setting(key, "")


def _issued_order(uid, days_ago):
    oid = db.create_order(uid, f"u{uid}", "Минск",
                          [{"product_id": 1, "name": "Под", "price": 10.0, "qty": 1}], 10.0, "")
    when = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET created_at = %s, status = 'issued' WHERE id = %s"), (when, oid))
    conn.commit(); conn.close()
    return oid


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    cur.execute("UPDATE users SET reminded_at = NULL, no_reminders = 0")
    conn.commit(); conn.close()


def run():
    # Час 0 наступил всегда, час 99 не наступит никогда: так проверка не зависит
    # от того, в какое время суток её запустили.
    c = Checker("Ночное дело случается раз в сутки")
    db.set_setting("проба_ночного_дела", "")
    c("до назначенного часа не срабатывает", botmod._claim_daily("проба_ночного_дела", 99) is False)
    c("в назначенный час срабатывает", botmod._claim_daily("проба_ночного_дела", 0) is True)
    c("второй раз за сутки — нет", botmod._claim_daily("проба_ночного_дела", 0) is False)
    c("отметка лежит в базе, а не в памяти процесса",
      db.get_setting("проба_ночного_дела") == botmod._local_now().date().isoformat())
    db.set_setting("проба_ночного_дела", "2020-01-01")
    c("назавтра срабатывает снова", botmod._claim_daily("проба_ночного_дела", 0) is True)

    real_backup_hour, real_summary_hour = botmod.BACKUP_HOUR, botmod.SUMMARY_HOUR
    botmod.BACKUP_HOUR = botmod.SUMMARY_HOUR = 0
    real_send_backup = botmod._send_backup
    backups = []
    botmod._send_backup = lambda ids, note="": backups.append(note) or None
    try:
        # --- Сводка дня ---
        c2 = Checker("Сводка дня владельцу")
        _clean()
        _issued_order(9601, 0)
        _reset_marks()
        reset_sent()
        botmod._maybe_send_daily_summary()
        c2("сводка ушла", len(SENT) == 1)
        text = SENT[0][1] if SENT else ""
        c2("в ней есть выручка", "Выручка" in text)
        c2("и сколько заказов ждёт прямо сейчас", "Ждут вас сейчас" in text)

        # Перезапуск сервиса: память процесса чиста, отметка осталась в базе.
        botmod._maybe_send_daily_summary()
        c2("после перезапуска сводка не дублируется", len(SENT) == 1)

        # --- Напоминания покупателям ---
        c3 = Checker("Суточный потолок переживает перезапуск")
        _clean()
        for uid in range(9610, 9620):
            _issued_order(uid, 40)
        db.set_setting("remind_after_days", 21)
        db.set_setting("remind_daily_cap", 3)
        _reset_marks()
        reset_sent()
        botmod._maybe_send_repeat_reminders()
        c3("за прогон ушло ровно по потолку", len(SENT) == 3)

        # Четыре деплоя за вечер — каждый поднимает сервис заново.
        for _ in range(4):
            botmod._maybe_send_repeat_reminders()
        c3("перезапуски не пробивают потолок", len(SENT) == 3)
        c3("а неразосланные остались ждать завтра",
           len(db.customers_to_remind(days=21, limit=50)) == 7)

        # Выключенная рассылка не должна «съедать» день: если владелец включит
        # её обратно тем же вечером, напоминания обязаны уйти.
        db.set_setting("remind_daily_cap", 0)
        _reset_marks()
        reset_sent()
        botmod._maybe_send_repeat_reminders()
        c3("при нулевом потолке молчим", not SENT)
        db.set_setting("remind_daily_cap", 2)
        botmod._maybe_send_repeat_reminders()
        c3("включили обратно — рассылка идёт в тот же день", len(SENT) == 2)

        # --- Два экземпляра сервиса разом ---
        # Render при деплое некоторое время держит старый и новый вместе.
        # «Прочитать отметку, потом записать» пропустило бы обоих — и порция
        # напоминаний ушла бы дважды.
        c35 = Checker("Ночное дело не достаётся двоим")
        db.set_setting("проба_гонки", "")
        победы = []
        потоки = [threading.Thread(target=lambda: победы.append(
            db.claim_setting("проба_гонки", "2026-01-01"))) for _ in range(12)]
        for t in потоки:
            t.start()
        for t in потоки:
            t.join()
        c35("из двенадцати заявок победила ровно одна", sum(победы) == 1)
        c35("в отметке — то, что записал победитель",
            db.get_setting("проба_гонки") == "2026-01-01")
        c35("на следующие сутки отметка снова свободна",
            db.claim_setting("проба_гонки", "2026-01-02") is True)

        # --- Копия не ушла: об этом обязаны сказать ---
        c36 = Checker("Молчаливая потеря копий")
        _reset_marks()
        reset_sent()
        botmod._send_backup = lambda ids, note="": "нет сети"
        try:
            botmod._maybe_send_backup()
        finally:
            botmod._send_backup = lambda ids, note="": backups.append(note) or None
        сказано = " | ".join(str(x[1]) for x in SENT)
        c36("владельцу сказали, что копии за сегодня нет", "копия за сегодня не ушла" in сказано)
        c36("и подсказали, как снять её руками", "/backup" in сказано)

        # --- Шаги не тянут друг друга на дно ---
        c4 = Checker("Поломка одного дела не отменяет остальные")
        _clean()
        _reset_marks()
        db.set_setting("remind_daily_cap", 20)
        backups.clear()
        reset_sent()
        real_needing = db.orders_needing_reminder
        db.orders_needing_reminder = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("шаг сломан"))
        try:
            botmod._background_tick()
        finally:
            db.orders_needing_reminder = real_needing
        c4("копия базы снята, хотя предыдущий шаг упал", len(backups) == 1)
        c4("сводка дня тоже ушла", any("Итоги за день" in str(s[1]) for s in SENT))
        c4("владелец узнал о поломке от бота",
           any("напоминания продавцам" in str(s[1]) for s in SENT))
        # Считать шаги штуками смысла нет — важно, что на месте именно те, чьё
        # молчаливое исчезновение обнаружилось бы слишком поздно.
        дела = {name for name, _ in botmod._BACKGROUND_STEPS}
        c4("резервная копия в списке дел", "резервная копия" in дела)
        c4("авто-отмена неоплаченных в списке дел", "авто-отмена неоплаченных" in дела)
        c4("у каждого дела своё имя", len(дела) == len(botmod._BACKGROUND_STEPS))

        # Оборот без единой поломки не должен ничего сообщать владельцу.
        c5 = Checker("Спокойный оборот")
        _reset_marks()
        backups.clear()
        reset_sent()
        botmod._background_tick()
        c5("про ошибки не пишем, когда их нет",
           not any("ошибк" in str(s[1]).lower() for s in SENT))
        c5("а дела при этом сделаны", len(backups) == 1)
    finally:
        botmod._send_backup = real_send_backup
        botmod.BACKUP_HOUR, botmod.SUMMARY_HOUR = real_backup_hour, real_summary_hour
        db.set_setting("remind_daily_cap", 20)
        db.set_setting("remind_after_days", 21)
        db.set_setting("проба_гонки", "")
        _reset_marks()
        _clean()

    return c.fails + c2.fails + c3.fails + c35.fails + c36.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
