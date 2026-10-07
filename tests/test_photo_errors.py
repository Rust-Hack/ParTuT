"""Почему Телеграм не принял фото — словами, по виду отказа (В-4, проход 6.10).

Фото товаров, моделей, призов и черновиков хранит Телеграм: сервер шлёт снимок
в чат человеку и берёт file_id. Раньше пять ручек на ЛЮБОЙ отказ отвечали
одно: «Телеграм не принял этот файл. Попробуйте другой снимок». И при обрыве
связи, и когда Телеграм просил подождать, и когда бот не мог написать продавцу
(тот ни разу не нажимал «Старт»). Другой снимок там не помогает — помогает
повтор или «Старт», а человек перебирал годные фотографии. Чек покупателя —
то же самое, только «попробуйте другой снимок чека».

Проверяется: каждый вид отказа узнаётся; каждая из шести загрузок отвечает
своим кодом, текстом и признаком «повтор поможет»; при отказе ничего не
записано; удачная загрузка по-прежнему работает.
"""
import io

import requests
from telebot import apihelper

from _common import db, client, Checker, as_admin, as_user
from partut.integrations import tgsend
from partut.web import photos
from partut import cache


def _телеграм(код, описание, **параметры):
    тело = {"ok": False, "error_code": код, "description": описание}
    if параметры:
        тело["parameters"] = параметры
    return apihelper.ApiTelegramException("sendPhoto", None, тело)


class _Ответ:
    """Ответ шлюза, а не Телеграма: не JSON (ApiHTTPException)."""

    def __init__(self, статус):
        self.status_code, self.reason, self.text = статус, "Bad Gateway", "<html>шлюз</html>"


def _файл(имя="p.jpg"):
    return (io.BytesIO(b"\xff\xd8\xffjpeg"), имя)


