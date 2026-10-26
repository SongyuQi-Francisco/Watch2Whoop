/* Flag ui_ai_first (#279, docs/ui-ai-first-redesign.md v0.4): the AI-first information architecture, in the page's current look.
   app.js loads this file only for people with the flag (index.html declares it type w2w/lazy, so nobody else downloads it); with
   the flag off nothing here runs and the page is exactly as before. Guiding rule: someone who has used apps for years finds their
   way without any tutorial, so the AI's entry points are the familiar ones.
   - Tab bar: 今天 · 身体 · (Jarvis) · 圈子 · 我. 记录 and 饮食 leave the bar and open as second-level pages (a back link on top).
   - 今天: three rings side by side (recovery in the middle, sleep and strain at the sides; they open the same sheets as before),
     a search field that is also "ask Jarvis", two or three concrete questions made from the user's own numbers, 重点 (the
     reminders), the circles card (always there: friends, or an invitation), my modules (at most 6), a row to 全部功能.
   - Long press (or right click) on any data card or number (elements with data-card): a short menu whose first row asks Jarvis
     about exactly that card.
   - 我 → 全部功能 (S.meSub "all"): every feature by area, nothing locked, the same search field.
   - No intro screens (app.js skips intro.js for these people).
   It reuses the page's own pieces (window.BodyPage) and the class names of app.css; the few extra rules are injected below. */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  let P = null;        // window.BodyPage (app.js)
  const MAX_MODULES = 6;
  const LONG_PRESS_MS = 480;

  const CSS = `.a1-tri{display:grid;grid-template-columns:1fr 1.75fr 1fr;align-items:center;padding:10px 0 0}
.a1-tri>button{border:0;background:none;padding:0;display:flex;flex-direction:column;align-items:center;gap:2px;color:inherit;font:inherit}
.a1-tri .ring.a1-sm{width:92px;height:92px}.a1-tri .ring.a1-lg{width:164px;height:164px}
.a1-word{display:flex;flex-direction:column;align-items:center}
.a1-ask{position:relative;margin-top:14px}.a1-ask .input{padding-left:40px}
.a1-ask .a1-sp{position:absolute;left:13px;top:50%;transform:translateY(-50%);display:flex;color:var(--text-2);pointer-events:none}
.a1-sp svg{width:18px;height:18px}
.a1-hits{margin-top:8px}
.a1-asks{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.a1-asks .chip{display:inline-flex;align-items:center;gap:4px}
.a1-asks .chip svg{width:14px;height:14px;color:var(--text-2)}
.a1-gap{pointer-events:none}
.a1-circ .row{display:flex;align-items:center;gap:10px}.a1-avs{display:flex}.a1-avs .avatar{width:32px;height:32px;font-size:13px}
.a1-avs .avatar+.avatar{margin-left:-6px}.a1-avs .avatar img{width:100%;height:100%;border-radius:50%;object-fit:cover}
.a1-circ .a1-acts{display:flex;gap:8px;margin-top:12px}.a1-circ .a1-acts .btn{flex:1}
body.a1 #view [data-card]{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}`;

  // The Jarvis icon the dock already uses (Tabler "sparkles", MIT, see index.html)
  const SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CHEV = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';   // app.js ICON.chev
  const chev = () => `<span class="chev">${CHEV}</span>`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- asking Jarvis
  // A question the user tapped is sent at once (the half sheet's own quick-reply chip); for an AI outside the page (MCP, the
  // butler link) the page's usual path copies it.
  function ask(text) {
    const ai = window.BodyAI;
    if (ai?.handlesAsk()) {
      if (!ai.openHalf({ chips: [{ label: text, send: text }] })) return;   // false: the consent sheet came first
      requestAnimationFrame(() => $('#ai-chat .ai-q[data-ql="0"]')?.click());
      return;
    }
    if (P.canAskAI()) { P.askAI(text); return; }
    window.BodyAI?.openSettings();   // no AI yet: where to connect one
  }
  function photo() {   // the Jarvis key's own camera input (jarvis-entry.js)
    const cam = $("#jv-cam");
    if (cam && window.BodyAI?.isJarvis() && window.BodyAI.ensureJarvis(() => {})) cam.click();
    else ask(tr("我想拍照记一顿饭"));
  }
  function haptic() { if (window.Watch2WhoopFeatures?.haptics) { try { window.Watch2Whoop.postMessage("haptic:light"); } catch (e) { /* older app */ } } }

  // ---------------------------------------------------------------- features (全部功能 and the search field)
  const S = () => P.S;
  function goTab(tab, set) { if (set) set(S()); P.setTab(tab); }
  const toFood = (sub) => () => {
    const s = S();
    s.foodMode = sub === "shopping" ? "shopping" : "diet"; s.foodSub = sub === "recipes" ? "recipes" : null;
    if (P.friendsOn()) P.setTab("food"); else { s.recSub = "food"; P.setTab("record"); }
  };
  const FEATURES = () => [
    [tr("恢复"), [
      [tr("恢复与 HRV"), () => goTab("trends", (s) => { s.trendsMode = "metrics"; })],
      [tr("健康监测"), () => goTab("trends", (s) => { s.trendsMode = "metrics"; })],
      [tr("压力"), () => goTab("trends", (s) => { s.trendsMode = "metrics"; })],
      [tr("训练负荷"), () => goTab("trends", (s) => { s.trendsMode = "metrics"; })]]],
    [tr("睡眠"), [
      [tr("昨晚的睡眠"), () => P.goPage("sleep", {})],
      [tr("睡眠趋势"), () => goTab("trends", (s) => { s.trendsMode = "sleep"; })],
      [tr("入睡目标"), () => scrollToCard("sleep_goal")]]],
    [tr("训练"), [
      [tr("训练计划和记录"), () => goTab("record", (s) => { s.recSub = "training"; })],
      [tr("肌肉负荷"), () => goTab("record", (s) => { s.recSub = "training"; })]]],
    [tr("饮食"), [
      [tr("今天吃了"), toFood("diet")], [tr("喝水"), toFood("diet")], [tr("食谱"), toFood("recipes")], [tr("采购"), toFood("shopping")]]],
    [tr("身体"), [
      [tr("体重"), () => goTab("trends", (s) => { s.trendsMode = "metrics"; })],
      [tr("身体年龄"), () => P.goPage("healthspan")],
      [tr("周报 / 月报"), () => P.goPage("trends.report")]]],
    [tr("健康档案"), [
      [tr("化验"), () => goTab("me", (s) => { s.meSub = null; })],
      [tr("补剂"), () => P.goPage("supplements")],
      [tr("日记"), () => window.BodyJournal?.openDay(P.journalCtx(), P.viewDay())]]],
    ...(P.friendsOn() ? [[tr("圈子"), [[tr("圈子动态"), () => goTab("friends", (s) => { s.friendsMode = "feed"; })], [tr("邀请好友"), () => window.BodyCircles?.open()]]]] : []),
    [tr("设置"), [
      [tr("AI 设置"), () => window.BodyAI?.openSettings()],
      [tr("训练和饮食记录"), () => goTab("record", (s) => { s.recSub = "training"; })],
      [tr("数据状态"), () => P.goPage("data_status")],
      [tr("设置"), () => goTab("me", (s) => { s.meSub = "settings"; })]]],
  ];
  const flat = () => FEATURES().flatMap(([area, list]) => list.map(([name, go]) => ({ area, name, go })));
  const norm = (t) => String(t || "").toLowerCase().replace(/[\s·/]+/g, "");
  function matches(q) {
    const n = norm(q);
    return n ? flat().filter((f) => norm(f.name).includes(n) || norm(f.area) === n).slice(0, 5) : [];
  }
  function scrollToCard(key) {
    const go = () => { const el = $(`#view [data-card="${key}"]`); if (el) el.scrollIntoView({ block: "center", behavior: "smooth" }); };
    if (S().tab === "today") go(); else { P.setTab("today"); setTimeout(go, 600); }
  }

  // The search field: typing lists the matching features under it; Enter opens the only / exact match, otherwise asks Jarvis
  function askBar(example, full) {
    return `<form class="a1-ask" role="search" data-feature="ask_bar"><span class="a1-sp">${SPARK}</span>
      <input class="input" type="search" enterkeyhint="send" autocomplete="off" placeholder="${esc(full || tr("搜索，或问 Jarvis：{q}", { q: example }))}" aria-label="${esc(tr("搜索，或问 Jarvis"))}"></form>
      <div class="a1-hits group" hidden></div>`;
  }
  function bindAsk(root) {
    for (const form of $$(".a1-ask", root)) {
      const input = $("input", form), hits = form.nextElementSibling;
      const show = () => {
        const list = matches(input.value);
        hits.hidden = !list.length;
        hits.innerHTML = list.map((f, i) => `<button class="item" type="button" data-hit="${i}"><span class="row-title">${esc(f.name)}</span><span class="val">${esc(f.area)}</span>${chev()}</button>`).join("");
        for (const b of $$("[data-hit]", hits)) b.onclick = () => { input.blur(); list[Number(b.dataset.hit)].go(); };
      };
      input.oninput = show;
      form.onsubmit = (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (!q) return;
        const list = matches(q), exact = list.find((f) => norm(f.name) === norm(q));
        input.blur();
        if (exact || list.length === 1) (exact || list[0]).go();
        else { ask(q); input.value = ""; hits.hidden = true; }
      };
    }
  }
  const asksHtml = (qs) => (qs.length ? `<div class="a1-asks">${qs.map((q, i) => `<button class="chip" type="button" data-ask="${i}">${q.photo ? "" : SPARK}${esc(q.label)}</button>`).join("")}</div>` : "");
  function bindAsks(root, qs) {
    for (const b of $$("[data-ask]", root)) b.onclick = () => { const q = qs[Number(b.dataset.ask)]; if (q.photo) photo(); else ask(q.send || q.label); };
  }

  // Concrete questions from the user's own numbers (never the abstract "try the AI")
  function todayQuestions(d, self) {
    if (!self) return [];
    const t = d?.scores?.today || {}, qs = [];
    if (t.recovery != null) qs.push({ label: tr("为什么恢复 {v}？", { v: t.recovery }) });
    if (t.sleep_actual_min != null && t.sleep_need_min && t.sleep_need_min - t.sleep_actual_min >= 30) qs.push({ label: tr("为什么睡得比需要的少？") });
    qs.push({ label: tr("记一顿饭"), send: tr("帮我记一顿饭") });
    if ($("#jv-cam") && window.BodyAI?.isJarvis()) qs.push({ label: tr("拍照记饭"), photo: true });
    return qs.slice(0, 3);
  }

  // ---------------------------------------------------------------- Today
  // A small ring like the recovery one (same markup and stroke animation), for sleep and strain
  function ring(value, frac, color, label) {
    const r = 84, c = 2 * Math.PI * r;
    const arc = frac == null ? "" : `<circle class="arc" style="stroke:${color}" cx="98" cy="98" r="${r}" fill="none" stroke-width="12" stroke-linecap="round"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${c.toFixed(1)}" data-target="${(c * (1 - Math.max(0, Math.min(1, frac)))).toFixed(1)}" transform="rotate(-90 98 98)"/>`;
    return `<svg class="ring a1-sm" viewBox="0 0 196 196" role="img" aria-label="${esc(`${label} ${value}`)}"><circle class="track" cx="98" cy="98" r="${r}" fill="none" stroke-width="12"/>${arc}
      <text class="val" x="98" y="118" text-anchor="middle">${esc(value)}</text></svg>`;
  }
  const attrs = (el) => (el ? ["data-sheet", "data-card"].filter((a) => el.hasAttribute(a)).map((a) => `${a}="${esc(el.getAttribute(a))}"`).join(" ") : "");
  function rings(sc) {
    const box = document.createElement("div");
    box.innerHTML = P.scoresHtml(sc);
    const hero = $("button.hero", box);
    if (!hero || sc?.error) return box.innerHTML;   // no scores: the page's own message
    const tiles = $$(".tile", box), t = sc.today || {}, y = sc.yesterday || {};
    const hm = window.BodyCards.hm;
    const sleep = t.sleep_actual_min, strain = y.strain;
    const recRing = $("svg.ring", hero);
    recRing.classList.add("a1-lg");
    const word = [...hero.children].filter((c) => c !== recRing).map((c) => c.outerHTML).join("");
    return `<div class="a1-tri">
        <button ${attrs(tiles[0])}>${ring(hm(sleep) ?? "—", sleep != null && t.sleep_need_min ? sleep / t.sleep_need_min : null, "var(--sleep)", tr("睡眠"))}<span class="tile-label">${tr("睡眠")}</span></button>
        <button ${attrs(hero)}>${recRing.outerHTML}</button>
        <button ${attrs(tiles[2])}>${ring(strain != null ? Number(strain).toFixed(1) : "—", strain != null ? strain / 21 : null, "var(--train)", tiles[2] ? $(".tile-label", tiles[2]).textContent : tr("负荷"))}<span class="tile-label">${esc(tiles[2] ? $(".tile-label", tiles[2]).textContent : tr("负荷"))}</span></button>
      </div><div class="a1-word">${word}</div>${$(".chip-row", box)?.outerHTML || ""}`;
  }

  function circleCard(ov) {
    if (!ov) return "";
    const people = (ov.members || []).slice(0, 5);
    const av = (m) => { const n = m.remark || m.display_name || m.name || "?"; return `<span class="avatar">${m.avatar ? `<img src="${esc(m.avatar)}" alt="">` : esc(n.slice(0, 1))}</span>`; };
    const body = ov.has_circles && people.length
      ? `<div class="row"><span class="a1-avs">${people.map(av).join("")}</span><span class="tile-label">${esc(tr("{n} 位朋友", { n: (ov.members || []).length }))}</span></div>
         <div class="a1-acts"><button class="btn btn-primary" type="button" data-a1="feed">${tr("看动态")}</button><button class="btn" type="button" data-a1="invite">${tr("邀请好友")}</button></div>`
      : `<div class="row"><span class="tile-label" style="white-space:normal">${ov.has_circles ? tr("圈子里还没有朋友，邀请他们一起看恢复和排行") : tr("和朋友一起看恢复、训练和排行")}</span></div>
         <div class="a1-acts"><button class="btn btn-primary" type="button" data-a1="invite">${tr("邀请好友")}</button>${ov.has_circles ? `<button class="btn" type="button" data-a1="feed">${tr("看动态")}</button>` : `<button class="btn" type="button" data-a1="join">${tr("输入邀请码")}</button>`}</div>`;
    return `<div class="section"><div class="section-head"><h2>${tr("圈子")}</h2></div><div class="card a1-circ" data-card="circles">${body}</div></div>`;
  }

  async function today() {
    const s = S();
    if (s.sleepNight) return null;   // the sleep detail page stays as it is
    const day = P.viewDay(), past = P.isPast(), self = P.isSelf();
    const own = `/v1/users/${P.ownerPath()}`, v2 = P.recordsV2(), mload = P.muscleOn() && !past;
    // The same reads as the old Today minus stress and the activity row (they are in 身体), plus the circles card: no more requests
    const [d, goal, meals, coach, monitor, sleepCoach, jitems, jday, mdata, ov] = await Promise.all([
      P.load(P.todayKey(), P.todayPath()),
      (past ? P.load(`goal${day}`, `${own}/goals/sleep?night=${day}`) : P.load("goal", `${own}/goals/sleep`)).catch((e) => ({ error: e })),
      self && !past ? P.load("meals", `${own}/meal-templates`).catch(() => null) : null,
      P.optional(`coach${day}`, P.WHOOP_API.strainCoach(day)),
      P.optional(`monitor${day}`, P.WHOOP_API.monitor(day)), P.optional(`sleepcoach${day}`, P.WHOOP_API.sleepCoach(day)),
      v2 ? P.optional("jitems", `${own}/journal/items?active_only=true`) : null,
      v2 ? P.optional(`jday${day}`, `${own}/journal?start=${day}&end=${day}`) : null,
      mload ? P.optional("mload", `${own}/training/muscle-load`) : null,
      self && !past && P.friendsOn() ? P.optional("a1friends", "/v1/friends/overview") : null,
    ]);
    s.todaySleepDate = d.sleep?.date || null;   // the night the sleep ring opens on the sleep detail page
    if (mdata) await window.BodyMuscleLoad.ready();
    const qs = todayQuestions(d, self && !past);
    state.qs = qs;
    const focus = P.remindersHtml(d.reminders).replace(`<h2>${tr("提醒")}</h2>`, `<h2>${tr("重点")}</h2>`);
    // My modules: the page's own cards, at most MAX_MODULES, in the order Today had them
    const mods = [
      P.monitorHtml(monitor), P.strainCoachHtml(coach), P.sleepCardHtml(d.sleep), P.goalCardHtml(goal, d.sleep, sleepCoach),
      P.dayCardHtml(d, meals), mdata ? window.BodyMuscleLoad.card(mdata, P.muscleCtx()) : "",
      v2 ? window.BodyJournal.cardHtml(P.journalCtx(), day, jitems && jday ? { items: jitems, entries: jday } : null) : "",
    ].filter(Boolean).slice(0, MAX_MODULES);
    const all = self ? `<div class="section"><div class="group"><button class="item" type="button" data-a1="all"><span class="row-title">${tr("全部功能")}</span><span class="val">${tr("随时能看")}</span>${chev()}</button></div></div>` : "";
    return `<div class="dash"><div class="col">${rings(d.scores)}${self && !past ? askBar(qs.find((q) => !q.send && !q.photo)?.label || tr("今天练什么？")) + asksHtml(qs) : ""}
      ${focus}${circleCard(ov)}${mods.join("")}${all}</div></div>`;
  }
  const state = { qs: [] };

  // ---------------------------------------------------------------- 全部功能 (我 → 全部功能)
  function allPage() {
    return `<div class="sub-head"><button class="sub-back" type="button" data-a1="me">${CHEV}<span>${tr("tab.me")}</span></button><h2>${tr("全部功能")}</h2></div>
      ${askBar(tr("怎么记喝水？"))}
      ${FEATURES().map(([area, list], gi) => `<div class="section"><div class="section-head"><h2>${esc(area)}</h2></div><div class="group">${list.map(([name], i) =>
        `<button class="item" type="button" data-feat="${gi}.${i}"><span class="row-title">${esc(name)}</span>${chev()}</button>`).join("")}</div></div>`).join("")}`;
  }

  // ---------------------------------------------------------------- the page hooks (app.js render / after)
  async function render(tab) {
    if (tab === "today") return today();
    if (tab === "me" && S().meSub === "all") return allPage();
    return null;
  }

  function after(tab, view) {
    tabs();
    document.body.classList.add("a1");
    for (const b of $$("[data-a1]", view)) b.onclick = () => action(b.dataset.a1);
    if (tab === "today") {
      for (const arc of $$(".a1-tri .ring .arc[data-target]", view)) requestAnimationFrame(() => requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", arc.dataset.target)));
      bindAsk(view); bindAsks(view, state.qs);
    } else if (tab === "trends") {
      const bar = $(".toolbar", view);
      if (bar && P.isSelf()) { bar.insertAdjacentHTML("afterend", askBar(tr("这周为什么恢复低？"))); bindAsk(view); }
    } else if (tab === "me" && S().meSub === "all") {
      bindAsk(view);
      const list = FEATURES();
      for (const b of $$("[data-feat]", view)) b.onclick = () => { const [g, i] = b.dataset.feat.split(".").map(Number); list[g][1][i][1](); };
    } else if (tab === "me" && !S().meSub) {
      $("#me-intro", view)?.remove();   // no tutorial
      if (P.isSelf() && view.firstElementChild) {
        view.firstElementChild.insertAdjacentHTML("afterend", `${askBar("", tr("搜设置和功能，或问 Jarvis"))}<div class="section" style="margin-top:12px"><div class="group">
          <button class="item" type="button" data-a1="all"><span class="row-title">${tr("全部功能")}</span>${chev()}</button>
          <button class="item" type="button" data-a1="records"><span class="row-title">${tr("训练和饮食记录")}</span>${chev()}</button></div></div>`);
        for (const b of $$("[data-a1]", view)) b.onclick = () => action(b.dataset.a1);
        bindAsk(view);
      }
    } else if (tab === "record" || tab === "food") {
      if (!$(".a1-back", view)) view.insertAdjacentHTML("afterbegin", `<div class="sub-head a1-back"><button class="sub-back" type="button" data-a1="today">${CHEV}<span>${tr("tab.today")}</span></button></div>`);
      $(".a1-back [data-a1]", view).onclick = () => action("today");
      for (const b of $$(".tab", $(".tabbar"))) b.setAttribute("aria-current", b.dataset.tab === "today" ? "page" : "false");
    }
  }

  function action(a) {
    const s = S();
    if (a === "all") { if (s.tab === "me") { s.meSub = "all"; P.render(); } else { P.setTab("me"); s.meSub = "all"; P.render(); } }
    else if (a === "me") { s.meSub = null; P.render(); }
    else if (a === "today") P.setTab("today");
    else if (a === "records") goTab("record", (x) => { x.recSub = "training"; });
    else if (a === "feed") goTab("friends", (x) => { x.friendsMode = "feed"; });
    else if (a === "invite" || a === "join") window.BodyCircles?.open();
  }

  // Tab bar: 今天 · 身体 · (Jarvis over the gap) · 圈子 · 我
  function tabs() {
    const nav = $(".tabbar");
    if (!nav) return;
    for (const k of ["record", "food"]) { const b = $(`.tab[data-tab="${k}"]`, nav); if (b) b.hidden = true; }
    if (!$(".a1-gap", nav)) {
      const friends = $('.tab[data-tab="friends"]', nav), meTab = $('.tab[data-tab="me"]', nav);
      const gap = Object.assign(document.createElement("span"), { className: "a1-gap" });
      gap.setAttribute("aria-hidden", "true");
      nav.insertBefore(gap, friends && !friends.hidden ? friends : meTab);
      if (meTab) nav.append(meTab);
      for (const [k, label] of [["trends", tr("身体")], ["friends", tr("圈子")]]) {
        const span = $(`.tab[data-tab="${k}"] span`, nav);
        if (span) { span.removeAttribute("data-i18n"); span.textContent = label; }
      }
    }
  }

  // ---------------------------------------------------------------- long press: "ask Jarvis about this" on any data card
  const QUESTION = {
    recovery: (v) => tr("为什么我今天恢复 {v}？", { v }), sleep: () => tr("我昨晚睡得怎么样？"), strain: (v) => tr("负荷 {v} 算高吗？", { v }),
    sleep_goal: () => tr("今晚几点睡最好？"), health_monitor: () => tr("我的健康监测有什么要注意的？"), strain_coach: () => tr("今天还该练多少？"),
    training: () => tr("今天该练什么？"), nutrition: () => tr("今天还该吃什么？"), training_load: () => tr("我最近是不是练多了？"),
    stress: () => tr("我今天压力大吗？"), reminders: () => tr("先做哪件事？"), circles: () => tr("我这周在圈里排第几？"),
  };
  function cardInfo(el) {
    const key = el.dataset.card || "";
    const sec = el.closest(".section");
    const label = ($(".tile-label, .trend-label", el) || (sec && $(".section-head h2", sec)) || $(".row-title", el))?.textContent.trim() || "";
    const value = ($(".tile-value, .trend-value, .val", el))?.textContent.trim() || "";
    return { key, label: label.slice(0, 30), value: value.slice(0, 20) };
  }
  function question(info) {
    const base = info.key.split(":")[0];
    if (QUESTION[base]) return QUESTION[base](info.value);
    return info.value ? tr("「{label}」{value}，这说明什么？", info) : tr("「{label}」是什么意思？", info);
  }
  function menu(el) {
    const info = cardInfo(el), q = question(info);
    haptic();
    P.openSheet(info.label || tr("问 Jarvis"), `<div class="group">
        <button class="item" type="button" data-m="ask"><span class="a1-sp">${SPARK}</span><span class="row-title">${esc(tr("问 Jarvis：{q}", { q }))}</span>${chev()}</button>
        ${el.hasAttribute("data-sheet") || el.tagName === "BUTTON" ? `<button class="item" type="button" data-m="open"><span class="row-title">${tr("看详情")}</span>${chev()}</button>` : ""}
      </div>`, (root) => {
      $('[data-m="ask"]', root).onclick = () => { P.closeSheet(); ask(q); };
      const o = $('[data-m="open"]', root);
      if (o) o.onclick = () => { P.closeSheet(); setTimeout(() => el.click(), 250); };
    });
  }
  const lp = { t: 0, x: 0, y: 0, el: null, fired: false };
  function bindLongPress() {
    const cancel = () => { clearTimeout(lp.t); lp.el = null; };
    document.addEventListener("pointerdown", (e) => {
      if (!on()) return;
      const el = e.target.closest?.("#view [data-card]");
      if (!el || e.button) return;
      lp.el = el; lp.x = e.clientX; lp.y = e.clientY; lp.fired = false;
      clearTimeout(lp.t);
      lp.t = setTimeout(() => { if (lp.el === el) { lp.fired = true; menu(el); } }, LONG_PRESS_MS);
    }, { passive: true });
    document.addEventListener("pointermove", (e) => { if (lp.el && Math.hypot(e.clientX - lp.x, e.clientY - lp.y) > 10) cancel(); }, { passive: true });
    for (const ev of ["pointerup", "pointercancel"]) document.addEventListener(ev, () => { clearTimeout(lp.t); }, { passive: true });
    window.addEventListener("scroll", cancel, { passive: true });
    // the click that ends a long press does not also open the card
    document.addEventListener("click", (e) => { if (lp.fired) { lp.fired = false; e.preventDefault(); e.stopPropagation(); } }, true);
    document.addEventListener("contextmenu", (e) => {
      if (!on()) return;
      const el = e.target.closest?.("#view [data-card]");
      if (!el) return;
      e.preventDefault(); clearTimeout(lp.t); lp.fired = false; menu(el);
    });
  }
  const on = () => !!P && (P.S.flags || []).includes("ui_ai_first");

  // ---------------------------------------------------------------- start
  window.BodyAIFirst = { render, after, _test: { matches: (q) => matches(q).map((f) => f.name), question, todayQuestions } };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init() {
      P = window.BodyPage;
      if (!on()) return;
      if (!$("#a1-css")) document.head.append(Object.assign(document.createElement("style"), { id: "a1-css", textContent: CSS }));
      P.TABS && Object.assign(P.TABS, { trends: tr("身体"), friends: tr("圈子") });   // the header shows the new names too
      tabs();
      bindLongPress();
    },
  });
})();
