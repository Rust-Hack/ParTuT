"""Сжатие: большие текстовые ответы отдаются gzip при Accept-Encoding: gzip."""
import gzip
from _common import client, Checker


def run():
    c = Checker("Gzip-сжатие ответов")

    # index.html большой → должен сжиматься при поддержке клиентом
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    c("index: 200", r.status_code == 200)
    c("index: Content-Encoding gzip", r.headers.get("Content-Encoding") == "gzip")
    body = gzip.decompress(r.get_data())
    c("index: распаковывается в HTML", b"<" in body and len(body) > 1024)

    # без заголовка — без сжатия (совместимость со старыми клиентами)
    r2 = client.get("/")
    c("без Accept-Encoding: не сжато", r2.headers.get("Content-Encoding") != "gzip")
    c("без Accept-Encoding: сразу читается", b"<" in r2.get_data())
    c("сжатое и обычное — один и тот же файл", body == r2.get_data())

    return c.fails


def run_etag():
    """Приложение не качается заново, пока не поменялось.

    Сто с лишним килобайт уезжали клиенту при КАЖДОМ открытии магазина, хотя
    файл меняется только при выкладке. По мобильной сети это заметная пауза
    перед первым экраном — и оплачивается она трафиком покупателя.
    """
    c = Checker("Повторное открытие приложения")

    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    etag = r.headers.get("ETag")
    c("сервер называет версию файла (ETag)", bool(etag))

    r2 = client.get("/", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
    c("та же версия — ответ 304", r2.status_code == 304)
    c("и тело не пересылается", not r2.get_data())
    c("версия в ответе сохраняется", r2.headers.get("ETag") == etag)

    r3 = client.get("/", headers={"Accept-Encoding": "gzip", "If-None-Match": '"чужое"'})
    c("другая версия — файл отдаётся целиком", r3.status_code == 200 and len(r3.get_data()) > 1024)

    # Cache-Control обязан оставаться no-cache: иначе браузер перестанет
    # спрашивать вовсе и после выкладки покажет старое приложение.
    c("браузер продолжает сверяться после выкладки",
      "no-cache" in (r.headers.get("Cache-Control") or ""))

    # --- То же для справочников витрины ---
    c2 = Checker("Повторная загрузка справочников")
    for path in ("/api/products", "/api/locations", "/api/categories", "/api/brands", "/api/flavors"):
        a = client.get(path)
        tag = a.headers.get("ETag")
        b = client.get(path, headers={"If-None-Match": tag})
        c2(f"{path}: не пересылается заново", bool(tag) and b.status_code == 304)

    # Данные поменялись — старая версия больше не подходит, иначе витрина
    # застынет на позавчерашних остатках.
    import db, server
    before = client.get("/api/products").headers.get("ETag")
    pid = db.add_product("Минск", "disposable", "ETag-проверка", 11.0, 3)
    server._cache_bust()
    after = client.get("/api/products").headers.get("ETag")
    c2("после правки витрины версия сменилась", before != after)
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM products WHERE id = %s"), (pid,))
    conn.commit(); conn.close()
    server._cache_bust()

    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
