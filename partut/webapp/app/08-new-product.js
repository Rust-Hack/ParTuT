// 08-new-product.js — новый товар одним маршрутом (этап 2 редизайна «Товары»).
//
// Раньше новый товар заводился в два раздела: модель в «Ассортименте»
// (название, варианты, фото — причём дополнительные фото только после первого
// сохранения), потом «Завезти на точку» (цена, закупка, остаток). Несколько
// запросов подряд, и сбой посередине оставлял полтовара.
//
// Теперь — три шага на одном экране и одна публикация:
// 1. Основное: категория, бренд, название, фото, описание.
// 2. Варианты и характеристики — шаг называется словом категории (вкусы,
//    цвета, сопротивления). У категории без вариантов и характеристик его нет.
// 3. Продажа на точке: точка, цена, закупка, сколько завезли — по вариантам.
//
// Черновик — в телефоне, у каждого человека свой. Фото грузятся сразу при
// выборе и хранятся в черновике идентификаторами Telegram (file_id): File из
// браузера после перезапуска не восстановить. Публикация — одна операция на
// сервере (/api/admin/product/publish): модель, фото, точки, варианты и первый
// приход вместе или никак. Её ключ сохраняется в черновике ДО запроса — повтор
// после обрыва связи или перезапуска не создаст второй товар. Черновик
// стирается только после подтверждённого итога.

const НТ_ХРАНИЛИЩЕ = "partut_newproduct_v1";
const НТ_МАКС_ФОТО = 6;                  // главное и пять в галерее — как у модели
const НТ_МАКС_ШТУК = 100000;             // как у сервера: больше за один приход — похоже на опечатку
let нт = null;                           // черновик, см. нтНовый()
let нтИдёт = false;                      // публикация в пути: второе нажатие ждёт ответа
let нтОшибка = null;                     // отказ, который надо показать: {step, message}
let нтСохраняется = true;                // удаётся ли писать черновик в телефон
let нтЗаметкаВкусов = "";                // что пропущено при добавлении вариантов
let нтОбновление = null;                 // обновление списков после публикации
let нтГрузит = false;                    // очередь фото уже работает
let нтМестный = 0;                       // номер фото, пока у него нет file_id
const нтФайлы = new Map();               // фото без file_id → файл (только в этой сессии)
const нтПревью = new Map();              // фото → картинка из самого файла, пока в этой сессии

// Исход публикации неизвестен: сервер упал, ответ оборвался. Ключ остаётся
// в черновике — повтор с ним второй товар не создаст.
class НтНеизвестно extends Error {}

const нтКлюч = () => `${НТ_ХРАНИЛИЩЕ}.${человек()}`;

function нтНовый() {
  // specs — по категориям: «Объём» у жидкости — миллилитры, у одноразки —
  // затяжки; перенос значения при смене категории был бы враньём.
  // points[].variants — по названию варианта: {on — продаётся ли здесь, qty}.
  return { step: 1, category: (CAT_OPTS[0] || [""])[0], brand: "", name: "", description: "",
           photos: [], flavors: [], flavorInput: "", specs: {}, points: [нтТочка([])],
           token: null, published: null, updated: Date.now() };
}

// Точка по умолчанию — та, что выбрана в списке товаров, потом текущая точка
// магазина, потом первая свободная. Продавцу — только его.
function нтСвоиТочки() { return myScope() ? [myScope()] : locations.map(l => l.name); }
function нтТочка(занятые) {
  const свободные = нтСвоиТочки().filter(c => !занятые.includes(c));
  const город = [admLocFilter, city].find(c => c && свободные.includes(c)) || свободные[0] || "";
  return { city: город, price: "", cost: "", is_hit: false, stock: "", variants: {}, all: "" };
}

function нтПрочитать() {
  try {
    const d = JSON.parse(localStorage.getItem(нтКлюч()) || "null");
    if (!d || !d.step || !Array.isArray(d.points) || !d.points.length) return null;
    return { ...нтНовый(), ...d,
      photos: (Array.isArray(d.photos) ? d.photos : []).filter(f => f && f.file_id)
        .map(f => ({ file_id: f.file_id, thumb: f.thumb || "", status: "ok" })),
      flavors: (Array.isArray(d.flavors) ? d.flavors : []).filter(f => typeof f === "string"),
      specs: d.specs && typeof d.specs === "object" ? d.specs : {},
      points: d.points.map(т => ({ ...нтТочка([]), ...т,
        variants: т && т.variants && typeof т.variants === "object" ? т.variants : {} })) };
  } catch (e) { return null; }            // испорченный черновик — начнём заново
}

// В черновик — только загруженные фото: то, что ещё грузится или не
// загрузилось, после перезапуска всё равно не восстановить. Не сохраняется
// вовсе (приватный режим, нет места) — говорим сразу: иначе человек закроет
// приложение, надеясь на черновик, и потеряет всё.
function нтСохранить() {
  if (!нт) return;
  нт.updated = Date.now();
  try {
    localStorage.setItem(нтКлюч(), JSON.stringify({ ...нт,
      photos: нт.photos.filter(f => f.file_id).map(f => ({ file_id: f.file_id, thumb: f.thumb || "" })) }));
    нтСохраняется = true;
  } catch (e) { нтСохраняется = false; }
  $("npSaveWarn").hidden = нтСохраняется;
}
function нтСтереть() {
  try { localStorage.removeItem(нтКлюч()); } catch (e) {}
  for (const url of нтПревью.values()) { try { URL.revokeObjectURL(url); } catch (e) {} }
  нтПревью.clear(); нтФайлы.clear();
  нт = null; нтОшибка = null; нтЗаметкаВкусов = "";
}
function нтЕстьЧерновик() {
  const ч = нт || нтПрочитать();
  return !!ч && !ч.published && !!(ч.name.trim() || ч.photos.length || ч.flavors.length);
}

