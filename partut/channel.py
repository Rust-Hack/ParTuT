"""
partut/channel.py — посты для телеграм-канала магазина.

Приложение само собирает пост, когда в магазине что-то произошло:
  • продавец закрыл точку на время — «⏸ Точка закрыта до …»;
  • (выключено, см. ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО) продавец провёл поставку или
    владелец завёл новый товар — «📦 Поступление» с брендами и вкусами.

В канал пост уходит только после нажатия владельца («📣 В канал» в чате с
ботом): это публикация от имени магазина, и решать её человеку. Пост о
закрытии, опубликованный в канале, бот сам дополняет «снова открыта», когда
точка открылась, — новым постом канал не засоряется.

Канал — тот же, на который приложение требует подписку (SUBSCRIBE_CHANNEL).
Не задан — постов не предлагаем вовсе. Бот должен быть администратором
канала с правом публиковать — иначе владелец узнает об этом при нажатии.
"""

import datetime

from telebot import apihelper, types

from partut import config, db
from partut.integrations import tgsend

# Пост «📦 Поступление» ВЫКЛЮЧЕН (решение владельца 3.10.2026). Перечень
# жидкостей со ссылкой на магазин — реклама, а п. 3 ст. 17 Закона «О рекламе»
# запрещает рекламу жидкостей для электронных систем курения и нетабачных
# никотинсодержащих изделий (кроме сайтов производителей и импортёров).
# Включать — только после письменной правовой оценки. Служебные посты
# («точка закрыта / снова открыта») это не трогает.
ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО = False

МЕСЯЦЫ = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def канал():
    return f"@{config.SUBSCRIBE_CHANNEL}" if config.SUBSCRIBE_CHANNEL else ""


def _дата():
    d = db.shop_now()
    return f"{d.day} {МЕСЯЦЫ[d.month - 1]}"


# ---------- Поступление ----------

def _единица(category, key):
    for s in db.list_category_specs(category):
        if s["key"] == key:
            return s["unit"] or ""
    return ""


def _название(p):
    """Название для поста: у жидкости — с крепостью («ANNIMA LOVE 50 мг»), если
    её нет в самом названии. Крепость у неё — характеристика, а в канале
    покупатель выбирает по ней."""
    имя = (p["name"] or "").strip()
    крепость = str(p["strength"] or "").strip() if "strength" in p.keys() else ""
    if крепость and крепость not in имя:
        имя = f"{имя} {крепость} {_единица(p['category'], 'strength')}".strip()
    return имя


def строки_поступления(движения):
    """[(product_id, flavor, штук)] → данные поста {"lines": [{name, category, flavors}]}.
    Порядок — как пришло; вкусы одного товара — в одну строку."""
    строки, где = [], {}
    for pid, вкус, штук in движения:
        if not штук or штук <= 0:
            continue
        p = db.get_product(pid)
        if not p:
            continue
        имя = _название(p)
        if имя not in где:
            где[имя] = {"name": имя, "category": p["category"], "flavors": []}
            строки.append(где[имя])
        if вкус and вкус not in где[имя]["flavors"]:
            где[имя]["flavors"].append(вкус)
    return {"lines": строки}


def слить_поступление(старый, новый):
    """Вторая партия той же поставки — в тот же пост."""
    строки = [dict(x, flavors=list(x.get("flavors") or [])) for x in (старый.get("lines") or [])]
    где = {x["name"]: x for x in строки}
    for x in новый.get("lines") or []:
        if x["name"] not in где:
            где[x["name"]] = dict(x, flavors=list(x.get("flavors") or []))
            строки.append(где[x["name"]])
        else:
            for f in x.get("flavors") or []:
                if f not in где[x["name"]]["flavors"]:
                    где[x["name"]]["flavors"].append(f)
    return {"lines": строки}


def _категории():
    try:
        return {c["code"]: c["name"] for c in db.list_categories()}
    except Exception:
        return {}


def текст(post):
    """Текст поста для канала — из данных черновика."""
    city, данные = post["city"], post["payload"] or {}
    бот = f"@{tgsend.BOT_USERNAME}" if tgsend.BOT_USERNAME else "приложении магазина"
    if post["kind"] == "supply":
        имена = _категории()
        группы, порядок = {}, []
        for x in данные.get("lines") or []:
            к = x.get("category") or ""
            if к not in группы:
                группы[к] = []
                порядок.append(к)
            группы[к].append(x)
        блоки = []
        for к in порядок:
            строки = [f"• {x['name']}" + (f" — {', '.join(x['flavors'])}" if x.get("flavors") else "") for x in группы[к]]
            блоки.append((имена.get(к) or к) + "\n" + "\n".join(строки))
        return (f"📦 Поступление · {данные.get('date') or _дата()} · {city}\n\n" + "\n\n".join(блоки)
                + f"\n\nЧто есть на точке — в приложении: {бот}\n"
                  # «Жду поступления» ждёт товар целиком, а не отдельный вкус
                  # (приёмка D-01) — обещать вкус нельзя.
                  "Товара нет на вашей точке — нажмите «Жду поступления» в его карточке: бот напишет, когда он снова появится.")
    if post["kind"] == "pause":
        заметка = (данные.get("note") or "").strip()
        слова = данные.get("words") or ""
        когда = f"закрыта {слова}" if слова.startswith("до ") else "временно закрыта"
        return (f"⏸ Точка «{city}» {когда}"
                + (f" — {заметка}" if заметка else "") + ".\n\n"
                "Каталог в приложении работает, корзину можно собрать — оформить заказ получится после открытия.")
    return ""


# ---------- Предложить владельцу ----------

def предложить(kind, city, payload, merge=None, tg=None):
    """Черновик и сообщение владельцам с «📣 В канал» / «Не надо». Канал не
    задан — ничего (None). Сбой отправки владельцу не роняет того, кто позвал:
    поставка проведена, а пост — дело второе."""
    if not канал():
        return None
    tg = tg or tgsend.tg
    post, дополнен = db.offer_channel_post(kind, city, payload, merge)
    for uid in config.SUPER_ADMIN_IDS:
        try:
            показать(post, uid, tg, дополнен)
        except Exception as e:
            print(f"Не предложил пост владельцу {uid}: {e}")
    return post


def кнопки(post):
    """«📣 В канал» привязана к версии текста: дополненный пост — новая версия
    и новое согласование (CH-01)."""
    kb = types.InlineKeyboardMarkup()
    v = int((post["payload"] or {}).get("v") or 1)
    kb.row(types.InlineKeyboardButton("📣 В канал", callback_data=f"chpost:{post['id']}:{v}"),
           types.InlineKeyboardButton("Не надо", callback_data=f"chskip:{post['id']}"))
    return kb


def кнопка_повтора(post):
    """После неизвестного исхода — только осознанный повтор (CH-02)."""
    kb = types.InlineKeyboardMarkup()
    v = int((post["payload"] or {}).get("v") or 1)
    kb.row(types.InlineKeyboardButton("📣 Поста в канале нет — опубликовать ещё раз", callback_data=f"chforce:{post['id']}:{v}"))
    return kb


def кнопки_по_состоянию(post):
    """Кнопки под сообщением — по тому, в каком состоянии черновик СЕЙЧАС
    (приёмка CH-02-R1): ждёт решения — «📣 В канал»; исход неизвестен —
    осознанный повтор; решён — никаких. После однозначного отказа Telegram на
    осознанный повтор черновик снова ждёт решения, и кнопка повтора под ним
    была бы ложью: она ждёт «неизвестно» и ничего не публикует."""
    if not post:
        return None
    if post["status"] == "offered":
        return кнопки(post)
    if post["status"] == "unknown":
        return кнопка_повтора(post)
    return None


def показать(post, chat_id, tg=None, дополнен=False):
    """Черновик одному владельцу: текст и кнопки по состоянию."""
    tg = tg or tgsend.tg
    шапка = (f"📣 Пост для канала {канал()}" + (" — дополнен второй частью поставки" if дополнен else "") + ":\n\n")
    tg.send_message(chat_id, шапка + текст(post) + "\n\nОпубликовать? В канал уйдёт ровно этот текст.",
                    reply_markup=кнопки_по_состоянию(post))


