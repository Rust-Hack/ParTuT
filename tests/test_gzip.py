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

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
