// 05-orders.js — заказы с обеих сторон: история покупателя и управление продавцом
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ----- Мои заказы (история + повтор) -----
let myOrders = [];
let payInfoText = "";          // реквизиты магазина — приходят вместе со списком заказов
let payConfirmMin = 15;        // за сколько обычно подтверждают (из настроек магазина)
$("myOrdersClose").onclick = () => $("myOrdersView").classList.remove("show");
async function openMyOrders() {
  $("myOrdersView").classList.add("show");
  $("myOrdersList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
  try {
    const r = await fetch("/api/orders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    myOrders = d.orders || [];
    if (d.payment_info) payInfoText = d.payment_info;   // чтобы показать реквизиты повторно
    if (d.confirm_minutes) payConfirmMin = d.confirm_minutes;
  } catch (e) { myOrders = []; }
  await loadReviewables();      // «оцените покупку» показываем сразу, а не вторым экраном
  renderMyOrders();
}
// ----- Оценить покупку (из «Мои заказы») -----
let reviewables = [];
async function loadReviewables() {
  try {
    const r = await fetch("/api/my-reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    reviewables = d.ok ? d.can : [];
  } catch (e) { reviewables = []; }
}
function reviewPromptHtml() {
  if (!reviewables.length) return "";
  return `<div class="ocard">
    <div class="ocard-id">Оцените покупку</div>
    <div class="ocard-sub" style="margin-bottom:6px">Отзыв увидят другие покупатели после проверки продавцом.</div>
    ${reviewables.map((p, i) => `<div class="revq"><span>${esc(p.name)}</span><button data-rev="${i}">Оценить</button></div>`).join("")}
  </div>`;
}
function openReviewSheet(p) {
  let picked = 0;
  showInfo(p.name, `
    <div class="rateset" id="rateSet">${[1, 2, 3, 4, 5].map(n => `<button data-star="${n}">★</button>`).join("")}</div>
    <textarea id="revText" class="admsearch" style="min-height:90px;resize:vertical" placeholder="Что понравилось, что нет (не обязательно)"></textarea>
    <button class="bigbtn" id="revSend" style="margin-top:12px">Отправить отзыв</button>`);
  $("infoClose").textContent = "Отмена";   // это форма, а не сообщение: «Понятно» тут не ответ
  const paint =() => document.querySelectorAll("#rateSet button").forEach(b => b.classList.toggle("on", +b.dataset.star <= picked));
  document.querySelectorAll("#rateSet button").forEach(b => b.onclick = () => { picked = +b.dataset.star; paint(); });
  $("revSend").onclick = async () => {
    if (!picked) { alertMsg("Поставьте оценку от 1 до 5 звёзд."); return; }
    $("revSend").disabled = true; $("revSend").textContent = "Отправляю…";
    try {
      const r = await fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, product_id: p.id, rating: picked, text: $("revText").value.trim() }) });
      const d = await r.json();
      if (d.ok) {
        closeOverlay($("infoOverlay"));
        // Товар уходит из списка сразу: иначе человек оценит второй раз и получит отказ.
        reviewables = reviewables.filter(x => x.id !== p.id);
        renderMyOrders();
        alertMsg("Спасибо! Отзыв появится после проверки ⭐");
      } else alertMsg(d.error === "not_allowed" ? "Оценить можно только полученный товар." : "Не удалось отправить.");
    } catch (e) { alertMsg("Сеть недоступна."); }
    finally { $("revSend").disabled = false; $("revSend").textContent = "Отправить отзыв"; }
  };
}

