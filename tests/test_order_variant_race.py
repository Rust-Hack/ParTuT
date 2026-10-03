"""Отмена заказа и удаление варианта одновременно (приёмка SF-01-R1, 4.10.2026).

В заказе вкус «black», на полке его уже поправили в «Black». Раньше:
  • резерв «под заказом» сравнивал названия буква в букву — «Black» считался
    свободным, и удалить его давали, хоть он и обещан заказу;
  • отмена читала варианты без замка, и между её поиском и возвратом вариант
    успевали удалить в другом соединении: возврат менял ноль строк, заказ
    становился «отменён», а 3 штуки пропадали.

Теперь резерв узнаёт вкус без учёта регистра (как отмена), отмена и правка
заказа держат замок на варианты от поиска до возврата и проверяют, что
возврат записался. Порядок операций задаётся барьером — сами операции
настоящие, в разных соединениях. Гонки — только на Postgres: в SQLite замок
один на всю базу. Проверка резерва — на обеих.
"""
import threading
import time

from _common import db, Checker

from partut.db import orders as заказы_модуль

ПОКУПАТЕЛЬ = 79001
ВЛАДЕЛЕЦ = 1


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _товар():
    """Аксессуар: black 3 и White 3; заказ забрал все 3 black; на полке — «Black»."""
    m = db.add_model("accessories", "QA Шнурок", "QA", "", {}, ["black", "White"])
    pid = db.create_point_product(m, "Минск", 10.0, 5.0, variants=[{"flavor": "black", "stock": 3},
                                                                   {"flavor": "White", "stock": 3}])
    oid, *_ = db.place_order(ПОКУПАТЕЛЬ, "qa", "Минск",
                             [{"id": pid, "name": "QA Шнурок — black", "price": 10.0, "qty": 3, "flavor": "black"}],
                             30.0, 0, 0.01, 0, "Самовывоз", "", "cash", "", "", "confirmed")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE product_variants SET flavor = %s WHERE product_id = %s AND flavor = %s"),
                ("Black", pid, "black"))
    conn.commit(); conn.close()
    return pid, oid


def _полка(pid):
    return {v["flavor"]: v["stock"] for v in db.get_variants(pid)}


def _в_потоке(fn, итог, ключ):
    def run():
        try:
            итог[ключ] = fn()
        except BaseException as e:            # noqa: BLE001 — нужен сам отказ
            итог[ключ] = e
    t = threading.Thread(target=run)
    t.start()
    return t


def _удалить(pid):
    return lambda: db.change_variants(pid, remove=["Black"], admin_id=ВЛАДЕЛЕЦ)


