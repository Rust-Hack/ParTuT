// «🧾 Продажа на точке» — настоящий код из 09-point-sale.js в песочнице Node.
// Проверяет то, на чём продажа могла бы задвоиться или потеряться: ключ
// попытки в черновике ДО запроса; потерянный ответ — экран сам находит свою
// продажу в «Сегодня» по ключу (иначе остаток уже уменьшен, и тот же чек
// выглядел бы «больше, чем на полке»); исправленный чек с прежним ключом;
// строки чека (перебор, кривые числа, цена с запятой).
//
// Запуск: node tests/js/point_sale.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const код = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "09-point-sale.js"), "utf8");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// сеть: { "/api/admin/sale": ответ | "throw" | (тело) => ответ, "/api/admin/sales": ... }
function стенд(сеть, настройки = {}) {
  const узлы = {};
  const $ = (id) => узлы[id] || (узлы[id] = (() => {
    const к = new Set();
    return { id, value: "", textContent: "", innerHTML: "", disabled: false, hidden: false, классы: к,
      classList: { add: (x) => к.add(x), remove: (x) => к.delete(x), toggle: (x, да) => (да ? к.add(x) : к.delete(x)), contains: (x) => к.has(x) },
      querySelectorAll: () => [], querySelector: () => null };
  })());
  const хранилище = new Map(Object.entries(настройки.хранилище || {}));
  const сказано = [], тосты = [], запросы = [];
  const полка = настройки.полка || [
    { id: 1, city: "Минск", name: "Жижа", price: 25, stock: 3, variants: [{ flavor: "Мята", stock: 2 }, { flavor: "Вишня", stock: 1 }] },
    { id: 2, city: "Минск", name: "Зарядка", price: 15, stock: 4, variants: [] },
  ];
  const ctx = vm.createContext({
    $, console, JSON, Math, Promise, Object, Array, String, Number, initData: "qa",
    document: { querySelectorAll: () => [], createElement: () => ({}) },
    localStorage: { getItem: (k) => (хранилище.has(k) ? хранилище.get(k) : null), setItem: (k, v) => хранилище.set(k, String(v)),
                    removeItem: (k) => хранилище.delete(k) },
    shelf: () => полка, myScope: () => "Минск", locations: [{ name: "Минск" }], admLocFilter: "all",
    hasVariants: (p) => !!(p.variants && p.variants.length), деньги: (n) => (Math.round(n * 100) / 100).toFixed(2),
    человек: () => "7", новыйКлючОперации: () => "KEY" + (++ctx.__ключей), __ключей: 0,
    plural: (n, a, b, c) => (n === 1 ? a : n < 5 && n > 1 ? b : c), esc: (s) => String(s ?? ""),
    toast: (m) => тосты.push(m), alertMsg: (m) => сказано.push(m), confirmMsg: (m, да) => { ctx.__да = да(); },
    refreshProducts: async () => true, renderAdminList() {},
    // Свежесть списка управления (приёмка UX-48-01): грузится ли и пришёл ли.
    админГрузится: настройки.грузится || 0, админСписокСвеж: настройки.свеж !== false,
    fetchAdminProducts: async () => true, остаткиПродажиПришли() {},
    fetch: async (url, o) => {
      const тело = JSON.parse(o.body);
      запросы.push({ url, тело, вХранилище: хранилище.get("partut_sale_v1.7.Минск") || null });
      let ответ = сеть[url];
      if (typeof ответ === "function") ответ = ответ(тело);
      if (ответ === "throw" || ответ === undefined) throw new TypeError("Failed to fetch");
      return { json: async () => ответ };
    },
  });
  vm.runInContext(require("./_search.cjs") + "\n" + код, ctx);      // поиск — настоящий (ROLE-04/05)
  const js = (к) => vm.runInContext(к, ctx);
  return { ctx, js, сказано, тосты, запросы, хранилище, узлы,
           ждать: async () => { for (let i = 0; i < 5; i++) { await (ctx.__да || null); await new Promise(r => setImmediate(r)); } },
           чек: () => JSON.parse(хранилище.get("partut_sale_v1.7.Минск") || "null") };
}
const ок = (тело) => ({ ok: true, id: 501, total: 50, replay: false });
const список = (продажи) => ({ ok: true, sales: продажи });

