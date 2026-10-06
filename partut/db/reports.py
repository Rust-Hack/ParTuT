"""
partut/db/reports.py — цифры магазина: выручка, прибыль, монеты, что с чем берут.

Десятый кусок, вынесенный из ядра базы. Здесь не хранится ничего — только
считается по уже записанному. Отдельный файл потому, что читают его иначе, чем
остальную базу: сюда смотрят, решая, чем торговать дальше, и ошибка тут стоит
не сломанной кнопки, а неверного решения.

Ровно здесь всплыла одинарная точность: SUM(real) в Postgres возвращает тоже
real, и выручка копилась в четырёх байтах — см. перенос 0005 в ядре.

Примитивы и соседние функции берутся ЧЕРЕЗ модуль (db.connect(), db._q()),
а не копиями имён: копия не заметила бы подмены в тестах — см. partut/db/raffles.py.
"""

import datetime
import json

from partut import db
from partut import money


def inc_stat(key, delta=1):
    """Увеличивает счётчик игры (прокруты/ставки/выплаты)."""
    conn = db.connect()
    cur = conn.cursor()
    if db.USE_PG:
        cur.execute("""INSERT INTO game_stats (key, n) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET n = game_stats.n + EXCLUDED.n""", (key, int(delta)))
    else:
        cur.execute("INSERT INTO game_stats (key, n) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET n = n + ?",
                    (key, int(delta), int(delta)))
    conn.commit()
    conn.close()


def reset_statistics(orders=True, games=True):
    """Сброс тестовой статистики: удаляет заказы и/или обнуляет игровые счётчики.
    Возвращает {orders: сколько_удалено}."""
    conn = db.connect()
    cur = conn.cursor()
    n_orders = 0
    if orders:
        cur.execute("SELECT COUNT(*) AS c FROM orders")
        n_orders = cur.fetchone()["c"]
        cur.execute("DELETE FROM orders")
    if games:
        cur.execute("DELETE FROM game_stats")
    conn.commit()
    conn.close()
    return {"orders": n_orders}


# Продажа на точке — заказ без покупателя (user_id = 0). В выручку она идёт, а
# в покупателей — нет: иначе «Покупателей» и «Покупали хоть раз» вырастали на
# одного несуществующего человека с первой же продажей у прилавка.
_НЕ_ТОЧКА = " AND COALESCE(source, '') <> 'point'"


