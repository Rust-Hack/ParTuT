"""
partut/channel.py — посты для телеграм-канала магазина.

Приложение само собирает пост, когда в магазине что-то произошло:
  • продавец провёл поставку или владелец завёл новый товар с приходом —
    «📦 Поступление» с брендами и вкусами по точке;
  • продавец закрыл точку на время — «⏸ Точка закрыта до …».

В канал пост уходит только после нажатия владельца («📣 В канал» в чате с
ботом): это публикация от имени магазина, и решать её человеку. Пост о
закрытии, опубликованный в канале, бот сам дополняет «снова открыта», когда
точка открылась, — новым постом канал не засоряется.

Канал — тот же, на который приложение требует подписку (SUBSCRIBE_CHANNEL).
Не задан — постов не предлагаем вовсе. Бот должен быть администратором
канала с правом публиковать — иначе владелец узнает об этом при нажатии.
"""

from telebot import types

from partut import config, db
from partut.integrations import tgsend

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
                  "Нужного вкуса нет — нажмите «Жду поступления» в карточке: бот напишет, когда он появится.")
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
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("📣 В канал", callback_data=f"chpost:{post['id']}"),
           types.InlineKeyboardButton("Не надо", callback_data=f"chskip:{post['id']}"))
    шапка = (f"📣 Пост для канала {канал()}" + (" — дополнен второй частью поставки" if дополнен else "") + ":\n\n")
    for uid in config.SUPER_ADMIN_IDS:
        try:
            tg.send_message(uid, шапка + текст(post) + "\n\nОпубликовать? В канал уйдёт ровно этот текст.",
                            reply_markup=kb)
        except Exception as e:
            print(f"Не предложил пост владельцу {uid}: {e}")
    return post


def предложить_поступление(движения, tg=None):
    """После проведённой поставки или нового товара: [(product_id, flavor, штук)],
    по точкам — свой пост на каждую."""
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

def опубликовать(post_id, admin_id, tg=None):
    """(получилось?, что сказать владельцу)."""
    tg = tg or tgsend.tg
    post = db.get_channel_post(post_id)
    if not post:
        return False, "Этого черновика больше нет."
    if post["status"] != "offered":
        return False, {"posted": "Этот пост уже опубликован.", "posting": "Пост уже публикуется.",
                       "skipped": "Этот пост решили не публиковать.", "expired": "Пост устарел — точка уже открыта.",
                       "reopened": "Этот пост уже опубликован."}.get(post["status"], "Уже решено.")
    if not канал():
        return False, "Канал не задан (SUBSCRIBE_CHANNEL в настройках Render) — публиковать некуда."
    # «Закрыта до 15:00» про точку, которая уже открылась, — неправда.
    if post["kind"] == "pause" and not db.location_pause(post["city"]):
        db.decide_channel_post(post_id, "expired", admin_id)
        return False, "Точка уже открыта — пост о закрытии не публикую."
    if not db.claim_channel_post(post_id, admin_id):
        return False, "Пост уже публикуется или решён другим владельцем."
    try:
        msg = tg.send_message(канал(), текст(post))
    except Exception as e:
        db.finish_channel_post(post_id, "offered")
        return False, (f"Не получилось опубликовать: {e}\n\nБот должен быть администратором канала {канал()} "
                       "с правом публиковать сообщения. Добавьте его и нажмите «📣 В канал» ещё раз.")
    db.finish_channel_post(post_id, "posted", getattr(msg, "message_id", None))
    return True, f"Опубликовано в {канал()} ✅"


def отказаться(post_id, admin_id):
    return db.decide_channel_post(post_id, "skipped", admin_id)


def точка_открыта(city, tg=None):
    """Точка открылась (вручную или по времени). Опубликованный пост о закрытии
    дополняем строкой «снова открыта» — правкой, без нового поста; черновики
    о закрытии больше не публикуются."""
    tg = tg or tgsend.tg
    post = db.posted_pause_post(city)
    db.close_pause_posts(city)
    if not post or not post.get("message_id") or not канал():
        return False
    try:
        tg.edit_message_text(текст(post) + f"\n\n▶️ Обновление: точка снова открыта с {db.shop_now().strftime('%H:%M')}.",
                             chat_id=канал(), message_id=post["message_id"])
        return True
    except Exception as e:
        print(f"Не дополнил пост о закрытии «{city}»: {e}")
        return False
