"""
partut/web/games.py — ручки развлечений: колесо, слот, розыгрыши.

Первый кусок, вынесенный из server.py (3873 строки). Здесь всё, что раздаёт
призы: колесо фортуны, слот «Облако Монет» и розыгрыш с билетами.

Маршруты регистрируются на том же приложении (server.app), права проверяет тот
же общий страж — переезд для магазина невидим.

Помощники берутся ЧЕРЕЗ модуль (auth.get_user(), db), а не копиями
имён: копия не заметила бы подмены в тестах. То же правило, что и у модулей
базы, — см. partut/db/raffles.py.
"""

import json
import secrets

from flask import Blueprint, jsonify, request

from partut.web import auth
from partut import db
from partut.web import photos
from partut.integrations import tgsend
from partut import inputs
from partut import notifications

# Маршруты объявляются на Blueprint, а не на приложении: так этот модуль
# НЕ импортирует server, и граф зависимостей остаётся деревом.
# Подключает его фабрика в server.py.
bp = Blueprint("games", __name__)


# Жребий, который не предсказать.
#
# Обычный random — это вихрь Мерсенна: зная шестьсот двадцать четыре подряд
# выданных числа, дальнейшие считают точно. Для перемешивания списка это
# безразлично, а здесь на кону монеты, то есть скидка, то есть деньги
# магазина. Тот, кто видит исходы (а колесо их и показывает), в принципе
# способен подгадать момент прокрута. Цена вопроса — одна строка, поэтому
# спорить тут не о чем.
жребий = secrets.SystemRandom()


# Колесо фортуны: секторы (монеты + вес вероятности). Малые призы — часто, крупные — редко.
WHEEL_SECTORS = [
    {"label": "100",  "coins": 100,  "weight": 26},
    {"label": "200",  "coins": 200,  "weight": 20},
    {"label": "300",  "coins": 300,  "weight": 16},
    {"label": "400",  "coins": 400,  "weight": 12},
    {"label": "500",  "coins": 500,  "weight": 9},
    {"label": "600",  "coins": 600,  "weight": 6},
    {"label": "700",  "coins": 700,  "weight": 4},
    {"label": "800",  "coins": 800,  "weight": 3},
    {"label": "900",  "coins": 900,  "weight": 2},
    {"label": "1000", "coins": 1000, "weight": 1},
    {"label": "SUPER","coins": 2000, "weight": 1},
]


@bp.route("/api/wheel", methods=["POST"])
def api_wheel():
    """Состояние колеса: секторы, доступные прокруты, прогресс."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    w = db.get_wheel(uid)
    return jsonify({"ok": True,
                    "sectors": [{"label": s["label"], "coins": s["coins"]} for s in WHEEL_SECTORS],
                    "spins": w["spins"], "progress": w["progress"], "step": w["step"]})


@bp.route("/api/wheel/spin", methods=["POST"])
def api_wheel_spin():
    """Прокрут колеса: списываем прокрут, выбираем приз по весам, начисляем монеты."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    weights = [s["weight"] for s in WHEEL_SECTORS]
    idx = жребий.choices(range(len(WHEEL_SECTORS)), weights=weights, k=1)[0]
    prize = WHEEL_SECTORS[idx]
    # Списание, начисление, летопись монет и счётчики — одним подключением.
    res = db.do_wheel_spin(uid, prize["coins"])
    if res is None:
        return jsonify({"ok": False, "error": "no_spins"}), 400
    coins, spins = res
    return jsonify({"ok": True, "index": idx, "coins": prize["coins"], "label": prize["label"],
                    "balance": coins, "spins": spins})


# Ставка регулируется игроком: 2..10 с шагом 2. Приз = ставка × множитель символа.
SLOT_BETS = [2, 4, 6, 8, 10]


SLOT_MIN_BET = SLOT_BETS[0]


# Экономика: суммарный шанс выигрыша = 30%, средневзвеш. множитель ≈ 2.67 → RTP ≈ 79% (house edge ~21%).
# Множители целые, а ставки чётные → приз всегда целый. Символы от частых/мелких к редким/крупным.
SLOT_SYMBOLS = [
    {"key": "cart",   "emoji": "🔋", "label": "Картридж",  "mult": 2,  "percent": 9},
    {"key": "cig",    "emoji": "🚬", "label": "Сигарета",  "mult": 2,  "percent": 7},
    {"key": "snus",   "emoji": "🟤", "label": "Снюс",      "mult": 2,  "percent": 5},
    {"key": "liquid", "emoji": "🧪", "label": "Жижа",      "mult": 3,  "percent": 4},
    {"key": "disp",   "emoji": "💨", "label": "Одноразка", "mult": 3,  "percent": 2.5},
    {"key": "pod",    "emoji": "📦", "label": "Под",       "mult": 5,  "percent": 1.5},
    {"key": "coin",   "emoji": "🪙", "label": "Монетка",   "mult": 8,  "percent": 0.7},
    {"key": "crown",  "emoji": "👑", "label": "Корона",    "mult": 15, "percent": 0.3},
]


# Линии слота (клетки [row, col]): 3 ряда, 2 диагонали и 2 зигзага (галочка ∨ / крышка ∧).
SLOT_LINES = [
    [[0, 0], [0, 1], [0, 2]],   # верхний ряд
    [[1, 0], [1, 1], [1, 2]],   # центральный ряд
    [[2, 0], [2, 1], [2, 2]],   # нижний ряд
    [[0, 0], [1, 1], [2, 2]],   # диагональ ↘
    [[0, 2], [1, 1], [2, 0]],   # диагональ ↙
    [[0, 0], [1, 1], [0, 2]],   # галочка ∨ (верх-слева → центр → верх-справа)
    [[2, 0], [1, 1], [2, 2]],   # крышка ∧ (низ-слева → центр → низ-справа)
]


SLOT_LINE_NAMES = ["Верхний ряд", "Центр", "Нижний ряд", "Диагональ ↘", "Диагональ ↙", "Галочка ∨", "Крышка ∧"]
# Названия и сами линии идут парами. Разойдись они в длине — лишняя линия молча
# пропала бы с экрана правил, а игрок выигрывал бы по линии, о которой ему не
# сказали. Лучше не запуститься, чем играть по необъявленным правилам.
assert len(SLOT_LINE_NAMES) == len(SLOT_LINES), \
    f"названий линий {len(SLOT_LINE_NAMES)}, а самих линий {len(SLOT_LINES)}"


def _line_vals(grid, line):
    return [grid[r][c] for r, c in line]


def _slot_grid(win_emoji, line_idx):
    """Строит 3×3. Если win_emoji задан — выкладывает его по линии line_idx и ломает
    любые ДРУГИЕ случайно совпавшие линии. При проигрыше — гарантирует, что НИ ОДНА
    линия не совпала (чтобы не показать неоплаченный «выигрыш»)."""
    emojis = [s["emoji"] for s in SLOT_SYMBOLS]
    grid = [[жребий.choice(emojis) for _ in range(3)] for _ in range(3)]
    if win_emoji is not None:
        for r, c in SLOT_LINES[line_idx]:
            grid[r][c] = win_emoji
        target = {(r, c) for r, c in SLOT_LINES[line_idx]}
        for _ in range(50):     # убрать ЧУЖИЕ совпадения, не трогая целевую линию
            bad = [i for i, ln in enumerate(SLOT_LINES)
                   if i != line_idx and len(set(_line_vals(grid, ln))) == 1]
            if not bad:
                break
            free = [(r, c) for r, c in SLOT_LINES[bad[0]] if (r, c) not in target]
            if not free:
                break
            r, c = жребий.choice(free)
            cur = grid[r][c]
            grid[r][c] = жребий.choice([e for e in emojis if e != cur] or emojis)
        return grid
    # проигрыш — ломаем любые случайно совпавшие линии
    for _ in range(50):
        bad = [i for i, ln in enumerate(SLOT_LINES) if len(set(_line_vals(grid, ln))) == 1]
        if not bad:
            break
        r, c = SLOT_LINES[bad[0]][жребий.randint(0, 2)]
        cur = grid[r][c]
        grid[r][c] = жребий.choice([e for e in emojis if e != cur] or emojis)
    return grid


