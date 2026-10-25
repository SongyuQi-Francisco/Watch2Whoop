/* Jarvis dial: four opening-animation prototypes (monochrome, flag jarvis_dial). A throwaway page for choosing, not the product code:
   ?v=1 trace-and-light, 2 gauge needle, 3 concentric rings + scan, 4 combined start-up sequence. One ink (currentColor = the page's --text),
   layers by opacity only. Animated: transform, opacity and SVG stroke-dashoffset (Web Animations). Open ≤ 280 ms (v4 ≤ 450 ms), close = the
   same animations played backwards, faster. Lucide icons (ISC). Reduced motion: the final state at once, nothing turns. */
(() => {
  "use strict";
  const LU = {
    orbit: '<path d="M20.341 6.484A10 10 0 0 1 10.266 21.85"/><path d="M3.659 17.516A10 10 0 0 1 13.74 2.152"/><circle cx="12" cy="12" r="3"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/>',
    "bot-message-square": '<path d="M12 6V2H8"/><path d="M15 11v2"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M20 16a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/><path d="M9 11v2"/>',
    "audio-waveform": '<path d="M2 13a2 2 0 0 0 2-2V7a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0V4a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0v-4a2 2 0 0 1 2-2"/>',
    "scan-eye": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="1"/><path d="M18.944 12.33a1 1 0 0 0 0-.66 7.5 7.5 0 0 0-13.888 0 1 1 0 0 0 0 .66 7.5 7.5 0 0 0 13.888 0"/>',
    zap: '<path d="M15.914 4a1.5 1.5 0 00-2.474-1.561l-9 9A1.5 1.5 0 005.5 14h4.002a.5.5 0 01.471.666L8.086 20a1.5 1.5 0 002.475 1.56l9-9A1.5 1.5 0 0018.5 10h-3.997a.5.5 0 01-.472-.667z"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    mic: '<path d="M12 19v3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><rect x="9" y="2" width="6" height="13" rx="3"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  };
  const lu = (n, sw = 1.75) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${LU[n]}</svg>`;
  const NS = "http://www.w3.org/2000/svg", R = 120, DY = 22, EASE = "cubic-bezier(.2,.8,.2,1)";
  const KEYS = [
    { id: "chat", th: -72, icon: "bot-message-square", label: "对话" }, { id: "voice", th: -36, icon: "audio-waveform", label: "语音" },
    { id: "photo", th: 36, icon: "scan-eye", label: "拍照" }, { id: "quick", th: 72, icon: "zap", label: "快记" },
  ];
  const rad = (d) => d * Math.PI / 180;
  const pt = (deg, r, oy = DY) => ({ x: Math.sin(rad(deg)) * r, y: oy - Math.cos(rad(deg)) * r });
  const f = (n) => n.toFixed(1);
  const V = +(new URLSearchParams(location.search).get("v") || 1);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s) => document.querySelector(s);
  const jv = $("#ask"), svg = $("#vx"), fan = $("#fan");
  const kr = jv.getBoundingClientRect(), K = { x: kr.left + kr.width / 2, y: kr.top + kr.height / 2 };
  let A = [], open = false, g = null, keyEls = [], rotors = [];

  const S = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); (parent || g).appendChild(e); return e; };
  const an = (el, kf, o = {}) => { const a = el.animate(kf, { fill: "both", easing: EASE, ...o }); A.push(a); return a; };
  // time at which an eased 0..1 progress reaches p (to light things as a sweep passes them)
  const bez = (x1, y1, x2, y2) => (p) => { const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx, cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    let lo = 0, hi = 1; for (let i = 0; i < 30; i++) { const m = (lo + hi) / 2; if (((ay * m + by) * m + cy) * m < p) lo = m; else hi = m; } return ((ax * lo + bx) * lo + cx) * lo; };
  const tAt = bez(.2, .8, .2, 1);
  const arcD = (deg) => { const a = pt(0, R), b = pt(deg, R); return `M${f(a.x)} ${f(a.y)}A${R} ${R} 0 0 ${deg > 0 ? 1 : 0} ${f(b.x)} ${f(b.y)}`; };
  const draw = (el, delay, dur, easing = "linear") => { el.setAttribute("pathLength", "1"); el.style.strokeDasharray = "1 2"; return an(el, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { delay, duration: dur, easing }); };
  const tick = (a, r0, r1, parent) => { const p = pt(a, r0), q = pt(a, r1); return S("line", { x1: f(p.x), y1: f(p.y), x2: f(q.x), y2: f(q.y) }, parent); };
  const lineStyle = (e, w = 1.25, op = 1) => { e.setAttribute("stroke", "currentColor"); e.setAttribute("stroke-width", w); e.setAttribute("fill", "none"); e.setAttribute("stroke-linecap", "round"); e.style.opacity = op; return e; };
  const ANG = []; for (let a = 12; a <= 84; a += 12) ANG.push(-a, a);

  function reset() {
    A.forEach((a) => a.cancel()); A = []; rotors = [];
    svg.replaceChildren(); g = S("g", { transform: `translate(${f(K.x)} ${f(K.y)})` }, svg);
    fan.replaceChildren();
    keyEls = KEYS.map((k) => { const p = pt(k.th, R), b = document.createElement("button"); b.className = "jv-key"; b.type = "button";
      b.style.left = `${K.x + p.x}px`; b.style.top = `${K.y + p.y}px`;
      b.innerHTML = `<span class="jv-kbase"></span>${lu(k.icon)}<span class="lbl">${k.label}</span>`; fan.appendChild(b); return b; });
  }
  // a key coming up: from a little behind its place on the arc, small to full
  const keyPop = (i, delay, dur, back = 12, lbl = true) => {
    const k = KEYS[i], from = pt(k.th - Math.sign(k.th) * back, R), to = pt(k.th, R);
    an(keyEls[i], [{ opacity: 0, transform: `translate(${f(from.x - to.x)}px, ${f(from.y - to.y)}px) scale(.6)` }, { opacity: 1, transform: "none" }], { delay, duration: dur });
    const l = keyEls[i].querySelector(".lbl"); if (lbl) an(l, [{ opacity: 0 }, { opacity: 1 }], { delay: delay + dur * .4, duration: dur * .8 });
  };
  const wedge = (a0, a1, r0, r1) => { const P = (a, r) => { const p = pt(a, r, 0); return `${f(p.x)} ${f(p.y)}`; };
    return `M${P(a0, r0)}L${P(a0, r1)}A${r1} ${r1} 0 0 1 ${P(a1, r1)}L${P(a1, r0)}A${r0} ${r0} 0 0 0 ${P(a0, r0)}Z`; };
  const scanGroup = () => { const sw = S("g", {}); sw.style.transform = `translate(0px, ${DY}px) rotate(-84deg)`;
    [[-26, -12, .035], [-12, -4, .06], [-4, 0, .10]].forEach(([a0, a1, o]) => { const w = S("path", { d: wedge(a0, a1, 30, R + 14), fill: "currentColor" }, sw); w.style.opacity = o; });
    const e = lineStyle(S("line", { x1: 0, y1: -30, x2: 0, y2: -(R + 14) }, sw), 1.25, .75); return sw; };
  const rot = (el, dur, dir) => { if (reduced) return; const a = el.animate([{ transform: "rotate(0deg)" }, { transform: `rotate(${360 * dir}deg)` }], { duration: dur, iterations: Infinity }); rotors.push(a); };

  // ---------- 1: trace and light
  function v1() {
    const stem = lineStyle(S("line", { x1: 0, y1: -30, x2: 0, y2: f(pt(0, R).y) })); draw(stem, 0, 50);
    for (const d of [84, -84]) {
      const gl = lineStyle(S("path", { d: arcD(d) }), 6, .1); draw(gl, 40, 150);
      draw(lineStyle(S("path", { d: arcD(d) }), 1.25, .9), 40, 150);
    }
    const reach = (a) => 40 + 150 * Math.abs(a) / 84;
    for (const a of [0, ...ANG]) { const major = Math.abs(a) === 36 || Math.abs(a) === 72, e = lineStyle(tick(a, R - (major ? 9 : 5), R + (major ? 9 : 5)), major ? 1.5 : 1);
      an(e, [{ opacity: 0 }, { opacity: 1, offset: .35 }, { opacity: major ? .85 : .4 }], { delay: reach(a) - 10, duration: 80 }); }
    KEYS.forEach((k, i) => keyPop(i, reach(k.th), 80, 0));
  }

  // ---------- 2: gauge needle
  function v2() {
    const dial = S("g", {}); an(dial, [{ opacity: 0 }, { opacity: 1 }], { duration: 90 });
    const base = lineStyle(S("path", { d: `M${f(pt(-88, R).x)} ${f(pt(-88, R).y)}A${R} ${R} 0 0 1 ${f(pt(88, R).x)} ${f(pt(88, R).y)}` }, dial), 1, .4);
    const fine = lineStyle(S("path", { d: base.getAttribute("d") }, dial), 8, .3); fine.style.strokeDasharray = "0.9 4.1"; fine.setAttribute("stroke-linecap", "butt");
    for (const a of [0, ...ANG]) lineStyle(tick(a, R - 10, R + 6, dial), 1.25, .8);
    [[-72, "00"], [-36, "25"], [0, "50"], [36, "75"], [72, "99"]].forEach(([a, t]) => { const p = pt(a, R + 17), e = S("text", { x: f(p.x), y: f(p.y + 2.5), "text-anchor": "middle", "font-size": 7, fill: "currentColor", "font-family": "ui-monospace, Menlo, monospace" }, dial); e.textContent = t; e.style.opacity = .55; });
    for (const s of [-1, 1]) { const n = S("g", {}), T = (d, o) => `translate(0px, ${DY}px) rotate(${d}deg)`; n.style.transform = T(0);
      lineStyle(S("line", { x1: 0, y1: -46, x2: 0, y2: -(R - 12) }, n), 1.25, .95);
      an(n, [{ transform: T(0), opacity: 1 }, { transform: T(s * 78), opacity: 1, offset: .8 }, { transform: T(s * 78), opacity: 0 }], { delay: 30, duration: 250 }); }
    KEYS.forEach((k, i) => keyPop(i, 30 + 250 * tAt(.8 * Math.abs(k.th) / 78) - 15, 70, 6));
  }

  // ---------- 3: concentric rings + scan
  function v3() {
    [[38, "none", .6, 0, 0], [46, "2 4", .45, 20, -1], [55, "10 3 1 3", .35, 40, 1]].forEach(([r, dash, op, delay, dir]) => {
      const out = S("g", {}), c = lineStyle(S("circle", { cx: 0, cy: 0, r }, out), 1, op); if (dash !== "none") c.style.strokeDasharray = dash;
      an(out, [{ opacity: 0, transform: "scale(.82)" }, { opacity: 1, transform: "scale(1)" }], { delay, duration: 120 }); if (dir) rot(c, 70000, dir); });
    const guide = lineStyle(S("path", { d: `M${f(pt(-88, R).x)} ${f(pt(-88, R).y)}A${R} ${R} 0 0 1 ${f(pt(88, R).x)} ${f(pt(88, R).y)}` }), 1, .22); an(guide, [{ opacity: 0 }, { opacity: .22 }], { delay: 40, duration: 120 });
    const sw = scanGroup();
    const SA = (d, o) => ({ transform: `translate(0px, ${DY}px) rotate(${d}deg)`, ...o });
    an(sw, [SA(-84, { opacity: 0 }), SA(-84, { opacity: 1, offset: .04 }), SA(84, { opacity: 1, offset: .8 }), SA(84, { opacity: 0 })], { delay: 20, duration: 250, easing: "cubic-bezier(.4,0,.2,1)" });
    const sweepT = bez(.4, 0, .2, 1);
    KEYS.forEach((k, i) => keyPop(i, 20 + 250 * sweepT(.04 + .76 * (k.th + 84) / 168) - 20, 90, 14));
  }

  // ---------- 4: combined start-up sequence (≤ 450 ms)
  function v4() {
    document.body.classList.add("v4");
    // 0–80: the key's ring squeezes and springs back, the centre flashes inverted
    const pr = lineStyle(S("circle", { cx: 0, cy: 0, r: 34 }), 1.25, .9);
    an(pr, [{ transform: "scale(1)", opacity: .9 }, { transform: "scale(.86)", opacity: 1, offset: .45 }, { transform: "scale(1)", opacity: .35 }], { duration: 80 });
    const fl = S("circle", { cx: 0, cy: 0, r: 28, fill: "currentColor" }); an(fl, [{ opacity: 0 }, { opacity: .9, offset: .4 }, { opacity: 0 }], { duration: 80 });
    // 40–200: three fine rings spread outward, each turning a little the other way
    [[40, 40, 1], [50, 70, -1], [61, 100, 1]].forEach(([r, delay, dir], i) => { const out = S("g", {}), mid = S("g", {}, out), c = lineStyle(S("circle", { cx: 0, cy: 0, r }, mid), 1, 1); c.style.strokeDasharray = i === 0 ? "none" : i === 1 ? "2 4" : "10 3 1 3";
      an(out, [{ opacity: 0, transform: "scale(.6)" }, { opacity: i === 0 ? .5 : .4, transform: "scale(1)" }], { delay, duration: 120 });
      an(mid, [{ transform: "rotate(0deg)" }, { transform: `rotate(${dir * 28}deg)` }], { delay, duration: 120 });
      if (i === 2) rot(c, 80000, -1); });
    // 80–260: the arc is traced from the centre, ticks light in turn, tiny readouts roll
    for (const d of [84, -84]) { draw(lineStyle(S("path", { d: arcD(d) }), 1.25, .9), 90, 150); }
    const gl = lineStyle(S("path", { d: `M${f(pt(-84, R).x)} ${f(pt(-84, R).y)}A${R} ${R} 0 0 1 ${f(pt(84, R).x)} ${f(pt(84, R).y)}` }), 7, 0);
    an(gl, [{ opacity: 0 }, { opacity: .14, offset: .4 }, { opacity: 0 }], { delay: 330, duration: 120, easing: "ease-in-out" });   // 300–450: one breath, then still
    const reach = (a) => 90 + 150 * Math.abs(a) / 84;
    for (const a of [0, ...ANG]) { const major = Math.abs(a) === 36 || Math.abs(a) === 72, e = lineStyle(tick(a, R - (major ? 9 : 5), R + (major ? 9 : 5)), major ? 1.5 : 1);
      an(e, [{ opacity: 0 }, { opacity: 1, offset: .35 }, { opacity: major ? .85 : .4 }], { delay: reach(a) - 10, duration: 70 }); }
    [[-72, "00"], [-36, "25"], [36, "75"], [72, "99"]].forEach(([a, t]) => { const p = pt(a, R + 19), e = S("text", { x: f(p.x), y: f(p.y + 2.5), "text-anchor": "middle", "font-size": 7, fill: "currentColor", "font-family": "ui-monospace, Menlo, monospace" }); e.textContent = t;
      an(e, [{ opacity: 0 }, { opacity: .6 }], { delay: reach(a), duration: 80 }); });
    // the readout: a column of digits rolling past a small window above the arc's centre
    const rd = S("g", {}); rd.setAttribute("transform", `translate(0 ${f(pt(0, R - 38).y)})`);
    const clip = S("clipPath", { id: "rdclip" }, rd); S("rect", { x: -12, y: -6, width: 24, height: 8 }, clip);
    const win = S("g", { "clip-path": "url(#rdclip)" }, rd), col = S("g", {}, win);
    for (let i = 0; i < 9; i++) { const t = S("text", { x: 0, y: i * 8, "text-anchor": "middle", "font-size": 7, fill: "currentColor", "font-family": "ui-monospace, Menlo, monospace" }, col); t.textContent = String(i * 9 + 9).padStart(2, "0"); }
    an(col, [{ transform: "translateY(0px)" }, { transform: "translateY(-64px)" }], { delay: 110, duration: 150, easing: "steps(8, end)" });
    an(rd, [{ opacity: 0 }, { opacity: .7 }], { delay: 100, duration: 60 });
    // 160–360: a scan sweeps out of the middle; keys light and grow in turn, 36 ms apart
    const sw = scanGroup(), HIT = [190, 226, 262, 298], ANGS = [-72, -36, 36, 72];
    const SA = (d, o) => ({ transform: `translate(0px, ${DY}px) rotate(${d}deg)`, ...o });
    an(sw, [SA(-84, { opacity: 0, offset: 0 }), SA(-84, { opacity: 1, offset: .02 }), ...ANGS.map((a, i) => SA(a, { opacity: 1, offset: (HIT[i] - 160) / 210 })),
      SA(84, { opacity: 1, offset: .86 }), SA(84, { opacity: 0, offset: 1 })], { delay: 160, duration: 210, easing: "linear" });
    // the keys come up as the scan passes (left keys: outer first, as the scan comes from the left), outline closes 300–450
    const order = [0, 1, 2, 3];
    order.forEach((i, n) => {
      keyPop(i, HIT[n] - 10, 90, 10);
      const k = KEYS[i], p = pt(k.th, R), ol = lineStyle(S("circle", { cx: f(p.x), cy: f(p.y), r: 23 }), 1.5, 1); ol.setAttribute("transform", `rotate(-90 ${f(p.x)} ${f(p.y)})`);
      draw(ol, 300 + n * 12, 90, EASE);
      const fo = lineStyle(S("circle", { cx: f(p.x), cy: f(p.y), r: 27 }), 1, 0); an(fo, [{ opacity: 0 }, { opacity: .9, offset: .25 }, { opacity: 0 }], { delay: HIT[n] - 10, duration: 110 });
    });
  }

  function openMenu() {
    if (open) return; open = true; reset(); jv.classList.add("open"); document.body.classList.toggle("v4", V === 4);
    ({ 1: v1, 2: v2, 3: v3, 4: v4 })[V](); if (reduced) A.forEach((a) => a.finish());
    $("#scrim").hidden = false; an($("#scrim"), [{ opacity: 0 }, { opacity: 1 }], { duration: 150 });
  }
  function closeMenu() {
    if (!open) return; open = false; jv.classList.remove("open");
    const total = Math.max(...A.map((a) => { const t = a.effect.getComputedTiming(); return t.endTime; }).filter(isFinite)), len = V === 4 ? 200 : 150;
    rotors.forEach((a) => a.cancel());
    A.forEach((a) => { a.updatePlaybackRate(-(total / len)); a.play(); });
    setTimeout(() => { if (!open) { svg.replaceChildren(); fan.replaceChildren(); $("#scrim").hidden = true; } }, len + 40);
  }
  jv.addEventListener("pointerdown", () => jv.classList.add("pressed"));
  jv.addEventListener("pointerup", () => { jv.classList.remove("pressed"); open ? closeMenu() : openMenu(); });
  jv.addEventListener("pointercancel", () => jv.classList.remove("pressed"));
  document.addEventListener("visibilitychange", () => rotors.forEach((a) => (document.hidden ? a.pause() : a.play())));
  window.__closeMenu = closeMenu;
})();
