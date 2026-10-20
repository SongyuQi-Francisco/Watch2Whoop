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
  const IN_APP = !!window.Watch2Whoop;
  let C = null;
  let ME = null;

  const CSS = `.acct-err{color:var(--bad);font-size:13px;min-height:1.2em;margin:10px 0 0}
.ref-code{font:600 28px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.06em;text-align:center;margin:18px 0 6px;user-select:all;-webkit-user-select:all}
.ref-count{text-align:center;margin:0 0 4px}`;

  // User-facing strings of the referral feature, in one place so the i18n layer can wrap them later.
  const T = {
    inviteTitle: "邀请好友",
    inviteLead: "好友用你的邀请码注册，就能开通账号",
    joined: (n, max) => `已有 ${n} 位好友加入 · 还能邀请 ${Math.max(0, max - n)} 位`,
    copyCode: "复制邀请码",
    shareLink: "分享链接",
    copied: "已复制",
    linkCopied: "链接已复制",
    shareText: (code) => `用我的邀请码 ${code} 注册 Watch2Whoop`,
    revoked: "你的邀请码已被管理员停用",
    fromFriend: "已填好好友的邀请码",
    full: "邀请名额已用完",
  };

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
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-Body-Web": "1" },
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) { const err = new Error("连不上服务器"); err.status = 0; throw err; }
    let data = null;
    if (r.status !== 204) { try { data = await r.json(); } catch (e) { /* no body */ } }
    if (!r.ok) {
      const d = data?.detail;
      const err = new Error(typeof d === "string" ? d : `请求失败（${r.status}）`);
      err.status = r.status;
      err.retryAfter = Number(r.headers.get("Retry-After")) || 0;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- login: step flow
  const STEPS = ["email", "code", "invite"];
  const RESEND_SECONDS = 60;           // the server allows one code e-mail per address per minute (account/api.py)
  const methods = fetch("/v1/auth/methods", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : {})).catch(() => ({}));   // older server or offline: only the other ways
  const L = { step: null, sentTo: "", resendAt: 0, timer: 0 };
  const appleInApp = () => !!window.Watch2Whoop && !!window.Watch2WhoopFeatures?.appleSignIn;

  function setError(id, text, ok) {
    const el = $(id);
    el.textContent = text || "";
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
    $("#le-hint").textContent = apple ? "登录或注册" : "用邮箱登录或注册";
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

  function tickResend() {
    clearInterval(L.timer);
    const btn = $("#le-resend");
    const draw = () => {
      const left = Math.ceil((L.resendAt - Date.now()) / 1000);
      btn.disabled = left > 0;
      btn.textContent = left > 0 ? `重新发送（${left} 秒）` : "重新发送";
      if (left <= 0) clearInterval(L.timer);
    };
    draw();
    L.timer = setInterval(draw, 1000);
  }

  const sendError = (err) => (err.status === 429 ? `发送太频繁，${err.retryAfter ? `${err.retryAfter} 秒后` : "过一会儿"}再试`
    : err.status === 400 ? "邮箱格式不对" : err.status === 503 ? "服务器暂时不能用邮箱登录" : err.message);

  async function sendCode(email) {
    await call("/v1/auth/email/start", "POST", { email, lang: "zh" });
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
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { setError("#le-email-error", email ? "邮箱格式不对" : "请输入邮箱"); $("#le-email").focus(); return; }
      const toCode = () => { $("#le-to").textContent = email; $("#le-code").value = ""; go("code"); tickResend(); };
      if (email.toLowerCase() === L.sentTo.toLowerCase() && Date.now() < L.resendAt) { toCode(); return; }   // just sent: no second e-mail
      const btn = $("#le-send");
      btn.disabled = true;
      try { await sendCode(email); toCode(); setError("#le-code-error", "10 分钟内有效", true); }
      catch (err) { setError("#le-email-error", sendError(err)); }
      finally { btn.disabled = false; }
    });
    $("#le-apple").onclick = () => { try { window.Watch2Whoop.postMessage("apple-sign-in"); } catch (e) { /* older app */ } };
    $("#le-back").onclick = () => go("email");
    $("#le-resend").onclick = async () => {
      const btn = $("#le-resend");
      btn.disabled = true;
      setError("#le-code-error", "");
      try { await sendCode(L.sentTo); setError("#le-code-error", "已重新发送", true); }
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
      if (!/^\d{6}$/.test(v)) { setError("#le-code-error", "验证码是 6 位数字"); code.focus(); return; }
      const btn = $("#le-verify");
      btn.disabled = true;
      try {
        await call("/v1/auth/email/verify", "POST", { email: L.sentTo, code: v, client: "web" });
        btn.textContent = "已登录…";
        location.replace(location.pathname + location.search);   // signed in (cookie): start the page again
        return;
      } catch (err) {
        setError("#le-code-error", err.status === 401 ? "验证码不对或已过期" : err.status === 429 ? "尝试太多次，过一会儿再试"
          : err.status === 403 ? "这个账号已停用" : err.message);
        code.select();
      }
      btn.disabled = false;
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
    if (pre && !$("#ap-code").value) {
      $("#ap-code").value = `${pre.slice(0, 5)}-${pre.slice(5)}`;
      setError("#ap-error", T.fromFriend, true);
    }
    $("#ls-invite").onsubmit = async (e) => {
      e.preventDefault();
      const v = $("#ap-code").value.trim();
      setError("#ap-error", "");
      if (v.replace(/[\s-]/g, "").length < 6) { setError("#ap-error", v ? "激活码不完整" : "请输入激活码"); $("#ap-code").focus(); return; }
      const btn = $("#ap-btn");
      btn.disabled = true;
      try {
        await call("/v1/account/redeem-invite", "POST", { code: v });
        $("#ap-code").value = "";
        try { sessionStorage.removeItem(INVITE_KEY); } catch (e2) { /* storage blocked */ }
        onActive?.();
      } catch (err) {
        setError("#ap-error", err.status === 401 ? "激活码无效、已用完或已过期" : err.status === 429 ? "尝试太多次，过一会儿再试" : err.message);
      } finally { btn.disabled = false; }
    };
    $("#ap-logout").onclick = async () => {
      try { await call("/v1/web/logout", "POST"); } catch (e) { /* signed out locally anyway */ }
      location.replace(location.pathname);
    };
  }

  // ---------------------------------------------------------------- Me: delete account
  function deleteSheet() {
    C.openSheet("删除账号", `<p class="para">会永久删除你的账号和全部数据：评分、睡眠、训练、饮食、日记、化验、AI 设置和记忆、同步到服务器的手表数据。删除后不能恢复。</p>
      <p class="para small muted">This permanently deletes your account and all its data. It cannot be undone.</p>
      <input id="del-confirm" class="input" autocomplete="off" placeholder="输入「删除」确认" aria-label="输入删除确认">
      <div class="btns"><button class="btn btn-danger" id="del-go" disabled>永久删除</button></div>
      <p class="acct-err" id="del-error" role="alert"></p>`, (root) => {
      const input = $("#del-confirm", root), go = $("#del-go", root);
      input.oninput = () => { go.disabled = !["删除", "DELETE"].includes(input.value.trim()); };
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
    C.openSheet(T.inviteTitle, `<div id="ref-body"><p class="para muted">…</p></div>`, async (root) => {
      const body = $("#ref-body", root);
      let r;
      try { r = await C.api("/v1/users/me/invite-code"); }
      catch (err) { body.innerHTML = `<p class="para">${esc(err.status === 403 && /revoked/.test(err.message) ? T.revoked : err.message)}</p>`; return; }
      const link = new URL(r.link, location.origin).href;
      body.innerHTML = `<p class="para muted">${esc(T.inviteLead)}</p>
        <div class="ref-code" id="ref-code">${esc(r.code)}</div>
        <p class="para small muted ref-count" id="ref-count">${esc(r.remaining > 0 ? T.joined(r.joined, r.max_uses) : `${T.joined(r.joined, r.max_uses)} · ${T.full}`)}</p>
        <div class="btns"><button class="btn" id="ref-copy">${esc(T.copyCode)}</button><button class="btn btn-primary" id="ref-share">${esc(T.shareLink)}</button></div>`;
      const copy = async (text, msg) => {
        try { await navigator.clipboard.writeText(text); C.toast(msg); } catch (e) { C.toast(text); }
      };
      $("#ref-copy", root).onclick = () => copy(r.code, T.copied);
      $("#ref-share", root).onclick = async () => {
        if (navigator.share) {
          try { await navigator.share({ title: "Watch2Whoop", text: T.shareText(r.code), url: link }); return; }
          catch (e) { if (e && e.name === "AbortError") return; }
        }
        copy(`${T.shareText(r.code)} ${link}`, T.linkCopied);
      };
    });
  }

  window.BodyAccount = { showPending, loginShown, showOther };
  // before the delete-account row on the Me page
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meItem: (chev) => (ME ? `<button class="item" id="me-invite" hidden><span class="row-title">${esc(T.inviteTitle)}</span>${chev || ""}</button>` : ""),
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
      ? `<button class="item danger" id="me-delete-account"><span class="row-title">删除账号</span>${chev || ""}</button>` : ""),
    bindMe: (view) => { const b = $("#me-delete-account", view); if (b) b.onclick = deleteSheet; },
  });

  injectCss();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindLogin);
  else bindLogin();
})();