@bp.route("/api/slot", methods=["POST"])
def api_slot():
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    return jsonify({"ok": True, "bets": SLOT_BETS, "balance": db.get_coins(uid),
                    "symbols": [{"emoji": s["emoji"], "label": s["label"], "mult": s["mult"]} for s in SLOT_SYMBOLS],
                    "lines": [{"name": n, "cells": ln} for n, ln in zip(SLOT_LINE_NAMES, SLOT_LINES)]})


@bp.route("/api/slot/spin", methods=["POST"])
def api_slot_spin():
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    bet = inputs.целое(data.get("bet", SLOT_MIN_BET), SLOT_MIN_BET)
    if bet not in SLOT_BETS:
        return jsonify({"ok": False, "error": "bad_bet"}), 400
    roll = жребий.random() * 100.0
    cum, win = 0.0, None
    for s in SLOT_SYMBOLS:
        cum += s["percent"]
        if roll < cum:
            win = s
            break
    prize_coins = bet * win["mult"] if win else 0     # приз = ставка × множитель
    # Списание, приз, летопись монет и счётчики — одним подключением.
    balance = db.do_slot_spin(uid, bet, prize_coins)
    if balance is None:
        return jsonify({"ok": False, "error": "no_coins"}), 400

    # Сетка 3×3. Выигрыш выкладывается по случайной линии (ряд или диагональ).
    if win:
        line_idx = жребий.randint(0, len(SLOT_LINES) - 1)
        grid = _slot_grid(win["emoji"], line_idx)
        win_cells = SLOT_LINES[line_idx]
    else:
        grid = _slot_grid(None, 0)
        win_cells = []
    return jsonify({"ok": True, "win": bool(win), "grid": grid, "win_cells": win_cells,
                    "coins": prize_coins, "label": win["label"] if win else "",
                    "bet": bet, "balance": balance})


def _draw_raffle(raffle):
    """Итоги розыгрыша. Сама работа — в notifications: её зовёт и бот по ночам."""
    return notifications.draw_raffle(tgsend.tg, raffle)


def _close_expired_raffle():
    """Если срок вышел — подвести итоги. Нового розыгрыша не заводим.

    Раньше здесь был «ленивый планировщик»: не нашёл активного розыгрыша —
    завёл новый. Из-за этого розыгрыш висел в приложении всегда и выключить его
    было нельзя в принципе. Теперь розыгрыш идёт только тогда, когда владелец
    его начал.
    """
    return notifications.close_expired_raffle(tgsend.tg)


def _raffle_results(raffle, for_admin=False):
    """Итоги завершённого розыгрыша: кто выиграл и кто вообще участвовал.

    Участники показываются наравне с победителями. Розыгрыш, в котором видно
    только троих счастливчиков, выглядит как розыгрыш без свидетелей.

    for_admin добавляет к победителю user_id и username — владельцу нужно
    понимать, с кем связаться за призом, а не только порадоваться за него
    вместе с покупателями."""
    try:
        winners = json.loads(raffle["winners"]) if raffle["winners"] else []
    except (TypeError, ValueError):
        winners = []
    winner_ids = {int(w.get("user_id") or 0) for w in winners}
    entrants = [int(u) for u in db.get_raffle_user_ids(raffle["id"])]
    # Фото своё у каждого места — берём из самого розыгрыша (колонки живут и
    # после завершения), а не из записи победителя: место разыграно, а картинка
    # приза к нему привязана всегда, даже если это место никто не занял.
    photos_by_place = {1: raffle["photo1"] or "", 2: raffle["photo2"] or "", 3: raffle["photo3"] or ""}
    winners_out = []
    for w in winners:
        entry = {"place": w.get("place"), "who": _winner_name(w.get("user_id")),
                 "prize": w.get("prize") or "", "photo": photos_by_place.get(w.get("place"), "")}
        if for_admin:
            u = db.get_user_row(w.get("user_id"))
            entry["user_id"] = w.get("user_id")
            entry["username"] = (u["username"] or "") if (u and "username" in u.keys()) else ""
        winners_out.append(entry)
    return {"title": raffle["title"] or "Розыгрыш",
            "finished_at": raffle["finished_at"] or raffle["ends_at"],
            "winners": winners_out,
            # Победители уже названы выше — в списке участников их не повторяем.
            "participants": [_mask_id(u) for u in entrants if u not in winner_ids],
            "participants_count": len(entrants)}


