// 06-catalog.js — ассортимент: бренды, вкусы, характеристики, редактор товара
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ----- Бренды и вкусы -----
let brands = [], brandFlavors = [], editingBrandId = null;
async function fetchBrands() {
  try {
    const r = await fetch("/api/brands");
    brands = await r.json();
  } catch (e) { /* останемся с тем, что было — экран уже открыт */ }
}
const catName = (code) => (CAT_OPTS.find(([c]) => c === code) || [, code])[1];

function renderFlavorChips() {
  $("brFlavorChips").innerHTML = brandFlavors.map((f, i) =>
    `<span class="fchip">${esc(f)}<b data-fx="${i}">✕</b></span>`).join("");
  $("brFlavorChips").querySelectorAll("[data-fx]").forEach(b =>
    b.onclick = () => { brandFlavors.splice(+b.dataset.fx, 1); renderFlavorChips(); renderKnownFlavors(); });
}
$("brFlavorAdd").onclick = () => {
  const v = $("brFlavorInput").value.trim();
  if (!v) return;
  разобратьСписок(v).forEach(f => {          // «0,6 Ом» — одно значение, не два
    // Сверяем без учёта регистра: «мята» после «Мята» — это тот же вкус.
    if (!brandFlavors.some(x => ключВарианта(x) === ключВарианта(f))) brandFlavors.push(f);
  });
  $("brFlavorInput").value = ""; renderFlavorChips(); renderKnownFlavors();
};
$("brFlavorInput").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("brFlavorAdd").click(); } };

$("brSave").onclick = async () => {
  const name = $("brName").value.trim();
  if (!name) { alertMsg("Введите название бренда."); return; }
  const body = { initData, name, category: $("brCat").value, flavors: brandFlavors };
  if (editingBrandId) body.id = editingBrandId;
  try {
    const r = await fetch("/api/admin/brand", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "exists" ? `Бренд «${d.name}» уже есть — правьте его, а не заводите второй.`
             : "Не удалось сохранить бренд.");
      return;
    }
    resetBrandForm();
    // fetchFlavors() отдельно не зовём — refreshAll() его и так перечитывает
    // (товары, категории, точки, вкусы разом); fetchBrands() он не трогает,
    // поэтому идёт параллельно, а не третьим ожиданием подряд.
    await Promise.all([fetchBrands(), refreshAll()]);   // переименование бренда переносит и товары, и вкусы
    renderKnownFlavors();
    renderBrandList();
    alertMsg(d.moved ? `Бренд сохранён ✅ Товаров перенесено: ${d.moved}` : "Бренд сохранён ✅");
  } catch (e) { alertMsg(текстСбоя(e)); }
};
function resetBrandForm() {
  editingBrandId = null; brandFlavors = [];
  $("brName").value = ""; $("brFlavorInput").value = "";
  $("brCancel").style.display = "none"; $("brSave").textContent = "Сохранить бренд";
  renderFlavorChips();
}
$("brCancel").onclick = resetBrandForm;

let brandSearch = "";
const brandExpanded = new Set();   // id брендов, у которых раскрыты вкусы
$("brSearch").oninput = () => { brandSearch = $("brSearch").value; renderBrandList(); };

function renderBrandList() {
  if (!brands.length) { $("brList").innerHTML = `<p style="color:var(--hint);margin-top:12px">Брендов пока нет.</p>`; return; }
  const q = brandSearch.trim().toLowerCase();
  const filtered = brands.filter(b => !q || b.name.toLowerCase().includes(q));
  if (!filtered.length) { $("brList").innerHTML = `<p style="color:var(--hint);margin-top:12px">Ничего не найдено.</p>`; return; }
  let html = "";
  // Общие бренды идут первыми: бренд «во всех категориях» — теперь норма,
  // а не исключение (Vaporesso делает и поды, и картриджи).
  const groups = группыКатегорий(filtered, [["", "Во всех категориях"], ...CAT_OPTS]);
  for (const [cat, cn] of groups) {
    const group = filtered.filter(b => (b.category || "") === cat);
    if (!group.length) continue;
    html += `<div class="brgroup">${cn} · ${group.length}</div>`;
    html += group.map(b => {
      const open = brandExpanded.has(b.id);
      const chips = open
        ? `<div class="brflavors">${b.flavors.length
            ? b.flavors.map(f => `<span class="brchip">${esc(f)}</span>`).join("")
            : '<span style="color:var(--hint);font-size:12.5px">вкусов нет</span>'}</div>`
        : "";
      const used = shelf().filter(p => p.brand === b.name).length;
      return `<div class="admrow brrow" data-brtoggle="${b.id}">
          <div class="an">${esc(b.name)}<small>вкусов: ${b.flavors.length} · ${used ? `${used} ${plural(used, "товар", "товара", "товаров")}` : "нет товаров"} ${open ? '▲' : '▼'}</small></div>
          <button class="iconbtn" data-bre="${b.id}">✏️</button>
          <button class="iconbtn danger" data-brd="${b.id}">🗑</button>
        </div>${chips}`;
    }).join("");
  }
  $("brList").innerHTML = html;
  $("brList").querySelectorAll("[data-brtoggle]").forEach(row => row.onclick = (e) => {
    if (e.target.closest("[data-bre],[data-brd]")) return;   // клик по кнопкам не раскрывает
    const id = +row.dataset.brtoggle;
    if (brandExpanded.has(id)) brandExpanded.delete(id); else brandExpanded.add(id);
    renderBrandList();
  });
  $("brList").querySelectorAll("[data-bre]").forEach(b => b.onclick = () => editBrand(+b.dataset.bre));
  $("brList").querySelectorAll("[data-brd]").forEach(b => b.onclick = () => delBrand(+b.dataset.brd));
}
function editBrand(id) {
  const b = brands.find(x => x.id === id); if (!b) return;
  editingBrandId = id; $("brName").value = b.name; $("brCat").value = b.category || "";
  brandFlavors = [...b.flavors]; renderFlavorChips(); renderKnownFlavors();
  $("brCancel").style.display = "block"; $("brSave").textContent = "Обновить бренд";
  $("brFormSect").open = true;   // раскрыть свёрнутую форму
  $("brName").scrollIntoView({ behavior: "smooth", block: "center" });
}
function delBrand(id) { confirmMsg("Удалить бренд?", () => doDelBrand(id)); }
async function doDelBrand(id, force) {
  try {
    const r = await fetch("/api/admin/brand/delete", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id, force: !!force }) });
    const d = await r.json();
    if (!d.ok) {
      // У товаров бренд записан строкой: удаление справочника их не тронет,
      // поэтому честно говорим, сколько их, и спрашиваем ещё раз.
      if (d.error === "has_products") {
        confirmMsg(`На этом бренде ${d.count} ${plural(d.count, "товар", "товара", "товаров")}. Они останутся с прежним названием бренда, но подсказки вкусов пропадут. Всё равно удалить?`,
          () => doDelBrand(id, true));
        return;
      }
      alertMsg("Не удалось удалить бренд.");
      return;
    }
    if (editingBrandId === id) resetBrandForm();
    await fetchBrands(); renderBrandList();
  } catch (e) { alertMsg(текстСбоя(e)); }
}

// Управление локациями
let deliveryByCity = {}, pointsByCity = {};
async function loadDelivery() {
  deliveryByCity = {}; pointsByCity = {};
  await Promise.all(locations.map(async l => {
    try {
      const r = await fetch(`/api/delivery?city=${encodeURIComponent(l.name)}`);
      const d = await r.json();
      deliveryByCity[l.name] = Array.isArray(d) ? d : (d.methods || []);
      pointsByCity[l.name] = Array.isArray(d) ? [] : (d.points || []);
    } catch (e) { deliveryByCity[l.name] = []; pointsByCity[l.name] = []; }
  }));
}
// Способ получения — это ОДНО решение: везём мы или человек забирает сам.
// Всё остальное — следствие, поэтому лишние поля не показываем: на самовывозе
// не спрашивают «как подписать поле адреса», а на доставке не указывают адрес
// нашей точки. Форма одна на добавление и на правку, чтобы они не разъезжались.
function deliveryFormHtml(m, точек) {
  const везём = m ? !!m.needs_address : true;
  const зн = x => esc(x == null ? "" : String(x));
  return `
    <label>Название</label>
    <input class="dm-name" value="${зн(m ? m.name : "")}" placeholder="Доставка / Самовывоз">
    <div class="dlabel">Как клиент получает заказ</div>
    <div class="modepick">
      <button type="button" class="opt dm-mode${везём ? " active" : ""}" data-mode="courier">🚚 Везём клиенту<small class="ppnote">Спросим адрес и телефон, можно взять доплату</small></button>
      <button type="button" class="opt dm-mode${везём ? "" : " active"}" data-mode="pickup">🏬 Клиент забирает сам<small class="ppnote">${точек === 1 ? "Покажем адрес точки самовывоза"
        : точек ? `Выберет одну из ${точек} ${plural(точек, "точки", "точек", "точек")} самовывоза`
        : "Точек самовывоза нет — сначала заведите"}</small></button>
    </div>
    <div class="dm-courier"${везём ? "" : ` style="display:none"`}>
      <label>Как подписать поле адреса</label>
      <input class="dm-alabel" value="${зн(m ? m.address_label : "")}" placeholder="Адрес">
      <div class="dnote">Так поле называется у клиента: «Адрес», «Станция метро». Телефон на доставке спрашиваем всегда.</div>
      <label>Доплата за доставку (Br)</label>
      <input class="dm-fee" inputmode="decimal" value="${зн(m ? (m.fee || 0) : "")}" placeholder="0">
    </div>
    <div class="dm-pickup-box"${везём ? ` style="display:none"` : ""}>
      ${точек ? "" : `<div class="dwarn">Сначала заведите точку самовывоза выше — иначе клиенту некуда приехать, и способ работать не будет.</div>`}
    </div>
    <div class="chk"><input type="checkbox" class="dm-pay"${(m ? m.needs_payment : true) ? " checked" : ""}><label style="margin:0">Спросить способ оплаты</label></div>
    <div class="dnote">Снимите, если платят на месте — тогда клиент не выбирает «картой/наличными».</div>`;
}

// Переключение режима прячет чужие поля, но НЕ стирает их: значения остаются в
// разметке, и передумавший админ не теряет то, что уже вписал.
function bindDeliveryForm(body) {
  body.querySelectorAll(".dm-mode").forEach(b => b.onclick = () => {
    body.querySelectorAll(".dm-mode").forEach(x => x.classList.toggle("active", x === b));
    const везём = b.dataset.mode === "courier";
    body.querySelector(".dm-courier").style.display = везём ? "" : "none";
    body.querySelector(".dm-pickup-box").style.display = везём ? "none" : "";
  });
}

// Собрать способ из формы. null — значит уже сказали человеку, чего не хватает.
function собратьСпособ(body) {
  const точек = +(body.dataset.points || 0);
  const name = body.querySelector(".dm-name").value.trim();
  if (!name) { alertMsg("Введите название способа."); return null; }
  const везём = body.querySelector(`.dm-mode[data-mode="courier"]`).classList.contains("active");
  if (!везём && !точек) {
    alertMsg("Сначала заведите точку самовывоза — иначе клиенту некуда приехать.");
    return null;
  }
  return {
    name,
    needs_address: везём,
    address_label: body.querySelector(".dm-alabel").value.trim() || (везём ? "Адрес" : ""),
    // Адрес самовывоза теперь ОДИН на город — список точек. Старую строку в
    // способе чистим: два места для одного адреса и были причиной путаницы,
    // а пропавший адрес уже перенесён в точки (перенос 0006).
    pickup_address: "",
    // На самовывозе доплаты за доставку нет. Поле спрятано, и оставить в базе
    // старое число значило бы брать с покупателя деньги, которых в настройках
    // не видно.
    fee: везём ? (body.querySelector(".dm-fee").value || 0) : 0,
    needs_payment: body.querySelector(".dm-pay").checked,
  };
}

