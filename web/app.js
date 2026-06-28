/* GD Skill Match — frontend. Vanilla JS, no build step.
   All user-facing text goes through t() (see i18n.js). */

const $ = (s, r = document) => r.querySelector(s);
const el = (t_, c, h) => { const e = document.createElement(t_); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async p => { const r = await fetch(p); if (!r.ok) throw new Error(await r.text()); return r.json(); };
const fmt = (n, d = 0) => (n == null ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
// percentages at source precision (achievement is recorded to 2 decimals, e.g. 97.54%)
const pct = n => (n == null ? "—" : (n * 100).toFixed(2) + "%");

const YT_TERM = { drum: "GITADORA DRUMMANIA", guitar: "GITADORA GUITARFREAKS" };
const ytLink = name => `https://www.youtube.com/results?search_query=${encodeURIComponent((YT_TERM[INST] || YT_TERM.drum) + " " + name)}`;
const EXT_ICON = `<svg class="exti" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg>`;
const ytLinkEl = name => `<a class="ytlink" href="${ytLink(name)}" target="_blank" rel="noopener">Youtube ${EXT_ICON}</a>`;

// small "?" help chip with a hover/focus tooltip
function help(text, align = "") {
  return `<span class="help ${align}" tabindex="0" role="note" aria-label="?"><span class="htip">${esc(text)}</span>?</span>`;
}

/* color encodings ------------------------------------------------ */
function zColor(z) {
  if (z == null) return "rgb(74,88,120)";
  const t_ = Math.max(-2, Math.min(2, z)) / 2;
  return t_ >= 0 ? mix([74, 88, 120], [255, 176, 46], t_) : mix([74, 88, 120], [58, 110, 165], -t_);
}
const rgb = a => `rgb(${a[0]},${a[1]},${a[2]})`;
const mix = (a, b, t_) => `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t_)).join(",")})`;

// GITADORA difficulty-class colours: BASIC=blue, ADV=yellow, EXT=red, MAS=purple
const DIFF_COLOR = { BAS: "#1ca0e6", ADV: "#f2a01c", EXT: "#e62832", MAS: "#9b3fd4" };
const diffColor = d => DIFF_COLOR[d] || "#5b6478";
const LV = x => (x == null ? "—" : Number(x).toFixed(2));
const lvtag = (diff, level) => `<span class="lvtag" style="color:${diffColor(diff)}">${diff} Lv${LV(level)}</span>`;
// GuitarFreaks: tag guitar (G) / bass (B) charts so identically-named songs are
// distinguishable. DrumMania charts are single-part ("D") → no badge.
const partTag = p => (p === "G" || p === "B") ? ` <span class="parttag p-${p}">${p}</span>` : "";

// GITADORA skill-tier colours (gsv scheme): tier = floor(SP / 500)
function gdTier(sp) { return Math.max(0, Math.min(20, Math.floor((sp || 0) / 500))); }
const GD_SOLID = {0:"#FFFFFF",1:"#FFFFFF",2:"#FF6600",4:"#FFFF00",6:"#33FF00",8:"#3366FF",10:"#FF00FF",12:"#FF0000",14:"#DD8844",15:"#C0C0C0",16:"#FFD700"};
const GD_GRAD  = {3:"#FF6600",5:"#FFFF00",7:"#33FF00",9:"#3366FF",11:"#FF00FF",13:"#FF0000"};
const GD_NAME  = {0:"WHITE",1:"WHITE",2:"ORANGE",3:"ORANGE",4:"YELLOW",5:"YELLOW",6:"GREEN",7:"GREEN",8:"BLUE",9:"BLUE",10:"PURPLE",11:"PURPLE",12:"RED",13:"RED",14:"BRONZE",15:"SILVER",16:"GOLD",17:"RAINBOW",18:"RAINBOW",19:"RAINBOW",20:"RAINBOW"};
function gdSkillBg(sp) {
  const lv = gdTier(sp);
  if (lv >= 17) return "linear-gradient(115deg,#ff2222 8%,#ff22ff 24%,#2222ff 42%,#22ffff 58%,#22ff22 74%,#ffff22 90%)";
  if (GD_GRAD[lv]) return `linear-gradient(170deg, ${GD_GRAD[lv]} 30%, #ffffff)`;
  const c = GD_SOLID[lv];
  return `linear-gradient(0deg, ${c}, ${c})`;
}
const gdName = sp => GD_NAME[gdTier(sp)];
function skillText(sp, text, cls = "") {
  const tier = gdTier(sp);
  return `<span class="skillc ${cls}" style="background-image:${gdSkillBg(sp)}" title="${t("tierTitle", { tier: gdName(sp), lo: tier * 500, hi: tier * 500 + 499 })}">${text}</span>`;
}
const pnameLink = p => `<a href="${esc(p.gsvUrl)}" target="_blank" rel="noopener">${skillText(p.sp, esc(p.name))} ↗</a>`;
const pspan = sp => skillText(sp, fmt(sp, 2), "sp");

function confDots(c) {
  const on = Math.round((c || 0) * 4);
  let d = "";
  for (let i = 0; i < 4; i++) d += `<i class="${i < on ? "on" : ""}"></i>`;
  return `<span class="conf" title="${esc(t("confTitle"))}">${t("lblConf")} <span class="dots">${d}</span></span>`;
}
function gapSpan(g) {
  return `<span class="${g >= 0 ? "gap-pos" : "gap-neg"}">${g >= 0 ? "+" : ""}${fmt(g, 2)}</span>`;
}
const SIM_TITLE = { style: "simTitleStyle", taste: "simTitleTaste", latent: "simTitleLatent" };
const RIVAL_LABEL = { peer: "rivalPeer", growth: "rivalGrowth", chaser: "rivalChaser", aspirational: "rivalAspirational" };
const TAG_KEY = { popular: "tagPopular", highGain: "tagHighGain", efficient: "tagEfficient", easy: "tagEasy", challenge: "tagChallenge", confident: "tagConfident" };

/* ---------------- state ---------------- */
let META = null;
let CURRENT_ID = null;
// Selected instrument (drum | guitar). Default drum; persisted in localStorage
// and reflected in the ?inst= URL param. Every API call carries it via withInst().
let INST = "drum";
function loadInst() { try { return localStorage.getItem("gd_inst"); } catch (e) { return null; } }
function saveInst(v) { try { localStorage.setItem("gd_inst", v); } catch (e) { /* ignore */ } }
function instName(code) { return t(code === "guitar" ? "instGuitar" : "instDrum"); }
// Append the selected instrument to an API path (default drum is harmless/explicit).
function withInst(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}inst=${encodeURIComponent(INST)}`;
}

/* Overlay share tokens (spec §9): the owner's one-time token, kept in
   localStorage keyed by the player's internal id so we can (a) request the
   private overlay via ?token= and (b) flip visibility via /api/publish. */
const TOKENS_KEY = "gd_overlay_tokens";
function loadTokens() {
  try { return JSON.parse(localStorage.getItem(TOKENS_KEY) || "{}") || {}; }
  catch (e) { return {}; }
}
function getToken(internalId) { return loadTokens()[String(internalId)] || null; }
function saveToken(internalId, info) {
  try {
    const m = loadTokens();
    m[String(internalId)] = Object.assign({}, m[String(internalId)], info);
    localStorage.setItem(TOKENS_KEY, JSON.stringify(m));
  } catch (e) { /* private mode / quota: tokens just won't persist */ }
}

function twDate(builtAt) {
  const d = new Date(builtAt);
  if (isNaN(d)) return String(builtAt).slice(0, 13);
  const tw = new Date(d.getTime() + 8 * 3600 * 1000);  // shift to UTC+8, read UTC parts
  const p = n => String(n).padStart(2, "0");
  return `${tw.getUTCFullYear()}-${p(tw.getUTCMonth() + 1)}-${p(tw.getUTCDate())} ${p(tw.getUTCHours())}:00`;
}

function applyStatic() {
  document.documentElement.lang = getLang() === "zh" ? "zh-Hant" : getLang();
  document.title = t("docTitle", { inst: instName(INST) });
  const inp = $("#search"); if (inp) inp.placeholder = t("searchPlaceholder");
  document.querySelectorAll("[data-i18n]").forEach(e => { e.textContent = t(e.dataset.i18n); });
  const gh = $("#ghlink"); if (gh) gh.title = t("ghIssuesTitle");
  const ham = $("#hamburger"); if (ham) ham.setAttribute("aria-label", t("menu"));
  if (META) {
    $("#metaSub").textContent = t("metaSub", { inst: instName(META.instrument || INST), version: META.versionName, players: fmt(META.players), charts: fmt(META.charts) });
    if (META.builtAt) $("#dataDate").textContent = t("dataUpdated", { date: twDate(META.builtAt) });
  }
}

function renderLangSel() {
  const box = $("#langsel");
  if (!box) return;
  box.innerHTML = LANGS.map(([code, label]) =>
    `<button class="langbtn ${code === getLang() ? "on" : ""}" data-l="${code}">${label}</button>`).join("");
  box.querySelectorAll(".langbtn").forEach(b => {
    b.onclick = () => changeLang(b.dataset.l);
  });
}
function changeLang(l) {
  if (l === getLang()) return;
  setLang(l);
  applyStatic();
  renderLangSel();
  renderInstSel();
  closeModal();
  if (CURRENT_ID != null) loadPlayer(CURRENT_ID, { hist: "replace" }); else loadDefault();
}

const INST_SHORT = { drum: "DM", guitar: "GF" };
function renderInstSel() {
  const box = $("#instsel");
  if (!box) return;
  const insts = (META && META.instruments) || ["drum"];
  // Only show the switcher when more than one instrument is actually built.
  if (insts.length < 2) { box.innerHTML = ""; return; }
  box.title = t("instSwitchTitle");
  box.innerHTML = insts.map(code =>
    `<button class="instbtn ${code === INST ? "on" : ""}" data-i="${esc(code)}" title="${esc(instName(code))}">${INST_SHORT[code] || code.toUpperCase()}</button>`).join("");
  box.querySelectorAll(".instbtn").forEach(b => { b.onclick = () => changeInst(b.dataset.i); });
}

async function fetchMeta() {
  // INST must already be set to the desired instrument before calling.
  try { return await api(withInst("/api/meta")); }
  catch (e) { INST = "drum"; return await api("/api/meta"); }
}

async function changeInst(code) {
  if (code === INST) return;
  INST = code;
  saveInst(code);
  const url = new URL(location.href);
  url.searchParams.set("inst", code);
  url.searchParams.delete("p");   // player ids do not map across instruments
  history.replaceState(null, "", url.search);
  META = await fetchMeta();
  INST = META.instrument || code;
  applyStatic();
  renderInstSel();
  closeModal();
  CURRENT_ID = null;
  loadDefault();
}

async function boot() {
  const url = new URL(location.href);
  INST = url.searchParams.get("inst") || loadInst() || "drum";
  META = await fetchMeta();
  INST = META.instrument || "drum";
  saveInst(INST);
  applyStatic();
  renderLangSel();
  renderInstSel();
  setupSearch();
  setupHamburger();
  setupHistory();
  $("#brand").onclick = () => loadDefault({ hist: "push" });
  const pid = url.searchParams.get("p");
  if (pid != null) return loadPlayer(+pid, { hist: "replace" });
  loadDefault();
}

async function loadDefault({ hist = "replace" } = {}) {
  const r = await api(withInst("/api/top"));
  if (r.results && r.results.length) loadPlayer(r.results[0].id, { hist });
}

/* ---------------- mobile control dropdown ---------------- */
// On narrow screens the instrument / language / GitHub controls collapse into a
// dropdown the hamburger toggles. Listeners are delegated to #topctl so they
// survive the innerHTML rebuilds in renderInstSel / renderLangSel.
function setupHamburger() {
  const btn = $("#hamburger"), ctl = $("#topctl");
  if (!btn || !ctl) return;
  const setOpen = open => {
    // On close, return focus to the trigger if it was inside the menu — the
    // dropdown is display:none'd, which would otherwise drop focus to <body>.
    if (!open && ctl.contains(document.activeElement)) btn.focus();
    ctl.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn.addEventListener("click", e => { e.stopPropagation(); setOpen(!ctl.classList.contains("open")); });
  // Picking any control (instrument / language / GitHub) closes the menu.
  ctl.addEventListener("click", e => { if (e.target.closest("button, a")) setOpen(false); });
  // Click outside or Escape closes it.
  document.addEventListener("click", e => {
    if (!e.target.closest("#topctl") && !e.target.closest("#hamburger")) setOpen(false);
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") setOpen(false); });
}

/* ---------------- back/forward navigation ----------------
   Player jumps pushState a new history entry, so the browser Back button returns
   to the previously-viewed player; popstate replays whatever the URL now names
   without pushing again. */
function setupHistory() {
  window.addEventListener("popstate", async () => {
    const url = new URL(location.href);
    // Drum history entries omit ?inst= (see loadPlayer's histUrl), so a missing
    // param canonically means drum — default to that, not the current INST, or
    // backing into a drum entry while on guitar would load the wrong player.
    const inst = url.searchParams.get("inst") || "drum";
    const pid = url.searchParams.get("p");
    // A hash-only navigation (e.g. the #songs / #rivals subnav quick-jump links)
    // also fires popstate, but the player has not changed — reloading it here would
    // reset scroll and kill the jump. Skip when player + instrument are unchanged
    // and let the browser perform the native fragment scroll.
    if (inst === INST && pid != null && Number(pid) === CURRENT_ID) return;
    if (inst !== INST) {
      INST = inst;
      META = await fetchMeta();
      INST = META.instrument || inst;
      saveInst(INST);
      applyStatic();
      renderInstSel();
    }
    closeModal();
    if (pid != null) loadPlayer(+pid, { hist: "none" });
    else loadDefault({ hist: "none" });
  });
}

/* ---------------- search ---------------- */
function setupSearch() {
  const inp = $("#search"), box = $("#results");
  let timer = null;
  const close = () => box.classList.remove("show");
  inp.addEventListener("input", () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (!q) { close(); return; }
    timer = setTimeout(async () => {
      const r = await api(withInst("/api/search?q=" + encodeURIComponent(q)));
      box.innerHTML = "";
      if (!r.results.length) box.innerHTML = `<div class="row"><span class="nm" style="color:var(--dim)">${t("noPlayer")}</span></div>`;
      r.results.forEach(p => {
        const row = el("div", "row");
        row.innerHTML = `<span class="nm">${skillText(p.sp, esc(p.name))}</span><span class="meta-chip">#${p.playerId}</span>${pspan(p.sp)}`;
        row.onclick = () => { close(); inp.value = ""; loadPlayer(p.id); };
        box.appendChild(row);
      });
      box.classList.add("show");
    }, 180);
  });
  document.addEventListener("click", e => { if (!e.target.closest(".search-wrap")) close(); });
  inp.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
}

