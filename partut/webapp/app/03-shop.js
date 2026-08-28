// 03-shop.js — покупка: оформление, реквизиты, карточка товара, отзывы
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ----- Оформление: способ получения + оплата -----
let deliveryMethods = [], selMethod = null, selPayment = null, selAddress = "", selComment = "", selPhone = "";
let selPromo = "", promoOff = 0, promoErr = "";   // введённый код, скидка по нему, текст ошибки
const deliveryCache = {};        // city -> способы получения (чтобы оформление открывалось мгновенно)
const pointsCache = {};          // city -> точки самовывоза этого города
const freeFromCache = {};        // city -> с какой суммы доставка бесплатна (0 = порога нет)
const payMethodsCache = {};      // city -> какие способы оплаты сейчас включены владельцем
let ordersDone = 0;              // сколько заказов магазин уже выдал (0 = показывать рано)
let pickupPoints = [], selPoint = null;
const deliveryPending = {};      // city -> запрос «в полёте» (чтобы не слать второй такой же)
function fetchDeliveryFor(c) {
  if (!c) return Promise.resolve([]);
  // Если запрос за этой точкой уже летит — ждём его, а не запускаем дубль.
  if (deliveryPending[c]) return deliveryPending[c];
  deliveryPending[c] = (async () => {
    try {
      const r = await fetch(`/api/delivery?city=${encodeURIComponent(c)}`);
      const d = await r.json();
      // Сервер отдаёт способы и точки самовывоза одним ответом. Старый
      // формат (просто список) поддерживаем на случай ответа из кэша.
      deliveryCache[c] = Array.isArray(d) ? d : (d.methods || []);
      pointsCache[c] = Array.isArray(d) ? [] : (d.points || []);
      freeFromCache[c] = Array.isArray(d) ? 0 : (d.free_from || 0);
      // Владелец мог выключить способ оплаты. Умолчание — оба разрешены,
      // как раньше: старый кэш-ответ (Array.isArray(d)) этого поля не знает.
      payMethodsCache[c] = Array.isArray(d) ? { cash: true, card: true }
        : { cash: d.pay_cash !== false, card: d.pay_card !== false };
      if (!Array.isArray(d)) ordersDone = d.orders_done || 0;
    } catch (e) { deliveryCache[c] = deliveryCache[c] || []; }
    finally { delete deliveryPending[c]; }
    return deliveryCache[c];
  })();
  return deliveryPending[c];
}
function prefetchDelivery() { if (city && !deliveryCache[city]) fetchDeliveryFor(city); }
$("deliveryClose").onclick = () => closeOverlay($("deliveryOverlay"));
async function openDelivery() {
  if (!Object.keys(cart).length) return;
  // Телефон помним из прошлых заказов: заставлять постоянного покупателя
  // набирать один и тот же номер при каждой покупке — лишнее трение ровно
  // там, где мы его и возвращаем напоминанием.
  selMethod = null; selPayment = null; selAddress = ""; selComment = ""; selPoint = null;
  selPromo = ""; promoOff = 0; promoErr = "";
  selPhone = (me && me.prefill && me.prefill.phone) || "";
  $("deliveryOverlay").classList.add("show");
  if (deliveryCache[city]) {
    deliveryMethods = deliveryCache[city];   // мгновенно из кэша
    pickupPoints = pointsCache[city] || [];
    renderDelivery();
    if (!deliveryPending[city]) fetchDeliveryFor(city);   // тихо обновим на случай изменений
  } else {
    $("deliveryBody").innerHTML = `<p style="color:var(--hint)">Загрузка…</p>`;
    const openedFor = city;
    deliveryMethods = await fetchDeliveryFor(city);
    if (openedFor !== city) return;          // пока грузилось, точку сменили — не рисуем чужое
    pickupPoints = pointsCache[city] || [];
    renderDelivery();
  }
}
let delTotal = 0;   // итог последней отрисовки — им подписываем кнопку

// Что мешает оформить прямо сейчас (или null, если можно). Раньше кнопка
// просто гасла, не объясняя причины, а при способе с адресом, но без оплаты
// оставалась активной с пустым адресом — заказ уходил и его отвергал сервер.
function deliveryBlocker() {
  if (selMethod === null) return "Выберите способ получения";
  const m = deliveryMethods[selMethod];
  if (!m) return "Выберите способ получения";
  // Название поля пишет админ и всегда в именительном («Станция метро»),
  // склонять чужой текст нельзя — иначе выходит «Укажите станция метро».
  // Кавычки снимают вопрос падежа.
  if (m.needs_address && !selAddress.trim())
    return m.address_label ? `Заполните поле «${m.address_label}»` : "Укажите адрес";
  if (!m.needs_address && pickupPoints.length && !selPoint) return "Выберите точку самовывоза";
  // На доставку телефон обязателен: курьеру нужно чем-то позвонить, если
  // он стоит у подъезда, а Telegram у покупателя выключен.
  if (m.needs_address && (selPhone.replace(/\D/g, "").length < 7)) return "Укажите телефон для курьера";
  if (m.needs_payment && !selPayment) return "Выберите способ оплаты";
  return null;
}

