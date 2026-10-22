/* AI integration for the Today page (docs/ai-assistant-plan.md §9). Separate from app.js, which calls into this file in only 3 places:
   1. After startup BodyAI.init(ctx): ctx = { api, openSheet, closeSheet, toast, esc, refresh, page, isSelf, go, me, ai, assistantUrl }
   2. "Ask the butler" (「问管家」): when BodyAI.handlesAsk() is true (the default provider is an API type) open the in-page chat panel, otherwise keep the original redirect
   3. "AI settings" (「AI 设置」) on the Me page: BodyAI.openSettings(), BodyAI.settingsValue()
   Endpoints: /v1/users/me/ai-providers, /v1/users/me/ai-tokens, POST /v1/assistant/chat (SSE), POST /v1/assistant/chat/confirm (SSE),
   /v1/users/me/ai-fallback (+ /consent): the fallback AI (admin's DeepSeek, CNY quota) for people without their own AI; opt-in consent sheet first.
   /v1/users/me/ai-cards (+ /{id}/use): Jarvis allowance cards (#182, flag ai_cards): count next to the allowance, the card list, use after a confirm.
   The API key is sent to the backend only once (afterwards only the last 4 characters are visible); write actions in a chat run only after tapping "Confirm" (「确认」) on the confirmation card. */
