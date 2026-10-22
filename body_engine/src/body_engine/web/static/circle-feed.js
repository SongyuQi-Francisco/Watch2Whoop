/* Circle training feed (body_engine/friends/feed.py, flag circle_feed): the Friends tab's 「动态」 segment — a Hevy / Instagram
   style post per person per day — laid out like a Hevy post (header, training name, time / energy / volume / sets / PR row, per
   watch workout a line of average / max heart rate, distance and load with a heart-rate zone bar (#194), the first 3 exercises
   with a picture, muscle icon or dumbbell, "view N more", tap an exercise for its sets) with Instagram-style line icons
   (heart fills red with a bounce, double-tap to like, "N likes", "view all N comments", the newest comments as a preview);
   comments (delete your own, report others'), newest first, more when you scroll down, refreshed every minute while open, and a
   red dot on the Friends tab / the segment when someone liked or commented on my post since I last looked.
   Posts are made by the server for people who ticked the training scope; there is no way to delete or edit one.
   Separate from app.js: window.BodyCircleFeed { shell(), bind(view, active), count() } + BodyPlugins init.
   A notification link /app/?tab=friends&feed=<post id> (notify.js fires "body:link") opens 「动态」 at that post, comments open.
   Endpoints: GET /v1/friends/feed?before=&limit=, GET …/unread, POST …/seen, PUT|DELETE …/posts/{id}/like,
   GET|POST …/posts/{id}/comments, DELETE …/comments/{id}, POST …/comments/{id}/report. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => window.I18N.t(k, p);
  const POLL_MS = 60000;
  let C = null;
  let unread = 0;
  let timer = null;
  let focus = null;   // post to scroll to once the feed is drawn (from a notification link)
  const st = { posts: [], next: null, loading: false, open: new Set(), more: new Set(), sets: new Set(), pop: null, burst: null, comments: {}, comment_max: 300, preview: 3, preview_comments: 2, loaded: false, err: null };

  const CSS = `.cf-list{display:flex;flex-direction:column;gap:12px}
.cf-post{position:relative;background:var(--surface);border-radius:16px;padding:14px 14px 6px;min-width:0}
.cf-head{display:flex;align-items:center;gap:10px}.cf-av{flex:none;width:38px;height:38px;border-radius:50%;background:var(--surface-2);display:flex;align-items:center;justify-content:center;font-weight:600;font-size:15px}
.cf-circ{display:inline-flex;align-items:center;gap:4px;vertical-align:bottom}.cf-circ .cc-av{font-size:8px}
.cf-who{flex:1;min-width:0}.cf-who b{display:block;font-size:15px;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cf-who i{display:block;font-style:normal;font-size:12.5px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf-title{font-size:18px;font-weight:700;margin:12px 0 10px;line-height:1.25;overflow-wrap:anywhere}
.cf-stats{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 18px;margin:0 0 12px}.cf-stat{flex:none}.cf-stat small{display:block;font-size:12px;color:var(--text-2)}
.cf-stat b{display:block;font-size:18px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}.cf-stat b em{font-style:normal;font-size:13px;font-weight:500;color:var(--text-2);margin-left:2px}
.cf-wk{margin:-2px 0 12px}.cf-wk+.cf-wk{margin-top:-4px}.cf-wkn{font-size:13px;font-weight:600;margin:0 0 4px}
.cf-chips{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px;font-variant-numeric:tabular-nums}.cf-chips span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.cf-chips svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;color:var(--text-2)}
.cf-chips em{font-style:normal;font-size:12px;color:var(--text-2)}
.cf-zb{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--surface-2);margin-top:8px}.cf-zb span{display:block;height:100%;background:var(--train)}
.cf-zl{display:flex;justify-content:space-between;gap:4px;margin-top:4px;font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
.cf-none{display:flex;align-items:center;gap:8px;border-top:1px solid var(--hair);min-height:44px;font-size:14px;color:var(--text-2)}.cf-none svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
.cf-exs{border-top:1px solid var(--hair)}
.cf-exb{display:flex;align-items:center;gap:12px;width:100%;min-height:56px;padding:6px 0;border:0;background:none;color:var(--text);text-align:left;font:inherit}
.cf-th{flex:none;width:44px;height:44px;border-radius:10px;background:var(--surface-2);overflow:hidden;display:flex;align-items:center;justify-content:center}
.cf-th img{width:100%;height:100%;object-fit:cover;display:block}
.cf-th .mm{padding:0;border:0;gap:0}.cf-th .mm-view{margin:0}.cf-th .mm-view svg{width:44px;height:44px}.cf-th .mm-off{fill:var(--text-3);fill-opacity:.3}.cf-th .mm-view figcaption{display:none}.cf-th.back .mm-view:first-child,.cf-th:not(.back) .mm-view:last-child{display:none}
.cf-ext{flex:1;min-width:0;font-size:15px;line-height:1.3;overflow-wrap:anywhere}.cf-ext span{color:var(--text-2);margin-right:6px;font-variant-numeric:tabular-nums}
.cf-pr{flex:none;display:inline-flex;background:var(--mid-soft);color:var(--mid);border-radius:8px;padding:3px 7px}.cf-pr svg,.cf-tro svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.cf-tro{display:inline-flex;vertical-align:-1px;margin-right:5px;color:var(--mid)}.cf-ic{color:var(--text-2)}.cf-ic svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
.cf-chev{flex:none;color:var(--text-3);transition:transform .15s}.cf-exb[aria-expanded="true"] .cf-chev{transform:rotate(180deg)}
.cf-sets{margin:0 0 8px 56px;font-size:14px;font-variant-numeric:tabular-nums}.cf-sets div{display:flex;gap:14px;padding:3px 0}.cf-sets i{flex:none;width:18px;font-style:normal;color:var(--text-2);text-align:right}
.cf-toggle{display:flex;align-items:center;min-height:44px;border:0;border-top:1px solid var(--hair);background:none;color:var(--text-2);font-size:14px;padding:0;width:100%}
.cf-act{display:flex;align-items:center;gap:2px;margin:0 -8px}
.cf-act button{border:0;background:none;color:var(--text);width:44px;height:44px;padding:0;display:inline-flex;align-items:center;justify-content:center;border-radius:12px;-webkit-tap-highlight-color:transparent}
.cf-act svg{width:26px;height:26px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;display:block}
.cf-act [data-cf="like"][aria-pressed="true"]{color:#f0364b}.cf-act [data-cf="like"][aria-pressed="true"] svg{fill:currentColor}
.cf-act .pop svg{animation:cf-pop .38s ease-out}
.cf-likes{font-size:14px;font-weight:600;margin:0 0 2px}
.cf-all{display:block;border:0;background:none;color:var(--text-2);font-size:14px;padding:6px 0;min-height:32px}
.cf-pv{font-size:14px;margin:0 0 2px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}.cf-pv b{font-weight:600;margin-right:6px}
.cf-cm{margin-top:4px}.cf-c{display:flex;gap:8px;align-items:flex-start;padding:5px 0;font-size:14px}
.cf-c .t{flex:1;min-width:0;overflow-wrap:anywhere}.cf-c .t b{font-weight:600;margin-right:6px}
.cf-c button{flex:none;border:0;background:none;color:var(--text-3);font-size:12px;padding:12px 4px;margin:-8px 0}
.cf-new{display:flex;gap:8px;margin:6px 0 8px}.cf-new input{flex:1;min-width:0;border:0;border-radius:22px;background:var(--surface-2);color:var(--text);font-size:16px;padding:0 14px;min-height:44px}
.cf-new .btn{flex:none;min-height:44px;padding:0 16px}
.cf-end{height:8px}
.cf-burst{position:absolute;left:50%;top:42%;width:92px;height:92px;margin:-46px 0 0 -46px;color:#f0364b;pointer-events:none;opacity:0;animation:cf-burst .7s ease-out}
.cf-burst svg{width:100%;height:100%;fill:currentColor;filter:drop-shadow(0 2px 8px rgba(0,0,0,.25))}
@keyframes cf-pop{0%{transform:scale(1)}30%{transform:scale(1.4)}60%{transform:scale(.9)}100%{transform:scale(1)}}
@keyframes cf-burst{0%{opacity:0;transform:scale(.4)}25%{opacity:.95;transform:scale(1.15)}60%{opacity:.95;transform:scale(1)}100%{opacity:0;transform:scale(1.1)}}
@media (prefers-reduced-motion:reduce){.cf-act .pop svg{animation:none}.cf-burst{animation-duration:.01s}}
.cf-more{display:block;margin:12px auto;border:0;background:none;color:var(--text-2);font-size:13px;text-decoration:underline;min-height:44px}
.cf-empty{text-align:center;color:var(--text-2);padding:32px 12px;font-size:14px}
.cf-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--bad);margin-left:5px;vertical-align:middle}
.tab .cf-dot{position:absolute;margin:-18px 0 0 14px}
@media (min-width:900px){.cf-list{max-width:620px}}`;
  function injectCss() {
    if ($("#cf-css")) return;
    document.head.append(Object.assign(document.createElement("style"), { id: "cf-css", textContent: CSS }));
  }

  // ---- text helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 0) => window.I18N.num(v, d);
  function dayText(iso) {
    const d = new Date(`${iso}T12:00:00`), now = new Date();
    const diff = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
    return diff === 0 ? tr("今天") : diff === 1 ? tr("昨天") : window.I18N.dayLabel(d);
  }
  const kg = (v) => `${num(v, v % 1 ? 1 : 0)} kg`;
  function setText(s) {
    if (s.duration_s && !s.reps) return `${num(s.duration_s)} s`;
    return s.weight_kg ? `${num(s.weight_kg, s.weight_kg % 1 ? 1 : 0)} kg × ${s.reps ?? "—"}` : `× ${s.reps ?? "—"}`;
  }

  // ---- drawing
  function commentsHtml(p) {
    const list = st.comments[p.id];
    if (!list) return `<div class="cf-cm"><p class="muted">${esc(tr("加载中…"))}</p></div>`;
    const rows = list.map((c) => `<div class="cf-c"><span class="t"><b>${esc(c.user.name)}</b>${esc(c.body)}</span>
      <button type="button" data-cf="${c.user.self ? "cdel" : "crep"}" data-c="${c.id}" data-p="${p.id}">${esc(c.user.self ? tr("删除") : tr("举报"))}</button></div>`).join("");
    return `<div class="cf-cm">${rows}<form class="cf-new" data-p="${p.id}"><input type="text" maxlength="${st.comment_max}" placeholder="${esc(tr("写评论…"))}" aria-label="${esc(tr("写评论…"))}" enterkeyhint="send">
      <button class="btn btn-primary" type="submit">${esc(tr("发送"))}</button></form></div>`;
  }
  // Line icons from Lucide (https://lucide.dev, ISC; chevron-down derived from Feather, MIT): only the paths of the icons used
  // are inlined, nothing is loaded at runtime. Notices: THIRD_PARTY_NOTICES.md
  const HEART = '<path d="M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5"/>';
  const BUBBLE = '<path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719"/>';
  const TROPHY = '<path d="M10 14.66V17a1 1 0 0 1-1 1 2 2 0 0 0-2 2v2"/><path d="M14 14.66V17a1 1 0 0 0 1 1 2 2 0 0 1 2 2v2"/><path d="M17.916 10H19.5A2.5 2.5 0 0 0 22 7.5V5a1 1 0 0 0-1-1h-3"/><path d="M4 22h16"/><path d="M6 9a6 6 0 0 0 12 0V3a1 1 0 0 0-1-1H7a1 1 0 0 0-1 1z"/><path d="M6.084 10H4.5A2.5 2.5 0 0 1 2 7.5V5a1 1 0 0 1 1-1h3"/>';
  const DUMBBELL = '<path d="M17.596 12.768a2 2 0 1 0 2.829-2.829l-1.768-1.767a2 2 0 0 0 2.828-2.829l-2.828-2.828a2 2 0 0 0-2.829 2.828l-1.767-1.768a2 2 0 1 0-2.829 2.829z"/><path d="m2.5 21.5 1.4-1.4"/><path d="m20.1 3.9 1.4-1.4"/><path d="M5.343 21.485a2 2 0 1 0 2.829-2.828l1.767 1.768a2 2 0 1 0 2.829-2.829l-6.364-6.364a2 2 0 1 0-2.829 2.829l1.768 1.767a2 2 0 0 0-2.828 2.829z"/><path d="m9.6 14.4 4.8-4.8"/>';
  const FLAME = '<path d="M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4"/>';
  const PULSE = '<path d="M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5"/><path d="M3.22 13H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/>';
  const PIN = '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>';
  const ACTIVITY = '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>';
  const CHEV = '<svg class="cf-chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  const svg = (inner) => `<svg viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
  const BACK_MUSCLES = new Set(["lats", "middle back", "lower back", "traps", "hamstrings", "glutes", "triceps", "calves"]);
  const LEG_MUSCLES = new Set(["quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"]);
  function thumbHtml(e) {
    if (!e.thumb && !(e.muscles || []).length) return `<span class="cf-th cf-ic" aria-hidden="true">${svg(DUMBBELL)}</span>`;   // cardio, an unmatched imported name
    if (e.thumb) return `<span class="cf-th"><img src="${esc(e.thumb)}" alt="" loading="lazy" decoding="async"></span>`;
    const ms = e.muscles || [];
    // no picture: the muscle map of the exercise (muscle-map.js), one side, cropped to the legs or the upper body so it is readable at 44 px
    const box = ms.some((m) => LEG_MUSCLES.has(m)) ? "100 930 800 1000" : "0 150 1000 1000";
    const mm = (window.BodyMuscleMap?.html(ms, []) || "").replaceAll('viewBox="0 0 1000 2000"', `viewBox="${box}"`);
    return `<span class="cf-th${ms.some((m) => BACK_MUSCLES.has(m)) ? " back" : ""}" aria-hidden="true">${mm}</span>`;
  }
  function ago(p) {   // "3 h ago" from the workout's end; a plain day label when it is older than a day or has no time
    const t = p.started_at ? Date.parse(p.started_at) + (p.duration_min || 0) * 6e4 : NaN;
    const s = Math.floor((Date.now() - t) / 1000);
    if (!(s >= -60) || s >= 86400) return dayText(p.date);
    if (s < 60) return tr("刚刚");
    return s < 3600 ? tr("{n} 分钟前", { n: Math.floor(s / 60) }) : tr("{n} 小时前", { n: Math.floor(s / 3600) });
  }
  function durText(min) {
    return min >= 60 ? tr("{h} 小时 {m} 分", { h: Math.floor(min / 60), m: min % 60 }) : tr("{n} 分钟", { n: min });
  }
  function statsHtml(p) {
    const cell = (label, v) => `<div class="cf-stat"><small>${esc(label)}</small><b>${v}</b></div>`;
    return [p.duration_min ? cell(tr("时长"), esc(durText(p.duration_min))) : "",
      p.active_kcal ? cell(tr("消耗"), `${num(p.active_kcal)}<em>kcal</em>`) : "",
      p.volume_kg ? cell(tr("训练量"), `${num(p.volume_kg)}<em>kg</em>`) : "",
      p.set_count ? cell(tr("组数"), num(p.set_count)) : "",
      p.pr_count ? cell(tr("纪录"), `<span class="cf-tro">${svg(TROPHY)}</span>${num(p.pr_count)}`) : ""].join("");
  }
  // one watch workout's summary under the stats row (#194): average / max heart rate, distance, load (TRIMP), minutes per zone;
  // with several workouts that day each gets its name, duration and energy too
  function zonesHtml(z) {
    const total = (z || []).reduce((a, b) => a + (Number(b) || 0), 0);
    if (!total) return "";
    const seg = z.map((v, i) => `<span style="width:${((Number(v) || 0) / total * 100).toFixed(1)}%;opacity:${(0.25 + i * 0.18).toFixed(2)}" title="${esc(tr("区间 {i}：{n} 分钟", { i: i + 1, n: Math.round(v) }))}"></span>`).join("");
    return `<div class="cf-zb" role="img" aria-label="${esc(tr("心率区间"))}">${seg}</div><div class="cf-zl">${z.map((v, i) => `<span>Z${i + 1} ${Math.round(Number(v) || 0)}′</span>`).join("")}</div>`;
  }
  function activityHtml(a, many) {
    const chip = (icon, label, v) => `<span title="${esc(label)}" aria-label="${esc(label)}">${svg(icon)}${v}</span>`;
    const chips = [many && a.active_kcal ? chip(FLAME, tr("消耗"), `${num(a.active_kcal)}<em>kcal</em>`) : "",
      a.avg_hr || a.max_hr ? chip(PULSE, tr("平均 / 最高心率"), `${a.avg_hr ? num(a.avg_hr) : "—"} / ${a.max_hr ? num(a.max_hr) : "—"}<em>bpm</em>`) : "",
      a.distance_km ? chip(PIN, tr("距离"), `${num(a.distance_km, a.distance_km < 10 ? 2 : 1)}<em>km</em>`) : "",
      a.load ? chip(ACTIVITY, tr("训练负荷"), `${num(a.load)}<em>TRIMP</em>`) : ""].join("");
    const zones = zonesHtml(a.hr_zones);
    if (!chips && !zones && !many) return "";
    const head = many ? `<p class="cf-wkn">${esc(a.name)}${a.duration_min ? ` · ${esc(durText(a.duration_min))}` : ""}</p>` : "";
    return `<div class="cf-wk">${head}${chips ? `<div class="cf-chips">${chips}</div>` : ""}${zones}</div>`;
  }
  function exerciseHtml(p, e, i) {
    const open = st.sets.has(`${p.id}:${i}`);
    const n = e.sets.length;
    const rows = open ? `<div class="cf-sets">${e.sets.map((s, k) => `<div><i>${k + 1}</i><span>${esc(setText(s))}</span></div>`).join("")}</div>` : "";
    return `<button type="button" class="cf-exb" data-cf="sets" data-p="${p.id}" data-i="${i}" aria-expanded="${open}">${thumbHtml(e)}
      <span class="cf-ext"><span>${esc(n === 1 ? tr("1 组") : tr("{n} 组", { n }))}</span>${esc(e.name)}</span>${e.pr ? `<span class="cf-pr" title="${esc(tr("个人纪录"))}" aria-label="${esc(tr("个人纪录"))}">${svg(TROPHY)}</span>` : ""}${CHEV}</button>${rows}`;
  }
  function actionsHtml(p, open) {
    const n = p.likes.count, shown = p.preview_comments || [];
    const likes = n ? `<p class="cf-likes">${esc(n === 1 ? tr("1 次赞") : tr("{n} 次赞", { n: num(n) }))}</p>` : "";
    const all = !open && p.comment_count > shown.length
      ? `<button type="button" class="cf-all" data-cf="cm" data-p="${p.id}">${esc(p.comment_count === 1 ? tr("查看 1 条评论") : tr("查看全部 {n} 条评论", { n: num(p.comment_count) }))}</button>` : "";
    const pv = open ? "" : shown.map((c) => `<p class="cf-pv"><b>${esc(c.user.name)}</b>${esc(c.body)}</p>`).join("");
    return `<div class="cf-act"><button type="button" data-cf="like" data-p="${p.id}" aria-pressed="${p.likes.mine}" aria-label="${esc(tr("点赞"))}">${svg(HEART)}</button>
      <button type="button" data-cf="cm" data-p="${p.id}" aria-expanded="${open}" aria-label="${esc(tr("评论"))}">${svg(BUBBLE)}</button></div>${likes}${pv}${all}`;
  }
  // the circles a post came through, each with its avatar (or initial) from circles.js
  function circlesHtml(p) {
    const refs = p.circle_refs || (p.circles || []).map((name) => ({ name }));
    if (!refs.length) return "";
    const av = window.BodyCircles?.avatar;
    return " · " + refs.map((c) => `<span class="cf-circ">${av ? av(c, 14) : ""}${esc(c.name)}</span>`).join(" · ");
  }
  function postHtml(p) {
    const all = st.more.has(p.id), shown = all ? p.exercises : p.exercises.slice(0, st.preview);
    const rest = p.exercises.length - st.preview;
    const toggle = rest > 0 ? `<button type="button" class="cf-toggle" data-cf="more" data-p="${p.id}">${esc(all ? tr("收起") : tr("查看另外 {n} 个动作", { n: rest }))}</button>` : "";
    const open = st.open.has(p.id);
    return `<article class="cf-post" data-id="${p.id}"><div class="cf-head"><span class="cf-av">${esc((p.user.name || "?").slice(0, 1).toUpperCase())}</span>
        <div class="cf-who"><b>${esc(p.user.self ? tr("who.me", { name: p.user.name }) : p.user.name)}</b><i>${esc(ago(p))}${circlesHtml(p)}</i></div></div>
      <div class="cf-body"><div class="cf-title">${esc(p.title)}</div><div class="cf-stats">${statsHtml(p)}</div>
      ${(p.activities || []).map((a, _, all) => activityHtml(a, all.length > 1)).join("")}
      ${p.no_exercises ? `<div class="cf-none">${svg(DUMBBELL)}${esc(tr("没有记录动作"))}</div>` : ""}
      ${shown.length ? `<div class="cf-exs">${shown.map((e, i) => exerciseHtml(p, e, i)).join("")}</div>` : ""}${toggle}</div>
      ${actionsHtml(p, open)}${open ? commentsHtml(p) : ""}</article>`;
  }
  function listHtml() {
    if (st.err) return `<div class="cf-empty">${esc(st.err)}</div>`;
    if (!st.loaded) return `<div class="cf-empty">${esc(tr("加载中…"))}</div>`;
    if (!st.posts.length) return `<div class="cf-empty">${esc(tr("还没有训练动态。给圈子勾选「训练」共享，练完就会自动出现在这里"))}</div>`;
    return `<div class="cf-list">${st.posts.map(postHtml).join("")}</div>${st.next ? `<button type="button" class="cf-more" data-cf="next">${esc(st.loading ? tr("加载中…") : tr("加载更多"))}</button>` : ""}<div id="cf-end"></div>`;
  }
  function paint() {
    const root = $("#cf-root");
    if (!root) return;
    const typing = root.contains(document.activeElement) && document.activeElement.tagName === "INPUT";
    if (typing) return;   // never rebuild under someone's typing; the next refresh paints
    const target = focus && st.loaded ? st.posts.find((p) => p.id === focus) : null;
    if (target) st.open.add(target.id);
    const y = window.scrollY;
    root.innerHTML = listHtml();
    window.scrollTo(0, y);
    animate(root);
    watchEnd();
    if (st.loaded && focus) {
      focus = null;
      if (target) {
        $(`.cf-post[data-id="${target.id}"]`, root)?.scrollIntoView({ block: "start" });
        if (!st.comments[target.id]) loadComments(target.id);
      }
    }
  }
  function animate(root) {   // the heart that was just tapped bounces; a double-tap shows the big heart on the post
    if (st.pop) $(`[data-cf="like"][data-p="${st.pop}"]`, root)?.classList.add("pop");
    if (st.burst) $(`.cf-post[data-id="${st.burst}"]`, root)?.insertAdjacentHTML("beforeend", `<span class="cf-burst" aria-hidden="true">${svg(HEART)}</span>`);
    st.pop = st.burst = null;
  }
  function openPost(id) {   // switch the Friends tab to 「动态」 (the tab itself is switched by notify.js)
    focus = id;
    const t0 = Date.now();
    const tick = () => {
      const seg = $('[data-fmode2="feed"]');
      if (seg && seg.getAttribute("aria-pressed") !== "true") seg.click();
      else if ($("#cf-root") && st.loaded) paint();
      if ((seg && seg.getAttribute("aria-pressed") === "true") || Date.now() - t0 > 5000) return;
      setTimeout(tick, 150);
    };
    tick();
  }
  window.addEventListener("body:link", (e) => { const id = Number(e.detail?.feed); if (id) openPost(id); });

  // ---- data
  async function fetchPage(before) {
    const q = before ? `?before=${encodeURIComponent(before)}` : "";
    return C.api(`/v1/friends/feed${q}`);
  }
  function merge(fresh) {
    const ids = new Set(fresh.posts.map((p) => p.id));
    const rest = st.posts.filter((p) => !ids.has(p.id));
    st.posts = [...fresh.posts, ...rest].sort((a, b) => (a.date === b.date ? b.id - a.id : a.date < b.date ? 1 : -1));
  }
  async function refresh(first) {
    try {
      const d = await fetchPage();
      st.comment_max = d.comment_max || st.comment_max; st.preview = d.preview_exercises || st.preview; st.preview_comments = d.comment_preview || st.preview_comments;
      if (first) { st.posts = d.posts; st.next = d.next; } else {
        const before = JSON.stringify(st.posts);
        merge(d);
        if (!st.next && d.next) st.next = d.next;
        if (before === JSON.stringify(st.posts)) return;
      }
      st.loaded = true; st.err = null;
    } catch (e) { if (first) st.err = e.message; else return; }
    paint();
  }
  async function loadMore() {
    if (st.loading || !st.next) return;
    st.loading = true; paint();
    try {
      const d = await fetchPage(st.next);
      merge(d); st.next = d.next;
    } catch (e) { C.toast(e.message); }
    st.loading = false; paint();
  }
  let io = null;
  function watchEnd() {
    io?.disconnect();
    const end = $("#cf-end");
    if (!end || !st.next || !("IntersectionObserver" in window)) return;
    io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) loadMore(); }, { rootMargin: "300px" });
    io.observe(end);
  }
  async function loadComments(id) {
    try { st.comments[id] = (await C.api(`/v1/friends/feed/posts/${id}/comments`)).comments; } catch (e) { st.comments[id] = []; C.toast(e.message); }
    paint();
  }

  // ---- red dot
  function showDot() {
    for (const el of $$(".cf-dot")) el.remove();
    if (!unread) return;
    for (const host of [$('.tab[data-tab="friends"]'), $('[data-fmode2="feed"]')]) {
      if (host) host.insertAdjacentHTML("beforeend", `<i class="cf-dot" aria-label="${esc(tr("有新的点赞或评论"))}"></i>`);
    }
  }
  async function pollUnread() {
    if (document.visibilityState !== "visible" || !window.BodyFlags?.on("circle_feed")) return;
    try { unread = (await C.api("/v1/friends/feed/unread")).count || 0; } catch (e) { return; }
    if ($("#cf-root")) { refresh(false); if (unread) markSeen(); return; }   // the feed is open: keep it fresh, nothing is "new"
    showDot();
  }
  async function markSeen() {
    unread = 0; showDot();
    try { await C.api("/v1/friends/feed/seen", { method: "POST" }); } catch (e) { /* the dot comes back at the next poll */ }
  }

  // ---- actions
  async function act(btn) {
    const kind = btn.dataset.cf, id = Number(btn.dataset.p), p = st.posts.find((x) => x.id === id);
    if (kind === "next") return loadMore();
    if (kind === "more") { st.more.has(id) ? st.more.delete(id) : st.more.add(id); return paint(); }
    if (kind === "cm") {
      if (st.open.has(id)) st.open.delete(id); else { st.open.add(id); if (!st.comments[id]) loadComments(id); }
      return paint();
    }
    if (kind === "sets") {
      const k = `${id}:${btn.dataset.i}`;
      st.sets.has(k) ? st.sets.delete(k) : st.sets.add(k);
      return paint();
    }
    if (kind === "like" && p) return setLike(p, !p.likes.mine);
    const cid = Number(btn.dataset.c);
    if (kind === "cdel") {
      if (!confirm(tr("删除这条评论？"))) return;
      try { await C.api(`/v1/friends/feed/comments/${cid}`, { method: "DELETE" }); } catch (e) { return C.toast(e.message); }
      st.comments[id] = (st.comments[id] || []).filter((c) => c.id !== cid);
      if (p) { p.comment_count = Math.max(0, p.comment_count - 1); p.preview_comments = (p.preview_comments || []).filter((c) => c.id !== cid); }
      return paint();
    }
    if (kind === "crep") {
      if (!confirm(tr("举报这条评论？"))) return;
      try { await C.api(`/v1/friends/feed/comments/${cid}/report`, { method: "POST", body: {} }); C.toast(tr("已举报")); } catch (e) { C.toast(e.message); }
    }
  }
  async function setLike(p, mine) {   // optimistic: the heart fills at once, the server's count replaces it (or the tap is undone)
    if (p.likes.mine === mine) return;
    const was = p.likes;
    p.likes = { count: Math.max(0, was.count + (mine ? 1 : -1)), mine };
    if (mine) st.pop = p.id;
    paint();
    const guess = p.likes;
    try {
      p.likes = await C.api(`/v1/friends/feed/posts/${p.id}/like`, { method: mine ? "PUT" : "DELETE" });
      if (p.likes.count === guess.count && p.likes.mine === guess.mine) return;   // nothing to redraw: let the bounce finish
    } catch (e) { p.likes = was; st.pop = null; C.toast(e.message); }
    paint();
  }
  let lastTap = { id: 0, t: 0 };
  function bodyTap(e) {   // double-tap (or double-click) the training to like it, like Instagram
    if (e.target.closest("[data-cf]")) return;
    const el = e.target.closest(".cf-post"), id = Number(el?.dataset.id), now = Date.now();
    if (!id) return;
    if (id === lastTap.id && now - lastTap.t < 350) {
      lastTap = { id: 0, t: 0 };
      const p = st.posts.find((x) => x.id === id);
      if (!p) return;
      st.burst = id;
      return p.likes.mine ? paint() : setLike(p, true);
    }
    lastTap = { id, t: now };
  }
  async function submitComment(form) {
    const id = Number(form.dataset.p), input = $("input", form), text = input.value.trim();
    if (!text) return;
    try {
      const c = await C.api(`/v1/friends/feed/posts/${id}/comments`, { method: "POST", body: { body: text } });
      (st.comments[id] ||= []).push(c);
      const p = st.posts.find((x) => x.id === id);
      if (p) { p.comment_count += 1; p.preview_comments = [...(p.preview_comments || []), { id: c.id, user: c.user, body: c.body }].slice(-st.preview_comments); }
      input.value = "";
      input.blur();
      paint();
    } catch (e) { C.toast(e.message); }
  }

  window.BodyCircleFeed = {
    shell: () => '<div id="cf-root"></div>',
    count: () => unread,
    bind(view, active) {
      showDot();
      const root = $("#cf-root", view);
      if (!root || !active) return;
      injectCss();
      root.innerHTML = listHtml();
      root.addEventListener("click", (e) => { const b = e.target.closest("[data-cf]"); if (b) act(b); });
      root.addEventListener("click", (e) => { if (e.target.closest(".cf-body")) bodyTap(e); });
      root.addEventListener("submit", (e) => { e.preventDefault(); const f = e.target.closest("form.cf-new"); if (f) submitComment(f); });
      refresh(true).then(markSeen);
    },
  };

  (window.BodyPlugins ||= []).push({
    init: (ctx) => {
      C = ctx; injectCss();
      if (!window.BodyFlags?.on("circle_feed")) return;
      pollUnread();
      timer ??= setInterval(pollUnread, POLL_MS);
    },
  });
})();
