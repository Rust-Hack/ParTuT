"""
Стенд нагрузки: копия магазина на локальном Postgres с крупными данными.

Сервер тот же, что на хостинге (waitress, 8 потоков, run.py), база — отдельная
локальная, своя на каждый замер: tools/load/run.sh заводит её под уникальным
именем и удаляет после. Телеграм и внешняя сеть заглушены, токен подставной.

Данные: 4 точки, 125 моделей × 4 точки = 500 товаров (у жидкостей и
одноразок по 6 вкусов), 60 товаров в архиве (половина с движениями склада),
8 000 выданных и 200 открытых заказов за 60 дней, 400 покупателей, продавец
Минска. Один вкус одного товара — 10 штук: на нём гонка заказов.

Сам по себе не запускается — его поднимает run.sh. Боевую базу не трогает и
не может: адрес базы собирается здесь, только 127.0.0.1.

LOAD_ROOT — папка с кодом магазина, который меряем (по умолчанию этот же
репозиторий). Чтобы сравнить «до и после», старый код достают рабочей копией
git (git worktree add) и показывают её сюда.
"""
import datetime
import hashlib
import hmac
import json
import os
import random
import sys
import threading
import time
from urllib.parse import urlencode

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КОРЕНЬ = os.environ.get("LOAD_ROOT") or os.path.dirname(os.path.dirname(ЗДЕСЬ))
sys.path.insert(0, КОРЕНЬ)
БАЗА = "postgresql://{user}@127.0.0.1:{port}/{name}".format(
    user=os.environ.get("LOAD_DB_USER", "partut"), port=os.environ.get("LOAD_DB_PORT", "5433"),
    name=os.environ.get("LOAD_DB_NAME", "partut_load"))
os.environ.update({
    "PYTHON_DOTENV_DISABLED": "1", "BOT_TOKEN": "000000:LOCAL-DUMMY-NO-SEND",
    "DATABASE_URL": БАЗА, "TEST_DATABASE_URL": "",
    "DEV_MODE": "0", "ADMIN_IDS": "30001", "SUPER_ADMIN_IDS": "30001", "DEV_IDS": "30002",
    "WEBAPP_URL": "", "SUBSCRIBE_CHANNEL": "", "SUPPORT_IDS": "30001",
    "BACKUP_URL": "", "BACKUP_USER": "", "BACKUP_PASSWORD": "", "KEEP_WARM": "0",
})

import requests  # noqa: E402
from partut import db  # noqa: E402
db.init_db()
from partut.web import server  # noqa: E402
from partut.integrations import tgsend  # noqa: E402

# Телеграм — заглушки, сеть наружу — выключена.
tgsend.tg.send_message = lambda *a, **k: type("M", (), {"message_id": 1})()
tgsend.tg.get_me = lambda: type("Bot", (), {"username": "qa_load_bot"})()
tgsend.tg.send_document = lambda *a, **k: type("M", (), {"message_id": 1})()
tgsend.is_subscribed = lambda uid: True
requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть выключена"))
_снимков = [0]


def _фото(*a, **k):
    _снимков[0] += 1
    n = _снимков[0]

    def размер(w, i):
        return type("PS", (), {"file_id": f"load-{n}-{i}", "file_unique_id": f"u{n}{i}",
                               "width": w, "height": w, "file_size": w * 100})()
    return type("M", (), {"message_id": 1, "photo": [размер(90, "s"), размер(320, "m"), размер(1280, "x")]})()


tgsend.tg.send_photo = _фото

ТОЧКИ = ["Минск", "Туров", "Лунинец", "Горки"]
ВЛАДЕЛЕЦ, ПРОДАВЕЦ = 30001, 30003
ПОКУПАТЕЛИ = list(range(40001, 40401))
ВКУСЫ = ["Мята", "Арбуз", "Кислая вишня", "Манго", "Клубника киви", "Черника"]
random.seed(7)


