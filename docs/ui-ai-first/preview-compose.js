/* Preview of the AI-first information architecture (#279, docs/ui-ai-first-redesign.md v0.4) in the CURRENT visual style.
   Injected by preview.py into the real Today page (quickstart, synthetic data). It does not draw anything new: it takes the
   components the real app.js rendered (rings, cards, sections, rows) and rearranges them; the few new pieces (重点 list,
   圈子 card, 全部功能 list) use only class names that already exist in web/static/app.css. Nothing here ships. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const CHEV = '<span class="chev"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';
  // the Jarvis icon the dock already uses (Tabler "sparkles", see index.html)
  const SPARK = `<span class="pv-sp"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
  // familiar entry points into the AI: a search field that is also "ask Jarvis", and concrete example questions as chips
  const askBar = (ph) => `<div class="input pv-ask">${SPARK}<span>${ph}</span></div>`;
  const asks = (...qs) => `<div class="chip-row pv-asks">${qs.map((q) => `<button class="chip">${SPARK}${q}</button>`).join("")}</div>`;
  const badge = (tone, t) => `<span class="badge ${tone}" style="margin-left:6px;vertical-align:2px">${t}</span>`;

  // three rings side by side: the recovery ring is the real one; sleep and strain are copies of it with the existing colours
  const style = document.createElement("style");
  style.textContent = `.pv-tri { display: grid; grid-template-columns: 92px 1fr 92px; align-items: center; gap: 4px; padding: 10px 0 0; }
    .pv-tri > div { display: flex; justify-content: center; } .pv-tri .ring.pv-sm { width: 92px; height: 92px; } .pv-tri .ring.pv-lg { width: 164px; height: 164px; }
    .pv-tri .ring.pv-sm .val { font-size: 54px; } .pv-gap { pointer-events: none; }
    .pv-ask { display: flex; align-items: center; gap: 8px; color: var(--text-3); margin-top: 14px; }
    .pv-sp svg { width: 16px; height: 16px; display: block; color: var(--text-2); }
    .pv-asks { flex-wrap: wrap; gap: 6px; margin-top: 10px; } .pv-asks .chip { display: inline-flex; align-items: center; gap: 4px; }`;
  document.head.appendChild(style);

  // tab bar: 今天 · 身体 · (Jarvis) · 圈子 · 我; 记录 and 饮食 are no longer tabs
  function tabs(cur) {
    const nav = $(".tabbar"), t = (k) => $(`[data-tab="${k}"]`, nav);
    t("food").hidden = true; t("record").hidden = true; t("friends").hidden = false;
    $("span", t("trends")).textContent = "身体"; $("span", t("friends")).textContent = "圈子";
    let gap = $(".pv-gap", nav);
    if (!gap) { gap = document.createElement("span"); gap.className = "pv-gap"; }
    nav.insertBefore(gap, t("friends")); nav.append(t("me"));
    for (const b of $$(".tab", nav)) b.setAttribute("aria-current", b.dataset.tab === cur ? "page" : "false");
  }
  const title = (t) => { $("#hdr-title").textContent = t; };

  function ringCopy(src, value, cap, frac, color, cls) {
    const s = src.cloneNode(true);
    s.classList.add(cls);
    const arc = $(".arc", s), C = 2 * Math.PI * 84;
    arc.removeAttribute("class"); arc.classList.add("arc"); arc.style.stroke = color;
    arc.setAttribute("stroke-dasharray", C.toFixed(1)); arc.setAttribute("stroke-dashoffset", (C * (1 - frac)).toFixed(1));
    $(".val", s).textContent = value; $(".cap", s).textContent = cap;
    return s.outerHTML;
  }

  // 今天: rings on top, then 重点, 圈子, the modules on my Today (each with why it is there), 全部功能
  function today() {
    const v = $("#view"), src = v.cloneNode(true);
    const ring = $("button.hero svg.ring", src), rec = $("button.hero svg.ring .val", src)?.textContent || "—";
    const recArc = $("button.hero .arc", src);
    const recFrac = Number(rec) / 100;
    const tiles = $$(".tile .tile-value", src).map((x) => x.textContent.trim());
    const sleepTxt = tiles[0] || "—", strainTxt = tiles[2] || "—";
    const [h, m] = sleepTxt.split(":").map(Number);
    const tri = `<div class="pv-tri"><div>${ringCopy(ring, sleepTxt, "睡眠", Math.min(1, (h * 60 + m) / 480 || 0), "var(--sleep)", "pv-sm")}</div>
      <div>${ringCopy(ring, rec, "恢复", recFrac, `var(--${["good", "mid", "bad"].find((z) => recArc?.classList.contains(`stroke-${z}`)) || "good"})`, "pv-lg")}</div>
      <div>${ringCopy(ring, strainTxt, "负荷", Math.min(1, Number(strainTxt) / 21 || 0), "var(--train)", "pv-sm")}</div></div>`;
    const word = $("button.hero .hero-word", src)?.outerHTML || "";
    const chip = $(".chip-row", src)?.outerHTML || "";
    const rems = $$(".card.rem", src).slice(0, 2).map((x) => x.outerHTML);
    const coach = $$(".card.rem", src)[0]?.cloneNode(true);
    if (coach) { $(".row-title", coach).textContent = "今天负荷目标 14–18，现在 0"; rems.push(coach.outerHTML); }
    const focus = `<div class="section"><div class="section-head"><h2>重点</h2><span class="small muted">${rems.length} 件</span></div>${rems.join("")}</div>`;
    const avatars = ["鹿", "木", "K"].map((t) => `<span class="avatar" style="width:34px;height:34px;font-size:14px">${t}</span>`).join("");
    const circle = `<div class="section"><div class="section-head"><h2>圈子</h2><span class="small muted">还没有圈子</span></div>
      <div class="card"><div class="row" style="display:flex;align-items:center;gap:10px"><span style="display:flex;gap:4px;opacity:.55">${avatars}</span>
        <span class="tile-label" style="white-space:normal">和朋友一起看恢复、训练和排行</span></div>
        <div style="display:flex;gap:8px;margin-top:12px"><button class="btn btn-primary">邀请好友</button><button class="btn">输入邀请码</button></div></div></div>`;
    const sec = (h2) => $$(".section", src).find((s) => $(".section-head h2", s)?.textContent.trim().startsWith(h2));
    const withWhy = (el, why) => { if (!el) return ""; const c = el.cloneNode(true); const h = $(".section-head h2", c); if (h) h.insertAdjacentHTML("beforeend", why); return c.outerHTML; };
    const monitor = $$("button.card", src).find((b) => b.textContent.includes("健康监测"));
    const mods = [
      withWhy(sec("入睡目标"), badge("plain", "关注")),
      withWhy(sec("睡眠"), badge("plain", "关注")).replace(/<\/div>$/, `${asks("为什么深睡比平时少？")}</div>`),
      monitor ? `<div class="section"><div class="section-head"><h2>健康监测${badge("plain", "发现")}</h2></div>${monitor.outerHTML}</div>` : "",
      withWhy(sec("训练与饮食"), badge("plain", "用过")),
      withWhy(sec("肌肉负荷"), badge("plain", "用过")),
    ].join("");
    const head = `<div class="section"><div class="section-head"><h2>我的首页</h2><span class="small muted">5 个 · 按你的关注</span></div></div>`;
    const all = `<div class="section"><div class="group"><button class="item"><span class="row-title">全部功能</span><span class="val">24 个，随时能看</span>${CHEV}</button></div></div>`;
    v.innerHTML = `<div class="dash"><div class="col">${tri}<div style="text-align:center">${word}</div>${chip}
      ${askBar("搜索，或问 Jarvis：今天练什么？")}${asks(`为什么恢复 ${rec}？`, "记一顿饭", "拍照记饭")}${focus}${circle}${head.replace('<div class="section">', '<div class="section" style="margin-bottom:-18px">')}${mods}${all}</div></div>`;
    title("今天"); tabs("today");
  }

  // 身体 = the real trends page; the user's focus (恢复, 睡眠) stays open, the other groups are folded
  function body() {
    for (const g of ["strain", "stress", "long", "behavior"]) {
      const b = $(`[data-tg-toggle="${g}"]`);
      if (b && b.getAttribute("aria-expanded") === "true") b.click();
    }
    const bar = $("#view .toolbar");
    if (bar && !$(".pv-ask", $("#view"))) bar.insertAdjacentHTML("afterend", askBar("搜指标，或问：这周为什么恢复低？"));
    title("身体"); tabs("trends");
  }

  function circles() {
    const bar = $("#view .toolbar");
    if (bar && !$(".pv-asks", $("#view"))) bar.insertAdjacentHTML("afterend", asks("我这周在圈里排第几？", "邀请好友一起练"));
    title("圈子"); tabs("friends");
  }

  function me() {
    const v = $("#view");
    if (!$("#pv-all", v)) v.firstElementChild.insertAdjacentHTML("afterend", askBar("搜设置和功能，或问 Jarvis") + `<div class="section" style="margin-top:12px"><div class="group"><button class="item" id="pv-all"><span class="row-title">全部功能</span><span class="val">24 个</span>${CHEV}</button><button class="item"><span class="row-title">全部记录</span>${CHEV}</button></div></div>`);
    title("我"); tabs("me");
  }

  // 全部功能: every module by area with its state on this account (首页 / 看过 / 新); nothing locked
  function all() {
    const ST = { home: ["good", "首页"], seen: ["plain", "看过"], new: ["mid", "新"] };
    const areas = [
      ["恢复", [["HRV · 静息心率", "seen"], ["负荷目标", "seen"], ["训练负荷", "new"], ["健康监测", "home"], ["压力", "seen"]]],
      ["睡眠", [["睡眠", "home"], ["入睡目标", "home"], ["睡眠规律", "new"]]],
      ["训练", [["训练与饮食", "home"], ["肌肉负荷", "home"], ["活动", "seen"], ["VO₂max", "new"]]],
      ["饮食", [["饮食", "seen"], ["喝水", "new"], ["食谱", "new"], ["采购", "new"]]],
      ["身体", [["体重", "seen"], ["身体年龄", "seen"], ["周报", "new"], ["行为影响", "new"], ["深入指标", "seen"]]],
      ["健康档案", [["化验", "new"], ["补剂", "new"], ["日记", "seen"]]],
    ];
    const row = ([n, s]) => `<button class="item"><span class="row-title">${n}</span><span class="badge ${ST[s][0]}">${ST[s][1]}</span>${CHEV}</button>`;
    $("#view").innerHTML = `<div class="sub-head"><button class="sub-back">${CHEV.replace('<span class="chev">', "").replace("</span>", "")}<span>我</span></button><h2>全部功能</h2></div>
      ${askBar("搜功能，或直接问：怎么记喝水？")}
      <div class="section" style="margin-top:12px"><div class="group"><div class="item"><span class="row-title">Jarvis 自动整理首页</span><span class="val">开</span></div></div></div>
      ${areas.map(([a, ms]) => `<div class="section"><div class="section-head"><h2>${a}</h2><span class="small muted">${ms.length}</span></div><div class="group">${ms.map(row).join("")}</div></div>`).join("")}`;
    title("我"); tabs("me");
  }

  // 饮食 as a second-level page (opened from the 饮食 module): the real food page under a back link
  function food() {
    const v = $("#view");
    const rseg = $$(".toolbar", v).find((t) => $("[data-rsub]", t));
    if (rseg) rseg.remove();
    if (!$(".sub-head", v)) v.insertAdjacentHTML("afterbegin", `<div class="sub-head"><button class="sub-back">${CHEV.replace('<span class="chev">', "").replace("</span>", "")}<span>今天</span></button><h2>饮食</h2></div>${asks("拍照记一餐", "晚饭吃什么能补蛋白？")}`);
    title("今天"); tabs("today");
  }

  function sheet(titleText, html) {
    const sh = $("#sheet");
    $("#sheet-title").textContent = titleText; $("#sheet-body").innerHTML = html;
    sh.hidden = false; sh.classList.add("open");
  }
  function day1() {
    const find = (area, big, unit, label, tone, t) => `<div class="card" style="margin-top:8px"><div class="row" style="display:flex;justify-content:space-between;align-items:center">
      <span class="tile-label">${area}</span><span class="badge ${tone}">${t}</span></div>
      <div class="tile-value" style="margin-top:4px">${big}<small>${unit}</small></div><div class="small muted" style="margin-top:2px">${label}</div></div>`;
    const qs = ["减脂", "增肌", "睡得更好", "训练和恢复", "健康指标"].map((t) => `<button class="chip">${t}</button>`).join("");
    sheet("你的身体概况", `<div class="small muted">已读入你的 Apple 健康历史 · 60 天</div>
      ${find("恢复", "66", "% 30 天均值", "比上个月 +11", "good", "变好")}
      ${find("睡眠", "7:06", "", "深睡 1:28，比你平时少", "mid", "注意")}
      ${find("负荷", "17.8", "/ 21 昨天", "连续 3 天偏高", "mid", "注意")}
      <div class="section-head" style="margin-top:18px"><h2>你最关心哪些？</h2><span class="small muted">可多选</span></div>
      <div class="chip-row pv-asks" style="justify-content:flex-start">${qs}</div>
      <div class="btns"><button class="btn btn-primary">好了</button><button class="btn">先跳过</button></div>`);
  }
  // long press (or tap the ⋯) on anything: the same short menu everywhere, "ask Jarvis about this" first
  function longpress() {
    sheet("睡眠 · 7:06", `<div class="group">
      <button class="item"><span class="pv-sp">${SPARK}</span><span class="row-title">问 Jarvis：为什么比平时睡得少？</span>${CHEV}</button>
      <button class="item"><span class="row-title">看 30 天趋势</span>${CHEV}</button>
      <button class="item"><span class="row-title">分享到圈子</span>${CHEV}</button>
      <button class="item"><span class="row-title">从首页移走</span></button></div>`);
  }
  window.PV = { tabs, today, body, circles, me, all, food, day1, longpress };
})();
