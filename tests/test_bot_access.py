"""Бот в чате: кому что позволено.

Магазин ведут из приложения, но в чате остался быстрый путь — «⚡ Цена и
остаток». Приложение проверяет границы точек на каждом действии, а чат про них
когда-то не знал вовсе, и продавец Турова правил цены Минска. Здесь эта
граница и проверяется — вместе с тем, что покупателю в админку не попасть.

Отдельно проверяется журнал: правка из чата — такое же действие, как из
приложения, и обязана в нём отражаться. Иначе достаточно открыть бота, чтобы
менять цены без следа.
"""
import types

from _common import db, Checker, SENT, reset_sent
from partut.bot import handlers as botmod
from partut import config

OWNER, SELLER, BUYER = 8601, 8602, 8603


def _msg(uid, text):
    return types.SimpleNamespace(
        text=text, content_type="text",
        from_user=types.SimpleNamespace(id=uid, username=f"u{uid}", first_name="Т"),
        chat=types.SimpleNamespace(id=uid))


def _call(uid, data):
    return types.SimpleNamespace(
        data=data, id="c1",
        from_user=types.SimpleNamespace(id=uid, username=f"u{uid}"),
        message=types.SimpleNamespace(chat=types.SimpleNamespace(id=uid), message_id=1))


def _said():
    return " | ".join(str(s[1]) for s in SENT)


def run():
    old_super = config.SUPER_ADMIN_IDS
    config.SUPER_ADMIN_IDS = old_super | {OWNER}
    db.add_staff(SELLER, "Туров", "продавец Турова")
    config.refresh_staff()

    # Кнопки бота в тестах никуда не летят — подменяем то, чего нет в общем стенде.
    orig = (botmod.bot.answer_callback_query, botmod.bot.edit_message_text,
            botmod.bot.edit_message_reply_markup)
    botmod.bot.answer_callback_query = lambda *a, **k: SENT.append(
        ("cb", (a[1] if len(a) > 1 else k.get("text", "")), None))
    botmod.bot.edit_message_text = lambda *a, **k: SENT.append(("edit", str(a[0]), None))
    botmod.bot.edit_message_reply_markup = lambda *a, **k: None

    alien = db.add_product("Минск", "disposable", "Минский", 30.0, 5)
    mine = db.add_product("Туров", "disposable", "Туровский", 32.0, 4)
    try:
        c = Checker("Команда /admin")
        reset_sent(); botmod.on_admin(_msg(BUYER, "/admin"))
        c("покупателю отказано", "только для админов" in _said())
        reset_sent(); botmod.on_admin(_msg(SELLER, "/admin"))
        c("продавцу админка открыта", "Админка" in _said())
        reset_sent(); botmod.on_admin(_msg(OWNER, "/admin"))
        c("владельцу тоже", "Админка" in _said())

        c2 = Checker("Чужая точка в чате")
        reset_sent(); botmod.on_button(_call(SELLER, f"admcard:{alien}"))
        c2("карточку чужого товара не открыть", "другой точки" in _said())
        reset_sent(); botmod.on_button(_call(SELLER, f"admset:price:{alien}"))
        c2("цену чужого товара не сменить", "другой точки" in _said())
        c2("и ввод не запрошен", botmod.admin_state.get(SELLER) is None)
        reset_sent(); botmod.on_button(_call(SELLER, f"admdelyes:{alien}"))
        c2("чужой товар не удалить", "другой точки" in _said())
        c2("товар чужой точки на месте", db.get_product(alien) is not None)
        c2("и цена его не менялась", db.get_product(alien)["price"] == 30.0)

        c3 = Checker("Своя точка в чате")
        reset_sent(); botmod.on_button(_call(SELLER, f"admcard:{mine}"))
        c3("карточка своего товара открывается", bool(SENT))
        reset_sent(); botmod.on_button(_call(SELLER, f"admset:price:{mine}"))
        state = botmod.admin_state.get(SELLER)
        c3("бот ждёт новую цену", bool(state) and state.get("field") == "price")

        # Правка ценой из чата обязана попасть в журнал.
        before = len(db.list_admin_log(200))
        botmod.handle_admin_input(SELLER, SELLER, "44")
        c3("цена изменилась", db.get_product(mine)["price"] == 44.0)
        after = db.list_admin_log(200)
        c3("правка из чата записана в журнал", len(after) > before)
        c3("и видно, кто правил", any(int(r["admin_id"]) == SELLER for r in after[:3]))

        c4 = Checker("Кнопки от покупателя")
        reset_sent(); botmod.on_button(_call(BUYER, f"admcard:{mine}"))
        c4("покупателю карточка не открывается", "Туровский" not in _said())
        reset_sent(); botmod.on_button(_call(BUYER, f"admdelyes:{mine}"))
        c4("и удалить он не может", db.get_product(mine) is not None)

        c5 = Checker("Копия базы")
        reset_sent(); botmod.cmd_backup(_msg(BUYER, "/backup"))
        c5("покупателю копия не отдаётся", not SENT)
        reset_sent(); botmod.cmd_backup(_msg(SELLER, "/backup"))
        c5("продавцу тоже нет", not SENT)
        reset_sent(); botmod.cmd_backup(_msg(OWNER, "/backup"))
        c5("владельцу — да", "копи" in _said().lower())
    finally:
        (botmod.bot.answer_callback_query, botmod.bot.edit_message_text,
         botmod.bot.edit_message_reply_markup) = orig
        botmod.admin_state.pop(SELLER, None)
        db.remove_staff(SELLER)
        config.SUPER_ADMIN_IDS = old_super
        config.refresh_staff()
        conn = db.connect(); cur = conn.cursor()
        cur.execute("DELETE FROM products")
        conn.commit(); conn.close()

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
