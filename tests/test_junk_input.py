"""Мусор в запросе не должен ронять сервер.

По всему серверу стояло `(data.get("x") or "").strip()`. Приложение шлёт строку,
и всё работало. Но стоило прислать список или словарь — обработчик падал с 500.
Одиннадцать адресов, три из них доступны обычному покупателю.

Само по себе это не кража и не порча данных. Плохо другое: каждое падение
отправляет разработчику письмо о сбое, то есть любой, кто знает адрес, может
завалить почту — и настоящая поломка утонет среди этого шума. А покупатель
вместо понятного отказа видит «Ошибка оформления».

Тест обстреливает ВСЕ маршруты сразу: список берётся из самого server.py, так
что новый адрес попадает под проверку сам, без правки теста.
"""
import io
import os
import re

from _common import db, client, server, Checker, as_admin, as_user

# Значения нарочно не тех типов, каких ждёт обработчик.
JUNK = [
    {},
    {"initData": "x"},
    {"initData": "x", "id": {"a": 1}, "user_id": [], "qty": {}, "price": [1], "value": {"x": 2},
     "delta": {}, "product_id": {"n": 1}, "order_id": [], "items": "строка", "city": [1, 2],
     "code": {"a": 1}, "text": ["л"], "rating": {}, "stock": [], "coins": {}, "spins": [],
     "period": [], "search": {}, "name": [], "point_id": {}, "phone": [], "note": {},
     "delivery_method_id": {}, "payment_method": [], "reason": {}, "action": [],
     "status": [], "field": {}, "model_id": [], "specs": "строка", "variants": "строка",
     "quantities": "строка", "flavors": "строка", "address": {}, "answer": [], "reply": {},
     "min_total": {}, "uses_left": [], "kind": {}, "emoji": [], "sort": {}, "decision": [],
     "active": {}, "hidden": [], "force": {}},
    {"initData": "x", "items": [1, "два", None, {"id": {}, "qty": []}]},
    {"initData": "x", "items": [{"id": 1, "qty": 1, "flavor": {"a": 1}}]},
]

GET_QUERIES = ("", "?city=%00", "?product_id=abc", "?category=%FF", "?id=[]", "?file_id=../../etc/passwd")


def _routes():
    """Адреса берём из исходника: так новый маршрут проверяется сам собой."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
    s = io.open(path, encoding="utf-8").read()
    found = re.findall(r'@app\.route\("([^"]+)"(?:,\s*methods=\[([^\]]+)\])?\)', s)
    posts = sorted({r for r, m in found if m and "POST" in m and "<" not in r})
    gets = sorted({r for r, m in found if (not m or "GET" in m) and "<" not in r})
    return posts, gets


def run():
    c = Checker("Мусор в запросе")
    posts, gets = _routes()
    c("маршруты найдены в исходнике", len(posts) > 50 and len(gets) > 5)

    # Стучимся владельцем: иначе почти всё упрётся в 403 и тело обработчика
    # никто не проверит — а падает как раз оно.
    as_admin()
    as_user(4242, "fuzz")
    db.set_age_ok(4242)

    fell = []
    for path in posts:
        for payload in JUNK:
            r = client.post(path, json=payload)
            if r.status_code >= 500:
                fell.append(f"POST {path}")
                break
    for path in gets:
        for q in GET_QUERIES:
            r = client.get(path + q)
            if r.status_code >= 500:
                fell.append(f"GET {path}{q}")
                break

    c(f"ни один из {len(posts)} POST не упал в 500", not [f for f in fell if f.startswith("POST")])
    c(f"ни один из {len(gets)} GET не упал в 500", not [f for f in fell if f.startswith("GET")])
    if fell:
        print("   упали:", ", ".join(fell[:12]))

    # --- Сам помощник, ради которого всё затевалось ---
    c2 = Checker("Приведение к строке")
    t = server._text
    c2("список — это не текст", t(["а"]) == "")
    c2("словарь — тоже", t({"a": 1}) == "")
    c2("None — пусто", t(None) == "")
    c2("число становится строкой", t(42) == "42")
    c2("пробелы по краям срезаются", t("  привет  ") == "привет")
    c2("длина ограничивается", t("абвгде", 3) == "абв")

    as_admin()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
