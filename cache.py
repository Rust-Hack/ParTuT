"""
cache.py — кэш чтений в памяти и ответы со сверкой версии.

Листовой модуль: ничего из своего проекта не импортирует. Так и задумано —
кэш нужен всем, а если бы он лежал в server.py, то каждый, кому нужен кэш,
тянул бы за собой всё приложение. На этом и вырос круг импортов, который
пришлось разрывать.

Две разные вещи под одной крышей, и обе про «не ходить лишний раз»:
  • _cache_* — не ходить в базу за тем, что меняется раз в неделю;
  • json_etag — не гонять по сети то, что у браузера уже есть.
"""

import hashlib
import json
import threading
import time

from flask import Response, request

_cache = {}
_cache_lock = threading.Lock()


def get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        if item:
            _cache.pop(key, None)
    return None


def put(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


def bust(*prefixes):
    """Сбросить кэш чтений. Без аргументов — весь; с префиксами — только нужные ключи.

    Точечный сброс важен для заказов: заказ меняет ТОЛЬКО остатки (каталог и статистику),
    а способы доставки/точки/бренды остаются прежними. Раньше любой заказ чистил всё,
    и следующий покупатель снова ждал Neon на экране «Способ получения»."""
    with _cache_lock:
        if not prefixes:
            _cache.clear()
            return
        for k in [k for k in _cache if k.startswith(prefixes)]:
            _cache.pop(k, None)


def json_etag(payload):
    """JSON со сверкой версии: пока данные не менялись, браузер получает 304.

    Витрина и справочники перекачивались целиком при каждом открытии приложения,
    хотя меняются редко. Хэш считается от готового тела ответа, поэтому «не
    менялись» здесь значит буквально это — отдать устаревшее нельзя.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Метка версии зависит и от сжатия: ниже ответ может уйти gzip'ом, и одна
    # метка на два разных тела — это шанс однажды получить от прокси не тот
    # вариант, который просили.
    gz = "gzip" in request.headers.get("Accept-Encoding", "")
    etag = '"%s%s"' % (hashlib.md5(body.encode("utf-8")).hexdigest(), "-gz" if gz else "")
    if request.headers.get("If-None-Match") == etag:
        resp = Response(status=304)
    else:
        resp = Response(body, content_type="application/json")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp
