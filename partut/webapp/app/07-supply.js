// 07-supply.js — приём поставки: несколько товаров и вариантов одной
// точки, одно «Провести».
//
// Раньше завоз шёл «Массовым приходом» прямо в списке товаров: товар со
// вкусами открывал своё окно склада, закупочной цены и итога не было, а
// набранное пропадало вместе с закрытым приложением. Теперь поставка —
// отдельный документ: точка, товары с вариантами, количество и закупка,
// итог и одно «Провести».
//
// Записывает всё тот же склад: каждая строка — своя операция db.stock_operation
// через /api/admin/stock/move/batch, со своим ключом. Черновик вместе с
// ключами живёт в телефоне (localStorage, по человеку и точке): свернул
// приложение, пропала связь — ничего не потеряно, а проведённое второй раз
// не запишется. Пачка не атомарна, и экран это говорит: прошедшие строки
// отмечены, не прошедшие остаются в черновике со своей причиной, и повтор
// отправляет только их.

const ПОСТАВКА_КЛЮЧ = "partut_supply_v1";
const ПОСТАВКА_МАКС = 100000;     // как у сервера: больше за одно движение — похоже на опечатку
const ПОСТАВКА_ПАЧКА = 300;       // сервер принимает до 300 строк за запрос
let пТочка = null;                // точка поставки
let пЧерн = null;                 // черновик, см. новыйЧерновик()
let пИдёт = false;                // «Провести» в пути: второе нажатие ждёт ответа
let пПоиск = "";                  // что ищут среди товаров точки
const пВкусы = {};                // товар → поиск по его вариантам

// Исход неизвестен: сервер упал, ответ оборвался или неполон. Строки с
// ключами остаются как есть — повтор с теми же ключами дважды не запишет.
class ПоставкаНеизвестно extends Error {}

function новыйЧерновик(city) {
  // order — товары по порядку добавления; names — их названия (товар могут
  // убрать с точки, а в черновике он должен остаться узнаваемым); costs —
  // закупка за штуку, как вписана; rows — строки по ключу «товар + вариант»:
  // qty — как вписано, token — ключ операции, status — "" | "posted" |
  // "failed", error — причина отказа, delta — сколько записано.
  return { city, order: [], names: {}, costs: {}, rows: {}, updated: Date.now() };
}
const человек = () => String((tgUser && tgUser.id) || "0");
const ключЧерновика = (city) => `${ПОСТАВКА_КЛЮЧ}.${человек()}.${city}`;
function прочитатьЧерновик(city) {
  try {
    const ч = JSON.parse(localStorage.getItem(ключЧерновика(city)) || "null");
    if (ч && ч.city === city && Array.isArray(ч.order) && ч.rows && ч.costs) return { names: {}, ...ч };
  } catch (e) { /* испорченный черновик — начинаем с чистого листа */ }
  return новыйЧерновик(city);
}
function сохранитьЧерновик(ч = пЧерн) {
  if (!ч) return;
  ч.updated = Date.now();
  try {
    if (!ч.order.length) localStorage.removeItem(ключЧерновика(ч.city));
    else localStorage.setItem(ключЧерновика(ч.city), JSON.stringify(ч));
  } catch (e) { /* приватный режим — черновик проживёт до закрытия приложения */ }
}

// Ключ строки: товар и вариант. JSON, а не склейка через разделитель: в
// названии варианта может оказаться любой символ.
const ключСтроки = (pid, flavor) => JSON.stringify([pid, flavor || ""]);
const пТовар = (pid, точка = пТочка) => shelf().find(p => p.id === pid && p.city === точка) || null;
function вариантыТовара(p) {
  return hasVariants(p)
    ? p.variants.map(v => ({ flavor: v.flavor, stock: +v.stock || 0 }))
    : [{ flavor: "", stock: +p.stock || 0 }];
}

// Пусто — строки нет в поставке (не «списать до нуля»). Иначе целое от 1.
function разобратьШтуки(v) {
  const t = String(v ?? "").trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 && n <= ПОСТАВКА_МАКС ? n : NaN;
}
function разобратьЗакупку(v) {
  const t = String(v ?? "").replace(",", ".").replace(/\s/g, "");
  if (!t) return null;
  const n = Number(t);
  return isFinite(n) && n >= 0 ? Math.round(n * 100) / 100 : NaN;
}
const деньги = (n) => (Math.round(n * 100) / 100).toFixed(2);