function refreshDelSubmit() {
  const b = $("delSubmit"); if (!b) return;
  const blocker = deliveryBlocker();
  b.disabled = !!blocker;
  b.textContent = blocker || `Оформить · ${delTotal.toFixed(2)} Br`;
}

const PROMO_ERRORS = {
  promo_unknown: "Такого кода нет — проверьте написание.",
  promo_used_up: "Код уже разобрали.",
  promo_min: "Код действует от большей суммы заказа.",
  promo_once: "Этим кодом вы уже пользовались.",
};

async function applyPromo() {
  const код = ($("delPromo") ? $("delPromo").value : selPromo).trim().toUpperCase();
  selPromo = код; promoErr = ""; promoOff = 0;
  if (!код) { renderDelivery(); return; }
  const btn = $("promoApply");
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const r = await fetch("/api/promo/check", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, code: код, subtotal: cartTotal() }) });
    const d = await r.json();
    if (d.ok) promoOff = d.discount || 0;
    else promoErr = PROMO_ERRORS[d.error] || "Код не подошёл.";
  } catch (e) { promoErr = "Сеть недоступна."; }
  renderDelivery();
}

function renderDelivery() {
  if (!deliveryMethods.length) {
    $("deliveryBody").innerHTML = `<p style="color:var(--hint)">Для «${esc(city)}» способы получения ещё не настроены.</p>`;
    return;
  }
  const methodBtns = deliveryMethods.map((m, i) =>
    `<button class="opt ${selMethod === i ? 'active' : ''}" data-dm="${i}">${esc(m.name)}${m.fee ? ` · +${m.fee.toFixed(2)} Br` : ''}</button>`).join("");
  let extra = "";
  if (selMethod !== null) {
    const m = deliveryMethods[selMethod];
    // Точка одна и способ самовывозный — выбирать нечего, подставляем её.
    // Проверка «выбрана ли точка» на сервере остаётся: она защищает от
    // подделанного запроса, а не от нерешительного покупателя.
    if (!m.needs_address && pickupPoints.length === 1) selPoint = pickupPoints[0].id;
    if (m.needs_address) {
      extra += `<div class="dlabel">${esc(m.address_label)}</div><input id="delAddr" class="admsearch" placeholder="${esc(m.address_label)}" value="${esc(selAddress)}">`;
    } else if (pickupPoints.length === 1) {
      // Один адрес — это не выбор. Кнопка «выберите из одного варианта»
      // требует действия там, где решать нечего; показываем просто адрес,
      // а точку подставляем сами (см. ниже, перед отрисовкой).
      const т = pickupPoints[0];
      extra += `<div class="dlabel">Куда приехать</div>`;
      extra += `<div class="dpickup">📍 ${esc(т.address)}${т.note ? `<small class="ppnote">${esc(т.note)}</small>` : ""}</div>`;
    } else if (pickupPoints.length) {
      // Точки заведены — значит их и выбирают. Никакого скрытого
      // переключателя: функция, которую нужно найти в настройках способа,
      // всё равно что отсутствует.
      extra += `<div class="dlabel">Куда приедете</div>`;
      extra += pickupPoints.map(p => `<button class="opt ${selPoint === p.id ? 'active' : ''}" data-pp="${p.id}">
             ${esc(p.address)}${p.note ? `<small class="ppnote">${esc(p.note)}</small>` : ""}</button>`).join("");
    } else if (m.pickup_address) {
      // Осталось от старых настроек, где адрес хранился в самом способе.
      // Новый адрес так уже не завести, но показать старый обязаны.
      extra += `<div class="dpickup">📍 ${esc(m.pickup_address)}</div>`;
    }
    if (m.needs_payment) {
      const pm = payMethodsCache[city] || { cash: true, card: true };
      const пары = [pm.card && ["card", "💳 Картой"], pm.cash && ["cash", "💵 Наличными"]].filter(Boolean);
      // Включён ровно один способ — выбирать нечего, подставляем его сам, как
      // и с единственной точкой самовывоза выше: кнопка на один вариант не
      // выбор, а лишний тычок.
      if (пары.length === 1) selPayment = пары[0][0];
      extra += `<div class="dlabel">Оплата</div><div class="paypick">`
        + пары.map(([код, подпись]) => `<button class="opt ${selPayment === код ? 'active' : ''}" data-pm="${код}">${подпись}</button>`).join("")
        + `</div>`;
    } else {
      extra += `<div class="dnote">Оплата не нужна — продавец свяжется с вами.</div>`;
    }
    // Телефон и комментарий заполнять необязательно, поэтому они не должны
    // удлинять шторку: прячем под раскрытие, а в заголовке показываем, что
    // уже заполнено — иначе непонятно, есть там что-то или пусто.
    // На доставку телефон обязателен, поэтому шторку сразу раскрываем и
    // говорим об этом заголовком: иначе человек упрётся в неактивную кнопку
    // и не поймёт, чего от него хотят.
    const нуженТел = !!m.needs_address;
    const нетТел = нуженТел && selPhone.replace(/\D/g, "").length < 7;
    const сводка = [selPhone && esc(selPhone), selComment && "комментарий",
                    promoOff > 0 && `промокод −${promoOff.toFixed(2)} Br`].filter(Boolean).join(" · ");
    const заголовок = нуженТел ? "Телефон для курьера, комментарий, промокод"
                               : "Телефон, комментарий, промокод";
    extra += `<details class="dopt"${(нетТел || selComment || promoErr || promoOff) ? " open" : ""}>
        <summary><span class="dopt-t">${заголовок}</span><b>${сводка || (нуженТел ? "нужен телефон" : "по желанию")}</b></summary>
        <div class="dopt-body">
          <div class="dfield">
            <label for="delPhone">Телефон для связи${нуженТел ? " — обязательно" : ""}</label>
            <input id="delPhone" inputmode="tel" placeholder="+375 29 000-00-00" value="${esc(selPhone)}">
            ${нуженТел ? `<div class="dnote" style="margin:6px 0 0">Курьер позвонит, если не сможет вас найти.</div>` : ""}
          </div>
          <div class="dfield">
            <label for="delComment">Комментарий продавцу</label>
            <input id="delComment" placeholder="этаж, подъезд, во сколько удобно" value="${esc(selComment)}">
          </div>
          <div class="dfield">
            <label for="delPromo">Промокод</label>
            <div class="promorow">
              <input id="delPromo" placeholder="код из группы" value="${esc(selPromo)}">
              <button class="promobtn" id="promoApply">Применить</button>
            </div>
            ${promoErr ? `<div class="dmsg bad">${esc(promoErr)}</div>` : ""}
            ${promoOff > 0 ? `<div class="dmsg ok">Код принят — скидка ${promoOff.toFixed(2)} Br</div>` : ""}
          </div>
        </div>
      </details>`;
  }
  // Итог с разбивкой (live)
  const subtotal = cartTotal();
  const coinVal = bonus.coin_value || 0.01;
  // Монетами добираем то, что осталось после промокода — так же, как считает
  // сервер: иначе показанная скидка не совпадёт с фактической.
  const afterPromo = Math.max(0, subtotal - promoOff);
  const maxCoins = Math.min(bonus.coins || 0, Math.floor(afterPromo / coinVal));
  const discount = useCoins ? +(maxCoins * coinVal).toFixed(2) : 0;
  // Монеты предлагаем И ЗДЕСЬ, а не только в корзине. Это экран, на котором
  // человек думает о деньгах: рядом поле промокода, рядом сумма. Оставить
  // монеты экраном раньше — значит, что накопленное чаще всего не тратится, и
  // программа лояльности существует только на бумаге.
  const coinsRow = maxCoins > 0
    ? `<label class="coinsopt" style="margin:14px 0 0"><input type="checkbox" id="delCoinsChk" ${useCoins ? "checked" : ""}>
         <span>Списать ${maxCoins} 🪙 — скидка ${(maxCoins * coinVal).toFixed(2)} Br</span></label>`
    : "";
  let fee = selMethod !== null ? (deliveryMethods[selMethod].fee || 0) : 0;
  // Порог считаем от стоимости ТОВАРОВ, до скидки монетами: иначе покупатель
  // дотягивается до бесплатной доставки своими же монетами. Сервер проверяет
  // это ещё раз — здесь только показываем.
  const freeFrom = freeFromCache[city] || 0;
  const freeNow = fee > 0 && freeFrom > 0 && subtotal >= freeFrom;
  if (freeNow) fee = 0;
  const finalTotal = +(Math.max(0, subtotal - promoOff - discount) + fee).toFixed(2);
  const sum = `<div class="osum">
    <div class="osum-r"><span>Товары</span><span>${subtotal.toFixed(2)} Br</span></div>
    ${promoOff > 0 ? `<div class="osum-r"><span>Промокод ${esc(selPromo)}</span><span style="color:#2e9e4f">−${promoOff.toFixed(2)} Br</span></div>` : ""}
    ${discount > 0 ? `<div class="osum-r"><span>Скидка монетами</span><span style="color:#2e9e4f">−${discount.toFixed(2)} Br</span></div>` : ""}
    ${fee > 0 ? `<div class="osum-r"><span>Доставка</span><span>+${fee.toFixed(2)} Br</span></div>` : ""}
    ${freeNow ? `<div class="osum-r"><span>Доставка</span><span style="color:#1f8a5f">бесплатно</span></div>` : ""}
    <div class="osum-r osum-total"><span>Итого</span><span>${finalTotal.toFixed(2)} Br</span></div>
  </div>`;
  delTotal = finalTotal;
  // Доказательство ставим у самой кнопки оплаты: сомнение возникает именно
  // в момент перевода денег незнакомому человеку.
  const trust = ordersDone ? `<div class="trustline">🤝 Магазин выполнил ${ordersDone} заказов</div>` : "";
  const btn = `<button class="bigbtn" id="delSubmit" style="margin-top:14px"></button>${trust}`;
  $("deliveryBody").innerHTML = `<div class="dlabel">Способ получения</div>${methodBtns}${extra}${coinsRow}${sum}${btn}`;
  refreshDelSubmit();
  $("deliveryBody").querySelectorAll("[data-dm]").forEach(b => b.onclick = () => {
    selMethod = +b.dataset.dm; selPayment = null;
    // Адрес помним ОТДЕЛЬНО по способу: у метро это станция, у курьера —
    // улица, и подставлять одно вместо другого нельзя.
    const м = deliveryMethods[selMethod] || {};
    const прошлый = ((me && me.prefill && me.prefill.addresses) || {})[м.name || ""] || "";
    selAddress = м.needs_address ? прошлый : "";
    // Для самовывоза подставляем ТУ ЖЕ точку, куда человек ездил в прошлый
    // раз — если она ещё существует.
    // Сначала точка из настроек покупателя, если она есть в этом городе;
    // иначе та, куда он ездил в прошлый раз.
    selPoint = null;
    if (!м.needs_address && pickupPoints.length) {
      const своя = pickupPoints.find(p => p.id === (me && me.my_point));
      if (своя) selPoint = своя.id;
      else if (прошлый) {
        const была = pickupPoints.find(p => p.address === прошлый);
        if (была) selPoint = была.id;
      }
    }
    renderDelivery();
  });
  $("deliveryBody").querySelectorAll("[data-pp]").forEach(b => b.onclick = () => {
    selPoint = +b.dataset.pp; renderDelivery();
  });
  $("deliveryBody").querySelectorAll("[data-pm]").forEach(b => b.onclick = () => { selPayment = b.dataset.pm; renderDelivery(); });
  if ($("delCoinsChk")) $("delCoinsChk").onchange = () => { useCoins = $("delCoinsChk").checked; renderDelivery(); };
  // Кнопку обновляем прямо во время ввода, но БЕЗ перерисовки: перерисовка
  // забрала бы фокус из поля на первом же символе.
  if ($("delAddr")) $("delAddr").oninput = () => { selAddress = $("delAddr").value; refreshDelSubmit(); };
  if ($("delPromo")) $("delPromo").oninput = () => { selPromo = $("delPromo").value.trim().toUpperCase(); };
  if ($("promoApply")) $("promoApply").onclick = applyPromo;
  if ($("delPhone")) $("delPhone").oninput = () => { selPhone = $("delPhone").value; refreshDelSubmit(); };
  if ($("delComment")) $("delComment").oninput = () => { selComment = $("delComment").value; };
  if ($("delSubmit")) $("delSubmit").onclick = doSubmitOrder;
}
let submitting = false;          // защита от второго нажатия, пока заказ оформляется
// Ключ попытки оформления. Кнопка блокируется, но это защита только внутри
// приложения: если ответ потеряется по дороге (метро, слабая связь), человек
// нажмёт снова — а заказ на сервере уже есть. По этому ключу сервер вернёт
// ТОТ ЖЕ заказ вместо второго. Живёт до успеха, потом сбрасывается.
let orderToken = "";
async function doSubmitOrder() {
  if (submitting) return;
  const m = deliveryMethods[selMethod]; if (!m) return;
  if (m.needs_address && !selAddress.trim()) { alertMsg("Введите " + (m.address_label || "адрес").toLowerCase()); return; }
  if (!m.needs_address && pickupPoints.length && !selPoint) { alertMsg("Выберите точку самовывоза."); return; }
  if (m.needs_payment && !selPayment) { alertMsg("Выберите способ оплаты."); return; }
  const items = Object.values(cart).map(it => ({ id: it.product_id, qty: it.qty, flavor: it.flavor || undefined }));
  if (!items.length) return;
  // Окно НЕ закрываем сразу: пока ждём сервер, показываем это на кнопке,
  // иначе экран выглядит зависшим и хочется нажать ещё раз.
  submitting = true;
  if (!orderToken) orderToken = "o" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  const btn = $("delSubmit");
  const btnText = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Оформляем…"; }
  try {
    const r = await fetch("/api/order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      initData, city, items, use_coins: useCoins, client_token: orderToken,
      delivery_method_id: m.id, delivery_address: selAddress.trim(),
      pickup_point_id: selPoint || undefined,
      promo_code: promoOff > 0 ? selPromo : undefined,
      comment: selComment.trim(), phone: selPhone.trim(),
      payment_method: m.needs_payment ? selPayment : "none" }) });
    const d = await r.json();
    if (!d.ok) {
      const errs = { auth: "Откройте магазин из бота.", no_phone: "Для доставки нужен телефон.", age: "Нужно подтвердить 18+.", empty: "Корзина пуста.", multi_city: "Товары из разных точек.", bad_delivery: "Выберите способ получения.", no_address: "Введите адрес.", bad_payment: "Выберите оплату.",
                    no_point: "Выберите точку самовывоза.", bad_point: "Эта точка больше не работает — выберите другую.",
                    promo_unknown: "Такого промокода нет.", promo_used_up: "Промокод уже разобрали.",
                    promo_min: "Промокод действует от большей суммы.", promo_once: "Этим промокодом вы уже пользовались." };
      // Товар мог кончиться, пока человек заполнял доставку: сервер честно
      // отказывает, а мы обновляем витрину, чтобы он видел это своими глазами.
      // Промокод разобрали, пока человек оформлял. Сумма изменилась бы —
      // молча оформлять нельзя, поэтому просим убрать код и повторить.
      if (d.error === "promo_gone") {
        selPromo = ""; promoOff = 0; promoErr = "";
        renderDelivery();
        alertMsg(d.message || "Промокод больше не действует.");
        return;
      }
      if (d.error === "sold_out") {
        await refreshProducts();
        reconcileCart(d);        // без этого «Оформить» упирается в тот же отказ
        renderCart(); renderNav();
        alertMsg(d.message || "Товар разобрали. Обновите корзину.");
        return;
      }
      alertMsg(errs[d.error] || "Ошибка оформления."); return;   // окно оставляем — можно поправить
    }
    closeOverlay($("deliveryOverlay"));
    orderToken = "";                 // заказ есть — следующий будет новым
    currentOrder = d;
    if (d.coins_used) bonus.coins = Math.max(0, (bonus.coins || 0) - d.coins_used);
    useCoins = false;
    for (const k in cart) delete cart[k]; renderNav();   // заказ СОЗДАН — чистим корзину сразу (нет дублей)
    if (d.needs_receipt) {
      $("payTitle").textContent = `Оплата заказа #${d.order_id}`;
      const disc = d.discount ? `Списано ${d.coins_used} 🪙 (−${d.discount.toFixed(2)} Br)\n` : "";
      const feeLine = d.fee ? `Доставка: +${d.fee.toFixed(2)} Br\n` : "";
      renderPayReq(d);
      $("payView").classList.add("show");
    } else {
      // Последний экран перед тем, как человек закроет приложение. Раньше он
      // не говорил ни суммы, ни адреса — а на самовывозе адрес и есть главное:
      // покупатель выбрал его десять секунд назад и уже не помнит, какой из
      // двух. Дальше адрес живёт только в «Моих заказах», куда ещё надо дойти.
      const строки = [`Заказ #${d.order_id} на ${(+d.total).toFixed(2)} Br принят.`];
      if (!m.needs_address && d.delivery_address) строки.push(`Забрать: ${d.delivery_address}.`);
      else if (d.delivery_address) строки.push(`Привезём: ${d.delivery_address}.`);
      строки.push(d.payment_method === "cash"
        ? "Оплата наличными при получении. Продавец свяжется с вами."
        : "Продавец скоро свяжется с вами.");
      $("doneText").textContent = строки.join(" ");
      $("doneView").classList.add("show");
    }
    fetchBonus();
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally {
    submitting = false;
    if (btn) { btn.disabled = false; btn.textContent = btnText; }
  }
}
// ----- Реквизиты для оплаты -----
// Владелец пишет реквизиты свободным текстом («Карта:», номер строкой ниже,
// «или по номеру: +375…»), поэтому разбираем их, а не требуем формат.
const PAY_NUM = /\+?[A-Z0-9][A-Z0-9\s-]{7,}[A-Z0-9]/i;
function payNumber(line) {
  const m = String(line).match(PAY_NUM);
  if (!m) return "";
  const raw = m[0].replace(/[\s-]/g, "");
  // 6+ цифр — это счёт, карта или телефон. Меньше — просто слово с цифрой.
  return raw.replace(/\D/g, "").length >= 6 ? raw : "";
}
function payReqRows(info) {
  const rows = [];
  let pending = "";                       // подпись, у которой номер строкой ниже
  for (const raw of String(info || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const i = line.indexOf(":");
    let label = pending, value = line;
    if (i > 0) { label = line.slice(0, i).trim(); value = line.slice(i + 1).trim(); }
    pending = "";
    if (!value) { pending = label; continue; }     // строка вида «Карта:»
    const num = payNumber(value);
    rows.push(num ? { label, value, copy: num } : { text: line });
  }
  return rows;
}
function renderPayReq(d) {
  const html = [];
  if (d.discount) html.push(`<div class="rline">Списано ${d.coins_used} 🪙 (−${d.discount.toFixed(2)} Br)</div>`);
  if (d.fee) html.push(`<div class="rline">Доставка: +${d.fee.toFixed(2)} Br</div>`);
  const row = (label, value, copy) =>
    `<button class="creq" data-copy="${esc(copy)}"><span class="cl">${label ? `<small>${esc(label)}</small>` : ""}<b>${esc(value)}</b></span><span class="ci">⧉</span></button>`;
  // Сумму тоже копируем: в банковском приложении её вбивают руками.
  html.push(row("К оплате", `${d.total.toFixed(2)} Br`, d.total.toFixed(2)));
  for (const r of payReqRows(d.payment_info)) {
    html.push(r.copy ? row(r.label, r.value, r.copy) : `<div class="rline">${esc(r.text)}</div>`);
  }
  // Реквизитов может не оказаться (магазин их не заполнил или ответ не дошёл).
  // Пустой экран оплаты хуже честной строчки: человек не поймёт, куда платить.
  if (!payReqRows(d.payment_info).some(r => r.copy)) {
    html.push(`<div class="rline" style="color:var(--hint);font-size:13px">Реквизиты не загрузились. Напишите в поддержку — пришлём их в чат.</div>`);
  } else {
    html.push(`<div class="rline" style="color:var(--hint);font-size:13px">Нажмите на номер — он скопируется.</div>`);
  }
  // Про срок говорим ЗДЕСЬ, а не письмом через сутки. Человек, выбравший
  // «оплачу позже», возвращается через два дня и не находит ни заказа, ни
  // товара — и причину узнать неоткуда, если о ней не сказали заранее.
  const часов = d.unpaid_hours || payUnpaidHours;
  if (часов) {
    html.push(`<div class="rline" style="color:var(--hint);font-size:13px">Заказ ждёт чек ${часов} ч, потом отменится сам и товар вернётся в продажу.</div>`);
  }
  $("payReq").innerHTML = html.join("");
  $("payReq").querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => {
    const ok = await copyText(b.dataset.copy);
    haptic("impact", "light");
    if (ok) toast("Скопировано ✓"); else alertMsg("Не удалось скопировать. Выделите номер и скопируйте вручную.");
  });
}

