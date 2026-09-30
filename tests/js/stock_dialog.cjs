// Окно «📦 Склад» — настоящий код из 04-admin.js в песочнице Node, с подменой
// экрана и сети. Не браузер: проверяет обработчики, а не вёрстку. Зато ловит
// то, что серверные тесты не видят в принципе — что экран ОТПРАВИТ.
//
// Три случая из приёмки 30 сентября 2026 (S-01..S-03). Каждый проверяет
// ПРАВИЛЬНОЕ поведение: на коде до исправления эти проверки падают.
//
// Запуск: node tests/js/stock_dialog.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_stock_dialog_js.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const файл = path.join(__dirname, "..", "..", "partut", "webapp", "app", "04-admin.js");
const исходник = fs.readFileSync(файл, "utf8");
const начало = исходник.indexOf("// ----- Склад: приход, списание, пересчёт -----");
const конец = исходник.indexOf("// ----- Промокоды -----");
if (начало < 0 || конец < 0) { console.log("❌ не нашёл код окна склада в 04-admin.js"); process.exit(1); }
const код = исходник.slice(начало, конец);

let провалов = 0;
// Прогон, оборвавшийся на ожидании (промис, который никто не разрешит), Node
// завершает молча и с кодом 0 — выглядело бы как успех. Не дошли до конца —
// провал, и громкий.
let дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то повисло"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// Поддельный узел страницы: хранит то, что в него записали, и из разметки
// вытаскивает кнопки выбора (data-sc) и галочки заказов (data-so) — ровно то,
// на что код окна вешает обработчики.
function узел() {
  return {
    value: "", textContent: "", style: {}, disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    кнопки: [], галочки: [],
    set innerHTML(html) {
      this._html = html;
      this.кнопки = [...html.matchAll(/data-sc="(\w+)"/g)].map(m => ({ dataset: { sc: m[1] } }));
      this.галочки = [...html.matchAll(/<input type="checkbox" data-so="(\d+)"([^>]*)>/g)]
        .map(m => ({ dataset: { so: m[1] }, checked: /checked/.test(m[2]) }));
    },
    get innerHTML() { return this._html || ""; },
    querySelector() { return { textContent: "" }; },
    querySelectorAll(sel) {
      if (sel === "[data-sc]") return this.кнопки;
      if (sel === "[data-so]") return this.галочки;
      return [];
    },
    closest() { return { dataset: { srow: "" } }; },
  };
}

function стенд(товары) {
  const узлы = new Map();
  const $ = (id) => { if (!узлы.has(id)) узлы.set(id, узел()); return узлы.get(id); };
  const журнал = { posts: [], alerts: [], confirms: [], подтверждать: true };
  const ожидают = [];                                   // незавершённые запросы истории
  let ответПачки = () => ({ ok: true, done: [], failed: {} });
  const ctx = vm.createContext({
    $, shelf: () => товары, window: {}, initData: "qa", CSS: { escape: (x) => x },
    esc: (x) => String(x), qtyHtml: () => "", bindQty() {}, toast() {}, plural: (n, a) => a,
    alertMsg: (m) => журнал.alerts.push(m),
    confirmMsg: (m, да) => { журнал.confirms.push(m); if (журнал.подтверждать) да(); },
    refreshProducts: async () => {}, обновитьОстатокВКарточке() {},
    текстСбоя: (e) => String(e), ОТКАЗЫ: {},
    fetch: (url, opts) => {
      if (url === "/api/admin/stock/moves") {
        const тело = JSON.parse(opts.body);
        return new Promise((готово) => ожидают.push({ id: тело.id, ответить: (d) => готово({ ok: true, status: 200, json: async () => d }) }));
      }
      const тело = JSON.parse(opts.body);
      журнал.posts.push(тело);
      const d = ответПачки(тело);
      return Promise.resolve({ ok: true, status: 200, json: async () => d });
    },
  });
  vm.runInContext(код, ctx);
  const тик = () => new Promise((r) => setImmediate(r));
  // Нажать «Записать» и дать запросу уйти — не дожидаясь перечитывания
  // истории после записи (на него стенд отвечает только по команде).
  const записать = async () => { vm.runInContext(`stockЗаписать(false)`, ctx); for (let i = 0; i < 5; i++) await тик(); };
  return {
    ctx, $, журнал, ожидают, тик, записать,
    запустить: (js) => vm.runInContext(js, ctx),
    пачка: (f) => { ответПачки = f; },
    // Ответить на самый старый незавершённый запрос истории.
    история: async (заказы) => { ожидают.shift().ответить({ ok: true, moves: [], reserved_orders: заказы }); await тик(); await тик(); },
  };
}

