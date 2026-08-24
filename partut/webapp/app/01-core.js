// 01-core.js — каркас: Telegram, тема, жесты, «Назад», шторки, фильтры,
// настройки покупателя и бонусы
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();                                        // раскрыть на всю высоту
  // Отключаем свайп-вниз, который сворачивает приложение (Bot API 7.7+).
  try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) {}
  // Подстраховка: если Telegram всё же попробует свернуть — снова разворачиваем.
  try { if (tg.onEvent) tg.onEvent("viewportChanged", function () { if (!tg.isExpanded) tg.expand(); }); } catch (e) {}

  // Полноэкранный режим — убирает верхнюю плашку Telegram (Bot API 8.0).
  // В нём контент уходит под «чёлку»/статус-бар, поэтому высоту безопасной
  // зоны считаем и кладём в CSS-переменную --safe-top (её использует шапка).
  function applySafeArea() {
    var sa  = tg.safeAreaInset        || { top: 0 };  // вырез экрана (чёлка/часы)
    var csa = tg.contentSafeAreaInset || { top: 0 };  // плавающие кнопки Telegram
    var top = (sa.top || 0) + (csa.top || 0) + 14;    // запас, чтобы шапка была ниже кнопок Telegram
    document.documentElement.style.setProperty("--safe-top", top + "px");
  }
  try {
    if (tg.requestFullscreen) {
      tg.requestFullscreen();
      if (tg.onEvent) {
        tg.onEvent("fullscreenChanged", applySafeArea);
        tg.onEvent("safeAreaChanged", applySafeArea);
        tg.onEvent("contentSafeAreaChanged", applySafeArea);
      }
      applySafeArea();
    }
  } catch (e) {}

  // Закрытие — на родной кнопке Telegram (в полноэкранном режиме она справа вверху).
  // Спрашиваем подтверждение перед закрытием (плавающий ✕ Telegram, свайп).
  try { if (tg.enableClosingConfirmation) tg.enableClosingConfirmation(); } catch (e) {}
}
const initData = tg ? tg.initData : "";
const tgUser = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;

// ---------- Тема ----------
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem("theme", t);
  try { if (tg && tg.setHeaderColor) tg.setHeaderColor(t === "dark" ? "#102630" : "#ffffff"); } catch (e) {}
  if (activeTab === "profile") renderProfile();
}
function initTheme() {
  const saved = localStorage.getItem("theme");
  const sys = (tg && tg.colorScheme) || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(saved || sys);
}
const currentTheme = () => document.documentElement.getAttribute("data-theme") || "light";

// Категории ведёт владелец в админке, поэтому приходят из базы. Стартовые
// значения — чтобы экран не был пустым, пока ответ в пути.
let categories = [
  { code: "disposable", name: "Одноразки", emoji: "🔋" },
  { code: "liquid", name: "Жидкости", emoji: "💧" },
  { code: "podsystem", name: "Подсистемы", emoji: "🧩" },
];
let CATS = {}, CAT_EMOJI = {}, CAT_OPTS = [];

// Группы для списков моделей и брендов. Раньше списки шли строго по CAT_OPTS,
// и запись с категорией, которой в справочнике нет, ПРОПАДАЛА молча: в базе
// есть, на экране нет, и узнать неоткуда. Теперь для таких заводим последнюю
// группу «Без категории» — видно, что запись есть, и её можно поправить.
function группыКатегорий(записи, свои) {
  свои = свои || CAT_OPTS;
  const известны = new Set(свои.map(([c]) => c));
  const чужие = [...new Set(записи.map(z => z.category || "").filter(c => !известны.has(c)))];
  return [...свои, ...чужие.map(c => [c, c ? `Без категории (${c})` : "Без категории"])];
}
function applyCategories() {
  CATS = { "": "Все товары" };
  CAT_EMOJI = {};
  CAT_OPTS = [];
  categories.forEach(c => {
    CATS[c.code] = c.name;
    if (c.emoji) CAT_EMOJI[c.code] = c.emoji;
    CAT_OPTS.push([c.code, c.name]);
  });
}
applyCategories();
// Характеристики категории (сопротивление, мощность, совместимость…).
const specsOf = (code) => ((categories.find(c => c.code === code) || {}).specs) || [];
const catHasFlavors = (code) => !!(categories.find(c => c.code === code) || {}).has_flavors;
async function fetchCategories() {
  try {
    const list = await bootFetch("categories", "/api/categories");
    if (Array.isArray(list) && list.length) { categories = list; applyCategories(); }
  } catch (e) {}
  // Выбранной категории могло не стать — иначе витрина молча покажет пусто.
  if (cat && !CATS[cat]) cat = "";
}
let locations = [];   // [{id, name}] — точки продаж, приходят из базы
const CUR = `<span class="cur">Br</span>`;
const withUnit = (val, unit) => {
  val = String(val || "").trim();
  if (!val || !unit) return val;                       // «Затяжек» и «Тип» единиц не имеют
  return /[a-zа-я%]/i.test(val) ? val : val + " " + unit;   // единицу мог вписать и сам админ
};

let allProducts = [], cityList = [], city = null, cat = "", search = "", brandFilters = [], flavorFilters = [], sortMode = "default";
const cart = {};
let useCoins = false;   // списывать ли монеты при оформлении
let favs = JSON.parse(localStorage.getItem("favs") || "[]");
let currentOrder = null, me = null, activeTab = "catalog";

const $ = (id) => document.getElementById(id);
// Окошки Telegram появились не сразу: в клиентах старее Bot API 6.2 методы
// showAlert/showConfirm НА ОБЪЕКТЕ ЕСТЬ, но при вызове бросают исключение.
// Поэтому проверять их наличие бесполезно — надо ловить отказ. Без этого
// исключение вылетало ДО самого действия: продавец жал «Подтвердить», а
// заказ молча оставался неподтверждённым.
// Ответ, начатый в шапке страницы. Забираем его один раз: при повторном
// обновлении витрины запрос делается заново, иначе мы бы вечно показывали
// самые первые данные.
function bootFetch(key, url, opts) {
  const b = window.__boot;
  const started = b && b[key];
  if (started) {
    b[key] = null;
    return started.then(r => r.json()).catch(() => fetch(url, opts).then(r => r.json()));
  }
  return fetch(url, opts).then(r => r.json());
}

function alertMsg(m) {
  try {
    if (tg && tg.showAlert) { tg.showAlert(m); return; }
  } catch (e) { /* старый клиент — покажем обычным окном */ }
  try { alert(m); } catch (e) {}
}

