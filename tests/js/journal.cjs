// Журнал действий показывает изменения (приёмка ROLE-02). Настоящий код из
// 04-admin.js в песочнице Node; адреса сервера — из partut/web/*.py.
//
// Сервер давно пишет готовую строку «было → стало», а экран ждал старое
// «ключ=значение» и выбрасывал её: правка цены выглядела как «изменил товар · :»,
// публикация и новый вкус — пустыми. А новые действия («sale»,
// «stock/move/batch», «product/publish») шли в журнал без русских названий.
//
// Запуск: node tests/js/journal.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const корень = path.join(__dirname, "..", "..");
const админка = fs.readFileSync(path.join(корень, "partut", "webapp", "app", "04-admin.js"), "utf8");
function кусок(текст, от, до) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл: ${от}`); process.exit(1); }
  return текст.slice(а, б);
}
let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

const ctx = vm.createContext({ shelf: () => [{ id: 5, name: "QA чехол", city: "Минск" }] });
vm.runInContext(кусок(админка, "const LOG_NAMES", "async function openLog()"), ctx);
const строка = (action, details) => { ctx.__x = { action, details }; return vm.runInContext("logLine(__x)", ctx); };

// ---- Новые записи: готовая строка сервера — как есть ----
проверка("правка цены: ««QA Чехол PRO» · Минск: Цена 25.00 Br → 27.00 Br», а не «:»",
  строка("product/update", "«QA Чехол PRO» · Минск: Цена 25.00 Br → 27.00 Br") === "«QA Чехол PRO» · Минск: Цена 25.00 Br → 27.00 Br");
проверка("новый товар — с точками, ценами и первым приходом",
  /Минск — 25.00 Br, первый приход 10 шт/.test(строка("product/publish", "Новый товар «QA Чехол PRO» · QA: Минск — 25.00 Br, первый приход 10 шт; Туров — 25.00 Br, первый приход 5 шт")));
проверка("новый вариант — «варианты + Фиолетовый»",
  строка("product/variants/change", "«QA Чехол PRO» · Минск: варианты + Фиолетовый") === "«QA Чехол PRO» · Минск: варианты + Фиолетовый");
проверка("одиночное движение склада — с товаром и количеством, а не «шт»",
  строка("stock/move", "«PILOW TALK» · Минск · Черная вишня: Недостача −1") === "«PILOW TALK» · Минск · Черная вишня: Недостача −1");
проверка("правка описания — «название «Общая» → «Общая Про»»",
  /название «Общая» → «Общая Про»/.test(строка("model", "Описание «Общая Про»: название «Общая» → «Общая Про»; варианты + Мята · точек: 2")));

// ---- Старые записи «ключ=значение» — как раньше ----
проверка("старая правка цены: товар по номеру и «поле: значение»",
  строка("product/update", "id=5 · field=price · value=27") === "QA чехол · Минск — цена: 27", строка("product/update", "id=5 · field=price · value=27"));
проверка("статус заказа: «заказ #2 — выдан»", строка("order/status", "id=2 · action=issued") === "заказ #2 — выдан", строка("order/status", "id=2 · action=issued"));
проверка("прочие старые записи — по-русски: «категория: accessories», а не «code=accessories»",
  строка("category/update", "code=accessories") === "категория: accessories", строка("category/update", "code=accessories"));
проверка("…«покупатель: 7 · изменение: 50» у правки монет", строка("coins/adjust", "user_id=7 · delta=50") === "покупатель: 7 · изменение: 50",
  строка("coins/adjust", "user_id=7 · delta=50"));
проверка("разовая наводка каталога названа по-русски", vm.runInContext("LOG_NAMES['catalog/fix']", ctx) === "наводка каталога");

// ---- Названия: у всех действий, что пишутся в журнал ----
const сервер = fs.readFileSync(path.join(корень, "partut", "web", "server.py"), "utf8");
const блок = кусок(сервер, "_ADMIN_READS = {", "\n}\n");
const чтения = new Set([...блок.matchAll(/"(\/api\/admin\/[^"]+)"/g)].map(m => m[1]));
const маршруты = new Set();
for (const ф of fs.readdirSync(path.join(корень, "partut", "web")).filter(ф => ф.endsWith(".py")))
  for (const m of fs.readFileSync(path.join(корень, "partut", "web", ф), "utf8").matchAll(/@bp\.route\("(\/api\/admin\/[^"]+)"/g)) маршруты.add(m[1]);
const имена = vm.runInContext("LOG_NAMES", ctx);
const безИмени = [...маршруты].filter(м => !чтения.has(м)).map(м => м.replace("/api/admin/", "")).filter(д => !имена[д]);
проверка(`у каждого действия, что пишется в журнал, — русское название (их ${[...маршруты].filter(м => !чтения.has(м)).length})`,
  безИмени.length === 0, безИмени);
for (const д of ["sale", "sale/cancel", "stock/move/batch", "product/publish", "product/variants/change", "pause", "pause/open"])
  проверка(`«${д}» → «${имена[д]}»`, !!имена[д] && !/[a-z]\//.test(имена[д]));
проверка("просмотры «Товаров», «Работы», «Продажи», «Зарплаты» в журнал не пишутся (список «только чтение»)",
  ["/api/admin/archive", "/api/admin/pauses", "/api/admin/sales", "/api/admin/payroll"].every(м => чтения.has(м)));

дошлиДоКонца = true;
console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
process.exit(провалов ? 1 : 0);
