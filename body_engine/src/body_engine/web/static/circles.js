/* Circles (body_engine/friends/, docs/friends-social-plan.md §3.2): create a circle, invite with a 72-hour link, join with
   a code (or an invite from a friend's referral sign-up), pick what to share (scopes; ECGs never), change it, leave; the owner
   can reset invites, remove members and dissolve. Opening /app/?circle=CODE shows the join sheet after sign-in.
   Separate from app.js: registered in window.BodyPlugins (init / meItem / bindMe); window.BodyCircles.open() for other pages.
   Behind the `friends` feature flag (docs/rollout.md): while it is off nothing is drawn and the API answers 404.
   Endpoints: GET /v1/friends, POST /v1/friends/circles, …/circles/{id}/invite, GET /v1/friends/invite?code=,
   POST /v1/friends/join, PATCH|DELETE /v1/friends/circles/{id}/me, DELETE …/members/{u}, DELETE …/circles/{id},
   DELETE /v1/friends/invites/{id}. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  let C = null;
  let ME = null;
  let data = null;
  const LABEL = {
    scores: tr("评分"), sleep: tr("睡眠"), training: tr("训练"), nutrition_summary: tr("饮食达标率"),
    nutrition: tr("具体饮食"), weight: tr("体重"), metrics: tr("每日指标"), labs: tr("化验"), supplements: tr("补剂"),
    journal: tr("日记"), events: tr("身体事件"), memory: tr("长期记忆"),
  };
  const CODE_KEY = "w2w-circle";

  const CSS = `.cc-card{background:var(--surface);border-radius:14px;padding:12px 14px;margin:0 0 12px}
.cc-head{display:flex;align-items:center;gap:8px}.cc-head b{font-size:16px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
.cc-mem{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.cc-mem span{display:inline-flex;align-items:center;gap:4px;font-size:13px;background:var(--surface-2);border-radius:8px;padding:3px 8px}
.cc-mem button{border:0;background:none;color:var(--text-2);padding:0 0 0 2px;font-size:14px;line-height:1}
.cc-sc{font-size:12px;color:var(--text-2);margin:10px 0 0}
.cc-act{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 0}
.cc-act button{border:0;background:none;padding:0;color:var(--text);text-decoration:underline;font-size:13px}
.cc-act button.bad{color:var(--bad)}
.cc-pick{margin:6px 0 0}.cc-pick h4{font-size:12px;color:var(--text-2);font-weight:600;margin:14px 0 4px}
.cc-pick label{display:flex;align-items:center;gap:10px;padding:8px 0;font-size:15px;border-top:1px solid var(--hair)}
.cc-pick label:first-of-type{border-top:0}.cc-pick input{width:20px;height:20px;margin:0}
.cc-never{font-size:12px;color:var(--text-2);margin:10px 0 0}
.cc-code{font:600 26px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.06em;text-align:center;margin:16px 0 4px;user-select:all;-webkit-user-select:all}
.cc-inv{display:flex;align-items:center;gap:10px;background:var(--surface);border-radius:14px;padding:12px 14px;margin:0 0 12px}
.cc-inv .f{flex:1;min-width:0;font-size:14px}.cc-inv .btn{flex:none;min-height:36px;padding:0 14px;font-size:14px}
.cc-err{color:var(--bad);font-size:13px;min-height:1.2em;margin:8px 0 0}`;

  function injectCss() {
    if ($("#circles-css")) return;
    const st = document.createElement("style");
    st.id = "circles-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // /app/?circle=XXXXX-XXXXX: kept through sign-in, used once
  (function rememberCode() {
    let v = "";
    try { v = new URLSearchParams(location.search).get("circle") || ""; } catch (e) { /* old browser */ }
    v = v.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10);
    if (v.length === 10) { try { sessionStorage.setItem(CODE_KEY, v); } catch (e) { /* storage blocked */ } }
  })();
  const takeCode = () => {
    let v = "";
    try { v = sessionStorage.getItem(CODE_KEY) || ""; sessionStorage.removeItem(CODE_KEY); } catch (e) { /* storage blocked */ }
    try {
      const u = new URL(location.href);
      if (u.searchParams.has("circle")) { u.searchParams.delete("circle"); history.replaceState(null, "", u.pathname + u.search + u.hash); }
    } catch (e) { /* old browser */ }
    return v;
  };

  async function load() {
    try { data = await C.api("/v1/friends"); } catch (e) { data = null; }
    return data;
  }

  const esc = (v) => C.esc(v);
  const scopeList = (scopes) => (scopes.length ? scopes.map((s) => LABEL[s] || s).join(" · ") : tr("什么都不共享"));

  function picker(chosen) {
    const all = data?.scopes || Object.keys(LABEL);
    const def = new Set(data?.default_scopes || ["scores", "sleep", "training", "nutrition_summary"]);
    const on = new Set(chosen || def);
    const row = (s) => `<label><input type="checkbox" data-scope="${esc(s)}"${on.has(s) ? " checked" : ""}><span>${esc(LABEL[s] || s)}</span></label>`;
    return `<div class="cc-pick"><h4>${esc(tr("推荐"))}</h4>${all.filter((s) => def.has(s)).map(row).join("")}
      <h4>${esc(tr("可选（适合家人）"))}</h4>${all.filter((s) => !def.has(s)).map(row).join("")}
      <p class="cc-never">${esc(tr("心电图永远不进圈子"))}</p></div>`;
  }
  const picked = (root) => [...root.querySelectorAll("[data-scope]")].filter((x) => x.checked).map((x) => x.dataset.scope);

  function consentGate(root) {
    if (data?.consent_ok !== false) return false;
    root.innerHTML = `<p class="para" style="margin:0">${esc(tr("同意书更新了"))}</p>
      <div class="btns"><button class="btn btn-primary" id="cc-consent">${esc(tr("查看并确认"))}</button></div>`;
    $("#cc-consent", root).onclick = () => { C.closeSheet(); window.BodyProfile?.consent?.(true); };
    return true;
  }

  // ---------------------------------------------------------------- sheets
  function circleCard(c) {
    const owner = c.is_owner;
    const mem = c.members.map((m) => `<span>${esc(m.display_name)}${m.owner ? ` <b class="badge plain">${esc(tr("圈主"))}</b>` : ""}${owner && !m.me
      ? `<button type="button" data-remove="${m.id}" data-name="${esc(m.display_name)}" aria-label="${esc(tr("移除"))}">×</button>` : ""}</span>`).join("");
    const acts = [`<button type="button" data-scopes="${c.id}">${esc(tr("改共享范围"))}</button>`];
    if (owner) acts.push(`<button type="button" data-invite="${c.id}">${esc(tr("邀请"))}</button>`,
      `<button type="button" data-reset="${c.id}">${esc(tr("重置邀请"))}</button>`);
    acts.push(`<button type="button" class="bad" data-leave="${c.id}">${esc(tr("退出"))}</button>`);
    if (owner) acts.push(`<button type="button" class="bad" data-dissolve="${c.id}">${esc(tr("解散"))}</button>`);
    const live = owner && c.invites?.live ? ` · ${esc(tr("{n} 个邀请有效", { n: c.invites.live }))}` : "";
    return `<div class="cc-card"><div class="cc-head"><b>${esc(c.name)}</b><span class="small muted">${esc(tr("{n} 人", { n: c.members.length }))}${live}</span></div>
      <div class="cc-mem">${mem}</div>
      <p class="cc-sc">${esc(tr("我共享：{list}", { list: scopeList(c.my_scopes) }))}</p>
      <div class="cc-act">${acts.join("")}</div></div>`;
  }

  function render(root) {
    if (!data) { root.innerHTML = `<p class="empty">${esc(tr("读取失败"))}</p>`; return; }
    if (consentGate(root)) return;
    const inv = data.invites.map((i) => `<div class="cc-inv"><span class="f">${esc(tr("{who} 邀请你加入「{name}」", { who: i.from_name || "", name: i.name }))}</span>
      <button class="btn btn-primary" type="button" data-accept="${i.id}" data-name="${esc(i.name)}">${esc(tr("加入"))}</button>
      <button class="btn" type="button" data-decline="${i.id}">${esc(tr("忽略"))}</button></div>`).join("");
    const list = data.circles.length ? data.circles.map(circleCard).join("") : `<p class="empty">${esc(tr("还没有圈子"))}</p>`;
    root.innerHTML = `${inv}${list}
      <div class="btns"><button class="btn btn-primary" id="cc-new">${esc(tr("建圈子"))}</button><button class="btn" id="cc-join">${esc(tr("用邀请码加入"))}</button></div>
      <p class="cc-err" id="cc-error" role="alert"></p>`;
    const err = (e) => { $("#cc-error", root).textContent = e.message; };
    const by = (sel, fn) => root.querySelectorAll(sel).forEach((b) => { b.onclick = () => fn(b).catch(err); });
    const find = (id) => data.circles.find((c) => String(c.id) === String(id));
    $("#cc-new", root).onclick = createSheet;
    $("#cc-join", root).onclick = () => codeSheet("");
    by("[data-accept]", async (b) => joinSheet({ invite_id: Number(b.dataset.accept), name: b.dataset.name }));
    by("[data-decline]", async (b) => { await C.api(`/v1/friends/invites/${b.dataset.decline}`, { method: "DELETE" }); await reopen(); });
    by("[data-scopes]", async (b) => scopesSheet(find(b.dataset.scopes)));
    by("[data-invite]", async (b) => inviteSheet(find(b.dataset.invite), false));
    by("[data-reset]", async (b) => {
      if (!confirm(tr("重置后，之前发出的邀请链接立即失效。确定？"))) return;
      await inviteSheet(find(b.dataset.reset), true);
    });
    by("[data-remove]", async (b) => {
      if (!confirm(tr("把 {name} 移出圈子？你们立即互相看不到。", { name: b.dataset.name }))) return;
      const cid = b.closest(".cc-card").querySelector("[data-scopes]").dataset.scopes;
      await C.api(`/v1/friends/circles/${cid}/members/${b.dataset.remove}`, { method: "DELETE" });
      await reopen();
    });
    by("[data-leave]", async (b) => {
      if (!confirm(tr("退出「{name}」？圈子里的人立即看不到你的数据。", { name: find(b.dataset.leave).name }))) return;
      await C.api(`/v1/friends/circles/${b.dataset.leave}/me`, { method: "DELETE" });
      await reopen();
    });
    by("[data-dissolve]", async (b) => {
      if (!confirm(tr("解散「{name}」？所有人立即互相看不到。", { name: find(b.dataset.dissolve).name }))) return;
      await C.api(`/v1/friends/circles/${b.dataset.dissolve}`, { method: "DELETE" });
      await reopen();
    });
  }

  async function reopen() {
    await load();
    open();
    updateItem();
    window.BodyCircles.onChange?.();
  }

  function open() {
    C.openSheet(tr("圈子"), `<p class="para" style="margin:0">${esc(tr("读取中…"))}</p>`, async (root) => {
      if (!data) await load();
      render(root);
    });
  }

  function createSheet() {
    C.openSheet(tr("建圈子"), `<input class="input" id="cc-name" maxlength="40" placeholder="${esc(tr("圈子名称"))}" aria-label="${esc(tr("圈子名称"))}">
      ${picker(null)}<div class="btns"><button class="btn btn-primary" id="cc-ok">${esc(tr("建好并邀请"))}</button></div>
      <p class="cc-err" id="cc-error" role="alert"></p>`, (root) => {
      $("#cc-ok", root).onclick = async () => {
        $("#cc-ok", root).disabled = true;
        try {
          const r = await C.api("/v1/friends/circles", { method: "POST", body: { name: $("#cc-name", root).value, scopes: picked(root) } });
          await load();
          updateItem();
          window.BodyCircles.onChange?.();
          showCode(r.circle, r.invite);
        } catch (e) { $("#cc-error", root).textContent = e.message; $("#cc-ok", root).disabled = false; }
      };
    });
  }

  async function inviteSheet(c, reset) {
    const r = await C.api(`/v1/friends/circles/${c.id}/invite`, { method: "POST", body: { reset } });
    await load();
    showCode(c, r);
  }

  function showCode(c, inv) {
    const link = `${location.origin}/app/?circle=${encodeURIComponent(inv.code)}`;
    const until = new Date(inv.expires_at);
    const text = tr("加入我在 Watch2Whoop 的圈子「{name}」：邀请码 {code}", { name: c.name, code: inv.code });
    C.openSheet(c.name, `<p class="para muted" style="margin:0">${esc(tr("邀请"))}</p><div class="cc-code">${esc(inv.code)}</div>
      <p class="para small muted" style="text-align:center;margin:0">${esc(tr("{when} 前有效", { when: until.toLocaleString(window.I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) }))}</p>
      <div class="btns"><button class="btn" id="cc-copy">${esc(tr("复制邀请码"))}</button><button class="btn btn-primary" id="cc-share">${esc(tr("分享链接"))}</button></div>
      <div class="btns"><button class="btn" id="cc-back">${esc(tr("返回"))}</button></div>`, (root) => {
      const copy = async (t, msg) => { try { await navigator.clipboard.writeText(t); C.toast(msg); } catch (e) { C.toast(t); } };
      $("#cc-copy", root).onclick = () => copy(inv.code, tr("已复制"));
      $("#cc-share", root).onclick = async () => {
        if (navigator.share) {
          try { await navigator.share({ title: "Watch2Whoop", text, url: link }); return; }
          catch (e) { if (e && e.name === "AbortError") return; }
        }
        copy(`${text} ${link}`, tr("链接已复制"));
      };
      $("#cc-back", root).onclick = open;
    });
  }

  function codeSheet(code) {
    C.openSheet(tr("用邀请码加入"), `<input class="input" id="cc-code" autocapitalize="characters" autocomplete="off" spellcheck="false" placeholder="XXXXX-XXXXX" value="${esc(code)}" aria-label="${esc(tr("邀请码"))}">
      <div class="btns"><button class="btn btn-primary" id="cc-next">${esc(tr("下一步"))}</button></div>
      <p class="cc-err" id="cc-error" role="alert"></p>`, (root) => {
      $("#cc-next", root).onclick = async () => {
        const v = $("#cc-code", root).value.trim();
        try {
          const pv = await C.api(`/v1/friends/invite?code=${encodeURIComponent(v)}`);
          joinSheet({ code: v, name: pv.name, owner: pv.owner_name, members: pv.members });
        } catch (e) { $("#cc-error", root).textContent = e.message; }
      };
    });
  }

  function joinSheet(t) {
    const sub = t.owner ? tr("{who} 的圈子 · {n} 人", { who: t.owner, n: t.members }) : "";
    C.openSheet(tr("加入「{name}」", { name: t.name }), `${sub ? `<p class="para muted" style="margin:0">${esc(sub)}</p>` : ""}
      <p class="para" style="margin:8px 0 0">${esc(tr("给圈子里的人看"))}</p>${picker(null)}
      <div class="btns"><button class="btn btn-primary" id="cc-ok">${esc(tr("加入"))}</button></div>
      <p class="cc-err" id="cc-error" role="alert"></p>`, (root) => {
      if (consentGate(root)) return;
      $("#cc-ok", root).onclick = async () => {
        $("#cc-ok", root).disabled = true;
        try {
          const body = { scopes: picked(root), ...(t.invite_id ? { invite_id: t.invite_id } : { code: t.code }) };
          await C.api("/v1/friends/join", { method: "POST", body });
          C.toast(tr("已加入"));
          await reopen();
          C.reload?.();   // the people you may view changed
        } catch (e) { $("#cc-error", root).textContent = e.message; $("#cc-ok", root).disabled = false; }
      };
    });
  }

  function scopesSheet(c) {
    C.openSheet(tr("给「{name}」看", { name: c.name }), `${picker(c.my_scopes)}
      <div class="btns"><button class="btn btn-primary" id="cc-ok">${esc(tr("common.save"))}</button><button class="btn" id="cc-back">${esc(tr("返回"))}</button></div>
      <p class="cc-err" id="cc-error" role="alert"></p>`, (root) => {
      $("#cc-back", root).onclick = open;
      $("#cc-ok", root).onclick = async () => {
        try {
          await C.api(`/v1/friends/circles/${c.id}/me`, { method: "PATCH", body: { scopes: picked(root) } });
          C.toast(tr("common.saved"));
          await reopen();
        } catch (e) { $("#cc-error", root).textContent = e.message; }
      };
    });
  }

  const shortVal = () => (data ? String(data.circles.length || "") + (data.invites.length ? ` · ${tr("{n} 个邀请", { n: data.invites.length })}` : "") : "");
  function updateItem() {
    const v = document.getElementById("me-circles-val");
    if (v) v.textContent = shortVal();
  }

  const on = () => !!window.BodyFlags?.on("friends");
  window.BodyCircles = { open, load: () => load(), data: () => data, onChange: null };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: async (ctx, me) => {
      C = ctx; ME = me?.me || null; injectCss();
      if (!ME || !(me?.flags || []).includes("friends")) return;   // a ?circle= code stays remembered until the flag is on
      const code = takeCode();
      await load();
      if (!code || !data) return;
      try {
        const pv = await C.api(`/v1/friends/invite?code=${encodeURIComponent(code)}`);
        joinSheet({ code, name: pv.name, owner: pv.owner_name, members: pv.members });
      } catch (e) { codeSheet(code); }
    },
    meItem: (chev) => (ME && on() ? `<button class="item" id="me-circles"><span class="row-title">${esc(tr("圈子"))}</span><span class="val" id="me-circles-val">${esc(shortVal())}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => {
      const b = $("#me-circles", view);
      if (!b) return;
      b.onclick = open;
      load().then(updateItem);
    },
  });
})();