// Копирование в буфер. navigator.clipboard есть не везде (старый WebView,
// не-https), и там он молча отказывает — поэтому держим запасной путь через
// скрытое поле. Кнопка «скопировать», которая иногда ничего не делает, хуже,
// чем её отсутствие: человек уверен, что номер у него в буфере.
async function copyText(text) {
  const s = String(text == null ? "" : text);
  if (!s) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch (e) { /* пробуем запасной путь ниже */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = s;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select(); ta.setSelectionRange(0, s.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}

function confirmMsg(question, onYes) {
  try {
    if (tg && tg.showConfirm) { tg.showConfirm(question, ok => { if (ok) onYes(); }); return; }
  } catch (e) { /* старый клиент — спросим обычным окном */ }
  if (confirm(question)) onYes();
}
const esc = (s) => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const NAV = [
  { id: "catalog", label: "Каталог", icon: '<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/>' },
  { id: "bonus", label: "Бонусы", icon: '<rect x="3" y="8" width="18" height="13" rx="1"/><path d="M3 12h18M12 8v13"/><path d="M12 8S9 3 6.5 4.5 9 8 12 8zM12 8s3-5 5.5-3.5S15 8 12 8z"/>' },
  { id: "cart", label: "Корзина", icon: '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.4 12.4a2 2 0 002 1.6h8.7a2 2 0 002-1.6L23 6H6"/>' },
  { id: "fav", label: "Избранное", icon: '<path d="M20.8 5.6a5 5 0 00-7.1 0L12 7.3l-1.7-1.7a5 5 0 10-7.1 7.1L12 21l8.8-8.3a5 5 0 000-7.1z"/>' },
  { id: "profile", label: "Профиль", icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>' },
];
function renderNav() {
  $("nav").innerHTML = NAV.map(n => {
    const cnt = n.id === "cart" ? cartCount() : 0;
    return `<div class="navwrap"><button class="navbtn ${n.id===activeTab?'active':''}" data-tab="${n.id}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${n.icon}</svg>
      ${n.label}</button>${cnt?`<span class="badge-count">${cnt}</span>`:''}</div>`;
  }).join("");
  $("nav").querySelectorAll("[data-tab]").forEach(b => b.onclick = () => showTab(b.dataset.tab));
}
function showTab(id) {
  const order = NAV.map(n => n.id);
  const dir = order.indexOf(id) - order.indexOf(activeTab);   // >0 вперёд, <0 назад
  activeTab = id;
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("show", "slide-l", "slide-r"));
  const el = $("tab-" + id);
  el.classList.add("show");
  if (dir > 0) el.classList.add("slide-r");                   // вперёд — приезжает справа
  else if (dir < 0) el.classList.add("slide-l");              // назад — слева
  renderNav();
  if (id === "cart") renderCart();
  if (id === "fav") renderFav();
  if (id === "bonus") renderBonus();
  if (id === "profile") renderProfile();
  window.scrollTo(0, 0);
}

// ---------- Жесты ----------
// Свайп вправо по экрану = «Назад», экран едет за пальцем (18+/оплата не свайпаются)
document.querySelectorAll(".view").forEach(view => {
  if (view.id === "productView") return;    // у карточки — свой, более богатый жест
  const backBtn = view.querySelector(".viewhead button");
  if (!backBtn) return;
  let sx = 0, sy = 0, dx = 0, mode = null;   // mode: null(не решили)|'h'(тянем назад)|'v'(отдали скроллу)
  view.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) { mode = "v"; return; }
    sx = e.touches[0].clientX; sy = e.touches[0].clientY; dx = 0; mode = null;
    view.style.transition = "none";
  }, { passive: true });
  view.addEventListener("touchmove", (e) => {
    if (mode === "v") return;
    const ddx = e.touches[0].clientX - sx, ddy = e.touches[0].clientY - sy;
    if (mode === null) {
      if (Math.abs(ddx) < 8 && Math.abs(ddy) < 8) return;              // ещё не понятно направление
      mode = (ddx > 0 && Math.abs(ddx) > Math.abs(ddy)) ? "h" : "v";   // вправо+горизонтально — назад
      if (mode === "v") return;
    }
    dx = Math.max(0, ddx);
    e.preventDefault();                        // забираем жест у вертикального скролла
    view.style.transform = `translateX(${dx}px)`;
  }, { passive: false });
  view.addEventListener("touchend", () => {
    if (mode !== "h") { mode = null; return; }
    mode = null;
    const w = view.clientWidth || 320;
    view.style.transition = "transform .2s ease";
    if (dx > Math.min(120, w * 0.33)) {        // достаточно — уезжаем и закрываем
      view.style.transform = `translateX(${w}px)`;
      setTimeout(() => { view.style.transition = "none"; view.style.transform = ""; backBtn.click(); }, 200);
    } else {
      view.style.transform = "";               // мало — вернуть на место
    }
  }, { passive: true });
});
// Карточка товара: свайп-назад с параллаксом каталога, затемнением и учётом скорости
(function () {
  const view = $("productView"), content = document.querySelector(".content"), backBtn = $("prodBack");
  const dim = document.createElement("div"); dim.className = "swipe-dim"; document.body.appendChild(dim);
  let sx = 0, sy = 0, dx = 0, mode = null, t0 = 0;
  const noTrans = () => view.style.transition = content.style.transition = dim.style.transition = "none";
  const anim = () => view.style.transition = content.style.transition = dim.style.transition = "transform .2s ease, opacity .2s ease";
  const setPos = (x) => {
    const w = view.clientWidth || 320;
    view.style.transform = `translateX(${x}px)`;
    content.style.transform = `translateX(${-0.25 * (w - x)}px)`;   // каталог подъезжает справа-налево
    dim.style.opacity = String(0.4 * (1 - x / w));                  // затемнение спадает по мере ухода
  };
  const clear = () => {
    view.style.transition = content.style.transition = dim.style.transition = "";
    view.style.transform = content.style.transform = ""; dim.style.opacity = ""; dim.style.display = "none";
  };
  view.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) { mode = "v"; return; }
    sx = e.touches[0].clientX; sy = e.touches[0].clientY; dx = 0; mode = null; t0 = Date.now(); noTrans();
  }, { passive: true });
  view.addEventListener("touchmove", (e) => {
    if (mode === "v") return;
    const ddx = e.touches[0].clientX - sx, ddy = e.touches[0].clientY - sy;
    if (mode === null) {
      if (Math.abs(ddx) < 8 && Math.abs(ddy) < 8) return;
      mode = (ddx > 0 && Math.abs(ddx) > Math.abs(ddy)) ? "h" : "v";
      if (mode === "v") return;
      dim.style.display = "block";
    }
    dx = Math.max(0, ddx); e.preventDefault(); setPos(dx);
  }, { passive: false });
  view.addEventListener("touchend", () => {
    if (mode !== "h") { mode = null; return; }
    mode = null;
    const w = view.clientWidth || 320;
    const vel = dx / Math.max(1, Date.now() - t0);   // скорость, px/мс
    anim();
    if (dx > w * 0.33 || vel > 0.5) {                // треть ширины ИЛИ быстрый флик
      view.style.transform = `translateX(${w}px)`; content.style.transform = ""; dim.style.opacity = "0";
      setTimeout(() => { clear(); backBtn.click(); }, 200);
    } else {
      setPos(0); setTimeout(clear, 200);
    }
  }, { passive: true });
})();

// Свайп влево/вправо по каталогу = переключение вкладок нижнего меню
(function () {
  const order = NAV.map(n => n.id);
  const content = document.querySelector(".content");
  let sx = 0, sy = 0, track = false;
  content.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) { track = false; return; }
    if (e.target.closest(".chips, input, textarea, select, .searchrow")) { track = false; return; }
    sx = e.touches[0].clientX; sy = e.touches[0].clientY; track = true;
  }, { passive: true });
  content.addEventListener("touchend", (e) => {
    if (!track) return; track = false;
    const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;   // слишком слабо/вертикально
    const i = order.indexOf(activeTab);
    if (dx < 0 && i < order.length - 1) showTab(order[i + 1]);          // влево → следующая
    else if (dx > 0 && i > 0) showTab(order[i - 1]);                     // вправо → предыдущая
  }, { passive: true });
})();

// ---------- Нативная кнопка «Назад» Telegram ----------
// Показываем её, когда открыт экран/шторка, и закрываем верхний слой (а не приложение).
function managedTopLayer() {
  const ov = [...document.querySelectorAll(".overlay.show")];
  if (ov.length) return { type: "overlay", el: ov[ov.length - 1] };
  // экраны с кнопкой в шапке (18+/оплата/готово не трогаем — у них свой поток)
  const views = [...document.querySelectorAll(".view.show")].filter(v => v.querySelector(".viewhead button"));
  if (views.length) return { type: "view", el: views[views.length - 1] };
  return null;
}
function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  if (managedTopLayer()) tg.BackButton.show(); else tg.BackButton.hide();
}
if (tg && tg.BackButton) {
  tg.BackButton.onClick(() => {
    const top = managedTopLayer();
    if (!top) return;
    if (top.type === "overlay") closeOverlay(top.el);
    else top.el.querySelector(".viewhead button").click();
    setTimeout(updateBackButton, 60);
  });
  const obs = new MutationObserver(() => updateBackButton());
  document.querySelectorAll(".view, .overlay").forEach(el =>
    obs.observe(el, { attributes: true, attributeFilter: ["class"] }));
  updateBackButton();
}

