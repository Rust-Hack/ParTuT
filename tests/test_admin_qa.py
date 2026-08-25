"""Админка глазами проверяющего: права, роли, загрузки, ввод, поиск.

Это не проверка отдельной задачи, а сторож для того, что уже работает
правильно и должно работать дальше. Порядок такой же, каким админку проходят
руками: сначала кто это и что ему можно, потом что он вводит и грузит.

Почему именно эти проверки. Каждая закрывает вопрос, ответ на который нельзя
получить чтением кода — только попыткой:

  • отзыв доступа: действует ли он СО СЛЕДУЮЩЕГО запроса или через полминуты,
    пока не протухнет кэш прав;
  • повышение роли: нельзя ли из приложения выписать себе владельца;
  • настройки: обрезаются ли числа по границам, а не записываются как есть;
  • загрузка: отбивается ли файл больше потолка ДО чтения в память;
  • список людей: честно ли сказано, что показаны не все.
"""
from _common import db, client, Checker, real_auth

from partut import cache
from partut import config
from partut.web import auth

ВЛАДЕЛЕЦ = 9101
ПРОДАВЕЦ = 9102
НОВЫЙ = 9103


def _как(uid):
    """Настоящая проверка прав, только подменяем, кто стучится.

    Через модуль (auth.get_admin = ...), а не копией имени: копия подмены не
    заметит, и проверки прав пройдут вхолостую. На этом уже спотыкались.
    """
    real_auth()
    auth.get_user = lambda init, u=uid: {"id": u, "username": f"u{u}"}
    auth.get_admin = lambda init, u=uid: (
        {"id": u, "username": f"u{u}", "role": config.admin_role(u), "city": config.admin_city(u)}
        if config.admin_role(u) else None)


def _post(путь, **тело):
    r = client.post(путь, json={"initData": "x", **тело})
    return r.status_code, (r.get_json() or {})


def run_права():
    """Отзыв доступа и попытки подняться выше своей роли."""
    старые = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = старые | {ВЛАДЕЛЕЦ}
    db.add_staff(ПРОДАВЕЦ, "Минск", "продавец")
    config.refresh_staff()
    провалы = []
    try:
        c = Checker("Отзыв доступа действует немедленно")
        _как(ПРОДАВЕЦ)
        c("продавец работает", _post("/api/admin/orders")[0] == 200)
        _как(ВЛАДЕЛЕЦ)
        c("владелец отозвал", _post("/api/admin/staff/remove", user_id=ПРОДАВЕЦ)[0] == 200)
        _как(ПРОДАВЕЦ)
        # Права ненадолго кэшируются (30 с). Если снятие не сбросит кэш, уволенный
        # продавец полминуты продолжит вести заказы — и это худшие полминуты.
        код, тело = _post("/api/admin/orders")
        c("следующий же запрос отбит", код == 403 and тело.get("error") == "forbidden")
        c("приложение больше не считает его админом",
          _post("/api/me")[1].get("is_admin") is False)
        провалы += c.fails

        c2 = Checker("Выше своей роли не подняться")
        db.add_staff(ПРОДАВЕЦ, "Минск", "продавец")
        config.refresh_staff()
        _как(ПРОДАВЕЦ)
        закрыто = [
            ("/api/admin/staff/add", {"user_id": ПРОДАВЕЦ, "city": ""}, "owner_only"),
            ("/api/admin/staff/remove", {"user_id": ВЛАДЕЛЕЦ}, "owner_only"),
            ("/api/admin/grant", {"user_id": ПРОДАВЕЦ, "coins": 100000}, "owner_only"),
            ("/api/admin/settings/update", {"payment_info": "мой счёт"}, "owner_only"),
            ("/api/admin/user/delete", {"user_id": ВЛАДЕЛЕЦ}, "owner_only"),
            ("/api/admin/stats/reset", {}, "dev_only"),
        ]
        for путь, тело, ожидаем in закрыто:
            код, о = _post(путь, **тело)
            c2(f"продавцу закрыт {путь}", код == 403 and о.get("error") == ожидаем)
        провалы += c2.fails

        c3 = Checker("Владельца не снять из приложения")
        _как(ВЛАДЕЛЕЦ)
        код, о = _post("/api/admin/staff/remove", user_id=ВЛАДЕЛЕЦ)
        # Списки владельцев живут в переменных окружения, и это главная причина,
        # по которой права нельзя перехватить через приложение.
        c3("сам себя владелец не снимает", код == 400 and о.get("error") == "super_protected")
        c3("и остаётся владельцем", config.admin_role(ВЛАДЕЛЕЦ) == "owner")
        код, о = _post("/api/admin/staff/add", user_id=НОВЫЙ, city="Минск")
        c3("новому выдаётся именно продавец", код == 200 and config.admin_role(НОВЫЙ) == "seller")
        код, о = _post("/api/admin/staff/add", user_id=НОВЫЙ, city="Атлантида")
        c3("несуществующая точка отбита", код == 400 and о.get("error") == "bad_city")
        код, о = _post("/api/admin/staff/add", user_id=-5, city="Минск")
        c3("отрицательный id отбит", код == 400)
        провалы += c3.fails
        return провалы
    finally:
        config.SUPER_ADMIN_IDS = старые
        for u in (ПРОДАВЕЦ, НОВЫЙ):
            db.remove_staff(u)
        config.refresh_staff()
        real_auth()


