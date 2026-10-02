// Новый товар — настоящий код 08-new-product.js в песочнице Node, с подменой
// экрана, хранилища телефона и сети. Проверяет то, что серверные тесты не
// видят: что экран ОТПРАВИТ, что он запомнит в черновике и когда.
//
// Экран здесь «нажимается» по-настоящему: HTML, который рисует мастер,
// разбирается в дерево, кнопки получают свои обработчики, поля — значения.
// Помощники формы (выбор бренда, характеристики, слово варианта) — тоже
// настоящие: берутся из 06-catalog.js и 01-core.js по имени.
//
// Главное: ключ публикации лежит в черновике ДО запроса, и повтор после
// любого сбоя уходит с тем же ключом; черновик переживает перезапуск вместе
// с фото (по file_id); сбой одного фото не трогает остальные; итог не
// говорит «опубликовано», пока сервер этого не подтвердил.
//
// Запуск: node tests/js/new_product.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ПАПКА = path.join(__dirname, "..", "..", "partut", "webapp", "app");
const читать = (f) => fs.readFileSync(path.join(ПАПКА, f), "utf8");
const КОД = читать("08-new-product.js");
const ЯДРО = читать("01-core.js"), АДМИНКА = читать("04-admin.js"), КАТАЛОГ = читать("06-catalog.js");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то повисло"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// Определение по имени: «function имя(…) {…}» до закрывающей скобки тела или
// «const имя = …;» до точки с запятой вне скобок.
function взять(src, имя) {
  const i = [`function ${имя}(`, `const ${имя} =`].map(н => src.indexOf(н)).find(x => x >= 0);
  if (i === undefined) throw new Error(`не нашёл ${имя}`);
  const функция = src.startsWith("function", i);
  let глубина = 0;
  for (let k = i; k < src.length; k++) {
    const c = src[k];
    if ("([{".includes(c)) глубина++;
    else if (")]}".includes(c)) { глубина--; if (функция && глубина === 0 && c === "}") return src.slice(i, k + 1); }
    else if (!функция && c === ";" && глубина === 0) return src.slice(i, k + 1);
  }
  throw new Error(`не закрыл ${имя}`);
}
const ПОМОЩНИКИ = [
  взять(ЯДРО, "esc"), взять(ЯДРО, "specsOf"), взять(ЯДРО, "catHasFlavors"), взять(ЯДРО, "catVariant"),
  взять(ЯДРО, "catVariantMany"), взять(АДМИНКА, "plural"),
  взять(ЯДРО, "_ДЕС"), взять(ЯДРО, "разобратьСписок"), взять(ЯДРО, "ключВарианта"),
  взять(КАТАЛОГ, "pickerHtml"), взять(КАТАЛОГ, "bindPicker"), взять(КАТАЛОГ, "pickerValue"),
  взять(КАТАЛОГ, "brandNames"), взять(КАТАЛОГ, "specFieldsHtml"), взять(КАТАЛОГ, "collectSpecs"),
  взять(КАТАЛОГ, "ФОРМЫ_ВАРИАНТА"), взять(КАТАЛОГ, "формыВарианта"), взять(КАТАЛОГ, "catName"),
].join("\n");

// ---------- Маленький DOM: HTML → дерево узлов ----------
const ПУСТЫЕ = new Set(["input", "img", "br", "hr", "meta", "link", "source"]);
const раскод = (s) => String(s).replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" }[e]));
function атрибуты(s) {
  const a = {};
  const re = /([^\s=/]+)(?:\s*=\s*"([^"]*)")?/g;
  let m;
  while ((m = re.exec(s))) a[m[1].toLowerCase()] = m[2] === undefined ? "" : раскод(m[2]);
  return a;
}
class Узел {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = attrs; this.children = []; this.id = attrs.id || "";
    this.dataset = {};
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    this.value = attrs.value ?? ""; this.checked = "checked" in attrs; this.disabled = "disabled" in attrs;
    this.hidden = "hidden" in attrs; this.style = {}; this.слушатели = {}; this._html = ""; this.scrollTop = 0;
    this.onclick = this.onchange = this.oninput = null; this.files = null;
    const классы = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
    this._классы = классы;
    this.classList = { add: (k) => классы.add(k), remove: (k) => классы.delete(k), contains: (k) => классы.has(k),
                       toggle: (k, on) => ((on ?? !классы.has(k)) ? классы.add(k) : классы.delete(k)) };
  }
  addEventListener(t, f) { (this.слушатели[t] = this.слушатели[t] || []).push(f); }
  focus() {} blur() {}
  click() { if (this.onclick) this.onclick({ target: this }); }
  set innerHTML(html) { this._html = String(html); this.children = []; разобрать(this._html, this); }
  get innerHTML() { return this._html; }
  set textContent(t) { this.children = [{ text: String(t) }]; this._html = String(t); }
  get textContent() { return this.children.map(ч => (ч instanceof Узел ? ч.textContent : раскод(ч.text))).join(""); }
  *потомки() { for (const ч of this.children) if (ч instanceof Узел) { yield ч; yield* ч.потомки(); } }
  querySelectorAll(sel) {
    const части = sel.split(",").map(s => s.trim());
    return [...this.потомки()].filter(у => части.some(ч => подходит(у, ч)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function подходит(у, sel) {
  let m;
  if ((m = sel.match(/^\[([^\]=]+)\]$/))) return m[1] in у.attrs;
  if ((m = sel.match(/^#(.+)$/))) return у.id === m[1];
  if ((m = sel.match(/^\.(.+)$/))) return у._классы.has(m[1]);
  if ((m = sel.match(/^([a-z]+)$/))) return у.tagName === m[1].toUpperCase();
  throw new Error("селектор не поддержан: " + sel);
}
function разобрать(html, корень) {
  const стек = [корень];
  const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith("<!--")) continue;
    const верх = стек[стек.length - 1];
    if (m[4] !== undefined) { верх.children.push({ text: m[4] }); continue; }
    const tag = m[2].toLowerCase();
    if (m[1]) {
      for (let i = стек.length - 1; i > 0; i--) if (стек[i].tagName === tag.toUpperCase()) { стек.length = i; break; }
      continue;
    }
    const у = new Узел(tag, атрибуты(m[3]));
    верх.children.push(у);
    if (!ПУСТЫЕ.has(tag)) стек.push(у);
  }
  // Значения, которые задаются содержимым, а не атрибутом.
  for (const у of корень.потомки()) {
    if (у.tagName === "TEXTAREA") у.value = у.textContent;
    if (у.tagName === "SELECT") {
      const опции = [...у.потомки()].filter(x => x.tagName === "OPTION");
      const выбрана = опции.filter(x => "selected" in x.attrs).pop() || опции[0];
      у.value = выбрана ? ("value" in выбрана.attrs ? выбрана.attrs.value : выбрана.textContent.trim()) : "";
    }
  }
}
const нажать = (у) => { if (!у) throw new Error("нет такой кнопки"); if (!у.disabled) у.click(); };
function ввести(у, v) {
  if (!у) throw new Error("нет такого поля");
  у.value = String(v);
  if (у.oninput) у.oninput({ target: у });
  (у.слушатели.input || []).forEach(f => f({ target: у }));
}
function сменить(у) {
  if (у.onchange) у.onchange({ target: у });
  (у.слушатели.change || []).forEach(f => f({ target: у }));
}
const пауза = () => new Promise(r => setImmediate(r));
async function дождаться(условие) {
  for (let i = 0; i < 300; i++) { if (условие()) return true; await пауза(); }
  return false;
}

function хранилище() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k), key: (i) => [...m.keys()][i] ?? null,
    get length() { return m.size; }, _m: m,
  };
}

