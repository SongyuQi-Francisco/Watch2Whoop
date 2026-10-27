/* Tiny i18n layer for the Today page and the activation page (issues #13, #42). No build step: load this file first, then i18n-en.js.
   - Language: the user's profile setting `language` (zh / en, from /v1/web/me, cached in localStorage "be.lang"); empty = follow the device
     (navigator.language: zh* -> zh, anything else -> en). Before login the login page's 中文 / English switch sets the same localStorage key,
     and so does #lang=zh|en in the address (the Watch2Whoop app's native sign-in switch).
   - I18N.t(key, params), aliased `tr` in the page scripts (app.js has many locals named `t`). Two kinds of key:
       1. dotted keys ("tab.today"): looked up in the ZH / EN tables below (the shell, from stage 1);
       2. the Chinese source text itself (gettext style): tr("恢复"), tr("{n} 项", { n }). Chinese = the key; English = i18n-en.js.
          New UI text is written this way: the code stays readable and a parallel change only has to wrap its new strings.
     Lookup falls back to zh, then to the key itself; "{name}" placeholders are filled from params.
   - Static HTML: data-i18n="key" (text), data-i18n-aria (aria-label), data-i18n-ph (placeholder), data-i18n-title (title),
     data-i18n-href (href: the key's text is a relative address, e.g. the handbook in the page language);
     <html data-i18n-title> sets the document title. Keys may be either kind.
   - tests/test_i18n.py: every key in both languages with the same placeholders, every tr() key used by the scripts / HTML exists,
     and no Chinese literal outside tr(...) in the page scripts (mark a deliberate one, e.g. a regex on server data, with `i18n-ok`).
   - Data from the server (food names, journal items, AI-written status cards) is shown as stored. Server texts the page shows
     (reminders, explain sheets, labels, error messages) come in the request's language: api() sends X-Body-Lang (I18N.headers()). */