def предложить_поступление(движения, tg=None):
    """После проведённой поставки или нового товара: [(product_id, flavor, штук)],
    по точкам — свой пост на каждую. Выключено (см. ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО)."""
    if not ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО:
        return
    по_точкам = {}
    for pid, вкус, штук in движения:
        p = db.get_product(pid)
        if p:
            по_точкам.setdefault(p["city"], []).append((pid, вкус, штук))
    for city, свои in по_точкам.items():
        данные = строки_поступления(свои)
        if данные["lines"]:
            данные["date"] = _дата()
            предложить("supply", city, данные, merge=слить_поступление, tg=tg)


def предложить_паузу(city, words, note, tg=None):
    return предложить("pause", city, {"words": words, "note": note or ""}, tg=tg)


# ---------- Решение владельца ----------

# Итоги публикации — для кнопки в боте: что сказать и что делать с кнопками.
POSTED, FAILED, UNKNOWN, STALE, DECIDED = "posted", "failed", "unknown", "stale", "decided"
# Кнопка старого образца, без версии текста: прислать черновик заново.
RESEND = "resend"


def _однозначный_отказ(e):
    """Telegram ответил отказом (нет прав, неверный канал, слишком часто) —
    сообщение точно не ушло. Сеть, тайм-аут, 5xx — исход неизвестен: могло
    и уйти, а ответ потеряться (CH-02)."""
    return isinstance(e, apihelper.ApiTelegramException) and int(getattr(e, "error_code", 0) or 0) < 500


def опубликовать(post_id, admin_id, tg=None, version=None, повтор=False):
    """Опубликовать черновик. version — версия текста под нажатой кнопкой;
    повтор — осознанная повторная попытка после неизвестного исхода.
    Возвращает (итог, что сказать владельцу), итог — одна из констант выше."""
    tg = tg or tgsend.tg
    post = db.get_channel_post(post_id)
    if not post:
        return DECIDED, "Этого черновика больше нет."
    ждём = "unknown" if повтор else "offered"
    if post["status"] != ждём:
        return DECIDED, {"posted": "Этот пост уже опубликован.", "posting": "Пост уже публикуется.",
                         "skipped": "Этот пост решили не публиковать.", "expired": "Пост устарел — точка уже открыта.",
                         "reopened": "Этот пост уже опубликован.",
                         "unknown": "Опубликован ли этот пост, неизвестно — посмотрите канал и нажмите кнопку под сообщением об этом.",
                         "offered": "Этот пост ещё не публиковали — нажмите «📣 В канал»."}.get(post["status"], "Уже решено.")
    # Выключенный вид — не публикуется НИКАКОЙ кнопкой (приёмка CH-05): ни
    # черновиком, подготовленным до выключения, ни осознанным повтором.
    # Выключатель проверяется здесь, на самой отправке, а не только там,
    # где черновики создаются.
    if post["kind"] == "supply" and not ПОСТУПЛЕНИЕ_ВКЛЮЧЕНО:
        db.decide_channel_post(post_id, "skipped", admin_id, from_status=ждём)
        return DECIDED, ("Посты «Поступление» выключены (закон «О рекламе», ст. 17) — этот не публикую. "
                         "Черновик снят.")
    текущая = int((post["payload"] or {}).get("v") or 1)
    # Кнопка старого образца (до 16f4be2 версии в ней не было): какой текст
    # видел владелец, неизвестно — отсутствие версии не согласие с нынешним
    # текстом (приёмка CH-01-R1). Присылаем черновик заново.
    if version is None:
        return RESEND, ("Кнопка старого образца — не знаю, какой текст был перед вами. Прислал пост заново: "
                        "проверьте текст и опубликуйте из нового сообщения.")
    if int(version) != текущая:
        return STALE, ("Пост с тех пор дополнен — под этой кнопкой старый текст. Опубликуйте из последнего "
                       "сообщения с этим постом: там то, что уйдёт в канал.")
    if not канал():
        return FAILED, "Канал не задан (SUBSCRIBE_CHANNEL в настройках Render) — публиковать некуда."
    # «Закрыта до 15:00» про точку, которая уже открылась, — неправда.
    if post["kind"] == "pause" and not db.location_pause(post["city"]):
        db.decide_channel_post(post_id, "expired", admin_id)
        return DECIDED, "Точка уже открыта — пост о закрытии не публикую."
    if not db.claim_channel_post(post_id, admin_id, from_status=ждём):
        return DECIDED, "Пост уже публикуется или решён другим владельцем."
    try:
        msg = tg.send_message(канал(), текст(post))
    except Exception as e:
        if _однозначный_отказ(e):
            db.finish_channel_post(post_id, "offered")
            return FAILED, (f"Telegram не принял пост: {e}\n\nБот должен быть администратором канала {канал()} "
                            "с правом публиковать сообщения. Добавьте его и нажмите «📣 В канал» ещё раз.")
        db.finish_channel_post(post_id, "unknown")
        return UNKNOWN, ("Ответ Telegram не дошёл — не знаю, опубликован ли пост. Посмотрите канал "
                         f"{канал()}: если поста нет, нажмите «Опубликовать ещё раз»; если есть — ничего не делайте.")
    db.finish_channel_post(post_id, "posted", getattr(msg, "message_id", None))
    return POSTED, f"Опубликовано в {канал()} ✅"


