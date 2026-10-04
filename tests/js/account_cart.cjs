// Корзина — у каждого аккаунта своя (приёмка F-02, 5.10.2026). Настоящий код
// из 01-core.js в песочнице Node.
//
// Раньше корзина лежала в хранилище одной записью «partut_cart_v1» — без
// владельца. В одном Telegram (или браузере) второй аккаунт открывал
// приложение уже с корзиной первого. Теперь ключ — с номером пользователя;
// прежнюю общую запись не получает никто: чья она, неизвестно.
//
// Запуск: node tests/js/account_cart.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const текст = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "01-core.js"), "utf8");
function кусок(от, до) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл кусок: ${от}`); process.exit(1); }
  return текст.slice(а, б);
}
const корзина = кусок("const CART_KEY", "let useCoins");
const полка = кусок("function variantStock(p, flavor)", "// В списках");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

const товары = [
  { id: 1, city: "Минск", name: "Кабель", stock: 5, variants: [] },
  { id: 2, city: "Минск", name: "Чехол", stock: 4, variants: [{ flavor: "Белый", stock: 4 }] },
  { id: 3, city: "Туров", name: "Держатель", stock: 2, variants: [] },
];

// Одно хранилище на «телефон»: в нём могут открывать приложение разные аккаунты.
function телефон() {
  const м = new Map();
  return { getItem: (k) => (м.has(k) ? м.get(k) : null), setItem: (k, v) => м.set(k, String(v)),
           removeItem: (k) => м.delete(k), ключи: () => [...м.keys()], _м: м };
}
// Открыть приложение аккаунтом uid (null — не из Telegram) на точке city.
function открыть(хранилище, uid, город = "Минск") {
  const ctx = vm.createContext({
    city: город, allProducts: товары, alertMsg: () => {}, tgUser: uid ? { id: uid } : null,
    hasVariants: (p) => !!(p.variants && p.variants.length), localStorage: хранилище,
  });
  vm.runInContext(полка + "\n" + корзина + "\nthis.__cart = cart;", ctx);
  vm.runInContext("восстановитьКорзину()", ctx);
  return {
    ctx, корзина: () => Object.keys(ctx.__cart).sort().join(),
    положить: (ключ, позиция) => { ctx.__cart[ключ] = позиция; vm.runInContext("сохранитьКорзину()", ctx); },
  };
}

{
  // A → B → A на одной точке.
  const т = телефон();
  const а = открыть(т, 10006);
  а.положить("1", { product_id: 1, flavor: null, qty: 1 });
  а.положить("2::Белый", { product_id: 2, flavor: "Белый", qty: 1 });
  проверка("покупатель A собрал корзину: кабель и белый чехол", а.корзина() === "1,2::Белый", а.корзина());

  const б = открыть(т, 10001);
  проверка("F-02: аккаунт B на том же телефоне — корзина пуста, чужой нет", б.корзина() === "", б.корзина());
  б.положить("1", { product_id: 1, flavor: null, qty: 3 });

  const а2 = открыть(т, 10006);
  проверка("A вернулся — его корзина на месте, нетронута", а2.корзина() === "1,2::Белый"
    && а2.ctx.__cart["1"].qty === 1, а2.ctx.__cart);
  const б2 = открыть(т, 10001);
  проверка("…и у B своя: кабель × 3", б2.корзина() === "1" && б2.ctx.__cart["1"].qty === 3, б2.ctx.__cart);
  проверка("в хранилище — две записи, у каждой номер владельца",
    т.ключи().sort().join() === "partut_cart_v2.10001,partut_cart_v2.10006", т.ключи());
}
{
  // A на Минске, B на Турове — корзина по-прежнему по одной точке на аккаунт.
  const т = телефон();
  открыть(т, 10006).положить("1", { product_id: 1, flavor: null, qty: 1 });
  const б = открыть(т, 10001, "Туров");
  б.положить("3", { product_id: 3, flavor: null, qty: 1 });
  const а = открыть(т, 10006, "Минск");
  проверка("разные точки: A на Минске получил свой кабель, а не держатель B из Турова", а.корзина() === "1", а.корзина());
}
{
  // Старая общая запись — без владельца.
  const т = телефон();
  т.setItem("partut_cart_v1", JSON.stringify({ city: "Минск", items: { "1": { product_id: 1, flavor: null, qty: 2 } } }));
  const кто = открыть(т, 10001);
  проверка("F-02: старую общую корзину не получает первый вошедший", кто.корзина() === "", кто.корзина());
  проверка("…и она стёрта — следующему тоже не достанется", т.getItem("partut_cart_v1") === null);
}
{
  // Открыто не из Telegram — номера нет, в хранилище ничего не пишем.
  const т = телефон();
  const кто = открыть(т, null);
  кто.положить("1", { product_id: 1, flavor: null, qty: 1 });
  проверка("без номера пользователя корзина в хранилище не пишется", т.ключи().length === 0, т.ключи());
}

дошлиДоКонца = true;
console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
process.exit(провалов ? 1 : 0);
