// Действия архива — настоящий код из 06-catalog.js в песочнице Node: что
// приложение говорит человеку, когда ответ не дошёл и когда перечитать списки
// удалось лишь отчасти (перепроверка приёмки архива, 945e728: «Список
// обновлён» сообщалось, хотя товары точек не загрузились).
//
// Запуск: node tests/js/archive_actions.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const исходник = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "06-catalog.js"), "utf8");
const начало = исходник.indexOf("// ----- Архив: действия -----");
const конец = исходник.indexOf("// Убираем заставку по факту готовности данных");
if (начало < 0 || конец < 0) { console.log("❌ не нашёл действия архива в 06-catalog.js"); process.exit(1); }
const код = исходник.slice(начало, конец);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

// сеть: { "/api/...": ответ | "throw" | число (HTTP-код без тела) }
function стенд(сеть, настройки = {}) {
  const сказано = [], тосты = [], вопросы = [], ждём = [];
  const ctx = vm.createContext({
    console, JSON, Promise, Array, initData: "qa",
    $: (id) => ({ classList: { contains: () => id === "productsView" } }),
    shelf: () => настройки.полка || [{ id: 1, name: "Кабель", city: "Минск", model_id: 7 }],
    refreshProducts: async () => настройки.спискиЗагрузились !== false,
    renderAdminList() {}, обновитьБлокТочек() {},
    alertMsg: (m) => сказано.push(m), toast: (m) => тосты.push(m), confirmMsg: (m, да) => { вопросы.push(m); ждём.push(да()); },
    fetch: async (url) => {
      const о = сеть[url];
      if (о === "throw" || о === undefined) throw new TypeError("Failed to fetch");
      if (typeof о === "number") return { json: async () => { throw new SyntaxError("not json"); } };
      return { json: async () => о };
    },
  });
  vm.runInContext("let архивТоваров = " + JSON.stringify(настройки.архив || []) + ", архивСостояние = 'none';\n" + код, ctx);
  // Обработчик «да» живёт своей жизнью (confirmMsg его не возвращает) — ждём
  // и его, иначе читали бы сказанное раньше, чем оно сказано.
  const js = async (к) => { const итог = await vm.runInContext(к, ctx); await Promise.all(ждём); return итог; };
  return { ctx, сказано, тосты, вопросы, js, знач: (к) => vm.runInContext(к, ctx) };
}
const ок = { ok: true, items: [{ id: 5, name: "Старый", city: "Минск", model_id: 9 }] };

(async () => {
  // ---- Загрузка архива: состояние отдельно от данных ----
  {
    const с = стенд({ "/api/admin/archive": ок });
    проверка("загрузился — true, состояние ok, данные свежие",
      (await с.js("загрузитьАрхив()")) === true && с.знач("архивСостояние") === "ok" && с.знач("архивТоваров.length") === 1);
    const сбой = стенд({ "/api/admin/archive": 503 }, { архив: [{ id: 4 }] });
    проверка("503 — false, состояние error, прежние данные не стёрты",
      (await сбой.js("загрузитьАрхив()")) === false && сбой.знач("архивСостояние") === "error" && сбой.знач("архивТоваров.length") === 1);
    const кривой = стенд({ "/api/admin/archive": { ok: false } });
    проверка("ok:false — тоже сбой, а не «архив пуст»", (await кривой.js("загрузитьАрхив()")) === false && кривой.знач("архивСостояние") === "error");
  }

  // ---- Исход неизвестен: что сказано после перечитывания ----
  const случаи = [
    ["всё перечитали", { "/api/admin/archive": ок }, {}, true],
    ["архив перечитали, товары точек — нет", { "/api/admin/archive": ок }, { спискиЗагрузились: false }, false],
    ["товары перечитали, архив — нет", { "/api/admin/archive": 503 }, {}, false],
  ];
  for (const [что, сеть, наст, обновлён] of случаи) {
    const с = стенд(сеть, наст);              // сам запрос «в архив» обрывается — ответа нет
    await с.js("вАрхив(1)");
    const текст = с.сказано.at(-1) || "";
    проверка(`ответ не дошёл, ${что}: ${обновлён ? "«список обновлён»" : "«обновить не вышло»"}`,
      /не знаю, получилось ли/.test(текст) && (обновлён ? /Список обновлён/.test(текст) && !/не вышло/.test(текст)
                                                       : /Обновить список тоже не вышло/.test(текст) && !/Список обновлён/.test(текст)), текст);
  }

  // ---- Обычные исходы ----
  {
    const с = стенд({ "/api/admin/product/archive": { ok: true, changed: true }, "/api/admin/archive": ок });
    await с.js("вАрхив(1)");
    проверка("в архив — спросили, что останется, и «В архиве»",
      /Отзывы, фото и история склада останутся/.test(с.вопросы[0]) && с.тосты.at(-1) === "В архиве");
    const отказ = стенд({ "/api/admin/product/archive": { ok: false, error: "on_stock", message: "На полке ещё 3 шт." } });
    await отказ.js("вАрхив(1)");
    проверка("отказ сервера — его словами", отказ.сказано.at(-1) === "На полке ещё 3 шт.");
    const везде = стенд({ "/api/admin/model/archive": { ok: true, archived: ["Туров"], left: [{ city: "Минск", message: "На полке ещё 2 шт." }] },
                         "/api/admin/archive": ок }, { полка: [{ id: 1, name: "Кабель", city: "Минск", model_id: 7 }, { id: 2, name: "Кабель", city: "Туров", model_id: 7 }] });
    await везде.js("вАрхивВезде(7)");
    проверка("на всех точках — итог «где убран, где остался и почему»",
      /В архиве: Туров/.test(везде.сказано.at(-1)) && /Минск — На полке ещё 2 шт\./.test(везде.сказано.at(-1)), везде.сказано.at(-1));
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
