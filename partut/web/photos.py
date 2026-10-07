"""
photos.py — пропуска к картинкам и выбор нужного размера.

Фото товара публичны, а фото чека — нет: на нём видны имя плательщика и сумма.
Отдавать их по одному адресу без разбора нельзя, поэтому у непубличной картинки
есть короткий пропуск, который выдаётся только тому, кому этот заказ и так
показывают.

И сохранение картинки: фото товаров, моделей, призов и черновиков хранит
Телеграм — сервер шлёт снимок в чат человеку и берёт file_id. Почему Телеграм
не принял снимок, здесь же переводится в слова (сохранить_фото).

Знает config, db и tgsend; про приложение и ручки — ничего.
"""

import hashlib
import hmac
import time

import requests
from telebot import apihelper

from partut import db
from partut.config import BOT_TOKEN
from partut.integrations import tgsend

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


# ---------- Почему Телеграм не принял фото (В-4, проход 6.10) ----------
# Раньше на любой отказ отвечали одно: «Телеграм не принял этот файл.
# Попробуйте другой снимок». И при обрыве связи, и когда Телеграм просил
# подождать, и когда бот не мог написать продавцу (тот не нажимал «Старт»).
# Другой снимок в этих случаях не помогает — помогает повтор или «Старт», и
# человек ходил по кругу, перебирая годные фотографии.
ОТКАЗЫ_ФОТО = {
    # код: (HTTP-статус, что сказать)
    "tg_unreachable": (503, "Нет связи с Телеграмом — фото не сохранилось. Попробуйте ещё раз."),
    "tg_down": (503, "Телеграм сейчас не отвечает — фото не сохранилось. Попробуйте ещё раз через минуту."),
    "tg_busy": (503, "Телеграм просит подождать{пауза} — фото не сохранилось. Попробуйте ещё раз чуть позже."),
    "bot_blocked": (409, "Бот не может написать вам в Телеграм, а фото сохраняются через него. "
                         "Откройте чат с ботом, нажмите «Старт» и повторите."),
    "too_large": (400, "Снимок слишком большой для Телеграма — уменьшите его (до 10 МБ) и повторите."),
    "bad_photo": (400, "Телеграм не принял этот снимок — так бывает с битым файлом или необычным форматом. "
                       "Выберите другое фото — обычный jpg или png из галереи."),
    "send_failed": (502, "Фото не сохранилось: Телеграм ответил непонятно. Попробуйте ещё раз; "
                         "если повторится — другой снимок."),
}
# Временные отказы: тот же снимок пройдёт, если повторить.
ВРЕМЕННЫЕ = ("tg_unreachable", "tg_down", "tg_busy", "send_failed")


class ФотоНеПринято(Exception):
    """Телеграм не принял снимок: code — для экрана, message — для человека."""

    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def ответ(self):
        """Тело отказа для ручки: retry — поможет ли повтор с тем же снимком."""
        return {"ok": False, "error": self.code, "message": self.message, "retry": self.code in ВРЕМЕННЫЕ}


def вид_отказа(e):
    """Исключение отправки в Телеграм → (код из ОТКАЗЫ_ФОТО, пауза в секундах или None)."""
    if isinstance(e, apihelper.ApiTelegramException):
        код = int(e.error_code or 0)
        текст = str(e.description or "").lower()
        if код == 429:
            пауза = ((e.result_json or {}).get("parameters") or {}).get("retry_after")
            return "tg_busy", пауза
        # 403 — бот заблокирован или человек ни разу не нажимал «Старт»; «chat
        # not found» приходит с 400, но означает то же самое: писать некуда.
        if код == 403 or any(x in текст for x in ("blocked", "initiate", "chat not found", "deactivated")):
            return "bot_blocked", None
        if код == 413 or "too large" in текст or "too big" in текст:
            return "too_large", None
        if код == 400:                  # остальное с 400 — про сам файл: размеры, формат, битые данные
            return "bad_photo", None
        if код >= 500:
            return "tg_down", None
        return "send_failed", None
    if isinstance(e, apihelper.ApiHTTPException):     # ответ не JSON: шлюз, балансировщик
        статус = int(getattr(getattr(e, "result", None), "status_code", 0) or 0)
        if статус == 413:
            return "too_large", None
        if статус == 429:
            return "tg_busy", None
        if статус >= 500:
            return "tg_down", None
        return "send_failed", None
    if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return "tg_unreachable", None
    return "send_failed", None


def сохранить_фото(chat_id, данные, подпись):
    """Отправить снимок в чат человеку ради file_id → (file_id, thumb_id).

    Не принял — ФотоНеПринято с причиной словами (ОТКАЗЫ_ФОТО)."""
    try:
        msg = tgsend.tg.send_photo(chat_id, данные, caption=подпись, disable_notification=True)
    except Exception as e:
        код, пауза = вид_отказа(e)
        статус, текст = ОТКАЗЫ_ФОТО[код]
        print(f"Фото не сохранилось ({код}): {e}", flush=True)
        raise ФотоНеПринято(код, текст.format(пауза=f" {int(пауза)} с" if пауза else ""), статус) from e
    return _pick_photo_sizes(msg.photo)
