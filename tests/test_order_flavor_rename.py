"""Вкус в заказе после переименования на полке (приёмка SF-01, 3.10.2026).

Наводка витрины переименовала вкусы PILOW TALK регистром («клубника манго» →
«Клубника манго»), а заказы отбирала по названию товара в составе — и
пропускала те, что оформлены, пока товар назывался иначе. Отмена такого
заказа ставила «отменён», а штуки не возвращала: UPDATE по старому
названию менял ноль строк, и этого никто не проверял.

Теперь:
  • отмена, правка заказа и отмена продажи на точке находят вариант по
    нынешнему названию (регистр не важен — два таких варианта приложение
    завести не даёт);
  • варианта нет вовсе — отказ целиком, заказ не отменён, ничего не тронуто;
  • наводки находят заказы по НОМЕРУ товара; третья, разовая, доделывает
    пропущенное на уже выкаченной базе и честно перечисляет отменённые.
"""
import datetime
import json

from _common import db, client, Checker, as_user, as_admin

from partut.db import catalog_fix

ПОКУПАТЕЛЬ = 78001
СТАРЫЕ = ["клубника манго", "клубника банан", "экзотические фрукты клубника", "Виноград"]


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "orders", "products", "models", "brands"):
        cur.execute(f"DELETE FROM {t}")
    for k in (catalog_fix.ОТМЕТКА_ВИТРИНЫ, catalog_fix.ОТМЕТКА_ЗАКАЗОВ):
        cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (k,))
    conn.commit(); conn.close()


def _заказ(pid, вкус, штук=1, имя="PILOW TALK IC40000", статус="confirmed"):
    oid, *_ = db.place_order(ПОКУПАТЕЛЬ, "qa", "Минск",
                             [{"id": pid, "name": f"{имя} — {вкус}", "price": 40.0, "qty": штук, "flavor": вкус}],
                             40.0 * штук, 0, 0.01, 0, "Самовывоз", "", "cash", "", "", статус)
    return oid


def _остаток(pid, вкус):
    return {v["flavor"]: v["stock"] for v in db.get_variants(pid)}.get(вкус)


def _переименовать_на_полке(pid):
    """Как сделала выкаченная наводка: полка — новые названия, заказы — нет."""
    conn = db.connect(); cur = conn.cursor()
    for старый, новый in catalog_fix.PILOW_ВКУСЫ.items():
        cur.execute(db._q("UPDATE product_variants SET flavor = %s WHERE product_id = %s AND flavor = %s"),
                    (новый, pid, старый))
    conn.commit(); conn.close()


