// ----- Продажа на точке -----
// Человек купил у прилавка, мимо приложения. Раньше продавцу оставалось
// «списать» с неподходящей причиной: остаток сходился, а денег в статистике
// не было. Решение владельца (2.10.2026): это продажа — заказ без покупателя,
// сразу «выдан» (db.orders.record_point_sale). Выручка и прибыль её видят,
// кэшбэк, розыгрыш и «давно не заказывали» — нет.
//
// Экран — по образцу «Приёма поставки»: точка, поиск, карточки товаров со
// строками вариантов, итог, «Провести». Чек — черновиком в телефоне
// (partut_sale_v1.<человек>.<точка>) вместе с ключом попытки: ключ
// записывается ДО запроса, поэтому повтор после потерянного ответа не
// продаёт дважды, а повтор с исправленным чеком сервер отклонит.
const ПРОДАЖА_КЛЮЧ = "partut_sale_v1";
let сТочка = null;        // точка продажи
let сЧек = null;          // { order: [pid], rows: {ключ: {pid, flavor, qty}}, prices: {pid: "25"}, payment, token }
let сИдёт = false;        // «Провести» в пути
let сПоиск = "";
let сСегодня = null;      // продажи за сегодня; null — не загрузились

const продажаКлюч = (city) => `${ПРОДАЖА_КЛЮЧ}.${человек()}.${city}`;
const строкаКлюч = (pid, flavor) => JSON.stringify([pid, flavor || ""]);
function новыйЧек() { return { order: [], rows: {}, prices: {}, payment: "cash", token: null }; }
function прочитатьЧек(city) {
  try {
    const ч = JSON.parse(localStorage.getItem(продажаКлюч(city)) || "null");
    if (ч && Array.isArray(ч.order) && ч.rows && ч.prices) return ч;
  } catch (e) { /* испорченный черновик — начинаем чистый */ }
  return новыйЧек();
}
function сохранитьЧек() {
  if (!сТочка || !сЧек) return;
  try { localStorage.setItem(продажаКлюч(сТочка), JSON.stringify(сЧек)); } catch (e) { /* без хранилища — без черновика */ }
}
const сТовар = (pid) => shelf().find(p => p.id === pid && p.city === сТочка) || null;
// Штуки в строке: пусто — 0; «2» — 2; иначе NaN (подсветим).
function штукВСтроке(v) {
  const s = String(v ?? "").trim();
  if (!s) return 0;
  return /^\d+$/.test(s) && +s <= 1000 ? +s : NaN;
}
function ценаВЧеке(pid) {
  const s = String(сЧек.prices[pid] ?? "").trim().replace(",", ".");
  return s !== "" && /^\d+(\.\d{1,2})?$/.test(s) ? +s : NaN;
}

// Строки чека, которые уйдут на сервер, и итог.
function строкиЧека() {
  const out = [];
  let ошибок = 0;
  for (const pid of сЧек.order) {
    const p = сТовар(pid);
    const цена = ценаВЧеке(pid);
    for (const [ключ, r] of Object.entries(сЧек.rows)) {
      if (r.pid !== pid) continue;
      const q = штукВСтроке(r.qty);
      if (Number.isNaN(q)) { ошибок++; continue; }
      if (!q) continue;
      if (!p || Number.isNaN(цена)) { ошибок++; continue; }
      // Больше, чем на полке, — тоже ошибка строки: сервер такую продажу
      // всё равно отклонит, и предлагать «Провести» незачем. Чаще всего это
      // непроведённый приход — его и стоит проверить.
      const вар = hasVariants(p) ? p.variants.find(v => v.flavor === r.flavor) : null;
      if (q > (+(вар ? вар.stock : p.stock) || 0)) { ошибок++; continue; }
      out.push({ key: ключ, id: pid, flavor: r.flavor || null, qty: q, price: цена });
    }
  }
  const штук = out.reduce((s, x) => s + x.qty, 0);
  const сумма = Math.round(out.reduce((s, x) => s + x.qty * x.price, 0) * 100) / 100;
  return { строки: out, штук, сумма, ошибок };
}

