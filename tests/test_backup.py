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
    from partut.bot import handlers as botmod

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


def run_empty_restore():
    """Копию разворачивают в ПУСТУЮ базу — там, где магазина ещё нет.

    Обычная проверка восстановления заливает копию туда, где таблицы уже есть.
    Настоящая беда выглядит иначе: базы нет вовсе, и всё — схему и данные —
    надо поднять с нуля. Именно этот случай и наступает в тот единственный день,
    ради которого копии и снимают.

    Проверено и вживую, на настоящей копии из бота, залитой в отдельную пустую
    базу Postgres (порядок — в tests/README.md). Здесь то же самое, но само и
    на каждый пуск.
    """
    import tempfile

    c = Checker("Восстановление в пустую базу")
    данные = db.export_tables()
    было = {t: len(rows) for t, rows in данные.items()}
    c("копия непустая", sum(было.values()) > 0)

    if db.USE_PG:
        # Второй пустой базы под рукой нет — на Postgres этот случай проверяется
        # вживую, руками, по порядку из README. Молчать об этом нельзя, иначе
        # пропуск выглядел бы как успех.
        c("на Postgres проверяется вживую — здесь пропущено", True)
        return c.fails

    прежний = db.SQLITE_FILE
    db.SQLITE_FILE = tempfile.mktemp(suffix=".db")
    try:
        db.init_db()                       # схема с нуля, как при первом запуске
        conn = db.connect(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM products")
        c("новая база и правда пуста", int(cur.fetchone()["n"]) == 0)
        conn.close()

        db.import_tables(данные, wipe=True)
        conn = db.connect(); cur = conn.cursor()
        расхождения = []
        for таблица, сколько in было.items():
            try:
                cur.execute(f"SELECT COUNT(*) AS n FROM {таблица}")
                стало = int(cur.fetchone()["n"])
            except Exception as e:
                расхождения.append(f"{таблица}: не читается ({e})")
                continue
            if стало != сколько:
                расхождения.append(f"{таблица}: было {сколько}, стало {стало}")
        conn.close()
        c("всё содержимое доехало" + ("" if not расхождения else ": " + "; ".join(расхождения[:3])),
          not расхождения)

        # И главное: магазин на этой базе отвечает.
        from _common import client
        c("магазин на восстановленной базе работает",
          client.get("/api/products").status_code == 200)
    finally:
        import os
        try:
            os.unlink(db.SQLITE_FILE)
        except OSError:
            pass
        db.SQLITE_FILE = прежний
    return c.fails


def run_handler_order():
    """Команды обязаны быть объявлены выше общего обработчика.

    Телебот перебирает обработчики в порядке объявления, а on_text ловит ЛЮБОЕ
    сообщение. /backup был объявлен ниже — и владелец на свою команду получал
    «магазин открывается по кнопке ниже», думая, что копии сломаны. Ошибка не
    видна ни в коде команды, ни в логах: она в порядке строк.
    """
    c = Checker("Порядок обработчиков бота")
    from partut.bot import handlers as botmod

    handlers = botmod.bot.message_handlers
    def имя(h):
        return getattr(h.get("function"), "__name__", "?")

    # Общий — тот, что ловит текст без списка команд (у него стоит func-фильтр).
    catch_all = next((i for i, h in enumerate(handlers)
                      if "text" in ((h.get("filters") or {}).get("content_types") or [])
                      and not (h.get("filters") or {}).get("commands")), None)
    c("общий обработчик найден", catch_all is not None)

    commands = {}
    for i, h in enumerate(handlers):
        for cmd in ((h.get("filters") or {}).get("commands") or []):
            commands.setdefault(cmd, i)
    c("команды вообще есть", len(commands) >= 5)

    поздние = [cmd for cmd, i in commands.items() if catch_all is not None and i > catch_all]
    c(f"ни одна команда не объявлена ниже общего обработчика (иначе она не работает): {поздние}",
      not поздние)
    c("/backup среди команд", "backup" in commands)
    c("и он выше общего обработчика", commands.get("backup", 99) < (catch_all if catch_all is not None else 0))
    return c.fails


def run_daily_once():
    """Копия уходит раз в сутки, а не после каждого перезапуска.

    Дата последней копии хранилась в памяти процесса. На Render сервис
    поднимается заново при каждом деплое — и владельцу падало по три копии
    подряд, а «раз в сутки» держалось только пока процесс жив.
    """
    import datetime
    from partut.bot import handlers as botmod

    c = Checker("Копия раз в сутки")
    db.set_setting(botmod._BACKUP_MARK, "")
    отправлено = []
    orig = botmod._send_backup
    botmod._send_backup = lambda ids, note="": отправлено.append(note) or None
    # Час заведомо после BACKUP_HOUR — иначе проверка зависела бы от времени прогона.
    orig_hour = botmod.BACKUP_HOUR
    botmod.BACKUP_HOUR = 0
    try:
        botmod._maybe_send_backup()
        c("первая копия ушла", len(отправлено) == 1)
        botmod._maybe_send_backup()
        c("вторая в тот же день — нет", len(отправлено) == 1)
        # Перезапуск сервиса: память чиста, но отметка осталась в базе.
        botmod._maybe_send_backup()
        c("и после перезапуска тоже нет", len(отправлено) == 1)
        c("отметка в базе — сегодняшняя дата",
          db.get_setting(botmod._BACKUP_MARK) ==
          (datetime.datetime.utcnow() + datetime.timedelta(hours=botmod.SUMMARY_TZ_OFFSET)).date().isoformat())
        # Новый день — копия снова уходит.
        db.set_setting(botmod._BACKUP_MARK, "2020-01-01")
        botmod._maybe_send_backup()
        c("назавтра копия уходит снова", len(отправлено) == 2)
    finally:
        botmod._send_backup = orig
        botmod.BACKUP_HOUR = orig_hour
        db.set_setting(botmod._BACKUP_MARK, "")
    return c.fails


def run_restore_keeps_numbering():
    """После восстановления магазин обязан принимать НОВЫЕ заказы.

    Копия привозит строки, но не счётчики id. На SQLite это незаметно —
    следующий номер он берёт от самих строк. У Postgres счётчик отдельный, в
    новой базе стоит на единице, и копия его не двигает: заказ №1 в базе уже
    есть, а счётчик собирается выдать именно единицу.

    Беда идеально спрятана: восстановление проходит, все данные на месте, отчёт
    зелёный — и магазин не может принять НИ ОДНОГО заказа. Найдено живьём на
    Postgres: 40 восстановленных заказов, первый же новый — duplicate key.

    Здесь состояние новой базы воспроизводится честно: счётчик сбрасывается на
    единицу тем же способом, каким он там и оказывается, — обычным SQL, а не
    тем кодом, который проверяем.
    """
    c = Checker("Нумерация после восстановления")

    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()

    pid = db.add_product("Минск", "pods", "Нумерация", 10.0, 100, cost=6.0)
    номера = [db.create_order(500 + i, "buyer", "Минск",
                              [{"id": pid, "name": "Нумерация", "price": 10.0, "qty": 1}],
                              10.0, "") for i in range(3)]
    копия = db.export_tables()
    c("заказы в копии", len(копия["orders"]) == 3)

    # --- Базы больше нет: пустые таблицы И счётчики с нуля ---
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
        if db.USE_PG:
            cur.execute(f"ALTER SEQUENCE {t}_id_seq RESTART WITH 1")
        else:
            cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))
    conn.commit(); conn.close()

    db.import_tables(копия, wipe=True)
    c("заказы вернулись", all(db.get_order(n) is not None for n in номера))

    # --- Первый день после восстановления ---
    беда = None
    новый = None
    try:
        новый = db.create_order(999, "buyer", "Минск",
                                [{"id": pid, "name": "Нумерация", "price": 10.0, "qty": 1}],
                                10.0, "")
    except Exception as e:
        беда = f"{type(e).__name__}: {str(e).strip().splitlines()[0][:80]}"
    c(f"новый заказ принимается{'' if not беда else ' (упало: ' + беда + ')'}", беда is None)
    c(f"и получает свободный номер (после {max(номера)})",
      новый is not None and новый > max(номера))
    c("старые заказы при этом целы", all(db.get_order(n) is not None for n in номера))

    # Товары — та же беда, только тише: новый товар молча занял бы чужой id.
    # Ловим и здесь: непойманное исключение оборвало бы уборку в конце, и
    # следующие тесты получили бы чужой мусор в таблицах.
    try:
        товар = db.add_product("Минск", "pods", "Новый после беды", 12.0, 3, cost=7.0)
    except Exception as e:
        товар, беда2 = None, f"{type(e).__name__}"
        c(f"новый товар заводится (упало: {беда2})", False)
    c("новый товар не наступает на старый", товар is not None and товар > pid)

    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    return c.fails


