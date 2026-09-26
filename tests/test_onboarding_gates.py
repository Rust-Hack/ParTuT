"""Гейты перед входом в магазин: город запоминается, подписка на канал
проверяется заново при каждом открытии.

Раньше выбор точки нигде не хранился — фронтенд при каждом открытии заново
брал первую точку по сортировке, и человек, выбравший вчера «Минск», сегодня
снова видел то, что попало первым. Проверки подписки не было вовсе.
"""
from _common import db, client, server, Checker, as_user

from partut.integrations import tgsend

BUYER = 9001


def run():
    c = Checker("Город и подписка при входе")

    as_user(BUYER)
    db.set_age_ok(BUYER)

    me = client.post("/api/me", json={"initData": "x"}).get_json()
    c("новый покупатель без сохранённого города", me["city"] == "")

    # Мусор/несуществующая точка — сервер отказывает, а не заводит что попало.
    r = client.post("/api/set-city", json={"initData": "x", "city": "Нигдевск"})
    c("несуществующий город отклонён", r.status_code == 400 and r.get_json()["ok"] is False)
    r = client.post("/api/set-city", json={"initData": "x", "city": [1, 2]})
    c("мусор в поле не роняет ручку", r.status_code == 400 and r.get_json()["ok"] is False)

    locs = db.location_names()
    c("есть хотя бы одна точка для проверки", len(locs) > 0)
    chosen = locs[0]
    r = client.post("/api/set-city", json={"initData": "x", "city": chosen})
    c("существующий город принят", r.get_json()["ok"] is True)

    me2 = client.post("/api/me", json={"initData": "x"}).get_json()
    c("город запомнился до следующего открытия", me2["city"] == chosen)

    # --- Подписка выключена (SUBSCRIBE_CHANNEL пуст) — не мешает никому ---
    me3 = client.post("/api/me", json={"initData": "x"}).get_json()
    c("без настроенного канала — подписан всегда", me3["subscribed"] is True)
    c("и ссылки на канал нет", me3["subscribe_channel"] == "")

    # --- Подписка включена: спрашиваем Telegram при КАЖДОМ /api/me ---
    старый_канал, старая_ссылка = server.SUBSCRIBE_CHANNEL, server.SUBSCRIBE_CHANNEL_LINK
    старая_проверка = tgsend.is_subscribed
    try:
        server.SUBSCRIBE_CHANNEL = "test_channel"
        server.SUBSCRIBE_CHANNEL_LINK = "https://t.me/test_channel"

        tgsend.is_subscribed = lambda uid: False
        me4 = client.post("/api/me", json={"initData": "x"}).get_json()
        c("не подписан — приложение это видит", me4["subscribed"] is False)
        c("ссылка на канал приходит", me4["subscribe_channel"] == "https://t.me/test_channel")

        tgsend.is_subscribed = lambda uid: True
        me5 = client.post("/api/me", json={"initData": "x"}).get_json()
        c("подписался — на следующий же запрос это видно", me5["subscribed"] is True)

        # Каждый раз заново, а не однажды по факту регистрации.
        tgsend.is_subscribed = lambda uid: False
        me6 = client.post("/api/me", json={"initData": "x"}).get_json()
        c("отписался — снова не пущен", me6["subscribed"] is False)
    finally:
        server.SUBSCRIBE_CHANNEL, server.SUBSCRIBE_CHANNEL_LINK = старый_канал, старая_ссылка
        tgsend.is_subscribed = старая_проверка

    return c.fails


def run_проверка_без_канала():
    """is_subscribed сама по себе: пустой SUBSCRIBE_CHANNEL значит «не спрашивать»."""
    c = Checker("tgsend.is_subscribed без настроенного канала")
    c("пустой канал — всегда подписан", tgsend.is_subscribed(BUYER) is True)
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if (run() or run_проверка_без_канала()) else 0)