def посеять():
    """Наполнить базу. Возвращает id товара, на котором идёт гонка заказов."""
    t0 = time.time()
    for т in ТОЧКИ:
        db.add_location(т)
        db.add_delivery_method(т, "Самовывоз", False, "", "у точки", 0, False)
    db.add_staff(ПРОДАВЕЦ, "Минск", "Продавец Минска", ВЛАДЕЛЕЦ)
    for uid in [ВЛАДЕЛЕЦ, ПРОДАВЕЦ] + ПОКУПАТЕЛИ:
        db.ensure_user(uid)
        db.set_age_ok(uid)
    conn = db.connect(); cur = conn.cursor()
    for uid in ПОКУПАТЕЛИ:
        cur.execute("UPDATE users SET city = %s WHERE user_id = %s", ("Минск", uid))
    conn.commit(); conn.close()

    модели = []
    for i in range(125):
        кат = ["liquid", "disposable", "coils", "accessories"][i % 4]
        варианты = ВКУСЫ if кат in ("liquid", "disposable") else []
        mid = db.add_model(кат, f"LOAD Товар {i:03d}", f"BRAND{i % 12}", "описание " * 5, {}, варианты)
        модели.append((mid, варианты))
    товары = []
    for mid, варианты in модели:
        for т in ТОЧКИ:
            pid = db.create_point_product(mid, т, 20 + (mid % 30), 10, 0,
                                          stock=0 if варианты else 50,
                                          variants=[{"flavor": в, "stock": 40} for в in варианты] if варианты else None,
                                          admin_id=ВЛАДЕЛЕЦ)
            товары.append((pid, т, варианты))

    conn = db.connect(); cur = conn.cursor()
    for k in range(60):
        cur.execute("""INSERT INTO products (city, category, name, price, stock, archived, cost, hidden)
                       VALUES (%s, 'accessories', %s, 15, 0, 1, 7, 0) RETURNING id""", (ТОЧКИ[k % 4], f"LOAD Архив {k:03d}"))
        pid = cur.fetchone()["id"]
        if k % 2 == 0:
            cur.execute("""INSERT INTO stock_moves (product_id, flavor, delta, reason, cost, note, admin_id, created_at)
                           VALUES (%s, NULL, 5, 'supply', 7, 'нагрузка', %s, '2026-09-01 10:00')""", (pid, ВЛАДЕЛЕЦ))
    conn.commit()

    # Заказы читают статистика, зарплата, история архива и «в заказах».
    сейчас = db.shop_now()
    строки = []
    for n in range(8200):
        _, т, _ = random.choice(товары)
        состав = []
        for _ in range(random.randint(1, 3)):
            p2, _, в2 = random.choice([x for x in товары if x[1] == т][:60])
            вкус = random.choice(в2) if в2 else None
            состав.append({"id": p2, "flavor": вкус, "name": f"LOAD {p2}" + (f" — {вкус}" if вкус else ""),
                           "price": 25.0, "cost": 10.0, "qty": random.randint(1, 2)})
        когда = (сейчас - datetime.timedelta(minutes=random.randint(0, 60 * 24 * 60))).strftime("%Y-%m-%d %H:%M")
        статус = "issued" if n < 8000 else random.choice(["paid", "confirmed"])
        строки.append((random.choice(ПОКУПАТЕЛИ), "buyer", т, json.dumps(состав, ensure_ascii=False),
                       sum(с["price"] * с["qty"] for с in состав), статус, когда,
                       когда if статус == "issued" else None))
    cur.executemany("""INSERT INTO orders (user_id, username, city, items, total, pickup_time, status, created_at,
                                           coins_used, delivery_method, delivery_fee, payment_method, issued_at)
                       VALUES (%s, %s, %s, %s, %s, '', %s, %s, 0, 'Самовывоз', 0, 'cash', %s)""", строки)
    conn.commit(); conn.close()

    гонка = next(pid for pid, т, варианты in товары if т == "Минск" and варианты)
    conn = db.connect(); cur = conn.cursor()
    cur.execute("UPDATE product_variants SET stock = 10 WHERE product_id = %s AND flavor = %s", (гонка, "Мята"))
    cur.execute("UPDATE products SET stock = (SELECT SUM(stock) FROM product_variants WHERE product_id = %s) WHERE id = %s",
                (гонка, гонка))
    conn.commit(); conn.close()
    print(f"товаров на точках: {len(товары)}, заказов: {len(строки)}, посеяно за {time.time() - t0:.1f} с", flush=True)
    return гонка


def подписано(uid):
    """initData, подписанный подставным токеном, — как от Telegram."""
    user = {"id": uid, "username": f"u{uid}", "first_name": f"U{uid}"}
    поля = {"user": json.dumps(user, separators=(",", ":")), "query_id": "load", "auth_date": str(int(time.time()))}
    ключ = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    поля["hash"] = hmac.new(ключ, "\n".join(f"{k}={поля[k]}" for k in sorted(поля)).encode(), hashlib.sha256).hexdigest()
    return urlencode(поля)


if __name__ == "__main__":
    import psycopg2
    from flask import jsonify
    from waitress import serve
    гонка = посеять()

    # Время работы каждого запроса на сервере — без ожидания в очереди waitress.
    # Сравнение со временем у замерщика показывает, где теряется время.
    _замеры, _замок, _нуль = [], threading.Lock(), time.time()
    _исходное = server.app.wsgi_app

    def _с_замером(environ, start_response):
        t0 = time.time()
        try:
            return _исходное(environ, start_response)
        finally:
            with _замок:
                _замеры.append((round(t0 - _нуль, 3), round((time.time() - t0) * 1000, 1), environ.get("PATH_INFO", "")))

    server.app.wsgi_app = _с_замером

    @server.app.get("/__load/meta")
    def _meta():
        return jsonify({"owner": подписано(ВЛАДЕЛЕЦ), "seller": подписано(ПРОДАВЕЦ),
                        "buyers": [подписано(u) for u in ПОКУПАТЕЛИ], "race_product": гонка})

    @server.app.get("/__load/server")
    def _server_times():
        with _замок:
            копия = list(_замеры)
            _замеры.clear()
        return jsonify({"zero": _нуль, "rows": копия})

    @server.app.get("/__load/sessions")
    def _sessions():
        """Сколько подключений база приняла с начала жизни — своим отдельным
        соединением, мимо пула магазина (сам подсчёт стоит одно подключение)."""
        c = psycopg2.connect(БАЗА)
        cur = c.cursor()
        cur.execute("SELECT sessions FROM pg_stat_database WHERE datname = current_database()")
        n = cur.fetchone()[0]
        c.close()
        return jsonify({"sessions": n})

    serve(server.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8793, threads=8)
