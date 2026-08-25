"""
photos.py — пропуска к картинкам и выбор нужного размера.

Фото товара публичны, а фото чека — нет: на нём видны имя плательщика и сумма.
Отдавать их по одному адресу без разбора нельзя, поэтому у непубличной картинки
есть короткий пропуск, который выдаётся только тому, кому этот заказ и так
показывают.

Листовой модуль: знает config и db, про приложение и ручки — ничего.
"""

import hashlib
import hmac
import time

from partut import db
from partut.config import BOT_TOKEN

RECEIPT_TOKEN_TTL = 6 * 3600       # ссылка на чек живёт полдня, не вечно
GRID_PHOTO_MIN_WIDTH = 480     # карточка каталога ~190px, но экраны телефонов 2-3x

КАРТИНКИ = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")


def это_картинка(file):
    """Похоже ли присланное на изображение — по типу и по расширению.

    Проверять надо ДО того, как файл прочитан в память и отправлен в Телеграм.
    Раньше туда улетало что угодно — pdf, архив, — Телеграм отказывался, а
    админ видел «Не удалось» без единого намёка, что дело в самом файле.

    Заголовок присылает браузер, и подделать его ничего не стоит. Это не
    защита, а вежливость: настоящую проверку делает Телеграм, а мы избавляем
    человека от непонятной ошибки и не гоняем зря мегабайты.
    """
    тип = (getattr(file, "mimetype", "") or "").lower()
    имя = (getattr(file, "filename", "") or "").lower()
    return тип.startswith("image/") or any(имя.endswith(x) for x in КАРТИНКИ)


def photo_token(file_id):
    """Короткий пропуск к картинке чека. Выдаём его тому, кто уже доказал право
    на заказ; в самой ссылке пропуск ничего не раскрывает и через полдня гаснет.

    Класть в адрес картинки строку входа Telegram нельзя: адреса попадают в
    логи сервера и историю браузера, а она — ключ от аккаунта на сутки."""
    exp = int(time.time()) + RECEIPT_TOKEN_TTL
    sig = hmac.new(BOT_TOKEN.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def _token_ok(file_id, token):
    try:
        exp_s, sig = (token or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    want = hmac.new(BOT_TOKEN.encode(), f"{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, sig)


def _may_see_photo(file_id, token=""):
    """Картинки товаров открыты всем — это витрина. Всё остальное здесь —
    чеки об оплате: к ним нужен пропуск.

    Раньше по этой ссылке чек забирал кто угодно: адрес вида
    /api/photo?file_id=… ничем не защищён, а живёт он вечно — достаточно,
    чтобы он попал в чужой лог, историю браузера или на скриншот.
    """
    try:
        if db.is_product_photo(file_id):
            return True
        owner = db.receipt_owner(file_id)
    except Exception as e:
        print(f"Не смог проверить права на фото {file_id}: {e}")
        return True                     # база отвечает плохо — витрина важнее
    if owner is None:
        return True                     # ни товар, ни чек: старые картинки из чата
    return _token_ok(file_id, token)


def _pick_photo_sizes(sizes):
    """Из набора копий, который вернул Telegram, берём две: большую и для сетки.

    Telegram сам хранит одну картинку в нескольких размерах (обычно 90/320/800/1280).
    Раньше мы всегда брали самую большую — и гоняли её в каталог, где она
    показывается в ~190 пикселей шириной. Теперь для сетки берём копию поменьше,
    а полноразмерную оставляем для карточки товара. Своего ресайза не нужно."""
    sizes = list(sizes or [])
    if not sizes:
        return None, None
    ordered = sorted(sizes, key=lambda s: getattr(s, "width", 0) or 0)
    full = ordered[-1]
    grid = next((s for s in ordered if (getattr(s, "width", 0) or 0) >= GRID_PHOTO_MIN_WIDTH), full)
    return full.file_id, grid.file_id
