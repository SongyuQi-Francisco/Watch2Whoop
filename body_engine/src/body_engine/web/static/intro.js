/* First-run intro (issue #60): three short screens on what Watch2Whoop is, then tips for getting the most out of it.
   Shown once per user after login (server flag users.intro_seen_at, /v1/web/me -> me.intro_seen, so it follows the account
   across devices), skippable, and re-openable from 「我 → 新手引导」 (next to 「使用手册」). Kept short on purpose: the long
   explanations live in the handbook (/app/handbook/), which the last screen opens. In the Watch2Whoop app the native
   permissions step comes first; this page part is the same for web and app users.
   Two looks behind the rollout flag intro_v2 (rollout/registry.py, docs/rollout.md): on = the look below, off = the
   previous one (#63: icon, title, text, dots). Each look's CSS is scoped under .intro.v2 / .intro.v1.
   Look (v2): one big visual per screen on a dark "stage" panel (a recovery ring with metric chips, a chat that logs a meal,
   your server with swappable AIs), a large title with a numbered accent eyebrow, segmented progress at the top and a pill
   button at the bottom. The stage stays dark in both themes; the page around it follows the light / dark tokens. Each screen
   has its own accent. The numbers in the pictures are examples. Motion is short and off under prefers-reduced-motion.
   Registered in window.BodyPlugins (init) and window.BodyIntro (open, used by the Me page row). */
