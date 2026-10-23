/* Private remarks for circle members (#212, flag member_remarks; server friends/remarks.py). A remark is what I call
   someone; only I see it. The server puts `remark` next to the original name wherever it sends a co-member (cards,
   leaderboard, feed posts and comments, circle member lists, /v1/web/me viewable) and leaves it out while the flag is off,
   so drawing needs no flag; only the 「设置备注」 entry checks it.
   window.BodyRemarks { on(), label(remark, name), html(remark, name), button(id, name, remark), open(member) } + BodyPlugins init. */
(() => {
  "use strict";
  let C = null;
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (v) => (C ? C.esc(v) : String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const on = () => !!window.BodyFlags?.on("member_remarks");
  const CSS = `
    .rm-orig{font-size:.78em;font-weight:400;color:var(--text-2);margin-left:5px;white-space:nowrap}
    .rm-more{flex:none;border:0;background:none;color:var(--text-2);font-size:18px;line-height:1;padding:4px 6px;margin:-4px 0;border-radius:8px;cursor:pointer}
    .rm-more:active{opacity:.5}
    .rm-sheet .field{margin-top:4px}`;
  function injectCss() {
    if ($("#remarks-css")) return;
    const st = document.createElement("style");
    st.id = "remarks-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }
  injectCss();

  // Plain text (titles, aria labels, sheet headings) and HTML (remark + the original name in small type)
  const label = (remark, name) => remark || name || "";
  const html = (remark, name) => (remark ? `${esc(remark)}<span class="rm-orig">${esc(name)}</span>` : esc(name));

  // The 「⋯」 button on a member card; app.js binds [data-remark] to open()
  const button = (id, name, remark) => (on()
    ? `<button type="button" class="rm-more" data-remark="${Number(id)}" data-name="${esc(name)}" data-rm="${esc(remark || "")}" aria-label="${esc(tr("设置备注"))}" title="${esc(tr("设置备注"))}">⋯</button>`
    : "");

  let MAX = 20;   // the server's friends/params.py REMARK_MAX, taken from the overview (remark_max) when it is there
  /* open({ id, name, remark, max, onDone }): a sheet with one text field; empty clears the remark. */
  function open(m) {
    if (!C || !on()) return;
    if (m.max) MAX = m.max;
    C.openSheet(tr("设置备注"), `<div class="rm-sheet">
        <p class="small muted" style="margin:0">${esc(tr("原名：{name}", { name: m.name }))}</p>
        <div class="field"><input class="input" id="rm-text" maxlength="${MAX}" value="${esc(m.remark || "")}" placeholder="${esc(tr("备注（只有你看得到）"))}" aria-label="${esc(tr("备注名"))}" autocomplete="off" enterkeyhint="done"></div>
        <p class="small muted" id="rm-msg" role="alert" style="margin:6px 0 0"></p>
        <div class="btns"><button class="btn btn-primary" type="button" id="rm-save">${esc(tr("保存"))}</button>
          ${m.remark ? `<button class="btn" type="button" id="rm-clear">${esc(tr("清除备注"))}</button>` : ""}</div></div>`, (root) => {
      const input = $("#rm-text", root);
      const send = async (text) => {
        for (const b of root.querySelectorAll("button")) b.disabled = true;
        try {
          const r = await C.api(`/v1/friends/remarks/${Number(m.id)}`, { method: "PUT", body: { remark: text } });
          C.closeSheet();
          C.toast(tr(r?.remark ? "备注已保存" : "备注已清除"));
          (m.onDone || C.reload)?.();
        } catch (e) {
          $("#rm-msg", root).textContent = e.message;
          for (const b of root.querySelectorAll("button")) b.disabled = false;
        }
      };
      $("#rm-save", root).onclick = () => send(input.value.trim());
      input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); send(input.value.trim()); } };
      const clear = $("#rm-clear", root);
      if (clear) clear.onclick = () => send("");
      setTimeout(() => input.focus(), 50);
    });
  }

  window.BodyRemarks = { on, label, html, button, open };
  (window.BodyPlugins = window.BodyPlugins || []).push({ init: (ctx) => { C = ctx; } });
})();
