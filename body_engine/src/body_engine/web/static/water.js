/* Water card on 饮食 (issue #125, water/): 「今天喝水 x / 目标 y ml」 with a progress bar, +250 / +500 buttons and undo of the
   newest drink. Separate from app.js: registered in window.BodyPlugins with init(ctx), foodDiet(info) -> placeholder HTML
   (rendered right after 「今天吃了」) and bindFoodDiet(view), which loads GET /v1/users/{u}/water?date= and draws the card.
   Buttons only for one's own account on today's date; a past day or a family member's day is read-only. The card stays
   out while the "water" rollout flag is off for the signed-in user (BodyFlags.on; the endpoints answer 404 then too) and
   hidden when the request fails. Each button press carries a client_id, so a retry or
   a double tap is stored once. With offline_mode (offline.js) a press while offline is kept on the device with its time
   (`at`) and sent later; the card counts the kept drinks in at once. */
(() => {
  "use strict";
  let C = null;
  let info = null;
  let busy = false;
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const num = (v) => window.I18N.num(v ?? 0, 0);
  const path = () => `/v1/users/${info.owner}/water`;
  const clientId = () => (window.crypto?.randomUUID ? window.crypto.randomUUID() : `w${Date.now()}${Math.random().toString(36).slice(2, 10)}`);
  const EXTRA = { training: () => tr("训练日"), sauna: () => tr("桑拿") };
  const offlineOn = () => !!window.BodyFlags?.on("offline_mode");
  const pad = (n) => String(n).padStart(2, "0");
  const localAt = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  // Drinks kept offline for this day, not on the server yet
  const keptMl = (date) => (offlineOn() && info.self ? window.BodyOffline?.pending("water") || [] : [])
    .filter((it) => String(it.body?.at || "").startsWith(date)).reduce((n, it) => n + (it.body.ml || 0), 0);
  const withKept = (d) => {
    const ml = keptMl(info.date);
    if (!ml) return d;
    const total = d.total_ml + ml;
    return { ...d, total_ml: total, percent: d.goal?.ml ? Math.round((100 * total) / d.goal.ml) : d.percent, undo: null };
  };
  let server = null;   // the day as the server last sent it (kept drinks are added on top when drawing)

  const CSS = `
    .water .w-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
    .water .w-v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
    .water .w-v small{font-size:13px;font-weight:500;color:var(--text-2)}
    .water .bar{height:6px;border-radius:3px;margin:10px 0 2px}
    .water .bar>span{background:#4ea8ff;border-radius:3px}
    .water .w-sub{font-size:12px;color:var(--text-3);margin-top:6px}
    .water .w-btns{display:flex;gap:8px;margin-top:12px}
    .water .w-btns .btn{flex:1;margin:0;min-height:40px;padding:6px 10px;font-weight:600;border-radius:999px;background:var(--bg);color:var(--text)}
    .water .w-btns .w-undo{flex:none;font-weight:500;color:var(--text-2);background:none}`;

  function injectCss() {
    if ($("#water-css")) return;
    const st = document.createElement("style");
    st.id = "water-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function foodDiet(i) {
    info = i;
    if (!window.BodyFlags?.on("water")) return "";   // rollout flag (docs/rollout.md)
    return `<div class="section" id="water-box" hidden></div>`;
  }

  function draw(box, d) {
    const g = d.goal || {}, pct = g.ml ? Math.min(100, (100 * d.total_ml) / g.ml) : 0;
    const canWrite = info.self && !info.past;
    const extras = (g.extras || []).map((e) => `${EXTRA[e.kind]?.() || e.kind} +${num(e.ml)}`);
    const sub = [g.source === "profile" ? tr("目标来自饮食档案") : tr("通用建议值"), ...extras].join(" · ");
    box.innerHTML = `<div class="section-head"><h2>${info.past ? tr("喝水") : tr("今天喝水")}</h2>
        <span class="small muted">${d.percent != null ? `${num(Math.min(d.percent, 999))}%` : ""}</span></div>
      <div class="card water">
        <div class="w-top"><span class="w-v">${num(d.total_ml)}<small> / ${num(g.ml)} ml</small></span></div>
        <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="${g.ml || 0}" aria-valuenow="${d.total_ml}"
          aria-label="${C.esc(tr("今天喝水"))}"><span style="width:${pct.toFixed(0)}%"></span></div>
        <div class="w-sub">${C.esc(sub)}</div>
        ${canWrite ? `<div class="w-btns">${(d.quick_ml || [250, 500]).map((ml) =>
          `<button type="button" class="btn" data-water-add="${ml}">+${num(ml)} ml</button>`).join("")}
          ${d.undo ? `<button type="button" class="btn w-undo" data-online data-water-undo="${d.undo.id}"
            aria-label="${C.esc(tr("撤销最近一次（{ml} ml）", { ml: num(d.undo.ml) }))}">${C.esc(tr("撤销"))}</button>` : ""}</div>` : ""}
      </div>`;
    box.hidden = false;
    for (const b of box.querySelectorAll("[data-water-add]")) {
      b.onclick = () => write(box, "POST", path(), { ml: +b.dataset.waterAdd, source: "button", client_id: clientId(), ...(offlineOn() ? { at: localAt() } : {}) });
    }
    const u = $("[data-water-undo]", box);
    if (u) u.onclick = () => write(box, "DELETE", `${path()}/${u.dataset.waterUndo}`);
  }

  async function write(box, method, url, body) {
    if (busy) return;
    busy = true;
    for (const b of box.querySelectorAll("button")) b.disabled = true;
    try {
      const queue = method === "POST" ? { kind: "water", label: tr("喝水 {ml} ml", { ml: num(body.ml) }) } : undefined;
      const r = await C.api(url, body ? { method, body, queue } : { method });
      if (r.queued) {   // kept offline (offline.js): count it in now, it is sent once the network is back
        draw(box, withKept(server));
        C.toast?.(tr("离线，已存在本机，联网后自动提交"));
        return;
      }
      server = r.day;
      draw(box, withKept(server));
      if (method === "DELETE") C.toast?.(tr("已撤销"));
    } catch (e) {
      C.toast?.(tr("没记上，再试一次"));
      for (const b of box.querySelectorAll("button")) b.disabled = false;
    } finally { busy = false; }
  }

  async function bindFoodDiet(view) {
    const box = $("#water-box", view);
    if (!box || !info) return;
    injectCss();
    const want = `${info.owner}|${info.date}`;
    let d = null;
    try { d = await C.api(`${path()}?date=${info.date}`); } catch (e) { return; }   // feature_off / no share: stay hidden
    if (!box.isConnected || `${info.owner}|${info.date}` !== want) return;
    server = d;
    draw(box, withKept(server));
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx) => { C = ctx; }, foodDiet, bindFoodDiet,
  });
})();
