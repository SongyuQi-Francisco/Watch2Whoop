/* Muscle load map (rollout flag muscle_load; training/muscle_load.py): the body of the profile's sex (male, with a hint to set it,
   when the profile has none; no manual switch, #189), front and back, coloured by this week's effective sets against the recommended
   weekly range, an outline on what was trained today, stripes on what a sport (watch workouts: basketball, swimming...) worked, tap a
   muscle for the numbers, the last session, the sports and recommended exercises. Pieces: full() on 记录 → 训练, card() on the Today
   page, cardSvg() for Jarvis' muscle card, 「我 → 训练设置」 (focus muscles + physique goal), the 「让 Jarvis 分析」 button and
   「让 Jarvis 补进动作库」 for exercises the catalog does not know (custom exercises, #189). Muscle and sport names come from the
   server (already in the page language); the body drawing is BodyMuscleMap.bodyHtml (muscle-map.js).
   ctx: { api, esc, openSheet, closeSheet, toast, owner, self, openExercise(slug), ask(text), canAsk(), refresh(), setTab(tab, sub) }. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => window.I18N.t(k, p);
  const num = (v, d = 0) => window.I18N.num(v, d);
  const SEP = () => tr("、"), COLON = () => tr("：");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const RANGES = ["today", "week", "last7"];
  const RANK = { none: 0, low: 1, ok: 2, high: 3 };
  let C = null;
  const S = { range: "week", data: null };

  const CSS = `
    .mml .mml-top{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
    .mml .mml-top .seg{margin:0}
    .mml-bodies{display:flex;justify-content:center;gap:10px;padding:6px 0 2px}
    .mml-fig{margin:0;text-align:center;flex:0 1 150px;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}
    .mml-fig figcaption{font-size:12px;color:var(--text-2);margin-top:2px}
    svg.mm2{height:300px;width:auto;max-width:100%;display:block;overflow:visible}
    .mm2-base path{fill:var(--text-3);fill-opacity:.22}
    .mm2-line{fill:none;stroke:var(--text-3);stroke-width:1.3;vector-effect:non-scaling-stroke;pointer-events:none}
    .mm2-r{cursor:pointer;-webkit-tap-highlight-color:transparent}
    .mm2-r path{stroke:var(--bg,#fff);stroke-width:.8;vector-effect:non-scaling-stroke;transition:fill .2s}
    .mm2-none path{fill:var(--surface-2)} .mm2-low path{fill:var(--train);fill-opacity:.4}
    .mm2-ok path{fill:var(--good)} .mm2-high path{fill:var(--bad)}
    .mm2-today path{stroke:var(--text);stroke-width:1.8}
    .mml-legend{display:flex;flex-wrap:wrap;gap:6px 14px;justify-content:center;font-size:12px;color:var(--text-2);margin:6px 0 2px}
    .mml-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px;background:var(--surface-2);box-shadow:inset 0 0 0 1px var(--hair)}
    .mml-legend i.low{background:var(--train);opacity:.5} .mml-legend i.ok{background:var(--good)} .mml-legend i.high{background:var(--bad)}
    .mml-legend i.today{background:none;box-shadow:inset 0 0 0 2px var(--text)}
    .mml-legend i.sport{background:repeating-linear-gradient(45deg,var(--text-2) 0 2px,transparent 2px 5px)}
    .mm2-hatch{fill:var(--text);fill-opacity:.42} .mm2-sp{pointer-events:none}
    .mml-sports{font-size:13px;color:var(--text-2);margin-top:8px;font-variant-numeric:tabular-nums}
    .mml-link{background:none;border:0;padding:0;font:inherit;font-size:12px;color:var(--accent,var(--train));text-decoration:underline;cursor:pointer}
    i.mml-sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px;background:repeating-linear-gradient(45deg,var(--text-2) 0 2px,transparent 2px 5px)}
    .mml-um{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px} .mml-um .mml-none{margin:0}
    .mml-list{margin-top:10px}
    .mml-row{display:grid;grid-template-columns:1fr auto auto;gap:4px 10px;align-items:center;width:100%;padding:11px 2px;background:none;border:0;border-top:1px solid var(--hair);color:inherit;font:inherit;text-align:left}
    .mml-row:first-child{border-top:0}
    .mml-row .mml-n{font-weight:600} .mml-row .mml-s{font-size:13px;color:var(--text-2);font-variant-numeric:tabular-nums;text-align:right}
    .mml-row .mml-d{grid-column:1/-1;font-size:12px;color:var(--text-3);font-variant-numeric:tabular-nums}
    .mml-none{font-size:12px;color:var(--text-3);margin-top:8px}
    .mml-acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
    .mml-mini{display:flex;align-items:center;gap:12px;width:100%;background:none;border:0;color:inherit;font:inherit;text-align:left;padding:0}
    .mml-mini .mml-bodies{flex:none;width:112px;padding:0;gap:2px} .mml-mini .mml-fig{flex:1 1 0} .mml-mini svg.mm2{height:140px}
    .mml-mini .mml-txt{flex:1;min-width:0} .mml-mini .mml-big{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
    .mml-mini .mml-sub{font-size:12px;color:var(--text-2);margin-top:2px}
    .mml-sib{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
    .mml-chip{border:0;border-radius:999px;padding:7px 12px;font:inherit;font-size:14px;background:var(--surface-2);color:var(--text)}
    .mml-chip[aria-pressed="true"]{background:var(--train);color:#fff}
    .mml-rec{display:flex;align-items:center;gap:8px;width:100%;padding:10px 0;background:none;border:0;border-top:1px solid var(--hair);color:inherit;font:inherit;text-align:left}
    .mml-ex{font-size:13px;color:var(--text-2);margin:2px 0;font-variant-numeric:tabular-nums}
    .mml-ta{width:100%;min-height:70px;resize:vertical}
    .ai-card-muscle .mml-bodies{padding:2px 0} .ai-card-muscle svg.mm2{height:210px}
    @media (min-width:900px){.mml-fig{flex-basis:170px}}`;
  function css() {
    if (document.getElementById("mml-css")) return;
    document.head.append(Object.assign(document.createElement("style"), { id: "mml-css", textContent: CSS }));
  }

  const on = () => !!window.BodyFlags?.on("muscle_load") && !!window.BodyMuscleMap?.bodyHtml && !!window.BodyMuscleBodies;
  const STATUS = () => ({ none: tr("没练"), low: tr("偏少"), ok: tr("合适"), high: tr("偏多") });
  const RANGE_NAME = () => ({ today: tr("今天"), week: tr("本周"), last7: tr("近 7 天") });
  const badgeOf = (st) => `<span class="badge ${st === "ok" ? "good" : st === "high" ? "bad" : st === "low" ? "mid" : "plain"}">${esc(STATUS()[st])}</span>`;
  const day = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(window.I18N.locale(), { month: "numeric", day: "numeric", weekday: "short" });
  const exName = (e) => (window.I18N.lang === "en" && e.name_en ? e.name_en : e.name);

  // region -> { status, today, key } from rows [{key, sets, status, today}]. `onlyTrained` = the 今天 view: grey unless trained today.
  function regionStates(rows, range) {
    const out = {};
    const MM = window.BodyMuscleMap;
    for (const r of rows) {
      for (const region of MM.BODY_REGIONS[r.key] || []) {
        const cur = out[region];
        const st = range === "today" && !r.today ? "none" : r.status;
        const sport = r.sport > 0 || !!cur?.sport;
        if (!cur || r.sets > cur.sets || (r.sets === cur.sets && RANK[st] > RANK[cur.status])) out[region] = { status: st, today: !!r.today || !!cur?.today, key: r.key, sets: r.sets, sport };
        else { if (r.today) cur.today = true; cur.sport = sport; }
      }
    }
    return out;
  }
  const rowsOf = (data, range) => data.muscles.map((m) => ({ key: m.key, name: m.name, sets: m.ranges[range].sets, kg: m.ranges[range].volume_kg,
    status: m.status[range === "last7" ? "last7" : "week"], today: m.trained_today, sport: m.sport_min?.[range] || 0 }));
  const sexOf = (data) => data.sex || "male";   // the profile decides; not set = the male body + sexHint()
  const sexHint = (data, id) => (!data.sex && C?.self ? `<button class="mml-link" id="${id}">${tr("在个人资料里设置性别")}</button>` : "");
  const bindSexHint = (root, id) => { const b = $(`#${id}`, root); if (b) b.onclick = (e) => { e.stopPropagation(); window.BodyProfile?.open(); }; };
  const sportsText = (list, withUnmapped) => (list || []).map((x) => `${x.name} ${tr("{n} 分钟", { n: num(x.min) })}${withUnmapped && x.mapped === false ? ` (${tr("不计入肌群")})` : ""}`).join(" · ");

  function bodies(rows, range, sex, mini) {
    const reg = regionStates(rows, range);
    const fig = (side, cap) => `<figure class="mml-fig">${window.BodyMuscleMap.bodyHtml(sex, side, reg, cap)}${mini ? "" : `<figcaption>${esc(cap)}</figcaption>`}</figure>`;
    return `<div class="mml-bodies">${fig("front", tr("正面"))}${fig("back", tr("背面"))}</div>`;
  }

  // ---------------------------------------------------------------- Jarvis card (and anything that has plain rows)
  function cardSvg(c) {
    css();
    if (!window.BodyMuscleMap?.bodyHtml || !window.BodyMuscleBodies) return "";
    const rows = (c.muscles || []).map((m) => ({ ...m, today: m.today }));
    const low = rows.filter((m) => m.status === "none" || m.status === "low").map((m) => m.name);
    const rows2 = rows.map((m) => ({ ...m, sport: m.sport_min || 0 }));
    const sub = `${esc(RANGE_NAME()[c.range] || "")}${low.length ? ` · ${esc(tr("偏少 / 没练"))}${COLON()}${esc(low.slice(0, 6).join(SEP()))}` : ""}`;
    const sports = sportsText(c.sports);
    return `<div class="ai-card-head"><span class="ai-card-title">${esc(tr("肌肉负荷"))}</span></div>
      <div class="mml">${bodies(rows2, c.range === "last7" ? "last7" : c.range === "today" ? "today" : "week", c.sex || "male")}</div>
      <div class="small muted ai-card-sub">${sub}</div>
      ${sports ? `<div class="small muted ai-card-sub"><i class="mml-sw"></i>${esc(tr("运动"))}${COLON()}${esc(sports)}</div>` : ""}
      ${c.sex ? "" : `<div class="small muted ai-card-sub">${esc(tr("按男性图显示，可在个人资料里设置性别"))}</div>`}`;
  }

  // ---------------------------------------------------------------- full view on 记录 → 训练
  function placeholder() { return `<div class="section mml" id="mml-box"><div class="section-head"><h2>${tr("肌肉负荷")}</h2></div><div class="card"><div class="empty faint">…</div></div></div>`; }

  function fullHtml(data) {
    const range = S.range, sex = sexOf(data), rows = rowsOf(data, range);
    const trained = rows.filter((r) => r.sets > 0 || r.sport > 0).sort((a, b) => b.sets - a.sets || b.sport - a.sport);
    const rest = rows.filter((r) => r.sets <= 0 && r.sport <= 0).map((r) => r.name);
    const sports = sportsText(data.sports?.[range], true);
    const byKey = Object.fromEntries(data.muscles.map((m) => [m.key, m]));
    const list = trained.map((r) => {
      const m = byKey[r.key], d = m.delta_sets;
      const delta = range === "week" ? ` · ${tr("上周")} ${num(m.prev_week.sets, 1)} ${tr("组")}${d ? ` · ${tr("较上周")} ${d > 0 ? "+" : ""}${num(d, 1)}` : ""}` : "";
      const sp = r.sport > 0 ? ` · ${tr("运动 {n} 分钟", { n: num(r.sport) })}` : "";
      const badge = r.sets > 0 ? badgeOf(r.status) : `<span class="badge plain"><i class="mml-sw"></i>${esc(tr("运动"))}</span>`;
      return `<button class="mml-row" data-m="${esc(r.key)}"><span class="mml-n">${esc(r.name)} ${badge}</span>
        <span class="mml-s">${num(r.sets, 1)} ${tr("组")}</span><span class="mml-s">${num(r.kg)} kg</span>
        <span class="mml-d">${esc(tr("每周推荐 {a}–{b} 组", { a: m.target.low, b: m.target.high }))}${esc(delta)}${esc(sp)}</span></button>`;
    }).join("");
    const seg = (items, cur) => `<div class="seg" role="group">${items.map(([k, t]) => `<button data-mr="${k}" aria-pressed="${k === cur}">${esc(t)}</button>`).join("")}</div>`;
    return `<div class="section-head"><h2>${tr("肌肉负荷")}</h2><span class="small muted">${esc(RANGE_NAME()[range])}</span></div>
      <div class="card">
        <div class="mml-top">${seg(RANGES.map((k) => [k, RANGE_NAME()[k]]), range)}${sexHint(data, "mml-sex")}</div>
        ${bodies(rows, range, sex)}
        <div class="mml-legend"><span><i></i>${tr("没练")}</span><span><i class="low"></i>${tr("偏少")}</span><span><i class="ok"></i>${tr("合适")}</span><span><i class="high"></i>${tr("偏多")}</span><span><i class="today"></i>${tr("今天练过")}</span><span><i class="sport"></i>${tr("运动")}</span></div>
        ${sports ? `<div class="mml-sports">${esc(tr("运动"))}${COLON()}${esc(sports)}</div>` : ""}
        <div class="mml-list">${list || `<div class="empty faint" style="padding:10px">${tr("这段时间没有逐组记录")}</div>`}</div>
        ${rest.length && trained.length ? `<div class="mml-none">${esc(tr("没练"))}${COLON()}${esc(rest.join(SEP()))}</div>` : ""}
        ${data.unmatched?.length ? `<div class="mml-um"><span class="mml-none">${esc(tr("{n} 个动作没对上动作库，没有计入", { n: data.unmatched.length }))}${COLON()}${esc(data.unmatched.slice(0, 6).join(SEP()))}${data.unmatched.length > 6 ? "…" : ""}</span>
          ${C.self && C.canAsk() ? `<button class="mml-link" id="mml-fill">${tr("让 Jarvis 补进动作库")}</button>` : ""}</div>` : ""}
        <div class="mml-acts">${C.self && C.canAsk() ? `<button class="btn btn-primary" id="mml-ask">${tr("让 Jarvis 分析我的肌群训练")}</button>` : ""}
          ${C.self ? `<button class="btn" id="mml-goals">${tr("训练设置")}</button>` : ""}</div>
      </div>`;
  }

  async function mount(el, ctx) {
    C = ctx; css();
    if (!on()) return;
    const box = $("#mml-box", el) || el;
    try { S.data = await C.api(`/v1/users/${C.owner}/training/muscle-load`); } catch (e) { box.innerHTML = ""; return; }
    draw(box);
  }
  function draw(box) {
    box.innerHTML = fullHtml(S.data);
    $$("[data-mr]", box).forEach((b) => { b.onclick = () => { S.range = b.dataset.mr; draw(box); }; });
    bindSexHint(box, "mml-sex");
    $$("[data-m]", box).forEach((b) => { b.onclick = () => openMuscle(b.dataset.m); });
    $$("[data-region]", box).forEach((g) => {
      g.onclick = () => {
        const key = regionStates(rowsOf(S.data, S.range), S.range)[g.dataset.region]?.key;
        if (key) openMuscle(key);
      };
    });
    const ask = $("#mml-ask", box);
    if (ask) ask.onclick = () => C.ask(tr("请分析我的肌群训练：先用 get_muscle_load 看各肌群本周的组数，结合我的重点肌群和体态目标，告诉我哪些偏少、哪些偏多、和目标差在哪、具体怎么改（每周给哪块加几组、换哪些动作，避开我的禁用动作）。要改训练计划的话先给我看改动，我确认后再改。"));
    const fill = $("#mml-fill", box);
    if (fill) fill.onclick = () => C.ask(fillPrompt(S.data.unmatched));
    const g = $("#mml-goals", box);
    if (g) g.onclick = () => openGoals();
  }

  // The message 「让 Jarvis 补进动作库」 pre-fills in the chat (the user can edit it before sending); the names stay as logged
  function fillPrompt(names) {
    return tr("下面这些动作在我的训练记录里，但没对上动作库，所以没计入肌肉图：{names}。请逐个判断主要肌群和次要肌群（只用动作库的肌群），拿不准的先问我，然后用 add_custom_exercise 加进我自己的动作库，名字和记录里的一致。", { names: (names || []).join(SEP()) });
  }

  // ---------------------------------------------------------------- one muscle
  async function openMuscle(key) {
    css();
    let d;
    try { d = await C.api(`/v1/users/${C.owner}/training/muscle-load/${encodeURIComponent(key)}`); } catch (e) { C.toast(e.message); return; }
    const sibs = (window.BodyMuscleMap.BODY_REGIONS[key] || []).flatMap((r) => window.BodyMuscleMap.REGION_MUSCLES[r] || []).filter((m, i, a) => a.indexOf(m) === i && m !== key);
    const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`;
    const r = d.ranges, st = d.status.week, delta = d.delta_sets;
    const last = d.last ? `${esc(day(d.last.date))}<div>${d.last.exercises.map((e) => `<div class="mml-ex">${esc(exName(e))} · ${esc(tr("{n} 组", { n: e.sets }))}${e.top ? ` · ${esc(e.top)} kg` : ""}${e.primary ? "" : ` · ${esc(tr("辅助"))}`}</div>`).join("")}</div>` : esc(tr("近 4 周没练"));
    const sp = d.sports?.[S.range] || [];
    const sports = sp.map((x) => `<div class="mml-ex"><i class="mml-sw"></i>${esc(x.name)} · ${esc(tr("{n} 分钟", { n: num(x.min) }))} · ${esc(x.primary ? tr("主要") : tr("辅助"))}</div>`).join("");
    const rec = d.recommended.map((x) => `<button class="mml-rec" data-ex="${esc(x.slug)}"><span style="flex:1">${esc(window.I18N.lang === "en" && x.name_en ? x.name_en : x.name_zh)}${x.equipment_zh?.length && window.I18N.lang !== "en" ? ` <span class="small muted">${esc(x.equipment_zh.join(SEP()))}</span>` : ""}</span>${x.status === "caution" ? `<span class="badge mid">${tr("注意")}</span>` : ""}</button>`).join("");
    C.openSheet(d.name, `
      ${sibs.length ? `<div class="mml-sib"><button class="mml-chip" aria-pressed="true">${esc(d.name)}</button>${sibs.map((m) => `<button class="mml-chip" data-sib="${esc(m)}" aria-pressed="false">${esc((S.data.muscles.find((x) => x.key === m) || {}).name || m)}</button>`).join("")}</div>` : ""}
      ${kv(tr("本周"), `${badgeOf(st)} ${num(r.week.sets, 1)} ${tr("组")} · ${num(r.week.volume_kg)} kg`)}
      ${kv(tr("每周推荐"), esc(tr("{a}–{b} 组", { a: d.target.low, b: d.target.high })))}
      ${kv(tr("上周"), `${num(d.prev_week.sets, 1)} ${tr("组")} · ${num(d.prev_week.volume_kg)} kg${delta ? ` (${delta > 0 ? "+" : ""}${num(delta, 1)} ${tr("组")})` : ""}`)}
      ${kv(tr("近 7 天"), `${num(r.last7.sets, 1)} ${tr("组")} · ${num(r.last7.volume_kg)} kg`)}
      ${kv(tr("最近一次练"), last)}
      ${sports ? kv(`${tr("运动")} · ${RANGE_NAME()[S.range]}`, `<div>${sports}</div>`) : ""}
      <div class="xp-h" style="margin-top:14px">${tr("推荐动作")}</div>${rec || `<div class="empty faint" style="padding:10px">${tr("暂无")}</div>`}`,
    (root) => {
      $$("[data-sib]", root).forEach((b) => { b.onclick = () => { C.closeSheet(); openMuscle(b.dataset.sib); }; });
      $$("[data-ex]", root).forEach((b) => { b.onclick = () => { C.closeSheet(); C.openExercise(b.dataset.ex); }; });
    });
  }

  // ---------------------------------------------------------------- Today page card
  function card(data, ctx) {
    C = ctx || C; css();
    if (!on() || !data?.muscles) return "";
    const rows = rowsOf(data, "week"), sex = sexOf(data);
    const total = rows.reduce((a, r) => a + r.sets, 0);
    const trainedToday = data.muscles.filter((m) => m.trained_today).map((m) => m.name);
    const low = rows.filter((r) => r.sets > 0 && r.status === "low").length;
    const sportMin = (data.sports?.week || []).reduce((a, x) => a + x.min, 0);
    const big = total > 0 ? `${num(total, 0)} <small>${tr("组")}</small>` : "—";
    const sub = trainedToday.length ? `${tr("今天练了")}${COLON()}${esc(trainedToday.slice(0, 4).join(SEP()))}` : (low ? tr("{n} 块肌群偏少", { n: low }) : tr("本周还没有逐组记录"));
    const sp = sportMin > 0 ? `<span class="mml-sub" style="display:block">${esc(tr("运动 {n} 分钟", { n: num(sportMin) }))}</span>` : "";
    return `<div class="section" data-card="muscle_load"><div class="section-head"><h2>${tr("肌肉负荷")}</h2><span class="small muted">${tr("本周")}</span></div>
      <div class="card mml"><div class="mml-mini" id="mml-open" role="button" tabindex="0">${bodies(rows, "week", sex, true)}
        <span class="mml-txt"><span class="mml-big">${big}</span><span class="mml-sub" style="display:block">${sub}</span>${sp}</span><span class="chev">›</span></div>
        ${sexHint(data, "mml-sex-card")}</div></div>`;
  }
  function bindCard(view, ctx) {
    C = ctx || C;
    const b = $("#mml-open", view);
    if (b) { b.onclick = () => C.setTab("record", "training"); b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); b.click(); } }; }
    bindSexHint(view, "mml-sex-card");
  }

  // ---------------------------------------------------------------- 我 → 训练设置
  async function openGoals() {
    css();
    let g;
    try { g = await C.api(`/v1/users/${C.owner}/training/goals`); } catch (e) { C.toast(e.message); return; }
    const sel = new Set(g.focus_muscles);
    C.openSheet(tr("训练设置"), `
      <div class="xp-h">${tr("重点肌群")} <span class="small muted">${tr("最多 {n} 个", { n: g.max_focus })}</span></div>
      <div class="mml-sib" id="mlg-chips">${g.muscles.map((m) => `<button class="mml-chip" data-k="${esc(m.key)}" aria-pressed="${sel.has(m.key)}">${esc(m.name)}</button>`).join("")}</div>
      <div class="xp-h" style="margin-top:12px">${tr("体态 / 形体目标")}</div>
      <textarea class="input mml-ta" id="mlg-goal" maxlength="200" placeholder="${esc(tr("例如：改善圆肩，背更厚，腿更有线条"))}">${esc(g.physique_goal || "")}</textarea>
      <p class="small muted" id="mlg-msg" style="margin-top:8px"></p>
      <div class="btns"><button class="btn btn-primary" id="mlg-save">${tr("保存")}</button></div>`,
    (root) => {
      $$("#mlg-chips .mml-chip", root).forEach((b) => {
        b.onclick = () => {
          const k = b.dataset.k;
          if (sel.has(k)) sel.delete(k);
          else if (sel.size >= g.max_focus) { $("#mlg-msg", root).textContent = tr("最多 {n} 个", { n: g.max_focus }); return; }
          else sel.add(k);
          b.setAttribute("aria-pressed", sel.has(k));
        };
      });
      $("#mlg-save", root).onclick = async () => {
        try {
          await C.api(`/v1/users/${C.owner}/training/goals`, { method: "PUT", body: { focus_muscles: [...sel], physique_goal: $("#mlg-goal", root).value.trim() || null } });
        } catch (e) { $("#mlg-msg", root).textContent = e.message; return; }
        C.closeSheet(); C.toast(tr("已保存"));
        C.refresh?.();
      };
    });
  }

  window.BodyMuscleLoad = { on, placeholder, mount, card, bindCard, cardSvg, openGoals, openMuscle, fillPrompt, setCtx: (c) => { C = c; } };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meGroup: "main",
    meItem: (chev) => (on() && C?.self ? `<button class="item" id="me-train-settings"><span class="row-title">${tr("训练设置")}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => { const b = $("#me-train-settings", view); if (b) b.onclick = () => openGoals(); },
  });
})();