function renderMyOrders() {
  if (!myOrders.length) {
    $("myOrdersList").innerHTML = `<div class="empty"><div class="circ">📦</div><h3>Заказов пока нет</h3><p>Оформите первый заказ в ассортименте.</p></div>`;
    return;
  }
  $("myOrdersList").innerHTML = reviewPromptHtml() + myOrders.map((o, idx) => {
    const st = OSTATUS[o.status] || { label: o.status, cls: "new" };
    const items = (o.items || []).map(it =>
      `<div class="oitem"><span>${esc(it.name)}${it.flavor ? " · " + esc(it.flavor) : ""} × ${it.qty}</span><span>${(it.price * it.qty).toFixed(2)} ${CUR}</span></div>`).join("");
    const pm = { card: "💳 Картой", cash: "💵 Наличными", none: "🚕 При получении" }[o.payment_method] || "";
    const details = [
      o.delivery_method ? ["Получение", o.delivery_method + (o.delivery_address ? " · " + esc(o.delivery_address) : "")] : null,
      pm ? ["Оплата", pm] : null,
      o.delivery_fee ? ["Доставка", (+o.delivery_fee).toFixed(2) + " " + "Br"] : null,
      o.comment ? ["Комментарий", esc(o.comment)] : null,
    ].filter(Boolean).map(([k, v]) => `<div class="statrow"><span>${k}</span><b>${v}</b></div>`).join("");
    const needReceipt = o.status === "new" && o.payment_method === "card";
    const payBtn = needReceipt ? `<button class="bigbtn" data-pay="${idx}" style="margin-top:10px">📷 Загрузить чек</button>` : "";
    const canCancel = (o.status === "new" || o.status === "paid");
    const cancelBtn = canCancel ? `<button class="closebtn" data-cancel="${idx}" style="color:var(--danger);margin-top:8px">Отменить заказ</button>` : "";
    return `<div class="ocard">
      <div class="ocard-top">
        <div><div class="ocard-id">Заказ #${o.id}</div><div class="ocard-sub">${esc(o.city)} · ${esc(o.created_at)}</div></div>
        <span class="obadge ${st.cls}">${st.label}</span>
      </div>
      ${orderTracker(o.status)}
      <div class="oitems" style="margin-top:8px">${items}<div class="ototal"><span>Итого</span><span>${(+o.total).toFixed(2)} ${CUR}</span></div></div>
      ${details ? `<div class="odetails">${details}</div>` : ""}
      ${payBtn}
      <button class="bigbtn" data-repeat="${idx}" style="margin-top:10px;background:var(--surface-2);color:var(--text)">🔁 Повторить заказ</button>
      <button class="bigbtn" data-ask="${idx}" style="margin-top:8px;background:var(--surface-2);color:var(--text)">💬 Написать по заказу</button>
      ${cancelBtn}
    </div>`;
  }).join("");
  $("myOrdersList").querySelectorAll("[data-rev]").forEach(b => b.onclick = () => openReviewSheet(reviewables[+b.dataset.rev]));
  $("myOrdersList").querySelectorAll("[data-repeat]").forEach(b => b.onclick = () => repeatOrder(myOrders[+b.dataset.repeat]));
  $("myOrdersList").querySelectorAll("[data-ask]").forEach(b => b.onclick = () => openSupport(myOrders[+b.dataset.ask].id));
  $("myOrdersList").querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => cancelMyOrder(myOrders[+b.dataset.cancel]));
  // Раньше эта кнопка сразу открывала выбор файла — а человек, нажавший
  // «оплачу позже», к тому моменту уже не помнил номер счёта и найти его
  // было негде. Теперь возвращаем тот же экран оплаты, с реквизитами.
  $("myOrdersList").querySelectorAll("[data-pay]").forEach(b => b.onclick = () => {
    const o = myOrders[+b.dataset.pay];
    currentOrder = { order_id: o.id, confirm_minutes: payConfirmMin };
    $("payTitle").textContent = `Оплата заказа #${o.id}`;
    renderPayReq({ total: +o.total, payment_info: payInfoText });
    $("payView").classList.add("show");
  });
}
function cancelMyOrder(o) {
  const go = async () => {
    try {
      const r = await fetch("/api/order/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, order_id: o.id }) });
      const d = await r.json();
      if (d.ok) { alertMsg("Заказ отменён. Монеты возвращены."); openMyOrders(); fetchBonus(); }
      else alertMsg(d.error === "too_late" ? "Заказ уже подтверждён — отмена через поддержку." : "Не удалось отменить.");
    } catch (e) { alertMsg("Сеть недоступна."); }
  };
  const msg = `Отменить заказ #${o.id}?`;
  confirmMsg(msg, go);
}
// Трекер: Оформлен → Подтверждён → Выдан (или «отклонён»).
function orderTracker(status) {
  if (status === "canceled") return `<div class="ocanceled">✖️ Заказ отклонён</div>`;
  const stage = status === "issued" ? 3 : status === "confirmed" ? 2 : 1;
  const s1 = "done";
  const s2 = stage >= 2 ? "done" : "active";
  const s3 = stage >= 3 ? "done" : (stage >= 2 ? "active" : "");
  const d = (cls, done) => `<div class="dot">${done ? "✓" : ""}</div>`;
  return `<div class="otrack">
    <div class="ostep ${s1}">${d(s1, true)}<div class="lbl">Оформлен</div></div>
    <div class="ostep ${s2}">${d(s2, stage >= 2)}<div class="lbl">Подтверждён</div></div>
    <div class="ostep ${s3}">${d(s3, stage >= 3)}<div class="lbl">Выдан</div></div>
  </div>`;
}
function repeatOrder(o) {
  const unavail = [], toAdd = [];
  (o.items || []).forEach(it => {
    const p = allProducts.find(x => x.id === it.id);
    const avail = p ? (it.flavor ? variantStock(p, it.flavor) : p.stock) : 0;
    if (!p || avail <= 0) { unavail.push(esc(it.name) + (it.flavor ? " · " + esc(it.flavor) : "")); return; }
    toAdd.push({ id: p.id, flavor: it.flavor || null, qty: Math.min(it.qty, avail) });
  });
  if (!toAdd.length) { alertMsg("Эти товары сейчас недоступны."); return; }
  city = o.city; $("pointName").textContent = city;          // корзина привязана к одной точке
  for (const k in cart) delete cart[k];
  toAdd.forEach(a => { cart[cartKey(a.id, a.flavor)] = { product_id: a.id, flavor: a.flavor, qty: a.qty }; });
  $("myOrdersView").classList.remove("show");
  updateFilterBtn(); renderGrid(); renderNav(); showTab("cart");
  if (unavail.length) alertMsg("Добавлено в корзину. Сейчас недоступно: " + unavail.join(", "));
}

// ============ Заказы (управление продавцом) ============
let adminOrders = [], ordersStatusFilter = "active", ordersSearch = "", ordersCityFilter = "all";
const OSTATUS = {
  new:       { label: "Ждёт чек",            cls: "new" },
  paid:      { label: "Ждёт подтверждения",  cls: "paid" },
  confirmed: { label: "Подтверждён", cls: "confirmed" },
  issued:    { label: "Выдан",               cls: "issued" },
  canceled:  { label: "Отклонён",            cls: "canceled" },
};
const OFILTERS = [
  ["active", "Активные"], ["paid", "Ждут подтверждения"], ["confirmed", "К выдаче"],
  ["issued", "Выданы"], ["canceled", "Отклонены"], ["all", "Все"],
];

// Возвращаясь в «Управление», продавец должен видеть сегодняшние цифры, а не
// те, с которыми входил: заказы он только что и разгребал.
$("ordersClose").onclick = () => { $("ordersView").classList.remove("show"); loadToday(); };
$("ordersSearch").oninput = (e) => { ordersSearch = e.target.value; renderOrders(); };

async function loadAdminOrders() {
  $("ordersList").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
  try {
    const r = await fetch("/api/admin/orders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    adminOrders = d.orders || [];
  } catch (e) { adminOrders = []; }
  setBadge("ordBadge", adminOrders.filter(o => o.status === "paid").length);
  renderOrdersFilter();
  renderOrders();
}

function renderOrdersFilter() {
  $("ordersFilter").innerHTML = OFILTERS.map(([k, name]) =>
    `<button class="ochip ${ordersStatusFilter === k ? 'active' : ''}" data-of="${k}">${name}</button>`).join("");
  $("ordersFilter").querySelectorAll("[data-of]").forEach(b =>
    b.onclick = () => { ordersStatusFilter = b.dataset.of; renderOrdersFilter(); renderOrders(); });
  // Владелец видит заказы всех точек — но сам обычно стоит на одной.
  // Продавцу точки чипы не нужны: у него в списке и так только его город.
  const cities = [...new Set(adminOrders.map(o => o.city))];
  const box = $("ordersCityFilter");
  if (!box) return;
  if (cities.length < 2) { box.innerHTML = ""; return; }
  box.innerHTML = [["all", "Все точки"], ...cities.map(c => [c, c])].map(([k, name]) =>
    `<button class="ochip ${ordersCityFilter === k ? 'active' : ''}" data-ocity="${esc(k)}">${esc(name)}</button>`).join("");
  box.querySelectorAll("[data-ocity]").forEach(b =>
    b.onclick = () => { ordersCityFilter = b.dataset.ocity; renderOrdersFilter(); renderOrders(); });
}

function orderMatchesFilter(o) {
  if (ordersCityFilter !== "all" && o.city !== ordersCityFilter) return false;
  const f = ordersStatusFilter;
  if (f === "all") return true;
  if (f === "active") return ["new", "paid", "confirmed"].includes(o.status);
  return o.status === f;
}

function orderMatchesSearch(o) {
  const q = ordersSearch.trim().toLowerCase();
  if (!q) return true;
  return String(o.id).includes(q)
    || (o.username || "").toLowerCase().includes(q)
    || String(o.user_id).includes(q)
    || (o.phone || "").toLowerCase().includes(q);
}

function renderOrders() {
  const list = adminOrders.filter(o => orderMatchesFilter(o) && orderMatchesSearch(o));
  if (!list.length) {
    const msg = ordersSearch.trim() ? "Ничего не найдено." : "Заказов нет.";
    $("ordersList").innerHTML = `<p style="color:var(--hint)">${msg}</p>`; return;
  }
  $("ordersList").innerHTML = list.map(o => {
    const st = OSTATUS[o.status] || { label: o.status, cls: "new" };
    const items = (o.items || []).map(it =>
      `<div class="oitem"><span>${esc(it.name)}${it.flavor ? " · " + esc(it.flavor) : ""} × ${it.qty}</span><span>${(it.price * it.qty).toFixed(2)} Br</span></div>`).join("");
    const receipt = o.receipt_url ? `<div class="oreceipt"><img src="${o.receipt_url}" alt="чек"></div>` : "";
    const who = o.username ? "@" + esc(o.username) : "id " + o.user_id;
    const PM = { card: "💳 картой", cash: "💵 наличными", none: "🚕 при получении" };
    const deliv = o.delivery_method
      ? `<div class="odeliv">🚚 ${esc(o.delivery_method)}${o.delivery_address ? ": " + esc(o.delivery_address) : ""}${o.delivery_fee ? ` (+${o.delivery_fee.toFixed(2)} Br)` : ""}${o.payment_method ? " · " + (PM[o.payment_method] || o.payment_method) : ""}</div>`
      : "";
    const contact = (o.phone ? `<div class="odeliv">📞 ${esc(o.phone)}</div>` : "")
      + (o.comment ? `<div class="odeliv">💬 ${esc(o.comment)}</div>` : "");
    let acts = "";
    if (o.status === "paid") acts = `<button class="ok" data-oact="confirm" data-oid="${o.id}">✅ Подтвердить</button><button class="rej" data-oact="reject" data-oid="${o.id}">✖️ Отклонить</button>`;
    else if (o.status === "confirmed") acts = `<button class="issue" data-oact="issued" data-oid="${o.id}">📦 Выдан</button><button class="rej" data-oact="reject" data-oid="${o.id}">✖️ Отклонить</button>`;
    else if (o.status === "new") acts = `<button class="rej" data-oact="reject" data-oid="${o.id}">✖️ Отменить</button>`;
    // Правка возможна, пока заказ не выдан: «осталась одна» и «добавьте ещё»
    // — обычное дело у прилавка, а раньше на это был один ответ: отклонить.
    const edit = ["new", "paid", "confirmed"].includes(o.status)
      ? `<button class="omsg" data-oedit="${o.id}">✏️ Изменить состав</button>` : "";
    return `<div class="ocard">
      <div class="ocard-top">
        <div><div class="ocard-id">Заказ #${o.id}</div>
          <div class="ocard-sub">${esc(o.city)} · ${who}<br>${esc(o.created_at)}</div></div>
        <span class="obadge ${st.cls}">${st.label}</span>
      </div>
      ${deliv}${contact}
      <div class="oitems">${items}<div class="ototal"><span>Итого</span><span>${(+o.total).toFixed(2)} Br</span></div></div>
      ${receipt}
      <div class="oacts">${acts}<button class="omsg" data-omsg="${o.user_id}" data-owho="${esc(who)}">✍️ Написать</button>${edit}<button class="omsg" data-ocomp="${o.id}" data-owho="${esc(who)}">🎁 Компенсация</button></div>
    </div>`;
  }).join("");
  $("ordersList").querySelectorAll("[data-oact]").forEach(b => b.onclick = () => orderAction(+b.dataset.oid, b.dataset.oact));
  $("ordersList").querySelectorAll("[data-omsg]").forEach(b => b.onclick = () => openAdminMsg(+b.dataset.omsg, b.dataset.owho));
  $("ordersList").querySelectorAll("[data-oedit]").forEach(b => b.onclick = () => openOrderEdit(+b.dataset.oedit));
  $("ordersList").querySelectorAll("[data-ocomp]").forEach(b => b.onclick = () => openCompensation(+b.dataset.ocomp, b.dataset.owho));
}

// ----- Правка состава заказа -----
let oeditOrder = null, oeditQty = {};
$("oeditClose").onclick = () => $("oeditView").classList.remove("show");

function openOrderEdit(id) {
  oeditOrder = adminOrders.find(o => o.id === id);
  if (!oeditOrder) return;
  oeditQty = {};
  (oeditOrder.items || []).forEach((it, i) => { oeditQty[i] = +it.qty; });
  $("oeditTitle").textContent = `Заказ #${id} · ${oeditOrder.city}`;
  renderOrderEdit();
  $("oeditView").classList.add("show");
}

function renderOrderEdit() {
  const items = oeditOrder.items || [];
  $("oeditItems").innerHTML = items.map((it, i) => {
    const q = oeditQty[i];
    return `<div class="admrow">
      <div class="an">${esc(it.name)}${it.flavor ? " · " + esc(it.flavor) : ""}
        <small>${(+it.price).toFixed(2)} Br за шт${q !== +it.qty ? ` · было ${it.qty}` : ""}</small></div>
      <button class="iconbtn" data-oq="-1" data-i="${i}">−</button>
      <b style="min-width:22px;text-align:center">${q}</b>
      <button class="iconbtn" data-oq="1" data-i="${i}">+</button></div>`;
  }).join("");
  // Считаем ту же сумму, что и сервер: товары минус скидки плюс доставка.
  const subtotal = items.reduce((s, it, i) => s + (+it.price) * oeditQty[i], 0);
  const off = Math.min(oeditOrder.promo_discount || 0, subtotal) + (oeditOrder.coins_discount || 0);
  const total = Math.max(0, subtotal - off) + (oeditOrder.delivery_fee || 0);
  $("oeditTotal").textContent = total.toFixed(2) + " Br";
  $("oeditItems").querySelectorAll("[data-oq]").forEach(b => b.onclick = () => {
    const i = +b.dataset.i;
    oeditQty[i] = Math.max(0, oeditQty[i] + (+b.dataset.oq));
    renderOrderEdit();
  });
}

$("oeditSave").onclick = async () => {
  const changed = Object.keys(oeditQty).some(i => oeditQty[i] !== +oeditOrder.items[i].qty);
  if (!changed) { alertMsg("Ничего не изменилось."); return; }
  if (!Object.values(oeditQty).some(q => q > 0)) {
    alertMsg("Пустой заказ — это отмена. Закройте окно и нажмите «Отклонить»."); return;
  }
  $("oeditSave").disabled = true;
  try {
    const r = await fetch("/api/admin/order/items", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id: oeditOrder.id, qty: oeditQty }) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "no_stock" ? `На полке только ${d.have} шт: ${d.name}`
             : d.error === "closed" ? "Заказ уже закрыт — правка невозможна."
             : "Не получилось изменить заказ.");
      return;
    }
    $("oeditView").classList.remove("show");
    await loadAdminOrders();
    await refreshProducts();
    toast("Состав изменён, покупателю отправлено");
  } catch (e) { alertMsg("Сеть недоступна."); }
  finally { $("oeditSave").disabled = false; }
};