// Шаг 2 — только если у категории есть варианты или характеристики.
const нтЕстьШаг2 = () => catHasFlavors(нт.category) || specsOf(нт.category).length > 0;
const нтШаги = () => (нтЕстьШаг2() ? [1, 2, 3] : [1, 3]);
const нтВкусы = () => (catHasFlavors(нт.category) ? нт.flavors : []);
const нтХарактеристики = () => нт.specs[нт.category] || {};
const нтНазваниеШага = (ш) => ш === 1 ? "Основное"
  : ш === 2 ? (catHasFlavors(нт.category) ? `${catVariantMany(нт.category)} и характеристики` : "Характеристики")
  : "Продажа на точке";

// ---------- Вход: кнопка в «Ценах и остатках» ----------
// Новый товар заводит владелец: модель общая для всех точек. Продавцу эта
// кнопка не нужна — сервер всё равно откажет, — у него остаётся «Завезти».
function обновитьКнопкуНового() {
  $("npOpen").hidden = !isOwner();
  $("npOpenNote").textContent = нтЕстьЧерновик() ? "есть черновик — продолжить" : "описание, фото, цена и приход";
}

async function openNewProduct() {
  if (!isOwner()) return;
  $("npView").classList.add("show");
  // Черновик в памяти свежее сохранённого: к нему привязаны фото, которые
  // ещё грузятся. Из телефона читаем только при первом открытии.
  const изТелефона = !нт;
  if (изТелефона) { $("npSteps").innerHTML = ""; $("npBody").innerHTML = loaderHtml(); $("npSum").textContent = ""; }
  await Promise.all([fetchModels(), fetchBrands()]);   // двойники и бренды — свежие
  if (!нт) нт = нтПрочитать() || нтНовый();
  if (!$("npView").classList.contains("show")) return;  // закрыли, пока грузилось
  нтОшибка = null;
  нтНарисовать();
  if (изТелефона && нтЕстьЧерновик()) toast("Черновик восстановлен");
}
$("npOpen").onclick = openNewProduct;
$("npClose").onclick = () => {
  if (нт && нт.published) { нтЗавершить(); return; }   // итог подтверждён — черновик больше не нужен
  нтЗабратьВвод(); нтСохранить();
  $("npView").classList.remove("show");
  обновитьКнопкуНового();
};

// ---------- Рисование ----------
function нтНарисовать() {
  if (!нт) return;
  if (нт.published) { нтИтогПубликации(); return; }
  const шаги = нтШаги();
  if (!шаги.includes(нт.step)) нт.step = 3;           // у категории пропал шаг 2
  const номер = шаги.indexOf(нт.step);
  $("npSteps").innerHTML = шаги.map((ш, i) =>
    `<button type="button" class="npstep${ш === нт.step ? " on" : ""}${i < номер ? " done" : ""}" data-npgo="${ш}"
       ${ш === нт.step ? 'aria-current="step"' : ""}><b>${i < номер ? "✓" : i + 1}</b><span>${esc(нтНазваниеШага(ш))}</span></button>`).join("");
  $("npSteps").querySelectorAll("[data-npgo]").forEach(b => b.onclick = () => нтПерейти(+b.dataset.npgo));
  const ошибка = нтОшибка && нтОшибка.step === нт.step
    ? `<div class="dwarn nperr" role="alert">${esc(нтОшибка.message)}</div>` : "";
  $("npBody").innerHTML = ошибка + (нт.step === 1 ? нтШаг1() : нт.step === 2 ? нтШаг2() : нтШаг3());
  $("npBack").hidden = номер === 0;
  $("npNext").textContent = нт.step === 3 ? (нтИдёт ? "Публикую…" : "Опубликовать") : "Дальше →";
  $("npNext").disabled = нтИдёт;
  $("npSaveWarn").hidden = нтСохраняется;
  нтПривязать();
  нтИтог();
}

function нтШаг1() {
  const категории = CAT_OPTS.map(([c, n]) =>
    `<button type="button" class="ochip ${c === нт.category ? "active" : ""}" data-npcat="${esc(c)}">${esc(n)}</button>`).join("");
  return `<div class="sect nps form">
    <label>Категория</label>
    <div class="ochips chiprow" id="npCats">${категории}</div>
    <label for="npBrand">Бренд</label>
    <div id="npBrandBox">${pickerHtml("npBrand", нт.brand, brandNames(нт.category), "+ Новый бренд…")}</div>
    <label for="npName">Название</label>
    <input id="npName" value="${esc(нт.name)}" placeholder="Например: XROS 3 Mini" autocomplete="off" maxlength="120">
    <div id="npTwin">${нтДвойникHtml()}</div>
    <label>Фото</label>
    <div class="npsub">До ${НТ_МАКС_ФОТО}. Первое — главное: его видно в каталоге.</div>
    <div class="npphotos" id="npPhotos">${нтФотоHtml()}</div>
    <details class="npdesc" ${нт.description ? "open" : ""}>
      <summary>Описание <small class="nphint">· необязательно</small></summary>
      <textarea id="npDesc" rows="4" maxlength="2000" placeholder="Что важно знать покупателю">${esc(нт.description)}</textarea>
    </details>
    ${нтЕстьЧерновик() ? `<button type="button" class="nplink npreset" id="npReset">Стереть черновик и начать заново</button>` : ""}
  </div>`;
}