(async () => {
  // ---------- S-01: пересчёт до загрузки невыданных заказов ----------
  {
    const с = стенд([{ id: 1, name: "Под", city: "Минск", stock: 3, reserved: 2, variants: [] }]);
    с.запустить(`openStockMove(1, "fix"); stockDraft[""] = "5";`);
    await с.записать();
    проверка("S-01: пока заказы грузятся, пересчёт не отправляется", с.журнал.posts.length === 0, с.журнал.posts);
    проверка("S-01: и сказано почему", /проверяю невыданные/i.test(с.журнал.alerts.join(" ")), с.журнал.alerts);

    await с.история([{ order_id: 7, status: "confirmed", method: "Самовывоз", flavor: "", qty: 2 }]);
    await с.записать();
    проверка("S-01: заказы есть, ответа «что посчитали» нет — не отправляется", с.журнал.posts.length === 0, с.журнал.posts);

    const кнопки = с.$("stockOrders").кнопки;
    кнопки.find(b => b.dataset.sc === "free").onclick();
    await с.записать();
    const строка = с.журнал.posts[0] && с.журнал.posts[0].items[0];
    проверка("S-01: ответ «только свободное» уходит явно", строка && строка.counted_scope === "free"
      && JSON.stringify(строка.counted_orders) === "[]", строка);
  }
  {
    // Запоздавший ответ про прошлый товар не должен лечь в окно нового.
    const с = стенд([{ id: 1, name: "А", city: "Минск", stock: 3, variants: [] },
                     { id: 2, name: "Б", city: "Минск", stock: 5, variants: [] }]);
    с.запустить(`openStockMove(1, "fix")`);
    с.запустить(`openStockMove(2, "fix")`);
    await с.история([{ order_id: 9, status: "paid", method: "Самовывоз", flavor: "", qty: 1 }]);   // это ответ про товар 1
    проверка("S-01: ответ про прошлый товар проигнорирован",
      с.запустить(`stockOrdersState`) === "loading" && с.запустить(`stockOrders.length`) === 0,
      { state: с.запустить(`stockOrdersState`), orders: с.запустить(`stockOrders.length`) });
    await с.история([]);                                                                            // ответ про товар 2
    проверка("S-01: свой ответ принят", с.запустить(`stockOrdersState`) === "ready");
    с.запустить(`stockDraft[""] = "5";`);
    await с.записать();
    const строка = с.журнал.posts[0] && с.журнал.posts[0].items[0];
    проверка("S-01: заказов нет — пересчёт уходит как «только свободное»", строка && строка.counted_scope === "free", строка);
  }

  // ---------- S-02: после «уже записано» тап не создаёт новую операцию ----------
  {
    const с = стенд([{ id: 1, name: "Под", city: "Минск", stock: 10, variants: [] }]);
    с.запустить(`openStockMove(1, "in");`);
    await с.история([]);
    с.запустить(`stockDraft[""] = "12"; stockTokens[""] = "original-request-key";`);
    с.пачка(() => ({ ok: true, done: [], failed: { "0": { error: "token_reused", message: "уже записано",
                                                          recorded: { reason: "in", delta: 10, flavor: "" } } } }));
    await с.записать();
    проверка("S-02: первая отправка — с прежним ключом", с.журнал.posts[0].items[0].token === "original-request-key");
    проверка("S-02: число из поля не осталось для повтора", с.запустить(`stockDraft[""]`) === undefined);
    проверка("S-02: видно, что уже записано", с.запустить(`stockConflicts[""]`) === "Приход +10", с.запустить(`stockConflicts[""]`));

    await с.записать();
    проверка("S-02: второй тап без нового числа ничего не отправил", с.журнал.posts.length === 1, с.журнал.posts.length);

    с.журнал.подтверждать = false;
    с.запустить(`stockDraft[""] = "2";`);
    await с.записать();
    проверка("S-02: новое число — сначала вопрос «это отдельная операция?»", с.журнал.confirms.length === 1, с.журнал.confirms);
    проверка("S-02: без согласия не отправлено", с.журнал.posts.length === 1, с.журнал.posts.length);

    с.журнал.подтверждать = true;
    с.пачка(() => ({ ok: true, done: [{ index: 0, id: 1, stock: 12, left: 12, delta: 2, replay: false }], failed: {} }));
    await с.записать();
    const второй = с.журнал.posts[1] && с.журнал.posts[1].items[0];
    проверка("S-02: с согласием — ровно добавленное и с новым ключом",
      второй && второй.qty === 2 && второй.token !== "original-request-key", второй);
    проверка("S-02: после удачной записи пометка снята", с.запустить(`stockConflicts[""]`) === undefined);
  }

  // ---------- S-03: заказ с двумя вкусами — одна галочка, как и отправляется ----------
  {
    const с = стенд([{ id: 1, name: "Жижа", city: "Минск", stock: 4, reserved: 3,
                       variants: [{ flavor: "A", stock: 2, reserved: 2 }, { flavor: "B", stock: 2, reserved: 1 }] }]);
    с.запустить(`openStockMove(1, "fix");`);
    await с.история([{ order_id: 7, status: "confirmed", method: "Самовывоз", flavor: "A", qty: 2 },
                     { order_id: 7, status: "confirmed", method: "Самовывоз", flavor: "B", qty: 1 }]);
    с.$("stockOrders").кнопки.find(b => b.dataset.sc === "all").onclick();
    const галочки = с.$("stockOrders").галочки;
    проверка("S-03: на заказ с двумя вкусами — одна галочка", галочки.length === 1, галочки.length);
    проверка("S-03: отмечена — считаются оба вкуса",
      с.запустить(`stockПосчитаноПодЗаказы("A")`) === 2 && с.запустить(`stockПосчитаноПодЗаказы("B")`) === 1);
    галочки[0].checked = false; галочки[0].onchange();
    проверка("S-03: снята — не считается ни один",
      с.запустить(`stockПосчитаноПодЗаказы("A")`) === 0 && с.запустить(`stockПосчитаноПодЗаказы("B")`) === 0);
    галочки[0].checked = true; галочки[0].onchange();
    с.запустить(`stockDraft["A"] = "4"; stockDraft["B"] = "3";`);
    await с.записать();
    const строки = (с.журнал.posts[0] || { items: [] }).items;
    проверка("S-03: что отмечено — то и отправлено, по каждому вкусу",
      строки.length === 2 && строки.every(x => x.counted_scope === "orders" && JSON.stringify(x.counted_orders) === "[7]"), строки);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})().catch((e) => { console.log("❌ упало: " + (e && e.stack || e)); process.exit(1); });
