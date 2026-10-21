/* AI integration for the Today page (docs/ai-assistant-plan.md §9). Separate from app.js, which calls into this file in only 3 places:
   1. After startup BodyAI.init(ctx): ctx = { api, openSheet, closeSheet, toast, esc, refresh, me, ai, assistantUrl }
   2. "Ask the butler" (「问管家」): when BodyAI.handlesAsk() is true (the default provider is an API type) open the in-page chat panel, otherwise keep the original redirect
   3. "AI settings" (「AI 设置」) on the Me page: BodyAI.openSettings(), BodyAI.settingsValue()
   Endpoints: /v1/users/me/ai-providers, /v1/users/me/ai-tokens, POST /v1/assistant/chat (SSE), POST /v1/assistant/chat/confirm (SSE),
   /v1/users/me/ai-fallback (+ /consent): the fallback AI (admin's DeepSeek, CNY quota) for people without their own AI; opt-in consent sheet first.
   The API key is sent to the backend only once (afterwards only the last 4 characters are visible); write actions in a chat run only after tapping "Confirm" (「确认」) on the confirmation card. */
(() => {
  "use strict";
  let C = null;            // context provided by app.js
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const money = (v) => (v == null ? "—" : `$${Number(v).toFixed(Number(v) < 1 ? 3 : 2)}`);
  const yuan = (v) => (v == null ? "—" : `¥${Number(v).toFixed(2)}`);
  const quotaText = (f) => `${yuan(f.spent_cny)} / ¥${Number(f.quota_cny)}`;
  const mmdd = (iso) => (iso ? window.I18N.monthDay(new Date(`${iso.slice(0, 10)}T12:00:00`)) : "");
  const CLIENTS = [["claude-code", tr("Claude Code / Claude 桌面版")], ["codex", "Codex CLI"], ["antigravity", "Antigravity"], ["gemini-cli", tr("Gemini CLI（API key）")], ["other", tr("其他 MCP 客户端")]];

  // ---------------------------------------------------------------- settings
  async function loadProviders() { return C.api("/v1/users/me/ai-providers"); }

  function settingsValue() {
    const a = C.ai || {};
    if (a.mode === "none" || !a.mode) return tr("jarvis.unset");
    if (a.mode === "fallback") return tr("托底 · {quota}", { quota: quotaText(a) });
    if (a.mode === "butler") return tr("身体管家");
    return a.mode === "api" || /订阅|subscription/i.test(a.display_name || "") ? `${a.display_name}` : tr("{name}（订阅）", { name: a.display_name });   // i18n-ok (matches the server's name)
  }
  // Short name for the chat panel's switcher
  function shortName(a = C.ai || {}) {
    if (a.mode === "fallback") return tr("托底");
    if (a.mode === "butler") return tr("身体管家");
    return a.display_name || tr("jarvis.unset");
  }

  // ---------------------------------------------------------------- which AI Jarvis uses (/v1/users/me/ai-choice, ai/choice.py)
  // One pick per person: the fallback AI, one of their own AIs, or (deployer only) the body butler. Takes effect at once: the answer
  // carries the new `ai` state, which goes to app.js (onAiChange) so the Jarvis button routes the new way without a reload.
  function applyAi(ai, url) {
    C.ai = ai || { mode: "none" }; C.assistantUrl = url || null;
    C.onAiChange?.(C.ai, C.assistantUrl);
  }

  async function pick(opt, after) {
    if (opt.key === "fallback" && !opt.consent) { consentSheet(null, () => pick({ ...opt, consent: true }, after)); return; }
    try {
      const r = await C.api("/v1/users/me/ai-choice", { method: "PUT", body: { key: opt.key } });
      applyAi(r.ai, r.assistant_url);
      after?.(r);
    } catch (e) { C.toast(e.message); }
  }

  function optionRows(options) {
    return options.map((o) => `<button class="item ai-opt${o.current ? " on" : ""}" data-key="${esc(o.key)}" role="radio" aria-checked="${o.current}">
      <span class="ai-radio" aria-hidden="true"></span>
      <span class="row-title">${esc(o.label)}${o.status === "disabled" ? ` <span class="badge mid">${esc(tr("已停用"))}</span>` : (o.available ? "" : ` <span class="badge plain">${esc(tr("不可用"))}</span>`)}
        <span class="small muted" style="display:block;font-weight:400">${esc(o.note || "")}</span></span></button>`).join("");
  }

  async function openSettings() {
    let d, ch;
    try { [d, ch] = await Promise.all([loadProviders(), C.api("/v1/users/me/ai-choice")]); } catch (e) { C.toast(e.message); return; }
    applyAi(ch.ai, ch.ai.assistant_url);
    const kinds = Object.fromEntries(d.kinds.map((k) => [k.kind, k]));
    const subLine = (p) => {
      if (p.category !== "api") return esc(tr("订阅"));
      if (kinds[p.kind]?.needs_key === false) return `${esc(p.model)} · ${esc(tr("服务端订阅"))}`;
      const free = p.price_in_per_mtok === 0 && p.price_out_per_mtok === 0;
      const cost = esc(free ? tr("免费额度") : tr("本月 {cost} / {budget}", { cost: money(p.month_cost_usd), budget: money(p.monthly_budget_usd) }));
      return `${esc(p.model)} · ${cost}${p.secret_last4 ? ` · key …${esc(p.secret_last4)}` : ""}`;
    };
    const row = (p) => `<button class="item" data-pid="${p.id}"><span class="row-title">${esc(p.display_name)}${p.status !== "active" ? ` <span class="badge mid">${esc(tr("已停用"))}</span>` : ""}
        <span class="small muted" style="display:block;font-weight:400">${subLine(p)}</span></span></button>`;
    const f = d.fallback;
    const fbRow = f ? `<button class="item" id="ai-fb"><span class="row-title">${esc(f.display_name)}${fbBadge(f)}
        <span class="small muted" style="display:block;font-weight:400">${esc(tr("本月 {quota} · {day} 重置", { quota: quotaText(f), day: mmdd(f.reset_on) }))}</span></span></button>` : "";
    C.openSheet(tr("me.aiSettings"), `
      <div class="small muted ai-sec">${esc(tr("Jarvis 用"))}</div>
      ${ch.options.length ? `<div class="group" role="radiogroup">${optionRows(ch.options)}</div>` : `<div class="group"><div class="item"><span class="row-title faint">${esc(tr("jarvis.unset"))}</span></div></div>`}
      ${d.providers.length || f ? `<div class="small muted ai-sec">${esc(tr("管理"))}</div><div class="group">${fbRow}${d.providers.map(row).join("")}</div>` : ""}
      <div class="btns"><button class="btn" id="ai-add">${esc(tr("添加"))}</button><button class="btn" id="ai-mcp">${esc(tr("连接客户端（MCP）"))}</button></div>
      ${d.providers.some((p) => p.category === "api") ? `<div class="kv" style="margin-top:12px"><span class="k">${esc(tr("本月"))}</span><span class="v">${money(d.month_cost_usd)}</span></div>` : ""}`, (root) => {
      $$("[data-pid]", root).forEach((b) => { b.onclick = () => editSheet(d.providers.find((p) => String(p.id) === b.dataset.pid), kinds, d.mcp); });
      $("#ai-add", root).onclick = () => addSheet(d.kinds, d.mcp);
      const fb = $("#ai-fb", root); if (fb) fb.onclick = () => fallbackSheet(f);
      $$("[data-key]", root).forEach((b) => { b.onclick = () => {
        const o = ch.options.find((x) => x.key === b.dataset.key);
        if (o.current) return;
        if (o.status === "disabled") { C.toast(tr("先在下面「管理」里启用")); return; }
        pick(o, () => { C.toast(tr("Jarvis 用：{name}", { name: o.label })); openSettings(); });
      }; });
      $("#ai-mcp", root).onclick = () => mcpSheet(d.mcp);
    });
  }

  function apiFields(k, p = {}) {
    const models = (k.models || []).map((m) => `<option value="${esc(m)}">`).join("");
    const modelField = `<div class="field"><label>${esc(tr("模型"))}</label><input class="input" id="f-model" list="f-models" value="${esc(p.model || k.default_model || "")}" placeholder="${k.kind === "openai_compatible" ? esc(tr("例：deepseek-chat、qwen-plus、llama3.1")) : ""}" autocapitalize="off" spellcheck="false"><datalist id="f-models">${models}</datalist></div>`;
    if (k.needs_key === false) {
      return `${modelField}${k.note ? `<p class="small muted" style="margin-top:8px">${esc(k.note)}</p>` : ""}`;
    }
    const isFree = p.price_in_per_mtok === 0 && p.price_out_per_mtok === 0;
    return `
      ${modelField}
      ${k.needs_base_url ? `<div class="field"><label>${esc(tr("接口地址 base_url"))}</label><input class="input" id="f-base" value="${esc(p.base_url || "")}" placeholder="${esc((k.base_url_examples || [])[0] || "https://…/v1")}" autocapitalize="off" spellcheck="false"></div>` : ""}
      <div class="field"><label>API key${p.secret_last4 ? esc(tr("（现在：…{last4}，留空不改）", { last4: p.secret_last4 })) : ""}</label><input class="input" id="f-key" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${esc(k.key_hint || "")}"></div>
      <div class="field"><label>${esc(tr("每月预算（美元，超过就停）"))}</label><input class="input" id="f-budget" type="number" inputmode="decimal" min="0" step="1" value="${esc(p.monthly_budget_usd ?? 5)}"></div>
      <label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="f-free"${isFree ? " checked data-was-free=\"1\"" : ""}> ${esc(tr("免费额度（按 $0 计）"))}</label>`;
  }

  function readApiFields(root, k, editing) {
    if (k.needs_key === false) return { model: $("#f-model", root).value.trim() };
    const box = $("#f-free", root);
    const free = box?.checked;
    // Unticking clears only the $0 price this box set; a custom unit price set some other way is left alone
    const unset = editing && box?.dataset.wasFree === "1";
    const body = {
      model: $("#f-model", root).value.trim(),
      monthly_budget_usd: Number($("#f-budget", root).value || 0),
      ...(free ? { price_in_per_mtok: 0, price_out_per_mtok: 0 } : (unset ? { price_in_per_mtok: null, price_out_per_mtok: null } : {})),
    };
    if (k.needs_base_url) body.base_url = $("#f-base", root).value.trim();
    const key = $("#f-key", root).value.trim();
    if (key) body.api_key = key; else if (!editing) body.api_key = undefined;
    return body;
  }

  function addSheet(kindsList, mcp) {
    const opts = kindsList.map((k) => `<option value="${k.kind}">${esc(k.label)}</option>`).join("");
    C.openSheet(tr("添加 AI"), `
      <div class="field"><label>${esc(tr("来源"))}</label><select class="input" id="f-kind">${opts}</select></div>
      <div id="f-more"></div>
      <label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:14px"><input type="checkbox" id="f-default" checked> ${esc(tr("设为默认（Jarvis 用它）"))}</label>
      <p class="login-error" id="f-err" role="alert"></p>
      <div class="btns"><button class="btn btn-primary" id="f-save">${esc(tr("common.save"))}</button></div>`, (root) => {
      const kinds = Object.fromEntries(kindsList.map((k) => [k.kind, k]));
      const draw = () => {
        const k = kinds[$("#f-kind", root).value];
        $("#f-more", root).innerHTML = k.category === "api" ? apiFields(k)
          : `<div class="rcard-tags" style="margin-top:12px"><span class="badge plain">${esc(tr("订阅"))}</span><span class="badge plain">MCP</span></div>${k.note ? `<p class="small muted" style="margin-top:8px">${esc(k.note)}</p>` : ""}`;
      };
      $("#f-kind", root).onchange = draw; draw();
      $("#f-save", root).onclick = async () => {
        const k = kinds[$("#f-kind", root).value];
        const body = { kind: k.kind, is_default: $("#f-default", root).checked, ...(k.category === "api" ? readApiFields(root, k, false) : {}) };
        try {
          await C.api("/v1/users/me/ai-providers", { method: "POST", body });
          await afterChange();
          if (k.category === "mcp") mcpSheet(mcp, k.client); else { C.toast(tr("common.saved")); openSettings(); }
        } catch (e) { $("#f-err", root).textContent = e.message; }
      };
    });
  }

  function editSheet(p, kinds, mcp) {
    const k = kinds[p.kind] || { kind: p.kind, category: p.category, models: [] };
    C.openSheet(p.display_name, `
      ${p.category === "api" ? apiFields(k, p) : `<div class="rcard-tags"><span class="badge plain">${esc(tr("订阅"))}</span><span class="badge plain">MCP</span></div>${k.note ? `<p class="small muted" style="margin-top:8px">${esc(k.note)}</p>` : ""}`}
      <p class="login-error" id="f-err" role="alert"></p>
      <div class="btns">${C.ai?.choice === `p:${p.id}` || p.status !== "active" ? "" : `<button class="btn" id="f-def">${esc(tr("用这个"))}</button>`}${p.category === "api" ? `<button class="btn btn-primary" id="f-save">${esc(tr("common.save"))}</button>` : `<button class="btn" id="f-mcp">${esc(tr("连接说明"))}</button>`}</div>
      <div class="btns"><button class="btn" id="f-toggle">${esc(p.status === "active" ? tr("停用") : tr("启用"))}</button><button class="btn btn-danger" id="f-del">${esc(tr("删除"))}</button></div>`, (root) => {
      const patch = async (body, msg) => {
        try { await C.api(`/v1/users/me/ai-providers/${p.id}`, { method: "PATCH", body }); await afterChange(); C.toast(msg); openSettings(); }
        catch (e) { $("#f-err", root).textContent = e.message; }
      };
      const save = $("#f-save", root); if (save) save.onclick = () => patch(readApiFields(root, k, true), tr("common.saved"));
      const def = $("#f-def", root); if (def) def.onclick = () => pick({ key: `p:${p.id}` }, () => { C.toast(tr("Jarvis 用：{name}", { name: p.display_name })); openSettings(); });
      const m = $("#f-mcp", root); if (m) m.onclick = () => mcpSheet(mcp, k.client);
      $("#f-toggle", root).onclick = () => patch({ status: p.status === "active" ? "disabled" : "active" }, tr("已更新"));
      $("#f-del", root).onclick = async () => {
        if (!confirm(tr("删除「{name}」？保存的 key 也会删掉。", { name: p.display_name }))) return;
        try { await C.api(`/v1/users/me/ai-providers/${p.id}`, { method: "DELETE" }); await afterChange(); C.toast(tr("已删除")); openSettings(); }
        catch (e) { $("#f-err", root).textContent = e.message; }
      };
    });
  }

  async function mcpSheet(mcp, defaultClient = "claude-code") {
    let d;
    try { d = await C.api("/v1/users/me/ai-tokens"); } catch (e) { C.toast(e.message); return; }
    const live = d.tokens.filter((t) => !t.revoked_at);
    C.openSheet(tr("连接客户端（MCP）"), `
      <div class="kv"><span class="k">${esc(tr("地址"))}</span><span class="v" style="word-break:break-all">${esc(mcp.url)}</span></div>
      ${live.length ? `<div class="group" style="margin-top:12px">${live.map((t) => `<div class="item"><span class="row-title">${esc(t.name.replace(/^mcp:/, ""))}${t.scopes.includes("write") ? "" : ` <span class="badge plain">${esc(tr("me.ro"))}</span>`}
          <span class="small muted" style="display:block;font-weight:400">${esc(t.last_used_at ? tr("最近使用 {when}", { when: new Date(t.last_used_at).toLocaleString(window.I18N.locale(), { hour12: false }) }) : tr("还没用过"))}</span></span>
          <button class="btn btn-danger" style="flex:none;min-height:34px" data-revoke="${t.id}">${esc(tr("吊销"))}</button></div>`).join("")}</div>` : ""}
      <div class="field"><label>${esc(tr("客户端"))}</label><select class="input" id="t-client">${CLIENTS.map(([v, l]) => `<option value="${v}"${v === defaultClient ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
      <label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="t-ro"> ${esc(tr("me.ro"))}</label>
      <div class="btns"><button class="btn btn-primary" id="t-new">${esc(tr("生成 token"))}</button></div>
      <div id="t-out"></div>`, (root) => {
      $$("[data-revoke]", root).forEach((b) => { b.onclick = async () => {
        if (!confirm(tr("吊销后这个客户端立即连不上，确定？"))) return;
        try { await C.api(`/v1/users/me/ai-tokens/${b.dataset.revoke}`, { method: "DELETE" }); await afterChange(); C.toast(tr("已吊销")); mcpSheet(mcp, defaultClient); } catch (e) { C.toast(e.message); }
      }; });
      $("#t-new", root).onclick = async () => {
        const client = $("#t-client", root).value;
        try {
          const r = await C.api("/v1/users/me/ai-tokens", { method: "POST", body: { client, read_only: $("#t-ro", root).checked } });
          const snip = r.mcp.clients[client] || r.mcp.clients["other"] || r.mcp.clients["claude-code"];
          $("#t-out", root).innerHTML = `
            <div class="notice" style="margin-top:14px">${esc(tr("只显示一次"))}</div>
            <pre class="ai-code" id="t-token">${esc(r.token)}</pre>
            <p class="small muted">1. ${esc(tr("钥匙串"))}</p><pre class="ai-code">${esc(r.mcp.keychain)}</pre>
            <p class="small muted">2. ${esc(tr("客户端"))}</p><pre class="ai-code">${esc(snip)}</pre>
            <p class="small muted">3. ${esc(r.mcp.prompt)}</p>`;
          $("#t-token", root).onclick = () => navigator.clipboard?.writeText(r.token).then(() => C.toast(tr("已复制")));
        } catch (e) { C.toast(e.message); }
      };
    });
  }

  // ---------------------------------------------------------------- fallback AI (admin's DeepSeek)
  function fbBadge(f) {
    const b = { quota: ["mid", tr("已用完")], fallback_consent: ["plain", tr("未同意")], fallback_paused: ["mid", tr("已暂停")], fallback_off: ["plain", tr("已关闭")] }[f.reason];
    return b ? ` <span class="badge ${b[0]}">${esc(b[1])}</span>` : "";
  }

  function fallbackSheet(f) {
    const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`;
    C.openSheet(tr("托底 AI"), `
      ${kv(tr("模型"), esc(f.model))}${kv(tr("本月"), quotaText(f))}${kv(tr("剩余"), yuan(f.remaining_cny))}${kv(tr("重置"), mmdd(f.reset_on))}
      ${f.balance_cny !== undefined ? kv(tr("账户余额"), yuan(f.balance_cny)) : ""}
      ${kv(tr("已同意"), f.consent ? (f.consented_at ? esc(new Date(f.consented_at).toLocaleDateString(window.I18N.locale())) : "✓") : "—")}
      ${f.message ? `<div class="notice" style="margin-top:12px">${esc(f.message)}</div>` : ""}
      <div class="rcard-tags" style="margin-top:10px"><span class="badge plain">${esc(tr("自己的默认 AI 优先"))}</span></div>
      <p class="login-error" id="fb-err" role="alert"></p>
      <div class="btns">${f.used ? "" : `<button class="btn btn-primary" id="fb-use">${esc(tr("用这个"))}</button>`}${f.consent ? (f.consented_at ? `<button class="btn" id="fb-revoke">${esc(tr("撤回同意"))}</button>` : "") : (f.used ? `<button class="btn btn-primary" id="fb-agree">${esc(tr("查看并同意"))}</button>` : "")}</div>`, (root) => {
      const u = $("#fb-use", root); if (u) u.onclick = () => pick({ key: "fallback", consent: f.consent }, () => { C.toast(tr("Jarvis 用：{name}", { name: tr("托底 AI") })); openSettings(); });
      const a = $("#fb-agree", root); if (a) a.onclick = () => consentSheet(f, openSettings);
      const r = $("#fb-revoke", root);
      if (r) r.onclick = async () => {
        if (!confirm(tr("撤回后托底 AI 不再发送你的数据，也就不能用了。确定？"))) return;
        try { await C.api("/v1/users/me/ai-fallback/consent", { method: "DELETE" }); await afterChange(); C.toast(tr("已撤回")); openSettings(); }
        catch (e) { $("#fb-err", root).textContent = e.message; }
      };
    });
  }

  // Opt-in consent for sending data to DeepSeek: shown before the first fallback chat; then() runs after agreeing
  async function consentSheet(f, then) {
    if (!f?.points) { try { f = await C.api("/v1/users/me/ai-fallback"); } catch (e) { C.toast(e.message); return; } }
    C.openSheet(tr("托底 AI · 使用前确认"), `
      <ul class="small" style="padding-left:18px;line-height:1.6;margin:0">${f.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <label class="small" style="display:flex;gap:8px;align-items:flex-start;margin-top:14px"><input type="checkbox" id="fb-check"> ${esc(tr("我已阅读，同意把上面的数据发给 DeepSeek"))}</label>
      <p class="login-error" id="fb-err" role="alert"></p>
      <div class="btns"><button class="btn btn-primary" id="fb-ok" disabled>${esc(tr("同意并开始"))}</button></div>`, (root) => {
      const ok = $("#fb-ok", root);
      $("#fb-check", root).onchange = (e) => { ok.disabled = !e.target.checked; };
      ok.onclick = async () => {
        try {
          await C.api("/v1/users/me/ai-fallback/consent", { method: "POST", body: { version: f.consent_version, agree: true } });
          await afterChange(); C.closeSheet(); then?.();
        } catch (e) { $("#fb-err", root).textContent = e.message; }
      };
    });
  }

  function quotaLine(q) {
    const el = $("#ai-quota");
    if (!el) return;
    el.hidden = !q;
    if (q) el.textContent = tr("托底 · 剩 {left} / {quota} · {day} 重置", { left: yuan(q.remaining_cny), quota: `¥${Number(q.quota_cny)}`, day: mmdd(q.reset_on) });
  }

  async function afterChange() {
    try { const me = await C.api("/v1/web/me"); applyAi(me.ai, me.assistant_url); } catch (e) { /* refresh on next startup */ }
  }

  // ---------------------------------------------------------------- chat panel
  // items: what goes with the next message: {kind: "photo", img} or {kind: "video", frames, seconds}. Frames count as images (at most MAX_IMAGES).
  const chat = { conv: null, busy: false, items: [] };
  const MAX_IMAGES = 8;
  const imageCount = () => chat.items.reduce((n, it) => n + (it.kind === "video" ? it.frames.length : 1), 0);
  const ICON = {
    photo: `<svg viewBox="0 0 24 24"><path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.8l1.4-2h4.6l1.4 2h1.8A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="12.5" r="3.2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`,
    video: `<svg viewBox="0 0 24 24"><rect x="3.5" y="6.5" width="12" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15.5 10.5l5-3v9l-5-3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
    mic: `<svg viewBox="0 0 24 24"><rect x="9" y="3.5" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  };

  function panel() {
    let p = $("#ai-chat");
    if (p) return p;
    p = document.createElement("div");
    p.id = "ai-chat"; p.className = "panel ai-chat"; p.hidden = true;
    p.setAttribute("role", "dialog"); p.setAttribute("aria-modal", "true"); p.setAttribute("aria-label", "Jarvis");
    p.innerHTML = `
      <div class="panel-head">
        <button class="close" id="ai-close" aria-label="${esc(tr("返回"))}"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <h2>Jarvis <button type="button" class="ai-switch small muted" id="ai-model" aria-haspopup="true" aria-expanded="false"></button></h2>
        <button class="close" id="ai-new" aria-label="${esc(tr("新对话"))}"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></button>
      </div>
      <div class="ai-switcher group" id="ai-switcher" role="radiogroup" hidden></div>
      <div class="ai-usage small muted" id="ai-quota" hidden></div>
      <div class="panel-body ai-log" id="ai-log"></div>
      <div class="ai-thumbs" id="ai-thumbs"></div>
      <div class="ai-rec" id="ai-rec" hidden><span class="ai-rec-dot"></span><span id="ai-rec-time">0:00</span><span class="small" id="ai-rec-hint">${esc(tr("松开识别 · 上滑取消"))}</span></div>
      <form class="ai-composer" id="ai-form" autocomplete="off">
        <label class="close ai-attach" id="ai-photo" aria-label="${esc(tr("照片"))}"><input type="file" accept="image/*" id="ai-file" multiple hidden>${ICON.photo}</label>
        <label class="close ai-attach" id="ai-video" aria-label="${esc(tr("视频"))}"><input type="file" accept="video/*" id="ai-vfile" hidden>${ICON.video}</label>
        <textarea class="input" id="ai-text" rows="1" placeholder="${esc(tr("问问题或记点什么"))}" aria-label="${esc(tr("消息"))}"></textarea>
        <button class="close ai-attach ai-mic" id="ai-mic" type="button" aria-label="${esc(tr("按住说话"))}">${ICON.mic}</button>
        <button class="btn btn-primary ai-send" id="ai-send" type="submit" aria-label="${esc(tr("发送"))}"><svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 19V5M6 11l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      </form>`;
    document.body.appendChild(p);
    $("#ai-close", p).onclick = closeChat;
    $("#ai-new", p).onclick = () => { if (!chat.busy) newConversation(); };
    $("#ai-model", p).onclick = () => toggleSwitcher();
    $("#ai-form", p).onsubmit = (e) => { e.preventDefault(); send(); };
    $("#ai-text", p).addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.matchMedia("(min-width: 1024px)").matches) { e.preventDefault(); send(); } });
    $("#ai-text", p).addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`; });
    $("#ai-file", p).onchange = (e) => addImages([...e.target.files]).finally(() => { e.target.value = ""; });
    $("#ai-vfile", p).onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) addVideo(f); };
    setupMic($("#ai-mic", p));
    return p;
  }

  // draft: a question prepared elsewhere (e.g. the "Ask AI" button of an explanation sheet); it is only put in the box, the user sends it
  function openChat(draft) {
    if (C.ai?.mode === "fallback" && !C.ai.consent) { consentSheet(null, () => openChat(draft)); return; }
    const p = panel();
    $("#ai-switcher", p).hidden = true;
    if (typeof draft === "string" && draft) $("#ai-text", p).value = draft;
    header();
    p.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => p.classList.add("open")));
    if (!$("#ai-log", p).children.length) hello();
    setTimeout(() => $("#ai-text", p).focus(), 300);
  }
  function header() {
    const p = panel();
    $("#ai-model", p).textContent = `${shortName()} ▾`;
    quotaLine(C.ai?.mode === "fallback" ? C.ai : null);
    $("#ai-photo", p).hidden = $("#ai-video", p).hidden = C.ai?.vision === false;
  }
  function newConversation() {
    chat.conv = null; $("#ai-log").innerHTML = ""; hello();
  }

  // Quick switch in the chat panel: same options as AI settings. An in-page AI (API / fallback) starts a new conversation here;
  // a subscription or the butler is used outside the page, so the panel closes and the Jarvis button now goes there.
  async function toggleSwitcher() {
    const box = $("#ai-switcher"), btn = $("#ai-model");
    if (!box.hidden) { box.hidden = true; btn.setAttribute("aria-expanded", "false"); return; }
    let ch;
    try { ch = await C.api("/v1/users/me/ai-choice"); } catch (e) { C.toast(e.message); return; }
    box.innerHTML = optionRows(ch.options) + `<button class="item" id="ai-sw-more"><span class="row-title small muted">${esc(tr("me.aiSettings"))}</span></button>`;
    box.hidden = false; btn.setAttribute("aria-expanded", "true");
    $("#ai-sw-more", box).onclick = () => { box.hidden = true; closeChat(); openSettings(); };
    $$("[data-key]", box).forEach((b) => { b.onclick = () => {
      const o = ch.options.find((x) => x.key === b.dataset.key);
      box.hidden = true; btn.setAttribute("aria-expanded", "false");
      if (o.current) return;
      if (o.status === "disabled") { C.toast(tr("这个 AI 停用了，去 AI 设置里启用")); return; }
      pick(o, () => {
        if (chat.busy) return;
        if (window.BodyAI.handlesAsk()) { header(); newConversation(); C.toast(tr("Jarvis 用：{name}", { name: o.label })); }
        else { closeChat(); C.toast(tr("Jarvis 用：{name}（点 Jarvis 打开）", { name: o.label })); }
      });
    }; });
  }

  function closeChat() {
    const p = panel(); p.classList.remove("open");
    setTimeout(() => { if (!p.classList.contains("open")) p.hidden = true; }, 280);
  }
  function hello() {
    bubble("assistant", [tr("你好，我是你的身体管家。问我今天怎么样，或者告诉我吃了什么、练了什么、哪里不舒服，我帮你记下来。"),
      C.ai?.vision === false ? "" : tr("拍照、发视频也可以。"), micOn() ? tr("按住麦克风说话。") : ""].filter(Boolean).join(window.I18N.lang === "en" ? " " : ""));
  }

  function bubble(role, text, cls = "") {
    const el = document.createElement("div");
    el.className = `ai-msg ${role} ${cls}`;
    el.textContent = text;
    $("#ai-log").appendChild(el);
    scroll();
    return el;
  }
  function note(text, tone = "") { return bubble("note", text, tone); }
  function scroll() { const l = $("#ai-log"); l.scrollTop = l.scrollHeight; }

  // ---------------------------------------------------------------- photos / video / voice (helpers in ai-media.js)
  async function addImages(files) {
    const room = MAX_IMAGES - imageCount();
    if (files.length > room) C.toast(tr("一次最多 {n} 张", { n: MAX_IMAGES }));
    for (const f of files.slice(0, Math.max(room, 0))) {
      try { chat.items.push({ kind: "photo", img: await window.BodyMedia.shrinkImage(f) }); } catch (e) { C.toast(tr("这张图读不了")); }
    }
    drawThumbs();
  }

  async function addVideo(file) {
    const room = MAX_IMAGES - imageCount();
    if (room <= 0) { C.toast(tr("一次最多 {n} 张", { n: MAX_IMAGES })); return; }
    const status = note(tr("视频处理中…"), "faint");
    try {
      const v = await window.BodyMedia.videoFrames(file, room);
      chat.items.push({ kind: "video", frames: v.frames, seconds: Math.round(v.seconds) });
      drawThumbs();
      if (asrOn()) {  // its sound track -> text in the box, for the user to check (nothing is sent until they press send)
        const a = await window.BodyMedia.videoAudio(file);
        if (a) await transcribeInto(a.wav, tr("［视频语音］"));
      }
    } catch (e) { C.toast(tr("这个视频读不了")); }
    finally { status.remove(); }
  }

  function drawThumbs() {
    const t = $("#ai-thumbs");
    t.innerHTML = chat.items.map((it, i) => {
      const im = it.kind === "video" ? it.frames[0] : it.img;
      return `<button type="button" class="ai-thumb" data-i="${i}" aria-label="${esc(tr("移除"))}"><img src="data:${im.media_type};base64,${im.data}" alt="">${it.kind === "video" ? `<span class="ai-thumb-tag">${ICON.video}${it.frames.length}</span>` : ""}</button>`;
    }).join("");
    $$("[data-i]", t).forEach((b) => { b.onclick = () => { chat.items.splice(Number(b.dataset.i), 1); drawThumbs(); }; });
  }

  // Voice: hold the mic button, release to recognise (local, on the server); the text goes into the box to be checked, never sent directly.
  // Inside the Watch2Whoop app the WebView can only record once the app grants microphone access (window.Watch2WhoopFeatures.mic);
  // otherwise, and where voice is off on the server, a tap explains the keyboard's dictation key instead.
  const asrOn = () => !!C.ai?.asr?.enabled;
  const micOn = () => asrOn() && window.BodyMedia?.micSupported() && (!window.Watch2Whoop || !!window.Watch2WhoopFeatures?.mic);

  async function transcribeInto(wavBlob, prefix = tr("［语音］")) {
    const ta = $("#ai-text");
    const status = note(tr("识别中…"), "faint");
    try {
      const r = await fetch("/v1/assistant/transcribe", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "audio/wav", "X-Body-Web": "1", ...window.I18N.headers() }, body: wavBlob });
      let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
      if (!r.ok) { note(d?.detail?.message || tr("识别失败（{status}）", { status: r.status }), "warn"); return; }
      if (!d.text) { C.toast(tr("没听清")); return; }
      ta.value = `${ta.value.trim() ? `${ta.value.trim()}\n` : ""}${prefix}${d.text}`;
      ta.dispatchEvent(new Event("input"));
      ta.focus();
    } catch (e) { note(tr("连不上，稍后再试"), "warn"); }
    finally { status.remove(); }
  }

  function setupMic(btn) {
    const box = $("#ai-rec"), time = $("#ai-rec-time"), hint = $("#ai-rec-hint");
    let rec = null, timer = null, startY = 0, cancelled = false, starting = null;
    const show = (on) => { box.hidden = !on; btn.classList.toggle("on", on); };
    const tick = () => {
      const s = rec ? rec.elapsed() : 0;
      time.textContent = `0:${String(Math.floor(s)).padStart(2, "0")}`;
      if (s >= window.BodyMedia.MAX_SECONDS) finish();
    };
    async function begin(e) {
      if (!micOn()) {
        note(asrOn() ? tr("这里不能录音：点输入框，用键盘上的 🎤 听写") : tr("语音识别没开通：点输入框，用键盘上的 🎤 听写"), "warn");
        $("#ai-text").focus();
        return;
      }
      if (chat.busy || rec) return;
      e.preventDefault();
      btn.setPointerCapture?.(e.pointerId);
      startY = e.clientY; cancelled = false;
      rec = window.BodyMedia.recorder();
      show(true); hint.textContent = tr("松开识别 · 上滑取消"); tick();
      starting = rec.start().then(() => { timer = setInterval(tick, 250); }).catch(() => {
        rec = null; show(false); note(tr("麦克风不可用：在系统设置里允许，或用键盘上的 🎤 听写"), "warn");
      });
    }
    async function finish() {
      if (!rec) return;
      const r = rec; rec = null; clearInterval(timer); show(false);
      await starting;
      if (cancelled) { r.cancel(); return; }
      const out = await r.stop().catch(() => null);
      if (!out) { C.toast(tr("按住说话，说完再松开")); return; }
      await transcribeInto(out.wav);
    }
    btn.addEventListener("pointerdown", begin);
    btn.addEventListener("pointermove", (e) => {
      if (!rec) return;
      cancelled = startY - e.clientY > 60;
      hint.textContent = cancelled ? tr("松开取消") : tr("松开识别 · 上滑取消");
      box.classList.toggle("cancel", cancelled);
    });
    btn.addEventListener("pointerup", finish);
    btn.addEventListener("pointercancel", () => { cancelled = true; finish(); });
    btn.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  async function send() {
    if (chat.busy) return;
    const ta = $("#ai-text");
    let text = ta.value.trim();
    if (!text && !chat.items.length) return;
    const items = chat.items; chat.items = []; drawThumbs();
    const photos = items.filter((it) => it.kind === "photo"), videos = items.filter((it) => it.kind === "video");
    const images = [...photos.map((it) => it.img), ...videos.flatMap((it) => it.frames)];
    // Tell the model what the frames are (the server forwards the text as is)
    const tags = videos.map((v) => tr("［视频 {n} 帧，约 {s} 秒］", { n: v.frames.length, s: v.seconds })).join("");
    if (tags) text = `${tags}${text ? "\n" : ""}${text}`;
    const shown = [photos.length ? tr("［图片 {n} 张］", { n: photos.length }) : "", tags].filter(Boolean).join("");
    bubble("user", (ta.value.trim() + (shown ? `${ta.value.trim() ? "\n" : ""}${shown}` : "")));
    ta.value = ""; ta.style.height = "auto";
    await stream("/v1/assistant/chat", { message: text, images, conversation_id: chat.conv });
  }

  async function stream(path, body) {
    chat.busy = true; $("#ai-send").disabled = true;
    let cur = null, wrote = false;
    const status = note("…", "faint");
    try {
      const r = await fetch(path, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-Body-Web": "1", ...window.I18N.headers() }, body: JSON.stringify(body) });
      if (!r.ok) {
        let d = null; try { d = (await r.json()).detail; } catch (e) { /* no body */ }
        status.remove();
        const msg = typeof d === "string" ? d : d?.message || tr("err.request", { status: r.status });
        if (d?.code === "fallback_consent") { closeChat(); consentSheet(null, () => openChat()); return; }
        if (d?.quota_cny != null) quotaLine(d);
        const el = note(msg, "warn");
        if (d?.assistant_url) { const a = document.createElement("a"); a.href = d.assistant_url; a.target = "_blank"; a.rel = "noopener"; a.className = "btn"; a.textContent = tr("打开"); el.appendChild(a); }
        if (r.status === 401) C.toast(tr("登录过期了，请重新登录"));
        return;
      }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = /^event: (.*)$/m.exec(block)?.[1], data = /^data: (.*)$/m.exec(block)?.[1];
          if (!ev || data == null) continue;
          const d = JSON.parse(data);
          if (ev === "conversation") { chat.conv = d.id; if (d.quota) quotaLine(d.quota); }
          else if (ev === "text") { if (!cur) { status.remove(); cur = bubble("assistant", ""); } cur.textContent += d.delta; scroll(); }
          else if (ev === "tool") {
            cur = null;
            status.textContent = { running: tr("查看 {name}…", { name: d.name }), done: `✓ ${d.name}`, error: `✗ ${d.name}`, rejected: tr("已取消 {name}", { name: d.name }) }[d.status] || d.name;
            if (d.status === "done" && body.call_id) wrote = true;
          }
          else if (ev === "confirm") { status.remove(); d.calls.forEach(confirmCard); }
          else if (ev === "usage") { usageLine(d); }
          else if (ev === "notice") note(d.message, "warn");
          else if (ev === "error") { status.remove(); note(d.message || tr("出错了"), "warn"); if (d.quota_cny != null) quotaLine(d); }
          else if (ev === "done") { status.remove(); }
        }
      }
    } catch (e) {
      status.remove(); note(tr("连不上（没联网，或 Tailscale 没开）"), "warn");
    } finally {
      if (status.isConnected && status.textContent === "…") status.remove();
      chat.busy = false; $("#ai-send").disabled = false;
      if (wrote) C.refresh?.();  // Something was written to the database: refresh the Today page data
    }
  }

  function usageLine(d) {
    if (d.currency === "CNY") {
      quotaLine({ remaining_cny: d.remaining_cny, quota_cny: d.quota_cny, reset_on: C.ai?.reset_on });
      if (C.ai?.mode === "fallback") Object.assign(C.ai, { spent_cny: d.month_cost_cny, remaining_cny: d.remaining_cny });
      return;
    }
    const el = $("#ai-usage") || (() => { const x = document.createElement("div"); x.id = "ai-usage"; x.className = "ai-usage small muted"; $("#ai-log").after(x); return x; })();
    el.textContent = tr("本月 {cost} / {budget}", { cost: money(d.month_cost_usd), budget: money(d.budget_usd) });
  }

  function confirmCard(c) {
    const el = document.createElement("div");
    el.className = "ai-confirm";
    el.innerHTML = `<div class="small muted">${esc(tr("要写入（确认后才执行）"))}</div><div class="ai-confirm-text">${esc(c.summary)}</div>
      <div class="btns"><button class="btn" data-no>${esc(tr("不要"))}</button><button class="btn btn-primary" data-yes>${esc(tr("确认"))}</button></div>`;
    $("#ai-log").appendChild(el); scroll();
    const decide = async (approve) => {
      if (chat.busy) return;
      el.querySelector(".btns").innerHTML = `<span class="small muted">${esc(approve ? tr("已确认") : tr("已取消"))}</span>`;
      await stream("/v1/assistant/chat/confirm", { call_id: c.call_id, approve });
    };
    $("[data-yes]", el).onclick = () => decide(true);
    $("[data-no]", el).onclick = () => decide(false);
  }

  // ---------------------------------------------------------------- towards app.js
  window.BodyAI = {
    init(ctx) { C = ctx; },
    handlesAsk() { return !!C && (C.ai?.mode === "api" || C.ai?.mode === "fallback"); },
    openChat, openSettings, settingsValue,
    reset() { chat.conv = null; chat.items = []; const p = $("#ai-chat"); if (p) p.remove(); },
  };
})();