// Такая модель уже есть — предлагаем завезти её, а не заводить вторую:
// двойник раздвоит витрину, остатки и статистику. Сервер двойника тоже не
// примет, а здесь это видно ещё до публикации. Похожие по названию —
// подсказкой: вдруг это они.
function нтДвойникHtml() {
  const имя = нт.name.trim().toLowerCase();
  if (имя.length < 2) return "";
  const бренд = (нт.brand || "").trim().toLowerCase();
  const своя = models.filter(m => m.category === нт.category);
  const точно = своя.find(m => (m.name || "").trim().toLowerCase() === имя && (m.brand || "").trim().toLowerCase() === бренд);
  if (точно) {
    return `<div class="dwarn">Такая модель уже есть в ассортименте: «${esc(точно.name)}». Вторую заводить не нужно — завезите эту на точку.
      <button type="button" class="npbtn nptwin" data-npstockin="${точно.id}">📥 Завезти «${esc(точно.name)}» на точку</button></div>`;
  }
  const похожие = своя.filter(m => {
    const n = (m.name || "").trim().toLowerCase();
    return n.length >= 3 && (n.includes(имя) || имя.includes(n));
  }).slice(0, 3);
  return похожие.length ? `<div class="npnote npsimilar">Похоже на то, что уже есть: ${похожие.map(m =>
    `<button type="button" class="nplink" data-npstockin="${m.id}">${esc((m.brand ? m.brand + " " : "") + m.name)}</button>`).join(", ")}.
    Если это оно — завезите его на точку, а не заводите новый.</div>` : "";
}

function нтФотоHtml() {
  const ячейки = нт.photos.map((f, i) => {
    const src = (f.local && нтПревью.get(f.local)) || f.thumb;
    const картинка = src ? `<img src="${esc(src)}" alt="Фото ${i + 1}" decoding="async">` : "";
    const состояние = f.status === "failed" ? `<span class="npphstate bad" aria-hidden="true">⚠</span>
        <button type="button" class="npretry" data-npretry="${i}" aria-label="Повторить загрузку фото ${i + 1}">Повторить</button>`
      : f.status !== "ok" ? `<span class="npphstate">загружаю…</span>` : "";
    const пометка = i === 0 ? `<b class="npmain">главное</b>`
      : f.status === "ok" ? `<button type="button" class="npstar" data-npmain="${i}" aria-label="Сделать фото ${i + 1} главным">★</button>` : "";
    return `<div class="npph ${f.status || "ok"}">${картинка}${пометка}${состояние}
      <button type="button" class="npdel" data-npdel="${i}" aria-label="Убрать фото ${i + 1}">✕</button></div>`;
  }).join("");
  const добавить = нт.photos.length < НТ_МАКС_ФОТО
    ? `<button type="button" class="npadd" id="npAddPhoto"><b>＋</b>фото</button>` : "";
  const сбой = нт.photos.find(f => f.status === "failed" && f.error);
  return ячейки + добавить + (сбой ? `<div class="dlverr npphotoerr">${esc(сбой.error)}</div>` : "");
}

function нтШаг2() {
  const части = [];
  if (catHasFlavors(нт.category)) {
    const n = нт.flavors.length;
    части.push(`<div class="sect nps form">
      <label for="npFlavorIn">${esc(catVariantMany(нт.category))}
        <small class="nphint">· ${n ? n : "пока ни одного"}</small></label>
      <div class="brflavors npflv" id="npFlavors">${нт.flavors.map((f, i) =>
        `<span class="fchip">${esc(f)}<button type="button" class="npfx" data-npfx="${i}" aria-label="Убрать «${esc(f)}»">✕</button></span>`).join("")}</div>
      <textarea id="npFlavorIn" rows="3" placeholder="Один или список: через запятую или каждый с новой строки">${esc(нт.flavorInput || "")}</textarea>
      <button type="button" class="npbtn" id="npFlavorAdd">Добавить в список</button>
      <div class="npnote" id="npFlavorNote" aria-live="polite">${esc(нтЗаметкаВкусов)}</div>
      ${n ? "" : `<div class="npnote">Нет вариантов — товар будет одной позицией со своим остатком.</div>`}
    </div>`);
  }
  if (specsOf(нт.category).length) {
    части.push(`<div class="sect nps form" id="npSpecs">${specFieldsHtml(нт.category, нтХарактеристики(), "npsp_")}
      <div class="npnote" id="npFitNote">${нтПодсказкаСовместимости()}</div></div>`);
  }
  return части.join("");
}
// Совместимость вписывают руками: по ней покупатель ищет картридж под своё
// устройство. Пустая — товар не найдётся, а заметить это потом некому.
function нтПодсказкаСовместимости() {
  const поле = specsOf(нт.category).find(s => s.key === "fit");
  if (!поле || String(нтХарактеристики().fit || "").trim()) return "";
  return `«${esc(поле.label)}» не заполнена — покупатель не найдёт товар по названию своего устройства. Впишите модели через запятую.`;
}