def _raffle_public_from_state(st, for_admin=False):
    r = st["raffle"]
    threshold = round(r["threshold"] or 0, 2)
    spent = round(st["spent"], 2)
    try:
        raw_winners = json.loads(st["last_winners_raw"]) if st["last_winners_raw"] else []
    except (TypeError, ValueError):
        raw_winners = []
    # Та же подстановка имени, что и в _raffle_results: сырые данные хранят
    # только user_id, а кто это — решаем каждый раз заново при чтении (имя в
    # Telegram могло смениться), и одинаково для обеих витрин с победителями.
    last_winners = []
    for w in raw_winners:
        entry = {"place": w.get("place"), "who": _winner_name(w.get("user_id")),
                 "prize": w.get("prize") or ""}
        if for_admin:
            u = db.get_user_row(w.get("user_id"))
            entry["user_id"] = w.get("user_id")
            entry["username"] = (u["username"] or "") if (u and "username" in u.keys()) else ""
        last_winners.append(entry)
    return {
        "id": r["id"], "title": r["title"] or "Розыгрыш месяца",
        "prize1": r["prize1"] or "", "prize2": r["prize2"] or "", "prize3_coins": r["prize3_coins"],
        "ends_at": r["ends_at"], "threshold": threshold,
        "photo1": r["photo1"] or "", "photo2": r["photo2"] or "", "photo3": r["photo3"] or "",
        "participants": st["participants"],
        "spent": spent, "remaining": round(max(0, threshold - spent), 2),
        "eligible": spent >= threshold, "entered": st["entered"],
        "last_winners": last_winners,
    }


@bp.route("/api/raffle", methods=["POST"])
def api_raffle():
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    _close_expired_raffle()
    st = db.get_raffle_state(uid)      # всё за одно подключение
    if not st:
        # Розыгрыш не идёт. Но если он только что закончился, покажем итоги:
        # победителям бот написал лично, а остальные участники иначе не узнают
        # о розыгрыше вообще ничего.
        finished_raffle = db.recent_finished_raffle()
        if finished_raffle:
            for_admin = bool(auth.get_admin(data.get("initData", "")))
            return jsonify({"ok": True, "raffle": None,
                            "finished": _raffle_results(finished_raffle, for_admin)})
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    for_admin = bool(auth.get_admin(data.get("initData", "")))
    return jsonify({"ok": True, "raffle": _raffle_public_from_state(st, for_admin)})


@bp.route("/api/raffle/history", methods=["POST"])
def api_raffle_history():
    """Архив прошлых розыгрышей — не только последний.

    Один маршрут на покупателя и владельца: и тому, и другому нужен один и
    тот же список, разница только в том, что админ вдобавок видит контакты
    победителей (user_id/username) — с кем связаться за призом. Заводить
    отдельный /api/admin/... ради одного доп. поля незачем."""
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    for_admin = bool(auth.get_admin(data.get("initData", "")))
    return jsonify({"ok": True, "history": [_raffle_results(r, for_admin) for r in db.finished_raffles(15)]})


