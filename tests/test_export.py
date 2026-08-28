"""Выгрузка заказов в файл.

Главное здесь не «файл создался», а то, что файл и экран говорят ОДНО И ТО ЖЕ.
Выгрузка, которая считает по-своему, хуже её отсутствия: два разных ответа на
один вопрос, и владелец не знает, какому верить. Поэтому проверяем не формат
ради формата, а совпадение сумм со статистикой.

Второе по важности — русский Excel. Файл с точкой в дробной части он молча
читает как текст, столбец перестаёт складываться, и человек узнаёт об этом,
когда итог окажется нулём.
"""
import csv
import io

from _common import db, client, Checker, as_admin, ДОКУМЕНТЫ

from partut import cache
from partut.integrations import tgsend


BUYER = 9701


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()
    cache.bust()
    ДОКУМЕНТЫ.clear()


def _order(items, total, status="issued", coins=0, promo=0.0, fee=0.0):
    oid = db.create_order(BUYER, "buyer", "Минск", items, total, "")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET status = %s, coins_used = %s, promo_discount = %s, "
                      "delivery_fee = %s WHERE id = %s"), (status, coins, promo, fee, oid))
    conn.commit(); conn.close()
    cache.bust()
    return oid


def _таблица(текст):
    """Разбирает CSV обратно — так же, как это сделает Excel."""
    return list(csv.reader(io.StringIO(текст), delimiter=";"))


def _столбец(строки, шляпка, имя):
    i = шляпка.index(имя)
    return [r[i] for r in строки]


def _сумма(строки, шляпка, имя):
    """Сумма столбца ТАК, как её посчитает Excel: запятая — дробная часть."""
    out = 0.0
    for v in _столбец(строки, шляпка, имя):
        if v and v != "—":
            out += float(v.replace(",", "."))
    return round(out, 2)


def run():
    c = Checker("Выгрузка заказов в файл")
    _clean()
    as_admin()

    # Тот самый заказ из истории про прибыль: 25.77 по ценнику, закупка 22,
    # сотня монет скидкой. Прибыль обязана выйти 2.77, а не 14.
    _order([{"id": 1, "name": "Под", "flavor": "Арбуз", "price": 20.0, "cost": 18.0, "qty": 1},
            {"id": 2, "name": "Жижа", "flavor": None, "price": 5.77, "cost": 4.0, "qty": 1}],
           24.77, coins=100)

    r = client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    d = r.get_json() or {}
    c("выгрузка принята", d.get("ok") is True)
    c("сказано, сколько заказов", d.get("rows") == 1)
    c("имя файла с датой и расширением", str(d.get("file", "")).endswith(".csv"))

    tgsend.дождаться_фона(5)
    c("файл ушёл документом в чат", len(ДОКУМЕНТЫ) == 1)
    chat, имя, байты = ДОКУМЕНТЫ[0]
    c("документ адресован админу, а не покупателю", chat != BUYER)

    # --- Русский Excel ---
    c("файл начинается с BOM", байты[:3] == b"\xef\xbb\xbf")
    текст = байты.decode("utf-8-sig")
    c("разделитель — точка с запятой", ";" in текст.splitlines()[0])
    строки = _таблица(текст)
    шляпка = строки[0]
    тело = строки[1:]
    c("шляпка на месте", шляпка[0] == "Дата" and "Прибыль" in шляпка)
    c("дробная часть через запятую", any("," in v for v in _столбец(тело, шляпка, "Цена")))
    c("и НЕ через точку — иначе Excel считает это текстом",
      not any("." in v for v in _столбец(тело, шляпка, "Цена")))

    # --- Файл и экран считают одно и то же ---
    s = db.get_business_stats(None)
    c("в файле строка на каждую позицию", len(тело) == 2)
    c("выручка в файле = выручка на экране",
      abs(_сумма(тело, шляпка, "Выручка") - s["revenue"]) < 0.01)
    c("прибыль в файле = прибыль на экране",
      abs(_сумма(тело, шляпка, "Прибыль") - s["profit"]) < 0.01)
    c("прибыль посчитана от денег, а не от ценника (2.77, не 14)",
      abs(_сумма(тело, шляпка, "Прибыль") - 2.77) < 0.01)
    c("вариант товара попал в свой столбец", "Арбуз" in _столбец(тело, шляпка, "Вариант"))

    # --- Незаполненная закупка ---
    _clean()
    _order([{"id": 3, "name": "БезЗакупки", "price": 10.0, "qty": 1}], 10.0)
    client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    tgsend.дождаться_фона(5)
    строки = _таблица(ДОКУМЕНТЫ[-1][2].decode("utf-8-sig"))
    c2 = c
    c2("незаполненная закупка — прочерк, а не ноль",
       _столбец(строки[1:], строки[0], "Прибыль") == ["—"])
    c2("а нулевая прибыль не подмешалась в сумму", _сумма(строки[1:], строки[0], "Прибыль") == 0.0)

    # --- Доставка: без неё столбец не сойдётся с выручкой экрана ---
    _clean()
    _order([{"id": 4, "name": "Под", "price": 20.0, "cost": 10.0, "qty": 1}], 23.0, fee=3.0)
    client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    tgsend.дождаться_фона(5)
    строки = _таблица(ДОКУМЕНТЫ[-1][2].decode("utf-8-sig"))
    шляпка, тело = строки[0], строки[1:]
    c("доставка вынесена отдельной строкой", "Доставка" in _столбец(тело, шляпка, "Товар"))
    c("выручка с доставкой сходится с суммой заказа",
      abs(_сумма(тело, шляпка, "Выручка") - 23.0) < 0.01)

    # --- Отказы тоже в файле, со статусом ---
    _clean()
    _order([{"id": 5, "name": "Отказ", "price": 9.0, "qty": 1}], 9.0, status="canceled")
    client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    tgsend.дождаться_фона(5)
    строки = _таблица(ДОКУМЕНТЫ[-1][2].decode("utf-8-sig"))
    c("отклонённый заказ виден — ради этого в файл и лезут",
      _столбец(строки[1:], строки[0], "Статус") == ["Отклонён"])

    # --- Пустой период объясняется словами ---
    _clean()
    r = client.post("/api/admin/stats/export", json={"initData": "x", "period": "today"})
    c("пустая выгрузка — отказ, а не пустой файл", (r.get_json() or {}).get("ok") is False)
    c("и сказано человеческими словами", "нет" in ((r.get_json() or {}).get("message") or ""))
    c("файл при этом не отправлен", len(ДОКУМЕНТЫ) == 0)

    return c.fails