function нтШаг3() {
  const вкусы = нтВкусы(), свои = нтСвоиТочки();
  const формы = формыВарианта({ category: нт.category });
  const блоки = нт.points.map((т, i) => {
    const занятые = нт.points.filter((_, j) => j !== i).map(x => x.city);
    const точки = свои.map(c => `<button type="button" class="ochip ${c === т.city ? "active" : ""}"
        data-npcity="${i}" data-c="${esc(c)}" ${занятые.includes(c) ? "disabled" : ""}>${esc(c)}</button>`).join("");
    const пропала = т.city && !свои.includes(т.city)
      ? `<div class="dwarn">Точки «${esc(т.city)}» больше нет — выберите другую.</div>` : "";
    let количество;
    if (вкусы.length) {
      const всем = вкусы.length > 1 ? `<div class="npall">
          <label for="npall${i}">Одно число всем отмеченным</label>
          <input id="npall${i}" data-npallin="${i}" inputmode="numeric" enterkeyhint="done" placeholder="шт" value="${esc(т.all || "")}">
          <button type="button" class="npbtn" data-npall="${i}">Вписать</button></div>` : "";
      количество = `<label>Сколько завезли, шт</label>
        <div class="npsub">Какого-то ${esc(формы[1])} на этой точке нет — снимите у него галочку.</div>
        ${всем}
        <div class="nprows">${вкусы.map(f => {
          const v = т.variants[f] || { on: true, qty: "" };
          return `<div class="dlvrow npvar${v.on ? "" : " off"}">
            <label class="npvcheck"><input type="checkbox" data-npvon="${i}" data-f="${esc(f)}" ${v.on ? "checked" : ""}><span>${esc(f)}</span></label>
            <input data-npvq="${i}" data-f="${esc(f)}" inputmode="numeric" enterkeyhint="next" placeholder="0"
              value="${esc(v.qty || "")}" ${v.on ? "" : "disabled"} aria-label="Сколько завезли: ${esc(f)}"></div>`;
        }).join("")}</div>`;
    } else {
      количество = `<label for="npq${i}">Сколько завезли, шт</label>
        <input id="npq${i}" data-npf="${i}" data-k="stock" inputmode="numeric" placeholder="0" value="${esc(т.stock || "")}">`;
    }
    return `<div class="sect nps form nppoint">
      <div class="nphead"><b>${нт.points.length > 1 ? `Точка ${i + 1}` : "Где продаётся"}</b>
        ${i > 0 ? `<button type="button" class="iconbtn" data-nprm="${i}" aria-label="Убрать точку ${i + 1}">✕</button>` : ""}</div>
      ${свои.length > 1 ? `<div class="ochips chiprow">${точки}</div>` : `<div class="npcity">${esc(т.city || "—")}</div>`}
      ${пропала}
      <div class="rowf">
        <div><label for="npp${i}">Цена за 1 шт, Br</label>
          <input id="npp${i}" data-npf="${i}" data-k="price" inputmode="decimal" placeholder="18.50" value="${esc(т.price || "")}"></div>
        <div><label for="npc${i}">Закупка за 1 шт, Br</label>
          <input id="npc${i}" data-npf="${i}" data-k="cost" inputmode="decimal" placeholder="12.00" value="${esc(т.cost || "")}"></div>
      </div>
      <label class="chk npchk"><input type="checkbox" data-nphit="${i}" ${т.is_hit ? "checked" : ""}> 🔥 Хит</label>
      ${количество}
    </div>`;
  }).join("");
  const ещё = свои.length > нт.points.length
    ? `<button type="button" class="npbtn npmore" id="npAddPoint">＋ Ещё точка</button>` : "";
  return блоки + ещё;
}

// Итог внизу — что уйдёт на витрину: по точке цена, сколько вариантов и штук.
function нтИтог() {
  if (!нт || нт.published) { $("npSum").textContent = ""; return; }
  if (нт.step !== 3) {
    const имя = нт.name.trim();
    const фото = нт.photos.length ? `фото: ${нт.photos.length}` : "без фото";
    $("npSum").textContent = имя ? `«${имя}» · ${catName(нт.category)} · ${фото}` : "Впишите название товара";
    return;
  }
  const вкусы = нтВкусы(), [один, два, пять] = формыВарианта({ category: нт.category });
  $("npSum").innerHTML = нт.points.map(т => {
    const отмечены = вкусы.filter(f => (т.variants[f] || { on: true }).on);
    const штук = вкусы.length
      ? отмечены.reduce((s, f) => s + (нтШтуки((т.variants[f] || {}).qty) || 0), 0)
      : (нтШтуки(т.stock) || 0);
    const цена = нтЧисло(т.price);
    const части = [цена > 0 ? `${цена.toFixed(2)} Br` : "цена?"];
    if (вкусы.length) части.push(`${отмечены.length} ${plural(отмечены.length, один, два, пять)}`);
    части.push(`${штук} шт`);
    return `<div><b>${esc(т.city || "точка?")}</b> — ${части.join(" · ")}</div>`;
  }).join("");
}

// «18,5» и «18.5» — одно число; мусор — NaN, а не тихий ноль.
function нтЧисло(v) {
  const t = String(v ?? "").replace(/\s/g, "").replace(",", ".");
  return /^\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
}
// Пусто — ноль (не завезли). Не целое, отрицательное или больше потолка — NaN.
function нтШтуки(v) {
  const t = String(v ?? "").trim();
  if (!t) return 0;
  return /^\d+$/.test(t) && Number(t) <= НТ_МАКС_ШТУК ? Number(t) : NaN;
}

