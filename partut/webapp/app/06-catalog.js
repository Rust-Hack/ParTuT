// 06-catalog.js — ассортимент: бренды, вкусы, характеристики, редактор товара
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ----- Бренды и вкусы -----
let brands = [], brandFlavors = [], editingBrandId = null;
async function fetchBrands() {
  const r = await fetch("/api/brands");
  brands = await r.json();
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
  v.split(",").map(s => s.trim()).filter(Boolean).forEach(f => {
    // Сверяем без учёта регистра: «мята» после «Мята» — это тот же вкус.
    if (!brandFlavors.some(x => x.toLowerCase() === f.toLowerCase())) brandFlavors.push(f);
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
    await fetchBrands(); await fetchFlavors(); renderKnownFlavors();
    await refreshProducts();      // переименование бренда переносит и товары
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
      <div class="admrow" style="padding:0;background:none;box-shadow:none">
        <div class="an" style="font-weight:800;font-size:15px">${esc(l.name)}</div>
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
    await refreshProducts();
    await loadDelivery(); renderLocList();
  } catch (e) { alertMsg(текстСбоя(e)); }
};
function delLocation(id) { confirmMsg("Удалить локацию?", () => doDelLocation(id)); }
async function doDelLocation(id) {
  try {
    const r = await fetch("/api/admin/location/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id }) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "has_products" ? "Нельзя удалить: в этой локации есть товары. Сначала уберите их." : "Не удалось удалить локацию.");
      return;
    }
    await refreshProducts();
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
  $("stockPickList").innerHTML = `<p style="color:var(--hint);margin:8px 0 0">Загрузка…</p>`;
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
        <small>${esc(catName(m.category))}${m.flavors.length ? ` · вкусов: ${m.flavors.length}` : ""}</small>${уже}</div>
      <span style="color:var(--hint)">›</span></div>`;
  }).join("");
  $("stockPickList").querySelectorAll("[data-pick]").forEach(b => b.onclick = () => {
    $("stockPickView").classList.remove("show");
    openStockIn(+b.dataset.pick);
  });
}

function renderAdminList() {
  if (!shelf().length) { $("adminList").innerHTML = `<p style="color:var(--hint)">Товаров пока нет.</p>`; return; }
  const q = (admSearch || "").trim().toLowerCase();
  const list = shelf().filter(p => {
    // Продавец точки ведёт свою точку — чужие товары ему не показываем даже
    // на чтение: правки по ним сервер отклонит, а список только путает.
    if (myScope() && p.city !== myScope()) return false;
    if (admCatFilter !== "all" && p.category !== admCatFilter) return false;
    if (admLocFilter !== "all" && p.city !== admLocFilter) return false;
    const st = stockState(p);
    if (admStockFilter === "need" && st === "ok") return false;
    if (admStockFilter === "out" && st !== "out") return false;
    if (q && !(`${p.name} ${p.brand || ""} ${p.flavor || ""}`.toLowerCase().includes(q))) return false;
    return true;
  });
  if (!list.length) {
    const msg = admStockFilter === "out" ? "Ничего не кончилось — на всех точках есть остаток."
              : admStockFilter === "need" ? "Завозить нечего: везде больше " + LOW_STOCK + " шт."
              : "Ничего не найдено.";
    $("adminList").innerHTML = `<p style="color:var(--hint)">${msg}</p>`; return;
  }
  $("adminList").innerHTML = list.map(p => {
    // Закончившийся товар и число ждущих — то, ради чего в этот список
    // заходят чаще всего: он отвечает на вопрос «что срочно завезти».
    const st = stockState(p);
    const out = st === "out" ? `<span class="tagbadge out">нет</span>`
              : st === "low" ? `<span class="tagbadge warn">осталось ${p.stock}</span>` : "";
    const wait = p.waiting ? `<span class="tagbadge warn">ждут ${p.waiting}</span>` : "";
    const off = p.hidden ? `<span class="tagbadge">снят с витрины</span>` : "";
    const marks = (out || wait || off) ? `<div class="admmarks">${out}${wait}${off}</div>` : "";
    // «Больше не продаём» и «этого не было» — разные вещи. Снятый товар
    // сохраняет остаток, историю и отзывы, удалённый уносит их с собой.
    const tail = `<button class="iconbtn" data-move="${p.id}" title="Приход или списание">📦</button>
        <button class="iconbtn" data-hide="${p.id}" title="${p.hidden ? 'Вернуть на витрину' : 'Снять с витрины'}">${p.hidden ? '👁' : '🚫'}</button>
        <button class="iconbtn" data-edit="${p.id}">✏️</button>
        <button class="iconbtn danger" data-del="${p.id}">🗑</button></div>`;
    const фото = p.photo_url ? "фото ✓" : "без фото";
    const хит = p.is_hit ? " · 🔥" : "";
    if (hasVariants(p)) {
      // товар-модель: цену/вкусы/остаток правим в редакторе (✏️), но ВИДНО
      // цену должно быть здесь: за ней в этот список и заходят чаще всего.
      return `<div class="admrow">
        <div class="an">${esc(p.name)}<small>${p.city} · ${p.price} Br · ${p.variants.length} вк · ${p.stock} шт · ${фото}${хит}</small>${marks}</div>
        ${tail}`;
    }
    return `<div class="admrow">
      <div class="an">${esc(p.name)}<small>${p.city} · ${p.price} Br · ${p.stock} шт · ${фото}${хит}</small>${marks}</div>
      ${tail}`;
  }).join("");
  $("adminList").querySelectorAll("[data-move]").forEach(b => b.onclick = () => openStockMove(+b.dataset.move));
  $("adminList").querySelectorAll("[data-edit]").forEach(b => b.onclick = () => openEdit(+b.dataset.edit));
  $("adminList").querySelectorAll("[data-del]").forEach(b => b.onclick = () => delAdminRow(+b.dataset.del));
  $("adminList").querySelectorAll("[data-hide]").forEach(b => b.onclick = () => toggleHidden(+b.dataset.hide));
}

// ----- Редактор товара -----
let editId = null, editVariants = [], editPhotoFile = null;
$("editClose").onclick = () => $("editView").classList.remove("show");

function openEdit(id) {
  const p = shelf().find(x => x.id === id); if (!p) return;
  editId = id;
  editVariants = (p.variants || []).map(v => ({ flavor: v.flavor, stock: v.stock }));
  editPhotoFile = null;
  // id > 0 — только дополнительные: главное фото меняется отдельным полем выше.
  editPhotos = (p.photos || []).filter(g => g.id);
  renderEdit(p);
  $("editView").classList.add("show");
}

// Общий блок замены фото — превью + выбор файла (для любого товара).
function editPhotoBlock(p) {
  return `<label>Главное фото</label>
    <div class="edphoto">
      <img id="edPhotoPrev" alt="" src="${thumbOf(p) || ''}" ${p.photo_url ? '' : 'style="display:none"'}>
      <input type="file" id="edPhoto" accept="image/*">
    </div>
`;
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
          <div style="display:flex;gap:8px;margin-top:10px">
            <input id="edNewFlavor" placeholder="Добавить: ${esc(catVariant(p.category).toLowerCase())}" style="flex:1" list="edFlavorOpts">
            <datalist id="edFlavorOpts">${(md ? md.flavors : []).map(f => `<option value="${esc(f)}">`).join("")}</datalist>
            <button class="iconbtn ok" id="edAddFlavor" style="width:auto;padding:0 16px">＋</button>
          </div>`
          : `<label>Остаток (шт.)</label>${qtyHtml(p.stock, 'id="edStock"')}`}
        ${editHitBlock(p)}
        <label style="margin-top:18px">Точки продаж</label>
        <div id="edPoints"></div>
        <div class="dnote" style="margin-top:12px">Название, характеристики, вкусы и фото — в «Ассортименте»: там они правятся сразу для всех точек.</div>
        <button class="closebtn" id="edToModel" style="margin-top:6px">📚 Открыть модель</button>
        <button class="bigbtn" id="edSave" style="margin-top:10px">Сохранить</button>
      </div>`;
    if (isVar) renderEditVariants();
    renderEditPoints(p, md);
    if ($("edAddFlavor")) $("edAddFlavor").onclick = () => {
      const v = $("edNewFlavor").value.trim(); if (!v) return;
      if (!editVariants.some(x => x.flavor === v)) editVariants.push({ flavor: v, stock: 0 });
      $("edNewFlavor").value = ""; renderEditVariants();
    };
    $("edToModel").onclick = () => {
      $("editView").classList.remove("show");
      openModels().then(() => editModel(p.model_id));
    };
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
          <div><label>Остаток (шт.)</label>${qtyHtml(p.stock, 'id="edStock"')}</div>
        </div>
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
    // Кнопки –/+ оживляем после отрисовки: разметку собрал qtyHtml,
    // обработчики вешаются здесь. Вкусы биндятся отдельно — они перерисовываются.
    bindQty($("editView"));
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
      <div style="display:flex;gap:8px;margin-top:10px">
        <input id="edNewFlavor" placeholder="Добавить: ${esc(catVariant(p.category).toLowerCase())}" style="flex:1" list="edFlavorOpts">
        <datalist id="edFlavorOpts">${avail.map(f => `<option value="${esc(f)}">`).join("")}</datalist>
        <button class="iconbtn ok" id="edAddFlavor" style="width:auto;padding:0 16px">＋</button>
      </div>
      ${editHitBlock(p)}
      ${editPhotoBlock(p)}
      ${toModelBlock()}
      <button class="bigbtn" id="edSave" style="margin-top:16px">Сохранить</button>
    </div>`;
  renderEditVariants();
  bindEditPhoto(); renderEditGallery();
  // Кнопки –/+ оживляем после отрисовки: разметку собрал qtyHtml,
  // обработчики вешаются здесь. Вкусы биндятся отдельно — они перерисовываются.
  bindQty($("editView"));
  // Товар без модели: кнопка «Сделать моделью» — единственный путь к точкам.
  if ($("edToModelNew")) $("edToModelNew").onclick = () => сделатьМоделью(p);
  $("edAddFlavor").onclick = () => {
    const v = $("edNewFlavor").value.trim(); if (!v) return;
    if (!editVariants.some(x => x.flavor === v)) editVariants.push({ flavor: v, stock: 0 });
    $("edNewFlavor").value = ""; renderEditVariants();
  };
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
  // Вкусы берём ИЗ КАРТОЧКИ, прямо с экрана (editVariants), а не из сохранённой
  // модели. Три причины, и все три — найденные грабли:
  //
  //  • добавил вкус вверху — он тут же виден внизу, а не после сохранения;
  //  • верх и низ экрана говорят об одном товаре одинаково: если наверху
  //    «Остаток» числом, то и внизу число, а не список вкусов;
  //  • своя кнопка «Добавить вкус» в блоке становится не нужна — а третье
  //    место, где заводят вкусы, это ровно то, из-за чего списки разошлись.
  return editVariants.map(v => String(v.flavor || "").trim()).filter(Boolean);
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
  return `<div style="border-top:1px solid var(--line);margin:20px 0 0"></div>
    <label style="margin-top:16px">Точки продаж</label>
    <div class="dnote" style="margin:0 0 10px">Этот товар заведён без модели, поэтому живёт только на одной точке. Модель — это описание товара, общее для всех городов; из неё он и добавляется куда угодно.</div>
    <button class="closebtn" id="edToModelNew">📚 Сделать моделью</button>`;
}

async function сделатьМоделью(p) {
  const r = await fetch("/api/admin/product/to-model", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData, id: p.id }) });
  const d = await r.json().catch(() => ({}));
  if (!d.ok) { alertMsg(d.message || "Не удалось сделать моделью."); return; }
  await refreshProducts(); await fetchModels();
  // Открываем карточку заново: теперь у товара есть модель, и в ней появится
  // блок точек. Показать это сразу важнее, чем сэкономить одну перерисовку.
  openEdit(p.id);
  alertMsg("Готово ✅\n\nОписание уехало в «Ассортимент». Ниже появились точки продаж.");
}

