/* Forced upgrade (R2, client/policy.py; docs/ios-native-2.0.md §4 R2). The Today page, also inside the retired 1.0.8 app:
   - /v1/web/me → upgrade.state "notice": a sheet once a day (「去升级」 opens the App Store / TestFlight link from the server's
     config, 「稍后」 closes it; the day is kept on this device);
   - "block" (or any API answer 426 upgrade_required, app.js api()): a full-screen page over everything, no way to close it.
   Title and text come from the server in the page language; only the buttons are translated here. The link is https only.
   Registered in window.BodyPlugins (init) and window.BodyUpgrade (block / apply); noticeDue / safeUrl are module.exports for
   tests/test_upgrade.py. */
(() => {
  "use strict";
  const DAY_KEY = "w2w.upgrade.notice.day";

  /** Whether today's notice is still to be shown (one a day). */
  function noticeDue(today, lastShown) { return !!today && today !== lastShown; }
  /** The upgrade link when it is a plain https address, else null. */
  function safeUrl(u) { return typeof u === "string" && /^https:\/\/[^\s<>"']+$/.test(u) ? u : null; }

  if (typeof module !== "undefined") module.exports = { noticeDue, safeUrl };
  if (typeof window === "undefined") return;

  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let C = null;
  let blocked = false;

  const link = (url) => {
    const u = safeUrl(url);
    return u ? `<a class="btn btn-primary" id="up-go" href="${esc(u)}"${window.Watch2Whoop ? "" : ' target="_blank" rel="noopener"'}>${esc(tr("去升级"))}</a>` : "";
  };

  function block(info) {
    if (blocked || !document.body) return;
    blocked = true;
    const el = document.createElement("div");
    el.id = "upgrade-wall";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.style.cssText = "position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;"
      + "padding:24px 16px;background:var(--bg);color:var(--text);text-align:center;overflow:auto";
    el.innerHTML = `<div style="max-width:360px;width:100%">
      <h2 style="margin:0 0 12px;font-size:22px">${esc(info?.title || tr("请升级 Watch2Whoop"))}</h2>
      <p class="para" style="margin:0 0 20px;color:var(--text-2)">${esc(info?.message || tr("这个版本已停用，请升级后继续使用。"))}</p>
      <div class="btns" style="justify-content:center">${link(info?.url)}</div></div>`;
    document.body.append(el);
    document.body.style.overflow = "hidden";
  }

  function localDay() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function notice(info) {
    if (!C || window.W2W_SHELL) return;
    let last = null;
    try { last = localStorage.getItem(DAY_KEY); } catch (e) { /* private mode: shown on every load */ }
    const today = localDay();
    if (!noticeDue(today, last)) return;
    // after the first paint and not over another sheet (consent, onboarding, an admin notice): else the next load shows it
    setTimeout(() => {
      if (blocked || document.querySelector("#sheet")?.hidden === false || document.visibilityState === "hidden") return;
      try { localStorage.setItem(DAY_KEY, today); } catch (e) { /* private mode */ }
      C.openSheet(info.title || tr("请升级 Watch2Whoop"), `<p class="para" style="margin:0">${esc(info.message || "")}</p>
        <div class="btns">${link(info.url)}<button class="btn" type="button" id="up-later">${esc(tr("稍后"))}</button></div>`, (root) => {
        root.querySelector("#up-later").onclick = () => C.closeSheet();
        const go = root.querySelector("#up-go");
        if (go) go.addEventListener("click", () => setTimeout(() => C.closeSheet(), 300));
      });
    }, 1200);
  }

  function apply(u) {
    if (u?.state === "block") block(u);
    else if (u?.state === "notice") notice(u);
  }

  window.BodyUpgrade = { block, apply };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx, me) { C = ctx; apply(me?.upgrade); },
  });
})();