@bp.route("/api/raffle/join", methods=["POST"])
def api_raffle_join():
    data = request.get_json(force=True, silent=True) or {}
    user = auth.get_user(data.get("initData", ""))
    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = int(user["id"])
    _close_expired_raffle()
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    if db.is_entered(r["id"], uid):
        return jsonify({"ok": True, "entered": True})
    if db.spent_since(uid, r["starts_at"]) < (r["threshold"] or 0):
        return jsonify({"ok": False, "error": "not_eligible"}), 400
    db.add_raffle_entry(r["id"], uid)
    return jsonify({"ok": True, "entered": True})


@bp.route("/api/admin/raffle", methods=["POST"])
def api_admin_raffle():
    """Текущий розыгрыш для настройки."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _close_expired_raffle()
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": True, "raffle": None})
    return jsonify({"ok": True, "raffle": {
        "photo1": r["photo1"] or "", "photo2": r["photo2"] or "", "photo3": r["photo3"] or "",
        "id": r["id"], "title": r["title"] or "", "prize1": r["prize1"] or "", "prize2": r["prize2"] or "",
        "prize3_coins": r["prize3_coins"], "threshold": round(r["threshold"] or 0, 2),
        "ends_at": r["ends_at"], "participants": db.count_entries(r["id"]),
    }})


@bp.route("/api/admin/raffle/update", methods=["POST"])
def api_admin_raffle_update():
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _close_expired_raffle()
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    # Пустое поле — не «оставить как было», а промах: название и призы уходят
    # прямо на витрину. «Сохранено ✅» на стёртом призе значило бы, что так и
    # задумано. Проверяем ВСЕ поля до записи — иначе первое пустое стёрлось бы,
    # а второе с ошибкой откатило бы запрос, оставив розыгрыш наполовину правленным.
    правки = {}
    for field in ("title", "prize1", "prize2"):
        if field in data:
            value = inputs._text(data[field], 100)
            if not value:
                return jsonify({"ok": False, "error": "empty",
                                "message": "Название и призы не могут быть пустыми."}), 400
            правки[field] = value
    # prize3_coins/threshold раньше при плохом вводе тихо пропускались (except:
    # pass) — форма говорила «Сохранено ✅», а число оставалось прежним. Тот же
    # принцип «отказ дешевле молчания», что и у title/prize1/prize2 выше:
    # проверяем ДО записи, отказом с сообщением, а не молчаливым no-op.
    if "prize3_coins" in data:
        try:
            правки["prize3_coins"] = max(0, int(data["prize3_coins"]))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad_input",
                            "message": "Монеты за 3 место — целым числом."}), 400
    if "threshold" in data:
        try:
            правки["threshold"] = max(0.0, float(str(data["threshold"]).replace(",", ".")))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad_input",
                            "message": "Порог участия — числом."}), 400
    for field, value in правки.items():
        db.update_raffle_field(r["id"], field, value)
    return jsonify({"ok": True})


@bp.route("/api/admin/raffle/start", methods=["POST"])
def api_admin_raffle_start():
    """Начать розыгрыш. Пока владелец его не начал, розыгрыша нет вовсе.

    Раньше приложение заводило розыгрыш само, как только предыдущий кончился, —
    и вкладка «Розыгрыши» висела у покупателей всегда, даже когда магазин
    ничего не разыгрывал."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if db.get_active_raffle():
        return jsonify({"ok": False, "error": "already"}), 409     # двух сразу не бывает
    days = inputs.целое(data.get("days") or 30, 30)
    days = min(365, max(1, days))
    try:
        prize3 = max(0, int(data.get("prize3_coins") or 500))
    except (TypeError, ValueError):
        prize3 = 500
    try:
        threshold = max(0.0, float(str(data.get("threshold") or 25).replace(",", ".")))
    except (TypeError, ValueError):
        threshold = 25.0
    rid = db.create_raffle(
        title=inputs._text(data.get("title"), 100) or "Розыгрыш месяца",
        prize1=inputs._text(data.get("prize1"), 100) or "Одноразка",
        prize2=inputs._text(data.get("prize2"), 100) or "Жидкость",
        prize3_coins=prize3, threshold=threshold, days=days)
    return jsonify({"ok": True, "raffle_id": rid})


