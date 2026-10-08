"""
Замер нагрузки на стенде (tools/load/stand.py). Запускает его run.sh.

Печатает время ответа (медиана, 95-й процентиль, максимум) по каждому
сценарию, сколько новых подключений к базе стоил каждый раздел, и проверяет
итог гонки заказов: продано ровно столько, сколько было.

Разделы (ONLY=store,logins,… — только часть):
  store  — витрина: первый запрос, из кэша, 50 разом; 50 входов покупателей разом;
  logins — по 3 залпа из 50 первых и 50 повторных входов (у залпа большой разброс);
  owner  — товары всех точек, архив, статистика, зарплата, журнал;
  seller — сводка дня, заказы, продажи, товары своей точки;
  race   — 40 заказов разом за 10 последних штук одного вкуса;
  mix    — 20 с без пауз: 30 покупателей, продавец и владелец одновременно.

Что здесь нарочно и почему — каждое из этого однажды соврало в замере:
  • ответы, которые не нужны, не разбираются и не хранятся. Тысячи
    разобранных витрин раздували память замерщика, и уборка мусора
    останавливала все его потоки на две секунды разом — выглядело как
    «магазин замирает», а сервер при этом отвечал за 0,1 с;
  • соединения постоянные, как у браузера и прокси хостинга (KEEPALIVE=0 —
    новое на каждый запрос). С новым соединением на запрос у macOS кончаются
    порты быстрее, чем у сервера силы: замерщик падает сам;
  • в смешанной нагрузке отдельно «без первой секунды»: в первую секунду все
    32 участника стартуют разом, и максимум там — очередь старта, а не работа.
"""
import http.client
import json
import os
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

АДРЕС = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8793"
ИТОГ = sys.argv[2] if len(sys.argv) > 2 else "load_result.json"
РАЗДЕЛЫ = set((os.environ.get("ONLY") or "store,logins,owner,seller,race,mix").split(","))
ПОСТОЯННЫЕ = os.environ.get("KEEPALIVE", "1") == "1"

META = json.loads(urllib.request.urlopen(АДРЕС + "/__load/meta", timeout=60).read())
ВЛАДЕЛЕЦ, ПРОДАВЕЦ, ПОКУПАТЕЛИ = META["owner"], META["seller"], META["buyers"]
ЖУРНАЛ = []                 # (когда начат, мс, путь, код) — для поиска заминок
_соединение = threading.local()
итог = {}


def _http(метод, путь, data, заголовки):
    """Один запрос. Постоянное соединение — своё у каждого потока."""
    u = urllib.parse.urlsplit(АДРЕС)
    for попытка in (1, 2):
        c = getattr(_соединение, "c", None) if ПОСТОЯННЫЕ else None
        if c is None:
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=120)
            if ПОСТОЯННЫЕ:
                _соединение.c = c
        try:
            c.request(метод, путь, body=data, headers=заголовки)
            r = c.getresponse()
            ответ = r.status, r.read()
            if not ПОСТОЯННЫЕ:
                c.close()
            return ответ
        except (http.client.HTTPException, OSError):
            c.close()
            _соединение.c = None
            if попытка == 2:
                raise


def запрос(путь, тело=None, разбор=True):
    """(код, мс, ответ или None, байт, когда начат)."""
    когда = time.time()
    t0 = time.perf_counter()
    код, сырое = _http("POST" if тело is not None else "GET", путь,
                       None if тело is None else json.dumps(тело).encode(), {"Content-Type": "application/json"})
    мс = (time.perf_counter() - t0) * 1000
    ЖУРНАЛ.append((когда, мс, путь.split("?")[0], код))
    ответ = None
    if разбор and сырое:
        try:
            ответ = json.loads(сырое)
        except ValueError:
            pass
    return код, мс, ответ, len(сырое), когда


def сводка(имя, замеры, байт=None):
    мс = sorted(x[1] for x in замеры)
    коды = {}
    for x in замеры:
        коды[x[0]] = коды.get(x[0], 0) + 1
    p95 = мс[max(0, int(round(len(мс) * 0.95)) - 1)]
    print(f"{имя:<50} n={len(мс):<5} медиана {statistics.median(мс):5.0f} мс · p95 {p95:5.0f} · макс {мс[-1]:5.0f}"
          f" · коды {коды}" + (f" · {байт // 1024} КБ" if байт else ""), flush=True)
    return {"median": statistics.median(мс), "p95": p95, "max": мс[-1], "codes": коды}


