"""Склад без дыр: остаток меняется только движением, и движение честное.

Проверка 29–30 сентября 2026 нашла пять способов, которыми учёт расходился с
полкой, — каждый воспроизведён на временной базе:

1. Пересчёт при невыданных заказах записывал «нашлись лишние»: заказ снимает
   товар с остатка сразу, а с полки он уходит только при выдаче.
2. Остаток из карточки товара и из бота ставился числом мимо истории склада.
3. Приход на вкус, которого у товара нет, «записывался», а остаток не менялся.
4. Приход без вкуса у товара со вкусами сбивал итог мимо вариантов.
5. Повтор нажатия после потерянного ответа записывал приход второй раз.

И ещё два попутно: массовый приход не сбрасывал кэш (список ещё полминуты
показывал старые остатки, ждавшим поступления никто не писал), а завоз на
точку шёл несколькими отдельными записями без следа в истории.
"""
import types

from _common import db, client, Checker, as_admin, SENT, reset_sent

from partut import cache
from partut import config
from partut.bot import handlers as botmod
from partut.integrations import tgsend

КЛИЕНТ = 770001


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("stock_moves", "product_variants", "products", "models", "orders", "stock_alerts"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def _заказ(pid, qty, flavor=None, method="Самовывоз", status="confirmed"):
    """Заказ так, как его оформляет магазин: остаток снимается сразу."""
    item = {"id": pid, "name": "т", "price": 10.0, "qty": qty}
    if flavor:
        item["flavor"] = flavor
    oid, *_ = db.place_order(КЛИЕНТ, "buyer", "Минск", [item], 10.0 * qty, 0, 0.01, 0, method, "",
                             "cash", "", "", status)
    return oid


def _ход(**тело):
    return client.post("/api/admin/stock/move", json={"initData": "x", **тело})


def _ходов(pid):
    return len(db.get_stock_moves(pid, limit=500))


def run_пересчёт_с_заказами():
    """Пересчёт спрашивает, чей отложенный товар вошёл в насчитанное.

    Самовывоз лежит на точке до выдачи, а заказ с такси уже уехал, хотя
    выданным ещё не отмечен. Угадывать нельзя: продавец отмечает заказы сам."""
    c = Checker("Пересчёт с учётом невыданных заказов")
    _clean(); as_admin(uid=771)

    pid = db.add_product("Минск", "pods", "ПолкаПод", 20.0, 5, cost=12.0)
    самовывоз = _заказ(pid, 2)
    c("заказ снял 2 шт с остатка сразу", db.get_product(pid)["stock"] == 3)

    # Окно пересчёта знает, какие заказы ждут этот товар.
    d = client.post("/api/admin/stock/moves", json={"initData": "x", "id": pid}).get_json()
    c("окно пересчёта видит невыданный заказ",
      [(z["order_id"], z["qty"]) for z in d.get("reserved_orders", [])] == [(самовывоз, 2)])

    # На полке физически 5 (3 свободных + 2 отложенных). Продавец посчитал всё
    # и отметил заказ — свободных так и остаётся 3, записывать нечего.
    r = _ход(id=pid, qty=5, reason="fix", counted_orders=[самовывоз], expected=3, counted_scope="orders")
    c("полка сходится — «записывать нечего», а не «нашлись лишние»",
      r.status_code == 400 and r.get_json()["error"] == "no_change")
    c("остаток не тронут", db.get_product(pid)["stock"] == 3)

    # После выдачи на полке 3 — и в базе 3. Раньше тут было 5.
    db.issue_order(самовывоз)
    c("после выдачи в базе столько же, сколько на полке", db.get_product(pid)["stock"] == 3)

    # --- Курьер: товар уже уехал, заказ ещё не выдан ---
    такси = _заказ(pid, 1, method="Доставка такси")
    ещё = _заказ(pid, 1)
    c("свободно 1", db.get_product(pid)["stock"] == 1)
    # На точке физически: 1 свободный + 1 отложенный под самовывоз = 2.
    # Продавец посчитал 2 и отметил только самовывоз: такси не на полке.
    r = _ход(id=pid, qty=2, reason="fix", counted_orders=[ещё], expected=1, counted_scope="orders")
    c("с такси, который уехал, пересчёт тоже сходится",
      r.status_code == 400 and r.get_json()["error"] == "no_change")

    # Нашёл меньше: на полке 1, а отмечен самовывоз на 1 → свободных 0.
    r = _ход(id=pid, qty=1, reason="fix", counted_orders=[ещё], expected=1, counted_scope="orders")
    d = r.get_json()
    c("недостача записана", d.get("ok") and d["stock"] == 0 and d["delta"] == -1)
    c("в ответе видно, сколько учли под заказы", d.get("counted") == 1)
    ход = db.get_stock_moves(pid, limit=1)[0]
    c("в истории сказано, что считали вместе с отложенным", "под заказы" in (ход["note"] or ""))

    # На полке 0, а под самовывоз обещан 1 — не хватает, и это сказано прямо.
    r = _ход(id=pid, qty=0, reason="fix", counted_orders=[ещё], expected=0, counted_scope="orders")
    c("нехватка под заказ названа, а не проглочена",
      r.status_code == 400 and r.get_json()["error"] == "short")

    # Заказ, который тем временем выдали, отмечать нельзя — экран устарел.
    db.issue_order(такси)
    r = _ход(id=pid, qty=1, reason="fix", counted_orders=[такси], expected=0, counted_scope="orders")
    c("выданный заказ в пересчёте — отказ «обновите экран»",
      r.status_code == 409 and r.get_json()["error"] == "orders_changed")

    # Пока считали, пришёл новый заказ — пересчёт устарел.
    _ход(id=pid, qty=4, reason="in")
    _заказ(pid, 1)
    r = _ход(id=pid, qty=9, reason="fix", counted_orders=[], counted_scope="free", expected=4)
    c("новый заказ во время пересчёта — конфликт, а не перезапись",
      r.status_code == 409 and r.get_json()["error"] == "stock_conflict")

    # --- Ответ «что посчитали» обязателен, если что-то отложено ---
    # Молчание не отличить от «экран не успел загрузить заказы»: принять его
    # как «только свободное» — значит завысить остаток ровно на отложенное.
    при = db.add_product("Минск", "pods", "ОтветПод", 20.0, 5, cost=12.0)
    _заказ(при, 2)
    r = _ход(id=при, qty=5, reason="fix", expected=3)
    c("отложено 2, ответа нет — отказ, а не «нашлись лишние»",
      r.status_code == 400 and r.get_json()["error"] == "need_counted_choice")
    c("остаток не тронут", db.get_product(при)["stock"] == 3)
    r = _ход(id=при, qty=3, reason="fix", counted_scope="free", counted_orders=[99], expected=3)
    c("«только свободное» и отмеченные заказы вместе — отказ", r.status_code == 400)
    r = _ход(id=при, qty=2, reason="fix", counted_scope="free", expected=3)
    c("ответ «только свободное» принят: насчитали 2 свободных", r.get_json().get("ok") and db.get_product(при)["stock"] == 2)
    без = db.add_product("Минск", "pods", "БезЗаказовПод", 20.0, 5, cost=12.0)
    r = _ход(id=без, qty=4, reason="fix", expected=5)
    c("ничего не отложено — ответ не нужен (так пересчитывает и бот)", r.get_json().get("ok"))

    _clean()
    return c.fails


def run_списание_и_варианты():
    """Списание не больше свободного; движение — только по существующему варианту."""
    c = Checker("Честное списание и варианты")
    _clean(); as_admin(uid=772)

    pid = db.add_product("Минск", "pods", "СписаниеПод", 20.0, 3, cost=12.0)
    _заказ(pid, 2)
    r = _ход(id=pid, qty=2, reason="broken")
    d = r.get_json()
    c("списать больше свободного нельзя", r.status_code == 400 and d["error"] == "not_enough")
    c("в отказе сказано про невыданные заказы", "невыданных" in (d.get("message") or ""))
    c("остаток не тронут", db.get_product(pid)["stock"] == 1)
    c("в историю ничего не легло", _ходов(pid) == 0)

    vid = db.add_product("Минск", "disposable", "ВкусоПод", 25.0, 0, cost=15.0)
    db.add_variant(vid, "Мята", 5)
    db.recalc_product_stock(vid)

    r = _ход(id=vid, qty=10, reason="in", flavor="Манго")
    c("приход на несуществующий вкус — отказ", r.status_code == 409 and r.get_json()["error"] == "variant_missing")
    c("остаток не тронут", db.get_product(vid)["stock"] == 5)
    c("фантомного «+10» в истории нет", _ходов(vid) == 0)

    r = _ход(id=vid, qty=4, reason="in")
    c("приход без вкуса у товара со вкусами — отказ",
      r.status_code == 400 and r.get_json()["error"] == "need_variant")
    c("итог не разошёлся с вариантами", db.get_product(vid)["stock"] == 5)

    r = _ход(id=pid, qty=1, reason="in", flavor="Мята")
    c("вкус у товара без вкусов — отказ", r.status_code == 400 and r.get_json()["error"] == "no_variants")

    r = _ход(id=vid, qty=3, reason="in", flavor="Мята")
    d = r.get_json()
    c("приход по существующему вкусу проходит", d.get("ok") and d["left"] == 8 and d["stock"] == 8)

    _clean()
    return c.fails


def run_повтор_не_двоит():
    """Ответ потерялся — повтор с тем же ключом не записывается второй раз.
    Тот же ключ с другим содержимым — это другая операция, и она отклоняется."""
    c = Checker("Повтор операции склада")
    _clean(); as_admin(uid=773)

    pid = db.add_product("Минск", "pods", "ПовторПод", 20.0, 0, cost=12.0)
    r1 = _ход(id=pid, qty=10, reason="in", client_token="tok-povtor-0001")
    r2 = _ход(id=pid, qty=10, reason="in", client_token="tok-povtor-0001")
    c("первый приход записан", r1.get_json().get("ok") and not r1.get_json().get("replay"))
    c("повтор узнан", r2.get_json().get("ok") and r2.get_json().get("replay") is True)
    c("остаток 10, а не 20", db.get_product(pid)["stock"] == 10)
    c("движение одно", _ходов(pid) == 1)

    r3 = _ход(id=pid, qty=12, reason="in", client_token="tok-povtor-0001")
    c("тот же ключ с другим числом — отказ", r3.status_code == 409 and r3.get_json()["error"] == "token_reused")
    c("в отказе сказано, что уже записано", "+10" in (r3.get_json().get("message") or ""))
    # Экрану — не только текст, но и что именно записано: он покажет это у
    # строки и не отправит число из поля второй раз (S-02).
    c("в отказе — записанная операция", r3.get_json().get("recorded") == {"reason": "in", "delta": 10, "flavor": ""})
    r4 = _ход(id=pid, qty=10, reason="in", cost="15", client_token="tok-povtor-0001")
    c("тот же ключ с другой ценой — тоже отказ", r4.status_code == 409)
    c("остаток всё ещё 10", db.get_product(pid)["stock"] == 10)

    r = _ход(id=pid, qty=1, reason="in", client_token="плохой ключ!")
    c("кривой ключ — отказ, а не молчаливая работа без защиты", r.status_code == 400)

    # --- Пачка: у каждой строки свой ключ ---
    vid = db.add_product("Минск", "disposable", "ПачкаВкусы", 25.0, 0, cost=15.0)
    db.add_variant(vid, "Мята", 0)
    db.add_variant(vid, "Вишня", 0)
    db.recalc_product_stock(vid)
    строки = [{"id": vid, "flavor": "Мята", "qty": 5, "token": "row-mint-00001"},
              {"id": vid, "flavor": "Манго", "qty": 7, "token": "row-mango-0001"},
              {"id": vid, "flavor": "Вишня", "qty": 3, "token": "row-cherr-0001"}]
    d = client.post("/api/admin/stock/move/batch",
                    json={"initData": "x", "reason": "in", "items": строки}).get_json()
    c("две строки проведены", {x["index"] for x in d["done"]} == {0, 2})
    c("строка с несуществующим вкусом названа по месту",
      d["failed"].get("1", {}).get("error") == "variant_missing")

    # Ответ «потерялся» — экран шлёт ту же пачку с теми же ключами строк.
    d2 = client.post("/api/admin/stock/move/batch",
                     json={"initData": "x", "reason": "in", "items": строки}).get_json()
    c("повтор пачки: проведённые строки узнаны", all(x.get("replay") for x in d2["done"]))
    вкусы = {v["flavor"]: v["stock"] for v in db.get_variants(vid)}
    c(f"ничего не удвоилось: {вкусы}", вкусы == {"Мята": 5, "Вишня": 3})
    c("движений ровно два", _ходов(vid) == 2)

    _clean()
    return c.fails


def run_карточка_и_бот_не_обходят_склад():
    """Остаток не меняется ни карточкой, ни ботом мимо истории склада."""
    c = Checker("Карточка и бот — только через склад")
    _clean(); as_admin(uid=774)

    pid = db.add_product("Минск", "pods", "ОбходПод", 20.0, 5, cost=12.0)
    r = client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                    "fields": {"stock": "9", "price": "22"}, "expected_stock": 5})
    d = r.get_json()
    c("цена сохранилась", "price" in (d.get("saved") or []))
    c("остаток — отказ «только через склад»", d["failed"]["stock"]["error"] == "use_stock_moves")
    c("остаток не тронут", db.get_product(pid)["stock"] == 5)
    c("низкоуровневая правка поля тоже не пускает остаток", db.update_field(pid, "stock", 99) is False)
    c("и после неё остаток прежний", db.get_product(pid)["stock"] == 5)

    # --- Бот ---
    old_super = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = old_super | {774}
    orig = botmod.bot.answer_callback_query
    botmod.bot.answer_callback_query = lambda *a, **k: SENT.append(("cb", (a[1] if len(a) > 1 else k.get("text", "")), None))
    call = lambda data: types.SimpleNamespace(  # noqa: E731
        data=data, id="c1", from_user=types.SimpleNamespace(id=774, username="o"),
        message=types.SimpleNamespace(chat=types.SimpleNamespace(id=774), message_id=1))
    try:
        reset_sent(); botmod.on_button(call(f"admset:stock:{pid}"))
        c("бот спрашивает пересчёт свободного", "СВОБОДНО" in " ".join(str(s[1]) for s in SENT))
        botmod.handle_admin_input(774, 774, "7")
        c("пересчёт из бота прошёл", db.get_product(pid)["stock"] == 7)
        ход = db.get_stock_moves(pid, limit=1)[0]
        c("и лёг в историю как пересчёт с автором",
          ход["reason"] == "fix" and ход["delta"] == 2 and int(ход["admin_id"]) == 774)

        vid = db.add_product("Минск", "disposable", "БотВкусы", 25.0, 0, cost=15.0)
        db.add_variant(vid, "Мята", 5)
        db.recalc_product_stock(vid)
        reset_sent(); botmod.on_button(call(f"admset:stock:{vid}"))
        c("товар со вкусами бот отправляет в приложение", "по вариантам" in " ".join(str(s[1]) for s in SENT))
        c("и ввода не ждёт", botmod.admin_state.get(774) is None)
    finally:
        botmod.bot.answer_callback_query = orig
        botmod.admin_state.pop(774, None)
        config.SUPER_ADMIN_IDS = old_super

    _clean()
    return c.fails