def run_настройки():
    """Числа обрезаются по границам, а не пишутся как прислали."""
    from _common import as_admin
    as_admin()
    c = Checker("Границы настроек")

    def сохранить_и_прочитать(поле, значение):
        _post("/api/admin/settings/update", **{поле: значение})
        return client.post("/api/admin/settings", json={"initData": "x"}).get_json()["settings"].get(поле)

    # Лишний ноль в кэшбэке превращает 1% в 10% на каждом заказе — потолок
    # здесь не «на всякий случай», а защита выручки.
    c("кэшбэк выше потолка прижат", сохранить_и_прочитать("coins_per_byn", 9999) == 10.0)
    c("кэшбэк ниже нуля прижат", сохранить_и_прочитать("coins_per_byn", -5) == 0.0)
    c("буквы вместо числа не записываются",
      сохранить_и_прочитать("coins_per_byn", "бесплатно") == 0.0)   # осталось прежнее
    c("подтверждение не бывает нулевым", сохранить_и_прочитать("confirm_minutes", 0) == 1)
    c("шаг колеса не бывает нулевым", сохранить_и_прочитать("wheel_step", 0) == 1.0)
    c("компенсация не бывает миллионной", сохранить_и_прочитать("compensation_max", 10**7) == 100000)
    # Реквизиты — свободный текст, но нулевой байт Postgres не принимает вовсе.
    c("нулевой байт вычищен", "\x00" not in (сохранить_и_прочитать("payment_info", "хвост\x00голова") or ""))
    c("список вместо строки не роняет", сохранить_и_прочитать("payment_info", ["a"]) == "")
    сохранить_и_прочитать("payment_info", "Карта: 0000")
    return c.fails


def run_загрузки_и_поиск():
    """Потолок на файл и честность списка людей."""
    import io
    from _common import as_admin
    as_admin()
    провалы = []

    c = Checker("Файл больше потолка не читается в память")
    pid = db.add_product("Минск", "disposable", "КЧ-тест", 10.0, 1)
    r = client.post("/api/admin/photo", data={
        "initData": "x", "id": str(pid),
        "file": (io.BytesIO(b"\xff\xd8\xff" + b"x" * (13 * 1024 * 1024)), "big.png")},
        content_type="multipart/form-data")
    # 413 обязан прийти ДО чтения тела: иначе восемь таких запросов разом
    # (столько потоков у сервера) убивают процесс нехваткой памяти.
    c("13 МБ → 413", r.status_code == 413)
    c("и с человеческим текстом", "МБ" in ((r.get_json() or {}).get("message") or ""))
    провалы += c.fails

    c2 = Checker("Список людей не врёт о своём размере")
    conn = db.connect(); cur = conn.cursor()
    for i in range(320):
        cur.execute(db._q("INSERT INTO users (user_id, created_at, username) VALUES (%s,%s,%s)"),
                    (770000 + i, "2026-01-01 10:00", f"клиент{i}"))
    conn.commit(); conn.close()
    try:
        код, о = _post("/api/admin/users")
        # Список обрезан по 300. Само по себе это нормально, но «всего» обязано
        # говорить правду — иначе владелец считает, что видит всех.
        c2("показано не больше трёхсот", о.get("shown") <= 300)
        c2("а всего сказано честно", о.get("total") >= 320)
        c2("видно, что список урезан", о.get("shown") < о.get("total"))
        код, о = _post("/api/admin/users", search="770042")
        c2("поиск по точному id находит одного", о.get("shown") == 1)
        код, о = _post("/api/admin/users", search="'")
        c2("апостроф в поиске не роняет", код == 200)
        провалы += c2.fails
    finally:
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("DELETE FROM users WHERE user_id >= %s"), (770000,))
        conn.commit(); conn.close()
        cache.bust()
    return провалы


