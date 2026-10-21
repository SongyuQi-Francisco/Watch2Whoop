/* First-run intro (issue #60): three short screens on what Watch2Whoop is, then tips for getting the most out of it.
   Shown once per user after login (server flag users.intro_seen_at, /v1/web/me -> me.intro_seen, so it follows the account
   across devices), skippable, and re-openable from 「我 → 新手引导」 (next to 「使用手册」). Kept short on purpose: the long
   explanations live in the handbook (/app/handbook/), which the last screen opens. In the Watch2Whoop app the native
   permissions step comes first; this page part is the same for web and app users.
   Registered in window.BodyPlugins (init) and window.BodyIntro (open, used by the Me page row). */
(() => {
  "use strict";
  let C = null;              // ctx from app.js: api, esc, toast, openHandbook, openAiSettings
  let step = 0;
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);

  // Screens: icon (inline SVG paths, 24px grid), title, text. The last one is the tips list.
  const ICONS = {
    watch: '<rect x="7" y="6" width="10" height="12" rx="3"/><path d="M9 6l.5-3h5l.5 3M9 18l.5 3h5l.5-3M10 12h1.5l1-2 1 4 1-2H15"/>',
    chat: '<path d="M4 5h16v10H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    star: '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.5 6.7 19.4l1.2-6L3.4 9.3l6-.7z"/>',
  };
  const SCREENS = () => [
    { icon: "watch", title: tr("手表数据 → 每天的分数"),
      text: tr("Apple Watch 的心率、HRV、睡眠和训练自动同步，算出恢复、负荷和睡眠分数。") },
    { icon: "chat", title: tr("记录：和你的 AI 说"),
      text: tr("吃了什么、练了什么、感觉怎样，告诉你自己的 AI（Claude、ChatGPT 等），它直接记进来，不用填表。") },
    { icon: "lock", title: tr("数据在你的服务器上"),
      text: tr("数据存在你自己的账号和服务器里；AI 只负责读写，换一个 AI，数据还在。") },
    { icon: "star", title: tr("用得更好"), tips: true },
  ];
  const TIPS = () => [
    { text: tr("连接你的 AI：「我 → AI 设置」"), action: "ai", label: tr("去连接") },
    { text: tr("每天看一眼今天页：恢复分告诉你今天练多重") },
    { text: tr("戴着手表睡觉，睡眠和恢复才准") },
    { text: tr("第一次同步最近 90 天，要几分钟到十几分钟；保持 app 打开、手机解锁") },
    { text: tr("更多说明在「我 → 使用手册」"), action: "handbook", label: tr("打开手册") },
  ];

  const CSS = `
    .intro{position:fixed;inset:0;z-index:55;background:var(--bg);color:var(--text);display:flex;flex-direction:column;overflow:auto;-webkit-overflow-scrolling:touch}
    .intro-in{flex:1;display:flex;flex-direction:column;width:100%;max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 12px) 24px calc(env(safe-area-inset-bottom) + 24px)}
    .intro-top{display:flex;justify-content:flex-end;min-height:36px}
    .intro-skip{font:inherit;font-size:15px;color:var(--text-2);background:none;border:0;padding:8px 4px;cursor:pointer}
    .intro-body{flex:1;display:flex;flex-direction:column;justify-content:center;padding:12px 0}
    .intro-icon{width:64px;height:64px;border-radius:18px;background:var(--surface);display:flex;align-items:center;justify-content:center;margin-bottom:22px}
    .intro-icon svg{width:34px;height:34px;fill:none;stroke:var(--text);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
    .intro h1{font-size:26px;line-height:1.25;margin:0 0 12px;letter-spacing:-.01em}
    .intro p{font-size:16px;line-height:1.55;color:var(--text-2);margin:0}
    .intro-tips{list-style:none;padding:0;margin:4px 0 0;display:flex;flex-direction:column;gap:10px}
    .intro-tips li{background:var(--surface);border-radius:14px;padding:13px 14px;display:flex;align-items:center;gap:12px;font-size:15px;line-height:1.4}
    .intro-tips li span{flex:1}
    .intro-tips .btn{flex:none;padding:7px 12px;font-size:14px;margin:0;background:var(--bg)}
    .intro-dots{display:flex;justify-content:center;gap:6px;margin:8px 0 18px}
    .intro-dots i{width:7px;height:7px;border-radius:50%;background:var(--surface-2)}
    .intro-dots i.on{background:var(--text);width:18px;border-radius:4px}
    .intro-nav{display:flex;gap:10px}.intro-nav .btn{flex:1;margin:0}`;

  function injectCss() {
    if ($("#intro-css")) return;
    const st = document.createElement("style");
    st.id = "intro-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // Server flag; failures are not shown (worst case the intro shows once more)
  async function mark(seen) {
    try { await C.api("/v1/users/me/intro", { method: "PUT", body: { seen } }); } catch (e) { /* offline / read-only token */ }
  }

  function close(done) {
    const el = $("#intro");
    if (el) el.remove();
    if (done) mark(true);
  }

  function draw() {
    const el = $("#intro");
    if (!el) return;
    const screens = SCREENS(), s = screens[step], last = step === screens.length - 1;
    const body = s.tips
      ? `<ul class="intro-tips">${TIPS().map((t) => `<li><span>${C.esc(t.text)}</span>${t.action ? `<button type="button" class="btn" data-intro-act="${t.action}">${C.esc(t.label)}</button>` : ""}</li>`).join("")}</ul>`
      : `<p>${C.esc(s.text)}</p>`;
    el.innerHTML = `<div class="intro-in">
        <div class="intro-top">${last ? "" : `<button type="button" class="intro-skip" id="intro-skip">${C.esc(tr("跳过"))}</button>`}</div>
        <div class="intro-body"><div class="intro-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[s.icon]}</svg></div>
          <h1 id="intro-title">${C.esc(s.title)}</h1>${body}</div>
        <div class="intro-dots" aria-hidden="true">${screens.map((_, i) => `<i class="${i === step ? "on" : ""}"></i>`).join("")}</div>
        <div class="intro-nav">${step > 0 ? `<button type="button" class="btn" id="intro-prev">${C.esc(tr("上一步"))}</button>` : ""}
          <button type="button" class="btn btn-primary" id="intro-next">${C.esc(last ? tr("开始使用") : tr("下一步"))}</button></div></div>`;
    el.setAttribute("aria-labelledby", "intro-title");
    const skip = $("#intro-skip", el);
    if (skip) skip.onclick = () => close(true);
    const prev = $("#intro-prev", el);
    if (prev) prev.onclick = () => { step--; draw(); };
    $("#intro-next", el).onclick = () => { if (last) close(true); else { step++; draw(); } };
    for (const b of el.querySelectorAll("[data-intro-act]")) b.onclick = () => {
      close(true);
      if (b.dataset.introAct === "ai") C.openAiSettings?.(); else C.openHandbook?.();
    };
    $("#intro-next", el).focus({ preventScroll: true });
  }

  function open() {
    if (!C) return;
    injectCss();
    step = 0;
    let el = $("#intro");
    if (!el) {
      el = document.createElement("div");
      el.id = "intro";
      el.className = "intro";
      el.setAttribute("role", "dialog");
      el.setAttribute("aria-modal", "true");
      document.body.appendChild(el);
    }
    draw();
  }

  // Shown once: own active account, not seen yet, and only once the consent sheet (profile.js) is out of the way
  function maybeShow(me) {
    const u = me?.me;
    if (!u || u.intro_seen !== false || u.status === "pending" || !(u.token_scopes || []).includes("write")) return;
    let tries = 0;
    const wait = () => {
      if (++tries > 600) return;   // give up after ~10 minutes
      const busy = document.querySelector(".consent") || $("#app")?.hidden !== false || !$("#login")?.hidden;
      if (busy) { setTimeout(wait, 1000); return; }
      u.intro_seen = true;   // not again in this page session even if the PUT fails
      open();
    };
    setTimeout(wait, 400);
  }

  window.BodyIntro = { open };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx, me) { C = ctx; maybeShow(me); },
  });
})();
