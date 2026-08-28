// 04-admin.js — админка: хаб, модели, завоз, категории, статистика,
// пользователи, компенсация, настройки, склад, промокоды, журнал
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ---------- Админка (только для админов) ----------

// Промис двух справочников, которые нужны хабу не самому, а двум его
// разделам («Цены и остатки» — товарам, «Бренды» — брендам). Раньше оба
// похода в базу шли ПОДРЯД (await, потом await) и держали за собой сам
// показ экрана: тап по «Управление» секундами ничего не показывал, хотя
// хаб — это меню с бейджами, ни товары, ни бренды ему для отрисовки не
// нужны. Теперь хаб виден сразу, а запросы идут параллельно; кому эти
// данные действительно нужны — ждёт этот же промис, а не гонит свой поход.
let _adminBoot = null;

async function openAdmin() {
  if ($("mRequests")) $("mRequests").style.display = (me && me.is_super) ? "" : "none";
  // Раздавать доступ может только владелец: иначе продавец выпишет права сам себе.
  if ($("mStaff")) $("mStaff").style.display = (me && me.is_super) ? "" : "none";
  // Документы магазина — только владельцу: это не настройка смены.
  if ($("mDocs")) $("mDocs").style.display = (me && me.is_super) ? "" : "none";
  if ($("mLog")) $("mLog").style.display = (me && me.is_super) ? "" : "none";
  applyAdminScope();
  $("adminView").classList.add("show");
  if (me && me.is_super) loadReqBadge();
  loadPendingReviews(true);          // счётчик отзывов — без открытия раздела
  loadOrdersBadge();                 // сколько заказов ждут продавца
  loadToday();                       // сводка дня — первое, что видно на входе
  _adminBoot = Promise.all([fetchAdminProducts(), fetchBrands()]);
  await _adminBoot;
  renderLowBadge();                  // сколько позиций надо завезти — ждёт товары
}

// Продавец точки ведёт свою точку: товары, склад, заказы. Всё, что меняет
// магазин целиком — ассортимент, бренды, категории, настройки, деньги, —
// сервер ему всё равно вернёт 403, и показывать эти строки значит обещать
// то, чего не будет.
// Две разные вещи, которые раньше решались одним признаком: РОЛЬ (что тебе
// вообще можно) и ТОЧКА (с чем именно ты работаешь). Продавец всех точек
// имеет пустой город — но владельцем от этого не становится.
const myScope = () => (me && me.admin_city) || "";
const isOwner = () => !!me && (me.role === "owner" || me.role === "dev");

function applyAdminScope() {
  const shopWide = isOwner();
  // Ассортимент продавцу нужен: оттуда он завозит модель на свою точку.
  // Заводить и править модели там он не сможет — это прячется внутри.
  ["mBrands", "mCats", "gShop", "mStats", "mReferrals", "mPromos", "mRaffle",
   "gSetup", "mLocations", "mSettings", "gAccess"].forEach(id => {
    const el = $(id); if (el) el.style.display = shopWide ? "" : "none";
  });
  const head = document.querySelector("#adminView .viewhead h2");
  if (head) head.textContent = shopWide ? "🛠 Управление"
    : `🛠 Управление · ${myScope() || "все точки"}`;
}

// Заказы и остаток — то, ради чего сюда заходят. Без счётчиков в оба раздела
// приходилось заглядывать вслепую, чтобы узнать, есть ли вообще работа.
async function loadOrdersBadge() {
  try {
    const r = await fetch("/api/admin/orders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    adminOrders = d.orders || [];
    // Ждут ИМЕННО продавца: 'new' — это заказ картой без чека, там ход клиента.
    setBadge("ordBadge", adminOrders.filter(o => o.status === "paid").length);
  } catch (e) {}
}
// Сводка дня. Отвечает на четыре вопроса, ради которых сюда и заходят:
// что ждёт меня, что ждёт покупателя, сколько наторговали, что кончается.
// Каждая плитка — кнопка в нужный раздел с уже выставленным фильтром.
async function loadToday() {
  try {
    const r = await fetch("/api/admin/today", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) { $("todayCard").innerHTML = ""; return; }
    renderToday(d.today);
  } catch (e) { $("todayCard").innerHTML = ""; }
}

function renderToday(t) {
  const need = t.out_stock + t.low_stock;
  const tiles = [
    { n: t.waiting, lab: "ждут подтверждения", cls: t.waiting ? "act" : "calm", go: () => { ordersStatusFilter = "paid"; openOrders(); } },
    { n: t.to_issue, lab: "к выдаче", cls: "calm", go: () => { ordersStatusFilter = "confirmed"; openOrders(); } },
    { n: `${(+t.revenue_today).toFixed(2)} ${CUR}`,
      lab: `выдано сегодня${t.issued_today ? ` · ${t.issued_today} ${plural(t.issued_today, "заказ", "заказа", "заказов")}` : ""}`,
      cls: "calm",
      // Продавцу точки статистика магазина закрыта — ведём его в свои выданные.
      go: () => { if (isOwner()) openStats(); else { ordersStatusFilter = "issued"; openOrders(); } } },
    { n: need, lab: t.out_stock ? `надо завезти · ${t.out_stock} кончилось` : "надо завезти",
      cls: need ? "warn" : "calm", go: () => { admStockFilter = "need"; openProducts(); } },
  ];
  const quiet = !t.waiting && !need
    ? `<div class="tquiet">Ничего не ждёт — можно спокойно работать.</div>` : "";
  $("todayCard").innerHTML = `<div class="today">${tiles.map((x, i) =>
    `<button class="tcard ${x.cls}" data-t="${i}"><div class="tnum">${x.n}</div><div class="tlab">${x.lab}</div></button>`).join("")}</div>${quiet}`;
  $("todayCard").querySelectorAll("[data-t]").forEach(b => b.onclick = () => tiles[+b.dataset.t].go());
}

function renderLowBadge() {
  setBadge("lowBadge", shelf().filter(p => (!myScope() || p.city === myScope())
                                               && stockState(p) !== "ok").length);
}
function setBadge(id, n) {
  const b = $(id); if (!b) return;
  b.textContent = n; b.style.display = n ? "" : "none";
}
async function loadReqBadge() {
  try {
    const r = await fetch("/api/admin/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    const b = $("reqBadge"); if (!b || !d.ok) return;
    b.textContent = d.count; b.style.display = d.count ? "" : "none";
  } catch (e) {}
}
$("mRequests").onclick = openRequests;
$("requestsClose").onclick = () => $("requestsView").classList.remove("show");
async function openRequests() {
  $("requestsView").classList.add("show");
  await loadRequests();
}
async function loadRequests() {
  $("requestsList").innerHTML = `<div class="card-block" style="color:var(--hint)">Загрузка…</div>`;
  try {
    const r = await fetch("/api/admin/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) { $("requestsList").innerHTML = `<div class="card-block" style="color:var(--hint)">Нет доступа.</div>`; return; }
    if (!d.requests.length) { $("requestsList").innerHTML = `<div class="card-block" style="color:var(--hint);text-align:center">Нет ожидающих заявок 🎉</div>`; return; }
    $("requestsList").innerHTML = d.requests.map(q => `
      <div class="card-block" style="text-align:left">
        <div style="font-weight:800;margin-bottom:4px">${esc(q.summary)}</div>
        <div style="color:var(--hint);font-size:12px;margin-bottom:10px">От: ${esc(q.requester_name)} · id ${q.requester_id} · ${esc(q.created_at || "")}</div>
        <div style="display:flex;gap:8px">
          <button class="bigbtn reqok" data-id="${q.id}" style="flex:1">✅ Разрешить</button>
          <button class="bigbtn reqno" data-id="${q.id}" style="flex:1;background:var(--danger)">✖️ Отклонить</button>
        </div>
      </div>`).join("");
    document.querySelectorAll(".reqok").forEach(b => b.onclick = () => decideRequest(b.dataset.id, "approve"));
    document.querySelectorAll(".reqno").forEach(b => b.onclick = () => decideRequest(b.dataset.id, "reject"));
  } catch (e) { $("requestsList").innerHTML = `<div class="card-block" style="color:var(--hint)">Сеть недоступна.</div>`; }
}
async function decideRequest(id, decision) {
  try {
    const r = await fetch("/api/admin/request/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, request_id: id, decision }) });
    const d = await r.json();
    if (d.ok) { alertMsg(decision === "approve" ? "Разрешено ✅" : "Отклонено ✖️"); }
    else alertMsg(d.error === "already" ? "Заявка уже обработана." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  loadRequests(); loadReqBadge();
}
// Обычный админ инициирует чувствительную операцию → ждёт подтверждения супер-админа.
function handledPending(d) {
  if (d && d.ok && d.pending) { alertMsg("⏳ Запрос отправлен супер-админу на подтверждение."); return true; }
  return false;
}
$("adminClose").onclick = () => $("adminView").classList.remove("show");

// --- Разделы админки (открываются поверх хаба, «Назад» возвращает в меню) ---
function openOrders() { $("ordersView").classList.add("show"); loadAdminOrders(); }
$("mOrders").onclick = openOrders;
$("mProducts").onclick = openProducts;
$("mLocations").onclick = openLocations;
$("mBrands").onclick = openBrands;

// ---------- Ассортимент: модели ----------
// Модель описывается один раз. Товар на точке — это её наличие: цена,
// закупка, остаток. Раньше одна подсистема на трёх точках описывалась
// трижды, и три описания расходились после первой же правки.
let models = [], editingModelId = null, modelFlavors = [], modelPhotoFile = null, modelSearch = "";
$("mModels").onclick = openModels;
$("modelsClose").onclick = () => $("modelsView").classList.remove("show");
$("mdSearch").oninput = () => { modelSearch = $("mdSearch").value; renderModelList(); };

async function fetchModels() {
  try {
    const r = await fetch("/api/admin/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    models = d.ok ? d.models : [];
  } catch (e) { models = []; }
}
async function openModels() {
  $("modelsView").classList.add("show");
  // Продавец точки приходит сюда за одним — завезти модель к себе. Описание
  // модели общее для всех точек, поэтому форму и правку ему не показываем:
  // сервер их всё равно не примет.
  if ($("mdFormSect")) $("mdFormSect").style.display = isOwner() ? "" : "none";
  $("mdCat").innerHTML = CAT_OPTS.map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
  await Promise.all([fetchModels(), fetchBrands(), fetchFlavors()]);
  resetModelForm();
  renderModelList();
}
function renderModelForm() {
  const cat = $("mdCat").value;
  $("mdBrandBox").innerHTML = pickerHtml("mdBrand", $("mdBrand") ? pickerValue("mdBrand") : "", brandNames(cat), "+ Новый бренд…");
  bindPicker("mdBrand");
  $("mdSpecs").innerHTML = specFieldsHtml(cat, collectSpecs("mdSpecs"), "mds_");
  // Вкусы спрашиваем только там, где они есть: у зарядки их не бывает.
  $("mdFlavorsWrap").style.display = catHasFlavors(cat) ? "" : "none";
  $("mdFlavorsLabel").textContent = catVariantMany(cat);
  $("mdFlavorInput").placeholder = `Например: ${cat === "coils" ? "0.6 Ом" : cat === "podsystem" || cat === "devices" ? "Чёрный" : "Манго"}`;
  $("mdFlavorKnown").innerHTML = knownFlavors.map(f => `<option value="${esc(f)}">`).join("");
  renderModelFlavors();
}
$("mdCat").onchange = renderModelForm;
function renderModelFlavors() {
  $("mdFlavorChips").innerHTML = modelFlavors.map((f, i) =>
    `<span class="fchip">${esc(f)}<b data-mfx="${i}">✕</b></span>`).join("");
  $("mdFlavorChips").querySelectorAll("[data-mfx]").forEach(b =>
    b.onclick = () => { modelFlavors.splice(+b.dataset.mfx, 1); renderModelFlavors(); });
}
$("mdFlavorAdd").onclick = () => {
  const v = $("mdFlavorInput").value.trim();
  if (!v) return;
  v.split(",").map(x => x.trim()).filter(Boolean).forEach(f => {
    if (!modelFlavors.some(x => x.toLowerCase() === f.toLowerCase())) modelFlavors.push(f);
  });
  $("mdFlavorInput").value = ""; renderModelFlavors();
};
$("mdFlavorInput").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("mdFlavorAdd").click(); } };
$("mdPhoto").onchange = () => {
  const f = $("mdPhoto").files[0]; modelPhotoFile = f || null;
  if (f) { $("mdPhotoPrev").src = URL.createObjectURL(f); $("mdPhotoPrev").style.display = ""; }
};
// Дополнительные фото грузятся сразу, а у новой модели ещё нет id, к которому
// их привязать — поэтому блок появляется после первого сохранения.
function showModelGallery(on) {
  $("mdGalLabel").style.display = on ? "" : "none";
  $("mdGal").style.display = on ? "" : "none";
  if (on) renderEditGallery();
}
function resetModelForm() {
  editingModelId = null; modelFlavors = []; modelPhotoFile = null; editPhotos = [];
  showModelGallery(false);
  $("mdName").value = ""; $("mdDesc").value = ""; $("mdPhoto").value = "";
  $("mdPhotoPrev").style.display = "none"; $("mdPhotoPrev").removeAttribute("src");
  $("mdCancel").style.display = "none"; $("mdSave").textContent = "Сохранить модель";
  renderModelForm();
}
$("mdCancel").onclick = resetModelForm;

$("mdSave").onclick = async () => {
  const name = $("mdName").value.trim();
  if (!name) { alertMsg("Введите название модели."); return; }
  const brandName = pickerValue("mdBrand");
  await ensureBrandExists(brandName);
  const body = { initData, category: $("mdCat").value, name, brand: brandName,
                 description: $("mdDesc").value.trim(), specs: collectSpecs("mdSpecs"), flavors: modelFlavors };
  if (editingModelId) body.id = editingModelId;
  $("mdSave").disabled = true; $("mdSave").textContent = "Сохраняю…";
  try {
    const r = await fetch("/api/admin/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "exists"
        ? `Такая модель уже есть: «${d.name}». Правьте её, а не заводите вторую — иначе остатки и продажи разъедутся по двум карточкам.`
        : "Не удалось сохранить модель — проверьте название и категорию.");
      return;
    }
    if (modelPhotoFile) {
      const fd = new FormData();
      fd.append("initData", initData); fd.append("id", d.id); fd.append("file", modelPhotoFile);
      // Модель сохранится и без фото, поэтому не выходим: говорим про фото и
      // идём дальше. Молчать нельзя — снимок бы просто пропал.
      await админФайл("/api/admin/model/photo", fd, "загрузить фото модели");
    }
    const updated = d.updated, wasNew = !editingModelId;
    resetModelForm();
    await Promise.all([fetchModels(), fetchFlavors()]);
    await refreshAll();          // правка модели меняет и товары, и справочник вкусов
    renderModelList();
    // У новой модели галерея появляется только сейчас — id для неё уже есть.
    if (wasNew) editModel(d.id);
    const orphans = (d.orphans || []).filter(o => o.stock > 0);
    if (orphans.length) {
      // Вкус убрали из модели, а на полке он есть — он и дальше продаётся.
      alertMsg(`Сохранено ✅ Обновлено товаров на точках: ${updated}\n\n`
        + `На точках остались варианты, которых больше нет в модели: `
        + orphans.map(o => `${o.flavor} (${o.stock} шт)`).join(", ")
        + `.\nОни продолжают продаваться. Уберите их в «Товарах», если больше не возите.`);
    } else {
      alertMsg(updated ? `Сохранено ✅ Обновлено товаров на точках: ${updated}`
                       : "Модель сохранена ✅ Теперь можно добавить ещё фото и завезти её на точку.");
    }
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("mdSave").disabled = false; $("mdSave").textContent = editingModelId ? "Обновить модель" : "Сохранить модель"; }
};

function renderModelList() {
  const q = modelSearch.trim().toLowerCase();
  const list = models.filter(m => !q || `${m.name} ${m.brand}`.toLowerCase().includes(q));
  if (!list.length) {
    $("mdList").innerHTML = `<p style="color:var(--hint)">${models.length ? "Ничего не найдено." : "Ассортимент пуст — добавьте первую модель."}</p>`;
    return;
  }
  let html = "";
  for (const [code, cn] of группыКатегорий(list)) {
    const group = list.filter(m => (m.category || "") === code);
    if (!group.length) continue;
    html += `<div class="brgroup">${cn} · ${group.length}</div>`;
    html += group.map(m => {
      const specs = specsOf(m.category).map(s => {
        const v = (m.specs || {})[s.key];
        return (v === undefined || String(v).trim() === "") ? null : `${s.label}: ${withUnit(v, s.unit)}`;
      }).filter(Boolean).join(" · ");
      // «1 точка» не отвечало на вопрос, ради которого сюда и заходят: где
      // эта модель лежит и сколько её там. Показываем точки с остатком.
      const mine = shelf().filter(p => p.model_id === m.id);
      const where = mine.length
        ? mine.map(p => `${esc(p.city)} <b style="color:${p.stock <= 0 ? "var(--danger)" : p.stock <= LOW_STOCK ? "var(--warn)" : "inherit"}">${p.stock}</b>`).join(" · ")
        : `<span style="color:var(--danger)">нет ни на одной точке</span>`;
      // Снята везде — значит «больше не возим». Остаток и отзывы при этом целы.
      const allOff = mine.length && mine.every(p => p.hidden);
      const off = allOff ? ` · <span style="color:var(--hint)">снята с витрины</span>` : "";
      const own = !isOwner() ? "" :
        `<button class="iconbtn" data-mdhide="${m.id}" data-on="${allOff ? 1 : 0}"
                 title="${allOff ? 'Вернуть на витрину везде' : 'Снять с витрины на всех точках'}">${allOff ? '👁' : '🚫'}</button>
         <button class="iconbtn" data-mdedit="${m.id}">✏️</button>
         <button class="iconbtn danger" data-mddel="${m.id}">🗑</button>`;
      return `<div class="admrow">
        <div class="an">${m.brand ? esc(m.brand) + " " : ""}${esc(m.name)}
          <small>${where}${off}${specs ? " · " + esc(specs) : ""}${m.flavors.length ? ` · вариантов: ${m.flavors.length}` : ""}</small></div>
        ${own}</div>`;
    }).join("");
  }
  $("mdList").innerHTML = html;
  $("mdList").querySelectorAll("[data-mdedit]").forEach(b => b.onclick = () => editModel(+b.dataset.mdedit));
  $("mdList").querySelectorAll("[data-mddel]").forEach(b => b.onclick = () => delModel(+b.dataset.mddel));
  $("mdList").querySelectorAll("[data-mdhide]").forEach(b =>
    b.onclick = () => hideModel(+b.dataset.mdhide, b.dataset.on !== "1"));
}
// ----- Завоз: модель появляется на точке с ценой и остатком -----
let stockInModel = null;
$("stockInClose").onclick = () => $("stockInView").classList.remove("show");
function openStockIn(modelId) {
  stockInModel = models.find(m => m.id === modelId);
  if (!stockInModel) return;
  const m = stockInModel;
  $("stockInView").classList.add("show");
  $("stockInName").textContent = `${m.brand ? m.brand + " " : ""}${m.name}`;
  $("stockInMeta").textContent = catName(m.category);
  // Продавцу точки — только его точка: чужую сервер всё равно не примет.
  const points = myScope() ? locations.filter(l => l.name === myScope()) : locations;
  $("stockInCity").innerHTML = points.map(l => `<option value="${esc(l.name)}">${esc(l.name)}</option>`).join("");
  $("stockInPrice").value = ""; $("stockInCost").value = ""; $("stockInStock").value = "";
  $("stockInHit").checked = false;
  const withFlavors = m.flavors.length > 0;
  $("stockInFlavorsLabel").textContent = `${catVariantMany(m.category)} и остаток`;
  $("stockInFlavorsWrap").style.display = withFlavors ? "" : "none";
  $("stockInStockWrap").style.display = withFlavors ? "none" : "";
  $("stockInFlavors").innerHTML = m.flavors.map(f =>
    `<div class="admrow" data-flavor="${esc(f)}"><label class="an" style="display:flex;gap:8px;align-items:center;font-weight:600">
       <input type="checkbox" class="sifchk" style="width:auto" checked> ${esc(f)}</label>
     ${qtyHtml(0, 'class="sifst" placeholder="шт."')}</div>`).join("");
  bindQty($("stockInFlavors"));
  // Где модель уже стоит — чтобы не завезти второй раз на ту же точку.
  const where = shelf().filter(p => p.model_id === m.id);
  $("stockInWhere").innerHTML = where.length
    ? "Уже на точках: " + where.map(p => `${esc(p.city)} — ${p.price} Br, ${p.stock} шт`).join("; ")
    : "Пока нет ни на одной точке.";
}
$("stockInSave").onclick = async () => {
  if (!stockInModel) return;
  const price = $("stockInPrice").value.trim();
  if (!price) { alertMsg("Укажите цену."); return; }
  const city = $("stockInCity").value;
  if (shelf().some(p => p.model_id === stockInModel.id && p.city === city)) {
    alertMsg("На этой точке модель уже есть — правьте её в «Ценах и остатках».");
    return;
  }
  // Закупку спрашиваем здесь, а не «когда-нибудь потом»: незаполненная,
  // она навсегда выбрасывает товар из подсчёта прибыли, и отчёт занижает
  // заработок молча. Ноль принимаем — подарок и образец бывают, — но его
  // надо вписать руками.
  const cost = $("stockInCost").value.trim();
  if (!cost) {
    alertMsg("Впишите закупочную цену — без неё прибыль по этому товару не посчитается.\n\n" +
             "Если закупки не было (подарок, образец), поставьте 0.");
    $("stockInCost").focus();
    return;
  }
  const body = { initData, model_id: stockInModel.id, city, price,
                 cost, is_hit: $("stockInHit").checked };
  if (stockInModel.flavors.length) {
    body.variants = [...document.querySelectorAll("#stockInFlavors .admrow")]
      .filter(row => row.querySelector(".sifchk").checked)
      .map(row => ({ flavor: row.dataset.flavor, stock: row.querySelector(".sifst").value || "0" }));
    if (!body.variants.length) { alertMsg(`Отметьте хотя бы одно значение: ${catVariant(stockInModel.category)}.`); return; }
  } else {
    body.stock = $("stockInStock").value.trim() || "0";
  }
  $("stockInSave").disabled = true; $("stockInSave").textContent = "Добавляю…";
  try {
    const r = await fetch("/api/admin/product/from-model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "already_here" ? "На этой точке модель уже есть — правьте её в «Товарах»."
             : d.error === "bad_price" ? "Цена должна быть больше нуля."
             : "Не удалось завезти — проверьте цену и точку.");
      return;
    }
    await refreshProducts(); await fetchModels(); renderModelList();
    $("stockInView").classList.remove("show");
    alertMsg("Добавлено ✅");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("stockInSave").disabled = false; $("stockInSave").textContent = "Добавить на точку"; }
};

function editModel(id) {
  const m = models.find(x => x.id === id); if (!m) return;
  editingModelId = id; modelFlavors = [...m.flavors]; modelPhotoFile = null;
  $("mdCat").value = m.category;
  renderModelForm();
  $("mdName").value = m.name; $("mdDesc").value = m.description || "";
  if ($("mdBrand")) $("mdBrand").value = m.brand || "";
  $("mdSpecs").innerHTML = specFieldsHtml(m.category, m.specs, "mds_");
  if (m.thumb_url) { $("mdPhotoPrev").src = m.thumb_url; $("mdPhotoPrev").style.display = ""; }
  editPhotos = (m.gallery || []).map(g => ({ id: g.id, url: g.thumb || g.url }));
  showModelGallery(true);
  $("mdCancel").style.display = "block"; $("mdSave").textContent = "Обновить модель";
  $("mdFormSect").open = true;
  $("mdName").scrollIntoView({ behavior: "smooth", block: "center" });
}
// Снять модель с витрины на всех точках сразу — обычный ответ на «мы это
// больше не возим». Удаление на этот вопрос отвечает слишком грубо: уносит
// остаток, историю движений и отзывы.
async function hideModel(id, hidden) {
  const m = models.find(x => x.id === id);
  const ask = hidden
    ? `Снять «${m ? m.name : ""}» с витрины на всех точках? Покупатели её не увидят, остаток и отзывы останутся.`
    : `Вернуть «${m ? m.name : ""}» на витрину?`;
  confirmMsg(ask, async () => {
    try {
      const r = await fetch("/api/admin/model/hide", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, id, hidden }) });
      const d = await r.json();
      if (!d.ok) { alertMsg("Не получилось."); return; }
      await refreshProducts();
      renderModelList();
      toast(hidden ? `Снята с витрины · точек: ${d.count}` : `Снова продаётся · точек: ${d.count}`);
    } catch (e) { alertMsg(текстСбоя(e)); }
  });
}