// Все строки черновика по товару: нынешние варианты и те, что остались в
// черновике, а у товара их уже нет (вариант убрали, пока поставка лежала).
function строкиВЧерновике(pid, ч = пЧерн) {
  const p = пТовар(pid, ч.city);
  const есть = p ? вариантыТовара(p) : [];
  const знаем = new Set(есть.map(v => v.flavor));
  const лишние = Object.keys(ч.rows)
    .map(k => JSON.parse(k))
    .filter(([id, flavor]) => id === pid && !знаем.has(flavor))
    .map(([, flavor]) => ({ flavor, stock: null, нет: true }));
  return [...есть, ...лишние].map(v => ({ ...v, key: ключСтроки(pid, v.flavor), r: ч.rows[ключСтроки(pid, v.flavor)] || {} }));
}

// Что уйдёт при «Провести»: строки с числом, ещё не проведённые, у живого
// товара и существующего варианта.
function итогПоставки(ч = пЧерн) {
  // нельзя — строки с числом, которые уже не провести: товар убрали с
  // точки или вариант из карточки. Итог обязан их называть: иначе он
  // сказал бы «поставка проведена», а шесть штук так и не легли бы на склад.
  const итог = { позиций: 0, штук: 0, денег: 0, безЗакупки: 0, кривых: 0, проведено: 0, нельзя: 0, строки: [] };
  for (const pid of ч.order) {
    const p = пТовар(pid, ч.city);
    const закупка = разобратьЗакупку(ч.costs[pid]);
    if (Number.isNaN(закупка)) итог.кривых++;
    for (const v of строкиВЧерновике(pid, ч)) {
      if (v.r.status === "posted") { итог.проведено++; continue; }
      const q = разобратьШтуки(v.r.qty);
      if (q === null) continue;
      if (!p || v.нет) { итог.нельзя++; continue; }    // не проведётся — сказано у строки
      if (Number.isNaN(q)) { итог.кривых++; continue; }
      итог.позиций++; итог.штук += q;
      if (закупка > 0) итог.денег += q * закупка; else итог.безЗакупки++;
      итог.строки.push({ key: v.key, pid, flavor: v.flavor, qty: q, cost: закупка });
    }
  }
  return итог;
}

// ---------- Открыть и закрыть ----------
function точкиПоставки() {
  if (myScope()) return [myScope()];
  return locations.map(l => l.name);
}
function openSupply() {
  const точки = точкиПоставки();
  let точка = myScope() || (admLocFilter !== "all" ? admLocFilter : null);
  if (!точка) {
    try { точка = localStorage.getItem(`${ПОСТАВКА_КЛЮЧ}.точка.${человек()}`); } catch (e) {}
  }
  if (!точка || !точки.includes(точка)) {
    точка = точки.find(т => shelf().some(p => p.city === т)) || точки[0] || null;
  }
  // Та же точка — тот же черновик в памяти, а не перечитанный: пока экран
  // был закрыт, мог прийти ответ на «Провести», и он лёг именно сюда.
  if (точка === пТочка && пЧерн) нарисоватьПоставку();
  else выбратьТочкуПоставки(точка);
  $("supplyView").classList.add("show");
}
function выбратьТочкуПоставки(точка) {
  if (пЧерн) сохранитьЧерновик();
  пТочка = точка;
  пЧерн = точка ? прочитатьЧерновик(точка) : null;
  пПоиск = ""; $("dlvFind").value = "";
  try { if (точка && !myScope()) localStorage.setItem(`${ПОСТАВКА_КЛЮЧ}.точка.${человек()}`, точка); } catch (e) {}
  нарисоватьПоставку();
}
$("supplyOpen").onclick = openSupply;
$("supplyClose").onclick = () => {
  сохранитьЧерновик();
  $("supplyView").classList.remove("show");
  обновитьКнопкуПоставки();
  renderAdminList();
};

