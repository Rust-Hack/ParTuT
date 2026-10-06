"""Продажи на точке по дням: ошибку прошлой смены можно найти и отменить (приёмка DAY-03).

Ревьюер провёл продажу «вчера» и не смог её отменить: экран показывал только
«Сегодня на точке», сервер игнорировал день и отдавал сегодня, а в очередь
заказов продажи на точке не попадают. Отмена на сервере была — пути к ней не было.

Решение владельца (7.10.2026): продавец видит и отменяет продажи своей точки
за 7 дней, владелец — любые. Проверяется:
  • день и поиск по номеру отдают нужное, будущий и кривой день — отказ;
  • вчерашний чек продавец своей точки находит и отменяет ровно один раз,
    склад и выручка дня пересчитываются;
  • старше 7 дней — продавцу ни увидеть, ни отменить, владельцу — можно;
  • чужая точка закрыта и для просмотра по номеру, и для отмены;
  • в журнале отмена прошлой продажи — с её датой.
"""
import datetime

from _common import db, client, Checker, real_auth, as_admin, REAL_GET_USER
from partut import cache
from partut import config
from partut.web import auth

CITY = "ИсторияПродаж"
OTHER = "ИсторияЧужая"
OWNER = 7911
SELLER = 7901
STRANGER = 7902        # продавец другой точки


def _as(uid):
    real_auth()
    auth.get_user = lambda init: {"id": uid, "username": f"u{uid}"}


def _post(path, **body):
    return client.post(path, json={"initData": "x", **body})


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for город in (CITY, OTHER):
        cur.execute(db._q("DELETE FROM orders WHERE city = %s"), (город,))
        cur.execute(db._q("DELETE FROM products WHERE city = %s"), (город,))
    cur.execute(db._q("DELETE FROM admin_log WHERE action = %s"), ("sale/cancel",))
    conn.commit(); conn.close()
    cache.bust()


def _продажа(pid, цена, дней_назад, город=CITY):
    oid, _, _ = db.record_point_sale(город, [{"id": pid, "qty": 1, "price": цена}], OWNER, "owner", "cash", "")
    if дней_назад:
        когда = (db.shop_now() - datetime.timedelta(days=дней_назад)).strftime("%Y-%m-%d 11:30")
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE orders SET created_at = %s, issued_at = %s WHERE id = %s"), (когда, когда, oid))
        conn.commit(); conn.close()
    return oid


def _день(дней_назад):
    return (db.shop_now() - datetime.timedelta(days=дней_назад)).strftime("%Y-%m-%d")


def _склад(pid):
    return int(db.get_product(pid)["stock"])