(() => {
  "use strict";
  const ZH = {
    "app.title": "身体 · 今天",
    "tab.today": "今天",
    "tab.trends": "趋势",
    "tab.food": "饮食",
    "tab.friends": "朋友",
    "tab.record": "记录",
    "tab.me": "我",
    "nav.tabs": "页面",
    "hdr.prevDay": "前一天",
    "hdr.nextDay": "后一天",
    "hdr.who": "切换查看的人",
    "hdr.pickDate": "选择日期",
    "hdr.yesterday": "昨天",
    "common.backToday": "回到今天",
    "common.close": "关闭",
    "common.retry": "重试",
    "common.save": "保存",
    "common.saved": "已保存",
    "common.refreshed": "已刷新",
    "common.none": "暂无",
    "common.all": "全部 {n}",
    "login.title": "身体管理",
    "login.hint": "输入激活码（16 位）、登录码（12 位）或 token",
    "login.placeholder": "激活码 / 登录码",
    "login.aria": "激活码、登录码或 token",
    "login.submit": "登录",
    "login.app": "手机同步与设置",
    "login.badActivation": "激活码无效或已停用：找管理员要新的激活链接",
    "login.badCode": "登录码已用过或已过期：找管理员要新的，或填激活码",
    "login.badToken": "token 不对或已吊销",
    "login.expired": "登录已过期，请重新输入 token",
    "err.offline": "连不上",
    "err.notLoggedIn": "没有登录",
    "err.request": "请求失败（{status}）",
    "banner.offlineAt": "离线 · {when} 的数据",
    "banner.offline": "离线 · 上次的数据",
    "picker.title": "日期",
    "picker.prevMonth": "上个月",
    "picker.nextMonth": "下个月",
    "picker.hasData": "有数据",
    "picker.recovery": "，恢复 {n}",
    "who.title": "查看",
    "who.me": "我（{name}）",
    "feed.sets": "组",
    "me.status": "身体状态",
    "me.observations": "观察笔记",
    "me.observationsN": "观察笔记 · {n}",
    "me.supplements": "补剂",
    "me.events": "身体事件",
    "me.labs": "化验",
    "me.activeN": "{n} 在吃",
    "me.labsN": "{n} 项",
    "me.aiSettings": "AI 设置",
    "me.phoneSync": "手机同步",
    "me.switch": "切换查看",
    "me.updated": "数据更新",
    "me.logout": "退出登录",
    "me.version": "今天页 {web} · Body Engine {api}",
    "me.rw": "可查看 · 可记录",
    "me.ro": "只读",
    "me.family": "家人 · 只读",
    "jarvis.subscription": "订阅",
    "jarvis.connected": "已连接",
    "jarvis.askInApp": "在官方 app 里问",
    "jarvis.switch": "换 AI",
    "jarvis.unset": "未设置",
    "jarvis.setup": "去 AI 设置",
    "jarvis.photo": "拍照",
    "profile.title": "个人资料",
    "profile.complete": "完善个人资料",
    "profile.intro": "这些用来算心率区间、身体年龄、和同龄人对比。只有你自己看得到，以后在「我」→「个人资料」里随时能改。",
    "profile.name": "怎么称呼你",
    "profile.birth": "生日",
    "profile.sex": "性别",
    "profile.sexNone": "不填",
    "profile.male": "男",
    "profile.female": "女",
    "profile.height": "身高（cm）",
    "profile.goal": "目标体重（kg，可不填）",
    "profile.tz": "时区",
    "profile.lang": "语言",
    "profile.langAuto": "跟随系统",
    "profile.later": "以后再填",
    "profile.incomplete": "未填完",
    "activate.title": "激活 Watch2Whoop",
    "activate.hint": "先在 TestFlight 装好 Watch2Whoop，再点下面的按钮",
    "activate.code": "激活码",
    "activate.go": "打开 App 完成设置",
    "activate.handbook": "使用手册",
    "activate.handbookUrl": "handbook/",
    "activate.enterCode": "请输入激活码",
    "activate.invalid": "激活码无效或已停用",
    "activate.tooMany": "失败次数太多，请稍后再试",
    "activate.network": "网络连接失败，请重试",
    "activate.opening": "正在打开 Watch2Whoop…",
    "activate.webLogin": "正在这个浏览器里登录…",
    "activate.openApp": "在 App 里打开",
    "activate.openWeb": "在网页里打开",
    "activate.invalidAsk": "激活码无效或已停用，找管理员要新的链接",
    "activate.installTitle": "先安装 App",
    "activate.installLead": "iPhone 上要用 Watch2Whoop App。装好以后再点一次这个激活链接。",
    "activate.install1": "在 App Store 安装 TestFlight",
    "activate.install2": "打开 TestFlight 邀请邮件，点「查看测试」→「接受」",
    "activate.install3": "在 TestFlight 里安装 Watch2Whoop",
    "activate.install4": "回到这里，再点一次激活链接",
    "activate.getTestFlight": "去 App Store 装 TestFlight",
    "activate.noInvite": "没收到邀请邮件？请管理员把你的 Apple ID 加为测试员",
    "activate.computer": "电脑上用浏览器打开今天页就行",
    "activate.iosBanner": "iPhone 请改用 Watch2Whoop App，网页版即将停用",
    "activate.iosBannerLink": "怎么安装",
  };
  const EN = {
    "app.title": "Body · Today",
    "tab.today": "Today",
    "tab.trends": "Trends",
    "tab.food": "Food",
    "tab.friends": "Friends",
    "tab.record": "Log",
    "tab.me": "Me",
    "nav.tabs": "Pages",
    "hdr.prevDay": "Previous day",
    "hdr.nextDay": "Next day",
    "hdr.who": "Switch person",
    "hdr.pickDate": "Choose a date",
    "hdr.yesterday": "Yesterday",
    "common.backToday": "Back to today",
    "common.close": "Close",
    "common.retry": "Retry",
    "common.save": "Save",
    "common.saved": "Saved",
    "common.refreshed": "Refreshed",
    "common.none": "None yet",
    "common.all": "All {n}",
    "login.title": "Watch2Whoop",
    "login.hint": "Enter your activation code (16 characters), login code (12) or a token",
    "login.placeholder": "Activation or login code",
    "login.aria": "Activation code, login code or token",
    "login.submit": "Sign in",
    "login.app": "Phone sync & settings",
    "login.badActivation": "Invalid or disabled activation code: ask your admin for a new activation link",
    "login.badCode": "Used or expired login code: ask your admin for a new one, or enter your activation code",
    "login.badToken": "Wrong or revoked token",
    "login.expired": "Session expired, please sign in again",
    "err.offline": "Can't connect",
    "err.notLoggedIn": "Not signed in",
    "err.request": "Request failed ({status})",
    "banner.offlineAt": "Offline · data from {when}",
    "banner.offline": "Offline · last saved data",
    "picker.title": "Date",
    "picker.prevMonth": "Previous month",
    "picker.nextMonth": "Next month",
    "picker.hasData": "Has data",
    "picker.recovery": ", recovery {n}",
    "who.title": "View",
    "who.me": "Me ({name})",
    "feed.sets": "sets",
    "me.status": "Body status",
    "me.observations": "Notes",
    "me.observationsN": "Notes · {n}",
    "me.supplements": "Supplements",
    "me.events": "Body events",
    "me.labs": "Lab results",
    "me.activeN": "{n} active",
    "me.labsN": "{n} tests",
    "me.aiSettings": "AI settings",
    "me.phoneSync": "Phone sync",
    "me.switch": "Switch person",
    "me.updated": "Data updated",
    "me.logout": "Sign out",
    "me.version": "Today page {web} · Body Engine {api}",
    "me.rw": "View · Log",
    "me.ro": "Read-only",
    "me.family": "Family · read-only",
    "jarvis.subscription": "Subscription",
    "jarvis.connected": "Connected",
    "jarvis.askInApp": "Ask in the official app",
    "jarvis.switch": "Switch AI",
    "jarvis.unset": "Not set",
    "jarvis.setup": "Open AI settings",
    "jarvis.photo": "Photo",
    "profile.title": "Profile",
    "profile.complete": "Complete your profile",
    "profile.intro": "Used for heart-rate zones, body age and comparisons with your age group. Only you can see it; change it any time under Me → Profile.",
    "profile.name": "What should we call you",
    "profile.birth": "Birthday",
    "profile.sex": "Sex",
    "profile.sexNone": "Prefer not to say",
    "profile.male": "Male",
    "profile.female": "Female",
    "profile.height": "Height (cm)",
    "profile.goal": "Goal weight (kg, optional)",
    "profile.tz": "Time zone",
    "profile.lang": "Language",
    "profile.langAuto": "Same as device",
    "profile.later": "Later",
    "profile.incomplete": "Incomplete",
    "activate.title": "Activate Watch2Whoop",
    "activate.hint": "Install Watch2Whoop from TestFlight first, then tap the button below",
    "activate.code": "Activation code",
    "activate.go": "Open the app to finish setup",
    "activate.handbook": "User guide",
    "activate.handbookUrl": "handbook/en/",
    "activate.enterCode": "Please enter your activation code",
    "activate.invalid": "Invalid or disabled activation code",
    "activate.tooMany": "Too many attempts, please try again later",
    "activate.network": "Network error, please try again",
    "activate.opening": "Opening Watch2Whoop…",
    "activate.webLogin": "Signing you in in this browser…",
    "activate.openApp": "Open in the app",
    "activate.openWeb": "Open in the browser",
    "activate.invalidAsk": "Invalid or disabled activation code — ask your admin for a new link",
    "activate.installTitle": "Install the app first",
    "activate.installLead": "On iPhone, Watch2Whoop is used through its app. Once it is installed, tap this activation link again.",
    "activate.install1": "Install TestFlight from the App Store",
    "activate.install2": "Open the TestFlight invitation e-mail, tap \"View in TestFlight\" → \"Accept\"",
    "activate.install3": "Install Watch2Whoop in TestFlight",
    "activate.install4": "Come back and tap the activation link again",
    "activate.getTestFlight": "Get TestFlight on the App Store",
    "activate.noInvite": "No invitation e-mail? Ask your admin to add your Apple ID as a tester",
    "activate.computer": "On a computer, just open the Today page in a browser",
    "activate.iosBanner": "On iPhone, please use the Watch2Whoop app — the web version is being retired",
    "activate.iosBannerLink": "How to install",
  };
  const TABLES = { zh: ZH, en: EN };
  const PREF_KEY = "be.lang";
  const UNSAVED_KEY = "be.lang.unsaved";   // chosen on the login page, not yet in the profile (app.js saves it after login)

  const device = () => {
    const l = String((navigator.languages && navigator.languages[0]) || navigator.language || "").toLowerCase();
    return l.startsWith("zh") ? "zh" : "en";
  };
  const readPref = () => { try { return localStorage.getItem(PREF_KEY) || ""; } catch (e) { return ""; } };
  // The app's native sign-in screen passes its 中文 / English choice in the fragment (#…&lang=en): taken like this page's
  // login switch (used now, saved to the profile after login), then removed from the address bar (#login=… stays for app.js)
  const fromApp = /(?:^#|&)lang=(zh|en)(?=&|$)/.exec(location.hash);
  if (fromApp) {
    try { localStorage.setItem(PREF_KEY, fromApp[1]); localStorage.setItem(UNSAVED_KEY, "1"); } catch (e) { /* private mode */ }
    const rest = location.hash.slice(1).split("&").filter((p) => p && !p.startsWith("lang=")).join("&");
    history.replaceState(null, "", location.pathname + location.search + (rest ? `#${rest}` : ""));
  }
  const resolve = (pref) => (TABLES[pref] ? pref : device());
  let lang = resolve(readPref());

  const EN_TEXT = {};   // Chinese source text -> English (filled by i18n-en.js via addEn)
  const MISSING = new Set();   // English page: Chinese keys that came back without an English text (tools/i18n_crawl.mjs lists them)
  function t(key, params) {
    let s = TABLES[lang][key] ?? (lang === "en" ? EN_TEXT[key] : undefined) ?? ZH[key] ?? key;
    if (s === key && lang === "en" && /[一-鿿]/.test(key)) MISSING.add(key);
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] != null ? String(params[k]) : m));
    return s;
  }

  // Locale for Intl / toLocaleString: zh-CN, or the device's English variant (en-AU, en-US …), en-GB otherwise
  const locale = () => {
    if (lang === "zh") return "zh-CN";
    const l = (navigator.languages && navigator.languages[0]) || navigator.language || "";
    return /^en\b/i.test(l) ? l : "en-GB";
  };
  const WD_ZH = ["日", "一", "二", "三", "四", "五", "六"];
  // "10月20日 周二" / "Tue 20 Oct" (withYear: "2026年10月20日 周二" / "Tue 20 Oct 2026")
  function dayLabel(d, withYear) {
    if (lang === "zh") return `${withYear ? `${d.getFullYear()}年` : ""}${d.getMonth() + 1}月${d.getDate()}日 周${WD_ZH[d.getDay()]}`;
    return d.toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });
  }
  const monthDay = (d) => (lang === "zh" ? `${d.getMonth() + 1}月${d.getDate()}日` : d.toLocaleDateString(locale(), { day: "numeric", month: "short" }));
  const yearMonth = (y, m) => (lang === "zh" ? `${y}年${m}月` : new Date(y, m - 1, 1).toLocaleDateString(locale(), { month: "long", year: "numeric" }));
  const monthShort = (m) => (lang === "zh" ? `${m}月` : new Date(2000, m - 1, 1).toLocaleDateString(locale(), { month: "short" }));
  // Weekday column headers starting on Monday
  const weekdaysMon = () => (lang === "zh" ? ["一", "二", "三", "四", "五", "六", "日"]
    : Array.from({ length: 7 }, (_, k) => new Date(2024, 0, 1 + k).toLocaleDateString(locale(), { weekday: "narrow" })));

  function apply(root = document) {
    if (root === document) {
      document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
      const title = document.documentElement.dataset.i18nTitle;
      if (title) document.title = t(title);
    }
    for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of root.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", t(el.dataset.i18nAria));
    for (const el of root.querySelectorAll("[data-i18n-href]")) el.setAttribute("href", t(el.dataset.i18nHref));   // a link per language (the handbook)
    for (const el of root.querySelectorAll("[data-i18n-ph]")) el.setAttribute("placeholder", t(el.dataset.i18nPh));
    for (const el of root.querySelectorAll("[data-i18n-title]")) if (el !== document.documentElement) el.setAttribute("title", t(el.dataset.i18nTitle));
  }

  // The profile setting from the server ("zh" / "en" / null = device). Returns true when the page language changed
  // (the caller reloads: strings computed at startup, e.g. tab names, are only right after a reload).
  function setPref(pref) {
    const p = TABLES[pref] ? pref : "";
    try { if (p) localStorage.setItem(PREF_KEY, p); else localStorage.removeItem(PREF_KEY); } catch (e) { /* private mode */ }
    const next = resolve(p);
    if (next === lang) return false;
    lang = next;
    return true;
  }

  // The language chosen on this device before login (login page switch): remembered, used until the profile says otherwise.
  // Returns true when the page language changed (the caller reloads or re-applies).
  function choose(l) {
    if (!TABLES[l]) return false;
    try { localStorage.setItem(PREF_KEY, l); localStorage.setItem(UNSAVED_KEY, "1"); } catch (e) { /* private mode */ }
    if (l === lang) return false;
    lang = l;
    return true;
  }
  // After login: the language picked on the login page that the profile does not have yet (null when there is none)
  const unsaved = () => { try { return localStorage.getItem(UNSAVED_KEY) ? (TABLES[readPref()] ? readPref() : null) : null; } catch (e) { return null; } };
  const markSaved = () => { try { localStorage.removeItem(UNSAVED_KEY); } catch (e) { /* private mode */ } };
  const addEn = (entries) => { Object.assign(EN_TEXT, entries); if (lang === "en") apply(); };
  // The English text table (i18n-en.js) is only loaded when the page starts in English (lang-gate.js). A page that switches to English
  // without a reload (the login card) awaits this first; it resolves false when the file cannot be fetched.
  let enLoading = null;
  const ensureEn = () => {
    if (Object.keys(EN_TEXT).length) return Promise.resolve(true);
    enLoading ??= new Promise((ok) => {
      const decl = document.querySelector('script[type="w2w/lazy"][data-lang="en"]');
      const s = document.createElement("script");
      s.src = (decl && decl.getAttribute("src")) || "i18n-en.js";
      s.onload = () => ok(true);
      s.onerror = () => { enLoading = null; ok(false); };
      document.head.append(s);
    });
    return enLoading;
  };
  // Headers for every API call so server texts (reminders, explain sheets, labels, errors) come back in this language
  const headers = () => ({ "X-Body-Lang": lang });
  const num = (v, d = 0) => Number(v).toLocaleString(locale(), { maximumFractionDigits: d });

  window.I18N = { t, apply, setPref, choose, unsaved, markSaved, addEn, ensureEn, headers, num, locale, dayLabel, monthDay, yearMonth, monthShort, weekdaysMon,
    get lang() { return lang; }, tables: TABLES, enText: EN_TEXT, missing: () => [...MISSING].filter((k) => !EN_TEXT[k]) };
  apply();   // again when i18n-en.js adds the English text table
})();
