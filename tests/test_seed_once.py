"""Стартовые данные сеются ОДИН раз за жизнь базы, а не при каждом запуске.

Владелец удалял все категории или все точки, магазин перезапускался — и
удалённое возвращалось само. Причина была невидимой: init_db() выполняется при
каждом старте процесса (то есть при каждой выкатке и перезапуске хостинга), а
засев смотрел «пуста ли таблица». Пустая таблица — это не «новый магазин», это
может быть магазин, который осознанно у себя всё убрал.
"""
from _common import db, Checker

from partut import cache


def _выключить_отметку():
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (db.ЗАСЕВ,))
    conn.commit(); conn.close()


def _снести_всё():
    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products", "delivery_methods", "pickup_points",
              "category_specs", "categories", "locations"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Стартовые данные сеются один раз")

    # --- Обжитая база: отметки нет, но данные есть. Не сеем ничего. ---
    _выключить_отметку()
    было_категорий = len(db.category_codes())
    c("на стенде категории есть", было_категорий > 0)
    db._засеять_однажды()
    c("в обжитой базе засев не сработал", len(db.category_codes()) == было_категорий)
    c("но отметка проставлена", bool(db.get_setting(db.ЗАСЕВ)))

    # --- Владелец снёс всё и магазин перезапустился ---
    _снести_всё()
    db._засеять_однажды()          # это и есть «перезапуск»
    cache.bust()
    c("категории НЕ вернулись", not db.category_codes())
    c("точки НЕ вернулись", not db.get_locations())
    c("способы получения НЕ вернулись", not db.get_delivery_methods("Минск"))

    # Сколько бы раз ни перезапускали — результат тот же.
    for _ in range(3):
        db._засеять_однажды()
    cache.bust()
    c("и после трёх перезапусков пусто", not db.category_codes())

    # --- Новая база: отметки нет и данных нет. Сеем, но ровно один раз. ---
    _выключить_отметку()
    db._засеять_однажды()
    cache.bust()
    посеяно = len(db.category_codes())
    c("новая база получила стартовый набор", посеяно == len(db.CATEGORY_SEED))
    c("и точки", len(db.get_locations()) > 0)
    c("у жидкостей вкусы включены сразу", 
      next(x for x in db.list_categories() if x["code"] == "liquid")["has_flavors"] == 1)

    db._засеять_однажды()
    cache.bust()
    c("повторный запуск ничего не добавил", len(db.category_codes()) == посеяно)

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
