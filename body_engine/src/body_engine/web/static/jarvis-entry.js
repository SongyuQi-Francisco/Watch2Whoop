/* Jarvis entry (issue #53, rollout flag jarvis_orbit; styles in jarvis-entry.css). Registered in window.BodyPlugins; with the flag
   off nothing here runs and the dock keeps the 「Jarvis」 pill (index.html).

   - The dock's Jarvis key becomes a round icon button (Lucide "orbit", no text), centred and raised above the tab bar.
   - Jarvis = the built-in fallback AI (BodyAI.isJarvis()). For Jarvis:
     · tap: a light runs once clockwise round the key's ring and brings out four keys on an arc above it: 对话 (the full chat),
       语音 (talk without holding: straight into the locked state), 拍照 (the camera; the photo goes to Jarvis), 快记 (quick log);
     · hold (HOLD_MS): talk at once. Release = done; slide up-left = cancel; slide up-right = lock (hands free, then 发送).
       Recognition: the app's on-device recogniser when it has one for the language (window.Watch2WhoopFeatures.voice, app 1.0.8+:
       words appear while talking; Chinese is finalised by the server afterwards, English stays on the phone), otherwise this page
       records and the server recognises (POST /v1/assistant/transcribe, as in the chat). Either way the text is corrected with the
       user's own terms on the server (ai/voice_fix.py) and then goes to Jarvis in the half sheet (ai.js openHalf), at once with flag
       jarvis_voice_direct, otherwise after the user checked it (the consent says the text is confirmed before it is sent).
     · the same hold-to-talk on the chat's own mic button (bindMic), started at once (it is a mic button).
   - Anyone else (own API / subscription AI, the body butler, none): only the look changes; a tap does what the pill did.
   Motion follows prefers-reduced-motion in JS too (Web Animations are not covered by app.css' global rule): no sweep, no fly-out,
   keys fade in place. Haptics only when the app says it has them (Watch2WhoopFeatures.haptics). Nothing of a recording is kept. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const reduced = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const FLAG = "jarvis_orbit";

  // Gesture and layout parameters (the design's numbers; HOLD_MS is the one to tune in the beta)
  const HOLD_MS = 350;          // longer than this without moving = talk (iOS long press ~500 ms; WeChat starts at once)
  const RING_DELAY_MS = 120;    // the hold ring starts filling only after this, so a quick tap shows nothing
  const MOVE_TOL = 10;          // moving more than this before HOLD_MS = not a press (a scroll)
  const CLICK_GUARD_MS = 450;   // the click a browser sends after a release is ignored
  const MIN_S = 0.6;            // shorter holds are not sent (as the chat's mic, ai-media.js)
  const UP_PX = 60;             // slide up at least this far before cancel / lock count by side
  const HIT_PX = 48, NEAR_PX = 150;
  const R_KEY = 98, R_RING = 35, START = 225, SWEEP_MS = 560, CIRC = 219.91, BARS = 34;
  const EASE = [0.45, 0.05, 0.35, 1];
  const FINAL_WAIT_MS = 8000;   // the app's final result after 发送; then the last partial text is used
  const START_WAIT_MS = 20000;  // the app says "started" (after its permission prompts the first time); else unavailable
  const KEYS = [   // angle on the arc, 0 = 12 o'clock, clockwise; the order never changes
    { id: "chat", label: () => tr("对话"), icon: "message-circle", th: 290 },
    { id: "voice", label: () => tr("语音"), icon: "mic", th: 336.67 },
    { id: "photo", label: () => tr("jarvis.photo"), icon: "camera", th: 23.33 },
    { id: "quick", label: () => tr("快记"), icon: "square-pen", th: 70 },
  ];

  // Lucide icons (https://lucide.dev, ISC; THIRD_PARTY_NOTICES.md), paths as published, only the ones used
  const LU = {
    orbit: '<path d="M20.341 6.484A10 10 0 0 1 10.266 21.85"/><path d="M3.659 17.516A10 10 0 0 1 13.74 2.152"/><circle cx="12" cy="12" r="3"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/>',
    "message-circle": '<path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719"/>',
    mic: '<path d="M12 19v3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><rect x="9" y="2" width="6" height="13" rx="3"/>',
    camera: '<path d="M13.997 4a2 2 0 0 1 1.76 1.05l.486.9A2 2 0 0 0 18.003 7H20a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1.997a2 2 0 0 0 1.759-1.048l.489-.904A2 2 0 0 1 10.004 4z"/><circle cx="12" cy="13" r="3"/>',
    "square-pen": '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  };
  const lu = (name, sw = 2) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${LU[name]}</svg>`;

  let C = null;            // plugin context from app.js
  let jv = null;           // the Jarvis key (id "ask", so app.js' offline greying and needsNet still apply)
  const M = { open: false };
  let press = null, ignoreClickUntil = 0;

  const AI = () => window.BodyAI;
  const isJarvis = () => !!AI()?.isJarvis();
  const inApp = () => !!window.Watch2Whoop;
  const offline = () => document.body.classList.contains("is-offline");
  const directSend = () => !!window.BodyFlags?.on("jarvis_voice_direct");
  const tell = (msg) => { try { if (inApp()) window.Watch2Whoop.postMessage(msg); } catch (e) { /* older app */ } };
  function haptic(kind) { if (window.Watch2WhoopFeatures?.haptics) tell(`haptic:${kind}`); }
  function announce(text) { const el = $("#jv-sr"); if (!el) return; el.textContent = ""; setTimeout(() => { el.textContent = text; }, 30); }

  // ---------------------------------------------------------------- recognition language (zh / en), remembered per person on this device
  let meId = null;
  const langKey = () => `jv.voiceLang.${meId ?? ""}`;
  function voiceLang() {
    try { const v = localStorage.getItem(langKey()); if (v === "zh" || v === "en") return v; } catch (e) { /* private mode */ }
    return window.I18N.lang === "en" ? "en" : "zh";
  }
  function setVoiceLang(l) { try { localStorage.setItem(langKey(), l); } catch (e) { /* private mode */ } }
  const langLabel = (l) => (l === "en" ? "English" : tr("中文"));

  // Which recogniser serves a language: "native" (the app, on the phone), "server" (this page records, the server recognises), null
  function engineFor(lang) {
    const nv = window.BodyMedia?.nativeVoice();
    if (nv && nv.langs.includes(lang)) return "native";
    return AI()?.voicePath() === "record" ? "server" : null;
  }
  const voiceOn = () => !!(jv && isJarvis() && engineFor(voiceLang()));

  // ---------------------------------------------------------------- mount (once, when the flag is on)
  function mount() {
    const old = $("#ask");
    if (!old || jv) return;
    const row = document.createElement("div");
    row.className = "jv-row";
    row.innerHTML = `
      <span class="jv-halo" id="jv-halo" aria-hidden="true"></span>
      <button type="button" class="jv" id="ask" aria-label="Jarvis">
        <svg class="jv-ring" viewBox="0 0 76 76" aria-hidden="true">
          <circle class="trk" cx="38" cy="38" r="35"/>
          <circle class="hold" id="jv-ring-hold" cx="38" cy="38" r="35" transform="rotate(-90 38 38)"/>
          <circle class="swp" id="jv-ring-swp" cx="38" cy="38" r="35" transform="rotate(135 38 38)"/>
        </svg>
        <span class="jv-comet" id="jv-comet" aria-hidden="true"></span>
        <span class="jv-ic jv-ic-main">${lu("orbit", 1.7)}</span>
        <span class="jv-ic jv-ic-x">${lu("x", 2)}</span>
        <span class="jv-ic jv-ic-mic">${lu("mic", 2)}</span>
      </button>
      <span class="jv-sr" id="jv-desc"></span>`;
    old.replaceWith(row);
    jv = $("#ask", row);
    document.body.classList.add("jv-orbit");
    const layer = document.createElement("div");
    layer.className = "jv-layer";
    layer.innerHTML = `
      <div class="jv-scrim" id="jv-scrim" hidden></div>
      <div class="jv-fan" id="jv-fan" role="menu" hidden>${KEYS.map((k) => `<button type="button" class="jv-key" role="menuitem" data-key="${k.id}">${lu(k.icon, 2)}<span class="lbl" aria-hidden="true"></span></button>`).join("")}</div>
      <div class="jv-vscrim" id="jv-vscrim" hidden></div>
      <div class="jv-vt jv-vt-cancel" id="jv-vt-cancel" aria-hidden="true" hidden>${lu("x", 2)}<span class="lbl"></span></div>
      <div class="jv-vt jv-vt-lock" id="jv-vt-lock" aria-hidden="true" hidden>${lu("lock", 2)}<span class="lbl"></span></div>
      <div class="jv-vpanel" id="jv-vpanel" role="dialog" hidden>
        <div class="jv-vp-top"><span class="jv-vp-dot" aria-hidden="true"></span><span id="jv-vp-title"></span>
          <button type="button" class="jv-vp-lang" id="jv-vp-lang" hidden></button><span class="jv-vp-time num" id="jv-vp-time">0:00</span></div>
        <div class="jv-wave" id="jv-wave" aria-hidden="true">${"<i></i>".repeat(BARS)}</div>
        <p class="jv-vp-cap" id="jv-vp-cap"></p>
        <textarea class="input jv-vp-edit" id="jv-vp-edit" rows="3" hidden></textarea>
        <p class="jv-vp-hint" id="jv-vp-hint"></p>
        <div class="jv-vp-acts" id="jv-vp-acts" hidden><button type="button" id="jv-vp-cancel"></button><button type="button" class="pri" id="jv-vp-send"></button></div>
      </div>
      <input type="file" accept="image/*" capture="environment" id="jv-cam" hidden>
      <div class="jv-sr" id="jv-sr" aria-live="assertive"></div>`;
    document.body.appendChild(layer);
    bindKey();
    bindMenu();
    bindVoice();
    sync();
  }

  // aria and the menu marker follow the current AI (it can change at any time in AI settings)
  function sync() {
    if (!jv) return;
    const j = isJarvis();
    jv.setAttribute("aria-haspopup", j ? "menu" : "dialog");
    if (j) jv.setAttribute("aria-expanded", String(M.open)); else jv.removeAttribute("aria-expanded");
    jv.setAttribute("aria-label", M.open ? tr("收起 Jarvis 菜单") : "Jarvis");
    $("#jv-desc").textContent = j ? (voiceOn() ? tr("点按展开快捷菜单；按住直接说话，松开发送") : tr("点按展开快捷菜单")) : "";
    if (j) jv.setAttribute("aria-describedby", "jv-desc"); else jv.removeAttribute("aria-describedby");
    $$(".jv-key", $("#jv-fan")).forEach((b, i) => { const l = KEYS[i].label(); $(".lbl", b).textContent = l; b.setAttribute("aria-label", l); });
  }

  // While the menu or a locked recording is up, the page and the tab bar cannot be reached (taps or VoiceOver)
  function syncInert() {
    const block = M.open || V.state === "locked" || V.state === "review" || V.state === "final";
    for (const el of [$("#view"), $(".top"), $(".tabbar")]) if (el) el.inert = block;
  }

  // ---------------------------------------------------------------- the key: tap / hold
  function bindKey() {
    const holdRing = $("#jv-ring-hold");
    const ringStart = () => {
      if (reduced()) return;
      holdRing.getAnimations().forEach((a) => a.cancel());
      holdRing.animate([{ strokeDashoffset: CIRC }, { strokeDashoffset: 0 }], { duration: HOLD_MS - RING_DELAY_MS, delay: RING_DELAY_MS, easing: "linear", fill: "both" });
    };
    const ringStop = () => holdRing.getAnimations().forEach((a) => a.cancel());
    const cancelPress = (keepRec) => {
      if (!press) return;
      clearTimeout(press.timer);
      if (!keepRec) press.rec?.cancel();
      press = null; ringStop(); jv.classList.remove("pressed");
    };
    jv.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      sync();
      if (offline()) return;   // the click that follows says "needs a connection" (app.js)
      if (V.state === "locked" && V.anchor === jv) { e.preventDefault(); ignoreClickUntil = performance.now() + CLICK_GUARD_MS; finishVoice(); return; }
      if (V.state !== "off") return;
      e.preventDefault();
      try { jv.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
      press = { id: e.pointerId, x: e.clientX, y: e.clientY };
      jv.classList.add("pressed");
      if (!voiceOn()) return;   // not Jarvis, or no way to record here: a hold is a tap
      // iOS starts audio only inside the press itself: wake the recorder now, the microphone opens once the hold counts
      if (engineFor(voiceLang()) === "server") { press.rec = window.BodyMedia.recorder(); press.rec.prime(); }
      ringStart();
      press.timer = setTimeout(() => {
        if (!press) return;
        const p = press; cancelPress(true);
        if (M.open) closeMenu();
        if (!AI().ensureJarvis(() => C.toast(tr("好了，再按住说话")))) { p.rec?.cancel(); return; }
        startVoice({ anchor: jv, mode: "hold", x: p.x, y: p.y, pointerId: p.id, dest: "half", rec: p.rec });
      }, HOLD_MS);
    });
    let moved = false;   // the finger moved before the hold counted: not a press at all (no menu, no recording)
    jv.addEventListener("pointermove", (e) => {
      if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > MOVE_TOL) { moved = true; cancelPress(); }
    });
    jv.addEventListener("pointerup", (e) => {
      if (moved) { moved = false; ignoreClickUntil = performance.now() + CLICK_GUARD_MS; return; }
      if (!press || e.pointerId !== press.id) return;
      cancelPress();
      ignoreClickUntil = performance.now() + CLICK_GUARD_MS;
      tap(false);
    });
    jv.addEventListener("pointercancel", () => { moved = false; cancelPress(); });
    jv.addEventListener("contextmenu", (e) => e.preventDefault());
    // Keyboard (Enter / Space) and VoiceOver's double tap send only a click: a tap
    jv.addEventListener("click", (e) => {
      e.preventDefault();
      if (performance.now() < ignoreClickUntil) return;
      if (V.state === "locked" && V.anchor === jv) { finishVoice(); return; }
      if (V.state !== "off") return;
      tap(e.detail === 0);
    });
  }

  function tap(byKeyboard) {
    sync();
    if (offline()) { C.needsNet?.(); return; }
    if (!isJarvis()) { C.openAssistant?.(); return; }   // own AI / butler / none: as before
    if (M.open) closeMenu(byKeyboard); else openMenu(byKeyboard);
  }

  // ---------------------------------------------------------------- the ring menu
  // The inverse of a CSS cubic-bezier: the time at which the eased light reaches a given share of the turn, so each key comes out
  // just as the light passes its angle
  function timeAtProgress(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx, cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const X = (u) => ((ax * u + bx) * u + cx) * u, Y = (u) => ((ay * u + by) * u + cy) * u;
    return (p) => { let lo = 0, hi = 1; for (let i = 0; i < 32; i++) { const m = (lo + hi) / 2; if (Y(m) < p) lo = m; else hi = m; } return X((lo + hi) / 2); };
  }
  const timeAt = timeAtProgress(...EASE);
  function swapAnim(el, keyframes, opts) {   // start the new animation before cancelling the old one: no flash of the CSS start state
    const old = el.getAnimations();
    const a = el.animate(keyframes, { fill: "both", ...opts });
    old.forEach((x) => x.cancel());
    return a;
  }
  const centre = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };

  function openMenu(byKeyboard) {
    if (M.open || V.state !== "off") return;
    M.open = true;
    const fan = $("#jv-fan"), scrim = $("#jv-scrim"), sweep = $("#jv-ring-swp"), comet = $("#jv-comet");
    const c = centre(jv), rm = reduced(), keys = $$(".jv-key", fan);
    fan.hidden = false; scrim.hidden = false;
    $(".dock").classList.add("jv-dim");
    jv.classList.add("open");
    sync(); syncInert(); haptic("light");
    swapAnim(scrim, [{ opacity: 0 }, { opacity: 1 }], { duration: rm ? 140 : 220 });
    if (rm) swapAnim(sweep, [{ strokeDashoffset: 0, opacity: 0 }, { strokeDashoffset: 0, opacity: 1 }], { duration: 140 });
    else {   // one turn clockwise from half past seven, the drawn arc following the light
      const ease = `cubic-bezier(${EASE.join(",")})`;
      swapAnim(sweep, [{ strokeDashoffset: CIRC, opacity: 1 }, { strokeDashoffset: 0, opacity: 1 }], { duration: SWEEP_MS, easing: ease });
      swapAnim(comet, [{ transform: `rotate(${START}deg)`, opacity: 1 }, { transform: `rotate(${START + 360}deg)`, opacity: 1 }], { duration: SWEEP_MS, easing: ease });
      comet.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: SWEEP_MS, fill: "forwards" });
    }
    KEYS.forEach((k, i) => {
      const el = keys[i], lbl = $(".lbl", el), a = k.th * Math.PI / 180;
      const fx = Math.sin(a) * R_KEY, fy = -Math.cos(a) * R_KEY;
      el.style.left = `${c.x + fx}px`; el.style.top = `${c.y + fy}px`;
      if (rm) {   // reduced motion: no turn, no flight; the keys fade in where they stay
        swapAnim(el, [{ opacity: 0 }, { opacity: 1 }], { duration: 160 });
        swapAnim(lbl, [{ opacity: 0 }, { opacity: 1 }], { duration: 160 });
        return;
      }
      const rx = Math.sin(a) * R_RING - fx, ry = -Math.cos(a) * R_RING - fy;
      const frac = ((((k.th - START) % 360) + 360) % 360) / 360;
      const delay = Math.max(0, timeAt(frac) * SWEEP_MS - 40);
      swapAnim(el, [
        { opacity: 0, transform: `translate(${rx}px, ${ry}px) scale(.35)` },
        { opacity: 1, offset: 0.3 },
        { opacity: 1, transform: "translate(0, 0) scale(1)" },
      ], { duration: 380, delay, easing: "cubic-bezier(.34, 1.45, .55, 1)" });
      swapAnim(lbl, [{ opacity: 0, transform: "translate(-50%, -4px)" }, { opacity: 1, transform: "translate(-50%, 0)" }], { duration: 200, delay: delay + 180 });
    });
    announce(tr("菜单已展开：对话、语音、拍照、快记"));
    if (byKeyboard) setTimeout(() => keys[0].focus(), 30);
  }

  function closeMenu(focusBack) {
    if (!M.open) return;
    M.open = false;
    const fan = $("#jv-fan"), scrim = $("#jv-scrim"), rm = reduced(), keys = $$(".jv-key", fan);
    jv.classList.remove("open");
    $(".dock").classList.remove("jv-dim");
    sync(); syncInert();
    const done = [];
    KEYS.forEach((k, i) => {
      const el = keys[i], lbl = $(".lbl", el), a = k.th * Math.PI / 180;
      const fx = Math.sin(a) * R_KEY, fy = -Math.cos(a) * R_KEY;
      const rx = Math.sin(a) * R_RING - fx, ry = -Math.cos(a) * R_RING - fy;
      const an = rm ? swapAnim(el, [{ opacity: 1 }, { opacity: 0 }], { duration: 120 })
        : swapAnim(el, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: `translate(${rx}px, ${ry}px) scale(.35)` }],
          { duration: 180, delay: (KEYS.length - 1 - i) * 25, easing: "cubic-bezier(.4, 0, .6, 1)" });
      swapAnim(lbl, [{ opacity: 1 }, { opacity: 0 }], { duration: 100 });
      done.push(an.finished.catch(() => {}));
    });
    swapAnim($("#jv-ring-swp"), [{ strokeDashoffset: 0, opacity: 1 }, { strokeDashoffset: 0, opacity: 0 }], { duration: 180 });
    swapAnim($("#jv-comet"), [{ opacity: 0 }, { opacity: 0 }], { duration: 1 });
    swapAnim(scrim, [{ opacity: 1 }, { opacity: 0 }], { duration: rm ? 120 : 200 });
    Promise.all(done).then(() => { if (!M.open) { fan.hidden = true; scrim.hidden = true; } });
    if (focusBack) jv.focus();
  }

  function bindMenu() {
    const fan = $("#jv-fan");
    $("#jv-scrim").addEventListener("click", () => closeMenu());
    $(".dock").addEventListener("click", (e) => { if (M.open && !e.target.closest(".jv")) { e.preventDefault(); e.stopPropagation(); closeMenu(); } }, true);
    fan.addEventListener("click", (e) => {
      const b = e.target.closest(".jv-key");
      if (!b) return;
      closeMenu();
      runKey(b.dataset.key);
    });
    fan.addEventListener("keydown", (e) => {
      const keys = $$(".jv-key", fan), i = keys.indexOf(document.activeElement);
      const go = (j) => { e.preventDefault(); keys[(j + keys.length) % keys.length].focus(); };
      if (e.key === "ArrowRight" || e.key === "ArrowDown") go(i + 1);
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") go(i - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(keys.length - 1);
      else if (e.key === "Tab") { e.preventDefault(); if (e.shiftKey ? i === 0 : i === keys.length - 1) jv.focus(); else go(i + (e.shiftKey ? -1 : 1)); }
    });
    window.addEventListener("resize", () => { if (M.open) closeMenu(); });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (M.open) { e.stopImmediatePropagation(); closeMenu(true); }
      else if (V.state !== "off" && V.state !== "final") { e.stopImmediatePropagation(); cancelVoice(tr("已取消，没有发送")); }
    }, true);
    $("#jv-cam").addEventListener("change", (e) => {
      const f = e.target.files?.[0]; e.target.value = "";
      if (!f || !AI().openHalf({})) return;
      AI().sendPhoto(f);
    });
  }

  // The four keys. Each first makes sure Jarvis may be used (the consent sheet when it was never accepted).
  function runKey(id) {
    if (!AI().ensureJarvis(() => (id === "voice" || id === "photo" ? C.toast(tr("好了，再点一次")) : runKey(id)))) return;
    if (id === "chat") AI().openChat();
    else if (id === "voice") {
      if (!engineFor(voiceLang())) { noVoice(); return; }
      startVoice({ anchor: jv, mode: "locked", dest: "half" });
    } else if (id === "photo") {
      if (AI().ai().vision === false) { C.toast(tr("这个 AI 看不了图片")); return; }
      $("#jv-cam").click();
    } else if (id === "quick") openQuick();
  }

  // No way to record here (an app before 1.0.7, a browser without a microphone, recognition off on the server): the keyboard's
  // dictation key does it, as in the chat
  function noVoice() {
    if (!AI().openHalf({})) return;
    AI().draft("");
    C.toast(AI().ai().asr?.enabled ? tr("这里不能录音：用键盘上的 🎤 听写") : tr("语音识别没开通：用键盘上的 🎤 听写"));
  }

  // 快记: the half sheet with the most used meal templates, water, "training done as planned" and weight; a tap sends one sentence
  // to Jarvis (who logs it and answers with a card), weight only fills the box
  let quickTok = 0;
  function staticChips() {
    const out = [];
    if (window.BodyFlags?.on("water")) out.push({ label: tr("喝水 {ml} ml", { ml: 250 }), send: tr("喝了 {ml} 毫升水", { ml: 250 }) });
    out.push({ label: tr("训练按计划完成"), send: tr("今天的训练按计划完成") });
    out.push({ label: tr("体重"), prefill: tr("今天体重 ") });
    return out;
  }
  async function openQuick() {
    const tok = ++quickTok;
    const opts = { title: tr("快记"), placeholder: tr("比如：午饭一碗牛肉面"), chips: staticChips() };
    if (!AI().openHalf(opts)) return;
    let meals = [];
    try { meals = await C.api("/v1/users/me/meal-templates"); } catch (e) { return; }
    if (tok !== quickTok || !Array.isArray(meals) || !meals.length || $("#ai-quicklog")?.hidden !== false) return;
    const top = meals.map((t, i) => [t, i]).sort((a, b) => (b[0].uses_30d || 0) - (a[0].uses_30d || 0) || a[1] - b[1]).slice(0, 3).map(([t]) => t);
    AI().openHalf({ ...opts, chips: [...top.map((t) => ({ label: t.name, send: tr("按快捷模板「{name}」记一次（模板 id {id}）", { name: t.name, id: t.id }) })), ...opts.chips] });
  }

  // ---------------------------------------------------------------- voice: recognisers
  // Both kinds: start() -> Promise (the microphone is open), stop() -> Promise<{text, seconds} | null>, cancel(), level() 0–1,
  // partial() (words so far; "" for the server), lang switch (native only).
  async function postJson(path, body) {
    const r = await fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Body-Web": "1", ...window.I18N.headers() }, body: JSON.stringify(body) });
    let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) throw Object.assign(new Error(d?.detail?.message || (typeof d?.detail === "string" ? d.detail : tr("识别失败（{status}）", { status: r.status }))), { status: r.status });
    return d;
  }
  async function transcribe(wavBlob) {
    const r = await fetch("/v1/assistant/transcribe?fix=1", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "audio/wav", "X-Body-Web": "1", ...window.I18N.headers() }, body: wavBlob });
    let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) throw Object.assign(new Error(d?.detail?.message || tr("识别失败（{status}）", { status: r.status })), { status: r.status });
    return d;
  }
  const fixText = (text) => postJson("/v1/assistant/voice-fix", { text }).then((d) => d.text || text).catch(() => text);

  function serverEngine(rec) {
    rec = rec || window.BodyMedia.recorder();
    let started = null;
    return {
      kind: "server",
      start() { started = rec.start(); return started; },
      level: () => rec.level(),
      partial: () => "",
      elapsed: () => rec.elapsed(),
      async stop() {
        try { await started; } catch (e) { return null; }
        const out = await rec.stop().catch(() => null);
        if (!out) return null;
        const d = await transcribe(out.wav);
        return d.text ? { text: d.text, seconds: out.seconds } : null;
      },
      cancel() { rec.cancel(); started?.catch(() => {}); },
    };
  }

  // The app's on-device recogniser (app 1.0.8+, apps/watch2whoop/lib/src/voice_bridge.dart). Page -> app:
  //   voice:start {session, lang: "zh"|"en", audio}   (audio = keep the 16 kHz recording in memory for the server's final pass)
  //   voice:stop {session} · voice:cancel {session} · voice:lang {session, lang, audio}
  // App -> page: window.Watch2WhoopVoice.event({type, session, ...}): started · level {rms} · partial {text}
  //   · final {text, seconds, wav?} (wav = base64 WAV, only when audio was asked) · error {code: permission | interrupted |
  //   unsupported_lang | unavailable | busy}
  let seq = 0;
  const listeners = new Map();
  window.Watch2WhoopVoice = { event(e) { try { listeners.get(e?.session)?.(e); } catch (err) { /* a bad event never breaks the page */ } } };
  function nativeEngine(lang) {
    const session = `jv${Date.now().toString(36)}${++seq}`;
    let lvl = 0, text = "", t0 = 0, done = null, fail = null, gotStart = null, onError = null;
    const audioFor = (l) => l === "zh";   // Chinese: the server's recogniser finalises (more accurate there); English stays on the phone
    const send = (verb, extra = {}) => tell(`voice:${verb} ${JSON.stringify({ session, ...extra })}`);
    const eng = {
      kind: "native", lang,
      start() {
        return new Promise((ok, no) => {
          gotStart = ok; fail = no;
          listeners.set(session, (e) => {
            if (e.type === "started") { t0 = performance.now(); gotStart?.(); gotStart = null; }
            else if (e.type === "level") lvl = clamp(Number(e.rms) || 0);
            else if (e.type === "partial") text = String(e.text || "");
            else if (e.type === "final") done?.(e);
            else if (e.type === "error") {
              const err = Object.assign(new Error(e.message || e.code), { code: e.code });
              if (gotStart) { gotStart = null; fail?.(err); } else onError?.(err);
            }
          });
          send("start", { lang, audio: audioFor(lang) });
          // no answer at all (an app that lost its recogniser): give up; the first permission prompts fit well inside this
          setTimeout(() => { if (gotStart) { gotStart = null; fail?.(Object.assign(new Error("unavailable"), { code: "unavailable" })); } }, START_WAIT_MS);
        });
      },
      level: () => lvl,
      partial: () => text,
      elapsed: () => (t0 ? (performance.now() - t0) / 1000 : 0),
      onError(fn) { onError = fn; },
      setLang(l) { eng.lang = l; text = ""; send("lang", { lang: l, audio: audioFor(l) }); },
      async stop() {
        const seconds = eng.elapsed();
        const fin = await new Promise((ok) => {
          const t = setTimeout(() => ok(null), FINAL_WAIT_MS);
          done = (e) => { clearTimeout(t); ok(e); };
          send("stop");
        });
        listeners.delete(session);
        const said = (fin?.text ?? text).trim();
        const secs = Number(fin?.seconds) || seconds;
        if (fin?.wav) {   // Chinese: the same recording through the server's recogniser + the user's terms; the phone's text if that fails
          try {
            const bin = atob(fin.wav), buf = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
            const d = await transcribe(new Blob([buf], { type: "audio/wav" }));
            if (d.text) return { text: d.text, seconds: secs };
          } catch (e) { /* the phone's own text below */ }
        }
        if (!said) return null;
        return { text: await fixText(said), seconds: secs };
      },
      cancel() { listeners.delete(session); send("cancel"); },
    };
    return eng;
  }

  // ---------------------------------------------------------------- voice: the overlay and its states
  // off -> hold (finger down) -> [release] final -> sent / review; hold -> [slide up-left] cancel; hold -> [slide up-right] locked;
  // locked -> 发送 / the key again -> final; locked -> 取消 / the dimmed page -> cancel; a system interruption -> cancel.
  const V = { state: "off", anchor: null, dest: "half", pid: null, sx: 0, sy: 0, t0: 0, raf: 0, last: 0, zone: null, hist: [], eng: null, targets: null, why: null };

  function setState(st) {
    V.state = st;
    const vp = $("#jv-vpanel"), direct = directSend();
    for (const s of ["hold", "locked", "final", "review"]) vp.classList.toggle(s, st === s);
    $("#jv-vp-title").textContent = { hold: tr("在听"), locked: tr("已锁定"), final: tr("识别中"), review: tr("确认文字") }[st] || "";
    const hint = $("#jv-vp-hint");
    hint.hidden = st === "final";
    hint.textContent = st === "hold" ? (direct ? tr("松开 发送") : tr("松开 识别"))
      : st === "locked" ? (direct ? tr("说完点「发送」") : tr("说完点「完成」"))
      : st === "review" ? tr("可以改，点「发送」才发给 Jarvis") : "";
    $("#jv-vp-acts").hidden = st !== "locked" && st !== "review";
    $("#jv-vp-cancel").textContent = tr("取消");
    $("#jv-vp-send").innerHTML = `${lu("arrow-up", 2.2)}<span>${esc(st === "locked" && !direct ? tr("完成") : tr("发送"))}</span>`;
    $("#jv-vp-edit").hidden = st !== "review";
    $("#jv-vp-cap").hidden = st === "review";
    $("#jv-vscrim").classList.toggle("tap", st === "locked" || st === "review");
    for (const t of [$("#jv-vt-cancel"), $("#jv-vt-lock")]) t.hidden = st !== "hold";
    $("#jv-wave").hidden = st === "review";
    $("#jv-vp-time").hidden = st === "review";
    syncInert();
  }

  function drawLang() {
    // Only with the app's recogniser: the server's recognises either language by itself
    const b = $("#jv-vp-lang");
    b.hidden = !window.BodyMedia?.nativeVoice();
    b.textContent = langLabel(V.eng?.lang || voiceLang());
    b.setAttribute("aria-label", tr("识别语言：{lang}", { lang: b.textContent }));
  }

  function placePanel() {
    const vp = $("#jv-vpanel"), c = centre(V.anchor), h = window.innerHeight;
    vp.style.top = ""; vp.style.bottom = "";
    if (V.state === "review") return;   // the CSS puts it near the top, clear of the keyboard
    vp.style.bottom = `${Math.max(12, h - (V.state === "hold" ? c.y - 132 - 30 - 46 : c.y - 48))}px`;
  }

  function startVoice(o) {
    if (V.state !== "off") return;
    const lang = voiceLang(), kind = engineFor(lang);
    if (!kind) { o.rec?.cancel(); noVoice(); return; }
    const eng = kind === "native" ? nativeEngine(lang) : serverEngine(o.rec);
    Object.assign(V, { anchor: o.anchor, dest: o.dest, pid: o.pointerId ?? null, t0: performance.now(), last: 0, zone: null,
      hist: new Array(BARS).fill(0), eng, why: null });
    const c = centre(o.anchor), w = window.innerWidth;
    V.sx = o.x ?? c.x; V.sy = o.y ?? c.y;
    const lim = (x) => Math.min(w - 40, Math.max(40, x));
    let cx = lim(c.x - 104);
    const lx = lim(c.x + 104);
    if (lx - cx < 150) cx = lim(lx - 150);
    V.targets = { cancel: { x: cx, y: c.y - 132 }, lock: { x: lx, y: c.y - 132 } };
    const tc = $("#jv-vt-cancel"), tl = $("#jv-vt-lock");
    tc.style.left = `${cx}px`; tc.style.top = `${c.y - 132}px`; tl.style.left = `${lx}px`; tl.style.top = `${c.y - 132}px`;
    tc.style.transform = tl.style.transform = "";
    tc.classList.remove("hot"); tl.classList.remove("hot");
    $(".lbl", tc).textContent = tr("取消"); $(".lbl", tl).textContent = tr("锁定");
    const vp = $("#jv-vpanel");
    vp.setAttribute("aria-label", tr("语音输入"));
    vp.classList.remove("z-cancel", "z-lock");
    $("#jv-vp-cap").textContent = "";
    $("#jv-vp-cap").dataset.empty = kind === "native" ? tr("说吧，我边听边写") : tr("说完再转成文字");
    $("#jv-vp-time").textContent = "0:00";
    const locked = o.mode === "locked";
    setState(locked ? "locked" : "hold");
    placePanel(); drawLang();
    ignoreClickUntil = Infinity;   // the click the browser sends on release must not open the menu
    const scrim = $("#jv-vscrim");
    scrim.hidden = o.dest !== "half"; vp.hidden = false;
    requestAnimationFrame(() => { scrim.classList.add("on"); vp.classList.add("on"); });
    o.anchor.classList.add("rec");
    if (o.anchor === jv) { $("#jv-halo").classList.add("on"); $(".dock").classList.add("jv-dim"); }
    haptic("medium");
    announce(locked ? (directSend() ? tr("已锁定，说完点发送") : tr("已锁定，说完点完成")) : tr("在听。松开发送，往左上取消，往右上锁定"));
    if (locked) setTimeout(() => $("#jv-vp-send").focus({ preventScroll: true }), 60);
    eng.onError?.((err) => { if (V.eng === eng) voiceError(err); });
    eng.start().catch((err) => { if (V.eng === eng) voiceError(err); else if (err?.name === "AbortError" && eng.why === "interrupted") C.toast(tr("好了，再按住说话")); });
    V.raf = requestAnimationFrame(loop);
  }

  function voiceError(err) {
    const code = err?.code || err?.name;
    endVoice();
    if (code === "AbortError") return;
    haptic("warning");
    if (code === "permission" || window.BodyMedia.micBlockedInApp(err)) {
      C.toast(tr("麦克风或语音识别没打开：在系统设置里允许 Watch2Whoop"), tr("打开设置"), () => window.BodyMedia.openMicSettings(), 6000);
    } else if (code === "interrupted") C.toast(tr("被系统打断，没有发送"));
    else if (code === "NotAllowedError" || code === "NotFoundError" || code === "NotReadableError") C.toast(tr("麦克风不可用：在系统设置里允许，或用键盘上的 🎤 听写"));
    else C.toast(tr("语音识别暂时不可用，用键盘上的 🎤 听写"));
  }

  function loop(now) {
    if (V.state !== "hold" && V.state !== "locked") return;
    if (now - V.last > 50) {
      V.last = now;
      const lv = V.eng.level();
      V.hist.push(lv); V.hist.shift();
      const bars = $$("#jv-wave i");
      for (let i = 0; i < BARS; i++) bars[i].style.transform = `scaleY(${(0.06 + V.hist[i] * 0.94).toFixed(3)})`;
      if (V.anchor === jv && !reduced()) $("#jv-halo").style.transform = `scale(${(1 + lv * 0.45).toFixed(3)})`;
      const said = V.eng.partial();
      if (said) {   // the last few characters may still change: grey
        const tail = /[一-鿿]/.test(said) ? 2 : 6, cut = Math.max(0, said.length - tail);   // i18n-ok (Chinese characters?)
        const cap = $("#jv-vp-cap");
        cap.innerHTML = `${esc(said.slice(0, cut))}<span class="pend">${esc(said.slice(cut))}</span>`;
        cap.scrollTop = cap.scrollHeight;
      }
      const secs = (now - V.t0) / 1000;
      $("#jv-vp-time").textContent = mmss(secs);
      if (secs >= window.BodyMedia.MAX_SECONDS) { finishVoice(); return; }
    }
    V.raf = requestAnimationFrame(loop);
  }

  function zoneAt(x, y) {
    const T = V.targets, tc = $("#jv-vt-cancel"), tl = $("#jv-vt-lock");
    const dC = Math.hypot(x - T.cancel.x, y - T.cancel.y), dL = Math.hypot(x - T.lock.x, y - T.lock.y);
    const up = V.sy - y, mid = (T.cancel.x + T.lock.x) / 2;
    if (!reduced()) {
      tc.style.transform = `scale(${(1 + 0.18 * clamp(1 - dC / 170)).toFixed(3)})`;
      tl.style.transform = `scale(${(1 + 0.18 * clamp(1 - dL / 170)).toFixed(3)})`;
    }
    if (dC < HIT_PX || (up > UP_PX && x < mid && dC < NEAR_PX)) return "cancel";
    if (dL < HIT_PX || (up > UP_PX && x >= mid && dL < NEAR_PX)) return "lock";
    return null;
  }

  function bindVoice() {
    document.addEventListener("pointermove", (e) => {
      if (V.state !== "hold" || e.pointerId !== V.pid) return;
      const z = zoneAt(e.clientX, e.clientY);
      if (z === V.zone) return;
      V.zone = z;
      $("#jv-vt-cancel").classList.toggle("hot", z === "cancel"); $("#jv-vt-lock").classList.toggle("hot", z === "lock");
      const vp = $("#jv-vpanel");
      vp.classList.toggle("z-cancel", z === "cancel"); vp.classList.toggle("z-lock", z === "lock");
      $("#jv-vp-hint").textContent = z === "cancel" ? tr("松开 取消") : z === "lock" ? tr("松开 锁定，可以松手接着说") : (directSend() ? tr("松开 发送") : tr("松开 识别"));
      if (z) haptic("selection");
    });
    document.addEventListener("pointerup", (e) => {
      if (V.state !== "hold" || e.pointerId !== V.pid) return;
      if (V.zone === "cancel") cancelVoice(tr("已取消，没有发送"));
      else if (V.zone === "lock") { haptic("selection"); setState("locked"); placePanel(); announce(directSend() ? tr("已锁定，说完点发送") : tr("已锁定，说完点完成")); }
      else finishVoice();
    });
    // e.g. a call, Siri, or iOS' first microphone prompt taking the touch
    document.addEventListener("pointercancel", (e) => { if (V.state === "hold" && e.pointerId === V.pid) cancelVoice(tr("被系统打断，没有发送"), "interrupted"); });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && (V.state === "hold" || V.state === "locked")) cancelVoice(tr("被系统打断，没有发送"), "interrupted"); });
    $("#jv-vp-cancel").addEventListener("click", () => cancelVoice(tr("已取消，没有发送")));
    $("#jv-vp-send").addEventListener("click", () => (V.state === "review" ? sendReviewed() : finishVoice()));
    $("#jv-vscrim").addEventListener("click", () => { if (V.state === "locked" || V.state === "review") cancelVoice(tr("已取消，没有发送")); });
    // Switching the recognition language (locked only: a holding finger cannot tap): recognition starts again in that language
    $("#jv-vp-lang").addEventListener("click", () => {
      const next = (V.eng?.lang || voiceLang()) === "zh" ? "en" : "zh";
      setVoiceLang(next);
      if (V.state === "locked" && V.eng?.kind === "native" && engineFor(next) === "native") {
        V.eng.setLang(next); $("#jv-vp-cap").textContent = "";
      }
      drawLang(); haptic("selection");
      announce(tr("识别语言：{lang}", { lang: langLabel(next) }));
    });
  }

  function endVoice() {
    cancelAnimationFrame(V.raf);
    V.anchor?.classList.remove("rec");
    $("#jv-halo").classList.remove("on"); $("#jv-halo").style.transform = "";
    if (!M.open) $(".dock").classList.remove("jv-dim");
    V.state = "off"; V.eng = null;
    const vp = $("#jv-vpanel"), scrim = $("#jv-vscrim");
    vp.classList.remove("on", "hold", "locked", "final", "review", "z-cancel", "z-lock");
    scrim.classList.remove("on", "tap");
    for (const t of [$("#jv-vt-cancel"), $("#jv-vt-lock")]) t.hidden = true;
    setTimeout(() => { if (V.state === "off") { vp.hidden = true; scrim.hidden = true; } }, reduced() ? 0 : 200);
    ignoreClickUntil = performance.now() + CLICK_GUARD_MS;
    syncInert();
  }

  function cancelVoice(msg, why) {
    if (V.state === "off") return;
    const eng = V.eng;
    if (eng) { eng.why = why || "cancel"; eng.cancel(); }
    endVoice();
    haptic("warning"); C.toast(msg); announce(msg);
  }

  async function finishVoice() {
    if (V.state !== "hold" && V.state !== "locked") return;
    const eng = V.eng, secs = (performance.now() - V.t0) / 1000;
    if (V.state === "hold" && secs < MIN_S) { cancelVoice(tr("按住说话，说完再松开")); return; }
    cancelAnimationFrame(V.raf);
    setState("final"); placePanel();
    V.anchor.classList.remove("rec");   // the microphone is off from here on
    $("#jv-halo").classList.remove("on");
    const said = eng.partial();
    if (said) $("#jv-vp-cap").textContent = said;
    let out = null;
    try { out = await eng.stop(); } catch (e) {
      if (V.eng !== eng) return;
      endVoice(); haptic("warning"); C.toast(e.message || tr("识别失败（{status}）", { status: e.status || "—" }));
      return;
    }
    if (V.eng !== eng) return;   // cancelled meanwhile
    if (!out?.text) { endVoice(); haptic("warning"); C.toast(tr("按住说话，说完再松开")); return; }
    if (directSend()) { endVoice(); deliver(out); return; }
    // Until the consent says otherwise, the text is shown first: it can be changed, and 发送 sends it
    V.result = out;
    setState("review"); placePanel();
    const ed = $("#jv-vp-edit");
    ed.value = out.text;
    announce(tr("识别出：{text}。可以改，点发送才发给 Jarvis", { text: out.text }));
    setTimeout(() => $("#jv-vp-send").focus({ preventScroll: true }), 60);
  }

  function sendReviewed() {
    const text = $("#jv-vp-edit").value.trim(), out = V.result;
    V.result = null;
    endVoice();
    if (!text) { C.toast(tr("已取消，没有发送")); return; }
    deliver({ ...out, text });
  }

  function deliver(v) {
    if (V.dest === "half" && !AI().openHalf({})) return;
    if (AI().sendVoice(v)) { haptic("success"); announce(tr("已发送：{text}", { text: v.text })); }
  }

  // ---------------------------------------------------------------- the chat's own mic button (ai.js calls bindMic)
  function bindMic(btn) {
    if (!btn || btn._jv) return;
    btn._jv = true;
    btn.addEventListener("pointerdown", (e) => {
      if (!window.BodyJarvis.ownsMic()) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      if (V.state === "locked" && V.anchor === btn) { finishVoice(); return; }
      if (V.state !== "off" || AI().busy()) { if (AI().busy()) C.toast(tr("等 Jarvis 回答完再说")); return; }
      try { btn.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
      const rec = engineFor(voiceLang()) === "server" ? window.BodyMedia.recorder() : null;
      rec?.prime();
      startVoice({ anchor: btn, mode: "hold", x: e.clientX, y: e.clientY, pointerId: e.pointerId, dest: "panel", rec });
    });
    btn.addEventListener("click", (e) => {   // keyboard / VoiceOver cannot hold: straight into the locked state
      if (!window.BodyJarvis.ownsMic() || e.detail !== 0 || V.state !== "off") return;
      startVoice({ anchor: btn, mode: "locked", dest: "panel" });
    });
  }

  window.BodyJarvis = {
    ownsMic: () => !!(jv && isJarvis() && engineFor(voiceLang())),
    bindMic,
    // for tests and screenshots: the state of the overlay
    state: () => V.state,
  };

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx, me) {
      C = ctx; meId = me?.me?.id ?? null;
      if (!window.BodyFlags?.on(FLAG)) return;
      mount();
      sync();
      const mic = $("#ai-mic");
      if (mic) bindMic(mic);
    },
  });
})();
