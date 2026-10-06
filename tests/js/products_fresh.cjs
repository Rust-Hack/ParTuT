// Список товаров — свежий при каждом входе (приёмка UX-48-01). Настоящий код
// из 01-core.js и 04-admin.js в песочнице Node, сервер — управляемый.
//
// В отчёте: продавец открыл «Управление», владелец с другого телефона списал
// все 5 кабелей, продавец нажал плитку «надо завезти» — и увидел «Свободно 5»:
// плитка звала openProducts() напрямую, а тот ждал прошлую, давно
// закончившуюся загрузку. Теперь свежий список берёт сам openProducts.
// Здесь же — порядок ответов: поздний ответ старого запроса не затирает свежий.
//
// Запуск: node tests/js/products_fresh.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const папка = path.join(__dirname, "..", "..", "partut", "webapp", "app");
const ядро = fs.readFileSync(path.join(папка, "01-core.js"), "utf8");
const админка = fs.readFileSync(path.join(папка, "04-admin.js"), "utf8");
function кусок(текст, от, до, что) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл ${что}`); process.exit(1); }
  return текст.slice(а, б);
}
const загрузка = кусок(ядро, "let админСписокСвеж", "// Когда точки последний раз", "загрузку списка в 01-core.js");
const входы = кусок(ядро, "function готовитьАдминку", "// ---- Разделы нижнего меню", "входы из меню в 01-core.js");
const открыть = кусок(админка, "async function openProducts() {", '$("productsClose").onclick', "openProducts в 04-admin.js");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };

// Сервер: склад и очередь ответов. сам — отвечать сразу; иначе тест сам
// решает, когда и чем ответить на каждый запрос.
function стенд({ сам = true } = {}) {
  const склад = { кабель: 5 };
  const ждут = [];
  const отрисовки = [];
  const узлы = {};
  const $ = (id) => узлы[id] || (узлы[id] = (() => {
    const к = new Set();
    return { id, innerHTML: "", textContent: "", классы: к,
      classList: { add: (...x) => x.forEach(y => к.add(y)), remove: (...x) => x.forEach(y => к.delete(y)), contains: (x) => к.has(x),
                   toggle: (x, да) => (да ? к.add(x) : к.delete(x)) } };
  })());
  const ответ = (тело) => ({ json: async () => тело });
  const снимок = () => ({ ok: true, products: [{ id: 1, name: "QA кабель", city: "Минск", stock: склад.кабель }] });
  const ctx = vm.createContext({
    $, initData: "qa", allProducts: [], adminProducts: [], _adminBoot: null,
    fetch: (url) => {
      if (url !== "/api/admin/products") return Promise.reject(new Error("неожиданный запрос " + url));
      if (сам) return Promise.resolve(ответ(снимок()));
      return new Promise(r => ждут.push({ ответить: (тело) => r(ответ(тело === undefined ? снимок() : тело)),
                                         оборвать: () => r({ json: async () => { throw new Error("обрыв"); } }) }));
    },
    applyAdminScope() {}, fetchBrands: async () => true, fetchModels: async () => true, загрузитьАрхив: async () => true,
    isOwner: () => true, loaderHtml: () => "…", renderAdmFilters() {},
    обновитьКнопкуПоставки() {}, обновитьКнопкуНового() {}, выбранныеЧипыВВиду() {},
    renderAdminList: () => отрисовки.push({ остаток: ctx.adminProducts.map(p => p.stock).join(),
      приглушён: $("adminList").classList.contains("updating"), свеж: vm.runInContext("админСписокСвеж", ctx) }),
    openOrders() {}, openSale() {}, остаткиПродажиПришли() {},
  });
  vm.runInContext(загрузка + "\n" + входы + "\n" + открыть, ctx);
  return { ctx, склад, ждут, отрисовки, $, js: (к) => vm.runInContext(к, ctx) };
}

(async () => {
  // ---- Сценарий отчёта: внешнее списание между двумя посещениями ----
  {
    const с = стенд();
    // «Управление» открылось и загрузило список (как openAdmin).
    await с.js("_adminBoot = Promise.all([fetchAdminProducts(), fetchBrands()])");
    проверка("продавец открыл «Управление» — у кабеля 5", с.js("adminProducts[0].stock") === 5);
    с.склад.кабель = 0;                                     // владелец списал все 5 с другого телефона
    await с.js("openProducts()");                             // плитка «надо завезти» / пункт «Товары» в «Управлении»
    const последняя = с.отрисовки.at(-1);
    проверка("UX-48-01: вход в «Товары» из «Управления» — список перечитан, у кабеля 0, а не прежние 5",
      последняя.остаток === "0" && с.js("adminProducts[0].stock") === 0, с.отрисовки);
    проверка("…и он не помечен «не обновился»", последняя.свеж === true && !последняя.приглушён, последняя);
  }
  {
    const с = стенд();
    await с.js("openProducts()");                              // первый вход — 5
    с.$("productsView").classList.remove("show");
    с.склад.кабель = 2;                                     // продали 3 с другой кассы
    await с.js("товарыИзМеню()");                               // нижняя вкладка
    проверка("нижняя вкладка «Товары» — тоже свежий список (2)", с.отрисовки.at(-1).остаток === "2", с.отрисовки);
  }
  // ---- Пока свежий идёт: прежний приглушён и не нажимается ----
  {
    const с = стенд({ сам: false });
    const первый = с.js("fetchAdminProducts()"); с.ждут[0].ответить(); await первый;   // прежний — 5
    с.склад.кабель = 0;
    const вход = с.js("openProducts()");
    await тик();
    const пока = с.отрисовки.at(-1);
    проверка("свежий ещё в пути — показан прежний (5), приглушён и не нажимается",
      пока && пока.остаток === "5" && пока.приглушён === true, с.отрисовки);
    с.ждут[1].ответить(); await вход;
    const после = с.отрисовки.at(-1);
    проверка("…пришёл — 0, приглушение снято", после.остаток === "0" && после.приглушён === false, после);
  }
  // ---- Не загрузился — несвежесть сказана, а не выдана за подтверждённое ----
  {
    const с = стенд({ сам: false });
    const первый = с.js("fetchAdminProducts()"); с.ждут[0].ответить(); await первый;
    const вход = с.js("openProducts()");
    await тик();
    с.ждут[1].оборвать(); await вход;
    const после = с.отрисовки.at(-1);
    проверка("сбой загрузки — прежний список с пометкой «не обновился» (свеж = false), не приглушён",
      после.остаток === "5" && после.свеж === false && после.приглушён === false, после);
  }
  // ---- Порядок ответов: поздний старый не затирает свежий ----
  {
    const с = стенд({ сам: false });
    const старый = с.js("fetchAdminProducts()");             // ушёл первым — увидит 5
    с.склад.кабель = 0;
    const новый = с.js("fetchAdminProducts()");              // ушёл вторым — увидит 0
    с.ждут[1].ответить();                                     // новый пришёл первым
    с.ждут[0].ответить({ ok: true, products: [{ id: 1, stock: 5 }] });   // старый — позже, со старыми 5
    const [а, б] = await Promise.all([старый, новый]);
    проверка("старый ответ пришёл позже нового — список остаётся свежим (0), а не 5",
      с.js("adminProducts[0].stock") === 0 && а === true && б === true, { остаток: с.js("adminProducts[0].stock"), а, б });
  }
  {
    const с = стенд({ сам: false });
    const старый = с.js("fetchAdminProducts()");
    const новый = с.js("fetchAdminProducts()");
    с.ждут[0].ответить({ ok: true, products: [{ id: 1, stock: 4 }] });   // старый пришёл первым — его и показываем
    await тик();
    проверка("старый пришёл первым — применён: он новее того, что было", с.js("adminProducts[0].stock") === 4);
    с.ждут[1].оборвать();                                     // а новый оборвался
    const [а, б] = await Promise.all([старый, новый]);
    проверка("…новый оборвался — список «не обновился»: итог по самому новому запросу",
      а === false && б === false && с.js("админСписокСвеж") === false, { а, б });
  }
  {
    const с = стенд({ сам: false });
    const старый = с.js("fetchAdminProducts()");
    const новый = с.js("fetchAdminProducts()");
    с.ждут[1].ответить({ ok: true, products: [{ id: 1, stock: 0 }] });
    await тик();
    с.ждут[0].оборвать();                                     // старый оборвался уже после свежего
    const [а, б] = await Promise.all([старый, новый]);
    проверка("старый оборвался после свежего — список свежий, без пометки «не обновился»",
      а === true && б === true && с.js("админСписокСвеж") === true && с.js("adminProducts[0].stock") === 0);
  }
  {
    const с = стенд({ сам: false });
    const идёт = с.js("fetchAdminProducts()");
    проверка("пока запрос в пути — «грузится» (для приглушения и «обновляю…»)", с.js("админГрузится") === 1);
    с.ждут[0].ответить(); await идёт;
    проверка("…ответ пришёл — уже не грузится", с.js("админГрузится") === 0);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