function renderEditPoints(p, md) {
  точкиТовар = p; точкиМодель = md;
  const узел = $("edPoints");
  if (!узел) return;
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
          ? `<label>Какие из вкусов есть на точке «${esc(имя)}»</label><div class="pflavors"></div>`
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

  узел.querySelectorAll("[data-gopoint]").forEach(b => b.onclick = () => openEdit(+b.dataset.gopoint));
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

function renderEditVariants() {
  $("edVarList").innerHTML = editVariants.length
    ? editVariants.map((v, i) => `<div class="admrow"><div class="an">${esc(v.flavor)}</div>
        ${qtyHtml(v.stock, `class="edvst" data-i="${i}"`)}
        <button class="iconbtn danger" data-vdel="${i}">✕</button></div>`).join("")
    : `<p style="color:var(--hint)">Нет вкусов — добавьте ниже.</p>`;
  $("edVarList").querySelectorAll(".edvst").forEach(inp => inp.oninput = () => { editVariants[+inp.dataset.i].stock = inp.value; });
  bindQty($("edVarList"));
  $("edVarList").querySelectorAll("[data-vdel]").forEach(b => b.onclick = () => { editVariants.splice(+b.dataset.vdel, 1); renderEditVariants(); });
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
    if (т.variants && !т.variants.length) { беды.push(`${т.city}: не отмечен ни один вкус`); continue; }
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
  $("editView").classList.remove("show");
  const беды = (отказы || []).length ? "Не сохранилось — " + (отказы || []).join("; ") : "";
  const строки = [беды, текст].filter(Boolean).join("\n");
  // «Сохранено» пишем только если всё и правда сохранилось. Половина работы,
  // объявленная успехом, — это ошибка, которую заметят через неделю по цифрам.
  alertMsg(строки ? (беды ? "⚠️ Сохранено не всё\n\n" : "Сохранено ✅\n\n") + строки
                  : "Сохранено ✅");
}

async function saveEdit(p) {
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
    bad_price: "цена должна быть больше нуля",
    bad_id: "товар не найден",
    not_found: "товар не найден",
  };
  const назвать = (что, d) => `${что}: ${(d && (d.message || ЛЮДСКИ[d.error])) || "не сохранилось"}`;

  const upd = async (field, value, что) => {
    const r = await fetch("/api/admin/product/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id: editId, field, value }) });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) отказы.push(назвать(что || field, d));
    return d;
  };
  const послать = async (адрес, тело, что) => {
    const r = await fetch(адрес, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(тело) });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) отказы.push(назвать(что, d));
    return d;
  };
  $("edSave").disabled = true; $("edSave").textContent = "Сохраняю…";
  try {
    // Товар из ассортимента: сохраняем только то, что своё у этой точки.
    if (p.model_id) {
      await upd("price", $("edPrice").value, "цена");
      await upd("cost", $("edCost").value || 0, "закупка");
      if (isVar) {
        const variants = editVariants.filter(v => v.flavor).map(v => ({ flavor: v.flavor, stock: v.stock || "0" }));
        if (!variants.length) { alertMsg("Оставьте хотя бы один вкус."); return; }
        await послать("/api/admin/product/variants", { initData, id: editId, variants }, "вкусы");
      } else {
        await upd("stock", $("edStock").value, "остаток");
      }
      // Города у товара с моделью правятся галочками ниже, а не селектом.
      // Два контрола об одном и том же всегда расходятся: селект предлагал
      // продавцу Турова все города, включая те, куда сервер его не пустит.
      await upd("is_hit", $("edHit").checked ? 1 : 0, "отметка «Хит»");
      // Точки — уже после того, как своя карточка сохранена: если что-то из
      // них упадёт, правки цены и остатка всё равно на месте.
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
      const variants = editVariants.filter(v => v.flavor).map(v => ({ flavor: v.flavor, stock: v.stock || "0" }));
      if (!variants.length) { alertMsg("Оставьте хотя бы один вкус."); return; }
      // У одноразок число затяжек — часть названия модели («Elf Bar 6000»).
      const name = (p.category === "disposable" && specs.volume)
        ? [p.brand, specs.volume].filter(x => x && x !== "0").join(" ") : (p.brand || p.name);
      await upd("price", $("edPrice").value);
      await upd("cost", $("edCost").value || 0);
      if (name) await upd("name", name);
      await послать("/api/admin/product/variants", { initData, id: editId, variants }, "вкусы");
    } else {
      const nm = $("edName").value.trim();
      if (!nm) { alertMsg("Введите название."); return; }
      await upd("category", $("edCat").value);
      await upd("name", nm);
      await upd("price", $("edPrice").value);
      await upd("cost", $("edCost").value || 0);
      await upd("stock", $("edStock").value);
      const brandName = pickerValue("edBrand");
      await ensureBrandExists(brandName);
      await upd("brand", brandName);
      await upd("flavor", pickerValue("edFlavor"));
      await upd("description", $("edDesc").value.trim());
    }
    // Характеристики сохраняем одним запросом — сервер сам разложит крепость
    // и объём по своим колонкам, а остальное в JSON.
    await послать("/api/admin/product/specs", { initData, id: editId, specs }, "характеристики");
    await upd("city", $("edCity").value, "точка");
    await upd("is_hit", $("edHit").checked ? 1 : 0);
    if (editPhotoFile) {
      const fd = new FormData();
      fd.append("initData", initData); fd.append("id", editId); fd.append("file", editPhotoFile);
      const r = await fetch("/api/admin/photo", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!d.ok) отказы.push(назвать("фото", d));
    }
    await refreshProducts();
    $("editView").classList.remove("show");
    alertMsg(отказы.length ? "⚠️ Сохранено не всё\n\nНе сохранилось — " + отказы.join("; ")
                           : "Сохранено ✅");
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

// Убираем заставку после проигрыша анимации, чтобы не мешала кликам.
setTimeout(() => { const s = document.getElementById("splash"); if (s) s.style.display = "none"; }, 4000);

start();
