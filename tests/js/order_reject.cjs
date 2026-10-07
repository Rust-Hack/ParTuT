// Окно «✖️ Отклонить» заказ — настоящий код из 05-orders.js в песочнице Node.
//
// П-3 (проход 6.10, разбор ревьюера): окно открывалось с уже отмеченной
// причиной «Товара не оказалось». Отклонил не глядя — и покупатель с
// неподошедшим чеком читал «товар разобрали». Теперь причина — только та, что
// выбрали; «Без причины» — тоже выбор; до выбора «Отклонить» выключено.
//
// Запуск: node tests/js/order_reject.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const исходник = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "05-orders.js"), "utf8");
const а = исходник.indexOf("// ----- Отказ с причиной -----"), б = исходник.indexOf("async function orderAction(", а);
if (а < 0 || б < 0) { console.log("❌ не нашёл окно отказа в 05-orders.js"); process.exit(1); }
const код = исходник.slice(а, б);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r)); };

function стенд(ответ = { ok: true }) {
  const узлы = {}, запросы = [];
  const узел = (id) => {
    const н = { id, value: "", textContent: "", disabled: false, innerHTML: "", onclick: null, кнопки: [],
      _к: new Set(),
      classList: { add: (x) => н._к.add(x), remove: (x) => н._к.delete(x), contains: (x) => н._к.has(x) },
      // Кнопки причин: разбираем разметку, как это сделал бы браузер.
      querySelectorAll(sel) {
        if (sel !== "[data-rr]") return [];
        н.кнопки = [...н.innerHTML.matchAll(/<button class="opt ([^"]*)" data-rr="([^"]*)">([^<]*)<\/button>/g)]
          .map(m => ({ активна: /active/.test(m[1]), dataset: { rr: m[2] }, текст: m[3], onclick: null }));
        return н.кнопки;
      } };
    return н;
  };
  const $ = (id) => узлы[id] || (узлы[id] = узел(id));
  const ctx = vm.createContext({
    $, console, JSON, Promise,
    админПост: async (url, тело) => { запросы.push({ url, тело }); return typeof ответ === "function" ? ответ(тело) : ответ; },
    loadAdminOrders: async () => {}, refreshProducts: async () => {}, loadToday() {},
  });
  vm.runInContext(код, ctx);
  const причины = () => $("orejReasons").кнопки;
  return {
    $, запросы, js: (к) => vm.runInContext(к, ctx),
    открыть: (id) => { vm.runInContext(`openOrderReject(${id})`, ctx); },
    выбрать: (текст) => { причины().find(b => b.текст === текст).onclick(); },
    активная: () => (причины().find(b => b.активна) || {}).текст || null,
    отклонить: async () => { await $("orejGo").onclick(); await тик(); },
  };
}

(async () => {
  {
    const с = стенд();
    с.открыть(12);
    проверка("П-3: окно открылось без отмеченной причины", с.активная() === null, с.активная());
    проверка("…«Отклонить» выключено и говорит «Сначала выберите причину»",
      с.$("orejGo").disabled && с.$("orejGo").textContent === "Сначала выберите причину", [с.$("orejGo").disabled, с.$("orejGo").textContent]);
    await с.отклонить();
    проверка("…нажатие на выключенную кнопку ничего не отправляет", с.запросы.length === 0, с.запросы);
    с.выбрать("Чек не подошёл");
    проверка("выбрал «Чек не подошёл» — она отмечена, кнопка «Отклонить и вернуть товар»",
      с.активная() === "Чек не подошёл" && !с.$("orejGo").disabled && с.$("orejGo").textContent === "Отклонить и вернуть товар");
    с.$("orejNote").value = "переведите ещё раз";
    await с.отклонить();
    проверка("ушла выбранная причина и приписка", JSON.stringify(с.запросы[0].тело) ===
      JSON.stringify({ id: 12, action: "reject", reason: "receipt", note: "переведите ещё раз" }), с.запросы[0].тело);
    проверка("окно закрылось", !с.$("orejView").classList.contains("show"));
    с.открыть(13);
    проверка("следующий заказ — снова без причины, прошлая не запоминается", с.активная() === null && с.$("orejGo").disabled);
    с.выбрать("Без причины");
    проверка("«Без причины» — тоже выбор: кнопка включилась", с.активная() === "Без причины" && !с.$("orejGo").disabled);
    await с.отклонить();
    проверка("…и ушла пустая причина — сервер скажет покупателю общее «напишите нам»", с.запросы[1].тело.reason === "", с.запросы[1].тело);
  }
  {
    const с = стенд(null);                          // админПост вернул null — сервер отказал, окно сказало
    с.открыть(14);
    с.выбрать("Товара не оказалось");
    await с.отклонить();
    проверка("сервер не отклонил — окно остаётся открытым с выбранной причиной",
      с.$("orejView").classList.contains("show") && с.активная() === "Товара не оказалось");
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