function delModel(id, force) {
  const m = models.find(x => x.id === id);
  const ask = force
    ? `Модель есть на точках. Товары там останутся, но перестанут обновляться вместе с моделью. Всё равно убрать из ассортимента?`
    : `Убрать «${m ? m.name : ""}» из ассортимента?`;
  confirmMsg(ask, async () => {
    try {
      const r = await fetch("/api/admin/model/delete", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, id, force: !!force }) });
      const d = await r.json();
      if (!d.ok) {
        if (d.error === "has_products") { delModel(id, true); return; }
        alertMsg("Не удалось убрать модель.");
        return;
      }
      await fetchModels(); renderModelList();
      toast("Убрана из ассортимента");
    } catch (e) { alertMsg(текстСбоя(e)); }
  });
}

// Фильтры админ-списка товаров
let admSearch = "", admCatFilter = "all", admLocFilter = "all", admStockFilter = "all";
// Тот же порог, что и в «Статистике», иначе списки «мало» разойдутся между экранами.
const LOW_STOCK = 3;
const stockState = (p) => p.stock <= 0 ? "out" : (p.stock <= LOW_STOCK ? "low" : "ok");

function renderAdmFilters() {
  const cats = [["all", "Все"], ...CAT_OPTS];
  $("admCatChips").innerHTML = cats.map(([c, n]) =>
    `<button class="ochip ${admCatFilter === c ? 'active' : ''}" data-ac="${c}">${n}</button>`).join("");
  const locs = [["all", "Все точки"], ...locations.map(l => [l.name, l.name])];
  $("admLocChips").innerHTML = myScope() ? "" : locs.map(([c, n]) =>
    `<button class="ochip ${admLocFilter === c ? 'active' : ''}" data-al="${esc(c)}">${esc(n)}</button>`).join("");
  // «Что довезти» — главный вопрос к этому списку, а раньше на него отвечали
  // прокруткой всех точек подряд.
  const mine = shelf().filter(p => !myScope() || p.city === myScope());
  const nOut = mine.filter(p => stockState(p) === "out").length;
  const nLow = mine.filter(p => stockState(p) === "low").length;
  const st = [["all", "Любой остаток"], ["need", `Надо завезти${nOut + nLow ? ` · ${nOut + nLow}` : ""}`],
              ["out", `Кончились${nOut ? ` · ${nOut}` : ""}`]];
  $("admStockChips").innerHTML = st.map(([c, n]) =>
    `<button class="ochip ${admStockFilter === c ? 'active' : ''}" data-as="${c}">${n}</button>`).join("");
  $("admCatChips").querySelectorAll("[data-ac]").forEach(b =>
    b.onclick = () => { admCatFilter = b.dataset.ac; renderAdmFilters(); renderAdminList(); });
  $("admLocChips").querySelectorAll("[data-al]").forEach(b =>
    b.onclick = () => { admLocFilter = b.dataset.al; renderAdmFilters(); renderAdminList(); });
  $("admStockChips").querySelectorAll("[data-as]").forEach(b =>
    b.onclick = () => { admStockFilter = b.dataset.as; renderAdmFilters(); renderAdminList(); });
}
$("admSearch").oninput = () => { admSearch = $("admSearch").value; renderAdminList(); };

async function openProducts() {
  // Список читает shelf() — тот самый adminProducts, который заводит
  // openAdmin(). Тап сразу после открытия хаба мог обогнать этот запрос
  // и показать пустоту или прошлый визит; ждём тот же промис, а не свой.
  if (_adminBoot) await _adminBoot;
  renderAdmFilters();
  renderAdminList();
  $("productsView").classList.add("show");
}
$("productsClose").onclick = () => $("productsView").classList.remove("show");

