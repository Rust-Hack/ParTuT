"""Разрезание server.py на модули не должно ничего терять по дороге.

Три беды, каждая из которых уже случалась или была в шаге от того.

1. Модуль вынесли, а импортировать внизу server.py забыли. Маршруты просто
   не регистрируются: приложение поднимается, ручки отвечают 404, и молчат об
   этом ровно до того теста, который их дёргает. Здесь это ловится сразу.

2. Модуль скопировал себе помощника (`from server import get_admin`) вместо
   обращения через модуль. Копия не заметит подмены — а весь тестовый стенд
   стоит на подмене server.get_admin: проверки прав начнут проходить вхолостую.

3. Приписка префикса задела чужое поле: db.REFERRAL_BONUS превращался в
   db.server.REFERRAL_BONUS. Ruff такое не видит — это обращение к атрибуту,
   а не неизвестное имя, — и падает оно уже у покупателя.
"""
import io
import os
import re

from _common import server, Checker

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Помощники, которые обязаны браться ЧЕРЕЗ server: на их подмене стоят тесты.
ЧЕРЕЗ_МОДУЛЬ = ["get_admin", "get_user", "_bg", "_gate", "_text", "_cache_bust",
                "deny_city", "_guard_owner_only", "app"]


def _модули():
    return sorted(и for и in os.listdir(КОРЕНЬ)
                  if и.startswith("server_") and и.endswith(".py"))


def run():
    исходник = io.open(os.path.join(КОРЕНЬ, "server.py"), encoding="utf-8").read()

    c = Checker("Каждый вынесенный модуль подключён к server.py")
    модули = _модули()
    c(f"модули найдены ({len(модули)})", len(модули) >= 5)
    for файл in модули:
        имя = файл[:-3]
        c(f"{имя} импортирован в server.py (иначе его ручки — 404)",
          re.search(rf"^import {имя}\b", исходник, re.M) is not None)

    # И проверка делом: модуль не просто упомянут, а действительно загружен.
    import sys
    for файл in модули:
        c(f"{файл[:-3]} и правда загружен", файл[:-3] in sys.modules)

    c2 = Checker("Помощники берутся через server, а не копией")
    for файл in модули:
        текст = io.open(os.path.join(КОРЕНЬ, файл), encoding="utf-8").read()
        скопировано = [и for и in ЧЕРЕЗ_МОДУЛЬ
                       if re.search(rf"^from server import .*\b{и}\b", текст, re.M)]
        c2(f"{файл[:-3]} не копирует помощников"
           + ("" if not скопировано else f": {скопировано}"), not скопировано)

    c3 = Checker("Префикс не задел чужие поля")
    for файл in модули + ["server.py"]:
        текст = io.open(os.path.join(КОРЕНЬ, файл), encoding="utf-8").read()
        кривое = re.findall(r"\b(?:db|server)\.(?:db|server)\.\w+", текст)
        c3(f"{файл[:-3]} без двойного префикса"
           + ("" if not кривое else f": {sorted(set(кривое))}"), not кривое)

    c4 = Checker("Ручки не задваиваются")
    пути = {}
    for правило in server.app.url_map.iter_rules():
        for метод in правило.methods - {"HEAD", "OPTIONS"}:
            пути.setdefault((метод, str(правило)), []).append(правило.endpoint)
    дубли = {к: v for к, v in пути.items() if len(v) > 1}
    c4("один путь — одна ручка" + ("" if not дубли else f": {list(дубли)[:3]}"), not дубли)
    c4(f"ручек всего ({len(пути)})", len(пути) > 100)

    # Подмена server.get_admin обязана доходить до вынесенных модулей.
    c5 = Checker("Подмена доходит до вынесенного кода")
    звонков = {"n": 0}
    настоящий = server.get_admin

    def счётчик(*a, **k):
        звонков["n"] += 1
        return настоящий(*a, **k)

    server.get_admin = счётчик
    try:
        from _common import client
        client.post("/api/admin/promos", json={"initData": "x"})      # server_promos
        client.post("/api/admin/stats", json={"initData": "x"})       # server_admin
        client.post("/api/admin/orders", json={"initData": "x"})      # server_orders
        client.post("/api/admin/users", json={"initData": "x"})       # server_customers
    finally:
        server.get_admin = настоящий
    c5("вынесенные модули сходили через подменённый server.get_admin",
       звонков["n"] >= 4)

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)


def run_standalone():
    """«python server.py» обязан отдавать ВСЕ ручки, а не половину.

    Запуск файла напрямую делает из него модуль __main__, и вынесенные модули
    импортируют server ВТОРОЙ раз — отдельным модулем со своим Flask-приложением.
    Маршруты регистрируются на нём, а порт слушает первое: ручки из вынесенных
    модулей молча отвечают 404.

    Это не выдумка: так и случилось после разрезов, и нашлось только когда
    приложение открыли в браузере. Ни один разбор текста такого не видит —
    поэтому здесь настоящий запуск и настоящий запрос.
    """
    import socket
    import subprocess
    import time
    import urllib.error
    import urllib.request

    c = Checker("Сайт, запущенный напрямую (python server.py)")

    с_сокетом = socket.socket()
    с_сокетом.bind(("127.0.0.1", 0))
    порт = с_сокетом.getsockname()[1]
    с_сокетом.close()

    окружение = dict(os.environ)
    окружение.update({"BOT_TOKEN": "000000:TEST-NO-SEND", "PORT": str(порт),
                      "DATABASE_URL": "", "KEEP_WARM": "0"})
    процесс = subprocess.Popen(["python3", os.path.join(КОРЕНЬ, "server.py")],
                               cwd=КОРЕНЬ, env=окружение,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        адрес = f"http://127.0.0.1:{порт}"
        поднялся = False
        for _ in range(60):
            try:
                urllib.request.urlopen(адрес + "/health", timeout=1).read()
                поднялся = True
                break
            except Exception:
                time.sleep(0.5)
        c("сайт поднялся", поднялся)
        if not поднялся:
            return c.fails

        # По одной ручке из каждого вынесенного модуля: они и пропадали.
        for путь in ("/api/products", "/api/locations", "/api/categories", "/api/brands"):
            try:
                код = urllib.request.urlopen(адрес + путь, timeout=5).getcode()
            except urllib.error.HTTPError as e:
                код = e.code
            except Exception as e:
                код = f"не ответил ({type(e).__name__})"
            c(f"{путь} отвечает (было 404 после разрезов): {код}", код == 200)
    finally:
        процесс.terminate()
        try:
            процесс.wait(timeout=10)
        except subprocess.TimeoutExpired:
            процесс.kill()
    return c.fails