function точкиПродажи() { return myScope() ? [myScope()] : locations.map(l => l.name); }
function openSale() {
  const точки = точкиПродажи();
  let точка = myScope() || (admLocFilter !== "all" ? admLocFilter : null) || сТочка;
  if (!точка || !точки.includes(точка)) точка = точки.find(т => shelf().some(p => p.city === т)) || точки[0] || null;
  выбратьТочкуПродажи(точка);
  $("saleView").classList.add("show");
}
function выбратьТочкуПродажи(точка) {
  if (сИдёт) return;
  сохранитьЧек();
  сТочка = точка;
  сЧек = точка ? прочитатьЧек(точка) : null;
  сПоиск = ""; $("saleFind").value = "";
  сСегодня = null;
  нарисоватьПродажу();
  загрузитьСегодня();
}
// Плитки «Продажа на точке» в «Товарах» больше нет — вкладка «🧾 Продажа» внизу.
$("saleClose").onclick = () => { сохранитьЧек(); $("saleView").classList.remove("show"); renderAdminList(); if (activeTab === "work") loadToday(); };
$("saleFind").oninput = () => { сПоиск = $("saleFind").value; нарисоватьНайденноеПродажи(); };

// ---------- Рисование ----------
function нарисоватьПродажу() {
  const точки = точкиПродажи();
  $("salePoints").innerHTML = myScope() || точки.length < 2 ? "" : точки.map(т =>
    `<button class="ochip ${т === сТочка ? "active" : ""}" data-spt="${esc(т)}">${esc(т)}</button>`).join("");
  $("salePoints").querySelectorAll("[data-spt]").forEach(b => b.onclick = () => выбратьТочкуПродажи(b.dataset.spt));
  if (!сТочка) {
    $("saleScope").textContent = "Точек пока нет — продавать негде.";
    $("saleFound").innerHTML = ""; $("saleDoc").innerHTML = ""; нарисоватьИтогПродажи(); return;
  }
  $("saleScope").textContent = `Продажа на точке «${сТочка}». Остаток уменьшится, а выручка попадёт в статистику, когда нажмёте «Провести».`;
  нарисоватьСвежестьПродажи();
  нарисоватьНайденноеПродажи();
  $("saleDoc").innerHTML = сЧек.order.map(карточкаПродажи).join("");
  привязатьЧек();
  нарисоватьИтогПродажи();
  нарисоватьСегодня();
}

// Свежи ли остатки в чеке (приёмка UX-48-01). Пока перечитываются — цифры
// «есть N шт» приглушены; не перечитались — так и сказано, с «Повторить»:
// прежние цифры могли устареть, а выглядели бы подтверждёнными.
function нарисоватьСвежестьПродажи() {
  $("saleView").classList.toggle("stockupd", админГрузится > 0);
  const место = $("saleStale");
  if (админГрузится > 0 || админСписокСвеж) { место.hidden = true; место.innerHTML = ""; return; }
  место.hidden = false;
  место.innerHTML = `<div class="dwarn staleall">⚠️ Остатки не обновились — показаны прежние, могли устареть.
    <button type="button" class="barbtn" id="saleRetry">↻ Повторить</button></div>`;
  $("saleRetry").onclick = async () => {
    const загрузка = fetchAdminProducts();
    нарисоватьСвежестьПродажи();                     // «Повторить» пропадает, цифры приглушены
    await загрузка;
    if ($("saleView").classList.contains("show")) остаткиПродажиПришли();
  };
}
// Свежий список пришёл, пока чек открыт: перерисовать, не сбив ввод — поле с
// курсором остаётся тем же, и курсор на месте.
function остаткиПродажиПришли() {
  const поле = document.activeElement;
  const вЧеке = поле && $("saleDoc").contains(поле) ? поле : null;
  const ключ = !вЧеке ? null : вЧеке.dataset.sq !== undefined ? ["sq", вЧеке.dataset.sq]
    : вЧеке.dataset.sprice !== undefined ? ["sprice", вЧеке.dataset.sprice] : null;
  const где = вЧеке ? вЧеке.selectionStart : null;
  нарисоватьПродажу();
  if (!ключ) return;
  const новое = [...$("saleDoc").querySelectorAll(`[data-${ключ[0]}]`)].find(i => i.dataset[ключ[0]] === ключ[1]);
  if (новое) { новое.focus(); try { новое.setSelectionRange(где, где); } catch (e) { /* поле без курсора */ } }
}

