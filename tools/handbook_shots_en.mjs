#!/usr/bin/env node
// English screenshots for docs/tester-handbook.en.md (issue #13): the Today page in English, captured with headless Chrome
// from a local Quick Start server that holds only synthetic data (./quickstart.sh, never a real person's account).
//
//   node tools/handbook_shots_en.mjs --base http://localhost:18100 --code <login code> --code-new <login code> [--code-ai <login code>]
//   python3 tools/handbook_images.py --lang en        # annotates them -> docs/images/tester-handbook/en/*.jpg
//
// --code      a demo user with 60 days of synthetic data (the Today page)
// --code-ai   a demo user with no AI set up yet (Me / AI settings screens); default: the same as --code
// --code-new  a demo user who has not agreed to the consent form yet (the first-run screens)
// Login codes: `body-engine login-code --user-id N` (one use each). Needs Node >= 22 (global WebSocket) and Google Chrome.
// Writes docs/images/tester-handbook/en/src/<name>.jpg and en/spec.json: the boxes to draw (iPhone points = CSS px) found from
// the page itself, so the red frames follow the layout. The 402 x 874 viewport is the iPhone 17 Pro in points. The status bar
// is not part of a web capture; the page content starts where it does on the phone (safe area 59 pt).
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { arg, launch as launchChrome, sleep } from "./cdp.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = arg("base", "http://localhost:18100");
const ONLY = arg("only") ? arg("only").split(",") : null;   // e.g. --only consent,profile
const want = (...names) => !ONLY || names.some((n) => ONLY.includes(n));
const CODE = arg("code"), CODE_NEW = arg("code-new"), CODE_AI = arg("code-ai", CODE);
const OUT = resolve(arg("out", join(ROOT, "docs/images/tester-handbook/en/src")));
if (!CODE || !CODE_NEW) { console.error("need --code and --code-new (see the header)"); process.exit(2); }
mkdirSync(OUT, { recursive: true });
const W = 402, H = 874;   // iPhone 17 Pro in points

// a Chrome whose screenshots go to OUT
async function launch() {
  const c = await launchChrome({ width: W, height: H });
  return { ...c, shot: (name) => c.shot(join(OUT, `${name}.jpg`)) };
}

// In-page helpers: find an element by its visible text, click it, and measure boxes in CSS px
const HELPERS = `
  window.$t = (sel, text, root = document) => [...root.querySelectorAll(sel)].find((e) => e.offsetParent !== null && e.textContent.replace(/\\s+/g, " ").trim().includes(text));
  window.$wait = async (fn, ms = 6000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = fn(); if (v) return v; await new Promise((r) => setTimeout(r, 100)); } throw new Error("timeout: " + fn); };
  window.$box = (el, pad = 0) => { const r = el.getBoundingClientRect(); return [r.left - pad, r.top - pad, r.right + pad, r.bottom + pad].map((v) => Math.round(v)); };
  window.$click = async (sel, text) => { const el = await $wait(() => $t(sel, text)); el.click(); await new Promise((r) => setTimeout(r, 700)); return el; };
  window.$tab = async (t) => { document.querySelector('[data-tab="' + t + '"]').click(); await new Promise((r) => setTimeout(r, 1500)); };
  window.$closeSheet = async () => { document.querySelector('#sheet-close')?.click(); await new Promise((r) => setTimeout(r, 500)); };
`;
const spec = {};
const note = (name, boxes) => { spec[name] = boxes; };

async function session(code, fn) {
  const c = await launch();
  try {
    await c.goto(`${BASE}/app/#login=${code}&lang=en`, 4000);
    await c.eval(HELPERS);
    await fn(c);
  } finally { c.close(); }
}

// ---- 1. the activation page (a plain web page, 390 wide)
if (want("activate")) {
  const c = await launch();
  try {
    await c.metrics(390, 460);
    await c.goto(`${BASE}/app/activate.html?lang=en#lang=en`, 2500);
    await c.shot("activate");
    note("activate", []);
  } finally { c.close(); }
}

// ---- 2. first run: "Get started" (one-tap consent), then the profile sheet (a taller screen: the sheet is long)
if (want("consent", "profile")) await session(CODE_NEW, async (c) => {
  await c.eval(`$wait(() => $t("button", "Get started"))`);
  await c.shot("consent");
  note("consent", [await c.eval(`$box($t("button", "Get started"), 4)`)]);
  await c.eval(`$t("button", "Get started").click(); 1`);
  await c.eval(`$wait(() => $t("#sheet button", "Save") && document.querySelector("#sheet:not([hidden])"))`);
  await c.metrics(W, 1040);
  await sleep(3500);   // the "Thank you" toast goes away
  // headless Chrome words an empty date field in the computer's language (年/月/日): show a synthetic birthday instead
  await c.eval(`(() => { const d = document.querySelector("#sheet input[type=date]"); if (d) { d.value = "1990-01-01"; d.dispatchEvent(new Event("input", { bubbles: true })); } })()`);
  await c.shot("profile");
  note("profile", [await c.eval(`$box($t("#sheet button", "Save"), 4)`)]);
});

// ---- 3. Today, then Me -> AI settings and its sheets (a user with no AI set up)
if (want("today")) await session(CODE, async (c) => {
  await c.eval(`document.querySelector(".ss-card")?.remove(); document.querySelector("#sheet-close")?.click(); 1`);
  await sleep(800);
  await c.eval(`$tab("today")`);
  await c.eval(`document.querySelector(".ss-card")?.remove(); 1`);
  await c.shot("today");
  note("today", []);
});

