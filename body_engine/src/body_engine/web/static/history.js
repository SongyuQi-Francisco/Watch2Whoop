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
  const MB = (n) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
  const BASE = "/v1/users/me/history-import";
  const PHASE = { parsing: "解析中", saving: "写入中", scores: "重算评分" };

  function shortStatus(j) {
    if (uploading) return `上传中 ${Math.floor((uploading.sent / uploading.total) * 100)}%`;
    if (!j) return "未导入";
    if (j.status === "uploading") return "没传完";
    if (j.status === "queued") return "排队中";
    if (j.status === "processing") return j.phase === "parsing" && j.progress != null ? `导入中 ${Math.round(j.progress * 100)}%` : "导入中";
    if (j.status === "done") return j.result?.last_day ? `已导入到 ${j.result.last_day.slice(5).replace("-", "/")}` : "已导入";
    if (j.status === "failed") return "失败";
    return "未导入";
  }

  async function refreshJob() {
    try { job = (await C.api(BASE)).job; } catch (e) { /* older servers don't have this endpoint */ }
    return job;
  }

  function stepsHtml() {
    return `<p class="para" style="margin:0">app 只同步最近 90 天。更早的历史（戴手表以来的心率、睡眠、训练）可以一次性导入，导入后趋势和评分会补上。</p>
      <ol class="para" style="padding-left:20px;white-space:normal">
        <li>打开 iPhone 的「健康」app → 右上角头像 → 拉到最下面「导出所有健康数据」→「导出」，等几分钟。</li>
        <li>弹出分享菜单后选「存储到“文件”」→「我的 iPhone」→「存储」。</li>
        <li>回到这里点「选择文件」，选刚才的 <b>export.zip</b>（中文系统叫 <b>导出.zip</b>）。</li>
      </ol>
      <p class="small muted">上传时别锁屏、别切走（文件一般 20–100MB）；断了重新选同一个文件会接着传。文件只用来导入，导入完立刻从服务器删除。</p>`;
  }

  function statusHtml() {
    if (uploading) {
      const pct = (uploading.sent / uploading.total) * 100;
      return `<div class="group" style="padding:14px 16px"><div class="row-title">上传中 ${MB(uploading.sent)} / ${MB(uploading.total)}</div>
        <div class="bar" style="margin-top:8px"><span style="width:${pct.toFixed(1)}%"></span></div>
        <div class="btns"><button class="btn" id="hist-stop">暂停</button></div></div>`;
    }
    const j = job;
    if (!j || j.status === "cancelled") return "";
    const when = (t) => (t ? new Date(t).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "");
    if (j.status === "uploading") {
      return `<div class="notice">上次的 ${esc(j.filename)} 只传了 ${MB(j.received_bytes)} / ${MB(j.size_bytes)}：重新选同一个文件会接着传。</div>
        <div class="btns" style="margin-top:0"><button class="btn btn-danger" id="hist-cancel">放弃这个文件</button></div>`;
    }
    if (j.status === "queued") return `<div class="group" style="padding:14px 16px"><div class="row-title">已上传，排队中</div><p class="small muted">一般 1 分钟内开始。可以关掉这一页，导入在服务器上进行。</p></div>`;
    if (j.status === "processing") {
      const pct = j.phase === "parsing" && j.progress != null ? j.progress * 100 : j.phase === "scores" ? 100 : 0;
      return `<div class="group" style="padding:14px 16px"><div class="row-title">${PHASE[j.phase] || "导入中"}${j.phase === "parsing" ? ` ${pct.toFixed(0)}%` : ""}</div>
        <div class="bar" style="margin-top:8px"><span style="width:${pct.toFixed(1)}%"></span></div>
        <p class="small muted">可以关掉这一页，导入在服务器上进行（一年的数据大约几分钟）。</p></div>`;
    }
    if (j.status === "done") return `<div class="group" style="padding:14px 16px"><div class="row-title">✓ ${esc(j.message || "导入完成")}</div><p class="small muted">${when(j.finished_at)}。以后想补新的数据，再导出、上传一次就行（重复的不会重复算）。</p></div>`;
    if (j.status === "failed") return `<div class="notice">导入失败：${esc(j.message || "")}</div>`;
    return "";
  }

  function render(root) {
    const busy = uploading || (job && ["queued", "processing"].includes(job.status));
    root.innerHTML = `${stepsHtml()}<div id="hist-st" style="margin-top:14px">${statusHtml()}</div>
      ${busy ? "" : `<div class="btns"><label class="btn btn-primary" style="text-align:center">选择文件<input type="file" id="hist-file" accept=".zip,.xml,application/zip,application/xml,text/xml" hidden></label></div>`}`;
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
      headers: { "X-Body-Web": "1", "Content-Type": "application/octet-stream", Accept: "application/json" } });
    let d = null;
    try { d = await r.json(); } catch (e) { /* no body */ }
    if (r.ok) return d.received_bytes;
    const m = /received_bytes=(\d+)/.exec(d?.detail || "");
    if (r.status === 409 && m) return Number(m[1]);   // The server already received part of it: resume from there
    const err = new Error(typeof d?.detail === "string" ? d.detail : `上传失败（${r.status}）`);
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
        if (uploading.cancel) throw Object.assign(new Error("已暂停：重新选同一个文件会接着传"), { paused: true });
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
      C.toast("已上传，服务器开始导入");
    } catch (e) {
      C.toast(e.paused ? e.message : `上传没完成：${e.message}`);
      await refreshJob();
    } finally {
      uploading = null;
      if (root.isConnected) render(root);
      updateItem();
    }
  }

  async function open() {
    C.openSheet("导入健康历史", `<p class="para" style="margin:0">读取中…</p>`, async (root) => {
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
      return `<button class="item" id="me-history"><span class="row-title">导入健康历史</span><span class="val" id="me-history-val">${esc(shortStatus(job))}</span>${chev || ""}</button>`;
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
