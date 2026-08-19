"""Что копится в базе само по себе.

База бесплатная и небольшая, а место на ней кончается тихо: заметить это можно
было бы только когда магазин перестанет принимать заказы. Копится две вещи.

Картинки товаров лежат у нас, чтобы не качать их из Telegram при каждом показе.
Товар снимают с точки — строки о нём уходят, а картинка оставалась навсегда.
Ошибиться при уборке почти нечем: file_id в Telegram остаётся рабочим, и
удалённая по недосмотру картинка скачается заново. Но живую трогать всё равно
нельзя — иначе каждый показ каталога снова идёт в Telegram.

Летопись монет нужна для отчёта «роздано за период», а не навсегда.
"""
import datetime

from _common import db, Checker

from partut.bot import handlers as botmod

LIVE = ("живое_фото", "живая_мелкая", "фото_галереи", "мелкая_галереи")


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("photo_blobs", "product_photos", "products", "coin_log"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()


def _age_photos(days):
    when = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE photo_blobs SET created_at = %s"), (when,))
    conn.commit(); conn.close()


def _blobs():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT file_id FROM photo_blobs")
    out = {r["file_id"] for r in cur.fetchall()}
    conn.close()
    return out


def run():
    c = Checker("Уборка осиротевших картинок")
    _clean()

    pid = db.add_product("Минск", "pods", "Живой", 10.0, 1)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE products SET photo = %s, photo_thumb = %s WHERE id = %s"),
                ("живое_фото", "живая_мелкая", pid))
    conn.commit(); conn.close()
    db.add_product_photo(pid, "фото_галереи", "мелкая_галереи")

    for fid in LIVE + ("сирота_1", "сирота_2"):
        db.save_photo_blob(fid, "image/jpeg", b"x" * 100)
    _age_photos(3)

    gone = db.purge_orphan_photos()
    left = _blobs()
    c("сироты убраны", gone == 2 and not any(f.startswith("сирота") for f in left))
    c("картинка товара цела", "живое_фото" in left and "живая_мелкая" in left)
    c("картинки галереи целы", "фото_галереи" in left and "мелкая_галереи" in left)
    c("повторная уборка убирать больше нечего", db.purge_orphan_photos() == 0)

    # Картинка появляется в базе следом за товаром, а не одновременно с ним:
    # уборка не должна успеть влезть между этими двумя действиями.
    db.save_photo_blob("свежая_сирота", "image/jpeg", b"y" * 10)
    db.purge_orphan_photos()
    c("картинку моложе суток не трогаем", "свежая_сирота" in _blobs())

    # Снятый с точки товар уносит свои картинки — но только свои.
    _age_photos(3)
    db.delete_product(pid)
    db.purge_orphan_photos()
    c("после снятия товара его картинки убраны", not (set(LIVE) & _blobs()))

    c2 = Checker("Летопись монет не растёт без предела")
    _clean()
    db.log_coins(7001, 5, "cashback")
    conn = db.connect(); cur = conn.cursor()
    long_ago = (datetime.datetime.now() - datetime.timedelta(days=500)).strftime("%Y-%m-%d %H:%M")
    cur.execute(db._q("UPDATE coin_log SET created_at = %s"), (long_ago,))
    conn.commit(); conn.close()
    db.log_coins(7002, 3, "wheel")

    c2("старое движение убрано", db.trim_coin_log() == 1)
    flow = db.coin_flow(30)
    c2("свежее движение осталось", flow["granted"] == 3)
    c2("срок хранения переживает сравнение год к году", db.COIN_LOG_KEEP_DAYS > 365)

    # --- Уборка делается сама, раз в сутки ---
    c3 = Checker("Уборка в ночных делах")
    c3("шаг уборки стоит в фоновых делах",
       any(name == "ночная уборка" for name, _ in botmod._BACKGROUND_STEPS))
    db.set_setting(botmod._CLEANUP_MARK, "")
    real_hour, botmod.BACKUP_HOUR = botmod.BACKUP_HOUR, 0
    try:
        db.save_photo_blob("сирота_ночная", "image/jpeg", b"z" * 10)
        _age_photos(3)
        botmod._nightly_cleanup()
        c3("ночью сироты убираются сами", "сирота_ночная" not in _blobs())
        db.save_photo_blob("сирота_вторая", "image/jpeg", b"z" * 10)
        _age_photos(3)
        botmod._nightly_cleanup()
        c3("дважды за сутки базу не тревожим", "сирота_вторая" in _blobs())
    finally:
        botmod.BACKUP_HOUR = real_hour
        db.set_setting(botmod._CLEANUP_MARK, "")
        _clean()

    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