if (want("me-ai", "ai-settings", "ai-kinds", "ai-api", "ai-mcp", "jarvis-none")) await session(CODE_AI, async (c) => {
  await c.eval(`document.querySelector("#sheet-close")?.click(); 1`);
  await sleep(600);
  await c.eval(`$tab("me")`);
  await c.eval(`document.querySelector(".ss-card")?.remove(); const r = $t("button", "AI settings"); r.scrollIntoView({ block: "center" }); 1`);
  await sleep(500);
  await c.shot("me-ai");
  note("me-ai", [await c.eval(`$box($t("button", "AI settings"), 2)`)]);

  await c.eval(`$click("button", "AI settings")`);
  await c.eval(`$wait(() => $t("#sheet button", "Add"))`);
  await c.shot("ai-settings");
  note("ai-settings", [await c.eval(`$box($t("#sheet button", "Add"), 2)`), await c.eval(`$box($t("#sheet button", "Connect a client"), 2)`)]);

  // "Add" -> the source list. The native iOS picker cannot be captured on the web, so the list is drawn the way iOS shows it
  await c.eval(`$click("#sheet button", "Add")`);
  await c.eval(`$wait(() => document.querySelector("#sheet select"))`);
  const kinds = await c.eval(`(() => {
    const sel = document.querySelector("#sheet select"), sr = sel.getBoundingClientRect();
    const labels = [...sel.options].map((o) => o.textContent.trim());
    const menu = document.createElement("div");
    menu.id = "shot-menu";
    menu.style.cssText = "position:fixed;left:" + Math.round(sr.left + 28) + "px;width:" + Math.round(sr.width - 56) + "px;z-index:99999;background:rgba(246,246,248,.98);border-radius:14px;box-shadow:0 8px 40px rgba(0,0,0,.28);padding:6px 0;font:17px/1.25 -apple-system,system-ui,sans-serif;color:#111";
    labels.forEach((t, i) => { const d = document.createElement("div"); d.style.cssText = "padding:9px 16px 9px 38px;position:relative"; d.textContent = t; if (i === sel.selectedIndex) { const k = document.createElement("span"); k.textContent = "✓"; k.style.cssText = "position:absolute;left:14px"; d.appendChild(k); } menu.appendChild(d); });
    document.body.appendChild(menu);
    const mh = menu.getBoundingClientRect().height;
    menu.style.top = Math.max(70, Math.round(sr.top - mh - 8)) + "px";
    const items = [...menu.children].map((d) => d.getBoundingClientRect());
    const apiRows = labels.map((t, i) => [t, i]).filter(([t]) => /API|compatible/i.test(t)).map(([, i]) => items[i]);
    const x0 = Math.min(...apiRows.map((r) => r.left)) + 6, y0 = Math.min(...apiRows.map((r) => r.top)) + 1, x1 = Math.max(...apiRows.map((r) => r.right)) - 6, y1 = Math.max(...apiRows.map((r) => r.bottom)) - 1;
    return { labels, box: [x0, y0, x1, y1].map(Math.round) };
  })()`);
  await c.shot("ai-kinds");
  note("ai-kinds", [kinds.box]);
  await c.eval(`document.querySelector("#shot-menu")?.remove(); 1`);

  // the OpenAI-compatible source: model, endpoint and key fields
  await c.eval(`(() => { const s = document.querySelector("#sheet select"); const o = [...s.options].find((x) => /compatible/i.test(x.textContent)); s.value = o.value; s.dispatchEvent(new Event("change", { bubbles: true })); })()`);
  await sleep(700);
  await c.shot("ai-api");
  note("ai-api", [await c.eval(`(() => { const inputs = [...document.querySelectorAll("#sheet input[type=text], #sheet input[type=password], #sheet input:not([type])")].filter((e) => e.offsetParent); const labels = inputs.slice(0, 3).map((e) => e.closest("label, .field, div") || e); const r = labels.map((e) => e.getBoundingClientRect()); return [Math.min(...r.map((x) => x.left)) - 4, Math.min(...r.map((x) => x.top)) - 6, Math.max(...r.map((x) => x.right)) + 4, Math.max(...r.map((x) => x.bottom)) + 2].map(Math.round); })()`)]);
  await c.eval(`$closeSheet()`);

  // Connect a client (MCP)
  await c.eval(`$click("button", "AI settings")`);
  await c.eval(`$click("#sheet button", "Connect a client")`);
  await c.eval(`$wait(() => $t("#sheet button", "Create token"))`);
  await sleep(600);
  await c.shot("ai-mcp");
  note("ai-mcp", [await c.eval(`(() => { const a = [...document.querySelectorAll("#sheet .kv, #sheet .card, #sheet div")].find((e) => /\\/mcp/.test(e.textContent) && e.getBoundingClientRect().height < 80 && e.getBoundingClientRect().height > 20); return a ? $box(a, 2) : null; })()`),
                  await c.eval(`$box($t("#sheet button", "Create token"), 2)`)].filter(Boolean));
  await c.eval(`$closeSheet()`);

  // Jarvis with no AI set up
  await c.eval(`$tab("today")`);
  await c.eval(`(() => { const j = document.querySelector("#jarvis-entry, .jarvis-entry, [data-jarvis], #ai-fab, .ai-fab"); if (j) j.click(); })()`);
  await sleep(1200);
  await c.eval(`(() => { const b = $t("button", "Chat"); if (b) b.click(); })()`);
  await sleep(1200);
  await c.shot("jarvis-none");
  note("jarvis-none", [await c.eval(`(() => { const b = $t("button", "Not set"); return b ? $box(b, 4) : null; })()`)].filter(Boolean));
});

const specFile = join(dirname(OUT), "spec.json");
let old = {};
try { old = JSON.parse(readFileSync(specFile, "utf8")); } catch { /* first run */ }
writeFileSync(specFile, JSON.stringify({ ...old, ...spec }, null, 1) + "\n");
console.log("wrote", Object.keys(spec).join(", "), "->", OUT);
