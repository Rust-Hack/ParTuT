"""Промокод и одновременные заказы.

Найдено обстрелом на гонки. Проверка кода и его списание были двумя отдельными
походами в базу, и между ними успевал вклиниться другой заказ:

  • код «один раз на покупателя» применялся по четыре раза подряд;
  • код на два применения уходил в шесть заказов, а счётчик всё равно
    показывал ноль.

Это прямая потеря денег: код выкладывают в канал, и достаточно нажать
«Оформить» несколько раз подряд. Теперь и проверка, и списание живут внутри
транзакции заказа, а строка кода на это время блокируется.

Тест обязателен к прогону и на Postgres: блокировка там своя (SELECT ... FOR
UPDATE), и на SQLite её подменяет запись. TEST_DATABASE_URL=... python tests/run_all.py
"""
import threading

from _common import db, client, server, Checker, as_admin

UID = 7501


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "promos"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("DELETE FROM delivery_methods WHERE city = 'Минск'")
    conn.commit(); conn.close()
    server._cache_bust()


def _setup():
    db.add_delivery_method("Минск", "Самовывоз", False, "", "ул. Тест", 0, True, 0)
    mid = db.get_delivery_methods("Минск")[0]["id"]
    pid = db.add_product("Минск", "disposable", "Товар", 10.0, 100)
    db.set_age_ok(UID)
    server.get_user = lambda init: {"id": UID, "username": "racer"}
    server._cache_bust()
    return mid, pid


def _parallel(fn, n=6):
    out = []
    threads = [threading.Thread(target=lambda: out.append(fn())) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _used(code):
    return len([o for o in db.get_orders(99) if (o["promo_code"] or "") == code])


def run():
    _clean()
    mid, pid = _setup()

    def buy(code):
        return client.post("/api/order", json={
            "initData": "x", "city": "Минск", "delivery_method_id": mid,
            "payment_method": "cash", "promo_code": code,
            "items": [{"id": pid, "qty": 1}]}).get_json()

    # --- «Один раз на покупателя» ---
    c = Checker("Промокод «один раз на покупателя»")
    db.add_promo("ONCE", "fixed", 5, 0, None, True)
    res = _parallel(lambda: buy("ONCE"))
    c("код применён ровно один раз", _used("ONCE") == 1)
    # Отказ приходит одним из двух путей и оба верные: кого-то отсекает обычная
    # проверка кода (promo_once), кого-то — блокировка внутри транзакции
    # (promo_gone). Что именно сработает, зависит от того, кто успел раньше;
    # требовать конкретный из них значит проверять случайность. Важно другое:
    # отказ ПОНЯТНЫЙ, а не падение сервера и не молчаливое оформление.
    PROMO_REFUSALS = ("promo_gone", "promo_once", "promo_used_up")
    c("остальные заказы отклонены с объяснением",
      all(d.get("error") in PROMO_REFUSALS for d in res if not d.get("ok")))
    c("и ни один не упал в ошибку сервера",
      all(d.get("error") != "server_error" for d in res if not d.get("ok")))
    # Приложение знает оба ответа: у promo_gone сообщение приходит с сервера,
    # у promo_once — своё, из списка errs. Человек в любом случае понимает,
    # почему заказ не оформился.
    c("человеку есть что показать",
      all(d.get("message") or d.get("error") in PROMO_REFUSALS
          for d in res if not d.get("ok")))
    # Заказ без скидки втихую не оформляется: человек согласился на одну сумму.
    c("заказ со сгоревшим кодом не создан", sum(1 for d in res if d.get("ok")) == 1)

    # --- Ограниченное число применений ---
    c2 = Checker("Промокод на два применения")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); conn.commit(); conn.close()
    db.add_promo("LIM", "fixed", 5, 0, 2, False)
    _parallel(lambda: buy("LIM"))
    c2("применён ровно дважды", _used("LIM") == 2)
    c2("счётчик показывает ноль", db._promo_row("LIM")["uses_left"] == 0)
    # Последовательный заказ отсекает обычная проверка кода (promo_used_up), а
    # promo_gone возникает только в гонке. Отказ обязателен в обоих случаях.
    c2("третий раз уже не пройдёт",
       buy("LIM").get("error") in ("promo_used_up", "promo_gone"))

    # --- Обычный код без ограничений работает как работал ---
    c3 = Checker("Код без ограничений")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); conn.commit(); conn.close()
    db.add_promo("FREE", "fixed", 5, 0, None, False)
    res = _parallel(lambda: buy("FREE"), 4)
    c3("проходят все четыре заказа", sum(1 for d in res if d.get("ok")) == 4)
    c3("и скидка применилась в каждом", _used("FREE") == 4)

    # --- Отменённый заказ освобождает «один раз на покупателя» ---
    # Иначе неудачная покупка навсегда лишает человека акции.
    c4 = Checker("Отмена возвращает право на код")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders"); conn.commit(); conn.close()
    db.add_promo("BACK", "fixed", 5, 0, None, True)
    d = buy("BACK")
    c4("первый заказ прошёл", d.get("ok") is True)
    c4("повторный — отказ", buy("BACK").get("error") in ("promo_once", "promo_gone"))
    db.cancel_order(d["order_id"], ["new", "paid"])
    c4("после отмены код снова доступен", buy("BACK").get("ok") is True)

    as_admin()
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