function нарисоватьНайденноеПродажи() {
  if (!сЧек) return;
  const q = сПоиск.trim().toLowerCase();
  const в = new Set(сЧек.order);
  if (сЧек.order.length && !q) { $("saleFound").innerHTML = ""; return; }
  const все = shelf().filter(p => p.city === сТочка && !в.has(p.id) && +p.stock > 0
    && нашлось(p, q));
  const видно = все.slice(0, 40);
  $("saleFound").innerHTML = видно.length
    ? `<div class="dlvhint">${q ? "Нашлось:" : "Что продали? Выберите — или найдите поиском:"}</div>`
      + видно.map(p => `<button type="button" class="dlvfound" data-sadd="${p.id}">
          <span class="dlvfname">${esc(p.name)}<small>есть ${+p.stock || 0} шт · ${деньги(+p.price)} Br</small></span>
          <b aria-hidden="true">＋</b></button>`).join("")
      + (все.length > видно.length ? `<div class="dlvhint">…и ещё ${все.length - видно.length} — уточните поиск.</div>` : "")
    : `<div class="dlvhint">${q ? "Ничего не нашлось среди товаров в наличии на этой точке." : "На этой точке нет товаров в наличии."}</div>`;
  $("saleFound").querySelectorAll("[data-sadd]").forEach(b => b.onclick = () => добавитьВЧек(+b.dataset.sadd));
}

function карточкаПродажи(pid) {
  const p = сТовар(pid);
  const убрать = сИдёт ? "" : `<button type="button" class="iconbtn" data-sremove="${pid}" aria-label="Убрать из чека">✕</button>`;
  if (!p) {
    return `<div class="sect dlvcard"><div class="dlvhead"><div class="dlvname">Товар</div>${убрать}</div>
      <div class="dwarn" style="margin-top:6px">Этого товара больше нет на точке «${esc(сТочка)}» — уберите его из чека.</div></div>`;
  }
  const варианты = hasVariants(p) ? p.variants : [{ flavor: null, stock: +p.stock || 0 }];
  const цена = ценаВЧеке(pid);
  const строки = варианты.map(v => {
    const ключ = строкаКлюч(pid, v.flavor);
    const r = сЧек.rows[ключ] || {};
    const ошибка = ошибкаСтроки(r.qty, +v.stock || 0);
    return `<div class="dlvrow${ошибка ? " bad" : ""}"><div class="dlvvar">${esc(v.flavor || "Продано, шт")}<small>есть ${+v.stock || 0} шт</small></div>
      <input data-sq="${esc(ключ)}" data-spid="${pid}" data-sfl="${esc(v.flavor || "")}" inputmode="numeric" enterkeyhint="next"
        autocomplete="off" placeholder="0" aria-label="Продано: ${esc(v.flavor || p.name)}" value="${esc(r.qty ?? "")}"${сИдёт ? " disabled" : ""}>
      ${ошибка ? `<div class="dlverr">${esc(ошибка)}</div>` : ""}</div>`;
  }).join("");
  return `<div class="sect dlvcard" data-sp="${pid}">
    <div class="dlvhead"><div class="dlvname">${esc(p.name)}</div>${убрать}</div>
    <div class="dlvcost"><label for="salp${pid}">Цена за штуку, Br</label>
      <input id="salp${pid}" data-sprice="${pid}" inputmode="decimal" autocomplete="off" value="${esc(сЧек.prices[pid] ?? "")}"${сИдёт ? " disabled" : ""}>
      <span class="dlvwas">${+p.price !== цена && !Number.isNaN(цена) ? `в карточке ${деньги(+p.price)}` : ""}</span></div>
    ${Number.isNaN(цена) ? `<div class="dlverr">Цена — число, например 25 или 24.50.</div>` : ""}
    <div class="dlvrows">${строки}</div>
  </div>`;
}

