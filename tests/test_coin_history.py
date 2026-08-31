"""Единый журнал монет покупателя: колесо, слот, кэшбэк, рефералы —
раньше это было видно только по разным экранам порознь, если вообще видно.
"""
from _common import db, client, Checker, as_user


ПОКУПАТЕЛЬ = 8200


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM coin_log WHERE user_id = %s"), (ПОКУПАТЕЛЬ,))
    cur.execute(db._q("DELETE FROM users WHERE user_id = %s"), (ПОКУПАТЕЛЬ,))
    conn.commit(); conn.close()


def run():
    c = Checker("Журнал монет")
    _clean()
    db.ensure_user(ПОКУПАТЕЛЬ)

    db.add_coins(ПОКУПАТЕЛЬ, 300, "wheel")
    db.add_coins(ПОКУПАТЕЛЬ, -50, "order")
    db.add_coins(ПОКУПАТЕЛЬ, 120, "cashback")

    as_user(ПОКУПАТЕЛЬ, "покупатель")
    r = client.post("/api/coins/history", json={"initData": "x"})
    d = r.get_json()
    c("запрос успешен", d.get("ok") is True)
    hist = d.get("history") or []
    c("все три движения видны", len(hist) == 3)
    c("новые сверху", hist[0]["delta"] == 120 and hist[-1]["delta"] == 300)
    c("причина — по-русски, не техническим словом",
      hist[0]["reason"] == "Кэшбэк с заказов")
    c("списание видно отрицательным числом", any(h["delta"] == -50 for h in hist))

    # Чужую историю не отдаём.
    as_user(9999, "посторонний")
    r = client.post("/api/coins/history", json={"initData": "x"})
    c("у постороннего своя (пустая) история, не чужая", (r.get_json() or {}).get("history") == [])

    _clean()
    return c.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
