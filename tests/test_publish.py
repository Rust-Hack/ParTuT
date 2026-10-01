"""Новый товар одним маршрутом: публикация — одна транзакция с ключом повтора.

Этап 2 редизайна «Товары». Раньше новый товар заводился в два раздела и
несколькими запросами подряд: модель в «Ассортименте», потом её фото, потом
«Завезти на точку». Сбой посередине оставлял полтовара, а повтор после
потерянного ответа — вторую модель.

Приёмка плана (docs/buyer-acceptance-d0968bd) задала условия, их здесь и
проверяем на настоящем обработчике и базе:
- модель, фото, точки, варианты и первый приход появляются вместе или никак;
- тот же ключ — тот же итог, без второй карточки и второго прихода; тот же
  ключ с другим содержимым не выполняется;
- количество ложится первым приходом с автором и закупкой;
- фото — только загруженные в черновик этим же человеком;
- двойник не создаётся, даже если публикуют одновременно;
- цена, количества и варианты проверяет сервер, а не форма;
- продавцу публикация закрыта.
"""
import datetime
import io
import threading

from _common import db, client, Checker, as_admin, deny_admin

from partut import cache


class _С:
    """Checker с подробностями: при провале к названию дописывается, что было."""

    def __init__(self, название):
        self._c = Checker(название)

    def __call__(self, что, ок, подробно=None):
        self._c(что if ок or подробно is None else f"{что} — {подробно}", ок)

    @property
    def fails(self):
        return self._c.fails


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "product_photos", "products", "models",
              "draft_photos", "publish_ops"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def _считать(таблица, где="", парам=()):
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q(f"SELECT COUNT(*) AS c FROM {таблица} {где}"), парам)
    n = int(cur.fetchone()["c"]); conn.close()
    return n


def _опубликовать(ключ, модель=None, точки=None, фото=None):
    # Закупка обязательна (cost_required). Проверкам, которые не о ней, она
    # нужна лишь затем, чтобы их отказ пришёл по их причине: подставляем 1,
    # если ключа "cost" нет вовсе. Пустую закупку проверяют явно — с ключом.
    if точки is not None:
        точки = [dict(т, cost="1") if isinstance(т, dict) and "cost" not in т else т for т in точки]
    тело = {"initData": "x", "client_token": ключ,
            "model": модель or {"category": "liquid", "name": "Chaser Lux", "brand": "Chaser",
                                "flavors": ["Мята", "Вишня", "Лимон"], "specs": {"strength": "20"}},
            "points": точки if точки is not None else [
                {"city": "Минск", "price": "18,5", "cost": "9.9",
                 "variants": [{"flavor": "Мята", "stock": 5}, {"flavor": "Вишня", "stock": 0},
                              {"flavor": "Лимон", "stock": 3}]}],
            "photos": фото or []}
    return client.post("/api/admin/product/publish", json=тело)


