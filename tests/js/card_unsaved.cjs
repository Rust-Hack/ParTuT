// Карточка товара: несохранённое видно и называется словами (В-2, проход 6.10).
// Настоящий код из 06-catalog.js (проверка несохранённого, кнопка «Сохранить»,
// выход, список вариантов, «＋ вариант») и 01-core.js (разбор списка) в
// песочнице Node.
//
// Вариант, добавленный «＋», лежал только в форме с пометкой «новый», а
// «Сохранить» стояла в самом низу длинной карточки — уходили, не сохранив, и
// при выходе слышали общее «Есть несохранённые изменения». Теперь у варианта —
// «не сохранено», кнопка закреплена внизу экрана и говорит, сколько правок
// ждёт, а выход перечисляет, что пропадёт.
//
// В-3: красный «✕» стоял у каждого заведённого варианта, вплотную к обычным
// строкам. Теперь он появляется только в режиме «Убрать вкусы…»; отмена своего
// же действия («↩︎», «✕ не добавлять») видна всегда. В-5: цена в сообщении
// при выходе — с копейками, как в поле и во всём приложении.
//
// Запуск: node tests/js/card_unsaved.cjs — печатает ✅/❌, код выхода 1 при ❌.
// Из pytest его зовёт tests/test_js_handlers.py.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const корень = path.join(__dirname, "..", "..", "partut", "webapp");
const каталог = fs.readFileSync(path.join(корень, "app", "06-catalog.js"), "utf8");
const ядро = fs.readFileSync(path.join(корень, "app", "01-core.js"), "utf8");
const стили = fs.readFileSync(path.join(корень, "styles.css"), "utf8");
function кусок(текст, от, до) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) { console.log(`❌ не нашёл: ${от}`); process.exit(1); }
  return текст.slice(а, б);
}
const код = [
  кусок(ядро, 'const _ДЕС = "\\u0001";', "\n\n"),                                         // разобратьСписок, ключВарианта
  кусок(каталог, "let editId = null, editVariants", "function openEdit(id)"),                // несохранённое и выход
  кусок(каталог, "function bindVariantAdd(category)", "// Заводит товар на отмеченные точки."),  // «＋» и список вариантов
].join("\n");

let провалов = 0, дошлиДоКонца = false;
process.on("exit", () => {
  if (!дошлиДоКонца) { console.log("❌ проверки не дошли до конца — что-то упало"); process.exitCode = 1; }
});
function проверка(что, ок, подробно) {
  if (!ок) провалов++;
  console.log((ок ? "✅ " : "❌ ") + что + (ок || подробно === undefined ? "" : " — " + JSON.stringify(подробно)));
}

function стенд() {
  const узлы = {}, вопросы = [], сказано = [];
  const узел = (id) => {
    const н = { id, value: "", textContent: "", innerHTML: "", disabled: false, checked: false, onclick: null, слушатели: {},
      _к: new Set(),
      classList: { add: (x) => н._к.add(x), remove: (x) => н._к.delete(x), toggle: (x, да) => (да ? н._к.add(x) : н._к.delete(x)), contains: (x) => н._к.has(x) },
      addEventListener: (событие, f) => (н.слушатели[событие] = н.слушатели[событие] || []).push(f),
      querySelectorAll(sel) {
        const имя = { "[data-vrm]": "vrm", "[data-vadd]": "vadd", "[data-vrmode]": "vrmode" }[sel];
        if (!имя) return [];
        return (н[`_${имя}`] = [...н.innerHTML.matchAll(new RegExp(`data-${имя}="([^"]*)"`, "g"))]
          .map(m => ({ dataset: { [имя]: m[1] }, onclick: null })));
      } };
    return н;
  };
  // Как настоящий getElementById: в карточке товара с моделью нет полей
  // названия, категории, точки, описания — для них null.
  const ЕСТЬ = new Set(["edPrice", "edCost", "edHit", "edSave", "editBody", "editView", "editClose",
                        "edVarList", "edNewFlavor", "edNewQty", "edAddFlavor"]);
  const $ = (id) => !ЕСТЬ.has(id) ? null : (узлы[id] || (узлы[id] = узел(id)));
  const полка = [{ id: 5, name: "Жижа", city: "Минск", price: 25, category: "liquid" }];
  const ctx = vm.createContext({
    $, console, JSON, String, Number, Set, Array, Object,
    shelf: () => полка, pickerValue: () => "", снятьЧерновикТочек: () => [], editPointList: () => [],
    catVariant: () => "Вкус", catVariant2: () => "", catVariantMany: () => "Вкусы", catTwoAxis: () => false,
    esc: (s) => String(s ?? ""), alertMsg: (m) => сказано.push(m), confirmMsg: (m) => вопросы.push(m),
    window: {},
  });
  vm.runInContext(код, ctx);
  const js = (к) => vm.runInContext(к, ctx);
  // Карточка открыта: цена 25, вариант «Мята» на 3 шт.
  js(`editId = 5; editCategory = "liquid"; editOrigPrice = 25; editOrigCost = ""; editOrigHit = false;
      editVariants = [{ flavor: "Мята", stock: 3, reserved: 0 }]; editAdds = []; editRemoves = new Set();`);
  $("edPrice").value = "25";
  $("edSave").textContent = "Сохранить";
  js(`bindVariantAdd("liquid"); renderEditVariants()`);
  const событие = (что) => ($("editBody").слушатели[что] || []).forEach(f => f({ target: {} }));
  return { $, js, вопросы, сказано, событие, кнопка: () => $("edSave") };
}