// Кнопка в «Ценах и остатках» напоминает о непроведённом черновике: поставку
// начали, свернули приложение — и забыли, а остаток в базе так и не вырос.
function обновитьКнопкуПоставки() {
  let строк = 0;
  try {
    const начало = `${ПОСТАВКА_КЛЮЧ}.${человек()}.`;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(начало)) continue;
      const ч = JSON.parse(localStorage.getItem(k) || "null");
      if (!ч || !ч.rows) continue;
      строк += Object.values(ч.rows).filter(r => r && r.status !== "posted" && String(r.qty ?? "").trim()).length;
    }
  } catch (e) { /* нет хранилища — нет и напоминания */ }
  $("supplyOpen").textContent = строк ? `📦 Приём поставки · черновик` : "📦 Приём поставки";
}

// ---------- Рисование ----------
function нарисоватьПоставку() {
  const точки = точкиПоставки();
  $("dlvPoints").innerHTML = myScope() || точки.length < 2 ? "" : точки.map(т =>
    `<button class="ochip ${т === пТочка ? "active" : ""}" data-dpt="${esc(т)}">${esc(т)}</button>`).join("");
  $("dlvPoints").querySelectorAll("[data-dpt]").forEach(b => b.onclick = () => {
    if (пИдёт) return;
    выбратьТочкуПоставки(b.dataset.dpt);
  });
  if (!пТочка) {
    $("dlvScope").textContent = "Точек пока нет — поставку принимать некуда.";
    $("dlvFound").innerHTML = ""; $("dlvDoc").innerHTML = ""; нарисоватьИтог(); return;
  }
  // Где вырастет остаток — написано прямо, как и в окне цены.
  $("dlvScope").textContent = `Поставка на точку «${пТочка}». Остаток вырастет, когда нажмёте «Провести».`;
  нарисоватьНайденное();
  $("dlvDoc").innerHTML = пЧерн.order.map(карточкаПоставки).join("");
  привязатьКарточки();
  нарисоватьИтог();
}

function нарисоватьНайденное() {
  const q = пПоиск.trim().toLowerCase();
  const в = new Set(пЧерн.order);
  // Пустая поставка — сразу список товаров точки: выбирать проще, чем
  // вспоминать название. Когда товары уже есть, список — только по поиску.
  if (пЧерн.order.length && !q) { $("dlvFound").innerHTML = ""; return; }
  const все = shelf().filter(p => p.city === пТочка && !в.has(p.id)
    && нашлось(p, q));
  const видно = все.slice(0, 40);
  $("dlvFound").innerHTML = (видно.length
    ? `<div class="dlvhint">${q ? "Нашлось:" : "Выберите, что привезли, — или найдите поиском:"}</div>`
      + видно.map(p => `<button type="button" class="dlvfound" data-dadd="${p.id}">
          <span class="dlvfname">${esc(p.name)}<small>есть ${+p.stock || 0} шт${hasVariants(p) ? ` · ${esc(вариантовСтрокой(p))}` : ""}${p.hidden ? " · снят с витрины" : ""}</small></span>
          <b aria-hidden="true">＋</b></button>`).join("")
      + (все.length > видно.length ? `<div class="dlvhint">…и ещё ${все.length - видно.length} — уточните поиск.</div>` : "")
    : `<div class="dlvhint">${q ? "Ничего не нашлось на этой точке. Нового товара здесь нет — сначала «Завезти на точку»." : "На этой точке пока нет товаров."}</div>`);
  $("dlvFound").querySelectorAll("[data-dadd]").forEach(b => b.onclick = () => добавитьВПоставку(+b.dataset.dadd));
}