$("uploadBtn").onclick = () => $("receiptFile").click();
$("payCancel").onclick = () => $("payView").classList.remove("show");
$("receiptFile").onchange = async (e) => {
  const file = e.target.files[0]; if (!file || !currentOrder) return;
  $("uploadBtn").disabled = true; $("uploadBtn").textContent = "Отправляю…";
  try {
    const fd = new FormData();
    fd.append("initData", initData); fd.append("order_id", currentOrder.order_id); fd.append("file", file);
    const r = await fetch("/api/receipt", { method: "POST", body: fd });
    const d = await r.json();
    if (d.ok) {
      $("payView").classList.remove("show");
      $("doneText").textContent = `Чек по заказу #${currentOrder.order_id} получен. Продавец подтвердит за ~${currentOrder.confirm_minutes} минут.`;
      $("doneView").classList.add("show");
      for (const k in cart) delete cart[k]; renderNav();
    } else alertMsg(d.message || "Не удалось отправить чек. Попробуйте ещё раз.");
    // Причину говорит сервер: «это не фото» и «файл слишком большой» лечатся
    // по-разному, а «попробуйте ещё раз» с тем же файлом не лечится вовсе.
  } catch (e) { alertMsg(текстСбоя(e)); }
  finally { $("uploadBtn").disabled = false; $("uploadBtn").textContent = "📷 Загрузить чек"; }
};
$("doneBtn").onclick = () => { showTab("catalog"); $("doneView").classList.remove("show"); };

