/* Exercise library navigation and "add to plan" (issue #154).
   - createNav(): the library's filters (search text, muscle group) and scroll position, kept while an exercise detail is open,
     so 「返回」 from the detail lands on the same list (pure; tests/test_exercise_plan.py runs it in node).
   - addRequest(form): the POST /training/plan/add-exercise body from the sheet's form (pure, validates the numbers).
   - openAdd(ctx, ex): the sheet on an exercise detail, rollout flag exercise_add_to_plan (app.js only calls it while the flag is on):
     pick a session of the weekly plan (every week) or one of the next days (that date only), sets / reps pre-filled with the
     recommendation from GET /training/plan/add-options; a banned exercise is shown as blocked (the server refuses it as well).
   ctx: { api, esc, toast, openSheet(title, html), setBack(fn|null), back(), openDetail(slug), onAdded(res) } */
(function (root) {
  "use strict";
  const tr = (k, p) => root.I18N.t(k, p);

  function createNav() {
    let st = { q: "", grp: "", scroll: 0 };
    return {
      get: () => ({ ...st }),
      set: (patch) => { st = { ...st, ...patch }; },
      reset: () => { st = { q: "", grp: "", scroll: 0 }; },
    };
  }

  // form: { slug, scope: "template" | "day", session: { day_of_week, name }, day: "YYYY-MM-DD", timed, sets, repLow, repHigh, durationSec }
  // → { body } or { error } (already translated)
  function addRequest(f) {
    const int = (v) => (v === "" || v == null || !/^\d+$/.test(String(v).trim()) ? null : Number(v));
    const sets = int(f.sets);
    if (sets == null || sets < 1 || sets > 20) return { error: tr("组数要在 1–20 之间") };
    const body = { exercise: f.slug, scope: f.scope, sets };
    if (f.timed) {
      const d = int(f.durationSec);
      if (d == null || d < 1 || d > 7200) return { error: tr("时长要在 1 秒到 2 小时之间") };
      body.duration_sec = d;
    } else {
      const lo = int(f.repLow), hi = int(f.repHigh);
      if (lo == null || hi == null || lo < 1 || hi > 100 || lo > hi) return { error: tr("次数要在 1–100 之间，下限不能大于上限") };
      body.rep_low = lo; body.rep_high = hi;
    }
    if (f.scope === "template") {
      if (!f.session) return { error: tr("先选一个训练组") };
      body.day_of_week = f.session.day_of_week; body.session_name = f.session.name;
    } else {
      if (!f.day) return { error: tr("先选一天") };
      body.day = f.day;
    }
    return { body };
  }

  function openAdd(ctx, ex) {
    const { esc } = ctx;
    ctx.openSheet(tr("加入训练计划"), `<div class="empty faint">…</div>`);
    ctx.setBack(ctx.back);
    const body = document.getElementById("sheet-body");
    const fail = (e) => {
      body.innerHTML = e.status === 0
        ? `<div class="empty faint">${esc(tr("离线，连上网络后重试"))}</div><div class="btns" style="justify-content:center"><button class="btn" data-retry>${esc(tr("common.retry"))}</button></div>`
        : `<div class="empty faint">${esc(e.message || "")}</div>`;
      const r = body.querySelector("[data-retry]");
      if (r) r.onclick = () => openAdd(ctx, ex);
    };
    ctx.api(`/v1/users/me/training/plan/add-options?exercise=${encodeURIComponent(ex.slug)}`).then((o) => draw(o), fail);

    function draw(o) {
      const name = (root.I18N.lang === "en" && o.exercise.name_en) || o.exercise.name_zh;
      const head = `<div class="ap-name">${esc(name)}</div>`;
      if (o.blocked) {
        const alts = (o.alternatives || []).map((a) => `<button class="item" data-alt="${esc(a.slug)}"><div class="row-title" style="flex:1;text-align:left">${esc((root.I18N.lang === "en" && a.name_en) || a.name_zh)}</div></button>`).join("");
        body.innerHTML = `${head}<div class="ap-note ap-bad">${esc(tr("这是禁用动作，不能加入训练计划"))}${o.exercise.reason ? `<br><span class="small muted">${esc(o.exercise.reason)}</span>` : ""}</div>
          ${alts ? `<div class="cat" style="margin-top:12px">${esc(tr("可以改用"))}</div><div class="card list">${alts}</div>` : ""}`;
        for (const b of body.querySelectorAll("[data-alt]")) b.onclick = () => ctx.openDetail(b.dataset.alt, ctx.back);
        return;
      }
      if (!o.plan) {
        body.innerHTML = `${head}<div class="empty faint">${esc(tr("还没有周训练计划，先让 Jarvis 帮你做一个"))}</div>`;
        return;
      }
      const d = o.defaults, timed = o.exercise.modality === "duration";
      const f = { slug: ex.slug, scope: "template", session: null, day: null, timed,
        sets: d.sets, repLow: d.rep_low, repHigh: d.rep_high, durationSec: d.duration_sec };
      const rows = () => {
        if (f.scope === "template") {
          return o.sessions.map((s, i) => {
            const on = f.session && f.session.day_of_week === s.day_of_week && f.session.name === s.name;
            return `<button class="item ap-opt" data-i="${i}" aria-pressed="${on}" ${s.has_exercise ? "disabled" : ""}><span class="ap-dot"></span>
              <div style="flex:1;min-width:0;text-align:left"><div class="row-title">${esc(`${tr(s.weekday_zh)} · ${s.name}`)}</div>
              <div class="small muted">${esc(s.has_exercise ? tr("已包含") : tr("{n} 个动作", { n: s.exercise_count }))}</div></div></button>`;
          }).join("");
        }
        return o.days.map((x, i) => `<button class="item ap-opt" data-i="${i}" aria-pressed="${f.day === x.date}" ${x.has_exercise ? "disabled" : ""}><span class="ap-dot"></span>
          <div style="flex:1;min-width:0;text-align:left"><div class="row-title">${esc(`${tr(x.weekday_zh)} · ${x.date.slice(5)}`)}</div>
          <div class="small muted">${esc(x.has_exercise ? tr("已包含") : x.names.length ? x.names.join(" / ") : tr("休息日"))}</div></div></button>`).join("");
      };
      const nums = () => timed
        ? `<label class="ap-f"><span>${esc(tr("组数"))}</span><input class="input" id="ap-sets" type="number" inputmode="numeric" min="1" max="20" value="${esc(f.sets)}"></label>
           <label class="ap-f"><span>${esc(tr("时长（秒）"))}</span><input class="input" id="ap-dur" type="number" inputmode="numeric" min="1" value="${esc(f.durationSec)}"></label>`
        : `<label class="ap-f"><span>${esc(tr("组数"))}</span><input class="input" id="ap-sets" type="number" inputmode="numeric" min="1" max="20" value="${esc(f.sets)}"></label>
           <label class="ap-f"><span>${esc(tr("次数"))}</span><span class="ap-reps"><input class="input" id="ap-lo" type="number" inputmode="numeric" min="1" max="100" value="${esc(f.repLow)}"><i>–</i><input class="input" id="ap-hi" type="number" inputmode="numeric" min="1" max="100" value="${esc(f.repHigh)}"></span></label>`;
      const paint = () => {
        body.innerHTML = `${head}
          <div class="seg ap-seg" role="group"><button data-scope="template" aria-pressed="${f.scope === "template"}">${esc(tr("训练组（每周都有）"))}</button><button data-scope="day" aria-pressed="${f.scope === "day"}">${esc(tr("只加到某一天"))}</button></div>
          <div class="card list ap-list">${rows() || `<div class="empty faint">${esc(tr("没有可选的"))}</div>`}</div>
          <div class="ap-nums">${nums()}</div>
          <div class="btns"><button class="btn btn-primary btn-block" id="ap-go">${esc(tr("加入计划"))}</button></div>`;
        for (const b of body.querySelectorAll("[data-scope]")) b.onclick = () => { read(); f.scope = b.dataset.scope; paint(); };
        for (const b of body.querySelectorAll(".ap-opt")) b.onclick = () => {
          read();
          if (f.scope === "template") f.session = o.sessions[Number(b.dataset.i)]; else f.day = o.days[Number(b.dataset.i)].date;
          paint();
        };
        body.querySelector("#ap-go").onclick = go;
      };
      const read = () => {
        const v = (id) => body.querySelector(id)?.value;
        f.sets = v("#ap-sets"); if (timed) f.durationSec = v("#ap-dur"); else { f.repLow = v("#ap-lo"); f.repHigh = v("#ap-hi"); }
      };
      const go = async () => {
        read();
        const req = addRequest(f);
        if (req.error) { ctx.toast(req.error); return; }
        const btn = body.querySelector("#ap-go");
        btn.disabled = true; btn.textContent = tr("加入中…");
        try {
          const res = await ctx.api("/v1/users/me/training/plan/add-exercise", { method: "POST", body: req.body });
          ctx.onAdded(res);
          const where = res.scope === "day" ? `${tr(res.weekday_zh)} ${res.date.slice(5)}` : `${tr(res.weekday_zh)} · ${res.session_name}`;
          ctx.toast(tr("已加入 {where}", { where }));
          ctx.back();
        } catch (e) {
          ctx.toast(e.status === 0 ? tr("离线，连上网络后重试") : e.message);
          btn.disabled = false; btn.textContent = tr("加入计划");
        }
      };
      paint();
    }
  }

  const exported = { createNav, addRequest, openAdd };
  if (typeof module !== "undefined" && module.exports) module.exports = exported; else root.BodyExercisePlan = exported;
})(typeof window !== "undefined" ? window : globalThis);