def run_мусор_вместо_картинки():
    """Не-картинка — ошибка ВВОДА, а не сбой сервера.

    Раньше файл улетал в Телеграм, тот отказывался, и мы отвечали пятисоткой.
    Админ читал «Не удалось» без единого намёка на причину и грузил тот же
    файл заново. У чека покупателя было хуже: приложение прямо советовало
    «попробуйте ещё раз» — с тем же файлом это не сработает никогда.
    """
    import io
    from _common import as_admin, as_user
    провалы = []

    as_admin()
    c = Checker("Фото товара: мусор отбивается внятно")
    pid = db.add_product("Минск", "disposable", "КЧ-мусор", 10.0, 1)
    мусор = [("PDF", b"%PDF-1.4\n", "chek.pdf", "application/pdf"),
             ("скрипт", b"#!/bin/sh\nrm -rf /\n", "evil.sh", "application/x-sh"),
             ("архив", b"PK\x03\x04", "a.zip", "application/zip")]
    for имя, тело, файл, тип in мусор:
        r = client.post("/api/admin/photo", data={
            "initData": "x", "id": str(pid), "file": (io.BytesIO(тело), файл, тип)},
            content_type="multipart/form-data")
        о = r.get_json() or {}
        # 400, а не 500: виноват файл, а не сервер.
        c(f"{имя}: отказ 400", r.status_code == 400 and о.get("error") == "not_image")
        c(f"{имя}: причина названа", "изображен" in (о.get("message") or "").lower())
    провалы += c.fails

    c2 = Checker("Чек покупателя: то же правило")
    as_user(9401)
    oid = db.create_order(9401, "b", "Минск",
                          [{"id": pid, "name": "т", "price": 10.0, "qty": 1}], 10.0, "")
    db.set_order_delivery(oid, "Самовывоз", "", 0, "card")
    r = client.post("/api/receipt", data={
        "initData": "x", "order_id": str(oid),
        "file": (io.BytesIO(b"%PDF-1.4\n"), "chek.pdf", "application/pdf")},
        content_type="multipart/form-data")
    о = r.get_json() or {}
    c2("PDF вместо чека отбит", r.status_code == 400 and о.get("error") == "not_image")
    # Человек уже заплатил: отказ обязан говорить, ЧТО прислать вместо этого.
    c2("сказано, что прислать", "снимок" in (о.get("message") or "").lower())
    c2("чек к заказу не привязался", db.get_order(oid)["receipt_file_id"] is None)
    провалы += c2.fails
    return провалы


def run_настройки_не_врут():
    """Прижали значение — обязаны сказать, к чему прижали.

    Владелец вводил кэшбэк 9999, читал «Сохранено ✅» и уходил уверенный, что
    так и есть, — а в базе лежала десятка. Ошибиться на порядок в проценте от
    КАЖДОГО заказа легко, а узнать об этом было неоткуда, кроме выручки через
    неделю.
    """
    from _common import as_admin
    as_admin()
    c = Checker("Сервер отвечает тем, что легло")

    def сохранить(**поля):
        return (client.post("/api/admin/settings/update",
                            json={"initData": "x", **поля}).get_json() or {})

    d = сохранить(coins_per_byn=9999)
    c("прижатое значение возвращается", d.get("applied", {}).get("coins_per_byn") == 10.0)
    d = сохранить(confirm_minutes=0)
    c("нижняя граница тоже видна", d.get("applied", {}).get("confirm_minutes") == 1)
    d = сохранить(coins_per_byn=2)
    c("нормальное значение возвращается как есть",
      d.get("applied", {}).get("coins_per_byn") == 2.0)
    d = сохранить(coins_per_byn="бесплатно")
    c("не-число не попадает в ответ вовсе", "coins_per_byn" not in d.get("applied", {}))

    # Реквизиты уходят КАЖДОМУ покупателю на экран оплаты — им нужен потолок.
    сохранить(payment_info="я" * 200000)
    легло = client.post("/api/admin/settings",
                        json={"initData": "x"}).get_json()["settings"]["payment_info"]
    c("реквизиты ограничены по длине", len(легло) <= 2000)
    сохранить(payment_info="Карта: 0000 0000 0000 0000")
    return c.fails


def run_поиск_ищет_буквы():
    """% и _ — это символы в нике, а не шаблон поиска."""
    from _common import as_admin
    as_admin()
    c = Checker("Джокеры LIKE не управляют поиском")
    conn = db.connect(); cur = conn.cursor()
    люди = [(880101, "vasya"), (880102, "ma_sha"), (880103, "skidka100%"), (880104, "petya")]
    for uid, ник in люди:
        cur.execute(db._q("INSERT INTO users (user_id, created_at, username) VALUES (%s,%s,%s)"),
                    (uid, "2026-01-01 10:00", ник))
    conn.commit(); conn.close()
    try:
        def найти(q):
            d = client.post("/api/admin/users",
                            json={"initData": "x", "search": q}).get_json()
            return {u["username"] for u in d["users"]}
        # Раньше «%» находил ВСЮ базу, а «ma_sha» — заодно и «masha», потому что
        # подчёркивание в LIKE значит «любой символ».
        c("процент ищется как символ", найти("%") == {"skidka100%"})
        c("подчёркивание ищется как символ", найти("_") == {"ma_sha"})
        c("обычный поиск не сломан", найти("vasya") == {"vasya"})
        c("шаблон не срабатывает", найти("%_%") == set())
        return c.fails
    finally:
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("DELETE FROM users WHERE user_id >= %s"), (880101,))
        conn.commit(); conn.close()
        cache.bust()


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run_права() + run_настройки() + run_загрузки_и_поиск()
                   + run_мусор_вместо_картинки() + run_настройки_не_врут()
                   + run_поиск_ищет_буквы()) else 0)
