// Быстрая цена из списка «Цены и остатки» — настоящий код из 06-catalog.js в
// песочнице Node, с подменой экрана и сети. Проверяет то, что серверные
// тесты не видят: что именно экран ОТПРАВИТ и что станет с окном и вводом.
//
// Запуск: node tests/js/quick_price.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const файл = path.join(__dirname, "..", "..", "partut", "webapp", "app", "06-catalog.js");
const исходник = fs.readFileSync(файл, "utf8");
const начало = исходник.indexOf("// ----- Быстрая цена -----");
const конец = исходник.indexOf("// ----- /Быстрая цена -----");
if (начало < 0 || конец < 0) { console.log("❌ не нашёл код быстрой цены в 06-catalog.js"); process.exit(1); }
const код = исходник.slice(начало, конец);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то повисло"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

function узел() {
  const классы = new Set();
  return {
    value: "", textContent: "", innerHTML: "", disabled: false, style: {},
    classList: { add: (k) => классы.add(k), remove: (k) => классы.delete(k), contains: (k) => классы.has(k) },
    focus() {}, select() {},
  };
}

function стенд() {
  const узлы = new Map();
  const $ = (id) => { if (!узлы.has(id)) узлы.set(id, узел()); return узлы.get(id); };
  const товар = () => ({ id: 1, name: "XROS 3", city: "Минск", price: 25, stock: 4 });
  const adminProducts = [товар()], allProducts = [товар()];
  const журнал = { posts: [], toasts: [], перерисовок: 0, обновлений: 0, закрытий: 0 };
  let ответ = () => ({ status: 200, body: { ok: true, saved: ["price"] } });
  const ctx = vm.createContext({
    $, adminProducts, allProducts, shelf: () => adminProducts, initData: "qa",
    ОТКАЗЫ: {}, текстСбоя: () => "Сеть недоступна.",
    toast: (m) => журнал.toasts.push(m),
    renderAdminList: () => { журнал.перерисовок++; },
    refreshProducts: () => { журнал.обновлений++; },
    closeOverlay: (ov) => { журнал.закрытий++; ov.classList.remove("show"); },
    setTimeout: (f) => f(),
    fetch: async (url, opts) => {
      журнал.posts.push({ url, body: JSON.parse(opts.body) });
      const о = ответ();
      if (о.throws) throw new Error("network");
      return { ok: о.status < 400, status: о.status, json: async () => о.body };
    },
  });
  vm.runInContext(код, ctx);
  return {
    $, журнал, adminProducts, allProducts,
    js: (строка) => vm.runInContext(строка, ctx),
    ответ: (f) => { ответ = f; },
    сохранить: async () => { await vm.runInContext("сохранитьЦену()", ctx); },
    открыто: () => $("priceOverlay").classList.contains("show"),
  };
}

