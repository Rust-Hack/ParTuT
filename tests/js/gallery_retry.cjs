// Повтор загрузки фото в галерею описания — настоящий код из 06-catalog.js
// в песочнице Node, сеть с управляемой задержкой (приёмка R16-01).
//
// Ревьюер: открыл описание модели A, начал добавлять фото; пока запрос ждал
// ответа, закрыл описание и открыл модель B; первый запрос вернул временную
// ошибку, «Повторить загрузку» — и повтор ушёл с model_id модели B: снимок A
// попадал в галерею B. Теперь адресат запоминается до первого ожидания.
//
// Запуск: node tests/js/gallery_retry.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const исходник = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "06-catalog.js"), "utf8");
const а = исходник.indexOf("// Сбой загрузки — словами сервера (В-4)"), б = исходник.indexOf("function delEditPhoto(photoId)", а);
if (а < 0 || б < 0) { console.log("❌ не нашёл загрузку фото в галерею в 06-catalog.js"); process.exit(1); }
const код = исходник.slice(а, б);

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}
const тик = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
const НЕТ_СВЯЗИ = { ok: false, error: "tg_unreachable", retry: true, message: "Нет связи с Телеграмом — фото не сохранилось. Попробуйте ещё раз." };

function стенд() {
  const узлы = {}, запросы = [], вопросы = [], сказано = [], галерея = [];
  const узел = (id) => ({ id, innerHTML: "", insertAdjacentHTML(где, html) { this.innerHTML += html; },
    remove() { delete узлы[id]; узлы.mdGal && (узлы.mdGal.innerHTML = узлы.mdGal.innerHTML.replace(new RegExp(`<div class="g" id="${id}">.*?</div>`), "")); } });
  const $ = (id) => {
    if (id === "mdGal") return узлы.mdGal || (узлы.mdGal = узел("mdGal"));
    if (/^gLoading/.test(id)) return (узлы.mdGal && узлы.mdGal.innerHTML.includes(`id="${id}"`)) ? узел(id) : null;
    return null;
  };
  const ctx = vm.createContext({
    $, console, JSON, Promise, Set, Array, Object, String, initData: "qa", MAX_EXTRA_PHOTOS: 8,
    editingModelId: 101, описаниеСеанс: 1, editPhotos: [],
    models: [{ id: 101, name: "Модель A" }, { id: 202, name: "Модель B" }],
    URL: { createObjectURL: (f) => `blob:${f.name}` },
    FormData: class { constructor() { this.поля = {}; } append(k, v) { this.поля[k] = typeof v === "object" ? v : String(v); } },
    fetch: (url, o) => new Promise(ответ => запросы.push({ url, модель: o.body.поля.model_id, файл: o.body.поля.file.name,
      ответить: (d, статус = 200) => ответ({ ok: статус < 400, json: async () => d }) })),
    ОТКАЗЫ: {}, текстСбоя: (e) => "сбой: " + e,
    alertMsg: (m) => сказано.push(m), confirmMsg: (m, да) => вопросы.push({ m, да }),
    renderEditGallery: () => галерея.push(vm.runInContext("JSON.stringify([editingModelId, editPhotos.map(g => g.id)])", ctx)),
    refreshProducts() {},
  });
  vm.runInContext(код, ctx);
  const js = (к) => vm.runInContext(к, ctx);
  return { js, запросы, вопросы, сказано, галерея, узлы,
    загрузить: (...имена) => js(`addEditPhotos(${JSON.stringify(имена.map(name => ({ name })))})`),
    // Окно описания закрыли и открыли в нём другую модель — как открытьОписание/editModel.
    открытьB: () => js(`описаниеСеанс += 2; editingModelId = 202; editPhotos = [];`) };
}

(async () => {
  // ---- Сценарий ревьюера: A в пути → открыли B → временная ошибка → «Повторить» ----
  {
    const с = стенд();
    const идёт = с.загрузить("photo-of-A.png");
    await тик();
    проверка("первый запрос — в модель A (101)", с.запросы[0].модель === "101" && с.запросы[0].файл === "photo-of-A.png");
    с.открытьB();
    с.запросы[0].ответить(НЕТ_СВЯЗИ, 503);
    await идёт; await тик();
    проверка("вопрос о повторе называет исходный товар: «Модель A»: Нет связи… Повторить загрузку (1 фото)?",
      с.вопросы.length === 1 && /^«Модель A»: Нет связи с Телеграмом/.test(с.вопросы[0].m) && /Повторить загрузку \(1 фото\)\?$/.test(с.вопросы[0].m), с.вопросы);
    проверка("…экран B не тронут: галерея B не перерисовывалась", с.галерея.length === 0, с.галерея);
    с.вопросы[0].да(); await тик();
    проверка("R16-01: повтор ушёл в модель A (101), а не в открытую B (202)",
      с.запросы.length === 2 && с.запросы[1].модель === "101" && с.запросы[1].файл === "photo-of-A.png", с.запросы.map(з => [з.модель, з.файл]));
    с.запросы[1].ответить({ ok: true, photo_id: 7 });
    await тик();
    проверка("…удачный повтор не добавил фото A в галерею B", с.js("editPhotos.length") === 0 && с.галерея.length === 0, [с.js("JSON.stringify(editPhotos)"), с.галерея]);
    проверка("…и молча: ни ошибок, ни вопросов сверх первого", с.сказано.length === 0 && с.вопросы.length === 1, с.сказано);
  }

  // ---- То же окно: повтор — в ту же модель, галерея обновляется, без имени товара ----
  {
    const с = стенд();
    const идёт = с.загрузить("a.png");
    await тик();
    проверка("пока грузится — превью в галерее A", /id="gLoading\d+"/.test(с.узлы.mdGal.innerHTML));
    с.запросы[0].ответить(НЕТ_СВЯЗИ, 503);
    await идёт; await тик();
    проверка("окно то же — вопрос без имени товара", /^Нет связи с Телеграмом/.test(с.вопросы[0].m), с.вопросы[0].m);
    проверка("…превью «грузится» убрано", !/gLoading/.test(с.узлы.mdGal.innerHTML));
    с.вопросы[0].да(); await тик();
    с.запросы[1].ответить({ ok: true, photo_id: 9 });
    await тик();
    проверка("повтор в ту же модель, фото в галерее, галерея перерисована",
      с.запросы[1].модель === "101" && с.js("editPhotos.map(g => g.id).join()") === "9" && с.галерея.at(-1) === "[101,[9]]", с.галерея);
  }

  // ---- Несколько файлов: окно сменилось между ними — оба всё равно в A ----
  {
    const с = стенд();
    const идёт = с.загрузить("a1.png", "a2.png");
    await тик();
    с.открытьB();
    с.запросы[0].ответить({ ok: true, photo_id: 11 });
    await тик();
    проверка("второй файл пачки — тоже в A, хотя открыта B", с.запросы[1] && с.запросы[1].модель === "101" && с.запросы[1].файл === "a2.png",
      с.запросы.map(з => [з.модель, з.файл]));
    с.запросы[1].ответить({ ok: true, photo_id: 12 });
    await идёт; await тик();
    проверка("…ни одно фото A не попало в галерею B", с.js("editPhotos.length") === 0 && с.галерея.length === 0);
  }

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