// ---------- Карточка товара ----------
let currentProductId = null;
let selectedFlavor = null;
function openProduct(id, focusFlavors = false) {
  currentProductId = id; selectedFlavor = null; renderProduct();
  $("productView").classList.add("show");
  $("prodBody").scrollTop = 0;   // новая карточка — с начала
  if (focusFlavors) {
    setTimeout(() => {
      const wrap = $("prodBody").querySelector(".fsel-list");
      if (wrap) {
        wrap.scrollIntoView({ behavior: "smooth", block: "center" });
        wrap.classList.remove("attn"); void wrap.offsetWidth;   // рестарт анимации
        wrap.classList.add("attn");
      }
    }, 150);
  }
}
$("prodBack").onclick = () => { $("productView").classList.remove("show"); renderGrid(); };

function renderProduct() {
  const p = allProducts.find(x => x.id === currentProductId);
  if (!p) { $("productView").classList.remove("show"); return; }
  // Галерея: одна картинка — просто картинка, несколько — листаются пальцем.
  const gal = (p.photos && p.photos.length) ? p.photos : (p.photo_url ? [{ url: p.photo_url }] : []);
  const photo = !gal.length
    ? `<div class="ph">${CAT_EMOJI[p.category] || "🛒"}</div>`
    : gal.length === 1
      ? `<img src="${gal[0].url}" alt="">`
      : `<div class="pgal" id="pgal">${gal.map(g => `<img src="${g.url}" alt="" decoding="async">`).join("")}</div>`
        + `<div class="pdots" id="pdots">${gal.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>`;
  // Характеристики берём из настроек категории: у картриджа сопротивление и
  // совместимость, у пода мощность и аккумулятор — а не «бренд и вкус» на всё.
  const info = [];
  if (p.brand) info.push(["Бренд", p.brand]);
  if (p.flavor) info.push(["Вкус", p.flavor]);
  specsOf(p.category).forEach(s => {
    const v = (p.specs || {})[s.key];
    if (v !== undefined && String(v).trim() !== "") info.push([s.label, withUnit(v, s.unit)]);
  });
  const infoHtml = info.length
    ? `<div class="pd-info-wrap">${info.map(([k, v]) => `<div class="pd-info"><span>${k}</span><b>${esc(v)}</b></div>`).join("")}</div>` : "";

  let flavorHtml = "";
  if (hasVariants(p)) {
    // Вкусы — строками на всю ширину, чтобы список не «прыгал» при добавлении.
    flavorHtml = `<div style="font-weight:700;margin:16px 0 8px">${esc(catVariantMany(p.category))}:</div><div class="fsel-list">` +
      p.variants.map(v => {
        const out = v.stock <= 0;
        const qty = cart[cartKey(p.id, v.flavor)] ? cart[cartKey(p.id, v.flavor)].qty : 0;
        const f = esc(v.flavor);
        const right = out ? ""
          : (qty > 0
              ? `<div class="fstep"><button data-fldec="${f}">−</button><span>${qty}</span><button data-flinc="${f}">+</button></div>`
              : `<button class="addbtn" data-fladd="${f}">+</button>`);
        const left = out ? `нет в наличии` : `${v.stock} шт`;
        return `<div class="frow ${qty>0?'on':''} ${out?'out':''}">
            <div class="fname">${f}</div>
            <div class="fbottom"><span class="fbn">${left}</span>${right}</div></div>`;
      }).join("") + `</div>`;
  }

  const rating = p.rating || { avg: 0, count: 0 };
  const rateHtml = rating.count
    ? `<div class="rateline"><span class="stars">${starsHtml(rating.avg)}</span><b>${rating.avg.toFixed(1)}</b><small>${rating.count} ${plural(rating.count, "отзыв", "отзыва", "отзывов")}</small></div>`
    : "";

  $("prodBody").innerHTML = `<div class="pd-img">${photo}${productBadges(p, false)}</div>
    <div class="pd-card">
      <h2 class="pd-name">${esc(p.name)}</h2>
      <div class="pd-price">${p.price.toFixed(2)} ${CUR}</div>
      ${rateHtml}
      ${infoHtml}
      ${p.description ? `<p class="pd-desc">${esc(p.description)}</p>` : ""}
      ${flavorHtml}
      ${rating.count ? `<div class="revs" id="prodRevs"><h3>Отзывы покупателей</h3><div style="color:var(--hint);font-size:13px">Загрузка…</div></div>` : ""}
    </div>`;
  if (rating.count) loadProductReviews(p.id);
  const gwrap = $("pgal");
  if (gwrap) {
    // Точки — единственная подсказка, что фото ещё есть; без них листать никто не догадается.
    const dots = $("pdots").children;
    gwrap.onscroll = () => {
      const i = Math.round(gwrap.scrollLeft / gwrap.clientWidth);
      [...dots].forEach((d, n) => d.classList.toggle("on", n === i));
    };
  }
  $("prodBody").querySelectorAll("[data-fladd]").forEach(b => b.onclick = () => flavorQty(b.dataset.fladd, +1));
  $("prodBody").querySelectorAll("[data-flinc]").forEach(b => b.onclick = () => flavorQty(b.dataset.flinc, +1));
  $("prodBody").querySelectorAll("[data-fldec]").forEach(b => b.onclick = () => flavorQty(b.dataset.fldec, -1));

  renderProdActions(p);
}

