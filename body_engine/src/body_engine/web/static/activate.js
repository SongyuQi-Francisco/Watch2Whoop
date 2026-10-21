/* Activation page (/app/activate.html#<code>): a reusable activation code → the Watch2Whoop app, or the Today page on the web.
   - The code is read from the URL fragment, which browsers never send to the server, so it stays out of URLs and access logs.
   - With a code in the link the page never asks for it. It exchanges the code (POST /v1/web/activate, web/activation.py) for a
     fresh watch2whoop://setup link and opens it. If the page stayed in front for FALLBACK_MS (the app is not installed, or
     iOS did not switch), it signs in on the web and goes to the Today page: it exchanges the code again with web=true, which
     mints only a new Today page login code (no OW invitation), and redeems that (POST /v1/web/login-code, as app.js
     redeemLogin does). So the link handed to the app keeps its own codes even if iOS was only slow to ask "open in the app?".
     Once the page was hidden (the app came up) the fallback is off for that attempt.
   - 「在 App 里打开」 / 「在网页里打开」 are for when that guess is wrong; each tap exchanges the code again, i.e. mints new
     one-time codes, so tapping again is always safe.
   - An invalid / revoked code: 「激活码无效或已停用」 and 「找管理员要新的链接」, still without asking the person to type.
   - Without a fragment the person types the code; then the same flow runs. */
(function () {
  "use strict";
  const FALLBACK_MS = 1800;
  const $ = (id) => document.getElementById(id);
  const form = $("form"), input = $("code"), go = $("go"), choose = $("choose"), status = $("status");
  const openApp = $("open-app"), openWeb = $("open-web"), err = $("err"), hint = $("hint");
  const tr = window.I18N ? window.I18N.t : (k) => k;

  const clean = (s) => (s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  // the one-time Today page login code inside a setup link (same regex as app.js redeemLogin)
  const loginFrom = (link) => { const m = /[?&]login=([^&]+)/.exec(link || ""); return m ? decodeURIComponent(m[1]) : null; };

  let code = clean(location.hash.replace(/^#/, ""));
  let timer = 0;
  let busy = false;
  let left = false;   // the page was hidden since this attempt started (the app came up)
  const cancelFallback = () => { clearTimeout(timer); timer = 0; };
  document.addEventListener("visibilitychange", () => { if (document.hidden) { left = true; cancelFallback(); } });
  window.addEventListener("pagehide", () => { left = true; cancelFallback(); });

  function say(text) { status.textContent = text || ""; }
  function fail(text, final) {
    cancelFallback();
    say("");
    err.textContent = text;
    if (final && !$("ask")) {   // the code itself is no good: no buttons, no input, just who to ask
      choose.hidden = true;
      hint.hidden = true;
      const ask = document.createElement("p");
      ask.id = "ask";
      ask.textContent = tr("activate.askAdmin");
      err.after(ask);
    }
  }

  // POST /v1/web/activate → setup link (fresh one-time codes every call; web: only a login code); null after showing the error
  async function exchange(web) {
    let r;
    try {
      r = await fetch("/v1/web/activate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(web ? { code, web: true } : { code }),
        credentials: "omit", cache: "no-store",
      });
    } catch (_) { fail(tr("activate.network")); return null; }
    if (r.status === 429) { fail(tr("activate.tooMany")); return null; }
    const data = r.ok ? await r.json().catch(() => null) : null;
    const link = data && typeof data.setup_link === "string" && data.setup_link.startsWith("watch2whoop://") ? data.setup_link : null;
    if (!link) { fail(tr("activate.invalid"), true); return null; }
    return link;
  }

  async function webLogin() {
    const link = await exchange(true);
    if (!link) return;
    const login = loginFrom(link);
    if (!login) { fail(tr("activate.invalid"), true); return; }
    say(tr("activate.webFallback"));
    try {
      const r = await fetch("/v1/web/login-code", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Body-Web": "1" },
        body: JSON.stringify({ code: login }), credentials: "same-origin", cache: "no-store",
      });
      if (r.status === 429) { fail(tr("activate.tooMany")); return; }
      if (!r.ok) { fail(tr("activate.invalid"), true); return; }
    } catch (_) { fail(tr("activate.network")); return; }
    location.replace("./");   // the Today page, signed in; replaces activate.html#<code> in the history
  }

  async function run(mode) {   // mode: "auto" (app, then the web by itself) | "app" | "web"
    if (busy) return;
    busy = true;
    cancelFallback();
    left = false;
    err.textContent = "";
    openApp.disabled = openWeb.disabled = true;
    try {
      if (mode === "web") { await webLogin(); return; }
      const link = await exchange(false);
      if (!link) return;
      say(tr("activate.opening"));
      location.href = link;   // the app takes over; if it is not installed the page simply stays
      if (mode === "auto") timer = setTimeout(() => { timer = 0; if (!left && !document.hidden) webLogin(); }, FALLBACK_MS);
    } finally {
      busy = false;
      setTimeout(() => { openApp.disabled = openWeb.disabled = false; }, 1200);
    }
  }

  openApp.addEventListener("click", () => run("app"));
  openWeb.addEventListener("click", () => run("web"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    code = clean(input.value);
    err.textContent = "";
    if (!code) { err.textContent = tr("activate.enterCode"); input.focus(); return; }
    form.hidden = true;
    hint.textContent = tr("activate.hintLink");
    choose.hidden = false;
    run("auto");
  });

  if (code) {
    hint.textContent = tr("activate.hintLink");
    choose.hidden = false;
    run("auto");
  } else {
    form.hidden = false;
    setTimeout(() => input.focus(), 50);
  }
  window.W2WActivate = { clean, loginFrom, FALLBACK_MS };   // for tests
})();
