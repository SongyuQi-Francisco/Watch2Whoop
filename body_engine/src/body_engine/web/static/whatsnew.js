/* 「本周更新」 (body_engine/whatsnew/): a sheet listing what shipped, grouped by week, newest first; an entry with a link jumps there.
   Opened from 我 → 设置 → 更新记录 (app.js) and from the Sunday-evening message (its url /app/?whatsnew=1: notify.js announces the
   "body:link" and calls window.BodyLazy.script("whatsnew.js") then BodyWhatsNew.open()). GET /v1/whats-new?weeks= -> {weeks: [{week_start, items: [{date, text, url}]}]},
   texts already in the page language. On demand (lazyScript, first-screen budget): registers in window.BodyPlugins (init) and window.BodyWhatsNew.open.
   Icons: Lucide sparkles and chevron-right (ISC). */
(() => {
  "use strict";
  let C = null;
  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (v) => C.esc(v);
  const day = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const svg = (paths, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const SPARKLES = svg('<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>');
  const CHEV = svg('<path d="m9 18 6-6-6-6"/>');

  function css() {
    if (document.getElementById("wn-css")) return;
    const s = document.createElement("style");
    s.id = "wn-css";
    s.textContent = ".wn-week{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--text-2);margin:18px 0 4px}.wn-week:first-child{margin-top:0}"
      + ".wn-row{display:flex;align-items:baseline;gap:10px;width:100%;padding:10px 0;border:0;border-top:1px solid var(--hair);background:none;text-align:left;color:inherit;font:inherit}"
      + ".wn-week + .wn-row{border-top:0}.wn-row .d{flex:none;width:48px;font-size:13px;color:var(--text-3);font-variant-numeric:tabular-nums}"
      + ".wn-row .t{flex:1;min-width:0}.wn-row .c{flex:none;color:var(--text-3);align-self:center;display:inline-flex}.wn-empty{display:flex;gap:8px;align-items:center;color:var(--text-2)}";
    document.head.appendChild(s);
  }

  /** "This week" / "Last week" / "Oct 26 on" for the week starting on `iso` (a Monday), counted from the user's today. */
  function weekLabel(iso, today) {
    const n = Math.round((day(today) - day(iso)) / (7 * 86400000));
    if (n <= 0) return tr("本周");
    if (n === 1) return tr("上周");
    return tr("{d} 起的一周", { d: window.I18N.monthDay(day(iso)) });
  }

  function html(weeks) {
    const today = C.today?.() || new Date().toISOString().slice(0, 10);
    if (!weeks.length) return `<p class="wn-empty">${SPARKLES}<span>${esc(tr("暂无"))}</span></p>`;
    return weeks.map((w, i) => `<div class="wn-week">${i === 0 ? SPARKLES : ""}<span>${esc(weekLabel(w.week_start, today))}</span></div>`
      + w.items.map((it) => {
        const inner = `<span class="d">${esc(window.I18N.monthDay(day(it.date)))}</span><span class="t">${esc(it.text)}</span>${it.url ? `<span class="c">${CHEV}</span>` : ""}`;
        return it.url ? `<button type="button" class="wn-row" data-wn="${esc(it.url)}">${inner}</button>` : `<div class="wn-row">${inner}</div>`;
      }).join("")).join("");
  }

  /** A link inside this page ("/app/?tab=me"): switch to its tab and announce it like notify.js does. */
  function go(url) {
    const p = Object.fromEntries(new URLSearchParams(String(url).split("#")[0].split("?")[1] || ""));
    C.closeSheet();
    if (p.tab) C.setTab?.(p.tab);
    window.dispatchEvent(new CustomEvent("body:link", { detail: p }));
  }

  function open() {
    css();
    C.openSheet(tr("本周更新"), `<p class="para" style="margin:0">${esc(tr("读取中…"))}</p>`, async (root) => {
      try {
        root.innerHTML = html((await C.api("/v1/whats-new")).weeks || []);
        for (const b of root.querySelectorAll("[data-wn]")) b.onclick = () => go(b.dataset.wn);
      } catch (e) { root.innerHTML = `<p class="empty">${esc(tr("读取失败"))}</p>`; }
    });
  }

  if (typeof module !== "undefined") module.exports = { weekLabel, html };
  if (typeof window === "undefined") return;
  window.BodyWhatsNew = { open };
  (window.BodyPlugins = window.BodyPlugins || []).push({ init: (ctx) => { C = ctx; } });
})();
