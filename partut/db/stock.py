"""
partut/db/stock.py — движение склада и подписки на поступление.

Шестой кусок, вынесенный из db.py.

Каждое списание записывается с закупочной ценой НА МОМЕНТ движения: цена
меняется, а во что обошёлся бой прошлого месяца — уже нет. Поэтому потери
считаются по сохранённой цене, а не по нынешней.

Главное правило склада: остаток меняется только ДВИЖЕНИЕМ — приходом,
списанием или пересчётом, — и каждое движение ложится в stock_moves с автором
и датой. Продажа движением не считается: её след — сам заказ. Всё остальное,
откуда бы оно ни пришло (окно склада, карточка товара, бот, завоз на точку),
идёт через stock_operation() ниже. Раньше карточка и бот ставили остаток
числом мимо истории, и на вопрос «куда делись пять штук» ответа не было.

Примитивы берутся ЧЕРЕЗ модуль (db.connect(), db._q()), а не копиями имён:
копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime
import json

from partut import db


# Причины движения. Приход прибавляет, пересчёт ставит насчитанное,
# остальные — списывают.
STOCK_REASONS = {
    "in":      "Приход",
    "broken":  "Брак или бой",
    "expired": "Просрочка",
    "lost":    "Недостача",
    "gift":    "Подарок или образец",
    "fix":     "Пересчёт",
}


class StockRefused(Exception):
    """Движение не записано, и в базе не поменялось ничего.

    code — для программы (экран решает, что показать), message — для
    человека: почему нельзя и что сделать вместо этого."""

    def __init__(self, code, message="", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message
        # Подробности для экрана: например, что именно уже записано под этим
        # ключом, — чтобы показать человеку, а не заставлять читать журнал.
        self.extra = extra


def _открытые(cur):
    """Составы невыданных заказов: [(заказ, позиция)] — позиция как в заказе.

    Товар снят с остатка и ещё не выдан — ровно те статусы, в которых заказ
    можно править (ORDER_EDITABLE): оформлен, оплачен, подтверждён."""
    места = ", ".join(["%s"] * len(db.ORDER_EDITABLE))
    cur.execute(db._q(f"SELECT id, status, delivery_method, items FROM orders "
                      f"WHERE status IN ({места}) ORDER BY id"), db.ORDER_EDITABLE)
    out = []
    for строка in cur.fetchall():
        try:
            состав = json.loads(строка["items"] or "[]")
        except (TypeError, ValueError):
            continue
        for позиция in состав:
            if not isinstance(позиция, dict):
                continue
            try:
                pid, штук = int(позиция.get("id") or 0), int(позиция.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if pid and штук > 0:
                out.append((строка, {"id": pid, "flavor": str(позиция.get("flavor") or ""), "qty": штук}))
    return out


def reserved_stock(cur=None):
    """Сколько товара ждёт в невыданных заказах: {(id товара, вариант): штук}.

    Заказ снимает товар с остатка сразу при оформлении, а отдают его позже.
    Между этими моментами штука ещё у магазина, но продать её уже нельзя —
    она обещана. Остаток в базе — то, что можно продать; здесь — то, что
    обещано сверх него.

    Без этого числа пересчёт врал: продавец считал полку вместе с отложенным,
    и система записывала «нашлись лишние». После выдачи в базе числилось
    больше, чем было на полке, — и это можно было продать.

    У товара без вариантов вариант — пустая строка. cur — чтобы считать
    внутри уже открытой транзакции (пересчёт склада, правка вариантов).
    """
    своё = cur is None
    if своё:
        conn = db.connect()
        cur = conn.cursor()
    позиции = _открытые(cur)
    if своё:
        conn.close()
    итог = {}
    for _, п in позиции:
        ключ = (п["id"], п["flavor"])
        итог[ключ] = итог.get(ключ, 0) + п["qty"]
    return итог


def reserved_orders(product_id, cur=None):
    """Невыданные заказы с этим товаром — для окна пересчёта.

    Один только итог «в заказах 3 шт» не отвечает на вопрос, который задаёт
    себе продавец с пересчётом в руках: а где эти три штуки? Заказ на
    самовывоз лежит на точке до выдачи, заказ с такси уже уехал, хотя
    выданным ещё не отмечен. Поэтому показываем каждый заказ, и продавец
    сам отмечает, чей товар он посчитал.

    [{"order_id", "status", "method", "flavor", "qty"}] по порядку заказов.
    """
    своё = cur is None
    if своё:
        conn = db.connect()
        cur = conn.cursor()
    позиции = _открытые(cur)
    if своё:
        conn.close()
    out = []
    for заказ, п in позиции:
        if п["id"] == int(product_id):
            out.append({"order_id": int(заказ["id"]), "status": заказ["status"],
                        "method": заказ["delivery_method"] or "", "flavor": п["flavor"], "qty": п["qty"]})
    return out


def _запереть(cur, product_id, flavor):
    """Замок на полку до конца транзакции: два движения одной полки идут друг
    за другом, а не бок о бок, — иначе оба прочитают одно и то же «было».

    Порядок тот же, что у заказа (place_order): сначала строка варианта,
    потом товар. Возьми их наоборот — и заказ с пересчётом, пришедшие в одну
    секунду, ждали бы друг друга вечно."""
    if db.USE_PG:
        if flavor:
            cur.execute("SELECT id FROM product_variants WHERE product_id = %s AND flavor = %s FOR UPDATE",
                        (product_id, flavor))
        else:
            cur.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (product_id,))
    else:
        # У SQLite замок один на всю базу, и берёт его первая же запись,
        # даже пустая: дальше внутри транзакции никто не вклинится.
        cur.execute("UPDATE products SET id = id WHERE id = ?", (product_id,))


def _отпечаток(product_id, reason, qty, flavor, cost, counted_orders, expected, counted_scope=None):
    """Содержимое операции одной строкой — чтобы узнать повтор НАВЕРНЯКА.

    Одного ключа мало: ответ потерялся, человек поправил число и нажал
    снова — ключ тот же, а операция уже другая. Применить её молча значило бы
    записать не то, что записано; поэтому такой повтор отклоняется."""
    return json.dumps({"p": int(product_id), "r": reason, "q": int(qty), "f": flavor or "",
                       "c": round(float(cost or 0), 2),
                       "o": sorted(int(x) for x in (counted_orders or [])),
                       "e": None if expected is None else int(expected),
                       "s": counted_scope or ""},
                      sort_keys=True, ensure_ascii=False)


def _повтор(cur, client_token, отпечаток):
    """Движение с этим ключом уже записано? Возвращает его разницу или None.

    Ключ придумывает экран — один на строку операции, и держит его, пока не
    получит ответ. Ответ потерялся в плохой сети, человек нажал ещё раз,
    запрос пришёл второй раз с тем же ключом — записывать его снова значило
    бы удвоить приход."""
    cur.execute(db._q("SELECT delta, reason, flavor, client_request FROM stock_moves WHERE client_token = %s"),
                (client_token,))
    r = cur.fetchone()
    if not r:
        return None
    if (r["client_request"] or "") != отпечаток:
        было = f"{STOCK_REASONS.get(r['reason'], r['reason'])} {int(r['delta']):+d}"
        raise StockRefused("token_reused",
                           f"Эта операция уже записана раньше ({было}), а сейчас пришла с другими "
                           f"данными. Посмотрите историю: если нужно ещё, запишите разницу отдельно.",
                           recorded={"reason": r["reason"], "delta": int(r["delta"]),
                                     "flavor": r["flavor"] or ""})
    return int(r["delta"])


def _остатки(cur, product_id, flavor):
    """Итог товара и остаток той полки, о которой речь (варианта или товара)."""
    cur.execute(db._q("SELECT stock FROM products WHERE id = %s"), (product_id,))
    r = cur.fetchone()
    итог = int(r["stock"] or 0) if r else 0
    полка = итог
    if flavor:
        cur.execute(db._q("SELECT stock FROM product_variants WHERE product_id = %s AND flavor = %s"),
                    (product_id, flavor))
        r = cur.fetchone()
        полка = int(r["stock"] or 0) if r else 0
    return {"stock": итог, "left": полка}


def _record_move(cur, product_id, flavor, delta, reason, cost, note="", admin_id=None,
                 client_token=None, client_request=None):
    """Строка в истории склада. Зовётся ВНУТРИ транзакции того, кто двигает
    остаток: остаток и запись о нём не должны разъезжаться — иначе появится
    изменение, которого «никто не делал»."""
    cur.execute(db._q("""INSERT INTO stock_moves (product_id, flavor, delta, reason, cost, note,
                                                  admin_id, created_at, client_token, client_request)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""),
                (product_id, flavor or None, int(delta), reason, float(cost or 0),
                 (note or "").strip()[:120], admin_id, db._now_str(),
                 client_token or None, client_request if client_token else None))