async function start() {
  initTheme();
  try {
    const startParam = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";
    me = await bootFetch("me", "/api/me", { method: "POST", headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ initData, start_param: startParam }) });
    if (!me.ok) { alertMsg("Откройте магазин из бота."); return; }
    stockAlerts = new Set(me.alerts || []);   // о чём уже просили сообщить
    remindersOn = me.reminders_on !== false;
    raffleOn = me.raffle_on === true;         // нет розыгрыша — нет и вкладки
    renderNav();
    // Сначала каталог (первый экран), бонусы прогреваем в фоне ПОСЛЕ него.
    if (me.age_ok) loadCatalog().then(prefetchBonuses).then(openDeepLink); else $("ageView").classList.add("show");
  } catch (e) { alertMsg("Сеть недоступна."); }
}

// Продавец приходит сюда из уведомления о заказе — кнопка в чате ведёт на
// #orders, и приложение открывает нужный экран само, без похода по меню.
async function openDeepLink() {
  if (!me || !me.is_admin) return;
  const to = (location.hash || "").replace("#", "");
  if (to !== "orders") return;
  showTab("profile");
  await openAdmin();
  openOrders();
}
// Ответ сервера надо ЧИТАТЬ. «Готово ✅», сказанное не глядя на ответ, —
// худший вид ошибки: человек уверен, что сделал, а на деле не сделано ничего,
// и узнаёт он об этом через неделю по чужой жалобе. Через этот помощник идут
// все админские действия: он возвращает разобранный ответ или null, назвав
// причину человеческими словами.
const ОТКАЗЫ = {
  forbidden:   "Нет доступа.",
  owner_only:  "Это меняет магазин целиком — только у владельца.",
  other_city:  "Это другая точка — её ведёт другой продавец.",
  closed:      "Заказ уже закрыт — обновите список.",
  not_found:   "Не найдено: возможно, кто-то уже удалил.",
  no_raffle:   "Розыгрыш не идёт — подводить нечего.",
  already:     "Это уже сделано.",
  bad_number:  "Проверьте число.",
  bad_input:   "Поле заполнено неверно.",
};

async function админПост(адрес, тело, что) {
  try {
    const r = await fetch(адрес, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ initData, ...(тело || {}) }) });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) return d;
    alertMsg(d.message || ОТКАЗЫ[d.error] || (что ? `Не удалось: ${что}.` : "Не удалось."));
    return null;
  } catch (e) { alertMsg("Сеть недоступна."); return null; }
}

// То же для отправки файла: там тело — FormData, а разбор ответа тот же.
async function админФайл(адрес, fd, что) {
  try {
    const r = await fetch(адрес, { method: "POST", body: fd });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) return d;
    alertMsg(d.message || ОТКАЗЫ[d.error] || (что ? `Не удалось: ${что}.` : "Не удалось."));
    return null;
  } catch (e) { alertMsg("Сеть недоступна."); return null; }
}

function prefetchBonuses() { prefetchDelivery(); fetchBonus(); fetchWheel(); fetchSlot(); fetchRaffle(); }
$("ageYes").onclick = async () => {
  // Не записалось — не закрываем окно: иначе человек ходит по магазину, а на
  // «Оформить» получает отказ по возрасту и не понимает, при чём тут это.
  if (!await админПост("/api/age", {}, "подтвердить возраст")) return;
  $("ageView").classList.remove("show"); loadCatalog().then(prefetchBonuses);
};
$("ageNo").onclick = () => { if (tg) tg.close(); };

async function fetchProducts() {
  allProducts = await bootFetch("products", "/api/products");
}

// Витрина и админка смотрят на разные списки: покупателю снятое с продажи
// не приходит вовсе, а продавцу оно нужно — иначе «скрыть» превращалось бы
// в «потерять». Пока админский список не загружен, работаем по витрине.
let adminProducts = [];
const shelf = () => adminProducts.length ? adminProducts : allProducts;
async function fetchAdminProducts() {
  try {
    const r = await fetch("/api/admin/products", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) adminProducts = d.products || [];
  } catch (e) { /* останемся на витрине — это хуже, но не пусто */ }
}
async function fetchLocations() {
  locations = await bootFetch("locations", "/api/locations");
}
function recomputeCities() {
  cityList = locations.map(l => l.name);   // точки берём из базы (даже пустые)
  if (!cityList.includes(city)) city = cityList[0] || null;
  $("pointName").textContent = city || "Выбери точку";
}
async function loadCatalog() {
  await Promise.all([fetchLocations(), fetchProducts(), fetchCategories()]);
  recomputeCities(); renderChips(); updateFilterBtn(); renderGrid();
  fetchAlsoBought(); fetchFlavors();   // подсказки корзины и справочник вкусов — в фоне
}
async function refreshProducts() {
  await Promise.all([fetchLocations(), fetchProducts(), fetchCategories(), fetchFlavors(),
                     (me && me.is_admin) ? fetchAdminProducts() : null]);
  recomputeCities(); renderChips(); updateFilterBtn(); renderGrid(); renderNav();
  if ($("productsView").classList.contains("show")) renderAdminList();
  if ($("locationsView").classList.contains("show")) renderLocList();
}

function renderChips() {
  $("chips").innerHTML = Object.entries(CATS).map(([code, t]) =>
    `<button class="chip ${code===cat?'active':''}" data-c="${code}">${t}</button>`).join("");
  $("chips").querySelectorAll("[data-c]").forEach(b => b.onclick = () => {
    cat = b.dataset.c; brandFilters = []; flavorFilters = [];   // сменили категорию — сбрасываем фильтры
    renderChips(); updateFilterBtn(); renderGrid();
  });
}

// ---------- Фильтры и сортировка (панель) ----------
const SORTS = [
  ["default", "По умолчанию"], ["price_asc", "Сначала дешевле"], ["price_desc", "Сначала дороже"],
  ["str_desc", "Сначала крепкие"], ["str_asc", "Сначала не крепкие"],
];
const strengthVal = (p) => parseFloat(String(p.strength || "").replace(",", ".")) || 0;

