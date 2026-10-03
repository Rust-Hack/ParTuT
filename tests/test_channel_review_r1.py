"""Посты для канала — приёмка a64f5d5 (3.10.2026): CH-02-R1, CH-05, CH-01-R1, CH-03-R1.

Всё — настоящими нажатиями кнопок в боте (on_button), как в приёмке, а не
прямым вызовом публикации. Telegram подменён: «отказ» — однозначный отказ
(403), «обрыв» — сообщение ушло, а ответ потерян.
"""
import datetime
import types as _t

from _common import db, client, Checker, as_admin, SENT, reset_sent

from telebot import apihelper

from partut import cache, channel, config
from partut.bot import handlers as botmod
from partut.integrations import tgsend


def _отказ():
    return apihelper.ApiTelegramException("sendMessage", None, {"error_code": 403,
                                          "description": "Forbidden: bot is not a member of the channel chat"})


class Бот:
    """Подмена методов bot, которыми пользуется обработчик кнопок."""

    def __init__(self):
        self.канал, self.клавиатуры, self.режим = [], [], None
        self._н = 500

    def send_message(self, chat_id, text, **kw):
        if str(chat_id).startswith("@"):
            if self.режим == "отказ":
                raise _отказ()
            self._н += 1
            self.канал.append(text)
            if self.режим == "обрыв":
                raise ConnectionError("Read timed out")
            return _t.SimpleNamespace(message_id=self._н)
        SENT.append((chat_id, text, kw.get("reply_markup")))
        return _t.SimpleNamespace(message_id=1)

    def edit_message_reply_markup(self, *a, **k):
        self.клавиатуры.append(k.get("reply_markup"))

    def answer_callback_query(self, *a, **k):
        SENT.append(("cb", a[1] if len(a) > 1 else k.get("text", ""), None))