def run_точка_с_запятой_в_названии():
    """Название с разделителем внутри не должно разъезжать столбцы."""
    c = Checker("Выгрузка: разделитель внутри названия")
    _clean()
    as_admin()
    _order([{"id": 6, "name": "Под; со скидкой", "price": 10.0, "qty": 1}], 10.0)
    client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    tgsend.дождаться_фона(5)
    строки = _таблица(ДОКУМЕНТЫ[-1][2].decode("utf-8-sig"))
    c("столбцов столько же, сколько в шляпке", len(строки[1]) == len(строки[0]))
    c("название уехало целиком", "Под; со скидкой" in строки[1])
    return c.fails


def run_отказ_телеграма():
    """Telegram не принял документ — человек обязан узнать об этом.

    Это ровно тот случай, из-за которого экран настроек когда-то говорил
    «Сохранено ✅» при отказе сервера: фоновая отправка отвечает «готово»
    раньше, чем что-то доставлено, и человек ищет в чате файл, которого нет.
    """
    c = Checker("Выгрузка: Telegram отказал")
    _clean()
    as_admin()
    _order([{"id": 7, "name": "Под", "price": 10.0, "qty": 1}], 10.0)

    целый = tgsend.tg.send_document

    def падает(*a, **kw):
        raise RuntimeError("bot can't initiate conversation with a user")
    tgsend.tg.send_document = падает
    try:
        r = client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    finally:
        tgsend.tg.send_document = целый

    d = r.get_json() or {}
    c("ответ — не «готово»", d.get("ok") is False)
    c("код говорит о сбое доставки", r.status_code == 502)
    c("сказано, ЧТО делать, а не «попробуйте ещё раз»", "/start" in (d.get("message") or ""))
    c("файл действительно не ушёл", len(ДОКУМЕНТЫ) == 0)
    return c.fails


def run_только_владельцу():
    """Файл со всеми продажами магазина — не для продавца точки."""
    c = Checker("Выгрузка: права")
    _clean()
    # Продавец точки — админ с ролью seller. Именно роль решает, а не сам факт
    # админства: файл со всеми продажами всех точек ему не положен.
    as_admin(uid=101, username="seller", role="seller", city="Минск")
    r = client.post("/api/admin/stats/export", json={"initData": "x", "period": "all"})
    c("продавцу выгрузка закрыта", r.status_code == 403)
    c("и файл не готовился", len(ДОКУМЕНТЫ) == 0)
    return c.fails