// Вкусы товара: у модели со вкусами это варианты, у обычного — своё поле.
function flavorsOf(p) {
  const list = (p.variants || []).map(v => v.flavor).filter(Boolean);
  if (!list.length && p.flavor) list.push(p.flavor);
  return list;
}
function flavorsInScope() {
  const set = new Set();
  allProducts.forEach(p => {
    if (p.city !== city) return;
    if (cat && p.category !== cat) return;
    flavorsOf(p).forEach(f => set.add(f));
  });
  return [...set].sort((a, b) => a.localeCompare(b, "ru"));
}
function brandsInScope() {
  const set = new Set();
  allProducts.forEach(p => {
    if (p.city !== city) return;
    if (cat && p.category !== cat) return;
    if (p.brand) set.add(p.brand);
  });
  return [...set].sort((a, b) => a.localeCompare(b, "ru"));
}
function filtersActive() { return brandFilters.length > 0 || flavorFilters.length > 0 || sortMode !== "default"; }
function updateFilterBtn() {
  const scope = brandsInScope();
  brandFilters = brandFilters.filter(b => scope.includes(b));   // убрать бренды не из выборки
  const fscope = flavorsInScope();
  flavorFilters = flavorFilters.filter(f => fscope.includes(f));
  $("filterBtn").classList.toggle("on", filtersActive());
}
function openFilter() {
  $("sortList").innerHTML = SORTS.map(([k, n]) =>
    `<button class="opt ${sortMode === k ? 'active' : ''}" data-s="${k}">${n}</button>`).join("");
  $("sortList").querySelectorAll("[data-s]").forEach(b => b.onclick = () => {
    sortMode = b.dataset.s; openFilter(); updateFilterBtn(); renderGrid();
  });
  const brs = brandsInScope();
  // «Все бренды» активно, когда ничего не выбрано; каждый бренд — переключатель
  const allOpt = `<button class="opt ${brandFilters.length ? '' : 'active'}" data-ball="1">Все бренды</button>`;
  const opt = (b) => `<button class="opt ${brandFilters.includes(b) ? 'active' : ''}" data-b="${esc(b)}">${esc(b)}</button>`;
  $("brandFilterList").innerHTML = brs.length
    ? allOpt + brs.map(opt).join("")
    : `<p style="color:var(--hint)">В этой категории брендов нет.</p>`;
  $("brandFilterList").querySelector("[data-ball]") && ($("brandFilterList").querySelector("[data-ball]").onclick = () => {
    brandFilters = []; openFilter(); updateFilterBtn(); renderGrid();
  });
  $("brandFilterList").querySelectorAll("[data-b]").forEach(btn => btn.onclick = () => {
    const b = btn.dataset.b;
    const i = brandFilters.indexOf(b);
    if (i >= 0) brandFilters.splice(i, 1); else brandFilters.push(b);   // добавить/убрать
    openFilter(); updateFilterBtn(); renderGrid();
  });

  // Вкус — то, по чему жидкость и одноразку выбирают в первую очередь.
  // Показываем блок только там, где вкусы вообще есть: у зарядки его быть не должно.
  const fls = flavorsInScope();
  $("flavorFilterLabel").style.display = fls.length ? "" : "none";
  $("flavorFilterList").style.display = fls.length ? "" : "none";
  if (fls.length) {
    const allF = `<button class="opt ${flavorFilters.length ? '' : 'active'}" data-fall="1">Все вкусы</button>`;
    $("flavorFilterList").innerHTML = allF + fls.map(f =>
      `<button class="opt ${flavorFilters.includes(f) ? 'active' : ''}" data-f="${esc(f)}">${esc(f)}</button>`).join("");
    $("flavorFilterList").querySelector("[data-fall]").onclick = () => {
      flavorFilters = []; openFilter(); updateFilterBtn(); renderGrid();
    };
    $("flavorFilterList").querySelectorAll("[data-f]").forEach(btn => btn.onclick = () => {
      const f = btn.dataset.f;
      const i = flavorFilters.indexOf(f);
      if (i >= 0) flavorFilters.splice(i, 1); else flavorFilters.push(f);
      openFilter(); updateFilterBtn(); renderGrid();
    });
  }
  $("filterOverlay").classList.add("show");
}
$("filterBtn").onclick = openFilter;
$("filterApply").onclick = () => closeOverlay($("filterOverlay"));
$("filterReset").onclick = () => { brandFilters = []; flavorFilters = []; sortMode = "default"; openFilter(); updateFilterBtn(); renderGrid(); };

// ---------- Нижние шторки: анимированное закрытие + свайп вниз ----------
function closeOverlay(ov) {
  const sheet = ov.querySelector(".sheet");
  sheet.style.transition = "transform .22s ease";
  sheet.style.transform = "translateY(100%)";
  ov.style.transition = "opacity .22s ease"; ov.style.opacity = "0";
  setTimeout(() => {
    ov.classList.remove("show");
    ov.style.opacity = ""; ov.style.transition = "";
    sheet.style.transition = ""; sheet.style.transform = "";
  }, 220);
}
document.querySelectorAll(".overlay").forEach(ov => {
  const sheet = ov.querySelector(".sheet");
  if (!sheet) return;
  let startY = 0, delta = 0, dragging = false, atTop = false;
  sheet.addEventListener("touchstart", (e) => {
    atTop = sheet.scrollTop <= 0;             // тянуть можно только от самого верха
    startY = e.touches[0].clientY; delta = 0; dragging = false;
  }, { passive: true });
  sheet.addEventListener("touchmove", (e) => {
    const dy = e.touches[0].clientY - startY;
    if (!dragging) {
      if (atTop && dy > 6) { dragging = true; sheet.style.transition = "none"; }
      else return;                            // обычная прокрутка внутри шторки
    }
    delta = Math.max(0, dy);
    e.preventDefault();                        // не даём прокручиваться каталогу позади
    sheet.style.transform = `translateY(${delta}px)`;
  }, { passive: false });
  sheet.addEventListener("touchend", () => {
    if (!dragging) return;
    dragging = false;
    sheet.style.transition = "transform .2s ease";
    if (delta > 90) closeOverlay(ov);         // потянул достаточно — закрываем
    else sheet.style.transform = "";          // иначе — вернуть на место
  });
  // тап по затемнённому фону — тоже закрыть
  ov.addEventListener("click", (e) => { if (e.target === ov) closeOverlay(ov); });
});

function sortProducts(list) {
  const arr = [...list];
  if (sortMode === "price_asc") arr.sort((a, b) => a.price - b.price);
  else if (sortMode === "price_desc") arr.sort((a, b) => b.price - a.price);
  else if (sortMode === "str_desc") arr.sort((a, b) => strengthVal(b) - strengthVal(a));
  else if (sortMode === "str_asc") arr.sort((a, b) => strengthVal(a) - strengthVal(b));
  return arr;
}
// Ищем и по бренду, и по вкусам: «мята» — обычный запрос покупателя, а
// раньше поиск смотрел только в название и не находил ничего.
function searchText(p) {
  return [p.name, p.brand, ...flavorsOf(p)].filter(Boolean).join(" ").toLowerCase();
}
function visibleProducts() {
  const s = search.trim().toLowerCase();
  return allProducts.filter(p => p.city === city
    && (!cat || p.category === cat)
    && (!brandFilters.length || brandFilters.includes(p.brand))
    && (!flavorFilters.length || flavorsOf(p).some(f => flavorFilters.includes(f)))
    && (!s || searchText(p).includes(s)));
}
// withSpecs=false на карточке товара: там крепость и объём уже перечислены
// таблицей характеристик прямо под фото, дублировать их бейджами незачем.
function productBadges(p, withSpecs = true) {
  const b = [];
  // У жидкостей — крепость (мг) и объём (мл). У одноразок затяжки НЕ показываем (они в названии).
  if (withSpecs && p.category === "liquid") {
    if (p.strength) b.push(`<span class="tagbadge">${esc(withUnit(p.strength, "мг"))}</span>`);
    if (p.volume) b.push(`<span class="tagbadge alt">${esc(withUnit(p.volume, "мл"))}</span>`);
  }
  // Отсутствие важнее любой похвалы: если товара нет, «хит» уже не новость.
  if (p.stock <= 0) b.push(`<span class="tagbadge out">Нет в наличии</span>`);
  else if (p.is_hit) b.push(`<span class="tagbadge hit">🔥 Хит</span>`);
  else if (p.stock <= 3) b.push(`<span class="tagbadge warn">Осталось ${p.stock}</span>`);
  return b.length ? `<div class="badges-tr">${b.join("")}</div>` : "";
}
// Подпись под названием. Название почти всегда уже содержит и бренд, и вкус
// («Elf Bar BC10000 Кислое яблоко»), поэтому повторять их целиком — значит
// писать одно и то же дважды. Показываем только то, чего в названии нет.
// Подпись под названием на полке. У одноразки это бренд и вкус, у картриджа —
// сопротивление и совместимость: по ним его и выбирают, а бренд обычно уже
// в названии. Берём первые две заполненные характеристики категории.
function subtitleFor(p) {
  const name = (p.name || "").toLowerCase();
  const parts = [p.brand, p.flavor].filter(Boolean).filter(v => !name.includes(v.toLowerCase()));
  if (parts.length < 2) {
    specsOf(p.category).forEach(s => {
      const v = (p.specs || {})[s.key];
      if (parts.length >= 2 || v === undefined || String(v).trim() === "") return;
      // Не повторяем то, что уже стоит в названии: у одноразки затяжки
      // входят в имя модели, и «Elf Bar 6000 · 6000» выглядит ошибкой.
      if (name.includes(String(v).trim().toLowerCase())) return;
      parts.push(withUnit(v, s.unit));
    });
  }
  return parts.join(" · ");
}

