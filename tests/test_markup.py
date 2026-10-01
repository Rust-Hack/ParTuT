"""Разметка приложения: идентификаторы не повторяются, и всё, что код ищет
по идентификатору, существует.

30 сентября 2026 новый экран «Приём поставки» получил кнопку «Назад» с id
deliveryClose — а такой id уже был у «Отмены» в окне доставки покупателя.
$("deliveryClose") находит первый элемент с этим id, и обработчик нового
экрана молча сел бы на кнопку покупателя: «Отмена» в окне доставки перестала
бы закрывать окно. Нашлось случайно — браузерный сценарий споткнулся о два
элемента. Здесь это ловится без браузера.
"""
import os
import re
from collections import Counter

from _common import Checker

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПРИЛОЖЕНИЕ = os.path.join(КОРЕНЬ, "partut", "webapp")


def run():
    c = Checker("Разметка: идентификаторы")
    with open(os.path.join(ПРИЛОЖЕНИЕ, "index.html"), encoding="utf-8") as f:
        разметка = f.read()
    части = sorted(и for и in os.listdir(os.path.join(ПРИЛОЖЕНИЕ, "app")) if и.endswith(".js"))
    код = ""
    for и in части:
        with open(os.path.join(ПРИЛОЖЕНИЕ, "app", и), encoding="utf-8") as f:
            код += f.read()

    в_разметке = re.findall(r'\bid="([^"$]+)"', разметка)
    c(f"идентификаторы в разметке найдены ({len(в_разметке)})", len(в_разметке) > 200)
    дубли = sorted(и for и, n in Counter(в_разметке).items() if n > 1)
    c("в разметке ни один id не повторяется" + (f": {дубли}" if дубли else ""), not дубли)

    # Что код ищет через $("…") — есть в разметке или создаётся самим кодом:
    # в шаблоне (id="…") или помощником выбора из списка (pickerHtml("…")).
    известны = set(в_разметке)
    известны |= set(re.findall(r'\bid="([A-Za-z_][\w-]*)"', код))
    выбор = set(re.findall(r'pickerHtml\("([A-Za-z_][\w-]*)"', код))
    известны |= выбор | {и + "_new" for и in выбор}     # список и поле «новое значение» — парой
    ищет = set(re.findall(r'\$\("([A-Za-z_][\w-]*)"\)', код))
    c(f"код ищет по id ({len(ищет)})", len(ищет) > 200)
    нет = sorted(ищет - известны)
    c("всё, что код ищет по id, существует" + (f": {нет}" if нет else ""), not нет)
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
