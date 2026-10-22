/* Training day v2 on 记录 → 训练 (FB-5, rollout flag training_day_v2; training/session_log.py):
   today's planned exercises as cards — demo thumbnail, recommended sets × reps · weight, banned / caution badge with the
   user's own cue — and set-by-set logging in the same card: each set its own reps and weight, a done tick, add / remove sets.
   The rows are a server draft (PUT /training/session/{date}, saved a moment after each change), so the app's WebView and a
   browser show the same rows; 「提交」 writes the ticked sets to the training log (POST …/submit), a resubmit replaces it.
   The plan sits in one collapsible 「今日训练计划」 card (collapsed by default: session names, exercise count, about N minutes);
   opened, it lists every exercise, each with a remove button (issue #172): only this day (undo in the toast and under 「已移除」),
   or from the session template ("never again": asks twice). askRemove() is also used by the future-day plan sheet in app.js.
   app.js draws placeholder() in the training page and calls mount(el, ctx) after rendering, only while the flag is on.
   ctx: { api, esc, toast, owner, self, date, openDetail(slug), openTrainingLog(), openSheet, closeSheet, onSubmitted(), onPlanChanged() }. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => window.I18N.t(k, p);
  const num = (v, d = 1) => window.I18N.num(v, d);
  const escape = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  let C = null, V = null, box = null;
  let rows = {};          // slug -> [{reps, weight_kg, duration_s, done}]
  const open = new Set();
  let planOpen = false;   // the plan card is collapsed until the user opens it
  let saveTimer = null, saveState = "", busy = false;

  const CSS = `
    .td2 .td-ex{padding:0;overflow:hidden;margin-top:10px}
    .td2 .td-ex:first-of-type{margin-top:0}
    .td2 .td-head{display:flex;align-items:center;gap:12px;width:100%;padding:12px 14px;background:none;border:0;text-align:left;color:inherit;font:inherit}
    .td2 .td-thumb{flex:none;width:48px;height:48px;border-radius:10px;background:#fff;object-fit:contain}
    .td2 .td-ph{flex:none;width:48px;height:48px;border-radius:10px;background:var(--surface-2)}
    .td2 .td-mm{display:flex;align-items:center;justify-content:center}
    .td2 .td-mm .mm{border:0;padding:0;gap:1px}
    .td2 .td-mm .mm-view svg{width:21px;height:42px}
    .td2 .td-mm figcaption{display:none}
    .td2 .td-title{flex:1;min-width:0}
    .td2 .td-name{font-weight:600;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
    .td2 .td-rx{font-size:13px;color:var(--text-2);margin-top:3px;font-variant-numeric:tabular-nums}
    .td2 .td-cnt{flex:none;font-size:13px;color:var(--text-2);font-variant-numeric:tabular-nums}
    .td2 .td-cnt.all{color:var(--good);font-weight:600}
    .td2 .td-body{padding:0 14px 14px}
    .td2 .td-note{font-size:13px;color:var(--text-2);margin:0 0 10px}
    .td2 .td-note .badge{margin-right:6px}
    .td2 .td-row{display:grid;grid-template-columns:28px 1fr 1fr 40px 32px;gap:8px;align-items:center;margin-top:6px}
    .td2 .td-row.one{grid-template-columns:28px 1fr 40px 32px}
    .td2 .td-row.hdr{font-size:11px;color:var(--text-3);margin-top:0;text-align:center}
    .td2 .td-n{font-size:13px;color:var(--text-2);text-align:center;font-variant-numeric:tabular-nums}
    .td2 .td-row input{width:100%;min-width:0;height:40px;border:0;border-radius:10px;background:var(--bg);color:var(--text);text-align:center;font-variant-numeric:tabular-nums;padding:0 4px}
    .td2 .td-row input::placeholder{color:var(--text-3)}
    .td2 .td-done{height:40px;border:0;border-radius:10px;background:var(--bg);color:var(--text-3);font-weight:700;font-size:16px}
    .td2 .td-done[aria-pressed="true"]{background:var(--good);color:#fff}
    .td2 .td-row.is-done input{color:var(--good)}
    .td2 .td-del{height:32px;border:0;background:none;color:var(--text-3);font-size:18px}
    .td2 .td-acts{display:flex;gap:8px;margin-top:10px}
    .td2 .td-acts .btn{min-height:38px;border-radius:999px;font-size:14px;background:var(--bg)}
    .td2 .td-last{font-size:12px;color:var(--text-3);margin-top:8px;font-variant-numeric:tabular-nums}
    .td2 .td-foot{margin-top:12px}
    .td2 .td-st{font-size:12px;color:var(--text-3);text-align:center;margin-top:6px;min-height:16px}
    .td2 .td-ex.banned .td-name>span:first-child{text-decoration:line-through;color:var(--text-2)}
    .td2 .td-plan{padding:0;overflow:hidden}
    .td2 .td-plan>.td-head .td-chev{flex:none;color:var(--text-3);transition:transform .15s}
    .td2 .td-plan[data-open="true"]>.td-head .td-chev{transform:rotate(90deg)}
    .td2 .td-plan-body{padding:0 12px 12px}
    .td2 .td-plan-body .td-ex{background:var(--bg);box-shadow:none}
    .td2 .td-hrow{display:flex;align-items:center}
    .td2 .td-hrow .td-head{flex:1;min-width:0}
    .td2 .td-rm{flex:none;width:44px;height:44px;border:0;background:none;color:var(--text-3);font-size:20px;line-height:1}
    .td2 .td-removed{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin-top:10px;font-size:13px;color:var(--text-2)}
    .td2 .td-removed button{border:0;background:var(--bg);color:var(--text);border-radius:999px;padding:6px 12px;min-height:32px;font-size:13px}
    .td-rmsheet .btn{width:100%;margin-top:10px}
    .td-rmsheet .btn.warn{color:var(--bad)}
    .td-rmsheet p{margin:0 0 4px;color:var(--text-2);font-size:14px}`;

  function injectCss() {
    if ($("#td2-css")) return;
    const st = document.createElement("style");
    st.id = "td2-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  const placeholder = () => `<div class="section td2" id="td2"><div class="section-head"><h2>${tr("今天")}</h2></div><div class="empty faint">…</div></div>`;

  const name = (c) => (window.I18N.lang === "en" && c.name_en ? c.name_en : c.name_zh);
  const timed = (c) => c.target?.duration_s != null;
  const weighted = (c) => !timed(c) && (c.modality || "weight_reps") === "weight_reps";   // bands / bodyweight: reps only
  const cols = (c) => (weighted(c) ? "" : "one");
  const cards = () => (V?.sessions || []).flatMap((s) => s.exercises).filter((c) => c.slug);
  const canLog = () => V?.can_log && C.self;
  const loggable = (c) => c.status !== "banned";

  function initRows() {
    rows = {};
    const saved = Object.fromEntries((V.log?.exercises || []).map((e) => [e.slug, e.sets || []]));
    for (const c of cards()) {
      const t = c.target || {};
      rows[c.slug] = saved[c.slug] ? saved[c.slug].map((s) => ({ ...s }))
        : Array.from({ length: t.sets || 3 }, () => ({ reps: t.reps ?? null, weight_kg: t.weight_kg ?? null, duration_s: t.duration_s ?? null, done: false }));
    }
  }

  function rxText(c) {
    const t = c.target || {};
    const reps = t.duration_s != null ? tr("{n} 秒", { n: num(t.duration_s, 0) })
      : t.rep_low && t.rep_high && t.rep_low !== t.rep_high ? `${t.rep_low}–${t.rep_high}` : t.reps ?? t.rep_low ?? "";
    const ld = c.load || {};
    const w = ld.kg != null ? `${num(ld.kg)} kg${ld.basis === "estimate" ? ` ${tr("估算")}` : ""}` : "";
    return [`${t.sets || ""} × ${reps}`, w, c.per_side ? tr("每侧") : ""].filter(Boolean).join(" · ");
  }

  function statusBadge(c) {
    if (c.status === "banned") return `<span class="badge bad">${escape(tr("禁用"))}</span>`;
    if (c.status === "caution") return `<span class="badge mid">${escape(tr("注意"))}</span>`;
    return "";
  }

  function rowHtml(c, s, i) {
    const t = c.target || {}, tm = timed(c), ro = !canLog();
    const inp = (k, ph, mode) => `<input type="text" inputmode="${mode}" data-k="${k}" data-i="${i}" value="${s[k] ?? ""}" placeholder="${ph ?? ""}"
      aria-label="${C.esc(`${tr("第 {n} 组", { n: i + 1 })} ${k === "reps" ? tr("次数") : k === "weight_kg" ? "kg" : tr("秒")}`)}" ${ro ? "readonly" : ""}>`;
    return `<div class="td-row ${cols(c)} ${s.done ? "is-done" : ""}"><span class="td-n">${i + 1}</span>
      ${tm ? inp("duration_s", t.duration_s, "numeric") : inp("reps", t.reps, "numeric") + (weighted(c) ? inp("weight_kg", t.weight_kg, "decimal") : "")}
      <button type="button" class="td-done" data-done="${i}" aria-pressed="${s.done ? "true" : "false"}" aria-label="${C.esc(tr("这组做完了"))}" ${ro ? "disabled" : ""}>✓</button>
      ${ro ? "<span></span>" : `<button type="button" class="td-del" data-del="${i}" aria-label="${C.esc(tr("删掉这组"))}">×</button>`}</div>`;
  }

  function picHtml(c) {
    const mm = !c.thumb && window.BodyMuscleMap ? window.BodyMuscleMap.html(c.primary_muscles, c.secondary_muscles) : "";
    return c.thumb ? `<img class="td-thumb" src="${escape(c.thumb)}" alt="" loading="lazy" decoding="async">`
      : mm ? `<span class="td-ph td-mm" aria-hidden="true">${mm}</span>` : `<span class="td-ph"></span>`;
  }

  // Read-only card heads in the training page's look (picture, name + restriction badge, prescription), for Jarvis' training
  // card (ai.js, rollout flag jarvis_rich). exercises: the exercise cards of a session view (ai/cards.py keeps their fields).
  function summaryHtml(exercises) {
    injectCss();
    return `<div class="td2">${(exercises || []).map((c) => `<div class="card td-ex ${c.status === "banned" ? "banned" : ""}"><div class="td-head">${picHtml(c)}
      <span class="td-title"><span class="td-name"><span>${escape(name(c))}</span>${statusBadge(c)}</span>
        <span class="td-rx" style="display:block">${escape(loggable(c) ? rxText(c) : tr("不要做"))}</span></span></div></div>`).join("")}</div>`;
  }

  function cardHtml(c) {
    const sets = rows[c.slug] || [], done = sets.filter((s) => s.done).length;
    const pic = picHtml(c);
    const isOpen = open.has(c.slug);
    const note = c.status !== "active" && (c.reason || c.cues)
      ? `<div class="td-note">${statusBadge(c)}${C.esc(c.reason || c.cues)}</div>` : "";
    const last = (c.last || [])[0];
    const body = !isOpen ? "" : `<div class="td-body">${note}
      ${loggable(c) ? `<div class="td-row hdr ${cols(c)}"><span class="td-n">${C.esc(tr("组"))}</span>
          ${timed(c) ? `<span>${C.esc(tr("秒"))}</span>` : `<span>${C.esc(tr("次数"))}</span>${weighted(c) ? `<span>kg${c.per_side ? ` · ${C.esc(tr("每侧"))}` : ""}</span>` : ""}`}<span></span><span></span></div>
        ${sets.map((s, i) => rowHtml(c, s, i)).join("")}` : ""}
      ${last?.sets?.length ? `<div class="td-last">${C.esc(tr("上次"))} ${C.esc(last.date.slice(5))} · ${C.esc(last.sets.join(" · "))}</div>` : ""}
      <div class="td-acts">${loggable(c) && canLog() ? `<button type="button" class="btn" data-add>${C.esc(tr("+ 一组"))}</button>` : ""}
        <button type="button" class="btn" data-guide>${C.esc(loggable(c) ? tr("动作指导") : tr("看替代动作"))}</button></div></div>`;
    return `<div class="card td-ex ${c.status === "banned" ? "banned" : ""}" data-slug="${C.esc(c.slug)}">
      <div class="td-hrow"><button type="button" class="td-head" data-toggle aria-expanded="${isOpen}">${pic}
        <span class="td-title"><span class="td-name"><span>${C.esc(name(c))}</span>${statusBadge(c)}</span>
          <span class="td-rx" style="display:block">${C.esc(loggable(c) ? rxText(c) : tr("不要做"))}</span></span>
        ${loggable(c) && sets.length ? `<span class="td-cnt ${done && done >= sets.length ? "all" : ""}">${done}/${sets.length}</span>` : ""}</button>
        ${V.can_edit_plan ? `<button type="button" class="td-rm" data-rm aria-label="${C.esc(tr("移除这个动作"))}">×</button>` : ""}</div>${body}</div>`;
  }

  const doneSets = () => cards().filter(loggable).reduce((a, c) => a + (rows[c.slug] || []).filter((s) => s.done && (s.reps != null || s.weight_kg != null || s.duration_s != null)).length, 0);

  function footHtml() {
    if (!canLog() || !cards().some(loggable)) return "";
    const n = doneSets(), lg = V.log, submitted = lg?.status === "submitted";
    const label = submitted ? tr("已提交 {n} 组", { n }) : lg?.changed_since_submit ? tr("重新提交 {n} 组", { n }) : tr("提交 {n} 组", { n });
    const linkRow = V.training_log !== "connected"
      ? `<button type="button" class="btn btn-block" data-tlog style="margin-bottom:8px">${C.esc(tr("先连接训练记录"))}</button>` : "";
    return `<div class="td-foot">${linkRow}<button type="button" class="btn btn-primary btn-block" data-submit ${!n || submitted || busy ? "disabled" : ""}>${C.esc(label)}</button>
      <div class="td-st" aria-live="polite">${C.esc(saveState)}</div></div>`;
  }

  const planSub = () => {   // "上肢 B · 8 个动作 · 约 60 分钟"
    const n = cards().length;
    return [(V.sessions || []).map((x) => x.name).join(" · "), tr("{n} 个动作", { n }), V.minutes ? tr("约 {n} 分钟", { n: V.minutes }) : ""].filter(Boolean).join(" · ");   // i18n-ok: plan names are data
  };

  function removedHtml() {
    const gone = V.removed || [];
    if (!gone.length) return "";
    return `<div class="td-removed"><span>${C.esc(tr("已移除"))}</span>${gone.map((r) => `<button type="button" data-restore="${C.esc(r.slug)}">${C.esc(window.I18N.lang === "en" && r.name_en ? r.name_en : r.name_zh || r.slug)} · ${C.esc(tr("恢复"))}</button>`).join("")}</div>`;
  }

  function planCardHtml() {
    const rd = V.readiness;
    const rdTag = V.is_today && rd && (rd.tier === "red" || rd.tier === "yellow")
      ? `<div class="rcard-tags" style="margin:0 0 10px"><span class="badge ${rd.tier === "red" ? "bad" : "mid"}">${C.esc(rd.label || "")}</span></div>` : "";
    const list = cards();
    const sets = list.filter(loggable).reduce((a, c) => a + (rows[c.slug] || []).length, 0);
    const done = list.filter(loggable).reduce((a, c) => a + (rows[c.slug] || []).filter((s) => s.done).length, 0);
    const body = !planOpen ? "" : `<div class="td-plan-body">${rdTag}${list.map(cardHtml).join("")}${removedHtml()}${footHtml()}</div>`;
    return `<div class="card td-plan" data-open="${planOpen}">
      <button type="button" class="td-head" data-plan-toggle aria-expanded="${planOpen}">
        <span class="td-title"><span class="td-name"><span>${C.esc(V.is_today ? tr("今日训练计划") : tr("{day} 训练计划", { day: V.date.slice(5) }))}</span></span>
          <span class="td-rx" style="display:block">${C.esc(planSub())}</span></span>
        ${sets ? `<span class="td-cnt ${done >= sets ? "all" : ""}">${done}/${sets}</span>` : ""}<span class="td-chev" aria-hidden="true">›</span></button>${body}</div>`;
  }

  function draw() {
    if (!box) return;
    const sess = V.sessions || [];
    const body = !sess.length
      ? `<div class="card"><div class="empty faint" style="padding:8px">${C.esc(V.rest_day ? tr("休息") : tr("无计划"))}</div></div>`
      : planCardHtml();
    box.innerHTML = `<div class="section-head"><h2>${C.esc(V.is_today ? tr("今天") : V.date)}</h2>
      <span class="small muted">${V.change ? `<span class="badge mid">${C.esc(tr("已调整"))}</span>` : ""}</span></div>${body}`;
  }

  // ---- saving: one PUT a moment after the last change; submit flushes first
  const payload = () => ({ exercises: cards().filter(loggable).map((c) => ({ slug: c.slug, name: c.name_zh, sets: rows[c.slug] || [] })) });

  function setState(text) {
    saveState = text;
    const el = $(".td-st", box);
    if (el) el.textContent = text;
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    setState(tr("保存中…"));
    saveTimer = setTimeout(save, 700);
  }

  async function save() {
    clearTimeout(saveTimer);
    saveTimer = null;
    try {
      // offline_mode (offline.js): kept on the device while offline; a later save of the same day replaces the kept one
      const r = await C.api(`/v1/users/me/training/session/${V.date}`, { method: "PUT", body: payload(),
        queue: { kind: "training-save", label: tr("训练 {day} · 逐组", { day: V.date }), group: `t:${V.date}`, coalesce: `ts:${V.date}` } });
      if (r.queued) { setState(tr("离线，已存在本机")); return true; }
      V.log = r.log;
      saveState = tr("已保存");
      redrawFoot();
      return true;
    } catch (e) {
      setState(e.status === 0 ? tr("离线，没保存") : e.message);
      return false;
    }
  }

  async function submit(allowDuplicate) {
    if (busy) return;
    busy = true;
    const btn = $("[data-submit]", box);
    if (btn) btn.disabled = true;
    try {
      if (!(await save())) return;
      const r = await C.api(`/v1/users/me/training/session/${V.date}/submit`, { method: "POST", body: { allow_duplicate: !!allowDuplicate },
        queue: { kind: "training-submit", label: tr("训练 {day} · 提交", { day: V.date }), group: `t:${V.date}` } });
      if (r.queued) { C.toast(tr("离线，联网后自动提交")); return; }
      if (r.duplicate) {
        C.toast(tr("今天已经记过这些动作"), tr("仍然提交"), () => submit(true));
        return;
      }
      V.log = r.log;
      C.toast(r.replaced ? tr("已更新训练记录（{n} 组）", { n: r.sets }) : tr("已提交 {n} 组", { n: r.sets }));
      C.onSubmitted?.();
    } catch (e) {
      C.toast(e.status === 0 ? tr("离线，连上网络后重试") : e.message);
    } finally {
      busy = false;
      draw();
    }
  }

  function redrawFoot() {   // the submit button follows the ticks without redrawing the inputs being typed in
    const foot = $(".td-foot", box);
    if (foot) foot.outerHTML = footHtml();
  }

  // ---- removing a planned exercise (issue #172)

  function planChanged() {
    (C.onPlanChanged || C.onSubmitted)?.();
  }

  // The remove sheet: this day only (default, undo in the toast) or out of the session template (second confirmation).
  // info: { slug, name, date, dayOfWeek, sessionName, addedForDay }; done(kind) runs after the server accepted it.
  function askRemove(ctx, info, done) {
    const E = ctx.esc;
    const fail = (e) => ctx.toast(e.status === 0 ? tr("离线，连上网络后重试") : e.message);
    const first = () => `<div class="td-rmsheet"><p>${E(info.name)}</p>
      <button type="button" class="btn btn-primary" data-rm-day>${E(info.isToday ? tr("只删今天的") : tr("只删这一天的"))}</button>
      ${info.addedForDay || !info.dayOfWeek ? "" : `<button type="button" class="btn warn" data-rm-tpl>${E(tr("以后都不要这个动作…"))}</button>`}</div>`;
    const second = () => `<div class="td-rmsheet"><p>${E(tr("会从训练组「{name}」里删掉，以后每周都不再出现。", { name: info.sessionName }))}</p>
      <button type="button" class="btn warn" data-rm-tpl-ok>${E(tr("确定，以后都不要"))}</button>
      <button type="button" class="btn" data-rm-back>${E(tr("返回"))}</button></div>`;
    const bind = (root) => {
      const on = (sel, fn) => { const b = root.querySelector(sel); if (b) b.onclick = fn; };
      on("[data-rm-day]", async () => {
        try {
          await ctx.api(`/v1/users/me/training/plan/remove-exercise`, { method: "POST", body: { exercise: info.slug, scope: "day", day: info.date } });
        } catch (e) { fail(e); return; }
        ctx.closeSheet();
        done("day");
        ctx.toast(tr("已移除「{name}」", { name: info.name }), tr("撤销"), async () => {
          try {
            await ctx.api(`/v1/users/me/training/plan/restore-exercise`, { method: "POST", body: { exercise: info.slug, day: info.date } });
            done("restore");
          } catch (e) { fail(e); }
        });
      });
      on("[data-rm-tpl]", () => { root.innerHTML = second(); bind(root); });
      on("[data-rm-back]", () => { root.innerHTML = first(); bind(root); });
      on("[data-rm-tpl-ok]", async () => {
        try {
          await ctx.api(`/v1/users/me/training/plan/remove-exercise`, { method: "POST", body: { exercise: info.slug, scope: "template", confirm: true,
            day_of_week: info.dayOfWeek, session_name: info.sessionName } });
        } catch (e) { fail(e); return; }
        ctx.closeSheet();
        done("template");
        ctx.toast(tr("已从「{name}」里删除", { name: info.sessionName }));
      });
    };
    ctx.openSheet(tr("移除这个动作"), first(), bind);
  }

  async function reload() {   // after the plan changed: the server's view of the day (the typed-in sets were saved first)
    if (saveTimer) await save();
    try { V = await C.api(`/v1/users/${C.owner}/training/session?date=${C.date}`); } catch (e) { C.toast(e.message); return; }
    initRows();
    draw();
  }

  function removeCard(c) {
    const s = (V.sessions || []).find((x) => x.exercises.includes(c));
    askRemove(C, { slug: c.slug, name: name(c), date: V.date, isToday: V.is_today, dayOfWeek: s?.day_of_week,
      sessionName: s?.name, addedForDay: c.added_for_day || s?.added_for_day }, (kind) => { planChanged(); reload(); });
  }

  async function restore(slug) {
    try {
      await C.api(`/v1/users/me/training/plan/restore-exercise`, { method: "POST", body: { exercise: slug, day: V.date } });
    } catch (e) { C.toast(e.message); return; }
    planChanged();
    reload();
  }

  function onClick(e) {
    if (e.target.closest("[data-plan-toggle]")) { planOpen = !planOpen; draw(); return; }
    const rs = e.target.closest("[data-restore]");
    if (rs) { restore(rs.dataset.restore); return; }
    const card = e.target.closest(".td-ex");
    if (!card) {
      if (e.target.closest("[data-submit]")) submit(false);
      else if (e.target.closest("[data-tlog]")) C.openTrainingLog?.();
      return;
    }
    const slug = card.dataset.slug, c = cards().find((x) => x.slug === slug), sets = rows[slug];
    if (e.target.closest("[data-rm]")) { if (c) removeCard(c); return; }
    if (e.target.closest("[data-toggle]")) {
      if (open.has(slug)) open.delete(slug); else open.add(slug);
      draw();
      return;
    }
    if (e.target.closest("[data-guide]")) { C.openDetail(slug); return; }
    if (!canLog()) return;
    const doneBtn = e.target.closest("[data-done]"), del = e.target.closest("[data-del]");
    if (doneBtn) {
      const s = sets[Number(doneBtn.dataset.done)], t = c.target || {};
      s.done = !s.done;
      if (s.done) {   // ticking an empty row takes the recommendation
        if (timed(c)) s.duration_s ??= t.duration_s ?? null;
        else { s.reps ??= t.reps ?? null; s.weight_kg ??= t.weight_kg ?? null; }
      }
    } else if (del) sets.splice(Number(del.dataset.del), 1);
    else if (e.target.closest("[data-add]")) {
      const prev = sets[sets.length - 1] || {};
      sets.push({ reps: prev.reps ?? c.target?.reps ?? null, weight_kg: prev.weight_kg ?? c.target?.weight_kg ?? null,
        duration_s: prev.duration_s ?? c.target?.duration_s ?? null, done: false });
    } else return;
    if (V.log?.status === "submitted") V.log = { ...V.log, status: "draft", changed_since_submit: true };
    draw();
    scheduleSave();
  }

  function onInput(e) {
    const inp = e.target.closest("input[data-k]");
    if (!inp || !canLog()) return;
    const slug = inp.closest(".td-ex").dataset.slug, s = rows[slug][Number(inp.dataset.i)];
    const raw = inp.value.replace(",", ".").trim();
    const v = raw === "" ? null : Number(raw);
    if (raw !== "" && !Number.isFinite(v)) return;
    s[inp.dataset.k] = inp.dataset.k === "reps" && v != null ? Math.round(v) : v;
    if (V.log?.status === "submitted") {
      V.log = { ...V.log, status: "draft", changed_since_submit: true };
      redrawFoot();
    }
    scheduleSave();
  }

  async function mount(el, ctx) {
    C = ctx;
    box = el;
    if (!box) return;
    injectCss();
    if (saveTimer) await save();   // the page redrew while a change was waiting to be saved
    try {
      V = await C.api(`/v1/users/${C.owner}/training/session?date=${C.date}`);
    } catch (e) {
      box.innerHTML = `<div class="section-head"><h2>${C.esc(tr("今天"))}</h2></div><div class="empty faint">${C.esc(e.status === 0 ? tr("离线，连上网络后重试") : e.message)}</div>`;
      return;
    }
    // Sets kept on this device while offline (offline.js) are newer than the server's draft: show them
    const kept = (window.BodyOffline?.pending("training-save") || []).filter((it) => it.group === `t:${V.date}`).pop();
    if (kept && C.self) V = { ...V, log: { ...(V.log || {}), status: "draft", exercises: kept.body.exercises } };
    initRows();
    saveState = V.log?.status === "submitted" ? tr("已提交") : V.log ? tr("已保存") : "";
    draw();
    box.onclick = onClick;
    box.oninput = onInput;
  }

  window.BodyTrainingDay = { placeholder, mount, summaryHtml, askRemove, pending: () => saveTimer !== null, flush: () => (saveTimer ? save() : null) };
})();
