"""Выдача доступа продавцам: кто может раздавать права и что нельзя отнять.

Главное, что проверяем: владельца (супер-админа) нельзя лишить доступа из
приложения, а обычный админ не может выписать права ни себе, ни другому.
"""
from _common import db, client, Checker, as_user


SUPER = 716030279          # зашит в config.SUPER_ADMIN_IDS
PLAIN = 555001             # обычный админ
NOBODY = 555002            # посторонний
SELLER = 555003            # кому выдаём доступ


def _as(uid):
    """Запросы идут «от» этого пользователя (в т.ч. для супер-админ-проверок)."""
    as_user(uid)


def run():
    import config
    c = Checker("Доступ: выдать и забрать")

    conn = db.connect(); conn.cursor().execute("DELETE FROM staff"); conn.commit(); conn.close()
    config.refresh_staff()
    # Заглушку отправки НЕ трогаем: она общая для всех тестов и копит SENT.
    # Подменишь её здесь — следующие модули перестанут видеть свои сообщения.

    # --- Кто вообще может сюда попасть ---
    _as(NOBODY)
    c("посторонний не видит список", client.post("/api/admin/staff", json={"initData": "x"}).status_code == 403)
    c("посторонний не может выдать доступ",
      client.post("/api/admin/staff/add", json={"initData": "x", "user_id": SELLER}).status_code == 403)

    _as(PLAIN)
    db.add_staff(PLAIN, "", "обычный админ", SUPER)     # сделаем его админом напрямую
    config.refresh_staff()
    c("обычный админ теперь админ", config.is_admin(PLAIN))
    c("но раздавать доступ не может",
      client.post("/api/admin/staff/add", json={"initData": "x", "user_id": 999777}).status_code == 403)
    c("и список ему не отдаём", client.post("/api/admin/staff", json={"initData": "x"}).status_code == 403)

    # --- Владелец выдаёт доступ ---
    _as(SUPER)
    c("до выдачи продавец не админ", not config.is_admin(SELLER))
    r = client.post("/api/admin/staff/add", json={"initData": "x", "user_id": SELLER, "city": "Минск", "note": "@seller"})
    c("владелец выдал доступ", (r.get_json() or {}).get("ok"))
    c("права действуют СРАЗУ, без ожидания кэша", config.is_admin(SELLER))
    c("заказы Минска идут ему", SELLER in config.admins_for_city("Минск"))
    c("заказы другого города — нет", SELLER not in config.admins_for_city("Туров"))

    lst = client.post("/api/admin/staff", json={"initData": "x"}).get_json()["staff"]
    mine = next((s for s in lst if s["user_id"] == SELLER), None)
    c("продавец есть в списке", mine is not None)
    c("подпись сохранена", mine and mine["note"] == "@seller")
    c("его можно убрать", mine and mine["can_remove"])

    env_row = next((s for s in lst if s["user_id"] == SUPER), None)
    c("владелец виден в списке", env_row is not None)
    c("владельца убрать нельзя", env_row and not env_row["can_remove"])
    c("владелец помечен", env_row and env_row["is_super"])

    # --- Что нельзя отнять ---
    r = client.post("/api/admin/staff/remove", json={"initData": "x", "user_id": SUPER})
    c("снять владельца — отказ", r.status_code == 400 and r.get_json()["error"] == "super_protected")
    c("владелец остался админом", config.is_admin(SUPER))

    db.add_staff(SUPER, "", "", SUPER)                  # даже если он попал в таблицу
    config.refresh_staff()
    r = client.post("/api/admin/staff/remove", json={"initData": "x", "user_id": SUPER})
    c("и из таблицы его не снять", r.status_code == 400)
    c("доступ владельца цел", config.is_admin(SUPER))

    # --- Обычное снятие ---
    r = client.post("/api/admin/staff/remove", json={"initData": "x", "user_id": SELLER})
    c("продавца сняли", (r.get_json() or {}).get("ok"))
    c("права отозваны сразу", not config.is_admin(SELLER))

    # --- Мусор на входе ---
    c("не число → 400", client.post("/api/admin/staff/add", json={"initData": "x", "user_id": "абв"}).status_code == 400)
    c("пустой id → 400", client.post("/api/admin/staff/add", json={"initData": "x", "user_id": ""}).status_code == 400)
    c("отрицательный id → 400", client.post("/api/admin/staff/add", json={"initData": "x", "user_id": -5}).status_code == 400)
    c("несуществующая точка → 400",
      client.post("/api/admin/staff/add", json={"initData": "x", "user_id": 424242, "city": "Атлантида"}).status_code == 400)

    # --- Если база недоступна, владелец не должен потерять доступ ---
    real = db.staff_ids_by_city
    db.staff_ids_by_city = lambda: (_ for _ in ()).throw(RuntimeError("база упала"))
    config.refresh_staff()
    c("база упала — владелец всё ещё админ", config.is_admin(SUPER))
    db.staff_ids_by_city = real
    config.refresh_staff()

    # --- Перенос из переменных окружения ---
    # Ради этого всё и затевалось: раньше админа из окружения нельзя было убрать
    # из приложения вовсе. Теперь его переносят в базу — и он снимается как все.
    c2 = Checker("Перенос админов из настроек сервера")
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM staff"); cur.execute(_q_del()); conn.commit(); conn.close()
    config.refresh_staff()

    env_admin = next(iter(config.ADMIN_IDS - config.SUPER_ADMIN_IDS), None)
    if env_admin is None:
        env_admin = 555444
        config.ADMIN_IDS = set(config.ADMIN_IDS) | {env_admin}

    config.seed_admins_from_env()
    ids = {int(r["user_id"]) for r in db.list_staff()}
    c2("админ из окружения попал в базу", env_admin in ids)
    c2("и остался админом", config.is_admin(env_admin))

    lst = client.post("/api/admin/staff", json={"initData": "x"}).get_json()["staff"]
    row = next((s for s in lst if s["user_id"] == env_admin), None)
    c2("теперь его МОЖНО убрать", row and row["can_remove"])

    r = client.post("/api/admin/staff/remove", json={"initData": "x", "user_id": env_admin})
    c2("сняли перенесённого", (r.get_json() or {}).get("ok"))
    c2("права отозваны", not config.is_admin(env_admin))

    # Перезапуск сервиса не должен воскрешать снятых.
    config.seed_admins_from_env()
    config.refresh_staff()
    c2("после перезапуска НЕ вернулся", not config.is_admin(env_admin))

    # Заказ обязан кому-то прийти, даже когда продавцов не осталось.
    conn = db.connect(); conn.cursor().execute("DELETE FROM staff"); conn.commit(); conn.close()
    config.refresh_staff()
    c2("без продавцов заказ уходит владельцу", config.admins_for_city("Минск") == set(config.SUPER_ADMIN_IDS))
    c2("владелец админ и с пустой таблицей", config.is_admin(SUPER))

    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM staff"); cur.execute(_q_del()); conn.commit(); conn.close()
    config.refresh_staff()
    return c.fails + c2.fails


def _q_del():
    """Сбрасывает отметку о переносе, чтобы его можно было проверить заново."""
    return db._q("DELETE FROM settings WHERE key = 'staff_seeded'")


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
