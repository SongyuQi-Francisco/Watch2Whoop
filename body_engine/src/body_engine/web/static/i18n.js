/* Tiny i18n layer for the Today page and the activation page (issue #13). No build step: load this file before the other scripts.
   - Language: the user's profile setting `language` (zh / en, from /v1/web/me, cached in localStorage "be.lang"); empty = follow the device
     (navigator.language: zh* -> zh, anything else -> en).
   - I18N.t(key, params) looks the key up in the current language's table, falls back to zh, then to the key itself; "{name}" placeholders
     are filled from params. Page scripts alias it as `tr` (app.js has many local variables named `t`).
   - Static HTML: data-i18n="key" (text), data-i18n-aria (aria-label), data-i18n-ph (placeholder); <html data-i18n-title> sets the title.
   - Every key must exist in both tables with the same placeholders: tests/test_i18n.py checks this, and that every key used in the
     page scripts / HTML exists. Add new languages as another table (same keys). Stage-2 checklist: docs/i18n-plan.md. */
(() => {
  "use strict";
  const ZH = {
    "app.title": "身体 · 今天",
    "tab.today": "今天",
    "tab.trends": "趋势",
    "tab.food": "饮食",
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
    "activate.enterCode": "请输入激活码",
    "activate.invalid": "激活码无效或已停用",
    "activate.tooMany": "失败次数太多，请稍后再试",
    "activate.network": "网络连接失败，请重试",
  };
  const EN = {
    "app.title": "Body · Today",
    "tab.today": "Today",
    "tab.trends": "Trends",
    "tab.food": "Food",
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
    "activate.enterCode": "Please enter your activation code",
    "activate.invalid": "Invalid or disabled activation code",
    "activate.tooMany": "Too many attempts, please try again later",
    "activate.network": "Network error, please try again",
  };
  const TABLES = { zh: ZH, en: EN };
  const PREF_KEY = "be.lang";

  const device = () => {
    const l = String((navigator.languages && navigator.languages[0]) || navigator.language || "").toLowerCase();
    return l.startsWith("zh") ? "zh" : "en";
  };
  const readPref = () => { try { return localStorage.getItem(PREF_KEY) || ""; } catch (e) { return ""; } };
  const resolve = (pref) => (TABLES[pref] ? pref : device());
  let lang = resolve(readPref());

  function t(key, params) {
    let s = TABLES[lang][key] ?? ZH[key] ?? key;
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
    for (const el of root.querySelectorAll("[data-i18n-ph]")) el.setAttribute("placeholder", t(el.dataset.i18nPh));
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

  window.I18N = { t, apply, setPref, locale, dayLabel, monthDay, yearMonth, monthShort, weekdaysMon, get lang() { return lang; }, tables: TABLES };
  apply();
})();