(async () => {
  // ---- Строки чека ----
  {
    const с = стенд({ "/api/admin/sales": список([]) });
    с.js("openSale()");
    await с.ждать();
    с.js(`добавитьВЧек(1); добавитьВЧек(2);
          сЧек.rows[строкаКлюч(1, "Мята")] = { pid: 1, flavor: "Мята", qty: "2" };`);
    let и = с.js("строкиЧека()");
    проверка("зарядка добавилась с «1», вкус — как вписан: 3 шт на 65 Br", и.штук === 3 && и.сумма === 65 && !и.ошибок, и);
    с.js(`сЧек.rows[строкаКлюч(1, "Вишня")] = { pid: 1, flavor: "Вишня", qty: "2" }`);
    проверка("вишни на полке 1, вписано 2 — ошибка строки", с.js("строкиЧека()").ошибок === 1);
    с.js(`сЧек.rows[строкаКлюч(1, "Вишня")].qty = "1,5"`);
    проверка("«1,5» штуки — ошибка строки", с.js("строкиЧека()").ошибок === 1);
    с.js(`сЧек.rows[строкаКлюч(1, "Вишня")].qty = ""; сЧек.prices[2] = "13,50"`);
    и = с.js("строкиЧека()");
    проверка("цена с запятой «13,50» — 13.5", и.строки.find(x => x.id === 2).price === 13.5 && !и.ошибок, и);
    с.js(`сЧек.prices[2] = "дёшево"`);
    проверка("цена не числом — ошибка", с.js("строкиЧека()").ошибок === 1);
  }

  // ---- Провести: ключ до запроса, успех — чек заново ----
  {
    const с = стенд({ "/api/admin/sale": ок, "/api/admin/sales": список([]) });
    с.js("openSale(); добавитьВЧек(2)");
    с.js("провестиПродажу()");
    await с.ждать();
    const первый = с.запросы.find(з => з.url === "/api/admin/sale");
    проверка("ключ попытки лежал в черновике ДО запроса", первый && JSON.parse(первый.вХранилище).token === первый.тело.client_token
      && первый.тело.client_token === "KEY1");
    проверка("ушла строка «зарядка × 1 по 15», наличными", JSON.stringify(первый.тело.lines) === '[{"id":2,"flavor":null,"qty":1,"price":15}]'
      && первый.тело.payment === "cash");
    проверка("записано — чек пуст, «Продано»", с.чек() === null && с.js("сЧек.order.length") === 0 && /^Продано/.test(с.тосты.at(-1)));
  }

  // ---- Отказ сервера — чек и ключ на месте ----
  {
    const с = стенд({ "/api/admin/sale": { ok: false, error: "short", message: "«Зарядка»: на полке 0 шт" }, "/api/admin/sales": список([]) });
    с.js("openSale(); добавитьВЧек(2); провестиПродажу()");
    await с.ждать();
    проверка("отказ — словами сервера, чек на месте", с.сказано.at(-1) === "«Зарядка»: на полке 0 шт" && с.чек().order.length === 1);
  }

  // ---- Тот же ключ, другой чек — ключ отслужил ----
  {
    const с = стенд({ "/api/admin/sale": { ok: false, error: "token_reused", message: "Прошлая продажа уже записана — №9" },
                      "/api/admin/sales": список([]) });
    с.js("openSale(); добавитьВЧек(2); сЧек.token = 'OLDKEY'; провестиПродажу()");
    await с.ждать();
    проверка("token_reused — следующая попытка пойдёт с новым ключом", с.чек().token === null && /№9/.test(с.сказано.at(-1)));
  }

  // ---- Ответ потерян: продажа в «Сегодня» есть — «записана», чек заново ----
  {
    let записана = false;
    const с = стенд({ "/api/admin/sale": () => { записана = true; return "throw"; },
                      "/api/admin/sales": () => список(записана ? [{ id: 7, total: 15, status: "issued", token: "KEY1", items: [] }] : []) });
    с.js("openSale(); добавитьВЧек(2); провестиПродажу()");
    await с.ждать();
    проверка("ответ потерян, но продажа в «Сегодня» по ключу — «записана», без «не знаю»",
      /Продажа записана/.test(с.тосты.at(-1) || "") && !с.сказано.some(m => /не знаю/.test(m)) && с.чек() === null);
  }

  // ---- Ответ потерян, продажи в «Сегодня» нет — не записалась, чек на месте ----
  {
    const с = стенд({ "/api/admin/sale": "throw", "/api/admin/sales": список([]) });
    с.js("openSale(); добавитьВЧек(2); провестиПродажу()");
    await с.ждать();
    проверка("ответ потерян, в «Сегодня» её нет — «не записалась», чек и ключ на месте",
      /не записалась/.test(с.сказано.at(-1) || "") && с.чек().order.length === 1 && с.чек().token === "KEY1");
  }

  // ---- Ответ потерян и «Сегодня» не загрузилось — честно «не знаю» ----
  {
    const с = стенд({ "/api/admin/sale": "throw", "/api/admin/sales": "throw" });
    с.js("openSale(); добавитьВЧек(2); провестиПродажу()");
    await с.ждать();
    проверка("всё неизвестно — так и сказано, чек на месте",
      /не знаю, прошла ли продажа, и список проверить не вышло/.test(с.сказано.at(-1) || "") && с.чек().order.length === 1);
  }

  // ---- Перезапуск: чек с ключом, продажа с этим ключом уже в списке ----
  {
    const черновик = { order: [2], rows: { "[2,\"\"]": { pid: 2, flavor: null, qty: "1" } }, prices: { 2: "15.00" }, payment: "cash", token: "KEYOLD" };
    const с = стенд({ "/api/admin/sales": список([{ id: 8, total: 15, status: "issued", token: "KEYOLD", items: [] }]) },
                    { хранилище: { "partut_sale_v1.7.Минск": JSON.stringify(черновик) } });
    с.js("openSale()");
    await с.ждать();
    проверка("после перезапуска чек узнал свою записанную продажу — очищен", с.чек() === null && /Продажа записана/.test(с.тосты.at(-1) || ""));
    const чужой = стенд({ "/api/admin/sales": список([{ id: 8, total: 15, status: "issued", token: "OTHER", items: [] }]) },
                        { хранилище: { "partut_sale_v1.7.Минск": JSON.stringify(черновик) } });
    чужой.js("openSale()");
    await чужой.ждать();
    проверка("чужой ключ в списке — чек не трогаем", чужой.чек() && чужой.чек().order.length === 1);
  }

  // ---- Поиск в «Продаже»: варианты и «е» = «ё» (приёмки ROLE-04/05) ----
  {
    const с = стенд({ "/api/admin/sales": список([]) }, { полка: [
      { id: 7, city: "Минск", name: "QA чехол", price: 20, stock: 3, variants: [{ flavor: "Чёрный", stock: 2 }, { flavor: "Белый", stock: 1 }] },
      { id: 8, city: "Минск", name: "Кабель", price: 10, stock: 5, variants: [] },
    ] });
    с.js("openSale()");
    await с.ждать();
    const нашлось = (q) => { с.js(`сПоиск = ${JSON.stringify(q)}; нарисоватьНайденноеПродажи()`);
      return [...с.узлы.saleFound.innerHTML.matchAll(/data-sadd="(\d+)"/g)].map(m => +m[1]).join(); };
    проверка("«черный» в «Продаже» находит чехол с вариантом «Чёрный»", нашлось("черный") === "7", нашлось("черный"));
    проверка("«белый» — тот же чехол, «кабель» — кабель", нашлось("белый") === "7" && нашлось("кабель") === "8");
  }

  // ---- Свежесть остатков в чеке (приёмка UX-48-01) ----
  {
    const свежо = стенд({ "/api/admin/sales": список([]) });
    свежо.js("openSale()");
    await свежо.ждать();
    проверка("остатки свежие — строки «не обновились» нет, цифры не приглушены",
      свежо.узлы.saleStale.hidden === true && !свежо.узлы.saleView.классы.has("stockupd"));
    const грузится = стенд({ "/api/admin/sales": список([]) }, { грузится: 1, свеж: false });
    грузится.js("openSale()");
    await грузится.ждать();
    проверка("остатки ещё грузятся — цифры приглушены, предупреждения нет (рано)",
      грузится.узлы.saleView.классы.has("stockupd") && грузится.узлы.saleStale.hidden === true);
    const сбой = стенд({ "/api/admin/sales": список([]) }, { свеж: false });
    сбой.js("openSale()");
    await сбой.ждать();
    проверка("остатки не загрузились — так и сказано, с «↻ Повторить»",
      сбой.узлы.saleStale.hidden === false && /Остатки не обновились/.test(сбой.узлы.saleStale.innerHTML)
        && /id="saleRetry"/.test(сбой.узлы.saleStale.innerHTML) && typeof сбой.узлы.saleRetry.onclick === "function",
      сбой.узлы.saleStale.innerHTML);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
