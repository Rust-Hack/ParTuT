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

# Без токена бот не существует, поэтому падаем сразу — но по-человечески.
# Голый KeyError ничего не объясняет тому, кто впервые поднимает магазин.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit(
        "Не задан BOT_TOKEN — без него бот не запустится.\n"
        "Локально: впишите его в файл .env рядом с кодом (BOT_TOKEN=...).\n"
        "На Render: Settings → Environment → добавьте переменную BOT_TOKEN.")

# Публичный адрес, по которому открывается Mini App (появится после деплоя).
# Пока пусто — кнопка Mini App просто не показывается.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()


# --- Справочники: код <-> красивое название ---
# Категории переехали в базу (таблица categories, стартовый набор — db.CATEGORY_SEED):
# владелец заводит и переименовывает их прямо в приложении. Здесь оставлен только
# ориентир, что коды disposable/liquid/podsystem — особые: к ним привязаны свои
# поля в редакторе товара (затяжки у одноразок, крепость и объём у жидкостей).
SPECIAL_CATEGORIES = ("disposable", "liquid", "podsystem")

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

# ------------------------------------------------------------------
#  Четыре уровня доступа. Списки двух верхних живут в переменных сервера,
#  а не в базе: из приложения их не отредактировать в принципе, поэтому
#  никто не может выдать права сам себе или забрать их у того, кто выше.
#
#  1. Разработчик (DEV_IDS)  — всё, что у владельца, плюс техническое:
#     отчёты о сбоях, сброс статистики. Его нельзя тронуть из приложения.
#  2. Владелец   (SUPER_ADMIN_IDS) — весь магазин: ассортимент, цены,
#     настройки, деньги, доступ продавцов, журнал, статистика.
#  3. Продавец   (таблица staff) — свои точки: заказы, склад, цены, завоз.
#     Пустой город значит «продавец всех точек», но НЕ владельца.
#  4. Покупатель — все остальные.
# ------------------------------------------------------------------
DEV_IDS = _ids_from_env("DEV_IDS") | {716030279}

# Владелец магазина. Разработчик тоже владелец: отдельно перечислять его
# в каждой проверке — верный способ однажды забыть.
SUPER_ADMIN_IDS = _ids_from_env("SUPER_ADMIN_IDS") | DEV_IDS


def is_dev(user_id):
    """Ведущий проекта. Отличается от владельца только техническими правами."""
    return user_id in DEV_IDS


def is_owner(user_id):
    """Владелец магазина (и разработчик как один из них)."""
    return user_id in SUPER_ADMIN_IDS


# Прежнее имя: осталось у бота и части маршрутов, значит то же самое.
is_super_admin = is_owner


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


def seed_admins_from_env():
    """Одноразово переносит админов из переменных окружения в базу.

    После переноса база — единственный источник прав, поэтому любого админа
    можно убрать прямо из приложения. Раньше записи из окружения удалить было
    нельзя вовсе: сервер читает их при запуске, и приложение до них не достаёт.

    Отметку о переносе храним в settings, а НЕ «таблица пуста»: иначе стоит
    владельцу убрать всех продавцов, как при следующем перезапуске они бы
    вернулись из окружения.

    Города при переносе переводим из кодов в названия: заказы носят название
    («Минск»), а переменные заданы кодом (ADMIN_MINSK), и раньше они не
    совпадали — продавцы городов из окружения не получали заказы никогда."""
    try:
        if db.get_setting("staff_seeded"):
            return
        rows = [(uid, "") for uid in ADMIN_IDS]
        for code, ids in CITY_ADMINS.items():
            rows += [(uid, CITIES.get(code, code)) for uid in ids]
        for uid, city in rows:
            if uid in SUPER_ADMIN_IDS and not city:
                continue                      # владелец и так админ всегда
            db.add_staff(uid, city, "перенесён из настроек сервера", None)
        db.set_setting("staff_seeded", "1")
        refresh_staff()
        if rows:
            print(f"Админы перенесены из настроек сервера в базу: {len(rows)}")
    except Exception as e:
        print(f"Не удалось перенести админов из настроек сервера: {e}")


def all_admin_ids():
    # Супер-админ — всегда админ, даже если базы нет: это последний ключ от
    # магазина. Все остальные живут в базе и оттуда же убираются.
    ids = set(SUPER_ADMIN_IDS)
    for city_ids in _staff_by_city().values():
        ids |= city_ids
    return ids


def is_admin(user_id):
    return user_id in all_admin_ids()


def admin_role(user_id):
    """'dev' | 'owner' | 'seller' | '' — на этом стоят все проверки прав."""
    if is_dev(user_id):
        return "dev"
    if is_owner(user_id):
        return "owner"
    return "seller" if is_admin(user_id) else ""


def admin_city(user_id):
    """Точка продавца или '' — все точки.

    Раньше город в «Доступе» выглядел как разграничение, а на деле решал одно:
    кому падает уведомление о заказе. Теперь город — это граница по товарам,
    складу и заказам. Пустой город НЕ повышает до владельца: продавец всех
    точек остаётся продавцом, ассортимент и настройки ему всё равно закрыты.
    """
    if user_id in SUPER_ADMIN_IDS:
        return ""
    staff = _staff_by_city()
    if user_id in staff.get("", set()):
        return ""
    for city, ids in staff.items():
        if city and user_id in ids:
            return city
    return ""      # админ есть, а строки нет (гонка кэша) — не отбираем доступ молча


def admins_for_city(city):
    """Кому отправлять заказ этого города. Если продавца города нет — тем, у кого
    полный доступ, а если и таких нет — владельцу: заказ обязан кому-то прийти,
    иначе его просто никто не увидит."""
    staff = _staff_by_city()
    return (set(staff.get(city) or set())
            or set(staff.get("") or set())
            or set(SUPER_ADMIN_IDS))


# --- Реквизиты оплаты (МЕНЯЙТЕ ПОД СЕБЯ). Обычный текст — его показывает приложение. ---
PAYMENT_INFO = (
    "Карта: 0000 0000 0000 0000 (Иван И.)\n"
    "или по номеру: +375 00 000-00-00"
)

# Через сколько минут продавец обычно подтверждает (для доверия клиента).
CONFIRM_MINUTES = 15
