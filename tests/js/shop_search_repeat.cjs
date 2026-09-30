// Витрина покупателя — настоящий код из 01-core.js и 05-orders.js в
// песочнице Node. Две правки из проверки удобства 29 сентября:
//
// 1. Поиск расходника по модели устройства. Покупатель знает свой «XROS 3»,
//    а поиск смотрел только в название, бренд и вкусы — заполненная
//    совместимость (specs.fit) в нём не участвовала.
// 2. «Повторить заказ» молча выбрасывал собранную корзину — даже с другой
//    точки. Теперь при непустой корзине приложение спрашивает.
//
// Запуск: node tests/js/shop_search_repeat.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = path.join(__dirname, "..", "..", "partut", "webapp", "app");
function кусок(файл, от, до) {
  const текст = fs.readFileSync(path.join(app, файл), "utf8");
  const а = текст.indexOf(от), б = текст.indexOf(до);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл кусок в ${файл}: ${от}`); process.exit(1); }
  return текст.slice(а, б);
}
const поиск = кусок("01-core.js", "// ----- Поиск на витрине -----", "// ----- /Поиск на витрине -----");
const смена = кусок("01-core.js", "// ----- Смена точки -----", "// ----- /Смена точки -----");
const повтор = кусок("05-orders.js", "function repeatOrder(o, подтверждено) {", "// ============ Заказы (управление продавцом) ============");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

const товары = () => [
  { id: 1, name: "Картридж 0.8 Ом", brand: "Vaporesso", city: "Минск", category: "coils", stock: 10, variants: [],
    specs: { kind: "Картридж", resistance: 0.8, fit: "XROS 3, XROS Mini, XROS Nano" } },
  { id: 2, name: "Картридж Caliburn", brand: "Uwell", city: "Минск", category: "coils", stock: 5, variants: [],
    specs: { kind: "Картридж", resistance: 1.0, fit: "" } },
  { id: 3, name: "Husky Double Ice", brand: "Husky", city: "Минск", category: "liquid", stock: 4,
    variants: [{ flavor: "Мята", stock: 2 }, { flavor: "Малина", stock: 2 }], specs: { base: "Солевая" } },
  { id: 5, name: "Испаритель GTX", brand: "Vaporesso", city: "Минск", category: "coils", stock: 6,
    variants: [{ flavor: "0,6 Ом", stock: 3 }, { flavor: "1,2 Ом", stock: 3 }], specs: {} },
  { id: 4, name: "Картридж 0.8 Ом", brand: "Vaporesso", city: "Туров", category: "coils", stock: 3, variants: [],
    specs: { fit: "XROS 3" } },
];

// ---------- Поиск ----------
function витрина(запрос, настройки = {}) {
  const ctx = vm.createContext({
    allProducts: товары(), city: настройки.точка || "Минск", cat: настройки.категория || "",
    brandFilters: [], flavorFilters: [], search: запрос,
    flavorsOf: (p) => (p.variants || []).map(v => v.flavor).filter(Boolean),
  });
  vm.runInContext(поиск, ctx);
  return vm.runInContext("visibleProducts()", ctx).map(p => p.id);
}
{
  проверка("«xros mini» — картридж с этой совместимостью, хоть в названии модели нет", витрина("xros mini").join() === "1", витрина("xros mini"));
  проверка("«XROS3» без пробела — тот же картридж", витрина("XROS3").join() === "1", витрина("XROS3"));
  проверка("«xros 3» — только своя точка", витрина("xros 3").join() === "1", витрина("xros 3"));
  проверка("совместимость не заполнена — по ней не находится", !витрина("caliburn a2").length, витрина("caliburn a2"));
  проверка("по названию — как раньше", витрина("caliburn").join() === "2", витрина("caliburn"));
  проверка("по вкусу — как раньше", витрина("малина").join() === "3", витрина("малина"));
  проверка("по бренду — как раньше", витрина("uwell").join() === "2", витрина("uwell"));
  проверка("по типу из характеристик — «солевая»", витрина("солевая").join() === "3", витрина("солевая"));
  проверка("категория отбирает и при поиске", витрина("xros", { категория: "liquid" }).length === 0);
  проверка("пустой поиск — всё на точке", витрина("").length === 4, витрина(""));
  проверка("«0,8» и «0.8» — одно сопротивление из характеристик", витрина("0,8").join() === "1" && витрина("0.8").join() === "1",
    { запятая: витрина("0,8"), точка: витрина("0.8") });
  проверка("«0.6» находит вариант «0,6 Ом»", витрина("0.6").join() === "5", витрина("0.6"));
}

// ---------- Повторить заказ ----------
// Хранилище телефона — общее на «запуски» одного теста.
function хранилище() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
}
// Сеть для /api/set-city: "ok" — запомнил, "down" — нет связи, "gone" — такой точки нет.
function магазин({ корзина = {}, точка = "Минск", сервер = "ok", ls = хранилище(), помнит = точка } = {}) {
  const журнал = { вопросы: [], алерты: [], вкладка: null, сохранено: 0, setCity: [], ждалоДоЗапроса: [] };
  const ctx = vm.createContext({
    me: { city: помнит }, brandFilters: ["x"], prefetchDelivery() {}, initData: "qa", localStorage: ls,
    fetch: async (url, opts) => {
      const тело = JSON.parse(opts.body);
      журнал.setCity.push(тело.city);
      журнал.ждалоДоЗапроса.push(ls.getItem("partut_city_pending_v1"));
      if (сервер === "down") throw new TypeError("Failed to fetch");
      if (сервер === "gone") return { ok: false, status: 400, json: async () => ({ ok: false, error: "bad_input" }) };
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    },
    allProducts: товары(), cart: { ...корзина }, city: точка, plural,
    variantStock: (p, f) => ((p.variants || []).find(v => v.flavor === f) || { stock: 0 }).stock,
    esc: (s) => String(s), имяПозиции: (it) => it.name + (it.flavor ? ` · ${it.flavor}` : ""),
    cartKey: (id, flavor) => `${id}|${flavor || ""}`,
    alertMsg: (m) => журнал.алерты.push(m),
    confirmMsg: (m, да) => { журнал.вопросы.push(m); журнал.да = да; },
    сохранитьКорзину: () => { журнал.сохранено++; },
    $: () => ({ classList: { remove() {} }, textContent: "" }),
    updateFilterBtn() {}, renderGrid() {}, renderNav() {}, showTab: (t) => { журнал.вкладка = t; },
  });
  vm.runInContext(смена + "\n" + повтор, ctx);
  return { журнал, js: (с) => vm.runInContext(с, ctx), ctx, ls };
}
const тик = () => new Promise((r) => setImmediate(r));
const заказМинск = { city: "Минск", items: [{ id: 3, name: "Husky", flavor: "Мята", qty: 1 }, { id: 1, name: "Картридж", qty: 2 }] };
const заказТуров = { city: "Туров", items: [{ id: 4, name: "Картридж", qty: 1 }] };
{
  const м = магазин();
  м.ctx.o = заказМинск; м.js("repeatOrder(o)");
  проверка("пустая корзина — без вопроса, товары заказа в корзине",
    !м.журнал.вопросы.length && Object.keys(м.ctx.cart).length === 2 && м.журнал.вкладка === "cart", м.ctx.cart);
}
{
  const м = магазин({ корзина: { "2|": { product_id: 2, flavor: null, qty: 1 }, "3|Малина": { product_id: 3, flavor: "Малина", qty: 1 } } });
  м.ctx.o = заказМинск; м.js("repeatOrder(o)");
  проверка("корзина не пуста — сначала вопрос", м.журнал.вопросы.length === 1
    && /В корзине уже 2 позиции\. Заменить их товарами из этого заказа\?/.test(м.журнал.вопросы[0]), м.журнал.вопросы);
  проверка("та же точка — про точку ничего не сказано", !/точке/.test(м.журнал.вопросы[0]));
  проверка("пока не ответили — корзина прежняя", Object.keys(м.ctx.cart).join() === "2|,3|Малина" && !м.журнал.сохранено, м.ctx.cart);
  м.журнал.да();
  проверка("ответили «да» — корзина заменена товарами заказа", Object.keys(м.ctx.cart).sort().join() === "1|,3|Мята" && м.журнал.сохранено === 1, м.ctx.cart);
}
{
  const м = магазин({ корзина: { "2|": { product_id: 2, flavor: null, qty: 1 } } });
  м.ctx.o = заказТуров; м.js("repeatOrder(o)");
  проверка("заказ с другой точки — вопрос говорит, что корзина переключится",
    /Заказ был на точке «Туров» — корзина переключится на неё\./.test(м.журнал.вопросы[0] || ""), м.журнал.вопросы);
  проверка("без ответа точка не меняется", м.ctx.city === "Минск");
  м.журнал.да();
  проверка("после «да» — точка «Туров» и товар заказа в корзине", м.ctx.city === "Туров" && Object.keys(м.ctx.cart).join() === "4|", { город: м.ctx.city, корзина: м.ctx.cart });
}
{
  // Недоступное — как и раньше: сказано после добавления.
  const м = магазин();
  м.ctx.o = { city: "Минск", items: [{ id: 3, name: "Husky", flavor: "Вишня", qty: 1 }, { id: 2, name: "Caliburn", qty: 1 }] };
  м.js("repeatOrder(o)");
  проверка("недоступное названо, доступное — в корзине",
    /Сейчас недоступно: Husky · Вишня/.test(м.журнал.алерты.join(" ")) && Object.keys(м.ctx.cart).join() === "2|", м.журнал.алерты);
}
{
  const м = магазин({ корзина: { "2|": { product_id: 2, flavor: null, qty: 1 } } });
  м.ctx.o = { city: "Минск", items: [{ id: 99, name: "Снятый", qty: 1 }] };
  м.js("repeatOrder(o)");
  проверка("всё недоступно — без вопроса и без замены корзины",
    !м.журнал.вопросы.length && /недоступны/.test(м.журнал.алерты.join(" ")) && Object.keys(м.ctx.cart).join() === "2|");
}

(async () => {
  // ---------- BR-01: повтор другой точки переживает перезапуск ----------
  {
    const ls = хранилище();
    const м = магазин({ ls, корзина: { "2|": { product_id: 2, flavor: null, qty: 1 } } });
    м.ctx.o = заказТуров; м.js("repeatOrder(o)"); м.журнал.да(); await тик(); await тик();
    проверка("BR-01: точка сменилась и запомнена — на сервер ушёл выбор «Туров»",
      м.ctx.city === "Туров" && м.ctx.me.city === "Туров" && м.журнал.setCity.join() === "Туров", { город: м.ctx.city, сервер: м.журнал.setCity });
    проверка("BR-01: выбор лежал в телефоне ещё до запроса", м.журнал.ждалоДоЗапроса[0] === "Туров");
    проверка("BR-01: сервер запомнил — в телефоне ждать нечего", ls.getItem("partut_city_pending_v1") === null);
    проверка("BR-01: корзина сохранена с новой точкой", м.журнал.сохранено >= 1 && Object.keys(м.ctx.cart).join() === "4|");
  }
  {
    // Сервер недоступен: выбор ждёт в телефоне, при запуске берётся он и досылается.
    const ls = хранилище();
    const м = магазин({ ls, сервер: "down" });
    м.ctx.o = заказТуров; м.js("repeatOrder(o)"); await тик(); await тик();
    проверка("BR-01: связи нет — выбор «Туров» ждёт в телефоне", ls.getItem("partut_city_pending_v1") === "Туров");
    const запуск = магазин({ ls, помнит: "Минск", точка: "Минск" });      // сервер помнит Минск
    запуск.js("применитьЖдущуюТочку()"); await тик(); await тик();
    проверка("BR-01: при запуске — «Туров», а не Минск с сервера", запуск.ctx.city === "Туров", запуск.ctx.city);
    проверка("BR-01: и выбор дослан на сервер", запуск.журнал.setCity.join() === "Туров" && ls.getItem("partut_city_pending_v1") === null,
      { сервер: запуск.журнал.setCity, ждёт: ls.getItem("partut_city_pending_v1") });
  }
  {
    // Точки больше нет: сервер отказал — ждать нечего, досылать тоже.
    const ls = хранилище();
    const м = магазин({ ls, сервер: "gone" });
    м.ctx.o = заказТуров; м.js("repeatOrder(o)"); await тик(); await тик();
    проверка("BR-01: точки больше нет — ждущий выбор снят, не досылается вечно", ls.getItem("partut_city_pending_v1") === null);
  }
  {
    // Та же точка — сервер не дёргаем.
    const м = магазин();
    м.ctx.o = заказМинск; м.js("repeatOrder(o)"); await тик();
    проверка("BR-01: та же точка — без лишнего запроса", м.журнал.setCity.length === 0, м.журнал.setCity);
  }
  {
    // Отказ от замены — ничего не меняется и никуда не уходит.
    const м = магазин({ корзина: { "2|": { product_id: 2, flavor: null, qty: 1 } } });
    м.ctx.o = заказТуров; м.js("repeatOrder(o)"); await тик();
    проверка("BR-01: отказались — точка и корзина прежние, запросов нет",
      м.ctx.city === "Минск" && Object.keys(м.ctx.cart).join() === "2|" && !м.журнал.setCity.length);
  }

  // ---------- BR-02: неполный повтор назван ----------
  {
    const м = магазин();
    м.ctx.o = { city: "Минск", items: [{ id: 3, name: "Husky", flavor: "Мята", qty: 5 }, { id: 2, name: "Caliburn", qty: 1 }] };
    м.js("repeatOrder(o)");
    const а = м.журнал.алерты.join(" ");
    проверка("BR-02: осталось меньше — сказано «2 из 5»", /Меньше, чем в заказе[\s\S]*Husky · Мята — 2 из 5/.test(а), м.журнал.алерты);
    проверка("BR-02: в корзине сколько есть, вкус тот же", м.ctx.cart["3|Мята"] && м.ctx.cart["3|Мята"].qty === 2 && !м.ctx.cart["3|Малина"], м.ctx.cart);
    проверка("BR-02: целиком доступное без пометок", !/Caliburn/.test(а));
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})().catch((e) => { console.log("❌ упало: " + (e && e.stack || e)); process.exit(1); });
