/* Activation page (/app/activate.html#<code>): a reusable activation code → the Watch2Whoop app (iPhone / iPad) or the
   Today page in the browser (computers).
   - The code is read from the URL fragment, which browsers never send to the server, so it stays out of URLs and access logs.
   - With a code in the link the page never asks for it.
   - iPhone / iPad (also iPadOS that reports itself as a Mac with touch): the app only. The page exchanges the code
     (POST /v1/web/activate, web/activation.py) for a fresh watch2whoop://setup link and opens it. If the page is still in
     front FALLBACK_MS later (the app is not installed), it shows the install guide (TestFlight → accept the invitation →
     install → tap the link again) and keeps 「在 App 里打开」. No web sign-in on iOS: the web version in iOS Safari is being
     retired (BODY_IOS_WEB, ios-web.js).
   - Computers: sign in on the web with the same code: exchange with web=true (only a Today page login code, no Open
     Wearables invitation) → POST /v1/web/login-code (as app.js redeemLogin does) → the Today page.
   - Each tap exchanges the code again, i.e. mints new one-time codes, so tapping again is always safe.
   - An invalid / revoked code: 「激活码无效或已停用，找管理员要新的链接」, no input, no buttons.
   - Without a fragment the person types the code; then the same flow runs. */
(function () {
  "use strict";
  const FALLBACK_MS = 1800;
  const $ = (id) => document.getElementById(id);
  const form = $("form"), input = $("code"), choose = $("choose"), status = $("status");
  const openApp = $("open-app"), openWeb = $("open-web"), err = $("err"), hint = $("hint"), guide = $("install");
  const tr = window.I18N ? window.I18N.t : (k) => k;

  const clean = (s) => (s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  // the one-time Today page login code inside a setup link (same regex as app.js redeemLogin)
  const loginFrom = (link) => { const m = /[?&]login=([^&]+)/.exec(link || ""); return m ? decodeURIComponent(m[1]) : null; };
  const isIOS = (ua, platform, touch) => /iPhone|iPad|iPod/.test(ua || "") || (platform === "MacIntel" && touch > 1);
  const IOS = isIOS(navigator.userAgent, navigator.platform, navigator.maxTouchPoints || 0);

  let code = clean(location.hash.replace(/^#/, ""));
  let timer = 0;
  let busy = false;
  let left = false;   // the page was hidden since this attempt started (the app came up)
  const cancel = () => { clearTimeout(timer); timer = 0; };
  document.addEventListener("visibilitychange", () => { if (document.hidden) { left = true; cancel(); } });
  window.addEventListener("pagehide", () => { left = true; cancel(); });

  const say = (text) => { status.textContent = text || ""; };
  function fail(text, final) {
    cancel();
    say("");
    err.textContent = text;
    if (final) { choose.hidden = true; hint.hidden = true; guide.hidden = true; }   // the code itself is no good
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
    if (!link) { fail(tr("activate.invalidAsk"), true); return null; }
    return link;
  }

  async function webLogin() {   // computers only
    say(tr("activate.webLogin"));
    const link = await exchange(true);
    if (!link) return;
    const login = loginFrom(link);
    if (!login) { fail(tr("activate.invalidAsk"), true); return; }
    try {
      const r = await fetch("/v1/web/login-code", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Body-Web": "1" },
        body: JSON.stringify({ code: login }), credentials: "same-origin", cache: "no-store",
      });
      if (r.status === 429) { fail(tr("activate.tooMany")); return; }
      if (!r.ok) { fail(tr("activate.invalidAsk"), true); return; }
    } catch (_) { fail(tr("activate.network")); return; }
    location.replace("./");   // the Today page, signed in; replaces activate.html#<code> in the history
  }

  async function openTheApp() {   // iPhone / iPad
    cancel();
    left = false;
    const link = await exchange(false);
    if (!link) return;
    say(tr("activate.opening"));
    location.href = link;   // the app takes over; if it is not installed the page simply stays
    timer = setTimeout(() => {
      timer = 0;
      if (left || document.hidden) return;
      say("");
      guide.hidden = false;   // the app did not open: install it first
    }, FALLBACK_MS);
  }

  async function run() {
    if (busy) return;
    busy = true;
    err.textContent = "";
    openApp.disabled = openWeb.disabled = true;
    try {
      if (IOS) await openTheApp();
      else await webLogin();
    } finally {
      busy = false;
      setTimeout(() => { openApp.disabled = openWeb.disabled = false; }, 1200);
    }
  }

  function start() {
    hint.hidden = true;
    choose.hidden = false;
    (IOS ? openApp : openWeb).hidden = false;
    run();
  }
  openApp.addEventListener("click", run);
  openWeb.addEventListener("click", run);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    code = clean(input.value);
    err.textContent = "";
    if (!code) { err.textContent = tr("activate.enterCode"); input.focus(); return; }
    form.hidden = true;
    start();
  });

  if (code) start();
  else {
    form.hidden = false;
    setTimeout(() => input.focus(), 50);
  }
  window.W2WActivate = { clean, loginFrom, isIOS, FALLBACK_MS };   // for tests
})();