// ---------- Ввод ----------
// Всё, что вписано в поля, — в черновик. Зовётся перед любым переходом и
// перерисовкой: без этого перерисовка стирала бы набранное.
function нтЗабратьВвод() {
  if (!нт || нт.published) return;
  const тело = $("npBody");
  if ($("npName")) нт.name = $("npName").value;
  if ($("npBrand")) нт.brand = pickerValue("npBrand");
  if ($("npDesc")) нт.description = $("npDesc").value;
  if ($("npFlavorIn")) нт.flavorInput = $("npFlavorIn").value;
  if ($("npSpecs")) нт.specs = { ...нт.specs, [нт.category]: { ...нтХарактеристики(), ...collectSpecs("npSpecs") } };
  тело.querySelectorAll("[data-npf]").forEach(inp => { нт.points[+inp.dataset.npf][inp.dataset.k] = inp.value; });
  тело.querySelectorAll("[data-npallin]").forEach(inp => { нт.points[+inp.dataset.npallin].all = inp.value; });
  тело.querySelectorAll("[data-npvq]").forEach(inp => {
    const т = нт.points[+inp.dataset.npvq], f = inp.dataset.f;
    т.variants[f] = { on: true, ...(т.variants[f] || {}), qty: inp.value };
  });
}
function нтПравка() { нтЗабратьВвод(); нтСохранить(); }

function нтПривязать() {
  const тело = $("npBody");
  const на = (sel, fn) => тело.querySelectorAll(sel).forEach(fn);
  на("[data-npcat]", b => b.onclick = () => {
    нтЗабратьВвод();
    нт.category = b.dataset.npcat;
    нтСохранить(); нтНарисовать();
  });
  bindPicker("npBrand");
  if ($("npBrand")) $("npBrand").addEventListener("change", () => { нтПравка(); нтОбновитьДвойника(); });
  if ($("npBrand_new")) $("npBrand_new").oninput = () => { нтПравка(); нтОбновитьДвойника(); };
  if ($("npName")) $("npName").oninput = () => { нтПравка(); нтОбновитьДвойника(); нтИтог(); };
  if ($("npDesc")) $("npDesc").oninput = нтПравка;
  if ($("npFlavorIn")) $("npFlavorIn").oninput = нтПравка;
  if ($("npReset")) $("npReset").onclick = () => confirmMsg("Стереть черновик? Всё вписанное и загруженные фото пропадут.", () => {
    нтСтереть(); нт = нтНовый(); нтСохранить(); нтНарисовать(); обновитьКнопкуНового();
  });
  нтПривязатьДвойника();
  нтПривязатьФото();
  if ($("npSpecs")) $("npSpecs").querySelectorAll("[data-spec]").forEach(el => el.oninput = el.onchange = () => {
    нтПравка();
    if ($("npFitNote")) $("npFitNote").innerHTML = нтПодсказкаСовместимости();
  });
  на("[data-npfx]", b => b.onclick = () => {
    нтЗабратьВвод(); нт.flavors.splice(+b.dataset.npfx, 1); нтЗаметкаВкусов = ""; нтСохранить(); нтНарисовать();
  });
  if ($("npFlavorAdd")) $("npFlavorAdd").onclick = нтДобавитьВарианты;
  на("[data-npcity]", b => b.onclick = () => {
    нтЗабратьВвод(); нт.points[+b.dataset.npcity].city = b.dataset.c; нтСохранить(); нтНарисовать();
  });
  на("[data-npf], [data-npvq], [data-npallin]", inp => inp.oninput = () => { нтПравка(); нтИтог(); });
  на("[data-npvon]", ch => ch.onchange = () => {
    нтЗабратьВвод();
    const т = нт.points[+ch.dataset.npvon], f = ch.dataset.f;
    т.variants[f] = { qty: "", ...(т.variants[f] || {}), on: ch.checked };
    нтСохранить(); нтНарисовать();
  });
  на("[data-npall]", b => b.onclick = () => {
    нтЗабратьВвод();
    const т = нт.points[+b.dataset.npall], n = нтШтуки(т.all);
    if (!String(т.all || "").trim() || Number.isNaN(n)) { alertMsg(`Впишите целое число от 0 до ${НТ_МАКС_ШТУК}, например 10.`); return; }
    for (const f of нтВкусы()) {
      const v = т.variants[f] || { on: true, qty: "" };
      if (v.on) т.variants[f] = { ...v, qty: String(n) };
    }
    т.all = "";
    нтСохранить(); нтНарисовать();
  });
  на("[data-nphit]", ch => ch.onchange = () => { нт.points[+ch.dataset.nphit].is_hit = ch.checked; нтСохранить(); });
  на("[data-nprm]", b => b.onclick = () => {
    нтЗабратьВвод(); нт.points.splice(+b.dataset.nprm, 1); нтСохранить(); нтНарисовать();
  });
  if ($("npAddPoint")) $("npAddPoint").onclick = () => {
    нтЗабратьВвод(); нт.points.push(нтТочка(нт.points.map(т => т.city))); нтСохранить(); нтНарисовать();
  };
}
function нтОбновитьДвойника() {
  if (!$("npTwin")) return;
  $("npTwin").innerHTML = нтДвойникHtml();
  нтПривязатьДвойника();
}
function нтПривязатьДвойника() {
  $("npBody").querySelectorAll("[data-npstockin]").forEach(b => b.onclick = () => {
    нтПравка();
    openStockIn(+b.dataset.npstockin);          // черновик остаётся — вдруг это всё-таки другой товар
  });
}