(() => {
  const с = стенд();
  проверка("ничего не меняли — «Сохранить», без отметки", (с.событие("input"), с.кнопка().textContent === "Сохранить" && !с.кнопка().classList.contains("dirty")));
  // «＋»: Манго с приходом 6 шт.
  с.$("edNewFlavor").value = "Манго"; с.$("edNewQty").value = "6";
  с.$("edAddFlavor").onclick(); с.событие("click");
  проверка("В-2: новый вариант помечен «не сохранено · новый · приход 6 шт»",
    /Манго\s*<small><b class="unsaved">не сохранено<\/b> · новый · приход 6 шт/.test(с.$("edVarList").innerHTML), с.$("edVarList").innerHTML);
  проверка("…кнопка «Сохранить изменения (1)» и выделена", с.кнопка().textContent === "Сохранить изменения (1)" && с.кнопка().classList.contains("dirty"));
  с.$("edPrice").value = "27"; с.событие("input");
  проверка("правка цены — «Сохранить изменения (2)»", с.кнопка().textContent === "Сохранить изменения (2)");
  // В-3: у заведённого варианта «✕» нет, пока не включён режим «Убрать вкусы…».
  const список = () => с.$("edVarList").innerHTML;
  проверка("В-3: у заведённого «Мята» «✕» нет — под списком «Убрать вкусы…»",
    !/data-vrm="Мята"/.test(список()) && /data-vrmode="1">Убрать вкусы…</.test(список()), список());
  проверка("…а у только что добавленного «Манго» «✕ не добавлять» есть", /data-vadd="0" title="Не добавлять">✕</.test(список()));
  с.$("edVarList")._vrmode[0].onclick();
  проверка("режим «Убрать вкусы…»: у «Мята» красный «✕», кнопка режима — «Готово»",
    /class="iconbtn danger" data-vrm="Мята" title="Убрать вариант"/.test(список()) && /data-vrmode="1">Готово</.test(список()), список());
  // Убрать «Мята» — тоже несохранённое.
  с.$("edVarList")._vrm[0].onclick(); с.событие("click");
  проверка("убираемый вариант помечен «не сохранено · будет убран — остаток 3 шт спишется»",
    /Мята\s*<small><b class="unsaved">не сохранено<\/b> · будет убран — остаток 3 шт спишется/.test(с.$("edVarList").innerHTML), с.$("edVarList").innerHTML);
  проверка("…«Сохранить изменения (3)»", с.кнопка().textContent === "Сохранить изменения (3)");
  с.$("edVarList")._vrmode[0].onclick();                  // «Готово» — из режима вышли
  проверка("вышли из режима — у помеченной «Мята» осталась «↩︎ Не убирать», «Убрать вкусы…» снова внизу",
    /data-vrm="Мята" title="Не убирать"[^>]*>↩︎</.test(список()) && /data-vrmode="1">Убрать вкусы…</.test(список()), список());
  // Выход — перечислено, что пропадёт.
  let закрыто = false;
  с.js("закрытьРедактор")(() => { закрыто = true; });
  проверка("выход перечисляет: новый вариант «Манго» (приход 6 шт); цена 25.00 → 27; убрать «Мята»",
    с.вопросы.length === 1 && с.вопросы[0].includes("новый вариант «Манго» (приход 6 шт)") && с.вопросы[0].includes("цена 25.00 → 27")
      && с.вопросы[0].includes("убрать «Мята»") && /Закрыть без сохранения\? Это пропадёт\./.test(с.вопросы[0]) && !закрыто, с.вопросы);
  // Отменили всё — снова «Сохранить», выход без вопроса.
  с.$("edVarList")._vrm[0].onclick();                     // «↩︎ не убирать»
  с.$("edVarList")._vadd[0].onclick();                    // «✕ не добавлять»
  с.$("edPrice").value = "25"; с.событие("input");
  проверка("всё вернули — «Сохранить», без отметки", с.кнопка().textContent === "Сохранить" && !с.кнопка().classList.contains("dirty"));
  с.js("закрытьРедактор")(() => { закрыто = true; });
  проверка("…и выход без вопроса", закрыто && с.вопросы.length === 1);
  // Пока сохраняется — кнопку не перерисовываем («Сохраняю…» остаётся).
  с.кнопка().disabled = true; с.кнопка().textContent = "Сохраняю…";
  с.$("edPrice").value = "30"; с.событие("input");
  проверка("во время сохранения подпись «Сохраняю…» не перебивается", с.кнопка().textContent === "Сохраняю…");
  // Кнопка закреплена внизу экрана — правило в стилях.
  проверка("стили: #edSave закреплена внизу (position: sticky; bottom)", /#edSave\s*\{[^}]*position:\s*sticky;[^}]*bottom:/.test(стили));

  дошлиДоКонца = true;
  console.log(провалов ? `\nНе прошло: ${провалов}` : "\nВсё прошло");
  process.exit(провалов ? 1 : 0);
})();
