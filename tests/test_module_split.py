"""Разрезание db.py на модули не должно ничего менять снаружи.

db.py вырос до 4787 строк, и его режут по кускам. Правило переезда одно: для
всего остального магазина ничего не меняется — по-прежнему `db.что_угодно()`.

Отдельно проверяется то, на чём держится весь тестовый стенд: примитивы внутри
вынесенных модулей берутся ЧЕРЕЗ db (db.connect(), db._q()), а не копиями имён.
Скопируй имя при импорте — и подмена db.connect перестанет доходить: тесты
молча уйдут работать с боевой базой вместо временной, и узнать об этом можно
было бы только по испорченным данным.
"""
from _common import db, Checker

from partut.db import games as db_games
from partut.db import photos as db_photos
from partut.db import promos as db_promos
from partut.db import raffles as db_raffles
from partut.db import reviews as db_reviews
from partut.db import stock as db_stock

# Модули, вынесенные из db.py, и что именно из них переехало.
ВЫНЕСЕНО = [
    (db_raffles, ["get_active_raffle", "create_raffle", "add_raffle_entry",
                  "get_raffle_state", "claim_raffle_draw", "spent_since"]),
    (db_games, ["get_wheel", "add_spins", "do_wheel_spin", "do_slot_spin",
                "wheel_step", "get_game_stats"]),
    (db_photos, ["get_photo_blob", "save_photo_blob", "is_shop_photo",
                 "purge_orphan_photos", "add_product_photo", "photo_blob_stats"]),
    (db_reviews, ["add_review", "list_reviews", "set_review_status", "get_review",
                  "count_pending_reviews", "reviewable_products"]),
    (db_promos, ["check_promo", "add_promo", "list_promos", "delete_promo",
                 "set_promo_active", "consume_promo"]),
    (db_stock, ["move_stock", "get_stock_moves", "stock_losses", "add_stock_alert",
                "clear_stock_alerts", "stock_alert_counts"]),
]

# Примитивы, которые вынесенные модули обязаны брать через db.
ПРИМИТИВЫ = ["connect", "_q", "_insert_id", "_now_str", "shop_now", "_table_columns"]


def run():
    c = Checker("Переезд кусков db.py")
    for модуль, имена in ВЫНЕСЕНО:
        короткое = модуль.__name__
        for имя in имена:
            c(f"{короткое}.{имя} доступен и через db",
              getattr(db, имя, None) is getattr(модуль, имя))

    # Модуль не должен делать копий примитивов у себя: копия не заметит подмены.
    c2 = Checker("Примитивы берутся через db, а не копией")
    for модуль, _ in ВЫНЕСЕНО:
        свои = [и for и in ПРИМИТИВЫ if и in vars(модуль)]
        c2(f"{модуль.__name__} не держит своих копий"
           + ("" if not свои else f": {свои}"), not свои)

    # И проверка делом: подменяем db.connect и смотрим, дошло ли.
    c3 = Checker("Подмена доходит до вынесенного кода")
    считано = {"n": 0}
    настоящий = db.connect

    def счётчик(*a, **k):
        считано["n"] += 1
        return настоящий(*a, **k)

    db.connect = счётчик
    try:
        db.get_active_raffle()          # живёт в db_raffles
        db.get_game_stats()             # живёт в db_games
        db.photo_blob_stats()           # живёт в db_photos
        db.list_promos()                # живёт в db_promos
        db.stock_alert_counts()         # живёт в db_stock
    finally:
        db.connect = настоящий
    c3("вынесенный модуль сходил через подменённый db.connect", считано["n"] > 0)

    return c.fails + c2.fails + c3.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