def _кнопка(kb):
    return kb.keyboard[0][0].callback_data if kb is not None else None


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("channel_posts", "stock_moves", "product_variants", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("UPDATE locations SET closed = 0, closed_until = NULL, closed_note = NULL, closed_by = NULL")
    conn.commit(); conn.close()
    cache.bust()


def _пауза(минут=60):
    client.post("/api/admin/pause", json={"initData": "x", "city": "Минск", "minutes": минут, "note": "обед"})
    tgsend.дождаться_фона()
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT id FROM channel_posts WHERE kind = 'pause' ORDER BY id DESC LIMIT 1")
    pid = cur.fetchone()["id"]
    conn.close()
    return pid


def run():
    c = Checker("Канал: приёмка a64f5d5")
    было_канал, было_бот = config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME
    config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME = "partut_test", "partut_bot"
    б = Бот()
    имена = ("send_message", "edit_message_reply_markup", "answer_callback_query", "edit_message_text")
    прежние = {и: getattr(botmod.bot, и) for и in имена}
    прежняя_правка = tgsend.tg.edit_message_text
    for и in имена[:3]:
        setattr(botmod.bot, и, getattr(б, и))
    правки, правка_ломается = [], {"да": False}

    def правка(text, chat_id=None, message_id=None, **kw):
        if правка_ломается["да"]:
            raise ConnectionError("Read timed out")
        правки.append(text)
    botmod.bot.edit_message_text = tgsend.tg.edit_message_text = правка
    владелец = sorted(config.SUPER_ADMIN_IDS)[0]

    def нажать(data):
        reset_sent(); б.клавиатуры.clear()
        msg = _t.SimpleNamespace(chat=_t.SimpleNamespace(id=владелец), message_id=7)
        botmod.on_button(_t.SimpleNamespace(data=data, id="c1", from_user=_t.SimpleNamespace(id=владелец, username="o"),
                                            message=msg))
    _чисто(); as_admin()
    try:
        # ---- CH-02-R1: неизвестно → осознанный повтор → однозначный отказ ----
        пост = _пауза()
        б.режим = "обрыв"
        нажать(f"chpost:{пост}:1")
        c("ответ потерян — «неизвестно», под сообщением осознанный повтор",
          db.get_channel_post(пост)["status"] == "unknown" and bool(б.клавиатуры)
          and _кнопка(б.клавиатуры[-1]) == f"chforce:{пост}:1")
        б.режим = "отказ"
        нажать(f"chforce:{пост}:1")
        c("повтор, Telegram отказал — черновик снова ждёт решения",
          db.get_channel_post(пост)["status"] == "offered" and len(б.канал) == 1)
        c("…и под сообщением теперь «📣 В канал», а не мёртвый повтор",
          bool(б.клавиатуры) and _кнопка(б.клавиатуры[-1]) == f"chpost:{пост}:1")
        б.режим = None                                     # права боту выдали
        нажать(_кнопка(б.клавиатуры[-1]) if б.клавиатуры else f"chpost:{пост}:1")
        c("нажал эту кнопку — опубликовано, один новый пост",
          db.get_channel_post(пост)["status"] == "posted" and len(б.канал) == 2)

        # ---- CH-05: выключенное «Поступление» не уходит никакой кнопкой ----
        mid = db.add_model("liquid", "ANNIMA LOVE", "ANNIMA LOVE", "", {}, ["Кислая вишня"])
        жижа = db.create_point_product(mid, "Минск", 25.0, 9.0, variants=[{"flavor": "Кислая вишня", "stock": 0}])
        channel.ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = True                # черновик подготовлен ДО выключения
        channel.предложить_поступление([(жижа, "Кислая вишня", 5)], tg=б)
        channel.предложить_поступление([(жижа, "Кислая вишня", 2)], tg=б)
        channel.ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = False
        conn = db.connect(); cur = conn.cursor()
        cur.execute("SELECT id FROM channel_posts WHERE kind = 'supply' ORDER BY id DESC LIMIT 1")
        supply = cur.fetchone()["id"]
        conn.close()
        v = db.get_channel_post(supply)["payload"]["v"]
        было = len(б.канал)
        нажать(f"chpost:{supply}:{v}")
        c("кнопка с правильной версией под старым «Поступлением» — не публикует, черновик снят",
          len(б.канал) == было and db.get_channel_post(supply)["status"] == "skipped")
        # Тот же запрет на осознанном повторе после неизвестного исхода.
        channel.ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = True
        channel.предложить_поступление([(жижа, "Кислая вишня", 1)], tg=б)
        channel.ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = False
        conn = db.connect(); cur = conn.cursor()
        cur.execute("SELECT id FROM channel_posts WHERE kind = 'supply' ORDER BY id DESC LIMIT 1")
        второй = cur.fetchone()["id"]
        cur.execute(db._q("UPDATE channel_posts SET status = 'unknown' WHERE id = %s"), (второй,))
        conn.commit(); conn.close()
        нажать(f"chforce:{второй}:1")
        c("и осознанный повтор — тоже нет", len(б.канал) == было and db.get_channel_post(второй)["status"] == "skipped")

        # ---- CH-01-R1: кнопка старого образца (без версии) ----
        db.open_location("Минск")
        tgsend.дождаться_фона()
        старый = _пауза(90)
        нажать(f"chpost:{старый}")
        c("кнопка без версии — в канал ничего", len(б.канал) == было and db.get_channel_post(старый)["status"] == "offered")
        заново = [s for s in SENT if s[0] == владелец and "Пост для канала" in str(s[1])]
        c("…старая кнопка снята, пост прислан заново с кнопкой нынешней версии",
          б.клавиатуры == [None] and len(заново) == 1 and _кнопка(заново[0][2]) == f"chpost:{старый}:1"
          and "⏸ Точка «Минск» закрыта до" in заново[0][1])
        нажать(_кнопка(заново[0][2]))
        c("кнопка из нового сообщения публикует", db.get_channel_post(старый)["status"] == "posted" and len(б.канал) == было + 1)

        # ---- CH-03-R1: «снова открыта с» — время события, не повтора ----
        правка_ломается["да"] = True
        conn = db.connect(); cur = conn.cursor()
        назначено = (db.shop_now() - datetime.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
        cur.execute(db._q("UPDATE locations SET closed_until = %s WHERE name = 'Минск'"), (назначено,))
        conn.commit(); conn.close()
        botmod._reopen_paused_points()                     # обход бота дошёл через 15 минут после срока
        c("открылась по времени, правка не удалась — пост в работе", db.get_channel_post(старый)["status"] == "posted")
        правка_ломается["да"] = False
        прежнее_сейчас = db._now_str
        db._now_str = lambda: (db.shop_now() + datetime.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
        try:
            botmod._finish_channel_reopens()               # повтор ещё через 15 минут
        finally:
            db._now_str = прежнее_сейчас
        c(f"повтор написал время открытия {назначено[11:]}, а не время повтора: {правки[-1][-40:] if правки else None}",
          правки and f"снова открыта с {назначено[11:]}." in правки[-1] and db.get_channel_post(старый)["status"] == "reopened")

        # Открыли вручную, правка не удалась; повтор позже — время ручного открытия.
        ручной = _пауза(60)
        нажать(f"chpost:{ручной}:1")
        правка_ломается["да"] = True
        открыли = db._now_str()
        client.post("/api/admin/pause/open", json={"initData": "x", "city": "Минск"})
        tgsend.дождаться_фона()
        правка_ломается["да"] = False
        db._now_str = lambda: (db.shop_now() + datetime.timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M")
        try:
            botmod._finish_channel_reopens()
        finally:
            db._now_str = прежнее_сейчас
        c(f"открыли вручную в {открыли[11:]} — столько и написано после повтора",
          f"снова открыта с {открыли[11:]}." in правки[-1] and db.get_channel_post(ручной)["status"] == "reopened")
    finally:
        config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME = было_канал, было_бот
        channel.ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = False
        for и, f in прежние.items():
            setattr(botmod.bot, и, f)
        tgsend.tg.edit_message_text = прежняя_правка
        _чисто()
    return c.fails
