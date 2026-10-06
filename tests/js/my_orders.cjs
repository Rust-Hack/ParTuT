// «Мои заказы»: сбой загрузки — не «заказов нет» (приёмка ROLE-01). Настоящий
// код из 05-orders.js в песочнице Node, сервер — управляемый.
//
// Раньше 503, оборванный ответ или потеря связи превращались в пустой список,
// и покупатель сразу после заказа видел «Заказов пока нет». Теперь пустота —
// только по успешному ответу; при сбое — прежний список с пометкой времени
// или «Не удалось загрузить заказы» с «Повторить».
//
// Запуск: node tests/js/my_orders.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const текст = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "05-orders.js"), "utf8");
const а = текст.indexOf("let myOrders = [];"), б = текст.indexOf("function cancelMyOrder(o)");
if (а < 0 || б < 0) { console.log("❌ не нашёл «Мои заказы» в 05-orders.js"); process.exit(1); }
const код = текст.slice(а, б);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };

// ответы: очередь того, что вернёт сервер на /api/orders по порядку
// ("503" | "обрыв" | "сеть" | {ok:true, orders:[…]} | "ждать" — ответ вручную)
function стенд(ответы) {
  const узлы = {};
  const $ = (id) => узлы[id] || (узлы[id] = { id, innerHTML: "", textContent: "", classList: { add() {}, remove() {} },
    querySelectorAll: (sel) => [...(узлы[id].innerHTML.matchAll(/data-myretry/g))].map(() => ({ disabled: false, textContent: "", onclick: null })) });
  const ждут = [];
  let n = 0;
  const ctx = vm.createContext({
    $, initData: "qa", loaderHtml: () => "⏳", CUR: "Br",
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    OSTATUS: { paid: { label: "Ждёт подтверждения", cls: "paid" }, issued: { label: "Выдан", cls: "issued" } },
    имяПозиции: (it) => it.name, orderTracker: () => "", orderDeductions: () => "",
    fetch: (url) => {
      if (url === "/api/my-reviews") return Promise.resolve({ ok: true, json: async () => ({ ok: true, can: [] }) });
      const о = ответы[n++];
      if (о === "сеть") return Promise.reject(new TypeError("Failed to fetch"));
      if (о === "503") return Promise.resolve({ ok: false, status: 503, json: async () => ({ ok: false, error: "QA" }) });
      if (о === "обрыв") return Promise.resolve({ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected end of JSON input"); } });
      if (о === "ждать") return new Promise(r => ждут.push((тело) => r({ ok: true, status: 200, json: async () => тело })));
      return Promise.resolve({ ok: true, status: 200, json: async () => о });
    },
  });
  vm.runInContext(код, ctx);
  return { ctx, ждут, js: (к) => vm.runInContext(к, ctx), html: () => узлы.myOrdersList ? узлы.myOrdersList.innerHTML : "" };
}
const заказ = { id: 2, status: "issued", items: [{ name: "QA чехол", qty: 1, price: 20 }], total: 20 };
const ок = (orders) => ({ ok: true, orders });

(async () => {
  for (const [сбой, что] of [["503", "503"], ["обрыв", "оборванный ответ"], ["сеть", "потеря связи"]]) {
    const с = стенд([сбой]);
    await с.js("openMyOrders()"); await тик();
    const h = с.html();
    проверка(`${что} при первом открытии — не «Заказов пока нет», а «Не удалось загрузить заказы» с «Повторить»`,
      !/Заказов пока нет/.test(h) && /Не удалось загрузить заказы/.test(h) && /data-myretry/.test(h), h.replace(/\s+/g, " ").slice(0, 160));
  }
  {
    const с = стенд([ок([])]);
    await с.js("openMyOrders()"); await тик();
    проверка("успешный пустой ответ — «Заказов пока нет», без предупреждений",
      /Заказов пока нет/.test(с.html()) && !/Не удалось/.test(с.html()));
  }
  {
    // Был заказ — потом 503: заказ на месте, с пометкой времени и «Повторить».
    const с = стенд([ок([заказ]), "503", ок([заказ])]);
    await с.js("openMyOrders()"); await тик();
    проверка("заказ загрузился — виден", /Заказ #2/.test(с.html()) && !/Не удалось/.test(с.html()));
    await с.js("openMyOrders()"); await тик();
    const h = с.html();
    проверка("503 при повторном открытии — заказ остался, сверху «Не удалось обновить заказы — показан список на ЧЧ:ММ»",
      /Заказ #2/.test(h) && /Не удалось обновить заказы — показан список на \d\d:\d\d/.test(h) && !/Заказов пока нет/.test(h),
      h.replace(/\s+/g, " ").slice(0, 200));
    await с.js("openMyOrders()"); await тик();
    проверка("«Повторить» при живой сети — список свежий, предупреждения нет", /Заказ #2/.test(с.html()) && !/Не удалось/.test(с.html()));
  }
  {
    // Прежде история была пуста, теперь 503: «заказов нет» показывать нельзя —
    // заказ мог появиться с тех пор (живой обход стенда, ROLE-01).
    const с = стенд([ок([]), "503"]);
    await с.js("openMyOrders()"); await тик();
    await с.js("openMyOrders()"); await тик();
    проверка("была пустая история, теперь 503 — «Не удалось загрузить заказы», а не «Заказов пока нет»",
      /Не удалось загрузить заказы/.test(с.html()) && !/Заказов пока нет/.test(с.html()), с.html().replace(/\s+/g, " ").slice(0, 160));
  }
  {
    // Повтор после сбоя первого открытия восстанавливает настоящий заказ.
    const с = стенд(["сеть", ок([заказ])]);
    await с.js("openMyOrders()"); await тик();
    await с.js("openMyOrders()"); await тик();
    проверка("после «Повторить» — настоящий заказ, а не пустота", /Заказ #2/.test(с.html()) && !/Не удалось/.test(с.html()));
  }
  {
    // Поздний ответ старого запроса не затирает свежий.
    const с = стенд(["ждать", "ждать"]);
    const первый = с.js("openMyOrders()");
    const второй = с.js("openMyOrders()");
    с.ждут[1](ок([заказ]));                                       // новый пришёл первым
    await тик();
    с.ждут[0](ок([]));                                            // старый — позже, пустой
    await Promise.all([первый, второй]); await тик();
    проверка("старый ответ пришёл позже — на экране свежий список, а не старая пустота",
      /Заказ #2/.test(с.html()) && !/Заказов пока нет/.test(с.html()));
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
