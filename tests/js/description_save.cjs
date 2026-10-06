// Окно «📝 Описание» — настоящий код из 04-admin.js в песочнице Node.
// MG-01 (приёмка 2.10): сохранение описания A ещё в пути, окно закрыли и
// открыли в нём товар B, выбрали для B фото — запоздавший ответ A забирал
// это фото в товар A и закрывал окно B. Здесь сеть и поля под нашим
// управлением: ответ приходит тогда, когда мы скажем.
//
// Запуск: node tests/js/description_save.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const файл = path.join(__dirname, "..", "..", "partut", "webapp", "app", "04-admin.js");
const исходник = fs.readFileSync(файл, "utf8");
function кусок(с, до) {
  const a = исходник.indexOf(с), b = исходник.indexOf(до, a);
  if (a < 0 || b < 0) { console.log(`❌ не нашёл в 04-admin.js: ${с}`); process.exit(1); }
  return исходник.slice(a, b);
}
const объявления = кусок("let models = [], editingModelId", "\nasync function fetchModels(");
const окно = кусок("// Что сейчас в полях описания", "\nfunction editModel(");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = () => new Promise(r => setImmediate(r));

function стенд() {
  const узлы = {};
  const $ = (id) => узлы[id] || (узлы[id] = {
    id, value: "", textContent: "", disabled: false, scrollTop: 0,
    _cls: new Set(),
    get classList() { const s = this._cls; return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c) }; },
  });
  const запросы = [], фото = [], тосты = [], сообщения = [];
  const ctx = vm.createContext({
    $, console, JSON, Promise, Set, setImmediate, initData: "qa",
    isOwner: () => true, shelf: () => [], CAT_OPTS: [["liquid", "Жидкости"]],
    esc: (t) => String(t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    pickerValue: () => "", collectSpecs: () => ({}), ensureBrandExists: async () => {},
    fetchBrands: async () => {}, fetchFlavors: async () => {}, refreshAll: async () => {},
    обновитьКарточкуПослеОписания: () => {}, confirmMsg: (m, да) => да(),
    toast: (m) => тосты.push(m), alertMsg: (m) => сообщения.push(m), текстСбоя: (e) => "сбой: " + e,
    FormData: class { constructor() { this.поля = {}; } append(k, v) { this.поля[k] = typeof v === "object" ? v : String(v); } },   // как настоящий: числа строкой
    // Каждый POST описания ждёт, пока тест не ответит за него.
    fetch: (url, o) => new Promise(ответ => запросы.push({ url, тело: JSON.parse(o.body),
      ответить: (d) => ответ({ json: async () => d }) })),
    админФайл: async (url, fd) => { фото.push({ url, id: fd.поля.id, файл: fd.поля.file && fd.поля.file.name }); return true; },
  });
  vm.runInContext(объявления + `
    models = [
      { id: 1, category: "liquid", name: "Товар A", description: "про A", flavors: ["Мята"], specs: {} },
      { id: 2, category: "liquid", name: "Товар B", description: "про B", flavors: [], specs: {} },
    ];
    async function fetchModels() {}
    function editModel(id) {
      const m = models.find(x => x.id === id);
      editingModelId = id; modelFlavors = [...m.flavors]; modelPhotoFile = null;
      $("mdCat").value = m.category; $("mdName").value = m.name; $("mdDesc").value = m.description;
    }
  ` + окно, ctx);
  const js = (код) => vm.runInContext(код, ctx);
  return {
    js, $, запросы, фото, тосты, сообщения,
    открыть: async (id) => { await js(`открытьОписание(${id})`); },
    выбратьФото: (имя) => js(`modelPhotoFile = { name: ${JSON.stringify(имя)} }`),
    сохранить: () => $("mdSave").onclick(),
    закрыть: () => $("descClose").onclick(),
    открыто: () => $("descView").classList.contains("show"),
  };
}