function привязатьЧек() {
  const док = $("saleDoc");
  док.querySelectorAll("[data-sremove]").forEach(b => b.onclick = () => убратьИзЧека(+b.dataset.sremove));
  док.querySelectorAll("[data-sq]").forEach(inp => {
    inp.oninput = () => {
      сЧек.rows[inp.dataset.sq] = { pid: +inp.dataset.spid, flavor: inp.dataset.sfl || null, qty: inp.value };
      сохранитьЧек();
      обновитьСтрокуПродажи(inp);       // только эта строка и итог: карточку не трогаем, клавиатура не дёргается
      нарисоватьИтогПродажи();
    };
    inp.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const все = [...док.querySelectorAll("[data-sq]:not([disabled])")];
      const дальше = все[все.indexOf(inp) + 1];
      if (дальше) дальше.focus(); else inp.blur();
    };
  });
  док.querySelectorAll("[data-sprice]").forEach(inp => inp.oninput = () => {
    сЧек.prices[+inp.dataset.sprice] = inp.value;
    сохранитьЧек();
    перерисоватьКарточкуПродажи(+inp.dataset.sprice, "[data-sprice]");
  });
}
// Ошибка строки — сразу под ней, без перерисовки карточки.
function ошибкаСтроки(qty, есть) {
  const q = штукВСтроке(qty);
  return Number.isNaN(q) ? "Целое число штук, до 1000." : q > есть ? `На полке ${есть} шт — проверьте.` : "";
}
function обновитьСтрокуПродажи(inp) {
  const строка = inp.closest(".dlvrow");
  const p = сТовар(+inp.dataset.spid);
  const v = p && hasVariants(p) ? p.variants.find(x => x.flavor === inp.dataset.sfl) : null;
  const есть = +(v ? v.stock : p ? p.stock : 0) || 0;
  const текст = ошибкаСтроки(inp.value, есть);
  строка.classList.toggle("bad", !!текст);
  let под = строка.querySelector(".dlverr");
  if (текст && !под) { под = document.createElement("div"); под.className = "dlverr"; строка.appendChild(под); }
  if (под) { if (текст) под.textContent = текст; else под.remove(); }
}
function перерисоватьКарточкуПродажи(pid, фокус) {
  const старая = $("saleDoc").querySelector(`[data-sp="${pid}"]`);
  if (!старая) { нарисоватьПродажу(); return; }
  const поле = фокус ? старая.querySelector(фокус) : null;
  const где = поле ? поле.selectionStart : null;
  const t = document.createElement("div");
  t.innerHTML = карточкаПродажи(pid);
  старая.replaceWith(t.firstElementChild);
  привязатьЧек();
  if (фокус) {
    const новое = $("saleDoc").querySelector(`[data-sp="${pid}"] ${фокус}`);
    if (новое) { новое.focus(); try { новое.setSelectionRange(где, где); } catch (e) {} }
  }
  нарисоватьИтогПродажи();
}

function нарисоватьИтогПродажи() {
  const btn = $("salePost");
  document.querySelectorAll("#salePay [data-spay]").forEach(b => {
    b.classList.toggle("active", !!сЧек && сЧек.payment === b.dataset.spay);
    b.disabled = сИдёт;
  });
  if (!сЧек) { $("saleSum").textContent = ""; btn.disabled = true; return; }
  const и = строкиЧека();
  $("saleSum").textContent = и.штук
    ? `${и.штук} шт на ${деньги(и.сумма)} Br` + (и.ошибок ? ` · исправьте ${и.ошибок} ${plural(и.ошибок, "строку", "строки", "строк")}` : "")
    : (сЧек.order.length ? "Впишите, сколько продано." : "");
  btn.disabled = сИдёт || !и.штук || и.ошибок > 0;
  btn.textContent = сИдёт ? "Провожу…" : "Провести продажу";
}
document.querySelectorAll("#salePay [data-spay]").forEach(b => b.onclick = () => {
  if (сИдёт || !сЧек) return;
  сЧек.payment = b.dataset.spay; сохранитьЧек(); нарисоватьИтогПродажи();
});

