"""
shopinfo.py — настройки магазина, которые читаются на каждом шагу.

Реквизиты оплаты, срок подтверждения, порог бесплатной доставки, счётчик
выданных заказов. Все они лежат в базе, меняются раз в год и читаются на
каждом оформлении — поэтому каждая закэширована, и у каждой есть значение по
умолчанию из config.

Умолчание здесь не мелочь: пустое поле владелец однажды сохранит, и без
отката на config магазин показал бы покупателю пустые реквизиты. На этом уже
спотыкались, когда экран настроек стал собирать всё одним запросом.

Заодно тут живут постоянные величины денег — цена монеты и порог «мало на
складе»: они нужны и заказам, и статистике, и админке, а лежали в server.py,
из-за чего за одним числом тянулось всё приложение.
"""

from partut import cache
from partut import db
from partut.config import CONFIRM_MINUTES, PAYMENT_INFO

REFERRAL_BONUS = 50        # vapecoins пригласившему за нового друга
COINS_PER_BYN = 1          # vapecoins клиенту за каждый Br выданного заказа
COIN_VALUE = 0.01          # сколько стоит 1 монета при списании (100 монет = 1 Br)
LOW_STOCK = 3              # с этого остатка товар считается «заканчивается» (везде одинаково)


def _payment_info():
    """Реквизиты оплаты: из настроек магазина, иначе — значение из config.
    Кэшируем: настройки меняются раз в год, а читались на каждом оформлении заказа."""
    cached = cache.get("settings:payment_info")
    if cached is None:
        cached = cache.put("settings:payment_info", db.get_setting("payment_info", PAYMENT_INFO), 300)
    return cached


# Ниже этого числа хвастаться нечем: «выполнено 3 заказа» отпугивает сильнее,
# чем молчание. Показываем счётчик, только когда он работает на доверие.
ORDERS_DONE_MIN = 15


def _orders_done():
    cached = cache.get("orders_done")
    if cached is None:
        try:
            n = db.issued_orders_count()
        except Exception:
            n = 0
        cached = cache.put("orders_done", n if n >= ORDERS_DONE_MIN else 0, 300)
    return cached


def _free_delivery_from():
    """С какой суммы доставка бесплатна. 0 = порога нет.
    Кэшируем: читается на каждом оформлении, а меняется раз в год."""
    cached = cache.get("settings:free_delivery_from")
    if cached is None:
        try:
            val = float(db.get_setting("free_delivery_from", 0) or 0)
        except (TypeError, ValueError):
            val = 0.0
        cached = cache.put("settings:free_delivery_from", max(0.0, val), 300)
    return cached


def _confirm_minutes():
    """Через сколько минут продавец подтверждает: из настроек, иначе — из config."""
    cached = cache.get("settings:confirm_minutes")
    if cached is None:
        try:
            val = int(db.get_setting("confirm_minutes", CONFIRM_MINUTES))
        except (TypeError, ValueError):
            val = CONFIRM_MINUTES
        cached = cache.put("settings:confirm_minutes", val, 300)
    return cached
