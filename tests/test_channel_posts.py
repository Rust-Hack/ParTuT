"""Посты для телеграм-канала: приложение готовит, публикует владелец.

Поставка проведена или заведён новый товар — владельцу уходит готовый пост
«📦 Поступление» с кнопками «📣 В канал» / «Не надо»; вторая партия той же
поставки дописывается в тот же пост. Точку закрыли — пост «⏸ Точка закрыта»;
опубликованный, он дополняется «снова открыта», когда точка открылась, а
неопубликованный устаревает. Без нажатия владельца в канал не уходит ничего.
"""
import datetime
import types as _t

from _common import db, client, Checker, as_admin, SENT, reset_sent

from telebot import apihelper

from partut import cache, channel, config
from partut.bot import handlers as botmod
from partut.integrations import tgsend


def _отказ_телеграма():
    return apihelper.ApiTelegramException("sendMessage", None, {"error_code": 403,
                                          "description": "Forbidden: bot is not a member of the channel chat"})


class ПоддельныйТелеграм:
    """Запоминает, что ушло в канал и что в нём поправили. сломан: "отказ" —
    Telegram однозначно отказал; "обрыв" — сообщение ушло, а ответ потерян."""

    def __init__(self, сломан=None, правка_сломана=False):
        self.отправлено, self.правки, self.сломан, self.правка_сломана, self._н = [], [], сломан, правка_сломана, 100

    def send_message(self, chat_id, text, **kw):
        if self.сломан == "отказ":
            raise _отказ_телеграма()
        self._н += 1
        self.отправлено.append((chat_id, text))
        if self.сломан == "обрыв":
            raise ConnectionError("Read timed out")
        return _t.SimpleNamespace(message_id=self._н)

    def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        if self.правка_сломана:
            raise ConnectionError("Read timed out")
        self.правки.append((chat_id, message_id, text))