def run_состав_вариантов():
    """Состав вариантов меняется отдельно от их остатков и по правилам склада."""
    c = Checker("Состав вариантов")
    _clean(); as_admin(uid=775)

    mid = db.add_model("disposable", "Модель", "BR", "", {}, ["Мята"])
    pid = db.create_point_product(mid, "Минск", 30.0, 15.0, variants=[{"flavor": "Мята", "stock": 4}])
    _заказ(pid, 1, flavor="Мята")
    зв = lambda **т: client.post("/api/admin/product/variants/change", json={"initData": "x", "id": pid, **т})  # noqa: E731

    r = зв(add=[{"flavor": "Манго", "qty": 6}])
    c("новый вкус с первым приходом", r.get_json().get("ok") and db.get_product(pid)["stock"] == 9)
    ход = [m for m in db.get_stock_moves(pid, limit=10) if m["flavor"] == "Манго"]
    c("первый приход — в истории", len(ход) == 1 and ход[0]["reason"] == "in" and ход[0]["delta"] == 6)
    c("вкус попал в модель", "Манго" in db.get_model(mid)["flavors"])

    r = зв(remove=["Мята"], writeoff=True)
    c("вкус под невыданный заказ не убирается",
      r.status_code == 409 and r.get_json()["error"] == "reserved")
    r = зв(remove=["Манго"])
    c("вкус с остатком без подтверждения — отказ", r.get_json()["error"] == "has_stock")
    r = зв(remove=["Манго"], writeoff=True)
    c("с подтверждением — убран", r.get_json().get("ok") and r.get_json()["written_off"] == {"Манго": 6})
    c("списание — в истории", any(m["flavor"] == "Манго" and m["delta"] == -6
                                   for m in db.get_stock_moves(pid, limit=10)))

    r = зв(add=[{"flavor": "Лимон", "qty": -1}])
    c("отрицательный приход — отказ, а не тихий ноль", r.status_code == 400)
    c("и Лимона не появилось", "Лимон" not in {v["flavor"] for v in db.get_variants(pid)})

    _clean()
    return c.fails


