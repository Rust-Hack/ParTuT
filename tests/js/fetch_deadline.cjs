// Срок запроса — на весь обмен, до конца чтения тела ответа (QP-04).
//
// Приёмка 8ae2ea9 (docs/swipe-price-acceptance-8ae2ea9): общий таймер
// снимался, как только приходили заголовки, и r.json() над застрявшим телом
// ждал вечно — «Сохраняю…» до перезагрузки, а с QP-03 и следующее сохранение
// того же товара ждало первое бесконечно.
//
// Здесь — настоящий локальный HTTP-сервер (только 127.0.0.1): он отдаёт
// заголовки и начало JSON и замолкает. Через него идут настоящая обёртка
// запросов из 01-core.js и настоящая быстрая цена из 06-catalog.js; срок
// сокращён с 20 с до долей секунды.
//
// 1 октября 2026 тест упал в CI (sqlite · UTC) ещё до первой проверки: срок
// был 150 мс, а самый первый fetch в процессе Node заодно загружает сетевую
// библиотеку и на этом Mac идёт в 15 раз дольше второго. На загруженной
// машине CI он не уложился в срок и оборвался. Поэтому fetch прогревается до
// измерений, срок — 400 мс, а «запрос ушёл» ждётся, а не угадывается паузой.
//
// Запуск: node tests/js/fetch_deadline.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const vm = require("node:vm");

const app = path.join(__dirname, "..", "..", "partut", "webapp", "app");
const ядро = fs.readFileSync(path.join(app, "01-core.js"), "utf8");
const каталог = fs.readFileSync(path.join(app, "06-catalog.js"), "utf8");
const СРОК = 400;
const обёртка = ядро.slice(ядро.indexOf("const СРОК_ЗАПРОСА"), ядро.indexOf("// «Сеть недоступна» и «сервер молчит»"))
  .replace("const СРОК_ЗАПРОСА = 20000;", `const СРОК_ЗАПРОСА = ${СРОК};`)
  .replace("const СРОК_ЗАГРУЗКИ = 60000;", `const СРОК_ЗАГРУЗКИ = ${СРОК * 2};`);
const сбой = ядро.slice(ядро.indexOf("function текстСбоя(e)"), ядро.indexOf("const tg ="));
const цена = каталог.slice(каталог.indexOf("// ----- Быстрая цена -----"), каталог.indexOf("// ----- /Быстрая цена -----"));
if (!обёртка.includes(`СРОК_ЗАПРОСА = ${СРОК}`) || !цена.includes("сохранитьЦену")) {
  console.log("❌ не нашёл обёртку запросов или быструю цену"); process.exit(1);
}

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то повисло"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const пауза = (мс) => new Promise((r) => setTimeout(r, мс));
// Сторож: с прежней обёрткой чтение застрявшего тела ждёт вечно — тест
// должен упасть, а не повиснуть (и не держать CI).
setTimeout(() => {
  console.log("❌ зависло: чтение ответа не закончилось за 10 с — срок не доходит до тела");
  process.exit(1);
}, 10000).unref();