def _чисто():
    conn = db.connect(); cur = conn.cursor()
    for t in ("channel_posts", "stock_moves", "product_variants", "products", "models"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("UPDATE locations SET closed = 0, closed_until = NULL, closed_note = NULL, closed_by = NULL")
    conn.commit(); conn.close()
    cache.bust()


def _посты():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("SELECT id, kind, city, status, message_id FROM channel_posts ORDER BY id")
    r = [dict(x) for x in cur.fetchall()]
    conn.close()
    return r


def _поставка(строки, ключ):
    r = client.post("/api/admin/stock/move/batch", json={"initData": "x", "reason": "in", "note": "поставка",
                                                         "client_token": ключ, "items": строки})
    tgsend.дождаться_фона()
    return r


def run():
    c = Checker("Посты для канала")
    было_канал, было_бот = config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME
    config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME = "partut_test", "partut_bot"
    было_правка = tgsend.tg.edit_message_text, botmod.bot.edit_message_text
    правки = []
    # Правит и сервер (открыли вручную), и бот (фоновый обход) — у бота свой
    # экземпляр telebot, подменяем оба: иначе тест ушёл бы в настоящий Telegram.
    def правка(text, chat_id=None, message_id=None, **kw):
        правки.append((chat_id, message_id, text))
    tgsend.tg.edit_message_text = botmod.bot.edit_message_text = правка
    _чисто(); as_admin()
    владелец = sorted(config.SUPER_ADMIN_IDS)[0]
    try:
        mid = db.add_model("liquid", "ANNIMA LOVE", "ANNIMA LOVE", "", {"strength": "50"}, ["Кислая вишня", "Клубника киви"])
        жижа = db.create_point_product(mid, "Минск", 25.0, 9.0, variants=[{"flavor": "Кислая вишня", "stock": 0},
                                                                           {"flavor": "Клубника киви", "stock": 0}])
        картридж = db.create_point_product(db.add_model("coils", "XROS", "VAPORESSO"), "Минск", 8.0, 6.5, stock=0)

        # --- Поставка → предложение владельцу ---
        reset_sent()
        _поставка([{"id": жижа, "flavor": "Кислая вишня", "qty": 5}, {"id": картридж, "qty": 10}], "supply-key-0001")
        предложения = [s for s in SENT if int(s[0]) == владелец and "Пост для канала" in s[1]]
        c(f"владельцу пришёл готовый пост: {len(предложения)}", len(предложения) == 1)
        текст = предложения[0][1] if предложения else ""
        c("в посте — бренд с крепостью и вкус, картридж, точка и ссылка на бота",
          "ANNIMA LOVE 50" in текст and "Кислая вишня" in текст and "XROS" in текст and "Минск" in текст and "@partut_bot" in текст)
        c("в канал без владельца не ушло ничего — черновик ждёт", [p["status"] for p in _посты()] == ["offered"])

        # Вторая часть той же поставки — в тот же пост.
        reset_sent()
        _поставка([{"id": жижа, "flavor": "Клубника киви", "qty": 3}], "supply-key-0002")
        посты = _посты()
        c("вторая часть поставки — тот же черновик, не второй", len(посты) == 1)
        пост = db.get_channel_post(посты[0]["id"])
        c("вкусы сложились в одну строку", any(x["flavors"] == ["Кислая вишня", "Клубника киви"] for x in пост["payload"]["lines"]))
        c("владельцу сказано, что пост дополнен", any("дополнен" in s[1] for s in SENT))

        # Повтор уже проведённой пачки и обычный приход в «Складе» — не повод для поста.
        reset_sent()
        _поставка([{"id": жижа, "flavor": "Клубника киви", "qty": 3}], "supply-key-0002")
        client.post("/api/admin/stock/move", json={"initData": "x", "id": картридж, "qty": 1, "reason": "in"})
        tgsend.дождаться_фона()
        c("повтор и обычный приход — без предложений", not any("Пост для канала" in s[1] for s in SENT))

        # --- CH-01: кнопка под старым текстом не публикует новый ---
        тг = ПоддельныйТелеграм()
        итог, сказано = channel.опубликовать(посты[0]["id"], владелец, тг, version=1)
        c(f"кнопка под первой версией (до дополнения) — не публикует: {сказано[:50]}…",
          итог == channel.STALE and not тг.отправлено and db.get_channel_post(посты[0]["id"])["status"] == "offered")
        c("версия черновика после дополнения — 2", пост["payload"]["v"] == 2)

        # --- Публикует владелец — кнопкой под последним текстом ---
        итог, сказано = channel.опубликовать(посты[0]["id"], владелец, тг, version=2)
        c(f"опубликовано в канал: {сказано}", итог == channel.POSTED and тг.отправлено and тг.отправлено[0][0] == "@partut_test"
          and "📦 Поступление" in тг.отправлено[0][1] and "Клубника киви" in тг.отправлено[0][1])
        c("второй раз — нет", channel.опубликовать(посты[0]["id"], владелец, тг, version=2)[0] == channel.DECIDED
          and len(тг.отправлено) == 1)
        c("D-01: пост не обещает ждать отдельный вкус", "когда он снова появится" in тг.отправлено[0][1]
          and "Нужного вкуса нет" not in тг.отправлено[0][1])

        # Telegram однозначно отказал (бот не админ) — сказано, что сделать, можно нажать ещё раз.
        _поставка([{"id": картридж, "qty": 2}], "supply-key-0003")
        новый = _посты()[-1]["id"]
        итог, сказано = channel.опубликовать(новый, владелец, ПоддельныйТелеграм(сломан="отказ"), version=1)
        c(f"отказ — сказано про права бота: {сказано[:60]}…", итог == channel.FAILED and "администратором канала" in сказано
          and db.get_channel_post(новый)["status"] == "offered")

        # --- CH-02: ответ потерян — «неизвестно», повтор только осознанный ---
        обрыв = ПоддельныйТелеграм(сломан="обрыв")
        итог, сказано = channel.опубликовать(новый, владелец, обрыв, version=1)
        c(f"ответ потерян — «не знаю, опубликован ли», без «нажмите ещё раз»: {сказано[:50]}…",
          итог == channel.UNKNOWN and "не знаю" in сказано and db.get_channel_post(новый)["status"] == "unknown")
        итог, _ = channel.опубликовать(новый, владелец, обрыв, version=1)
        c("обычная кнопка второй раз не шлёт (другой владелец, двойное нажатие)", итог == channel.DECIDED and len(обрыв.отправлено) == 1)
        итог, _ = channel.опубликовать(новый, владелец, тг, version=1, повтор=True)
        c("«поста нет — опубликовать ещё раз» — публикует", итог == channel.POSTED and db.get_channel_post(новый)["status"] == "posted")

        # Кнопки в боте: продавцу нельзя; «Не надо» — черновик закрыт.
        def нажать(uid, data):
            reset_sent()
            msg = _t.SimpleNamespace(chat=_t.SimpleNamespace(id=uid), message_id=1)
            botmod.on_button(_t.SimpleNamespace(data=data, id="c1", from_user=_t.SimpleNamespace(id=uid, username="u"), message=msg))
        _поставка([{"id": картридж, "qty": 3}], "supply-key-0005")
        третий = _посты()[-1]["id"]
        клавиатуры = []
        старые = (botmod.bot.answer_callback_query, botmod.bot.edit_message_reply_markup, botmod.bot.send_message)
        botmod.bot.answer_callback_query = lambda *a, **k: SENT.append(("cb", (a[1] if len(a) > 1 else k.get("text", "")), None))
        botmod.bot.edit_message_reply_markup = lambda *a, **k: клавиатуры.append(k.get("reply_markup", "убрана"))

        def шлёт(cid, text, **kw):
            if str(cid).startswith("@"):
                raise _отказ_телеграма()
            SENT.append((cid, text, None))
        try:
            нажать(9999001, f"chpost:{третий}:1")
            c("не владельцу публиковать нельзя", any("только владелец" in str(s[1]) for s in SENT)
              and db.get_channel_post(третий)["status"] == "offered")
            botmod.bot.send_message = шлёт
            клавиатуры.clear()
            нажать(владелец, f"chpost:{третий}:1")
            c("CH-04: Telegram отказал — кнопки под предложением остаются, чтобы нажать ещё раз",
              клавиатуры == [] and db.get_channel_post(третий)["status"] == "offered"
              and any("администратором канала" in str(s[1]) for s in SENT))
            botmod.bot.send_message = старые[2]
            клавиатуры.clear()
            нажать(владелец, f"chskip:{третий}")
            c("«Не надо» — черновик закрыт, кнопки сняты", db.get_channel_post(третий)["status"] == "skipped" and клавиатуры == [None])
        finally:
            botmod.bot.answer_callback_query, botmod.bot.edit_message_reply_markup, botmod.bot.send_message = старые

        # --- Новый товар с приходом — тоже поступление ---
        reset_sent()
        r = client.post("/api/admin/product/publish", json={
            "initData": "x", "client_token": "channel-publish-0001",
            "model": {"category": "accessories", "name": "QA Шнурок", "brand": "QA"},
            "points": [{"city": "Минск", "price": "5", "cost": "2", "stock": 4}], "photos": []})
        tgsend.дождаться_фона()
        c(f"новый товар с приходом — предложен пост: {r.status_code}",
          any("Пост для канала" in s[1] and "QA Шнурок" in s[1] for s in SENT))

        # --- Точка закрыта: пост; открылась — «снова открыта» правкой ---
        reset_sent()
        client.post("/api/admin/pause", json={"initData": "x", "city": "Минск", "minutes": 60, "note": "обед"})
        tgsend.дождаться_фона()
        пауза = [p for p in _посты() if p["kind"] == "pause"][-1]
        c("закрыли — предложен пост «⏸ Точка закрыта до …»",
          any("⏸ Точка «Минск» закрыта до" in s[1] and "обед" in s[1] for s in SENT))
        итог, _ = channel.опубликовать(пауза["id"], владелец, тг, version=1)
        c("опубликован", итог == channel.POSTED and "⏸ Точка «Минск» закрыта до" in тг.отправлено[-1][1])
        постов_до = len(тг.отправлено)
        # CH-03: правка в канале не удалась — пост не считается дописанным.
        tgsend.tg.edit_message_text = botmod.bot.edit_message_text = ПоддельныйТелеграм(правка_сломана=True).edit_message_text
        client.post("/api/admin/pause/open", json={"initData": "x", "city": "Минск"})
        tgsend.дождаться_фона()
        c("правка «снова открыта» не удалась — пост остаётся в работе", db.get_channel_post(пауза["id"])["status"] == "posted")
        tgsend.tg.edit_message_text = botmod.bot.edit_message_text = правка
        c("следующий обход фона дописывает", botmod._finish_channel_reopens() == 1)
        c(f"пост в канале дополнен «снова открыта», нового поста нет: {правки[-1][2][-60:] if правки else None}",
          правки and правки[-1][0] == "@partut_test" and правки[-1][1] == db.get_channel_post(пауза["id"])["message_id"]
          and "снова открыта" in правки[-1][2] and len(тг.отправлено) == постов_до
          and db.get_channel_post(пауза["id"])["status"] == "reopened")
        c("и больше не трогает", botmod._finish_channel_reopens() == 0)

        # Неопубликованный пост о закрытии устаревает с открытием.
        client.post("/api/admin/pause", json={"initData": "x", "city": "Минск", "minutes": 30})
        tgsend.дождаться_фона()
        вторая = [p for p in _посты() if p["kind"] == "pause"][-1]
        client.post("/api/admin/pause/open", json={"initData": "x", "city": "Минск"})
        tgsend.дождаться_фона()
        итог, сказано = channel.опубликовать(вторая["id"], владелец, тг, version=1)
        c(f"точка уже открыта — «закрыта» не публикуется: {сказано}", итог == channel.DECIDED and "устарел" in сказано
          and len(тг.отправлено) == постов_до)

        # Время открытия прошло, а фон ещё не снял пометку (черновик «живой»):
        # публиковать «закрыта» про уже открытую точку всё равно нельзя.
        client.post("/api/admin/pause", json={"initData": "x", "city": "Минск", "minutes": 30})
        tgsend.дождаться_фона()
        третья = [p for p in _посты() if p["kind"] == "pause"][-1]
        conn = db.connect(); cur = conn.cursor()
        прошло = (db.shop_now() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        cur.execute(db._q("UPDATE locations SET closed_until = %s WHERE name = 'Минск'"), (прошло,))
        conn.commit(); conn.close()
        итог, сказано = channel.опубликовать(третья["id"], владелец, тг, version=1)
        c(f"время вышло, фон не успел — всё равно не публикуем: {сказано}",
          итог == channel.DECIDED and "уже открыта" in сказано and db.get_channel_post(третья["id"])["status"] == "expired")

        # --- Канал не задан — постов не предлагаем ---
        config.SUBSCRIBE_CHANNEL = ""
        reset_sent()
        _поставка([{"id": картридж, "qty": 1}], "supply-key-0004")
        c("канал не задан — ни предложений, ни черновиков",
          not any("Пост для канала" in s[1] for s in SENT) and _посты()[-1]["kind"] == "pause")
    finally:
        config.SUBSCRIBE_CHANNEL, tgsend.BOT_USERNAME = было_канал, было_бот
        tgsend.tg.edit_message_text, botmod.bot.edit_message_text = было_правка
        _чисто()
    return c.fails
