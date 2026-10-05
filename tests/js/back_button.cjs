// Кнопка «Назад» Telegram закрывает то, что сверху (пересмотр 5.10.2026).
// Настоящий код из 01-core.js в песочнице Node.
//
// Раньше верхним считался экран, последний в разметке. Но «📦 Склад» стоит в
// ней выше «Товаров», правка заказа и «Причина отказа» — выше «Заказов»;
// поверх их поднимает слой («ontop»). «Назад» закрывал нижний экран, а
// верхний оставался висеть.
//
// Запуск: node tests/js/back_button.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const текст = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "01-core.js"), "utf8");
const а = текст.indexOf("function верхний("), б = текст.indexOf("function updateBackButton");
if (а < 0 || б < 0) { console.log("❌ не нашёл выбор верхнего слоя в 01-core.js"); process.exit(1); }
const код = текст.slice(а, б);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// Экраны и шторки — в порядке настоящей разметки index.html, слои — из
// styles.css: таблица, написанная руками, разошлась бы с ними незаметно.
const папка = path.join(__dirname, "..", "..", "partut", "webapp");
const html = fs.readFileSync(path.join(папка, "index.html"), "utf8");
const css = fs.readFileSync(path.join(папка, "styles.css"), "utf8");
const слойИз = (re, что) => { const m = css.match(re); if (!m) { console.log(`❌ не нашёл слой: ${что}`); process.exit(1); } return +m[1]; };
const СЛОЙ = { view: слойИз(/\.view \{[^}]*z-index:\s*(\d+)/, ".view"), ontop: слойИз(/\.view\.ontop \{\s*z-index:\s*(\d+)/, ".view.ontop"),
               overlay: слойИз(/\.overlay \{[^}]*z-index:\s*(\d+)/, ".overlay") };
const метки = [...html.matchAll(/<div class="(view(?: ontop)?|overlay)" id="([^"]+)">/g)];
const РАЗМЕТКА = метки.map((m, i) => {
  const кусок = html.slice(m.index, i + 1 < метки.length ? метки[i + 1].index : html.length);
  const вид = m[1] === "overlay" ? "overlay" : "view";
  const шапка = кусок.match(/<div class="viewhead">([\s\S]*?)<\/div>/);
  return [m[2], вид, m[1] === "view ontop" ? СЛОЙ.ontop : СЛОЙ[вид], !!шапка && /<button/.test(шапка[1])];
});
const есть = (id) => РАЗМЕТКА.some(([x]) => x === id);
for (const id of ["payView", "adminView", "stockView", "statsView", "productsView", "oeditView", "orejView", "ordersView", "priceOverlay", "rowMoreOverlay"])
  if (!есть(id)) { console.log(`❌ в разметке нет ${id}`); process.exit(1); }
const место = (id) => РАЗМЕТКА.findIndex(([x]) => x === id);
проверка("разметка как в жалобе: «Склад» выше «Товаров», правка заказа выше «Заказов»",
  место("stockView") < место("productsView") && место("oeditView") < место("ordersView") && место("orejView") < место("ordersView"));
проверка("…а поверх их поднимает слой ontop", РАЗМЕТКА[место("stockView")][2] > РАЗМЕТКА[место("productsView")][2]
  && РАЗМЕТКА[место("oeditView")][2] > РАЗМЕТКА[место("ordersView")][2]);
проверка("у оплаты нет кнопки в шапке, у «Товаров» есть", !РАЗМЕТКА[место("payView")][3] && РАЗМЕТКА[место("productsView")][3]);
function верх(открыто) {
  const эл = РАЗМЕТКА.filter(([id]) => открыто.includes(id)).map(([id, вид, слой, кнопка]) =>
    ({ id, вид, слой, querySelector: (sel) => (sel === ".viewhead button" && кнопка ? {} : null) }));
  const ctx = vm.createContext({
    document: { querySelectorAll: (sel) => эл.filter(e => (sel === ".view.show" ? e.вид === "view" : e.вид === "overlay")) },
    getComputedStyle: (e) => ({ zIndex: String(e.слой) }),
  });
  vm.runInContext(код, ctx);
  const r = vm.runInContext("managedTopLayer()", ctx);
  return r ? `${r.type}:${r.el.id}` : null;
}

проверка("«Товары» → «📦 Склад»: «Назад» закрывает «Склад», а не «Товары» под ним",
  верх(["productsView", "stockView"]) === "view:stockView", верх(["productsView", "stockView"]));
проверка("«Заказы» → правка заказа: «Назад» закрывает правку",
  верх(["ordersView", "oeditView"]) === "view:oeditView", верх(["ordersView", "oeditView"]));
проверка("«Заказы» → причина отказа: «Назад» закрывает её",
  верх(["ordersView", "orejView"]) === "view:orejView", верх(["ordersView", "orejView"]));
проверка("«Управление» → «Заказы» (слой один): сверху тот, что ниже в разметке, — «Заказы»",
  верх(["adminView", "ordersView"]) === "view:ordersView", верх(["adminView", "ordersView"]));
проверка("«Управление» → «Статистика»: «Статистика»",
  верх(["adminView", "statsView"]) === "view:statsView");
проверка("открыта шторка — «Назад» закрывает её, а не экран под ней",
  верх(["productsView", "stockView", "priceOverlay"]) === "overlay:priceOverlay");
проверка("две шторки — последняя в разметке",
  верх(["priceOverlay", "rowMoreOverlay"]) === "overlay:rowMoreOverlay");
проверка("экран без кнопки в шапке (оплата) — «Назад» его не трогает",
  верх(["payView"]) === null);
проверка("ничего не открыто — «Назад» не нужен", верх([]) === null);

дошлиДоКонца = true;
console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
process.exit(провалов ? 1 : 0);