(async () => {
  // Сервер: /stall — заголовки и начало тела, дальше тишина; /ok — ответ
  // целиком; /noheaders — молчит ещё до заголовков.
  let запросов = 0;
  const сервер = http.createServer((req, res) => {
    запросов++;
    req.resume();
    if (req.url.startsWith("/ok")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"ok":true,"saved":["price"],"price_rev":1}');
    } else if (req.url.startsWith("/stall") || req.url.startsWith("/api/")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.write('{"ok":true,"saved":[');
    }
    // /noheaders — ничего не отвечаем
  });
  await new Promise((r) => сервер.listen(0, "127.0.0.1", r));
  const адрес = `http://127.0.0.1:${сервер.address().port}`;
  const сигналы = [];
  const window = { fetch: (url, opts) => { сигналы.push(opts.signal); return fetch(new URL(url, адрес), opts); } };
  const ctx = vm.createContext({ window, AbortController, FormData, setTimeout, clearTimeout, console });
  vm.runInContext(обёртка + "\n" + сбой, ctx);

  // Прогрев: первый fetch в процессе медленный сам по себе (см. шапку). Мимо
  // обёртки и без срока — он ничего не проверяет.
  await (await fetch(адрес + "/ok", { method: "POST", body: "{}" })).json();

  try {
    // ---------- Сама обёртка ----------
    {
      const r = await ctx.window.fetch("/ok", { method: "POST", body: "{}" });
      const d = await r.json();
      await пауза(СРОК * 2);
      проверка("целый ответ читается, срок после чтения снят — запрос не обрывается",
        d.ok === true && сигналы.at(-1).aborted === false, { d, оборван: сигналы.at(-1).aborted });
    }
    {
      const r = await ctx.window.fetch("/stall", { method: "POST", body: "{}" });
      const начало = Date.now();
      let ошибка = null;
      try { await r.json(); } catch (e) { ошибка = e; }
      const прошло = Date.now() - начало;
      проверка("застрявшее тело: чтение падает, а не ждёт вечно", !!ошибка, ошибка && String(ошибка));
      проверка("падает по сроку — AbortError, через срок, а не мгновенно",
        ошибка && ошибка.name === "AbortError" && прошло >= СРОК * 0.5 && прошло < СРОК * 10, { имя: ошибка && ошибка.name, прошло });
      проверка("и человеку это «сервер не ответил вовремя»", /вовремя/.test(ctx.текстСбоя(ошибка)), ctx.текстСбоя(ошибка));
    }
    {
      let ошибка = null;
      try { await ctx.window.fetch("/noheaders", { method: "POST", body: "{}" }); } catch (e) { ошибка = e; }
      проверка("сервер молчит до заголовков — тоже по сроку, как и раньше", ошибка && ошибка.name === "AbortError", ошибка && String(ошибка));
    }

    // ---------- Быстрая цена поверх застрявшего тела ----------
    {
      const узлы = new Map();
      const $ = (id) => {
        if (!узлы.has(id)) {
          const классы = new Set();
          узлы.set(id, { value: "", textContent: "", innerHTML: "", style: {}, disabled: false, focus() {}, select() {},
            classList: { add: (k) => классы.add(k), remove: (k) => классы.delete(k), contains: (k) => классы.has(k) } });
        }
        return узлы.get(id);
      };
      const товар = { id: 1, name: "QA", city: "QA", price: 25, price_rev: 0 };
      const тосты = [];
      const ц = vm.createContext({
        $, fetch: ctx.window.fetch, shelf: () => [товар], adminProducts: [товар], allProducts: [], initData: "qa",
        ОТКАЗЫ: {}, текстСбоя: ctx.текстСбоя, renderAdminList() {}, refreshProducts() {}, toast: (m) => тосты.push(m),
        closeOverlay: (el) => el.classList.remove("show"), setTimeout, clearTimeout,
      });
      vm.runInContext(цена, ц);
      const было = запросов;
      vm.runInContext("открытьЦену(1)", ц);
      $("priceNew").value = "30";
      await vm.runInContext("сохранитьЦену()", ц);                 // тело застрянет, срок его оборвёт
      проверка("быстрая цена: сохранение закончилось, кнопка снова доступна",
        $("priceSave").disabled === false && $("priceSave").textContent === "Сохранить цену", $("priceSave").textContent);
      проверка("быстрая цена: сказано «не ответил вовремя, цена могла сохраниться» — не «не сохранилась»",
        /вовремя/.test($("priceMsg").textContent) && /могла сохраниться/.test($("priceMsg").textContent)
        && !/не сохранилась/.test($("priceMsg").textContent), $("priceMsg").textContent);
      проверка("быстрая цена: введённое на месте, цена у себя прежняя", $("priceNew").value === "30" && товар.price === 25);
      проверка("быстрая цена: товар помечен — исход неизвестен", vm.runInContext("ценаСомнительна(1)", ц) === true);
      $("priceCancel").onclick();
      vm.runInContext("открытьЦену(1)", ц);
      const второе = vm.runInContext("сохранитьЦену()", ц);
      // Ждём, пока запрос дойдёт до сервера, но не дольше половины срока: дальше
      // его оборвёт срок, и состояние кнопки уже ничего не скажет.
      for (const конец = Date.now() + СРОК / 2; запросов < было + 2 && Date.now() < конец;) await пауза(5);
      проверка("переоткрыли и сохранили — запрос ушёл, а не «Жду прошлое сохранение» навсегда",
        запросов === было + 2 && !/Жду/.test($("priceSave").textContent), { запросов: запросов - было, кнопка: $("priceSave").textContent });
      await второе;
    }
  } finally {
    сервер.closeAllConnections();
    await new Promise((r) => сервер.close(r));
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
// Стек — в одну строку: из вывода в общий прогон попадают только строки с ❌,
// и без места падения по журналу CI его не найти.
})().catch((e) => { console.log("❌ упало: " + String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); process.exit(1); });
