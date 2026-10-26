/* 「手机同步已断开」: the phone's Open Wearables sync stopped while its owner still opens the app (seen in production: the
   SDK's credentials went bad: every upload got 401, unnoticed). /v1/users/me/sync/connection (sync/broken.py; at start-up
   from sync.js's shared overview) says so: a banner on Today until data flows again and, at most once a day, a sheet.
   Its button gets a fresh invitation code (POST /v1/account/ow-invitation) for the app:
   - apps announcing `Watch2WhoopFeatures.syncRepair` (1.0.8+) take watch2whoop://setup?ow=…&code=… and reconnect after
     one confirmation;
   - 1.0.6 / 1.0.7 ignore that link, so the sheet shows the code (copy button) and opens 「手机同步」 (watch2whoop://sync),
     where 「连接同步」 (or 「重新连接」 / 「用邀请码连接」 with the code) reconnects.
   The code lives only in page memory and the app's link: never in a URL sent to a server, never stored.
   After the tap: asks again every 30 s for 10 minutes (and on coming back to the front); the banner goes once data arrives.
   window.BodyPlugins (init, onTab); pure helpers are module.exports for tests/test_sync_broken.py. */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);
  const $ = (s, r = document) => r.querySelector(s);
  const DAY_KEY = "w2w.syncRepair.shownDay";   // local date the sheet was last shown (once a day)
  const RECHECK_MS = 30000, WATCH_MS = 10 * 60 * 1000, MIN_GAP_MS = 60000;

  /** The link the app takes (watch2whoop://setup, same format as web/activation.setup_link). */
  function setupLink(inv) {
    const q = new URLSearchParams();
    if (inv && inv.ow_url) q.set("ow", inv.ow_url);
    q.set("code", (inv && inv.code) || "");
    return "watch2whoop://setup?" + q.toString();
  }
  /** The device's local date, YYYY-MM-DD. */
  const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  /** Whether the sheet may come up: broken, not shown today. */
  const wantsSheet = (st, shownDay, today) => !!(st && st.broken) && shownDay !== today;

  if (typeof module !== "undefined") module.exports = { setupLink, localDay, wantsSheet };
  if (typeof window === "undefined") return;

  let C = null;
  let st = null;          // last /sync/connection
  let checkedAt = 0;
  let onToday = true;
  let watchUntil = 0, watchTimer = 0;
  const IN_APP = () => !!window.Watch2Whoop;
  const canLink = () => IN_APP() && !!window.Watch2WhoopFeatures?.syncRepair;

  function store(v) {
    try { if (v === undefined) return localStorage.getItem(DAY_KEY); localStorage.setItem(DAY_KEY, v); } catch (e) { /* private mode */ }
    return null;
  }

  function css() {
    if ($("#sr-css")) return;
    const s = document.createElement("style");
    s.id = "sr-css";
    s.textContent = ".sr-code{font:600 26px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.06em;text-align:center;margin:12px 0 4px;user-select:all;-webkit-user-select:all}"
      + ".sr-steps{margin:14px 0 0;padding-left:20px;font-size:14px;color:var(--text-2);line-height:1.6}"
      + "#sync-broken{margin-top:8px}";
    document.head.appendChild(s);
  }

  function banner() {
    let e = $("#sync-broken");
    if (!e) {
      const anchor = $("#banner");
      if (!anchor) return null;
      e = document.createElement("div");
      e.id = "sync-broken";
      e.className = "banner tap";
      e.setAttribute("role", "button");
      e.hidden = true;
      e.onclick = () => openSheet();
      anchor.after(e);
    }
    return e;
  }

  function draw() {
    const e = banner();
    if (!e) return;
    e.textContent = tr("手机同步已断开 · 点一下重新连接");
    e.hidden = !(st?.broken && onToday);
  }

  const when = (iso) => new Date(iso).toLocaleString(window.I18N.locale(),
    { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

  function openSheet() {
    store(localDay(new Date()));
    const last = st?.last_data_at ? `<p class="para muted" style="margin:0">${C.esc(tr("最后收到数据 {when}", { when: when(st.last_data_at) }))}</p>` : "";
    C.openSheet(tr("手机同步已断开"), `${last}
      <div class="btns"><button class="btn btn-primary" type="button" id="sr-go">${C.esc(tr("重新连接"))}</button>
      <button class="btn" type="button" id="sr-later">${C.esc(tr("稍后"))}</button></div>`, (root) => {
      $("#sr-later", root).onclick = () => C.closeSheet();
      $("#sr-go", root).onclick = (ev) => reconnect(root, ev.currentTarget);
    });
  }

  async function reconnect(root, btn) {
    if (!IN_APP()) {   // a browser: the phone's app has to do it
      root.innerHTML = `<p class="para" style="margin:0">${C.esc(tr("在 iPhone 的 Watch2Whoop app 里打开今天页，再点「重新连接」"))}</p>`;
      return;
    }
    btn.disabled = true;
    let inv = null;
    try { inv = await C.api("/v1/account/ow-invitation", { method: "POST", body: {} }); } catch (e) { inv = null; }
    btn.disabled = false;
    if (!inv?.code) { C.toast(tr("现在拿不到连接码，稍后再试")); return; }
    watch();
    if (canLink()) {   // the app confirms once, then reconnects (home_page.dart → setup_flow.dart)
      C.closeSheet();
      location.href = setupLink(inv);
      return;
    }
    // 1.0.6 / 1.0.7: their sync page reconnects; the code is there in case it asks for one
    root.innerHTML = `<p class="para muted" style="margin:0">${C.esc(tr("连接码"))}</p><div class="sr-code">${C.esc(inv.code)}</div>
      <div class="btns"><button class="btn" type="button" id="sr-copy">${C.esc(tr("复制连接码"))}</button>
      <a class="btn btn-primary" id="sr-open" href="watch2whoop://sync">${C.esc(tr("打开手机同步"))}</a></div>
      <ol class="sr-steps"><li>${C.esc(tr("点「连接同步」"))}</li><li>${C.esc(tr("没有这个按钮：点「重新连接」或「用邀请码连接」，粘贴连接码"))}</li></ol>`;
    $("#sr-copy", root).onclick = async () => {
      try { await navigator.clipboard.writeText(inv.code); C.toast(tr("已复制")); } catch (e) { C.toast(inv.code); }
    };
  }

  async function check(force) {
    if (!C || window.W2W_SHELL) return;
    if (!force && Date.now() - checkedAt < MIN_GAP_MS) { draw(); return; }
    checkedAt = Date.now();
    const shared = force ? null : (await window.BodySync?.overview?.())?.parts?.connection;
    if (shared) st = shared;
    else try { st = await C.api("/v1/users/me/sync/connection", { quiet401: true }); } catch (e) { return; }
    draw();
    if (!st?.broken) { watchUntil = 0; return; }
    if (watchUntil) return;   // just tapped: no second sheet
    setTimeout(() => {
      if (onToday && document.visibilityState !== "hidden" && $("#sheet")?.hidden !== false
          && wantsSheet(st, store(), localDay(new Date()))) openSheet();
    }, 1500);
  }

  // After the tap: ask again until the data flows (or 10 minutes passed)
  function watch() {
    watchUntil = Date.now() + WATCH_MS;
    clearTimeout(watchTimer);
    const tick = async () => {
      if (!watchUntil || Date.now() > watchUntil) { watchUntil = 0; return; }
      await check(true);
      if (watchUntil) watchTimer = setTimeout(tick, RECHECK_MS);
    };
    watchTimer = setTimeout(tick, RECHECK_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && C && st?.broken) check(true);   // back from the app's sync page
  });

  window.BodySyncRepair = { open: () => (C ? openSheet() : null) };   // the sync status panel's 「重新连接」 (sync-status.js)
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx) { C = ctx; css(); check(false); },   // runs again on every reload (at most one request a minute)
    onTab(tab) { onToday = tab === "today"; draw(); },
  });
})();
