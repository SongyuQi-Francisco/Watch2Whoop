/* Today page v2 (PWA): restrained, few words, let the numbers speak. No build step: plain JS, charts are hand-drawn SVG.
   Pages: Today / Trends (metrics, sleep) / Log / Me; explanations and details live in bottom sheets that open on tap.
   A persistent "Ask the butler" (「问管家」) at the bottom: when the default AI is an API type (each person's own key) it opens the in-page chat (ai.js); for subscription types / when unset it opens the body butler
   Claude Code session (Remote Control) link (assistant_url from /v1/web/me, only given to the deployer) -- subscriptions can only be used in the official client.
   Endpoints: /v1/web/* (login), /v1/users/{u}/today | sleep/nights | trends | journal/frequent (aggregates),
   /v1/users/{u}/memory (body profile card, observation notes, provided by the body butler session), plus the existing journal / events / supplements / reminders.
   Auth relies on an httpOnly cookie; write requests carry X-Body-Web: 1.
   Inside the Watch2Whoop iOS app (the WebView injects window.Watch2Whoop): the Me page and the login page get an extra "Phone sync" (「手机同步」) entry;
   the link watch2whoop://sync is intercepted by the app to open the native sync status page; it is not shown in a browser. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const IN_APP = !!window.Watch2Whoop;  // the WebView of the Watch2Whoop iOS app
  // In the app, long press on an image offers "Save to Photos", which kills app builds without NSPhotoLibraryAddUsageDescription
  // (1.0.5 and older): app.css turns the long-press menu off under html.in-app; loadHandbook adds the same rule to its shadow root.
  if (IN_APP) document.documentElement.classList.add("in-app");
  // i18n.js (loaded first): tr(key, params) -> text in the user's language. Named tr because many locals here are called t.
  const I18N = window.I18N, tr = I18N.t;
  // Features in separate files (profile.js profile and consent, sync.js refresh and history progress): each entry in window.BodyPlugins may have
  // init(ctx, me), meItem(chev) -> one row of HTML for the Me page, bindMe(view); grocery.js adds foodShop(info) / bindFoodShop(view) for 饮食 → 采购;
  // diet-profile.js adds foodTop(info) / bindFoodTop(view): one line above both 饮食 modes; water.js adds foodDiet(info) / bindFoodDiet(view):
  // the water card right after 「今天吃了」.
  const PLUGINS = () => window.BodyPlugins || [];
  // Optional plugins of this server (BODY_PLUGIN_DIR, body_engine/plugins.py): /app/plugins/manifest.json lists the scripts and
  // styles for the signed-in user; they load before the plugins are initialised and register in window.BodyPlugins like the
  // files above. A server without plugins lists nothing and nothing is loaded.
  let pluginFiles = null;
  function loadPluginFiles() {
    pluginFiles ??= fetch("/app/plugins/manifest.json", { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {})).catch(() => ({}))
      .then((m) => {
        for (const href of m.styles || []) document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href }));
        return Promise.all((m.scripts || []).map((src) => new Promise((done) => {
          const el = Object.assign(document.createElement("script"), { src });
          el.onload = el.onerror = done;
          document.head.append(el);
        })));
      });
    return pluginFiles;
  }
  // Page events for plugins: BodyHooks.on(name, fn). The page emits "page" ({page}) when the visible page changes and "action"
  // ({feature, page}) when a control is used. feature = the control's data-feature, its id, or the name of its data-* attribute
  // (with the value only for data-tab / data-mode); never text the user typed or values of records. Nothing listens by default.
  const HOOKS = new Map();
  window.BodyHooks = {
    on(name, fn) { if (!HOOKS.has(name)) HOOKS.set(name, []); HOOKS.get(name).push(fn); },
    emit(name, detail) { for (const fn of HOOKS.get(name) || []) { try { fn(detail); } catch (e) { /* a plugin never breaks the page */ } } },
  };
  let lastPage = "";
  const pageKey = () => { const sub = { record: S.recSub, food: S.foodMode, trends: S.trendsMode, me: S.meSub }[S.tab]; return sub ? `${S.tab}:${sub}` : S.tab; };
  function featureOf(el) {
    const named = el.closest("[data-feature]")?.dataset.feature;
    if (named) return named;
    if (/^[a-z][a-z0-9_-]{0,39}$/.test(el.id || "")) return el.id.replace(/[-_]?\d+$/, "");   // tip-3 -> tip
    for (const a of el.getAttributeNames()) {
      const k = a.startsWith("data-") ? a.slice(5) : "";
      if ((k === "tab" || k === "mode") && /^[a-z_-]{1,30}$/.test(el.getAttribute(a))) return `${k}:${el.getAttribute(a)}`;
      if (/^[a-z][a-z-]{0,30}$/.test(k)) return k;
    }
    return null;
  }
  document.addEventListener("click", (e) => {
    if (!HOOKS.has("action")) return;
    const el = e.target.closest?.("[data-feature], button, a, [role=button], [data-sheet]");
    const feature = el && featureOf(el);
    if (feature) window.BodyHooks.emit("action", { feature, page: lastPage });
  }, true);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const WEEK = (() => { const w = I18N.weekdaysMon(); return [w[6], ...w.slice(0, 6)]; })();   // Sunday first (getDay())
  const WEEK_MON = I18N.weekdaysMon();   // Monday first (the training week strip)
  const ZONE = { green: "good", yellow: "mid", red: "bad" };
  const ZONE_LABEL = { green: tr("绿"), yellow: tr("黄"), red: tr("红") };
  const LEVEL = { alert: "bad", warning: "mid", info: "" };
  const CATEGORY = { supplement: tr("补剂"), diet: tr("饮食"), training: tr("训练"), lifestyle: tr("生活"), symptom: tr("症状") };
  const EVENT_CATEGORY = { symptom: tr("症状"), injury: tr("受伤 / 疼痛"), medical: tr("就医 / 体检"), posture: tr("体态"), other: tr("其他") };
  const SUPP_STATUS = { active: tr("在吃"), as_needed: tr("按需"), paused: tr("已暂停"), stopped: tr("已停"), not_needed: tr("不需要") };
  // 饮食 (food) is a segment of 记录 since the Friends tab (docs/friends-social-plan.md §3.1): S.tab "food" draws the food
  // page with the record segments on top and keeps 记录 highlighted in the tab bar
  const TABS = { today: tr("tab.today"), trends: tr("tab.trends"), friends: tr("tab.friends"), food: tr("tab.food"), record: tr("tab.record"), me: tr("tab.me") };
  const DATED = new Set(["today", "trends", "food"]);   // tabs that follow the selected date (top date opens the month calendar)
  const WIDE = window.matchMedia("(min-width: 1024px)");   // desktop wide screen: left navigation, multiple columns, bigger charts
  const LOAD_FLAGS = ["no_raw_hr", "load_missing", "day_in_progress", "hr_coverage_low", "prev_strain_missing", "muscle_load_unavailable"];

  const ICON = {
    info: `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 7.2v3.9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="4.9" r=".9" fill="currentColor"/></svg>`,
    chev: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    check: `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    pulse: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2.5-6 4 12 2.5-6H21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    // Jarvis icon: "sparkles" from Tabler Icons (MIT, https://tabler.io/icons)
    jarvis: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    spark: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
    cart: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 4.5h2l2.2 10.2a1.5 1.5 0 0 0 1.5 1.2h7.6a1.5 1.5 0 0 0 1.5-1.1l1.6-6.3H6.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="9.5" cy="19.5" r="1.3" fill="currentColor"/><circle cx="16.5" cy="19.5" r="1.3" fill="currentColor"/></svg>`,
    cal: `<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="3.5" width="11" height="10" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2.5 6.8h11M5.5 2v3M10.5 2v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>`,
    ext: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6 3.5h6.5V10M12.5 3.5L4 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  };

  // /app/?date=YYYY-MM-DD: the Today / Trends / Food pages viewing a given day (look-back; one date shared by the three tabs). Switching dates in the page changes this parameter with history.replaceState, without reloading the whole page.
  const DATE_PARAM = /^\d{4}-\d{2}-\d{2}$/.test(new URLSearchParams(location.search).get("date") || "") ? new URLSearchParams(location.search).get("date") : null;

  const S = {
    me: null, viewable: [], owner: null, tab: "today", todayDate: null,
    viewDate: DATE_PARAM,   // the date the Today / Trends / Food pages are viewing; null = today
    pickMonth: null,        // the month the date picker is viewing, YYYY-MM
    recordDay: 0, recSub: (() => { try { const v = localStorage.getItem("be.recSub"); return ["training", "food"].includes(v) ? v : "journal"; } catch (e) { return "journal"; } })(), showAll: false, trendsDays: (() => { try { const v = Number(localStorage.getItem("be.trendsDays")); return [7, 30, 90, 180, 365].includes(v) ? v : 30; } catch (e) { return 30; } })(), trendsMode: "metrics", data: {}, offlineAt: null,
    assistantUrl: null, version: null,
    backTab: "friends",     // where 「返回」 on the read-only banner goes (viewing a friend's pages)
    friendsMode: "cards",   // Friends tab: member cards / leaderboard
    board: (() => { try { return localStorage.getItem("be.board") || "body"; } catch (e) { return "body"; } })(),
    mealLogs: {},
    calMonth: null,
    foodMode: "diet", foodSub: null, foodQ: "", reportPeriod: "week",  // food page: diet / shopping, the recipes sub-page ("recipes" or null), search term  // month the training history is viewing, YYYY-MM   // meal templates logged since this open: template id -> log_id (for undo)
  };

  // ------------------------------------------------------------------ utilities
  const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const isoDay = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
  const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return isoDay(d); };
  const fmtDay = (s, withYear) => I18N.dayLabel(parseDay(s), withYear);
  const shortDay = (s) => { const d = parseDay(s); return I18N.lang === "zh" ? `${d.getMonth() + 1}/${d.getDate()}` : d.toLocaleDateString(I18N.locale(), { day: "numeric", month: "numeric" }); };
  const hm = (m) => (m == null ? null : `${Math.floor(m / 60)}:${String(Math.round(m % 60)).padStart(2, "0")}`);
  const fmtMin = (m) => {
    if (m == null) return "—";
    const h = Math.floor(m / 60), mm = Math.round(m % 60);
    return h ? tr("{h} 小时 {m} 分", { h, m: mm }) : tr("{n} 分钟", { n: mm });
  };
  const fmtNum = (v, d = 0) => (v == null ? "—" : I18N.num(v, d));
  const viewDay = () => S.viewDate || S.todayDate;          // which day the dated tabs show (Trends: the end of the range)
  const isPast = () => !!S.viewDate && S.viewDate !== S.todayDate;
  const todayPath = (day = viewDay()) => `/v1/users/${ownerPath()}/today${day && day !== S.todayDate ? `?date=${day}` : ""}`;
  const todayKey = (day = viewDay()) => `today${day && day !== S.todayDate ? day : ""}`;
  const ownerPath = () => (S.owner === S.me?.id ? "me" : String(S.owner));
  const isSelf = () => S.owner === S.me?.id;
  // The page Jarvis is opened from (#101): the tab, plus its sub-page where it has one. Same ids as the chat request's `page` (#99).
  const pageId = () => ({ trends: `trends.${S.trendsMode}`, record: `record.${S.recSub}`,
    food: S.foodMode === "shopping" ? "food.shopping" : S.foodSub === "recipes" ? "food.recipes" : "food.diet" })[S.tab] || S.tab;
  const dash = () => `<span class="dash">—</span>`;
  // The UI only shows numbers and short words; the backend's original reasons / explanations are left for the butler (read via API / MCP), here they are mapped to one- or two-character labels
  // The regexes match the server's Chinese reasons (and English ones, should a reason come back in English)
  const SHORT = [
    [/授权|permission|forbidden|not allowed/i, "无权限"], [/还没有计算|还没计算|没算出来|not (yet )?(computed|calculated)/i, "未计算"],   // i18n-ok
    [/基线|baseline/i, "基线不足"], [/HRV/i, "无 HRV"], [/原始心率|心率|heart rate/i, "无心率"], [/睡眠|sleep/i, "无睡眠"],   // i18n-ok
    [/力量训练|strength/i, "无力量"], [/负荷|沿用|strain|load/i, "无负荷"],   // i18n-ok
    [/进行中|还没结束|in progress|not over/i, "进行中"], [/连不上|超时|timeout|offline|connect/i, "离线"],   // i18n-ok
  ].map(([re, label]) => [re, tr(label)]);
  const short = (text, fallback = tr("无数据")) => { const t = String(text || ""); const hit = SHORT.find(([re]) => re.test(t)); return hit ? hit[1] : fallback; };
  const kv = (k, v, reason) => `<div class="kv"><span class="k">${esc(k)}</span>${v != null ? `<span class="v">${v}</span>` : `<span class="v r">${esc(reason ? short(reason) : "—")}</span>`}</div>`;
  const badge = (cls, text) => `<span class="badge ${cls}">${esc(text)}</span>`;
  const tags = (list) => (list.length ? `<div class="rcard-tags" style="margin-top:12px">${list.join("")}</div>` : "");

  class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }

  async function api(path, { method = "GET", body, quiet401 } = {}) {
    const headers = { Accept: "application/json", ...I18N.headers() };   // X-Body-Lang: server texts in the page language
    if (method !== "GET") headers["X-Body-Web"] = "1";
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetch(path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new ApiError(0, tr("err.offline"));
    }
    if (r.headers.get("X-Offline") === "1") { S.offlineAt = r.headers.get("X-Cached-At") || ""; showBanner(); }
    else if (method === "GET" && S.offlineAt !== null) { S.offlineAt = null; showBanner(); }
    let data = null;
    if (r.status !== 204) { try { data = await r.json(); } catch (e) { /* no body */ } }
    if (r.status === 401 && !quiet401) {
      showLogin(/过期|expired/i.test(String(data?.detail || "")) ? tr("login.expired") : "");   // i18n-ok: server detail
      throw new ApiError(401, tr("err.notLoggedIn"));
    }
    if (!r.ok) {
      const d = data?.detail;
      throw new ApiError(r.status, typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : tr("err.request", { status: r.status }));
    }
    return data;
  }

  function toast(text, actionLabel, action) {
    const el = $("#toast");
    el.innerHTML = `<span>${esc(text)}</span>` + (actionLabel ? `<button type="button">${esc(actionLabel)}</button>` : "");
    el.hidden = false;
    if (actionLabel) $("button", el).onclick = () => { el.hidden = true; action(); };
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { el.hidden = true; }, actionLabel ? 5000 : 2200);
  }

  function showBanner() {
    const el = $("#banner");
    if (S.offlineAt === null) { el.hidden = true; return; }
    const when = S.offlineAt ? new Date(S.offlineAt) : null;
    el.textContent = when && !isNaN(when)
      ? tr("banner.offlineAt", { when: when.toLocaleString(I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) })
      : tr("banner.offline");
    el.hidden = false;
  }

  // ------------------------------------------------------------------ sheets (all explanations and details live here)
  const SH = new Map();
  let shSeq = 0;
  const sheetAttr = (fn) => { const id = `s${++shSeq}`; SH.set(id, fn); return `data-sheet="${id}"`; };
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-sheet]");
    if (!t) return;
    const fn = SH.get(t.dataset.sheet);
    if (fn) { e.preventDefault(); fn(); }
  });

  let sheetTimer = 0;  // closeSheet's hide timer: cancelled when another sheet opens right away (close one, open the next)
  function openSheet(title, html, after) {
    const sh = $("#sheet");
    clearTimeout(sheetTimer);
    $("#sheet-title").textContent = title;
    $("#sheet-body").innerHTML = html;
    sh.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => sh.classList.add("open")));
    after?.($("#sheet-body"));
  }
  function closeSheet() {
    closeAltPreview();
    const sh = $("#sheet");
    sh.classList.remove("open");
    sheetTimer = setTimeout(() => { if (!sh.classList.contains("open")) sh.hidden = true; }, 240);
  }
  $("#sheet-close").onclick = closeSheet;
  $("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
  document.addEventListener("keydown", (e) => { if (e.key !== "Escape") return; if (!$("#sheet").hidden) closeSheet(); else closeHandbook(); });

  // ------------------------------------------------------------------ login
  function showLogin(msg) {
    $("#login").hidden = false;
    $("#app").hidden = true;
    $("#login-error").textContent = msg || "";
    $("#login-app").hidden = !IN_APP;
    if (window.BodyAccount) window.BodyAccount.loginShown(msg);   // step flow (e-mail → code → invite), account.js
    else setTimeout(() => $("#token").focus(), 50);
  }

  // What the login box got: a 16-character activation code (activate.html link, reusable), a 12-character one-time login code
  // (dashes / spaces ignored, alphabet without 0 O 1 I), or else an API token.
  function loginKind(v) {
    const c = String(v || "").replace(/[\s-]/g, "").toUpperCase();
    if (/^[A-HJ-NP-Z2-9]{16}$/.test(c)) return { kind: "activation", code: c };
    if (/^[A-HJ-NP-Z2-9]{12}$/.test(c)) return { kind: "code", code: c };
    return { kind: "token", code: String(v || "").trim() };
  }
  const LOGIN_401 = { activation: tr("login.badActivation"), code: tr("login.badCode"), token: tr("login.badToken") };
  // Redeem what was typed; an activation code is swapped for a fresh one-time login code first (POST /v1/web/activate
  // answers a watch2whoop://setup link whose `login` parameter is that code). A 401 error carries .kind = the step that failed.
  async function redeemLogin(v) {
    let { kind, code } = loginKind(v);
    const step = async (k, fn) => { try { return await fn(); } catch (err) { err.kind = k; throw err; } };
    if (kind === "activation") {
      const res = await step("activation", () => api("/v1/web/activate", { method: "POST", body: { code, web: true }, quiet401: true }));
      const m = /[?&]login=([^&]+)/.exec(res?.setup_link || "");
      if (!m) { const err = new Error(LOGIN_401.code); err.status = 401; err.kind = "code"; throw err; }
      kind = "code"; code = decodeURIComponent(m[1]);
    }
    if (kind === "code") await step("code", () => api("/v1/web/login-code", { method: "POST", body: { code }, quiet401: true }));
    else await step("token", () => api("/v1/web/login", { method: "POST", body: { token: code }, quiet401: true }));
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#login-btn");
    btn.disabled = true;
    $("#login-error").textContent = "";
    try {
      await redeemLogin($("#token").value);
      $("#token").value = "";
      await boot();
    } catch (err) {
      $("#login-error").textContent = err.status === 401 ? LOGIN_401[err.kind] || err.message : err.message;
    } finally { btn.disabled = false; }
  });

  async function logout() {
    try { await api("/v1/web/logout", { method: "POST" }); } catch (e) { /* clear local state even when offline */ }
    try { if (window.caches) await caches.delete("be-data"); } catch (e) { /* no Cache API */ }
    S.data = {}; S.me = null; pluginFiles = null;
    window.BodyAI?.reset();
    $("#view").innerHTML = ""; $("#view").dataset.shown = "";
    closeSheet();
    showLogin("");
  }

  // ------------------------------------------------------------------ page switching
  // The Friends page is behind the feature flag `friends` (docs/rollout.md). Off: the old tabs (饮食 is its own tab, no
  // 朋友 tab, 记录 has 日记 | 训练). On: 朋友 replaces 饮食 in the tab bar and 饮食 becomes the third segment of 记录.
  const friendsOn = () => (S.flags || []).includes("friends");
  function applyTabs() {
    const on = friendsOn();
    $('.tab[data-tab="friends"]').hidden = !on;
    $('.tab[data-tab="food"]').hidden = on;
  }
  function setTab(tab) {
    if (tab === "friends" && !friendsOn()) tab = "today";
    if (tab === "record" && S.recSub === "food" && friendsOn()) tab = "food";
    if (tab === "friends" && S.me) S.owner = S.me.id;   // the Friends tab is always about me and my circles
    if (tab !== S.tab) S.meSub = null;   // 「设置」 is a sub-page of 我: leaving the tab closes it
    S.tab = tab;
    const lit = tab === "food" && friendsOn() ? "record" : tab;
    for (const b of $$(".tab")) b.setAttribute("aria-current", b.dataset.tab === lit ? "page" : "false");
    try { localStorage.setItem("be.tab", tab); } catch (e) { /* private mode */ }
    window.scrollTo(0, 0);
    render();
  }
  for (const b of $$(".tab")) b.onclick = () => setTab(b.dataset.tab);

  function header() {
    const who = S.viewable.find((v) => v.id === S.owner);
    const onToday = S.tab === "today", dated = DATED.has(S.tab), day = dated ? viewDay() : S.todayDate;
    const past = dated && isPast();
    $("#hdr-title").textContent = !onToday || !past ? TABS[S.tab] : day === addDays(S.todayDate, -1) ? tr("hdr.yesterday") : I18N.monthDay(parseDay(day));
    const otherYear = !!day && day.slice(0, 4) !== String(S.todayDate || "").slice(0, 4);
    $("#hdr-date").innerHTML = (day ? esc(fmtDay(day, otherYear)) : "") + (who && !who.self ? ` · ${esc(who.display_name)}` : "")
      + (dated ? `<span class="pick-ic">${ICON.cal}</span>` : "");
    const pick = $("#hdr-pick");
    pick.classList.toggle("pickable", dated);
    if (dated) { pick.setAttribute("role", "button"); pick.tabIndex = 0; pick.setAttribute("aria-label", tr("hdr.pickDate")); }
    else { pick.removeAttribute("role"); pick.removeAttribute("tabindex"); pick.removeAttribute("aria-label"); }
    $("#daynav").hidden = !dated;
    $("#day-next").disabled = !past;
    $("#back-today").hidden = !past;
    const btn = $("#who");
    btn.hidden = S.viewable.length < 2;
    btn.textContent = (who?.display_name || "?").slice(0, 1).toUpperCase();
    btn.classList.toggle("other", !!who && !who.self);
    const ro = $("#viewing"), other = !!who && !who.self;
    $(".dock").classList.toggle("viewing-other", other);   // no AI chat on someone else's pages
    ro.hidden = !other || S.tab === "friends";
    if (other) {
      ro.innerHTML = `<span>${esc(tr("正在查看：{name}（只读）", { name: who.display_name }))}</span><button type="button" id="viewing-back">${esc(tr("返回"))}</button>`;
      $("#viewing-back").onclick = () => { S.owner = S.me.id; setTab(S.backTab || "friends"); };
    }
    window.BodySync?.header(onToday && !past && !!who?.self);   // 「同步中… / 已更新 07:42」 (sync.js)
  }
  $("#who").onclick = () => whoSheet();

  // ---- Look-back: switch the date of the dated tabs (in-SPA state + replaceState, no full reload); `tab` jumps to that tab as well
  function setViewDate(day, dir, tab) {
    if (!day || !S.todayDate) return;
    if (day > S.todayDate) day = S.todayDate;
    S.viewDate = day === S.todayDate ? null : day;
    const q = new URLSearchParams(location.search);
    if (S.viewDate) q.set("date", S.viewDate); else q.delete("date");
    history.replaceState(null, "", location.pathname + (q.toString() ? `?${q}` : "") + location.hash);
    if (tab && tab !== S.tab) { setTab(tab); return; }
    if (!DATED.has(S.tab)) { setTab("today"); return; }
    const view = $("#view");
    view.dataset.shown = "";   // a different date = a different page: don't keep the scroll position
    view.dataset.slide = dir > 0 ? "l" : dir < 0 ? "r" : "";
    window.scrollTo(0, 0);
    render();
  }
  $("#day-prev").onclick = () => setViewDate(addDays(viewDay(), -1), -1);
  $("#day-next").onclick = () => setViewDate(addDays(viewDay(), 1), 1);
  $("#back-today").onclick = () => setViewDate(S.todayDate, 1);
  $("#hdr-pick").addEventListener("click", () => { if (DATED.has(S.tab)) pickerSheet(); });
  $("#hdr-pick").addEventListener("keydown", (e) => { if (DATED.has(S.tab) && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pickerSheet(); } });
  // Swipe left / right on the dated tabs: swipe right for the previous day, swipe left for the next day (swipes starting on horizontally scrollable strips and charts don't count)
  (() => {
    let x0 = null, y0 = null;
    const view = $("#view");
    view.addEventListener("touchstart", (e) => {
      x0 = DATED.has(S.tab) && !e.target.closest(".quick, .rec-row, .chart, .seg, input") ? e.touches[0].clientX : null;
      y0 = e.touches[0].clientY;
    }, { passive: true });
    view.addEventListener("touchend", (e) => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = null;
      if (Math.abs(dx) < 70 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
      if (dx > 0) setViewDate(addDays(viewDay(), -1), -1);
      else if (isPast()) setViewDate(addDays(viewDay(), 1), 1);
    }, { passive: true });
  })();

  // ---- Date picker: month calendar (/days?month= marks days with data, coloured by recovery score), page between months or jump straight to a year and month
  const ZONE_CLS = { green: "good", yellow: "mid", red: "bad" };
  async function pickerSheet() {
    S.pickMonth = monthOf(viewDay());
    openSheet(tr("picker.title"), `<div id="pick-body"><div class="empty faint">…</div></div>`);
    await drawPicker(0);
  }
  async function drawPicker(dir, jump) {
    const root = $("#pick-body");
    if (!root) return;
    const ym = S.pickMonth;
    let data = null;
    try { data = await load(`days${ym}`, `/v1/users/${ownerPath()}/days?month=${ym}`); }
    catch (e) { data = null; }
    if (S.pickMonth !== ym || !$("#pick-body")) return;
    const first = data?.first || S.pickFirst || null;
    if (data?.first) S.pickFirst = data.first;
    const firstYm = first ? monthOf(first) : null, todayYm = monthOf(S.todayDate);
    const [y, m] = ym.split("-").map(Number);
    const nav = `<div class="month-nav"><button class="m pick-title" id="pk-title" aria-expanded="${!!jump}">${esc(I18N.yearMonth(y, m))}<span class="chev">${ICON.chev}</span></button><div class="arrows">
        <button id="pk-prev" aria-label="${tr("picker.prevMonth")}" ${firstYm && ym <= firstYm ? "disabled" : ""}><svg viewBox="0 0 16 16"><path d="M10 3.5L5.5 8 10 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <button id="pk-next" aria-label="${tr("picker.nextMonth")}" ${ym >= todayYm ? "disabled" : ""}><svg viewBox="0 0 16 16"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div></div>`;
    let body;
    if (jump) {   // Jump to year / month: one row of years (the earliest year with data -> this year), 12 months below
      const y0 = Number((first || S.todayDate).slice(0, 4)), y1 = Number(S.todayDate.slice(0, 4));
      const years = Array.from({ length: y1 - y0 + 1 }, (_, k) => y1 - k);
      body = `<div class="pk-years">${years.map((yy) => `<button data-pk-year="${yy}" aria-pressed="${yy === y}">${yy}</button>`).join("")}</div>
        <div class="pk-months">${Array.from({ length: 12 }, (_, k) => {
          const v = `${y}-${String(k + 1).padStart(2, "0")}`, off = v > todayYm || (firstYm && v < firstYm);
          return `<button data-pk-month="${v}" aria-pressed="${v === ym}" ${off ? "disabled" : ""}>${esc(I18N.monthShort(k + 1))}</button>`;
        }).join("")}</div>`;
    } else {
      const by = Object.fromEntries((data?.days || []).map((d) => [d.date, d]));
      const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7, daysIn = new Date(y, m, 0).getDate();
      let cells = I18N.weekdaysMon().map((w) => `<div class="cal-wd">${w}</div>`).join("") + `<div></div>`.repeat(lead);
      for (let k = 1; k <= daysIn; k++) {
        const iso = `${ym}-${String(k).padStart(2, "0")}`, d = by[iso];
        const future = iso > S.todayDate, has = !data || !!d?.has_data;   // don't block selection when the endpoint can't be read
        const off = future || (!has && iso !== S.todayDate);
        const dot = d?.zone ? `<i class="pz ${ZONE_CLS[d.zone]}"></i>` : d?.has_data ? `<i class="pz"></i>` : `<i class="pz none"></i>`;
        cells += `<button class="pday ${iso === S.todayDate ? "today" : ""} ${iso === viewDay() ? "sel" : ""} ${off ? "off" : ""}" data-pk-day="${iso}" ${off ? "disabled" : ""}
            aria-label="${esc(I18N.monthDay(new Date(y, m - 1, k)))}${d?.recovery != null ? tr("picker.recovery", { n: d.recovery }) : ""}"><span class="n">${k}</span>${dot}</button>`;
      }
      body = `<div class="cal-grid pk-grid ${dir > 0 ? "slide-l" : dir < 0 ? "slide-r" : ""}">${cells}</div>
        <div class="pk-legend"><span><i class="pz good"></i>≥67</span><span><i class="pz mid"></i>34–66</span><span><i class="pz bad"></i>&lt;34</span><span><i class="pz"></i>${tr("picker.hasData")}</span></div>`;
    }
    root.innerHTML = nav + body + (isPast() ? `<div class="btns"><button class="btn btn-primary" id="pk-today">${tr("common.backToday")}</button></div>` : "");
    $("#pk-title", root).onclick = () => drawPicker(0, !jump);
    const step = (n) => { const v = addMonths(S.pickMonth, n); if (v > todayYm || (firstYm && v < firstYm)) return; S.pickMonth = v; drawPicker(n); };
    $("#pk-prev", root).onclick = () => step(-1);
    $("#pk-next", root).onclick = () => step(1);
    for (const b of $$("[data-pk-year]", root)) b.onclick = () => {
      let v = `${b.dataset.pkYear}-${S.pickMonth.slice(5)}`;
      if (v > todayYm) v = todayYm; if (firstYm && v < firstYm) v = firstYm;
      S.pickMonth = v; drawPicker(0, true);
    };
    for (const b of $$("[data-pk-month]", root)) b.onclick = () => { S.pickMonth = b.dataset.pkMonth; drawPicker(0); };
    for (const b of $$("[data-pk-day]", root)) b.onclick = () => { closeSheet(); const d = b.dataset.pkDay; setViewDate(d, d < viewDay() ? -1 : 1); };
    const tb = $("#pk-today", root);
    if (tb) tb.onclick = () => { closeSheet(); setViewDate(S.todayDate, 1); };
    // Swipe left / right in the sheet to change month
    root.ontouchstart = (e) => { root._x = e.touches[0].clientX; root._y = e.touches[0].clientY; };
    root.ontouchend = (e) => {
      if (root._x == null || jump) return;
      const dx = e.changedTouches[0].clientX - root._x, dy = e.changedTouches[0].clientY - root._y;
      root._x = null;
      if (Math.abs(dx) > 60 && Math.abs(dy) < Math.abs(dx)) step(dx < 0 ? 1 : -1);
    };
  }

  function whoSheet() {
    openSheet(tr("who.title"), `<div class="group">${S.viewable.map((v) => `<button class="item" data-owner="${v.id}">
        <span class="row-title">${esc(v.self ? tr("who.me", { name: v.display_name }) : v.display_name)}</span>${v.id === S.owner ? `<span class="check">${ICON.check}</span>` : ""}</button>`).join("")}</div>`,
    (root) => { for (const b of $$("[data-owner]", root)) b.onclick = () => { S.owner = Number(b.dataset.owner); closeSheet(); render(); }; });
  }

  async function load(key, path) {
    const k = `${key}:${S.owner}`;
    if (!S.data[k]) S.data[k] = await api(path);
    return S.data[k];
  }

  let renderSeq = 0;
  async function render() {
    const seq = ++renderSeq;
    const view = $("#view");
    header();
    if (pageKey() !== lastPage) { lastPage = pageKey(); window.BodyHooks.emit("page", { page: lastPage }); }
    const shown = `${S.tab}:${S.owner}`, same = view.dataset.shown === shown;  // reloading the same page: keep content and scroll position
    if (!same) view.innerHTML = `<div class="empty faint">…</div>`;
    try {
      SH.clear();
      const html = await RENDER[S.tab]();
      if (seq !== renderSeq) return;
      const y = window.scrollY;
      view.innerHTML = html;
      view.dataset.shown = shown;
      if (view.dataset.slide) setTimeout(() => { view.dataset.slide = ""; }, 400);
      if (same) window.scrollTo(0, y);
      AFTER[S.tab]?.(view);
    } catch (err) {
      if (seq !== renderSeq || err.status === 401) return;
      view.dataset.shown = "";
      view.innerHTML = `<div class="empty">${esc(err.message)}<div class="btns"><button class="btn" id="retry">${tr("common.retry")}</button></div></div>`;
      $("#retry").onclick = () => render();
    }
  }

  async function refresh() { S.data = {}; await boot(true); }

  // Block pinch-zoom on iOS (Safari ignores user-scalable=no in some modes): gesture events + two-finger moves
  for (const ev of ["gesturestart", "gesturechange", "gestureend"]) document.addEventListener(ev, (e) => e.preventDefault(), { passive: false });
  // The non-passive touchmove listener exists only while two or more fingers are down, so one-finger pans never wait on the page.
  // (Sideways strips such as .quick: the real blocker in issue #11 was the :active opacity on their div.card, see app.css.)
  const noPinch = (e) => { if (e.touches.length > 1) e.preventDefault(); };
  document.addEventListener("touchstart", (e) => { if (e.touches.length > 1) document.addEventListener("touchmove", noPinch, { passive: false }); }, { passive: true });
  for (const ev of ["touchend", "touchcancel"]) document.addEventListener(ev, (e) => { if (e.touches.length < 2) document.removeEventListener("touchmove", noPinch); }, { passive: true });

  // Pull to refresh (a home-screen app has no built-in one from the browser)
  (() => {
    let y0 = null, dy = 0;
    const ptr = $("#ptr");
    window.addEventListener("touchstart", (e) => { y0 = window.scrollY <= 0 && !$("#app").hidden && $("#sheet").hidden ? e.touches[0].clientY : null; dy = 0; }, { passive: true });
    window.addEventListener("touchmove", (e) => {
      if (y0 === null) return;
      dy = e.touches[0].clientY - y0;
      ptr.style.height = `${Math.max(0, Math.min(44, dy / 2.2))}px`;
    }, { passive: true });
    window.addEventListener("touchend", async () => {
      if (y0 === null) return;
      y0 = null;
      if (dy > 80) {
        window.BodySync?.refresh("pull");   // also ask the server for fresh data (sync.js polls and reloads when it is in)
        ptr.classList.add("spin"); ptr.style.height = "30px"; try { await refresh(); } finally { ptr.classList.remove("spin"); ptr.style.height = "0"; }
      }
      else ptr.style.height = "0";
    });
  })();

  // ------------------------------------------------------------------ explanations (/explain: what an item is, how to read it, advice from the owner's own numbers)
  // The main screens stay numbers and short labels; the words live only here, in sheets opened on tap. Advice is rule-based on the backend;
  // "Ask AI" hands ai_prompt to the viewer's own AI (in-page chat with their own key, or their butler link), never anyone else's.
  const xpPath = (kind, key) => `/v1/users/${ownerPath()}/explain/${kind}/${encodeURIComponent(key)}${DATED.has(S.tab) && isPast() ? `?date=${viewDay()}` : ""}`;
  const xpUnit = (c) => (c.unit ? `<small>${esc(c.unit)}</small>` : "");
  const xpList = (items, cls = "") => `<ul class="xp-list${cls ? ` xp-l-${cls}` : ""}">${items.map((t) => `<li>${t}</li>`).join("")}</ul>`;
  const xpHead = (t) => `<div class="xp-h">${esc(t)}</div>`;
  function canAskAI() { return isSelf() && (window.BodyAI?.handlesAsk() || !!S.assistantUrl || S.ai?.mode === "mcp"); }
  async function askAI(prompt) {
    if (window.BodyAI?.handlesAsk()) { closeSheet(); window.BodyAI.openChat(prompt); return; }
    try { await navigator.clipboard.writeText(prompt); } catch (e) { /* clipboard not allowed: the link still opens */ }
    if (S.assistantUrl) { window.open(S.assistantUrl, "_blank", "noopener"); toast(tr("问题已复制")); }
    else toast(tr("问题已复制 · 去你的 AI app 粘贴"));
  }

  function explainHtml(x, head) {
    const c = x.current, ex = x.exercises;
    const avoid = [...(x.avoid || []).map(esc), ...(ex?.banned || []).map((b) => `<b>${esc(b.name)}</b>${b.reason ? ` <span class="muted">· ${esc(b.reason)}</span>` : ""} ${badge("bad", tr("禁用"))}`)];
    const caution = (ex?.caution || []).map((b) => `<b>${esc(b.name)}</b>${b.cues ? ` <span class="muted">· ${esc(b.cues)}</span>` : ""}`);
    return `${head && c ? `<div class="xp-cur"><span class="xp-v">${esc(typeof c.value === "number" ? fmtNum(c.value, 1) : c.value)}${xpUnit(c)}</span>
          ${badge(c.tone || "plain", c.basis || "")}</div>` : ""}
      ${x.facts?.length ? `<div class="xp-facts">${x.facts.map((f) => kv(f.label, esc(f.value))).join("")}</div>` : ""}
      ${x.missing && !c ? tags([badge("plain", short(x.missing))]) : ""}
      ${x.advice?.length ? `${xpHead(tr("建议"))}<div class="xp-adv">${x.advice.map((a) => `<div class="xp-a xp-t-${esc(a.tone || "plain")}"><i></i><span>${esc(a.text)}</span></div>`).join("")}</div>` : ""}
      ${xpHead(tr("是什么"))}<p class="para xp-p">${esc(x.what)}</p>
      ${x.read?.length ? `${xpHead(tr("怎么看"))}<div class="xp-read">${x.read.map((r) => `<div class="kv"><span class="k">${esc(r.range)}</span><span class="v">${badge(r.tone, r.label)}</span></div>`).join("")}</div>` : ""}
      ${x.recovery ? `${xpHead(x.key === "recovery" ? tr("怎么用") : tr("和恢复"))}<p class="para xp-p">${esc(x.recovery)}</p>` : ""}
      ${x.tips?.length ? `${xpHead(tr("注意"))}${xpList(x.tips.map(esc))}` : ""}
      ${avoid.length ? `${xpHead(tr("避免"))}${xpList(avoid, "bad")}` : ""}
      ${caution.length ? `${xpHead(tr("小心做"))}${xpList(caution, "mid")}` : ""}
      ${x.profile?.length ? `${xpHead(tr("你的档案"))}${xpList(x.profile.map(esc), "quote")}` : ""}
      ${canAskAI() ? `<div class="btns"><button class="btn" data-xp-ask>${ICON.spark}<span>${tr("问 AI")}</span></button></div>` : ""}`;
  }

  // Fill a placeholder inside an open sheet; head = show the big current value (off when the sheet already shows it)
  async function explainInto(root, kind, key, head) {
    const box = $(".xp", root);
    if (!box) return;
    const ck = `xp:${S.owner}:${kind}:${key}:${DATED.has(S.tab) ? viewDay() : ""}`;
    let x = S.data[ck];
    if (!x) {
      try { x = S.data[ck] = await api(xpPath(kind, key)); }
      catch (e) { if (box.isConnected) box.innerHTML = tags([badge("plain", e.status === 403 ? tr("无权限") : short(e.message))]); return; }
    }
    if (!box.isConnected) return;   // the sheet was closed or replaced while loading
    box.innerHTML = explainHtml(x, head);
    const ask = $("[data-xp-ask]", box);
    if (ask) ask.onclick = () => askAI(x.ai_prompt);
  }
  const xpBox = `<div class="xp"><div class="xp-load"><span></span><span></span><span></span></div></div>`;
  function explainSheet(kind, key, title) { openSheet(title, xpBox, (root) => explainInto(root, kind, key, true)); }
  // A kv row that opens the explanation of one metric
  const xkv = (k, v, reason, key) => `<button class="kv kv-btn" data-feature="explain" ${sheetAttr(() => explainSheet("metric", key, k))}><span class="k">${esc(k)}<i class="xp-i">${ICON.info}</i></span>${v != null ? `<span class="v">${v}</span>` : `<span class="v r">${esc(reason ? short(reason) : "—")}</span>`}</button>`;

  // ------------------------------------------------------------------ today
  function ring(value, zone) {
    const r = 84, c = 2 * Math.PI * r, frac = value == null ? 0 : Math.max(0, Math.min(100, value)) / 100;
    return `<svg class="ring" viewBox="0 0 196 196" role="img" aria-label="${tr("恢复分 {v}", { v: value ?? "—" })}">
      <circle class="track" cx="98" cy="98" r="${r}" fill="none" stroke-width="12"/>
      ${value == null ? "" : `<circle class="arc stroke-${ZONE[zone]}" cx="98" cy="98" r="${r}" fill="none" stroke-width="12" stroke-linecap="round"
        stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${c.toFixed(1)}" data-target="${(c * (1 - frac)).toFixed(1)}" transform="rotate(-90 98 98)"/>`}
      <text class="val" x="98" y="114" text-anchor="middle">${value == null ? "—" : value}</text>
      <text class="cap" x="98" y="140" text-anchor="middle">${tr("恢复")}</text></svg>`;
  }

  // flags (data problems of the scores) -> short labels; the original text stays in the endpoint for the butler
  const FLAG_SHORT = {
    no_sleep: tr("无睡眠"), hrv_missing: tr("无 HRV"), baseline_insufficient: tr("基线不足"), no_raw_hr: tr("无心率"), load_missing: tr("无负荷"),
    day_in_progress: tr("进行中"), hr_coverage_low: tr("心率不全"), prev_strain_missing: tr("缺前一天"), muscle_load_unavailable: tr("无力量"),
  };
  const flagTags = (flags, skip = []) => tags(Object.keys(flags || {}).filter((c) => !skip.includes(c) && FLAG_SHORT[c]).map((c) => badge("plain", FLAG_SHORT[c])));

  // Level bar: a few colour segments + a tick for the current value (used for recovery score and TSB)
  function scaleBar(v, lo, hi, cuts, classes, labels) {
    const pos = (x) => ((Math.max(lo, Math.min(hi, x)) - lo) / (hi - lo)) * 100;
    const edges = [lo, ...cuts, hi];
    return `<div class="scale">${classes.map((c, i) => `<span class="${c}" style="left:${pos(edges[i]).toFixed(1)}%;width:${(pos(edges[i + 1]) - pos(edges[i])).toFixed(1)}%"></span>`).join("")}
        ${v != null ? `<i style="left:${pos(v).toFixed(1)}%"></i>` : ""}</div>
      <div class="scale-l">${labels.map(([x, t]) => `<span style="left:${pos(x).toFixed(1)}%">${esc(t)}</span>`).join("")}</div>`;
  }
  const recoveryScale = (v) => scaleBar(v, 0, 100, [34, 67], ["bad", "mid", "good"], [[0, "0"], [34, "34"], [67, "67"], [100, "100"]]);

  function recoverySheet(sc) {
    const t = sc.today || {}, r = sc.today_reasons, last = sc.last_recovery;
    openSheet(tr("恢复"), t.recovery == null ? `${recoveryScale(last?.recovery)}
      ${kv(tr("恢复分"), null, r.recovery)}
      ${last ? kv(tr("上次"), `${last.recovery} ${badge(ZONE[last.zone], ZONE_LABEL[last.zone])} · ${shortDay(last.date)}`) : ""}
      ${xkv("HRV", null, r.hrv_night_ms, "hrv")}${xkv(tr("静息心率"), null, r.rhr_night, "resting_hr")}${xpBox}` : `${recoveryScale(t.recovery)}
      ${kv(tr("恢复分"), `${t.recovery} ${badge(ZONE[sc.recovery_zone], ZONE_LABEL[sc.recovery_zone])}`)}
      ${xkv("HRV", t.hrv_night_ms != null ? `${fmtNum(t.hrv_night_ms)} <small class="muted">ms</small>` : null, r.hrv_night_ms, "hrv")}
      ${xkv(tr("静息心率"), t.rhr_night != null ? `${fmtNum(t.rhr_night)} <small class="muted">bpm</small>` : null, r.rhr_night, "resting_hr")}
      ${flagTags(t.flags, LOAD_FLAGS)}${xpBox}`, (root) => explainInto(root, "metric", "recovery", false));
  }

  function sleepSheet(sc) {
    const t = sc.today || {}, r = sc.today_reasons, ls = sc.last_sleep;
    const missing = t.sleep_score == null;
    const src = missing ? (ls || {}) : t;
    openSheet(missing && ls ? `${tr("睡眠")} · ${shortDay(ls.date)}` : tr("睡眠"), `
      ${missing && !ls ? kv(tr("睡眠分"), null, r.sleep_score) : ""}
      ${!missing || ls ? `
        ${kv(tr("睡眠分"), src.sleep_score ?? null)}
        ${xkv(tr("实际"), esc(hm(src.sleep_actual_min) ?? "—"), null, "sleep_hours")}
        ${kv(tr("需求"), esc(hm(src.sleep_need_min) ?? "—"))}
        ${xkv(tr("睡眠债"), esc(hm(src.sleep_debt_min) ?? "—"), null, "sleep_debt")}
        ${!missing && t.sleep_performance != null ? kv(tr("表现"), `${Math.round(t.sleep_performance * 100)}%`) : ""}` : ""}
      <div class="btns"><button class="btn" id="to-sleep">${tr("14 晚")}</button></div>${xpBox}`,
    (root) => { $("#to-sleep", root).onclick = () => { closeSheet(); S.trendsMode = "sleep"; setTab("trends"); }; explainInto(root, "metric", "sleep_score", false); });
  }

  function strainSheet(sc) {
    const y = sc.yesterday || {}, r = sc.yesterday_reasons;
    openSheet(`${tr("负荷")}${sc.yesterday ? ` · ${shortDay(sc.yesterday.date)}` : ""}`, `
      ${scaleBar(y.strain, 0, 21, [10, 14, 18], ["plain", "low", "mid2", "hi"], [[0, "0"], [10, "10"], [14, "14"], [18, "18"], [21, "21"]])}
      ${kv("strain", y.strain != null ? fmtNum(y.strain, 1) : null, r.strain)}
      ${kv(tr("肌肉负荷"), y.muscle_load != null ? `${fmtNum(y.muscle_load)} <small class="muted">kg</small>` : null, r.muscle_load)}
      ${flagTags(y.flags)}${xpBox}`, (root) => explainInto(root, "metric", "strain", false));
  }

  function loadSheet(sc) {
    const y = sc.yesterday || {};
    openSheet(tr("训练负荷"), `
      ${scaleBar(y.tsb, -45, 25, [-30, -10, 10], ["bad", "mid", "plain", "good"], [[-30, "−30"], [-10, "−10"], [10, "+10"]])}
      ${kv(tr("状态"), sc.load_status ? esc(sc.load_status.label) : null)}
      ${kv(tr("CTL 体能"), y.ctl != null ? fmtNum(y.ctl) : null)}
      ${kv(tr("ATL 疲劳"), y.atl != null ? fmtNum(y.atl) : null)}
      ${kv("TSB", y.tsb != null ? (y.tsb > 0 ? "+" : "") + fmtNum(y.tsb) : null)}
      ${sc.load_missing_days ? tags([badge("mid", tr("缺 {n} 天", { n: sc.load_missing_days }))]) : ""}`);
  }

  function scoresHtml(sc) {
    if (sc.error) return `<div class="hero">${ring(null)}<div class="hero-word muted">${esc(short(sc.error))}</div></div>`;
    const t = sc.today || {}, y = sc.yesterday || {};
    const word = sc.recovery_word ? `<div class="hero-word">${esc(sc.recovery_word)}</div>` : "";
    const sub = t.recovery == null && sc.last_recovery ? `<div class="hero-sub">${tr("上次")} ${sc.last_recovery.recovery} · ${shortDay(sc.last_recovery.date)}</div>` : "";
    const tile = (label, value, fn) => `<button class="tile" ${sheetAttr(fn)}><span class="tile-label">${label}</span>
      <span class="tile-value">${value != null ? value : dash()}</span></button>`;
    return `<button class="hero" ${sheetAttr(() => recoverySheet(sc))}>${ring(t.recovery, sc.recovery_zone)}${word}${sub}</button>
      <div class="tiles">
        ${tile(tr("睡眠"), hm(t.sleep_actual_min), () => sleepSheet(sc))}
        ${tile(tr("睡眠分"), t.sleep_score, () => sleepSheet(sc))}
        ${tile(isPast() ? tr("前一天负荷") : tr("昨天负荷"), y.strain != null ? fmtNum(y.strain, 1) : null, () => strainSheet(sc))}
      </div>
      ${sc.load_status ? `<div class="chip-row"><button class="chip" ${sheetAttr(() => loadSheet(sc))}>${tr("负荷")} <b>${esc(sc.load_status.label)}</b></button></div>` : ""}`;
  }

  // ---- Training + nutrition cards
  // Recommended working weight (training/loads.py): progression up = good, estimate = muted "估算", otherwise plain
  const loadBasisText = (ld) => {
    const key = ld.basis === "estimate" ? ld.reason : ld.basis, date = ld.last ? shortDay(ld.last.date) : "";
    switch (key) {
      case "progression": return tr("按进阶规则");
      case "history": return tr("同上次（{date}）", { date });
      case "e1rm": return tr("按上次（{date}）换算", { date });
      case "plan": return tr("计划里写的重量");
      case "bodyweight": return tr("自重动作");
      case "no_history": return tr("按体重 × 系数估算：这个动作还没有记录");
      case "not_linked": return tr("按体重 × 系数估算：还没连训练记录");
      case "history_unreadable": return tr("按体重 × 系数估算：训练记录读不到");
      case "no_body_weight": return tr("缺体重：记一次体重后可估算");
      default: return null;
    }
  };

  function loadBadge(ld) {
    if (ld?.reason === "no_body_weight") return `<span class="small muted">${esc(tr("缺体重"))}</span>`;
    if (!ld || (ld.kg == null && ld.basis !== "bodyweight")) return "";
    if (ld.kg == null) return badge("plain", ld.label);
    const kg = `${fmtNum(ld.kg, 1)} kg`;
    if (ld.basis === "estimate") return `${badge("plain", `≈ ${kg}`)}<span class="small muted">${tr("估算")}</span>`;
    const tone = ld.readiness === "red" || ld.direction === "down" ? "mid" : ld.basis === "progression" && ld.direction === "up" ? "good" : "plain";
    return badge(tone, ld.basis === "progression" && ld.direction === "up" ? `→ ${kg}` : kg);
  }

  function exerciseRow(x) {
    const st = x.status === "banned" ? badge("bad", tr("禁用")) : x.status === "caution" ? badge("mid", tr("注意")) : "";
    const sug = x.load?.basis === "progression" ? "" : (x.suggestions || []).map((s) => badge(s.level === "warning" ? "mid" : "good", s.next_weight_kg != null ? `→ ${s.next_weight_kg} kg` : tr("建议"))).join(" ");
    const ref = x.slug || x.name || "";
    return `<div class="ex ${x.status === "banned" ? "banned" : ""}" data-ex-ref="${esc(ref)}" style="cursor:pointer"><div class="ex-name"><span>${esc(x.name)}</span>${st}${sug}</div>
      ${x.sets_text || x.load ? `<div class="ex-meta">${esc(x.sets_text || "")}${x.status !== "banned" ? ` ${loadBadge(x.load)}` : ""}</div>` : ""}</div>`;
  }

  function bindExRows(root) {
    for (const el of $$("[data-ex-ref]", root)) {
      if (el.dataset.exRef) el.onclick = () => exerciseDetailSheet(el.dataset.exRef);
    }
  }

  function trainingSheet(d) {
    const t = d.training;
    let html = "";
    if (t.error) html += kv(tr("训练"), null, t.error);
    else {
      if (t.readiness?.advice && t.readiness.tier !== "unknown") {
        const tone = t.readiness.tier === "green" ? "good" : t.readiness.tier === "red" ? "bad" : "mid";
        html += `<div class="rcard-tags" style="margin-bottom:6px">${badge(tone, t.readiness.label || tr("恢复自调节"))}</div><div class="small muted" style="margin-bottom:10px">${esc(t.readiness.advice)}</div>`;
      }
      if (t.sparky_error && t.planned.length) html += tags([badge("mid", tr("训练记录读不到 · 重量为估算"))]);
      if (t.change) html += kv(tr("原计划"), esc(t.change.from?.join(" · ") || tr("休息")));
      html += t.planned.length ? t.planned.map((p) => {
        const single = p.exercises.length === 1 && p.exercises[0].name === p.name;
        return `${single ? "" : `<div class="small muted" style="margin:14px 0 2px">${esc(p.name)}</div>`}${p.exercises.map(exerciseRow).join("")}`;
      }).join("") : `<div class="empty faint" style="padding:12px">${t.rest_day ? tr("休息") : tr("无计划")}</div>`;
      if (t.logged.length) html += `<div class="cat">${tr("已记录")}</div>` + t.logged.map((s) => s.exercises.map(exerciseRow).join("")).join("");
    }
    openSheet(`${tr("训练")} · ${shortDay(d.date)}`, html, bindExRows);
  }

  function nutritionSheet(d) {
    const n = d.nutrition;
    if (n.error) { openSheet(tr("饮食"), kv(tr("饮食"), null, n.error)); return; }
    const g = n.goal || {}, i = n.intake;
    openSheet(`${tr("饮食")} · ${shortDay(d.date)}`, kv(tr("热量"), `${fmtNum(i.calories)} / ${fmtNum(g.calories)} kcal`) + kv(tr("蛋白质"), `${fmtNum(i.protein_g)} / ${fmtNum(g.protein_g)} g`)
      + kv(tr("碳水"), `${fmtNum(i.carbs_g)} / ${fmtNum(g.carbs_g)} g`) + kv(tr("脂肪"), `${fmtNum(i.fat_g)} / ${fmtNum(g.fat_g)} g`)
      + (n.entries.length ? `<div class="cat">${tr("{n} 项", { n: n.entries.length })}</div><div class="group">${n.entries.map((e) => `<button class="item food-entry" ${sheetAttr(() => foodEntrySheet(e))}><span class="f">${esc([e.time, e.food].filter(Boolean).join(" ") || "—")}</span>
          <span class="k">${e.calories != null ? `${fmtNum(e.calories)}<small> kcal</small>` : "—"}</span><span class="chev">${ICON.chev}</span></button>`).join("")}</div>` : ""));
  }

  function dayCardHtml(d, meals) {
    const t = d.training, n = d.nutrition;
    if (t.code === "forbidden" && n.code === "forbidden") return "";
    let title = tr("训练"), sub = "";
    if (t.error) sub = badge("plain", short(t.error, "—"));
    else if (!t.planned.length && t.logged?.length) {   // No plan but trained that day (common when looking back): the title says what was done
      title = t.logged.map((x) => x.name).filter(Boolean).join(" · ") || tr("训练");
      sub = badge("plain", tr("{n} 动作", { n: t.logged.reduce((a, x) => a + x.exercises.length, 0) }));
    } else if (!t.planned.length) title = t.rest_day ? tr("休息日") : tr("无计划");
    else {
      title = t.planned.map((p) => p.name).join(" · ");
      const ex = t.planned.flatMap((p) => p.exercises);
      const caution = ex.filter((x) => x.status !== "active").length, up = ex.filter((x) => x.suggestions?.length).length;
      sub = [badge("plain", tr("{n} 动作", { n: ex.length })), caution ? badge("mid", tr("注意 {n}", { n: caution })) : "", up ? badge("good", tr("加重 {n}", { n: up })) : ""].join("");
    }
    if (t.change) sub += badge("mid", tr("已调整"));
    if (t.logged?.length) sub += badge("good", tr("已练"));
    let food = "";
    if (!n.error) {
      const g = n.goal || {}, i = n.intake;
      const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
      const m = (label, a, b) => `<div><div class="macro-l"><span>${label}</span><span>${fmtNum(a)}/${fmtNum(b)}</span></div><div class="bar"><span style="width:${pct(a, b).toFixed(0)}%"></span></div></div>`;
      food = `<div class="card"><button class="food-main" ${sheetAttr(() => nutritionSheet(d))}>
          <div class="kcal" style="margin-top:0"><span class="row-sub">${tr("饮食")}${n.logged ? "" : ` ${badge("plain", tr("未记录"))}`}</span><span class="v">${fmtNum(i.calories)}<span class="small muted"> / ${fmtNum(g.calories)} kcal</span></span></div>
          <div class="bar ${g.calories && i.calories > g.calories * 1.05 ? "over" : ""}"><span style="width:${pct(i.calories, g.calories).toFixed(0)}%"></span></div>
          <div class="macros">${m(tr("蛋白"), i.protein_g, g.protein_g)}${m(tr("碳水"), i.carbs_g, g.carbs_g)}${m(tr("脂肪"), i.fat_g, g.fat_g)}</div></button>
        ${isSelf() && !isPast() ? quickHtml(meals) : ""}</div>`;
    }
    const calLink = t.code === "forbidden" ? "" : `<button class="link link-ic" id="to-cal">${ICON.cal}${tr("训练历史")}</button>`;
    return `<div class="section"><div class="section-head"><h2>${tr("训练与饮食")}</h2>${calLink}</div>
      <button class="card" ${sheetAttr(() => trainingSheet(d))}>
        <div class="row"><span class="row-title">${esc(title)}</span><span class="chev">${ICON.chev}</span></div>
        ${sub ? `<div class="rcard-tags" style="margin-top:8px">${sub}</div>` : ""}</button>${food}</div>`;
  }

  // ---- Sleep card: bedtime / wake time, stages, stage chart (the layered timeline like the Apple Watch sleep page)
  const HYP_LANES = ["awake", "rem", "core", "deep"];
  const HYP_NAME = { awake: tr("清醒"), rem: "REM", core: tr("核心"), deep: tr("深睡"), unspecified: tr("睡着") };

  function hypnoSvg(m, stages) {
    const W = 320, L = 30, laneH = 12, gap = 9, T = 2, total = Math.max(1, m.in_bed_min);
    const H = T + HYP_LANES.length * (laneH + gap) - gap + 16;
    const sx = (min) => L + (min / total) * (W - L);
    const lane = (st) => Math.max(0, HYP_LANES.indexOf(st === "unspecified" ? "core" : st));
    const y = (i) => T + i * (laneH + gap);
    let g = HYP_LANES.map((st, i) => `<line class="lane" x1="${L}" x2="${W}" y1="${(y(i) + laneH / 2).toFixed(1)}" y2="${(y(i) + laneH / 2).toFixed(1)}"/>
      <text x="0" y="${(y(i) + laneH - 2).toFixed(1)}">${HYP_NAME[st]}</text>`).join("");
    stages.forEach((s, k) => {
      if (k > 0) {
        const prev = stages[k - 1], x = sx(s.offset_min);
        if (lane(prev.stage) !== lane(s.stage)) {
          const a = y(lane(prev.stage)) + laneH / 2, b = y(lane(s.stage)) + laneH / 2;
          g += `<line class="join" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${a.toFixed(1)}" y2="${b.toFixed(1)}"/>`;
        }
      }
      g += `<rect class="seg hy-${s.stage}" x="${sx(s.offset_min).toFixed(1)}" y="${y(lane(s.stage))}" width="${Math.max(1.2, sx(s.offset_min + s.minutes) - sx(s.offset_min)).toFixed(1)}" height="${laneH}" rx="3" style="animation-delay:${Math.min(600, k * 14)}ms"><title>${HYP_NAME[s.stage]} ${s.start}–${s.end}</title></rect>`;
    });
    g += `<text x="${L}" y="${H - 2}">${m.start}</text><text x="${W}" y="${H - 2}" text-anchor="end">${m.end}</text>`;
    return `<svg class="hyp" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(tr("睡眠阶段图 {a} 到 {b}", { a: m.start, b: m.end }))}">${g}</svg>`;
  }

  function sleepDetailSheet(sl) {
    const m = sl.main;
    openSheet(`${tr("睡眠")} · ${shortDay(sl.date)}`, `
      ${kv(tr("入睡 → 醒来"), `${m.start} → ${m.end}`)}${kv(tr("在床"), hm(m.in_bed_min))}${kv(tr("睡着"), hm(m.asleep_min))}
      ${kv(tr("深睡"), hm(m.deep_min))}${kv(tr("核心"), hm(m.core_min))}${kv("REM", hm(m.rem_min))}${kv(tr("清醒"), hm(m.awake_min))}
      ${kv(tr("阶段来源"), sl.stages_source === "ow" ? "OW" : sl.stages_source === "shortcut" ? tr("快捷指令") : sl.stages_source === "export" ? tr("导入的历史") : null)}
      <div class="btns"><button class="btn" id="to-sleep">${tr("14 晚")}</button></div>`,
    (root) => { $("#to-sleep", root).onclick = () => { closeSheet(); S.trendsMode = "sleep"; setTab("trends"); }; });
  }

  function sleepCardHtml(sl) {
    if (!sl || sl.code === "forbidden") return "";
    const head = `<div class="section-head"><h2>${tr("睡眠")}</h2>${sl.latest && sl.main ? badge("plain", shortDay(sl.date)) : ""}</div>`;
    if (sl.error || !sl.main) {
      return `<div class="section">${head}<div class="card row"><span class="dash">—</span><span class="small faint">${esc(short(sl.error || sl.reason))}</span></div></div>`;
    }
    const m = sl.main;
    const stat = (st, v) => `<div><div class="stage-l"><i class="k-${st}"></i>${HYP_NAME[st]}</div><div class="stage-v">${hm(v) ?? "—"}</div></div>`;
    const chart = sl.stages.length ? hypnoSvg(m, sl.stages) : "";
    return `<div class="section">${head}<button class="card" ${sheetAttr(() => sleepDetailSheet(sl))}>
      <div class="sleep-head"><div><div class="sub-l">${tr("入睡 → 醒来")}</div><div class="sleep-times">${m.start}<span class="arrow">→</span>${m.end}</div></div>
        <div class="sleep-total"><div class="sub-l">${tr("睡着")}</div><div class="v">${hm(m.asleep_min)}</div></div></div>
      ${chart}
      <div class="stages4">${stat("deep", m.deep_min)}${stat("core", m.core_min)}${stat("rem", m.rem_min)}${stat("awake", m.awake_min)}</div>
    </button></div>`;
  }

  // ---- Bedtime goal (GET / PUT /v1/users/{u}/goals/sleep from the rules session; when the endpoint is missing the last 7 nights are still shown)
  const PACES = [[tr("温和"), 10], [tr("标准"), 15], [tr("积极"), 20]];   // minutes earlier per step
  const offset18 = (s) => { if (!s) return null; const [h, m] = s.split(":").map(Number); const t = h * 60 + m; return t < 18 * 60 ? t + 6 * 60 : t - 18 * 60; };
  const fromOffset = (o) => minToHm(((Math.round(o) + 18 * 60) % 1440 + 1440) % 1440);
  const ON_TARGET_SLACK = 15;   // within 15 minutes after the target counts as on target (only used for dot colour; the count of on-target nights comes from the endpoint)

  function targetFor(goal, night) {
    const plan = (goal?.plan || []).filter((p) => p.from_date <= night).sort((a, b) => (a.from_date < b.from_date ? -1 : 1));
    return plan.length ? plan[plan.length - 1].bedtime : null;
  }

  function bedChart(recent, goal, today) {
    const W = 320, H = 86, T = 8, B = 16, n = 7;
    const rn = Array.isArray(goal?.recent_nights) && goal.recent_nights.length ? goal.recent_nights : null;
    // If recent_nights exists use the server's 7 nights (sleep day = bedtime − 12h); otherwise take the last 7 nights counting back from today, using recent from the today endpoint
    const lastNight = rn ? rn.map((r) => r.night).sort().slice(-1)[0] : addDays(today, -1);
    const nights = Array.from({ length: n }, (_, k) => addDays(lastNight, k - n + 1));
    const by = rn ? Object.fromEntries(rn.map((r) => [r.night, { ...r, bed_offset_min: offset18(r.bedtime) }]))
      : Object.fromEntries((recent || []).map((r) => [r.night, r]));
    const vals = nights.map((nt) => by[nt]?.bed_offset_min ?? null);
    const tg = nights.map((nt) => offset18((rn && by[nt]?.target) || targetFor(goal, nt)));
    const tonight = tg.every((v) => v == null) ? offset18(goal?.tonight_target_bedtime) : null;   // All these nights are before the plan started: draw tonight's target as a reference
    const all = [...vals, ...tg, tonight].filter((v) => v != null);   // The final goal is only written in the title, not on the vertical axis (too far away would squash the dots)
    if (!all.length) return "";
    const lo = Math.min(...all) - 20, hi = Math.max(...all) + 20;
    const sx = (i) => 12 + (i / (n - 1)) * (W - 24), sy = (v) => T + ((v - lo) / (hi - lo || 1)) * (H - T - B);   // the later, the lower
    let g = "", d = "";
    tg.forEach((v, i) => { if (v == null) return; d += `${d ? "L" : "M"}${(sx(i) - 10).toFixed(1)},${sy(v).toFixed(1)}L${(sx(i) + 10).toFixed(1)},${sy(v).toFixed(1)}`; });
    if (d) g += `<path class="tgt" d="${d}"/>`;
    if (tonight != null) g += `<path class="tgt" d="M4,${sy(tonight).toFixed(1)}H${W - 4}"/><text class="tgt-l" x="${W - 4}" y="${(sy(tonight) - 4).toFixed(1)}" text-anchor="end">${esc(tr("今晚 {t}", { t: goal.tonight_target_bedtime }))}</text>`;
    vals.forEach((v, i) => {
      g += `<text x="${sx(i).toFixed(1)}" y="${H - 2}" text-anchor="middle">${WEEK[parseDay(nights[i]).getDay()]}</text>`;
      if (v == null) return;
      const ok = by[nights[i]]?.on_target ?? (tg[i] != null && v <= tg[i] + ON_TARGET_SLACK);
      g += `<circle class="${ok ? "ok" : "miss"}" cx="${sx(i).toFixed(1)}" cy="${sy(v).toFixed(1)}" r="4"><title>${by[nights[i]].bedtime}</title></circle>`;
    });
    return `<svg class="bedchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("最近 7 晚入睡时间和目标")}">${g}</svg>`;
  }

  function goalSheet(goal, recent, coach) {
    const g = goal && !goal.error ? goal : null, p = g?.progress || {};
    const pace = g?.pace_min_per_step ?? 15;
    const avg = p.avg_bedtime_7d || (recent?.length ? fromOffset(recent.reduce((s, r) => s + r.bed_offset_min, 0) / recent.length) : null);
    openSheet(tr("入睡目标"), `
      ${g?.target_bedtime ? kv(tr("今晚"), esc([g.tonight_target_bedtime, coach?.wake_time || g.tonight_target_wake].filter(Boolean).join(" → ") || "—")) : ""}
      ${p.behind_plan_min > 0 ? kv(tr("比计划晚"), tr("{n} 分钟", { n: p.behind_plan_min })) : ""}
      ${coach?.need_min ? kv(tr("需要睡"), `${hm(coach.need_min)}${coach.sleep_debt_min ? ` <small class="muted">· ${tr("债")} ${coach.sleep_debt_min}′</small>` : ""}`) : ""}
      ${kv(tr("7 晚平均入睡"), avg ? esc(avg) : null)}${p.avg_wake_7d ? kv(tr("平均醒来"), esc(p.avg_wake_7d)) : ""}
      ${p.on_target_nights_7d != null ? kv(tr("达标"), tr("{a} / {b} 晚", { a: p.on_target_nights_7d, b: p.nights_7d || 7 })) : ""}
      ${p.eta_date ? kv(tr("预计达成"), shortDay(p.eta_date)) : ""}
      <div class="pair field"><div><label for="g-bed">${tr("期望入睡")}</label><input class="input time-input" id="g-bed" type="time" value="${esc(g?.target_bedtime || "00:00")}"></div>
        <div><label for="g-wake">${tr("期望醒来")}</label><input class="input time-input" id="g-wake" type="time" value="${esc(g?.target_wake || "")}"></div></div>
      <div class="field"><label>${tr("节奏")}</label><div class="sev" id="g-pace">${PACES.map(([t, v]) => `<button type="button" data-pace="${v}" aria-pressed="${v === pace}">${t}</button>`).join("")}</div></div>
      <div class="btns"><button class="btn btn-primary" id="g-save">${tr("保存")}</button></div>`,
    (root) => {
      let chosen = pace;
      for (const b of $$("[data-pace]", root)) b.onclick = () => { chosen = Number(b.dataset.pace); for (const x of $$("[data-pace]", root)) x.setAttribute("aria-pressed", String(Number(x.dataset.pace) === chosen)); };
      $("#g-save", root).onclick = async () => {
        const body = { target_bedtime: $("#g-bed", root).value, pace_min_per_step: chosen };
        if ($("#g-wake", root).value) body.target_wake = $("#g-wake", root).value;
        if (!body.target_bedtime) { toast(tr("选一个入睡时间")); return; }
        try {
          await api(`/v1/users/me/goals/sleep`, { method: "PUT", body });
          delete S.data[`goal:${S.owner}`];
          closeSheet(); render(); toast(tr("目标已更新"));
        } catch (err) { toast(err.status === 404 ? tr("入睡目标接口还没上线") : err.message); }
      };
    });
  }

  function goalCardHtml(goal, sl, coach) {
    if (goal?.error?.status === 403 || sl?.code === "forbidden") return "";
    const g = goal && !goal.error ? goal : null, recent = sl?.recent || [];
    const self = isSelf() && !isPast();   // Looking back at another day: view only, don't change the goal
    const when = isPast() ? tr("当晚") : tr("今晚");
    // Look-back: when that night had no plan yet, don't apply the current goal to it
    const planned = !isPast() || (g?.plan || []).some((p) => p.from_date <= viewDay());
    const big = planned && g?.target_bedtime
      ? `<div class="goal-big">${when} ${esc(g.tonight_target_bedtime || g.target_bedtime)}<small>${tr("最终")} ${esc(g.target_bedtime)}</small></div>`
      : planned && coach?.recommended_bedtime ? `<div class="goal-big">${when} ${esc(coach.recommended_bedtime)}</div>`
      : `<div class="goal-big" style="font-size:17px">${self ? tr("设置目标") : "—"}</div>`;
    // Sleep coach (sleep-coach): when a bedtime goal exists the recommendation is the goal (source = goal), here we only add "how long to sleep, what time to get up"
    if (!planned && !recent.length) return "";
    const coachLine = planned && coach?.need_min
      ? `<div class="coach-line">${tr("需要")} <b>${hm(coach.need_min)}</b>${coach.wake_time ? ` · ${esc(tr("{t} 起", { t: coach.wake_time }))}` : ""}${coach.shortfall_min > 0 ? ` · ${tr("差 {t}", { t: hm(coach.shortfall_min) })}` : ""}</div>` : "";
    const on = g?.progress?.on_target_nights_7d, n7 = g?.progress?.nights_7d;
    const sub = planned && on != null ? tr("达标 {a}/{b}", { a: on, b: n7 || 7 }) : recent.length ? tr("{n}/7 晚", { n: recent.length }) : "";
    return `<div class="section"><div class="section-head"><h2>${planned ? tr("入睡目标") : tr("入睡")}</h2>${sub ? `<span class="small muted">${esc(sub)}</span>` : ""}</div>
      <button class="card" ${self ? sheetAttr(() => goalSheet(goal, recent, coach)) : ""}>
        <div class="row">${big}${self ? `<span class="chev">${ICON.chev}</span>` : ""}</div>${coachLine}
        ${bedChart(recent, g, viewDay())}</button></div>`;
  }

  // ---- Meal templates (/meal-templates from the body butler session). One component everywhere (Today food card, Record, Food):
  // a sideways strip of the most used templates (uses_30d), then 「全部」 for a sheet with all of them. Tap = log (已记 ✓ + toast 撤销);
  // tapping a logged one only offers 撤销 again; templates whose grams need confirming open their detail sheet first.
  const QUICK_MAX = 5;
  const mealKcal = (t) => t.totals?.kcal ?? t.totals?.calories ?? (t.items || []).reduce((s, x) => s + (x.kcal || 0), 0);
  const mealsByUse = (meals) => meals.map((t, i) => [t, i]).sort((a, b) => (b[0].uses_30d || 0) - (a[0].uses_30d || 0) || a[1] - b[1]).map(([t]) => t);
  const mealSub = (t) => (loggedId(t) ? tr("已记 ✓") : t.needs_confirmation ? tr("待确认") : `${fmtNum(mealKcal(t))} kcal`);
  function quickHtml(meals, bare) {
    if (!Array.isArray(meals) || !meals.length) return "";
    const top = mealsByUse(meals).slice(0, QUICK_MAX);
    return `<div class="quick ${bare ? "bare" : ""}">${top.map((t) => `<button class="qchip ${t.needs_confirmation && !loggedId(t) ? "warn" : ""} ${loggedId(t) ? "done" : ""}" data-meal="${esc(t.id)}">
        <span class="n">${esc(t.name)}</span><span class="k">${mealSub(t)}</span></button>`).join("")}${meals.length > QUICK_MAX
      ? `<button class="qchip more" data-meal-all><span class="n">${tr("全部")}</span><span class="k">${meals.length}</span></button>` : ""}</div>`;
  }
  function allMealsSheet() {
    const meals = mealsByUse(S.data[`meals:${S.owner}`] || []);
    openSheet(tr("快捷记录"), meals.map((t) => `<div class="item-row meal-row">
        <button class="f" data-log="${esc(t.id)}">${esc(t.name)}<small class="${loggedId(t) ? "" : t.needs_confirmation ? "mid" : ""}">${mealSub(t)}</small></button>
        <button class="btn" data-edit="${esc(t.id)}">${tr("克数")}</button></div>`).join(""),
    (root) => {
      for (const b of $$("[data-log]", root)) b.onclick = () => { const m = mealOf(b.dataset.log); closeSheet(); if (m) tapMeal(m); };
      for (const b of $$("[data-edit]", root)) b.onclick = () => { const m = mealOf(b.dataset.edit); if (m) mealSheet(m); };
    });
  }
  const loggedId = (t) => S.mealLogs[t.id] ?? t.today_log?.log_id ?? null;   // logged during this open, or the server remembers it was already logged today
  const mealOf = (id) => (S.data[`meals:${S.owner}`] || []).find((t) => String(t.id) === String(id));

  async function logMeal(t, items, again) {
    try {
      const body = items ? { items } : {};
      if (again) body.allow_duplicates = true;
      const res = await api(`/v1/users/me/meal-templates/${encodeURIComponent(t.id)}/log`, { method: "POST", body });
      S.mealLogs[t.id] = res?.log_id;
      delete S.data[`today:${S.owner}`]; delete S.data[`mealplan${S.todayDate}:${S.owner}`];
      render();
      toast(tr("已记 · {name}", { name: t.name }), tr("撤销"), () => undoMeal(t));
    } catch (err) {
      if (err.status === 409) toast(tr("今天已记过"), tr("仍要记"), () => logMeal(t, items, true));
      else toast(err.status === 404 ? tr("暂不可用") : err.message);
    }
  }
  async function undoMeal(t) {
    const id = loggedId(t);
    if (!id) return;
    try {
      await api(`/v1/users/me/meal-logs/${encodeURIComponent(id)}`, { method: "DELETE" });
      delete S.mealLogs[t.id];
      if (t.today_log) t.today_log = null;
      delete S.data[`today:${S.owner}`]; delete S.data[`mealplan${S.todayDate}:${S.owner}`];
      render();
      toast(tr("已撤销 · {name}", { name: t.name }));
    } catch (err) { toast(err.message); }
  }

  function mealSheet(t) {
    const items = t.items || [];
    openSheet(t.name, `${t.needs_confirmation ? `<div class="rcard-tags" style="margin-bottom:8px">${badge("mid", tr("克数待确认"))}</div>` : ""}
      ${items.map((x, i) => `<div class="item-row"><div class="f">${esc(x.food)}<small>${[x.kcal != null && `${fmtNum(x.kcal)} kcal`, x.protein_g != null && `${tr("蛋白")} ${fmtNum(x.protein_g, 1)} g`].filter(Boolean).join(" · ")}</small></div>
        <input class="input" type="number" inputmode="decimal" min="0" step="any" data-i="${i}" value="${x.grams ?? ""}"><span class="u">g</span></div>`).join("")}
      <div class="btns">${t.needs_confirmation ? `<button class="btn" id="m-ask">Jarvis</button>` : ""}<button class="btn btn-primary" id="m-log">${tr("记录")}</button></div>`,
    (root) => {
      const ask = $("#m-ask", root);
      if (ask) ask.onclick = (e) => { closeSheet(); openAssistant(e); };
      $("#m-log", root).onclick = () => {
        const edited = items.map((x, i) => ({ ...x, grams: Number($(`[data-i="${i}"]`, root).value) }));
        const changed = edited.some((x, i) => x.grams !== items[i].grams);
        if (edited.some((x) => !(x.grams >= 0))) { toast(tr("克数要不小于 0")); return; }
        closeSheet();
        logMeal(t, changed ? edited.map(({ food, grams }) => ({ food, grams })) : null);
      };
    });
  }

  function tapMeal(m) {
    if (loggedId(m)) toast(tr("已记 · {name}", { name: m.name }), tr("撤销"), () => undoMeal(m));   // already logged: no second log by accident, undo stays one tap away
    else if (m.needs_confirmation) mealSheet(m);
    else logMeal(m);
  }
  function bindMeals(view) {
    for (const b of $$("[data-meal]", view)) b.onclick = () => { const m = mealOf(b.dataset.meal); if (m) tapMeal(m); };
    for (const b of $$("[data-meal-all]", view)) b.onclick = allMealsSheet;
  }

  // ---- Reminders
  function reminderSheet(rm) {
    const self = isSelf();
    const actions = !self ? "" : rm.status === "new"
      ? `<button class="btn" data-act="seen">${tr("已看")}</button><button class="btn" data-act="dismissed">${tr("不再提醒")}</button>`
      : `<button class="btn" data-act="new">${tr("标为未读")}</button><button class="btn" data-act="dismissed">${tr("不再提醒")}</button>`;
    // The reminder body (message) is an explanation for the butler; here only the title, level and date are kept
    const lv = { alert: ["bad", tr("重要")], warning: ["mid", tr("注意")], info: ["plain", tr("提示")] }[rm.level] || ["plain", tr("提示")];
    openSheet(tr("提醒"), `<div class="rem-title">${esc(remTitle(rm.title))}</div>${tags([badge(lv[0], lv[1]), rm.date ? badge("plain", shortDay(String(rm.date).slice(0, 10))) : ""].filter(Boolean))}
      ${actions ? `<div class="btns">${actions}</div>` : ""}`,
      (root) => { for (const b of $$("[data-act]", root)) b.onclick = () => { closeSheet(); setReminder(rm.id, b.dataset.act); }; });
  }

  const remTitle = (t) => t.replace(/\b\d{4}-(\d{2})-(\d{2})\b/g, (_, m, d) => `${Number(m)}/${Number(d)}`);
  const reminderRow = (rm) => `<button class="card rem ${rm.status === "seen" ? "seen" : ""}" ${sheetAttr(() => reminderSheet(rm))}>
      <span class="dot ${LEVEL[rm.level] || ""}"></span><span class="row-title">${esc(remTitle(rm.title))}</span><span class="chev">${ICON.chev}</span></button>`;

  function allRemindersSheet(list) { openSheet(`${tr("提醒")} · ${list.length}`, list.map(reminderRow).join("")); }

  function remindersHtml(list) {
    if (list?.error) return list.code === "forbidden" ? "" : `<div class="section"><div class="section-head"><h2>${tr("提醒")}</h2></div><div class="card row"><span class="dash">—</span><span class="small faint">${esc(short(list.error))}</span></div></div>`;
    if (!list.length) return "";
    const top = [...list.filter((r) => r.status === "new"), ...list.filter((r) => r.status !== "new")].slice(0, 3);
    return `<div class="section"><div class="section-head"><h2>${tr("提醒")}</h2>
        ${list.length > 3 ? `<button class="link" ${sheetAttr(() => allRemindersSheet(list))}>${tr("common.all", { n: list.length })}</button>` : ""}</div>
      ${top.map(reminderRow).join("")}</div>`;
  }

  async function setReminder(id, status, quiet) {
    const d = S.data[`${todayKey()}:${S.owner}`];
    const list = d?.reminders;
    const rm = Array.isArray(list) && list.find((r) => r.id === id);
    const prev = rm ? rm.status : null;
    try { await api(`/v1/users/me/reminders/${id}`, { method: "PATCH", body: { status } }); }
    catch (err) { toast(err.message); return; }
    if (rm) {
      rm.status = status;
      if (status === "dismissed") { d.reminders = list.filter((r) => r.id !== id); d._dismissed = { ...(d._dismissed || {}), [id]: rm }; }
    } else if (status !== "dismissed" && d?._dismissed?.[id]) {
      d.reminders = [...d.reminders, { ...d._dismissed[id], status }].sort((a, b) => a.id - b.id);
    }
    if (S.tab === "today") render();
    if (!quiet && status === "dismissed") toast(tr("不再提醒"), tr("撤销"), () => setReminder(id, prev || "new", true));
  }

  const RENDER_today = async () => {
    const day = viewDay(), past = isPast();
    const [d, goal, meals, coach, stress, monitor, sleepCoach, tday] = await Promise.all([
      load(todayKey(), todayPath()),
      (past ? load(`goal${day}`, `/v1/users/${ownerPath()}/goals/sleep?night=${day}`) : load("goal", `/v1/users/${ownerPath()}/goals/sleep`)).catch((e) => ({ error: e })),
      isSelf() && !past ? load("meals", `/v1/users/${ownerPath()}/meal-templates`).catch(() => null) : null,   // meal templates only log today
      optional(`coach${day}`, WHOOP_API.strainCoach(day)), optional(`stress${day}`, WHOOP_API.stress(day)),
      optional(`monitor${day}`, WHOOP_API.monitor(day)), optional(`sleepcoach${day}`, WHOOP_API.sleepCoach(day)),
      optional(`tday${day}`, `/v1/users/${ownerPath()}/training/day/${day}`),   // that day's activity (steps etc., Apple Health history has it too)
    ]);
    const pair = [strainCoachHtml(coach), stressHtml(stress)].filter(Boolean);
    const extras = monitorHtml(monitor) + (pair.length ? `<div class="${pair.length === 2 ? "half" : ""}" style="${pair.length === 1 ? "margin-top:8px" : ""}">${pair.join("")}</div>` : "");
    // Stacked on phones; two / three columns on wide screens (.dash in app.css)
    // Activity right after the scores, so on phones it is near the first screen
    return `<div class="dash"><div class="col col-a">${scoresHtml(d.scores)}${activityHtml(tday?.daily)}${extras}${remindersHtml(d.reminders)}</div>
      <div class="col col-b">${sleepCardHtml(d.sleep)}${goalCardHtml(goal, d.sleep, sleepCoach)}</div>
      <div class="col col-c">${dayCardHtml(d, meals)}</div></div>`;
  };

  // That day's activity: steps, active energy, exercise minutes (daily from training/day)
  function activityHtml(dl) {
    if (!dl || [dl.steps, dl.active_kcal, dl.exercise_min].every((v) => v == null)) return "";
    const t = (label, v, u) => `<div class="tile"><span class="tile-label">${label}</span><span class="tile-value">${v != null ? `${fmtNum(v)}${u ? `<small>${u}</small>` : ""}` : dash()}</span></div>`;
    return `<div class="section"><div class="section-head"><h2>${tr("活动")}</h2></div><div class="tiles" style="margin-top:0">
      ${t(tr("步数"), dl.steps)}${t(tr("活动"), dl.active_kcal, "kcal")}${t(tr("锻炼"), dl.exercise_min, tr("分"))}</div></div>`;
  }

  function afterToday(view) {
    const arc = $(".ring .arc", view);
    if (arc) requestAnimationFrame(() => requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", arc.dataset.target)));
    bindMeals(view);
    const c = $("#to-cal", view);
    if (c) c.onclick = () => { S.recSub = "training"; setTab("record"); };
  }

  // ------------------------------------------------------------------ WHOOP-style features (insights endpoints from the scores session; fields in system design §7 "Body age and other analyses")
  // Paths are collected here; when an endpoint errors / doesn't exist the corresponding card is hidden.
  const WHOOP_API = {
    healthspan: () => `/v1/users/${ownerPath()}/healthspan?weeks=26`,
    strainCoach: (d) => `/v1/users/${ownerPath()}/strain-coach?date=${d}`,
    stress: (d) => `/v1/users/${ownerPath()}/stress?start=${d}&end=${d}&hours=true`,
    monitor: (d) => `/v1/users/${ownerPath()}/health-monitor?date=${d}`,
    sleepCoach: (d) => `/v1/users/${ownerPath()}/sleep-coach?date=${d}`,
    sleepDetails: (end) => `/v1/users/${ownerPath()}/sleep/details${end ? `?end=${end}` : ""}`,
    vo2max: (end) => `/v1/users/${ownerPath()}/vo2max?days=365${end ? `&end=${end}` : ""}`,
    reports: (p, d) => `/v1/users/${ownerPath()}/reports?period=${p}&end=${d}`,
  };
  const optional = (key, path) => load(key, path).catch(() => null);   // no such endpoint = not shown
  const firstReason = (flags) => (flags && typeof flags === "object" ? Object.values(flags).find((v) => typeof v === "string") : null);

  // Today's strain target (Strain Coach): a 0–21 ring, the target range is the light arc, the current value is the solid arc
  function strainCoachHtml(c) {
    if (!c || c.target_low == null || (isPast() && c.current_strain == null)) return "";   // Look-back: if that day has no strain, don't show the target
    const r = 22, C = 2 * Math.PI * r, f = (v) => Math.max(0, Math.min(21, v || 0)) / 21;
    const zoneLen = (f(c.target_high) - f(c.target_low)) * C, zoneOff = -f(c.target_low) * C;
    const cur = c.current_strain, has = cur != null;
    const state = !has ? short(c.current_missing_reason, "—") : c.status === "in_range" ? tr("达标") : c.status === "above" ? tr("超出") : tr("差 {n}", { n: fmtNum(c.remaining_to_low ?? c.target_low - cur, 1) });
    const sheet = () => openSheet(tr("负荷目标"), `${scaleBar(cur, 0, 21, [c.target_low, c.target_high], ["plain", "good", "plain"], [[0, "0"], [c.target_low, fmtNum(c.target_low, 0)], [c.target_high, fmtNum(c.target_high, 0)], [21, "21"]])}
      ${kv(tr("区间"), `${fmtNum(c.target_low, 0)}–${fmtNum(c.target_high, 0)}`)}${kv(tr("现在"), has ? fmtNum(cur, 1) : null, c.current_missing_reason)}
      ${c.status ? tags([badge(c.status === "in_range" ? "good" : c.status === "above" ? "mid" : "plain", state)]) : ""}`);
    return `<button class="mini" ${sheetAttr(sheet)}>
      <span class="tile-label">${tr("负荷目标")}</span>
      <span class="mini-row"><svg class="coach-ring" viewBox="0 0 54 54" aria-hidden="true"><g transform="rotate(-90 27 27)">
        <circle class="track" cx="27" cy="27" r="${r}" fill="none" stroke-width="6"/>
        <circle class="zone" cx="27" cy="27" r="${r}" fill="none" stroke-width="6" stroke-dasharray="${zoneLen.toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${zoneOff.toFixed(1)}"/>
        ${has && cur > 0 ? `<circle class="cur" cx="27" cy="27" r="${r}" fill="none" stroke-width="6" stroke-linecap="round" stroke-dasharray="${(f(cur) * C).toFixed(1)} ${C.toFixed(1)}"/>` : ""}</g>
        <text x="27" y="32" text-anchor="middle">${has ? fmtNum(cur, 1) : "—"}</text></svg>
        <span class="s">${fmtNum(c.target_low, 0)}–${fmtNum(c.target_high, 0)}<br>${state}</span></span></button>`;
  }

  // Stress 0–3: that day's score + hourly curve; when it can't be computed show "—" + reason
  const STRESS_LEVEL = { low: tr("低"), medium: tr("中"), high: tr("高") };
  const STRESS_SHORT = { no_raw_hr: tr("无心率"), baseline_insufficient: tr("基线不足"), no_calm_hours: tr("无安静时段"), no_motion_data: tr("无步数") };
  function stressHtml(st) {
    const d = st?.days?.[0];
    if (!d) return "";
    const why = d.score == null;
    const reason = why ? Object.keys(d.flags || {}).map((k) => STRESS_SHORT[k]).find(Boolean) || short(firstReason(d.flags), "—") : null;
    const pts = (d.hours || []).filter((h) => h.score != null);
    let line = "";
    if (pts.length > 1) {
      const W = 140, H = 28, x = (h) => (h / 23) * W, y = (v) => H - (Math.max(0, Math.min(3, v)) / 3) * H;
      const dd = pts.map((h, i) => `${i ? "L" : "M"}${x(h.hour).toFixed(1)},${y(h.score).toFixed(1)}`).join("");
      line = `<svg class="stress-line" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="area" d="${dd}L${x(pts[pts.length - 1].hour).toFixed(1)},${H}L${x(pts[0].hour).toFixed(1)},${H}Z"/><path d="${dd}" vector-effect="non-scaling-stroke"/></svg>`;
    }
    const hrs = [[tr("高"), d.high_h, "bad"], [tr("中"), d.medium_h, "mid"], [tr("低"), d.low_h, "good"]];
    const tot = hrs.reduce((a, [, v]) => a + (v || 0), 0);
    const sheet = () => openSheet(tr("压力"), why ? kv(tr("压力"), null, firstReason(d.flags) || tr("心率")) : `${scaleBar(d.score, 0, 3, [1, 2], ["good", "mid", "bad"], [[0, "0"], [1, "1"], [2, "2"], [3, "3"]])}
      ${kv(tr("分数"), `${fmtNum(d.score, 1)} <small class="muted">/ 3</small> ${STRESS_LEVEL[d.level] ? badge(d.level === "high" ? "bad" : d.level === "medium" ? "mid" : "good", STRESS_LEVEL[d.level]) : ""}`)}
      ${tot ? `<div class="hbar">${hrs.map(([, v, c]) => `<span class="${c}" style="width:${((v || 0) / tot * 100).toFixed(1)}%"></span>`).join("")}</div>
        <div class="hbar-l">${hrs.map(([t, v]) => `<span><b>${fmtNum(v, 1)}</b> h ${t}</span>`).join("")}</div>` : ""}
      ${d.peak_hour != null ? kv(tr("峰值"), `${d.peak_hour}:00`) : ""}
      ${d.baseline?.calm_hr ? kv(tr("平静心率"), `${fmtNum(d.baseline.calm_hr)} <small class="muted">bpm</small>`) : ""}`);
    return `<button class="mini" ${sheetAttr(sheet)}>
      <span class="tile-label">${tr("压力")}</span>${why ? `<span class="v">—</span><span class="s faint">${esc(reason)}</span>`
        : `<span class="v">${fmtNum(d.score, 1)}<small class="muted" style="font-size:12px;font-weight:500"> ${STRESS_LEVEL[d.level] || "/ 3"}</small></span>${line}`}</button>`;
  }

  // Health monitor: whether the 5 vital signs are within the personal normal range (60-day baseline ± 2 SD)
  const VITAL_NAME = { rhr: tr("静息心率"), hrv: "HRV", resp_rate: tr("呼吸"), spo2: tr("血氧"), wrist_temp: tr("体温") };
  // the five-dot strip on the card has room for about four letters a name in English
  const VITAL_SHORT = I18N.lang === "zh" ? VITAL_NAME : { rhr: "RHR", hrv: "HRV", resp_rate: "Resp.", spo2: "SpO₂", wrist_temp: "Temp" };
  const VITAL_XP = { rhr: "resting_hr", hrv: "hrv" };   // vitals that have an explanation sheet
  function monitorHtml(m) {
    const list = m?.metrics || [];
    if (!list.some((x) => x.value != null)) return "";   // None at all (very early days): don't show the whole card
    const st = (x) => (x.value == null ? "missing" : x.status);
    const off = list.filter((x) => st(x) === "high" || st(x) === "low").length;   // items without data don't count as abnormal
    // Each item: value + position within the personal normal range (60 days ±2 SD)
    const range = (x) => {
      if (x.value == null || x.low == null || x.high == null) return "";
      const span = x.high - x.low || 1, lo = x.low - span * 0.5, hi = x.high + span * 0.5;
      const p = (v) => (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * 100;
      return `<div class="vrange"><span style="left:${p(x.low).toFixed(1)}%;width:${(p(x.high) - p(x.low)).toFixed(1)}%"></span><i class="${st(x) === "high" || st(x) === "low" ? "bad" : ""}" style="left:${p(x.value).toFixed(1)}%"></i></div>
        <div class="vrange-l"><span>${esc(fmtNum(x.low, 1))}</span><span>${esc(fmtNum(x.high, 1))}</span></div>`;
    };
    const name = (x) => esc(VITAL_NAME[x.key] || x.label);
    const sheet = () => openSheet(tr("健康监测"), list.map((x) => `<div class="vrow"><div class="kv" style="border:0;padding-bottom:4px">${VITAL_XP[x.key]
        ? `<button class="k xp-label" ${sheetAttr(() => explainSheet("metric", VITAL_XP[x.key], VITAL_NAME[x.key]))}>${name(x)}<i class="xp-i">${ICON.info}</i></button>` : `<span class="k">${name(x)}</span>`}<span class="v">${x.value != null ? `${esc(fmtNum(x.value, 1))}${x.unit ? ` <small class="muted">${esc(x.unit)}</small>` : ""}` : `<span class="muted" style="font-weight:400">${esc(short(x.missing_reason))}</span>`}
      ${st(x) === "high" ? badge("bad", tr("偏高")) : st(x) === "low" ? badge("bad", tr("偏低")) : ""}</span></div>${range(x)}</div>`).join(""));
    return `<button class="card" style="margin-top:8px" ${sheetAttr(sheet)}><div class="row"><span class="tile-label">${tr("健康监测")}</span>${off ? badge("bad", tr("{n} 异常", { n: off })) : badge("good", tr("正常"))}</div>
      <div class="vitals">${list.map((x) => `<span class="vital ${esc(st(x) || "")}"><i></i><span>${esc(VITAL_SHORT[x.key] || x.label)}</span></span>`).join("")}</div></button>`;
  }

  // Body age (Healthspan): top of the Me page
  const paceClass = (p) => (p == null ? "" : p < 0.95 ? "good" : p > 1.05 ? "bad" : "muted");
  const CAT_NAME = { sleep: tr("睡眠"), activity: tr("活动"), fitness: tr("体能") };
  const signed = (v, dig = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(dig)}`;   // fixed decimals: +4.0 years, −1.0×
  function historySvg(hist) {
    const xs = (hist || []).filter((h) => h.body_age != null);
    if (xs.length < 2) return "";
    const W = 320, H = 80, lo = Math.min(...xs.map((h) => h.body_age)) - 0.5, hi = Math.max(...xs.map((h) => h.body_age)) + 0.5;
    const x = (i) => (i / (xs.length - 1)) * W, y = (v) => 6 + (1 - (v - lo) / (hi - lo || 1)) * (H - 22);
    const day = (h) => shortDay(String(h.as_of || h.week || h.date).slice(0, 10));
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("身体年龄每周历史")}"><path class="avg" d="${xs.map((h, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(h.body_age).toFixed(1)}`).join("")}"/>
      <text x="0" y="${H - 3}">${day(xs[0])}</text><text x="${W}" y="${H - 3}" text-anchor="end">${day(xs[xs.length - 1])}</text></svg>`;
  }
  const contribValue = (c) => {
    if (c.value == null) return "";
    const v = c.key === "body_comp" ? `${c.measure === "waist_to_height" ? tr("腰围身高比") : "BMI"} ${fmtNum(c.value, c.measure === "waist_to_height" ? 2 : 1)}`
      : `${fmtNum(c.value, Math.abs(c.value) >= 100 || Number.isInteger(c.value) ? 0 : 1)}${c.unit ? ` ${c.unit}` : ""}`;
    return ` <span class="muted">${esc(v)}</span>`;
  };
  function healthspanSheet(h) {
    const cs = h.contributors || [], max = Math.max(0.5, ...cs.map((c) => Math.abs(c.years || 0)));
    const groups = new Map();
    for (const c of cs) { const g = c.category || "other"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(c); }
    const lack = Array.isArray(h.insufficient) ? h.insufficient : [];
    openSheet(tr("身体年龄"), `${historySvg(h.history)}
      ${kv(tr("实际年龄"), h.chrono_age != null ? fmtNum(h.chrono_age, 1) : null)}${kv(tr("衰老速度"), h.pace_of_aging != null ? `${signed(h.pace_of_aging, 1)}×` : null)}
      ${[...groups].map(([g, list]) => `<div class="cat" style="margin-top:16px">${esc(CAT_NAME[g] || g)}</div>${list.map((c) => {
        const y = c.years || 0, w = (Math.abs(y) / max) * 50;
        return `<div class="contrib"><div class="contrib-head"><span>${esc(c.label)}${contribValue(c)}</span>
          <span class="y ${y > 0 ? "bad" : y < 0 ? "good" : "muted"}">${tr("{n} 年", { n: signed(y) })}</span></div>
          <div class="dbar"><span class="${y > 0 ? "older" : "younger"}" style="width:${w.toFixed(1)}%"></span></div></div>`;
      }).join("")}`).join("")}
      ${lack.length ? `<div class="cat" style="margin-top:16px">${tr("数据不足")}</div>${tags(lack.map((x) => badge("plain", x.label)))}` : ""}`);
  }
  function healthspanHtml(h) {
    if (!h) return "";
    if (h.body_age == null) {
      return `<div class="section"><div class="card age"><div><div class="age-l">${tr("身体年龄")}</div><div class="age-v">${dash()}</div></div><span class="small faint">${esc(short(h.missing_reason, tr("数据不足")))}</span></div></div>`;
    }
    const d = h.delta_years;
    return `<div class="section"><button class="card age" ${sheetAttr(() => healthspanSheet(h))}>
      <div><div class="age-l">${tr("身体年龄")}</div><div class="age-v">${fmtNum(h.body_age, 1)}</div></div>
      <div class="age-side"><div class="x ${d > 0 ? "bad" : d < 0 ? "good" : ""}">${signed(d)}<small>${tr("岁")}</small></div>
        ${h.pace_of_aging != null ? `<div class="x ${paceClass(h.pace_of_aging)}">${signed(h.pace_of_aging, 1)}×<small>${tr("衰老速度")}</small></div>` : ""}</div></button></div>`;
  }

  // Weekly / monthly report: card stream (structured data, the text is written by the butler; here only numbers and changes)
  // [label, value getter, formatter, change getter, change formatter, direction of improvement (1 higher is better / -1 lower is better / 0 neutral)]
  const REPORT_CARDS = [
    [tr("恢复"), (c) => c.recovery?.avg, (v) => fmtNum(v), (ch) => ch.recovery?.avg, (v) => fmtNum(v), 1],
    [tr("睡眠"), (c) => c.sleep?.avg_actual_min, (v) => hm(v), (ch) => ch.sleep?.avg_actual_min, (v) => `${fmtNum(v)}′`, 1],
    [tr("入睡"), (c) => c.sleep?.avg_bedtime, (v) => v, null, null, 0],
    [tr("规律性"), (c) => c.sleep?.consistency_pct, (v) => `${fmtNum(v)}%`, (ch) => ch.sleep?.consistency_pct, (v) => fmtNum(v), 1],
    [tr("负荷"), (c) => c.load?.avg_strain, (v) => fmtNum(v, 1), (ch) => ch.load?.avg_strain, (v) => fmtNum(v, 1), 0],
    [tr("压力"), (c) => c.stress?.avg, (v) => fmtNum(v, 2), (ch) => ch.stress?.avg, (v) => fmtNum(v, 2), -1],
    [tr("训练"), (c) => c.training?.sessions, (v) => `${v}<small> ${tr("次")}</small>`, (ch) => ch.training?.sessions, (v) => fmtNum(v), 0],
    [tr("体重"), (c) => c.weight?.last_kg, (v) => `${fmtNum(v, 1)}<small> kg</small>`, (c2) => c2.__weight, (v) => `${fmtNum(v, 1)}kg`, -1],
  ];
  async function reportHtml() {
    const p = S.reportPeriod;
    const [r, ja] = await Promise.all([optional(`report-${p}${trendEnd() || ""}`, WHOOP_API.reports(p, viewDay())),
      optional(`journal-an${trendEnd() || ""}`, `/v1/users/${ownerPath()}/journal/analysis?days=90&end=${viewDay()}`)]);
    const seg = `<div class="toolbar"><div class="seg" role="group" aria-label="${tr("报告周期")}">${[["week", tr("周报")], ["month", tr("月报")]].map(([k, t]) => `<button data-rperiod="${k}" aria-pressed="${k === p}">${t}</button>`).join("")}</div></div>`;
    if (!r?.current) return seg + `<div class="empty">${tr("暂无")}</div>`;
    const cur = r.current, ch = { ...(r.change || {}), __weight: cur.weight?.change_kg };
    const prevBed = r.previous_values?.sleep?.avg_bedtime;
    const cards = REPORT_CARDS.map(([label, get, fmt, getCh, fmtCh, good]) => {
      const v = get(cur);
      if (v == null) return "";
      const dv = getCh ? getCh(ch) : null;
      const tone = dv == null || dv === 0 || !good ? "neutral" : dv * good > 0 ? "good" : "bad";
      const delta = label === tr("入睡") ? (prevBed ? `${tr("上期")} ${esc(prevBed)}` : "") : dv == null ? "" : `${dv > 0 ? "↑" : dv < 0 ? "↓" : "→"} ${fmtCh(Math.abs(dv))}`;
      return `<div class="mini rep-card"><span class="tile-label">${label}</span><span class="v">${fmt(v)}</span>${delta ? `<span class="d ${tone}">${delta}</span>` : ""}</div>`;
    }).join("");
    const finds = (r.journal_findings || []).slice(0, 3);
    return seg + `<div class="rep-head">${esc(`${shortDay(r.start)} – ${shortDay(r.end)}`)}<span class="muted"> vs ${esc(`${shortDay(r.previous.start)} – ${shortDay(r.previous.end)}`)}</span></div>
      ${finds.length ? `<div class="card"><ul class="rep-hl">${finds.map((f) => `<li class="row"><span>${esc(f.label)}</span><span class="${f.diff_recovery > 0 ? "good" : f.diff_recovery < 0 ? "bad" : "muted"}">${tr("次日恢复")} ${signed(f.diff_recovery)}</span></li>`).join("")}</ul></div><div style="height:8px"></div>` : ""}
      <div class="rep-grid">${cards}</div>${behaviorHtml(ja)}`;
  }

  // Behavior insights (WHOOP): each journal item with ≥ 5 "yes" and ≥ 5 "no" days in the last 90 days, and its link to next-day recovery
  function behaviorHtml(list) {
    if (!Array.isArray(list) || !list.length) return "";
    const ok = list.map((a) => ({ a, o: a.outcomes?.next_recovery })).filter(({ o }) => o?.status === "ok")
      .sort((x, y) => Math.abs(y.o.diff_yes_minus_no || 0) - Math.abs(x.o.diff_yes_minus_no || 0));
    const lack = list.length - ok.length;
    if (!ok.length && !lack) return "";
    const max = Math.max(5, ...ok.map(({ o }) => Math.abs(o.diff_yes_minus_no || 0)));
    const rows = ok.map(({ a, o }) => {
      const v = o.diff_yes_minus_no || 0, cls = o.direction === "better" ? "good" : o.direction === "worse" ? "bad" : "muted";
      return `<div class="contrib"><div class="contrib-head"><span>${esc(shortLabel(a.item))}</span><span class="y ${cls}">${signed(v, 0)}${o.reliable ? "" : `<span class="muted"> ?</span>`}</span></div>
        <div class="dbar"><span class="${v >= 0 ? "younger" : "older"} ${cls === "muted" ? "faint" : ""}" style="width:${(Math.abs(v) / max * 50).toFixed(1)}%"></span></div></div>`;
    }).join("");
    return `<div class="section"><div class="section-head">${xpLabel("behavior", tr("行为影响 · 次日恢复"))}<span class="small muted">${tr("{n} 天", { n: 90 })}</span></div>
      <div class="card">${rows || `<div class="empty faint" style="padding:8px">—</div>`}${lack ? tags([badge("plain", tr("{n} 项样本不足", { n: lack }))]) : ""}</div></div>`;
  }

  // Heart-rate zone distribution (Trends page; data comes from the per-day five-zone minutes computed during scoring)
  function hrZonesHtml(z) {
    if (!z || !z.total_min?.some((v) => v > 0)) return "";
    const total = z.total_min.reduce((a, b) => a + b, 0);
    const bar = (mins) => mins.map((v, i) => `<span style="width:${(v / (mins.reduce((a, b) => a + b, 0) || 1) * 100).toFixed(2)}%;opacity:${(0.25 + i * 0.18).toFixed(2)}"></span>`).join("");
    const weeks = z.weeks.slice(-8);
    return `<div class="card trend"><div class="trend-head"><div><div class="trend-label">${tr("心率区间分布")}</div>
        <div class="trend-value">${fmtNum(total / 60, 1)}<small>${tr("小时")}</small></div></div><div class="trend-tip">%HRmax</div></div>
      <div class="zbar">${bar(z.total_min)}</div>
      <div class="zleg">${z.total_min.map((v, i) => `<span><b>${Math.round((v / total) * 100)}%</b>${esc(z.labels[i])}</span>`).join("")}</div>
      ${weeks.map((w) => `<div class="row" style="margin-top:8px;gap:8px"><span class="small muted" style="width:40px">${shortDay(String(w.week).slice(0, 10))}</span><div class="zbar" style="flex:1;margin:0;height:6px">${bar(w.minutes)}</div></div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ trends: metrics (every WHOOP metric, grouped like WHOOP; series and status cards from /trends, trends/series.py)
  // One chart helper for all cards: layers of bars / lines / bands on a shared x axis (one slot per day), with a hover cursor.
  // layer: {type: "bar" | "line" | "avg" | "dots" | "band" | "range", values, cls, lo, hi, axis: "r"} ("range" = per-day [lo, hi] boxes, "r" = right axis 0–21)
  const CH = new Map();   // chart id -> {n, W, tip(i), y(i)} for the hover cursor
  // chart-range:start (pure; tests/test_charts.py runs it with node)
  // y range of a chart: every reading, and the reference band opts.zone at least where it is nearest to the readings: a band
  // wholly above (below) them adds its lower (upper) edge, so a strip of it always shows; a band that overlaps them adds
  // nothing (it is clipped at the plot edge) so a normal week is not flattened. opts.min / opts.max are the usual scale and
  // widen when a reading falls outside; every side not pinned by them gets padding. (FB-2: the HRR card had min 0, zone
  // 20–60 and a few low readings; the band was drawn from far outside the chart over the cards below.)
  function chartRange(nums, opts = {}) {
    const data = nums.filter((v) => v != null && Number.isFinite(v));
    const z = (opts.zone || []).filter((v) => v != null && Number.isFinite(v));
    const vals = [...data];
    if (z.length === 2) {
      const [zlo, zhi] = [Math.min(...z), Math.max(...z)];
      if (!data.length) vals.push(zlo, zhi);
      else if (zlo > Math.max(...data)) vals.push(zlo);
      else if (zhi < Math.min(...data)) vals.push(zhi);
    }
    if (!vals.length) return { lo: opts.min ?? 0, hi: opts.max ?? (opts.min ?? 0) + 1 };
    const dlo = Math.min(...vals), dhi = Math.max(...vals);
    let lo = opts.min != null ? Math.min(opts.min, dlo) : dlo, hi = opts.max != null ? Math.max(opts.max, dhi) : dhi;
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.05 || 1;
    if (opts.min == null || dlo < opts.min) lo -= pad;
    if (opts.max == null || dhi > opts.max) hi += pad;
    return { lo, hi };
  }
  // chart-range:end
  function tChart(id, dates, layers, opts = {}) {
    const wide = WIDE.matches, W = wide ? 560 : 320, H = opts.h ?? (wide ? 150 : 92), T = 6, B = 16, n = dates.length;
    const nums = layers.filter((l) => l.axis !== "r").flatMap((l) => (l.type === "range" ? l.values.flat() : [...(l.values || []), ...(l.lo || []), ...(l.hi || [])])).filter((v) => v != null);
    const { lo, hi } = chartRange(nums, opts);
    const top = (y) => Math.max(T, Math.min(H - B, y));   // a band edge, kept inside the plot area
    const slot = W / Math.max(1, n), bw = Math.max(1, slot * (n > 60 ? 0.9 : 0.66));
    const cx = (i) => (n === 1 ? W / 2 : opts.bars ? slot * (i + 0.5) : (i / (n - 1)) * W);
    const sy = (v, axis) => (axis === "r" ? T + (1 - Math.max(0, Math.min(21, v)) / 21) * (H - T - B) : T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B));
    const path = (vals, axis) => { let d = "", pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${cx(i).toFixed(1)},${sy(v, axis).toFixed(1)}`; pen = true; }); return d; };
    // Data layers are clipped to the plot area (a little room left and right for the end dots); the date labels stay outside the clip
    let g = `<defs><clipPath id="${id}-clip"><rect x="-4" y="0" width="${W + 8}" height="${H - B + 0.5}"/></clipPath></defs><g clip-path="url(#${id}-clip)">`;
    if (opts.guide) for (const [v, t] of opts.guide) g += `<line class="guide" x1="0" x2="${W}" y1="${sy(v).toFixed(1)}" y2="${sy(v).toFixed(1)}"/>${t ? `<text x="${W}" y="${(sy(v) - 3).toFixed(1)}" text-anchor="end">${t}</text>` : ""}`;
    if (opts.zone) { const y1 = top(sy(opts.zone[1])), y0 = top(sy(opts.zone[0])); g += `<rect class="band" x="0" width="${W}" y="${y1.toFixed(1)}" height="${Math.max(0, y0 - y1).toFixed(1)}" rx="3"/>`; }
    for (const l of layers) {
      if (l.type === "band") {   // personal baseline band, one block per run of consecutive values
        let seg = [];
        const flush = () => { if (seg.length > 1) g += `<path class="base" d="M${seg.map((i) => `${cx(i).toFixed(1)},${sy(l.hi[i]).toFixed(1)}`).join("L")}L${[...seg].reverse().map((i) => `${cx(i).toFixed(1)},${sy(l.lo[i]).toFixed(1)}`).join("L")}Z"/>`; seg = []; };
        l.lo.forEach((v, i) => { if (v == null || l.hi[i] == null) flush(); else seg.push(i); });
        flush();
        if (l.mean) g += `<path class="base-mean" d="${path(l.mean)}"/>`;
      } else if (l.type === "range") {
        l.values.forEach((r, i) => { if (r) { const y1 = top(sy(r[1], l.axis)), y0 = top(sy(r[0], l.axis)); g += `<rect class="tgt" x="${(cx(i) - slot / 2).toFixed(1)}" width="${slot.toFixed(2)}" y="${y1.toFixed(1)}" height="${Math.max(0.5, y0 - y1).toFixed(1)}"/>`; } });
      } else if (l.type === "bar") {
        const base = sy(Math.max(lo, Math.min(hi, opts.barBase ?? Math.max(lo, 0))), l.axis);
        l.values.forEach((v, i) => {
          if (v == null) return;
          const y = sy(v, l.axis), cls = typeof l.cls === "function" ? l.cls(v, i) : l.cls || "";
          g += `<rect class="tbar ${cls}" x="${(cx(i) - bw / 2).toFixed(1)}" width="${bw.toFixed(2)}" y="${Math.min(y, base).toFixed(1)}" height="${Math.max(0.8, Math.abs(base - y)).toFixed(1)}" rx="${Math.min(2, bw / 3).toFixed(1)}"/>`;
        });
      } else if (l.type === "dots") {
        l.values.forEach((v, i) => { if (v != null) g += `<circle class="dot ${l.cls || ""}" cx="${cx(i).toFixed(1)}" cy="${sy(v, l.axis).toFixed(1)}" r="2.6"/>`; });
      } else {
        g += `<path class="${l.type === "avg" ? "avg" : "raw"} ${l.cls || ""}" d="${path(l.values, l.axis)}"/>`;
        if (l.type === "line" && l.dots !== false && (n <= 31 || (wide && n <= 92))) l.values.forEach((v, i) => { if (v != null) g += `<circle class="dot" cx="${cx(i).toFixed(1)}" cy="${sy(v, l.axis).toFixed(1)}" r="1.3"/>`; });
      }
    }
    g += "</g>";
    if (wide && !opts.noTicks) for (const v of [lo + (hi - lo) * 0.15, hi - (hi - lo) * 0.15]) g += `<text class="ylab" x="0" y="${(sy(v) - 3).toFixed(1)}">${fmtNum(v, Math.abs(hi - lo) < 5 ? 1 : 0)}</text>`;
    g += `<line class="grid" x1="0" x2="${W}" y1="${H - B}" y2="${H - B}"/>`;
    g += `<text x="0" y="${H - 3}">${shortDay(dates[0])}</text>${wide ? `<text x="${W / 2}" y="${H - 3}" text-anchor="middle">${shortDay(dates[Math.floor((n - 1) / 2)])}</text>` : ""}<text x="${W}" y="${H - 3}" text-anchor="end">${shortDay(dates[n - 1])}</text>`;
    g += `<line class="cursor" id="${id}-cur" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><circle class="cursor-dot" id="${id}-dot" r="3.5" visibility="hidden"/>`;
    const main = layers.find((l) => l.main) || layers.find((l) => l.type !== "band" && l.type !== "range");
    CH.set(id, { n, W, cx, tip: opts.tip, y: (i) => { const v = main?.values?.[i]; return v == null ? null : sy(v, main.axis); } });
    return `<svg class="chart" id="${id}" data-hover="1" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "")}">${g}</svg>`;
  }

  function bindCharts(view) {
    for (const svg of $$("svg[data-hover]", view)) {
      const c = CH.get(svg.id), tip = $(`#tip-${svg.id}`, view);
      if (!c || !tip) continue;
      const cur = $(`#${svg.id}-cur`, svg), dot = $(`#${svg.id}-dot`, svg);
      const move = (ev) => {
        const rect = svg.getBoundingClientRect();
        const xv = ((ev.clientX - rect.left) / rect.width) * c.W;
        let i = 0;
        for (let k = 1; k < c.n; k++) if (Math.abs(c.cx(k) - xv) < Math.abs(c.cx(i) - xv)) i = k;
        const px = c.cx(i), y = c.y(i);
        cur.setAttribute("x1", px); cur.setAttribute("x2", px); cur.setAttribute("visibility", "visible");
        if (y != null) { dot.setAttribute("cx", px); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible"); } else dot.setAttribute("visibility", "hidden");
        tip.innerHTML = c.tip ? c.tip(i) : "";
      };
      const leave = () => { cur.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.innerHTML = tip.dataset.base || ""; };
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerdown", move);
      svg.addEventListener("pointerleave", leave);
      svg.addEventListener("pointercancel", leave);
    }
  }

  const xpLabel = (key, label) => `<button class="trend-label xp-label" data-feature="explain" ${sheetAttr(() => explainSheet("metric", key, label))}>${label}<i class="xp-i">${ICON.info}</i></button>`;
  const RANGE = [[7, tr("1周")], [30, tr("1月")], [90, tr("3月")], [180, tr("6月")], [365, tr("1年")]];
  const rangeName = (n) => (RANGE.find(([d]) => d === n) || [n, tr("{n} 天", { n })])[1];
  // [key, label, unit, decimals, better direction (1 up / -1 down / 0 neutral), chart, explain key]
  // chart: "line" (raw + 7-day mean), "bars" (daily bars), "sparse" (dots, few readings), "last" = headline is the latest value
  const TCARD = {
    hrv: ["HRV", "ms", 0, 1, "line", "hrv"], resting_hr: [tr("静息心率"), "bpm", 0, -1, "line", "resting_hr"],
    resp_rate: [tr("呼吸频率"), tr("次/分"), 1, 0, "line", "resp_rate"], spo2: [tr("血氧"), "%", 1, 1, "line", "spo2"],
    wrist_temp: [tr("手腕温度"), "°C", 2, 0, "sparse", "wrist_temp"], illness: [tr("生病风险"), "", 0, -1, "line", "illness"],
    recovery_index: [tr("最低心率出现"), "%", 0, -1, "line", "recovery_index"],
    sleep_performance: [tr("睡眠表现"), "%", 0, 1, "line", "sleep_performance"], sleep_perf_whoop: [tr("睡眠表现（综合）"), "", 0, 1, "line", "sleep_perf_whoop"],
    sleep_debt_hours: [tr("睡眠债"), tr("小时"), 1, -1, "line", "sleep_debt"], restorative_hours: [tr("恢复性睡眠"), tr("小时"), 1, 1, "bars", "restorative"],
    sleep_efficiency: [tr("睡眠效率"), "%", 0, 1, "line", "sleep_efficiency"], sleep_score: [tr("睡眠分"), "", 0, 1, "line", "sleep_score"],
    awakenings: [tr("夜间醒来"), tr("次"), 1, -1, "bars", "awakenings"], daylight: [tr("日照"), tr("分钟"), 0, 1, "bars", "daylight"],
    acwr: [tr("急慢性负荷比"), "", 2, 0, "line", "acwr"], strain_combined: [tr("负荷（含肌肉）"), "", 1, 0, "line", "strain_combined"],
    steps: [tr("步数"), "", 0, 1, "bars", "steps"], active_kcal: [tr("活动消耗"), "kcal", 0, 0, "bars", "calories"],
    total_kcal: [tr("总消耗"), "kcal", 0, 0, "bars", "calories"], hr_recovery: [tr("运动后心率恢复"), "bpm", 0, 1, "sparse", "hr_recovery"],
    stress: [tr("压力"), "", 1, -1, "line", "stress"], resilience: [tr("韧性"), "", 0, 1, "line", "resilience"],
    vo2max: ["VO₂max", "", 1, 1, "sparse", "vo2max"], weight: [tr("体重"), "kg", 1, -1, "sparse", "weight"],
    body_age: [tr("身体年龄"), tr("岁"), 1, -1, "sparse", "body_age"],
  };
  const CHART_OPTS = {
    spo2: { max: 100 }, illness: { min: 0, max: 100, zone: [0, 30] }, recovery_index: { min: 0, max: 100, zone: [0, 50] },
    sleep_performance: { min: 0, max: 110, zone: [85, 110] }, sleep_perf_whoop: { min: 0, max: 100, zone: [85, 100] },
    sleep_efficiency: { max: 100 }, sleep_score: { min: 0, max: 100 }, acwr: { min: 0, zone: [0.8, 1.3] },
    strain_combined: { min: 0, max: 21 }, stress: { min: 0, max: 3, zone: [0, 1] }, resilience: { zone: [42, 100] },
    wrist_temp: { zone: [-0.5, 0.5] }, hr_recovery: { min: 0, zone: [20, 60] },
  };
  const OPTIONAL = new Set(["recovery_index", "hr_recovery", "wrist_temp", "strain_combined", "daylight", "body_age"]);   // hidden when there is no data at all
  const LATEST = new Set(["vo2max", "weight", "body_age"]);
  const lastOf = (xs) => [...(xs || [])].reverse().find((v) => v != null);
  // "vs 上期": change of the period figure against the previous period of the same length, coloured by the better direction
  function deltaHtml(cur, prev, dig, better, unit) {
    if (cur == null || prev == null) return "";
    const dv = cur - prev, small = Math.abs(dv) < Math.pow(10, -dig) / 2;
    const tone = small || !better ? "neutral" : dv * better > 0 ? "good" : "bad";
    return `<span class="dlt ${tone}">${small ? "→" : dv > 0 ? "↑" : "↓"} ${fmtNum(Math.abs(dv), dig)}${unit ? `<small> ${esc(unit)}</small>` : ""}</span>`;
  }
  const headOf = (key, s) => (LATEST.has(key) ? lastOf(s.values) : s.mean);
  const tipDay = (d, s, dig, unit) => (i) => `${shortDay(d.dates[i])} · ${s.values[i] != null ? `${fmtNum(s.values[i], dig)}${unit ? ` ${esc(unit)}` : ""}` : "—"}`;

  function cardHtml(key, d, { label, unit, dig, better, chart, xp, body, head, base, sub, sheet } = {}) {
    const s = d.series[key];
    const [l0, u0, g0, b0, c0, x0] = TCARD[key] || [];
    label ??= l0; unit ??= u0 ?? s?.unit; dig ??= g0 ?? 0; better ??= b0 ?? 0; chart ??= c0 || "line"; xp ??= x0 || key;
    if (!s) return /授权|permission|forbidden/i.test(d.unavailable?.[key] || "") ? "" : `<div class="card trend">${xpLabel(xp, label)}<div class="trend-value">${dash()}</div></div>`;   // i18n-ok: server reason
    if (!s.n && OPTIONAL.has(key) && !s.prev_n) return "";
    const id = `ch-${key}`;
    const hv = head !== undefined ? head : headOf(key, s);
    const tipBase = base ?? deltaHtml(hv, s.prev_mean, dig, better, "");
    const opts = { ...(CHART_OPTS[key] || {}), label, tip: tipDay(d, s, dig, unit) };
    const layers = chart === "bars" ? [{ type: "bar", values: s.values, cls: "plain", main: true }]
      : chart === "sparse" ? [{ type: "dots", values: s.values, main: true }, { type: "avg", values: s.avg7 }]
      : [...(s.baseline ? [{ type: "band", lo: s.baseline.lo, hi: s.baseline.hi, mean: s.baseline.mean }] : []), { type: "line", values: s.values, main: true }, { type: "avg", values: s.avg7 }];
    const svg = !s.n ? "" : body ?? tChart(id, d.dates, layers, { ...opts, bars: chart === "bars", barBase: opts.min ?? 0 });
    const inner = `<div class="trend-head"><div>${sheet ? `<span class="trend-label">${label}</span>` : xpLabel(xp, label)}
        <div class="trend-value">${hv != null ? fmtNum(hv, dig) : dash()}${unit && hv != null ? `<small>${esc(unit)}</small>` : ""}${sub ? `<small class="sub">${sub}</small>` : ""}</div></div>
        <div class="trend-tip" id="tip-${id}" data-base="${esc(tipBase)}">${tipBase}</div></div>
      ${s.n ? svg : `<div class="trend-tip faint" style="text-align:left">${tr("{r} 无数据", { r: rangeName(d.days) })}</div>`}${s.baseline && s.n ? `<div class="legend"><span><i class="base-key"></i>${tr("基线")}</span></div>` : ""}`;
    return sheet ? `<button class="card trend" ${sheetAttr(sheet)}>${inner}</button>` : `<div class="card trend">${inner}</div>`;
  }

  // Recovery: bars coloured green / yellow / red, the day counts, contributors (z-scores vs baseline) in the sheet
  const Z_NAME = { hrv: "HRV", rhr: tr("静息心率"), resp_rate: tr("呼吸频率"), sleep_performance: tr("睡眠表现") };
  function recoveryTrendHtml(d) {
    const s = d.series.recovery;
    if (!s) return cardHtml("recovery", d, { label: tr("恢复"), unit: "", dig: 0, better: 1 });
    const zc = s.zones || {}, zn = (v) => (v >= 67 ? "good" : v >= 34 ? "mid" : "bad");
    const body = s.n ? tChart("ch-recovery", d.dates, [{ type: "bar", values: s.values, cls: zn, main: true }], { min: 0, max: 100, bars: true, label: tr("恢复"), tip: tipDay(d, s, 0, "") }) : "";
    const tot = (zc.green || 0) + (zc.yellow || 0) + (zc.red || 0);
    const zbar = tot ? `<div class="hbar" style="margin-top:10px">${[["green", "good"], ["yellow", "mid"], ["red", "bad"]].map(([k, c]) => `<span class="${c}" style="width:${((zc[k] || 0) / tot * 100).toFixed(1)}%"></span>`).join("")}</div>
      <div class="hbar-l"><span><b>${zc.green || 0}</b> ${tr("绿")}</span><span><b>${zc.yellow || 0}</b> ${tr("黄")}</span><span><b>${zc.red || 0}</b> ${tr("红")}</span></div>` : "";
    const sheet = () => {
      const zs = (s.contributors || []).filter(Boolean);
      const avg = Object.keys(Z_NAME).map((k) => { const xs = zs.map((z) => z[k]).filter((v) => v != null); return [k, xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null]; });
      const last = [...(s.contributors || [])].reverse().find(Boolean) || {};
      const rows = avg.filter(([, v]) => v != null).map(([k, v]) => {
        const w = Math.min(50, Math.abs(v) * 25);
        return `<div class="contrib"><div class="contrib-head"><span>${Z_NAME[k]}</span><span class="y ${v > 0 ? "good" : v < 0 ? "bad" : "muted"}">${signed(v, 2)}<span class="muted"> · ${tr("最近")} ${last[k] != null ? signed(last[k], 2) : "—"}</span></span></div>
          <div class="dbar"><span class="${v > 0 ? "younger" : "older"}" style="width:${w.toFixed(1)}%"></span></div></div>`;
      }).join("");
      openSheet(tr("恢复贡献项"), `${kv(tr("{r}均值", { r: rangeName(d.days) }), s.mean != null ? fmtNum(s.mean) : null)}${kv(tr("上期"), s.prev_mean != null ? fmtNum(s.prev_mean) : null)}
        ${rows ? `<div class="cat" style="margin-top:14px">${tr("相对基线（z）")}</div>${rows}` : ""}${xpBox}`, (root) => explainInto(root, "metric", "recovery", false));
    };
    return cardHtml("recovery", d, { label: tr("恢复"), unit: "", dig: 0, better: 1, xp: "recovery", body: body + zbar, sheet });
  }

  // Strain: daily bars inside the Strain Coach target band of each day, days in range
  function strainTrendHtml(d) {
    const s = d.series.strain;
    if (!s) return cardHtml("strain", d, { label: tr("负荷"), dig: 1 });
    const t = s.target || {};
    const inRange = (v, i) => { const b = t.bands?.[i]; return b && v >= b[0] && v <= b[1] ? "good" : "plain"; };
    const body = s.n ? tChart("ch-strain", d.dates, [{ type: "range", values: t.bands || [] }, { type: "bar", values: s.values, cls: inRange, main: true }],
      { min: 0, max: 21, bars: true, label: tr("负荷"), tip: (i) => `${shortDay(d.dates[i])} · ${s.values[i] != null ? fmtNum(s.values[i], 1) : "—"}${t.bands?.[i] ? ` <span class="muted">/ ${fmtNum(t.bands[i][0], 0)}–${fmtNum(t.bands[i][1], 0)}</span>` : ""}` }) : "";
    const legend = t.days_with_strain ? `<div class="legend"><span><i class="tgt-key"></i>${tr("目标区间")}</span><span><b>${t.in_range_days}</b>/${tr("{n} 天达标", { n: t.days_with_strain })}</span></div>` : "";
    return cardHtml("strain", d, { label: tr("负荷"), unit: "", dig: 1, better: 0, xp: "strain", body: body + legend });
  }

  // Recovery vs strain on one chart (left axis recovery 0–100, right axis strain 0–21)
  function recoveryStrainHtml(d) {
    const r = d.series.recovery, s = d.series.strain;
    if (!r?.n || !s?.n) return "";
    const body = tChart("ch-rs", d.dates, [{ type: "avg", values: r.avg7, cls: "good", main: true }, { type: "avg", values: s.avg7, cls: "train", axis: "r" }],
      { min: 0, max: 100, label: tr("恢复和负荷"), noTicks: true, tip: (i) => `${shortDay(d.dates[i])} · ${tr("恢复")} ${r.values[i] ?? "—"} · ${tr("负荷")} ${s.values[i] != null ? fmtNum(s.values[i], 1) : "—"}` });
    return `<div class="card trend"><div class="trend-head"><div>${xpLabel("recovery_vs_strain", tr("恢复 vs 负荷"))}<div class="trend-value">${fmtNum(r.mean)}<small>/ ${fmtNum(s.mean, 1)}</small></div></div>
      <div class="trend-tip" id="tip-ch-rs" data-base=""></div></div>${body}
      <div class="legend"><span><i class="k-good"></i>${tr("恢复")}</span><span><i class="k-train"></i>${tr("负荷")}</span><span class="muted">${tr("7 天均")}</span></div></div>`;
  }

  // Sleep vs need: bars = actual, line = need
  function sleepNeedHtml(d) {
    const a = d.series.sleep_hours, n = d.series.sleep_need_hours;
    if (!a) return cardHtml("sleep_hours", d, { label: tr("睡眠时长"), unit: tr("小时"), dig: 1, better: 1, xp: "sleep_hours" });
    const body = a.n ? tChart("ch-sleep_hours", d.dates, [{ type: "bar", values: a.values, cls: "sleep", main: true }, ...(n ? [{ type: "line", values: n.values, cls: "need", dots: false }] : [])],
      { min: 0, bars: true, label: tr("睡眠和需求"), tip: (i) => `${shortDay(d.dates[i])} · ${hm(a.values[i] != null ? a.values[i] * 60 : null) ?? "—"}${n?.values[i] != null ? ` <span class="muted">/ ${hm(n.values[i] * 60)}</span>` : ""}` }) : "";
    return cardHtml("sleep_hours", d, { label: tr("睡眠 vs 需求"), unit: tr("小时"), dig: 1, better: 1, xp: "sleep_need", body: body + (n ? `<div class="legend"><span><i class="k-sleep"></i>${tr("实际")}</span><span><i class="k-need"></i>${tr("需求 {n} 小时", { n: fmtNum(n.mean, 1) })}</span></div>` : "") });
  }

  // Training load: CTL (fitness) and ATL (fatigue) lines; headline TSB
  function loadTrendHtml(d) {
    const c = d.series.ctl, a = d.series.atl, t = d.series.tsb;
    if (!c?.n) return "";
    const body = tChart("ch-ctl", d.dates, [{ type: "avg", values: c.values, cls: "train", main: true }, ...(a ? [{ type: "avg", values: a.values, cls: "fatigue" }] : [])],
      { min: 0, label: tr("训练负荷"), tip: (i) => `${shortDay(d.dates[i])} · CTL ${fmtNum(c.values[i])} · ATL ${fmtNum(a?.values[i])} · TSB ${t?.values[i] != null ? signed(t.values[i], 0) : "—"}` });
    const tsb = lastOf(t?.values);
    return `<div class="card trend"><div class="trend-head"><div>${xpLabel("training_load", tr("训练负荷"))}<div class="trend-value">${tsb != null ? signed(tsb, 0) : dash()}<small>TSB</small></div></div>
      <div class="trend-tip" id="tip-ch-ctl" data-base="${esc(deltaHtml(lastOf(c.values), c.prev_mean, 0, 1, ""))}">${deltaHtml(lastOf(c.values), c.prev_mean, 0, 1, "")}</div></div>${body}
      <div class="legend"><span><i class="k-train"></i>CTL ${fmtNum(lastOf(c.values))}</span><span><i class="k-fatigue"></i>ATL ${fmtNum(lastOf(a?.values))}</span></div></div>`;
  }

  // Weekly bars (aerobic minutes vs the 150-minute target; strength volume)
  function weeklyBarsHtml(id, label, xp, weeks, unit, target) {
    if (!weeks?.length || !weeks.some((w) => w.value)) return "";
    const dates = weeks.map((w) => String(w.week).slice(0, 10)), vals = weeks.map((w) => w.value);
    const body = tChart(id, dates, [{ type: "bar", values: vals, cls: (v) => (target && v >= target ? "good" : "train"), main: true }],
      { min: 0, max: Math.max(target || 0, ...vals) * 1.1, bars: true, guide: target ? [[target, String(target)]] : null, label, tip: (i) => `${tr("{d} 起", { d: shortDay(dates[i]) })} · ${fmtNum(vals[i])} ${unit}` });
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return `<div class="card trend"><div class="trend-head"><div>${xpLabel(xp, label)}<div class="trend-value">${fmtNum(avg)}<small>${esc(unit)} / ${tr("周")}</small></div></div>
      <div class="trend-tip" id="tip-${id}" data-base="${target ? tr("{a}/{b} 周达标", { a: weeks.filter((w) => w.value >= target).length, b: weeks.length }) : ""}">${target ? tr("{a}/{b} 周达标", { a: weeks.filter((w) => w.value >= target).length, b: weeks.length }) : ""}</div></div>${body}</div>`;
  }
  function muscleWeeks(d) {
    const s = d.series.muscle_load;
    if (!s?.n) return null;
    const m = new Map();
    d.dates.forEach((x, i) => { const dt = parseDay(x); dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); const k = isoDay(dt); m.set(k, (m.get(k) || 0) + (s.values[i] || 0)); });
    return [...m].map(([week, value]) => ({ week, value }));
  }

  // ---- status cards as of the selected day (short labels; details and the explanation in the sheet)
  const ST_TONE = {
    hrv_status: { balanced: "good", high: "plain", low: "mid", poor: "bad" },
    training_status: { productive: "good", maintaining: "plain", recovery: "plain", strained: "mid", unproductive: "mid", overreaching: "bad", detraining: "mid" },
    resilience: { exceptional: "good", strong: "good", solid: "plain", adequate: "mid", limited: "bad" },
    illness: { low: "good", watch: "mid", high: "bad" },
    recovery_index: { good: "good", fair: "mid", late: "bad" }, hr_recovery: { good: "good", fair: "plain", low: "bad" },
  };
  function statusTiles(st) {
    if (!st) return "";
    const tile = (key, label, value, tone, sub, rows) => `<button class="mini st-tile" ${sheetAttr(() => openSheet(label, `${rows || ""}${xpBox}`, (root) => explainInto(root, "metric", key, false)))}>
      <span class="tile-label">${label}</span><span class="v ${tone ? `t-${tone}` : ""}">${value}</span>${sub ? `<span class="s">${sub}</span>` : ""}</button>`;
    const out = [];
    const h = st.hrv_status;
    if (h) out.push(tile("hrv_status", tr("HRV 状态"), esc(h.label), ST_TONE.hrv_status[h.status], `${fmtNum(h.avg7_ms)} ms`,
      kv(tr("7 天均"), `${fmtNum(h.avg7_ms)} ms`) + kv(tr("基线带"), `${fmtNum(h.band_ms[0])}–${fmtNum(h.band_ms[1])} ms`) + kv(tr("夜数"), h.nights)));
    const t = st.training_status;
    if (t) out.push(tile("training_status", tr("训练状态"), esc(t.label), ST_TONE.training_status[t.status], tr("负荷比 {n}", { n: fmtNum(t.acwr, 2) }),
      kv(tr("急慢性负荷比"), fmtNum(t.acwr, 2)) + kv(tr("7 天 / 28 天 TRIMP"), `${fmtNum(t.acute)} / ${fmtNum(t.chronic)}`) + kv(tr("VO₂max 60 天"), t.vo2_change_60d != null ? signed(t.vo2_change_60d, 1) : null)
      + (st.load_focus ? `<div class="cat" style="margin-top:14px">${tr("负荷重点 · 28 天")}</div><div class="hbar">${st.load_focus.groups.map((g, i) => `<span class="${["good", "mid", "bad"][i]}" style="width:${g.pct}%"></span>`).join("")}</div>
        <div class="hbar-l">${st.load_focus.groups.map((g) => `<span><b>${g.pct}%</b> ${esc(g.label)}</span>`).join("")}</div>` : "")));
    const r = st.resilience;
    if (r) out.push(tile("resilience", tr("韧性"), esc(r.label), ST_TONE.resilience[r.level], tr("14 天"),
      kv(tr("恢复能力"), fmtNum(r.capacity * 100)) + kv(tr("压力负担"), r.stress_load != null ? fmtNum(r.stress_load * 100) : null)));
    const il = st.illness;
    if (il) out.push(tile("illness", tr("生病风险"), esc(il.label), ST_TONE.illness[il.level], `${il.score}`, kv(tr("分数"), `${il.score} / 100`)));
    const bb = st.body_battery;
    if (bb) out.push(tile("body_battery", tr("身体电量"), `${bb.current}`, bb.current >= 60 ? "good" : bb.current >= 30 ? "mid" : "bad", tr("估算"),
      bbChart(bb) + kv(tr("醒来"), bb.wake) + kv(tr("最低"), bb.low) + tags([badge("plain", tr("估算"))])));
    const rt = st.recovery_time;
    if (rt) out.push(tile("recovery_time", tr("恢复时间"), rt.hours ? `${rt.hours}<small> ${tr("小时")}</small>` : tr("已恢复"), rt.hours > 24 ? "mid" : rt.hours ? "plain" : "good", tr("估算"),
      kv(tr("还需"), tr("{n} 小时", { n: rt.hours })) + (rt.from_date ? kv(tr("来自"), `${shortDay(String(rt.from_date))} · ${fmtNum(rt.from_strain, 1)}`) : "") + tags([badge("plain", tr("估算"))])));
    const ri = st.recovery_index;
    if (ri) out.push(tile("recovery_index", tr("最低心率"), esc(ri.label), ST_TONE.recovery_index[ri.level], `${ri.pct}%`, kv(tr("出现在整晚的"), `${ri.pct}%`)));
    const hr = st.hr_recovery;
    if (hr) out.push(tile("hr_recovery", tr("心率恢复"), `${fmtNum(hr.bpm)}<small> bpm</small>`, ST_TONE.hr_recovery[hr.level], shortDay(String(hr.date)), kv(tr("1 分钟下降"), `${fmtNum(hr.bpm)} bpm`)));
    const c = st.chronotype;
    if (c) out.push(tile("chronotype", tr("生物钟"), esc(c.label), "", `${tr("中点")} ${esc(c.mid_sleep)}`, kv(tr("睡眠中点"), esc(c.mid_sleep)) + kv(tr("夜数"), c.nights) + kv(tr("按休息日"), c.free_days ? tr("是") : tr("否"))));
    return out.length ? `<div class="st-grid">${out.join("")}</div>` : "";
  }
  function bbChart(bb) {
    const pts = bb.points || [];
    if (pts.length < 2) return "";
    const W = 320, H = 70, x = (h) => ((h - pts[0].hour) / Math.max(1, pts[pts.length - 1].hour - pts[0].hour)) * W, y = (v) => 4 + (1 - v / 100) * (H - 20);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.hour).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("身体电量估算")}"><path class="avg good" d="${d}"/><line class="grid" x1="0" x2="${W}" y1="${H - 16}" y2="${H - 16}"/>
      <text x="0" y="${H - 3}">${pts[0].hour}:00</text><text x="${W}" y="${H - 3}" text-anchor="end">${pts[pts.length - 1].hour}:00</text></svg>`;
  }

  // Trends end on the selected date (null = today, so the cache keys of today stay the same)
  const trendEnd = () => (isPast() ? S.viewDate : null);
  const GROUPS = [["recovery", tr("恢复")], ["sleep", tr("睡眠")], ["strain", tr("负荷")], ["stress", tr("压力")], ["long", tr("长期")]];
  const VO2_LEVEL = { excellent: tr("优秀"), good: tr("良好"), average: tr("中等"), fair: tr("一般"), poor: tr("较差") };
  async function metricsHtml() {
    const days = S.trendsDays, end = trendEnd(), q = end ? `&end=${end}` : "";
    const [d, vo2, sd] = await Promise.all([load(`trends${days}${end || ""}`, `/v1/users/${ownerPath()}/trends?days=${days}${q}`),
      optional(`vo2max${end || ""}`, WHOOP_API.vo2max(end)), optional(`sleepdetails${end || ""}`, WHOOP_API.sleepDetails(end))]);
    CH.clear();
    const fitness = vo2?.level_zh ? tr("体能 {l}", { l: VO2_LEVEL[vo2.level] || vo2.level_zh }) : null;   // vo2max endpoint: level converted from the same-age, same-sex percentile
    const restPct = d.series.restorative_pct?.mean;
    const cards = {
      recovery: [recoveryTrendHtml(d), cardHtml("hrv", d), cardHtml("resting_hr", d), cardHtml("resp_rate", d), cardHtml("spo2", d),
        cardHtml("wrist_temp", d), cardHtml("illness", d), cardHtml("recovery_index", d)],
      sleep: [sleepNeedHtml(d), cardHtml("sleep_performance", d), cardHtml("sleep_perf_whoop", d), cardHtml("sleep_debt_hours", d),
        cardHtml("restorative_hours", d, { sub: restPct != null ? ` ${fmtNum(restPct)}%` : "" }), cardHtml("sleep_efficiency", d), cardHtml("sleep_score", d),
        regularityHtml(sd), cardHtml("awakenings", d), cardHtml("daylight", d)],
      strain: [strainTrendHtml(d), recoveryStrainHtml(d), loadTrendHtml(d), cardHtml("acwr", d), cardHtml("strain_combined", d),
        weeklyBarsHtml("ch-azm", tr("有氧分钟"), "aerobic_min", d.aerobic_weeks, tr("分钟"), 150), weeklyBarsHtml("ch-vol", tr("训练量"), "muscle_load", muscleWeeks(d), "kg", 0),
        hrZonesHtml(d.hr_zones), cardHtml("steps", d), cardHtml("active_kcal", d), cardHtml("total_kcal", d), cardHtml("hr_recovery", d)],
      stress: [cardHtml("stress", d), cardHtml("resilience", d)],
      long: [cardHtml("vo2max", d, { base: fitness ? esc(fitness) : undefined }), cardHtml("weight", d), cardHtml("body_age", d)],
    };
    // Long ranges (or ones crossing a year) show the year, otherwise "1 year" read as "10/19 – 10/18 vs 10/19 – 10/18"
    const withYear = d.days >= 180 || String(d.prev_start).slice(0, 4) !== String(d.end).slice(0, 4);
    const day = (x) => (withYear ? `${String(x).slice(0, 4)}/${shortDay(String(x))}` : shortDay(String(x)));
    const range = `<div class="rep-head">${esc(`${day(d.start)} – ${day(d.end)}`)}<span class="muted"> vs ${esc(`${day(d.prev_start)} – ${day(addDays(String(d.start), -1))}`)}</span></div>`;
    // Status tiles describe "now" (as of the end date) and do not change with the range: keep them below the range-dependent charts
    const now = statusTiles(d.status);
    return range + GROUPS.map(([g, t]) => {
      const html = cards[g].filter(Boolean).join("");
      return html ? `<div class="section"><div class="section-head"><h2>${t}</h2></div><div class="trend-grid">${html}</div></div>` : "";
    }).join("") + (now ? `<div class="section"><div class="section-head"><h2>${tr("当前状态")}</h2><span class="small muted">${esc(shortDay(String(d.end)))}</span></div>${now}</div>` : "");
  }

  function regularityHtml(sd) {
    if (!sd || sd.consistency_pct_7d == null && sd.consistency_pct_30d == null) return "";
    const nights = (sd.nights || []).filter((n) => n.bedtime && n.wake_time);
    const W = 320, H = 70, rowH = Math.min(4, (H - 14) / Math.max(1, nights.length));
    const off = (iso) => { const t = Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16)); return t < 18 * 60 ? t + 6 * 60 : t - 18 * 60; };   // 18:00 → 0
    const x = (m) => (m / (22 * 60)) * W;   // 18:00 -> 16:00 next day
    const bars = nights.map((n, i) => { const a = off(n.bedtime), b = Math.max(a + 10, off(n.wake_time)); return `<rect x="${x(a).toFixed(1)}" y="${(i * (rowH + 0.6)).toFixed(1)}" width="${(x(b) - x(a)).toFixed(1)}" height="${rowH.toFixed(1)}" rx="1.5"/>`; }).join("");
    const ticks = [[6, tr("0 点")], [12, tr("6 点")], [18, tr("12 点")]].map(([h, t]) => `<text x="${x(h * 60).toFixed(1)}" y="${H - 2}" text-anchor="middle">${t}</text>`).join("");
    const sheet = () => openSheet(tr("睡眠规律性"), `${kv(tr("近 7 天"), sd.consistency_pct_7d != null ? `${sd.consistency_pct_7d}%` : null)}${kv(tr("近 30 天"), sd.consistency_pct_30d != null ? `${sd.consistency_pct_30d}%` : null)}${xpBox}`,
      (root) => explainInto(root, "metric", "sleep_consistency", false));
    return `<button class="card trend" ${sheetAttr(sheet)}><div class="trend-head"><div><div class="trend-label">${tr("睡眠规律性")}</div>
        <div class="trend-value">${sd.consistency_pct_7d ?? sd.consistency_pct_30d}<small>%</small></div></div><div class="trend-tip">${sd.consistency_pct_7d != null ? tr("7 天") : tr("30 天")}${sd.consistency_pct_30d != null && sd.consistency_pct_7d != null ? ` · ${tr("30 天")} ${sd.consistency_pct_30d}%` : ""}</div></div>
      ${bars ? `<svg class="chart regbars" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("最近 {n} 晚的入睡到醒来", { n: nights.length })}">${bars}${ticks}</svg>` : ""}</button>`;
  }

  // ------------------------------------------------------------------ trends: sleep
  const STAGE_NAME = { deep: tr("深睡"), core: tr("核心"), rem: "REM", unspec: tr("未分阶段"), awake: tr("清醒") };
  const hmToMin = (s) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
  const minToHm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  function circMean(times) {  // bedtimes cross midnight: average on the 24-hour circle
    if (!times.length) return null;
    let x = 0, y = 0;
    for (const t of times) { const a = (t / 1440) * 2 * Math.PI; x += Math.cos(a); y += Math.sin(a); }
    let a = Math.atan2(y, x); if (a < 0) a += 2 * Math.PI;
    return Math.round((a / (2 * Math.PI)) * 1440) % 1440;
  }

  function stagesChart(nights) {
    const W = 320, H = 120, B = 16, n = nights.length, gap = 4, bw = (W - gap * (n - 1)) / n;
    const maxMin = Math.max(600, ...nights.map((x) => (x.main ? (x.main.asleep_min || 0) + (x.main.awake_min || 0) : 0)));
    const sy = (m) => (m / maxMin) * (H - B);
    let g = `<line class="grid" x1="0" x2="${W}" y1="${H - B - sy(480)}" y2="${H - B - sy(480)}"/><text x="${W}" y="${H - B - sy(480) - 3}" text-anchor="end">8h</text>`;
    nights.forEach((x, i) => {
      const left = i * (bw + gap);
      if (i === 0 || i === n - 1 || i === Math.floor(n / 2)) g += `<text x="${(left + bw / 2).toFixed(1)}" y="${H - 3}" text-anchor="middle">${shortDay(x.date)}</text>`;
      if (!x.main) { g += `<rect class="st-unspec" x="${left.toFixed(1)}" y="${H - B - 2}" width="${bw.toFixed(1)}" height="2" rx="1"/>`; return; }
      const m = x.main;
      const staged = (m.deep_min || 0) + (m.core_min || 0) + (m.rem_min || 0) + (m.unspecified_min || 0);
      const parts = staged ? [["deep", m.deep_min], ["core", m.core_min], ["rem", m.rem_min], ["unspec", m.unspecified_min], ["awake", m.awake_min]] : [["unspec", m.asleep_min], ["awake", m.awake_min]];
      let y = H - B;
      for (const [cls, v] of parts) {
        if (!v) continue;
        const h = sy(v);
        y -= h;
        g += `<rect class="st-${cls}" x="${left.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, h).toFixed(1)}"><title>${STAGE_NAME[cls]} ${tr("{n} 分钟", { n: v })}</title></rect>`;
      }
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("最近 {n} 晚的睡眠阶段", { n })}">${g}</svg>
      <div class="legend"><span><i class="st-deep"></i>${tr("深睡")}</span><span><i class="st-core"></i>${tr("核心")}</span><span><i class="st-rem"></i>REM</span><span><i class="st-awake"></i>${tr("清醒")}</span></div>`;
  }

  function windowChart(nights) {
    // x axis 20:00 -> 16:00 next day (often sleeping into the afternoon)
    const W = 320, rowH = 9, H = nights.length * rowH + 16, SPAN = 20 * 60, ORIGIN = 20 * 60;
    const pos = (s) => { let m = hmToMin(s); if (m < ORIGIN) m += 1440; return Math.max(0, Math.min(SPAN, m - ORIGIN)); };
    const sx = (m) => (m / SPAN) * W;
    let g = "";
    for (let h = 0; h <= 20; h += 4) {
      const x = sx(h * 60);
      g += `<line class="grid" x1="${x}" x2="${x}" y1="0" y2="${H - 16}"/><text x="${x}" y="${H - 3}" text-anchor="${h === 0 ? "start" : h === 20 ? "end" : "middle"}">${minToHm((ORIGIN + h * 60) % 1440).slice(0, 2)}</text>`;
    }
    nights.forEach((x, i) => {
      const y = i * rowH + 1;
      for (const nap of x.naps) { const a = pos(nap.start), b = pos(nap.end); if (b > a) g += `<rect class="st-rem" x="${sx(a).toFixed(1)}" y="${y + 1}" width="${(sx(b) - sx(a)).toFixed(1)}" height="5" rx="2.5"/>`; }
      if (!x.main) return;
      const a = pos(x.main.start), b = pos(x.main.end);
      g += `<rect class="st-deep" x="${sx(a).toFixed(1)}" y="${y}" width="${Math.max(1, sx(b) - sx(a)).toFixed(1)}" height="7" rx="3.5"><title>${shortDay(x.date)} ${x.main.start}–${x.main.end}</title></rect>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${tr("入睡和醒来时间")}">${g}</svg>`;
  }

  function nightSheet(x) {
    const m = x.main;
    openSheet(fmtDay(x.date), !m ? `${kv(tr("睡眠"), null, x.reason || tr("睡眠"))}${x.sleep_score != null ? kv(tr("睡眠分"), x.sleep_score) : ""}` : `
      ${kv(tr("入睡 → 醒来"), `${m.start} → ${m.end}`)}${kv(tr("睡着"), esc(fmtMin(m.asleep_min)))}
      ${m.deep_min != null ? kv(tr("深睡 / 核心 / REM"), `${m.deep_min} / ${m.core_min} / ${m.rem_min} ${tr("分")}`) : ""}
      ${m.awake_min != null ? kv(tr("清醒"), `${m.awake_min} ${tr("分")}`) : ""}
      ${x.nap_min ? kv(tr("小睡"), esc(fmtMin(x.nap_min))) : ""}
      ${kv(tr("睡眠分"), x.sleep_score, x.reason)}
      ${x.sleep_need_min != null ? kv(tr("需求 / 睡眠债"), `${hm(x.sleep_need_min)} / ${hm(x.sleep_debt_min)}`) : ""}
      <div class="btns"><button class="btn" id="night-day">${tr("看这天")}</button></div>`, (root) => { $("#night-day", root).onclick = () => { closeSheet(); setViewDate(x.date, -1, "today"); }; });
  }

  async function sleepHtml() {
    const end = trendEnd();
    const d = await load(`sleep${end || ""}`, `/v1/users/${ownerPath()}/sleep/nights?days=14${end ? `&end=${end}` : ""}`);
    const nights = d.nights, mains = nights.filter((x) => x.main);
    const avg = mains.length ? Math.round(mains.reduce((s, x) => s + (x.main.asleep_min || 0), 0) / mains.length) : null;
    const bed = circMean(mains.map((x) => hmToMin(x.main.start))), wake = circMean(mains.map((x) => hmToMin(x.main.end)));
    const tile = (label, v) => `<div class="tile"><span class="tile-label">${label}</span><span class="tile-value">${v ?? dash()}</span></div>`;
    return `<div class="tiles" style="margin-top:0">${tile(tr("平均睡眠"), hm(avg))}${tile(tr("平均入睡"), bed != null ? minToHm(bed) : null)}${tile(tr("平均醒来"), wake != null ? minToHm(wake) : null)}</div>
      <div class="section"><div class="section-head"><h2>${tr("阶段")}</h2><span class="small muted">${tr("{a}/{b} 晚", { a: mains.length, b: nights.length })}</span></div><div class="card">${stagesChart(nights)}</div></div>
      <div class="section"><div class="section-head"><h2>${tr("作息")}</h2></div><div class="card">${windowChart(nights)}</div></div>
      <div class="section"><div class="card list">${[...nights].reverse().map((x) => `<button class="item night" ${sheetAttr(() => nightSheet(x))}>
          <span class="d">${shortDay(x.date)} ${WEEK[parseDay(x.date).getDay()]}</span>
          <span class="t">${x.main ? `${x.main.start}–${x.main.end}` : `<span class="faint">—</span>`}</span>
          <span class="h">${x.main ? hm(x.main.asleep_min) : ""}</span><span class="chev">${ICON.chev}</span></button>`).join("")}</div></div>`;
  }

  const RENDER_trends = async () => {
    const hasReport = !!(await optional(`report-week`, WHOOP_API.reports("week", S.todayDate)));   // show "Report" (「报告」) only once the report endpoint exists
    if (S.trendsMode === "report" && !hasReport) S.trendsMode = "metrics";
    const segMode = `<div class="seg" role="group" aria-label="${tr("内容")}">${[["metrics", tr("指标")], ["sleep", tr("睡眠")], ...(hasReport ? [["report", tr("报告")]] : [])].map(([k, t]) => `<button data-mode="${k}" aria-pressed="${S.trendsMode === k}">${t}</button>`).join("")}</div>`;
    const segDays = S.trendsMode === "metrics" ? `<div class="seg seg-range" role="group" aria-label="${tr("时间范围")}">${RANGE.map(([n, t]) => `<button data-days="${n}" aria-pressed="${n === S.trendsDays}">${t}</button>`).join("")}</div>` : "";
    const body = S.trendsMode === "metrics" ? await metricsHtml() : S.trendsMode === "report" ? await reportHtml() : await sleepHtml();
    return `<div class="toolbar">${segMode}${segDays}</div>${body}`;
  };

  function afterTrends(view) {
    for (const b of $$("[data-mode]", view)) b.onclick = () => { S.trendsMode = b.dataset.mode; render(); };
    for (const b of $$("[data-days]", view)) b.onclick = () => { S.trendsDays = Number(b.dataset.days); try { localStorage.setItem("be.trendsDays", S.trendsDays); } catch (e) { /* private mode */ } render(); };
    for (const b of $$("[data-rperiod]", view)) b.onclick = () => { S.reportPeriod = b.dataset.rperiod; render(); };
    if (S.trendsMode === "metrics") bindCharts(view);
  }

  // ------------------------------------------------------------------ log
  const recordDay = () => addDays(S.todayDate, -S.recordDay);
  const entriesKey = () => `journal:${addDays(S.todayDate, -1)}:${S.owner}`;
  // Use short names on labels: strip the parenthesised explanation and the leading "吃了" ("ate")
  const itemLabel = (it) => it.label || it.label_zh;   // label = the name in the page language (default items), else the stored one
  const shortLabel = (it) => itemLabel(it).replace(/（[^）]*）/g, "").replace(/\([^)]*\)/g, "").replace(/^吃了/, "").split(" / ")[0].trim() || itemLabel(it);   // i18n-ok: journal item labels are data

  function entryValues() {
    const out = {};
    for (const e of S.data[entriesKey()] || []) if (e.date === recordDay()) out[e.key] = e.value;
    return out;
  }

  function tagHtml(it, val, self) {
    const v = val[it.key];
    if (it.kind !== "bool") {
      return `<button class="tag ${v != null ? "on" : ""}" data-num="${esc(it.key)}" ${self ? "" : "disabled"}><span>${esc(shortLabel(it))}</span>${v != null ? `<span class="v">${fmtNum(v, 1)}${esc(it.unit || "")}</span>` : ""}</button>`;
    }
    return `<button class="tag ${v === 1 ? "on" : v === 0 ? "no" : ""}" data-jkey="${esc(it.key)}" aria-pressed="${v === 1}" ${self ? "" : "disabled"}><span>${esc(shortLabel(it))}</span></button>`;
  }

  const recSeg = () => {
    const subs = [["journal", tr("日记")], ["training", tr("训练")], ...(friendsOn() ? [["food", tr("饮食")]] : [])];
    const cur = S.recSub === "food" && !friendsOn() ? "journal" : S.recSub;
    return `<div class="toolbar"><div class="seg seg-range" role="group" aria-label="${tr("记录")}">${subs
      .map(([k, t]) => `<button data-rsub="${k}" aria-pressed="${cur === k}">${t}</button>`).join("")}</div></div>`;
  };
  function bindRecSeg(view) {
    for (const b of $$("[data-rsub]", view)) b.onclick = () => {
      S.recSub = b.dataset.rsub;
      try { localStorage.setItem("be.recSub", S.recSub); } catch (e) { /* private mode */ }
      setTab("record");
    };
  }
  const RENDER_record = async () => recSeg() + (S.recSub === "training" ? await trainingPageHtml() : await journalPageHtml());

  const journalPageHtml = async () => {
    const today = S.todayDate;
    const [freq, items, entries, meals] = await Promise.all([
      load("jfreq", `/v1/users/${ownerPath()}/journal/frequent`).catch((e) => ({ error: e })),
      load("jitems", `/v1/users/${ownerPath()}/journal/items?active_only=true`).catch((e) => ({ error: e })),
      load(`journal:${addDays(today, -1)}`, `/v1/users/${ownerPath()}/journal?start=${addDays(today, -1)}&end=${today}`).catch((e) => ({ error: e })),
      isSelf() ? load("meals", `/v1/users/${ownerPath()}/meal-templates`).catch(() => null) : null,
    ]);
    const self = isSelf();
    const seg = `<div class="toolbar"><div class="seg" role="group" aria-label="${tr("哪一天")}">
      ${[[tr("今天"), 0], [tr("昨天"), 1]].map(([t, n]) => `<button data-rday="${n}" aria-pressed="${n === S.recordDay}">${t}</button>`).join("")}</div>
      <span class="small muted">${shortDay(recordDay())}</span></div>`;
    const err = freq.error || items.error || entries.error;
    if (err) return seg + `<div class="empty">${esc(err.message)}</div>`;
    const val = entryValues();
    const quick = S.recordDay === 0 ? quickHtml(meals, true) : "";   // meal templates only log today
    let html = `<div class="cols2"><div>` + seg + (quick ? `<div class="section-head"><h2>${tr("快捷记录")}</h2></div>${quick}<div style="height:22px"></div>` : "")
      + `<div class="section-head"><h2>${tr("常用")}</h2><button class="link" id="show-all">${S.showAll ? tr("收起") : tr("全部")}</button></div>
      <div class="tags">${freq.map((it) => tagHtml(it, val, self)).join("")}</div>`;
    if (S.showAll) {
      const groups = {};
      for (const it of items) (groups[it.category || "other"] ||= []).push(it);
      html += `<div class="section" style="margin-top:18px">` + Object.entries(groups).map(([cat, list]) => `<div class="cat">${esc(CATEGORY[cat] || cat)}</div>
        <div class="tags">${list.map((it) => tagHtml(it, val, self)).join("")}</div>`).join("") + `</div>`;
    }
    html += `</div><div>`;
    if (self) {
      html += `<div class="section"><button class="card action" id="add-event"><span class="ic">${ICON.pulse}</span><span class="row-title">${tr("记身体事件")}</span><span class="chev">${ICON.chev}</span></button>
        <button class="card action" id="open-ai"><span class="ic">${ICON.jarvis}</span><span class="row-title">${tr("Jarvis 记录")}</span><span class="chev">${ICON.chev}</span></button></div>`;
    }
    return html + `</div></div>`;
  };

  async function writeJournal(key, value) {
    const day = recordDay();
    if (value == null) await api(`/v1/users/me/journal/${day}/${encodeURIComponent(key)}`, { method: "DELETE" });
    else await api(`/v1/users/me/journal/${day}/${encodeURIComponent(key)}`, { method: "PUT", body: { value } });
    const list = (S.data[entriesKey()] || []).filter((e) => !(e.date === day && e.key === key));
    if (value != null) list.push({ date: day, key, value });
    S.data[entriesKey()] = list;
    delete S.data[`jfreq:${S.owner}`];  // the frequent-items ordering is recomputed next time
  }

  function itemOf(key) {
    return (S.data[`jitems:${S.owner}`] || []).find((i) => i.key === key) || { key, label_zh: key };
  }

  function numberSheet(key) {
    const it = itemOf(key), v = entryValues()[key];
    openSheet(shortLabel(it), `<div class="field"><label for="num-in">${esc(itemLabel(it))}${it.unit ? tr("（{u}）", { u: esc(it.unit) }) : ""} · ${shortDay(recordDay())}</label>
      <input class="input" id="num-in" type="number" inputmode="decimal" step="any" min="0" value="${v ?? ""}" placeholder="${tr("没记录")}"></div>
      <div class="btns">${v != null ? `<button class="btn btn-danger" id="num-clear">${tr("清除")}</button>` : ""}<button class="btn btn-primary" id="num-save">${tr("保存")}</button></div>`,
    (root) => {
      const input = $("#num-in", root);
      setTimeout(() => input.focus(), 260);
      const save = async (value) => {
        try { await writeJournal(key, value); closeSheet(); render(); toast(value == null ? tr("已清除") : tr("已记录 {v}", { v: `${value}${it.unit || ""}` })); } catch (err) { toast(err.message); }
      };
      $("#num-save", root).onclick = () => {
        const raw = input.value.trim(), value = raw === "" ? null : Number(raw);
        if (value != null && !(value >= 0)) { toast(tr("请输入不小于 0 的数")); return; }
        save(value);
      };
      const clear = $("#num-clear", root);
      if (clear) clear.onclick = () => save(null);
    });
  }

  function eventSheet() {
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    openSheet(tr("身体事件"), `<form id="event-form" autocomplete="off">
      <div class="field"><label for="ev-title">${tr("发生了什么")}</label><input class="input" id="ev-title" required maxlength="200" placeholder="${tr("例：头晕")}"></div>
      <div class="pair field">
        <div><label for="ev-cat">${tr("类别")}</label><select class="input" id="ev-cat">${Object.entries(EVENT_CATEGORY).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div><label for="ev-part">${tr("部位")}</label><input class="input" id="ev-part" maxlength="50" placeholder="${tr("可不填")}"></div></div>
      <div class="field"><label>${tr("程度")}</label><div class="sev">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-sev="${n}" aria-pressed="false">${n}</button>`).join("")}</div></div>
      <div class="field"><label for="ev-time">${tr("时间")}</label><input class="input" id="ev-time" type="datetime-local" value="${now}" required></div>
      <div class="field"><label for="ev-note">${tr("备注")}</label><textarea class="input" id="ev-note" rows="2" maxlength="2000" placeholder="${tr("可不填")}"></textarea></div>
      <div class="btns"><button class="btn btn-primary" type="submit" id="ev-submit">${tr("保存")}</button></div></form>`,
    (root) => {
      let sev = null;
      for (const b of $$("[data-sev]", root)) b.onclick = () => {
        sev = sev === Number(b.dataset.sev) ? null : Number(b.dataset.sev);
        for (const x of $$("[data-sev]", root)) x.setAttribute("aria-pressed", String(Number(x.dataset.sev) === sev));
      };
      $("#event-form", root).onsubmit = async (e) => {
        e.preventDefault();
        const btn = $("#ev-submit", root);
        btn.disabled = true;
        const t = $("#ev-time", root).value;
        const body = { title: $("#ev-title", root).value.trim(), category: $("#ev-cat", root).value, time_precision: "minute",
          occurred_at: t.length === 16 ? `${t}:00` : t };
        if ($("#ev-part", root).value.trim()) body.body_part = $("#ev-part", root).value.trim();
        if ($("#ev-note", root).value.trim()) body.note = $("#ev-note", root).value.trim();
        if (sev) body.severity = sev;
        try {
          const row = await api("/v1/users/me/events", { method: "POST", body });
          delete S.data[`events:${S.owner}`];
          closeSheet();
          toast(tr("已记录：{t}", { t: row.title }));
        } catch (err) { toast(err.message); btn.disabled = false; }
      };
    });
  }

  function afterRecord(view) {
    bindRecSeg(view);
    if (S.recSub === "training") { afterTraining(view); return; }
    for (const b of $$("[data-rday]", view)) b.onclick = () => { S.recordDay = Number(b.dataset.rday); render(); };
    const all = $("#show-all", view);
    if (all) all.onclick = () => { S.showAll = !S.showAll; render(); };
    if (!isSelf()) return;
    for (const tag of $$("[data-jkey]", view)) {
      tag.onclick = async () => {
        const key = tag.dataset.jkey, on = tag.classList.contains("on");
        const prev = on ? 1 : tag.classList.contains("no") ? 0 : null;
        tag.classList.add("busy");
        try {
          await writeJournal(key, on ? null : 1);
          for (const t of $$(`[data-jkey="${CSS.escape(key)}"]`, view)) { t.classList.toggle("on", !on); t.classList.remove("no"); t.setAttribute("aria-pressed", String(!on)); }
          toast(on ? tr("已撤销 · {name}", { name: shortLabel(itemOf(key)) }) : tr("已记录 · {name}", { name: shortLabel(itemOf(key)) }), tr("撤销"), async () => {
            try { await writeJournal(key, prev); render(); } catch (err) { toast(err.message); }
          });
        } catch (err) { toast(err.message); } finally { tag.classList.remove("busy"); }
      };
    }
    for (const tag of $$("[data-num]", view)) tag.onclick = () => numberSheet(tag.dataset.num);
    bindMeals(view);
    const ev = $("#add-event", view);
    if (ev) ev.onclick = eventSheet;
    const ai = $("#open-ai", view);
    if (ai) ai.onclick = (e) => openAssistant(e);
  }

  // ------------------------------------------------------------------ me
  // The body status card and observation notes come from the body butler's long-term memory (docs/ai-assistant-plan.md 2.2). The detailed profile text is the butler's underlying memory,
  // not shown or edited in the front end; here only status (short status items) and observation notes (deletable) are shown.
  // GET /memory → {status: [{key, label, value, unit, tone, trend, good_direction, group, spark, note, updated_at, source}],
  //               observations: [{id, text, created_at, source, kind, valid_until}], expired_recent, profile_card (unused)};
  // DELETE /memory/observations/{id}. kind=recent notes (with an end date, #112) get their own 「近况」 block; expired ones are only counted.
  const fmtWhen = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString(I18N.locale(), { year: "numeric", month: "numeric", day: "numeric" }); };
  const memPath = () => `/v1/users/${ownerPath()}/memory`;
  const SOURCE_LABEL = { assistant: tr("管家"), butler: tr("管家"), user: tr("你"), import: tr("导入"), rules: tr("规则"), scores: tr("评分"), auto: tr("自动") };
  const TREND_CHAR = { up: "↑", down: "↓", flat: "→" };

  const trendClass = (it) => (!it.trend || it.trend === "flat" || !it.good_direction ? "flat" : it.trend === it.good_direction ? "good" : "bad");
  function sparkSvg(vals, tone) {
    const xs = (vals || []).filter((v) => v != null && v !== "").map(Number).filter((v) => Number.isFinite(v));
    if (xs.length < 2) return "";
    const lo = Math.min(...xs), hi = Math.max(...xs), W = 100, H = 24;
    const d = xs.map((v, i) => `${i ? "L" : "M"}${((i / (xs.length - 1)) * W).toFixed(1)},${(H - 2 - ((v - lo) / (hi - lo || 1)) * (H - 4)).toFixed(1)}`).join("");
    return `<svg class="spark ${tone === "good" || tone === "warn" || tone === "bad" ? tone : ""}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function statusSheet(it) {
    const when = it.updated_at ? shortDay(String(it.updated_at).slice(0, 10)) : "";
    openSheet(it.label, `<div class="stat-v" style="font-size:30px"><span>${esc(it.value ?? "—")}</span>${it.unit ? `<small>${esc(it.unit)}</small>` : ""}
        ${it.trend ? `<span class="trend ${trendClass(it)}">${TREND_CHAR[it.trend] || ""}</span>` : ""}</div>
      ${sparkSvg(it.spark, it.tone).replace('class="spark', 'class="spark big')}
      ${tags([badge("plain", SOURCE_LABEL[it.source] || it.source || tr("管家")), when ? badge("plain", when) : ""].filter(Boolean))}${xpBox}`,
    (root) => explainInto(root, "status", it.key, false));
  }

  // ---- Lab trend sheet: every reading of one test (GET /labs/{key}/history, scope labs). Status cards with lab_key and the lab list open it.
  // Chart: x = sampling date (to scale), y = value, reference range as a band; lab results are filled dots, home meters hollow (larger error).
  // Tap a point -> date, value, source, fasting. Below: every reading with the change from the previous one, coloured by good_direction.
  const LAB_SRC = { lab: tr("化验"), home_device: tr("家测") };
  const LAB_FAST = { true: tr("空腹"), false: tr("饭后") };
  const LAB_ST = { high: ["bad", tr("偏高")], low: ["mid", tr("偏低")], normal: ["good", tr("正常")], abnormal: ["bad", tr("异常")] };
  const labDay = (s) => (String(s).slice(0, 4) === String(S.todayDate || "").slice(0, 4) ? shortDay(s) : `${String(s).slice(0, 4)}/${shortDay(s)}`);
  const labDig = (rows) => Math.min(2, Math.max(0, ...rows.filter((r) => r.value != null).map((r) => (String(r.value).split(".")[1] || "").length)));
  const labTags = (r) => [badge(r.source === "home_device" ? "mid" : "plain", LAB_SRC[r.source] || r.source), r.fasting != null ? badge("plain", LAB_FAST[r.fasting]) : ""].join("");
  function labDelta(r, better, dig) {
    if (r.delta == null) return "";
    const flat = Math.abs(r.delta) < Math.pow(10, -dig) / 2;
    const tone = flat || !better ? "neutral" : (r.delta < 0) === (better === "down") ? "good" : "bad";
    const pct = r.pct == null ? "" : ` · ${r.pct > 0 ? "+" : r.pct < 0 ? "−" : ""}${fmtNum(Math.abs(r.pct), Math.abs(r.pct) < 1 ? 1 : 0)}%`;
    return `<span class="dlt ${tone}">${flat ? "→ 0" : `${r.delta > 0 ? "+" : "−"}${fmtNum(Math.abs(r.delta), dig)}`}${pct}</span>`;
  }
  function labTip(h, r, dig) {
    return `${esc(labDay(r.collected_on))} · <b>${fmtNum(r.value, dig)}</b>${h.unit ? ` ${esc(h.unit)}` : ""} · ${esc(LAB_SRC[r.source] || r.source)}${r.fasting != null ? ` · ${LAB_FAST[r.fasting]}` : ""}${r.lab ? ` <span class="muted">· ${esc(r.lab)}</span>` : ""}`;
  }
  function labChart(h, pts, dig) {
    const W = WIDE.matches ? 500 : 320, H = WIDE.matches ? 180 : 150, T = 10, B = 18, PX = 12;
    const PR = h.ref_low != null || h.ref_high != null ? 30 : PX;   // right margin keeps the reference labels clear of the last point
    const t = (r) => parseDay(r.collected_on).getTime(), t0 = t(pts[0]), t1 = t(pts[pts.length - 1]);
    const refs = [h.ref_low, h.ref_high].filter((v) => v != null);
    let lo = Math.min(...pts.map((r) => r.value), ...refs), hi = Math.max(...pts.map((r) => r.value), ...refs);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1;
    lo -= pad; hi += pad;
    if (lo < 0 && pts.every((r) => r.value >= 0)) lo = 0;
    const cx = (r) => (t1 === t0 ? (W - PR + PX) / 2 : PX + ((t(r) - t0) / (t1 - t0)) * (W - PX - PR));
    const sy = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    let g = "";
    if (refs.length) {
      const inPlot = (y) => Math.max(T, Math.min(H - B, y));   // the reference band never leaves the plot area
      const top = inPlot(sy(h.ref_high ?? hi)), bot = inPlot(sy(h.ref_low ?? lo));
      g += `<rect class="band" x="0" width="${W - PR + 4}" y="${top.toFixed(1)}" height="${Math.max(0, bot - top).toFixed(1)}" rx="3"/>`;
      for (const v of refs) g += `<line class="guide" x1="0" x2="${W - PR + 4}" y1="${sy(v).toFixed(1)}" y2="${sy(v).toFixed(1)}"/><text x="${W}" y="${(sy(v) + 3.5).toFixed(1)}" text-anchor="end">${fmtNum(v, dig)}</text>`;
    }
    g += `<line class="grid" x1="0" x2="${W}" y1="${H - B}" y2="${H - B}"/>`;
    if (pts.length > 1) g += `<path class="raw lab-line" d="${pts.map((r, i) => `${i ? "L" : "M"}${cx(r).toFixed(1)},${sy(r.value).toFixed(1)}`).join("")}"/>`;
    pts.forEach((r, i) => {
      const tone = (LAB_ST[r.status] || [""])[0];
      g += `<circle class="lp ${r.source === "home_device" ? "home" : "lab"} ${tone}" data-i="${i}" cx="${cx(r).toFixed(1)}" cy="${sy(r.value).toFixed(1)}" r="4.2"/>`;
    });
    const last = pts[pts.length - 1];
    g += `<circle class="lp-sel" id="lab-sel" cx="${cx(last).toFixed(1)}" cy="${sy(last.value).toFixed(1)}" r="7.5"/>`;
    const mid = pts.length > 2 ? pts[Math.floor((pts.length - 1) / 2)] : null;
    g += `<text x="0" y="${H - 4}">${esc(labDay(pts[0].collected_on))}</text>${mid && WIDE.matches && cx(mid) > 70 && cx(mid) < W - PR - 70 ? `<text x="${cx(mid).toFixed(1)}" y="${H - 4}" text-anchor="middle">${esc(labDay(mid.collected_on))}</text>` : ""}${pts.length > 1 ? `<text x="${W}" y="${H - 4}" text-anchor="end">${esc(labDay(last.collected_on))}</text>` : ""}`;
    return { svg: `<svg class="chart lab-chart" id="lab-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(h.test_name)}">${g}</svg>`, cx, sy };
  }

  async function labSheet(key, title) {
    openSheet(title || tr("化验"), `<div class="empty faint">…</div>`);
    let h;
    try { h = await load(`labh:${key}`, `/v1/users/${ownerPath()}/labs/${encodeURIComponent(key)}/history`); }
    catch (e) { if ($("#sheet-body")) $("#sheet-body").innerHTML = tags([badge("plain", e.status === 403 ? tr("无权限") : e.status === 404 ? tr("暂无") : short(e.message))]); return; }
    if ($("#sheet-title").textContent !== (title || tr("化验"))) return;   // another sheet opened while loading
    $("#sheet-title").textContent = title || h.test_name;
    const pts = h.rows.filter((r) => r.value != null), dig = labDig(h.rows), last = h.rows[h.rows.length - 1];
    const [tone, word] = LAB_ST[last.status] || ["plain", ""];
    const head = `<div class="xp-cur"><span class="xp-v">${last.value != null ? fmtNum(last.value, dig) : esc(last.value_text || "—")}${last.value != null && h.unit ? `<small>${esc(h.unit)}</small>` : ""}</span>
        ${word ? badge(tone, word) : ""}</div>${tags([badge("plain", labDay(last.collected_on)), labTags(last)])}`;
    const ch = pts.length ? labChart(h, pts, dig) : null;
    const ref = h.ref_low != null && h.ref_high != null ? `${fmtNum(h.ref_low, dig)}–${fmtNum(h.ref_high, dig)}` : h.ref_high != null ? `≤ ${fmtNum(h.ref_high, dig)}` : h.ref_low != null ? `≥ ${fmtNum(h.ref_low, dig)}` : "";
    const chart = ch ? `<div class="lab-tip trend-tip" id="lab-tip">${labTip(h, pts[pts.length - 1], dig)}</div>${ch.svg}
      <div class="legend"><span><i class="k-lab"></i>${tr("化验")}</span><span><i class="k-home"></i>${tr("家测")}</span>${ref ? `<span><i class="tgt-key"></i>${tr("参考")} ${esc(ref)}</span>` : ""}</div>` : "";
    const rows = [...h.rows].reverse().map((r) => `<div class="lab-row">
        <span class="lr-d">${esc(labDay(r.collected_on))}</span>
        <span class="lr-v">${r.value != null ? fmtNum(r.value, dig) : esc(r.value_text || "—")}${r.value != null && r.unit ? `<small> ${esc(r.unit)}</small>` : ""}</span>
        <span class="lr-t">${labTags(r)}</span><span class="lr-c">${labDelta(r, h.good_direction, dig)}</span></div>`).join("");
    $("#sheet-body").innerHTML = `${head}${chart}<div class="cat">${tr("记录 · {n}", { n: h.rows.length })}</div><div class="lab-rows">${rows}</div>${xpBox}`;
    const root = $("#sheet-body");
    explainInto(root, "lab", key, false);
    const svg = $("#lab-chart", root);
    if (!svg) return;
    const pick = (ev) => {
      const rect = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      const xv = ((ev.clientX - rect.left) / rect.width) * vb.width;
      let best = 0;
      pts.forEach((r, i) => { if (Math.abs(ch.cx(r) - xv) < Math.abs(ch.cx(pts[best]) - xv)) best = i; });
      const r = pts[best], sel = $("#lab-sel", svg);
      sel.setAttribute("cx", ch.cx(r).toFixed(1)); sel.setAttribute("cy", ch.sy(r.value).toFixed(1));
      $("#lab-tip", root).innerHTML = labTip(h, r, dig);
    };
    svg.addEventListener("pointerdown", pick);
    svg.addEventListener("pointermove", (ev) => { if (ev.pointerType !== "mouse" || ev.buttons) pick(ev); });
  }

  // Every lab test (GET /labs/summary): name, latest value, status; tap -> trend sheet
  function labsSheet(list) {
    openSheet(tr("化验"), !list.length ? `<div class="empty faint">${tr("暂无")}</div>` : `<div class="group">${list.map((x) => {
      const l = x.latest || {}, [tone] = LAB_ST[x.status] || ["plain"];
      const v = l.value != null ? `${fmtNum(l.value, 2)}${l.unit ? `<small> ${esc(l.unit)}</small>` : ""}` : esc(l.value_text || "—");
      return `<button class="item lab-item" data-lab="${esc(x.test_key)}" data-name="${esc(x.test_name)}"><span class="tone ${tone === "mid" ? "warn" : tone}"></span>
        <span class="row-title">${esc(x.test_name)}</span><span class="val clip">${v}<small class="muted"> · ${esc(labDay(l.collected_on))}</small></span><span class="chev">${ICON.chev}</span></button>`;
    }).join("")}</div>`,
    (root) => { for (const b of $$("[data-lab]", root)) b.onclick = () => labSheet(b.dataset.lab, b.dataset.name); });
  }

  // Values like "偏高 550" ("high 550"): the leading word is small, the number stays large (the card is narrow, a long string in large type won't fit)
  function statValue(it) {
    const v = it.value == null ? "" : String(it.value);
    const m = v.match(/^(\S+?)\s+(-?[\d.,:]+)$/);
    const [pre, num] = m && !/\d/.test(m[1]) ? [m[1], m[2]] : ["", v];
    return `${pre ? `<small class="pre">${esc(pre)}</small>` : ""}<span class="num" data-key="${esc(it.key)}" data-val="${esc(num)}">${esc(num || "—")}</span>`;
  }
  function statusHtml(mem) {
    const list = Array.isArray(mem?.status) ? mem.status : [];
    const head = `<div class="section-head"><h2>${tr("me.status")}</h2></div>`;
    if (!list.length) return `<div class="section">${head}<div class="group"><div class="item"><span class="row-title faint">${tr("common.none")}</span></div></div></div>`;
    const groups = new Map();
    for (const it of list) { const g = it.group || ""; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(it); }
    return `<div class="section">${head}${[...groups].map(([g, items]) => `${g ? `<div class="cat">${esc(g)}</div>` : ""}
      <div class="stat-grid">${items.map((it) => `<button class="stat" ${sheetAttr(() => (it.lab_key ? labSheet(it.lab_key, it.label) : statusSheet(it)))}>
          <span class="stat-l"><span class="tone ${esc(it.tone || "neutral")}"></span><span>${esc(it.label)}</span></span>
          <span class="stat-v">${statValue(it)}${it.unit ? `<small>${esc(it.unit)}</small>` : ""}
            ${it.trend ? `<span class="trend ${trendClass(it)}">${TREND_CHAR[it.trend] || ""}</span>` : ""}</span>
          ${sparkSvg(it.spark, it.tone)}</button>`).join("")}</div>`).join("")}</div>`;
  }

  // When the value differs from last time, roll from the old value to the new one (restrained: 0.5 s, plain numbers only)
  function animateStatus(view) {
    let seen = {};
    try { seen = JSON.parse(localStorage.getItem(`be.status.${S.owner}`) || "{}"); } catch (e) { /* private mode */ }
    const now = {};
    for (const el of $$(".stat-v .num[data-key]", view)) {
      const key = el.dataset.key, raw = el.dataset.val, to = Number(raw), from = Number(seen[key]);
      now[key] = raw;
      if (raw === "" || !Number.isFinite(to) || !Number.isFinite(from) || from === to || seen[key] == null) continue;
      const dig = (raw.split(".")[1] || "").length, t0 = performance.now();
      const step = (t) => {
        const k = Math.min(1, (t - t0) / 500), e = 1 - (1 - k) ** 3;
        el.textContent = (from + (to - from) * e).toFixed(dig);
        if (k < 1) requestAnimationFrame(step); else el.textContent = raw;
      };
      requestAnimationFrame(step);
    }
    try { localStorage.setItem(`be.status.${S.owner}`, JSON.stringify(now)); } catch (e) { /* private mode */ }
  }

  function observationSheet(o) {
    const self = isSelf();
    openSheet(o.kind === "recent" ? tr("近况") : tr("观察笔记"), `<p class="para" style="margin:0;color:var(--text)">${esc(o.text)}</p>
      ${tags([o.kind === "recent" && o.valid_until && badge("plain", untilLabel(o)), fmtWhen(o.created_at) && badge("plain", fmtWhen(o.created_at)), o.source && badge("plain", SOURCE_LABEL[o.source] || o.source)].filter(Boolean))}
      ${self ? `<div class="btns"><button class="btn btn-danger" id="obs-del">${tr("删除这条")}</button></div>` : ""}`,
    (root) => {
      const del = $("#obs-del", root);
      if (!del) return;
      del.onclick = async () => {
        del.disabled = true;
        try {
          await api(`/v1/users/me/memory/observations/${encodeURIComponent(o.id)}`, { method: "DELETE" });
          const m = S.data[`memory:${S.owner}`];
          if (m?.observations) m.observations = m.observations.filter((x) => x.id !== o.id);
          closeSheet(); render(); toast(tr("已删除"));
        } catch (err) { toast(err.status === 404 ? tr("这条已经不在了") : err.message); del.disabled = false; }
      };
    });
  }

  const untilLabel = (o) => tr("到 {d}", { d: I18N.monthDay(parseDay(o.valid_until)) });
  const obsRow = (o) => `<button class="obs" ${sheetAttr(() => observationSheet(o))}><div class="obs-text">${esc(o.text)}</div>
      <div class="obs-meta">${o.kind === "recent" ? `${esc(untilLabel(o))} · ` : ""}${esc(fmtWhen(o.created_at))}${o.source ? ` · ${esc(SOURCE_LABEL[o.source] || o.source)}` : ""}</div></button>`;

  // 近况: notes with an end date (travelling, a cold); shown only while in effect, the expired ones as a count
  // Behind the rollout flag memory_recent: off -> no block, every note stays in 「观察笔记」 as before.
  const recentOn = () => (S.flags || []).includes("memory_recent");
  function recentHtml(mem) {
    if (!recentOn()) return "";
    const recent = (Array.isArray(mem?.observations) ? mem.observations : []).filter((o) => o.kind === "recent");
    const expired = mem?.expired_recent || 0;
    if (!recent.length && !expired) return "";
    const rows = recent.map(obsRow).join("");
    const tail = expired ? `<div class="item"><span class="row-title faint">${tr("已过期 {n} 条（AI 不再参考）", { n: expired })}</span></div>` : "";
    return `<div class="section"><div class="section-head"><h2>${tr("近况")}</h2></div><div class="group">${rows}${tail}</div></div>`;
  }

  function observationsHtml(mem) {
    const none = `<div class="group"><div class="item"><span class="row-title faint">${tr("common.none")}</span></div></div>`;
    const obs = (Array.isArray(mem?.observations) ? mem.observations : []).filter((o) => !recentOn() || o.kind !== "recent");
    return `<div class="section"><div class="section-head"><h2>${tr("me.observations")}</h2>
        ${obs.length > 3 ? `<button class="link" ${sheetAttr(() => openSheet(tr("me.observationsN", { n: obs.length }), `<div class="group">${obs.map(obsRow).join("")}</div>`))}>${tr("common.all", { n: obs.length })}</button>` : ""}</div>
      ${obs.length ? `<div class="group">${obs.slice(0, 3).map(obsRow).join("")}</div>` : none}</div>`;
  }

  // Language switch (Me page, own account only): saved as the profile's `language`, the page reloads in it at once
  const LANG_LABEL = "语言 / Language";   // i18n-ok: both names, readable whichever language the page is in
  const LANGS = [["zh", "中文"], ["en", "English"]];   // i18n-ok: language names shown natively
  const langRow = () => `<div class="item lang-row"><span class="row-title">${LANG_LABEL}</span><div class="seg" role="group" aria-label="${LANG_LABEL}">${LANGS
    .map(([k, t]) => `<button type="button" data-lang="${k}" aria-pressed="${k === I18N.lang}" lang="${k === "zh" ? "zh-CN" : "en"}">${t}</button>`).join("")}</div></div>`;
  async function setLanguage(lang, view) {
    try { await api("/v1/users/me/profile", { method: "PATCH", body: { language: lang } }); }
    catch (err) { toast(err.message); return; }
    if (S.me) S.me.language = lang;
    I18N.markSaved();
    if (I18N.setPref(lang)) { location.reload(); return; }   // strings computed at startup (tabs, constant maps) need a reload
    tellApp(`lang:${lang}`);
    for (const b of $$("[data-lang]", view)) b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
    toast(tr("common.saved"));
  }

  // 「我」 page v2 behind the rollout flag me_settings (issue #140, docs/rollout.md): the four record rows fold into one
  // 「健康档案」 entry, rarely used account-level rows move to the 「设置」 sub-page (S.meSub = "settings"). Plugin rows
  // (meItem) are routed by their element id; a plugin can also say meGroup: "main" | "settings-account" | "settings-ai" |
  // "settings-danger". Rows nobody routed (e.g. a server's private admin plugin) stay at the bottom of the main page.
  const meSettingsOn = () => (S.flags || []).includes("me_settings");
  const ME_ROUTE = { "me-profile": "profile", "me-circles": "main", "me-feedback": "settings-ai", "me-signin": "settings-account",
                     "me-invite": "settings-account", "me-delete-account": "settings-danger" };
  function pluginRows(chev) {
    const out = {};
    for (const p of PLUGINS()) {
      const h = p.meItem?.(chev);
      if (!h) continue;
      const g = p.meGroup || ME_ROUTE[/\bid="([^"]+)"/.exec(h)?.[1]] || "other";
      (out[g] ||= []).push(h);
    }
    return out;
  }
  const meSection = (title, rows) => (rows.filter(Boolean).length
    ? `<div class="section">${title ? `<div class="section-head"><h2>${title}</h2></div>` : ""}<div class="group">${rows.join("")}</div></div>` : "");

  function meSettingsHtml(who) {
    const chev = `<span class="chev">${ICON.chev}</span>`;
    const rows = pluginRows(chev);
    const item = (title, val, attr = "", tail = chev) => `<button class="item" ${attr}><span class="row-title">${title}</span>${val != null ? `<span class="val">${val}</span>` : ""}${tail}</button>`;
    const ver = [S.version?.web, S.version?.api && `API ${S.version.api}`].filter(Boolean).join(" · ");
    return `<div class="me-settings"><div class="sub-head"><button class="sub-back" id="me-back" aria-label="${tr("返回「我」")}">${ICON.chev}<span>${tr("tab.me")}</span></button><h2>${tr("设置")}</h2></div>
      ${meSection(tr("账号"), [isSelf() ? langRow() : "", ...(rows["settings-account"] || [])])}
      ${meSection("AI", rows["settings-ai"] || [])}
      ${meSection(tr("帮助"), [window.BodyIntro ? item(tr("新手引导"), null, 'id="me-intro"') : ""])}
      ${meSection(tr("关于"), [`<div class="item"><span class="row-title">${tr("版本")}</span><span class="val">${esc(ver || "—")}</span></div>`])}
      ${meSection("", [`<button class="item danger" id="me-logout"><span class="row-title">${tr("me.logout")}</span></button>`, ...(rows["settings-danger"] || [])])}
      ${who.self ? "" : `<p class="small faint" style="margin:14px 2px 0">${tr("这里是你自己账号的设置")}</p>`}</div>`;
  }

  const RENDER_me = async () => {
    if (meSettingsOn() && S.meSub === "settings") {
      return meSettingsHtml(S.viewable.find((v) => v.id === S.owner) || { display_name: S.me.display_name, self: true });
    }
    const [mem, supps, events, d, hs, labs, tlog] = await Promise.all([
      load("memory", memPath()).catch((e) => ({ error: e })),
      load("supps", `/v1/users/${ownerPath()}/supplements`).catch((e) => ({ error: e })),
      load("events", `/v1/users/${ownerPath()}/events?start=${addDays(S.todayDate, -364)}&end=${S.todayDate}`).catch((e) => ({ error: e })),
      load("today", todayPath(S.todayDate)).catch(() => null),
      optional("healthspan", WHOOP_API.healthspan()),
      load("labs", `/v1/users/${ownerPath()}/labs/summary`).catch((e) => ({ error: e })),
      load("tlog", `/v1/users/${ownerPath()}/training/sparky`).catch(() => null),
    ]);
    const who = S.viewable.find((v) => v.id === S.owner) || { display_name: S.me.display_name, self: true };
    const computed = d?.scores?.computed_at;
    const item = (title, val, attr = "", tail = `<span class="chev">${ICON.chev}</span>`) => `<button class="item" ${attr}><span class="row-title">${title}</span>${val != null ? `<span class="val">${val}</span>` : ""}${tail}</button>`;
    const updatedVal = computed ? new Date(computed).toLocaleString(I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
    if (meSettingsOn()) {
      const chev = `<span class="chev">${ICON.chev}</span>`;
      const rows = who.self ? pluginRows(chev) : {};
      const archive = [
        item(tr("me.supplements"), supps.error ? "—" : tr("me.activeN", { n: supps.filter((x) => x.status === "active").length }), supps.error ? "" : sheetAttr(() => suppSheet(supps))),
        item(tr("me.events"), events.error ? "—" : `${events.length}`, events.error ? "" : sheetAttr(() => eventsSheet(events))),
        item(tr("me.labs"), labs.error ? "—" : tr("me.labsN", { n: labs.length }), labs.error ? "" : sheetAttr(() => labsSheet(labs))),
        tlog ? item(tr("训练记录"), tlogBadge(tlog), sheetAttr(() => trainingLogSheet(tlog))) : "",
      ].join("");
      const archiveVal = [supps.error ? null : tr("补剂 {n}", { n: supps.filter((x) => x.status === "active").length }),
        labs.error ? null : tr("化验 {n}", { n: labs.length })].filter(Boolean).join(" · ");
      return `<div class="me"><span class="avatar">${esc(who.display_name.slice(0, 1).toUpperCase())}</span>
          <div><div class="me-name">${esc(who.display_name)}</div><div class="small muted">${tr(who.self ? ((S.me.token_scopes || []).includes("write") ? "me.rw" : "me.ro") : "me.family")}</div></div></div>
        ${healthspanHtml(hs)}
        <div class="cols2"><div>${statusHtml(mem)}</div><div>${recentHtml(mem)}${observationsHtml(mem)}
        ${meSection("", [...(rows.profile || []),
          item(tr("健康档案"), esc(archiveVal) || null, sheetAttr(() => openSheet(tr("健康档案"), `<div class="group">${archive}</div>`)) + ' id="me-archive"')])}
        ${meSection("", [who.self && window.BodyAI ? item(tr("me.aiSettings"), esc(window.BodyAI.settingsValue()), 'id="me-ai"') : "",
          IN_APP ? `<a class="item" href="watch2whoop://sync"><span class="row-title">${tr("me.phoneSync")}</span><span class="chev">${ICON.chev}</span></a>` : "",
          item(tr("me.updated"), updatedVal, 'id="me-refresh"'), ...(rows.main || []),
          S.viewable.length > 1 ? item(tr("me.switch"), esc(who.display_name), 'id="me-who"') : ""])}
        ${meSection("", [item(tr("使用手册"), null, 'id="me-handbook"'), ...(rows.other || []), item(tr("设置"), null, 'id="me-settings"')])}
        </div></div>`;
    }
    return `<div class="me"><span class="avatar">${esc(who.display_name.slice(0, 1).toUpperCase())}</span>
        <div><div class="me-name">${esc(who.display_name)}</div><div class="small muted">${tr(who.self ? ((S.me.token_scopes || []).includes("write") ? "me.rw" : "me.ro") : "me.family")}</div></div></div>
      ${healthspanHtml(hs)}
      <div class="cols2"><div>${statusHtml(mem)}</div><div>${recentHtml(mem)}${observationsHtml(mem)}
      <div class="section"><div class="group">
        ${item(tr("me.supplements"), supps.error ? "—" : tr("me.activeN", { n: supps.filter((x) => x.status === "active").length }), supps.error ? "" : sheetAttr(() => suppSheet(supps)))}
        ${item(tr("me.events"), events.error ? "—" : `${events.length}`, events.error ? "" : sheetAttr(() => eventsSheet(events)))}
        ${item(tr("me.labs"), labs.error ? "—" : tr("me.labsN", { n: labs.length }), labs.error ? "" : sheetAttr(() => labsSheet(labs)))}
        ${tlog ? item(tr("训练记录"), tlogBadge(tlog), sheetAttr(() => trainingLogSheet(tlog))) : ""}
      </div></div>
      <div class="section"><div class="group">
        ${isSelf() ? langRow() : ""}
        ${who.self && window.BodyAI ? item(tr("me.aiSettings"), esc(window.BodyAI.settingsValue()), 'id="me-ai"') : ""}
        ${who.self ? PLUGINS().map((p) => p.meItem?.(`<span class="chev">${ICON.chev}</span>`) || "").join("") : ""}
        ${IN_APP ? `<a class="item" href="watch2whoop://sync"><span class="row-title">${tr("me.phoneSync")}</span><span class="chev">${ICON.chev}</span></a>` : ""}
        ${item(tr("使用手册"), null, 'id="me-handbook"')}
        ${window.BodyIntro ? item(tr("新手引导"), null, 'id="me-intro"') : ""}
        ${S.viewable.length > 1 ? item(tr("me.switch"), esc(who.display_name), 'id="me-who"') : ""}
        ${item(tr("me.updated"), computed ? new Date(computed).toLocaleString(I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—", 'id="me-refresh"')}
        <button class="item danger" id="me-logout"><span class="row-title">${tr("me.logout")}</span></button>
      </div></div>
      <div class="version">${esc(tr("me.version", { web: S.version?.web || "", api: S.version?.api || "" }))}</div></div></div>`;
  };

  // Supplements (「我 → 补剂」): own list is editable (add / edit / stop / delete a mistaken entry); someone else's is read-only
  const canEditSupps = () => isSelf() && (S.me?.token_scopes || []).includes("write");
  function suppSheet(list) {
    const edit = canEditSupps();
    const row = (s) => {
      const meta = [s.ingredients, s.dose, s.timing].filter(Boolean).join(" · ");
      const inner = `<div class="ex-name"><span>${esc(s.name)}</span>${badge(s.status === "active" ? "good" : "plain", SUPP_STATUS[s.status] || s.status)}</div>
        ${meta ? `<div class="ex-meta">${esc(meta)}</div>` : ""}`;
      return edit ? `<button class="ex" data-supp="${s.id}">${inner}</button>` : `<div class="ex">${inner}</div>`;
    };
    openSheet(tr("me.supplements"), `${list.map(row).join("") || `<div class="empty faint">${tr("暂无")}</div>`}
      ${edit ? `<div class="btns"><button class="btn" id="supp-add">${tr("添加")}</button></div>` : ""}`,
    (root) => {
      for (const b of $$("[data-supp]", root)) b.onclick = () => suppEditSheet(list.find((s) => String(s.id) === b.dataset.supp));
      const add = $("#supp-add", root);
      if (add) add.onclick = () => suppEditSheet(null);
    });
  }

  async function suppReload(msg) {
    delete S.data[`supps:${S.owner}`];
    let list = null;
    try { list = await load("supps", `/v1/users/${ownerPath()}/supplements`); } catch (e) { /* the list re-loads on the next render */ }
    render();
    if (list) suppSheet(list); else closeSheet();
    toast(msg);
  }

  function suppEditSheet(s) {
    const f = (id, label, v, ph = tr("可不填")) => `<div class="field"><label for="${id}">${label}</label><input class="input" id="${id}" maxlength="200" placeholder="${ph}" value="${esc(v || "")}"></div>`;
    openSheet(s ? tr("编辑补剂") : tr("添加补剂"), `<form id="supp-form" autocomplete="off">
      ${f("sp-name", tr("名称"), s?.name, tr("例：鱼油"))}
      ${f("sp-spec", tr("规格"), s?.ingredients, tr("例：1000mg / 粒"))}
      <div class="pair field"><div><label for="sp-dose">${tr("每日用量")}</label><input class="input" id="sp-dose" maxlength="200" placeholder="${tr("例：1 粒")}" value="${esc(s?.dose || "")}"></div>
        <div><label for="sp-timing">${tr("服用时间")}</label><input class="input" id="sp-timing" maxlength="200" placeholder="${tr("例：随早餐")}" value="${esc(s?.timing || "")}"></div></div>
      <div class="field"><label for="sp-status">${tr("状态")}</label><select class="input" id="sp-status">${Object.entries(SUPP_STATUS).map(([k, v]) => `<option value="${k}" ${k === (s?.status || "active") ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="btns"><button class="btn btn-primary" type="submit" id="sp-save">${tr("保存")}</button></div>
      ${s ? `<div class="btns" id="sp-more">${s.status !== "stopped" ? `<button class="btn" type="button" id="sp-stop">${tr("不再服用")}</button>` : ""}<button class="btn btn-danger" type="button" id="sp-del">${tr("录错了，删除")}</button></div>` : ""}</form>`,
    (root) => {
      const val = (id) => $(id, root).value.trim();
      $("#supp-form", root).onsubmit = async (e) => {
        e.preventDefault();
        const name = val("#sp-name");
        if (!name) { toast(tr("填一个名称")); return; }
        const body = { name, ingredients: val("#sp-spec") || null, dose: val("#sp-dose") || null, timing: val("#sp-timing") || null, status: val("#sp-status") };
        if (body.status === "stopped" && !s?.stopped_on) body.stopped_on = S.todayDate;
        if (!s) for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
        const btn = $("#sp-save", root);
        btn.disabled = true;
        try {
          if (s) await api(`/v1/users/me/supplements/${encodeURIComponent(s.id)}`, { method: "PATCH", body });
          else await api("/v1/users/me/supplements", { method: "POST", body });
          await suppReload(s ? tr("已保存") : tr("已添加：{name}", { name }));
        } catch (err) { toast(err.message); btn.disabled = false; }
      };
      const stop = $("#sp-stop", root);
      if (stop) stop.onclick = async () => {
        stop.disabled = true;
        try {
          await api(`/v1/users/me/supplements/${encodeURIComponent(s.id)}`, { method: "PATCH", body: { status: "stopped", stopped_on: S.todayDate } });
          await suppReload(tr("已停，记录保留"));
        } catch (err) { toast(err.message); stop.disabled = false; }
      };
      const del = $("#sp-del", root);
      if (del) del.onclick = () => {   // confirm inside the sheet (window.confirm does nothing in the app's WebView)
        $("#sp-more", root).outerHTML = `<p class="para">${tr("连同它的日记项一起删除，不能恢复")}</p>
          <div class="btns"><button class="btn" type="button" id="sp-cancel">${tr("取消")}</button><button class="btn btn-danger" type="button" id="sp-del-ok">${tr("确认删除")}</button></div>`;
        $("#sp-cancel", root).onclick = () => suppEditSheet(s);
        const ok = $("#sp-del-ok", root);
        ok.onclick = async () => {
          ok.disabled = true;
          try {
            await api(`/v1/users/me/supplements/${encodeURIComponent(s.id)}`, { method: "DELETE" });
            delete S.data[`jitems:${S.owner}`];
            await suppReload(tr("已删除"));
          } catch (err) {
            if (err.status === 404) await suppReload(tr("这条已经不在了"));
            else { toast(err.message); ok.disabled = false; }
          }
        };
      };
    });
  }

  function eventsSheet(list) {
    openSheet(tr("me.events"), [...list].reverse().map((e) => `<div class="ex"><div class="ex-name"><span>${esc(e.title)}</span>${e.severity ? badge(e.severity >= 3 ? "bad" : "mid", `${e.severity}`) : ""}</div>
      <div class="ex-meta">${esc(new Date(e.occurred_at).toLocaleDateString(I18N.locale()))}${e.category ? ` · ${esc(EVENT_CATEGORY[e.category] || e.category)}` : ""}${e.body_part ? ` · ${esc(e.body_part)}` : ""}</div>${e.resolved_on ? `<div class="rcard-tags" style="margin-top:4px">${badge("good", tr("{d} 好了", { d: shortDay(e.resolved_on) }))}</div>` : ""}
      ${e.note ? `<div class="ex-meta clamp1">${esc(e.note)}</div>` : ""}</div>`).join("") || `<div class="empty faint">${tr("暂无")}</div>`);
  }

  function afterMe(view) {
    animateStatus(view);
    const w = $("#me-who", view);
    if (w) w.onclick = whoSheet;
    const upd = $("#me-refresh", view);
    if (upd) upd.onclick = async () => { window.BodySync?.refresh("me"); await refresh(); toast(tr("common.refreshed")); };
    const settings = $("#me-settings", view), back = $("#me-back", view);   // 「设置」 sub-page (me_settings flag)
    if (settings) settings.onclick = () => { S.meSub = "settings"; render(); window.scrollTo(0, 0); };
    if (back) back.onclick = () => { S.meSub = null; render(); window.scrollTo(0, 0); };
    const ai = $("#me-ai", view);
    if (ai) ai.onclick = () => window.BodyAI.openSettings();
    PLUGINS().forEach((p) => p.bindMe?.(view));
    for (const b of $$("[data-lang]", view)) b.onclick = () => { if (b.getAttribute("aria-pressed") !== "true" || !S.me?.language) setLanguage(b.dataset.lang, view); };   // the highlighted one too while the profile follows the device
    const out = $("#me-logout", view), hb = $("#me-handbook", view);
    if (out) out.onclick = logout;
    if (hb) hb.onclick = () => openHandbook();
    const intro = $("#me-intro", view);
    if (intro) intro.onclick = () => window.BodyIntro.open();   // intro.js (issue #60)
  }

  // ---- Handbook (「我 → 使用手册」, issue #37): /app/handbook/ shown in a full-screen panel with a back button instead of navigating away.
  // Inside the iOS app the WebView has no back gesture and the home-screen PWA has no browser bar, so leaving the page would strand the user;
  // an iframe is blocked by CSP (frame-ancestors 'none'). The handbook is static HTML rendered by the deployer's handbook.py: its <style> and
  // <main> go into a shadow root (its CSS stays isolated), relative image / link URLs are resolved against the handbook's address.
  function handbookPanel() {
    let p = $("#handbook");
    if (p) return p;
    p = document.createElement("div");
    p.id = "handbook"; p.className = "panel"; p.hidden = true;
    p.setAttribute("role", "dialog"); p.setAttribute("aria-modal", "true"); p.setAttribute("aria-label", tr("使用手册"));
    p.innerHTML = `<div class="panel-head">
        <button class="close" id="hb-close" aria-label="${tr("返回")}"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <h2>${tr("使用手册")}</h2><span class="panel-spacer"></span></div>
      <div class="panel-body hb-body" id="hb-body"></div>`;
    document.body.append(p);
    $("#hb-close", p).onclick = closeHandbook;
    const host = $("#hb-body", p);
    host.attachShadow({ mode: "open" }).addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (!a) return;
      e.preventDefault();
      const href = a.getAttribute("href");
      if (href.startsWith("#")) { a.getRootNode().getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView({ behavior: "smooth" }); return; }
      const url = new URL(href, HANDBOOK_URL);
      if (url.origin === location.origin && url.pathname.startsWith(new URL(HANDBOOK_URL).pathname)) loadHandbook(url.href);   // the web edition (handbook/web/)
      else window.open(url.href, "_blank", "noopener");   // outside: the iOS app hands it to the system
    });
    return p;
  }
  const HANDBOOK_URL = new URL("handbook/", location.href).href;
  async function loadHandbook(url) {
    const root = $("#hb-body").shadowRoot;
    root.innerHTML = `<p style="text-align:center;opacity:.5;padding:28px 0">…</p>`;
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      for (const el of doc.querySelectorAll("[src]")) el.setAttribute("src", new URL(el.getAttribute("src"), url).href);
      for (const el of doc.querySelectorAll("a[href]:not([href^='#'])")) el.setAttribute("href", new URL(el.getAttribute("href"), url).href);
      const css = [...doc.querySelectorAll("style")].map((x) => x.textContent).join("\n").replace(/:root\b/g, ":host").replace(/(^|[}\s])body\s*\{/g, "$1.hb{");
      root.innerHTML = `<style>${css}\n:host{display:block;min-height:100%;background:var(--bg)}${IN_APP ? "img{-webkit-touch-callout:none}" : ""}</style><div class="hb">${doc.querySelector("main")?.outerHTML || doc.body.innerHTML}</div>`;
      $("#hb-body").scrollTop = 0;
    } catch (err) {
      root.innerHTML = `<p style="text-align:center;padding:28px 0">${esc(tr("打不开手册（{err}）", { err: err.message }))}</p>`;
    }
  }
  function openHandbook() {
    const p = handbookPanel();
    p.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => p.classList.add("open")));
    loadHandbook(HANDBOOK_URL);
  }
  function closeHandbook() {
    const p = $("#handbook");
    if (!p) return;
    p.classList.remove("open");
    setTimeout(() => { if (!p.classList.contains("open")) p.hidden = true; }, 280);
  }

  // ------------------------------------------------------------------ food: recipes (/recipes from the body butler session, scope nutrition) and later shopping
  // GET /recipes?slot=&tag=&q= → [{id, name, slot, kcal, protein_g, carbs_g, fat_g, prep_min, tags, badges, favorite, can_make}]
  // GET /recipes/{id} (+ ingredients, steps); POST /recipes/{id}/log {date, meal, servings} → {log_id}; DELETE /meal-logs/{log_id}
  // PATCH /recipes/{id} {favorite | can_make}; GET /recipes/meal-plan?date= -> per-meal suggestions {remaining, slots[{slot, logged, target, suggestions[+labels]}]} (issue #43).
  // Field names follow the butler session; be lenient here.
  const SLOTS = [["breakfast", tr("早餐")], ["lunch", tr("午餐")], ["dinner", tr("晚餐")], ["snack", tr("加餐")]];
  const SLOT_NAME = Object.fromEntries([...SLOTS, ["snacks", tr("加餐")]]);
  const slotOf = (r) => { const v = r.slot || (Array.isArray(r.slots) ? r.slots[0] : null) || r.meal || "lunch"; return v === "snacks" ? "snack" : v; };
  const nut = (r, k) => r[k] ?? r.per_serving?.[k] ?? r.nutrition?.[k] ?? (k === "kcal" ? r.calories ?? r.per_serving?.calories ?? r.nutrition?.calories : null);
  const badgeOf = (b) => (typeof b === "string" ? { label: b, tone: "" } : { label: b.label || b.text || "", tone: b.tone || "" });
  const TONE_CLASS = { good: "good", warn: "mid", bad: "bad", neutral: "plain" };
  const defaultMeal = () => { const h = new Date().getHours() + new Date().getMinutes() / 60; return h < 10.5 ? "breakfast" : h < 15 ? "lunch" : h < 21 ? "dinner" : "snack"; };
  const recipePath = () => `/v1/users/${ownerPath()}/recipes`;

  const PURINE = { low: [tr("嘌呤低"), "good"], medium: [tr("嘌呤中"), "warn"], high: [tr("嘌呤高"), "bad"] };
  function recipeBadges(r) {   // health-constraint badges: ready-made from the server (judged against the user's own diet constraints, #76), else the raw flags
    if (r.badges || r.constraints) return (r.badges || r.constraints).map(badgeOf);
    const f = r.constraint_flags || {}, out = [];
    if (PURINE[f.purine_level]) out.push({ label: PURINE[f.purine_level][0], tone: PURINE[f.purine_level][1] });
    if (f.red_meat_g > 0) out.push({ label: tr("红肉 {n}g", { n: fmtNum(f.red_meat_g) }), tone: "warn" });
    if (f.sat_fat_level === "high") out.push({ label: tr("饱和脂肪高"), tone: "bad" });
    return out;
  }
  const plainTags = (r) => (r.tags || []).filter((t) => !/嘌呤|红肉|饱和脂肪|purine|red meat|saturated/i.test(t));   // i18n-ok: tags are data; no longer shown if they duplicate the constraint badges
  function recipeCard(r, slot) {   // slot: a per-meal suggestion (its own short labels, kcal with the rice it needs, opens with that meal selected)
    const badges = (r.labels ? r.labels.map(badgeOf).map((b) => badge(TONE_CLASS[b.tone] || "mid", b.label))
      : [...recipeBadges(r).map((b) => badge(TONE_CLASS[b.tone] || "mid", b.label)), ...plainTags(r).slice(0, 3).map((t) => badge("plain", t))]).join("");
    const kc = r.meal_kcal ?? nut(r, "kcal"), pr = r.meal_protein_g ?? nut(r, "protein_g");
    const meta = [kc != null && `${fmtNum(kc)} kcal${r.with_staple ? ` ${tr("含饭")}` : ""}`, pr != null && `${tr("蛋白")} ${fmtNum(pr)} g`,
      (r.prep_min ?? r.time_min) != null && `${r.prep_min ?? r.time_min}′`].filter(Boolean).join(" · ");
    return `<button class="rcard ${r.can_make === false && !slot ? "off" : ""}" data-recipe="${esc(r.id)}"${slot ? ` data-slot="${esc(slot)}"` : ""}>
      <div class="rcard-top"><span class="rcard-name">${esc(r.name)}</span>${r.favorite ? `<span class="star on" aria-label="${tr("已收藏")}">★</span>` : ""}</div>
      ${meta ? `<div class="rcard-meta">${esc(meta)}</div>` : ""}${badges ? `<div class="rcard-tags">${badges}</div>` : ""}
</button>`;
  }

  function recipeListHtml(list, q) {
    const qq = (q || "").trim().toLowerCase();
    const hit = (r) => !qq || [r.name, ...(r.tags || []), ...(r.ingredients || []).map((x) => x.food || x.name)].some((t) => String(t || "").toLowerCase().includes(qq));
    const shown = list.filter(hit);
    if (!shown.length) return `<div class="empty">${qq ? tr("没有找到") : tr("暂无食谱")}</div>`;
    const by = new Map(SLOTS.map(([k]) => [k, []]));
    for (const r of shown) (by.get(slotOf(r)) || by.get("lunch")).push(r);
    for (const arr of by.values()) arr.sort((a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0));
    return SLOTS.filter(([k]) => by.get(k).length).map(([k, t]) => `<div class="section"><div class="section-head"><h2>${t}</h2><span class="small muted">${by.get(k).length}</span></div>
      <div class="recipe-grid">${by.get(k).map((r) => recipeCard(r)).join("")}</div></div>`).join("");
  }

  const perServing = (s) => s.replace(/^1 (份|serving)/i, "").replace(/^[（(](.*)[）)]$/, "$1") || s;   // i18n-ok: "1 份（…）" from the server
  async function recipeSheet(id, slot) {
    const day = viewDay();   // log to the day the page shows (issue #43: looking back at 10/18 it logged to today, so the page did not change)
    openSheet(tr("食谱"), `<div class="empty faint">…</div>`);
    let r;
    try { r = await load(`recipe${id}`, `${recipePath()}/${encodeURIComponent(id)}`); }
    catch (e) { $("#sheet-body").innerHTML = `<div class="empty faint">${tr("暂无")}</div>`; return; }
    $("#sheet-title").textContent = r.name;
    const self = isSelf();
    let servings = 1, meal = slot || slotOf(r) || defaultMeal();
    const ings = r.ingredients || [];
    const steps = (r.steps || []).map((x) => (typeof x === "string" ? x : x.text || ""));
    const draw = () => {
      const n4 = (l, k, u) => `<div><div class="l">${l}</div><div class="v">${nut(r, k) != null ? fmtNum(nut(r, k) * servings) : "—"}<small class="muted"> ${u}</small></div></div>`;
      $("#sheet-body").innerHTML = `
        <div class="nut4">${n4(tr("热量"), "kcal", "kcal")}${n4(tr("蛋白"), "protein_g", "g")}${n4(tr("碳水"), "carbs_g", "g")}${n4(tr("脂肪"), "fat_g", "g")}</div>
        ${typeof r.per_serving === "string" ? `<div class="small muted" style="margin:-4px 0 10px">${tr("每份")} = ${esc(perServing(r.per_serving))}</div>` : ""}
        ${recipeBadges(r).length ? `<div class="rcard-tags" style="margin-bottom:6px">${recipeBadges(r).map((b) => badge(TONE_CLASS[b.tone] || "mid", b.label)).join("")}</div>` : ""}
        ${ings.length ? `<div class="cat" style="margin-top:12px">${tr("食材")}${servings !== 1 ? ` · ${tr("{n} 份", { n: servings })}` : ""}</div>${ings.map((x) => kv(x.food || x.name, x.grams != null ? `${fmtNum((x.grams * servings) / (r.servings || 1), 1)} g` : esc(x.amount || "—"))).join("")}` : ""}
        ${steps.length ? `<div class="cat" style="margin-top:16px">${tr("做法")}</div><ol class="steps">${steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
        ${self ? `<div class="log-row">
            <div class="seg" role="group" aria-label="${tr("餐次")}">${SLOTS.map(([k, t]) => `<button data-meal-slot="${k}" aria-pressed="${k === meal}">${t}</button>`).join("")}</div>
            <div class="stepper"><button id="sv-minus" aria-label="${tr("少半份")}">−</button><span class="v">${tr("{n} 份", { n: servings })}</span><button id="sv-plus" aria-label="${tr("多半份")}">&#xFF0B;</button></div></div>
          <div class="btns"><button class="btn" id="r-fav">${r.favorite ? tr("★ 已收藏") : tr("☆ 收藏")}</button><button class="btn" id="r-can">${r.can_make === false ? tr("现在做不了") : tr("现在能做")}</button>
            <button class="btn btn-primary" id="r-log">${day !== S.todayDate ? tr("记到 {d}", { d: shortDay(day) }) : tr("记 {n} 份", { n: servings })}</button></div>` : ""}`;
      if (!self) return;
      const root = $("#sheet-body");
      for (const b of $$("[data-meal-slot]", root)) b.onclick = () => { meal = b.dataset.mealSlot; draw(); };
      $("#sv-minus", root).onclick = () => { servings = Math.max(0.5, servings - 0.5); draw(); };
      $("#sv-plus", root).onclick = () => { servings = Math.min(6, servings + 0.5); draw(); };
      $("#r-fav", root).onclick = () => patchRecipe(r, { favorite: !r.favorite }, draw);
      $("#r-can", root).onclick = () => patchRecipe(r, { can_make: r.can_make === false }, draw);
      $("#r-log", root).onclick = () => logRecipe(r, servings, meal, day);
    };
    draw();
  }

  async function patchRecipe(r, body, redraw) {
    try {
      await api(`/v1/users/me/recipes/${encodeURIComponent(r.id)}`, { method: "PATCH", body });
      Object.assign(r, body);
      const list = S.data[`recipes:${S.owner}`];
      const item = Array.isArray(list) && list.find((x) => String(x.id) === String(r.id));
      if (item) Object.assign(item, body);
      redraw();
      if (S.tab === "food") render();
    } catch (err) { toast(err.status === 404 ? tr("暂不可用") : err.message); }
  }

  // Cached data a recipe log changes: that day's intake (/today?date=), the per-meal plan, the recipe list (last eaten)
  const dropFoodDay = (day) => { for (const k of [todayKey(day), "today", `mealplan${day}`, "recipes"]) delete S.data[`${k}:${S.owner}`]; };
  async function logRecipe(r, servings, meal, day, again) {
    try {
      const res = await api(`/v1/users/me/recipes/${encodeURIComponent(r.id)}/log`, { method: "POST", body: { date: day, meal, servings, ...(again ? { allow_duplicates: true } : {}) } });
      closeSheet();
      dropFoodDay(day);
      S.foodSub = null;   // back to the day's intake, where the new entry shows
      render();
      const logId = res?.log_id;
      toast(day !== S.todayDate ? tr("已记到 {d}", { d: shortDay(day) }) : tr("已记 · {n} 份", { n: servings }), logId != null ? tr("撤销") : null, async () => {
        try { await api(`/v1/users/me/meal-logs/${encodeURIComponent(logId)}`, { method: "DELETE" }); dropFoodDay(day); render(); toast(tr("已撤销")); }
        catch (err) { toast(err.message); }
      });
    } catch (err) {
      if (err.status === 409) toast(day !== S.todayDate ? tr("{d} 已记过", { d: shortDay(day) }) : tr("已记过"), tr("再记一份"), () => logRecipe(r, servings, meal, day, true));
      else toast(err.status === 404 ? tr("暂不可用") : err.message);
    }
  }

  // ---- The selected day's intake (nutrition block of /today?date=): totals vs targets, then each entry by meal
  const MEAL_ORDER = ["breakfast", "lunch", "dinner", "snack"];
  const mealSlot = (e) => { const v = String(e.meal_type || "").toLowerCase(); return v === "snacks" ? "snack" : MEAL_ORDER.includes(v) ? v : "snack"; };
  const sumOf = (list, k) => { const xs = list.map((e) => e[k]).filter((v) => v != null); return xs.length ? xs.reduce((a, b) => a + b, 0) : null; };
  function foodEntrySheet(e) {
    const g = (k, u, dig = 1) => (e[k] != null ? `${fmtNum(e[k], dig)} <small class="muted">${u}</small>` : null);
    openSheet(e.food || tr("食物"), `${kv(tr("份量"), e.quantity ? esc(`${fmtNum(e.quantity, 1)} ${e.unit || ""}`) : null)}${e.time ? kv(tr("时间"), esc(e.time)) : ""}
      ${kv(tr("热量"), g("calories", "kcal", 0))}${kv(tr("蛋白质"), g("protein_g", "g"))}${kv(tr("碳水"), g("carbs_g", "g"))}${kv(tr("脂肪"), g("fat_g", "g"))}
      ${e.saturated_fat_g != null ? kv(tr("饱和脂肪"), g("saturated_fat_g", "g")) : ""}${e.fiber_g != null ? kv(tr("纤维"), g("fiber_g", "g")) : ""}
      ${e.sugar_g != null ? kv(tr("糖"), g("sugar_g", "g")) : ""}${e.added_sugar_g != null ? kv(tr("添加糖"), g("added_sugar_g", "g")) : ""}${e.sodium_mg != null ? kv(tr("钠"), g("sodium_mg", "mg", 0)) : ""}
      ${isSelf() && e.id && e.date ? `<div class="btns" id="fe-more">${e.merge_id && window.BodyFlags?.on("meal_merge") ? `<button class="btn" type="button" id="fe-split">${tr("拆开成原来的几条")}</button>` : ""}<button class="btn btn-danger" type="button" id="fe-del">${tr("删除这条")}</button></div>` : ""}`,
    (root) => {
      const split = $("#fe-split", root);
      if (split) split.onclick = () => { closeSheet(); splitMerge(e.merge_id, e.date); };
      const del = $("#fe-del", root);
      if (!del) return;
      del.onclick = () => {   // confirm inside the sheet (window.confirm does nothing in the app's WebView)
        $("#fe-more", root).outerHTML = `<p class="para">${tr("从饮食记录里删除，不能恢复")}</p>
          <div class="btns"><button class="btn" type="button" id="fe-cancel">${tr("取消")}</button><button class="btn btn-danger" type="button" id="fe-del-ok">${tr("确认删除")}</button></div>`;
        $("#fe-cancel", root).onclick = () => foodEntrySheet(e);
        const ok = $("#fe-del-ok", root);
        ok.onclick = async () => {
          ok.disabled = true;
          const done = (msg) => { dropFoodDay(e.date); closeSheet(); render(); toast(msg); };
          try {
            await api(`/v1/users/me/nutrition/entries/${encodeURIComponent(e.id)}?date=${encodeURIComponent(e.date)}`, { method: "DELETE" });
            done(tr("已删除"));
          } catch (err) {
            if (err.status === 404) done(tr("这条已经不在了"));
            else { toast(err.message); ok.disabled = false; }
          }
        };
      };
    });
  }
  function foodDayHtml(d) {
    const n = d?.nutrition;
    if (!n || n.code === "forbidden") return "";
    const head = `<div class="section-head"><h2>${isPast() ? tr("{d}吃了", { d: esc(shortDay(viewDay())) }) : tr("今天吃了")}</h2>${n.logged === false && !n.error ? badge("plain", tr("未记录")) : ""}</div>`;
    if (n.error) return `<div class="section">${head}<div class="card row"><span class="dash">—</span><span class="small faint">${esc(short(n.error))}</span></div></div>`;
    const g = n.goal || {}, i = n.intake || {}, entries = n.entries || [];
    const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
    const m = (label, a, b) => `<div><div class="macro-l"><span>${label}</span><span>${fmtNum(a)}/${fmtNum(b)}</span></div><div class="bar ${b && a > b * 1.1 ? "over" : ""}"><span style="width:${pct(a, b).toFixed(0)}%"></span></div></div>`;
    const sat = sumOf(entries, "saturated_fat_g"), fib = sumOf(entries, "fiber_g"), add = sumOf(entries, "added_sugar_g");
    const extra = [sat != null && [tr("饱和脂肪"), `${fmtNum(sat, 1)}<small> g</small>`], fib != null && [tr("纤维"), `${fmtNum(fib, 1)}<small> g</small>`],
      add != null && [tr("添加糖"), `${fmtNum(add, 1)}<small> g</small>`],
      entries.length && [tr("条目"), `${entries.length}`]].filter(Boolean);
    const total = `<div class="card food-day">
        <div class="kcal" style="margin-top:0"><span class="row-sub">${tr("热量")}</span><span class="v">${fmtNum(i.calories)}<span class="small muted"> / ${fmtNum(g.calories)} kcal</span></span></div>
        <div class="bar ${g.calories && i.calories > g.calories * 1.05 ? "over" : ""}"><span style="width:${pct(i.calories, g.calories).toFixed(0)}%"></span></div>
        <div class="macros">${m(tr("蛋白"), i.protein_g, g.protein_g)}${m(tr("碳水"), i.carbs_g, g.carbs_g)}${m(tr("脂肪"), i.fat_g, g.fat_g)}</div>
        ${extra.length ? `<div class="food-extra">${extra.map(([l, v]) => `<div><span class="l">${l}</span><span class="v">${v}</span></div>`).join("")}</div>` : ""}</div>`;
    const by = new Map(MEAL_ORDER.map((k) => [k, []]));
    for (const e of entries) by.get(mealSlot(e)).push(e);
    const pick = S.foodPick;   // select mode (issue #93): pick entries, then 「存成模板」 / 「合并成一条」
    S.foodDayEntries = entries;
    const row = (e) => (pick && e.id
      ? `<button class="item food-entry pick" data-fpick="${esc(e.id)}" aria-pressed="${pick.has(String(e.id))}"><span class="pchk" aria-hidden="true"></span><span class="f">${esc(e.food || "—")}</span>
          <span class="k">${e.calories != null ? `${fmtNum(e.calories)}<small> kcal</small>` : "—"}</span></button>`
      : `<button class="item food-entry" ${sheetAttr(() => foodEntrySheet(e))}><span class="f">${esc(e.food || "—")}</span>
          <span class="k">${e.calories != null ? `${fmtNum(e.calories)}<small> kcal</small>` : "—"}</span><span class="chev">${ICON.chev}</span></button>`);
    const meals = MEAL_ORDER.filter((k) => by.get(k).length).map((k) => {
      const xs = by.get(k), kc = sumOf(xs, "calories"), pr = sumOf(xs, "protein_g");
      return `<div class="card list food-meal"><div class="food-meal-h"><span class="row-title">${SLOT_NAME[k]}</span>
          <span class="small muted">${kc != null ? `${fmtNum(kc)} kcal` : ""}${pr != null ? ` · ${tr("蛋白")} ${fmtNum(pr)} g` : ""}</span></div>
        ${xs.map(row).join("")}</div>`;
    }).join("");
    const canPick = isSelf() && entries.some((e) => e.id) && window.BodyFlags?.on("meal_merge");   // rollout flag meal_merge
    const pickBtn = canPick ? `<button class="pick-btn" id="fd-pick" type="button">${pick ? tr("完成") : tr("选择")}</button>` : "";
    const bar = pick ? `<div class="pickbar"><span class="small">${tr("已选 {n} 条", { n: pick.size })}</span>
        <button class="btn" id="fp-tpl" type="button"${pick.size ? "" : " disabled"}>${tr("存成模板")}</button>
        <button class="btn btn-primary" id="fp-merge" type="button"${pick.size >= 2 ? "" : " disabled"}>${tr("合并成一条")}</button></div>` : "";
    return `<div class="section">${head.replace("</div>", `${pickBtn}</div>`)}${total}${meals}${bar}</div>`;
  }

  // ---- Per-meal suggestions (GET /recipes/meal-plan, issue #43): each meal not logged yet gets 2 recipes (taste, pantry, health caps)
  function mealPlanHtml(plan) {
    const slots = (plan?.slots || []).filter((s) => s.suggestions?.length);
    if (!slots.length) return "";
    const rem = plan.remaining;
    const rows = slots.map((s) => `<div class="mp-slot"><div class="mp-h"><span class="row-title">${SLOT_NAME[s.slot] || s.slot}</span>
        ${s.target ? `<span class="small muted">${s.target.kcal > 0 ? `≈ ${fmtNum(s.target.kcal)} kcal · ${fmtNum(s.target.protein_g)} g` : tr("已到目标")}</span>` : ""}</div>
      <div class="rec-row">${s.suggestions.map((r) => recipeCard(r, s.slot)).join("")}</div></div>`).join("");
    return `<div class="section"><div class="section-head"><h2>${tr("每餐推荐")}</h2>${rem ? `<span class="small muted">${tr("余 {k} kcal · {p} g 蛋白", { k: fmtNum(Math.max(0, rem.kcal)), p: fmtNum(Math.max(0, rem.protein_g)) })}</span>` : ""}</div>${rows}</div>`;
  }

  // ---- Recipes sub-page (饮食 → 食谱 at the bottom of the page): search + the list by meal
  async function recipesSubHtml() {
    const list = await load("recipes", recipePath()).catch((e) => ({ error: e }));
    const head = `<div class="sub-head"><button class="sub-back" id="food-back" aria-label="${tr("返回饮食")}">${ICON.chev}<span>${tr("饮食")}</span></button><h2>${tr("食谱")}</h2></div>`;
    if (list.error) return head + `<div class="empty">${tr("暂无食谱")}</div>`;
    return head + `<input class="search" id="food-q" type="search" placeholder="${tr("搜索")}" value="${esc(S.foodQ || "")}" aria-label="${tr("搜索食谱")}">
      <div id="food-list">${recipeListHtml(list, S.foodQ)}</div>`;
  }

  const RENDER_food = async () => {
    if (S.foodSub === "recipes" && S.foodMode !== "shopping") return recipesSubHtml();
    const seg = (friendsOn() ? recSeg() : "") + `<div class="toolbar"><div class="seg" role="group" aria-label="${tr("饮食")}">${[["diet", tr("饮食")], ["shopping", tr("采购")]].map(([k, t]) => `<button data-fmode="${k}" aria-pressed="${S.foodMode === k}">${t}</button>`).join("")}</div></div>`
      + PLUGINS().map((p) => p.foodTop?.({ owner: ownerPath(), self: isSelf(), date: S.todayDate }) || "").join("");   // diet-profile.js
    if (S.foodMode === "shopping") {
      const shop = PLUGINS().find((p) => p.foodShop);   // grocery.js
      return seg + (shop ? await shop.foodShop({ owner: ownerPath(), self: isSelf(), date: S.todayDate })
        : `<div class="soon"><span class="glyph">${ICON.cart}</span><div>${tr("即将上线")}</div></div>`);
    }
    const past = isPast();   // looking back: that day's intake + the recipes entry; quick log and suggestions are only for today
    const [list, plan, d, meals] = await Promise.all([
      load("recipes", recipePath()).catch((e) => ({ error: e })),
      past ? null : load(`mealplan${S.todayDate}`, `${recipePath()}/meal-plan?date=${S.todayDate}`).catch(() => null),
      load(todayKey(), todayPath()).catch(() => null),
      isSelf() && !past ? load("meals", `/v1/users/${ownerPath()}/meal-templates`).catch(() => null) : null,
    ]);
    const quick = isSelf() ? quickHtml(meals, true) : "";
    let html = seg + foodDayHtml(d)
      + PLUGINS().map((p) => p.foodDiet?.({ owner: ownerPath(), self: isSelf(), date: S.todayDate, past }) || "").join("");   // water.js
    if (quick) html += `<div class="section"><div class="section-head"><h2>${tr("快捷记录")}</h2></div>${quick}</div>`;
    html += mealPlanHtml(plan);
    const n = Array.isArray(list) ? list.length : null;
    html += `<div class="section"><div class="group"><button class="item" id="food-recipes"><span class="row-title">${tr("食谱")}</span>${n != null ? `<span class="val">${n}</span>` : ""}<span class="chev">${ICON.chev}</span></button></div></div>`;
    return html;
  };

  // ---- Several entries -> a template / one entry (issue #93): POST /nutrition/combine; undo a merge: DELETE /nutrition/merges/{id}
  function combineSheet(action) {
    const picked = (S.foodDayEntries || []).filter((e) => S.foodPick?.has(String(e.id)));
    if (!picked.length) return;
    const kc = sumOf(picked, "calories"), pr = sumOf(picked, "protein_g");
    const def = picked.map((e) => String(e.food || "").replace(/\s*[\uff08(][^\uff08\uff09()]*\d\s*(?:g|\u514b|ml|\u4efd|\u4e2a)[^\uff08\uff09()]*[\uff09)]\s*$/, "")).join(" + ");
    openSheet(action === "merge" ? tr("合并成一条") : tr("存成模板"), `
      <p class="small muted" style="margin:0 0 10px">${tr("{n} 条", { n: picked.length })} · ${kc != null ? `${fmtNum(kc)} kcal` : "—"}${pr != null ? ` · ${tr("蛋白")} ${fmtNum(pr, 1)} g` : ""}</p>
      <div class="field"><label>${tr("名字")}</label><input class="input" id="fp-name" maxlength="${action === "merge" ? 50 : 30}" value="${esc(def.slice(0, action === "merge" ? 50 : 30))}"></div>
      <div class="btns"><button class="btn" type="button" id="fp-cancel">${tr("取消")}</button><button class="btn btn-primary" type="button" id="fp-ok">${action === "merge" ? tr("合并") : tr("存成模板")}</button></div>`,
    (root) => {
      $("#fp-cancel", root).onclick = () => closeSheet();
      const ok = $("#fp-ok", root);
      ok.onclick = async () => {
        ok.disabled = true;
        const day = picked[0].date;
        try {
          const res = await api("/v1/users/me/nutrition/combine", { method: "POST", body: {
            date: day, entry_ids: picked.map((e) => String(e.id)), action, name: $("#fp-name", root).value.trim() || null } });
          S.foodPick = null;
          dropFoodDay(day);
          delete S.data[`meals:${S.owner}`];
          closeSheet();
          render();
          if (res.merge) toast(tr("已合并 · {name}", { name: res.merge.name }), tr("撤销"), () => splitMerge(res.merge.id, day));
          else toast(tr("已存成模板 · {name}", { name: res.template.name }));
        } catch (err) { toast(err.message); ok.disabled = false; }
      };
    });
  }
  async function splitMerge(id, day) {
    try {
      await api(`/v1/users/me/nutrition/merges/${encodeURIComponent(id)}`, { method: "DELETE" });
      dropFoodDay(day);
      render();
      toast(tr("已拆开"));
    } catch (err) { toast(err.message); }
  }
  function bindPick(view) {
    const btn = $("#fd-pick", view);
    if (btn) btn.onclick = () => { S.foodPick = S.foodPick ? null : new Set(); render(); };
    for (const b of $$("[data-fpick]", view)) b.onclick = () => {
      const id = b.dataset.fpick;
      if (S.foodPick.has(id)) S.foodPick.delete(id); else S.foodPick.add(id);
      render();
    };
    const tpl = $("#fp-tpl", view), merge = $("#fp-merge", view);
    if (tpl) tpl.onclick = () => combineSheet("template");
    if (merge) merge.onclick = () => combineSheet("merge");
  }

  function bindRecipes(root) { for (const b of $$("[data-recipe]", root)) b.onclick = () => recipeSheet(b.dataset.recipe, b.dataset.slot); }
  function afterFood(view) {
    bindRecSeg(view);
    for (const b of $$("[data-fmode]", view)) b.onclick = () => { S.foodMode = b.dataset.fmode; S.foodSub = null; render(); };
    PLUGINS().forEach((p) => p.bindFoodTop?.(view));
    if (S.foodMode === "shopping") { PLUGINS().forEach((p) => p.bindFoodShop?.(view)); return; }
    PLUGINS().forEach((p) => p.bindFoodDiet?.(view));
    bindRecipes(view);
    bindMeals(view);
    bindPick(view);
    const open = $("#food-recipes", view), back = $("#food-back", view);
    if (open) open.onclick = () => { S.foodSub = "recipes"; render(); window.scrollTo(0, 0); };
    if (back) back.onclick = () => { S.foodSub = null; render(); window.scrollTo(0, 0); };
    const q = $("#food-q", view);
    if (q) q.oninput = () => {
      S.foodQ = q.value;
      const list = S.data[`recipes:${S.owner}`];
      if (Array.isArray(list)) { $("#food-list", view).innerHTML = recipeListHtml(list, S.foodQ); bindRecipes($("#food-list", view)); }
    };
  }

  // ------------------------------------------------------------------ friends (friends/cards.py, docs/friends-social-plan.md §3.3 / §3.5)
  // Member cards: today (status label, recovery / sleep / strain, trained, last sync) + 7 recovery dots and 7 strain bars;
  // a block the person did not share is not drawn. Tap a card → that person's pages, read-only, with the banner on top.
  const NUDGE = { clap: "👏", sleep: "💤", train: "💪" };
  const NUDGE_LABEL = { clap: tr("棒"), sleep: tr("早点睡"), train: tr("一起练") };
  function weekSvg(week) {
    const w = 300, h = 40, step = w / 7, top = Math.max(14, ...week.map((d) => d.strain || 0));
    let g = "";
    week.forEach((d, i) => {
      const cx = step * i + step / 2;
      g += `<circle class="fd-dot ${d.zone ? ZONE[d.zone] : "none"}" cx="${cx.toFixed(1)}" cy="6" r="4.5"><title>${esc(shortDay(d.date))}${d.recovery != null ? ` · ${d.recovery}` : ""}</title></circle>`;
      const bh = d.strain ? Math.max(2, (d.strain / top) * 22) : 0;
      if (bh) g += `<rect class="fd-bar" x="${(cx - 5).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="10" height="${bh.toFixed(1)}" rx="2"/>`;
    });
    return `<svg class="fd-week" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(tr("最近 7 天"))}">${g}</svg>`;
  }
  function friendCard(m, kinds) {
    const sc = m.scores;
    const nums = sc ? `<div class="fd-nums">
        <span><b class="${sc.zone ? `tone-${ZONE[sc.zone]}` : ""}">${sc.recovery ?? "—"}</b><i>${esc(tr("恢复"))}</i></span>
        <span><b>${sc.sleep_min != null ? hm(sc.sleep_min) : "—"}</b><i>${esc(tr("睡眠"))}</i></span>
        <span><b>${sc.strain != null ? fmtNum(sc.strain, 1) : "—"}</b><i>${esc(tr("负荷"))}</i></span></div>${weekSvg(sc.week)}` : `<p class="fd-none">${esc(tr("未共享"))}</p>`;
    const tagsH = [];
    if (sc?.label) tagsH.push(badge(ZONE[sc.zone] || "plain", sc.label));
    if (m.trained) tagsH.push(badge("plain", tr("已训练")));
    if (sc && !sc.synced_today) tagsH.push(badge("plain", sc.last_date ? tr("上次同步 {d}", { d: shortDay(sc.last_date) }) : tr("未同步")));
    const canOpen = !m.self && S.viewable.some((v) => v.id === m.id);
    const cheers = m.self
      ? (() => { const got = kinds.filter((k) => m.received?.[k]?.length).map((k) => `<span title="${esc(m.received[k].join(", "))}">${NUDGE[k]} ${m.received[k].length}</span>`); return got.length ? `<div class="fd-got">${got.join("")}</div>` : ""; })()
      : `<div class="fd-cheer">${kinds.map((k) => `<button type="button" data-nudge="${k}" data-to="${m.id}" aria-pressed="${(m.sent || []).includes(k)}" aria-label="${esc(NUDGE_LABEL[k])}" title="${esc(NUDGE_LABEL[k])}">${NUDGE[k]}</button>`).join("")}</div>`;
    return `<div class="card fd-card${canOpen ? " tap" : ""}"${canOpen ? ` data-member="${m.id}" role="button" tabindex="0"` : ""}>
      <div class="fd-head"><span class="fd-av">${esc((m.display_name || "?").slice(0, 1).toUpperCase())}</span>
        <span class="fd-name">${esc(m.self ? tr("who.me", { name: m.display_name }) : m.display_name)}</span>${canOpen ? `<span class="chev">${ICON.chev}</span>` : ""}</div>
      ${tagsH.length ? `<div class="rcard-tags">${tagsH.join("")}</div>` : ""}${nums}${cheers}</div>`;
  }
  // Leaderboards (friends/leaderboard.py): scores 0–100 against each person's own baseline; "not shared" and "not enough
  // data" rows have no rank and come last.
  const BOARD = { body: tr("身体状态"), activity: tr("运动"), lifestyle: tr("认真生活"), recomp: tr("瘦体增肌"), longevity: tr("抗衰老") };
  function boardSub(board, s) {
    if (!s || !Object.keys(s).length) return "";
    if (board === "body") return [tr("绿灯 {n}/7", { n: s.green_days }), s.sleep_avg_min != null ? tr("睡眠 {t}", { t: hm(s.sleep_avg_min) }) : ""].filter(Boolean).join(" · ");
    if (board === "activity") return `${tr("负荷比 {r}", { r: fmtNum(s.acwr, 2) })} · ${tr("训练 {n} 天", { n: s.training_days })}`;
    if (board === "lifestyle") return `${tr("按时睡 {n}/{d}", { n: s.on_time, d: s.days })} · ${tr("饮食达标 {n}/{d}", { n: s.food_met, d: s.days })}`;
    if (board === "recomp") return `${tr("{v}%/周", { v: signed(s.rate_pct_week, 2) })} · ${tr("训练量 ×{v}", { v: fmtNum(s.volume_ratio, 2) })} · ${tr("蛋白 {n}/{d}", { n: s.protein_met, d: s.days })}`;
    if (board === "longevity") return [tr("身体年龄差 {v} 岁", { v: signed(s.gap_change_years, 1) }), s.pace != null ? tr("衰老速度 {v}", { v: fmtNum(s.pace, 2) }) : ""].filter(Boolean).join(" · ");
    return "";
  }
  async function boardHtml() {
    const b = await api(`/v1/friends/leaderboard?board=${encodeURIComponent(S.board)}`);
    const seg = `<div class="toolbar"><div class="seg seg-range fd-boards" role="group" aria-label="${esc(tr("排行榜"))}">${b.boards.map((k) =>
      `<button data-board="${k}" aria-pressed="${k === b.board}">${esc(BOARD[k] || k)}</button>`).join("")}</div></div>`;
    const rows = b.rows.map((r) => {
      const ok = r.status === "ok";
      const note = ok ? boardSub(b.board, r.sub) : r.status === "not_shared" ? tr("未共享") : tr("数据不足");
      return `<div class="fd-row${ok ? "" : " off"}${r.self ? " fd-self" : ""}"><span class="fd-rank">${ok ? r.rank : "·"}</span>
        <span class="fd-av">${esc((r.display_name || "?").slice(0, 1).toUpperCase())}</span>
        <span class="fd-rn"><b>${esc(r.self ? tr("who.me", { name: r.display_name }) : r.display_name)}</b><i>${esc(note)}</i></span>
        <span class="fd-sc">${ok ? r.score : "—"}</span>${ok ? `<span class="fd-sbar"><span style="width:${Math.max(2, r.score)}%"></span></span>` : ""}</div>`;
    }).join("");
    return `${seg}<p class="fd-win">${esc(b.window_days === 28 ? tr("近 4 周 · 和自己的基线比") : tr("近 7 天 · 和自己的基线比"))}</p><div class="card fd-board">${rows}</div>`;
  }
  const RENDER_friends = async () => {
    const mode = `<div class="seg" role="group" aria-label="${esc(tr("朋友"))}">${[["cards", tr("成员")], ["board", tr("排行榜")]].map(([k, t]) =>
      `<button data-fmode2="${k}" aria-pressed="${S.friendsMode === k}">${esc(t)}</button>`).join("")}</div>`;
    const manage = `<div class="toolbar fd-bar-top">${mode}<button class="btn" type="button" id="fd-circles">${esc(tr("圈子"))}</button></div>`;
    const ov = await api("/v1/friends/overview");
    S.data.friends = ov;
    if (!ov.has_circles) {
      return `${manage}<div class="empty">${esc(tr("还没有圈子"))}<div class="btns"><button class="btn btn-primary" id="fd-new">${esc(tr("建圈子"))}</button><button class="btn" id="fd-join">${esc(tr("用邀请码加入"))}</button></div></div>`;
    }
    if (S.friendsMode === "board") return manage + await boardHtml();
    const kinds = ov.nudge_kinds || Object.keys(NUDGE);
    return `${manage}<div class="fd-grid">${[ov.me, ...ov.members].map((m) => friendCard(m, kinds)).join("")}</div>`;
  };
  function viewMember(id) {
    S.owner = id; S.backTab = "friends";
    setTab("today");
  }
  function afterFriends(view) {
    window.BodyCircles && (window.BodyCircles.onChange = () => { S.data = {}; refresh(); });
    for (const b of $$("[data-fmode2]", view)) b.onclick = () => { S.friendsMode = b.dataset.fmode2; render(); };
    for (const b of $$("[data-board]", view)) b.onclick = () => {
      S.board = b.dataset.board;
      try { localStorage.setItem("be.board", S.board); } catch (e) { /* private mode */ }
      render();
    };
    const c = $("#fd-circles", view);
    if (c) c.onclick = () => window.BodyCircles?.open();
    const n = $("#fd-new", view), j = $("#fd-join", view);
    if (n) n.onclick = () => window.BodyCircles?.open();
    if (j) j.onclick = () => window.BodyCircles?.open();
    for (const el of $$("[data-member]", view)) {
      el.onclick = (e) => { if (!e.target.closest("[data-nudge]")) viewMember(Number(el.dataset.member)); };
      el.onkeydown = (e) => { if (e.key === "Enter") viewMember(Number(el.dataset.member)); };
    }
    for (const b of $$("[data-nudge]", view)) b.onclick = async (e) => {
      e.stopPropagation();
      if (b.getAttribute("aria-pressed") === "true") return;
      try {
        await api("/v1/friends/nudges", { method: "POST", body: { to: Number(b.dataset.to), kind: b.dataset.nudge } });
        b.setAttribute("aria-pressed", "true");
        toast(tr("已发送"));
      } catch (err) { toast(err.message); }
    };
  }

  const RENDER = { today: RENDER_today, trends: RENDER_trends, friends: RENDER_friends, food: RENDER_food, record: RENDER_record, me: RENDER_me };
  const AFTER = { today: afterToday, trends: afterTrends, friends: afterFriends, food: afterFood, record: afterRecord, me: afterMe };

  // ------------------------------------------------------------------ training history (month calendar; /training/calendar and /training/day from the training history session, scope training)
  // Fields: see system design §16: calendar?month= → {days[{date, today, future, sessions_count, duration_min, workout_kcal, intensity…}],
  //   summary{training_days, total_duration_min, total_workout_kcal, longest_streak…}, sparky_error, hidden}; day/{date} → {sessions, daily}.
  const INTENSITY = { light: [0.32, tr("轻松")], moderate: [0.55, tr("中等")], hard: [0.8, tr("高强度")], max: [1, tr("极限")] };
  const WORKOUT_NAME = {
    traditional_strength_training: tr("力量训练"), functional_strength_training: tr("功能性力量"), strength_training: tr("力量训练"), strength: tr("力量训练"),
    basketball: tr("篮球"), swimming: tr("游泳"), pool_swimming: tr("游泳"), walking: tr("步行"), running: tr("跑步"), hiking: tr("徒步"), cycling: tr("骑行"),
    high_intensity_interval_training: "HIIT", hiit: "HIIT", stair_climbing: tr("爬楼"), stairs: tr("爬楼"), elliptical: tr("椭圆机"), rowing: tr("划船机"),
    yoga: tr("瑜伽"), flexibility: tr("拉伸"), stretching: tr("拉伸"), core_training: tr("核心训练"), cooldown: tr("放松"), mixed_cardio: tr("有氧"),
    incline_walk: tr("爬坡"), treadmill: tr("跑步机"), other: tr("其他"),
  };
  const workoutName = (s) => s.name || s.type_name || WORKOUT_NAME[String(s.type || "").toLowerCase()] || s.type || tr("训练");
  const monthOf = (iso) => iso.slice(0, 7);
  const addMonths = (ym, n) => { const [y, m] = ym.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };
  const durText = (m) => (m == null ? "" : m >= 60 ? `${Math.floor(m / 60)}h${String(Math.round(m % 60)).padStart(2, "0")}` : `${Math.round(m)}′`);

  function markSvg(day) {
    const it = INTENSITY[day?.intensity];
    if (!it || !(day.sessions_count > 0)) return `<svg class="mark" viewBox="0 0 28 28" aria-hidden="true"></svg>`;
    return `<svg class="mark" viewBox="0 0 28 28" aria-hidden="true"><circle class="i" cx="14" cy="14" r="${(7 + it[0] * 4).toFixed(1)}" fill-opacity="${it[0]}"/>
      ${day.intensity === "max" ? `<circle class="ring" cx="14" cy="14" r="13"/>` : ""}</svg>`;
  }

  async function calHtml(ym) {
    let data = null, err = null;
    try { data = await load(`cal${ym}`, `/v1/users/${ownerPath()}/training/calendar?month=${ym}`); }
    catch (e) { err = e; }
    const [y, m] = ym.split("-").map(Number);
    const first = new Date(y, m - 1, 1), daysIn = new Date(y, m, 0).getDate();
    const lead = (first.getDay() + 6) % 7;   // weeks start on Monday
    const by = Object.fromEntries((data?.days || []).map((d) => [d.date, d]));
    const today = S.todayDate;
    let cells = I18N.weekdaysMon().map((w) => `<div class="cal-wd">${w}</div>`).join("");
    cells += Array.from({ length: lead }, () => `<div></div>`).join("");
    for (let k = 1; k <= daysIn; k++) {
      const iso = `${ym}-${String(k).padStart(2, "0")}`, d = by[iso];
      const trained = d && d.sessions_count > 0 && d.intensity !== "rest";
      const isToday = d?.today ?? iso === today, future = d?.future ?? iso > today;
      cells += `<button class="cday ${isToday ? "today" : ""} ${future ? "future" : ""}" data-day="${iso}" ${future ? "disabled" : ""}
          aria-label="${esc(I18N.monthDay(new Date(y, m - 1, k)))}${trained ? esc(tr("，训练 {n} 分钟，{i}", { n: Math.round(d.duration_min || 0), i: INTENSITY[d.intensity]?.[1] || "" })) : ""}">
        <span class="n">${k}</span>${markSvg(trained ? d : null)}<span class="dur">${trained && d.duration_min > 0 ? durText(d.duration_min) : ""}</span></button>`;
    }
    const sm = data?.summary || {};
    const sum = (l, v, u = "") => `<div><div class="l">${l}</div><div class="v">${v ?? "—"}${v != null && u ? `<small>${u}</small>` : ""}</div></div>`;
    return `<div class="month-nav"><div class="m">${esc(I18N.yearMonth(y, m))}</div><div class="arrows">
        <button id="cal-prev" aria-label="${tr("picker.prevMonth")}"><svg viewBox="0 0 16 16"><path d="M10 3.5L5.5 8 10 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <button id="cal-next" aria-label="${tr("picker.nextMonth")}" ${ym >= monthOf(today) ? "disabled" : ""}><svg viewBox="0 0 16 16"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div></div>
      <div class="msum">${sum(tr("训练"), sm.training_days, tr("天"))}${sum(tr("时长"), sm.total_duration_min != null ? hm(sm.total_duration_min) : null)}${sum(tr("训练消耗"), (sm.total_workout_kcal ?? sm.total_active_kcal) != null ? fmtNum(sm.total_workout_kcal ?? sm.total_active_kcal) : null, "kcal")}${sum(tr("最长连续"), sm.longest_streak, tr("天"))}</div>
      <div class="cal-grid" id="cal-grid">${cells}</div>
      ${data?.sparky_error ? `<div class="cal-note">${tr("力量 —")}</div>` : ""}
      ${data ? `<div class="cal-legend">${Object.entries(INTENSITY).map(([, [o, t]]) => `<span><i style="opacity:${o}"></i>${t}</span>`).join("")}</div>`
        : `<div class="cal-note">${tr("暂无")}</div>`}`;
  }

  async function renderCal(dir) {
    const body = $("#rec-cal");
    if (!body) return;
    const ym = S.calMonth;
    let html;
    try { html = await calHtml(ym); }
    catch (e) { html = `<div class="cal-note">${esc(e.message)}</div>`; }
    if (body !== $("#rec-cal") || ym !== S.calMonth) return;   // page changed or another month was picked meanwhile
    body.innerHTML = html;
    if (dir) $("#cal-grid", body)?.classList.add(dir > 0 ? "slide-l" : "slide-r");
    const prev = $("#cal-prev", body), next = $("#cal-next", body);
    if (prev) prev.onclick = () => { S.calMonth = addMonths(S.calMonth, -1); renderCal(-1); };
    if (next) next.onclick = () => { if (S.calMonth < monthOf(S.todayDate)) { S.calMonth = addMonths(S.calMonth, 1); renderCal(1); } };
    for (const b of $$("[data-day]", body)) b.onclick = () => daySheet(b.dataset.day);
  }

  function bindCalSwipe(el) {   // swipe left / right on the month grid to change month
    let x0 = null, y0 = null;
    el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
    el.addEventListener("touchend", (e) => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = null;
      if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx)) return;
      if (dx < 0 && S.calMonth < monthOf(S.todayDate)) { S.calMonth = addMonths(S.calMonth, 1); renderCal(1); }
      else if (dx > 0) { S.calMonth = addMonths(S.calMonth, -1); renderCal(-1); }
    }, { passive: true });
  }

  // ---- Record tab, training page: this week (plan + what was trained), suggestions, caution exercises, history calendar
  // /training/week?date= → {start, end, plan, days[{date, planned[{name, exercises}], rest_day, change{from[], source, reason}|null}], suggestions[{name, level, title, next_weight_kg}], cautions[{name, status, reason, cues}]};
  // what was trained this week comes from /training/calendar?start=&end=.
  const weekStart = (iso) => addDays(iso, -((parseDay(iso).getDay() + 6) % 7));
  const planShort = (planned) => planned[0].name.replace(/（[^）]*）|\([^)]*\)/g, "").split(" / ")[0].trim() || planned[0].name;   // week strip: first session, short (i18n-ok: plan names are data)

  // Offline (network error, ApiError status 0) is not "no data": say so and offer a retry (the page-wide handling is #77)
  const offlineHtml = () => `<div class="empty faint">${esc(tr("离线，连上网络后重试"))}</div>
    <div class="btns" style="justify-content:center;margin-top:4px"><button class="btn" data-retry>${esc(tr("common.retry"))}</button></div>`;

  async function swapInPlan(fromSlug, toSlug, btn) {
    btn.disabled = true;
    try {
      const res = await api(`/v1/users/me/training/plan/swap`, { method: "POST", body: { from_exercise: fromSlug, to_exercise: toSlug } });
      delete S.data[`tweek${S.todayDate}:${S.owner}`];
      delete S.data[todayKey()];
      closeAltPreview(); closeSheet(); render();
      toast(tr("已替换为 {name}", { name: res.to_exercise?.name_zh || toSlug }));
    } catch (err) { toast(err.status === 0 ? tr("离线，连上网络后重试") : err.message); btn.disabled = false; }
  }

  // ---- Preview card of a safe alternative (#67): opens over the detail sheet from data the detail response already carries
  // (thumbnail, names, muscles, equipment, recommended weight), so it also works from the cached detail when offline.
  function closeAltPreview() { $(".pv-scrim")?.remove(); }
  document.addEventListener("keydown", (e) => {   // capture: Escape closes the card first, not the sheet under it
    if (e.key === "Escape" && $(".pv-scrim")) { e.stopImmediatePropagation(); closeAltPreview(); }
  }, true);

  function altPreview(a, fromSlug) {
    closeAltPreview();
    const names = (list) => (list || []).map((x) => tr(x)).join(tr("、"));   // catalog labels: English in i18n-en.js
    const ld = a.load;
    const weight = ld && a.status !== "banned" && (ld.kg != null || ld.reason === "no_body_weight" || ld.basis === "bodyweight")
      ? (ld.kg != null ? `${fmtNum(ld.kg, 1)} kg${ld.basis === "estimate" ? ` <small class="muted">${esc(tr("估算"))}</small>` : ""}` : esc(tr(ld.label))) : null;
    const pic = a.thumb ? `<img class="pv-img" src="${esc(a.thumb)}" alt="${esc(a.name_zh)}" loading="lazy" decoding="async">`
      : window.BodyMuscleMap ? `<div class="pv-map">${window.BodyMuscleMap.html(a.primary_muscles, a.secondary_muscles)}</div>` : "";
    const st = a.status === "banned" ? badge("bad", tr("禁用")) : a.status === "caution" ? badge("mid", tr("注意")) : "";
    const [main, other] = I18N.lang === "en" && a.name_en ? [a.name_en, a.name_zh] : [a.name_zh, a.name_en];   // both names, page language first
    const el = document.createElement("div");
    el.className = "pv-scrim";
    el.innerHTML = `<div class="pv-card" role="dialog" aria-modal="true" aria-label="${esc(a.name_zh)}">
      <button class="pv-x" data-pv="close" aria-label="${esc(tr("common.close"))}">×</button>
      ${pic}
      <div class="pv-name">${esc(main)} ${st}</div>
      ${other && other !== main ? `<div class="pv-en">${esc(other)}</div>` : ""}
      ${(a.primary_muscles_zh || []).length ? kv(tr("主肌群"), esc(names(a.primary_muscles_zh))) : ""}
      ${(a.equipment_zh || []).length ? kv(tr("器械"), esc(names(a.equipment_zh))) : ""}
      ${weight ? kv(tr("推荐重量"), weight) : ""}
      <div class="btns"><button class="btn" data-pv="detail">${esc(tr("查看详情"))}</button>
        ${isSelf() && a.status !== "banned" ? `<button class="btn btn-primary" data-pv="swap">${esc(tr("替换成这个"))}</button>` : ""}</div>
    </div>`;
    el.addEventListener("click", (e) => {
      const act = e.target.closest("[data-pv]")?.dataset.pv;
      if (e.target === el || act === "close") closeAltPreview();
      else if (act === "detail") { closeAltPreview(); exerciseDetailSheet(a.slug); }
      else if (act === "swap") swapInPlan(fromSlug, a.slug, e.target.closest("button"));
    });
    document.body.appendChild(el);
    $(".pv-card .btn", el)?.focus({ preventScroll: true });
  }

  async function exerciseDetailSheet(slugOrName) {
    openSheet(tr("动作详情"), `<div class="empty faint">…</div>`);
    let ex;
    try { ex = await load(`ex:${slugOrName}`, `/v1/users/${ownerPath()}/training/exercises/${encodeURIComponent(slugOrName)}`); }
    catch (e) {
      $("#sheet-body").innerHTML = e.status === 0 ? offlineHtml() : `<div class="empty faint">${esc(e.message || tr("暂无详情"))}</div>`;
      const retry = $("[data-retry]", $("#sheet-body"));
      if (retry) retry.onclick = () => exerciseDetailSheet(slugOrName);
      return;
    }
    $("#sheet-title").textContent = ex.name_zh || slugOrName;
    const stBadge = ex.status === "banned" ? badge("bad", tr("禁用")) : ex.status === "caution" ? badge("mid", tr("注意")) : badge("good", tr("正常"));
    const muscles = [...(ex.equipment_zh || []).map((q) => badge("plain", q)),
      ex.movement_pattern_zh ? badge("plain", ex.movement_pattern_zh) : ""].filter(Boolean).join("");
    const imgs = ex.image_urls || [];
    const carousel = imgs.length ? `<div class="ex-car" id="ex-car">${imgs.map((u, i) => `<img src="${esc(u)}" alt="${esc(`${ex.name_zh} ${i + 1}`)}" loading="lazy" decoding="async">`).join("")}</div>
      ${imgs.length > 1 ? `<div class="ex-dots">${imgs.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>` : ""}` : "";
    const map = window.BodyMuscleMap ? window.BodyMuscleMap.html(ex.primary_muscles, ex.secondary_muscles) : "";
    const ld = ex.load;
    const loadV = ld && ex.status !== "banned" && (ld.kg != null || ld.basis === "bodyweight")
      ? `${ld.kg != null ? `${fmtNum(ld.kg, 1)} kg` : esc(ld.label)}${ld.kg != null ? ` <small class="muted">${esc([ld.per, `× ${ld.target_reps}`, ld.label].filter(Boolean).join(" · "))}</small>` : ""}` : null;
    const basis = ld && ex.status !== "banned" ? loadBasisText(ld) : null;
    const basisV = basis ? `${esc(basis)}${ld.body_weight_kg ? ` <small class="muted">${esc(`${fmtNum(ld.body_weight_kg, 1)} kg × ${ld.bw_fraction}`)}</small>` : ""}` : null;
    const recent = (ex.recent || []).map((r) => kv(shortDay(r.date), esc(r.sets.join(" · ")))).join("");
    const joints = Object.entries(ex.joint_flags_zh || {}).map(([k, v]) => badge(v === "high" ? "bad" : "mid", `${k}${v === "high" ? tr("高") : tr("中")}`)).join("");
    const steps = (ex.instructions_zh || []).map((s) => `<li>${esc(s)}</li>`).join("");
    const alts = (ex.alternatives || []).map((a, i) => `<button class="item alt-row" data-alt="${i}">
      ${a.thumb ? `<img class="alt-thumb" src="${esc(a.thumb)}" alt="" loading="lazy" decoding="async">` : `<span class="alt-thumb"></span>`}
      <div style="flex:1;min-width:0"><div class="row-title">${esc(a.name_zh)}</div>
        <div class="small muted">${esc([...(a.equipment_zh || []), ...(a.primary_muscles_zh || [])].map((x) => tr(x)).join(" · "))}</div></div>
      ${a.load_text && a.status !== "banned" ? `<span class="val">${esc(a.load?.kg != null ? `${a.load.basis === "estimate" ? "≈ " : ""}${fmtNum(a.load.kg, 1)} kg` : tr(a.load_text))}</span>` : ""}
      <span class="chev">${ICON.chev}</span></button>`).join("");
    $("#sheet-body").innerHTML = `
      ${ex.name_en && ex.name_en !== ex.name_zh ? `<div class="small muted" style="margin:-4px 0 8px">${esc(ex.name_en)}</div>` : ""}
      ${carousel}
      <div class="rcard-tags" style="margin-bottom:8px">${stBadge}${muscles}${joints}</div>
      ${ex.reason ? kv(tr("限制原因"), esc(ex.reason)) : ""}
      ${loadV ? kv(tr("推荐重量"), loadV) : ""}
      ${basisV ? kv(tr("依据"), basisV) : ""}
      ${(ex.primary_muscles_zh || []).length ? kv(tr("主肌群"), esc(ex.primary_muscles_zh.join(tr("、")))) : ""}
      ${(ex.secondary_muscles_zh || []).length ? kv(tr("次肌群"), esc(ex.secondary_muscles_zh.join(tr("、")))) : ""}
      ${map}
      ${ex.cues ? kv(tr("动作要点"), esc(ex.cues)) : ""}
      ${recent ? `<div class="cat" style="margin-top:12px">${tr("最近")}</div>${recent}` : ""}
      ${steps ? `<div class="cat" style="margin-top:12px">${tr("执行步骤")}</div><ol class="steps">${steps}</ol>` : ""}
      ${alts ? `<div class="cat" style="margin-top:14px">${tr("同模式安全替代动作")}</div><div class="card list">${alts}</div>` : ""}`;
    const root = $("#sheet-body");
    const car = $("#ex-car", root);
    if (car) {   // free-exercise-db shows start / end positions: alternate them like a slow animation until the user swipes
      const dots = $$(".ex-dots i", root);
      const at = () => Math.round(car.scrollLeft / Math.max(1, car.clientWidth));
      car.addEventListener("scroll", () => dots.forEach((d, i) => d.classList.toggle("on", i === at())), { passive: true });
      let auto = imgs.length > 1 ? setInterval(() => {
        if (!car.isConnected) { clearInterval(auto); return; }
        car.scrollTo({ left: ((at() + 1) % imgs.length) * car.clientWidth, behavior: "smooth" });
      }, 1600) : null;
      const stop = () => { if (auto) { clearInterval(auto); auto = null; } };
      car.addEventListener("touchstart", stop, { passive: true });
      car.addEventListener("pointerdown", stop);
    }
    for (const b of $$("[data-alt]", root)) b.onclick = () => altPreview(ex.alternatives[Number(b.dataset.alt)], ex.slug);
  }

  async function exerciseLibrarySheet() {
    openSheet(tr("动作库"), `<div class="empty faint">…</div>`);
    let list = [], q = "", grp = "", seq = 0, timer = null;
    const GROUPS_FILTER = [["", tr("全部")], ["back", tr("背")], ["chest", tr("胸")], ["legs", tr("腿")], ["shoulders", tr("肩")], ["arms", tr("手臂")], ["core", tr("核心")]];
    const fetchList = async () => {   // searched on the server: the catalog has ~950 exercises (curated + free-exercise-db)
      const my = ++seq, qs = new URLSearchParams({ limit: "100" });
      if (q.trim()) qs.set("q", q.trim());
      if (grp) qs.set("muscle", grp);
      try { const res = await load(`exlib:${qs}`, `/v1/users/${ownerPath()}/training/exercises?${qs}`); if (my === seq) { list = res.exercises || []; draw(); } }
      catch (e) { if (my === seq) $("#sheet-body").innerHTML = `<div class="empty faint">${esc(e.message)}</div>`; }
    };
    const draw = () => {
      const shown = list;
      $("#sheet-body").innerHTML = `
        <input class="search" id="ex-q" type="search" placeholder="${esc(tr("搜中英文动作名、别名…"))}" value="${esc(q)}" style="margin-bottom:8px">
        <div class="tags" style="margin-bottom:10px">${GROUPS_FILTER.map(([k, t]) => `<button class="tag ${grp === k ? "on" : ""}" data-exgrp="${k}"><span>${t}</span></button>`).join("")}</div>
        <div class="card list" id="ex-list">${shown.map((x) => `<button class="item" data-ex-open="${esc(x.slug)}">
          <div style="flex:1;min-width:0;text-align:left"><div class="row-title">${esc(x.name_zh)} ${x.status === "banned" ? badge("bad", tr("禁用")) : x.status === "caution" ? badge("mid", tr("注意")) : ""}</div>
            <div class="small muted">${esc([...(x.primary_muscles_zh || []), ...(x.equipment_zh || [])].join(" · "))}</div></div>
          <span class="chev">${ICON.chev}</span></button>`).join("") || `<div class="empty faint">${tr("没有匹配的动作")}</div>`}</div>`;
      const root = $("#sheet-body");
      for (const b of $$("[data-exgrp]", root)) b.onclick = () => { grp = b.dataset.exgrp; fetchList(); };
      for (const b of $$("[data-ex-open]", root)) b.onclick = () => exerciseDetailSheet(b.dataset.exOpen);
      const inp = $("#ex-q", root);
      if (inp) {
        inp.oninput = () => { q = inp.value; clearTimeout(timer); timer = setTimeout(fetchList, 250); };
        if (q) { inp.focus(); inp.setSelectionRange(q.length, q.length); }
      }
    };
    fetchList();
  }

  function splitPickerSheet(wk) {
    const splits = wk?.available_splits || [];
    let chosen = wk?.plan?.split_type || (splits[2]?.split_type || "4_day_upper_lower");
    const draw = () => {
      const cur = splits.find((s) => s.split_type === chosen) || splits[0];
      openSheet(tr("周训练分化计划"), `
        <div class="tags" style="margin-bottom:10px">${splits.map((s) => `<button class="tag ${s.split_type === chosen ? "on" : ""}" data-split="${esc(s.split_type)}"><span>${esc(s.name)}</span></button>`).join("")}</div>
        ${cur ? `<div class="small muted" style="margin-bottom:10px">${esc(cur.summary)}</div>
          <div class="card list">${(cur.days || []).map((d) => kv(`${d.weekday_zh} · ${d.name}`, tr("{n} 动作", { n: d.exercise_count }))).join("")}</div>` : ""}
        <div class="btns"><button class="btn btn-primary" id="save-split">${tr("启用该周计划（自动避开禁用动作）")}</button></div>`,
      (root) => {
        for (const b of $$("[data-split]", root)) b.onclick = () => { chosen = b.dataset.split; draw(); };
        const save = $("#save-split", root);
        if (save) save.onclick = async () => {
          save.disabled = true;
          try {
            const res = await api(`/v1/users/me/training/plan`, { method: "PUT", body: { split_type: chosen, auto_swap_banned: true, sync_sparky: true } });
            delete S.data[`tweek${S.todayDate}:${S.owner}`];
            delete S.data[todayKey()];
            closeSheet(); render();
            // Saved even when the training log sync failed (issue #66 FB-4): say so instead of a plain OK
            toast(res.sparky_sync && res.sparky_sync.synced === false && res.sparky_sync.link !== undefined
              ? tr("已启用：{name} · 训练记录未同步", { name: res.saved?.name || chosen })
              : tr("已启用：{name}", { name: res.saved?.name || chosen }));
          } catch (err) { toast(err.message); save.disabled = false; }
        };
      });
    };
    draw();
  }

  // ---- Training log link, issue #66 FB-4: state on the training page and under Me, self-service connect (/training/sparky).
  const TLOG_STATE = {
    connected: ["good", tr("已连接")],
    not_linked: ["mid", tr("未连接")],
    unreadable: ["bad", tr("读不到")],
  };
  const tlogBadge = (st) => { const [tone, word] = TLOG_STATE[st?.state] || TLOG_STATE.unreadable; return badge(tone, word); };
  function trainingLogSheet(st) {
    const html = kv(tr("训练记录"), tlogBadge(st))
      + (st?.hint ? `<div class="small muted" style="margin:10px 0">${esc((I18N.lang === "en" && st.hint_en) || st.hint)}</div>` : "")
      + `<div class="small muted" id="tlog-err" hidden></div>`
      + (st?.can_connect ? `<div class="btns"><button class="btn btn-primary" id="tlog-connect">${tr("连接")}</button></div>` : "");
    openSheet(tr("训练记录"), html, (body) => {
      const b = $("#tlog-connect", body);
      if (b) b.onclick = () => connectTrainingLog(b, body);
    });
  }
  async function connectTrainingLog(btn, body) {
    btn.disabled = true;
    btn.textContent = tr("连接中…");
    try {
      const r = await api(`/v1/users/me/training/sparky/connect`, { method: "POST" });
      if (r.state === "connected") {
        closeSheet();
        toast(tr("训练记录已连接"));
        for (const k of Object.keys(S.data)) if (/^(tweek|tlog|today|tcal)/.test(k)) delete S.data[k];
        render();
        return;
      }
      const e = $("#tlog-err", body);
      const en = I18N.lang === "en";
      e.textContent = (en && (r.error_en || r.hint_en)) || r.error || r.hint || "";
      e.hidden = false;
    } catch (err) {
      toast(err.message);
    }
    btn.disabled = false;
    btn.textContent = tr("连接");
  }

  async function trainingPageHtml() {
    const today = S.todayDate, mon = weekStart(today), sun = addDays(mon, 6);
    const [wk, wcal] = await Promise.all([
      load(`tweek${today}`, `/v1/users/${ownerPath()}/training/week?date=${today}`).catch((e) => ({ error: e.message })),
      load(`tcal${mon}`, `/v1/users/${ownerPath()}/training/calendar?start=${mon}&end=${sun}`).catch(() => null),
    ]);
    if (!S.calMonth) S.calMonth = monthOf(today);
    const done = Object.fromEntries((wcal?.days || []).map((d) => [d.date, d]));
    const planDays = Object.fromEntries((wk.days || []).map((d) => [d.date, d]));
    let week = "";
    for (let k = 0; k < 7; k++) {
      const iso = addDays(mon, k), p = planDays[iso], d = done[iso];
      const trained = d && d.sessions_count > 0 && d.intensity !== "rest";
      const label = p?.planned?.length ? planShort(p.planned) : p?.rest_day ? tr("休") : "";
      week += `<button class="wday ${iso === today ? "today" : ""} ${iso > today ? "future" : ""} ${p?.change ? "changed" : ""}" data-wday="${iso}">
        <span class="wd">${WEEK_MON[k]}</span><span class="n">${parseDay(iso).getDate()}</span>${markSvg(trained ? d : null)}
        <span class="pl">${esc(label)}</span></button>`;
    }
    const sm = wcal?.summary || {};
    const rd = wk.readiness;
    const rdHtml = rd && rd.tier && rd.tier !== "unknown"
      ? `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)"><div class="rcard-tags">${badge(rd.tier === "green" ? "good" : rd.tier === "red" ? "bad" : "mid", rd.label)}</div><div class="small muted" style="margin-top:4px">${esc(rd.advice)}</div></div>` : "";
    const volEntries = Object.values(wk.volume_by_group || {});
    const volHtml = volEntries.length
      ? `<div class="rcard-tags" style="margin-top:10px">${volEntries.map((g) => badge(g.status === "optimal" ? "good" : "plain", `${g.name_zh} ${tr("{n}组", { n: g.direct_sets })}`)).join("")}</div>` : "";
    const headActions = `<span style="display:flex;gap:10px;align-items:center">
      ${wk.plan ? `<span class="small muted">${esc(wk.plan.name)}</span>` : ""}
      ${isSelf() && wk.available_splits?.length ? `<button class="link" id="pick-split">${wk.plan ? tr("切换分化") : tr("选周计划")}</button>` : ""}
      <button class="link" id="open-exlib">${tr("动作库")}</button></span>`;
    const tlog = wk.sparky && wk.sparky.state !== "connected"
      ? `<div class="card list" style="margin-top:10px"><button class="item" id="tlog-row"><span class="row-title">${tr("训练记录")}</span><span class="val">${tlogBadge(wk.sparky)}</span><span class="chev">${ICON.chev}</span></button></div>`
      : "";
    const left = `<div class="section"><div class="section-head"><h2>${tr("本周")}</h2>${headActions}</div>
        <div class="card"><div class="week">${week}</div>
        <div class="msum" style="margin:14px 0 0">${[[tr("训练"), sm.training_days, tr("天")], [tr("时长"), sm.total_duration_min != null ? hm(sm.total_duration_min) : null, ""],
          [tr("消耗"), (sm.total_workout_kcal ?? sm.total_active_kcal) != null ? fmtNum(sm.total_workout_kcal ?? sm.total_active_kcal) : null, "kcal"],
          [tr("计划"), (wk.days || []).filter((x) => x.planned.length).length || null, tr("天")]]
          .map(([l, v, u]) => `<div><div class="l">${l}</div><div class="v">${v ?? "—"}${v != null && u ? `<small>${u}</small>` : ""}</div></div>`).join("")}</div>${volHtml}${rdHtml}
        ${wk.load_history_error && wk.plan ? tags([badge("mid", tr("训练记录读不到 · 重量为估算"))]) : ""}</div>${tlog}</div>`;
    const sugg = (wk.suggestions || []).map((x) => `<button class="item" ${sheetAttr(() => openSheet(x.name, kv(tr("建议"), esc(x.title)) + (x.next_weight_kg != null ? kv(tr("下次重量"), `${fmtNum(x.next_weight_kg, 1)} kg`) : "")))}>
        <span class="row-title">${esc(x.name)}</span><span class="val">${badge(x.level === "warning" ? "mid" : "good", x.next_weight_kg != null ? `→ ${fmtNum(x.next_weight_kg, 1)} kg` : tr("建议"))}</span><span class="chev">${ICON.chev}</span></button>`);
    const caut = (wk.cautions || []).map((x) => `<button class="item" ${sheetAttr(() => exerciseDetailSheet(x.slug || x.name))}>
        <span class="row-title">${esc(x.name)}</span><span class="val">${badge(x.status === "banned" ? "bad" : "mid", x.status === "banned" ? tr("禁用") : tr("注意"))}</span><span class="chev">${ICON.chev}</span></button>`);
    const recs = wk.error ? `<div class="section"><div class="section-head"><h2>${tr("建议")}</h2></div><div class="empty faint">${esc(short(wk.error, "—"))}</div></div>`
      : `<div class="section"><div class="section-head"><h2>${tr("建议")}</h2></div>${sugg.length || caut.length ? `<div class="card list">${[...sugg, ...caut].join("")}</div>` : `<div class="empty faint">${tr("暂无")}</div>`}</div>`;
    const hist = `<div class="section"><div class="section-head"><h2>${tr("训练历史")}</h2></div><div id="rec-cal"><div class="empty faint">…</div></div></div>`;
    return `<div class="cols2"><div>${left}${recs}</div><div>${hist}</div></div>`;
  }

  function planSheet(iso, day) {
    const html = (day?.change ? kv(tr("原计划"), esc(day.change.from?.join(" · ") || tr("休息"))) : "") + (day?.planned?.length ? day.planned.map((p) => {
      const single = p.exercises.length === 1 && p.exercises[0].name === p.name;
      return `${single ? "" : `<div class="small muted" style="margin:14px 0 2px">${esc(p.name)}</div>`}${p.exercises.map(exerciseRow).join("")}`;
    }).join("") : `<div class="empty faint" style="padding:12px">${day?.rest_day ? tr("休息") : tr("无计划")}</div>`);
    openSheet(`${tr("计划")} · ${fmtDay(iso)}`, html, bindExRows);
  }

  function afterTraining(view) {
    const wk = S.data[`tweek${S.todayDate}:${S.owner}`];
    const planDays = Object.fromEntries((wk?.days || []).map((d) => [d.date, d]));
    for (const b of $$("[data-wday]", view)) b.onclick = () => {
      const iso = b.dataset.wday;
      if (iso > S.todayDate) planSheet(iso, planDays[iso]); else daySheet(iso, planDays[iso]);
    };
    const pick = $("#pick-split", view);
    if (pick && wk) pick.onclick = () => splitPickerSheet(wk);
    const lib = $("#open-exlib", view);
    if (lib) lib.onclick = () => exerciseLibrarySheet();
    const tl = $("#tlog-row", view);
    if (tl && wk) tl.onclick = () => trainingLogSheet(wk.sparky);
    const c = $("#rec-cal", view);
    if (c) { bindCalSwipe(c); renderCal(0); }
  }

  function zonesHtml(z) {
    const vals = Array.isArray(z) ? z : z && typeof z === "object" ? Object.keys(z).sort().map((k) => z[k]) : [];
    const total = vals.reduce((s, v) => s + (Number(v) || 0), 0);
    if (!total) return "";
    return `<div class="zones">${vals.map((v, i) => `<span style="width:${((Number(v) || 0) / total * 100).toFixed(1)}%;opacity:${(0.25 + i * 0.18).toFixed(2)}" title="${tr("区间 {i}：{n} 分钟", { i: i + 1, n: Math.round(v) })}"></span>`).join("")}</div>
      <div class="zones-l"><span>${tr("心率区间")}</span><span>${vals.map((v, i) => `Z${i + 1} ${Math.round(Number(v) || 0)}′`).join(" · ")}</span></div>`;
  }

  function sessionHtml(s) {
    const it = INTENSITY[s.intensity];
    const time = [s.start && s.end ? `${String(s.start).slice(11, 16) || s.start}–${String(s.end).slice(11, 16) || s.end}` : "", s.duration_min != null ? tr("{n} 分钟", { n: Math.round(s.duration_min) }) : ""].filter(Boolean).join(" · ");
    const stat = (l, v) => `<div><div class="l">${l}</div><div class="v">${v ?? "—"}</div></div>`;
    const exs = (s.exercises || []).filter((x) => x.name !== workoutName(s) || (x.sets || []).some((z) => z.reps != null || z.weight_kg)).map((x) => {   // entries with the same name as the workout and no sets (the cardio line) are not listed again
      const sk = x.status_key || x.status;   // training history endpoint: status is Chinese, status_key is banned / caution / active
      const st = sk === "banned" ? badge("bad", tr("禁用")) : sk === "caution" ? badge("mid", tr("注意")) : "";
      const sets = (x.sets || []).map((z) => (z.weight_kg ? `${fmtNum(z.weight_kg, 1)}×${z.reps ?? "?"}` : z.reps != null ? tr("{n} 次", { n: z.reps })
        : [z.duration_s ? tr("{n} 分钟", { n: Math.round(z.duration_s / 60) }) : "", z.distance_km ? `${fmtNum(z.distance_km, 1)} km` : ""].filter(Boolean).join(" "))).filter(Boolean).join(" · ");
      return `<div class="ex ${sk === "banned" ? "banned" : ""}"><div class="ex-name"><span>${esc(x.name)}</span>${st}</div>${sets ? `<div class="ex-meta">${esc(sets)}</div>` : ""}</div>`;
    }).join("");
    return `<div class="sess"><div class="sess-head"><span class="sess-name">${esc(workoutName(s))}</span>${it ? badge("plain", it[1]) : ""}</div>
      ${time ? `<div class="sess-time">${esc(time)}</div>` : ""}
      ${s.active_kcal == null && s.avg_hr == null && s.max_hr == null ? `<div class="rcard-tags" style="margin-top:6px">${badge("plain", tr("无手表数据"))}</div>`
        : `<div class="sess-stats">${stat(tr("活动能量"), s.active_kcal != null ? `${fmtNum(s.active_kcal)}<small class="muted"> kcal</small>` : null)}
        ${stat(tr("平均心率"), s.avg_hr != null ? fmtNum(s.avg_hr) : null)}${stat(tr("最高心率"), s.max_hr != null ? fmtNum(s.max_hr) : null)}</div>`}
      ${zonesHtml(s.hr_zone_min)}
      ${s.volume_kg ? `<div class="ex-meta" style="margin-top:8px">${tr("容量")} ${fmtNum(s.volume_kg)} kg</div>` : ""}
      ${exs ? `<div class="exs">${exs}</div>` : ""}
</div>`;
  }

  async function daySheet(iso, plan) {
    openSheet(fmtDay(iso), `<div class="empty faint">…</div>`);
    let d;
    try { d = await load(`tday${iso}`, `/v1/users/${ownerPath()}/training/day/${iso}`); }
    catch (e) { $("#sheet-body").innerHTML = `<div class="empty faint">${tr("暂无")}</div>`; return; }
    const dl = d.daily || {};
    const sessions = d.sessions || [];
    $("#sheet-body").innerHTML = (d.sparky_error ? tags([badge("mid", tr("力量 —"))]) : "")
      + (sessions.length ? sessions.map(sessionHtml).join("") : `<div class="empty faint" style="padding:12px">${tr("无训练")}</div>`)
      + `<div style="height:14px"></div><div>`
      + [[tr("训练消耗"), dl.workout_kcal != null ? `${fmtNum(dl.workout_kcal)} kcal` : null],
         [tr("全天活动能量"), dl.active_kcal != null ? `${fmtNum(dl.active_kcal)} kcal${dl.flags?.daily_incomplete ? ` ${badge("mid", tr("不全"))}` : ""}` : null],
         [tr("全天总消耗"), dl.total_kcal != null ? `${fmtNum(dl.total_kcal)} kcal` : null], [tr("锻炼分钟"), dl.exercise_min != null ? tr("{n} 分钟", { n: fmtNum(dl.exercise_min) }) : null],
         [tr("步数"), dl.steps != null ? fmtNum(dl.steps) : null], ["strain", dl.strain != null ? fmtNum(dl.strain, 1) : null],
         [tr("肌肉负荷"), dl.muscle_load != null ? `${fmtNum(dl.muscle_load)} kg` : null], [tr("次日恢复"), dl.recovery_next_day ?? null]]
        .filter(([, v]) => v != null).map(([l, v]) => kv(l, v)).join("")
      + (plan?.change ? kv(tr("原计划"), esc(plan.change.from?.join(" · ") || tr("休息"))) : "")
      + (plan?.planned?.length ? `<div class="cat">${tr("计划")}</div>${plan.planned.map((p) => p.exercises.map(exerciseRow).join("")).join("")}` : "") + `</div>`;
  }

  // ------------------------------------------------------------------ Ask the butler (body butler Claude Code session, Remote Control)
  function setupAssistant() {
    const a = $("#ask");
    if (S.assistantUrl) { a.href = S.assistantUrl; a.target = "_blank"; }
    else { a.href = "#"; a.removeAttribute("target"); }
  }
  // Jarvis follows the user's pick (ai/choice.py, switched in AI settings or the chat panel; ai.js reports changes through onAiChange,
  // which updates S.ai / S.assistantUrl at once): API type or the fallback AI -> in-page chat (ai.js); the body butler (deployer only) -> open Claude;
  // subscription type without a link (family members) -> explain that they chat in their own AI app; nothing configured -> prompt to go to "Me -> AI settings" (「我 → AI 设置」).
  function openAssistant(e) {
    if (window.BodyAI?.handlesAsk()) { e?.preventDefault(); window.BodyAI.openChat(); return; }
    // Open the butler link from code for every entry point: inside the iOS app (WKWebView) a target=_blank <a> does nothing,
    // while window.open reaches the app's navigation delegate, which hands claude.ai to the Claude app.
    if (S.assistantUrl) { e?.preventDefault(); window.open(S.assistantUrl, "_blank", "noopener"); return; }
    e?.preventDefault();
    if (S.ai?.mode === "mcp") {
      openSheet("Jarvis", `${kv("AI", esc(S.ai.display_name || tr("jarvis.subscription")))}${tags([badge("good", tr("jarvis.connected")), badge("plain", tr("jarvis.askInApp"))])}
        ${window.BodyAI ? `<div class="btns"><button class="btn" id="ask-switch">${tr("jarvis.switch")}</button></div>` : ""}`,
        (root) => { const b = $("#ask-switch", root); if (b) b.onclick = () => { closeSheet(); window.BodyAI.openSettings(); }; });
      return;
    }
    const canSet = !!window.BodyAI && S.owner === S.me?.id;
    openSheet("Jarvis", `${kv("AI", null)}${tags([badge("plain", tr("jarvis.unset"))])}
      ${canSet ? `<div class="btns"><button class="btn btn-primary" id="ask-setup">${tr("jarvis.setup")}</button></div>` : ""}`,
      (root) => { const b = $("#ask-setup", root); if (b) b.onclick = () => { closeSheet(); window.BodyAI.openSettings(); }; });
  }
  $("#ask").addEventListener("click", (e) => openAssistant(e));

  // ------------------------------------------------------------------ startup
  // The Watch2Whoop app keeps its one-time login code until the page reports the outcome on its JS channel (`logged-in` after
  // /v1/web/me works, `login-failed` when the server rejected the code), so a load that was interrupted is retried next time.
  const tellApp = (msg) => { try { if (IN_APP) window.Watch2Whoop.postMessage(msg); } catch (e) { /* older app */ } };
  async function loginFromHash() {
    const m = /(?:^#|&)login=([A-Za-z0-9-]{8,40})/.exec(location.hash);
    if (!m) return;
    history.replaceState(null, "", location.pathname + location.search);
    try { await redeemLogin(m[1]); }
    catch (err) {
      if (err.status !== 401) return;   // offline etc.: the app tries again with the same code
      // used / expired (this device may still be signed in: boot() then shows the page and the box stays hidden)
      tellApp("login-failed");
      const msg = LOGIN_401[err.kind] || err.message;
      if (window.BodyAccount) window.BodyAccount.showOther(msg);   // under 「其他登录方式」 once the login screen shows
      else setTimeout(() => { if (!$("#login").hidden) $("#login-error").textContent = msg; }, 0);
    }
  }

  async function boot(keepTab) {
    let me;
    const plugged = loadPluginFiles();
    try { me = await api("/v1/web/me"); }
    catch (err) {
      if (err.status === 401) return;
      $("#login").hidden = true; $("#app").hidden = false;
      $("#view").innerHTML = `<div class="empty">${esc(err.message)}<div class="btns"><button class="btn" id="retry">${tr("common.retry")}</button></div></div>`;
      $("#retry").onclick = () => boot();
      return;
    }
    // Language picked on the login page and the profile has none yet: save it to the profile (it then follows the account)
    const picked = I18N.unsaved();
    if (picked && me.me && !me.me.language) {
      try { await api("/v1/users/me/profile", { method: "PATCH", body: { language: picked } }); I18N.markSaved(); }
      catch (e) { /* e.g. account still pending: keep it on this device, try again next time */ }
      me.me.language = picked;
    } else I18N.markSaved();
    // Profile language (null = follow the device): if it differs from what this page was drawn in, reload once in the right language
    if (I18N.setPref(me.me?.language)) { location.reload(); return; }
    tellApp(`lang:${me.me?.language || ""}`);   // the app's native screens follow the profile language ("" = the system language)
    if (me.me?.status === "pending" && window.BodyAccount) {   // self-signed-up, no invite code yet (account.js)
      $("#login").hidden = true; $("#app").hidden = true;
      window.BodyAccount.showPending(() => boot());
      return;
    }
    S.me = me.me; S.viewable = me.viewable; S.todayDate = me.today;
    S.flags = me.flags || []; S.experiments = me.experiments || {};
    applyTabs();
    S.assistantUrl = me.assistant_url || null; S.version = me.version || null; S.ai = me.ai || { mode: "none" };
    window.BodyAI?.init({ api, openSheet, closeSheet, toast, esc, refresh, page: pageId, isSelf, onAiChange: (ai, url) => { S.ai = ai || { mode: "none" }; S.assistantUrl = url || null; setupAssistant(); render(); }, me: me.me, ai: me.ai || { mode: "none" }, assistantUrl: S.assistantUrl });
    await plugged;
    PLUGINS().forEach((p) => p.init?.({ api, openSheet, closeSheet, toast, esc, openHandbook, openAiSettings: () => window.BodyAI?.openSettings(), reload: refresh }, me));
    setupAssistant();
    if (!S.viewable.some((v) => v.id === S.owner)) S.owner = S.me.id;
    $("#login").hidden = true;
    $("#app").hidden = false;
    tellApp("logged-in");
    if (keepTab) { render(); return; }
    let tab = "today";
    try { tab = localStorage.getItem("be.tab") || "today"; } catch (e) { /* private mode */ }
    setTab(TABS[tab] ? tab : "today");
  }

  // Feature flags and A/B variants of the signed-in user (rollout/, docs/rollout.md), from /v1/web/me. A gated feature draws
  // its entry only when BodyFlags.on("key"); an experiment asks BodyFlags.variant("key") when it renders: one exposure per
  // page load is counted and the variant comes back (null = not in the experiment: draw the normal page).
  const exposed = new Set();
  window.BodyFlags = {
    on: (key) => (S.flags || []).includes(key),
    variant: (key) => {
      const v = (S.experiments || {})[key] || null;
      if (v && !exposed.has(key)) {
        exposed.add(key);
        api(`/v1/users/me/experiments/${encodeURIComponent(key)}/exposure`, { method: "POST" }).catch(() => {});
      }
      return v;
    },
  };

  // Redraw when the window crosses the wide-screen breakpoint (charts have different sizes)
  WIDE.addEventListener?.("change", () => { if (S.me) { $("#view").dataset.shown = ""; render(); } });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => { /* no offline support */ });
  // Refresh after more than 10 minutes in the background; the log page and sheets may have input in progress, so don't redraw them
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && S.me && Date.now() - (boot.last || 0) > 10 * 60 * 1000) {
      boot.last = Date.now(); S.data = {};
      if (S.tab !== "record" && $("#sheet").hidden) boot(true);
    }
  });
  boot.last = Date.now();
  // The Watch2Whoop app's "setup info" / QR code carries a one-time login code: opening /app/#login=<code> logs in automatically (the code is wiped from the address bar immediately)
  loginFromHash().then(boot);
  // The app may set #login=<code> on a page that is already open (same-document navigation, no reload)
  window.addEventListener("hashchange", () => { if (/(?:^#|&)login=/.test(location.hash)) loginFromHash().then(() => boot()); });
})();