/* ---------------- load + render ---------------- */
async function loadPlayer(id, { hist = "push" } = {}) {
  const app = $("#app");
  CURRENT_ID = id;
  app.innerHTML = `<div class="loading"><span class="spin"></span> ${t("analyzing")}</div>`;
  window.scrollTo({ top: 0, behavior: "auto" });
  // Owner overlay token: a share token in the URL takes precedence over a stored
  // one (lets a shared ?p=&token= link work on a fresh device); both grant the
  // owner a private-overlay read via ?token=.
  const stored = getToken(id);
  const urlToken = new URL(location.href).searchParams.get("token");
  const token = urlToken || (stored && stored.token);
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  let data;
  try { data = await api(withInst(`/api/player/${id}/all${q}`)); }
  catch (e) { app.innerHTML = `<div class="empty">${t("loadFail", { msg: esc(e.message) })}</div>`; return; }
  // Persist/refresh the token (carry the gsvPlayerId the publish route needs);
  // keep the token out of the visible address bar.
  if (token) {
    saveToken(id, { token, gsvPlayerId: data.profile.player.playerId,
                    visibility: data.profile.visibility || (stored && stored.visibility) || "private" });
  }
  const histUrl = INST === "drum" ? `?p=${id}` : `?p=${id}&inst=${INST}`;
  if (hist === "push") history.pushState({ p: id }, "", histUrl);
  else if (hist === "replace") history.replaceState({ p: id }, "", histUrl);
  // hist === "none": URL was already set by popstate navigation.
  app.innerHTML = "";
  app.appendChild(renderHero(data.profile));
  app.appendChild(renderSimilar(data.similar, data.profile));
  app.appendChild(renderRivals(data.rivals, data.profile));
  app.appendChild(renderSongs(data.songs, data.profile));
}

