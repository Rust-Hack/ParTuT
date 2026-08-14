"""
errors.py — сообщать владельцу о поломках.

Раньше ошибка печаталась в логи Render, а их никто не читает: поломка жила до
тех пор, пока на неё случайно не наткнутся. Так и вышло с кнопкой
«Подтвердить» — она молча не срабатывала неизвестно сколько времени.

Теперь приложение жалуется само, в личку супер-админам. Два правила:

  • одинаковые ошибки не спамят — повторы копятся и уходят одним сообщением
    не чаще, чем раз в ERROR_COOLDOWN секунд;
  • сам отчёт никогда не роняет то, что его вызвало: если Telegram недоступен,
    мы просто печатаем в лог, как и раньше.
"""

import datetime
import os
import threading
import time
import traceback

from config import SUPER_ADMIN_IDS

# Как часто можно повторять сообщение об ОДНОЙ И ТОЙ ЖЕ ошибке.
COOLDOWN = int(os.environ.get("ERROR_COOLDOWN", "600"))     # 10 минут

_seen = {}          # ключ ошибки -> {"at": когда отправляли, "skipped": сколько проглотили}
_lock = threading.Lock()


def _key(where, exc):
    """Одинаковыми считаем ошибки одного типа в одном месте — иначе один
    сломавшийся экран завалит владельца сотней одинаковых сообщений."""
    return f"{where}|{type(exc).__name__}|{exc}"[:300]


def _should_send(key):
    """Пора ли слать. Возвращает (слать?, сколько повторов проглочено)."""
    now = time.time()
    with _lock:
        rec = _seen.get(key)
        if rec and now - rec["at"] < COOLDOWN:
            rec["skipped"] += 1
            return False, 0
        skipped = rec["skipped"] if rec else 0
        _seen[key] = {"at": now, "skipped": 0}
        if len(_seen) > 200:                 # предохранитель от роста в памяти
            oldest = sorted(_seen.items(), key=lambda kv: kv[1]["at"])[:100]
            for k, _ in oldest:
                _seen.pop(k, None)
        return True, skipped


def report(tg, where, exc, extra=""):
    """Сообщить о поломке. tg — телебот; where — понятное место («POST /api/order»)."""
    try:
        key = _key(where, exc)
        send, skipped = _should_send(key)
        print(f"ОШИБКА [{where}]: {type(exc).__name__}: {exc}")
        if not send:
            return

        tail = traceback.format_exc(limit=3)
        if tail.strip() in ("NoneType: None", ""):        # вызвали не из except
            tail = ""
        when = datetime.datetime.utcnow().strftime("%H:%M UTC")

        lines = [f"⚠️ Сбой в приложении ({when})",
                 f"Место: {where}",
                 f"Что: {type(exc).__name__}: {exc}"]
        if extra:
            lines.append(extra)
        if skipped:
            lines.append(f"Повторилось ещё {skipped} раз с прошлого сообщения.")
        if tail:
            lines.append("")
            lines.append(tail[-600:])       # хвост следа: где именно оборвалось

        text = "\n".join(lines)
        for admin_id in SUPER_ADMIN_IDS:
            try:
                tg.send_message(admin_id, text)
            except Exception as e:
                print(f"Не смог сообщить о сбое админу {admin_id}: {e}")
    except Exception as e:
        # Отчёт об ошибке не имеет права сам стать ошибкой.
        print(f"Сбой в самом отчёте об ошибках: {e}")


def reset():
    """Забыть, о чём уже сообщали (нужно тестам)."""
    with _lock:
        _seen.clear()