def подряд(имя, n, f):
    замеры = [f() for _ in range(n)]
    return сводка(имя, замеры, замеры[-1][3])


def разом(имя, n, f):
    with ThreadPoolExecutor(max_workers=n) as пул:
        return сводка(имя, list(пул.map(f, range(n))))


def раздел(имя, действие):
    """Выполнить раздел и посчитать, сколько новых подключений к базе он стоил
    (без одного — того, которым стенд их считает)."""
    if имя not in РАЗДЕЛЫ:
        return
    до = запрос("/__load/sessions")[2]["sessions"]
    действие()
    time.sleep(1.5)                     # база засчитывает подключения не мгновенно
    стало = запрос("/__load/sessions")[2]["sessions"] - до - 1
    итог.setdefault("sessions", {})[имя] = стало
    print(f"   новых подключений к базе за раздел: {стало}", flush=True)


def витрина():
    print("=== Витрина покупателя ===")
    итог["products_cold"] = подряд("GET /api/products (первый, без кэша)", 1, lambda: запрос("/api/products"))
    итог["products_warm"] = подряд("GET /api/products (повтор, из кэша)", 10, lambda: запрос("/api/products"))
    итог["products_50"] = разом("GET /api/products × 50 разом", 50, lambda i: запрос("/api/products", разбор=False))
    итог["me_50"] = разом("POST /api/me × 50 разом (вход покупателей)", 50,
                          lambda i: запрос("/api/me", {"initData": ПОКУПАТЕЛИ[i]}, разбор=False))


def входы():
    # Первый вход — имени ещё нет, запись нужна. Повторный — обычный случай.
    print("=== Входы: по 3 залпа из 50 первых и повторных ===")
    первые, повторные = [], []
    for блок in range(3):
        люди = ПОКУПАТЕЛИ[250 + 50 * блок: 300 + 50 * блок]
        for куда in (первые, повторные):
            with ThreadPoolExecutor(max_workers=50) as пул:
                куда += list(пул.map(lambda init: запрос("/api/me", {"initData": init}, разбор=False), люди))
            time.sleep(1)
    итог["logins_first"] = сводка("POST /api/me × 50 разом, первый вход (3 залпа)", первые)
    итог["logins_repeat"] = сводка("POST /api/me × 50 разом, повторный вход (3 залпа)", повторные)


def владелец():
    print("=== Владелец ===")
    for ключ, имя, n, тело in (("admin_products", "POST /api/admin/products (все точки)", 5, {}),
                               ("archive", "POST /api/admin/archive", 5, {}),
                               ("stats_30", "POST /api/admin/stats 30 дней", 3, {"period": "30d"}),
                               ("stats_all", "POST /api/admin/stats всё время", 3, {"period": "all"}),
                               ("payroll", "POST /api/admin/payroll", 5, {}),
                               ("log", "POST /api/admin/log", 5, {})):
        путь = имя.split()[1]
        итог[ключ] = подряд(имя, n, lambda: запрос(путь, {"initData": ВЛАДЕЛЕЦ, **тело}))


def продавец():
    print("=== Продавец ===")
    for ключ, путь, n in (("today", "/api/admin/today", 10), ("orders", "/api/admin/orders", 10),
                          ("sales", "/api/admin/sales", 10), ("seller_products", "/api/admin/products", 5)):
        итог[ключ] = подряд(f"POST {путь} (продавец Минска)", n, lambda: запрос(путь, {"initData": ПРОДАВЕЦ}))