/* ---------------- hero / profile ---------------- */
function renderHero(p) {
  const sec = el("section", "section"); sec.id = "hero";
  const pl = p.player;
  const maxBar = Math.max(p.hotPoint, p.otherPoint) * 1.05 || 1;
  const enhanced = p.enhanced === true;
  const eBadge = enhanced
    ? `<span class="ebadge" title="${esc(t("enhancedTitle"))}">${t("enhancedBadge")}</span>` : "";

  const left = el("div", "left");
  left.innerHTML = `
    <div class="pname">
      <h2 class="skillc" style="background-image:${gdSkillBg(pl.sp)}">${esc(pl.name)}</h2>
      <span class="tier">${gdName(pl.sp)}</span>${eBadge}
      <a class="gsv" href="${esc(pl.gsvUrl)}" target="_blank" rel="noopener">${t("gsvProfile")}</a>
    </div>
    <div class="readout">
      <div class="big"><span class="skillc" style="background-image:${gdSkillBg(pl.sp)}">${fmt(pl.sp, 2)}</span><small>${t("skillUnit")}</small></div>
      <div class="rank">${t("rankLine", { rank: fmt(p.rank), total: fmt(p.totalPlayers) })}<br/>${t("leadLine", { pct: p.percentile })}</div>
    </div>
    <div class="bars">
      <div class="bar-row">
        <span class="lbl">HOT</span>
        <div class="bar-track"><div class="bar-fill hot" style="width:${(p.hotPoint/maxBar*100).toFixed(1)}%"></div></div>
        <span class="val">${fmt(p.hotPoint,2)}</span>
      </div>
      <div class="bar-row"><span class="lbl"></span><span class="cut">${t("cutoffLine", { cut: fmt(p.hotCutoff,2), count: p.hotCount })}</span><span></span></div>
      <div class="bar-row">
        <span class="lbl">OTHER</span>
        <div class="bar-track"><div class="bar-fill other" style="width:${(p.otherPoint/maxBar*100).toFixed(1)}%"></div></div>
        <span class="val">${fmt(p.otherPoint,2)}</span>
      </div>
      <div class="bar-row"><span class="lbl"></span><span class="cut">${t("cutoffLine", { cut: fmt(p.otherCutoff,2), count: p.otherCount })}</span><span></span></div>
    </div>
    <div class="stat-grid">
      <div class="stat"><div class="k">${t("statMaxLevel")}</div><div class="v">${p.maxLevel}</div></div>
      <div class="stat"><div class="k">${t("statAvgLevel")}</div><div class="v">${p.avgLevel}</div></div>
      <div class="stat"><div class="k">${t("statAvgAch")}</div><div class="v">${(p.avgAch*100).toFixed(2)}<small>%</small></div></div>
      <div class="stat"><div class="k">${t("statBracket")}</div><div class="v">${fmt(p.kasegiScope)}<small>+</small></div></div>
    </div>
    ${enhanced ? renderOverlayPanel(p) : ""}
    ${renderHist(p.levelHist)}
    ${renderSignature(p.signature)}
    ${renderImprove(p.improve)}
  `;

  const right = el("div", "padbox");
  right.appendChild(padHeader());
  right.appendChild(poolLabel(t("poolHot", { n: p.hotCount })));
  right.appendChild(padGrid(p.sheetHot));
  right.appendChild(poolLabel(t("poolOther", { n: p.otherCount })));
  right.appendChild(padGrid(p.sheetOther));

  const hero = el("div", "hero");
  hero.appendChild(left); hero.appendChild(right);

  const head = el("div", "head", `<span class="idx">01</span><h2>${t("secReadout")} ${help(t("heroHelp"))}</h2>
    <span class="note">${t("readoutNote")}</span>`);
  sec.appendChild(head); sec.appendChild(hero);
  return sec;
}