def run():
    c = _С("Новый товар: публикация целиком")
    _clean()
    as_admin(uid=100)

    # --- Товар со вкусами на одной точке ---
    r = _опубликовать("publish-key-0001")
    d = r.get_json()
    c("опубликован", r.status_code == 200 and d.get("ok") and d.get("model_id") and len(d.get("products") or []) == 1)
    m = db.get_model(d["model_id"])
    c("модель заведена со вкусами и характеристиками", m and m["flavors"] == ["Мята", "Вишня", "Лимон"]
      and (m["specs"] or {}).get("strength") == "20")
    pid = d["products"][0]["id"]
    p = db.get_product(pid)
    c("товар на точке: цена с запятой понята, закупка записана",
      p["city"] == "Минск" and abs(float(p["price"]) - 18.5) < 0.001 and abs(float(p["cost"]) - 9.9) < 0.001)
    вар = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c("варианты — ровно вписанные, с нулём тоже", вар == {"Мята": 5, "Вишня": 0, "Лимон": 3}, вар)
    c("итог товара — из вариантов", int(p["stock"]) == 8)
    ходы = db.get_stock_moves(pid, limit=10)
    c("первый приход в истории склада — по строке на вкус с количеством",
      sorted((h["flavor"], h["delta"]) for h in ходы) == [("Лимон", 3), ("Мята", 5)], [dict(h) for h in ходы])
    c("приход с автором, закупкой и пометкой", all(int(h["admin_id"]) == 100 and abs(float(h["cost"]) - 9.9) < 0.001
                                                   and "первый приход" in (h["note"] or "") for h in ходы))
    cache.bust()
    витрина = client.get("/api/products").get_json()
    c("покупатель сразу видит товар", any(x["id"] == pid for x in витрина))

    # --- Повтор с тем же ключом: тот же итог, второго товара нет ---
    r2 = _опубликовать("publish-key-0001")
    d2 = r2.get_json()
    c("повтор — тот же итог и пометка replay", d2.get("ok") and d2.get("replay") is True
      and d2.get("model_id") == d["model_id"] and d2["products"] == d["products"], d2)
    c("второй модели нет", _считать("models") == 1)
    c("второго товара нет", _считать("products") == 1)
    c("второго прихода нет", _считать("stock_moves") == 2)

    # --- Тот же ключ с другим содержимым — отказ, ничего не записано ---
    r3 = _опубликовать("publish-key-0001", точки=[{"city": "Минск", "price": "20",
                                                   "variants": [{"flavor": "Мята", "stock": 9}]}])
    d3 = r3.get_json()
    c("тот же ключ, другое содержимое — 409 token_reused, с прежним итогом",
      r3.status_code == 409 and d3.get("error") == "token_reused" and d3.get("recorded", {}).get("model_id") == d["model_id"], d3)
    c("и ничего не записано", _считать("products") == 1 and _считать("stock_moves") == 2)

    # --- Двойник: та же модель (регистр и кириллица не спасают) ---
    r4 = _опубликовать("publish-key-0002", модель={"category": "liquid", "name": "chaser lux", "brand": "CHASER",
                                                   "flavors": ["Мята"]},
                       точки=[{"city": "Туров", "price": 18, "variants": [{"flavor": "Мята", "stock": 1}]}])
    d4 = r4.get_json()
    c("двойник — 409 exists со ссылкой на имеющуюся модель",
      r4.status_code == 409 and d4.get("error") == "exists" and d4.get("model_id") == d["model_id"], d4)
    c("двойник не создан", _считать("models") == 1 and _считать("products") == 1)
    кир = _опубликовать("publish-key-0003", модель={"category": "accessories", "name": "Зарядка Быстрая", "flavors": []},
                        точки=[{"city": "Минск", "price": 12, "stock": 2}]).get_json()
    кир2 = _опубликовать("publish-key-0004", модель={"category": "accessories", "name": "зарядка быстрая", "flavors": []},
                         точки=[{"city": "Туров", "price": 12, "stock": 1}]).get_json()
    c("двойник по-русски другим регистром — тоже exists", кир.get("ok") and кир2.get("error") == "exists", кир2)

    # --- Товар без вариантов на двух точках ---
    r5 = _опубликовать("publish-key-0005", модель={"category": "accessories", "name": "Кабель USB-C", "flavors": []},
                       точки=[{"city": "Минск", "price": "7", "cost": "3", "stock": 4},
                              {"city": "Туров", "price": "7.5", "stock": 0}])
    d5 = r5.get_json()
    по_городам = {x["city"]: x["id"] for x in d5.get("products") or []}
    c("без вариантов — товар на двух точках", d5.get("ok") and set(по_городам) == {"Минск", "Туров"}, d5)
    c("остатки по точкам как вписаны", int(db.get_product(по_городам["Минск"])["stock"]) == 4
      and int(db.get_product(по_городам["Туров"])["stock"]) == 0)
    c("приход — только там, где он был", len(db.get_stock_moves(по_городам["Минск"], limit=5)) == 1
      and not db.get_stock_moves(по_городам["Туров"], limit=5))

    # --- Фото: только из черновика этого человека ---
    db.add_draft_photo(100, "draft-main", "draft-main-thumb")
    db.add_draft_photo(100, "draft-extra", "draft-extra-thumb")
    db.add_draft_photo(555, "draft-alien", "")
    r6 = _опубликовать("publish-key-0006", модель={"category": "accessories", "name": "Чехол", "flavors": []},
                       точки=[{"city": "Минск", "price": 5, "stock": 1}], фото=["draft-main", "draft-extra"])
    d6 = r6.get_json()
    m6 = db.get_model(d6.get("model_id")) if d6.get("ok") else None
    c("фото: главное — у модели и товара, второе — в галерее модели",
      m6 and m6["photo"] == "draft-main" and m6["photo_thumb"] == "draft-main-thumb"
      and db.get_product(d6["products"][0]["id"])["photo"] == "draft-main"
      and [g["file_id"] for g in db.model_photos(d6["model_id"])] == ["draft-extra"], d6)
    c("использованные фото черновика убраны", _считать("draft_photos", "WHERE admin_id = %s", (100,)) == 0)
    r7 = _опубликовать("publish-key-0007", модель={"category": "accessories", "name": "Брелок", "flavors": []},
                       точки=[{"city": "Минск", "price": 5, "stock": 1}], фото=["draft-alien"])
    c("чужое фото (загружал другой человек) — отказ, товар не создан",
      r7.status_code == 400 and r7.get_json().get("error") == "bad_photo" and not any(
          m["name"] == "Брелок" for m in db.list_models()))
    r8 = _опубликовать("publish-key-0008", модель={"category": "accessories", "name": "Брелок", "flavors": []},
                       точки=[{"city": "Минск", "price": 5, "stock": 1}], фото=["fid-of-some-receipt"])
    c("произвольный file_id (например, чека) — отказ", r8.status_code == 400 and r8.get_json().get("error") == "bad_photo")

    # --- Загрузка фото в черновик ---
    r9 = client.post("/api/admin/photo/draft", data={"initData": "x", "file": (io.BytesIO(b"jpegdata"), "p.jpg")},
                     content_type="multipart/form-data")
    d9 = r9.get_json()
    c("фото в черновик: file_id и миниатюра", d9.get("ok") and d9.get("file_id") and d9.get("thumb", "").startswith("/api/photo"), d9)
    c("и оно числится за этим человеком", _считать("draft_photos", "WHERE admin_id = %s AND file_id = %s",
                                                  (100, d9.get("file_id"))) == 1)
    return c.fails