def run_завоз_одной_транзакцией():
    """Завоз на точку — одной транзакцией и с первым приходом в истории."""
    c = Checker("Завоз на точку")
    _clean(); as_admin(uid=776)

    mid = db.add_model("disposable", "ЗавозМодель", "BR", "", {}, ["Мята", "Вишня"])
    r = client.post("/api/admin/product/from-model", json={
        "initData": "x", "model_id": mid, "city": "Минск", "price": "30", "cost": "15",
        "variants": [{"flavor": "Мята", "stock": 4}, {"flavor": "Вишня", "stock": 2}]})
    pid = r.get_json()["id"]
    ходы = {m["flavor"]: (m["reason"], m["delta"]) for m in db.get_stock_moves(pid, limit=10)}
    c("первый приход по каждому вкусу — в истории", ходы == {"Мята": ("in", 4), "Вишня": ("in", 2)})
    c("итог собран", db.get_product(pid)["stock"] == 6)

    # Сбой посередине: запись в историю падает — не должно остаться ничего.
    настоящий = db._record_move
    счёт = {"n": 0}

    def ломается(*a, **k):
        счёт["n"] += 1
        if счёт["n"] == 2:
            raise RuntimeError("сбой базы посередине")
        return настоящий(*a, **k)

    было = len(db.get_all_products())
    db._record_move = ломается
    try:
        try:
            db.create_point_product(mid, "Туров", 30.0, 15.0,
                                    variants=[{"flavor": "Мята", "stock": 1}, {"flavor": "Вишня", "stock": 1}])
        except RuntimeError:
            pass
    finally:
        db._record_move = настоящий
    c("после сбоя на точке нет полутовара", len(db.get_all_products()) == было)
    c("и вариантов-сирот нет", all(v["product_id"] in {p["id"] for p in db.get_all_products()}
                                   for v in db.get_all_variants()))

    _clean()
    return c.fails