def stock_operation(product_id, reason, qty, flavor=None, cost=0, note="", admin_id=None,
                    client_token=None, counted_orders=None, expected=None, counted_scope=None):
    """Одно движение склада — так, как его задаёт человек: причина и число.

      • Приход: qty — сколько привезли, остаток растёт на qty.
      • Списание (брак, просрочка, недостача, подарок): qty — сколько ушло.
        Больше, чем свободно, списать нельзя: остальное обещано в заказах.
        Раньше остаток молча прижимался к нулю, а в историю ложилось всё
        число — и отчёт о потерях считал штуки, которых не было.
      • Пересчёт: qty — сколько НАСЧИТАЛИ; разницу считаем здесь, а не на
        экране: ошибка в знаке вскрылась бы только следующей недостачей.
        counted_scope — ЧТО посчитали, ответ человека: "free" — только
        свободное, "orders" — вместе с отложенным под заказы из
        counted_orders. Свободным станет насчитанное минус товар этих
        заказов. Если под заказы что-то отложено, а ответа нет — отказ:
        молчание не отличить от «не успело загрузиться», и пересчёт вслепую
        завысил бы остаток ровно на отложенное.
        expected — сколько было свободно, когда человек начинал: пришёл
        новый заказ или отмена — отказ, пересчёт устарел. Карточка товара
        держит число с момента открытия по той же причине.

    Всё одной транзакцией: проверка, остаток, итог товара и строка истории не
    могут разъехаться. client_token — ключ попытки (см. _повтор).

    Возвращает словарь: stock — итог товара; left — остаток той полки
    (варианта или товара); delta — на сколько изменилось; reserved — сколько
    этой полки в невыданных заказах; counted — сколько из них посчитали;
    short — пересчёт нашёл меньше обещанного, на столько; replay — это
    повтор, ничего не менялось. Нельзя — StockRefused, и не меняется ничего.
    """
    if reason not in STOCK_REASONS:
        raise StockRefused("bad_reason", "Неизвестная причина движения.")
    try:
        qty = int(qty)
        expected = None if expected is None else int(expected)
        заказы = sorted({int(x) for x in (counted_orders or [])})
    except (TypeError, ValueError):
        raise StockRefused("bad_number", "Количество — целое число.")
    if qty < 0 or (reason != "fix" and qty == 0):
        raise StockRefused("bad_number", "Укажите количество больше нуля.")
    if reason != "fix" and (заказы or counted_scope):
        raise StockRefused("bad_input", "Заказы отмечают только при пересчёте.")
    if counted_scope not in (None, "free", "orders"):
        raise StockRefused("bad_input", "Непонятно, что посчитали: только свободное или вместе с отложенным.")
    if counted_scope == "free" and заказы:
        raise StockRefused("bad_input", "Отмечены заказы, а выбрано «только свободное» — выберите что-то одно.")
    flavor = str(flavor or "").strip() or None
    отпечаток = _отпечаток(product_id, reason, qty, flavor, cost, заказы, expected, counted_scope)

    conn = db.connect()
    cur = conn.cursor()
    try:
        # 1. Замок, и только потом всё остальное: повтор, прочитанный ДО замка,
        #    мог бы не увидеть параллельный первый запрос.
        _запереть(cur, product_id, flavor)
        if client_token:
            прежняя = _повтор(cur, client_token, отпечаток)
            if прежняя is not None:
                итог = _остатки(cur, product_id, flavor)
                conn.rollback()
                conn.close()
                return {**итог, "delta": прежняя, "reserved": 0, "counted": 0, "short": 0, "replay": True}

        # 2. Что за полка и что на ней сейчас.
        cur.execute(db._q("SELECT * FROM products WHERE id = %s"), (product_id,))
        товар = cur.fetchone()
        if not товар:
            raise StockRefused("not_found", "Товар не найден — возможно, его уже убрали с точки.")
        cur.execute(db._q("SELECT flavor, stock FROM product_variants WHERE product_id = %s"), (product_id,))
        варианты = {r["flavor"]: int(r["stock"] or 0) for r in cur.fetchall()}
        # У товара с вариантами остаток — сумма вариантов. Движение мимо них
        # меняло итог, которого на полке нет: следующий пересчёт итога (любой
        # заказ) молча стирал эти штуки.
        if варианты and not flavor:
            raise StockRefused("need_variant",
                               "Остаток этого товара ведётся по вариантам — выберите, по какому записать.")
        if flavor and not варианты:
            raise StockRefused("no_variants",
                               "У этого товара нет вариантов — обновите экран и запишите заново.")
        # Вариант убрали, пока было открыто окно склада. Раньше такой приход
        # «записывался»: в истории +10, а остаток не менялся вовсе.
        if flavor and flavor not in варианты:
            raise StockRefused("variant_missing",
                               f"Варианта «{flavor}» у товара больше нет — обновите экран.")
        было = варианты[flavor] if flavor else int(товар["stock"] or 0)
        if expected is not None and expected != было:
            raise StockRefused("stock_conflict",
                               f"Пока вы считали, остаток изменился: было {expected}, сейчас {было} "
                               f"(новый заказ или отмена). Обновите экран и запишите заново.")

        # 3. Разница — по причине, а не по знаку от клиента.
        по_заказам = {}             # заказ -> сколько в нём этой полки
        for з, п in _открытые(cur):
            if п["id"] == int(product_id) and п["flavor"] == (flavor or ""):
                по_заказам[int(з["id"])] = по_заказам.get(int(з["id"]), 0) + п["qty"]
        резерв = sum(по_заказам.values())
        посчитано = 0
        нехватка = 0
        if reason == "fix":
            # Под заказы что-то отложено, а человек не сказал, считал ли он
            # это: экран не успел загрузить заказы или это старая страница.
            # Принять такое как «только свободное» — значит завысить остаток.
            if резерв and counted_scope is None:
                raise StockRefused("need_counted_choice",
                                   f"Под невыданные заказы отложено {резерв} шт. Отметьте, считали ли вы "
                                   f"отложенное вместе со свободным, — иначе пересчёт завысит остаток. "
                                   f"Если вопроса на экране нет, обновите приложение.")
            if заказы:
                лишние = [x for x in заказы if x not in по_заказам]
                if лишние:
                    raise StockRefused("orders_changed",
                                       "Заказ " + ", ".join(f"№{x}" for x in лишние)
                                       + " уже выдан, отменён или без этого товара. "
                                         "Обновите экран и отметьте заказы заново.")
                посчитано = sum(по_заказам[x] for x in заказы)
            цель = qty - посчитано
            if цель < 0:
                нехватка, цель = -цель, 0
            delta = цель - было
            if delta == 0:
                if нехватка:
                    raise StockRefused("short",
                                       f"Насчитали {qty} шт, а в отмеченных заказах {посчитано}: "
                                       f"не хватает {нехватка}. Свободного и так ноль — поправьте "
                                       f"или отмените заказ, которому не хватило.")
                raise StockRefused("no_change", "Столько и числится — записывать нечего.")
        elif reason == "in":
            delta = qty
        else:
            if qty > было:
                raise StockRefused("not_enough",
                                   f"Свободно только {было} шт"
                                   + (f", ещё {резерв} — в невыданных заказах: их списывают, только "
                                      f"поправив или отменив заказ." if резерв else "."))
            delta = -qty

        # При движении цену никто не вводит, кроме прихода по новой цене:
        # берём закупочную товара на этот момент — иначе списание посчиталось
        # бы нулём и убыток стал бы невидимым.
        цена = float(cost or 0) or float(товар["cost"] or 0)
        if reason == "fix" and посчитано:
            своя = (note or "").strip()
            note = f"насчитали {qty}, из них {посчитано} под заказы" + (f" · {своя}" if своя else "")

        # 4. Само движение: полка, итог товара, закупка, строка истории.
        if flavor:
            cur.execute(db._q("UPDATE product_variants SET stock = stock + %s "
                              "WHERE product_id = %s AND flavor = %s"), (delta, product_id, flavor))
            cur.execute(db._q("""UPDATE products SET stock =
                              (SELECT COALESCE(SUM(stock), 0) FROM product_variants WHERE product_id = %s)
                              WHERE id = %s"""), (product_id, product_id))
        else:
            cur.execute(db._q("UPDATE products SET stock = stock + %s WHERE id = %s"), (delta, product_id))
        # Приход по новой цене обновляет закупочную: считать прибыль по старой
        # цене после подорожания — значит обманывать себя.
        if reason == "in" and cost and float(cost) > 0:
            cur.execute(db._q("UPDATE products SET cost = %s WHERE id = %s"), (float(cost), product_id))
        _record_move(cur, product_id, flavor, delta, reason, цена, note, admin_id,
                     client_token, отпечаток)
        итог = _остатки(cur, product_id, flavor)
        conn.commit()
    except StockRefused:
        conn.rollback()
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        # Два запроса с одним ключом всё-таки столкнулись на вставке:
        # уникальный индекс пропустил один. Второму отдаём первый — это не
        # сбой, а тот же самый повтор (или отказ, если содержимое другое).
        if client_token:
            with db.connect() as c2:
                cur2 = c2.cursor()
                прежняя = _повтор(cur2, client_token, отпечаток)
                if прежняя is not None:
                    return {**_остатки(cur2, product_id, flavor), "delta": прежняя,
                            "reserved": 0, "counted": 0, "short": 0, "replay": True}
        raise
    conn.close()
    return {**итог, "delta": delta, "reserved": резерв, "counted": посчитано,
            "short": нехватка, "replay": False}


