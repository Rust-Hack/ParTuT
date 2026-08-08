"""
run.py — единый запуск для хостинга (Render).

На бесплатном хостинге сервис — один процесс, который обязан «слушать порт».
Поэтому здесь мы:
  • запускаем бота (long polling) в фоновом потоке,
  • а веб-сервер Mini App (Flask) — в основном (он и слушает порт $PORT).

Локально можно по-прежнему запускать по отдельности:
  venv/bin/python bot.py      — только бот
  venv/bin/python server.py   — только сайт
А этот файл — как на хостинге: и то, и другое сразу.
"""

import os
import threading

import bot                      # регистрирует обработчики (polling не стартует при импорте)
from server import app         # Flask-приложение Mini App


def main():
    # Бот — в фоне (daemon: завершится вместе с процессом).
    threading.Thread(target=bot.run, daemon=True).start()
    # Веб-сервер — на порту, который даёт хостинг (или 5000 локально).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
