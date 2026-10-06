// Нижнее меню по ролям и режимам — настоящий код из 01-core.js в песочнице Node.
//
// 3.10.2026: «🧾 Продажа» — вкладкой внизу (владелец не нашёл её в «Товарах»).
// 5.10.2026 (приёмка f58f7d2, решение владельца): у продавца — рабочее меню
// «Работа · Заказы · Продажа · Товары · Профиль»; у владельца — меню
// покупателя с «Продажей», рабочее — переключателем в профиле. Выбор режима —
// у каждого аккаунта свой. «Заказы», «Продажа», «Товары» открывают разделы
// поверх, не переключая вкладку; «Работа» — вкладка.
//
// Запуск: node tests/js/sale_tab.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const текст = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "01-core.js"), "utf8");
const а = текст.indexOf("const NAV = ["), б = текст.indexOf("function showTab(id) {");
if (а < 0 || б < 0) { console.log("❌ не нашёл меню в 01-core.js"); process.exit(1); }
const меню = текст.slice(а, б);
// Сводка дня (плитки) — из 04-admin.js: она и в «Управлении», и на «Работе».
const админка = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "04-admin.js"), "utf8");
const в = админка.indexOf("const МЕСТА_СВОДКИ"), г = админка.indexOf("// ----- Точка закрыта на время -----");
if (в < 0 || г < 0) { console.log("❌ не нашёл сводку дня в 04-admin.js"); process.exit(1); }
const сводка = админка.slice(в, г);
// Открытие «Товаров» — настоящее: свежий список при каждом входе берёт оно само
// (приёмка UX-48-01), а не тот, кто его зовёт.
const д = админка.indexOf("async function openProducts() {"), е = админка.indexOf('$("productsClose").onclick');
if (д < 0 || е < 0) { console.log("❌ не нашёл openProducts в 04-admin.js"); process.exit(1); }
const товарыКод = админка.slice(д, е);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// me — кто вошёл; режим — что лежит в хранилище этого аккаунта ("1"/"0"/нет).
// общее — хранилище телефона, которое делят аккаунты (Map); иначе своё.
// Окна разделов: classList и кнопка «Назад» (как в приложении — снимает show).
function окно(id, журнал) {
  const классы = new Set();
  const el = {
    id, innerHTML: "",
    classList: { add: (...к) => к.forEach(x => классы.add(x)), remove: (...к) => к.forEach(x => классы.delete(x)),
                 contains: (к) => классы.has(к) },
    querySelector: (sel) => (sel === ".viewhead button" ? { click: () => { журнал.закрыто.push(id); классы.delete("show"); } } : null),
  };
  return el;
}
function стенд(me, { список = [], загрузка = true, режим = null, uid = 7, общее = null, ручная = false } = {}) {
  const журнал = { вкладки: [], чек: 0, заказы: 0, товары: 0, права: 0, загрузок: 0, списокПриЧеке: null, html: "", закрыто: [] };
  const окна = Object.fromEntries(["ordersView", "saleView", "productsView", "saleFound", "saleDoc"].map(id => [id, окно(id, журнал)]));
  окна.pointBtn = { id: "pointBtn", hidden: false };              // точка покупателя в шапке
  окна.adminList = окно("adminList", журнал); окна.admCount = { id: "admCount", textContent: "" };
  // Места сводки дня: плитки — кнопки data-t, как их рисует renderToday.
  for (const id of ["todayCard", "workToday"]) {
    const плитки = [];
    окна[id] = {
      id, set innerHTML(html) { плитки.length = 0; for (const m of html.matchAll(/data-t="(\d+)"/g)) плитки.push({ dataset: { t: m[1] }, onclick: null }); },
      querySelectorAll: () => плитки, плитки,
    };
  }
  const ждут = [];
  const хранилище = общее || new Map();
  if (режим !== null) хранилище.set(`partut_work_mode_v1.${uid}`, режим);
  const кнопки = [];
  const nav = {
    set innerHTML(html) {
      журнал.html = html;
      кнопки.length = 0;
      for (const m of html.matchAll(/data-tab="([^"]+)"/g)) кнопки.push({ dataset: { tab: m[1] }, onclick: null });
    },
    querySelectorAll: () => кнопки,
  };
  const ctx = vm.createContext({
    me, activeTab: "catalog", adminProducts: список, $: (id) => (id === "nav" ? nav : окна[id] || null), loaderHtml: () => "…",
    cartCount: () => 0, showTab: (id) => журнал.вкладки.push(id),
    номерДляХранилища: () => (uid ? String(uid) : ""),
    localStorage: { getItem: (k) => (хранилище.has(k) ? хранилище.get(k) : null), setItem: (k, v) => хранилище.set(k, String(v)) },
    _adminBoot: null, fetchBrands: async () => true, applyAdminScope: () => журнал.права++,
    openOrders: () => { журнал.заказы++; окна.ordersView.classList.add("show"); },
    renderAdmFilters() {}, renderAdminList() {}, fetchModels: async () => true, загрузитьАрхив: async () => true,
    обновитьКнопкуПоставки() {}, обновитьКнопкуНового() {}, выбранныеЧипыВВиду() {},
    остаткиПродажиПришли: () => { журнал.остатки = (журнал.остатки || 0) + 1; },
    fetchAdminProducts: async () => {
      журнал.загрузок++;
      if (ручная) await new Promise(r => ждут.push(r));
      if (загрузка) ctx.adminProducts.push({ id: 1 });
      return загрузка;
    },
    openSale: () => { журнал.чек++; журнал.списокПриЧеке = ctx.adminProducts.length; окна.saleView.classList.add("show"); },
    isOwner: () => !!(me && me.role === "owner"), openStats: () => { журнал.статистика = (журнал.статистика || 0) + 1; },
    plural: (n, a, b, c) => (n === 1 ? a : n < 5 ? b : c), CUR: "Br",
    ordersStatusFilter: "all", admStockFilter: "all",
  });
  vm.runInContext(меню + "\n" + сводка + "\n" + товарыКод + "\nrenderNav();", ctx);
  const настоящиеТовары = ctx.openProducts;                         // считаем входы, открывает — настоящий
  ctx.openProducts = (...а) => { журнал.товары++; return настоящиеТовары(...а); };
  const тик = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };
  const открыты = () => ["ordersView", "saleView", "productsView"].filter(id => окна[id].classList.contains("show"));
  const активна = () => (журнал.html.match(/navbtn active" data-tab="([^"]+)"/) || [])[1];
  return { ctx, журнал, кнопки, хранилище, окна, открыты, активна, ждут, js: (к) => vm.runInContext(к, ctx),
           нажать: async (id) => { кнопки.find(k => k.dataset.tab === id).onclick(); await тик(); },
           плитка: async (где, i) => { окна[где].плитки[i].onclick(); await тик(); },
           шапкаСТочкой: () => !окна.pointBtn.hidden,
           вкладки: () => кнопки.map(k => k.dataset.tab).join() };
}

(async () => {
  {
    const с = стенд({ is_admin: false });
    проверка("покупатель — каталог, бонусы, корзина, избранное, профиль", с.вкладки() === "catalog,bonus,cart,fav,profile", с.вкладки());
  }
  {
    const с = стенд(null);
    проверка("пока не знаем, кто вошёл, — как у покупателя", с.вкладки() === "catalog,bonus,cart,fav,profile");
  }
  {
    const с = стенд({ is_admin: true, role: "seller", admin_city: "Минск" });
    проверка("продавец — рабочее меню: Работа · Заказы · Продажа · Товары · Профиль",
      с.вкладки() === "work,orders,sale,products,profile", с.вкладки());
    await с.нажать("sale");
    проверка("«Продажа» — открылся чек, вкладка не переключилась", с.журнал.чек === 1 && !с.журнал.вкладки.length, с.журнал);
    проверка("список управления не был загружен — сначала догружен, потом чек",
      с.журнал.загрузок >= 1 && с.журнал.списокПриЧеке >= 1, с.журнал);
    await с.нажать("orders");
    проверка("«Заказы» — открыт раздел заказов поверх, права выставлены, вкладка прежняя",
      с.журнал.заказы === 1 && с.журнал.права >= 1 && !с.журнал.вкладки.length, с.журнал);
    const загрузокДо = с.журнал.загрузок;
    await с.нажать("products");
    проверка("«Товары» — открыт раздел товаров поверх", с.журнал.товары === 1 && !с.журнал.вкладки.length, с.журнал);
    с.окна.productsView.querySelector(".viewhead button").click();          // «Назад»
    с.js("разделМеню = null");                                               // как наблюдатель в браузере
    await с.нажать("products");
    проверка("каждый новый вход в «Товары» перечитывает список — остатки не с первого открытия",
      с.журнал.загрузок === загрузокДо + 2, { загрузок: с.журнал.загрузок, было: загрузокДо });
    await с.нажать("work");
    проверка("«Работа» — это вкладка", с.журнал.вкладки.join() === "work", с.журнал.вкладки);
    с.js("заказовЖдут = 3; renderNav()");
    проверка("значок на «Заказах» — сколько ждут продавца", /data-tab="orders"[\s\S]*?badge-count">3</.test(с.журнал.html));
  }
  {
    const с = стенд({ is_admin: true, role: "seller" }, { режим: "0" });
    проверка("продавец выключил рабочее меню — меню покупателя с «Продажей» вместо «Избранного»",
      с.вкладки() === "catalog,bonus,cart,sale,profile", с.вкладки());
  }
  {
    const с = стенд({ is_admin: true, role: "owner" });
    проверка("владелец — как было: каталог, бонусы, корзина, продажа, профиль", с.вкладки() === "catalog,bonus,cart,sale,profile", с.вкладки());
    с.js("задатьРабочийРежим(true); renderNav()");
    проверка("владелец включил рабочее меню — оно и показано", с.вкладки() === "work,orders,sale,products,profile", с.вкладки());
    проверка("…и запомнено за этим аккаунтом", с.хранилище.get("partut_work_mode_v1.7") === "1");
  }
  {
    // Выбор одного аккаунта не переносится на другой в том же телефоне.
    const телефон = new Map();
    const владелец = стенд({ is_admin: true, role: "owner" }, { режим: "1", uid: 1, общее: телефон });
    проверка("режим «1» у аккаунта 1 — у него рабочее меню", владелец.вкладки() === "work,orders,sale,products,profile");
    const другой = стенд({ is_admin: true, role: "owner" }, { uid: 2, общее: телефон });
    проверка("аккаунт 2 в том же телефоне — своё (по умолчанию) меню покупателя", другой.вкладки() === "catalog,bonus,cart,sale,profile");
  }
  {
    // Список уже загружен. Раньше чек открывался по нему без запроса — и товар,
    // довезённый с другого телефона, не появлялся в «Продаже» до перезапуска.
    // Теперь (приёмка UX-48-01): чек — сразу, по прежнему; остатки перечитаны.
    const с = стенд({ is_admin: true, role: "owner" }, { список: [{ id: 5 }], ручная: true });
    с.кнопки.find(k => k.dataset.tab === "sale").onclick();
    проверка("список уже загружен — чек открыт сразу, по прежнему списку, свежий уже запрошен",
      с.журнал.чек === 1 && с.журнал.списокПриЧеке === 1 && с.журнал.загрузок === 1 && !с.журнал.остатки, с.журнал);
    с.ждут[0](); for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    проверка("…свежий пришёл — чек перерисован по нему", с.журнал.остатки === 1 && с.ctx.adminProducts.length === 2, с.журнал);
  }
  {
    const с = стенд({ is_admin: true, role: "owner" }, { список: [{ id: 5 }], ручная: true });
    с.кнопки.find(k => k.dataset.tab === "sale").onclick();
    с.окна.saleView.querySelector(".viewhead button").click();          // закрыл, не дождавшись
    с.ждут[0](); for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    проверка("закрыл продажу, пока остатки шли, — чек не перерисовывается в закрытом окне", !с.журнал.остатки, с.журнал);
  }
  {
    // «Товары» из «Управления» (пункт и плитка зовут openProducts напрямую) —
    // тоже свежий список при каждом входе: в отчёте плитка показывала «Свободно 5».
    const с = стенд({ is_admin: true, role: "owner" }, { список: [{ id: 5 }] });
    с.js("_adminBoot = Promise.resolve([true, true])");               // «Управление» давно загрузилось
    await с.js("openProducts()");
    с.окна.productsView.querySelector(".viewhead button").click();
    await с.js("openProducts()");
    проверка("UX-48-01: вход в «Товары» мимо меню (из «Управления») — каждый раз свежий список",
      с.журнал.загрузок === 2, с.журнал);
  }
  {
    const с = стенд({ is_admin: true, role: "owner" }, { загрузка: false });
    await с.нажать("sale");
    проверка("список не загрузился — чек всё равно открыт (по витрине)", с.журнал.чек === 1, с.журнал);
  }
  // ---- Разделы меню — как вкладки (замечание владельца 5.10.2026) ----
  {
    const с = стенд({ is_admin: true, role: "seller", admin_city: "Минск" }, { список: [{ id: 5 }] });
    с.ctx.activeTab = "work";
    await с.нажать("products");
    проверка("«Товары»: раздел открыт над меню, пункт подсвечен", с.открыты().join() === "productsView"
      && с.окна.productsView.classList.contains("vnav") && с.активна() === "products", { открыты: с.открыты(), активна: с.активна() });
    await с.нажать("products");
    проверка("второе нажатие «Товары» — ничего не дублирует", с.журнал.товары === 1 && с.открыты().join() === "productsView");
    await с.нажать("orders");
    проверка("«Заказы» при открытых «Товарах» — «Товары» закрыты своим «Назад», открыты «Заказы», одно окно",
      с.открыты().join() === "ordersView" && с.журнал.закрыто.join() === "productsView" && с.активна() === "orders",
      { открыты: с.открыты(), закрыто: с.журнал.закрыто, активна: с.активна() });
    проверка("…«Товары» больше не помечены как раздел меню", !с.окна.productsView.classList.contains("vnav"));
    await с.нажать("work");
    проверка("«Работа» при открытых «Заказах» — раздел закрыт, вкладка «Работа»",
      !с.открыты().length && с.журнал.закрыто.join() === "productsView,ordersView" && с.журнал.вкладки.join() === "work");
  }
  {
    // Продажа, список ещё не загружен: окно — сразу, с «загружаю», чек — когда список пришёл.
    const с = стенд({ is_admin: true, role: "seller" }, { ручная: true });
    с.кнопки.find(k => k.dataset.tab === "sale").onclick();
    проверка("«Продажа»: окно открыто сразу, пока список идёт, — с «загружаю»",
      с.окна.saleView.classList.contains("show") && с.окна.saleFound.innerHTML === "…" && с.журнал.чек === 0);
    с.ждут[0](); for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    проверка("…список пришёл — чек нарисован", с.журнал.чек === 1);
  }
  {
    const с = стенд({ is_admin: true, role: "seller" }, { ручная: true });
    с.кнопки.find(k => k.dataset.tab === "sale").onclick();
    с.окна.saleView.querySelector(".viewhead button").click();          // закрыл, не дождавшись
    с.ждут[0](); for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    проверка("закрыл продажу, пока грузилась, — окно само не открылось снова", с.журнал.чек === 0 && !с.окна.saleView.classList.contains("show"));
  }
  // ---- Точка покупателя в шапке: в рабочем меню её нет (пересмотр 5.10.2026) ----
  {
    проверка("покупатель — точка в шапке есть", стенд({ is_admin: false }).шапкаСТочкой());
    проверка("продавец в рабочем меню — точки покупателя в шапке нет",
      !стенд({ is_admin: true, role: "seller", admin_city: "Минск" }).шапкаСТочкой());
    проверка("продавец выключил рабочее меню — точка снова в шапке",
      стенд({ is_admin: true, role: "seller" }, { режим: "0" }).шапкаСТочкой());
    const с = стенд({ is_admin: true, role: "owner" });
    const былаТочка = с.шапкаСТочкой();
    с.js("задатьРабочийРежим(true); renderNav()");
    const вРабочем = с.шапкаСТочкой();
    с.js("задатьРабочийРежим(false); renderNav()");
    проверка("владелец: точка есть → включил рабочее меню — пропала → выключил — вернулась",
      былаТочка && !вРабочем && с.шапкаСТочкой(), { былаТочка, вРабочем, после: с.шапкаСТочкой() });
  }
  // ---- Плитки сводки: с «Работы» — как меню, из «Управления» — поверх, как раньше ----
  const день = "renderToday({ waiting: 2, to_issue: 1, revenue_today: 10, issued_today: 1, point_today: 0, out_stock: 1, low_stock: 0 })";
  {
    const с = стенд({ is_admin: true, role: "seller", admin_city: "Минск" }, { список: [{ id: 5 }] });
    с.ctx.activeTab = "work";
    с.js(день);
    await с.плитка("workToday", 0);                                  // «ждут подтверждения»
    проверка("«Работа» → «ждут подтверждения»: «Заказы» над меню, пункт подсвечен, фильтр «оплачены»",
      с.открыты().join() === "ordersView" && с.окна.ordersView.classList.contains("vnav")
        && с.активна() === "orders" && с.js("ordersStatusFilter") === "paid",
      { открыты: с.открыты(), активна: с.активна(), фильтр: с.js("ordersStatusFilter") });
    await с.нажать("work");
    await с.плитка("workToday", 3);                                  // «надо завезти»
    проверка("«Работа» → «надо завезти»: «Товары» над меню, подсвечены, фильтр «надо завезти»",
      с.открыты().join() === "productsView" && с.окна.productsView.classList.contains("vnav")
        && с.активна() === "products" && с.js("admStockFilter") === "need",
      { открыты: с.открыты(), активна: с.активна(), фильтр: с.js("admStockFilter") });
    await с.нажать("work");
    await с.плитка("workToday", 2);                                  // «выдано сегодня» у продавца
    проверка("«Работа» → «выдано сегодня» у продавца: его выданные заказы, над меню",
      с.открыты().join() === "ordersView" && с.активна() === "orders" && с.js("ordersStatusFilter") === "issued");
  }
  {
    const с = стенд({ is_admin: true, role: "owner" }, { список: [{ id: 5 }] });
    с.js(день);
    const былаАктивна = с.активна();
    await с.плитка("todayCard", 1);                                  // «к выдаче» в «Управлении»
    проверка("«Управление» → «к выдаче»: «Заказы» поверх, как раньше, меню не трогаем",
      с.открыты().join() === "ordersView" && !с.окна.ordersView.classList.contains("vnav")
        && с.активна() === былаАктивна && с.js("разделМеню") === null && с.js("ordersStatusFilter") === "confirmed",
      { открыты: с.открыты(), активна: с.активна(), было: былаАктивна, фильтр: с.js("ordersStatusFilter") });
    await с.плитка("todayCard", 2);                                  // «выдано сегодня» у владельца
    проверка("«выдано сегодня» у владельца — статистика", с.журнал.статистика === 1, с.журнал.статистика);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