function renderLocList() {
  if (!locations.length) { $("locList").innerHTML = `<p style="color:var(--hint)">Локаций пока нет.</p>`; return; }
  $("locList").innerHTML = locations.map(l => {
    const methods = deliveryByCity[l.name] || [];
    const точек = (pointsByCity[l.name] || []).length;
    const mrows = methods.map(m => {
      const info = m.needs_address ? `везём клиенту · поле «${esc(m.address_label)}»`
                 : точек === 1 ? `забирает сам · ${esc((pointsByCity[l.name] || [])[0].address)}`
                 : точек ? `забирает сам · выбор из ${точек} ${plural(точек, "точки", "точек", "точек")}`
                 : "забирает сам · ⚠️ ТОЧЕК НЕТ";
      const tail = `${m.fee ? " · +" + m.fee.toFixed(2) + " Br" : ""} · ${m.needs_payment ? "оплата" : "без оплаты"}`;
      // Строка способа И ЕСТЬ кнопка правки: раньше рядом жили карандаш и
      // отдельная полоска «Изменить «X»» — два органа управления на одно
      // действие и две строки на способ.
      return `<details class="sect" data-dmbox="${m.id}" style="margin:0 0 8px;background:var(--surface-2);box-shadow:none">
          <summary class="secthead dmhead">
            <span class="an">${esc(m.name)}<small>${info}${tail}</small></span>
            <button class="iconbtn danger" data-dmdel="${m.id}">🗑</button>
          </summary>
          <div class="sectbody form" data-points="${точек}">
            ${deliveryFormHtml(m, точек)}
            <button class="bigbtn dm-save" data-mid="${m.id}" style="margin-top:12px">Сохранить</button>
          </div>
        </details>`;
    }).join("") || `<p style="color:var(--hint);font-size:13px;margin:4px 0">Способов ещё нет.</p>`;
    return `<div class="card-block">
      <div class="admrow" style="padding:0;background:none;box-shadow:none" data-locrow="${l.id}">
        <div class="an" style="font-weight:800;font-size:15px" data-locname="${l.id}">${esc(l.name)}</div>
        <button class="iconbtn" data-locedit="${l.id}">✏️</button>
        <button class="iconbtn danger" data-locdel="${l.id}">🗑</button></div>
      <div class="dlabel" style="margin:10px 0 6px">📍 Куда клиент может приехать</div>
      ${(pointsByCity[l.name] || []).map(p => `
        <div class="admrow" style="background:var(--surface-2);box-shadow:none">
          <div class="an">${esc(p.address)}${p.note ? `<small>${esc(p.note)}</small>` : ""}</div>
          <button class="iconbtn danger" data-ppdel="${p.id}">🗑</button></div>`).join("")
        || `<p style="color:var(--hint);font-size:13px;margin:4px 0">Адресов ещё нет. Пока нет ни одного, самовывоз работать не будет: клиенту некуда приехать.</p>`}
      <details class="sect" style="margin:8px 0 0;background:var(--surface-2);box-shadow:none">
        <summary class="secthead" style="font-size:14px">➕ Добавить адрес</summary>
        <div class="sectbody form" data-ptcity="${esc(l.name)}">
          <label>Адрес</label><input class="pp-addr" placeholder="ул. Немига 5, вход со двора">
          <label>Примечание (когда работает, ориентир)</label><input class="pp-note" placeholder="10:00–21:00">
          <button class="bigbtn pp-add" style="margin-top:12px">Добавить адрес</button>
        </div>
      </details>

      <div class="dlabel" style="margin:16px 0 6px">🚚 Способы получения</div>
      ${mrows}
      <details class="sect" style="margin:8px 0 0;background:var(--surface-2);box-shadow:none">
        <summary class="secthead" style="font-size:14px">➕ Добавить способ</summary>
        <div class="sectbody form" data-city="${esc(l.name)}" data-points="${точек}">
          ${deliveryFormHtml(null, точек)}
          <button class="bigbtn dm-add" style="margin-top:12px">Добавить способ</button>
        </div>
      </details>
    </div>`;
  }).join("");
  $("locList").querySelectorAll("[data-locdel]").forEach(b => b.onclick = () => delLocation(+b.dataset.locdel));
  $("locList").querySelectorAll("[data-locedit]").forEach(b => b.onclick = () => editLocation(+b.dataset.locedit));
  $("locList").querySelectorAll("[data-dmdel]").forEach(b => b.onclick = e => {
    // Кнопка живёт внутри summary: без этого нажатие заодно раскрывало бы
    // форму правки под вопросом «удалить?».
    e.preventDefault(); e.stopPropagation();
    delDeliveryMethod(+b.dataset.dmdel);
  });
  $("locList").querySelectorAll(".dm-add").forEach(b => b.onclick = () => addDeliveryMethod(b));
  $("locList").querySelectorAll(".sectbody.form").forEach(body => { if (body.querySelector(".dm-mode")) bindDeliveryForm(body); });
  $("locList").querySelectorAll(".dm-save").forEach(b => b.onclick = () => saveDeliveryMethod(b));
  $("locList").querySelectorAll(".pp-add").forEach(b => b.onclick = () => addPickupPoint(b));
  $("locList").querySelectorAll("[data-ppdel]").forEach(b => b.onclick = () => delPickupPoint(+b.dataset.ppdel));
}

async function addPickupPoint(btn) {
  const body = btn.closest(".sectbody");
  const address = body.querySelector(".pp-addr").value.trim();
  if (!address) { alertMsg("Введите адрес точки."); return; }
  const payload = { initData, city: body.dataset.ptcity, address,
                    note: body.querySelector(".pp-note").value.trim() };
  btn.disabled = true;
  try {
    const r = await fetch("/api/admin/point", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if ((await r.json()).ok) { await loadDelivery(); renderLocList(); }
    else alertMsg("Не удалось добавить точку.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { btn.disabled = false; }
}

function delPickupPoint(id) { confirmMsg("Удалить точку самовывоза?", () => doDelPickupPoint(id)); }
async function doDelPickupPoint(id) {
  if (!await админПост("/api/admin/point/delete", { id }, "удалить адрес")) return;
  await loadDelivery(); renderLocList();
}
async function saveDeliveryMethod(btn) {
  const body = btn.closest(".sectbody");
  const форма = собратьСпособ(body);
  if (!форма) return;
  const payload = Object.assign({ initData, id: +btn.dataset.mid }, форма);
  try {
    const r = await fetch("/api/admin/delivery/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не удалось сохранить."); return; }
    await loadDelivery(); renderLocList();
  } catch (e) { alertMsg(текстСбоя(e)); }
}
async function addDeliveryMethod(btn) {
  const body = btn.closest(".sectbody");
  const форма = собратьСпособ(body);
  if (!форма) return;
  const payload = Object.assign({ initData, city: body.dataset.city }, форма);
  try {
    const r = await fetch("/api/admin/delivery", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не удалось добавить способ."); return; }
    await loadDelivery(); renderLocList();
  } catch (e) { alertMsg(текстСбоя(e)); }
}
function delDeliveryMethod(id) { confirmMsg("Удалить способ получения?", () => doDelDeliveryMethod(id)); }
async function doDelDeliveryMethod(id) {
  if (!await админПост("/api/admin/delivery/delete", { id }, "удалить способ")) return;
  await loadDelivery(); renderLocList();
}
$("locAdd").onclick = async () => {
  const name = $("locName").value.trim();
  if (!name) { alertMsg("Введите название локации."); return; }
  try {
    const r = await fetch("/api/admin/location", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, name }) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не удалось добавить локацию."); return; }
    $("locName").value = "";
    await refreshAll();          // появился город: он в фильтрах и в формах
    await loadDelivery(); renderLocList();
  } catch (e) { alertMsg(текстСбоя(e)); }
};
function delLocation(id) { confirmMsg("Удалить локацию?", () => doDelLocation(id)); }
async function doDelLocation(id) {
  try {
    const r = await fetch("/api/admin/location/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id }) });
    const d = await r.json();
    if (!d.ok) {
      const тексты = {
        has_products: "Нельзя удалить: в этой локации есть товары. Сначала уберите их.",
        has_orders: `Нельзя удалить: есть незакрытые заказы (${d.count || "?"}). Сначала закройте их.`,
        has_delivery: "Нельзя удалить: у точки настроены способы доставки. Сначала уберите их.",
        has_pickup: "Нельзя удалить: у точки есть точки самовывоза. Сначала уберите их.",
        has_staff: "Нельзя удалить: за точкой закреплены продавцы. Сначала снимите их.",
      };
      alertMsg(тексты[d.error] || "Не удалось удалить локацию.");
      return;
    }
    await refreshAll();          // города пропали из фильтров и форм
  } catch (e) { alertMsg(текстСбоя(e)); }
}

// Переименование вместо удалить+создать: раньше опечатку в названии города
// правили только так — а удаление заблокировано, пока в точке есть товары.
function editLocation(id) {
  const row = document.querySelector(`[data-locrow="${id}"]`);
  const nameEl = document.querySelector(`[data-locname="${id}"]`);
  if (!row || !nameEl) return;
  const было = nameEl.textContent;
  nameEl.outerHTML = `<div class="an" style="font-weight:800;font-size:15px;flex:1">
    <input data-locinput="${id}" value="${esc(было)}" style="width:100%"></div>`;
  row.querySelector(`[data-locedit="${id}"]`).outerHTML =
    `<button class="iconbtn" data-locok="${id}">✓</button>`;
  const inp = row.querySelector(`[data-locinput="${id}"]`);
  inp.focus(); inp.select();
  const save = () => doRenameLocation(id, inp.value.trim());
  row.querySelector(`[data-locok="${id}"]`).onclick = save;
  inp.onkeydown = e => { if (e.key === "Enter") save(); if (e.key === "Escape") renderLocList(); };
}
async function doRenameLocation(id, name) {
  if (!name) { alertMsg("Название не может быть пустым."); return; }
  try {
    const r = await fetch("/api/admin/location/rename", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id, name }) });
    const d = await r.json();
    if (!d.ok) { alertMsg(d.message || "Не удалось переименовать."); return; }
    await refreshAll();
  } catch (e) { alertMsg(текстСбоя(e)); }
}

// Переключение формы: одноразки vs обычный товар
// ----- Бренд и вкус выбираются из справочника, а не набираются заново -----
// Свободный ввод плодил «Vaporesso», «vaporesso» и «Vaporesso » — в фильтре
// каталога это три разных бренда, и половина товаров пряталась не там.
let knownFlavors = [];
async function fetchFlavors() {
  try {
    const r = await fetch("/api/flavors");
    const list = await r.json();
    if (Array.isArray(list)) knownFlavors = list;
  } catch (e) {}
}
function pickerHtml(id, current, options, newLabel) {
  const cur = (current || "").trim();
  const known = options.includes(cur);
  return `<select id="${id}">
      <option value="">— не указан —</option>
      ${options.map(n => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("")}
      ${cur && !known ? `<option selected>${esc(cur)}</option>` : ""}
      <option value="__new">${newLabel}</option>
    </select>
    <input id="${id}_new" placeholder="Введите название" style="display:none;margin-top:6px">`;
}
function bindPicker(id) {
  const sel = $(id), inp = $(id + "_new");
  if (!sel || !inp) return;
  sel.onchange = () => {
    const isNew = sel.value === "__new";
    inp.style.display = isNew ? "" : "none";
    if (isNew) inp.focus();
  };
}
function pickerValue(id) {
  const sel = $(id), inp = $(id + "_new");
  if (!sel) return "";
  return (sel.value === "__new" ? (inp ? inp.value.trim() : "") : sel.value.trim());
}
// Для категории показываем её бренды и общие: Elf Bar не нужен в списке
// брендов для зарядок, а Vaporesso нужен везде.
const brandNames = (category) => brands
  .filter(b => !category || !b.category || b.category === category)
  .map(b => b.name)
  .sort((a, b) => a.localeCompare(b, "ru"));
// Новое имя из формы товара сразу попадает в справочник: иначе оно осталось бы
// только строкой в товаре, и в следующий раз его пришлось бы набирать заново.
async function ensureBrandExists(name) {
  name = (name || "").trim();
  if (!name || brands.some(b => b.name.toLowerCase() === name.toLowerCase())) return;
  try {
    await fetch("/api/admin/brand", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, name, category: "", flavors: [] }) });
    await fetchBrands();
  } catch (e) { /* молчим намеренно: продавцу справочник брендов не положен,
                   а имя бренда всё равно сохранится строкой в товаре */ }
}

// ----- Поля характеристик строятся по настройкам категории -----
// Раньше «крепость» и «объём» были прибиты к форме, а у картриджа нужно
// сопротивление и совместимость. Теперь набор полей приходит из категории.
function specFieldsHtml(category, values, prefix) {
  const specs = specsOf(category);
  if (!specs.length) return "";
  return `<label>Характеристики</label>` + specs.map(s => {
    const id = `${prefix}${s.key}`;
    const v = (values || {})[s.key];
    const val = v === undefined ? "" : String(v);
    const label = s.label + (s.unit ? ` (${s.unit})` : "");
    if (s.kind === "select" && s.options.length) {
      return `<label class="speclbl">${esc(label)}</label><select id="${id}" data-spec="${esc(s.key)}">
        <option value="">—</option>
        ${s.options.map(o => `<option value="${esc(o)}" ${val === o ? "selected" : ""}>${esc(o)}</option>`).join("")}
      </select>`;
    }
    const mode = s.kind === "number" ? ` inputmode="decimal"` : "";
    return `<label class="speclbl">${esc(label)}</label><input id="${id}" data-spec="${esc(s.key)}"${mode} value="${esc(val)}">`;
  }).join("");
}
function collectSpecs(scopeId) {
  const out = {};
  (document.getElementById(scopeId) || document).querySelectorAll("[data-spec]").forEach(el => {
    out[el.dataset.spec] = el.value.trim();
  });
  return out;
}

// Форма «новый товар» переехала в «Ассортимент»: модель описывается один раз,
// а здесь у товара остаётся то, что своё у каждой точки — цена и остаток.
// Завоз на точку начинается здесь же, в «Ценах и остатках»: раньше отсюда
// отсылали в «Ассортимент», и продавец уходил в раздел, который ведёт владелец
// и в котором ему больше нечего делать.
$("openStockPick").onclick = openStockPick;
$("stockPickClose").onclick = () => $("stockPickView").classList.remove("show");
$("stockPickSearch").oninput = renderStockPick;

async function openStockPick() {
  $("stockPickView").classList.add("show");
  $("stockPickSearch").value = "";
  // Модели грузились только при входе в «Ассортимент», а сюда попадают мимо
  // него — список был пуст, и это выглядело как «завозить нечего».
  $("stockPickList").innerHTML = loaderHtml();
  await fetchModels();
  renderStockPick();
}

