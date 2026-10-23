/* Avatars (#200, flag `avatars`): a personal picture and a circle's logo, uploaded from the Me page / the circle settings.
   Separate from app.js: window.BodyAvatar { on(), inner(url, name), small(user, px), editor(opts), profileRow(url, name), bindProfile(root, url, name) }
   + BodyPlugins init. Showing a picture needs no flag (inner / small just draw what the server sent); only the upload / delete
   controls (editor, profileRow) check the flag. Pictures are fetched with the page's cookie (/v1/avatars/users/{id}), a picture
   that fails to load turns back into the initial.
   Picking: the camera (capture) or the photo library (input[type=file]); the picture is cropped to a square in the page
   (drag + zoom), re-drawn on a canvas (this also applies the camera's rotation and drops its metadata) and sent as JPEG; the server
   checks and shrinks it again (images/). Line icons camera / image / trash-2 from Lucide (https://lucide.dev, ISC; THIRD_PARTY_NOTICES.md). */
(() => {
  "use strict";
  let C = null;
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (v) => (C ? C.esc(v) : String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const initial = (name) => (Array.from(String(name || "").trim())[0] || "?").toUpperCase();
  const on = () => !!window.BodyFlags?.on("avatars");

  const SVG = (body) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  const ICON = {
    camera: SVG('<path d="M13.997 4a2 2 0 0 1 1.76 1.05l.486.9A2 2 0 0 0 18.003 7H20a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1.997a2 2 0 0 0 1.759-1.048l.489-.904A2 2 0 0 1 10.004 4z"/><circle cx="12" cy="13" r="3"/>'),
    image: SVG('<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'),
    trash: SVG('<path d="M10 11v6"/><path d="M14 11v6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
  };
  const CSS = `
    .av-img{width:100%;height:100%;object-fit:cover;border-radius:50%;display:block}
    .av-s{flex:none;display:inline-flex;align-items:center;justify-content:center;width:var(--s,20px);height:var(--s,20px);border-radius:50%;overflow:hidden;
      background:var(--surface-2);color:var(--text-2);font-weight:600;font-size:calc(var(--s,20px) * .5);vertical-align:middle;margin-right:6px}
    .av-ed{display:flex;flex-direction:column;align-items:center;gap:14px}
    .av-big{width:112px;height:112px;border-radius:50%;overflow:hidden;background:var(--surface-2);color:var(--text-2);display:flex;align-items:center;justify-content:center;font-size:44px;font-weight:600}
    .av-btns{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;width:100%}
    .av-btns .btn{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;padding-left:14px;padding-right:14px}
    .av-crop{position:relative;width:240px;height:240px;border-radius:50%;overflow:hidden;background:var(--surface-2);touch-action:none;cursor:grab;
      box-shadow:0 0 0 3px var(--surface-2)}
    .av-crop canvas{width:100%;height:100%;display:block}
    .av-zoom{width:240px}
    .av-err{color:var(--bad);font-size:13px;min-height:1em;margin:0;text-align:center}
    .av-row{display:flex;align-items:center;gap:14px}
    .av-row .av-big{width:56px;height:56px;font-size:22px;flex:none}
    .av-row .btn{display:inline-flex;align-items:center;gap:6px}
    .av-file{display:none}`;
  function injectCss() {
    if ($("#avatars-css")) return;
    const st = document.createElement("style");
    st.id = "avatars-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }
  injectCss();

  // The picture (or the initial) to put inside a circle that already has its own size and clipping. A picture that fails
  // to load (removed, not visible to this viewer) is replaced by the initial, see the capture listener below.
  const inner = (url, name) => (url ? `<img class="av-img" src="${esc(url)}" alt="" loading="lazy" decoding="async" data-i="${esc(initial(name))}">` : esc(initial(name)));
  const small = (user, px = 20) => `<span class="av-s" style="--s:${Number(px) || 20}px" aria-hidden="true">${inner(user?.avatar, user?.name || user?.display_name)}</span>`;
  document.addEventListener("error", (e) => {
    const im = e.target;
    if (im && im.tagName === "IMG" && im.classList.contains("av-img")) im.replaceWith(document.createTextNode(im.dataset.i || "?"));
  }, true);

  // ---------------------------------------------------------------- upload
  async function send(method, path, blob) {
    const headers = { Accept: "application/json", "X-Body-Web": "1", ...window.I18N.headers() };
    if (blob) headers["Content-Type"] = blob.type || "image/jpeg";
    const r = await fetch(path, { method, headers, credentials: "same-origin", body: blob || undefined });
    if (r.ok) return r.status === 204 ? null : r.json();
    let msg = tr("上传失败，请稍后再试");
    try { const d = (await r.json()).detail; if (typeof d === "string") msg = d; } catch (e) { /* not JSON */ }
    throw new Error(msg);
  }

  async function decode(file) {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(file, { imageOrientation: "from-image" }); } catch (e) { /* fall back to <img> */ }
    }
    return new Promise((ok, bad) => {
      const url = URL.createObjectURL(file), im = new Image();
      im.onload = () => { URL.revokeObjectURL(url); ok(im); };
      im.onerror = () => { URL.revokeObjectURL(url); bad(new Error("decode")); };
      im.src = url;
    });
  }

  // The crop view: a 240 px circle you drag and zoom; `out()` renders the visible square at OUT px as JPEG
  const VIEW = 240, OUT = 512;
  function cropper(box, img) {
    const w = img.width, h = img.height, cover = VIEW / Math.min(w, h);
    let k = 1, x = 0, y = 0;                       // zoom (1 = just covers the circle) and centre offset in view pixels
    const cv = document.createElement("canvas");
    cv.width = cv.height = VIEW * 2;
    box.appendChild(cv);
    const ctx = cv.getContext("2d");
    const clamp = () => {
      const sw = w * cover * k, sh = h * cover * k;
      x = Math.min(Math.max(x, (VIEW - sw) / 2), (sw - VIEW) / 2);
      y = Math.min(Math.max(y, (VIEW - sh) / 2), (sh - VIEW) / 2);
    };
    const draw = (c, size) => {
      const f = size / VIEW, sw = w * cover * k * f, sh = h * cover * k * f;
      c.fillStyle = "#fff";
      c.fillRect(0, 0, size, size);
      c.drawImage(img, (size - sw) / 2 + x * f, (size - sh) / 2 + y * f, sw, sh);
    };
    const paint = () => { clamp(); draw(ctx, VIEW * 2); };
    let drag = null;
    box.addEventListener("pointerdown", (e) => { drag = { px: e.clientX, py: e.clientY, x, y }; box.setPointerCapture(e.pointerId); box.style.cursor = "grabbing"; });
    box.addEventListener("pointermove", (e) => { if (!drag) return; x = drag.x + e.clientX - drag.px; y = drag.y + e.clientY - drag.py; paint(); });
    const end = () => { drag = null; box.style.cursor = ""; };
    box.addEventListener("pointerup", end);
    box.addEventListener("pointercancel", end);
    paint();
    return {
      zoom(v) { k = v; paint(); },
      out: () => new Promise((ok) => { const o = document.createElement("canvas"); o.width = o.height = OUT; draw(o.getContext("2d"), OUT); o.toBlob(ok, "image/jpeg", 0.9); }),
    };
  }

  /* editor({ title, url, name, put, del, onDone(newUrl | null) }): a sheet with the current picture and three buttons
     (take a photo / choose from the library / remove). After picking a file the same sheet shows the crop. */
  function editor(o) {
    if (!C) return;
    injectCss();
    const show = (root) => {
      root.innerHTML = `<div class="av-ed"><div class="av-big">${inner(o.url, o.name)}</div>
        <div class="av-btns"><button class="btn" type="button" data-av="cam">${ICON.camera}<span>${esc(tr("拍照"))}</span></button>
          <button class="btn" type="button" data-av="lib">${ICON.image}<span>${esc(tr("从相册选"))}</span></button>
          ${o.url ? `<button class="btn btn-danger" type="button" data-av="del">${ICON.trash}<span>${esc(tr("删除图片"))}</span></button>` : ""}</div>
        <input class="av-file" type="file" accept="image/*" capture="user" data-f="cam" aria-label="${esc(tr("拍照"))}">
        <input class="av-file" type="file" accept="image/png,image/jpeg,image/webp,image/heic,image/heif" data-f="lib" aria-label="${esc(tr("从相册选"))}">
        <p class="av-err" role="alert"></p></div>`;
      const err = (m) => { $(".av-err", root).textContent = m || ""; };
      for (const f of root.querySelectorAll("input[type=file]")) f.onchange = async () => {
        const file = f.files[0];
        f.value = "";
        if (!file) return;
        try { crop(root, await decode(file)); } catch (e) { err(tr("读不出这张图片")); }
      };
      $('[data-av="cam"]', root).onclick = () => $('[data-f="cam"]', root).click();
      $('[data-av="lib"]', root).onclick = () => $('[data-f="lib"]', root).click();
      const del = $('[data-av="del"]', root);
      if (del) del.onclick = async () => {
        del.disabled = true;
        try { await send("DELETE", o.del); done(null); } catch (e) { err(e.message); del.disabled = false; }
      };
    };
    const crop = (root, img) => {
      root.innerHTML = `<div class="av-ed"><div class="av-crop" id="av-crop"></div>
        <input class="av-zoom" id="av-zoom" type="range" min="1" max="4" step="0.01" value="1" aria-label="${esc(tr("缩放"))}">
        <div class="av-btns"><button class="btn btn-primary" type="button" id="av-ok">${esc(tr("保存"))}</button><button class="btn" type="button" id="av-back">${esc(tr("重选"))}</button></div>
        <p class="av-err" role="alert"></p></div>`;
      const cr = cropper($("#av-crop", root), img);
      $("#av-zoom", root).oninput = (e) => cr.zoom(Number(e.target.value));
      $("#av-back", root).onclick = () => show(root);
      $("#av-ok", root).onclick = async () => {
        $("#av-ok", root).disabled = true;
        try {
          const blob = await cr.out();
          if (!blob) throw new Error(tr("读不出这张图片"));
          const r = await send("PUT", o.put, blob);
          done(r?.avatar || null);
        } catch (e) { $(".av-err", root).textContent = e.message; $("#av-ok", root).disabled = false; }
      };
    };
    const done = (url) => { C.closeSheet(); C.toast(url ? tr("头像已更新") : tr("头像已删除")); o.onDone?.(url); };
    C.openSheet(o.title, "", show);
  }

  // "My picture" row for the top of the profile sheet (profile.js) and its click handler
  function profileRow(url, name) {
    if (!on()) return "";
    return `<div class="field av-row"><span class="av-big">${inner(url, name)}</span>
      <button class="btn" type="button" id="pf-avatar">${ICON.camera}<span>${esc(url ? tr("更换头像") : tr("设置头像"))}</span></button></div>`;
  }
  function bindProfile(root, url, name) {
    const b = $("#pf-avatar", root);
    if (b) b.onclick = () => editor({ title: tr("头像"), url, name, put: "/v1/avatars/me", del: "/v1/avatars/me", onDone: () => setTimeout(() => location.reload(), 500) });
  }

  window.BodyAvatar = { on, inner, small, editor, profileRow, bindProfile, icons: ICON };
  (window.BodyPlugins = window.BodyPlugins || []).push({ init: (ctx) => { C = ctx; } });
})();