function flavorQty(flavor, delta) { changeQty(currentProductId, delta, flavor); renderProduct(); }

// ----- Отзывы в карточке товара -----
// Половинки не рисуем: пять звёзд читаются мгновенно, а «4.5 звезды» покупатель
// всё равно берёт из цифры рядом.
function starsHtml(avg) {
  const n = Math.round(avg);
  return "★".repeat(n) + `<span style="opacity:.3">${"★".repeat(5 - n)}</span>`;
}
async function loadProductReviews(pid) {
  let list = [];
  try {
    const r = await fetch(`/api/reviews?product_id=${pid}`);
    const d = await r.json();
    list = d.ok ? d.reviews : [];
  } catch (e) { list = []; }
  const box = $("prodRevs");
  if (!box || currentProductId !== pid) return;    // карточку успели закрыть
  box.innerHTML = `<h3>Отзывы покупателей</h3>` + (list.length
    ? list.map(v => `<div class="rev">
        <div class="rtop"><span class="stars">${starsHtml(v.rating)}</span><span class="rwho">${esc(v.who)} · ${esc(whenRu(v.created_at).slice(0, 10))}</span></div>
        ${v.text ? `<p>${esc(v.text)}</p>` : ""}
        ${v.reply ? `<div class="revreply"><b>Ответ магазина:</b> ${esc(v.reply)}</div>` : ""}</div>`).join("")
    : `<div style="color:var(--hint);font-size:13px">Пока без текста — только оценки.</div>`);
}