function hasVariants(p) { return p.variants && p.variants.length > 0; }
function variantStock(p, flavor) { const v = (p.variants || []).find(x => x.flavor === flavor); return v ? v.stock : 0; }
function cartKey(id, flavor) { return flavor ? id + "::" + flavor : "" + id; }

// В списках (каталог, избранное, допродажа) показываем уменьшенную копию:
// полноразмерная нужна только на карточке товара. thumb_url есть не у всех
// старых товаров — тогда откатываемся на обычную.
function thumbOf(p) { return p.thumb_url || p.photo_url; }

let remindersOn = true;     // напоминания о повторной покупке (приходит в /api/me)

// Подписки «сообщить о поступлении»: приходят в /api/me, пополняются по нажатию.
let stockAlerts = new Set();
const waitingFor = (id) => stockAlerts.has(id);

async function notifyMe(id) {
  try {
    const r = await fetch("/api/notify-me", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, product_id: id }) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не получилось. Попробуйте позже."); return; }
    if (d.in_stock) { alertMsg("Товар уже в наличии — можно заказывать."); await loadProducts(); return; }
    stockAlerts.add(id);
    renderGrid(); renderFavs();
    if (currentProductId === id) renderProduct();
    alertMsg("Сообщим, как только появится 🔔");
  } catch (e) { alertMsg("Сеть недоступна."); }
}

// Оценка видна прямо на полке, с первого же отзыва — но всегда рядом с их
// числом. «★ 5.0» само по себе выглядит убедительнее сотни оценок 4.6;
// «★ 5.0 · 1 отзыв» такого впечатления не создаёт и не врёт покупателю.
function gridRating(p) {
  const r = p.rating || {};
  if (!r.count) return "";
  return `<div class="cstars"><span class="stars">★</span> ${r.avg.toFixed(1)} <small>· ${r.count} ${plural(r.count, "отзыв", "отзыва", "отзывов")}</small></div>`;
}
function cardHtml(p) {
  const photo = p.photo_url ? `<img src="${thumbOf(p)}" alt="" loading="lazy" decoding="async" data-open="${p.id}">` : `<div class="ph" data-open="${p.id}">${CAT_EMOJI[p.category] || "🛒"}</div>`;
  const isFav = favs.includes(p.id);
  const sub = subtitleFor(p);
  let ctrl;
  if (p.stock <= 0) {
    // Крестик на кнопке был загадкой: кнопка есть, а нажать нельзя и неясно
    // почему. Теперь причина написана бейджем, а вместо мёртвой кнопки —
    // единственное осмысленное действие: попросить сообщить о поступлении.
    ctrl = waitingFor(p.id)
      ? `<button class="notifybtn on" disabled>🔔 Ждёте</button>`
      : `<button class="notifybtn" data-notify="${p.id}">🔔 Жду</button>`;
  } else if (hasVariants(p)) {
    ctrl = `<button class="pickbtn" data-pick="${p.id}">Выбрать</button>`;
  } else {
    const qty = cart[cartKey(p.id)] ? cart[cartKey(p.id)].qty : 0;
    ctrl = qty > 0
      ? `<div class="mini"><button data-dec="${p.id}">−</button><span>${qty}</span><button data-inc="${p.id}">+</button></div>`
      : `<button class="plus" data-inc="${p.id}">+</button>`;
  }
  return `<div class="card${p.stock <= 0 ? " out" : ""}"><div class="imgwrap">${photo}${productBadges(p)}
      <button class="fav ${isFav?'on':''}" data-fav="${p.id}">${isFav?'♥':'♡'}</button></div>
    <div class="cbody"><p class="cname" data-open="${p.id}">${esc(p.name)}</p>
      ${gridRating(p)}
      ${sub ? `<div class="csub">${esc(sub)}</div>` : ""}
      <div class="crow"><span class="cprice">${p.price.toFixed(2)} ${CUR}</span>${ctrl}</div></div></div>`;
}
function renderGrid() {
  const list = sortProducts(visibleProducts());
  $("grid").innerHTML = list.map(cardHtml).join("");
  $("catEmpty").innerHTML = list.length ? "" : `<div class="empty"><div class="circ">🔎</div><h3>Ничего не найдено</h3><p>Другая категория или точка.</p></div>`;
  bindCardButtons($("grid"));
}
function bindCardButtons(root) {
  root.querySelectorAll("[data-inc]").forEach(b => b.onclick = () => changeQty(+b.dataset.inc, +1, b.dataset.flavor || null));
  root.querySelectorAll("[data-dec]").forEach(b => b.onclick = () => changeQty(+b.dataset.dec, -1, b.dataset.flavor || null));
  root.querySelectorAll("[data-fav]").forEach(b => b.onclick = () => toggleFav(+b.dataset.fav));
  root.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openProduct(+b.dataset.open));
  root.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => openProduct(+b.dataset.pick, true));
  root.querySelectorAll("[data-notify]").forEach(b => b.onclick = () => notifyMe(+b.dataset.notify));
}
function changeQty(id, delta, flavor = null) {
  const p = allProducts.find(x => x.id === id); if (!p) return;
  const key = cartKey(id, flavor);
  const max = flavor ? variantStock(p, flavor) : p.stock;
  const cur = cart[key] ? cart[key].qty : 0;
  const next = cur + delta;
  if (next <= 0) delete cart[key];
  else cart[key] = { product_id: id, flavor: flavor || null, qty: Math.min(next, max) };
  renderGrid(); renderNav(); if (activeTab === "cart") renderCart();
}
function toggleFav(id) {
  const i = favs.indexOf(id); if (i >= 0) favs.splice(i, 1); else favs.push(id);
  localStorage.setItem("favs", JSON.stringify(favs));
  renderGrid(); if (activeTab === "fav") renderFav();
}
let searchTimer = 0;
$("searchInput").oninput = (e) => {
  search = e.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderGrid, 160);   // перерисовать после паузы в наборе
};

$("pointBtn").onclick = () => {
  $("pointList").innerHTML = cityList.map(c => `<button class="opt ${c===city?'active':''}" data-city="${esc(c)}">${esc(c)}</button>`).join("");
  $("pointList").querySelectorAll("[data-city]").forEach(b => b.onclick = () => {
    const next = b.dataset.city;
    const switchTo = () => {
      if (next !== city) { for (const k in cart) delete cart[k]; }
      city = next; $("pointName").textContent = city;
      brandFilters = [];   // другая точка — другой набор брендов
      prefetchDelivery();  // заранее подтянем способы получения новой точки
      closeOverlay($("pointOverlay")); updateFilterBtn(); renderGrid(); renderNav();
    };
    if (next !== city && Object.keys(cart).length)
      confirmMsg("Сменить точку? Корзина очистится.", switchTo);
    else switchTo();
  });
  $("pointOverlay").classList.add("show");
};
$("pointClose").onclick = () => closeOverlay($("pointOverlay"));

const cartCount = () => Object.values(cart).reduce((s, it) => s + it.qty, 0);
const cartTotal = () => Object.values(cart).reduce((s, it) => { const p = allProducts.find(x => x.id === it.product_id); return s + (p ? p.price * it.qty : 0); }, 0);
// Сервер отказал, потому что чего-то не хватило. Приводим корзину в
// соответствие с полкой: разобранное убираем, лишнее количество подрезаем.
// Иначе человек жмёт «Оформить» и получает тот же отказ снова и снова.
function reconcileCart(d) {
  const key = (id, flavor) => Object.keys(cart).find(k => {
    const it = cart[k];
    return it.product_id === id && (it.flavor || null) === (flavor || null);
  });
  for (const g of (d.gone || [])) {
    const k = key(g.id, g.flavor);
    if (k) delete cart[k];
  }
  for (const sh of (d.short || [])) {
    const k = key(sh.id, sh.flavor);
    if (!k) continue;
    if (sh.left > 0) cart[k].qty = sh.left; else delete cart[k];
  }
}