function карточкаПоставки(pid) {
  const p = пТовар(pid);
  const имя = p ? p.name : (пЧерн.names[pid] || "Товар");
  const строки = строкиВЧерновике(pid);
  const проведено = строки.some(v => v.r.status === "posted");
  // Убрать товар из поставки можно, пока по нему ничего не проведено:
  // проведённое — уже движение склада, и из документа ему не исчезать.
  const убрать = проведено || пИдёт ? "" :
    `<button type="button" class="iconbtn" data-dremove="${pid}" aria-label="Убрать «${esc(имя)}» из поставки">✕</button>`;
  if (!p) {
    return `<div class="sect dlvcard"><div class="dlvhead"><div class="dlvname">${esc(имя)}</div>${убрать}</div>
      <div class="dwarn" style="margin-top:6px">Этого товара больше нет на точке «${esc(пТочка)}» — строки по нему не проведутся.</div>
      ${строки.filter(v => v.r.status === "posted").map(строкаПоставки).join("")}</div>`;
  }
  const закупка = пЧерн.costs[pid] ?? "";
  const было = +p.cost > 0 ? деньги(+p.cost) : "";
  const изменена = было && разобратьЗакупку(закупка) !== +было;
  const формы = формыВарианта(p);
  const фильтр = (пВкусы[pid] || "").trim().toLowerCase();
  const показаны = строки.filter(v => !фильтр || v.flavor.toLowerCase().includes(фильтр));
  const открытых = показаны.filter(v => v.r.status !== "posted" && !v.нет).length;
  const поиск = hasVariants(p) && p.variants.length > 8
    ? `<input class="admsearch dlvvfind" data-dvf="${pid}" value="${esc(пВкусы[pid] || "")}" autocomplete="off"
         placeholder="🔍 Найти ${esc(формы[0])}…">` : "";
  // «Всем по N» — только показанным и ещё не проведённым строкам, и это
  // написано на кнопке: скрытые поиском строки она не трогает.
  const всем = hasVariants(p) && открытых > 1 && !пИдёт
    ? `<div class="dlvall"><label for="dlva${pid}">Всем ${открытых} показанным ${esc(формы[3])}</label>
         <input id="dlva${pid}" data-dall="${pid}" inputmode="numeric" enterkeyhint="done" placeholder="шт">
         <button type="button" class="barbtn" data-dallgo="${pid}">Вписать</button></div>` : "";
  return `<div class="sect dlvcard" data-dp="${pid}">
    <div class="dlvhead"><div class="dlvname">${esc(имя)}${p.hidden ? `<small>снят с витрины</small>` : ""}</div>${убрать}</div>
    <div class="dlvcost"><label for="dlvc${pid}">Закупка за штуку, Br</label>
      <input id="dlvc${pid}" data-dc="${pid}" inputmode="decimal" autocomplete="off" value="${esc(закупка)}" placeholder="не указана"${пИдёт || проведено ? " disabled" : ""}>
      <span class="dlvwas">${изменена ? `было ${было}` : ""}</span></div>
    ${поиск}${всем}
    <div class="dlvrows">${показаны.map(строкаПоставки).join("") || `<div class="dlvhint">Ничего не нашлось среди вариантов.</div>`}</div>
  </div>`;
}

function строкаПоставки(v) {
  const r = v.r;
  const название = v.flavor || "Пришло, шт";
  const есть = v.нет ? "" : `<small>есть ${v.stock} шт</small>`;
  if (r.status === "posted") {
    const сколько = r.delta != null ? `+${r.delta}` : "";
    return `<div class="dlvrow done"><div class="dlvvar">${esc(название)}${есть}</div>
      <div class="dlvok">✓ ${сколько}${r.earlier ? `<small>записано раньше</small>` : ""}</div></div>`;
  }
  const q = разобратьШтуки(r.qty);
  const ошибка = v.нет ? "Этого варианта у товара больше нет — строка не проведётся."
    : Number.isNaN(q) ? "Целое число штук, от 1 до 100 000." : (r.error || "");
  return `<div class="dlvrow${ошибка ? " bad" : ""}"><div class="dlvvar">${esc(название)}${есть}</div>
    <input data-dq="${esc(v.key)}" inputmode="numeric" enterkeyhint="next" autocomplete="off" placeholder="+0"
      aria-label="Пришло: ${esc(название)}" value="${esc(r.qty ?? "")}"${пИдёт || v.нет ? " disabled" : ""}>
    ${ошибка ? `<div class="dlverr">${esc(ошибка)}</div>` : ""}</div>`;
}