(() => {
  "use strict";
  let C = null;              // ctx from app.js: api, esc, toast, openHandbook, openAiSettings
  let step = 0;
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);

  // Line icons (24px grid, stroked)
  const ICONS = {
    chat: '<path d="M4 5h16v10H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    ring: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 8 8"/>',
    moon: '<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 1 7.5 18H19"/>',
    back: '<path d="M14.5 6l-6 6 6 6"/>',
    check: '<path d="M6 12.5l4 4 8-9"/>',
    spark: '<path d="M12 4l1.8 4.7L18.5 10l-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.3z"/>',
  };
  const icon = (n, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n]}</svg>`;

  // Screens: accent, stage picture, title, text. The last one is the tips list.
  const SCREENS = () => [
    { accent: "#3cc8a8", stage: stageScores,
      title: tr("手表数据 → 每天的分数"),
      text: tr("Apple Watch 的心率、HRV、睡眠和训练自动同步，算出恢复、负荷和睡眠分数。") },
    { accent: "#9b8cff", stage: stageChat,
      title: tr("记录：和你的 AI 说"),
      text: tr("吃了什么、练了什么、感觉怎样，告诉你自己的 AI（Claude、ChatGPT 等），它直接记进来，不用填表。") },
    { accent: "#5aa9ff", stage: stageServer,
      title: tr("数据在你的服务器上"),
      text: tr("数据存在你自己的账号和服务器里；AI 只负责读写，换一个 AI，数据还在。") },
    { accent: "#f2b84b", tips: true, title: tr("用得更好") },
  ];
  const TIPS = () => [
    { icon: "chat", text: tr("连接你的 AI：「我 → AI 设置」"), action: "ai", label: tr("去连接") },
    { icon: "ring", text: tr("每天看一眼今天页：恢复分告诉你今天练多重") },
    { icon: "moon", text: tr("戴着手表睡觉，睡眠和恢复才准") },
    { icon: "clock", text: tr("第一次同步默认读最近 90 天，几分钟到十几分钟；要更早的历史，在「我 → 手机同步」选「全部历史」") },
    { icon: "book", text: tr("更多说明在「我 → 使用手册」"), action: "handbook", label: tr("打开手册") },
  ];

  // ---- stage pictures (example numbers; all text through tr)
  function stageScores() {
    const r = 64, c = +(2 * Math.PI * r).toFixed(1), pct = 82;
    return `<div class="is-ring">
        <svg viewBox="0 0 160 160" aria-hidden="true">
          <defs><linearGradient id="is-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#7cf0cf"/><stop offset="1" style="stop-color:var(--ia)"/></linearGradient></defs>
          <circle cx="80" cy="80" r="${r}" class="is-track"/>
          <circle cx="80" cy="80" r="${r}" class="is-arc" style="--c:${c};--o:${(c * (1 - pct / 100)).toFixed(1)}"
            stroke-dasharray="${c}" stroke-dashoffset="${(c * (1 - pct / 100)).toFixed(1)}" transform="rotate(-90 80 80)"/>
        </svg>
        <div class="is-num"><b>${pct}</b><i>%</i><span>${C.esc(tr("恢复"))}</span></div>
      </div>
      <div class="is-chip is-c1"><em>HRV</em><b>64</b><small>ms</small></div>
      <div class="is-chip is-c2"><em>${C.esc(tr("静息心率"))}</em><b>52</b><small>bpm</small></div>
      <div class="is-chip is-c3"><em>${C.esc(tr("睡眠"))}</em><b>7:40</b></div>`;
  }

  function stageChat() {
    return `<div class="is-chat">
        <div class="is-bub is-me">${C.esc(tr("午饭：鸡胸肉沙拉，一碗米饭"))}</div>
        <div class="is-bub is-ai">${icon("spark", "is-ai-ic")}<div><div class="is-ai-t">${C.esc(tr("已记录午饭"))}</div>
          <div class="is-ai-n"><b>620</b> kcal <span>·</span> <b>48</b> g ${C.esc(tr("蛋白质"))}</div></div></div>
        <div class="is-bub is-me is-b2">${C.esc(tr("练了腿，深蹲 5 组"))}</div>
        <div class="is-bub is-ai is-b3">${icon("check", "is-ai-ic")}<div class="is-ai-t">${C.esc(tr("已记进训练"))}</div></div>
      </div>`;
  }

  function stageServer() {
    return `<div class="is-srv">
        <svg class="is-links" viewBox="0 0 300 220" aria-hidden="true" preserveAspectRatio="xMidYMid meet">
          <path d="M150 110 L58 44"/><path d="M150 110 L242 44"/><path d="M150 110 L150 196"/>
        </svg>
        <div class="is-node is-n1">${icon("spark")}<span>AI</span></div>
        <div class="is-node is-n2">${icon("spark")}<span>AI</span></div>
        <div class="is-node is-n3">${icon("spark")}<span>AI</span></div>
        <div class="is-db"><svg viewBox="0 0 48 48" aria-hidden="true"><ellipse cx="24" cy="11" rx="15" ry="5.5"/>
          <path d="M9 11v26c0 3 6.7 5.5 15 5.5S39 40 39 37V11"/><path d="M9 20c0 3 6.7 5.5 15 5.5S39 23 39 20M9 28.5c0 3 6.7 5.5 15 5.5s15-2.5 15-5.5"/></svg>
          <span>${C.esc(tr("你的服务器"))}</span></div>
      </div>`;
  }

  const CSS_V2 = `
    .intro.v2{position:fixed;inset:0;z-index:55;background:var(--bg);color:var(--text);display:flex;flex-direction:column;overflow:auto;-webkit-overflow-scrolling:touch}
    .intro.v2 .intro-in{flex:1;display:flex;flex-direction:column;width:100%;max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 10px) 20px calc(env(safe-area-inset-bottom) + 20px)}
    .intro.v2 .intro-top{display:flex;align-items:center;gap:14px;min-height:40px}
    .intro.v2 .intro-prog{flex:1;display:flex;gap:6px}
    .intro.v2 .intro-prog i{flex:1;height:4px;border-radius:2px;background:var(--surface-2);overflow:hidden;position:relative}
    .intro.v2 .intro-prog i.on::after{content:"";position:absolute;inset:0;background:var(--ia);border-radius:2px}
    .intro.v2 .intro-prog i.cur::after{animation:introFill .5s var(--ease) both}
    .intro.v2 .intro-skip{font:inherit;font-size:15px;font-weight:500;color:var(--text-2);background:none;border:0;padding:8px 2px;cursor:pointer}
    .intro.v2 .intro-stage{position:relative;margin-top:14px;height:clamp(230px,44vh,380px);border-radius:28px;overflow:hidden;color:#f4f5f7;
      background:radial-gradient(120% 80% at 50% 0%,var(--ia-34) 0%,transparent 62%),
                 linear-gradient(180deg,#151a20 0%,#0b0d10 100%);
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.07)}
    .intro.v2 .intro-stage::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
      background:linear-gradient(135deg,rgba(255,255,255,.10),rgba(255,255,255,0) 40%)}
    .intro.v2 .intro-copy{padding:24px 4px 8px}
    .intro.v2 .intro-eyebrow{font-size:13px;font-weight:600;letter-spacing:.06em;color:var(--ia-text);font-variant-numeric:tabular-nums;margin-bottom:10px}
    .intro.v2 h1{font-size:30px;line-height:1.18;margin:0 0 12px;letter-spacing:-.02em;font-weight:700;outline:none}
    .intro.v2 p{font-size:16px;line-height:1.55;color:var(--text-2);margin:0}
    .intro.v2 .intro-body{flex:1;display:flex;flex-direction:column}
    .intro.v2 .intro-tips{list-style:none;padding:0;margin:6px 0 0;display:flex;flex-direction:column;gap:10px}
    .intro.v2 .intro-tips li{background:var(--surface);border-radius:18px;padding:12px 12px 12px 12px;display:flex;align-items:center;gap:12px;font-size:15px;line-height:1.4}
    .intro.v2 .intro-tips li span{flex:1}
    .intro.v2 .intro-tips .ti{flex:none;width:36px;height:36px;border-radius:12px;display:flex;align-items:center;justify-content:center;
      background:var(--ia-18);color:var(--ia-text)}
    .intro.v2 .intro-tips .ti svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
    .intro.v2 .intro-tips .btn{flex:none;padding:7px 13px;font-size:14px;margin:0;border-radius:999px;background:var(--text);color:var(--bg)}
    .intro.v2 .intro-nav{display:flex;gap:10px;align-items:center;margin-top:18px}
    .intro.v2 .intro-nav .btn{margin:0}
    .intro.v2 .intro-next{flex:1;height:54px;border-radius:999px;font-size:17px;font-weight:600}
    .intro.v2 .intro-prev{flex:none;width:54px;height:54px;border-radius:50%;padding:0;display:flex;align-items:center;justify-content:center;background:var(--surface)}
    .intro.v2 .intro-prev svg{width:22px;height:22px;fill:none;stroke:var(--text);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
    .intro.v2.intro-anim .intro-stage>*,.intro.v2.intro-anim .intro-copy{animation:introUp .55s var(--ease) both}
    .intro.v2.intro-anim .intro-copy{animation-delay:.08s}

    .intro.v2 /* stage 1: recovery ring + chips */
    .is-ring{--rs:min(176px,48vw,26vh);position:absolute;inset:0;margin:auto;width:var(--rs);height:var(--rs)}
    .intro.v2 .is-ring svg{width:100%;height:100%;overflow:visible}
    .intro.v2 .is-track{fill:none;stroke:rgba(255,255,255,.09);stroke-width:12}
    .intro.v2 .is-arc{fill:none;stroke:url(#is-grad);stroke-width:12;stroke-linecap:round;filter:drop-shadow(0 0 10px var(--ia-55))}
    .intro.v2.intro-anim .is-arc{animation:introArc 1.1s .15s var(--ease) both}
    .intro.v2 .is-num{position:absolute;inset:0;display:flex;flex-wrap:wrap;align-content:center;justify-content:center;align-items:baseline}
    .intro.v2 .is-num b{font-size:clamp(40px,12vw,54px);font-weight:700;letter-spacing:-.03em;line-height:1}
    .intro.v2 .is-num i{font-style:normal;font-size:20px;font-weight:600;opacity:.7;margin-left:2px}
    .intro.v2 .is-num span{flex-basis:100%;text-align:center;font-size:13px;font-weight:600;color:var(--ia);margin-top:4px;letter-spacing:.04em}
    .intro.v2 .is-chip{position:absolute;display:flex;align-items:baseline;gap:5px;padding:8px 12px;border-radius:14px;font-size:12px;white-space:nowrap;
      background:rgba(255,255,255,.07);box-shadow:inset 0 0 0 1px rgba(255,255,255,.10);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
    .intro.v2 .is-chip em{font-style:normal;color:rgba(255,255,255,.62)}
    .intro.v2 .is-chip b{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}
    .intro.v2 .is-chip small{color:rgba(255,255,255,.5)}
    .intro.v2 .is-c1{left:5%;top:7%}.intro.v2 .is-c2{right:5%;bottom:7%}.intro.v2 .is-c3{left:5%;bottom:7%}
    .intro.v2.intro-anim .is-c1{animation-delay:.35s}.intro.v2.intro-anim .is-c2{animation-delay:.5s}.intro.v2.intro-anim .is-c3{animation-delay:.65s}

    .intro.v2 /* stage 2: chat that logs */
    .is-chat{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;gap:9px;padding:18px}
    .intro.v2 .is-bub{max-width:78%;padding:10px 13px;border-radius:18px;font-size:14px;line-height:1.35}
    .intro.v2 .is-me{align-self:flex-end;background:var(--ia);color:#14112a;border-bottom-right-radius:6px;font-weight:500}
    .intro.v2 .is-ai{align-self:flex-start;display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.08);
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.10);border-bottom-left-radius:6px}
    .intro.v2 .is-ai-ic{flex:none;width:20px;height:20px;fill:none;stroke:var(--ia);stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
    .intro.v2 .is-ai-t{font-weight:600}
    .intro.v2 .is-ai-n{font-size:12.5px;color:rgba(255,255,255,.65);margin-top:2px;font-variant-numeric:tabular-nums}
    .intro.v2 .is-ai-n b{color:#fff;font-size:15px}
    .intro.v2.intro-anim .is-chat>*{animation:introUp .45s var(--ease) both}
    .intro.v2.intro-anim .is-chat>:nth-child(2){animation-delay:.35s}.intro.v2.intro-anim .is-chat>:nth-child(3){animation-delay:.7s}.intro.v2.intro-anim .is-chat>:nth-child(4){animation-delay:1.05s}

    .intro.v2 /* stage 3: your server,.intro.v2 AIs around it */
    .is-srv{position:absolute;inset:0}
    .intro.v2 .is-links{position:absolute;inset:0;width:100%;height:100%}
    .intro.v2 .is-links path{fill:none;stroke:var(--ia-55);stroke-width:1.5;stroke-dasharray:4 6}
    .intro.v2.intro-anim .is-links path{animation:introDash 1.6s linear infinite}
    .intro.v2 .is-db{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;gap:8px}
    .intro.v2 .is-db svg{width:76px;height:76px;padding:16px;border-radius:24px;fill:none;stroke:#fff;stroke-width:2.2;stroke-linejoin:round;
      background:linear-gradient(160deg,var(--ia-d70),var(--ia-d35));
      box-shadow:0 0 0 1px rgba(255,255,255,.14) inset,0 10px 40px var(--ia-45)}
    .intro.v2 .is-db span{font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.08)}
    .intro.v2 .is-node{position:absolute;display:flex;align-items:center;gap:5px;padding:7px 11px;border-radius:999px;font-size:12px;font-weight:700;
      background:rgba(255,255,255,.07);box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}
    .intro.v2 .is-node svg{width:15px;height:15px;fill:none;stroke:var(--ia);stroke-width:1.8;stroke-linejoin:round}
    .intro.v2 .is-n1{left:8%;top:12%}.intro.v2 .is-n2{right:8%;top:12%}.intro.v2 .is-n3{left:50%;bottom:5%;transform:translateX(-50%)}
    .intro.v2.intro-anim .is-n2{animation:introSwap 3.2s 1s ease-in-out infinite}

    @keyframes introUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
    @keyframes introFill{from{transform:translateX(-100%)}to{transform:none}}
    @keyframes introArc{from{stroke-dashoffset:var(--c)}to{stroke-dashoffset:var(--o)}}
    @keyframes introDash{to{stroke-dashoffset:-20}}
    @keyframes introSwap{0%,40%{opacity:1}50%,60%{opacity:.15}70%,100%{opacity:1}}
    @media (prefers-reduced-motion: reduce){
      .intro.v2 *,.intro.v2 *::after{animation:none!important;transition:none!important}
    }
    @media (max-height:640px){.intro.v2 .intro-copy{padding-top:16px}.intro.v2 h1{font-size:26px}.intro.v2 .intro-stage{height:clamp(200px,38vh,300px)}.intro.v2 .is-b2,.intro.v2 .is-b3{display:none}}`;

  // The previous look (#63), shown while the intro_v2 flag is off for the user.
  const ICONS_V1 = {
    watch: '<rect x="7" y="6" width="10" height="12" rx="3"/><path d="M9 6l.5-3h5l.5 3M9 18l.5 3h5l.5-3M10 12h1.5l1-2 1 4 1-2H15"/>',
    chat: '<path d="M4 5h16v10H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    star: '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.5 6.7 19.4l1.2-6L3.4 9.3l6-.7z"/>',
  };
  const SCREENS_V1 = () => [
    { icon: "watch", title: tr("手表数据 → 每天的分数"),
      text: tr("Apple Watch 的心率、HRV、睡眠和训练自动同步，算出恢复、负荷和睡眠分数。") },
    { icon: "chat", title: tr("记录：和你的 AI 说"),
      text: tr("吃了什么、练了什么、感觉怎样，告诉你自己的 AI（Claude、ChatGPT 等），它直接记进来，不用填表。") },
    { icon: "lock", title: tr("数据在你的服务器上"),
      text: tr("数据存在你自己的账号和服务器里；AI 只负责读写，换一个 AI，数据还在。") },
    { icon: "star", title: tr("用得更好"), tips: true },
  ];
  const CSS_V1 = `
    .intro.v1{position:fixed;inset:0;z-index:55;background:var(--bg);color:var(--text);display:flex;flex-direction:column;overflow:auto;-webkit-overflow-scrolling:touch}
    .intro.v1 .intro-in{flex:1;display:flex;flex-direction:column;width:100%;max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 12px) 24px calc(env(safe-area-inset-bottom) + 24px)}
    .intro.v1 .intro-top{display:flex;justify-content:flex-end;min-height:36px}
    .intro.v1 .intro-skip{font:inherit;font-size:15px;color:var(--text-2);background:none;border:0;padding:8px 4px;cursor:pointer}
    .intro.v1 .intro-body{flex:1;display:flex;flex-direction:column;justify-content:center;padding:12px 0}
    .intro.v1 .intro-icon{width:64px;height:64px;border-radius:18px;background:var(--surface);display:flex;align-items:center;justify-content:center;margin-bottom:22px}
    .intro.v1 .intro-icon svg{width:34px;height:34px;fill:none;stroke:var(--text);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
    .intro.v1 h1{font-size:26px;line-height:1.25;margin:0 0 12px;letter-spacing:-.01em}
    .intro.v1 p{font-size:16px;line-height:1.55;color:var(--text-2);margin:0}
    .intro.v1 .intro-tips{list-style:none;padding:0;margin:4px 0 0;display:flex;flex-direction:column;gap:10px}
    .intro.v1 .intro-tips li{background:var(--surface);border-radius:14px;padding:13px 14px;display:flex;align-items:center;gap:12px;font-size:15px;line-height:1.4}
    .intro.v1 .intro-tips li span{flex:1}
    .intro.v1 .intro-tips .btn{flex:none;padding:7px 12px;font-size:14px;margin:0;background:var(--bg)}
    .intro.v1 .intro-dots{display:flex;justify-content:center;gap:6px;margin:8px 0 18px}
    .intro.v1 .intro-dots i{width:7px;height:7px;border-radius:50%;background:var(--surface-2)}
    .intro.v1 .intro-dots i.on{background:var(--text);width:18px;border-radius:4px}
    .intro.v1 .intro-nav{display:flex;gap:10px}.intro.v1 .intro-nav .btn{flex:1;margin:0}`;

  function injectCss(v2) {
    const id = v2 ? "intro-css-v2" : "intro-css-v1";
    if ($(`#${id}`)) return;
    const st = document.createElement("style");
    st.id = id;
    st.textContent = v2 ? CSS_V2 : CSS_V1;
    document.head.appendChild(st);
  }

  // Server flag; failures are not shown (worst case the intro shows once more)
  async function mark(seen) {
    try { await C.api("/v1/users/me/intro", { method: "PUT", body: { seen } }); } catch (e) { /* offline / read-only token */ }
  }

  function close(done) {
    const el = $("#intro");
    if (el) el.remove();
    if (done) mark(true);
  }

  // Accent shades as plain rgb() (the iOS 15 WebView has no CSS color-mix). On a light page the accent text is darkened so
  // it stays readable; the stage is always dark.
  function accentVars(el, hex) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const a = (x) => `rgba(${r},${g},${b},${x})`, d = (k) => `rgb(${Math.round(r * k)},${Math.round(g * k)},${Math.round(b * k)})`;
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const vars = { "--ia": hex, "--ia-text": dark ? hex : d(0.62), "--ia-18": a(0.18), "--ia-34": a(0.34), "--ia-45": a(0.45),
                   "--ia-55": a(0.55), "--ia-d70": d(0.7), "--ia-d35": d(0.35) };
    for (const [k, v] of Object.entries(vars)) el.style.setProperty(k, v);
  }

  function drawV2() {
    const el = $("#intro");
    if (!el) return;
    const screens = SCREENS(), s = screens[step], last = step === screens.length - 1, n = screens.length;
    accentVars(el, s.accent);
    const eyebrow = `${String(step + 1).padStart(2, "0")} / ${String(n).padStart(2, "0")}`;
    const body = s.tips
      ? `<div class="intro-copy"><div class="intro-eyebrow">${eyebrow}</div><h1 id="intro-title" tabindex="-1">${C.esc(s.title)}</h1></div>
         <ul class="intro-tips">${TIPS().map((t) => `<li><div class="ti">${icon(t.icon)}</div><span>${C.esc(t.text)}</span>${t.action
           ? `<button type="button" class="btn" data-intro-act="${t.action}">${C.esc(t.label)}</button>` : ""}</li>`).join("")}</ul>`
      : `<div class="intro-stage" aria-hidden="true">${s.stage()}</div>
         <div class="intro-copy"><div class="intro-eyebrow">${eyebrow}</div><h1 id="intro-title" tabindex="-1">${C.esc(s.title)}</h1><p>${C.esc(s.text)}</p></div>`;
    el.innerHTML = `<div class="intro-in">
        <div class="intro-top"><div class="intro-prog" aria-hidden="true">${screens.map((_, i) =>
          `<i class="${i <= step ? "on" : ""}${i === step ? " cur" : ""}"></i>`).join("")}</div>
          ${last ? "" : `<button type="button" class="intro-skip" id="intro-skip">${C.esc(tr("跳过"))}</button>`}</div>
        <div class="intro-body">${body}</div>
        <div class="intro-nav">${step > 0 ? `<button type="button" class="btn intro-prev" id="intro-prev" aria-label="${C.esc(tr("上一步"))}">${icon("back")}</button>` : ""}
          <button type="button" class="btn btn-primary intro-next" id="intro-next">${C.esc(last ? tr("开始使用") : tr("下一步"))}</button></div></div>`;
    el.setAttribute("aria-labelledby", "intro-title");
    // restart the entrance animation for every screen
    el.classList.remove("intro-anim");
    void el.offsetWidth;
    el.classList.add("intro-anim");
    const skip = $("#intro-skip", el);
    if (skip) skip.onclick = () => close(true);
    const prev = $("#intro-prev", el);
    if (prev) prev.onclick = () => { step--; drawV2(); };
    $("#intro-next", el).onclick = () => { if (last) close(true); else { step++; drawV2(); } };
    for (const b of el.querySelectorAll("[data-intro-act]")) b.onclick = () => {
      close(true);
      if (b.dataset.introAct === "ai") C.openAiSettings?.(); else C.openHandbook?.();
    };
    $("#intro-title", el).focus({ preventScroll: true });   // screen readers read the new screen's title
  }

  function drawV1() {
    const el = $("#intro");
    if (!el) return;
    const screens = SCREENS_V1(), s = screens[step], last = step === screens.length - 1;
    const body = s.tips
      ? `<ul class="intro-tips">${TIPS().map((t) => `<li><span>${C.esc(t.text)}</span>${t.action ? `<button type="button" class="btn" data-intro-act="${t.action}">${C.esc(t.label)}</button>` : ""}</li>`).join("")}</ul>`
      : `<p>${C.esc(s.text)}</p>`;
    el.innerHTML = `<div class="intro-in">
        <div class="intro-top">${last ? "" : `<button type="button" class="intro-skip" id="intro-skip">${C.esc(tr("跳过"))}</button>`}</div>
        <div class="intro-body"><div class="intro-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS_V1[s.icon]}</svg></div>
          <h1 id="intro-title">${C.esc(s.title)}</h1>${body}</div>
        <div class="intro-dots" aria-hidden="true">${screens.map((_, i) => `<i class="${i === step ? "on" : ""}"></i>`).join("")}</div>
        <div class="intro-nav">${step > 0 ? `<button type="button" class="btn" id="intro-prev">${C.esc(tr("上一步"))}</button>` : ""}
          <button type="button" class="btn btn-primary" id="intro-next">${C.esc(last ? tr("开始使用") : tr("下一步"))}</button></div></div>`;
    el.setAttribute("aria-labelledby", "intro-title");
    const skip = $("#intro-skip", el);
    if (skip) skip.onclick = () => close(true);
    const prev = $("#intro-prev", el);
    if (prev) prev.onclick = () => { step--; drawV1(); };
    $("#intro-next", el).onclick = () => { if (last) close(true); else { step++; drawV1(); } };
    for (const b of el.querySelectorAll("[data-intro-act]")) b.onclick = () => {
      close(true);
      if (b.dataset.introAct === "ai") C.openAiSettings?.(); else C.openHandbook?.();
    };
    $("#intro-next", el).focus({ preventScroll: true });
  }

  // Which look: the rollout flag intro_v2 (docs/rollout.md) of the signed-in user, read once per opening
  let isV2 = false;
  const draw = () => (isV2 ? drawV2() : drawV1());

  function open() {
    if (!C) return;
    isV2 = !!window.BodyFlags?.on("intro_v2");
    injectCss(isV2);
    step = 0;
    let el = $("#intro");
    if (!el) {
      el = document.createElement("div");
      el.id = "intro";
      el.className = "intro";
      el.setAttribute("role", "dialog");
      el.setAttribute("aria-modal", "true");
      document.body.appendChild(el);
    }
    el.className = `intro ${isV2 ? "v2" : "v1"}`;
    draw();
  }

  // Shown once: own active account, not seen yet, and only once the consent sheet (profile.js) is out of the way
  function maybeShow(me) {
    const u = me?.me;
    if (!u || u.intro_seen !== false || u.status === "pending" || !(u.token_scopes || []).includes("write")) return;
    let tries = 0;
    const wait = () => {
      if (++tries > 600) return;   // give up after ~10 minutes
      const busy = document.querySelector(".consent") || $("#app")?.hidden !== false || !$("#login")?.hidden;
      if (busy) { setTimeout(wait, 1000); return; }
      u.intro_seen = true;   // not again in this page session even if the PUT fails
      open();
    };
    setTimeout(wait, 400);
  }

  window.BodyIntro = { open };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx, me) { C = ctx; maybeShow(me); },
  });
})();
