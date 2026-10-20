/* 「我 → 反馈记录」 (body_engine/feedback/, docs/ai-feedback-plan.md): bugs and feature requests the user's AI reported to the
   developers, their status, the feedback mode (auto / ask / off) and withdrawing a report nobody has looked at yet.
   No input form on purpose: users report by telling their AI. Separate from app.js: registered in window.BodyPlugins
   (init / meItem / bindMe). Endpoints: GET /v1/users/me/feedback, PUT …/feedback-mode, DELETE …/feedback/{FB-N}. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  let C = null;
  let ME = null;
  let data = null;
  const MODES = [["auto", "自动报告"], ["ask", "先问我"], ["off", "关闭"]];
  const KIND = { bug: "问题", feature: "需求" };
  const DONE = new Set(["fixed", "public"]);

  const CSS = `.fb-mode{display:flex;margin:0 0 14px}.fb-mode button{flex:1}
.fb-item{padding:12px 0;border-bottom:1px solid var(--surface)}
.fb-item:last-child{border-bottom:0}
.fb-head{display:flex;gap:8px;align-items:baseline;font-size:12px;color:var(--text-2)}
.fb-head .fb-st{margin-left:auto;font-weight:600;color:var(--text)}
.fb-head .fb-st.ok{color:var(--good)}
.fb-title{font-size:15px;margin:4px 0 0}
.fb-note{font-size:13px;color:var(--text-2);margin:4px 0 0}
.fb-act{display:flex;gap:12px;margin-top:6px;font-size:13px}
.fb-act a,.fb-act button{background:none;border:0;padding:0;color:var(--text);text-decoration:underline;font-size:13px}
.fb-err{color:var(--bad);font-size:13px;min-height:1.2em;margin:8px 0 0}`;

  function injectCss() {
    if ($("#feedback-css")) return;
    const st = document.createElement("style");
    st.id = "feedback-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  async function load() {
    try { data = await C.api("/v1/users/me/feedback"); } catch (e) { data = null; }
    return data;
  }

  const shortVal = () => (data ? (data.reports.length ? String(data.reports.length) : (data.mode === "off" ? "关闭" : "")) : "");

  function itemHtml(r) {
    const esc = C.esc;
    const day = String(r.created_at || "").slice(5, 10);
    const acts = [];
    if (r.public_url) acts.push(`<a href="${esc(r.public_url)}" target="_blank" rel="noopener">公开链接</a>`);
    if (r.withdrawable) acts.push(`<button type="button" data-withdraw="${esc(r.id)}">撤回</button>`);
    return `<div class="fb-item">
      <div class="fb-head"><span>${esc(r.id)}</span><span>${esc(KIND[r.kind] || r.kind)}</span><span>${esc(day)}</span>
        ${r.votes ? `<span>+${r.votes}</span>` : ""}${r.voted ? "<span>我 +1</span>" : ""}
        <span class="fb-st${DONE.has(r.status) ? " ok" : ""}">${esc(r.status_label || r.status)}</span></div>
      <p class="fb-title">${esc(r.title)}</p>
      ${r.note ? `<p class="fb-note">${esc(r.note)}</p>` : ""}
      ${acts.length ? `<div class="fb-act">${acts.join("")}</div>` : ""}
    </div>`;
  }

  function render(root) {
    if (!data) { root.innerHTML = `<p class="empty">读取失败</p>`; return; }
    const seg = data.available
      ? `<div class="seg fb-mode" role="group" aria-label="反馈模式">${MODES.map(([k, t]) =>
        `<button type="button" data-mode="${k}" aria-pressed="${data.chosen_mode === k}">${t}</button>`).join("")}</div>`
      : `<p class="para muted small">反馈暂停</p>`;
    const list = data.reports.length ? data.reports.map(itemHtml).join("") : `<p class="empty">还没有反馈</p>`;
    root.innerHTML = `${seg}${list}<p class="fb-err" id="fb-error" role="alert"></p>`;
    root.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = async () => {
        try {
          await C.api("/v1/users/me/feedback-mode", { method: "PUT", body: { mode: b.dataset.mode } });
          data.chosen_mode = b.dataset.mode;
          await load();
          render(root);
        } catch (e) { $("#fb-error", root).textContent = e.message; }
      };
    });
    root.querySelectorAll("[data-withdraw]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        try {
          await C.api(`/v1/users/me/feedback/${encodeURIComponent(b.dataset.withdraw)}`, { method: "DELETE" });
          await load();
          render(root);
          updateItem();
        } catch (e) { $("#fb-error", root).textContent = e.message; b.disabled = false; }
      };
    });
  }

  function open() {
    C.openSheet("反馈记录", `<p class="para" style="margin:0">读取中…</p>`, async (root) => {
      await load();
      render(root);
    });
  }

  function updateItem() {
    const v = document.getElementById("me-feedback-val");
    if (v) v.textContent = shortVal();
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => { C = ctx; ME = me?.me || null; injectCss(); },
    meItem: (chev) => (ME ? `<button class="item" id="me-feedback"><span class="row-title">反馈记录</span><span class="val" id="me-feedback-val">${C.esc(shortVal())}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => {
      const b = $("#me-feedback", view);
      if (!b) return;
      b.onclick = open;
      load().then(updateItem);
    },
  });
})();