function привязатьКарточки() {
  const док = $("dlvDoc");
  док.querySelectorAll("[data-dremove]").forEach(b => b.onclick = () => убратьИзПоставки(+b.dataset.dremove));
  док.querySelectorAll("[data-dq]").forEach(inp => {
    inp.oninput = () => {
      const r = пЧерн.rows[inp.dataset.dq] = пЧерн.rows[inp.dataset.dq] || {};
      r.qty = inp.value;
      // Правка строки после отказа — это новая попытка: причина отказа
      // больше не про неё.
      if (r.status === "failed") { r.status = ""; r.error = ""; }
      сохранитьЧерновик();
      const q = разобратьШтуки(inp.value);
      inp.closest(".dlvrow").classList.toggle("bad", Number.isNaN(q));
      нарисоватьИтог();
    };
    // Enter на цифровой клавиатуре — к следующей строке, как в любой форме
    // на телефоне: поставку вбивают подряд, не целясь пальцем в каждое поле.
    inp.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const все = [...док.querySelectorAll("[data-dq]:not([disabled])")];
      const дальше = все[все.indexOf(inp) + 1];
      if (дальше) дальше.focus(); else inp.blur();
    };
  });
  док.querySelectorAll("[data-dc]").forEach(inp => inp.oninput = () => {
    пЧерн.costs[+inp.dataset.dc] = inp.value;
    сохранитьЧерновик();
    нарисоватьИтог();
  });
  док.querySelectorAll("[data-dvf]").forEach(inp => inp.oninput = () => {
    пВкусы[+inp.dataset.dvf] = inp.value;
    перерисоватьКарточку(+inp.dataset.dvf, "[data-dvf]");
  });
  док.querySelectorAll("[data-dallgo]").forEach(b => b.onclick = () => вписатьВсем(+b.dataset.dallgo));
}

// Перерисовать одну карточку, не теряя поле, в котором печатают.
function перерисоватьКарточку(pid, фокус) {
  const старая = $("dlvDoc").querySelector(`[data-dp="${pid}"]`);
  if (!старая) { нарисоватьПоставку(); return; }
  const поле = фокус ? старая.querySelector(фокус) : null;
  const где = поле ? поле.selectionStart : null;
  const t = document.createElement("div");
  t.innerHTML = карточкаПоставки(pid);
  старая.replaceWith(t.firstElementChild);
  привязатьКарточки();
  if (фокус) {
    const новое = $("dlvDoc").querySelector(`[data-dp="${pid}"] ${фокус}`);
    if (новое) { новое.focus(); try { новое.setSelectionRange(где, где); } catch (e) {} }
  }
  нарисоватьИтог();
}

function нарисоватьИтог() {
  const btn = $("dlvPost");
  if (!пЧерн) { $("dlvSum").textContent = ""; btn.disabled = true; return; }
  const и = итогПоставки();
  const поз = `${и.позиций} ${plural(и.позиций, "позиция", "позиции", "позиций")}`;
  const строк = (n) => `${n} ${plural(n, "строка", "строки", "строк")}`;
  const части = [];
  if (и.позиций) {
    части.push(`${поз} · ${и.штук} шт`);
    if (и.денег) части.push(`${деньги(и.денег)} Br закупкой`);
    if (и.безЗакупки) части.push(`без закупки: ${и.безЗакупки}`);
    if (и.проведено) части.push(`уже проведено: ${и.проведено}`);
  } else if (и.проведено) {
    части.push(и.нельзя ? `Проведено: ${строк(и.проведено)}` : `✓ Поставка проведена: ${строк(и.проведено)}`);
  } else {
    части.push(и.нельзя ? "Проводить нечего" : пЧерн.order.length ? "Впишите, сколько пришло" : "Добавьте товары, которые привезли");
  }
  if (и.нельзя) части.push(`не проведётся: ${и.нельзя} — товара или варианта больше нет`);
  if (и.кривых) части.push(`проверьте строки с пометкой: ${и.кривых}`);
  $("dlvSum").textContent = части.join(" · ");
  $("dlvSum").classList.toggle("done", !и.позиций && !!и.проведено && !и.нельзя);
  if (пИдёт) { btn.disabled = true; btn.textContent = "Провожу…"; return; }
  btn.disabled = !и.позиций && !и.проведено && !и.нельзя;
  btn.textContent = и.позиций ? (и.проведено ? `Провести оставшиеся (${и.позиций})` : "Провести поставку")
    : и.проведено || и.нельзя ? "Новая поставка" : "Провести поставку";
}

