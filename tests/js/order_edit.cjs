// Правка состава заказа — настоящий код из 05-orders.js в песочнице Node.
//
// П-4 (проход 6.10, разбор ревьюера): продавец убавлял всё до нуля — «Итого
// станет 0.00 Br», «Сохранить состав» оставалась как была и вела в тупик:
// «Пустой заказ — это отмена. Закройте окно и нажмите «Отклонить»». Теперь
// кнопка сама становится «Отклонить заказ…» и открывает окно причины; заказ
// при этом НЕ отменяется — только окно отказа, после выбора причины.
//
// Запуск: node tests/js/order_edit.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const исходник = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "05-orders.js"), "utf8");
const а = исходник.indexOf("// ----- Правка состава заказа -----"), б = исходник.indexOf("async function orderAction(", а);
if (а < 0 || б < 0) { console.log("❌ не нашёл правку состава в 05-orders.js"); process.exit(1); }
const код = исходник.slice(а, б);                 // правка состава и окно отказа — вместе, как в приложении

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r)); };

function стенд(заказ) {
  const узлы = {}, запросы = [], сказано = [];
  const узел = (id) => {
    const н = { id, value: "", textContent: "", innerHTML: "", disabled: false, hidden: false, onclick: null, кнопки: [],
      _к: new Set(),
      classList: { add: (x) => н._к.add(x), remove: (x) => н._к.delete(x), toggle: (x, да) => (да ? н._к.add(x) : н._к.delete(x)), contains: (x) => н._к.has(x) },
      querySelectorAll(sel) {
        if (sel === "[data-oq]") return (н.кнопки = [...н.innerHTML.matchAll(/data-oq="(-?1)" data-i="(\d+)"/g)].map(m => ({ dataset: { oq: m[1], i: m[2] }, onclick: null })));
        if (sel === "[data-rr]") return (н.кнопки = [...н.innerHTML.matchAll(/<button class="opt ([^"]*)" data-rr="([^"]*)">([^<]*)<\/button>/g)]
          .map(m => ({ активна: /active/.test(m[1]), dataset: { rr: m[2] }, текст: m[3], onclick: null })));
        return [];
      } };
    return н;
  };
  const $ = (id) => узлы[id] || (узлы[id] = узел(id));
  const ctx = vm.createContext({
    $, console, JSON, Math, Promise, Object, adminOrders: [заказ], initData: "qa",
    имяПозиции: (it) => it.name, esc: (s) => String(s ?? ""), alertMsg: (m) => сказано.push(m), toast() {},
    текстСбоя: (e) => String(e), loadAdminOrders: async () => {}, refreshProducts: async () => {}, loadToday() {},
    fetch: async (url, o) => { запросы.push({ url, тело: JSON.parse(o.body) }); return { json: async () => ({ ok: true, order: { total: 0 } }) }; },
    админПост: async (url, тело) => { запросы.push({ url, тело }); return { ok: true }; },
  });
  vm.runInContext(код, ctx);
  const кнопкаКол = (i, знак) => $("oeditItems").кнопки.find(b => b.dataset.i === String(i) && b.dataset.oq === знак).onclick();
  return { $, запросы, сказано, js: (к) => vm.runInContext(к, ctx), кнопкаКол,
           открыть: () => vm.runInContext(`openOrderEdit(${заказ.id})`, ctx) };
}
const заказ = (поз) => ({ id: 41, city: "Минск", status: "paid", total: 25, payment_method: "card", promo_discount: 0, coins_discount: 0,
  delivery_fee: 0, items: поз });

(async () => {
  {
    const с = стенд(заказ([{ name: "Жижа — Мята", price: 25, qty: 1 }]));
    с.открыть();
    const кн = с.$("oeditSave");
    проверка("открыли правку — «Сохранить состав», подсказки про отказ нет",
      кн.textContent === "Сохранить состав" && !кн.classList.contains("danger") && с.$("oeditEmpty").hidden === true);
    с.кнопкаКол(0, "-1");
    проверка("П-4: убавили до нуля — «Итого станет 0.00 Br» и кнопка «Отклонить заказ…» (красная)",
      с.$("oeditTotal").textContent === "0.00 Br" && кн.textContent === "Отклонить заказ…" && кн.classList.contains("danger"), кн.textContent);
    проверка("…и сказано, что это отказ и что дальше", с.$("oeditEmpty").hidden === false);
    await кн.onclick(); await тик();
    проверка("…нажали — правка закрылась, открылось окно причины",
      !с.$("oeditView").classList.contains("show") && с.$("orejView").classList.contains("show") && с.$("orejTitle").textContent === "Заказ #41");
    проверка("…заказ сам не отменён и состав не отправлен — ни одного запроса", с.запросы.length === 0, с.запросы);
    проверка("…и причина в окне не выбрана заранее (П-3)", !с.$("orejReasons").кнопки.some(b => b.активна) && с.$("orejGo").disabled);
    проверка("тупикового «Закройте окно и нажмите «Отклонить»» больше нет", !с.сказано.some(m => /Закройте окно/.test(m)), с.сказано);
  }
  {
    const с = стенд(заказ([{ name: "Жижа — Мята", price: 25, qty: 1 }, { name: "Кабель", price: 10, qty: 2 }]));
    с.открыть();
    с.кнопкаКол(0, "-1");
    проверка("убрали одну позицию из двух — это правка: «Сохранить состав»", с.$("oeditSave").textContent === "Сохранить состав");
    с.кнопкаКол(1, "-1"); с.кнопкаКол(1, "-1");
    проверка("убрали и вторую — «Отклонить заказ…»", с.$("oeditSave").textContent === "Отклонить заказ…");
    с.кнопкаКол(1, "1");
    проверка("вернули штуку — снова «Сохранить состав», подсказка ушла",
      с.$("oeditSave").textContent === "Сохранить состав" && !с.$("oeditSave").classList.contains("danger") && с.$("oeditEmpty").hidden === true);
    await с.$("oeditSave").onclick(); await тик();
    проверка("…и «Сохранить состав» сохраняет правку как раньше", с.запросы.some(з => з.url === "/api/admin/order/items"), с.запросы);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