def get_stock_moves(product_id=None, limit=100, city=None):
    """Движения склада. city ограничивает выборку одной точкой: без товара в
    запросе продавец иначе получал бы всю историю магазина — а по ней видно
    завоз и списания соседних точек."""
    conn = db.connect()
    cur = conn.cursor()
    if product_id:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE m.product_id = %s ORDER BY m.id DESC LIMIT %s"""), (product_id, limit))
    elif city:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          WHERE p.city = %s ORDER BY m.id DESC LIMIT %s"""), (city, limit))
    else:
        cur.execute(db._q("""SELECT m.*, p.name AS product FROM stock_moves m
                          LEFT JOIN products p ON p.id = m.product_id
                          ORDER BY m.id DESC LIMIT %s"""), (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def stock_losses(days=None):
    """Во сколько обошлись списания за период — по закупочной цене на момент
    движения. Это настоящие деньги, и владелец должен их видеть."""
    conn = db.connect()
    cur = conn.cursor()
    cutoff = ((db.shop_now() - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00")
              if days else None)
    sql = """SELECT reason, SUM(-delta) AS qty,
                    SUM(-delta * COALESCE(NULLIF(cost, 0), 0)) AS money
             FROM stock_moves WHERE delta < 0"""
    if cutoff:
        cur.execute(db._q(sql + " AND created_at >= %s GROUP BY reason"), (cutoff,))
    else:
        cur.execute(sql + " GROUP BY reason")
    rows = [{"reason": r["reason"], "qty": int(r["qty"] or 0), "money": round(float(r["money"] or 0), 2)}
            for r in cur.fetchall()]
    conn.close()
    return sorted(rows, key=lambda r: -r["money"])


def add_stock_alert(product_id, user_id):
    """Покупатель ждёт этот товар. Повторное нажатие не создаёт дубль."""
    conn = db.connect()
    cur = conn.cursor()
    sql = ("INSERT INTO stock_alerts (product_id, user_id, created_at) VALUES (%s, %s, %s) "
           + ("ON CONFLICT (product_id, user_id) DO NOTHING" if db.USE_PG else ""))
    if db.USE_PG:
        cur.execute(sql, (product_id, user_id, db._now_str()))
    else:
        cur.execute("INSERT OR IGNORE INTO stock_alerts (product_id, user_id, created_at) "
                    "VALUES (?, ?, ?)", (product_id, user_id, db._now_str()))
    conn.commit()
    conn.close()


def remove_stock_alert(product_id, user_id):
    """Покупатель передумал ждать. Подписка ставилась одним нажатием, а снять её
    было нельзя вовсе — оставалось терпеть сообщение о товаре, который уже не
    нужен, или блокировать бота."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM stock_alerts WHERE product_id = %s AND user_id = %s"),
                (product_id, user_id))
    conn.commit()
    conn.close()


def stock_alerts_ready():
    """Кого пора обрадовать: подписки на товары, которые СНОВА в наличии.
    Возвращает [(user_id, product_id, название)]."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("""SELECT a.user_id, a.product_id, p.name
                   FROM stock_alerts a JOIN products p ON p.id = a.product_id
                   WHERE p.stock > 0""")
    rows = [(int(r["user_id"]), int(r["product_id"]), r["name"]) for r in cur.fetchall()]
    conn.close()
    return rows


def clear_stock_alerts(product_id):
    """Сообщили — подписки на этот товар больше не нужны."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("DELETE FROM stock_alerts WHERE product_id = %s"), (product_id,))
    conn.commit()
    conn.close()


def stock_alert_counts():
    """{товар: сколько ждут} — админу видно, что именно стоит завезти."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT product_id, COUNT(*) AS n FROM stock_alerts GROUP BY product_id")
    out = {int(r["product_id"]): int(r["n"]) for r in cur.fetchall()}
    conn.close()
    return out
