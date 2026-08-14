"""Сообщения о поломках: владелец узнаёт о сбое, но не тонет в спаме.

Отдельно проверяем, что сам отчёт безопасен: если Telegram недоступен или
что-то не так внутри отчёта, вызвавший его код не должен упасть.
"""
from _common import db, client, server, Checker, as_user

import errors


class _Tg:
    """Подставной телебот: копит отправленное."""
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise RuntimeError("Telegram недоступен")
        self.sent.append((chat_id, text))


def run():
    c = Checker("Сообщения о поломках")
    errors.reset()

    tg = _Tg()
    try:
        raise ValueError("касса сгорела")
    except ValueError as e:
        errors.report(tg, "POST /api/order", e)

    c("владельцу ушло сообщение", len(tg.sent) == 1)
    text = tg.sent[0][1]
    c("видно место", "POST /api/order" in text)
    c("видно что случилось", "касса сгорела" in text)
    c("есть след ошибки", "test_errors.py" in text)

    # --- Антиспам ---
    for _ in range(5):
        try:
            raise ValueError("касса сгорела")
        except ValueError as e:
            errors.report(tg, "POST /api/order", e)
    c("повторы не спамят", len(tg.sent) == 1)

    # Другая ошибка в том же месте — это уже новость, её пропускаем.
    try:
        raise KeyError("нет такого товара")
    except KeyError as e:
        errors.report(tg, "POST /api/order", e)
    c("другая ошибка проходит", len(tg.sent) == 2)

    # После паузы повторы уходят одним сообщением со счётчиком.
    errors.COOLDOWN = 0
    try:
        raise ValueError("касса сгорела")
    except ValueError as e:
        errors.report(tg, "POST /api/order", e)
    c("после паузы сообщение уходит", len(tg.sent) == 3)
    c("и говорит, сколько повторов проглочено", "Повторилось ещё 5" in tg.sent[2][1])
    errors.COOLDOWN = 600

    # --- Отчёт не имеет права ронять то, что его вызвало ---
    errors.reset()
    broken = _Tg(fail=True)
    try:
        raise RuntimeError("что угодно")
    except RuntimeError as e:
        errors.report(broken, "фон", e)          # не должно бросить наружу
        c("недоступный Telegram не ломает вызвавший код", True)

    errors.reset()
    try:
        errors.report(None, "совсем без бота", ValueError("х"))
        c("отсутствие бота тоже переживает", True)
    except Exception:
        c("отсутствие бота тоже переживает", False)

    # --- Живой сбой в настоящем запросе ---
    # Ломаем то, чем пользуется существующий маршрут: так проверяется вся
    # цепочка — Flask действительно зовёт обработчик, а клиент получает 500.
    c2 = Checker("Сбой запроса доходит до владельца")
    errors.reset()
    real_tg, server.tg = server.tg, _Tg()
    real_fn = db.get_orders_by_user
    db.get_orders_by_user = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("база отвалилась"))
    try:
        as_user(4242)
        r = client.post("/api/orders", json={"initData": "x"})
        c2("клиент получает честную 500, а не пустоту", r.status_code == 500)
        c2("и понятный ответ", (r.get_json() or {}).get("error") == "server_error")
        c2("владельцу пришло сообщение", len(server.tg.sent) == 1)
        c2("в нём есть адрес запроса", "/api/orders" in server.tg.sent[0][1])
        c2("и причина", "база отвалилась" in server.tg.sent[0][1])
    finally:
        db.get_orders_by_user = real_fn

    # Штатная 404 — не поломка, беспокоить владельца незачем.
    server.tg.sent.clear()
    r = client.post("/api/такого-маршрута-нет", json={})
    c2("несуществующий адрес — штатный отказ, не сбой", 400 <= r.status_code < 500)
    c2("и владельцу НЕ шлётся", not server.tg.sent)

    server.tg = real_tg
    errors.reset()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