def run_проверки_сервера():
    """Цена, количества и варианты проверяет сервер — форма может ошибиться."""
    c = _С("Новый товар: проверки на сервере")
    _clean()
    as_admin(uid=100)
    плохие = [
        ("категория", {"model": {"category": "nope", "name": "X"}}, "bad_category"),
        ("пустое название", {"model": {"category": "accessories", "name": "  "}}, "no_name"),
        ("цена 0", {"points": [{"city": "Минск", "price": 0, "stock": 1}]}, "bad_price"),
        ("цена буквами", {"points": [{"city": "Минск", "price": "abc", "stock": 1}]}, "bad_price"),
        ("закупка минус", {"points": [{"city": "Минск", "price": 5, "cost": -1, "stock": 1}]}, "bad_cost"),
        ("количество минус", {"points": [{"city": "Минск", "price": 5, "stock": -1}]}, "bad_number"),
        ("количество дробное", {"points": [{"city": "Минск", "price": 5, "stock": "2.5"}]}, "bad_number"),
        ("количество буквами", {"points": [{"city": "Минск", "price": 5, "stock": "много"}]}, "bad_number"),
        ("закупка буквами", {"points": [{"city": "Минск", "price": 5, "cost": "дёшево", "stock": 1}]}, "bad_cost"),
        ("закупка пустая", {"points": [{"city": "Минск", "price": 5, "cost": "", "stock": 1}]}, "cost_required"),
        ("закупка не передана", {"points": [{"city": "Минск", "price": 5, "cost": None, "stock": 1}]}, "cost_required"),
        ("нет такой точки", {"points": [{"city": "Атлантида", "price": 5, "stock": 1}]}, "bad_point"),
        ("точка дважды", {"points": [{"city": "Минск", "price": 5, "stock": 1}, {"city": "Минск", "price": 6, "stock": 1}]}, "bad_point"),
        ("нет точек", {"points": []}, "no_point"),
        ("варианты у категории без вариантов", {"model": {"category": "accessories", "name": "X", "flavors": ["A"]}}, "no_variants"),
    ]
    for что, правка, код in плохие:
        модель = {"category": "accessories", "name": "Проверка", "flavors": []}
        модель.update(правка.get("model", {}))
        точки = правка.get("points", [{"city": "Минск", "price": 5, "stock": 1}])
        r = _опубликовать(f"check-key-{abs(hash(что)) % 10**8:08d}", модель=модель, точки=точки)
        d = r.get_json()
        c(f"{что} — отказ «{код}» с понятным текстом", r.status_code == 400 and d.get("error") == код and d.get("message"), d)
    вкусная = {"category": "liquid", "name": "Проверка вкусов", "flavors": ["Мята", "Вишня"]}
    for что, точки, код in [
        ("вариант не из списка", [{"city": "Минск", "price": 5, "variants": [{"flavor": "Арбуз", "stock": 1}]}], "bad_variant"),
        ("вариант дважды", [{"city": "Минск", "price": 5, "variants": [{"flavor": "Мята", "stock": 1},
                                                                         {"flavor": "Мята", "stock": 2}]}], "bad_variant"),
        ("ни одного варианта на точке", [{"city": "Минск", "price": 5, "variants": []}], "no_variants"),
    ]:
        d = _опубликовать(f"check-var-{abs(hash(что)) % 10**8:08d}", модель=вкусная, точки=точки).get_json()
        c(f"{что} — отказ «{код}»", d.get("error") == код, d)
    r = client.post("/api/admin/product/publish", json={"initData": "x", "client_token": "short",
                                                          "model": {"category": "accessories", "name": "X"}})
    c("без годного ключа публикации — отказ", r.status_code == 400 and r.get_json().get("error") == "bad_token")
    c("ни одна проверка ничего не создала", _считать("models") == 0 and _считать("products") == 0
      and _считать("publish_ops") == 0)

    # Продавец точки — публикация закрыта (общий ассортимент — дело владельца).
    as_admin(uid=300, role="seller", city="Минск")
    r = _опубликовать("seller-key-0001", модель={"category": "accessories", "name": "От продавца", "flavors": []},
                      точки=[{"city": "Минск", "price": 5, "stock": 1}])
    c("продавцу — 403 owner_only", r.status_code == 403 and r.get_json().get("error") == "owner_only")
    deny_admin()
    r = _опубликовать("stranger-key-001")
    c("постороннему — 403", r.status_code == 403)
    as_admin()
    _clean()
    return c.fails


