// Корзина и обновление витрины вместе (приёмки ROLE-03-R1 и ROLE-03-R2).
// Настоящий код из 01-core.js в песочнице Node: хранение корзины, загрузка и
// обновление витрины, сверка, renderCart, cartCount и cartTotal — без заглушки
// renderCart. Прежний тест свежести (storefront_fresh.cjs) подменял её
// счётчиком вызовов и потому не видел, что получается у них вместе.
//
// R1: владелец снял чехол с витрины — после обновления строки пропадали из
// списка, а в корзине оставались: значок 2, «Оформить · 0.00 Br», убрать
// нечем. R2: обновление не удалось — предупреждение было только в каталоге,
// а корзина показывала прежние цены молча.
//
// Запуск: node tests/js/cart_fresh.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ядро = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "01-core.js"), "utf8");
function кусок(от, до) {
  const а = ядро.indexOf(от), б = ядро.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл в 01-core.js: ${от}`); process.exit(1); }
  return ядро.slice(а, б);
}
const код = [
  кусок('const CART_KEY = "partut_cart_v2";', "let useCoins = false;"),                       // корзина в хранилище
  кусок("let useCoins = false;", "\n"),                                                         // галочка «списать монеты»
  кусок("let витринаСвежа = true;", "// Витрина и админка смотрят на разные списки"),           // витрина и сверка
  кусок("function hasVariants(p)", "\n\n"),                                                      // hasVariants, variantStock, cartKey
  кусок("const cartCount = ", "function renderFav() {"),                                         // корзина, сумма, допродажа
].join("\n");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };

// Чехол с двумя цветами и кабель, как у ревьюера.
const ЧЕХОЛ = { id: 7, name: "QA Чехол", city: "Минск", price: 20, stock: 4, category: "accessories",
                variants: [{ flavor: "Чёрный", stock: 2 }, { flavor: "Белый", stock: 2 }] };
const КАБЕЛЬ = { id: 8, name: "QA Кабель", city: "Минск", price: 17, stock: 5, category: "accessories", variants: [] };
const копия = (x) => JSON.parse(JSON.stringify(x));

function стенд() {
  const сервер = { товары: [копия(ЧЕХОЛ), копия(КАБЕЛЬ)], сбой: false };
  const часы = { сейчас: 1_000_000 };
  const узлы = {}, тосты = [], значок = [], вкладки = [], закрыто = [];
  const хранилище = new Map();
  const узел = (id) => {
    const н = { id, innerHTML: "", textContent: "", disabled: false, hidden: true, onclick: null, кнопки: [],
      classList: { _к: new Set(), add(x) { this._к.add(x); }, remove(x) { this._к.delete(x); }, contains(x) { return this._к.has(x); } },
      // «Убрать» у строки корзины: разбираем разметку, как это сделал бы браузер.
      querySelectorAll(sel) {
        if (sel !== "[data-cdrop]") return [];
        н.кнопки = [...н.innerHTML.matchAll(/data-cdrop="([^"]*)"/g)].map(m => ({ dataset: { cdrop: m[1] }, onclick: null }));
        return н.кнопки;
      } };
    return н;
  };
  const $ = (id) => узлы[id] || (узлы[id] = узел(id));
  const ctx = vm.createContext({
    $, console, JSON, Math, Promise, Object, Array, String, Number, Set,
    Date: class extends Date { static now() { return часы.сейчас; } },
    window: { scrollY: 0, scrollTo() {} },
    localStorage: { getItem: (k) => (хранилище.has(k) ? хранилище.get(k) : null), setItem: (k, v) => хранилище.set(k, String(v)), removeItem: (k) => хранилище.delete(k) },
    tgUser: { id: 501 }, city: "Минск", activeTab: "catalog", allProducts: [],
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    CUR: "Br", CAT_EMOJI: {}, thumbOf: () => "", bonus: { coins: 0, coin_value: 0.01 }, freeFromCache: { Минск: 0 },
    fetchDeliveryFor: async () => [], паузаТочки: () => null, docsReady: () => false, перечитатьТочки: async () => true,
    openDelivery() {}, openDocs() {}, alertMsg() {}, toast: (m) => тосты.push(m),
    updateFilterBtn() {}, renderGrid() {}, renderFav() {},
    renderNav: () => значок.push(vm.runInContext("cartCount()", ctx)),
    showTab: (id) => { вкладки.push(id); vm.runInContext(`activeTab = ${JSON.stringify(id)}; renderCart()`, ctx); },
    closeOverlay: (ов) => { закрыто.push(ов.id); ов.classList.remove("show"); },
    submitting: false,
    bootFetch: async () => {
      if (сервер.сбой) throw new TypeError("Failed to fetch");
      return копия(сервер.товары);
    },
    bindCardButtons() {},
  });
  vm.runInContext(код, ctx);
  const js = (к) => vm.runInContext(к, ctx);
  return {
    ctx, js, сервер, часы, узлы, тосты, значок, вкладки, закрыто, хранилище,
    корзина: () => узлы["tab-cart"] ? узлы["tab-cart"].innerHTML : "",
    async открыть() { await js("fetchProducts()"); },
    async обновить() { часы.сейчас += 21_000; await js("освежитьВитрину()"); await тик(); },
  };
}
const строки = (html) => [...html.matchAll(/<div class="ci-name">([^<]*)<\/div>/g)].map(m => m[1]);
const кнопка = (html) => { const m = html.match(/<button class="bigbtn" id="checkout"([^>]*)>([^<]*)/); return m ? { выкл: /disabled/.test(m[1]), текст: m[2].trim() } : null; };

(async () => {
  // ---- R1, как у ревьюера: цена 20 → 27, потом чехол сняли с витрины ----
  {
    const с = стенд();
    await с.открыть();
    с.js(`cart[cartKey(7, "Чёрный")] = { product_id: 7, flavor: "Чёрный", qty: 1 };
          cart[cartKey(7, "Белый")] = { product_id: 7, flavor: "Белый", qty: 1 }; activeTab = "cart"; renderCart()`);
    проверка("в корзине два чехла, 40.00 Br", строки(с.корзина()).length === 2 && кнопка(с.корзина()).текст === "Оформить · 40.00 Br", кнопка(с.корзина()));
    с.сервер.товары[0].price = 27;
    await с.обновить();
    проверка("цена 20 → 27: сумма 54.00 Br", кнопка(с.корзина()).текст === "Оформить · 54.00 Br", кнопка(с.корзина()));
    проверка("…и об этом сказано: «QA Чехол — Чёрный»: цена 20.00 → 27.00 Br",
      /«QA Чехол — Чёрный»: цена 20\.00 → 27\.00 Br/.test(с.корзина()), с.корзина().slice(0, 300));
    с.сервер.товары.splice(0, 1);                                   // сняли с витрины
    await с.обновить();
    const html = с.корзина();
    проверка("R1: снятый чехол убран из самой корзины, а не только из списка", с.js("Object.keys(cart).length") === 0, с.js("JSON.stringify(cart)"));
    проверка("…значок 0, а не 2", с.значок.at(-1) === 0, с.значок);
    проверка("…«Корзина пуста» без «Оформить · 0.00 Br»", /Корзина пуста/.test(html) && !кнопка(html), html.slice(0, 200));
    проверка("…и сказано, что убрано: оба цвета — «больше не продаётся — убран из корзины»",
      /«QA Чехол — Чёрный» больше не продаётся — убран из корзины/.test(html) && /«QA Чехол — Белый» больше не продаётся/.test(html), html);
    const сохранено = JSON.parse(с.хранилище.get("partut_cart_v2.501") || "null");
    проверка("…сохранённая корзина тоже без него (перезапуск не вернёт невидимое)", сохранено && Object.keys(сохранено.items).length === 0, сохранено);
    с.узлы.cartChangedOk.onclick();
    проверка("«Понятно» убирает пометку", !/cartChanged/.test(с.корзина()));
    // Добавили другой товар — строка одна, значок 1 (у ревьюера было «строка одна, счётчик 3»).
    с.js(`cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 1 }; renderCart()`);
    с.js("renderNav()");
    проверка("потом кабель: одна строка и значок 1", строки(с.корзина()).length === 1 && с.значок.at(-1) === 1, [строки(с.корзина()), с.значок.at(-1)]);
  }

  // ---- Смешанная корзина: недоступное уходит, остальное и монеты — как были ----
  {
    const с = стенд();
    await с.открыть();
    с.js(`cart[cartKey(7, "Чёрный")] = { product_id: 7, flavor: "Чёрный", qty: 2 };
          cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 2 }; useCoins = true; activeTab = "cart"; renderCart()`);
    с.сервер.товары[0].variants = [{ flavor: "Белый", stock: 2 }];   // «Чёрный» убрали из карточки
    await с.обновить();
    const html = с.корзина();
    проверка("удалённый вариант убран с пометкой «такого варианта больше нет»",
      /«QA Чехол — Чёрный»: такого варианта больше нет — убран из корзины/.test(html) && с.js("!cart[cartKey(7, 'Чёрный')]"), html.slice(0, 300));
    проверка("кабель остался: строка, 2 шт, «Оформить · 34.00 Br», значок 2",
      строки(html).join() === "QA Кабель" && кнопка(html).текст === "Оформить · 34.00 Br" && с.значок.at(-1) === 2, [строки(html), кнопка(html), с.значок]);
    проверка("галочка «списать монеты» не сброшена", с.js("useCoins") === true);
    с.сервер.товары[1].stock = 1;                                      // остаток кабеля уменьшился
    await с.обновить();
    проверка("остаток уменьшился 5 → 1: в корзине 1 шт и сказано «осталось 1 шт — в корзине теперь 1 вместо 2»",
      с.js("cart[cartKey(8)].qty") === 1 && /«QA Кабель»: осталось 1 шт — в корзине теперь 1 вместо 2/.test(с.корзина()), с.корзина().slice(0, 400));
    проверка("…сумма 17.00 Br и значок 1", кнопка(с.корзина()).текст === "Оформить · 17.00 Br" && с.значок.at(-1) === 1);
    проверка("…прежние пометки остаются, пока не нажали «Понятно»", /такого варианта больше нет/.test(с.корзина()));
    с.сервер.товары[1].stock = 0;                                      // кабель разобрали
    await с.обновить();
    проверка("остаток 0: кабель убран с пометкой «разобрали»",
      с.js("Object.keys(cart).length") === 0 && /«QA Кабель»: разобрали — убран из корзины/.test(с.корзина()));
  }

  // ---- Архив и вкус, переименованный регистром ----
  {
    const с = стенд();
    await с.открыть();
    с.js(`cart[cartKey(7, "Белый")] = { product_id: 7, flavor: "Белый", qty: 1 }; cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 1 }; activeTab = "catalog"`);
    с.сервер.товары[0].variants = [{ flavor: "белый", stock: 2 }, { flavor: "Чёрный", stock: 2 }];   // переименовали регистром
    с.сервер.товары.splice(1, 1);                                      // кабель в архив — с витрины пропал
    await с.обновить();
    проверка("вкус «Белый» → «белый» — тот же вкус: в корзине под новым именем, без пометки",
      с.js("cart[cartKey(7, 'белый')] && cart[cartKey(7, 'белый')].qty") === 1 && !с.js("cart[cartKey(7, 'Белый')]")
        && !/QA Чехол/.test(с.js("корзинаИзменилась.join()")), с.js("JSON.stringify(cart)"));
    проверка("кабель из архива убран, а на другой вкладке — всплывашка «Корзина изменилась»",
      !с.js("cart[cartKey(8)]") && с.тосты.at(-1) === "Корзина изменилась — загляните в неё", с.тосты);
    с.js(`activeTab = "cart"; renderCart()`);
    проверка("в корзине — одна строка и пометка про кабель", строки(с.корзина()).join() === "QA Чехол — белый"
      && /«QA Кабель» больше не продаётся/.test(с.корзина()), строки(с.корзина()));
  }

  // ---- Оформление открыто, а корзина поменялась ----
  {
    const с = стенд();
    await с.открыть();
    с.js(`cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 1 }; cart[cartKey(7, "Чёрный")] = { product_id: 7, flavor: "Чёрный", qty: 1 }`);
    с.js(`$("deliveryOverlay").classList.add("show"); activeTab = "cart"`);
    с.сервер.товары.splice(0, 1);
    с.часы.сейчас += 21_000;
    await с.js("освежитьВитрину()"); await тик();
    проверка("окно оформления закрыто и показана корзина с пометкой — оформлять не то, что видел, нельзя",
      с.закрыто.includes("deliveryOverlay") && с.вкладки.at(-1) === "cart" && /больше не продаётся/.test(с.корзина()), [с.закрыто, с.вкладки]);
    const с2 = стенд();
    await с2.открыть();
    с2.js(`cart[cartKey(7, "Чёрный")] = { product_id: 7, flavor: "Чёрный", qty: 1 }; $("deliveryOverlay").classList.add("show"); submitting = true`);
    с2.сервер.товары.splice(0, 1);
    await с2.обновить();
    проверка("…а если заказ уже отправляется — окно не трогаем, решает сервер", !с2.закрыто.length);
  }

  // ---- Невидимой строки не бывает ни при каком пути ----
  {
    const с = стенд();
    await с.открыть();
    // Позиция, которой нет в каталоге, попала в корзину мимо сверки.
    с.js(`cart["99::Синий"] = { product_id: 99, flavor: "Синий", qty: 3 }; cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 1 }; activeTab = "cart"; renderCart()`);
    const html = с.корзина();
    проверка("неизвестная позиция — видимая строка «Товара нет на витрине — Синий» с «Убрать»",
      /Товара нет на витрине — Синий/.test(html) && /data-cdrop="99::Синий"/.test(html), html.slice(0, 400));
    проверка("…«Оформить» выключено: «Уберите то, чего нет на витрине»", кнопка(html).выкл && кнопка(html).текст === "Уберите то, чего нет на витрине", кнопка(html));
    с.узлы["tab-cart"].кнопки[0].onclick();
    проверка("«Убрать» убирает её, значок 1, «Оформить · 17.00 Br»",
      !с.js("cart['99::Синий']") && с.значок.at(-1) === 1 && кнопка(с.корзина()).текст === "Оформить · 17.00 Br", [с.значок, кнопка(с.корзина())]);
  }

  // ---- R2: витрина не обновилась — сказано в корзине, рядом с суммой ----
  {
    const с = стенд();
    await с.открыть();
    с.js(`cart[cartKey(8)] = { product_id: 8, flavor: null, qty: 1 }; activeTab = "cart"; renderCart()`);
    проверка("пока витрина свежая — в корзине предупреждения нет", !/cartStale/.test(с.корзина()));
    с.сервер.сбой = true;
    await с.обновить();
    const html = с.корзина();
    const время = new Date(1_000_000).toTimeString().slice(0, 5);
    проверка(`R2: в корзине «Не удалось обновить витрину — цены и наличие на ${время}» и «↻ Обновить»`,
      /id="cartStale"/.test(html) && html.includes(`цены и наличие на ${время}, могли измениться`) && /id="cartRetry"/.test(html), html.slice(0, 500));
    проверка("…внутри полосы с суммой, перед «Оформить»", html.indexOf('id="cartStale"') > html.indexOf('class="checkoutbar"')
      && html.indexOf('id="cartStale"') < html.indexOf('id="checkout"'));
    проверка("…содержимое на месте: кабель и «Оформить · 17.00 Br»", строки(html).join() === "QA Кабель" && кнопка(html).текст === "Оформить · 17.00 Br");
    // Повтор, пока сети нет: «Загружаю…», потом снова «Обновить», плашка остаётся.
    с.часы.сейчас += 1000;
    с.узлы.cartRetry.onclick();
    проверка("«Обновить» на время запроса — «Загружаю…» и выключена", с.узлы.cartRetry.disabled && с.узлы.cartRetry.textContent === "Загружаю…");
    await тик();
    проверка("сеть не вернулась — плашка осталась, кнопка снова «↻ Обновить»", /id="cartStale"/.test(с.корзина()) && /↻ Обновить/.test(с.корзина()));
    // Переключились в каталог и обратно — состояние не потеряно.
    с.js(`activeTab = "catalog"`); с.js(`activeTab = "cart"; renderCart()`);
    проверка("переключение вкладок не теряет предупреждение", /id="cartStale"/.test(с.корзина()));
    с.сервер.сбой = false;
    с.узлы.cartRetry.onclick();
    await тик();
    проверка("сеть вернулась — после успешного повтора плашки нет", !/cartStale/.test(с.корзина()) && с.js("витринаСвежа") === true);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