(async () => {
  // ---------- Открыли окно ----------
  {
    const с = стенд();
    с.js("открытьЦену(1)");
    проверка("окно открыто", с.открыто());
    проверка("подпись: только эта точка", /Только точка «Минск»/.test(с.$("priceScope").textContent), с.$("priceScope").textContent);
    проверка("видно, какая цена сейчас", /25\.00 Br/.test(с.$("priceNow").innerHTML), с.$("priceNow").innerHTML);
    проверка("в поле — текущая цена", с.$("priceNew").value === "25.00", с.$("priceNew").value);

    // ---------- Сохранили ----------
    с.$("priceNew").value = "30,5";
    await с.сохранить();
    const отправлено = с.журнал.posts[0] && с.журнал.posts[0].body;
    проверка("ушла только цена", отправлено && JSON.stringify(Object.keys(отправлено.fields)) === '["price"]'
      && отправлено.fields.price === 30.5, отправлено);
    проверка("вместе с ценой, какую видел человек", отправлено && отправлено.expected && отправлено.expected.price === 25, отправлено);
    проверка("цена обновлена у себя, без перечитывания каталога",
      с.adminProducts[0].price === 30.5 && с.allProducts[0].price === 30.5);
    проверка("список перерисован (поиск и прокрутка — внутри перерисовки)", с.журнал.перерисовок >= 1);
    проверка("окно закрыто", !с.открыто());
    проверка("сказано, что было и что стало", /25\.00 → 30\.50/.test(с.журнал.toasts.join(" ")), с.журнал.toasts);
  }

  // ---------- Цену успел поменять другой ----------
  {
    const с = стенд();
    с.js("открытьЦену(1)");
    с.$("priceNew").value = "30";
    с.ответ(() => ({ status: 200, body: { ok: true, saved: [], failed: { price: { error: "conflict", current: 27, message: "уже поменяли" } } } }));
    await с.сохранить();
    проверка("конфликт: окно осталось открытым", с.открыто());
    проверка("конфликт: введённое осталось в поле", с.$("priceNew").value === "30", с.$("priceNew").value);
    проверка("конфликт: показано, что там теперь", /сейчас 27\.00 Br/.test(с.$("priceMsg").textContent)
      && /27\.00 Br/.test(с.$("priceNow").innerHTML), { msg: с.$("priceMsg").textContent, now: с.$("priceNow").innerHTML });
    проверка("конфликт: у себя цена уже чужая, а не наша", с.adminProducts[0].price === 27);

    с.ответ(() => ({ status: 200, body: { ok: true, saved: ["price"] } }));
    await с.сохранить();
    const второй = с.журнал.posts[1] && с.журнал.posts[1].body;
    проверка("повторное «Сохранить» — осознанное, со свежим снимком", второй && второй.expected.price === 27 && второй.fields.price === 30, второй);
    проверка("и теперь цена наша", с.adminProducts[0].price === 30 && !с.открыто());
  }

  // ---------- Там уже ровно наша цена (прошлое нажатие дошло) ----------
  {
    const с = стенд();
    с.js("открытьЦену(1)");
    с.$("priceNew").value = "30";
    с.ответ(() => ({ status: 200, body: { ok: true, saved: [], failed: { price: { error: "conflict", current: 30 } } } }));
    await с.сохранить();
    проверка("там уже наша цена — это успех, а не конфликт", !с.открыто() && /Цена уже 30\.00/.test(с.журнал.toasts.join(" ")), с.журнал.toasts);
  }

  // ---------- Неверный ввод и та же цена ----------
  {
    const с = стенд();
    с.js("открытьЦену(1)");
    for (const плохо of ["0", "-5", "abc", ""]) {
      с.$("priceNew").value = плохо;
      await с.сохранить();
    }
    проверка("ноль, минус, буквы, пусто — ничего не отправлено", с.журнал.posts.length === 0, с.журнал.posts.length);
    проверка("и сказано, какое число нужно", /больше нуля/.test(с.$("priceMsg").textContent));
    с.$("priceNew").value = "25";
    await с.сохранить();
    проверка("та же цена — не отправляется, окно закрывается", с.журнал.posts.length === 0 && !с.открыто());
  }

  // ---------- Сбои ----------
  {
    const с = стенд();
    с.js("открытьЦену(1)");
    с.$("priceNew").value = "31";
    с.ответ(() => ({ status: 503, body: { ok: false, error: "server_error" } }));
    await с.сохранить();
    проверка("сервер упал — окно открыто, введённое на месте", с.открыто() && с.$("priceNew").value === "31");
    проверка("и цена у себя не тронута", с.adminProducts[0].price === 25);
    с.ответ(() => ({ throws: true }));
    await с.сохранить();
    проверка("нет сети — сказано, что цена могла сохраниться и повтор проверит",
      с.открыто() && /могла сохраниться/.test(с.$("priceMsg").textContent), с.$("priceMsg").textContent);
    проверка("кнопка снова доступна", с.$("priceSave").disabled === false);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})().catch((e) => { console.log("❌ упало: " + (e && e.stack || e)); process.exit(1); });
