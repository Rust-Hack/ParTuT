// Поиск на витрине глазами покупателя — настоящий код из 01-core.js (поиск,
// карточка выдачи, пустая выдача) и 03-shop.js (окно товара) в песочнице Node.
//
// К-1 (проход 6.10): искал в «Жидкостях» одноразку — «Ничего не найдено.
// Другая категория или точка.», а догадаться нажать «Все товары» надо было
// самому. Теперь сказано, где нашлось, и кнопка «Искать во всех категориях».
// К-3: поиск «вишн» находил ANNIMA LOVE, а о вишне не было ни слова — ни в
// выдаче, ни в окне товара (семь вкусов подряд). Теперь «Нашлось: …» в
// карточке, а в окне совпавшие — сверху, с пометкой, и не прыгают после «+».
// К-5: «В корзину · 1 шт» после «+» читалось как «добавить ещё», а кнопка
// открывает корзину. Теперь «Перейти в корзину · 1 шт · …».
//
// Запуск: node tests/js/storefront_search.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const папка = path.join(__dirname, "..", "..", "partut", "webapp", "app");
const ядро = fs.readFileSync(path.join(папка, "01-core.js"), "utf8");
const магазин = fs.readFileSync(path.join(папка, "03-shop.js"), "utf8");
function кусок(текст, от, до) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл: ${от}`); process.exit(1); }
  return текст.slice(а, б);
}
const код = [
  кусок(ядро, "// Вкусы товара: у модели со вкусами это варианты", "// Вкусы для фильтра «Вкус»"),   // flavorsOf — им ищет поиск
  кусок(ядро, "// ----- Поиск на витрине -----", "// ----- /Поиск на витрине -----"),
  // Границы — по меткам, которые есть и в версии до К-1/К-3: тогда на старом
  // коде проверка падает по сути, а не потому, что не нашла кусок.
  кусок(ядро, "function gridRating(p)", "// Точка закрыта на время"),            // карточка выдачи
  кусок(ядро, "let таймерПаузы = null;", "function bindCardButtons(root)"),       // сетка и пустая выдача
  кусок(магазин, "let currentProductId = null;", "function flavorQty("),
  кусок(магазин, "function renderProdActions(p)", "function prodQty("),
].join("\n");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const текст = (html) => String(html).replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

const ANNIMA = { id: 1, name: "ANNIMA LOVE", brand: "ANNIMA", city: "Минск", category: "liquid", price: 20, stock: 9, specs: {},
  variants: [{ flavor: "Арбуз", stock: 3 }, { flavor: "Кислая вишня", stock: 2 }, { flavor: "Мята", stock: 3 },
             { flavor: "Смородина вишня черника", stock: 0 }, { flavor: "Вишня кола", stock: 1 }] };
const PILOW = { id: 2, name: "PILOW TALK IC40000", brand: "PILOW TALK", city: "Минск", category: "disposable", price: 40, stock: 5, specs: {}, variants: [] };
const ЧУЖОЙ = { id: 3, name: "PILOW TALK ULTRA", brand: "PILOW TALK", city: "Туров", category: "disposable", price: 42, stock: 5, specs: {}, variants: [] };
const КАБЕЛЬ = { id: 4, name: "QA Кабель", brand: "", city: "Минск", category: "liquid", price: 17, stock: 5, specs: {}, variants: [] };

function стенд() {
  const узлы = {}, события = { чипы: 0 };
  const $ = (id) => узлы[id] || (узлы[id] = { id, innerHTML: "", textContent: "", hidden: false, scrollTop: 0, onclick: null,
    classList: { add() {}, remove() {}, contains: () => false }, querySelectorAll: () => [], querySelector: () => null });
  const ctx = vm.createContext({
    $, console, JSON, Math, String, Set, Object, Array, setTimeout,
    allProducts: [ANNIMA, PILOW, ЧУЖОЙ, КАБЕЛЬ].map(p => JSON.parse(JSON.stringify(p))),
    city: "Минск", cat: "liquid", search: "", brandFilters: [], flavorFilters: [],
    CATS: { "": "Все товары", liquid: "Жидкости", disposable: "Одноразки" }, CAT_EMOJI: {}, CUR: "Br",
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    вкусыДляФильтра: (p) => (p.variants || []).map(v => v.flavor), sortProducts: (l) => l,
    паузаТочки: () => null, перечитатьТочки: async () => true, clearTimeout() {},
    bindCardButtons() {}, renderChips: () => события.чипы++, updateFilterBtn() {},
    favs: new Set(), subtitleFor: () => "", waitingFor: () => false, productBadges: () => "",
    thumbOf: (p) => p.photo_url, cart: {}, cartKey: (id, f) => f ? `${id}::${f}` : `${id}`,
    hasVariants: (p) => !!(p.variants && p.variants.length), catTwoAxis: () => false, catVariant2: () => "",
    catVariantMany: () => "Вкусы", catVariant: () => "Вкус", AXIS_SEP: " · ", specsOf: () => [], withUnit: (v) => v, starsHtml: () => "",
    plural: (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? b : c),
    loaderHtml: () => "", loadProductReviews() {}, notifyMe() {}, renderGrid: null, showTab() {},
  });
  vm.runInContext(код, ctx);
  const js = (к) => vm.runInContext(к, ctx);
  return { ctx, js, узлы, события,
    искать: (запрос, категория = "liquid") => { js(`search = ${JSON.stringify(запрос)}; cat = ${JSON.stringify(категория)}; renderGrid()`); },
    сетка: () => узлы.grid.innerHTML, пусто: () => узлы.catEmpty.innerHTML };
}

(() => {
  // ---- К-1: в категории пусто, а в другой нашлось ----
  {
    const с = стенд();
    с.искать("pilow");
    проверка("К-1: в «Жидкостях» «pilow» — сетка пуста", !/data-open/.test(с.сетка()));
    проверка("…сказано «В «Жидкости» не нашлось» и где есть: «PILOW TALK IC40000»",
      /В «Жидкости» не нашлось/.test(с.пусто()) && /Есть в других категориях: PILOW TALK IC40000\./.test(текст(с.пусто())), текст(с.пусто()));
    проверка("…кнопка «Искать во всех категориях · 1» (чужая точка не в счёт)", /Искать во всех категориях · 1</.test(с.пусто()), с.пусто());
    проверка("…фильтр сам не сменился — всё ещё «Жидкости»", с.js("cat") === "liquid");
    с.js(`brandFilters = ["ANNIMA"]; flavorFilters = ["Мята"]`);
    с.узлы.searchAllCats.onclick();
    проверка("«Искать во всех категориях» — как «Все товары»: категория «Все», фильтры брендов и вкусов сброшены, чипы перерисованы",
      с.js("cat") === "" && с.js("brandFilters.length + flavorFilters.length") === 0 && с.события.чипы === 1);
    проверка("…и PILOW TALK в сетке, поиск сохранён", /PILOW TALK IC40000/.test(с.сетка()) && с.js("search") === "pilow");
    с.искать("такого нет нигде");
    проверка("нигде не нашлось — прежнее «Ничего не найдено», без кнопки",
      /Ничего не найдено/.test(с.пусто()) && !/searchAllCats/.test(с.пусто()));
    с.искать("", "liquid");
    проверка("без запроса пустая категория — без подсказки про другие категории", !/searchAllCats/.test(с.пусто()));
  }

  // ---- К-3: нашли по вкусу — назван в выдаче ----
  {
    const с = стенд();
    с.искать("вишн");
    const карточка = текст(с.сетка());
    проверка(`К-3: «вишн» — в карточке ANNIMA LOVE «Нашлось: Кислая вишня, Смородина вишня черника (нет) и ещё 1» (${карточка})`,
      /ANNIMA LOVE/.test(карточка) && /Нашлось: Кислая вишня, Смородина вишня черника \(нет\) и ещё 1/.test(карточка));
    с.искать("annima");
    проверка("нашли по названию — строки «Нашлось» нет", /ANNIMA LOVE/.test(с.сетка()) && !/Нашлось/.test(с.сетка()));
    // Окно товара: совпавшие — сверху, с пометкой, и не прыгают после «+».
    с.искать("вишн");
    с.js("openProduct(1)");
    const порядок = () => [...с.узлы.prodBody.innerHTML.matchAll(/<div class="fname">([^<]*)/g)].map(m => m[1].trim());
    проверка(`окно товара: совпавшие сверху — ${порядок().join(", ")}`,
      порядок().join("|") === "Кислая вишня|Смородина вишня черника|Вишня кола|Арбуз|Мята", порядок());
    проверка("…с пометкой «совпало с поиском» (3 строки)",
      (с.узлы.prodBody.innerHTML.match(/совпало с поиском/g) || []).length === 3);
    с.js(`cart[cartKey(1, "Мята")] = { product_id: 1, flavor: "Мята", qty: 1 }; renderProduct()`);
    проверка("после «+» у «Мяты» порядок тот же — строки не прыгают", порядок().join("|") === "Кислая вишня|Смородина вишня черника|Вишня кола|Арбуз|Мята");
    с.искать("");
    с.js("openProduct(1)");
    проверка("без поиска — исходный порядок и без пометок",
      порядок().join("|") === "Арбуз|Кислая вишня|Мята|Смородина вишня черника|Вишня кола" && !/совпало с поиском/.test(с.узлы.prodBody.innerHTML));
  }

  // ---- К-5: кнопка говорит, что откроет корзину ----
  {
    const с = стенд();
    с.js(`cart[cartKey(1, "Мята")] = { product_id: 1, flavor: "Мята", qty: 1 }; openProduct(1)`);
    проверка(`К-5: после «+» у вкуса — «${текст(с.узлы.prodActions.innerHTML)}»`,
      текст(с.узлы.prodActions.innerHTML) === "Перейти в корзину · 1 шт · 20.00 Br");
    с.js(`cart[cartKey(4)] = { product_id: 4, flavor: null, qty: 2 }; openProduct(4)`);
    проверка(`…у товара без вкусов — «${текст(с.узлы.prodActions.innerHTML)}»`, /Перейти в корзину · 34\.00 Br/.test(текст(с.узлы.prodActions.innerHTML)));
    с.js(`delete cart[cartKey(4)]; openProduct(4)`);
    проверка("…а «+ В корзину» (добавить) — как было", /\+ В корзину/.test(с.узлы.prodActions.innerHTML));
  }

  // ---- К-4: на узком экране кнопка карточки уходит под цену, а не за край ----
  {
    const стили = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "styles.css"), "utf8");
    проверка("К-4: строка цены и кнопки переносится (flex-wrap), кнопка прижата вправо",
      /\.crow \{[^}]*flex-wrap: wrap;/.test(стили) && /\.crow > :last-child \{ margin-left: auto; \}/.test(стили));
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