function renderCart() {
  const entries = Object.values(cart);
  if (!entries.length) { $("tab-cart").innerHTML = `<div class="empty"><div class="circ">🛒</div><h3>Корзина пуста</h3><p>Добавьте товары из ассортимента.</p></div>`; return; }
  const rows = entries.map(it => {
    const p = allProducts.find(x => x.id === it.product_id); if (!p) return "";
    const thumb = p.photo_url ? `<img src="${thumbOf(p)}" loading="lazy" decoding="async">` : (CAT_EMOJI[p.category] || "🛒");
    const fattr = it.flavor ? ` data-flavor="${esc(it.flavor)}"` : "";
    return `<div class="citem"><div class="thumb">${thumb}</div>
      <div style="flex:1"><div class="ci-name">${esc(p.name)}${it.flavor ? ` — ${esc(it.flavor)}` : ""}</div><div class="ci-price">${(p.price*it.qty).toFixed(2)} ${CUR}</div></div>
      <div class="mini"><button data-dec="${p.id}"${fattr}>−</button><span>${it.qty}</span><button data-inc="${p.id}"${fattr}>+</button></div></div>`;
  }).join("");
  const ups = upsellProducts();
  // Заголовок обещает ровно то, что внутри: «часто берут вместе» — только
  // когда это посчитано по заказам, иначе это просто витрина хитов.
  const upsHtml = ups.length
    ? `<div class="upsell-h">${ups.paired ? "С этим часто берут" : "Добавьте к заказу 🔥"}</div><div class="upsell">${ups.map(ucard).join("")}</div>`
    : "";
  const total = cartTotal();
  const coinVal = bonus.coin_value || 0.01;
  const maxCoins = Math.min(bonus.coins || 0, Math.floor(total / coinVal));
  const discount = useCoins ? +(maxCoins * coinVal).toFixed(2) : 0;
  const payable = +(total - discount).toFixed(2);
  const coinsHtml = maxCoins > 0
    ? `<label class="coinsopt"><input type="checkbox" id="useCoinsChk" ${useCoins ? "checked" : ""}>
         <span>Списать ${maxCoins} 🪙 — скидка ${(maxCoins * coinVal).toFixed(2)} ${CUR}</span></label>`
    : "";
  const totalLine = discount > 0
    ? `<div class="cotot"><span>Итого</span><span><s>${total.toFixed(2)}</s> ${payable.toFixed(2)} ${CUR}</span></div>`
    : "";
  // Подсказка про бесплатную доставку — ради неё порог и вводится: человек
  // сам добирает жидкость, чтобы не платить за дорогу. Считаем от стоимости
  // товаров, не от суммы со скидкой, иначе обещание не совпадёт с расчётом.
  const freeFrom = freeFromCache[city] || 0;
  const freeHtml = !freeFrom ? ""
    : total >= freeFrom
      ? `<div class="freehint on">Доставка бесплатная 🎉</div>`
      : `<div class="freehint">До бесплатной доставки — ещё ${(freeFrom - total).toFixed(2)} ${CUR}</div>`;
  $("tab-cart").innerHTML = `<div class="clist">${rows}</div>${freeHtml}${upsHtml}
    <div class="checkoutbar">${coinsHtml}${totalLine}
      <button class="bigbtn" id="checkout">Оформить · ${payable.toFixed(2)} ${CUR}</button>
      ${docsReady() ? `<div class="termsnote">Оформляя заказ, вы соглашаетесь с
        <a id="termsOffer">офертой</a> и <a id="termsPrivacy">обработкой данных</a>.</div>` : ""}</div>`;
  bindCardButtons($("tab-cart"));
  if ($("useCoinsChk")) $("useCoinsChk").onchange = () => { useCoins = $("useCoinsChk").checked; renderCart(); };
  $("checkout").onclick = openDelivery;
  // Ссылки рядом с кнопкой, а не в дальнем разделе: согласие, до которого надо
  // искать дорогу, согласием не является.
  if ($("termsOffer")) $("termsOffer").onclick = () => openDocs("offer");
  if ($("termsPrivacy")) $("termsPrivacy").onclick = () => openDocs("privacy");
}
// Что покупали вместе — приходит с сервера, считается по выданным заказам.
let alsoBought = {};
async function fetchAlsoBought() {
  try {
    const r = await fetch("/api/also-bought");
    const d = await r.json();
    alsoBought = (d && typeof d === "object") ? d : {};
  } catch (e) { alsoBought = {}; }
}
// Допродажа: сначала то, что реально берут вместе с содержимым корзины,
// потом — хиты. «Просто хиты» советуют всем одно и то же и к корзине
// отношения не имеют; совместная покупка — уже разговор по делу.
function upsellProducts() {
  const inCart = new Set(Object.values(cart).map(it => it.product_id));
  const avail = (id) => {
    const p = allProducts.find(x => x.id === id);
    return (p && p.city === city && p.stock > 0 && !inCart.has(p.id)) ? p : null;
  };
  const picked = [], seen = new Set();
  Object.values(cart).forEach(it => (alsoBought[it.product_id] || []).forEach(id => {
    const p = avail(id);
    if (p && !seen.has(id)) { seen.add(id); picked.push(p); }
  }));
  const paired = picked.length;
  const hits = allProducts.filter(p => p.city === city && p.stock > 0 && !inCart.has(p.id) && !seen.has(p.id))
                          .sort((a, b) => (b.is_hit ? 1 : 0) - (a.is_hit ? 1 : 0));
  const list = picked.concat(hits).slice(0, 8);
  list.paired = paired;      // по этому фронт выбирает честный заголовок блока
  return list;
}
function ucard(p) {
  const photo = p.photo_url
    ? `<img src="${thumbOf(p)}" alt="" loading="lazy" decoding="async" data-open="${p.id}">`
    : `<div class="uph" data-open="${p.id}">${CAT_EMOJI[p.category] || "🛒"}</div>`;
  const btn = hasVariants(p)
    ? `<button class="uadd" data-pick="${p.id}">Выбрать</button>`
    : `<button class="uadd" data-inc="${p.id}">+ ${p.price.toFixed(2)} ${CUR}</button>`;
  return `<div class="ucard">${photo}<div class="uname" data-open="${p.id}">${esc(p.name)}</div>${btn}</div>`;
}

function renderFav() {
  const list = allProducts.filter(p => favs.includes(p.id));
  if (!list.length) { $("tab-fav").innerHTML = `<div class="empty"><div class="circ">♡</div><h3>Нет избранных товаров</h3><p>Добавьте товары, нажав на сердечко.</p></div>`; return; }
  $("tab-fav").innerHTML = `<div class="grid">${list.map(cardHtml).join("")}</div>`;
  bindCardButtons($("tab-fav"));
}

function renderProfile() {
  const name = (tgUser && tgUser.first_name) || "Гость";
  const letter = (name[0] || "Г").toUpperCase();
  const dark = currentTheme() === "dark";
  $("tab-profile").innerHTML = `<div class="prof">
    <div class="profcard"><div class="avatar">${esc(letter)}</div>
      <div><div class="pn">${esc(name)}</div><div class="pid">ID: ${(tgUser && tgUser.id) || "—"}</div></div>
      <div class="coins"><b>${bonus.coins || 0}</b><small>VAPECOINS</small></div></div>
    <div class="plist">
      ${(me && me.is_admin) ? `<div class="prow" id="openAdmin"><span>🛠 Управление</span><span>›</span></div>` : ""}
      <!-- Строка называет ТЕКУЩУЮ тему, а не одну и ту же всегда: раньше при
           свете рядом со словом «Тёмная» горело солнышко — значок и подпись
           противоречили друг другу, и было неясно, что показано, а что будет. -->
      <div class="prow"><span>${dark ? "🌙 Тёмная тема" : "☀️ Светлая тема"}</span><span class="switch ${dark?'on':''}" id="themeSwitch"></span></div>
      <div class="prow" id="openMySettings"><span>⚙️ Мои настройки</span><span>›</span></div>
      <div class="prow" id="openMyOrders"><span>📦 Мои заказы</span><span>›</span></div>
      <div class="prow" id="openSupport"><span>💬 Написать в поддержку</span><span>›</span></div>
    </div></div>`;
  $("themeSwitch").onclick = () => applyTheme(currentTheme() === "dark" ? "light" : "dark");
  $("openMySettings").onclick = openMySettings;
  if ($("openAdmin")) $("openAdmin").onclick = openAdmin;
  $("openMyOrders").onclick = openMyOrders;
  $("openSupport").onclick = () => openSupport();
}
// ----- Настройки покупателя -----
// Точка, телефон и напоминания задаются один раз здесь и подставляются в
// заказ. В самом заказе всё остаётся сменяемым — возвращаться сюда не надо.
let myPointId = null, myPoints = [];
$("myClose").onclick = () => $("myView").classList.remove("show");

