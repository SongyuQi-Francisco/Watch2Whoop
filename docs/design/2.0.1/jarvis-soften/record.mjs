// Renders stills and film frames of the prototype with headless Chrome over the DevTools protocol (no dependencies; Node 22+).
//   node record.mjs still <page.html?query> <out.png> [width height | auto]   (auto = the size of the page: boards)
//   node record.mjs film  <page.html?query> <frames dir> [seconds=21] [fps=60]
import {spawn} from 'node:child_process';
import {mkdirSync, writeFileSync, mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
const [mode, page, out, a3, a4] = process.argv.slice(2);
const se = /size=se/.test(page);
const W = mode === 'still' && +a3 ? +a3 : se ? 375 : 402, H = mode === 'still' && a4 ? +a4 : se ? 667 : 874;
const port = 9300 + Math.floor(Math.random() * 600);
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', ['--headless=new', '--hide-scrollbars',
  '--remote-debugging-port=' + port, '--user-data-dir=' + mkdtempSync(join(tmpdir(), 'soften-')), 'about:blank'], {stdio: 'ignore'});
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws;
for (let i = 0; i < 50 && !ws; i++) {
  await sleep(200);
  try { const t = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(x => x.type === 'page'); if (t) ws = new WebSocket(t.webSocketDebuggerUrl); } catch {}
}
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const wait = new Map();
ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d.result); wait.delete(d.id); } });
const send = (method, params = {}) => new Promise(r => { wait.set(++id, r); ws.send(JSON.stringify({id, method, params})); });
await send('Emulation.setDeviceMetricsOverride', {width: W, height: H, deviceScaleFactor: 2, mobile: false});
const [file, query = ''] = page.split('?');
const film = mode === 'film';
await send('Page.navigate', {url: 'file://' + resolve(here, file) + '?' + query + (film ? '&rec=1' : '')});
await sleep(+(process.env.WAIT || 1200));
if (a3 === 'auto') { const r = JSON.parse((await send('Runtime.evaluate', {expression: 'JSON.stringify([document.body.scrollWidth,document.body.scrollHeight])'})).result.value);
  await send('Emulation.setDeviceMetricsOverride', {width: r[0], height: r[1], deviceScaleFactor: 2, mobile: false}); await sleep(1500); }
const shot = async path => writeFileSync(path, Buffer.from((await send('Page.captureScreenshot', {format: 'png'})).data, 'base64'));
if (film) {
  mkdirSync(out, {recursive: true});
  const secs = +(a3 || 21), fps = +(a4 || 60);
  for (let i = 0; i < secs * fps; i++) {
    await send('Runtime.evaluate', {expression: `frame(${(i * 1000 / fps).toFixed(3)})`});
    await shot(join(out, String(i).padStart(5, '0') + '.png'));
  }
} else await shot(out);
ws.close(); chrome.kill();
