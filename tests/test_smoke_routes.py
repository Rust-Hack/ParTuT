"""Обход всех ручек: ни одна не должна отвечать пятисоткой.

Отказ — это нормально и правильно: 401 постороннему, 403 продавцу, 400 на
кривом вводе. Пятисотка — другое: она означает, что ручка не пережила
неожиданный ввод и упала внутри, а значит человек увидел «что-то пошло не так»
там, где ему полагался внятный ответ.

Проверка идёт от четырёх лиц сразу — посторонний, покупатель, продавец,
владелец, — потому что падают обычно не сами ручки, а проверки прав на входе:
именно они первыми встречают пустое тело.

Список ручек не ведётся руками: он берётся у самого приложения. Новая ручка
попадает под проверку в тот же день, когда появилась.
"""
from _common import db, client, server, Checker, as_user, as_admin, deny_admin, real_auth

from partut import cache

# Тела, которыми стучимся: пустое, мусорное и почти правдоподобное.
ТЕЛА = [
    {"initData": "x"},
    {"initData": "x", "id": "не-число", "user_id": [], "coins": {"a": 1}, "items": "строка"},
    {},
]


def _routes():
    for rule in server.app.url_map.iter_rules():
        путь = str(rule)
        if "<" in путь or путь.startswith("/static"):
            continue          # с параметрами в пути — отдельная история
        for метод in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield метод, путь


def _sweep():
    """Возвращает список (метод, путь, код, кусок ответа) для всех пятисоток."""
    плохие = []
    for метод, путь in _routes():
        for тело in (ТЕЛА if метод == "POST" else [None]):
            try:
                r = (client.post(путь, json=тело) if метод == "POST"
                     else client.open(путь, method=метод))
                if r.status_code >= 500:
                    плохие.append((метод, путь, r.status_code,
                                   r.get_data(as_text=True)[:100]))
            except Exception as e:
                плохие.append((метод, путь, "упало", f"{type(e).__name__}: {e}"[:100]))
    return плохие


def _clean():
    conn = db.connect(); cur = conn.cursor()
    for t in ("raffles", "raffle_entries", "admin_requests"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    cache.bust()


def run():
    c = Checker("Ни одна ручка не отвечает пятисоткой")
    ручек = len(list(_routes()))
    c(f"ручки найдены сами ({ручек} шт.)", ручек > 50)

    # server.py режут на модули, и маршруты регистрируются уже из разных мест.
    # Потеряться при переезде они должны громко, а не молча: забытый импорт
    # модуля — это исчезнувший экран, о котором узнают от покупателя.
    ЖДЁМ = ["/api/products", "/api/order", "/api/me", "/api/raffle", "/api/wheel",
            "/api/slot", "/api/admin/orders", "/api/admin/raffle/start",
            "/api/admin/order/compensate", "/api/photo", "/health"]
    пути = {путь for _, путь in _routes()}
    пропали = [п for п in ЖДЁМ if п not in пути]
    c("ключевые ручки на месте" + ("" if not пропали else f": нет {пропали}"), not пропали)

    лица = [
        ("посторонний", lambda: (real_auth(), deny_admin())),
        ("покупатель", lambda: (as_user(9801, "покупатель"), deny_admin())),
        ("продавец", lambda: as_admin(uid=9802, username="продавец", role="staff", city="Туров")),
        ("владелец", lambda: as_admin()),
    ]
    for имя, назначить in лица:
        назначить()
        плохие = _sweep()
        c(f"{имя}: пятисоток нет" + ("" if not плохие else f" — {плохие[:2]}"), not плохие)

    as_admin()
    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
