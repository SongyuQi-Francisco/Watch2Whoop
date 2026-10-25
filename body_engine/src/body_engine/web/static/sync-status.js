/* 「开启数据同步」 card and 「同步状态」 panel (GET /v1/users/me/sync/overview, sync/overview.py).
   - Nothing ever synced (someone who skipped the app's setup): a card above the Today / Trends / Friends pages with one button
     (「一键开启」) until the first data arrives, and once per user a sheet with the same button the first time the page opens.
   - One tap: apps that announce `Watch2WhoopFeatures.syncConnect` (1.0.8+, docs/sync-onboarding-app-requirements.md) get
     watch2whoop://sync-connect?ow=…&code=… and do permission → connect → first sync themselves, reporting progress to
     window.BodySyncStatus.onApp({phase, percent, error}); 1.0.6 / 1.0.7 get their 「手机同步」 page (watch2whoop://sync), where
     「连接同步」 asks for Health access and starts the sync (the code is shown with a copy button for accounts that need it).
     A browser: the card says to open the app on the iPhone. The code only lives in this page's memory and the app's link.
   - The panel (the status line under the Today title, a banner when the sync is stuck, 「我 → 同步状态」): the last sync,
     the newest data per main type, whether today's scores are in, and when stuck one short reason with its buttons
     (overview.ACTIONS: 重新连接 = sync-repair.js, 立即重试 = POST /sync/refresh via sync.js, 去授权 / 开启睡眠追踪 = the data
     status steps, 打开同步页 / 立即上传 = the app). A broken OW link keeps sync-repair.js's own banner.
   - window.BodySyncStatus.device(report): the app (1.0.8+) hands its phone report to the server through the page
     (PUT /v1/users/me/sync/device).
   Registered in window.BodyPlugins (init / onTab / meItem / bindMe). The pure helpers are module.exports for
   tests/test_sync_overview.py. */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);
  const $ = (s, r = document) => r.querySelector(s);
  const ASK_KEY = "w2w.syncSetup.asked";   // + user id: the first-open sheet was shown (once per user on this device)
  const RECHECK_MS = 20000, WATCH_MS = 10 * 60 * 1000, MIN_GAP_MS = 60000, RETRY_POLL_MS = 5000, RETRY_POLL_MAX_MS = 3 * 60 * 1000;
  const CARD_TABS = ["today", "trends", "friends"];
  // Banners only for a sync that is stuck: credentials have sync-repair.js's banner, consent the consent sheet, a missing
  // type the status dot and the empty recovery ring, not connected the card
  const BANNER_CODES = ["server", "app_closed", "background_off", "low_power", "locked", "lagging"];

  /** "刚刚 / x 分钟前 / x 小时前 / x 天前" for a number of minutes (null = "—"). */
  function agoText(min) {
    if (min == null) return "—";
    if (min < 1) return tr("刚刚");
    if (min < 60) return tr("{n} 分钟前", { n: Math.floor(min) });
    if (min < 48 * 60) return tr("{n} 小时前", { n: Math.floor(min / 60) });
    return tr("{n} 天前", { n: Math.floor(min / 1440) });
  }
  /** The newest data of one type, as a short label. now: Date. */
  function typeAgo(t, now) {
    if (t.last_at) return agoText((now - new Date(t.last_at)) / 60000);
    if (t.days_since == null) return tr("从没收到");
    return t.days_since === 0 ? tr("今天") : t.days_since === 1 ? tr("昨天") : tr("{n} 天前", { n: t.days_since });
  }
  /** The link one button opens inside the app, or null (handled in the page). f = window.Watch2WhoopFeatures. */
  function actionLink(action, f, inApp) {
    f = f || {};
    if (action === "sync_now") return !inApp ? "watch2whoop://sync" : f.syncNow ? "watch2whoop://sync-now" : "watch2whoop://sync";
    if (action === "open_app") return "watch2whoop://sync";
    if (action === "open_settings") return inApp && f.appSettings ? "watch2whoop://app-settings" : null;
    if (action === "authorize") return inApp && f.healthSettings ? "watch2whoop://health-settings" : null;
    return null;
  }
  /** The link that connects in one step (1.0.8+: watch2whoop://sync-connect, the app does permission → connect → sync). */
  function connectLink(inv) {
    const q = new URLSearchParams();
    if (inv && inv.ow_url) q.set("ow", inv.ow_url);
    if (inv && inv.code) q.set("code", inv.code);
    const s = q.toString();
    return "watch2whoop://sync-connect" + (s ? "?" + s : "");
  }
  /** Whether the card shows: nothing ever synced, own page, on a card tab. */
  const wantsCard = (ov, tab, self) => !!(ov && ov.setup_needed && self && CARD_TABS.includes(tab));
  /** Whether the first-open sheet comes up: the card would show and it was not asked on this device yet. */
  const wantsAsk = (ov, asked) => !!(ov && ov.setup_needed) && !asked;
  /** The banner text for a stuck sync, or "" (none). */
  const bannerFor = (ov) => (ov && ov.state === "issue" && ov.issue && BANNER_CODES.includes(ov.issue.code) ? ov.issue.label : "");
  /** The tone of the status dot: good / mid / bad / plain. */
  const toneOf = (ov) => (!ov ? "plain" : ov.state === "ok" ? "good" : ov.state === "issue" || ov.state === "consent" ? (ov.issue?.tone || "mid") : "plain");

  if (typeof module !== "undefined") module.exports = { agoText, typeAgo, actionLink, connectLink, wantsCard, wantsAsk, bannerFor, toneOf };
  if (typeof window === "undefined") return;

  let C = null, ME = null;
  let ov = null;            // last /sync/overview
  let checkedAt = 0;
  let tab = "today";
  let phase = null;         // the connect flow: null | "asked" (1.0.6 / 1.0.7: their sync page is open) | {phase, percent, error} from the app
  let invCode = null;       // the invitation code minted for the connect flow (memory only)
  let watchUntil = 0, watchTimer = 0;
  let retryAt = 0;
  const IN_APP = () => !!window.Watch2Whoop;
  const F = () => window.Watch2WhoopFeatures || {};
  const self = () => (C?.isSelf ? C.isSelf() : true) && $("#viewing")?.hidden !== false;

  // Icon: Lucide (ISC) "watch", THIRD_PARTY_NOTICES.md
  const ICON_WATCH = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6"/><polyline points="12 10 12 12 13 13"/><path d="m16.13 7.66-.81-4.05a2 2 0 0 0-2-1.61h-2.68a2 2 0 0 0-2 1.61l-.78 4.05"/><path d="m7.88 16.36.8 4a2 2 0 0 0 2 1.61h2.72a2 2 0 0 0 2-1.61l.81-4.05"/></svg>';

  function css() {
    if ($("#ss-css")) return;
    const s = document.createElement("style");
    s.id = "ss-css";
    s.textContent = "#sync-setup{margin:8px 20px 0}#sync-setup[hidden]{display:none}"
      + ".ss-card{background:var(--surface);border-radius:var(--radius);padding:16px;display:flex;flex-direction:column;gap:12px}"
      + ".ss-head{display:flex;align-items:center;gap:12px}.ss-ic{width:40px;height:40px;border-radius:12px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;flex:none}"
      + ".ss-ic svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}"
      + ".ss-t{font-size:17px;font-weight:600}.ss-s{font-size:13px;color:var(--text-2);margin-top:2px}"
      + ".ss-card .btn{margin:0}.ss-code{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:13px;color:var(--text-2)}"
      + ".ss-code b{font:600 15px ui-monospace,Menlo,monospace;letter-spacing:.06em;color:var(--text)}.ss-code button{border:0;background:none;color:var(--text);font-weight:600;font-size:13px;padding:6px 0}"
      + ".ss-bar{height:4px;border-radius:2px;background:var(--surface-2);overflow:hidden}.ss-bar i{display:block;height:100%;background:var(--good);transition:width .4s}"
      + ".ss-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--text-3);margin-right:6px;vertical-align:1px}"
      + ".ss-dot.good{background:var(--good)}.ss-dot.mid{background:var(--mid)}.ss-dot.bad{background:var(--bad)}"
      + "#sync-st.ss-tap{cursor:pointer}#sync-st[data-tone]::before{content:\"\";display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px;vertical-align:1px;background:var(--text-3)}"
      + "#sync-st[data-tone=good]::before{background:var(--good)}#sync-st[data-tone=mid]::before{background:var(--mid)}#sync-st[data-tone=bad]::before{background:var(--bad)}#sync-issue{margin-top:8px}"
      + ".ss-path{font-size:13px;color:var(--text-2);margin:6px 0 0}"
      + ".ss-h{font-size:13px;font-weight:600;margin:18px 0 6px;color:var(--text-2)}.ss-sc{display:flex;gap:8px;flex-wrap:wrap}"
      + "@media (min-width:1024px){#sync-setup{margin-left:calc(var(--side) + 40px);max-width:560px}}";
    document.head.appendChild(s);
  }

  async function load(force) {
    if (!C || window.W2W_SHELL) return ov;
    if (!force && ov && Date.now() - checkedAt < MIN_GAP_MS) return ov;
    checkedAt = Date.now();
    try { ov = await C.api(`/v1/users/me/sync/overview${IN_APP() ? "?in_app=1" : ""}`, { quiet401: true }); } catch (e) { return ov; }
    draw();
    return ov;
  }

  // ---- the card (above the page, outside #view so every tab can have it)
  function cardEl() {
    let e = $("#sync-setup");
    if (!e) {
      const view = $("#view");
      if (!view) return null;
      e = document.createElement("section");
      e.id = "sync-setup";
      e.hidden = true;
      view.before(e);
    }
    return e;
  }

  function cardHtml() {
    const head = (title, sub) => `<div class="ss-head"><span class="ss-ic">${ICON_WATCH}</span><div><div class="ss-t">${C.esc(title)}</div>${sub ? `<div class="ss-s">${C.esc(sub)}</div>` : ""}</div></div>`;
    if (!IN_APP()) {
      return `<div class="ss-card">${head(tr("开启数据同步"), tr("在 iPhone 上打开 Watch2Whoop"))}
        <a class="btn btn-primary" href="watch2whoop://sync">${C.esc(tr("打开 app"))}</a></div>`;
    }
    if (phase && typeof phase === "object") {   // 1.0.8+: the app reports its progress
      const p = phase.phase, pct = Math.max(0, Math.min(100, phase.percent ?? 0));
      if (p === "error") {
        return `<div class="ss-card">${head(tr("没能开启同步"), phase.error ? String(phase.error).slice(0, 60) : null)}
          <button class="btn btn-primary" type="button" data-ss="connect">${C.esc(tr("再试一次"))}</button></div>`;
      }
      const label = p === "permission" ? tr("等待健康权限…") : p === "connecting" ? tr("连接中…") : p === "done" ? tr("已开始同步") : tr("上传中 {p}%", { p: pct });
      return `<div class="ss-card">${head(tr("开启数据同步"), label)}<div class="ss-bar"><i style="width:${p === "uploading" ? pct : p === "done" ? 100 : 8}%"></i></div></div>`;
    }
    if (phase === "asked") {   // 1.0.6 / 1.0.7: their sync page was opened
      return `<div class="ss-card">${head(tr("开启数据同步"), tr("在同步页点「连接同步」"))}
        <a class="btn btn-primary" href="watch2whoop://sync">${C.esc(tr("打开同步页"))}</a>
        ${invCode ? `<div class="ss-code"><span>${C.esc(tr("连接码"))} <b>${C.esc(invCode)}</b></span><button type="button" data-ss="copy">${C.esc(tr("复制"))}</button></div>` : ""}</div>`;
    }
    return `<div class="ss-card">${head(tr("开启数据同步"), tr("恢复 · 睡眠 · 负荷"))}
      <button class="btn btn-primary" type="button" data-ss="connect">${C.esc(tr("一键开启"))}</button></div>`;
  }

  function drawCard() {
    const e = cardEl();
    if (!e) return;
    const show = wantsCard(ov, tab, self());
    e.hidden = !show;
    if (!show) return;
    e.innerHTML = cardHtml();
    bindActions(e);
  }

  // ---- the banner (stuck sync) and the status line under the Today title (sync.js draws its text)
  function drawBanner() {
    let e = $("#sync-issue");
    if (!e) {
      const anchor = $("#sync-broken") || $("#banner");
      if (!anchor) return;
      e = document.createElement("div");
      e.id = "sync-issue";
      e.className = "banner tap";
      e.setAttribute("role", "button");
      e.hidden = true;
      e.onclick = () => openPanel();
      anchor.after(e);
    }
    const text = bannerFor(ov);
    e.textContent = text ? `${text} · ${tr("点一下处理")}` : "";
    e.hidden = !text || tab !== "today" || !self();
  }

  // The status line under the Today title (sync.js draws it and asks line() / decorate())
  function decorate(e) {
    e.classList.add("ss-tap");
    e.setAttribute("role", "button");
    e.onclick = () => openPanel();
    if (ov) e.dataset.tone = toneOf(ov);
  }
  const line = () => (ov && ov.state === "issue" && ov.issue ? ov.issue.label : "");

  function draw() {
    drawCard();
    drawBanner();
    window.BodySync?.redraw();
    updateMe();
    if (ov && !ov.setup_needed && phase) {   // the first data arrived: the card goes, the page shows it
      phase = null;
      invCode = null;
      watchUntil = 0;
      C.toast(tr("已开始同步"));
      C.reload?.();
    }
    maybeAsk();
  }

  // ---- first open: one sheet, once per user on this device
  function asked(v) {
    const k = `${ASK_KEY}.${ME?.id ?? ""}`;
    try { if (v === undefined) return localStorage.getItem(k) === "1"; localStorage.setItem(k, "1"); } catch (e) { /* private mode */ }
    return false;
  }
  let askTimer = 0;
  function maybeAsk() {
    if (askTimer || !wantsAsk(ov, asked()) || !self() || !(ME?.token_scopes || []).includes("write")) return;
    let tries = 0;
    const wait = () => {
      askTimer = 0;
      if (++tries > 300 || !wantsAsk(ov, asked())) return;
      const busy = $(".consent") || $("#intro") || $("#sheet")?.hidden === false || $("#app")?.hidden !== false;
      if (busy) { askTimer = setTimeout(wait, 1000); return; }
      asked(true);
      C.openSheet(tr("开启数据同步"), `<div class="ss-card" style="padding:0;background:none">
          <div class="ss-head"><span class="ss-ic">${ICON_WATCH}</span><div class="ss-s" style="margin:0">${C.esc(tr("恢复 · 睡眠 · 负荷"))}</div></div></div>
        <div class="btns"><button class="btn btn-primary" type="button" data-ss="connect">${C.esc(IN_APP() ? tr("一键开启") : tr("打开 app"))}</button>
        <button class="btn" type="button" id="ss-later">${C.esc(tr("稍后"))}</button></div>`, (root) => {
        $("#ss-later", root).onclick = () => C.closeSheet();
        bindActions(root, true);
      });
    };
    askTimer = setTimeout(wait, 1200);
  }

  // ---- one-tap connect
  async function connect(btn) {
    if (!IN_APP()) { location.href = "watch2whoop://sync"; return; }
    if (btn) btn.disabled = true;
    let inv = null, err = null;
    try { inv = await C.api("/v1/account/ow-invitation", { method: "POST", body: {} }); } catch (e) { err = e; }
    if (btn) btn.disabled = false;
    if (err && (err.status === 409 || /provisioning/.test(err.message || ""))) { C.toast(tr("账号还在准备中，稍后再试")); return; }
    watch();
    if (F().syncConnect) {   // 1.0.8+: the app asks for Health access, connects and syncs; progress comes to onApp()
      phase = { phase: "connecting", percent: 0 };
      drawCard();
      location.href = connectLink(inv);
      return;
    }
    // 1.0.6 / 1.0.7: 「连接同步」 on their sync page (an account mints its own code; the code is here for the others)
    invCode = inv?.code || null;
    phase = "asked";
    drawCard();
    location.href = "watch2whoop://sync";
  }

  // After the tap: ask again until the first data is in (or 10 minutes passed)
  function watch() {
    watchUntil = Date.now() + WATCH_MS;
    clearTimeout(watchTimer);
    const tick = async () => {
      if (!watchUntil || Date.now() > watchUntil) { watchUntil = 0; return; }
      await load(true);
      if (watchUntil) watchTimer = setTimeout(tick, RECHECK_MS);
    };
    watchTimer = setTimeout(tick, RECHECK_MS);
  }

  // The app's progress (1.0.8+, docs/sync-onboarding-app-requirements.md §3): {phase: permission | connecting | uploading |
  // done | error, percent?, error?}
  function onApp(ev) {
    if (!ev || typeof ev !== "object") return;
    phase = { phase: String(ev.phase || "uploading"), percent: Number(ev.percent) || 0, error: ev.error ? String(ev.error) : null };
    drawCard();
    if (phase.phase === "done") load(true);
  }

  // ---- the panel
  const ACT_LABEL = () => ({
    connect: tr("一键开启"), reconnect: tr("重新连接"), retry: tr("立即重试"),
    sync_now: IN_APP() && F().syncNow ? tr("立即上传") : tr("打开同步页"), open_app: tr("打开 app"),
    authorize: tr("去授权"), open_settings: tr("去设置"), sleep_help: tr("开启睡眠追踪"), consent: tr("去确认"),
  });
  // Where the switch is when the app cannot open it (1.0.6 / 1.0.7): one short path
  const PATH = () => ({
    open_settings: tr("设置 → Watch2Whoop → 后台 App 刷新"),
    authorize: tr("设置 → 健康 → 数据访问与设备 → Watch2Whoop"),
  });

  function actionButtons(actions, primaryFirst = true) {
    const L = ACT_LABEL();
    return actions.map((a, i) => {
      const cls = `btn${i === 0 && primaryFirst ? " btn-primary" : ""}`;
      const href = actionLink(a, F(), IN_APP());
      return href ? `<a class="${cls}" href="${href}" data-ss-link="${a}">${C.esc(L[a])}</a>`
        : `<button class="${cls}" type="button" data-ss="${a}">${C.esc(L[a])}</button>`;
    }).join("");
  }

  function panelHtml() {
    if (!ov) return `<p class="para muted" style="margin:0">${C.esc(tr("读取失败"))}</p>`;
    const now = new Date();
    const head = `<div class="kv"><span class="k"><i class="ss-dot ${toneOf(ov)}"></i>${C.esc(ov.label)}</span>
      <span class="v">${C.esc(tr("最近同步"))} ${C.esc(agoText(ov.minutes_ago))}</span></div>`;
    const is = ov.issue;
    const paths = is ? is.actions.map((a) => (!actionLink(a, F(), IN_APP()) && PATH()[a] && a !== "authorize" ? PATH()[a] : "")).filter(Boolean) : [];
    const issue = is ? `<div class="btns">${actionButtons(is.actions)}</div>${paths.map((p) => `<p class="ss-path">${C.esc(p)}</p>`).join("")}` : "";
    const rows = (ov.types || []).map((t) => `<div class="kv"><span class="k"><i class="ss-dot ${t.status === "ok" ? "good" : t.status === "stale" ? "mid" : "bad"}"></i>${C.esc(t.label)}</span>
      <span class="v">${C.esc(typeAgo(t, now))}</span></div>`).join("");
    const sc = ov.scores || {};
    const mark = (ok, label) => `<span class="badge ${ok ? "good" : "plain"}">${C.esc(label)} ${ok ? "✓" : "—"}</span>`;
    const scores = `<div class="ss-sc">${mark(sc.recovery, tr("恢复"))}${mark(sc.sleep, tr("睡眠"))}${mark(sc.strain, tr("负荷"))}</div>
      ${!sc.recovery && sc.empty && !is ? `<p class="ss-path">${C.esc(sc.empty.label)}</p>` : ""}`;
    const busy = ov.refresh?.busy || ov.history?.active;
    const bottom = [
      !is?.actions.includes("retry") && ov.refresh?.linked && !ov.setup_needed ? `<button class="btn" type="button" data-ss="retry"${busy ? " disabled" : ""}>${C.esc(busy ? tr("同步中…") : tr("立即重试"))}</button>` : "",
      IN_APP() && !is?.actions.includes("sync_now") && !ov.setup_needed ? `<a class="btn" href="${F().syncCenter && window.BodyFlags?.on("sync_center") ? "watch2whoop://sync-center" : "watch2whoop://sync"}">${C.esc(tr("打开同步页"))}</a>` : "",
    ].filter(Boolean).join("");
    const ds = PLUGINS().find((p) => p.openDataStatus) && window.BodyFlags?.on("data_status")
      ? `<button class="item" type="button" data-ss="data_status"><span class="row-title">${C.esc(tr("数据状态"))}</span></button>` : "";
    return `${head}${issue}
      <div class="ss-h">${C.esc(tr("最新数据"))}</div>${rows}
      <div class="ss-h">${C.esc(tr("今天的分数"))}</div>${scores}
      ${bottom ? `<div class="btns">${bottom}</div>` : ""}${ds ? `<div class="group" style="margin-top:12px">${ds}</div>` : ""}`;
  }
  const PLUGINS = () => window.BodyPlugins || [];

  let panelOpen = false;
  function openPanel() {
    panelOpen = true;
    const paint = (root) => { root.innerHTML = panelHtml(); bindActions(root, true); };
    C.openSheet(tr("同步状态"), panelHtml(), (root) => {
      bindActions(root, true);
      load(true).then(() => { if (panelOpen && root.isConnected && $("#sheet")?.hidden === false) paint(root); });
    });
  }

  async function retry(btn) {
    if (Date.now() < retryAt) { C.toast(tr("{n} 秒后再试", { n: Math.ceil((retryAt - Date.now()) / 1000) })); return; }
    if (btn) { btn.disabled = true; btn.textContent = tr("同步中…"); }
    const res = await window.BodySync?.refresh("status");
    if (!res) { C.toast(tr("现在连不上服务器")); if (btn) { btn.disabled = false; btn.textContent = tr("立即重试"); } return; }
    if (res.throttled) {
      retryAt = Date.now() + (res.retry_after || 60) * 1000;
      C.toast(tr("{n} 秒后再试", { n: res.retry_after || 60 }));
    } else if (res.reason === "not_linked") {
      C.toast(tr("还没开启数据同步"));
    } else {
      retryAt = Date.now() + 30000;   // the server allows one every 2 minutes; the page does not even ask more than every 30 s
    }
    const t0 = Date.now();
    const poll = async () => {   // follow the refresh until it is done; the panel repaints
      await load(true);
      const root = $("#sheet-body");
      if (panelOpen && root && $("#sheet")?.hidden === false && $("#sheet-title")?.textContent === tr("同步状态")) {
        root.innerHTML = panelHtml();
        bindActions(root, true);
      }
      if ((ov?.refresh?.busy) && Date.now() - t0 < RETRY_POLL_MAX_MS) setTimeout(poll, RETRY_POLL_MS);
    };
    setTimeout(poll, 1500);
  }

  function copyCode() {
    if (!invCode) return;
    navigator.clipboard?.writeText(invCode).then(() => C.toast(tr("已复制")), () => C.toast(invCode));
  }

  function bindActions(root, inSheet) {
    for (const b of root.querySelectorAll("[data-ss]")) b.onclick = () => {
      const a = b.dataset.ss;
      const first = ov?.issue?.types?.[0] || "sleep";
      const ds = PLUGINS().find((p) => p.openDataStatusType);
      if (a === "connect") { if (inSheet) C.closeSheet(); connect(inSheet ? null : b); }
      else if (a === "retry") retry(b);
      else if (a === "copy") copyCode();
      else if (a === "reconnect") { if (inSheet) C.closeSheet(); window.BodySyncRepair?.open(); }
      else if (a === "consent") { if (inSheet) C.closeSheet(); window.BodyProfile?.consent?.(false); }
      else if (a === "sleep_help") ds?.openDataStatusType("sleep", { watchSleep: true });
      else if (a === "authorize") ds?.openDataStatusType(first);
      else if (a === "data_status") PLUGINS().find((p) => p.openDataStatus)?.openDataStatus();
      else if (a === "open_settings") C.toast(PATH().open_settings);
    };
    for (const l of root.querySelectorAll("[data-ss-link]")) l.addEventListener("click", () => watch());
  }

  // ---- 「我 → 同步状态」
  function meVal() {
    if (!ov) return "";
    const t = ov.state === "ok" || ov.state === "syncing" ? agoText(ov.minutes_ago) : ov.label;
    return `<i class="ss-dot ${toneOf(ov)}"></i>${C.esc(t)}`;
  }
  function updateMe() {
    const v = document.getElementById("me-sync-status-val");
    if (v) v.innerHTML = meVal();
  }

  // ---- the app's phone report (1.0.8+): handed to the server with the page's login
  async function device(report) {
    if (!C || !report || typeof report !== "object") return null;
    try { return await C.api("/v1/users/me/sync/device", { method: "PUT", body: report }); } catch (e) { return null; }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && C && (watchUntil || ov?.state !== "ok")) load(true);   // back from the app's sync page
  });

  window.BodySyncStatus = { open: () => openPanel(), onApp, device, refresh: () => load(true), line, decorate };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meGroup: "main",
    init(ctx, me) { C = ctx; ME = me?.me || null; css(); load(false); },
    onTab(t) { tab = t; panelOpen = false; draw(); if (t === "today") load(false); },
    meItem: (chev) => (ME ? `<button class="item" id="me-sync-status"><span class="row-title">${C.esc(tr("同步状态"))}</span><span class="val" id="me-sync-status-val">${meVal()}</span>${chev || ""}</button>` : ""),
    bindMe(view) {
      const b = $("#me-sync-status", view);
      if (!b) return;
      b.onclick = () => openPanel();
      load(false).then(updateMe);
    },
  });
})();
