// A small Chrome DevTools Protocol driver for the tools in this folder (Node >= 22: global WebSocket, no packages).
// Headless Google Chrome, an iPhone-sized English page; `inApp` makes the page believe it runs inside the Watch2Whoop app
// (the app's WebView injects window.Watch2Whoop), so the tab bar and the Me entries look like they do on the phone.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const CHROME = arg("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
export const IN_APP = `window.Watch2Whoop = { postMessage() {} };
  window.Watch2WhoopFeatures = { syncCenter: true, syncConnect: true, backfill: true, syncRepair: true, mic: true, voice: true, net: "wifi" };`;

export async function launch({ port = 9400 + Math.floor(Math.random() * 400), width = 402, height = 874, dpr = 2, lang = "en-US", inApp = true } = {}) {
  const proc = spawn(CHROME, [`--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "w2w-chrome-"))}`, "--headless=new",
    "--no-first-run", "--disable-gpu", "--hide-scrollbars", `--lang=${lang}`, `--window-size=${width},${height}`, "about:blank"], { stdio: "ignore" });
  let tab;
  for (let i = 0; i < 50 && !tab; i++) {
    try { tab = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch { /* not up yet */ }
    if (!tab) await sleep(200);
  }
  if (!tab) throw new Error("Chrome did not start");
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { const { res, rej } = pending.get(d.id); pending.delete(d.id); d.error ? rej(new Error(JSON.stringify(d.error))) : res(d.result); }
    // a confirm() / alert() would freeze every later call: answer "cancel" (nothing is deleted or signed out by a crawl)
    else if (d.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: false }).catch(() => {});
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  const metrics = (w, h) => send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dpr, mobile: true });
  await metrics(width, height);
  await send("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", acceptLanguage: lang });
  await send("Emulation.setLocaleOverride", { locale: lang });   // date inputs and number formats in the page language
  try { await send("Emulation.setSafeAreaInsetsOverride", { insets: { top: 59, bottom: 34, left: 0, right: 0 } }); } catch { /* older Chrome: no safe area */ }
  if (inApp) await send("Page.addScriptToEvaluateOnNewDocument", { source: IN_APP });
  return {
    metrics,
    async goto(url, wait = 2500) { await send("Page.navigate", { url }); await sleep(wait); },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails));
      return r.result.value;
    },
    async shot(file) {
      await send("Runtime.evaluate", { expression: `(() => { const t = document.querySelector("#toast"); if (t) t.hidden = true; })()` });   // no toast in a manual's picture
      const r = await send("Page.captureScreenshot", { format: "jpeg", quality: 92 });
      writeFileSync(file, Buffer.from(r.data, "base64"));
    },
    close() { ws.close(); proc.kill(); },
  };
}
