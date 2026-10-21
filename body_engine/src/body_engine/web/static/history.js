/* Me -> "Import health history" (「导入健康历史」): upload the export.zip exported from the iPhone Health app; the server imports it in the background (body_engine/history/).
   Separate from app.js: registered in window.BodyPlugins (init / meItem / bindMe), which app.js calls after startup and when rendering / binding the Me page.
   ctx = { api, openSheet, closeSheet, toast, esc }.
   Chunked upload (each chunk <= 1.5MB, the public nginx limit is 2m); if interrupted it resumes from the position the server recorded; picking the same file again continues the upload.
   Endpoints: GET/POST /v1/users/me/history-import, PUT …/{id}/data?offset=, POST …/{id}/complete, DELETE …/{id}. */
(() => {
  "use strict";
  let C = null;
  let job = null;
  let uploading = null;   // {sent, total, cancel}
  let poll = null;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const MB = (n) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
  const BASE = "/v1/users/me/history-import";
  const PHASE = { parsing: tr("解析中"), saving: tr("写入中"), scores: tr("重算评分") };

  function shortStatus(j) {
    if (uploading) return tr("上传中 {pct}%", { pct: Math.floor((uploading.sent / uploading.total) * 100) });
    if (!j) return tr("未导入");
    if (j.status === "uploading") return tr("没传完");
    if (j.status === "queued") return tr("排队中");
    if (j.status === "processing") return j.phase === "parsing" && j.progress != null ? tr("导入中 {pct}%", { pct: Math.round(j.progress * 100) }) : tr("导入中");
    if (j.status === "done") return j.result?.last_day ? tr("已导入到 {day}", { day: window.I18N.monthDay(new Date(`${j.result.last_day}T12:00:00`)) }) : tr("已导入");
    if (j.status === "failed") return tr("失败");
    return tr("未导入");
  }

  async function refreshJob() {
    try { job = (await C.api(BASE)).job; } catch (e) { /* older servers don't have this endpoint */ }
    return job;
  }

  function stepsHtml() {
    // {file} / {zh}: the export file's name in bold (the Chinese system calls it 导出.zip)
    const step3 = esc(tr("回到这里点「选择文件」，选刚才的 {file}（中文系统叫 {zh}）。")).replace("{file}", "<b>export.zip</b>")
      .replace("{zh}", "<b>导出.zip</b>");   // i18n-ok (file name on a Chinese iPhone)
    return `<p class="para" style="margin:0">${esc(tr("app 只同步最近 90 天。更早的历史（戴手表以来的心率、睡眠、训练）可以一次性导入，导入后趋势和评分会补上。"))}</p>
      <ol class="para" style="padding-left:20px;white-space:normal">
        <li>${esc(tr("打开 iPhone 的「健康」app → 右上角头像 → 拉到最下面「导出所有健康数据」→「导出」，等几分钟。"))}</li>
        <li>${esc(tr("弹出分享菜单后选「存储到“文件”」→「我的 iPhone」→「存储」。"))}</li>
        <li>${step3}</li>
      </ol>
      <p class="small muted">${esc(tr("上传时别锁屏、别切走（文件一般 20–100MB）；断了重新选同一个文件会接着传。文件只用来导入，导入完立刻从服务器删除。"))}</p>`;
  }

  function statusHtml() {
    if (uploading) {
      const pct = (uploading.sent / uploading.total) * 100;
      return `<div class="group" style="padding:14px 16px"><div class="row-title">${esc(tr("上传中 {sent} / {total}", { sent: MB(uploading.sent), total: MB(uploading.total) }))}</div>
        <div class="bar" style="margin-top:8px"><span style="width:${pct.toFixed(1)}%"></span></div>
        <div class="btns"><button class="btn" id="hist-stop">${esc(tr("暂停"))}</button></div></div>`;
    }
    const j = job;
    if (!j || j.status === "cancelled") return "";
    const when = (t) => (t ? new Date(t).toLocaleString(window.I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "");
    if (j.status === "uploading") {
      return `<div class="notice">${esc(tr("上次的 {file} 只传了 {sent} / {total}：重新选同一个文件会接着传。", { file: j.filename, sent: MB(j.received_bytes), total: MB(j.size_bytes) }))}</div>
        <div class="btns" style="margin-top:0"><button class="btn btn-danger" id="hist-cancel">${esc(tr("放弃这个文件"))}</button></div>`;
    }
    if (j.status === "queued") return `<div class="group" style="padding:14px 16px"><div class="row-title">${esc(tr("已上传，排队中"))}</div><p class="small muted">${esc(tr("一般 1 分钟内开始。可以关掉这一页，导入在服务器上进行。"))}</p></div>`;
    if (j.status === "processing") {
      const pct = j.phase === "parsing" && j.progress != null ? j.progress * 100 : j.phase === "scores" ? 100 : 0;
      return `<div class="group" style="padding:14px 16px"><div class="row-title">${esc(PHASE[j.phase] || tr("导入中"))}${j.phase === "parsing" ? ` ${pct.toFixed(0)}%` : ""}</div>
        <div class="bar" style="margin-top:8px"><span style="width:${pct.toFixed(1)}%"></span></div>
        <p class="small muted">${esc(tr("可以关掉这一页，导入在服务器上进行（一年的数据大约几分钟）。"))}</p></div>`;
    }
    if (j.status === "done") return `<div class="group" style="padding:14px 16px"><div class="row-title">✓ ${esc(j.message || tr("导入完成"))}</div><p class="small muted">${esc(tr("{when}。以后想补新的数据，再导出、上传一次就行（重复的不会重复算）。", { when: when(j.finished_at) }))}</p></div>`;
    if (j.status === "failed") return `<div class="notice">${esc(tr("导入失败：{msg}", { msg: j.message || "" }))}</div>`;
    return "";
  }

  function render(root) {
    const busy = uploading || (job && ["queued", "processing"].includes(job.status));
    root.innerHTML = `${stepsHtml()}<div id="hist-st" style="margin-top:14px">${statusHtml()}</div>
      ${busy ? "" : `<div class="btns"><label class="btn btn-primary" style="text-align:center">${esc(tr("选择文件"))}<input type="file" id="hist-file" accept=".zip,.xml,application/zip,application/xml,text/xml" hidden></label></div>`}`;
    const f = $("#hist-file", root);
    if (f) f.onchange = () => { if (f.files[0]) start(f.files[0], root); };
    const stop = $("#hist-stop", root);
    if (stop) stop.onclick = () => { if (uploading) uploading.cancel = true; };
    const cancel = $("#hist-cancel", root);
    if (cancel) cancel.onclick = async () => {
      try { job = (await C.api(`${BASE}/${job.id}`, { method: "DELETE" })).job; } catch (e) { C.toast(e.message); }
      render(root);
    };
    schedulePoll(root);
  }

  function schedulePoll(root) {
    clearTimeout(poll);
    if (!job || !["queued", "processing"].includes(job.status) || uploading) return;
    poll = setTimeout(async () => {
      if (!root.isConnected || $("#sheet").hidden) return;   // Stop polling once the sheet is closed
      await refreshJob();
      render(root);
      updateItem();
    }, 4000);
  }

  async function putChunk(id, offset, blob) {
    const r = await fetch(`${BASE}/${id}/data?offset=${offset}`, {
      method: "PUT", credentials: "same-origin", body: blob,
      headers: { "X-Body-Web": "1", "Content-Type": "application/octet-stream", Accept: "application/json", ...window.I18N.headers() } });
    let d = null;
    try { d = await r.json(); } catch (e) { /* no body */ }
    if (r.ok) return d.received_bytes;
    const m = /received_bytes=(\d+)/.exec(d?.detail || "");
    if (r.status === 409 && m) return Number(m[1]);   // The server already received part of it: resume from there
    const err = new Error(typeof d?.detail === "string" ? d.detail : tr("上传失败（{status}）", { status: r.status }));
    err.fatal = r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429;
    throw err;
  }

  async function start(file, root) {
    let created;
    try {
      created = await C.api(BASE, { method: "POST", body: { filename: file.name, size: file.size } });
    } catch (e) { C.toast(e.message); return; }
    job = created.job;
    const step = created.chunk_bytes || 1572864;
    uploading = { sent: job.received_bytes, total: file.size, cancel: false };
    render(root); updateItem();
    let off = job.received_bytes, fails = 0;
    try {
      while (off < file.size) {
        if (uploading.cancel) throw Object.assign(new Error(tr("已暂停：重新选同一个文件会接着传")), { paused: true });
        try {
          off = await putChunk(job.id, off, file.slice(off, Math.min(off + step, file.size)));
          fails = 0;
        } catch (e) {
          if (e.fatal || ++fails > 6) throw e;
          await new Promise((res) => setTimeout(res, Math.min(2000 * 2 ** fails, 30000)));   // Network hiccup: wait a bit and retry
          continue;
        }
        uploading.sent = off;
        const st = $("#hist-st", root);
        if (st && root.isConnected) st.innerHTML = statusHtml();
        const stop = $("#hist-stop", root);
        if (stop) stop.onclick = () => { uploading.cancel = true; };
        updateItem();
      }
      job = (await C.api(`${BASE}/${job.id}/complete`, { method: "POST" })).job;
      C.toast(tr("已上传，服务器开始导入"));
    } catch (e) {
      C.toast(e.paused ? e.message : tr("上传没完成：{msg}", { msg: e.message }));
      await refreshJob();
    } finally {
      uploading = null;
      if (root.isConnected) render(root);
      updateItem();
    }
  }

  async function open() {
    C.openSheet(tr("导入健康历史"), `<p class="para" style="margin:0">${esc(tr("读取中…"))}</p>`, async (root) => {
      if (!uploading) await refreshJob();
      render(root);
    });
  }

  function updateItem() {
    const v = document.getElementById("me-history-val");
    if (v) v.textContent = shortStatus(job);
  }

  window.BodyHistory = {
    init(ctx) { C = ctx; refreshJob(); },
    // A row on the Me page (visible only to yourself)
    item(chev) {
      return `<button class="item" id="me-history"><span class="row-title">${esc(tr("导入健康历史"))}</span><span class="val" id="me-history-val">${esc(shortStatus(job))}</span>${chev || ""}</button>`;
    },
    bind(view) {
      const b = $("#me-history", view);
      if (b) b.onclick = open;
      refreshJob().then(updateItem);
    },
    open,
  };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx) => window.BodyHistory.init(ctx), meItem: (chev) => window.BodyHistory.item(chev),
    bindMe: (view) => window.BodyHistory.bind(view),
  });
})();