function padHeader() {
  return el("div", "", `<div class="padhead"><span class="t">${t("benchTitle")} ${help(t("zFull"))}</span>
    <span class="legend">${t("legendWeak")} <span class="grad"></span> ${t("legendStrong")} ${help(t("zFull"), "r")}</span></div>`);
}
function poolLabel(text) { return el("div", "pool-label", esc(text)); }
function padGrid(list) {
  const g = el("div", "pads-grid");
  list.forEach(c => {
    const pad = el("div", "pad");
    pad.style.background = zColor(c.z);
    pad.setAttribute("role", "button");
    pad.setAttribute("tabindex", "0");
    pad.innerHTML = `<span class="plv" style="color:${diffColor(c.diff)}">Lv${LV(c.level)} ${c.diff}${partTag(c.part)}</span>
      <span class="pnm">${esc(c.name)}</span>
      <b class="pskill">${fmt(c.skill,2)}</b>
      <span class="pmeta">${pct(c.ach)}</span>
      <div class="tip"><div class="nm">${esc(c.name)}${partTag(c.part)}</div>
        <div class="kv"><span style="color:${diffColor(c.diff)}">${t("padTipDiff", { diffFull: c.diffFull, level: LV(c.level), pool: c.pool.toUpperCase() })}</span></div>
        <div class="kv">${t("padTipSkill", { skill: fmt(c.skill,2), ach: pct(c.ach) })}</div>
        <div class="kv">${t("padTipZ", { z: c.z == null ? "—" : c.z, near: c.nearPeers == null ? "—" : c.nearPeers, count: c.count })}</div>
        <div class="kv tap">${t("padTipTap")}</div>
      </div>`;
    pad.onclick = () => openChart(c.id);
    pad.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChart(c.id); } };
    g.appendChild(pad);
  });
  return g;
}