def run():
    _clean()
    старые = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = старые | {OWNER}
    db.add_staff(SELLER, CITY, "Олег")
    db.add_staff(STRANGER, OTHER, "Чужой")
    config.refresh_staff()
    pid = db.add_product(CITY, "accessories", "Кабель", 10.0, 20)
    чужой_pid = db.add_product(OTHER, "accessories", "Кабель", 10.0, 20)
    cache.bust()
    сегодняшняя = _продажа(pid, 10.0, 0)
    вчерашняя = _продажа(pid, 20.0, 1)
    шестой = _продажа(pid, 15.0, 6)
    старая = _продажа(pid, 30.0, 10)
    чужая = _продажа(чужой_pid, 10.0, 1, OTHER)
    try:
        c = Checker("История продаж: продавец своей точки")
        _as(SELLER)
        d = _post("/api/admin/sales").get_json()
        c(f"без дня — сегодня: {[x['id'] for x in d['sales']]}", [x["id"] for x in d["sales"]] == [сегодняшняя])
        c(f"сервер называет сегодня и самый ранний день ({d.get('today')}, {d.get('min_day')})",
          d["today"] == _день(0) and d["min_day"] == _день(6))
        d = _post("/api/admin/sales", day=_день(1)).get_json()
        c(f"вчера — вчерашний чек, его можно отменить ({d['sales']})",
          [x["id"] for x in d["sales"]] == [вчерашняя] and d["sales"][0]["can_cancel"] is True)
        d = _post("/api/admin/sales", day=_день(6)).get_json()
        c("шестой день назад ещё виден", [x["id"] for x in d["sales"]] == [шестой])
        r = _post("/api/admin/sales", day=_день(10))
        c(f"10 дней назад — не продавцу ({r.status_code} {r.get_json().get('error')})",
          r.status_code == 403 and r.get_json().get("error") == "too_old")
        r = _post("/api/admin/sales", day=_день(-1))
        c("завтрашний день — отказ", r.status_code == 400)
        r = _post("/api/admin/sales", day="2026-02-30")
        c("несуществующий день — отказ", r.status_code == 400)
        d = _post("/api/admin/sales", id=вчерашняя).get_json()
        c("по номеру находится вчерашний чек", d.get("ok") and [x["id"] for x in d["sales"]] == [вчерашняя])
        r = _post("/api/admin/sales", id=старая)
        c("по номеру старше 7 дней — не продавцу", r.status_code == 403)
        r = _post("/api/admin/sales", id=чужая)
        c(f"по номеру чужой точки — закрыто ({r.status_code})", r.status_code == 403)
        заказ = db.create_order(501, "kl", CITY, [{"id": 1, "name": "X", "price": 5, "qty": 1}], 5, "")
        r = _post("/api/admin/sales", id=заказ)
        c("номер обычного заказа — это не продажа на точке", r.status_code == 404)

        c2 = Checker("Отмена вчерашней продажи продавцом")
        склад_до = _склад(pid)
        выручка_до = db.get_business_stats(days=2)["revenue"]
        r = _post("/api/admin/sale/cancel", id=вчерашняя)
        c2(f"отменил ({r.status_code})", r.status_code == 200 and r.get_json().get("ok"))
        c2(f"штука вернулась на полку ({склад_до} → {_склад(pid)})", _склад(pid) == склад_до + 1)
        выручка_после = db.get_business_stats(days=2)["revenue"]
        c2(f"выручка за два дня уменьшилась на 20 ({выручка_до} → {выручка_после})", abs(выручка_до - выручка_после - 20.0) < 0.001)
        r = _post("/api/admin/sale/cancel", id=вчерашняя)
        c2(f"повторная отмена — отказ, склад не удвоился ({r.status_code}, {_склад(pid)})",
           r.status_code == 409 and _склад(pid) == склад_до + 1)
        d = _post("/api/admin/sales", day=_день(1)).get_json()
        c2("в списке дня она отменённая и без кнопки", d["sales"][0]["status"] == "canceled" and d["sales"][0]["can_cancel"] is False)
        r = _post("/api/admin/sale/cancel", id=старая)
        c2(f"продажу 10-дневной давности продавец не отменяет ({r.status_code})",
           r.status_code == 403 and r.get_json().get("error") == "too_old")
        c2("и её штуки на полку не вернулись", _склад(pid) == склад_до + 1)

        c3 = Checker("Чужая точка")
        r = _post("/api/admin/sale/cancel", id=чужая)
        c3(f"отменить продажу чужой точки нельзя ({r.status_code})", r.status_code == 403)
        _as(STRANGER)
        d = _post("/api/admin/sales", day=_день(1), city=CITY).get_json()
        c3("продавец другой точки, даже назвав чужую, видит только свою",
           [x["id"] for x in d["sales"]] == [чужая])

        c4 = Checker("Владелец: любой день и любая продажа")
        _as(OWNER)
        d = _post("/api/admin/sales", day=_день(10), city=CITY).get_json()
        c4(f"видит продажу 10-дневной давности, отмена доступна ({d.get('min_day')})",
           [x["id"] for x in d["sales"]] == [старая] and d["sales"][0]["can_cancel"] is True and d["min_day"] is None)
        r = _post("/api/admin/sale/cancel", id=старая)
        c4("отменяет её", r.status_code == 200)
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("SELECT details FROM admin_log WHERE action = %s ORDER BY id"), ("sale/cancel",))
        журнал = [r["details"] for r in cur.fetchall()]
        conn.close()
        дата = _день(10)
        c4(f"в журнале отмена с датой продажи: {журнал}",
           any(f"№{старая} от {дата[8:10]}.{дата[5:7]} отменена" in x for x in журнал)
           and any(f"№{вчерашняя} от " in x for x in журнал))
    finally:
        config.SUPER_ADMIN_IDS = старые
        db.remove_staff(SELLER)
        db.remove_staff(STRANGER)
        config.refresh_staff()
        auth.get_user = REAL_GET_USER
        as_admin()
        _clean()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
