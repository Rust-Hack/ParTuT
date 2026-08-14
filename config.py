"""
config.py — общие настройки и справочники.

Их используют И бот (bot.py), И веб-сервер Mini App (server.py),
чтобы всё было в одном месте, а не дублировалось в двух файлах.
"""

import os
import threading
import time

from dotenv import load_dotenv

import db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Публичный адрес, по которому открывается Mini App (появится после деплоя).
# Пока пусто — кнопка Mini App просто не показывается.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()


# --- Справочники: код <-> красивое название ---
CATEGORIES = {
    "disposable": "🔋 Одноразки",
    "liquid":     "💧 Жидкости",
    "podsystem":  "🧩 Подсистемы",
}

CITIES = {
    "minsk":  "Минск",
    "slutsk": "Слуцк",
    "turov":  "Туров",
}


# --- Админы / продавцы ---
def _ids_from_env(name):
    """Читает из .env строку '123,456' и превращает в множество чисел {123, 456}."""
    result = set()
    for part in os.environ.get(name, "").split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


ADMIN_IDS = _ids_from_env("ADMIN_IDS")        # владелец(ы) — управляют товарами

# Супер-админ(ы): ВСЕГДА админ и ЗАЩИЩЁН — его нельзя удалить/отвязать/тронуть
# через админ-инструменты (даже другим админам). 716030279 зашит навсегда,
# плюс можно добавить ещё через env SUPER_ADMIN_IDS.
SUPER_ADMIN_IDS = _ids_from_env("SUPER_ADMIN_IDS") | {716030279}


def is_super_admin(user_id):
    return user_id in SUPER_ADMIN_IDS


# Кому приходят вопросы клиентов «Написать в поддержку» (менеджер/поддержка).
# По умолчанию — 1376577605; можно переопределить/добавить через env SUPPORT_IDS.
SUPPORT_IDS = _ids_from_env("SUPPORT_IDS") | {1376577605}

CITY_ADMINS = {                                # продавцы по городам — им идут заказы
    "minsk":  _ids_from_env("ADMIN_MINSK"),
    "slutsk": _ids_from_env("ADMIN_SLUTSK"),
    "turov":  _ids_from_env("ADMIN_TUROV"),
}


# --- Админы из приложения (их супер-админ добавляет прямо в Mini App) ---
# Права спрашивают на КАЖДЫЙ админ-запрос, поэтому список ненадолго кэшируем:
# без этого каждая проверка прав — отдельный поход в базу.
_STAFF_TTL = 30
_staff_cache = {"at": 0.0, "by_city": {}}
_staff_lock = threading.Lock()


def _staff_by_city():
    """{'': {id...}, 'minsk': {id...}} из базы. Если база недоступна или таблицы
    ещё нет — возвращаем пусто, и права считаются только по переменным окружения:
    сломанная база не должна отбирать доступ у владельца."""
    now = time.time()
    with _staff_lock:
        if now - _staff_cache["at"] < _STAFF_TTL:
            return _staff_cache["by_city"]
    try:
        by_city = db.staff_ids_by_city()
    except Exception as e:
        print(f"Не удалось прочитать админов из базы: {e}")
        by_city = {}
    with _staff_lock:
        _staff_cache["at"] = now
        _staff_cache["by_city"] = by_city
    return by_city


def refresh_staff():
    """Сбросить кэш — вызывается сразу после добавления/удаления админа,
    чтобы права начали действовать немедленно, а не через полминуты."""
    with _staff_lock:
        _staff_cache["at"] = 0.0


def all_admin_ids():
    ids = set(ADMIN_IDS) | set(SUPER_ADMIN_IDS)     # супер-админ всегда среди админов
    for city_ids in CITY_ADMINS.values():
        ids |= city_ids
    for city_ids in _staff_by_city().values():      # добавленные из приложения
        ids |= city_ids
    return ids


def is_admin(user_id):
    return user_id in all_admin_ids()


def admins_for_city(city):
    """Кому отправлять заказ этого города. Если продавец города не задан —
    владельцу: заказ обязан кому-то прийти, иначе его просто никто не увидит."""
    staff = _staff_by_city()
    ids = set(CITY_ADMINS.get(city) or set()) | set(staff.get(city) or set())
    if ids:
        return ids
    return set(ADMIN_IDS) | set(staff.get("") or set())


# --- Реквизиты оплаты (МЕНЯЙТЕ ПОД СЕБЯ). Обычный текст — его показывает приложение. ---
PAYMENT_INFO = (
    "Карта: 0000 0000 0000 0000 (Иван И.)\n"
    "или по номеру: +375 00 000-00-00"
)

# Через сколько минут продавец обычно подтверждает (для доверия клиента).
CONFIRM_MINUTES = 15