def run_список_и_кэш():
    """Резерв виден админу и не виден покупателю; пачка сбрасывает кэш."""
    c = Checker("Список товаров и кэш")
    _clean(); as_admin(uid=777)
    real_bg = tgsend.bg
    tgsend.bg = lambda fn, *a, **k: fn(*a, **k)
    try:
        pid = db.add_product("Минск", "pods", "КэшПод", 20.0, 3, cost=12.0)
        _заказ(pid, 2)
        строка = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                      if p["id"] == pid)
        c("админ видит, сколько в невыданных заказах", строка.get("reserved") == 2)
        витрина = next(p for p in client.get("/api/products").get_json() if p["id"] == pid)
        c("покупатель — нет", "reserved" not in витрина)

        ждём = db.add_product("Минск", "pods", "ЖдёмПод", 20.0, 0, cost=12.0)
        db.add_stock_alert(ждём, КЛИЕНТ)
        cache.bust()
        # Предусловие: кэш прогрет и показывает старый остаток — иначе проверка
        # ниже прошла бы и без сброса кэша, ничего не доказав.
        до = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                  if p["id"] == ждём)
        c("до прихода список показывает 0", до["stock"] == 0)
        reset_sent()
        client.post("/api/admin/stock/move/batch", json={"initData": "x", "reason": "in",
                                                         "items": [{"id": ждём, "qty": 7}]})
        строка = next(p for p in client.post("/api/admin/products", json={"initData": "x"}).get_json()["products"]
                      if p["id"] == ждём)
        c("после массового прихода список показывает новый остаток", строка["stock"] == 7)
        c("ждавшему сообщили о поступлении", any("снова в наличии" in str(t) for _, t, _ in SENT))
    finally:
        tgsend.bg = real_bg

    _clean()
    return c.fails


