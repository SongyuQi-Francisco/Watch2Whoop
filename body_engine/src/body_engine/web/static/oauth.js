/* Consent page of the MCP OAuth server (/app/oauth.html?req=…, body_engine/ai/oauth.py, oauth_api.py).
   An AI app (Claude, ChatGPT, the Gemini app, Antigravity…) sends the browser to /oauth/authorize, which redirects here.
   - That navigation came from the AI app's site, so this HTML request carried no SameSite=Strict session cookie. The page
     therefore loads first and then asks GET /v1/oauth/requests/{req} from our own origin, where the cookie is sent.
   - 401: not signed in → 「去登录」 opens the Today page in a new tab; 「我已登录」 asks again.
   - Shows the app's name and the host it will send the browser back to (and a warning for loopback addresses); the user
     approves (optionally read-only) or denies; POST answers with the redirect URL (code or access_denied), which we follow. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tr = window.I18N ? window.I18N.t : (k) => k;
  const req = new URLSearchParams(location.search).get("req") || "";
  const headers = () => ({ Accept: "application/json", ...(window.I18N ? window.I18N.headers() : {}) });
  let busy = false;

  function show(id) {
    for (const s of ["login", "consent"]) $(s).hidden = s !== id;
    $("status").hidden = true;
  }
  function fail(text) { $("err").textContent = text || ""; }

  async function load() {
    fail("");
    if (!req) { $("status").textContent = ""; fail(tr("链接不完整，请回到 AI 应用重新连接")); return; }
    let r;
    try {
      r = await fetch(`/v1/oauth/requests/${encodeURIComponent(req)}`, { credentials: "same-origin", headers: headers(), cache: "no-store" });
    } catch (_) { fail(tr("连不上服务器")); return; }
    if (r.status === 401) { show("login"); return; }
    const data = await r.json().catch(() => null);
    if (!r.ok) { $("status").hidden = true; fail((data && typeof data.detail === "string" && data.detail) || tr("请求失败（{n}）", { n: r.status })); return; }
    $("title").textContent = tr("连接「{app}」", { app: data.client_name });
    $("client").textContent = data.client_name;
    $("host").textContent = data.redirect_host;
    $("user").textContent = data.user || "";
    $("scopes").textContent = data.scopes.includes("write") ? tr("读写") : tr("只读");
    $("ro").checked = !data.scopes.includes("write");
    $("ro").disabled = !data.scopes.includes("write");
    $("loopback").hidden = !data.loopback;
    $("unknown").hidden = data.loopback || !!data.known_vendor;   // a registered client can call itself anything
    show("consent");
  }

  async function decide(approve) {
    if (busy) return;
    busy = true;
    $("approve").disabled = $("deny").disabled = true;
    fail("");
    try {
      const r = await fetch(`/v1/oauth/requests/${encodeURIComponent(req)}`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { ...headers(), "Content-Type": "application/json", "X-Body-Web": "1" },
        body: JSON.stringify({ approve, read_only: $("ro").checked }),
      });
      const data = await r.json().catch(() => null);
      if (r.status === 401) { show("login"); return; }
      if (!r.ok || !data || typeof data.redirect !== "string") {
        fail((data && typeof data.detail === "string" && data.detail) || tr("请求失败（{n}）", { n: r.status }));
        return;
      }
      $("consent").hidden = true;
      $("status").hidden = false;
      $("status").textContent = approve ? tr("已同意，正在回到应用…") : tr("已拒绝，正在回到应用…");
      location.replace(data.redirect);
    } catch (_) {
      fail(tr("连不上服务器"));
    } finally {
      busy = false;
      $("approve").disabled = $("deny").disabled = false;
    }
  }

  $("approve").onclick = () => decide(true);
  $("deny").onclick = () => decide(false);
  $("retry").onclick = load;
  if (window.I18N) window.I18N.apply();
  load();
})();
