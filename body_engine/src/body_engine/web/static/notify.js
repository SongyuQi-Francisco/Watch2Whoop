/* Notifications (issue #175, flag notifications): a bell with the unread count in the header, the list (tap = read + open
   its page), 「全部已读」, and the settings (push per category, quiet hours). Server: notify/api.py.
   Links: a notification's url is a path of this page; its query or hash parameters say where to go: tab=<tab> switches
   the tab, notifications opens the list; every link is also announced as the window event "body:link" (detail = the
   parameters, e.g. {tab: "friends", feed: "3"}) so the feature that sent it can open its own detail (e.g. a post).
   In the Watch2Whoop app: the first visit to the Friends tab or to these settings sends "push:ask" to the app, which asks
   iOS for permission (once) and registers the device; the app answers with the event "watch2whoop:push" {state}. The
   page sends "badge:<unread>" for the app icon; the app fires "watch2whoop:notification" when a push arrives while open.
   Separate from app.js: registered in window.BodyPlugins (init / onTab). timeAgo / parseLink are pure (module.exports). */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);
  const $ = (s, r = document) => r.querySelector(s);
  const POLL_MS = 120000;
  let C = null;
  let ME = null;
  let unread = 0;
  let items = null;
  let more = false;
  let pushState = null;   // the app's answer: granted / denied / unknown (null = not in the app or an older app)
  let asked = false;

  /** Query and hash parameters together: "/app/?tab=friends&feed=3" or "/app/#tab=friends&post=9" -> {tab: "friends", …};
      "#notifications" -> {notifications: ""}. */
  function parseLink(url) {
    const s = String(url || ""), h = s.indexOf("#"), q = s.indexOf("?");
    const parts = [q >= 0 ? s.slice(q + 1, h > q ? h : undefined) : "", h >= 0 ? s.slice(h + 1) : ""].join("&");
    const out = {};
    for (const part of parts.split("&")) {
      if (!part) continue;
      const [k, v = ""] = part.split("=");
      out[decodeURIComponent(k)] = decodeURIComponent(v);
    }
    return out;
  }

  /** Short relative time: 刚刚 / 5 分钟前 / 3 小时前 / 2 天前 / the date. */
  function timeAgo(iso, now = Date.now()) {
    const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
    if (s < 60) return tr("刚刚");
    if (s < 3600) return tr("{n} 分钟前", { n: Math.floor(s / 60) });
    if (s < 86400) return tr("{n} 小时前", { n: Math.floor(s / 3600) });
    if (s < 7 * 86400) return tr("{n} 天前", { n: Math.floor(s / 86400) });
    return new Date(iso).toLocaleDateString(window.I18N.locale(), { month: "numeric", day: "numeric" });
  }

  const BELL = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2H4.5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M10 20.5a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`;
  const CSS = `.nt-bell{position:relative;width:34px;height:34px;border-radius:50%;border:0;background:var(--surface);color:var(--text-2);display:inline-flex;align-items:center;justify-content:center}
