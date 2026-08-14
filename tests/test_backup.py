"""Резервная копия: снять, потерять базу, восстановить.

Копия имеет смысл только если по ней действительно можно поднять магазин,
поэтому тест не просто смотрит на файл, а стирает данные и заливает их обратно.
"""
import gzip
import json

from _common import db, Checker


def run():
    c = Checker("Резервная копия и восстановление")

    # Готовим узнаваемое состояние магазина.
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "stock_alerts", "staff"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()

    pid = db.add_product("Минск", "pods", "БэкапПод", 21.5, 4)
    db.set_setting("payment_info", "Карта 1111 2222")
    db.add_staff(777123, "Минск", "@seller", 1)
    db.add_stock_alert(pid, 660777)
    oid = db.create_order(555999, "buyer", "Минск",
                          [{"product_id": pid, "name": "БэкапПод", "price": 21.5, "qty": 2}], 43.0, "")

    data = db.export_tables()
    c("товары попали в копию", any(r["name"] == "БэкапПод" for r in data["products"]))
    c("заказы попали в копию", any(int(r["id"]) == oid for r in data["orders"]))
    c("настройки попали в копию", any(r["key"] == "payment_info" for r in data["settings"]))
    c("доступы попали в копию", any(int(r["user_id"]) == 777123 for r in data["staff"]))
    c("подписки попали в копию", len(data["stock_alerts"]) == 1)

    # Картинки — кэш, их можно скачать из Telegram заново; в копии им не место.
    c("картинки в копию НЕ кладём", "photo_blobs" not in data)

    # Таблицы берутся из базы, а не списком: новая таблица попадёт в копию сама.
    conn = db.connect(); cur = conn.cursor()
    live = {t for t in db._all_table_names(cur) if t not in db.BACKUP_SKIP}
    conn.close()
    c("в копии ВСЕ таблицы базы", live == set(data.keys()))

    # Копия должна пережить сжатие и запись в файл — так её и присылает бот.
    blob = gzip.compress(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
    restored = json.loads(gzip.decompress(blob).decode("utf-8"))
    c("после сжатия читается", restored["products"][0]["name"] == "БэкапПод")

    # --- Теряем базу ---
    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "settings", "staff", "stock_alerts"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    c("данные стёрты", not db.get_all_products())

    # --- Восстанавливаем ---
    db.import_tables(restored, wipe=True)
    prods = db.get_all_products()
    c("товар вернулся", any(p["name"] == "БэкапПод" for p in prods))
    c("остаток тот же", any(p["stock"] == 4 for p in prods))
    c("цена та же", any(abs(p["price"] - 21.5) < 0.001 for p in prods))
    c("заказ вернулся", db.get_order(oid) is not None)
    c("сумма заказа цела", db.get_order(oid) and abs(db.get_order(oid)["total"] - 43.0) < 0.001)
    c("реквизиты вернулись", db.get_setting("payment_info") == "Карта 1111 2222")
    c("доступ продавца вернулся", any(int(r["user_id"]) == 777123 for r in db.list_staff()))

    # Повторная заливка не должна плодить дубли.
    db.import_tables(restored, wipe=True)
    c("повторное восстановление не двоит", len(db.get_all_products()) == len(prods))

    # Копия могла быть снята до обновления кода — лишние поля и таблицы не должны ронять восстановление.
    hurt = json.loads(json.dumps(restored))
    hurt["таблица_из_будущего"] = [{"a": 1}]
    hurt["products"][0]["колонка_из_будущего"] = "мусор"
    try:
        rep = db.import_tables(hurt, wipe=True)
        c("старая копия заливается без падения", True)
        c("исчезнувшая таблица пропущена", "пропущено" in rep.get("таблица_из_будущего", ""))
        c("товар всё равно восстановлен", any(p["name"] == "БэкапПод" for p in db.get_all_products()))
    except Exception as e:
        c(f"старая копия заливается без падения (упало: {e})", False)

    # --- Отправка файла ботом ---
    # Самая хрупкая часть: собрать архив и отдать его в Telegram. Проверяем без
    # сети — подменяем отправку и смотрим, что уходит.
    c2 = Checker("Отправка копии в Telegram")
    import bot as botmod

    sent = []
    real_send = botmod.bot.send_document
    botmod.bot.send_document = lambda chat_id, doc, **kw: sent.append((chat_id, doc, kw))
    try:
        err = botmod._send_backup([12345])
        c2("отправка прошла без ошибки", err is None)
        c2("файл ушёл одному адресату", len(sent) == 1)
        chat_id, doc, kw = sent[0]
        c2("в нужный чат", chat_id == 12345)
        c2("это архив gzip", isinstance(doc, bytes) and doc[:2] == b"\x1f\x8b")
        c2("у файла говорящее имя", kw.get("visible_file_name", "").startswith("partut-")
                                    and kw["visible_file_name"].endswith(".json.gz"))
        c2("в подписи есть размер", "Размер:" in kw.get("caption", ""))

        import gzip as gz, json as js
        inside = js.loads(gz.decompress(doc).decode("utf-8"))
        c2("внутри архива — данные магазина", "products" in inside and "orders" in inside)

        # Если Telegram недоступен, ежедневная задача не должна падать молча.
        sent.clear()
        botmod.bot.send_document = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети"))
        err = botmod._send_backup([12345])
        c2("сбой отправки сообщается наверх", bool(err))
    finally:
        botmod.bot.send_document = real_send

    conn = db.connect(); cur = conn.cursor()
    for t in ("products", "orders", "stock_alerts", "staff"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
