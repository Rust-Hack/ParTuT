"""Ограничения частоты: пауза для человека и ведро для ресурса.

Стенд паузы выключает (см. _common) — иначе каждая проверка, делающая два
запроса подряд, упиралась бы в антиспам вместо того, что она проверяет.
Значит проверить сами ограничения надо здесь, включив их обратно.

Отдельно проверяем то, ради чего всё и затевалось: бюджет обращений к Telegram.
/api/photo — единственная ручка, открытая вообще всем, и каждый её промах стоит
двух обращений к Telegram по токену бота. По тому же токену уходят заказы
продавцу — то есть чужой скрипт, не зная о магазине ничего, останавливает
торговлю, не тронув ни витрину, ни базу.
"""
import time

from _common import db, client, Checker

from partut import limits
from partut.web import server
from partut.integrations import tgsend


class _Ответ:
    content = b"\xff\xd8\xff" + b"vape" * 20
    headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass


def run_пауза():
    """«Не чаще раза в N секунд» — каждому свой счёт."""
    c = Checker("Пауза: человеку, а не всем сразу")
    limits.включить()
    try:
        п = limits.Пауза(0.3)
        c("первый раз можно", п.сколько_ждать("вася") == 0)
        c("сразу второй — нельзя", п.сколько_ждать("вася") > 0)
        c("другому — можно", п.сколько_ждать("петя") == 0)
        time.sleep(0.35)
        c("после паузы снова можно", п.сколько_ждать("вася") == 0)

        c2 = Checker("Пауза не растёт в памяти без края")
        мелкая = limits.Пауза(0.01, максимум_ключей=50)
        for i in range(500):
            мелкая.сколько_ждать(f"кто-{i}")
        c("ключей не больше потолка", len(мелкая._когда) <= 50)

        c3 = Checker("Выключатель стенда")
        limits.выключить()
        всегда = limits.Пауза(60)
        c("с выключенными паузами проходит подряд",
          всегда.сколько_ждать("х") == 0 and всегда.сколько_ждать("х") == 0)
        return c.fails + c2.fails + c3.fails
    finally:
        limits.выключить()


def run_ведро():
    """«Не больше N за окно» — на всех вместе."""
    c = Checker("Ведро: всплеск разрешён, средний расход — нет")
    в = limits.Ведро(5, 60)
    взяли = sum(1 for _ in range(20) if в.взять())
    c("всплеск ровно на ёмкость", взяли == 5)
    c("сверх ёмкости — отказ", в.взять() is False)
    в.наполнить()
    c("после наполнения снова можно", в.взять() is True)

    быстрое = limits.Ведро(2, 1)          # два в секунду
    быстрое.взять(); быстрое.взять()
    c("исчерпано", быстрое.взять() is False)
    time.sleep(0.6)
    c("восполняется само", быстрое.взять() is True)
    return c.fails


def run_бюджет_телеграма():
    """Чужой скрипт не должен выжечь токен бота картинками."""
    c = Checker("Бюджет обращений к Telegram")

    обращений = []
    настоящий_get_file, настоящий_get = tgsend.tg.get_file, server.requests.get
    tgsend.tg.get_file = lambda fid: (обращений.append(fid),
                                      type("F", (), {"file_path": f"p/{fid}.jpg"})())[1]
    server.requests.get = lambda url, **kw: _Ответ()
    старое_ведро = server.TELEGRAM_PHOTO_BUDGET
    server.TELEGRAM_PHOTO_BUDGET = limits.Ведро(5, 60)
    server._photo_misses.забыть()
    server._photo_cache.clear()
    server._photo_cache_bytes = 0
    server._file_path_cache.clear()
    try:
        коды = []
        for i in range(30):
            коды.append(client.get(f"/api/photo?file_id=chuzhoy{i}").status_code)
        c("к Telegram сходили ровно по бюджету", len(обращений) == 5)
        c("остальным отказали", коды.count(503) == 25)
        c("отказ временный (503), а не «нет такой» (404)", 404 not in коды)

        # Промах помнят: тот же чужой ключ не тратит бюджет второй раз.
        server.TELEGRAM_PHOTO_BUDGET = limits.Ведро(50, 60)
        было = len(обращений)
        for _ in range(10):
            client.get("/api/photo?file_id=chuzhoy0")
        c("повторы промаха в Telegram не ходят", len(обращений) == было)

        # Настоящая картинка платит один раз: дальше она в памяти и в базе.
        server._photo_misses.забыть()
        pid = db.add_product("minsk", "pods", "ЛимитПод", 10, 1)
        db.update_field(pid, "photo", "нормальное_фото")
        было = len(обращений)
        for _ in range(10):
            r = client.get("/api/photo?file_id=нормальное_фото")
        c("своя картинка отдаётся", r.status_code == 200)
        c("а в Telegram за ней сходили один раз", len(обращений) - было == 1)
        return c.fails
    finally:
        tgsend.tg.get_file, server.requests.get = настоящий_get_file, настоящий_get
        server.TELEGRAM_PHOTO_BUDGET = старое_ведро
        server._photo_misses.забыть()
        server._photo_cache.clear()
        server._photo_cache_bytes = 0


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run_пауза() + run_ведро() + run_бюджет_телеграма()) else 0)
