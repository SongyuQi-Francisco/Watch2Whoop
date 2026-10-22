/* 「我 → 设置 → 导入 → 从 Hevy 导入」 (issue #161, rollout flag hevy_import, training/hevy_import.py): upload the CSV Hevy
   exports, see what was imported, and match the exercise names the library did not know. The page gzips the file before
   sending (the public server takes 2 MB per request; a CSV compresses ~10x); browsers without CompressionStream send it plain.
   Separate from app.js: registered in window.BodyPlugins (init / meItem / meGroup / bindMe).
   Endpoints: GET / POST /v1/users/me/training/import/hevy, PUT …/import/hevy/map, GET …/training/exercises (the picker). */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const URL_ = "/v1/users/me/training/import/hevy";
  let C = null;
  let ON = false;
  let data = null;
  let library = null;

  const CSS = `.hv-steps{margin:0 0 12px;padding-left:20px;font-size:14px;color:var(--text-2)}
.hv-steps li{margin:4px 0}
.hv-pick{display:flex;gap:10px;align-items:center;margin:0 0 6px}
.hv-msg{font-size:13px;min-height:1.2em;margin:6px 0 10px;color:var(--text-2)}
.hv-msg.err{color:var(--bad)}
.hv-un{display:flex;flex-direction:column;gap:6px;padding:10px 0;border-top:1px solid var(--hair)}
.hv-un-head{display:flex;justify-content:space-between;gap:10px;font-size:14px}
.hv-un-head span:last-child{color:var(--text-3);font-variant-numeric:tabular-nums}
.hv-un select{width:100%;font-size:14px;padding:6px;border-radius:8px;border:1px solid var(--hair);background:var(--surface);color:var(--text)}`;

  function injectCss() {
    if ($("#hevy-import-css")) return;
    const st = document.createElement("style");
    st.id = "hevy-import-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  async function load() {
    try { data = await C.api(URL_); } catch (e) { data = null; }
    return data;
  }

  const shortVal = () => (data?.last ? tr("{n} 次训练", { n: data.last.workouts }) : "");

  async function loadLibrary() {
    if (library) return library;
    try {
      const r = await C.api("/v1/users/me/training/exercises?limit=200");
      library = (r.exercises || []).slice().sort((a, b) => String(a.name_zh).localeCompare(String(b.name_zh), "zh"));
    } catch (e) { library = []; }
    return library;
  }

  const kv = (k, v) => `<div class="kv"><span class="k">${C.esc(k)}</span><span class="v">${C.esc(String(v))}</span></div>`;

  function resultHtml(r) {
    if (!r) return "";
    return `<div class="section"><div class="section-head"><h2>${C.esc(tr("上次导入"))}</h2></div><div class="card">
      ${kv(tr("训练次数"), r.workouts)}${kv(tr("组数"), r.sets)}${kv(tr("未对上的动作"), (r.unmatched || []).length)}
      ${r.skipped_rows ? kv(tr("跳过的行"), r.skipped_rows) : ""}
      ${r.start ? kv(tr("日期"), r.start === r.end ? r.start : `${r.start} – ${r.end}`) : ""}</div></div>`;
  }

  function unmatchedHtml(list) {
    if (!list?.length) return "";
    const opts = (library || []).map((x) => `<option value="${C.esc(x.slug)}">${C.esc(window.I18N.locale().startsWith("en") ? (x.name_en || x.name_zh) : x.name_zh)}</option>`).join("");
    return `<div class="section"><div class="section-head"><h2>${C.esc(tr("对应到动作库"))}</h2></div><div class="card">
      ${list.map((u, i) => `<div class="hv-un"><div class="hv-un-head"><span>${C.esc(u.name)}</span><span>${C.esc(tr("{n} 组", { n: u.sets }))}</span></div>
        <select data-i="${i}" aria-label="${C.esc(tr("对应到动作库"))}"><option value="">${C.esc(tr("未对应"))}</option>${opts}</select></div>`).join("")}
    </div></div>`;
  }

  function render(root) {
    root.innerHTML = `<ol class="hv-steps"><li>${C.esc(tr("Hevy：设置 → 导出数据 → 导出训练"))}</li><li>${C.esc(tr("选择导出的 CSV"))}</li></ol>
      <div class="hv-pick"><input type="file" id="hv-file" accept=".csv,text/csv" hidden>
        <button type="button" class="btn" id="hv-choose">${C.esc(tr("选择 CSV"))}</button></div>
      <p class="hv-msg" id="hv-msg" role="status"></p>
      ${resultHtml(data?.last)}${unmatchedHtml(data?.unmatched)}`;
    const file = $("#hv-file", root);
    $("#hv-choose", root).onclick = () => file.click();
    file.onchange = () => file.files?.[0] && upload(root, file.files[0]);
    root.querySelectorAll("select[data-i]").forEach((sel) => {
      sel.onchange = async () => {
        const u = data.unmatched[Number(sel.dataset.i)];
        sel.disabled = true;
        try {
          const r = await C.api(`${URL_}/map`, { method: "PUT", body: { name: u.name, exercise: sel.value || null } });
          data.unmatched = r.unmatched;
          C.toast(tr("已对应"));
          render(root);
        } catch (e) { msg(root, e.message, true); sel.disabled = false; }
      };
    });
  }

  function msg(root, text, err) {
    const m = $("#hv-msg", root);
    if (!m) return;
    m.textContent = text || "";
    m.classList.toggle("err", !!err);
  }

  async function body(file) {
    if (window.CompressionStream) {
      const blob = await new Response(file.stream().pipeThrough(new CompressionStream("gzip"))).blob();
      return { blob, type: "application/gzip" };
    }
    return { blob: file, type: "text/csv" };
  }

  async function upload(root, file) {
    const lim = data?.limits || {};
    if (lim.csv_bytes && file.size > lim.csv_bytes) { msg(root, tr("文件太大"), true); return; }
    const btn = $("#hv-choose", root);
    btn.disabled = true;
    msg(root, tr("导入中…"));
    try {
      const { blob, type } = await body(file);
      if (lim.upload_bytes && blob.size > lim.upload_bytes) throw new Error(tr("文件太大"));
      let r;
      try {
        r = await fetch(URL_, { method: "POST", credentials: "same-origin", body: blob,
                                headers: { "X-Body-Web": "1", "Content-Type": type, Accept: "application/json", ...window.I18N.headers() } });
      } catch (e) { throw new Error(tr("err.offline")); }
      let res = null;
      try { res = await r.json(); } catch (e) { /* no body */ }
      if (!r.ok) throw new Error(typeof res?.detail === "string" ? res.detail : tr("err.request", { status: r.status }));
      await Promise.all([load(), res.unmatched?.length ? loadLibrary() : null]);
      render(root);
      msg(root, tr("导入完成：{w} 次训练 · {s} 组 · {u} 个动作没对上", { w: res.workouts, s: res.sets, u: res.unmatched.length }));
      updateItem();
      C.reload?.();
    } catch (e) {
      msg(root, e.message, true);
      btn.disabled = false;
    }
  }

  function open() {
    C.openSheet(tr("从 Hevy 导入"), `<p class="para" style="margin:0">${C.esc(tr("读取中…"))}</p>`, async (root) => {
      await load();
      if (data?.unmatched?.length) await loadLibrary();
      render(root);
    });
  }

  function updateItem() {
    const v = document.getElementById("me-import-hevy-val");
    if (v) v.textContent = shortVal();
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => { C = ctx; ON = !!me?.me && (me.flags || []).includes("hevy_import"); injectCss(); },
    meGroup: "settings-import",
    meItem: (chev) => (ON ? `<button class="item" id="me-import-hevy"><span class="row-title">${C.esc(tr("从 Hevy 导入"))}</span><span class="val" id="me-import-hevy-val">${C.esc(shortVal())}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => {
      const b = $("#me-import-hevy", view);
      if (!b) return;
      b.onclick = open;
      load().then(updateItem);
    },
  });
})();