function renderStockPick() {
  const q = ($("stockPickSearch").value || "").trim().toLowerCase();
  const список = models.filter(m => !q || `${m.name} ${m.brand || ""}`.toLowerCase().includes(q));
  if (!models.length) {
    $("stockPickList").innerHTML = `<p style="color:var(--hint);font-size:13.5px;margin:8px 0 0">Ассортимент пуст. Модели — название, вкусы, фото — заводит владелец в разделе «Ассортимент».</p>`;
    return;
  }
  if (!список.length) { $("stockPickList").innerHTML = `<p style="color:var(--hint);margin:8px 0 0">Ничего не найдено.</p>`; return; }
  // Где модель уже стоит — прямо в строке: чаще всего сюда заходят завезти
  // то, чего на своей точке ещё нет, и это должно быть видно до нажатия.
  const мояТочка = myScope();
  $("stockPickList").innerHTML = список.map(m => {
    const стоит = shelf().filter(p => p.model_id === m.id);
    const уже = мояТочка
      ? (стоит.some(p => p.city === мояТочка) ? `<span class="tagbadge">уже на вашей точке</span>` : "")
      : (стоит.length ? `<small>уже: ${стоит.map(p => esc(p.city)).join(", ")}</small>` : "");
    return `<div class="admrow" data-pick="${m.id}">
      <div class="an">${m.brand ? esc(m.brand) + " " : ""}${esc(m.name)}
        <small>${esc(catName(m.category))}${m.flavors.length ? ` · вариантов: ${m.flavors.length}` : ""}</small>${уже}</div>
      <span style="color:var(--hint)">›</span></div>`;
  }).join("");
  $("stockPickList").querySelectorAll("[data-pick]").forEach(b => b.onclick = () => {
    $("stockPickView").classList.remove("show");
    openStockIn(+b.dataset.pick);
  });
}

// Перерисовка списка (после правки цены, прихода, обновления с сервера) не
// должна уносить человека к началу: поиск и фильтры живут в переменных и
// переживают её сами, а положение прокрутки — нет, его держим здесь.
function renderAdminList() {
  const вид = $("productsView");
  const y = вид ? вид.scrollTop : 0;
  нарисоватьСписокТоваров();
  if (вид) вид.scrollTop = y;
}

// ----- Список товаров -----
function нарисоватьСписокТоваров() {
  const счёт = $("admCount");
  if (!shelf().length) {
    счёт.textContent = "";
    $("adminList").innerHTML = `<p class="listempty">Товаров пока нет.</p>`; return;
  }
  const q = (admSearch || "").trim().toLowerCase();
  // Продавец точки ведёт свою точку — чужие товары ему не показываем даже
  // на чтение: правки по ним сервер отклонит, а список только путает.
  const свои = shelf().filter(p => !myScope() || p.city === myScope());
  const list = свои.filter(p => {
    if (admCatFilter !== "all" && p.category !== admCatFilter) return false;
    if (admLocFilter !== "all" && p.city !== admLocFilter) return false;
    const st = stockState(p);
    if (admStockFilter === "need" && st === "ok") return false;
    if (admStockFilter === "out" && st !== "out") return false;
    if (q && !(`${p.name} ${p.brand || ""} ${p.flavor || ""}`.toLowerCase().includes(q))) return false;
    return true;
  });
  // Сколько показано — и заодно видно, что отбор включён: «3 товара» при
  // забытом фильтре читались бы как «на точке всего три».
  счёт.textContent = list.length === свои.length ? `${свои.length} ${plural(свои.length, "товар", "товара", "товаров")}`
    : `Показано ${list.length} из ${свои.length}`;
  if (!list.length) {
    const msg = admStockFilter === "out" ? "Ничего не кончилось — на всех точках есть остаток."
              : admStockFilter === "need" ? "Завозить нечего: везде больше " + LOW_STOCK + " шт."
              : "Ничего не найдено.";
    $("adminList").innerHTML = `<p class="listempty">${msg}</p>`; return;
  }
  // Точку пишем в строке, только когда в списке все точки сразу. Выбран один
  // город или продавец ведёт свою точку — «Минск» в каждой строке ничего не
  // сообщает, а место под сведения на узком телефоне дорого.
  const сТочкой = !myScope() && admLocFilter === "all" && locations.length > 1;
  $("adminList").innerHTML = list.map(p => строкаТовара(p, сТочкой)).join("");
  $("adminList").querySelectorAll("[data-price]").forEach(b => b.onclick = () => открытьЦену(+b.dataset.price));
  $("adminList").querySelectorAll("[data-move]").forEach(b => b.onclick = () => openStockMove(+b.dataset.move));
  $("adminList").querySelectorAll("[data-edit]").forEach(b => b.onclick = () => openEdit(+b.dataset.edit));
  $("adminList").querySelectorAll("[data-more]").forEach(b => b.onclick = () => открытьЕщё(+b.dataset.more));
}

// «3 вкуса», «4 цвета», «2 сопротивления» — словом категории, а не безликим
// «3 варианта»: у жидкости владелец думает вкусами, у пода — расцветками.
// Слово он задаёт в категории сам; склоняем известные, остальное — «варианты».
// Последняя форма — для «Всем показанным вкусам» в приёме поставки.
const ФОРМЫ_ВАРИАНТА = { "вкус": ["вкус", "вкуса", "вкусов", "вкусам"], "цвет": ["цвет", "цвета", "цветов", "цветам"],
  "сопротивление": ["сопротивление", "сопротивления", "сопротивлений", "сопротивлениям"] };
const формыВарианта = (p) => ФОРМЫ_ВАРИАНТА[String(catVariant(p.category) || "").trim().toLowerCase()]
  || ["вариант", "варианта", "вариантов", "вариантам"];
function вариантовСтрокой(p) {
  const n = p.variants.length, [один, два, пять] = формыВарианта(p);
  return `${n} ${plural(n, один, два, пять)}`;
}

// Строка товара. Сверху — название, под ним остаток; справа — цена-кнопка:
// за ней в этот список заходят чаще всего. Ниже — второстепенное (точка,
// варианты, «хит», кто ждёт) и действия с подписями.
function строкаТовара(p, сТочкой) {
  // Неразрывные пробелы внутри кусков: «без фото» или «в заказах 3»,
  // разорванные переносом по словам, читаются как мусор.
  const нр = (t) => String(t).replace(/ /g, "&nbsp;");
  const st = stockState(p);
  // Остаток — словами: сколько можно продать и сколько уже обещано в
  // невыданных заказах. Раньше было «12 шт · +3 в заказах», и «+3» читалось
  // как «ещё три сверху». Кончилось или мало — цветом и словом, а не только
  // цветом: цвет на солнце и у дальтоника не различить.
  const обещано = p.reserved ? ` · ${нр("в заказах")}&nbsp;<b>${p.reserved}</b>` : "";
  const остаток = st === "out"
    ? `<span class="stk out">${p.reserved ? "Свободных нет" : "Нет в наличии"}</span>${обещано}`
    : `<span class="stk ${st}">Свободно&nbsp;<b>${p.stock}</b>&nbsp;шт${st === "low" ? " · мало" : ""}</span>${обещано}`;
  // Второстепенное — отдельной строкой под остатком и только то, что есть:
  // впихнуть всё в одну строку узкого телефона — значит сделать нечитаемым.
  // Словами, а не значками: «♥ 2» без подсказки не расшифровать.
  const детали = [
    // Сколько человек подписались на поступление — прямой повод завезти,
    // поэтому первым.
    p.waiting ? `<span class="warnc">${нр(`ждут поступления ${p.waiting}`)}</span>` : "",
    сТочкой ? esc(p.city) : "",
    hasVariants(p) ? нр(вариантовСтрокой(p)) : "",
    p.is_hit ? нр("🔥 хит") : "",
    p.favored ? нр(`в избранном ${p.favored}`) : "",
    p.photo_url ? "" : нр("без фото"),
  ].filter(Boolean).join(" · ");
  // Снятый с витрины — плашкой перед сведениями: это состояние товара, а не
  // ещё одна подробность в ряду.
  const снят = p.hidden ? `<span class="tagbadge offtag">снят с витрины</span>` : "";
  // Цена-кнопка. Пока по цене есть сомнение (ответ не пришёл, запрос в
  // пути), строка так и говорит: число может быть уже неправдой, нажатие
  // сверит его с сервером.
  const сомнение = ценаСомнительна(p.id);
  const цена = `<button type="button" class="pricetap${сомнение ? " unsure" : ""}" data-price="${p.id}"
         aria-label="${сомнение ? "Цена не подтверждена — нажмите, чтобы сверить" : "Изменить цену"}">${(+p.price).toFixed(2)} Br</button>`;
  // Действия — снизу и с подписями: «📦 Склад» и «✏️ Карточка» нужны
  // каждый день. Редкие — «снять с витрины» и «удалить» — в меню «⋯»:
  // удаление уносит остаток, историю и отзывы, и держать его в одном
  // касании от правки цены нельзя.
  const действия = `<div class="prodacts">
      <button type="button" class="actbtn" data-move="${p.id}">📦 Склад</button>
      <button type="button" class="actbtn" data-edit="${p.id}">✏️ Карточка</button>
      <button type="button" class="actbtn more" data-more="${p.id}" aria-label="Ещё: снять с витрины, удалить">⋯</button>
    </div>`;
  return `<div class="admrow prodrow${p.hidden ? " off" : ""}">
      <div class="prodtop">
        <div class="prodhead"><div class="prodname">${esc(p.name)}</div><div class="prodstock">${остаток}</div></div>
        ${цена}
      </div>
      ${снят || детали ? `<div class="prodmeta">${снят}${снят && детали ? " " : ""}${детали}</div>` : ""}
      ${действия}
    </div>`;
}

// ----- /Список товаров -----

// Меню «⋯» у строки товара: снять с витрины (или вернуть) и удалить.
// Сами действия — прежние (toggleHidden, delAdminRow с их вопросами), меню
// только убирает их с глаз из ежедневной строки.
let ещёТовар = null;
function открытьЕщё(id) {
  const p = shelf().find(x => x.id === id);
  if (!p) return;
  ещёТовар = p;
  $("rowMoreTitle").textContent = p.name;
  $("rowMoreScope").textContent = `Точка «${p.city}»`;
  $("rowMoreHide").textContent = p.hidden ? "👁 Вернуть на витрину" : "🚫 Снять с витрины";
  $("rowMoreOverlay").classList.add("show");
}
$("rowMoreHide").onclick = () => {
  const p = ещёТовар; closeOverlay($("rowMoreOverlay"));
  if (p) toggleHidden(p.id);
};
$("rowMoreDel").onclick = () => {
  const p = ещёТовар; closeOverlay($("rowMoreOverlay"));
  if (p) delAdminRow(p.id);
};
$("rowMoreCancel").onclick = () => closeOverlay($("rowMoreOverlay"));

// ----- Быстрая цена -----
// Цену меняют чаще всего остального, а раньше ради неё открывалась вся
// карточка: закупка, вкусы, точки — и её сохранение задевало и их. Теперь
// нажатие на цену в строке списка: одно поле, одна точка, и на сервер уходит
// только цена — вместе с тем, какой её видел человек. Если за это время цену
// поменял кто-то другой, сервер не затрёт его правку: окно покажет, что там
// теперь, а введённое оставит в поле.
let ценаТовар = null, ценаБыло = null;
// Номер версии цены, который видел человек (price_rev с сервера). Уходит вместе
// с ценой: сверка по одному значению не видит сохранения той же цены, и
// застрявший старый запрос прошёл бы поверх нового (QP-03). null — сервер
// номера не прислал, тогда сверка только по значению, как раньше.
let ценаРев = null;
// Номер открытия окна. Ответ сервера приходит позже и может застать уже
// другое окно: нажали «Отмена» и открыли другой товар (или тот же заново).
// Такой ответ обновляет только строку своего товара и говорит о себе
// уведомлением — новое окно, его поле и его «Сейчас» он не трогает.
let ценаОткрытие = 0;
// Запрос этого окна в пути: второе нажатие (Enter, двойной тап) ждёт ответа,
// а не уходит вторым запросом вдогонку.
let ценаИдёт = false;
// Правду о цене товара мы знаем не всегда. Запрос в пути — сервер вот-вот
// запишет новую цену (или уже записал). Ответ не пришёл, сервер упал, ответ
// оборвался на середине — цена могла сохраниться, а могла и нет. Помним это
// при ТОВАРЕ, а не при окне: закрыли окно, открыли снова — в строке всё то же
// прежнее число, а на сервере может быть уже новое. Пока сомнение не снято,
// «вписана та же цена — сохранять нечего» не говорим: только запрос со
// снимком, и сервер сам скажет, что у него. Снимает сомнение только ответ
// сервера о цене этого товара: «сохранено» или «сейчас там X».
const ценаВПути = new Map();        // товар → сколько запросов его цены в пути
const ценаНеизвестна = new Map();   // товар → какую цену пытались записать
function ценаСомнительна(id) { return (ценаВПути.get(id) || 0) > 0 || ценаНеизвестна.has(id); }
// Кто ждёт, пока у товара закончатся запросы цены в пути (см. сохранитьЦену):
// второе сохранение не уходит вдогонку первому.
const ценаЖдут = new Map();         // товар → [продолжить, …]
function ценаДождаться(id) {
  if (!((ценаВПути.get(id) || 0) > 0)) return Promise.resolve();
  return new Promise(дальше => ценаЖдут.set(id, [...(ценаЖдут.get(id) || []), дальше]));
}
const номерВерсии = (v) => (Number.isInteger(v) ? v : null);

const ценаСтрокой = (v) => (+v).toFixed(2);
const вКопейках = (v) => Math.round(+v * 100);