// ---------- Действия ----------
$("dlvFind").oninput = () => { пПоиск = $("dlvFind").value; нарисоватьНайденное(); };

function добавитьВПоставку(pid) {
  const p = пТовар(pid);
  if (!p || пИдёт) return;
  if (!пЧерн.order.includes(pid)) {
    пЧерн.order.push(pid);
    пЧерн.names[pid] = p.name;
    // Закупка — предложением, из карточки товара: чаще всего она та же.
    if (!(pid in пЧерн.costs)) пЧерн.costs[pid] = +p.cost > 0 ? деньги(+p.cost) : "";
  }
  пПоиск = ""; $("dlvFind").value = "";
  сохранитьЧерновик();
  нарисоватьПоставку();
  const первое = $("dlvDoc").querySelector(`[data-dp="${pid}"] [data-dq]:not([disabled])`);
  if (первое) { try { первое.focus(); первое.scrollIntoView({ block: "center" }); } catch (e) {} }
}

function убратьИзПоставки(pid) {
  if (пИдёт) return;
  пЧерн.order = пЧерн.order.filter(x => x !== pid);
  delete пЧерн.costs[pid]; delete пЧерн.names[pid]; delete пВкусы[pid];
  for (const k of Object.keys(пЧерн.rows)) if (JSON.parse(k)[0] === pid) delete пЧерн.rows[k];
  сохранитьЧерновик();
  нарисоватьПоставку();
}

function вписатьВсем(pid) {
  const карточка = $("dlvDoc").querySelector(`[data-dp="${pid}"]`);
  const поле = карточка && карточка.querySelector("[data-dall]");
  const q = разобратьШтуки(поле ? поле.value : "");
  if (q === null || Number.isNaN(q)) { alertMsg("Впишите, сколько штук пришло каждого, — целое число от 1."); return; }
  const фильтр = (пВкусы[pid] || "").trim().toLowerCase();
  for (const v of строкиВЧерновике(pid)) {
    if (v.нет || v.r.status === "posted" || (фильтр && !v.flavor.toLowerCase().includes(фильтр))) continue;
    пЧерн.rows[v.key] = { ...v.r, qty: String(q), status: "", error: "" };
  }
  сохранитьЧерновик();
  перерисоватьКарточку(pid);
}

$("dlvPost").onclick = () => провестиПоставку(false);