def run_sequences_only_forward():
    """Счётчик id двигаем только вперёд — назад нельзя ни при каких условиях.

    Соблазн поставить счётчик ровно на max(id)+1 понятен и опасен. Заказы
    удаляют (отменённые, тестовые), и тогда счётчик стоит ДАЛЬШЕ максимума.
    Откат назад заставил бы новый заказ занять номер удалённого — а на номера
    заказов ссылаются начисления монет, журнал действий и переписка с
    покупателем. Новый заказ молча получил бы чужую историю: чужие монеты,
    чужие сообщения. Это хуже, чем падение, — падение хотя бы видно.
    """
    c = Checker("Счётчики id — только вперёд")
    if not db.USE_PG:
        # У SQLite отдельного счётчика нет, двигать нечего.
        c("на SQLite счётчиков нет — проверять нечего", db.advance_sequences() == [])
        return c.fails

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    conn.commit(); conn.close()

    pid = db.add_product("Минск", "pods", "Вперёд", 10.0, 100, cost=6.0)
    товар = {"id": pid, "name": "Вперёд", "price": 10.0, "qty": 1}
    номера = [db.create_order(700 + i, "buyer", "Минск", [товар], 10.0, "") for i in range(4)]

    # Последний заказ удалили — счётчик остался за максимумом.
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM orders WHERE id = %s"), (номера[-1],))
    conn.commit(); conn.close()

    db.advance_sequences()

    следующий = db.create_order(999, "buyer", "Минск", [товар], 10.0, "")
    c(f"номер удалённого заказа ({номера[-1]}) не выдан заново", следующий != номера[-1])
    c("нумерация пошла дальше", следующий > номера[-1])

    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    return c.fails