function открытьЦену(id) {
  const p = shelf().find(x => x.id === id);
  if (!p) return;
  ценаОткрытие++;
  ценаИдёт = false;
  ценаТовар = p; ценаБыло = +p.price; ценаРев = номерВерсии(p.price_rev);
  $("priceTitle").textContent = p.name;
  // Где действует правка — написано прямо: у каждой точки своя цена, и
  // «поменял в Минске» не должно читаться как «поменял везде».
  $("priceScope").textContent = `Только точка «${p.city}». На других точках цена своя.`;
  показатьЦенуСейчас();
  $("priceNew").value = ценаСтрокой(p.price);
  // Прошлое сохранение этого товара ещё идёт или не подтвердилось — число в
  // строке может быть уже неправдой. Человек должен видеть это сразу, а не
  // узнать после «Сохранить».
  показатьОшибкуЦены(ценаСомнительна(p.id) ? сомнениеВЦене(p.id) : "");
  $("priceSave").disabled = false; $("priceSave").textContent = "Сохранить цену";
  $("priceOverlay").classList.add("show");
  setTimeout(() => { try { $("priceNew").focus(); $("priceNew").select(); } catch (e) {} }, 60);
}

function сомнениеВЦене(id) {
  const пробовали = ценаНеизвестна.get(id);
  if ((ценаВПути.get(id) || 0) > 0) {
    return "Прошлое сохранение цены ещё идёт. «Сохранить цену» дождётся ответа на него и сверит цену с сервером.";
  }
  const что = `Прошлое сохранение${пробовали === undefined ? "" : ` (${ценаСтрокой(пробовали)} Br)`} не подтвердилось`;
  return `${что} — на сервере цена могла уже поменяться. «Сохранить цену» сначала сверит её с сервером.`;
}

function показатьЦенуСейчас() {
  $("priceNow").innerHTML = `Сейчас: <b>${ценаСтрокой(ценаБыло)} Br</b>`;
}

function показатьОшибкуЦены(текст) {
  $("priceMsg").textContent = текст;
  $("priceMsg").style.display = текст ? "" : "none";
}

// Цена поменялась — только в этом товаре и только у себя: перечитывать ради
// одной цифры весь каталог незачем. Список перерисовывается в конце
// сохранения — с тем же поиском, фильтрами и прокруткой.
function поставитьЦенуЛокально(id, цена, рев) {
  [adminProducts, allProducts].forEach(список => (список || []).forEach(x => {
    if (x.id !== id) return;
    x.price = цена;
    if (номерВерсии(рев) !== null) x.price_rev = рев;
  }));
}

// Ответ, по которому нельзя сказать, записана ли цена: сервер упал, тело
// ответа не дочиталось, в ответе нет ни «сохранено», ни причины отказа.
// Это не «не сохранилось» — сервер мог уже записать новую цену.
class НеизвестныйИсход extends Error {}

async function сохранитьЦену() {
  const p = ценаТовар;
  if (!p || ценаИдёт) return;
  if ((ценаВПути.get(p.id) || 0) > 0) {
    // Прошлое сохранение этой цены ещё в пути: окно закрыли и открыли снова,
    // пока запрос шёл. Второй запрос вдогонку сервер мог бы обработать раньше
    // первого — и тогда победила бы старая цена (QP-03). Ждём ответа на
    // первый; введённое остаётся в поле, другие товары не ждут. Потом —
    // обычное сохранение того, что к тому времени в поле, уже с правдой о
    // первом запросе.
    const моё = ценаОткрытие, btn = $("priceSave");
    ценаИдёт = true;
    btn.disabled = true; btn.textContent = "Жду прошлое сохранение…";
    await ценаДождаться(p.id);
    // Пока ждали, окно закрыли или открыли заново — ничего не шлём и ничего
    // не трогаем: кнопка и флаг теперь у нового окна.
    if (моё !== ценаОткрытие || !$("priceOverlay").classList.contains("show")) return;
    ценаИдёт = false;
    btn.disabled = false; btn.textContent = "Сохранить цену";
    return сохранитьЦену();
  }
  const n = Number(String($("priceNew").value || "").replace(",", ".").replace(/\s/g, ""));
  if (!isFinite(n) || n <= 0) { показатьОшибкуЦены("Цена — число больше нуля, например 18.5."); return; }
  const новая = Math.round(n * 100) / 100;
  // «Та же цена — сохранять нечего» верно, только пока мы точно знаем, что на
  // сервере: по товару нет сомнений, и строка списка не менялась с тех пор,
  // как открыли окно (её мог обновить ответ прошлого нажатия).
  // Номер версии тоже: ту же цену могли сохранить заново — число прежнее, а
  // снимок уже устарел.
  const строка = shelf().find(x => x.id === p.id) || p;
  const точно = !ценаСомнительна(p.id) && вКопейках(строка.price) === вКопейках(ценаБыло)
    && номерВерсии(строка.price_rev) === ценаРев;
  if (точно && вКопейках(новая) === вКопейках(ценаБыло)) {
    closeOverlay($("priceOverlay"));
    toast("Цена та же — сохранять нечего");
    return;
  }
  const было = ценаБыло, рев = ценаРев, наугад = !точно, моё = ценаОткрытие;
  // Своё ли ещё окно: пока шёл запрос, его могли закрыть (кнопкой, свайпом,
  // тапом мимо) или открыть заново — для другого товара или для этого же.
  const своё = () => моё === ценаОткрытие && $("priceOverlay").classList.contains("show");
  ценаИдёт = true;
  ценаВПути.set(p.id, (ценаВПути.get(p.id) || 0) + 1);
  const btn = $("priceSave");
  btn.disabled = true; btn.textContent = "Сохраняю…";
  try {
    const r = await fetch("/api/admin/product/update", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id: p.id, fields: { price: новая },
                             expected: рев === null ? { price: было } : { price: было, price_rev: рев } }) });
    // 5xx — сервер споткнулся, исход неизвестен: то же, что обрыв сети.
    if (r.status >= 500) throw new НеизвестныйИсход("Сервер ответил ошибкой.");
    // Заголовки и тело ответа приходят отдельно, и связь может оборваться
    // между ними. Недочитанный ответ — не «не сохранилось», а «не знаем».
    let d;
    try { d = await r.json(); } catch (e) {
      throw new НеизвестныйИсход(e && e.name === "AbortError" ? "Сервер не ответил вовремя." : "Ответ сервера оборвался.");
    }
    if (!d || typeof d !== "object") throw new НеизвестныйИсход("Ответ сервера не разобрать.");
    if (d.ok !== true) {
      // Отказ — только когда сервер назвал причину: тогда он точно ничего не
      // записал. Сомнение от прошлых нажатий этот ответ не снимает — о том,
      // что сейчас на сервере, он ничего не говорит.
      if (!d.error && !d.message) throw new НеизвестныйИсход("Ответ сервера не разобрать.");
      const текст = d.message || ОТКАЗЫ[d.error] || "Цена не сохранилась — попробуйте ещё раз.";
      if (своё()) показатьОшибкуЦены(текст); else toast(`«${p.name}»: ${текст}`);
      return;
    }
    const отказ = (d.failed || {}).price;
    if (отказ && отказ.error === "conflict") {
      const сейчас = Number(отказ.current);
      if (отказ.current === null || отказ.current === undefined || !isFinite(сейчас)) {
        throw new НеизвестныйИсход("Ответ сервера не разобрать.");
      }
      // Сервер сказал, что у него: сомнение снято. Строка своего товара —
      // всегда по правде сервера, чьё бы окно ни было открыто.
      ценаНеизвестна.delete(p.id);
      const сейчасРев = номерВерсии(отказ.current_rev);
      поставитьЦенуЛокально(p.id, сейчас, сейчасРев);
      if (вКопейках(сейчас) === вКопейках(новая)) {
        // Там уже ровно то, что человек вписал: чаще всего это его же прошлое
        // нажатие, ответ на которое потерялся в сети.
        if (своё()) closeOverlay($("priceOverlay"));
        toast(`«${p.name}»: цена уже ${ценаСтрокой(новая)} Br`);
        return;
      }
      if (!своё()) {
        toast(`«${p.name}»: цена не сохранилась — её уже поменяли, сейчас ${ценаСтрокой(сейчас)} Br`);
        return;
      }
      ценаБыло = сейчас; ценаРев = сейчасРев;
      показатьЦенуСейчас();
      // Число то же, а номер другой — цену за это время сохраняли заново.
      const пересохранили = вКопейках(сейчас) === вКопейках(было);
      показатьОшибкуЦены((наугад
        ? `На сервере сейчас ${ценаСтрокой(сейчас)} Br — похоже, дошло прошлое нажатие. `
        : пересохранили
          ? `Пока окно было открыто, цену уже сохраняли: сейчас ${ценаСтрокой(сейчас)} Br. `
          : `Пока окно было открыто, цену уже поменяли: сейчас ${ценаСтрокой(сейчас)} Br. `)
        + `Нажмите «Сохранить цену» ещё раз, если нужно ${ценаСтрокой(новая)} Br.`);
      return;
    }
    if (отказ) {
      const текст = отказ.message || ОТКАЗЫ[отказ.error] || "Цена не сохранилась.";
      if (своё()) показатьОшибкуЦены(текст); else toast(`«${p.name}»: ${текст}`);
      return;
    }
    // Успех — только если сервер прямо назвал цену сохранённой. «ok» без
    // этого — неполный ответ, и показать по нему новую цену значило бы
    // показать то, чего на сервере может не быть.
    if (!Array.isArray(d.saved) || !d.saved.includes("price")) throw new НеизвестныйИсход("Ответ сервера не разобрать.");
    ценаНеизвестна.delete(p.id);
    поставитьЦенуЛокально(p.id, новая, d.price_rev);
    if (своё()) closeOverlay($("priceOverlay"));
    toast(`«${p.name}» · ${p.city}: ${ценаСтрокой(было)} → ${ценаСтрокой(новая)} Br`);
    // Фоном — сверить остальное с сервером (витрина покупателя, чужие правки).
    Promise.resolve(refreshProducts()).catch(() => {});
  } catch (e) {
    // Исход неизвестен: цена могла и сохраниться. Помним это при товаре —
    // следующее «Сохранить» по нему (в этом окне или открытом заново)
    // спросит сервер со снимком, а строка списка покажет, что цена не
    // подтверждена.
    ценаНеизвестна.set(p.id, новая);
    const что = e instanceof НеизвестныйИсход ? e.message : текстСбоя(e);
    if (своё()) {
      показатьОшибкуЦены(что + " Цена могла сохраниться — нажмите «Сохранить цену» ещё раз, приложение проверит.");
    } else {
      toast(`«${p.name}»: ответ не пришёл — цена могла сохраниться. Нажмите на цену в строке, чтобы сверить.`);
    }
  } finally {
    const вПути = (ценаВПути.get(p.id) || 1) - 1;
    if (вПути > 0) ценаВПути.set(p.id, вПути); else ценаВПути.delete(p.id);
    // Кнопку и флаг трогаем, только если окно всё ещё наше: у нового окна
    // они свои, и старый запрос не должен их ни занимать, ни освобождать.
    if (моё === ценаОткрытие) { ценаИдёт = false; btn.disabled = false; btn.textContent = "Сохранить цену"; }
    // Перерисовка — после того как запрос снят с учёта «в пути»: иначе
    // строка так и осталась бы с пометкой «не подтверждена».
    renderAdminList();
    // Запросов цены у товара больше нет — ждавшие его сохранения идут дальше.
    if (!ценаВПути.has(p.id)) {
      const ждут = ценаЖдут.get(p.id) || [];
      ценаЖдут.delete(p.id);
      ждут.forEach(дальше => дальше());
    }
  }
}

$("priceSave").onclick = сохранитьЦену;
// «Отмена» закрывает окно и отвязывает от него запрос в пути: его ответ
// обновит строку товара, но не это окно (см. своё() выше).
$("priceCancel").onclick = () => { ценаОткрытие++; closeOverlay($("priceOverlay")); };
$("priceNew").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); сохранитьЦену(); } };
// ----- /Быстрая цена -----

// ----- Редактор товара -----
// Остаток в карточке не правится вовсе — ни числом, ни списком вкусов с
// числами. Раньше правился, и любое сохранение (даже одной цены) могло
// вернуть на полку то, что купили, пока форма была открыта; а само изменение
// шло мимо истории склада. Теперь число меняет только «📦 Склад», а карточка
// показывает его и ведёт туда.
//
// editOrig* — поля НА МОМЕНТ ОТКРЫТИЯ формы. Шлём только то, что человек
// правда поменял, и вместе со снимком: если поле за это время поменял кто-то
// другой, сервер не перезапишет чужую правку, а скажет о ней.
let editId = null, editVariants = [], editPhotoFile = null, editCategory = null;
// Состав вариантов меняется отдельно от их остатков: новые — с первым
// приходом, убираемые — отметкой. Сохраняются вместе с карточкой.
let editAdds = [], editRemoves = new Set();
let editOrigPrice = null, editOrigCost = null, editOrigHit = null, editOrigPoints = null;
// Номер версии цены при открытии карточки (см. ценаРев в быстрой цене): с ним
// сервер не пропустит застрявшее сохранение поверх более нового.
let editOrigPriceRev = null;
// Только у товара без модели: там правятся название, категория, точка,
// бренд, вкус и описание — у товара из «Ассортимента» этих полей в форме нет.
let editOrigName = null, editOrigCat = null, editOrigCity = null, editOrigDesc = null,
    editOrigBrand = null, editOrigFlavor = null;