async function провестиПоставку(подтверждено) {
  if (пИдёт || !пЧерн) return;
  const и = итогПоставки();
  if (!и.позиций) {
    if (и.проведено || и.нельзя) {                    // проводить больше нечего — начать новую
      const заново = () => { пЧерн = новыйЧерновик(пТочка); сохранитьЧерновик(); нарисоватьПоставку(); };
      if (и.нельзя && !подтверждено) {
        confirmMsg(`В черновике ${и.нельзя} ${plural(и.нельзя, "строка", "строки", "строк")}, которые уже не провести: `
          + "товара или варианта больше нет. Начать новую поставку? Эти строки удалятся.", () => провестиПоставку(true));
        return;
      }
      заново(); return;
    }
    alertMsg("Впишите, сколько пришло, хотя бы в одну строку."); return;
  }
  if (и.кривых) { alertMsg("Проверьте строки с пометкой: количество — целое число от 1, закупка — число, например 9.90."); return; }
  if (!подтверждено) {
    confirmMsg(`Провести поставку на точку «${пТочка}»?\n\n${и.позиций} ${plural(и.позиций, "позиция", "позиции", "позиций")}, ${и.штук} шт`
      + (и.денег ? `, ${деньги(и.денег)} Br закупкой` : "") + ".\nОстаток вырастет сразу.", () => провестиПоставку(true));
    return;
  }
  // Ответ пишется в ЭТОТ черновик, даже если за время запроса экран закрыли
  // и открыли поставку другой точки.
  const ч = пЧерн;
  // Ключ у каждой строки — и сохраняем его ДО запроса: закроют приложение,
  // пока запрос в пути, — повтор пойдёт с теми же ключами, и сервер узнает
  // уже проведённое.
  const строки = и.строки.map(s => {
    const r = ч.rows[s.key];
    if (!r.token) r.token = новыйКлючОперации();
    const item = { id: s.pid, qty: s.qty, token: r.token };
    if (s.flavor) item.flavor = s.flavor;
    if (s.cost > 0) item.cost = s.cost;
    return { key: s.key, item };
  });
  сохранитьЧерновик(ч);
  пИдёт = true;
  нарисоватьПоставку();
  let неизвестно = false, отказ = "", сбой = "";
  try {
    for (let i = 0; i < строки.length; i += ПОСТАВКА_ПАЧКА) {
      const часть = строки.slice(i, i + ПОСТАВКА_ПАЧКА);
      const r = await fetch("/api/admin/stock/move/batch", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, reason: "in", note: "поставка", items: часть.map(x => x.item) }) });
      if (r.status >= 500) throw new ПоставкаНеизвестно("Сервер ответил ошибкой.");
      let d;
      try { d = await r.json(); } catch (e) { throw new ПоставкаНеизвестно("Ответ сервера оборвался."); }
      if (!d || typeof d !== "object") throw new ПоставкаНеизвестно("Ответ сервера не разобрать.");
      if (d.ok !== true) {
        // Отказ всей пачке (права, пустой список) — не записано ничего.
        if (!d.error && !d.message) throw new ПоставкаНеизвестно("Ответ сервера не разобрать.");
        часть.forEach(x => { delete ч.rows[x.key].token; });
        отказ = d.message || ОТКАЗЫ[d.error] || "Поставка не проведена.";
        break;
      }
      const отвечено = new Set();
      (d.done || []).forEach(x => {
        const s = часть[x.index]; if (!s) return;
        отвечено.add(x.index);
        Object.assign(ч.rows[s.key], { status: "posted", delta: x.delta, error: "" });
      });
      Object.entries(d.failed || {}).forEach(([k, f]) => {
        const s = часть[+k]; if (!s) return;
        отвечено.add(+k);
        const row = ч.rows[s.key];
        if (f.error === "token_reused") {
          // Прошлая попытка по этой строке уже проведена — с другим числом.
          // Второй раз не пишем, а поле очищаем: иначе следующее нажатие
          // записало бы его как новый приход.
          Object.assign(row, { status: "posted", delta: f.recorded ? f.recorded.delta : null, earlier: true, qty: "", error: "" });
          return;
        }
        delete row.token;                              // отказ — точно не записано
        Object.assign(row, { status: "failed", error: f.message || ОТКАЗЫ[f.error] || "Не записано." });
      });
      // Строка, про которую ответ молчит, — исход неизвестен: ключ остаётся.
      if (часть.some((x, j) => !отвечено.has(j))) неизвестно = true;
    }
  } catch (e) {
    неизвестно = true;
    сбой = e instanceof ПоставкаНеизвестно ? e.message : текстСбоя(e);
  } finally {
    пИдёт = false;
    сохранитьЧерновик(ч);
    if (ч === пЧерн) нарисоватьПоставку();
  }
  // Остатки в списке и в строках поставки — свежие, с сервера.
  Promise.resolve(refreshProducts()).catch(() => {}).then(() => {
    if ($("supplyView").classList.contains("show")) нарисоватьПоставку();
  });
  const после = итогПоставки(ч);
  if (неизвестно) {
    alertMsg(`${сбой || "Ответ пришёл не по всем строкам."}\n\nЧисла на месте. Нажмите «Провести» ещё раз — проведённое второй раз не запишется.`);
  } else if (отказ) {
    alertMsg(отказ);
  } else if (после.позиций || после.нельзя) {
    alertMsg(`Проведено ${после.проведено}, не прошло ${после.позиций + после.нельзя} — причина у каждой строки, числа на месте.`);
  } else {
    toast(`Поставка проведена ✅ ${после.проведено} ${plural(после.проведено, "строка", "строки", "строк")}`);
  }
}
