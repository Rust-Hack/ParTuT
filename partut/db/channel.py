"""
partut/db/channel.py — посты для телеграм-канала магазина.

Приложение само готовит пост («Поступление», «Точка закрыта»), но в канал он
уходит только после нажатия владельца: это публикация от имени магазина.
Здесь — черновики и их судьба. Текст поста собирает partut/channel.py, а
отправляет бот (partut/bot/handlers.py, кнопки chpost/chskip).

Черновик хранит не готовый текст, а данные поста (payload, JSON): несколько
партий одной поставки — «Провести», потом «Провести оставшиеся» — ложатся
в один пост, а не в два.
"""

import datetime
import json

from partut import db

# Сколько часов незаконченный черновик поступления принимает новые строки.
# Поставку проводят частями в пределах вечера, а не суток.
ПОСТУПЛЕНИЕ_ОКНО_ЧАСОВ = 3


def _ensure_channel_tables():
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS channel_posts (
            id         {db.ID_COL},
            kind       TEXT    NOT NULL,
            city       TEXT    NOT NULL,
            payload    TEXT    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'offered',
            created_at TEXT    NOT NULL,
            message_id BIGINT,
            decided_by BIGINT
        )
    """)
    conn.commit()
    conn.close()


def _строка(r):
    d = dict(r)
    try:
        d["payload"] = json.loads(d["payload"] or "{}")
    except (TypeError, ValueError):
        d["payload"] = {}
    return d


def get_channel_post(post_id):
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("SELECT * FROM channel_posts WHERE id = %s"), (post_id,))
    r = cur.fetchone()
    conn.close()
    return _строка(r) if r else None


def offer_channel_post(kind, city, payload, merge=None):
    """Новый черновик или — если merge(старый, новый) задан и есть свежий
    непринятый черновик того же вида и точки — дополненный прежний.
    Возвращает (черновик, дополнен?)."""
    conn = db.connect()
    cur = conn.cursor()
    try:
        if merge:
            с = (db.shop_now() - datetime.timedelta(hours=ПОСТУПЛЕНИЕ_ОКНО_ЧАСОВ)).strftime("%Y-%m-%d %H:%M")
            cur.execute(db._q("SELECT * FROM channel_posts WHERE kind = %s AND city = %s AND status = 'offered' "
                              "AND created_at >= %s ORDER BY id DESC LIMIT 1"), (kind, city, с))
            было = cur.fetchone()
            if было:
                старый = _строка(было)
                итог = merge(старый["payload"], payload)
                # Новая версия — новое согласование: кнопка под прежним текстом
                # больше не публикует (приёмка CH-01).
                итог["v"] = int(старый["payload"].get("v") or 1) + 1
                cur.execute(db._q("UPDATE channel_posts SET payload = %s WHERE id = %s AND status = 'offered'"),
                            (json.dumps(итог, ensure_ascii=False), старый["id"]))
                if cur.rowcount > 0:
                    conn.commit()
                    conn.close()
                    return dict(старый, payload=итог), True
        payload = dict(payload, v=1)
        pid = db._insert_id(cur, "INSERT INTO channel_posts (kind, city, payload, status, created_at) "
                                 "VALUES (%s, %s, %s, 'offered', %s)",
                            (kind, city, json.dumps(payload, ensure_ascii=False), db._now_str()))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return get_channel_post(pid), False


def decide_channel_post(post_id, status, admin_id, message_id=None):
    """Отметить решение по черновику — только если он ещё не решён. True — отмечено
    (второе нажатие той же кнопки или второй владелец ничего не повторят)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE channel_posts SET status = %s, decided_by = %s, message_id = %s "
                      "WHERE id = %s AND status = 'offered'"), (status, admin_id, message_id, post_id))
    ок = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ок


def claim_channel_post(post_id, admin_id, from_status="offered"):
    """Занять черновик под публикацию: from_status → 'posting'. Только один
    нажавший публикует — два владельца или двойное нажатие не дадут двух постов.
    from_status='unknown' — осознанный повтор после неизвестного исхода."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE channel_posts SET status = 'posting', decided_by = %s "
                      "WHERE id = %s AND status = %s"), (admin_id, post_id, from_status))
    ок = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ок


def finish_channel_post(post_id, status, message_id=None):
    """Итог публикации занятого черновика: 'posted' (с номером сообщения в
    канале), 'offered' (Telegram отказал — можно нажать ещё раз) или
    'unknown' (ответ не дошёл — опубликован ли, неизвестно, CH-02)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE channel_posts SET status = %s, message_id = %s WHERE id = %s AND status = 'posting'"),
                (status, message_id, post_id))
    conn.commit()
    conn.close()


def posted_pause_posts():
    """Опубликованные посты «точка закрыта», ещё без «снова открыта», — все."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM channel_posts WHERE kind = 'pause' AND status = 'posted' ORDER BY id")
    rows = [_строка(r) for r in cur.fetchall()]
    conn.close()
    return rows


def mark_pause_reopened(post_id):
    """Правка «снова открыта» в канале удалась — только тогда отмечаем (CH-03)."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE channel_posts SET status = 'reopened' WHERE id = %s AND status = 'posted'"), (post_id,))
    conn.commit()
    conn.close()


def expire_pause_drafts(city):
    """Точка открылась: неопубликованные черновики «закрыта» больше не публикуются."""
    conn = db.connect()
    cur = conn.cursor()
    cur.execute(db._q("UPDATE channel_posts SET status = 'expired' WHERE kind = 'pause' AND city = %s AND status = 'offered'"),
                (city,))
    conn.commit()
    conn.close()