async function openLocations() {
  $("locationsView").classList.add("show");
  await loadDelivery();
  renderLocList();
}
$("locationsClose").onclick = () => $("locationsView").classList.remove("show");

// ----- Категории товара -----
// Раньше «Расходники» появлялись только правкой кода и деплоем. Теперь
// владелец заводит раздел сам, и витрина перестраивается сразу.
$("mCats").onclick = openCats;
$("catsClose").onclick = () => $("catsView").classList.remove("show");
async function openCats() {
  $("catsView").classList.add("show");
  await fetchCategories();
  renderCatList();
}
let catEditing = null;      // код категории, которую сейчас переименовывают
function renderCatList() {
  $("catList").innerHTML = categories.map(c => {
    const used = shelf().filter(p => p.category === c.code).length;
    // Правка прямо в строке: окно prompt в Telegram открывается не везде,
    // и на таких кнопках это оборачивается «нажал — ничего не произошло».
    if (catEditing === c.code) {
      return `<div class="admrow">
        <input class="cate" value="${esc(c.emoji || "")}" placeholder="🍬" style="width:56px;text-align:center">
        <input class="catn" value="${esc(c.name)}" style="flex:1">
        <button class="iconbtn ok" data-csave="${esc(c.code)}">✓</button>
        <button class="iconbtn" data-ccancel="1">✕</button>
      </div>`;
    }
    const nspecs = (c.specs || []).length;
    return `<div class="admrow">
      <div class="an" data-cspecs="${esc(c.code)}" style="cursor:pointer">${c.emoji ? esc(c.emoji) + " " : ""}${esc(c.name)}<small>${used ? `${used} ${plural(used, "товар", "товара", "товаров")}` : "пусто"} · ${nspecs} ${plural(nspecs, "характеристика", "характеристики", "характеристик")} ›</small></div>
      <button class="iconbtn" data-cup="${esc(c.code)}" title="Выше">↑</button>
      <button class="iconbtn" data-crn="${esc(c.code)}" title="Переименовать">✏️</button>
      <button class="iconbtn danger" data-cdel="${esc(c.code)}">✕</button>
    </div>`;
  }).join("");
  $("catList").querySelectorAll("[data-cspecs]").forEach(el => el.onclick = () => openSpecs(el.dataset.cspecs));
  $("catList").querySelectorAll("[data-cup]").forEach(b => b.onclick = () => moveCat(b.dataset.cup, -1));
  $("catList").querySelectorAll("[data-crn]").forEach(b => b.onclick = () => { catEditing = b.dataset.crn; renderCatList(); });
  $("catList").querySelectorAll("[data-cdel]").forEach(b => b.onclick = () => delCat(b.dataset.cdel));
  $("catList").querySelectorAll("[data-ccancel]").forEach(b => b.onclick = () => { catEditing = null; renderCatList(); });
  $("catList").querySelectorAll("[data-csave]").forEach(b => b.onclick = () => {
    const row = b.closest(".admrow");
    saveCatName(b.dataset.csave, row.querySelector(".catn").value.trim(), row.querySelector(".cate").value.trim());
  });
}
async function saveCatName(code, name, emoji) {
  if (!name) { alertMsg("Название не может быть пустым."); return; }
  const d = await catApi("/api/admin/category/update", { code, name, emoji });
  if (!d.ok) { alertMsg("Не удалось переименовать."); return; }
  catEditing = null;
  await afterCatsChanged();
}
async function catApi(path, body) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, ...body }) });
    return await r.json();
  } catch (e) { alertMsg(текстСбоя(e)); return { ok: false }; }
}
$("catAdd").onclick = async () => {
  const name = $("catNameNew").value.trim();
  if (!name) { alertMsg("Введите название категории."); return; }
  const d = await catApi("/api/admin/category", { name, emoji: $("catEmojiNew").value.trim() });
  if (!d.ok) { alertMsg(d.error === "exists" ? "Такая категория уже есть." : "Не удалось добавить."); return; }
  $("catNameNew").value = ""; $("catEmojiNew").value = "";
  await afterCatsChanged();
  toast("Категория добавлена ✅");
};
async function moveCat(code, dir) {
  const i = categories.findIndex(c => c.code === code);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= categories.length) return;
  // Меняем местами веса соседей — так порядок задаётся одним понятным движением.
  const a = categories[i], b = categories[j];
  await catApi("/api/admin/category/update", { code: a.code, sort: b.sort });
  await catApi("/api/admin/category/update", { code: b.code, sort: a.sort });
  await afterCatsChanged();
}
function delCat(code) {
  const c = categories.find(x => x.code === code);
  confirmMsg(`Удалить категорию «${c ? c.name : code}»?`, async () => {
    const d = await catApi("/api/admin/category/delete", { code });
    if (!d.ok) {
      alertMsg(d.error === "has_products" ? `Сначала перенесите или уберите: в этой категории ${d.count} товаров и моделей.`
             : d.error === "last_one" ? "Это последняя категория — без неё товар не завести."
             : "Не удалось удалить.");
      return;
    }
    await afterCatsChanged();
    toast("Категория удалена");
  });
}
// Вернуть то, что удаляли. Добавляет ТОЛЬКО отсутствующее: свои категории и
// переименования владельца не трогает — иначе кнопка «вернуть» однажды
// затёрла бы чужую работу.
$("catRestore").onclick = () => confirmMsg(
  "Вернуть стандартные категории — одноразки, жидкости, подсистемы, расходники, устройства, аксессуары? Ваши собственные категории и названия останутся как есть.",
  async () => {
    const d = await админПост("/api/admin/category/restore", {}, "вернуть категории");
    if (!d) return;
    await afterCatsChanged();
    const n = (d.added || []).length;
    alertMsg(n ? `Возвращено категорий: ${n} ✅` : "Все стандартные категории уже на месте.");
  });

async function afterCatsChanged() {
  await refreshAll();          // фильтры каталога и формы админки строятся по категориям
  renderCatList();
}

// ----- Характеристики категории -----
let specsCat = null;
$("specsClose").onclick = () => $("specsView").classList.remove("show");
$("specKind").onchange = () => {
  $("specOptionsWrap").style.display = $("specKind").value === "select" ? "" : "none";
};
function openSpecs(code) {
  specsCat = code;
  $("specsView").classList.add("show");
  renderSpecList();
}
function renderSpecList() {
  const c = categories.find(x => x.code === specsCat);
  if (!c) { $("specsView").classList.remove("show"); return; }
  $("specsCatName").textContent = `${c.emoji || ""} ${c.name}`.trim();
  $("specFlavors").checked = !!c.has_flavors;
  // Слово нужно только тем категориям, у которых остаток вообще считается по
  // вариантам: у устройства без вариантов подписывать нечего.
  $("specVarWrap").style.display = c.has_flavors ? "" : "none";
  $("specVarLabel").value = c.variant_label || "Вкус";
  const list = c.specs || [];
  $("specList").innerHTML = list.length
    ? list.map(s => `<div class="admrow">
        <div class="an">${esc(s.label)}${s.unit ? ` <small style="display:inline">${esc(s.unit)}</small>` : ""}
          <small>${s.kind === "select" ? esc(s.options.join(" · ")) : s.kind === "number" ? "число" : "текст"}</small></div>
        <button class="iconbtn danger" data-sdel="${s.id}">✕</button></div>`).join("")
    : `<p style="color:var(--hint);margin:0">Характеристик нет — товар будет только с названием, ценой и фото.</p>`;
  $("specList").querySelectorAll("[data-sdel]").forEach(b => b.onclick = () => delSpec(+b.dataset.sdel));
}
$("specFlavors").onchange = async () => {
  await catApi("/api/admin/category/update", { code: specsCat, has_flavors: $("specFlavors").checked });
  await afterCatsChanged();
  renderSpecList();
};
// Слово сохраняем по уходу из поля, а не на каждую букву: иначе на «Сопр»
// успело бы уехать полдюжины запросов.
$("specVarLabel").onchange = async () => {
  const слово = $("specVarLabel").value.trim();
  if (!слово) { $("specVarLabel").value = "Вкус"; }
  await catApi("/api/admin/category/update", { code: specsCat, variant_label: $("specVarLabel").value.trim() || "Вкус" });
  await afterCatsChanged();
  renderSpecList();
  toast("Сохранено ✓");
};
$("specAdd").onclick = async () => {
  const label = $("specLabel").value.trim();
  if (!label) { alertMsg("Введите название характеристики."); return; }
  const kind = $("specKind").value;
  if (kind === "select" && !$("specOptions").value.trim()) { alertMsg("Перечислите варианты через запятую."); return; }
  const d = await catApi("/api/admin/category/spec", { category: specsCat, label,
    unit: $("specUnit").value.trim(), kind, options: $("specOptions").value.trim() });
  if (!d.ok) { alertMsg(d.error === "exists" ? "Такая характеристика уже есть." : "Не удалось добавить."); return; }
  $("specLabel").value = ""; $("specUnit").value = ""; $("specOptions").value = "";
  await afterCatsChanged();
  renderSpecList();
  toast("Характеристика добавлена ✅");
};
function delSpec(id) {
  // Значения у товаров остаются в базе: вернули поле — вернулись и они.
  confirmMsg("Убрать характеристику из категории?", async () => {
    const d = await catApi("/api/admin/category/spec/delete", { id });
    if (!d.ok) { alertMsg("Не удалось убрать."); return; }
    await afterCatsChanged();
    renderSpecList();
  });
}

async function openBrands() {
  // Список читает brands — тот же справочник, что заводит openAdmin();
  // сам этот раздел его не перечитывает. См. openProducts().
  if (_adminBoot) await _adminBoot;
  // «Во всех категориях» — первым: бренд обычно делает не одно, а всё сразу.
  $("brCat").innerHTML = `<option value="">Во всех категориях</option>`
    + CAT_OPTS.map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
  await fetchFlavors();
  renderKnownFlavors();
  renderBrandList();
  $("brandsView").classList.add("show");
}
// Ранее введённые вкусы под рукой: без них одна и та же «Мята» набирается
// каждый раз заново и в фильтре покупателя дробится на несколько.
function renderKnownFlavors() {
  const dl = $("brFlavorKnown");
  if (dl) dl.innerHTML = knownFlavors.map(f => `<option value="${esc(f)}">`).join("");
  const box = $("brKnownFlavors");
  if (!box) return;
  const free = knownFlavors.filter(f => !brandFlavors.includes(f)).slice(0, 24);
  box.innerHTML = free.length
    ? `<span style="color:var(--hint);font-size:12px;width:100%">Уже встречались — нажмите, чтобы добавить:</span>`
      + free.map(f => `<span class="brchip" data-kf="${esc(f)}" style="cursor:pointer">${esc(f)}</span>`).join("")
    : "";
  box.querySelectorAll("[data-kf]").forEach(el => el.onclick = () => {
    if (!brandFlavors.includes(el.dataset.kf)) brandFlavors.push(el.dataset.kf);
    renderFlavorChips(); renderKnownFlavors();
  });
}
$("brandsClose").onclick = () => $("brandsView").classList.remove("show");