def run_всё_или_ничего():
    """Сбой посередине публикации не оставляет ни модели, ни товара, ни ключа."""
    c = _С("Новый товар: всё или ничего")
    _clean()
    as_admin(uid=100)
    настоящий = db._record_move
    вызовов = {"n": 0}

    def сломанный(*a, **k):
        вызовов["n"] += 1
        if вызовов["n"] == 2:
            raise RuntimeError("сбой базы на втором приходе")
        return настоящий(*a, **k)
    db._record_move = сломанный
    try:
        r = _опубликовать("atomic-key-0001")
        c("сбой посередине — ответ-ошибка", r.status_code >= 500 or not (r.get_json() or {}).get("ok"))
    finally:
        db._record_move = настоящий
    c("модели нет", _считать("models") == 0)
    c("товара нет", _считать("products") == 0)
    c("вариантов нет", _считать("product_variants") == 0)
    c("прихода нет", _считать("stock_moves") == 0)
    c("ключ не занят — повтор возможен", _считать("publish_ops") == 0)
    r = _опубликовать("atomic-key-0001")
    c("повтор с тем же ключом после сбоя — проходит целиком", r.get_json().get("ok") and _считать("products") == 1
      and _считать("stock_moves") == 2)
    _clean()
    return c.fails


def run_одновременно():
    """Два владельца одновременно публикуют одну и ту же модель — один товар."""
    c = _С("Новый товар: одновременная публикация")
    _clean()
    модель = {"category": "accessories", "name": "Одновременный", "brand": "", "description": "", "specs": {}, "flavors": []}
    итоги, беды = [], []

    def публикатор(i):
        try:
            итоги.append(db.publish_product(100 + i, f"race-key-{i:08d}", f"f{i}", модель, [],
                                            [{"city": "Минск", "price": 5, "cost": 0, "stock": 1}]))
        except db.PublishRefused as e:
            беды.append(e.code)
        except Exception as e:
            беды.append(f"сбой: {e}")
    # Окно между проверкой «такой модели ещё нет» и её созданием в жизни
    # узкое, и потоки в него почти не попадают — тест проходил бы и без
    # замка. Растягиваем окно: создание модели ждёт 0.3 с. С замком второй
    # публикатор ждёт у замка и видит готовую модель; без замка оба проходят
    # проверку и создают двойника.
    import time
    настоящий = db._insert_id

    def медленный(cur, sql, params):
        if "INSERT INTO models" in sql:
            time.sleep(0.3)
        return настоящий(cur, sql, params)
    db._insert_id = медленный
    try:
        потоки = [threading.Thread(target=публикатор, args=(i,)) for i in range(4)]
        for п in потоки:
            п.start()
        for п in потоки:
            п.join()
    finally:
        db._insert_id = настоящий
    c("ровно одна публикация прошла", len(итоги) == 1, {"итоги": итоги, "беды": беды})
    c("остальные — «такая модель уже есть», без сбоев", sorted(беды) == ["exists"] * 3, беды)
    c("в базе одна модель и один товар", _считать("models") == 1 and _считать("products") == 1)
    _clean()
    return c.fails