function renderHist(hist) {
  const entries = Object.entries(hist || {});
  if (!entries.length) return "";
  const max = Math.max(...entries.map(e => e[1]));
  const cols = entries.map(([lv, n]) => {
    const f = parseFloat(lv);
    const end = lv === "9.9" ? 9.99 : f + 0.49;
    return `<div class="col"><span class="cn">${n}</span>` +
      `<div class="b" style="height:${Math.max(4, Math.round(n/max*60))}px" title="${t("histBarTitle", { a: LV(f), b: LV(end), n })}"></div>` +
      `<div class="x">${LV(f)}</div></div>`;
  }).join("");
  return `<div style="margin-top:16px"><div class="pool-label">${t("histTitle")}</div><div class="hist">${cols}</div></div>`;
}

function renderSignature(sig) {
  if (!sig || !sig.length) return "";
  const items = sig.slice(0, 8).map(c =>
    `<div class="li"><span class="s">${esc(c.name)}${partTag(c.part)} ${lvtag(c.diff, c.level)}</span>
      <span class="vs"><span class="a">z ${c.z}</span> · ${pct(c.ach)}</span></div>`).join("");
  return `<div class="mini"><div class="mt">${t("signatureTitle")} ${help(t("zShort"))}</div>${items}</div>`;
}
function renderImprove(list) {
  if (!list || !list.length) return "";
  const items = list.slice(0, 6).map(c =>
    `<div class="li"><span class="s">${esc(c.name)}${partTag(c.part)} ${lvtag(c.diff, c.level)}</span>
      <span class="vs"><span style="color:var(--coral)">z ${c.z}</span> · ${pct(c.ach)}</span></div>`).join("");
  return `<div class="mini"><div class="mt">${t("improveTitle")} ${help(t("zShort"))}</div>${items}</div>`;
}
// Summary of the uploaded overlay shown in the hero when the profile is enhanced.
function renderOverlayPanel(p) {
  const os = p.overlayStats || {};
  const vis = p.visibility || "private";
  const visTxt = vis === "public" ? t("visibilityPublic") : t("visibilityPrivate");
  return `<div class="overlay-panel">
    <div class="op-line">${t("overlayStatsLine", { played: fmt(os.played), evaluated: fmt(os.evaluated), exact: fmt(os.exact), coarse: fmt(os.coarse) })}</div>
    <div class="op-vis">${t("visLabel")}: <b class="vis ${vis}">${visTxt}</b></div>
  </div>`;
}

/* ---------------- similar players ---------------- */
function renderSimilar(list, prof) {
  const sec = el("section", "section"); sec.id = "similar";
  sec.appendChild(el("div", "head", `<span class="idx">02</span><h2>${t("secSimilar")} ${help(t("similarHelp"))}</h2>
    <span class="note">${t("similarNote", { name: esc(prof.player.name) })}</span>`));
  if (!list || !list.length) { sec.appendChild(el("div", "empty", t("emptySimilar"))); return sec; }
  const cards = el("div", "cards");
  list.forEach(s => cards.appendChild(similarCard(s)));
  sec.appendChild(cards);
  return sec;
}
function simBar(name, cls, v) {
  const w = Math.max(0, Math.min(1, v)) * 100;
  return `<div class="simbar"><span class="nm" title="${esc(t(SIM_TITLE[cls]))}">${name}</span><div class="tr"><div class="fl ${cls}" style="width:${w}%"></div></div><span class="v">${v.toFixed(2)}</span></div>`;
}
function similarCard(s) {
  const c = el("div", "card");
  const shared = (s.sharedHighlights || []).map(h =>
    `<div class="li"><span class="s">${esc(h.name)}${partTag(h.part)} ${lvtag(h.diff, h.level)}</span>
      <span class="vs"><span class="a">${fmt(h.yours,2)}</span> / <span class="b">${fmt(h.theirs,2)}</span></span></div>`).join("");
  const learn = (s.learnFrom || []).map(h =>
    `<div class="li"><span class="s">${esc(h.name)}${partTag(h.part)} ${lvtag(h.diff, h.level)}</span>
      <span class="tag ${h.pool}">${h.pool.toUpperCase()}</span></div>`).join("");
  c.innerHTML = `
    <div class="top"><span class="nm">${pnameLink(s)}</span>${pspan(s.sp)}</div>
    <div class="simbars">
      ${simBar("style","style",s.style)}
      ${simBar("taste","taste",s.taste)}
      ${simBar("latent","latent",s.latent)}
    </div>
    <div class="metarow">
      <span class="m">${t("lblShared")} <b>${s.sharedCharts}</b></span>
      <span class="m">${t("lblSpGap")} ${gapSpan(s.spGap)}</span>
      ${confDots(s.confidence)}
    </div>
    ${shared ? `<div class="mini"><div class="mt">${t("sharedTitle")}</div>${shared}</div>` : ""}
    ${learn ? `<div class="mini"><div class="mt">${t("learnTitle")}</div>${learn}</div>` : ""}
  `;
  return c;
}

