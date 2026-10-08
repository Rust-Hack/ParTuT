"""«🗄 Архив»: история товаров одним заходом, а не походом в базу на товар.

Нагрузка на копии (8.10.2026, 500 товаров на точках, 8 000 заказов): список
архива спрашивал историю каждого товара отдельно — подсчёт движений и полный
перебор заказов с разбором состава на КАЖДЫЙ товар. Шестьдесят товаров в
архиве — 94 мс в одиночку и больше секунды под нагрузкой. Теперь история
читается для всего архива разом (db.products_with_history).

Проверяем две вещи: ответ тот же, что у поштучной проверки (в том числе на
неудобных составах заказов), и число запросов к базе не растёт вместе с
архивом.
"""
import datetime

from _common import db, client, Checker, as_admin


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _в_архиве(имя, город="Минск"):
    conn = db.connect(); cur = conn.cursor()
    pid = db._insert_id(cur, """INSERT INTO products (city, category, name, price, stock, archived, cost, hidden)
                                VALUES (%s, 'accessories', %s, 15, 0, 1, 7, 0)""", (город, имя))
    conn.commit(); conn.close()
    return pid


def _движение(pid):
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("""INSERT INTO stock_moves (product_id, flavor, delta, reason, cost, note, admin_id, created_at)
                         VALUES (%s, NULL, 5, 'supply', 7, 'приход', 1, '2026-09-01 10:00')"""), (pid,))
    conn.commit(); conn.close()


def _заказ(состав_как_текст):
    """Заказ с составом ровно в том виде, в каком он лежит в базе, — строкой."""
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("""INSERT INTO orders (user_id, username, city, items, total, pickup_time, status, created_at)
                         VALUES (9601, 'qa', 'Минск', %s, 10, '', 'issued', %s)"""),
                (состав_как_текст, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()


def _после(функция):
    """Битые составы заказов, нарочно положенные здесь, не должны достаться
    следующим проверкам — за собой убираем при любом исходе."""
    def обёртка():
        try:
            return функция()
        finally:
            _чисто()
    обёртка.__name__, обёртка.__doc__ = функция.__name__, функция.__doc__
    return обёртка


def _архив():
    return {x["id"]: x for x in client.post("/api/admin/archive", json={"initData": "x"}).get_json()["items"]}


class _КурсорСоСчётом:
    def __init__(self, cur, счёт):
        self._cur, self._счёт = cur, счёт

    def execute(self, *a, **k):
        self._счёт[0] += 1
        return self._cur.execute(*a, **k)

    def executemany(self, *a, **k):
        self._счёт[0] += 1
        return self._cur.executemany(*a, **k)

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, имя):
        return getattr(self._cur, имя)


def _запросов_к_базе(действие):
    """Сколько запросов ушло в базу за время действия — через обёртку курсора
    в самом db, то есть одинаково на SQLite и на Postgres."""
    счёт = [0]
    было = db._Conn.cursor
    db._Conn.cursor = lambda conn, *a, **k: _КурсорСоСчётом(было(conn, *a, **k), счёт)
    try:
        действие()
    finally:
        db._Conn.cursor = было
    return счёт[0]


@_после
def run_тот_же_ответ():
    c = Checker("Архив: история одним заходом — тот же ответ, что поштучно")
    _чисто(); as_admin()

    с_движением = _в_архиве("QA движение")
    _движение(с_движением)
    в_заказе = _в_архиве("QA в заказе")
    _заказ(f'[{{"id": {в_заказе}, "name": "QA в заказе", "price": 10.0, "qty": 1}}]')
    старый_ключ = _в_архиве("QA product_id")
    _заказ(f'[{{"product_id": {старый_ключ}, "name": "QA", "price": 10.0, "qty": 1}}]')
    # Число товара встречается в заказе, но не как его id: чужой id, где оно
    # подстрока, цена и название. LIKE такой заказ пропускает, разбор — нет.
    похожий = _в_архиве("QA похожий")
    _заказ(f'[{{"id": {похожий}0, "name": "Товар {похожий}", "price": {похожий}.5, "qty": 1}}]')
    битый = _в_архиве("QA битый состав")
    _заказ(f'[{{"id": {битый}, "name": "обрезано')
    с_мусором = _в_архиве("QA мусорный id рядом")
    _заказ(f'[{{"id": "abc", "name": "QA"}}, {{"id": {с_мусором}, "name": "QA", "price": 1.0, "qty": 1}}]')
    не_список = _в_архиве("QA состав не список")
    _заказ(f'{{"id": {не_список}}}')
    чистый = _в_архиве("QA без истории")

    все = [с_движением, в_заказе, старый_ключ, похожий, битый, с_мусором, не_список, чистый]
    ждём = {с_движением, в_заказе, старый_ключ, с_мусором}
    поштучно = {p for p in все if (lambda и: и["moves"] or и["orders"])(db.product_history(p))}
    разом = db.products_with_history(все)
    c(f"поштучная проверка видит историю ровно у {len(ждём)}: движение, заказ, старый ключ, заказ с мусором рядом",
      поштучно == ждём)
    c("одним заходом — то же самое", разом == поштучно)
    c("пустой список — пустой ответ, без запроса", db.products_with_history([]) == set())

    архив = _архив()
    c("«удалить насовсем» в списке архива — ровно у товаров без истории",
      {p for p in все if архив[p]["can_delete"]} == set(все) - ждём)
    return c.fails


@_после
def run_запросов_не_больше_с_ростом_архива():
    c = Checker("Архив: число запросов к базе не растёт вместе с архивом")
    _чисто(); as_admin()
    for i in range(3):
        _в_архиве(f"QA малый {i}")
    _движение(_в_архиве("QA с движением"))
    малый = _запросов_к_базе(_архив)
    for i in range(20):
        pid = _в_архиве(f"QA большой {i}")
        if i % 2:
            _движение(pid)
        else:
            _заказ(f'[{{"id": {pid}, "name": "QA", "price": 10.0, "qty": 1}}]')
    большой = _запросов_к_базе(_архив)
    c(f"4 товара в архиве — {малый} запросов, 24 — {большой}: столько же", малый == большой)
    c(f"и их немного: {большой} ≤ 5", большой <= 5)
    return c.fails