def гонка():
    print("=== Гонка: 40 заказов разом за 10 последних штук ===")
    метод = запрос("/api/delivery?city=" + urllib.parse.quote("Минск"))[2]["methods"][0]["id"]
    товар = META["race_product"]
    with ThreadPoolExecutor(max_workers=40) as пул:
        замеры = list(пул.map(lambda i: запрос("/api/order", {
            "initData": ПОКУПАТЕЛИ[100 + i], "items": [{"id": товар, "qty": 1, "flavor": "Мята"}],
            "delivery_method_id": метод, "payment_method": "cash", "phone": "+375291234567",
            "client_token": f"race-{i}-{time.time()}"}), range(40)))
    итог["race"] = сводка("POST /api/order × 40 разом", замеры)
    ошибки = {}
    for x in замеры:
        if not (x[2] and x[2].get("ok")):
            ошибки[(x[2] or {}).get("error", "?")] = ошибки.get((x[2] or {}).get("error", "?"), 0) + 1
    принято = sum(1 for x in замеры if x[0] == 200 and x[2] and x[2].get("ok"))
    разобрали = ошибки.get("sold_out", 0)
    товары = запрос("/api/admin/products", {"initData": ВЛАДЕЛЕЦ})[2]["products"]
    остаток = next(v["stock"] for p in товары if p["id"] == товар for v in p["variants"] if v["flavor"] == "Мята")
    итог["race_ok"] = принято == 10 and разобрали == 30 and остаток == 0
    print(f"   принято {принято}, «разобрали» {разобрали}, отказы сервера {ошибки}; остаток «Мяты» {остаток}"
          f" — {'ВЕРНО' if итог['race_ok'] else 'НЕВЕРНО'}", flush=True)


def смешанная():
    print("=== Смешанная нагрузка 20 с: 30 покупателей, продавец, владелец ===")
    запрос("/__load/server")              # сбросить серверный журнал
    ЖУРНАЛ.clear()
    начало = time.time()
    стоп = начало + 20
    сборы = {"buyer": [], "seller": [], "owner": []}
    замок = threading.Lock()

    def участник(роль, запросы):
        while time.time() < стоп:
            rs = [запрос(путь, тело, разбор=False) for путь, тело in запросы]
            with замок:
                сборы[роль] += rs

    потоки = [threading.Thread(target=участник, args=("buyer", [("/api/products", None),
                                                                 ("/api/me", {"initData": ПОКУПАТЕЛИ[200 + i]})]))
              for i in range(30)]
    потоки.append(threading.Thread(target=участник, args=("seller", [
        ("/api/admin/today", {"initData": ПРОДАВЕЦ}), ("/api/admin/orders", {"initData": ПРОДАВЕЦ}),
        ("/api/admin/products", {"initData": ПРОДАВЕЦ})])))
    потоки.append(threading.Thread(target=участник, args=("owner", [
        ("/api/admin/products", {"initData": ВЛАДЕЛЕЦ}), ("/api/admin/archive", {"initData": ВЛАДЕЛЕЦ}),
        ("/api/admin/stats", {"initData": ВЛАДЕЛЕЦ, "period": "30d"})])))
    for т in потоки:
        т.start()
    for т in потоки:
        т.join()
    for роль, подпись in (("buyer", "покупатели (витрина + вход)"), ("seller", "продавец (сводка, заказы, товары)"),
                          ("owner", "владелец (товары, архив, статистика)")):
        итог[f"mix_{роль}"] = сводка(f"смешанная: {подпись}", сборы[роль])
        итог[f"mix_{роль}_steady"] = сводка("   …без первой секунды", [x for x in сборы[роль] if x[4] >= начало + 1])

    # Где теряется время: у замерщика (с очередью) против сервера (только работа).
    сервер = запрос("/__load/server")[2]
    свои = [x for x in ЖУРНАЛ if x[2] != "/__load/server"]
    print(f"   запросов за 20 с: {len(свои)} (≈{len(свои) / 20:.0f} в секунду)")
    итог["mix_rps"] = len(свои) / 20
    по_путям = {}
    for _, мс, путь in сервер["rows"]:
        по_путям.setdefault(путь, []).append(мс)
    print("   работа сервера по запросам (без очереди):")
    for путь, v in sorted(по_путям.items(), key=lambda x: -sum(x[1])):
        v.sort()
        print(f"     {путь:<24} n={len(v):<5} медиана {statistics.median(v):6.1f} мс · p95 {v[max(0, int(len(v) * .95) - 1)]:6.1f}"
              f" · макс {v[-1]:7.1f} · всего {sum(v) / 1000:5.1f} с")
    медленные = sorted(свои, key=lambda x: -x[1])[:6]
    print("   самые долгие у замерщика (начало от старта стенда, с; мс; путь):",
          [(round(когда - сервер["zero"], 2), round(мс), путь) for когда, мс, путь, _ in медленные])


for имя, действие in (("store", витрина), ("logins", входы), ("owner", владелец), ("seller", продавец),
                      ("race", гонка), ("mix", смешанная)):
    раздел(имя, действие)
with open(ИТОГ, "w") as f:
    json.dump(итог, f, ensure_ascii=False, indent=1)
