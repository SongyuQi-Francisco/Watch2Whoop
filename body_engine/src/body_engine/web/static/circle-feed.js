/* Circle training feed (body_engine/friends/feed.py, flag circle_feed): the Friends tab's 「动态」 segment — a Hevy / Instagram
   style post per person per day (training name, duration, exercises with sets × weight, total load, 🏆 records), ❤️ and text
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
  const st = { posts: [], next: null, loading: false, open: new Set(), more: new Set(), comments: {}, comment_max: 300, preview: 3, loaded: false, err: null };

  const CSS = `.cf-list{display:flex;flex-direction:column;gap:12px}
.cf-post{background:var(--surface);border-radius:16px;padding:14px;min-width:0}
.cf-head{display:flex;align-items:center;gap:10px}.cf-av{flex:none;width:34px;height:34px;border-radius:50%;background:var(--surface-2);display:flex;align-items:center;justify-content:center;font-weight:600;font-size:14px}
.cf-who{flex:1;min-width:0}.cf-who b{display:block;font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cf-who i{display:block;font-style:normal;font-size:12px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf-title{font-size:17px;font-weight:600;margin:10px 0 2px}
.cf-stats{display:flex;flex-wrap:wrap;gap:4px 16px;margin:6px 0 4px}.cf-stats span{font-size:13px;color:var(--text-2)}.cf-stats b{font-size:16px;color:var(--text);font-variant-numeric:tabular-nums;margin-right:2px}
.cf-pr{display:inline-block;font-size:12px;background:var(--mid-soft);color:var(--mid);border-radius:8px;padding:1px 7px;margin-left:6px;font-weight:600}
.cf-ex{border-top:1px solid var(--hair);padding:8px 0}.cf-ex:first-of-type{margin-top:8px}.cf-exn{font-size:14px;font-weight:600}
.cf-sets{display:flex;flex-wrap:wrap;gap:4px 6px;margin-top:4px}.cf-sets span{font-size:12.5px;background:var(--surface-2);border-radius:7px;padding:2px 7px;font-variant-numeric:tabular-nums}
.cf-toggle,.cf-link{border:0;background:none;color:var(--text-2);font-size:13px;padding:6px 0;text-decoration:underline}
.cf-foot{display:flex;align-items:center;gap:6px;margin-top:6px;border-top:1px solid var(--hair);padding-top:8px}
.cf-foot button{border:0;background:none;color:var(--text-2);font-size:14px;min-height:36px;padding:0 10px;border-radius:10px;display:inline-flex;align-items:center;gap:5px}
.cf-foot button[aria-pressed="true"]{color:var(--bad);background:var(--bad-soft)}
.cf-cm{margin-top:6px}.cf-c{display:flex;gap:8px;align-items:flex-start;padding:6px 0;font-size:14px}
.cf-c .t{flex:1;min-width:0;overflow-wrap:anywhere}.cf-c .t b{font-weight:600;margin-right:6px}
.cf-c button{flex:none;border:0;background:none;color:var(--text-3);font-size:12px;text-decoration:underline;padding:4px 2px}
.cf-new{display:flex;gap:8px;margin-top:6px}.cf-new input{flex:1;min-width:0;border:0;border-radius:10px;background:var(--in-sheet);color:var(--text);font-size:16px;padding:0 12px;min-height:40px}
.cf-new .btn{flex:none;min-height:40px;padding:0 14px}
.cf-more{display:block;margin:12px auto;border:0;background:none;color:var(--text-2);font-size:13px;text-decoration:underline}
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
  function postHtml(p) {
    const all = st.more.has(p.id), shown = all ? p.exercises : p.exercises.slice(0, st.preview);
    const ex = shown.map((e) => `<div class="cf-ex"><div class="cf-exn">${esc(e.name)}${e.pr ? `<span class="cf-pr">🏆 ${esc(tr("个人纪录"))}</span>` : ""}</div>
      <div class="cf-sets">${e.sets.map((s) => `<span>${esc(setText(s))}</span>`).join("")}</div></div>`).join("");
    const rest = p.exercises.length - st.preview;
    const toggle = rest > 0 ? `<button type="button" class="cf-toggle" data-cf="more" data-p="${p.id}">${esc(all ? tr("收起") : tr("展开全部 {n} 个动作", { n: p.exercises.length }))}</button>` : "";
    const stats = [p.duration_min ? `<span><b>${num(p.duration_min)}</b>${esc(tr("分钟"))}</span>` : "",
      p.set_count ? `<span><b>${num(p.set_count)}</b>${esc(tr("feed.sets"))}</span>` : "",
      p.volume_kg ? `<span><b>${num(p.volume_kg)}</b>${esc(tr("总负荷 kg"))}</span>` : "",
      p.pr_count ? `<span><b>${p.pr_count}</b>🏆</span>` : ""].join("");
    const open = st.open.has(p.id);
    return `<article class="cf-post" data-id="${p.id}"><div class="cf-head"><span class="cf-av">${esc((p.user.name || "?").slice(0, 1).toUpperCase())}</span>
        <div class="cf-who"><b>${esc(p.user.self ? tr("who.me", { name: p.user.name }) : p.user.name)}</b><i>${esc(dayText(p.date))}${p.circles?.length ? ` · ${esc(p.circles.join(" · "))}` : ""}</i></div></div>
      <div class="cf-title">${esc(p.title)}</div><div class="cf-stats">${stats}</div>${ex}${toggle}
      <div class="cf-foot"><button type="button" data-cf="like" data-p="${p.id}" aria-pressed="${p.likes.mine}" aria-label="${esc(tr("点赞"))}">❤️ <span>${p.likes.count || ""}</span></button>
        <button type="button" data-cf="cm" data-p="${p.id}" aria-expanded="${open}" aria-label="${esc(tr("评论"))}">💬 <span>${p.comment_count || ""}</span></button></div>
      ${open ? commentsHtml(p) : ""}</article>`;
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
    watchEnd();
    if (st.loaded && focus) {
      focus = null;
      if (target) {
        $(`.cf-post[data-id="${target.id}"]`, root)?.scrollIntoView({ block: "start" });
        if (!st.comments[target.id]) loadComments(target.id);
      }
    }
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
      st.comment_max = d.comment_max || st.comment_max; st.preview = d.preview_exercises || st.preview;
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
    if (kind === "like" && p) {
      const mine = !p.likes.mine;
      try {
        p.likes = await C.api(`/v1/friends/feed/posts/${id}/like`, { method: mine ? "PUT" : "DELETE" });
      } catch (e) { C.toast(e.message); }
      return paint();
    }
    const cid = Number(btn.dataset.c);
    if (kind === "cdel") {
      if (!confirm(tr("删除这条评论？"))) return;
      try { await C.api(`/v1/friends/feed/comments/${cid}`, { method: "DELETE" }); } catch (e) { return C.toast(e.message); }
      st.comments[id] = (st.comments[id] || []).filter((c) => c.id !== cid);
      if (p) p.comment_count = Math.max(0, p.comment_count - 1);
      return paint();
    }
    if (kind === "crep") {
      if (!confirm(tr("举报这条评论？"))) return;
      try { await C.api(`/v1/friends/feed/comments/${cid}/report`, { method: "POST", body: {} }); C.toast(tr("已举报")); } catch (e) { C.toast(e.message); }
    }
  }
  async function submitComment(form) {
    const id = Number(form.dataset.p), input = $("input", form), text = input.value.trim();
    if (!text) return;
    try {
      const c = await C.api(`/v1/friends/feed/posts/${id}/comments`, { method: "POST", body: { body: text } });
      (st.comments[id] ||= []).push(c);
      const p = st.posts.find((x) => x.id === id);
      if (p) p.comment_count += 1;
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