def run_журнал_было_стало():
    """Журнал отвечает на «кто, когда и что именно»: с прежним значением.

    Раньше правка карточки записывалась как «product/update · id=5» — ни что
    изменили, ни каким оно было."""
    from flask import g
    from partut.web import auth
    c = Checker("Журнал: было → стало")
    _clean()

    def владелец(init):
        u = {"id": 778, "username": "owner", "role": "owner", "city": "", "name": "Владелец"}
        g.admin = u               # как настоящий get_admin: журнал берёт админа отсюда
        return u

    старый = auth.get_admin
    auth.get_admin = владелец
    try:
        pid = db.add_product("Минск", "pods", "ЖурналПод", 20.0, 5, cost=12.0)
        client.post("/api/admin/product/update", json={"initData": "x", "id": pid,
                    "fields": {"price": "25", "is_hit": 1, "cost": "12"}})
        _ход(id=pid, qty=3, reason="in")
        mid = db.add_model("disposable", "ЖурналМодель", "BR", "", {}, ["Мята"])
        client.post("/api/admin/product/from-model", json={
            "initData": "x", "model_id": mid, "city": "Минск", "price": "30", "cost": "15",
            "variants": [{"flavor": "Мята", "stock": 4}]})
        строки = [r["details"] or "" for r in db.list_admin_log(limit=10)]
        правка = next((x for x in строки if "ЖурналПод" in x and "Цена" in x), "")
        c(f"цена с прежним значением: {правка}", "Цена 20.00 Br → 25.00 Br" in правка)
        c("хит назван словами", "отмечен «Хит»" in правка)
        c("неизменённая закупка не шумит", "Закупка" not in правка)
        c("приход назван с товаром и числом", any("ЖурналПод" in x and "Приход +3" in x for x in строки))
        c("завоз назван с первым приходом", any("ЖурналМодель" in x and "первый приход 4" in x for x in строки))
    finally:
        auth.get_admin = старый

    _clean()
    return c.fails


