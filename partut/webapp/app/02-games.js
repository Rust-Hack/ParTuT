// 02-games.js — развлечения: слот «Облако Монет», звук, розыгрыши, колесо
//
// Куски склеиваются сервером по порядку имён в один <script>.
// Порядок важен: это одна программа, разложенная по файлам, а не модули.

// ----- Слот «Облако Монет» (сетка 3×3, барабаны прокручиваются) -----
let slotSpinning = false;
const slotEmojis = () => (slot.symbols || []).map(s => s.emoji);
const haptic = (kind, style) => { try { const h = tg && tg.HapticFeedback; if (!h) return; kind === "notify" ? h.notificationOccurred(style || "success") : h.impactOccurred(style || "light"); } catch (e) {} };
// ---- Звук (Web Audio, без внешних файлов) ----
let audioCtx = null, soundOn = localStorage.getItem("slotSound") !== "0";
function ac() {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}
// Мягкая нота: синус с плавной атакой и экспоненциальным затуханием (не режет слух).
function tone(freq, dur, vol, type) {
  if (!soundOn) return;
  try {
    const ctx = ac(), o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || "sine"; o.frequency.value = freq;
    const t = ctx.currentTime;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.015);           // мягкая атака
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + dur + 0.02);
  } catch (e) {}
}
// Щелчок остановки барабана — короткий затухающий шум через полосовой фильтр.
function clickSound() {
  if (!soundOn) return;
  try {
    const ctx = ac(), len = Math.floor(ctx.sampleRate * 0.045);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 1100; bp.Q.value = 0.8;
    const g = ctx.createGain(); g.gain.value = 0.22;
    src.connect(bp); bp.connect(g); g.connect(ctx.destination); src.start();
  } catch (e) {}
}
// Звук выигрыша — «монетный» звон: яркий динь + мягкий аккорд снизу.
const winSound = () => {
  tone(1046.5, 0.12, 0.05, "sine");
  setTimeout(() => tone(1568, 0.55, 0.05, "sine"), 90);
  [523.25, 659.25, 783.99].forEach(f => setTimeout(() => tone(f, 0.6, 0.028, "triangle"), 130));
};
// Крупный выигрыш — восходящее арпеджио + звон.
const bigWinSound = () => {
  [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => setTimeout(() => tone(f, 0.3, 0.05, "triangle"), i * 85));
  setTimeout(winSound, 120);
};
// Мега-выигрыш — фанфара.
const megaWinSound = () => {
  [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((f, i) => setTimeout(() => tone(f, 0.34, 0.055, "triangle"), i * 80));
  [783.99, 987.77, 1318.5].forEach((f, i) => setTimeout(() => tone(f, 0.6, 0.04, "sine"), 420 + i * 70));
};
// Нарастающий «саспенс» на предвкушении выигрыша — пила с плавным подъёмом частоты.
function anticipationSound(durSec) {
  if (!soundOn || durSec <= 0) return;
  try {
    const ctx = ac(), o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "sawtooth";
    const t = ctx.currentTime;
    o.frequency.setValueAtTime(160, t);
    o.frequency.exponentialRampToValueAtTime(880, t + durSec);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.032, t + 0.12);
    g.gain.setValueAtTime(0.032, Math.max(t + 0.13, t + durSec - 0.15));
    g.gain.exponentialRampToValueAtTime(0.0001, t + durSec);
    o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + durSec + 0.03);
  } catch (e) {}
}
// Шумовой буфер для «клаца» создаём ОДИН раз и переиспользуем (без нагрузки на каждый тик).
let _ratchetBuf = null;
function ratchetBuffer(ctx) {
  if (_ratchetBuf) return _ratchetBuf;
  const len = Math.floor(ctx.sampleRate * 0.022);
  const buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 5);
  _ratchetBuf = buf; return buf;
}
function ratchetTick() {
  if (!soundOn) return;
  try {
    const ctx = ac();
    const src = ctx.createBufferSource(); src.buffer = ratchetBuffer(ctx);
    const bp = ctx.createBiquadFilter(); bp.type = "bandpass";
    bp.frequency.value = 500 + Math.random() * 200; bp.Q.value = 2.4;     // низкий металлический «клац»
    const g = ctx.createGain(); g.gain.value = 0.3;
    src.connect(bp); bp.connect(g); g.connect(ctx.destination); src.start();
  } catch (e) {}
}
// Трещотка: тики ПЛАНИРУЕМ заранее по кривой замедления (ease-out) — без чтения DOM в кадре.
let _ratchetTimers = [];
function scheduleRatchet(ticks, durSec) {
  _ratchetTimers.forEach(clearTimeout); _ratchetTimers = [];
  if (!soundOn || ticks < 1) return;
  for (let k = 1; k <= ticks; k++) {
    const tsec = durSec * (1 - Math.pow(1 - k / ticks, 1 / 3));   // инверсия ease-out: часто → редко
    _ratchetTimers.push(setTimeout(ratchetTick, tsec * 1000));
  }
}
function slotBtnText() {
  const can = (bonus.coins || 0) >= slotBet;
  return can ? `Крутить · ${slotBet} 🪙` : "Недостаточно монет";
}
function initStrip(el) {   // стартовое состояние барабана — 3 случайных символа
  const em = slotEmojis();
  const cells = [0, 1, 2].map(() => em[Math.floor(Math.random() * em.length)] || "❔");
  el.classList.remove("blur");
  el.style.transition = "none"; el.style.transform = "translateY(0)";
  el.innerHTML = cells.map(e => `<div class="reel-cell">${e}</div>`).join("");
}
// Единый плавный прокрут барабана: ОДИН transition с мягким ease-in-out —
// плавный разгон и плавное затихание, без переключений анимаций (не «багает»).
// Крутится СВЕРХУ ВНИЗ: результат [верх,середина,низ] в начале ленты, филлер снизу.
const REEL_EASE = "cubic-bezier(.28,0,.14,1)";   // быстрый разгон, долгое мягкое торможение
function runReel(el, three, K, dur) {
  const em = slotEmojis();
  const cells = [three[0], three[1], three[2]];
  for (let i = 0; i < K; i++) cells.push(em[Math.floor(Math.random() * em.length)] || "❔");
  const cellH = el.parentElement.clientHeight / 3;
  el.innerHTML = cells.map(e => `<div class="reel-cell">${e}</div>`).join("");
  el.style.transition = "none";
  el.style.transform = `translateY(${-K * cellH}px)`;      // старт: виден нижний филлер
  el.classList.add("blur");
  void el.offsetHeight;                                     // зафиксировать старт до анимации
  requestAnimationFrame(() => {
    el.style.transition = `transform ${dur}s ${REEL_EASE}`;
    el.style.transform = "translateY(0)";                  // плавно съезжает к результату (сверху)
  });
  setTimeout(() => el.classList.remove("blur"), Math.max(0, dur * 1000 - 280));  // резкость к остановке
}
// Пейтабл показывает реальный выигрыш в монетах для ТЕКУЩЕЙ ставки.
function slotPayHtml() {
  return (slot.symbols || []).map(s =>
    `<div class="slotpay"><span>${s.emoji}</span><small>${slotBet * s.mult} 🪙</small></div>`).join("");
}
// Обновление сумм НА МЕСТЕ (без перестройки разметки — не «прыгает»).
function updateSlotPays() {
  const smalls = document.querySelectorAll("#slotPays .slotpay small");
  (slot.symbols || []).forEach((s, i) => { if (smalls[i]) smalls[i].textContent = `${slotBet * s.mult} 🪙`; });
}
function renderSlot() {
  if (!(slot.bets || []).includes(slotBet)) slotBet = (slot.bets || [2])[0];   // защита от stale-значения
  const canSpin = (bonus.coins || 0) >= slotBet;
  const pay = slotPayHtml();
  const bets = (slot.bets || []).map(b => `<button class="betbtn ${b === slotBet ? 'on' : ''}" data-bet="${b}">${b}</button>`).join("");
  $("gameWrap").innerHTML = `
    <div class="slot-top"><div class="wheel-head" id="slotHead" style="margin:0">🪙 ${bonus.coins || 0} монет</div>
      <button class="soundtgl" id="slotInfo" aria-label="Правила">ℹ️</button>
      <button class="soundtgl" id="soundTgl" aria-label="Звук">${soundOn ? "🔊" : "🔇"}</button></div>
    <div class="slot3">
      <div class="reel-col"><div class="reel-strip" id="strip0"></div></div>
      <div class="reel-col"><div class="reel-strip" id="strip1"></div></div>
      <div class="reel-col"><div class="reel-strip" id="strip2"></div></div>
    </div>
    <div class="game-result" id="slotResult">3 одинаковых в линию — ряд, диагональ или зигзаг</div>
    <div class="wheel-hist" id="slotHist"></div>
    <div class="betsel"><span class="betlbl">Ставка</span>${bets}</div>
    <button class="bigbtn" id="slotBtn" ${canSpin ? "" : "disabled"}>${slotBtnText()}</button>
    <button class="autobtn" id="slotAutoBtn">🔄 Авто-прокрут</button>
    <div class="wheel-hint">Возможный выигрыш за 3 в линию:</div>
    <div class="slotpays" id="slotPays">${pay}</div>`;
  slotAuto = false;                       // при каждом заходе на слот — авто выключено
  [0, 1, 2].forEach(c => initStrip($("strip" + c)));
  renderSlotHist(); updateAutoBtn();
  $("slotAutoBtn").onclick = toggleAuto;
  document.querySelectorAll(".betsel .betbtn").forEach(btn => btn.onclick = () => {
    if (slotSpinning) return;
    slotBet = parseInt(btn.dataset.bet, 10);
    localStorage.setItem("slotBet", slotBet);
    document.querySelectorAll(".betsel .betbtn").forEach(b => b.classList.toggle("on", parseInt(b.dataset.bet, 10) === slotBet));
    const sb = $("slotBtn"); if (sb) { sb.disabled = (bonus.coins || 0) < slotBet; sb.textContent = slotBtnText(); }
    updateSlotPays();          // суммы пересчитываются на месте (без рефлоу)
    haptic("impact", "light");
  });
  $("slotBtn").onclick = spinSlot;
  $("soundTgl").onclick = () => { toggleSound(); $("soundTgl").textContent = soundOn ? "🔊" : "🔇"; };
  $("slotInfo").onclick = () => {
    const pay = (slot.symbols || []).map(s => `<div class="statrow"><span>${s.emoji} ${esc(s.label)} × 3</span><b>${slotBet * s.mult} 🪙</b></div>`).join("");
    const board = (cells) => {                          // cells: [[r,c],...] → плоские индексы 0..8
      const on = cells.map(([r, c]) => r * 3 + c);
      return `<div class="miniboard">${[0,1,2,3,4,5,6,7,8].map(i => `<i class="${on.includes(i) ? "on" : ""}"></i>`).join("")}</div>`;
    };
    const lines = (slot.lines || []).map(l => `<div class="lineitem">${board(l.cells)}<small>${esc(l.name)}</small></div>`).join("");
    const net = slotStats.won - slotStats.wagered;
    const stats = slotStats.spins ? `
      <div style="font-weight:800;margin:14px 0 4px">Ваша статистика</div>
      <div class="statrow"><span>Прокрутов</span><b>${slotStats.spins}</b></div>
      <div class="statrow"><span>Выиграно</span><b>${slotStats.won} 🪙</b></div>
      <div class="statrow"><span>Лучший выигрыш</span><b>${slotStats.best} 🪙</b></div>
      <div class="statrow"><span>Баланс игры</span><b style="color:${net >= 0 ? 'var(--brand-neon)' : 'var(--hint)'}">${net >= 0 ? '+' : ''}${net} 🪙</b></div>` : "";
    showInfo("🎰 Облако Монет", `
      <p>Выберите <b>ставку</b> (${(slot.bets || []).join(" / ")} 🪙) и соберите <b>3 одинаковых</b> символа по одной из <b>${(slot.lines || []).length} линий</b>. Приз = <b>ставка × множитель</b>.</p>
      <div class="slotlines">${lines}</div>
      <div style="font-weight:800;margin:14px 0 4px">Выигрыш за 3 в линию (ставка ${slotBet} 🪙):</div>${pay}
      <p style="color:var(--hint);margin:12px 0">Шанс выигрыша за прокрут ≈ <b>30%</b>. Чем реже символ — тем крупнее множитель. Монеты тратятся скидкой в корзине (100 монет = 1 Br).</p>${stats}`);
  };
}
function renderSlotHist() {
  const el = $("slotHist"); if (!el) return;
  if (!slotHistory.length) { el.innerHTML = ""; return; }
  let slots = "";                                        // всегда 5 слотов фикс. ширины — ряд не «прыгает»
  for (let i = 0; i < 5; i++) {
    if (i < slotHistory.length) {
      const v = slotHistory[i];
      slots += v > 0 ? `<span class="hchip">+${v}</span>` : `<span class="hchip miss">—</span>`;
    } else slots += `<span class="hchip ph">+0</span>`;
  }
  el.innerHTML = `<span class="hlabel">Последние:</span>` + slots;
}
// Уровень выигрыша по множителю (coins/bet): small <5, big 5–14, mega ≥15.
function slotWinFx(coins, bet, stage) {
  const mult = bet ? coins / bet : 0;
  const tier = mult >= 15 ? "mega" : (mult >= 5 ? "big" : "small");
  if (tier === "small") {
    const pop = document.createElement("div");
    pop.className = "winpop"; pop.textContent = `+${coins} 🪙`;
    stage.appendChild(pop); setTimeout(() => pop.remove(), 1500);
    winSound(); haptic("notify", "success");
    return;
  }
  const b = document.createElement("div");
  b.className = "winbanner " + tier;
  b.innerHTML = `<div class="wt">${tier === "mega" ? "MEGA WIN" : "BIG WIN"}</div><div class="wc">+${coins} 🪙</div>`;
  stage.appendChild(b); setTimeout(() => b.remove(), 2200);
  confetti(stage, tier === "mega" ? 60 : 34);
  if (tier === "mega") {
    stage.classList.add("mega-flash"); setTimeout(() => stage.classList.remove("mega-flash"), 700);
    megaWinSound(); haptic("notify", "success"); setTimeout(() => haptic("impact", "heavy"), 260);
  } else {
    bigWinSound(); haptic("notify", "success");
  }
}
function updateAutoBtn() {
  const b = $("slotAutoBtn"); if (!b) return;
  b.textContent = slotAuto ? "⏹ Остановить авто" : "🔄 Авто-прокрут";
  b.classList.toggle("on", slotAuto);
}
function toggleAuto() {
  slotAuto = !slotAuto;
  updateAutoBtn();
  if (slotAuto && !slotSpinning && (bonus.coins || 0) >= slotBet) spinSlot();
  else if (slotAuto) { slotAuto = false; updateAutoBtn(); }   // нет монет — не запускаем
}
// Звук — общий тумблер для слота и колеса.
function toggleSound() {
  soundOn = !soundOn; localStorage.setItem("slotSound", soundOn ? "1" : "0");
  if (soundOn) tone(660, 0.15, 0.05);
}
// Шторка с правилами.
function showInfo(title, html) {
  $("infoTitle").textContent = title; $("infoBody").innerHTML = html;
  $("infoClose").textContent = "Понятно";   // подпись по умолчанию; форма меняет её на «Отмена»
  $("infoOverlay").classList.add("show");
}
$("infoClose").onclick = () => closeOverlay($("infoOverlay"));
async function spinSlot() {
  if (slotSpinning || (bonus.coins || 0) < slotBet) return;
  slotSpinning = true; $("slotBtn").disabled = true;
  const stage = document.querySelector(".slot3"); if (stage) stage.classList.remove("win");
  [0, 1, 2].forEach(c => { const s = $("strip" + c); if (s) [...s.children].forEach(ch => ch.classList.remove("hit")); });
  haptic("impact", "medium");
  // Крутим СРАЗУ, не дожидаясь сети: колесо (spinWheel) уже так делает, а слот
  // раньше стоял неподвижным барабаном, пока ответ шёл до Render и обратно —
  // на живой сети это заметная пауза, и тап ощущался как «не сработал».
  [0, 1, 2].forEach(c => { const s = $("strip" + c); if (s) s.classList.add("blur", "spinning"); });
  // 1) Параллельно с этим — результат (быстрый запрос).
  let res;
  try {
    const r = await fetch("/api/slot/spin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData, bet: slotBet }) });
    res = await r.json();
  } catch (e) {
    slotSpinning = false; $("slotBtn").disabled = false;
    [0, 1, 2].forEach(c => { const s = $("strip" + c); if (s) s.classList.remove("spinning"); });
    alertMsg(текстСбоя(e)); return;
  }
  // Плейсхолдер-прокрут снят — дальше барабаны ведёт настоящий результат.
  [0, 1, 2].forEach(c => { const s = $("strip" + c); if (s) s.classList.remove("spinning"); });
  if (!res.ok) { slotSpinning = false; $("slotBtn").disabled = false; alertMsg(res.error === "no_coins" ? "Недостаточно монет." : "Ошибка."); return; }
  bonus.coins = res.balance;
  const isWin = !!res.win;
  const K = 22;
  const cols = [0, 1, 2].map(c => [res.grid[0][c], res.grid[1][c], res.grid[2][c]]);
  const base = isWin ? [1.6, 2.05, 2.95] : [1.6, 2.05, 2.45];   // 3-й тормозит дольше — предвкушение
  const durs = slotAuto ? base.map(d => d * 0.55) : base;       // авто-прокрут — быстрее
  const maxEnd = durs[2] * 1000;
  // 2) Трещотка на всё время самого длинного барабана (замедляется к концу).
  scheduleRatchet(Math.round(durs[2] * 16), durs[2]);
  // 3) Барабаны стартуют одновременно, останавливаются по очереди (разные dur), каждый — плавно.
  [0, 1, 2].forEach(c => runReel($("strip" + c), cols[c], K, durs[c]));
  [0, 1, 2].forEach(c => setTimeout(() => { haptic("impact", "light"); clickSound(); }, durs[c] * 1000));
  // 4) Предвкушение на 3-м барабане при выигрыше: свечение колонки + нарастающий звук + пульс.
  if (isWin) {
    const col2 = document.querySelectorAll(".reel-col")[2];
    const from = durs[1] * 1000, to = durs[2] * 1000;
    setTimeout(() => {
      if (col2) col2.classList.add("antic");
      anticipationSound((to - from) / 1000);
      _anticHb = setInterval(() => haptic("impact", "medium"), 240);
    }, from);
    setTimeout(() => {
      if (col2) col2.classList.remove("antic");
      if (_anticHb) { clearInterval(_anticHb); _anticHb = null; }
    }, to);
  }
  setTimeout(() => {
    slotSpinning = false;
    if ($("slotHead")) $("slotHead").textContent = `🪙 ${bonus.coins} монет`;
    const btn = $("slotBtn");
    if (btn) { btn.disabled = (bonus.coins || 0) < slotBet; btn.textContent = slotBtnText(); }
    if (isWin) {
      (res.win_cells || []).forEach(([r, c]) => {              // подсветить выигрышные клетки (результат сверху)
        const cell = $("strip" + c) && $("strip" + c).children[r];
        if (cell) cell.classList.add("hit");
      });
      if (stage) { stage.classList.add("win"); slotWinFx(res.coins, res.bet || slotBet, stage); }
    }
    const rl = $("slotResult");
    if (rl) rl.innerHTML = isWin ? `<span class="winbadge">+${res.coins} 🪙</span>` : "Не повезло — крути ещё";
    slotHistory.unshift(isWin ? res.coins : 0);
    if (slotHistory.length > 5) slotHistory.pop();
    renderSlotHist();
    // Моя статистика (локально).
    slotStats.spins++; slotStats.wagered += (res.bet || slotBet);
    if (isWin) { slotStats.won += res.coins; slotStats.best = Math.max(slotStats.best, res.coins); }
    saveSlotStats();
    // Авто-прокрут: продолжаем, если ещё включён, хватает монет и слот на экране.
    if (slotAuto && document.querySelector(".slot3") && (bonus.coins || 0) >= slotBet) {
      setTimeout(() => { if (slotAuto) spinSlot(); }, isWin ? 900 : 350);
    } else if (slotAuto) { slotAuto = false; updateAutoBtn(); }
  }, maxEnd + 120);
}

// ----- Розыгрыши -----
let raffle = null, raffleDone = null, raffleReady = false;
const maskId = (id) => "•••" + ("" + id).slice(-3);
function raffleTimeLeft(ends) {
  if (!ends) return "—";
  const ms = new Date(ends.replace(" ", "T")) - new Date();
  if (ms <= 0) return "скоро итоги";
  const d = Math.floor(ms / 86400000), h = Math.floor((ms % 86400000) / 3600000);
  return d > 0 ? `${d} д ${h} ч` : `${h} ч`;
}
async function fetchRaffle() {
  try {
    const r = await fetch("/api/raffle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    raffle = d.ok ? d.raffle : null;
    raffleDone = d.ok ? (d.finished || null) : null;   // итоги только что закончившегося
    raffleReady = true;
  } catch (e) { raffle = null; }
}
// Архив прошлых розыгрышей: без него виден только последний завершённый —
// как только стартует следующий, итоги предыдущего пропадают безвозвратно.
async function openRaffleHistory() {
  showInfo("🏆 История розыгрышей", `<p style="color:var(--hint)">Загрузка…</p>`);
  try {
    const r = await fetch("/api/raffle/history", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (!d.ok) { $("infoBody").innerHTML = `<p style="color:var(--hint)">Не удалось загрузить.</p>`; return; }
    const list = d.history || [];
    if (!list.length) { $("infoBody").innerHTML = `<p style="color:var(--hint)">Пока не было ни одного розыгрыша.</p>`; return; }
    $("infoBody").innerHTML = list.map(rf => {
      const wins = (rf.winners || []).length
        ? rf.winners.map(w => `<div class="statrow" style="font-size:14px">
            <span>${["", "🥇", "🥈", "🥉"][w.place] || (w.place + " место")} ${esc(w.who)}</span><b>${esc(w.prize)}</b></div>`).join("")
        : `<p style="color:var(--hint);font-size:13px;margin:4px 0">Участников не набралось.</p>`;
      return `<div class="card-block" style="text-align:left;margin-bottom:10px">
        <div style="font-weight:800;margin-bottom:2px">${esc(rf.title)}</div>
        <div style="color:var(--hint);font-size:12px;margin-bottom:8px">${whenRu(rf.finished_at)} · участников: ${rf.participants_count}</div>
        ${wins}</div>`;
    }).join("");
  } catch (e) { $("infoBody").innerHTML = `<p style="color:var(--hint)">${текстСбоя(e)}</p>`; }
}
function renderRaffle() {
  if (!raffle) {
    if (!$("raffleWrap")) return;
    // Розыгрыш кончился — показываем, чем именно. Победителям бот написал
    // лично, остальные участники иначе не узнают ничего.
    if (raffleDone) {
      const d = raffleDone;
      // Своя картинка у места, а не общая на весь розыгрыш: победитель видит
      // ровно то, что выиграл.
      const wins = (d.winners || []).length
        ? d.winners.map(w => `<div class="statrow"><span style="display:flex;align-items:center;gap:8px">
            ${w.photo ? `<img src="/api/photo?file_id=${encodeURIComponent(w.photo)}" alt=""
                              style="width:32px;height:32px;border-radius:8px;object-fit:cover;flex:none">` : ""}
            ${["", "🥇", "🥈", "🥉"][w.place] || (w.place + " место")} ${esc(w.who)}</span><b>${esc(w.prize)}</b></div>`).join("")
        : `<p style="color:var(--hint);font-size:14px;margin:0">Участников не набралось.</p>`;
      // Участники — наравне с победителями: розыгрыш, где видно только
      // троих счастливчиков, выглядит как розыгрыш без свидетелей.
      const люди = (d.participants || []).length
        ? `<div class="card-block" style="text-align:left">
             <div style="font-weight:800;margin-bottom:6px">👥 Участвовали · ${d.participants_count}</div>
             <div class="fltchips">${d.participants.map(x => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>`
        : "";
      $("raffleWrap").innerHTML = `<div class="card-block" style="text-align:left">
        <div style="font-weight:800;font-size:17px;margin-bottom:4px">🏁 ${esc(d.title)} — итоги</div>
        <div style="color:var(--hint);font-size:13px;margin-bottom:12px">Розыгрыш завершён ${whenRu(d.finished_at)}</div>
        ${wins}
        <p style="color:var(--hint);font-size:13px;margin:12px 0 0">Следующий розыгрыш объявим здесь же.</p></div>${люди}`;
    } else {
      $("raffleWrap").innerHTML = `<p style="color:var(--hint)">Сейчас розыгрыш не идёт.</p>`;
    }
    return;
  }
  const r = raffle;
  // Своя картинка у каждого места: 1-2 обычно вещь, 3-е чаще монеты, но и оно
  // бывает вещью — общая картинка на весь розыгрыш подписывала бы любое место
  // одной и той же вещью.
  const prizeRows = [
    [1, "🥇 1 место", r.prize1 || "—", r.photo1], [2, "🥈 2 место", r.prize2 || "—", r.photo2],
    [3, "🥉 3 место", `${r.prize3_coins} монет`, r.photo3],
  ].map(([place, метка, приз, фото]) => `<div class="statrow"><span style="display:flex;align-items:center;gap:8px">
      ${фото ? `<img src="/api/photo?file_id=${encodeURIComponent(фото)}" alt=""
                     style="width:32px;height:32px;border-radius:8px;object-fit:cover;flex:none">` : ""}
      ${метка}</span><b>${esc(приз)}</b></div>`).join("");
  let cta;
  if (r.entered) cta = `<div class="rbanner" style="border-color:#2e9e4f;color:#2e9e4f">✅ Вы участвуете! Ждём итогов.</div>`;
  else if (r.eligible) cta = `<button class="bigbtn" id="raffleJoin">Участвовать</button>`;
  else {
    const pct = r.threshold ? Math.min(100, Math.round(r.spent / r.threshold * 100)) : 0;
    cta = `<div class="wheel-bar"><i style="width:${pct}%"></i></div>
      <div style="color:var(--hint);font-size:13px;margin-top:8px;text-align:center">Осталось потратить <b>${r.remaining.toFixed(2)} Br</b> до участия<br>(${r.spent.toFixed(2)} / ${r.threshold.toFixed(2)} Br за месяц)</div>`;
  }
  const wins = (r.last_winners || []).length
    ? r.last_winners.map(w => `<div class="statrow"><span>${["", "🥇", "🥈", "🥉"][w.place] || (w.place + " место")} ${maskId(w.user_id)}</span><b>${esc(w.prize)}</b></div>`).join("")
    : "";
  $("raffleWrap").innerHTML = `
    <div class="card-block" style="text-align:left">
      <div style="font-weight:800;font-size:17px;margin-bottom:4px">🏆 ${esc(r.title)}</div>
      <div style="color:var(--hint);font-size:13px;margin-bottom:12px">Участников: <b>${r.participants}</b> · до конца: <b>${raffleTimeLeft(r.ends_at)}</b></div>
      ${prizeRows}
      <div style="margin-top:14px">${cta}</div>
    </div>
    <div class="card-block" style="text-align:left">
      <div style="font-weight:800;margin-bottom:6px">🎯 Как участвовать</div>
      <p style="color:var(--hint);font-size:14px;margin:0">Совершайте покупки на сумму от <b>${r.threshold.toFixed(2)} Br</b> за месяц — и жмите «Участвовать». Победителей бот выбирает автоматически раз в месяц.</p>
    </div>
    ${wins ? `<div class="card-block" style="text-align:left"><div style="font-weight:800;margin-bottom:6px">🏅 Прошлый розыгрыш</div>${wins}</div>` : ""}`;
  if ($("raffleJoin")) $("raffleJoin").onclick = joinRaffle;
}
async function joinRaffle() {
  try {
    const r = await fetch("/api/raffle/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    const d = await r.json();
    if (d.ok) { alertMsg("Поздравляем! Вы участвуете в розыгрыше 🎉"); await fetchRaffle(); renderRaffle(); }
    else alertMsg(d.error === "not_eligible" ? "Ещё не набрана сумма для участия." : "Не удалось.");
  } catch (e) { alertMsg(текстСбоя(e)); }
}

function referralHtml() {
  const share = bonus.ref_link
    ? `<button class="bigbtn" id="refShare">Пригласить друга</button>
       <button class="closebtn" id="refCopy">Скопировать ссылку</button>`
    : `<p style="color:var(--hint)">Ссылка появится чуть позже.</p>`;
  // Проценты — с сервера (bonus.ref_tiers), а не свои числа в JS: сервер уже
  // отдаёт их и для экрана правил, здесь раньше был свой дубль [2,3,4,5].
  const tierBar = (bonus.ref_tiers || []).map(t => t.percent)
    .map(t => `<div class="rtier ${(bonus.ref_percent || 2) >= t ? 'on' : ''}">${t}%</div>`).join("");
  const nextLine = bonus.next_need
    ? `Ещё <b>${bonus.next_need}</b> ${plural(bonus.next_need, "активный реферал", "активных реферала", "активных рефералов")} — и вы будете получать <b>${bonus.next_percent}%</b>`
    : "Максимальный процент достигнут 🎉";
  const list = (bonus.referrals_list || []).length
    ? bonus.referrals_list.map((r, i) => `<div class="statrow"><span>Реферал ${i + 1}</span><b style="color:${r.active ? '#2e9e4f' : 'var(--hint)'}">${r.active ? "активен" : "ждём заказ"}</b></div>`).join("")
    : `<div class="statrow"><span style="color:var(--hint)">Пока нет рефералов — поделись ссылкой выше</span></div>`;
  return `<div class="bonushero"><div class="bonuscoin">🪙 ${bonus.coins || 0}</div><div class="bonuslab">ваши VAPECOINS</div></div>
    <div class="card-block" style="text-align:left">
      <div style="font-weight:800;margin-bottom:8px">👥 Пригласи друга — получи монеты</div>
      ${bonus.ref_link ? `<div class="reflink">${esc(bonus.ref_link)}</div>` : ""}
      ${share}
    </div>
    <div class="card-block" style="text-align:left">
      <div style="font-weight:800;margin-bottom:12px">✨ Как это работает</div>
      <div class="howto">
        <div class="howstep"><div class="howic">🔗</div><b>Поделись</b><small>отправь ссылку другу</small></div>
        <div class="howarr">›</div>
        <div class="howstep"><div class="howic">📦</div><b>Друг заказывает</b><small>после 1-го заказа он активен</small></div>
        <div class="howarr">›</div>
        <div class="howstep"><div class="howic">🪙</div><b>Получи монеты</b><small>${bonus.referral_bonus} сразу + % с заказов</small></div>
      </div>
    </div>
    <div class="card-block" style="text-align:left">
      <div class="statgrid" style="grid-template-columns:1fr 1fr 1fr;margin-bottom:14px">
        <div style="text-align:center"><div class="statnum" style="color:var(--text)">${bonus.referrals || 0}</div><div class="statlab">Всего</div></div>
        <div style="text-align:center"><div class="statnum" style="color:#2e9e4f">${bonus.active_referrals || 0}</div><div class="statlab">Активных</div></div>
        <div style="text-align:center"><div class="statnum" style="color:var(--warn)">${bonus.ref_earned || 0}</div><div class="statlab">Заработано 🪙</div></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="color:var(--hint);font-size:14px">Ваш процент от заказов</span>
        <span class="rpct">${bonus.ref_percent || 2}%</span>
      </div>
      <div class="rtiers">${tierBar}</div>
      <div style="color:var(--hint);font-size:13px;margin-top:10px">${nextLine}</div>
      <div class="rbanner">🎁 +${bonus.referral_bonus} монет за первый заказ каждого реферала</div>
    </div>
    <div class="card-block" style="text-align:left">
      <div style="font-weight:800;margin-bottom:6px">👥 Мои рефералы</div>${list}
    </div>`;
}
function bindReferral() {
  if ($("refShare")) $("refShare").onclick = () => {
    const text = "Загляни в наш вейп-магазин 👇";
    const url = `https://t.me/share/url?url=${encodeURIComponent(bonus.ref_link)}&text=${encodeURIComponent(text)}`;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url); else window.open(url, "_blank");
  };
  if ($("refCopy")) $("refCopy").onclick = () => {
    navigator.clipboard && navigator.clipboard.writeText(bonus.ref_link);
    alertMsg("Ссылка скопирована ✅");
  };
}

// ----- Колесо фортуны -----
// Лёгкое авто-вращение в покое (медленно, только когда колесо на экране и не крутится).
let idleStarted = false;
function ensureIdleLoop() {
  if (idleStarted) return; idleStarted = true;
  let prev = performance.now();
  const step = (t) => {
    const dt = Math.min(50, t - prev); prev = t;
    const we = $("wheelEl");
    if (we && we.offsetParent && !spinning && bonusTab === "wheel" && gameMode === "wheel" && !document.hidden) {
      wheelDeg += dt * 0.006;                            // ~60 сек на оборот
      we.style.transform = `rotate(${wheelDeg}deg)`;
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
let wheelHistory = [];   // последние выигрыши (сессия)
function renderWheelHist() {
  const el = $("wheelHist"); if (!el) return;
  if (!wheelHistory.length) { el.innerHTML = ""; return; }
  // Всегда 5 слотов фикс. ширины — ряд не «прыгает» при новом выигрыше.
  let slots = "";
  for (let i = 0; i < 5; i++) {
    slots += i < wheelHistory.length
      ? `<span class="hchip">+${wheelHistory[i]}</span>`
      : `<span class="hchip ph">+0</span>`;
  }
  el.innerHTML = `<span class="hlabel">Последние:</span>` + slots;
}
// Кирпичная стена (как на сплеше) — база каждого сектора.
const WHEEL_BRICK = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60'%3E%3Cpath d='M0 30H120M0 60H120M0 0V30M60 0V30M120 0V30M30 30V60M90 30V60' stroke='%23081419' stroke-width='2' fill='none'/%3E%3Cpath d='M0 2H120M0 32H120' stroke='%23163039' stroke-width='1' fill='none'/%3E%3C/svg%3E")`;
let wheelDeg = 0, spinning = false;
// Границы секторов — поровну, не по весу. Пробовали рисовать по весу (шансы
// разнятся в 26 раз), но узкие дольки для редких призов слипались в кашу из
// цифр — некрасиво важнее математической точности здесь. Честность про
// шансы — текстом в правилах (см. wheelInfo ниже), а не искажением колеса.
function wheelBounds(sectors) {
  const seg = 360 / (sectors.length || 1);
  return sectors.map((_, i) => ({ start: i * seg, end: (i + 1) * seg }));
}
function sectorIndexAt(angle, bounds) {
  const a = ((angle % 360) + 360) % 360;
  for (let i = 0; i < bounds.length; i++) if (a >= bounds[i].start && a < bounds[i].end) return i;
  return bounds.length - 1;
}
function renderWheel() {
  const sectors = wheel.sectors || [];
  const bounds = wheelBounds(sectors);
  // Сектора: кирпич + лёгкая неоновая подсветка (SUPER — розовый), у начала
  // каждого сектора — неоновая черта-разделитель (сама и есть «луч»).
  // Цвета — переменными, а не значениями: колесо собирается один раз, но при
  // смене темы перекрашивается само, без перерисовки.
  const tint = sectors.map((s, i) => {
    const c = s.label === "SUPER" ? "var(--wheel-super)" : (i % 2 ? "var(--wheel-sec-b)" : "var(--wheel-sec-a)");
    const { start, end } = bounds[i];
    const rayEnd = Math.min(start + 1.1, end);
    return `var(--wheel-ray) ${start}deg ${rayEnd}deg, ${c} ${rayEnd}deg ${end}deg`;
  }).join(",");
  const wheelBgImage = `conic-gradient(${tint}), var(--wheel-brick)`;   // ставим из JS (кавычки в url ломают inline style)
  // Огоньки на границах секторов (крутятся вместе с колесом), в самом центре черты.
  const cornerLamps = sectors.map((_, i) =>
    `<div class="clamp" style="transform:rotate(${bounds[i].start + 0.55}deg)"><i></i></div>`).join("");
  const labels = sectors.map((s, i) => {
    const a = (bounds[i].start + bounds[i].end) / 2;
    const inner = s.label === "SUPER" ? `<b class="lstar">★</b>` : `${esc(s.label)}<b class="lcoin">🪙</b>`;
    return `<div class="wlabel ${s.label === 'SUPER' ? 'lsuper' : ''}" style="transform:rotate(${a}deg)"><span>${inner}</span></div>`;
  }).join("");
  const canSpin = wheel.spins > 0;
  const pct = Math.min(100, Math.round((wheel.progress / wheel.step) * 100));
  $("wheelWrap").innerHTML = `
    <div class="slot-top"><div class="wheel-head" id="wheelHead" style="margin:0">🪙 ${bonus.coins || 0} монет</div>
      <button class="soundtgl" id="wheelInfo" aria-label="Правила">ℹ️</button>
      <button class="soundtgl" id="wheelSound">${soundOn ? "🔊" : "🔇"}</button></div>
    <div class="wheel-stage">
      <div class="wheel-ptr"></div>
      <div class="wheel ${canSpin ? 'ready' : ''}" id="wheelEl">${labels}${cornerLamps}</div>
      <div class="wheel-hub ${canSpin ? 'ready' : ''}" id="wheelHub"><svg class="hub-logo" viewBox="-2 2 28 20" fill="none"><path d="M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg></div>
    </div>
    <div class="game-result" id="wheelResult"></div>
    <div class="wheel-hist" id="wheelHist"></div>
    <!-- Прогресс на карточке: само колесо висит прямо на стене и это уместно,
         а мелкий текст поверх кладки читать труднее, чем на белом. -->
    <div class="gamecard">
      <div class="wheel-prog-top"><span>Прогресс до прокрута</span><span>${wheel.progress}/${wheel.step} ${CUR}</span></div>
      <div class="wheel-bar"><i style="width:${pct}%"></i></div>
      <div class="wheel-hint" id="wheelHint" style="margin-bottom:0">${canSpin ? `Доступно прокрутов: ${wheel.spins}` : `Ещё ${wheel.step - wheel.progress} ${CUR} покупок до прокрута`}</div>
    </div>
    <button class="bigbtn" id="spinBtn" ${canSpin ? "" : "disabled"}>${canSpin ? "Крутить 🎡" : "Накопите покупок для прокрута"}</button>
    ${(me && me.is_super) ? `<button class="closebtn" id="grantSpin" style="color:var(--danger);margin-top:8px">🔧 +3 прокрута (тест)</button>` : ""}`;
  const we = $("wheelEl");                                 // фон задаём из JS (url с кавычками)
  we.style.backgroundImage = wheelBgImage;
  we.style.backgroundSize = "auto, auto, 88px 44px";
  we.style.transform = `rotate(${wheelDeg}deg)`;           // сохранить текущий угол при перерисовке
  renderWheelHist();
  ensureIdleLoop();
  $("spinBtn").onclick = spinWheel;
  if ($("wheelHub")) $("wheelHub").onclick = spinWheel;   // тап по центру = крутить
  $("wheelSound").onclick = () => { toggleSound(); $("wheelSound").textContent = soundOn ? "🔊" : "🔇"; };
  $("wheelInfo").onclick = () => showInfo("🎡 Колесо Фортуны", `
    <p>Крутите колесо и выигрывайте монеты 🪙.</p>
    <ul style="margin:8px 0;padding-left:18px;color:var(--text)">
      <li><b>1 прокрут</b> — за каждые <b>${wheel.step} ${CUR}</b> покупок (считается после выдачи заказа).</li>
      <li>Призы: от <b>100</b> до <b>1000</b> монет, сектор <b>SUPER</b> — 2000. Сектора нарисованы поровну, а по-настоящему малые призы выпадают заметно чаще: 100 монет — самый частый исход, SUPER — самый редкий.</li>
      <li>Монеты тратятся скидкой в корзине (100 монет = 1 Br) и на слот «Облако Монет».</li>
    </ul>`);
  if ($("grantSpin")) $("grantSpin").onclick = async () => {
    try {
      const r = await fetch("/api/admin/wheel/grant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
      const d = await r.json();
      if (handledPending(d)) return;
      if (d.ok) { wheel.spins = d.result.spins; renderWheel(); }
    } catch (e) { alertMsg(текстСбоя(e)); }
  };
}
function refreshWheelInfo() {
  const canSpin = wheel.spins > 0;
  if ($("wheelHead")) $("wheelHead").textContent = `🪙 ${bonus.coins || 0} монет`;
  if ($("wheelHint")) $("wheelHint").textContent = canSpin ? `Доступно прокрутов: ${wheel.spins}` : `Ещё ${wheel.step - wheel.progress} Br покупок до прокрута`;
  const btn = $("spinBtn");
  if (btn) { btn.disabled = !canSpin || spinning; btn.textContent = canSpin ? "Крутить 🎡" : "Накопите товаров для прокрута"; }
  if ($("wheelHub")) $("wheelHub").classList.toggle("ready", canSpin && !spinning);
  if ($("wheelEl")) $("wheelEl").classList.toggle("ready", canSpin && !spinning);
}
// Конфетти-салют на крупный выигрыш.
function confetti(container, n) {
  const colors = ["#35e0e0", "#ffd23f", "#ff3d81", "#5ff2f2", "#8fd694", "#ffffff"];
  for (let i = 0; i < (n || 26); i++) {
    const p = document.createElement("div");
    p.className = "confetti"; p.style.background = colors[i % colors.length];
    p.style.setProperty("--dx", ((Math.random() * 2 - 1) * 170).toFixed(0) + "px");
    p.style.setProperty("--dy", (110 + Math.random() * 170).toFixed(0) + "px");
    p.style.setProperty("--rot", (Math.random() * 720 - 360).toFixed(0) + "deg");
    p.style.animation = `confFall ${(1 + Math.random() * 0.7).toFixed(2)}s ease-out forwards`;
    container.appendChild(p); setTimeout(() => p.remove(), 1900);
  }
}
async function spinWheel() {
  if (spinning || wheel.spins <= 0) return;
  spinning = true; $("spinBtn").disabled = true;
  haptic("impact", "medium");
  if ($("wheelResult")) { $("wheelResult").textContent = ""; $("wheelResult").classList.remove("win"); }
  const el = $("wheelEl");
  const ptr = document.querySelector(".wheel-ptr");
  if (ptr) ptr.classList.remove("win", "bump");
  if ($("wheelHub")) $("wheelHub").classList.remove("ready");
  el.classList.remove("won", "ready");                       // не мерцать во время прокрута
  el.querySelectorAll(".wlabel.win").forEach(l => l.classList.remove("win"));
  el.style.transition = "none";

  const n = (wheel.sectors || []).length;
  const bounds = wheelBounds(wheel.sectors || []);
  const v0 = 0.9;                                  // °/мс — постоянная скорость раскрутки
  let angle = wheelDeg, lastBucket = sectorIndexAt(angle, bounds);
  let phase = 1, running = true, prevT = performance.now();
  let decelStart = 0, decelDur = 0, startAngle = 0, dist = 0, res = null;
  // Пройден сектор → тик трещотки.
  const tickCross = () => { const b = sectorIndexAt(angle, bounds); if (b !== lastBucket) { ratchetTick(); lastBucket = b; } };
  // Флажок: резкий толчок по ходу вращения сразу после штырька, плавно к нулю.
  const flickPointer = () => {
    if (!ptr) return;
    const am = ((angle % 360) + 360) % 360;
    const b = bounds[sectorIndexAt(am, bounds)];
    const width = b.end - b.start;
    const frac = width > 0 ? (am - b.start) / width : 0;   // 0..1 — позиция внутри сектора
    const defl = -11 * Math.exp(-frac * 7);                // толчок после штырька, затухает
    ptr.style.transform = `translateX(-50%) rotate(${defl.toFixed(2)}deg)`;
  };

  function finishWheelSpin() {
    running = false; wheelDeg = angle; spinning = false;
    if (ptr) ptr.style.transform = "";                       // вернуть флажок в покой (для .bump)
    bonus.coins = res.balance; wheel.spins = res.spins;
    refreshWheelInfo();
    haptic("notify", "success"); winSound();
    if (ptr) { ptr.classList.add("win", "bump"); setTimeout(() => ptr.classList.remove("bump"), 420); }
    el.classList.add("won");                                  // неон по окружности
    const wl = el.querySelectorAll(".wlabel")[res.index]; if (wl) wl.classList.add("win");
    const rl = $("wheelResult"); if (rl) rl.innerHTML = `<span class="winbadge">+${res.coins} 🪙</span>`;
    const stage = document.querySelector(".wheel-stage");
    if (stage) {
      const pop = document.createElement("div"); pop.className = "winpop"; pop.textContent = `+${res.coins} 🪙`;
      stage.appendChild(pop); setTimeout(() => pop.remove(), 1500);
      if (res.coins >= 1000) confetti(stage);              // джекпот — салют
    }
    wheelHistory.unshift(res.coins); if (wheelHistory.length > 5) wheelHistory.pop();
    renderWheelHist();
    setTimeout(() => {                                        // неон гаснет через ~1.5 сек
      el.classList.remove("won");
      if (ptr) ptr.classList.remove("win");
      if (wl) wl.classList.remove("win");
    }, 1600);
  }

  const loop = (t) => {
    if (!running) return;
    const dt = t - prevT; prevT = t;
    if (phase === 1) {
      angle += v0 * dt;                            // равномерно, пока ждём сеть
    } else {
      const p = Math.min(1, (t - decelStart) / decelDur);
      angle = startAngle + dist * (2 * p - p * p);  // линейное торможение (старт со скорости v0 — без рывка)
      if (p >= 1) { el.style.transform = `rotate(${angle}deg)`; tickCross(); flickPointer(); return finishWheelSpin(); }
    }
    el.style.transform = `rotate(${angle}deg)`;
    tickCross(); flickPointer();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame((t) => { prevT = t; requestAnimationFrame(loop); });

  try {
    const r = await fetch("/api/wheel/spin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
    res = await r.json();
  } catch (e) { running = false; spinning = false; $("spinBtn").disabled = false; if ($("wheelResult")) $("wheelResult").textContent = "Сеть недоступна."; return; }
  if (!res.ok) {
    running = false; spinning = false;
    el.style.transition = "transform .3s ease"; el.style.transform = `rotate(${wheelDeg}deg)`;   // вернуть на место, не оставлять колесо замершим на полпути
    refreshWheelInfo();
    alertMsg(res.error === "no_spins" ? "Прокруты закончились — попробуйте обновить страницу." : "Не удалось прокрутить, попробуйте ещё раз.");
    return;
  }

  // Переход в торможение: стартуем ровно с текущей скорости v0 (без скачка).
  const curMod = ((angle % 360) + 360) % 360;
  const targetMid = (bounds[res.index].start + bounds[res.index].end) / 2;
  const desiredMod = ((360 - targetMid) % 360 + 360) % 360;
  const delta = (desiredMod - curMod + 360) % 360;
  // «Почти джекпот»: если выпал SUPER или соседний сектор — тормозим дольше и на оборот больше.
  const superIdx = (wheel.sectors || []).findIndex(s => s.label === "SUPER");
  const nearSuper = superIdx >= 0 && [0, 1, n - 1].includes((res.index - superIdx + n) % n);
  dist = 360 * 3 + delta + (nearSuper ? 360 : 0);
  startAngle = angle;
  decelDur = 2 * dist / v0 * (nearSuper ? 1.3 : 1);   // у SUPER — драматичнее
  decelStart = performance.now();
  phase = 2;
}