// ----- Статистика -----
const STATUS_RU = { new: "Ждут чек", paid: "Ждут подтверждения", confirmed: "Подтверждены", issued: "Выданы", canceled: "Отклонены" };
$("mStats").onclick = openStats;
$("statsClose").onclick = () => $("statsView").classList.remove("show");
let statsPeriod = "30d";
const PERIOD_LABEL = { today: "сегодня", "7d": "7 дней", "30d": "30 дней", all: "всё время" };
async function openStats() {
  $("statsView").classList.add("show");
  await loadStats();
}
function periodSelHtml() {
  return `<div class="periodsel">` + ["today", "7d", "30d", "all"].map(p =>
    `<button class="periodbtn ${p === statsPeriod ? "on" : ""}" data-period="${p}">${{ today: "Сегодня", "7d": "7 дней", "30d": "30 дней", all: "Всё" }[p]}</button>`).join("") + `</div>`;
}
// Вертикальный столбчатый график по дням.
function dayChart(daily, valKey, title, fmt) {
  const vals = daily.map(d => d[valKey]);
  const max = Math.max(1, ...vals);
  const total = vals.reduce((a, b) => a + b, 0);
  const bars = daily.map(d => `<div class="cbar"><i style="height:${Math.round(d[valKey] / max * 100)}%"></i></div>`).join("");
  const dl = (s) => s ? s.slice(8, 10) + "." + s.slice(5, 7) : "";
  return `<div class="chartwrap">
    <div class="charttop"><b>${title}</b><span>всего ${fmt(total)} · пик ${fmt(max)}</span></div>
    <div class="chart">${bars}</div>
    <div class="chartx"><span>${dl(daily[0] && daily[0].date)}</span><span>${dl(daily[daily.length - 1] && daily[daily.length - 1].date)}</span></div>
  </div>`;
}
// Горизонтальные полоски (топ товаров / города).
function hbars(rows) {
  const max = Math.max(1, ...rows.map(r => r.value));
  return rows.map(r => `<div class="hbar"><div class="hbar-l">${esc(r.label)}</div><div class="hbar-t"><i style="width:${Math.round(r.value / max * 100)}%"></i></div><div class="hbar-v">${r.sub}</div></div>`).join("");
}
async function loadStats() {
  $("statsBody").innerHTML = periodSelHtml() + `<p style="color:var(--hint)">Загрузка…</p>`;
  bindPeriodBtns();
  let s;
  try {
    const r = await fetch("/api/admin/stats", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, period: statsPeriod }) });
    const d = await r.json();
    if (!d.ok) { $("statsBody").innerHTML = periodSelHtml() + `<p style="color:var(--hint)">Не удалось загрузить.</p>`; bindPeriodBtns(); return; }
    s = d.stats;
  } catch (e) { $("statsBody").innerHTML = periodSelHtml() + `<p style="color:var(--hint)">Сеть недоступна.</p>`; bindPeriodBtns(); return; }

  const money = (v) => `${(+v).toFixed(2)} ${CUR}`;
  const moneyShort = (v) => `${Math.round(+v)} Br`;
  const repeatPct = s.total_buyers ? Math.round(s.repeat_buyers / s.total_buyers * 100) : 0;
  // Дельта к прошлому периоду: ▲/▼ % (без сравнения при period=all).
  const delta = (cur, key) => {
    if (!s.prev) return "";
    const p = s.prev[key];
    if (p == null) return "";
    if (p === 0) return cur > 0 ? `<div class="statdelta up">▲ ново</div>` : `<div class="statdelta flat">— 0</div>`;
    const pct = Math.round((cur - p) / p * 100);
    const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    const arr = pct > 0 ? "▲" : pct < 0 ? "▼" : "—";
    return `<div class="statdelta ${cls}">${arr} ${Math.abs(pct)}% <span style="color:var(--hint);font-weight:500">пред.</span></div>`;
  };
  const statusRows = Object.entries(s.by_status || {})
    .map(([k, v]) => `<div class="statrow"><span>${STATUS_RU[k] || k}</span><b>${v}</b></div>`).join("") || `<div class="statrow"><span>Заказов нет</span></div>`;
  const topRows = (s.top || []).length
    ? hbars(s.top.map(t => ({ label: t.name, value: t.qty,
        // Прибыль по товару показываем, только если закупка заполнена:
        // прочерк честнее нуля, который выглядит как «не заработали».
        sub: `${t.qty} шт · ${moneyShort(t.revenue)}${t.profit === null ? "" : ` · +${moneyShort(t.profit)}`}` })))
    : `<div class="statrow"><span style="color:var(--hint)">Пока нет продаж</span></div>`;
  const cityRows = (s.revenue_by_city || []).length
    ? hbars(s.revenue_by_city.map(c => ({ label: c.city, value: c.total, sub: money(c.total) })))
    : `<div class="statrow"><span style="color:var(--hint)">Ещё нет выданных заказов</span></div>`;
  const outR = (s.out_stock || []).map(p => `<div class="statrow"><span>${esc(p.name)} · ${esc(p.city)}</span><b style="color:var(--danger)">нет в наличии</b></div>`).join("");
  const lowR = (s.low_stock || []).map(p => `<div class="statrow"><span>${esc(p.name)} · ${esc(p.city)}</span><b style="color:var(--warn)">осталось ${p.stock} шт</b></div>`).join("");
  const stockRows = (outR + lowR) || `<div class="statrow"><span style="color:var(--hint)">Всё в достатке — пополнять не нужно 👍</span></div>`;
  const pl = PERIOD_LABEL[statsPeriod];

  $("statsBody").innerHTML = periodSelHtml() + `
    <div class="statgrid">
      <div class="statcard"><div class="statnum">${money(s.revenue)}</div><div class="statlab">Выручка · ${pl}</div>${delta(s.revenue, "revenue")}</div>
      <div class="statcard"><div class="statnum" style="color:#1f8a5f">${money(s.profit)}</div><div class="statlab">Прибыль${s.margin ? ` · наценка ${s.margin}%` : ""}</div></div>
      <div class="statcard"><div class="statnum">${s.orders}</div><div class="statlab">Заказов · ${pl}</div>${delta(s.orders, "orders")}</div>
      <div class="statcard"><div class="statnum">${money(s.avg_check)}</div><div class="statlab">Средний чек</div>${delta(s.avg_check, "avg_check")}</div>
      <div class="statcard"><div class="statnum">${s.buyers_period}</div><div class="statlab">Покупателей · ${pl}</div>${delta(s.buyers_period, "buyers")}</div>
    </div>
    ${(s.losses || []).length ? `<div class="stathead">📉 Списано <span class="stathint">${pl}</span></div><div class="statlist">${
        s.losses.map(l => `<div class="statrow"><span>${esc({broken:"Брак или бой",expired:"Просрочка",lost:"Недостача",gift:"Подарок или образец",fix:"Пересчёт"}[l.reason] || l.reason)}</span><b style="color:var(--danger)">${l.qty} шт${l.money ? ` · ${money(l.money)}` : ""}</b></div>`).join("")
      }</div>` : ""}
    ${s.no_cost_total ? `<div class="dnote" style="margin:-6px 0 14px">⚠️ Без закупочной цены: <b>${s.no_cost_total}</b> ${plural(s.no_cost_total, "товар", "товара", "товаров")}${s.revenue_unknown_cost ? ` — выручка на ${money(s.revenue_unknown_cost)} в прибыль не попала` : ""}.
      <div class="nocostlist">${s.no_cost.map(p => `<span class="nocostitem">${esc(p.name)} <i>${esc(p.city)}</i></span>`).join("")}${s.no_cost_total > s.no_cost.length ? `<span class="nocostitem">и ещё ${s.no_cost_total - s.no_cost.length}</span>` : ""}</div>
      Проставьте закупку в карточках этих товаров — тогда прибыль станет настоящей.</div>` : ""}
    ${dayChart(s.daily || [], "revenue", "Выручка по дням", moneyShort)}
    ${dayChart(s.daily || [], "orders", "Заказы по дням", (v) => `${v} шт`)}
    <div class="stathead">🏆 Топ товаров <span class="stathint">${pl}</span></div><div class="statlist">${topRows}</div>
    <div class="stathead">🏙 Выручка по точкам <span class="stathint">${pl}</span></div><div class="statlist">${cityRows}</div>
    <div class="stathead">📦 Заказы по статусам <span class="stathint">${pl}</span></div><div class="statlist">${statusRows}
      <div class="statrow"><span>В работе (ждут выдачи)</span><b>${money(s.inwork_total)}</b></div></div>
    <div class="stathead">👥 Пользователи</div><div class="statlist">
      <div class="statrow"><span>Всего пользователей</span><b>${s.users_total}</b></div>
      <div class="statrow"><span>Новых · ${pl}</span><b>${s.new_users}</b></div>
      <div class="statrow"><span>Покупали хоть раз</span><b>${s.total_buyers}</b></div>
      <div class="statrow"><span>Повторные покупатели</span><b>${s.repeat_buyers} (${repeatPct}%)</b></div>
    </div>
    <div class="stathead">🪙 Монеты лояльности <span class="stathint">клиенты копят и тратят скидкой · 100 монет = 1 Br</span></div>
    <div class="statlist">
      ${(s.coins && (s.coins.granted || s.coins.spent)) ? `
        <div class="statrow"><span>Роздано · ${pl}</span><b>${s.coins.granted} 🪙 · ${money(s.coins.granted * (s.coin_value || 0.01))}</b></div>
        <div class="statrow"><span>Вернулось скидками и ставками · ${pl}</span><b>${s.coins.spent} 🪙 · ${money(s.coins.spent * (s.coin_value || 0.01))}</b></div>
        ${s.coins.by_reason.map(r => `<div class="statrow"><span style="padding-left:12px;color:var(--hint)">${esc(r.label)}</span><b>${r.granted ? `+${r.granted}` : ""}${r.granted && r.spent ? " / " : ""}${r.spent ? `−${r.spent}` : ""} 🪙</b></div>`).join("")}` : ""}
      <div class="statrow"><span>Накоплено у всех клиентов</span><b>${s.coins_circulation} 🪙</b></div>
      <div class="statrow"><span>Если все потратят — скидок на</span><b style="color:var(--warn)">${money(s.coins_circulation / 100)}</b></div>
    </div>
    ${(s.coins && s.coins.granted) ? `<div class="dnote" style="margin:-6px 0 14px">«Роздано» — сколько магазин начислил за период; «вернулось» — сколько из этого уже потрачено покупателями. Разница оседает на балансах и будет предъявлена скидкой позже.</div>` : ""}
    <div class="stathead">🎮 Игры на монеты <span class="stathint">колесо (бесплатно за покупки) и слот (за монеты)</span></div>
    <div class="statlist">${gamesRows(s.games || {})}</div>
    <div class="stathead">📦 Склад <span class="stathint">пополни, чтобы не терять продажи</span></div>
    <div class="statlist">${stockRows}</div>
    <button class="closebtn" id="exportStats" style="margin-top:18px">📄 Выгрузить заказы в файл</button>
    <div class="dnote" style="margin:6px 0 0">Придёт документом в чат с ботом — за выбранный период, строка на каждую позицию. Открывается в Excel.</div>
    ${(me && me.is_super) ? `<button class="closebtn" id="resetStats" style="color:var(--danger);margin-top:18px">🧹 Сбросить статистику (тестовые данные)</button>` : ""}`;
  bindPeriodBtns();
  if ($("exportStats")) $("exportStats").onclick = exportStats;
  if ($("resetStats")) $("resetStats").onclick = resetStats;
}
// Файл уходит в чат, а не скачивается: внутри Telegram скачивание то работает,
// то молча не делает ничего. Поэтому и текст кнопки обещает чат, а не «Скачать».
async function exportStats() {
  const b = $("exportStats");
  b.disabled = true; b.textContent = "Готовлю файл…";
  try {
    const r = await fetch("/api/admin/stats/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, period: statsPeriod }) });
    const d = await r.json();
    if (d.ok) alertMsg(`Готово ✅ ${d.rows} ${plural(d.rows, "заказ", "заказа", "заказов")} — файл придёт в чат с ботом.`);
    else alertMsg(d.message || "Не удалось выгрузить.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { b.disabled = false; b.textContent = "📄 Выгрузить заказы в файл"; }
}
function resetStats() {
  const go = async () => {
    try {
      const r = await fetch("/api/admin/stats/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
      const d = await r.json();
      if (d.ok) { alertMsg(`Готово ✅ Удалено заказов: ${d.orders}. Статистика обнулена.`); loadStats(); }
      else alertMsg(d.error === "forbidden" ? "Только для супер-админа." : "Не удалось.");
    } catch (e) { alertMsg(текстСбоя(e)); }
  };
  const msg = "Удалить ВСЕ заказы и обнулить счётчики игр? Отменить нельзя.";
  confirmMsg(msg, go);
}
function bindPeriodBtns() {
  document.querySelectorAll("#statsBody .periodbtn").forEach(b => b.onclick = () => {
    if (b.dataset.period === statsPeriod) return;
    statsPeriod = b.dataset.period; haptic("impact", "light"); loadStats();
  });
}
function gamesRows(g) {
  const w = { spins: g.wheel_spins || 0, paid: g.wheel_paid || 0 };
  const sl = { spins: g.slot_spins || 0, bet: g.slot_bet || 0, paid: g.slot_paid || 0 };
  const net = sl.bet - sl.paid;   // ставки − выплаты = осталось у заведения
  return `
    <div class="statrow"><span>🎡 Колесо — прокрутили раз</span><b>${w.spins}</b></div>
    <div class="statrow"><span>🎡 Колесо — раздали монет</span><b>${w.paid} 🪙</b></div>
    <div class="statrow"><span>🎰 Слот — прокрутили раз</span><b>${sl.spins}</b></div>
    <div class="statrow"><span>🎰 Слот — поставили монет</span><b>${sl.bet} 🪙</b></div>
    <div class="statrow"><span>🎰 Слот — выиграли монет</span><b>${sl.paid} 🪙</b></div>
    <div class="statrow"><span>🎰 Слот — осталось у заведения</span><b style="color:${net >= 0 ? '#2e9e4f' : 'var(--danger)'}">${net} 🪙</b></div>`;
}

// ----- Розыгрыш (админ) -----
let raffleRunning = false, raffleУчастников = 0;
$("mRaffle").onclick = openRaffleAdmin;
$("raffleAdminClose").onclick = () => $("raffleAdminView").classList.remove("show");
async function openRaffleAdmin() {
  $("raffleAdminView").classList.add("show");
  try {
    const r = await fetch("/api/admin/raffle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) return;
    const ra = d.raffle;
    // Розыгрыш идёт только тогда, когда его начали. Пока не начали —
    // показываем заготовку и одну кнопку, а не настройки того, чего нет.
    raffleRunning = !!ra;
    raffleУчастников = ra ? (ra.participants || 0) : 0;
    $("raTitle").value = ra ? (ra.title || "") : "Розыгрыш месяца";
    $("raPrize1").value = ra ? (ra.prize1 || "") : "Одноразка";
    $("raPrize2").value = ra ? (ra.prize2 || "") : "Жидкость";
    $("raPrize3").value = ra ? (ra.prize3_coins || 500) : 500;
    $("raThreshold").value = ra ? (ra.threshold || 25) : 25;
    $("raDays").value = 30;
    $("raPhotoRow").style.display = raffleRunning ? "" : "none";
    const прев = $("raPhotoPrev");
    if (raffleRunning && ra.photo) { прев.src = `/api/photo?file_id=${encodeURIComponent(ra.photo)}`; прев.style.display = ""; }
    else { прев.style.display = "none"; }
    $("raPhoto").value = "";
    $("raSave").style.display = raffleRunning ? "" : "none";
    $("raDraw").style.display = raffleRunning ? "" : "none";
    $("raStart").style.display = raffleRunning ? "none" : "";
    $("raDaysRow").style.display = raffleRunning ? "none" : "";
    $("raffleAdminInfo").innerHTML = raffleRunning
      ? `Участников: <b>${ra.participants}</b> · до конца: ${raffleTimeLeft(ra.ends_at)}`
      : `Сейчас розыгрыш не идёт — вкладка «Розыгрыши» у покупателей скрыта.`;
  } catch (e) { alertMsg(текстСбоя(e)); }
}
$("raSave").onclick = async () => {
  const body = { initData, title: $("raTitle").value, prize1: $("raPrize1").value, prize2: $("raPrize2").value,
    prize3_coins: $("raPrize3").value, threshold: $("raThreshold").value };
  $("raSave").disabled = true; $("raSave").textContent = "Сохраняю…";
  try {
    const r = await fetch("/api/admin/raffle/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    alertMsg(d.ok ? "Сохранено ✅" : "Не удалось сохранить.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("raSave").disabled = false; $("raSave").textContent = "Сохранить"; }
};
$("raPhoto").onchange = async () => {
  const f = $("raPhoto").files[0];
  if (!f) return;
  const прев = $("raPhotoPrev");
  прев.src = URL.createObjectURL(f); прев.style.display = "";
  const fd = new FormData();
  fd.append("initData", initData); fd.append("file", f);
  try {
    const r = await fetch("/api/admin/raffle/photo", { method: "POST", body: fd });
    const d = await r.json();
    alertMsg(d.ok ? "Фото приза сохранено ✅"
                  : (d.error === "no_raffle" ? "Сначала начните розыгрыш." : "Не удалось загрузить."));
  } catch (e) { alertMsg(текстСбоя(e)); }
};
$("raStart").onclick = async () => {
  const body = { initData, title: $("raTitle").value, prize1: $("raPrize1").value,
    prize2: $("raPrize2").value, prize3_coins: $("raPrize3").value,
    threshold: $("raThreshold").value, days: $("raDays").value };
  $("raStart").disabled = true; $("raStart").textContent = "Начинаю…";
  try {
    const r = await fetch("/api/admin/raffle/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.ok) { alertMsg("Розыгрыш начат ✅"); openRaffleAdmin(); }
    else alertMsg(d.error === "already" ? "Розыгрыш уже идёт." : "Не удалось начать.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("raStart").disabled = false; $("raStart").textContent = "Начать розыгрыш"; }
};
$("raDraw").onclick = () => {
  const go = async () => {
    if (!await админПост("/api/admin/raffle/draw", {}, "подвести итоги")) return;
    alertMsg("Итоги подведены, розыгрыш завершён ✅"); openRaffleAdmin();
  };
  // Сколько участников — здесь же: подвести итоги при нуле значит закрыть
  // розыгрыш без победителей, и узнать об этом потом будет неоткуда.
  const сколько = raffleУчастников;
  confirmMsg(`Подвести итоги сейчас и завершить розыгрыш? Участников: ${сколько}. `
             + "Новый начнётся только когда вы его начнёте.", go);
};

// ----- Пользователи и рефералы (админ) -----
$("mReferrals").onclick = openReferralsAdmin;
$("referralAdminClose").onclick = () => $("referralAdminView").classList.remove("show");
let _userSearchTimer = null;
$("userSearch").oninput = () => { clearTimeout(_userSearchTimer); _userSearchTimer = setTimeout(loadAllUsers, 300); };
async function openReferralsAdmin() {
  $("referralAdminView").classList.add("show");
  $("unrefId").value = ""; $("unrefResult").textContent = ""; $("userSearch").value = "";
  $("coinUserId").value = ""; $("coinAmount").value = ""; $("spinAmount").value = ""; $("coinResult").textContent = "";
  await Promise.all([loadAllUsers(), loadMyReferrals()]);
}
function refreshUsersAdmin() { loadAllUsers(); loadMyReferrals(); }
function toast(msg) {
  let t = document.getElementById("miniToast");
  if (!t) { t = document.createElement("div"); t.id = "miniToast"; t.className = "minitoast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 1400);
}
// Тап по ID: копируем в буфер И подставляем во все админ-поля (монеты/отвязка/удаление).
function copyUserId(id) {
  try { navigator.clipboard && navigator.clipboard.writeText(String(id)); } catch (e) {}
  ["coinUserId", "unrefId"].forEach(f => { if ($(f)) $(f).value = id; });
  haptic("impact", "light");
  toast(`ID ${id} скопирован ✓`);
}
function bindIdCopy(scope) {
  (scope || document).querySelectorAll(".idcopy").forEach(el => el.onclick = (e) => { e.stopPropagation(); copyUserId(el.dataset.copy); });
}
let _usersById = {};
// Админ пишет клиенту (по id).
let _msgTargetId = null;
function openAdminMsg(userId, label) {
  _msgTargetId = userId;
  $("msgTo").textContent = label || `Клиент id ${userId}`;
  $("msgText").value = "";
  $("msgOverlay").classList.add("show");
}
$("msgClose").onclick = () => closeOverlay($("msgOverlay"));
$("msgSend").onclick = async () => {
  const text = $("msgText").value.trim();
  if (!text || !_msgTargetId) { alertMsg("Напишите сообщение."); return; }
  $("msgSend").disabled = true; $("msgSend").textContent = "Отправляю…";
  try {
    const r = await fetch("/api/admin/message", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: _msgTargetId, text }) });
    const d = await r.json();
    if (d.ok && d.sent) { closeOverlay($("msgOverlay")); alertMsg("Отправлено клиенту ✅"); }
    else if (d.ok) alertMsg("Клиент не получит — он не запускал бота.");
    else alertMsg(d.error === "forbidden" ? "Нет доступа." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("msgSend").disabled = false; $("msgSend").textContent = "Отправить"; }
};

// ----- Компенсация покупателю -----
// Единственное денежное действие продавца, и то с подтверждением владельца:
// разбитый под или задержка — это к продавцу, а не к владельцу в личку.
let _compOrderId = null;
function openCompensation(orderId, who) {
  _compOrderId = orderId;
  $("compTo").textContent = `Заказ #${orderId} · ${who || ""}`;
  $("compCoins").value = ""; $("compReason").value = "";
  $("compHint").textContent = "";
  $("compOverlay").classList.add("show");
}
$("compClose").onclick = () => closeOverlay($("compOverlay"));
// Монеты — не деньги на вид: показываем, во что это обходится магазину.
$("compCoins").oninput = () => {
  const n = +$("compCoins").value || 0;
  $("compHint").textContent = n > 0 ? `Это ${(n * 0.01).toFixed(2)} Br скидки в следующем заказе` : "";
};
$("compSend").onclick = async () => {
  const coins = +$("compCoins").value || 0;
  if (coins < 1) { alertMsg("Укажите, сколько монет начислить."); return; }
  $("compSend").disabled = true; $("compSend").textContent = "Отправляю…";
  try {
    const r = await fetch("/api/admin/order/compensate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, order_id: _compOrderId, coins, reason: $("compReason").value.trim() }) });
    const d = await r.json();
    if (d.ok && d.pending) { closeOverlay($("compOverlay")); alertMsg("Отправлено владельцу на подтверждение ⏳"); }
    else if (d.ok) { closeOverlay($("compOverlay")); alertMsg("Монеты начислены покупателю ✅"); }
    else alertMsg(d.message || (d.error === "other_city" ? "Это заказ другой точки." : "Не удалось."));
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("compSend").disabled = false; $("compSend").textContent = "Начислить"; }
};

// «5 покупки» выдаёт машину с головой. Русский счёт: 1 покупка, 2 покупки, 5 покупок.
function plural(n, one, few, many) {
  const d = Math.abs(n) % 10, dd = Math.abs(n) % 100;
  if (d === 1 && dd !== 11) return one;
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return few;
  return many;
}
// База отдаёт «2026-08-17 17:30» — человеку читать удобнее «17.08.2026, 17:30».
function whenRu(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/.exec(String(s || ""));
  return m ? `${m[3]}.${m[2]}.${m[1]}, ${m[4]}` : (s || "—");
}
// Карточка покупателя (тап по строке): кто это, что берёт и сколько принёс.
async function showUserCard(u) {
  if (!u) return;
  const who = (u.username ? `@${esc(u.username)}` : `ID ${u.id}`) + (u.super ? " 🛡" : "");
  showInfo(who, `<div style="color:var(--hint)">Загрузка…</div>`);
  let card = null;
  try {
    const r = await fetch("/api/admin/customer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: u.id }) });
    const d = await r.json();
    card = d.ok ? d.card : null;
  } catch (e) { card = null; }
  // Не открылась история — показываем то, что уже знаем из списка, а не пустоту.
  const c = card || { ...u, issued: u.orders, spent: u.spent, avg_check: 0, favorites: [], history: [],
                      coins: u.coins, phone: "", point: "", days_since: null, profit: 0, profit_known: false,
                      canceled: 0, open: 0, orders_total: u.orders, first_buy: "", last_buy: u.last_order || "" };

  // Одна строка сверху, по которой сразу понятно, как себя вести с человеком.
  let flag = `<div class="ccflag calm">Пока не покупал</div>`;
  if (c.issued >= 3) flag = `<div class="ccflag good">Постоянный — ${c.issued} ${plural(c.issued, "покупка", "покупки", "покупок")}</div>`;
  else if (c.issued >= 1) flag = `<div class="ccflag calm">Покупал ${c.issued} ${plural(c.issued, "раз", "раза", "раз")}</div>`;
  if (c.days_since !== null && c.days_since >= 30) flag = `<div class="ccflag warn">Молчит ${c.days_since} ${plural(c.days_since, "день", "дня", "дней")} — можно напомнить</div>`;

  const tiles = `<div class="cctiles">
    <div class="cctile"><div class="v">${(c.spent || 0).toFixed(2)} Br</div><div class="k">Принёс за всё время</div></div>
    <div class="cctile"><div class="v">${(c.avg_check || 0).toFixed(2)} Br</div><div class="k">Средний чек</div></div>
    <div class="cctile"><div class="v">${c.issued || 0}</div><div class="k">Выдано заказов${c.canceled ? `<small>отклонено: ${c.canceled}</small>` : ""}${c.open ? `<small>в работе: ${c.open}</small>` : ""}</div></div>
    <div class="cctile"><div class="v">${c.profit_known ? (c.profit || 0).toFixed(2) + " Br" : "—"}</div><div class="k">Заработали на нём${c.profit_known ? "" : `<small>${c.issued ? "нет закупочных цен" : "покупок ещё не было"}</small>`}</div></div>
  </div>`;

  const fav = (c.favorites || []).length
    ? `<div class="cchead">Что берёт</div><div class="ccfav">${c.favorites.map(f => `<span>${esc(f.name)} · ${f.qty} шт</span>`).join("")}</div>`
    : "";

  const rows = [
    ["Баланс", `${c.coins} 🪙`],
    ["Прокруты колеса", `${u.wheel_spins} 🎡`],
    ["Телефон", c.phone || "—"],
    ["Своя точка самовывоза", c.point || "—"],
    ["Первая покупка", c.first_buy ? whenRu(c.first_buy) : "—"],
    ["Последняя покупка", c.last_buy ? whenRu(c.last_buy) : "—"],
    ["Рефералов", `${c.referrals}`],
    ["Пригласил", c.referred_by ? `id ${c.referred_by}` : "—"],
    ["Заработано на рефералах", `${c.ref_earned || 0} 🪙`],
    ["18+", c.age_ok ? "да" : "нет"],
    ["Напоминания", c.no_reminders ? "отписался" : "получает"],
  ].map(([k, v]) => `<div class="statrow"><span>${k}</span><b>${esc(String(v))}</b></div>`).join("");

  const hist = (c.history || []).length
    ? `<div class="cchead">История заказов</div>` + c.history.map(o => {
        const st = OSTATUS[o.status] || { label: o.status, cls: "new" };
        const items = (o.items || []).map(i => esc(i.name) + (i.flavor ? ` — ${esc(i.flavor)}` : "") + ` ×${i.qty}`).join(", ");
        return `<div class="ccorder">
          <div class="top"><span class="when">№${o.id} · ${esc(whenRu(o.created_at))}</span><span class="obadge ${st.cls}">${st.label}</span></div>
          <div class="top" style="margin-top:4px"><span class="items">${items || "—"}</span><span class="sum">${o.total.toFixed(2)} Br</span></div>
        </div>`;
      }).join("")
      + (c.orders_total > c.history_shown ? `<div style="color:var(--hint);font-size:12px;margin-top:8px">Показаны последние ${c.history_shown} из ${c.orders_total}.</div>` : "")
    : `<div class="cchead">История заказов</div><div style="color:var(--hint);font-size:13px">Заказов ещё не было.</div>`;

  const acts = `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
    <button class="bigbtn" id="ucMsg" style="flex:1 1 100%">✍️ Написать клиенту</button>
    <button class="bigbtn" id="ucCoins" style="flex:1 1 100%;background:var(--surface-2);color:var(--text)">💰 Изменить монеты / прокруты</button>
    ${(c.referred_by && !u.super) ? `<button class="closebtn ucUnref" style="flex:1;color:var(--danger)">Отвязать реферала</button>` : ""}
    ${!u.super ? `<button class="closebtn ucDel" style="flex:1;color:var(--danger)">🗑 Удалить</button>` : `<div style="flex:1;color:var(--hint);text-align:center;align-self:center">🛡 защищён</div>`}
  </div>`;

  showInfo(who, `<div style="color:var(--hint);font-size:12px;margin-bottom:8px"><span class="idcopy" data-copy="${u.id}">ID ${u.id} 📋</span></div>`
    + flag + tiles + fav + `<div class="cchead">Профиль</div>` + rows + hist + acts);
  bindIdCopy($("infoBody"));
  const close = () => closeOverlay($("infoOverlay"));
  if ($("ucMsg")) $("ucMsg").onclick = () => { close(); openAdminMsg(u.id, who); };
  if ($("ucCoins")) $("ucCoins").onclick = () => { ["coinUserId", "unrefId"].forEach(f => { if ($(f)) $(f).value = u.id; }); close(); toast(`ID ${u.id} подставлен в «Монеты и прокруты»`); };
  document.querySelectorAll(".ucUnref").forEach(b => b.onclick = () => { close(); doUnref(u.id); });
  document.querySelectorAll(".ucDel").forEach(b => b.onclick = () => { close(); confirmDelUser(u.id); });
}
async function adjustCoins(sign) {
  const id = $("coinUserId").value.trim();
  const amt = parseInt($("coinAmount").value, 10);
  if (!id) { alertMsg("Введите ID."); return; }
  if (!amt || amt <= 0) { alertMsg("Введите количество монет."); return; }
  const delta = sign * amt;
  try {
    const r = await fetch("/api/admin/coins/adjust", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: id, delta }) });
    const d = await r.json();
    if (handledPending(d)) { $("coinResult").textContent = "⏳ Ждёт подтверждения супер-админа."; return; }
    if (d.ok) {
      $("coinResult").textContent = `Готово ✅ Баланс ID ${id}: ${d.result.coins} 🪙`;
      alertMsg(sign > 0 ? "Добавлено ✅" : "Убрано ✅");
      loadAllUsers();
    } else alertMsg(d.error === "protected" ? "🛡 Монеты супер-админа трогать нельзя." : d.error === "bad_input" ? "Проверьте ID и количество." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}
$("coinAdd").onclick = () => adjustCoins(1);
$("coinRemove").onclick = () => adjustCoins(-1);
async function adjustSpins(sign) {
  const id = $("coinUserId").value.trim();
  const amt = parseInt($("spinAmount").value, 10);
  if (!id) { alertMsg("Введите ID."); return; }
  if (!amt || amt <= 0) { alertMsg("Введите число прокрутов."); return; }
  const spins = sign * amt;
  try {
    const r = await fetch("/api/admin/grant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: id, coins: 0, spins }) });
    const d = await r.json();
    if (handledPending(d)) { $("coinResult").textContent = "⏳ Ждёт подтверждения супер-админа."; return; }
    if (d.ok) {
      $("coinResult").textContent = `Готово ✅ Прокрутов у ID ${id}: ${d.result.spins}`;
      alertMsg(sign > 0 ? "Начислено ✅" : "Убрано ✅");
      loadAllUsers();
    } else alertMsg(d.error === "bad_id" ? "Неверный ID." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}
$("spinAdd").onclick = () => adjustSpins(1);
$("spinRemove").onclick = () => adjustSpins(-1);
async function loadAllUsers() {
  const q = $("userSearch").value.trim();
  $("allUsersList").innerHTML = `<div style="color:var(--hint)">Загрузка…</div>`;
  try {
    const r = await fetch("/api/admin/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, search: q }) });
    const d = await r.json();
    if (!d.ok) { $("allUsersList").innerHTML = `<div style="color:var(--hint)">Не удалось загрузить.</div>`; return; }
    // При поиске в скобках стояло общее число людей в базе: «Все пользователи (28)»
    // над списком из трёх, а то и над «Ничего не найдено». Формально не враньё,
    // но читается как «нашлось 28». Ищут — показываем сколько нашлось.
    $("allUsersCount").textContent = q ? `${d.shown} из ${d.total}` : d.total;
    if (!d.users.length) { $("allUsersList").innerHTML = `<div style="color:var(--hint)">Ничего не найдено.</div>`; return; }
    _usersById = {};
    d.users.forEach(u => { _usersById[u.id] = u; });
    $("allUsersList").innerHTML = d.users.map(u => {
      // Имя, а если его в Telegram нет — имя из профиля. Голый id остаётся
      // только для тех, кто не открывал ни бота, ни приложение.
      const имя = u.username ? `@${esc(u.username)}`
                : u.first_name ? esc(u.first_name) : `ID ${u.id}`;
      const who = имя + (u.super ? ` <span class="superbadge">🛡 SUPER</span>` : "");
      const meta = `🪙${u.coins} · 🎡${u.wheel_spins} · ${u.orders} зак · ${u.referrals} реф${u.age_ok ? "" : " · нет 18+"}`;
      const acts = u.super
        ? `<span class="uprotected">защищён</span>`
        : `${u.referred_by ? `<button class="unrefx" data-unref="${u.id}">Отвязать</button>` : ""}<button class="delx" data-del="${u.id}">Удалить</button>`;
      return `<div class="urow"><div class="uinfo" data-udetail="${u.id}"><b>${who}</b><small><span class="idcopy" data-copy="${u.id}">ID ${u.id} 📋</span> · ${meta} · <span class="uopen">подробнее ›</span></small></div><div class="uacts">${acts}</div></div>`;
    }).join("")
      + ((d.shown < d.total && !q) ? `<div style="color:var(--hint);font-size:12px;margin-top:8px">Показаны последние ${d.shown} из ${d.total} — уточните поиск.</div>` : "");
    document.querySelectorAll("#allUsersList .unrefx").forEach(b => b.onclick = () => doUnref(b.dataset.unref));
    document.querySelectorAll("#allUsersList .delx").forEach(b => b.onclick = () => confirmDelUser(b.dataset.del));
    document.querySelectorAll("#allUsersList .uinfo").forEach(el => el.onclick = () => showUserCard(_usersById[el.dataset.udetail]));
    bindIdCopy($("allUsersList"));
  } catch (e) { $("allUsersList").innerHTML = `<div style="color:var(--hint)">Сеть недоступна.</div>`; }
}
async function loadMyReferrals() {
  $("myRefList").innerHTML = `<div style="color:var(--hint)">Загрузка…</div>`;
  try {
    const r = await fetch("/api/admin/referrals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) { $("myRefList").innerHTML = `<div style="color:var(--hint)">Не удалось загрузить.</div>`; return; }
    $("myRefCount").textContent = d.referrals.length;
    $("myRefList").innerHTML = d.referrals.length
      ? d.referrals.map(r => `<div class="statrow"><span><span class="idcopy" data-copy="${r.id}">ID ${r.id} 📋</span> · <b style="color:${r.active ? '#2e9e4f' : 'var(--hint)'}">${r.active ? "активен" : "ждём заказ"}</b></span><span style="display:flex;gap:8px"><button class="unrefx" data-unref="${r.id}">Отвязать</button><button class="delx" data-del="${r.id}">Удалить</button></span></div>`).join("")
      : `<div style="color:var(--hint)">Пока нет рефералов.</div>`;
    document.querySelectorAll("#myRefList .unrefx").forEach(b => b.onclick = () => doUnref(b.dataset.unref));
    document.querySelectorAll("#myRefList .delx").forEach(b => b.onclick = () => confirmDelUser(b.dataset.del));
    bindIdCopy($("myRefList"));
  } catch (e) { $("myRefList").innerHTML = `<div style="color:var(--hint)">Сеть недоступна.</div>`; }
}
async function doUnref(id) {
  try {
    const r = await fetch("/api/admin/referral/unlink", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: id }) });
    const d = await r.json();
    if (handledPending(d)) return;
    if (d.ok) { alertMsg(d.result.unlinked ? "Отвязан ✅" : "У этого ID не было привязки."); refreshUsersAdmin(); }
    else alertMsg(d.error === "protected" ? "🛡 Супер-админа трогать нельзя." : d.error === "bad_id" ? "Неверный ID." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}
async function doDelUser(id) {
  try {
    const r = await fetch("/api/admin/user/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: id }) });
    const d = await r.json();
    if (handledPending(d)) return;
    if (d.ok) { alertMsg(d.result.deleted ? "Пользователь удалён ✅" : "Пользователь не найден."); refreshUsersAdmin(); }
    else alertMsg(d.error === "protected" ? "🛡 Супер-админа удалить нельзя." : d.error === "self" ? "Себя удалить нельзя." : d.error === "bad_id" ? "Неверный ID." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}
function confirmDelUser(id) {
  const msg = `Удалить пользователя ID ${id}? Он потеряет монеты, прокруты и 18+. Заказы останутся в истории.`;
  confirmMsg(msg, () => doDelUser(id));
}
$("unrefDo").onclick = async () => {
  const id = $("unrefId").value.trim();
  if (!id) { alertMsg("Введите ID."); return; }
  $("unrefDo").disabled = true; $("unrefDo").textContent = "Отвязываю…";
  await doUnref(id);
  $("unrefDo").disabled = false; $("unrefDo").textContent = "Отвязать реферала"; $("unrefId").value = "";
};
$("delUserDo").onclick = () => {
  const id = $("unrefId").value.trim();
  if (!id) { alertMsg("Введите ID."); return; }
  confirmDelUser(id);
  $("unrefId").value = "";
};
$("clearRefs").onclick = () => {
  const go = async () => {
    try {
      const r = await fetch("/api/admin/referral/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
      const d = await r.json();
      if (handledPending(d)) return;
      if (d.ok) { alertMsg(`Отвязано: ${d.result.count} ✅`); refreshUsersAdmin(); }
      else alertMsg("Не удалось.");
    } catch (e) { alertMsg(текстСбоя(e)); }
  };
  confirmMsg("Отвязать всех ваших рефералов?", go);
};

// ----- Настройки магазина -----
// ----- Документы магазина (правит владелец) -----
$("mDocs").onclick = openDocsAdmin;
$("docsAdminClose").onclick = () => $("docsAdminView").classList.remove("show");

async function postDocs(body) {
  const r = await fetch("/api/admin/docs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ initData }, body)) });
  return r.json();
}

async function openDocsAdmin() {
  $("docsAdminView").classList.add("show");
  $("docsAdminState").textContent = "Загрузка…";
  try {
    const d = await postDocs({});
    const док = (d && d.docs) || {};
    $("setOffer").value = док.offer || "";
    $("setPrivacy").value = док.privacy || "";
    $("docsAdminState").textContent = док["своими_словами"]
      ? `Редакция ${док.version}. Тексты ваши.`
      : `Редакция ${док.version}. Сейчас показывается ЧЕРНОВИК — замените его.`;
  } catch (e) {
    $("docsAdminState").textContent = "Не удалось загрузить.";
  }
}

$("docsAdminSave").onclick = async () => {
  const offer = $("setOffer").value.trim(), privacy = $("setPrivacy").value.trim();
  if (!offer || !privacy) { alertMsg("Оба документа обязаны быть непустыми."); return; }
  $("docsAdminSave").disabled = true; $("docsAdminSave").textContent = "Сохраняю…";
  try {
    const d = await postDocs({ offer, privacy });
    if (d && d.ok) {
      docsCache = null;             // покупателю показываем уже новое
      $("docsAdminState").textContent = `Редакция ${d.version}. Тексты ваши.`;
      alertMsg("Сохранено ✅");
    } else {
      alertMsg((d && d.message) || "Не удалось сохранить.");
    }
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("docsAdminSave").disabled = false; $("docsAdminSave").textContent = "Сохранить"; }
};

$("mSettings").onclick = openSettings;
$("settingsClose").onclick = () => $("settingsView").classList.remove("show");
async function openSettings() {
  $("settingsView").classList.add("show");
  $("setPay").value = ""; $("setConfirm").value = "";
  try {
    const r = await fetch("/api/admin/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) {
      $("setPay").value = d.settings.payment_info || "";
      $("setConfirm").value = d.settings.confirm_minutes || "";
      $("setFreeFrom").value = d.settings.free_delivery_from ?? "";
      $("setRemindDays").value = d.settings.remind_after_days ?? "";
      $("setRemindCap").value = d.settings.remind_daily_cap ?? "";
      $("setCashback").value = d.settings.coins_per_byn ?? "";
      $("setWheelStep").value = d.settings.wheel_step ?? "";
      $("setRefBonus").value = d.settings.referral_bonus ?? "";
      $("setCompMax").value = d.settings.compensation_max ?? "";
      $("setPayCash").checked = d.settings.pay_cash !== false;
      $("setPayCard").checked = d.settings.pay_card !== false;
      coinValue = d.settings.coin_value || 0.01;
      renderGenerosity();
    }
  } catch (e) { alertMsg(текстСбоя(e)); }
}

// Цифры лояльности абстрактны сами по себе: «1 монета за Br» ничего не
// говорит, а «это 1% с каждого заказа» — говорит. Считаем прямо под полем,
// пока владелец печатает.
let coinValue = 0.01;
const WHEEL_AVG_PRIZE = 343;      // средний приз колеса в монетах
function renderGenerosity() {
  const cb = parseFloat(($("setCashback").value || "0").replace(",", ".")) || 0;
  $("setCashbackNote").innerHTML =
    `Это <b>${(cb * coinValue * 100).toFixed(1)}%</b> от суммы заказа. Начисляется после выдачи, с товаров без доставки.`;
  const step = parseFloat(($("setWheelStep").value || "0").replace(",", ".")) || 0;
  const wheelPct = step > 0 ? (WHEEL_AVG_PRIZE * coinValue / step * 100) : 0;
  $("setWheelNote").innerHTML = step > 0
    ? `Средний приз — около ${WHEEL_AVG_PRIZE} монет (${(WHEEL_AVG_PRIZE * coinValue).toFixed(2)} Br), значит колесо отдаёт примерно <b>${wheelPct.toFixed(1)}%</b> с заказа. Чем меньше шаг, тем щедрее.`
    : `Укажите сумму: прокрут даётся за потраченные Br, а не за штуки.`;
  const rb = parseInt($("setRefBonus").value || "0", 10) || 0;
  $("setRefNote").innerHTML =
    `Разово <b>${(rb * coinValue).toFixed(2)} Br</b> пригласившему — за первый заказ друга. Дальше он получает процент с каждого заказа.`;
  const cm = parseInt($("setCompMax").value || "0", 10) || 0;
  $("setCompNote").innerHTML = cm > 0
    ? `До <b>${(cm * coinValue).toFixed(2)} Br</b> за раз. Продавец начисляет их покупателю по заказу (разбитый под, задержка), но только с вашего подтверждения в боте.`
    : `Ноль — продавцы не могут начислять компенсации совсем.`;
}
["setCashback", "setWheelStep", "setRefBonus", "setCompMax"].forEach(id => {
  const el = $(id);
  if (el) el.oninput = renderGenerosity;
});
$("setSave").onclick = async () => {
  const body = { initData, payment_info: $("setPay").value, confirm_minutes: $("setConfirm").value,
                 free_delivery_from: $("setFreeFrom").value,
                 remind_after_days: $("setRemindDays").value, remind_daily_cap: $("setRemindCap").value,
                 coins_per_byn: $("setCashback").value, wheel_step: $("setWheelStep").value,
                 referral_bonus: $("setRefBonus").value,
                 compensation_max: $("setCompMax").value,
                 pay_cash: $("setPayCash").checked, pay_card: $("setPayCard").checked };
  $("setSave").disabled = true; $("setSave").textContent = "Сохраняю…";
  try {
    const d = await админПост("/api/admin/settings/update", body, "сохранить настройки");
    if (d) {
      if (d.failed && (d.failed.pay || d.failed.payment_info)) {
        // Отклонили («оба способа сразу нельзя» или пустые реквизиты) — поле
        // осталось в том виде, как его натыкал человек. Перечитываем
        // настройки заново, чтобы показать то, что реально лежит в базе, а
        // не несохранённое — иначе экран покажет пустые реквизиты как
        // сохранённые, хотя старые всё ещё действуют.
        await openSettings();
      }
      показатьСохранённое(body, d.applied || {}, d.failed || {});
    }
  } finally { $("setSave").disabled = false; $("setSave").textContent = "Сохранить"; }
};

// Поля настроек и то, как их зовут по-человечески. Нужен для одного: сказать,
// ЧТО именно поправилось, а не «сохранено» вообще.
const ПОЛЯ_НАСТРОЕК = {
  payment_info: ["setPay", "Реквизиты"],
  pay_cash: ["setPayCash", "Оплата наличными"],
  pay_card: ["setPayCard", "Оплата картой"],
  confirm_minutes: ["setConfirm", "Время подтверждения"],
  free_delivery_from: ["setFreeFrom", "Бесплатная доставка от"],
  remind_after_days: ["setRemindDays", "Напоминать через (дней)"],
  remind_daily_cap: ["setRemindCap", "Напоминаний в день"],
  coins_per_byn: ["setCashback", "Кэшбэк"],
  wheel_step: ["setWheelStep", "Шаг колеса"],
  referral_bonus: ["setRefBonus", "Бонус за друга"],
  compensation_max: ["setCompMax", "Потолок компенсации"],
};

function показатьСохранённое(отправили, легло, отказы) {
  // Сервер прижимает значения к границам — и раньше делал это молча. Владелец
  // вводил кэшбэк 9999, читал «Сохранено ✅» и уходил уверенный, что так и
  // есть, хотя в базе лежала десятка. Показываем расхождение сразу и в полях,
  // и словами: другого момента, когда человек об этом думает, не будет.
  const поправлено = [];
  for (const ключ in легло) {
    const [id, имя] = ПОЛЯ_НАСТРОЕК[ключ] || [];
    if (!id) continue;
    const стало = легло[ключ];
    // Галочки сравниваем как есть (true/false), остальные поля — строками,
    // с учётом запятой вместо точки в дробных.
    const строкой = typeof стало !== "boolean";
    const было = строкой ? String(отправили[ключ] ?? "").trim().replace(",", ".") : !!отправили[ключ];
    // Сравниваем числами, где это числа: «10» и «10.0» — одно и то же, и
    // ругаться на такое значило бы кричать по любому сохранению.
    const одно = !строкой ? было === стало
      : (было === String(стало) || (было !== "" && !isNaN(+было) && !isNaN(+стало) && +было === +стало));
    if (!одно) поправлено.push(`${имя}: ${строкой ? (было || "пусто") : (было ? "да" : "нет")} → ${строкой ? стало : (стало ? "да" : "нет")}`);
    const поле = $(id);
    if (поле && поле.type === "checkbox") поле.checked = !!стало;
    else if (поле) поле.value = стало;      // в поле — то, что действительно легло
  }
  const отказано = Object.values(отказы || {});
  alertMsg(отказано.length
    ? "Не всё сохранилось:\n\n" + отказано.join("\n")
      + (поправлено.length ? "\n\nПоправлено под допустимые границы:\n" + поправлено.join("\n") : "")
    : поправлено.length
    ? "Сохранено, но часть значений поправлена под допустимые границы:\n\n"
      + поправлено.join("\n")
    : "Сохранено ✅");
}

// ----- Склад: приход и списание -----
// Раньше остаток правили числом в редакторе, и на вопрос «куда делось» ответа
// не было. Теперь каждое изменение — с причиной, автором и датой.
const STOCK_REASONS = { in: "Приход", broken: "Брак или бой", expired: "Просрочка",
                        lost: "Недостача", gift: "Подарок или образец", fix: "Пересчёт" };
let stockProduct = null, stockReason = "in";
$("stockClose").onclick = () => $("stockView").classList.remove("show");

function openStockMove(id) {
  stockProduct = shelf().find(p => p.id === id);
  if (!stockProduct) return;
  stockReason = "in";
  $("stockView").classList.add("show");
  $("stockName").textContent = stockProduct.name;
  $("stockNow").textContent = `${stockProduct.city} · сейчас ${stockProduct.stock} шт`;
  $("stockQty").value = ""; $("stockCost").value = ""; $("stockNote").value = "";
  // Кнопки –/+ у количества. Минимум 1: приход или списание нуля штук —
  // это не операция, а промах, и сохранять его незачем.
  bindQty($("stockView"));

  // У товара со вкусами склад ведётся по каждому вкусу отдельно.
  const вкусы = stockProduct.variants || [];
  $("stockFlavorWrap").style.display = вкусы.length ? "" : "none";
  $("stockFlavor").innerHTML = вкусы.map(v => `<option value="${esc(v.flavor)}">${esc(v.flavor)} · ${v.stock} шт</option>`).join("");

  renderStockReasons();
  // Пересчёт считает разницу от выбранного вкуса — смена вкуса меняет и её.
  $("stockFlavor").onchange = applyStockMode;
  $("stockQty").oninput = () => { if (stockReason === "fix") stockПоказатьРазницу(); };
  loadStockLog(id);
}

// Сколько сейчас числится по той полке, о которой идёт речь: у товара со
// вкусами — по выбранному вкусу, иначе по товару целиком.
function stockСейчас() {
  const вкусы = (stockProduct && stockProduct.variants) || [];
  if (!вкусы.length) return +(stockProduct ? stockProduct.stock : 0);
  const выбран = $("stockFlavor").value;
  const v = вкусы.find(x => x.flavor === выбран);
  return +(v ? v.stock : 0);
}

function renderStockReasons() {
  $("stockReasons").innerHTML = Object.entries(STOCK_REASONS).map(([k, n]) =>
    `<button class="opt ${stockReason === k ? 'active' : ''}" data-sr="${k}">${n}</button>`).join("");
  $("stockReasons").querySelectorAll("[data-sr]").forEach(b => b.onclick = () => {
    stockReason = b.dataset.sr; renderStockReasons();
  });
  // Закупочная цена нужна только при приходе — в остальных случаях
  // спрашивать её незачем.
  $("stockCostWrap").style.display = stockReason === "in" ? "" : "none";
  applyStockMode();
}

// Пересчёт спрашивает РЕЗУЛЬТАТ, остальные причины — количество. Считать
// разницу в уме — работа для машины, и на ней же ошибаются: минус вместо
// плюса виден только назавтра, по недостаче.
function applyStockMode() {
  const пересчёт = stockReason === "fix";
  const было = stockСейчас();
  $("stockQtyLabel").textContent = пересчёт ? "Сколько получилось при пересчёте" : "Сколько штук";
  $("stockQtyNote").style.display = пересчёт ? "" : "none";
  const поле = $("stockQty");
  поле.closest(".qty").dataset.min = пересчёт ? "0" : "1";
  if (пересчёт) {
    if (!поле.value) поле.value = было;
    stockПоказатьРазницу();
  } else if (String(поле.value) === String(было)) {
    поле.value = "";
  }
}

function stockПоказатьРазницу() {
  const было = stockСейчас();
  const стало = parseInt($("stockQty").value, 10);
  if (isNaN(стало)) { $("stockQtyNote").textContent = `Сейчас числится ${было} шт.`; return; }
  const d = стало - было;
  $("stockQtyNote").textContent = d === 0
    ? `Числится ${было} шт — сходится, записывать нечего.`
    : `Числится ${было} шт → станет ${стало} шт (${d > 0 ? "+" : ""}${d}).`;
}

async function loadStockLog(id) {
  $("stockLog").innerHTML = "";
  try {
    const r = await fetch("/api/admin/stock/moves", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id }) });
    const d = await r.json();
    const moves = d.moves || [];
    if (!moves.length) { $("stockLog").innerHTML = `<div class="card-block"><p style="color:var(--hint);margin:0">Движений пока не было.</p></div>`; return; }
    const rows = moves.map(m => {
      const знак = m.delta > 0 ? `+${m.delta}` : `${m.delta}`;
      const цвет = m.delta > 0 ? "#1f8a5f" : "var(--danger)";
      return `<div class="statrow"><span>${esc(STOCK_REASONS[m.reason] || m.reason)}${m.flavor ? ` · ${esc(m.flavor)}` : ""}
          <small style="display:block;color:var(--hint)">${esc(m.created_at || "")}${m.note ? ` · ${esc(m.note)}` : ""}</small></span>
        <b style="color:${цвет}">${знак} шт</b></div>`;
    }).join("");
    $("stockLog").innerHTML = `<div class="stathead">История движений</div><div class="statlist">${rows}</div>`;
  } catch (e) { /* журнал не критичен — молчим */ }
}

$("stockSave").onclick = async () => {
  const qty = parseInt($("stockQty").value, 10);
  if (isNaN(qty) || qty < 0) { alertMsg("Укажите количество."); return; }
  if (stockReason === "fix") {
    if (qty === stockСейчас()) { alertMsg("Столько и числится — записывать нечего."); return; }
  } else if (qty <= 0) { alertMsg("Укажите количество."); return; }
  const body = { initData, id: stockProduct.id, qty, reason: stockReason,
                 cost: stockReason === "in" ? $("stockCost").value : "",
                 note: $("stockNote").value,
                 flavor: (stockProduct.variants || []).length ? $("stockFlavor").value : "" };
  const btn = $("stockSave");
  btn.disabled = true; btn.textContent = "Записываю…";
  try {
    const r = await fetch("/api/admin/stock/move", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не удалось записать движение."); return; }
    await refreshProducts();
    stockProduct = shelf().find(p => p.id === stockProduct.id) || stockProduct;
    $("stockNow").textContent = `${stockProduct.city} · сейчас ${d.stock} шт`;
    $("stockQty").value = ""; $("stockNote").value = "";
    await loadStockLog(stockProduct.id);
    toast(`${STOCK_REASONS[stockReason]}: остаток ${d.stock} шт`);
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { btn.disabled = false; btn.textContent = "Записать"; }
};

// ----- Промокоды -----
// Владелец постит в группу вручную, и без кодов нельзя понять, что сработало.
// Поэтому в списке главное не сам код, а сколько заказов и выручки он принёс.
$("mPromos").onclick = openPromos;
$("promosClose").onclick = () => $("promosView").classList.remove("show");

// ----- Отзывы на модерации -----
$("mReviews").onclick = openReviewsAdmin;
$("reviewsClose").onclick = () => $("reviewsView").classList.remove("show");

// Фильтр: раньше админ видел только очередь, и опубликованный отзыв исчезал
// из его поля зрения навсегда — убрать его было уже нечем.
const REV_FILTERS = [["pending", "На модерации"], ["approved", "Опубликованные"],
                     ["hidden", "Скрытые"], ["all", "Все"]];
let revFilter = "pending", revReplying = null;

async function openReviewsAdmin() {
  $("reviewsView").classList.add("show");
  // Продавец отвечает покупателю, но публикует и удаляет владелец: отзыв
  // виден на всех точках. Пишем это прямо, чтобы отсутствие кнопок не
  // выглядело поломкой.
  const note = $("revNote");
  if (note && !isOwner()) note.textContent =
    "Здесь вы отвечаете покупателям. Спокойный ответ на тройку убеждает нового покупателя сильнее, чем её отсутствие. Публикует и скрывает отзывы владелец.";
  await loadPendingReviews();
}
function renderRevFilter() {
  $("revFilter").innerHTML = REV_FILTERS.map(([k, n]) =>
    `<button class="ochip ${revFilter === k ? 'active' : ''}" data-rf="${k}">${n}</button>`).join("");
  $("revFilter").querySelectorAll("[data-rf]").forEach(b => b.onclick = () => {
    revFilter = b.dataset.rf; revReplying = null; loadPendingReviews();
  });
}
async function loadPendingReviews(silent) {
  if (!silent) { renderRevFilter(); $("reviewsList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`; }
  try {
    const r = await fetch("/api/admin/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, status: silent ? "pending" : revFilter }) });
    const d = await r.json();
    const list = d.reviews || [];
    // Счётчик в меню: иначе отзыв висит непроверенным, пока о нём не вспомнят.
    const badge = $("revBadge");
    if (badge) { badge.textContent = d.pending || 0; badge.style.display = d.pending ? "" : "none"; }
    if (!silent) renderPendingReviews(list);
    return d.pending || 0;
  } catch (e) {
    if (!silent) $("reviewsList").innerHTML = `<p style="color:var(--hint)">Сеть недоступна.</p>`;
    return 0;
  }
}
const REV_STATUS_RU = { pending: "на модерации", approved: "опубликован", hidden: "скрыт" };
function renderPendingReviews(list) {
  if (!list.length) {
    $("reviewsList").innerHTML = `<p style="color:var(--hint);margin:0">${revFilter === "pending" ? "Новых отзывов нет." : "Здесь пусто."}</p>`;
    return;
  }
  $("reviewsList").innerHTML = list.map(v => `<div class="rev">
    <div class="rtop"><span class="stars">${starsHtml(v.rating)}</span><span class="rwho">${esc(v.who)} · ${esc(whenRu(v.created_at).slice(0, 10))}</span></div>
    <div style="font-size:13px;color:var(--hint);margin-top:3px">${esc(v.product)} · ${REV_STATUS_RU[v.status] || v.status}</div>
    ${v.text ? `<p>${esc(v.text)}</p>` : `<p style="color:var(--hint)">Без текста — только оценка.</p>`}
    ${v.reply && revReplying !== v.id ? `<div class="revreply"><b>Ответ магазина:</b> ${esc(v.reply)}</div>` : ""}
    ${revReplying === v.id ? `<textarea class="admsearch rrtext" style="min-height:70px;margin-top:8px" placeholder="Ответ покупателю">${esc(v.reply || "")}</textarea>
      <div style="display:flex;gap:8px"><button class="unrefx" data-rsave="${v.id}">Сохранить ответ</button><button class="unrefx" data-rcancel="1">Отмена</button></div>` : `
    <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
      ${isOwner() && v.status !== "approved" ? `<button class="unrefx" data-rok="${v.id}">Опубликовать</button>` : ""}
      ${isOwner() && v.status !== "hidden" ? `<button class="unrefx" data-rno="${v.id}">Скрыть</button>` : ""}
      <button class="unrefx" data-rrep="${v.id}">${v.reply ? "Изменить ответ" : "Ответить"}</button>
      ${isOwner() ? `<button class="delx" data-rdel="${v.id}">Удалить</button>` : ""}
    </div>`}</div>`).join("");
  $("reviewsList").querySelectorAll("[data-rok]").forEach(b => b.onclick = () => decideReview(+b.dataset.rok, true));
  $("reviewsList").querySelectorAll("[data-rno]").forEach(b => b.onclick = () => decideReview(+b.dataset.rno, false));
  $("reviewsList").querySelectorAll("[data-rdel]").forEach(b => b.onclick = () => delReview(+b.dataset.rdel));
  $("reviewsList").querySelectorAll("[data-rrep]").forEach(b => b.onclick = () => { revReplying = +b.dataset.rrep; renderPendingReviews(list); });
  $("reviewsList").querySelectorAll("[data-rcancel]").forEach(b => b.onclick = () => { revReplying = null; renderPendingReviews(list); });
  $("reviewsList").querySelectorAll("[data-rsave]").forEach(b => b.onclick = () => {
    saveReviewReply(+b.dataset.rsave, b.closest(".rev").querySelector(".rrtext").value.trim());
  });
}
async function reviewApi(path, body) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, ...body }) });
    return await r.json();
  } catch (e) { alertMsg(текстСбоя(e)); return { ok: false }; }
}
async function decideReview(id, ok) {
  const d = await reviewApi("/api/admin/review/decide", { id, ok });
  if (!d.ok) { alertMsg("Не удалось."); return; }
  toast(ok ? "Опубликован ✅" : "Скрыт");
  await loadPendingReviews();
  refreshProducts();       // средняя оценка в карточке товара меняется сразу
}
function delReview(id) {
  // Удаление — навсегда. «Скрыть» оставляет отзыв в базе, его можно вернуть.
  confirmMsg("Удалить отзыв насовсем? Скрытый отзыв можно вернуть, удалённый — нет.", async () => {
    const d = await reviewApi("/api/admin/review/delete", { id });
    if (!d.ok) { alertMsg("Не удалось удалить."); return; }
    toast("Отзыв удалён");
    await loadPendingReviews();
    refreshProducts();
  });
}
async function saveReviewReply(id, text) {
  const d = await reviewApi("/api/admin/review/reply", { id, text });
  if (!d.ok) { alertMsg("Не удалось сохранить ответ."); return; }
  revReplying = null;
  toast(text ? "Ответ сохранён ✅" : "Ответ убран");
  await loadPendingReviews();
}

async function openPromos() {
  $("promosView").classList.add("show");
  $("pmCode").value = ""; $("pmValue").value = ""; $("pmMin").value = ""; $("pmUses").value = "";
  await loadPromos();
}

async function loadPromos() {
  $("promoList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
  try {
    const r = await fetch("/api/admin/promos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    renderPromos(d.promos || []);
  } catch (e) { $("promoList").innerHTML = `<p style="color:var(--hint)">Сеть недоступна.</p>`; }
}

function renderPromos(list) {
  if (!list.length) { $("promoList").innerHTML = `<div class="card-block"><p style="color:var(--hint);margin:0">Кодов пока нет.</p></div>`; return; }
  const rows = list.map(p => {
    const скидка = p.kind === "fixed" ? `−${p.value} Br` : `−${p.value}%`;
    const условия = [p.min_total ? `от ${p.min_total} Br` : "",
                     p.uses_left === null ? "без ограничения" : `осталось ${p.uses_left}`,
                     p.once_per_user ? "1 на человека" : ""].filter(Boolean).join(" · ");
    const итог = p.orders
      ? `<b style="color:#1f8a5f">${p.orders} зак. · ${p.revenue} Br</b>`
      : `<span style="color:var(--hint)">не применяли</span>`;
    return `<div class="urow"><div style="flex:1">
        <div><b>${esc(p.code)}</b> <span class="tagbadge">${скидка}</span>${p.active ? "" : ` <span class="tagbadge alt">выключен</span>`}</div>
        <small style="color:var(--hint)">${условия}</small>
        <div style="margin-top:4px;font-size:13px">${итог}${p.given ? ` <span style="color:var(--hint)">· скидок на ${p.given} Br</span>` : ""}</div>
      </div>
      <div style="display:flex;gap:6px;flex-direction:column">
        <button class="unrefx" data-pmtog="${esc(p.code)}" data-act="${p.active ? 0 : 1}">${p.active ? "Выключить" : "Включить"}</button>
        <button class="delx" data-pmdel="${esc(p.code)}">Удалить</button>
      </div></div>`;
  }).join("");
  $("promoList").innerHTML = `<div class="card-block"><label>Коды и что они принесли</label>${rows}</div>`;
  $("promoList").querySelectorAll("[data-pmtog]").forEach(b => b.onclick = () => togglePromo(b.dataset.pmtog, b.dataset.act === "1"));
  $("promoList").querySelectorAll("[data-pmdel]").forEach(b => b.onclick = () => delPromo(b.dataset.pmdel));
}

$("pmAdd").onclick = async () => {
  const code = $("pmCode").value.trim().toUpperCase();
  if (!code || code.includes(" ")) { alertMsg("Код без пробелов, например AVGUST10."); return; }
  if (!$("pmValue").value.trim()) { alertMsg("Укажите размер скидки."); return; }
  const body = { initData, code, kind: $("pmKind").value, value: $("pmValue").value,
                 min_total: $("pmMin").value, uses_left: $("pmUses").value,
                 once_per_user: $("pmOnce").checked };
  $("pmAdd").disabled = true; $("pmAdd").textContent = "Создаю…";
  try {
    const r = await fetch("/api/admin/promo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.ok) { $("pmCode").value = ""; $("pmValue").value = ""; await loadPromos(); }
    else alertMsg({ exists: "Такой код уже есть.", bad_code: "Код без пробелов, до 24 символов.",
                    bad_value: "Скидка должна быть больше нуля (процент — не больше 100)." }[d.error] || "Не удалось создать код.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("pmAdd").disabled = false; $("pmAdd").textContent = "Создать код"; }
};

async function togglePromo(code, active) {
  if (!await админПост("/api/admin/promo/toggle", { code, active }, "переключить код")) return;
  await loadPromos();
}

// Удаление уносит и статистику, поэтому предупреждаем: обычно нужен «выключить».
function delPromo(code) {
  confirmMsg(`Удалить код ${code}? Статистика по нему тоже пропадёт — чтобы просто перестать его принимать, достаточно выключить.`,
             () => doDelPromo(code));
}
async function doDelPromo(code) {
  if (!await админПост("/api/admin/promo/delete", { code }, "удалить код")) return;
  await loadPromos();
}

// ----- Доступ продавцов (только супер-админ) -----
// Раньше админов задавали переменными окружения на хостинге: чтобы добавить
// продавца, нужно было зайти в панель и перезапустить сервис. Теперь список
// живёт в базе, а переменные окружения остаются страховкой — записи оттуда
// показываем, но удалить из приложения нельзя.
$("mStaff").onclick = openStaff;
$("staffClose").onclick = () => $("staffView").classList.remove("show");

// ----- Журнал действий -----
$("mLog").onclick = openLog;
$("logClose").onclick = () => $("logView").classList.remove("show");

// Путь запроса — не то, что читает человек. Переводим в дело: «цена»,
// «удалил товар», «настройки магазина».
const LOG_NAMES = {
  "product/update": "изменил товар", "product/delete": "удалил товар с точки",
  "product/variants": "изменил варианты", "product/specs": "изменил характеристики",
  "product/from-model": "завёз на точку", "product": "добавил товар",
  "stock/move": "движение склада", "order/status": "статус заказа",
  "model": "изменил модель", "model/delete": "удалил модель",
  "model/photo": "фото модели", "photo/add": "добавил фото", "photo/delete": "убрал фото",
  "brand": "изменил бренд", "brand/delete": "удалил бренд",
  "category": "добавил категорию", "category/update": "изменил категорию",
  "category/delete": "удалил категорию", "settings/update": "настройки магазина",
  "promo": "промокод", "promo/delete": "удалил промокод", "promo/toggle": "вкл/выкл промокод",
  "location": "добавил точку", "location/delete": "удалил точку",
  "delivery": "способ доставки", "delivery/update": "изменил доставку",
  "delivery/delete": "удалил доставку", "staff/add": "выдал доступ",
  "staff/remove": "забрал доступ", "review/decide": "решение по отзыву",
  "review/delete": "удалил отзыв", "review/reply": "ответил на отзыв",
  "coins/adjust": "правка монет", "grant": "начислил", "message": "написал клиенту",
  "stats/reset": "сбросил статистику", "raffle/update": "правка розыгрыша",
  "raffle/draw": "разыграл призы", "user/delete": "удалил пользователя",
  // Дальше — то, что раньше попадало в журнал английским путём запроса.
  // Журнал читают, когда ищут, кто что сделал: «order/compensate» на этот
  // вопрос не отвечает, а именно компенсации и правки заказов ищут чаще всего.
  "order/compensate": "компенсация покупателю", "order/items": "правка состава заказа",
  "request/decide": "решение по заявке", "raffle/start": "начал розыгрыш",
  "raffle/photo": "фото розыгрыша", "model/hide": "скрыл/вернул модель",
  "product/to-model": "перенёс товар в ассортимент", "photo": "фото товара",
  "point": "добавил адрес самовывоза", "point/update": "изменил адрес самовывоза",
  "point/delete": "удалил адрес самовывоза", "docs": "правка оферты",
  "category/spec": "добавил характеристику", "category/spec/update": "изменил характеристику",
  "category/spec/delete": "удалил характеристику",
  "referral/unlink": "отвязал реферала", "referral/clear": "отвязал всех рефералов",
  "wheel/grant": "начислил прокруты",
};

const LOG_FIELDS = { price: "цена", cost: "закупка", stock: "остаток", name: "название",
                     city: "точка", is_hit: "хит", description: "описание", category: "категория" };
const LOG_REASONS = { in: "приход", broken: "брак", expired: "просрочка", lost: "недостача",
                      gift: "подарок", fix: "пересчёт" };
// Что сделали с заказом. Раньше в журнале стояло английское confirm/issued.
const LOG_ACTIONS = { confirm: "подтверждён", issued: "выдан", reject: "отклонён" };

// «id=14 · field=price · value=15.5» — это язык запроса, а не человека.
// Показываем товар по имени и говорим, что именно изменилось.
function logLine(x) {
  const kv = {};
  (x.details || "").split(" · ").forEach(p => {
    const i = p.indexOf("="); if (i > 0) kv[p.slice(0, i)] = p.slice(i + 1);
  });
  const pid = +(kv.id || kv.product_id || 0);
  const p = shelf().find(o => o.id === pid);
  const what = p ? `${p.name} · ${p.city}` : (kv.name || (pid ? `товар #${pid}` : ""));
  if (x.action === "product/update")
    return [what, `${LOG_FIELDS[kv.field] || kv.field || ""}: ${kv.value || ""}`].filter(Boolean).join(" — ");
  if (x.action === "stock/move")
    return [what, `${LOG_REASONS[kv.reason] || kv.reason || ""} ${kv.qty || ""} шт`].filter(Boolean).join(" — ");
  if (x.action === "order/status")
    return `заказ #${kv.id || "?"} — ${LOG_ACTIONS[kv.action] || kv.action || ""}`;
  if (x.action.startsWith("product/") || x.action === "product") return what;
  return x.details || "";
}

async function openLog() {
  $("logView").classList.add("show");
  $("logList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
  try {
    const r = await fetch("/api/admin/log", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    const rows = d.log || [];
    if (!rows.length) { $("logList").innerHTML = `<p style="color:var(--hint)">Пока пусто. Здесь появятся правки цен, остатков и настроек.</p>`; return; }
    $("logList").innerHTML = rows.map(x => {
      const line = logLine(x);
      return `<div class="statrow"><span>${esc(LOG_NAMES[x.action] || x.action)}${line ? " · " + esc(line) : ""}
        <small style="display:block;color:var(--hint)">${esc(x.who)} · ${esc(x.at)}</small></span></div>`;
    }).join("");
  } catch (e) { $("logList").innerHTML = `<p style="color:var(--hint)">Сеть недоступна.</p>`; }
}

async function openStaff() {
  $("staffView").classList.add("show");
  $("stId").value = ""; $("stNote").value = "";
  $("stCity").innerHTML = `<option value="">Все точки</option>` +
    locations.map(l => `<option value="${esc(l.name)}">${esc(l.name)}</option>`).join("");
  await loadStaff();
}

async function loadStaff() {
  $("staffList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
  try {
    const r = await fetch("/api/admin/staff", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) { $("staffList").innerHTML = `<p style="color:var(--hint)">Нет доступа.</p>`; return; }
    renderStaff(d.staff || []);
  } catch (e) { $("staffList").innerHTML = `<p style="color:var(--hint)">Сеть недоступна.</p>`; }
}

function renderStaff(list) {
  if (!list.length) { $("staffList").innerHTML = `<p style="color:var(--hint)">Пока никого.</p>`; return; }
  const rows = list.map(s => {
    // Без подписи заголовком служит сам ID — тогда во второй строке его
    // повторять незачем, там остаётся только точка.
    // У владельца город НЕ ограничивает права — он только направляет
    // уведомления о заказах. Без этой подписи выглядит как понижение.
    const where = s.is_super
      ? (s.city ? `весь магазин · заказы точки ${esc(s.city)}` : "весь магазин")
      : (s.city ? `точка ${esc(s.city)}` : "все точки");
    const who = s.note ? esc(s.note) : `ID ${s.user_id}`;
    const sub = s.note ? `ID ${s.user_id} · ${where}` : where;
    const tags = s.is_super
      ? `<span class="tagbadge">владелец · снять нельзя</span>`
      : `<span class="tagbadge">продавец</span>`;
    const del = s.can_remove ? `<button class="delx" data-stdel="${s.user_id}">Убрать</button>` : "";
    return `<div class="urow"><div><div><b>${who}</b></div>
      <small style="color:var(--hint)">${sub}</small>
      <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${tags}</div></div>${del}</div>`;
  }).join("");
  $("staffList").innerHTML = `<div class="card-block"><label>Кому открыт доступ</label>${rows}</div>`;
  $("staffList").querySelectorAll("[data-stdel]").forEach(b => b.onclick = () => removeStaff(b.dataset.stdel));
}

$("stAdd").onclick = async () => {
  const id = $("stId").value.trim();
  if (!/^\d+$/.test(id)) { alertMsg("ID — это число. Пусть человек пришлёт /myid боту."); return; }
  const body = { initData, user_id: id, city: $("stCity").value, note: $("stNote").value };
  $("stAdd").disabled = true; $("stAdd").textContent = "Выдаю…";
  try {
    const r = await fetch("/api/admin/staff/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.ok) { $("stId").value = ""; $("stNote").value = ""; await loadStaff(); alertMsg("Доступ выдан ✅"); }
    else alertMsg(d.error === "bad_city" ? "Такой точки нет." : "Не удалось выдать доступ.");
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("stAdd").disabled = false; $("stAdd").textContent = "Выдать доступ"; }
};

function removeStaff(uid) { confirmMsg(`Убрать доступ у ID ${uid}?`, () => doRemoveStaff(uid)); }
async function doRemoveStaff(uid) {
  try {
    const r = await fetch("/api/admin/staff/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, user_id: uid }) });
    const d = await r.json();
    if (d.ok) await loadStaff();
    else alertMsg(d.error === "super_protected" ? "Владельца убрать нельзя." : "Не удалось убрать.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}

