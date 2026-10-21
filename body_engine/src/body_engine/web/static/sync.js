/* On-demand refresh (sync/refresh.py): a small 「同步中… / 已更新 07:42」 line under the Today title, and the call that asks the
   server for fresh data. Used by pull to refresh and 「我 → 数据更新」 (app.js), and by the Watch2Whoop app after its sync round
   on open (it runs `BodySync.refresh("app")` in the page). POST /v1/users/me/sync/refresh pulls the new data from Open Wearables
   in the background and queues the score recompute; this file polls /sync/status while that is busy and reloads the page
   data when the import and then the scores are done. Registered in window.BodyPlugins (init) and window.BodySync. */
(() => {
  "use strict";
  let C = null;          // ctx from app.js: api, reload
  let st = null;         // last /sync/status
  let shown = false;     // the line is visible: own data, Today tab, today's date
  let polling = 0;       // poll generation (a new refresh restarts polling)
  let active = false;    // a poll loop is running
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const POLL_MS = 3000, POLL_MAX_MS = 5 * 60 * 1000;

  function el() {
    let e = $("#sync-st");
    if (!e) {
      if (!$("#sync-css")) {
        const css = document.createElement("style");
        css.id = "sync-css";
        css.textContent = ".top-sync{font-size:12px;color:var(--text-3);margin-top:2px;font-variant-numeric:tabular-nums}";
        document.head.appendChild(css);
      }
      e = document.createElement("div");
      e.id = "sync-st";
      e.className = "top-sync";
      e.setAttribute("aria-live", "polite");
      $("#hdr-pick")?.appendChild(e);
    }
    return e;
  }

  const lastTime = (s) => [s?.scored_at, s?.imported_at, s?.last_import_at].filter(Boolean).sort().pop() || null;
  const hhmm = (iso) => new Date(iso).toLocaleTimeString(window.I18N.locale(), { hour: "2-digit", minute: "2-digit", hour12: false });

  function draw() {
    const e = el();
    const last = lastTime(st);
    const text = !st || !st.linked ? "" : st.busy ? tr("同步中…") : last ? tr("已更新 {t}", { t: hhmm(last) }) : "";
    e.textContent = text;
    e.hidden = !shown || !text;
  }

  async function load() {
    try { st = await C.api("/v1/users/me/sync/status"); } catch (e) { st = null; }
    draw();
    if (st?.busy && !active) poll(++polling);   // opened while a refresh is running (e.g. from another device)
  }

  // Poll while the server is busy; reload the page data once the import is in and again when the scores are done
  async function poll(gen) {
    const t0 = Date.now();
    let seen = st?.status;
    active = true;
    try {
      while (gen === polling && st?.busy && Date.now() - t0 < POLL_MAX_MS) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (gen !== polling) return;
        try { st = await C.api("/v1/users/me/sync/status"); } catch (e) { return; }
        draw();
        if (st && st.status !== seen && (st.status === "scores_queued" || st.status === "idle")) C.reload?.();
        seen = st?.status;
      }
    } finally { if (gen === polling) active = false; }
  }

  // Ask the server for fresh data. source: "pull" | "me" | "app" (only for the logs). Returns the server's answer (or null).
  async function refresh(source) {
    if (!C) return null;
    let res = null;
    try { res = await C.api("/v1/users/me/sync/refresh", { method: "POST", body: {} }); } catch (e) { return null; }
    st = res;
    draw();
    if (res?.busy) poll(++polling);
    else if (source === "app") C.reload?.();   // the phone just uploaded: show what is already there
    return res;
  }

  // Called by app.js header() on every render: show the line only on the Today tab for one's own data and today's date
  function header(visible) {
    shown = !!visible;
    if (shown && !st && C) load(); else draw();
  }

  window.BodySync = { refresh, header };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx) { C = ctx; },   // runs again on every reload: keep st (a poll may be running)
  });
})();