@bp.route("/api/admin/raffle/photo", methods=["POST"])
def api_admin_raffle_photo():
    """Фото приза за конкретное место (1/2/3).

    Своя картинка у каждого места: 1-2 место обычно вещь (одноразка, жидкость),
    3-е чаще монеты — но точка продажи разыгрывает и им вещь, и общее фото на
    весь розыгрыш подписывало бы любое место одной и той же картинкой.

    Картинка получает file_id тем же способом, что и фото товара: отправляем её
    в чат владельцу тихо и забираем идентификатор. Второго способа хранить
    картинки в магазине нет, и заводить его ради розыгрыша незачем."""
    user = auth.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    try:
        place = int(request.form.get("place"))
    except (TypeError, ValueError):
        place = None
    if place not in (1, 2, 3):
        return jsonify({"ok": False, "error": "bad_place"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    if not photos.это_картинка(file):
        return jsonify({"ok": False, "error": "not_image",
                        "message": "Это не изображение. Нужен файл jpg, png или webp."}), 400
    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                            caption=f"🖼 Фото приза за {place} место сохранено", disable_notification=True)
        file_id, _thumb = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото приза: {e}")
        return jsonify({"ok": False, "error": "send_failed",
                        "message": "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи."}), 502
    db.update_raffle_field(r["id"], f"photo{place}", file_id)
    return jsonify({"ok": True, "place": place, "photo": file_id})


@bp.route("/api/admin/raffle/draw", methods=["POST"])
def api_admin_raffle_draw():
    """Подвести итоги сейчас и завершить розыгрыш.

    Нового не заводим: решать, идёт ли розыгрыш, — дело владельца."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    _draw_raffle(r)
    # Нового не заводим: решать, идёт ли розыгрыш, — дело владельца.
    return jsonify({"ok": True})


@bp.route("/api/admin/raffle/cancel", methods=["POST"])
def api_admin_raffle_cancel():
    """Отменить розыгрыш без итогов — для тестового или заведённого по ошибке.

    В отличие от «Подвести итоги»: победителей не выбираем, участникам и
    владельцу ничего не пишем, монеты не начисляем. Строка удаляется совсем,
    будто розыгрыша не было. Права — как у остальных ручек розыгрыша:
    /api/admin/raffle/ владельцу открыт целиком (auth._OWNER_ONLY), сюда
    заходит только тот, у кого прошёл общий шлюз."""
    data = request.get_json(force=True, silent=True) or {}
    if not auth.get_admin(data.get("initData", "")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    db.cancel_raffle(r["id"])
    return jsonify({"ok": True})


def _mask_id(uid):
    """Кто это был — не называя человека. Показывать полный id участникам
    незачем: по нему пишут в личку."""
    return "•••" + str(uid)[-3:]


def _winner_name(uid):
    """Имя победителя для витрины: из Telegram, а не обезличенный id.

    Победа в розыгрыше — повод для гордости, не утечка данных: имя и так
    видно любому в общих чатах Telegram. Участников (не победивших) это не
    касается — тех по-прежнему маскируем _mask_id, у победителей другая
    роль на экране."""
    u = db.get_user_row(uid)
    first_name = (u["first_name"] or "").strip() if (u and "first_name" in u.keys()) else ""
    if first_name:
        return first_name
    username = (u["username"] or "").strip() if (u and "username" in u.keys()) else ""
    return ("@" + username) if username else _mask_id(uid)


@bp.route("/api/admin/wheel/grant", methods=["POST"])
def api_admin_wheel_grant():
    """Тест: начислить админу 3 прокрута колеса."""
    data = request.get_json(force=True, silent=True) or {}
    admin = auth.get_admin(data.get("initData", ""))
    if not admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    uid = int(admin["id"])
    return auth._gate(admin, "wheel_grant_self", {"user_id": uid, "spins": 3},
                        f"+3 прокрута колеса админу id {uid}")