// ----- Количество: поле с кнопками –/+ -----
// Один приём на весь магазин. Разметку собирает qtyHtml, поведение вешает
// bindQty — иначе в каждом экране завёлся бы свой обработчик, и они бы
// разошлись: где-то минус уходил бы ниже нуля, где-то не срабатывал бы oninput.
//
// Поле оставляем редактируемым намеренно: набрать 24 быстрее, чем нажать плюс
// двадцать три раза.

function qtyHtml(значение, атрибуты = "", мин = 0) {
  return `<span class="qty" data-min="${мин}">
    <button type="button" class="qtybtn" data-qty="-1">−</button>
    <input inputmode="numeric" value="${значение}" ${атрибуты}>
    <button type="button" class="qtybtn" data-qty="1">+</button></span>`;
}

// Вешает кнопки внутри контейнера. Зовётся ПОСЛЕ вставки разметки, и её можно
// звать повторно: обработчики присваиваются, а не накапливаются.
function bindQty(корень, приПравке) {
  (корень || document).querySelectorAll(".qty").forEach(узел => {
    const поле = узел.querySelector("input");
    const мин = +(узел.dataset.min || 0);
    узел.querySelectorAll(".qtybtn").forEach(кнопка => {
      кнопка.onclick = () => {
        const было = parseInt(поле.value, 10);
        const стало = Math.max(мин, (isNaN(было) ? мин : было) + (+кнопка.dataset.qty));
        поле.value = стало;
        // Событие шлём настоящее: экраны уже слушают oninput, и отдельный путь
        // «а если нажали кнопку» им знать незачем.
        поле.dispatchEvent(new Event("input", { bubbles: true }));
        if (приПравке) приПравке(поле);
      };
    });
    const обновить = () => {
      const n = parseInt(поле.value, 10);
      узел.querySelector('[data-qty="-1"]').disabled = !isNaN(n) && n <= мин;
    };
    поле.addEventListener("input", обновить);
    обновить();
  });
}

// ----- Документы магазина -----
// Оферта и политика обработки данных. Тексты приходят с сервера, а не зашиты
// в приложение: правит их владелец из админки, и ждать выкатки ради запятой
// нельзя. Раз загруженные — держим в памяти: документ читают один раз.
let docsCache = null;

async function openDocs(which) {
  $("docsView").classList.add("show");
  showDocTab(which || "offer");
  if (!docsCache) {
    $("docsText").textContent = "Загрузка…";
    try {
      docsCache = await (await fetch("/api/docs")).json();
    } catch (e) {
      $("docsText").textContent = "Не удалось загрузить. Проверьте связь и попробуйте снова.";
      return;
    }
  }
  showDocTab(which || "offer");
}

function showDocTab(which) {
  document.querySelectorAll("#docsTabs .doctab").forEach(b =>
    b.classList.toggle("on", b.dataset.doc === which));
  if (!docsCache) return;
  // Текст приходит с разметкой <b> — вставляем как разметку, но ТОЛЬКО её:
  // документ пишет владелец, а не покупатель, и всё же лишние теги убираем.
  const сырое = (which === "privacy" ? docsCache.privacy : docsCache.offer) || "";
  $("docsText").innerHTML = сырое
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/&lt;b&gt;/g, "<b>").replace(/&lt;\/b&gt;/g, "</b>");
}

$("docsClose").onclick = () => $("docsView").classList.remove("show");
document.querySelectorAll("#docsTabs .doctab").forEach(b =>
  b.onclick = () => showDocTab(b.dataset.doc));
$("myDocs").onclick = () => openDocs("offer");

// Документы показываем покупателю ТОЛЬКО когда владелец заменил черновик своим
// текстом. Болванка с местами вида [УНП] выглядит как настоящий документ и
// вводит в заблуждение сильнее, чем честное отсутствие.
//
// Условие — готовность, а не переключатель: включить показ после вставки текста
// нельзя забыть, он появится сам.
function docsReady() { return !!(me && me.docs_ready); }

function applyDocsVisibility() {
  if ($("myDocs")) $("myDocs").style.display = docsReady() ? "" : "none";
}

async function openMySettings() {
  $("myView").classList.add("show");
  applyDocsVisibility();
  myPointId = (me && me.my_point) || null;
  $("myPhone").value = (me && me.prefill && me.prefill.phone) || "";
  $("myRemind").classList.toggle("on", remindersOn);
  $("myPoints").innerHTML = `<p style="color:var(--hint);font-size:13px;margin:0">Загрузка…</p>`;
  $("myIdVal").textContent = (tgUser && tgUser.id) || "—";
  renderMyAlerts();
  try {
    const r = await fetch("/api/my-points");
    myPoints = (await r.json()).points || [];
  } catch (e) { myPoints = []; }
  renderMyPoints();
}

$("myIdCopy").onclick = async () => {
  const id = (tgUser && tgUser.id) || "";
  if (!id) return;
  const ok = await copyText(id);
  haptic("impact", "light");
  if (ok) toast("ID скопирован ✓");
};

// Список «жду поступления». Названия берём с витрины; если товар с неё убрали,
// показываем номер — отписаться человек должен уметь в любом случае.
function renderMyAlerts() {
  const box = $("myAlerts");
  const ids = [...stockAlerts];
  if (!ids.length) {
    box.innerHTML = `<div class="dnote" style="margin:6px 0 0">Пока ничего не ждёте. Если товара нет в наличии, на его карточке есть кнопка «Сообщить о поступлении».</div>`;
    return;
  }
  box.innerHTML = ids.map(id => {
    const p = allProducts.find(x => x.id === id);
    return `<div class="prow" style="padding-left:0;padding-right:0">
      <span>${p ? esc(p.name) : "Товар №" + id}</span>
      <button class="closebtn" style="width:auto;padding:6px 10px;color:var(--danger)" data-unwait="${id}">Не ждать</button></div>`;
  }).join("");
  box.querySelectorAll("[data-unwait]").forEach(b => b.onclick = () => stopWaiting(+b.dataset.unwait));
}

async function stopWaiting(id) {
  try {
    const r = await fetch("/api/notify-me", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, product_id: id, off: true }) });
    const d = await r.json();
    if (!d.ok) { alertMsg("Не удалось отписаться."); return; }
    stockAlerts.delete(id);
    renderMyAlerts();
    renderGrid();          // на витрине кнопка снова станет «сообщить о поступлении»
    toast("Больше не ждём ✓");
  } catch (e) { alertMsg("Сеть недоступна."); }
}