const КАТЕГОРИИ = [
  { code: "liquid", name: "Жидкости", has_flavors: 1, variant_label: "Вкус",
    specs: [{ key: "strength", label: "Крепость", unit: "мг", kind: "number", options: [] },
            { key: "volume", label: "Объём", unit: "мл", kind: "number", options: [] }] },
  { code: "coils", name: "Расходники", has_flavors: 1, variant_label: "Сопротивление",
    specs: [{ key: "resistance", label: "Сопротивление", unit: "Ом", kind: "number", options: [] },
            { key: "fit", label: "Совместимость", unit: "", kind: "text", options: [] }] },
  { code: "podsystem", name: "Под-системы", has_flavors: 1, variant_label: "Цвет", specs: [] },
  { code: "charger", name: "Зарядки", has_flavors: 0, specs: [] },
];
const СТАТИЧНЫЕ = ["npOpen", "npOpenNote", "npClose", "npView", "npSteps", "npSaveWarn", "npBody", "npSum",
                   "npBack", "npNext", "npFile", "admSearch"];

function приложение({ ls = хранилище(), uid = 7, роль = "owner", моделиСервера = [], точки = ["Минск", "Гомель", "Брест"],
                      выбранная = "all", блобы = null } = {}) {
  const статичные = new Map(СТАТИЧНЫЕ.map(id => [id, new Узел("div", id === "npOpen" || id === "npSaveWarn" ? { id, hidden: "" } : { id })]));
  const $ = (id) => {
    if (статичные.has(id)) return статичные.get(id);
    for (const с of статичные.values()) for (const у of с.потомки()) if (у.id === id) return у;
    return null;
  };
  const журнал = { запросы: [], фото: [], алерты: [], вопросы: [], тосты: [], завоз: [], бренды: [],
                   ключПередЗапросом: [], моделиЗапрошены: 0, фотоВПути: 0, фотоМакс: 0 };
  const ответыФото = [], ответыПубликации = [];
  let счётчик = 0;
  const ctx = vm.createContext({
    $, document: { getElementById: $ }, localStorage: ls, console,
    JSON, Date, Math, Number, String, Object, Array, Set, Map, Promise, Error, TypeError, RegExp, isFinite,
    FormData, Blob, File, URL, setTimeout, clearTimeout,
    categories: КАТЕГОРИИ, CAT_OPTS: КАТЕГОРИИ.map(c => [c.code, c.name]),
    brands: [{ name: "Vaporesso", category: "", flavors: [] }, { name: "Chaser", category: "liquid", flavors: ["Вишня", "Grape B - POP"] }],
    models: [], locations: точки.map(name => ({ name })), admLocFilter: выбранная, city: "Минск",
    admSearch: "", admCatFilter: "Жидкости", admStockFilter: "low",
    myScope: () => (роль === "seller" ? "Минск" : ""), isOwner: () => роль === "owner",
    человек: () => String(uid), tgUser: { id: uid }, initData: "qa",
    новыйКлючОперации: () => `key${++счётчик}abcdefgh`,
    loaderHtml: () => "<div>Загрузка…</div>",
    toast: (m) => журнал.тосты.push(m), alertMsg: (m) => журнал.алерты.push(m),
    confirmMsg: (m, да) => { журнал.вопросы.push(m); журнал.да = да; },
    ОТКАЗЫ: { forbidden: "Нет доступа." },
    текстСбоя: (e) => (e && e.name === "AbortError" ? "Сервер не ответил вовремя." : "Сеть недоступна."),
    fetchModels: async () => { журнал.моделиЗапрошены++; ctx.models = моделиСервера.slice(); },
    fetchBrands: async () => {},
    ensureBrandExists: async (b) => { журнал.бренды.push(b); },
    openStockIn: (id) => журнал.завоз.push(id),
    refreshProducts: async () => { журнал.обновлено = (журнал.обновлено || 0) + 1; },
    renderAdmFilters: () => {}, renderAdminList: () => { журнал.список = (журнал.список || 0) + 1; },
    выбранныеЧипыВВиду: () => {},
    fetch: async (url, opts) => {
      if (url === "/api/admin/photo/draft") {
        журнал.фото.push(opts.body.get("file").name);
        const ответ = ответыФото.shift();
        if (!ответ) throw new Error("тест не задал ответ на фото");
        журнал.фотоВПути++;
        журнал.фотоМакс = Math.max(журнал.фотоМакс, журнал.фотоВПути);
        try { return await ответ(opts.body); } finally { журнал.фотоВПути--; }
      }
      if (url === "/api/admin/product/publish") {
        const тело = JSON.parse(opts.body);
        журнал.запросы.push(тело);
        // Что к этому моменту уже лежит в телефоне: ключ обязан быть там ДО ответа.
        const ч = JSON.parse(ls.getItem(`partut_newproduct_v1.${uid}`) || "null");
        журнал.ключПередЗапросом.push(ч && ч.token);
        const ответ = ответыПубликации.shift();
        if (!ответ) throw new Error("тест не задал ответ на публикацию");
        return ответ(тело);
      }
      throw new Error("неожиданный запрос " + url);
    },
  });
  vm.runInContext(ПОМОЩНИКИ, ctx);
  vm.runInContext(КОД, ctx);
  // IndexedDB в песочнице нет: без «блобы» файлы фото в телефон не ложатся
  // (как в приватном режиме), с ними — общее на все «запуски» хранилище.
  if (блобы) {
    ctx.__блобы = блобы;
    vm.runInContext(`нтБлобы = { положить: async (к, ф) => { __блобы.set(к, ф); }, взять: async (к) => __блобы.get(к) || null,
                                 убрать: async (к) => { __блобы.delete(к); } };`, ctx);
  }
  const js = (с) => vm.runInContext(с, ctx);
  const тело = () => $("npBody");
  const а = {
    $, js, журнал, ls, ctx,
    найти: (sel, усл = () => true) => тело().querySelectorAll(sel).find(усл) || null,
    открыть: () => js("openNewProduct()"),
    шаги: () => $("npSteps").querySelectorAll(".npstep")
      .map(b => `${b.querySelector("b").textContent} ${b.querySelector("span").textContent}`).join("|"),
    категория: (code) => нажать(а.найти("[data-npcat]", b => b.dataset.npcat === code)),
    дальше: () => нажать($("npNext")),
    ответитьФото: (f) => ответыФото.push(f),
    ответитьПубликации: (f) => ответыПубликации.push(f),
    выбратьФайлы: (файлы) => { $("npFile").files = файлы; $("npFile").onchange(); },
    // «Опубликовать» — со всеми вопросами «да»; ждём, пока запрос отработает.
    опубликовать: async () => {
      const было = журнал.вопросы.length;
      await js("нтОпубликовать(false)");
      if (журнал.вопросы.length > было) await журнал.да();
    },
    черновик: () => JSON.parse(ls.getItem(`partut_newproduct_v1.${uid}`) || "null"),
    фотоСтатусы: () => js("нт.photos.map(f => f.status).join(',')"),
    фотоИд: () => js("нт.photos.map(f => f.file_id || '-').join(',')"),
  };
  return а;
}
const ответ = (status, тело) => () => ({ status, ok: status < 400, json: async () => тело });
const фотоОк = (fid) => ответ(200, { ok: true, file_id: fid, thumb_id: fid + "_s", url: `/api/photo?file_id=${fid}`,
                                   thumb: `/api/photo?file_id=${fid}_s` });
