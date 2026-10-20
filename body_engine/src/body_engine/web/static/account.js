/* Self-service sign-up on the Today page (body_engine/account/). Separate from app.js:
   - Login screen: an extra "用邮箱登录" (sign in with e-mail) card under the login form: e-mail → 6-digit code → cookie session → reload.
   - Pending accounts (signed in, no invite code yet): a full-screen "输入激活码" page; app.js boot() calls BodyAccount.showPending().
   - Me page: "删除账号" (delete account) row (registered in window.BodyPlugins; not for the deployer), explicit confirm, then
     DELETE /v1/account. Inside the Watch2Whoop app the page then opens watch2whoop://account-deleted so the app resets itself.
   Endpoints: POST /v1/auth/email/start, /v1/auth/email/verify, /v1/account/redeem-invite, DELETE /v1/account, POST /v1/web/logout. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const IN_APP = !!window.Watch2Whoop;
  let C = null;
  let ME = null;

  const CSS = `
    .login{flex-direction:column;gap:18px}
    .login-email{border-top:1px solid var(--line, rgba(127,127,127,.25));padding-top:16px}
    .login-email .linkbtn{display:block;margin:0 auto;background:none;border:0;color:var(--text-2);font-size:14px;cursor:pointer}
    .login-email .row{display:flex;gap:8px}.login-email .row .input{flex:1;min-width:0}
    .acct-page{position:fixed;inset:0;z-index:60;background:var(--bg);color:var(--text);overflow:auto;display:flex;align-items:center;justify-content:center;
      padding:calc(env(safe-area-inset-top) + 20px) 28px calc(env(safe-area-inset-bottom) + 20px)}
    .acct-page .card{width:100%;max-width:360px;text-align:center}
    .acct-page h1{font-size:24px;margin:0 0 6px}.acct-page p{color:var(--text-2);font-size:14px;margin:0 0 18px}
    .acct-page .input{margin-bottom:10px;text-align:center;letter-spacing:.12em;text-transform:uppercase}
    .acct-err{color:var(--bad);font-size:13px;min-height:1.2em;margin:10px 0 0}
    .acct-page .linkbtn{background:none;border:0;color:var(--text-2);font-size:13px;margin-top:18px;cursor:pointer}`;

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
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- login: e-mail code
  function emailCard() {
    const login = $("#login");
    if (!login || $("#login-email")) return;
    fetch("/v1/auth/methods", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : {})).then((m) => {
      if (m.email) buildEmailCard(login);
    }).catch(() => { /* older server or offline: no e-mail sign-in */ });
  }

  function buildEmailCard(login) {
    if ($("#login-email")) return;
    const box = document.createElement("div");
    box.className = "login-card login-email";
    box.id = "login-email";
    box.innerHTML = `<button type="button" class="linkbtn" id="le-open">用邮箱登录 / 注册</button>
      <form id="le-form" hidden autocomplete="on">
        <input id="le-email" class="input" type="email" autocomplete="email" autocapitalize="off" spellcheck="false" placeholder="邮箱" aria-label="邮箱" required>
        <div class="row" id="le-code-row" hidden>
          <input id="le-code" class="input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6 位验证码" aria-label="验证码">
        </div>
        <button type="submit" class="btn btn-primary btn-block" id="le-btn">发送验证码</button>
        <button type="button" class="linkbtn" id="le-resend" hidden>重新发送</button>
        <p class="login-error" id="le-error" role="alert"></p>
      </form>`;
    login.appendChild(box);
    let sent = false;
    $("#le-open").onclick = () => { $("#le-open").hidden = true; $("#le-form").hidden = false; $("#le-email").focus(); };
    const send = async () => {
      $("#le-error").textContent = "";
      await call("/v1/auth/email/start", "POST", { email: $("#le-email").value.trim(), lang: "zh" });
      sent = true;
      $("#le-code-row").hidden = false; $("#le-resend").hidden = false;
      $("#le-btn").textContent = "登录";
      $("#le-error").textContent = "验证码已发到邮箱，10 分钟内有效";
      $("#le-code").focus();
    };
    $("#le-resend").onclick = () => send().catch((e) => { $("#le-error").textContent = e.status === 429 ? "发送太频繁，过一会儿再试" : e.message; });
    $("#le-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#le-btn");
      btn.disabled = true;
      try {
        if (!sent) { await send(); return; }
        await call("/v1/auth/email/verify", "POST", { email: $("#le-email").value.trim(), code: $("#le-code").value.trim(), client: "web" });
        location.replace(location.pathname + location.search);   // signed in (cookie): start the page again
      } catch (err) {
        $("#le-error").textContent = err.status === 401 ? "验证码不对或已过期" : err.status === 429 ? "尝试太多次，过一会儿再试"
          : err.status === 503 ? "服务器暂时不能用邮箱登录" : err.message;
      } finally { btn.disabled = false; }
    });
  }

  // ---------------------------------------------------------------- pending: invite code
  function showPending(onActive) {
    injectCss();
    let page = $("#acct-pending");
    if (!page) {
      page = document.createElement("div");
      page.id = "acct-pending";
      page.className = "acct-page";
      page.innerHTML = `<form class="card" id="ap-form" autocomplete="off">
          <img src="icon.svg" alt="" width="60" height="60" style="border-radius:14px">
          <h1>输入激活码</h1>
          <p>内测期间凭激活码开通 · Enter your invite code</p>
          <input id="ap-code" class="input" autocapitalize="characters" autocorrect="off" spellcheck="false" maxlength="14" placeholder="XXXXX-XXXXX" aria-label="激活码" required>
          <button class="btn btn-primary btn-block" id="ap-btn" type="submit">开通</button>
          <p class="acct-err" id="ap-error" role="alert"></p>
          <button type="button" class="linkbtn" id="ap-logout">退出登录</button>
        </form>`;
      document.body.appendChild(page);
    }
    page.hidden = false;
    $("#ap-form", page).onsubmit = async (e) => {
      e.preventDefault();
      const btn = $("#ap-btn", page);
      btn.disabled = true;
      $("#ap-error", page).textContent = "";
      try {
        await call("/v1/account/redeem-invite", "POST", { code: $("#ap-code", page).value.trim() });
        page.hidden = true;
        onActive?.();
      } catch (err) {
        $("#ap-error", page).textContent = err.status === 401 ? "激活码无效、已用完或已过期" : err.status === 429 ? "尝试太多次，过一会儿再试" : err.message;
      } finally { btn.disabled = false; }
    };
    $("#ap-logout", page).onclick = async () => {
      try { await call("/v1/web/logout", "POST"); } catch (e) { /* signed out locally anyway */ }
      location.replace(location.pathname);
    };
    setTimeout(() => $("#ap-code", page).focus(), 50);
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

  window.BodyAccount = { showPending };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => { C = ctx; ME = me?.me || null; },
    meItem: (chev) => (ME && ME.role !== "owner"
      ? `<button class="item danger" id="me-delete-account"><span class="row-title">删除账号</span>${chev || ""}</button>` : ""),
    bindMe: (view) => { const b = $("#me-delete-account", view); if (b) b.onclick = deleteSheet; },
  });

  injectCss();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", emailCard);
  else emailCard();
})();