.nt-bell svg{width:18px;height:18px}
.nt-bell .nt-dot{position:absolute;top:-2px;right:-3px;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:var(--bad);color:#fff;font-size:11px;font-weight:700;line-height:17px;text-align:center;box-shadow:0 0 0 2px var(--bg)}
.nt-item{display:block;width:100%;border:0;background:none;text-align:left;padding:12px 4px;color:inherit}
.nt-item + .nt-item{border-top:1px solid var(--hair)}
.nt-item .nt-t{font-weight:600;font-size:15px;display:flex;gap:8px;align-items:center}
.nt-item.read .nt-t{font-weight:500;color:var(--text-2)}
.nt-item .nt-t .dot{background:var(--bad)}
.nt-item .nt-b{font-size:14px;color:var(--text-2);margin-top:2px}
.nt-item .nt-w{font-size:12px;color:var(--text-3);margin-top:2px}
.nt-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0}
.nt-row + .nt-row{border-top:1px solid var(--hair)}
.nt-times{display:flex;gap:8px;align-items:center;margin:4px 0 0}
.nt-h{font-size:13px;font-weight:600;margin:16px 0 0;color:var(--text-2)}`;

  function injectCss() {
    if ($("#notify-css")) return;
    const st = document.createElement("style");
    st.id = "notify-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  const on = () => !!ME && !!window.BodyFlags?.on("notifications");
  const tellApp = (msg) => { try { window.Watch2Whoop?.postMessage(msg); } catch (e) { /* older app */ } };

  // ---------------------------------------------------------------- bell
  let badgeSent = null;
  function drawBell() {
    let b = $("#nt-bell");
    if (!on()) { b?.remove(); return; }
    if (unread !== badgeSent) { badgeSent = unread; tellApp(`badge:${unread}`); }   // the app icon's badge (app 1.0.7)
    if (!b) {
      b = document.createElement("button");
      b.id = "nt-bell";
      b.className = "nt-bell";
      b.type = "button";
      b.onclick = openList;
      const who = $("#who");
      who ? who.before(b) : $(".top-r")?.append(b);
    }
    b.innerHTML = BELL + (unread ? `<span class="nt-dot">${unread > 99 ? "99+" : unread}</span>` : "");
    b.setAttribute("aria-label", unread ? tr("通知（{n} 条未读）", { n: unread }) : tr("通知"));
  }

  async function refreshCount() {
    if (!on() || document.hidden) return;
    try { unread = (await C.api("/v1/users/me/notifications/unread")).unread || 0; } catch (e) { return; }
    drawBell();
  }

  // ---------------------------------------------------------------- list
  function itemHtml(n) {
    return `<button class="nt-item${n.read ? " read" : ""}" type="button" data-nt="${n.id}">
      <div class="nt-t">${n.read ? "" : '<span class="dot"></span>'}<span>${C.esc(n.title)}</span></div>
      ${n.body ? `<div class="nt-b">${C.esc(n.body)}</div>` : ""}<div class="nt-w">${C.esc(timeAgo(n.created_at))}</div></button>`;
  }

  function listHtml() {
    if (!items) return `<p class="empty">${C.esc(tr("读取失败"))}</p>`;
    const list = items.length ? `<div>${items.map(itemHtml).join("")}</div>` : `<p class="empty">${C.esc(tr("还没有通知"))}</p>`;
    return `${list}${more ? `<div class="btns"><button class="btn" type="button" id="nt-more">${C.esc(tr("更早的"))}</button></div>` : ""}
      <div class="btns">${unread ? `<button class="btn" type="button" id="nt-all">${C.esc(tr("全部已读"))}</button>` : ""}
      <button class="btn" type="button" id="nt-settings">${C.esc(tr("通知设置"))}</button></div>`;
  }

  async function loadList(before) {
    const r = await C.api(`/v1/users/me/notifications${before ? `?before=${before}` : ""}`);
    items = before ? [...(items || []), ...r.items] : r.items;
    more = r.more;
    unread = r.unread;
    drawBell();
  }

  function bindList(root) {
    for (const b of root.querySelectorAll("[data-nt]")) b.onclick = () => openItem(items.find((n) => n.id === Number(b.dataset.nt)));
    const m = $("#nt-more", root);
    if (m) m.onclick = async () => { await loadList(items[items.length - 1].id); root.innerHTML = listHtml(); bindList(root); };
    const all = $("#nt-all", root);
    if (all) all.onclick = async () => {
      await read({});
      for (const n of items) n.read = true;
      root.innerHTML = listHtml();
      bindList(root);
    };
    $("#nt-settings", root).onclick = openSettings;
  }

  async function read(body) {
    try { unread = (await C.api("/v1/users/me/notifications/read", { method: "POST", body })).unread; } catch (e) { return; }
    drawBell();
  }

  function openItem(n) {
    if (!n) return;
    if (!n.read) { n.read = true; read({ ids: [n.id] }); }
    if (n.url) { C.closeSheet(); go(n.url); }
  }

  function openList() {
    C.openSheet(tr("通知"), `<p class="para" style="margin:0">${C.esc(tr("读取中…"))}</p>`, async (root) => {
      try { await loadList(); } catch (e) { items = null; }
      root.innerHTML = listHtml();
      bindList(root);
    });
  }

  // ---------------------------------------------------------------- settings
  const seg = (id, value) => `<div class="seg" data-seg="${id}"><button type="button" data-v="1" aria-pressed="${value}">${C.esc(tr("开"))}</button><button type="button" data-v="0" aria-pressed="${!value}">${C.esc(tr("关"))}</button></div>`;

  function pushLine(st) {
    if (!window.Watch2Whoop) return tr("推送只在 Watch2Whoop iPhone app 里有；网页上看这里的列表");
    if (pushState === "denied") return tr("iPhone 上关掉了这个 app 的通知：「设置」→「通知」→「Watch2Whoop」→ 打开「允许通知」");
    if (!st.push_configured) return tr("这台服务器还没配置推送，通知只在这里显示");
    if (!st.devices) return tr("这台手机还没登记推送");
    return tr("这台手机会收到推送");
  }

  function settingsHtml(st) {
    const cats = Object.keys(st.categories).map((k) => `<div class="nt-row"><span>${C.esc(st.labels[k] || k)}</span>${seg(`cat:${k}`, st.categories[k])}</div>`).join("");
    const q = st.quiet;
    const ask = window.Watch2Whoop && pushState !== "granted" && pushState !== "denied"
      ? `<div class="btns"><button class="btn" type="button" id="nt-ask">${C.esc(tr("打开推送通知"))}</button></div>` : "";
    return `<p class="small faint" style="margin:0 0 8px">${C.esc(pushLine(st))}</p>${ask}
      <div class="nt-h">${C.esc(tr("推送哪些"))}</div>${cats}
      <div class="nt-h">${C.esc(tr("免打扰"))}</div>
      <div class="nt-row"><span>${C.esc(tr("免打扰时段不推送"))}</span>${seg("quiet", q.enabled)}</div>
      <div class="nt-times"${q.enabled ? "" : " hidden"}><input class="input time-input" id="nt-qs" type="time" value="${C.esc(q.start)}" aria-label="${C.esc(tr("开始"))}">
        <span>–</span><input class="input time-input" id="nt-qe" type="time" value="${C.esc(q.end)}" aria-label="${C.esc(tr("结束"))}"></div>
      <div class="btns"><button class="btn" type="button" id="nt-back">${C.esc(tr("返回"))}</button></div>`;
  }

  async function save(root, body) {
    try {
      const st = await C.api("/v1/users/me/notification-settings", { method: "PUT", body });
      root.innerHTML = settingsHtml(st);
      bindSettings(root, st);
    } catch (e) { C.toast(tr("没保存上，请再试一次")); }
  }

  function bindSettings(root, st) {
    for (const s of root.querySelectorAll("[data-seg]")) {
      for (const b of s.querySelectorAll("button")) b.onclick = () => {
        const v = b.dataset.v === "1", key = s.dataset.seg;
        save(root, key === "quiet" ? { quiet: { enabled: v } } : { categories: { [key.slice(4)]: v } });
      };
    }
    const qs = $("#nt-qs", root), qe = $("#nt-qe", root);
    for (const el of [qs, qe]) if (el) el.onchange = () => { if (qs.value && qe.value) save(root, { quiet: { start: qs.value, end: qe.value } }); };
    const ask = $("#nt-ask", root);
    if (ask) ask.onclick = () => { asked = true; tellApp("push:ask"); };
    $("#nt-back", root).onclick = openList;
  }

  let settingsRoot = null;
  function openSettings() {
    askOnce();
    C.openSheet(tr("通知设置"), `<p class="para" style="margin:0">${C.esc(tr("读取中…"))}</p>`, async (root) => {
      settingsRoot = root;
      try {
        const st = await C.api("/v1/users/me/notification-settings");
        root.innerHTML = settingsHtml(st);
        bindSettings(root, st);
      } catch (e) { root.innerHTML = `<p class="empty">${C.esc(tr("读取失败"))}</p>`; }
    });
  }

  // ---------------------------------------------------------------- app permission + links
  function askOnce() {
    if (asked || !on() || !window.Watch2Whoop) return;
    asked = true;
    tellApp("push:ask");   // the app asks iOS only while the permission was never decided, otherwise just re-registers
  }

  function go(url) {
    const p = parseLink(url);
    if (p.tab) C.setTab?.(p.tab);
    window.dispatchEvent(new CustomEvent("body:link", { detail: p }));
    if ("notifications" in p) openList();
  }

  // A link opened from a push (the app loads its url) or typed: act once, then drop its parameters so a reload does not
  // open it again (other query parameters such as ?date= stay)
  const LINK_KEYS = ["tab", "notifications", "feed", "post"];
  function fromLocation() {
    const p = parseLink(location.search + location.hash);
    if (!LINK_KEYS.some((k) => k in p)) return;
    const q = new URLSearchParams(location.search);
    for (const k of LINK_KEYS) q.delete(k);
    history.replaceState(null, "", location.pathname + (q.toString() ? `?${q}` : ""));
    if (on()) go(`?${new URLSearchParams(p)}`);
  }

  if (typeof module !== "undefined") { module.exports = { parseLink, timeAgo }; return; }   // node (tests/test_notify.py)
  window.addEventListener("watch2whoop:push", (e) => {
    pushState = e.detail?.state || null;
    if (settingsRoot?.isConnected && $("#nt-back", settingsRoot)) openSettings();
  });
  window.addEventListener("hashchange", fromLocation);
  window.addEventListener("watch2whoop:foreground", refreshCount);
  window.addEventListener("watch2whoop:notification", refreshCount);   // the app got a push while open
  document.addEventListener("visibilitychange", refreshCount);
  setInterval(refreshCount, POLL_MS);
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => {
      C = ctx; ME = me?.me || null; injectCss();
      drawBell();
      refreshCount();
      setTimeout(fromLocation, 0);
    },
    onTab: (tab) => { if (tab === "friends") askOnce(); },
  });
})();