// Правда, только если что-то реально изменили с момента открытия формы.
// Раньше «Закрыть» и «Открыть модель» уходили молча — набранная цена, вкус
// или заполненная вторая точка терялись без единого предупреждения, и это
// не было заметно, пока не открывал карточку заново.
function формаИзменена() {
  if (!editId) return false;
  // Раньше брали точкиТовар — а её выставляет только renderEditPoints(), то
  // есть только у товара с моделью. У товара без модели точкиТовар оставалась
  // null (или вообще от ранее открытой чужой карточки), и проверка выходила
  // ещё до сравнения цены: закрытие с несохранённой правкой уходило молча.
  const p = shelf().find(x => x.id === editId);
  if (!p) return false;
  if ($("edPrice") && Number(String($("edPrice").value).replace(",", ".")) !== Number(editOrigPrice)) return true;
  if ($("edCost") && ($("edCost").value || "") !== String(editOrigCost)) return true;
  if ($("edHit") && $("edHit").checked !== editOrigHit) return true;
  if (editAdds.length || editRemoves.size) return true;
  // Поля, которые есть только в форме товара без модели.
  if ($("edName") && $("edName").value.trim() !== editOrigName) return true;
  if ($("edCat") && $("edCat").value !== editOrigCat) return true;
  if ($("edCity") && $("edCity").value !== editOrigCity) return true;
  if ($("edDesc") && $("edDesc").value.trim() !== editOrigDesc) return true;
  if ($("edBrand") && pickerValue("edBrand") !== editOrigBrand) return true;
  if ($("edFlavor") && pickerValue("edFlavor") !== editOrigFlavor) return true;
  if (editPhotoFile) return true;
  if (editOrigPoints !== null && JSON.stringify(снятьЧерновикТочек()) !== editOrigPoints) return true;
  return false;
}

// Общее место для любого выхода из редактора: спросить, если есть что терять.
function закрытьРедактор(закрыть) {
  if (!формаИзменена()) { закрыть(); return; }
  confirmMsg("Есть несохранённые изменения. Закрыть без сохранения?", закрыть);
}

$("editClose").onclick = () => закрытьРедактор(() => $("editView").classList.remove("show"));

function openEdit(id) {
  const p = shelf().find(x => x.id === id); if (!p) return;
  editId = id;
  editCategory = p.category;
  editVariants = (p.variants || []).map(v => ({ flavor: v.flavor, stock: v.stock, reserved: v.reserved || 0 }));
  editAdds = []; editRemoves = new Set();
  editOrigPrice = p.price; editOrigCost = p.cost || ""; editOrigHit = !!p.is_hit;
  editOrigPriceRev = номерВерсии(p.price_rev);
  editOrigName = p.name; editOrigCat = p.category; editOrigCity = p.city;
  editOrigDesc = p.description || ""; editOrigBrand = p.brand || ""; editOrigFlavor = p.flavor || "";
  editPhotoFile = null;
  // id > 0 — только дополнительные: главное фото меняется отдельным полем выше.
  editPhotos = (p.photos || []).filter(g => g.id);
  renderEdit(p);
  // Снимок блока точек — уже ПОСЛЕ того, как renderEdit его отрисовал.
  editOrigPoints = $("edPoints") ? JSON.stringify(снятьЧерновикТочек()) : null;
  $("editView").classList.add("show");
}

// Общий блок замены фото — превью + выбор файла (для любого товара).
function editPhotoBlock(p) {
  // /api/admin/photo — owner-only на сервере (фото витрины общее для всех
  // точек). Продавцу показывать поле загрузки незачем — нажатие «Сохранить»
  // молча отказало бы именно в этой части, а остальные поля бы сохранились.
  if (!isOwner()) {
    return p.photo_url
      ? `<label>Главное фото</label><img alt="" src="${thumbOf(p)}" style="max-width:120px;border-radius:10px;display:block">`
      : "";
  }
  return `<label>Главное фото</label>
    <div class="edphoto">
      <img id="edPhotoPrev" alt="" src="${thumbOf(p) || ''}" ${p.photo_url ? '' : 'style="display:none"'}>
      <input type="file" id="edPhoto" accept="image/*">
    </div>
`;
}
// Остаток в карточке — только для чтения, с дверью в склад. Раньше здесь было
// поле с числом, и сохранение карточки ставило его поверх склада: чужая
// продажа, случившаяся пока форма открыта, возвращалась на полку, а само
// изменение шло мимо истории. Склад открывается поверх карточки — введённое
// в карточке никуда не девается.
function остатокВКарточке(p) {
  return `<label style="margin-top:14px">Остаток</label>
    <div class="dnote" id="edStockLine" style="margin:0 0 6px">${строкаОстатка(p)}</div>
    <button type="button" class="closebtn" id="edStockOps">📦 Приход, списание, пересчёт</button>`;
}
function строкаОстатка(p) {
  return `свободно <b>${p.stock}</b> шт` + (p.reserved ? ` · ещё ${p.reserved} в невыданных заказах` : "")
    + (hasVariants(p) ? ` · по вариантам — в окне склада` : "");
}
function bindОстатокВКарточке(p) {
  if ($("edStockOps")) $("edStockOps").onclick = () => openStockMove(p.id);
}
// Окно склада закрылось поверх открытой карточки — числа в карточке устарели.
// Обновляем только их: набранные цена, вкусы и точки остаются как были.
function обновитьОстатокВКарточке() {
  if (!editId || !$("editView").classList.contains("show")) return;
  const p = shelf().find(x => x.id === editId);
  if (!p) return;
  const свежие = new Map((p.variants || []).map(v => [v.flavor, v]));
  editVariants = editVariants.map(v => свежие.has(v.flavor)
    ? { ...v, stock: свежие.get(v.flavor).stock, reserved: свежие.get(v.flavor).reserved || 0 } : v);
  if ($("edStockLine")) $("edStockLine").innerHTML = строкаОстатка(p);
  if ($("edVarList")) renderEditVariants();
}

function editHitBlock(p) {
  return `<div class="chk" style="margin-top:12px"><input type="checkbox" id="edHit" ${p.is_hit ? 'checked' : ''}>
    <label for="edHit" style="margin:0">🔥 Отметить как «Хит»</label></div>`;
}
// Навесить превью выбранного файла (общее для обеих веток редактора).
function bindEditPhoto() {
  const inp = $("edPhoto"); if (!inp) return;
  inp.onchange = () => {
    const f = inp.files[0]; editPhotoFile = f || null;
    const prev = $("edPhotoPrev");
    if (f && prev) { prev.src = URL.createObjectURL(f); prev.style.display = ""; }
  };
}