function renderProdActions(p) {
  if (hasVariants(p)) {
    // Итог по этому товару (все набранные вкусы)
    let items = 0;
    Object.values(cart).forEach(it => { if (it.product_id === p.id) items += it.qty; });
    if (items > 0) {
      $("prodActions").innerHTML = `<button class="bigbtn" id="pdGoCart">В корзину · ${items} шт · ${(items*p.price).toFixed(2)} ${CUR}</button>`;
      $("pdGoCart").onclick = () => { $("productView").classList.remove("show"); renderGrid(); showTab("cart"); };
    } else {
      $("prodActions").innerHTML = `<div style="text-align:center;color:var(--hint);padding:10px 0">Нажмите на ${esc(catVariant(p.category).toLowerCase())}, чтобы добавить</div>`;
    }
    return;
  }
  // Обычный товар (без вкусов)
  if (p.stock <= 0) {
    // Мёртвая кнопка «Нет в наличии» просто сообщала плохую новость. Теперь
    // рядом с ней есть что сделать — попросить сообщить о поступлении.
    if (waitingFor(p.id)) {
      $("prodActions").innerHTML = `<button class="bigbtn" disabled>🔔 Сообщим о поступлении</button>`;
    } else {
      $("prodActions").innerHTML = `<button class="bigbtn" id="pdNotify">🔔 Сообщить о поступлении</button>`;
      $("pdNotify").onclick = () => notifyMe(p.id);
    }
    return;
  }
  const qty = cart[cartKey(p.id)] ? cart[cartKey(p.id)].qty : 0;
  if (qty > 0) {
    $("prodActions").innerHTML = `<div class="pd-step"><button id="pdDec">−</button><span>${qty}</span><button id="pdInc">+</button></div>
      <button class="bigbtn" id="pdGoCart">В корзину · ${(p.price*qty).toFixed(2)} ${CUR}</button>`;
    $("pdDec").onclick = () => prodQty(-1);
    $("pdInc").onclick = () => prodQty(+1);
    $("pdGoCart").onclick = () => { $("productView").classList.remove("show"); renderGrid(); showTab("cart"); };
  } else {
    $("prodActions").innerHTML = `<button class="bigbtn" id="pdAdd">+ В корзину</button>`;
    $("pdAdd").onclick = () => prodQty(+1);
  }
}

function prodQty(delta) { changeQty(currentProductId, delta, null); renderProduct(); }

