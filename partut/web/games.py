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
    res = db.do_wheel_spin(uid, prize["coins"])     # списание+начисление за 1 запрос
    if res is None:
        return jsonify({"ok": False, "error": "no_spins"}), 400
    coins, spins = res
    db.inc_stat("wheel_spins", 1); db.inc_stat("wheel_paid", prize["coins"])
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
    balance = db.do_slot_spin(uid, bet, prize_coins)   # списание+приз за 1 запрос
    if balance is None:
        return jsonify({"ok": False, "error": "no_coins"}), 400
    db.inc_stat("slot_spins", 1); db.inc_stat("slot_bet", bet); db.inc_stat("slot_paid", prize_coins)

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


def _raffle_results(raffle):
    """Итоги завершённого розыгрыша: кто выиграл и кто вообще участвовал.

    Участники показываются наравне с победителями. Розыгрыш, в котором видно
    только троих счастливчиков, выглядит как розыгрыш без свидетелей."""
    try:
        winners = json.loads(raffle["winners"]) if raffle["winners"] else []
    except (TypeError, ValueError):
        winners = []
    winner_ids = {int(w.get("user_id") or 0) for w in winners}
    entrants = [int(u) for u in db.get_raffle_user_ids(raffle["id"])]
    return {"title": raffle["title"] or "Розыгрыш",
            "finished_at": raffle["finished_at"] or raffle["ends_at"],
            "photo": raffle["photo"] or "",
            "winners": [{"place": w.get("place"), "who": _mask_id(w.get("user_id")),
                         "prize": w.get("prize") or ""} for w in winners],
            # Победители уже названы выше — в списке участников их не повторяем.
            "participants": [_mask_id(u) for u in entrants if u not in winner_ids],
            "participants_count": len(entrants)}


def _raffle_public_from_state(st):
    r = st["raffle"]
    threshold = round(r["threshold"] or 0, 2)
    spent = round(st["spent"], 2)
    try:
        last_winners = json.loads(st["last_winners_raw"]) if st["last_winners_raw"] else []
    except (TypeError, ValueError):
        last_winners = []
    return {
        "id": r["id"], "title": r["title"] or "Розыгрыш месяца",
        "prize1": r["prize1"] or "", "prize2": r["prize2"] or "", "prize3_coins": r["prize3_coins"],
        "ends_at": r["ends_at"], "threshold": threshold,
        "photo": r["photo"] or "",
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
            return jsonify({"ok": True, "raffle": None,
                            "finished": _raffle_results(finished_raffle)})
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    return jsonify({"ok": True, "raffle": _raffle_public_from_state(st)})


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
        "photo": r["photo"] or "",
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
    for field in ("title", "prize1", "prize2"):
        if field in data:
            db.update_raffle_field(r["id"], field, str(data[field]).strip())
    if "prize3_coins" in data:
        try:
            db.update_raffle_field(r["id"], "prize3_coins", max(0, int(data["prize3_coins"])))
        except (TypeError, ValueError):
            pass
    if "threshold" in data:
        try:
            db.update_raffle_field(r["id"], "threshold", max(0.0, float(str(data["threshold"]).replace(",", "."))))
        except (TypeError, ValueError):
            pass
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
    """Фото разыгрываемого товара.

    Картинка получает file_id тем же способом, что и фото товара: отправляем её
    в чат владельцу тихо и забираем идентификатор. Второго способа хранить
    картинки в магазине нет, и заводить его ради розыгрыша незачем."""
    user = auth.get_admin(request.form.get("initData", ""))
    if not user:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    r = db.get_active_raffle()
    if not r:
        return jsonify({"ok": False, "error": "no_raffle"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no_file"}), 400
    try:
        msg = tgsend.tg.send_photo(int(user["id"]), file.read(),
                            caption="🖼 Фото приза сохранено", disable_notification=True)
        file_id, _thumb = photos._pick_photo_sizes(msg.photo)
    except Exception as e:
        print(f"Не смог обработать фото приза: {e}")
        return jsonify({"ok": False, "error": "send_failed"}), 500
    db.update_raffle_field(r["id"], "photo", file_id)
    return jsonify({"ok": True, "photo": file_id})


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


def _mask_id(uid):
    """Кто это был — не называя человека. Показывать полный id участникам
    незачем: по нему пишут в личку."""
    return "•••" + str(uid)[-3:]


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