def run():
    c = Checker("Вкус в заказе после переименования на полке (SF-01)")
    _чисто()
    db.ensure_user(ПОКУПАТЕЛЬ)

    # ---- Сценарий приёмки: товар переименован между двумя заказами ----
    m = db.add_model("disposable", "QA historical name", "PILOW TALK", "", {}, СТАРЫЕ)
    pid = db.create_point_product(m, "Минск", 40.0, 20.0, variants=[{"flavor": f, "stock": 5} for f in СТАРЫЕ])
    первый = _заказ(pid, "клубника манго", имя="QA historical name")
    db.update_model(m, name="PILOW TALK IC40000")
    второй = _заказ(pid, "клубника манго")
    c("до наводки: на полке манго 3", _остаток(pid, "клубника манго") == 3)
    catalog_fix.apply_storefront_fix()
    состав = [json.loads(db.get_order(n)["items"])[0]["flavor"] for n in (первый, второй)]
    c("наводка переписала ОБА заказа — и оформленный под прежним названием товара", состав == ["Клубника манго"] * 2)
    db.cancel_order(второй)
    c("отмена второго — на полке 4", _остаток(pid, "Клубника манго") == 4)
    db.cancel_order(первый)
    c("отмена первого — на полке 5 (было: «отменён», а штуки пропали)", _остаток(pid, "Клубника манго") == 5)

    # ---- Выкаченная база: полка переименована, заказ со старым названием остался ----
    _чисто()
    m = db.add_model("disposable", "PILOW TALK IC40000", "PILOW TALK", "", {}, СТАРЫЕ)
    pid = db.create_point_product(m, "Минск", 40.0, 20.0, variants=[{"flavor": f, "stock": 5} for f in СТАРЫЕ])
    открытый = _заказ(pid, "клубника манго", 2)
    правка = _заказ(pid, "клубника банан", 1)
    _переименовать_на_полке(pid)
    db.cancel_order(открытый)
    c("отмена заказа со старым «клубника манго» вернула 2 шт на «Клубника манго»", _остаток(pid, "Клубника манго") == 5)

    order, итог = db.update_order_items(правка, {0: 3}, 0.01)
    c("правка заказа со старым названием: +2 списано с «Клубника банан»",
      order is not None and _остаток(pid, "Клубника банан") == 2)
    order, итог = db.update_order_items(правка, {0: 1}, 0.01)
    c("…и −2 вернулось туда же", order is not None and _остаток(pid, "Клубника банан") == 4)

    продажа = db.record_point_sale("Минск", [{"id": pid, "flavor": "Виноград", "qty": 1, "price": 40}], 1, "qa")[0]
    conn = db.connect(); cur = conn.cursor()       # продажа записана, потом вкус переименовали регистром
    cur.execute(db._q("UPDATE product_variants SET flavor = %s WHERE product_id = %s AND flavor = %s"), ("ВИНОГРАД", pid, "Виноград"))
    conn.commit(); conn.close()
    db.cancel_point_sale(продажа)
    c("отмена продажи на точке со старым названием вернула штуку на «ВИНОГРАД»", _остаток(pid, "ВИНОГРАД") == 5)

    # ---- Варианта нет вовсе: отказ целиком ----
    пропал = _заказ(pid, "Экзотические фрукты клубника", 1)
    было_остаток = db.get_product(pid)["stock"]
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM product_variants WHERE product_id = %s AND flavor = %s"), (pid, "Экзотические фрукты клубника"))
    conn.commit(); conn.close()
    отказ = None
    try:
        db.cancel_order(пропал)
    except db.CancelRefused as e:
        отказ = e
    c("вкуса нет вовсе — отмена отказана, а не «отменён» без возврата",
      отказ is not None and отказ.code == "variant_missing" and "не отменён" in отказ.message)
    c("…и ничего не тронуто: заказ ждёт, остаток прежний",
      db.get_order(пропал)["status"] == "confirmed" and db.get_product(pid)["stock"] == было_остаток - 0)
    as_admin()
    r = client.post("/api/admin/order/status", json={"initData": "x", "id": пропал, "action": "reject", "reason": "out"})
    d = r.get_json()
    c("продавец «Отклонить» — 409 и объяснение, что делать", r.status_code == 409 and "Верните вариант" in (d.get("message") or ""))
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'paid' WHERE id = %s"), (пропал,))
    conn.commit(); conn.close()
    as_user(ПОКУПАТЕЛЬ, "qa")
    r = client.post("/api/order/cancel", json={"initData": "x", "order_id": пропал})
    d = r.get_json()
    c("покупатель — 409 и понятное ему «напишите в поддержку»",
      r.status_code == 409 and "поддержку" in (d.get("message") or "") and db.get_order(пропал)["status"] == "paid")
    order, итог = db.update_order_items(пропал, {0: 2}, 0.01)
    c("правка количества пропавшего варианта — отказ no_variant", order is None and str(итог).startswith("no_variant:"))

    # Авто-отмена брошенного заказа картой: отказ не роняет обход, заказ не трогается.
    from partut.bot import handlers as botmod
    conn = db.connect(); cur = conn.cursor()
    старое = (db.shop_now() - datetime.timedelta(hours=100)).strftime("%Y-%m-%d %H:%M")
    cur.execute(db._q("UPDATE orders SET status = 'new', created_at = %s WHERE id = %s"), (старое, пропал))
    conn.commit(); conn.close()
    прежний_send = botmod._safe_send
    botmod._safe_send = lambda *a, **k: None
    try:
        отменено = botmod._expire_unpaid_orders()
    finally:
        botmod._safe_send = прежний_send
    c("авто-отмена: отказ пропущен, обход не упал, заказ остался", пропал not in отменено
      and db.get_order(пропал)["status"] == "new")

    # ---- Третья наводка на выкаченной базе ----
    _чисто()
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("INSERT INTO settings (key, value) VALUES (%s, %s)"), (catalog_fix.ОТМЕТКА_ВИТРИНЫ, "2026-10-03 04:50"))
    conn.commit(); conn.close()
    m = db.add_model("disposable", "PILOW TALK IC40000", "PILOW TALK", "", {}, СТАРЫЕ)
    pid = db.create_point_product(m, "Минск", 40.0, 20.0, variants=[{"flavor": f, "stock": 5} for f in СТАРЫЕ])
    туров = db.create_point_product(m, "Туров", 40.0, 20.0, variants=[{"flavor": f, "stock": 5} for f in СТАРЫЕ])
    ждёт = _заказ(pid, "клубника манго", имя="Старое имя")
    отменён = _заказ(pid, "клубника банан", имя="Старое имя")
    выдан = _заказ(pid, "Виноград", имя="Старое имя")
    туровский = _заказ(туров, "клубника манго", имя="Старое имя")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = 'canceled' WHERE id = %s"), (отменён,))
    conn.commit(); conn.close()
    _переименовать_на_полке(pid)                    # Минск — как после выката; Туров — полка ещё старая
    n = catalog_fix.apply_order_flavor_fix()
    вкус = lambda o: json.loads(db.get_order(o)["items"])[0]["flavor"]
    c(f"переписаны заказы Минска со старыми вкусами: {n}", n == 2 and вкус(ждёт) == "Клубника манго"
      and вкус(отменён) == "Клубника банан")
    c("«Виноград» не менялся — его и не переименовывали", вкус(выдан) == "Виноград")
    c("Туров — полка ещё старая, заказ не тронут (иначе разошлись бы снова)", вкус(туровский) == "клубника манго")
    журнал = [r["details"] for r in db.list_admin_log(limit=30) if r["admin_name"] == catalog_fix.КТО_ЗАКАЗЫ]
    c("в журнале: сколько и в каком состоянии", any("заказов 2 (ждут выдачи: 1, отменены: 1, выданы: 0)" in x for x in журнал))
    c(f"в журнале отменённый заказ назван для сверки — №{отменён}",
      any(f"№{отменён}" in x and "пересчётом" in x for x in журнал))
    c("остаток отменённого заново НЕ оприходован", _остаток(pid, "Клубника банан") == 4)
    c("второй запуск — не запускается", catalog_fix.apply_order_flavor_fix() is None)

    _чисто()
    m = db.add_model("disposable", "PILOW TALK IC40000", "PILOW TALK", "", {}, ["Клубника манго"])
    pid = db.create_point_product(m, "Минск", 40.0, 20.0, variants=[{"flavor": "Клубника манго", "stock": 5}])
    _заказ(pid, "Клубника манго")
    c("на чистой базе — ноль заказов, без шума", catalog_fix.apply_order_flavor_fix() == 0)
    c("…и в журнале честное «всё уже сходится»",
      any("всё уже сходится" in r["details"] for r in db.list_admin_log(limit=10) if r["admin_name"] == catalog_fix.КТО_ЗАКАЗЫ))
    _чисто()
    return c.fails
