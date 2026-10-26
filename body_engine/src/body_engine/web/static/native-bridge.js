/* Native views of the iOS app over this page (#247 phase 2, docs/ios-native-plan.md 5.3; app 1.0.9+). Loaded on demand by app.js
   (nativeHello) only inside an app that announces window.Watch2WhoopFeatures.nativeUi; browsers, Android and older apps never load it.

   Page -> app (the JS channel Watch2Whoop; the app answers nothing it does not know):
   - "native:" {v, jarvis, today}: the flags native_jarvis (needs jarvis_orbit) / native_today for this person, once per load. The app
     remembers them; off = it removes its view and the page stays as it is.
   - "jvn:" {show, rect, menu, voice, offline, dark, theme, labels}: where the page's Jarvis key is and whether it can be seen (nothing
     covers its centre, the dock shows it). Only while the app said it draws the key (Watch2WhoopFeatures.nativeJarvis); the page's
     own key then stays in place but invisible (body.jv-native), so the dock and everything anchored to it keep their layout.
   - "tdn:" {show, rect, dark, theme}: the 今天 tab from its scores down to the dock while it is the visible page,
     about yourself, today, and nothing covers it (a sheet, the chat, the login screen …). Only with Watch2WhoopFeatures.nativeToday.
   - "tdn-data:" {v, uid, day, scores, daily, reminders}: what the 今天 tab just drew (BodyHooks "today"): the app's native Today
     shows it and keeps the last copy on the phone.
   App -> page: window.BodyNative.action({kind: "jarvis", id: tap | chat | voice | photo | quick | hold}),
   ({kind: "today", card: <data-card key> | "web" | "refresh"}), window.BodyNative.photo(base64, mime) (the app's camera).
   Reports are coalesced (at most one per ~80 ms, plus one ~400 ms later for sheets that are still moving) and sent only when they
   changed. Nothing here changes the page while the app has not switched a native view on. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const tr = (k, p) => window.I18N.t(k, p);
  const tell = (msg) => { try { window.Watch2Whoop.postMessage(msg); } catch (e) { /* not in the app */ } };
  const F = () => window.Watch2WhoopFeatures || {};
  const on = (k) => !!window.BodyFlags?.on(k);
  let C = null, uid = null, page = "", webToday = false, data = null;

  const style = document.createElement("style");
  style.textContent = "body.jv-native .jv-row { opacity: 0 !important; }";
  document.head.append(style);

  const dark = () => !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const THEME = ["bg", "surface", "surface-2", "text", "text-2", "text-3", "good", "mid", "bad", "sleep", "train"];
  function theme() {
    const cs = getComputedStyle(document.documentElement);
    return Object.fromEntries(THEME.map((k) => [k, cs.getPropertyValue(`--${k}`).trim()]));
  }
  const box = (r) => [r.left, r.top, r.width, r.height].map((v) => Math.round(v * 10) / 10);
  // Nothing on top of (x, y) but `el` itself or what is inside it
  function clear(el, x, y) { const hit = document.elementFromPoint(x, y); return !!hit && (hit === el || el.contains(hit)); }

  // The page's Jarvis code (jarvis-entry.js) still decides what a press does. menu = a tap opens the app's own menu (Jarvis, online,
  // no recording); otherwise the app sends the tap here: own AI / butler = what the key always did, offline = "needs a connection",
  // a locked recording = send it. "hold" (a long press) = hands-free talk at once, as 语音.
  const J = () => window.BodyJarvis?.native?.() || null;
  const jvState = (j = J()) => (j?.jv ? { menu: j.isJarvis() && !j.offline() && j.V.state === "off", voice: j.V.state, offline: j.offline() } : {});
  function jvTap(j = J()) {
    if (!j?.jv) return;
    if (j.V.state === "locked" && j.V.anchor === j.jv) j.finishVoice();
    else if (j.V.state === "off") { if (j.offline()) j.C?.needsNet?.(); else if (!j.isJarvis()) j.C?.openAssistant?.(); }
  }
  function jvRun(id, j = J()) {
    if (!j?.jv || j.V.state !== "off") return;
    if (j.offline()) j.C?.needsNet?.();
    else if (!j.isJarvis()) j.C?.openAssistant?.();
    else if (id === "hold") j.runKey("voice");
    else if (j.keys.includes(id)) j.runKey(id);
  }

  function jarvisState() {
    const key = $("#ask"), st = jvState();
    let show = false, rect = null;
    if (key && document.visibilityState === "visible" && !$("#app")?.hidden) {
      const r = key.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { rect = box(r); show = clear(key, r.left + r.width / 2, r.top + r.height / 2); }
    }
    return { show, rect, menu: !!st.menu, voice: st.voice || "off", offline: !!st.offline, dark: dark(), theme: theme(),
      labels: { chat: tr("对话"), voice: tr("语音"), photo: tr("jarvis.photo"), quick: tr("快记"), close: tr("收起 Jarvis 菜单"), key: "Jarvis" } };
  }

  function todayState() {
    const view = $("#view"), top = $(".top"), dock = $(".dock");
    let show = false, rect = null;
    if (!webToday && page === "today" && data && data.self && !data.past && view && top && dock && document.visibilityState === "visible" && !$("#app")?.hidden) {
      // from the scores down: what the page shows above them (the sync card, notices, the offline banner) stays the page's
      const hero = $("#view .hero"), y1 = dock.getBoundingClientRect().top;
      const y0 = Math.max(0, top.getBoundingClientRect().bottom, hero ? hero.getBoundingClientRect().top - 8 : 0);
      if (y1 - y0 > 120) {
        rect = box({ left: 0, top: y0, width: window.innerWidth, height: y1 - y0 });
        const x = window.innerWidth / 4;   // off the middle: the Jarvis key sits over the bottom edge there
        show = window.scrollY < 4 && [y0 + 24, (y0 + y1) / 2, y1 - 40].every((y) => clear(view, x, y));
      }
    }
    return { show, rect, dark: dark(), theme: theme() };
  }

  const last = {};
  function send(prefix, obj) { const s = JSON.stringify(obj); if (last[prefix] !== s) { last[prefix] = s; tell(prefix + s); } }
  function report() {
    document.body.classList.toggle("jv-native", !!F().nativeJarvis);
    if (F().nativeJarvis) send("jvn:", jarvisState());
    if (F().nativeToday) send("tdn:", todayState());
  }
  let soon = 0, later = 0;
  function schedule() {
    if (!F().nativeJarvis && !F().nativeToday && !document.body.classList.contains("jv-native")) return;
    if (!soon) soon = setTimeout(() => { soon = 0; requestAnimationFrame(report); }, 80);
    clearTimeout(later); later = setTimeout(() => requestAnimationFrame(report), 400);
  }

  function sendData() {
    if (!F().nativeToday || !data || !data.self || data.past) return;
    const pick = (o, ks) => (o && typeof o === "object" ? Object.fromEntries(ks.filter((k) => o[k] != null).map((k) => [k, o[k]])) : null);
    const sc = data.scores || {};
    send("tdn-data:", {
      v: 1, uid, day: data.day,
      scores: {
        error: sc.error ? String(sc.error) : null,
        today: pick(sc.today, ["recovery", "hrv_night_ms", "rhr_night", "sleep_actual_min", "sleep_score", "sleep_performance", "sleep_need_min", "sleep_debt_min", "strain"]),
        yesterday: pick(sc.yesterday, ["strain", "date"]),
        recovery_zone: sc.recovery_zone || null, recovery_word: sc.recovery_word || null,
        recovery_empty: sc.recovery_empty?.label ? { label: sc.recovery_empty.label } : null,
        last_recovery: pick(sc.last_recovery, ["recovery", "date"]),
        load_status: sc.load_status?.label ? { label: sc.load_status.label } : null,
      },
      daily: pick(data.daily, ["steps", "active_kcal", "exercise_min"]),
      coach: pick(data.coach, ["target_low", "target_high", "current_strain", "status", "remaining_to_low"]),
      monitor: (data.monitor?.metrics || []).slice(0, 8).map((x) => pick(x, ["key", "label", "value", "unit", "status"])),
      reminders: Array.isArray(data.reminders) ? data.reminders.filter((r) => r.status !== "dismissed").length : 0,
    });
  }

  function apply() {   // the app (re)announced its features
    document.body.classList.toggle("jv-native", !!F().nativeJarvis);
    sendData();
    schedule();
  }

  function card(key) {
    if (key === "web") { webToday = true; schedule(); return; }   // 「完整今天页」: the web page until the user leaves 今天
    if (key === "refresh") { C?.reload?.(); return; }
    const el = /^[a-z0-9_.:-]{1,40}$/.test(key || "") && $(`#view [data-card="${key}"]`);
    if (el) el.click();
  }

  window.BodyNative = {
    action(a) {
      if (!a || typeof a !== "object") return;
      if (a.kind === "jarvis") { if (a.id === "tap") jvTap(); else jvRun(String(a.id)); }
      else if (a.kind === "today") card(String(a.card || ""));
      schedule();
    },
    photo(b64, mime) {
      try {
        const bin = atob(String(b64 || "")), u8 = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
        const type = /^image\/(jpeg|png|heic)$/.test(mime) ? mime : "image/jpeg";
        const AI = window.BodyAI;
        if (u8.length && AI?.openHalf({})) AI.sendPhoto(new File([u8], `photo.${type.split("/")[1]}`, { type }));
      } catch (e) { C?.toast?.(tr("err.request", { status: 0 })); }
    },
  };

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init(ctx, me) {
      C = ctx; uid = me?.me?.id ?? null;
      page = ctx.page?.() || ""; data = ctx.todayDrawn?.() || null;   // drawn before this file loaded
      tell(`native:${JSON.stringify({ v: 1, jarvis: on("native_jarvis") && on("jarvis_orbit"), today: on("native_today") })}`);
      window.addEventListener("w2w-features", apply);
      window.BodyHooks?.on("page", (d) => { if (d.page !== "today") webToday = false; page = d.page; schedule(); });
      window.BodyHooks?.on("sheet", schedule);
      window.BodyHooks?.on("today", (d) => { data = d; sendData(); schedule(); });
      new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });   // 「外观」 themes change the tokens
      new MutationObserver(schedule).observe(document.body,{ subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "class", "inert"] });
      for (const ev of ["resize", "scroll", "pageshow"]) window.addEventListener(ev, schedule, { passive: true });
      document.addEventListener("visibilitychange", schedule);
      window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => { last["jvn:"] = last["tdn:"] = ""; schedule(); });
      apply();
    },
  });
})();