def run():
    c = Checker("В-4: вид отказа Телеграма")
    случаи = [
        ("403 «bot can't initiate conversation» — продавец не нажимал «Старт»",
         _телеграм(403, "Forbidden: bot can't initiate conversation with a user"), "bot_blocked"),
        ("403 «bot was blocked by the user»", _телеграм(403, "Forbidden: bot was blocked by the user"), "bot_blocked"),
        ("400 «chat not found» — писать некуда", _телеграм(400, "Bad Request: chat not found"), "bot_blocked"),
        ("429 «retry after 7»", _телеграм(429, "Too Many Requests: retry after 7", retry_after=7), "tg_busy"),
        ("413 «Request Entity Too Large»", _телеграм(413, "Request Entity Too Large"), "too_large"),
        ("400 «IMAGE_PROCESS_FAILED» — сам файл", _телеграм(400, "Bad Request: IMAGE_PROCESS_FAILED"), "bad_photo"),
        ("400 «PHOTO_INVALID_DIMENSIONS» — сам файл", _телеграм(400, "Bad Request: PHOTO_INVALID_DIMENSIONS"), "bad_photo"),
        ("502 от Телеграма", _телеграм(502, "Bad Gateway"), "tg_down"),
        ("502 от шлюза, не JSON", apihelper.ApiHTTPException("sendPhoto", _Ответ(502)), "tg_down"),
        ("413 от шлюза, не JSON", apihelper.ApiHTTPException("sendPhoto", _Ответ(413)), "too_large"),
        ("нет соединения", requests.exceptions.ConnectionError("Max retries exceeded"), "tg_unreachable"),
        ("истёк срок ответа", requests.exceptions.ReadTimeout("Read timed out"), "tg_unreachable"),
        ("непонятная ошибка", ValueError("что-то странное"), "send_failed"),
    ]
    for подпись, e, ждём in случаи:
        c(f"{подпись} → {ждём}", photos.вид_отказа(e)[0] == ждём)

    настоящий = tgsend.tg.send_photo
    отказ = {"e": None}

    def подменённый(*a, **k):
        if отказ["e"] is not None:
            raise отказ["e"]
        return настоящий(*a, **k)

    tgsend.tg.send_photo = подменённый
    try:
        as_admin()
        cache.bust()
        pid = db.add_product("ФотоОтказ", "accessories", "Кабель", 10.0, 5)
        mid = db.add_model("accessories", "Фото-модель В4")
        db.create_raffle(title="В-4")
        ручки = {
            "фото товара": lambda: client.post("/api/admin/photo", data={"initData": "x", "id": str(pid), "file": _файл()},
                                               content_type="multipart/form-data"),
            "фото модели": lambda: client.post("/api/admin/model/photo", data={"initData": "x", "model_id": str(mid), "file": _файл()},
                                               content_type="multipart/form-data"),
            "галерея": lambda: client.post("/api/admin/photo/add", data={"initData": "x", "model_id": str(mid), "file": _файл()},
                                           content_type="multipart/form-data"),
            "черновик нового товара": lambda: client.post("/api/admin/photo/draft", data={"initData": "x", "file": _файл()},
                                                          content_type="multipart/form-data"),
            "приз розыгрыша": lambda: client.post("/api/admin/raffle/photo", data={"initData": "x", "place": "1", "file": _файл()},
                                                  content_type="multipart/form-data"),
        }

        c2 = Checker("В-4: загрузки продавца и владельца называют причину")
        for имя, послать in ручки.items():
            отказ["e"] = requests.exceptions.ConnectionError("Max retries exceeded")
            r = послать(); d = r.get_json()
            c2(f"{имя}: обрыв связи — 503 tg_unreachable, «Нет связи с Телеграмом», повтор поможет, без «другой снимок»",
               r.status_code == 503 and d["error"] == "tg_unreachable" and d["retry"] is True
               and d["message"].startswith("Нет связи с Телеграмом") and "другой снимок" not in d["message"])
            отказ["e"] = _телеграм(403, "Forbidden: bot can't initiate conversation with a user")
            r = послать(); d = r.get_json()
            c2(f"{имя}: бот не может написать — 409 bot_blocked и «нажмите «Старт»», повтор сам по себе не поможет",
               r.status_code == 409 and d["error"] == "bot_blocked" and "«Старт»" in d["message"] and d["retry"] is False)
            отказ["e"] = _телеграм(400, "Bad Request: IMAGE_PROCESS_FAILED")
            r = послать(); d = r.get_json()
            c2(f"{имя}: сам снимок не подошёл — 400 bad_photo и «Выберите другое фото»",
               r.status_code == 400 and d["error"] == "bad_photo" and "Выберите другое фото" in d["message"])
        отказ["e"] = _телеграм(429, "Too Many Requests: retry after 7", retry_after=7)
        d = ручки["фото товара"]().get_json()
        c2(f"Телеграм просит подождать — пауза названа: «{d['message']}»", d["error"] == "tg_busy" and " 7 с" in d["message"])

        c3 = Checker("В-4: при отказе ничего не записано, удача — как раньше")
        c3("у товара фото не появилось", not db.get_product(pid)["photo"])
        c3("в галерее модели пусто", not db.model_photos(mid))
        c3("у приза фото не появилось", not (db.get_active_raffle() or {})["photo1"])
        отказ["e"] = None
        r = ручки["фото товара"]()
        c3(f"сеть вернулась — фото товара сохранено ({r.status_code})", r.status_code == 200 and db.get_product(pid)["photo"] == "fid")
        r = ручки["галерея"]()
        c3("и галерея принимает снимок", r.status_code == 200 and len(db.model_photos(mid)) == 1)

        c4 = Checker("В-4: чек покупателя — совет по причине")
        as_user(7701, "buyer")
        db.ensure_user(7701)
        oid = db.create_order(7701, "buyer", "ФотоОтказ", [{"id": pid, "name": "Кабель", "price": 10, "qty": 1}], 10, "")

        def чек():
            return client.post("/api/receipt", data={"initData": "x", "order_id": str(oid), "file": _файл("чек.jpg")},
                               content_type="multipart/form-data")
        отказ["e"] = requests.exceptions.ReadTimeout("Read timed out")
        r = чек(); d = r.get_json()
        c4(f"обрыв связи — «Чек не отправился — нет связи…», «Заказ ждёт чек», без «другой снимок» ({r.status_code})",
           r.status_code == 503 and d["error"] == "tg_unreachable" and "Заказ ждёт чек" in d["message"]
           and "другой снимок" not in d["message"])
        отказ["e"] = _телеграм(403, "Forbidden: bot was blocked by the user")
        d = чек().get_json()
        c4("бот заблокирован — «нажмите «Старт» и отправьте чек ещё раз»", d["error"] == "bot_blocked" and "«Старт»" in d["message"])
        отказ["e"] = _телеграм(400, "Bad Request: IMAGE_PROCESS_FAILED")
        d = чек().get_json()
        c4("сам файл не подошёл — «Попробуйте другой снимок чека»", d["error"] == "bad_photo" and "другой снимок чека" in d["message"])
        c4("заказ всё ещё ждёт чек", db.get_order(oid)["status"] == "new" and not db.get_order(oid)["receipt_file_id"])
        отказ["e"] = None
        r = чек()
        c4(f"сеть вернулась — чек принят ({r.status_code})", r.status_code == 200 and db.get_order(oid)["status"] == "paid")
    finally:
        tgsend.tg.send_photo = настоящий
        as_admin()
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("DELETE FROM orders WHERE city = %s"), ("ФотоОтказ",))
        cur.execute(db._q("DELETE FROM products WHERE city = %s"), ("ФотоОтказ",))
        cur.execute("DELETE FROM raffles")
        conn.commit(); conn.close()
        cache.bust()
    return c.fails + c2.fails + c3.fails + c4.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