// Варианты — по одному или списком: через запятую или с новой строки, как
// их прислал поставщик. Повторы (без учёта регистра) пропускаем и говорим,
// какие именно, — молча выброшенная строка выглядит как потерянная.
function нтДобавитьВарианты() {
  нтЗабратьВвод();
  const новые = String(нт.flavorInput || "").split(/[,;\n]+/).map(x => x.replace(/\s+/g, " ").trim()).filter(Boolean);
  if (!новые.length) { нтЗаметкаВкусов = "Впишите название варианта."; нтНарисовать(); return; }
  const есть = new Set(нт.flavors.map(f => f.toLowerCase()));
  const повторы = [], длинные = [];
  let лишние = 0;
  for (const f of новые) {
    if (f.length > 60) { длинные.push(f.slice(0, 24) + "…"); continue; }
    if (есть.has(f.toLowerCase())) { повторы.push(f); continue; }
    if (нт.flavors.length >= 200) { лишние++; continue; }
    есть.add(f.toLowerCase()); нт.flavors.push(f);
  }
  const заметки = [];
  if (повторы.length) заметки.push(`уже в списке: ${повторы.map(f => `«${f}»`).join(", ")}`);
  if (длинные.length) заметки.push(`длиннее 60 знаков: ${длинные.map(f => `«${f}»`).join(", ")}`);
  if (лишние) заметки.push(`ещё ${лишние} — больше 200 вариантов у товара не бывает`);
  нтЗаметкаВкусов = заметки.length ? "Не добавлено: " + заметки.join("; ") + "." : "";
  нт.flavorInput = "";
  нтСохранить(); нтНарисовать();
  if ($("npFlavorIn")) $("npFlavorIn").focus();
}

// ---------- Фото ----------
// Каждое фото грузится сразу при выборе и по одному: сбой одного не трогает
// ни остальные фото, ни поля, а «Повторить» повторяет только его.
function нтПривязатьФото() {
  if ($("npAddPhoto")) $("npAddPhoto").onclick = () => { $("npFile").value = ""; $("npFile").click(); };
  const тело = $("npBody");
  тело.querySelectorAll("[data-npdel]").forEach(b => b.onclick = () => {
    const [f] = нт.photos.splice(+b.dataset.npdel, 1);
    if (f && f.local) {
      нтФайлы.delete(f.local);
      const url = нтПревью.get(f.local);
      if (url) { try { URL.revokeObjectURL(url); } catch (e) {} нтПревью.delete(f.local); }
    }
    нтСохранить(); нтПерерисоватьФото();
  });
  тело.querySelectorAll("[data-npmain]").forEach(b => b.onclick = () => {
    const [f] = нт.photos.splice(+b.dataset.npmain, 1);
    нт.photos.unshift(f); нтСохранить(); нтПерерисоватьФото();
  });
  тело.querySelectorAll("[data-npretry]").forEach(b => b.onclick = () => {
    const f = нт.photos[+b.dataset.npretry];
    if (!f) return;
    f.status = "queued"; f.error = "";
    нтПерерисоватьФото(); нтЗапуститьОчередь();
  });
}
function нтПерерисоватьФото() {
  if ($("npPhotos")) { $("npPhotos").innerHTML = нтФотоHtml(); нтПривязатьФото(); }
  нтИтог();
}
$("npFile").onchange = () => {
  if (!нт) return;
  const файлы = [...($("npFile").files || [])];
  const места = НТ_МАКС_ФОТО - нт.photos.length;
  for (const файл of файлы.slice(0, Math.max(0, места))) {
    const запись = { local: `l${++нтМестный}`, status: "queued" };
    нтФайлы.set(запись.local, файл);
    try { нтПревью.set(запись.local, URL.createObjectURL(файл)); } catch (e) { /* покажем после загрузки */ }
    нт.photos.push(запись);
  }
  if (файлы.length > места) toast(`Фото — не больше ${НТ_МАКС_ФОТО}: лишние не добавлены`);
  нтПерерисоватьФото();
  нтЗапуститьОчередь();
};
async function нтЗапуститьОчередь() {
  if (нтГрузит) return;                     // уже идёт — новые фото она подберёт сама
  нтГрузит = true;
  try {
    for (;;) {
      const f = нт && нт.photos.find(x => x.status === "queued");
      if (!f) break;
      await нтЗагрузитьФото(f);
    }
  } finally { нтГрузит = false; }
}
async function нтЗагрузитьФото(запись) {
  const файл = нтФайлы.get(запись.local);
  if (!файл) {
    запись.status = "failed"; запись.error = "Файла уже нет — уберите это фото и выберите его заново.";
    нтПерерисоватьФото(); return;
  }
  запись.status = "uploading"; запись.error = ""; нтПерерисоватьФото();
  const fd = new FormData();
  fd.append("initData", initData); fd.append("file", файл);
  try {
    const r = await fetch("/api/admin/photo/draft", { method: "POST", body: fd });
    const d = await r.json().catch(() => null);
    if (!r.ok || !d || !d.ok || !d.file_id) {
      throw new Error((d && (d.message || ОТКАЗЫ[d.error])) || "Фото не загрузилось — нажмите «Повторить».");
    }
    Object.assign(запись, { file_id: d.file_id, thumb: d.thumb || d.url || "", status: "ok", error: "" });
    нтФайлы.delete(запись.local);
    нтСохранить();
  } catch (e) {
    запись.status = "failed";
    запись.error = e && (e.name === "AbortError" || e.name === "TypeError")
      ? `${текстСбоя(e)} Фото не загрузилось — нажмите «Повторить».` : ((e && e.message) || "Фото не загрузилось.");
  }
  нтПерерисоватьФото();
}