def отказаться(post_id, admin_id):
    return db.decide_channel_post(post_id, "skipped", admin_id)


def _когда_открыта(когда):
    """«2026-10-03 10:00» → «10:00» сегодня, «2 октября, 10:00» — в другой день."""
    try:
        d = datetime.datetime.strptime(str(когда)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return str(когда)
    if d.date() == db.shop_now().date():
        return d.strftime("%H:%M")
    return f"{d.day} {МЕСЯЦЫ[d.month - 1]}, {d.strftime('%H:%M')}"


def дописать_открытые(tg=None, city=None, когда=None):
    """Опубликованные посты «точка закрыта» тех точек, что уже открыты, —
    дополнить «▶️ снова открыта». Отмечаем только удавшуюся правку (CH-03):
    не удалась — пост остаётся в работе, и следующий обход (фон бота раз в
    15 минут, следующее открытие) попробует снова. Возвращает, сколько дописано.

    Время открытия запоминается в посте при ПЕРВОЙ попытке (CH-03-R1): у точки
    city — переданное когда (время события), иначе — сейчас. Повтор через
    15 минут пишет то же время, а не время самого повтора."""
    tg = tg or tgsend.tg
    if not канал():
        return 0
    дописано = 0
    for post in db.posted_pause_posts():
        if db.location_pause(post["city"]) or not post.get("message_id"):
            continue
        событие = когда if (когда and post["city"] == city) else db._now_str()
        открыта = db.remember_reopen_time(post["id"], (post["payload"] or {}).get("opened_at") or событие)
        try:
            tg.edit_message_text(текст(post) + f"\n\n▶️ Обновление: точка снова открыта с {_когда_открыта(открыта)}.",
                                 chat_id=канал(), message_id=post["message_id"])
        except Exception as e:
            # Уже дописано прошлым обходом, а отметка не легла, — это успех.
            if "message is not modified" not in str(e):
                print(f"Не дополнил пост о закрытии «{post['city']}»: {e}")
                continue
        db.mark_pause_reopened(post["id"])
        дописано += 1
    return дописано


def точка_открыта(city, tg=None, когда=None):
    """Точка открылась (вручную или по времени): черновики «закрыта» больше
    не публикуются, опубликованный пост — «снова открыта». когда — время
    открытия («ГГГГ-ММ-ДД ЧЧ:ММ»): по времени — то, что было назначено, а не
    минута, когда до неё дошёл обход бота; вручную — сейчас."""
    db.expire_pause_drafts(city)
    return дописать_открытые(tg, city=city, когда=когда)
