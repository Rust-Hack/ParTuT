"""Единый порядок замков склада: оформление и отмена одновременно (приёмка SF-01-R2, 4.10.2026).

После SF-01-R1 отмена запирала варианты товара по номерам (Black, потом
White), а оформление — в порядке строк корзины (White, потом Black). Корзина
«White, Black» и отмена заказа на Black в одну секунду ждали друг друга, и
Postgres обрывал одну из них (40P01): человек получал ошибку вместо заказа
или отмены. Теперь все операции с заказами берут склад в начале транзакции
одним помощником (orders._запереть_склад) в общем порядке.

Порядок задаётся остановкой потока на нужном запросе — приём из приёмки:
подменённое соединение останавливает оформление сразу после того, как оно
списало White. Сами операции настоящие, в разных соединениях. statement_timeout
— страховка, чтобы зависание не повесило прогон. Только Postgres: в SQLite
замок один на всю базу и взаимной блокировки быть не может.
"""
import threading
import time

from _common import db, Checker

ПЕРВЫЙ, ВТОРОЙ = 79101, 79102


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _товар():
    """Black и White по 5 шт; Black заведён первым — у него номер меньше."""
    m = db.add_model("accessories", "QA Кабель", "QA", "", {}, ["Black", "White"])
    return db.create_point_product(m, "Минск", 10.0, 5.0, variants=[{"flavor": "Black", "stock": 5},
                                                                    {"flavor": "White", "stock": 5}])


def _строка(pid, вкус, штук=1):
    return {"id": pid, "flavor": вкус, "qty": штук, "price": 10.0, "name": f"QA Кабель — {вкус}"}


def _оформить(покупатель, строки):
    return db.place_order(покупатель, "qa", "Минск", строки, sum(x["qty"] * 10.0 for x in строки),
                          0, 0.01, 0, "Самовывоз", "", "cash", "", "", "confirmed")[0]


def _полка(pid):
    return {v["flavor"]: v["stock"] for v in db.get_variants(pid)}


class _Остановка:
    """Подменяет db.connect: в потоке с именем `кто` останавливается сразу ПОСЛЕ
    запроса, для которого `где(запрос, параметры)` истинно, и ждёт `дальше`."""

    def __init__(self, кто, где):
        self.кто, self.где = кто, где
        self.дошёл, self.дальше = threading.Event(), threading.Event()
        self.настоящее = db.connect

    def __enter__(self):
        остановка = self

        class Курсор:
            def __init__(self, внутри):
                self.внутри = внутри

            def __getattr__(self, имя):
                return getattr(self.внутри, имя)

            def execute(self, запрос, параметры=None):
                r = self.внутри.execute(запрос, параметры)
                if threading.current_thread().name == остановка.кто and остановка.где(запрос, параметры):
                    остановка.дошёл.set()
                    остановка.дальше.wait(10)
                return r

        class Соединение:
            def __init__(self, внутри):
                self.внутри = внутри

            def __getattr__(self, имя):
                return getattr(self.внутри, имя)

            def cursor(self, *a, **k):
                return Курсор(self.внутри.cursor(*a, **k))

        def connect():
            conn = остановка.настоящее()
            if threading.current_thread().name.startswith("qa-"):
                conn.cursor().execute("SET statement_timeout = '8s'")
                return Соединение(conn)
            return conn
        db.connect = connect
        return self

    def __exit__(self, *exc):
        self.дальше.set()
        db.connect = self.настоящее


def _поток(имя, fn, итог):
    def run():
        try:
            итог[имя] = ("ok", fn())
        except BaseException as e:            # noqa: BLE001 — нужен сам отказ
            итог[имя] = ("ошибка", f"{type(e).__name__} {getattr(e, 'pgcode', '')}: {e}")
    t = threading.Thread(name=имя, target=run)
    t.start()
    return t


def _ждёт(поток, итог, имя):
    time.sleep(0.6)
    return поток.is_alive() and имя not in итог