// ----- Отказ с причиной -----
const REJECT_REASONS = [
  ["out", "Товара не оказалось"], ["receipt", "Чек не подошёл"],
  ["client", "Клиент передумал"], ["duplicate", "Дубль заказа"], ["", "Без причины"],
];
let orejId = null, orejReason = "out";
$("orejClose").onclick = () => $("orejView").classList.remove("show");

function openOrderReject(id) {
  orejId = id; orejReason = "out";
  $("orejTitle").textContent = `Заказ #${id}`;
  $("orejNote").value = "";
  renderRejectReasons();
  $("orejView").classList.add("show");
}
function renderRejectReasons() {
  $("orejReasons").innerHTML = REJECT_REASONS.map(([k, n]) =>
    `<button class="opt ${orejReason === k ? 'active' : ''}" data-rr="${k}">${n}</button>`).join("");
  $("orejReasons").querySelectorAll("[data-rr]").forEach(b =>
    b.onclick = () => { orejReason = b.dataset.rr; renderRejectReasons(); });
}
$("orejGo").onclick = async () => {
  try {
    await fetch("/api/admin/order/status", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, id: orejId, action: "reject", reason: orejReason, note: $("orejNote").value }) });
    $("orejView").classList.remove("show");
    await loadAdminOrders();
    await refreshProducts();
    loadToday();               // отклонённый заказ ушёл из «ждут подтверждения»
  } catch (e) { alertMsg("Сеть недоступна."); }
};

async function orderAction(id, action) {
  if (action === "reject") { openOrderReject(id); return; }
  const ask = action === "issued" ? "Отметить заказ выданным?" : "Подтвердить заказ?";
  const go = async () => {
    try {
      await fetch("/api/admin/order/status", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, id, action }) });
      await loadAdminOrders();
      await refreshProducts();   // остаток мог вернуться при отклонении
      loadToday();               // плитки «ждут подтверждения» / «к выдаче» — сразу
    } catch (e) { alertMsg("Сеть недоступна."); }
  };
  confirmMsg(ask, go);
}

