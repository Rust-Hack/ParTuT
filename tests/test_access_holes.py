"""Дыры, найденные при разборе прав. Каждая однажды была открыта.

Тест не про красивые сценарии, а про попытки пролезть мимо: без подписи,
с чужой подписью, старой подписью, через чат, через прямую ссылку на файл.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from _common import db, client, server, Checker, as_admin, REAL_GET_USER, real_auth

import config
import server_orders

BUYER = 7501
SELLER = 7502          # продавец Турова


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "models"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    server._cache_bust()


def _init_data(uid, when=None):
    """Настоящая подпись Telegram — как её присылает приложение."""
    pairs = {"user": json.dumps({"id": uid, "username": f"u{uid}"}),
             "auth_date": str(int(when if when is not None else time.time()))}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", server.BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def run():
    c = Checker("Подпись входа")
    _clean()
    real_auth()

    c("правильная подпись принимается", server.validate_init_data(_init_data(BUYER))["id"] == BUYER)
    c("подделанная — нет", server.validate_init_data(_init_data(BUYER)[:-4] + "0000") is None)
    c("пустая — нет", server.validate_init_data("") is None)
    c("без hash — нет", server.validate_init_data("user=%7B%22id%22%3A1%7D&auth_date=1") is None)
    # Подпись верна вечно, поэтому одна утёкшая строка работала бы всегда.
    old = time.time() - server.INIT_DATA_MAX_AGE - 60
    c("вчерашняя подпись не годится", server.validate_init_data(_init_data(BUYER, old)) is None)
    c("свежая — годится",
      server.validate_init_data(_init_data(BUYER, time.time() - 60))["id"] == BUYER)

    # --- Вход без Telegram ---
    c2 = Checker("DEV_MODE")
    # DEV_MODE подставляет владельца любому, кто открыл страницу. На боевой базе
    # это была бы админка без пароля для всего интернета.
    c2("на боевой базе выключен намертво",
      "not _IS_PRODUCTION" in open("server.py").read().split("DEV_MODE = ")[1][:60])
    c2("сейчас (тестовая база) он и так выключен", server.DEV_MODE is False)
    c2("без подписи пользователя нет", server.get_user("") is None)

    # --- Что видно на витрине ---
    c3 = Checker("Витрина без входа")
    pid = db.add_product("Минск", "disposable", "Elf Bar", 10.0, 5, cost=6.0)
    server._cache_bust()
    shop = client.get("/api/products").get_json()
    c3("товар виден всем — это витрина", any(p["id"] == pid for p in shop))
    c3("закупка не приходит", all("cost" not in p for p in shop))
    c3("сколько ждут — тоже наша кухня", all("waiting" not in p for p in shop))
    c3("админский список закрыт без подписи",
      client.post("/api/admin/products", json={"initData": ""}).status_code == 403)

    # --- Чек об оплате ---
    c4 = Checker("Чек по прямой ссылке")
    oid = db.create_order(BUYER, "buyer", "Минск",
                          [{"id": pid, "name": "Elf Bar", "price": 10.0, "qty": 1}], 10.0, "")
    db.set_order_receipt(oid, "receipt_file_777")
    c4("хозяин чека определяется", db.receipt_owner("receipt_file_777") == BUYER)
    c4("посторонний чек не получит", not server._may_see_photo("receipt_file_777"))
    c4("и по прямой ссылке тоже",
      client.get("/api/photo?file_id=receipt_file_777").status_code == 404)

    # Пропуск выдаётся вместе с заказом тому, кому заказ и так показывают.
    token = server.photo_token("receipt_file_777")
    c4("с пропуском чек открывается", server._may_see_photo("receipt_file_777", token))
    c4("чужой пропуск не подходит",
      not server._may_see_photo("receipt_file_777", server.photo_token("другой_файл")))
    c4("подделанный пропуск не подходит", not server._may_see_photo("receipt_file_777", token[:-3] + "000"))
    stale = f"{int(time.time()) - 10}.{'0' * 32}"
    c4("просроченный пропуск не подходит", not server._may_see_photo("receipt_file_777", stale))
    c4("в ссылке на чек нет строки входа",
      "initData" not in (server_orders._order_json(db.get_order(oid), "секрет")["receipt_url"] or ""))
    c4("зато есть пропуск", "&t=" in server_orders._order_json(db.get_order(oid))["receipt_url"])
    c4("картинка товара остаётся открытой всем",
      server._may_see_photo(db.get_product(pid)["photo"] or "нет-такого") is True
      or db.get_product(pid)["photo"] is None)

    # --- Бот не должен быть обходной дверью ---
    c5 = Checker("Границы точек в чате")
    import bot as botmod
    minsk = db.add_product("Минск", "disposable", "Минский", 10.0, 5)
    turov = db.add_product("Туров", "disposable", "Туровский", 10.0, 5)
    db.add_staff(SELLER, "Туров", "продавец точки")
    config.refresh_staff()
    c5("свой товар в чате доступен", botmod._my_product(SELLER, turov) is not None)
    c5("чужой — нет", botmod._my_product(SELLER, minsk) is None)
    c5("у владельца ограничений нет", botmod._my_product(next(iter(config.DEV_IDS)), minsk) is not None)

    # Правка из чата обязана попадать в журнал — иначе достаточно открыть бота,
    # чтобы менять цены без следа.
    conn = db.connect(); cur = conn.cursor(); cur.execute("DELETE FROM admin_log")
    conn.commit(); conn.close()
    botmod._log_bot(SELLER, "product/update", f"id={turov} · field=price · value=99")
    rows = db.list_admin_log(5)
    c5("действие из чата записано", any(r["action"] == "product/update" for r in rows))
    c5("и видно, что это чат", any("бот" in (r["admin_name"] or "") for r in rows))

    db.remove_staff(SELLER)
    config.refresh_staff()
    as_admin()
    server.get_user = REAL_GET_USER
    _clean()
    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