function добавитьВЧек(pid) {
  const p = сТовар(pid);
  if (!p || сИдёт) return;
  if (!сЧек.order.includes(pid)) сЧек.order.push(pid);
  if (сЧек.prices[pid] == null) сЧек.prices[pid] = деньги(+p.price);
  // Одна строка без вариантов — сразу «1»: так продают чаще всего.
  if (!hasVariants(p)) сЧек.rows[строкаКлюч(pid, null)] = { pid, flavor: null, qty: "1" };
  сПоиск = ""; $("saleFind").value = "";
  сохранитьЧек();
  нарисоватьПродажу();
}
function убратьИзЧека(pid) {
  if (сИдёт) return;
  сЧек.order = сЧек.order.filter(x => x !== pid);
  for (const [к, r] of Object.entries(сЧек.rows)) if (r.pid === pid) delete сЧек.rows[к];
  delete сЧек.prices[pid];
  сохранитьЧек();
  нарисоватьПродажу();
}

// ---------- Провести ----------
$("salePost").onclick = () => провестиПродажу();
function провестиПродажу() {
  if (сИдёт || !сЧек) return;
  const и = строкиЧека();
  if (!и.штук || и.ошибок) return;
  const оплата = сЧек.payment === "card" ? "картой" : "наличными";
  confirmMsg(`Провести продажу на точке «${сТочка}»?\n\n${и.штук} шт на ${деньги(и.сумма)} Br, ${оплата}. Остаток уменьшится сразу.`,
             () => отправитьПродажу(и));
}
async function отправитьПродажу(и) {
  const точка = сТочка, чек = сЧек;
  // Ключ — ДО запроса и в черновик: ответ не дошёл, приложение закрыли —
  // повтор отдаст ту же продажу, а не запишет вторую.
  if (!чек.token) чек.token = новыйКлючОперации();
  сохранитьЧек();
  сИдёт = true; нарисоватьИтогПродажи(); нарисоватьПродажу();
  let d = null;
  try {
    const r = await fetch("/api/admin/sale", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, city: точка, payment: чек.payment, client_token: чек.token,
                             lines: и.строки.map(x => ({ id: x.id, flavor: x.flavor, qty: x.qty, price: x.price })) }) });
    d = await r.json().catch(() => null);
  } catch (e) { d = null; }
  сИдёт = false;
  if (!d || typeof d !== "object") {
    // Ответ потерян. Записана ли продажа — видно по списку «Сегодня»: там у
    // каждой продажи её ключ. Нажать «Провести» ещё раз было бы мало: остаток
    // уже уменьшен записанной продажей, и тот же чек выглядел бы «больше,
    // чем на полке».
    const [, списокЕсть] = await Promise.all([refreshProducts(), загрузитьСегодня()]);
    if (списокЕсть && чекЗаписан(точка, чек)) return;
    нарисоватьПродажу();
    alertMsg(списокЕсть
      ? "Ответ сервера не дошёл, и продажи в списке «Сегодня» нет — значит, она не записалась. Чек на месте: нажмите «Провести» ещё раз."
      : "Ответ сервера не дошёл — не знаю, прошла ли продажа, и список проверить не вышло. Чек на месте: "
        + "нажмите «Провести» ещё раз — если она уже записана, второй раз не запишется.");
    return;
  }
  if (!d.ok) {
    // Прошлая попытка с этого экрана уже записана, а чек с тех пор другой:
    // ключ отслужил — следующая попытка пойдёт с новым.
    if (d.error === "token_reused") { чек.token = null; сохранитьЧек(); }
    нарисоватьПродажу();
    alertMsg(d.message || "Не получилось провести продажу.");
    загрузитьСегодня();
    return;
  }
  забытьЧек(точка);
  toast(d.replay ? `Эта продажа уже была записана · ${деньги(d.total)} Br` : `Продано · ${деньги(d.total)} Br`);
  await Promise.all([refreshProducts(), загрузитьСегодня()]);
  нарисоватьПродажу();
}
// Записано — чек этой точки начинается заново.
function забытьЧек(точка) {
  try { localStorage.removeItem(продажаКлюч(точка)); } catch (e) {}
  if (сТочка === точка) сЧек = новыйЧек();
  нарисоватьПродажу();
}
// Чек с ключом уже есть в списке «Сегодня» — значит, записан (ответ когда-то
// потерялся). Говорим об этом и начинаем чек заново. true — так и было.
function чекЗаписан(точка, чек) {
  const найдено = чек && чек.token && (сСегодня || []).find(x => x.token === чек.token);
  if (!найдено || сТочка !== точка) return false;
  забытьЧек(точка);
  toast(`Продажа записана · ${деньги(найдено.total)} Br`);
  return true;
}

