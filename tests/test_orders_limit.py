"""Лимит списка заказов и граница города.

Список заказов для админки берётся с потолком — двести последних. Потолок нужен:
без него страница продавца однажды начнёт тянуть всю историю магазина.

Но считаться он обязан ВНУТРИ города. Пока точка одна, разницы нет никакой, и
именно поэтому ошибку не видно: она просыпается ровно в тот день, когда магазин
пошёл в гору. Минск делает двести заказов, продавец Турова открывает список — и
он пуст. Заказы есть, покупатели ждут, а продавец их не видит и обработать не
может.

Проверяем самым грубым способом: заваливаем чужой город заказами сверх потолка
и смотрим, остался ли на экране свой.
"""
from _common import db, client, Checker, real_auth

from partut import cache
from partut import config
from partut.web import auth

OWNER = 7620
TUROV = 7621
BUYER = 7622

ПОТОЛОК = 30          # столько запрашиваем — и столько же чужих заказов заводим


def _as(uid):
    real_auth()
    auth.get_user = lambda init, u=uid: {"id": u, "username": f"u{u}"}
    auth.get_admin = lambda init, u=uid: (
        {"id": u, "username": f"u{u}", "role": config.admin_role(u), "city": config.admin_city(u)}
        if config.admin_role(u) else None)


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def _order(city, pid):
    oid = db.create_order(BUYER, "buyer", city,
                          [{"id": pid, "name": "Тестовый", "price": 20.0, "qty": 1}], 20.0, "")
    db.set_order_delivery(oid, "Самовывоз", "", 0, "cash")
    db.set_order_status(oid, "paid")
    return oid


def run():
    _clean()
    старые = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = старые | {OWNER}
    db.add_staff(TUROV, "Туров", "продавец Турова")
    config.refresh_staff()

    p_minsk = db.add_product("Минск", "disposable", "Минский", 20.0, 999)
    p_turov = db.add_product("Туров", "disposable", "Туровский", 20.0, 999)
    fails = []
    try:
        # Свой заказ — ПЕРВЫМ, то есть самым старым. Дальше его заваливают чужие.
        o_turov = _order("Туров", p_turov)
        for _ in range(ПОТОЛОК):
            _order("Минск", p_minsk)

        c = Checker("Потолок считается внутри города")
        свои = db.get_orders(ПОТОЛОК, city="Туров")
        c("свой заказ виден из-под завала чужих",
          [o["id"] for o in свои] == [o_turov])
        c("чужих в выборке нет",
          all(o["city"] == "Туров" for o in свои))
        fails += c.fails

        c2 = Checker("То же через ручку продавца")
        _as(TUROV)
        r = client.post("/api/admin/orders", json={"initData": "x"})
        видит = [o["id"] for o in r.get_json().get("orders", [])]
        c2("продавец Турова видит свой заказ", o_turov in видит)
        c2("и только свой", len(видит) == 1)
        fails += c2.fails

        c3 = Checker("Без города — по всему магазину, как и раньше")
        c3("владелец видит и чужие", len(db.get_orders(ПОТОЛОК + 5)) == ПОТОЛОК + 1)
        fails += c3.fails
    finally:
        config.SUPER_ADMIN_IDS = старые
        db.remove_staff(TUROV)
        config.refresh_staff()
        _clean()
        real_auth()
    return fails