// ---------- Шаги ----------
function нтПерейти(шаг) {
  if (!нт || нт.published || нтИдёт) return;
  нтЗабратьВвод();
  if (шаг > нт.step) {
    const с = нтСобрать(нт.step);
    if (с.ошибка) { нтПоказать(с.ошибка); return; }
  }
  нт.step = шаг; нтОшибка = null;
  нтСохранить(); нтНарисовать();
  $("npView").scrollTop = 0;
}
function нтПоказать(ошибка) {
  нтОшибка = ошибка; нт.step = ошибка.step;
  нтСохранить(); нтНарисовать();
  $("npView").scrollTop = 0;
}
$("npBack").onclick = () => {
  const шаги = нтШаги();
  нтПерейти(шаги[Math.max(0, шаги.indexOf(нт.step) - 1)]);
};
$("npNext").onclick = () => {
  if (!нт) return;
  if (нт.published) { нтЗавершить(); return; }
  if (нт.step === 3) { нтОпубликовать(false); return; }
  const шаги = нтШаги();
  нтПерейти(шаги[шаги.indexOf(нт.step) + 1]);
};

// Проверка черновика до шага «до» включительно. Итог — {ошибка: {step,
// message}} или готовое тело публикации. Сервер проверяет всё то же самое
// ещё раз: форма может ошибиться или оказаться старой.
function нтСобрать(до = 3) {
  const стоп = (step, message) => ({ ошибка: { step, message } });
  if (!CAT_OPTS.some(([c]) => c === нт.category)) return стоп(1, "Выберите категорию.");
  const имя = нт.name.trim();
  if (!имя) return стоп(1, "Впишите название товара.");
  if (нт.photos.some(f => f.status === "failed")) return стоп(1, "Одно из фото не загрузилось — нажмите «Повторить» или уберите его.");
  if (до < 3) return {};
  if (нт.photos.some(f => !f.file_id)) return стоп(1, "Фото ещё загружается — дождитесь, пока оно загрузится.");
  const вкусы = нтВкусы(), свои = нтСвоиТочки();
  const точки = [];
  for (const [i, т] of нт.points.entries()) {
    const где = нт.points.length > 1 ? `Точка ${i + 1}: ` : "";
    if (!т.city) return стоп(3, `${где}выберите точку.`);
    if (!свои.includes(т.city)) return стоп(3, `${где}точки «${т.city}» больше нет — выберите другую.`);
    const цена = нтЧисло(т.price);
    if (!(цена > 0) || цена > НТ_МАКС_ШТУК) return стоп(3, `${где}цена — число больше нуля, например 18.50.`);
    // Закупку спрашиваем здесь, как и при завозе: незаполненная, она молча
    // выбрасывает товар из подсчёта прибыли. Ноль — можно, но вписанный руками.
    if (!String(т.cost ?? "").trim()) {
      return стоп(3, `${где}впишите закупку за штуку — без неё прибыль по товару не посчитается. Если закупки не было (подарок, образец), поставьте 0.`);
    }
    const закупка = нтЧисло(т.cost);
    if (!(закупка >= 0) || закупка > НТ_МАКС_ШТУК) return стоп(3, `${где}закупка — число от нуля, например 12.`);
    const точка = { city: т.city, price: Math.round(цена * 100) / 100, cost: Math.round(закупка * 100) / 100, is_hit: !!т.is_hit };
    if (вкусы.length) {
      точка.variants = [];
      for (const f of вкусы) {
        const v = т.variants[f] || { on: true, qty: "" };
        if (!v.on) continue;
        const n = нтШтуки(v.qty);
        if (Number.isNaN(n)) return стоп(3, `${где}«${f}»: количество — целое число от 0 до ${НТ_МАКС_ШТУК}.`);
        точка.variants.push({ flavor: f, stock: n });
      }
      if (!точка.variants.length) return стоп(3, `${где}отметьте хотя бы один вариант, который здесь продаётся.`);
    } else {
      const n = нтШтуки(т.stock);
      if (Number.isNaN(n)) return стоп(3, `${где}количество — целое число от 0 до ${НТ_МАКС_ШТУК}.`);
      точка.stock = n;
    }
    точки.push(точка);
  }
  const specs = {};
  for (const s of specsOf(нт.category)) {
    const v = String(нтХарактеристики()[s.key] ?? "").trim();
    if (v) specs[s.key] = v;
  }
  return { model: { category: нт.category, name: имя, brand: (нт.brand || "").trim(),
                    description: нт.description.trim(), specs, flavors: вкусы },
           photos: нт.photos.map(f => f.file_id), points: точки };
}

