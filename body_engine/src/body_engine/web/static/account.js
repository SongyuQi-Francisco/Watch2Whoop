/* Self-service sign-up on the Today page (body_engine/account/). Separate from app.js:
   - Login screen (#login in index.html) is a step flow, one thing per screen: 1 e-mail (「发送验证码」; 「通过 Apple 登录」 only
     inside the Watch2Whoop app when it sets window.Watch2WhoopFeatures.appleSignIn, then the app is asked over its JS channel
     with "apple-sign-in") → 2 six-digit code (resend after a cooldown, change e-mail) → cookie session → reload →
     3 pending accounts (signed in, no invite code yet): the invite-code step; app.js boot() calls BodyAccount.showPending().
     Active accounts skip 3. The older ways (login code, activation code, token: app.js #login-form) sit in a collapsed
     「其他登录方式」, opened by itself when the server has no e-mail sign-in. Errors only ever show on the current step.
   - Me page: "删除账号" (delete account) row (registered in window.BodyPlugins; not for the deployer), explicit confirm, then
     DELETE /v1/account. Inside the Watch2Whoop app the page then opens watch2whoop://account-deleted so the app resets itself.
   - Referrals (only when /v1/auth/methods says referrals: true, i.e. the server sets BODY_REFERRALS_ENABLED): Me page row
     「邀请好友」 → the user's own code, copy, share link /app/?invite=CODE and how many friends joined. A link like that is
     remembered (sessionStorage) through sign-in and fills the invite step.
   Endpoints: GET /v1/auth/methods, POST /v1/auth/email/start, /v1/auth/email/verify, /v1/account/redeem-invite,
   GET /v1/users/me/invite-code, DELETE /v1/account, POST /v1/web/logout. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tr = (k, p) => window.I18N.t(k, p);
  const IN_APP = !!window.Watch2Whoop;
  let C = null;
  let ME = null;

  const CSS = `.acct-err{color:var(--bad);font-size:13px;min-height:1.2em;margin:10px 0 0}
.ref-code{font:600 28px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.06em;text-align:center;margin:18px 0 6px;user-select:all;-webkit-user-select:all}
.ref-count{text-align:center;margin:0 0 4px}`;

  // ?invite=XXXXX-XXXXX (a friend's share link): remembered through e-mail / Apple sign-in for the invite step.
  const INVITE_KEY = "w2w-invite";
  (function rememberInvite() {
    let v = "";
    try { v = new URLSearchParams(location.search).get("invite") || ""; } catch (e) { /* old browser */ }
    v = v.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10);
    if (v.length === 10) { try { sessionStorage.setItem(INVITE_KEY, v); } catch (e) { /* storage blocked */ } }
  })();
  const pendingInvite = () => { try { return sessionStorage.getItem(INVITE_KEY) || ""; } catch (e) { return ""; } };

  function injectCss() {
    if ($("#account-css")) return;
    const st = document.createElement("style");
    st.id = "account-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  async function call(path, method, body) {
    let r;
    try {
      r = await fetch(path, { method, credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-Body-Web": "1", ...window.I18N.headers() },
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) { const err = new Error(tr("连不上服务器")); err.status = 0; throw err; }
    let data = null;
    if (r.status !== 204) { try { data = await r.json(); } catch (e) { /* no body */ } }
    if (!r.ok) {
      const d = data?.detail;
      const err = new Error(typeof d === "string" ? d : tr("err.request", { status: r.status }));
      err.status = r.status;
      err.retryAfter = Number(r.headers.get("Retry-After")) || 0;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- login: step flow
  const STEPS = ["email", "code", "invite"];
  const RESEND_SECONDS = 60;           // the server allows one code e-mail per address per minute (account/api.py)
  const methods = fetch("/v1/auth/methods", { credentials: "same-origin", headers: { ...window.I18N.headers() } })
    .then((r) => (r.ok ? r.json() : {})).catch(() => ({}));   // older server or offline: only the other ways
  const L = { step: null, sentTo: "", resendAt: 0, timer: 0 };
  const appleInApp = () => !!window.Watch2Whoop && !!window.Watch2WhoopFeatures?.appleSignIn;

  // `text` may be a function returning the text, so a language switch on the login card can draw it again (see setLang)
  const shown = {};
  function setError(id, text, ok) {
    const el = $(id);
    shown[id] = text;
    el.textContent = (typeof text === "function" ? text() : text) || "";
    el.classList.toggle("ok", !!ok);
  }

  // Show one step: its form, the progress indicator (earlier steps ticked) and nothing else's errors.
  function go(step) {
    L.step = step;
    for (const s of STEPS) $(`#ls-${s}`).hidden = s !== step;
    const idx = STEPS.indexOf(step);
    $("#login-steps").hidden = idx < 0;
    for (const li of document.querySelectorAll("#login-steps li")) {
      const i = STEPS.indexOf(li.dataset.step);
      li.classList.toggle("done", i < idx);
      if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    }
    for (const id of ["#le-email-error", "#le-code-error", "#ap-error", "#login-error"]) setError(id, "");
    const other = $("#login-other");
    other.hidden = step === "invite";              // already signed in: only the invite code (or sign out)
    other.classList.toggle("only", idx < 0);
    if (idx < 0) other.open = true;
    if (step) other.open = false;
    const focus = { email: "#le-email", code: "#le-code", invite: "#ap-code" }[step] || "#token";
    setTimeout(() => { const el = $(focus); if (el && !el.closest("[hidden]")) el.focus(); }, 50);
  }

  // app.js showLogin(): back to the first step; `msg` (e.g. session expired) shows there.
  async function loginShown(msg) {
    const m = await methods;
    const apple = !!(m.apple && appleInApp());
    $("#le-apple").hidden = $("#le-or").hidden = !apple;
    L.apple = apple;
    drawHint();
    if (m.email) {
      go("email");
      if (msg) setError("#le-email-error", msg);
    } else {
      go(null);
      if (msg) setError("#login-error", msg);
    }
    applyOther();
  }

  // app.js: a one-time login code from the app's link failed; its error belongs to 「其他登录方式」. The login screen may not
  // be up yet (boot() still asking /v1/web/me), so keep it a few seconds for loginShown().
  function showOther(msg) {
    L.other = { msg, at: Date.now() };
    if (!$("#login").hidden && L.step !== "invite") applyOther();
  }
  function applyOther() {
    const o = L.other;
    L.other = null;
    if (!o || Date.now() - o.at > 10000) return;
    for (const id of ["#le-email-error", "#le-code-error"]) setError(id, "");
    $("#login-other").open = true;
    setError("#login-error", o.msg);
  }

  const drawHint = () => { $("#le-hint").textContent = L.apple ? tr("登录或注册") : tr("用邮箱登录或注册"); };

  function tickResend() {
    clearInterval(L.timer);
    const btn = $("#le-resend");
    const draw = () => {
      const left = Math.ceil((L.resendAt - Date.now()) / 1000);
      btn.disabled = left > 0;
      btn.textContent = left > 0 ? tr("重新发送（{n} 秒）", { n: left }) : tr("重新发送");
      if (left <= 0) clearInterval(L.timer);
    };
    draw();
    L.timer = setInterval(draw, 1000);
  }

  const sendError = (err) => () => (err.status === 429 ? (err.retryAfter ? tr("发送太频繁，{n} 秒后再试", { n: err.retryAfter }) : tr("发送太频繁，过一会儿再试"))
    : err.status === 400 ? tr("邮箱格式不对") : err.status === 503 ? tr("服务器暂时不能用邮箱登录") : err.message);

  async function sendCode(email) {
    await call("/v1/auth/email/start", "POST", { email, lang: window.I18N.lang });
    L.sentTo = email;
    L.resendAt = Date.now() + RESEND_SECONDS * 1000;
  }

  function bindLogin() {
    if (!$("#ls-email") || bindLogin.done) return;
    bindLogin.done = true;
    $("#ls-email").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = $("#le-email").value.trim();
      setError("#le-email-error", "");
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { setError("#le-email-error", () => (email ? tr("邮箱格式不对") : tr("请输入邮箱"))); $("#le-email").focus(); return; }
      const toCode = () => { $("#le-to").textContent = email; $("#le-code").value = ""; go("code"); tickResend(); };
      if (email.toLowerCase() === L.sentTo.toLowerCase() && Date.now() < L.resendAt) { toCode(); return; }   // just sent: no second e-mail
      const btn = $("#le-send");
      btn.disabled = true;
      try { await sendCode(email); toCode(); setError("#le-code-error", () => tr("10 分钟内有效"), true); }
      catch (err) { setError("#le-email-error", sendError(err)); }
      finally { btn.disabled = false; }
    });
    $("#le-apple").onclick = () => { try { window.Watch2Whoop.postMessage("apple-sign-in"); } catch (e) { /* older app */ } };
    $("#le-back").onclick = () => go("email");
    $("#le-resend").onclick = async () => {
      const btn = $("#le-resend");
      btn.disabled = true;
      setError("#le-code-error", "");
      try { await sendCode(L.sentTo); setError("#le-code-error", () => tr("已重新发送"), true); }
      catch (err) { if (err.retryAfter) L.resendAt = Date.now() + err.retryAfter * 1000; setError("#le-code-error", sendError(err)); }
      tickResend();
    };
    const code = $("#le-code");
    code.addEventListener("input", () => {
      const digits = code.value.replace(/\D/g, "").slice(0, 6);
      if (digits !== code.value) code.value = digits;
      if (digits.length === 6 && !$("#le-verify").disabled) $("#ls-code").requestSubmit();   // pasted / auto-filled code
    });
    $("#ls-code").addEventListener("submit", async (e) => {
      e.preventDefault();
      const v = code.value.trim();
      setError("#le-code-error", "");
      if (!/^\d{6}$/.test(v)) { setError("#le-code-error", () => tr("验证码是 6 位数字")); code.focus(); return; }
      const btn = $("#le-verify");
      btn.disabled = true;
      try {
        await call("/v1/auth/email/verify", "POST", { email: L.sentTo, code: v, client: "web" });
        btn.textContent = tr("已登录…");
        location.replace(location.pathname + location.search);   // signed in (cookie): start the page again
        return;
      } catch (err) {
        setError("#le-code-error", () => (err.status === 401 ? tr("验证码不对或已过期") : err.status === 429 ? tr("尝试太多次，过一会儿再试")
          : err.status === 403 ? tr("这个账号已停用") : err.message));
        code.select();
      }
      btn.disabled = false;
    });
  }

  // ---------------------------------------------------------------- login: language switch (top right of the login screen,
  // also on the pending-account step). Before login the page follows the device (I18N); a choice is remembered and saved to the
  // profile after login (app.js boot). Nothing typed yet: reload; otherwise re-translate in place and keep the inputs.
  function bindLang() {
    const host = $("#login");
    if (!host || $("#login-lang")) return;
    const box = document.createElement("div");
    box.className = "login-lang";
    box.id = "login-lang";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Language / 语言");   // i18n-ok (both names, shown before the language is known)
    box.innerHTML = '<button type="button" data-lang="zh" lang="zh-CN">中文</button><span aria-hidden="true">/</span>'   // i18n-ok (language names in their own language)
      + '<button type="button" data-lang="en" lang="en">English</button>';
    host.prepend(box);
    const mark = () => { for (const b of box.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.lang === window.I18N.lang)); };
    mark();
    box.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-lang]");
      if (!b) return;
      const l = b.dataset.lang;
      const changed = window.I18N.choose(l);
      try { window.Watch2Whoop?.postMessage("lang:" + l); } catch (err) { /* older app */ }
      if (!changed) return;
      if (!["#le-email", "#le-code", "#ap-code", "#token"].some((id) => $(id)?.value)) { location.reload(); return; }
      window.I18N.apply();
      mark();
      drawHint();
      for (const [id, text] of Object.entries(shown)) if (typeof text === "function") $(id).textContent = text() || "";
      if (L.sentTo) tickResend();
    });
  }

  // ---------------------------------------------------------------- step 3: invite code (pending accounts)
  function showPending(onActive) {
    bindLogin();
    $("#app").hidden = true;
    $("#login").hidden = false;
    $("#login-app").hidden = !IN_APP;
    go("invite");
    const pre = pendingInvite();
    window.I18N.apply($("#ls-invite"));
    if (pre && !$("#ap-code").value) {
      $("#ap-code").value = `${pre.slice(0, 5)}-${pre.slice(5)}`;
      setError("#ap-error", tr("已填好好友的邀请码"), true);
    }
    $("#ls-invite").onsubmit = async (e) => {
      e.preventDefault();
      const v = $("#ap-code").value.trim();
      setError("#ap-error", "");
      if (v.replace(/[\s-]/g, "").length < 6) { setError("#ap-error", () => (v ? tr("激活码不完整") : tr("请输入激活码"))); $("#ap-code").focus(); return; }
      const btn = $("#ap-btn");
      btn.disabled = true;
      try {
        await call("/v1/account/redeem-invite", "POST", { code: v });
        $("#ap-code").value = "";
        try { sessionStorage.removeItem(INVITE_KEY); } catch (e2) { /* storage blocked */ }
        onActive?.();
      } catch (err) {
        setError("#ap-error", () => (err.status === 401 ? tr("激活码无效、已用完或已过期") : err.status === 429 ? tr("尝试太多次，过一会儿再试") : err.message));
      } finally { btn.disabled = false; }
    };
    $("#ap-logout").onclick = async () => {
      try { await call("/v1/web/logout", "POST"); } catch (e) { /* signed out locally anyway */ }
      location.replace(location.pathname);
    };
  }

  // ---------------------------------------------------------------- Me: delete account
  function deleteSheet() {
    C.openSheet(tr("删除账号"), `<p class="para">${esc(tr("会永久删除你的账号和全部数据：评分、睡眠、训练、饮食、日记、化验、AI 设置和记忆、同步到服务器的手表数据。删除后不能恢复。"))}</p>
      <input id="del-confirm" class="input" autocomplete="off" placeholder="${esc(tr("输入「删除」确认"))}" aria-label="${esc(tr("输入删除确认"))}">
      <div class="btns"><button class="btn btn-danger" id="del-go" disabled>${esc(tr("永久删除"))}</button></div>
      <p class="acct-err" id="del-error" role="alert"></p>`, (root) => {
      const input = $("#del-confirm", root), go = $("#del-go", root);
      input.oninput = () => { go.disabled = !["删除", "DELETE", "delete"].includes(input.value.trim()); };   // i18n-ok (typed word, both languages)
      go.onclick = async () => {
        go.disabled = true;
        try {
          await call("/v1/account", "DELETE", { confirm: "DELETE" });
          try { if (window.caches) await caches.delete("be-data"); } catch (e) { /* no Cache API */ }
          if (IN_APP) location.href = "watch2whoop://account-deleted";
          else location.replace(location.pathname);
        } catch (err) {
          $("#del-error", root).textContent = err.message;
          go.disabled = false;
        }
      };
    });
  }

  // ---------------------------------------------------------------- Me: invite friends (referral code)
  function inviteSheet() {
    C.openSheet(tr("邀请好友"), `<div id="ref-body"><p class="para muted">…</p></div>`, async (root) => {
      const body = $("#ref-body", root);
      let r;
      try { r = await C.api("/v1/users/me/invite-code"); }
      catch (err) { body.innerHTML = `<p class="para">${esc(err.status === 403 && /revoked/.test(err.message) ? tr("你的邀请码已被管理员停用") : err.message)}</p>`; return; }
      const link = new URL(r.link, location.origin).href;
      body.innerHTML = `<p class="para muted">${esc(tr("好友用你的邀请码注册，就能开通账号"))}</p>
        <div class="ref-code" id="ref-code">${esc(r.code)}</div>
        <p class="para small muted ref-count" id="ref-count">${esc(tr("已有 {n} 位好友加入 · 还能邀请 {left} 位", { n: r.joined, left: r.remaining }) + (r.remaining > 0 ? "" : ` · ${tr("邀请名额已用完")}`))}</p>
        <div class="btns"><button class="btn" id="ref-copy">${esc(tr("复制邀请码"))}</button><button class="btn btn-primary" id="ref-share">${esc(tr("分享链接"))}</button></div>`;
      const copy = async (text, msg) => {
        try { await navigator.clipboard.writeText(text); C.toast(msg); } catch (e) { C.toast(text); }
      };
      $("#ref-copy", root).onclick = () => copy(r.code, tr("已复制"));
      $("#ref-share", root).onclick = async () => {
        if (navigator.share) {
          try { await navigator.share({ title: "Watch2Whoop", text: tr("用我的邀请码 {code} 注册 Watch2Whoop", { code: r.code }), url: link }); return; }
          catch (e) { if (e && e.name === "AbortError") return; }
        }
        copy(`${tr("用我的邀请码 {code} 注册 Watch2Whoop", { code: r.code })} ${link}`, tr("链接已复制"));
      };
    });
  }

  window.BodyAccount = { showPending, loginShown, showOther };
  // before the delete-account row on the Me page
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meItem: (chev) => (ME ? `<button class="item" id="me-invite" hidden><span class="row-title">${esc(tr("邀请好友"))}</span>${chev || ""}</button>` : ""),
    bindMe: async (view) => {
      const b = $("#me-invite", view);
      if (!b) return;
      b.onclick = inviteSheet;
      if ((await methods).referrals) b.hidden = false;
    },
  });
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => { C = ctx; ME = me?.me || null; },
    meItem: (chev) => (ME && ME.role !== "owner"
      ? `<button class="item danger" id="me-delete-account"><span class="row-title">${esc(tr("删除账号"))}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => { const b = $("#me-delete-account", view); if (b) b.onclick = deleteSheet; },
  });

  injectCss();
  const start = () => { bindLogin(); bindLang(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
