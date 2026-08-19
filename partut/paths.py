"""
paths.py — где что лежит на диске. Одно место на весь пакет.

Раньше путь до webapp/ считался прямо в server.py от __file__, и переезд файла
в другую папку тихо ломал сборку страницы: приложение поднималось, а витрина
отдавала 500 при первом же открытии. Такие пути обязаны быть в одном файле —
тогда переезд правится одной строкой, а не поиском по репозиторию.
"""

from pathlib import Path

ПАКЕТ = Path(__file__).resolve().parent      # …/partut
КОРЕНЬ = ПАКЕТ.parent                        # …/vape-bot — рядом с run.py

WEBAPP = ПАКЕТ / "webapp"                    # разметка, стили и куски приложения
INDEX = WEBAPP / "index.html"
STYLES = WEBAPP / "styles.css"
APP_PARTS = WEBAPP / "app"                   # 01-core.js, 02-games.js, …