function renderMyPoints() {
  if (!myPoints.length) {
    $("myPoints").innerHTML = `<p style="color:var(--hint);font-size:13px;margin:0">Точек самовывоза пока нет — их заводит магазин.</p>`;
    return;
  }
  // «Не выбрано» — обычный вариант, а не отсутствие ответа: человек вправе
  // решать каждый раз заново.
  const строки = [`<button class="opt ${!myPointId ? 'active' : ''}" data-mp="0">Спрашивать при заказе</button>`]
    .concat(myPoints.map(p => `<button class="opt ${myPointId === p.id ? 'active' : ''}" data-mp="${p.id}">
        ${esc(p.address)}<small class="ppnote">${esc(p.city)}${p.note ? ` · ${esc(p.note)}` : ""}</small></button>`));
  $("myPoints").innerHTML = строки.join("");
  $("myPoints").querySelectorAll("[data-mp]").forEach(b => b.onclick = () => {
    myPointId = +b.dataset.mp || null; renderMyPoints();
  });
}

$("myRemind").onclick = () => {
  remindersOn = !remindersOn;
  $("myRemind").classList.toggle("on", remindersOn);
};

$("mySave").onclick = async () => {
  const btn = $("mySave");
  // Огрызок номера хуже пустого поля: он подставится в заказ, и продавец
  // будет звонить в никуда. Пустое поле — законный ответ, его пропускаем.
  const phone = $("myPhone").value.trim();
  if (phone && phone.replace(/\D/g, "").length < 7) {
    alertMsg("Похоже, номер неполный. Проверьте телефон или оставьте поле пустым.");
    return;
  }
  btn.disabled = true; btn.textContent = "Сохраняю…";
  try {
    const r = await fetch("/api/my-settings", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, point_id: myPointId || 0, phone, reminders_on: remindersOn }) });
    const d = await r.json();
    if (!d.ok) {
      alertMsg(d.error === "bad_phone" ? "Похоже, номер неполный. Проверьте телефон."
                                       : "Не удалось сохранить.");
      return;
    }
    // Обновляем то, что подставляется в заказ, без перезагрузки приложения.
    me.my_point = d.point_id;
    if (me.prefill) me.prefill.phone = d.phone || "";
    remindersOn = d.reminders_on !== false;
    $("myView").classList.remove("show");
    toast("Сохранено ✓");
  } catch (e) { alertMsg("Сеть недоступна."); }
  finally { btn.disabled = false; btn.textContent = "Сохранить"; }
};

let supportOrderId = null;
function openSupport(orderId) {
  supportOrderId = orderId || null;
  $("supportText").value = "";
  $("supportHint").textContent = supportOrderId
    ? `Вопрос по заказу #${supportOrderId} — менеджер ответит вам в этом чате.`
    : "Опишите вопрос — менеджер ответит вам в этом чате.";
  $("supportOverlay").classList.add("show");
}
$("supportClose").onclick = () => closeOverlay($("supportOverlay"));
$("supportSend").onclick = async () => {
  const text = $("supportText").value.trim();
  if (!text) { alertMsg("Напишите вопрос."); return; }
  $("supportSend").disabled = true; $("supportSend").textContent = "Отправляю…";
  try {
    const r = await fetch("/api/support", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, text, order_id: supportOrderId }) });
    const d = await r.json();
    if (d.ok && d.delivered) { closeOverlay($("supportOverlay")); alertMsg("Отправлено ✅ Менеджер ответит в этом чате."); }
    else if (d.error === "cooldown") alertMsg(`Слишком часто. Подождите ${d.retry_after || 20} сек.`);
    else alertMsg("Не удалось отправить. Попробуйте позже.");
  } catch (e) { alertMsg("Сеть недоступна."); }
  finally { $("supportSend").disabled = false; $("supportSend").textContent = "Отправить"; }
};

// ---------- Бонусы (vapecoins + рефералы) ----------
let bonus = { coins: 0, referrals: 0, ref_link: "", referral_bonus: 50 };
let raffleOn = false;            // идёт ли розыгрыш — приходит из /api/me
let bonusReady = false, wheelReady = false, slotReady = false;   // кэш: не перезапрашивать зря
async function fetchBonus() {
  try {
    const r = await fetch("/api/bonus", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) { bonus = d; bonusReady = true; }
  } catch (e) {}
}
let bonusTab = "wheel", gameMode = "wheel";
let wheel = { sectors: [], spins: 0, progress: 0, step: 100 };   // шаг в Br, приходит с сервера
let slot = { bets: [2], balance: 0, symbols: [], lines: [] };
let slotBet = parseInt(localStorage.getItem("slotBet") || "2", 10) || 2;   // текущая ставка
let slotHistory = [];   // последние результаты слота (сессия): >0 выигрыш, 0 промах
let _anticHb = null;    // таймер пульс-хаптика на предвкушении
let slotAuto = false;   // авто-прокрут вкл/выкл
let _slotStats = (() => { try { return JSON.parse(localStorage.getItem("slotStats") || "{}"); } catch (e) { return {}; } })();
const slotStats = { spins: _slotStats.spins || 0, won: _slotStats.won || 0, wagered: _slotStats.wagered || 0, best: _slotStats.best || 0 };
const saveSlotStats = () => { try { localStorage.setItem("slotStats", JSON.stringify(slotStats)); } catch (e) {} };
async function fetchWheel() {
  try {
    const r = await fetch("/api/wheel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) { wheel = d; wheelReady = true; }
  } catch (e) {}
}
async function fetchSlot() {
  try {
    const r = await fetch("/api/slot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) { slot = d; slotReady = true; }
  } catch (e) {}
}
async function renderBonus() {
  // Тянем только то, чего ещё нет — и параллельно.
  const jobs = [];
  if (!bonusReady) jobs.push(fetchBonus());
  if (bonusTab === "wheel" && gameMode === "wheel" && !wheelReady) jobs.push(fetchWheel());
  if (bonusTab === "wheel" && gameMode === "slot" && !slotReady) jobs.push(fetchSlot());
  if (bonusTab === "raffle" && !raffleReady) jobs.push(fetchRaffle());
  if (jobs.length) {
    if (!bonusReady) $("tab-bonus").innerHTML = `<div class="bonuswrap"><p style="color:var(--hint)">Загрузка…</p></div>`;
    await Promise.all(jobs);
  }
  // Вкладку показываем, только когда розыгрыш идёт: «Розыгрыши» там, где
  // ничего не разыгрывают, — обещание, которого магазин не давал.
  if (!raffleOn && bonusTab === "raffle") bonusTab = "wheel";
  const seg = `<div class="bseg">
    <button data-bt="wheel" class="${bonusTab === 'wheel' ? 'on' : ''}">🎡 Игры</button>
    ${raffleOn ? `<button data-bt="raffle" class="${bonusTab === 'raffle' ? 'on' : ''}">🏆 Розыгрыши</button>` : ""}
    <button data-bt="ref" class="${bonusTab === 'ref' ? 'on' : ''}">👥 Рефералы</button></div>`;
  let body;
  if (bonusTab === "raffle") {
    body = `<div id="raffleWrap"><p style="color:var(--hint)">Загрузка…</p></div>`;
  } else if (bonusTab === "ref") {
    body = referralHtml();
  } else {
    body = `<div class="bseg" style="margin-bottom:14px">
        <button data-gm="wheel" class="${gameMode === 'wheel' ? 'on' : ''}">🎡 Колесо</button>
        <button data-gm="slot" class="${gameMode === 'slot' ? 'on' : ''}">🎰 Облако Монет</button></div>
      <div id="gameWrap"></div>`;
  }
  $("tab-bonus").innerHTML = `<div class="bonuswrap">${seg}${body}</div>`;
  $("tab-bonus").querySelectorAll("[data-bt]").forEach(b => b.onclick = () => { bonusTab = b.dataset.bt; renderBonus(); });
  $("tab-bonus").querySelectorAll("[data-gm]").forEach(b => b.onclick = () => { gameMode = b.dataset.gm; renderBonus(); });
  if (bonusTab === "wheel") {
    if (gameMode === "slot") renderSlot();
    else { $("gameWrap").innerHTML = `<div id="wheelWrap"></div>`; renderWheel(); }
  } else if (bonusTab === "ref") bindReferral();
  else if (bonusTab === "raffle") renderRaffle();
}

