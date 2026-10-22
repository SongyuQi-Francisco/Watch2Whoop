/* Sleep detail page (rollout flag sleep_detail; GET /v1/users/{u}/sleep/night?date= from sleep_night/ on the server):
   one night in full — bedtime / wake time, time asleep, in bed, efficiency; stage timeline + composition; the sleep score and
   its parts; this night against the owner's own 7 / 30-night averages; overnight vitals; rule-based notes and a button that
   hands the night to the viewer's own AI (Jarvis) with a prefilled question. Nothing here calls an AI by itself.
   Numbers, charts and short labels only: the notes are the one place with sentences, longer explanations open in the
   explain sheets. app.js draws placeholder() as a sub-page of 今天 while a night is open and calls mount(el, ctx).
   ctx: { api, owner, date, hypno(main, stages), fmtDay(date), canAsk(), askAI(prompt), explain(key, title), back(), onDate(date) }.
   Swipe left / right (or ‹ ›) to the next / previous night with data. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const num = (v, d = 0) => (v == null ? "—" : window.I18N.num(v, d));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const hm = (m) => (m == null ? "—" : `${Math.floor(m / 60)}:${String(Math.round(m % 60)).padStart(2, "0")}`);
  const CHEV = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const SPARK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  const STAGE = () => ({ deep: tr("深睡"), core: tr("核心"), rem: "REM", unspecified: tr("未分阶段"), awake: tr("清醒") });
  const PART = () => ({ duration: tr("时长"), stages: tr("睡眠阶段"), consistency: tr("规律性"), interruptions: tr("中途醒来") });
  // vitals: label, digits, which direction is worse (−1 lower, 1 higher, 0 either way), threshold for colouring the delta
  const VITAL = () => ({ hrv: ["HRV", 0, -1, 5], rhr: [tr("最低心率"), 0, 1, 3], resp_rate: [tr("呼吸"), 1, 1, 1],
    spo2: ["SpO₂", 1, -1, 1.5], wrist_temp: [tr("腕温"), 2, 0, 0.4] });
  const UNIT = { ms: "ms", bpm: "bpm", rpm: tr("次/分"), "%": "%", "°C": "°C" };
  const tone = (s) => (s == null ? "plain" : s >= 80 ? "good" : s >= 60 ? "mid" : "bad");

  let C = null, box = null, day = null, busy = false;
  const cache = new Map();

  // ---------------------------------------------------------------- charts
  function compositionBar(comp) {
    const total = comp.reduce((s, c) => s + (c.min || 0), 0);
    if (!total) return "";
    const W = 320, H = 10;
    let x = 0, g = "";
    for (const c of comp) {
      if (!c.min) continue;
      const w = (c.min / total) * W;
      g += `<rect class="hy-${c.stage}" x="${x.toFixed(1)}" y="0" width="${Math.max(0.5, w - 1).toFixed(1)}" height="${H}"><title>${esc(STAGE()[c.stage])} ${num(c.pct)}%</title></rect>`;
      x += w;
    }
    return `<svg class="sd-comp" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(tr("阶段占比"))}"><clipPath id="sd-clip"><rect width="${W}" height="${H}" rx="5"/></clipPath><g clip-path="url(#sd-clip)">${g}</g></svg>`;
  }

  // ---------------------------------------------------------------- blocks
  function nav(d) {
    return `<div class="sub-head sd-head"><button class="sub-back" id="sd-back" aria-label="${esc(tr("返回今天"))}">${CHEV}<span>${esc(tr("tab.today"))}</span></button><h2>${esc(tr("睡眠"))}</h2></div>
      <div class="sd-nav">
        <button class="sd-step prev" id="sd-prev" aria-label="${esc(tr("前一晚"))}" ${d?.prev ? "" : "disabled"}>${CHEV}</button>
        <div class="sd-day">${esc(C.fmtDay(d?.date || day))}</div>
        <button class="sd-step" id="sd-next" aria-label="${esc(tr("后一晚"))}" ${d?.next ? "" : "disabled"}>${CHEV}</button></div>`;
  }

  function heroHtml(m) {
    const cell = (label, v, key) => `<button class="sd-cell" type="button" ${key ? `data-xp="${key}"` : "disabled"}><span class="sub-l">${esc(label)}</span><span class="sd-cv">${v}</span></button>`;
    return `<div class="card">
      <div class="sleep-head"><div><div class="sub-l">${esc(tr("入睡 → 醒来"))}</div><div class="sleep-times">${esc(m.start)}<span class="arrow">→</span>${esc(m.end)}</div></div>
        <div class="sleep-total"><div class="sub-l">${esc(tr("睡着"))}</div><div class="v">${hm(m.asleep_min)}</div></div></div>
      <div class="sd-cells">${cell(tr("在床"), hm(m.in_bed_min))}${cell(tr("效率"), m.efficiency_pct != null ? `${num(m.efficiency_pct)}<small>%</small>` : "—", "sleep_efficiency")}
        ${cell(tr("入睡用时"), m.latency_min != null ? `${num(m.latency_min)}<small>${esc(tr("分"))}</small>` : "—")}${cell(tr("夜醒"), m.awakenings != null ? `${num(m.awakenings)}<small>${esc(tr("次"))}</small>` : "—")}</div></div>`;
  }

  function stagesHtml(m) {
    const S = STAGE();
    const chart = m.stages?.length ? C.hypno(m, m.stages) : `<div class="rcard-tags" style="margin-top:12px"><span class="badge plain">${esc(tr("无阶段时间轴"))}</span></div>`;
    const cells = m.composition.map((c) => `<div><div class="stage-l"><i class="k-${c.stage === "unspecified" ? "core" : c.stage}"></i>${esc(S[c.stage])}</div>
      <div class="stage-v">${hm(c.min)}</div><div class="sd-pct">${num(c.pct)}%</div></div>`).join("");
    return `<div class="section"><div class="section-head"><h2>${esc(tr("睡眠阶段"))}</h2></div><div class="card">
      ${chart}${m.staged ? compositionBar(m.composition) : ""}<div class="stages4">${cells}</div></div></div>`;
  }

  function scoreHtml(s) {
    if (!s) return "";
    const P = PART();
    const rows = s.parts.map((p) => `<div class="sd-part"><span class="sd-pl">${esc(P[p.key])} <small>${num(p.weight * 100)}%</small></span>
      <span class="sd-bar"><span class="${tone(p.score)}" style="width:${Math.max(0, Math.min(100, p.score || 0))}%"></span></span><span class="sd-pv">${num(p.score)}</span></div>`).join("");
    const kv = (k, v) => (v == null ? "" : `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`);
    return `<div class="section"><div class="section-head"><h2>${esc(tr("睡眠分"))}</h2></div>
      <button class="card" type="button" data-xp="sleep_score"><div class="sd-score"><span class="sd-big ${tone(s.value)}">${num(s.value)}</span><div class="sd-parts">${rows}</div></div>
      <div class="sd-kvs">${kv(tr("需求"), s.need_min != null ? hm(s.need_min) : null)}${kv(tr("实际"), s.actual_min != null ? hm(s.actual_min) : null)}
        ${kv(tr("表现"), s.performance_pct != null ? `${num(s.performance_pct)}%` : null)}${kv(tr("睡眠债"), s.debt_min != null ? hm(s.debt_min) : null)}</div></button></div>`;
  }

  function baselineHtml(d) {
    const m = d.main, b = d.baseline || {}, a7 = b.avg7 || {}, a30 = b.avg30 || {};
    const pct = (v) => (v == null ? "—" : `${num(v)}%`), t = (v) => esc(v || "—");
    const rows = [
      [tr("睡着"), hm(m.asleep_min), hm(a7.asleep_min), hm(a30.asleep_min)],
      [tr("入睡"), t(m.start), t(a7.bedtime), t(a30.bedtime)],
      [tr("醒来"), t(m.end), t(a7.wake), t(a30.wake)],
      [tr("效率"), pct(m.efficiency_pct), pct(a7.efficiency_pct), pct(a30.efficiency_pct)],
      ...(m.staged ? [[tr("深睡"), hm(m.deep_min), hm(a7.deep_min), hm(a30.deep_min)], ["REM", hm(m.rem_min), hm(a7.rem_min), hm(a30.rem_min)]] : []),
      ...(d.score ? [[tr("睡眠分"), num(d.score.value), num(a7.sleep_score), num(a30.sleep_score)]] : []),
    ];
    const reg = (v) => (v == null ? "—" : `${num(v)}%`);
    return `<div class="section"><div class="section-head"><h2>${esc(tr("和平时比"))}</h2></div><div class="card">
      <div class="sd-table"><span></span><b>${esc(tr("这晚"))}</b><b>${esc(tr("7 晚均"))}</b><b>${esc(tr("30 晚均"))}</b>
        ${rows.map((r) => `<span>${esc(r[0])}</span><span class="v">${r[1]}</span><span>${r[2]}</span><span>${r[3]}</span>`).join("")}</div>
      <button class="kv kv-btn" type="button" data-xp="sleep_consistency"><span class="k">${esc(tr("规律性"))}</span><span class="v">${reg(b.regularity_pct_7d)} <small class="muted">7d</small> · ${reg(b.regularity_pct_30d)} <small class="muted">30d</small></span></button>
      ${b.need_min != null ? `<div class="kv"><span class="k">${esc(tr("睡眠需求"))}</span><span class="v">${hm(b.need_min)}</span></div>` : ""}</div></div>`;
  }

  function vitalsHtml(vitals) {
    if (!vitals?.length) return "";
    const V = VITAL();
    const tiles = vitals.filter((v) => V[v.key]).map((v) => {
      const [label, dig, worse, cut] = V[v.key];
      let delta = "";
      if (v.delta != null) {
        const bad = worse === 0 ? Math.abs(v.delta) >= cut : v.delta * worse >= cut;
        const zero = Math.abs(v.delta) < 0.5 / 10 ** dig;
        delta = `<span class="sd-delta ${bad ? "mid" : ""}">${zero ? "±0" : `${v.delta > 0 ? "+" : ""}${num(v.delta, dig)}`}</span>`;
      }
      return `<div class="tile"><span class="tile-label">${esc(label)}</span><span class="tile-value">${num(v.value, dig)}<small>${esc(UNIT[v.unit] || v.unit)}</small></span>${delta}</div>`;
    }).join("");
    return `<div class="section"><div class="section-head"><h2>${esc(tr("夜间指标"))}</h2><span class="small faint">${esc(tr("vs 30 晚均"))}</span></div><div class="tiles sd-vitals">${tiles}</div></div>`;
  }

  function aiHtml(d) {
    const notes = (d.notes || []).map((n) => `<div class="xp-a xp-t-${esc(n.tone)}"><i></i><span>${esc(n.text)}</span></div>`).join("");
    const ask = C.canAsk() ? `<div class="btns"><button class="btn btn-primary btn-block" id="sd-ask">${SPARK}<span>${esc(tr("让 Jarvis 详细分析这晚的睡眠"))}</span></button></div>` : "";
    if (!notes && !ask) return "";
    return `<div class="section"><div class="section-head"><h2>${esc(tr("AI 分析"))}</h2><span class="small faint">${esc(tr("规则要点"))}</span></div>
      <div class="card">${notes ? `<div class="xp-adv">${notes}</div>` : ""}${ask}</div></div>`;
  }

  function pageHtml(d) {
    if (!d.main) {
      return `${nav(d)}<div class="card row"><span class="dash">—</span><span class="small faint">${esc(tr("这晚没有手表睡眠数据"))}</span></div>`;
    }
    const naps = (d.naps || []).length ? `<div class="rcard-tags" style="margin-top:10px">${d.naps.map((n) => `<span class="badge plain">${esc(tr("小睡"))} ${esc(n.start)}–${esc(n.end)}</span>`).join("")}</div>` : "";
    return `${nav(d)}${heroHtml(d.main)}${naps}${stagesHtml(d.main)}${scoreHtml(d.score)}${baselineHtml(d)}${vitalsHtml(d.vitals)}${aiHtml(d)}`;
  }

  // ---------------------------------------------------------------- loading and events
  async function show(date, dir = 0) {
    if (busy) return;
    busy = true;
    day = date;
    const key = `${C.owner}:${date || "latest"}`;
    if (!cache.has(key)) box.innerHTML = `${nav(null)}<div class="empty faint">…</div>`;
    try {
      let d = cache.get(key);
      if (!d) {
        d = await C.api(`/v1/users/${C.owner}/sleep/night${date ? `?date=${date}` : ""}`);
        cache.set(key, d);
        cache.set(`${C.owner}:${d.date}`, d);
      }
      if (!box.isConnected) return;
      day = d.date;
      C.onDate(d.date);
      box.innerHTML = pageHtml(d);
      box.dataset.slide = dir < 0 ? "r" : dir > 0 ? "l" : "";
      bind(d);
    } catch (e) {
      if (e.status === 404) { C.back(); return; }   // the flag was turned off meanwhile
      if (box.isConnected) box.innerHTML = `${nav(null)}<div class="card row"><span class="dash">—</span><span class="small faint">${esc(e.status === 403 ? tr("无权限") : e.message)}</span></div>`;
      bindNav(null);
    } finally { busy = false; }
  }

  function bindNav(d) {
    $("#sd-back", box).onclick = () => C.back();
    const p = $("#sd-prev", box), n = $("#sd-next", box);
    if (p) p.onclick = () => d?.prev && show(d.prev, -1);
    if (n) n.onclick = () => d?.next && show(d.next, 1);
  }

  function bind(d) {
    bindNav(d);
    const ask = $("#sd-ask", box);
    if (ask) ask.onclick = () => C.askAI(d.ai_prompt);
    for (const b of box.querySelectorAll("[data-xp]")) {
      b.onclick = () => C.explain(b.dataset.xp, { sleep_score: tr("睡眠分"), sleep_efficiency: tr("睡眠效率"), sleep_consistency: tr("规律性") }[b.dataset.xp] || tr("睡眠"));
    }
    box.ontouchstart = (e) => { box._x = e.touches[0].clientX; box._y = e.touches[0].clientY; };
    box.ontouchend = (e) => {
      if (box._x == null) return;
      const dx = e.changedTouches[0].clientX - box._x, dy = e.changedTouches[0].clientY - box._y;
      box._x = null;
      if (Math.abs(dx) < 70 || Math.abs(dy) > Math.abs(dx) * 0.6 || e.target.closest?.(".hyp")) return;
      if (dx < 0 && d.next) show(d.next, 1);
      else if (dx > 0 && d.prev) show(d.prev, -1);
    };
  }

  window.BodySleepDetail = {
    placeholder: () => `<div class="sd" id="sleep-detail"></div>`,
    mount(el, ctx) {
      C = ctx; box = el;
      show(ctx.date);
    },
    reset() { cache.clear(); },
  };
})();
