"""Разрезание server.py на модули не должно ничего терять по дороге.

Три беды, каждая из которых уже случалась или была в шаге от того.

1. Модуль вынесли, а импортировать внизу server.py забыли. Маршруты просто
   не регистрируются: приложение поднимается, ручки отвечают 404, и молчат об
   этом ровно до того теста, который их дёргает. Здесь это ловится сразу.

2. Модуль скопировал себе помощника (`from server import get_admin`) вместо
   обращения через модуль. Копия не заметит подмены — а весь тестовый стенд
   стоит на подмене auth.get_admin: проверки прав начнут проходить вхолостую.

3. Приписка префикса задела чужое поле: db.REFERRAL_BONUS превращался в
   db.server.REFERRAL_BONUS. Ruff такое не видит — это обращение к атрибуту,
   а не неизвестное имя, — и падает оно уже у покупателя.
"""
import io
import os
import re

from _common import server, Checker

from partut.web import auth

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ВЕБ = os.path.join(КОРЕНЬ, "partut", "web")

# Помощники, которые обязаны браться ЧЕРЕЗ модуль: на их подмене стоят тесты.
ЧЕРЕЗ_МОДУЛЬ = ["get_admin", "get_user", "_bg", "_gate", "_text", "_cache_bust",
                "deny_city", "_guard_owner_only", "app"]


def _модули():
    """Файлы partut/web/, объявляющие Blueprint, — то есть модули с ручками.

    Признак — сам Blueprint в тексте, а не имя файла. Раньше модули искались по
    префиксу «server_», и после переезда в папку поиск нашёл бы ноль файлов:
    тест прошёл бы вхолостую, ничего не проверив.
    """
    найдено = []
    for и in sorted(os.listdir(ВЕБ)):
        if not и.endswith(".py") or и == "__init__.py":
            continue
        if re.search(r"^bp = Blueprint\(", io.open(os.path.join(ВЕБ, и), encoding="utf-8").read(), re.M):
            найдено.append(и)
    return найдено


def run():
    c = Checker("Каждый модуль с ручками подключён к фабрике")
    модули = _модули()
    c(f"модули найдены ({len(модули)})", len(модули) >= 5)

    # Спрашиваем у самого приложения, а не у текста: забыть модуль в списке —
    # ровно та ошибка, ради которой тест и написан (три модуля однажды не
    # подключили, и их ручки молча отвечали 404).
    for файл in модули:
        имя = файл[:-3]
        c(f"{имя} зарегистрирован в приложении (иначе его ручки — 404)",
          имя in server.app.blueprints)

    # И проверка делом: модуль не просто упомянут, а действительно загружен.
    import sys
    for файл in модули:
        c(f"{файл[:-3]} и правда загружен", f"partut.web.{файл[:-3]}" in sys.modules)

    c2 = Checker("Помощники берутся через модуль, а не копией")
    for файл in модули:
        текст = io.open(os.path.join(ВЕБ, файл), encoding="utf-8").read()
        скопировано = [и for и in ЧЕРЕЗ_МОДУЛЬ
                       if re.search(rf"^from partut\.web\.(server|auth) import .*\b{и}\b",
                                    текст, re.M)]
        c2(f"{файл[:-3]} не копирует помощников"
           + ("" if not скопировано else f": {скопировано}"), not скопировано)

    c3 = Checker("Префикс не задел чужие поля")
    for файл in модули + ["server.py"]:
        текст = io.open(os.path.join(ВЕБ, файл), encoding="utf-8").read()
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

    # Подмена auth.get_admin обязана доходить до вынесенных модулей.
    c5 = Checker("Подмена доходит до вынесенного кода")
    звонков = {"n": 0}
    настоящий = auth.get_admin

    def счётчик(*a, **k):
        звонков["n"] += 1
        return настоящий(*a, **k)

    auth.get_admin = счётчик
    try:
        from _common import client
        client.post("/api/admin/promos", json={"initData": "x"})      # server_promos
        client.post("/api/admin/stats", json={"initData": "x"})       # server_admin
        client.post("/api/admin/orders", json={"initData": "x"})      # server_orders
        client.post("/api/admin/users", json={"initData": "x"})       # server_customers
    finally:
        auth.get_admin = настоящий
    c5("вынесенные модули сходили через подменённый auth.get_admin",
       звонков["n"] >= 4)

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)


def run_standalone():
    """«python -m partut.web.server» обязан отдавать ВСЕ ручки, а не половину.

    Запуск файла напрямую (python partut/web/server.py) делает из него модуль
    __main__, и пакет загрузил бы server ВТОРОЙ раз — отдельным модулем со своим
    Flask-приложением.
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

    c = Checker("Сайт, запущенный отдельно (python -m partut.web.server)")

    с_сокетом = socket.socket()
    с_сокетом.bind(("127.0.0.1", 0))
    порт = с_сокетом.getsockname()[1]
    с_сокетом.close()

    окружение = dict(os.environ)
    окружение.update({"BOT_TOKEN": "000000:TEST-NO-SEND", "PORT": str(порт),
                      "DATABASE_URL": "", "KEEP_WARM": "0"})
    процесс = subprocess.Popen(["python3", "-m", "partut.web.server"],
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


def run_write_paths_registered():
    """Каждая ручка, меняющая каталог, обязана быть в _WRITE_PATHS.

    Кэш сбрасывается в одном месте — в after_request по списку путей. Забыть
    вписать туда новую ручку легко, а последствие подлое: сервер сохранил, а
    приложение ещё полминуты показывает старое. Именно так и вышло с
    /api/admin/product/to-model: экран говорил «Готово ✅», модель создавалась,
    а список товаров оставался прежним — и выглядело это как «не сработало».

    Проверяем ручки каталога: всё под /api/admin/product и /api/admin/model
    меняет то, что кэшируется. Если появится ручка, которая только читает,
    впишите её в ЧИТАЮЩИЕ — явно, чтобы это было решением, а не забывчивостью.
    """
    # Ручки, которые ничего не меняют, — списком и явно.
    ЧИТАЮЩИЕ = {
        "/api/admin/products",      # список товаров
        "/api/admin/models",        # список моделей
    }

    c = Checker("Ручки записи сбрасывают кэш")
    пишущие = []
    for правило in server.app.url_map.iter_rules():
        путь = str(правило)
        if "POST" not in правило.methods:
            continue
        if not (путь.startswith("/api/admin/product") or путь.startswith("/api/admin/model")):
            continue
        if путь in ЧИТАЮЩИЕ:
            continue
        пишущие.append(путь)

    c(f"ручки каталога найдены ({len(пишущие)})", len(пишущие) >= 8)
    забыты = [п for п in пишущие if п not in server._WRITE_PATHS]
    c("все вписаны в _WRITE_PATHS" + ("" if not забыты else f": забыты {забыты}"), not забыты)
    return c.fails