/* ---------------- rivals ---------------- */
function renderRivals(list, prof) {
  const sec = el("section", "section"); sec.id = "rivals";
  sec.appendChild(el("div", "head", `<span class="idx">03</span><h2>${t("secRivals")} ${help(t("rivalsHelp"))}</h2>
    <span class="note">${t("rivalsNote")}</span>`));
  if (!list || !list.length) { sec.appendChild(el("div", "empty", t("emptyRivals"))); return sec; }
  const cards = el("div", "cards");
  list.forEach(r => cards.appendChild(rivalCard(r)));
  sec.appendChild(cards);
  sec.appendChild(el("div", "footnote", t("rivalsFootnote")));
  return sec;
}
function rivalCard(r) {
  const c = el("div", "card");
  const you = (r.youWin || []).map(h => h2hLine(h, "win")).join("");
  const them = (r.theyWin || []).map(h => h2hLine(h, "lose")).join("");
  c.innerHTML = `
    <div class="top">
      <span class="nm">${pnameLink(r)}</span>
      <span class="tag ${r.category}">${t(RIVAL_LABEL[r.category] || "rivalPeer")}</span>
      ${pspan(r.sp)}
    </div>
    <div class="metarow">
      <span class="m">${t("lblSpGap")} ${gapSpan(r.spGap)}</span>
      <span class="m">style <b>${r.style.toFixed(2)}</b></span>
      <span class="m">taste <b>${r.taste.toFixed(2)}</b></span>
      <span class="m">${t("lblShared")} <b>${r.sharedCharts}</b></span>
      ${confDots(r.confidence)}
    </div>
    ${them ? `<div class="mini"><div class="mt">${t("theyWinTitle")}</div>${them}</div>` : ""}
    ${you ? `<div class="mini"><div class="mt">${t("youWinTitle")}</div>${you}</div>` : ""}
  `;
  return c;
}
function h2hLine(h, cls) {
  return `<div class="li ${cls}"><span class="s">${esc(h.name)}${partTag(h.part)} ${lvtag(h.diff, h.level)}</span>
    <span class="vs"><span class="a">${fmt(h.yours,2)}</span>/<span class="b">${fmt(h.theirs,2)}</span> <span class="delta">${h.delta>=0?"+":""}${fmt(h.delta,2)}</span></span></div>`;
}

