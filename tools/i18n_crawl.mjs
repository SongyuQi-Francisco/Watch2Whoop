#!/usr/bin/env node
// Finds Chinese left on the Today page when it is in English (issue #64), by looking at the real page: headless Chrome signs in
// to a LOCAL server (the Quick Start demo: synthetic data only, never a real account), opens every tab, clicks what it finds
// (two levels of sheets) and lists
//   - the Chinese text that is on the screen (text nodes, placeholder / aria-label / title / alt), and
//   - the keys tr() was asked for that i18n-en.js has no English for (I18N.missing()).
//
//   node tools/i18n_crawl.mjs --base http://localhost:18100 --code <login code> [--tabs today,trends,friends,food,record,me] [--lang en]
//
// What the static tests (body_engine/tests/test_i18n*.py) cannot see: a text that is only worked out at run time, e.g. a plan
// name that comes from the server and goes through tr(), or one of the short reasons in app.js (SHORT). Run it on a demo user of
// every kind you have (new user with no data; a user with data; owner with every rollout flag on) before a UI release.
// The crawl clicks buttons, so use a throw-away database. It skips anything that looks like delete / sign out / reset / revoke.
// Language names ("语言 / Language", 中文) are meant to stay Chinese and are left out. Needs Node >= 22 and Google Chrome.
import { arg, launch, sleep } from "./cdp.mjs";

const BASE = arg("base", "http://localhost:18100");
const CODE = arg("code");
const LANG = arg("lang", "en");
const TABS = arg("tabs", "today,trends,friends,food,record,me").split(",");
if (!CODE) { console.error("need --code (body-engine login-code --user-id N)"); process.exit(2); }

const PAGE = `
window.__hits = {};
window.__cjk = () => {
  const re = /[\\u3000-\\u303f\\u4e00-\\u9fff\\uff00-\\uffef]/, out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) {
    const t = n.nodeValue.trim();
    if (t && re.test(t) && !["SCRIPT", "STYLE"].includes(n.parentElement?.tagName)) out.push("text: " + t.slice(0, 90));
  }
  document.querySelectorAll("[placeholder],[aria-label],[title],[alt]").forEach((e) => {
    for (const a of ["placeholder", "aria-label", "title", "alt"]) { const v = e.getAttribute(a); if (v && re.test(v)) out.push(a + ": " + v.slice(0, 80)); }
  });
  return [...new Set(out)];
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const BAD = /delete|remove|sign out|log out|logout|clear|reset|revoke|disconnect|withdraw|删除|退出|撤销|重置/i;
window.__crawl = async (tab, max = 40) => {
  const tb = () => document.querySelector('[data-tab="' + tab + '"]');
  if (!tb()) return;
  tb().click(); await sleep(1200);
  const record = (ctx) => { for (const h of window.__cjk()) { if (/语言 \\/ Language|Language \\/ 语言|^text: 中文$/.test(h)) continue; (window.__hits[h] = window.__hits[h] || new Set()).add(tab + " > " + ctx); } };
  record("(tab)");
  const pick = () => [...document.querySelectorAll("#view button, #view [role=button], #view .item, #view a[href], main button, main .item, #app button")]
    .filter((e) => e.offsetParent && !e.closest("nav,.tabbar") && !e.closest("#sheet") && !BAD.test(e.textContent + e.getAttribute("aria-label")) && !(e.id || "").startsWith("sheet"));
  const n = pick().length;
  for (let i = 0; i < Math.min(n, max); i++) {
    tb().click(); await sleep(500);
    const el = pick()[i]; if (!el) break;
    const label = (el.textContent.trim().slice(0, 30) || el.getAttribute("aria-label") || el.id || el.className).toString().replace(/\\s+/g, " ");
    try { el.click(); } catch { /* gone */ }
    await sleep(700);
    record("click: " + label);
    if (document.querySelector("#sheet:not([hidden])")) {
      const sel = "#sheet:not([hidden]) button, #sheet:not([hidden]) .item";
      const ok = (e) => e.offsetParent && !BAD.test(e.textContent + e.getAttribute("aria-label")) && e.id !== "sheet-close" && e.id !== "sheet-back";
      const m = [...document.querySelectorAll(sel)].filter(ok).length;
      for (let j = 0; j < Math.min(m, 8); j++) {
        const cur = [...document.querySelectorAll(sel)].filter(ok)[j]; if (!cur) break;
        const l2 = cur.textContent.trim().slice(0, 25).replace(/\\s+/g, " ");
        try { cur.click(); } catch { /* gone */ }
        await sleep(600); record("click: " + label + " > " + l2);
        document.querySelector("#sheet-back:not([hidden])")?.click(); await sleep(300);
      }
    }
    document.querySelector("#sheet-close")?.click(); await sleep(250);
    document.querySelector(".sub-back")?.click(); await sleep(250);
  }
};
window.__report = () => ({ onScreen: Object.fromEntries(Object.entries(window.__hits).map(([k, v]) => [k, [...v].slice(0, 3)])), missingKeys: window.I18N.missing() });
`;

const c = await launch({ lang: LANG === "en" ? "en-US" : "zh-CN" });
try {
  await c.goto(`${BASE}/app/#login=${CODE}&lang=${LANG}`, 4000);
  await c.eval(PAGE);
  await c.eval(`document.querySelector("#sheet-close")?.click(); 1`);
  for (const t of TABS) {
    await c.eval(`window.__crawl(${JSON.stringify(t)})`);
    console.error("crawled", t);
  }
  const r = await c.eval("window.__report()");
  console.log(JSON.stringify(r, null, 2));
  const n = Object.keys(r.onScreen).length + r.missingKeys.length;
  console.error(n ? `${n} finding(s)` : "nothing left in Chinese");
  process.exitCode = n ? 1 : 0;
} finally { c.close(); }
