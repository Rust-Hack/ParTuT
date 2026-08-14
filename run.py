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
import time
import threading

import requests
from waitress import serve     # «боевой» веб-сервер (вместо встроенного в Flask)

import bot                      # регистрирует обработчики (polling не стартует при импорте)
import db
from server import app         # Flask-приложение Mini App


def _keep_warm():
    """Пингуем свой же /health, чтобы Render (free) не усыплял сервис после простоя.
    Работает только если задан RENDER_EXTERNAL_URL (его выдаёт Render автоматически)."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        return
    while True:
        time.sleep(600)         # каждые 10 минут (порог засыпания Render — 15 мин)
        try:
            requests.get(url + "/health", timeout=10)
        except Exception as e:
            print(f"keep-warm пинг не удался: {e}")


def main():
    # Прогреваем пул соединений к БД, чтобы первый запрос не платил за инициализацию.
    try:
        c = db.connect(); c.close()
    except Exception as e:
        print(f"Прогрев БД пропущен: {e}")

    # Бот — в фоне (daemon: завершится вместе с процессом).
    threading.Thread(target=bot.run, daemon=True).start()
    # Самопинг, чтобы не засыпал на бесплатном тарифе.
    threading.Thread(target=_keep_warm, daemon=True).start()

    # Веб-сервер — на порту, который даёт хостинг (или 5000 локально).
    port = int(os.environ.get("PORT", 5000))
    print(f"Веб-сервер запущен на порту {port}")
    # threads=8 — параллельно обслуживаем всплеск запросов при старте Mini App
    # (me/locations/products/бонусы летят разом), иначе они стоят в очереди.
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