def run():
    c = Checker("Отмена заказа и удаление варианта одновременно (SF-01-R1)")
    _чисто()
    db.ensure_user(ПОКУПАТЕЛЬ)

    # ---- Резерв узнаёт «black» в «Black» (обе базы) ----
    pid, oid = _товар()
    c("резерв «под заказом» — на «Black», хоть в заказе «black»",
      db.reserved_stock().get((pid, "Black")) == 3 and (pid, "black") not in db.reserved_stock())
    отказ = None
    try:
        db.change_variants(pid, remove=["Black"], admin_id=ВЛАДЕЛЕЦ)
    except db.StockRefused as e:
        отказ = e
    c("удалить вариант под заказом нельзя — отказ «reserved»", отказ is not None and отказ.code == "reserved")
    db.cancel_order(oid)
    c("отмена вернула 3 шт на «Black», всего снова 6", _полка(pid) == {"Black": 3, "White": 3}
      and db.get_product(pid)["stock"] == 6)

    if not db.USE_PG:
        _чисто()
        return c.fails

    # ---- Порядок 1: отмена нашла вариант, потом пришло удаление ----
    _чисто()
    pid, oid = _товар()
    дошла, можно = threading.Event(), threading.Event()
    настоящая = заказы_модуль._полка_заказа

    def держит(cur, items):
        полка = настоящая(cur, items)
        дошла.set()
        можно.wait(10)
        return полка
    заказы_модуль._полка_заказа = держит
    итог = {}
    try:
        отмена = _в_потоке(lambda: db.cancel_order(oid), итог, "отмена")
        c("отмена нашла вариант и держит его", дошла.wait(10))
        удаление = _в_потоке(_удалить(pid), итог, "удаление")
        time.sleep(0.6)
        c("удаление ждёт, пока отмена не закончит (замок на варианте)", удаление.is_alive() and "удаление" not in итог)
    finally:
        можно.set()
        заказы_модуль._полка_заказа = настоящая
    отмена.join(10); удаление.join(10)
    c("обе операции закончились — взаимной блокировки нет", not отмена.is_alive() and not удаление.is_alive())
    c("отмена прошла", not isinstance(итог.get("отмена"), BaseException) and db.get_order(oid)["status"] == "canceled")
    c("удаление после отмены — отказ: на «Black» теперь 3 шт, без списания не убрать",
      isinstance(итог.get("удаление"), db.StockRefused) and итог["удаление"].code == "has_stock")
    c("3 шт вернулись на «Black», всего 6 — ничего не пропало",
      _полка(pid) == {"Black": 3, "White": 3} and db.get_product(pid)["stock"] == 6)

    # ---- Порядок 2: удаление взяло варианты, потом пришла отмена ----
    _чисто()
    pid, oid = _товар()
    дошло, можно = threading.Event(), threading.Event()
    настоящий_резерв = db.reserved_stock

    def резерв_держит(cur=None):
        r = настоящий_резерв(cur)
        дошло.set()
        можно.wait(10)
        return r
    db.reserved_stock = резерв_держит
    итог = {}
    try:
        удаление = _в_потоке(_удалить(pid), итог, "удаление")
        c("удаление заперло варианты и считает резерв", дошло.wait(10))
        db.reserved_stock = настоящий_резерв
        отмена = _в_потоке(lambda: db.cancel_order(oid), итог, "отмена")
        time.sleep(0.6)
        c("отмена ждёт, пока удаление не закончит", отмена.is_alive() and "отмена" not in итог)
    finally:
        можно.set()
        db.reserved_stock = настоящий_резерв
    удаление.join(10); отмена.join(10)
    c("обе закончились — взаимной блокировки нет", not удаление.is_alive() and not отмена.is_alive())
    c("удаление — отказ «reserved»: вариант обещан заказу",
      isinstance(итог.get("удаление"), db.StockRefused) and итог["удаление"].code == "reserved")
    c("отмена прошла и вернула 3 шт: всего 6", db.get_order(oid)["status"] == "canceled"
      and _полка(pid) == {"Black": 3, "White": 3} and db.get_product(pid)["stock"] == 6)

    # ---- Уменьшение состава заказа и удаление одновременно ----
    _чисто()
    pid, oid = _товар()
    дошла, можно = threading.Event(), threading.Event()
    заказы_модуль._полка_заказа = держит
    итог = {}
    try:
        правка = _в_потоке(lambda: db.update_order_items(oid, {0: 1}, 0.01), итог, "правка")
        c("правка нашла вариант и держит его", дошла.wait(10))
        удаление = _в_потоке(_удалить(pid), итог, "удаление")
        time.sleep(0.6)
        c("удаление ждёт правку", удаление.is_alive())
    finally:
        можно.set()
        заказы_модуль._полка_заказа = настоящая
    правка.join(10); удаление.join(10)
    c("правка «3 → 1» прошла, 2 шт вернулись на «Black»",
      isinstance(итог.get("правка"), tuple) and итог["правка"][0] is not None and _полка(pid).get("Black") == 2)
    c("удаление после правки — отказ (на полке 2 шт, 1 обещана заказу)",
      isinstance(итог.get("удаление"), db.StockRefused))
    c("всего 5 на полке + 1 в заказе — ничего не пропало", db.get_product(pid)["stock"] == 5)
    _чисто()
    return c.fails
