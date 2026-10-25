/* Messages from the admin to this user (notices/, `body-engine notice-send`): GET /v1/users/me/notices lists the ones not
   seen yet; the oldest comes up as a sheet (title, text, optional button) shortly after the page loads, and is marked seen
   (POST …/notices/{id}/seen) as soon as it is shown: each one appears once. The next one waits for the next page load.
   The button's link is https://…, a /app/ path or a watch2whoop:// link the app handles (checked on the server).
   Registered in window.BodyPlugins (init); linkAttrs is module.exports for tests/test_notices.py. */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);
  const $ = (s, r = document) => r.querySelector(s);

  /** The button's href and target for a notice link; null when it is none of the allowed kinds. */
  function linkAttrs(link) {
    if (typeof link !== "string") return null;
    if (/^https:\/\/[^\s<>"']+$/.test(link)) return { href: link, external: true };
    if (/^\/app\/[^\s<>"']*$/.test(link) || /^watch2whoop:\/\/[a-z-]+(\?[^\s<>"']*)?$/.test(link)) return { href: link, external: false };
    return null;
  }

  if (typeof module !== "undefined") module.exports = { linkAttrs };
  if (typeof window === "undefined") return;

  let C = null;
  let loaded = false;   // once per page load (init runs again on every data reload)

  function show(n) {
    const a = linkAttrs(n.link);
    const btn = a ? `<a class="btn btn-primary" id="nc-link" href="${C.esc(a.href)}"${a.external && !window.Watch2Whoop ? ' target="_blank" rel="noopener"' : ""}>${C.esc(n.link_label || tr("打开"))}</a>` : "";
    C.openSheet(n.title, `${n.body ? `<p class="para" style="margin:0">${C.esc(n.body)}</p>` : ""}
      <div class="btns">${btn}<button class="btn" type="button" id="nc-ok">${C.esc(tr("知道了"))}</button></div>`, (root) => {
      $("#nc-ok", root).onclick = () => C.closeSheet();
      const l = $("#nc-link", root);
      if (l) l.addEventListener("click", () => setTimeout(() => C.closeSheet(), 300));
    });
    C.api(`/v1/users/me/notices/${encodeURIComponent(n.id)}/seen`, { method: "POST", body: {} }).catch(() => {});
  }

  async function load() {
    if (loaded || !C || window.W2W_SHELL) return;
    loaded = true;
    let res = null;
    try { res = await C.api("/v1/users/me/notices", { quiet401: true }); } catch (e) { loaded = false; return; }
    const n = res?.items?.[0];
    if (!n) return;
    // after the first paint and not over another sheet (consent, onboarding): otherwise the next page load shows it
    setTimeout(() => { if ($("#sheet")?.hidden !== false && document.visibilityState !== "hidden") show(n); else loaded = false; }, 800);
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx) { C = ctx; load(); },
  });
})();
