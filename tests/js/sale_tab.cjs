// Вкладка «🧾 Продажа» в нижнем меню — настоящий код из 01-core.js в песочнице Node.
//
// 3.10.2026 владелец не нашёл продажу на точке: плитка жила в «Управление →
// Товары». Теперь у продавцов и владельца — вкладка внизу; покупатель её не
// видит. У продавца она на месте «Корзины» («Избранного» у него нет), у
// владельца — на месте «Избранного». Вкладка не переключает экран, а открывает чек, догрузив список
// управления, если его ещё нет.
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

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

function стенд(me, { список = [], загрузка = true } = {}) {
  const журнал = { вкладки: [], чек: 0, загрузок: 0, списокПриЧеке: null };
  const кнопки = [];
  const nav = {
    set innerHTML(html) {
      кнопки.length = 0;
      for (const m of html.matchAll(/data-tab="([^"]+)"/g)) кнопки.push({ dataset: { tab: m[1] }, onclick: null });
    },
    querySelectorAll: () => кнопки,
  };
  const ctx = vm.createContext({
    me, activeTab: "catalog", adminProducts: список, $: (id) => (id === "nav" ? nav : null),
    cartCount: () => 0, showTab: (id) => журнал.вкладки.push(id),
    fetchAdminProducts: async () => { журнал.загрузок++; if (загрузка) ctx.adminProducts.push({ id: 1 }); return загрузка; },
    openSale: () => { журнал.чек++; журнал.списокПриЧеке = ctx.adminProducts.length; },
  });
  vm.runInContext(меню + "\nrenderNav();", ctx);
  return { журнал, кнопки, нажать: async (id) => { await кнопки.find(k => k.dataset.tab === id).onclick(); } };
}

(async () => {
  {
    const с = стенд({ is_admin: false });
    проверка("покупатель — пять вкладок, «Продажи» нет", с.кнопки.map(k => k.dataset.tab).join() === "catalog,bonus,cart,fav,profile",
      с.кнопки.map(k => k.dataset.tab));
  }
  {
    const с = стенд(null);
    проверка("пока не знаем, кто вошёл, — как у покупателя", !с.кнопки.some(k => k.dataset.tab === "sale"));
  }
  {
    const с = стенд({ is_admin: true, role: "owner" });
    проверка("владелец — «Продажа» на месте «Избранного», «Корзина» остаётся",
      с.кнопки.map(k => k.dataset.tab).join() === "catalog,bonus,cart,sale,profile", с.кнопки.map(k => k.dataset.tab));
  }
  {
    const с = стенд({ is_admin: true, role: "dev" });
    проверка("разработчик — как владелец", с.кнопки.map(k => k.dataset.tab).join() === "catalog,bonus,cart,sale,profile");
  }
  {
    const с = стенд({ is_admin: true, role: "seller", admin_city: "Минск" });
    проверка("продавец — «Продажа» на месте «Корзины», «Избранного» нет",
      с.кнопки.map(k => k.dataset.tab).join() === "catalog,bonus,sale,profile", с.кнопки.map(k => k.dataset.tab));
    await с.нажать("sale");
    проверка("нажал «Продажа» — открылся чек, вкладка не переключилась", с.журнал.чек === 1 && !с.журнал.вкладки.length, с.журнал);
    проверка("список управления не был загружен — сначала догружен, потом чек",
      с.журнал.загрузок === 1 && с.журнал.списокПриЧеке === 1, с.журнал);
    await с.нажать("profile");
    проверка("другие вкладки — как раньше", с.журнал.вкладки.join() === "profile", с.журнал.вкладки);
  }
  {
    const с = стенд({ is_admin: true, role: "owner" }, { список: [{ id: 5 }] });
    await с.нажать("sale");
    проверка("список уже загружен — без лишнего запроса", с.журнал.загрузок === 0 && с.журнал.чек === 1, с.журнал);
  }
  {
    const с = стенд({ is_admin: true }, { загрузка: false });
    await с.нажать("sale");
    проверка("список не загрузился — чек всё равно открыт (по витрине)", с.журнал.загрузок === 1 && с.журнал.чек === 1, с.журнал);
  }
  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
