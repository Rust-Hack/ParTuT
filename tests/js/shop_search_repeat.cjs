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
  проверка("пустой поиск — всё на точке", витрина("").length === 3, витрина(""));
}

// ---------- Повторить заказ ----------
function магазин({ корзина = {}, точка = "Минск" } = {}) {
  const журнал = { вопросы: [], алерты: [], вкладка: null, сохранено: 0 };
  const ctx = vm.createContext({
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
  vm.runInContext(повтор, ctx);
  return { журнал, js: (с) => vm.runInContext(с, ctx), ctx };
}
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

дошлиДоКонца = true;
console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
process.exit(провалов ? 1 : 0);
