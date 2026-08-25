"""Правила для покупателя берут НАСТОЯЩИЕ настройки магазина.

Смысл проверки один: правила не должны быть вторым текстом, который живёт своей
жизнью. Владелец меняет кэшбэк или порог бесплатной доставки — экран «Как всё
устроено» обязан поменяться вместе с ним. Правила, разошедшиеся с магазином,
хуже отсутствующих: по ним человек считает свою выгоду и приходит с претензией.
"""
from _common import db, client, Checker, as_admin

from partut import cache
from partut import config


def run():
    c = Checker("Правила покупателя")
    as_admin()

    # Ставим заведомо непохожие на умолчания числа.
    client.post("/api/admin/settings/update", json={
        "initData": "x", "coins_per_byn": 3, "wheel_step": 250,
        "referral_bonus": 77, "free_delivery_from": 45, "confirm_minutes": 25})
    cache.bust()

    r = client.get("/api/rules")
    d = r.get_json()
    c("правила отдаются всем", r.status_code == 200 and d.get("coin_value"))
    c("кэшбэк из настроек", float(d["coins_per_byn"]) == 3.0)
    c("шаг колеса из настроек", float(d["wheel_step"]) == 250.0)
    c("бонус за друга из настроек", int(d["referral_bonus"]) == 77)
    c("порог бесплатной доставки из настроек", float(d["free_delivery_from"]) == 45.0)
    c("срок подтверждения из настроек", int(d["confirm_minutes"]) == 25)
    c("срок на чек — из кода магазина", int(d["unpaid_hours"]) == config.CANCEL_UNPAID_HOURS)

    ступени = d.get("ref_tiers") or []
    c("ступени процента отданы", len(ступени) == len(db.REFERRAL_TIERS))
    c("ступени идут снизу вверх", [т["from"] for т in ступени] == sorted(т["from"] for т in ступени))
    c("самая нижняя начинается с нуля", ступени[0]["from"] == 0)

    # Правила открыты БЕЗ входа: экран, который видно только вошедшему,
    # своей задачи не выполняет.
    c("вход не требуется", "initData" not in r.get_data(as_text=True))

    # Меняем настройку ещё раз — ответ обязан поменяться, а не остаться в кэше.
    client.post("/api/admin/settings/update", json={"initData": "x", "coins_per_byn": 1})
    d2 = client.get("/api/rules").get_json()
    c("правка настроек сразу видна в правилах", float(d2["coins_per_byn"]) == 1.0)

    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
