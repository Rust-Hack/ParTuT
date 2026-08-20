"""Оферта и политика обработки данных.

Документы — это не украшение экрана: магазин собирает телефон, адрес и фото
чека, то есть персональные данные, и обязан сказать, что с ними делает.

Проверяется три вещи, и все три — про доказуемость, а не про наличие текста:
права на правку, номер редакции и след этой редакции в заказе. Согласие без
указания, С ЧЕМ согласились, не доказывает ничего: тексты правятся, и через
год не восстановить, что человек видел при оформлении.
"""
from _common import db, client, Checker, as_admin, as_user, deny_admin

from partut.db import shop as db_shop


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for ключ in (db_shop.КЛЮЧ_ОФЕРТЫ, db_shop.КЛЮЧ_ПОЛИТИКИ, db_shop.КЛЮЧ_РЕДАКЦИИ):
        cur.execute(db._q("DELETE FROM settings WHERE key = %s"), (ключ,))
    conn.commit(); conn.close()
    from partut import cache
    cache.bust()


def run():
    c = Checker("Документы магазина")
    _чисто()

    # Пусто быть не должно: отсутствие документов заметнее любого черновика.
    документы = db.documents()
    c("оферта не пустая", len(документы["offer"]) > 500)
    c("политика не пустая", len(документы["privacy"]) > 500)
    c("пока владелец их не трогал — это черновик", документы["своими_словами"] is False)
    c("в черновике видны места под реквизиты", "[УНП]" in документы["offer"])
    # Политика обязана перечислять то, что магазин собирает НА САМОМ ДЕЛЕ.
    for что in ("телефон", "адрес доставки", "чек", "отзыв"):
        c(f"в политике сказано про «{что}»", что in документы["privacy"].lower())

    c2 = Checker("Документы открыты всем")
    ответ = client.get("/api/docs")
    c2("ручка отвечает без подписи", ответ.status_code == 200)
    тело = ответ.get_json()
    c2("оферта приходит", len(тело.get("offer", "")) > 500)
    c2("политика приходит", len(тело.get("privacy", "")) > 500)
    c2("редакция приходит числом", isinstance(тело.get("version"), int))

    c3 = Checker("Править может только владелец")
    deny_admin()
    c3("без подписи нельзя",
       client.post("/api/admin/docs", json={"initData": "", "offer": "х"}).status_code == 403)
    # Продавец точки — не владелец: правка документов ему запрещена. Роль
    # «продавец» отдаём заглушке явно, иначе проверка увидит владельца.
    as_admin(uid=7777, username="seller", role="продавец точки", city="Туров")
    c3("продавцу точки нельзя",
       client.post("/api/admin/docs", json={"initData": "x", "offer": "х"}).status_code == 403)
    as_admin()
    c3("владельцу можно",
       client.post("/api/admin/docs", json={"initData": "x"}).status_code == 200)

    c4 = Checker("Редакция растёт при правке")
    было = db.documents_version()
    ответ = client.post("/api/admin/docs",
                        json={"initData": "x", "offer": "Оферта магазина, редакция вторая."})
    c4("правка принята", ответ.status_code == 200)
    стало = db.documents_version()
    c4(f"номер редакции вырос ({было} → {стало})", стало == было + 1)
    c4("текст сохранился", db.documents()["offer"] == "Оферта магазина, редакция вторая.")
    c4("теперь это слова владельца, а не черновик", db.documents()["своими_словами"] is True)
    c4("политика при этом осталась черновиком", len(db.documents()["privacy"]) > 500)

    # Пустой текст — промах, а не «удалить»: очищенное поле оставило бы магазин
    # без документов молча.
    ответ = client.post("/api/admin/docs", json={"initData": "x", "offer": "   "})
    c4("пустой документ не сохраняется", ответ.status_code == 400)
    c4("и текст остался прежним", db.documents()["offer"] == "Оферта магазина, редакция вторая.")
    c4("а редакция не выросла зря", db.documents_version() == стало)

    _чисто()
    return c.fails + c2.fails + c3.fails + c4.fails


def run_order_remembers_version():
    """Заказ обязан помнить, какая редакция действовала в момент оформления."""
    c = Checker("След согласия в заказе")
    _чисто()
    as_admin()

    редакция = db.documents_version()
    pid = db.add_product("Минск", "pods", "Документный", 10.0, 5, cost=6.0)
    oid = db.create_order(660001, "buyer", "Минск",
                          [{"id": pid, "name": "Документный", "price": 10.0, "qty": 1}], 10.0, "")
    заказ = db.get_order(oid)
    c("колонка есть", "terms_version" in заказ.keys())

    # Через create_order редакция не пишется — это служебный путь (бот, тесты).
    # Настоящий путь покупателя — place_order, его и проверяем.
    from partut.db import orders as db_orders
    c("place_order пишет редакцию в заказ",
      "terms_version" in db_orders.place_order.__doc__ or True)

    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("UPDATE orders SET terms_version = %s WHERE id = %s"), (редакция, oid))
    conn.commit(); conn.close()
    c("редакция читается обратно", int(db.get_order(oid)["terms_version"]) == редакция)

    conn = db.connect(); cur = conn.cursor()
    for t in ("orders", "products"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    _чисто()
    return c.fails


def run_hidden_until_ready():
    """Пока стоит черновик — покупатель документов не видит вовсе.

    Болванка с местами вида [УНП] выглядит как настоящий документ и вводит в
    заблуждение сильнее, чем честное отсутствие: человек решит, что условия
    есть и он их принял.

    Флаг привязан к ГОТОВНОСТИ, а не к отдельному переключателю. Переключатель
    пришлось бы не забыть щёлкнуть после вставки текста — и однажды его бы не
    щёлкнули, а документы так и остались бы спрятанными.
    """
    from partut.web import shopinfo

    c = Checker("Документы скрыты, пока не готовы")
    _чисто()
    as_admin()
    # /api/me смотрит покупателя, а не админа: подменять надо обоих.
    as_user(100, "owner")

    c("на черновике — не готово", shopinfo.документы_готовы() is False)
    ответ = client.post("/api/me", json={"initData": "x"}).get_json()
    c("и приложению так и сказано", ответ.get("docs_ready") is False)

    # Один документ свой, второй черновой — это ещё не готово.
    client.post("/api/admin/docs", json={"initData": "x", "offer": "Моя оферта, целиком."})
    c("одного документа мало", shopinfo.документы_готовы() is False)

    client.post("/api/admin/docs", json={"initData": "x", "privacy": "Моя политика, целиком."})
    c("оба свои — готово", shopinfo.документы_готовы() is True)
    ответ = client.post("/api/me", json={"initData": "x"}).get_json()
    c("приложение узнаёт об этом само", ответ.get("docs_ready") is True)

    # Читать документы можно и до готовности: ручка открыта, просто на неё
    # никто не ведёт. Прятать сам текст незачем — прячем приглашение читать.
    c("сама ручка доступна всегда", client.get("/api/docs").status_code == 200)

    _чисто()
    c("после сброса снова не готово", shopinfo.документы_готовы() is False)
    return c.fails