def _состарить(file_id, дней):
    когда = (db.shop_now() - datetime.timedelta(days=дней)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE draft_photos SET created_at = %s WHERE file_id = %s"), (когда, file_id))
    conn.commit(); conn.close()


def run_уборка_черновых_фото():
    """Фото брошенного черновика не лежит в базе вечно: ночная уборка убирает
    строки старше срока, свежие не трогает. Опубликовать с убранным фото
    нельзя — отказ понятный, товара нет."""
    c = _С("Новый товар: уборка фото брошенных черновиков")
    _clean()
    db.add_draft_photo(100, "draft-old", "draft-old-s")
    db.add_draft_photo(100, "draft-fresh", "draft-fresh-s")
    _состарить("draft-old", db.DRAFT_PHOTO_KEEP_DAYS + 1)
    _состарить("draft-fresh", db.DRAFT_PHOTO_KEEP_DAYS - 1)
    c("убрана одна строка — старше срока", db.purge_draft_photos() == 1)
    c("фото младше срока на месте", _считать("draft_photos", "WHERE file_id = %s", ("draft-fresh",)) == 1)
    c("повторная уборка убирать больше нечего", db.purge_draft_photos() == 0)
    as_admin(100)
    r = _опубликовать("cleanup-key-0001", фото=["draft-old"])
    c("публикация с убранным фото — понятный отказ, товара нет",
      r.status_code == 400 and (r.get_json() or {}).get("error") == "bad_photo" and _считать("models") == 0, r.get_json())
    r = _опубликовать("cleanup-key-0002", фото=["draft-fresh"])
    c("с фото младше срока — публикуется", r.status_code == 200 and (r.get_json() or {}).get("ok"), r.get_json())

    # Уборка стоит в ночных делах, а не только существует.
    from partut.bot import handlers as botmod
    db.add_draft_photo(100, "draft-night", "")
    _состарить("draft-night", db.DRAFT_PHOTO_KEEP_DAYS + 5)
    db.set_setting(botmod._CLEANUP_MARK, "")
    настоящий_час, botmod.BACKUP_HOUR = botmod.BACKUP_HOUR, 0
    try:
        botmod._nightly_cleanup()
    finally:
        botmod.BACKUP_HOUR = настоящий_час
        db.set_setting(botmod._CLEANUP_MARK, "")
    c("ночью фото брошенного черновика убирается само",
      _считать("draft_photos", "WHERE file_id = %s", ("draft-night",)) == 0)
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    fails = run() + run_проверки_сервера() + run_всё_или_ничего() + run_одновременно() + run_уборка_черновых_фото()
    sys.exit(1 if fails else 0)