// ---------- Публикация ----------
async function нтОпубликовать(подтверждено) {
  if (нтИдёт || !нт || нт.published) return;
  нтЗабратьВвод();
  const с = нтСобрать();
  if (с.ошибка) { нтПоказать(с.ошибка); return; }
  // Без фото — только осознанно: покупатель увидит заглушку вместо товара.
  if (!с.photos.length && !подтверждено) {
    confirmMsg("Опубликовать без фото? Покупатель увидит вместо товара заглушку. Фото можно добавить потом в «Ассортименте».",
      () => нтОпубликовать(true));
    return;
  }
  // Ключ — до запроса и в черновик: закроют приложение, пока запрос в пути, —
  // повтор уйдёт с тем же ключом, и сервер вернёт уже созданный товар.
  const ч = нт;
  if (!ч.token) ч.token = новыйКлючОперации();
  нтСохранить();
  нтИдёт = true; нтОшибка = null; нтНарисовать();
  try {
    const r = await fetch("/api/admin/product/publish", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, client_token: ч.token, model: с.model, photos: с.photos, points: с.points }) });
    if (r.status >= 500) throw new НтНеизвестно("Сервер ответил ошибкой.");
    let d;
    try { d = await r.json(); } catch (e) {
      throw new НтНеизвестно(e && e.name === "AbortError" ? "Сервер не ответил вовремя." : "Ответ сервера оборвался.");
    }
    if (!d || typeof d !== "object") throw new НтНеизвестно("Ответ сервера не разобрать.");
    if (d.ok === true && d.model_id) {
      ч.published = { model_id: d.model_id, products: d.products || [], name: с.model.name, points: с.points };
    } else if (d.error === "token_reused") {
      // Прошлое нажатие уже создало товар — с теми данными, что были тогда.
      // Правки, сделанные после, в него не вошли: так и говорим.
      const было = d.recorded || {};
      ч.published = { model_id: было.model_id, products: было.products || [], name: с.model.name, earlier: true };
    } else if (d.error || d.message) {
      // Отказ с причиной — ничего не создано. Показываем у нужного шага.
      const поле = String(d.field || "");
      const шаг = поле.startsWith("points") ? 3
        : (поле.startsWith("flavors") || поле.startsWith("specs")) && нтЕстьШаг2() ? 2
        : ["exists", "bad_photo", "no_name", "bad_category"].includes(d.error) || поле === "photos" ? 1 : 3;
      if (d.error === "exists") await fetchModels();      // пусть двойник появится с кнопкой «Завезти»
      if (нт === ч) нтОшибка = { step: шаг, message: d.message || ОТКАЗЫ[d.error] || "Не опубликовано — проверьте поля." };
      ч.step = шаг;
      return;
    } else {
      throw new НтНеизвестно("Ответ сервера не разобрать.");
    }
    // Итог подтверждён. Черновик хранит его, пока человек не нажмёт «Готово»:
    // закроют приложение сейчас — при следующем открытии увидят тот же итог.
    нтОбновление = Promise.all([fetchModels(), refreshProducts()]).catch(() => {});
    ensureBrandExists(ч.published.earlier ? "" : с.model.brand);   // новый бренд — в справочник, уже после публикации
  } catch (e) {
    const что = e instanceof НтНеизвестно ? e.message : текстСбоя(e);
    alertMsg(`${что} Товар мог и опубликоваться.\n\nНажмите «Опубликовать» ещё раз — второй раз он не создастся.`);
  } finally {
    нтИдёт = false;
    if (нт === ч) { нтСохранить(); нтНарисовать(); }
  }
}

// ---------- Итог ----------
function нтИтогПубликации() {
  const п = нт.published;
  $("npSteps").innerHTML = "";
  const где = (п.products || []).map(x => {
    const т = (п.points || []).find(t => t.city === x.city);
    if (!т) return `<li><b>${esc(x.city)}</b></li>`;
    const штук = т.variants ? т.variants.reduce((s, v) => s + v.stock, 0) : т.stock;
    return `<li><b>${esc(x.city)}</b> — ${т.price.toFixed(2)} Br, первый приход ${штук} шт</li>`;
  }).join("");
  $("npBody").innerHTML = `<div class="sect nps npdone" role="status">
    <div class="npok" aria-hidden="true">✓</div>
    <h3>${п.earlier ? "Товар уже был опубликован" : "Опубликовано"}</h3>
    <p class="npdname">«${esc(п.name || "")}»${где ? " — на витрине:" : " — на витрине."}</p>
    ${где ? `<ul class="npwhere">${где}</ul>` : ""}
    ${п.earlier ? `<div class="dwarn">Его создало прошлое нажатие — с данными, какими они были тогда. Если после этого вы что-то поправили, поменяйте это в списке товаров.</div>` : ""}
    <p class="npnote">Остаток записан первым приходом — он виден в истории склада товара.</p>
    <button type="button" class="npbtn npagain" id="npAgain">✨ Завести ещё один товар</button>
  </div>`;
  $("npAgain").onclick = () => { нтСтереть(); нт = нтНовый(); нтСохранить(); нтНарисовать(); };
  $("npBack").hidden = true;
  $("npNext").disabled = false;
  $("npNext").textContent = "Готово — показать в списке";
  $("npSum").textContent = "";
  $("npSaveWarn").hidden = true;
}

// «Готово»: черновик стираем (итог подтверждён), новый товар показываем в
// списке — поиском по названию и с фильтрами, при которых он виден.
async function нтЗавершить() {
  const п = нт && нт.published;
  нтСтереть();
  $("npView").classList.remove("show");
  обновитьКнопкуНового();
  if (!п) return;
  await нтОбновление;
  admSearch = п.name || ""; $("admSearch").value = admSearch;
  admCatFilter = "all"; admStockFilter = "all";
  const города = (п.products || []).map(x => x.city);
  if (admLocFilter !== "all" && !города.includes(admLocFilter)) admLocFilter = "all";
  renderAdmFilters();
  renderAdminList();
  выбранныеЧипыВВиду();
}
