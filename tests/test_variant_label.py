"""У каждой категории своё слово для варианта.

Механизм остатка один — метка и число штук, — а называется он по-разному.
Разбиралось по живым магазинам: позиции одной модели испарителя отличаются как
«0,17 Ом, упак. 3 шт», пода — расцветкой, одноразки — вкусом. Заводить картридж
со «вкусом 0.6 Ом» продавец не станет, и механизм останется неиспользованным.
"""
from _common import client, Checker, as_admin, as_user, deny_admin

from partut import cache


def _слово(code):
    return next(c for c in client.get("/api/categories").get_json() if c["code"] == code)["variant_label"]


def run():
    c = Checker("Слово для варианта")
    as_admin()
    cache.bust()

    # --- Умолчания стартовых категорий ---
    c("у одноразок вкус", _слово("disposable") == "Вкус")
    c("у жидкостей вкус", _слово("liquid") == "Вкус")
    c("у расходников сопротивление", _слово("coils") == "Сопротивление")
    c("у подсистем цвет", _слово("podsystem") == "Цвет")
    c("у устройств цвет", _слово("devices") == "Цвет")

    # --- Владелец меняет слово ---
    r = client.post("/api/admin/category/update",
                    json={"initData": "x", "code": "coils", "variant_label": "Ом"})
    cache.bust()
    c("слово меняется", r.get_json().get("ok") and _слово("coils") == "Ом")

    # Пустое слово не оставляет категорию без подписи.
    client.post("/api/admin/category/update", json={"initData": "x", "code": "coils", "variant_label": "   "})
    cache.bust()
    c("пустое слово заменяется вкусом", _слово("coils") == "Вкус")

    # Длинное обрезается, а не ломает разметку.
    client.post("/api/admin/category/update",
                json={"initData": "x", "code": "coils", "variant_label": "С" * 50})
    cache.bust()
    c("длинное обрезано", len(_слово("coils")) <= 20)
    client.post("/api/admin/category/update", json={"initData": "x", "code": "coils", "variant_label": "Сопротивление"})
    cache.bust()

    # --- Новая категория получает слово ---
    r = client.post("/api/admin/category", json={"initData": "x", "name": "Паучи", "emoji": "🟤"})
    код = r.get_json().get("code")
    cache.bust()
    c("новой категории слово задано", _слово(код) == "Вкус")

    # --- Слово видит и покупатель: витрина отдаёт его без входа ---
    as_user(7000); deny_admin()
    вид = client.get("/api/categories").get_json()
    c("покупателю слово тоже отдаётся", all("variant_label" in x for x in вид))

    as_admin()
    client.post("/api/admin/category/delete", json={"initData": "x", "code": код})
    cache.bust()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
