"""Движение склада: приход и списание с причиной и автором.

Раньше остаток менялся продажей и ручной правкой числа — на вопрос «куда
делось» ответа не было. Теперь у каждого изменения есть причина, автор и дата,
а списание считается деньгами по закупочной цене.
"""
from _common import db, client, Checker, as_admin, deny_admin

from partut import cache


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM stock_moves")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Приход и списание")
    _clean()
    as_admin(uid=555)

    pid = db.add_product("Минск", "pods", "СкладПод", 20.0, 10, cost=12.0)

    # --- Приход ---
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 5, "reason": "in"})
    d = r.get_json()
    c("приход записан", d.get("ok"))
    c("остаток вырос", d["stock"] == 15)

    # --- Приход по новой цене обновляет закупку ---
    client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 5,
                                               "reason": "in", "cost": "13,5"})
    c("новая закупка сохранена", abs(float(db.get_product(pid)["cost"]) - 13.5) < 0.01)
    c("остаток вырос ещё", db.get_product(pid)["stock"] == 20)

    # --- Списание ---
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 3,
                                                   "reason": "broken", "note": "разбили при перевозке"})
    d = r.get_json()
    c("списание записано", d.get("ok"))
    c("остаток уменьшился", d["stock"] == 17)

    # Знак задаёт ПРИЧИНА, а не клиент: «списание» с плюсом всё равно списывает.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 2, "reason": "lost"})
    c("недостача списывает, а не прибавляет", r.get_json()["stock"] == 15)

    # --- Журнал ---
    moves = client.post("/api/admin/stock/moves", json={"initData": "x", "id": pid}).get_json()["moves"]
    c("движения записаны", len(moves) == 4)
    c("автор сохранён", moves[0]["admin_id"] == 555)
    c("причина сохранена", moves[0]["reason"] == "lost")
    брак = next(m for m in moves if m["reason"] == "broken")
    c("примечание сохранено", брак["note"] == "разбили при перевозке")
    c("у списания знак минус", брак["delta"] == -3)

    # --- Потери в деньгах ---
    # Цену при списании никто не вводит — берётся закупочная на тот момент.
    c("списание оценено по закупке", abs(брак["cost"] - 13.5) < 0.01)
    потери = {l["reason"]: l for l in db.stock_losses()}
    c("брак посчитан деньгами", abs(потери["broken"]["money"] - 40.5) < 0.01)
    c("недостача посчитана", abs(потери["lost"]["money"] - 27.0) < 0.01)
    c("приход в потери не попал", "in" not in потери)

    # --- Мусор на входе ---
    c("выдуманная причина отклонена",
      client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 1, "reason": "кража"}).status_code == 400)
    c("ноль штук отклонён",
      client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 0, "reason": "in"}).status_code == 400)
    c("отрицательное количество отклонено",
      client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": -5, "reason": "in"}).status_code == 400)
    c("несуществующий товар отклонён",
      client.post("/api/admin/stock/move", json={"initData": "x", "id": 999999, "qty": 1, "reason": "in"}).status_code == 404)

    deny_admin()
    c("посторонний склад не трогает",
      client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 99, "reason": "in"}).status_code == 403)
    as_admin(uid=555)

    # --- Остаток не уходит в минус ---
    client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 999, "reason": "lost"})
    c("в минус не уходим", db.get_product(pid)["stock"] == 0)

    # --- Товар со вкусами: склад по каждому вкусу ---
    c2 = Checker("Склад по вкусам")
    vid = db.add_product("Минск", "disposable", "ВкусыПод", 25.0, 0, cost=15.0)
    db.add_variant(vid, "Мята", 5)
    db.add_variant(vid, "Вишня", 5)
    db.recalc_product_stock(vid)
    c2("общий остаток собран", db.get_product(vid)["stock"] == 10)

    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": vid, "qty": 2,
                                                   "reason": "broken", "flavor": "Мята"})
    c2("списали у нужного вкуса", r.get_json()["stock"] == 8)
    вкусы = {v["flavor"]: v["stock"] for v in db.get_variants(vid)}
    c2("Мята уменьшилась", вкусы["Мята"] == 3)
    c2("Вишня не тронута", вкусы["Вишня"] == 5)

    # --- Статистика показывает потери ---
    cache.bust()
    st = client.post("/api/admin/stats", json={"initData": "x", "period": "all"}).get_json()
    c2("потери попали в статистику", any(l["reason"] == "broken" for l in st["stats"]["losses"]))

    _clean()
    return c.fails + c2.fails


def run_recount():
    """Пересчёт: продавец присылает РЕЗУЛЬТАТ, разницу считает сервер.

    Раньше пересчёт работал как списание — просил разницу и умел только вниз.
    Насчитал больше, чем в базе, — поправить было нечем, кроме «Прихода», а тот
    переписывает закупочную цену и врёт про завоз, которого не было.
    """
    c = Checker("Пересчёт склада")
    _clean()
    as_admin(uid=556)

    pid = db.add_product("Минск", "pods", "ПересчётПод", 20.0, 10, cost=12.0)

    # Насчитали меньше, чем числится.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 8, "reason": "fix"})
    c("недостача записана", r.get_json().get("ok"))
    c("остаток стал ровно тем, что насчитали", r.get_json()["stock"] == 8)
    ход = db.get_stock_moves(pid, limit=1)[0]
    c("в журнале разница, а не итог", ход["delta"] == -2)

    # Насчитали БОЛЬШЕ — раньше это было невозможно.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 11, "reason": "fix"})
    c("излишек записан", r.get_json().get("ok"))
    c("остаток вырос до насчитанного", r.get_json()["stock"] == 11)
    c("разница положительная", db.get_stock_moves(pid, limit=1)[0]["delta"] == 3)
    c("закупочная цена не тронута", abs(float(db.get_product(pid)["cost"]) - 12.0) < 0.01)

    # Сошлось — записывать нечего.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 11, "reason": "fix"})
    c("совпадение не пишется в журнал", r.status_code == 400 and r.get_json()["error"] == "no_change")
    c("движений всё столько же", len(db.get_stock_moves(pid, limit=50)) == 2)

    # Ноль при пересчёте — законный результат: полка пуста.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 0, "reason": "fix"})
    c("ноль при пересчёте разрешён", r.get_json().get("ok") and r.get_json()["stock"] == 0)
    # А у прочих причин ноль по-прежнему промах, а не операция.
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": pid, "qty": 0, "reason": "broken"})
    c("ноль в списании отвергнут", r.status_code == 400)

    # У товара со вкусами считаем по выбранному вкусу, а не по товару целиком.
    vid = db.add_product("Минск", "disposable", "ПересчётВкус", 30.0, 0, cost=15.0)
    db.add_variant(vid, "Мята", 5)
    db.add_variant(vid, "Вишня", 4)
    r = client.post("/api/admin/stock/move", json={"initData": "x", "id": vid, "qty": 2,
                                                   "reason": "fix", "flavor": "Мята"})
    c("вкус пересчитан", r.get_json().get("ok"))
    вкусы = {v["flavor"]: v["stock"] for v in db.get_variants(vid)}
    c("у Мяты стало 2", вкусы["Мята"] == 2)
    c("Вишня не тронута", вкусы["Вишня"] == 4)
    c("общий остаток пересобран", db.get_product(vid)["stock"] == 6)

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