/* ---------------- songs ---------------- */
const SONG_TABS = [["combined", "tabCombined", "tabHintCombined"], ["skillUp", "tabSkillUp", "tabHintSkillUp"], ["discovery", "tabDiscovery", "tabHintDiscovery"]];
// When the player has an uploaded overlay the engine returns the three dense-data
// classes (spec §6.3) instead of the sweet-spot tab.
const ENHANCED_SONG_TABS = [["practiceTargets", "tabPractice", "tabHintPractice"], ["skillUp", "tabSkillUp", "tabHintSkillUpOv"], ["discovery", "tabDiscovery", "tabHintDiscovery"]];
function songWhy(c, kind) {
  if (kind === "discovery")
    return t("whyDiscovery", { nbr: c.neighborHolders, total: c.neighborTotal, level: LV(c.level), kasegi: c.inKasegi ? t("whyKasegiSuffix") : "" });
  if (kind === "combined")
    return t("whyCombined", { nbr: c.neighborHolders, total: c.neighborTotal, estAch: (c.estAch*100).toFixed(2), pool: c.pool.toUpperCase(), gain: fmt(c.gain, 2) });
  return t("whySkillUp", { estAch: (c.estAch*100).toFixed(2), estSkill: fmt(c.estSkill, 2), pool: c.pool.toUpperCase(), cutoff: fmt(c.cutoff, 2), gain: fmt(c.gain, 2), conf: Math.round((c.estConfidence||0)*100) + "%" });
}
function renderSongs(songs, prof) {
  const sec = el("section", "section"); sec.id = "songs";
  const enhanced = songs.enhanced === true;
  const eBadge = enhanced ? ` <span class="ebadge" title="${esc(t("enhancedTitle"))}">${t("enhancedBadge")}</span>` : "";
  sec.appendChild(el("div", "head", `<span class="idx">04</span><h2>${t("secSongs")}${eBadge} ${help(t("songsHelp"))}</h2>
    <span class="note">${t("songsNote", { scope: fmt(songs.kasegiScope), med: songs.myMedianLevel, max: songs.myMaxLevel })}</span>`));

  const tabSet = enhanced ? ENHANCED_SONG_TABS : SONG_TABS;
  const tabs = el("div", "tabs");
  const listWrap = el("div", "songlist");
  let active = tabSet[0][0];
  function paint() {
    [...tabs.children].forEach(t_ => t_.classList.toggle("on", t_.dataset.k === active));
    const data = songs[active] || [];
    listWrap.innerHTML = "";
    if (!data.length) { listWrap.appendChild(el("div", "empty", t("songsEmpty"))); return; }
    // Enhanced practiceTargets / skillUp carry overlay-specific fields (currentAch,
    // neededAch, headroom …); discovery keeps the base shape.
    const enhRow = enhanced && (active === "practiceTargets" || active === "skillUp");
    data.forEach(c => listWrap.appendChild(enhRow ? enhancedSongRow(c, active) : songRow(c, active)));
  }
  tabSet.forEach(([k, labelKey, hintKey]) => {
    const tab = el("div", "tab"); tab.dataset.k = k;
    tab.innerHTML = `${t(labelKey)} <span class="ct">${(songs[k]||[]).length}</span>`;
    tab.title = t(hintKey);
    tab.onclick = () => { active = k; paint(); };
    tabs.appendChild(tab);
  });
  sec.appendChild(tabs);
  sec.appendChild(listWrap);
  paint();
  return sec;
}
// Row renderer for the overlay-only classes (practiceTargets / dense skillUp).
function enhancedSongRow(c, kind) {
  const row = el("div", "song");
  row.setAttribute("role", "button");
  row.setAttribute("tabindex", "0");
  const tags = (c.tags || []).map(k => `<span class="tag">${t(TAG_KEY[k] || k)}</span>`).join("");
  const obsTag = c.exact
    ? `<span class="tag exact">${t("exactTag")}</span>`
    : `<span class="tag coarse">${t("coarseTag")}</span>`;
  let why, right;
  if (kind === "practiceTargets") {
    why = t("whyPractice", {
      curAch: (c.currentAch * 100).toFixed(2), needAch: (c.neededAch * 100).toFixed(2),
      pool: c.pool.toUpperCase(), cutoff: fmt(c.cutoff, 2), gain: fmt(c.gainIfCutoff, 2),
    });
    right = `<div class="gain" title="${esc(t("gainIfCutoffTitle"))}">+${fmt(c.gainIfCutoff, 2)}</div>
      <div class="sub tgt">${t("targetPct", { pct: (c.neededAch * 100).toFixed(2) })}</div>
      <div class="sub">${t("curAchLabel", { v: (c.currentAch * 100).toFixed(2) })}</div>`;
  } else { // overlay skillUp: already in sheet, headroom to push achievement
    why = t("whySkillUpOv", {
      curAch: (c.currentAch * 100).toFixed(2), head: fmt(c.headroom, 2),
      pool: c.pool.toUpperCase(), cutoff: fmt(c.cutoff, 2),
    });
    right = `<div class="gain" title="${esc(t("headroomTitle"))}">+${fmt(c.headroom, 2)}</div>
      <div class="sub">${t("curAchLabel", { v: (c.currentAch * 100).toFixed(2) })}</div>
      <div class="sub">${t("cutoffLabel", { v: fmt(c.cutoff, 2) })}</div>`;
  }
  row.innerHTML = `
    <div class="lvbadge" style="background:${diffColor(c.diff)}" title="${c.diffFull} · Lv${LV(c.level)}">
      <span class="n">${LV(c.level)}</span><span class="d">${c.diff}</span>
    </div>
    <div class="body">
      <div class="nm"><span class="snm">${esc(c.name)}</span>${partTag(c.part)}
        <span class="tag ${c.pool}">${c.pool.toUpperCase()}</span>
        ${obsTag}
        ${ytLinkEl(c.name)}</div>
      <div class="why">${esc(why)}</div>
      <div class="tags">${tags}</div>
    </div>
    <div class="right">${right}</div>
  `;
  row.onclick = () => openChart(c.id);
  row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChart(c.id); } };
  const yt = row.querySelector(".ytlink");
  if (yt) yt.onclick = e => e.stopPropagation();
  return row;
}
function songRow(c, kind) {
  const row = el("div", "song");
  row.setAttribute("role", "button");
  row.setAttribute("tabindex", "0");
  const showGain = kind !== "discovery";
  const tags = (c.tags || []).map(k => `<span class="tag">${t(TAG_KEY[k] || k)}</span>`).join("");
  const fracW = Math.round((c.neighborFrac || 0) * 100);
  // achievement needed for this chart's single-song skill to clear your cutoff:
  // skill = level*20*ach ≥ cutoff  →  ach ≥ cutoff/(level*20)
  const tp = c.level > 0 ? (c.cutoff / (c.level * 20)) * 100 : null;
  const tpStr = tp == null ? "—" : (tp > 100 ? ">100" : tp.toFixed(2));
  const tgt = `<div class="sub tgt" title="${esc(t("targetPctTitle"))}">${t("targetPct", { pct: tpStr })}</div>`;
  const right = showGain
    ? `<div class="gain ${c.gain>0?"":"neg"}" title="${esc(t("gainTitle"))}">${c.gain>=0?"+":""}${fmt(c.gain,2)}</div>
       <div class="sub" title="${esc(t("estSkillTitle", { pool: c.pool.toUpperCase() }))}">${t("estSkillLabel", { v: fmt(c.estSkill,2) })}<br/>${t("cutoffLabel", { v: fmt(c.cutoff,2) })}</div>
       ${tgt}`
    : `<div class="frac-mini" title="${esc(t("fracTitle", { total: c.neighborTotal, n: c.neighborHolders }))}"><div class="tr"><div class="fl" style="width:${fracW}%"></div></div></div>
       <div class="sub">${t("fracSub", { n: c.neighborHolders, total: c.neighborTotal })}</div>
       ${tgt}`;
  row.innerHTML = `
    <div class="lvbadge" style="background:${diffColor(c.diff)}" title="${c.diffFull} · Lv${LV(c.level)}">
      <span class="n">${LV(c.level)}</span><span class="d">${c.diff}</span>
    </div>
    <div class="body">
      <div class="nm"><span class="snm">${esc(c.name)}</span>${partTag(c.part)}
        <span class="tag ${c.pool}">${c.pool.toUpperCase()}</span>
        ${ytLinkEl(c.name)}</div>
      <div class="why">${esc(songWhy(c, kind))}</div>
      <div class="tags">${tags}</div>
    </div>
    <div class="right">${right}</div>
  `;
  row.onclick = () => openChart(c.id);
  row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChart(c.id); } };
  const yt = row.querySelector(".ytlink");
  if (yt) yt.onclick = e => e.stopPropagation();
  return row;
}