// ----- Галерея товара в редакторе -----
// Дополнительные фото сохраняются сразу, а не по кнопке «Сохранить»: у них нет
// полей, которые можно передумать заполнять, а ждать общего сохранения ради
// картинки — лишний шаг, на котором её теряют.
const MAX_EXTRA_PHOTOS = 5;
let editPhotos = [];
function renderEditGallery() {
  const box = $("mdGal"); if (!box) return;
  box.innerHTML = editPhotos.map(g =>
      `<div class="g"><img src="${g.thumb || g.url}" alt=""><button data-gdel="${g.id}" title="Убрать">✕</button></div>`).join("")
    + (editPhotos.length < MAX_EXTRA_PHOTOS
        ? `<label class="add">＋<input type="file" id="mdGalAdd" accept="image/*" multiple style="display:none"></label>`
        : `<div style="color:var(--hint);font-size:12px;align-self:center">Больше ${MAX_EXTRA_PHOTOS} — уже долгая загрузка у покупателя</div>`);
  box.querySelectorAll("[data-gdel]").forEach(b => b.onclick = () => delEditPhoto(+b.dataset.gdel));
  const add = $("mdGalAdd");
  if (add) add.onchange = () => addEditPhotos([...add.files]);
}
async function addEditPhotos(files) {
  const box = $("mdGal");
  for (const f of files) {
    if (editPhotos.length >= MAX_EXTRA_PHOTOS) { alertMsg(`Больше ${MAX_EXTRA_PHOTOS} дополнительных фото не нужно.`); break; }
    if (box) box.insertAdjacentHTML("beforeend", `<div class="g" id="gLoading"><img src="${URL.createObjectURL(f)}" alt="" style="opacity:.45"></div>`);
    try {
      const fd = new FormData();
      fd.append("initData", initData); fd.append("model_id", editingModelId); fd.append("file", f);
      const r = await fetch("/api/admin/photo/add", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) editPhotos.push({ id: d.photo_id, url: URL.createObjectURL(f) });
      else alertMsg(d.error === "too_many" ? `Больше ${MAX_EXTRA_PHOTOS} фото не нужно.` : "Фото не загрузилось.");
    } catch (e) { alertMsg(текстСбоя(e)); }
    const tmp = $("gLoading"); if (tmp) tmp.remove();
  }
  renderEditGallery();
  refreshProducts();     // витрина должна увидеть новые фото сразу
}
function delEditPhoto(photoId) {
  confirmMsg("Убрать это фото?", async () => {
    try {
      const r = await fetch("/api/admin/photo/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, photo_id: photoId }) });
      const d = await r.json();
      if (!d.ok) { alertMsg("Не удалось убрать."); return; }
      editPhotos = editPhotos.filter(g => g.id !== photoId);
      renderEditGallery();
      refreshProducts();
    } catch (e) { alertMsg(текстСбоя(e)); }
  });
}

function renderEdit(p) {
  const isVar = hasVariants(p);
  const catOptions = CAT_OPTS.map(([c, n]) => `<option value="${c}" ${p.category === c ? 'selected' : ''}>${n}</option>`).join("");
  const cityOptions = locations.map(l => `<option value="${esc(l.name)}" ${p.city === l.name ? 'selected' : ''}>${esc(l.name)}</option>`).join("");

  // ---- Товар, заведённый из ассортимента: тут только цена и остаток ----
  // Название, бренд, характеристики и вкусы — свойства модели: они одинаковы
  // на всех точках, и править их здесь значило бы разводить копии.
  if (p.model_id) {
    const md = models.find(m => m.id === p.model_id);
    $("editBody").innerHTML = `
      <div class="card-block form">
        <div style="font-weight:800">${esc(p.name)}</div>
        <div class="csub" style="margin-top:4px">${esc(p.brand || "")} · ${catName(p.category)} · ${esc(p.city)}</div>
        <div class="rowf">
          <div><label>Цена (Br)</label><input id="edPrice" inputmode="decimal" value="${p.price}"></div>
          <div><label>Закупка (Br)</label><input id="edCost" inputmode="decimal" value="${p.cost || ""}"></div>
        </div>
        ${isVar ? `<label>${esc(catVariantMany(p.category))} и остаток</label><div id="edVarList"></div>
          ${variantAddRowHtml(p.category, md ? md.flavors : [])}`
          : ""}
        ${остатокВКарточке(p)}
        ${editHitBlock(p)}
        <label style="margin-top:18px">Точки продаж</label>
        <div id="edPoints"></div>
        <div class="dnote" style="margin-top:12px">Название, характеристики, вкусы и фото — в «Ассортименте»: там они правятся сразу для всех точек.</div>
        <button class="closebtn" id="edToModel" style="margin-top:6px">📚 Открыть модель</button>
        <button class="bigbtn" id="edSave" style="margin-top:10px">Сохранить</button>
      </div>`;
    if (isVar) { renderEditVariants(); bindVariantAdd(p.category); }
    bindQty($("editView"));
    bindОстатокВКарточке(p);
    renderEditPoints(p, md);
    $("edToModel").onclick = () => закрытьРедактор(() => {
      $("editView").classList.remove("show");
      openModels().then(() => editModel(p.model_id));
    });
    $("edSave").onclick = () => saveEdit(p);
    return;
  }

  if (!isVar) {
    // ---- Товар без модели (заведён до «Ассортимента»): поля правятся вручную ----
    $("editBody").innerHTML = `
      <div class="card-block form">
        <div class="csub">${catName(p.category)} · ${esc(p.city)}</div>
        <label>Категория</label><select id="edCat">${catOptions}</select>
        <label>Точка (город)</label><select id="edCity">${cityOptions}</select>
        <label>Название</label><input id="edName" value="${esc(p.name)}">
        <div class="rowf">
          <div><label>Цена (Br)</label><input id="edPrice" inputmode="decimal" value="${p.price}"></div>
          <div><label>Закупка (Br)</label><input id="edCost" inputmode="decimal" value="${p.cost || ""}"></div>
        </div>
        ${остатокВКарточке(p)}
        <label>Бренд</label>${pickerHtml("edBrand", p.brand || "", brandNames(p.category), "+ Новый бренд…")}
        <label>Вкус (если есть)</label>${pickerHtml("edFlavor", p.flavor || "", knownFlavors, "+ Новый вкус…")}
        <div id="edSpecs">${specFieldsHtml(p.category, p.specs, "eds_")}</div>
        <label>Описание</label><input id="edDesc" value="${esc(p.description || '')}">
        ${editHitBlock(p)}
        ${editPhotoBlock(p)}
        ${toModelBlock()}
        <button class="bigbtn" id="edSave" style="margin-top:16px">Сохранить</button>
      </div>`;
    bindEditPhoto(); renderEditGallery();
    bindОстатокВКарточке(p);
    // Товар без модели: кнопка «Сделать моделью» — единственный путь к точкам.
    if ($("edToModelNew")) $("edToModelNew").onclick = () => сделатьМоделью(p);
    bindPicker("edBrand"); bindPicker("edFlavor");
    $("edSave").onclick = () => saveEdit(p);
    return;
  }

  // ---- Товар-модель со вкусами ----
  const specs = `<div id="edSpecs">${specFieldsHtml(p.category, p.specs, "eds_")}</div>`;
  // Бренд ищем по имени: у общего бренда категория пустая, и прежнее условие
  // «имя + категория» его не находило — список вкусов молча пустел.
  const brandObj = brands.find(b => b.name === p.brand);
  const avail = brandObj ? brandObj.flavors : [];
  $("editBody").innerHTML = `
    <div class="card-block form">
      <div style="font-weight:800">${esc(p.name)}</div>
      <div class="csub" style="margin-top:4px">${esc(p.brand || '')} · ${catName(p.category)} · ${esc(p.city)}</div>
      <label>Точка (город)</label><select id="edCity">${cityOptions}</select>
      <label>Цена (Br)</label><input id="edPrice" inputmode="decimal" value="${p.price}">
      <label>Закупочная цена (Br)</label><input id="edCost" inputmode="decimal" value="${p.cost || ""}">
      ${specs}
      <label>${esc(catVariantMany(p.category))} и остаток</label>
      <div id="edVarList"></div>
      ${variantAddRowHtml(p.category, avail)}
      ${остатокВКарточке(p)}
      ${editHitBlock(p)}
      ${editPhotoBlock(p)}
      ${toModelBlock()}
      <button class="bigbtn" id="edSave" style="margin-top:16px">Сохранить</button>
    </div>`;
  renderEditVariants();
  bindVariantAdd(p.category);
  bindEditPhoto(); renderEditGallery();
  bindQty($("editView"));
  bindОстатокВКарточке(p);
  // Товар без модели: кнопка «Сделать моделью» — единственный путь к точкам.
  if ($("edToModelNew")) $("edToModelNew").onclick = () => сделатьМоделью(p);
  $("edSave").onclick = () => saveEdit(p);
}

// ----- Точки продаж прямо в карточке товара -----
// Один товар живёт на нескольких точках: в базе это «модель», а на каждой точке
// своя запись со своей ценой и своим остатком. Механизм был, но добраться до
// него можно было только через «Ассортимент» — и казалось, что для второго
// города надо заводить товар заново.
//
// Галочка отвечает на вопрос «есть ли этот товар на точке». Снять её — значит
// убрать товар с точки, и это делается по-настоящему, с подтверждением: иначе
// галочка врала бы, а вранью в интерфейсе цена — доверие ко всему остальному.
let editPointFlavors = {};     // город -> [вкусы], выбранные для этой точки

function editPointList() {
  // Вкусы берём ИЗ КАРТОЧКИ, прямо с экрана, а не из сохранённой модели.
  // Три причины, и все три — найденные грабли:
  //
  //  • добавил вкус вверху — он тут же виден внизу, а не после сохранения;
  //  • верх и низ экрана говорят об одном товаре одинаково;
  //  • своя кнопка «Добавить вкус» в блоке становится не нужна — а третье
  //    место, где заводят вкусы, это ровно то, из-за чего списки разошлись.
  return [...editVariants.filter(v => !editRemoves.has(v.flavor)).map(v => v.flavor), ...editAdds.map(a => a.flavor)]
    .map(f => String(f || "").trim()).filter(Boolean);
}

let точкиТовар = null, точкиМодель = null;

// Перерисовать блок точек — например, когда в карточке поменяли вкусы.
// Отдельная функция, потому что зовут её из renderEditVariants, а тот про
// товар и модель ничего не знает.
function обновитьБлокТочек() {
  if (точкиТовар) renderEditPoints(точкиТовар, точкиМодель);
}

// Товар без модели продавать на нескольких точках нельзя: точки держатся на
// модели. Раньше это никак не объяснялось — блока точек просто не было, и
// владелец заводил товар в другом городе заново, руками.
function toModelBlock() {
  // Заводит запись в ОБЩЕМ ассортименте (то же самое, что и «Ассортимент» в
  // хабе «Управление») — сервер это тоже проверяет (owner-only), но кнопка,
  // на которую продавец жмёт и получает молчаливый отказ, — плохой экран
  // сама по себе. Продавцу просто объясняем, что делать в этом случае.
  if (!isOwner()) {
    return `<div style="border-top:1px solid var(--line);margin:20px 0 0"></div>
      <label style="margin-top:16px">Точки продаж</label>
      <div class="dnote" style="margin:0 0 10px">Этот товар заведён без модели — на других точках его заводит владелец через «Ассортимент».</div>`;
  }
  return `<div style="border-top:1px solid var(--line);margin:20px 0 0"></div>
    <label style="margin-top:16px">Точки продаж</label>
    <div class="dnote" style="margin:0 0 10px">Этот товар заведён без модели, поэтому живёт только на одной точке. Модель — это описание товара, общее для всех городов; из неё он и добавляется куда угодно.</div>
    <button class="closebtn" id="edToModelNew">📚 Сделать моделью</button>`;
}

// Такая же модель уже есть — новую не заводим: спрашиваем, привязать ли
// товар к ней. Похожие, но не такие же, приложение само не сливает никогда.
async function сделатьМоделью(p, привязатьК) {
  let d = null;
  try {
    const r = await fetch("/api/admin/product/to-model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id: p.id, link_to: привязатьК || null }) });
    d = await r.json().catch(() => null);
    if (!d) { alertMsg("Ответ сервера не дошёл. Обновите список: товар мог уже получить модель."); return; }
  } catch (e) { alertMsg(текстСбоя(e)); return; }
  if (d.error === "exists" && d.model_id) {
    confirmMsg(`${d.message}\n\nПривязать этот товар к ней? Название, описание и фото станут как у модели. ` +
               "Цена, остаток, история склада, заказы и отзывы товара сохранятся.", () => сделатьМоделью(p, d.model_id));
    return;
  }
  if (!d.ok) { alertMsg(d.message || "Не удалось сделать моделью."); return; }
  await Promise.all([refreshProducts(), fetchModels()]);   // независимы — не ждём по очереди
  // Открываем карточку заново: теперь у товара есть модель, и в ней появится
  // блок точек. Показать это сразу важнее, чем сэкономить одну перерисовку.
  openEdit(p.id);
  const ещё = [];
  if (d.photos_moved) ещё.push(`доп. фото в галерее модели: ${d.photos_moved}`);
  if (d.photos_left) ещё.push(`не поместилось фото: ${d.photos_left} (в галерее модели до ${MAX_EXTRA_PHOTOS})`);
  if (d.reviews) ещё.push(`отзывы товара теперь у модели: ${d.reviews}`);
  if (d.added_flavors && d.added_flavors.length) ещё.push(`в модель добавлены варианты: ${d.added_flavors.join(", ")}`);
  alertMsg((d.linked ? "Готово ✅\n\nТовар привязан к модели из «Ассортимента»." : "Готово ✅\n\nОписание уехало в «Ассортимент».") +
           " Ниже появились точки продаж." + (ещё.length ? "\n\n" + ещё.join("\n") : ""));
}

// Снимок того, что уже введено в блоке точек, — ДО того как его перерисуют.
// Добавление вкуса дёргает обновитьБлокТочек (список вариантов внизу должен
// совпадать со списком наверху), а перерисовка раньше просто стирала галочки,
// цену/закупку и введённые количества: человек отмечал точку, вводил цену —
// добавил ещё вкус — и всё введённое молча пропадало, сохранить это было
// нельзя, даже не заметив пропажи.
function снятьЧерновикТочек() {
  const узел = $("edPoints");
  const черновик = {};
  if (!узел) return черновик;
  узел.querySelectorAll(".pointadd").forEach(блок => {
    const флаги = {};
    блок.querySelectorAll("[data-flavor]").forEach(строка => {
      флаги[строка.dataset.flavor] = {
        checked: строка.querySelector(".pfchk").checked,
        stock: строка.querySelector(".pfst").value,
      };
    });
    const поле = блок.querySelector(".pstock");
    черновик[блок.dataset.city] = {
      checked: блок.querySelector(".pchk").checked,
      price: блок.querySelector(".pprice").value,
      cost: блок.querySelector(".pcost").value,
      stock: поле ? поле.value : null,
      flavors: флаги,
    };
  });
  // Точки, где товар УЖЕ есть: там правится только «оставить/убрать» —
  // снятая галочка помечает точку на удаление (см. собратьТочки). Раньше
  // черновик хранил только .pointadd (новые точки), и добавление вкуса
  // (которое зовёт обновитьБлокТочек → renderEditPoints → свежая разметка
  // с checked по умолчанию) молча возвращало снятую галочку обратно.
  узел.querySelectorAll(".pointrow[data-have]").forEach(блок => {
    const чек = блок.querySelector(".pchk");
    if (чек.disabled) return;   // своя точка — переключать нечего, всегда отмечена
    черновик["have:" + блок.dataset.city] = { checked: чек.checked };
  });
  return черновик;
}

// Возвращает то, что было в снимке, поверх свежей разметки. Новый вкус,
// которого в снимке ещё не было, остаётся с тем же значением по умолчанию
// (отмечен, 0) — восстанавливать там нечего.
function применитьЧерновикТочек(черновик) {
  const узел = $("edPoints");
  if (!узел) return;
  узел.querySelectorAll(".pointadd").forEach(блок => {
    const сохранено = черновик[блок.dataset.city];
    if (!сохранено) return;
    const чек = блок.querySelector(".pchk");
    чек.checked = сохранено.checked;
    блок.querySelector(".pbody").style.display = сохранено.checked ? "" : "none";
    блок.querySelector(".pprice").value = сохранено.price;
    блок.querySelector(".pcost").value = сохранено.cost;
    if (сохранено.checked) renderPointFlavors(блок, блок.dataset.city);
    const поле = блок.querySelector(".pstock");
    if (поле && сохранено.stock !== null) поле.value = сохранено.stock;
    блок.querySelectorAll("[data-flavor]").forEach(строка => {
      const ф = сохранено.flavors[строка.dataset.flavor];
      if (!ф) return;
      строка.querySelector(".pfchk").checked = ф.checked;
      строка.querySelector(".pfst").value = ф.stock;
    });
  });
  узел.querySelectorAll(".pointrow[data-have]").forEach(блок => {
    const чек = блок.querySelector(".pchk");
    if (чек.disabled) return;
    const сохранено = черновик["have:" + блок.dataset.city];
    if (сохранено) чек.checked = сохранено.checked;
  });
}

function renderEditPoints(p, md) {
  точкиТовар = p; точкиМодель = md;
  const узел = $("edPoints");
  if (!узел) return;
  const черновик = снятьЧерновикТочек();
  const мой = myScope();
  const вкусы = editPointList();
  editPointFlavors = {};

  const где = {};
  shelf().filter(x => x.model_id === p.model_id).forEach(x => { где[x.city] = x; });
  const города = locations.map(l => l.name).filter(имя => !мой || имя === мой);

  узел.innerHTML = города.map(имя => {
    const уже = где[имя];
    const свой = уже && уже.id === p.id;
    if (уже) {
      return `<div class="pointrow" data-city="${esc(имя)}" data-have="${уже.id}">
        <label class="an" style="display:flex;gap:8px;align-items:center;font-weight:600">
          <input type="checkbox" class="pchk" style="width:auto" checked ${свой ? "disabled" : ""}>
          Есть на точке «${esc(имя)}»</label>
        <div class="dnote" style="margin:6px 0 0">${свой
          ? "эта карточка" + (p.hidden ? " · 🚫 снят с витрины" : "")
          : `${(+уже.price).toFixed(2)} Br · ${уже.stock} шт`
            + (уже.hidden ? " · 🚫 снят с витрины" : "")
            + ` · <a data-gopoint="${уже.id}">открыть</a>`}</div>
      </div>`;
    }
    return `<div class="pointrow pointadd" data-city="${esc(имя)}">
      <label class="an" style="display:flex;gap:8px;align-items:center;font-weight:600">
        <input type="checkbox" class="pchk" style="width:auto"> Есть на точке «${esc(имя)}»</label>
      <div class="pbody" style="display:none">
        <div class="rowf">
          <div><label>Цена (Br)</label><input class="pprice" inputmode="decimal" value="${p.price}"></div>
          <div><label>Закупка (Br)</label><input class="pcost" inputmode="decimal" value="${p.cost || ""}"></div>
        </div>
        ${вкусы.length
          ? `<label>Какие варианты есть на точке «${esc(имя)}»</label><div class="pflavors"></div>`
          : `<label>Остаток (шт.)</label>${qtyHtml(0, 'class="pstock"')}`}
      </div></div>`;
  }).join("") || `<p style="color:var(--hint)">Точек продаж пока нет.</p>`;

  города.forEach(имя => { if (!где[имя]) editPointFlavors[имя] = [...вкусы]; });

  узел.querySelectorAll(".pointadd").forEach(блок => {
    const город = блок.dataset.city;
    const чек = блок.querySelector(".pchk");
    чек.onchange = () => {
      блок.querySelector(".pbody").style.display = чек.checked ? "" : "none";
      if (чек.checked) renderPointFlavors(блок, город);
    };
  });
  применитьЧерновикТочек(черновик);

  узел.querySelectorAll("[data-gopoint]").forEach(b => b.onclick = () => закрытьРедактор(() => openEdit(+b.dataset.gopoint)));
}

// Вкусы одной точки: галочка «этот вкус тут есть» + количество.
// Галочки, а не просто количества: на точку привозят не весь ассортимент, и
// нули по всем вкусам, кроме одного, — это не выбор, а лишняя работа.
function renderPointFlavors(блок, город) {
  const где = блок.querySelector(".pflavors");
  if (!где) return;
  где.innerHTML = editPointFlavors[город].map(f =>
    `<div class="admrow" data-flavor="${esc(f)}">
       <label class="an" style="display:flex;gap:8px;align-items:center;font-weight:600">
         <input type="checkbox" class="pfchk" style="width:auto" checked> ${esc(f)}</label>
       ${qtyHtml(0, 'class="pfst"')}</div>`).join("");
  bindQty(где);
}

// Собирает с экрана: что завести и что убрать.
function собратьТочки() {
  const узел = $("edPoints");
  if (!узел) return { завести: [], убрать: [] };

  const завести = [...узел.querySelectorAll(".pointadd")]
    .filter(б => б.querySelector(".pchk").checked)
    .map(б => {
      const вкусы = [...б.querySelectorAll("[data-flavor]")]
        .filter(строка => строка.querySelector(".pfchk").checked)
        .map(строка => ({ flavor: строка.dataset.flavor,
                          stock: строка.querySelector(".pfst").value || "0" }));
      const поле = б.querySelector(".pstock");
      return {
        city: б.dataset.city,
        price: б.querySelector(".pprice").value.trim(),
        cost: б.querySelector(".pcost").value.trim(),
        variants: поле ? null : вкусы,
        stock: поле ? (поле.value || "0") : null,
      };
    });

  const убрать = [...узел.querySelectorAll(".pointrow[data-have]")]
    .filter(б => !б.querySelector(".pchk").checked && !б.querySelector(".pchk").disabled)
    .map(б => ({ city: б.dataset.city, id: +б.dataset.have }));

  return { завести, убрать };
}

// Строка добавления нового значения варианта. У категории с двумя измерениями
// (например у снюса — крепость и вкус) это два поля, которые здесь же
// склеиваются в одну строку через AXIS_SEP — дальше по всему приложению
// (корзина, заказ, склад, выгрузка) она живёт как обычный «вкус», без единой
// правки в этих местах.
function variantAddRowHtml(category, flavorOptions) {
  // Новый вариант приходит сразу с первым приходом: «Манго, 6 шт» — одним
  // действием, и этот приход ложится в историю склада, как любой другой.
  const сколько = `<input id="edNewQty" inputmode="numeric" placeholder="приход, шт" style="width:96px;flex:0 0 96px">`;
  if (!catTwoAxis(category)) {
    return `<div style="display:flex;gap:8px;margin-top:10px">
      <input id="edNewFlavor" placeholder="Добавить: ${esc(catVariant(category).toLowerCase())}" style="flex:1;min-width:0" list="edFlavorOpts">
      <datalist id="edFlavorOpts">${flavorOptions.map(f => `<option value="${esc(f)}">`).join("")}</datalist>
      ${сколько}
      <button class="iconbtn ok" id="edAddFlavor" style="width:auto;padding:0 16px">＋</button>
    </div>
    <div class="dnote" style="margin:4px 0 0">Можно вставить сразу список — через запятую или с новой строки.</div>`;
  }
  // Подсказка для первого измерения — то, что уже вводили для этого же товара:
  // одну и ту же крепость иначе пришлось бы перепечатывать на каждой строке.
  const axis1Opts = [...new Set(editPointList().map(f => String(f).split(AXIS_SEP)[0]).filter(Boolean))];
  return `<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
    <input id="edNewAxis1" placeholder="${esc(catVariant2(category))}" style="flex:1;min-width:90px" list="edAxis1Opts">
    <datalist id="edAxis1Opts">${axis1Opts.map(a => `<option value="${esc(a)}">`).join("")}</datalist>
    <input id="edNewAxis2" placeholder="${esc(catVariant(category))}" style="flex:1;min-width:90px" list="edFlavorOpts">
    <datalist id="edFlavorOpts">${flavorOptions.map(f => `<option value="${esc(f)}">`).join("")}</datalist>
    ${сколько}
    <button class="iconbtn ok" id="edAddFlavor" style="width:auto;padding:0 16px">＋</button>
  </div>`;
}
function bindVariantAdd(category) {
  // Однострочное поле при вставке склеивает строки в одну — «Манго Мята
  // Вишня» стал бы одним вкусом. Список построчно превращаем в список через
  // запятую прямо при вставке.
  const поле = $("edNewFlavor");
  if (поле) поле.onpaste = (e) => {
    const текст = (e.clipboardData || window.clipboardData || { getData: () => "" }).getData("text") || "";
    if (!/[\r\n]/.test(текст)) return;
    e.preventDefault();
    const список = текст.split(/[\r\n]+/).map(x => x.trim()).filter(Boolean).join(", ");
    поле.value = [поле.value.trim(), список].filter(Boolean).join(", ");
  };
  $("edAddFlavor").onclick = () => {
    let имена;
    if (catTwoAxis(category)) {
      const a1 = $("edNewAxis1").value.trim(), a2 = $("edNewAxis2").value.trim();
      if (!a1 || !a2) { alertMsg(`Заполните и «${catVariant2(category)}», и «${catVariant(category)}».`); return; }
      имена = [a1 + AXIS_SEP + a2];
    } else {
      // Список через запятую или построчно: пять вкусов поставки — одна
      // вставка, а не пять нажатий «＋».
      // «0,6 Ом» при этом — одно значение, а не «0» и «6 Ом» (NP-02).
      имена = разобратьСписок($("edNewFlavor").value);
      if (!имена.length) return;
    }
    const сырое = String($("edNewQty").value || "").trim();
    const штук = сырое === "" ? 0 : Number(сырое);
    if (!Number.isInteger(штук) || штук < 0) { alertMsg("Приход — целое число штук (или пусто, если пока ноль)."); return; }
    const уже = [];
    имена.forEach(имя => {
      const низ = ключВарианта(имя);
      const был = editVariants.find(v => ключВарианта(v.flavor) === низ);
      if (был && editRemoves.has(был.flavor)) { editRemoves.delete(был.flavor); return; }   // передумал убирать
      if (был || editAdds.some(a => ключВарианта(a.flavor) === низ)) { уже.push(имя); return; }
      editAdds.push({ flavor: имя, qty: штук });
    });
    if (catTwoAxis(category)) { $("edNewAxis2").value = ""; } else { $("edNewFlavor").value = ""; }
    $("edNewQty").value = "";
    renderEditVariants();
    if (уже.length) alertMsg(`Уже есть: ${уже.join(", ")}. Приход по заведённому варианту записывают в «📦 Склад».`);
  };
}

function renderEditVariants() {
  const строки = editVariants.map(v => {
    const уберём = editRemoves.has(v.flavor);
    const есть = `${v.stock} шт` + (v.reserved ? ` · в заказах ${v.reserved}` : "");
    return `<div class="admrow" style="${уберём ? "opacity:.55" : ""}"><div class="an">${esc(v.flavor)}
        <small>${уберём ? `будет убран${+v.stock > 0 ? ` — остаток ${v.stock} шт спишется` : ""}` : есть}</small></div>
      <button class="iconbtn${уберём ? "" : " danger"}" data-vrm="${esc(v.flavor)}" title="${уберём ? "Не убирать" : "Убрать вариант"}">${уберём ? "↩︎" : "✕"}</button></div>`;
  }).concat(editAdds.map((a, i) => `<div class="admrow"><div class="an">${esc(a.flavor)}
        <small>новый · ${a.qty ? `приход ${a.qty} шт` : "пока без остатка"}</small></div>
      <button class="iconbtn danger" data-vadd="${i}" title="Не добавлять">✕</button></div>`));
  $("edVarList").innerHTML = строки.length ? строки.join("")
    : `<p style="color:var(--hint)">Нет значений «${esc(catVariant(editCategory))}» — добавьте ниже.</p>`;
  $("edVarList").querySelectorAll("[data-vrm]").forEach(b => b.onclick = () => {
    const f = b.dataset.vrm;
    const v = editVariants.find(x => x.flavor === f);
    // Под невыданные заказы вариант не убрать: отмена такого заказа вернула бы
    // товар варианту, которого больше нет. Говорим сразу, а не при сохранении.
    if (!editRemoves.has(f) && v && v.reserved) {
      alertMsg(`«${f}»: ${v.reserved} шт в невыданных заказах. Уберите вариант после их выдачи или отмены.`);
      return;
    }
    if (editRemoves.has(f)) editRemoves.delete(f); else editRemoves.add(f);
    renderEditVariants();
  });
  $("edVarList").querySelectorAll("[data-vadd]").forEach(b => b.onclick = () => {
    editAdds.splice(+b.dataset.vadd, 1); renderEditVariants();
  });
  // Блок точек живёт на тех же вкусах — перерисовываем и его, иначе внизу
  // останется список, которого наверху уже нет.
  if (typeof обновитьБлокТочек === "function") обновитьБлокТочек();
}

// Приводит точки к тому, что отмечено на экране: заводит новые, убирает снятые.
// Возвращает строку для человека или "".
//
// Ходим по точкам по одной той же ручкой, что и «Добавить на точку»: она уже
// проверяет права, цену, закупку и повтор. Своя ручка «сразу на несколько»
// означала бы второй экземпляр этих проверок, и однажды они бы разошлись.
//
// Про каждую точку отвечаем отдельно: «завели в Турове, в Лунинце не вышло» —
// правда, а молчаливое «сохранено» после половины сделанного — нет.
async function применитьТочки(p, убрать) {
  const { завести } = собратьТочки();
  const удачно = [], убраны = [], беды = [], заминки = [];

  for (const т of завести) {
    if (!т.price) { беды.push(`${т.city}: не указана цена`); continue; }
    // Закупку требуем так же, как на отдельном экране: незаполненная навсегда
    // выбрасывает товар из подсчёта прибыли, и отчёт занижает заработок молча.
    if (!т.cost) { беды.push(`${т.city}: не указана закупка (если её не было — поставьте 0)`); continue; }
    if (т.variants && !т.variants.length) { беды.push(`${т.city}: не отмечено ни одно значение «${catVariant(p.category)}»`); continue; }
    const тело = { initData, model_id: p.model_id, city: т.city,
                   price: т.price, cost: т.cost, is_hit: 0 };
    if (т.variants) тело.variants = т.variants; else тело.stock = т.stock;
    try {
      const r = await fetch("/api/admin/product/from-model", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(тело) });
      const d = await r.json();
      if (d.ok) удачно.push(т.city);
      else беды.push(`${т.city}: ` + (d.error === "already_here" ? "товар уже там"
                   : d.error === "bad_price" ? "цена должна быть больше нуля"
                   : d.message || "не удалось добавить"));
    } catch (e) { беды.push(`${т.city}: сеть недоступна`); }
  }

  for (const т of (убрать || [])) {
    try {
      const r = await fetch("/api/admin/product/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, id: т.id, force: !!т.force }) });
      const d = await r.json();
      if (d.ok) убраны.push(т.city);
      // Сервер придержал: по товару есть незакрытые заказы. Это не отказ, а
      // вопрос — и задать его должен человек, а не мы за него решить.
      else if (d.error === "open_orders") заминки.push({ город: т.city, id: т.id, что: d.message });
      else беды.push(`${т.city}: ` + (d.message || "не удалось убрать"));
    } catch (e) { беды.push(`${т.city}: сеть недоступна`); }
  }

  const строки = [];
  if (удачно.length) строки.push("Добавлено: " + удачно.join(", "));
  if (убраны.length) строки.push("Убрано: " + убраны.join(", "));
  if (беды.length) строки.push("Не получилось — " + беды.join("; "));
  return { текст: строки.join("\n"), заминки };
}

// Общий хвост сохранения: применить точки, обновить список, закрыть экран.
// Вынесен, потому что зовётся из двух мест — сразу и после подтверждения.
async function завершитьПравку(p, убрать, отказы) {
  const { текст, заминки } = await применитьТочки(p, убрать);

  // Сервер придержал удаление: по товару есть незакрытые заказы. Спрашиваем и,
  // если человек настаивает, повторяем с force. Решает он, а не мы.
  if (заминки.length) {
    const вопрос = заминки.map(з => з.что).join("\n\n") + "\n\nВсё равно убрать?";
    confirmMsg(вопрос, async () => {
      const ещё = await применитьТочки(p, заминки.map(з => ({ city: з.город, id: з.id, force: true })));
      await refreshProducts();
      $("editView").classList.remove("show");
      alertMsg([текст, ещё.текст].filter(Boolean).join("\n") || "Сохранено ✅");
    });
    return;
  }

  await refreshProducts();
  const беды = (отказы || []).length ? "Не сохранилось — " + (отказы || []).join("; ") : "";
  const строки = [беды, текст].filter(Boolean).join("\n");
  // Не всё сохранилось — карточка остаётся открытой с введённым: закрыть её
  // значило бы выбросить работу человека вместе с ошибкой. Точки
  // перерисовываем по свежим данным (заведённая только что точка теперь
  // «есть», а не «завести»), остальной ввод остаётся как был.
  if (беды) {
    const свежий = shelf().find(x => x.id === p.id);
    if (свежий && $("edPoints")) renderEditPoints(свежий, точкиМодель);
    обновитьОстатокВКарточке();
    alertMsg("⚠️ Сохранено не всё\n\n" + строки + "\n\nВведённое осталось в карточке — проверьте и сохраните ещё раз.");
    return;
  }
  $("editView").classList.remove("show");
  // «Сохранено» пишем только если всё и правда сохранилось. Половина работы,
  // объявленная успехом, — это ошибка, которую заметят через неделю по цифрам.
  alertMsg(строки ? (беды ? "⚠️ Сохранено не всё\n\n" : "Сохранено ✅\n\n") + строки
                  : "Сохранено ✅");
}

// То же ли это значение поля, что было при открытии: «20», 20 и «20,0» — одно
// и то же, иначе сохранение слало бы нетронутую цену и ловило бы «конфликт»
// с самим собой.
function тоЖеПоле(поле, стало, было) {
  if (поле === "price" || поле === "cost") {
    const n = (x) => Number(String(x ?? "").replace(",", ".").trim() || 0);
    return n(стало) === n(было);
  }
  if (поле === "is_hit") return !!Number(стало) === !!Number(было);
  return String(стало ?? "").trim() === String(было ?? "").trim();
}

// Поле сохранено (или его поменял кто-то другой) — снимок «каким было»
// переезжает на новое значение. См. отправитьПоля в saveEdit.
function запомнитьКакБыло(поле, значение) {
  if (поле === "price") editOrigPrice = значение;
  else if (поле === "cost") editOrigCost = значение || "";
  else if (поле === "is_hit") editOrigHit = !!Number(значение);
  else if (поле === "name") editOrigName = значение;
  else if (поле === "category") editOrigCat = значение;
  else if (поле === "city") editOrigCity = значение;
  else if (поле === "description") editOrigDesc = значение;
  else if (поле === "brand") editOrigBrand = значение;
  else if (поле === "flavor") editOrigFlavor = значение;
}

const НЕИЗВЕСТНО_КАРТОЧКА = "ответ сервера не дошёл — могло и сохраниться. Закройте карточку, откройте снова и проверьте";

async function saveEdit(p, подтверждено) {
  const isVar = hasVariants(p);
  // Ответы сервера ПРОВЕРЯЕМ. Раньше их не смотрели вовсе: сервер отказывал —
  // «нельзя перенести туда, где товар уже есть», — а экран говорил
  // «Сохранено ✅» и город оставался прежним. Ошибка, которая учит доверять
  // неверному, хуже видимой поломки.
  const отказы = [];
  const ЛЮДСКИ = {
    already_here: "на этой точке товар уже есть",
    other_city: "это точка другого продавца",
    forbidden: "нет прав на это действие",
    bad_price: "цена должна быть больше нуля",
    cost_required: "не указана закупочная цена",
    bad_input: "поле заполнено неверно",
    bad_value: "значение введено неверно — проверьте, что это число",
    bad_id: "товар не найден",
    not_found: "товар не найден",
  };
  const назвать = (что, d) => `${что}: ${(d && (d.message || ЛЮДСКИ[d.error])) || "не сохранилось"}`;

  // Состав вариантов: не оставить товар пустым и не убрать молча то, что
  // лежит на полке. Спрашиваем ДО любой отправки — отменить списание нечем.
  if (isVar && !editPointList().length) {
    alertMsg(`Оставьте хотя бы одно значение: ${catVariant(p.category)}.`);
    return;
  }
  const сОстатком = editVariants.filter(v => editRemoves.has(v.flavor) && +v.stock > 0);
  if (сОстатком.length && !подтверждено) {
    confirmMsg(`Убрать ${сОстатком.map(v => `«${v.flavor}» (${v.stock} шт)`).join(", ")}? `
               + `Остаток спишется пересчётом до нуля и останется в истории склада.`,
               () => saveEdit(p, true));
    return;
  }

  // Поля копим и отправляем ОДНИМ запросом. Шлём только то, что человек правда
  // поменял, и вместе с тем, каким поле было при открытии: если его за это
  // время поменял кто-то другой, сервер не затрёт чужую правку, а скажет о ней.
  const поля = {}, имена = {}, ожидали = {};
  const upd = (field, value, что, было) => {
    if (было !== undefined && тоЖеПоле(field, value, было)) return;
    поля[field] = value; имена[field] = что || field;
    if (было !== undefined) ожидали[field] = было;
  };
  const отправитьПоля = async () => {
    if (!Object.keys(поля).length) return { ok: true };
    if ("price" in поля && editOrigPriceRev !== null) ожидали.price_rev = editOrigPriceRev;
    const r = await fetch("/api/admin/product/update", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id: editId, fields: поля, expected: ожидали }) });
    const d = await r.json().catch(() => null);
    // Ответ не дочитался или сервер упал — правка могла и сохраниться.
    // «Не сохранилось» было бы неправдой; повтор безопасен: сервер сверит
    // значения со снимком и не затрёт то, что уже записано.
    if (!d || r.status >= 500) { отказы.push(`правка товара: ${НЕИЗВЕСТНО_КАРТОЧКА}`); return {}; }
    if (!d.ok) { отказы.push(назвать("правка товара", d)); return d; }
    // Сервер сохраняет всё, что прошло, и называет, что не прошло: отказ в
    // цене не повод потерять только что вписанное описание.
    for (const [field, беда] of Object.entries(d.failed || {})) отказы.push(назвать(имена[field] || field, беда));
    // Снимок полей — туда, где они теперь есть. Сохранённое — новым значением,
    // иначе повторное «Сохранить» ловило бы конфликт с самим собой. Чужая
    // правка (конфликт) — тем, что сейчас на сервере: человек её увидел, и
    // следующее сохранение его значения будет уже осознанным.
    (d.saved || []).forEach(f => запомнитьКакБыло(f, поля[f]));
    if (номерВерсии(d.price_rev) !== null) editOrigPriceRev = d.price_rev;
    for (const [f, беда] of Object.entries(d.failed || {})) {
      if (беда && беда.error === "conflict") запомнитьКакБыло(f, беда.current);
      if (f === "price" && беда && номерВерсии(беда.current_rev) !== null) editOrigPriceRev = беда.current_rev;
    }
    return d;
  };
  const послать = async (адрес, тело, что) => {
    const r = await fetch(адрес, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(тело) });
    const d = await r.json().catch(() => null);
    if (!d || r.status >= 500) { отказы.push(`${что}: ${НЕИЗВЕСТНО_КАРТОЧКА}`); return {}; }
    if (!d.ok) отказы.push(назвать(что, d));
    return d;
  };
  // Новые варианты — с первым приходом, убранные — со списанием остатка.
  // Числа у заведённых вариантов тут не меняются: это работа склада, и потому
  // продажа, случившаяся пока карточка открыта, этому сохранению не мешает.
  const сменитьСостав = async () => {
    if (!editAdds.length && !editRemoves.size) return;
    const d = await послать("/api/admin/product/variants/change",
      { initData, id: editId, add: editAdds, remove: [...editRemoves], writeoff: сОстатком.length > 0 },
      catVariantMany(p.category).toLowerCase());
    if (d.ok) { editAdds = []; editRemoves = new Set(); }
  };

  $("edSave").disabled = true; $("edSave").textContent = "Сохраняю…";
  try {
    // Товар из ассортимента: сохраняем только то, что своё у этой точки.
    if (p.model_id) {
      upd("price", $("edPrice").value, "цена", editOrigPrice);
      upd("cost", $("edCost").value || 0, "закупка", editOrigCost || 0);
      // Города у товара с моделью правятся галочками ниже, а не селектом.
      // Два контрола об одном и том же всегда расходятся: селект предлагал
      // продавцу Турова все города, включая те, куда сервер его не пустит.
      upd("is_hit", $("edHit").checked ? 1 : 0, "отметка «Хит»", editOrigHit ? 1 : 0);
      await отправитьПоля();
      if (isVar) await сменитьСостав();
      // Точки — уже после того, как своя карточка сохранена: если что-то из
      // них упадёт, правки цены и состава всё равно на месте.
      //
      // Снятая галочка убирает товар с точки НАСОВСЕМ, вместе с её остатком и
      // историей склада. Спрашиваем до, а не после: отменить это нечем.
      const { убрать } = собратьТочки();
      if (убрать.length) {
        const где = убрать.map(т => `«${т.city}»`).join(", ");
        confirmMsg(`Убрать товар с точки ${где}? Остаток и движения склада этой точки удалятся. Отменить будет нечем.`,
                   async () => { await завершитьПравку(p, убрать, отказы); });
        $("edSave").disabled = false; $("edSave").textContent = "Сохранить";
        return;
      }
      await завершитьПравку(p, [], отказы);
      return;
    }
    const specs = collectSpecs("edSpecs");
    if (isVar) {
      // У одноразок число затяжек — часть названия модели («Elf Bar 6000»).
      const name = (p.category === "disposable" && specs.volume)
        ? [p.brand, specs.volume].filter(x => x && x !== "0").join(" ") : (p.brand || p.name);
      upd("price", $("edPrice").value, "цена", editOrigPrice);
      upd("cost", $("edCost").value || 0, "закупка", editOrigCost || 0);
      if (name) upd("name", name, "название", editOrigName);
    } else {
      const nm = $("edName").value.trim();
      if (!nm) { alertMsg("Введите название."); return; }
      upd("category", $("edCat").value, "категория", editOrigCat);
      upd("name", nm, "название", editOrigName);
      upd("price", $("edPrice").value, "цена", editOrigPrice);
      upd("cost", $("edCost").value || 0, "закупка", editOrigCost || 0);
      const brandName = pickerValue("edBrand");
      await ensureBrandExists(brandName);
      upd("brand", brandName, "бренд", editOrigBrand);
      upd("flavor", pickerValue("edFlavor"), "вкус", editOrigFlavor);
      upd("description", $("edDesc").value.trim(), "описание", editOrigDesc);
    }
    // Характеристики сохраняем одним запросом — сервер сам разложит крепость
    // и объём по своим колонкам, а остальное в JSON.
    await послать("/api/admin/product/specs", { initData, id: editId, specs }, "характеристики");
    upd("city", $("edCity").value, "точка", editOrigCity);
    upd("is_hit", $("edHit").checked ? 1 : 0, "отметка «Хит»", editOrigHit ? 1 : 0);
    await отправитьПоля();
    if (isVar) await сменитьСостав();
    if (editPhotoFile) {
      const fd = new FormData();
      fd.append("initData", initData); fd.append("id", editId); fd.append("file", editPhotoFile);
      const r = await fetch("/api/admin/photo", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!d.ok) отказы.push(назвать("фото", d)); else editPhotoFile = null;
    }
    await refreshProducts();
    // «Сохранено» пишем только если всё и правда сохранилось. Не сохранилось —
    // карточка остаётся открытой с введённым: закрыть её значило бы выбросить
    // работу человека вместе с ошибкой.
    if (отказы.length) {
      обновитьОстатокВКарточке();
      alertMsg("⚠️ Сохранено не всё\n\nНе сохранилось — " + отказы.join("; ")
               + "\n\nВведённое осталось в карточке — проверьте и сохраните ещё раз.");
      return;
    }
    $("editView").classList.remove("show");
    alertMsg("Сохранено ✅");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("edSave").disabled = false; $("edSave").textContent = "Сохранить"; }
}