(async () => {
  // ---- Обычное сохранение: своё фото, своё окно ----
  {
    const с = стенд();
    await с.открыть(1);
    с.$("mdDesc").value = "новое про A";
    с.выбратьФото("a.png");
    const идёт = с.сохранить();
    await тик();
    проверка("пока идёт сохранение, кнопка «Сохраняю…» и выключена",
      с.$("mdSave").disabled && с.$("mdSave").textContent === "Сохраняю…");
    с.запросы[0].ответить({ ok: true, id: 1, updated: 2 });
    await идёт;
    проверка("ушло описание A", с.запросы[0].тело.id === 1 && с.запросы[0].тело.description === "новое про A", с.запросы[0].тело);
    проверка("фото A загружено в A", JSON.stringify(с.фото) === JSON.stringify([{ url: "/api/admin/model/photo", id: "1", файл: "a.png" }]), с.фото);
    проверка("окно A закрылось само", !с.открыто());
    проверка("«Сохранено · точек: 2» — без имени товара (окно было его)", с.тосты.at(-1) === "Сохранено · точек: 2", с.тосты);
    проверка("кнопка снова «Сохранить описание»", !с.$("mdSave").disabled && с.$("mdSave").textContent === "Сохранить описание");
  }

  // ---- MG-01: A в пути → закрыли → открыли B, выбрали фото → ответ A ----
  {
    const с = стенд();
    await с.открыть(1);
    с.выбратьФото("a.png");
    const идётA = с.сохранить();
    await тик();
    с.закрыть();
    await с.открыть(2);
    с.$("mdDesc").value = "черновик B";
    с.выбратьФото("b.png");
    проверка("в окне B кнопка не «Сохраняю…»: в пути описание A, а не B",
      !с.$("mdSave").disabled && с.$("mdSave").textContent === "Сохранить описание", [с.$("mdSave").disabled, с.$("mdSave").textContent]);
    с.запросы[0].ответить({ ok: true, id: 1, updated: 1 });
    await идётA;
    проверка("MG-01: в A ушло фото, выбранное для A, а не для B",
      JSON.stringify(с.фото) === JSON.stringify([{ url: "/api/admin/model/photo", id: "1", файл: "a.png" }]), с.фото);
    проверка("MG-01: окно B осталось открытым", с.открыто());
    проверка("MG-01: фото, выбранное для B, ждёт сохранения B", с.js("modelPhotoFile && modelPhotoFile.name") === "b.png");
    проверка("MG-01: черновик B не тронут", с.$("mdDesc").value === "черновик B" && с.js("editingModelId") === 2);
    проверка("итог A назван по имени — окно уже о другом товаре", с.тосты.at(-1) === "«Товар A»: Сохранено · точек: 1", с.тосты);
    // Теперь сохраняем B — его фото уходит в B.
    const идётB = с.сохранить();
    await тик();
    с.запросы[1].ответить({ ok: true, id: 2, updated: 0 });
    await идётB;
    проверка("сохранение B: описание и фото — в B",
      с.запросы[1].тело.id === 2 && с.запросы[1].тело.description === "черновик B" && с.фото.at(-1).id === "2" && с.фото.at(-1).файл === "b.png",
      [с.запросы[1].тело, с.фото]);
    проверка("и окно B закрылось как обычно", !с.открыто());
  }

  // ---- Без фото у A: запоздавший ответ ничего не грузит ----
  {
    const с = стенд();
    await с.открыть(1);
    const идётA = с.сохранить();
    await тик();
    с.закрыть();
    await с.открыть(2);
    с.выбратьФото("b.png");
    с.запросы[0].ответить({ ok: true, id: 1, updated: 1 });
    await идётA;
    проверка("у A фото не выбирали — запоздавший ответ не грузит ничего", с.фото.length === 0, с.фото);
  }

  // ---- Повторное открытие того же товара, пока его описание в пути ----
  {
    const с = стенд();
    await с.открыть(1);
    const идёт = с.сохранить();
    await тик();
    с.закрыть();
    await с.открыть(1);
    проверка("A снова открыт, его описание ещё в пути — кнопка «Сохраняю…»",
      с.$("mdSave").disabled && с.$("mdSave").textContent === "Сохраняю…");
    await с.сохранить();
    проверка("второе сохранение того же товара не уходит, пока идёт первое", с.запросы.length === 1, с.запросы.length);
    с.запросы[0].ответить({ ok: true, id: 1, updated: 0 });
    await идёт;
    проверка("ответ пришёл в ДРУГОЕ открытие A — окно не закрыто, правки в нём не потеряны", с.открыто());
    проверка("кнопка снова доступна", !с.$("mdSave").disabled);
  }

  // ---- Отказ сервера, когда окно уже о другом ----
  {
    const с = стенд();
    await с.открыть(1);
    const идёт = с.сохранить();
    await тик();
    с.закрыть();
    await с.открыть(2);
    с.запросы[0].ответить({ ok: false, error: "exists", name: "Товар C" });
    await идёт;
    проверка("отказ по A назван по имени A и окно B не закрыто",
      /^«Товар A»: Товар «Товар C» с таким брендом уже есть/.test(с.сообщения.at(-1) || "") && с.открыто(), с.сообщения);
  }

  // ---- Быстро открыли A, потом B: старое открытие не перебивает новое ----
  {
    const с = стенд();
    let отпустить;
    с.js("fetchBrands = () => new Promise(r => { globalThis.__отпустить = r; })");
    const первое = с.открыть(1);
    отпустить = с.js("__отпустить");
    с.js("fetchBrands = async () => {}");
    await с.открыть(2);
    отпустить();
    await первое;
    проверка("открыли A, не дождавшись — открыли B: в окне B, не A", с.js("editingModelId") === 2 && с.$("mdName").value === "Товар B",
      [с.js("editingModelId"), с.$("mdName").value]);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\n❌ провалов: ${провалов}` : "\nВсё прошло");
  process.exitCode = провалов ? 1 : 0;
})();
