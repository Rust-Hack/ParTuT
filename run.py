"""
run.py — единый запуск для хостинга (Render).

На бесплатном хостинге сервис — один процесс, который обязан «слушать порт».
Поэтому здесь мы:
  • запускаем бота (long polling) в фоновом потоке,
  • а веб-сервер Mini App (Flask) — в основном (он и слушает порт $PORT).

Локально можно по-прежнему запускать по отдельности:
  venv/bin/python -m partut.bot.handlers   — только бот
  venv/bin/python -m partut.web.server     — только сайт
А этот файл — как на хостинге: и то, и другое сразу.

Почему точка входа лежит в корне, а не внутри пакета: пакет — это библиотека,
его импортируют. Запускать — отсюда, и только отсюда. По одному файлу в корне
сразу видно, чем поднимается магазин.
"""

import os
import time
import threading

import requests
from waitress import serve     # «боевой» веб-сервер (вместо встроенного в Flask)

from partut import db
from partut.bot import handlers as bot   # обработчики; polling при импорте не стартует
from partut.web.server import app        # Flask-приложение Mini App


def _keep_warm():
    """Держим «тёплыми» и веб-сервис Render, и базу Neon — иначе первый запрос после
    простоя тормозит (Render засыпает без входящих запросов, Neon-compute — без запросов к БД).
      • HTTP-пинг своего /health — не даёт Render уснуть (нужен RENDER_EXTERNAL_URL);
      • лёгкий SELECT 1 — не даёт уснуть Neon-compute."""
    if os.environ.get("KEEP_WARM", "1") != "1":     # можно выключить, если жрёт лимиты тарифа
        print("keep-warm выключен (KEEP_WARM=0)")
        return
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    while True:
        time.sleep(240)         # каждые 4 минуты (Neon засыпает ~через 5 мин простоя)
        # 1) будим/держим Neon
        try:
            conn = db.connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()
        except Exception as e:
            print(f"keep-warm БД не удался: {e}")
        # 2) держим Render (только если знаем свой адрес)
        if url:
            try:
                requests.get(url + "/health", timeout=10)
            except Exception as e:
                print(f"keep-warm пинг не удался: {e}")


def main():
    # Куда уходит вторая копия базы — печатаем при старте.
    #
    # Переменные окружения читаются ОДИН раз, при запуске: поменять их в панели
    # хостинга мало, нужен перезапуск. Дважды это стоило нам вечера гадания —
    # магазин работал со старым адресом, а понять это можно было только по чужой
    # формулировке в тексте ошибки. Теперь ответ есть в первых строках лога.
    from partut.integrations import offsite
    # flush=True не украшение: вывод в файл (а на хостинге — в лог) буферизуется
    # блоками, и строка, напечатанная при старте, доезжает неизвестно когда.
    # Проверено живьём: процесс подняли, остановили — в логе её нет вовсе.
    # Диагностика, которую не видно в нужный момент, бесполезна.
    print(f"Вторая копия базы: {offsite.хранилище()}" if offsite.настроено()
          else "Вторая копия базы: НЕ настроена (BACKUP_URL пуст)", flush=True)

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
    print(f"Веб-сервер запущен на порту {port}", flush=True)
    # threads=8 — параллельно обслуживаем всплеск запросов при старте Mini App
    # (me/locations/products/бонусы летят разом), иначе они стоят в очереди.
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