function delAdminRow(id) {
  const p = shelf().find(x => x.id === id);
  // Удаление уносит остаток и историю. Если товар просто кончился —
  // правильный ход другой, и сказать об этом надо до, а не после.
  const warn = p && p.stock > 0
    ? `Удалить «${p.name}» с точки ${p.city}? На полке ещё ${p.stock} шт — если товар просто закончился, лучше снять с витрины (🚫).`
    : "Удалить товар с точки?";
  confirmMsg(warn, () => doDelAdminRow(id));
}

// Снять с витрины / вернуть. Для покупателя товар исчезает, для магазина
// остаётся: остаток, движения склада и отзывы на месте.
async function toggleHidden(id) {
  const p = shelf().find(x => x.id === id); if (!p) return;
  // Продавцу чужой точки сервер откажет — и раньше список просто
  // перерисовывался по-старому, без единого слова.
  if (!await админПост("/api/admin/product/update", { id, field: "hidden", value: p.hidden ? 0 : 1 },
                       p.hidden ? "вернуть на витрину" : "снять с витрины")) return;
  await refreshProducts();
  toast(p.hidden ? "Снова на витрине" : "Снят с витрины — остаток сохранён");
}
async function doDelAdminRow(id, force) {
  try {
    const r = await fetch("/api/admin/product/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id, force: !!force }) });
    const d = await r.json().catch(() => ({}));
    // Сервер придерживает удаление, если по товару есть незакрытые заказы.
    // Раньше ответ не читался вовсе: товар оставался, а экран молчал — и это
    // выглядело бы как «кнопка не работает».
    if (!d.ok && d.error === "open_orders") {
      confirmMsg(d.message + "\n\nВсё равно удалить?", () => doDelAdminRow(id, true));
      return;
    }
    if (!d.ok) { alertMsg(d.message || "Не удалось удалить товар."); return; }
    await refreshProducts();
  } catch (e) { alertMsg(текстСбоя(e)); }
}

// Убираем заставку по факту готовности данных (зовёт start() в 01-core.js),
// а не по фиксированному времени. Раньше уходила ровно через 4 сек всегда —
// на медленной сети (ради которой и написан весь остальной код) это значило
// увидеть пустую сетку каталога ДО того, как он реально загрузился. Таймер
// остаётся только страховкой: если что-то в start() зависло навсегда,
// заставка всё равно не провисит вечно.
function hideSplash() { const s = document.getElementById("splash"); if (s) s.style.display = "none"; }
setTimeout(hideSplash, 4000);

start();