def run_гонка_заказов_и_склада():
    """Заказы, приходы и правка состава по одному товару — одновременно.

    Замки берутся в одном порядке (сначала вариант, потом товар), как у
    заказа. Возьми их где-то наоборот — и заказ с приходом, пришедшие в одну
    секунду, ждали бы друг друга вечно: на Postgres это «deadlock detected» и
    отказ одному из них. Здесь проверяем, что ни одна операция не упала, ни
    одна штука не потерялась и итог товара равен сумме вариантов."""
    import threading
    c = Checker("Гонка: заказы, приходы и состав одновременно")
    _clean()
    mid = db.add_model("disposable", "ГонкаМодель", "BR", "", {}, ["Мята", "Вишня"])
    pid = db.create_point_product(mid, "Минск", 10.0, 5.0,
                                  variants=[{"flavor": "Мята", "stock": 50}, {"flavor": "Вишня", "stock": 50}])
    беды = []

    def покупатель():
        for _ in range(10):
            try:
                _заказ(pid, 1, flavor="Мята", status="paid")
            except Exception as e:           # нехватки тут быть не может — любая беда настоящая
                беды.append(f"заказ: {e!r}")

    def приход():
        for _ in range(5):
            try:
                db.stock_operation(pid, "in", 1, flavor="Мята", admin_id=1)
            except Exception as e:
                беды.append(f"приход: {e!r}")

    def состав(кто):
        for i in range(6):
            try:
                db.change_variants(pid, add=[{"flavor": f"Лимон{кто}-{i}", "qty": 2}], admin_id=1)
                db.change_variants(pid, remove=[f"Лимон{кто}-{i}"], admin_id=1, writeoff=True)
            except Exception as e:
                беды.append(f"состав: {e!r}")

    # Плотность подобрана контролем: с замками в обратном порядке такая гонка
    # на Postgres давала сотни взаимных блокировок за полтора десятка прогонов.
    потоки = ([threading.Thread(target=покупатель) for _ in range(4)]
              + [threading.Thread(target=приход) for _ in range(2)]
              + [threading.Thread(target=состав, args=(к,)) for к in range(2)])
    for п in потоки:
        п.start()
    for п in потоки:
        п.join()

    вкусы = {v["flavor"]: v["stock"] for v in db.get_variants(pid)}
    c("ни одна операция не упала" + (f": {беды[:3]}" if беды else ""), not беды)
    c(f"Мята: 50 − 40 заказов + 10 приходов = 20 (вышло {вкусы.get('Мята')})", вкусы.get("Мята") == 20)
    c("Вишня не тронута", вкусы.get("Вишня") == 50)
    c("временные вкусы убраны", set(вкусы) == {"Мята", "Вишня"})
    c("итог товара = сумма вариантов", db.get_product(pid)["stock"] == sum(вкусы.values()))
    приходы = sum(m["delta"] for m in db.get_stock_moves(pid, limit=500)
                  if m["flavor"] == "Мята" and m["reason"] == "in")
    c(f"в истории Мяты: первый завоз 50 + 10 приходов (вышло {приходы})", приходы == 60)

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    fails = []
    for имя, f in list(globals().items()):
        if имя.startswith("run") and callable(f):
            fails += f() or []
    sys.exit(1 if fails else 0)