def get_business_stats(days=None):
    """Сводная бизнес-аналитика за период (days=None → всё время). Считается в SQL.
    Возвращает выручку, заказы, средний чек, воронку статусов, по городам, по дням,
    топ товаров, метрики пользователей и монеты в обороте."""
    now = db.shop_now()
    cutoff = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00") if days else None
    conn = db.connect()
    cur = conn.cursor()

    # Выручка/заказы (выданные) за период — по дню ВЫДАЧИ (db.ДЕНЬ_ВЫДАЧИ):
    # выручка появляется, когда товар отдан и деньги в кассе, а не когда
    # заказ оформили (приёмка DAY-01, решение владельца 7.10.2026). Воронка
    # статусов ниже — про оформленные заказы и считается по дню оформления.
    выдан = db.ДЕНЬ_ВЫДАЧИ
    if cutoff:
        cur.execute(db._q(f"SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND {выдан} >= %s"), (cutoff,))
    else:
        cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued'")
    row = cur.fetchone()
    issued_count = row["c"]
    revenue = float(row["s"] or 0)
    avg_check = revenue / issued_count if issued_count else 0

    # В работе (текущий пайплайн — не зависит от периода)
    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders WHERE status IN ('paid','confirmed')")
    row = cur.fetchone()
    inwork_count = row["c"]
    inwork_total = float(row["s"] or 0)

    # Воронка статусов за период
    if cutoff:
        cur.execute(db._q("SELECT status AS st, COUNT(*) AS c FROM orders WHERE created_at >= %s GROUP BY status"), (cutoff,))
    else:
        cur.execute("SELECT status AS st, COUNT(*) AS c FROM orders GROUP BY status")
    by_status = {r["st"]: r["c"] for r in cur.fetchall()}

    # Выручка по точкам (выданные, период)
    if cutoff:
        cur.execute(db._q(f"SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' AND {выдан} >= %s GROUP BY city ORDER BY s DESC"), (cutoff,))
    else:
        cur.execute("SELECT city AS ct, COALESCE(SUM(total),0) AS s FROM orders WHERE status='issued' GROUP BY city ORDER BY s DESC")
    revenue_by_city = [{"city": r["ct"], "total": round(float(r["s"] or 0), 2)} for r in cur.fetchall()]

    # По дням (для графика): последние N дней, пробелы = 0
    n_days = days if days else 30
    start = now - datetime.timedelta(days=n_days - 1)
    start_str = start.strftime("%Y-%m-%d 00:00")
    cur.execute(db._q(f"SELECT substr({выдан},1,10) AS d, COUNT(*) AS c, COALESCE(SUM(total),0) AS s "
                      f"FROM orders WHERE status='issued' AND {выдан} >= %s GROUP BY substr({выдан},1,10)"), (start_str,))
    day_map = {r["d"]: (r["c"], float(r["s"] or 0)) for r in cur.fetchall()}
    daily = []
    for i in range(n_days):
        d = (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        c, s = day_map.get(d, (0, 0.0))
        daily.append({"date": d, "orders": c, "revenue": round(s, 2)})

    # Топ товаров (парсим JSON только выданных за период)
    if cutoff:
        cur.execute(db._q(f"SELECT items, coins_used, promo_discount FROM orders "
                          f"WHERE status='issued' AND {выдан} >= %s"), (cutoff,))
    else:
        cur.execute("SELECT items, coins_used, promo_discount FROM orders WHERE status='issued'")
    qty_by_name, rev_by_name, profit_by_name = {}, {}, {}
    profit = 0.0            # прибыль ТОЛЬКО по позициям с известной закупочной ценой
    revenue_known = 0.0     # выручка этих же позиций — чтобы посчитать наценку
    revenue_unknown = 0.0   # выручка там, где закупочная цена не заполнена
    for r in cur.fetchall():
        try:
            # Как скидка ложится на позиции — правило одно на всех, оно в
            # partut/money.py. Выгрузка в файл считает тем же кодом, иначе
            # файл и экран однажды разойдутся в числах.
            for стр in money.разложить_заказ(json.loads(r["items"]), r["coins_used"],
                                             r["promo_discount"], db.COIN_VALUE):
                nm = стр["name"]
                qty_by_name[nm] = qty_by_name.get(nm, 0) + стр["qty"]
                rev_by_name[nm] = rev_by_name.get(nm, 0) + стр["revenue"]
                if стр["profit"] is None:
                    revenue_unknown += стр["revenue"]
                else:
                    profit += стр["profit"]
                    revenue_known += стр["revenue"]
                    profit_by_name[nm] = profit_by_name.get(nm, 0) + стр["profit"]
        except (TypeError, ValueError):
            pass
    top = [{"name": n, "qty": q, "revenue": round(rev_by_name.get(n, 0), 2),
            "profit": (round(profit_by_name[n], 2) if n in profit_by_name else None)}
           for n, q in sorted(qty_by_name.items(), key=lambda x: -x[1])[:8]]
    margin = (profit / revenue_known * 100) if revenue_known else 0

    # Пользователи
    cur.execute("SELECT COUNT(*) AS c FROM users")
    users_total = cur.fetchone()["c"]
    if cutoff:
        cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s"), (cutoff,))
        new_users = cur.fetchone()["c"]
        cur.execute(db._q(f"SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued' AND {выдан} >= %s"
                          f"{_НЕ_ТОЧКА}"), (cutoff,))
        buyers_period = cur.fetchone()["c"]
    else:
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at IS NOT NULL")
        new_users = cur.fetchone()["c"]
        cur.execute(f"SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued'{_НЕ_ТОЧКА}")
        buyers_period = cur.fetchone()["c"]
    cur.execute(f"SELECT COUNT(*) AS c FROM (SELECT user_id FROM orders WHERE status='issued'{_НЕ_ТОЧКА} "
                f"GROUP BY user_id HAVING COUNT(*) >= 2) t")
    repeat_buyers = cur.fetchone()["c"]
    cur.execute(f"SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status='issued'{_НЕ_ТОЧКА}")
    total_buyers = cur.fetchone()["c"]

    # Монеты в обороте
    cur.execute("SELECT COALESCE(SUM(coins),0) AS s FROM users")
    coins_circulation = int(cur.fetchone()["s"] or 0)

    # Сравнение с ПРЕДЫДУЩИМ таким же окном (только для конкретного периода)
    prev = None
    if days:
        prev_start = (now - datetime.timedelta(days=2 * days - 1)).strftime("%Y-%m-%d 00:00")
        cur.execute(db._q(f"SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS s FROM orders "
                          f"WHERE status='issued' AND {выдан} >= %s AND {выдан} < %s"), (prev_start, cutoff))
        r = cur.fetchone()
        p_cnt = r["c"]
        p_rev = float(r["s"] or 0)
        cur.execute(db._q(f"SELECT COUNT(DISTINCT user_id) AS c FROM orders "
                          f"WHERE status='issued' AND {выдан} >= %s AND {выдан} < %s{_НЕ_ТОЧКА}"), (prev_start, cutoff))
        p_buyers = cur.fetchone()["c"]
        cur.execute(db._q("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s AND created_at < %s"), (prev_start, cutoff))
        p_new = cur.fetchone()["c"]
        prev = {"revenue": round(p_rev, 2), "orders": p_cnt,
                "avg_check": round(p_rev / p_cnt, 2) if p_cnt else 0,
                "buyers": p_buyers, "new_users": p_new}

    # Какие именно товары остались без закупочной цены. Предупреждение «выручка
    # на N Br в прибыль не попала» говорит размер беды, но не говорит, где она:
    # владелец видел цифру и не знал, что открыть. Теперь знает.
    cur.execute("""SELECT name, city FROM products
                    WHERE (cost IS NULL OR cost <= 0) AND (hidden IS NULL OR hidden = 0)
                      AND COALESCE(archived, 0) = 0
                    ORDER BY name""")
    без_закупки = [{"name": r["name"], "city": r["city"]} for r in cur.fetchall()]

    conn.close()
    return {
        "period_days": days, "prev": prev,
        "no_cost": без_закупки[:30], "no_cost_total": len(без_закупки),
        "revenue": round(revenue, 2), "orders": issued_count, "avg_check": round(avg_check, 2),
        "profit": round(profit, 2), "margin": round(margin, 1),
        "revenue_unknown_cost": round(revenue_unknown, 2),   # выручка без закупочной цены
        "inwork_total": round(inwork_total, 2), "inwork_count": inwork_count,
        "by_status": by_status, "revenue_by_city": revenue_by_city, "daily": daily, "top": top,
        "users_total": users_total, "new_users": new_users, "buyers_period": buyers_period,
        "repeat_buyers": repeat_buyers, "total_buyers": total_buyers,
        "coins_circulation": coins_circulation,
    }


def coin_flow(days=None):
    """Сколько монет роздано и списано за период, с разбивкой по причинам.

    Считается по летописи, а не по балансам: розданное и уже потраченное на
    балансах не видно вовсе, и раздача выглядела бы меньше, чем есть.
    """
    conn = db.connect()
    cur = conn.cursor()
    where, params = "", ()
    if days:
        cutoff = (db.shop_now() - datetime.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M")
        where, params = "WHERE created_at >= %s", (cutoff,)
    cur.execute(db._q(f"SELECT reason AS r, "
                   f"COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS plus, "
                   f"COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END), 0) AS minus "
                   f"FROM coin_log {where} GROUP BY reason"), params)
    rows = cur.fetchall()
    conn.close()
    granted = sum(int(r["plus"]) for r in rows)
    spent = sum(int(r["minus"]) for r in rows)
    by_reason = sorted(
        ({"reason": r["r"] or "other",
          "label": db.COIN_REASONS.get(r["r"] or "other", "Прочее"),
          "granted": int(r["plus"]), "spent": int(r["minus"])} for r in rows),
        key=lambda x: -(x["granted"] + x["spent"]))
    return {"granted": granted, "spent": spent, "by_reason": by_reason}


# ---------- Зарплата продавцов ----------
#
# Правила владельца (7.10.2026, приёмка DAY-01 и DAY-02):
#   • начисление — процент от выручки точки БЕЗ ДОСТАВКИ за календарный месяц,
#     а месяц заказа — месяц его ВЫДАЧИ (db.ДЕНЬ_ВЫДАЧИ), как и «выдано сегодня»;
#   • отметить выплату можно только за закончившийся месяц: пока месяц идёт,
#     сумма растёт, и выплата посреди месяца потом не сходилась ни с чем;
#   • отменили продажу уже выплаченного месяца — выплату не трогаем (деньги у
#     человека), а разница переходит в ближайшую выплату отдельной строкой.
#
# Каждая выплата хранит выручку и процент на момент выплаты — так разница
# считается честно, даже если владелец потом поменяет процент: старые месяцы
# пересчитываются по своему проценту, а не по новому.

МЕСЯЦ_КТО = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
             "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
МЕСЯЦ_ЧЕГО = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]


class PayrollRefused(Exception):
    """Отметить выплату нельзя: code — для программы, message — для человека."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def месяц_словами(period):
    """"2026-09" → "сентябрь 2026"."""
    год, месяц = (int(x) for x in period.split("-"))
    return f"{МЕСЯЦ_КТО[месяц - 1]} {год}"


def _следующий_месяц(period):
    год, месяц = (int(x) for x in period.split("-"))
    return f"{год + 1:04d}-01" if месяц == 12 else f"{год:04d}-{месяц + 1:02d}"


def _границы_месяца(period):
    return f"{period}-01 00:00", f"{_следующий_месяц(period)}-01 00:00"


def месяц_открыт(period):
    """Месяц ещё идёт (или ещё не начался) — выплату за него отмечать рано."""
    return period >= db.shop_now().strftime("%Y-%m")


def выплата_с(period):
    """С какого дня можно отметить выплату за месяц: "1 ноября"."""
    год, месяц = (int(x) for x in _следующий_месяц(period).split("-"))
    return f"1 {МЕСЯЦ_ЧЕГО[месяц - 1]}"


def _выручка_точек(cur, period):
    """{точка: выручка без доставки} за месяц — по дню выдачи."""
    начало, конец = _границы_месяца(period)
    выдан = db.ДЕНЬ_ВЫДАЧИ
    cur.execute(db._q(
        f"SELECT city AS ct, COALESCE(SUM(total - COALESCE(delivery_fee, 0)), 0) AS s "
        f"FROM orders WHERE status = 'issued' AND {выдан} >= %s AND {выдан} < %s "
        f"GROUP BY city"), (начало, конец))
    return {r["ct"]: round(float(r["s"] or 0), 2) for r in cur.fetchall()}


def _доля(выручка, процент):
    return round(round(float(выручка or 0), 2) * float(процент or 0) / 100, 2)


def _копейки(x):
    """Деньги хранятся дробными числами — меньше копейки считаем нулём."""
    x = round(float(x), 2)
    return 0.0 if abs(x) < 0.005 else x


def _поправки_выплаты(сырое):
    """corrections выплаты → {период: сумма}. Пусто или битое — поправок не было."""
    if not сырое:
        return {}
    try:
        d = json.loads(сырое)
    except (TypeError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {str(k): float(v) for k, v in d.items() if isinstance(v, (int, float))}


def _перерасчёт(cur, user_id, кэш):
    """Что изменилось в уже выплаченных месяцах продавца и ещё не вошло ни в одну выплату.

    Возвращает (остаток, выплаты): остаток — {период: разница}, только
    ненулевые; плюс — недоплатили, минус — переплатили. выплаты — все его
    выплаты с разбором: что вошло в каждую и сколько начислено за тот месяц
    сейчас. кэш — {период: выручка точек}, чтобы не считать месяц дважды."""
    cur.execute(db._q("SELECT * FROM seller_payouts WHERE user_id = %s ORDER BY period"), (user_id,))
    выплаты = [dict(r) for r in cur.fetchall()]
    учтено = {}
    for в in выплаты:
        в["поправки"] = _поправки_выплаты(в.get("corrections"))
        for п, сумма in в["поправки"].items():
            учтено[п] = учтено.get(п, 0.0) + сумма
    остаток = {}
    for в in выплаты:
        if в["period"] not in кэш:
            кэш[в["period"]] = _выручка_точек(cur, в["period"])
        в["сейчас"] = _доля(кэш[в["period"]].get(в["city"], 0.0), в["percent"])
        в["было"] = _доля(в["revenue"], в["percent"])
        в["учтено"] = _копейки(учтено.get(в["period"], 0.0))
        разница = _копейки(в["сейчас"] - в["было"] - в["учтено"])
        if разница:
            остаток[в["period"]] = разница
    return остаток, выплаты


def _зачесть(начислено, остаток):
    """Сколько выплатить за месяц и какие разницы прошлых месяцев этим закрыты.

    Обычно закрываются все: выплата = начислено + разницы. Переплата больше
    начисленного — платить нечего (0): гасим переплату ровно на начисленное,
    старые месяцы первыми, а её остаток ждёт следующей выплаты. Отрицательной
    выплаты не бывает — забирать деньги назад приложение не умеет и не должно."""
    итог = _копейки(начислено + sum(остаток.values()))
    if итог >= 0:
        return итог, dict(остаток)
    закрыто = {п: с for п, с in остаток.items() if с > 0}
    можно = _копейки(начислено + sum(закрыто.values()))     # сколько переплаты погасит этот месяц
    for п in sorted(п for п, с in остаток.items() if с < 0):
        if можно <= 0:
            break
        взять = max(остаток[п], -можно)
        закрыто[п] = _копейки(взять)
        можно = _копейки(можно + взять)
    return 0.0, закрыто


def _список(поправки):
    return [{"period": п, "label": месяц_словами(п), "amount": round(с, 2)} for п, с in sorted(поправки.items())]


def _о_выплате(в, выплаты):
    """Выплата месяца для экрана: сколько, из чего, и что с ней стало потом."""
    где = [{"period": q["period"], "label": месяц_словами(q["period"]), "amount": round(q["поправки"][в["period"]], 2)}
           for q in выплаты if в["period"] in q["поправки"]]
    return {
        "amount": round(float(в["amount"]), 2),
        "base": в["было"],                         # начислено на момент выплаты
        "revenue": round(float(в["revenue"]), 2),
        "percent": float(в["percent"]),
        "created_at": в["created_at"],
        "corrections": _список(в["поправки"]),     # разницы прошлых месяцев, вошедшие в неё
        "now": в["сейчас"],                        # начислено за этот месяц сейчас
        "diff": _копейки(в["сейчас"] - в["было"]),  # изменилось после выплаты
        "settled": где,                            # где эта разница уже учтена
        "left": _копейки(в["сейчас"] - в["было"] - в["учтено"]),   # ещё ждёт выплаты
    }


def payroll_for_period(period):
    """Зарплата продавцов за календарный месяц (period = "ГГГГ-ММ").

    Процент — от выручки точки БЕЗ ДОСТАВКИ, тем же принципом, что и
    реферальный процент («без доставки, как везде»): курьер съедает свою
    доставку сам, продавец не должен получать процент с чужих расходов.

    Точка без продавцов в ответ не попадает — платить там некому. Точка с
    несколькими продавцами возвращается с amount=None: общая сумма видна,
    но как её делить между людьми — решает владелец сам, авто-разбивки нет.

    У каждого продавца: paid — выплата этого месяца (с разбором), carry —
    разницы прошлых выплаченных месяцев, ещё не вошедшие ни в одну выплату,
    to_pay — сколько отметить сейчас (только закончившийся невыплаченный
    месяц точки с одним продавцом), settle — какие разницы эта выплата закроет.

    Выплата человека, который больше не продавец этой точки, из месяца не
    пропадает: он остаётся в строке точки с former=True — история выплат
    не должна зависеть от того, кто работает сегодня.
    """
    открыт = месяц_открыт(period)
    процент = float(db.get_setting("seller_commission_percent", 10) or 10)
    по_точкам = {}
    for s in db.list_staff():
        город = s["city"]
        if not город:              # пустой город — админ над всеми точками, не продавец
            continue
        по_точкам.setdefault(город, []).append({"user_id": int(s["user_id"]), "note": s["note"] or "", "former": False})

    conn = db.connect()
    cur = conn.cursor()
    try:
        кэш = {period: _выручка_точек(cur, period)}
        cur.execute(db._q("SELECT user_id, city FROM seller_payouts WHERE period = %s"), (period,))
        for r in cur.fetchall():
            uid, город = int(r["user_id"]), r["city"]
            if not any(x["user_id"] == uid for x in по_точкам.get(город, [])):
                по_точкам.setdefault(город, []).append({"user_id": uid, "note": "", "former": True})
        строки = []
        for город, люди in по_точкам.items():
            выручка = кэш[period].get(город, 0.0)
            сумма = _доля(выручка, процент)
            одиночка = sum(1 for x in люди if not x["former"]) == 1
            продавцы = []
            for x in люди:
                остаток, выплаты = _перерасчёт(cur, x["user_id"], кэш)
                эта = next((в for в in выплаты if в["period"] == period and в["city"] == город), None)
                # Перевели на другую точку посреди истории: за этот месяц ему
                # уже заплатили там — второй выплаты за месяц не бывает.
                там = next((в["city"] for в in выплаты if в["period"] == period and в["city"] != город), None)
                чужие = {п: с for п, с in остаток.items() if п != period}
                к_выплате, закроет = None, {}
                if not эта and not там and not открыт and одиночка and not x["former"]:
                    к_выплате, закроет = _зачесть(сумма, чужие)
                продавцы.append({
                    **x,
                    "paid": _о_выплате(эта, выплаты) if эта else None,
                    "paid_elsewhere": там,
                    "carry": _список(чужие),
                    "to_pay": к_выплате,
                    "settle": _список(закроет),
                })
            строки.append({
                "city": город,
                "revenue": выручка,
                "percent": процент,
                # Сумму к выплате показываем только когда продавец один — иначе
                # это чья сумма, непонятно, и кнопка «выплачено» была бы враньём.
                "amount": сумма if одиночка else None,
                "sellers": продавцы,
            })
    finally:
        conn.close()
    строки.sort(key=lambda r: r["city"])
    return строки


def pay_seller(user_id, city, period, paid_by):
    """Отметить зарплату продавца за закончившийся месяц выплаченной.

    Сумму считает сервер, а не присылает экран: начислено за месяц плюс
    разницы прошлых выплаченных месяцев (перерасчёт), не меньше нуля.

    ОДНОЙ транзакцией под замком продавца: две выплаты одному человеку
    (скажем, за два месяца с двух телефонов) иначе обе закрыли бы одну и ту
    же разницу, и переплату вычли бы дважды. Повтор той же выплаты упирается
    в UNIQUE(user_id, period) — второй записи не будет.

    Возвращает {"amount", "base", "corrections": {период: сумма}} или бросает
    PayrollRefused."""
    if месяц_открыт(period):
        raise PayrollRefused("month_open", f"{месяц_словами(period).capitalize()} ещё не закончился — "
                                           f"выплату можно отметить с {выплата_с(period)}.")
    процент = float(db.get_setting("seller_commission_percent", 10) or 10)
    conn = db.connect()
    cur = conn.cursor()
    try:
        if db.USE_PG:
            cur.execute("SELECT user_id FROM staff WHERE user_id = %s FOR UPDATE", (user_id,))
        else:
            cur.execute("UPDATE staff SET user_id = user_id WHERE user_id = ?", (user_id,))
        cur.execute(db._q("SELECT city FROM staff WHERE user_id = %s"), (user_id,))
        r = cur.fetchone()
        if not r or (r["city"] or "") != city:
            raise PayrollRefused("not_found", "Этот человек больше не продавец этой точки — обновите экран.")
        cur.execute(db._q("SELECT COUNT(*) AS c FROM staff WHERE city = %s"), (city,))
        if int(cur.fetchone()["c"]) != 1:
            raise PayrollRefused("not_split", "На этой точке несколько продавцов — разделите сумму сами, "
                                              "отметить отсюда нельзя.")
        cur.execute(db._q("SELECT amount, created_at FROM seller_payouts WHERE user_id = %s AND period = %s"),
                    (user_id, period))
        было = cur.fetchone()
        if было:
            raise PayrollRefused("already_paid", f"За {месяц_словами(period)} уже отмечено: "
                                                 f"{float(было['amount']):.2f} Br ({(было['created_at'] or '')[:10]}).")
        кэш = {period: _выручка_точек(cur, period)}
        выручка = кэш[period].get(city, 0.0)
        начислено = _доля(выручка, процент)
        остаток, _ = _перерасчёт(cur, user_id, кэш)
        сумма, закрыто = _зачесть(начислено, остаток)
        cur.execute(db._q(
            "INSERT INTO seller_payouts (user_id, city, period, revenue, percent, amount, paid_by, created_at, corrections) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"),
            (user_id, city, period, выручка, процент, сумма, paid_by, db._now_str(),
             json.dumps(закрыто, ensure_ascii=False) if закрыто else None))
        conn.commit()
    except PayrollRefused:
        conn.rollback()
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        # Два одинаковых нажатия одновременно: замок не взялся (продавца
        # удалили между делом) — последнее слово за UNIQUE.
        if db.seller_payouts_for_period(period).get(int(user_id)):
            raise PayrollRefused("already_paid", f"За {месяц_словами(period)} уже отмечено.")
        raise
    conn.close()
    return {"amount": сумма, "base": начислено, "corrections": закрыто}


def also_bought(top=5, scan=500, min_count=2):
    """{товар: [товары, которые брали вместе с ним]} — по реальным выданным заказам.

    Считаем только пары, встретившиеся не меньше min_count раз: единственная
    совместная покупка — это совпадение, а не закономерность, и советовать по
    ней значит выдавать шум за рекомендацию.
    """
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT items FROM orders WHERE status = 'issued' ORDER BY id DESC LIMIT %s"), (scan,))
    pairs = {}
    for r in cur.fetchall():
        try:
            ids = {int(it.get("id", 0)) for it in json.loads(r["items"]) if it.get("id")}
        except (TypeError, ValueError):
            continue
        for a in ids:
            for b in ids:
                if a != b:
                    pairs.setdefault(a, {})
                    pairs[a][b] = pairs[a].get(b, 0) + 1
    conn.close()
    out = {}
    for a, others in pairs.items():
        best = [pid for pid, n in sorted(others.items(), key=lambda x: -x[1]) if n >= min_count][:top]
        if best:
            out[a] = best
    return out


def orders_for_export(days=None, city=None):
    """Сырые заказы за период — для выгрузки в файл. days=None → всё время.
    city сужает выгрузку до одной точки — продавцу нужны только свои заказы,
    а не весь магазин.

    Отдаём ВСЕ заказы, а не только выданные, и статус кладём столбцом. Выгрузка
    из одних выданных выглядела бы аккуратнее, но скрывала бы отказы — а это
    ровно то, ради чего в файл и лезут: почему заказ не дошёл до выдачи.
    Сумма по строкам «Выдан» при этом сходится с выручкой на экране.

    Сортировка по дате, а не по id: в файле человек читает историю, а не
    внутренние номера.
    """
    now = db.shop_now()
    cutoff = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00") if days else None
    conn = db.connect()
    cur = conn.cursor()
    поля = ("id, created_at, issued_at, city, status, username, user_id, items, total, "
            "coins_used, promo_code, promo_discount, delivery_method, delivery_fee, payment_method")
    условия, параметры = [], []
    if cutoff:
        # Период — по тому же дню, что и выручка на экране: выданный заказ —
        # по дню выдачи, остальные — по дню оформления (у них issued_at пуст).
        # Иначе заказ «оформили 31-го, выдали 1-го» был бы в выручке одного
        # периода, а в файле — другого, и суммы перестали бы сходиться.
        условия.append(f"{db.ДЕНЬ_ВЫДАЧИ} >= %s"); параметры.append(cutoff)
    if city:
        условия.append("city = %s"); параметры.append(city)
    where = f" WHERE {' AND '.join(условия)}" if условия else ""
    cur.execute(db._q(f"SELECT {поля} FROM orders{where} ORDER BY created_at, id"), tuple(параметры))
    строки = [dict(r) for r in cur.fetchall()]
    conn.close()          # раньше не закрывалось: соединение подбирала страховка в конце запроса
    return строки