const файл = (имя) => new File(["x"], имя, { type: "image/jpeg" });
const опубликован = (mid, города) => ответ(200, { ok: true, model_id: mid, products: города.map((c, i) => ({ id: 500 + i, city: c })), replay: false });

// Товар без вариантов и характеристик — короткий путь до шага 3.
async function зарядка(а, { имя = "Зарядка USB-C", цена = "12", закупка = "6", штук = "5" } = {}) {
  await а.открыть();
  а.категория("charger");
  ввести(а.$("npName"), имя);
  а.дальше();
  ввести(а.$("npp0"), цена); ввести(а.$("npc0"), закупка); ввести(а.$("npq0"), штук);
}

(async () => {
  // ---------- Вход: кнопка — только владельцу ----------
  {
    const в = приложение();
    в.js("обновитьКнопкуНового()");
    проверка("владелец видит «✨ Новый товар»", в.$("npOpen").hidden === false);
    const п = приложение({ роль: "seller" });
    п.js("обновитьКнопкуНового()");
    проверка("продавец не видит «Новый товар»: модель общая для всех точек", п.$("npOpen").hidden === true);
    await п.js("openNewProduct()");
    проверка("продавцу экран не открывается и в обход кнопки", !п.$("npView").classList.contains("show"));
  }

  // ---------- Шаги зависят от категории ----------
  {
    const а = приложение();
    await а.открыть();
    проверка("открыт на шаге 1, категория — первая", а.js("нт.step") === 1 && а.js("нт.category") === "liquid");
    проверка("жидкость: три шага, второй — «Вкусы и характеристики»",
      а.шаги() === "1 Основное|2 Вкусы и характеристики|3 Продажа на точке", а.шаги());
    а.категория("coils");
    проверка("расходники: второй шаг назван их словом", /Сопротивление и характеристики/.test(а.шаги()), а.шаги());
    а.категория("charger");
    проверка("зарядка без вариантов и характеристик: два шага, пустого нет", а.шаги() === "1 Основное|2 Продажа на точке", а.шаги());
    а.дальше();
    проверка("без названия дальше нельзя — сказано почему",
      а.js("нт.step") === 1 && /Впишите название/.test(а.$("npBody").textContent), а.$("npBody").textContent.slice(0, 80));
    ввести(а.$("npName"), "Зарядка");
    а.дальше();
    проверка("с названием — сразу к продаже, шаг 2 пропущен", а.js("нт.step") === 3 && !!а.$("npq0") && !а.найти("[data-npvq]"));
    проверка("«Назад» виден со второго шага, на первом скрыт", а.$("npBack").hidden === false);
    нажать(а.$("npBack"));
    проверка("«Назад» с шага 3 у такой категории — на шаг 1", а.js("нт.step") === 1 && а.$("npBack").hidden === true);
  }

  // ---------- Ввод не теряется: шаги, варианты списком, перезапуск ----------
  const ls = хранилище();
  {
    const а = приложение({ ls });
    await а.открыть();
    ввести(а.$("npName"), "Chaser Lux 30 мл");
    а.$("npBrand").value = "Chaser"; сменить(а.$("npBrand"));
    // Описание — без события ввода: так бывает при автозаполнении и у части
    // клавиатур. Переход между шагами обязан забрать и такое значение.
    а.$("npDesc").value = "Солевая жидкость";
    а.дальше();
    проверка("шаг 2 открыт", а.js("нт.step") === 2 && !!а.$("npFlavorIn"));
    ввести(а.$("npFlavorIn"), "Мята, Арбуз\nманго\n\nМЯТА; Лёд");
    нажать(а.$("npFlavorAdd"));
    проверка("варианты списком: через запятую, с новой строки, через «;»",
      JSON.stringify(а.js("нт.flavors")) === JSON.stringify(["Мята", "Арбуз", "манго", "Лёд"]), а.js("нт.flavors"));
    проверка("повтор (без учёта регистра) пропущен и назван", /уже в списке: «МЯТА»/.test(а.$("npFlavorNote").textContent),
      а.$("npFlavorNote").textContent);
    проверка("поле списка очищено", а.$("npFlavorIn").value === "");
    ввести(а.$("npsp_strength"), "20");
    нажать(а.найти("[data-npfx]", b => b.dataset.npfx === "3"));
    проверка("✕ у чипа убирает вариант", JSON.stringify(а.js("нт.flavors")) === JSON.stringify(["Мята", "Арбуз", "манго"]));
    ввести(а.$("npFlavorIn"), "Лёд");
    нажать(а.$("npFlavorAdd"));
    нажать(а.$("npSteps").querySelector("[data-npgo]"));        // «1 Основное»
    проверка("вернулись на шаг 1 — название, бренд и описание на месте",
      а.$("npName").value === "Chaser Lux 30 мл" && а.$("npBrand").value === "Chaser" && а.$("npDesc").value === "Солевая жидкость",
      [а.$("npName").value, а.$("npBrand").value, а.$("npDesc").value]);
    а.дальше();
    проверка("и на шаге 2 — варианты и крепость на месте", а.$("npsp_strength").value === "20" && а.js("нт.flavors.length") === 4);
    а.дальше();
    проверка("шаг 3: точка подставлена — текущая точка магазина",
      а.js("нт.points[0].city") === "Минск" && !!а.найти("[data-npcity]", b => b.dataset.c === "Минск" && b._классы.has("active")));
    ввести(а.$("npp0"), "18,5"); ввести(а.$("npc0"), "9");
    проверка("шаг 3: строка на каждый вариант", а.$("npBody").querySelectorAll("[data-npvq]").length === 4);
    const лёд = а.найти("[data-npvon]", b => b.dataset.f === "Лёд");
    лёд.checked = false; сменить(лёд);
    проверка("снятая галочка — поле количества закрыто", а.найти("[data-npvq]", x => x.dataset.f === "Лёд").disabled === true);
    ввести(а.$("npall0"), "7"); нажать(а.найти("[data-npall]"));
    const кол = (f) => а.найти("[data-npvq]", x => x.dataset.f === f).value;
    проверка("«Одно число всем» — только отмеченным", кол("Мята") === "7" && кол("манго") === "7" && кол("Лёд") === "",
      [кол("Мята"), кол("манго"), кол("Лёд")]);
    ввести(а.найти("[data-npvq]", x => x.dataset.f === "Арбуз"), "3");
    проверка("итог внизу: точка, цена, сколько вариантов и штук",
      а.$("npSum").textContent === "Минск — 18.50 Br · 3 вкуса · 17 шт", а.$("npSum").textContent);
    проверка("кнопка на последнем шаге — «Опубликовать»", а.$("npNext").textContent === "Опубликовать");
    нажать(а.$("npClose"));
    проверка("закрыли — пометка на кнопке: есть черновик", /есть черновик/.test(а.$("npOpenNote").textContent));
  }
  {
    const б = приложение({ ls });
    await б.открыть();
    проверка("перезапуск: черновик восстановлен, и сказано об этом", б.журнал.тосты.includes("Черновик восстановлен"), б.журнал.тосты);
    const кол = (f) => б.найти("[data-npvq]", x => x.dataset.f === f).value;
    проверка("перезапуск: тот же шаг, цена, закупка, количества и галочки",
      б.js("нт.step") === 3 && б.$("npp0").value === "18,5" && б.$("npc0").value === "9" && кол("Арбуз") === "3"
      && б.найти("[data-npvon]", x => x.dataset.f === "Лёд").checked === false);
    проверка("перезапуск: название, бренд, варианты, характеристика",
      б.js("нт.name") === "Chaser Lux 30 мл" && б.js("нт.brand") === "Chaser" && б.js("нт.flavors.length") === 4
      && б.js("нт.specs.liquid.strength") === "20");

    // Без фото — только осознанно.
    б.дальше();
    проверка("без фото — сначала вопрос, запрос не ушёл",
      б.журнал.вопросы.length === 1 && /без фото/.test(б.журнал.вопросы[0]) && б.журнал.запросы.length === 0);
    б.ответитьПубликации(опубликован(41, ["Минск"]));
    await б.журнал.да();
    const тело = б.журнал.запросы[0];
    проверка("ключ публикации лежал в черновике ДО запроса",
      !!тело.client_token && б.журнал.ключПередЗапросом[0] === тело.client_token, б.журнал.ключПередЗапросом);
    проверка("тело: модель — категория, название, бренд, описание",
      тело.model.category === "liquid" && тело.model.name === "Chaser Lux 30 мл" && тело.model.brand === "Chaser"
      && тело.model.description === "Солевая жидкость", тело.model);
    проверка("тело: характеристики — только заполненные", JSON.stringify(тело.model.specs) === '{"strength":"20"}', тело.model.specs);
    проверка("тело: «18,5» ушло числом 18.5, закупка 9", тело.points[0].price === 18.5 && тело.points[0].cost === 9, тело.points[0]);
    проверка("тело: варианты — только отмеченные, с количествами",
      JSON.stringify(тело.points[0].variants) === JSON.stringify([{ flavor: "Мята", stock: 7 }, { flavor: "Арбуз", stock: 3 },
                                                                 { flavor: "манго", stock: 7 }]), тело.points[0].variants);
    проверка("тело: у модели все четыре варианта (Лёд — в модели, но не на этой точке)",
      JSON.stringify(тело.model.flavors) === JSON.stringify(["Мята", "Арбуз", "манго", "Лёд"]));
    проверка("итог: «Опубликовано», точка, цена и первый приход",
      /Опубликовано/.test(б.$("npBody").textContent) && /Минск — 18.50 Br, первый приход 17 шт/.test(б.$("npBody").textContent),
      б.$("npBody").textContent.replace(/\s+/g, " ").slice(0, 160));
    проверка("итог подтверждён — черновик хранит его до «Готово»", (б.черновик() || {}).published?.model_id === 41);
    проверка("новый бренд уходит в справочник после публикации", б.журнал.бренды.includes("Chaser"));
    проверка("списки обновлены после публикации", б.журнал.обновлено === 1 && б.журнал.моделиЗапрошены >= 2);
  }
  {
    const в = приложение({ ls });
    await в.открыть();
    проверка("закрыли на итоге — при открытии тот же итог, а не пустая форма",
      /Опубликовано/.test(в.$("npBody").textContent) && в.$("npNext").textContent === "Готово — показать в списке");
    нажать(в.$("npNext"));
    await дождаться(() => в.js("admSearch") !== "");
    проверка("«Готово»: черновик стёрт", ls.getItem("partut_newproduct_v1.7") === null);
    проверка("«Готово»: список открыт на новом товаре, фильтры его не прячут",
      в.js("admSearch") === "Chaser Lux 30 мл" && в.$("admSearch").value === "Chaser Lux 30 мл"
      && в.js("admCatFilter") === "all" && в.js("admStockFilter") === "all" && в.журнал.список >= 1);
    проверка("«Готово»: экран закрыт", !в.$("npView").classList.contains("show"));
  }

  // ---------- Фото: по одному, сбой одного, повтор, главное, перезапуск ----------
  {
    const ls2 = хранилище();
    const а = приложение({ ls: ls2 });
    await а.открыть();
    ввести(а.$("npName"), "XROS 3 Mini");
    а.ответитьФото(фотоОк("fa"));
    а.ответитьФото(ответ(502, { ok: false, error: "send_failed",
      message: "Телеграм не принял этот файл. Попробуйте другой снимок — обычный jpg или png из галереи." }));
    а.ответитьФото(фотоОк("fc"));
    а.выбратьФайлы([файл("a.jpg"), файл("b.jpg"), файл("c.jpg")]);
    проверка("выбранные фото видны сразу, до загрузки", а.$("npPhotos").querySelectorAll("img").length === 3);
    await дождаться(() => !/queued|uploading/.test(а.фотоСтатусы()));
    проверка("фото грузятся по одному и каждое один раз", JSON.stringify(а.журнал.фото) === '["a.jpg","b.jpg","c.jpg"]', а.журнал.фото);
    проверка("сбой одного фото не трогает остальные", а.фотоСтатусы() === "ok,failed,ok", а.фотоСтатусы());
    проверка("у несработавшего — причина и «Повторить»",
      /Телеграм не принял/.test(а.$("npPhotos").textContent) && !!а.найти("[data-npretry]"));
    const вЧерновике = а.черновик().photos;
    проверка("в черновике: загруженные — по file_id, незагруженное — записью, а не пропадает (NP-03)",
      вЧерновике.length === 3 && вЧерновике[0].file_id === "fa" && вЧерновике[2].file_id === "fc"
      && !вЧерновике[1].file_id && вЧерновике[1].name === "b.jpg" && !!вЧерновике[1].local, вЧерновике);
    проверка("название на месте после сбоя фото", а.$("npName").value === "XROS 3 Mini");
    а.дальше();
    проверка("с несработавшим фото дальше нельзя — сказано, что делать",
      а.js("нт.step") === 1 && /нажмите «Повторить» или уберите его/.test(а.$("npBody").textContent));
    а.ответитьФото(фотоОк("fb"));
    нажать(а.найти("[data-npretry]"));
    await дождаться(() => !/queued|uploading/.test(а.фотоСтатусы()));
    проверка("«Повторить» грузит только это фото", а.журнал.фото.length === 4 && а.журнал.фото[3] === "b.jpg", а.журнал.фото);
    проверка("порядок фото сохранён", а.фотоИд() === "fa,fb,fc", а.фотоИд());
    нажать(а.найти("[data-npmain]", b => b.dataset.npmain === "2"));
    проверка("★ делает фото главным (первым) — и в черновике",
      а.фотоИд() === "fc,fa,fb" && а.черновик().photos.map(f => f.file_id).join(",") === "fc,fa,fb");
    проверка("у главного — пометка «главное»", а.$("npPhotos").querySelector(".npmain") !== null);
    нажать(а.найти("[data-npdel]", b => b.dataset.npdel === "1"));
    проверка("✕ убирает фото и из черновика", а.фотоИд() === "fc,fb" && а.черновик().photos.length === 2);
    // Добавили ещё, пока первое грузится, — очередь одна, повторов нет.
    let отпустить;
    а.ответитьФото(() => new Promise(r => { отпустить = () => r(фотоОк("fd")()); }));
    а.выбратьФайлы([файл("d.jpg")]);
    await пауза();
    а.ответитьФото(фотоОк("fe"));
    а.выбратьФайлы([файл("e.jpg")]);
    await пауза();
    отпустить();
    await дождаться(() => !/queued|uploading/.test(а.фотоСтатусы()));
    проверка("фото грузятся строго по одному — не два сразу", а.журнал.фотоМакс === 1, а.журнал.фотоМакс);
    проверка("фото, добавленные во время загрузки, грузятся по разу",
      а.журнал.фото.filter(x => x === "d.jpg").length === 1 && а.журнал.фото.filter(x => x === "e.jpg").length === 1, а.журнал.фото);
    а.ответитьФото(фотоОк("ff")); а.ответитьФото(фотоОк("fg"));
    а.выбратьФайлы([файл("f.jpg"), файл("g.jpg"), файл("h.jpg")]);
    await дождаться(() => !/queued|uploading/.test(а.фотоСтатусы()));
    проверка("больше шести фото не берём — и говорим об этом",
      а.js("нт.photos.length") === 6 && а.журнал.тосты.some(t => /не больше 6/.test(t)) && !а.$("npAddPhoto"));
    const б = приложение({ ls: ls2 });
    await б.открыть();
    проверка("перезапуск: все шесть фото на месте, по порядку, с превью с сервера",
      б.фотоИд() === "fc,fb,fd,fe,ff,fg" && б.$("npPhotos").querySelectorAll("img")[0].attrs.src === "/api/photo?file_id=fc_s",
      б.фотоИд());
    // Публикация уносит фото по порядку: первое — главное.
    б.дальше(); б.дальше();
    ввести(б.$("npp0"), "95"); ввести(б.$("npc0"), "70");
    б.ответитьПубликации(опубликован(42, ["Минск"]));
    await б.опубликовать();
    проверка("с фото — без вопроса «без фото?»", б.журнал.вопросы.length === 0);
    проверка("фото ушли списком file_id по порядку", JSON.stringify(б.журнал.запросы[0].photos) === '["fc","fb","fd","fe","ff","fg"]',
      б.журнал.запросы[0].photos);
  }

  // ---------- Исход неизвестен: ключ остаётся, повтор — с ним же ----------
  for (const [что, сбой] of [
    ["сеть", () => { throw new TypeError("Failed to fetch"); }],
    // Так отвечает сам сервер, когда падает (server.py, _report_unhandled): с кодом
    // ошибки, но без «не выполнено» — запись могла пройти до падения.
    ["500 сервера", ответ(500, { ok: false, error: "server_error" })],
    ["502 прокси страницей", () => ({ status: 502, json: async () => { throw new SyntaxError("Unexpected token <"); } })],
    ["тело оборвалось", () => ({ status: 200, json: async () => { throw new TypeError("оборвалось"); } })],
    ["ответ без смысла", ответ(200, { hello: 1 })],
  ]) {
    const а = приложение();
    await зарядка(а);
    а.ответитьПубликации(сбой);
    await а.опубликовать();
    const ключ = а.журнал.запросы[0].client_token;
    проверка(`${что}: человеку сказано «мог и опубликоваться, нажмите ещё раз»`,
      а.журнал.алерты.length === 1 && /мог и опубликоваться/.test(а.журнал.алерты[0]) && /второй раз он не создастся/.test(а.журнал.алерты[0]),
      а.журнал.алерты);
    проверка(`${что}: не «опубликовано», ключ в черновике`, !а.черновик().published && а.черновик().token === ключ);
    проверка(`${что}: кнопка снова «Опубликовать»`, а.$("npNext").textContent === "Опубликовать" && !а.$("npNext").disabled);
    а.ответитьПубликации(ответ(200, { ok: true, model_id: 7, products: [{ id: 70, city: "Минск" }], replay: true }));
    await а.опубликовать();
    проверка(`${что}: повтор ушёл с тем же ключом`, а.журнал.запросы[1].client_token === ключ);
    проверка(`${что}: сервер вернул прежний итог — «Опубликовано»`, /Опубликовано/.test(а.$("npBody").textContent));
  }

  // ---------- Перезапуск посреди публикации: ключ пережил его ----------
  {
    const ls3 = хранилище();
    const а = приложение({ ls: ls3 });
    await зарядка(а);
    а.ответитьПубликации(() => new Promise(() => {}));      // ответа не будет никогда
    а.js("нтОпубликовать(false)");
    await дождаться(() => а.журнал.вопросы.length > 0);
    а.журнал.да();
    await дождаться(() => а.журнал.запросы.length === 1);
    const ключ = а.журнал.запросы[0].client_token;
    const б = приложение({ ls: ls3 });                     // приложение закрыли, пока запрос в пути
    await б.открыть();
    б.ответитьПубликации(ответ(200, { ok: true, model_id: 8, products: [{ id: 80, city: "Минск" }], replay: true }));
    await б.опубликовать();
    проверка("после перезапуска повтор ушёл с тем же ключом", б.журнал.запросы[0].client_token === ключ,
      [ключ, б.журнал.запросы[0].client_token]);
  }

  // ---------- Двойное нажатие ----------
  {
    const а = приложение();
    await зарядка(а);
    let отпустить;
    а.ответитьПубликации(() => new Promise(r => { отпустить = () => r(опубликован(9, ["Минск"])()); }));
    const первое = а.js("нтОпубликовать(true)");
    await а.js("нтОпубликовать(true)");
    нажать(а.$("npNext"));
    проверка("второе нажатие, пока первое в пути, ничего не шлёт", а.журнал.запросы.length === 1, а.журнал.запросы.length);
    проверка("кнопка «Публикую…» и заблокирована", а.$("npNext").disabled === true && а.$("npNext").textContent === "Публикую…");
    нажать(а.$("npBack"));
    проверка("пока публикуется, шаги не переключаются", а.js("нт.step") === 3);
    отпустить(); await первое;
    проверка("после ответа — итог", /Опубликовано/.test(а.$("npBody").textContent));
  }

  // ---------- Тот же ключ с другими данными — «уже был опубликован» ----------
  {
    const а = приложение();
    await зарядка(а);
    а.ответитьПубликации(ответ(409, { ok: false, error: "token_reused", message: "Эта публикация уже проведена с другими данными.",
                                      recorded: { model_id: 11, products: [{ id: 110, city: "Минск" }] } }));
    await а.опубликовать();
    const текст = а.$("npBody").textContent;
    проверка("token_reused: «Товар уже был опубликован» — не «ошибка» и не второй товар",
      /Товар уже был опубликован/.test(текст) && /прошлое нажатие/.test(текст), текст.replace(/\s+/g, " ").slice(0, 200));
    проверка("token_reused: итог хранится до «Готово»", (а.черновик() || {}).published?.model_id === 11);
  }

  // ---------- Отказ с причиной: у нужного шага, ввод цел ----------
  {
    const а = приложение();
    await зарядка(а, { цена: "12" });
    а.ответитьПубликации(ответ(400, { ok: false, error: "bad_price", message: "«Минск»: цена — число больше нуля, например 18.5.",
                                      field: "points.0.price" }));
    await а.опубликовать();
    проверка("отказ по цене: показан на шаге 3", а.js("нт.step") === 3 && /цена — число больше нуля/.test(а.$("npBody").textContent));
    проверка("отказ: ввод на месте, «опубликовано» не сказано",
      а.$("npp0").value === "12" && а.$("npq0").value === "5" && !а.черновик().published);
    а.ответитьПубликации(ответ(400, { ok: false, error: "bad_photo",
      message: "Фото не найдено среди загруженных для нового товара — загрузите его заново." }));
    await а.опубликовать();
    проверка("отказ по фото — на шаге 1, где фото", а.js("нт.step") === 1 && /загрузите его заново/.test(а.$("npBody").textContent));
  }

  // ---------- Двойник: на экране и от сервера ----------
  {
    const твин = { id: 5, category: "podsystem", name: "XROS 3", brand: "Vaporesso", flavors: [] };
    const а = приложение({ моделиСервера: [твин] });
    await а.открыть();
    а.категория("podsystem");
    а.$("npBrand").value = "Vaporesso"; сменить(а.$("npBrand"));
    ввести(а.$("npName"), "xros 3");
    проверка("двойник: «такая модель уже есть» — ещё до публикации", /уже есть в ассортименте/.test(а.$("npTwin").textContent));
    нажать(а.найти("[data-npstockin]"));
    проверка("кнопка ведёт в «Завезти» этой модели, черновик цел", а.журнал.завоз[0] === 5 && а.js("нт.name") === "xros 3");
    ввести(а.$("npName"), "XROS");
    проверка("похожее название — подсказка со ссылкой", /Похоже на то, что уже есть/.test(а.$("npTwin").textContent)
      && !!а.найти("[data-npstockin]"));

    const б = приложение();                                   // на экране двойника не видно — сервер откажет
    await зарядка(б, { имя: "Зарядка Vaporesso" });
    б.ctx.models = [];
    б.ответитьПубликации(ответ(409, { ok: false, error: "exists", model_id: 6, name: "Зарядка Vaporesso",
      message: "Такая модель уже есть: «Зарядка Vaporesso». Завезите её на точку, а не заводите вторую." }));
    const моделейДо = б.журнал.моделиЗапрошены;
    await б.опубликовать();
    проверка("exists от сервера: шаг 1, причина названа", б.js("нт.step") === 1 && /Такая модель уже есть/.test(б.$("npBody").textContent));
    проверка("exists: список моделей перечитан — появится кнопка «Завезти»", б.журнал.моделиЗапрошены > моделейДо);
  }

  // ---------- Числа и закупка ----------
  {
    const а = приложение();
    await зарядка(а, { закупка: "" });
    await а.опубликовать();
    проверка("пустая закупка — не публикуем, подсказка про 0",
      а.журнал.запросы.length === 0 && /впишите закупку/.test(а.$("npBody").textContent) && /поставьте 0/.test(а.$("npBody").textContent));
    ввести(а.$("npc0"), "0"); ввести(а.$("npp0"), "abc");
    await а.опубликовать();
    проверка("цена «abc» — отказ формы, не ноль", а.журнал.запросы.length === 0 && /цена — число больше нуля/.test(а.$("npBody").textContent));
    ввести(а.$("npp0"), "12"); ввести(а.$("npq0"), "2.5");
    await а.опубликовать();
    проверка("количество «2.5» — отказ формы", а.журнал.запросы.length === 0 && /целое число/.test(а.$("npBody").textContent));
    ввести(а.$("npq0"), "5");
    а.ответитьПубликации(опубликован(12, ["Минск"]));
    await а.опубликовать();
    const т = а.журнал.запросы[0] && а.журнал.запросы[0].points[0];
    проверка("закупка 0, вписанная руками, принята; у товара без вариантов — stock, без variants",
      т && т.cost === 0 && т.price === 12 && т.stock === 5 && !("variants" in т) && JSON.stringify(а.журнал.запросы[0].model.flavors) === "[]", т);
  }

  // ---------- Точки: выбранная в списке, ещё точка, у каждой своя ----------
  {
    const а = приложение({ выбранная: "Гомель" });
    await зарядка(а);
    проверка("точка по умолчанию — выбранная в списке товаров", а.js("нт.points[0].city") === "Гомель");
    нажать(а.$("npAddPoint"));
    проверка("«＋ Ещё точка» — следующая свободная; занятая у второй закрыта",
      а.js("нт.points[1].city") === "Минск"
      && а.найти("[data-npcity]", b => b.dataset.npcity === "1" && b.dataset.c === "Гомель").disabled === true);
    ввести(а.$("npp1"), "13"); ввести(а.$("npc1"), "6"); ввести(а.$("npq1"), "2");
    проверка("итог — по каждой точке", /Гомель — 12.00 Br · 5 шт/.test(а.$("npSum").textContent)
      && /Минск — 13.00 Br · 2 шт/.test(а.$("npSum").textContent), а.$("npSum").textContent);
    а.ответитьПубликации(опубликован(13, ["Гомель", "Минск"]));
    await а.опубликовать();
    const тело = а.журнал.запросы[0];
    проверка("ушли обе точки со своими ценами", тело.points.length === 2 && тело.points[1].city === "Минск" && тело.points[1].price === 13);
    const б = приложение();
    await зарядка(б);
    нажать(б.$("npAddPoint"));
    нажать(б.найти("[data-nprm]"));
    проверка("✕ убирает лишнюю точку", б.js("нт.points.length") === 1);
    б.js('locations = [{ name: "Гомель" }, { name: "Брест" }]');     // точку «Минск» закрыли, пока черновик лежал
    await б.опубликовать();
    проверка("точки из черновика больше нет — сказано, что выбрать другую",
      б.журнал.запросы.length === 0 && /точки «Минск» больше нет/.test(б.$("npBody").textContent));
  }

  // ---------- Характеристики: у каждой категории свои, совместимость ----------
  {
    const а = приложение();
    await а.открыть();
    ввести(а.$("npName"), "Картридж");
    а.дальше();
    ввести(а.$("npsp_strength"), "20");
    нажать(а.$("npBack"));
    а.категория("coils");
    а.дальше();
    проверка("у расходников своих полей нет крепости", !а.$("npsp_strength") && !!а.$("npsp_fit"));
    проверка("пустая совместимость — подсказка, почему её надо заполнить", /не найдёт товар по названию своего устройства/.test(а.$("npFitNote").textContent));
    ввести(а.$("npsp_fit"), "XROS 3, XROS 4");
    проверка("совместимость вписана — подсказка ушла", а.$("npFitNote").textContent === "");
    нажать(а.$("npBack"));
    а.категория("liquid");
    а.дальше();
    проверка("вернулись к жидкости — крепость на месте, а не перенесена в чужую категорию",
      а.$("npsp_strength").value === "20" && а.js("нт.specs.coils.fit") === "XROS 3, XROS 4" && !("strength" in а.js("нт.specs.coils")));
  }

  // ---------- Вкусы бренда из справочника ----------
  {
    const а = приложение();
    await а.открыть();
    ввести(а.$("npName"), "Chaser Mix");
    а.$("npBrand").value = "Chaser"; сменить(а.$("npBrand"));
    а.дальше();
    const чипы = () => а.$("npBody").querySelectorAll("[data-npkf]").map(b => b.dataset.npkf);
    проверка("шаг 2: вкусы бренда из справочника подсказаны", JSON.stringify(чипы()) === '["Вишня","Grape B - POP"]', чипы());
    нажать(а.найти("[data-npkf]", b => b.dataset.npkf === "Вишня"));
    проверка("нажал — вкус в списке, из подсказок ушёл", а.js("нт.flavors").includes("Вишня") && !чипы().includes("Вишня"), чипы());
    ввести(а.$("npFlavorIn"), "grape b - pop, Лимон");
    нажать(а.$("npFlavorAdd"));
    проверка("набранное руками — в написании справочника",
      JSON.stringify(а.js("нт.flavors")) === '["Вишня","Grape B - POP","Лимон"]', а.js("нт.flavors"));
    проверка("все вкусы бренда уже в списке — подсказки нет", !а.$("npBody").querySelector(".npknown"));
    const б = приложение();
    await б.открыть();
    ввести(б.$("npName"), "Chaser Mix");
    б.$("npBrand").value = "Chaser"; сменить(б.$("npBrand"));
    б.дальше();
    нажать(б.$("npKnownAll"));
    проверка("«Добавить все» — все вкусы бренда разом", JSON.stringify(б.js("нт.flavors")) === '["Вишня","Grape B - POP"]', б.js("нт.flavors"));
    const в = приложение();
    await в.открыть();
    ввести(в.$("npName"), "Без бренда");
    в.дальше();
    проверка("бренд не выбран — подсказок нет", !в.$("npBody").querySelector(".npknown"));
  }

  // ---------- NP-01: набранный, но не добавленный список вариантов ----------
  {
    const а = приложение();
    await а.открыть();
    ввести(а.$("npName"), "Chaser Salt");
    а.дальше();
    ввести(а.$("npFlavorIn"), "Мята\nАрбуз\nМанго\nЛёд\nКола");
    а.дальше();                                                   // «Добавить в список» не нажимали
    проверка("NP-01: «Дальше» забрал набранный список — на шаге 3 пять строк, а не общий остаток",
      а.js("нт.step") === 3 && а.$("npBody").querySelectorAll("[data-npvq]").length === 5 && !а.$("npq0"),
      { шаг: а.js("нт.step"), вкусы: а.js("нт.flavors") });
    проверка("NP-01: и сказано, сколько добавлено", а.журнал.тосты.includes("Добавлено в список: 5"), а.журнал.тосты);
    нажать(а.$("npBack"));
    ввести(а.$("npFlavorIn"), "мята, Дыня");
    нажать(а.$("npSteps").querySelectorAll("[data-npgo]").find(b => b.dataset.npgo === "3"));
    проверка("NP-01: через заголовок шага — то же; повтор назван, и экран остался на шаге вариантов",
      а.js("нт.step") === 2 && а.js("нт.flavors").includes("Дыня") && /уже в списке: «мята»/.test(а.$("npFlavorNote").textContent),
      { шаг: а.js("нт.step"), заметка: а.$("npFlavorNote").textContent });
    а.дальше();
    ввести(а.$("npp0"), "15"); ввести(а.$("npc0"), "8");
    а.js('нт.flavorInput = "Персик"');                          // набрано, а публикуют (как если бы ушли с шага по-другому)
    а.ответитьПубликации(опубликован(31, ["Минск"]));
    await а.опубликовать();
    const тело = а.журнал.запросы[0];
    проверка("NP-01: и при публикации набранное входит в товар",
      тело && тело.model.flavors.includes("Персик") && тело.points[0].variants.some(v => v.flavor === "Персик"), тело && тело.model.flavors);
  }

  // ---------- NP-02: десятичная запятая в вариантах ----------
  {
    const а = приложение();
    await а.открыть();
    а.категория("coils");
    ввести(а.$("npName"), "Картридж XROS");
    а.дальше();
    ввести(а.$("npFlavorIn"), "0,6\n0,8");
    нажать(а.$("npFlavorAdd"));
    проверка("NP-02: «0,6» и «0,8» строками — два сопротивления, а не «0», «6», «8»",
      JSON.stringify(а.js("нт.flavors")) === '["0,6","0,8"]', а.js("нт.flavors"));
    ввести(а.$("npFlavorIn"), "0.6, 1,0 Ом; 0,8");
    нажать(а.$("npFlavorAdd"));
    проверка("NP-02: «0.6» = «0,6» — повтор; «1,0 Ом» — одно значение",
      JSON.stringify(а.js("нт.flavors")) === '["0,6","0,8","1,0 Ом"]' && /«0\.6»/.test(а.$("npFlavorNote").textContent),
      { вкусы: а.js("нт.flavors"), заметка: а.$("npFlavorNote").textContent });
    ввести(а.$("npFlavorIn"), "0,4, 1,2");
    нажать(а.$("npFlavorAdd"));
    проверка("NP-02: «0,4, 1,2» через запятую с пробелом — два значения",
      а.js("нт.flavors").includes("0,4") && а.js("нт.flavors").includes("1,2"), а.js("нт.flavors"));
  }

  // ---------- NP-03: незагруженное фото после перезапуска ----------
  {
    // Без хранилища файлов (приватный режим): фото не пропадает молча.
    const ls4 = хранилище();
    const а = приложение({ ls: ls4 });
    await а.открыть();
    ввести(а.$("npName"), "Под с фото");
    а.ответитьФото(фотоОк("ga"));
    а.ответитьФото(ответ(502, { ok: false, error: "send_failed", message: "Телеграм не принял этот файл." }));
    а.выбратьФайлы([файл("a.jpg"), файл("b.jpg")]);
    await дождаться(() => !/queued|uploading/.test(а.фотоСтатусы()));
    const б = приложение({ ls: ls4 });
    await б.открыть();
    await дождаться(() => !/restore/.test(б.фотоСтатусы()));
    проверка("NP-03: после перезапуска незагруженное фото на месте — с просьбой выбрать снова",
      б.фотоСтатусы() === "ok,lost" && /не сохранилось в телефоне/.test(б.$("npPhotos").textContent), б.фотоСтатусы());
    б.дальше();
    проверка("NP-03: пока оно не выбрано снова или не убрано — дальше нельзя", б.js("нт.step") === 1 && /выберите его снова/.test(б.$("npBody").textContent));
    нажать(б.найти("[data-npdel]", x => x.dataset.npdel === "1"));
    б.дальше();
    проверка("NP-03: убрали осознанно — можно дальше", б.js("нт.step") === 2 && б.js("нт.photos.length") === 1, б.js("нт.step"));

    // С хранилищем файлов: очередь восстанавливается и догружает сама.
    const ls5 = хранилище(), блобы = new Map();
    const в = приложение({ ls: ls5, блобы });
    await в.открыть();
    ввести(в.$("npName"), "Под с фото 2");
    в.ответитьФото(фотоОк("ha"));
    в.ответитьФото(ответ(502, { ok: false, error: "send_failed", message: "Телеграм не принял этот файл." }));
    в.выбратьФайлы([файл("a.jpg"), файл("b.jpg")]);
    await дождаться(() => !/queued|uploading/.test(в.фотоСтатусы()));
    await пауза();
    проверка("NP-03: файл незагруженного фото лежит в телефоне, загруженного — убран", блобы.size === 1, блобы.size);
    const г = приложение({ ls: ls5, блобы });
    г.ответитьФото(фотоОк("hb"));
    await г.открыть();
    await дождаться(() => г.фотоСтатусы() === "ok,ok");
    проверка("NP-03: после перезапуска файл нашёлся и догрузился сам — фото по порядку",
      г.фотоИд() === "ha,hb" && JSON.stringify(г.журнал.фото) === '["b.jpg"]', { ид: г.фотоИд(), грузили: г.журнал.фото });
    await пауза();
    проверка("NP-03: после загрузки файл из телефона убран", блобы.size === 0, блобы.size);
  }

  // ---------- Черновик не пишется — это видно; черновик — по человеку ----------
  {
    const плохое = хранилище();
    плохое.setItem = () => { throw new Error("QuotaExceededError"); };
    const а = приложение({ ls: плохое });
    await а.открыть();
    ввести(а.$("npName"), "Тест");
    проверка("не удалось сохранить черновик — предупреждение на экране", а.$("npSaveWarn").hidden === false);
    const общее = хранилище();
    const п = приложение({ ls: общее, uid: 1 });
    await п.открыть();
    ввести(п.$("npName"), "Черновик первого");
    const в = приложение({ ls: общее, uid: 2 });
    await в.открыть();
    проверка("у другого человека на том же телефоне — свой черновик", в.$("npName").value === "" && !в.журнал.тосты.length);
    проверка("на пустой форме стирать нечего — ссылки нет", !п.$("npReset"));
    нажать(п.$("npClose"));
    await п.открыть();
    нажать(п.$("npReset"));
    проверка("«Стереть черновик» — сначала вопрос", /Стереть черновик\?/.test(п.журнал.вопросы.at(-1) || ""));
    await п.журнал.да();
    проверка("«Стереть черновик» — форма чистая", п.$("npName").value === "" && (п.черновик() || {}).name === "");
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\n❌ провалов: ${провалов}` : "\nВсё прошло");
  process.exitCode = провалов ? 1 : 0;
})().catch(e => { console.log("❌ упало: " + (e && e.stack || e)); process.exitCode = 1; дошлиДоКонца = true; });