/* ---------------- pad/song click → same-bracket detail modal ---------------- */
function _esc(e) { if (e.key === "Escape") closeModal(); }
function closeModal() {
  const m = $("#modal");
  if (m) { m.remove(); document.removeEventListener("keydown", _esc); }
}
async function openChart(chartId) {
  if (CURRENT_ID == null) return;
  let d;
  try { d = await api(withInst(`/api/player/${CURRENT_ID}/chart/${chartId}`)); }
  catch (e) { return; }
  renderChartModal(d);
}
function renderChartModal(d) {
  closeModal();
  const ch = d.chart, you = d.you, near = d.near;

  let histHtml = "";
  if (d.achHist && d.achHist.length) {
    const maxN = Math.max(...d.achHist.map(b => b.n));
    const yourBin = you.present && you.ach != null ? Math.floor(you.ach * 100) : null;
    const bars = d.achHist.map(b => {
      const me = b.pct === yourBin;
      const lab = (b.pct % 5 === 0 || me) ? b.pct : "";
      return `<div class="hcol"><div class="hb${me ? " me" : ""}" style="height:${Math.max(3, Math.round(b.n / maxN * 84))}px"
        title="${t("modalHistBarTitle", { a: b.pct, b: b.pct + 1, n: b.n })}"></div><div class="hx">${lab}</div></div>`;
    }).join("");
    histHtml = `<div class="mblock"><div class="mbh">${t("modalHistTitle", { self: you.present ? t("modalHistSelf") : "" })}</div>
      <div class="mhist">${bars}</div></div>`;
  }

  const peers = (d.peers || []).map(p =>
    `<div class="prow${p.isYou ? " me" : ""}" data-id="${p.id}">
       <span class="pn"><span class="pnmt">${p.isYou ? "★ " : ""}${skillText(p.sp, esc(p.name))}</span><span class="psp">(${fmt(p.sp, 2)})</span></span>
       <span class="pp">${pct(p.ach)}</span>
       <span class="ps">${fmt(p.skill, 2)}</span>
       <a class="pgsv" href="${esc(p.gsvUrl)}" target="_blank" rel="noopener" title="gsv">↗</a>
     </div>`).join("");

  const youLine = you.present
    ? t("modalYouHas", { skill: fmt(you.skill, 2), ach: pct(you.ach) })
      + (you.achPercentile != null ? t("modalYouPct", { pct: you.achPercentile }) : "")
    : t("modalYouNone");
  const nearLine = near
    ? t("modalNear", { band: d.band, size: fmt(d.bracketSize), n: near.n, rate: (d.holdRate * 100).toFixed(2) })
    : t("modalNearNone");
  const statLine = near
    ? t("modalStat", { mean: pct(near.achMean), median: pct(near.achMedian), min: pct(near.achMin), max: pct(near.achMax), smean: fmt(near.skillMean, 2), smedian: fmt(near.skillMedian, 2) })
    : "";

  const overlay = el("div", "modal-overlay"); overlay.id = "modal";
  overlay.innerHTML = `<div class="modalbox" role="dialog" aria-modal="true" aria-label="${esc(ch.name)}">
    <button class="mclose" aria-label="${t("close")}">✕</button>
    <div class="mhead">
      <span class="mbadge" style="background:${diffColor(ch.diff)}">${LV(ch.level)}<small>${ch.diff}</small></span>
      <div class="mht"><div class="mtitle">${esc(ch.name)}${partTag(ch.part)}</div>
        <div class="msub"><span style="color:${diffColor(ch.diff)};font-weight:600">${ch.diffFull} · Lv${LV(ch.level)}</span> · <span class="tag ${ch.pool}">${ch.pool.toUpperCase()}</span> · ${t("globalHolders", { n: fmt(ch.globalHolders) })}
          · ${ytLinkEl(ch.name)}</div></div>
    </div>
    <div class="myou">${youLine}</div>
    <div class="mline">${nearLine}</div>
    ${statLine ? `<div class="mline dim">${statLine}</div>` : ""}
    ${histHtml}
    <div class="mblock"><div class="mbh">${t("modalPeersTitle")}</div>
      <div class="ptable"><div class="prow phead"><span class="pn">${t("modalPeerPlayer")}</span><span class="pp">${t("modalPeerAch")}</span><span class="ps">${t("modalPeerSkill")}</span><span class="pgsv"></span></div>${peers}</div>
    </div>
    <div class="footnote">${t("modalFootnote")}</div>
  </div>`;
  overlay.onclick = e => { if (e.target === overlay) closeModal(); };
  overlay.querySelector(".mclose").onclick = closeModal;
  overlay.querySelectorAll(".prow[data-id] .pn").forEach(eln => {
    eln.style.cursor = "pointer";
    eln.onclick = () => { const id = +eln.parentElement.dataset.id; closeModal(); loadPlayer(id); };
  });
  document.body.appendChild(overlay);
  document.addEventListener("keydown", _esc);
}

boot();
