/* Slim-muscle mode / 薄肌模式 (issue #126, rollout flag slim_mode, migration 900; appearance/).
   Two things, both only for someone the flag is on for:
   - a theme (data-theme="slim" on <html>): a sporty palette (orange accent; chalk-white in light mode, near-black with a volt-green
     "good" colour in dark mode) made only of CSS variables and a handful of accent rules; no layout change. Chosen in 「我 → 外观」,
     saved on the server (PUT /v1/users/me/appearance, also in /v1/web/me), and mirrored in localStorage `be.theme` so the next
     start can apply it before the first page is drawn.
   - a daily quote at the top of the Today page: one post of a real person, word for word, with a link to the post
     (slim-quotes.js, loaded on demand). The quote changes each day, a tap shows the next one, ✕ turns it off.
   Loaded on demand (type w2w/lazy, app.js lazyScript): at start when `be.theme` says slim, or when the flag is on. Registered in
   window.BodyPlugins (init / meGroup / meItem / bindMe) plus window.BodySlim { top, bind, reset } used by app.js.
   Icons: Lucide quote, x, external-link (THIRD_PARTY_NOTICES.md). pick / dayNumber are pure (module.exports for tests/test_slim_mode.py). */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);   // tests/test_slim_mode.py runs this file in node with a stub I18N
  const $ = (s, r = document) => r.querySelector(s);
  const KEY = "be.theme", QKEY = "be.slimq";
  const FLAG = "slim_mode";

  const CSS = `
html[data-theme="slim"] {
  --bg: #fbfaf7; --surface: #f1efe9; --surface-2: #e5e2da; --text: #15140f; --text-2: #625f55; --text-3: #9d998d;
  --hair: rgba(21, 20, 15, 0.09);
  --good: #2f8a2f; --mid: #c27c00; --bad: #d23a2a; --sleep: #5560d0; --train: #d93f0b;
  --good-soft: rgba(47, 138, 47, 0.13); --mid-soft: rgba(194, 124, 0, 0.14); --bad-soft: rgba(210, 58, 42, 0.13);
  --scrim: rgba(21, 20, 15, 0.32); --sheet: #fffefb; --in-sheet: #f1efe9; --chrome: rgba(251, 250, 247, 0.86);
  --accent: #d93f0b; --on-accent: #ffffff;
}
@media (prefers-color-scheme: dark) {
  html[data-theme="slim"] {
    --bg: #0a0a0b; --surface: #151517; --surface-2: #212125; --text: #f6f4ee; --text-2: #9a978c; --text-3: #5d5b53;
    --hair: rgba(255, 255, 255, 0.08);
    --good: #b8f23d; --mid: #ffb224; --bad: #ff5a4d; --sleep: #8f9bff; --train: #ff5a1f;
    --good-soft: rgba(184, 242, 61, 0.14); --mid-soft: rgba(255, 178, 36, 0.15); --bad-soft: rgba(255, 90, 77, 0.15);
    --scrim: rgba(0, 0, 0, 0.62); --sheet: #131315; --in-sheet: #212125; --chrome: rgba(10, 10, 11, 0.82);
    --accent: #ff5a1f; --on-accent: #0a0a0b;
  }
}
html[data-theme="slim"] :is(.btn-primary, .tag.on, .qchip.done, .sev button[aria-pressed="true"], .fd-cbar button[aria-pressed="true"],
  .pk-years button[aria-pressed="true"], .pk-months button[aria-pressed="true"], .pday.sel .n, .cday.today .n, .wday.today .n,
  .login-steps li[aria-current="step"] i) { background: var(--accent); color: var(--on-accent); }
html[data-theme="slim"] .tag.on, html[data-theme="slim"] .sev button[aria-pressed="true"] { border-color: var(--accent); }
html[data-theme="slim"] .tab[aria-current="page"] { color: var(--accent); }
.slimq { display: flex; align-items: flex-start; gap: 10px; margin: 0 0 12px; padding: 12px 8px 12px 14px; border-radius: var(--radius); background: var(--surface); border-left: 3px solid var(--accent, var(--text)); }
.slimq[hidden] { display: none; }
.slimq-body { flex: 1; min-width: 0; }
.slimq-t { display: block; width: 100%; border: 0; background: none; padding: 0; text-align: left; color: var(--text); font: inherit; cursor: pointer; }
.slimq-txt { display: block; font-size: 15px; font-weight: 600; line-height: 1.5; white-space: pre-line; overflow-wrap: anywhere; transition: opacity .18s; }
.slimq-txt.fade { opacity: 0; }
.slimq-by { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; color: var(--text-2); }
.slimq-by a { display: inline-flex; color: var(--text-2); }
.slimq svg { width: 15px; height: 15px; flex-shrink: 0; }
.slimq-q { margin-top: 3px; color: var(--accent, var(--text-2)); }
.slimq-x { border: 0; background: none; color: var(--text-3); padding: 0; min-width: 32px; min-height: 32px; display: inline-flex; align-items: center; justify-content: center; }
.slimq-x svg { width: 16px; height: 16px; }
@media (prefers-reduced-motion: reduce) { .slimq-txt { transition: none; } }
`;

  // Lucide (https://lucide.dev, ISC): only the paths used; notices in THIRD_PARTY_NOTICES.md
  const ICON = {
    quote: '<path d="M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/><path d="M5 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    link: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  };
  const svg = (k, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[k]}</svg>`;

  // ---- pure helpers
  /** Whole days since 1970-01-01 of an ISO date (the user's own date, so the quote changes at their midnight). */
  function dayNumber(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 86400000) : 0;
  }
  const hash = (s) => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h; };
  /** The verified items in a fixed, mixed-up order (so the same person does not come three days in a row). */
  function ordered(items) {
    return items.filter((q) => q.verified === true && q.text && q.source_url).sort((a, b) => hash(a.id) - hash(b.id) || (a.id < b.id ? -1 : 1));
  }
  /** The quote for a day out of an ordered list, `shift` taps further on. */
  const at = (list, iso, shift = 0) => (list.length ? list[(((dayNumber(iso) + shift) % list.length) + list.length) % list.length] : null);
  const pick = (items, iso, shift = 0) => at(ordered(items), iso, shift);

  if (typeof module !== "undefined") module.exports = { dayNumber, ordered, pick };
  if (typeof window === "undefined") return;

  // ---- state
  let C = null, A = null, TODAY = "", ON = false, quotes = null, loading = null;
  const store = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private mode */ } };
  const stored = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };

  function injectCss() {
    if ($("#slim-css")) return;
    const st = document.createElement("style");
    st.id = "slim-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }
  // The browser's bar colour follows the theme (the page's <meta name="theme-color"> has one value per colour scheme)
  const BAR = { light: "#fbfaf7", dark: "#0a0a0b" };
  function paintBar(slim) {
    for (const m of document.querySelectorAll('meta[name="theme-color"]')) {
      if (!m.dataset.was) m.dataset.was = m.getAttribute("content");
      const dark = /dark/.test(m.getAttribute("media") || "");
      m.setAttribute("content", slim ? (dark ? BAR.dark : BAR.light) : m.dataset.was);
    }
  }
  function apply(theme) {
    const slim = theme === "slim";
    if (slim) injectCss();
    if (slim) document.documentElement.dataset.theme = "slim"; else delete document.documentElement.dataset.theme;
    paintBar(slim);
  }
  // The theme is shown only while the flag is on (a kill switch), and only for the signed-in user
  const wanted = () => ON && A?.theme === "slim";
  const quoteOn = () => wanted() && A?.quotes !== false;
  function settle() {
    apply(wanted() ? "slim" : "default");
    store(KEY, wanted() ? "slim" : null);
  }
  // Applied at once when the file loads, from what the last start saved (before init knows the account)
  if (stored(KEY) === "slim") apply("slim");

  // ---- the quote
  function loadQuotes() {
    if (quotes) return Promise.resolve(quotes);
    loading ??= window.BodyLazy.script("slim-quotes.js").then(() => {
      quotes = ordered(window.BodySlimQuotes?.items || []);
      if (!quotes.length) loading = null;
      return quotes;
    });
    return loading;
  }
  const shift = () => { try { const o = JSON.parse(stored(QKEY) || "null"); return o && o.day === TODAY ? o.n | 0 : 0; } catch (e) { return 0; } };
  const quoteHtml = (q) => `<span class="slimq-q">${svg("quote")}</span>
    <div class="slimq-body"><button type="button" class="slimq-t" id="slimq-next" aria-label="${C.esc(tr("换一句"))}"><span class="slimq-txt" id="slimq-txt" lang="${q.lang === "en" ? "en" : "zh-CN"}">${C.esc(q.text)}</span></button>
      <div class="slimq-by"><span>${C.esc(q.person)}</span><a href="${C.esc(q.source_url)}" target="_blank" rel="noopener noreferrer" aria-label="${C.esc(tr("原帖"))}">${svg("link")}</a></div></div>
    <button type="button" class="slimq-x" id="slimq-x" aria-label="${C.esc(tr("关闭语录"))}">${svg("x")}</button>`;

  async function fill(box) {
    const list = await loadQuotes().catch(() => null);
    if (!list?.length || !box.isConnected) return;
    let n = shift();
    const draw = () => {
      box.innerHTML = quoteHtml(at(list, TODAY, n));
      box.hidden = false;
      $("#slimq-next", box).onclick = () => {
        n += 1;
        store(QKEY, JSON.stringify({ day: TODAY, n }));
        $("#slimq-txt", box).classList.add("fade");
        setTimeout(() => { if (box.isConnected) draw(); }, 180);
      };
      $("#slimq-x", box).onclick = () => setQuotes(false, box);
    };
    draw();
  }

  async function setQuotes(on, box) {
    A = { ...A, quotes: on };
    if (box) { box.hidden = true; box.innerHTML = ""; }
    try { await C.api("/v1/users/me/appearance", { method: "PUT", body: { quotes: on } }); }
    catch (e) { A = { ...A, quotes: !on }; C.toast(e.message); }
  }

  // ---- 「我 → 外观」
  const label = () => (wanted() ? tr("薄肌") : tr("默认"));
  function sheet() {
    const seg = (name, opts) => `<div class="seg" role="group">${opts.map(([v, t, on]) => `<button type="button" data-slim-${name}="${v}" aria-pressed="${on}">${t}</button>`).join("")}</div>`;
    const html = () => `<div class="group">
      <div class="item lang-row"><span class="row-title">${tr("主题")}</span>${seg("theme", [["default", tr("默认"), !wanted()], ["slim", tr("薄肌"), wanted()]])}</div>
      <div class="item lang-row"><span class="row-title">${tr("每日语录")}</span>${seg("quotes", [["on", tr("开"), A?.quotes !== false], ["off", tr("关"), A?.quotes === false]])}</div></div>`;
    C.openSheet(tr("外观"), html(), (root) => {
      const redraw = () => { root.innerHTML = html(); bind(root); const v = $("#me-appearance-val"); if (v) v.textContent = label(); };
      const bind = (r) => {
        for (const b of r.querySelectorAll("[data-slim-theme]")) b.onclick = async () => {
          const was = A;
          A = { ...A, theme: b.dataset.slimTheme };
          settle(); redraw();
          try { await C.api("/v1/users/me/appearance", { method: "PUT", body: { theme: A.theme } }); }
          catch (e) { A = was; settle(); redraw(); C.toast(e.message); }
        };
        for (const b of r.querySelectorAll("[data-slim-quotes]")) b.onclick = async () => {
          await setQuotes(b.dataset.slimQuotes === "on");
          redraw();
        };
      };
      bind(root);
    });
  }

  window.BodySlim = {
    // app.js puts this above the Today page's cards; the quote is filled in afterwards by bind()
    top: () => (quoteOn() ? '<div class="slimq" id="slimq" hidden></div>' : ""),
    bind: (view) => { const box = $("#slimq", view); if (box && quoteOn()) fill(box); },
    reset: () => { A = null; ON = false; quotes = null; store(KEY, null); store(QKEY, null); apply("default"); },
  };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meGroup: "settings-account",
    init: (ctx, me) => {
      C = ctx;
      ON = !!me?.flags?.includes(FLAG);
      A = ON ? me.appearance || { theme: "default", quotes: true } : null;
      TODAY = me?.today || "";
      settle();
    },
    meItem: (chev) => (ON ? `<button class="item" id="me-appearance"><span class="row-title">${C.esc(tr("外观"))}</span><span class="val" id="me-appearance-val">${C.esc(label())}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => { const b = $("#me-appearance", view); if (b) b.onclick = sheet; },
  });
})();