def run():
    c = Checker("Единый порядок замков: оформление и отмена (SF-01-R2)")
    if not db.USE_PG:
        c("SQLite: замок один на всю базу — взаимной блокировки нет, проверяется на Postgres", True)
        return c.fails
    _чисто()
    for uid in (ПЕРВЫЙ, ВТОРОЙ):
        db.ensure_user(uid)

    # ---- 1. Оформление «White, Black» списало White — тут приходит отмена заказа на Black ----
    pid = _товар()
    старый = _оформить(ПЕРВЫЙ, [_строка(pid, "Black")])
    итог = {}
    где = lambda q, p: q.startswith("UPDATE product_variants SET stock = stock -") and p and p[2] == "White"
    with _Остановка("qa-оформление", где) as ст:
        оформление = _поток("qa-оформление", lambda: _оформить(ВТОРОЙ, [_строка(pid, "White"), _строка(pid, "Black")]), итог)
        c("оформление списало White и стоит перед Black", ст.дошёл.wait(10))
        отмена = _поток("qa-отмена", lambda: db.cancel_order(старый), итог)
        c("отмена ждёт оформление (склад уже заперт им целиком)", _ждёт(отмена, итог, "qa-отмена"))
    оформление.join(12); отмена.join(12)
    c("обе закончились", not оформление.is_alive() and not отмена.is_alive())
    c(f"оформление прошло: {итог.get('qa-оформление')}", итог.get("qa-оформление", ("",))[0] == "ok")
    c(f"отмена прошла, а не оборвана взаимной блокировкой: {str(итог.get('qa-отмена'))[:80]}",
      итог.get("qa-отмена", ("",))[0] == "ok" and db.get_order(старый)["status"] == "canceled")
    c(f"на полке Black 4, White 4: {_полка(pid)}", _полка(pid) == {"Black": 4, "White": 4})

    # ---- 2. Отмена заперла склад первой — тут приходит оформление «White, Black» ----
    _чисто()
    pid = _товар()
    старый = _оформить(ПЕРВЫЙ, [_строка(pid, "Black")])
    итог = {}
    где = lambda q, p: q.startswith("SELECT id FROM products WHERE id = %s FOR UPDATE")   # склад заперт целиком
    with _Остановка("qa-отмена", где) as ст:
        отмена = _поток("qa-отмена", lambda: db.cancel_order(старый), итог)
        c("отмена заперла склад и стоит", ст.дошёл.wait(10))
        оформление = _поток("qa-оформление", lambda: _оформить(ВТОРОЙ, [_строка(pid, "White"), _строка(pid, "Black")]), итог)
        c("оформление ждёт отмену", _ждёт(оформление, итог, "qa-оформление"))
    оформление.join(12); отмена.join(12)
    c("обе прошли — ни одна не оборвана",
      итог.get("qa-оформление", ("",))[0] == "ok" and итог.get("qa-отмена", ("",))[0] == "ok")
    c(f"на полке Black 4, White 4: {_полка(pid)}", _полка(pid) == {"Black": 4, "White": 4})

    # ---- 3. Правка состава (Black 3 → 1) и оформление «White, Black» ----
    _чисто()
    pid = _товар()
    старый = _оформить(ПЕРВЫЙ, [_строка(pid, "Black", 3)])
    итог = {}
    где = lambda q, p: q.startswith("UPDATE product_variants SET stock = stock -") and p and p[2] == "White"
    with _Остановка("qa-оформление", где) as ст:
        оформление = _поток("qa-оформление", lambda: _оформить(ВТОРОЙ, [_строка(pid, "White"), _строка(pid, "Black")]), итог)
        c("оформление списало White и стоит", ст.дошёл.wait(10))
        правка = _поток("qa-правка", lambda: db.update_order_items(старый, {0: 1}, 0.01), итог)
        c("правка ждёт оформление", _ждёт(правка, итог, "qa-правка"))
    оформление.join(12); правка.join(12)
    c("обе прошли", итог.get("qa-оформление", ("",))[0] == "ok" and итог.get("qa-правка", ("",))[0] == "ok"
      and итог["qa-правка"][1][0] is not None)
    c(f"на полке Black 3 (5 − 3 + 2 − 1), White 4: {_полка(pid)}", _полка(pid) == {"Black": 3, "White": 4})

    # ---- 4. Продажа на точке «White, Black» и отмена заказа на Black ----
    _чисто()
    pid = _товар()
    старый = _оформить(ПЕРВЫЙ, [_строка(pid, "Black")])
    итог = {}
    где = lambda q, p: q.startswith("UPDATE product_variants SET stock = stock -") and p and p[2] == "White"
    with _Остановка("qa-продажа", где) as ст:
        продажа = _поток("qa-продажа", lambda: db.record_point_sale(
            "Минск", [{"id": pid, "flavor": "White", "qty": 1, "price": 10}, {"id": pid, "flavor": "Black", "qty": 1, "price": 10}],
            1, "qa"), итог)
        c("продажа на точке списала White и стоит", ст.дошёл.wait(10))
        отмена = _поток("qa-отмена", lambda: db.cancel_order(старый), итог)
        c("отмена ждёт продажу", _ждёт(отмена, итог, "qa-отмена"))
    продажа.join(12); отмена.join(12)
    c("обе прошли", итог.get("qa-продажа", ("",))[0] == "ok" and итог.get("qa-отмена", ("",))[0] == "ok")
    c(f"на полке Black 4, White 4: {_полка(pid)}", _полка(pid) == {"Black": 4, "White": 4})

    # ---- 5. Один покупатель: новый заказ с монетами и отмена старого с монетами ----
    # Раньше оформление брало монеты покупателя ДО склада, а отмена — после:
    # оформление держит покупателя и ждёт склад, отмена держит склад и ждёт
    # покупателя.
    _чисто()
    pid = _товар()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE users SET coins = 1000 WHERE user_id = %s"), (ПЕРВЫЙ,))
    conn.commit(); conn.close()
    def с_монетами(строки):
        return db.place_order(ПЕРВЫЙ, "qa", "Минск", строки, sum(x["qty"] * 10.0 for x in строки),
                              0, 0.01, 100, "Самовывоз", "", "cash", "", "", "confirmed")[0]
    старый = с_монетами([_строка(pid, "Black")])
    итог = {}
    где = lambda q, p: q.lstrip().startswith("UPDATE users SET coins = COALESCE(coins, 0) - %s")
    with _Остановка("qa-оформление", где) as ст:
        оформление = _поток("qa-оформление", lambda: с_монетами([_строка(pid, "White"), _строка(pid, "Black")]), итог)
        c("оформление списало монеты и стоит", ст.дошёл.wait(10))
        отмена = _поток("qa-отмена", lambda: db.cancel_order(старый), итог)
        c("отмена ждёт оформление", _ждёт(отмена, итог, "qa-отмена"))
    оформление.join(12); отмена.join(12)
    c("обе прошли — ни одна не оборвана",
      итог.get("qa-оформление", ("",))[0] == "ok" and итог.get("qa-отмена", ("",))[0] == "ok")
    c(f"на полке Black 4, White 4: {_полка(pid)}", _полка(pid) == {"Black": 4, "White": 4})
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("SELECT coins FROM users WHERE user_id = %s"), (ПЕРВЫЙ,))
    монет = int(cur.fetchone()["coins"]); conn.close()
    c(f"монеты сошлись: 1000 − 100 − 100 + 100 = 900 ({монет})", монет == 900)
    _чисто()
    return c.fails