(() => {
  "use strict";
  let C = null;            // context provided by app.js
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const money = (v) => (v == null ? "—" : `$${Number(v).toFixed(Number(v) < 1 ? 3 : 2)}`);
  // The fallback quota is shown to users as a remaining percentage only (no amounts, no provider / model name; owner, 2026-10-22)
  const leftPct = (f) => (f?.remaining_pct != null ? f.remaining_pct
    : f?.quota_cny ? Math.max(0, Math.min(100, Math.round(100 * (f.remaining_cny ?? (f.quota_cny - (f.spent_cny || 0))) / f.quota_cny))) : null);
  const quotaText = (f) => tr("剩 {p}%", { p: leftPct(f) ?? "—" });
  const mmdd = (iso) => (iso ? window.I18N.monthDay(new Date(`${iso.slice(0, 10)}T12:00:00`)) : "");
  const CLIENTS = [["claude-code", tr("Claude Code / Claude 桌面版")], ["codex", "Codex CLI"], ["antigravity", "Antigravity"], ["gemini-cli", tr("Gemini CLI（API key）")], ["other", tr("其他 MCP 客户端")]];

  // ---------------------------------------------------------------- settings
  async function loadProviders() { return C.api("/v1/users/me/ai-providers"); }

  function settingsValue() {
    const a = C.ai || {};
    if (a.mode === "none" || !a.mode) return tr("jarvis.unset");
    if (a.mode === "fallback") return `Jarvis · ${quotaText(a)}${cardCount(a) ? ` · ${cardsText(a)}` : ""}`;
    if (a.mode === "butler") return tr("身体管家");
    return a.mode === "api" || /订阅|subscription/i.test(a.display_name || "") ? `${a.display_name}` : tr("{name}（订阅）", { name: a.display_name });   // i18n-ok (matches the server's name)
  }
  // Short name for the chat panel's switcher
  function shortName(a = C.ai || {}) {
    if (a.mode === "fallback") return "Jarvis";
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
      if (p.retired) return esc(tr("已下线，只能删除"));
      if (p.category !== "api") return esc(tr("订阅"));
      const free = p.price_in_per_mtok === 0 && p.price_out_per_mtok === 0;
      const cost = esc(free ? tr("免费额度") : tr("本月 {cost} / {budget}", { cost: money(p.month_cost_usd), budget: money(p.monthly_budget_usd) }));
      return `${esc(p.model)} · ${cost}${p.secret_last4 ? ` · key …${esc(p.secret_last4)}` : ""}`;
    };
    const row = (p) => `<button class="item" data-pid="${p.id}"><span class="row-title">${esc(p.display_name)}${p.retired ? ` <span class="badge plain">${esc(tr("已下线"))}</span>` : p.status !== "active" ? ` <span class="badge mid">${esc(tr("已停用"))}</span>` : ""}
        <span class="small muted" style="display:block;font-weight:400">${subLine(p)}</span></span></button>`;
    const f = d.fallback;
    const fbRow = f ? `<button class="item" id="ai-fb"><span class="row-title">${esc(f.display_name)}${fbBadge(f)}
        <span class="small muted" style="display:block;font-weight:400">${esc(tr("本月{quota} · {day} 重置", { quota: quotaText(f), day: mmdd(f.reset_on) }))}${cardCount(f) ? ` · ${esc(cardsText(f))}` : ""}</span></span></button>` : "";
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

  const WEB_KINDS = ["anthropic_api", "openai_api", "gemini_api"];   // ai/catalog.WEB_TOOL_KINDS: the vendor's own web search, off by default

  function apiFields(k, p = {}) {
    const models = (k.models || []).map((m) => `<option value="${esc(m)}">`).join("");
    const modelField = `<div class="field"><label>${esc(tr("模型"))}</label><input class="input" id="f-model" list="f-models" value="${esc(p.model || k.default_model || "")}" placeholder="${k.kind === "openai_compatible" ? esc(tr("例：deepseek-chat、qwen-plus、llama3.1")) : ""}" autocapitalize="off" spellcheck="false"><datalist id="f-models">${models}</datalist></div>`;
    const isFree = p.price_in_per_mtok === 0 && p.price_out_per_mtok === 0;
    return `
      ${modelField}
      ${k.needs_base_url ? `<div class="field"><label>${esc(tr("接口地址 base_url"))}</label><input class="input" id="f-base" value="${esc(p.base_url || "")}" placeholder="${esc((k.base_url_examples || [])[0] || "https://…/v1")}" autocapitalize="off" spellcheck="false"></div>` : ""}
      <div class="field"><label>API key${p.secret_last4 ? esc(tr("（现在：…{last4}，留空不改）", { last4: p.secret_last4 })) : ""}</label><input class="input" id="f-key" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${esc(k.key_hint || "")}"></div>
      <div class="field"><label>${esc(tr("每月预算（美元，超过就停）"))}</label><input class="input" id="f-budget" type="number" inputmode="decimal" min="0" step="1" value="${esc(p.monthly_budget_usd ?? 5)}"></div>
      <label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="f-free"${isFree ? " checked data-was-free=\"1\"" : ""}> ${esc(tr("免费额度（按 $0 计）"))}</label>
      ${WEB_KINDS.includes(k.kind) ? `<label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="f-web"${p.web_tools ? " checked" : ""}> ${esc(tr("允许联网（官方搜索 / 读网页，用来查价格；费用算在你的 key 上）"))}</label>` : ""}`;
  }

  function readApiFields(root, k, editing) {
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
    const web = $("#f-web", root);
    if (web) body.web_tools = web.checked;
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
    const del = (root) => async () => {
      if (!confirm(tr("删除「{name}」？保存的 key 也会删掉。", { name: p.display_name }))) return;
      try { await C.api(`/v1/users/me/ai-providers/${p.id}`, { method: "DELETE" }); await afterChange(); C.toast(tr("已删除")); openSettings(); }
      catch (e) { $("#f-err", root).textContent = e.message; }
    };
    if (p.retired) {   // a kind that no longer exists (the server-side Gemini CLI): it can only be deleted
      C.openSheet(p.display_name, `<p class="para">${esc(tr("这个 AI 提供商已下线，不能再用，只能删除。"))}</p>
        <p class="login-error" id="f-err" role="alert"></p>
        <div class="btns"><button class="btn btn-danger" id="f-del">${esc(tr("删除"))}</button></div>`, (root) => { $("#f-del", root).onclick = del(root); });
      return;
    }
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
      $("#f-del", root).onclick = del(root);
    });
  }

  async function mcpSheet(mcp, defaultClient = "claude-code") {
    let d;
    try { d = await C.api("/v1/users/me/ai-tokens"); } catch (e) { C.toast(e.message); return; }
    const live = d.tokens.filter((t) => !t.revoked_at);
    const conns = d.connections || [];
    const when = (t) => new Date(t).toLocaleString(window.I18N.locale(), { hour12: false });
    C.openSheet(tr("连接客户端（MCP）"), `
      <div class="kv"><span class="k">${esc(tr("地址"))}</span><span class="v" style="word-break:break-all">${esc(mcp.url)}</span></div>
      ${d.mcp?.oauth ? `<p class="small muted" style="margin:8px 0 0">${esc(d.mcp.oauth)}</p>` : ""}
      ${conns.length ? `<p class="small muted" style="margin:14px 0 0">${esc(tr("已连接的应用"))}</p><div class="group" style="margin-top:6px">${conns.map((c) => `<div class="item"><span class="row-title">${esc(c.client_name || tr("未命名的应用"))}${c.scopes.includes("write") ? "" : ` <span class="badge plain">${esc(tr("me.ro"))}</span>`}
          <span class="small muted" style="display:block;font-weight:400">${esc(c.redirect_host)} · ${esc(c.last_used_at ? tr("最近使用 {when}", { when: when(c.last_used_at) }) : tr("还没用过"))}</span></span>
          <button class="btn btn-danger" style="flex:none;min-height:34px" data-disconnect="${c.id}">${esc(tr("断开"))}</button></div>`).join("")}</div>` : ""}
      ${live.length ? `<div class="group" style="margin-top:12px">${live.map((t) => `<div class="item"><span class="row-title">${esc(t.name.replace(/^mcp:/, ""))}${t.scopes.includes("write") ? "" : ` <span class="badge plain">${esc(tr("me.ro"))}</span>`}
          <span class="small muted" style="display:block;font-weight:400">${esc(t.last_used_at ? tr("最近使用 {when}", { when: new Date(t.last_used_at).toLocaleString(window.I18N.locale(), { hour12: false }) }) : tr("还没用过"))}</span></span>
          <button class="btn btn-danger" style="flex:none;min-height:34px" data-revoke="${t.id}">${esc(tr("吊销"))}</button></div>`).join("")}</div>` : ""}
      <div class="field"><label>${esc(tr("客户端"))}</label><select class="input" id="t-client">${CLIENTS.map(([v, l]) => `<option value="${v}"${v === defaultClient ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
      <label class="small muted" style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="t-ro"> ${esc(tr("me.ro"))}</label>
      <div class="btns"><button class="btn btn-primary" id="t-new">${esc(tr("生成 token"))}</button></div>
      <div id="t-out"></div>`, (root) => {
      $$("[data-disconnect]", root).forEach((b) => { b.onclick = async () => {
        if (!confirm(tr("断开后这个应用立即连不上，确定？"))) return;
        try { await C.api(`/v1/users/me/ai-connections/${b.dataset.disconnect}`, { method: "DELETE" }); await afterChange(); C.toast(tr("已断开")); mcpSheet(mcp, defaultClient); } catch (e) { C.toast(e.message); }
      }; });
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
    C.openSheet("Jarvis", `
      ${kv(tr("本月剩余"), `${leftPct(f) ?? "—"}%`)}${kv(tr("重置"), mmdd(f.reset_on))}
      ${cardsOn() ? `<button class="item" id="fb-cards"><span class="row-title">${esc(tr("额度卡"))}</span><span class="v">${cardCount(f)} ›</span></button>` : ""}
      ${kv(tr("已同意"), f.consent ? (f.consented_at ? esc(new Date(f.consented_at).toLocaleDateString(window.I18N.locale())) : "✓") : "—")}
      ${f.message ? `<div class="notice" style="margin-top:12px">${esc(f.message)}</div>` : ""}
      <div class="rcard-tags" style="margin-top:10px"><span class="badge plain">${esc(tr("自己的默认 AI 优先"))}</span></div>
      <p class="login-error" id="fb-err" role="alert"></p>
      <div class="btns">${f.used ? "" : `<button class="btn btn-primary" id="fb-use">${esc(tr("用这个"))}</button>`}${f.consent ? (f.consented_at ? `<button class="btn" id="fb-revoke">${esc(tr("撤回同意"))}</button>` : "") : (f.used ? `<button class="btn btn-primary" id="fb-agree">${esc(tr("查看并同意"))}</button>` : "")}</div>`, (root) => {
      const cb = $("#fb-cards", root); if (cb) cb.onclick = () => cardsSheet();
      const u = $("#fb-use", root); if (u) u.onclick = () => pick({ key: "fallback", consent: f.consent }, () => { C.toast(tr("Jarvis 用：{name}", { name: "Jarvis" })); openSettings(); });
      const a = $("#fb-agree", root); if (a) a.onclick = () => consentSheet(f, openSettings);
      const r = $("#fb-revoke", root);
      if (r) r.onclick = async () => {
        if (!confirm(tr("撤回后 Jarvis 不再发送你的数据，也就不能用了。确定？"))) return;
        try { await C.api("/v1/users/me/ai-fallback/consent", { method: "DELETE" }); await afterChange(); C.toast(tr("已撤回")); openSettings(); }
        catch (e) { $("#fb-err", root).textContent = e.message; }
      };
    });
  }

  // Opt-in consent for sending data to DeepSeek: shown before the first fallback chat; then() runs after agreeing
  async function consentSheet(f, then) {
    if (!f?.points) { try { f = await C.api("/v1/users/me/ai-fallback"); } catch (e) { C.toast(e.message); return; } }
    C.openSheet(tr("Jarvis · 使用前确认"), `
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
    if (!q) return;
    if (q.cards == null && C.ai?.cards) q = { ...q, cards: C.ai.cards };   // stream events carry no card counts
    el.textContent = tr("Jarvis · 本月剩余 {p}% · {day} 重置", { p: leftPct(q) ?? "—", day: mmdd(q.reset_on) })
      + (cardCount(q) ? ` · ${cardsText(q)}` : "");
    el.classList.toggle("ai-quota-link", cardsOn());
    el.onclick = cardsOn() ? () => cardsSheet() : null;
  }

  // ---------------------------------------------------------------- Jarvis allowance cards (#182, flag ai_cards; ai/quota_cards.py)
  // reset = this month's used allowance back to 0, extend = +N% this month. Shown as percentages only (no amounts, no provider).
  // Using one asks first; the server counts a card once however often it is tapped, and the new percentage is drawn at once.
  const cardsOn = () => !!window.BodyFlags?.on("ai_cards");
  const cardCount = (f) => (cardsOn() && f?.cards?.total) || 0;
  const cardsText = (f) => tr("额度卡 ×{n}", { n: cardCount(f) });
  const cardName = (c) => (c.kind === "reset" ? tr("重置卡") : tr("扩充卡 +{p}%", { p: c.pct ?? "—" }));
  const SOURCE = () => ({ admin: tr("管理员发放"), periodic: tr("定期发放"), reward: tr("奖励"), other: tr("其他") });
  let cardBusy = false;

  function applyQuota(f, counts) {
    if (!f) return;
    if (counts) f = { ...f, cards: { reset: counts.reset, extend: counts.extend, total: counts.total } };
    if (C.ai?.mode === "fallback") Object.assign(C.ai, f);
    quotaLine({ ...(C.ai?.mode === "fallback" ? C.ai : {}), ...f });
    C.onAiChange?.(C.ai, C.assistantUrl);
  }

  async function cardsSheet() {
    let d;
    try { d = await C.api("/v1/users/me/ai-cards"); } catch (e) { C.toast(e.message); return; }
    applyQuota(d.fallback, d.counts);
    const left = d.fallback ? leftPct(d.fallback) : null;
    const until = (c) => (c.expires_on ? tr("{day} 到期", { day: mmdd(c.expires_on) }) : tr("长期有效"));
    const sub = (c, tail) => [SOURCE()[c.source] || "", c.reason, tail].filter(Boolean).map(esc).join(" · ");
    const card = (c) => `<div class="item ai-cardrow"><span class="row-title">${esc(cardName(c))}
        <span class="small muted" style="display:block;font-weight:400">${sub(c, until(c))}</span></span>
        <button class="btn btn-primary ai-card-use" data-use="${c.id}">${esc(tr("使用"))}</button></div>`;
    const STATUS = { used: tr("已用"), expired: tr("已过期"), revoked: tr("已收回") };
    const past = (c) => `<div class="item ai-cardrow"><span class="row-title faint">${esc(cardName(c))}
        <span class="small muted" style="display:block;font-weight:400">${sub(c, c.used_on ? `${STATUS.used} ${mmdd(c.used_on)}` : STATUS[c.status])}</span></span></div>`;
    C.openSheet(tr("Jarvis 额度卡"), `
      ${left != null ? `<div class="kv"><span class="k">${esc(tr("本月剩余"))}</span><span class="v">${left}%</span></div>` : ""}
      <div class="group">${d.cards.length ? d.cards.map(card).join("") : `<div class="item"><span class="row-title faint">${esc(tr("还没有额度卡"))}</span></div>`}</div>
      ${d.history.length ? `<div class="small muted ai-sec">${esc(tr("记录"))}</div><div class="group">${d.history.map(past).join("")}</div>` : ""}
      <p class="login-error" id="ac-err" role="alert"></p>`, (root) => {
      $$("[data-use]", root).forEach((b) => { b.onclick = () => useCard(d.cards.find((c) => String(c.id) === b.dataset.use), b, left, root); });
    });
  }

  async function useCard(c, btn, left, root) {
    if (cardBusy || !c) return;
    const ask = c.kind === "reset" ? tr("用一张重置卡？本月 Jarvis 额度回到 100%（现在剩 {p}%）", { p: left ?? "—" })
      : tr("用一张扩充卡？本月 Jarvis 额度 +{p}%", { p: c.pct ?? "—" });
    if (!confirm(ask)) return;
    cardBusy = true; btn.disabled = true;
    try {
      const r = await C.api(`/v1/users/me/ai-cards/${c.id}/use`, { method: "POST" });
      applyQuota(r.fallback, r.counts);
      C.toast(tr("已用卡：Jarvis 本月剩 {p}%", { p: leftPct(r.fallback) ?? "—" }));
      cardsSheet();
    } catch (e) { $("#ac-err", root).textContent = e.message; btn.disabled = false; }
    finally { cardBusy = false; }
  }
  // A notification about new cards links to /app/?tab=me&ai_cards=1 (notify.js announces it as "body:link")
  window.addEventListener("body:link", (e) => { if (e.detail && "ai_cards" in e.detail && C && cardsOn()) cardsSheet(); });

  async function afterChange() {
    try { const me = await C.api("/v1/web/me"); applyAi(me.ai, me.assistant_url); } catch (e) { /* refresh on next startup */ }
  }

  // ---------------------------------------------------------------- chat panel
  // items: what goes with the next message: {kind: "photo", img} or {kind: "video", frames, seconds}. Frames count as images (at most MAX_IMAGES).
  const chat = { conv: null, busy: false, items: [], live: null, lost: null, photoQuick: null };   // live: the open stream; lost: a turn to pick up (#114); photoQuick: a photo quick task waiting for the picker
  const MAX_IMAGES = 8;
  const imageCount = () => chat.items.reduce((n, it) => n + (it.kind === "video" ? it.frames.length : 1), 0);
  const ICON = {
    photo: `<svg viewBox="0 0 24 24"><path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.8l1.4-2h4.6l1.4 2h1.8A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="12.5" r="3.2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`,
    video: `<svg viewBox="0 0 24 24"><rect x="3.5" y="6.5" width="12" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15.5 10.5l5-3v9l-5-3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
    spark: `<svg viewBox="0 0 24 24"><path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,   // Tabler "sparkles" (MIT), as the Jarvis button
    mic: `<svg viewBox="0 0 24 24"><rect x="9" y="3.5" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  };

  function panel() {
    let p = $("#ai-chat");
    if (p) return p;
    p = document.createElement("div");
    p.id = "ai-chat"; p.className = `panel ai-chat${window.BodyFlags?.on("jarvis_ui_v2") ? " v2" : ""}`; p.hidden = true;   // rollout flag jarvis_ui_v2 (#53): the look is all in ai.css under .ai-chat.v2
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
      <div class="ai-quick-row" id="ai-quick-row" role="group" aria-label="${esc(tr("快捷问题"))}" hidden></div>
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
    $("#ai-file", p).onchange = (e) => addImages([...e.target.files]).finally(() => { e.target.value = ""; photoQuickDone(); });
    $("#ai-file", p).addEventListener("cancel", () => { chat.photoQuick = null; });
    // The photo button itself is a normal attach (the picker's own click bubbles up here too: ignore that one)
    $("#ai-photo", p).addEventListener("click", (e) => { if (e.target.id !== "ai-file") chat.photoQuick = null; });
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
    drawQuick();
    setTimeout(() => $("#ai-text", p).focus(), 300);
  }
  function header() {
    const p = panel();
    $("#ai-model", p).textContent = `${shortName()} ▾`;
    quotaLine(C.ai?.mode === "fallback" ? C.ai : null);
    $("#ai-photo", p).hidden = $("#ai-video", p).hidden = C.ai?.vision === false;
  }
  function newConversation() {
    chat.conv = null; $("#ai-log").innerHTML = ""; hello(); drawQuick();
  }

  // Quick tasks for the page Jarvis was opened from (#101): before the first message under the greeting, afterwards a
  // scrollable row above the input. A tap sends only its own text; the draft and attached photos stay for the user.
  // photo: opens the photo picker and sends the text with the photos picked there.
  // None while viewing someone else (the chat answers about the signed-in user), none on a page without a list.
  function quickList() {
    if (!window.BodyFlags?.on("jarvis_quick")) return [];   // rollout flag (docs/rollout.md): off -> no quick tasks, as before
    if (C.isSelf && !C.isSelf()) return [];
    const page = C.page?.() || "today", tab = page.split(".")[0];
    const Q = {
      today: [{ t: tr("今天适合练吗") }, { t: tr("解释一下今天的恢复分") }, { t: tr("昨晚睡得怎么样") }],
      trends: [{ t: tr("复盘这一周") }, { t: tr("最近负荷是不是太高") }, { t: tr("最近睡得够吗") }],
      "record.journal": [{ t: tr("最近哪些习惯影响恢复") }, { t: tr("记一下今天的状态") }],
      "record.training": [{ t: tr("今天练什么") }, { t: tr("这周按恢复调整一下") }],
      food: [{ t: tr("拍照记一餐"), photo: true }, { t: tr("今天还差多少蛋白") }, { t: tr("下一餐吃什么合适") }],
      "food.shopping": [{ t: tr("按下周菜单列清单并查价") }, { t: tr("这周有什么半价适合我") }],
      me: [{ t: tr("我的数据都共享给了谁") }, { t: tr("你都记得我哪些事") }],
    };
    // records_v2: the journal moved into Trends (行为影响), so its quick question goes with it
    if (window.BodyFlags?.on("records_v2")) Q.trends = [{ t: tr("复盘这一周") }, { t: tr("最近哪些习惯影响恢复") }, { t: tr("最近睡得够吗") }];
    return (Q[page] || Q[tab] || []).filter((q) => !(q.photo && C.ai?.vision === false));
  }
  function drawQuick() {
    const p = panel(), list = quickList(), started = !!$(".ai-msg.user", p), row = $("#ai-quick-row", p);
    const hadFocus = !!$("#ai-quick", p)?.contains(document.activeElement) && document.activeElement.matches(":focus-visible");
    $("#ai-quick", p)?.remove();
    const html = list.map((q, i) => `<button type="button" class="ai-q" data-q="${i}"${chat.busy ? " disabled" : ""}>${esc(q.t)}</button>`).join("");
    row.innerHTML = started ? html : ""; row.hidden = !started || !list.length; row.scrollLeft = 0;
    if (!started && list.length) {
      const box = document.createElement("div"); box.id = "ai-quick"; box.className = "ai-quick"; box.innerHTML = html;
      box.setAttribute("role", "group"); box.setAttribute("aria-label", tr("快捷问题"));
      $("#ai-log", p).appendChild(box);
    }
    $$("[data-q]", p).forEach((b) => { b.onclick = () => askQuick(list[Number(b.dataset.q)]); });
    if (hadFocus) $("#ai-text", p).focus();  // keyboard use: the pressed button is gone (and the row is disabled while replying)
  }
  function quickBusy(on) { $$("#ai-chat [data-q], #ai-chat [data-cq]").forEach((b) => { b.disabled = on; }); }   // data-cq: card buttons that send
  // Send text (+ items) as the user's message without touching the draft or the photos already attached
  function sendQuick(text, items = []) {
    const ta = $("#ai-text"), draft = ta.value, kept = chat.items;
    chat.items = items; ta.value = text;
    send();  // takes the text and items synchronously
    chat.items = kept; drawThumbs();
    ta.value = draft; ta.dispatchEvent(new Event("input"));
  }
  function askQuick(q) {
    if (chat.busy) return;
    if (q.photo) { chat.photoQuick = { text: q.t, before: chat.items.length }; $("#ai-file").click(); return; }
    sendQuick(q.t);
  }
  // After the photo picker: a pending photo quick task sends its text with the newly picked photos only
  function photoQuickDone() {
    const q = chat.photoQuick; chat.photoQuick = null;
    if (!q || chat.busy) return;
    const picked = chat.items.slice(q.before);
    if (!picked.length) return;
    chat.items = chat.items.slice(0, q.before);
    sendQuick(q.text, picked);
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
    const extra = [C.ai?.vision === false ? "" : tr("拍照、发视频也可以。"), micOn() ? tr("按住麦克风说话。") : ""].filter(Boolean);
    const sep = window.I18N.lang === "en" ? " " : "";
    if ($("#ai-chat")?.classList.contains("v2")) { welcome(extra.join(sep), sep); return; }
    bubble("assistant", [tr("你好，我是你的身体管家。问我今天怎么样，或者告诉我吃了什么、练了什么、哪里不舒服，我帮你记下来。"), ...extra].join(sep));
  }
  // Jarvis UI v2: an empty state in the intro's visual language (icon tile, bold title, muted line) instead of a chat bubble;
  // it goes away with the first message (bubble())
  function welcome(extra, sep) {
    const el = document.createElement("div");
    el.className = "ai-welcome";
    el.innerHTML = `<div class="ai-welcome-ic" aria-hidden="true">${ICON.spark}</div>
      <h3>${esc(tr("你好，我是你的身体管家"))}</h3>
      <p>${esc(tr("问我今天怎么样，或者告诉我吃了什么、练了什么、哪里不舒服，我帮你记下来。"))}${esc(extra ? sep + extra : "")}</p>`;
    $("#ai-log").appendChild(el);
  }

  function bubble(role, text, cls = "") {
    const el = document.createElement("div");
    el.className = `ai-msg ${role} ${cls}`;
    el.textContent = text;
    if (role === "user") $(".ai-welcome")?.remove();
    $("#ai-log").appendChild(el);
    scroll();
    return el;
  }
  function note(text, tone = "") { return bubble("note", text, tone); }

  // The AI's answer as safe Markdown (md.js, issue #92). While it streams, repaint at most once per frame and with
  // partial=true (a dangling ** or ` stays hidden until its closer arrives); the final paint renders everything.
  function paint(el, final) {
    if (!el) return;
    if (final) el._final = true;
    const draw = () => {
      el._queued = false;
      if (window.BodyMarkdown) { el.classList.add("md"); el.innerHTML = window.BodyMarkdown.render(el._raw || "", { partial: !el._final }); }
      else el.textContent = el._raw || "";
      scroll();
    };
    if (final) { draw(); return; }
    if (!el._queued) { el._queued = true; requestAnimationFrame(draw); }
  }
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
  // Inside the Watch2Whoop app the WebView can only record once the app grants microphone access (window.Watch2WhoopFeatures.mic,
  // app 1.0.7+, #171); otherwise, and where voice is off on the server, a tap explains the keyboard's dictation key instead
  // (BodyMedia.voicePath in ai-media.js).
  const asrOn = () => !!C.ai?.asr?.enabled;
  const micOn = () => window.BodyMedia?.voicePath(asrOn()) === "record";

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
      starting = rec.start().then(() => { timer = setInterval(tick, 250); }).catch((err) => {
        rec = null; show(false);
        if (!window.BodyMedia.micBlockedInApp(err)) { note(tr("麦克风不可用：在系统设置里允许，或用键盘上的 🎤 听写"), "warn"); return; }
        // Inside the app with the microphone declined in iOS: a button to the app's page in Settings
        const el = note(tr("麦克风没打开：在系统设置里允许 Watch2Whoop 使用麦克风"), "warn");
        const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = tr("打开设置");
        b.onclick = () => window.BodyMedia.openMicSettings();
        el.appendChild(b);
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
    if ($("#ai-quick")) drawQuick();  // first message: the quick tasks move to the row above the input
    ta.value = ""; ta.style.height = "auto";
    await stream("/v1/assistant/chat", { message: text, images, conversation_id: chat.conv, page: C.page?.() || null });   // page: extra tool groups (#99)
  }

  const RESTART_WAITS = [3000, 6000];
  // A failed send: a button on the message sends the same thing again (the page is not left with a dead end)
  function retryButton(el, path, body) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn"; b.textContent = tr("common.retry");
    b.onclick = () => { if (chat.busy) return; el.remove(); stream(path, body); };
    el.appendChild(b);
  }

  // resume: { conv, seq, cur, wrote, body } of a turn whose connection dropped (#114); path is then its events URL
  async function stream(path, body, resume = null) {
    chat.busy = true; $("#ai-send").disabled = true; quickBusy(true);
    let cur = resume?.cur || null, wrote = resume?.wrote || false, seq = resume?.seq || 0, lost = null, ended = false;
    const wait = resume ? tr("接着回答…") : "…";
    const status = note(wait, "faint");
    const ctrl = new AbortController(); chat.live = { ctrl, hidden: document.visibilityState === "hidden", started: () => seq > 0 };
    if (!resume) chat.lost = null;   // a new message replaces a turn that was still waiting to be picked up
    try {
      // A deploy restarts the server for a few seconds (502 / 503 / 504, or no connection): a new message waits and is sent
      // again by itself, at most twice. Nothing was received yet, so it cannot be answered twice.
      const init = resume ? { credentials: "same-origin", signal: ctrl.signal, headers: { Accept: "text/event-stream", ...window.I18N.headers() } }
        : { method: "POST", credentials: "same-origin", signal: ctrl.signal,
          headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-Body-Web": "1", ...window.I18N.headers() }, body: JSON.stringify(body) };
      let r = null;
      for (let n = 0; ; n++) {
        let down = false;
        try { r = await fetch(path, init); down = [502, 503, 504].includes(r.status); }
        catch (e) { if (resume || ctrl.signal.aborted || navigator.onLine === false || n >= RESTART_WAITS.length) throw e; down = true; }
        if (!down || resume || n >= RESTART_WAITS.length) break;
        status.textContent = tr("服务器正在更新，几秒后自动重试…");
        await new Promise((ok) => setTimeout(ok, RESTART_WAITS[n]));
        status.textContent = wait;
      }
      if (!r.ok) {
        let d = null; try { d = (await r.json()).detail; } catch (e) { /* no body */ }
        status.remove();
        const restart = [502, 503, 504].includes(r.status) && !resume;
        const msg = restart ? tr("服务器正在更新，请过一会儿再试") : typeof d === "string" ? d : d?.message || tr("err.request", { status: r.status });
        if (d?.code === "fallback_consent") { closeChat(); consentSheet(null, () => openChat()); return; }
        if (d?.quota_cny != null) quotaLine(d);
        const el = note(msg, "warn");
        if (d?.code === "quota") cardButton(el, d);
        if (restart) retryButton(el, path, body);
        if (d?.assistant_url) { const a = document.createElement("a"); a.href = d.assistant_url; a.target = "_blank"; a.rel = "noopener"; a.className = "btn"; a.textContent = tr("打开"); el.appendChild(a); }
        if (r.status === 401) C.toast(tr("登录过期了，请重新登录"));
        return;
      }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        // A stream cut without its last event (a proxy or the server closing it, iOS ending the socket cleanly) is a
        // dropped connection too, not the end of the answer: pick the turn up like a network error (#114)
        if (done) { if (!ended) throw new Error("stream ended early"); break; }
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = /^event: (.*)$/m.exec(block)?.[1], data = /^data: (.*)$/m.exec(block)?.[1];
          if (!ev || data == null) continue;
          seq = Number(/^id: (\d+)$/m.exec(block)?.[1] || seq);
          const d = JSON.parse(data);
          if (ev === "conversation") { chat.conv = d.id; if (d.quota) quotaLine(d.quota); }
          else if (ev === "text") { if (!cur) { status.remove(); cur = bubble("assistant", ""); cur._raw = ""; } cur._raw += d.delta; paint(cur); }
          else if (ev === "tool") {
            paint(cur, true);
            cur = null;
            status.textContent = { running: tr("查看 {name}…", { name: d.name }), done: `✓ ${d.name}`, error: `✗ ${d.name}`, rejected: tr("已取消 {name}", { name: d.name }) }[d.status] || d.name;
            if (d.status === "done" && body.call_id) wrote = true;
            // A rejected value (which field, how much over) is told apart from a network failure ("连不上") below
            if (d.status === "error" && d.message) note(tr("{name} 校验失败：{msg}", { name: d.name, msg: d.message }), "warn");
          }
          else if (ev === "card") { if (rich()) { status.remove(); paint(cur, true); cur = null; showCard(d.card); } }
          else if (ev === "confirm") { ended = true; status.remove(); d.calls.forEach(confirmCard); }
          else if (ev === "usage") { usageLine(d); }
          else if (ev === "notice") note(d.message, "warn");
          else if (ev === "error") { ended = true; status.remove(); const el = note(d.message || tr("出错了"), "warn"); if (d.quota_cny != null) quotaLine(d); if (d.code === "quota") cardButton(el, d); }
          else if (ev === "done") { ended = true; status.remove(); paint(cur, true); }
          else if (ev === "resync") { ended = true; status.remove(); await resync(d.conversation_id, cur); }
        }
      }
    } catch (e) {
      status.remove();
      // The turn goes on on the server (#114): once it started (an event arrived), pick it up instead of reporting an error
      if (seq > 0 && chat.conv) lost = { conv: chat.conv, seq, cur, wrote, body, tries: seq > (resume?.seq || 0) ? 0 : resume?.tries || 0 };
      else {
        const el = note(navigator.onLine === false ? tr("离线，连上网络后重试") : tr("连不上（没联网，或 Tailscale 没开）"), "warn");
        if (!resume) retryButton(el, path, body);
      }
    } finally {
      if (!lost) paint(cur, true);   // a turn that is picked up again keeps streaming into the same bubble
      if (status.isConnected && status.textContent === wait) status.remove();
      if (chat.live?.ctrl === ctrl) chat.live = null;
      chat.busy = false; $("#ai-send").disabled = false; quickBusy(false);
      if (lost) carryOn(lost);
      else if (wrote) C.refresh?.();  // Something was written to the database: refresh the Today page data
    }
  }

  // ---------------------------------------------------------------- picking a turn up again (#114)
  // iOS suspends the WebView (and drops its connections) when the app goes to the background; the server finishes the
  // turn anyway and keeps its numbered events. Back in the foreground the page asks for the events after the last one it got.
  function carryOn(run) {
    chat.lost = run; chat.busy = true; $("#ai-send").disabled = true;   // nothing new is sent before the turn is shown
    if (document.visibilityState !== "hidden") resumeTurn();
  }
  async function resumeTurn() {
    const run = chat.lost;
    if (!run || chat.live) return;
    if (run.conv !== chat.conv) { chat.lost = null; return; }   // the user moved on to another conversation
    if (navigator.onLine === false) {   // tried again by the "online" event
      if (!run.offline) { run.offline = note(tr("离线，连上网络后重试"), "warn"); }
      chat.busy = false; $("#ai-send").disabled = false;
      return;
    }
    chat.lost = null; run.offline?.remove(); run.offline = null;
    if (run.tries > 0) await new Promise((ok) => setTimeout(ok, 1500));   // the network may need a moment after waking up
    if (run.tries >= 3) { note(tr("连不上（没联网，或 Tailscale 没开）"), "warn"); chat.busy = false; $("#ai-send").disabled = false; return; }
    await stream(`/v1/assistant/chat/${run.conv}/events?after=${run.seq}`, run.body, { ...run, tries: run.tries + 1 });
  }
  // The turn is no longer kept on the server (it restarted, or the turn ended long ago): show what the database has
  async function resync(id, cur) {
    let v = null;
    try { v = await C.api(`/v1/assistant/conversations/${id}`); } catch (e) { note(e.message || tr("出错了"), "warn"); return; }
    const msgs = v.messages || [], from = msgs.map((m) => m.role).lastIndexOf("user") + 1;
    const text = msgs.slice(from).filter((m) => m.role === "assistant" && m.text).map((m) => m.text).join("\n\n");
    if (text) { const el = cur || bubble("assistant", ""); el._raw = text; paint(el, true); }   // rendered like a finished answer (md.js)
    if (rich()) msgs.slice(from).forEach((m) => (m.cards || []).forEach(showCard));
    (v.pending || []).forEach(confirmCard);
  }
  function onForeground() {
    if (document.visibilityState === "hidden") return;
    // A stream that lived through the background may hang on a dead socket: drop it and resume from the last event
    // (only once it has one: before the first event there is nothing to resume from)
    if (chat.live?.hidden && chat.live.started()) { chat.live.ctrl.abort(); return; }
    if (chat.lost) resumeTurn();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { if (chat.live) chat.live.hidden = true; }
    else onForeground();
  });
  window.addEventListener("pageshow", onForeground);
  window.addEventListener("online", onForeground);
  window.addEventListener("watch2whoop:foreground", onForeground);   // sent by the iOS app on resume (apps/watch2whoop)

  // Allowance used up while there are cards: a button under the message opens the card list (the sheet sits above this panel)
  function cardButton(el, d) {
    if (!cardCount(d.cards ? d : C.ai)) return;
    const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = tr("用一张额度卡");
    b.onclick = () => cardsSheet(); el.appendChild(b);
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

  // ---------------------------------------------------------------- cards (ai/cards.py, rollout flag jarvis_rich)
  // The server builds every card from the user's own data; the model only picks which. Nothing here renders model HTML:
  // all text goes through esc(), and buttons can only send a text back as the user's message or open one of the Today
  // page's own pages (C.go). The training card reuses the training page's exercise card heads (training-day.js).
  const rich = () => !!window.BodyFlags?.on("jarvis_rich");
  const METRIC = { recovery: tr("恢复"), strain: tr("负荷"), sleep: tr("睡眠"), hrv: "HRV", rhr: tr("静息心率"), weight: tr("体重") };
  const OPEN = { "record.training": tr("去记录"), food: tr("打开饮食"), trends: tr("看趋势"), today: tr("回到今天"), sleep: tr("睡眠详情"), profile: tr("设置性别"),
    "food.recipes": tr("全部食谱"), "food.shopping": tr("打开采购"), recipe: tr("打开食谱") };
  // sleep card: rule findings (sleep_night/view.py notes, by key) as short labels
  const NOTE = { short_of_need: tr("睡得不够"), weak_duration: tr("时长拉分"), weak_stages: tr("阶段拉分"), weak_consistency: tr("规律性拉分"),
    weak_interruptions: tr("中途醒来拉分"), deep_low: tr("深睡偏少"), rem_low: tr("REM 偏少"), efficiency_low: tr("效率偏低"),
    latency_long: tr("入睡慢"), waso_long: tr("夜里醒得多"), bedtime_late: tr("睡得晚"), hrv_low: tr("HRV 偏低"), rhr_high: tr("心率偏高"),
    resp_high: tr("呼吸偏快"), temp_off: tr("体温异常"), spo2_low: tr("血氧偏低"), illness: tr("可能生病") };
  const SEND = {
    adjust_training: () => [tr("按恢复调整"), tr("按今天的恢复调整一下训练")],
    next_meal: () => [tr("下一餐吃什么"), tr("下一餐吃什么合适")],
    analyze_muscles: () => [tr("分析肌群训练"), tr("请分析我的肌群训练：先用 get_muscle_load 看各肌群本周的组数，结合我的重点肌群和体态目标，告诉我哪些偏少、哪些偏多、和目标差在哪、具体怎么改（每周给哪块加几组、换哪些动作，避开我的禁用动作）。要改训练计划的话先给我看改动，我确认后再改。")],
    explain_trend: (c) => [tr("解释这个变化"), tr("解释一下{name}最近的变化", { name: METRIC[c.metric] || c.metric })],
    // jarvis_cards_v2 (diet cards)
    more_meals: () => [tr("换一个"), tr("换几道别的推荐")],
    log_recipe: (c) => [tr("记下这餐"), tr("按食谱「{name}」记一份（食谱 id {id}）", { name: c.name, id: c.id })],
    recipe_steps: (c) => [tr("怎么做"), tr("「{name}」怎么做？", { name: c.name })],
    shopping_plan: () => [tr("还缺什么"), tr("看看这周的采购清单还缺什么")],
    undo_meal: () => [tr("撤销"), tr("撤销刚才记的这餐")],
  };
  // Row buttons of the diet cards (jarvis_cards_v2): the text names the row by its own fields (the server's ids), never model text
  const ROW = {
    portion: (c, e, x) => tr("把 {d} 记的「{food}」改成原来的 {x} 倍（id {id}）", { d: c.date, food: e.food || "", x, id: e.id }),
    delete: (c, e) => tr("删掉 {d} 记的「{food}」（id {id}）", { d: c.date, food: e.food || "", id: e.id }),
    recipe: (r, slot) => (slot ? tr("按食谱「{name}」记一份{meal}（食谱 id {id}）", { name: r.name, meal: window.BodyFood?.slotName(slot) || slot, id: r.id })
      : tr("按食谱「{name}」记一份（食谱 id {id}）", { name: r.name, id: r.id })),
    template: (t) => tr("按快捷模板「{name}」记一次（模板 id {id}）", { name: t.name, id: t.id }),
  };
  // Lucide icons (https://lucide.dev, ISC): only the paths used; notices in THIRD_PARTY_NOTICES.md
  const LUCIDE = {
    utensils: '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
  };
  const lucide = (k, cls = "") => `<svg class="ai-ic ${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${LUCIDE[k]}</svg>`;
  let cardSeq = 0;
  const n0 = (v, d = 0) => (v == null ? "—" : window.I18N.num(v, d));

  function cardHead(title, date, extra = "") {
    return `<div class="ai-card-head"><span class="ai-card-title">${esc(title)}</span>${date ? `<span class="ai-card-date">${esc(mmdd(date))}</span>` : ""}${extra}</div>`;
  }
  function meter(label, v, goal, unit) {
    const pct = goal ? Math.min(100, Math.round((100 * (v || 0)) / goal)) : 0;
    return `<div class="ai-meter"><div class="ai-meter-top"><span>${esc(label)}</span><span class="num"><b>${n0(v)}</b>${goal ? ` / ${n0(goal)}` : ""} ${esc(unit)}</span></div>
      ${goal ? `<div class="bar${(v || 0) > goal * 1.1 ? " over" : ""}"><span style="width:${pct}%"></span></div>` : ""}</div>`;
  }
  function trainingBody(c) {
    const sub = [c.plan, ...(c.sessions || [])].filter(Boolean).join(" · ");
    const tier = { red: "bad", yellow: "mid", green: "good" }[c.readiness?.tier];
    const head = cardHead(tr("训练"), c.date, tier && c.readiness?.label ? ` <span class="badge ${tier}">${esc(c.readiness.label)}</span>` : "");
    const log = c.log?.status === "submitted" ? tr("已提交") : c.log?.done_sets ? tr("已记 {n} 组", { n: c.log.done_sets }) : "";
    const list = !c.exercises?.length ? `<div class="small muted">${esc(c.rest_day ? tr("休息日") : tr("这天没有训练计划"))}</div>`
      : window.BodyTrainingDay?.summaryHtml ? window.BodyTrainingDay.summaryHtml(c.exercises)
        : `<ul class="ai-card-list">${c.exercises.map((e) => `<li>${esc(window.I18N.lang === "en" && e.name_en ? e.name_en : e.name_zh)}</li>`).join("")}</ul>`;
    return `${head}${sub ? `<div class="small muted ai-card-sub">${esc(sub)}${log ? ` · ${esc(log)}` : ""}</div>` : ""}${list}`;
  }
  function mealBody(c) {
    const t = c.totals || {}, g = c.goal || {};
    const tap = c.entry_actions?.length;   // v2: a row opens its portion / delete buttons (mealRowsV2)
    const rows = (c.entries || []).map((e, i) => `<li${tap && e.id ? ` class="ai-entry" data-entry="${i}" role="button" tabindex="0"` : ""}><span>${esc(e.food || "")}${e.quantity ? ` <span class="muted">${esc(n0(e.quantity, 1))} ${esc(e.unit || "")}</span>` : ""}</span>
      <span class="num muted">${n0(e.calories)} kcal</span></li>`);
    const more = rows.length > 6 ? `<li class="muted small">${esc(tr("还有 {n} 条", { n: rows.length - 6 }))}</li>` : "";
    return `${cardHead(tr("饮食"), c.date)}
      ${meter(tr("热量"), t.calories, g.calories, "kcal")}${meter(tr("蛋白"), t.protein_g, g.protein_g, "g")}
      <div class="small muted ai-card-sub num">${esc(tr("碳水"))} ${n0(t.carbs_g)} g · ${esc(tr("脂肪"))} ${n0(t.fat_g)} g</div>
      ${rows.length ? `<ul class="ai-card-list">${rows.slice(0, 6).join("")}${more}</ul>` : `<div class="small muted">${esc(tr("这天还没有记录"))}</div>`}`;
  }
  function metricBody(c) {
    const name = METRIC[c.metric] || c.metric, d = c.digits ?? 0;
    const chart = window.BodyCharts?.line ? window.BodyCharts.line(`aic-${++cardSeq}`, c.dates, c.values, { h: 72, label: name })
      : window.BodyCharts?.spark?.(c.values) || "";
    return `${cardHead(name, null, `<span class="ai-card-date">${esc(tr("近 {n} 天", { n: c.dates.length }))}</span>`)}
      <div class="ai-card-big num"><b>${n0(c.latest, d)}</b> <small>${esc(c.unit || "")}</small>
        <span class="small muted">${esc(tr("均值"))} ${n0(c.avg, d)}</span></div>${chart}`;
  }
  function sleepBody(c) {
    const m = c.main;
    if (!m) return `${cardHead(tr("睡眠"), c.date)}<div class="small muted">${esc(tr("这晚没有手表睡眠数据"))}</div>`;
    const t = (v) => (v == null ? "—" : `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}`);
    const tier = c.score == null ? "" : c.score >= 80 ? "good" : c.score >= 60 ? "mid" : "bad";
    const chart = m.stages?.length && window.BodyCharts?.hypno ? window.BodyCharts.hypno(m, m.stages) : "";
    const found = (c.notes || []).filter((n) => n.tone !== "good" && NOTE[n.key]).slice(0, 3);
    return `${cardHead(tr("睡眠"), c.date, tier ? ` <span class="badge ${tier}">${esc(tr("睡眠分"))} ${n0(c.score)}</span>` : "")}
      <div class="ai-card-big num"><b>${t(m.asleep_min)}</b> <small>${esc(m.start)} → ${esc(m.end)}</small>
        ${c.need_min ? `<span class="small muted">${esc(tr("需求"))} ${t(c.need_min)}</span>` : ""}</div>${chart}
      ${found.length ? `<div class="rcard-tags" style="margin-top:8px">${found.map((n) => `<span class="badge ${n.tone === "bad" ? "bad" : "mid"}">${esc(NOTE[n.key])}</span>`).join("")}</div>` : ""}`;
  }
  // ---- diet cards (ai/cards_food.py, flag jarvis_cards_v2)
  function ringMini(v, goal, label, unit) {   // the food page's progress colours (.bar): --text, --mid when over by 10 %
    const r = 34, len = 2 * Math.PI * r, frac = goal ? Math.min(1, (v || 0) / goal) : 0, over = goal && (v || 0) > goal * 1.1;
    return `<div class="ai-ring"><svg viewBox="0 0 84 84" role="img" aria-label="${esc(`${label} ${n0(v)}${goal ? ` / ${n0(goal)}` : ""} ${unit}`)}">
      <circle class="track" cx="42" cy="42" r="${r}" fill="none" stroke-width="7"/>
      ${goal ? `<circle class="arc${over ? " over" : ""}" cx="42" cy="42" r="${r}" fill="none" stroke-width="7" stroke-linecap="round"
        stroke-dasharray="${len.toFixed(1)}" stroke-dashoffset="${(len * (1 - frac)).toFixed(1)}" transform="rotate(-90 42 42)"/>` : ""}
      <text class="val" x="42" y="44" text-anchor="middle">${n0(v)}</text>
      <text class="cap" x="42" y="59" text-anchor="middle">${goal ? `/ ${n0(goal)}` : esc(unit)}</text></svg>
      <div class="ai-ring-l">${esc(label)}${goal ? ` <span class="muted">${esc(unit)}</span>` : ""}</div></div>`;
  }
  function goalsBody(c) {
    const t = c.totals || {}, g = c.goal;
    const head = cardHead(tr("饮食目标"), c.date, c.name ? ` <span class="badge plain">${esc(c.name)}</span>` : "");
    if (!g) return `${head}<div class="small muted">${esc(tr("还没有设饮食目标"))}</div>`;
    const cap = (label, v, max) => (max ? meter(label, v, max, "g") : "");
    return `${head}<div class="ai-rings">${ringMini(t.calories, g.calories, tr("热量"), "kcal")}${ringMini(t.protein_g, g.protein_g, tr("蛋白"), "g")}</div>
      ${meter(tr("碳水"), t.carbs_g, g.carbs_g, "g")}${meter(tr("脂肪"), t.fat_g, g.fat_g, "g")}
      ${cap(tr("饱和脂肪上限"), t.saturated_fat_g, g.sat_fat_max_g)}${cap(tr("添加糖上限"), t.added_sugar_g, g.added_sugar_max_g)}${cap(tr("纤维"), t.fiber_g, g.fiber_min_g)}
      ${c.logged ? "" : `<div class="small muted">${esc(tr("这天还没有记录"))}</div>`}`;
  }
  // a recipe row as the food page draws it (app.js recipeCard), with the fields a suggestion uses there
  const rcard = (r, slot) => window.BodyFood?.recipeCard({ id: r.id, name: r.name, favorite: r.favorite, labels: r.badges || [],
    meal_kcal: r.kcal, meal_protein_g: r.protein_g, with_staple: r.with_staple, prep_min: r.min }, slot)
    || `<button class="rcard" data-recipe="${esc(r.id)}"><span class="rcard-name">${esc(r.name)}</span></button>`;
  function suggestBody(c) {
    const rem = c.remaining || {};
    const head = cardHead(tr("推荐餐"), c.date) + (rem.kcal != null ? `<div class="small muted ai-card-sub num">${esc(tr("余 {k} kcal · {p} g 蛋白", { k: n0(Math.max(0, rem.kcal)), p: n0(Math.max(0, rem.protein_g || 0)) }))}</div>` : "");
    const slots = (c.slots || []).filter((s) => s.items?.length || s.logged);
    if (!slots.length) return `${head}<div class="small muted">${esc(tr("没有合适的菜"))}</div>`;
    return `${head}${slots.map((s) => `<div class="ai-slot">
      <div class="mp-h"><span class="row-title">${esc(window.BodyFood?.slotName(s.slot) || s.slot)}</span>
        <span class="small muted num">${s.logged ? esc(tr("已吃 {k} kcal", { k: n0(s.logged.kcal) })) : s.target?.kcal > 0 ? `≈ ${n0(s.target.kcal)} kcal · ${n0(s.target.protein_g)} g` : ""}</span></div>
      ${(s.items || []).map((r) => `<div class="ai-rc">${rcard(r, s.slot)}<button type="button" class="ai-q" data-cq data-row="recipe" data-slot="${esc(s.slot)}" data-rid="${esc(r.id)}"${chat.busy ? " disabled" : ""}>${esc(tr("记下这餐"))}</button></div>`).join("")}</div>`).join("")}`;
  }
  function recipeBody(c) {
    const pic = c.image ? `<img class="ai-rimg" src="${esc(c.image)}" alt="" loading="lazy" decoding="async">` : `<span class="ai-rimg ph">${lucide("utensils")}</span>`;
    const n4 = (l, v, u) => `<div><div class="l">${esc(l)}</div><div class="v">${n0(v)}<small class="muted"> ${esc(u)}</small></div></div>`;
    const badges = (c.badges || []).map((b) => `<span class="badge ${{ good: "good", warn: "mid", bad: "bad" }[b.tone] || "plain"}">${esc(b.label)}</span>`).join("");
    const meta = [c.min && `${c.min}′`, c.steps && tr("{n} 步", { n: c.steps }), c.per_serving && `${tr("每份")} ${c.per_serving}`].filter(Boolean).join(" · ");
    const ings = (c.ingredients || []).map((x) => `<li><span>${esc(x.food || "")}</span><span class="num muted">${x.grams != null ? `${n0(x.grams)} g` : ""}</span></li>`).join("");
    return `<div class="ai-recipe-top">${pic}<div class="ai-recipe-t"><div class="ai-card-title">${esc(c.name)}${c.favorite ? ` <span class="star on">★</span>` : ""}</div>
        ${meta ? `<div class="small muted">${esc(meta)}</div>` : ""}${badges ? `<div class="rcard-tags">${badges}</div>` : ""}</div></div>
      <div class="nut4">${n4(tr("热量"), c.kcal, "kcal")}${n4(tr("蛋白"), c.protein_g, "g")}${n4(tr("碳水"), c.carbs_g, "g")}${n4(tr("脂肪"), c.fat_g, "g")}</div>
      ${ings ? `<div class="small muted ai-card-cap">${esc(tr("每份食材"))}</div><ul class="ai-card-list">${ings}${c.more_ingredients ? `<li class="muted small">${esc(tr("还有 {n} 样", { n: c.more_ingredients }))}</li>` : ""}</ul>` : ""}`;
  }
  function recipesBody(c) {
    const head = cardHead(c.slot ? window.BodyFood?.slotName(c.slot) || tr("食谱") : tr("食谱"), null, `<span class="ai-card-date">${esc(tr("{n} 道", { n: c.total || 0 }))}</span>`);
    if (!c.items?.length) return `${head}<div class="small muted">${esc(tr("暂无食谱"))}</div>`;
    return `${head}<div class="ai-rlist">${c.items.map((r) => rcard(r)).join("")}</div>`;
  }
  function templatesBody(c) {
    const head = cardHead(tr("快捷记录"), null, `<span class="ai-card-date">${esc(tr("{n} 个", { n: c.total || 0 }))}</span>`);
    if (!c.items?.length) return `${head}<div class="small muted">${esc(tr("还没有快捷记餐模板"))}</div>`;
    return `${head}<ul class="ai-card-list ai-tpl">${c.items.map((t, i) => `<li><span><b>${esc(t.name)}</b>
        <span class="small muted num">${esc(window.BodyFood?.slotName(t.meal) || t.meal || "")} · ${n0(t.kcal)} kcal · ${esc(tr("蛋白"))} ${n0(t.protein_g)} g</span></span>
      ${t.logged ? `<span class="small muted">${esc(tr("已记 ✓"))}</span>` : `<button type="button" class="ai-q" data-cq data-row="template" data-i="${i}"${chat.busy ? " disabled" : ""}>${esc(t.needs_confirmation ? tr("确认克数") : tr("记一次"))}</button>`}</li>`).join("")}</ul>`;
  }
  function shoppingBody(c) {
    const p = c.progress || { bought: 0, total: 0 };
    const cost = Object.entries(c.est_total || {}).map(([cur, v]) => `≈ ${cur} ${n0(v, 2)}`).join(" · ");
    const head = cardHead(tr("采购清单"), c.week_start, "");
    if (!p.total) return `${head}<div class="small muted">${esc(tr("这周还没有采购清单"))}</div>`;
    const items = [...(c.items || []).filter((i) => !i.bought), ...(c.items || []).filter((i) => i.bought)];   // to buy first
    const shown = items.slice(0, 12), more = items.length - shown.length + (c.more || 0);
    return `${head}${meter(tr("已买"), p.bought, p.total, "")}
      ${cost ? `<div class="small muted ai-card-sub num">${esc(cost)}</div>` : ""}
      ${c.warnings?.length ? `<div class="rcard-tags" style="margin-bottom:6px">${c.warnings.map((w) => `<span class="badge mid">${esc(w)}</span>`).join("")}</div>` : ""}
      <ul class="ai-card-list ai-shop">${shown.map((it) => `<li class="${it.bought ? "done" : ""}">
        <span>${it.bought ? lucide("check", "ok") : `<i class="ai-dot"></i>`}<span class="n">${esc(it.name)}</span>${it.category_label ? `<span class="small muted">${esc(it.category_label)}</span>` : ""}</span>
        <span class="num muted">${esc(it.qty || "")}</span></li>`).join("")}</ul>
      ${more > 0 ? `<div class="small muted">${esc(tr("还有 {n} 样", { n: more }))}</div>` : ""}`;
  }
  // meal card rows (v2: the card has entry_actions): tap a row for its portion / delete buttons
  function mealRowsV2(el, c) {
    $$("[data-entry]", el).forEach((li) => {
      li.onclick = () => {
        const open = li.nextElementSibling?.classList.contains("ai-entry-acts");
        $$(".ai-entry-acts", el).forEach((x) => x.remove());
        if (open) return;
        const e = c.entries[Number(li.dataset.entry)];
        const acts = [];
        if (c.entry_actions.includes("portion")) for (const x of [0.5, 1.5, 2]) acts.push([`×${x}`, ROW.portion(c, e, x)]);
        if (c.entry_actions.includes("delete")) acts.push([tr("删除"), ROW.delete(c, e)]);
        const row = document.createElement("li");
        row.className = "ai-entry-acts";
        row.innerHTML = acts.map(([l], i) => `<button type="button" class="ai-q" data-cq data-ea="${i}"${chat.busy ? " disabled" : ""}>${esc(l)}</button>`).join("");
        $$("[data-ea]", row).forEach((b) => { b.onclick = (ev) => { ev.stopPropagation(); if (!chat.busy) sendQuick(acts[Number(b.dataset.ea)][1]); }; });
        li.after(row);
      };
    });
  }
  function bindRows(el, c) {
    if (c.type === "meal" && c.entry_actions?.length) mealRowsV2(el, c);
    $$("[data-recipe]", el).forEach((b) => { b.onclick = () => { closeChat(); C.go?.("recipe", { id: b.dataset.recipe, slot: b.dataset.slot || null }); }; });
    $$("[data-row]", el).forEach((b) => {
      b.onclick = () => {
        if (chat.busy) return;
        if (b.dataset.row === "recipe") {
          const s = (c.slots || []).find((x) => x.slot === b.dataset.slot), r = s?.items.find((x) => String(x.id) === b.dataset.rid);
          if (r) sendQuick(ROW.recipe(r, s.slot));
        } else if (b.dataset.row === "template") {
          const t = c.items?.[Number(b.dataset.i)];
          if (t) sendQuick(ROW.template(t));
        }
      };
    });
  }

  function actionButtons(c) {
    return (c.actions || []).map((a) => {
      if (a.kind === "open" && OPEN[a.page]) return { label: OPEN[a.page], run: () => { closeChat(); C.go?.(a.page, c); } };
      if (a.kind === "send") {
        const [label, text] = a.key ? (SEND[a.key]?.(c) || []) : [a.label, a.text];
        if (label && text) return { label, run: () => { if (!chat.busy) sendQuick(text); }, send: true };
      }
      return null;
    }).filter(Boolean);
  }
  function showCard(c) {
    if (!c?.type) return;
    const body = { training: trainingBody, meal: mealBody, metric: metricBody, sleep: sleepBody, actions: () => "", muscle: (m) => window.BodyMuscleLoad?.cardSvg(m) || "",
      goals: goalsBody, suggest: suggestBody, recipe: recipeBody, recipes: recipesBody, templates: templatesBody, shopping: shoppingBody }[c.type];
    if (!body) return;
    const acts = actionButtons(c);
    const el = document.createElement("div");
    el.className = `ai-card ai-card-${c.type}`;
    el.innerHTML = body(c) + (acts.length ? `<div class="ai-card-acts">${acts.map((a, i) => `<button type="button" class="${a.send ? "ai-q" : "btn"}" data-act="${i}"${a.send ? ` data-cq${chat.busy ? " disabled" : ""}` : ""}>${esc(a.label)}</button>`).join("")}</div>` : "");
    $$("[data-act]", el).forEach((b) => { b.onclick = () => acts[Number(b.dataset.act)].run(); });
    bindRows(el, c);
    $(".ai-welcome")?.remove();
    $("#ai-log").appendChild(el); scroll();
  }

  // ---------------------------------------------------------------- towards app.js
  window.BodyAI = {
    init(ctx) { C = ctx; },
    handlesAsk() { return !!C && (C.ai?.mode === "api" || C.ai?.mode === "fallback"); },
    openChat, openSettings, settingsValue,
    reset() { chat.conv = null; chat.items = []; const p = $("#ai-chat"); if (p) p.remove(); },
  };
})();
