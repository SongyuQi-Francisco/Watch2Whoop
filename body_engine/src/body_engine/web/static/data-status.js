/* 「我 → 数据状态」 (issue #163, flag data_status): per health data type what the server actually received, because iOS
   never tells an app whether Health read access was granted. GET /v1/users/me/data-status (sync/data_status.py): the
   latest day, days / records of the last 7 days, sources, status ok / stale / never; plus the strength-training records of
   the last weeks by source (#210). Tapping a type that is stale or never
   received shows likely reasons and the steps to switch it on in iOS Settings (the user taps them; nothing is changed for
   them). Works in a desktop browser too; inside the Watch2Whoop app there is also a link to its native Phone sync page.
   Separate from app.js: registered in window.BodyPlugins (init / meItem / bindMe). reasonsFor / stepsFor are pure
   (module.exports for tests/test_data_status.py). */
(() => {
  "use strict";
  const tr = (k, p) => window.I18N.t(k, p);   // tests/test_data_status.py runs this file in node with a stub I18N
  const $ = (s, r = document) => r.querySelector(s);
  let C = null;
  let ME = null;
  let data = null;
  let CHEV = "";   // the page's chevron (passed to meItem)

  // The switch's name in iOS Settings → Health → Data Access & Devices → Watch2Whoop
  const IOS_NAME = {
    heart_rate: tr("心率"), hrv: tr("心率变异性"), resting_heart_rate: tr("静息心率"), sleep: tr("睡眠"), steps: tr("步数"),
    active_energy: tr("活动能量"), workouts: tr("体能训练"), weight: tr("体重"), blood_oxygen: tr("血氧"),
    respiratory_rate: tr("呼吸速率"), wrist_temperature: tr("睡眠时手腕温度"), vo2max: tr("心肺适能"), heartbeats: tr("心跳序列"),
    ecg: tr("心电图"), water: tr("水"),
  };
  const TONE = { ok: "good", stale: "mid", never: "bad" };
  const LABEL = { ok: tr("正常"), stale: tr("很久没收到"), never: tr("从没收到") };
  const NOTE = {
    sdk_unsupported: tr("app 的同步还读不了这一项，要用快捷指令或导入健康历史"),
    app_direct: tr("这一项由 app 打开时自己读取：解锁手机、打开一次 app"),
    consent: tr("要先在今天页确认新版同意书"),
    on_demand: tr("只有在手表上测过心电图才会有"),
    needs_device: tr("要有智能体重秤，或在「健康」里手动记录"),
    outdoor: tr("戴手表户外步行、跑步或徒步后才会估算"),
    flag_off: tr("这台服务器还没开饮水功能"),
  };
  // A permission does not help: the app cannot read it yet / the server does not take it
  const noFix = (t) => !!t.notes?.some((n) => n === "sdk_unsupported" || n === "flag_off");

  /** Likely reasons for a type that is not ok (texts in the page's language). */
  function reasonsFor(t) {
    if (!t || t.status === "ok") return [];
    const notes = (t.notes || []).filter((n) => NOTE[n]).map((n) => NOTE[n]);
    if (noFix(t)) return notes;
    const general = t.status === "never"
      ? [tr("健康权限里没有打开这一项"), tr("手表或 iPhone 没有记录这类数据"), tr("第一次同步还没传完")]
      : [tr("健康权限被关掉了"), tr("最近没戴手表，或很久没打开 app"), tr("后台同步被 iOS 暂停：打开一次 app")];
    return [...general, ...notes];
  }

  /** Steps to switch the type on in iOS (the user taps them); [] when a permission would not help. */
  function stepsFor(t) {
    if (!t || t.status === "ok" || noFix(t)) return [];
    return [
      tr("打开 iPhone 的「设置」"),
      tr("点「健康」（新版 iOS：「App」→「健康」）"),
      tr("点「数据访问与设备」→「Watch2Whoop」"),
      tr("打开「{name}」，或者全部打开", { name: IOS_NAME[t.key] || t.label }),
      tr("回到 Watch2Whoop：「我 → 手机同步」→「立即同步」"),
    ];
  }

  const CSS = `.ds-sum{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.ds-row .val{display:flex;gap:8px;align-items:center}
.ds-steps{margin:6px 0 0;padding-left:20px;font-size:14px;line-height:1.6}
.ds-list{margin:6px 0 0;padding-left:18px;font-size:14px;line-height:1.6;color:var(--text-2)}
.ds-h{font-size:13px;font-weight:600;margin:16px 0 0;color:var(--text-2)}`;

  function injectCss() {
    if ($("#data-status-css")) return;
    const st = document.createElement("style");
    st.id = "data-status-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  async function load() {
    try { data = await C.api("/v1/users/me/data-status"); } catch (e) { data = null; }
    return data;
  }

  const badge = (status) => `<span class="badge ${TONE[status]}">${C.esc(LABEL[status])}</span>`;
  const ago = (t) => (t.days_since == null ? "—" : t.days_since === 0 ? tr("今天") : t.days_since === 1 ? tr("昨天")
    : tr("{n} 天前", { n: t.days_since }));
  const when = (iso) => (iso ? new Date(iso).toLocaleString(window.I18N.locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
  const kv = (k, v) => `<div class="kv"><span class="k">${C.esc(k)}</span><span class="v">${C.esc(v)}</span></div>`;
  const shortVal = () => (data ? `${data.summary.ok}/${data.types.length}` : "");

  /** Strength-training records of the last weeks by source (#210): the same records body age counts. */
  function strengthText(st) {
    if (!st) return null;
    const sp = st.sparky === "connected" ? String(st.sessions.sparky ?? 0) : st.sparky === "not_linked" ? tr("未连接") : tr("读不到");
    // flag ai_parity: the back end is never named in the app
    const db = typeof window !== "undefined" && window.BodyFlags?.on("ai_parity") ? tr("底层数据库") : "SparkyFitness";
    return {
      days: tr("{d} 天有记录", { d: st.days }),
      sources: `${tr("手表")} ${st.sessions.watch} · ${db} ${sp} · Hevy ${st.sessions.hevy}`,
    };
  }
  function strengthHtml(st) {
    const t = strengthText(st);
    if (!t) return "";
    return `<div class="ds-h">${C.esc(tr("力量训练记录（近 {n} 周）", { n: st.weeks }))}</div>${kv(tr("天数"), t.days)}${kv(tr("来源（次）"), t.sources)}`;
  }

  function listHtml() {
    if (!data) return `<p class="empty">${C.esc(tr("读取失败"))}</p>`;
    const s = data.summary;
    const sum = `<div class="ds-sum">${["ok", "stale", "never"].map((k) => `<span class="badge ${TONE[k]}">${C.esc(LABEL[k])} ${s[k]}</span>`).join("")}</div>`;
    const rows = data.types.map((t) => `<button class="item ds-row" data-ds="${C.esc(t.key)}"><span class="row-title">${C.esc(t.label)}</span>
      <span class="val">${C.esc(ago(t))} ${badge(t.status)}</span>${CHEV}</button>`).join("");
    const ch = data.channels || {};
    const chan = [kv(tr("手机同步：最新数据"), when(ch.ow_last_sample_at)), kv(tr("app 直传：最近上传"), when(ch.app_upload_at)),
      ch.shortcut_upload_at ? kv(tr("快捷指令：最近上传"), when(ch.shortcut_upload_at)) : ""].join("");
    const inApp = window.Watch2Whoop ? `<div class="btns"><a class="btn" href="watch2whoop://sync">${C.esc(tr("me.phoneSync"))}</a></div>` : "";
    return `${sum}<div class="group">${rows}</div><div class="ds-h">${C.esc(tr("收到时间"))}</div>${chan}${strengthHtml(data.strength)}${inApp}`;
  }

  function detail(t) {
    const reasons = reasonsFor(t), steps = stepsFor(t);
    const body = `${kv(tr("状态"), LABEL[t.status])}
      ${kv(tr("最近收到"), t.last_at ? when(t.last_at) : t.last_date || "—")}
      ${kv(tr("近 {n} 天", { n: data.window_days }), tr("{d} 天 · {n} 条", { d: t.days, n: t.count }))}
      ${t.sources.length ? kv(tr("来源"), t.sources.join(" · ")) : ""}
      ${reasons.length ? `<div class="ds-h">${C.esc(tr("可能的原因"))}</div><ul class="ds-list">${reasons.map((r) => `<li>${C.esc(r)}</li>`).join("")}</ul>` : ""}
      ${steps.length ? `<div class="ds-h">${C.esc(tr("在 iPhone 上打开"))}</div><ol class="ds-steps">${steps.map((x) => `<li>${C.esc(x)}</li>`).join("")}</ol>
        <p class="small faint" style="margin:8px 0 0">${C.esc(tr("也可以：「健康」app → 右上角头像 →「App」→「Watch2Whoop」"))}</p>` : ""}
      ${window.Watch2Whoop && steps.length ? `<div class="btns"><a class="btn" href="watch2whoop://sync">${C.esc(tr("me.phoneSync"))}</a></div>` : ""}
      <div class="btns"><button class="btn" type="button" id="ds-back">${C.esc(tr("返回"))}</button></div>`;
    C.openSheet(t.label, body, (root) => { $("#ds-back", root).onclick = open; });
  }

  function bindList(root) {
    for (const b of root.querySelectorAll("[data-ds]")) b.onclick = () => detail(data.types.find((t) => t.key === b.dataset.ds));
  }

  function open() {
    if (data) { C.openSheet(tr("数据状态"), listHtml(), bindList); return; }
    C.openSheet(tr("数据状态"), `<p class="para" style="margin:0">${C.esc(tr("读取中…"))}</p>`, async (root) => {
      await load();
      root.innerHTML = listHtml();
      bindList(root);
      updateItem();
    });
  }

  function updateItem() {
    const v = document.getElementById("me-data-status-val");
    if (v) v.textContent = shortVal();
  }

  const on = () => !!ME && !!window.BodyFlags?.on("data_status");

  if (typeof module !== "undefined") module.exports = { reasonsFor, stepsFor, strengthText, IOS_NAME };
  if (typeof window === "undefined") return;
  (window.BodyPlugins = window.BodyPlugins || []).push({
    meGroup: "main",
    init: (ctx, me) => { C = ctx; ME = me?.me || null; injectCss(); },
    meItem: (chev) => (CHEV = chev || "", on() ? `<button class="item" id="me-data-status"><span class="row-title">${C.esc(tr("数据状态"))}</span><span class="val" id="me-data-status-val">${C.esc(shortVal())}</span>${chev || ""}</button>` : ""),
    bindMe: (view) => {
      const b = $("#me-data-status", view);
      if (!b) return;
      b.onclick = () => { data = null; open(); };
      load().then(updateItem);
    },
  });
})();