// ---------- Сегодня на точке ----------
// true — загрузились. Не загрузились — так и сказано, с «Повторить»: пустой
// список читался бы как «сегодня продаж не было».
async function загрузитьСегодня() {
  const точка = сТочка;
  let список = null;
  try {
    const r = await fetch("/api/admin/sales", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ initData, city: точка }) });
    const d = await r.json();
    if (d && d.ok && Array.isArray(d.sales)) список = d.sales;
  } catch (e) { /* список = null */ }
  if (точка !== сТочка) return false;           // пока грузили, выбрали другую точку
  сСегодня = список;
  // В телефоне чек с ключом прошлой попытки, а продажа с этим ключом уже
  // в списке — ответ тогда потерялся. Чек отслужил.
  if (список && !сИдёт) чекЗаписан(точка, сЧек);
  нарисоватьСегодня();
  return список !== null;
}
function нарисоватьСегодня() {
  const узел = $("saleToday");
  if (!сТочка) { узел.innerHTML = ""; return; }
  if (сСегодня === null) {
    узел.innerHTML = `<div class="dlvhint">Продажи за сегодня не загрузились. <button type="button" class="barbtn" id="saleTodayRetry">↻ Повторить</button></div>`;
    const b = $("saleTodayRetry"); if (b) b.onclick = загрузитьСегодня;
    return;
  }
  const живые = сСегодня.filter(x => x.status !== "canceled");
  const сумма = живые.reduce((s, x) => s + x.total, 0);
  узел.innerHTML = `<div class="nowhere-h">Сегодня на точке · ${живые.length} ${plural(живые.length, "продажа", "продажи", "продаж")} на ${деньги(сумма)} Br</div>`
    + (сСегодня.length ? сСегодня.map(x => `<div class="salesale${x.status === "canceled" ? " off" : ""}">
        <b>${esc((x.created_at || "").slice(11, 16))} · ${деньги(x.total)} Br · ${x.payment === "card" ? "картой" : x.payment === "cash" ? "наличными" : "—"}</b>
        ${x.status === "canceled" ? `<span class="dlvhint">отменена</span>`
          : `<button type="button" class="barbtn" data-scancel="${x.id}">Отменить</button>`}
        <div class="salewhat">${esc(x.items.map(и => `${и.name} × ${и.qty}`).join(", "))}${x.seller ? ` · ${esc(x.seller)}` : ""}</div>
      </div>`).join("") : `<div class="dlvhint">Пока ни одной.</div>`);
  узел.querySelectorAll("[data-scancel]").forEach(b => b.onclick = () => отменитьПродажу(+b.dataset.scancel));
}
function отменитьПродажу(id) {
  const x = (сСегодня || []).find(s => s.id === id); if (!x) return;
  confirmMsg(`Отменить продажу ${деньги(x.total)} Br (${(x.created_at || "").slice(11, 16)})?\n\nШтуки вернутся на полку, из выручки она уйдёт.`, async () => {
    let d = null;
    try {
      const r = await fetch("/api/admin/sale/cancel", { method: "POST", headers: { "Content-Type": "application/json" },
                                                      body: JSON.stringify({ initData, id }) });
      d = await r.json().catch(() => null);
    } catch (e) { d = null; }
    const [товарыЕсть, списокЕсть] = await Promise.all([refreshProducts(), загрузитьСегодня()]);
    нарисоватьПродажу();
    if (!d) alertMsg("Ответ сервера не дошёл — не знаю, отменилась ли продажа. " + (товарыЕсть && списокЕсть
      ? "Список обновлён: посмотрите, отменена ли она." : "Обновить список тоже не вышло — проверьте связь и посмотрите позже."));
    else if (!d.ok) alertMsg(d.message || "Не удалось отменить.");
    else toast("Продажа отменена — штуки на полке");
  });
}
// ----- /Продажа на точке -----
